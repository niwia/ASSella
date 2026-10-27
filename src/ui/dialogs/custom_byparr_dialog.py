"""
custom_byparr_dialog.py - Configuration dialog for custom Cloudflare Turnstile bypasser.

Allows users to point ASSella to their own self-hosted or remote Byparr / FlareSolverr
instance, optionally configure authorization tokens, and verify connectivity against SteamDB.
"""

import logging
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.dialog_titlebar import DialogTitleBar
from utils.settings import get_settings
from core.steamdb_scraper import (
    BYPARR_DEFAULT_URL,
    ByparrManager,
    test_byparr_connection,
)

logger = logging.getLogger(__name__)


class _TestConnectionWorker(QThread):
    finished_signal = pyqtSignal(bool, str, float)

    def __init__(self, url: str, auth: str):
        super().__init__()
        self.url = url
        self.auth = auth

    def run(self):
        try:
            ok, msg, elapsed = test_byparr_connection(self.url, self.auth, timeout_seconds=15)
            self.finished_signal.emit(ok, msg, elapsed)
        except Exception as exc:
            self.finished_signal.emit(False, str(exc), 0.0)


class CustomByparrDialog(QDialog):
    """Themed modal dialog to configure custom Byparr / FlareSolverr endpoint and credentials."""

    def __init__(self, parent: Optional[QWidget] = None, accent_color: str = "#6c5ce7"):
        super().__init__(parent)
        self.accent_color = accent_color
        self.settings = get_settings()

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(480)

        self._active_worker: Optional[_TestConnectionWorker] = None
        self._build_ui()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        card = QFrame()
        card.setObjectName("dialogCard")
        card.setStyleSheet("""
            QFrame#dialogCard {
                background-color: #16181f;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        titlebar = DialogTitleBar(self, "Cloudflare Solver Configuration", can_minimize=False, can_maximize=False)
        card_layout.addWidget(titlebar)

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(12)

        desc = QLabel(
            "Connect an existing Byparr or FlareSolverr instance to automate SteamDB "
            "Turnstile clearance for build manifests and patch history."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: rgba(255, 255, 255, 0.75); font-size: 8.5pt; line-height: 1.4;")
        lay.addWidget(desc)

        # Field 1: Service URL
        lbl_url = QLabel("Solver Service URL:")
        lbl_url.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 600;")
        lay.addWidget(lbl_url)

        self.url_input = QLineEdit()
        current_url = self.settings.value("byparr_url", BYPARR_DEFAULT_URL, type=str).strip()
        self.url_input.setText(current_url)
        self.url_input.setPlaceholderText("http://127.0.0.1:8191 or http://host:port")
        self.url_input.setStyleSheet("""
            QLineEdit {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 5px;
                color: #FFFFFF;
                padding: 6px 10px;
                font-size: 8.5pt;
            }
            QLineEdit:focus {
                border-color: #6c5ce7;
                background-color: rgba(255, 255, 255, 0.09);
            }
        """)
        lay.addWidget(self.url_input)

        # Field 2: Auth Header
        lbl_auth = QLabel("Authorization / API Key (Optional):")
        lbl_auth.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 600; margin-top: 4px;")
        lay.addWidget(lbl_auth)

        self.auth_input = QLineEdit()
        current_auth = self.settings.value("byparr_auth_header", "", type=str).strip()
        self.auth_input.setText(current_auth)
        self.auth_input.setPlaceholderText("e.g. Bearer my-secret-token or X-API-Key: secret")
        self.auth_input.setStyleSheet("""
            QLineEdit {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 5px;
                color: #FFFFFF;
                padding: 6px 10px;
                font-size: 8.5pt;
            }
            QLineEdit:focus {
                border-color: #6c5ce7;
                background-color: rgba(255, 255, 255, 0.09);
            }
        """)
        lay.addWidget(self.auth_input)

        # Test button & result label
        test_row = QHBoxLayout()
        test_row.setSpacing(10)
        self.test_btn = QPushButton("Test & Verify Connection")
        self.test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_btn.setFixedHeight(28)
        self.test_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 5px;
                color: #FFFFFF;
                font-size: 8pt;
                font-weight: 600;
                padding: 0 14px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.16);
            }
            QPushButton:disabled {
                background-color: rgba(255, 255, 255, 0.03);
                color: rgba(255, 255, 255, 0.3);
                border-color: rgba(255, 255, 255, 0.06);
            }
        """)
        self.test_btn.clicked.connect(self._on_test_clicked)
        test_row.addWidget(self.test_btn)
        test_row.addStretch()
        lay.addLayout(test_row)

        self.result_lbl = QLabel("")
        self.result_lbl.setWordWrap(True)
        self.result_lbl.setStyleSheet("font-size: 8pt;")
        self.result_lbl.setVisible(False)
        lay.addWidget(self.result_lbl)

        # Actions row
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)
        btn_bar.setContentsMargins(0, 10, 0, 0)

        btn_reset = QPushButton("Reset to Default")
        btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                color: rgba(255, 255, 255, 0.5);
                font-size: 8pt;
                padding: 4px 8px;
            }
            QPushButton:hover {
                color: #FFFFFF;
                text-decoration: underline;
            }
        """)
        btn_reset.clicked.connect(self._on_reset_clicked)
        btn_bar.addWidget(btn_reset)
        btn_bar.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setFixedSize(76, 30)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.07);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 5px;
                color: rgba(255, 255, 255, 0.85);
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.14);
                color: #FFFFFF;
            }
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_bar.addWidget(btn_cancel)

        btn_save = QPushButton("Save & Apply")
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.setFixedSize(100, 30)
        btn_save.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                border: none;
                border-radius: 5px;
                color: #FFFFFF;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: #FFFFFF;
                color: #000000;
            }}
        """)
        btn_save.clicked.connect(self._on_save_clicked)
        btn_bar.addWidget(btn_save)

        lay.addLayout(btn_bar)

        card_layout.addWidget(content)
        root_layout.addWidget(card)

    def _on_test_clicked(self):
        url = self.url_input.text().strip()
        auth = self.auth_input.text().strip()
        if not url:
            self.result_lbl.setText("Please enter a service URL.")
            self.result_lbl.setStyleSheet("color: #FFB74D; font-size: 8pt;")
            self.result_lbl.setVisible(True)
            return

        self.test_btn.setEnabled(False)
        self.test_btn.setText("Testing SteamDB Bypass...")
        self.result_lbl.setText("Sending test Turnstile bypass request to SteamDB...")
        self.result_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8pt;")
        self.result_lbl.setVisible(True)

        worker = _TestConnectionWorker(url, auth)
        self._active_worker = worker

        def _on_done(ok: bool, msg: str, elapsed: float):
            self.test_btn.setEnabled(True)
            self.test_btn.setText("Test & Verify Connection")
            if ok:
                self.result_lbl.setText(f"Success: {msg}")
                self.result_lbl.setStyleSheet("color: #81C784; font-size: 8pt; font-weight: 600;")
            else:
                self.result_lbl.setText(f"Verification failed: {msg}")
                self.result_lbl.setStyleSheet("color: #E57373; font-size: 8pt;")
            self._active_worker = None

        worker.finished_signal.connect(_on_done)
        worker.start()

    def _on_reset_clicked(self):
        self.url_input.setText(BYPARR_DEFAULT_URL)
        self.auth_input.clear()
        self.result_lbl.setVisible(False)

    def _on_save_clicked(self):
        url = self.url_input.text().strip().rstrip("/")
        if not url:
            url = BYPARR_DEFAULT_URL
        auth = self.auth_input.text().strip()

        self.settings.setValue("byparr_url", url)
        self.settings.setValue("byparr_auth_header", auth)
        self.settings.setValue("disable_steamdb_scraping", False)

        # Mutual exclusion: when custom service is configured, auto-disable local Byparr
        is_custom = bool(url and url.rstrip("/") != BYPARR_DEFAULT_URL.rstrip("/"))
        if is_custom:
            self.settings.setValue("byparr_enabled", False)
            ByparrManager.stop()
            logger.info("[ByparrConfig] Custom service active - local ASSella Byparr disabled.")

        logger.info(f"[ByparrConfig] Saved byparr_url={url}, auth_set={bool(auth)}")
        self.accept()
