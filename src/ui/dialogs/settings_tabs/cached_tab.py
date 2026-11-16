"""Settings -> Downloads -> Cached: games with cached info, one Clear button each.

Nothing is read until the tab is opened; the list is collected on a QThread
(utils/game_cache.py) and the rows are added via on-demand windowed pagination
(INITIAL_BATCH = 25) so the dialog never stalls or consumes high CPU.
"""

from __future__ import annotations

import logging
import threading
from typing import List, Optional, Tuple

from PyQt6 import sip
from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.sizes import size_control

logger = logging.getLogger(__name__)

INITIAL_BATCH = 25
BATCH_CHUNK = 25
_ROW_H = 46  # fits the standard control height plus padding
MUTED = "color: rgba(255,255,255,0.55); font-size: 8.5pt; border: none; background: transparent;"


class _Job(QThread):
    done = pyqtSignal(object)

    def __init__(self, fn, parent=None, cancel_token: Optional[threading.Event] = None):
        super().__init__(parent)
        self._fn = fn
        self._cancel_token = cancel_token

    def run(self):
        try:
            if self._cancel_token and self._cancel_token.is_set():
                return
            res = self._fn()
            if self._cancel_token and self._cancel_token.is_set():
                return
            self.done.emit(res)
        except Exception as e:
            if self._cancel_token and self._cancel_token.is_set():
                return
            logger.warning(f"[CachedTab] background job failed: {e}")
            self.done.emit(e)


def _start(owner, fn, on_done, cancel_token: Optional[threading.Event] = None) -> _Job:
    job = _Job(fn, owner, cancel_token=cancel_token)
    job.done.connect(on_done)
    job.finished.connect(job.deleteLater)
    owner._cached_jobs = [j for j in getattr(owner, "_cached_jobs", []) if not sip.isdeleted(j)] + [job]
    if hasattr(owner, "_job"):
        owner._job = job
    job.start()
    return job


class CachedTabPage(QWidget):
    """Cached tab widget with windowed on-demand pagination and cancellation on tab switch/hide."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._cancel_token = threading.Event()
        self._all_rows: List[Tuple[str, str]] = []
        self._rendered_count = 0
        self._job: Optional[_Job] = None
        self._is_loaded = False
        self._init_ui()

    def _init_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.setSpacing(8)

        self.info = QLabel("Loading cached games…")
        self.info.setStyleSheet(MUTED)
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        self.lst = QListWidget()
        self.lst.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.lst.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lst.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.lst.setStyleSheet(
            "QListWidget { background-color: rgba(18, 20, 26, 0.70); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 8px; padding: 4px; }"
            "QListWidget::item { padding: 0; border-bottom: 1px solid rgba(255, 255, 255, 0.06); }"
            "QListWidget::item:hover { background: rgba(255, 255, 255, 0.06); border-radius: 4px; }"
        )
        lay.addWidget(self.lst, 1)

        # Hook scroll bar for windowed on-demand pagination
        self.lst.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def _update_info(self) -> None:
        if sip.isdeleted(self.info):
            return
        n = len(self._all_rows)
        self.info.setText(
            f"{n} game{'s' if n != 1 else ''} with cached info. Clearing removes it everywhere ASSella "
            "keeps it; it is fetched again the next time you open the game. Installed and AT0-M games "
            "keep their decryption keys."
            if n else "No cached game info."
        )

    def _row(self, appid: str, name: str) -> None:
        if sip.isdeleted(self.lst):
            return
        item = QListWidgetItem()
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 0, 8, 0)
        w.setFixedHeight(_ROW_H)
        lbl = QLabel(name)
        lbl.setToolTip(f"AppID {appid}")
        lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; border: none; background: transparent;")
        btn = QPushButton("Clear")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        size_control(btn, None)
        h.addWidget(lbl, 1)
        h.addWidget(btn)
        lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        btn.setFixedWidth(84)
        item.setSizeHint(QSize(0, _ROW_H))
        self.lst.addItem(item)
        self.lst.setItemWidget(item, w)

        def _clear():
            btn.setEnabled(False)
            btn.setText("Clearing…")
            from utils.game_cache import clear_game_cache

            def _done(res):
                if sip.isdeleted(self.lst):
                    return
                if isinstance(res, Exception):
                    btn.setEnabled(True)
                    btn.setText("Clear")
                    btn.setToolTip(str(res))
                    return
                row = self.lst.row(item)
                if row >= 0:
                    self.lst.takeItem(row)
                    self._rendered_count = max(0, self._rendered_count - 1)
                self._all_rows = [(a, n) for a, n in self._all_rows if a != appid]
                self._update_info()

            _start(self, lambda: clear_game_cache(appid), _done)

        btn.clicked.connect(_clear)

    def _render_next_batch(self, count: int = 25) -> None:
        if sip.isdeleted(self.lst) or self._cancel_token.is_set():
            return
        if self._rendered_count >= len(self._all_rows):
            return
        self.lst.setUpdatesEnabled(False)
        end = min(self._rendered_count + count, len(self._all_rows))
        for appid, name in self._all_rows[self._rendered_count:end]:
            self._row(appid, name)
        self._rendered_count = end
        self.lst.setUpdatesEnabled(True)

    def _on_scroll(self, value: int) -> None:
        if self._cancel_token.is_set() or sip.isdeleted(self.lst):
            return
        sb = self.lst.verticalScrollBar()
        if sb.maximum() > 0 and value >= sb.maximum() - 150:
            if self._rendered_count < len(self._all_rows):
                self._render_next_batch(BATCH_CHUNK)

    def _start_loading(self) -> None:
        if self._is_loaded:
            return
        self._cancel_token.clear()
        self.info.setText("Loading cached games…")

        def _loaded(res):
            if sip.isdeleted(self) or self._cancel_token.is_set():
                return
            if isinstance(res, Exception):
                self.info.setText(f"Could not read the caches: {res}")
                return
            self._is_loaded = True
            self._all_rows = res or []
            self._rendered_count = 0
            if sip.isdeleted(self.lst):
                return
            self.lst.clear()
            self._update_info()
            if self._all_rows:
                self._render_next_batch(INITIAL_BATCH)

        from utils.game_cache import list_cached_games
        _start(self, lambda: list_cached_games(self._cancel_token), _loaded, cancel_token=self._cancel_token)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._cancel_token.clear()
        if not self._is_loaded:
            self._start_loading()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._cancel_token.set()
        if self._job and not sip.isdeleted(self._job) and self._job.isRunning():
            self._job.quit()


def create_cached_page(dialog) -> QWidget:
    page = CachedTabPage(dialog)
    return page
