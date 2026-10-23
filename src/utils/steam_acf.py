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


def _resolve_depot_manifest(
    depot_id: str,
    appid: str = "",
    game_data: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Find the best matching manifest ID for a depot from metadata, depotcache, or bundle cache."""
    depot_id_str = str(depot_id)

    # 1. From game_data metadata/manifests
    if game_data:
        for m_dict in (
            game_data.get("manifests"),
            game_data.get("metadata", {}).get("manifests"),
            game_data.get("manifest_overrides"),
        ):
            if isinstance(m_dict, dict) and m_dict.get(depot_id_str):
                mid = str(m_dict[depot_id_str]).strip()
                if mid.isdigit() and mid != "0":
                    return mid

        depot_info = (game_data.get("depots") or {}).get(depot_id_str)
        if isinstance(depot_info, dict):
            gid = depot_info.get("manifest_id") or depot_info.get("gid")
            if not (gid and str(gid).isdigit() and str(gid) != "0"):
                try:
                    from utils.branch_helpers import resolve_branch_manifest_gid
                    gid = resolve_branch_manifest_gid(depot_info, game_data.get("branch") or "public")
                except Exception:
                    pass
            if gid and str(gid).isdigit() and str(gid) != "0":
                return str(gid)

    # 2. From Steam active depotcache directories
    try:
        from core.native_steam.native_steam_handoff import get_depotcache_dirs
        for ddir in get_depotcache_dirs():
            if not ddir.is_dir():
                continue
            candidates: List[Tuple[str, float]] = []
            for mf in ddir.glob(f"{depot_id_str}_*.manifest"):
                m_file = re.match(rf"^{depot_id_str}_(\d+)\.manifest$", mf.name)
                if m_file:
                    mid = m_file.group(1)
                    if mid != "0":
                        try:
                            candidates.append((mid, mf.stat().st_mtime))
                        except OSError:
                            candidates.append((mid, 0.0))
            if candidates:
                candidates.sort(key=lambda x: x[1], reverse=True)
                return candidates[0][0]
    except Exception as e:
        logger.debug(f"[SteamACF] depotcache lookup failed for depot {depot_id_str}: {e}")

    # 3. From cached Hubcap bundles in hubcap_manifests
    if appid:
        try:
            import zipfile
            from utils.helpers import get_base_path
            hubcap_dir = Path(get_base_path()) / "hubcap_manifests"
            if hubcap_dir.exists():
                for z_path in hubcap_dir.glob(f"accela_fetch_{appid}*.zip"):
                    try:
                        with zipfile.ZipFile(z_path, "r") as zf:
                            for name in zf.namelist():
                                base_name = os.path.basename(name)
                                m_file = re.match(rf"^{depot_id_str}_(\d+)\.manifest$", base_name)
                                if m_file:
                                    mid = m_file.group(1)
                                    if mid != "0":
                                        return mid
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"[SteamACF] hubcap_manifests lookup failed for depot {depot_id_str}: {e}")

    return None


def _format_depot_entry(indent: str, depot_id: str, size: int, manifest_id: Optional[str] = None) -> str:
    """Render one InstalledDepots entry.

    ``"manifest" "0"`` tells Steam "manifest unknown, verify it yourself". This is
    the correct hint: Steam then matches the depot against the files already on
    disk instead of pinning a stale GID.
    """
    mid = manifest_id if (manifest_id and str(manifest_id).isdigit() and str(manifest_id) != "0") else "0"
    return (
        f'{indent}"{depot_id}"\n'
        f"{indent}{{\n"
        f'{indent}\t"manifest"\t\t"{mid}"\n'
        f'{indent}\t"size"\t\t"{size}"\n'
        f"{indent}}}"
    )


def _merge_installed_depots(
    content: str,
    wanted: List[str],
    per_depot_size: int,
    appid: str = "",
    game_data: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[str], List[str]]:
    """Add missing depots and upgrade existing depot entries that have manifest "0".

    Returns (new content, list of depot ids that were added, list of depot ids whose manifest was upgraded).
    """
    block = _find_vdf_block(content, "InstalledDepots")
    if block is None:
        return content, [], []
    body_start, body_end, body = block
    order, entries = _parse_installed_depots(body)
    missing = [d for d in wanted if d not in entries]

    indent = "\t\t"
    first = _DEPOT_ENTRY_RE.search(body)
    if first:
        indent = first.group(1)

    entry_texts: List[str] = []
    upgraded: List[str] = []
    for d in order:
        txt = entries[d]
        if re.search(r'"manifest"\s*"0"', txt):
            real_mid = _resolve_depot_manifest(d, appid, game_data)
            if real_mid:
                txt = re.sub(r'"manifest"\s*"0"', f'"manifest"\t\t"{real_mid}"', txt)
                upgraded.append(d)
        entry_texts.append(txt)

    added: List[str] = []
    for d in missing:
        real_mid = _resolve_depot_manifest(d, appid, game_data)
        entry_texts.append(_format_depot_entry(indent, d, per_depot_size, real_mid))
        added.append(d)

    new_body = "\n" + "\n".join(entry_texts) + "\n"
    return content[:body_start] + new_body + content[body_end:], added, upgraded


def _insert_installed_depots_block(
    content: str,
    wanted: List[str],
    per_depot_size: int,
    appid: str = "",
    game_data: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[str]]:
    """Create the InstalledDepots block when the ACF does not have one yet."""
    if not wanted:
        return content, []
    entries = [
        _format_depot_entry("\t\t", d, per_depot_size, _resolve_depot_manifest(d, appid, game_data))
        for d in wanted
    ]
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
        has_upgradeable_manifest = False
        if block is None:
            missing = list(wanted)
        else:
            _, entries = _parse_installed_depots(block[2])
            missing = [d for d in wanted if d not in entries]
            has_upgradeable_manifest = any(
                re.search(r'"manifest"\s*"0"', ent)
                and _resolve_depot_manifest(did, appid, game_data) is not None
                for did, ent in entries.items()
            )

        # Decide whether there is anything to write BEFORE asking whether Steam
        # is running. A manifest that already lists every depot, has no upgradeable
        # "0" manifests, and has a real SizeOnDisk needs no write.
        if not missing and not needs_size and not has_upgradeable_manifest:
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
        if not missing and not has_upgradeable_manifest and actual_size <= 0:
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

        upgraded: List[str] = []
        if block is None:
            content, added = _insert_installed_depots_block(
                content, wanted, per_depot_size, appid=appid, game_data=game_data
            )
        else:
            content, added, upgraded = _merge_installed_depots(
                content, wanted, per_depot_size, appid=appid, game_data=game_data
            )

        if needs_size and actual_size > 0:
            if m_size:
                content = re.sub(r'"SizeOnDisk"\s*"\d+"', f'"SizeOnDisk"\t\t"{actual_size}"', content)
            else:
                content = re.sub(
                    r'("AppState"\s*\{)', rf'\1\n\t"SizeOnDisk"\t\t"{actual_size}"', content
                )

        # If depots were upgraded or mounted, restore StateFlags to 4 (StateFullyInstalled)
        # and clear UpdateResult if Steam previously marked it update-required/paused due to 401 CDN rejections.
        m_flags = re.search(r'"StateFlags"\s*"(\d+)"', content)
        if m_flags and m_flags.group(1) in ("518", "534", "6"):
            content = re.sub(r'"StateFlags"\s*"(518|534|6)"', '"StateFlags"\t\t"4"', content)
            content = re.sub(r'"UpdateResult"\s*"[1-9]\d*"', '"UpdateResult"\t\t"0"', content)

        if content == original_content:
            logger.debug(f"[SteamACF] No effective change for {acf_path}; skipping the write.")
            return ACF_SKIPPED

        if not atomic_write_acf(acf_path, content):
            return ACF_FAILED
        logger.info(
            f"[SteamACF] Updated ACF {acf_path}: {len(wanted)} depot(s) wanted, "
            f"{len(added)} added, {len(upgraded)} manifest(s) upgraded"
        )
        return ACF_WRITTEN
    except Exception as e:
        logger.warning(f"[SteamACF] Graceful fallback on updating ACF {acf_path}: {e}")
        return ACF_FAILED
