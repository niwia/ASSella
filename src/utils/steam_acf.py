"""
Steam ACF (appmanifest) helpers.

Single owner for the "make Steam adopt an install that is already on disk" write.
Both migration entry points -- the single-game "Move to AT0-M" action in the game
details dialog and the library batch migration -- go through here so the rules
cannot drift apart.

Three things are deliberately NOT done here:

* Manifest GIDs are never read, resolved, or written. ``setManifestid`` in a
  Hubcap Lua is a *pinning* directive, not download metadata. Writing it into a
  Steam ACF (or into config.yaml) makes Steam verify against that exact
  manifest, which fights the live branch and triggers needless re-downloads.
  Build pinning is handled exclusively by core.native_steam.steam_manifest_pinning
  via the ManifestIds section, and only when the user opts into "Pin build".
  Steam derives manifest ids itself from the depotcache/CDN during adoption.
* AutoUpdateBehavior is never touched.
* The write never happens while Steam is running. Steam rewrites
  ``appmanifest_*.acf`` from its own in-memory state, so a manifest we edit
  underneath it is either clobbered or fought over. The caller is told the
  update was deferred so it can be retried when Steam is closed.

StateFlags normalization IS done here because corrupted flags (e.g. 518 =
UpdateRequired|FullyInstalled|UpdatePaused) prevent Steam from launching the
game even when the files are present. This is a common symptom of games
transferred from ASSella to AT0M where the ACF was partially written.
"""

import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Result codes returned by ensure_acf_has_installed_depots()
ACF_WRITTEN = "written"
ACF_DEFERRED = "deferred"  # Steam is running: writing now would be clobbered
ACF_SKIPPED = "skipped"  # nothing to do (no ACF on disk / nothing to add)
ACF_FAILED = "failed"


def steam_is_running() -> bool:
    """Return True when a Steam client process is alive on this machine."""
    try:
        from core.steam_helpers import is_steam_running

        return bool(is_steam_running())
    except Exception as e:
        logger.debug(f"[SteamACF] Could not determine whether Steam is running: {e}")
        return False


