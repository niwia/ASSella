"""
Rollback Dialogs for ACCELA.

Contains:
1. ManualRollbackDialog: Clean modal popup to input/select Depot ID, Build ID, and Manifest ID.
2. SteamDBHistoryDialog: Interactive SteamDB version browser fetching past builds and automatically
   resolving Depot and Manifest IDs for 1-click game rollbacks.
"""

import logging
import threading
from typing import Dict, List, Optional, Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QCheckBox,
    QMessageBox,
    QWidget,
    QFrame,
    QAbstractItemView,
)

from utils.color_utils import get_dark_container_color
from core.steamdb_scraper import SteamDBScraper

logger = logging.getLogger(__name__)


def _make_thin_separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setStyleSheet("background-color: rgba(255, 255, 255, 0.08); max-height: 1px; border: none;")
    return line


class ManualRollbackDialog(QDialog):
    """Clean modal dialog for entering manual rollback parameters (Depot, Build ID, Manifest ID)."""

    depots_loaded = pyqtSignal(dict)

    def __init__(self, parent=None, appid: str = "", game_name: str = "", depots_dict: Optional[Dict[str, Any]] = None, accent_color: str = "#a1c9fd"):
        super().__init__(parent)
        self.appid = str(appid)
        self.game_name = game_name
        self.depots_dict = depots_dict or {}
        self.accent_color = accent_color

        self.selected_depot_id = ""
        self.selected_build_id = ""
        self.selected_manifest_id = ""
        self.should_pin_build = True

        self.setWindowTitle(f"Manual Rollback — {game_name or f'App {self.appid}'}")
        self.setFixedSize(480, 360)
        self.setModal(True)
        self._init_ui()

        self.depots_loaded.connect(self._on_async_depots_loaded)
        if not self.depots_dict:
            self._fetch_depots_async()

    def _fetch_depots_async(self):
        def _worker():
            try:
                from core.steam_api import get_depot_info_from_api
                info = get_depot_info_from_api(self.appid)
                if info and info.get("depots"):
                    self.depots_loaded.emit(info["depots"])
            except Exception:
                pass
        threading.Thread(target=_worker, daemon=True).start()

    def _on_async_depots_loaded(self, depots: dict):
        if not depots:
            return
        self.depots_dict = depots
        self.depot_combo.clear()
        for d_id, d_info in depots.items():
            label = f"Depot {d_id}"
            if isinstance(d_info, dict) and d_info.get("name"):
                label += f" — {d_info['name']}"
            self.depot_combo.addItem(label, str(d_id))

    def _init_ui(self):
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #16171b;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 12px;
            }}
            QLabel {{
                color: #E2E2E6;
                background: transparent;
                font-size: 9pt;
            }}
            QLineEdit {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #FFFFFF;
                padding: 6px 10px;
                font-size: 9.5pt;
                font-family: monospace;
            }}
            QLineEdit:focus {{
                border: 1px solid {self.accent_color};
                background-color: rgba(255, 255, 255, 0.08);
            }}
            QComboBox {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #FFFFFF;
                padding: 6px 10px;
                font-size: 9.5pt;
            }}
            QComboBox QAbstractItemView {{
                background-color: #1b1b1f;
                border: 1px solid rgba(255, 255, 255, 0.12);
                selection-background-color: {self.accent_color};
                selection-color: #000000;
                padding: 4px;
            }}
            QCheckBox {{
                color: rgba(255, 255, 255, 0.85);
                font-size: 9pt;
                background: transparent;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # Title
        title_lbl = QLabel("Manual Version Rollback")
        title_lbl.setStyleSheet(f"font-size: 13pt; font-weight: bold; color: {self.accent_color};")
        layout.addWidget(title_lbl)

        desc_lbl = QLabel(f"Download a specific previous build for <b>{self.game_name}</b> (AppID: {self.appid}) using official Steam depot manifests.")
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
        layout.addWidget(desc_lbl)

        layout.addWidget(_make_thin_separator())

        # Depot selector
        depot_row = QVBoxLayout()
        depot_row.setSpacing(4)
        depot_lbl = QLabel("Target Depot:")
        depot_lbl.setStyleSheet("font-weight: bold; color: rgba(255, 255, 255, 0.9);")
        depot_row.addWidget(depot_lbl)

        self.depot_combo = QComboBox()
        self.depot_combo.setFixedHeight(32)
        self.depot_combo.setEditable(True)
        if self.depots_dict:
            for d_id, d_info in self.depots_dict.items():
                label = f"Depot {d_id}"
                if isinstance(d_info, dict) and d_info.get("name"):
                    label += f" — {d_info['name']}"
                self.depot_combo.addItem(label, str(d_id))
        else:
            self.depot_combo.addItem(f"Depot {self.appid}", str(self.appid))
        depot_row.addWidget(self.depot_combo)
        layout.addLayout(depot_row)

        # Build ID input
        bid_row = QVBoxLayout()
        bid_row.setSpacing(4)
        bid_lbl = QLabel("Target Build ID:")
        bid_lbl.setStyleSheet("font-weight: bold; color: rgba(255, 255, 255, 0.9);")
        bid_row.addWidget(bid_lbl)
        self.build_input = QLineEdit()
        self.build_input.setPlaceholderText("e.g. 23935869")
        self.build_input.setFixedHeight(32)
        bid_row.addWidget(self.build_input)
        layout.addLayout(bid_row)

        # Manifest ID input
        mid_row = QVBoxLayout()
        mid_row.setSpacing(4)
        mid_lbl = QLabel("Target Manifest ID:")
        mid_lbl.setStyleSheet("font-weight: bold; color: rgba(255, 255, 255, 0.9);")
        mid_row.addWidget(mid_lbl)
        self.manifest_input = QLineEdit()
        self.manifest_input.setPlaceholderText("e.g. 4622465672933170056")
        self.manifest_input.setFixedHeight(32)
        mid_row.addWidget(self.manifest_input)
        layout.addLayout(mid_row)

        # Pin build checkbox
        self.pin_checkbox = QCheckBox("Pin build (prevent ACCELA auto-updater from overriding this version)")
        self.pin_checkbox.setChecked(True)
        layout.addWidget(self.pin_checkbox)

        layout.addSpacing(6)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 0 16px;
                font-weight: 500;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.14); }
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self.submit_btn = QPushButton("Start Rollback")
        self.submit_btn.setFixedHeight(34)
        self.submit_btn.setStyleSheet(f"""
            QPushButton {{
                background: {self.accent_color};
                color: #000000;
                border: none;
                border-radius: 6px;
                padding: 0 20px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
        """)
        self.submit_btn.clicked.connect(self._on_submit)
        btn_row.addWidget(self.submit_btn)

        layout.addLayout(btn_row)

    def _on_submit(self):
        depot_data = self.depot_combo.currentData()
        if depot_data:
            depot = str(depot_data)
        else:
            text = self.depot_combo.currentText().strip()
            import re
            m = re.search(r'\d+', text)
            depot = m.group(0) if m else text

        build_id = self.build_input.text().strip()
        manifest_id = self.manifest_input.text().strip()

        if not build_id or not manifest_id:
            QMessageBox.warning(self, "Missing Information", "Please enter both Build ID and Manifest ID.")
            return

        if not build_id.isdigit() or not manifest_id.isdigit():
            QMessageBox.warning(self, "Invalid Input", "Build ID and Manifest ID must be numeric digits only.")
            return

        self.selected_depot_id = str(depot)
        self.selected_build_id = str(build_id)
        self.selected_manifest_id = str(manifest_id)
        self.should_pin_build = self.pin_checkbox.isChecked()
        self.accept()


