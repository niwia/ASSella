"""
download_backend_dialog.py
==========================
Compact, minimal dialog prompting the user to choose between downloading
via Native Steam (at0-m) or ASSella Downloader.
Features early plugin verification blocking and a 'Remember my choice' option.
"""

from typing import Optional
from pathlib import Path
import logging

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QIcon
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QFrame,
    QWidget,
)

from utils.settings import get_settings
from utils.plugin_manager import are_plugins_present
from utils.helpers import get_base_path

logger = logging.getLogger(__name__)

BACKEND_CANCEL = 0
BACKEND_ASSELLA = 1
BACKEND_NATIVE_STEAM = 2


class DownloadBackendDialog(QDialog):
    """
    Compact dialog asking whether to download via Steam or ASSella.
    Includes early plugin presence check with blocking for Steam option.
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
        self.settings = get_settings()

        self._selected_backend = BACKEND_NATIVE_STEAM
        self._plugins_available = are_plugins_present()
        if not self._plugins_available:
            self._selected_backend = BACKEND_ASSELLA

        self.setWindowTitle("Download Method")
        self.setFixedWidth(440)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1c23;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }
            QLabel {
                color: #FFFFFF;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)

        # Title: Download: {game_name} via
        title_lbl = QLabel(f"Download : <b style='color: {self.accent_color};'>{self.game_name}</b> via")
        title_lbl.setStyleSheet("font-size: 11pt; color: rgba(255, 255, 255, 0.95);")
        title_lbl.setWordWrap(True)
        layout.addWidget(title_lbl)

        # Options Container (Two boxes side by side)
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(12)

        # 1. Steam Card (Left)
        self.steam_card = QFrame()
        self.steam_card.setCursor(Qt.CursorShape.PointingHandCursor if self._plugins_available else Qt.CursorShape.ForbiddenCursor)
        self.steam_card.setFixedHeight(115)
        steam_vbox = QVBoxLayout(self.steam_card)
        steam_vbox.setContentsMargins(10, 12, 10, 10)
        steam_vbox.setSpacing(6)
        steam_vbox.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Steam Logo
        self.steam_icon_lbl = QLabel()
        steam_pix = self._load_logo("steam.png")
        if not steam_pix.isNull():
            self.steam_icon_lbl.setPixmap(steam_pix.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        steam_vbox.addWidget(self.steam_icon_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        steam_name = QLabel("Steam")
        steam_name.setStyleSheet("font-size: 10pt; font-weight: bold; background: transparent;")
        steam_vbox.addWidget(steam_name, alignment=Qt.AlignmentFlag.AlignCenter)

        self.steam_status_lbl = QLabel()
        self.steam_status_lbl.setStyleSheet("font-size: 7.5pt; color: #ff6b6b; background: transparent; font-weight: 500;")
        self.steam_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if not self._plugins_available:
            self.steam_status_lbl.setText("Plugins Missing")
            self.steam_status_lbl.setVisible(True)
        else:
            self.steam_status_lbl.setVisible(False)
        steam_vbox.addWidget(self.steam_status_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        self.steam_card.mousePressEvent = lambda e: self._select_backend(BACKEND_NATIVE_STEAM)
        cards_layout.addWidget(self.steam_card)

        # 2. ASSella Card (Right)
        self.assella_card = QFrame()
        self.assella_card.setCursor(Qt.CursorShape.PointingHandCursor)
        self.assella_card.setFixedHeight(115)
        assella_vbox = QVBoxLayout(self.assella_card)
        assella_vbox.setContentsMargins(10, 12, 10, 10)
        assella_vbox.setSpacing(6)
        assella_vbox.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ASSella Logo
        self.assella_icon_lbl = QLabel()
        assella_pix = self._load_logo("accela.png")
        if not assella_pix.isNull():
            self.assella_icon_lbl.setPixmap(assella_pix.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        assella_vbox.addWidget(self.assella_icon_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        assella_name = QLabel("ASSella")
        assella_name.setStyleSheet("font-size: 10pt; font-weight: bold; background: transparent;")
        assella_vbox.addWidget(assella_name, alignment=Qt.AlignmentFlag.AlignCenter)

        # Spacer placeholder for alignment matching steam card
        assella_sub = QLabel("Built-in")
        assella_sub.setStyleSheet("font-size: 7.5pt; color: rgba(255, 255, 255, 0.45); background: transparent;")
        assella_vbox.addWidget(assella_sub, alignment=Qt.AlignmentFlag.AlignCenter)

        self.assella_card.mousePressEvent = lambda e: self._select_backend(BACKEND_ASSELLA)
        cards_layout.addWidget(self.assella_card)

        layout.addLayout(cards_layout)

        # Plugin Missing Helper Link/Button (if blocked)
        self.plugin_help_btn = QPushButton("Enable Plugin Support in Settings")
        self.plugin_help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.plugin_help_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #ff9800;
                font-size: 8pt;
                text-decoration: underline;
                border: none;
                padding: 0px;
            }
            QPushButton:hover {
                color: #ffb74d;
            }
        """)
        self.plugin_help_btn.clicked.connect(self._open_settings_atom)
        self.plugin_help_btn.setVisible(not self._plugins_available)
        layout.addWidget(self.plugin_help_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Remember my choice Checkbox
        self.remember_chk = QCheckBox("Remember my choice")
        self.remember_chk.setChecked(False)
        self.remember_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remember_chk.setStyleSheet("""
            QCheckBox {
                font-size: 8.5pt;
                color: rgba(255, 255, 255, 0.75);
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 3px;
                background: rgba(255, 255, 255, 0.05);
            }
            QCheckBox::indicator:checked {
                background: %s;
                border-color: %s;
            }
        """ % (self.accent_color, self.accent_color))
        layout.addWidget(self.remember_chk)

        # Bottom buttons row: Cancel / Proceed
        bot_layout = QHBoxLayout()
        bot_layout.setContentsMargins(0, 4, 0, 0)
        bot_layout.setSpacing(10)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedSize(85, 30)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                color: rgba(255, 255, 255, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                color: #FFFFFF;
            }
        """)
        self.cancel_btn.clicked.connect(self._on_cancel)
        bot_layout.addWidget(self.cancel_btn)

        bot_layout.addStretch()

        self.proceed_btn = QPushButton("Proceed")
        self.proceed_btn.setFixedSize(85, 30)
        self.proceed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.proceed_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: #FFFFFF;
                border: 1px solid {self.accent_color};
                border-radius: 6px;
                font-size: 8.5pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.05);
                color: rgba(255, 255, 255, 0.25);
                border: 1px solid rgba(255, 255, 255, 0.08);
            }}
        """)
        self.proceed_btn.clicked.connect(self._on_proceed)
        bot_layout.addWidget(self.proceed_btn)

        layout.addLayout(bot_layout)

        self._update_cards_ui()

    def _load_logo(self, filename: str) -> QPixmap:
        """Load logo from bundled src/res/logo or ~/.local/share/ACCELA/Logo."""
        candidates = [
            Path(__file__).resolve().parent.parent.parent / "res" / "logo" / filename,
            get_base_path() / "Logo" / filename,
            Path.home() / ".local/share/ACCELA/Logo" / filename,
        ]
        for c in candidates:
            if c.is_file():
                return QPixmap(str(c))
        return QPixmap()

    def _select_backend(self, backend: int):
        if backend == BACKEND_NATIVE_STEAM and not self._plugins_available:
            return
        self._selected_backend = backend
        self._update_cards_ui()

    def _update_cards_ui(self):
        sel_style = f"""
            QFrame {{
                background-color: rgba(255, 255, 255, 0.08);
                border: 2px solid {self.accent_color};
                border-radius: 8px;
            }}
        """
        unsel_style = """
            QFrame {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
            }
            QFrame:hover {
                background-color: rgba(255, 255, 255, 0.07);
                border: 1px solid rgba(255, 255, 255, 0.25);
            }
        """
        disabled_style = """
            QFrame {
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 8px;
            }
        """

        # Update Steam Card
        if not self._plugins_available:
            self.steam_card.setStyleSheet(disabled_style)
            self.steam_card.setEnabled(False)
            self.steam_icon_lbl.setStyleSheet("opacity: 0.35;")
        elif self._selected_backend == BACKEND_NATIVE_STEAM:
            self.steam_card.setStyleSheet(sel_style)
            self.steam_card.setEnabled(True)
            self.steam_icon_lbl.setStyleSheet("")
        else:
            self.steam_card.setStyleSheet(unsel_style)
            self.steam_card.setEnabled(True)
            self.steam_icon_lbl.setStyleSheet("")

        # Update ASSella Card
        if self._selected_backend == BACKEND_ASSELLA:
            self.assella_card.setStyleSheet(sel_style)
        else:
            self.assella_card.setStyleSheet(unsel_style)

    def _open_settings_atom(self):
        """Open settings dialog directly to at0-m tab and recheck plugins on return."""
        parent = self.parent()
        opened = False
        while parent:
            if hasattr(parent, "open_settings"):
                parent.open_settings(initial_tab="at0-m")
                opened = True
                break
            parent = getattr(parent, "parent", lambda: None)()

        if not opened:
            try:
                from ui.dialogs.settings import SettingsDialog
                dlg = SettingsDialog(self, initial_tab="at0-m")
                dlg.exec()
            except Exception as e:
                logger.error(f"[DownloadBackendDialog] Error opening settings: {e}")

        # Re-check plugin availability
        self._plugins_available = are_plugins_present()
        self.plugin_help_btn.setVisible(not self._plugins_available)
        self.steam_status_lbl.setVisible(not self._plugins_available)
        self.steam_card.setCursor(Qt.CursorShape.PointingHandCursor if self._plugins_available else Qt.CursorShape.ForbiddenCursor)
        if self._plugins_available:
            self._selected_backend = BACKEND_NATIVE_STEAM
        self._update_cards_ui()

    def _on_proceed(self):
        self._choice = self._selected_backend
        if self.should_remember():
            val = "native" if self._choice == BACKEND_NATIVE_STEAM else "assella"
            self.settings.setValue("at0m_default_download_action", val)
            self.settings.setValue("vapor_default_download_action", val)
            logger.info(f"[DownloadBackendDialog] Saved default download method: {val}")
        self.accept()

    def _on_cancel(self):
        self._choice = BACKEND_CANCEL
        self.reject()

    def get_choice(self) -> int:
        return self._choice

    def should_remember(self) -> bool:
        return self.remember_chk.isChecked()
