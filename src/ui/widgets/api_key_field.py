"""API key input that shows the start of the key and masks the rest.

Masked:   ``ab12c••••••••••••••••`` (first 5 characters stay visible)
Focused / revealed: the full key, editable.

``text()`` / ``setText()`` always deal in the real key, so code that reads
the field (settings save, sync) never sees the masked display string.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLineEdit

VISIBLE_PREFIX = 5
MASK_CHAR = "•"


def mask_key(key: str) -> str:
    key = key or ""
    if len(key) <= VISIBLE_PREFIX:
        return key
    # One dot per hidden character, so the masked key is as long as the real one.
    return key[:VISIBLE_PREFIX] + MASK_CHAR * (len(key) - VISIBLE_PREFIX)


class ApiKeyField(QLineEdit):
    """Left-aligned key field; emits ``keyChanged`` with the real, stripped key."""

    keyChanged = pyqtSignal(str)

    def __init__(self, parent=None, placeholder: str = "", accent: str = "#a1c9fd"):
        super().__init__(parent)
        self._key = ""
        self._revealed = False
        self.setPlaceholderText(placeholder)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setEchoMode(QLineEdit.EchoMode.Normal)
        self.setMinimumHeight(32)
        self.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.14);"
            " border-radius: 7px; padding: 4px 10px; color: #FFFFFF; font-size: 9.5pt;"
            " font-family: 'JetBrains Mono','DejaVu Sans Mono',monospace; }"
            f"QLineEdit:focus {{ border-color: {accent}; }}"
        )
        super().textEdited.connect(self._on_edited)

    # -- real value ------------------------------------------------------
    def key(self) -> str:
        return self._key

    def text(self) -> str:  # noqa: D401 - Qt override, returns the real key
        return self._key

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        self._key = (text or "").strip()
        self._render()

    def setRevealed(self, revealed: bool) -> None:  # noqa: N802
        self._revealed = bool(revealed)
        self._render()

    def isRevealed(self) -> bool:  # noqa: N802
        return self._revealed

    # -- display ---------------------------------------------------------
    def _render(self) -> None:
        shown = self._key if (self._revealed or self.hasFocus()) else mask_key(self._key)
        if super().text() != shown:
            self.blockSignals(True)
            super().setText(shown)
            self.blockSignals(False)
        self.setCursorPosition(0 if not self.hasFocus() else len(shown))

    def _on_edited(self, raw: str) -> None:
        new = raw.strip()
        if new != self._key:
            self._key = new
            self.keyChanged.emit(new)

    def focusInEvent(self, event):  # noqa: N802
        super().focusInEvent(event)
        self._render()

    def focusOutEvent(self, event):  # noqa: N802
        super().focusOutEvent(event)
        self._render()
