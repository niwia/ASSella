import logging
from typing import Optional
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from utils.settings import get_settings
from ui.assets import MINIMIZE, MAXIMIZE, POWER_SVG

logger = logging.getLogger(__name__)


class DialogTitleBar(QFrame):
    """Universal in-app title bar matching the main window aesthetic with SVG controls."""

    def __init__(
        self,
        parent_dialog: QWidget,
        title: str = "",
        can_minimize: bool = True,
        can_maximize: bool = True,
        use_power_close: bool = True,
    ):
        super().__init__(parent_dialog)
        self.parent_dialog = parent_dialog
        self.can_minimize = can_minimize
        self.can_maximize = can_maximize
        self.use_power_close = use_power_close
        self.setFixedHeight(32)

        self.title_label = QLabel(title)
        self.minimize_btn: Optional[QPushButton] = None
        self.maximize_btn: Optional[QPushButton] = None
        self.close_btn: Optional[QPushButton] = None

        self._setup_ui()
        self.update_style()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(6)

        # Title Label
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.title_label.setStyleSheet("font-weight: bold; font-size: 9.5pt; border: none; background: transparent;")
        layout.addWidget(self.title_label)

        layout.addStretch()

        # Window Action Controls
        if self.can_minimize:
            self.minimize_btn = self._create_svg_button(MINIMIZE, self._on_minimize, "Minimize")
            layout.addWidget(self.minimize_btn)

        if self.can_maximize:
            self.maximize_btn = self._create_svg_button(MAXIMIZE, self._toggle_maximize, "Maximize")
            layout.addWidget(self.maximize_btn)

        self.close_btn = self._create_svg_button(
            POWER_SVG, self._on_close, "Close"
        )
        layout.addWidget(self.close_btn)

    def _build_svg_pixmap(self, svg_data: str, color: QColor) -> QPixmap:
        renderer = QSvgRenderer(svg_data.encode("utf-8"))
        icon_size = QSize(16, 16)
        pixmap = QPixmap(icon_size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), color)
        painter.end()
        return pixmap

    def _create_svg_button(self, svg_data: str, on_click, tooltip: str) -> QPushButton:
        btn = QPushButton()
        btn.setToolTip(tooltip)
        btn.setFixedSize(22, 22)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("background-color: transparent; border: none; padding: 2px; margin: 0px;")
        if on_click:
            btn.clicked.connect(on_click)
        return btn

    def update_style(self) -> None:
        """Update colors and icons to match active theme settings."""
        settings = get_settings()
        accent = settings.value("accent_color", "#C06C84")
        bg = settings.value("background_color", "#000000")
        accent_color = QColor(accent)
        bg_color = QColor(bg)

        # Subtle elevated background for the titlebar
        titlebar_bg = bg_color.lighter(108).name()

        self.setStyleSheet(
            f"""
            DialogTitleBar {{
                background-color: {titlebar_bg};
                border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            }}
            """
        )
        self.title_label.setStyleSheet(f"color: {accent}; font-weight: bold; font-size: 9.5pt; border: none; background: transparent;")

        # Update button icons
        for btn, svg in [
            (self.minimize_btn, MINIMIZE),
            (self.maximize_btn, MAXIMIZE),
            (self.close_btn, POWER_SVG),
        ]:
            if btn:
                pixmap = self._build_svg_pixmap(svg, accent_color)
                btn.setIcon(QIcon(pixmap))
                btn.setIconSize(pixmap.size())

    def _on_minimize(self) -> None:
        self.parent_dialog.showMinimized()

    def _toggle_maximize(self) -> None:
        if self.parent_dialog.isMaximized():
            self.parent_dialog.showNormal()
        else:
            self.parent_dialog.showMaximized()

    def _on_close(self) -> None:
        self.parent_dialog.close()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint() - self.parent_dialog.frameGeometry().topLeft()
            window = self.window().windowHandle()
            if window is not None:
                window.startSystemMove()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton and hasattr(self, "_drag_start_pos"):
            self.parent_dialog.move(event.globalPosition().toPoint() - self._drag_start_pos)
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.can_maximize:
            self._toggle_maximize()
            event.accept()
