import logging
import os
import re
import time
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from utils.helpers import get_base_path
from utils.settings import get_settings

logger = logging.getLogger(__name__)

# Maximum age (in seconds) of a cached LUA file before it is considered too stale
# to trust for update comparison. Default: 7 days.
_LUA_CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600


def _is_network_available() -> bool:
    """Quick pre-flight check: probe Steam store to verify internet connectivity.
    Returns True if network is reachable, False otherwise.
    Uses lightweight HEAD requests with short 3s timeout.
    """
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(
            "https://store.steampowered.com",
            method="HEAD",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status < 500
    except urllib.error.HTTPError:
        # Any HTTP response from Steam means connectivity is active
        return True
    except Exception:
        # Secondary fallback probe
        try:
            req = urllib.request.Request(
                "https://1.1.1.1",
                method="HEAD",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=2):
                return True
        except Exception:
            return False

try:
    from core.steam_api import batched_get_product_info
except ImportError:
    # For testing purposes
    batched_get_product_info = None


def _store_check_diag(appid, diag_meta):
    """Store depot diff data into QSettings for cache metadata assembly."""
    from utils.settings import get_settings
    s = get_settings()
    diffs = diag_meta.get("depot_diffs", {})
    i = 0
    for depot_id, diff_data in diffs.items():
        val = f"{depot_id}|{diff_data.get('saved', '')}|{diff_data.get('current', '')}"
        s.setValue(f"last_check_depot_diff/{appid}/{i}", val)
        i += 1
    # Clean up any stale entries beyond the current count
    while True:
        stale_key = f"last_check_depot_diff/{appid}/{i}"
        if not s.value(stale_key, "", type=str):
            break
        s.remove(stale_key)
        i += 1


class ManifestCheckTask(QObject):
    """
    Asynchronous task to check game updates by comparing .depot files
    with current Steam API manifest data without updating the database.
    """

    # Signals
    game_update_checked = pyqtSignal(str, str)  # (appid, update_status)
    progress = pyqtSignal(int, int)  # (current, total)
    batch_progress = pyqtSignal(int, int)  # (current_batch, total_batches)
    completed = pyqtSignal()
    error = pyqtSignal(tuple)  # (Exception, message, traceback)

    def __init__(self, games_list, trigger: str = "AUTO_TIMER"):
        """
        Args:
            games_list: List of game dictionaries to check
            trigger: Trigger source ('USER_MANUAL', 'AUTO_TIMER', 'BOOT_CHECK', 'USER_SINGLE')
        """
        super().__init__()
        self.games_list = games_list
        self.trigger = trigger
        self._is_running = False

    def run(self):
        """Run the update checks asynchronously using batched API calls"""
        logger.info(f"Starting async update check for {len(self.games_list)} games")
        self._is_running = True

        try:
            total_games = len(self.games_list)
            checked_games = 0

            # Collect all valid appids
            valid_games = []
            for game in self.games_list:
                # Check if task was stopped
                if not self._is_running:
                    logger.debug("Update check task was stopped, exiting")
                    return

                appid = game.get("appid")

                # Skip invalid appids
                if not appid or appid in ("0", "N/A", "unknown"):
                    logger.debug(f"Skipping update check for invalid appid: {appid}")
                    checked_games += 1
                    self.progress.emit(checked_games, total_games)
                    continue

                valid_games.append(game)

            if not valid_games:
                logger.warning("No valid games to check")
                return

            logger.info(f"Valid games to check: {len(valid_games)}")

            # Read tokens from depot files for token-gated apps
            access_tokens = {}
            additional_appids = set()
            for game in valid_games:
                appid = game.get("appid")
                
                # Check for DLC-only mode state
                is_dlc_mode = False
                try:
                    from utils.settings import get_settings
                    _s = get_settings()
                    is_dlc_mode = _s.value(f"dlc_only_mode/{appid}", False, type=bool)
                except Exception:
                    pass

                depot_file = Path(get_base_path()) / "depots" / f"{appid}.depot"
                if depot_file.exists():
                    try:
                        content = depot_file.read_text().strip()
                        for line in content.splitlines():
                            line = line.strip()
                            if not line or ":" not in line:
                                continue
                            parts = line.split(":", 2)
                            depot_id = parts[0].strip()
                            # Note: depot_id is a depot of this app, not a standalone appid.
                            # It is already returned in the parent game's depots payload.

                            if len(parts) >= 3 and parts[2].strip():
                                token = parts[2].strip()
                                access_tokens[appid] = token
                                access_tokens[depot_id] = token
                    except OSError:
                        pass

                if is_dlc_mode:
                    # In DLC mode, collect all DLC AppIDs associated with this game:
                    # 1. From cached LUA (matches addappid(...) for dedicated DLC appids)
                    lua_file = Path(get_base_path()) / "cached_luas" / f"{appid}.lua"
                    if lua_file.exists():
                        try:
                            for m in re.finditer(r"addappid\(\s*(\d+)", lua_file.read_text()):
                                additional_appids.add(m.group(1))
                        except Exception as e:
                            logger.error(f"Error parsing cached LUA for DLC appids ({appid}): {e}")

                    # 2. From dlc_helpers / .depot file
                    try:
                        from utils.dlc_helpers import get_dlc_only_info
                        for dlc_entry in get_dlc_only_info(appid):
                            additional_appids.add(dlc_entry["dlc_appid"])
                    except Exception as e:
                        logger.error(f"Error collecting DLC appids for {appid}: {e}")

            # Use batched API call for all valid games and any DLC depot IDs
            appid_list = list({game.get("appid") for game in valid_games if game.get("appid")} | additional_appids)
            batch_size = 20
            rate_limit_delay = 0.3

            # Calculate number of batches for progress reporting
            num_batches = (len(appid_list) + batch_size - 1) // batch_size
            logger.info(
                f"Will process {len(appid_list)} appids in {num_batches} batches"
            )

            # 1. Pre-flight connectivity check — abort early if offline to avoid
            #    25 concurrent worker threads all failing with timeout errors, which
            #    wastes ~6s per game × 3 retries = significant UI freeze for large libraries.
            if not _is_network_available():
                logger.warning(
                    "Update check aborted: network is not reachable (pre-flight check failed). "
                    "Skipping API fetch — all games will retain their current update status."
                )
                # Emit cannot_determine only for games whose status is not already known
                # (i.e. don't downgrade update_available to cannot_determine)
                for game in valid_games:
                    appid = game.get("appid")
                    current_status = game.get("update_status", "")
                    if current_status not in ("update_available",):
                        self.game_update_checked.emit(appid, "cannot_determine")
                return

            # 2. Check update check provider preference from QSettings (default: steampics)
            settings = get_settings()
            update_provider = settings.value("update_check_api_provider", "auto", type=str)
            batched_results = {}

            if update_provider == "auto":
                # HYBRID DUAL-ENGINE RACE (Option 1):
                # Runs SteamCMD REST API (50 workers) and live Steam PICS in parallel!
                from core.steam_api import batched_fetch_steamcmd_info
                import concurrent.futures

                logger.info(
                    f"Starting Auto (Hybrid) update check for {len(appid_list)} games (SteamCMD + Steam PICS in parallel)..."
                )
                cmd_results = {}
                pics_results = {}

                def _fetch_cmd_job():
                    try:
                        def on_cmd_progress(current_fetched, total_to_fetch):
                            progress_val = min(total_games, int(current_fetched * total_games / max(1, total_to_fetch)))
                            self.progress.emit(progress_val, total_games)

                        return batched_fetch_steamcmd_info(
                            appid_list,
                            max_workers=50,
                            on_progress=on_cmd_progress,
                        )
                    except Exception as cmd_err:
                        logger.debug(f"Auto-hybrid SteamCMD fetch error: {cmd_err}")
                        return {}

                def _fetch_pics_job():
                    if batched_get_product_info is None:
                        return {}
                    try:
                        from core.steam_api import get_steam_worker
                        _worker = get_steam_worker()
                        _worker._started_evt.wait(timeout=10)
                        if not (getattr(_worker.client, "connected", False) and getattr(_worker.client, "logged_on", False)):
                            logger.debug("Auto-hybrid Steam PICS worker not logged on; relying silently on SteamCMD.")
                            return {}

                        def on_pics_progress(current_fetched, total_to_fetch):
                            progress_val = min(total_games, int(current_fetched * total_games / max(1, total_to_fetch)))
                            self.progress.emit(progress_val, total_games)

                        return batched_get_product_info(
                            appid_list,
                            access_tokens=access_tokens,
                            batch_size=50,
                            rate_limit_delay=0.15,
                            is_cancelled=lambda: not self._is_running,
                            request_timeout=10,
                            on_progress=on_pics_progress,
                        )
                    except Exception as pics_err:
                        logger.debug(f"Auto-hybrid Steam PICS fetch error: {pics_err}")
                        return {}

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    f_cmd = executor.submit(_fetch_cmd_job)
                    f_pics = executor.submit(_fetch_pics_job)
                    cmd_results = f_cmd.result()
                    pics_results = f_pics.result()

                # Merge with "Highest BuildID Wins" & "PICS Preferred on Tie"
                for aid in appid_list:
                    aid_str = str(aid)
                    c_data = cmd_results.get(aid_str)
                    p_data = pics_results.get(aid_str)

                    if not c_data and not p_data:
                        continue
                    if p_data and not c_data:
                        batched_results[aid_str] = p_data
                        continue
                    if c_data and not p_data:
                        batched_results[aid_str] = c_data
                        continue

                    # Both available: compare build IDs
                    try:
                        c_bid = int(c_data.get("buildid") or 0)
                    except (ValueError, TypeError):
                        c_bid = 0
                    try:
                        p_bid = int(p_data.get("buildid") or 0)
                    except (ValueError, TypeError):
                        p_bid = 0

                    if p_bid > c_bid:
                        batched_results[aid_str] = p_data
                    elif c_bid > p_bid:
                        batched_results[aid_str] = c_data
                    else:
                        # Equal build ID: Prefer PICS because its depot manifests are direct from Valve,
                        # but if PICS has empty depots for an app and CMD has them, use CMD
                        p_depots = p_data.get("depots", {})
                        if not p_depots and c_data.get("depots"):
                            batched_results[aid_str] = c_data
                        else:
                            batched_results[aid_str] = p_data

                logger.info(
                    f"Auto (Hybrid) check resolved {len(batched_results)}/{len(appid_list)} games "
                    f"(SteamCMD: {len(cmd_results)}, Steam PICS: {len(pics_results)})."
                )

            elif update_provider == "steamcmd":
                # Primary: SteamCMD REST API (Fast concurrent HTTP via CDN)
                from core.steam_api import batched_fetch_steamcmd_info
                try:
                    logger.info(f"Starting SteamCMD REST API primary batch check for {len(appid_list)} games...")
                    def on_cmd_progress(current_fetched, total_to_fetch):
                        progress_val = min(total_games, int(current_fetched * total_games / max(1, total_to_fetch)))
                        self.progress.emit(progress_val, total_games)

                    batched_results = batched_fetch_steamcmd_info(
                        appid_list,
                        max_workers=50,
                        on_progress=on_cmd_progress,
                    )
                except Exception as cmd_err:
                    logger.warning(f"SteamCMD REST API primary batch fetch error: {cmd_err}")
                    batched_results = {}

                # Fallback: Live Steam PICS for any missing or unresolved games
                missing_appids = [aid for aid in appid_list if str(aid) not in batched_results]
                if missing_appids and batched_get_product_info is not None:
                    logger.info(
                        f"{len(missing_appids)} games missing from SteamCMD REST API; falling back to Steam PICS..."
                    )
                    try:
                        pics_results = batched_get_product_info(
                            missing_appids,
                            access_tokens=access_tokens,
                            batch_size=50,
                            rate_limit_delay=0.15,
                            is_cancelled=lambda: not self._is_running,
                            request_timeout=10,
                        )
                        batched_results.update(pics_results)
                    except BaseException as pics_err:
                        if isinstance(pics_err, (KeyboardInterrupt, SystemExit)):
                            raise
                        logger.error(f"PICS fallback error in update check: {pics_err}")
            else:
                # Primary: Live Steam PICS client (Direct from Valve, authoritative, zero stale cache)
                if batched_get_product_info is not None:
                    _worker_ok = False
                    try:
                        from core.steam_api import get_steam_worker
                        _worker = get_steam_worker()
                        _worker._started_evt.wait(timeout=10)
                        _worker_ok = (
                            _worker.client is not None
                            and getattr(_worker.client, "connected", False)
                            and getattr(_worker.client, "logged_on", False)
                        )
                        if _worker_ok:
                            logger.info("Steam PICS worker pre-warmed and logged on — starting batch check.")
                        else:
                            logger.debug(
                                "Steam PICS worker not logged on; seamlessly falling back to SteamCMD REST API."
                            )
                    except Exception as _pw_err:
                        logger.debug(f"Steam PICS worker pre-warm failed: {_pw_err}")
                        _worker_ok = False

                    if _worker_ok:
                        try:
                            logger.info(f"Starting live Steam PICS primary batch check for {len(appid_list)} games...")
                            def on_pics_progress(current_fetched, total_to_fetch):
                                progress_val = min(total_games, int(current_fetched * total_games / max(1, total_to_fetch)))
                                self.progress.emit(progress_val, total_games)

                            batched_results = batched_get_product_info(
                                appid_list,
                                access_tokens=access_tokens,
                                batch_size=50,
                                rate_limit_delay=0.15,
                                is_cancelled=lambda: not self._is_running,
                                request_timeout=10,
                                on_progress=on_pics_progress,
                            )
                        except BaseException as pics_err:
                            if isinstance(pics_err, (KeyboardInterrupt, SystemExit)):
                                raise
                            logger.debug(f"Steam PICS batch fetch error: {pics_err}")
                            batched_results = {}

                # Robust Fallback: SteamCMD REST API for any missing, unresolved, or failed games
                missing_appids = [aid for aid in appid_list if str(aid) not in batched_results]
                if missing_appids and self._is_running:
                    from core.steam_api import batched_fetch_steamcmd_info
                    logger.debug(
                        f"{len(missing_appids)}/{len(appid_list)} games missing or failed from Steam PICS; "
                        "silently falling back to SteamCMD REST API..."
                    )
                    try:
                        def on_cmd_fallback_progress(current_fetched, total_to_fetch):
                            already_done = len(appid_list) - len(missing_appids)
                            current_total = already_done + current_fetched
                            progress_val = min(total_games, int(current_total * total_games / max(1, len(appid_list))))
                            self.progress.emit(progress_val, total_games)

                        cmd_results = batched_fetch_steamcmd_info(
                            missing_appids,
                            max_workers=50,
                            on_progress=on_cmd_fallback_progress,
                        )
                        batched_results.update(cmd_results)
                    except Exception as cmd_fallback_err:
                        logger.debug(f"SteamCMD REST API fallback error: {cmd_fallback_err}")

            if not self._is_running:
                logger.debug("Update check task was stopped after batched fetch")
                return

            # Process each game with the batched results
            up_to_date_count = 0
            update_available_count = 0
            cannot_determine_count = 0
            update_games_list = []

            for game in valid_games:
                # Check if task was stopped
                if not self._is_running:
                    break

                appid = game.get("appid")

                try:
                    # Use batched results to determine update status
                    update_status = self._check_game_update_with_batched_data(
                        game, batched_results
                    )
                    # Emit signal with results
                    self.game_update_checked.emit(appid, update_status)

                    if update_status == "update_available":
                        update_available_count += 1
                        g_name = game.get("name") or game.get("clean_name") or str(appid)
                        update_games_list.append(g_name)
                    elif update_status == "up_to_date":
                        up_to_date_count += 1
                    else:
                        cannot_determine_count += 1

                except Exception as e:
                    logger.error(f"Error checking update for game {appid}: {e}")
                    self.error.emit((type(e), str(e), traceback.format_exc()))
                    self.game_update_checked.emit(appid, "cannot_determine")
                    cannot_determine_count += 1

                checked_games += 1
                self.progress.emit(checked_games, total_games)

            logger.info("Async update check complete")

        finally:
            self.completed.emit()

    @staticmethod
    def _check_game_update_with_batched_data(game_data, batched_results):
        """
        Check if a game has an update available using pre-fetched batched data.

        This method uses the results from a batched API call to determine if a game
        has an update, comparing the saved manifest ID with the current public manifest ID.

        Args:
            game_data: Dictionary containing game information
            batched_results: Dict mapping appid -> product_info from batched_get_product_info()

        Returns:
            str: Status constant ('update_available', 'up_to_date', 'cannot_determine')
        """
        appid = game_data.get("appid")

        # Skip if no valid appid
        if not appid or appid in ("0", "N/A", "unknown"):
            logger.info(f"[UpdateCheck] Cannot determine status: Invalid/missing AppID '{appid}' in game_data")
            return "cannot_determine"

        # Pinned build bypass: If game is pinned to a specific build, ignore updates and treat as up-to-date
        settings = get_settings()
        if settings.value(f"pin_build/{appid}", False, type=bool):
            logger.info(f"[UpdateCheck {appid}] Game build is pinned. Ignoring updates. Status: up_to_date.")
            return "up_to_date"

        # DLC-Only mode: the normal .depot file comparison below handles this correctly.
        # It reads all DLC depot IDs from {appid}.depot and looks each one up across ALL
        # entries in batched_results (including DLC appids fetched via additional_appids
        # in run()), so no special branch is needed here.

        # Read saved manifest ID from depot file
        depots_dir = Path(get_base_path()) / "depots"
        depot_file = depots_dir / f"{appid}.depot"

        if not depot_file.exists():
            # No saved manifest file, cannot determine version
            logger.info(f"[UpdateCheck {appid}] Cannot determine status: Local depot file does not exist ({depot_file})")
            return "cannot_determine"

        # Read the saved manifest IDs from the depot file
        saved_depots = {}
        try:
            with open(depot_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or ":" not in line:
                        continue
                    parts = line.split(":", 2)
                    depot_id = parts[0].strip()
                    manifest_id = parts[1].strip()
                    saved_depots[depot_id] = manifest_id
        except Exception as e:
            logger.error(f"[UpdateCheck {appid}] Error reading depot file {depot_file}: {e}")
            return "cannot_determine"

        if not saved_depots:
            logger.info(f"[UpdateCheck {appid}] Cannot determine status: Local depot file ({depot_file}) is empty or contains no valid depot entries")
            return "cannot_determine"

        # Compare manifest IDs for each saved depot
        try:
            any_update_available = False
            all_cannot_determine = True
            reasons = []
            _depot_diffs = []  # (depot_id, saved_manifest, current_manifest)

            # Read Steam API data
            steam_client_data = batched_results.get(appid, {})
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc_mode = is_dlc_only_mode(appid)

            # Resolve branch and build IDs for diagnostic logging only.
            # The depot manifest comparison below is the authoritative update signal.
            # Branch build IDs can increment for metadata-only pushes that don't
            # change depot content, so we never short-circuit on build ID alone.
            settings = get_settings()
            selected_branch = settings.value(f"selected_branch/{appid}", "public", type=str)
            branch_info = steam_client_data.get("branches", {}).get(selected_branch, {})
            branch_buildid = str(branch_info.get("buildid", "")) if isinstance(branch_info, dict) else ""
            
            settings = get_settings()
            installed_branch = settings.value(f"installed_branch/{appid}", "public", type=str)
            installed_bid = settings.value(f"installed_buildid/{appid}/{selected_branch}",
                                           settings.value(f"installed_buildid/{appid}", "", type=str),
                                           type=str)

            local_buildid = str(game_data.get("buildid") or "")
            effective_local_bid = installed_bid if (installed_branch == selected_branch and installed_bid) else local_buildid

            branch_bid_changed = False
            if branch_buildid and effective_local_bid and effective_local_bid not in ("", "0", "Unknown"):
                try:
                    branch_bid_changed = int(branch_buildid) > int(effective_local_bid)
                except (ValueError, TypeError):
                    branch_bid_changed = branch_buildid != effective_local_bid

            if branch_bid_changed:
                logger.info(
                    f"[UpdateCheck {appid}] Branch '{selected_branch}' build ID changed "
                    f"(local={effective_local_bid}, branch={branch_buildid}) — "
                    "checking depot manifests for actual content changes"
                )

            # Guard against stale API mirrors / lagging CDN caches offering older builds:
            # On Steam, build IDs are strictly sequential and monotonically increasing.
            # If the remote API build ID is strictly lower than the local installed build ID,
            # the API is returning stale/older data (or local install is newer).
            # We must NOT declare an update, which would downgrade the game!
            if not is_dlc_mode and branch_buildid and effective_local_bid and effective_local_bid not in ("", "0", "Unknown"):
                try:
                    if int(branch_buildid) < int(effective_local_bid):
                        logger.info(
                            f"[UpdateCheck {appid}] API build ID ({branch_buildid}) is older than local build ID ({effective_local_bid}) "
                            f"on branch '{selected_branch}'. Upstream API mirror may be stale; ignoring downgrade trigger. Status: up_to_date."
                        )
                        diag_meta = {
                            "branch": selected_branch,
                            "branch_buildid": branch_buildid,
                            "local_buildid": effective_local_bid,
                            "reason": "local_build_newer_than_api",
                        }
                        _store_check_diag(appid, diag_meta)
                        return "up_to_date"
                except (ValueError, TypeError):
                    pass

            # Persist build ID info so cache can record diagnostic data
            settings.setValue(f"last_checked_branch_buildid/{appid}", branch_buildid)
            settings.setValue(f"last_checked_local_buildid/{appid}", effective_local_bid or local_buildid)
            settings.setValue(f"last_checked_branch/{appid}", selected_branch)

            if not steam_client_data:
                # If remote lookup failed/unavailable, preserve previously known valid status if available
                current_status = game_data.get("update_status", "")
                if current_status in ("up_to_date", "update_available"):
                    logger.info(
                        f"[UpdateCheck {appid}] AppID {appid} not found in remote API response; preserving existing status '{current_status}'"
                    )
                    return current_status

                logger.info(
                    f"[UpdateCheck {appid}] Cannot determine status: AppID {appid} was not returned in remote Steam API results payload"
                )
                return "cannot_determine"


            for saved_depot_id, saved_manifest_id in saved_depots.items():
                try:
                    # 1. Try in base game depots
                    depots = steam_client_data.get("depots", {})

                    # 2. Try in DLC depots
                    if saved_depot_id not in depots:
                        for app_info in batched_results.values():
                            if isinstance(app_info, dict):
                                dlc_depots = app_info.get("depots", {})
                                if saved_depot_id in dlc_depots:
                                    depots = dlc_depots
                                    break

                    # 2b. If still not in depots and base game listed DLCs (hasdepotsindlc), fetch DLC AppIDs live
                    if saved_depot_id not in depots:
                        dlc_list_str = steam_client_data.get("listofdlc", "")
                        if dlc_list_str:
                            try:
                                dlc_ids = [int(x) for x in dlc_list_str.split(",") if x.strip()]
                                if dlc_ids:
                                    res_dlc = None
                                    try:
                                        from core.steam_api import get_steam_worker
                                        worker = get_steam_worker()
                                        if getattr(worker.client, "connected", False) and getattr(worker.client, "logged_on", False):
                                            res_dlc = worker.execute("get_product_info", apps=dlc_ids, timeout=10)
                                    except Exception:
                                        res_dlc = None

                                    if not res_dlc:
                                        try:
                                            from core.steam_api import batched_fetch_steamcmd_info
                                            cmd_dlc_results = batched_fetch_steamcmd_info([str(x) for x in dlc_ids])
                                            for d_app_id, d_app in cmd_dlc_results.items():
                                                if isinstance(d_app, dict):
                                                    d_depots = d_app.get("depots", {})
                                                    batched_results[str(d_app_id)] = {"depots": d_depots}
                                                    if saved_depot_id in d_depots or str(saved_depot_id) in d_depots:
                                                        depots = d_depots
                                                        break
                                        except Exception as cmd_dlc_err:
                                            logger.debug(f"[UpdateCheck {appid}] SteamCMD DLC lookup error: {cmd_dlc_err}")
                                    elif res_dlc and isinstance(res_dlc, dict):
                                        for d_app_id, d_app in res_dlc.get("apps", {}).items():
                                            if isinstance(d_app, dict):
                                                d_depots = d_app.get("depots", {})
                                                batched_results[str(d_app_id)] = {"depots": d_depots}
                                                if saved_depot_id in d_depots or str(saved_depot_id) in d_depots:
                                                    depots = d_depots
                                                    break
                            except Exception as dlc_fetch_err:
                                logger.debug(f"[UpdateCheck {appid}] Live listofdlc lookup error for depot {saved_depot_id}: {dlc_fetch_err}")

                    current_manifest_id = None
                    if depots and saved_depot_id in depots:
                        depot_info = depots[saved_depot_id]
                        if isinstance(depot_info, dict):
                            branch_manifests = depot_info.get("manifests", {})
                            if isinstance(branch_manifests, dict) and selected_branch in branch_manifests:
                                branch_manifest_entry = branch_manifests[selected_branch]
                                if isinstance(branch_manifest_entry, dict):
                                    current_manifest_id = str(branch_manifest_entry.get("gid", ""))
                            if not current_manifest_id:
                                current_manifest_id = str(depot_info.get("manifest_id") or "")

                    # 3. Fallback: Parse from cached Hubcap LUA file.
                    #    IMPORTANT: Only use if the LUA cache is fresh enough (≤7 days).
                    #    A stale LUA from a previous successful fetch could contain an old
                    #    manifest GID that no longer matches the .depot file, causing a
                    #    false-positive update_available when the network is unavailable.
                    if not current_manifest_id:
                        lua_file = Path(get_base_path()) / "cached_luas" / f"{appid}.lua"
                        if lua_file.exists():
                            try:
                                lua_age = time.time() - lua_file.stat().st_mtime
                                if lua_age > _LUA_CACHE_MAX_AGE_SECONDS:
                                    logger.debug(
                                        f"[UpdateCheck {appid}] Skipping stale LUA cache for depot {saved_depot_id} "
                                        f"(age={lua_age/86400:.1f}d > {_LUA_CACHE_MAX_AGE_SECONDS/86400:.0f}d limit)"
                                    )
                                else:
                                    lua_text = lua_file.read_text()
                                    pattern = r"setManifestid\(\s*" + re.escape(saved_depot_id) + r'\s*,\s*"([^"]+)"'
                                    m = re.search(pattern, lua_text)
                                    if m:
                                        current_manifest_id = m.group(1).strip()
                                        logger.debug(f"[UpdateCheck {appid}] Resolved manifest for depot {saved_depot_id} from cached LUA: {current_manifest_id}")
                            except Exception as e:
                                logger.error(f"Error parsing cached LUA for depot {saved_depot_id} manifest: {e}")

                    if not current_manifest_id:
                        reason_msg = f"Depot {saved_depot_id} not found in Steam depots payload or cached LUA file"
                        reasons.append(reason_msg)
                        logger.debug(f"[UpdateCheck {appid}] {reason_msg}")
                        continue

                    if current_manifest_id:
                        all_cannot_determine = False

                        # Save the latest manifest ID to settings for tracking
                        settings = get_settings()
                        settings.setValue(f"latest_steam_manifest_id/{appid}", current_manifest_id)

                        branch_timeupdated = branch_info.get("timeupdated") if isinstance(branch_info, dict) else None
                        timeupdated = branch_timeupdated or steam_client_data.get("timeupdated")
                        if timeupdated:
                            settings.setValue(f"latest_steam_timeupdated/{appid}", timeupdated)

                        # Compare manifest IDs
                        if saved_manifest_id != current_manifest_id:
                            logger.info(
                                f"[UpdateCheck {appid}] Update available for depot {saved_depot_id} (branch '{selected_branch}'): saved={saved_manifest_id}, current={current_manifest_id}"
                            )
                            any_update_available = True
                            _depot_diffs.append((saved_depot_id, saved_manifest_id, current_manifest_id))
                    else:
                        reason_msg = f"Steam API returned no public manifest_id for depot {saved_depot_id}"
                        reasons.append(reason_msg)
                        logger.debug(f"[UpdateCheck {appid}] {reason_msg}")
                    
                except Exception as e:
                    logger.error(f"[UpdateCheck {appid}] Error checking depot {saved_depot_id} update: {e}")

            # Build diagnostic metadata for the result
            diag_meta = {
                "branch": selected_branch,
                "branch_buildid": branch_buildid,
                "local_buildid": effective_local_bid or local_buildid,
            }

            if any_update_available:
                diag_meta["reason"] = "depot_manifest_mismatch"
                diag_meta["depot_diffs"] = {
                    did: {"saved": sv, "current": cv}
                    for did, sv, cv in _depot_diffs
                }
                _store_check_diag(appid, diag_meta)
                return "update_available"
            if all_cannot_determine:
                diag_meta["reason"] = "no_manifest_resolved"
                logger.info(
                    f"[UpdateCheck {appid}] Update status: cannot_determine. Reason: None of the {len(saved_depots)} saved depot(s) ({list(saved_depots.keys())}) resolved to a public manifest ID from Steam API data. Details: {'; '.join(reasons)}"
                )
                return "cannot_determine"
            diag_meta["reason"] = "manifests_match"
            _store_check_diag(appid, diag_meta)
            return "up_to_date"

        except Exception as e:
            logger.error(f"[UpdateCheck {appid}] Exception checking for updates: {e}")
            return "cannot_determine"

    @staticmethod
    def _get_depot_latest_manifest(depot_id: str, appid: str, batched_results: dict) -> str:
        # 1. Try in base game depots
        base_depots = batched_results.get(appid, {}).get("depots", {})
        if depot_id in base_depots:
            m = base_depots[depot_id].get("manifest_id")
            if not m:
                m = base_depots[depot_id].get("manifests", {}).get("public", {}).get("gid")
            if m:
                return str(m)

        # 2. Try in all loaded appids in batched_results
        for app_info in batched_results.values():
            if isinstance(app_info, dict):
                dlc_depots = app_info.get("depots", {})
                if depot_id in dlc_depots:
                    m = dlc_depots[depot_id].get("manifest_id")
                    if not m:
                        m = dlc_depots[depot_id].get("manifests", {}).get("public", {}).get("gid")
                    if m:
                        return str(m)

        # 3. Fallback: Parse from cached Hubcap LUA file
        lua_file = Path(get_base_path()) / "cached_luas" / f"{appid}.lua"
        if lua_file.exists():
            try:
                lua_text = lua_file.read_text()
                pattern = r"setManifestid\(\s*" + re.escape(depot_id) + r'\s*,\s*"([^"]+)"'
                m = re.search(pattern, lua_text)
                if m:
                    latest_manifest = m.group(1).strip()
                    logger.debug(f"[DLC Update Check] Resolved latest manifest for depot {depot_id} from cached LUA: {latest_manifest}")
                    return latest_manifest
            except Exception as e:
                logger.error(f"Error parsing cached LUA for depot {depot_id} manifest: {e}")

        return None

    @staticmethod
    def _get_installed_depot_manifest(depot_id: str, game_data: dict) -> str:
        install_path = game_data.get("install_path")
        if not install_path or not os.path.exists(install_path):
            return None

        ddm_dir = os.path.join(install_path, ".DepotDownloader")
        if not os.path.exists(ddm_dir):
            return None

        try:
            candidates = [
                fname for fname in os.listdir(ddm_dir)
                if fname.startswith(f"{depot_id}_") and fname.endswith(".manifest")
            ]
            # Sort newest-first so we always use the most recently written manifest
            candidates.sort(
                key=lambda f: os.path.getmtime(os.path.join(ddm_dir, f)),
                reverse=True,
            )
            for fname in candidates:
                base = fname[:-9]  # strip ".manifest"
                parts = base.split("_", 1)
                if len(parts) == 2:
                    return parts[1]
        except Exception:
            pass
        return None

    @staticmethod
    def _check_dlc_only_update(appid, selected_depot_ids, batched_results, game_data):
        has_changes = False
        any_resolved = False

        for depot_id in selected_depot_ids:
            depot_id_str = str(depot_id)
            latest_manifest = ManifestCheckTask._get_depot_latest_manifest(depot_id_str, appid, batched_results)
            if not latest_manifest:
                continue

            any_resolved = True
            installed_manifest = ManifestCheckTask._get_installed_depot_manifest(depot_id_str, game_data)

            # If we don't have it installed yet, or it matches, it's not a pending update
            if installed_manifest and installed_manifest != latest_manifest:
                logger.info(
                    f"[DLC Only Check] Update available for depot {depot_id_str} "
                    f"of app {appid}: installed={installed_manifest}, latest={latest_manifest}"
                )
                has_changes = True

        if has_changes:
            return "update_available"
        if any_resolved:
            return "up_to_date"
        logger.info(
            f"[UpdateCheck {appid}] [DLC Only] Cannot determine status: None of the selected DLC depots {selected_depot_ids} resolved to a public manifest ID in Steam API data"
        )
        return "cannot_determine"

    def stop(self):
        """Stop the task"""
        logger.debug("Stopping manifest check task")
        self._is_running = False