class SteamDBHistoryDialog(QDialog):
    """Fetches patch notes history from SteamDB and automates 1-click depot manifest rollbacks."""

    rollback_requested = pyqtSignal(str, str, str)  # (depot_id, build_id, manifest_id)
    history_loaded = pyqtSignal(list)
    history_error = pyqtSignal(str)
    depots_loaded = pyqtSignal(str, dict)
    depots_error = pyqtSignal(str)

    def __init__(self, parent=None, appid: str = "", game_name: str = "", accent_color: str = "#a1c9fd"):
        super().__init__(parent)
        self.appid = int(appid) if str(appid).isdigit() else 0
        self.game_name = game_name
        self.accent_color = accent_color

        self.scraper = SteamDBScraper()
        self.patchnotes_data: List[Dict[str, Any]] = []
        self.cached_depots: Dict[str, Dict[str, Any]] = {}

        self.setWindowTitle(f"SteamDB Version History — {game_name or f'App {self.appid}'}")
        self.resize(580, 410)
        self.setModal(True)

        self._init_ui()

        # Connect thread-safe signals
        self.history_loaded.connect(self._on_history_loaded)
        self.history_error.connect(self._on_history_error)
        self.depots_loaded.connect(self._display_depot_info)
        self.depots_error.connect(lambda msg: self.status_lbl.setText(msg))

        QTimer.singleShot(150, self._load_history_async)

    def _init_ui(self):
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #141518;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 12px;
            }}
            QLabel {{
                color: #E2E2E6;
                background: transparent;
            }}
            QLineEdit {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #FFFFFF;
                padding: 4px 10px;
                font-size: 8.5pt;
            }}
            QLineEdit:focus {{
                border: 1px solid {self.accent_color};
                background-color: rgba(255, 255, 255, 0.08);
            }}
            QTableWidget {{
                background-color: #1a1b1f;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                gridline-color: rgba(255, 255, 255, 0.04);
                color: #FFFFFF;
                font-size: 8.5pt;
                selection-background-color: {get_dark_container_color(self.accent_color)};
                selection-color: #FFFFFF;
            }}
            QHeaderView::section {{
                background-color: #1f2026;
                color: rgba(255, 255, 255, 0.7);
                padding: 4px 8px;
                font-weight: bold;
                font-size: 8pt;
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 7px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.2);
                border-radius: 3px;
                min-height: 20px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        # Header
        top_row = QHBoxLayout()
        header_vbox = QVBoxLayout()
        header_vbox.setSpacing(1)

        title_lbl = QLabel("SteamDB Version History (Beta)")
        title_lbl.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {self.accent_color};")
        header_vbox.addWidget(title_lbl)

        sub_lbl = QLabel(f"Browse past updates for <b>{self.game_name}</b> and roll back directly.")
        sub_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8pt;")
        header_vbox.addWidget(sub_lbl)
        top_row.addLayout(header_vbox)

        # Refresh button
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setFixedHeight(26)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #E0E0E0;
                padding: 0 12px;
                font-size: 8pt;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.12); }
        """)
        self.refresh_btn.clicked.connect(self._load_history_async)
        top_row.addWidget(self.refresh_btn)

        layout.addLayout(top_row)

        # Filter box
        self.filter_input = QLineEdit()
        self.filter_input.setFixedHeight(28)
        self.filter_input.setPlaceholderText("Filter by patch title, build ID, or date...")
        self.filter_input.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_input)

        # Table (sized for ~5 rows visible with scroll for rest)
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Date & Time", "Patch Title", "Build ID"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.setFixedHeight(172)
        layout.addWidget(self.table)

        # Status / Details Container
        self.details_box = QFrame()
        self.details_box.setStyleSheet("background: rgba(255, 255, 255, 0.03); border-radius: 6px; padding: 6px 10px;")
        details_layout = QVBoxLayout(self.details_box)
        details_layout.setContentsMargins(8, 6, 8, 6)
        details_layout.setSpacing(2)

        self.status_lbl = QLabel("Connecting to SteamDB via Byparr...")
        self.status_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8pt;")
        details_layout.addWidget(self.status_lbl)

        self.depot_info_lbl = QLabel("")
        self.depot_info_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: bold;")
        self.depot_info_lbl.setVisible(False)
        details_layout.addWidget(self.depot_info_lbl)

        layout.addWidget(self.details_box)

        # Footer Button Row
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)

        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(30)
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 0 14px;
                font-size: 8.5pt;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.14); }
        """)
        close_btn.clicked.connect(self.reject)
        bottom_row.addWidget(close_btn)

        self.rollback_btn = QPushButton("Rollback to Selected Build")
        self.rollback_btn.setFixedHeight(30)
        self.rollback_btn.setEnabled(False)
        self.rollback_btn.setStyleSheet(f"""
            QPushButton {{
                background: {self.accent_color};
                color: #000000;
                border: none;
                border-radius: 6px;
                padding: 0 18px;
                font-size: 8.5pt;
                font-weight: bold;
            }}
            QPushButton:disabled {{
                background: rgba(255, 255, 255, 0.08);
                color: rgba(255, 255, 255, 0.25);
            }}
        """)
        self.rollback_btn.clicked.connect(self._on_rollback_clicked)
        bottom_row.addWidget(self.rollback_btn)

        layout.addLayout(bottom_row)

    def _load_history_async(self):
        self.status_lbl.setText("Connecting to SteamDB via Byparr...")
        self.refresh_btn.setEnabled(False)
        self.rollback_btn.setEnabled(False)

        def _worker():
            try:
                data = self.scraper.get_patchnotes(self.appid, limit=50)
                self.history_loaded.emit(data)
            except Exception as e:
                logger.error(f"Error fetching SteamDB history: {e}", exc_info=True)
                self.history_error.emit(str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_history_loaded(self, data: List[Dict[str, Any]]):
        self.refresh_btn.setEnabled(True)
        self.patchnotes_data = data
        self.status_lbl.setText(f"Loaded {len(data)} previous builds from SteamDB. Select an update to roll back.")
        self._populate_table(data)

    def _on_history_error(self, err_msg: str):
        self.refresh_btn.setEnabled(True)
        self.status_lbl.setText(f"Failed to fetch SteamDB history: {err_msg}")

    def _populate_table(self, data: List[Dict[str, Any]]):
        self.table.setRowCount(0)
        self.table.setRowCount(len(data))

        for row_idx, item in enumerate(data):
            date_time = f"{item.get('date', '')} ({item.get('time', '')})".strip()
            title = item.get("title", "Update")
            build_id = item.get("buildid", "")

            it_date = QTableWidgetItem(date_time)
            it_title = QTableWidgetItem(title)
            it_build = QTableWidgetItem(build_id)
            it_build.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            it_date.setData(Qt.ItemDataRole.UserRole, item)

            self.table.setItem(row_idx, 0, it_date)
            self.table.setItem(row_idx, 1, it_title)
            self.table.setItem(row_idx, 2, it_build)

    def _apply_filter(self, text: str):
        query = text.strip().lower()
        if not query:
            for row in range(self.table.rowCount()):
                self.table.setRowHidden(row, False)
            return

        for row in range(self.table.rowCount()):
            date_text = (self.table.item(row, 0).text() if self.table.item(row, 0) else "").lower()
            title_text = (self.table.item(row, 1).text() if self.table.item(row, 1) else "").lower()
            build_text = (self.table.item(row, 2).text() if self.table.item(row, 2) else "").lower()

            match = query in date_text or query in title_text or query in build_text
            self.table.setRowHidden(row, not match)

    def _on_selection_changed(self):
        selected_rows = self.table.selectedItems()
        if not selected_rows:
            self.rollback_btn.setEnabled(False)
            self.depot_info_lbl.setVisible(False)
            return

        row = selected_rows[0].row()
        item_data = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        build_id = item_data.get("buildid")

        if not build_id:
            return

        # Check if depots are cached
        if build_id in self.cached_depots:
            self._display_depot_info(build_id, self.cached_depots[build_id])
            return

        # Fetch depot manifests asynchronously
        self.status_lbl.setText(f"Resolving depot manifests for Build {build_id} from SteamDB...")
        self.depot_info_lbl.setVisible(False)
        self.rollback_btn.setEnabled(False)

        def _depot_worker():
            try:
                depots = self.scraper.get_patch_depots(build_id)
                self.cached_depots[build_id] = depots
                self.depots_loaded.emit(build_id, depots)
            except Exception as e:
                logger.error(f"Failed to fetch depots for {build_id}: {e}")
                self.depots_error.emit(f"Failed to resolve manifests for Build {build_id}.")

        threading.Thread(target=_depot_worker, daemon=True).start()

    def _display_depot_info(self, build_id: str, depots: Dict[str, Dict[str, str]]):
        if not depots:
            self.status_lbl.setText(f"Build {build_id}: No depot manifest changes recorded in this patch.")
            self.depot_info_lbl.setVisible(False)
            self.rollback_btn.setEnabled(False)
            return

        depot_lines = [f"Depot {d_id} → Manifest: {info['manifest_id']}" for d_id, info in depots.items()]
        self.depot_info_lbl.setText(" | ".join(depot_lines))
        self.depot_info_lbl.setVisible(True)
        self.status_lbl.setText(f"Ready to rollback to Build {build_id}.")
        self.rollback_btn.setEnabled(True)

    def _on_rollback_clicked(self):
        selected_rows = self.table.selectedItems()
        if not selected_rows:
            return

        row = selected_rows[0].row()
        item_data = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        build_id = item_data.get("buildid")
        depots = self.cached_depots.get(build_id, {})

        if not depots:
            QMessageBox.warning(self, "No Manifests", f"Could not find depot manifests for Build {build_id}.")
            return

        # Use the first depot
        depot_id = list(depots.keys())[0]
        manifest_id = depots[depot_id]["manifest_id"]

        self.rollback_requested.emit(str(depot_id), str(build_id), str(manifest_id))
        self.accept()
