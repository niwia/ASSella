import logging
import math
import os
import platform
import subprocess
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer, QMetaObject, Q_ARG, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QIntValidator, QPixmap, QPainter, QBrush, QLinearGradient, QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGraphicsBlurEffect,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# --- Import Handling with Fallbacks ---
try:
    from core import morrenus_api
except ImportError:
    morrenus_api = None

try:
    from core.steam_helpers import slssteam_api_send
except ImportError:

    def slssteam_api_send(_cmd):
        return None


try:
    from utils.helpers import get_base_path
except ImportError:

    def get_base_path():
        return Path(".")


try:
    from managers.image_fetcher import ImageFetcher
except ImportError:
    try:
        from utils.image_fetcher import ImageFetcher
    except ImportError:
        ImageFetcher = None

try:
    from managers.db_manager import DatabaseManager
except ImportError:
    DatabaseManager = None

try:
    from utils.yaml_config_manager import (
        add_fake_app_id,
        get_fake_app_ids,
        get_fake_appid,
        get_user_config_path,
        is_slssteam_config_management_enabled,
        is_slssteam_mode_enabled,
        remove_fake_app_id,
    )
except ImportError:
    # Dummy fallbacks to prevent crash if module is missing
    def add_fake_app_id(*_args, **_kwargs):
        return False

    def get_fake_app_ids(*_args, **_kwargs):
        return []

    def get_fake_appid(*_args, **_kwargs):
        return None

    def get_user_config_path():
        return Path("config.yaml")

    def is_slssteam_config_management_enabled():
        return False

    def is_slssteam_mode_enabled():
        return False

    def remove_fake_app_id(*_args, **_kwargs):
        return False


logger = logging.getLogger(__name__)


def format_game_display_name(game_data: dict) -> str:
    """Return the display name for a game, with branch suffix for non-public branches."""
    name = game_data.get("game_name", "Unknown")
    appid = str(game_data.get("appid", ""))
    parts = [name]
    if appid and appid not in ("0", "N/A", "unknown"):
        from utils.dlc_helpers import is_dlc_only_mode
        if is_dlc_only_mode(appid):
            parts.append("[DLC MODE]")
        from utils.settings import get_settings
        branch = get_settings().value(f"installed_branch/{appid}", "public", type=str)
        if branch and branch != "public":
            parts.append(f"({branch})")
    return " ".join(parts)


class GameItemWidget(QWidget):
    """
    Custom widget for displaying a game item in the library list.
    Layout: [ Checkbox (select mode) ] [ Image ] [ Name/Size/Status ]
    """

    def __init__(
        self,
        game_data: dict,
        size_str: str,
        accent_color: str,
        background_color: str,
        select_mode: bool = False,
        is_selected: bool = False,
        applist_2_0_enabled: bool = True,
        parent_dialog = None,
    ):
        super().__init__()
        self.game_data = game_data
        self.accent_color = accent_color
        self.background_color = background_color
        self._select_mode = select_mode
        self._is_selected = is_selected
        self.checkbox = None
        self.applist_2_0_enabled = applist_2_0_enabled
        self.parent_dialog = parent_dialog
        self._init_ui(size_str)

    def _init_ui(self, size_str: str) -> None:
        """Initialize the UI components."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 1, 16, 1)
        layout.setSpacing(16)

        # Check setting
        applist_2_0_enabled = self.applist_2_0_enabled

        # --- Checkbox (select mode only, old style) ---
        if not applist_2_0_enabled and self._select_mode:
            self.checkbox = QCheckBox()
            self.checkbox.setChecked(self._is_selected)
            self.checkbox.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.checkbox.setStyleSheet("QCheckBox::indicator { width: 18px; height: 18px; }")
            layout.addWidget(self.checkbox)

        # --- Image Section ---
        self.image_label = QLabel()
        self.image_label.setFixedSize(210, 116)  # Fits perfectly in 118px card height minus borders
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        name = self.game_data.get("game_name", "Unknown")
        display_name = format_game_display_name(self.game_data)
        self.image_label.setText(name[:2].upper())

        self.image_label.setStyleSheet(
            f"border-top-left-radius: 11px; "
            f"border-bottom-left-radius: 11px; "
            f"border-top-right-radius: 0px; "
            f"border-bottom-right-radius: 0px; "
            f"background-color: rgba(255, 255, 255, 0.02); "
            f"color: {self.accent_color}; "
        )
        layout.addWidget(self.image_label)

        # --- Checkbox Overlay (new style) ---
        if applist_2_0_enabled:
            self.checkbox = QCheckBox(self.image_label)
            self.checkbox.setChecked(self._is_selected)
            self.checkbox.move(10, 10)
            self.checkbox.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.checkbox.setStyleSheet(
                f"""
                QCheckBox::indicator {{
                    width: 20px;
                    height: 20px;
                    border: 2px solid rgba(255, 255, 255, 180);
                    border-radius: 10px;
                    background-color: rgba(0, 0, 0, 150);
                }}
                QCheckBox::indicator:checked {{
                    background-color: {self.accent_color};
                    border-color: {self.accent_color};
                }}
                """
            )
            self.checkbox.setVisible(self._select_mode)

        # --- Info Section (Vertical) ---
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 8, 0, 8)
        info_layout.setSpacing(4)

        # Game name and status badge side-by-side row
        name_row_layout = QHBoxLayout()
        name_row_layout.setContentsMargins(0, 0, 0, 0)
        name_row_layout.setSpacing(8)

        name_label = QLabel(display_name)
        name_label.setStyleSheet(
            "color: #FFFFFF; font-size: 15px; font-weight: bold;"
        )
        name_label.setWordWrap(True)
        name_row_layout.addWidget(name_label)

        # Update status badge
        self.status_label = QLabel()
        self.status_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        update_status = self.game_data.get("update_status", "cannot_determine")
        status_map = {
            "update_available": ("New version available", "#FF8A80", "rgba(229, 115, 115, 0.15)"),
            "up_to_date": ("Up to date", "#81C784", "rgba(129, 199, 132, 0.15)"),
            "checking": ("Checking for updates...", "#FFA726", "rgba(255, 167, 38, 0.12)"),
        }
        text, color, bg_color = status_map.get(
            update_status, ("Unable to check updates", "#B0BEC5", "rgba(176, 190, 197, 0.12)")
        )
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"color: {color}; "
            f"background-color: {bg_color}; "
            f"border-radius: 10px; "
            f"padding: 3px 10px; "
            f"font-size: 11px; "
            f"font-weight: bold;"
        )
        name_row_layout.addWidget(self.status_label)
        name_row_layout.addStretch()

        info_layout.addLayout(name_row_layout)

        # Size
        size_label = QLabel(f"Size: {size_str}")
        size_label.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 12px;")
        info_layout.addWidget(size_label)

        # Manifest cache status
        self.manifest_label = QLabel()
        info_layout.addWidget(self.manifest_label)
        self.update_manifest_label()

        info_layout.addStretch()
        layout.addLayout(info_layout)

    def update_manifest_label(self) -> None:
        """Update the manifest status label in-place."""
        if not hasattr(self, "manifest_label") or not self.manifest_label:
            return

        appid = self.game_data.get("appid", "0")
        update_status = self.game_data.get("update_status", "cannot_determine")

        if not appid or appid in ("0", "N/A", "unknown"):
            self.manifest_label.setText("Manifest: N/A")
            self.manifest_label.setStyleSheet("color: rgba(255, 255, 255, 0.45); font-size: 12px; font-style: italic;")
            return

        last_updated = None
        if self.parent_dialog and hasattr(self.parent_dialog, "_manifest_mtimes"):
            last_updated = self.parent_dialog._manifest_mtimes.get(appid)

        if last_updated is None:
            from utils.helpers import get_base_path
            fpath = get_base_path() / "hubcap_manifests" / f"accela_fetch_{appid}.zip"
            if fpath.exists():
                try:
                    last_updated = fpath.stat().st_mtime
                except Exception:
                    pass

        if update_status == "checking":
            self.manifest_label.setText("Manifest: Fetching...")
            self.manifest_label.setStyleSheet("color: #FFA726; font-size: 12px; font-style: italic;")
        elif not last_updated:
            self.manifest_label.setText("Manifest: Not Found")
            self.manifest_label.setStyleSheet("color: rgba(255, 255, 255, 0.45); font-size: 12px; font-style: italic;")
        else:
            import time
            age_seconds = time.time() - last_updated
            if age_seconds < 0:
                age_seconds = 0

            if age_seconds < 60:
                age_str = f"{int(age_seconds)}s"
            elif age_seconds < 3600:
                age_str = f"{int(age_seconds // 60)}m"
            elif age_seconds < 86400:
                age_str = f"{int(age_seconds // 3600)}h"
            else:
                days = int(age_seconds // 86400)
                if days < 30:
                    age_str = f"{days}d"
                elif days < 365:
                    age_str = f"{days // 30}mo"
                else:
                    age_str = f"{days // 365}y"

            self.manifest_label.setText(f"Manifest: Cached ({age_str} ago)")
            self.manifest_label.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 12px; font-style: italic;")

    def update_status(self, update_status: str) -> None:
        """Update update and manifest status labels in-place."""
        self.game_data["update_status"] = update_status
        status_map = {
            "update_available": ("New version available", "#FF8A80", "rgba(229, 115, 115, 0.15)"),
            "up_to_date": ("Up to date", "#81C784", "rgba(129, 199, 132, 0.15)"),
            "checking": ("Checking for updates...", "#FFA726", "rgba(255, 167, 38, 0.12)"),
        }

        text, color, bg_color = status_map.get(
            update_status, ("Unable to check updates", "#B0BEC5", "rgba(176, 190, 197, 0.12)")
        )

        if hasattr(self, "status_label") and self.status_label:
            self.status_label.setText(text)
            self.status_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            self.status_label.setStyleSheet(
                f"color: {color}; "
                f"background-color: {bg_color}; "
                f"border-radius: 10px; "
                f"padding: 3px 10px; "
                f"font-size: 11px; "
                f"font-weight: bold;"
            )

        self.update_manifest_label()

    def set_image(self, pixmap: QPixmap) -> None:
        """Sets the image on the label, scaling it nicely with right-fade blend."""
        if not pixmap or pixmap.isNull():
            return

        target_size = self.image_label.size()
        scaled = pixmap.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )

        cropped_faded = QPixmap(target_size)
        cropped_faded.fill(Qt.GlobalColor.transparent)

        painter = QPainter(cropped_faded)
        dx = (target_size.width() - scaled.width()) // 2
        dy = (target_size.height() - scaled.height()) // 2
        painter.drawPixmap(dx, dy, scaled)

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        gradient = QLinearGradient(0, 0, target_size.width(), 0)
        gradient.setColorAt(0.0, QColor(0, 0, 0, 255))
        gradient.setColorAt(0.5, QColor(0, 0, 0, 255))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))

        painter.fillRect(cropped_faded.rect(), QBrush(gradient))
        painter.end()

        self.image_label.setPixmap(cropped_faded)

    def set_selected(self, selected: bool) -> None:
        """Update the checkbox checked state visually."""
        self._is_selected = selected
        if self.checkbox is not None:
            self.checkbox.setChecked(selected)

    def sizeHint(self) -> QSize:
        """Return size hint that matches the desired row height."""
        return QSize(400, 118)


class BlurredHeaderWidget(QWidget):
    """Custom widget containing a blurred background image and overlay."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bg_label = QLabel(self)
        self.bg_label.setScaledContents(True)
        self.overlay = QWidget(self)
        self.overlay.setStyleSheet("background-color: rgba(0, 0, 0, 165);")
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.bg_label.setGeometry(0, 0, self.width(), self.height())
        self.overlay.setGeometry(0, 0, self.width(), self.height())


