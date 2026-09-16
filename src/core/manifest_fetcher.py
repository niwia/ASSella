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
        if branch_str and branch_str != "public":
            cached_path = manifests_dir / f"accela_fetch_{app_id_str}_branch_{branch_str}.zip"
        else:
            cached_path = manifests_dir / f"accela_fetch_{app_id_str}.zip"

        if cached_path.exists():
            logger.info(f"[ManifestFetcher] Checking updates for cached manifest {app_id_str} (Branch: {branch_str})")

            # 0. Check Hubcap server freshness (free endpoint, 0 quota).
            #    /status/{app_id} only describes the PUBLIC bundle, so size comparison is only for public.
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
                            dl_res = morrenus_api.download_manifest(app_id_str, branch=branch_str)
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
                logger.warning(f"[ManifestFetcher] Failed to parse cached zip {cached_path}: {e}. Will redownload.")
                dl_res = morrenus_api.download_manifest(app_id_str, branch=branch_str)
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
                logger.warning(f"[ManifestFetcher] No manifests found in cached zip {cached_path}. Will redownload.")
                dl_res = morrenus_api.download_manifest(app_id_str, branch=branch_str)
                return dl_res, refetched_depots, missing_depots_info_patch

            # 2. Query Steam API / DB cache for current depot info
            try:
                from managers.db_manager import DatabaseManager
                db = DatabaseManager()
                cache_time = db.get_cache_time(app_id_str) or 0

                if cache_time and (time.time() - cache_time) < 43200:
                    logger.info(f"[ManifestFetcher] Using fresh DB cache for AppID {app_id_str} (cached {int(time.time() - cache_time)}s ago)")
                    steam_client_data = db.get_app_info(app_id_str, bypass_expiration=True)
                else:
                    logger.info(f"[ManifestFetcher] Querying Steam API for fresh AppID {app_id_str} info...")
                    from core.steam_api import get_depot_info_from_api
                    steam_client_data = get_depot_info_from_api(app_id_str, app_token)

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
                logger.info(
                    f"[ManifestFetcher] Cached manifest for {app_id_str} has stale depots: {depot_check.get('stale_depots')}. Redownloading."
                )
                dl_res = morrenus_api.download_manifest(app_id_str, branch=branch_str)
                return dl_res, refetched_depots, missing_depots_info_patch

            # 4. Bidirectional Auto-Fetch for official depots missing from cache
            try:
                from managers.db_manager import DatabaseManager
                _db = DatabaseManager()
                tracked_missing = _db.get_missing_hubcap_depots(app_id_str)
            except Exception:
                tracked_missing = []

            if tracked_missing:
                logger.info(
                    f"[ManifestFetcher] {len(tracked_missing)} previously-tracked missing depot(s) for App {app_id_str}. "
                    f"Checking /contents to see if any have returned..."
                )
                try:
                    contents_data = morrenus_api.get_manifest_contents(app_id_str, branch=branch_str)
                    hubcap_depot_ids = contents_data.get("depot_ids", set()) if isinstance(contents_data, dict) and "error" not in contents_data else set()
                except Exception as _ce:
                    logger.warning(f"[ManifestFetcher] /contents check failed for {app_id_str}: {_ce}")
                    hubcap_depot_ids = set()

                for tracked in tracked_missing:
                    t_did = tracked["depot_id"]
                    t_mid = tracked["manifest_id"]
                    t_name = tracked.get("depot_name") or f"Depot {t_did}"

                    if t_did in hubcap_depot_ids:
                        logger.info(f"[ManifestFetcher] Depot {t_did} is back in Hubcap bundle! Fetching via generate...")
                        manifest_bytes, gen_err = morrenus_api.generate_single_manifest(t_did, t_mid)
                        if manifest_bytes:
                            tmp_manifest_dir = Path(tempfile.gettempdir()) / "mistwalker_manifests"
                            tmp_manifest_dir.mkdir(parents=True, exist_ok=True)
                            (tmp_manifest_dir / f"{t_did}_{t_mid}.manifest").write_bytes(manifest_bytes)

                            persistent_manifest_dir = Path(get_base_path()) / "manifests"
                            persistent_manifest_dir.mkdir(parents=True, exist_ok=True)
                            (persistent_manifest_dir / f"{t_did}_{t_mid}.manifest").write_bytes(manifest_bytes)

                            local_manifests[str(t_did)] = str(t_mid)
                            refetched_depots.append(str(t_did))

                            try:
                                _db.clear_missing_hubcap_depot(app_id_str, t_did)
                            except Exception:
                                pass
                            logger.info(f"[ManifestFetcher] ✓ Recovered depot {t_did} ({t_name}) and cleared from tracking.")
                        else:
                            missing_depots_info_patch[str(t_did)] = {"hubcap_status": "failed"}
                            logger.warning(f"[ManifestFetcher] /contents says depot {t_did} exists but generate failed: {gen_err}")
                    else:
                        missing_depots_info_patch[str(t_did)] = {"hubcap_status": "not_found"}
                        logger.debug(f"[ManifestFetcher] Depot {t_did} still absent from Hubcap bundle. Skipping generate.")

            # Newly detected missing depots
            newly_missing_to_persist = []
            if depot_check.get("missing_for_fetch"):
                already_tracked_ids = {t["depot_id"] for t in tracked_missing}
                missing_for_fetch = [
                    entry for entry in depot_check["missing_for_fetch"]
                    if entry[0] not in already_tracked_ids
                ]
                if missing_for_fetch:
                    logger.info(
                        f"[ManifestFetcher] Detected {len(missing_for_fetch)} NEW official depot(s) missing from cache for {app_id_str}: {missing_for_fetch}"
                    )

                for missing_did, missing_mid, depot_name in missing_for_fetch:
                    logger.info(f"[ManifestFetcher] Auto-fetching missing depot {missing_did} ({depot_name}) via single manifest API...")
                    manifest_bytes, gen_err = morrenus_api.generate_single_manifest(missing_did, missing_mid)
                    if manifest_bytes:
                        tmp_manifest_dir = Path(tempfile.gettempdir()) / "mistwalker_manifests"
                        tmp_manifest_dir.mkdir(parents=True, exist_ok=True)
                        (tmp_manifest_dir / f"{missing_did}_{missing_mid}.manifest").write_bytes(manifest_bytes)

                        persistent_manifest_dir = Path(get_base_path()) / "manifests"
                        persistent_manifest_dir.mkdir(parents=True, exist_ok=True)
                        (persistent_manifest_dir / f"{missing_did}_{missing_mid}.manifest").write_bytes(manifest_bytes)

                        local_manifests[str(missing_did)] = str(missing_mid)
                        refetched_depots.append(str(missing_did))
                        logger.info(f"[ManifestFetcher] Successfully fetched and saved missing manifest {missing_did}_{missing_mid}.manifest")
                    else:
                        is_404 = bool(
                            gen_err
                            and ("404" in str(gen_err) or "not found" in str(gen_err).lower() or "unavailable" in str(gen_err).lower())
                        )
                        status_tag = "not_found" if is_404 else "failed"
                        missing_depots_info_patch[str(missing_did)] = {"hubcap_status": status_tag}
                        logger.warning(
                            f"[ManifestFetcher] Could not auto-generate missing manifest for depot {missing_did}: {gen_err} (status={status_tag})"
                        )
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
