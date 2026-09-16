import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QListWidgetItem

from ui.dialogs.library.widgets import GameItemWidget

try:
    from managers.image_fetcher import ImageFetcher
except ImportError:
    try:
        from utils.image_fetcher import ImageFetcher
    except ImportError:
        ImageFetcher = None

try:
    from managers.db_manager import DatabaseManager
except ImportError:
    DatabaseManager = None

logger = logging.getLogger(__name__)


class LibraryImageLoaderMixin:
    """Mixin for asynchronous game cover art fetching, queueing, and caching."""

    def _start_pending_image_fetches(self) -> None:
        """Sequential start of delayed image fetches."""
        if self._closing or not hasattr(self, "_pending_image_fetches"):
            return
        for item, app_id in self._pending_image_fetches:
            self._fetch_item_image(item, app_id)
        self._pending_image_fetches.clear()

    def _fetch_item_image(self, _item: QListWidgetItem, app_id: str) -> None:
        if not ImageFetcher:
            return
        if app_id in self._active_fetchers or app_id in self._image_fetch_queue:
            return

        self._image_fetch_queue.append(app_id)
        self._process_fetch_queue()

    def _process_fetch_queue(self) -> None:
        if self._closing or self._current_fetches >= self._max_concurrent_fetches:
            return
        if not self._image_fetch_queue:
            return

        app_id = self._image_fetch_queue.popleft()
        self._current_fetches += 1
        
        QTimer.singleShot(0, lambda: self._do_fetch_image(app_id))

    def _do_fetch_image(self, app_id: str) -> None:
        if self._closing:
            return
            
        url = ImageFetcher.get_header_image_url(app_id)
        if not url:
            self._cleanup_fetcher(app_id)
            return

        fetcher = ImageFetcher(url)
        fetcher.setProperty("app_id", app_id)
        self._active_fetchers[app_id] = fetcher

        fetcher.finished.connect(self._on_item_image_fetched)
        fetcher.finished.connect(lambda _, aid=app_id: self._cleanup_fetcher(aid))
        fetcher.start()

    def _cleanup_fetcher(self, app_id: str) -> None:
        if app_id in self._active_fetchers:
            del self._active_fetchers[app_id]
        self._current_fetches = max(0, self._current_fetches - 1)
        self._process_fetch_queue()

    def _on_item_image_fetched(self, image_data: bytes) -> None:
        if self._closing or not self.isVisible():
            return

        sender = self.sender()
        app_id = sender.property("app_id") if sender else None
        if not app_id:
            return

        if not image_data:
            # If image fetch failed, trigger a background refresh of the URL
            QTimer.singleShot(0, lambda: self._trigger_header_refresh(app_id))
            return

        self._image_cache[app_id] = image_data

        # Find item and widget using O(1) lookup index
        item = self._items_by_appid.get(app_id)
        if item:
            self._update_item_image_if_match(item, app_id, image_data)

    def _check_appid_match(self, data: dict, app_id: str) -> bool:
        """Helper to check if a game's AppID matches the target AppID."""
        game_appid = str(data.get("appid", "0"))
        if game_appid == app_id:
            return True
        if game_appid in ("0", "N/A", "unknown"):
            resolved = self._resolve_appid_by_name(data.get("game_name"))
            return resolved == app_id
        return False

    def _update_item_image_if_match(self, item: QListWidgetItem, app_id: str, image_data: bytes) -> None:
        """Helper to check if list item matches app_id and update image."""
        data = item.data(Qt.ItemDataRole.UserRole)
        if self._check_appid_match(data, app_id):
            widget = self.games_list.itemWidget(item)
            if isinstance(widget, GameItemWidget):
                pixmap = QPixmap()
                pixmap.loadFromData(image_data)
                widget.set_image(pixmap)

    def _trigger_header_refresh(self, app_id: str) -> None:
        """Trigger background refresh of header URL from API."""

        def fetch_and_update():
            try:
                from utils.image_fetcher import ImageFetcher
                return ImageFetcher.fetch_header_from_web_api(app_id)
            except Exception as e:
                logger.warning(f"Header refresh failed for {app_id}: {e}")
            return None

        def on_complete(future_result):
            try:
                url = future_result.result()
                if url and not self._closing:
                    QTimer.singleShot(
                        0, lambda: self._apply_header_refresh(app_id, url)
                    )
            except RuntimeError:
                pass

        future = self.executor.submit(fetch_and_update)
        future.add_done_callback(on_complete)

    def _apply_header_refresh(self, app_id: str, api_url: str) -> None:
        """Update DB and retry fetch with new URL."""
        if self._closing or not self.isVisible():
            return

        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            db.upsert_app_info(app_id, {"header_url": api_url})

            # Retry fetch
            if app_id not in self._active_fetchers and ImageFetcher:
                fetcher = ImageFetcher(api_url)
                fetcher.setProperty("app_id", app_id)
                self._active_fetchers[app_id] = fetcher
                fetcher.finished.connect(self._on_item_image_fetched)
                fetcher.finished.connect(
                    lambda _, aid=app_id: self._cleanup_fetcher(aid)
                )
                fetcher.start()
        except RuntimeError as e:
            logger.warning(f"Failed to apply header refresh: {e}")
