import os
import re
import subprocess
import time
import requests
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QCheckBox, QSpinBox, QGroupBox, QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from utils.settings import get_settings
from utils.paths import Paths
from utils.helpers import get_base_path, get_dotnet_path
from core import steam_helpers
from core.morrenus_api import BASE_URL
from ui.dialogs.steamlibrary import SteamLibraryDialog

def extract_workshop_id(raw: str):
    raw = raw.strip()
    m = re.search(r"[?&]id=(\d+)", raw)
    if m: return m.group(1)
    if re.fullmatch(r"\d+", raw): return raw
    return None

def parse_workshop_ids(text: str):
    tokens = re.split(r"[\s,]+", text)
    ids = []
    for t in tokens:
        t = t.strip()
        if not t: continue
        wid = extract_workshop_id(t)
        if wid: ids.append(wid)
    return list(dict.fromkeys(ids))

class WorkshopDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Workshop Downloader")
        self.resize(550, 420)
        self.settings = get_settings()
        
        self.accent_color = self.settings.value("accent_color", "#a1c9fd")
        self.bg_color = self.settings.value("background_color", "#111318")
        
        self.setStyleSheet(
            f"""
            QDialog {{
                background-color: {self.bg_color};
            }}
            QGroupBox {{
                color: {self.accent_color};
                border: 1px solid {self.accent_color};
                margin-top: 6px;
                padding-top: 10px;
                border-radius: 4px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 7px;
                padding: 0 3px 0 3px;
            }}
            QPushButton {{
                background-color: {self.bg_color};
                border: 1px solid {self.accent_color};
                color: {self.accent_color};
                padding: 6px 12px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: {self.accent_color};
                color: {self.bg_color};
            }}
            QLineEdit, QTextEdit, QSpinBox {{
                background-color: {self.bg_color};
                color: {self.accent_color};
                border: 1px solid {self.accent_color};
                border-radius: 4px;
                padding: 4px;
            }}
            QLabel {{
                color: {self.accent_color};
            }}
        """
        )
        
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # IDs
        ids_group = QGroupBox("Workshop IDs or Steam URLs (one per line, or comma-separated)")
        ids_layout = QVBoxLayout(ids_group)
        self.ids_input = QTextEdit()
        self.ids_input.setPlaceholderText("e.g. https://steamcommunity.com/sharedfiles/filedetails/?id=3739182697\nor 3739182697")
        ids_layout.addWidget(self.ids_input)
        layout.addWidget(ids_group)

        # Options
        opts_group = QGroupBox("Configuration Options")
        opts_layout = QHBoxLayout(opts_group)
        
        opts_layout.addWidget(QLabel("Max downloads:"))
        self.max_dl = QSpinBox()
        self.max_dl.setRange(1, 30)
        current_max = self.settings.value("max_downloads", 4, type=int)
        self.max_dl.setValue(current_max if 1 <= current_max <= 30 else 4)
        opts_layout.addWidget(self.max_dl)
        
        opts_layout.addWidget(QLabel("Cell ID:"))
        self.cell_id = QLineEdit()
        self.cell_id.setPlaceholderText("Optional")
        opts_layout.addWidget(self.cell_id)
        
        # Steam Integration Checkbox inline
        self.steam_check = QCheckBox("Enable Steam Integration")
        opts_layout.addSpacing(10)
        opts_layout.addWidget(self.steam_check)
        
        layout.addWidget(opts_group)

        # Buttons
        btns_layout = QHBoxLayout()
        self.dl_btn = QPushButton("⬇ Download (Add to Queue)")
        self.dl_btn.clicked.connect(self._download)
        self.save_btn = QPushButton("💾 Save Settings")
        self.save_btn.clicked.connect(self._save_settings)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        self.status_label = QLabel()
        
        btns_layout.addWidget(self.dl_btn)
        btns_layout.addWidget(self.save_btn)
        btns_layout.addWidget(self.close_btn)
        btns_layout.addWidget(self.status_label)
        btns_layout.addStretch()
        layout.addLayout(btns_layout)

    def _load_settings(self):
        self.steam_check.setChecked(self.settings.value("workshop_steam_enabled", False, type=bool))

    def _save_settings(self):
        self.settings.setValue("workshop_steam_enabled", self.steam_check.isChecked())
        self.status_label.setText("✓ Settings saved.")

    def _download(self):
        api_key = self.settings.value("morrenus_api_key", "", type=str)
        if not api_key:
            QMessageBox.warning(self, "No API Key", "Please enter your Hubcab API key in ACCELA Settings first.")
            return

        raw = self.ids_input.toPlainText().strip()
        if not raw:
            QMessageBox.warning(self, "No IDs", "Please enter at least one Workshop ID or URL.")
            return

        wids = parse_workshop_ids(raw)
        if not wids:
            QMessageBox.warning(self, "No Valid IDs", "Could not parse any Workshop IDs from the input.")
            return

        max_downloads = self.max_dl.value()
        cellid = self.cell_id.text().strip()
        steam_integration = self.steam_check.isChecked()

        dest_path = ""
        if steam_integration:
            libraries = steam_helpers.get_steam_libraries()
            if libraries:
                auto_skip_single_choice = self.settings.value("auto_skip_single_choice", False, type=bool)
                if auto_skip_single_choice and len(libraries) == 1:
                    dest_path = libraries[0]
                else:
                    dialog = SteamLibraryDialog(libraries, self)
                    if dialog.exec():
                        dest_path = dialog.get_selected_path()
                    else:
                        return
            else:
                dest_path = QFileDialog.getExistingDirectory(self, "Select Steam Library Folder")
                if not dest_path:
                    return
        else:
            dest_path = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
            if not dest_path:
                return

        self._save_settings()
        
        main_window = self.parent()
        if main_window and hasattr(main_window, "job_queue"):
            main_window.job_queue.add_workshop_job(wids, api_key, max_downloads, cellid, steam_integration, dest_path)
            QMessageBox.information(self, "Job Queued", f"Successfully added Workshop download job with {len(wids)} items to the queue.")
            self.accept()
        else:
            QMessageBox.critical(self, "Error", "Could not access the application job queue.")
