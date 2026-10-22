"""
native_steam_action_dialog.py
=============================
Minimal compact dialog prompting how to proceed with Steam installation:
  - Option 1: "Start steam download" (starts download immediately in Steam)
  - Option 2: "Download later" (adds game manifest to Steam library)
  - "Remember my choice" checkbox (unchecked by default)
  - Clean borderless cards with soft glow and accessible contrast.
"""

from typing import Optional
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QRadioButton,
    QButtonGroup,
    QFrame,
    QWidget,
)

from utils.settings import get_settings
from utils.color_utils import get_best_foreground_color

logger = logging.getLogger(__name__)

ACTION_CANCEL = 0
ACTION_DOWNLOAD = 1
ACTION_ADD_ONLY = 2
ACTION_TRACK = 1
ACTION_HANDOFF = 2


from ui.dialog_titlebar import DialogTitleBar


class NativeSteamActionDialog(QDialog):
    """
    Minimal dialog prompting how to proceed with Steam installation:
      - Start steam download
      - Download later
    Clean borderless cards with custom ASSella title bar and full-width buttons.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        app_id: str = "",
        game_name: str = "",
        accent_color: str = "#6c5ce7",
    ):
        super().__init__(parent)
        self.app_id = str(app_id)
        self.game_name = game_name or f"App {app_id}"
        self.accent_color = accent_color
        self._action = ACTION_CANCEL
        self.settings = get_settings()

        # Parse accent RGB for glowing backgrounds
        c = QColor(self.accent_color)
        if not c.isValid():
            c = QColor("#6c5ce7")
        self._r, self._g, self._b = c.red(), c.green(), c.blue()

        self.setWindowTitle("How to proceed?")
        self.setFixedWidth(420)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        # Outer Container Frame
        self.container = QFrame(self)
        self.container.setObjectName("dialogContainer")
        self.container.setStyleSheet("""
            QFrame#dialogContainer {
                background-color: #1a1c23;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }
            * {
                outline: none;
            }
            QLabel {
                color: #FFFFFF;
                border: none;
                background: transparent;
            }
        """)
        root.addWidget(self.container)

        container_lay = QVBoxLayout(self.container)
        container_lay.setContentsMargins(0, 0, 0, 16)
        container_lay.setSpacing(12)

        # Custom Themed Title Bar
        self.title_bar = DialogTitleBar(
            self,
            title="How to proceed?",
            can_minimize=False,
            can_maximize=False,
            use_power_close=True,
        )
        container_lay.addWidget(self.title_bar)

        # Body area
        body_lay = QVBoxLayout()
        body_lay.setContentsMargins(20, 4, 20, 4)
        body_lay.setSpacing(14)
        container_lay.addLayout(body_lay)

        # Header title
        title_lbl = QLabel("How to proceed?")
        title_lbl.setStyleSheet("font-size: 11pt; font-weight: bold; color: rgba(255, 255, 255, 0.95); border: none; background: transparent;")
        body_lay.addWidget(title_lbl)

        self.button_group = QButtonGroup(self)

        # Option 1: Start steam download
        self.start_frame = QFrame()
        self.start_frame.setObjectName("start_frame")
        self.start_frame.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_frame.setFixedHeight(48)
        start_layout = QHBoxLayout(self.start_frame)
        start_layout.setContentsMargins(16, 8, 16, 8)
        start_layout.setSpacing(12)

        start_lbl = QLabel("Start steam download")
        start_lbl.setStyleSheet("font-size: 9.5pt; color: #FFFFFF; font-weight: 500; border: none; background: transparent;")
        start_layout.addWidget(start_lbl)
        start_layout.addStretch()

        self.start_radio = QRadioButton()
        self.start_radio.setChecked(True)
        self.start_radio.setCursor(Qt.CursorShape.PointingHandCursor)
        self._style_radio(self.start_radio)
        self.button_group.addButton(self.start_radio)
        start_layout.addWidget(self.start_radio)

        self.start_frame.mousePressEvent = lambda e: self.start_radio.setChecked(True)
        body_lay.addWidget(self.start_frame)

        # Option 2: Download later
        self.later_frame = QFrame()
        self.later_frame.setObjectName("later_frame")
        self.later_frame.setCursor(Qt.CursorShape.PointingHandCursor)
        self.later_frame.setFixedHeight(48)
        later_layout = QHBoxLayout(self.later_frame)
        later_layout.setContentsMargins(16, 8, 16, 8)
        later_layout.setSpacing(12)

        later_lbl = QLabel("Download later")
        later_lbl.setStyleSheet("font-size: 9.5pt; color: #FFFFFF; font-weight: 500; border: none; background: transparent;")
        later_layout.addWidget(later_lbl)
        later_layout.addStretch()

        self.later_radio = QRadioButton()
        self.later_radio.setChecked(False)
        self.later_radio.setCursor(Qt.CursorShape.PointingHandCursor)
        self._style_radio(self.later_radio)
        self.button_group.addButton(self.later_radio)
        later_layout.addWidget(self.later_radio)

        self.later_frame.mousePressEvent = lambda e: self.later_radio.setChecked(True)
        body_lay.addWidget(self.later_frame)

        # Synchronize frame selection styles when radios change
        self.start_radio.toggled.connect(self._update_styles)
        self.later_radio.toggled.connect(self._update_styles)
        self._update_styles()

        # Remember my choice Checkbox
        self.remember_chk = QCheckBox("Remember my choice")
        self.remember_chk.setChecked(False)
        self.remember_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remember_chk.setStyleSheet("""
            QCheckBox {
                font-size: 8.5pt;
                color: rgba(255, 255, 255, 0.75);
                spacing: 7px;
                border: none;
                outline: none;
                background: transparent;
            }
            QCheckBox:focus {
                border: none;
                outline: none;
            }
            QCheckBox::indicator {
                width: 15px;
                height: 15px;
                border: none;
                border-radius: 3px;
                background: rgba(255, 255, 255, 0.12);
                outline: none;
            }
            QCheckBox::indicator:hover {
                background: rgba(255, 255, 255, 0.22);
            }
            QCheckBox::indicator:checked {
                background: %s;
                border: none;
            }
            QCheckBox::indicator:focus {
                border: none;
                outline: none;
            }
        """ % self.accent_color)
        body_lay.addWidget(self.remember_chk)

        # Bottom row: Cancel / Proceed (big buttons filling the bottom width)
        bot_layout = QHBoxLayout()
        bot_layout.setContentsMargins(0, 8, 0, 0)
        bot_layout.setSpacing(10)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancel_btn")
        self.cancel_btn.setFixedHeight(38)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton#cancel_btn {
                background-color: rgba(255, 255, 255, 0.06);
                color: rgba(255, 255, 255, 0.85);
                border: none;
                outline: none;
                border-radius: 6px;
                font-size: 9pt;
                font-weight: 500;
            }
            QPushButton#cancel_btn:hover {
                background-color: rgba(255, 255, 255, 0.12);
                color: #FFFFFF;
            }
            QPushButton#cancel_btn:focus {
                outline: none;
                border: none;
            }
        """)
        self.cancel_btn.clicked.connect(self._on_cancel)
        bot_layout.addWidget(self.cancel_btn, 1)

        # Foreground color chosen for maximum contrast against accent
        btn_fg = get_best_foreground_color(self.accent_color, dark_color="#121214", light_color="#FFFFFF")

        self.proceed_btn = QPushButton("Proceed")
        self.proceed_btn.setObjectName("proceed_btn")
        self.proceed_btn.setFixedHeight(38)
        self.proceed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.proceed_btn.setStyleSheet(f"""
            QPushButton#proceed_btn {{
                background-color: {self.accent_color};
                color: {btn_fg};
                border: none;
                outline: none;
                border-radius: 6px;
                font-size: 9pt;
                font-weight: bold;
            }}
            QPushButton#proceed_btn:hover {{
                opacity: 0.9;
            }}
            QPushButton#proceed_btn:focus {{
                outline: none;
                border: none;
            }}
        """)
        self.proceed_btn.clicked.connect(self._on_proceed)
        bot_layout.addWidget(self.proceed_btn, 1)

        body_lay.addLayout(bot_layout)

    def _style_radio(self, radio: QRadioButton):
        radio.setStyleSheet(f"""
            QRadioButton {{
                background: transparent;
                border: none;
                outline: none;
            }}
            QRadioButton:focus {{
                border: none;
                outline: none;
            }}
            QRadioButton::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: none;
                outline: none;
                background-color: rgba(255, 255, 255, 0.12);
            }}
            QRadioButton::indicator:hover {{
                background-color: rgba(255, 255, 255, 0.22);
            }}
            QRadioButton::indicator:checked {{
                background-color: {self.accent_color};
                border: none;
            }}
        """)

    def _update_styles(self):
        sel_style = f"""
            QFrame {{
                background-color: rgba({self._r}, {self._g}, {self._b}, 0.18);
                border: none;
                border-radius: 8px;
            }}
            QFrame:hover {{
                background-color: rgba({self._r}, {self._g}, {self._b}, 0.24);
            }}
            QLabel {{
                border: none;
                background: transparent;
            }}
        """
        unsel_style = """
            QFrame {
                background-color: rgba(255, 255, 255, 0.04);
                border: none;
                border-radius: 8px;
            }
            QFrame:hover {
                background-color: rgba(255, 255, 255, 0.08);
            }
            QLabel {
                border: none;
                background: transparent;
            }
        """
        self.start_frame.setStyleSheet(sel_style if self.start_radio.isChecked() else unsel_style)
        self.later_frame.setStyleSheet(sel_style if self.later_radio.isChecked() else unsel_style)

    def _on_proceed(self):
        if self.start_radio.isChecked():
            self._action = ACTION_DOWNLOAD
        else:
            self._action = ACTION_ADD_ONLY

        if self.should_remember():
            val = "immediate" if self._action == ACTION_DOWNLOAD else "add_only"
            self.settings.setValue("at0m_start_download_action", val)
            self.settings.setValue("vapor_start_download_action", val)
            logger.info(f"[NativeSteamActionDialog] Saved default start download action: {val}")

        self.accept()

    def _on_cancel(self):
        self._action = ACTION_CANCEL
        self.reject()

    def get_action(self) -> int:
        return self._action

    def should_remember(self) -> bool:
        return self.remember_chk.isChecked()
