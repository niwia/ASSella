"""
themed_message_box.py - ASSella custom themed alert & confirmation dialog.
Replaces native OS QMessageBox with ASSella dark theme and DialogTitleBar,
eliminating standard Linux window-manager decorations.

Call sites migrate mechanically, because the class methods mirror
``QMessageBox.{information,warning,critical,question}(parent, title, text, buttons, default_button)``
and return a ``StandardButton`` whose member names and values are QMessageBox's own:

    from ui.dialogs.themed_message_box import ThemedMessageBox

    reply = ThemedMessageBox.question(
        parent,
        "Title",
        "Text",
        ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
        ThemedMessageBox.StandardButton.Yes,
    )
    if reply == ThemedMessageBox.StandardButton.Yes:
        ...

A dialog that needs its own buttons (QMessageBox.addButton) uses
``ThemedMessageBox.choices`` instead, and gets the value it passed back:

    choice = ThemedMessageBox.choices(
        parent, "Title", "Text",
        [("Retry", "retry"), ("Cancel", "cancel")], default="cancel",
    )
    if choice == "retry":
        ...

The message text is rendered as plain text on purpose: pass newlines, never markup.
An optional ``informative`` line is drawn under the text for a second thought.
"""

import logging
from enum import IntFlag
from typing import Dict, Optional, Sequence, Tuple

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.dialog_titlebar import DialogTitleBar
from utils.color_utils import get_best_foreground_color
from utils.settings import get_settings

logger = logging.getLogger(__name__)

# A severity picks the glyph and the colour that sit beside the message text.
_SEVERITIES = {
    "information": ("i", "#3b82f6"),
    "question": ("?", "#8b5cf6"),
    "warning": ("!", "#f59e0b"),
    "critical": ("!", "#ef4444"),
}

_SCROLL_AFTER_CHARS = 600  # longer messages get a scroll area instead of growing off-screen
_SCROLL_HEIGHT = 300


class StandardButton(IntFlag):
    """Same member names and values as QMessageBox.StandardButton."""

    NoButton = int(QMessageBox.StandardButton.NoButton.value)
    Ok = int(QMessageBox.StandardButton.Ok.value)
    Cancel = int(QMessageBox.StandardButton.Cancel.value)
    Yes = int(QMessageBox.StandardButton.Yes.value)
    No = int(QMessageBox.StandardButton.No.value)


class _SeverityIcon(QWidget):
    """A filled circle carrying the severity glyph - drawn, so it needs no asset."""

    def __init__(self, severity: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        glyph, colour = _SEVERITIES.get(severity, _SEVERITIES["information"])
        self._glyph = glyph
        self._colour = QColor(colour)
        self.setFixedSize(34, 34)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._colour)
        painter.drawEllipse(1, 1, self.width() - 2, self.height() - 2)
        painter.setPen(QPen(QColor(get_best_foreground_color(self._colour.name()))))
        font = QFont(self.font())
        font.setPixelSize(20)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 0, self.width(), self.height()),
            Qt.AlignmentFlag.AlignCenter,
            self._glyph,
        )
        painter.end()


