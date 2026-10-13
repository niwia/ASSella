"""Authoritative manifest resolution for the Verify flow.

Verify must answer one question against *live* Steam data, never against the
24h update cache: do the local manifests match the current Steam PICS depot
GIDs? Tiers (cheapest first):

* ``LOCAL_CURRENT``  – 100% GID match, verify with the local bundle, 0 fetch.
* ``PROVIDER_REFRESH`` – stale GIDs but keys known: regenerate each manifest
  via ``generate_single_manifest`` (honours the user's manifest_provider
  setting, MRC + Steam CDN first, Hubcap fallback), rewrite the bundle.
* ``HUBCAP_FULL`` – brand-new depots (or stale depots) with no cached depot
  key: only a Hubcap full-zip fetch can supply the missing encryption key.
  This burns daily Hubcap quota, so the caller must ask the user first.
* ``OFFLINE`` – live PICS *and* the steamcmd fallback both failed: caller
  offers Retry / verify-with-local / Cancel. Never a hard error.

Pinned builds never reach this module: they verify strictly against the
pinned build zip with no network manifest resolution at all.
"""

import logging
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

LOCAL_CURRENT = "local_current"
PROVIDER_REFRESH = "provider_refresh"
HUBCAP_FULL = "hubcap_full"
OFFLINE = "offline"


def read_bundle_gids(zip_path: Any) -> Dict[str, str]:
    """Read {depot_id: manifest_gid} from a cached Hubcap bundle zip.

    Also merges standalone ``{depot}_{gid}.manifest`` files for already-known
    depots (mirrors manifest_fetcher), so Tier-0 compares the full local set.
    """
    local: Dict[str, str] = {}
    try:
        zp = Path(zip_path)
    except Exception:
        return local
    if not zp.exists():
        return local
    try:
        with zipfile.ZipFile(zp, "r") as zr:
            for name in zr.namelist():
                if not name.endswith(".manifest"):
                    continue
                parts = os.path.basename(name).replace(".manifest", "").split("_")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    local[parts[0]] = parts[1]
    except Exception as e:
        logger.warning(f"[VerifyFlow] Failed to parse cached bundle {zp}: {e}")
        return {}

    try:
        from managers.depot_key_manager import DepotKeyManager
        known: set = set(local.keys())
        try:
            known.update(DepotKeyManager().get_depot_keys(_app_of_zip(zp)).keys())
        except Exception:
            pass
        try:
            from utils.helpers import get_base_path
            base = Path(get_base_path())
        except Exception:
            base = None
        standalone_dirs = [Path(tempfile.gettempdir()) / "mistwalker_manifests"]
        if base is not None:
            standalone_dirs.append(base / "manifests")
        for s_dir in standalone_dirs:
            if not s_dir.exists():
                continue
            for mf in s_dir.glob("*.manifest"):
                parts = mf.stem.split("_")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    if parts[0] in known:
                        local[parts[0]] = parts[1]
    except Exception as e:
        logger.debug(f"[VerifyFlow] Standalone manifest scan failed: {e}")
    return local


def _app_of_zip(zip_path: Path) -> str:
    # accela_fetch_{appid}.zip / accela_fetch_{appid}_branch_{b}.zip /
    # accela_fetch_{appid}_build_{bid}.zip
    try:
        stem = zip_path.stem
        if stem.startswith("accela_fetch_"):
            rest = stem[len("accela_fetch_"):]
            return rest.split("_")[0]
    except Exception:
        pass
    return ""


def extract_bundle_token(zip_path: Any) -> Optional[str]:
    """Best-effort AppToken read from the bundle lua (for authenticated PICS)."""
    try:
        from utils.lua_parsing import extract_token
        with zipfile.ZipFile(zip_path, "r") as zr:
            luas = [n for n in zr.namelist() if n.endswith(".lua")]
            if luas:
                content = zr.read(luas[0]).decode("utf-8", errors="ignore")
                return extract_token(content)
    except Exception as e:
        logger.debug(f"[VerifyFlow] Token extract failed: {e}")
    return None


