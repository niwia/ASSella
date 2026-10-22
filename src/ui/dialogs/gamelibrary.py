import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from utils.paths import Paths

# Re-exports for 100% backward compatibility
from ui.dialogs.library.widgets import (
    format_game_display_name,
    format_size,
    ElidedLabel,
    BlurredHeaderWidget,
    GameItemWidget,
    GameTileWidget,
    GameCompactWidget,
)
from ui.dialogs.library.scanner import LibraryScannerMixin
from ui.dialogs.library.filter_sort import LibraryFilterSortMixin
from ui.dialogs.library.image_loader import LibraryImageLoaderMixin
from ui.dialogs.library.actions import LibraryActionsMixin
from ui.dialogs.library.tab_bar import (
    LibraryTabBar,
    TAB_ACCELA,
    TAB_ATOM,
    TAB_STEAM,
)
from ui.dialogs.library.steam_tab_cache import load_steam_cache, save_steam_cache
from ui.dialogs.library.acf_scanner import scan_acf_files

logger = logging.getLogger(__name__)


class GameLibraryDialog(
    QDialog,
    LibraryScannerMixin,
    LibraryFilterSortMixin,
    LibraryImageLoaderMixin,
    LibraryActionsMixin,
):
    """Dialog to display and manage the game library."""

    goldberg_check_complete = pyqtSignal(bool)  # is_applied
    manifest_download_complete = pyqtSignal(str, str, dict)  # fpath, error, game_data
    uninstall_complete = pyqtSignal(bool, str)  # success, error_message
    zip_parse_complete = pyqtSignal(object, str, dict, object, object)  # parsed_data, filepath, game_data, dialog, parse_progress
    hubcap_status_check_complete = pyqtSignal(dict, dict, object, object)  # result, game_data, dialog, check_progress
    steam_scan_complete = pyqtSignal(list)  # games list

    # Backward compatible static methods
    _format_size = staticmethod(format_size)
    _is_goldberg_applied = staticmethod(LibraryActionsMixin._is_goldberg_applied)
    _wipe_sls_only = staticmethod(LibraryActionsMixin._wipe_sls_only)
    _open_folder = staticmethod(LibraryActionsMixin._open_folder)
    _resolve_appid_by_name = staticmethod(LibraryFilterSortMixin._resolve_appid_by_name)
    _get_sort_key = staticmethod(LibraryFilterSortMixin._get_sort_key)
    _current_pinned_cache = None

    def __init__(self, main_window, show_details_for_appid=None):
        super().__init__(main_window)
        self.main_window = main_window
        self.game_manager = getattr(main_window, "game_manager", None)
        self.settings = getattr(main_window, "settings", None)
        if not self.settings:
            from utils.settings import get_settings
            self.settings = get_settings()
        self.executor = ThreadPoolExecutor(max_workers=4)

        # Load theme colors
        self.accent_color = "#a1c9fd"
        self.background_color = "#111318"

        if self.settings:
            self.accent_color = self.settings.value("accent_color", "#a1c9fd")
            self.background_color = self.settings.value("background_color", "#111318")
        self.applist_2_0_enabled = True

        # Search debounce timer
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._refresh_game_list)

        # State tracking
        self._active_fetchers = {}
        self._image_fetch_queue = deque()
        self._max_concurrent_fetches = 5
        self._current_fetches = 0
        self._image_cache = {}
        self._items_by_appid = {}
        self._manifest_mtimes = {}
        self._pending_image_fetches = []
        self._dialog_open = False
        self._refreshing = False
        self._closing = False
        self._scanning = False
        self._checking_updates = False
        self._download_progress_dialog = None
        self._uninstall_progress_dialog = None
        self._details_dialog = None

        # Multi-select state
        self._select_mode = False
        self._selected_appids: set = set()

        # Tab state
        self._active_tab = TAB_ACCELA
        self._steam_tab_games = []
        self._steam_scan_in_progress = False
        self._steam_scanned_this_session = False
        try:
            self._steam_tab_games = load_steam_cache()
        except Exception as e:
            logger.warning(f"Failed to load steam tab cache on startup: {e}")

        # If cache is empty, start background scan immediately
        if not self._steam_tab_games:
            self._trigger_steam_scan()

        self._setup_window()
        self._setup_ui()
        self._connect_signals()

        if self.parent():
            from ui.dialogs.dialog_raiser import DialogRaiser
            DialogRaiser(self.parent(), self)

        # Initial Load
        self._refresh_game_list()

        if show_details_for_appid:
            QTimer.singleShot(0, lambda: self._show_details_for_appid(show_details_for_appid))

    def _setup_window(self) -> None:
        """Configure main window properties and styles."""
        self.setWindowTitle("Game Library")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)
        self.resize(750, 500)

        self.setStyleSheet(
            f"""
            QDialog {{ background-color: {self.background_color}; color: #FFFFFF; border: 1px solid rgba(255, 255, 255, 0.15); }}
            
            QListWidget {{ 
                background-color: {self.background_color}; 
                border: none; 
                padding: 10px 0px;
            }}
            QListWidget::item {{ 
                background-color: rgba(255, 255, 255, 0.03); 
                border: 1px solid transparent; 
                border-radius: 10px;
                margin: 2px 0px;
                color: #FFFFFF;
            }}
            QListWidget::item:hover {{ 
                background-color: rgba(255, 255, 255, 0.08); 
                border: 1px solid rgba(255, 255, 255, 0.1); 
            }}
            QListWidget::item:selected {{ 
                background-color: rgba(255, 255, 255, 0.12); 
                border: 1px solid {self.accent_color}; 
                color: #FFFFFF; 
            }}
            
            QPushButton {{ 
                background-color: {self.accent_color}; 
                color: #000000; 
                border: none; 
                padding: 6px 12px; 
                border-radius: 4px; 
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #FFFFFF; }}
            QPushButton:disabled {{ background-color: #555555; color: #888888; }}
            
            QLineEdit {{ 
                background-color: #1e1e1e; 
                color: #FFFFFF; 
                border: 1px solid #333333; 
                padding: 5px; 
                border-radius: 4px; 
            }}
            
            QComboBox {{ 
                background-color: #1e1e1e; 
                color: #FFFFFF; 
                border: 1px solid #333333; 
                padding: 5px; 
                border-radius: 4px; 
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background-color: #1e1e1e;
                color: #FFFFFF;
                selection-background-color: #222;
                border: none;
            }}
        """
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_selection_fab()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(self, "settings") and self.settings:
            self.accent_color = self.settings.value("accent_color", getattr(self, "accent_color", "#a1c9fd"))
        QTimer.singleShot(50, self._position_selection_fab)

    def update_style(self) -> None:
        """Update colors and icons when theme or accent color changes."""
        if hasattr(self, "settings") and self.settings:
            self.accent_color = self.settings.value("accent_color", getattr(self, "accent_color", "#a1c9fd"))
            self.background_color = self.settings.value("background_color", getattr(self, "background_color", "#000000"))
        self._setup_style()

    def _show_sort_menu(self) -> None:
        """Show minimal dropdown menu for sorting options."""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction
        menu = QMenu(self)
        menu.setStyleSheet(
            f"""
            QMenu {{
                background-color: #1a1a20;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 18px;
                border-radius: 4px;
                font-size: 12px;
                color: #FFFFFF;
            }}
            QMenu::item:selected {{
                background-color: rgba(255, 255, 255, 0.12);
                color: {self.accent_color};
            }}
            """
        )

        current_data = self.sort_combo.currentData() if hasattr(self, "sort_combo") else "pinned_first"
        for i in range(self.sort_combo.count()):
            label = self.sort_combo.itemText(i)
            data = self.sort_combo.itemData(i)
            action = QAction(label, menu)
            action.setCheckable(True)
            if data == current_data:
                action.setChecked(True)
            action.triggered.connect(lambda checked, idx=i, l=label: self._on_sort_menu_item_selected(idx, l))
            menu.addAction(action)

        if hasattr(self, "sort_button") and self.sort_button:
            menu.exec(self.sort_button.mapToGlobal(self.sort_button.rect().bottomLeft()))

    def _on_sort_menu_item_selected(self, index: int, label: str) -> None:
        if hasattr(self, "sort_combo"):
            self.sort_combo.setCurrentIndex(index)
        if hasattr(self, "sort_button"):
            self.sort_button.setText(f"Sort by: {label} ▾")



    def _setup_ui(self) -> None:
        """Create and arrange UI elements."""
        window_layout = QVBoxLayout(self)
        window_layout.setContentsMargins(0, 0, 0, 0)
        window_layout.setSpacing(0)

        from ui.dialog_titlebar import DialogTitleBar
        self.title_bar = DialogTitleBar(self, title="Game Library", can_minimize=True, can_maximize=True)
        window_layout.addWidget(self.title_bar)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(16, 12, 16, 16)
        window_layout.addWidget(content_widget, 1)

        # Hidden compatibility controls (to preserve references used by mixins and tests)
        self.scan_button = QPushButton("Scan Libraries")
        self.scan_button.clicked.connect(self._scan_for_games)
        self.scan_imports_button = QPushButton("Scan for LUA")
        self.scan_imports_button.clicked.connect(self._on_scan_imports_clicked)
        self.select_mode_button = QPushButton("Select")
        self.select_mode_button.setCheckable(True)
        self.select_mode_button.clicked.connect(self._toggle_select_mode)

        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Pinned First", "pinned_first")
        self.sort_combo.addItem("Recently Installed", "recently_installed")
        self.sort_combo.addItem("Has Update First", "update_first")
        self.sort_combo.addItem("DLC Only First", "dlc_only_first")
        self.sort_combo.addItem("Name (A-Z)", "name_asc")
        self.sort_combo.addItem("Name (Z-A)", "name_desc")
        self.sort_combo.addItem("Size (Smallest)", "size_asc")
        self.sort_combo.addItem("Size (Largest)", "size_desc")
        self.sort_combo.addItem("AppID", "appid")

        # Load last saved sort option
        if self.settings:
            saved_sort = self.settings.value("library_sort_option", "pinned_first", type=str)
            idx = self.sort_combo.findData(saved_sort)
            if idx != -1:
                self.sort_combo.setCurrentIndex(idx)

        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)

        # --- Row 1: Centered Search Bar (Pill Shape with View Mode Switcher Button) ---
        search_container = QWidget()
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(0, 0, 0, 10)
        search_layout.setSpacing(0)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search games...")
        self.search_input.setFixedHeight(38)
        self.search_input.setMinimumWidth(320)
        self.search_input.setMaximumWidth(580)
        self.search_input.setStyleSheet(
            f"""
            QLineEdit {{
                background-color: rgba(255, 255, 255, 0.06);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 19px;
                padding: 0px 20px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                background-color: rgba(255, 255, 255, 0.09);
                border: 2px solid {self.accent_color};
                padding: 0px 19px;
            }}
            """
        )
        self.search_input.textChanged.connect(self._on_search_changed)

        search_layout.addStretch(1)
        search_layout.addWidget(self.search_input, 4)
        search_layout.addStretch(1)
        layout.addWidget(search_container)

        # --- Row 2: Tabs (left) & Sort by (right) ---
        self.tab_bar = LibraryTabBar(accent_color=self.accent_color, parent=self)
        self.tab_bar.current_tab_changed.connect(self._on_tab_changed)

        # Minimal borderless "Sort by: [Current Option] ▾" button
        saved_label = self.sort_combo.currentText() or "Pinned First"
        self.sort_button = QPushButton(f"Sort by: {saved_label} ▾")
        self.sort_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sort_button.setStyleSheet(
            f"""
            QPushButton {{
                background-color: transparent;
                color: {self.accent_color};
                border: none;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                color: #FFFFFF;
            }}
            """
        )
        self.sort_button.clicked.connect(self._show_sort_menu)
        self.tab_bar.add_right_widget(self.sort_button)
        layout.addWidget(self.tab_bar)

        # --- Games List ---
        self.games_list = QListWidget()
        self.games_list.setSpacing(2)
        layout.addWidget(self.games_list)

        # --- Persistent Floating Options / Actions Button ---
        self.selection_fab = QPushButton("options", self)
        self.selection_fab.setObjectName("SelectionFab")
        self.selection_fab.setVisible(True)
        self.selection_fab.setCursor(Qt.CursorShape.PointingHandCursor)
        self.selection_fab.setStyleSheet(
            f"""
            QPushButton#SelectionFab {{
                background-color: {self.accent_color};
                color: #000000;
                border: none;
                border-radius: 18px;
                min-height: 36px;
                max-height: 36px;
                padding: 0px 20px;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton#SelectionFab:hover {{
                background-color: #FFFFFF;
                color: #000000;
            }}
            """
        )
        self.selection_fab.clicked.connect(self._on_fab_clicked)

        shadow = QGraphicsDropShadowEffect(self.selection_fab)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 4)
        self.selection_fab.setGraphicsEffect(shadow)

        # Multi-selection actions are handled via right-aligned drop-up QMenu in _show_fab_menu()
        self.fab_menu_container = None
        self.btn_select_all = None
        self.btn_move_atom = None
        self.btn_update = None
        self.btn_uninstall = None
        self.btn_clear = None

        # --- Status Footer (omitted from layout to maximize space for games list) ---
        self.info_label = QLabel("Found 0 installed Steam games")
        self.info_label.setVisible(False)

    def _connect_signals(self) -> None:
        """Connect GameManager and local signals."""
        if not self.game_manager:
            return

        self.game_manager.scan_complete.connect(
            self._on_scan_complete, Qt.ConnectionType.UniqueConnection
        )
        self.game_manager.library_updated.connect(
            self._refresh_game_list, Qt.ConnectionType.UniqueConnection
        )
        self.game_manager.game_update_status_changed.connect(
            self._on_game_update_status_changed, Qt.ConnectionType.UniqueConnection
        )
        self.game_manager.all_updates_checked.connect(
            self._on_all_updates_checked, Qt.ConnectionType.UniqueConnection
        )

        self.games_list.itemClicked.connect(self._on_item_selected)
        self.games_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.games_list.customContextMenuRequested.connect(self._show_games_list_context_menu)
        self.goldberg_check_complete.connect(self._on_goldberg_check_complete)
        self.manifest_download_complete.connect(self._on_manifest_download_complete)
        self.uninstall_complete.connect(self._on_uninstall_complete)
        self.zip_parse_complete.connect(self._on_zip_parse_complete)
        self.hubcap_status_check_complete.connect(self._on_hubcap_status_check_complete)
        self.steam_scan_complete.connect(self._on_steam_scan_complete)

    @pyqtSlot(object)
    def _run_on_main_thread(self, fn) -> None:
        """Slot to execute a callable on the main thread (used by background threads)."""
        fn()

    def _on_tab_changed(self, tab: str) -> None:
        self._active_tab = tab
        if tab == TAB_STEAM:
            if not self._steam_scanned_this_session or not self._steam_tab_games:
                self._trigger_steam_scan()
        self._refresh_game_list()

    def _trigger_steam_scan(self) -> None:
        if getattr(self, "_steam_scan_in_progress", False) or getattr(self, "_closing", False):
            return
        self._steam_scan_in_progress = True
        if hasattr(self, "tab_bar"):
            self.tab_bar.set_scanning(TAB_STEAM, True)
        if getattr(self, "_active_tab", "") == TAB_STEAM and not self._steam_tab_games:
            self.info_label.setText("Scanning Steam libraries for installed games... Please wait.")
        self.executor.submit(self._bg_scan_steam_games)

    def _bg_scan_steam_games(self) -> None:
        try:
            games = scan_acf_files()
            if games:
                save_steam_cache(games)
            self._steam_scanned_this_session = True
            # Safely emit cross-thread signal to GUI thread
            self.steam_scan_complete.emit(games)
        except Exception as e:
            logger.error(f"[GameLibrary] Steam ACF scan error: {e}")
            self.steam_scan_complete.emit([])

    def _on_steam_scan_complete(self, games: list) -> None:
        self._steam_scan_in_progress = False
        if hasattr(self, "tab_bar"):
            self.tab_bar.set_scanning(TAB_STEAM, False)
        if getattr(self, "_closing", False):
            return
        if games:
            self._steam_tab_games = games
        if hasattr(self, "tab_bar"):
            self.tab_bar.update_counts(steam=len(self._steam_tab_games))
        if getattr(self, "_active_tab", "") == TAB_STEAM:
            self._refresh_game_list()

    def _show_game_details_dialog(self, game_data: dict) -> None:
        try:
            from ui.dialogs.gamelibrary_v2 import GameDetailsDialogV2
            dialog = GameDetailsDialogV2(self, game_data)
            self._details_dialog = dialog
            dialog.exec()
            self._details_dialog = None
        except Exception as e:
            logger.error(f"Failed to load Game Details V2: {e}", exc_info=True)

    def _show_details_for_appid(self, appid: str) -> None:
        appid_str = str(appid).strip()
        game_data = None
        if self.game_manager:
            game_data = self.game_manager.get_game(appid_str)
        if not game_data:
            from utils.plugin_games import get_plugin_game
            game_data = get_plugin_game(appid_str)
        if not game_data:
            # Fallback to local SQLite DatabaseManager & history cache for uninstalled games
            try:
                from managers.db_manager import DatabaseManager
                db_info = DatabaseManager().get_app_info(appid_str, bypass_expiration=True)
                if db_info and isinstance(db_info, dict):
                    game_data = {
                        "appid": appid_str,
                        "game_name": db_info.get("name") or f"App {appid_str}",
                        "header_url": db_info.get("header_url"),
                        "installdir": db_info.get("installdir", ""),
                        "depots": db_info.get("depots", {}),
                        "buildid": db_info.get("buildid", ""),
                        "branches": db_info.get("branches", {}),
                        "is_installed": False,
                        "installed": False,
                        "install_path": None,
                        "source": "database",
                    }
            except Exception as e:
                logger.debug(f"[GameLibrary] Database lookup fallback for {appid_str}: {e}")

        if not game_data:
            # Check recent history cache for game name
            try:
                from utils.history_cache import get_history_cache
                for h_entry in get_history_cache().get_history():
                    if str(h_entry.get("appid")) == appid_str:
                        game_data = {
                            "appid": appid_str,
                            "game_name": h_entry.get("game_name") or f"App {appid_str}",
                            "is_installed": False,
                            "installed": False,
                            "install_path": None,
                            "source": "history",
                        }
                        break
            except Exception as e:
                logger.debug(f"[GameLibrary] History lookup fallback for {appid_str}: {e}")

        if not game_data:
            game_data = {"appid": appid_str, "game_name": f"App {appid_str}", "is_installed": False, "installed": False}

        self._show_game_details_dialog(game_data)

    def closeEvent(self, event) -> None:
        """Cleanup resources on close."""
        self._closing = True
        for fetcher in list(self._active_fetchers.values()):
            try:
                fetcher.stop()
            except Exception:
                pass
        self._active_fetchers.clear()
        self.executor.shutdown(wait=False)
        super().closeEvent(event)
