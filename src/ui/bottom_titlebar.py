import logging
from typing import Callable, Optional

from PyQt6.QtCore import QSize, Qt, QTimer, QPropertyAnimation
from PyQt6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
    QGraphicsOpacityEffect,
)

from utils.helpers import get_base_path
from utils.settings import get_settings
from utils.version import app_version
from .assets import (
    BOOK_SVG,
    GEAR_SVG,
    MAXIMIZE,
    MINIMIZE,
    POWER_SVG,
    SEARCH_SVG,
    PALETTE_SVG,
)

logger = logging.getLogger(__name__)


class ClickableLabel(QLabel):
    """A QLabel that emits a callback when clicked."""

    def __init__(
        self,
        text: str,
        parent: Optional[QWidget] = None,
        callback: Optional[Callable[[], None]] = None,
    ):
        super().__init__(text, parent)
        self.callback = callback
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.callback:
            self.callback()
        super().mousePressEvent(event)


class BottomTitleBar(QFrame):
    """Custom title bar displayed at the bottom (or top) of the window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.parent_window = parent
        self.drag_pos = None
        self.setFixedHeight(32)
        self.no_previous_state = True

        self.navi_label: Optional[QLabel] = None
        self.title_label: Optional[QLabel] = None

        # Buttons
        self.status_button: Optional[QPushButton] = None
        self.search_button: Optional[QPushButton] = None
        self.game_library_button: Optional[QPushButton] = None
        self.settings_button: Optional[QPushButton] = None
        self.minimize_button: Optional[QPushButton] = None
        self.maximize_button: Optional[QPushButton] = None
        self.close_button: Optional[QPushButton] = None

        self.update_arrow_label: Optional[QLabel] = None

        self._setup_ui()
        self._apply_style()
        logger.debug("CustomTitleBar initialized.")

    def _setup_ui(self) -> None:
        """Setup the layout and widgets."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 0, 5, 0)
        layout.setSpacing(5)

        left_widget = self._create_left_section()
        right_widget = self._create_right_section()

        self.title_label = QLabel("ASSella")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        layout.addWidget(left_widget, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.title_label, 1)
        layout.addWidget(right_widget, 0, Qt.AlignmentFlag.AlignRight)

    def _create_left_section(self) -> QWidget:
        """Create the left section containing animation and version."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Navi GIF removed per user request

        version_label = ClickableLabel(
            app_version,
            self.parent_window,
            getattr(self.parent_window, "open_credits_dialog", None),
        )
        version_label.setStyleSheet("color: #888888;")
        version_label.setToolTip("View credits")
        layout.addWidget(version_label, alignment=Qt.AlignmentFlag.AlignLeft)

        self.update_arrow_label = ClickableLabel(
            " ⬆ Update Available",
            self.parent_window,
            lambda: self.trigger_update_flow()
        )
        self.update_arrow_label.setStyleSheet("color: #E05A47; font-weight: bold;")
        self.update_arrow_label.setToolTip("Click here to apply delta updates (ZSync) now.")
        self.update_arrow_label.setVisible(False)
        layout.addWidget(self.update_arrow_label, alignment=Qt.AlignmentFlag.AlignLeft)

        # Pulse fading effect using QGraphicsOpacityEffect & QPropertyAnimation
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.update_arrow_label.setGraphicsEffect(self.opacity_effect)

        self.fade_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_animation.setDuration(3000) # Super slow 3-second cycle
        self.fade_animation.setKeyValueAt(0, 0.15) # Pulse start (almost invisible)
        self.fade_animation.setKeyValueAt(0.5, 0.75) # Pulse peak (75% opacity)
        self.fade_animation.setKeyValueAt(1, 0.15) # Pulse end
        self.fade_animation.setLoopCount(-1) # Infinite looping

        widget.setMinimumSize(widget.sizeHint())
        widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return widget


    def _create_right_section(self) -> QWidget:
        """Create the right section containing buttons."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addStretch()

        parent = self.parent_window

        self.status_button = self._create_colored_circle_button(
            getattr(parent, "open_status_dialog", None),
            "Download Status",
        )
        self.status_button.setVisible(False)

        self.search_button = self._create_svg_button(
            SEARCH_SVG, getattr(parent, "open_fetch_dialog", None), "Download Game"
        )
        layout.addWidget(self.search_button)

        self.workshop_button = self._create_svg_button(
            PALETTE_SVG, getattr(parent, "open_workshop_dialog", None), "Workshop Downloader"
        )
        layout.addWidget(self.workshop_button)

        self.game_library_button = self._create_svg_button(
            BOOK_SVG, getattr(parent, "open_game_library", None), "Game Library"
        )
        layout.addWidget(self.game_library_button)

        self.settings_button = self._create_svg_button(
            GEAR_SVG, getattr(parent, "open_settings", None), "Settings"
        )
        layout.addWidget(self.settings_button)

        self.minimize_button = self._create_svg_button(
            MINIMIZE, self._minimize_window, "Minimize"
        )
        layout.addWidget(self.minimize_button)

        self.maximize_button = self._create_svg_button(
            MAXIMIZE, self._maximize_window, "Maximize"
        )
        layout.addWidget(self.maximize_button)

        self.close_button = self._create_svg_button(
            POWER_SVG, self._close_window, "Close"
        )
        layout.addWidget(self.close_button)

        widget.setMinimumSize(widget.sizeHint())
        widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return widget

    def _apply_style(self) -> None:
        """Apply style settings from the parent window."""
        settings = get_settings()
        bg_color = settings.value("background_color", "#000000")
        accent_color = settings.value("accent_color", "#C06C84")

        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {bg_color};
            }}
            QToolTip {{
                color: {accent_color};
                background-color: {bg_color};
                border: 1px solid {accent_color};
                padding: 2px;
            }}
        """
        )

        if self.title_label:
            self.title_label.setStyleSheet(f"color: {accent_color}; font-size: 14pt;")

    def update_style(self) -> None:
        """Update the style when colors change."""
        self._apply_style()
        self._update_button_colors()
        self._update_button_styles()

    def _update_button_styles(self) -> None:
        """Update all button styles with custom border and background."""
        settings = get_settings()
        bg_color = QColor(settings.value("background_color", "#000000"))

        bg_hover = bg_color
        hover_lightness = 150
        if bg_color == QColor("#000000"):
            bg_hover = QColor("#282828")
            hover_lightness = 120

        button_style = f"""
            QPushButton {{
                background-color: {bg_color.name()};
                border: none;
                border-radius: 3px;
                padding: 1px;
            }}
            QPushButton:hover {{
                background-color: {bg_hover.lighter(hover_lightness).name()};
            }}
        """

        buttons = [
            self.minimize_button,
            self.maximize_button,
            self.search_button,
            self.workshop_button,
            self.game_library_button,
            self.settings_button,
            self.close_button,
        ]

        for button in buttons:
            if button:
                button.setStyleSheet(button_style)

    def _update_button_colors(self) -> None:
        """Update all SVG button colors to match the current accent color."""
        settings = get_settings()
        accent_color = settings.value("accent_color", "#C06C84")

        buttons = [
            (self.minimize_button, MINIMIZE),
            (self.maximize_button, MAXIMIZE),
            (self.search_button, SEARCH_SVG),
            (self.workshop_button, PALETTE_SVG),
            (self.game_library_button, BOOK_SVG),
            (self.settings_button, GEAR_SVG),
            (self.close_button, POWER_SVG),
        ]

        for button, svg_data in buttons:
            if button:
                self._update_svg_button_color(button, svg_data, accent_color)

        if self.no_previous_state and self.status_button:
            self._update_colored_circle_button(self.status_button, accent_color)

    @staticmethod
    def _update_colored_circle_button(button: QPushButton, color: str) -> None:
        """Update a colored circle button's color."""
        try:
            stylesheet = f"""
            QPushButton {{
                border-radius: 10px;
                background-color: {color};
                border: none;
            }}
            QPushButton:hover {{
                border: 2px solid {color};
                background-color: {color};
                opacity: 0.8;
            }}
            QPushButton:pressed {{
                opacity: 0.6;
            }}
            """
            button.setStyleSheet(stylesheet)
        except Exception as e:
            logger.error(f"Failed to update colored circle button: {e}", exc_info=True)

    def update_colored_circle_button(self, button: QPushButton, color: str) -> None:
        self._update_colored_circle_button(button, color)

    @staticmethod
    def _build_svg_pixmap(svg_data: str, color: QColor) -> QPixmap:
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

    def _update_svg_button_color(
        self, button: QPushButton, svg_data: str, color: str
    ) -> None:
        """Update a single SVG button's color."""
        try:
            pixmap = self._build_svg_pixmap(svg_data, QColor(color))
            button.setIcon(QIcon(pixmap))

        except Exception as e:
            logger.error(f"Failed to update SVG button color: {e}", exc_info=True)

    def _create_svg_button(
        self,
        svg_data: str,
        on_click: Optional[Callable[[], None]],
        tooltip: str,
    ) -> QPushButton:
        """Create a button with an SVG icon."""
        try:
            button = QPushButton()
            button.setToolTip(tooltip)

            settings = get_settings()
            accent_color = QColor(settings.value("accent_color", "#C06C84"))

            pixmap = self._build_svg_pixmap(svg_data, accent_color)
            button.setIcon(QIcon(pixmap))
            button.setIconSize(pixmap.size())
            button.setFixedSize(20, 20)

            if on_click:
                button.clicked.connect(on_click)
            return button

        except Exception as e:
            logger.error(f"Failed to create SVG button: {e}", exc_info=True)
            fallback_button = QPushButton("X")
            fallback_button.setFixedSize(20, 20)
            if on_click:
                fallback_button.clicked.connect(on_click)
            return fallback_button

    @staticmethod
    def _create_colored_circle_button(
        callback: Optional[Callable[[], None]],
        tooltip_text: str,
    ) -> QPushButton:
        """Create a simple colored circle button."""
        button = QPushButton()
        button.setFixedSize(20, 20)

        if tooltip_text:
            button.setToolTip(tooltip_text)

        if callback:
            button.clicked.connect(callback)

        return button

    def _minimize_window(self) -> None:
        """Minimize the window."""
        self.parent_window.showMinimized()

    def _maximize_window(self) -> None:
        """Maximize or restore the window."""
        if self.parent_window.isMaximized():
            self.parent_window.showNormal()
        else:
            self.parent_window.showMaximized()

    def _close_window(self) -> None:
        """Close the window."""
        self.parent_window.close()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press for window movement."""
        if event.button() != Qt.MouseButton.LeftButton:
            event.accept()
            return

        # Check if we're not on a resize handle (border area)
        border_width = 6
        pos = event.pos()
        width = self.width()
        height = self.height()

        on_left_border = pos.x() <= border_width
        on_right_border = pos.x() >= width - border_width
        on_top_border = pos.y() <= border_width
        on_bottom_border = pos.y() >= height - border_width

        if on_left_border or on_right_border or on_top_border or on_bottom_border:
            event.accept()
            return

        window = self.window().windowHandle()
        if window is not None:
            window.startSystemMove()

        event.accept()

    def show_update_indicator(self, show: bool) -> None:
        """Show or hide the update indicator and start/stop the pulse animation."""
        if hasattr(self, "update_arrow_label") and self.update_arrow_label:
            self.update_arrow_label.setVisible(show)
            if show:
                self.fade_animation.start()
            else:
                self.fade_animation.stop()

    def trigger_update_flow(self) -> None:
        """Triggers the self-update logic on the main window."""
        if hasattr(self, "parent_window") and self.parent_window:
            if hasattr(self.parent_window, "run_self_update"):
                self.parent_window.run_self_update()


"""
The wired might actually be thought of as a highly advanced upper layer of
the real world. In other words, physical reality is nothing but an illusion,
a hologram of the information that flows to us through the wired.
This is because the body, physical motion, the activity of the human brain
is merely a physical phenomenon, simply caused by synapses delivering
electrical impulses.
The physical body exists at a less evolved plane only to verify one's
existence in the universe.
"""
