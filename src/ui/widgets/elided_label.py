"""A one-line label that shortens its text instead of widening its window."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QSizePolicy


class ElidedLabel(QLabel):
    """A single-line QLabel that elides with "..." and keeps the full text.

    A plain QLabel reports its whole text as ``minimumSizeHint()``. When such a
    label sits in a row that stretches across the window, a long string raises
    the window's minimum width - the download speed line made the frameless
    window grow for long game names. ``Ignored`` horizontal policy plus a zero
    minimum width removes that pressure, and the text is elided to whatever
    space the layout actually grants. The untruncated text stays available as
    ``full_text`` and in the tooltip.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setText(text)

    @property
    def full_text(self) -> str:
        return self._full_text

    def setText(self, text: str):  # noqa: N802 - Qt naming
        self._full_text = text or ""
        self.setToolTip(self._full_text)
        self._update_elided()

    def resizeEvent(self, event):  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._update_elided()

    def _update_elided(self) -> None:
        width = self.width()
        if width <= 0:
            super().setText(self._full_text)
            return
        elided = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, width
        )
        super().setText(elided)
