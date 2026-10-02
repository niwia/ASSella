import os
import random
import logging
from typing import cast
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QHBoxLayout,
    QApplication,
    QFrame,
)

from utils.helpers import get_base_path
from utils.paths import Paths, get_jumpscare_gif

logger = logging.getLogger(__name__)


class QueueListWidget(QListWidget):
    """QListWidget for the download queue with optional animated Wired background."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self._origins_movie = None
        self._setup_origins_movie()

    def _setup_origins_movie(self):
        try:
            from utils.settings import get_settings
            settings = getattr(self.main_window, "settings", None) or get_settings()
            if settings and settings.value("remember_origins", False, type=bool):
                gif_path = (
                    get_jumpscare_gif("171258.gif")
                    or get_jumpscare_gif("lain4.gif")
                    or get_jumpscare_gif("lain3.gif")
                )
                if gif_path and os.path.exists(gif_path):
                    from PyQt6.QtGui import QMovie
                    if not self._origins_movie or self._origins_movie.fileName() != gif_path:
                        self._origins_movie = QMovie(gif_path)
                        self._origins_movie.frameChanged.connect(self.viewport().update)
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
        from PyQt6.QtGui import QPainter, QMovie, QPainterPath, QColor, QLinearGradient, QBrush
        from PyQt6.QtCore import Qt, QRectF
        if hasattr(self, "_origins_movie") and self._origins_movie and self._origins_movie.state() == QMovie.MovieState.Running:
            viewport = self.viewport()
            painter = QPainter(viewport)
            current_pixmap = self._origins_movie.currentPixmap()
            if not current_pixmap.isNull():
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

                rect = viewport.rect()
                path = QPainterPath()
                path.addRoundedRect(QRectF(rect), 6.0, 6.0)
                painter.setClipPath(path)

                scaled_pixmap = current_pixmap.scaled(
                    viewport.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation
                )
                x = (viewport.width() - scaled_pixmap.width()) // 2
                y = (viewport.height() - scaled_pixmap.height()) // 2

                painter.setOpacity(0.18)
                painter.drawPixmap(x, y, scaled_pixmap)

                # Soft vignette blend
                vignette = QLinearGradient(0, 0, viewport.width(), 0)
                bg_col = QColor(20, 20, 20)
                vignette.setColorAt(0.0, QColor(bg_col.red(), bg_col.green(), bg_col.blue(), 80))
                vignette.setColorAt(0.12, QColor(bg_col.red(), bg_col.green(), bg_col.blue(), 0))
                vignette.setColorAt(0.88, QColor(bg_col.red(), bg_col.green(), bg_col.blue(), 0))
                vignette.setColorAt(1.0, QColor(bg_col.red(), bg_col.green(), bg_col.blue(), 80))

                painter.setOpacity(0.4)
                painter.fillPath(path, QBrush(vignette))


class UIStateManager:
    def __init__(self, main_window):
        self.main_window = main_window
        self.settings = main_window.settings

        # UI state
        self.fetch_dialog = None
        self.depot_dialog = None


        # Queue UI elements
        self.queue_widget = None
        self.queue_list_widget = None
        self.queue_move_up_button = None
        self.queue_move_down_button = None
        self.queue_remove_button = None
        self.pause_button = None
        self.cancel_button = None





    def setup_queue_panel(self):
        """Setup the download queue panel"""
        self.queue_widget = QWidget()
        queue_layout = QVBoxLayout(self.queue_widget)
        queue_layout.setContentsMargins(0, 0, 5, 0)

        # Queue label
        queue_label = QLabel("Download Queue")
        accent = getattr(self.main_window, "accent_color", "#C06C84") or "#C06C84"
        queue_label.setStyleSheet(f"color: {accent}; font-weight: bold; font-size: 8pt;")
        queue_layout.addWidget(queue_label)

        # Queue list
        self.queue_list_widget = QueueListWidget(self.main_window)
        self.queue_list_widget.setToolTip(
            "Current download queue. Select an item to move it."
        )
        queue_layout.addWidget(self.queue_list_widget)

        # Queue buttons
        self._setup_queue_buttons(queue_layout)

    def _setup_queue_buttons(self, parent_layout):
        """Setup queue control buttons"""
        queue_button_layout = QHBoxLayout()

        self.queue_move_up_button = QPushButton("Move Up")
        self.queue_move_up_button.clicked.connect(
            self.main_window.job_queue.move_item_up
        )
        queue_button_layout.addWidget(self.queue_move_up_button)

        self.queue_move_down_button = QPushButton("Move Down")
        self.queue_move_down_button.clicked.connect(
            self.main_window.job_queue.move_item_down
        )
        queue_button_layout.addWidget(self.queue_move_down_button)

        self.queue_remove_button = QPushButton("Remove")
        self.queue_remove_button.clicked.connect(self.main_window.job_queue.remove_item)
        queue_button_layout.addWidget(self.queue_remove_button)

        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self.main_window.task_manager.toggle_pause)
        self.pause_button.setVisible(False)
        queue_button_layout.addWidget(self.pause_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(
            self.main_window.task_manager.cancel_current_job
        )
        self.cancel_button.setVisible(False)
        queue_button_layout.addWidget(self.cancel_button)

        parent_layout.addLayout(queue_button_layout)

    def set_download_controls_visible(self, visible: bool) -> None:
        """Show or hide pause/cancel buttons based on current settings."""
        # Hide the traditional queue buttons
        if self.pause_button:
            self.pause_button.setVisible(False)
        if self.cancel_button:
            self.cancel_button.setVisible(False)
        # Show/hide inline text controls on main window
        mw = self.main_window
        if hasattr(mw, "media_pause_button") and mw.media_pause_button:
            mw.media_pause_button.setVisible(visible)
        if hasattr(mw, "media_cancel_button") and mw.media_cancel_button:
            mw.media_cancel_button.setVisible(visible)
        if hasattr(mw, "_sep_label") and mw._sep_label:
            mw._sep_label.setVisible(visible)

    def set_pause_button_text(self, text: str) -> None:
        """Set text for pause button based on state."""
        # Use plain text (Pause / Resume) — no emoji
        if hasattr(self.main_window, "media_pause_button") and self.main_window.media_pause_button:
            self.main_window.media_pause_button.setText(text)
            self.main_window.media_pause_button.setToolTip("")

    def _apply_queue_styles(self, beta: bool) -> None:
        accent = self.main_window.accent_color or "#C06C84"
        
        def hex_to_rgba(hex_color, alpha):
            hex_color = hex_color.lstrip('#')
            if len(hex_color) == 3:
                hex_color = ''.join([c*2 for c in hex_color])
            try:
                r = int(hex_color[0:2], 16)
                g = int(hex_color[2:4], 16)
                b = int(hex_color[4:6], 16)
                return f"rgba({r}, {g}, {b}, {alpha})"
            except Exception:
                return f"rgba(255, 255, 255, {alpha})"

        accent_alpha = hex_to_rgba(accent, 40)
        accent_hover_alpha = hex_to_rgba(accent, 60)
        
        if beta:
            q_sz = self.settings.value("font-size-queue", 10, type=int)
            # Styled list widget for 2.0
            list_style = f"""
                QListWidget {{
                    background-color: rgba(20, 20, 20, 160);
                    border: 1px solid rgba(255, 255, 255, 12);
                    border-radius: 6px;
                    color: #FFFFFF;
                    padding: 4px;
                    font-size: {q_sz}pt;
                }}
                QListWidget::item {{
                    background-color: rgba(255, 255, 255, 8);
                    border-radius: 4px;
                    padding: 5px 8px;
                    margin-bottom: 3px;
                    font-size: {q_sz}pt;
                }}
                QListWidget::item:hover {{
                    background-color: rgba(255, 255, 255, 18);
                }}
                QListWidget::item:selected {{
                    background-color: {accent_alpha};
                    border: 1px solid {accent};
                    color: #FFFFFF;
                }}
                QListWidget::item:selected:hover {{
                    background-color: {accent_hover_alpha};
                }}
                QScrollBar:vertical {{
                    border: none;
                    background: rgba(0, 0, 0, 20);
                    width: 6px;
                    margin: 0px;
                    border-radius: 3px;
                }}
                QScrollBar::handle:vertical {{
                    background: rgba(255, 255, 255, 30);
                    min-height: 20px;
                    border-radius: 3px;
                }}
                QScrollBar::handle:vertical:hover {{
                    background: {accent};
                }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                    height: 0px;
                }}
            """
            self.queue_list_widget.setStyleSheet(list_style)
            
            # Styled control buttons for 2.0
            btn_style = f"""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 6);
                    border: 1px solid rgba(255, 255, 255, 15);
                    border-radius: 4px;
                    color: #EEEEEE;
                    padding: 4px 8px;
                    font-size: 9pt;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 16);
                    border: 1px solid {accent};
                    color: {accent};
                }}
                QPushButton:pressed {{
                    background-color: rgba(255, 255, 255, 24);
                }}
                QPushButton:disabled {{
                    background-color: transparent;
                    border: 1px solid rgba(255, 255, 255, 8);
                    color: rgba(255, 255, 255, 20);
                }}
            """
            self.queue_move_up_button.setStyleSheet(btn_style)
            self.queue_move_down_button.setStyleSheet(btn_style)
            self.queue_remove_button.setStyleSheet(btn_style)
            self.pause_button.setStyleSheet(btn_style)
            self.cancel_button.setStyleSheet(btn_style)
        else:
            # Default style (reset to standard styles)
            self.queue_list_widget.setStyleSheet("")
            self.queue_move_up_button.setStyleSheet("")
            self.queue_move_down_button.setStyleSheet("")
            self.queue_remove_button.setStyleSheet("")
            self.pause_button.setStyleSheet("")
            self.cancel_button.setStyleSheet("")

    def apply_style_settings(self):
        """Apply current style settings to UI"""
        self.main_window.background_color = self.settings.value(
            "background_color", "#000000"
        )
        self.main_window.accent_color = self.settings.value("accent_color", "#C06C84")

        # Load font family
        font_family = self.settings.value("font", "Open Sans")
        if font_family.lower() in ("trixiecyrg-plain", "trixiecyrg-plain regular", "trixie"):
            font_family = "Open Sans"

        # Load size (default 10). If Sonic mode and user left default 10, bump to 12
        font_size = self.settings.value("font-size", 10, type=int)

        # Create font
        font = QFont(font_family)
        font.setPointSize(font_size)

        # Set font style
        font_style = self.settings.value("font-style", "Normal")
        if font_style == "Italic":
            font.setItalic(True)
        elif font_style == "Bold":
            font.setBold(True)
        elif font_style == "Bold Italic":
            font.setBold(True)
            font.setItalic(True)
        # "Normal" is the default, so no changes needed

        self.main_window.font = font
        self.main_window.setFont(font)

        # Update application appearance
        from ui.theme import update_appearance

        # UI mode (e.g., 'sonic') may override colors and font file
        ui_mode = self.settings.value("ui_mode", "default")

        font_file = None
        if ui_mode == "sonic":
            # Sonic mode: use specific palette (blue background, yellow accent)
            self.main_window.accent_color = "#ffcc00"
            self.main_window.background_color = "#002c83"
            font_file = self.settings.value("font-file", "sonic/sonic-1-hud-font.otf")

        font_ok, font_info = update_appearance(
            cast(QApplication, QApplication.instance()),
            self.main_window.accent_color,
            self.main_window.background_color,
            self.main_window.font,
            font_file=font_file,
        )

        if ui_mode == "sonic" and font_ok:
            # Sync main window font family to loaded Sonic font
            sonic_font = QFont(font_info)
            sonic_font.setPointSize(font_size)
            self.main_window.font = sonic_font
            self.main_window.setFont(sonic_font)

        # Update status pager font/style if present
        if hasattr(self.main_window, "status_pager") and self.main_window.status_pager:
            self.main_window.status_pager.update_style()

        self.main_window.update()

        self.queue_move_up_button.setText("▲ Move Up")
        self.queue_move_down_button.setText("▼ Move Down")
        self.queue_remove_button.setText("✖ Remove")
        # Hide the traditional pause/cancel buttons in queue list
        self.pause_button.setVisible(False)
        self.cancel_button.setVisible(False)

        self._apply_queue_styles(True)

        # Apply styles to various UI elements
        self._apply_background_color()
        self._apply_accent_color()

    def _apply_background_color(self):
        """Apply background color to main content"""
        preset = self.settings.value("material_preset", "ocean", type=str)
        if preset == "halloween":
            self.main_window.central_widget.setStyleSheet("background: transparent;")
        else:
            self.main_window.central_widget.setStyleSheet(
                f"background-color: {self.main_window.background_color};"
            )
        self.main_window.update()

    def _apply_accent_color(self):
        """Apply accent color to UI elements"""
        accent_style = f"color: {self.main_window.accent_color};"

        # Status Pager
        if hasattr(self.main_window, "status_pager") and self.main_window.status_pager:
            self.main_window.status_pager.update_style()

        # Active Hubcap label
        if hasattr(self.main_window, "active_hubcap_label") and self.main_window.active_hubcap_label:
            self.main_window.active_hubcap_label.setStyleSheet(
                f"color: {self.main_window.accent_color}; font-size: 11px; font-weight: bold; border: none; background: transparent;"
            )

        # Queue label
        hdr_sz = self.settings.value("font-size-headers", 11, type=int)
        if hasattr(self, "queue_widget") and self.queue_widget:
            queue_label = self.queue_widget.findChild(QLabel)
            if queue_label:
                queue_label.setStyleSheet(
                    f"color: {self.main_window.accent_color}; font-size: {hdr_sz}pt; font-weight: bold; border: none; background: transparent;"
                )

        # Speed label
        if hasattr(self.main_window, "speed_label") and self.main_window.speed_label:
            base_sz = self.settings.value("font-size", 10, type=int)
            self.main_window.speed_label.setStyleSheet(
                f"color: {self.main_window.accent_color}; font-size: {base_sz}pt; font-weight: 500; border: none; background: transparent;"
            )

        # Progress bar
        self.main_window.update_progress_bar_style()

        # Log output
        if hasattr(self.main_window, "log_output") and self.main_window.log_output:
            self.main_window.log_output.setStyleSheet(accent_style)

        # Simplified terminal
        if hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.update_style()

        # Bottom titlebar
        if hasattr(self.main_window, "bottom_titlebar"):
            self.main_window.bottom_titlebar.update_style()

        # Dashboard elements styling
        if hasattr(self.main_window, "usage_value") and self.main_window.usage_value:
            self.main_window.usage_value.setStyleSheet(
                f"color: {self.main_window.accent_color}; font-size: 11px; font-weight: bold; border: none; background: transparent;"
            )
        if hasattr(self.main_window, "expiry_value") and self.main_window.expiry_value:
            self.main_window.expiry_value.setStyleSheet(
                f"color: {self.main_window.accent_color}; font-size: 11px; font-weight: bold; border: none; background: transparent;"
            )
        if hasattr(self.main_window, "sls_status_value") and self.main_window.sls_status_value:
            self.main_window.sls_status_value.setStyleSheet(
                f"color: {self.main_window.accent_color}; font-size: 11px; font-weight: bold; border: none; background: transparent;"
            )
        if hasattr(self.main_window, "update_all_btn") and self.main_window.update_all_btn:
            self.main_window.update_all_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {self.main_window.accent_color};
                    border: 1px solid {self.main_window.accent_color};
                    border-radius: 4px;
                    padding: 4px 10px;
                    font-size: 11px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 15);
                }}
                QPushButton:disabled {{
                    border: 1px solid rgba(255, 255, 255, 15);
                    color: rgba(255, 255, 255, 60);
                }}
            """)

    def update_queue_visibility(self, is_processing, has_jobs):
        """Update queue visibility based on current state"""
        if not is_processing and not has_jobs:
            if self.queue_widget:
                self.queue_widget.setVisible(False)
            self.main_window.drop_text_label.setText("Drag and Drop Zip here")
        else:
            if self.queue_widget:
                self.queue_widget.setVisible(True)
            if not is_processing:
                self.main_window.drop_text_label.setText(
                    "Queue idle. Ready for next job."
                )

        # Toggle dashboard and active hubcap label based on processing state
        if hasattr(self.main_window, "dashboard_widget") and self.main_window.dashboard_widget:
            self.main_window.dashboard_widget.setVisible(not is_processing)
        if hasattr(self.main_window, "active_hubcap_label") and self.main_window.active_hubcap_label:
            self.main_window.active_hubcap_label.setVisible(is_processing)


