"""
Client-side assembly of Steam beta-branch bundles.

Hubcap's ``GET /api/v1/manifest/{app_id}`` only ever serves the PUBLIC bundle
(``{app_id}/public/{app_id}.zip``: a lua with the depot keys + the public depot
manifests). It has no branch parameter, and the branch-aware
``/generate/appmanifest`` endpoint is disabled server-side. The only Hubcap
endpoint that can hand out a non-public manifest is
``GET /api/v1/generate/manifest?depot_id=&manifest_id=``, which works by GID.

So a beta-branch bundle is put together here:

  1. depot keys / app token / depot list  -> the public bundle's lua
  2. the branch's manifest GID per depot   -> Steam PICS (``manifests[branch]``)
  3. each manifest that differs from public -> ``/generate/manifest`` by GID
  4. the lua's ``setManifestid`` lines are rewritten to the branch GIDs

The result is a real ``accela_fetch_{app_id}_branch_{branch}.zip`` whose lua and
manifest files describe the branch build, so every downstream consumer
(ProcessZipTask, update checks, pin-build snapshots, verify, rollback) works
unchanged.
"""

import io
import logging
import re
import zipfile
from pathlib import Path
from typing import Dict, Optional, Tuple

from utils.branch_helpers import resolve_branch_manifest_gid

logger = logging.getLogger(__name__)

# setManifestid(<depot>, "<gid>"[, <size>])
_SET_MANIFEST_RE = re.compile(
    r'(setManifestid\(\s*(\d+)\s*,\s*")([^"]+)(")(\s*,\s*\d+\s*)?(\s*\))',
    re.IGNORECASE,
)


def _rewrite_lua(lua: str, new_gids: Dict[str, str], new_sizes: Dict[str, str], dropped: set) -> str:
    """Rewrite setManifestid lines: swap GIDs (and sizes) for remapped depots and
    delete the lines of depots that are not part of the branch build. Lines are
    removed rather than commented out because the lua parsers match the call
    with a regex and would still pick up a commented line."""
    out = []
    for line in lua.splitlines(keepends=True):
        m = _SET_MANIFEST_RE.search(line)
        if not m:
            out.append(line)
            continue
        depot_id = m.group(2)
        if depot_id in dropped:
            continue
        gid = new_gids.get(depot_id)
        if not gid:
            out.append(line)
            continue
        size = new_sizes.get(depot_id)
        size_part = f", {size}" if size else ""
        out.append(line[:m.start()] + f"{m.group(1)}{gid}{m.group(4)}{size_part}{m.group(6)}" + line[m.end():])
    return "".join(out)


