"""
dlc_selection_checklist_dialog.py
=================================
High-DLC prompt and manual checklist selection dialogs for Steam games in DLC-only mode.
Handles:
- Games with >= 64 depot-less DLCs (prompting Add to DlcData vs Manual Selection).
- Searchable checklist allowing users to cherry-pick DLC entitlements.
- Auto-threshold detection (routing < 64 DLCs to AdditionalApps and >= 64 to DlcData).
"""

import logging
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QFrame,
    QWidget,
)

from ui.dialog_titlebar import DialogTitleBar
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class HighDlcPromptDialog(QDialog):
    """
    Prompt shown when an owned Steam game has >= 64 depot-less DLCs.
    Offers two clear choices:
      1. 'Add All to DlcData' (writes all DLCs to DlcData, bypassing Steam's 64 DLC limit)
      2. 'Manual Selection' (opens checklist modal to pick which DLCs to unlock)
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        app_id: str = "",
        game_name: str = "",
        dlc_count: int = 0,
        accent_color: str = "#6c5ce7",
    ):
        super().__init__(parent)
        self.app_id = str(app_id)
        self.game_name = game_name or f"App {app_id}"
        self.dlc_count = dlc_count
        self.accent_color = accent_color
        self._choice = "cancel"

        self.setWindowTitle("High DLC Count Detected")
        self.setFixedWidth(500)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        container = QFrame(self)
        container.setObjectName("promptContainer")
        container.setStyleSheet("""
            QFrame#promptContainer {
                background-color: #1a1c23;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }
            QLabel {
                color: #FFFFFF;
                border: none;
                background: transparent;
            }
        """)
        root.addWidget(container)

        c_lay = QVBoxLayout(container)
        c_lay.setContentsMargins(0, 0, 0, 16)
        c_lay.setSpacing(14)

        # Title bar
        self.title_bar = DialogTitleBar(
            self,
            title="High DLC Count Detected",
            can_minimize=False,
            can_maximize=False,
            use_power_close=True,
        )
        c_lay.addWidget(self.title_bar)

        # Content area
        body = QVBoxLayout()
        body.setContentsMargins(20, 6, 20, 6)
        body.setSpacing(14)
        c_lay.addLayout(body)

        title_lbl = QLabel(
            f"<b>{self.game_name}</b> has <span style='color: #EAB308; font-weight: bold;'>{self.dlc_count}</span> depot-less DLCs."
        )
        title_lbl.setStyleSheet("font-size: 11pt; color: #FFFFFF;")
        title_lbl.setWordWrap(True)
        body.addWidget(title_lbl)

        desc_lbl = QLabel(
            "Steam has a client limit that causes issues when more than 63 DLCs are added to "
            "<b>AdditionalApps</b>.<br><br>"
            "To bypass this limitation, you can write all DLCs to <b>DlcData</b> in SLSsteam config, "
            "or manually select which DLCs to unlock from a checklist."
        )
        desc_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.75); line-height: 140%;")
        desc_lbl.setWordWrap(True)
        body.addWidget(desc_lbl)

        # Action button cards
        btn_box = QVBoxLayout()
        btn_box.setSpacing(10)

        # 1. Add to DlcData Button (Recommended)
        self.dlc_data_btn = QPushButton()
        self.dlc_data_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dlc_data_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(108, 92, 231, 0.15);
                border: 1px solid {self.accent_color};
                border-radius: 8px;
                padding: 12px 14px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: rgba(108, 92, 231, 0.30);
                border-color: #A29BFE;
            }}
        """)
        d_lay = QVBoxLayout(self.dlc_data_btn)
        d_lay.setContentsMargins(6, 4, 6, 4)
        d_lay.setSpacing(2)
        d_title = QLabel(f"⚡ Add All to DlcData ({self.dlc_count} DLCs)")
        d_title.setStyleSheet("font-weight: bold; font-size: 9.5pt; color: #FFFFFF;")
        d_sub = QLabel("Recommended: Bypasses Steam's 64 DLC limit by writing to DlcData")
        d_sub.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.70);")
        d_lay.addWidget(d_title)
        d_lay.addWidget(d_sub)
        self.dlc_data_btn.clicked.connect(self._on_dlc_data_chosen)
        btn_box.addWidget(self.dlc_data_btn)

        # 2. Manual Selection Button
        self.manual_btn = QPushButton()
        self.manual_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.manual_btn.setStyleSheet("""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                padding: 12px 14px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.10);
                border-color: rgba(255, 255, 255, 0.30);
            }}
        """)
        m_lay = QVBoxLayout(self.manual_btn)
        m_lay.setContentsMargins(6, 4, 6, 4)
        m_lay.setSpacing(2)
        m_title = QLabel("📝 Manual Selection...")
        m_title.setStyleSheet("font-weight: bold; font-size: 9.5pt; color: #FFFFFF;")
        m_sub = QLabel("Select individual DLCs to unlock from a checklist")
        m_sub.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.70);")
        m_lay.addWidget(m_title)
        m_lay.addWidget(m_sub)
        self.manual_btn.clicked.connect(self._on_manual_chosen)
        btn_box.addWidget(self.manual_btn)

        body.addLayout(btn_box)

        # Cancel button
        cancel_row = QHBoxLayout()
        cancel_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid rgba(255, 255, 255, 0.20);
                border-radius: 5px;
                color: rgba(255, 255, 255, 0.80);
                padding: 6px 18px;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.10);
                color: #FFFFFF;
            }
        """)
        cancel_btn.clicked.connect(self._on_cancel)
        cancel_row.addWidget(cancel_btn)
        body.addLayout(cancel_row)

    def _on_dlc_data_chosen(self):
        self._choice = "dlc_data"
        self.accept()

    def _on_manual_chosen(self):
        self._choice = "manual"
        self.accept()

    def _on_cancel(self):
        self._choice = "cancel"
        self.reject()

    def get_choice(self) -> str:
        return self._choice


class DlcSelectionChecklistDialog(QDialog):
    """
    Checklist dialog for manually cherry-picking DLCs.
    Features:
    - Live search filter (by AppID or name)
    - Select All / Deselect All
    - Threshold status badge (< 64 -> AdditionalApps, >= 64 -> DlcData)
    - Fully dark themed with translucent container and custom titlebar
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        app_id: str = "",
        game_name: str = "",
        catalog: Optional[Dict[str, str]] = None,
        accent_color: str = "#6c5ce7",
    ):
        super().__init__(parent)
        self.app_id = str(app_id)
        self.game_name = game_name or f"App {app_id}"
        self.catalog = dict(catalog or {})
        self.accent_color = accent_color
        self._items_data: List[Dict[str, str]] = []

        self.setWindowTitle(f"Select DLCs — {self.game_name}")
        self.setMinimumSize(560, 520)
        self.resize(600, 580)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._init_ui()
        self._populate_list()
        self._update_counter_and_badge()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        container = QFrame(self)
        container.setObjectName("checklistContainer")
        container.setStyleSheet("""
            QFrame#checklistContainer {
                background-color: #1a1c23;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }
            QLabel {
                color: #FFFFFF;
                border: none;
                background: transparent;
            }
            QLineEdit {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 6px;
                color: #FFFFFF;
                padding: 6px 10px;
                font-size: 9pt;
            }
            QLineEdit:focus {
                border: 1px solid #6c5ce7;
                background-color: rgba(255, 255, 255, 0.09);
            }
            QListWidget {
                background-color: rgba(18, 19, 24, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                color: #FFFFFF;
                outline: none;
                padding: 4px;
            }
            QListWidget::item {
                padding: 4px 6px;
                border-radius: 4px;
            }
            QListWidget::item:hover {
                background-color: rgba(255, 255, 255, 0.06);
            }
            QListWidget::item:selected {
                background-color: rgba(108, 92, 231, 0.25);
            }
        """)
        root.addWidget(container)

        c_lay = QVBoxLayout(container)
        c_lay.setContentsMargins(0, 0, 0, 14)
        c_lay.setSpacing(10)

        # Title bar
        self.title_bar = DialogTitleBar(
            self,
            title=f"Select DLCs — {self.game_name}",
            can_minimize=False,
            can_maximize=True,
            use_power_close=True,
        )
        c_lay.addWidget(self.title_bar)

        body = QVBoxLayout()
        body.setContentsMargins(18, 4, 18, 4)
        body.setSpacing(10)
        c_lay.addLayout(body, stretch=1)

        # Subtitle
        sub_lbl = QLabel(f"Choose DLC entitlements to unlock for <b>{self.game_name}</b> ({self.app_id}):")
        sub_lbl.setStyleSheet("font-size: 9pt; color: rgba(255, 255, 255, 0.85);")
        body.addWidget(sub_lbl)

        # Search bar
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  Search DLCs by name or AppID...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        body.addWidget(self.search_input)

        # Controls & Badge row
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)

        self.sel_all_btn = QPushButton("Select All")
        self.sel_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sel_all_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 4px;
                color: #FFFFFF;
                font-size: 8pt;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.12);
            }
        """)
        self.sel_all_btn.clicked.connect(lambda: self._set_all_visible_checked(True))
        ctrl_row.addWidget(self.sel_all_btn)

        self.desel_all_btn = QPushButton("Deselect All")
        self.desel_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.desel_all_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 4px;
                color: #FFFFFF;
                font-size: 8pt;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.12);
            }
        """)
        self.desel_all_btn.clicked.connect(lambda: self._set_all_visible_checked(False))
        ctrl_row.addWidget(self.desel_all_btn)

        ctrl_row.addStretch(1)

        self.count_lbl = QLabel("Selected: 0 / 0 DLCs")
        self.count_lbl.setStyleSheet("font-size: 8.5pt; color: #FFFFFF; font-weight: bold;")
        ctrl_row.addWidget(self.count_lbl)

        body.addLayout(ctrl_row)

        # Mode Badge Indicator
        self.badge_lbl = QLabel("Standard Mode")
        self.badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge_lbl.setStyleSheet("""
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 8pt;
            font-weight: bold;
            background: rgba(16, 185, 129, 0.18);
            border: 1px solid #10B981;
            color: #10B981;
        """)
        body.addWidget(self.badge_lbl)

        # List Widget
        self.list_widget = QListWidget()
        self.list_widget.setSpacing(2)
        self.list_widget.itemChanged.connect(self._on_item_state_changed)
        body.addWidget(self.list_widget, stretch=1)

        # Bottom Button Bar
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch(1)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 5px;
                color: rgba(255, 255, 255, 0.85);
                padding: 7px 18px;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        self.apply_btn = QPushButton("Apply Selection")
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                border: none;
                border-radius: 5px;
                color: #FFFFFF;
                font-weight: bold;
                font-size: 8.5pt;
                padding: 7px 22px;
            }}
            QPushButton:hover {{
                background-color: #FFFFFF;
                color: #111318;
            }}
        """)
        self.apply_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.apply_btn)

        body.addLayout(btn_row)

    def _populate_list(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        # Sort DLCs by AppID numerically if possible, else alphabetically
        def _sort_key(item):
            k, _ = item
            return int(k) if str(k).isdigit() else 999999999

        sorted_dlcs = sorted(self.catalog.items(), key=_sort_key)

        for dlc_id, dlc_name in sorted_dlcs:
            did_str = str(dlc_id).strip()
            if did_str == str(self.app_id).strip():
                continue
            name_str = str(dlc_name or f"DLC {did_str}").strip()

            item = QListWidgetItem()
            item.setText(f"[{did_str}]  {name_str}")
            item.setData(Qt.ItemDataRole.UserRole, did_str)
            item.setData(Qt.ItemDataRole.UserRole + 1, name_str)
            item.setCheckState(Qt.CheckState.Checked)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setSizeHint(QSize(0, 26))

            self.list_widget.addItem(item)

        self.list_widget.blockSignals(False)

    def _on_search_text_changed(self, text: str):
        query = (text or "").strip().lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not query:
                item.setHidden(False)
            else:
                txt = item.text().lower()
                did = str(item.data(Qt.ItemDataRole.UserRole) or "").lower()
                item.setHidden(query not in txt and query not in did)

    def _set_all_visible_checked(self, check: bool):
        st = Qt.CheckState.Checked if check else Qt.CheckState.Unchecked
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item.isHidden():
                item.setCheckState(st)
        self.list_widget.blockSignals(False)
        self._update_counter_and_badge()

    def _on_item_state_changed(self, _item: QListWidgetItem):
        self._update_counter_and_badge()

    def _update_counter_and_badge(self):
        total = self.list_widget.count()
        checked_count = 0
        for i in range(total):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                checked_count += 1

        self.count_lbl.setText(f"Selected: {checked_count} / {total} DLCs")

        if checked_count == 0:
            self.badge_lbl.setText("No DLCs Selected")
            self.badge_lbl.setStyleSheet("""
                padding: 4px 10px;
                border-radius: 4px;
                font-size: 8pt;
                font-weight: bold;
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.20);
                color: rgba(255, 255, 255, 0.50);
            """)
        elif checked_count <= 63:
            self.badge_lbl.setText(f"Standard Mode ({checked_count} DLCs) → AdditionalApps in config.yaml")
            self.badge_lbl.setStyleSheet("""
                padding: 4px 10px;
                border-radius: 4px;
                font-size: 8pt;
                font-weight: bold;
                background: rgba(16, 185, 129, 0.16);
                border: 1px solid #10B981;
                color: #10B981;
            """)
        else:
            self.badge_lbl.setText(f"High-DLC Mode ({checked_count} DLCs) → DlcData in config.yaml (Bypasses Limit)")
            self.badge_lbl.setStyleSheet("""
                padding: 4px 10px;
                border-radius: 4px;
                font-size: 8pt;
                font-weight: bold;
                background: rgba(234, 179, 8, 0.16);
                border: 1px solid #EAB308;
                color: #EAB308;
            """)

    def get_selected_dlcs(self) -> Dict[str, str]:
        """Returns {dlc_appid: dlc_name} for all checked DLCs."""
        res: Dict[str, str] = {}
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                did = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
                name = str(item.data(Qt.ItemDataRole.UserRole + 1) or "").strip()
                if did:
                    res[did] = name
        return res