class GameLibraryDialog(QDialog):
    """Dialog to display and manage the game library."""

    goldberg_check_complete = pyqtSignal(bool)  # is_applied
    manifest_download_complete = pyqtSignal(str, str, dict)  # fpath, error, game_data
    uninstall_complete = pyqtSignal(bool, str)  # success, error_message
    zip_parse_complete = pyqtSignal(object, str, dict, object, object)  # parsed_data, filepath, game_data, dialog, parse_progress
    hubcap_status_check_complete = pyqtSignal(dict, dict, object, object)  # result, game_data, dialog, check_progress

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
                border: 1px solid rgba(255, 255, 255, 0.08); 
                border-radius: 12px;
                margin: 6px 0px;
                color: #FFFFFF;
            }}
            QListWidget::item:hover {{ 
                background-color: rgba(255, 255, 255, 0.07); 
                border-color: rgba(255, 255, 255, 0.16); 
            }}
            QListWidget::item:selected {{ 
                background-color: rgba(255, 255, 255, 0.12); 
                border-color: {self.accent_color}; 
            }}
            
            QLabel {{ color: rgba(255, 255, 255, 0.85); }}
            
            QComboBox {{ 
                background-color: {self.background_color}; 
                color: {self.accent_color}; 
                padding: 4px; 
                border: none; 
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background-color: {self.background_color};
                color: {self.accent_color};
                selection-background-color: #222;
                border: none;
            }}
        """
        )

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

        self.select_mode_button = QPushButton("☑ Select")
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

        # --- Selection Pill Bar (hidden by default, floats below game list) ---
        self.selection_footer = QWidget()
        self.selection_footer.setObjectName("selectionPillBar")
        self.selection_footer.setStyleSheet(
            f"""
            QWidget#selectionPillBar {{
                background-color: rgba(20, 20, 20, 220);
                border: 1px solid rgba(255, 255, 255, 18);
                border-radius: 10px;
            }}
            """
        )
        pill_layout = QHBoxLayout(self.selection_footer)
        pill_layout.setContentsMargins(10, 6, 10, 6)
        pill_layout.setSpacing(10)

        # Left: Exit select mode
        self._exit_select_btn = QPushButton("✕  Exit")
        self._exit_select_btn.setFlat(True)
        self._exit_select_btn.setStyleSheet(
            "QPushButton { color: rgba(255,255,255,120); border: none; background: transparent; font-size: 9pt; padding: 0; }"
            "QPushButton:hover { color: #FFFFFF; }"
        )
        self._exit_select_btn.clicked.connect(lambda: (
            self.select_mode_button.setChecked(False),
            self._toggle_select_mode()
        ))
        pill_layout.addWidget(self._exit_select_btn)

        pill_layout.addStretch()

        # Center: Count badge
        self.selection_count_label = QLabel("0 selected")
        self.selection_count_label.setStyleSheet(
            f"color: {self.accent_color}; font-size: 9pt; font-weight: bold;"
        )
        self.selection_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill_layout.addWidget(self.selection_count_label)

        pill_layout.addStretch()

        # Right: Queue action button
        self.queue_selected_btn = QPushButton("Start Queue  ▶")
        self.queue_selected_btn.clicked.connect(self._on_queue_selected)
        self.queue_selected_btn.setEnabled(False)
        self.queue_selected_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: #000000;
                border: none;
                border-radius: 5px;
                padding: 3px 14px;
                font-size: 9pt;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: rgba(255,255,255,220); }}
            QPushButton:disabled {{ background-color: rgba(255,255,255,25); color: rgba(255,255,255,40); }}
            """
        )
        pill_layout.addWidget(self.queue_selected_btn)

        self.selection_footer.setVisible(False)
        layout.addWidget(self.selection_footer)

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

    # --- Scanning & Updates ---

    def _scan_for_games(self) -> None:
        if self._scanning or not self.game_manager:
            return

        self._scanning = True
        self.scan_button.setEnabled(False)
        self.scan_button.setText("Scanning...")
        self.info_label.setText("Scanning Steam libraries...")
        self._refreshing = True
        self.games_list.clear()

        self.game_manager.scan_steam_libraries_async()

    def _on_scan_complete(self, count: int) -> None:
        self.scan_button.setEnabled(True)
        self.scan_button.setText("Scan Libraries")

        if count > 0:
            # Update checks are triggered separately; wait for all_updates_checked signal
            self._checking_updates = True
            self.info_label.setText(
                f"Found {count} game(s) — checking for updates..."
            )
            return

        self.info_label.setText(f"Scan complete: Found {count} installed Steam game(s).")
        self._scanning = False
        self._refresh_game_list()

    def _on_all_updates_checked(self) -> None:
        """Called when the full batch update check finishes (replaces the 500ms polling loop)."""
        if not self._checking_updates:
            return
        self._checking_updates = False
        self._scanning = False
        self._refresh_game_list()

    def _on_game_update_status_changed(self, appid: str, update_status: str) -> None:
        if self._closing or not self.isVisible():
            return

        # Find matching item
        item = None
        for i in range(self.games_list.count()):
            it = self.games_list.item(i)
            game_data = it.data(Qt.ItemDataRole.UserRole)
            if game_data and game_data.get("appid") == appid:
                item = it
                break

        if not item:
            return

        self._update_item_status(item, appid, update_status)

    def _update_item_status(
        self, item: QListWidgetItem, appid: str, update_status: str
    ) -> None:
        """Update specific item status logic extracted to flatten logic."""
        game_data = item.data(Qt.ItemDataRole.UserRole)
        game_data["update_status"] = update_status
        item.setData(Qt.ItemDataRole.UserRole, game_data)

        # Update widget in-place directly
        widget = self.games_list.itemWidget(item)
        if isinstance(widget, GameItemWidget):
            widget.update_status(update_status)

    # --- List Management ---

    def _on_sort_changed(self) -> None:
        if self.settings:
            sort_option = self.sort_combo.currentData()
            self.settings.setValue("library_sort_option", sort_option)
        self._refresh_game_list()

    def _on_search_changed(self) -> None:
        self.search_timer.start(300)

    @staticmethod
    def _get_sort_key(game, sort_option):
        """Helper for sorting keys."""
        if sort_option in ("name_asc", "name_desc"):
            return game.get("game_name", "").lower()
        if sort_option in ("size_asc", "size_desc"):
            return game.get("size_on_disk", 0)
        if sort_option == "appid":
            try:
                return int(game.get("appid", 0))
            except (ValueError, TypeError):
                return 0
        if sort_option == "recently_installed":
            path = (
                game.get("accela_marker_path")
                or game.get("depot_downloader_path")
                or game.get("appmanifest_path")
                or game.get("install_path", "")
            )
            if path and os.path.exists(path):
                return os.path.getmtime(path)
            return 0
        if sort_option == "update_first":
            # Games with an update available sort first (0), then everything else (1)
            has_update = game.get("update_status") == "update_available"
            return (0 if has_update else 1, game.get("game_name", "").lower())
        if sort_option == "dlc_only_first":
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc = is_dlc_only_mode(str(game.get("appid", "")))
            return (0 if is_dlc else 1, game.get("game_name", "").lower())
        return game.get("game_name", "").lower()

    def _sort_games(self, games: list) -> list:
        sort_option = self.sort_combo.currentData()
        reverse = sort_option in ("name_desc", "size_desc", "recently_installed")
        return sorted(
            games,
            key=lambda g: GameLibraryDialog._get_sort_key(g, sort_option),
            reverse=reverse,
        )

    def _refresh_game_list(self) -> None:
        if self._closing:
            return

        self._refreshing = True

        # Cancel any active image fetches and clear the queue
        for fetcher in list(self._active_fetchers.values()):
            try:
                fetcher.stop()
            except Exception:
                pass
        self._active_fetchers.clear()
        self._image_fetch_queue.clear()
        self._pending_image_fetches.clear()
        self._current_fetches = 0

        self.games_list.clear()
        self._items_by_appid.clear()
        self._manifest_mtimes.clear()

        # Pre-scan manifests directory for mtimes
        try:
            from utils.helpers import get_base_path
            manifests_dir = get_base_path() / "hubcap_manifests"
            if manifests_dir.exists():
                with os.scandir(manifests_dir) as entries:
                    for entry in entries:
                        if entry.is_file() and entry.name.startswith("accela_fetch_") and entry.name.endswith(".zip"):
                            parts = entry.name.split("_")
                            if len(parts) >= 3:
                                appid_part = parts[2].split(".")[0]
                                try:
                                    self._manifest_mtimes[appid_part] = entry.stat().st_mtime
                                except Exception:
                                    pass
        except Exception as e:
            logger.warning(f"Failed to pre-scan hubcap_manifests: {e}")

        if not self.game_manager:
            self._refreshing = False
            return

        games = self.game_manager.get_all_games()
        
        # Filter games by search term (case-insensitive)
        has_filter = False
        if hasattr(self, "search_input"):
            query = self.search_input.text().strip().lower()
            if query:
                games = [g for g in games if query in g.get("game_name", "").lower()]
                has_filter = True

        games = self._sort_games(games)
        
        # Limit displayed games count when filtering to prevent heavy UI layout lag
        truncated = False
        if has_filter and len(games) > 150:
            showing_games = games[:150]
            truncated = True
        else:
            showing_games = games

        total_size = 0
        accela_count = 0

        for game in showing_games:
            if game.get("is_accela_install"):
                accela_count += 1
            total_size += self._add_game_to_list(game)

        if truncated:
            self.info_label.setText(
                f"Showing top 150 of {len(games)} game(s) ({accela_count} ACCELA-managed) - "
                f"Please refine your search query."
            )
        else:
            self.info_label.setText(
                f"Found {len(games)} Steam game(s) ({accela_count} ACCELA-managed) - "
                f"Total Size: {GameLibraryDialog._format_size(total_size)}"
            )
        self._refreshing = False

        # Defer image downloads to a 100ms timer to prioritize UI list loading speed
        QTimer.singleShot(100, self._start_pending_image_fetches)

    def _add_game_to_list(self, game: dict) -> int:
        """Creates and adds a single game widget to the list. Returns size."""
        size = game.get("size_on_disk", 0)
        appid = str(game.get("appid", "0"))
        is_selected = appid in self._selected_appids
        widget = GameItemWidget(
            game,
            GameLibraryDialog._format_size(size),
            self.accent_color,
            self.background_color,
            select_mode=self._select_mode,
            is_selected=is_selected,
            applist_2_0_enabled=self.applist_2_0_enabled,
            parent_dialog=self,
        )
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, game)
        item.setSizeHint(widget.sizeHint())
        self.games_list.addItem(item)
        self.games_list.setItemWidget(item, widget)

        # Save to lookup index
        if appid not in ("0", "N/A", "unknown"):
            self._items_by_appid[appid] = item

        app_id = str(game.get("appid", "0"))
        if app_id in ("0", "N/A", "unknown"):
            self.executor.submit(self._resolve_and_update_item, item, game)
        else:
            self._pending_image_fetches.append((item, app_id))

        return size

    def _resolve_and_update_item(self, item: QListWidgetItem, game_data: dict) -> None:
        """Resolve AppID in a background thread and update the item."""
        name = game_data.get("game_name")
        resolved_appid = self._resolve_appid_by_name(name)
        if resolved_appid:
            game_data["appid"] = resolved_appid
            QTimer.singleShot(
                0, lambda: self._update_item_with_resolved_id(item, game_data)
            )

    def _start_pending_image_fetches(self) -> None:
        """Sequential start of delayed image fetches."""
        if self._closing or not hasattr(self, "_pending_image_fetches"):
            return
        for item, app_id in self._pending_image_fetches:
            self._fetch_item_image(item, app_id)
        self._pending_image_fetches.clear()

    @staticmethod
    def _resolve_appid_by_name(name: str) -> str | None:
        """Search the local database for an AppID by name."""
        if not name or not DatabaseManager:
            return None
        try:
            db = DatabaseManager()
            if not db.conn:
                return None

            cur = db.conn.cursor()
            cur.execute("SELECT appid FROM apps WHERE name = ? COLLATE NOCASE", (name,))
            row = cur.fetchone()
            if row:
                return str(row[0])
        except Exception as e:
            logger.debug(f"DB lookup failed for '{name}': {e}")
        return None

    def _update_item_with_resolved_id(
        self, item: QListWidgetItem, game_data: dict
    ) -> None:
        """Update the item on the main thread with the resolved AppID."""
        if self._closing:
            return
        item.setData(Qt.ItemDataRole.UserRole, game_data)
        appid = game_data.get("appid")
        if appid and appid not in ("0", "N/A", "unknown"):
            self._items_by_appid[appid] = item
        self._fetch_item_image(item, game_data["appid"])

    def _on_item_selected(self, item: QListWidgetItem) -> None:
        """Handle click on list item."""
        if self._refreshing:
            return

        if not item:
            return

        game_data = item.data(Qt.ItemDataRole.UserRole)
        if not game_data:
            return

        # In select mode: toggle selection, don't open dialog
        if self._select_mode:
            appid = str(game_data.get("appid", "0"))
            if appid in self._selected_appids:
                self._selected_appids.discard(appid)
            else:
                self._selected_appids.add(appid)
            # Update checkbox visual on the widget
            widget = self.games_list.itemWidget(item)
            if isinstance(widget, GameItemWidget):
                widget.set_selected(appid in self._selected_appids)
            self._update_selection_footer()
            return

        if self._dialog_open:
            return

        # Debounce
        self._dialog_open = True
        QTimer.singleShot(500, lambda: setattr(self, "_dialog_open", False))

        self._show_game_details_dialog(game_data)

    # --- Select Mode ---

    def _toggle_select_mode(self) -> None:
        """Enable or disable multi-select mode."""
        self._select_mode = self.select_mode_button.isChecked()
        if not self._select_mode:
            self._selected_appids.clear()

        self.selection_footer.setVisible(self._select_mode)
        self._update_selection_footer()
        self._refresh_game_list()

    def _update_selection_footer(self) -> None:
        """Update the selection count badge and button state."""
        count = len(self._selected_appids)
        self.selection_count_label.setText(
            f"{count} selected" if count != 1 else "1 selected"
        )
        self.queue_selected_btn.setEnabled(count > 0)

    def _clear_selection(self) -> None:
        """Clear all selections and refresh visual state."""
        self._selected_appids.clear()
        self._update_selection_footer()
        # Update all visible checkboxes
        for i in range(self.games_list.count()):
            item = self.games_list.item(i)
            widget = self.games_list.itemWidget(item)
            if isinstance(widget, GameItemWidget):
                widget.set_selected(False)

    def _on_queue_selected(self) -> None:
        """Directly enqueue selected games without any intermediate dialog."""
        if not self._selected_appids:
            return

        # Gather game data for selected appids
        selected_games = []
        for i in range(self.games_list.count()):
            item = self.games_list.item(i)
            if not item:
                continue
            game_data = item.data(Qt.ItemDataRole.UserRole)
            if not game_data:
                continue
            appid = str(game_data.get("appid", "0"))
            if appid in self._selected_appids:
                selected_games.append(game_data)

        if not selected_games:
            return

        # Disable the button so it can't be double-clicked
        self.queue_selected_btn.setEnabled(False)
        self.queue_selected_btn.setText("Queueing...")
        self.info_label.setText(f"Queueing {len(selected_games)} game(s)...")

        # Exit select mode immediately
        self.select_mode_button.setChecked(False)
        self._toggle_select_mode()

        # Run the heavy work (zip parse + depot resolve) off the main thread
        import threading
        threading.Thread(
            target=self._enqueue_games_background,
            args=(selected_games,),
            daemon=True
        ).start()

    def _enqueue_games_background(self, selected_games: list) -> None:
        """Background thread: enqueue each game using the normal download+depot flow."""
        queued_count = 0
        total = len(selected_games)
        for i, game_data in enumerate(selected_games):
            appid = str(game_data.get("appid", "0"))
            if appid in ("0", "N/A", "unknown"):
                logger.warning(f"Skipping batch queue for game with invalid appid: {game_data.get('game_name')}")
                continue
            try:
                success = self._enqueue_single_game(game_data)
                if success:
                    queued_count += 1
            except Exception as e:
                logger.error(f"Batch queue failed for {game_data.get('game_name')}: {e}", exc_info=True)

        # Update UI back on main thread
        QMetaObject.invokeMethod(
            self,
            "_on_enqueue_finished",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, queued_count),
            Q_ARG(int, total),
        )

    @pyqtSlot(int, int)
    def _on_enqueue_finished(self, queued_count: int, total: int) -> None:
        """Called on main thread after background enqueueing is done."""
        if queued_count > 0:
            self.info_label.setText(f"\u2713 Queued {queued_count} of {total} game(s) \u2014 downloads starting.")
        else:
            self.info_label.setText("Nothing queued. Check App IDs are valid.")

    def _enqueue_single_game(self, game_data: dict) -> bool:
        """Enqueue a single game through the manifest fetch + depot selection flow."""
        try:
            appid = str(game_data.get("appid", "0"))
            name = game_data.get("game_name", "Unknown")
            update_status = game_data.get("update_status")

            from utils.helpers import get_base_path
            from core import morrenus_api as _api
            from utils.settings import get_settings
            settings = get_settings()

            # Check local cache first
            local_path = None
            fpath = get_base_path() / "hubcap_manifests" / f"accela_fetch_{appid}.zip"
            is_fresh = settings.value(f"manifest_is_fresh/{appid}", False, type=bool)

            if fpath.exists() and (update_status != "update_available" or is_fresh):
                local_path = str(fpath)

            if not local_path:
                # Download manifest
                fpath, error = _api.download_manifest(appid)
                if error or not fpath:
                    logger.warning(f"Batch queue: manifest download failed for {name}: {error}")
                    return False
                local_path = str(fpath)
                # Manifest is fresh now
                settings.setValue(f"manifest_is_fresh/{appid}", True)
                latest_id = settings.value(f"latest_steam_manifest_id/{appid}", "", type=str)
                if latest_id:
                    settings.setValue(f"fetched_manifest_id/{appid}", latest_id)

            # Parse for depots
            from core.tasks.process_zip_task import ProcessZipTask

            zip_task = ProcessZipTask()
            parsed_data = zip_task.run(local_path)

            metadata = {
                "appid": appid,
                "library_path": game_data.get("library_path"),
                "install_path": game_data.get("install_path"),
                "game_name": name,
            }

            if parsed_data and parsed_data.get("depots"):
                from ui.dialogs.depotselection import DepotSelectionDialog
                auto_skip = settings.value("auto_skip_single_choice", False, type=bool)
                depots = parsed_data.get("depots")

                selected_depots = None

                # Smart selection logic
                import json
                smart_active = settings.value("smart_depot_selection", False, type=bool)
                val = settings.value(f"depot_selection/{appid}", "", type=str)
                should_prompt = True

                if smart_active and val:
                    try:
                        data = json.loads(val)
                        cached_selected = data.get("selected", [])
                        cached_all = data.get("all_available", [])
                        current_depots = list(depots.keys())
                        has_new_depot = any(d not in cached_all for d in current_depots)
                        if not has_new_depot:
                            selected_depots = [d for d in cached_selected if d in depots]
                            should_prompt = False
                            logger.info(f"Smart selection active (batch). Reusing cached depots for {appid}: {selected_depots}")
                    except Exception as e:
                        logger.warning(f"Error parsing cached depot selection: {e}")

                if should_prompt:
                    if auto_skip and len(depots) == 1:
                        selected_depots = list(depots.keys())
                    else:
                        # Depot dialog must run on the main thread
                        result_holder = [None]
                        done_event = __import__("threading").Event()

                        def _show_depot_dialog():
                            try:
                                depot_dialog = DepotSelectionDialog(
                                    parsed_data["appid"],
                                    parsed_data.get("game_name", name),
                                    depots,
                                    parsed_data.get("header_url"),
                                    self.main_window,
                                )
                                if depot_dialog.exec():
                                    result_holder[0] = depot_dialog.get_selected_depots()
                            finally:
                                done_event.set()

                        QMetaObject.invokeMethod(
                            self,
                            "_run_on_main_thread",
                            Qt.ConnectionType.QueuedConnection,
                            Q_ARG(object, _show_depot_dialog),
                        )
                        done_event.wait(timeout=120)
                        selected_depots = result_holder[0]

                if not selected_depots:
                    logger.info(f"Batch queue: user cancelled depot selection for {name}")
                    return False

                metadata["selected_depots_list"] = selected_depots
                # Cache the choice
                try:
                    settings.setValue(
                        f"depot_selection/{appid}",
                        json.dumps({
                            "selected": selected_depots,
                            "all_available": list(depots.keys()),
                            "descriptions": {d_id: depots.get(d_id, {}).get("desc", "") for d_id in selected_depots}
                        })
                    )
                except Exception as e:
                    logger.warning(f"Failed to cache depot selection: {e}")

            self.main_window.job_queue.add_job(local_path, metadata)
            logger.info(f"Batch queued: {name} (appid={appid})")
            return True

        except Exception as e:
            logger.error(f"Batch queue failed for {game_data.get('game_name')}: {e}", exc_info=True)
            return False

    @pyqtSlot(object)
    def _run_on_main_thread(self, fn) -> None:
        """Slot to execute a callable on the main thread (used by background enqueue thread)."""
        fn()

    # --- Image Handling ---

    def _fetch_item_image(self, _item: QListWidgetItem, app_id: str) -> None:
        if not ImageFetcher:
            return
        if app_id in self._active_fetchers or app_id in self._image_fetch_queue:
            return

        self._image_fetch_queue.append(app_id)
        self._process_fetch_queue()

    def _process_fetch_queue(self) -> None:
        if self._closing or self._current_fetches >= self._max_concurrent_fetches:
            return
        if not self._image_fetch_queue:
            return

        app_id = self._image_fetch_queue.popleft()
        self._current_fetches += 1
        
        QTimer.singleShot(0, lambda: self._do_fetch_image(app_id))

    def _do_fetch_image(self, app_id: str) -> None:
        if self._closing:
            return
            
        url = ImageFetcher.get_header_image_url(app_id)
        if not url:
            self._cleanup_fetcher(app_id)
            return

        fetcher = ImageFetcher(url)
        fetcher.setProperty("app_id", app_id)
        self._active_fetchers[app_id] = fetcher

        fetcher.finished.connect(self._on_item_image_fetched)
        fetcher.finished.connect(lambda _, aid=app_id: self._cleanup_fetcher(aid))
        fetcher.start()

    def _cleanup_fetcher(self, app_id: str) -> None:
        if app_id in self._active_fetchers:
            del self._active_fetchers[app_id]
        self._current_fetches = max(0, self._current_fetches - 1)
        self._process_fetch_queue()

    def _on_item_image_fetched(self, image_data: bytes) -> None:
        if self._closing or not self.isVisible():
            return

        sender = self.sender()
        app_id = sender.property("app_id")
        if not app_id:
            return

        if not image_data:
            # If image fetch failed, trigger a background refresh of the URL
            QTimer.singleShot(0, lambda: self._trigger_header_refresh(app_id))
            return

        self._image_cache[app_id] = image_data

        # Find item and widget using O(1) lookup index
        item = self._items_by_appid.get(app_id)
        if item:
            self._update_item_image_if_match(item, app_id, image_data)

    def _check_appid_match(self, data: dict, app_id: str) -> bool:
        """Helper to check if a game's AppID matches the target AppID."""
        game_appid = str(data.get("appid", "0"))
        if game_appid == app_id:
            return True
        if game_appid in ("0", "N/A", "unknown"):
            resolved = self._resolve_appid_by_name(data.get("game_name"))
            return resolved == app_id
        return False

    def _update_item_image_if_match(self, item, app_id, image_data):
        """Helper to check if list item matches app_id and update image."""
        data = item.data(Qt.ItemDataRole.UserRole)
        if self._check_appid_match(data, app_id):
            widget = self.games_list.itemWidget(item)
            if isinstance(widget, GameItemWidget):
                pixmap = QPixmap()
                pixmap.loadFromData(image_data)
                widget.set_image(pixmap)

    def _trigger_header_refresh(self, app_id: str) -> None:
        """Trigger background refresh of header URL from API."""

        def fetch_and_update():
            try:
                from utils.image_fetcher import ImageFetcher

                return ImageFetcher.fetch_header_from_web_api(app_id)
            except Exception as e:
                logger.warning(f"Header refresh failed for {app_id}: {e}")
            return None

        def on_complete(future_result):
            try:
                url = future_result.result()
                if url and not self._closing:
                    QTimer.singleShot(
                        0, lambda: self._apply_header_refresh(app_id, url)
                    )
            except RuntimeError:
                pass

        future = self.executor.submit(fetch_and_update)
        future.add_done_callback(on_complete)

    def _apply_header_refresh(self, app_id: str, api_url: str) -> None:
        """Update DB and retry fetch with new URL."""
        if self._closing or not self.isVisible():
            return

        try:
            from managers.db_manager import DatabaseManager

            db = DatabaseManager()
            db.upsert_app_info(app_id, {"header_url": api_url})

            # Retry fetch
            if app_id not in self._active_fetchers:
                fetcher = ImageFetcher(api_url)
                fetcher.setProperty("app_id", app_id)
                self._active_fetchers[app_id] = fetcher
                fetcher.finished.connect(self._on_item_image_fetched)
                fetcher.finished.connect(
                    lambda _, aid=app_id: self._cleanup_fetcher(aid)
                )
                fetcher.start()
        except RuntimeError as e:
            logger.warning(f"Failed to apply header refresh: {e}")

    # --- Game Details Dialog ---

    def _show_game_details_dialog(self, game_data: dict) -> None:
        try:
            from ui.dialogs.gamelibrary_v2 import GameDetailsDialogV2
            dialog = GameDetailsDialogV2(self, game_data)
            self._details_dialog = dialog
            dialog.exec()
            self._details_dialog = None
        except Exception as e:
            logger.error(f"Failed to load Game Details V2: {e}", exc_info=True)

    def _check_goldberg_async(self, path: str) -> None:
        """Background task to check Goldberg status."""
        is_applied = GameLibraryDialog._is_goldberg_applied(path)
        self.goldberg_check_complete.emit(is_applied)

    def _on_goldberg_check_complete(self, is_applied: bool) -> None:
        """Slot to update UI after background check."""
        # Ensure the dialog/button still exists and is relevant
        if not hasattr(self, "gb_btn"):
            return

        self.gb_btn.setText("Remove Goldberg" if is_applied else "Apply Goldberg")
        self.gb_btn.setEnabled(True)

        if is_applied:
            self.gb_btn.setStyleSheet(
                f"border: 1px solid {self.accent_color}; color: {self.accent_color};"
            )
        else:
            self.gb_btn.setStyleSheet("")

    # --- Actions ---

    def _fetch_game_manifest(self, game_data: dict, dialog: QDialog = None, download_only: bool = False, local_path_override: str = None, branch: str = "public") -> None:
        """Trigger background manifest download and show progress."""
        api_key = self.settings.value("morrenus_api_key", "", type=str).strip()
        if not api_key:
            QMessageBox.critical(self, "API Key Missing", "Please configure your Hubcap API key in Settings before downloading updates/manifests.")
            return

        app_id = str(game_data.get("appid"))
        if app_id in ("0", "N/A", "unknown"):
            QMessageBox.critical(self, "Error", f"Invalid AppID: {app_id}")
            return

        name = game_data.get("game_name", "Unknown")
        status = game_data.get("update_status")

        # Flag rollback so downstream won't mark manifest as fresh
        is_rollback = local_path_override is not None

        # ── Local zip path (for Verify or Rollback) ─────────────────────────
        if branch and branch != "public":
            fpath = get_base_path() / "hubcap_manifests" / f"accela_fetch_{app_id}_branch_{branch}.zip"
        else:
            fpath = get_base_path() / "hubcap_manifests" / f"accela_fetch_{app_id}.zip"
        is_fresh = self.settings.value(f"manifest_is_fresh/{app_id}", False, type=bool)

        local_path = None
        if is_rollback and local_path_override and Path(local_path_override).exists():
            local_path = local_path_override
        elif fpath.exists() and (status != "update_available" or is_fresh):
            local_path = str(fpath)

        if local_path and not download_only:
            logger.info(f"Using local manifest zip for verify/rollback: {local_path}")
            self._submit_job(local_path, game_data, dialog)
            return

        # ── Smart Update Mode routing (for network updates) ─────────────────
        # Route to SmartUpdateTask only when an update is available or local zip is missing
        smart_mode = True
        if smart_mode and not is_rollback and not download_only and status == "update_available":
            try:
                from managers.depot_key_manager import DepotKeyManager
                dkm = DepotKeyManager()
                if dkm.has_depot_keys(app_id):
                    logger.info(f"[Smart Update] Routing {name} ({app_id}) branch '{branch}' through SmartUpdateTask")
                    self._handle_smart_update(app_id, name, game_data, dialog, branch=branch)
                    return
                else:
                    logger.warning(
                        f"[Smart Update] {name} ({app_id}): no cached keys — "
                        "falling back to classic path. Run a full manifest fetch to enable Smart Mode."
                    )
                    from PyQt6.QtWidgets import QMessageBox
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(0, lambda: QMessageBox.information(
                        self,
                        "Smart Update Mode",
                        f"Smart Update Mode is enabled but {name} has no cached depot keys yet.\n\n"
                        "Using classic fetch. Smart Mode will activate automatically after the first successful fetch."
                    ))
            except Exception as _smart_err:
                logger.error(f"[Smart Update] Smart mode routing error for {app_id}: {_smart_err}")

        if not local_path:
            if download_only:
                game_data = game_data.copy()
                game_data["_download_only"] = True
            if status == "update_available" and not download_only:
                self._check_hubcap_status_first(app_id, game_data, dialog, branch=branch)
            else:
                self._handle_download_manifest(app_id, name, game_data, dialog, branch=branch)
        else:
            if not download_only:
                # For rollback builds, mark game data so we don't clear update_available
                if is_rollback:
                    game_data = game_data.copy()
                    game_data["_is_rollback"] = True
                self._submit_job(local_path, game_data, dialog)

    def _handle_smart_update(self, app_id: str, name: str, game_data: dict, dialog, branch: str = "public") -> None:
        """
        Handles a Smart Update Mode fetch: runs SmartUpdateTask in a background thread
        and routes the result to _submit_job on success, or falls back to classic path
        on needs_full_zip signal.
        """
        from core.tasks.smart_update_task import SmartUpdateTask
        from utils.task_runner import TaskRunner

        logger.info(f"[Smart Update] Starting SmartUpdateTask for {name} ({app_id})")

        task = SmartUpdateTask(app_id, name, branch=branch)
        runner = TaskRunner(self)

        # Store runner to prevent GC
        if not hasattr(self, "_smart_runners"):
            self._smart_runners = []
        self._smart_runners.append(runner)

        def on_smart_finished(assembled_game_data: dict):
            logger.info(f"[Smart Update] SmartUpdateTask finished for {name} ({app_id}) — submitting job")
            # Merge essential fields from original game_data (install_path, etc.) into assembled data
            merged = dict(game_data)
            merged.update(assembled_game_data)
            # Write a temp zip placeholder so _submit_job can find a path
            import io, zipfile, tempfile, os
            tmp_dir = get_base_path() / "hubcap_manifests"
            tmp_path = str(tmp_dir / f"accela_fetch_{app_id}.zip")
            # The SmartUpdateTask already saved the zip; just submit with that path
            self._submit_job(tmp_path, merged, dialog)
            self.settings.setValue(f"manifest_is_fresh/{app_id}", True)
            if assembled_game_data.get("buildid"):
                self.settings.setValue(f"fetched_buildid/{app_id}", assembled_game_data["buildid"])
            if runner in self._smart_runners:
                self._smart_runners.remove(runner)

        def on_needs_full_zip(reason: str):
            logger.warning(f"[Smart Update] {name} ({app_id}) needs full zip: {reason}")
            # Fall back to classic path transparently
            self._handle_download_manifest(app_id, name, game_data, dialog)
            if runner in self._smart_runners:
                self._smart_runners.remove(runner)

        def on_error(err_msg: str):
            logger.error(f"[Smart Update] Error for {name} ({app_id}): {err_msg}")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Smart Update Failed", f"Smart update failed for {name}:\n{err_msg}\n\nFalling back to classic fetch.")
            self._handle_download_manifest(app_id, name, game_data, dialog)
            if runner in self._smart_runners:
                self._smart_runners.remove(runner)

        task.finished.connect(on_smart_finished)
        task.needs_full_zip.connect(on_needs_full_zip)
        task.error.connect(on_error)
        task.progress.connect(lambda msg: logger.info(msg))

        runner.run(task.run)


    def _handle_download_manifest(self, app_id, name, game_data, dialog, branch: str = "public"):
        """Logic separated to flatten nesting in fetch_game_manifest."""
        if not morrenus_api:
            QMessageBox.critical(self, "Error", "API module missing.")
            return

        download_only = game_data.get("_download_only", False)

        if not download_only:
            self._download_progress_dialog = QProgressDialog(
                f"Downloading {name}...", "Cancel", 0, 0, self
            )
            self._download_progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            self._download_progress_dialog.show()

        # Start async download
        self.executor.submit(self._download_manifest_async, app_id, game_data, branch)

    def _download_manifest_async(self, app_id: str, game_data: dict, branch: str = "public") -> None:
        """Background task to download manifest."""
        try:
            fpath, error = morrenus_api.download_manifest(app_id, branch=branch)
            self.manifest_download_complete.emit(
                str(fpath) if fpath else "", str(error) if error else "", game_data
            )
        except Exception as e:
            self.manifest_download_complete.emit("", str(e), game_data)

    def _on_manifest_download_complete(
        self, fpath: str, error: str, game_data: dict
    ) -> None:
        """Slot to handle manifest download completion."""
        if self._download_progress_dialog:
            self._download_progress_dialog.close()
            self._download_progress_dialog = None

        download_only = game_data.get("_download_only", False)

        if hasattr(self, "_configure_depots_after_download") and self._configure_depots_after_download:
            target_game = self._configure_depots_after_download
            self._configure_depots_after_download = None
            if fpath:
                appid = str(target_game.get("appid"))
                self.settings.setValue(f"manifest_is_fresh/{appid}", True)
                latest_id = self.settings.value(f"latest_steam_manifest_id/{appid}", "", type=str)
                if latest_id:
                    self.settings.setValue(f"fetched_manifest_id/{appid}", latest_id)
                self._show_depot_selection_dialog(fpath, target_game)
            else:
                if not download_only:
                    QMessageBox.critical(self, "Error", f"Failed to download manifest: {error}")
                else:
                    logger.error(f"Background manifest download failed: {error}")
            return

        if fpath:
            # Mark manifest as fresh now
            appid = str(game_data.get("appid"))
            self.settings.setValue(f"manifest_is_fresh/{appid}", True)
            latest_id = self.settings.value(f"latest_steam_manifest_id/{appid}", "", type=str)
            if latest_id:
                self.settings.setValue(f"fetched_manifest_id/{appid}", latest_id)
            
            # If we have a valid path, submit the job
            # We need to access the dialog passed to _fetch_game_manifest, but it's not stored.
            # However, we stored _details_dialog in _show_game_details_dialog.
            if not download_only and self._details_dialog:
                self._submit_job(fpath, game_data, self._details_dialog)
        else:
            if not download_only:
                QMessageBox.critical(self, "Error", f"Failed: {error}")
            else:
                logger.error(f"Background manifest download failed for appid {game_data.get('appid')}: {error}")

    def _check_hubcap_status_first(self, app_id: str, game_data: dict, dialog: QDialog) -> None:
        """Runs the Stage 1 pre-download Hubcap status check asynchronously."""
        check_progress = QProgressDialog("Checking Hubcap status...", None, 0, 0, self)
        check_progress.setWindowModality(Qt.WindowModality.WindowModal)
        check_progress.setCancelButton(None)
        check_progress.show()

        def _check_in_background():
            from core import morrenus_api
            try:
                res = morrenus_api.get_manifest_status(app_id)
            except Exception as e:
                logger.error(f"Error checking Hubcap status in background: {e}")
                res = {"error": str(e)}
            self.hubcap_status_check_complete.emit(res, game_data, dialog, check_progress)

        self.executor.submit(_check_in_background)

    def _on_hubcap_status_check_complete(self, result: dict, game_data: dict, dialog: QDialog, check_progress: object) -> None:
        """Handles completion of Stage 1 check on the main thread."""
        if check_progress:
            try:
                check_progress.close()
            except Exception:
                pass

        app_id = str(game_data.get("appid"))
        name = game_data.get("game_name", "Unknown")

        # Check if Hubcap says it needs an update or update is in progress
        needs_update = result.get("needs_update", False) if isinstance(result, dict) else False
        update_in_progress = result.get("update_in_progress", False) if isinstance(result, dict) else False
        is_error = "error" in result if isinstance(result, dict) else True

        if is_error:
            logger.warning(f"Hubcap status check returned error or invalid result for {app_id}: {result}")
            # Do not block download on status check failure (e.g. offline/network issues)
            self._handle_download_manifest(app_id, name, game_data, dialog)
            return

        is_refined = False
        is_timestamp_stale = False
        timestamp_reason = ""
        if not (needs_update or update_in_progress) and is_refined:
            from utils.manifest_verifier import verify_hubcap_freshness
            ver_status, reason, _ = verify_hubcap_freshness(app_id, result)
            if ver_status == "stale":
                is_timestamp_stale = True
                timestamp_reason = reason

        if needs_update or update_in_progress or is_timestamp_stale:
            msg = (
                f"Hubcap reports that its manifest for '{name}' is currently outdated/stale.\n\n"
                "If you proceed, you might download the old version instead of the latest Steam update.\n\n"
            )
            if update_in_progress:
                msg += "Hubcap is currently processing/fetching the update. Please try again in a few minutes.\n\n"
            elif is_timestamp_stale:
                msg += f"Refined Update Check: {timestamp_reason}\n\n"
            else:
                msg += "Hubcap is aware of the new Steam update, but has not ingested the new manifest yet.\n\n"

            msg += "Do you want to continue anyway?"
            
            btn = QMessageBox.warning(
                self, 
                "Hubcap Manifest Not Ready", 
                msg, 
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if btn != QMessageBox.StandardButton.Yes:
                logger.info("User cancelled download due to stale Hubcap manifest")
                return

        # Proceed with download
        self._handle_download_manifest(app_id, name, game_data, dialog)

    def _submit_job(self, filepath: str, game_data: dict, dialog: QDialog) -> None:
        """Submit the job to the main window queue.

        The zip is parsed in a background thread so the main thread stays
        responsive. A loading dialog is shown while parsing runs.

        IMPORTANT: parse_progress must never be touched from the background
        thread — Qt widgets are not thread-safe. The reference is passed through
        the signal so the main-thread slot (_on_zip_parse_complete) closes it.

        Smart Update path: when game_data has _smart_update=True the game_data
        dict is already fully assembled (keys, manifests, buildid, etc.) and the
        saved zip is a lua-less generate bundle — skip ProcessZipTask re-parse.
        """
        # ── Smart Update fast-path ─────────────────────────────────────────
        # game_data already has depots, manifests, buildid from SmartUpdateTask.
        # The zip on disk has no .lua, so ProcessZipTask would crash.
        if game_data.get("_smart_update"):
            logger.info(f"[Smart Update] _submit_job: using pre-assembled game_data, skipping zip parse for AppID {game_data.get('appid')}")
            # Emit directly with assembled data — Stage 2 will be skipped in _on_zip_parse_complete
            parse_progress = QProgressDialog("Applying manifest...", None, 0, 0, self)
            parse_progress.setWindowModality(Qt.WindowModality.WindowModal)
            parse_progress.setMinimumDuration(0)
            parse_progress.setCancelButton(None)
            parse_progress.show()
            self.zip_parse_complete.emit(game_data, filepath, game_data, dialog, parse_progress)
            return

        # ── Classic path ───────────────────────────────────────────────────
        # Show a transient loading indicator while parsing happens in background
        parse_progress = QProgressDialog("Reading manifest...", None, 0, 0, self)
        parse_progress.setWindowModality(Qt.WindowModality.WindowModal)
        parse_progress.setMinimumDuration(200)  # only show if parse takes > 200 ms
        parse_progress.setCancelButton(None)
        parse_progress.show()

        def _parse_in_background():
            try:
                from core.tasks.process_zip_task import ProcessZipTask
                zip_task = ProcessZipTask()
                parsed = zip_task.run(filepath)
            except Exception as e:
                logger.warning(f"Failed to pre-parse zip for depot selection: {e}", exc_info=True)
                parsed = None
            # Emit the signal — the main-thread slot will close parse_progress safely.
            # Never call parse_progress.close() here; Qt widgets must only be
            # touched from the main thread, and doing so from a background thread
            # will deadlock or corrupt the UI.
            self.zip_parse_complete.emit(parsed, filepath, game_data, dialog, parse_progress)

        self.executor.submit(_parse_in_background)


    def _on_zip_parse_complete(
        self, parsed_data: object, filepath: str, game_data: dict, dialog: QDialog,
        parse_progress: object = None
    ) -> None:
        """Slot called on the main thread when background zip parsing is done.

        parse_progress is closed here (on the main thread) — it must never be
        closed from the background thread that performed the parsing.
        """
        if parse_progress is not None:
            try:
                parse_progress.close()
            except Exception:
                pass
        is_verify = (game_data.get("update_status") != "update_available")
        metadata = {
            "appid": game_data.get("appid"),
            "library_path": game_data.get("library_path"),
            "install_path": game_data.get("install_path"),
            "game_name": game_data.get("game_name", "Unknown"),
            "job_type": "verify" if is_verify else "download",
        }
        # Propagate rollback flag so task_manager won't mark game as up_to_date
        if game_data.get("_is_rollback"):
            metadata["_is_rollback"] = True
            metadata["job_type"] = "verify"

        if parsed_data:
            # Smart Update path: data was assembled by SmartUpdateTask using live PICS +
            # /generate/appmanifest — it's already the latest version. Skip Stage 2 check.
            is_smart = parsed_data.get("_smart_update") or game_data.get("_smart_update")
            if is_smart:
                logger.info(f"[Smart Update] Skipping Stage 2 manifest verification for AppID {game_data.get('appid')} (trust PICS)")
            else:
                # Stage 2 check:
                # Check if the parsed manifest IDs match the latest steam manifest ID we expected,
                # or if they are identical to the previously installed manifest IDs.
                from utils.manifest_verifier import verify_extracted_zip_manifest
                appid = str(game_data.get("appid"))
                is_update = game_data.get("update_status") == "update_available"
                is_valid_stage2, warning_reason = verify_extracted_zip_manifest(appid, parsed_data, is_update=is_update)

                should_warn = False
                if not game_data.get("_is_rollback") and not is_valid_stage2:
                    should_warn = True

                if should_warn:
                    msg = (
                        f"ASSella detected that the manifest is older than the latest Steam version.\n\n"
                        f"Reason: {warning_reason}\n\n"
                        "This usually means Hubcap has not yet ingested the latest Steam update.\n\n"
                        "Do you want to continue with this version anyway?"
                    )
                    btn = QMessageBox.warning(
                        self,
                        "Manifest Outdated",
                        msg,
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No
                    )
                    if btn != QMessageBox.StandardButton.Yes:
                        logger.info("User aborted task due to post-download manifest mismatch")
                        return

        if parsed_data and parsed_data.get("depots"):
            from ui.dialogs.depotselection import DepotSelectionDialog
            from utils.settings import get_settings
            import json
            settings = get_settings()
            auto_skip = settings.value("auto_skip_single_choice", False, type=bool)
            depots = parsed_data.get("depots")
            appid = str(parsed_data["appid"])

            selected_depots = None

            # Smart selection logic
            smart_active = settings.value("smart_depot_selection", False, type=bool)
            val = settings.value(f"depot_selection/{appid}", "", type=str)
            should_prompt = True

            if smart_active and val:
                try:
                    data = json.loads(val)
                    cached_selected = data.get("selected", [])
                    cached_all = data.get("all_available", [])
                    current_depots = list(depots.keys())
                    has_new_depot = any(d not in cached_all for d in current_depots)
                    if not has_new_depot:
                        selected_depots = [d for d in cached_selected if d in depots]
                        should_prompt = False
                        logger.info(f"Smart selection active. Reusing cached depots for {appid}: {selected_depots}")
                except Exception as e:
                    logger.warning(f"Error parsing cached depot selection: {e}")

            if should_prompt:
                if auto_skip and len(depots) == 1:
                    selected_depots = list(depots.keys())
                else:
                    depot_dialog = DepotSelectionDialog(
                        parsed_data["appid"],
                        parsed_data.get("game_name", ""),
                        depots,
                        parsed_data.get("header_url"),
                        self.main_window,
                    )
                    if depot_dialog.exec():
                        selected_depots = depot_dialog.get_selected_depots()

            if selected_depots:
                metadata["selected_depots_list"] = selected_depots
                # Cache the choice
                try:
                    settings.setValue(
                        f"depot_selection/{appid}",
                        json.dumps({
                            "selected": selected_depots,
                            "all_available": list(depots.keys()),
                            "descriptions": {d_id: depots.get(d_id, {}).get("desc", "") for d_id in selected_depots}
                        })
                    )
                except Exception as e:
                    logger.warning(f"Failed to cache depot selection: {e}")
            else:
                # User cancelled depot selection, don't submit job
                logger.info("User cancelled depot selection.")
                if dialog:
                    dialog.accept()
                self.accept()
                return

        self.main_window.job_queue.add_job(filepath, metadata)
        if dialog:
            dialog.accept()
        self.accept()

    def _show_games_list_context_menu(self, pos) -> None:
        """Show context menu for a game item."""
        item = self.games_list.itemAt(pos)
        if not item:
            return

        game_data = item.data(Qt.ItemDataRole.UserRole)
        if not game_data:
            return

        menu = QMenu(self)
        menu.setStyleSheet(
            f"""
            QMenu {{
                background-color: #111111;
                color: #FFFFFF;
                border: 1px solid #333333;
            }}
            QMenu::item:selected {{
                background-color: {self.accent_color};
                color: #000000;
            }}
            """
        )

        verify_action = QAction("Verify Game Files", self)
        verify_action.triggered.connect(lambda: self._fetch_game_manifest(game_data))
        menu.addAction(verify_action)

        open_folder_action = QAction("Open Install Folder", self)
        install_path = game_data.get("install_path")
        open_folder_action.triggered.connect(lambda: self._open_folder(install_path))
        menu.addAction(open_folder_action)

        reset_depots_action = QAction("Reset Depot Selection", self)
        reset_depots_action.triggered.connect(lambda: self._reset_depot_selection(game_data))
        menu.addAction(reset_depots_action)

        uninstall_action = QAction("Uninstall Game", self)
        uninstall_action.triggered.connect(lambda: self._uninstall_game(game_data, None, {}))
        menu.addAction(uninstall_action)

        menu.exec(self.games_list.mapToGlobal(pos))

    @staticmethod
    def _open_folder(path: str) -> None:
        if not path or not os.path.exists(path):
            return
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":
                subprocess.call(["open", path])
            else:
                subprocess.call(["xdg-open", path])
        except OSError:
            pass

    def _confirm_action(self, title: str, message: str) -> bool:
        reply = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _uninstall_game(self, game_data: dict, dialog: QDialog, opts: dict) -> None:
        if not self.game_manager:
            return

        msg = self.game_manager.get_uninstall_confirmation_message(game_data)
        if not self._confirm_action("Confirm Uninstall", msg):
            return

        # Extract boolean states from checkboxes
        c_data = opts.get("compat", False)
        c_saves = opts.get("saves", False)
        c_wipe_sls = opts.get("wipe_sls", False)

        is_dlc_only = False
        appid = str(game_data.get("appid", "0"))
        if appid and appid not in ("0", "N/A", "unknown"):
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc_only = is_dlc_only_mode(appid)

        progress_title = "Uninstalling DLC..." if is_dlc_only else "Uninstalling game..."
        self._uninstall_progress_dialog = QProgressDialog(
            progress_title, None, 0, 0, self
        )
        self._uninstall_progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._uninstall_progress_dialog.show()

        # Start async uninstall
        self.executor.submit(self._uninstall_game_async, game_data, c_data, c_saves, c_wipe_sls)

    def _uninstall_game_async(
        self, game_data: dict, c_data: bool, c_saves: bool, c_wipe_sls: bool = False
    ) -> None:
        """Background task to uninstall game."""
        try:
            # Wipe SLS only: remove from config + .DepotDownloader, leave files intact
            if c_wipe_sls and not c_data and not c_saves:
                success, err = self._wipe_sls_only(game_data)
            else:
                success, err = self.game_manager.uninstall_game(
                    game_data, remove_compatdata=c_data, remove_saves=c_saves,
                    remove_sls=c_wipe_sls
                )
            self.uninstall_complete.emit(success, str(err) if err else "")
        except Exception as e:
            self.uninstall_complete.emit(False, str(e))

    def _on_uninstall_complete(self, success: bool, error: str) -> None:
        """Slot to handle uninstall completion."""
        if self._uninstall_progress_dialog:
            self._uninstall_progress_dialog.close()
            self._uninstall_progress_dialog = None

        if success:
            QMessageBox.information(self, "Success", "Operation completed.")
            if self._details_dialog:
                self._details_dialog.accept()
            self._refresh_game_list()
        else:
            QMessageBox.critical(self, "Error", f"Failed: {error}")

    @staticmethod
    def _wipe_sls_only(game_data: dict) -> tuple:
        """Remove from SLS config and delete .DepotDownloader folder, leave everything else."""
        import shutil
        appid = str(game_data.get("appid", ""))
        install_path = game_data.get("install_path", "")
        errors = []

        # 1. Remove from SLSsteam config
        try:
            from utils.yaml_config_manager import remove_additional_app
            config_path = get_user_config_path()
            if config_path.exists():
                remove_additional_app(config_path, appid)
                logger.info(f"Wipe SLS: removed AppID {appid} from SLS config")
        except Exception as e:
            errors.append(f"SLS config: {e}")

        # 2. Remove .DepotDownloader folder
        if install_path and os.path.isdir(install_path):
            ddm = os.path.join(install_path, ".DepotDownloader")
            if os.path.exists(ddm):
                try:
                    shutil.rmtree(ddm)
                    logger.info(f"Wipe SLS: removed .DepotDownloader from {install_path}")
                except Exception as e:
                    errors.append(f".DepotDownloader: {e}")

        # 3. Remove from game manager list (no file deletion!)
        try:
            from managers.game_manager import GameManager
            # Access via parent window — we're a static method, skip for now
        except Exception:
            pass

        if errors:
            return False, "; ".join(errors)
        return True, None

    def _fix_game_install(self, game_data: dict) -> None:
        path = game_data.get("library_path")
        appid = str(game_data.get("appid", ""))

        if not path or not appid or appid == "0":
            return

        acf = os.path.join(path, "steamapps", f"appmanifest_{appid}.acf")
        if not os.path.exists(acf):
            QMessageBox.warning(self, "Error", "Manifest file not found.")
            return

        if not self._confirm_action(
            "Confirm", "Remove manifest file? Steam will re-verify files."
        ):
            return

        os.remove(acf)
        QMessageBox.information(self, "Done", "Manifest removed.")
        if sys.platform == "linux":
            slssteam_api_send(f"install|{appid}|0")

    @staticmethod
    def _is_goldberg_applied(game_dir: str) -> bool:
        """Check for Goldberg backup files (.valve)."""
        if not game_dir or game_dir == "N/A" or not os.path.exists(game_dir):
            return False

        for root, _, files in os.walk(game_dir):
            for fname in files:
                if fname.lower() in (
                    "steam_api.dll.valve",
                    "steam_api64.dll.valve",
                    "libsteam_api.so.valve",
                    "libsteam_api64.so.valve",
                ):
                    return True
        return False

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes == 0:
            return "0 B"
        size_names = ["B", "KB", "MB", "GB", "TB"]
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_names[i]}"

    def _update_depot_status_label(self, appid: str) -> None:
        if not hasattr(self, "depot_status_lbl") or not self.depot_status_lbl:
            return
        val = self.settings.value(f"depot_selection/{appid}", "", type=str)
        if val:
            try:
                import json
                data = json.loads(val)
                selected = data.get("selected", [])
                descriptions = data.get("descriptions", {})
                
                depot_names = []
                for d_id in selected:
                    desc = descriptions.get(d_id, "")
                    if not desc:
                        fpath = get_base_path() / "hubcap_manifests" / f"accela_fetch_{appid}.zip"
                        if fpath.exists():
                             try:
                                 from core.tasks.process_zip_task import ProcessZipTask
                                 zip_task = ProcessZipTask()
                                 parsed_data = zip_task.run(str(fpath))
                                 desc = parsed_data.get("depots", {}).get(d_id, {}).get("desc", "")
                             except Exception:
                                 pass
                    
                    if desc:
                        import re
                        desc_clean = re.sub(r"\s*-\s*Depot\s*" + re.escape(d_id), "", desc, flags=re.IGNORECASE).strip()
                        depot_names.append(f"{d_id} ({desc_clean})")
                    else:
                        depot_names.append(d_id)
                
                names_str = ", ".join(depot_names)
                self.depot_status_lbl.setText(f"Status: {len(selected)} depot(s) manually chosen:\n{names_str}")
            except Exception:
                self.depot_status_lbl.setText("Status: Error reading saved selection.")
        else:
            self.depot_status_lbl.setText("Status: Default (automatic download of all depots).")

    def _reset_depot_selection(self, game_data: dict) -> None:
        appid = str(game_data.get("appid", ""))
        if not appid or appid in ("0", "N/A", "unknown"):
            return
        
        self.settings.remove(f"depot_selection/{appid}")
        self._update_depot_status_label(appid)
        QMessageBox.information(self, "Reset Successful", "Depot selection has been reset to default.")

    def _configure_depots(self, game_data: dict) -> None:
        appid = str(game_data.get("appid", "0"))
        if appid in ("0", "N/A", "unknown"):
            QMessageBox.warning(self, "Error", "Invalid App ID.")
            return

        name = game_data.get("game_name", "Unknown")
        fpath = get_base_path() / "hubcap_manifests" / f"accela_fetch_{appid}.zip"
        
        if fpath.exists():
            self._show_depot_selection_dialog(str(fpath), game_data)
        else:
            self._download_progress_dialog = QProgressDialog(
                f"Downloading manifest for {name}...", "Cancel", 0, 0, self
            )
            self._download_progress_dialog.setWindowTitle("Downloading Manifest")
            self._download_progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            self._download_progress_dialog.show()

            self._configure_depots_after_download = game_data
            self.executor.submit(self._download_manifest_async, appid, game_data)

    def _show_depot_selection_dialog(self, filepath: str, game_data: dict) -> None:
        try:
            from core.tasks.process_zip_task import ProcessZipTask
            zip_task = ProcessZipTask()
            parsed_data = zip_task.run(filepath)
            
            if parsed_data and parsed_data.get("depots"):
                from ui.dialogs.depotselection import DepotSelectionDialog
                depots = parsed_data.get("depots")
                appid = str(parsed_data["appid"])
                
                saved_val = self.settings.value(f"depot_selection/{appid}", "", type=str)
                selected_depots = None
                if saved_val:
                    try:
                        import json
                        data = json.loads(saved_val)
                        selected_depots = data.get("selected", [])
                    except Exception:
                        pass

                depot_dialog = DepotSelectionDialog(
                    parsed_data["appid"],
                    parsed_data.get("game_name", ""),
                    depots,
                    parsed_data.get("header_url"),
                    self,
                    selected_depots=selected_depots
                )
                if depot_dialog.exec():
                    chosen = depot_dialog.get_selected_depots()
                    if chosen:
                        import json
                        self.settings.setValue(
                            f"depot_selection/{appid}",
                            json.dumps({
                                "selected": chosen,
                                "all_available": list(depots.keys()),
                                "descriptions": {d_id: depots.get(d_id, {}).get("desc", "") for d_id in chosen}
                            })
                        )
                        self._update_depot_status_label(appid)
                        QMessageBox.information(self, "Success", "Depot selection saved successfully.")
                    else:
                        QMessageBox.warning(self, "Warning", "No depots selected. Depot selection not saved.")
        except Exception as e:
            logger.error(f"Failed to show depot selection dialog: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to load depots: {e}")

    def closeEvent(self, event) -> None:
        """Cleanup resources on close."""
        self._closing = True
        for fetcher in self._active_fetchers.values():
            fetcher.stop()
        self._active_fetchers.clear()
        self.executor.shutdown(wait=False)
        super().closeEvent(event)

    def _show_details_for_appid(self, appid: str) -> None:
        if not self.game_manager:
            return
        game_data = self.game_manager.get_game(appid)
        if game_data:
            self._show_game_details_dialog(game_data)

