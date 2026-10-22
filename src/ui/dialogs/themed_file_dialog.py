"""
themed_file_dialog.py - ASSella's own file and folder picker.

QFileDialog without DontUseNativeDialog hands the job to the desktop: on a
Linux desktop that is a GTK or KDE window, so the picker carries the desktop's
decorations and colours instead of ASSella's. This module keeps Qt's own
browser (navigation, places, filters, open/save modes) but hosts it inside a
frameless, themed window with the same title bar every other dialog uses.

The browser is reused rather than reimplemented on purpose: a hand-rolled
browser would lose bookmarks, multi-select, type-ahead and the sidebar that
users already expect from a file picker, and those are not things to get wrong
in a download tool.

Drop-in replacements for the three calls the app makes:

    ThemedFileDialog.getOpenFileName(parent, title, directory, name_filter)
    ThemedFileDialog.getSaveFileName(parent, title, directory, name_filter)
    ThemedFileDialog.getExistingDirectory(parent, title, directory)

`getExistingDirectory` also accepts QFileDialog's `options` argument and ignores
it: this picker always shows folders only, which is what every caller wants.
"""

import logging
import os
from typing import Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QVBoxLayout,
    QWidget,
)

from ui.dialog_titlebar import DialogTitleBar
from utils.settings import get_settings

logger = logging.getLogger(__name__)


def start_directory(directory: str) -> str:
    """Turn whatever a caller passed into a directory the browser can open.

    Callers pass a folder, a file inside one (the save dialogs do), or an
    empty string. A path that does not exist yet falls back to the home
    folder, which is what QFileDialog does too.
    """
    candidate = (directory or "").strip()
    if not candidate:
        return os.path.expanduser("~")
    candidate = os.path.expanduser(candidate)
    if os.path.isdir(candidate):
        return candidate
    parent = os.path.dirname(candidate)
    if parent and os.path.isdir(parent):
        return parent
    return os.path.expanduser("~")


def suggested_name(directory: str) -> str:
    """The file name part of a caller's `directory` argument, if it has one."""
    candidate = (directory or "").strip()
    if not candidate or os.path.isdir(candidate):
        return ""
    return os.path.basename(candidate)


class ThemedFileDialog(QDialog):
    """Frameless host for Qt's file browser, so the picker looks like ASSella."""

    OPEN = "open"
    SAVE = "save"
    DIRECTORY = "directory"

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        title: str = "Select a file",
        mode: str = OPEN,
        directory: str = "",
        name_filter: str = "",
        filename: str = "",
    ):
        super().__init__(parent)
        self.result_path = ""
        self._mode = mode

        settings = get_settings()
        self.accent_color = settings.value("accent_color", "#a1c9fd")
        self.background_color = settings.value("background_color", "#111318")

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(760, 520)

        self._build_ui(title, mode, directory, name_filter, filename)

    # ─────────────────────────────────────────────────────────────────────────
    # Layout
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self, title, mode, directory, name_filter, filename):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        self.container = QFrame(self)
        self.container.setObjectName("dialogContainer")
        self.container.setStyleSheet(f"""
            QFrame#dialogContainer {{
                background-color: {self.background_color};
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }}
            QTreeView, QListView {{
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 6px;
                color: #e5e7eb;
            }}
            QTreeView::item:selected, QListView::item:selected {{
                background-color: {self.accent_color};
                color: #0b0d12;
            }}
            QLineEdit, QComboBox {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                color: #e5e7eb;
                padding: 4px 8px;
                min-height: 22px;
            }}
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                color: #e5e7eb;
                font-size: 8.5pt;
                padding: 4px 14px;
                min-height: 24px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.12);
                color: #ffffff;
                border-color: rgba(255, 255, 255, 0.25);
            }}
        """)

        container_lay = QVBoxLayout(self.container)
        container_lay.setContentsMargins(0, 0, 0, 10)
        container_lay.setSpacing(8)

        self.title_bar = DialogTitleBar(
            self,
            title=title,
            can_minimize=False,
            can_maximize=False,
            use_power_close=True,
        )
        container_lay.addWidget(self.title_bar)

        body = QVBoxLayout()
        body.setContentsMargins(12, 0, 12, 0)
        body.setSpacing(8)

        self.browser = QFileDialog(self)
        self.browser.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self.browser.setWindowFlags(Qt.WindowType.Widget)
        self.browser.setSizeGripEnabled(False)
        self.browser.setModal(False)
        self._configure_browser(mode, directory, name_filter, filename)
        self.browser.accepted.connect(self._on_browser_accepted)
        self.browser.rejected.connect(self.reject)
        body.addWidget(self.browser)

        container_lay.addLayout(body)
        root.addWidget(self.container)

    def _configure_browser(self, mode, directory, name_filter, filename):
        if mode == self.DIRECTORY:
            self.browser.setFileMode(QFileDialog.FileMode.Directory)
            self.browser.setOption(QFileDialog.Option.ShowDirsOnly, True)
            self.browser.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
            self.browser.setLabelText(QFileDialog.DialogLabel.Accept, "Select Folder")
        elif mode == self.SAVE:
            self.browser.setFileMode(QFileDialog.FileMode.AnyFile)
            self.browser.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
            self.browser.setLabelText(QFileDialog.DialogLabel.Accept, "Save")
        else:
            self.browser.setFileMode(QFileDialog.FileMode.ExistingFile)
            self.browser.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
            self.browser.setLabelText(QFileDialog.DialogLabel.Accept, "Open")

        self.browser.setDirectory(start_directory(directory))
        if name_filter:
            self.browser.setNameFilter(name_filter)
        chosen = filename or suggested_name(directory)
        if chosen and mode != self.DIRECTORY:
            self.browser.selectFile(chosen)

    # ─────────────────────────────────────────────────────────────────────────
    # Result
    # ─────────────────────────────────────────────────────────────────────────

    def _on_browser_accepted(self):
        selected = self.browser.selectedFiles()
        if not selected:
            return
        self.result_path = selected[0]
        self.accept()

    @staticmethod
    def _run(parent, title, mode, directory, name_filter, filename="") -> Tuple[str, str]:
        dialog = ThemedFileDialog(
            parent,
            title=title,
            mode=mode,
            directory=directory,
            name_filter=name_filter,
            filename=filename,
        )
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        path = dialog.result_path if accepted else ""
        if path:
            logger.debug("[ThemedFileDialog] %s -> %s", mode, path)
        return path, (dialog.browser.selectedNameFilter() if hasattr(dialog, "browser") else "")

    # ─────────────────────────────────────────────────────────────────────────
    # QFileDialog compatible interface
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def getOpenFileName(
        cls,
        parent=None,
        title: str = "Open File",
        directory: str = "",
        name_filter: str = "",
    ) -> Tuple[str, str]:
        return cls._run(parent, title, cls.OPEN, directory, name_filter)

    @classmethod
    def getSaveFileName(
        cls,
        parent=None,
        title: str = "Save File",
        directory: str = "",
        name_filter: str = "",
    ) -> Tuple[str, str]:
        return cls._run(parent, title, cls.SAVE, directory, name_filter)

    @classmethod
    def getExistingDirectory(
        cls,
        parent=None,
        title: str = "Select Folder",
        directory: str = "",
        options=None,
    ) -> str:
        path, _ = cls._run(parent, title, cls.DIRECTORY, directory, "")
        return path
