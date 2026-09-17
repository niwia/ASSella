import logging
import re
import os
import tempfile
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QMessageBox,
    QProgressDialog,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QSizePolicy,
)

from utils.image_fetcher import ImageFetcher
from utils.settings import get_settings
from ui.dialogs.dialog_helpers import create_standard_buttons

try:
    from ui.dialogs.dlc_warning_dialog import show_dlc_mode_warning
except ImportError:
    try:
        from .dlc_warning_dialog import show_dlc_mode_warning
    except ImportError:
        try:
            from dlc_warning_dialog import show_dlc_mode_warning
        except ImportError:
            show_dlc_mode_warning = None

logger = logging.getLogger(__name__)


def _depot_matches_platform(depot_data: dict, platform: str) -> bool:
    """Check if a depot matches the given platform (linux/windows).

    A depot matches if:
    - Its oslist contains the platform name, OR
    - Its description tags contain [PLATFORM], OR
    - It has no oslist set (shared/common depot)
    """
    oslist = (depot_data.get("oslist") or "").lower()
    desc = (depot_data.get("desc") or "").lower()
    platform = platform.lower()

    # No oslist means it's a shared depot (common to all platforms)
    if not oslist:
        return True

    # Check oslist field (can be "windows", "linux", "windows,linux", etc.)
    if platform in oslist:
        return True

    # Check description tags like [LINUX], [WINDOWS]
    if f"[{platform}]" in desc:
        return True

    return False


def _depot_is_macos(depot_data: dict) -> bool:
    """Check if a depot is macOS-only."""
    oslist = (depot_data.get("oslist") or "").lower()
    desc = (depot_data.get("desc") or "").lower()

    # Check oslist
    if oslist in ("macosx", "macos"):
        return True

    # Check description tags
    if "[macos]" in desc or "[macosx]" in desc:
        return True

    return False


def _depot_is_android(depot_data: dict) -> bool:
    """Check if a depot is Android-only."""
    oslist = (depot_data.get("oslist") or "").lower()
    desc = (depot_data.get("desc") or "").lower()

    # Check oslist
    if oslist == "android":
        return True

    # Check description tags
    if "[android]" in desc:
        return True

    return False


def is_bonus_or_media_depot(depot_data: dict) -> bool:
    """Check if depot is soundtrack, wallpaper, artbook, manual, etc."""
    text = (
        (depot_data.get("desc") or "") + " " +
        (depot_data.get("name") or "")
    ).lower()
    
    bonus_keywords = [
        "soundtrack", " ost", "ost ", "(ost)", "[ost]", "original soundtrack", "bonus track",
        "wallpaper", "artbook", "art book", "manual", "guide", "strategy guide",
        "comic", "novel", "goodies", "avatar", "poster", "press kit", "bonus content",
        "extra content", "credits", "dedicated server", "server"
    ]
    for kw in bonus_keywords:
        if kw in text:
            return True
    return False


def get_smart_default_depots(depots: dict, target_platform: str = "linux", language: str = "english") -> list:
    """
    Intelligently pre-select depots for the user:
    1. If target_platform == "linux" and native Linux depots exist -> Target Linux + shared.
       If target_platform == "linux" and NO native Linux depots exist -> Target Windows + shared (for Proton).
       If target_platform == "windows" -> Target Windows + shared.
    2. Exclude macOS-only and Android-only depots.
    3. Exclude soundtracks, wallpapers, artbooks, manuals, bonus media by default.
    4. Exclude 32-bit depots if 64-bit depots exist for the target architecture.
    5. Prioritize selected language (English) if language-specific depots exist.
    6. Safety: fallback to non-macOS/non-Android depots if filtered list is empty.
    """
    if not depots:
        return []

    # Check if there are any native Linux depots
    has_linux = False
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict) or _depot_is_macos(d_data) or _depot_is_android(d_data):
            continue
        oslist = (d_data.get("oslist") or "").lower()
        desc = (d_data.get("desc") or "").lower()
        if "linux" in oslist or "[linux]" in desc:
            has_linux = True
            break

    if target_platform.lower() == "linux":
        active_platform = "linux" if has_linux else "windows"
    else:
        active_platform = "windows"

    # Check if 64-bit depots exist for active platform
    has_64 = False
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict):
            continue
        if not _depot_matches_platform(d_data, active_platform):
            continue
        osarch = str(d_data.get("osarch") or "").lower()
        desc = (d_data.get("desc") or "").lower()
        if osarch == "64" or "64-bit" in desc or "x64" in desc or "64 bit" in desc or "[64]" in desc:
            has_64 = True
            break

    # Check if language-specific depots exist
    has_lang_depots = False
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict):
            continue
        d_lang = (d_data.get("language") or "").lower()
        desc = (d_data.get("desc") or "").lower()
        if d_lang or any(f"[{l}]" in desc for l in ["english", "french", "german", "spanish", "italian", "japanese", "chinese", "russian", "korean"]):
            has_lang_depots = True
            break

    selected = []
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict):
            continue

        # 1. Skip macOS
        if _depot_is_macos(d_data):
            continue

        # 2. Skip bonus/media (soundtracks, wallpapers, artbooks)
        if is_bonus_or_media_depot(d_data):
            continue

        # 3. Match platform (Linux if native exists, else Windows + shared)
        if not _depot_matches_platform(d_data, active_platform):
            continue

        # 4. Filter 32-bit if 64-bit exists
        if has_64:
            osarch = str(d_data.get("osarch") or "").lower()
            desc = (d_data.get("desc") or "").lower()
            is_32 = osarch == "32" or "32-bit" in desc or "x86" in desc or "32 bit" in desc or "[32]" in desc or "[x86]" in desc
            if is_32:
                continue

        # 5. Language filtering if applicable
        if has_lang_depots:
            d_lang = (d_data.get("language") or "").lower()
            desc = (d_data.get("desc") or "").lower()
            if d_lang and d_lang != language.lower() and d_lang != "all":
                continue
            other_langs = ["french", "german", "spanish", "italian", "japanese", "chinese", "russian", "korean", "portuguese", "polish"]
            if language.lower() in other_langs:
                other_langs.remove(language.lower())
            if any(f"[{l}]" in desc for l in other_langs) and f"[{language.lower()}]" not in desc:
                continue

        selected.append(str(d_id))

    # Safety fallback: if everything got filtered out, fallback to basic non-macOS/non-Android matching
    if not selected:
        for d_id, d_data in depots.items():
            if isinstance(d_data, dict) and not _depot_is_macos(d_data) and not _depot_is_android(d_data):
                if _depot_matches_platform(d_data, active_platform):
                    selected.append(str(d_id))

    # If still empty, return all depot keys
    if not selected:
        selected = [str(k) for k in depots.keys()]

    return selected


def format_size(size_bytes):
    if not size_bytes:
        return "0.00 B"
    try:
        bytes_val = int(size_bytes)
        if bytes_val <= 0:
            return "0.00 B"
        for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
            if bytes_val < 1024.0:
                return f"{bytes_val:.2f} {unit}"
            bytes_val /= 1024.0
        return f"{bytes_val:.2f} PiB"
    except Exception:
        return "Unknown"


class NumericTableWidgetItem(QTableWidgetItem):
    def __init__(self, text, sort_value, tier=0):
        super().__init__(text)
        self.sort_value = sort_value
        self.tier = tier

    def __lt__(self, other):
        if isinstance(other, NumericTableWidgetItem):
            tw = self.tableWidget()
            order = tw.horizontalHeader().sortIndicatorOrder() if tw else Qt.SortOrder.AscendingOrder
            if self.tier != other.tier:
                return (self.tier > other.tier) if order == Qt.SortOrder.DescendingOrder else (self.tier < other.tier)
            return self.sort_value < other.sort_value
        return super().__lt__(other)


class ConfigTableWidgetItem(QTableWidgetItem):
    def __init__(self, text, tier=0):
        super().__init__(text)
        self.tier = tier

    def __lt__(self, other):
        if isinstance(other, ConfigTableWidgetItem):
            tw = self.tableWidget()
            order = tw.horizontalHeader().sortIndicatorOrder() if tw else Qt.SortOrder.AscendingOrder
            if self.tier != other.tier:
                return (self.tier > other.tier) if order == Qt.SortOrder.DescendingOrder else (self.tier < other.tier)
            return self.text().lower() < other.text().lower()
        return super().__lt__(other)


