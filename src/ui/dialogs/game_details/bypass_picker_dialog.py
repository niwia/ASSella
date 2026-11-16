"""
bypass_picker_dialog.py - Themed modal picker for selecting game executables to bypass third-party launchers.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.themed_dialog import install_themed_frame
from ui.dialogs.themed_file_dialog import ThemedFileDialog
from ui.dialogs.themed_message_box import ThemedMessageBox
from utils.color_utils import get_best_foreground_color
from utils.executable_scanner import format_size, scan_game_executables

logger = logging.getLogger(__name__)


class _ExeRowFrame(QFrame):
    """Clickable row frame representing an executable option."""

    def __init__(self, item: Dict[str, Any], group: QButtonGroup, parent=None, is_active: bool = False):
        super().__init__(parent)
        self.item = item
        self.is_active = is_active
        self.setObjectName("exeRowFrame")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(44)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 12, 4)
        lay.setSpacing(10)

        self.radio = QRadioButton(self)
        self.radio.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        group.addButton(self.radio)
        lay.addWidget(self.radio)

        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(1)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)

        name_lbl = QLabel(item["name"], self)
        name_lbl.setStyleSheet("font-weight: 600; font-size: 8.5pt; color: #FFFFFF; background: transparent;")
        name_row.addWidget(name_lbl)

        if is_active:
            active_badge = QLabel("ACTIVE", self)
            active_badge.setStyleSheet("""
                font-size: 7pt;
                font-weight: bold;
                color: #4ade80;
                background: rgba(74, 222, 128, 0.15);
                border: 1px solid rgba(74, 222, 128, 0.35);
                border-radius: 4px;
                padding: 0 4px;
            """)
            name_row.addWidget(active_badge)

        name_row.addStretch(1)
        info_col.addLayout(name_row)

        path_lbl = QLabel(item["relative_path"], self)
        path_lbl.setStyleSheet("font-size: 7.5pt; color: rgba(255, 255, 255, 0.5); background: transparent;")
        info_col.addWidget(path_lbl)

        lay.addLayout(info_col, 1)

        size_lbl = QLabel(item.get("size_str", ""), self)
        size_lbl.setStyleSheet("font-size: 7.5pt; color: rgba(255, 255, 255, 0.4); background: transparent;")
        lay.addWidget(size_lbl)

        self._update_style(False)

    def _update_style(self, checked: bool):
        border = "rgba(74, 222, 128, 0.6)" if (checked or self.is_active) else "rgba(255, 255, 255, 0.08)"
        bg = "rgba(255, 255, 255, 0.06)" if checked else "rgba(255, 255, 255, 0.025)"
        self.setStyleSheet(f"""
            QFrame#exeRowFrame {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 7px;
            }}
            QFrame#exeRowFrame:hover {{
                background: rgba(255, 255, 255, 0.08);
                border-color: rgba(255, 255, 255, 0.25);
            }}
        """)

    def setChecked(self, checked: bool):
        self.radio.setChecked(checked)
        self._update_style(checked)

    def isChecked(self) -> bool:
        return self.radio.isChecked()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(True)
        super().mousePressEvent(event)


class BypassPickerDialog(QDialog):
    """Themed dialog allowing the user to select an executable to bypass third-party launchers."""

    def __init__(
        self,
        parent=None,
        install_path: Optional[str | Path] = None,
        current_exe: Optional[str] = None,
        accent_color: str = "#22c55e",
    ):
        super().__init__(parent)
        self.install_path = Path(install_path).resolve() if install_path else None
        self.current_exe = current_exe.replace("\\", "/").lstrip("/") if current_exe else None
        self.selected_exe: Optional[str] = self.current_exe
        self.accent_color = accent_color
        self.result_action: Optional[str] = None  # "select" or "clear"

        self.root_exes: List[Dict[str, Any]] = []
        self.subdir_exes: List[Dict[str, Any]] = []
        self._row_widgets: List[_ExeRowFrame] = []
        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)

        self._user_resized = False
        self._fitting = False
        self._shown_once = False

        # Scaled down to ~3/4th width of 620px
        self.resize(465, 320)
        self.setMinimumSize(380, 220)

        self._setup_ui()
        self._load_executables()

    def _setup_ui(self):
        lay = install_themed_frame(self, "Launcher Bypass - beta", use_power_close=True)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(8)

        # Subtitle
        sub_lbl = QLabel(
            "Select the game binary to launch directly, bypassing third-party launchers.",
            self,
        )
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
        lay.addWidget(sub_lbl)

        # Search Bar + Browse
        search_box = QHBoxLayout()
        search_box.setContentsMargins(0, 0, 0, 0)
        search_box.setSpacing(8)

        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Search executables...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedHeight(32)
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(0, 0, 0, 0.35);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 7px;
                color: #FFFFFF;
                font-size: 8.5pt;
                padding: 0 10px;
            }}
            QLineEdit:focus {{
                border-color: {self.accent_color};
            }}
        """)
        self.search_input.textChanged.connect(self._on_search_changed)
        search_box.addWidget(self.search_input, 1)

        browse_btn = QPushButton("Browse", self)
        browse_btn.setFixedHeight(32)
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.setStyleSheet("""
            QPushButton {{
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 7px;
                color: #E0E0E0;
                font-size: 8.5pt;
                font-weight: 500;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.12);
                border-color: rgba(255, 255, 255, 0.25);
            }}
        """)
        browse_btn.clicked.connect(self._on_browse_clicked)
        search_box.addWidget(browse_btn)

        lay.addLayout(search_box)

        # Scroll Area for Executables List
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
            QScrollBar:vertical {
                background: rgba(255, 255, 255, 0.02);
                width: 7px;
                margin: 4px 2px 4px 0;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.18);
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.32);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        self.list_container = QWidget()
        self.list_container.setStyleSheet("background: transparent;")
        self.list_lay = QVBoxLayout(self.list_container)
        self.list_lay.setContentsMargins(6, 6, 6, 14)
        self.list_lay.setSpacing(5)
        self.scroll.setWidget(self.list_container)
        lay.addWidget(self.scroll, 1)

        # Bottom Action Bar: Clear Bypass and Select filling whole width (50/50)
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(0, 4, 0, 0)
        bottom_bar.setSpacing(10)

        # Clear button (removes bypass)
        self.clear_btn = QPushButton("Clear Bypass", self)
        self.clear_btn.setFixedHeight(34)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setToolTip("Remove launcher bypass and restore normal launching")
        self.clear_btn.setStyleSheet("""
            QPushButton {
                background: rgba(239, 68, 68, 0.15);
                border: 1px solid rgba(239, 68, 68, 0.35);
                border-radius: 7px;
                color: #F87171;
                font-weight: 600;
                font-size: 8.5pt;
                padding: 0 14px;
            }
            QPushButton:hover {
                background: rgba(239, 68, 68, 0.25);
                border-color: rgba(239, 68, 68, 0.55);
                color: #FFA4A4;
            }
            QPushButton:disabled {
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.05);
                color: rgba(255, 255, 255, 0.25);
            }
        """)
        self.clear_btn.setEnabled(bool(self.current_exe))
        self.clear_btn.clicked.connect(self._on_clear_clicked)
        bottom_bar.addWidget(self.clear_btn, 1)

        # Select button (confirms selection)
        fg = get_best_foreground_color(self.accent_color, dark_color="#121214", light_color="#FFFFFF")
        self.select_btn = QPushButton("Select", self)
        self.select_btn.setFixedHeight(34)
        self.select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                border: none;
                border-radius: 7px;
                color: {fg};
                font-size: 8.5pt;
                font-weight: bold;
                padding: 0 20px;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.04);
                color: rgba(255, 255, 255, 0.25);
            }}
        """)
        self.select_btn.setEnabled(bool(self.selected_exe))
        self.select_btn.clicked.connect(self._on_select_clicked)
        bottom_bar.addWidget(self.select_btn, 1)

        lay.addLayout(bottom_bar)

        self.button_group.buttonToggled.connect(self._on_radio_toggled)

    def _fit_height(self) -> None:
        """Size the dialog to its content so no empty space appears below the executables."""
        if getattr(self, "_user_resized", False) or self.isMaximized():
            return
        self.list_container.adjustSize()
        list_h = self.list_lay.sizeHint().height() if self.list_lay else 80
        # Title bar + subtitle + search bar + bottom bar + frame padding + comfortable breathing room
        chrome = 215
        target = max(315, min(480, list_h + chrome))
        if abs(target - self.height()) > 3:
            self._fitting = True
            self.resize(self.width(), target)
            self._fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if (
            getattr(self, "_shown_once", False)
            and not getattr(self, "_fitting", False)
            and event.oldSize().height() > 0
            and event.size().height() != event.oldSize().height()
        ):
            self._user_resized = True

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            QTimer.singleShot(0, self._fit_height)

    def _load_executables(self):
        if not self.install_path or not self.install_path.is_dir():
            lbl = QLabel("Game installation folder not found on disk.", self)
            lbl.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8.5pt; padding: 20px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list_lay.addWidget(lbl)
            return

        self.root_exes, self.subdir_exes = scan_game_executables(self.install_path)
        self._populate_list()

    def _create_section_header(self, title: str, count: int) -> QWidget:
        header = QWidget(self)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(4, 6, 4, 3)
        h_lay.setSpacing(6)

        t_lbl = QLabel(title, header)
        t_lbl.setStyleSheet("font-size: 8pt; font-weight: bold; color: rgba(255, 255, 255, 0.85);")
        h_lay.addWidget(t_lbl)

        cnt_lbl = QLabel(str(count), header)
        cnt_lbl.setStyleSheet("""
            font-size: 7.5pt;
            font-weight: 600;
            color: rgba(255, 255, 255, 0.6);
            background: rgba(255, 255, 255, 0.08);
            border-radius: 4px;
            padding: 1px 5px;
        """)
        h_lay.addWidget(cnt_lbl)
        h_lay.addStretch(1)
        return header

    def _populate_list(self, filter_text: str = ""):
        # Clear existing rows
        for row in self._row_widgets:
            self.button_group.removeButton(row.radio)
            row.deleteLater()
        self._row_widgets.clear()

        # Remove previous section headers/empty labels
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        ft = filter_text.strip().lower()
        matched_roots = [
            x for x in self.root_exes
            if not ft or ft in x["name"].lower() or ft in x["relative_path"].lower()
        ]
        matched_subs = [
            x for x in self.subdir_exes
            if not ft or ft in x["name"].lower() or ft in x["relative_path"].lower()
        ]

        if not matched_roots and not matched_subs:
            empty = QLabel("No executables found matching your search.", self)
            empty.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8.5pt; padding: 20px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list_lay.addWidget(empty)
            self.list_lay.addStretch(1)
            QTimer.singleShot(0, self._fit_height)
            return

        # 1. Root executables section
        if matched_roots:
            self.list_lay.addWidget(self._create_section_header("Root Executables (Recommended)", len(matched_roots)))
            for item in matched_roots:
                is_act = bool(self.current_exe and item["relative_path"] == self.current_exe)
                row = _ExeRowFrame(item, self.button_group, is_active=is_act)
                if self.selected_exe and item["relative_path"] == self.selected_exe:
                    row.setChecked(True)
                self._row_widgets.append(row)
                self.list_lay.addWidget(row)

        # 2. Subdirectory executables section
        if matched_subs:
            self.list_lay.addWidget(self._create_section_header("Subdirectory Executables", len(matched_subs)))
            for item in matched_subs:
                is_act = bool(self.current_exe and item["relative_path"] == self.current_exe)
                row = _ExeRowFrame(item, self.button_group, is_active=is_act)
                if self.selected_exe and item["relative_path"] == self.selected_exe:
                    row.setChecked(True)
                self._row_widgets.append(row)
                self.list_lay.addWidget(row)

        self.list_lay.addStretch(1)
        QTimer.singleShot(0, self._fit_height)

    def _on_search_changed(self, text: str):
        self._populate_list(text)

    def _on_radio_toggled(self, button: QRadioButton, checked: bool):
        if not checked:
            return
        for row in self._row_widgets:
            if row.radio == button:
                row._update_style(True)
                self.selected_exe = row.item["relative_path"]
                self.select_btn.setEnabled(True)
                self.clear_btn.setEnabled(True)
            else:
                row._update_style(False)

    def _on_browse_clicked(self):
        start_dir = str(self.install_path) if self.install_path and self.install_path.is_dir() else ""
        chosen, _ = ThemedFileDialog.getOpenFileName(
            self,
            "Select Executable",
            start_dir,
            "Executable Files (*.exe);;All Files (*)",
        )
        if not chosen:
            return

        chosen_path = Path(chosen).resolve()
        if not self.install_path:
            ThemedMessageBox.warning(self, "Error", "Game installation folder is not set.")
            return

        try:
            rel = chosen_path.relative_to(self.install_path).as_posix()
        except ValueError:
            ThemedMessageBox.warning(
                self,
                "Invalid Executable Location",
                "The selected executable must be inside the game installation folder.\n\n"
                f"Game directory: {self.install_path}\n"
                f"Selected file: {chosen_path}",
            )
            return

        # Check if already present in scanned lists
        exists_in_roots = any(x["relative_path"] == rel for x in self.root_exes)
        exists_in_subs = any(x["relative_path"] == rel for x in self.subdir_exes)

        if not exists_in_roots and not exists_in_subs:
            try:
                sz = chosen_path.stat().st_size
            except OSError:
                sz = 0
            item = {
                "name": chosen_path.name,
                "relative_path": rel,
                "path": str(chosen_path),
                "size_bytes": sz,
                "size_str": format_size(sz),
                "is_root": "/" not in rel,
            }
            if "/" in rel:
                self.subdir_exes.insert(0, item)
            else:
                self.root_exes.insert(0, item)

        self.selected_exe = rel
        self.search_input.clear()
        self._populate_list()
        self.select_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)

    def _on_clear_clicked(self):
        self.result_action = "clear"
        self.accept()

    def _on_select_clicked(self):
        if not self.selected_exe:
            return
        self.result_action = "select"
        self.accept()
