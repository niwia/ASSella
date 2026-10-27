"""Static widget construction: the header (thumbnail, title, branch and
Build button), the depot table, the platform / DLC-only / Browse Files row
and the bottom OK / Cancel bar."""

import os
from typing import Tuple

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPalette, QColor

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QTableWidget,
    QHeaderView,
    QAbstractItemView,
    QComboBox,
    QSizePolicy,
)

from utils.settings import get_settings

import logging
from ui.dialogs.themed_message_box import ThemedMessageBox

logger = logging.getLogger(__name__)

from ui.dialogs.depot_selection.views import (
    DepotCheckboxDelegate,
    DepotHeaderView,
)


class BuildUIMixin:
    """Static widget construction for the header, table, action row and bottom bar."""

    def _load_settings(self) -> None:
        """Read the depot-visibility filters and accent colour from settings.

        A settings failure must not stop the dialog, so every filter falls
        back to its default and accent_color to the built-in pink.
        """
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

        try:
            from utils.color_utils import get_cached_thumbnail_color
            app_id = getattr(self, "app_id", None) or getattr(self, "_current_app_id", None)
            cached_col = get_cached_thumbnail_color(str(app_id)) if app_id else None
            if cached_col and cached_col.isValid():
                self._ambient_color = cached_col
                if hasattr(self, "title_bar") and self.title_bar:
                    self.title_bar.update_accent_color(cached_col)
        except Exception:
            pass

    def _resolve_accent_colors(self) -> Tuple[int, int, int]:
        """Turn the accent colour into an RGB triple for the table stylesheet.

        Returns the components rather than storing them so the caller keeps
        them as locals, matching how the stylesheet f-strings consume them.
        """
        hex_c = (self.accent_color or "#C06C84").lstrip('#')
        try:
            accent_r = int(hex_c[0:2], 16)
            accent_g = int(hex_c[2:4], 16)
            accent_b = int(hex_c[4:6], 16)
        except Exception:
            accent_r, accent_g, accent_b = 192, 108, 132

        return accent_r, accent_g, accent_b

    def _get_table_stylesheet(self, accent_color: str, accent_r: int, accent_g: int, accent_b: int) -> str:
        """Returns the stylesheet for the depot table matching the active/ambient accent."""
        return f"""
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
                border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            }}
            QTableWidget::item:disabled {{
                color: rgba(255, 255, 255, 0.35) !important;
                background-color: transparent !important;
            }}
            QTableWidget::item:disabled:hover {{
                background-color: transparent !important;
            }}
            QTableWidget::item:disabled:selected {{
                background-color: transparent !important;
                color: rgba(255, 255, 255, 0.35) !important;
            }}
            QTableWidget::item:hover {{
                background-color: rgba({accent_r}, {accent_g}, {accent_b}, 0.06);
            }}
            QTableWidget::item:selected {{
                background-color: rgba({accent_r}, {accent_g}, {accent_b}, 0.16) !important;
                color: #FFFFFF !important;
            }}
            QHeaderView::section {{
                background-color: #1b1e26;
                color: rgba(255, 255, 255, 0.78);
                padding: 6px 10px;
                border: none;
                font-size: 8.5pt;
                font-weight: bold;
                text-transform: uppercase;
            }}
            QHeaderView::section:first {{
                padding-left: 34px;
            }}
            QTableWidget::indicator {{
                width: 14px;
                height: 14px;
                background: transparent;
                border: 1px solid rgba(255, 255, 255, 0.35);
                border-radius: 3px;
            }}
            QTableWidget::indicator:checked {{
                background-color: {accent_color};
                border: 1px solid {accent_color};
            }}
            QTableWidget::indicator:disabled {{
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.12);
            }}
            QTableWidget::indicator:checked:disabled {{
                background-color: rgba(255, 255, 255, 0.20) !important;
                border: 1px solid rgba(255, 255, 255, 0.20) !important;
            }}
        """

    def _build_header_section(self, layout: QVBoxLayout) -> None:
        """Build the thumbnail banner, game title, branch/build badges, install path, and filter pills bar."""
        # Top Header Banner
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(14, 10, 14, 4)
        header_layout.setSpacing(14)

        # 1. Thumbnail / Capsule Image (184x86 in DLC mode, classic 120x56 otherwise)
        dlc_mode = bool(getattr(self, "_dlc_only_mode", False))
        self.header_label = QLabel()
        self.header_label.setFixedSize(*((184, 86) if dlc_mode else (120, 56)))
        self.header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if dlc_mode:
            self.header_label.setStyleSheet("""
                background-color: rgba(0, 0, 0, 0.4);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                color: rgba(255, 255, 255, 0.5);
                font-size: 8.5pt;
            """)
            self.header_label.setText("Loading banner...")
        else:
            self.header_label.setScaledContents(True)
            self.header_label.setStyleSheet("background-color: rgba(255, 255, 255, 0.04); border-radius: 6px;")
        header_layout.addWidget(self.header_label)

        # 2. Metadata Info Layout
        title_layout = QVBoxLayout()
        title_layout.setSpacing(4)
        title_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        title_hlayout = QHBoxLayout()
        title_hlayout.setSpacing(6)
        title_hlayout.setContentsMargins(0, 0, 0, 0)

        clean_name = str(self.game_name)
        if self.app_id and f"({self.app_id})" in clean_name:
            clean_name = clean_name.replace(f"({self.app_id})", "").strip()

        title_font_size = "11pt" if dlc_mode else "13pt"
        self.title_label = QLabel(clean_name)
        self.title_label.setStyleSheet(f"font-size: {title_font_size}; font-weight: bold; color: #FFFFFF;")
        title_hlayout.addWidget(self.title_label)

        if self.app_id and str(self.app_id) not in ("0", "unknown", "None", ""):
            aid_str = str(self.app_id).strip()
            self.appid_btn = QPushButton(f"({aid_str})")
            self.appid_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.appid_btn.setToolTip(f"Click to copy AppID ({aid_str})")
            aid_font_size = "9.5pt" if dlc_mode else "11.5pt"
            self.appid_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: none;
                    color: rgba(255, 255, 255, 0.55);
                    font-style: italic;
                    font-size: {aid_font_size};
                    padding: 0px 2px;
                    text-align: left;
                }}
                QPushButton:hover {{
                    color: #FFFFFF;
                    text-decoration: underline;
                }}
                QPushButton:pressed {{
                    color: #4CAF50;
                }}
            """)

            def _copy_appid():
                from PyQt6.QtWidgets import QApplication, QToolTip
                from PyQt6.QtCore import QRect
                QApplication.clipboard().setText(aid_str)
                pos = self.appid_btn.mapToGlobal(self.appid_btn.rect().bottomLeft())
                QToolTip.showText(pos, f"✓ Copied AppID: {aid_str}", self.appid_btn, QRect(), 2000)

            self.appid_btn.clicked.connect(_copy_appid)
            title_hlayout.addWidget(self.appid_btn)

        title_hlayout.addStretch()
        title_layout.addLayout(title_hlayout)

        # Badges row: Branch, Build ID, Selection summary
        badges_row = QHBoxLayout()
        badges_row.setSpacing(8)

        # Branch resolution
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
            branch_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.6);")
            badges_row.addWidget(branch_lbl)

            self.branch_combo = QComboBox()
            self.branch_combo.setFixedHeight(24)
            self.branch_combo.setMinimumWidth(110)
            self.branch_combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: rgba(255, 255, 255, 0.06);
                    border: 1px solid rgba(255, 255, 255, 0.16);
                    border-radius: 6px;
                    color: #FFFFFF;
                    padding: 1px 10px;
                    font-size: 8.5pt;
                    font-weight: 600;
                }}
                QComboBox:hover {{
                    border-color: rgba(255, 255, 255, 0.35);
                    background-color: rgba(255, 255, 255, 0.12);
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 16px;
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
            badges_row.addWidget(self.branch_combo)
        else:
            self.branch_combo = None
            self.branch_btn = QPushButton(f"Branch: {self.branch or 'public'}")
            self.branch_btn.setFixedHeight(24)
            self.branch_btn.setMinimumWidth(100)
            self.branch_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            self.branch_btn.setEnabled(False)
            self.branch_btn.setToolTip("No alternative branches available")
            self.branch_btn.setStyleSheet("""
                QPushButton, QPushButton:disabled {
                    background-color: rgba(255, 255, 255, 0.03);
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 6px;
                    color: rgba(255, 255, 255, 0.40);
                    font-size: 8.5pt;
                    font-weight: 600;
                    padding: 2px 12px;
                }
            """)
            badges_row.addWidget(self.branch_btn)

        badges_row.addSpacing(4)

        display_bid = self.current_build_id or "Latest"
        self.builds_btn = QPushButton(f"Build: {display_bid}")
        self.builds_btn.setToolTip(f"Build ID: {display_bid} (Click to switch builds)")
        self.builds_btn.setFixedHeight(24)
        self.builds_btn.setMinimumWidth(100)
        self.builds_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.builds_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_build_btn_style()
        self.builds_btn.clicked.connect(self._on_builds_clicked)
        badges_row.addWidget(self.builds_btn)

        self.summary_lbl = None
        self.show_hidden_chk = None
        self.smart_select_btn = None

        if self.branch and self.branch != "public":
            self._on_branch_changed(self.branch)

        badges_row.addStretch()
        title_layout.addLayout(badges_row)

        # Storage / Install Path row (the storage picker sits in the bottom bar
        # for classic installs, and in the header for DLC-only mode).
        if self.show_storage and dlc_mode:
            storage_row = QHBoxLayout()
            storage_row.setSpacing(8)
            storage_lbl = QLabel("Install Location:")
            storage_lbl.setStyleSheet("font-size: 8.5pt; font-weight: 600; color: rgba(255, 255, 255, 0.7);")
            storage_row.addWidget(storage_lbl)

            disp_path = self.preferred_library_path or ""
            if not disp_path:
                try:
                    from core.steam_helpers import get_steam_libraries
                    libs = get_steam_libraries()
                    if libs:
                        disp_path = libs[0]
                except Exception:
                    pass
            if not disp_path:
                disp_path = "Steam default directory"

            path_text = QLabel(disp_path)
            path_text.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.85); font-weight: 500;")
            storage_row.addWidget(path_text)
            storage_row.addStretch()
            title_layout.addLayout(storage_row)

        header_layout.addLayout(title_layout, 1)
        layout.addLayout(header_layout)

        # Filter Chips Bar (All, Unowned, Steam Owned, Injected, DlcData) — the
        # classic selector has no chips.
        if dlc_mode:
            chips_bar = self._build_filter_chips_bar()
            layout.addLayout(chips_bar)

        # Resolve missing depots info if needed
        if self.missing_hubcap_depots:
            self._resolve_missing_depots_info()
            self._check_missing_contents_async()

        self._fetch_header_image(self.app_id)

    def _build_filter_chips_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(14, 2, 14, 6)
        bar.setSpacing(6)

        self.filter_chips = {}
        # In DLC mode the owned rows cannot be picked, so open on what is missing.
        self._active_filter = "missing" if getattr(self, "_dlc_only_mode", False) else "all"

        chip_definitions = [
            ("all", "All", "#FFFFFF", "rgba(255, 255, 255, 0.12)", "rgba(255, 255, 255, 0.3)"),
            ("missing", "Unowned", "#f09a2f", "rgba(240, 154, 47, 0.16)", "rgba(240, 154, 47, 0.45)"),
            ("owned", "Steam Owned", "#25a878", "rgba(37, 168, 120, 0.16)", "rgba(37, 168, 120, 0.45)"),
            ("injected", "Injected", "#16a6c7", "rgba(22, 166, 199, 0.16)", "rgba(22, 166, 199, 0.45)"),
            ("dlcdata", "DlcData", "#eab308", "rgba(234, 179, 8, 0.16)", "rgba(234, 179, 8, 0.45)"),
        ]

        self._chip_colors = {}
        for key, label, fg_hex, bg_hex, border_hex in chip_definitions:
            self._chip_colors[key] = (fg_hex, bg_hex, border_hex)
            btn = QPushButton(self)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("filter_key", key)
            btn.clicked.connect(lambda checked, k=key: self._on_filter_chip_clicked(k))
            self.filter_chips[key] = btn
            bar.addWidget(btn)

        self._update_filter_chips_visual()
        bar.addStretch()
        return bar

    def _build_depot_table(
        self, content_widget: QVBoxLayout, accent_r: int, accent_g: int, accent_b: int,
    ) -> None:
        """Create the 3-column depot table, its header view and the checkbox delegate."""
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(3)
        self.table_widget.setHorizontalHeaderLabels([
            "DLC ID" if getattr(self, "_dlc_only_mode", False) else "Select",
            "Configuration",
            "Size",
        ])
        self.table_widget.setIconSize(QSize(76, 16))
        self.table_widget.verticalHeader().setVisible(False)
        self.table_widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table_widget.setShowGrid(False)
        self.table_widget.setAlternatingRowColors(True)

        self.table_widget.setStyleSheet(self._get_table_stylesheet(self.accent_color, accent_r, accent_g, accent_b))

        palette = self.table_widget.palette()
        palette.setColor(
            QPalette.ColorRole.AlternateBase,
            QColor("#111318").lighter(115),
        )
        self.table_widget.setPalette(palette)

        self.header_view = DepotHeaderView(self)
        self.table_widget.setHorizontalHeader(self.header_view)
        self.header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table_widget.setColumnWidth(0, 115)
        self.header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table_widget.setColumnWidth(2, 95)
        self.header_view.sectionClicked.connect(self._on_header_section_clicked)

        self.table_widget.setItemDelegateForColumn(0, DepotCheckboxDelegate(self))

        self._hidden_depots_expanded = False
        self._populate_table()

        content_widget.addWidget(self.table_widget)

        self.table_widget.cellClicked.connect(self.on_depot_cell_clicked)
        self.table_widget.itemSelectionChanged.connect(self._on_table_selection_changed)

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

            self._dlc_only_btn = None

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
            self._available_platforms = get_available_platforms(self.depots, self.app_id, allow_network=False)
            if not getattr(self, "_multi_platform_editions", None):
                # Offline on purpose: building the dialog never waits on
                # store.steampowered.com. The store tiers are fetched in the
                # background so the edition menu can show them when they land.
                self._multi_platform_editions = resolve_multi_platform_editions(
                    self.app_id, self.depots, include_store=False, allow_network=False
                )
            self._start_edition_prefetch()
            self._update_table_headers()

    def _build_bottom_bar(self, content_widget: QVBoxLayout) -> None:
        """Build the 3 action buttons: Inject DLC Data Only, Close, Download Selected."""
        dlc_mode = bool(getattr(self, "_dlc_only_mode", False))

        # Classic installs pick a drive above the buttons, the way the selector
        # always did; DLC-only mode keeps it in the header next to the badges.
        if self.show_storage and not dlc_mode:
            classic_storage_row = QHBoxLayout()
            classic_storage_row.setSpacing(8)
            self._setup_storage_buttons(classic_storage_row)
            content_widget.addLayout(classic_storage_row)

        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(0, 6, 0, 0)
        bottom_bar.setSpacing(8)

        # Selection summary is not shown in bottom left (status cleaned up)
        self.status_summary_label = None

        bottom_bar.addStretch(1)

        # [ Inject DLC Data Only ]  [ Move to AT0-M ]  [ Download ]
        btn_h = 32
        btn_min_w = 110

        self.inject_btn = QPushButton("Inject DLC Data Only")
        self.inject_btn.setFixedHeight(btn_h)
        self.inject_btn.setMinimumWidth(125)
        self.inject_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.inject_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(22, 166, 199, 0.16);
                border: 1px solid #16a6c7;
                border-radius: 5px;
                color: #ffffff;
                font-size: 8.5pt;
                font-weight: 600;
                padding: 0 14px;
            }
            QPushButton:hover {
                background-color: rgba(22, 166, 199, 0.28);
                border-color: #38bdf8;
            }
            QPushButton:disabled {
                background-color: rgba(255, 255, 255, 0.04) !important;
                border: 1px solid rgba(255, 255, 255, 0.08) !important;
                color: rgba(255, 255, 255, 0.28) !important;
            }
        """)
        self.inject_btn.setToolTip("Directly write checked DLC entitlements to SLSsteam config without downloading files")
        self.inject_btn.clicked.connect(self._handle_inject_btn_clicked)
        self.inject_btn.setVisible(dlc_mode)

        self.move_to_atom_btn = QPushButton("Move to AT0-M")
        self.move_to_atom_btn.setFixedHeight(btn_h)
        self.move_to_atom_btn.setMinimumWidth(110)
        self.move_to_atom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.move_to_atom_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(206, 147, 216, 0.16);
                border: 1px solid #CE93D8;
                border-radius: 5px;
                color: #CE93D8;
                font-size: 8.5pt;
                font-weight: 600;
                padding: 0 14px;
            }
            QPushButton:hover {
                background-color: rgba(206, 147, 216, 0.28);
                border-color: #E1BEE7;
                color: #FFFFFF;
            }
            QPushButton:disabled {
                background-color: rgba(255, 255, 255, 0.04) !important;
                border: 1px solid rgba(255, 255, 255, 0.08) !important;
                color: rgba(255, 255, 255, 0.28) !important;
            }
        """)
        is_atom = bool(getattr(self, "_is_atom_mode", False))
        if hasattr(self, "_handle_move_to_atom_clicked"):
            self.move_to_atom_btn.clicked.connect(self._handle_move_to_atom_clicked)
        self.move_to_atom_btn.setVisible(dlc_mode and not is_atom)

        # Close button in bottom buttons removed per UX requirement (dialog relies on window titlebar close button)
        self.cancel_btn = None

        from utils.color_utils import get_best_foreground_color
        text_hex = get_best_foreground_color(self.accent_color, dark_color="#111318", light_color="#FFFFFF")

        btn_text = "Register to AT0-M" if is_atom else "Download"
        self.download_btn = QPushButton(btn_text)
        self.download_btn.setObjectName("ok_button")
        self.download_btn.setFixedHeight(btn_h)
        self.download_btn.setMinimumWidth(125)
        self.download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: {text_hex};
                border: none;
                border-radius: 5px;
                font-weight: bold;
                font-size: 8.5pt;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background-color: #FFFFFF;
                color: #111318;
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.04) !important;
                border: 1px solid rgba(255, 255, 255, 0.08) !important;
                color: rgba(255, 255, 255, 0.28) !important;
            }}
        """)
        self.download_btn.clicked.connect(self.accept)

        bottom_bar.addWidget(self.inject_btn)
        bottom_bar.addWidget(self.move_to_atom_btn)
        bottom_bar.addWidget(self.download_btn)
        content_widget.addLayout(bottom_bar)

    def _handle_inject_btn_clicked(self):
        """Injects all currently checked DLCs into DlcData in SLSsteam config.yaml."""
        checked_dlcs = self.get_selected_dlc_data()
        if not checked_dlcs:
            ThemedMessageBox.warning(
                self,
                "No DLCs Selected",
                "Please select at least one DLC to inject into SLSsteam config.",
            )
            return

        try:
            from utils.yaml.dlc import add_dlc_data_batch
            from utils.yaml_config_manager import get_user_config_path, remove_additional_app

            config_path = get_user_config_path()
            if not config_path or not config_path.exists():
                raise RuntimeError("SLSsteam config.yaml could not be found.")

            add_dlc_data_batch(config_path, self.app_id, checked_dlcs)
            if getattr(self, "_dlc_only_mode", False):
                remove_additional_app(config_path, self.app_id)
                get_settings().setValue(f"dlc_only_mode/{self.app_id}", True)

            self._injection_only = True
            self._config_written = True
            ThemedMessageBox.information(
                self,
                "DLC Data Injected",
                f"✓ Successfully injected {len(checked_dlcs)} DLC(s) into DlcData for '{self.game_name}'.\n\n"
                f"SLSsteam will now explicitly report these DLCs as owned.",
            )
            self.accept()
        except Exception as exc:
            logger.warning(f"[DepotSelectionDialog] Could not inject DLC data: {exc}", exc_info=True)
            ThemedMessageBox.warning(
                self,
                "Injection Failed",
                f"Failed writing DLC data to SLSsteam config.yaml:\n\n{exc}",
            )

    def _apply_dynamic_theme(self, color: QColor) -> None:
        """Dynamically adapt ambient color and titlebar without touching table or buttons."""
        if not color or not isinstance(color, QColor) or not color.isValid():
            return
        self._ambient_color = color
        if hasattr(self, "title_bar") and self.title_bar:
            self.title_bar.update_accent_color(color)
        self.update()