def build_branch_bundle(
    app_id, branch: str, public_zip_path, save_path
) -> Tuple[Optional[str], Optional[str]]:
    """
    Build the bundle for ``branch`` from the freshly downloaded public bundle.

    Returns (zip_path, None) on success, or (None, error_message) on failure.
    Never falls back to returning the public bundle: installing public files
    under a beta label is exactly the bug this module exists to prevent.
    """
    from core import morrenus_api
    from core.steam_api import get_depot_info_from_api
    from core.tasks.process_zip_task import ProcessZipTask

    app_id = str(app_id)
    branch = branch or "public"
    public_zip_path = Path(public_zip_path)
    save_path = Path(save_path)

    if branch == "public":
        return str(public_zip_path), None

    # ── 1. Public bundle: lua + manifest files ─────────────────────────────
    try:
        with zipfile.ZipFile(public_zip_path, "r") as zf:
            lua_names = [n for n in zf.namelist() if n.endswith(".lua")]
            if not lua_names:
                return None, f"Public bundle for {app_id} contains no lua file; cannot build branch '{branch}'."
            lua_name = lua_names[0]
            lua_text = zf.read(lua_name).decode("utf-8", errors="ignore")
            public_manifests = {
                n: zf.read(n) for n in zf.namelist() if n.endswith(".manifest")
            }
    except (OSError, zipfile.BadZipFile) as e:
        return None, f"Cannot read public bundle {public_zip_path.name}: {e}"

    lua_gids: Dict[str, str] = {}
    for m in _SET_MANIFEST_RE.finditer(lua_text):
        lua_gids[m.group(2)] = m.group(3).strip()
    # Manifest files present in the zip are authoritative for the GID (same rule
    # ProcessZipTask applies), so index them by depot too.
    public_files_by_depot: Dict[str, Tuple[str, str]] = {}  # depot -> (gid, filename)
    for fname in public_manifests:
        parts = fname.replace(".manifest", "").split("_")
        if len(parts) == 2:
            public_files_by_depot[parts[0]] = (parts[1], fname)
            lua_gids[parts[0]] = parts[1]

    if not lua_gids:
        return None, f"Public bundle for {app_id} lists no depot manifests; cannot build branch '{branch}'."

    app_token = ProcessZipTask._extract_app_token(lua_text, app_id)

    # ── 2. Branch GIDs from Steam PICS ─────────────────────────────────────
    try:
        api_data = get_depot_info_from_api(int(app_id), app_token, force_refresh=True)
    except Exception as e:
        logger.error(f"[BranchBundle] PICS lookup failed for {app_id}: {e}", exc_info=True)
        api_data = None
    api_depots = (api_data or {}).get("depots") or {}
    branches = (api_data or {}).get("branches") or {}
    if not api_depots:
        return None, (
            f"Could not read Steam depot info for {app_id}; the '{branch}' branch manifests "
            f"cannot be resolved. Try again when Steam is reachable."
        )
    if isinstance(branches, dict) and branches and branch not in branches:
        return None, (
            f"Branch '{branch}' does not exist (or is password protected) for app {app_id}. "
            f"Available: {', '.join(sorted(branches.keys()))}"
        )

    # ── 3. Resolve per-depot GIDs and fetch the ones that differ ───────────
    new_gids: Dict[str, str] = {}
    new_sizes: Dict[str, str] = {}
    dropped = set()
    to_generate: Dict[str, str] = {}
    for depot_id, public_gid in lua_gids.items():
        dinfo = api_depots.get(depot_id)
        if not isinstance(dinfo, dict) or not isinstance(dinfo.get("manifests"), dict):
            # Shared / unlisted depot: Steam gives us nothing branch-specific, keep as is.
            continue
        gid = resolve_branch_manifest_gid(
            dinfo, branch, allow_public_fallback=bool(dinfo.get("from_dlc_app"))
        )
        if not gid:
            dropped.add(depot_id)
            continue
        if gid == str(public_gid):
            continue
        new_gids[depot_id] = gid
        entry = dinfo["manifests"].get(branch)
        if isinstance(entry, dict):
            size = entry.get("size") or entry.get("download")
            if size:
                new_sizes[depot_id] = str(size)
        to_generate[depot_id] = gid

    logger.info(
        f"[BranchBundle] {app_id} branch '{branch}': {len(new_gids)} depot(s) differ from public, "
        f"{len(lua_gids) - len(new_gids) - len(dropped)} shared with public, {len(dropped)} not in branch "
        f"{sorted(dropped) if dropped else ''}"
    )

    generated: Dict[str, bytes] = {}
    failed = []
    for depot_id, gid in to_generate.items():
        raw, err = morrenus_api.generate_single_manifest(depot_id, gid)
        if raw and not err:
            generated[f"{depot_id}_{gid}.manifest"] = raw
        else:
            # Keep the branch GID in the lua anyway: DownloadDepotsTask retries the
            # generation on demand for the depots the user actually selects.
            failed.append(depot_id)
            logger.warning(f"[BranchBundle] /generate/manifest failed for depot {depot_id} gid {gid}: {err}")

    # ── 4. Write the branch bundle ─────────────────────────────────────────
    branch_lua = _rewrite_lua(lua_text, new_gids, new_sizes, dropped)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(lua_name, branch_lua)
        for depot_id, (gid, fname) in public_files_by_depot.items():
            if depot_id in dropped or depot_id in new_gids:
                continue
            zf.writestr(fname, public_manifests[fname])
        for fname, raw in generated.items():
            zf.writestr(fname, raw)

    try:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(buf.getvalue())
    except OSError as e:
        return None, f"Failed to write branch bundle {save_path.name}: {e}"

    if failed:
        logger.warning(
            f"[BranchBundle] Bundle {save_path.name} written without {len(failed)} manifest(s) "
            f"({', '.join(failed)}); they will be generated on demand at download time."
        )
    logger.info(f"[BranchBundle] Saved branch bundle to {save_path}")
    return str(save_path), None
