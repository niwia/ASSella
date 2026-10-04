"""
BranchSelectionDialog — A comfortable, modern dialog for selecting game branches.
Replaces the cramped QInputDialog with proper dimensions, large typography, and branch details.
"""

import datetime
from typing import Dict, Any, Optional, Tuple
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QFrame,
)

from utils.settings import get_settings
from utils.color_utils import get_best_foreground_color


class BranchSelectionDialog(QDialog):
    """Modern modal dialog allowing users to pick a branch for an AppID."""

    def __init__(self, parent=None, app_id: str = "", branches: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.app_id = str(app_id)
        self.branches = dict(branches or {})
        self.selected_branch = "public"

        settings = get_settings()
        accent = settings.value("accent_color", "#C06C84", type=str) or "#C06C84"
        fg_color = get_best_foreground_color(accent, dark_color="#121214", light_color="#FFFFFF")

        self.setWindowTitle(f"Select Branch — App {self.app_id}")
        self.setMinimumWidth(540)
        self.setMinimumHeight(280)
        self.resize(560, 300)
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1a1c;
                color: #FFFFFF;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)

        # ── Title & Description ───────────────────────────────────────────
        title_lbl = QLabel("Branch Selection")
        title_lbl.setStyleSheet("font-size: 13pt; font-weight: bold; color: #FFFFFF;")
        layout.addWidget(title_lbl)

        desc_lbl = QLabel(
            f"Steam reports multiple branches for AppID <b style='color: #FFFFFF;'>{self.app_id}</b>.<br>"
            "Choose which branch manifest you want to download:"
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("font-size: 10pt; color: rgba(255, 255, 255, 0.7); line-height: 1.4;")
        layout.addWidget(desc_lbl)

        # ── Branch ComboBox ──────────────────────────────────────────────
        self.branch_combo = QComboBox()
        self.branch_combo.setFixedHeight(42)
        self.branch_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1.5px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                padding: 6px 14px;
                color: #FFFFFF;
                font-size: 10.5pt;
                font-weight: 500;
            }}
            QComboBox:hover {{
                border-color: {accent};
                background-color: rgba(255, 255, 255, 0.09);
            }}
            QComboBox:focus {{
                border-color: {accent};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 32px;
                border-left: none;
            }}
            QComboBox QAbstractItemView {{
                background-color: #242428;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #FFFFFF;
                selection-background-color: {accent};
                selection-color: {fg_color};
                padding: 6px;
                font-size: 10pt;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 34px;
                padding: 4px 10px;
                border-radius: 4px;
            }}
        """)

        # Sort branches: public first, then alphabetical
        self.branch_keys = sorted(self.branches.keys(), key=lambda k: (0 if k == "public" else 1, k.lower()))
        if "public" not in self.branch_keys and not self.branch_keys:
            self.branch_keys = ["public"]

        saved_branch = settings.value(f"selected_branch/{self.app_id}", "public", type=str)
        default_idx = 0

        for idx, b_name in enumerate(self.branch_keys):
            b_info = self.branches.get(b_name, {})
            bid = str(b_info.get("buildid", "") if isinstance(b_info, dict) else "").strip()
            desc = str(b_info.get("description", "") if isinstance(b_info, dict) else "").strip()

            label_parts = [b_name]
            if bid:
                label_parts.append(f"Build {bid}")
            if desc:
                label_parts.append(f"({desc})")

            display_str = "   •   ".join(label_parts) if len(label_parts) > 1 else b_name
            self.branch_combo.addItem(display_str, b_name)

            if b_name == saved_branch:
                default_idx = idx

        self.branch_combo.setCurrentIndex(default_idx)
        layout.addWidget(self.branch_combo)

        # ── Info Details Card ─────────────────────────────────────────────
        self.info_card = QFrame()
        self.info_card.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 6px;
                padding: 8px 12px;
            }
        """)
        info_lay = QVBoxLayout(self.info_card)
        info_lay.setContentsMargins(6, 4, 6, 4)
        info_lay.setSpacing(4)

        self.info_lbl = QLabel("")
        self.info_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 9pt;")
        info_lay.addWidget(self.info_lbl)
        layout.addWidget(self.info_card)

        self.branch_combo.currentIndexChanged.connect(self._update_info)
        self._update_info()

        layout.addStretch()

        # ── Action Buttons ────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(38)
        cancel_btn.setFixedWidth(110)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                font-size: 9.5pt;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.15);
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        select_btn = QPushButton("Select Branch")
        select_btn.setFixedHeight(38)
        select_btn.setFixedWidth(140)
        select_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: {fg_color};
                border: none;
                border-radius: 6px;
                font-size: 9.5pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #FFFFFF;
                color: #000000;
            }}
        """)
        select_btn.clicked.connect(self._on_select)
        select_btn.setDefault(True)
        btn_layout.addWidget(select_btn)

        layout.addLayout(btn_layout)

    def _update_info(self):
        idx = self.branch_combo.currentIndex()
        if idx < 0 or idx >= len(self.branch_keys):
            self.info_card.setVisible(False)
            return

        b_name = self.branch_keys[idx]
        b_info = self.branches.get(b_name, {})
        if not isinstance(b_info, dict):
            b_info = {}

        bid = str(b_info.get("buildid") or "N/A")
        time_up = b_info.get("timeupdated")
        time_str = ""
        if time_up:
            try:
                dt = datetime.datetime.fromtimestamp(int(time_up))
                time_str = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                time_str = str(time_up)

        details = [f"Branch: <b style='color: #FFFFFF;'>{b_name}</b>", f"Build ID: <b style='color: #FFFFFF;'>{bid}</b>"]
        if time_str:
            details.append(f"Updated: {time_str}")
        if b_info.get("pwdrequired") in (1, "1", True):
            details.append("<span style='color: #ffaa00;'>Password Protected</span>")

        self.info_lbl.setText("  •  ".join(details))
        self.info_card.setVisible(True)

    def _on_select(self):
        idx = self.branch_combo.currentIndex()
        if 0 <= idx < len(self.branch_keys):
            self.selected_branch = self.branch_keys[idx]
        else:
            self.selected_branch = "public"
        self.accept()

    @classmethod
    def select_branch(cls, parent=None, app_id: str = "", branches: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], bool]:
        """Convenience method to show dialog and return (selected_branch, ok)."""
        dlg = cls(parent=parent, app_id=app_id, branches=branches)
        ok = (dlg.exec() == QDialog.DialogCode.Accepted)
        return (dlg.selected_branch if ok else None, ok)
