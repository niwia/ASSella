import logging
import os
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from utils.helpers import get_base_path
from utils.settings import get_settings

logger = logging.getLogger(__name__)

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

    def __init__(self, games_list):
        """
        Args:
            games_list: List of game dictionaries to check
        """
        super().__init__()
        self.games_list = games_list
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
                depot_file = Path(get_base_path()) / "depots" / f"{appid}.depot"
                if depot_file.exists():
                    try:
                        content = depot_file.read_text().strip()
                        parts = content.split(":", 2)
                        
                        if parts and parts[0].strip():
                            main_depot_id = parts[0].strip()
                            additional_appids.add(main_depot_id)
                            
                        if len(parts) >= 3 and parts[2].strip():
                            access_tokens[appid] = parts[2].strip()
                            if 'main_depot_id' in locals():
                                access_tokens[main_depot_id] = parts[2].strip()
                    except OSError:
                        pass

            # Use batched API call for all valid games and any DLC depot IDs
            appid_list = list({game.get("appid") for game in valid_games if game.get("appid")} | additional_appids)
            batch_size = 20
            rate_limit_delay = 0.3

            # Calculate number of batches for progress reporting
            num_batches = (len(appid_list) + batch_size - 1) // batch_size
            logger.info(
                f"Will process {len(appid_list)} appids in {num_batches} batches"
            )

            # Fetch all data in batched calls
            if batched_get_product_info is None:
                logger.warning(
                    "batched_get_product_info is not available; skipping API fetch and assuming no data."
                )
                batched_results = {}
            else:
                try:
                    def on_fetch_progress(current_fetched, total_to_fetch):
                        progress_val = min(total_games, int(current_fetched * total_games / total_to_fetch))
                        self.progress.emit(progress_val, total_games)

                    batched_results = batched_get_product_info(
                        appid_list,
                        access_tokens=access_tokens,
                        batch_size=batch_size,
                        rate_limit_delay=rate_limit_delay,
                        is_cancelled=lambda: not self._is_running,
                        request_timeout=10,
                        on_progress=on_fetch_progress,
                    )
                except BaseException as e:
                    # Safety net: gevent.timeout.Timeout (and other BaseExceptions)
                    # can escape the retry loop in steam_api if something unexpected
                    # happens. Catch them here so the task thread doesn't crash.
                    if isinstance(e, (KeyboardInterrupt, SystemExit)):
                        raise
                    logger.error(
                        f"batched_get_product_info raised {type(e).__name__}: {e} — "
                        "falling back to empty results (all games will show 'cannot determine')."
                    )
                    batched_results = {}

            if not self._is_running:
                logger.debug("Update check task was stopped after batched fetch")
                return

            # Process each game with the batched results
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

                except Exception as e:
                    logger.error(f"Error checking update for game {appid}: {e}")
                    self.error.emit((type(e), str(e), traceback.format_exc()))
                    self.game_update_checked.emit(appid, "cannot_determine")

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

        # DLC-Only mode: check only the user-selected depots, not the whole game
        try:
            from utils.settings import get_settings
            import json as _json
            _s = get_settings()
            if _s.value(f"dlc_only_mode/{appid}", False, type=bool):
                val = _s.value(f"depot_selection/{appid}", "", type=str)
                if val:
                    saved_selection = _json.loads(val)
                    selected_depot_ids = saved_selection.get("selected", [])
                    if selected_depot_ids:
                        return ManifestCheckTask._check_dlc_only_update(
                            appid, selected_depot_ids, batched_results, game_data
                        )
        except Exception as _e:
            logger.debug(f"DLC-only mode check failed for {appid}: {_e}")

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
            if branch_buildid and effective_local_bid and effective_local_bid != "Unknown":
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

            # Persist build ID info so cache can record diagnostic data
            settings.setValue(f"last_checked_branch_buildid/{appid}", branch_buildid)
            settings.setValue(f"last_checked_local_buildid/{appid}", effective_local_bid or local_buildid)
            settings.setValue(f"last_checked_branch/{appid}", selected_branch)

            if not steam_client_data:
                logger.info(
                    f"[UpdateCheck {appid}] Cannot determine status: AppID {appid} was not returned in Steam API batched results payload (Steam API / DB lookup returned no product info)"
                )

            for saved_depot_id, saved_manifest_id in saved_depots.items():
                try:
                    # 1. Try in base game depots
                    depots = steam_client_data.get("depots", {})

                    # 2. Try in DLC depots
                    if saved_depot_id not in depots and saved_depot_id in batched_results:
                        dlc_data = batched_results[saved_depot_id]
                        dlc_depots = dlc_data.get("depots", {})
                        if saved_depot_id in dlc_depots:
                            depots = dlc_depots

                    if not depots or saved_depot_id not in depots:
                        reason_msg = f"Depot {saved_depot_id} not found in Steam depots payload (available depots in API: {list(depots.keys()) if depots else 'None'})"
                        reasons.append(reason_msg)
                        logger.debug(f"[UpdateCheck {appid}] {reason_msg}")
                        continue

                    depot_info = depots[saved_depot_id]
                    # Check if target branch specifies a manifest GID for this depot
                    branch_manifest_id = None
                    if isinstance(depot_info, dict):
                        branch_manifests = depot_info.get("manifests", {})
                        if isinstance(branch_manifests, dict) and selected_branch in branch_manifests:
                            branch_manifest_entry = branch_manifests[selected_branch]
                            if isinstance(branch_manifest_entry, dict):
                                branch_manifest_id = str(branch_manifest_entry.get("gid", ""))
                        if not branch_manifest_id:
                            branch_manifest_id = str(depot_info.get("manifest_id") or "")

                    current_manifest_id = branch_manifest_id

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
            return base_depots[depot_id].get("manifest_id")

        # 2. Try in batched_results[depot_id] directly
        dlc_depots = batched_results.get(depot_id, {}).get("depots", {})
        if depot_id in dlc_depots:
            return dlc_depots[depot_id].get("manifest_id")

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
