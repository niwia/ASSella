"""
Canary Welcome Slideshow Wizard
===============================
Compact, frameless 640x360 onboarding window matching the exact dimensions
of the Skyrim wake-up animation.

Features:
  1. Awakening: Full-window 640x360 Skyrim GIF with cinematic bottom overlay
     ("Hi, Your adventure begins" + "Next →" button) on completion.
  2. Hubcap API: Minimal API key field with dynamic Paste/Validate button,
     auto-mode ISP bypass validation, and clean User/Quota/Route status.
  3. SLSsteam Setup: Distro detection, Steam client type (Flatpak/Native),
     fresh Steam library warning, binary detection, "Run Headcrab" terminal
     launcher, and automatic API: yes config toggle.
  4. at0-m Native Downloads: Toggle to enable at0-m native Steam client downloads,
     immediate distro-aware plugin deployment (download.lua, spliced-tickets.lua),
     and Plugins: yes in config.yaml.
  5. Preferences & Maintenance: Config fixer with live check and conditional Fix
     button (only shown when issues detected), LAN Cache toggle, default download
     location, default download behavior, Open Sans font selection, "Apply Recommended",
     and "Finish & Start Exploring".
"""

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal, pyqtSlot, QPoint
from PyQt6.QtGui import QMovie
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QProgressBar,
    QStackedWidget,
    QFrame,
    QWidget,
    QCheckBox,
    QComboBox,
    QSizePolicy,
)

from ui.widgets.switch_toggle import SwitchToggle

from utils.settings import get_settings
from utils.paths import Paths
from utils.color_utils import get_semantic_colors

logger = logging.getLogger(__name__)