def atomic_write_acf(acf_path: str, content: str) -> bool:
    """Back up and atomically replace a Steam ACF.

    Steam parses ``appmanifest_*.acf`` strictly; a partial write can make it drop
    the app from the library entirely. Write to a sibling temp file, fsync, then
    ``os.replace`` so Steam only ever observes a complete file. A timestamped
    backup is kept because we are editing Steam's own state.
    """
    path = Path(acf_path)
    if not path.exists():
        return False
    try:
        stamp = int(time.time())
        backup = path.with_suffix(path.suffix + f".assella-bak-{stamp}")
        shutil.copy2(path, backup)
    except Exception as e:
        logger.warning(f"[SteamACF] Could not back up ACF {acf_path}: {e}")
        return False

    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.warning(f"[SteamACF] Atomic ACF write failed for {acf_path}: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception as unlink_err:
            logger.debug(f"[SteamACF] Could not remove the temporary manifest file: {unlink_err}")
        return False


def _find_vdf_block(content: str, key: str) -> Optional[Tuple[int, int, str]]:
    """Return (body_start, body_end, body) of the ``"key" { ... }`` block, or None."""
    m = re.search(rf'"{re.escape(key)}"\s*\{{', content)
    if not m:
        return None
    open_idx = content.index("{", m.start())
    depth = 0
    for idx in range(open_idx, len(content)):
        ch = content[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return (open_idx + 1, idx, content[open_idx + 1:idx])
    return None


def _detect_broken_acf(content: str) -> bool:
    """Check if the ACF is in a broken state (empty depots + zero size).

    A broken ACF has StateFlags=4 (FullyInstalled) but empty InstalledDepots
    and/or SizeOnDisk=0. This is a common symptom of games transferred from
    ASSella to AT0M where the ACF was partially written before the depot
    registration completed.
    """
    m_flags = re.search(r'"StateFlags"\s*"(\d+)"', content)
    m_size = re.search(r'"SizeOnDisk"\s*"(\d+)"', content)
    block = _find_vdf_block(content, "InstalledDepots")

    flags = int(m_flags.group(1)) if m_flags else 0
    size = int(m_size.group(1)) if m_size else 0

    # Empty InstalledDepots block or no block at all
    empty_depots = block is None or _parse_installed_depots(block[2])[0] == []

    # SizeOnDisk is zero or missing
    zero_size = m_size is None or size == 0

    # StateFlags says FullyInstalled (bit 2) but depots are missing
    has_installed_flag = (flags & 4) != 0

    return has_installed_flag and (empty_depots or zero_size)


def _normalize_state_flags(content: str) -> Tuple[str, bool]:
    """Repair corrupted StateFlags and UpdateResult in the ACF.

    Fixes:
    - StateFlags with UpdateRequired (2) or UpdatePaused (512) bits set
      while the game is supposed to be fully installed
    - Non-zero UpdateResult indicating a previous failed update

    Returns (new_content, changed).
    """
    m_flags = re.search(r'"StateFlags"\s*"(\d+)"', content)
    m_update_result = re.search(r'"UpdateResult"\s*"(\d+)"', content)

    if not m_flags:
        return content, False

    flags = int(m_flags.group(1))

    # Bits that indicate "not ready to play" despite having files on disk
    # 2 = UpdateRequired, 512 = UpdatePaused
    corrupted_bits = 2 | 512  # 514 total

    if (flags & corrupted_bits) == 0:
        return content, False

    # Clear the corrupted bits and ensure FullyInstalled (4) is set
    clean_flags = (flags & ~corrupted_bits) | 4

    new_content = content
    if clean_flags != flags:
        new_content = re.sub(
            r'"StateFlags"\s*"(\d+)"',
            f'"StateFlags"\t\t"{clean_flags}"',
            new_content
        )
        logger.info(
            f"[SteamACF] Normalized StateFlags {flags} -> {clean_flags} "
            f"(cleared UpdateRequired/UpdatePaused bits)"
        )

    if m_update_result and m_update_result.group(1) != "0":
        new_content = re.sub(
            r'"UpdateResult"\s*"\d+"',
            '"UpdateResult"\t\t"0"',
            new_content
        )
        logger.info(
            f"[SteamACF] Reset UpdateResult to 0"
        )

    return new_content, (new_content != content)


def set_acf_state_flags(acf_path: Union[str, Path], state_flags: int = 6) -> bool:
    """Set StateFlags in an existing ACF file to state_flags (default 6 = UpdateRequired | FullyInstalled).
    Also ensures UpdateResult is 0 so Steam triggers a clean verification pass.
    """
    path = Path(acf_path)
    if not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        m_flags = re.search(r'"StateFlags"\s*"(\d+)"', content)
        if m_flags:
            new_content = re.sub(
                r'"StateFlags"\s*"\d+"',
                f'"StateFlags"\t\t"{state_flags}"',
                content,
                count=1,
            )
        else:
            m_appstate = re.search(r'("AppState"\s*\{)', content)
            if m_appstate:
                pos = m_appstate.end()
                new_content = (
                    content[:pos]
                    + f'\n\t"StateFlags"\t\t"{state_flags}"'
                    + content[pos:]
                )
            else:
                new_content = content + f'\n"StateFlags"\t\t"{state_flags}"\n'

        m_upd = re.search(r'"UpdateResult"\s*"(\d+)"', new_content)
        if m_upd and m_upd.group(1) != "0":
            new_content = re.sub(
                r'"UpdateResult"\s*"\d+"',
                '"UpdateResult"\t\t"0"',
                new_content,
                count=1,
            )

        ok = atomic_write_acf(str(path), new_content)
        if ok:
            logger.info(f"[SteamACF] Set StateFlags={state_flags} on {acf_path}")
        return ok
    except Exception as e:
        logger.warning(f"[SteamACF] Failed setting StateFlags={state_flags} on {acf_path}: {e}")
        return False


def _discover_depots_from_disk(acf_path: str, appid: str) -> List[str]:
    """Discover depot IDs from .manifest files in the depotcache directory.

    Scans {steam_library}/steamapps/depotcache/ for files matching the pattern
    app{appid}_depot{depot_id}.manifest and returns the list of discovered
    depot IDs. Used as a fallback when the ACF has empty InstalledDepots.
    """
    discovered: List[str] = []
    try:
        from core.steam_helpers import get_steam_libraries

        for lib_path in get_steam_libraries() or []:
            depotcache_dir = Path(lib_path) / "steamapps" / "depotcache"
            if not depotcache_dir.is_dir():
                continue

            pattern = f"app{appid}_depot*.manifest"
            for manifest_file in depotcache_dir.glob(pattern):
                # Filename format: app{appid}_depot{depot_id}.manifest
                try:
                    name = manifest_file.stem  # e.g. "app2929250_depot2929252"
                    parts = name.split("_")
                    if len(parts) >= 3:
                        depot_id = parts[-1]  # last part after split is depot ID
                        if depot_id.isdigit() and depot_id != appid:
                            discovered.append(depot_id)
                except Exception as ex:
                    logger.debug(f"[SteamACF] Error parsing manifest filename {manifest_file}: {ex}")
                    continue
    except Exception as e:
        logger.debug(f"[SteamACF] Could not discover depots from disk: {e}")

    return sorted(set(discovered))
_DEPOT_ENTRY_RE = re.compile(r'([ \t]*)"(\d+)"[ \t]*\r?\n[ \t]*\{(.*?)\r?\n[ \t]*\}', re.DOTALL)


def _parse_installed_depots(body: str) -> Tuple[List[str], Dict[str, str]]:
    """Return (depot ids in file order, {depot id: verbatim entry text})."""
    order: List[str] = []
    entries: Dict[str, str] = {}
    for m in _DEPOT_ENTRY_RE.finditer(body):
        did = m.group(2)
        if did not in entries:
            entries[did] = m.group(0)
            order.append(did)
    return order, entries


def _format_depot_entry(indent: str, depot_id: str, size: int) -> str:
    """Render one InstalledDepots entry.

    ``"manifest" "0"`` tells Steam "manifest unknown, verify it yourself". This is
    the correct hint: Steam then matches the depot against the files already on
    disk instead of pinning a stale GID.
    """
    return (
        f'{indent}"{depot_id}"\n'
        f"{indent}{{\n"
        f'{indent}\t"manifest"\t\t"0"\n'
        f'{indent}\t"size"\t\t"{size}"\n'
        f"{indent}}}"
    )


def _merge_installed_depots(
    content: str, wanted: List[str], per_depot_size: int
) -> Tuple[str, List[str]]:
    """Add the depots from ``wanted`` that the ACF does not list yet.

    Existing entries are kept verbatim -- only the depots Steam does not know
    about are added. Returns (new content, list of depot ids that were added).
    """
    block = _find_vdf_block(content, "InstalledDepots")
    if block is None:
        return content, []
    body_start, body_end, body = block
    order, entries = _parse_installed_depots(body)
    missing = [d for d in wanted if d not in entries]
    if not missing:
        return content, []

    indent = "\t\t"
    first = _DEPOT_ENTRY_RE.search(body)
    if first:
        indent = first.group(1)

    entry_texts = [entries[d] for d in order]
    entry_texts.extend(_format_depot_entry(indent, d, per_depot_size) for d in missing)
    new_body = "\n" + "\n".join(entry_texts) + "\n"
    return content[:body_start] + new_body + content[body_end:], missing


def _insert_installed_depots_block(
    content: str, wanted: List[str], per_depot_size: int
) -> Tuple[str, List[str]]:
    """Create the InstalledDepots block when the ACF does not have one yet."""
    if not wanted:
        return content, []
    entries = [_format_depot_entry("\t\t", d, per_depot_size) for d in wanted]
    new_block = '\t"InstalledDepots"\n\t{\n' + "\n".join(entries) + "\n\t}"
    m = re.search(r'("AppState"\s*\{)', content)
    if not m:
        return content, []
    return content[: m.end()] + "\n" + new_block + content[m.end():], list(wanted)


def _measure_install_size(game_data: Dict[str, Any]) -> int:
    """Total size of the already-downloaded install, in bytes (0 when unknown)."""
    install_path = game_data.get("install_path")
    if not install_path and game_data.get("library_path"):
        folder_name = game_data.get("installdir") or game_data.get("game_name")
        if folder_name:
            cand_install = Path(game_data["library_path"]) / "steamapps" / "common" / folder_name
            if cand_install.is_dir():
                install_path = str(cand_install)

    if not install_path or not os.path.isdir(install_path):
        return 0

    actual_size = 0
    try:
        for root, _, files in os.walk(install_path):
            for file in files:
                try:
                    actual_size += os.path.getsize(os.path.join(root, file))
                except OSError as e:
                    logger.debug(f"[SteamACF] Could not stat a file while measuring the install size: {e}")
    except OSError as e:
        logger.warning(f"[SteamACF] Error calculating size: {e}")
    return actual_size


def find_appmanifest_path(game_data: Dict[str, Any], appid: str) -> Optional[str]:
    """Locate the Steam appmanifest of a game, if it exists on disk.

    Both migration entry points need the same lookup, and both need it to
    behave the same way: the path the library reported, then the Steam
    library folder, then a scan of the configured Steam libraries.
    """
    acf_path = game_data.get("appmanifest_path")
    if acf_path and os.path.exists(acf_path):
        return str(acf_path)

    library_path = game_data.get("library_path")
    if library_path:
        candidate = Path(library_path) / "steamapps" / f"appmanifest_{appid}.acf"
        if candidate.exists():
            return str(candidate)

    try:
        from core.steam_helpers import get_steam_libraries

        for steam_lib in get_steam_libraries() or []:
            candidate = Path(steam_lib) / "steamapps" / f"appmanifest_{appid}.acf"
            if candidate.exists():
                return str(candidate)
    except Exception as e:
        logger.debug(f"[SteamACF] Steam library scan failed for {appid}: {e}")

    return None


def parse_acf_file(acf_path: Union[str, Path]) -> Dict[str, Any]:
    """Parse an ACF / appmanifest file into a dictionary using vdf, with fallback."""
    try:
        import vdf
        with open(acf_path, "r", encoding="utf-8", errors="replace") as f:
            data = vdf.loads(f.read())
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.debug(f"[SteamACF] vdf parsing failed for {acf_path}: {e}")

    res: Dict[str, Any] = {"AppState": {"InstalledDepots": {}}}
    try:
        content = Path(acf_path).read_text(encoding="utf-8", errors="replace")
        block = _find_vdf_block(content, "InstalledDepots")
        if block:
            _, entries = _parse_installed_depots(block[2])
            for did, mid in entries.items():
                res["AppState"]["InstalledDepots"][str(did)] = {"manifest": str(mid)}
    except Exception as e:
        logger.debug(f"[SteamACF] Fallback parsing failed for {acf_path}: {e}")
    return res


def ensure_acf_has_installed_depots(
    acf_path: str,
    appid: str,
    depot_ids: list,
    game_data: Dict[str, Any],
    steam_running: Optional[bool] = None,
) -> str:
    """Make Steam adopt an already-downloaded install by listing its depots in the ACF.

    Depots Steam already knows about are kept as they are; only the missing ones
    are added. Also repairs corrupted StateFlags and empty InstalledDepots blocks
    that are common after ASSella-to-AT0M transfers.

    Returns one of ACF_WRITTEN / ACF_DEFERRED / ACF_SKIPPED / ACF_FAILED.
    """
    if not acf_path or not os.path.exists(acf_path):
        return ACF_SKIPPED

    try:
        with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        original_content = content

        wanted = [
            str(d).strip()
            for d in (depot_ids or [])
            if str(d).strip() and str(d).strip() != str(appid)
        ]

        m_size = re.search(r'"SizeOnDisk"\s*"(\d+)"', content)
        needs_size = m_size is None or m_size.group(1) == "0"

        block = _find_vdf_block(content, "InstalledDepots")
        if block is None:
            missing = list(wanted)
        else:
            _, entries = _parse_installed_depots(block[2])
            missing = [d for d in wanted if d not in entries]

        # Check for broken ACF: empty depots + zero size (common after AT0M transfer)
        is_broken = _detect_broken_acf(content)
        if is_broken and not wanted:
            # No depots provided — discover from disk as fallback
            disk_depots = _discover_depots_from_disk(acf_path, appid)
            if disk_depots:
                wanted = disk_depots
                missing = list(wanted)
                logger.info(
                    f"[SteamACF] Discovered {len(wanted)} depot(s) from disk for {appid}: {wanted}"
                )

        # Normalize StateFlags (e.g. 518 -> 4) and UpdateResult if corrupted
        content, flags_fixed = _normalize_state_flags(content)

        # Decide whether there is anything to write BEFORE asking whether Steam
        # is running.
        needs_depots = bool(missing)
        should_fix = is_broken or needs_size or needs_depots or flags_fixed

        if not should_fix:
            logger.debug(
                f"[SteamACF] {acf_path} already lists all {len(wanted)} depot(s) for {appid}; nothing to do."
            )
            return ACF_SKIPPED

        if steam_running is None:
            steam_running = steam_is_running()
        if steam_running:
            logger.info(
                f"[SteamACF] Steam is running - deferring the manifest update for {appid} "
                "(Steam rewrites appmanifest_*.acf from its own state when it exits)."
            )
            return ACF_DEFERRED

        # Measuring walks the whole install, so it happens only once a write is
        # actually going to happen.
        actual_size = _measure_install_size(game_data or {})
        if not missing and actual_size <= 0 and not needs_size and not is_broken:
            # Only SizeOnDisk was missing, and the install could not be
            # measured: writing "0" would be worse than leaving the file alone.
            logger.debug(f"[SteamACF] No install size available for {appid}; leaving {acf_path} untouched.")
            return ACF_SKIPPED

        # Per-depot sizes are not known individually, so the measured install
        # size is split across the wanted depots. Only the entries we add use
        # this number; existing entries keep their own size, so the resulting
        # sum can differ slightly from SizeOnDisk when some depots were already
        # listed.
        per_depot_size = actual_size if len(wanted) == 1 else actual_size // max(1, len(wanted))

        if block is None:
            content, added = _insert_installed_depots_block(content, wanted, per_depot_size)
        else:
            content, added = _merge_installed_depots(content, wanted, per_depot_size)

        if needs_size and actual_size > 0:
            if m_size:
                content = re.sub(r'"SizeOnDisk"\s*"\d+"', f'"SizeOnDisk"\t\t"{actual_size}"', content)
            else:
                content = re.sub(
                    r'("AppState"\s*\{)', rf'\1\n\t"SizeOnDisk"\t\t"{actual_size}"', content
                )

        if content == original_content:
            logger.debug(f"[SteamACF] No effective change for {acf_path}; skipping the write.")
            return ACF_SKIPPED

        if not atomic_write_acf(acf_path, content):
            return ACF_FAILED
        logger.info(
            f"[SteamACF] Updated ACF {acf_path}: {len(wanted)} depot(s), {len(added)} added, "
            f"flags_fixed={flags_fixed}, size_fixed={needs_size}, broken_repaired={is_broken}"
        )
        return ACF_WRITTEN
    except Exception as e:
        logger.warning(f"[SteamACF] Graceful fallback on updating ACF {acf_path}: {e}")
        return ACF_FAILED
