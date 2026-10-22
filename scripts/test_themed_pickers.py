#!/usr/bin/env python3
"""
Hermetic checks for the themed pickers: file/folder, colour and one-question.

No display and no desktop dialog: every check runs offscreen and stubs the
modal loop, so it pins the drop-in interface (shapes, modes, defaults) rather
than any real choice.

Run with:
    QT_QPA_PLATFORM=offscreen python3 scripts/test_themed_pickers.py
"""

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.gettempdir())

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog  # noqa: E402

app = QApplication.instance() or QApplication([])

from ui.dialogs.themed_color_dialog import ThemedColorDialog  # noqa: E402
from ui.dialogs.themed_file_dialog import (  # noqa: E402
    ThemedFileDialog,
    start_directory,
    suggested_name,
)
from ui.dialogs.themed_input_dialog import ThemedInputDialog  # noqa: E402

FRAMELESS = Qt.WindowType.FramelessWindowHint
_failures = []


def check(cond, label):
    if cond:
        print(f"  [ok] {label}")
    else:
        print(f"  [FAIL] {label}")
        _failures.append(label)


def accept_with(**attrs):
    """Replace exec() with one that prepares the dialog and accepts."""

    def _exec(self):
        for name, value in attrs.items():
            if name == "color":
                self.picker.setCurrentColor(value)
            elif name == "path":
                self.result_path = value
            elif name == "text":
                self.setTextValue(value)
        return QDialog.DialogCode.Accepted

    return _exec


def reject(self):
    return QDialog.DialogCode.Rejected


def check_file_dialog():
    print("file dialog")
    ThemedFileDialog.exec = accept_with(path="/tmp/picked.yaml")
    path, name_filter = ThemedFileDialog.getOpenFileName(None, "Open", "/tmp", "YAML (*.yaml)")
    check(path == "/tmp/picked.yaml", "getOpenFileName returns the accepted path")
    check(isinstance(name_filter, str), "getOpenFileName returns (path, filter)")

    ThemedFileDialog.exec = accept_with(path="/tmp/saved.yaml")
    path, _ = ThemedFileDialog.getSaveFileName(None, "Save", "/tmp/out.yaml", "YAML (*.yaml)")
    check(path == "/tmp/saved.yaml", "getSaveFileName returns the accepted path")

    ThemedFileDialog.exec = accept_with(path="/tmp")
    check(
        ThemedFileDialog.getExistingDirectory(None, "Folder", "/tmp") == "/tmp",
        "getExistingDirectory returns a plain path",
    )

    ThemedFileDialog.exec = reject
    check(
        ThemedFileDialog.getOpenFileName(None, "Open", "/tmp")[0] == "",
        "a cancelled open returns an empty path",
    )
    check(
        ThemedFileDialog.getSaveFileName(None, "Save", "")[0] == "",
        "a cancelled save returns an empty path",
    )
    check(
        ThemedFileDialog.getExistingDirectory(None, "Folder", "") == "",
        "a cancelled folder pick returns an empty path",
    )

    opener = ThemedFileDialog(None, mode=ThemedFileDialog.OPEN, directory="/tmp")
    check(bool(opener.windowFlags() & FRAMELESS), "the file picker is frameless")
    check(
        opener.browser.fileMode() == QFileDialog.FileMode.ExistingFile
        and opener.browser.acceptMode() == QFileDialog.AcceptMode.AcceptOpen,
        "open mode picks an existing file",
    )
    saver = ThemedFileDialog(None, mode=ThemedFileDialog.SAVE, directory="/tmp/out.yaml")
    check(
        saver.browser.fileMode() == QFileDialog.FileMode.AnyFile
        and saver.browser.acceptMode() == QFileDialog.AcceptMode.AcceptSave,
        "save mode accepts a new name",
    )
    check(saver.browser.selectedFiles() != [], "the suggested name is offered")
    folder = ThemedFileDialog(None, mode=ThemedFileDialog.DIRECTORY, directory="/tmp")
    check(
        folder.browser.fileMode() == QFileDialog.FileMode.Directory
        and folder.browser.testOption(QFileDialog.Option.ShowDirsOnly),
        "folder mode shows folders only",
    )


def check_start_directory():
    print("start directory helpers")
    base = tempfile.mkdtemp()
    home = os.path.expanduser("~")
    check(start_directory(base) == base, "a real directory is used as itself")
    check(start_directory(os.path.join(base, "a.yaml")) == base, "a file path opens its folder")
    check(start_directory(os.path.join(base, "gone", "x.yaml")) == home, "a missing tree falls back home")
    check(start_directory("") == home, "an empty path falls back home")
    check(suggested_name(os.path.join(base, "a.yaml")) == "a.yaml", "a file path suggests its name")
    check(suggested_name(base) == "", "a directory suggests no name")


def check_color_dialog():
    print("colour dialog")
    ThemedColorDialog.exec = accept_with(color=QColor("#123456"))
    check(ThemedColorDialog.getColor().name() == "#123456", "the picked colour comes back")
    ThemedColorDialog.exec = reject
    check(not ThemedColorDialog.getColor().isValid(), "cancelling returns an invalid colour")

    dialog = ThemedColorDialog(None, initial=QColor("#C06C84"))
    check(dialog.selected_color().name() == "#c06c84", "the initial colour is preselected")
    check(bool(dialog.windowFlags() & FRAMELESS), "the colour picker is frameless")
    check(dialog.title_bar is not None, "the colour picker carries ASSella's title bar")


def check_input_dialog():
    print("input dialog")
    ThemedInputDialog.exec = accept_with(text="hello")
    check(ThemedInputDialog.getText(None, "Name", "Enter a name") == ("hello", True), "getText returns text")
    ThemedInputDialog.exec = reject
    check(ThemedInputDialog.getText(None, "Name", "Enter a name") == ("", False), "a cancelled getText is empty")

    ThemedInputDialog.exec = lambda self: QDialog.DialogCode.Accepted
    check(
        ThemedInputDialog.getItem(None, "Branch", "Pick one", ["public", "beta"], current=1)
        == ("beta", True),
        "getItem returns the current choice",
    )

    dialog = ThemedInputDialog(None, title="Select Branch", label="Pick", items=["a", "b"])
    check(dialog.textValue() == "a", "a combo dialog starts on the first item")
    dialog.setComboBoxItems(["x", "y"])
    dialog.setTextValue("y")
    check(dialog.textValue() == "y", "items can be replaced and the value set")
    check(bool(dialog.windowFlags() & FRAMELESS), "the input dialog is frameless")

    typed = ThemedInputDialog(None, title="Name", label="Enter a name", text="abc")
    check(typed.textValue() == "abc", "a text dialog starts with the caller's text")
    check(typed.title_bar.title_label.text() == "Name", "the title bar shows the title")
    typed.setWindowTitle("Other")
    check(typed.title_bar.title_label.text() == "Other", "setWindowTitle updates the title bar")


def main() -> int:
    check_file_dialog()
    check_start_directory()
    check_color_dialog()
    check_input_dialog()

    print()
    if _failures:
        print(f"FAILED {len(_failures)} checks:")
        for label in _failures:
            print(f"  - {label}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
