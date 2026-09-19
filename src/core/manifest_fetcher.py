import logging
import os
import re
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import morrenus_api
from core.steam_api import get_depot_info_from_api
from managers.db_manager import DatabaseManager
from utils.helpers import get_base_path

logger = logging.getLogger(__name__)


def verify_or_download_manifest(
    app_id: str,
    branch: str = "public",
    discovered_branches: Optional[Dict[str, Any]] = None,
) -> Tuple[Tuple[Optional[str], Optional[str]], List[str], Dict[str, Any]]:
    """Checks if cached manifest zip exists and is up to date with Steam API.

    If valid and up to date, returns ((cached_path, None), refetched_depots, missing_depots_info_patch).
    If missing or stale, downloads via morrenus_api and returns ((downloaded_path, error), [], {}).
    """
    app_id_str = str(app_id).strip()
    branch_str = str(branch or "public").strip()
    refetched_depots: List[str] = []
    missing_depots_info_patch: Dict[str, Any] = {}

    try:
        manifests_dir = Path(get_base_path()) / "hubcap_manifests"
        manifests_dir.mkdir(parents=True, exist_ok=True)
        if branch_str and branch_str != "public":
            cached_path = manifests_dir / f"accela_fetch_{app_id_str}_branch_{branch_str}.zip"
        else:
            cached_path = manifests_dir / f"accela_fetch_{app_id_str}.zip"

        is_synthesized_from_lua = False
        cached_lua_path = Path(get_base_path()) / "cached_luas" / f"{app_id_str}.lua"
        if not cached_path.exists() and cached_lua_path.exists():
            try:
                logger.info(
                    f"[ManifestFetcher] Found local cached LUA for AppID {app_id_str} ({cached_lua_path.name}). "
                    "Constructing bundle from local cache to avoid redownloading..."
                )
                from core.tasks.process_zip_task import ProcessZipTask
                lua_content = cached_lua_path.read_text(encoding="utf-8", errors="ignore")
                parsed_gd: Dict[str, Any] = {}
                ProcessZipTask._parse_lua(lua_content, parsed_gd)

                with zipfile.ZipFile(cached_path, "w", zipfile.ZIP_DEFLATED) as zout:
                    zout.write(cached_lua_path, arcname=cached_lua_path.name)
                    # Include any standalone manifests that are already cached locally
                    m_dirs = [
                        Path(get_base_path()) / "manifests",
                        Path(tempfile.gettempdir()) / "mistwalker_manifests",
                    ]
                    for d_id, m_id in (parsed_gd.get("manifests") or {}).items():
                        for md in m_dirs:
                            mf_file = md / f"{d_id}_{m_id}.manifest"
                            if mf_file.exists():
                                zout.write(mf_file, arcname=mf_file.name)
                                break
                is_synthesized_from_lua = True
            except Exception as _synth_err:
                logger.warning(f"[ManifestFetcher] Failed to construct bundle from cached lua: {_synth_err}")
                if cached_path.exists():
                    try:
                        cached_path.unlink(missing_ok=True)
                    except Exception:
                        pass

        if cached_path.exists():
            logger.info(f"[ManifestFetcher] Checking updates for cached manifest {app_id_str} (Branch: {branch_str})")

            # Corrupt / 0-byte file check
            try:
                if cached_path.stat().st_size == 0:
                    logger.warning(f"[ManifestFetcher] Cached zip {cached_path} is 0 bytes. Removing and redownloading.")
                    cached_path.unlink(missing_ok=True)
                    dl_res = morrenus_api.download_manifest(app_id_str, branch=branch_str, force_update=True)
                    return dl_res, refetched_depots, missing_depots_info_patch
            except Exception as sz_err:
                logger.debug(f"[ManifestFetcher] Size check error: {sz_err}")

            # 0. Check Hubcap server freshness (free endpoint, 0 quota).
            #    /status/{app_id} only describes the PUBLIC bundle, so size comparison is only for public.
            #    Skip size comparison if bundle was synthesized from cached LUA or if targeted update is active.
            if not is_synthesized_from_lua:
                try:
                    status_res = morrenus_api.get_manifest_status(app_id_str) if (not branch_str or branch_str == "public") else None
                    if isinstance(status_res, dict) and status_res.get("status") == "available":
                        hubcap_size = status_res.get("file_size")
                        local_size = cached_path.stat().st_size
                        if hubcap_size and isinstance(hubcap_size, int) and hubcap_size > 0:
                            if hubcap_size != local_size:
                                logger.info(
                                    f"[ManifestFetcher] Hubcap manifest bundle size differs (server: {hubcap_size}, local: {local_size}). "
                                    f"Redownloading refreshed bundle for {app_id_str}."
                                )
                                dl_res = morrenus_api.download_manifest(app_id_str, branch=branch_str, force_update=True)
                                return dl_res, refetched_depots, missing_depots_info_patch
                except Exception as status_err:
                    logger.debug(f"[ManifestFetcher] Hubcap status check error (non-fatal): {status_err}")

            # 1. Parse the zip to find manifests inside it and app token
            local_manifests = {}
            app_token = None
            try:
                with zipfile.ZipFile(cached_path, "r") as zip_ref:
                    lua_files = [f for f in zip_ref.namelist() if f.endswith(".lua")]
                    if lua_files:
                        try:
                            lua_content = zip_ref.read(lua_files[0]).decode("utf-8", errors="ignore")
                            token_match = re.search(r'addtoken\s*\(\s*\d+\s*,\s*"([^"]+)"\s*\)', lua_content, re.IGNORECASE)
                            if token_match:
                                app_token = token_match.group(1)
                        except Exception as e:
                            logger.debug(f"[ManifestFetcher] Failed to read LUA from cached zip: {e}")

                    manifest_files = [
                        os.path.basename(f)
                        for f in zip_ref.namelist()
                        if f.endswith(".manifest")
                    ]
                    for filename in manifest_files:
                        parts = filename.replace(".manifest", "").split("_")
                        if len(parts) == 2:
                            local_manifests[parts[0]] = parts[1]
            except Exception as e:
                logger.warning(f"[ManifestFetcher] Failed to parse cached zip {cached_path}: {e}. Removing bad zip and redownloading.")
                try:
                    cached_path.unlink(missing_ok=True)
                except Exception:
                    pass
                dl_res = morrenus_api.download_manifest(app_id_str, branch=branch_str, force_update=True)
                return dl_res, refetched_depots, missing_depots_info_patch

            # Scan standalone manifests outside the zip
            try:
                from managers.depot_key_manager import DepotKeyManager
                known_app_depots = set(local_manifests.keys())
                try:
                    known_app_depots.update(DepotKeyManager().get_depot_keys(app_id_str).keys())
                except Exception:
                    pass

                standalone_dirs = [
                    Path(tempfile.gettempdir()) / "mistwalker_manifests",
                    Path(get_base_path()) / "manifests",
                ]
                for s_dir in standalone_dirs:
                    if s_dir.exists():
                        for mf_file in s_dir.glob("*.manifest"):
                            parts = mf_file.stem.split("_")
                            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                                did, mid = parts[0], parts[1]
                                if did in known_app_depots:
                                    local_manifests[did] = mid
            except Exception as e:
                logger.debug(f"[ManifestFetcher] Error scanning standalone manifests: {e}")

            if not local_manifests:
                logger.warning(f"[ManifestFetcher] No manifests found in cached zip {cached_path}. Removing bad zip and redownloading.")
                try:
                    cached_path.unlink(missing_ok=True)
                except Exception:
                    pass
                dl_res = morrenus_api.download_manifest(app_id_str, branch=branch_str, force_update=True)
                return dl_res, refetched_depots, missing_depots_info_patch

            # 2. Query Steam API / DB cache for current depot info with live PICS comparison
            try:
                from managers.db_manager import DatabaseManager
                from utils.settings import get_settings
                settings = get_settings()
                db = DatabaseManager()

                # Check live Steam PICS branch buildid if available
                live_buildid = None
                if discovered_branches and isinstance(discovered_branches, dict):
                    b_data = discovered_branches.get(branch_str) or discovered_branches.get("public")
                    if isinstance(b_data, dict):
                        live_buildid = str(b_data.get("buildid") or "").strip()

                cached_buildid = settings.value(f"fetched_buildid/{app_id_str}", "", type=str)
                cached_app_data = db.get_app_info(app_id_str, bypass_expiration=True) or {}
                if not cached_buildid and cached_app_data:
                    cached_buildid = str(cached_app_data.get("buildid") or "").strip()

                cache_time = db.get_cache_time(app_id_str) or 0
                time_since_cache = time.time() - cache_time if cache_time else 999999

                is_stale_build = bool(live_buildid and cached_buildid and live_buildid != cached_buildid)
                force_steam_refresh = is_stale_build or (time_since_cache > 14400)

                if force_steam_refresh:
                    if is_stale_build:
                        logger.info(
                            f"[ManifestFetcher] Live Steam PICS build ({live_buildid}) differs from cached build ({cached_buildid}). "
                            f"Force-refreshing Steam depot data for AppID {app_id_str}..."
                        )
                    else:
                        logger.info(f"[ManifestFetcher] Cache expired ({int(time_since_cache)}s ago). Querying fresh Steam API for AppID {app_id_str}...")
                    from core.steam_api import get_depot_info_from_api
                    steam_client_data = get_depot_info_from_api(app_id_str, app_token, force_refresh=True)
                else:
                    logger.info(f"[ManifestFetcher] Using DB cache for AppID {app_id_str} (cached {int(time_since_cache)}s ago, build={cached_buildid or 'unknown'})")
                    steam_client_data = cached_app_data or db.get_app_info(app_id_str, bypass_expiration=True)

                if steam_client_data:
                    cached_depots = steam_client_data.get("depots", {})
                    needs_refresh = False
                    if local_manifests and any(str(ldid) not in cached_depots for ldid in local_manifests):
                        needs_refresh = True
                    elif steam_client_data.get("hasdepotsindlc") and not steam_client_data.get("dlcs_expanded"):
                        needs_refresh = True
                    if needs_refresh:
                        logger.info(f"[ManifestFetcher] Cache for AppID {app_id_str} missing depots or unexpanded DLCs. Refreshing...")
                        from core.steam_api import get_depot_info_from_api
                        fresh = get_depot_info_from_api(app_id_str, app_token, force_refresh=True)
                        if fresh and fresh.get("depots"):
                            steam_client_data = fresh

                api_depots = steam_client_data.get("depots", {}) if steam_client_data else {}
            except BaseException as e:
                logger.error(f"[ManifestFetcher] Failed to fetch depot info for {app_id_str}: {e}")
                api_depots = {}

            if not api_depots:
                logger.info(f"[ManifestFetcher] Could not check Steam API for updates. Reusing cached manifest for {app_id_str}.")
                return (str(cached_path), None), refetched_depots, missing_depots_info_patch

            # 3. Consolidated Depot Check via depot_utils
            from utils.depot_utils import check_hubcap_vs_steam_depots
            depot_check = check_hubcap_vs_steam_depots(
                local_manifests,
                api_depots,
                app_id=app_id_str,
                branch=branch_str,
            )

            if not depot_check.get("is_up_to_date", True):
                stale_depots = depot_check.get("stale_depots", [])
                logger.info(
                    f"[ManifestFetcher] Cached manifest for {app_id_str} has {len(stale_depots)} stale depot(s): {stale_depots}. "
                    "Attempting targeted single-manifest update..."
                )

                # Targeted single-manifest update (saves full zip quota)
                all_stale_ok = True
                updated_manifests = {}
                for s_depot in stale_depots:
                    s_did = str(s_depot["depot_id"])
                    s_mid = str(s_depot["current_manifest_id"])
                    s_old_mid = str(s_depot.get("cached_manifest_id") or "")
                    s_name = s_depot.get("name") or f"Depot {s_did}"
                    logger.info(f"[ManifestFetcher] Fetching updated manifest for depot {s_did} (GID {s_mid}) via single manifest API...")
                    raw_bytes, s_err = morrenus_api.generate_single_manifest(s_did, s_mid)
                    if raw_bytes and not s_err:
                        updated_manifests[s_did] = (s_mid, s_old_mid, raw_bytes)
                    else:
                        logger.warning(
                            f"[ManifestFetcher] Targeted update failed for depot {s_did} ({s_name}): {s_err}. "
                            "Falling back to full zip download."
                        )
                        all_stale_ok = False
                        break

                if all_stale_ok and updated_manifests:
                    for s_did, (s_mid, s_old_mid, m_bytes) in updated_manifests.items():
                        for s_dir in [Path(tempfile.gettempdir()) / "mistwalker_manifests", Path(get_base_path()) / "manifests"]:
                            try:
                                s_dir.mkdir(parents=True, exist_ok=True)
                                (s_dir / f"{s_did}_{s_mid}.manifest").write_bytes(m_bytes)
                            except Exception:
                                pass
                        local_manifests[s_did] = s_mid
                        refetched_depots.append(s_did)

                    # Update cached zip with new manifests and remove old stale ones
                    try:
                        temp_zip_path = cached_path.with_suffix(".tmp.zip")
                        with zipfile.ZipFile(cached_path, "r") as zin:
                            with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED) as zout:
                                for item in zin.infolist():
                                    fname = item.filename
                                    is_old_stale = False
                                    for s_did, (_, s_old_mid, _) in updated_manifests.items():
                                        if s_old_mid and fname.endswith(f"{s_did}_{s_old_mid}.manifest"):
                                            is_old_stale = True
                                            break
                                    if not is_old_stale:
                                        zout.writestr(item, zin.read(fname))
                                for s_did, (s_mid, _, m_bytes) in updated_manifests.items():
                                    zout.writestr(f"{s_did}_{s_mid}.manifest", m_bytes)
                        temp_zip_path.replace(cached_path)
                        logger.info(f"[ManifestFetcher] Successfully updated cached zip {cached_path.name} with {len(updated_manifests)} new manifest(s).")
                    except Exception as z_err:
                        logger.warning(f"[ManifestFetcher] Could not rewrite cached zip (non-fatal, manifests saved to disk): {z_err}")

                    if live_buildid:
                        settings.setValue(f"fetched_buildid/{app_id_str}", live_buildid)
                else:
                    logger.info(f"[ManifestFetcher] Falling back to classic full zip download for {app_id_str}...")
                    dl_res = morrenus_api.download_manifest(app_id_str, branch=branch_str, force_update=True)
                    return dl_res, refetched_depots, missing_depots_info_patch

            # 4. Check Hubcap /contents once to safely identify recoverable missing depots
            try:
                from managers.db_manager import DatabaseManager
                _db = DatabaseManager()
                tracked_missing = _db.get_missing_hubcap_depots(app_id_str)
            except Exception:
                tracked_missing = []

            hubcap_contents_checked = False
            hubcap_depot_ids = set()

            if tracked_missing or depot_check.get("missing_for_fetch"):
                try:
                    contents_data = morrenus_api.get_manifest_contents(app_id_str, branch=branch_str)
                    if isinstance(contents_data, dict) and "error" not in contents_data:
                        hubcap_depot_ids = contents_data.get("depot_ids", set())
                        hubcap_contents_checked = True
                except Exception as _ce:
                    logger.warning(f"[ManifestFetcher] /contents check failed for {app_id_str}: {_ce}")

            if tracked_missing:
                logger.info(
                    f"[ManifestFetcher] {len(tracked_missing)} previously-tracked missing depot(s) for App {app_id_str}."
                )
                for tracked in tracked_missing:
                    t_did = tracked["depot_id"]
                    t_mid = tracked["manifest_id"]
                    t_name = tracked.get("depot_name") or f"Depot {t_did}"

                    if hubcap_contents_checked and t_did in hubcap_depot_ids:
                        logger.info(f"[ManifestFetcher] Depot {t_did} is in Hubcap bundle! Fetching via generate...")
                        manifest_bytes, gen_err = morrenus_api.generate_single_manifest(t_did, t_mid)
                        if manifest_bytes:
                            for s_dir in [Path(tempfile.gettempdir()) / "mistwalker_manifests", Path(get_base_path()) / "manifests"]:
                                try:
                                    s_dir.mkdir(parents=True, exist_ok=True)
                                    (s_dir / f"{t_did}_{t_mid}.manifest").write_bytes(manifest_bytes)
                                except Exception:
                                    pass

                            local_manifests[str(t_did)] = str(t_mid)
                            refetched_depots.append(str(t_did))
                            try:
                                _db.clear_missing_hubcap_depot(app_id_str, t_did)
                            except Exception:
                                pass
                            logger.info(f"[ManifestFetcher] ✓ Recovered depot {t_did} ({t_name}) and cleared from tracking.")
                        else:
                            missing_depots_info_patch[str(t_did)] = {"hubcap_status": "failed"}
                    else:
                        missing_depots_info_patch[str(t_did)] = {"hubcap_status": "not_found"}

            # Newly detected missing depots: check /contents before wasting calls on 404s
            newly_missing_to_persist = []
            if depot_check.get("missing_for_fetch"):
                already_tracked_ids = {t["depot_id"] for t in tracked_missing}
                missing_for_fetch = [
                    entry for entry in depot_check["missing_for_fetch"]
                    if entry[0] not in already_tracked_ids
                ]

                for missing_did, missing_mid, depot_name in missing_for_fetch:
                    if hubcap_contents_checked and str(missing_did) in hubcap_depot_ids:
                        logger.info(f"[ManifestFetcher] Depot {missing_did} ({depot_name}) exists on Hubcap. Auto-fetching...")
                        manifest_bytes, gen_err = morrenus_api.generate_single_manifest(missing_did, missing_mid)
                        if manifest_bytes:
                            for s_dir in [Path(tempfile.gettempdir()) / "mistwalker_manifests", Path(get_base_path()) / "manifests"]:
                                try:
                                    s_dir.mkdir(parents=True, exist_ok=True)
                                    (s_dir / f"{missing_did}_{missing_mid}.manifest").write_bytes(manifest_bytes)
                                except Exception:
                                    pass
                            local_manifests[str(missing_did)] = str(missing_mid)
                            refetched_depots.append(str(missing_did))
                            continue

                    status_tag = "not_found"
                    missing_depots_info_patch[str(missing_did)] = {"hubcap_status": status_tag}
                    newly_missing_to_persist.append({
                        "depot_id": str(missing_did),
                        "manifest_id": str(missing_mid),
                        "depot_name": depot_name,
                    })

            if newly_missing_to_persist:
                try:
                    from managers.db_manager import DatabaseManager
                    DatabaseManager().upsert_missing_hubcap_depots(app_id_str, newly_missing_to_persist)
                except Exception as _dbe:
                    logger.debug(f"[ManifestFetcher] Failed to persist missing depots to DB: {_dbe}")

            logger.info(f"[ManifestFetcher] Cached manifest for {app_id_str} is up-to-date. Using cache.")
            return (str(cached_path), None), refetched_depots, missing_depots_info_patch
        else:
            logger.info(f"[ManifestFetcher] No cached manifest found for {app_id_str}. Downloading.")

    except BaseException as e:
        logger.error(f"[ManifestFetcher] Error checking manifest cache for {app_id_str}: {e}", exc_info=True)

    dl_res = morrenus_api.download_manifest(app_id_str, branch=branch_str)
    return dl_res, refetched_depots, missing_depots_info_patch