def get_linux_distro() -> str:
    """Detect the current Linux distribution name."""
    try:
        import platform
        if hasattr(platform, "freedesktop_os_release"):
            data = platform.freedesktop_os_release()
            return data.get("PRETTY_NAME", data.get("NAME", "Linux"))
        if Path("/etc/os-release").exists():
            with open("/etc/os-release", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=", 1)[1].strip().strip('"')
                    if line.startswith("NAME="):
                        return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return "Linux"


class CanaryWelcomeDialog(QDialog):
    """Compact frameless 640x360 onboarding slideshow matching the GIF dimensions."""

    _hubcap_val_signal = pyqtSignal(bool, dict, str)
    _sls_check_signal = pyqtSignal(bool, dict)
    _headcrab_done_signal = pyqtSignal()
    _config_check_signal = pyqtSignal(tuple)

    def __init__(self, parent: Optional[QWidget] = None, manual: bool = False):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(640, 360)

        self.manual = manual
        self.settings = get_settings()
        self.accent_color = self.settings.value("accent_color", "#a1c9fd")
        self.bg_color = self.settings.value("background_color", "#111318")

        self.semantic = get_semantic_colors(self.accent_color)
        self.card_bg = "rgba(255, 255, 255, 0.04)"
        self.card_border = "rgba(255, 255, 255, 0.08)"

        self._intro_completed = False
        self._hubcap_validating = False
        self._drag_pos: Optional[QPoint] = None

        # Watermark is written immediately so that first-time launch is permanent
        if not self.manual:
            self.settings.setValue("canary_welcome_seen", True)
            self.settings.sync()

        # Connect signals
        self._hubcap_val_signal.connect(self._on_hubcap_validated)
        self._sls_check_signal.connect(self._on_sls_check_result)
        self._headcrab_done_signal.connect(self._on_headcrab_finished)
        self._config_check_signal.connect(self._on_config_check_result)

        self._init_ui()

        # Center on screen or parent window
        try:
            if parent:
                self.move(parent.geometry().center() - self.rect().center())
            else:
                from PyQt6.QtGui import QGuiApplication
                screen = QGuiApplication.primaryScreen()
                if screen:
                    self.move(screen.availableGeometry().center() - self.rect().center())
        except Exception as e:
            logger.debug(f"Failed to center welcome dialog: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Drag support for frameless window
    # ──────────────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    # ──────────────────────────────────────────────────────────────────────────
    # Button Styles (Game Details Styled)
    # ──────────────────────────────────────────────────────────────────────────

    def _btn_secondary_style(self) -> str:
        ac = self.accent_color
        return f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 8px;
                padding: 0 16px;
                color: #FFFFFF;
                font-size: 9pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.12);
                border-color: {ac};
                color: {ac};
            }}
            QPushButton:pressed {{
                background: rgba(255, 255, 255, 0.18);
            }}
            QPushButton:disabled {{
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.05);
                color: rgba(255, 255, 255, 0.25);
            }}
        """

    def _btn_primary_style(self) -> str:
        ac = self.accent_color
        return f"""
            QPushButton {{
                background: {ac};
                color: #000000;
                border: none;
                border-radius: 8px;
                padding: 0 18px;
                font-size: 9.5pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: #FFFFFF;
                color: #000000;
            }}
            QPushButton:pressed {{
                background: rgba(255, 255, 255, 0.85);
            }}
            QPushButton:disabled {{
                background: rgba(255, 255, 255, 0.12);
                color: rgba(255, 255, 255, 0.3);
            }}
        """

    # ──────────────────────────────────────────────────────────────────────────
    # UI construction
    # ──────────────────────────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Outer rounded frame with thin border
        self.outer_frame = QFrame(self)
        self.outer_frame.setObjectName("OuterFrame")
        self.outer_frame.setFixedSize(640, 360)
        self.outer_frame.setStyleSheet(f"""
            QFrame#OuterFrame {{
                background-color: {self.bg_color};
                border: 1px solid rgba(161, 201, 253, 0.35);
                border-radius: 10px;
            }}
            QLabel {{
                color: #FFFFFF;
                border: none;
                background: transparent;
            }}
            QLineEdit {{
                background-color: rgba(255, 255, 255, 0.04);
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.22);
                border-radius: 0px;
                padding: 6px 4px;
                color: #FFFFFF;
                font-size: 9.5pt;
                selection-background-color: {self.accent_color};
                selection-color: #000000;
            }}
            QLineEdit:focus {{
                border-bottom: 1px solid {self.accent_color};
                background-color: rgba(255, 255, 255, 0.07);
            }}
            QComboBox {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 4px 10px;
                color: #FFFFFF;
                font-size: 9pt;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background-color: #1a1c23;
                color: #FFFFFF;
                selection-background-color: {self.accent_color};
                selection-color: #000000;
            }}
            QCheckBox {{
                color: #FFFFFF;
                font-size: 9.5pt;
                spacing: 8px;
            }}
        """)

        frame_layout = QVBoxLayout(self.outer_frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        self.stack = QStackedWidget(self.outer_frame)
        self.stack.setFixedSize(638, 358)

        # 5 Slides
        self.slide_awakening = self._build_slide_awakening()
        self.slide_hubcap = self._build_slide_hubcap()
        self.slide_sls = self._build_slide_sls()
        self.slide_at0m = self._build_slide_at0m()
        self.slide_preferences = self._build_slide_preferences()

        self.stack.addWidget(self.slide_awakening)    # 0
        self.stack.addWidget(self.slide_hubcap)       # 1
        self.stack.addWidget(self.slide_sls)          # 2
        self.stack.addWidget(self.slide_at0m)         # 3
        self.stack.addWidget(self.slide_preferences)  # 4

        frame_layout.addWidget(self.stack)
        root_layout.addWidget(self.outer_frame)

        # Start GIF
        QTimer.singleShot(150, self._start_gif)

    # ──────────────────────────────────────────────────────────────────────────
    # Top Stepper Header (Clean text list, no pills)
    # ──────────────────────────────────────────────────────────────────────────

    def _create_header_bar(self, current_step: int) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        steps = ["Awakening", "Hubcap API", "SLSsteam", "at0-m", "Preferences"]
        stepper_layout = QHBoxLayout()
        stepper_layout.setSpacing(8)

        for idx, title in enumerate(steps):
            lbl = QLabel(f"{idx + 1}. {title}")
            lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            if idx == current_step:
                lbl.setStyleSheet(f"color: {self.accent_color}; font-weight: bold; font-size: 8.5pt;")
            elif idx < current_step:
                lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8pt;")
            else:
                lbl.setStyleSheet("color: rgba(255, 255, 255, 0.3); font-size: 8pt;")
            stepper_layout.addWidget(lbl)

            if idx < len(steps) - 1:
                sep = QLabel("•")
                sep.setStyleSheet("color: rgba(255, 255, 255, 0.2); font-size: 8pt;")
                stepper_layout.addWidget(sep)

        layout.addLayout(stepper_layout)
        layout.addStretch()

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(24, 24)
        btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: rgba(255, 255, 255, 0.4);
                font-size: 11pt;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #ff5252;
            }
        """)
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close)

        return header

    # ──────────────────────────────────────────────────────────────────────────
    # Slide 1: Awakening (Full 640x360 GIF + Bottom Overlay)
    # ──────────────────────────────────────────────────────────────────────────

    def _build_slide_awakening(self) -> QWidget:
        widget = QWidget()
        widget.setFixedSize(638, 358)

        self.gif_label = QLabel(widget)
        self.gif_label.setGeometry(0, 0, 638, 358)
        self.gif_label.setScaledContents(True)
        self.gif_label.setStyleSheet("border-radius: 9px; background: #000000;")

        self.btn_gif_close = QPushButton("✕", widget)
        self.btn_gif_close.setGeometry(604, 8, 26, 26)
        self.btn_gif_close.setStyleSheet("""
            QPushButton {
                background: rgba(0, 0, 0, 0.45);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 13px;
                color: rgba(255, 255, 255, 0.7);
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 82, 82, 0.7);
                color: #FFFFFF;
            }
        """)
        self.btn_gif_close.clicked.connect(self.close)

        self.btn_skip_intro = QPushButton("Skip intro", widget)
        self.btn_skip_intro.setGeometry(14, 10, 80, 26)
        self.btn_skip_intro.setStyleSheet("""
            QPushButton {
                background: rgba(0, 0, 0, 0.45);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 13px;
                color: rgba(255, 255, 255, 0.65);
                font-size: 8pt;
                font-weight: 500;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.15);
                color: #FFFFFF;
            }
        """)
        self.btn_skip_intro.clicked.connect(self._complete_awakening)

        # Bottom Overlay container
        self.overlay_widget = QFrame(widget)
        self.overlay_widget.setGeometry(0, 240, 638, 118)
        self.overlay_widget.setStyleSheet("""
            QFrame {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(17, 19, 24, 0),
                    stop:0.25 rgba(17, 19, 24, 0.88),
                    stop:1 rgba(17, 19, 24, 0.98)
                );
                border-bottom-left-radius: 9px;
                border-bottom-right-radius: 9px;
                border: none;
            }
        """)
        overlay_layout = QHBoxLayout(self.overlay_widget)
        overlay_layout.setContentsMargins(24, 26, 24, 18)
        overlay_layout.setSpacing(16)

        titles_layout = QVBoxLayout()
        titles_layout.setSpacing(3)
        titles_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.awake_title_lbl = QLabel("Hi, Your adventure begins")
        self.awake_title_lbl.setStyleSheet(f"""
            font-size: 15pt;
            font-weight: bold;
            color: {self.accent_color};
            background: transparent;
        """)
        titles_layout.addWidget(self.awake_title_lbl)

        self.awake_sub_lbl = QLabel("Welcome to ASSella Canary. Let's configure your setup.")
        self.awake_sub_lbl.setStyleSheet("""
            color: rgba(255, 255, 255, 0.75);
            font-size: 9pt;
            background: transparent;
        """)
        titles_layout.addWidget(self.awake_sub_lbl)
        overlay_layout.addLayout(titles_layout, 1)

        self.btn_awake_next = QPushButton("Next →")
        self.btn_awake_next.setFixedSize(120, 36)
        self.btn_awake_next.setStyleSheet(self._btn_primary_style())
        self.btn_awake_next.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        overlay_layout.addWidget(self.btn_awake_next, 0, Qt.AlignmentFlag.AlignVCenter)

        self.overlay_widget.hide()

        return widget

    def _start_gif(self) -> None:
        gif_candidates = [
            Paths.resource("intro/skyrim_awake.gif"),
            Path("/home/aiwin/Pictures/hey-you-youre-finally-awake-skyrim.gif"),
        ]

        target_path = None
        for p in gif_candidates:
            if p and Path(p).exists():
                target_path = str(p)
                break

        if not target_path:
            logger.warning("Skyrim wake-up GIF not found at expected paths.")
            self._complete_awakening()
            return

        try:
            self.movie = QMovie(target_path)
            self.movie.setScaledSize(QSize(638, 358))
            self.gif_label.setMovie(self.movie)
            self.movie.frameChanged.connect(self._on_gif_frame)
            self.movie.start()
            QTimer.singleShot(6500, self._complete_awakening)
        except Exception as e:
            logger.error(f"Error loading intro movie: {e}")
            self._complete_awakening()

    def _on_gif_frame(self, frame_num: int) -> None:
        if self._intro_completed:
            return
        if hasattr(self, "movie") and self.movie:
            total_frames = self.movie.frameCount()
            if total_frames > 0 and frame_num >= total_frames - 1:
                self.movie.stop()
                self._complete_awakening()

    def _complete_awakening(self) -> None:
        if self._intro_completed:
            return
        self._intro_completed = True

        if hasattr(self, "movie") and self.movie and self.movie.state() == QMovie.MovieState.Running:
            self.movie.stop()

        self.btn_skip_intro.hide()
        self.overlay_widget.show()

    # ──────────────────────────────────────────────────────────────────────────
    # Slide 2: Hubcap API Setup
    # ──────────────────────────────────────────────────────────────────────────

    def _build_slide_hubcap(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 14, 24, 14)
        layout.setSpacing(10)

        layout.addWidget(self._create_header_bar(current_step=1))

        t_lbl = QLabel("Setup Hubcap API")
        t_lbl.setStyleSheet(f"font-size: 14pt; font-weight: bold; color: {self.accent_color};")
        layout.addWidget(t_lbl)

        sub_lbl = QLabel("Connect your Hubcap API key to fetch manifests, depot decryption keys, and cloud LUAs.")
        sub_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9pt;")
        layout.addWidget(sub_lbl)

        # Single dynamic Paste / Validate button
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 4, 0, 4)
        input_row.setSpacing(12)

        self.txt_api_key = QLineEdit()
        self.txt_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_api_key.setPlaceholderText("Paste your Hubcap API key here...")
        cur_key = self.settings.value("morrenus_api_key", "", type=str)
        if cur_key:
            self.txt_api_key.setText(cur_key)

        input_row.addWidget(self.txt_api_key, 1)

        self.btn_key_action = QPushButton("Validate" if cur_key else "Paste")
        self.btn_key_action.setFixedHeight(34)
        self.btn_key_action.setStyleSheet(self._btn_secondary_style())
        self.btn_key_action.clicked.connect(self._handle_key_action)
        input_row.addWidget(self.btn_key_action)

        self.txt_api_key.textChanged.connect(self._on_key_text_changed)
        layout.addLayout(input_row)

        self.lbl_key_status = QLabel("")
        self.lbl_key_status.setStyleSheet("font-size: 9pt;")
        layout.addWidget(self.lbl_key_status)

        layout.addStretch()

        # Bottom Info Area
        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(5)

        self.lbl_user_and_quota = QLabel("User: --  •  Daily Quota: -- / -- manifests")
        self.lbl_user_and_quota.setStyleSheet("font-size: 9.5pt; font-weight: bold; color: #FFFFFF;")
        info_layout.addWidget(self.lbl_user_and_quota)

        curr_mode = self.settings.value("isp_bypass_mode", "auto", type=str) or "auto"
        self.lbl_connection_route = QLabel(f"Connection Route: {curr_mode.capitalize()} (Direct → DoH → Tor → Wirecutter)")
        self.lbl_connection_route.setStyleSheet(f"font-size: 9pt; color: {self.accent_color};")
        info_layout.addWidget(self.lbl_connection_route)

        layout.addWidget(info_widget)
        layout.addStretch()

        # Bottom navigation
        nav_row = QHBoxLayout()
        nav_row.setSpacing(12)

        btn_back = QPushButton("← Back")
        btn_back.setFixedHeight(36)
        btn_back.setStyleSheet(self._btn_secondary_style())
        btn_back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        nav_row.addWidget(btn_back)

        btn_skip = QPushButton("Skip for Now")
        btn_skip.setFixedHeight(36)
        btn_skip.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: rgba(255, 255, 255, 0.5);
                font-size: 8.5pt;
                padding: 0 12px;
            }
            QPushButton:hover {
                color: rgba(255, 255, 255, 0.85);
            }
        """)
        btn_skip.clicked.connect(lambda: self.stack.setCurrentIndex(2))
        nav_row.addWidget(btn_skip)

        nav_row.addStretch()

        btn_next = QPushButton("Next →")
        btn_next.setFixedSize(115, 36)
        btn_next.setStyleSheet(self._btn_primary_style())
        btn_next.clicked.connect(lambda: self.stack.setCurrentIndex(2))
        nav_row.addWidget(btn_next)
        layout.addLayout(nav_row)

        if cur_key:
            self.lbl_key_status.setText("All done! API Key already configured.")
            self.lbl_key_status.setStyleSheet(f"color: {self.semantic.get('success', '#81c784')}; font-size: 9pt; font-weight: 500;")
            QTimer.singleShot(150, lambda: self._start_hubcap_validation(force=False))

        return widget

    def _on_key_text_changed(self, text: str) -> None:
        if not text.strip():
            self.btn_key_action.setText("Paste")
            self.lbl_key_status.setText("")
        else:
            self.btn_key_action.setText("Validate")

    def _handle_key_action(self) -> None:
        cur_text = self.txt_api_key.text().strip()
        if not cur_text:
            cb = QApplication.clipboard()
            paste_text = cb.text().strip()
            if paste_text:
                self.txt_api_key.setText(paste_text)
                self.btn_key_action.setText("Validate")
                self._start_hubcap_validation(force=True)
            else:
                self.lbl_key_status.setText("Clipboard is empty.")
                self.lbl_key_status.setStyleSheet(f"color: {self.semantic.get('warn', '#ffb74d')}; font-size: 9pt;")
        else:
            self._start_hubcap_validation(force=True)

    def _start_hubcap_validation(self, force: bool = False) -> None:
        key = self.txt_api_key.text().strip()
        if not key:
            self.lbl_key_status.setText("Please enter an API key, or click Skip.")
            self.lbl_key_status.setStyleSheet(f"color: {self.semantic.get('warn', '#ffb74d')}; font-size: 9pt;")
            return

        if self._hubcap_validating:
            return

        self._hubcap_validating = True
        self.btn_key_action.setEnabled(False)
        self.btn_key_action.setText("Validating...")
        self.lbl_key_status.setText("Connecting via auto-fallback routing...")
        self.lbl_key_status.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 9pt;")

        def _worker():
            try:
                from core.morrenus_api import BASE_URL, get_session
                from utils.isp_bypass import execute_hubcap_request

                headers = {"Authorization": f"Bearer {key}"}
                url = f"{BASE_URL}/user/stats"
                resp = execute_hubcap_request(
                    get_session(), "GET", url, headers=headers, params={"api_key": key}, timeout=10
                )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict) and data.get("username"):
                    self._hubcap_val_signal.emit(True, data, "")
                else:
                    self._hubcap_val_signal.emit(False, {}, "Unexpected response from Hubcap API.")
            except Exception as e:
                from core.morrenus_api import _handle_request_exception
                err = _handle_request_exception(e, "Hubcap API Validation")
                self._hubcap_val_signal.emit(False, {}, str(err))

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(bool, dict, str)
    def _on_hubcap_validated(self, success: bool, data: dict, error_msg: str) -> None:
        self._hubcap_validating = False
        self.btn_key_action.setEnabled(True)
        self.btn_key_action.setText("Validate")

        success_color = self.semantic.get("success", "#81c784")
        error_color = self.semantic.get("error", "#e57373")

        if success:
            key = self.txt_api_key.text().strip()
            self.settings.setValue("morrenus_api_key", key)
            self.settings.sync()

            self.lbl_key_status.setText("✓ All done! API key verified & saved successfully.")
            self.lbl_key_status.setStyleSheet(f"color: {success_color}; font-size: 9pt; font-weight: bold;")

            user = data.get("username", "Unknown")
            daily_use = int(data.get("daily_usage", 0) or 0)
            daily_lim = int(data.get("daily_limit", 100) or 100)
            if daily_lim <= 0:
                daily_lim = 100

            self.lbl_user_and_quota.setText(f"User: @{user}  •  Daily Quota: {daily_use} / {daily_lim} manifests")

            from utils.isp_bypass import connection_status
            route = connection_status if connection_status else "Auto"
            self.lbl_connection_route.setText(f"Connection Route: {route} (Auto Fallback)")
        else:
            self.lbl_key_status.setText(f"Validation failed: {error_msg}")
            self.lbl_key_status.setStyleSheet(f"color: {error_color}; font-size: 9pt;")

    # ──────────────────────────────────────────────────────────────────────────
    # Slide 3: SLSsteam Setup Screen (Enhanced & Well-Fitted Layout)
    # ──────────────────────────────────────────────────────────────────────────

    def _build_slide_sls(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 14, 24, 14)
        layout.setSpacing(10)

        layout.addWidget(self._create_header_bar(current_step=2))

        t_lbl = QLabel("SLSsteam Setup & Environment")
        t_lbl.setStyleSheet(f"font-size: 14pt; font-weight: bold; color: {self.accent_color};")
        layout.addWidget(t_lbl)

        # 2 Rich Cards side-by-side
        cols = QHBoxLayout()
        cols.setSpacing(16)

        # Card 1: Linux & Steam System
        card1 = QFrame()
        card1.setObjectName("WelcomeCard1")
        card1.setStyleSheet(f"""
            QFrame#WelcomeCard1 {{
                background-color: {self.card_bg};
                border: 1px solid {self.card_border};
                border-radius: 8px;
            }}
            QLabel {{
                border: none;
                background: transparent;
            }}
        """)
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(16, 14, 16, 14)
        c1.setSpacing(8)

        c1_title = QLabel("Linux & Steam System")
        c1_title.setStyleSheet("font-size: 10.5pt; font-weight: bold; color: #FFFFFF;")
        c1.addWidget(c1_title)

        distro_name = get_linux_distro()
        self.lbl_distro = QLabel(f"Distro: {distro_name}")
        self.lbl_distro.setStyleSheet("font-size: 9.5pt; color: rgba(255,255,255,0.9);")
        c1.addWidget(self.lbl_distro)

        from core.steam_helpers import get_steam_env
        from utils.slssteam_integration import is_steam_process_running
        env = get_steam_env()
        steam_type = "Flatpak" if env.is_flatpak else "Native"
        steam_detected = env.steam_found()
        steam_running = is_steam_process_running()

        self.lbl_steam_type = QLabel(f"Steam Client: {steam_type}" if steam_detected else "Steam Client: Not Detected")
        self.lbl_steam_type.setStyleSheet("font-size: 9.5pt; color: rgba(255,255,255,0.9);")
        c1.addWidget(self.lbl_steam_type)

        self.lbl_steam_process = QLabel(f"Steam Process: {'Running' if steam_running else 'Stopped'}")
        p_color = self.semantic.get("success", "#81c784") if steam_running else self.semantic.get("warn", "#ffb74d")
        self.lbl_steam_process.setStyleSheet(f"font-size: 9pt; color: {p_color};")
        c1.addWidget(self.lbl_steam_process)

        self.lbl_steam_notice = QLabel(
            "Notice: Fresh Steam install detected. Download any free game to initialize library paths before proceeding."
        )
        self.lbl_steam_notice.setWordWrap(True)
        self.lbl_steam_notice.setStyleSheet(f"font-size: 8pt; color: {self.semantic.get('warn', '#ffb74d')};")
        self.lbl_steam_notice.setVisible(not steam_detected)
        c1.addWidget(self.lbl_steam_notice)

        c1.addStretch()
        cols.addWidget(card1, 1)

        # Card 2: SLSsteam Integration Engine
        card2 = QFrame()
        card2.setObjectName("WelcomeCard2")
        card2.setStyleSheet(f"""
            QFrame#WelcomeCard2 {{
                background-color: {self.card_bg};
                border: 1px solid {self.card_border};
                border-radius: 8px;
            }}
            QLabel {{
                border: none;
                background: transparent;
            }}
        """)
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(16, 14, 16, 14)
        c2.setSpacing(8)

        c2_title = QLabel("SLSsteam Integration Engine")
        c2_title.setStyleSheet("font-size: 10.5pt; font-weight: bold; color: #FFFFFF;")
        c2.addWidget(c2_title)

        self.lbl_sls_status = QLabel("Checking SLSsteam installation...")
        self.lbl_sls_status.setStyleSheet("font-size: 9.5pt;")
        c2.addWidget(self.lbl_sls_status)

        self.lbl_cmd_hint = QLabel("curl -fsSL headcrab.pages.dev | bash")
        self.lbl_cmd_hint.setStyleSheet("font-size: 8.5pt; color: rgba(255,255,255,0.55); font-family: monospace;")
        c2.addWidget(self.lbl_cmd_hint)

        self.btn_run_headcrab = QPushButton("Run Headcrab Installer")
        self.btn_run_headcrab.setFixedHeight(34)
        self.btn_run_headcrab.setStyleSheet(self._btn_secondary_style())
        self.btn_run_headcrab.clicked.connect(self._run_headcrab_installer)
        c2.addWidget(self.btn_run_headcrab)

        self.btn_recheck_sls = QPushButton("Check Again")
        self.btn_recheck_sls.setFixedHeight(34)
        self.btn_recheck_sls.setStyleSheet(self._btn_secondary_style())
        self.btn_recheck_sls.clicked.connect(self._check_sls_installation)
        c2.addWidget(self.btn_recheck_sls)

        self.lbl_api_toggled = QLabel("")
        self.lbl_api_toggled.setStyleSheet(f"font-size: 8.5pt; color: {self.accent_color};")
        c2.addWidget(self.lbl_api_toggled)

        c2.addStretch()
        cols.addWidget(card2, 1)

        layout.addLayout(cols)
        layout.addStretch()

        # Bottom navigation
        nav_row = QHBoxLayout()
        nav_row.setSpacing(12)

        btn_back = QPushButton("← Back")
        btn_back.setFixedHeight(36)
        btn_back.setStyleSheet(self._btn_secondary_style())
        btn_back.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        nav_row.addWidget(btn_back)

        btn_skip = QPushButton("Skip for Now")
        btn_skip.setFixedHeight(36)
        btn_skip.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: rgba(255, 255, 255, 0.5);
                font-size: 8.5pt;
                padding: 0 12px;
            }
            QPushButton:hover {
                color: rgba(255, 255, 255, 0.85);
            }
        """)
        btn_skip.clicked.connect(lambda: self.stack.setCurrentIndex(3))
        nav_row.addWidget(btn_skip)

        nav_row.addStretch()

        btn_next = QPushButton("Next →")
        btn_next.setFixedSize(115, 36)
        btn_next.setStyleSheet(self._btn_primary_style())
        btn_next.clicked.connect(lambda: self.stack.setCurrentIndex(3))
        nav_row.addWidget(btn_next)
        layout.addLayout(nav_row)

        QTimer.singleShot(150, self._check_sls_installation)

        return widget

    def _check_sls_installation(self) -> None:
        self.lbl_sls_status.setText("Checking SLSsteam installation...")
        def _worker():
            try:
                from core.steam_helpers import get_steam_env
                from utils.slssteam_integration import check_slssteam_binary_is_latest
                env = get_steam_env()
                installed = env.sls_installed()
                ver = check_slssteam_binary_is_latest() if installed else {"status": "no_local"}
                self._sls_check_signal.emit(installed, ver)
            except Exception as e:
                self._sls_check_signal.emit(False, {"status": "error", "error": str(e)})

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(bool, dict)
    def _on_sls_check_result(self, installed: bool, ver: dict) -> None:
        success_color = self.semantic.get("success", "#81c784")
        warn_color = self.semantic.get("warn", "#ffb74d")
        error_color = self.semantic.get("error", "#e57373")

        if installed:
            ver_status = ver.get("status", "")
            tag = ver.get("release_tag", "")
            tag_str = f" ({tag})" if tag else ""

            if ver_status == "outdated":
                self.lbl_sls_status.setText(f"SLSsteam is installed but outdated{tag_str}.")
                self.lbl_sls_status.setStyleSheet(f"color: {warn_color}; font-size: 9.5pt;")
                self.lbl_cmd_hint.show()
                self.btn_run_headcrab.show()
                self.btn_recheck_sls.show()
            else:
                self.lbl_sls_status.setText(f"SLSsteam is installed and ready. Go ahead!{tag_str}")
                self.lbl_sls_status.setStyleSheet(f"color: {success_color}; font-size: 9.5pt; font-weight: 500;")
                self.lbl_cmd_hint.hide()
                self.btn_run_headcrab.hide()
                self.btn_recheck_sls.show()

            self._ensure_api_in_config()
        else:
            self.lbl_sls_status.setText("SLSsteam is not detected.")
            self.lbl_sls_status.setStyleSheet(f"color: {error_color}; font-size: 9.5pt;")
            self.lbl_cmd_hint.show()
            self.btn_run_headcrab.show()
            self.btn_recheck_sls.show()

    def _ensure_api_in_config(self) -> None:
        try:
            from core.steam_helpers import get_steam_env
            from utils.yaml_config_manager import update_yaml_boolean_value
            env = get_steam_env()
            if env.sls_config_path.exists():
                update_yaml_boolean_value(env.sls_config_path, "API", True)
                self.settings.setValue("experimental_acf_independent", True)
                self.settings.setValue("sls_config_management", True)
                self.settings.setValue("prompt_steam_restart", False)
                self.settings.sync()
                self.lbl_api_toggled.setText("✓ API: yes automatically enabled in config.yaml")
        except Exception as e:
            logger.debug(f"Failed to auto-toggle API: yes: {e}")

    def _run_headcrab_installer(self) -> None:
        self.btn_run_headcrab.setEnabled(False)
        self.btn_run_headcrab.setText("Running in Terminal...")

        cmd = "curl -fsSL headcrab.pages.dev | bash; echo ''; echo 'Press Enter to finish...'; read"
        terminals = [
            ["konsole", "-e", "bash", "-c", cmd],
            ["gnome-terminal", "--", "bash", "-c", cmd],
            ["alacritty", "-e", "bash", "-c", cmd],
            ["kitty", "bash", "-c", cmd],
            ["xterm", "-e", "bash", "-c", cmd],
            ["xdg-terminal-exec", "bash", "-c", cmd],
        ]

        def _runner():
            proc = None
            for t in terminals:
                bin_path = shutil.which(t[0])
                if bin_path:
                    try:
                        proc = subprocess.Popen([bin_path] + t[1:])
                        break
                    except Exception:
                        pass
            if proc is None:
                proc = subprocess.Popen(["bash", "-c", "curl -fsSL headcrab.pages.dev | bash"])

            if proc:
                proc.wait()

            self._headcrab_done_signal.emit()

        threading.Thread(target=_runner, daemon=True).start()

    @pyqtSlot()
    def _on_headcrab_finished(self) -> None:
        self.btn_run_headcrab.setEnabled(True)
        self.btn_run_headcrab.setText("Run Headcrab Installer")
        self._check_sls_installation()

    # ──────────────────────────────────────────────────────────────────────────
    # Slide 4: at0-m Native Steam Client Downloads
    # ──────────────────────────────────────────────────────────────────────────

    def _build_slide_at0m(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 14, 24, 14)
        layout.setSpacing(10)

        layout.addWidget(self._create_header_bar(current_step=3))

        t_lbl = QLabel("at0-m Native Steam Client Downloads")
        t_lbl.setStyleSheet(f"font-size: 14pt; font-weight: bold; color: {self.accent_color};")
        layout.addWidget(t_lbl)

        sub_lbl = QLabel(
            "Download games, depot manifests, and workshop items directly inside the official Steam client UI."
        )
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9pt;")
        layout.addWidget(sub_lbl)

        # Toggle Card
        card = QFrame()
        card.setObjectName("WelcomeAt0mCard")
        card.setStyleSheet(f"""
            QFrame#WelcomeAt0mCard {{
                background-color: {self.card_bg};
                border: 1px solid {self.card_border};
                border-radius: 8px;
            }}
            QLabel {{
                border: none;
                background: transparent;
            }}
        """)
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(18, 16, 18, 16)
        c_layout.setSpacing(10)

        is_enabled = self.settings.value("enable_at0m", self.settings.value("enable_vapor", False, type=bool), type=bool)
        
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(12)
        toggle_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.toggle_enable_at0m = SwitchToggle(
            checked=is_enabled,
            active_color="#4CAF50",
            inactive_color="#374151",
            width=44,
            height=22,
            parent=self,
        )
        self.chk_enable_at0m = self.toggle_enable_at0m  # Compatibility alias

        lbl_toggle = QLabel("Enable at0-m (Native Steam Client Downloads)")
        lbl_toggle.setStyleSheet("font-size: 10.5pt; font-weight: bold; color: #FFFFFF;")
        lbl_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        lbl_toggle.mousePressEvent = lambda _ev: self.toggle_enable_at0m.setChecked(not self.toggle_enable_at0m.isChecked())

        self.lbl_at0m_badge = QLabel("ENABLED" if is_enabled else "DISABLED")
        self.lbl_at0m_badge.setStyleSheet(self._at0m_badge_style(is_enabled))

        toggle_row.addWidget(self.toggle_enable_at0m)
        toggle_row.addWidget(lbl_toggle)
        toggle_row.addWidget(self.lbl_at0m_badge)
        toggle_row.addStretch()
        c_layout.addLayout(toggle_row)

        self.toggle_enable_at0m.toggled.connect(self._on_at0m_toggled)

        self.lbl_at0m_feedback = QLabel("")
        self.lbl_at0m_feedback.setStyleSheet(f"font-size: 9pt; color: {self.semantic.get('success', '#81c784')}; border: none; background: transparent;")
        self.lbl_at0m_feedback.hide()
        c_layout.addWidget(self.lbl_at0m_feedback)

        lbl_desc = QLabel(
            "When enabled, download.lua and spliced-tickets.lua plugins are deployed to your SLSsteam plugins "
            "directory and Plugins: yes is set in config.yaml. You can also configure this later in Settings → at0-m."
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; border: none; background: transparent;")
        c_layout.addWidget(lbl_desc)

        layout.addWidget(card)
        layout.addStretch()

        # Bottom navigation
        nav_row = QHBoxLayout()
        nav_row.setSpacing(12)

        btn_back = QPushButton("← Back")
        btn_back.setFixedHeight(36)
        btn_back.setStyleSheet(self._btn_secondary_style())
        btn_back.clicked.connect(lambda: self.stack.setCurrentIndex(2))
        nav_row.addWidget(btn_back)

        btn_skip = QPushButton("Skip for Now")
        btn_skip.setFixedHeight(36)
        btn_skip.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: rgba(255, 255, 255, 0.5);
                font-size: 8.5pt;
                padding: 0 12px;
            }
            QPushButton:hover {
                color: rgba(255, 255, 255, 0.85);
            }
        """)
        btn_skip.clicked.connect(lambda: self._advance_to_preferences())
        nav_row.addWidget(btn_skip)

        nav_row.addStretch()

        btn_next = QPushButton("Next →")
        btn_next.setFixedSize(115, 36)
        btn_next.setStyleSheet(self._btn_primary_style())
        btn_next.clicked.connect(lambda: self._advance_to_preferences())
        nav_row.addWidget(btn_next)
        layout.addLayout(nav_row)

        return widget

    def _at0m_badge_style(self, enabled: bool) -> str:
        if enabled:
            return (
                "font-size: 8pt; font-weight: bold; color: #81c784; "
                "background-color: rgba(76, 175, 80, 0.16); "
                "border: 1px solid rgba(76, 175, 80, 0.4); "
                "border-radius: 4px; padding: 2px 7px;"
            )
        return (
            "font-size: 8pt; font-weight: bold; color: rgba(255, 255, 255, 0.5); "
            "background-color: rgba(255, 255, 255, 0.06); "
            "border: 1px solid rgba(255, 255, 255, 0.15); "
            "border-radius: 4px; padding: 2px 7px;"
        )

    def _on_at0m_toggled(self, checked: bool) -> None:
        self.settings.setValue("enable_at0m", checked)
        self.settings.setValue("enable_vapor", checked)
        self.settings.sync()

        if hasattr(self, "lbl_at0m_badge"):
            self.lbl_at0m_badge.setText("ENABLED" if checked else "DISABLED")
            self.lbl_at0m_badge.setStyleSheet(self._at0m_badge_style(checked))

        if checked:
            try:
                from core.steam_helpers import get_steam_env
                from utils.yaml_config_manager import deploy_all_sls_plugins, update_yaml_boolean_value
                env = get_steam_env()
                if env.sls_config_path.exists():
                    update_yaml_boolean_value(env.sls_config_path, "Plugins", True)
                ok, msgs = deploy_all_sls_plugins()
                if ok:
                    self.lbl_at0m_feedback.setText("✓ Plugins injected & Plugins: yes enabled in config.yaml.")
                else:
                    self.lbl_at0m_feedback.setText(f"Plugins notice: {msgs}")
            except Exception as e:
                self.lbl_at0m_feedback.setText(f"Deployment error: {e}")
            self.lbl_at0m_feedback.show()
        else:
            self.lbl_at0m_feedback.setText("at0-m disabled.")
            self.lbl_at0m_feedback.show()

    def _advance_to_preferences(self) -> None:
        self.stack.setCurrentIndex(4)
        self._run_config_check_async()

    # ──────────────────────────────────────────────────────────────────────────
    # Slide 5: Advanced Preferences & Config Fixer (Refined & Glowed-Up!)
    # ──────────────────────────────────────────────────────────────────────────

    def _build_slide_preferences(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 12, 24, 12)
        layout.setSpacing(8)

        layout.addWidget(self._create_header_bar(current_step=4))

        t_lbl = QLabel("Preferences & Maintenance")
        t_lbl.setStyleSheet(f"font-size: 14pt; font-weight: bold; color: {self.accent_color};")
        layout.addWidget(t_lbl)

        # 2 Clean Columns
        cols = QHBoxLayout()
        cols.setSpacing(18)

        # Left Column: Config Fixer & Font Selection (Inheritance removed as requested!)
        c1 = QVBoxLayout()
        c1.setSpacing(6)

        c1_title = QLabel("Config Health & Appearance")
        c1_title.setStyleSheet("font-size: 10.5pt; font-weight: bold; color: #FFFFFF;")
        c1.addWidget(c1_title)

        self.lbl_config_status = QLabel("SLS Config: Checking...")
        self.lbl_config_status.setStyleSheet("font-size: 9pt; color: #FFFFFF;")
        c1.addWidget(self.lbl_config_status)

        cfg_btn_row = QHBoxLayout()
        cfg_btn_row.setSpacing(8)

        self.btn_run_recheck = QPushButton("Re-check Config")
        self.btn_run_recheck.setFixedHeight(34)
        self.btn_run_recheck.setStyleSheet(self._btn_secondary_style())
        self.btn_run_recheck.clicked.connect(self._run_config_check_async)
        cfg_btn_row.addWidget(self.btn_run_recheck)

        # Fix Config button: only enabled/shown if anything is wrong!
        self.btn_run_fix = QPushButton("Fix Config")
        self.btn_run_fix.setFixedHeight(34)
        self.btn_run_fix.setStyleSheet(self._btn_secondary_style())
        self.btn_run_fix.clicked.connect(self._run_config_repair)
        self.btn_run_fix.hide()  # hidden initially until issues detected
        cfg_btn_row.addWidget(self.btn_run_fix)

        c1.addLayout(cfg_btn_row)

        c1.addWidget(QLabel("Interface Font:"))
        self.combo_font = QComboBox()
        self.combo_font.setFixedHeight(30)
        self.combo_font.addItem("Open Sans (Default)", "Open Sans")
        self.combo_font.addItem("Google Sans", "Google Sans")
        self.combo_font.addItem("Roboto", "Roboto")

        saved_font = self.settings.value("font", "Open Sans", type=str)
        f_idx = self.combo_font.findData(saved_font)
        if f_idx >= 0:
            self.combo_font.setCurrentIndex(f_idx)
        c1.addWidget(self.combo_font)

        lbl_fsize_notice = QLabel("Note: Font size adjustment is currently broken.")
        lbl_fsize_notice.setStyleSheet("font-size: 7.5pt; color: rgba(255,255,255,0.45);")
        c1.addWidget(lbl_fsize_notice)

        c1.addStretch()
        cols.addLayout(c1, 1)

        # Right Column: LAN Cache, Download Location, Download Behavior
        c2 = QVBoxLayout()
        c2.setSpacing(6)

        c2_title = QLabel("Downloads & LAN Cache")
        c2_title.setStyleSheet("font-size: 10.5pt; font-weight: bold; color: #FFFFFF;")
        c2.addWidget(c2_title)

        self.chk_lan_cache = QCheckBox("Enable LAN Cache")
        self.chk_lan_cache.setChecked(self.settings.value("use_lancache", True, type=bool))
        c2.addWidget(self.chk_lan_cache)

        lbl_lan_desc = QLabel("Use network saved game files (default: ON)")
        lbl_lan_desc.setStyleSheet("font-size: 8pt; color: rgba(255,255,255,0.55);")
        c2.addWidget(lbl_lan_desc)

        c2.addWidget(QLabel("Default Download Behavior:"))
        self.combo_behavior = QComboBox()
        self.combo_behavior.setFixedHeight(30)
        self.combo_behavior.addItem("Ask every time (Default)", "ask")
        self.combo_behavior.addItem("Always Native Steam (at0-m)", "native")
        self.combo_behavior.addItem("Always ASSella Downloader", "assella")

        cur_act = self.settings.value(
            "at0m_default_download_action",
            self.settings.value("vapor_default_download_action", "ask", type=str),
            type=str,
        )
        b_idx = self.combo_behavior.findData(cur_act)
        if b_idx >= 0:
            self.combo_behavior.setCurrentIndex(b_idx)
        c2.addWidget(self.combo_behavior)

        c2.addWidget(QLabel("Default Download Location:"))
        self.combo_location = QComboBox()
        self.combo_location.setFixedHeight(30)
        self.combo_location.addItem("Ask Every Time (Default)", "")

        from core import steam_helpers
        for lib in steam_helpers.get_steam_libraries():
            self.combo_location.addItem(str(lib), str(lib))

        cur_dl = self.settings.value("default_download_directory", "", type=str)
        l_idx = self.combo_location.findData(cur_dl)
        if l_idx >= 0:
            self.combo_location.setCurrentIndex(l_idx)
        c2.addWidget(self.combo_location)

        c2.addStretch()
        cols.addLayout(c2, 1)

        layout.addLayout(cols)
        layout.addStretch()

        # Bottom row with Apply Recommended & Finish
        b_row = QHBoxLayout()
        b_row.setSpacing(12)

        btn_back = QPushButton("← Back")
        btn_back.setFixedHeight(36)
        btn_back.setStyleSheet(self._btn_secondary_style())
        btn_back.clicked.connect(lambda: self.stack.setCurrentIndex(3))
        b_row.addWidget(btn_back)

        btn_recommended = QPushButton("Apply Recommended")
        btn_recommended.setFixedHeight(36)
        btn_recommended.setStyleSheet(self._btn_secondary_style())
        btn_recommended.clicked.connect(self._apply_recommended_defaults)
        b_row.addWidget(btn_recommended)

        b_row.addStretch()

        btn_finish = QPushButton("Finish & Start Exploring")
        btn_finish.setFixedSize(185, 36)
        btn_finish.setStyleSheet(self._btn_primary_style())
        btn_finish.clicked.connect(self._save_and_finish)
        b_row.addWidget(btn_finish)

        layout.addLayout(b_row)

        return widget

    def _run_config_check_async(self) -> None:
        self.lbl_config_status.setText("SLS Config: Checking...")
        def _worker():
            try:
                from utils.assfixer import check_config_status
                res = check_config_status()
            except Exception as e:
                res = (False, f"Check failed: {e}", [])
            self._config_check_signal.emit(res)

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(tuple)
    def _on_config_check_result(self, result: tuple) -> None:
        needs_repair, status_msg, _ = result
        success_color = self.semantic.get("success", "#81c784")
        warn_color = self.semantic.get("warn", "#ffb74d")
        if not needs_repair:
            self.lbl_config_status.setText("SLS Config: Healthy & Up to date")
            self.lbl_config_status.setStyleSheet(f"color: {success_color}; font-size: 9pt; font-weight: 500;")
            self.btn_run_fix.hide()
        else:
            self.lbl_config_status.setText(f"SLS Config: {status_msg}")
            self.lbl_config_status.setStyleSheet(f"color: {warn_color}; font-size: 9pt;")
            self.btn_run_fix.show()

    def _run_config_repair(self) -> None:
        self.btn_run_fix.setEnabled(False)
        self.btn_run_fix.setText("Fixing...")
        def _worker():
            try:
                from utils.assfixer import fix_config, check_config_status
                fix_config()
                res = check_config_status()
            except Exception as e:
                res = (False, str(e), [])
            self._config_check_signal.emit(res)
        threading.Thread(target=_worker, daemon=True).start()

    def _apply_recommended_defaults(self) -> None:
        self.chk_lan_cache.setChecked(True)
        self.combo_behavior.setCurrentIndex(0)  # Ask every time
        self.combo_location.setCurrentIndex(0)  # Ask every time
        self.combo_font.setCurrentIndex(0)      # Open Sans

        self._run_config_repair()

    def _save_and_finish(self) -> None:
        self.settings.setValue("use_lancache", self.chk_lan_cache.isChecked())
        self.settings.setValue("default_download_directory", self.combo_location.currentData() or "")

        behavior = self.combo_behavior.currentData() or "ask"
        self.settings.setValue("at0m_default_download_action", behavior)
        self.settings.setValue("vapor_default_download_action", behavior)
        self.settings.setValue("native_steam_default_action", behavior)

        chosen_font = self.combo_font.currentData() or "Open Sans"
        self.settings.setValue("font", chosen_font)

        self.settings.setValue("canary_welcome_seen", True)
        self.settings.sync()

        logger.info("[CanaryWelcome] All onboarding preferences saved successfully.")
        self.accept()
