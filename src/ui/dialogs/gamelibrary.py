import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
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

# Re-exports for 100% backward compatibility
from ui.dialogs.library.widgets import (
    format_game_display_name,
    format_size,
    ElidedLabel,
    BlurredHeaderWidget,
    GameItemWidget,
)
from ui.dialogs.library.scanner import LibraryScannerMixin
from ui.dialogs.library.filter_sort import LibraryFilterSortMixin
from ui.dialogs.library.image_loader import LibraryImageLoaderMixin
from ui.dialogs.library.actions import LibraryActionsMixin

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
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)
        self.resize(750, 500)

        self.setStyleSheet(
            f"""
            QDialog {{ background-color: {self.background_color}; color: #FFFFFF; }}
            
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

    def _setup_ui(self) -> None:
        """Create and arrange UI elements."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        applist_2_0_enabled = self.applist_2_0_enabled

        # --- Top Bar ---
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)

        self.scan_button = QPushButton("Scan Libraries")
        self.scan_button.clicked.connect(self._scan_for_games)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search games..." if applist_2_0_enabled else "Filter games...")
        self.search_input.textChanged.connect(self._on_search_changed)

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
            saved_sort = self.settings.value("library_sort_option", "recently_installed", type=str)
            idx = self.sort_combo.findData(saved_sort)
            if idx != -1:
                self.sort_combo.setCurrentIndex(idx)

        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)

        self.select_mode_button = QPushButton("Select")
        self.select_mode_button.setCheckable(True)
        self.select_mode_button.setFixedWidth(80)
        self.select_mode_button.clicked.connect(self._toggle_select_mode)

        if applist_2_0_enabled:
            self.search_input.setFixedWidth(220)
            self.search_input.setFixedHeight(36)
            self.search_input.setStyleSheet(
                f"""
                QLineEdit {{
                    background-color: rgba(255, 255, 255, 0.05);
                    color: #FFFFFF;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 18px;
                    padding: 0px 16px;
                    font-size: 12px;
                }}
                QLineEdit:focus {{
                    border: 2px solid {self.accent_color};
                    background-color: rgba(255, 255, 255, 0.08);
                    padding: 0px 15px;
                }}
                """
            )
            self.sort_combo.setFixedHeight(36)
            self.sort_combo.setStyleSheet(
                f"""
                QComboBox {{
                    background-color: rgba(255, 255, 255, 0.05);
                    color: #FFFFFF;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 18px;
                    padding: 0px 16px;
                    font-size: 12px;
                }}
                QComboBox:hover {{
                    background-color: rgba(255, 255, 255, 0.08);
                    border-color: rgba(255, 255, 255, 0.2);
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 0px;
                }}
                QComboBox QAbstractItemView {{
                    background-color: {self.background_color};
                    color: #FFFFFF;
                    selection-background-color: rgba(255, 255, 255, 0.12);
                    selection-color: {self.accent_color};
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 8px;
                    padding: 4px;
                }}
                """
            )
            self.select_mode_button.setFixedWidth(85)
            self.select_mode_button.setFixedHeight(36)
            self.select_mode_button.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.05);
                    color: #FFFFFF;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 18px;
                    padding: 0px 16px;
                    font-size: 12px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.08);
                    border-color: rgba(255, 255, 255, 0.2);
                }}
                QPushButton:checked {{
                    background-color: {self.accent_color};
                    color: #000000;
                    border: none;
                }}
                """
            )
            top_layout.addWidget(self.search_input)
            top_layout.addStretch()

            # Scan Imports button (Scan for user-provided LUA files)
            self.scan_imports_button = QPushButton("Scan for LUA")
            self.scan_imports_button.setToolTip(
                "Scan for user-provided .lua files in the cached_luas folder "
                "that are not yet registered in the library"
            )
            self.scan_imports_button.setFixedHeight(36)
            self.scan_imports_button.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.05);
                    color: #FFFFFF;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 18px;
                    padding: 0px 16px;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.08);
                    border-color: rgba(255, 255, 255, 0.2);
                }}
                QPushButton:pressed {{
                    background-color: {self.accent_color};
                    color: #000000;
                    border: none;
                }}
                """
            )
            self.scan_imports_button.clicked.connect(self._on_scan_imports_clicked)
            top_layout.addWidget(self.scan_imports_button)

            top_layout.addWidget(QLabel("Sort by:"))
            top_layout.addWidget(self.sort_combo)
            top_layout.addWidget(self.select_mode_button)
        else:
            self.search_input.setFixedWidth(150)
            top_layout.addWidget(self.scan_button)
            top_layout.addStretch()
            top_layout.addWidget(QLabel("Search:"))
            top_layout.addWidget(self.search_input)
            top_layout.addWidget(QLabel("Sort by:"))
            top_layout.addWidget(self.sort_combo)
            top_layout.addWidget(self.select_mode_button)

        layout.addLayout(top_layout)

        # --- Games List ---
        self.games_list = QListWidget()
        self.games_list.setSpacing(2)
        self.games_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.games_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.games_list)

        # --- Selection FAB (floating button) ---
        self.selection_fab = QPushButton("Actions  ▼", self)
        self.selection_fab.setVisible(False)
        self.selection_fab.setCursor(Qt.CursorShape.PointingHandCursor)
        self.selection_fab.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: #000000;
                border: none;
                border-radius: 18px;
                padding: 8px 16px;
                font-size: 9.5pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #FFFFFF;
                color: #000000;
            }}
            """
        )
        self.selection_fab.clicked.connect(self._show_fab_menu)

        shadow = QGraphicsDropShadowEffect(self.selection_fab)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 4)
        self.selection_fab.setGraphicsEffect(shadow)

        # Setup custom Material Floating Action Button Menu container
        self.fab_menu_container = QWidget(self)
        self.fab_menu_container.setVisible(False)
        self.fab_menu_container.setStyleSheet("background: transparent;")
        
        menu_layout = QVBoxLayout(self.fab_menu_container)
        menu_layout.setContentsMargins(0, 0, 0, 0)
        menu_layout.setSpacing(8)
        
        # Action 1: Update Selected
        row_update = QHBoxLayout()
        row_update.setSpacing(8)
        lbl_update = QLabel("Update Selected")
        lbl_update.setStyleSheet("background-color: #1a1a20; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 4px; padding: 4px 8px; color: #FFFFFF; font-size: 8.5pt;")
        btn_update = QPushButton("↻")
        btn_update.setFixedSize(32, 32)
        btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_update.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: #000000;
                border: none;
                border-radius: 16px;
                font-size: 11pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #FFFFFF;
                color: #000000;
            }}
        """)
        btn_update.clicked.connect(self._on_update_action_clicked)
        row_update.addWidget(lbl_update)
        row_update.addWidget(btn_update)
        menu_layout.addLayout(row_update)
        
        # Action 2: Uninstall Selected
        row_uninstall = QHBoxLayout()
        row_uninstall.setSpacing(8)
        lbl_uninstall = QLabel("Uninstall Selected")
        lbl_uninstall.setStyleSheet("background-color: #1a1a20; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 4px; padding: 4px 8px; color: #FFFFFF; font-size: 8.5pt;")
        btn_uninstall = QPushButton("🗑")
        btn_uninstall.setFixedSize(32, 32)
        btn_uninstall.setCursor(Qt.CursorShape.PointingHandCursor)
        
        from utils.color_utils import get_semantic_colors
        sem_colors = get_semantic_colors(self.accent_color)
        err_color = sem_colors["error"]
        
        btn_uninstall.setStyleSheet(f"""
            QPushButton {{
                background-color: {err_color};
                color: #FFFFFF;
                border: none;
                border-radius: 16px;
                font-size: 10pt;
            }}
            QPushButton:hover {{
                background-color: #FFFFFF;
                color: {err_color};
            }}
        """)
        btn_uninstall.clicked.connect(self._on_uninstall_action_clicked)
        row_uninstall.addWidget(lbl_uninstall)
        row_uninstall.addWidget(btn_uninstall)
        menu_layout.addLayout(row_uninstall)

        # --- Status Footer ---
        self.info_label = QLabel("Found 0 installed Steam games")
        layout.addWidget(self.info_label)

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
        self.games_list.customContextMenuRequested.connect(self._show_games_list_context_menu)
        self.goldberg_check_complete.connect(self._on_goldberg_check_complete)
        self.manifest_download_complete.connect(self._on_manifest_download_complete)
        self.uninstall_complete.connect(self._on_uninstall_complete)
        self.zip_parse_complete.connect(self._on_zip_parse_complete)
        self.hubcap_status_check_complete.connect(self._on_hubcap_status_check_complete)

    @pyqtSlot(object)
    def _run_on_main_thread(self, fn) -> None:
        """Slot to execute a callable on the main thread (used by background threads)."""
        fn()

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
        if not self.game_manager:
            return
        game_data = self.game_manager.get_game(appid)
        if game_data:
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
