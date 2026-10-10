"""Modern Depot Selection Dialog (Experimental).

Provides a premium, two-column game-launcher grade layout for depot selection
with left-sidebar metadata/category switcher, filter pills, live search,
drive cards with available disk space, and per-depot file inspection.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QRectF, QSize, QPoint, QEvent
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from utils.image_fetcher import ImageFetcher
from utils.settings import get_settings
from utils.color_utils import get_best_foreground_color
from utils.lua_parsing import find_live
from core.steam_helpers import find_steam_install

try:
    from ui.dialogs.dlc_warning_dialog import show_dlc_mode_warning
except ImportError:
    show_dlc_mode_warning = None

logger = logging.getLogger(__name__)


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable string."""
    try:
        val = float(size_bytes)
        for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
            if abs(val) < 1024.0:
                return f"{val:3.2f} {unit}"
            val /= 1024.0
        return f"{val:.2f} PiB"
    except (ValueError, TypeError):
        return "0.00 B"


def _depot_matches_platform(depot_data: dict, platform: str) -> bool:
    """Check if depot matches platform (linux/windows)."""
    oslist = (depot_data.get("oslist") or "").lower()
    desc = (depot_data.get("desc") or "").lower()
    if platform.lower() in oslist:
        return True
    if f"[{platform.lower()}]" in desc or f"({platform.lower()})" in desc:
        return True
    if not oslist:
        return True
    return False


class NumericTableWidgetItem(QTableWidgetItem):
    """Sortable table widget item storing raw sort value."""
    def __init__(self, text: str, sort_val: int = 0):
        super().__init__(text)
        self.sort_val = sort_val

    def __lt__(self, other):
        if isinstance(other, NumericTableWidgetItem):
            return self.sort_val < other.sort_val
        return super().__lt__(other)


