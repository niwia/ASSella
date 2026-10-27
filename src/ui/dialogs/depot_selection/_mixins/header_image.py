"""Thumbnail fetching for the header, backed by :mod:`utils.image_fetcher`."""

import logging

from PyQt6.QtCore import Qt, QThread, pyqtSignal

from PyQt6.QtGui import QPixmap

from utils.image_fetcher import ImageFetcher

logger = logging.getLogger(__name__)

_LIVE_HEADER_THREADS: set = set()


class _HeaderApiFallbackThread(QThread):
    """Fetch header URL from Steam Store API in the background without blocking the UI thread."""
    url_resolved = pyqtSignal(str, str)

    def __init__(self, app_id: str):
        super().__init__()
        self._app_id = str(app_id)

    def run(self) -> None:
        try:
            api_url = ImageFetcher.fetch_header_from_web_api(self._app_id)
            self.url_resolved.emit(self._app_id, api_url or "")
        except Exception as e:
            logger.debug(f"Async header API fetch failed for {self._app_id}: {e}")
            self.url_resolved.emit(self._app_id, "")


class HeaderImageMixin:
    """Thumbnail fetching for the header."""

    def _fetch_header_image(self, app_id):
        self._current_app_id = app_id
        url = ImageFetcher.get_header_image_url(app_id)
        self.fetcher = ImageFetcher(url, ephemeral=True)
        self.fetcher.finished.connect(self.on_image_fetched)
        self.fetcher.finished.connect(self._cleanup_fetcher)
        self.fetcher.start()

    def on_image_fetched(self, image_data):
        try:
            if image_data:
                pixmap = QPixmap()
                if pixmap.loadFromData(image_data) and not pixmap.isNull():
                    self._apply_header_pixmap(pixmap)
                else:
                    self._trigger_header_refresh()
            else:
                # Image fetch failed (404), try to get the correct URL from Steam API
                logger.debug("Image fetch failed, attempting to refresh from API")
                self._trigger_header_refresh()
        except Exception as e:
            logger.debug(f"Error handling fetched image: {e}")
            self._show_no_image()

    def _trigger_header_refresh(self):
        """
        Fetch the correct header URL from Steam API asynchronously without blocking GUI.
        """
        app_id = getattr(self, "_current_app_id", None)
        if not app_id:
            self._show_no_image()
            return

        logger.debug(f"Fetching header URL from Steam API asynchronously for appid {app_id}")
        worker = _HeaderApiFallbackThread(str(app_id))
        _LIVE_HEADER_THREADS.add(worker)
        worker.url_resolved.connect(self._on_header_api_resolved)
        worker.finished.connect(lambda w=worker: _LIVE_HEADER_THREADS.discard(w))
        worker.start()

    def _on_header_api_resolved(self, app_id: str, api_url: str):
        """Handle async header URL lookup response from Steam Store API."""
        current_id = getattr(self, "_current_app_id", None)
        if str(current_id) != str(app_id):
            return

        if not api_url:
            logger.debug(f"No header URL found in API for appid {app_id}")
            self._show_no_image()
            return

        logger.info(f"Got header URL from API for appid {app_id}: {api_url}")
        try:
            from managers.db_manager import DatabaseManager

            db = DatabaseManager()
            db.upsert_app_info(app_id, {"header_url": api_url})
        except Exception as e:
            logger.debug(f"Could not update DB: {e}")

        # Re-fetch the image with the correct URL
        self.retry_fetcher = ImageFetcher(api_url)
        self.retry_fetcher.finished.connect(self._on_retry_image_fetched)
        self.retry_fetcher.finished.connect(self._cleanup_retry_fetcher)
        self.retry_fetcher.start()

    def _on_retry_image_fetched(self, image_data):
        """Handle the retry image fetch result."""
        try:
            if image_data:
                pixmap = QPixmap()
                if pixmap.loadFromData(image_data) and not pixmap.isNull():
                    self._apply_header_pixmap(pixmap)
                    logger.info("Successfully loaded header image after refresh")
                else:
                    self._show_no_image()
            else:
                self._show_no_image()
        except Exception as e:
            logger.debug(f"Error handling retry fetched image: {e}")
            self._show_no_image()

    def _apply_header_pixmap(self, pixmap: QPixmap) -> None:
        try:
            if not hasattr(self, "header_label") or self.header_label is None or pixmap.isNull():
                self._show_no_image()
                return
            target_w = self.header_label.width() if self.header_label.width() > 0 else 184
            target_h = self.header_label.height() if self.header_label.height() > 0 else 86
            scaled = pixmap.scaled(
                target_w, target_h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
            self.header_label.setPixmap(scaled)
            self.header_label.setStyleSheet("border-radius: 6px;")

            # Extract vibrant thumbnail signature color and update dynamic theme
            from utils.color_utils import extract_dominant_color
            app_id = getattr(self, "app_id", None) or getattr(self, "_current_app_id", None)
            dom_col = extract_dominant_color(pixmap, app_id=str(app_id) if app_id else None)
            if dom_col and dom_col.isValid():
                if hasattr(self, "_apply_dynamic_theme"):
                    self._apply_dynamic_theme(dom_col)
                else:
                    self._ambient_color = dom_col
                    self.update()
        except Exception as e:
            logger.debug(f"Error applying header pixmap: {e}")
            self._show_no_image()

    def _show_no_image(self):
        """Show fallback text when image is not available."""
        try:
            if hasattr(self, "header_label") and self.header_label is not None:
                self.header_label.setText("No Image")
                self.header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.header_label.setStyleSheet(
                    "background-color: rgba(255, 255, 255, 0.039); "
                    "color: rgba(255, 255, 255, 0.314); "
                    "font-size: 8pt; "
                    "border-radius: 6px;"
                )
        except Exception:
            pass

    def _cleanup_fetcher(self, _data: bytes) -> None:
        if hasattr(self, "fetcher") and self.fetcher is not None:
            self.fetcher.deleteLater()
            self.fetcher = None

    def _cleanup_retry_fetcher(self, _data: bytes) -> None:
        if hasattr(self, "retry_fetcher") and self.retry_fetcher is not None:
            self.retry_fetcher.deleteLater()
            self.retry_fetcher = None
