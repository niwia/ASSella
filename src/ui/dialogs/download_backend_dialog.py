"""
download_backend_dialog.py
==========================
Dialog prompting the user to choose between downloading via ASSella
Downloader or downloading natively via the Steam client (at0-m).
"""

from typing import Optional
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

BACKEND_CANCEL = 0
BACKEND_ASSELLA = 1
BACKEND_NATIVE_STEAM = 2


class DownloadBackendDialog(QDialog):
    """
    Prompt user to choose between ASSella Downloader and Native Steam (at0-m).
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
        self._choice = BACKEND_CANCEL

        self.setWindowTitle("Choose Download Method")
        self.setFixedSize(500, 310)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1c23;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 12px;
            }
            QLabel {
                color: #FFFFFF;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # Title
        title_lbl = QLabel("Choose Download Method")
        title_lbl.setStyleSheet(f"font-size: 14pt; font-weight: bold; color: {self.accent_color};")
        layout.addWidget(title_lbl)

        # Target info
        info_lbl = QLabel(f"Target: <b style='color: #FFFFFF;'>{self.game_name}</b> (AppID: {self.app_id})")
        info_lbl.setStyleSheet("font-size: 9.5pt; color: rgba(255, 255, 255, 0.85);")
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

        desc_lbl = QLabel("How would you like to install this game?")
        desc_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.65);")
        layout.addWidget(desc_lbl)

        # Option 1: Native Steam (Vapor)
        self.native_btn = QPushButton()
        self.native_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.native_btn.setFixedHeight(64)
        self.native_btn.clicked.connect(self._on_native_clicked)
        n_layout = QVBoxLayout(self.native_btn)
        n_layout.setContentsMargins(14, 8, 14, 8)
        n_layout.setSpacing(2)

        n_title = QLabel("Download using Native Steam")
        n_title.setStyleSheet("font-size: 10pt; font-weight: bold; color: #FFFFFF; background: transparent;")
        n_sub = QLabel("Direct Steam integration via SLSsteam. Steam handles downloading and updates.")
        n_sub.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.65); background: transparent;")
        n_sub.setWordWrap(True)
        n_layout.addWidget(n_title)
        n_layout.addWidget(n_sub)
        self._style_option_btn(self.native_btn, primary=True)
        layout.addWidget(self.native_btn)

        # Option 2: ASSella Downloader
        self.assella_btn = QPushButton()
        self.assella_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.assella_btn.setFixedHeight(64)
        self.assella_btn.clicked.connect(self._on_assella_clicked)
        a_layout = QVBoxLayout(self.assella_btn)
        a_layout.setContentsMargins(14, 8, 14, 8)
        a_layout.setSpacing(2)

        a_title = QLabel("Download using ASSella")
        a_title.setStyleSheet("font-size: 10pt; font-weight: bold; color: #FFFFFF; background: transparent;")
        a_sub = QLabel("Classic downloader with branch selection, depot checklist, and storage picker.")
        a_sub.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.65); background: transparent;")
        a_sub.setWordWrap(True)
        a_layout.addWidget(a_title)
        a_layout.addWidget(a_sub)
        self._style_option_btn(self.assella_btn, primary=False)
        layout.addWidget(self.assella_btn)

        # Bottom row: Cancel button
        bot_layout = QHBoxLayout()
        bot_layout.setContentsMargins(0, 4, 0, 0)
        bot_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(80, 28)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.07);
                color: rgba(255, 255, 255, 0.8);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.14);
                color: #FFFFFF;
            }
        """)
        cancel_btn.clicked.connect(self._on_cancel_clicked)
        bot_layout.addWidget(cancel_btn)

        layout.addLayout(bot_layout)

    def _style_option_btn(self, btn: QPushButton, primary: bool):
        border_color = self.accent_color if primary else "rgba(255, 255, 255, 0.18)"
        bg_color = "rgba(255, 255, 255, 0.05)"
        hover_bg = "rgba(255, 255, 255, 0.10)"
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 8px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {hover_bg};
                border-color: {self.accent_color};
            }}
        """)

    def _on_assella_clicked(self):
        self._choice = BACKEND_ASSELLA
        self.accept()

    def _on_native_clicked(self):
        self._choice = BACKEND_NATIVE_STEAM
        self.accept()

    def _on_cancel_clicked(self):
        self._choice = BACKEND_CANCEL
        self.reject()

    def get_choice(self) -> int:
        return self._choice