class ModernDepotSelectionDialog(QDialog):
    """Experimental modern two-column layout for depot selection."""

    _depots_enriched_signal = pyqtSignal(dict)

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
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setWindowTitle("Select Depots to Download")
        self.app_id = str(app_id)
        self.game_name = str(game_name or f"App {app_id}")
        self.depots = {k: v for k, v in (depots or {}).items() if str(k) != str(app_id)}
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
        self.missing_hubcap_depots = list(missing_hubcap_depots or [])
        self.missing_depots_info = dict(missing_depots_info or {})
        self.game_data = kwargs.get("game_data", {})

        self._settings = get_settings()
        self.accent_color = self._settings.value("theme_accent", "#C06C84", type=str)
        try:
            c = QColor(self.accent_color)
            self.accent_r, self.accent_g, self.accent_b = c.red(), c.green(), c.blue()
        except Exception:
            self.accent_r, self.accent_g, self.accent_b = 192, 108, 132

        self.text_color_on_accent = get_best_foreground_color(self.accent_color, "#111318", "#FFFFFF")

        self.selected_storage_path = None
        self._selected_build_id = self.current_build_id
        self._is_build_pinned = (self.branch != "public")
        self._manifest_overrides: Dict[str, str] = {}
        self._dlc_only_mode = self._settings.value(f"dlc_only_mode/{self.app_id}", False, type=bool)
        self._current_category_filter = "all"  # "all" or "dlc"
        self._show_hidden_depots = self._settings.value("show_hidden_depots", False, type=bool)

        # Saved depot selections
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

        self.resize(1050, 680)
        self.setMinimumSize(960, 580)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Dialog)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._setup_theme()
        self._init_ui()
        self._load_header_image()

    def _setup_theme(self):
        """Configure clean dark palette and styling."""
        ar, ag, ab = self.accent_r, self.accent_g, self.accent_b
        acc = self.accent_color
        txt_acc = self.text_color_on_accent

        self.setStyleSheet(f"""
            QDialog {{
                background-color: #12141a;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.15);
                font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
            }}
            QLabel {{
                color: #FFFFFF;
            }}
            QTableWidget {{
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
                gridline-color: transparent;
                outline: 0;
                color: #FFFFFF;
                selection-background-color: transparent;
                selection-color: #FFFFFF;
            }}
            QTableWidget::item {{
                padding: 6px 10px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.02);
                border: none;
            }}
            QTableWidget::item:hover {{
                background-color: rgba({ar}, {ag}, {ab}, 0.06);
                border: none;
            }}
            QTableWidget::item:selected {{
                background-color: rgba({ar}, {ag}, {ab}, 0.16) !important;
                border: none !important;
                color: #FFFFFF !important;
            }}
            QHeaderView::section {{
                background-color: rgba(255, 255, 255, 0.04);
                color: rgba(255, 255, 255, 0.85);
                padding: 6px 10px;
                border: none;
                font-size: 8.5pt;
                font-weight: bold;
                text-transform: uppercase;
            }}
            QTableWidget::indicator {{
                width: 15px;
                height: 15px;
                background: transparent;
                border: 1.5px solid rgba({ar}, {ag}, {ab}, 0.55);
                border-radius: 4px;
            }}
            QTableWidget::indicator:unchecked {{
                background-color: transparent;
                border: 1.5px solid rgba({ar}, {ag}, {ab}, 0.55);
            }}
            QTableWidget::indicator:unchecked:hover {{
                border: 1.5px solid rgba({ar}, {ag}, {ab}, 1.0);
                background-color: rgba({ar}, {ag}, {ab}, 0.08);
            }}
            QTableWidget::indicator:checked,
            QTableWidget::indicator:checked:hover,
            QTableWidget::indicator:checked:selected {{
                background-color: {acc} !important;
                border: 1.5px solid {acc} !important;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {acc};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

    def _init_ui(self):
        """Construct the main two-column layout and bottom drawer."""
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        from ui.dialog_titlebar import DialogTitleBar
        self.title_bar = DialogTitleBar(
            self,
            title="Select Depots to Download",
            can_minimize=True,
            can_maximize=True,
            use_power_close=True,
        )
        root_layout.addWidget(self.title_bar)

        content_container = QWidget()
        main_layout = QVBoxLayout(content_container)
        main_layout.setContentsMargins(16, 14, 16, 16)
        main_layout.setSpacing(12)
        root_layout.addWidget(content_container, 1)

        # Center area: Sidebar + Main Content
        center_split = QHBoxLayout()
        center_split.setSpacing(14)

        # 1. Left Sidebar
        self.sidebar_frame = self._build_sidebar()
        center_split.addWidget(self.sidebar_frame, 0)

        # 2. Right Main Content Area
        self.main_content = self._build_main_content()
        center_split.addWidget(self.main_content, 1)

        main_layout.addLayout(center_split, 1)

        # 3. Bottom Drawer
        self.bottom_frame = self._build_bottom_bar()
        main_layout.addWidget(self.bottom_frame, 0)

        # Populate table and statistics
        self._populate_table()
        self._update_selection_summary()

    def _build_sidebar(self) -> QFrame:
        """Build the left sidebar containing game banner, metadata & categories."""
        ar, ag, ab = self.accent_r, self.accent_g, self.accent_b
        acc = self.accent_color

        frame = QFrame()
        frame.setFixedWidth(240)
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: rgba(255, 255, 255, 0.025);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Banner Cover Art
        self.header_label = QLabel()
        self.header_label.setFixedHeight(108)
        self.header_label.setScaledContents(False)
        self.header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_label.setStyleSheet("""
            QLabel {
                background-color: rgba(255, 255, 255, 0.04);
                border-radius: 8px;
                border: 1px solid rgba(255, 255, 255, 0.08);
            }
        """)
        layout.addWidget(self.header_label)

        # Game Title & App ID Subtitle
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.title_lbl = QLabel(self.game_name)
        self.title_lbl.setStyleSheet("font-size: 11pt; font-weight: bold; color: #FFFFFF; border: none; background: transparent;")
        self.title_lbl.setWordWrap(True)
        title_box.addWidget(self.title_lbl)

        self.appid_sub_lbl = QLabel(f"({self.app_id})")
        self.appid_sub_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.55); border: none; background: transparent;")
        title_box.addWidget(self.appid_sub_lbl)
        layout.addLayout(title_box)

        # Metadata Details Box
        meta_box = QVBoxLayout()
        meta_box.setSpacing(6)
        meta_box.setContentsMargins(0, 4, 0, 4)

        # App ID row with copy button
        appid_row = QHBoxLayout()
        appid_row.setSpacing(4)
        lbl_k1 = QLabel("App ID")
        lbl_k1.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8.5pt; border: none; background: transparent;")
        appid_row.addWidget(lbl_k1)
        appid_row.addStretch()

        self.appid_val_lbl = QLabel(str(self.app_id))
        self.appid_val_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500; border: none; background: transparent;")
        appid_row.addWidget(self.appid_val_lbl)

        copy_btn = QPushButton("📋")
        copy_btn.setFixedSize(20, 20)
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setToolTip("Copy App ID")
        copy_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 8pt;
                color: rgba(255, 255, 255, 0.6);
            }
            QPushButton:hover {
                color: #FFFFFF;
            }
        """)
        copy_btn.clicked.connect(self._copy_appid_to_clipboard)
        appid_row.addWidget(copy_btn)
        meta_box.addLayout(appid_row)

        # Branch row
        branch_row = QHBoxLayout()
        branch_row.setSpacing(4)
        lbl_k2 = QLabel("Branch")
        lbl_k2.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8.5pt; border: none; background: transparent;")
        branch_row.addWidget(lbl_k2)
        branch_row.addStretch()
        self.branch_val_lbl = QLabel(self.branch)
        self.branch_val_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500; border: none; background: transparent;")
        branch_row.addWidget(self.branch_val_lbl)
        meta_box.addLayout(branch_row)

        # Build ID row
        build_row = QHBoxLayout()
        build_row.setSpacing(4)
        lbl_k3 = QLabel("Build ID")
        lbl_k3.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8.5pt; border: none; background: transparent;")
        build_row.addWidget(lbl_k3)
        build_row.addStretch()
        self.build_val_lbl = QLabel(self.current_build_id or "Latest")
        self.build_val_lbl.setStyleSheet(f"color: {acc}; font-size: 8.5pt; font-weight: 600; border: none; background: transparent;")
        build_row.addWidget(self.build_val_lbl)
        meta_box.addLayout(build_row)

        # Depots row
        depots_row = QHBoxLayout()
        depots_row.setSpacing(4)
        lbl_k4 = QLabel("Depots")
        lbl_k4.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8.5pt; border: none; background: transparent;")
        depots_row.addWidget(lbl_k4)
        depots_row.addStretch()
        self.depots_count_lbl = QLabel()
        self.depots_count_lbl.setStyleSheet("font-size: 8.5pt; font-weight: 500; border: none; background: transparent;")
        depots_row.addWidget(self.depots_count_lbl)
        meta_box.addLayout(depots_row)

        # Total DLC Size row
        dlc_size_row = QHBoxLayout()
        dlc_size_row.setSpacing(4)
        lbl_k5 = QLabel("Total DLC Size")
        lbl_k5.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8.5pt; border: none; background: transparent;")
        dlc_size_row.addWidget(lbl_k5)
        dlc_size_row.addStretch()
        self.dlc_size_val_lbl = QLabel("0.00 B")
        self.dlc_size_val_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500; border: none; background: transparent;")
        dlc_size_row.addWidget(self.dlc_size_val_lbl)
        meta_box.addLayout(dlc_size_row)

        layout.addLayout(meta_box)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("background-color: rgba(255, 255, 255, 0.06); max-height: 1px; border: none;")
        layout.addWidget(div)

        # Navigation Category Switcher
        nav_box = QVBoxLayout()
        nav_box.setSpacing(6)

        self.btn_nav_depots = QPushButton("📦  Depots")
        self.btn_nav_depots.setCheckable(True)
        self.btn_nav_depots.setChecked(True)
        self.btn_nav_depots.setFixedHeight(34)
        self.btn_nav_depots.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_nav_dlcs = QPushButton("🏷️  DLCs")
        self.btn_nav_dlcs.setCheckable(True)
        self.btn_nav_dlcs.setFixedHeight(34)
        self.btn_nav_dlcs.setCursor(Qt.CursorShape.PointingHandCursor)

        self.nav_btn_group = QButtonGroup(self)
        self.nav_btn_group.addButton(self.btn_nav_depots, 0)
        self.nav_btn_group.addButton(self.btn_nav_dlcs, 1)
        self.nav_btn_group.idClicked.connect(self._on_category_changed)

        self._refresh_nav_btn_styles()

        nav_box.addWidget(self.btn_nav_depots)
        nav_box.addWidget(self.btn_nav_dlcs)
        layout.addLayout(nav_box)

        layout.addStretch()
        return frame

    def _refresh_nav_btn_styles(self):
        """Update navigation pill button styles."""
        ar, ag, ab = self.accent_r, self.accent_g, self.accent_b
        acc = self.accent_color

        for btn in (self.btn_nav_depots, self.btn_nav_dlcs):
            is_active = btn.isChecked()
            if is_active:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: rgba({ar}, {ag}, {ab}, 0.18);
                        border: 1px solid {acc};
                        border-radius: 6px;
                        color: #FFFFFF;
                        font-weight: bold;
                        font-size: 9pt;
                        text-align: left;
                        padding-left: 12px;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: transparent;
                        border: 1px solid transparent;
                        border-radius: 6px;
                        color: rgba(255, 255, 255, 0.7);
                        font-weight: 500;
                        font-size: 9pt;
                        text-align: left;
                        padding-left: 12px;
                    }}
                    QPushButton:hover {{
                        background-color: rgba(255, 255, 255, 0.06);
                        color: #FFFFFF;
                    }}
                """)

    def _on_category_changed(self, btn_id: int):
        """Handle Depots vs DLCs category navigation."""
        self._current_category_filter = "dlc" if btn_id == 1 else "all"
        self._refresh_nav_btn_styles()
        self._apply_row_filters()

    def _build_main_content(self) -> QWidget:
        """Construct the right main workspace with filters, table and search."""
        ar, ag, ab = self.accent_r, self.accent_g, self.accent_b
        acc = self.accent_color

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # Top Drawer / Header Row
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        hdr_title = QLabel("Depots")
        hdr_title.setStyleSheet("font-size: 14pt; font-weight: bold; color: #FFFFFF;")
        hdr_sub = QLabel("Select which depots you want to download for this build.")
        hdr_sub.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.55);")
        title_col.addWidget(hdr_title)
        title_col.addWidget(hdr_sub)
        top_row.addLayout(title_col)
        top_row.addStretch()

        # Branch Selector
        branch_list = list(self.branches.keys()) if self.branches else ["public"]
        if "public" not in branch_list:
            branch_list.insert(0, "public")

        if len(branch_list) > 1:
            lbl_b = QLabel("Branch:")
            lbl_b.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.7);")
            top_row.addWidget(lbl_b)

            self.branch_combo = QComboBox()
            self.branch_combo.setFixedHeight(28)
            self.branch_combo.setMinimumWidth(110)
            self.branch_combo.addItems(branch_list)
            if self.branch in branch_list:
                self.branch_combo.setCurrentText(self.branch)
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
                    border-color: rgba(255, 255, 255, 0.35);
                    background-color: rgba(255, 255, 255, 0.12);
                }}
            """)
            self.branch_combo.currentTextChanged.connect(self._on_branch_changed)
            top_row.addWidget(self.branch_combo)

        # Build Button
        lbl_bd = QLabel("Build ID:")
        lbl_bd.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.7);")
        top_row.addWidget(lbl_bd)

        display_bid = self.current_build_id or "Latest"
        self.builds_btn = QPushButton(f"{display_bid}")
        self.builds_btn.setFixedHeight(28)
        self.builds_btn.setMinimumWidth(110)
        self.builds_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.builds_btn.clicked.connect(self._on_builds_clicked)
        self._update_build_btn_style()
        top_row.addWidget(self.builds_btn)

        # Show Hidden Depots Checkbox
        self.hidden_chk = QCheckBox("Show Hidden Depots")
        self.hidden_chk.setChecked(self._show_hidden_depots)
        self.hidden_chk.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.8);")
        self.hidden_chk.toggled.connect(self._on_hidden_depots_toggled)
        top_row.addWidget(self.hidden_chk)

        layout.addLayout(top_row)

        # Action Filter Pills & Search Bar Row
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)

        self.pill_edition = self._create_filter_pill("Complete ▾")
        self.pill_edition.setMinimumWidth(180)
        self.pill_edition.setMaximumWidth(340)
        self.pill_edition.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.pill_edition.setToolTip("Select game edition and platform (Complete, Deluxe, Standard)")
        self.pill_edition.clicked.connect(self._show_edition_menu)

        filter_row.addWidget(self.pill_edition)

        filter_row.addStretch()

        # Search Bar
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  Search depots...")
        self.search_input.setFixedHeight(30)
        self.search_input.setFixedWidth(200)
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #FFFFFF;
                padding: 2px 10px;
                font-size: 8.5pt;
            }}
            QLineEdit:focus {{
                border-color: {acc};
                background-color: rgba(255, 255, 255, 0.08);
            }}
        """)
        self.search_input.textChanged.connect(self._apply_row_filters)
        filter_row.addWidget(self.search_input)

        layout.addLayout(filter_row)

        # Selection Summary Line
        summary_row = QHBoxLayout()
        self.selection_summary_lbl = QLabel()
        self.selection_summary_lbl.setStyleSheet(f"font-size: 9pt; font-weight: bold; color: {acc};")
        summary_row.addWidget(self.selection_summary_lbl)
        summary_row.addStretch()
        layout.addLayout(summary_row)

        # Main Depot Table
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(5)
        self.table_widget.setHorizontalHeaderLabels(["Select", "Configuration", "Platform", "Size", "Browse Files"])
        self.table_widget.setIconSize(QSize(16, 16))
        self.table_widget.verticalHeader().setVisible(False)
        self.table_widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table_widget.setShowGrid(False)
        self.table_widget.setAlternatingRowColors(True)

        header = self.table_widget.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._on_header_section_clicked)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table_widget.setColumnWidth(0, 130)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table_widget.setColumnWidth(2, 95)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table_widget.setColumnWidth(3, 95)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table_widget.setColumnWidth(4, 52)

        self.table_widget.cellClicked.connect(self._on_table_cell_clicked)
        layout.addWidget(self.table_widget, 1)

        return widget

    def _create_filter_pill(self, label: str) -> QPushButton:
        """Create a rounded filter action pill button."""
        ar, ag, ab = self.accent_r, self.accent_g, self.accent_b
        acc = self.accent_color

        btn = QPushButton(label)
        btn.setFixedHeight(28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #FFFFFF;
                font-size: 8.5pt;
                font-weight: 600;
                padding: 2px 10px;
            }}
            QPushButton:hover {{
                background-color: rgba({ar}, {ag}, {ab}, 0.2);
                border-color: {acc};
                color: #FFFFFF;
            }}
        """)
        return btn

    def _update_build_btn_style(self):
        """Synchronize build picker button styling."""
        acc = self.accent_color
        if getattr(self, "_is_build_pinned", False):
            self.builds_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.08);
                    border: 1px solid {acc};
                    border-radius: 6px;
                    color: {acc};
                    font-size: 8.5pt;
                    font-weight: 600;
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
                    border-color: rgba(255, 255, 255, 0.35);
                }}
            """)

    def _build_bottom_bar(self) -> QFrame:
        """Construct the bottom bar with drive cards, DLC Only option and action buttons."""
        ar, ag, ab = self.accent_r, self.accent_g, self.accent_b
        acc = self.accent_color

        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: rgba(255, 255, 255, 0.025);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 10px;
            }}
        """)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(16)

        # 1. Download Location (Drive Cards)
        drive_col = QVBoxLayout()
        drive_col.setSpacing(4)
        lbl_drives = QLabel("Download Location")
        lbl_drives.setStyleSheet("font-size: 8pt; font-weight: bold; color: rgba(255, 255, 255, 0.5); text-transform: uppercase; border: none; background: transparent;")
        drive_col.addWidget(lbl_drives)

        drives_row = QHBoxLayout()
        drives_row.setSpacing(6)
        self._setup_storage_drives(drives_row)
        drive_col.addLayout(drives_row)
        layout.addLayout(drive_col, 1)



        # 3. Action Buttons
        act_col = QVBoxLayout()
        act_col.setSpacing(4)
        act_col.addStretch()

        act_row = QHBoxLayout()
        act_row.setSpacing(8)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedHeight(34)
        self.cancel_btn.setFixedWidth(80)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                color: #FFFFFF;
                font-weight: 500;
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        act_row.addWidget(self.cancel_btn)

        self.ok_btn = QPushButton("📥  OK")
        self.ok_btn.setFixedHeight(34)
        self.ok_btn.setFixedWidth(90)
        self.ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {acc};
                border: none;
                border-radius: 6px;
                color: {self.text_color_on_accent};
                font-weight: bold;
                font-size: 9.5pt;
            }}
            QPushButton:hover {{
                background-color: rgba({ar}, {ag}, {ab}, 0.85);
            }}
        """)
        self.ok_btn.clicked.connect(self.accept)
        act_row.addWidget(self.ok_btn)

        act_col.addLayout(act_row)
        layout.addLayout(act_col, 0)

        return frame

    def _setup_storage_drives(self, layout: QHBoxLayout):
        """Populate storage drive cards with disk space and selection logic."""
        from core.steam_helpers import get_steam_libraries

        storage_paths = get_steam_libraries()
        if not storage_paths:
            storage_paths = [str(Path.home() / ".local/share/Steam")]

        self._storage_btn_group = QButtonGroup(self)
        self._storage_btn_group.setExclusive(True)
        self._storage_buttons = {}

        def _format_storage_info(spath: str):
            p = Path(spath)
            try:
                free_bytes = shutil.disk_usage(spath).free
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

            p_lower = spath.lower()
            steam_root = find_steam_install()
            if steam_root and os.path.realpath(spath) == os.path.realpath(steam_root):
                label = "Primary Drive"
            elif "/.local/share/steam" in p_lower or "/.steam/steam" in p_lower:
                label = "Primary Drive"
            elif "sdcard" in p_lower or "sd_card" in p_lower or "mmcblk" in p_lower or "/sd" in p_lower:
                label = "SD Card"
            else:
                label = p.name
                if label.lower() in ("steamlibrary", "steamapps", "common") and len(p.parts) > 1:
                    label = p.parts[-2]
                if len(label) > 14:
                    label = label[:12] + "…"

            tooltip = f"Path: {spath}" + (f"\nAvailable: {free_str}" if free_str else "")
            return label, free_str, tooltip

        ar, ag, ab = self.accent_r, self.accent_g, self.accent_b
        acc = self.accent_color

        total_storages = len(storage_paths)
        max_direct_buttons = 3

        for i in range(min(total_storages, max_direct_buttons)):
            spath = storage_paths[i]
            lbl_text, free_str, tip_text = _format_storage_info(spath)

            btn_text = f"💾  {lbl_text}\n    {free_str}" if free_str else f"💾  {lbl_text}"
            btn = QPushButton(btn_text)
            btn.setCheckable(True)
            btn.setFixedHeight(34)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip_text)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.04);
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 6px;
                    color: rgba(255, 255, 255, 0.85);
                    font-size: 8pt;
                    font-weight: 500;
                    text-align: left;
                    padding: 2px 8px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.08);
                    border-color: rgba(255, 255, 255, 0.25);
                }}
                QPushButton:checked {{
                    background-color: rgba({ar}, {ag}, {ab}, 0.2) !important;
                    border: 1px solid {acc} !important;
                    color: #FFFFFF !important;
                    font-weight: bold;
                }}
            """)
            self._storage_btn_group.addButton(btn, i)
            self._storage_buttons[spath] = btn
            layout.addWidget(btn)

        def _on_storage_btn_clicked(btn_id: int):
            if 0 <= btn_id < len(storage_paths):
                self.selected_storage_path = storage_paths[btn_id]
                logger.info(f"[ModernDepotSelection] Selected storage library: {self.selected_storage_path}")

        self._storage_btn_group.idClicked.connect(_on_storage_btn_clicked)

        if total_storages > max_direct_buttons:
            combo = QComboBox()
            combo.setFixedHeight(34)
            combo.addItem("More Drives ▾", None)
            combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: rgba(255, 255, 255, 0.04);
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 6px;
                    color: rgba(255, 255, 255, 0.85);
                    padding: 2px 8px;
                    font-size: 8pt;
                }}
                QComboBox:hover {{
                    border-color: {acc};
                }}
                QComboBox QAbstractItemView {{
                    background-color: #1a1c23;
                    color: #FFFFFF;
                    selection-background-color: {acc};
                }}
            """)
            for i in range(max_direct_buttons, total_storages):
                spath = storage_paths[i]
                lbl_text, free_str, _ = _format_storage_info(spath)
                combo.addItem(f"{lbl_text} ({free_str})", spath)

            def _on_combo_changed(idx: int):
                if idx > 0:
                    chosen = combo.itemData(idx)
                    if chosen:
                        self.selected_storage_path = chosen
                        self._storage_btn_group.setExclusive(False)
                        checked_btn = self._storage_btn_group.checkedButton()
                        if checked_btn:
                            checked_btn.setChecked(False)
                        self._storage_btn_group.setExclusive(True)

            combo.currentIndexChanged.connect(_on_combo_changed)
            layout.addWidget(combo)

        # Pre-select priority: preferred library or first drive
        target_select = self.preferred_library_path if self.preferred_library_path in self._storage_buttons else storage_paths[0]
        if target_select in self._storage_buttons:
            self._storage_buttons[target_select].setChecked(True)
            self.selected_storage_path = target_select

    def _refresh_dlc_only_style(self):
        """Update DLC Only toggle button styling."""
        ar, ag, ab = self.accent_r, self.accent_g, self.accent_b
        acc = self.accent_color
        if self.dlc_only_btn.isChecked():
            self.dlc_only_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba({ar}, {ag}, {ab}, 0.22);
                    border: 1px solid {acc};
                    border-radius: 6px;
                    color: #FFFFFF;
                    font-size: 8.5pt;
                    font-weight: bold;
                    padding: 4px 10px;
                }}
            """)
        else:
            self.dlc_only_btn.setStyleSheet("""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.04);
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 6px;
                    color: rgba(255, 255, 255, 0.7);
                    font-size: 8.5pt;
                    font-weight: 500;
                    padding: 4px 10px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.08);
                    color: #FFFFFF;
                }}
            """)

    def _on_dlc_only_toggled(self):
        """Handle DLC Only mode toggle."""
        new_state = self.dlc_only_btn.isChecked()
        if new_state and show_dlc_mode_warning:
            try:
                show_dlc_mode_warning(self)
            except Exception as e:
                logger.warning(f"DLC warning error: {e}")

        self._dlc_only_mode = new_state
        self._refresh_dlc_only_style()
        if self._settings:
            self._settings.setValue(f"dlc_only_mode/{self.app_id}", self._dlc_only_mode)

        if self._dlc_only_mode:
            self._apply_dlc_auto_selection()
        else:
            self._batch_select_platform("windows")

    def _load_header_image(self):
        """Fetch and render game capsule banner."""
        from managers.db_manager import DatabaseManager
        db_url = DatabaseManager().get_header_url(str(self.app_id))
        target_url = self.header_url or db_url or f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{self.app_id}/header.jpg"

        def _on_fetched(data):
            if data:
                pm = QPixmap()
                pm.loadFromData(data)
                if not pm.isNull():
                    scaled = pm.scaled(226, 105, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                    self.header_label.setPixmap(scaled)
                    return
            self.header_label.setText(self.game_name)

        self.img_fetcher = ImageFetcher(target_url)
        self.img_fetcher.finished.connect(_on_fetched)
        self.img_fetcher.start()

    def _copy_appid_to_clipboard(self):
        """Copy the App ID to the system clipboard."""
        cb = QApplication.clipboard()
        if cb:
            cb.setText(str(self.app_id))
            self.appid_val_lbl.setText("Copied!")
            QTimer.singleShot(1500, lambda: self.appid_val_lbl.setText(str(self.app_id)))

    def _populate_table(self):
        """Populate the depot table with items and initial checks."""
        self.table_widget.setRowCount(0)
        self.table_widget.blockSignals(True)

        # Compute smart defaults if no explicit selection - default to highest Complete Edition for target OS
        pre_selected_set = set()
        if self.selected_depots is not None:
            pre_selected_set = set(str(d) for d in self.selected_depots)
        else:
            try:
                from utils.edition_helpers import (
                    get_available_platforms,
                    get_default_platform,
                    resolve_multi_platform_editions,
                )
                from ui.dialogs.depotselection import get_smart_default_depots
                smart_on = self._settings.value("smart_depot_selection", True, type=bool)
                if smart_on:
                    self._available_platforms = get_available_platforms(self.depots)
                    self._default_platform = get_default_platform(self.depots)
                    self._multi_platform_editions = resolve_multi_platform_editions(self.app_id, self.depots)

                    default_eds = self._multi_platform_editions.get(self._default_platform, [])
                    highest_ed = default_eds[0] if default_eds else None
                    if highest_ed and highest_ed.get("depot_ids"):
                        pre_selected_set = set(highest_ed["depot_ids"])
                        if hasattr(self, "pill_edition"):
                            short = highest_ed.get("short_name", "Complete")
                            if len(self._available_platforms) > 1:
                                plat_prefix = "Linux: " if self._default_platform == "linux" else "Win: "
                                self.pill_edition.setText(f"{plat_prefix}{short} ▾")
                            else:
                                self.pill_edition.setText(f"{short} ▾")
                    else:
                        pre_selected_set = set(get_smart_default_depots(self.depots, target_platform=self._default_platform))
                else:
                    pre_selected_set = set(get_smart_default_depots(self.depots, target_platform="linux"))
            except Exception:
                pre_selected_set = set(get_smart_default_depots(self.depots, target_platform="linux"))

        total_dlc_bytes = 0
        total_depots_count = 0
        dlc_depots_count = 0
        win_count = 0
        linux_count = 0

        # Sort depots numerically
        sorted_keys = sorted(self.depots.keys(), key=lambda x: int(x) if str(x).isdigit() else 999999999)

        for depot_id in sorted_keys:
            d_data = self.depots[depot_id]
            if not isinstance(d_data, dict):
                continue

            desc = d_data.get("desc", "") or f"Depot {depot_id}"
            is_dlc = d_data.get("is_dlc", False) or "[dlc" in desc.lower() or bool(d_data.get("dlcappid"))
            is_hidden = bool(d_data.get("is_hidden", False))

            raw_size = int(d_data.get("size") or 0)
            if is_dlc:
                total_dlc_bytes += raw_size
                dlc_depots_count += 1

            total_depots_count += 1
            if _depot_matches_platform(d_data, "windows"):
                win_count += 1
            if _depot_matches_platform(d_data, "linux"):
                linux_count += 1

            row_idx = self.table_widget.rowCount()
            self.table_widget.insertRow(row_idx)

            # Col 0: ID Item with checkbox
            id_val = int(depot_id) if str(depot_id).isdigit() else 0
            id_item = NumericTableWidgetItem(str(depot_id), id_val)
            id_item.setData(Qt.ItemDataRole.UserRole, str(depot_id))
            id_item.setData(Qt.ItemDataRole.UserRole + 1, "dlc" if is_dlc else "base")
            id_item.setData(Qt.ItemDataRole.UserRole + 2, is_hidden)

            is_checked = (str(depot_id) in pre_selected_set)
            id_item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.table_widget.setItem(row_idx, 0, id_item)

            # Col 1: Configuration Text
            from utils.depot_tag_helpers import format_depot_row_label, get_os_icon
            clean_desc = format_depot_row_label(
                str(depot_id),
                d_data,
                desc,
                has_os_icon=True,
            )
            cfg_item = QTableWidgetItem(clean_desc)
            cfg_item.setFlags(cfg_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_widget.setItem(row_idx, 1, cfg_item)

            # Col 2: Platform Badge
            oslist = (d_data.get("oslist") or "").lower()
            plat_icon = get_os_icon(d_data.get("oslist"))
            if "windows" in oslist:
                plat_badge = "Windows"
            elif "linux" in oslist:
                plat_badge = "Linux"
            elif "macos" in oslist or "macosx" in oslist:
                plat_badge = "macOS"
            elif "android" in oslist:
                plat_badge = "Android"
            else:
                plat_badge = "Shared"
            plat_item = QTableWidgetItem(plat_badge)
            if plat_icon:
                plat_item.setIcon(plat_icon)
            plat_item.setFlags(plat_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_widget.setItem(row_idx, 2, plat_item)

            # Col 3: Size
            size_item = NumericTableWidgetItem(format_size(raw_size), raw_size)
            size_item.setFlags(size_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_widget.setItem(row_idx, 3, size_item)

            # Col 4: Per-depot Folder / File Inspection Action
            folder_btn = QPushButton("📁")
            folder_btn.setFixedSize(28, 24)
            folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            folder_btn.setToolTip(f"Inspect/Select files for depot {depot_id}")
            folder_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.05);
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 4px;
                    color: #FFFFFF;
                    font-size: 8.5pt;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 0.15);
                }
            """)
            folder_btn.clicked.connect(lambda checked, did=str(depot_id): self._open_file_selector_for_depot(did))

            btn_holder = QWidget()
            b_lay = QHBoxLayout(btn_holder)
            b_lay.setContentsMargins(0, 0, 0, 0)
            b_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            b_lay.addWidget(folder_btn)
            self.table_widget.setCellWidget(row_idx, 4, btn_holder)

            if is_hidden and not self._show_hidden_depots:
                self.table_widget.setRowHidden(row_idx, True)

        self.table_widget.blockSignals(False)

        # Update metadata counts in sidebar and filter pills
        self.depots_count_lbl.setText(f"<b style='color: {self.accent_color};'>{total_depots_count}</b> / {len(self.depots)}")
        self.dlc_size_val_lbl.setText(format_size(total_dlc_bytes))
        self.btn_nav_depots.setText(f"📦  Depots ({total_depots_count})")
        self.btn_nav_dlcs.setText(f"🏷️  DLCs ({dlc_depots_count})")

        if hasattr(self, "pill_all"):
            self.pill_all.setText(f"All ({total_depots_count})")
        if hasattr(self, "pill_win"):
            self.pill_win.setText(f"Windows ({win_count})")
        if hasattr(self, "pill_linux"):
            self.pill_linux.setText(f"Linux ({linux_count})")

    def _apply_row_filters(self):
        """Filter table rows by search query and category (Depots vs DLCs)."""
        search_query = self.search_input.text().strip().lower()
        cat = self._current_category_filter

        for r in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(r, 0)
            cfg_item = self.table_widget.item(r, 1)
            if not id_item:
                continue

            is_hidden = bool(id_item.data(Qt.ItemDataRole.UserRole + 2))
            if is_hidden and not self._show_hidden_depots:
                self.table_widget.setRowHidden(r, True)
                continue

            # Category filter:
            depot_type = id_item.data(Qt.ItemDataRole.UserRole + 1)
            if cat == "dlc" and depot_type != "dlc":
                self.table_widget.setRowHidden(r, True)
                continue

            # Search filter:
            did = str(id_item.text()).lower()
            desc = str(cfg_item.text()).lower() if cfg_item else ""
            if search_query and (search_query not in did and search_query not in desc):
                self.table_widget.setRowHidden(r, True)
            else:
                self.table_widget.setRowHidden(r, False)

    def _on_hidden_depots_toggled(self, checked: bool):
        """Toggle display of hidden depots."""
        self._show_hidden_depots = checked
        if self._settings:
            self._settings.setValue("show_hidden_depots", checked)
        self._apply_row_filters()

    def _on_table_cell_clicked(self, row: int, col: int):
        """Toggle checkbox state when clicking a cell."""
        id_item = self.table_widget.item(row, 0)
        if not id_item:
            return
        curr = id_item.checkState()
        new_st = Qt.CheckState.Unchecked if curr == Qt.CheckState.Checked else Qt.CheckState.Checked
        id_item.setCheckState(new_st)
        self._update_selection_summary()

    def _update_selection_summary(self):
        """Recalculate and display count and total size of selected depots."""
        sel_count = 0
        sel_bytes = 0
        for r in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(r, 0)
            if id_item and id_item.checkState() == Qt.CheckState.Checked:
                sel_count += 1
                size_item = self.table_widget.item(r, 3)
                if size_item and hasattr(size_item, "sort_val"):
                    sel_bytes += int(size_item.sort_val or 0)

        self.selection_summary_lbl.setText(f"{sel_count} depots selected • {format_size(sel_bytes)}")

        total_count = self.table_widget.rowCount()
        h0 = self.table_widget.horizontalHeaderItem(0)
        if h0:
            h0.setText(f"Select ({sel_count}/{total_count})")
            h0.setToolTip("Click to toggle Select All / Select None")

        if hasattr(self, "pill_edition") and hasattr(self, "_multi_platform_editions"):
            from utils.edition_helpers import match_selection_to_edition
            current_sel = set(self.get_selected_depots())
            matched = match_selection_to_edition(current_sel, self._multi_platform_editions)
            if matched:
                plat = matched.get("platform", "")
                short = matched.get("short_name", "Edition")
                if len(getattr(self, "_available_platforms", [])) > 1:
                    plat_prefix = "Linux: " if plat == "linux" else "Win: "
                    btn_text = f"{plat_prefix}{short} ▾"
                else:
                    btn_text = f"{short} ▾"
                self.pill_edition.setText(btn_text)
                self.pill_edition.setToolTip(matched.get("name", btn_text))
            else:
                self.pill_edition.setText("Custom ▾")
                self.pill_edition.setToolTip("Custom depot selection")

    def _on_header_section_clicked(self, logical_index: int):
        if logical_index == 0:
            total = self.table_widget.rowCount()
            checked = 0
            for r in range(total):
                item = self.table_widget.item(r, 0)
                if item and item.checkState() == Qt.CheckState.Checked:
                    checked += 1
            if checked == total and total > 0:
                self._batch_select_platform("none")
            else:
                self._batch_select_platform("all")

    def _batch_select_platform(self, platform: str):
        """Batch check depots matching a platform or deselect all."""
        from ui.dialogs.depotselection import _depot_matches_platform
        self.table_widget.blockSignals(True)
        for r in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(r, 0)
            if not id_item:
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            d_data = self.depots.get(depot_id, {})

            if platform == "none":
                id_item.setCheckState(Qt.CheckState.Unchecked)
            elif platform == "all":
                id_item.setCheckState(Qt.CheckState.Checked)
            else:
                matches = _depot_matches_platform(d_data, platform)
                id_item.setCheckState(Qt.CheckState.Checked if matches else Qt.CheckState.Unchecked)

        self.table_widget.blockSignals(False)
        self._update_selection_summary()

    def _show_edition_menu(self):
        """Displays drop-up/down menu of available editions grouped by OS if multiple exist."""
        from PyQt6.QtWidgets import QMenu
        from utils.edition_helpers import (
            get_available_platforms,
            resolve_multi_platform_editions,
        )

        if not hasattr(self, "_available_platforms") or not self._available_platforms:
            self._available_platforms = get_available_platforms(self.depots)
            self._multi_platform_editions = resolve_multi_platform_editions(self.app_id, self.depots)

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1e1e24;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 4px;
                color: #ffffff;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 0.12);
            }
        """)

        current_sel = set(self.get_selected_depots())

        if len(self._available_platforms) > 1:
            for plat in self._available_platforms:
                plat_label = "Linux (Native)" if plat == "linux" else "Windows (Proton)"
                sub_menu = menu.addMenu(plat_label)
                sub_menu.setStyleSheet(menu.styleSheet())
                eds = self._multi_platform_editions.get(plat, [])
                for ed in eds:
                    action = sub_menu.addAction(ed["name"])
                    action.setCheckable(True)
                    ed_depots = {str(d) for d in ed.get("depot_ids", set())}
                    if current_sel == ed_depots:
                        action.setChecked(True)
                    action.triggered.connect(lambda checked, target_ed=ed: self._apply_edition_selection(target_ed))
        else:
            plat = self._available_platforms[0] if self._available_platforms else "windows"
            eds = self._multi_platform_editions.get(plat, [])
            for ed in eds:
                action = menu.addAction(ed["name"])
                action.setCheckable(True)
                ed_depots = {str(d) for d in ed.get("depot_ids", set())}
                if current_sel == ed_depots:
                    action.setChecked(True)
                action.triggered.connect(lambda checked, target_ed=ed: self._apply_edition_selection(target_ed))

        menu.adjustSize()
        menu_height = menu.sizeHint().height()
        pos = self.pill_edition.mapToGlobal(QPoint(0, -menu_height - 2))
        if pos.y() < 0:
            pos = self.pill_edition.mapToGlobal(QPoint(0, self.pill_edition.height() + 2))
        menu.exec(pos)

    def _apply_edition_selection(self, edition: dict):
        """Apply the chosen edition to the depot checkboxes."""
        short_name = edition.get("short_name", "Edition")
        plat = edition.get("platform", "")
        if getattr(self, "_available_platforms", None) and len(self._available_platforms) > 1:
            plat_prefix = "Linux: " if plat == "linux" else "Win: "
            btn_text = f"{plat_prefix}{short_name} ▾"
        else:
            btn_text = f"{short_name} ▾"

        if hasattr(self, "pill_edition"):
            self.pill_edition.setText(btn_text)
            self.pill_edition.setToolTip(edition.get("name", btn_text))
        target_depots = {str(d) for d in edition.get("depot_ids", set())}

        self.table_widget.blockSignals(True)
        for r in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(r, 0)
            if not id_item:
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            if depot_id in target_depots:
                id_item.setCheckState(Qt.CheckState.Checked)
            else:
                id_item.setCheckState(Qt.CheckState.Unchecked)
        self.table_widget.blockSignals(False)
        self._update_selection_summary()

    def _batch_select_baseline(self):
        """Select baseline Store Package depots (excluding extra DLCs)."""
        if hasattr(self, "_available_editions"):
            for ed in self._available_editions:
                if ed.get("id") == "standard":
                    self._apply_edition_selection(ed)
                    return
        self._batch_select_platform("linux")

    def _apply_dlc_auto_selection(self):
        """Automatically select all DLC depots."""
        self.table_widget.blockSignals(True)
        for r in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(r, 0)
            if not id_item:
                continue
            depot_type = id_item.data(Qt.ItemDataRole.UserRole + 1)
            id_item.setCheckState(Qt.CheckState.Checked if depot_type == "dlc" else Qt.CheckState.Unchecked)
        self.table_widget.blockSignals(False)
        self._update_selection_summary()

    def _on_branch_changed(self, new_branch: str):
        """Handle branch switching."""
        self.branch = new_branch
        self.branch_val_lbl.setText(new_branch)
        b_info = self.branches.get(new_branch, {})
        new_bid = ""
        if isinstance(b_info, dict) and b_info.get("buildid"):
            new_bid = str(b_info["buildid"]).strip()
        if new_bid:
            self.current_build_id = new_bid
            self._selected_build_id = new_bid
            self.builds_btn.setText(new_bid)
            self.build_val_lbl.setText(new_bid)

        self._is_build_pinned = (new_branch != "public")
        self._update_build_btn_style()

    def _on_builds_clicked(self):
        """Open historical build picker dialog."""
        from core.steamdb_scraper import ByparrManager
        has_byparr = ByparrManager.find_byparr_dir() is not None
        first_depot = next(iter(self.depots.keys())) if self.depots else str(self.app_id)
        active_bid = getattr(self, "_selected_build_id", None) or self.current_build_id

        if not has_byparr:
            from ui.dialogs.manual_manifest_dialog import ManualManifestDialog
            dlg = ManualManifestDialog(
                parent=self,
                app_id=self.app_id,
                game_name=self.game_name,
                depots_dict=self.depots,
                default_depot_id=first_depot,
                current_build_id=active_bid,
                accent_color=self.accent_color,
            )
        else:
            from ui.dialogs.build_selection_dialog import BuildSelectionDialog
            dlg = BuildSelectionDialog(
                parent=self,
                app_id=self.app_id,
                game_name=self.game_name,
                current_build_id=active_bid,
                accent_color=self.accent_color,
                depots_dict=self.depots,
                default_depot_id=first_depot,
            )

        if dlg.exec():
            selected_bid, patch_depots = dlg.get_selected_build()
            if selected_bid:
                self._selected_build_id = selected_bid
                self.builds_btn.setText(selected_bid)
                self.build_val_lbl.setText(selected_bid)
                self._is_build_pinned = (selected_bid != self.current_build_id)
                self._update_build_btn_style()
                if patch_depots:
                    for did, info in patch_depots.items():
                        mid = info.get("manifest_id") if isinstance(info, dict) else str(info)
                        if mid:
                            self._manifest_overrides[str(did)] = str(mid)

    def _open_file_selector_for_depot(self, target_depot: str):
        """Open selective file customization dialog for a specific depot."""
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

        import zipfile
        temp_dir = os.path.join(tempfile.gettempdir(), f"selective_manifests_{app_id}")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            from utils.steam_manifest import safe_extract_zip
            safe_extract_zip(zip_path, temp_dir)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to extract manifest zip: {e}")
            return

        depot_key = None
        lua_files = list(Path(temp_dir).glob("*.lua"))
        if lua_files:
            try:
                with open(str(lua_files[0]), "r", encoding="utf-8") as lf:
                    lua_content = lf.read()
                    m = find_live(lua_content, r"addappid\(\s*" + re.escape(target_depot) + r"\s*,\s*\d+\s*,\s*\"([a-fA-F0-9]+)\"\)")
                    if m:
                        depot_key = m.group(1)
            except Exception:
                pass

        if not depot_key:
            try:
                from managers.depot_key_manager import DepotKeyManager
                cached = DepotKeyManager().get_depot_keys(app_id)
                if target_depot in cached:
                    depot_key = cached[target_depot]
            except Exception:
                pass

        if not depot_key:
            QMessageBox.critical(self, "Error", f"Could not find depot key for depot {target_depot}.")
            return

        keys_path = os.path.join(temp_dir, "depot.keys")
        try:
            with open(keys_path, "w") as kf:
                kf.write(f"{target_depot};{depot_key}\n")
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Failed to write keys file: {e}")
            return

        manifest_files = list(Path(temp_dir).glob(f"{target_depot}_*.manifest")) or list(Path(temp_dir).glob("*.manifest"))
        if not manifest_files:
            QMessageBox.critical(self, "Error", f"No manifest file found for depot {target_depot}.")
            return

        manifest_path = manifest_files[0]
        manifest_file = str(manifest_path)
        stem = manifest_path.name.replace(".manifest", "")
        manifest_id = stem.split("_", 1)[1] if "_" in stem else stem

        progress_dialog = QProgressDialog("Loading file list from manifest...", "Cancel", 0, 0, self)
        progress_dialog.setWindowTitle("Loading Manifest")
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.show()

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
            "-dir", temp_dir,
        ]

        class DumpThread(QThread):
            finished_signal = pyqtSignal(bool, str)
            def run(self):
                try:
                    subprocess.run(cmd, capture_output=True, text=True, check=True)
                    self.finished_signal.emit(True, "")
                except Exception as ex:
                    self.finished_signal.emit(False, str(ex))

        self._dump_thread = DumpThread()

        def _on_finished(success: bool, err: str):
            progress_dialog.close()
            if not success:
                QMessageBox.critical(self, "Error", f"Failed to load file list: {err}")
                return

            txt_path = os.path.join(temp_dir, f"manifest_{target_depot}_{manifest_id}.txt")
            if not os.path.exists(txt_path):
                QMessageBox.critical(self, "Error", "Failed to locate generated file list.")
                return

            from ui.dialogs.fileselection import FileSelectionDialog
            sel_dlg = FileSelectionDialog(
                app_id=app_id,
                depot_id=target_depot,
                manifest_txt_path=txt_path,
                parent=self,
                manifest_file=manifest_file,
                keys_path=keys_path,
                manifest_id=manifest_id,
            )
            if sel_dlg.exec():
                self.selected_files = sel_dlg.selected_files
                QMessageBox.information(
                    self,
                    "Selection Confirmed",
                    f"Selected {len(self.selected_files)} file(s) for depot {target_depot}.\nPress OK at the bottom to download.",
                )

        self._dump_thread.finished_signal.connect(_on_finished)
        self._dump_thread.start()

    # Public getters matching DepotSelectionDialog interface
    def get_selected_depots(self) -> List[str]:
        """Returns list of checked depot IDs."""
        selected = []
        for r in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(r, 0)
            if id_item and id_item.checkState() == Qt.CheckState.Checked:
                selected.append(str(id_item.data(Qt.ItemDataRole.UserRole)))
        return selected

    def get_selected_files(self) -> List[str]:
        """Returns custom selective file paths."""
        return self.selected_files

    def get_dlc_only_mode(self) -> bool:
        """Returns whether DLC Only mode is active."""
        return self._dlc_only_mode

    def get_selected_storage(self) -> Optional[str]:
        """Returns chosen storage library directory."""
        return self.selected_storage_path

    def get_selected_branch(self) -> str:
        """Returns active branch."""
        if hasattr(self, "branch_combo") and self.branch_combo:
            return self.branch_combo.currentText().strip()
        return self.branch

    def get_selected_build(self) -> Optional[str]:
        """Returns active or pinned build ID."""
        return getattr(self, "_selected_build_id", None) or self.current_build_id

    def is_build_pinned(self) -> bool:
        """Returns whether historical build is pinned."""
        return getattr(self, "_is_build_pinned", False)

    def get_manifest_overrides(self) -> Dict[str, str]:
        """Returns dictionary of depot to manifest overrides."""
        return self._manifest_overrides

    def changeEvent(self, a0):
        super().changeEvent(a0)
        if a0 and a0.type() == QEvent.Type.WindowStateChange:
            if not self.isMinimized():
                self.raise_()
                self.activateWindow()

    def showEvent(self, a0):
        super().showEvent(a0)
        self.raise_()
        self.activateWindow()
