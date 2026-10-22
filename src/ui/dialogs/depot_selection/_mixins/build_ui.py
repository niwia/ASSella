"""Static widget construction: the header (thumbnail, title, branch and
Build button), the depot table, the platform / DLC-only / Browse Files row
and the bottom OK / Cancel bar."""

import os

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

from ui.dialogs.depot_selection.views import (
    DepotCheckboxDelegate,
    DepotHeaderView,
)


class BuildUIMixin:
    """Static widget construction for the header, table, action row and bottom bar."""

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

        if dlc_mode:
            display_title = (
                f"<b>{self.game_name}</b> <span style='color: rgba(255, 255, 255, 0.55); font-size: 9.5pt;'>({self.app_id})</span>"
                if self.app_id and str(self.app_id) not in ("0", "unknown", "None", "") and f"({self.app_id})" not in str(self.game_name)
                else f"<b>{self.game_name}</b>"
            )
            self.title_label = QLabel(display_title)
            self.title_label.setStyleSheet("font-size: 11pt; color: #FFFFFF;")
        else:
            plain_title = (
                str(self.game_name)
                if not self.app_id
                or str(self.app_id) in ("0", "unknown", "None", "")
                or f"({self.app_id})" in str(self.game_name)
                else f"{self.game_name} ({self.app_id})"
            )
            self.title_label = QLabel(plain_title)
            self.title_label.setStyleSheet("font-size: 13pt; font-weight: bold; color: #FFFFFF;")
        title_layout.addWidget(self.title_label)

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
            self.branch_combo.setFixedHeight(26)
            self.branch_combo.setMinimumWidth(110)
            self.branch_combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: rgba(255, 255, 255, 0.06);
                    border: 1px solid rgba(255, 255, 255, 0.16);
                    border-radius: 4px;
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
            branch_lbl = QLabel(f"Branch: <b style='color: #FFFFFF;'>{self.branch or 'public'}</b>")
            branch_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.6);")
            badges_row.addWidget(branch_lbl)

        badges_row.addSpacing(4)

        display_bid = self.current_build_id or "Latest"
        self.builds_btn = QPushButton(f"Build: {display_bid}")
        self.builds_btn.setFixedHeight(24)
        self.builds_btn.setMinimumWidth(100)
        self.builds_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.builds_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_build_btn_style()
        self.builds_btn.clicked.connect(self._on_builds_clicked)
        badges_row.addWidget(self.builds_btn)

        if dlc_mode:
            self.summary_lbl = QLabel("Selected: 0 of 0 depots (0 B)")
            self.summary_lbl.setStyleSheet(f"font-size: 8.5pt; font-weight: 600; color: {self.accent_color};")
            badges_row.addWidget(self.summary_lbl)
        else:
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
            self._setup_storage_buttons(storage_row)
            title_layout.addLayout(storage_row)
        else:
            display_path = self.preferred_library_path or "Detected from Steam library"
            self.path_lbl = QLabel(f"Install Path: <span style='color: rgba(255, 255, 255, 0.8); font-family: monospace;'>{display_path}</span>")
            self.path_lbl.setToolTip(self.preferred_library_path or "Steam default directory")
            self.path_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8pt;")
            title_layout.addWidget(self.path_lbl)

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

        self.table_widget.setStyleSheet(f"""
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
                background-color: {self.accent_color};
                border: 1px solid {self.accent_color};
            }}
            QTableWidget::indicator:disabled {{
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.12);
            }}
            QTableWidget::indicator:checked:disabled {{
                background-color: rgba(255, 255, 255, 0.20) !important;
                border: 1px solid rgba(255, 255, 255, 0.20) !important;
            }}
        """)

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

            from utils.dlc_helpers import has_game_dlcs
            has_dlc = has_game_dlcs(str(self.app_id), self.depots)

            self._dlc_only_btn = QPushButton("DLC Only")
            self._dlc_only_btn.setFixedHeight(28)
            self._dlc_only_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._dlc_only_btn.setCheckable(True)

            if getattr(self, "lock_dlc_only", False):
                self._dlc_only_btn.setChecked(True)
                self._dlc_only_btn.setEnabled(False)
                self._dlc_only_btn.setCursor(Qt.CursorShape.ForbiddenCursor)
                self._dlc_only_btn.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(255, 255, 255, 0.04) !important;
                        color: rgba(255, 255, 255, 0.45) !important;
                        border: 1px solid rgba(255, 255, 255, 0.08) !important;
                        border-radius: 6px;
                        padding: 4px 12px;
                        font-size: 8.5pt;
                        font-weight: 500;
                    }
                """)
                self._dlc_only_btn.setToolTip("DLC-Only mode is locked for this installation.")
                self._dlc_only_mode = True
            elif not has_dlc:
                self._dlc_only_btn.setEnabled(False)
                self._dlc_only_btn.setChecked(False)
                self._dlc_only_btn.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(255, 255, 255, 0.03) !important;
                        color: rgba(255, 255, 255, 0.25) !important;
                        border: 1px solid rgba(255, 255, 255, 0.06) !important;
                        border-radius: 6px;
                        padding: 4px 12px;
                        font-size: 8.5pt;
                    }
                """)
                self._dlc_only_btn.setToolTip("DLC-Only mode is unavailable because this title has no DLC.")
                self._dlc_only_mode = False
            else:
                self._dlc_only_btn.setChecked(self._dlc_only_mode)
                self._refresh_dlc_only_style()
                self._dlc_only_btn.toggled.connect(self._on_dlc_only_toggled)

            button_layout.addWidget(self._dlc_only_btn, 1)

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

        # Status / Selection summary on the left
        self.status_summary_label = QLabel()
        self.status_summary_label.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.6);")
        self.status_summary_label.setVisible(dlc_mode)
        bottom_bar.addWidget(self.status_summary_label)

        bottom_bar.addStretch(1)

        # [ Inject DLC Data Only ]  [ Move to AT0-M ]  [ Close ]  [ Download Selected ]
        btn_h = 32
        btn_min_w = 140

        self.inject_btn = QPushButton("Inject DLC Data Only")
        self.inject_btn.setFixedHeight(btn_h)
        self.inject_btn.setMinimumWidth(btn_min_w)
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
        self.move_to_atom_btn.setMinimumWidth(btn_min_w)
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
        self.move_to_atom_btn.setToolTip("Register selected depots and DLC entitlements to SLSsteam AT0-M (Steam Native) mode")
        if hasattr(self, "_handle_move_to_atom_clicked"):
            self.move_to_atom_btn.clicked.connect(self._handle_move_to_atom_clicked)
        self.move_to_atom_btn.setVisible(dlc_mode or bool(getattr(self, "_is_atom_mode", False)))

        self.cancel_btn = QPushButton("Close")
        self.cancel_btn.setObjectName("cancel_button")
        self.cancel_btn.setFixedHeight(btn_h)
        self.cancel_btn.setMinimumWidth(btn_min_w)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                color: rgba(255, 255, 255, 0.8);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                padding: 0 14px;
                font-size: 8.5pt;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                color: #FFFFFF;
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)

        from utils.color_utils import get_best_foreground_color
        text_hex = get_best_foreground_color(self.accent_color, dark_color="#111318", light_color="#FFFFFF")

        self.download_btn = QPushButton("Download Selected")
        self.download_btn.setObjectName("ok_button")
        self.download_btn.setFixedHeight(btn_h)
        self.download_btn.setMinimumWidth(btn_min_w)
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
        bottom_bar.addWidget(self.cancel_btn)
        bottom_bar.addWidget(self.download_btn)
        content_widget.addLayout(bottom_bar)
