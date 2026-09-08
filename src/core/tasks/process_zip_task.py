import zipfile
import re
import os
import sys
import logging
import tempfile

from ui.assets import DEPOT_BLACKLIST
from core.steam_api import get_depot_info_from_api
from core.ini_parser import parse_depots_ini
from utils.helpers import get_base_path
from utils.yaml_config_manager import (
    get_user_config_path,
    add_app_token,
    is_slssteam_mode_enabled,
)
from core.appinfo_editor import get_appinfo_path, add_token_to_appinfo
from utils.settings import get_settings

try:
    from managers.depot_key_manager import DepotKeyManager
except Exception:
    DepotKeyManager = None

logger = logging.getLogger(__name__)


class ProcessZipTask:
    @staticmethod
    def _parse_lua(content, game_data):
        logger.debug("Starting LUA content parsing...")
        game_data.setdefault("manifest_sizes", {})

        try:
            all_app_matches = list(
                re.finditer(r"addappid\((.*?)\)(.*)", content, re.IGNORECASE)
            )
            if not all_app_matches:
                raise ValueError("LUA file is invalid; no 'addappid' entries found.")

            first_app_match = all_app_matches.pop(0)
            first_app_args = first_app_match.group(1).strip()

            # Explicitly break down operation to help static analysis
            args_list = first_app_args.split(",")
            app_id_val = args_list[0]
            game_data["appid"] = app_id_val.strip()

            comment_part = first_app_match.group(2)
            game_name_match = re.search(r"--\s*(.*)", comment_part)
            game_data["game_name"] = (
                game_name_match.group(1).strip() if game_name_match else None
            )

            game_data["depots"] = {}
            game_data["dlcs"] = {}
            for match in all_app_matches:
                args_str = match.group(1).strip()
                args = [arg.strip() for arg in args_str.split(",")]
                app_id = args[0]

                comment_part = match.group(2)
                desc_match = re.search(r"--\s*(.*)", comment_part)
                desc = desc_match.group(1).strip() if desc_match else f"Depot {app_id}"

                if len(args) > 2 and args[2].strip('"'):
                    depot_key = args[2].strip('"')
                    game_data["depots"][app_id] = {"key": depot_key, "desc": desc}
                else:
                    game_data["dlcs"][app_id] = desc

            manifest_size_matches = list(
                re.finditer(
                    r'setManifestid\(\s*(\d+)\s*,\s*".*?"\s*,\s*(\d+)\s*\)',
                    content,
                    re.IGNORECASE,
                )
            )
            for match in manifest_size_matches:
                depot_id = match.group(1).strip()
                size_bytes = match.group(2).strip()
                game_data["manifest_sizes"][depot_id] = size_bytes
                logger.debug(
                    f"Found LUA manifest size for Depot {depot_id}: {size_bytes} bytes"
                )

            # Parse manifest GIDs from LUA file (for branch/LUA-only matching)
            game_data.setdefault("manifests", {})
            manifest_gid_matches = list(
                re.finditer(
                    r'setManifestid\(\s*(\d+)\s*,\s*"([^"]+)"',
                    content,
                    re.IGNORECASE,
                )
            )
            for match in manifest_gid_matches:
                depot_id = match.group(1).strip()
                gid = match.group(2).strip()
                game_data["manifests"][depot_id] = gid
                logger.debug(
                    f"Found LUA manifest GID for Depot {depot_id}: {gid}"
                )

        except Exception as e:
            logger.error(f"Critical error during LUA parsing: {e}", exc_info=True)
            raise

    @staticmethod
    def _extract_app_token(lua_content: str, app_id: str) -> str | None:
        if not app_id:
            logger.debug("No app_id provided, skipping token extraction")
            return None

        try:
            # Extract token from LUA content
            # Pattern: addtoken(<app_id>, "<token>") with optional whitespace
            token_pattern = r'addtoken\s*\(\s*\d+\s*,\s*"([^"]+)"\s*\)'
            match = re.search(token_pattern, lua_content, re.IGNORECASE)

            if not match:
                logger.debug(f"No addtoken pattern found for AppID {app_id}")
                return None

            app_token = match.group(1)
            logger.info(f"Found token for AppID {app_id}: {app_token[:10]}...")

            if is_slssteam_mode_enabled():
                if sys.platform == "win32":
                    # Windows: Add token to Steam's appinfo.vdf
                    appinfo_path = get_appinfo_path()

                    if not appinfo_path:
                        logger.warning(
                            "Could not find Steam appinfo.vdf, skipping token addition"
                        )
                        return app_token

                    success = add_token_to_appinfo(appinfo_path, app_id, app_token)

                    if success:
                        logger.info(
                            f"Successfully added token for AppID {app_id} to Steam appinfo.vdf"
                        )

                    return app_token
                else:
                    # Linux: Add token to SLSsteam config.yaml
                    config_path = get_user_config_path()

                    if not config_path.exists():
                        logger.warning(f"SLSsteam config not found at {config_path}")
                        return app_token

                    success = add_app_token(config_path, app_id, app_token)

                    if success:
                        logger.info(
                            f"Successfully added token for AppID {app_id} to SLSsteam config"
                        )

                    return app_token
            return app_token

        except Exception as e:
            logger.error(f"Failed to extract/configure app token: {e}", exc_info=True)
            return None

    def run(self, zip_path):
        logger.info(f"Starting zip processing task for: {zip_path}")

        try:
            known_depot_descriptions = parse_depots_ini()
            logger.info(
                f"Successfully loaded {len(known_depot_descriptions)} depot descriptions from .ini."
            )
        except Exception as e:
            logger.error(f"Failed to load depots.ini: {e}", exc_info=True)
            known_depot_descriptions = {}

        game_data = {}
        fn = os.path.basename(zip_path)
        b_match = re.search(r"_branch_([a-zA-Z0-9_-]+)\.zip$", fn)
        if b_match:
            game_data["branch"] = b_match.group(1)
            logger.info(f"[ProcessZipTask] Extracted branch '{game_data['branch']}' from zip filename {fn}")
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                lua_files = [f for f in zip_ref.namelist() if f.endswith(".lua")]
                lua_content = None
                lua_timestamp = None
                if lua_files:
                    try:
                        lua_info = zip_ref.getinfo(lua_files[0])
                        dt = lua_info.date_time
                        import datetime, time
                        lua_timestamp = int(time.mktime(datetime.datetime(*dt).timetuple()))
                    except Exception:
                        pass
                    lua_content = zip_ref.read(lua_files[0]).decode("utf-8")
                    if lua_content:
                        ProcessZipTask._parse_lua(lua_content, game_data)
                        token = ProcessZipTask._extract_app_token(lua_content, game_data.get("appid"))
                        if token:
                            game_data["app_token"] = token

                        try:
                            _extracted_appid = game_data.get("appid")
                            if not _extracted_appid and lua_files and lua_files[0].endswith(".lua"):
                                _extracted_appid = lua_files[0].replace(".lua", "").replace("accela_", "")
                            if _extracted_appid:
                                lua_dir = get_base_path() / "cached_luas"
                                lua_dir.mkdir(parents=True, exist_ok=True)
                                lua_save_file = lua_dir / f"{_extracted_appid}.lua"
                                lua_save_file.write_text(lua_content, encoding="utf-8")
                                logger.info(f"[ProcessZipTask] Archived LUA file to {lua_save_file.name}")
                        except Exception as _lua_arch_err:
                            logger.debug(f"Failed to archive LUA backup: {_lua_arch_err}")

                manifest_files = {
                    os.path.basename(f): zip_ref.read(f)
                    for f in zip_ref.namelist()
                    if f.endswith(".manifest")
                }
                for depot_id_manifest in manifest_files:
                    parts = depot_id_manifest.replace(".manifest", "").split("_")
                    if len(parts) == 2:
                        game_data.setdefault("manifests", {})[parts[0]] = parts[1]

                # ── Persist depot keys + AppToken to depot_keys.db ──
                # This enables Smart Update Mode for this game from here on.
                _appid_for_cache = game_data.get("appid")
                if DepotKeyManager and _appid_for_cache and lua_content:
                    try:
                        _dkm = DepotKeyManager()
                        raw_depots = game_data.get("depots", {})
                        _keys_to_save = {
                            did: info.get("key")
                            for did, info in raw_depots.items()
                            if info.get("key")
                        }
                        if _keys_to_save:
                            _dkm.save_depot_keys(_appid_for_cache, _keys_to_save, timestamp=lua_timestamp)
                            logger.info(
                                f"[DepotKeyCache] Persisted {len(_keys_to_save)} depot key(s) "
                                f"for AppID {_appid_for_cache} to depot_keys.db"
                            )
                        if token:
                            _dkm.save_app_token(_appid_for_cache, token, timestamp=lua_timestamp)
                            logger.info(
                                f"[DepotKeyCache] Persisted AppToken for AppID {_appid_for_cache} "
                                f"to depot_keys.db"
                            )
                    except Exception as _dkm_err:
                        logger.warning(
                            f"[DepotKeyCache] Failed to persist keys for AppID "
                            f"{_appid_for_cache}: {_dkm_err}"
                        )

                if game_data.get("dlcs"):
                    enriched_dlcs = {}
                    for dlc_id, lua_desc in game_data["dlcs"].items():
                        enriched_dlcs[dlc_id] = known_depot_descriptions.get(
                            dlc_id, lua_desc
                        )
                    game_data["dlcs"] = enriched_dlcs

                if not lua_content:
                    fn = os.path.basename(zip_path)
                    match = re.search(r"accela_fetch_(\d+)", fn)
                    if match:
                        inferred_appid = match.group(1)
                        game_data["appid"] = inferred_appid
                        logger.info(f"[ProcessZipTask] Inferred AppID {inferred_appid} from zip filename {fn}")
                        if DepotKeyManager:
                            try:
                                _dkm = DepotKeyManager()
                                cached_keys = _dkm.get_depot_keys(inferred_appid)
                                cached_token = _dkm.get_app_token(inferred_appid)
                                if cached_token:
                                    game_data["app_token"] = cached_token
                                if cached_keys:
                                    depots_map = {}
                                    for did, k in cached_keys.items():
                                        if str(did) == str(inferred_appid):
                                            continue
                                        desc = known_depot_descriptions.get(did, f"Depot {did}")
                                        depots_map[did] = {"key": k, "desc": desc, "system": None}
                                    game_data["depots"] = depots_map
                                    logger.info(f"[ProcessZipTask] Reconstructed {len(depots_map)} depot(s) from depot_keys.db for AppID {inferred_appid}")
                            except Exception as _recon_err:
                                logger.warning(f"[ProcessZipTask] Failed to reconstruct depots from cache: {_recon_err}")

                unfiltered_depots = game_data.get("depots", {})
                if not unfiltered_depots:
                    logger.warning("LUA parsing did not identify any depots with keys.")
                else:
                    logger.info(
                        f"LUA parsing found {len(unfiltered_depots)} depots before filtering."
                    )

                    string_blacklist = {str(item) for item in DEPOT_BLACKLIST}
                    filtered_depots = {
                        depot_id: data
                        for depot_id, data in unfiltered_depots.items()
                        if depot_id not in string_blacklist
                    }
                    if len(unfiltered_depots) > len(filtered_depots):
                        logger.info(
                            f"Removed {len(unfiltered_depots) - len(filtered_depots)} depots based on blacklist."
                        )

                    game_data["depots"] = filtered_depots

                    if not filtered_depots:
                        logger.warning(
                            "All depots were filtered out. No depots to download."
                        )
                    else:
                        api_data = (
                            get_depot_info_from_api(
                                game_data["appid"], game_data.get("app_token")
                            )
                            if game_data.get("appid")
                            else {}
                        )

                        if api_data.get("installdir"):
                            game_data["installdir"] = api_data["installdir"]
                            logger.info(
                                f"Found official install directory: {game_data['installdir']}"
                            )

                        # Check if SteamDB builds cache has a match for the zip manifests
                        zip_manifests = game_data.get("manifests", {})
                        steamdb_matched_bid = None
                        if zip_manifests:
                            try:
                                from core.steamdb_scraper import SteamDBBuildsCache
                                aid_int = int(game_data["appid"]) if str(game_data.get("appid")).isdigit() else 0
                                if aid_int > 0:
                                    cached_builds = SteamDBBuildsCache().get_builds(aid_int) or []
                                    for cb in cached_builds:
                                        c_depots = cb.get("depots", {})
                                        if any(str(d_id) in c_depots and c_depots[str(d_id)].get("manifest_id") == str(m_id)
                                               for d_id, m_id in zip_manifests.items()):
                                            steamdb_matched_bid = str(cb.get("buildid", ""))
                                            logger.info(f"[ProcessZipTask] SteamDB matched manifest IDs to historical Build ID: {steamdb_matched_bid}")
                                            break
                            except Exception as _sdb_err:
                                logger.debug(f"SteamDB build matching failed in ProcessZipTask: {_sdb_err}")

                        if steamdb_matched_bid:
                            game_data["buildid"] = steamdb_matched_bid
                            if api_data.get("buildid") and str(api_data["buildid"]).isdigit() and steamdb_matched_bid.isdigit():
                                if int(steamdb_matched_bid) < int(api_data["buildid"]):
                                    game_data["_is_rollback"] = True
                        elif api_data.get("buildid"):
                            game_data["buildid"] = api_data["buildid"]

                        if api_data.get("buildid"):
                            get_settings().setValue(f"fetched_buildid/{game_data['appid']}", api_data["buildid"])
                            logger.info(
                                f"Official live buildid: {api_data['buildid']} (Assigned package buildid: {game_data.get('buildid')})"
                            )
                            # Smart branch detection:
                            # 1. First, try to match by manifest GIDs (most robust, works regardless of zip name)
                            matched_branch = None
                            zip_manifests = game_data.get("manifests", {})
                            api_depots = api_data.get("depots", {})
                            if zip_manifests and api_depots:
                                try:
                                    # Find all branches mentioned in live depot manifests
                                    candidate_branches = set()
                                    for depot_id, depot_info in api_depots.items():
                                        if isinstance(depot_info, dict):
                                            manifests = depot_info.get("manifests", {})
                                            if isinstance(manifests, dict):
                                                candidate_branches.update(manifests.keys())

                                    branch_scores = {}
                                    for branch in candidate_branches:
                                        matches = 0
                                        total_checked = 0
                                        for depot_id, zip_gid in zip_manifests.items():
                                            depot_info = api_depots.get(str(depot_id)) or api_depots.get(int(depot_id))
                                            if isinstance(depot_info, dict):
                                                manifests = depot_info.get("manifests", {})
                                                if isinstance(manifests, dict) and branch in manifests:
                                                    branch_entry = manifests[branch]
                                                    expected_gid = ""
                                                    if isinstance(branch_entry, dict):
                                                        expected_gid = str(branch_entry.get("gid", ""))
                                                    elif isinstance(branch_entry, (str, int)):
                                                        expected_gid = str(branch_entry)
                                                    
                                                    if expected_gid and expected_gid == str(zip_gid):
                                                        matches += 1
                                                    total_checked += 1
                                        if total_checked > 0:
                                            branch_scores[branch] = (matches, total_checked)

                                    best_branch = None
                                    best_score = -1
                                    best_match_ratio = 0.0
                                    for branch, (matches, total) in branch_scores.items():
                                        ratio = matches / total if total > 0 else 0.0
                                        # Prefer higher matches; tie-break on ratio
                                        if matches > best_score or (matches == best_score and ratio > best_match_ratio):
                                            best_branch = branch
                                            best_score = matches
                                            best_match_ratio = ratio

                                    if best_branch and best_score > 0:
                                        matched_branch = best_branch
                                        logger.info(
                                            f"[ProcessZipTask] Smart Branch Match: matched '{matched_branch}' via manifest GIDs "
                                            f"({best_score}/{len(zip_manifests)} depots match)."
                                        )
                                except Exception as _smart_err:
                                    logger.debug(f"Smart branch manifest matching failed: {_smart_err}")

                            if matched_branch:
                                game_data["branch"] = matched_branch
                            elif not game_data.get("branch"):
                                # 2. Fallback to Steam PICS buildid matching
                                try:
                                    from core.steam_api import find_branch_for_buildid
                                    pics_branch = find_branch_for_buildid(game_data["appid"], game_data["buildid"])
                                    if pics_branch:
                                        game_data["branch"] = pics_branch
                                        logger.info(f"[ProcessZipTask] Steam PICS matched buildid {game_data['buildid']} -> branch '{pics_branch}'")
                                except Exception as _pb_err:
                                    logger.debug(f"PICS branch match failed: {_pb_err}")

                            # If still no branch resolved, fall back to what the user currently has selected in the UI, else "public"
                            if not game_data.get("branch"):
                                try:
                                    sel_b = get_settings().value(f"selected_branch/{game_data['appid']}", "public", type=str)
                                    game_data["branch"] = sel_b
                                    logger.info(f"[ProcessZipTask] Branch fallback to currently selected UI branch: '{sel_b}'")
                                except Exception:
                                    game_data["branch"] = "public"

                        if api_data.get("header_url"):
                            game_data["header_url"] = api_data["header_url"]
                        if not game_data.get("game_name") and api_data.get("name"):
                            game_data["game_name"] = api_data["name"]
                            logger.info(
                                f"Resolved game name from Steam API: {game_data['game_name']}"
                            )

                        api_details = api_data.get("depots", {})
                        logger.debug(
                            f"Received API details for processing: {api_details}"
                        )

                        if not api_details:
                            logger.warning(
                                "Could not retrieve supplementary details from Steam API."
                            )
                        else:
                            missing_from_hubcap = [
                                str(did) for did in api_details.keys()
                                if str(did).isdigit() and str(did) not in filtered_depots and str(did) not in string_blacklist and str(did) != str(game_data.get("appid"))
                            ]
                            if missing_from_hubcap:
                                logger.warning(
                                    f"[DepotCheck {game_data.get('appid')}] Hubcap manifest is missing "
                                    f"{len(missing_from_hubcap)} official depot(s) listed on Steam: {missing_from_hubcap}"
                                )
                                game_data["missing_depots_from_hubcap"] = missing_from_hubcap

                        enriched_depots = {}
                        filter_soundtracks = get_settings().value("filter_soundtracks", True, type=bool)
                        db_enrichments = {}
                        try:
                            from managers.db_manager import DatabaseManager
                            db_enrichments = DatabaseManager().get_depot_enrichments(str(game_data.get("appid", "")))
                        except Exception:
                            pass

                        for depot_id, lua_data in filtered_depots.items():
                            final_depot_data = {"key": lua_data["key"]}
                            details = api_details.get(str(depot_id))

                            base_description = known_depot_descriptions.get(
                                depot_id, lua_data["desc"]
                            )

                            e_info = db_enrichments.get(str(depot_id))
                            if e_info:
                                is_generic = (
                                    not base_description
                                    or bool(re.match(r"^(?:\[.*?\]\s*)?Depot \d+$", base_description, re.IGNORECASE))
                                    or bool(re.match(r"^(?:\[.*?\]\s*)?DLC \d+$", base_description, re.IGNORECASE))
                                )
                                if is_generic and e_info.get("name"):
                                    if e_info.get("is_dlc"):
                                        base_description = f"[DLC] {e_info['name']}"
                                    else:
                                        base_description = e_info["name"]
                                if not details and e_info.get("oslist"):
                                    final_depot_data["oslist"] = e_info["oslist"]
                                if e_info.get("size_bytes"):
                                    final_depot_data["size"] = e_info["size_bytes"]
                                if e_info.get("size_str"):
                                    final_depot_data["size_str"] = e_info["size_str"]
                                if e_info.get("is_dlc"):
                                    final_depot_data["is_dlc"] = True

                            if details:
                                tags = []
                                if details.get("oslist"):
                                    tags.append(f"[{details['oslist'].upper()}]")
                                if details.get("steamdeck"):
                                    tags.append("[DECK]")

                                if details.get("language"):
                                    base_description += (
                                        f" ({details['language'].capitalize()})"
                                    )

                                final_description = (
                                    " ".join(tags) + " " + base_description
                                    if tags
                                    else base_description
                                )

                                final_depot_data["oslist"] = details.get("oslist")
                                final_depot_data["language"] = details.get("language")
                            else:
                                final_description = base_description

                            if filter_soundtracks:
                                lower_desc = final_description.lower()
                                if "soundtrack" in lower_desc or re.search(
                                    r"\bost\b", lower_desc
                                ):
                                    logger.info(
                                        f"Filtering out soundtrack depot {depot_id} ('{final_description}')."
                                    )
                                    continue

                            api_size = None
                            if details:
                                api_size = details.get("size")
                                if not api_size and details.get("manifests"):
                                    branch_entry = details["manifests"].get(game_data.get("branch", "public")) or details["manifests"].get("public")
                                    if isinstance(branch_entry, dict):
                                        api_size = branch_entry.get("download") or branch_entry.get("size")
                                if not api_size:
                                    api_size = details.get("maxsize")

                            if api_size:
                                final_depot_data["size"] = str(api_size)
                                logger.debug(
                                    f"Using API size for depot {depot_id}: {api_size}"
                                )
                            else:
                                lua_size = game_data.get("manifest_sizes", {}).get(
                                    depot_id
                                )
                                if lua_size:
                                    final_depot_data["size"] = str(lua_size)
                                    logger.debug(
                                        f"Using LUA fallback size for depot {depot_id}: {lua_size}"
                                    )
                                else:
                                    logger.debug(
                                        f"No size found for depot {depot_id} in API or LUA."
                                    )

                            final_depot_data["desc"] = final_description
                            enriched_depots[depot_id] = final_depot_data

                        game_data["depots"] = enriched_depots

                if not game_data.get("game_name"):
                    _fallback_name = f"App_{game_data.get('appid', 'Unknown')}"
                    game_data["game_name"] = _fallback_name
                    logger.warning(
                        f"Could not determine game name from Lua or API. Fallback to {_fallback_name}"
                    )

                manifest_dir = os.path.join(
                    tempfile.gettempdir(), "mistwalker_manifests"
                )
                os.makedirs(manifest_dir, exist_ok=True)
                for name, content in manifest_files.items():
                    with open(os.path.join(manifest_dir, name), "wb") as f:
                        f.write(content)

            logger.info("Zip processing task completed successfully.")
            return game_data
        except Exception as e:
            logger.error(f"Zip processing failed: {e}", exc_info=True)
            raise
