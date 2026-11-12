import logging
from typing import List, Optional

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QTimer, Qt
from PyQt6.QtWidgets import QApplication, QDialog, QWidget

logger = logging.getLogger(__name__)


def _is_usable_window(widget: Optional[QWidget]) -> bool:
    """Return True when widget is a live, visible top-level window/dialog."""
    if widget is None or sip.isdeleted(widget):
        return False
    if not widget.isVisible() or not widget.isWindow():
        return False
    if widget.windowType() == Qt.WindowType.SubWindow:
        return False
    if widget.objectName() == "PixelLoadingOverlay":
        return False
    return True


def _is_descendant_window(candidate: QWidget, ancestor: QWidget) -> bool:
    """Return True if candidate walks up via parent() to ancestor."""
    cur = candidate
    seen = set()
    while cur is not None and not sip.isdeleted(cur):
        obj_id = id(cur)
        if obj_id in seen:
            break
        seen.add(obj_id)
        if cur is ancestor:
            return True
        cur = cur.parent()
    return False


def _visible_dialog_chain(root_dialog: Optional[QWidget]) -> List[QWidget]:
    """Return [root_dialog, ..., leaf_dialog] for visible child QDialogs."""
    if not _is_usable_window(root_dialog):
        return []

    chain: List[QWidget] = [root_dialog]
    seen = {id(root_dialog)}
    app = QApplication.instance()
    cur: QWidget = root_dialog

    while True:
        # Direct visible child QDialogs owned by cur
        candidates = [
            w
            for w in cur.findChildren(QDialog, options=Qt.FindChildOption.FindDirectChildrenOnly)
            if id(w) not in seen and _is_usable_window(w)
        ]
        # Also catch any top-level modal widget parented directly to cur
        if app is not None:
            modal = app.activeModalWidget()
            if (
                modal is not None
                and id(modal) not in seen
                and _is_usable_window(modal)
                and modal.parent() is cur
                and modal not in candidates
            ):
                candidates.append(modal)

        if not candidates:
            break

        # Prefer a modal child, otherwise the most recently constructed/shown child
        modal_candidates = [w for w in candidates if w.isModal()]
        nxt = modal_candidates[-1] if modal_candidates else candidates[-1]
        seen.add(id(nxt))
        chain.append(nxt)
        cur = nxt

    return chain


def find_topmost_child_dialog(root_widget: Optional[QWidget]) -> Optional[QWidget]:
    """Return the deepest visible child/modal dialog under root_widget, if any."""
    chain = _visible_dialog_chain(root_widget)
    if not chain:
        return None
    return chain[-1]


def resolve_dialog_parent(fallback: Optional[QWidget] = None) -> Optional[QWidget]:
    """Resolve the currently active ASSella window/dialog for parenting new modals."""
    app = QApplication.instance()
    if app is not None:
        modal = app.activeModalWidget()
        if _is_usable_window(modal):
            return find_topmost_child_dialog(modal) or modal

    if fallback is not None and not sip.isdeleted(fallback):
        deepest = find_topmost_child_dialog(fallback)
        if deepest is not None and deepest is not fallback:
            return deepest

    if app is not None:
        active = app.activeWindow()
        if _is_usable_window(active):
            return find_topmost_child_dialog(active) or active

        visible_modals = [
            w
            for w in app.topLevelWidgets()
            if isinstance(w, QDialog) and w.isModal() and _is_usable_window(w)
        ]
        if fallback is not None and not sip.isdeleted(fallback):
            anchored = [w for w in visible_modals if _is_descendant_window(w, fallback)]
            if anchored:
                return find_topmost_child_dialog(anchored[-1]) or anchored[-1]
        if visible_modals:
            return find_topmost_child_dialog(visible_modals[-1]) or visible_modals[-1]

    if fallback is not None and not sip.isdeleted(fallback):
        return fallback
    return None


def focus_dialog_on_show(dialog: QWidget) -> None:
    """Raise a freshly shown dialog above ASSella windows without stealing external OS focus."""
    if dialog is None or sip.isdeleted(dialog) or not dialog.isVisible():
        return
    dialog.raise_()
    app = QApplication.instance()
    if app is None or app.applicationState() == Qt.ApplicationState.ApplicationActive:
        dialog.activateWindow()


class DialogRaiser(QObject):
    """Event filter installed on parent window/dialog.

    When the parent receives WindowActivate (e.g. via Alt-Tab or clicking an
    obscured parent), raises the visible child/modal stack in order and
    activates the leaf dialog so it cannot get trapped underneath.
    """

    def __init__(self, parent_window: Optional[QWidget], child_dialog: Optional[QWidget]):
        target_parent = parent_window.window() if isinstance(parent_window, QWidget) else parent_window
        super().__init__(target_parent)
        self.parent_window = target_parent
        self.child_dialog = child_dialog
        self._installed = False
        self._cleaned = False

        if (
            target_parent is None
            or child_dialog is None
            or target_parent is child_dialog
            or sip.isdeleted(target_parent)
            or sip.isdeleted(child_dialog)
        ):
            return

        existing = [
            r
            for r in target_parent.findChildren(
                DialogRaiser, options=Qt.FindChildOption.FindDirectChildrenOnly
            )
            if r is not self and not sip.isdeleted(r) and r.child_dialog is child_dialog and not r._cleaned
        ]
        if existing:
            return

        target_parent.installEventFilter(self)
        self._installed = True
        child_dialog.destroyed.connect(self._cleanup)

    @classmethod
    def attach(
        cls, parent_window: Optional[QWidget], child_dialog: Optional[QWidget]
    ) -> Optional["DialogRaiser"]:
        """Idempotently ensure parent_window raises child_dialog on activation."""
        target_parent = parent_window.window() if isinstance(parent_window, QWidget) else parent_window
        if (
            target_parent is None
            or child_dialog is None
            or target_parent is child_dialog
            or sip.isdeleted(target_parent)
            or sip.isdeleted(child_dialog)
        ):
            return None
        for existing in target_parent.findChildren(
            cls, options=Qt.FindChildOption.FindDirectChildrenOnly
        ):
            if not sip.isdeleted(existing) and existing.child_dialog is child_dialog and not existing._cleaned:
                return existing
        return cls(target_parent, child_dialog)

    def _cleanup(self, _obj=None) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        parent_win = self.parent_window
        self.child_dialog = None
        if self._installed and parent_win is not None and not sip.isdeleted(parent_win):
            try:
                parent_win.removeEventFilter(self)
            except (RuntimeError, TypeError) as exc:
                logger.debug("[DialogRaiser] removeEventFilter failed: %s", exc)
        self._installed = False
        if not sip.isdeleted(self):
            self.deleteLater()

    def eventFilter(self, obj, event):
        try:
            if event is not None and event.type() == QEvent.Type.WindowActivate:
                QTimer.singleShot(25, self._raise_child)
        except Exception as e:
            logger.debug(f"[DialogRaiser] event error: {e}")
        return False

    def _raise_child(self):
        try:
            if self._cleaned or self.child_dialog is None or sip.isdeleted(self.child_dialog):
                return
            chain = _visible_dialog_chain(self.child_dialog)
            if not chain:
                return
            for dlg in chain:
                if not sip.isdeleted(dlg) and dlg.isVisible():
                    dlg.raise_()
            leaf = chain[-1]
            if not sip.isdeleted(leaf) and leaf.isVisible():
                leaf.activateWindow()
        except RuntimeError as exc:
            logger.debug(f"[DialogRaiser] raise error: {exc}")
