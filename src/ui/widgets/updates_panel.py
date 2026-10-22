import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel



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
        self.badge = None
        self.action_btn = None

        self.setFixedHeight(38)

        from utils.image_fetcher import ImageFetcher
        cache_path = ImageFetcher.get_cache_path(appid)
        if cache_path.exists():
            from PyQt6.QtGui import QPixmap
            self.pixmap = QPixmap(str(cache_path))

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
                f"background: transparent; border: 1px solid {badge_color};"
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
                    if main_win and hasattr(main_win, "game_manager"):
                        game = main_win.game_manager.get_game(appid)
                        if game:
                            from ui.dialogs.gamelibrary_v2 import GameDetailsDialogV2
                            dlg = GameDetailsDialogV2(main_win, game)
                            dlg.exec()
                except Exception:
                    pass

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
        from PyQt6.QtGui import QPainter, QLinearGradient, QBrush, QPainterPath, QPen
        from PyQt6.QtCore import QRect
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = self.rect()
        bg_color = QColor(35, 35, 35) if self.underMouse() else QColor(25, 25, 25)
        
        path = QPainterPath()
        path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()), 6.0, 6.0)
        painter.setClipPath(path)
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg_color)
        painter.drawRect(rect)
        
        if self.pixmap and not self.pixmap.isNull():
            # Calculate aspect ratio scaling to fill the right section
            img_h = rect.height()
            img_w = int(img_h * (self.pixmap.width() / self.pixmap.height()))
            target_rect = QRect(rect.width() - img_w, 0, img_w, rect.height())
            
            # Draw the image
            painter.drawPixmap(target_rect, self.pixmap)
            
            # Smooth transition from solid background color to transparent specifically over the image's left side
            fade_w = min(img_w, 80)
            gradient = QLinearGradient(rect.width() - img_w, 0, rect.width() - img_w + fade_w, 0)
            gradient.setColorAt(0.0, bg_color)
            gradient.setColorAt(1.0, QColor(bg_color.red(), bg_color.green(), bg_color.blue(), 0))
            
            painter.setBrush(QBrush(gradient))
            painter.drawRect(rect)
            
        painter.setClipping(False)
        if self.underMouse():
            pen_color = QColor(self.accent_color)
            painter.setPen(QPen(pen_color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 6, 6)
