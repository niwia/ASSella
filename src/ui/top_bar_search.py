"""Live game search embedded in the main window's title bar."""

import logging
from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtWidgets import (
    QFrame,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from utils.settings import get_settings
from utils.task_runner import TaskRunner

logger = logging.getLogger(__name__)


class _SearchLineEdit(QLineEdit):
    def __init__(self, parent, search):
        super().__init__(parent)
        self._search = search

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._search.panel.hide()
            self.clearFocus()
            return
        if event.key() == Qt.Key.Key_Down and self._search.panel.isVisible():
            if self._search.results.count() > 0:
                self._search.results.setFocus()
                if self._search.results.currentRow() < 0:
                    self._search.results.setCurrentRow(0)
                return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        QTimer.singleShot(150, self._search._hide_if_focus_left)


class _ResultsListWidget(QListWidget):
    def __init__(self, parent, search):
        super().__init__(parent)
        self._search = search

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._search.panel.hide()
            self._search.input.setFocus()
            return
        if event.key() == Qt.Key.Key_Up and self.currentRow() <= 0:
            self.setCurrentRow(-1)
            self._search.input.setFocus()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            item = self.currentItem()
            if item and item.data(Qt.ItemDataRole.UserRole):
                self._search._on_item_clicked(item)
                return
        super().keyPressEvent(event)


class TopBarSearch:
    """Own the title-bar field, debounced search and main-window results panel."""

    DEBOUNCE_MS = 400
    MAX_RESULTS = 8
    ITEM_HEIGHT = 48
    MAX_VISIBLE_ITEMS = 4.5

    def __init__(self, main_window, title_bar):
        self.main_window = main_window
        self.title_bar = title_bar
        self.generation = 0
        self.runner = TaskRunner(main_window)
        self.active_fetchers = {}
        self.pending_image_timers = []

        self.input = _SearchLineEdit(title_bar, self)
        self.input.setObjectName("mainGameSearch")
        self.input.setPlaceholderText("Search games…")
        self.input.setClearButtonEnabled(True)
        target_w = max(340, min(480, main_window.width() - 320))
        self.input.setFixedWidth(target_w)
        self.input.setFixedHeight(26)
        self.input.setToolTip("Search games by name or App ID")

        self.panel = QFrame(main_window)
        self.panel.setObjectName("mainGameSearchPanel")
        self.panel.setWindowFlags(Qt.WindowType.Widget)
        self.panel.hide()
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(6, 6, 6, 6)
        panel_layout.setSpacing(2)
        self.results = _ResultsListWidget(self.panel, self)
        self.results.setObjectName("mainGameSearchResults")
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.results.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.results.itemClicked.connect(self._on_item_clicked)
        panel_layout.addWidget(self.results)

        self.timer = QTimer(main_window)
        self.timer.setSingleShot(True)
        self.timer.setInterval(self.DEBOUNCE_MS)
        self.timer.timeout.connect(self._search)
        self.input.textChanged.connect(self._on_text_changed)
        self.input.returnPressed.connect(self._on_return_pressed)
        self._apply_style()

    def _apply_style(self):
        settings = get_settings()
        background = settings.value("background_color", "#000000")
        accent = settings.value("accent_color", "#C06C84")
        self.input.setStyleSheet(
            f"QLineEdit {{ background: rgba(255,255,255,0.06); color: white; "
            f"border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; padding: 2px 9px; }}"
            f"QLineEdit:focus {{ background: rgba(255,255,255,0.10); border-color: {accent}; }}"
        )
        self.panel.setStyleSheet(
            f"QFrame#mainGameSearchPanel {{ background: {background}; border: 1px solid {accent}; border-radius: 10px; }}"
            "QListWidget { background: transparent; border: none; outline: none; }"
            "QListWidget::item { border-radius: 6px; margin: 1px 0px; }"
            f"QListWidget::item:selected {{ background: rgba(255,255,255,0.10); }}"
            "QScrollBar:vertical { border: none; background: rgba(0,0,0,0.25); width: 4px; margin: 2px 0px; border-radius: 2px; }"
            f"QScrollBar::handle:vertical {{ background: {accent}; min-height: 20px; border-radius: 2px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        )

    def _on_text_changed(self, text):
        self.timer.stop()
        self.generation += 1
        self._stop_image_fetchers()
        self.results.clear()
        query = text.strip()
        if len(query) < 2:
            self.panel.hide()
            return
        self._show_status("Searching…")
        self._position_panel()
        self.panel.show()
        self.panel.raise_()
        self.timer.start()

    def _search(self):
        query = self.input.text().strip()
        if len(query) < 2:
            return
        generation = self.generation
        manager = getattr(self.main_window, "game_manager", None)
        if manager:
            games = manager.games
            local_games = list(games.values()) if isinstance(games, dict) else list(games)
        else:
            local_games = []
        self.runner.run(
            self._search_games,
            query,
            local_games,
            on_finished=lambda result: self._on_search_finished(result, generation),
            on_error=lambda error: self._on_search_error(error, generation),
        )

    @staticmethod
    def _search_games(query, local_games):
        """Search the API and merge exact-session library entries before ranking."""
        from core import morrenus_api
        from utils.search_ranking import (
            extract_app_id,
            extract_game_name,
            filter_and_rank_results,
        )

        response = morrenus_api.search_games(query)
        if isinstance(response, dict) and "error" in response:
            return response
        api_games = response.get("results", []) if isinstance(response, dict) else []
        filtered_local_games = []
        query_lower = query.casefold()
        for game in local_games:
            if not isinstance(game, dict):
                continue
            name = extract_game_name(game)
            app_id = extract_app_id(game)
            if app_id and (query_lower in name.casefold() or query == app_id):
                filtered_local_games.append(game)
        from utils.settings import get_settings
        blacklist = get_settings().value("filter_search_blacklist", False, type=bool)
        ranked = filter_and_rank_results([*filtered_local_games, *api_games], query, filter_blacklist=blacklist)
        return ranked

    def _on_search_finished(self, result, generation):
        if generation != self.generation:
            return
        if isinstance(result, dict) and "error" in result:
            self._show_status("Search unavailable")
            return
        games = result.get("results", []) if isinstance(result, dict) else []
        self.results.clear()
        if not games:
            self._show_status("No results found")
            return
        for index, game in enumerate(games[: self.MAX_RESULTS]):
            self._add_result(game, generation, index)
        visible_count = min(len(games), self.MAX_VISIBLE_ITEMS)
        self.results.setFixedHeight(int(visible_count * self.ITEM_HEIGHT))
        self._position_panel()
        self.panel.show()
        self.panel.raise_()

    def _on_search_error(self, _error, generation):
        if generation == self.generation:
            self._show_status("Search unavailable")

    def _show_status(self, text):
        self.results.clear()
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.results.addItem(item)
        self.results.setFixedHeight(36)

    def _add_result(self, game, generation, index):
        from utils.search_ranking import extract_app_id, extract_game_name
        from utils.image_fetcher import ImageFetcher
        from ui.widgets.search_item_widget import SearchItemWidget

        app_id = extract_app_id(game)
        name = extract_game_name(game) or f"App {app_id}"
        if not app_id:
            return
        manager = getattr(self.main_window, "game_manager", None)
        in_library = bool(manager and manager.get_game(app_id) is not None)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, (app_id, name))
        item.setSizeHint(QSize(0, self.ITEM_HEIGHT))
        self.results.addItem(item)
        widget = SearchItemWidget(
            name,
            app_id,
            in_library,
            is_cached=ImageFetcher.get_cache_path(app_id).exists(),
            parent=self.panel,
            compact=True,
        )
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.results.setItemWidget(item, widget)
        if index < 4:
            self._fetch_image(widget, app_id, generation)
        else:
            timer = QTimer(self.panel)
            timer.setSingleShot(True)
            timer.timeout.connect(
                lambda w=widget, aid=app_id, gen=generation, t=timer: self._delayed_image(w, aid, gen, t)
            )
            self.pending_image_timers.append(timer)
            # Match FetchManifestDialog's lazy capsule-fetch cadence.
            timer.start(min(3500, 200 + max(0, index - 4) * 60))

    def _delayed_image(self, widget, app_id, generation, timer):
        if timer in self.pending_image_timers:
            self.pending_image_timers.remove(timer)
        if generation == self.generation:
            self._fetch_image(widget, app_id, generation)

    def _fetch_image(self, widget, app_id, generation):
        from utils.image_fetcher import ImageFetcher

        url = ImageFetcher.get_header_image_url(app_id)
        fetcher = ImageFetcher(url, ephemeral=True, parent=self.panel)
        self.active_fetchers[app_id] = fetcher
        fetcher.finished.connect(
            lambda data, w=widget, aid=app_id, gen=generation, f=fetcher:
                self._on_image_ready(data, w, aid, gen, f)
        )
        fetcher.start()

    def _on_image_ready(self, data, widget, app_id, generation, fetcher):
        self.active_fetchers.pop(app_id, None)
        fetcher.deleteLater()
        if generation != self.generation or not data:
            return
        from PyQt6.QtGui import QPixmap
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        if not pixmap.isNull():
            widget.img_lbl.setPixmap(pixmap)

    def _stop_image_fetchers(self):
        for timer in self.pending_image_timers:
            timer.stop()
            timer.deleteLater()
        self.pending_image_timers.clear()
        for fetcher in self.active_fetchers.values():
            fetcher.stop()
            fetcher.deleteLater()
        self.active_fetchers.clear()

    def _on_item_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        app_id, name = data
        self.panel.hide()
        self.input.clearFocus()
        self.main_window.open_fetch_dialog(initial_query=app_id)

    def _on_return_pressed(self):
        item = self.results.currentItem()
        if item and item.data(Qt.ItemDataRole.UserRole):
            self._on_item_clicked(item)
            return
        query = self.input.text().strip()
        if query:
            self.panel.hide()
            self.main_window.open_fetch_dialog(initial_query=query)

    def _position_panel(self):
        if not self.panel or not self.input:
            return
        anchor = self.input.mapTo(self.main_window, self.input.rect().bottomLeft())
        # Width strictly matches the search input field width
        width = self.input.width()
        x = anchor.x()
        panel_h = self.panel.sizeHint().height()
        window_h = self.main_window.height()

        below_y = anchor.y() + 4
        room_below = window_h - below_y - 8
        # The title bar can be docked at the bottom of the window
        # (titlebar_position = "bottom", the default), where there is no room
        # under the field at all. Open the panel upwards in that case instead
        # of letting the window clip it down to a few pixels.
        if panel_h > room_below:
            above_y = anchor.y() - self.input.height() - 4 - panel_h
            if above_y >= 8:
                self.panel.setGeometry(x, above_y, width, panel_h)
                return
            panel_h = max(0, room_below)

        self.panel.setGeometry(x, below_y, width, panel_h)

    def reposition(self):
        target_w = max(340, min(480, self.main_window.width() - 320))
        if self.input.width() != target_w:
            self.input.setFixedWidth(target_w)
        if self.panel.isVisible():
            self._position_panel()

    def _hide_if_focus_left(self):
        from PyQt6.QtWidgets import QApplication
        focused = QApplication.focusWidget()
        if (
            focused is not self.input
            and focused is not self.results
            and not (focused and self.panel.isAncestorOf(focused))
        ):
            self.panel.hide()

    def close(self):
        self.generation += 1
        self.timer.stop()
        self._stop_image_fetchers()
        self.panel.hide()
