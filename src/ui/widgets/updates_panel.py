import logging
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel

logger = logging.getLogger(__name__)



class UpdatesPanel(QFrame):
    """Container panel for pending updates with optional animated Wired background when empty."""

    def __init__(self, terminal_widget, parent=None):
        super().__init__(parent)
        self.terminal_widget = terminal_widget
        self._origins_movie = None
        self._setup_origins_movie()

    def _setup_origins_movie(self):
        try:
            settings = getattr(self.terminal_widget, "settings", None)
            if not settings:
                from utils.settings import get_settings
                settings = get_settings()
            if settings and settings.value("remember_origins", False, type=bool):
                from utils.paths import get_jumpscare_gif
                gif_path = (
                    get_jumpscare_gif("171258.gif")
                    or get_jumpscare_gif("lain4.gif")
                    or get_jumpscare_gif("lain3.gif")
                )
                if gif_path and os.path.exists(gif_path):
                    from PyQt6.QtGui import QMovie
                    if not self._origins_movie or self._origins_movie.fileName() != gif_path:
                        self._origins_movie = QMovie(gif_path)
                        self._origins_movie.frameChanged.connect(self.update)
                        self._origins_movie.start()
                    return
            if self._origins_movie:
                self._origins_movie.stop()
                self._origins_movie = None
        except Exception:
            self._origins_movie = None

    def showEvent(self, event):
        super().showEvent(event)
        self._setup_origins_movie()

    def paintEvent(self, event):
        super().paintEvent(event)
        from PyQt6.QtGui import QPainter, QMovie, QPainterPath, QLinearGradient, QBrush
        from PyQt6.QtCore import QRectF
        if hasattr(self, "_origins_movie") and self._origins_movie and self._origins_movie.state() == QMovie.MovieState.Running:
            if getattr(self.terminal_widget, "_total_updates", 0) == 0:
                painter = QPainter(self)
                current_pixmap = self._origins_movie.currentPixmap()
                if not current_pixmap.isNull():
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

                    rect = self.rect()
                    # Clip to rounded card corners so image fills seamlessly
                    path = QPainterPath()
                    path.addRoundedRect(QRectF(rect), 6.0, 6.0)
                    painter.setClipPath(path)

                    # Scale using KeepAspectRatioByExpanding (Cover mode) to eliminate cut-off side gaps
                    scaled_pixmap = current_pixmap.scaled(
                        self.size(),
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    x = (self.width() - scaled_pixmap.width()) // 2
                    y = (self.height() - scaled_pixmap.height()) // 2

                    painter.setOpacity(0.20)
                    painter.drawPixmap(x, y, scaled_pixmap)

                    # Soft horizontal edge vignette for smooth card blending
                    vignette = QLinearGradient(0, 0, self.width(), 0)
                    bg_col = QColor(30, 30, 30)
                    vignette.setColorAt(0.0, QColor(bg_col.red(), bg_col.green(), bg_col.blue(), 90))
                    vignette.setColorAt(0.12, QColor(bg_col.red(), bg_col.green(), bg_col.blue(), 0))
                    vignette.setColorAt(0.88, QColor(bg_col.red(), bg_col.green(), bg_col.blue(), 0))
                    vignette.setColorAt(1.0, QColor(bg_col.red(), bg_col.green(), bg_col.blue(), 90))

                    painter.setOpacity(0.4)
                    painter.fillPath(path, QBrush(vignette))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "terminal_widget") and self.terminal_widget:
            main_win = getattr(self.terminal_widget, "main_window", None)
            if main_win and hasattr(main_win, "position_update_all_btn"):
                main_win.position_update_all_btn()

def _build_faded_artwork(pixmap, target_size):
    """Scale artwork to cover target_size and apply a smooth S-curve left fade."""
    from ui.widgets.artwork_fade import build_faded_artwork

    return build_faded_artwork(
        pixmap,
        target_size,
        direction="fade_in_right",
        fade_start=0.0,
        fade_end=0.72,
        start_alpha=0,
        peak_alpha=225,
        end_alpha=238,
        edge_alpha=200,
        edge_span=0.16,
    )


