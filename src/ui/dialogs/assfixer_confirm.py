"""
ASSfixer Config Repair Confirmation Dialog
==========================================
Shown before applying ASSfixer repair (config resync from upstream template).

Warns the user that:
 1. The config will be updated to match the LATEST upstream SLSsteam template.
 2. If their local SLSsteam binary is outdated, the new config keys may cause
    issues until they also update SLSsteam.

Also performs a live SHA-based binary freshness check in the background while
the dialog is open, so the user sees a real green/red status rather than a
static message.
"""

import logging
import threading
from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.themed_dialog import install_themed_frame

logger = logging.getLogger(__name__)


class AssfixerConfirmDialog(QDialog):
    """Warning dialog shown before ASSfixer repair/resync is performed.

    Usage::

        dlg = AssfixerConfirmDialog(parent=self, accent_color="#a1c9fd")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # proceed with repair
    """

    _version_check_done = pyqtSignal(dict)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        accent_color: str = "#a1c9fd",
        bg_color: str = "#111318",
    ):
        super().__init__(parent)
        self.setWindowTitle("Confirm Config Repair")
        self.setMinimumWidth(480)
        self.setMaximumWidth(560)
        self.setSizeGripEnabled(False)

        self.accent_color = accent_color
        self.bg_color = bg_color

        self._version_check_done.connect(self._handle_version_result)

        self._build_ui()
        self._start_version_check()

    # ──────────────────────────────────────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = install_themed_frame(
            self, "Confirm Config Repair", margins=(22, 22, 22, 18), spacing=14
        )

        # ── Title ─────────────────────────────────────────────────────────
        title = QLabel("Config Repair / Resync")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"font-size: 13pt; font-weight: bold; color: {self.accent_color};"
        )
        root.addWidget(title)

        # ── Explanation card ──────────────────────────────────────────────
        exp_card = QFrame()
        exp_card.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
            }
        """)
        exp_lay = QVBoxLayout(exp_card)
        exp_lay.setContentsMargins(14, 12, 14, 12)
        exp_lay.setSpacing(6)

        exp_heading = QLabel("What will happen")
        exp_heading.setStyleSheet(
            "font-size: 9.5pt; font-weight: bold; color: #FFFFFF; "
            "border: none; background: transparent;"
        )
        exp_lay.addWidget(exp_heading)

        exp_body = QLabel(
            "ASSella will download the latest SLSsteam config template from GitHub "
            "and rebuild your local config to include any new settings, removing any "
            "obsolete keys while preserving your personal values (AppIds, tokens, etc.)."
        )
        exp_body.setWordWrap(True)
        exp_body.setStyleSheet(
            "color: rgba(255,255,255,0.7); font-size: 8.5pt; "
            "border: none; background: transparent;"
        )
        exp_lay.addWidget(exp_body)
        root.addWidget(exp_card)

        # ── Warning card ──────────────────────────────────────────────────
        warn_card = QFrame()
        warn_card.setStyleSheet("""
            QFrame {
                background: rgba(224, 175, 104, 0.07);
                border: 1px solid rgba(224, 175, 104, 0.35);
                border-radius: 8px;
            }
        """)
        warn_lay = QVBoxLayout(warn_card)
        warn_lay.setContentsMargins(14, 12, 14, 12)
        warn_lay.setSpacing(6)

        warn_heading = QLabel("Important: Config version compatibility")
        warn_heading.setStyleSheet(
            "font-size: 9.5pt; font-weight: bold; color: #e0af68; "
            "border: none; background: transparent;"
        )
        warn_lay.addWidget(warn_heading)

        warn_body = QLabel(
            "The repaired config always targets the LATEST upstream SLSsteam template. "
            "If your SLSsteam binary is outdated, new config keys may not be recognized "
            "and could cause unexpected behaviour until you update SLSsteam as well.\n\n"
            "Make sure your SLSsteam binary is up to date before proceeding. "
            "You can update SLSsteam via Headcrab (Settings → SLS → Install/Rerun Headcrab)."
        )
        warn_body.setWordWrap(True)
        warn_body.setStyleSheet(
            "color: rgba(255,255,255,0.7); font-size: 8.5pt; "
            "border: none; background: transparent;"
        )
        warn_lay.addWidget(warn_body)
        root.addWidget(warn_card)

        # ── Binary freshness card ─────────────────────────────────────────
        bin_card = QFrame()
        bin_card.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
            }
        """)
        bin_lay = QHBoxLayout(bin_card)
        bin_lay.setContentsMargins(14, 10, 14, 10)
        bin_lay.setSpacing(10)

        bin_lbl = QLabel("SLSsteam binary status")
        bin_lbl.setStyleSheet(
            "font-size: 9pt; color: rgba(255,255,255,0.85); font-weight: 500; "
            "border: none; background: transparent;"
        )
        bin_lay.addWidget(bin_lbl)
        bin_lay.addStretch()

        self.btn_binary_status = QPushButton("Checking...")
        self._style_pill(self.btn_binary_status, "neutral")
        self.btn_binary_status.setEnabled(False)
        bin_lay.addWidget(self.btn_binary_status)

        root.addWidget(bin_card)

        self.lbl_binary_hint = QLabel(
            "Downloading ~5 MB from GitHub to verify your binary matches the latest release..."
        )
        self.lbl_binary_hint.setStyleSheet(
            "color: rgba(255,255,255,0.45); font-size: 8pt;"
        )
        self.lbl_binary_hint.setWordWrap(True)
        root.addWidget(self.lbl_binary_hint)

        # ── Spinning indicator while checking ────────────────────────────
        self._dots = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.timeout.connect(self._tick_spinner)
        self._spinner_timer.start(400)

        # ── Bottom buttons ────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_cancel.setMinimumHeight(38)
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.16);
                border-radius: 8px;
                padding: 6px 14px;
                color: rgba(255,255,255,0.8);
                font-size: 9pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.12);
                color: #FFF;
            }
        """)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel, 1)

        self.btn_confirm = QPushButton("Repair Config")
        self.btn_confirm.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_confirm.setMinimumHeight(38)
        self._style_confirm_btn(ready=True)
        self.btn_confirm.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_confirm, 1)

        root.addLayout(btn_row)

    # ──────────────────────────────────────────────────────────────────────────
    # Background version check
    # ──────────────────────────────────────────────────────────────────────────

    def _start_version_check(self) -> None:
        def _worker():
            try:
                from utils.slssteam_integration import check_slssteam_binary_is_latest
                result = check_slssteam_binary_is_latest()
            except Exception as exc:
                result = {"status": "error", "error": str(exc),
                          "local_hash": None, "remote_hash": None, "release_tag": None}
            self._version_check_done.emit(result)

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(dict)
    def _handle_version_result(self, result: dict) -> None:
        self._spinner_timer.stop()
        status = result.get("status", "error")
        tag = result.get("release_tag") or "unknown"

        if status == "up_to_date":
            self._style_pill(self.btn_binary_status, "ok", "Up to Date")
            self.lbl_binary_hint.setText(
                f"Your SLSsteam.so matches the latest release ({tag}). Safe to proceed."
            )
            self.lbl_binary_hint.setStyleSheet("color: #9ece6a; font-size: 8pt;")

        elif status == "outdated":
            self._style_pill(self.btn_binary_status, "error", "Outdated")
            self.lbl_binary_hint.setText(
                f"Your SLSsteam binary is OUTDATED (latest: {tag}). "
                "Repairing the config now may cause issues. "
                "Update SLSsteam first via Headcrab (Settings → SLS)."
            )
            self.lbl_binary_hint.setStyleSheet("color: #f7768e; font-size: 8pt;")
            # Change confirm button to a more prominent warning style
            self._style_confirm_btn(ready=True, warn=True)
            self.btn_confirm.setText("Repair Anyway")

        elif status == "no_local":
            self._style_pill(self.btn_binary_status, "neutral", "Not Installed")
            self.lbl_binary_hint.setText(
                "SLSsteam is not installed locally. Config repair only affects your config.yaml."
            )
            self.lbl_binary_hint.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 8pt;")

        else:  # error
            self._style_pill(self.btn_binary_status, "warn", "Check Failed")
            err = result.get("error", "Unknown error")
            self.lbl_binary_hint.setText(
                f"Could not verify binary version (network issue?): {err}. "
                "Proceeding is safe if you are confident your SLSsteam is up to date."
            )
            self.lbl_binary_hint.setStyleSheet("color: #e0af68; font-size: 8pt;")

    def _tick_spinner(self) -> None:
        self._dots = (self._dots + 1) % 4
        self.btn_binary_status.setText("Checking" + "." * self._dots)

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _style_pill(btn: QPushButton, state: str, text: str = "") -> None:
        colours = {
            "ok":      {"bg": "rgba(158,206,106,0.12)", "border": "#9ece6a", "text": "#9ece6a"},
            "error":   {"bg": "rgba(247,118,142,0.12)", "border": "#f7768e", "text": "#f7768e"},
            "warn":    {"bg": "rgba(224,175,104,0.12)", "border": "#e0af68", "text": "#e0af68"},
            "neutral": {"bg": "rgba(255,255,255,0.05)", "border": "rgba(255,255,255,0.15)", "text": "rgba(255,255,255,0.6)"},
        }
        c = colours.get(state, colours["neutral"])
        if text:
            btn.setText(text)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {c['bg']};
                border: 1px solid {c['border']};
                border-radius: 6px;
                padding: 4px 14px;
                color: {c['text']};
                font-size: 8.5pt;
                font-weight: 600;
                min-width: 120px;
            }}
            QPushButton:disabled {{
                background: {c['bg']};
                border: 1px solid {c['border']};
                color: {c['text']};
            }}
        """)

    def _style_confirm_btn(self, ready: bool = True, warn: bool = False) -> None:
        if warn:
            style = """
                QPushButton {
                    background: rgba(247, 118, 142, 0.15);
                    border: 1px solid #f7768e;
                    border-radius: 8px;
                    padding: 6px 14px;
                    color: #f7768e;
                    font-size: 9pt;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: rgba(247, 118, 142, 0.28);
                    color: #FFF;
                }
            """
        else:
            style = f"""
                QPushButton {{
                    background: {self.accent_color};
                    color: #000;
                    border: none;
                    border-radius: 8px;
                    padding: 6px 14px;
                    font-size: 9pt;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background: #FFF;
                }}
            """
        self.btn_confirm.setStyleSheet(style)
        self.btn_confirm.setEnabled(ready)
