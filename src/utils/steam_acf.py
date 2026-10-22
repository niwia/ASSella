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
* StateFlags and AutoUpdateBehavior are never touched. The files on disk are
  current for the pinned build, so declaring "update required" only invites
  Steam to re-fetch a manifest for content we already have.
* The write never happens while Steam is running. Steam rewrites
  ``appmanifest_*.acf`` from its own in-memory state, so a manifest we edit
  underneath it is either clobbered or fought over. The caller is told the
  update was deferred so it can be retried when Steam is closed.
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


# One InstalledDepots entry: the depot id on its own line, then its own block.
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
    are added. Returns one of ACF_WRITTEN / ACF_DEFERRED / ACF_SKIPPED /
    ACF_FAILED so the caller can tell the user what actually happened.
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

        # Decide whether there is anything to write BEFORE asking whether Steam
        # is running. A manifest that already lists every depot and has a real
        # SizeOnDisk needs no write, so reporting it as "deferred" would send
        # the user to a pointless re-run for a game that is already correct --
        # and on most desktops Steam is always running, so that would be every
        # game.
        if not missing and not needs_size:
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
        if not missing and actual_size <= 0:
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
            f"[SteamACF] Updated ACF {acf_path}: {len(wanted)} depot(s) wanted, "
            f"{len(added)} added (StateFlags and manifests left untouched)"
        )
        return ACF_WRITTEN
    except Exception as e:
        logger.warning(f"[SteamACF] Graceful fallback on updating ACF {acf_path}: {e}")
        return ACF_FAILED