class UpdateItemWidget(QFrame):
    """One row of the pending-updates list.

    A row can carry a badge ("AT0-M", "Awaiting Hubcap Key") and an action
    button ("Sync Keys"). Steam-managed (AT0-M) games are never downloaded by
    ASSella - Steam does that - so their action syncs the decryption keys
    into SLSsteam instead of queueing a DepotDownloader job.
    """

    def __init__(
        self,
        appid,
        name,
        accent_color,
        parent=None,
        badge_text: str = "",
        badge_color: str = "#CE93D8",
        action_text: str = "",
        action_callback=None,
        action_tooltip: str = "",
    ):
        super().__init__(parent)
        self.appid = appid
        self.name = name
        self.accent_color = accent_color
        self.pixmap = None
        self._scaled_pixmap = None
        self.badge = None
        self.action_btn = None

        self.setFixedHeight(38)

        from utils.image_fetcher import ImageFetcher
        cache_path = ImageFetcher.get_cache_path(appid)
        if cache_path.exists():
            from PyQt6.QtGui import QPixmap
            self.pixmap = QPixmap(str(cache_path))
        elif appid and appid not in ("0", "N/A", "unknown"):
            def _on_img_fetched(data):
                from PyQt6 import sip
                if sip.isdeleted(self):
                    return
                if data:
                    from PyQt6.QtGui import QPixmap
                    pm = QPixmap()
                    if pm.loadFromData(data) and not pm.isNull():
                        self.pixmap = pm
                        self._scaled_pixmap = None
                        self.update()
            url = ImageFetcher.get_header_image_url(appid)
            self._img_fetcher = ImageFetcher(url, parent=self)
            self._img_fetcher.finished.connect(_on_img_fetched)
            self._img_fetcher.start()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 2, 12, 2)
        lay.setSpacing(8)

        self.lbl = QLabel(name)
        self.lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; background: transparent; border: none;")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl, 1)

        if badge_text:
            self.badge = QLabel(badge_text)
            self.badge.setStyleSheet(
                f"color: {badge_color}; font-size: 8pt; font-weight: bold;"
                f"background: rgba(18, 18, 22, 195); border: 1px solid {badge_color};"
                f"border-radius: 7px; padding: 1px 6px;"
            )
            self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)

        if action_text:
            from PyQt6.QtWidgets import QPushButton
            self.action_btn = QPushButton(action_text, self)
            self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_btn.setFixedHeight(24)
            self.action_btn.setToolTip(action_tooltip or action_text)
            self.action_btn.setStyleSheet(
                f"QPushButton {{ background-color: {accent_color}; color: #121214;"
                f" border: none; border-radius: 12px; font-weight: bold; font-size: 8.5pt;"
                f" padding-left: 10px; padding-right: 10px; }}"
                f"QPushButton:hover {{ background-color: #FFFFFF; color: #000000; }}"
                f"QPushButton:pressed {{ background-color: {accent_color}; }}"
                f"QPushButton:disabled {{ color: rgba(255,255,255,0.4); background-color: {accent_color}; }}"
            )
            if action_callback is not None:
                self.action_btn.clicked.connect(
                    lambda _checked=False, cb=action_callback: cb(self.appid)
                )
            lay.addWidget(self.action_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # The header image is painted on the right edge of the row, so the
        # layout has to leave room for it - otherwise the badge or the button
        # would end up underneath the artwork.
        if (badge_text or action_text) and self.pixmap and not self.pixmap.isNull():
            img_w = int(self.height() * (self.pixmap.width() / max(1, self.pixmap.height())))
            lay.setContentsMargins(12, 2, 12 + img_w, 2)

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            appid = self.appid

            def _open():
                try:
                    main_win = None
                    p = self.parent()
                    while p:
                        if hasattr(p, "game_manager"):
                            main_win = p
                            break
                        p = p.parent()
                    if not main_win:
                        from PyQt6.QtWidgets import QApplication
                        for w in QApplication.topLevelWidgets():
                            if hasattr(w, "game_manager"):
                                main_win = w
                                break
                    if main_win:
                        if hasattr(main_win, "open_game_library"):
                            main_win.open_game_library(show_details_for_appid=str(appid))
                        else:
                            from ui.dialogs.gamelibrary import GameLibraryDialog
                            dlg = GameLibraryDialog(main_win, show_details_for_appid=str(appid))
                            dlg.exec()
                except Exception as e:
                    logger.error(f"Failed to open game library/details for update item {appid}: {e}")

            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, _open)
            event.accept()
        else:
            super().mousePressEvent(event)

    def enterEvent(self, event):
        super().enterEvent(event)
        from PyQt6 import sip
        if not sip.isdeleted(self):
            self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        from PyQt6 import sip
        if not sip.isdeleted(self):
            self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QPainterPath, QPen
        from PyQt6.QtCore import QRect

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        hovered = self.underMouse()
        bg_color = QColor(34, 34, 38) if hovered else QColor(24, 24, 27)

        path = QPainterPath()
        path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()), 6.0, 6.0)
        painter.setClipPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg_color)
        painter.drawRect(rect)

        if self.pixmap and not self.pixmap.isNull():
            natural_w = int(rect.height() * (self.pixmap.width() / max(self.pixmap.height(), 1)))
            target_w = min(rect.width(), max(natural_w, int(rect.width() * 0.65)))
            target_rect = QRect(rect.width() - target_w, 0, target_w, rect.height())

            if self._scaled_pixmap is None or self._scaled_pixmap.size() != target_rect.size():
                self._scaled_pixmap = _build_faded_artwork(self.pixmap, target_rect.size())

            if not self._scaled_pixmap.isNull():
                painter.setOpacity(0.92 if hovered else 0.78)
                painter.drawPixmap(target_rect, self._scaled_pixmap)
                painter.setOpacity(1.0)

        painter.setClipping(False)
        if hovered:
            pen_color = QColor(self.accent_color)
            pen_color.setAlpha(200)
        else:
            pen_color = QColor(255, 255, 255, 16)
        painter.setPen(QPen(pen_color, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 6, 6)
