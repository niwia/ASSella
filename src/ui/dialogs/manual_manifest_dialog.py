"""
Manual Manifest Dialog
======================
Compact modal dialog allowing users to directly input a Depot ID, Manifest ID,
and optional Build ID to download a specific historical build without requiring
SteamDB or Byparr.
"""

import re
import logging
from typing import Dict, Optional, Tuple, Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QFrame,
    QMessageBox,
)

from utils.color_utils import get_best_foreground_color

logger = logging.getLogger("ACCELA.manual_manifest")


def _make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setStyleSheet("background-color: rgba(255, 255, 255, 0.08); max-height: 1px; border: none;")
    return sep


class ManualManifestDialog(QDialog):
    """Direct manual entry prompt for Depot ID and Manifest ID."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        app_id: str = "",
        game_name: str = "",
        depots_dict: Optional[Dict[str, Any]] = None,
        default_depot_id: str = "",
        current_build_id: str = "",
        accent_color: str = "#4c8df5",
    ):
        super().__init__(parent)
        self.app_id = str(app_id).strip()
        self.game_name = game_name or f"AppID {self.app_id}"
        self.depots_dict = dict(depots_dict or {})
        self.default_depot_id = str(default_depot_id or "").strip()
        self.current_build_id = str(current_build_id or "").strip()
        self.accent_color = accent_color

        self._selected_build_id = ""
        self._selected_patch_depots: Dict[str, Any] = {}

        self.setWindowTitle("Manual Manifest Override")
        self.setFixedWidth(440)
        self.setStyleSheet("""
            QDialog {
                background-color: #12131a;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }
        """)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        # Title & Subtitle
        header_layout = QVBoxLayout()
        header_layout.setSpacing(3)

        title_lbl = QLabel("Manual Build & Manifest Override")
        title_lbl.setStyleSheet("font-size: 11.5pt; font-weight: bold; color: #FFFFFF;")
        header_layout.addWidget(title_lbl)

        sub_text = f"{self.game_name} ({self.app_id})" if self.app_id else self.game_name
        subtitle_lbl = QLabel(sub_text)
        subtitle_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.6);")
        header_layout.addWidget(subtitle_lbl)

        layout.addLayout(header_layout)
        layout.addWidget(_make_separator())

        # Form Styles
        input_style = f"""
            QLineEdit {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 5px;
                color: #FFFFFF;
                padding: 5px 10px;
                font-size: 9pt;
                font-family: monospace;
            }}
            QLineEdit:focus {{
                border-color: {self.accent_color};
                background-color: rgba(255, 255, 255, 0.08);
            }}
        """
        combo_style = f"""
            QComboBox {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 5px;
                color: #FFFFFF;
                padding: 4px 8px;
                font-size: 9pt;
            }}
            QComboBox:hover {{
                border-color: rgba(255, 255, 255, 0.28);
            }}
            QComboBox QAbstractItemView {{
                background-color: #1a1c23;
                color: #FFFFFF;
                selection-background-color: {self.accent_color};
                border: 1px solid rgba(255, 255, 255, 0.15);
            }}
        """

        # 1. Target Depot Field
        depot_section = QVBoxLayout()
        depot_section.setSpacing(4)
        depot_lbl = QLabel("Target Depot:")
        depot_lbl.setStyleSheet("font-size: 8.5pt; font-weight: 600; color: rgba(255, 255, 255, 0.85);")
        depot_section.addWidget(depot_lbl)

        if self.depots_dict:
            self.depot_combo = QComboBox()
            self.depot_combo.setFixedHeight(30)
            self.depot_combo.setStyleSheet(combo_style)
            self.depot_combo.setEditable(True)

            sel_idx = 0
            for idx, (did, dinfo) in enumerate(self.depots_dict.items()):
                d_desc = ""
                if isinstance(dinfo, dict):
                    d_desc = dinfo.get("desc") or dinfo.get("name") or ""
                label = f"{did} — {d_desc}" if d_desc else str(did)
                self.depot_combo.addItem(label, str(did))
                if str(did) == self.default_depot_id:
                    sel_idx = idx

            self.depot_combo.setCurrentIndex(sel_idx)
            self.depot_input = None
            depot_section.addWidget(self.depot_combo)
        else:
            self.depot_input = QLineEdit()
            self.depot_input.setFixedHeight(30)
            self.depot_input.setStyleSheet(input_style)
            self.depot_input.setPlaceholderText("e.g. 2507001")
            self.depot_input.setText(self.default_depot_id or self.app_id)
            self.depot_combo = None
            depot_section.addWidget(self.depot_input)

        layout.addLayout(depot_section)

        # 2. Manifest ID Field (Required)
        mid_section = QVBoxLayout()
        mid_section.setSpacing(4)
        mid_lbl = QLabel("Target Manifest ID:")
        mid_lbl.setStyleSheet("font-size: 8.5pt; font-weight: 600; color: rgba(255, 255, 255, 0.85);")
        mid_section.addWidget(mid_lbl)

        self.manifest_input = QLineEdit()
        self.manifest_input.setFixedHeight(30)
        self.manifest_input.setStyleSheet(input_style)
        self.manifest_input.setPlaceholderText("e.g. 4622465672933170056")
        mid_section.addWidget(self.manifest_input)
        layout.addLayout(mid_section)

        # 3. Build ID Field (Optional)
        bid_section = QVBoxLayout()
        bid_section.setSpacing(4)
        bid_lbl = QLabel("Build ID / Version Label (Optional):")
        bid_lbl.setStyleSheet("font-size: 8.5pt; font-weight: 600; color: rgba(255, 255, 255, 0.85);")
        bid_section.addWidget(bid_lbl)

        self.build_input = QLineEdit()
        self.build_input.setFixedHeight(30)
        self.build_input.setStyleSheet(input_style)
        self.build_input.setPlaceholderText("e.g. 55251 or Custom")
        bid_section.addWidget(self.build_input)
        layout.addLayout(bid_section)

        layout.addSpacing(4)

        # Bottom Button Row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(32)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                color: #FFFFFF;
                font-weight: 600;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn, 1)

        apply_btn = QPushButton("Apply")
        apply_btn.setFixedHeight(32)
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        text_color = get_best_foreground_color(self.accent_color, dark_color="#111318", light_color="#FFFFFF")
        apply_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                border: none;
                border-radius: 5px;
                color: {text_color};
                font-weight: bold;
                font-size: 8.5pt;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
        """)
        apply_btn.clicked.connect(self._on_apply_clicked)
        btn_row.addWidget(apply_btn, 1)

        layout.addLayout(btn_row)

    def _on_apply_clicked(self):
        # Resolve depot ID
        depot_id = ""
        if self.depot_combo:
            data = self.depot_combo.currentData()
            if data:
                depot_id = str(data).strip()
            else:
                txt = self.depot_combo.currentText().strip()
                m = re.search(r"^\d+", txt)
                depot_id = m.group(0) if m else txt
        elif self.depot_input:
            depot_id = self.depot_input.text().strip()

        if not depot_id or not depot_id.isdigit():
            QMessageBox.warning(self, "Invalid Depot ID", "Please specify a valid numeric Depot ID.")
            return

        # Resolve manifest ID
        manifest_id = self.manifest_input.text().strip()
        if not manifest_id or not manifest_id.isdigit():
            QMessageBox.warning(self, "Invalid Manifest ID", "Please enter a valid numeric Steam Manifest ID.")
            return

        # Resolve build ID / tag
        build_id = self.build_input.text().strip()
        if not build_id:
            build_id = f"M-{manifest_id[-6:]}" if len(manifest_id) >= 6 else "Manual"

        self._selected_build_id = build_id
        self._selected_patch_depots = {
            str(depot_id): {"manifest_id": str(manifest_id)}
        }
        self.accept()

    def get_selected_build(self) -> Tuple[str, Dict[str, Any]]:
        """Return (selected_build_id, patch_depots_dict)."""
        return self._selected_build_id, self._selected_patch_depots
