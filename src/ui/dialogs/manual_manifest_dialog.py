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

from PyQt6.QtCore import Qt, QUrl, QSize
from PyQt6.QtGui import QIcon, QDesktopServices
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
from utils.paths import Paths
from utils.settings import get_settings

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

        self._settings = get_settings()
        self._remembered_build_id = ""
        if self._settings and self.app_id:
            try:
                self._remembered_build_id = str(self._settings.value(f"manual_manifest/{self.app_id}/last_build_id", "") or "").strip()
            except Exception:
                self._remembered_build_id = ""

        self._selected_build_id = ""
        self._selected_patch_depots: Dict[str, Any] = {}
        self._last_loaded_depot = ""

        self.setWindowTitle("Manual Manifest Override")
        self.setFixedWidth(460)
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

        # Title & Subtitle with SteamDB Patchnotes Button
        header_layout = QVBoxLayout()
        header_layout.setSpacing(3)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        title_lbl = QLabel("Manual Build & Manifest Override")
        title_lbl.setStyleSheet("font-size: 11pt; font-weight: bold; color: #FFFFFF;")
        title_row.addWidget(title_lbl)

        title_row.addStretch(1)

        steamdb_btn = QPushButton()
        steamdb_btn.setFixedHeight(24)
        steamdb_btn.setMinimumWidth(84)
        steamdb_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        steamdb_btn.setToolTip("Open SteamDB Patch Notes in Browser")
        icon_path = Paths.icon("steamdb-lockup.svg")
        if not icon_path.exists():
            icon_path = Paths.resource("steamdb-lockup.svg")
        if icon_path.exists():
            steamdb_btn.setIcon(QIcon(str(icon_path)))
            steamdb_btn.setIconSize(QSize(78, 16))
        else:
            steamdb_btn.setText("SteamDB")

        steamdb_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.07);
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 4px;
                padding: 2px 6px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.15);
                border-color: rgba(255, 255, 255, 0.35);
            }
        """)
        steamdb_btn.clicked.connect(self._open_steamdb_patchnotes)
        title_row.addWidget(steamdb_btn)

        header_layout.addLayout(title_row)

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
                # Strip repetitive "Depot {did}" or "[WINDOWS] Depot {did}"
                cleaned_desc = re.sub(rf"^(?:\[.*?\]\s*)?Depot\s+{did}\b", "", str(d_desc), flags=re.IGNORECASE).strip()
                cleaned_desc = re.sub(r"^[-—:\s]+", "", cleaned_desc).strip()
                label = f"{did} — {cleaned_desc}" if cleaned_desc else str(did)
                self.depot_combo.addItem(label, str(did))
                if str(did) == self.default_depot_id:
                    sel_idx = idx

            self.depot_combo.setCurrentIndex(sel_idx)
            self.depot_combo.currentIndexChanged.connect(self._on_depot_selection_changed)
            self.depot_input = None
            depot_section.addWidget(self.depot_combo)
        else:
            self.depot_input = QLineEdit()
            self.depot_input.setFixedHeight(30)
            self.depot_input.setStyleSheet(input_style)
            self.depot_input.setPlaceholderText("e.g. 2507001")
            self.depot_input.setText(self.default_depot_id or self.app_id)
            self.depot_input.textChanged.connect(self._on_depot_text_changed)
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

        # Initialize values from settings or props
        init_did = self._get_current_depot_id()
        self._last_loaded_depot = init_did
        if self._settings and self.app_id and init_did:
            try:
                saved_mid = str(self._settings.value(f"manual_manifest/{self.app_id}/{init_did}/manifest_id", "") or "").strip()
                if saved_mid:
                    self.manifest_input.setText(saved_mid)
            except Exception:
                pass

        init_bid = self.current_build_id or self._remembered_build_id
        if init_bid:
            self.build_input.setText(init_bid)

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

    def _open_steamdb_patchnotes(self):
        url = f"https://steamdb.info/app/{self.app_id}/patchnotes/" if self.app_id else "https://steamdb.info/"
        QDesktopServices.openUrl(QUrl(url))

    def _get_current_depot_id(self) -> str:
        if self.depot_combo:
            txt = self.depot_combo.currentText().strip()
            m = re.match(r"^(\d+)", txt)
            if m:
                return m.group(1)
            data = self.depot_combo.currentData()
            if data and str(data).strip().isdigit():
                return str(data).strip()
            m_any = re.search(r"\b\d+\b", txt)
            return m_any.group(0) if m_any else ""
        elif self.depot_input:
            txt = self.depot_input.text().strip()
            m = re.match(r"^(\d+)", txt)
            return m.group(1) if m else txt
        return ""

    def _on_depot_selection_changed(self, index: int):
        self._load_saved_manifest_for_depot()

    def _on_depot_text_changed(self, text: str):
        self._load_saved_manifest_for_depot()

    def _load_saved_manifest_for_depot(self):
        did = self._get_current_depot_id()
        if not did or did == self._last_loaded_depot:
            return
        self._last_loaded_depot = did
        if not self._settings or not self.app_id:
            return
        try:
            saved_mid = str(self._settings.value(f"manual_manifest/{self.app_id}/{did}/manifest_id", "") or "").strip()
            if saved_mid:
                self.manifest_input.setText(saved_mid)
            else:
                self.manifest_input.clear()
        except Exception:
            pass

    def _on_apply_clicked(self):
        # Resolve depot ID (strictly pure digits)
        depot_id = self._get_current_depot_id()
        if not depot_id or not depot_id.isdigit():
            QMessageBox.warning(self, "Invalid Depot ID", "Please specify a valid numeric Depot ID.")
            return

        # Resolve manifest ID
        manifest_id = self.manifest_input.text().strip()
        if not manifest_id or not manifest_id.isdigit():
            QMessageBox.warning(self, "Invalid Manifest ID", "Please enter a valid numeric Steam Manifest ID.")
            return

        # Verification safeguard: Steam Manifest IDs are 64-bit uints (~17-19 digits)
        if len(manifest_id) < 15 or len(manifest_id) > 20:
            res = QMessageBox.question(
                self,
                "Unusual Manifest ID Length",
                f"The entered Manifest ID '{manifest_id}' is {len(manifest_id)} digits long.\n"
                "Steam Manifest IDs are typically 17 to 19 digits.\n\nDo you want to proceed anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if res != QMessageBox.StandardButton.Yes:
                return

        # Resolve build ID / tag
        build_id = self.build_input.text().strip()
        if not build_id:
            build_id = f"M-{manifest_id[-6:]}" if len(manifest_id) >= 6 else "Manual"

        # Persist values in QSettings
        if self._settings and self.app_id:
            try:
                self._settings.setValue(f"manual_manifest/{self.app_id}/{depot_id}/manifest_id", str(manifest_id))
                if build_id:
                    self._settings.setValue(f"manual_manifest/{self.app_id}/last_build_id", str(build_id))
            except Exception as e:
                logger.debug(f"[ManualManifest] Failed to persist settings: {e}")

        self._selected_build_id = build_id
        self._selected_patch_depots = {
            str(depot_id): {"manifest_id": str(manifest_id)}
        }
        self.accept()

    def get_selected_build(self) -> Tuple[str, Dict[str, Any]]:
        """Return (selected_build_id, patch_depots_dict)."""
        return self._selected_build_id, self._selected_patch_depots
