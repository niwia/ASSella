"""
MoveStorageDialog: Dialog and helper to migrate game installations between Steam library storage locations.
Supports both ACCELA-managed and AT0-M (native Steam) games.
"""

import os
import shutil
import logging
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QProgressBar,
    QMessageBox,
    QFrame,
)

from core.steam_helpers import get_steam_libraries, find_steam_install
from utils.paths import is_valid_download_directory

logger = logging.getLogger(__name__)


def _format_bytes(num_bytes: int) -> str:
    if num_bytes >= 1024**4:
        return f"{num_bytes / (1024**4):.2f} TB"
    elif num_bytes >= 1024**3:
        return f"{num_bytes / (1024**3):.2f} GB"
    elif num_bytes >= 1024**2:
        return f"{num_bytes / (1024**2):.1f} MB"
    return f"{num_bytes} B"


class StorageMoveWorker(QThread):
    finished_signal = pyqtSignal(bool, str)
    status_signal = pyqtSignal(str)

    def __init__(self, src_install: str, src_acf: str, dest_lib: str, installdir_name: str, appid: str):
        super().__init__()
        self.src_install = src_install
        self.src_acf = src_acf
        self.dest_lib = dest_lib
        self.installdir_name = installdir_name
        self.appid = appid

    def run(self):
        try:
            self.status_signal.emit(f"Preparing destination in {self.dest_lib}...")
            dest_common = Path(self.dest_lib) / "steamapps" / "common"
            dest_steamapps = Path(self.dest_lib) / "steamapps"
            dest_common.mkdir(parents=True, exist_ok=True)

            dest_install = dest_common / self.installdir_name
            if dest_install.exists():
                self.finished_signal.emit(False, f"Destination folder already exists: {dest_install}")
                return

            self.status_signal.emit(f"Moving game folder to {dest_install}...")
            # Use shutil.move to handle cross-device copies cleanly
            shutil.move(self.src_install, str(dest_install))

            # Move ACF manifest if present
            if self.src_acf and os.path.exists(self.src_acf):
                dest_acf = dest_steamapps / f"appmanifest_{self.appid}.acf"
                self.status_signal.emit("Moving appmanifest.acf...")
                shutil.move(self.src_acf, str(dest_acf))

            self.finished_signal.emit(True, str(dest_install))
        except Exception as e:
            logger.error(f"[MoveStorage] Error during move: {e}", exc_info=True)
            self.finished_signal.emit(False, str(e))


