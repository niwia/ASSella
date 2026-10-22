"""
themed_dialog.py - give any dialog ASSella's own frame and title bar.

Most of ASSella's dialogs are plain QDialog subclasses, so the window manager
draws their decorations; on a Linux desktop that means the desktop's title bar,
close button and colours instead of ASSella's. Window managers also refuse to
let a program draw its own decorations there, so the fix is the one the rest of
the app already uses: a frameless window with DialogTitleBar on top.

The point of this module is that converting a dialog is a one-line change.
A dialog that starts like this:

    layout = QVBoxLayout(self)

starts like this instead:

    layout = install_themed_frame(self, "Move Storage")

and nothing else in it has to change: install_themed_frame() returns the same
kind of layout (the body inside the frame), so every addWidget/addLayout call
after it keeps working.

For new dialogs, ThemedDialog is the same thing as a base class: build on
self.body.

    class RenameDialog(ThemedDialog):
        def __init__(self, parent=None):
            super().__init__(parent, title="Rename")
            self.body.addWidget(QLabel("New name"))
"""

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFrame, QVBoxLayout, QWidget

from ui.dialog_titlebar import DialogTitleBar
from utils.settings import get_settings

logger = logging.getLogger(__name__)

# The frame is the one piece of a dialog that has to be transparent for the
# rounded corners to show; Qt needs it set before the first paint.
_CONTAINER_STYLE = """
    QFrame#themedDialogFrame {{
        background-color: {background};
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 10px;
    }}
"""


def install_themed_frame(
    dialog: QDialog,
    title: str = "",
    *,
    can_minimize: bool = False,
    can_maximize: bool = False,
    use_power_close: bool = True,
    margins: tuple = (16, 12, 16, 16),
    spacing: int = 10,
) -> QVBoxLayout:
    """Put ASSella's frame and title bar around a dialog; return its body layout.

    Call this instead of building the dialog's first ``QVBoxLayout``. The
    returned layout takes the widgets the dialog would have added to that
    layout, so callers only change the constructor line.
    """
    title = title or dialog.windowTitle()

    # QWidget.layout(dialog): a dialog that keeps its body in a `self.layout`
    # attribute (several of ours do) shadows the method, so go through the
    # base class to reach the real one.
    existing = QWidget.layout(dialog)
    if existing is not None:
        # Detach it; it is re-attached inside the frame below.
        existing.setParent(None)

    dialog.setWindowFlags(
        dialog.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
    )
    dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    if title:
        dialog.setWindowTitle(title)

    background = get_settings().value("background_color", "#111318")

    root = QVBoxLayout(dialog)
    root.setContentsMargins(10, 10, 10, 10)
    root.setSpacing(0)

    container = QFrame(dialog)
    container.setObjectName("themedDialogFrame")
    container.setStyleSheet(_CONTAINER_STYLE.format(background=background))

    container_layout = QVBoxLayout(container)
    container_layout.setContentsMargins(0, 0, 0, 0)
    container_layout.setSpacing(0)

    title_bar = DialogTitleBar(
        dialog,
        title=title,
        can_minimize=can_minimize,
        can_maximize=can_maximize,
        use_power_close=use_power_close,
    )
    container_layout.addWidget(title_bar)

    body = QVBoxLayout()
    body.setContentsMargins(*margins)
    body.setSpacing(spacing)
    container_layout.addLayout(body)
    root.addWidget(container)

    if existing is not None:
        body.addLayout(existing)

    # Keep the pieces reachable for dialogs that want to talk to them.
    dialog.themed_frame = container
    dialog.themed_title_bar = title_bar
    dialog.themed_body = body

    return body


class ThemedDialog(QDialog):
    """A dialog that starts out framed, opening on and filling ``self.body``."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        title: str = "",
        can_minimize: bool = False,
        can_maximize: bool = False,
        use_power_close: bool = True,
        margins: tuple = (16, 12, 16, 16),
        spacing: int = 10,
    ):
        super().__init__(parent)
        self.body = install_themed_frame(
            self,
            title,
            can_minimize=can_minimize,
            can_maximize=can_maximize,
            use_power_close=use_power_close,
            margins=margins,
            spacing=spacing,
        )