def get_live_depots(app_id: str, app_token: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Live depot map via PICS with steamcmd fallback (force_refresh=True).

    Returns (depots, full_data). Both empty on total failure (OFFLINE).
    """
    try:
        from core.steam_api import get_depot_info_from_api
        data = get_depot_info_from_api(str(app_id), app_token, force_refresh=True)
        if isinstance(data, dict) and isinstance(data.get("depots"), dict) and data["depots"]:
            return data["depots"], data
        logger.warning(f"[VerifyFlow] Live PICS+steamcmd returned no depots for AppID {app_id}")
    except Exception as e:
        logger.warning(f"[VerifyFlow] Live depot lookup failed for AppID {app_id}: {e}")
    return {}, {}


def decide_verify_source(
    app_id: str,
    branch: str,
    local_gids: Dict[str, str],
    live_depots: Dict[str, Any],
    live_data: Optional[Dict[str, Any]],
    cached_keys: Dict[str, str],
) -> Tuple[str, List[Dict[str, Any]], List[Tuple[str, str, str]]]:
    """Pick the verify tier. Returns (decision, refreshable_stale, hubcap_needed).

    * refreshable_stale: [{'depot_id','cached_manifest_id','current_manifest_id','name'}]
      with a known depot key — fetchable via provider/CDN for free.
    * hubcap_needed: [(depot_id, manifest_id, name)] with NO known key —
      only a Hubcap full zip can supply the key (quota cost: ask the user).
    """
    from utils.depot_utils import check_hubcap_vs_steam_depots

    live_data = live_data if isinstance(live_data, dict) else {}
    try:
        check = check_hubcap_vs_steam_depots(
            local_gids,
            live_depots,
            app_id=app_id,
            branch=branch or "public",
            base_languages=live_data.get("baselanguages"),
        )
    except Exception as e:
        logger.error(f"[VerifyFlow] Depot comparison failed for AppID {app_id}: {e}")
        return OFFLINE, [], []

    keys = {str(k): v for k, v in (cached_keys or {}).items()}
    refreshable: List[Dict[str, Any]] = []
    hubcap_needed: List[Tuple[str, str, str]] = []

    for stale in check.get("stale_depots", []):
        did = str(stale["depot_id"])
        if keys.get(did):
            refreshable.append(stale)
        else:
            hubcap_needed.append((did, str(stale["current_manifest_id"]), stale.get("name") or f"Depot {did}"))

    for missing_did, missing_mid, depot_name in check.get("missing_for_fetch", []):
        did = str(missing_did)
        if keys.get(did):
            refreshable.append({
                "depot_id": did,
                "cached_manifest_id": "",
                "current_manifest_id": str(missing_mid),
                "name": depot_name or f"Depot {did}",
            })
        else:
            hubcap_needed.append((did, str(missing_mid), depot_name or f"Depot {did}"))

    if hubcap_needed:
        return HUBCAP_FULL, refreshable, hubcap_needed
    if refreshable:
        return PROVIDER_REFRESH, refreshable, []
    return LOCAL_CURRENT, [], []


def refresh_via_provider(
    app_id: str,
    branch: str,
    cached_zip_path: Any,
    refresh_list: List[Dict[str, Any]],
    key_lookup: Optional[Callable[[str], Optional[str]]] = None,
    generate_fn: Optional[Callable[..., Tuple[Optional[bytes], Optional[str]]]] = None,
    manifest_dirs: Optional[List[Any]] = None,
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Regenerate stale manifests via the user's provider and rewrite the bundle.

    Returns (updated_depot_ids, failed[(depot_id, error)]). Successful
    manifests are written to the standalone dirs AND spliced into the cached
    zip (old GID files replaced), mirroring manifest_fetcher.
    """
    from core import morrenus_api

    if generate_fn is None:
        generate_fn = morrenus_api.generate_single_manifest
    if key_lookup is None:
        def key_lookup(did: str) -> Optional[str]:
            try:
                from managers.depot_key_manager import DepotKeyManager
                return DepotKeyManager().get_depot_keys(str(app_id)).get(str(did))
            except Exception:
                return None
    if manifest_dirs is None:
        try:
            from utils.helpers import get_base_path
            base = Path(get_base_path())
        except Exception:
            base = None
        manifest_dirs = [Path(tempfile.gettempdir()) / "mistwalker_manifests"]
        if base is not None:
            manifest_dirs.append(base / "manifests")

    updated: List[str] = []
    failed: List[Tuple[str, str]] = []
    new_files: Dict[str, Tuple[str, str, bytes]] = {}

    for entry in refresh_list:
        did = str(entry["depot_id"])
        mid = str(entry["current_manifest_id"])
        old_mid = str(entry.get("cached_manifest_id") or "")
        key = key_lookup(did)
        try:
            raw_bytes, gen_err = generate_fn(did, mid, depot_key=key)
        except TypeError:
            # Test doubles / older signatures without depot_key
            try:
                raw_bytes, gen_err = generate_fn(did, mid)
            except Exception as e:
                raw_bytes, gen_err = None, str(e)
        except Exception as e:
            raw_bytes, gen_err = None, str(e)
        if raw_bytes:
            new_files[did] = (mid, old_mid, raw_bytes)
            updated.append(did)
            logger.info(f"[VerifyFlow] Refreshed manifest for depot {did} (GID {mid}) via provider")
        else:
            failed.append((did, gen_err or "unknown error"))
            logger.warning(f"[VerifyFlow] Provider refresh failed for depot {did}: {gen_err}")

    for did, (mid, _, m_bytes) in new_files.items():
        for m_dir in manifest_dirs:
            try:
                Path(m_dir).mkdir(parents=True, exist_ok=True)
                (Path(m_dir) / f"{did}_{mid}.manifest").write_bytes(m_bytes)
            except Exception as e:
                logger.debug(f"[VerifyFlow] Standalone write failed for {did}: {e}")

    if new_files:
        try:
            zp = Path(cached_zip_path)
            tmp_zip = zp.with_suffix(".tmp.zip")
            with zipfile.ZipFile(zp, "r") as zin:
                with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zout:
                    for item in zin.infolist():
                        fname = item.filename
                        # Match by depot-ID prefix: the zip may hold a GID file
                        # that differs from the GID recorded in local_gids
                        # (e.g. local_gids merged a newer standalone copy), in
                        # which case old_mid matching alone would leave a
                        # duplicate manifest for the same depot behind.
                        if fname.endswith(".manifest") and any(
                            fname.startswith(f"{did}_") for did in new_files
                        ):
                            continue
                        zout.writestr(item, zin.read(fname))
                    for did, (mid, _, m_bytes) in new_files.items():
                        zout.writestr(f"{did}_{mid}.manifest", m_bytes)
            tmp_zip.replace(zp)
            logger.info(f"[VerifyFlow] Rewrote cached bundle {zp.name} with {len(new_files)} refreshed manifest(s)")
        except Exception as e:
            logger.warning(f"[VerifyFlow] Bundle rewrite failed (standalone copies saved): {e}")

    # Remove replaced standalone copies so a stale {did}_{old}.manifest can
    # never shadow the refreshed one in a future merge (glob order is
    # nondeterministic).
    for did, (_, old_mid, _) in new_files.items():
        if not old_mid:
            continue
        for m_dir in manifest_dirs:
            try:
                stale_copy = Path(m_dir) / f"{did}_{old_mid}.manifest"
                if stale_copy.exists():
                    stale_copy.unlink()
            except Exception as e:
                logger.debug(f"[VerifyFlow] Stale standalone cleanup failed for {did}: {e}")

    return updated, failed


def resolve_live_gids(live_depots: Optional[Dict[str, Any]], branch: str) -> Dict[str, str]:
    """Resolve {depot_id: current_manifest_gid} from a live PICS depot map."""
    from utils.depot_utils import get_depot_manifest_gid

    out: Dict[str, str] = {}
    for did, dinfo in (live_depots or {}).items():
        try:
            gid = get_depot_manifest_gid(dinfo, branch=branch or "public")
        except Exception:
            gid = None
        if gid:
            out[str(did)] = str(gid)
    return out
