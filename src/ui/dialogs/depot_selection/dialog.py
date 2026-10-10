"""The depot selection dialog.

Split out of ``ui.dialogs.depotselection`` so the classification rules, the
table items and the table views could move into their own modules; that module
re-exports everything, so all nine importers are unchanged.

Row order is fixed before the table is filled rather than by clicking column
headers. The header still carries the select-all checkbox - its only
interactive part.
"""

import logging
import re
import os
import tempfile
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QRectF, QEvent, QPoint, QSize
from PyQt6.QtGui import QPixmap, QColor, QPainter, QPen, QPalette
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
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QStyle,
    QWidget,
)

from utils.image_fetcher import ImageFetcher
from utils.lua_parsing import find_live
from utils.settings import get_settings
from ui.dialogs.dialog_helpers import create_standard_buttons

try:
    from ui.dialogs.dlc_warning_dialog import show_dlc_mode_warning
except ImportError:
    show_dlc_mode_warning = None

logger = logging.getLogger(__name__)



from ui.dialogs.depot_selection.items import (
    ConfigTableWidgetItem,
    NumericTableWidgetItem,
)
from ui.dialogs.depot_selection.rules import (
    _depot_is_android,
    _depot_is_macos,
    _depot_matches_platform,
    format_size,
    get_smart_default_depots,
    is_bonus_or_media_depot,
)
from ui.dialogs.depot_selection.views import DepotCheckboxDelegate, DepotHeaderView


