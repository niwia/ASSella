import logging
import os
import re
import zipfile
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QMetaObject, Q_ARG, pyqtSignal, pyqtSlot

from core.steam_helpers import (
    get_steam_libraries,
    get_library_index,
    find_steam_install,
)
from core.tasks.manifest_check_task import ManifestCheckTask
from utils.helpers import get_base_path
from utils.task_runner import TaskRunner
from utils.settings import get_settings
from utils.update_status_cache import get_update_cache
from utils.yaml_config_manager import (
    get_user_config_path,
    add_additional_app,
    remove_additional_app,
    fix_slssteam_config_indentation,
    get_app_tokens,
    add_app_token,
    is_greenluma_wrapper_mode_enabled,
    is_slssteam_mode_enabled,
)
from utils.wrapper_metadata import load_selected_dlcs, persist_selected_dlcs

logger = logging.getLogger(__name__)

# Update status constants
UPDATE_STATUS = {
    "UPDATE_AVAILABLE": "update_available",
    "UP_TO_DATE": "up_to_date",
    "CANNOT_DETERMINE": "cannot_determine",
    "CHECKING": "checking",  # While async update check is running
}


class GameManager(QObject):
    """
    Manager for handling game library operations.
    Manages game metadata, library view, and game-related operations.
    """

    # Signals
    game_updated = pyqtSignal(str)
    library_updated = pyqtSignal()
    game_selected = pyqtSignal(str)
    scan_complete = pyqtSignal(int)  # Emits number of games found
    game_update_status_changed = pyqtSignal(str, str)  # (appid, update_status)
    all_updates_checked = pyqtSignal()  # Emitted when a full batch check finishes
    game_hubcap_status_checked = pyqtSignal(str, bool, bool)  # (appid, needs_update, update_in_progress)
    update_check_progress = pyqtSignal(int, int)  # (current, total)

    @pyqtSlot(int)
    def _emit_scan_signals(self, games_found: int) -> None:
        """Main-thread slot: emit library_updated and scan_complete after a background scan."""
        self.is_scanning = False
        self.library_updated.emit()
        self.scan_complete.emit(games_found)

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.settings = main_window.settings

        # Game library data
        self.games = []
        self.selected_game = None
        self.filtered_games = []

        # O(1) lookup: appid -> game dict reference
        self._games_by_appid: dict = {}

        # Manifest check task management
        self.manifest_check_task = None
        self.manifest_check_runner = None
        self._games_to_check = []
        # Counter used by single-game checks (not the batch runner)
        self._single_check_runners: list = []

        # Library scan task management
        self.scan_runner = None
        self._scan_cancelled = False
        self.is_scanning = False

        logger.info("GameManager initialized")

    @staticmethod
    def _get_sorted_games(games_list):
        """Helper method to sort games by name (case-insensitive)"""
        return sorted(games_list, key=lambda x: x.get("game_name", "").lower())

    def add_game(self, game_data):
        """Add a game to the library"""
        logger.info(f"Adding game to library: {game_data.get('game_name', 'Unknown')}")
        self.games.append(game_data)
        game_id = str(game_data.get("appid", ""))
        if game_id:
            self._games_by_appid[game_id] = game_data
        # Sort the main games list
        self.games = self._get_sorted_games(self.games)
        self._apply_filters()
        self.library_updated.emit()

    def remove_game(self, game_id):
        """Remove a game from the library"""
        logger.info(f"Removing game from library: {game_id}")
        self.games = [g for g in self.games if g.get("appid") != game_id]
        if str(game_id) in self._games_by_appid:
            del self._games_by_appid[str(game_id)]
        # Sort the main games list
        self.games = self._get_sorted_games(self.games)
        self._apply_filters()
        self.library_updated.emit()

    def get_game(self, game_id):
        """Get a specific game by ID (O(1) lookup)"""
        return self._games_by_appid.get(str(game_id))

    def get_all_games(self):
        """Get all games in the library - returns sorted list"""
        games_to_return = self.filtered_games if self.filtered_games else self.games
        return list(games_to_return)

    def select_game(self, game_id):
        """Select a specific game"""
        game = self.get_game(game_id)
        if game:
            self.selected_game = game
            self.game_selected.emit(game_id)
            logger.info(
                f"Selected game: {game.get('game_name', 'Unknown')} ({game_id})"
            )
            return True
        return False

    def update_game(self, game_id, game_data):
        """Update game information"""
        logger.info(f"Updating game: {game_id}")
        for i, game in enumerate(self.games):
            if game.get("appid") == game_id:
                self.games[i].update(game_data)
                self._games_by_appid[str(game_id)] = self.games[i]
                # Sort the main games list after update
                self.games = self._get_sorted_games(self.games)
                self.game_updated.emit(game_id)
                self._apply_filters()
                self.library_updated.emit()
                return True
        return False

    def _apply_filters(self):
        """Apply current filters to the game list"""
        self.filtered_games = self._get_sorted_games(self.games)

    def search_games(self, query):
        """Search games by name or other criteria"""
        if not query:
            self.filtered_games = []
            self._apply_filters()
            self.library_updated.emit()
            return

        query = query.lower()
        matched_games = [
            game for game in self.games if query in game.get("game_name", "").lower()
        ]
        self.filtered_games = self._get_sorted_games(matched_games)
        self.library_updated.emit()

    def clear_filters(self):
        """Clear all applied filters"""
        self.filtered_games = []
        self._apply_filters()
        self.library_updated.emit()

    def reset_up_to_date_for_recheck(self) -> None:
        """
        Reset 'up_to_date' games back to 'checking' so the next batch check
        re-validates them. Called exclusively by the periodic update timer.
        Games with 'update_available' are left untouched.
        """
        reset_count = 0
        settings = get_settings()
        for game in self.games:
            appid = game.get("appid")
            if appid and settings.value(f"pin_build/{appid}", False, type=bool):
                continue
            if game.get("update_status") == UPDATE_STATUS["UP_TO_DATE"]:
                game["update_status"] = UPDATE_STATUS["CHECKING"]
                reset_count += 1
        if reset_count:
            logger.info(f"Periodic recheck: reset {reset_count} 'up_to_date' game(s) to 'checking'")

    def check_game_updates_async(self, is_periodic: bool = False, force_refresh: bool = False):
        """
        Start async update checking for games that still need a check.

        Smart skip logic:
        - If force_refresh is True, we check ALL games (ignoring cache/update status).
        - Otherwise, we only skip games that are already marked 'update_available'.
        - 'up_to_date' entries in the cache are ignored for checks but act as a UI middleman.
        """
        # Cancel any existing batch task before starting a new one
        if (
            self.manifest_check_task is not None
            or self.manifest_check_runner is not None
        ):
            logger.info("Cancelling previous manifest check task")
            self.cancel_update_checks()

        # Build filtered list according to smart skip logic
        games_to_check = []
        for g in self.games:
            appid = g.get("appid")
            if appid in ("0", "N/A", "unknown"):
                continue

            # Pinned build bypass
            settings = get_settings()
            if settings.value(f"pin_build/{appid}", False, type=bool):
                g["update_status"] = UPDATE_STATUS["UP_TO_DATE"]
                self.game_update_status_changed.emit(appid, UPDATE_STATUS["UP_TO_DATE"])
                continue

            status = g.get("update_status", "")
            if not force_refresh and status == UPDATE_STATUS["UPDATE_AVAILABLE"]:
                continue  # Skip checking already known updateable games
            games_to_check.append(g)

        self._games_to_check = games_to_check

        if not self._games_to_check:
            logger.info("No games need an update check at this time")
            self.all_updates_checked.emit()
            return

        logger.info(
            f"Starting async update check for {len(self._games_to_check)} game(s) "
            f"(skipped {len(self.games) - len(self._games_to_check)} with known status)"
        )

        # Create new task
        self.manifest_check_task = ManifestCheckTask(self._games_to_check)
        self.update_check_progress.emit(0, len(self._games_to_check))

        # Connect signals
        self.manifest_check_task.game_update_checked.connect(
            self._on_game_update_checked
        )
        self.manifest_check_task.progress.connect(self._on_update_check_progress)
        self.manifest_check_task.completed.connect(self._on_update_check_completed)
        self.manifest_check_task.error.connect(self._on_update_check_error)

        # Start task via TaskRunner
        self.manifest_check_runner = TaskRunner()
        # Connect to cleanup_complete to clear references AFTER thread finishes
        self.manifest_check_runner.cleanup_complete.connect(
            self._on_manifest_check_runner_cleanup
        )
        self.manifest_check_runner.run(self.manifest_check_task.run)

    def check_single_game_update(self, appid: str) -> None:
        """
        Trigger an update check for a single game by appid.
        Resets its status to 'checking', then runs ManifestCheckTask for just that game.
        Called from the per-game 'Check for Updates' button in the library UI.

        If a batch check is currently running, defers to it — the batch will
        include this game and emit the result when done.
        """
        game = self._games_by_appid.get(appid) or self.get_game(appid)
        if not game:
            logger.warning(f"check_single_game_update: appid {appid} not found")
            return

        # Pinned build bypass
        settings = get_settings()
        if settings.value(f"pin_build/{appid}", False, type=bool):
            logger.info(f"check_single_game_update: appid {appid} is pinned. Bypassing check.")
            game["update_status"] = UPDATE_STATUS["UP_TO_DATE"]
            self.game_update_status_changed.emit(appid, UPDATE_STATUS["UP_TO_DATE"])
            return

        # If a batch is already running, include this game in the batch instead
        if self.manifest_check_task is not None and self.manifest_check_runner is not None:
            logger.info(f"Batch check running — adding {appid} to existing batch")
            game["update_status"] = UPDATE_STATUS["CHECKING"]
            game["hubcap_needs_update"] = False
            game["hubcap_update_in_progress"] = False
            self.game_update_status_changed.emit(appid, UPDATE_STATUS["CHECKING"])
            if game not in self._games_to_check:
                self._games_to_check.append(game)
            return

        # Reset status so the UI shows spinner
        game["update_status"] = UPDATE_STATUS["CHECKING"]
        game["hubcap_needs_update"] = False
        game["hubcap_update_in_progress"] = False
        self.game_update_status_changed.emit(appid, UPDATE_STATUS["CHECKING"])

        task = ManifestCheckTask([game])
        runner = TaskRunner()

        def _on_checked(checked_appid, status):
            self._on_game_update_checked(checked_appid, status)
            if status == UPDATE_STATUS["UPDATE_AVAILABLE"]:
                self._check_hubcap_status_async(checked_appid)

        def _on_done():
            # Clean up this runner from the list
            self._single_check_runners[:] = [
                r for r in self._single_check_runners if r is not runner
            ]

        task.game_update_checked.connect(_on_checked)
        task.completed.connect(_on_done)
        task.error.connect(self._on_update_check_error)

        self._single_check_runners.append(runner)
        runner.run(task.run)
        logger.info(f"Single update check started for appid {appid}")

    def _check_hubcap_status_async(self, appid: str) -> None:
        """Fetch Hubcap status asynchronously and update game dict/UI."""
        from core import morrenus_api

        runner = TaskRunner()
        self._single_check_runners.append(runner)

        def _fetch_status():
            try:
                res = morrenus_api.get_manifest_status(appid)
                return res
            except Exception as e:
                logger.error(f"Error fetching Hubcap status for {appid}: {e}")
                return {"error": str(e)}

        def _on_status_done(result):
            # Clean up runner
            self._single_check_runners[:] = [
                r for r in self._single_check_runners if r is not runner
            ]
            if not result or "error" in result:
                logger.warning(f"Failed to get Hubcap status for {appid}: {result.get('error') if result else 'empty'}")
                return

            needs_update = result.get("needs_update", False)
            update_in_progress = result.get("update_in_progress", False)

            is_refined = False
            if is_refined and not needs_update and not update_in_progress:
                from utils.manifest_verifier import verify_hubcap_freshness
                ver_status, reason, _ = verify_hubcap_freshness(appid, result)
                if ver_status == "stale":
                    needs_update = True  # Override flag so UI indicates Hubcap is not ready

            game = self._games_by_appid.get(appid)
            if game:
                game["hubcap_needs_update"] = needs_update
                game["hubcap_update_in_progress"] = update_in_progress

            self.game_hubcap_status_checked.emit(appid, needs_update, update_in_progress)

        worker = runner.run(_fetch_status)
        worker.finished.connect(_on_status_done)

    def _on_game_update_checked(self, appid: str, update_status: str) -> None:
        """Handle individual game update check result — O(1) via _games_by_appid dict."""
        game = self._games_by_appid.get(appid)
        if game is None:
            # Fallback to linear search for safety (e.g. dict not yet populated)
            for g in self.games:
                if g.get("appid") == appid:
                    game = g
                    break

        if game is not None:
            old_status = game.get("update_status")
            game["update_status"] = update_status
            game_title = game.get("name", f"AppID {appid}")
            if update_status == UPDATE_STATUS["CANNOT_DETERMINE"]:
                logger.info(f"Updated status for game {appid} ({game_title}): {update_status}")
            else:
                logger.debug(f"Updated status for game {appid} ({game_title}): {update_status}")
            self.game_update_status_changed.emit(appid, update_status)

            # Persist to disk cache with diagnostic metadata
            diag_meta = self._build_diag_metadata(appid, update_status)
            get_update_cache().set_status(appid, update_status, diag_meta)
            get_update_cache().save_async()

            # If an update is detected, invalidate the manifest freshness cache
            if update_status == UPDATE_STATUS["UPDATE_AVAILABLE"]:
                self.settings.setValue(f"manifest_is_fresh/{appid}", False)
            elif update_status == UPDATE_STATUS["UP_TO_DATE"]:
                self.settings.remove(f"manifest_is_fresh/{appid}")
                self.settings.remove(f"fetched_manifest_id/{appid}")
                self.settings.remove(f"latest_steam_manifest_id/{appid}")

    def _build_diag_metadata(self, appid: str, status: str) -> dict:
        """Build diagnostic metadata for cache entry from the last check context."""
        meta = {}
        meta["branch"] = str(self.settings.value(f"last_checked_branch/{appid}", "public"))
        meta["branch_buildid"] = str(self.settings.value(f"last_checked_branch_buildid/{appid}", ""))
        meta["local_buildid"] = str(self.settings.value(f"last_checked_local_buildid/{appid}", ""))

        if status == UPDATE_STATUS["UPDATE_AVAILABLE"]:
            meta["reason"] = "depot_manifest_mismatch"
            dep_diff = {}
            i = 0
            while True:
                dk = f"last_check_depot_diff/{appid}/{i}"
                diff_str = self.settings.value(dk, "", type=str)
                if not diff_str:
                    break
                parts = diff_str.split("|", 2)
                if len(parts) >= 2:
                    dep_diff[parts[0]] = {"saved": parts[1], "current": parts[2] if len(parts) > 2 else ""}
                i += 1
            if dep_diff:
                meta["depot_diffs"] = dep_diff
        elif status == UPDATE_STATUS["UP_TO_DATE"]:
            meta["reason"] = "manifests_match"

        return meta

    def _on_update_check_progress(self, current, total):
        """Handle update check progress"""
        logger.debug(f"Update check progress: {current}/{total}")
        self.update_check_progress.emit(current, total)

    def _on_update_check_completed(self):
        """Handle update check completion"""
        logger.info("All game updates checked")
        self.all_updates_checked.emit()
        # Note: references cleared by _on_manifest_check_runner_cleanup when thread finishes

    @staticmethod
    def _on_update_check_error(error_info):
        """Handle update check error"""
        exc_type, exc_msg, exc_traceback = error_info
        logger.error(
            f"Error during update check: {exc_msg}",
            exc_info=(exc_type, exc_msg, exc_traceback),
        )

    def _on_manifest_check_runner_cleanup(self):
        """Handle TaskRunner cleanup completion - called when thread finishes"""
        logger.debug("TaskRunner cleanup complete, clearing references")
        self.manifest_check_task = None
        self.manifest_check_runner = None
        self._games_to_check = []

    def scan_steam_libraries_async(self):
        """
        Start an async scan of Steam library directories for installed Steam games.
        The UI will update automatically when the scan completes via signals.
        """
        logger.info("Starting async scan of Steam libraries for installed Steam games...")

        # Reset cancel flag and set scanning state
        self._scan_cancelled = False
        self.is_scanning = True

        # Create a worker function that does the scanning
        def do_scan():
            return self._perform_scan()

        # Use TaskRunner to run in background thread
        self.scan_runner = TaskRunner()
        self.scan_runner.run(do_scan)

    def cancel_scan(self):
        """Cancel any in-progress library scan."""
        self._scan_cancelled = True
        if self.scan_runner is not None:
            try:
                self.scan_runner.stop(wait_ms=0, terminate_on_timeout=False)
            except Exception as e:
                logger.debug(f"Error stopping scan runner: {e}")
            self.scan_runner = None

    def _perform_scan(self):
        """
        Internal method that performs the actual scan.
        Returns the number of games found.
        """
        steam_libraries = get_steam_libraries()

        if not steam_libraries:
            logger.warning("No Steam libraries found")
            return 0

        logger.info(f"Found {len(steam_libraries)} Steam library location(s)")

        games_found = 0
        scanned_libraries = 0

        # Cache the main Steam installation path to avoid repeated lookups
        steam_install_path = find_steam_install()

        # Build a GLOBAL ACF cache across ALL Steam libraries so that a game
        # installed in one library (e.g. external drive) whose ACF manifest sits
        # in another library (e.g. internal) can still be resolved correctly.
        # This is the common case for DLC-only installs: the DLC files land on
        # the external drive but appmanifest_XXXX.acf stays on the internal one.
        global_acf_cache = {}
        for lib in steam_libraries:
            sp = os.path.join(lib, "steamapps")
            if os.path.exists(sp):
                partial = self._build_acf_cache(sp)
                # Merge — first library wins on collision (preserves earlier match)
                for k, v in partial.items():
                    if k not in global_acf_cache:
                        global_acf_cache[k] = v
        logger.debug(f"Built global ACF cache with {len(global_acf_cache)} entries across {len(steam_libraries)} library(ies)")

        # Thread-safe local list to collect scanned games
        scanned_games = []

        for library_path in steam_libraries:
            if self._scan_cancelled:
                logger.info("Scan cancelled before scanning remaining libraries")
                break
            logger.info(f"Scanning library: {library_path}")
            scanned_libraries += 1

            games_found += self._scan_library(library_path, steam_install_path, scanned_games, global_acf_cache=global_acf_cache)

        accela_games_found = sum(
            1 for game in scanned_games if game.get("is_accela_install")
        )
        logger.info(
            "Scan complete. Scanned %s library location(s), found %s installed Steam "
            "game(s) (%s ACCELA-managed).",
            scanned_libraries,
            games_found,
            accela_games_found,
        )
        # Sort games after scanning
        self.games = self._get_sorted_games(scanned_games)
        self.filtered_games.clear()
        self._apply_filters()

        # Rebuild O(1) appid lookup dict after scan
        self._games_by_appid = {
            g["appid"]: g for g in self.games if g.get("appid") not in ("0", "N/A", "unknown", None)
        }
        logger.debug(f"Rebuilt _games_by_appid with {len(self._games_by_appid)} entries")

        # Fix SLSsteam config indentation if needed (before syncing)
        self._fix_slssteam_config()

        # Sync games to SLSsteam config if integration is enabled
        self._sync_games_to_slssteam_config()

        # Sync missing apptokens from manifests
        self._sync_app_tokens_from_manifests()

        # Emit signals on the main thread via QMetaObject.invokeMethod.
        # QTimer.singleShot called from a background thread does NOT schedule
        # on the main event loop — it silently fires on the worker thread's
        # (non-existent) loop. invokeMethod with QueuedConnection is the correct
        # cross-thread signal dispatch mechanism.
        from PyQt6.QtCore import QMetaObject, Qt as _Qt
        QMetaObject.invokeMethod(
            self,
            "_emit_scan_signals",
            _Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, games_found),
        )

        return games_found

    def _scan_library(self, library_path, steam_install_path, scanned_games, global_acf_cache=None):
        """Scan a single Steam library for games."""
        games_found = 0
        steamapps_path = os.path.join(library_path, "steamapps")
        if not os.path.exists(steamapps_path):
            logger.warning(f"Steamapps directory not found at: {steamapps_path}")
            return 0

        common_path = os.path.join(steamapps_path, "common")
        if not os.path.exists(common_path):
            logger.warning(f"Common directory not found at: {common_path}")
            return 0

        # Use the pre-built global ACF cache (covers all libraries) if available,
        # otherwise fall back to building a local cache for just this library.
        acf_cache = global_acf_cache if global_acf_cache is not None else self._build_acf_cache(steamapps_path)
        logger.debug(f"Using ACF cache with {len(acf_cache)} entries for {library_path}")

        seen_paths = {game.get("install_path") for game in scanned_games}

        # Scan all installed Steam game directories in this library.
        try:
            # Use scandir for better error handling during concurrent modifications
            with os.scandir(common_path) as entries:
                for entry in entries:
                    if self._scan_cancelled:
                        logger.info("Scan cancelled during library scan")
                        break
                    try:
                        if not entry.is_dir():
                            continue

                        game_name = entry.name
                        game_path = entry.path

                        if game_path in seen_paths:
                            continue

                        if not self._has_game_content(game_path):
                            logger.debug(f"  Skipped empty game folder: {game_name}")
                            continue

                        marker_path = self._get_accela_marker_path(game_path)
                        if not marker_path:
                            logger.debug(f"  Skipped non-ACCELA game: {game_name}")
                            continue

                        game_data = self._collect_game_data(
                            game_path,
                            game_name,
                            library_path,
                            steam_install_path,
                            marker_path=marker_path,
                            acf_cache=acf_cache,
                        )
                        if game_data:
                            scanned_games.append(game_data)
                            seen_paths.add(game_path)
                            games_found += 1
                            logger.debug(
                                "  Found %s game: %s",
                                "ACCELA" if marker_path else "Steam",
                                game_name,
                            )
                    except (OSError, FileNotFoundError, PermissionError):
                        # Skip entries that can't be accessed
                        continue

        except OSError as e:
            logger.error(f"Error scanning {common_path}: {e}")

        return games_found

    @staticmethod
    def _build_acf_cache(steamapps_path):
        """
        Scan steamapps/ once and build a dict mapping installdir (lowercased) to
        (manifest_path, appid). This replaces the per-game ACF scan loop and
        reduces total file reads from O(N×M) to O(M).
        """
        cache = {}  # { installdir_lower: (manifest_path, appid) }
        try:
            with os.scandir(steamapps_path) as entries:
                for entry in entries:
                    try:
                        if not (entry.name.startswith("appmanifest_") and entry.name.endswith(".acf")):
                            continue
                        manifest_path = entry.path
                        appid = entry.name.replace("appmanifest_", "").replace(".acf", "")
                        with open(manifest_path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        match = re.search(r'"installdir"\s+"([^"]+)"', content)
                        if match:
                            installdir = match.group(1)
                            # Store both case-sensitive and lower-cased keys
                            cache[installdir] = (manifest_path, appid)
                            cache[installdir.lower()] = (manifest_path, appid)
                    except (OSError, IOError, PermissionError):
                        continue
        except OSError as e:
            logger.debug(f"Error building ACF cache for {steamapps_path}: {e}")
        return cache

    @staticmethod
    def _has_game_content(game_path):
        """
        Check if the game folder has actual content beyond ACCELA marker folders.
        Returns True if there are files or folders other than the marker folders.
        """
        try:
            # Common names to ignore (case-insensitive)
            ignore_names = {".accela", ".depotdownloader", "desktop.ini", "thumbs.db"}

            with os.scandir(game_path) as entries:
                for entry in entries:
                    try:
                        name = entry.name
                        lname = name.lower()

                        # Skip ACCELA marker folders (case-insensitive)
                        # Skip typical OS metadata files and any hidden file (starts with '.')
                        if lname in ignore_names or name.startswith("."):
                            continue

                        # If we find any file or directory that is not ignored, treat it as content
                        if entry.is_file() or entry.is_dir():
                            return True
                    except (OSError, FileNotFoundError, PermissionError):
                        # Skip entries that can't be accessed
                        continue

            return False
        except OSError:
            return False

    @staticmethod
    def _get_accela_marker_path(game_path):
        """Return the ACCELA marker folder path for a game, if present."""
        for marker_name in (".ACCELA", ".DepotDownloader"):
            marker_path = os.path.join(game_path, marker_name)
            if os.path.exists(marker_path):
                return marker_path
        return None

    @staticmethod
    def _fix_slssteam_config():
        """
        Fix indentation of AdditionalApps entries in SLSsteam config.yaml.
        This runs automatically after a scan completes to fix any misformatted
        entries from older versions of ACCELA.
        """
        config_path = get_user_config_path()
        if config_path.exists():
            fix_slssteam_config_indentation(config_path)

    def _sync_games_to_slssteam_config(self):
        """
        Sync found games to SLSsteam AdditionalApps if integration is enabled.
        This runs automatically after a scan completes.
        """
        if not is_slssteam_mode_enabled():
            return

        # Get config path
        config_path = get_user_config_path()
        if not config_path.exists():
            logger.debug("SLSsteam config.yaml not found, skipping sync")
            return

        # Add each game's AppID to AdditionalApps (or DLC AppIDs if dlc_only_mode is enabled)
        from utils.dlc_helpers import sync_dlc_only_sls_config
        added_count = 0
        for game in self.games:
            if not game.get("is_accela_install"):
                continue
            appid = game.get("appid")
            game_name = game.get("game_name", "")
            if appid and appid not in ("0", "N/A", "unknown"):
                if sync_dlc_only_sls_config(config_path, str(appid), game_name):
                    added_count += 1

        if added_count > 0:
            logger.info(f"Synced {added_count} game(s) to SLSsteam AdditionalApps")

    def _sync_app_tokens_from_manifests(self):
        """
        Check all ZIPs in morrenus_manifests for apptokens
        and add any missing tokens to config.yaml.
        Called after game library scan completes.
        """
        if not is_slssteam_mode_enabled():
            return

        # Get paths
        config_path = get_user_config_path()
        if not config_path.exists():
            logger.debug("SLSsteam config.yaml not found, skipping token sync")
            return

        manifests_dir = Path(get_base_path()) / "hubcap_manifests"
        if not manifests_dir.exists():
            logger.debug("hubcap_manifests directory not found")
            return

        # Get existing tokens from config
        existing_tokens = get_app_tokens(config_path)
        logger.debug(f"Found {len(existing_tokens)} existing AppTokens in config")

        # Pattern to extract app_id from filename: accela_fetch_{app_id}.zip
        zip_pattern = re.compile(r"^accela_fetch_(\d+)\.zip$")

        tokens_added = 0
        tokens_skipped = 0

        try:
            for zip_file in manifests_dir.glob("accela_fetch_*.zip"):
                match = zip_pattern.match(zip_file.name)
                if not match:
                    continue

                app_id = match.group(1)

                # Skip if token already exists for this app_id
                if app_id in existing_tokens:
                    tokens_skipped += 1
                    continue

                # Extract token from ZIP
                try:
                    with zipfile.ZipFile(zip_file, "r") as zip_ref:
                        lua_files = [
                            f for f in zip_ref.namelist() if f.endswith(".lua")
                        ]
                        if not lua_files:
                            continue

                        lua_content = zip_ref.read(lua_files[0]).decode("utf-8")

                        # Extract token using the same pattern as ProcessZipTask
                        token_pattern = r'addtoken\s*\(\s*\d+\s*,\s*"([^"]+)"\s*\)'
                        token_match = re.search(
                            token_pattern, lua_content, re.IGNORECASE
                        )

                        if not token_match:
                            continue

                        app_token = token_match.group(1)

                        # Add token to config
                        if add_app_token(config_path, app_id, app_token):
                            tokens_added += 1
                            logger.info(
                                f"Added missing AppToken for AppID {app_id} from {zip_file.name}"
                            )
                        else:
                            tokens_skipped += 1

                except Exception as e:
                    logger.warning(f"Failed to extract token from {zip_file.name}: {e}")
                    continue

        except Exception as e:
            logger.error(
                f"Error scanning morrenus_manifests for tokens: {e}", exc_info=True
            )
            return

        if tokens_added > 0:
            logger.info(
                f"Synced {tokens_added} missing AppToken(s) from morrenus_manifests"
            )
        if tokens_skipped > 0:
            logger.debug(f"Skipped {tokens_skipped} AppToken(s) that already exist")

    def _collect_game_data(
        self,
        game_path,
        game_name,
        library_path,
        steam_path=None,
        marker_path=None,
        acf_cache=None,
    ):
        """
        Collect game data from installation directory.
        Returns a dictionary with game information.
        """
        try:
            if self._scan_cancelled:
                return None

            marker_path = marker_path or self._get_accela_marker_path(game_path)
            is_accela_install = bool(marker_path)

            # Try to read appmanifest to get AppID and other metadata
            appmanifest_path, appid = self._parse_acf_for_appid(library_path, game_name, acf_cache=acf_cache)

            # Warn if AppID could not be determined
            if not appid:
                logger.warning(
                    f"FAILED to determine AppID for '{game_name}'. Game will have AppID='0' (unknown). This may happen if the ACF file's installdir doesn't match the folder name exactly."
                )

            # Initialize game data dictionary early so we can populate it
            # Determine install directory name
            install_dir = game_name

            game_data = {
                "appid": appid or "0",
                "game_name": game_name,
                "install_dir": install_dir,
                "install_path": game_path,
                "library_path": library_path,
                "library_index": get_library_index(library_path, steam_path),
                "size_on_disk": 0,  # Will be calculated below
                "source": "ACCELA" if is_accela_install else "Steam",
                "is_accela_install": is_accela_install,
                "depot_downloader_path": marker_path or "",
                "accela_marker_path": marker_path or "",
                "appmanifest_path": appmanifest_path or "",
            }

            # Detect DLC-only installations by comparing the saved main depot against base game depots
            if is_accela_install and appid and appid not in ("0", "N/A", "unknown"):
                depot_file = Path(get_base_path()) / "depots" / f"{appid}.depot"
                if depot_file.exists():
                    try:
                        content = depot_file.read_text().strip()
                        parts = content.split(":", 2)
                        if parts and parts[0].strip():
                            main_depot_id = parts[0].strip()
                            from managers.db_manager import DatabaseManager
                            db = DatabaseManager()
                            app_info = db.get_app_info(appid)
                            if app_info and app_info.get("depots"):
                                if main_depot_id not in app_info["depots"]:
                                    # It's not in the base game depots -> it's a DLC
                                    game_data["is_dlc_only"] = True
                                    dlc_info = db.get_app_info(main_depot_id)
                                    if dlc_info and dlc_info.get("name"):
                                        game_data["game_name"] = f"{dlc_info['name']} [DLC]"
                                    else:
                                        game_data["game_name"] = f"{game_name} [DLC]"
                    except Exception as e:
                        logger.error(f"Error checking DLC-only status for {appid}: {e}")

            # Load persisted wrapper metadata (selected DLC IDs) for uninstall cleanup.
            if is_accela_install:
                persisted_selected_dlcs = load_selected_dlcs(game_path)
                if persisted_selected_dlcs:
                    game_data["selected_dlcs"] = persisted_selected_dlcs
                    # Keep compatibility with existing cleanup path that expects a dlcs mapping.
                    game_data["dlcs"] = {
                        dlc_id: "" for dlc_id in persisted_selected_dlcs
                    }
                elif os.name == "nt" and appid and appid not in ("0", "N/A", "unknown"):
                    # Best-effort migration for older installs without persisted DLC metadata.
                    inferred_selected_dlcs = (
                        self._infer_selected_dlcs_from_applist_and_manifest(appid)
                    )
                    if inferred_selected_dlcs:
                        game_data["selected_dlcs"] = inferred_selected_dlcs
                        game_data["dlcs"] = {
                            dlc_id: "" for dlc_id in inferred_selected_dlcs
                        }
                        if persist_selected_dlcs(game_path, inferred_selected_dlcs):
                            logger.debug(
                                f"Migrated and persisted {len(inferred_selected_dlcs)} DLC ID(s) for AppID {appid}"
                            )

            # Get file size - try ACF first, fall back to manual calculation
            size_on_disk = 0
            acf_size_available = False

            # Check for ACF file data first
            if appmanifest_path and os.path.exists(appmanifest_path):
                acf_size_available = self._parse_acf_for_metadata(
                    appmanifest_path, game_data
                )
                if acf_size_available:
                    size_on_disk = game_data["size_on_disk"]

            # Only calculate size manually if ACF doesn't have a valid SizeOnDisk
            if not acf_size_available:
                logger.debug(
                    f"ACF SizeOnDisk not available, calculating size manually for {game_name}"
                )
                try:
                    for dirpath, dirnames, filenames in os.walk(game_path):
                        if self._scan_cancelled:
                            return None
                        for filename in filenames:
                            if self._scan_cancelled:
                                return None
                            filepath = os.path.join(dirpath, filename)
                            try:
                                # Use lstat to get file size without following symlinks
                                # This avoids issues with broken symlinks
                                if os.path.isfile(filepath) or os.path.islink(filepath):
                                    size_on_disk += os.lstat(filepath).st_size
                            except (OSError, FileNotFoundError, PermissionError):
                                # Skip files that can't be accessed (broken symlinks, permission errors, etc.)
                                pass
                except OSError:
                    pass

            # Update the size in game_data
            game_data["size_on_disk"] = size_on_disk

            # Set update status — restore from disk cache if available
            if appid and appid not in ("0", "N/A", "unknown"):
                cached_status = get_update_cache().get_status(appid)
                if cached_status is not None:
                    # We have a fresh (non-expired) cached status — use it directly
                    game_data["update_status"] = cached_status
                    logger.debug(
                        f"Restored cached update status for {game_name} ({appid}): {cached_status}"
                    )
                else:
                    # No usable cache — needs a live check
                    game_data["update_status"] = UPDATE_STATUS["CHECKING"]
            else:
                game_data["update_status"] = UPDATE_STATUS["CANNOT_DETERMINE"]

            return game_data

        except Exception as e:
            logger.error(
                f"Error collecting game data for {game_name}: {e}", exc_info=True
            )
            return None

    def _parse_acf_for_appid(self, library_path, game_name, acf_cache=None):
        """
        Find the AppID and manifest path for a given game install directory name.

        When acf_cache is provided (built once per library by _build_acf_cache),
        this is an O(1) dict lookup. Without a cache it falls back to the original
        O(M) directory scan so callers outside the main scan loop still work.
        """
        # Fast path: use the pre-built cache (O(1) lookup)
        if acf_cache is not None:
            result = acf_cache.get(game_name) or acf_cache.get(game_name.lower())
            if result:
                appmanifest_path, appid = result
                logger.debug(f"ACF cache hit for '{game_name}': AppID={appid}")
                return appmanifest_path, appid
            logger.debug(f"ACF cache miss for '{game_name}' — no matching manifest found")
            return None, None

        # Slow path fallback: scan directory (used when called without a cache)
        appmanifest_path = None
        appid = None
        steamapps_path = os.path.join(library_path, "steamapps")
        if os.path.exists(steamapps_path):
            logger.debug(f"Looking for ACF match for game (no cache): '{game_name}'")
            try:
                with os.scandir(steamapps_path) as entries:
                    for entry in entries:
                        if self._scan_cancelled:
                            return None, None
                        try:
                            if not (
                                entry.name.startswith("appmanifest_")
                                and entry.name.endswith(".acf")
                            ):
                                continue
                            test_manifest_path = entry.path
                            try:
                                with open(test_manifest_path, "r", encoding="utf-8") as f:
                                    content = f.read()
                                match = re.search(r'"installdir"\s+"([^"]+)"', content)
                                if match:
                                    installdir = match.group(1)
                                    if installdir == game_name or installdir.lower() == game_name.lower():
                                        appmanifest_path = test_manifest_path
                                        appid = entry.name.replace("appmanifest_", "").replace(".acf", "")
                                        logger.debug(f"  ✓ Match found! AppID: {appid}")
                                        break
                            except (OSError, IOError, PermissionError):
                                continue
                        except (OSError, FileNotFoundError, PermissionError):
                            continue
            except OSError as e:
                logger.debug(f"  Error scanning steamapps directory: {e}")

        return appmanifest_path, appid

    @staticmethod
    def _parse_acf_for_metadata(appmanifest_path, game_data):
        """Parse ACF file for metadata like name, buildid, and size."""
        acf_size_available = False
        try:
            with open(appmanifest_path, "r", encoding="utf-8") as f:
                content = f.read()

                # Extract name using regex
                name_match = re.search(r'"name"\s+"([^"]+)"', content)
                if name_match:
                    game_data["game_name"] = name_match.group(1)

                # Extract buildid using regex
                buildid_match = re.search(r'"buildid"\s+"([^"]+)"', content)
                if buildid_match:
                    game_data["buildid"] = buildid_match.group(1)

                # Extract LastUpdated using regex
                lastupdated_match = re.search(r'"LastUpdated"\s+"([^"]+)"', content)
                if lastupdated_match:
                    game_data["last_updated"] = lastupdated_match.group(1)

                # Extract SizeOnDisk using regex (only use if non-zero)
                sizeon_disk_match = re.search(r'"SizeOnDisk"\s+"([^"]+)"', content)
                if sizeon_disk_match:
                    acf_size = int(sizeon_disk_match.group(1))
                    # Only use ACF size if it's greater than 0
                    if acf_size > 0:
                        game_data["size_on_disk"] = acf_size
                        acf_size_available = True
                        logger.debug(
                            f"Using ACF SizeOnDisk for {game_data['game_name']}: {acf_size} bytes"
                        )
        except Exception as e:
            logger.debug(f"Could not parse ACF file {appmanifest_path}: {e}")

        return acf_size_available

    def clear_library(self):
        """Clear all games from the library"""
        logger.info("Clearing entire game library")
        self.games.clear()
        self.filtered_games.clear()
        self._games_by_appid.clear()
        self.selected_game = None
        self.library_updated.emit()

    @staticmethod
    def import_library(file_path):
        """Import library from a file"""
        # TODO: Implement library import
        logger.info(f"Importing library from: {file_path}")
        return False

    def get_library_stats(self):
        """Get statistics about the game library"""
        total_games = len(self.games)
        total_size = sum(game.get("size_on_disk", 0) for game in self.games)

        return {
            "total_games": total_games,
            "total_size": total_size,
            "filtered_count": len(self.filtered_games),
        }

    def cleanup(self):
        """Clean up GameManager resources"""
        logger.info("Cleaning up GameManager")

        # Flush any unsaved cache entries to disk before exit
        try:
            get_update_cache().save()
        except Exception as e:
            logger.warning(f"Failed to save update status cache on cleanup: {e}")

        # Stop any running manifest check task
        self.cancel_update_checks()

        # Stop any running scan
        self._scan_cancelled = True
        if self.scan_runner is not None:
            try:
                self.scan_runner.stop(wait_ms=0, terminate_on_timeout=False)
            except Exception as e:
                logger.debug(f"Error stopping scan runner during cleanup: {e}")
            self.scan_runner = None

        self.games.clear()
        self.filtered_games.clear()
        self._games_by_appid.clear()
        self.selected_game = None
        self._games_to_check = []

    def cancel_update_checks(self):
        """Cancel any in-progress update checks and clean up task/runner references."""
        if self.manifest_check_task is not None:
            try:
                self.manifest_check_task.stop()
            except Exception as e:
                logger.debug(f"Error stopping manifest check task: {e}")

        if self.manifest_check_runner is not None:
            try:
                self.manifest_check_runner.stop(wait_ms=0, terminate_on_timeout=False)
            except Exception as e:
                logger.debug(f"Error stopping manifest check runner: {e}")

        self.manifest_check_task = None
        self.manifest_check_runner = None
        self._games_to_check = []

    def get_uninstall_confirmation_message(self, game_data):
        """
        Build a confirmation message for uninstalling a game.
        Returns a string with the confirmation message.
        """
        game_name = game_data.get("game_name", "Unknown")
        install_path = game_data.get("install_path")
        appid = game_data.get("appid", "0")

        import os
        import platform
        from core.steam_helpers import find_steam_install, get_steam_libraries

        is_accela_install = game_data.get("is_accela_install", False)

        is_dlc_only = False
        if appid and appid not in ("0", "N/A", "unknown"):
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc_only = is_dlc_only_mode(str(appid))

        if is_dlc_only:
            from utils.dlc_helpers import get_dlc_uninstall_message
            return get_dlc_uninstall_message(game_data)

        confirm_msg = f"Are you sure you want to uninstall '{game_name}'?\n\n"

        # Warn if appid is unknown
        if not appid or appid in ("0", "N/A", "unknown"):
            confirm_msg += "⚠️ WARNING: AppID is unknown for this game.\n"
            if platform.system() == "Linux":
                confirm_msg += "Compatdata and saves WILL NOT be removed.\n"
            elif platform.system() == "Windows" and is_accela_install:
                confirm_msg += "GreenLuma AppList files WILL NOT be removed.\n"
            confirm_msg += "\n"

        confirm_msg += "This will permanently delete:\n"
        confirm_msg += f"• Game folder: {install_path}\n"

        # Only show ACF removal if appid is valid
        if appid and appid not in ("0", "N/A", "unknown"):
            confirm_msg += f"• Steam app manifest ({appid}.acf)\n"

        # Check for additional items that would be removed
        if (
            platform.system() == "Linux"
            and appid
            and appid not in ("0", "N/A", "unknown")
        ):
            steam_libraries = get_steam_libraries()
            if steam_libraries:
                steam_dir = steam_libraries[0]
                compatdata_path = os.path.join(
                    steam_dir, "steamapps", "compatdata", appid
                )
                userdata_path = os.path.join(steam_dir, "userdata")

                # Check if compatdata exists
                if os.path.exists(compatdata_path):
                    confirm_msg += (
                        f"• Proton/Wine compatibility data: {compatdata_path}\n"
                    )

                # Check if userdata exists
                if os.path.exists(userdata_path):
                    has_saves = False
                    try:
                        for user_dir in os.listdir(userdata_path):
                            user_path = os.path.join(userdata_path, user_dir)
                            if os.path.isdir(user_path):
                                saves_path = os.path.join(user_path, appid, "remote")
                                if os.path.exists(saves_path):
                                    has_saves = True
                                    break
                    except OSError:
                        pass

                    if has_saves:
                        confirm_msg += "• Steam Cloud saves from userdata folders\n"
        elif (
            platform.system() == "Windows"
            and is_accela_install
            and appid
            and appid not in ("0", "N/A", "unknown")
        ):
            wrapper_mode_enabled = is_greenluma_wrapper_mode_enabled()
            if not wrapper_mode_enabled:
                confirm_msg += (
                    "• GreenLuma AppList cleanup skipped (Wrapper Mode is disabled)\n"
                )
            else:
                steam_path = find_steam_install()
                if steam_path:
                    app_list_dir = os.path.join(steam_path, "AppList")
                    if os.path.exists(app_list_dir):
                        try:
                            appid_str = str(appid)
                            dlc_ids = self._collect_known_dlc_ids(game_data)
                            if not dlc_ids:
                                dlc_ids = (
                                    self._infer_selected_dlcs_from_applist_and_manifest(
                                        appid_str
                                    )
                                )

                            app_ids_to_check = [appid_str, *dlc_ids]
                            files_by_id = self._find_applist_files_for_ids(
                                app_list_dir, app_ids_to_check
                            )

                            found_appid_files = files_by_id.get(appid_str, [])
                            found_dlc_files = []
                            for dlc_id in dlc_ids:
                                found_dlc_files.extend(files_by_id.get(str(dlc_id), []))

                            if found_appid_files:
                                confirm_msg += (
                                    "• GreenLuma AppList main file(s): "
                                    f"{', '.join(found_appid_files)}\n"
                                )
                            if found_dlc_files:
                                confirm_msg += (
                                    "• GreenLuma AppList DLC file(s): "
                                    f"{', '.join(found_dlc_files)}\n"
                                )
                        except OSError:
                            pass

        confirm_msg += "\nThis action cannot be undone!"
        return confirm_msg

    def _delete_single_dlc_depot(self, install_path, base_appid, dlc_appid, manifest_id):
        """Uses DepotDownloader to get files list for DLC depot and deletes only those files."""
        import subprocess
        import tempfile
        from utils.paths import Paths
        from utils.helpers import get_dotnet_path, get_dotnet_env
        
        depotdownloader_path = Paths.deps("depot-downloader/DepotDownloader.dll")
        if not depotdownloader_path.exists():
            logger.warning("DepotDownloader.dll not found, cannot delete DLC files")
            return
            
        dotnet = get_dotnet_path()
        if not dotnet:
            logger.warning(".NET runtime not found, cannot delete DLC files")
            return
            
        with tempfile.TemporaryDirectory() as temp_dir:
            cmd = [
                str(dotnet),
                str(depotdownloader_path),
                "-app", str(base_appid),
                "-depot", str(dlc_appid),
                "-manifest", str(manifest_id),
                "-manifest-only",
                "-dir", temp_dir
            ]
            try:
                env = get_dotnet_env()
                subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)
                txt_path = Path(temp_dir) / f"manifest_{dlc_appid}_{manifest_id}.txt"
                if txt_path.exists():
                    start_parsing = False
                    files_deleted = 0
                    dirs_to_check = set()
                    
                    with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line_stripped = line.strip()
                            if not line_stripped:
                                continue
                            if "Size Chunks File SHA" in line_stripped:
                                start_parsing = True
                                continue
                            if not start_parsing:
                                continue
                            
                            parts = line_stripped.split(None, 4)
                            if len(parts) >= 5:
                                name = parts[4].strip()
                                file_path = Path(install_path) / name
                                if file_path.exists() and file_path.is_file():
                                    try:
                                        file_path.unlink()
                                        files_deleted += 1
                                        dirs_to_check.add(file_path.parent)
                                    except OSError as e:
                                        logger.warning(f"Failed to delete DLC file {file_path}: {e}")
                                        
                    logger.info(f"Deleted {files_deleted} DLC files for depot {dlc_appid}")
                    
                    # Clean up empty subdirectories
                    sorted_dirs = sorted(list(dirs_to_check), key=lambda x: len(x.parts), reverse=True)
                    for d in sorted_dirs:
                        if d.exists() and d.is_dir() and not os.listdir(d):
                            try:
                                d.rmdir()
                            except OSError:
                                pass
            except Exception as e:
                logger.error(f"Failed to fetch manifest and delete files for DLC {dlc_appid}: {e}")

    def _delete_dlc_depot_files(self, install_path, appid):
        """Finds and deletes files for all DLC depots listed in appid.depot."""
        from utils.helpers import get_base_path
        depot_file = Path(get_base_path()) / "depots" / f"{appid}.depot"
        if not depot_file.exists():
            return

        try:
            lines = depot_file.read_text().splitlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(":")
                if len(parts) >= 2:
                    dlc_appid = parts[0].strip()
                    manifest_id = parts[1].strip()
                    self._delete_single_dlc_depot(install_path, appid, dlc_appid, manifest_id)
        except Exception as e:
            logger.error(f"Failed to read/delete files from depot config: {e}")

    def uninstall_game(
        self,
        game_data,
        remove_compatdata=False,
        remove_saves=False,
        remove_from_library=False,
        remove_shortcuts=False,
        remove_sls=False,
    ):
        """
        Uninstall a game by removing its folder (or DLC files if dlc_only), ACF file, and optionally compatdata/saves.
        Returns (success: bool, error_message: str)
        """
        game_name = game_data.get("game_name", "Unknown")
        install_path = game_data.get("install_path")
        library_path = game_data.get("library_path")
        appid = game_data.get("appid", "0")

        import os
        import platform

        try:
            is_dlc_only = False
            if appid and appid not in ("0", "N/A", "unknown"):
                from utils.dlc_helpers import is_dlc_only_mode
                is_dlc_only = is_dlc_only_mode(str(appid))

            if is_dlc_only:
                # 1. Delete only files belonging to the DLC depots
                if install_path and os.path.exists(install_path):
                    self._delete_dlc_depot_files(install_path, appid)
                    # Clean up empty install folder if all DLC files were removed
                    try:
                        if os.path.isdir(install_path) and not os.listdir(install_path):
                            import shutil
                            shutil.rmtree(install_path)
                            logger.info(f"Removed empty game folder after DLC uninstall: {install_path}")
                    except Exception as _e:
                        logger.warning(f"Could not remove empty folder {install_path}: {_e}")
            else:
                # Remove full game folder
                if install_path and os.path.exists(install_path):
                    import shutil
                    shutil.rmtree(install_path)
                    logger.info(f"Removed game folder: {install_path}")

                # Remove ACF file
                if library_path and appid != "N/A":
                    acf_path = os.path.join(
                        library_path, "steamapps", f"appmanifest_{appid}.acf"
                    )
                    if os.path.exists(acf_path):
                        os.remove(acf_path)
                        logger.info(f"Removed ACF file: {acf_path}")

            # Clean up .DepotDownloader folder if remove_sls is True and the folder is not already removed
            if remove_sls and install_path and os.path.exists(install_path):
                dd_path = os.path.join(install_path, ".DepotDownloader")
                if os.path.exists(dd_path):
                    try:
                        import shutil
                        shutil.rmtree(dd_path)
                        logger.info(f"Removed .DepotDownloader folder: {dd_path}")
                    except Exception as e:
                        logger.warning(f"Could not remove .DepotDownloader folder: {e}")

            # Remove depot file
            if (
                game_data.get("is_accela_install")
                and appid
                and appid not in ("0", "N/A", "unknown")
            ):
                try:
                    depot_file = Path(get_base_path()) / "depots" / f"{appid}.depot"
                    if depot_file.exists():
                        depot_file.unlink()
                        logger.info(f"Removed depot file: {depot_file}")
                except Exception as e:
                    logger.warning(
                        f"Failed to remove depot file for appid {appid}: {e}"
                    )

            # Remove platform-specific data (only for full uninstalls)
            if platform.system() == "Linux" and not is_dlc_only:
                self._remove_linux_game_data(appid, remove_compatdata, remove_saves)

                # Remove shortcuts only if explicitly requested
                if remove_shortcuts:
                    self._remove_linux_shortcuts_and_icons(appid)

            # Clean up appid from SLSsteam config.yaml
            if appid and appid not in ("0", "N/A", "unknown"):
                config_path = get_user_config_path()
                if config_path.exists():
                    if is_dlc_only:
                        # Remove all DLC entries matching the depots config
                        depot_file = Path(get_base_path()) / "depots" / f"{appid}.depot"
                        if depot_file.exists():
                            try:
                                for line in depot_file.read_text().splitlines():
                                    parts = line.split(":")
                                    if parts and parts[0].strip():
                                        remove_additional_app(config_path, str(parts[0].strip()))
                            except Exception:
                                pass
                    else:
                        remove_additional_app(config_path, str(appid))
                    logger.info(f"Removed appid entries from SLS config")
            elif platform.system() == "Windows" and not is_dlc_only:
                self._remove_windows_game_data(appid, game_data)

            # Remove from game manager list
            self.remove_game(appid)

            return True, None

        except Exception as e:
            error_msg = f"Error uninstalling game {game_name}: {e}"
            logger.error(error_msg)
            return False, str(e)

    @staticmethod
    def _remove_linux_game_data(appid, remove_compatdata, remove_saves):
        """
        Remove Linux-specific game data (compatdata and Steam Cloud saves).
        """
        import os

        from core.steam_helpers import get_steam_libraries

        # CRITICAL SAFETY CHECK: Never remove compatdata/saves for invalid appids
        if not appid or appid in ("0", "N/A", "unknown"):
            logger.warning(
                f"Skipping compatdata/saves removal for invalid appid: {appid}"
            )
            return

        # Validate appid is numeric
        if not str(appid).isdigit():
            logger.error(
                f"Invalid appid format: {appid}. Must be numeric. Skipping compatdata/saves removal."
            )
            return

        steam_libraries = get_steam_libraries()
        if not steam_libraries:
            return

        # Use the first (primary) Steam library
        steam_dir = steam_libraries[0]

        # Remove compatdata
        if remove_compatdata:
            compatdata_path = os.path.join(steam_dir, "steamapps", "compatdata", appid)
            if os.path.exists(compatdata_path):
                try:
                    import shutil

                    shutil.rmtree(compatdata_path)
                    logger.info(f"Removed compatdata: {compatdata_path}")
                except Exception as e:
                    logger.warning(
                        f"Failed to remove compatdata {compatdata_path}: {e}"
                    )

        # Remove Steam Cloud saves
        if remove_saves:
            userdata_path = os.path.join(steam_dir, "userdata")
            if os.path.exists(userdata_path):
                try:
                    # Find all user directories
                    for user_dir in os.listdir(userdata_path):
                        user_path = os.path.join(userdata_path, user_dir)
                        if os.path.isdir(user_path):
                            saves_path = os.path.join(user_path, appid, "remote")
                            if os.path.exists(saves_path):
                                import shutil

                                shutil.rmtree(saves_path)
                                logger.info(
                                    f"Removed saves for user {user_dir}: {saves_path}"
                                )
                except Exception as e:
                    logger.warning(f"Failed to remove saves: {e}")

    @staticmethod
    def _remove_linux_shortcuts_and_icons(appid):
        """
        Remove Linux desktop shortcuts and icons created by ApplicationShortcutsTask.
        """
        import os
        from pathlib import Path

        # CRITICAL SAFETY CHECK: Never remove shortcuts/icons for invalid appids
        if not appid or appid in ("0", "N/A", "unknown"):
            logger.warning(
                f"Skipping shortcuts/icons removal for invalid appid: {appid}"
            )
            return

        # Validate appid is numeric
        if not str(appid).isdigit():
            logger.error(
                f"Invalid appid format: {appid}. Must be numeric. Skipping shortcuts/icons removal."
            )
            return

        try:
            # Remove desktop entry
            desktop_dir = Path.home() / ".local" / "share" / "applications"
            if desktop_dir.exists():
                # Look for desktop files that contain the appid in the Exec line
                desktop_files_removed = 0
                for desktop_file in desktop_dir.glob("*.desktop"):
                    try:
                        with open(desktop_file, "r", encoding="utf-8") as f:
                            content = f.read()
                            if f"steam://rungameid/{appid}" in content:
                                os.remove(desktop_file)
                                logger.info(f"Removed desktop entry: {desktop_file}")
                                desktop_files_removed += 1
                    except OSError as e:
                        logger.warning(
                            f"Error reading desktop file {desktop_file}: {e}"
                        )

                if desktop_files_removed == 0:
                    logger.info(f"No desktop entries found for AppID {appid}")

            # Remove icons
            icon_base = Path.home() / ".local" / "share" / "icons" / "hicolor"
            if icon_base.exists():
                icon_name = f"steam_icon_{appid}.png"
                icons_removed = 0

                # Remove icons from all size directories
                for size_dir in icon_base.glob("*x*"):
                    if size_dir.is_dir():
                        apps_dir = size_dir / "apps"
                        if apps_dir.exists():
                            icon_path = apps_dir / icon_name
                            if icon_path.exists():
                                try:
                                    os.remove(icon_path)
                                    logger.info(f"Removed icon: {icon_path}")
                                    icons_removed += 1
                                except OSError as e:
                                    logger.warning(
                                        f"Failed to remove icon {icon_path}: {e}"
                                    )

                if icons_removed == 0:
                    logger.info(f"No icons found for AppID {appid}")

        except OSError as e:
            logger.error(
                f"Failed to remove Linux shortcuts and icons for AppID {appid}: {e}"
            )

    def _remove_windows_game_data(self, appid, game_data):
        """
        Remove Windows-specific game data (GreenLuma AppList files).
        """
        import os

        from core.steam_helpers import find_steam_install

        if not game_data.get("is_accela_install"):
            logger.debug("Skipping GreenLuma cleanup for non-ACCELA install")
            return

        # AppList cleanup on Windows should only run when GreenLuma wrapper mode is enabled.
        if not is_greenluma_wrapper_mode_enabled():
            logger.debug(
                "GreenLuma wrapper mode is disabled, skipping AppList cleanup"
            )
            return

        # CRITICAL SAFETY CHECK: Never remove AppList files for invalid appids
        if not appid or appid in ("0", "N/A", "unknown"):
            logger.warning(
                f"Skipping GreenLuma AppList cleanup for invalid appid: {appid}"
            )
            return

        # Validate appid is numeric
        if not str(appid).isdigit():
            logger.error(
                f"Invalid appid format: {appid}. Must be numeric. Skipping GreenLuma cleanup."
            )
            return

        # Find Steam installation path
        steam_path = find_steam_install()
        if not steam_path:
            logger.warning(
                "Could not find Steam installation path. Skipping GreenLuma AppList cleanup."
            )
            return

        # Locate AppList directory
        app_list_dir = os.path.join(steam_path, "AppList")
        if not os.path.exists(app_list_dir):
            logger.info(
                "AppList directory does not exist. No GreenLuma files to clean up."
            )
            return

        logger.info(f"Scanning GreenLuma AppList directory: {app_list_dir}")

        self._find_and_delete_greenluma_files(app_list_dir, appid)
        self._remove_dlc_files(app_list_dir, game_data, appid)

    @staticmethod
    def _find_and_delete_greenluma_files(app_list_dir, appid):
        """Find and delete GreenLuma files for the given AppID."""
        # Step 1: Find all .txt files that contain this appid
        files_to_delete = []
        all_files_data = []  # List of tuples (filename, filepath, appid_content)

        try:
            for filename in os.listdir(app_list_dir):
                if filename.lower().endswith(".txt"):
                    filepath = os.path.join(app_list_dir, filename)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            content = f.read().strip()
                            # Store all files for later renumbering
                            all_files_data.append((filename, filepath, content))

                            # Check if this file contains our appid
                            if content == str(appid):
                                files_to_delete.append((filename, filepath))
                                logger.info(
                                    f"Found GreenLuma file to delete: {filename} (contains AppID {appid})"
                                )
                    except OSError as e:
                        logger.warning(f"Error reading AppList file {filepath}: {e}")
        except OSError as e:
            logger.error(f"Error scanning AppList directory {app_list_dir}: {e}")
            return

        # Step 2: Delete files containing this appid
        for filename_to_delete, filepath in files_to_delete:
            try:
                os.remove(filepath)
                logger.info(f"Deleted GreenLuma file: {filepath}")
            except OSError as e:
                logger.warning(f"Failed to delete GreenLuma file {filepath}: {e}")

        # Step 3: Renumber remaining files to maintain sequential numbering
        # Build list of remaining files (those that don't contain our appid)
        remaining_files = [
            (fname, fpath, fcontent)
            for fname, fpath, fcontent in all_files_data
            if fpath not in [f[1] for f in files_to_delete]
        ]

        # Sort remaining files by their current number
        def extract_number(fname):
            match = re.match(r"^(\d+)\.txt$", fname)
            return int(match.group(1)) if match else 0

        remaining_files.sort(key=lambda x: extract_number(x[0]))

        # Renumber all remaining files sequentially starting from 0
        for index, (old_filename, old_filepath, content) in enumerate(remaining_files):
            new_filename = f"{index}.txt"
            new_filepath = os.path.join(app_list_dir, new_filename)

            # Only rename if the filename will change
            if old_filename != new_filename:
                try:
                    os.rename(old_filepath, new_filepath)
                    logger.debug(
                        f"Renamed GreenLuma file: {old_filename} -> {new_filename}"
                    )
                except OSError as e:
                    logger.warning(
                        f"Failed to rename {old_filename} to {new_filename}: {e}"
                    )

        logger.info(
            f"GreenLuma AppList cleanup complete. Removed {len(files_to_delete)} file(s)."
        )

    @staticmethod
    def _collect_known_dlc_ids(game_data):
        """Collect DLC IDs from in-memory game data fields."""
        selected_dlcs = game_data.get("selected_dlcs") or []
        dlc_map = game_data.get("dlcs", {})
        if not isinstance(dlc_map, dict):
            dlc_map = {}

        dlc_ids = []
        seen = set()

        for dlc_id in selected_dlcs:
            dlc_id_str = str(dlc_id).strip()
            if not dlc_id_str or dlc_id_str in seen:
                continue
            seen.add(dlc_id_str)
            dlc_ids.append(dlc_id_str)

        for dlc_id in dlc_map:
            dlc_id_str = str(dlc_id).strip()
            if not dlc_id_str or dlc_id_str in seen:
                continue
            seen.add(dlc_id_str)
            dlc_ids.append(dlc_id_str)

        return dlc_ids

    @staticmethod
    def _find_applist_files_for_ids(app_list_dir, app_ids):
        """Return a mapping {appid: [filename, ...]} for matching AppList .txt files."""
        target_ids = {str(app_id).strip() for app_id in app_ids if str(app_id).strip()}
        files_by_id = {app_id: [] for app_id in target_ids}
        if not target_ids:
            return files_by_id

        for filename in os.listdir(app_list_dir):
            if not filename.lower().endswith(".txt"):
                continue

            filepath = os.path.join(app_list_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as file_handle:
                    content = file_handle.read().strip()
                if content in files_by_id:
                    files_by_id[content].append(filename)
            except OSError:
                continue

        for file_list in files_by_id.values():
            file_list.sort()

        return files_by_id

    def _read_manifest_dlc_ids(self, appid):
        """
        Best-effort read of DLC IDs from the local manifest ZIP for the given appid.
        Returns [] if no usable manifest is available.
        """
        appid_str = str(appid).strip()
        if not appid_str.isdigit():
            return []

        manifests_dir = Path(get_base_path()) / "hubcap_manifests"
        manifest_zip = manifests_dir / f"accela_fetch_{appid_str}.zip"
        if not manifest_zip.exists():
            return []

        try:
            with zipfile.ZipFile(manifest_zip, "r") as zip_ref:
                lua_files = [
                    name for name in zip_ref.namelist() if name.endswith(".lua")
                ]
                if not lua_files:
                    return []

                lua_content = zip_ref.read(lua_files[0]).decode(
                    "utf-8", errors="ignore"
                )
        except (OSError, zipfile.BadZipFile, RuntimeError):
            return []

        app_matches = list(
            re.finditer(r"addappid\((.*?)\)(.*)", lua_content, re.IGNORECASE)
        )
        if len(app_matches) < 2:
            return []

        first_args = [arg.strip() for arg in app_matches[0].group(1).strip().split(",")]
        if not first_args:
            return []

        first_appid = first_args[0].strip('"')
        if first_appid != appid_str:
            return []

        dlc_ids = []
        seen = set()
        for match in app_matches[1:]:
            args = [arg.strip() for arg in match.group(1).strip().split(",")]
            if not args:
                continue

            candidate_id = args[0].strip('"')
            has_depot_key = len(args) > 2 and bool(args[2].strip('"'))
            if has_depot_key:
                continue
            if not candidate_id.isdigit():
                continue
            if candidate_id in seen:
                continue

            seen.add(candidate_id)
            dlc_ids.append(candidate_id)

        return dlc_ids

    def _infer_selected_dlcs_from_applist_and_manifest(self, appid):
        """
        Best-effort inference for old installs:
        intersect local manifest DLC IDs with IDs currently present in AppList.
        """
        appid_str = str(appid).strip()
        if not appid_str.isdigit():
            return []

        steam_path = find_steam_install()
        if not steam_path:
            return []

        app_list_dir = os.path.join(steam_path, "AppList")
        if not os.path.exists(app_list_dir):
            return []

        try:
            files_by_id = self._find_applist_files_for_ids(app_list_dir, [appid_str])
            if not files_by_id.get(appid_str):
                return []
        except OSError:
            return []

        manifest_dlc_ids = self._read_manifest_dlc_ids(appid_str)
        if not manifest_dlc_ids:
            return []

        try:
            files_by_id = self._find_applist_files_for_ids(
                app_list_dir, manifest_dlc_ids
            )
        except OSError:
            return []

        return [dlc_id for dlc_id in manifest_dlc_ids if files_by_id.get(str(dlc_id))]

    def _remove_dlc_files(self, app_list_dir, game_data, appid):
        """Remove DLC files from the AppList directory."""
        dlc_ids = self._collect_known_dlc_ids(game_data)
        if not dlc_ids:
            dlc_ids = self._infer_selected_dlcs_from_applist_and_manifest(appid)

        if not dlc_ids:
            return

        logger.info(f"Removing {len(dlc_ids)} DLC files from AppList directory.")
        for dlc_id in dlc_ids:
            self._find_and_delete_greenluma_files(app_list_dir, dlc_id)
