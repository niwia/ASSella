"""
Training Wheels Protocol (TWP) (Beta)
======================================
First-launch transition guide for users moving from ACCELA to ASSella.
Detects first-time users via the `assella_twp_seen` config watermark and
presents live SLSsteam health + recommended settings.
"""

import logging
import threading
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.settings_sls import get_sls_paths
from utils.assfixer import check_config_status
from utils.settings import get_settings
from utils.slssteam_integration import is_slssteam_process_active, is_steam_process_running

logger = logging.getLogger(__name__)


class TrainingWheelsDialog(QDialog):
    """Training Wheels Protocol (Beta) — ACCELA to ASSella first-launch transition guide.

    Shown automatically once (watermark key: ``assella_twp_seen``).
    Can also be triggered manually from Settings → ASSella with ``manual=True``.
    """

    _assfixer_result_signal = pyqtSignal(tuple)
    _version_result_signal = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None, manual: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Health & Setup Guide (Training Wheels Protocol)")
        self.setMinimumWidth(560)
        self.resize(600, 720)
        self.setSizeGripEnabled(True)

        self.manual = manual
        self.settings = get_settings()
        self.accent_color = self.settings.value("accent_color", "#a1c9fd")
        self.bg_color = self.settings.value("background_color", "#111318")

        # Watermark is written immediately so that skip/close is permanent
        if not self.manual:
            self.settings.setValue("assella_twp_seen", True)
            self.settings.sync()

        self._assfixer_result_signal.connect(self._handle_assfixer_done)
        self._version_result_signal.connect(self._handle_version_done)

        self._init_ui()
        self._refresh_sls_health()

    # ──────────────────────────────────────────────────────────────────────────
    # UI construction
    # ──────────────────────────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {self.bg_color};
                color: #FFFFFF;
            }}
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.2);
                min-height: 20px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(255, 255, 255, 0.35);
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 24, 20, 20)
        root.setSpacing(16)

        # ── Header (centred, no emojis) ──────────────────────────────────────
        title_lbl = QLabel("Training Wheels Protocol (Beta)")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setStyleSheet(
            f"font-size: 15pt; font-weight: bold; color: {self.accent_color};"
        )
        root.addWidget(title_lbl)

        desc_lbl = QLabel(
            "Welcome to ASSella! The settings below are recommended for users "
            "transitioning from ACCELA or starting fresh. Apply them all with one click, "
            "or customize them before continuing."
        )
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 9pt;")
        desc_lbl.setWordWrap(True)
        root.addWidget(desc_lbl)

        # ── Scrollable body ──────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 8, 0)
        body_layout.setSpacing(14)

        # 1. SLSsteam health card (ON TOP)
        body_layout.addWidget(self._build_sls_card())

        # 2. Recommended Settings card
        body_layout.addWidget(self._build_settings_card())

        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # ── Bottom Action Buttons (Taking Full Width) ────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.setContentsMargins(0, 4, 0, 0)

        skip_lbl = "Close" if self.manual else "Skip"
        self.btn_skip = QPushButton(skip_lbl)
        self.btn_skip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_skip.setMinimumHeight(40)
        self.btn_skip.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 8px;
                padding: 8px 16px;
                color: rgba(255, 255, 255, 0.8);
                font-size: 9.5pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.12);
                color: #FFFFFF;
                border-color: rgba(255, 255, 255, 0.3);
            }
        """)
        self.btn_skip.clicked.connect(self._handle_skip)
        btn_row.addWidget(self.btn_skip, 1)

        self.btn_apply = QPushButton("Apply Recommended Settings")
        self.btn_apply.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_apply.setMinimumHeight(40)
        self.btn_apply.setStyleSheet(f"""
            QPushButton {{
                background: {self.accent_color};
                color: #000000;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 9.5pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: #FFFFFF;
            }}
        """)
        self.btn_apply.clicked.connect(self._apply_settings)
        btn_row.addWidget(self.btn_apply, 1)

        root.addLayout(btn_row)

    # ── SLSsteam health card (Top) ───────────────────────────────────────────

    def _build_sls_card(self) -> QFrame:
        card, layout = self._make_card("SLSsteam")

        # ── Button 1: Binary Detection ───────────────────────────────────
        row1 = QHBoxLayout()
        lbl1 = QLabel("SLSsteam Binary")
        lbl1.setStyleSheet("font-size: 9pt; color: rgba(255, 255, 255, 0.85); font-weight: 500;")
        row1.addWidget(lbl1)
        row1.addStretch()
        self.btn_sls_install = QPushButton("Checking...")
        self._style_status_btn(self.btn_sls_install, state="neutral")
        self.btn_sls_install.setEnabled(False)
        row1.addWidget(self.btn_sls_install)

        self.btn_sls_version = QPushButton("Checking...")
        self._style_status_btn(self.btn_sls_version, state="neutral")
        self.btn_sls_version.setEnabled(False)
        row1.addWidget(self.btn_sls_version)
        layout.addLayout(row1)

        # ── Button 2: Process (Steam running + SLS injected) ─────────────
        row2 = QHBoxLayout()
        lbl2 = QLabel("SLSsteam Process")
        lbl2.setStyleSheet("font-size: 9pt; color: rgba(255, 255, 255, 0.85); font-weight: 500;")
        row2.addWidget(lbl2)
        row2.addStretch()
        self.btn_sls_process = QPushButton("Checking...")
        self._style_status_btn(self.btn_sls_process, state="neutral")
        self.btn_sls_process.setEnabled(False)
        row2.addWidget(self.btn_sls_process)
        layout.addLayout(row2)

        self.lbl_process_hint = QLabel("")
        self.lbl_process_hint.setStyleSheet(
            "color: rgba(255, 255, 255, 0.5); font-size: 8pt; margin-left: 2px;"
        )
        self.lbl_process_hint.setWordWrap(True)
        layout.addWidget(self.lbl_process_hint)

        # ── Separator ─────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(
            "background: rgba(255, 255, 255, 0.08); max-height: 1px; margin: 4px 0;"
        )
        layout.addWidget(sep)

        # ── Button 3: Config check / Fix button ──────────────────────────
        row3 = QHBoxLayout()
        lbl3 = QLabel("SLSsteam Config")
        lbl3.setStyleSheet("font-size: 9pt; color: rgba(255, 255, 255, 0.85); font-weight: 500;")
        row3.addWidget(lbl3)
        row3.addStretch()

        self.btn_config_check = QPushButton("Check Config")
        self.btn_config_check.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 5px 14px;
                color: #e0e0e0;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.15);
                border-color: {self.accent_color};
            }}
            QPushButton:disabled {{
                color: rgba(255, 255, 255, 0.3);
                background: rgba(255, 255, 255, 0.02);
                border-color: rgba(255, 255, 255, 0.05);
            }}
        """)
        self.btn_config_check.clicked.connect(self._run_config_check)
        row3.addWidget(self.btn_config_check)
        layout.addLayout(row3)

        self.lbl_config_status = QLabel("Checks upstream SLSsteam template via GitHub.")
        self.lbl_config_status.setStyleSheet(
            "color: rgba(255, 255, 255, 0.5); font-size: 8pt; margin-left: 2px;"
        )
        self.lbl_config_status.setWordWrap(True)
        layout.addWidget(self.lbl_config_status)

        # ── Separator ─────────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(
            "background: rgba(255, 255, 255, 0.08); max-height: 1px; margin: 4px 0;"
        )
        layout.addWidget(sep2)

        # ── Button 4: SLS Inheritance ────────────────────────────────────
        row4 = QHBoxLayout()
        lbl4 = QLabel("SLS Inheritance")
        lbl4.setStyleSheet("font-size: 9pt; color: rgba(255, 255, 255, 0.85); font-weight: 500;")
        row4.addWidget(lbl4)
        row4.addStretch()

        self.btn_sls_inheritance = QPushButton("Manage SLS Inheritance")
        self.btn_sls_inheritance.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 5px 14px;
                color: #e0e0e0;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.15);
                border-color: {self.accent_color};
            }}
        """)
        self.btn_sls_inheritance.clicked.connect(self._open_sls_inheritance)
        row4.addWidget(self.btn_sls_inheritance)
        layout.addLayout(row4)

        lbl_inh_status = QLabel("Manage orphan configs, external game installations, and library ownership mapping.")
        lbl_inh_status.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8pt; margin-left: 2px;")
        lbl_inh_status.setWordWrap(True)
        layout.addWidget(lbl_inh_status)

        self._config_needs_repair = False
        self._config_issue_count = 0

        return card

    # ── Recommended Settings card (Bottom) ───────────────────────────────────

    def _build_settings_card(self) -> QFrame:
        card, layout = self._make_card("Recommended Settings")

        curr_smart = self.settings.value("smart_depot_selection", False, type=bool)
        curr_gateway = self.settings.value("isp_bypass_mode", "auto", type=str) or "auto"
        curr_sls_api = self.settings.value("experimental_acf_independent", False, type=bool)
        curr_achievements = self.settings.value("generate_achievements", True, type=bool)
        curr_macos = self.settings.value("hide_macos_depots", False, type=bool)

        sls_paths = get_sls_paths()
        self.sls_detected = sls_paths.get("detected", False)

        self.chk_smart = self._setting_row(
            layout, "Smart Depot Selection",
            "Automatically reuse previously chosen depots on update unless brand-new depots are added.",
            "ON" if curr_smart else "OFF", "ON",
        )
        self.chk_gateway = self._setting_row(
            layout, "Hubcap Gateway: Auto",
            "Smart fallback routing (Direct → DoH → Tor → Wire) to bypass ISP throttling.",
            curr_gateway.capitalize(), "Auto",
        )

        if self.sls_detected:
            self.chk_sls_api = self._setting_row(
                layout, "SLSsteam Native API",
                "Enable native ACF generation and automated Steam library registration.",
                "ON" if curr_sls_api else "OFF", "ON",
            )
        else:
            self.chk_sls_api = self._setting_row(
                layout, "SLSsteam Native API",
                "SLSsteam binary not detected. Install SLSsteam first to enable this.",
                "Not Installed", "Requires SLSsteam",
                enabled=False, warn=True,
            )

        self.chk_achievements = self._setting_row(
            layout, "Disable Legacy Achievement Generation",
            "Turn off the slow legacy achievement parser that runs during manifest downloads.",
            "Enabled" if curr_achievements else "Disabled", "Disabled",
        )
        self.chk_macos = self._setting_row(
            layout, "Hide macOS Depots",
            "Filter macOS/OSX-specific depots from all selection dialogs and queues.",
            "ON" if curr_macos else "OFF", "ON",
        )

        return card

    # ──────────────────────────────────────────────────────────────────────────
    # SLS health refresh
    # ──────────────────────────────────────────────────────────────────────────

    def _refresh_sls_health(self) -> None:
        """Populate SLS installation and process status pills synchronously."""
        paths = get_sls_paths()
        installed = paths.get("detected", False)
        steam_running = is_steam_process_running()
        sls_active = is_slssteam_process_active()

        # Button 1: binary detection
        if installed:
            self._style_status_btn(self.btn_sls_install, state="ok", text="Detected")
        else:
            self._style_status_btn(self.btn_sls_install, state="error", text="Not Detected")

        # Button 2: process check (conditional on Steam running)
        if not steam_running:
            self._style_status_btn(self.btn_sls_process, state="warn", text="Steam Not Running")
            self.lbl_process_hint.setText(
                "Launch Steam first. SLSsteam only injects into the Steam process."
            )
        elif sls_active:
            self._style_status_btn(self.btn_sls_process, state="ok", text="Active (Injected)")
            self.lbl_process_hint.setText("")
        else:
            self._style_status_btn(
                self.btn_sls_process, state="error",
                text="Not Injected" if installed else "Not Installed",
            )
            if installed:
                self.lbl_process_hint.setText(
                    "Steam is running but SLSsteam is not loaded. "
                    "Restart Steam or check your SLSsteam setup."
                )
            else:
                self.lbl_process_hint.setText(
                    "SLSsteam is not installed. Install it via Settings → SLS."
                )

        # Async binary freshness / version check
        self._style_status_btn(self.btn_sls_version, state="neutral", text="Checking...")

        def _ver_worker():
            try:
                from utils.slssteam_integration import check_slssteam_binary_is_latest
                res = check_slssteam_binary_is_latest()
            except Exception as e:
                res = {"status": "error", "error": str(e)}
            self._version_result_signal.emit(res)

        threading.Thread(target=_ver_worker, daemon=True).start()

    @pyqtSlot(dict)
    def _handle_version_done(self, result: dict) -> None:
        status = result.get("status", "error")
        tag = result.get("release_tag") or "unknown"
        if status == "up_to_date":
            self._style_status_btn(self.btn_sls_version, state="ok", text=f"Up to Date ({tag})")
        elif status == "outdated":
            self._style_status_btn(self.btn_sls_version, state="warn", text=f"Outdated ({tag})")
        elif status == "no_local":
            self._style_status_btn(self.btn_sls_version, state="neutral", text="Not Installed")
        else:
            self._style_status_btn(self.btn_sls_version, state="neutral", text="Version Unknown")

    def _open_sls_inheritance(self) -> None:
        try:
            from ui.dialogs.sls_inheritance import SlsInheritanceDialog
            dlg = SlsInheritanceDialog(self)
            dlg.exec()
        except Exception as e:
            logger.error(f"Error opening SLS Inheritance dialog: {e}", exc_info=True)

    # ──────────────────────────────────────────────────────────────────────────
    # Config check & repair (online)
    # ──────────────────────────────────────────────────────────────────────────

    def _run_config_check(self) -> None:
        """Fetch upstream SLSsteam config template from GitHub and verify local config (background thread)."""
        logger.info("Config check triggered from Training Wheels Protocol.")
        self.btn_config_check.setEnabled(False)
        self.btn_config_check.setText("Checking...")
        self.lbl_config_status.setText("Fetching upstream template from GitHub...")
        self.lbl_config_status.setStyleSheet("color: #7aa2f7; font-size: 8pt;")

        def _worker():
            try:
                res = check_config_status(online=True)
            except Exception as exc:
                res = (True, f"Check failed: {exc}", [str(exc)])
            self._assfixer_result_signal.emit(res)

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(tuple)
    def _handle_assfixer_done(self, result: tuple) -> None:
        needs_repair, summary, details = result
        logger.info(f"TWP config check received result: needs_repair={needs_repair}, summary='{summary}', details_count={len(details)}")
        self._config_needs_repair = needs_repair
        self._config_issue_count = len(details)

        if needs_repair:
            # Change dynamically to a Fix button
            self.btn_config_check.setText(
                f"Fix ({self._config_issue_count} issue{'s' if self._config_issue_count != 1 else ''})"
            )
            self.btn_config_check.setEnabled(True)
            self.btn_config_check.setStyleSheet("""
                QPushButton {
                    background: rgba(224, 175, 104, 0.15);
                    border: 1px solid #e0af68;
                    border-radius: 6px;
                    padding: 5px 14px;
                    color: #e0af68;
                    font-size: 8.5pt;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background: rgba(224, 175, 104, 0.25);
                }
            """)
            try:
                self.btn_config_check.clicked.disconnect()
            except Exception:
                pass
            self.btn_config_check.clicked.connect(self._run_config_repair)

            self.lbl_config_status.setText(f"Issues found: {summary}")
            self.lbl_config_status.setStyleSheet("color: #e0af68; font-size: 8pt;")
            if details:
                self.lbl_config_status.setToolTip("\n".join(details))
        else:
            self.btn_config_check.setText("Config OK")
            self.btn_config_check.setEnabled(False)
            self._style_status_btn(self.btn_config_check, state="ok", text="Config OK")
            self.lbl_config_status.setText(f"{summary}")
            self.lbl_config_status.setStyleSheet("color: #9ece6a; font-size: 8pt;")
            self.lbl_config_status.setToolTip("")

    def _run_config_repair(self) -> None:
        """Show confirmation dialog (version check + warning), then run ASSfixer repair."""
        from ui.dialogs.assfixer_confirm import AssfixerConfirmDialog
        confirm_dlg = AssfixerConfirmDialog(
            parent=self,
            accent_color=self.accent_color,
            bg_color=self.bg_color,
        )
        if confirm_dlg.exec() != AssfixerConfirmDialog.DialogCode.Accepted:
            return

        self.btn_config_check.setEnabled(False)
        self.btn_config_check.setText("Repairing...")
        self.lbl_config_status.setText("Repairing config against upstream template...")
        self.lbl_config_status.setStyleSheet("color: #7aa2f7; font-size: 8pt;")

        def _worker():
            try:
                from utils.assfixer import repair_and_sync_config
                success, msg, _bak = repair_and_sync_config(online=True)
                if success:
                    res = check_config_status(online=True)
                else:
                    res = (True, f"Repair failed: {msg}", [msg])
            except Exception as exc:
                res = (True, f"Repair error: {exc}", [str(exc)])
            self._assfixer_result_signal.emit(res)

        threading.Thread(target=_worker, daemon=True).start()

    # ──────────────────────────────────────────────────────────────────────────
    # Button Actions
    # ──────────────────────────────────────────────────────────────────────────

    def _handle_skip(self) -> None:
        """Handle Skip/Close click: ensure watermark is recorded and close."""
        self.settings.setValue("assella_twp_seen", True)
        self.settings.sync()
        self.reject()

    def _apply_settings(self) -> None:
        """Apply all selected recommended settings to QSettings and close immediately."""
        applied = []

        if self.chk_smart.isChecked():
            self.settings.setValue("smart_depot_selection", True)
            applied.append("Smart Depot Selection: ON")

        if self.chk_gateway.isChecked():
            self.settings.setValue("isp_bypass_mode", "auto")
            self.settings.setValue("isp_bypass_hubcap", True)
            applied.append("Hubcap Gateway: Auto")

        if hasattr(self, "chk_sls_api") and self.chk_sls_api.isChecked() and self.sls_detected:
            self.settings.setValue("experimental_acf_independent", True)
            self.settings.setValue("sls_config_management", True)
            applied.append("SLSsteam Native API: ON")

        if self.chk_achievements.isChecked():
            self.settings.setValue("generate_achievements", False)
            applied.append("Generate Achievements: OFF")

        if self.chk_macos.isChecked():
            self.settings.setValue("hide_macos_depots", True)
            self.settings.setValue("hide_android_depots", True)
            applied.append("Hide macOS & Android Depots: ON")

        self.settings.setValue("assella_twp_seen", True)
        self.settings.sync()
        logger.info(f"Training Wheels Protocol applied {len(applied)} setting(s): {applied}")

        if self.parent() and hasattr(self.parent(), "refresh_system_status"):
            self.parent().refresh_system_status()

        self.accept()

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _make_card(self, title: str):
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"font-size: 10pt; font-weight: bold; color: {self.accent_color}; "
            "border: none; background: transparent;"
        )
        layout.addWidget(title_lbl)

        return card, layout

    def _setting_row(
        self,
        layout: QVBoxLayout,
        title: str,
        desc: str,
        current_text: str,
        rec_text: str,
        checked: bool = True,
        enabled: bool = True,
        warn: bool = False,
    ) -> QCheckBox:
        frame = QFrame()
        frame.setStyleSheet("border: none; background: transparent;")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(0, 2, 0, 2)
        fl.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(8)

        chk = QCheckBox(title)
        chk.setChecked(checked and enabled)
        chk.setEnabled(enabled)
        chk.setStyleSheet(f"""
            QCheckBox {{
                color: #FFFFFF;
                font-size: 9.5pt;
                font-weight: 500;
            }}
            QCheckBox::indicator {{
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 1px solid rgba(255, 255, 255, 0.3);
                background: rgba(255, 255, 255, 0.05);
            }}
            QCheckBox::indicator:checked {{
                background: {self.accent_color};
                border-color: {self.accent_color};
            }}
            QCheckBox::indicator:disabled {{
                background: rgba(255, 255, 255, 0.02);
                border-color: rgba(255, 255, 255, 0.1);
            }}
        """)
        top.addWidget(chk)
        top.addStretch()

        badge_color = "#e0af68" if warn else "#9ece6a"
        badge_text = (
            f"Current: {current_text}  →  {rec_text}"
            if not warn
            else f"{current_text}"
        )
        badge = QLabel(badge_text)
        badge.setStyleSheet(f"""
            QLabel {{
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 4px;
                padding: 2px 7px;
                color: {badge_color};
                font-size: 8pt;
                font-weight: 600;
            }}
        """)
        top.addWidget(badge)
        fl.addLayout(top)

        desc_lbl = QLabel(desc)
        desc_lbl.setStyleSheet(
            "color: rgba(255, 255, 255, 0.55); font-size: 8.5pt; margin-left: 24px;"
        )
        desc_lbl.setWordWrap(True)
        fl.addWidget(desc_lbl)

        layout.addWidget(frame)
        return chk

    @staticmethod
    def _style_status_btn(btn: QPushButton, state: str = "neutral", text: str = "") -> None:
        """Apply a coloured pill style to a status-only QPushButton."""
        colours = {
            "ok":      {"bg": "rgba(158, 206, 106, 0.12)", "border": "#9ece6a", "text": "#9ece6a"},
            "error":   {"bg": "rgba(247, 118, 142, 0.12)", "border": "#f7768e", "text": "#f7768e"},
            "warn":    {"bg": "rgba(224, 175, 104, 0.12)", "border": "#e0af68", "text": "#e0af68"},
            "neutral": {"bg": "rgba(255, 255, 255, 0.05)", "border": "rgba(255, 255, 255, 0.15)", "text": "rgba(255, 255, 255, 0.6)"},
        }
        c = colours.get(state, colours["neutral"])
        if text:
            btn.setText(text)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {c['bg']};
                border: 1px solid {c['border']};
                border-radius: 6px;
                padding: 4px 12px;
                color: {c['text']};
                font-size: 8.5pt;
                font-weight: 600;
                min-width: 110px;
            }}
            QPushButton:disabled {{
                background: {c['bg']};
                border: 1px solid {c['border']};
                color: {c['text']};
            }}
        """)


# Alias for Health dialog naming convention
HealthDialog = TrainingWheelsDialog
