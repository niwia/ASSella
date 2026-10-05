"""
download_backend_dialog.py
==========================
Compact, minimal dialog prompting the user to choose between downloading
via Native Steam (at0-m) or ASSella Downloader.
Features early plugin verification blocking and a 'Remember my choice' option.
Clean, borderless cards with soft glow and accessible contrast.
"""

from typing import Optional
from pathlib import Path
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QColor
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
from utils.plugin_manager import are_plugins_present, is_plugin_check_bypassed
from utils.helpers import get_base_path
from utils.color_utils import get_best_foreground_color

logger = logging.getLogger(__name__)

BACKEND_CANCEL = 0
BACKEND_ASSELLA = 1
BACKEND_NATIVE_STEAM = 2


class DownloadBackendDialog(QDialog):
    """
    Compact dialog asking whether to download via Steam or ASSella.
    Includes early plugin presence check with blocking for Steam option.
    Clean borderless cards with soft hover/selection glow.
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

        self._plugins_available = are_plugins_present()
        self._selected_backend = BACKEND_NATIVE_STEAM if self._plugins_available else BACKEND_ASSELLA

        # Parse accent RGB for glowing backgrounds
        c = QColor(self.accent_color)
        if not c.isValid():
            c = QColor("#6c5ce7")
        self._r, self._g, self._b = c.red(), c.green(), c.blue()

        self.setWindowTitle("Download Method")
        self.setFixedWidth(440)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1c23;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
            }
            QLabel {
                color: #FFFFFF;
                border: none;
                background: transparent;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(16)

        # Title: Download : {game_name} via
        title_lbl = QLabel(f"Download : <b style='color: {self.accent_color};'>{self.game_name}</b> via")
        title_lbl.setStyleSheet("font-size: 11pt; color: rgba(255, 255, 255, 0.95); border: none; background: transparent;")
        title_lbl.setWordWrap(True)
        layout.addWidget(title_lbl)

        # Options Container (Two borderless cards side by side)
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(14)

        # 1. Steam Card (Left)
        self.steam_card = QFrame()
        self.steam_card.setObjectName("steam_card")
        self.steam_card.setCursor(Qt.CursorShape.PointingHandCursor if self._plugins_available else Qt.CursorShape.ForbiddenCursor)
        self.steam_card.setFixedHeight(120)
        steam_vbox = QVBoxLayout(self.steam_card)
        steam_vbox.setContentsMargins(12, 14, 12, 12)
        steam_vbox.setSpacing(6)
        steam_vbox.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Steam Logo
        self.steam_icon_lbl = QLabel()
        self.steam_icon_lbl.setObjectName("steam_icon_lbl")
        steam_pix = self._load_logo("steam.png")
        if not steam_pix.isNull():
            self.steam_icon_lbl.setPixmap(steam_pix.scaled(46, 46, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        steam_vbox.addWidget(self.steam_icon_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        steam_name = QLabel("Steam")
        steam_name.setStyleSheet("font-size: 10pt; font-weight: bold; border: none; background: transparent;")
        steam_vbox.addWidget(steam_name, alignment=Qt.AlignmentFlag.AlignCenter)

        self.steam_status_lbl = QLabel()
        self.steam_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        phys_present = are_plugins_present(ignore_bypass=True)
        if not phys_present:
            if is_plugin_check_bypassed():
                self.steam_status_lbl.setStyleSheet("font-size: 7.5pt; color: #ffb74d; border: none; background: transparent; font-weight: 500;")
                self.steam_status_lbl.setText("Custom Plugins")
                self.steam_status_lbl.setVisible(True)
            else:
                self.steam_status_lbl.setStyleSheet("font-size: 7.5pt; color: #ff6b6b; border: none; background: transparent; font-weight: 500;")
                self.steam_status_lbl.setText("Plugins Missing")
                self.steam_status_lbl.setVisible(True)
        else:
            self.steam_status_lbl.setVisible(False)
        steam_vbox.addWidget(self.steam_status_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        self.steam_card.mousePressEvent = lambda e: self._select_backend(BACKEND_NATIVE_STEAM)
        cards_layout.addWidget(self.steam_card)

        # 2. ASSella Card (Right)
        self.assella_card = QFrame()
        self.assella_card.setObjectName("assella_card")
        self.assella_card.setCursor(Qt.CursorShape.PointingHandCursor)
        self.assella_card.setFixedHeight(120)
        assella_vbox = QVBoxLayout(self.assella_card)
        assella_vbox.setContentsMargins(12, 14, 12, 12)
        assella_vbox.setSpacing(6)
        assella_vbox.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ASSella Logo
        self.assella_icon_lbl = QLabel()
        self.assella_icon_lbl.setObjectName("assella_icon_lbl")
        assella_pix = self._load_logo("accela.png")
        if not assella_pix.isNull():
            self.assella_icon_lbl.setPixmap(assella_pix.scaled(46, 46, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        assella_vbox.addWidget(self.assella_icon_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        assella_name = QLabel("ASSella")
        assella_name.setStyleSheet("font-size: 10pt; font-weight: bold; border: none; background: transparent;")
        assella_vbox.addWidget(assella_name, alignment=Qt.AlignmentFlag.AlignCenter)

        assella_sub = QLabel("Built-in")
        assella_sub.setStyleSheet("font-size: 7.5pt; color: rgba(255, 255, 255, 0.45); border: none; background: transparent;")
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
                spacing: 7px;
                border: none;
                background: transparent;
            }
            QCheckBox::indicator {
                width: 15px;
                height: 15px;
                border: 1px solid rgba(255, 255, 255, 0.25);
                border-radius: 3px;
                background: rgba(255, 255, 255, 0.05);
            }
            QCheckBox::indicator:hover {
                border-color: rgba(255, 255, 255, 0.5);
            }
            QCheckBox::indicator:checked {
                background: %s;
                border-color: %s;
            }
        """ % (self.accent_color, self.accent_color))
        layout.addWidget(self.remember_chk)

        # Custom plugin warning (visible only when Steam is chosen and bypass is active with physical plugins missing)
        self.custom_plugin_warn_lbl = QLabel("⚠️ Custom plugins active — third-party plugin support cannot be guaranteed!")
        self.custom_plugin_warn_lbl.setStyleSheet("font-size: 7.5pt; color: #ffb74d; border: none; background: transparent;")
        self.custom_plugin_warn_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.custom_plugin_warn_lbl.setWordWrap(True)
        self.custom_plugin_warn_lbl.setVisible(
            self._selected_backend == BACKEND_NATIVE_STEAM and is_plugin_check_bypassed() and not are_plugins_present(ignore_bypass=True)
        )
        layout.addWidget(self.custom_plugin_warn_lbl)

        # Bottom buttons row: Cancel / Proceed
        bot_layout = QHBoxLayout()
        bot_layout.setContentsMargins(0, 4, 0, 0)
        bot_layout.setSpacing(10)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancel_btn")
        self.cancel_btn.setFixedSize(85, 30)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton#cancel_btn {
                background-color: rgba(255, 255, 255, 0.06);
                color: rgba(255, 255, 255, 0.85);
                border: none;
                border-radius: 6px;
                font-size: 8.5pt;
            }
            QPushButton#cancel_btn:hover {
                background-color: rgba(255, 255, 255, 0.12);
                color: #FFFFFF;
            }
        """)
        self.cancel_btn.clicked.connect(self._on_cancel)
        bot_layout.addWidget(self.cancel_btn)

        bot_layout.addStretch()

        # Foreground color chosen for maximum contrast against accent
        btn_fg = get_best_foreground_color(self.accent_color, dark_color="#121214", light_color="#FFFFFF")

        self.proceed_btn = QPushButton("Proceed")
        self.proceed_btn.setObjectName("proceed_btn")
        self.proceed_btn.setFixedSize(85, 30)
        self.proceed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.proceed_btn.setStyleSheet(f"""
            QPushButton#proceed_btn {{
                background-color: {self.accent_color};
                color: {btn_fg};
                border: none;
                border-radius: 6px;
                font-size: 8.5pt;
                font-weight: bold;
            }}
            QPushButton#proceed_btn:hover {{
                opacity: 0.9;
            }}
            QPushButton#proceed_btn:disabled {{
                background-color: rgba(255, 255, 255, 0.05);
                color: rgba(255, 255, 255, 0.25);
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
        if hasattr(self, "custom_plugin_warn_lbl"):
            self.custom_plugin_warn_lbl.setVisible(
                backend == BACKEND_NATIVE_STEAM and is_plugin_check_bypassed() and not are_plugins_present(ignore_bypass=True)
            )
        self._update_cards_ui()

    def _update_cards_ui(self):
        # Clean, borderless cards with soft background glow on select/hover
        # Explicit child selector ensures NO inner labels ever get borders or backgrounds
        sel_style = f"""
            QFrame {{
                background-color: rgba({self._r}, {self._g}, {self._b}, 0.20);
                border: none;
                border-radius: 10px;
            }}
            QFrame:hover {{
                background-color: rgba({self._r}, {self._g}, {self._b}, 0.26);
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
                border-radius: 10px;
            }
            QFrame:hover {
                background-color: rgba(255, 255, 255, 0.09);
            }
            QLabel {
                border: none;
                background: transparent;
            }
        """
        disabled_style = """
            QFrame {
                background-color: rgba(255, 255, 255, 0.02);
                border: none;
                border-radius: 10px;
            }
            QLabel {
                border: none;
                background: transparent;
            }
        """

        # Update Steam Card
        if not self._plugins_available:
            self.steam_card.setStyleSheet(disabled_style)
            self.steam_card.setEnabled(False)
        elif self._selected_backend == BACKEND_NATIVE_STEAM:
            self.steam_card.setStyleSheet(sel_style)
            self.steam_card.setEnabled(True)
        else:
            self.steam_card.setStyleSheet(unsel_style)
            self.steam_card.setEnabled(True)

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
        phys_present = are_plugins_present(ignore_bypass=True)
        bypassed = is_plugin_check_bypassed()
        self.plugin_help_btn.setVisible(not self._plugins_available)
        if not phys_present:
            if bypassed:
                self.steam_status_lbl.setStyleSheet("font-size: 7.5pt; color: #ffb74d; border: none; background: transparent; font-weight: 500;")
                self.steam_status_lbl.setText("Custom Plugins")
                self.steam_status_lbl.setVisible(True)
            else:
                self.steam_status_lbl.setStyleSheet("font-size: 7.5pt; color: #ff6b6b; border: none; background: transparent; font-weight: 500;")
                self.steam_status_lbl.setText("Plugins Missing")
                self.steam_status_lbl.setVisible(True)
        else:
            self.steam_status_lbl.setVisible(False)

        self.steam_card.setCursor(Qt.CursorShape.PointingHandCursor if self._plugins_available else Qt.CursorShape.ForbiddenCursor)
        if self._plugins_available and self._selected_backend != BACKEND_ASSELLA:
            self._selected_backend = BACKEND_NATIVE_STEAM
        if hasattr(self, "custom_plugin_warn_lbl"):
            self.custom_plugin_warn_lbl.setVisible(
                self._selected_backend == BACKEND_NATIVE_STEAM and bypassed and not phys_present
            )
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
