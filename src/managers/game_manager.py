"""Core GameManager class composed from domain-specific mixins."""

import logging
import os
import threading
import weakref

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from core.steam_helpers import (
    get_steam_libraries,
    get_library_index,
    find_steam_install,
)
from managers.game_manager_mixins import (
    GameManagerConfigSyncMixin,
    GameManagerScanningMixin,
    GameManagerUninstallMixin,
    GameManagerUpdatesMixin,
    UPDATE_STATUS,
)
from utils.helpers import get_base_path
from utils.lua_parsing import is_placeholder_key, iter_live_matches
from utils.settings import get_settings
from utils.task_runner import TaskRunner
from utils.update_status_cache import get_update_cache
from utils.wrapper_metadata import load_selected_dlcs, persist_selected_dlcs
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

logger = logging.getLogger(__name__)


class GameManager(
    QObject,
    GameManagerScanningMixin,
    GameManagerUpdatesMixin,
    GameManagerUninstallMixin,
    GameManagerConfigSyncMixin,
):
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
    # Emitted when the whole update check is over: the manifest batch AND the
    # AT0-M / DLC stage that follows it.
    update_checks_finished = pyqtSignal()
    update_check_started = pyqtSignal()  # Emitted when a batch update check starts
    game_hubcap_status_checked = pyqtSignal(
        str, bool, bool
    )  # (appid, needs_update, update_in_progress)
    update_check_progress = pyqtSignal(int, int)  # (current, total)

    @property
    def main_window(self):
        """Weakly referenced MainWindow instance to prevent reference cycles."""
        return self._main_window_ref() if self._main_window_ref is not None else None

    @main_window.setter
    def main_window(self, value) -> None:
        self._main_window_ref = weakref.ref(value) if value is not None else None

    @pyqtSlot()
    def _emit_library_updated(self) -> None:
        """Main-thread slot: emit library_updated safely from worker threads."""
        self.library_updated.emit()

    @pyqtSlot(int)
    def _emit_scan_signals(self, games_found: int) -> None:
        """Main-thread slot: emit library_updated and scan_complete after a background scan."""
        self.is_scanning = False
        self.library_updated.emit()
        self.scan_complete.emit(games_found)

    def __init__(self, main_window=None):
        super().__init__()
        self._main_window_ref = (
            weakref.ref(main_window) if main_window is not None else None
        )
        self.settings = (
            main_window.settings
            if main_window and hasattr(main_window, "settings") and main_window.settings
            else get_settings()
        )

        # Game library data
        self.games = []
        self.selected_game = None
        self.filtered_games = []

        # O(1) lookup: appid -> game dict reference
        self._games_by_appid: dict = {}

        # Pending size calculations & SizeCalculator concurrency state
        self._pending_size_calculations = []
        self._size_calc_lock = threading.Lock()
        self._size_calc_thread = None
        self._size_calc_generation = 0
        self._size_calc_queue = []

        # Disk-backed games cache for instant boot and cached sizes
        from utils.games_cache import get_games_cache

        self.games_cache = get_games_cache()
        cached_games = self.games_cache.get_games()
        if cached_games:
            self.games = self._get_sorted_games(cached_games)
            self._games_by_appid = {
                g["appid"]: g
                for g in self.games
                if g.get("appid") not in ("0", "N/A", "unknown", None)
            }
            logger.info("Loaded %d games from games cache on startup", len(self.games))

        # Manifest check task management
        self.manifest_check_task = None
        self.manifest_check_runner = None
        self._games_to_check = []
        self._runners_lock = threading.Lock()
        self._single_check_runners: list = []

        # Library scan task management
        self.scan_runner = None
        self._scan_cancelled = False
        self._scan_generation = 0
        self.is_scanning = False

        logger.info("GameManager initialized")

    @staticmethod
    def _get_sorted_games(games_list):
        """Helper method to sort games by name (case-insensitive)."""
        return sorted(games_list, key=lambda x: x.get("game_name", "").lower())

    def add_game(self, game_data):
        """Add a game to the library."""
        logger.info(f"Adding game to library: {game_data.get('game_name', 'Unknown')}")
        self.games.append(game_data)
        game_id = str(game_data.get("appid", ""))
        if game_id:
            self._games_by_appid[game_id] = game_data
        self.games = self._get_sorted_games(self.games)
        self._apply_filters()
        if hasattr(self, "games_cache"):
            self.games_cache.save(self.games)
        self.library_updated.emit()

    def remove_game(self, game_id):
        """Remove a game from the library."""
        logger.info(f"Removing game from library: {game_id}")
        self.games = [g for g in self.games if str(g.get("appid")) != str(game_id)]
        if str(game_id) in self._games_by_appid:
            del self._games_by_appid[str(game_id)]
        self.games = self._get_sorted_games(self.games)
        self._apply_filters()
        if hasattr(self, "games_cache"):
            self.games_cache.save(self.games)
        self.library_updated.emit()

    def get_game(self, game_id):
        """Get a specific game by ID (O(1) lookup)."""
        return self._games_by_appid.get(str(game_id))

    def get_all_games(self):
        """Get all games in the library - returns sorted list."""
        games_to_return = self.filtered_games if self.filtered_games else self.games
        return list(games_to_return)

    def select_game(self, game_id):
        """Select a specific game."""
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
        """Update game information."""
        logger.info(f"Updating game: {game_id}")
        for i, game in enumerate(self.games):
            if game.get("appid") == game_id:
                self.games[i].update(game_data)
                self._games_by_appid[str(game_id)] = self.games[i]
                self.games = self._get_sorted_games(self.games)
                self.game_updated.emit(game_id)
                self._apply_filters()
                if hasattr(self, "games_cache"):
                    self.games_cache.save(self.games)
                self.library_updated.emit()
                return True
        return False

    def _apply_filters(self):
        """Apply current filters to the game list."""
        self.filtered_games = self._get_sorted_games(self.games)

    def search_games(self, query):
        """Search games by name or other criteria."""
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
        """Clear all applied filters."""
        self.filtered_games = []
        self._apply_filters()
        self.library_updated.emit()

    def clear_library(self):
        """Clear all games from the library."""
        logger.info("Clearing entire game library")
        self.games.clear()
        self.filtered_games.clear()
        self._games_by_appid.clear()
        self.selected_game = None
        self.library_updated.emit()

    @staticmethod
    def import_library(file_path):
        """Import library from a file."""
        logger.info(f"Importing library from: {file_path}")
        return False

    def get_library_stats(self):
        """Get statistics about the game library."""
        total_games = len(self.games)
        total_size = sum(game.get("size_on_disk", 0) for game in self.games)

        return {
            "total_games": total_games,
            "total_size": total_size,
            "filtered_count": len(self.filtered_games),
        }

    def cleanup(self):
        """Clean up GameManager resources."""
        logger.info("Cleaning up GameManager")

        try:
            get_update_cache().save()
        except Exception as e:
            logger.warning(f"Failed to save update status cache on cleanup: {e}")

        self.cancel_update_checks()
        self.cancel_scan()

        self.games.clear()
        self.filtered_games.clear()
        self._games_by_appid.clear()
        self.selected_game = None
        self._games_to_check = []
        self._main_window_ref = None