class DepotSelectionDialog(QDialog):
    _depots_enriched_signal = pyqtSignal(dict)

    def __new__(cls, *args, **kwargs):
        if cls is not DepotSelectionDialog:
            return super().__new__(cls)

        is_single = kwargs.get("is_single_depot", False)
        depots = kwargs.get("depots")
        if depots is None and len(args) >= 3:
            depots = args[2]

        missing = kwargs.get("missing_hubcap_depots")
        if missing is None and len(args) >= 9:
            missing = args[8]

        # Only route to SingleDepotSelectionDialog if there are NO missing depots from Hubcap.
        # If DLCs/depots are missing, full DepotSelectionDialog MUST be shown so the user sees
        # the greyed out missing depots and their statuses.
        if not missing and (is_single or (isinstance(depots, dict) and len(depots) == 1)):
            from ui.dialogs.single_depot_dialog import SingleDepotSelectionDialog
            return SingleDepotSelectionDialog(*args, **kwargs)

        return super().__new__(cls)

    def __init__(
        self,
        app_id,
        game_name,
        depots,
        header_url=None,
        parent=None,
        selected_depots=None,
        show_storage=True,
        is_single_depot=False,
        missing_hubcap_depots=None,
        missing_depots_info=None,
        library_path=None,
        refetched_depots=None,
        branch="public",
        branches=None,
        current_build_id="",
        **kwargs,
    ):
        super().__init__(parent)
        self._depots_enriched_signal.connect(self._on_depots_enriched)
        self.setWindowTitle("Select Depots to Download")
        self.depots = depots or {}
        self.app_id = app_id
        self.game_name = game_name
        self.header_url = header_url
        self.selected_depots = selected_depots
        self.selected_files = []
        self.show_storage = show_storage
        self.is_single_depot = is_single_depot
        self.preferred_library_path = library_path
        self.refetched_depots = [str(d) for d in (refetched_depots or []) if str(d).strip()]
        self.branch = str(branch or "public")
        self.branches = dict(branches or {"public": {}})
        self.current_build_id = str(current_build_id or "").strip()
        if not self.current_build_id:
            self.current_build_id = self._resolve_local_buildid()
        if not self.current_build_id and self.depots:
            for d_data in self.depots.values():
                if isinstance(d_data, dict) and d_data.get("buildid"):
                    self.current_build_id = str(d_data["buildid"]).strip()
                    break
        self._selected_build_id = self.current_build_id
        self._is_build_pinned = False
        self._manifest_overrides: Dict[str, str] = {}

        if isinstance(missing_hubcap_depots, dict):
            if not missing_depots_info:
                missing_depots_info = missing_hubcap_depots
            self.missing_hubcap_depots = [str(d) for d in missing_hubcap_depots.keys() if str(d).strip()]
        else:
            self.missing_hubcap_depots = [str(d) for d in (missing_hubcap_depots or []) if str(d).strip()]

        self.missing_depots_info = dict(missing_depots_info or {})
        self.selected_storage_path = None
        self.resize(650, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(10)

        self.anchor_row = -1

        # Pre-apply any cached enrichments from database
        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            cached_enrichments = db.get_depot_enrichments(str(self.app_id))
            if cached_enrichments:
                self._apply_depot_enrichments(cached_enrichments)
        except Exception as e:
            logger.debug(f"[DepotSelection] Could not load cached enrichments: {e}")

        # Check if we should hide macOS & Android depots
        try:
            from utils.settings import get_settings
            settings = get_settings()
            self._settings = settings
            self._hide_macos = settings.value("hide_macos_depots", True, type=bool)
            self._hide_android = settings.value("hide_android_depots", True, type=bool)
            self._filter_soundtracks = settings.value("filter_soundtracks", True, type=bool)
            self._hide_artbooks = settings.value("hide_artbooks_depots", True, type=bool)
            self._hide_demos = settings.value("hide_demos_depots", True, type=bool)
            self._hide_tools = settings.value("hide_tools_depots", True, type=bool)
            self._show_hidden_depots = settings.value("show_hidden_depots_in_selector", False, type=bool)
            self.accent_color = settings.value("accent_color", "#C06C84", type=str)
        except Exception:
            self._settings = None
            self._hide_macos = True
            self._hide_android = True
            self._filter_soundtracks = True
            self._hide_artbooks = True
            self._hide_demos = True
            self._hide_tools = True
            self._show_hidden_depots = False
            self.accent_color = "#C06C84"

        # Dynamically generate solid dark container color for selection background
        from utils.color_utils import get_dark_container_color
        sel_bg_hex = get_dark_container_color(self.accent_color)

        # Parse hex to RGB
        hex_c = self.accent_color.lstrip('#')
        try:
            accent_r = int(hex_c[0:2], 16)
            accent_g = int(hex_c[2:4], 16)
            accent_b = int(hex_c[4:6], 16)
        except Exception:
            accent_r, accent_g, accent_b = 192, 108, 132

        # Header Layout
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(15, 10, 15, 10)
        header_layout.setSpacing(15)

        # Thumbnail Label
        self.header_label = QLabel()
        self.header_label.setFixedSize(120, 56)
        self.header_label.setScaledContents(True)
        self.header_label.setStyleSheet("background-color: rgba(255, 255, 255, 0.04); border-radius: 6px;")
        header_layout.addWidget(self.header_label)

        # Title / Info Layout
        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)

        display_title = (
            f"{self.game_name} ({self.app_id})"
            if self.app_id and str(self.app_id) not in ("0", "unknown", "None", "") and f"({self.app_id})" not in str(self.game_name)
            else self.game_name
        )
        self.title_label = QLabel(display_title)
        self.title_label.setStyleSheet("font-size: 13pt; font-weight: bold; color: #FFFFFF;")
        title_layout.addWidget(self.title_label)

        # --- Branch & Builds Row ---
        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)
        controls_row.setContentsMargins(0, 4, 0, 0)

        # Check saved branch if not explicitly given
        if not self.branch or self.branch == "public":
            try:
                from utils.settings import get_settings
                saved_b = get_settings().value(f"selected_branch/{self.app_id}", "", type=str)
                if saved_b:
                    self.branch = saved_b
            except Exception:
                pass

        branch_list = sorted(self.branches.keys(), key=lambda k: (0 if k == "public" else 1, k)) if self.branches else ["public"]
        if "public" not in branch_list:
            branch_list.insert(0, "public")
        if self.branch and self.branch not in branch_list:
            branch_list.append(self.branch)

        has_other_branches = any(b.lower() != "public" for b in branch_list)

        if has_other_branches:
            branch_lbl = QLabel("Branch:")
            branch_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.7);")
            controls_row.addWidget(branch_lbl)

            self.branch_combo = QComboBox()
            self.branch_combo.setFixedHeight(28)
            self.branch_combo.setMinimumWidth(110)
            self.branch_combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: rgba(255, 255, 255, 0.06);
                    border: 1px solid rgba(255, 255, 255, 0.16);
                    border-radius: 6px;
                    color: #FFFFFF;
                    padding: 2px 10px;
                    font-size: 8.5pt;
                    font-weight: 600;
                }}
                QComboBox:hover {{
                    border-color: rgba(255, 255, 255, 0.3);
                    background-color: rgba(255, 255, 255, 0.10);
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 18px;
                }}
                QComboBox QAbstractItemView {{
                    background-color: #1a1c23;
                    color: #FFFFFF;
                    selection-background-color: {self.accent_color};
                    border: 1px solid rgba(255, 255, 255, 0.15);
                }}
            """)
            self.branch_combo.addItems(branch_list)
            self.branch_combo.currentTextChanged.connect(self._on_branch_changed)
            if self.branch in branch_list:
                self.branch_combo.setCurrentText(self.branch)
            controls_row.addWidget(self.branch_combo)
        else:
            self.branch_combo = None
            branch_lbl = QLabel(f"Branch: <b style='color: #FFFFFF;'>{self.branch or 'public'}</b>")
            branch_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.7);")
            controls_row.addWidget(branch_lbl)

        controls_row.addSpacing(4)

        display_bid = self.current_build_id or "Latest"
        self.builds_btn = QPushButton(f"Build: {display_bid}")
        self.builds_btn.setFixedHeight(28)
        self.builds_btn.setMinimumWidth(110)
        self.builds_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_build_btn_style()
        self.builds_btn.clicked.connect(self._on_builds_clicked)
        controls_row.addWidget(self.builds_btn)

        if not self.is_single_depot:
            controls_row.addSpacing(10)
            self.show_hidden_chk = QCheckBox("Show Hidden Depots")
            self.show_hidden_chk.setCursor(Qt.CursorShape.PointingHandCursor)
            self.show_hidden_chk.setChecked(self._show_hidden_depots)
            self.show_hidden_chk.toggled.connect(self._on_show_hidden_toggled)
            self.show_hidden_chk.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.8);")
            controls_row.addWidget(self.show_hidden_chk)
        else:
            self.show_hidden_chk = None

        if self.branch and self.branch != "public":
            self._on_branch_changed(self.branch)

        controls_row.addStretch()
        title_layout.addLayout(controls_row)

        header_layout.addLayout(title_layout)
        header_layout.addStretch()

        layout.addLayout(header_layout)

        # Resolve missing depots info if needed (table will show them at bottom in greyscale)
        if self.missing_hubcap_depots:
            self._resolve_missing_depots_info()

        layout.addSpacing(5)

        self._fetch_header_image(app_id)

        # Load DLC-only mode state
        self._dlc_only_mode = (
            self._settings.value(f"dlc_only_mode/{self.app_id}", False, type=bool)
            if self._settings else False
        )

        # Check if saved depot selections exist from previous sessions
        if self.selected_depots is None and self._settings:
            saved_val = self._settings.value(f"depot_selection/{self.app_id}", "", type=str)
            if saved_val:
                try:
                    import json
                    saved_data = json.loads(saved_val)
                    if saved_data.get("selected"):
                        self.selected_depots = saved_data["selected"]
                except Exception:
                    pass

        self._has_saved_selection = bool(self.selected_depots is not None and len(self.selected_depots) > 0)
        self._user_interacted = False

        content_widget = QVBoxLayout()
        content_widget.setContentsMargins(10, 0, 10, 0)

        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(3)
        self.table_widget.setHorizontalHeaderLabels(["ID", "Configuration", "Size"])
        self.table_widget.verticalHeader().setVisible(False)
        self.table_widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table_widget.setShowGrid(False)
        self.table_widget.setAlternatingRowColors(True)

        self.table_widget.setStyleSheet(f"""
            QTableWidget {{
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.047);
                border-radius: 8px;
                gridline-color: transparent;
                outline: 0;
                color: #FFFFFF;
            }}
            QTableWidget::item {{
                padding: 6px 10px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.02);
            }}
            QTableWidget::item:hover {{
                background-color: rgba({accent_r}, {accent_g}, {accent_b}, 0.047);
            }}
            QTableWidget::item:selected {{
                background-color: {sel_bg_hex} !important;
                color: #FFFFFF !important;
            }}
            QHeaderView::section {{
                background-color: rgba(255, 255, 255, 0.031);
                color: rgba(255, 255, 255, 0.235);
                padding: 6px 10px;
                border: none;
                font-size: 8.5pt;
                font-weight: bold;
                text-transform: uppercase;
            }}
            QTableWidget::indicator, QTableView::indicator {{
                width: 14px;
                height: 14px;
                background: transparent;
                border: 1.5px solid rgba({accent_r}, {accent_g}, {accent_b}, 0.47);
                border-radius: 4px;
            }}
            QTableWidget::indicator:unchecked, QTableView::indicator:unchecked {{
                background-color: transparent;
            }}
            QTableWidget::indicator:checked, QTableView::indicator:checked {{
                background-color: {self.accent_color};
                border: 1.5px solid {self.accent_color};
            }}
            QTableWidget::indicator:hover, QTableView::indicator:hover {{
                border: 1.5px solid rgba({accent_r}, {accent_g}, {accent_b}, 1.0);
                background-color: rgba({accent_r}, {accent_g}, {accent_b}, 0.078);
            }}
        """)

        header = self.table_widget.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table_widget.setColumnWidth(0, 110)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self._hidden_depots_expanded = False
        self._populate_table()

        # Makes list widget update stylesheets for the items
        QApplication.processEvents()
 
        content_widget.addWidget(self.table_widget)
 
        self.table_widget.cellClicked.connect(self.on_depot_cell_clicked)

        if not self.is_single_depot:
            # Platform + Select/Deselect buttons in a single row
            button_layout = QHBoxLayout()
            button_layout.setSpacing(4)

            linux_button = QPushButton("Linux")
            self.linux_button = linux_button
            has_linux = self._has_native_linux_depots()
            linux_button.setEnabled(has_linux)
            if has_linux:
                linux_button.setToolTip("Smart select Linux installation (Native Linux if available; excludes media/32-bit)")
            else:
                linux_button.setToolTip("No native Linux depots available for this game")
            linux_button.clicked.connect(lambda: self._select_platform("linux"))
            button_layout.addWidget(linux_button)

            windows_button = QPushButton("Windows")
            windows_button.setToolTip("Smart select Windows installation (Windows + shared; excludes media/32-bit)")
            windows_button.clicked.connect(lambda: self._select_platform("windows"))
            button_layout.addWidget(windows_button)

            select_all_button = QPushButton("All")
            select_all_button.clicked.connect(
                lambda: self._toggle_all_checkboxes(check=True)
            )
            button_layout.addWidget(select_all_button)

            deselect_all_button = QPushButton("None")
            deselect_all_button.clicked.connect(
                lambda: self._toggle_all_checkboxes(check=False)
            )
            button_layout.addWidget(deselect_all_button)
            content_widget.addLayout(button_layout)

        # Custom File Selection + DLC Only button row
        file_sel_layout = QHBoxLayout()
        file_sel_layout.setSpacing(6)

        select_files_button = QPushButton("Select Files...")
        select_files_button.setToolTip("Customize downloaded files within the selected depots")
        select_files_button.clicked.connect(self._on_select_files_clicked)
        select_files_button.setStyleSheet("font-weight: bold; padding: 4px;")
        file_sel_layout.addWidget(select_files_button)

        self._dlc_only_btn = QPushButton("DLC Only")
        self._dlc_only_btn.setToolTip(
            "Only select this if you own the base game separately.\n"
            "Update checks will only compare the depots you select here."
        )
        self._dlc_only_btn.setCheckable(True)
        self._dlc_only_btn.setChecked(self._dlc_only_mode)
        self._dlc_only_btn.clicked.connect(self._on_dlc_only_toggled)
        self._refresh_dlc_only_style()
        file_sel_layout.addWidget(self._dlc_only_btn)

        content_widget.addLayout(file_sel_layout)

        # Bottom row: Storage Selection (Left, Expanding) + OK / Cancel (Right)
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(0, 4, 0, 0)
        bottom_bar.setSpacing(6)

        if self.show_storage:
            self._setup_storage_buttons(bottom_bar)
        else:
            bottom_bar.addStretch(1)

        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("ok_button")
        ok_btn.setFixedHeight(28)
        ok_btn.setMinimumWidth(80)
        ok_btn.clicked.connect(self.accept)
        ok_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: #111318;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                padding: 4px 14px;
            }}
            QPushButton:hover {{
                background-color: #FFFFFF;
            }}
        """)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancel_button")
        cancel_btn.setFixedHeight(28)
        cancel_btn.setMinimumWidth(80)
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setStyleSheet("""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.05);
                color: rgba(255, 255, 255, 0.8);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                padding: 4px 14px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.1);
                color: #FFFFFF;
            }}
        """)

        bottom_bar.addWidget(ok_btn)
        bottom_bar.addWidget(cancel_btn)

        content_widget.addLayout(bottom_bar)

        layout.addLayout(content_widget)

        # Check if any depot needs enrichment (generic description or missing size)
        needs_enrichment = any(
            (not d_data.get("size") and not d_data.get("size_str"))
            or re.match(r"^(?:\[.*?\]\s*)?(?:Depot|DLC)\s+\d+$", str(d_data.get("desc") or "").strip(), re.IGNORECASE)
            or not str(d_data.get("desc") or "").strip()
            for d_data in self.depots.values()
            if isinstance(d_data, dict)
        )
        if (needs_enrichment or self._dlc_only_mode) and self.app_id and str(self.app_id) not in ("0", "N/A", "unknown"):
            self._start_enrichment_async()
            if self._dlc_only_mode and not self._has_saved_selection and not self._user_interacted:
                self._apply_dlc_auto_selection()

    def _on_show_hidden_toggled(self, checked: bool):
        self._show_hidden_depots = checked
        if hasattr(self, "_settings") and self._settings:
            self._settings.setValue("show_hidden_depots_in_selector", checked)
        self.selected_depots = self.get_selected_depots()
        self._populate_table()

    def _has_native_linux_depots(self) -> bool:
        """Check if any depot explicitly targets Linux."""
        all_depot_dicts = []
        if isinstance(self.depots, dict):
            all_depot_dicts.extend(self.depots.values())
        if hasattr(self, "missing_depots_info") and isinstance(self.missing_depots_info, dict):
            all_depot_dicts.extend(self.missing_depots_info.values())
        for d_data in all_depot_dicts:
            if not isinstance(d_data, dict) or _depot_is_macos(d_data) or _depot_is_android(d_data):
                continue
            oslist = (d_data.get("oslist") or "").lower()
            desc = (d_data.get("desc") or d_data.get("name") or "").lower()
            if "linux" in oslist or "[linux]" in desc:
                return True
        return False

    def _populate_table(self):
        self.table_widget.setSortingEnabled(False)
        self.table_widget.clearContents()

        def get_sort_key(depot_item):
            _depot_id, data = depot_item

            os_val = data.get("oslist")
            if os_val is None:
                os_str = "zzzz"
            else:
                os_str = os_val.lower()

            os_priority = 4

            if os_str == "windows":
                os_priority = 1
            elif os_str == "linux":
                os_priority = 2
            elif "all" in os_str:
                os_priority = 3
            elif os_str == "macosx" or os_str == "macos":
                os_priority = 5

            desc_str = data.get("desc", "").lower()
            lang_val = data.get("language")

            lang_priority = 3
            lang_sort_key = lang_val.lower() if lang_val else "zzzz"

            is_no_language = (
                lang_val is None
                and "english" not in desc_str
                and "japanese" not in desc_str
            )

            if "english" in desc_str:
                lang_priority = 1
                lang_sort_key = lang_val.lower() if lang_val else "english"
            elif is_no_language:
                lang_priority = 1
                lang_sort_key = "english"
            elif "japanese" in desc_str:
                lang_priority = 2
                lang_sort_key = "japanese"

            final_key = (os_priority, lang_priority, lang_sort_key)
            return final_key

        sorted_depots = sorted(self.depots.items(), key=get_sort_key)

        # Separate depots into active (displayed) vs hidden (expandable)
        active_depots = []
        hidden_depots = []

        for depot_id, depot_data in sorted_depots:
            is_hidden = False
            if self._hide_macos and _depot_is_macos(depot_data):
                is_hidden = True
            elif self._hide_android and _depot_is_android(depot_data):
                is_hidden = True
            elif getattr(self, "_filter_soundtracks", True):
                desc_l = (depot_data.get("desc") or "").lower()
                name_l = (depot_data.get("name") or "").lower()
                if "soundtrack" in desc_l or "soundtrack" in name_l or "[ost]" in desc_l or " ost" in desc_l:
                    is_hidden = True
            if not is_hidden and getattr(self, "_hide_artbooks", True):
                desc_l = (depot_data.get("desc") or "").lower()
                name_l = (depot_data.get("name") or "").lower()
                if "artbook" in desc_l or "artbook" in name_l or "wallpaper" in desc_l or "extra content" in desc_l:
                    is_hidden = True
            if not is_hidden and getattr(self, "_hide_demos", True):
                desc_l = (depot_data.get("desc") or "").lower()
                name_l = (depot_data.get("name") or "").lower()
                if "demo" in desc_l or "trial" in desc_l or "playtest" in desc_l:
                    is_hidden = True
            if not is_hidden and getattr(self, "_hide_tools", True):
                desc_l = (depot_data.get("desc") or "").lower()
                name_l = (depot_data.get("name") or "").lower()
                if "dedicated server" in desc_l or "editor" in desc_l or "sdk" in desc_l or " tool" in desc_l:
                    is_hidden = True

            if is_hidden:
                hidden_depots.append((depot_id, depot_data))
            else:
                active_depots.append((depot_id, depot_data))

        # Fallback if all depots were filtered out
        if not active_depots and hidden_depots:
            active_depots = hidden_depots
            hidden_depots = []

        # Determine initial selection: if selected_depots is provided, use it; otherwise compute smart defaults
        if self.selected_depots is not None:
            pre_selected_set = set(str(d) for d in self.selected_depots)
        else:
            pre_selected_set = set(get_smart_default_depots(self.depots, target_platform="linux"))

        def _build_config_text(d_id, d_data, is_first=False):
            original_desc = d_data.get("desc", "")
            original_desc = re.sub(
                r"\s*-\s*Depot\s*" + re.escape(str(d_id)),
                "",
                original_desc,
                flags=re.IGNORECASE,
            )
            tags = ""
            base_desc = original_desc.strip()
            tags_match = re.match(r"^((?:\[.*?]\s*)*)(.*)", original_desc)
            if tags_match:
                tags = tags_match.group(1).strip()
                base_desc = tags_match.group(2).strip()

            is_generic_fallback = bool(
                re.fullmatch(r"Depot \d+", base_desc, re.IGNORECASE)
            )

            if is_first:
                if is_generic_fallback:
                    final_desc = f"{self.game_name}".strip()
                else:
                    final_desc = base_desc
            else:
                if is_generic_fallback:
                    final_desc = ""
                else:
                    final_desc = base_desc

            is_dlc = d_data.get("is_dlc", False) or "[dlc]" in original_desc.lower()
            final_desc = re.sub(r"^DLC\s+\d+\s*-?\s*", "", final_desc, flags=re.IGNORECASE).strip()
            if not final_desc and d_data.get("name"):
                final_desc = d_data["name"]

            oslist = (d_data.get("oslist") or "").lower()
            os_tag = ""
            if is_dlc:
                os_tag = "[DLC]"
            elif oslist == "windows":
                os_tag = "[Windows]"
            elif oslist == "linux":
                os_tag = "[Linux]"
            elif oslist in ("macos", "macosx"):
                os_tag = "[macOS]"
            elif "windows" in oslist and "linux" in oslist:
                os_tag = "[Windows, Linux]"
            elif "all" in oslist:
                os_tag = "[All]"

            display_tags = tags if tags else os_tag
            if display_tags:
                cfg_text = f"{display_tags}  {final_desc}".strip()
            else:
                cfg_text = final_desc.strip()

            if not cfg_text or cfg_text == display_tags.strip():
                cfg_text = f"{display_tags}  Depot {d_id}".strip() if display_tags else f"Depot {d_id}"

            return cfg_text

        include_hidden = getattr(self, "_show_hidden_depots", False) and bool(hidden_depots)
        total_rows = len(active_depots) + len(self.missing_hubcap_depots)
        if include_hidden:
            total_rows += 1 + len(hidden_depots)
        self.table_widget.setRowCount(total_rows)

        row_idx = 0
        is_first_depot = True

        # 1. Tier 0: Populate Active Depots
        for depot_id, depot_data in active_depots:
            config_text = _build_config_text(depot_id, depot_data, is_first=is_first_depot)
            is_first_depot = False

            size_str = ""
            if not depot_data.get("size") and isinstance(depot_data.get("manifests"), dict):
                manifests = depot_data["manifests"]
                b_entry = manifests.get(self.branch) or manifests.get("public")
                if isinstance(b_entry, dict):
                    raw_fb = b_entry.get("size") or b_entry.get("download")
                    if raw_fb:
                        depot_data["size"] = raw_fb

            if depot_data.get("size"):
                try:
                    size_bytes = int(depot_data["size"])
                    size_str = format_size(size_bytes)
                except (ValueError, TypeError):
                    size_str = "0.00 B"
            elif depot_data.get("size_str"):
                size_str = depot_data["size_str"]

            id_val = int(depot_id) if str(depot_id).isdigit() else 0
            id_item = NumericTableWidgetItem(str(depot_id), id_val, tier=0)
            id_item.setData(Qt.ItemDataRole.UserRole, str(depot_id))
            id_item.setData(Qt.ItemDataRole.UserRole + 1, str(depot_id))
            id_item.setData(Qt.ItemDataRole.UserRole + 2, "normal")

            is_checked = True if self.is_single_depot else (str(depot_id) in pre_selected_set)
            id_item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsUserCheckable)

            if str(depot_id) in getattr(self, "refetched_depots", []):
                config_text = f"[Refetched]  {config_text}"

            config_item = ConfigTableWidgetItem(config_text, tier=0)
            config_item.setFlags(config_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            raw_size = int(depot_data.get("size") or 0)
            size_item = NumericTableWidgetItem(size_str, raw_size, tier=0)
            size_item.setFlags(size_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            self.table_widget.setItem(row_idx, 0, id_item)
            self.table_widget.setItem(row_idx, 1, config_item)
            self.table_widget.setItem(row_idx, 2, size_item)
            row_idx += 1

        # 2. Tier 1: Populate Missing Depots from Hubcap (Darker Greyscale, Completely Untoggleable & Unclickable)
        darker_grey = QColor(135, 135, 135)
        for did in self.missing_hubcap_depots:
            minfo = self.missing_depots_info.get(did, {})
            mname = minfo.get("name")
            mraw_size = int(minfo.get("size") or 0)
            if not mraw_size and isinstance(minfo.get("manifests"), dict):
                manifests = minfo["manifests"]
                b_entry = manifests.get(self.branch) or manifests.get("public")
                if isinstance(b_entry, dict):
                    mraw_size = int(b_entry.get("size") or b_entry.get("download") or 0)
                if not mraw_size:
                    for m_val in manifests.values():
                        if isinstance(m_val, dict) and (m_val.get("size") or m_val.get("download")):
                            mraw_size = int(m_val.get("size") or m_val.get("download") or 0)
                            break
            msize_str = format_size(mraw_size) if mraw_size > 0 else "0 B"
            hubcap_status = minfo.get("hubcap_status")
            if hubcap_status in ("not_found", "404"):
                tag = "[Unavailable on Hubcap (404)]"
            else:
                tag = "[Missing from Hubcap]"

            if mname:
                mconfig_text = f"{tag}  {mname}"
            else:
                mconfig_text = f"{tag}  Depot {did}"

            mid_val = int(did) if str(did).isdigit() else 0
            mid_item = NumericTableWidgetItem(str(did), mid_val, tier=1)
            mid_item.setData(Qt.ItemDataRole.UserRole, str(did))
            mid_item.setData(Qt.ItemDataRole.UserRole + 1, str(did))
            mid_item.setData(Qt.ItemDataRole.UserRole + 2, "missing")
            mid_item.setCheckState(Qt.CheckState.Unchecked)
            mid_item.setFlags(Qt.ItemFlag.NoItemFlags)
            mid_item.setForeground(darker_grey)

            mconfig_item = ConfigTableWidgetItem(mconfig_text, tier=1)
            mconfig_item.setFlags(Qt.ItemFlag.NoItemFlags)
            mconfig_item.setForeground(darker_grey)

            msize_item = NumericTableWidgetItem(msize_str, mraw_size, tier=1)
            msize_item.setFlags(Qt.ItemFlag.NoItemFlags)
            msize_item.setForeground(darker_grey)

            self.table_widget.setItem(row_idx, 0, mid_item)
            self.table_widget.setItem(row_idx, 1, mconfig_item)
            self.table_widget.setItem(row_idx, 2, msize_item)
            row_idx += 1

        # 3. Tier 2 & 3: Populate Hidden Depots if enabled (Expander + Hidden Rows)
        brighter_grey = QColor(195, 195, 195)
        if include_hidden:
            is_expanded = getattr(self, "_hidden_depots_expanded", False)
            exp_id = NumericTableWidgetItem("▴" if is_expanded else "▾", 0, tier=2)
            exp_id.setData(Qt.ItemDataRole.UserRole, "__expander__")
            exp_id.setData(Qt.ItemDataRole.UserRole + 2, "expander")
            exp_id.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            exp_id.setForeground(brighter_grey)
            font_id = exp_id.font()
            font_id.setBold(True)
            exp_id.setFont(font_id)

            exp_cfg = ConfigTableWidgetItem(f"Hidden Depots ({len(hidden_depots)})", tier=2)
            exp_cfg.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            exp_cfg.setForeground(brighter_grey)
            font_cfg = exp_cfg.font()
            font_cfg.setBold(True)
            exp_cfg.setFont(font_cfg)

            action_text = "[Click to collapse]" if is_expanded else "[Click to expand]"
            exp_size = NumericTableWidgetItem(action_text, 0, tier=2)
            exp_size.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            exp_size.setForeground(brighter_grey)
            font_size = exp_size.font()
            font_size.setItalic(True)
            exp_size.setFont(font_size)

            self.table_widget.setItem(row_idx, 0, exp_id)
            self.table_widget.setItem(row_idx, 1, exp_cfg)
            self.table_widget.setItem(row_idx, 2, exp_size)
            row_idx += 1

            # Hidden depot rows (Tier 3)
            for depot_id, depot_data in hidden_depots:
                hconfig_text = _build_config_text(depot_id, depot_data, is_first=False)
                hsize_str = ""
                if depot_data.get("size"):
                    try:
                        hsize_bytes = int(depot_data["size"])
                        hsize_str = format_size(hsize_bytes)
                    except (ValueError, TypeError):
                        hsize_str = "0.00 B"
                elif depot_data.get("size_str"):
                    hsize_str = depot_data["size_str"]

                hid_val = int(depot_id) if str(depot_id).isdigit() else 0
                hid_item = NumericTableWidgetItem(str(depot_id), hid_val, tier=3)
                hid_item.setData(Qt.ItemDataRole.UserRole, str(depot_id))
                hid_item.setData(Qt.ItemDataRole.UserRole + 1, str(depot_id))
                hid_item.setData(Qt.ItemDataRole.UserRole + 2, "hidden_depot")
                is_h_checked = str(depot_id) in pre_selected_set
                hid_item.setCheckState(Qt.CheckState.Checked if is_h_checked else Qt.CheckState.Unchecked)
                hid_item.setFlags(hid_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsUserCheckable)
                hid_item.setForeground(brighter_grey)

                hconfig_item = ConfigTableWidgetItem(hconfig_text, tier=3)
                hconfig_item.setFlags(hconfig_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                hconfig_item.setForeground(brighter_grey)

                hraw_size = int(depot_data.get("size") or 0)
                hsize_item = NumericTableWidgetItem(hsize_str, hraw_size, tier=3)
                hsize_item.setFlags(hsize_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                hsize_item.setForeground(brighter_grey)

                self.table_widget.setItem(row_idx, 0, hid_item)
                self.table_widget.setItem(row_idx, 1, hconfig_item)
                self.table_widget.setItem(row_idx, 2, hsize_item)
                self.table_widget.setRowHidden(row_idx, not is_expanded)
                row_idx += 1

        self.table_widget.setRowCount(row_idx)
        self.table_widget.setSortingEnabled(True)

    def _resolve_missing_depots_info(self):
        """Resolves metadata (name, size, oslist) for missing_hubcap_depots (fast local DB first, async network)."""
        if not self.missing_hubcap_depots:
            return

        if not hasattr(self, "missing_depots_info") or self.missing_depots_info is None:
            self.missing_depots_info = {}

        db_depots = {}
        cached_enrichments = {}
        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            if self.app_id:
                app_info = db.get_app_info(str(self.app_id))
                if app_info and isinstance(app_info, dict):
                    db_depots = app_info.get("depots") or {}
                cached_enrichments = db.get_depot_enrichments(str(self.app_id)) or {}
        except Exception as e:
            logger.debug(f"[DepotSelection] Error loading DB info for missing depots: {e}")

        missing_to_fetch = []
        for did in self.missing_hubcap_depots:
            did_str = str(did)
            if did_str not in self.missing_depots_info:
                self.missing_depots_info[did_str] = {}

            info = self.missing_depots_info[did_str]

            if did_str in db_depots and isinstance(db_depots[did_str], dict):
                src = db_depots[did_str]
                if not info.get("size"):
                    sz = src.get("size")
                    if not sz and isinstance(src.get("manifests"), dict):
                        manifests = src["manifests"]
                        b_entry = manifests.get(self.branch) or manifests.get("public")
                        if isinstance(b_entry, dict):
                            sz = b_entry.get("size") or b_entry.get("download")
                        if not sz:
                            for m_val in manifests.values():
                                if isinstance(m_val, dict) and (m_val.get("size") or m_val.get("download")):
                                    sz = m_val.get("size") or m_val.get("download")
                                    break
                    if sz:
                        info["size"] = sz
                if not info.get("name") and src.get("name"):
                    info["name"] = src["name"]
                if not info.get("oslist") and src.get("oslist"):
                    info["oslist"] = src["oslist"]

            if did_str in cached_enrichments and isinstance(cached_enrichments[did_str], dict):
                src = cached_enrichments[did_str]
                if not info.get("name") and src.get("name"):
                    info["name"] = src["name"]
                if not info.get("size") and src.get("size_bytes"):
                    info["size"] = src["size_bytes"]
                if not info.get("oslist") and src.get("oslist"):
                    info["oslist"] = src["oslist"]

            if not info.get("name") or not info.get("size"):
                missing_to_fetch.append(did_str)

        if missing_to_fetch:
            self._start_missing_depots_fetch_async(missing_to_fetch)

    def _start_missing_depots_fetch_async(self, dids: list):
        """Asynchronously queries steamcmd for any missing depots that lacked local metadata."""
        import threading
        def _worker():
            try:
                from core.steam_api import fetch_steamcmd_info
                updated = False
                for did_str in dids:
                    scmd = fetch_steamcmd_info(str(did_str))
                    if scmd and isinstance(scmd, dict):
                        info = self.missing_depots_info.setdefault(str(did_str), {})
                        if not info.get("name") and scmd.get("name"):
                            info["name"] = scmd["name"]
                            updated = True
                        if not info.get("size") and scmd.get("size"):
                            info["size"] = scmd["size"]
                            updated = True
                if updated:
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(0, self._on_missing_depots_updated)
            except Exception as e:
                logger.debug(f"[DepotSelection] Async missing depots fetch error: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_missing_depots_updated(self):
        """Called on the main thread when async steamcmd metadata resolution finishes."""
        if hasattr(self, "table_widget") and self.table_widget:
            for row in range(self.table_widget.rowCount()):
                id_item = self.table_widget.item(row, 0)
                if id_item and id_item.data(Qt.ItemDataRole.UserRole + 2) == "missing":
                    did = str(id_item.data(Qt.ItemDataRole.UserRole))
                    minfo = self.missing_depots_info.get(did, {})
                    mname = minfo.get("name")
                    mraw_size = int(minfo.get("size") or 0)
                    if not mraw_size and isinstance(minfo.get("manifests"), dict):
                        manifests = minfo["manifests"]
                        b_entry = manifests.get(self.branch) or manifests.get("public")
                        if isinstance(b_entry, dict):
                            mraw_size = int(b_entry.get("size") or b_entry.get("download") or 0)
                        if not mraw_size:
                            for m_val in manifests.values():
                                if isinstance(m_val, dict) and (m_val.get("size") or m_val.get("download")):
                                    mraw_size = int(m_val.get("size") or m_val.get("download") or 0)
                                    break
                    msize_str = format_size(mraw_size) if mraw_size > 0 else "0 B"
                    hubcap_status = minfo.get("hubcap_status")
                    if hubcap_status in ("not_found", "404"):
                        tag = "[Unavailable on Hubcap (404)]"
                    else:
                        tag = "[Missing from Hubcap]"
                    cfg_text = f"{tag}  {mname}" if mname else f"{tag}  Depot {did}"
                    cfg_item = self.table_widget.item(row, 1)
                    if cfg_item:
                        cfg_item.setText(cfg_text)
                    sz_item = self.table_widget.item(row, 2)
                    if sz_item:
                        sz_item.setText(msize_str)
                        if hasattr(sz_item, "sort_value"):
                            sz_item.sort_value = mraw_size

            if hasattr(self, "linux_button") and self.linux_button:
                has_linux = self._has_native_linux_depots()
                self.linux_button.setEnabled(has_linux)
                if has_linux:
                    self.linux_button.setToolTip("Smart select Linux installation (Native Linux if available; excludes media/32-bit)")
                else:
                    self.linux_button.setToolTip("No native Linux depots available for this game")

    def _apply_depot_enrichments(self, enrichments: dict):
        """Merges enriched metadata into self.depots dictionary."""
        try:
            if enrichments and self.depots:
                for did, info in enrichments.items():
                    if did in self.depots and isinstance(self.depots[did], dict):
                        d_data = self.depots[did]
                        curr_desc = str(d_data.get("desc") or "").strip()
                        is_generic = (
                            not curr_desc
                            or bool(re.match(r"^(?:\[.*?\]\s*)?Depot \d+$", curr_desc, re.IGNORECASE))
                            or bool(re.match(r"^(?:\[.*?\]\s*)?DLC \d+$", curr_desc, re.IGNORECASE))
                        )
                        if is_generic and info.get("name"):
                            if info.get("is_dlc"):
                                d_data["desc"] = f"[DLC] {info['name']}"
                            else:
                                d_data["desc"] = info["name"]
                            d_data["name"] = info["name"]
                        if (not d_data.get("size") or d_data.get("size") == 0) and info.get("size_bytes"):
                            d_data["size"] = info["size_bytes"]
                        if info.get("size_str"):
                            d_data["size_str"] = info["size_str"]
                        if info.get("oslist") and not d_data.get("oslist"):
                            d_data["oslist"] = info["oslist"]
                        if info.get("is_dlc"):
                            d_data["is_dlc"] = True
        finally:
            if hasattr(self, "linux_button") and self.linux_button:
                has_linux = self._has_native_linux_depots()
                self.linux_button.setEnabled(has_linux)
                if has_linux:
                    self.linux_button.setToolTip("Smart select Linux installation (Native Linux if available; excludes media/32-bit)")
                else:
                    self.linux_button.setToolTip("No native Linux depots available for this game")

    def _start_enrichment_async(self, force: bool = False):
        """Asynchronously queries DB / SteamDB / Store API to enrich any generic or sizeless depots."""
        if not self.app_id or str(self.app_id) in ("0", "N/A", "unknown"):
            return

        # 1. Fast local SQLite cache check (0ms)
        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            cached_enrichments = db.get_depot_enrichments(str(self.app_id))
            if cached_enrichments:
                self._depots_enriched_signal.emit(cached_enrichments)
                if not force:
                    return
        except Exception as e:
            logger.debug(f"[DepotSelection] DB cache enrichment lookup error: {e}")

        import threading

        def _worker():
            try:
                from core.steamdb_scraper import ByparrManager, SteamDBScraper
                from managers.db_manager import DatabaseManager
                scraper = SteamDBScraper()
                depots_info = {}

                # Try SteamDB via Byparr if running
                if ByparrManager.is_running():
                    depots_info = scraper.get_app_depots(str(self.app_id))

                # Fast fallback: if SteamDB returned empty or Byparr is not running, query Steam Store API
                if not depots_info:
                    depots_info = scraper._fetch_depots_fallback_steam_store(str(self.app_id))

                if depots_info:
                    db = DatabaseManager()
                    db.save_depot_enrichments(str(self.app_id), depots_info)
                    self._depots_enriched_signal.emit(depots_info)
            except Exception as e:
                logger.debug(f"[DepotSelection] Background depot enrichment failed: {e}")

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _on_depots_enriched(self, depots_info: dict):
        """Live-updates the table widget with enriched names, DLC tags, and sizes."""
        if not depots_info or not hasattr(self, "table_widget"):
            return

        self._apply_depot_enrichments(depots_info)

        for row in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(row, 0)
            if not id_item:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role == "expander":
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            if depot_id in depots_info:
                info = depots_info[depot_id]
                config_item = self.table_widget.item(row, 1)
                size_item = self.table_widget.item(row, 2)

                if role == "missing":
                    curr_config = config_item.text().strip() if config_item else ""
                    if info.get("name") and "Depot " in curr_config:
                        config_item.setText(f"[Missing from Hubcap]  {info['name']}")
                else:
                    curr_config = config_item.text().strip() if config_item else ""
                    clean_curr = re.sub(r"^\[.*?\]\s*", "", curr_config).strip()
                    if not clean_curr or re.match(r"^(?:Depot|DLC)\s+\d+$", clean_curr, re.IGNORECASE):
                        name = info.get("name", "")
                        if name:
                            tag = "[DLC]" if info.get("is_dlc") else (f"[{info['oslist'].upper()}]" if info.get("oslist") else "")
                            new_text = f"{tag}  {name}".strip() if tag else name
                            if config_item:
                                config_item.setText(new_text)

                # Update size if missing
                curr_size = size_item.text().strip() if size_item else ""
                if not curr_size or curr_size in ("0.00 B", "No size", ""):
                    if info.get("size_str") and info.get("size_str") != "No size":
                        if size_item:
                            size_item.setText(info["size_str"])
                            if hasattr(size_item, "sort_value"):
                                size_item.sort_value = int(info.get("size_bytes") or 0)

        if getattr(self, "_dlc_only_mode", False) and not getattr(self, "_has_saved_selection", False) and not getattr(self, "_user_interacted", False):
            self._apply_dlc_auto_selection()

    def _apply_dlc_auto_selection(self):
        """
        Auto-selects DLC depots when DLC-only mode is active.
        Rules:
        1. Non-DLC (base game / redist) depots are unchecked.
        2. If DLC depots exist for both Windows and Linux, do NOT auto-select (leave unchecked).
        3. If total DLC count >= 64, auto-select ALL DLC depots (bypass the >1KB condition).
        4. If total DLC count < 64, auto-select DLC depots with size > 1024 bytes (skip stubs <= 1KB).
        """
        if not getattr(self, "_dlc_only_mode", False):
            return
        if getattr(self, "_has_saved_selection", False) or getattr(self, "_user_interacted", False):
            return

        from utils.dlc_helpers import is_base_game_main_depot

        dlc_depot_ids = set()
        has_win_dlc = False
        has_linux_dlc = False

        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if not id_item:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander"):
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            d_data = self.depots.get(depot_id) or self.depots.get(int(depot_id) if depot_id.isdigit() else 0) or {}

            desc = str(d_data.get("desc") or d_data.get("name") or "")
            cfg_item = self.table_widget.item(i, 1)
            cfg_text = cfg_item.text() if cfg_item else ""

            is_dlc = (
                d_data.get("is_dlc", False)
                or "[dlc]" in desc.lower()
                or "[dlc]" in cfg_text.lower()
                or bool(re.search(r"\bDLC\s+\d+", desc, re.IGNORECASE))
                or bool(re.search(r"\bDLC\s+\d+", cfg_text, re.IGNORECASE))
                or bool(d_data.get("dlcappid"))
            )

            if is_base_game_main_depot(depot_id, desc, str(self.app_id)):
                is_dlc = False

            if is_dlc:
                dlc_depot_ids.add(depot_id)
                oslist = (d_data.get("oslist") or "").lower()
                if not oslist and "[windows" in cfg_text.lower():
                    oslist = "windows"
                elif not oslist and "[linux" in cfg_text.lower():
                    oslist = "linux"

                if "windows" in oslist:
                    has_win_dlc = True
                if "linux" in oslist:
                    has_linux_dlc = True

        os_conflict = (has_win_dlc and has_linux_dlc)
        if os_conflict:
            logger.info(
                f"[DepotSelection] App {self.app_id}: DLC depots detected for both Windows and Linux — "
                "bypassing auto-selection so user can pick target platform manually."
            )

        total_dlcs = len(dlc_depot_ids)
        bypass_size_limit = (total_dlcs >= 64)

        self.table_widget.blockSignals(True)
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if not id_item:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander"):
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            d_data = self.depots.get(depot_id) or self.depots.get(int(depot_id) if depot_id.isdigit() else 0) or {}

            if depot_id in dlc_depot_ids:
                if os_conflict:
                    id_item.setCheckState(Qt.CheckState.Unchecked)
                elif bypass_size_limit:
                    id_item.setCheckState(Qt.CheckState.Checked)
                else:
                    raw_size = int(d_data.get("size") or 0)
                    if not raw_size:
                        size_item = self.table_widget.item(i, 2)
                        raw_size = getattr(size_item, "sort_value", 0) or 0

                    if 0 < raw_size <= 1024:
                        id_item.setCheckState(Qt.CheckState.Unchecked)
                    else:
                        id_item.setCheckState(Qt.CheckState.Checked)
            else:
                id_item.setCheckState(Qt.CheckState.Unchecked)

        self.table_widget.blockSignals(False)
        self.anchor_row = -1

    def _setup_storage_buttons(self, layout: QHBoxLayout) -> None:
        import shutil
        from core.steam_helpers import get_steam_libraries, find_steam_install
        from utils.paths import is_valid_download_directory

        storage_paths = []
        def_dir = self._settings.value("default_download_directory", "", type=str) if self._settings else ""
        if def_dir and is_valid_download_directory(def_dir):
            # Option A: User configured a custom/default download directory in Settings.
            # Show ONLY this single configured storage path taking the full width.
            storage_paths.append(os.path.realpath(def_dir))
        else:
            try:
                raw_libs = get_steam_libraries() or []
                for p in raw_libs:
                    if p and is_valid_download_directory(p):
                        real_p = os.path.realpath(p)
                        if real_p not in storage_paths:
                            storage_paths.append(real_p)
            except Exception as e:
                logger.warning(f"Error discovering Steam storage libraries: {e}")

        self._storage_paths = storage_paths
        self._storage_btn_group = QButtonGroup(self)
        self._storage_btn_group.setExclusive(True)

        if not storage_paths:
            layout.addStretch(1)
            return

        def _format_storage_info(path_str: str):
            p = Path(path_str)
            try:
                free_bytes = shutil.disk_usage(path_str).free
                if free_bytes >= 1024**4:
                    free_str = f"{free_bytes / (1024**4):.1f} TB free"
                elif free_bytes >= 1024**3:
                    free_str = f"{free_bytes / (1024**3):.1f} GB free"
                elif free_bytes >= 1024**2:
                    free_str = f"{free_bytes / (1024**2):.0f} MB free"
                else:
                    free_str = f"{free_bytes} B free"
            except Exception:
                free_str = ""

            steam_root = find_steam_install()
            p_str_lower = path_str.lower()

            if steam_root and os.path.realpath(path_str) == os.path.realpath(steam_root):
                label = "Primary"
            elif "/.local/share/steam" in p_str_lower or "/.steam/steam" in p_str_lower:
                label = "Primary"
            elif "sdcard" in p_str_lower or "sd_card" in p_str_lower or "mmcblk" in p_str_lower or "/sd" in p_str_lower:
                label = "SD Card"
            else:
                label = p.name
                if label.lower() in ("steamlibrary", "steamapps", "common") and len(p.parts) > 1:
                    label = p.parts[-2]
                if len(label) > 16:
                    label = label[:14] + "…"

            tooltip = f"Storage: {path_str}" + (f"\nAvailable: {free_str}" if free_str else "")
            return label, free_str, tooltip

        def _get_storage_btn_style():
            from utils.color_utils import get_best_foreground_color
            text_hex = get_best_foreground_color(self.accent_color, dark_color="#111318", light_color="#FFFFFF")
            return f"""
                QPushButton {{
                    background-color: transparent;
                    color: rgba(255, 255, 255, 0.7);
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-size: 8.5pt;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    border-color: {self.accent_color};
                    color: {self.accent_color};
                }}
                QPushButton:checked {{
                    background-color: {self.accent_color} !important;
                    color: {text_hex} !important;
                    border: 1px solid {self.accent_color} !important;
                    font-weight: bold;
                }}
            """

        self._storage_buttons = {}
        self._more_storage_combo = None

        total_storages = len(storage_paths)
        max_direct_buttons = 3

        for i in range(min(total_storages, max_direct_buttons)):
            spath = storage_paths[i]
            lbl_text, free_str, tip_text = _format_storage_info(spath)
            btn_text = f"{lbl_text} ({free_str})" if (total_storages <= 2 and free_str) else lbl_text
            btn = QPushButton(btn_text)
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setToolTip(tip_text)
            btn.setStyleSheet(_get_storage_btn_style())
            self._storage_btn_group.addButton(btn, i)
            self._storage_buttons[spath] = btn
            layout.addWidget(btn, 1)

        def _on_storage_btn_clicked(btn_id: int):
            if 0 <= btn_id < len(storage_paths):
                target_path = storage_paths[btn_id]
                self.selected_storage_path = target_path
                logger.info(f"Storage library selected via button [{btn_id}]: {target_path}")
                if self._more_storage_combo:
                    self._more_storage_combo.blockSignals(True)
                    self._more_storage_combo.setCurrentIndex(0)
                    self._more_storage_combo.blockSignals(False)

        self._storage_btn_group.idClicked.connect(_on_storage_btn_clicked)

        if len(storage_paths) > max_direct_buttons:
            self._more_storage_combo = QComboBox()
            self._more_storage_combo.setFixedHeight(28)
            self._more_storage_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._more_storage_combo.addItem("More Drives ▾", None)
            self._more_storage_combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: transparent;
                    color: rgba(255, 255, 255, 0.7);
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 4px;
                    padding: 2px 8px;
                    font-size: 8.5pt;
                }}
                QComboBox:hover {{
                    border-color: {self.accent_color};
                    color: {self.accent_color};
                }}
                QComboBox QAbstractItemView {{
                    background-color: #1a1a24;
                    color: #FFFFFF;
                    selection-background-color: {self.accent_color};
                    border: 1px solid rgba(255, 255, 255, 0.15);
                }}
            """)
            for i in range(max_direct_buttons, len(storage_paths)):
                spath = storage_paths[i]
                lbl_text, tip_text = _format_storage_info(spath)
                self._more_storage_combo.addItem(lbl_text, spath)

            def _on_combo_changed(index):
                if index > 0:
                    chosen_path = self._more_storage_combo.itemData(index)
                    if chosen_path:
                        self.selected_storage_path = chosen_path
                        logger.info(f"Storage library selected via dropdown: {chosen_path}")
                        checked_btn = self._storage_btn_group.checkedButton()
                        if checked_btn:
                            self._storage_btn_group.setExclusive(False)
                            checked_btn.setChecked(False)
                            self._storage_btn_group.setExclusive(True)

            self._more_storage_combo.currentIndexChanged.connect(_on_combo_changed)
            layout.addWidget(self._more_storage_combo, 1)

        # Check if the game is already installed in any of the detected Steam libraries
        installed_target = None
        if self.app_id and str(self.app_id) not in ("0", "N/A", "unknown"):
            parent = self.parent()
            gm = getattr(parent, "game_manager", None) if parent else None
            if gm and hasattr(gm, "get_game"):
                igame = gm.get_game(str(self.app_id))
                if igame and igame.get("library_path"):
                    real_installed = os.path.realpath(igame["library_path"])
                    if real_installed in storage_paths:
                        installed_target = real_installed
                        logger.info(f"[DepotSelection] Pre-selecting existing install library for AppID {self.app_id}: {installed_target}")

        # Pre-select priority:
        # 1. Explicitly preferred library path (from Zip confirmation or job metadata)
        # 2. Existing install library
        # 3. Configured default directory (if valid and in storage_paths)
        # 4. First storage library
        real_pref = os.path.realpath(self.preferred_library_path) if self.preferred_library_path else ""
        real_def = os.path.realpath(def_dir) if def_dir else ""
        if real_pref and real_pref in storage_paths:
            default_target = real_pref
            logger.info(f"[DepotSelection] Pre-selecting preferred library path: {default_target}")
        elif installed_target:
            default_target = installed_target
        elif real_def and real_def in storage_paths:
            default_target = real_def
        else:
            default_target = storage_paths[0]

        self.selected_storage_path = default_target
        if default_target in self._storage_buttons:
            self._storage_buttons[default_target].setChecked(True)
        elif self._more_storage_combo:
            for idx in range(1, self._more_storage_combo.count()):
                if self._more_storage_combo.itemData(idx) == default_target:
                    self._more_storage_combo.setCurrentIndex(idx)
                    break

    def on_depot_cell_clicked(self, row, col):
        id_item = self.table_widget.item(row, 0)
        if id_item is None:
            return

        role = id_item.data(Qt.ItemDataRole.UserRole + 2)
        if role == "missing":
            return

        if role == "expander":
            self._hidden_depots_expanded = not getattr(self, "_hidden_depots_expanded", False)
            arrow = "▴" if self._hidden_depots_expanded else "▾"
            action_text = "[Click to collapse]" if self._hidden_depots_expanded else "[Click to expand]"

            id_item.setText(arrow)
            size_item = self.table_widget.item(row, 2)
            if size_item:
                size_item.setText(action_text)

            self.table_widget.blockSignals(True)
            for r in range(self.table_widget.rowCount()):
                it = self.table_widget.item(r, 0)
                if it and it.data(Qt.ItemDataRole.UserRole + 2) == "hidden_depot":
                    self.table_widget.setRowHidden(r, not self._hidden_depots_expanded)
            self.table_widget.blockSignals(False)
            return

        modifiers = QApplication.keyboardModifiers()
        current_state = id_item.checkState()
        new_state = (
            Qt.CheckState.Unchecked
            if current_state == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

        # Toggle the checkbox in the first column
        id_item.setCheckState(new_state)
        self._user_interacted = True
        self._has_saved_selection = True

        if modifiers == Qt.KeyboardModifier.ShiftModifier:
            if self.anchor_row == -1:
                self.anchor_row = row
            else:
                anchor_item = self.table_widget.item(self.anchor_row, 0)
                target_state = anchor_item.checkState() if anchor_item else new_state

                start_row = min(self.anchor_row, row)
                end_row = max(self.anchor_row, row)

                self.table_widget.blockSignals(True)
                for r in range(start_row, end_row + 1):
                    r_item = self.table_widget.item(r, 0)
                    if r_item is not None:
                        r_role = r_item.data(Qt.ItemDataRole.UserRole + 2)
                        if r_role in ("normal", "hidden_depot"):
                            r_item.setCheckState(target_state)
                self.table_widget.blockSignals(False)
        else:
            self.anchor_row = row

    def _toggle_all_checkboxes(self, check=True):
        self._user_interacted = True
        self._has_saved_selection = True
        state = Qt.CheckState.Checked if check else Qt.CheckState.Unchecked
        self.table_widget.blockSignals(True)
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is not None:
                role = id_item.data(Qt.ItemDataRole.UserRole + 2)
                if role in ("missing", "expander"):
                    continue
                if self.table_widget.isRowHidden(i):
                    continue
                id_item.setCheckState(state)
        self.table_widget.blockSignals(False)

        self.anchor_row = -1

    def _select_platform(self, platform: str):
        """Smart select depots matching a platform (linux/windows), including shared depots and filtering out bonus media/32-bit."""
        self._user_interacted = True
        self._has_saved_selection = True
        smart_depots = set(get_smart_default_depots(self.depots, target_platform=platform))

        self.table_widget.blockSignals(True)
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander"):
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            if depot_id in smart_depots:
                id_item.setCheckState(Qt.CheckState.Checked)
            else:
                id_item.setCheckState(Qt.CheckState.Unchecked)
        self.table_widget.blockSignals(False)
        self.anchor_row = -1

    def _fetch_header_image(self, app_id):
        self._current_app_id = app_id
        url = ImageFetcher.get_header_image_url(app_id)
        self.fetcher = ImageFetcher(url, ephemeral=True)
        self.fetcher.finished.connect(self.on_image_fetched)
        self.fetcher.finished.connect(self._cleanup_fetcher)
        self.fetcher.start()

    def on_image_fetched(self, image_data):
        if image_data:
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            self._apply_header_pixmap(pixmap)
        else:
            # Image fetch failed (404), try to get the correct URL from Steam API
            logger.debug("Image fetch failed, attempting to refresh from API")
            self._trigger_header_refresh()

    def _trigger_header_refresh(self):
        """
        Fetch the correct header URL from Steam API when generic URL fails.
        """
        app_id = getattr(self, "_current_app_id", None)
        if not app_id:
            self._show_no_image()
            return

        logger.debug(f"Fetching header URL from Steam API for appid {app_id}")

        try:
            # Fetch the correct URL from Steam API (synchronous but fast)
            api_url = ImageFetcher.fetch_header_from_web_api(app_id)

            if api_url:
                logger.info(f"Got header URL from API for appid {app_id}: {api_url}")

                # Update database with fresh URL
                try:
                    from managers.db_manager import DatabaseManager

                    db = DatabaseManager()
                    db.upsert_app_info(app_id, {"header_url": api_url})
                except Exception as e:
                    logger.debug(f"Could not update DB: {e}")

                # Re-fetch the image with the correct URL
                self.retry_fetcher = ImageFetcher(api_url)
                self.retry_fetcher.finished.connect(self._on_retry_image_fetched)
                self.retry_fetcher.finished.connect(self._cleanup_retry_fetcher)
                self.retry_fetcher.start()
            else:
                logger.debug(f"No header URL found in API for appid {app_id}")
                self._show_no_image()
        except Exception as e:
            logger.warning(f"Failed to refresh header for appid {app_id}: {e}")
            self._show_no_image()

    def _on_retry_image_fetched(self, image_data):
        """Handle the retry image fetch result."""
        if image_data:
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            self._apply_header_pixmap(pixmap)
            logger.info("Successfully loaded header image after refresh")
        else:
            self._show_no_image()

    def _apply_header_pixmap(self, pixmap: QPixmap) -> None:
        scaled = pixmap.scaled(
            120, 56, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
        )
        self.header_label.setPixmap(scaled)
        self.header_label.setStyleSheet("border-radius: 6px;")

    def _show_no_image(self):
        """Show fallback text when image is not available."""
        self.header_label.setText("No Image")
        self.header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_label.setStyleSheet(
            "background-color: rgba(255, 255, 255, 0.039); "
            "color: rgba(255, 255, 255, 0.314); "
            "font-size: 8pt; "
            "border-radius: 6px;"
        )

    def _cleanup_fetcher(self, _data: bytes) -> None:
        if hasattr(self, "fetcher") and self.fetcher is not None:
            self.fetcher.deleteLater()
            self.fetcher = None

    def _cleanup_retry_fetcher(self, _data: bytes) -> None:
        if hasattr(self, "retry_fetcher") and self.retry_fetcher is not None:
            self.retry_fetcher.deleteLater()
            self.retry_fetcher = None

    def get_selected_depots(self):
        selected = []
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander"):
                continue
            if id_item.checkState() == Qt.CheckState.Checked:
                selected.append(str(id_item.data(Qt.ItemDataRole.UserRole)))
        return selected

    def get_selected_files(self):
        """Returns the list of custom checked relative file paths."""
        return self.selected_files

    def _refresh_dlc_only_style(self) -> None:
        """Update the DLC Only button style to reflect its on/off state."""
        active = self._dlc_only_btn.isChecked()
        from utils.color_utils import get_best_foreground_color
        if active:
            # Active state: Solid accent background with high-contrast text color
            text_hex = get_best_foreground_color(self.accent_color, dark_color="#121214", light_color="#FFFFFF")
            self._dlc_only_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.accent_color} !important;
                    color: {text_hex} !important;
                    border: 1px solid {self.accent_color} !important;
                    border-radius: 4px;
                    padding: 4px 10px;
                    font-weight: bold;
                }}
            """)
        else:
            # Inverted / outline style: Transparent background, accent hover
            self._dlc_only_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: rgba(255, 255, 255, 0.6);
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 4px;
                    padding: 4px 10px;
                }}
                QPushButton:hover {{
                    border-color: {self.accent_color};
                    color: {self.accent_color};
                }}
            """)

    def _on_dlc_only_toggled(self) -> None:
        """Toggle DLC Only mode, show 3-second lockout warning, and auto-select DLC depots."""
        new_state = self._dlc_only_btn.isChecked()
        if new_state:
            try:
                warn_fn = show_dlc_mode_warning
                if warn_fn is None:
                    try:
                        from ui.dialogs.dlc_warning_dialog import show_dlc_mode_warning as warn_fn
                    except ImportError:
                        try:
                            from .dlc_warning_dialog import show_dlc_mode_warning as warn_fn
                        except ImportError:
                            from dlc_warning_dialog import show_dlc_mode_warning as warn_fn
                if warn_fn:
                    warn_fn(self)
            except Exception as e:
                logger.warning(f"DLC warning dialog error: {e}")

        self._dlc_only_mode = new_state
        self._refresh_dlc_only_style()
        if self._settings:
            self._settings.setValue(f"dlc_only_mode/{self.app_id}", self._dlc_only_mode)

        if self._dlc_only_mode:
            self._start_enrichment_async(force=True)
            if not getattr(self, "_has_saved_selection", False) and not getattr(self, "_user_interacted", False):
                self._apply_dlc_auto_selection()
        else:
            self._select_platform("windows")

    def get_dlc_only_mode(self) -> bool:
        """Returns whether DLC Only mode is enabled for this dialog."""
        return self._dlc_only_mode

    def _on_select_files_clicked(self):
        # 1. Get chosen depots
        chosen_depots = []
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is not None and id_item.checkState() == Qt.CheckState.Checked:
                role = id_item.data(Qt.ItemDataRole.UserRole + 2)
                if role in ("missing", "expander"):
                    continue
                depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
                chosen_depots.append(depot_id)

        if not chosen_depots:
            QMessageBox.warning(self, "Warning", "Please select at least one depot first.")
            return

        # Use the first checked depot for file list customization
        target_depot = chosen_depots[0]

        # 2. Locate the manifest zip for this app
        from utils.helpers import get_base_path
        app_id = self.app_id

        manifests_dir = get_base_path() / "hubcap_manifests"
        zips = list(manifests_dir.glob(f"accela_fetch_{app_id}.zip")) + \
               list(manifests_dir.glob(f"accela_fetch_{app_id}_*.zip"))
        if not zips:
            QMessageBox.critical(self, "Error", f"No manifest zip file found for AppID {app_id} in {manifests_dir}.")
            return
        zips.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        zip_path = str(zips[0])

        # 3. Extract target manifests
        import zipfile
        temp_dir = os.path.join(tempfile.gettempdir(), f"selective_manifests_{app_id}")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to extract manifest zip: {e}")
            return

        # Extract key from LUA config or fallback to depot_keys.db
        depot_key = None
        lua_files = list(Path(temp_dir).glob("*.lua"))
        if lua_files:
            try:
                with open(str(lua_files[0]), "r", encoding="utf-8") as lf:
                    lua_content = lf.read()
                    match = re.search(r"addappid\(\s*" + re.escape(target_depot) + r"\s*,\s*\d+\s*,\s*\"([a-fA-F0-9]+)\"\)", lua_content)
                    if match:
                        depot_key = match.group(1)
            except Exception as e:
                logger.warning(f"Failed to parse LUA for depot keys: {e}")

        # Fallback to depot_keys.db if key was not in LUA file (e.g. smart generate bundle)
        if not depot_key:
            try:
                from managers.depot_key_manager import DepotKeyManager
                dkm = DepotKeyManager()
                cached = dkm.get_depot_keys(app_id)
                if target_depot in cached:
                    depot_key = cached[target_depot]
            except Exception as dkm_e:
                logger.warning(f"Failed to load key from depot_keys.db for depot {target_depot}: {dkm_e}")

        if not depot_key:
            QMessageBox.critical(self, "Error", f"Could not find depot key for depot {target_depot} in LUA config or local key database.")
            return

        # Create depot keys file
        keys_path = os.path.join(temp_dir, "depot.keys")
        try:
            with open(keys_path, "w") as kf:
                kf.write(f"{target_depot};{depot_key}\n")
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Failed to write keys file: {e}")
            return

        # Locate the manifest file and manifest ID
        manifest_files = list(Path(temp_dir).glob(f"{target_depot}_*.manifest"))
        if not manifest_files:
            # Fallback check if it was zipped without depot ID prefix
            manifest_files = list(Path(temp_dir).glob("*.manifest"))

        if not manifest_files:
            QMessageBox.critical(self, "Error", f"No manifest file (*.manifest) found for depot {target_depot} in the extracted manifest bundle.")
            return

        manifest_path = manifest_files[0]
        manifest_file = str(manifest_path)

        filename = manifest_path.name
        stem = filename.replace(".manifest", "")
        if "_" in stem:
            manifest_id = stem.split("_", 1)[1]
        else:
            manifest_id = stem

        # Dump manifest files using DDM in background progress
        progress_dialog = QProgressDialog("Loading file list from manifest...", "Cancel", 0, 0, self)
        progress_dialog.setWindowTitle("Loading Manifest")
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.show()

        # Define command args
        from utils.helpers import get_dotnet_path, resource_path
        dotnet_path = get_dotnet_path()
        dll_path = resource_path(os.path.join("deps", "DepotDownloader.dll"))

        cmd = [
            dotnet_path,
            dll_path,
            "-app", str(app_id),
            "-depot", str(target_depot),
            "-manifest", str(manifest_id),
            "-manifestfile", manifest_file,
            "-depotkeys", keys_path,
            "-manifest-only",
            "-dir", temp_dir
        ]

        class DumpThread(QThread):
            finished_signal = pyqtSignal(bool, str)
            def run(self):
                try:
                    subprocess.run(cmd, capture_output=True, text=True, check=True)
                    self.finished_signal.emit(True, "")
                except Exception as ex:
                    self.finished_signal.emit(False, str(ex))

        self.dump_thread = DumpThread()

        def on_dump_finished(success, err):
            progress_dialog.close()
            # Clean up temp keys file
            if os.path.exists(keys_path):
                try:
                    os.remove(keys_path)
                except OSError:
                    pass

            if not success:
                QMessageBox.critical(self, "Error", f"Failed to load file list: {err}")
                return

            txt_path = os.path.join(temp_dir, f"manifest_{target_depot}_{manifest_id}.txt")
            if not os.path.exists(txt_path):
                QMessageBox.critical(self, "Error", "Failed to locate generated file list text file.")
                return

            # Open File Selection Tree Dialog
            from ui.dialogs.fileselection import FileSelectionDialog
            sel_dialog = FileSelectionDialog(app_id, target_depot, txt_path, self)
            if sel_dialog.exec():
                self.selected_files = sel_dialog.selected_files
                QMessageBox.information(
                    self,
                    "Selection Confirmed",
                    f"Selected {len(self.selected_files)} file(s) for custom download.\nPress OK at the bottom to start installing."
                )

        self.dump_thread.finished_signal.connect(on_dump_finished)
        self.dump_thread.start()

    def accept(self):
        # 1. Validate depot selection
        selected_depots = self.get_selected_depots()
        if not selected_depots:
            QMessageBox.warning(self, "No Depots Selected", "Please select at least one depot to proceed.")
            return

        # 2. Validate storage selection if enabled
        if self.show_storage and hasattr(self, "_storage_paths") and self._storage_paths:
            if not self.selected_storage_path:
                QMessageBox.warning(self, "No Storage Selected", "Please select a storage location before proceeding.")
                return

        # 3. Promote header image to permanent cache since user is downloading
        if hasattr(self, "app_id") and self.app_id:
            ImageFetcher.promote_to_permanent_cache(self.app_id)

        super().accept()

    def get_selected_storage(self) -> str:
        """Returns the selected storage destination path, or None."""
        return self.selected_storage_path

    def get_selected_branch(self) -> str:
        if hasattr(self, "branch_combo") and self.branch_combo is not None:
            return self.branch_combo.currentText().strip()
        return getattr(self, "branch", "public") or "public"

    def _resolve_local_buildid(self) -> str:
        """Attempt to resolve installed build ID from local appmanifest or QSettings."""
        aid = str(getattr(self, "app_id", "") or "").strip()
        if not aid or aid in ("0", "N/A", "unknown"):
            return ""
        try:
            from core.steam_helpers import get_steam_libraries
            from pathlib import Path
            import re
            for lib in get_steam_libraries():
                acf_path = Path(lib) / "steamapps" / f"appmanifest_{aid}.acf"
                if acf_path.is_file():
                    with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    m = re.search(r'"buildid"\s+"([^"]+)"', content)
                    if m and m.group(1).strip() and m.group(1).strip() != "0":
                        return m.group(1).strip()
        except Exception:
            pass

        try:
            from utils.settings import get_settings
            s = get_settings()
            stored = str(s.value(f"installed_buildid/{aid}", "")).strip()
            if stored and stored.isdigit() and stored != "0":
                return stored
        except Exception:
            pass
        return ""

    def get_selected_build(self) -> Optional[str]:
        return getattr(self, "_selected_build_id", None) or self.current_build_id

    def is_build_pinned(self) -> bool:
        return getattr(self, "_is_build_pinned", False)

    def get_manifest_overrides(self) -> Dict[str, str]:
        return getattr(self, "_manifest_overrides", {})

    def _on_branch_changed(self, new_branch: str):
        if not new_branch:
            return
        self.branch = new_branch
        b_info = self.branches.get(new_branch)
        new_bid = ""
        if isinstance(b_info, dict) and b_info.get("buildid"):
            new_bid = str(b_info["buildid"]).strip()
            if new_bid:
                self.current_build_id = new_bid
                self._selected_build_id = new_bid
                if hasattr(self, "builds_btn") and self.builds_btn is not None:
                    self.builds_btn.setText(f"Build: {new_bid}")

        self._is_build_pinned = (new_branch != "public")
        if hasattr(self, "builds_btn") and self.builds_btn is not None:
            self._update_build_btn_style()

        # Update manifest overrides for the selected branch
        from utils.branch_helpers import resolve_branch_manifest_gid
        self._manifest_overrides.clear()

        depots_source = self.depots
        has_branch_manifests = any(
            isinstance(d, dict) and "manifests" in d for d in self.depots.values()
        )
        if not has_branch_manifests and self.app_id:
            try:
                from core.steam_api import get_depot_info_from_api
                pics_info = get_depot_info_from_api(self.app_id)
                if pics_info and pics_info.get("depots"):
                    depots_source = pics_info["depots"]
                    for did, d_data in depots_source.items():
                        if str(did) in self.depots and isinstance(self.depots[str(did)], dict):
                            if "manifests" in d_data:
                                self.depots[str(did)]["manifests"] = d_data["manifests"]
            except Exception as e:
                logger.debug(f"[DepotSelection] Could not fetch PICS depot info for branch manifests: {e}")

        if new_branch != "public":
            for did, d_info in depots_source.items():
                if str(did) == str(self.app_id):
                    continue
                gid = resolve_branch_manifest_gid(d_info, new_branch)
                if gid:
                    self._manifest_overrides[str(did)] = str(gid)
                    if str(did) in self.depots and isinstance(self.depots[str(did)], dict):
                        self.depots[str(did)]["manifest_id"] = str(gid)
            logger.info(f"[DepotSelection] Branch '{new_branch}' (Build {new_bid}) manifest overrides: {self._manifest_overrides}")
        else:
            for did, d_info in depots_source.items():
                if str(did) == str(self.app_id):
                    continue
                gid = resolve_branch_manifest_gid(d_info, "public")
                if gid:
                    if str(did) in self.depots and isinstance(self.depots[str(did)], dict):
                        self.depots[str(did)]["manifest_id"] = str(gid)
            logger.info(f"[DepotSelection] Switched to public branch (Build {new_bid})")

    def _update_build_btn_style(self):
        if getattr(self, "_is_build_pinned", False):
            self.builds_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.08);
                    border: 1px solid {self.accent_color};
                    border-radius: 6px;
                    color: {self.accent_color};
                    font-size: 8.5pt;
                    font-weight: bold;
                    padding: 2px 10px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.14);
                }}
            """)
        else:
            self.builds_btn.setStyleSheet("""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.06);
                    border: 1px solid rgba(255, 255, 255, 0.16);
                    border-radius: 6px;
                    color: #FFFFFF;
                    font-size: 8.5pt;
                    font-weight: 600;
                    padding: 2px 10px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.12);
                    border-color: rgba(255, 255, 255, 0.3);
                }}
            """)

    def _apply_build_selection(self, selected_bid: str, patch_depots: dict):
        if not selected_bid:
            return
        self._selected_build_id = selected_bid
        self.builds_btn.setText(f"Build: {selected_bid}")
        if self.current_build_id and selected_bid == self.current_build_id:
            self._is_build_pinned = False
        else:
            self._is_build_pinned = True
        self._update_build_btn_style()

        if patch_depots:
            for did, info in patch_depots.items():
                mid = info.get("manifest_id") if isinstance(info, dict) else str(info)
                if mid:
                    self._manifest_overrides[str(did)] = str(mid)
                    if str(did) in self.depots and isinstance(self.depots[str(did)], dict):
                        self.depots[str(did)]["manifest_id"] = str(mid)
                    logger.info(f"[DepotSelection] Overrode depot {did} manifest to {mid} for build {selected_bid}")

    def _on_builds_clicked(self):
        from core.steamdb_scraper import ByparrManager
        has_byparr = ByparrManager.find_byparr_dir() is not None

        first_depot = next(iter(self.depots.keys())) if self.depots else str(self.app_id)

        if not has_byparr:
            from ui.dialogs.manual_manifest_dialog import ManualManifestDialog
            dlg = ManualManifestDialog(
                parent=self,
                app_id=self.app_id,
                game_name=self.game_name,
                depots_dict=self.depots,
                default_depot_id=first_depot,
                current_build_id=self.current_build_id,
                accent_color=self.accent_color,
            )
            if dlg.exec():
                selected_bid, patch_depots = dlg.get_selected_build()
                self._apply_build_selection(selected_bid, patch_depots)
        else:
            from ui.dialogs.build_selection_dialog import BuildSelectionDialog
            dlg = BuildSelectionDialog(
                parent=self,
                app_id=self.app_id,
                game_name=self.game_name,
                current_build_id=self.current_build_id,
                accent_color=self.accent_color,
                depots_dict=self.depots,
                default_depot_id=first_depot,
            )
            if dlg.exec():
                selected_bid, patch_depots = dlg.get_selected_build()
                self._apply_build_selection(selected_bid, patch_depots)


    def closeEvent(self, a0):
        """Ensure image fetch is cleaned up when dialog closes."""
        if hasattr(self, "fetcher") and self.fetcher is not None:
            try:
                self.fetcher.stop()
            except RuntimeError:
                pass
        if hasattr(self, "retry_fetcher") and self.retry_fetcher is not None:
            try:
                self.retry_fetcher.stop()
            except RuntimeError:
                pass
        super().closeEvent(a0)