class ThemedMessageBox(QDialog):
    """Frameless, theme-aware confirmation and alert dialog matching ASSella aesthetics."""

    StandardButton = StandardButton
    Ok = StandardButton.Ok
    Cancel = StandardButton.Cancel
    Yes = StandardButton.Yes
    No = StandardButton.No

    def __init__(
        self,
        title: str = "",
        text: str = "",
        buttons: StandardButton = StandardButton.Ok,
        default_button: Optional[StandardButton] = None,
        parent: Optional[QWidget] = None,
        severity: str = "information",
        informative: str = "",
    ):
        super().__init__(parent)
        self._result_value = None
        self._buttons: Dict[StandardButton, QPushButton] = {}

        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(400)
        self.setMaximumWidth(520)

        settings = get_settings()
        self.accent_color = (
            getattr(parent, "accent_color", None)
            or (settings.value("accent_color", "#a1c9fd") if settings else "#a1c9fd")
        )
        self.background_color = (
            getattr(parent, "background_color", None)
            or (settings.value("background_color", "#111318") if settings else "#111318")
        )

        self._build_ui(title, text, buttons, default_button, severity, informative)

    def _build_ui(
        self,
        title: str,
        text: str,
        buttons: StandardButton,
        default_button: Optional[StandardButton],
        severity: str,
        informative: str = "",
    ):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        # Outer themed container
        self.container = QFrame(self)
        self.container.setObjectName("dialogContainer")
        self.container.setStyleSheet(f"""
            QFrame#dialogContainer {{
                background-color: {self.background_color};
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }}
            QLabel {{
                color: #FFFFFF;
                border: none;
                background: transparent;
            }}
        """)

        container_lay = QVBoxLayout(self.container)
        container_lay.setContentsMargins(0, 0, 0, 16)
        container_lay.setSpacing(14)

        # Custom Themed Title Bar
        self.title_bar = DialogTitleBar(
            self,
            title=title,
            can_minimize=False,
            can_maximize=False,
            use_power_close=True,
        )
        container_lay.addWidget(self.title_bar)

        # Body area
        body_lay = QVBoxLayout()
        body_lay.setContentsMargins(20, 6, 20, 6)
        body_lay.setSpacing(14)

        # Message text, prefixed by the severity mark
        msg_lbl = QLabel(text)
        msg_lbl.setWordWrap(True)
        msg_lbl.setTextFormat(Qt.TextFormat.PlainText)
        msg_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        msg_lbl.setStyleSheet("""
            color: rgba(255, 255, 255, 0.90);
            font-size: 9.5pt;
            line-height: 1.45;
        """)

        text_row = QHBoxLayout()
        text_row.setSpacing(12)
        text_row.addWidget(_SeverityIcon(severity, self.container), 0, Qt.AlignmentFlag.AlignTop)
        if len(text) > _SCROLL_AFTER_CHARS:
            area = QScrollArea(self.container)
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setFixedHeight(_SCROLL_HEIGHT)
            area.setStyleSheet(
                "QScrollArea { background: transparent; border: none; }"
                " QScrollArea > QWidget > QWidget { background: transparent; }"
            )
            area.setWidget(msg_lbl)
            text_row.addWidget(area, 1)
        else:
            text_row.addWidget(msg_lbl, 1)
        body_lay.addLayout(text_row)

        if informative:
            info_lbl = QLabel(informative)
            info_lbl.setWordWrap(True)
            info_lbl.setTextFormat(Qt.TextFormat.PlainText)
            info_lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            info_lbl.setStyleSheet("""
                color: rgba(255, 255, 255, 0.62);
                font-size: 8.5pt;
            """)
            info_row = QHBoxLayout()
            info_row.setContentsMargins(46, 0, 0, 0)
            info_row.addWidget(info_lbl, 1)
            body_lay.addLayout(info_row)

        # Buttons row
        self._button_row = QHBoxLayout()
        self._button_row.setSpacing(10)
        self._button_row.addStretch()

        c = QColor(self.accent_color)
        fg_color = get_best_foreground_color(self.accent_color)

        primary_style = f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: {fg_color};
                border: none;
                border-radius: 6px;
                font-weight: 600;
                font-size: 9pt;
                padding: 6px 18px;
                min-width: 75px;
            }}
            QPushButton:hover {{
                background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.85);
            }}
            QPushButton:pressed {{
                background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.70);
            }}
        """

        secondary_style = """
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                color: rgba(255, 255, 255, 0.90);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 6px;
                font-weight: 500;
                font-size: 9pt;
                padding: 6px 18px;
                min-width: 75px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.14);
                border-color: rgba(255, 255, 255, 0.22);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.05);
            }
        """

        labels = {
            StandardButton.Ok: "OK",
            StandardButton.Yes: "Yes",
            StandardButton.No: "No",
            StandardButton.Cancel: "Cancel",
        }
        # Windows / Qt order: the affirmative choice leads.
        yes_is_secondary = bool(buttons & StandardButton.Yes) and bool(buttons & StandardButton.No)

        self._primary_style = primary_style
        self._secondary_style = secondary_style

        for standard in (StandardButton.Ok, StandardButton.Yes, StandardButton.No, StandardButton.Cancel):
            if not buttons & standard:
                continue
            is_primary = standard == StandardButton.Ok or (
                standard == StandardButton.Yes and not yes_is_secondary
            )
            self._buttons[standard] = self._add_button(labels[standard], standard, primary=is_primary)

        self._apply_default_button(default_button)

        body_lay.addLayout(self._button_row)
        container_lay.addLayout(body_lay)
        root.addWidget(self.container)

    def _add_button(self, label: str, value, *, primary: bool = False) -> QPushButton:
        """One themed button in the row; clicking it closes the dialog with ``value``."""
        btn = QPushButton(label)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(self._primary_style if primary else self._secondary_style)
        btn.setAutoDefault(False)
        btn.clicked.connect(lambda _checked=False, chosen=value: self._finish(chosen))
        self._button_row.addWidget(btn)
        return btn

    def _finish(self, value) -> None:
        """Record what was clicked and close; an affirmation accepts, anything else rejects."""
        self._result_value = value
        if value in (StandardButton.Ok, StandardButton.Yes):
            self.accept()
        else:
            self.reject()

    def _apply_default_button(self, default_button: Optional[StandardButton]) -> None:
        """Give the requested button the Enter key; fall back to Yes, then OK.

        QMessageBox leaves the question dialog with no default at all, which leaves
        Enter doing nothing; an explicit choice is honoured exactly as given.
        """
        chosen = default_button if default_button in self._buttons else None
        if chosen is None:
            for candidate in (StandardButton.Yes, StandardButton.Ok):
                if candidate in self._buttons:
                    chosen = candidate
                    break
        if chosen is not None:
            self._buttons[chosen].setDefault(True)
            self._buttons[chosen].setFocus()

    @classmethod
    def choices(
        cls,
        parent: Optional[QWidget],
        title: str,
        text: str,
        buttons: Sequence[Tuple[str, object]],
        *,
        default: object = None,
        severity: str = "information",
        informative: str = "",
    ) -> object:
        """A themed dialog with its own buttons; returns the value that was clicked.

        ``buttons`` is ``[(label, value), ...]`` in the order they should read.
        The value equal to ``default`` (or the first one) carries the focus and
        the accent colour. Closing the window without a choice returns None.
        """
        logger.debug("Themed message box (choices): %s", title)
        dlg = cls(
            title=title,
            text=text,
            buttons=StandardButton.NoButton,
            default_button=None,
            parent=parent,
            severity=severity,
            informative=informative,
        )
        focus = None
        for index, (label, value) in enumerate(buttons):
            primary = value == default if default is not None else index == 0
            btn = dlg._add_button(label, value, primary=primary)
            if primary:
                focus = btn
        if focus is not None:
            focus.setDefault(True)
            focus.setFocus()
        dlg.exec()
        return dlg._result_value

    @classmethod
    def _run(
        cls,
        severity: str,
        parent: Optional[QWidget],
        title: str,
        text: str,
        buttons: StandardButton,
        default_button: Optional[StandardButton],
        informative: str = "",
    ) -> StandardButton:
        logger.debug("Themed message box (%s): %s", severity, title)
        dlg = cls(
            title=title,
            text=text,
            buttons=buttons,
            default_button=default_button,
            parent=parent,
            severity=severity,
            informative=informative,
        )
        dlg.exec()
        return dlg._result_value

    @classmethod
    def information(
        cls,
        parent: Optional[QWidget],
        title: str,
        text: str,
        buttons: StandardButton = StandardButton.Ok,
        default_button: Optional[StandardButton] = None,
        informative: str = "",
    ) -> StandardButton:
        return cls._run("information", parent, title, text, buttons, default_button, informative)

    @classmethod
    def question(
        cls,
        parent: Optional[QWidget],
        title: str,
        text: str,
        buttons: StandardButton = StandardButton.Yes | StandardButton.No,
        default_button: Optional[StandardButton] = None,
        informative: str = "",
    ) -> StandardButton:
        return cls._run("question", parent, title, text, buttons, default_button, informative)

    @classmethod
    def warning(
        cls,
        parent: Optional[QWidget],
        title: str,
        text: str,
        buttons: StandardButton = StandardButton.Ok,
        default_button: Optional[StandardButton] = None,
        informative: str = "",
    ) -> StandardButton:
        return cls._run("warning", parent, title, text, buttons, default_button, informative)

    @classmethod
    def critical(
        cls,
        parent: Optional[QWidget],
        title: str,
        text: str,
        buttons: StandardButton = StandardButton.Ok,
        default_button: Optional[StandardButton] = None,
        informative: str = "",
    ) -> StandardButton:
        return cls._run("critical", parent, title, text, buttons, default_button, informative)
