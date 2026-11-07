"""Settings -> Downloads -> Cached: games with cached info, one Clear button each.

Nothing is read until the tab is opened; the list is collected on a QThread
(utils/game_cache.py) and the rows are added in small batches so the dialog
never stalls.
"""

from __future__ import annotations

import logging

from PyQt6 import sip
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.sizes import size_control

logger = logging.getLogger(__name__)

_BATCH = 60
MUTED = "color: rgba(255,255,255,0.55); font-size: 8.5pt; border: none; background: transparent;"


class _Job(QThread):
    done = pyqtSignal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:
            logger.warning(f"[CachedTab] background job failed: {e}")
            self.done.emit(e)


def _start(owner, fn, on_done) -> None:
    job = _Job(fn, owner)
    job.done.connect(on_done)
    job.finished.connect(job.deleteLater)
    owner._cached_jobs = [j for j in getattr(owner, "_cached_jobs", []) if not sip.isdeleted(j)] + [job]
    job.start()


def create_cached_page(dialog) -> QWidget:
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(4, 8, 4, 4)
    lay.setSpacing(8)

    info = QLabel("Loading cached games…")
    info.setStyleSheet(MUTED)
    info.setWordWrap(True)
    lay.addWidget(info)

    lst = QListWidget()
    lst.setSelectionMode(QListWidget.SelectionMode.NoSelection)
    lst.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    lst.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
    lst.setStyleSheet(
        "QListWidget { background: transparent; border: none; }"
        "QListWidget::item { border-bottom: 1px solid rgba(255,255,255,0.06); }"
        "QListWidget::item:hover { background: rgba(255,255,255,0.04); }"
    )
    lay.addWidget(lst, 1)

    state = {"total": 0}

    def _update_info():
        n = lst.count()
        info.setText(
            f"{n} game{'s' if n != 1 else ''} with cached info. Clearing removes it everywhere ASSella "
            "keeps it; it is fetched again the next time you open the game. Installed and AT0-M games "
            "keep their decryption keys."
            if n else "No cached game info."
        )

    def _row(appid: str, name: str) -> None:
        item = QListWidgetItem()
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 3, 6, 3)
        lbl = QLabel(name)
        lbl.setToolTip(f"AppID {appid}")
        lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; border: none; background: transparent;")
        btn = QPushButton("Clear")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        size_control(btn, None)
        h.addWidget(lbl, 1)
        h.addWidget(btn)
        item.setSizeHint(w.sizeHint())
        lst.addItem(item)
        lst.setItemWidget(item, w)

        def _clear():
            btn.setEnabled(False)
            btn.setText("Clearing…")
            from utils.game_cache import clear_game_cache

            def _done(res):
                if sip.isdeleted(lst):
                    return
                if isinstance(res, Exception):
                    btn.setEnabled(True)
                    btn.setText("Clear")
                    btn.setToolTip(str(res))
                    return
                row = lst.row(item)
                if row >= 0:
                    lst.takeItem(row)
                _update_info()

            _start(page, lambda: clear_game_cache(appid), _done)

        btn.clicked.connect(_clear)

    def _fill(rows, start=0):
        if sip.isdeleted(lst):
            return
        lst.setUpdatesEnabled(False)
        for appid, name in rows[start:start + _BATCH]:
            _row(appid, name)
        lst.setUpdatesEnabled(True)
        if start + _BATCH < len(rows):
            QTimer.singleShot(0, lambda: _fill(rows, start + _BATCH))
        else:
            _update_info()

    def _loaded(res):
        if sip.isdeleted(info):
            return
        if isinstance(res, Exception):
            info.setText(f"Could not read the caches: {res}")
            return
        state["total"] = len(res)
        if not res:
            _update_info()
            return
        info.setText(f"Loading {len(res)} games…")
        _fill(res)

    from utils.game_cache import list_cached_games

    _start(page, list_cached_games, _loaded)
    return page