class DepotSelectionDialog(QDialog):
    _depots_enriched_signal = pyqtSignal(dict)
    _missing_contents_checked_signal = pyqtSignal(dict)
    _missing_depots_updated_signal = pyqtSignal()

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

        # Check experimental modern depot selection dialog toggle
        try:
            from utils.settings import get_settings
            _s = get_settings()
            if _s.value("use_modern_depot_dialog", False, type=bool):
                aid = kwargs.get("app_id", args[0] if args else "")
                filtered = {k: v for k, v in (depots or {}).items() if str(k) != str(aid)}
                if len(filtered) > 2 and not is_single:
                    from ui.dialogs.modern_depot_selection import ModernDepotSelectionDialog
                    return ModernDepotSelectionDialog(*args, **kwargs)
        except Exception as _m_err:
            logger.warning(f"[DepotSelectionDialog] Modern dialog routing fallback: {_m_err}")

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
        self._init_state(
            app_id, depots, game_name, header_url, selected_depots,
            show_storage, is_single_depot, library_path, refetched_depots,
            branch, branches, current_build_id, missing_hubcap_depots,
            missing_depots_info,
        )
        self.resize(680, 540)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Dialog)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setStyleSheet("""
            QDialog {
                background-color: #121318;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.15);
            }
        """)

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
        layout = QVBoxLayout(content_container)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(10)
        root_layout.addWidget(content_container, 1)

        self.anchor_row = -1

        self._preload_cached_enrichments()
        self._load_settings()
        accent_r, accent_g, accent_b = self._resolve_accent_colors()
        self._build_header_section(layout)
        self._load_selection_state()

        content_widget = QVBoxLayout()
        content_widget.setContentsMargins(10, 0, 10, 0)

        self._build_depot_table(content_widget, accent_r, accent_g, accent_b)
        self._add_depot_rows(content_widget)
        self._build_bottom_bar(content_widget)
        layout.addLayout(content_widget)

        self._kick_off_enrichment_check()

    def _init_state(
        self, app_id, depots, game_name, header_url, selected_depots,
        show_storage, is_single_depot, library_path, refetched_depots,
        branch, branches, current_build_id, missing_hubcap_depots, missing_depots_info,
    ) -> None:
        """Store every constructor argument and wire up signals and timers.

        Split out of __init__ so the constructor reads as a layout spine. All
        attributes the rest of the class relies on are established here.
        """
        self._depots_enriched_signal.connect(self._on_depots_enriched)
        self._missing_contents_checked_signal.connect(self._on_missing_contents_check_finished)
        self._missing_depots_updated_signal.connect(self._on_missing_depots_updated)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setWindowTitle("Select Depots to Download")
        self.app_id = app_id
        self.depots = {k: v for k, v in (depots or {}).items() if str(k) != str(app_id)}
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
        self._recovered_depots_map: Dict[str, str] = {}
        self._is_checking_missing_contents = False
        self._spinner_angle = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.timeout.connect(self._on_spinner_tick)

        if isinstance(missing_hubcap_depots, dict):
            if not missing_depots_info:
                # The keys are still worth keeping: we know which depots are
                # missing even before any metadata arrives. The values are
                # reason strings, not per-depot metadata, so seed empty dicts
                # rather than aliasing the whole mapping.
                missing_depots_info = {str(d): {} for d in missing_hubcap_depots}
            self.missing_hubcap_depots = [str(d) for d in missing_hubcap_depots.keys() if str(d).strip()]
        else:
            self.missing_hubcap_depots = [str(d) for d in (missing_hubcap_depots or []) if str(d).strip()]

        if self.missing_hubcap_depots:
            self._is_checking_missing_contents = True
            self._spinner_timer.start(16)

        # Every consumer of this mapping treats the values as per-depot
        # metadata dicts (minfo.get("name"), minfo.get("size"), and the
        # isinstance(d_data, dict) platform checks). Keep that invariant even if
        # a caller hands us something else.
        self.missing_depots_info = {
            str(k): (dict(v) if isinstance(v, dict) else {})
            for k, v in (missing_depots_info or {}).items()
        }
        self.selected_storage_path = None

    def _preload_cached_enrichments(self) -> None:
        """Apply depot descriptions and sizes cached in the database.

        Doing this before the table is built means rows render with their
        enriched text on the first paint instead of popping in afterwards.
        """
        # Pre-apply any cached enrichments from database
        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            cached_enrichments = db.get_depot_enrichments(str(self.app_id))
            if cached_enrichments:
                self._apply_depot_enrichments(cached_enrichments)
        except Exception as e:
            logger.debug(f"[DepotSelection] Could not load cached enrichments: {e}")


    def _load_settings(self) -> None:
        """Read the depot-visibility filters and accent colour from settings.

        A settings failure must not stop the dialog, so every filter falls
        back to its default and accent_color to the built-in pink.
        """
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


    def _resolve_accent_colors(self) -> Tuple[int, int, int]:
        """Turn the accent colour into an RGB triple for the table stylesheet.

        Returns the components rather than storing them so the caller keeps
        them as locals, matching how the stylesheet f-strings consume them.
        """
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

        return accent_r, accent_g, accent_b

    def _build_header_section(self, layout: QVBoxLayout) -> None:
        """Build the thumbnail, game title, branch selector and Build button."""
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
            self.branch_combo.setMinimumWidth(120)
            self.branch_combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: rgba(255, 255, 255, 0.06);
                    border: 1px solid rgba(255, 255, 255, 0.16);
                    border-radius: 6px;
                    color: #FFFFFF;
                    padding: 2px 12px;
                    font-size: 8.5pt;
                    font-weight: 600;
                }}
                QComboBox:hover {{
                    border-color: rgba(255, 255, 255, 0.35);
                    background-color: rgba(255, 255, 255, 0.12);
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 18px;
                }}
                QComboBox QAbstractItemView {{
                    background-color: #1a1c23;
                    color: #FFFFFF;
                    selection-background-color: transparent;
                    selection-color: #FFFFFF;
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

        controls_row.addSpacing(6)

        display_bid = self.current_build_id or "Latest"
        self.builds_btn = QPushButton(f"Build: {display_bid}")
        self.builds_btn.setFixedHeight(28)
        self.builds_btn.setMinimumWidth(140)
        self.builds_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.builds_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_build_btn_style()
        self.builds_btn.clicked.connect(self._on_builds_clicked)
        controls_row.addWidget(self.builds_btn)

        self.show_hidden_chk = None
        self.smart_select_btn = None

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
            self._check_missing_contents_async()

        layout.addSpacing(5)

        self._fetch_header_image(self.app_id)


    def _load_selection_state(self) -> None:
        """Load DLC-only mode and whether a previous depot selection exists.

        Must run before the table is built: _populate_table reads these to
        decide what to preselect.
        """
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


    def _build_depot_table(
        self, content_widget: QVBoxLayout, accent_r: int, accent_g: int, accent_b: int,
    ) -> None:
        """Create the depot table, its header view and the checkbox delegate."""
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(3)
        self.table_widget.setHorizontalHeaderLabels(["Select", "Configuration", "Size"])
        self.table_widget.setIconSize(QSize(16, 16))
        self.table_widget.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table_widget.horizontalHeader().setSectionsClickable(True)
        self.table_widget.horizontalHeader().sectionClicked.connect(self._on_header_section_clicked)
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
                selection-background-color: transparent;
                selection-color: #FFFFFF;
            }}
            QTableWidget::item {{
                padding: 6px 10px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.02);
                border: none;
            }}
            QTableWidget::item:hover {{
                background-color: rgba({accent_r}, {accent_g}, {accent_b}, 0.06);
                border: none;
            }}
            QTableWidget::item:selected {{
                background-color: rgba({accent_r}, {accent_g}, {accent_b}, 0.16) !important;
                border: none !important;
                color: #FFFFFF !important;
            }}
            QHeaderView::section {{
                background-color: rgba(255, 255, 255, 0.04);
                color: #FFFFFF;
                padding: 6px 10px;
                border: none;
                font-size: 8.5pt;
                font-weight: bold;
                text-transform: uppercase;
            }}
            {"" if (self._settings and self._settings.value("material_preset", "ocean", type=str) == "halloween" or str(self.accent_color).lower() in ("#ffb77d", "#ff7518") or (self._settings and self._settings.value("theme_checkbox_unlit", "", type=str) and os.path.exists(self._settings.value("theme_checkbox_unlit", "", type=str)))) else f"""
            QTableWidget::indicator, QTableView::indicator {{
                width: 14px;
                height: 14px;
                background: transparent;
                border: 1.5px solid rgba({accent_r}, {accent_g}, {accent_b}, 0.47);
                border-radius: 4px;
            }}
            QTableWidget::indicator:unchecked, QTableView::indicator:unchecked {{
                background-color: transparent;
                border: 1.5px solid rgba({accent_r}, {accent_g}, {accent_b}, 0.47);
            }}
            QTableWidget::indicator:unchecked:hover, QTableView::indicator:unchecked:hover {{
                border: 1.5px solid rgba({accent_r}, {accent_g}, {accent_b}, 1.0);
                background-color: rgba({accent_r}, {accent_g}, {accent_b}, 0.078);
            }}
            QTableWidget::indicator:checked, QTableView::indicator:checked,
            QTableWidget::indicator:checked:hover, QTableView::indicator:checked:hover,
            QTableWidget::indicator:checked:selected, QTableView::indicator:checked:selected {{
                background-color: {self.accent_color} !important;
                border: 1.5px solid {self.accent_color} !important;
            }}
            """}
        """)

        self.header_view = DepotHeaderView(self)
        self.table_widget.setHorizontalHeader(self.header_view)
        self.header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table_widget.setColumnWidth(0, 130)
        self.header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.header_view.sectionClicked.connect(self._on_header_section_clicked)

        self.table_widget.setItemDelegateForColumn(0, DepotCheckboxDelegate(self))

        self._hidden_depots_expanded = False
        self._populate_table()

        content_widget.addWidget(self.table_widget)
 
        self.table_widget.cellClicked.connect(self.on_depot_cell_clicked)



    def _add_depot_rows(self, content_widget: QVBoxLayout) -> None:
        """Add the platform edition selector and Browse Files row.

        Skipped for a single-depot app, where there is no choice to make.
        """
        if not self.is_single_depot:
            # Platform Edition Selector + Browse Files sharing 50/50 space identically
            button_layout = QHBoxLayout()
            button_layout.setSpacing(10)
            button_layout.setContentsMargins(0, 0, 0, 0)

            btn_style = f"""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.07);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    border-radius: 6px;
                    color: #FFFFFF;
                    font-size: 8.5pt;
                    font-weight: 600;
                    padding: 4px 12px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.14);
                    border: 1px solid {self.accent_color};
                    color: #FFFFFF;
                }}
                QPushButton:pressed {{
                    background-color: rgba(255, 255, 255, 0.20);
                }}
            """

            self.edition_button = QPushButton("Complete ▾")
            self.edition_button.setFixedHeight(28)
            self.edition_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.edition_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.edition_button.setStyleSheet(btn_style)
            self.edition_button.setToolTip("Select game edition and platform (Complete, Deluxe, Standard)")
            self.edition_button.clicked.connect(self._show_edition_menu)
            button_layout.addWidget(self.edition_button, 1)

            browse_files_button = QPushButton("Browse Files...")
            browse_files_button.setFixedHeight(28)
            browse_files_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            browse_files_button.setCursor(Qt.CursorShape.PointingHandCursor)
            browse_files_button.setStyleSheet(btn_style)
            browse_files_button.setToolTip("Customize downloaded files within the selected depots")
            browse_files_button.clicked.connect(self._on_select_files_clicked)
            button_layout.addWidget(browse_files_button, 1)
            content_widget.addLayout(button_layout)

            from utils.edition_helpers import (
                get_available_platforms,
                resolve_multi_platform_editions,
            )
            self._available_platforms = get_available_platforms(self.depots)
            self._multi_platform_editions = resolve_multi_platform_editions(self.app_id, self.depots)
            self._update_table_headers()


    def _build_bottom_bar(self, content_widget: QVBoxLayout) -> None:
        """Build the storage picker plus OK and Cancel."""
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


    def _kick_off_enrichment_check(self) -> None:
        """Start background description/size enrichment for depots that need it."""
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

    def _build_config_text(self, d_id, d_data, is_first=False) -> str:
        original_desc = d_data.get("desc") or d_data.get("name") or ""
        for p in ("[Checking]", "[Missing]", "[Recovered]", "[No Key]", "[Missing from Hubcap]", "[Unavailable on Hubcap (404)]"):
            original_desc = original_desc.replace(p, "").strip()
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

        from utils.depot_tag_helpers import format_depot_row_label, get_os_icon
        os_icon = get_os_icon(d_data.get("oslist"))
        cfg_text = format_depot_row_label(
            str(d_id),
            d_data,
            final_desc,
            has_os_icon=bool(os_icon),
        )
        return cfg_text

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

        # Determine initial selection: default to highest Complete Edition for target OS
        if self.selected_depots is not None:
            pre_selected_set = set(str(d) for d in self.selected_depots)
        else:
            try:
                from utils.edition_helpers import (
                    get_available_platforms,
                    get_default_platform,
                    resolve_multi_platform_editions,
                )
                self._available_platforms = get_available_platforms(self.depots)
                self._default_platform = get_default_platform(self.depots)
                self._multi_platform_editions = resolve_multi_platform_editions(self.app_id, self.depots)

                default_eds = self._multi_platform_editions.get(self._default_platform, [])
                highest_ed = default_eds[0] if default_eds else None
                if highest_ed and highest_ed.get("depot_ids"):
                    pre_selected_set = set(highest_ed["depot_ids"])
                    if hasattr(self, "edition_button"):
                        short = highest_ed.get("short_name", "Complete")
                        if len(self._available_platforms) > 1:
                            plat_prefix = "Linux: " if self._default_platform == "linux" else "Win: "
                            self.edition_button.setText(f"{plat_prefix}{short} ▾")
                        else:
                            self.edition_button.setText(f"{short} ▾")
                else:
                    pre_selected_set = set(get_smart_default_depots(self.depots, target_platform=self._default_platform))
            except Exception as _e:
                logger.debug(f"[DepotSelection] Complete edition initial lookup failed: {_e}")
                pre_selected_set = set(get_smart_default_depots(self.depots, target_platform="linux"))

        include_hidden = getattr(self, "_show_hidden_depots", False) and bool(hidden_depots)
        total_rows = len(active_depots) + len(self.missing_hubcap_depots)
        if include_hidden:
            total_rows += 1 + len(hidden_depots)
        self.table_widget.setRowCount(total_rows)

        row_idx = 0
        is_first_depot = True

        # 1. Tier 0: Populate Active Depots
        for depot_id, depot_data in active_depots:
            config_text = self._build_config_text(depot_id, depot_data, is_first=is_first_depot)
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
                config_text = f"[Recovered]  {config_text}"
                try:
                    from managers.db_manager import DatabaseManager
                    DatabaseManager().clear_missing_hubcap_depot(str(self.app_id), str(depot_id))
                except Exception:
                    pass

            config_item = ConfigTableWidgetItem(config_text, tier=0)
            config_item.setFlags(config_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            from utils.depot_tag_helpers import get_os_icon
            os_ic = get_os_icon(depot_data.get("oslist"))
            if os_ic:
                config_item.setIcon(os_ic)

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
            if getattr(self, "_is_checking_missing_contents", False):
                tag = "[Checking]"
            elif hubcap_status == "missing_key":
                tag = "[No Key]"
            else:
                tag = "[Missing]"

            base_mcfg = self._build_config_text(did, minfo)
            mconfig_text = f"{tag}  {base_mcfg}"

            mid_val = int(did) if str(did).isdigit() else 0
            mid_item = NumericTableWidgetItem(str(did), mid_val, tier=1)
            mid_item.setData(Qt.ItemDataRole.UserRole, str(did))
            mid_item.setData(Qt.ItemDataRole.UserRole + 1, str(did))
            mid_item.setData(Qt.ItemDataRole.UserRole + 2, "missing")
            mid_item.setCheckState(Qt.CheckState.Unchecked)
            mid_item.setFlags(Qt.ItemFlag.NoItemFlags)
            mid_item.setForeground(darker_grey)

            mconfig_item = ConfigTableWidgetItem(mconfig_text, tier=1)
            from utils.depot_tag_helpers import get_os_icon
            m_ic = get_os_icon(minfo.get("oslist"))
            if m_ic:
                mconfig_item.setIcon(m_ic)
            mconfig_item.setFlags(Qt.ItemFlag.NoItemFlags)
            mconfig_item.setForeground(darker_grey)
            cfg_font = mconfig_item.font()
            cfg_font.setItalic(True)
            mconfig_item.setFont(cfg_font)

            if hubcap_status == "missing_key":
                tip = f"Depot {did} has a manifest but no AES decryption key available. It cannot be downloaded."
                mid_item.setToolTip(tip)
                mconfig_item.setToolTip(tip)

            msize_item = NumericTableWidgetItem(msize_str, mraw_size, tier=1)
            msize_item.setFlags(Qt.ItemFlag.NoItemFlags)
            msize_item.setForeground(darker_grey)
            sz_font = msize_item.font()
            sz_font.setItalic(True)
            msize_item.setFont(sz_font)

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
                hconfig_text = self._build_config_text(depot_id, depot_data, is_first=False)
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
                from utils.depot_tag_helpers import get_os_icon
                h_ic = get_os_icon(depot_data.get("oslist"))
                if h_ic:
                    hconfig_item.setIcon(h_ic)
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
        # Row order comes from get_sort_key() above. Click-to-sort is
        # intentionally NOT enabled: reordering rows under the cursor fought
        # the header select-all checkbox, so a tick-all looked like it had
        # missed depots.
        self.table_widget.setSortingEnabled(False)
        self._update_table_headers()

    def _update_table_headers(self):
        """Updates horizontal table header labels with selected/total depot counts and selected size."""
        if not hasattr(self, "table_widget") or not self.table_widget:
            return

        total_count = 0
        sel_count = 0
        total_bytes = 0

        for r in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(r, 0)
            if not id_item:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role == "expander":
                continue

            total_count += 1
            if id_item.checkState() == Qt.CheckState.Checked:
                sel_count += 1
                size_item = self.table_widget.item(r, 2)
                if size_item and hasattr(size_item, "sort_value"):
                    total_bytes += int(size_item.sort_value or 0)

        if total_count == 0:
            hdr_state = Qt.CheckState.Unchecked
        elif sel_count == total_count:
            hdr_state = Qt.CheckState.Checked
        elif sel_count == 0:
            hdr_state = Qt.CheckState.Unchecked
        else:
            hdr_state = Qt.CheckState.PartiallyChecked

        if hasattr(self, "header_view") and self.header_view:
            self.header_view.set_check_state_and_count(hdr_state, f"({sel_count}/{total_count})")

        id_header = ""
        config_header = "Configuration"
        size_header = f"Size ({format_size(total_bytes)})"

        for col, text in enumerate([id_header, config_header, size_header]):
            h_item = self.table_widget.horizontalHeaderItem(col)
            if not h_item:
                h_item = QTableWidgetItem(text)
                self.table_widget.setHorizontalHeaderItem(col, h_item)
            else:
                h_item.setText(text)
            if col == 0:
                h_item.setToolTip("Click to toggle Select All / Select None")
            h_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.table_widget.resizeColumnToContents(2)

        if hasattr(self, "edition_button"):
            if not hasattr(self, "_multi_platform_editions") or not self._multi_platform_editions:
                from utils.edition_helpers import (
                    get_available_platforms,
                    resolve_multi_platform_editions,
                )
                self._available_platforms = get_available_platforms(self.depots)
                self._multi_platform_editions = resolve_multi_platform_editions(self.app_id, self.depots)

            from utils.edition_helpers import match_selection_to_edition
            current_sel = set(self.get_selected_depots())
            matched = match_selection_to_edition(current_sel, self._multi_platform_editions)
            if matched:
                plat = matched.get("platform", "")
                full_name = matched.get("name") or matched.get("short_name", "Edition")
                clean_name = re.sub(r"\s*\(All DLCs\)", "", full_name, flags=re.IGNORECASE).strip()
                clean_name = re.sub(r"\s*\(Base Game Only\)", "", clean_name, flags=re.IGNORECASE).strip()

                if len(getattr(self, "_available_platforms", [])) > 1:
                    plat_prefix = "Linux: " if plat == "linux" else "Win: "
                    btn_text = f"{plat_prefix}{clean_name} ▾"
                else:
                    btn_text = f"{clean_name} ▾"

                if len(btn_text) > 42:
                    display_text = f"{btn_text[:38].rstrip()}... ▾"
                else:
                    display_text = btn_text

                self.edition_button.setText(display_text)
                self.edition_button.setToolTip(f"{plat.capitalize() + ': ' if plat else ''}{clean_name}")
            else:
                self.edition_button.setText("Custom ▾")
                self.edition_button.setToolTip("Custom depot selection")

    def _on_header_section_clicked(self, logical_index: int):
        if logical_index == 0:
            total = 0
            checked = 0
            for r in range(self.table_widget.rowCount()):
                item = self.table_widget.item(r, 0)
                if item and item.data(Qt.ItemDataRole.UserRole + 2) != "expander":
                    total += 1
                    if item.checkState() == Qt.CheckState.Checked:
                        checked += 1
            if checked == total and total > 0:
                self._toggle_all_checkboxes(check=False)
            else:
                self._toggle_all_checkboxes(check=True)

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
                for field in ("name", "desc", "oslist", "dlcappid", "is_dlc"):
                    if not info.get(field) and src.get(field):
                        info[field] = src[field]

            if did_str in cached_enrichments and isinstance(cached_enrichments[did_str], dict):
                src = cached_enrichments[did_str]
                for field in ("name", "desc", "oslist", "dlcappid", "is_dlc"):
                    if not info.get(field) and src.get(field):
                        info[field] = src[field]
                if not info.get("size") and src.get("size_bytes"):
                    info["size"] = src["size_bytes"]

            if not info.get("name"):
                try:
                    from core.ini_parser import parse_depots_ini
                    ini_names = parse_depots_ini()
                    if did_str in ini_names:
                        info["name"] = ini_names[did_str]
                except Exception:
                    pass

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
                    self._missing_depots_updated_signal.emit()
            except Exception as e:
                logger.debug(f"[DepotSelection] Async missing depots fetch error: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_missing_depots_updated(self):
        """Called on the main thread when async steamcmd metadata resolution finishes."""
        darker_grey = QColor(135, 135, 135)
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
                    tag = "[Checking]" if getattr(self, "_is_checking_missing_contents", False) else "[Missing]"
                    base_cfg = self._build_config_text(did, minfo)
                    cfg_text = f"{tag}  {base_cfg}"
                    cfg_item = self.table_widget.item(row, 1)
                    if cfg_item:
                        cfg_item.setText(cfg_text)
                        cfg_item.setForeground(darker_grey)
                        f = cfg_item.font()
                        f.setItalic(True)
                        cfg_item.setFont(f)
                    sz_item = self.table_widget.item(row, 2)
                    if sz_item:
                        sz_item.setText(msize_str)
                        sz_item.setForeground(darker_grey)
                        sf = sz_item.font()
                        sf.setItalic(True)
                        sz_item.setFont(sf)
                        if hasattr(sz_item, "sort_value"):
                            sz_item.sort_value = mraw_size

            if hasattr(self, "linux_button") and self.linux_button:
                has_linux = self._has_native_linux_depots()
                self.linux_button.setEnabled(has_linux)
                if has_linux:
                    self.linux_button.setToolTip("Smart select Linux installation (Native Linux if available; excludes media/32-bit)")
                else:
                    self.linux_button.setToolTip("No native Linux depots available for this game")

    def _on_spinner_tick(self):
        """Advances the circular spinner animation angle and requests a redraw."""
        self._spinner_angle = (self._spinner_angle + 6) % 360
        if hasattr(self, "table_widget") and self.table_widget:
            self.table_widget.viewport().update()

    def _check_missing_contents_async(self):
        """Asynchronously checks Hubcap /contents (0-quota) in the background to see if missing depots are now available."""
        if not self.missing_hubcap_depots:
            return

        self._is_checking_missing_contents = True
        if hasattr(self, "_spinner_timer") and not self._spinner_timer.isActive():
            self._spinner_timer.start(16)

        import threading
        target_dids = list(self.missing_hubcap_depots)
        app_id_str = str(self.app_id)
        branch_str = str(self.branch or "public")

        def _worker():
            recovered = {}
            try:
                from core import morrenus_api
                logger.debug(f"[DepotSelection] Checking /contents for App {app_id_str} in background...")
                contents_data = morrenus_api.get_manifest_contents(app_id_str, branch=branch_str)
                if isinstance(contents_data, dict) and "error" not in contents_data:
                    hubcap_depot_ids = contents_data.get("depot_ids", set())
                    manifest_map = contents_data.get("manifest_map") or {}
                    if not manifest_map and isinstance(contents_data.get("manifests"), list):
                        manifest_map = {
                            str(m["depot_id"]): str(m.get("manifest_id", ""))
                            for m in contents_data["manifests"]
                            if isinstance(m, dict) and m.get("depot_id")
                        }

                    from managers.depot_key_manager import DepotKeyManager
                    cached_keys = DepotKeyManager().get_depot_keys(app_id_str) or {}
                    hubcap_key_index = morrenus_api.get_hubcap_depot_keys()
                    existing_key_dids = hubcap_key_index.get("existing_depot_ids", set()) if isinstance(hubcap_key_index, dict) else set()

                    # 1. Check if any missing depots lacking keys now have keys in Hubcap
                    needs_bundle_refetch = False
                    for did in target_dids:
                        did_str = str(did)
                        if did_str in existing_key_dids and not cached_keys.get(did_str):
                            logger.info(f"[DepotSelection] Missing depot {did_str} now has an AES decryption key on Hubcap!")
                            needs_bundle_refetch = True
                            break

                    # If Hubcap has added keys, refetch the fresh manifest zip to extract Lua keys and manifests
                    if needs_bundle_refetch:
                        logger.info(f"[DepotSelection] Refetching fresh manifest bundle for App {app_id_str} to extract new keys...")
                        fresh_zip, _ = morrenus_api.download_manifest(app_id_str, branch=branch_str, force_update=True)
                        if fresh_zip and Path(fresh_zip).exists():
                            try:
                                from core.tasks.process_zip_task import ProcessZipTask
                                parsed_fresh = ProcessZipTask().run(str(fresh_zip))
                                if parsed_fresh and parsed_fresh.get("depots"):
                                    cached_keys = DepotKeyManager().get_depot_keys(app_id_str) or {}
                                    for f_did, f_ddata in parsed_fresh["depots"].items():
                                        f_did_str = str(f_did)
                                        if f_did_str in target_dids:
                                            self.depots[f_did_str] = f_ddata
                                            f_mid = f_ddata.get("manifest_id")
                                            if not f_mid and isinstance(f_ddata.get("manifests"), dict):
                                                f_mid = (f_ddata["manifests"].get(branch_str) or f_ddata["manifests"].get("public") or {}).get("gid")
                                            if f_mid:
                                                recovered[f_did_str] = str(f_mid)
                            except Exception as _pe:
                                logger.warning(f"[DepotSelection] Error parsing freshly fetched zip: {_pe}")

                    # 2. Check remaining missing depots
                    for did in target_dids:
                        did_str = str(did)
                        if did_str in recovered:
                            continue
                        info = self.missing_depots_info.get(did_str, {})
                        # A depot cannot be recovered or downloaded if it lacks an AES decryption key
                        has_key = bool(
                            cached_keys.get(did_str)
                            or info.get("key")
                            or (self.depots.get(did_str) or {}).get("key")
                        )
                        if info.get("hubcap_status") == "missing_key" and not has_key:
                            continue

                        if did_str in hubcap_depot_ids:
                            mid = manifest_map.get(did_str)
                            if not mid:
                                mid = info.get("manifest_id") or info.get("gid")
                            if mid:
                                recovered[did_str] = str(mid)
            except Exception as e:
                logger.debug(f"[DepotSelection] Background /contents check error: {e}")
            finally:
                self._missing_contents_checked_signal.emit(recovered)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_missing_contents_check_finished(self, recovered: Dict[str, str]):
        """Called when background /contents check finishes. Settles spinners into final disabled state."""
        self._is_checking_missing_contents = False
        if hasattr(self, "_spinner_timer") and self._spinner_timer.isActive():
            self._spinner_timer.stop()

        if recovered:
            self._on_missing_depots_recovered(recovered)

        if hasattr(self, "table_widget") and self.table_widget:
            darker_grey = QColor(135, 135, 135)
            for row in range(self.table_widget.rowCount()):
                id_item = self.table_widget.item(row, 0)
                if id_item and id_item.data(Qt.ItemDataRole.UserRole + 2) == "missing":
                    did_str = str(id_item.data(Qt.ItemDataRole.UserRole))
                    minfo = self.missing_depots_info.get(did_str, {})
                    hubcap_status = minfo.get("hubcap_status")
                    if hubcap_status == "missing_key":
                        tag = "[No Key]"
                        tip = f"Depot {did_str} has a manifest on Hubcap but lacks an AES decryption key. It cannot be downloaded."
                    else:
                        tag = "[Missing]"
                        tip = "Checked: not available on Hubcap yet"

                    base_cfg = self._build_config_text(did_str, minfo)
                    cfg_text = f"{tag}  {base_cfg}"

                    config_item = self.table_widget.item(row, 1)
                    if config_item:
                        config_item.setText(cfg_text)
                        config_item.setForeground(darker_grey)
                        f = config_item.font()
                        f.setItalic(True)
                        config_item.setFont(f)
                        config_item.setToolTip(tip)
                    id_item.setToolTip(tip)

            self.table_widget.viewport().update()

    def _on_missing_depots_recovered(self, recovered_dict: Dict[str, str]):
        """Upgrades recovered depots from greyed-out missing to active selectable depots in the UI."""
        if not recovered_dict or not hasattr(self, "table_widget") or not self.table_widget:
            return

        logger.info(f"[DepotSelection] Upgrading {len(recovered_dict)} recovered depot(s) in UI: {list(recovered_dict.keys())}")
        for did, mid in recovered_dict.items():
            did_str = str(did)
            self._recovered_depots_map[did_str] = str(mid)
            if did_str in self.missing_hubcap_depots:
                self.missing_hubcap_depots.remove(did_str)

            # Clear from DB tracking so [Recovered] shows only once during this session
            try:
                from managers.db_manager import DatabaseManager
                DatabaseManager().clear_missing_hubcap_depot(str(self.app_id), did_str)
            except Exception:
                pass

            # Resolve depot metadata for the recovered depot
            d_data = dict(self.depots.get(did_str) or self.depots.get(int(did_str) if did_str.isdigit() else None) or {})
            if not d_data:
                d_data = dict(self.missing_depots_info.get(did_str) or {})

            try:
                from managers.db_manager import DatabaseManager
                app_info = DatabaseManager().get_app_info(str(self.app_id))
                if app_info and isinstance(app_info.get("depots"), dict):
                    db_depot = app_info["depots"].get(did_str) or app_info["depots"].get(int(did_str) if did_str.isdigit() else None)
                    if db_depot and isinstance(db_depot, dict):
                        for k, v in db_depot.items():
                            if not d_data.get(k) and v:
                                d_data[k] = v
            except Exception as e:
                logger.debug(f"[DepotSelection] DB lookup for recovered depot {did_str} failed: {e}")

            self.depots[did_str] = d_data

            for row in range(self.table_widget.rowCount()):
                id_item = self.table_widget.item(row, 0)
                if id_item and str(id_item.data(Qt.ItemDataRole.UserRole)) == did_str:
                    config_item = self.table_widget.item(row, 1)
                    size_item = self.table_widget.item(row, 2)

                    id_item.setData(Qt.ItemDataRole.UserRole + 2, "normal")
                    id_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                    id_item.setForeground(QColor(255, 255, 255))

                    # Restore check state if it was previously selected or in target edition
                    should_check = False
                    if self.selected_depots is not None:
                        should_check = did_str in set(str(d) for d in self.selected_depots)
                    elif hasattr(self, "_multi_platform_editions"):
                        default_eds = getattr(self, "_multi_platform_editions", {}).get(getattr(self, "_default_platform", "linux"), [])
                        highest_ed = default_eds[0] if default_eds else None
                        if highest_ed and highest_ed.get("depot_ids"):
                            should_check = did_str in set(str(d) for d in highest_ed["depot_ids"])

                    id_item.setCheckState(Qt.CheckState.Checked if should_check else Qt.CheckState.Unchecked)

                    if config_item:
                        base_txt = self._build_config_text(did_str, d_data)
                        config_item.setText(f"[Recovered]  {base_txt}")
                        config_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                        config_item.setForeground(QColor(255, 255, 255))
                        f = config_item.font()
                        f.setItalic(False)
                        config_item.setFont(f)
                        config_item.setToolTip("")

                    id_item.setToolTip("")

                    if size_item:
                        size_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                        size_item.setForeground(QColor(255, 255, 255))
                        sf = size_item.font()
                        sf.setItalic(False)
                        size_item.setFont(sf)
                        mraw_size = int(d_data.get("size") or 0)
                        if not mraw_size and isinstance(d_data.get("manifests"), dict):
                            manifests = d_data["manifests"]
                            b_entry = manifests.get(self.branch) or manifests.get("public")
                            if isinstance(b_entry, dict):
                                mraw_size = int(b_entry.get("size") or b_entry.get("download") or 0)
                            if not mraw_size:
                                for m_val in manifests.values():
                                    if isinstance(m_val, dict) and (m_val.get("size") or m_val.get("download")):
                                        mraw_size = int(m_val.get("size") or m_val.get("download") or 0)
                                        break
                        if mraw_size > 0:
                            size_item.setText(format_size(mraw_size))
                            if hasattr(size_item, "sort_value"):
                                size_item.sort_value = mraw_size
                    break

        if hasattr(self, "linux_button") and self.linux_button:
            self.linux_button.setEnabled(self._has_native_linux_depots())
        self._update_table_headers()
        self.table_widget.viewport().update()

    def _is_manifest_on_disk(self, did: str, mid: str) -> bool:
        """Checks if the .manifest file is already saved in persistent or temporary manifests directory."""
        from pathlib import Path
        import tempfile
        from utils.helpers import get_base_path
        for s_dir in [Path(tempfile.gettempdir()) / "mistwalker_manifests", Path(get_base_path()) / "manifests"]:
            if (s_dir / f"{did}_{mid}.manifest").exists():
                return True
        return False

    def _apply_depot_enrichments(self, enrichments: dict):
        """Merges enriched metadata into self.depots dictionary."""
        try:
            if enrichments and self.depots:
                for did, info in enrichments.items():
                    target_depot_ids = []
                    if did in self.depots:
                        target_depot_ids.append(did)
                    # Also match any depot whose dlcappid matches did
                    for dep_k, dep_v in self.depots.items():
                        if isinstance(dep_v, dict) and str(dep_v.get("dlcappid") or "") == str(did):
                            if dep_k not in target_depot_ids:
                                target_depot_ids.append(dep_k)

                    for target_did in target_depot_ids:
                        d_data = self.depots[target_did]
                        curr_desc = str(d_data.get("desc") or "").strip()
                        is_generic = (
                            not curr_desc
                            or bool(re.match(r"^(?:\[.*?\]\s*)?Depot \d+$", curr_desc, re.IGNORECASE))
                            or bool(re.match(r"^(?:\[.*?\]\s*)?DLC \d+$", curr_desc, re.IGNORECASE))
                        )
                        dlc_id = info.get("dlcappid") or d_data.get("dlcappid") or did
                        dlc_tag = f"[DLC {dlc_id}]" if dlc_id and str(dlc_id).isdigit() else "[DLC]"
                        if is_generic and info.get("name"):
                            if info.get("is_dlc") or d_data.get("dlcappid"):
                                d_data["desc"] = f"{dlc_tag} {info['name']}"
                            else:
                                d_data["desc"] = info["name"]
                            d_data["name"] = info["name"]
                        if (not d_data.get("size") or d_data.get("size") == 0) and info.get("size_bytes"):
                            d_data["size"] = info["size_bytes"]
                        if info.get("size_str"):
                            d_data["size_str"] = info["size_str"]
                        if info.get("oslist") and not d_data.get("oslist"):
                            d_data["oslist"] = info["oslist"]
                        if info.get("is_dlc") or d_data.get("dlcappid"):
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
                # Only return early if no active depots still require generic resolution
                still_unresolved = any(
                    isinstance(d, dict) and (
                        not str(d.get("desc") or "").strip()
                        or bool(re.match(r"^(?:\[.*?\]\s*)?(?:Depot|DLC)\s+\d+$", str(d.get("desc") or "").strip(), re.IGNORECASE))
                    )
                    for d in self.depots.values()
                )
                if not still_unresolved and not force:
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
            info = depots_info.get(depot_id)
            if not info:
                # Also check matching by dlcappid
                d_data = self.depots.get(depot_id, {})
                d_dlc = str(d_data.get("dlcappid") or "")
                if d_dlc and d_dlc in depots_info:
                    info = depots_info[d_dlc]

            if info:
                config_item = self.table_widget.item(row, 1)
                size_item = self.table_widget.item(row, 2)

                if role == "missing":
                    curr_config = config_item.text().strip() if config_item else ""
                    if info.get("name") and "Depot " in curr_config:
                        tag = "[Checking]" if getattr(self, "_is_checking_missing_contents", False) else "[Missing]"
                        config_item.setText(f"{tag}  {info['name']}")
                        f = config_item.font()
                        f.setItalic(True)
                        config_item.setFont(f)
                else:
                    curr_config = config_item.text().strip() if config_item else ""
                    clean_curr = re.sub(r"^\[.*?\]\s*", "", curr_config).strip()
                    if not clean_curr or re.match(r"^(?:Depot|DLC)\s+\d+$", clean_curr, re.IGNORECASE):
                        name = info.get("name", "")
                        if name:
                            d_data = self.depots.get(depot_id, {})
                            dlc_id = info.get("dlcappid") or d_data.get("dlcappid")
                            is_dlc = info.get("is_dlc") or bool(dlc_id)
                            os_val = info.get("oslist") or d_data.get("oslist")
                            os_prefix = f"[{os_val.upper()}]" if os_val else ""
                            dlc_tag = f"[DLC {dlc_id}]" if dlc_id else "[DLC]"
                            if is_dlc:
                                tags = f"{os_prefix} {dlc_tag}".strip()
                            else:
                                tags = os_prefix
                            new_text = f"{tags}  {name}".strip() if tags else name
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

        self._update_table_headers()
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
        self._update_table_headers()

    def _setup_storage_buttons(self, layout: QHBoxLayout) -> None:
        import shutil
        from core.steam_helpers import get_steam_libraries, find_steam_install
        from utils.paths import is_valid_download_directory

        storage_paths = []
        try:
            raw_libs = get_steam_libraries() or []
            for p in raw_libs:
                if p and is_valid_download_directory(p):
                    real_p = os.path.realpath(p)
                    if real_p not in storage_paths:
                        storage_paths.append(real_p)
        except Exception as e:
            logger.warning(f"Error discovering Steam storage libraries: {e}")

        def_dir = self._settings.value("default_download_directory", "", type=str) if self._settings else ""
        if def_dir and is_valid_download_directory(def_dir):
            real_def = os.path.realpath(def_dir)
            if real_def not in storage_paths:
                storage_paths.append(real_def)

        if self.preferred_library_path and is_valid_download_directory(self.preferred_library_path):
            real_pref = os.path.realpath(self.preferred_library_path)
            if real_pref not in storage_paths:
                storage_paths.append(real_pref)

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
                lbl_text, free_str, tip_text = _format_storage_info(spath)
                item_text = f"{lbl_text} ({free_str})" if free_str else lbl_text
                self._more_storage_combo.addItem(item_text, spath)

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

        self._update_table_headers()

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
        self._update_table_headers()

    def _on_smart_select_clicked(self):
        """Overwrites user selection with the depots Steam normally installs from package info / appcache."""
        self._user_interacted = True
        self._has_saved_selection = True

        try:
            from core.steam_package_info import get_steam_recommended_depots
            rec_depots = get_steam_recommended_depots(self.app_id, self.depots, target_platform="linux")
        except Exception as err:
            logger.warning(f"[DepotSelection] Smart Select error: {err}")
            rec_depots = []

        if not rec_depots:
            rec_depots = get_smart_default_depots(self.depots, target_platform="linux")

        smart_depots = set(str(d) for d in rec_depots)

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
        self._update_table_headers()

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
        self._update_table_headers()

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
        pos = self.edition_button.mapToGlobal(QPoint(0, -menu_height - 2))
        if pos.y() < 0:
            pos = self.edition_button.mapToGlobal(QPoint(0, self.edition_button.height() + 2))
        menu.exec(pos)

    def _apply_edition_selection(self, edition: dict):
        """Apply the chosen edition to the depot checkboxes."""
        self._user_interacted = True
        self._has_saved_selection = True
        full_name = edition.get("name") or edition.get("short_name", "Edition")
        clean_name = re.sub(r"\s*\(All DLCs\)", "", full_name, flags=re.IGNORECASE).strip()
        clean_name = re.sub(r"\s*\(Base Game Only\)", "", clean_name, flags=re.IGNORECASE).strip()
        plat = edition.get("platform", "")
        if getattr(self, "_available_platforms", None) and len(self._available_platforms) > 1:
            plat_prefix = "Linux: " if plat == "linux" else "Win: "
            btn_text = f"{plat_prefix}{clean_name} ▾"
        else:
            btn_text = f"{clean_name} ▾"

        if len(btn_text) > 42:
            display_text = f"{btn_text[:38].rstrip()}... ▾"
        else:
            display_text = btn_text

        if hasattr(self, "edition_button"):
            self.edition_button.setText(display_text)
            self.edition_button.setToolTip(f"{plat.capitalize() + ': ' if plat else ''}{clean_name}")
        target_depots = {str(d) for d in edition.get("depot_ids", set())}

        self.table_widget.blockSignals(True)
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander"):
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            if depot_id in target_depots:
                id_item.setCheckState(Qt.CheckState.Checked)
            else:
                id_item.setCheckState(Qt.CheckState.Unchecked)
        self.table_widget.blockSignals(False)
        self.anchor_row = -1
        self._update_table_headers()

    def _select_baseline_depots(self):
        """Legacy helper for selecting standard/baseline edition."""
        if hasattr(self, "_available_editions"):
            for ed in self._available_editions:
                if ed.get("id") == "standard":
                    self._apply_edition_selection(ed)
                    return
        self._on_smart_select_clicked()

    def _verify_executable_selection(self, selected_depots: list) -> bool:
        """
        Feature B Pre-flight guard:
        Verify that at least one selected depot contains the primary launch executable.
        If missing, prompts user with option to automatically select the depot and continue.
        """
        if not self.app_id or not selected_depots:
            return True

        appid_str = str(self.app_id).strip()
        exe_name = None

        # 1. Try resolving launch executable from game_data
        if hasattr(self, "game_data") and isinstance(self.game_data, dict):
            exe_name = self.game_data.get("executable") or self.game_data.get("launch_executable")

        # 2. Try resolving from local Steam appinfo.vdf
        if not exe_name:
            try:
                import struct
                for p in [
                    Path.home() / ".local/share/Steam/appcache/appinfo.vdf",
                    Path.home() / ".steam/steam/appcache/appinfo.vdf",
                    Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam/appcache/appinfo.vdf",
                ]:
                    if p.is_file():
                        with open(p, "rb") as f:
                            f.seek(16)
                            appid_num = int(appid_str)
                            while True:
                                buf = f.read(4)
                                if len(buf) < 4:
                                    break
                                aid = struct.unpack("<I", buf)[0]
                                if aid == 0:
                                    break
                                size = struct.unpack("<I", f.read(4))[0]
                                f.seek(48, 1)
                                vdf_data = f.read(size - 60)
                                if aid == appid_num:
                                    m = re.search(rb"executable\x00([^\x00]+)\x00", vdf_data)
                                    if m:
                                        exe_name = m.group(1).decode("latin1", errors="ignore").replace("\\", "/")
                                        break
                        if exe_name:
                            break
            except Exception as e:
                logger.debug(f"[DepotSelection] appinfo.vdf exe lookup error: {e}")

        if not exe_name:
            return True

        exe_filename = Path(exe_name).name.lower()
        if not exe_filename:
            return True

        # 3. Check which depot holds this executable
        depot_with_exe = None
        target_bytes = exe_filename.encode("latin1", errors="ignore")

        # Check in local hubcap zip bundle if present
        from utils.helpers import get_base_path
        zip_candidates = [
            Path(get_base_path()) / "hubcap_manifests" / f"accela_fetch_{appid_str}.zip",
            Path.home() / ".local/share/ACCELA/hubcap_manifests" / f"accela_fetch_{appid_str}.zip",
        ]
        import zipfile
        for z_path in zip_candidates:
            if z_path.is_file():
                try:
                    with zipfile.ZipFile(z_path, "r") as zf:
                        for mf in zf.namelist():
                            if mf.endswith(".manifest"):
                                raw = zf.read(mf)
                                if target_bytes in raw.lower():
                                    depot_with_exe = mf.split("_")[0]
                                    break
                except Exception:
                    pass
            if depot_with_exe:
                break

        # Check loose manifests in cache
        if not depot_with_exe:
            manifest_dirs = [
                Path(get_base_path()) / "manifests",
                Path.home() / ".local/share/Steam/steamapps/depotcache",
            ]
            for m_dir in manifest_dirs:
                if m_dir.is_dir():
                    try:
                        for mf in m_dir.glob("*.manifest"):
                            parts = mf.stem.split("_")
                            if len(parts) >= 1:
                                raw = mf.read_bytes()
                                if target_bytes in raw.lower():
                                    depot_with_exe = parts[0]
                                    break
                    except Exception:
                        pass
                if depot_with_exe:
                    break

        # If a depot containing the executable is found, check if it's selected
        if depot_with_exe and str(depot_with_exe) not in [str(d) for d in selected_depots]:
            reply = QMessageBox.warning(
                self,
                "Missing Game Executable",
                f"The launch executable '{Path(exe_name).name}' was detected in Depot {depot_with_exe}, "
                f"which is currently NOT selected.\n\n"
                f"Without this depot, Steam will fail to launch the game ('Missing Executable').\n\n"
                f"Would you like to select Depot {depot_with_exe} and proceed?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Ignore | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.table_widget.blockSignals(True)
                for i in range(self.table_widget.rowCount()):
                    id_item = self.table_widget.item(i, 0)
                    if id_item and str(id_item.data(Qt.ItemDataRole.UserRole)) == str(depot_with_exe):
                        id_item.setCheckState(Qt.CheckState.Checked)
                        break
                self.table_widget.blockSignals(False)
                self._update_table_headers()
                return True
            elif reply == QMessageBox.StandardButton.Ignore:
                return True
            else:
                return False

        return True

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
        if not self._dlc_only_btn.isEnabled():
            self._dlc_only_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.03) !important;
                    color: rgba(255, 255, 255, 0.25) !important;
                    border: 1px solid rgba(255, 255, 255, 0.06) !important;
                    border-radius: 4px;
                    padding: 4px 10px;
                }
            """)
            return

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
                        warn_fn = None
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
            from utils.steam_manifest import safe_extract_zip
            safe_extract_zip(zip_path, temp_dir)
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
                    match = find_live(
                        lua_content,
                        r"addappid\(\s*" + re.escape(target_depot) + r"\s*,\s*\d+\s*,\s*\"([a-fA-F0-9]+)\"\)",
                    )
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

            if not success:
                if os.path.exists(keys_path):
                    try:
                        os.remove(keys_path)
                    except OSError:
                        pass
                QMessageBox.critical(self, "Error", f"Failed to load file list: {err}")
                return

            txt_path = os.path.join(temp_dir, f"manifest_{target_depot}_{manifest_id}.txt")
            if not os.path.exists(txt_path):
                if os.path.exists(keys_path):
                    try:
                        os.remove(keys_path)
                    except OSError:
                        pass
                QMessageBox.critical(self, "Error", "Failed to locate generated file list text file.")
                return

            # Open File Selection Tree Dialog
            from ui.dialogs.fileselection import FileSelectionDialog
            sel_dialog = FileSelectionDialog(
                app_id=app_id,
                depot_id=target_depot,
                manifest_txt_path=txt_path,
                parent=self,
                manifest_file=manifest_file,
                keys_path=keys_path,
                manifest_id=manifest_id,
            )
            try:
                if sel_dialog.exec():
                    self.selected_files = sel_dialog.selected_files
                    QMessageBox.information(
                        self,
                        "Selection Confirmed",
                        f"Selected {len(self.selected_files)} file(s) for custom download.\nPress OK at the bottom to start installing."
                    )
            finally:
                if os.path.exists(keys_path):
                    try:
                        os.remove(keys_path)
                    except OSError:
                        pass

        self.dump_thread.finished_signal.connect(on_dump_finished)
        self.dump_thread.start()

    def accept(self):
        # 0. On-demand manifest generation for any selected recovered depots that lack files on disk
        selected_for_check = self.get_selected_depots()
        if hasattr(self, "_recovered_depots_map") and self._recovered_depots_map:
            needs_gen = [
                did for did in selected_for_check
                if did in self._recovered_depots_map and not self._is_manifest_on_disk(did, self._recovered_depots_map[did])
            ]
            if needs_gen:
                from PyQt6.QtWidgets import QProgressDialog
                from pathlib import Path
                import tempfile
                from utils.helpers import get_base_path
                from core import morrenus_api
                progress = QProgressDialog(f"Generating manifest for {len(needs_gen)} recovered depot(s)...", None, 0, len(needs_gen), self)
                progress.setWindowModality(Qt.WindowModality.WindowModal)
                progress.show()
                for idx, r_did in enumerate(needs_gen):
                    r_mid = self._recovered_depots_map[r_did]
                    progress.setValue(idx)
                    progress.setLabelText(f"Generating manifest for depot {r_did}...")
                    QApplication.processEvents()
                    raw_bytes, g_err = morrenus_api.generate_single_manifest(r_did, r_mid)
                    if raw_bytes:
                        try:
                            from utils.manifest_resolver import sanitize_manifest_bytes
                            raw_bytes = sanitize_manifest_bytes(raw_bytes)
                        except Exception:
                            pass
                        for s_dir in [Path(tempfile.gettempdir()) / "mistwalker_manifests", Path(get_base_path()) / "manifests"]:
                            try:
                                s_dir.mkdir(parents=True, exist_ok=True)
                                (s_dir / f"{r_did}_{r_mid}.manifest").write_bytes(raw_bytes)
                            except Exception:
                                pass
                        self._manifest_overrides[str(r_did)] = str(r_mid)
                        logger.info(f"[DepotSelection] Generated manifest for recovered depot {r_did}_{r_mid}")
                    else:
                        logger.warning(f"[DepotSelection] Failed to generate manifest for recovered depot {r_did}: {g_err}")
                progress.close()

        # 1. Validate depot selection
        selected_depots = self.get_selected_depots()
        if not selected_depots:
            QMessageBox.warning(self, "No Depots Selected", "Please select at least one depot to proceed.")
            return

        # 1b. Missing executable pre-flight guard
        if not self._verify_executable_selection(selected_depots):
            return

        # Re-fetch selected depots in case user chose to add the executable depot
        selected_depots = self.get_selected_depots()

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
                    font-weight: 600;
                    padding: 2px 12px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.14);
                    border: 1px solid {self.accent_color};
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
                    padding: 2px 12px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.12);
                    border-color: rgba(255, 255, 255, 0.35);
                }}
            """)
        if hasattr(self, "builds_btn") and self.builds_btn is not None:
            self.builds_btn.adjustSize()

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
            if dlg.exec():
                selected_bid, patch_depots = dlg.get_selected_build()
                self._apply_build_selection(selected_bid, patch_depots)
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
                self._apply_build_selection(selected_bid, patch_depots)


    def closeEvent(self, a0):
        """Ensure image fetch and animation timer are cleaned up when dialog closes."""
        if hasattr(self, "_spinner_timer") and self._spinner_timer is not None:
            try:
                self._spinner_timer.stop()
            except Exception:
                pass
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
