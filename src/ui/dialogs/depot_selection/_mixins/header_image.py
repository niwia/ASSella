"""Thumbnail fetching for the header, backed by :mod:`utils.image_fetcher`."""

import logging

from PyQt6.QtCore import Qt

from PyQt6.QtGui import QPixmap

from utils.image_fetcher import ImageFetcher

logger = logging.getLogger(__name__)


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
        if image_data:
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            self._apply_header_pixmap(pixmap)
        else:
            # Image fetch failed (404), try to get the correct URL from Steam API
            logger.debug("Image fetch failed, attempting to refresh from API")
            self._trigger_header_refresh()

    def _trigger_header_refresh(self):
        """
        Fetch the correct header URL from Steam API when generic URL fails.
        """
        app_id = getattr(self, "_current_app_id", None)
        if not app_id:
            self._show_no_image()
            return

        logger.debug(f"Fetching header URL from Steam API for appid {app_id}")

        try:
            # Fetch the correct URL from Steam API (synchronous but fast)
            api_url = ImageFetcher.fetch_header_from_web_api(app_id)

            if api_url:
                logger.info(f"Got header URL from API for appid {app_id}: {api_url}")

                # Update database with fresh URL
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
            else:
                logger.debug(f"No header URL found in API for appid {app_id}")
                self._show_no_image()
        except Exception as e:
            logger.warning(f"Failed to refresh header for appid {app_id}: {e}")
            self._show_no_image()

    def _on_retry_image_fetched(self, image_data):
        """Handle the retry image fetch result."""
        if image_data:
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            self._apply_header_pixmap(pixmap)
            logger.info("Successfully loaded header image after refresh")
        else:
            self._show_no_image()

    def _apply_header_pixmap(self, pixmap: QPixmap) -> None:
        target_w = self.header_label.width() if hasattr(self, "header_label") and self.header_label.width() > 0 else 184
        target_h = self.header_label.height() if hasattr(self, "header_label") and self.header_label.height() > 0 else 86
        scaled = pixmap.scaled(
            target_w, target_h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
        )
        self.header_label.setPixmap(scaled)
        self.header_label.setStyleSheet("border-radius: 6px;")

    def _show_no_image(self):
        """Show fallback text when image is not available."""
        self.header_label.setText("No Image")
        self.header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_label.setStyleSheet(
            "background-color: rgba(255, 255, 255, 0.039); "
            "color: rgba(255, 255, 255, 0.314); "
            "font-size: 8pt; "
            "border-radius: 6px;"
        )

    def _cleanup_fetcher(self, _data: bytes) -> None:
        if hasattr(self, "fetcher") and self.fetcher is not None:
            self.fetcher.deleteLater()
            self.fetcher = None

    def _cleanup_retry_fetcher(self, _data: bytes) -> None:
        if hasattr(self, "retry_fetcher") and self.retry_fetcher is not None:
            self.retry_fetcher.deleteLater()
            self.retry_fetcher = None
