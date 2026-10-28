import logging
from PyQt6.QtCore import QObject, QEvent, QTimer

logger = logging.getLogger(__name__)

class DialogRaiser(QObject):
    """An event filter installed on parent window/dialog.
    When parent is activated/restored, raises the active child dialog to the top.
    """
    def __init__(self, parent_window, child_dialog):
        super().__init__(parent_window)
        self.child_dialog = child_dialog
        if parent_window:
            parent_window.installEventFilter(self)

    def eventFilter(self, obj, event):
        try:
            if event is not None and event.type() == QEvent.Type.WindowActivate:
                QTimer.singleShot(50, self._raise_child)
        except Exception as e:
            logger.debug(f"[DialogRaiser] event error: {e}")
        return False

    def _raise_child(self):
        try:
            if self.child_dialog and self.child_dialog.isVisible():
                self.child_dialog.raise_()
                self.child_dialog.activateWindow()
        except RuntimeError:
            pass
