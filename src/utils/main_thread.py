"""Post callables to the Qt main thread from any thread.

Rule (see game_manager._perform_scan comment): ``QTimer.singleShot`` created
on a worker thread never fires — it schedules on the worker's non-existent
event loop. Use :func:`post_to_main_thread` instead.

Delivery mechanics, verified empirically:
* A signal must be *connected* while the receiver lives on the main thread;
  connecting on a worker (even with an explicit QueuedConnection and a later
  moveToThread) delivers to the worker or nowhere. Hence the bridge below is
  built at import / on main-thread posts only — app startup imports always
  run on the main thread, so it exists before any worker can post.
* Main-thread posts go through ``QTimer.singleShot(0, ...)`` (valid there),
  preserving the old async replay semantics of LatchSignal.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_bridge = None


def _build_bridge():
    """Create + connect the bridge. Caller must hold _lock and be on main."""
    from PyQt6.QtCore import QObject, pyqtSignal

    class _MainThreadBridge(QObject):
        posted = pyqtSignal(object)

        def __init__(self):
            super().__init__()
            self.posted.connect(self._invoke)

        @staticmethod
        def _invoke(payload):
            fn, args, kwargs = payload
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception("[MainThread] Posted callable failed")

    global _bridge
    _bridge = _MainThreadBridge()
    return _bridge


def _ensure_bridge():
    """Ensure a main-affinity bridge exists. Only effective on main thread."""
    global _bridge
    if _bridge is not None:
        return _bridge
    if threading.current_thread() is not threading.main_thread():
        return None
    with _lock:
        if _bridge is None:
            try:
                _bridge = _build_bridge()
            except Exception:
                logger.exception("[MainThread] Bridge creation failed")
    return _bridge


try:
    # App startup imports run on the main thread: build eagerly so the bridge
    # (and its connection) carries main affinity before any worker can post.
    if threading.current_thread() is threading.main_thread():
        with _lock:
            _bridge = _build_bridge()
except Exception:
    logger.debug("[MainThread] Eager bridge creation deferred", exc_info=True)


def post_to_main_thread(fn, *args, **kwargs):
    """Run ``fn(*args, **kwargs)`` on the Qt main thread (async).

    Safe to call from any thread, including threads without an event loop.
    Falls back to a direct call with no QApplication (headless/tests).
    """
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer
        if QApplication.instance() is None:
            fn(*args, **kwargs)
            return
    except Exception:
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("[MainThread] Direct fallback call failed")
        return

    if threading.current_thread() is threading.main_thread():
        # Main thread has an event loop: plain singleShot is valid here and
        # keeps the historical async (deferred) delivery semantics.
        _ensure_bridge()
        try:
            QTimer.singleShot(0, lambda: fn(*args, **kwargs))
        except Exception:
            logger.exception("[MainThread] singleShot post failed")
        return

    bridge = _ensure_bridge()
    if bridge is None:
        # Unreachable in the app (import + main-thread use always build
        # first); last resort beats silently stalling the caller.
        logger.error("[MainThread] No bridge available; running directly (unsafe off-thread)")
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("[MainThread] Direct fallback call failed")
        return
    try:
        bridge.posted.emit((fn, args, kwargs))
    except Exception:
        logger.exception("[MainThread] Post failed")