class MoveStorageDialog(QDialog):
    """Dialog allowing user to choose a target Steam library and move the game files."""

    def __init__(self, parent, game_data: dict, accent_color: str = "#C06C84"):
        super().__init__(parent)
        self.game_data = game_data
        self.accent_color = accent_color
        self.appid = str(game_data.get("appid", "0"))
        self.game_name = game_data.get("game_name", f"App {self.appid}")
        self.src_install = game_data.get("install_path", "")
        self.src_lib = game_data.get("library_path", "")
        self.src_acf = game_data.get("appmanifest_path", "")
        self.worker: Optional[StorageMoveWorker] = None

        self.setWindowTitle(f"Move Game Storage - {self.game_name}")
        self.setFixedSize(500, 360)
        self.setStyleSheet("""
            QDialog {
                background-color: #12131a;
                color: #FFFFFF;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel(f"Move <b>{self.game_name}</b> to another drive")
        title.setStyleSheet("font-size: 11pt; font-weight: bold; color: #FFFFFF;")
        layout.addWidget(title)

        curr_lbl = QLabel(f"Current Location:\n<span style='color: rgba(255,255,255,0.6);'>{self.src_install}</span>")
        curr_lbl.setWordWrap(True)
        curr_lbl.setStyleSheet("font-size: 8.5pt;")
        layout.addWidget(curr_lbl)

        # Detect candidate libraries
        raw_libs = get_steam_libraries() or []
        real_src_lib = os.path.realpath(self.src_lib) if self.src_lib else ""
        self.target_libs = []
        for p in raw_libs:
            if p and is_valid_download_directory(p):
                real_p = os.path.realpath(p)
                if real_p != real_src_lib and real_p not in self.target_libs:
                    self.target_libs.append(real_p)

        target_lbl = QLabel("Select Target Steam Library:")
        target_lbl.setStyleSheet(f"font-size: 9pt; font-weight: bold; color: {self.accent_color};")
        layout.addWidget(target_lbl)

        self.btn_group = QButtonGroup(self)
        self.radio_buttons = []

        options_frame = QFrame()
        options_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
        """)
        options_lay = QVBoxLayout(options_frame)
        options_lay.setContentsMargins(12, 10, 12, 10)
        options_lay.setSpacing(8)

        steam_root = find_steam_install()

        for idx, lib_path in enumerate(self.target_libs):
            try:
                free_bytes = shutil.disk_usage(lib_path).free
                free_str = f"{_format_bytes(free_bytes)} free"
            except Exception:
                free_str = ""

            p_lower = lib_path.lower()
            if steam_root and os.path.realpath(lib_path) == os.path.realpath(steam_root):
                tag = "Primary Drive"
            elif "sdcard" in p_lower or "sd_card" in p_lower or "mmcblk" in p_lower:
                tag = "SD Card"
            else:
                tag = Path(lib_path).name

            rb = QRadioButton(f"{tag} — {free_str}\n{lib_path}")
            rb.setStyleSheet(f"""
                QRadioButton {{
                    color: rgba(255, 255, 255, 0.85);
                    font-size: 8.5pt;
                }}
                QRadioButton:checked {{
                    color: #FFFFFF;
                    font-weight: bold;
                }}
            """)
            self.btn_group.addButton(rb, idx)
            self.radio_buttons.append(rb)
            options_lay.addWidget(rb)
            if idx == 0:
                rb.setChecked(True)

        layout.addWidget(options_frame)

        # Progress / Status
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255,255,255,0.7);")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: rgba(255,255,255,0.06);
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background-color: {self.accent_color};
                border-radius: 4px;
            }}
        """)
        layout.addWidget(self.status_lbl)
        layout.addWidget(self.progress_bar)

        # Bottom Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedHeight(34)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 6px;
                color: #FFFFFF;
                padding: 0 16px;
                font-size: 9pt;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.10);
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.move_btn = QPushButton("Move Game")
        self.move_btn.setFixedHeight(34)
        self.move_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.move_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                border: none;
                border-radius: 6px;
                color: #FFFFFF;
                font-weight: bold;
                padding: 0 18px;
                font-size: 9pt;
            }}
            QPushButton:hover {{
                background-color: #d17892;
            }}
            QPushButton:disabled {{
                background-color: rgba(255,255,255,0.1);
                color: rgba(255,255,255,0.3);
            }}
        """)
        self.move_btn.clicked.connect(self._start_move)
        btn_layout.addWidget(self.move_btn)

        layout.addLayout(btn_layout)

    def _start_move(self):
        checked_id = self.btn_group.checkedId()
        if checked_id < 0 or checked_id >= len(self.target_libs):
            QMessageBox.warning(self, "No Storage Selected", "Please select a target storage library.")
            return

        target_lib = self.target_libs[checked_id]
        folder_name = Path(self.src_install).name
        if not folder_name:
            folder_name = self.game_data.get("install_dir") or self.game_name

        # Check disk space
        try:
            free_bytes = shutil.disk_usage(target_lib).free
            game_size = self.game_data.get("size_on_disk", 0)
            if game_size and free_bytes < (game_size + 1024**3):  # 1GB margin
                QMessageBox.warning(
                    self,
                    "Insufficient Disk Space",
                    f"Target drive only has {_format_bytes(free_bytes)} free, which may not be enough for this game ({_format_bytes(game_size)})."
                )
                return
        except Exception:
            pass

        self.move_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setVisible(True)

        self.worker = StorageMoveWorker(
            src_install=self.src_install,
            src_acf=self.src_acf,
            dest_lib=target_lib,
            installdir_name=folder_name,
            appid=self.appid,
        )
        self.worker.status_signal.connect(self.status_lbl.setText)
        self.worker.finished_signal.connect(lambda ok, msg: self._on_move_finished(ok, msg, target_lib, folder_name))
        self.worker.start()

    def _on_move_finished(self, success: bool, msg: str, target_lib: str, folder_name: str):
        self.progress_bar.setVisible(False)
        self.move_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)

        if not success:
            QMessageBox.critical(self, "Move Failed", f"Failed to move game storage:\n\n{msg}")
            return

        new_install_path = msg
        new_acf_path = str(Path(target_lib) / "steamapps" / f"appmanifest_{self.appid}.acf")

        # Update in-memory game_data
        self.game_data["install_path"] = new_install_path
        self.game_data["library_path"] = target_lib
        if os.path.exists(new_acf_path):
            self.game_data["appmanifest_path"] = new_acf_path

        # Update GameManager if parent available
        parent = self.parent()
        if parent:
            gm = getattr(parent, "game_manager", None)
            if not gm:
                top = parent.parent() if hasattr(parent, "parent") else None
                gm = getattr(top, "game_manager", None) if top else None
            if gm and hasattr(gm, "get_game"):
                g = gm.get_game(self.appid)
                if g:
                    g["install_path"] = new_install_path
                    g["library_path"] = target_lib
                    g["appmanifest_path"] = new_acf_path

        # If in AT0-M mode, update plugin_library.json
        if self.game_data.get("is_atom") or self.game_data.get("is_plugin_game"):
            try:
                from utils.plugin_games import load_plugin_library, save_plugin_library
                lib = load_plugin_library()
                if self.appid in lib:
                    lib[self.appid]["installdir"] = folder_name
                    save_plugin_library(lib)
            except Exception as e:
                logger.warning(f"Could not update plugin_library.json installdir: {e}")

        QMessageBox.information(
            self,
            "Move Complete",
            f"Successfully moved '{self.game_name}' to:\n{target_lib}"
        )
        self.accept()
