import logging
import os
import sys
import threading
from pathlib import Path
from typing import Tuple

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QScrollArea,
    QSizePolicy,
)
from ui.dialogs.themed_message_box import ThemedMessageBox

logger = logging.getLogger(__name__)


class HealthStatusTile(QPushButton):
    """Clean interactive status tile button used in the Health tab dashboard."""
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_lbl = QLabel(title)
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setStyleSheet("font-size: 8.5pt; font-weight: 600; color: rgba(255, 255, 255, 0.65); border: none; background: transparent;")
        self.title_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_lbl)

        self.status_lbl = QLabel("Checking...")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("font-size: 10pt; font-weight: bold; color: #FFFFFF; border: none; background: transparent;")
        self.status_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.status_lbl)

        self.set_state("neutral", "Checking...")

    def set_state(self, state: str, text: str):
        self.status_lbl.setText(text)
        if state == "ok":
            bg = "rgba(76, 175, 80, 0.12)"
            border = "rgba(76, 175, 80, 0.45)"
            color = "#81C784"
            hover_bg = "rgba(76, 175, 80, 0.22)"
        elif state == "warn":
            bg = "rgba(255, 152, 0, 0.12)"
            border = "rgba(255, 152, 0, 0.45)"
            color = "#FFB74D"
            hover_bg = "rgba(255, 152, 0, 0.22)"
        elif state == "error":
            bg = "rgba(244, 67, 54, 0.12)"
            border = "rgba(244, 67, 54, 0.45)"
            color = "#E57373"
            hover_bg = "rgba(244, 67, 54, 0.22)"
        else:
            bg = "rgba(255, 255, 255, 0.04)"
            border = "rgba(255, 255, 255, 0.15)"
            color = "rgba(255, 255, 255, 0.7)"
            hover_bg = "rgba(255, 255, 255, 0.08)"

        self.status_lbl.setStyleSheet(f"font-size: 10.5pt; font-weight: bold; color: {color}; border: none; background: transparent;")
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QPushButton:hover {{
                background-color: {hover_bg};
                border-color: {color};
            }}
        """)


def create_rec_setting_row(title: str, desc: str, checkbox: QCheckBox) -> QWidget:
    """One recommended setting: a title and description beside its checkbox.

    The colours come from the surface the row is drawn on, so the row stays
    legible on a light background too. The checkbox is deliberately left
    unstyled: the application stylesheet already draws it (transparent
    indicator, no border), and a local override only fights that.
    """
    from utils.color_utils import get_raised_surface_tokens
    from utils.settings import get_settings

    settings = get_settings()
    surface = settings.value("background_color", "#111318", type=str) if settings else "#111318"
    tokens = get_raised_surface_tokens(surface)

    row = QWidget()
    row.setObjectName("rec_setting_row")
    row.setMinimumHeight(48)
    row.setStyleSheet(f"""
        QWidget#rec_setting_row {{
            background-color: {tokens["tint"]};
            border: 1px solid {tokens["border"]};
            border-radius: 8px;
        }}
        QWidget#rec_setting_row:hover {{
            background-color: {tokens["tint_hover"]};
            border-color: {tokens["border_hover"]};
        }}
    """)
    h = QHBoxLayout(row)
    h.setContentsMargins(14, 8, 14, 8)
    h.setSpacing(12)

    col = QVBoxLayout()
    col.setSpacing(2)

    t_lbl = QLabel(title)
    t_lbl.setStyleSheet(f"font-size: 9.5pt; font-weight: 600; color: {tokens['ink']}; border: none; background: transparent;")
    d_lbl = QLabel(desc)
    d_lbl.setStyleSheet(f"font-size: 8.2pt; color: {tokens['ink_muted']}; border: none; background: transparent;")
    d_lbl.setWordWrap(True)

    col.addWidget(t_lbl)
    col.addWidget(d_lbl)
    h.addLayout(col, 1)

    checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
    h.addWidget(checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


def create_health_tab(dialog) -> QWidget:
    """Create the consolidated Health settings tab combining SLS status, SLS config, and Recommended Settings."""
    tab = QWidget()
    dialog.health_tab = tab

    outer_layout = QVBoxLayout(tab)
    outer_layout.setContentsMargins(0, 0, 0, 0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)

    # ── 1. SLSsteam Status (3-Button Status Row) ────────────────────────
    sls_card, sls_layout = dialog._create_card_frame("System & SLSsteam Status")

    tiles_row = QHBoxLayout()
    tiles_row.setContentsMargins(0, 4, 0, 4)
    tiles_row.setSpacing(12)

    dialog.health_sls_bin_btn = HealthStatusTile("SLSsteam Binary")
    dialog.health_sls_bin_btn.clicked.connect(lambda: on_sls_bin_tile_clicked(dialog))
    tiles_row.addWidget(dialog.health_sls_bin_btn, 1)

    dialog.health_sls_ver_btn = HealthStatusTile("Version")
    dialog.health_sls_ver_btn.clicked.connect(lambda: on_sls_version_tile_clicked(dialog))
    tiles_row.addWidget(dialog.health_sls_ver_btn, 1)

    dialog.health_sls_proc_btn = HealthStatusTile("SLS Process")
    dialog.health_sls_proc_btn.clicked.connect(lambda: refresh_health_tab_status(dialog))
    tiles_row.addWidget(dialog.health_sls_proc_btn, 1)

    sls_layout.addLayout(tiles_row)

    # Binary Path & Refresh Row below all 3 buttons
    path_row = QHBoxLayout()
    path_row.setContentsMargins(2, 6, 2, 2)
    path_row.setSpacing(10)

    dialog.health_sls_path_lbl = QLabel("Binary Path: Checking...")
    dialog.health_sls_path_lbl.setWordWrap(True)
    dialog.health_sls_path_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8.5pt; font-family: monospace; border: none; background: transparent;")
    dialog.health_sls_path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    path_row.addWidget(dialog.health_sls_path_lbl, 1)

    dialog.health_refresh_btn = QPushButton("Refresh Status")
    dialog.health_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.health_refresh_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 6px;
            color: #FFFFFF;
            padding: 5px 14px;
            font-size: 8.5pt;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {dialog.accent_color};
        }}
    """)
    dialog.health_refresh_btn.clicked.connect(lambda: refresh_health_tab_status(dialog))
    path_row.addWidget(dialog.health_refresh_btn, 0)

    sls_layout.addLayout(path_row)

    # Crab Actions Row: h3adcrab, r3set-crab, smallcrab
    crab_row = QHBoxLayout()
    crab_row.setContentsMargins(2, 6, 2, 2)
    crab_row.setSpacing(8)

    crab_btn_style = f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 6px;
            color: #FFFFFF;
            padding: 6px 14px;
            font-size: 8.5pt;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {dialog.accent_color};
        }}
    """
    crab_danger_btn_style = """
        QPushButton {
            background-color: rgba(239, 68, 68, 0.10);
            border: 1px solid rgba(239, 68, 68, 0.30);
            border-radius: 6px;
            color: #fca5a5;
            padding: 6px 14px;
            font-size: 8.5pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(239, 68, 68, 0.22);
            border-color: rgba(239, 68, 68, 0.55);
            color: #FFFFFF;
        }
    """
    dialog.health_h3adcrab_btn = QPushButton("h3adcrab")
    dialog.health_h3adcrab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.health_h3adcrab_btn.setStyleSheet(crab_btn_style)
    dialog.health_h3adcrab_btn.setToolTip("Install or update SLSsteam via Headcrab installer")
    dialog.health_h3adcrab_btn.clicked.connect(lambda: on_h3adcrab_clicked(dialog))
    crab_row.addWidget(dialog.health_h3adcrab_btn, 1)

    dialog.health_r3set_crab_btn = QPushButton("r3set-crab")
    dialog.health_r3set_crab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.health_r3set_crab_btn.setStyleSheet(crab_danger_btn_style)
    dialog.health_r3set_crab_btn.setToolTip("Uninstall SLSsteam/Headcrab and reset Steam to vanilla")
    dialog.health_r3set_crab_btn.clicked.connect(lambda: on_r3set_crab_clicked(dialog))
    crab_row.addWidget(dialog.health_r3set_crab_btn, 1)

    dialog.health_smallcrab_btn = QPushButton("smallcrab")
    dialog.health_smallcrab_btn.setEnabled(False)
    dialog.health_smallcrab_btn.setCursor(Qt.CursorShape.ForbiddenCursor)
    dialog.health_smallcrab_btn.setStyleSheet("""
        QPushButton {
            background-color: rgba(255, 255, 255, 0.05);
            color: rgba(255, 255, 255, 0.35);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            padding: 6px 12px;
            font-size: 9pt;
            font-weight: 500;
        }
    """)
    dialog.health_smallcrab_btn.setToolTip("smallcrab installer is temporarily disabled")
    crab_row.addWidget(dialog.health_smallcrab_btn, 1)

    sls_layout.addLayout(crab_row)

    # Steam Updates Row: the steam.cfg block, and the button that flips it.
    steam_updates_row = QHBoxLayout()
    steam_updates_row.setContentsMargins(2, 2, 2, 2)
    steam_updates_row.setSpacing(8)

    dialog.health_steam_updates_lbl = QLabel("")
    dialog.health_steam_updates_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;")
    dialog.health_steam_updates_lbl.setWordWrap(True)
    dialog.health_steam_updates_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    steam_updates_row.addWidget(dialog.health_steam_updates_lbl, 1)

    dialog.health_steam_updates_btn = QPushButton("Disable Steam Updates")
    dialog.health_steam_updates_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.health_steam_updates_btn.setStyleSheet(crab_btn_style)
    dialog.health_steam_updates_btn.setToolTip("Steam reads steam.cfg when it starts, so this applies on the next Steam start.")
    dialog.health_steam_updates_btn.clicked.connect(lambda: on_steam_updates_clicked(dialog))
    steam_updates_row.addWidget(dialog.health_steam_updates_btn, 0)

    sls_layout.addLayout(steam_updates_row)
    refresh_steam_updates_control(dialog)
    layout.addWidget(sls_card)

    # ── 2. SLS Config (Linux/Steam Deck) ──────────────────────────────
    if sys.platform == "linux":
        sls_cfg_card, sls_cfg_layout = dialog._create_card_frame("SLS Config")

        sls_cfg_desc = QLabel("Validate and synchronize ~/.config/SLSsteam/config.yaml against upstream template.")
        sls_cfg_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;")
        sls_cfg_desc.setWordWrap(True)
        sls_cfg_layout.addWidget(sls_cfg_desc)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 4)
        btn_row.setSpacing(10)

        btn_style = """
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 8px;
                color: #FFFFFF;
                padding: 7px 16px;
                font-size: 9.5pt;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.16);
                border-color: rgba(255, 255, 255, 0.32);
            }
            QPushButton:disabled {
                background-color: rgba(255, 255, 255, 0.03) !important;
                border: 1px solid rgba(255, 255, 255, 0.08) !important;
                color: rgba(255, 255, 255, 0.3) !important;
            }
        """

        dialog.assfixer_check_btn = QPushButton("Check")
        dialog.assfixer_check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dialog.assfixer_check_btn.setStyleSheet(btn_style)
        dialog.assfixer_check_btn.clicked.connect(lambda: run_assfixer_check(dialog))
        btn_row.addWidget(dialog.assfixer_check_btn)

        dialog.assfixer_repair_btn = QPushButton("Repair / Resync")
        dialog.assfixer_repair_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dialog.assfixer_repair_btn.setStyleSheet(btn_style)
        dialog.assfixer_repair_btn.setEnabled(False)
        dialog.assfixer_repair_btn.clicked.connect(lambda: run_assfixer_repair(dialog))
        btn_row.addWidget(dialog.assfixer_repair_btn)

        dialog.assfixer_restore_btn = QPushButton("Restore Backup")
        dialog.assfixer_restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dialog.assfixer_restore_btn.setStyleSheet(btn_style)
        try:
            from utils.assfixer import has_config_backup
            dialog.assfixer_restore_btn.setEnabled(has_config_backup())
        except Exception:
            dialog.assfixer_restore_btn.setEnabled(False)
        dialog.assfixer_restore_btn.clicked.connect(lambda: run_assfixer_restore(dialog))
        btn_row.addWidget(dialog.assfixer_restore_btn)

        btn_row.addStretch()
        sls_cfg_layout.addLayout(btn_row)

        dialog.assfixer_status_lbl = QLabel("")
        dialog.assfixer_status_lbl.setStyleSheet("color: #a9b1d6; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
        dialog.assfixer_status_lbl.setWordWrap(True)
        dialog.assfixer_status_lbl.hide()
        sls_cfg_layout.addWidget(dialog.assfixer_status_lbl)

        layout.addWidget(sls_cfg_card)

    # ── 3. Recommended Settings ──────────────────────────────
    rec_card, rec_layout = dialog._create_card_frame("")
    rec_header_row = QHBoxLayout()
    rec_header_row.setContentsMargins(0, 0, 0, 2)
    rec_title_lbl = QLabel("Recommended Settings")
    rec_title_lbl.setStyleSheet(f"font-size: 10pt; font-weight: bold; color: {dialog.accent_color}; border: none; background: transparent;")
    rec_header_row.addWidget(rec_title_lbl)
    rec_header_row.addStretch()

    dialog.rec_score_badge = QLabel("● Checking...")
    dialog.rec_score_badge.setStyleSheet("font-size: 8.5pt; font-weight: bold; color: #81C784; background: rgba(76, 175, 80, 0.12); border: 1px solid rgba(76, 175, 80, 0.35); border-radius: 6px; padding: 2px 10px;")
    rec_header_row.addWidget(dialog.rec_score_badge)
    rec_layout.addLayout(rec_header_row)

    rec_desc = QLabel("Essential settings recommended for the optimal ASSella workflow.")
    rec_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent; margin-bottom: 4px;")
    rec_desc.setWordWrap(True)
    rec_layout.addWidget(rec_desc)

    # Checkboxes
    dialog.twp_chk_smart = None
    dialog.twp_chk_gateway = QCheckBox()
    dialog.twp_chk_gateway.setChecked(dialog.settings.value("isp_bypass_mode", "auto", type=str) == "auto")
    dialog.twp_chk_gateway.toggled.connect(lambda: on_rec_setting_toggled(dialog))
    rec_layout.addWidget(create_rec_setting_row(
        "Hubcap Gateway (Auto)",
        "Smart fallback routing to bypass ISP throttling and rate limits",
        dialog.twp_chk_gateway,
    ))

    dialog.twp_chk_sls_api = QCheckBox()
    dialog.twp_chk_sls_api.setChecked(dialog.settings.value("experimental_acf_independent", False, type=bool))
    dialog.twp_chk_sls_api.toggled.connect(lambda: on_rec_setting_toggled(dialog))
    rec_layout.addWidget(create_rec_setting_row(
        "SLSsteam Native API",
        "Native ACF generation and automated Steam game registration",
        dialog.twp_chk_sls_api,
    ))

    dialog.twp_chk_achievements = QCheckBox()
    dialog.twp_chk_achievements.setChecked(not dialog.settings.value("generate_achievements", True, type=bool))
    dialog.twp_chk_achievements.toggled.connect(lambda: on_rec_setting_toggled(dialog))
    rec_layout.addWidget(create_rec_setting_row(
        "Skip achievement generation",
        "Skip legacy achievement schema generation to accelerate downloads",
        dialog.twp_chk_achievements,
    ))

    dialog.twp_chk_macos = QCheckBox()
    dialog.twp_chk_macos.setChecked(dialog.settings.value("hide_macos_depots", False, type=bool))
    dialog.twp_chk_macos.toggled.connect(lambda: on_rec_setting_toggled(dialog))
    rec_layout.addWidget(create_rec_setting_row(
        "Hide Platform Clutter",
        "Filter out unnecessary macOS & Android depots from download queues",
        dialog.twp_chk_macos,
    ))

    dialog.health_apply_rec_btn = QPushButton("Apply Recommended Settings")
    dialog.health_apply_rec_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.health_apply_rec_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    dialog.health_apply_rec_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {dialog.accent_color};
            color: #000000;
            border: none;
            border-radius: 8px;
            padding: 10px 20px;
            font-size: 9.5pt;
            font-weight: bold;
            margin-top: 6px;
        }}
        QPushButton:hover {{
            background-color: #FFFFFF;
        }}
        QPushButton:disabled {{
            background-color: rgba(255, 255, 255, 0.05) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            color: rgba(255, 255, 255, 0.28) !important;
        }}
    """)
    dialog.health_apply_rec_btn.clicked.connect(lambda: apply_health_recommended_settings(dialog))
    rec_layout.addWidget(dialog.health_apply_rec_btn)

    layout.addWidget(rec_card)
    layout.addStretch()
    scroll.setWidget(container)
    outer_layout.addWidget(scroll)

    dialog.tab_widget.addTab(tab, "Health")
    QTimer.singleShot(50, lambda: refresh_health_tab_status(dialog))
    return tab


def refresh_health_tab_status(dialog) -> None:
    """Refresh binary, process, and version freshness in the Health tab."""
    if not hasattr(dialog, "health_sls_bin_btn") or not dialog.health_sls_bin_btn:
        return

    from ui.dialogs.settings_sls import get_sls_paths
    from utils.slssteam_integration import is_slssteam_process_active, is_steam_process_running

    paths = get_sls_paths()
    installed = paths.get("detected", False)
    so_path = paths.get("so_path", "")
    steam_running = is_steam_process_running()
    sls_active = is_slssteam_process_active()

    # 1. SLSsteam Binary
    if installed:
        dialog.health_sls_bin_btn.set_state("ok", "Detected")
        if hasattr(dialog, "health_sls_path_lbl") and dialog.health_sls_path_lbl:
            dialog.health_sls_path_lbl.setText(f"Binary Path: {so_path}")
    else:
        dialog.health_sls_bin_btn.set_state("error", "Not Detected")
        if hasattr(dialog, "health_sls_path_lbl") and dialog.health_sls_path_lbl:
            dialog.health_sls_path_lbl.setText("Binary Path: Not detected")

    # 2. SLS Process
    if not steam_running:
        dialog.health_sls_proc_btn.set_state("neutral", "Inactive")
    elif sls_active:
        dialog.health_sls_proc_btn.set_state("ok", "Active")
    else:
        dialog.health_sls_proc_btn.set_state("warn" if installed else "neutral", "Inactive")

    # 3. Version Check (Async)
    if hasattr(dialog.health_sls_ver_btn, "title_lbl"):
        dialog.health_sls_ver_btn.title_lbl.setText("Version")
    dialog.health_sls_ver_btn.set_state("neutral", "Checking...")

    def _ver_worker():
        try:
            from utils.slssteam_integration import check_slssteam_binary_is_latest
            res = check_slssteam_binary_is_latest()
        except Exception as e:
            res = {"status": "error", "error": str(e)}
        dialog.sls_version_check_signal.emit(res)

    threading.Thread(target=_ver_worker, daemon=True).start()

    update_rec_score_badge(dialog)

    refresh_steam_updates_control(dialog)


def handle_sls_version_check_done(dialog, result: dict) -> None:
    if not hasattr(dialog, "health_sls_ver_btn") or not dialog.health_sls_ver_btn:
        return

    status = result.get("status", "error")
    tag = result.get("release_tag") or ""
    if status == "up_to_date":
        if hasattr(dialog.health_sls_ver_btn, "title_lbl"):
            dialog.health_sls_ver_btn.title_lbl.setText(tag if tag else "SLSsteam Version")
        dialog.health_sls_ver_btn.set_state("ok", "Up to date!")
    elif status == "outdated":
        if hasattr(dialog.health_sls_ver_btn, "title_lbl"):
            dialog.health_sls_ver_btn.title_lbl.setText(tag if tag else "SLSsteam Version")
        dialog.health_sls_ver_btn.set_state("warn", "Update!")
    elif status == "no_local":
        if hasattr(dialog.health_sls_ver_btn, "title_lbl"):
            dialog.health_sls_ver_btn.title_lbl.setText("Version")
        dialog.health_sls_ver_btn.set_state("neutral", "Not Installed")
    else:
        from ui.dialogs.settings_sls import is_headcrab_installed, get_sls_paths
        paths = get_sls_paths()
        if paths.get("detected"):
            if is_headcrab_installed():
                if hasattr(dialog.health_sls_ver_btn, "title_lbl"):
                    dialog.health_sls_ver_btn.title_lbl.setText("SLSsteam Version")
                dialog.health_sls_ver_btn.set_state("ok", "Headcrab")
            else:
                if hasattr(dialog.health_sls_ver_btn, "title_lbl"):
                    dialog.health_sls_ver_btn.title_lbl.setText("SLSsteam Version")
                dialog.health_sls_ver_btn.set_state("ok", "Detected")
        else:
            if hasattr(dialog.health_sls_ver_btn, "title_lbl"):
                dialog.health_sls_ver_btn.title_lbl.setText("Version")
            dialog.health_sls_ver_btn.set_state("neutral", "Unknown")


def on_sls_bin_tile_clicked(dialog) -> None:
    from ui.dialogs.settings_sls import get_sls_paths
    installed = get_sls_paths().get("detected", False)
    if not installed and hasattr(dialog, "tab_widget") and dialog.tab_widget:
        for i in range(dialog.tab_widget.count()):
            if dialog.tab_widget.tabText(i) == "SLS":
                dialog.tab_widget.setCurrentIndex(i)
                return
    refresh_health_tab_status(dialog)


def on_sls_version_tile_clicked(dialog) -> None:
    current_status = getattr(dialog.health_sls_ver_btn, "status_lbl", None)
    status_text = current_status.text() if current_status else ""
    if "Update!" in status_text and hasattr(dialog, "tab_widget") and dialog.tab_widget:
        reply = ThemedMessageBox.question(
            dialog,
            "SLSsteam Update Available",
            "A newer SLSsteam build is available. Switch to the SLS tab to install or update?",
            ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
            ThemedMessageBox.StandardButton.Yes
        )
        if reply == ThemedMessageBox.StandardButton.Yes:
            for i in range(dialog.tab_widget.count()):
                if dialog.tab_widget.tabText(i) == "SLS":
                    dialog.tab_widget.setCurrentIndex(i)
                    return

    if hasattr(dialog.health_sls_ver_btn, "title_lbl"):
        dialog.health_sls_ver_btn.title_lbl.setText("Version")
    dialog.health_sls_ver_btn.set_state("neutral", "Checking...")

    def _ver_worker():
        try:
            from utils.slssteam_integration import check_slssteam_binary_is_latest
            res = check_slssteam_binary_is_latest(force_refresh=True)
        except Exception as e:
            res = {"status": "error", "error": str(e)}
        dialog.sls_version_check_signal.emit(res)

    threading.Thread(target=_ver_worker, daemon=True).start()


def on_rec_setting_toggled(dialog) -> None:
    update_rec_score_badge(dialog)


def update_rec_score_badge(dialog) -> None:
    if not hasattr(dialog, "rec_score_badge") or not dialog.rec_score_badge:
        return
    chk_list = [
        getattr(dialog, "twp_chk_gateway", None),
        getattr(dialog, "twp_chk_sls_api", None),
        getattr(dialog, "twp_chk_achievements", None),
        getattr(dialog, "twp_chk_macos", None),
    ]
    active_count = sum(1 for chk in chk_list if chk and chk.isChecked())
    total = len(chk_list)
    if active_count == total:
        dialog.rec_score_badge.setText(f"● {active_count}/{total} Optimal")
        dialog.rec_score_badge.setStyleSheet(
            "font-size: 8.5pt; font-weight: bold; color: #81C784; "
            "background: rgba(76, 175, 80, 0.12); "
            "border: 1px solid rgba(76, 175, 80, 0.35); "
            "border-radius: 6px; padding: 2px 10px;"
        )
        if hasattr(dialog, "health_apply_rec_btn") and dialog.health_apply_rec_btn:
            dialog.health_apply_rec_btn.setEnabled(False)
            dialog.health_apply_rec_btn.setToolTip("All recommended settings are already applied.")
    else:
        dialog.rec_score_badge.setText(f"● {active_count}/{total} Recommended")
        dialog.rec_score_badge.setStyleSheet(
            "font-size: 8.5pt; font-weight: bold; color: #FFB74D; "
            "background: rgba(255, 152, 0, 0.12); "
            "border: 1px solid rgba(255, 152, 0, 0.35); "
            "border-radius: 6px; padding: 2px 10px;"
        )
        if hasattr(dialog, "health_apply_rec_btn") and dialog.health_apply_rec_btn:
            dialog.health_apply_rec_btn.setEnabled(True)
            dialog.health_apply_rec_btn.setToolTip("Click to apply all recommended settings.")


def apply_health_recommended_settings(dialog) -> None:
    if hasattr(dialog, "twp_chk_gateway") and dialog.twp_chk_gateway:
        dialog.twp_chk_gateway.setChecked(True)
    if hasattr(dialog, "twp_chk_sls_api") and dialog.twp_chk_sls_api:
        dialog.twp_chk_sls_api.setChecked(True)
    if hasattr(dialog, "twp_chk_achievements") and dialog.twp_chk_achievements:
        dialog.twp_chk_achievements.setChecked(True)
    if hasattr(dialog, "twp_chk_macos") and dialog.twp_chk_macos:
        dialog.twp_chk_macos.setChecked(True)

    dialog.settings.setValue("smart_depot_selection", True)
    dialog.settings.setValue("isp_bypass_mode", "auto")
    dialog.settings.setValue("isp_bypass_hubcap", True)

    try:
        from ui.dialogs.settings_sls import get_sls_paths
        sls_detected = get_sls_paths().get("detected", False)
    except Exception:
        sls_detected = False

    if sls_detected:
        dialog.settings.setValue("experimental_acf_independent", True)
        dialog.settings.setValue("sls_config_management", True)

    dialog.settings.setValue("generate_achievements", False)
    dialog.settings.setValue("hide_macos_depots", True)
    dialog.settings.setValue("hide_android_depots", True)
    dialog.settings.sync()

    if hasattr(dialog, "isp_gateway_combo") and dialog.isp_gateway_combo:
        idx = dialog.isp_gateway_combo.findData("auto")
        if idx >= 0:
            dialog.isp_gateway_combo.setCurrentIndex(idx)
    if hasattr(dialog, "experimental_acf_independent_checkbox") and dialog.experimental_acf_independent_checkbox:
        dialog.experimental_acf_independent_checkbox.setChecked(sls_detected)
    if hasattr(dialog, "achievements_checkbox") and dialog.achievements_checkbox:
        dialog.achievements_checkbox.setChecked(False)
    if hasattr(dialog, "hide_macos_depots_checkbox") and dialog.hide_macos_depots_checkbox:
        dialog.hide_macos_depots_checkbox.setChecked(True)
    if hasattr(dialog, "hide_android_depots_checkbox") and dialog.hide_android_depots_checkbox:
        dialog.hide_android_depots_checkbox.setChecked(True)

    update_rec_score_badge(dialog)
    ThemedMessageBox.information(dialog, "Settings Applied", "Recommended workflow settings applied successfully!")


def run_assfixer_check(dialog) -> None:
    logger.info("ASSfixer check triggered from Settings.")
    dialog.assfixer_check_btn.setEnabled(False)
    dialog.assfixer_status_lbl.setText("Checking config against upstream template...")
    dialog.assfixer_status_lbl.setStyleSheet("color: #7aa2f7; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
    dialog.assfixer_status_lbl.show()

    def _target():
        try:
            from utils.assfixer import check_config_status
            res = check_config_status(online=True)
        except Exception as e:
            res = (True, f"Check failed: {e}", [str(e)])
        dialog.assfixer_done_signal.emit(res)

    t = threading.Thread(target=_target, daemon=True)
    t.start()


def handle_assfixer_check_done(dialog, result) -> None:
    needs_repair, summary, details = result
    logger.info(f"ASSfixer check UI handler received result: needs_repair={needs_repair}, summary='{summary}', details_count={len(details)}")
    dialog.assfixer_check_btn.setEnabled(True)
    if needs_repair:
        dialog.assfixer_repair_btn.setEnabled(True)
        dialog.assfixer_status_lbl.setText(f"[!] {summary}")
        dialog.assfixer_status_lbl.setStyleSheet("color: #e0af68; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
        if details:
            dialog.assfixer_status_lbl.setToolTip("\n".join(details))
    else:
        dialog.assfixer_repair_btn.setEnabled(True)
        dialog.assfixer_status_lbl.setText(f"[OK] {summary}")
        dialog.assfixer_status_lbl.setStyleSheet("color: #9ece6a; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
        dialog.assfixer_status_lbl.setToolTip("")


def run_assfixer_repair(dialog) -> None:
    from ui.dialogs.assfixer_confirm import AssfixerConfirmDialog
    accent = dialog.settings.value("accent_color", "#C06C84")
    bg = dialog.settings.value("background_color", "#111318")
    confirm_dlg = AssfixerConfirmDialog(parent=dialog, accent_color=accent, bg_color=bg)
    if confirm_dlg.exec() != AssfixerConfirmDialog.DialogCode.Accepted:
        return

    dialog.assfixer_repair_btn.setEnabled(False)
    dialog.assfixer_status_lbl.setText("Repairing and synchronizing config...")
    dialog.assfixer_status_lbl.setStyleSheet("color: #7aa2f7; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
    dialog.assfixer_status_lbl.show()

    def _target():
        try:
            from utils.assfixer import repair_and_sync_config, has_config_backup
            success, msg, bak_path = repair_and_sync_config(online=True)
            has_bak = has_config_backup()
            res = (success, msg, has_bak)
        except Exception as e:
            res = (False, f"Repair error: {e}", False)
        dialog.assfixer_repair_done_signal.emit(res)

    t = threading.Thread(target=_target, daemon=True)
    t.start()


def handle_assfixer_repair_done(dialog, result) -> None:
    success, msg, has_bak = result
    dialog.assfixer_repair_btn.setEnabled(True)
    dialog.assfixer_restore_btn.setEnabled(has_bak)
    if success:
        dialog.assfixer_status_lbl.setText(f"[OK] {msg}")
        dialog.assfixer_status_lbl.setStyleSheet("color: #9ece6a; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
        ThemedMessageBox.information(dialog, "ASSfixer", msg)
    else:
        dialog.assfixer_status_lbl.setText(f"[FAIL] {msg}")
        dialog.assfixer_status_lbl.setStyleSheet("color: #f7768e; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
        ThemedMessageBox.warning(dialog, "ASSfixer", msg)


def run_assfixer_restore(dialog) -> None:
    reply = ThemedMessageBox.question(
        dialog,
        "Restore Config Backup",
        "Are you sure you want to restore the latest config backup?\nThis will overwrite current config.yaml.",
        ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
        ThemedMessageBox.StandardButton.No,
    )
    if reply != ThemedMessageBox.StandardButton.Yes:
        return

    try:
        from utils.assfixer import restore_config_backup, has_config_backup
        success, msg, bak_path = restore_config_backup()
        dialog.assfixer_restore_btn.setEnabled(has_config_backup())
        if success:
            dialog.assfixer_status_lbl.setText(f"[OK] {msg}")
            dialog.assfixer_status_lbl.setStyleSheet("color: #9ece6a; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
            dialog.assfixer_status_lbl.show()
            ThemedMessageBox.information(dialog, "Backup Restored", msg)
        else:
            dialog.assfixer_status_lbl.setText(f"[FAIL] {msg}")
            dialog.assfixer_status_lbl.setStyleSheet("color: #f7768e; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
            dialog.assfixer_status_lbl.show()
            ThemedMessageBox.warning(dialog, "Restore Failed", msg)

    except Exception as e:
        ThemedMessageBox.critical(dialog, "Restore Error", str(e))


def _launch_terminal_and_quit_app(dialog, cmd: list) -> bool:
    """Launch terminal command and gracefully quit ASSella."""
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    launched = dialog._launch_terminal_command(cmd, os.path.expanduser("~"))
    if launched:
        QTimer.singleShot(600, QApplication.instance().quit)
        return True
    return False


def _slssteam_config_path() -> Path:
    """The SLSsteam config.yaml the user has to back up."""
    try:
        from utils.yaml_config_manager import get_user_config_path
        return get_user_config_path()
    except Exception as e:
        logger.warning("Could not resolve the SLSsteam config path: %s", e)
        from utils.assfixer import DEFAULT_CONFIG_PATH
        return DEFAULT_CONFIG_PATH


def _backup_slssteam_config(config_path: Path) -> Tuple[bool, str]:
    """Copy config.yaml next to itself; the Health tab can restore it later.

    Returns ``(ok, backup_path)``; the path is empty when there was nothing
    to copy.
    """
    if not config_path.exists():
        return True, ""
    try:
        from utils.assfixer import make_backup_with_rotation
        backup = make_backup_with_rotation(config_path)
    except Exception as e:
        logger.error("Could not back up %s: %s", config_path, e)
        return False, f"Could not back up {config_path}: {e}"
    return True, str(backup)


def _confirm_slssteam_backup(dialog, installer: str = "Headcrab") -> Tuple[bool, str]:
    """Warn that the installer rewrites config.yaml, and offer the backup."""
    config_path = _slssteam_config_path()

    if installer == "smallcrab":
        msg = (
            "Headcrab - the installer smallcrab is forked from - rewrites the SLSsteam "
            "configuration when it installs, and smallcrab patches the same Steam files.\n\n"
            f"Config file:\n{config_path}\n\n"
            "Back it up first if you keep custom settings there. ASSella can make the copy "
            "for you and restore it later from Health -> SLS Config -> Restore Backup."
        )
    else:
        msg = (
            "Headcrab rewrites the SLSsteam configuration when it installs.\n\n"
            f"Config file:\n{config_path}\n\n"
            "Back it up first if you keep custom settings there. ASSella can make the copy "
            "for you and restore it later from Health -> SLS Config -> Restore Backup."
        )

    choice = ThemedMessageBox.choices(
        dialog,
        "Back Up Your SLSsteam Config",
        msg,
        [
            ("Back Up Now", "backup"),
            ("Continue Without Backup", "skip"),
            ("Cancel", "cancel"),
        ],
        default="backup",
        severity="warning",
    )

    if choice == "cancel":
        return False, ""
    if choice != "backup":
        return True, ""

    ok, backup_path = _backup_slssteam_config(config_path)
    if not ok:
        ThemedMessageBox.warning(dialog, "Backup Failed", backup_path)
        return True, ""
    if not backup_path:
        return True, ""
    return True, f"\n\nBackup created: {backup_path}"


def on_h3adcrab_clicked(dialog) -> None:
    """Prompt user with Headcrab mirror options and execute selected script in terminal."""
    proceed, backup_note = _confirm_slssteam_backup(dialog, installer="Headcrab")
    if not proceed:
        return

    choice = ThemedMessageBox.choices(
        dialog,
        "Run Headcrab",
        "Choose an installation script source to run in terminal:\n\n"
        "Option 1: Primary mirror (Cloudflare Pages)\n"
        "curl -fsSL headcrab.pages.dev | bash\n\n"
        "Option 2: Alternative mirror (runn.i.ng / GitHub)\n"
        'curl -fsSL "https://runn.i.ng/Deadboy666/h3adcr-b/refs/heads/main/headcrab.sh" | bash\n\n'
        "A terminal window will open and ASSella will close so Headcrab can safely configure SLSsteam without file conflicts."
        + backup_note,
        [
            ("Option 1 (Cloudflare)", "opt1"),
            ("Option 2 (runn.i.ng)", "opt2"),
            ("Cancel", "cancel"),
        ],
        default="opt1",
    )
    if choice == "opt1":
        cmd = ["bash", "-c", "curl -fsSL headcrab.pages.dev | bash; echo; echo '--- Done. Press Enter to close ---'; read _"]
        _launch_terminal_and_quit_app(dialog, cmd)
    elif choice == "opt2":
        cmd = ["bash", "-c", 'curl -fsSL "https://runn.i.ng/Deadboy666/h3adcr-b/refs/heads/main/headcrab.sh" | bash; echo; echo \'--- Done. Press Enter to close ---\'; read _']
        _launch_terminal_and_quit_app(dialog, cmd)


def on_r3set_crab_clicked(dialog) -> None:
    """Confirm and run reset2vanilla script in terminal without emojis."""
    reply = ThemedMessageBox.question(
        dialog,
        "Reset Steam to Vanilla",
        "This will uninstall SLSsteam and Headcrab, and put Steam client back to original.\n"
        "You will not lose the games you added.\n\n"
        "A terminal window will open and ASSella will close to allow the reset process to run.\n\n"
        "Do you want to proceed?",
        ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
        ThemedMessageBox.StandardButton.No,
    )
    if reply == ThemedMessageBox.StandardButton.Yes:
        cmd = ["bash", "-c", 'curl -fsSL "https://raw.githubusercontent.com/Deadboy666/h3adcr-b-modul3s/refs/heads/main/reset2vanilla.sh" | bash; echo; echo \'--- Done. Press Enter to close ---\'; read _']
        _launch_terminal_and_quit_app(dialog, cmd)


# ── Steam updates (steam.cfg) ───────────────────────────────────────────

def refresh_steam_updates_control(dialog) -> None:
    """Show the live state of the steam.cfg update block in the Health tab."""
    btn = getattr(dialog, "health_steam_updates_btn", None)
    lbl = getattr(dialog, "health_steam_updates_lbl", None)
    if btn is None:
        return

    from utils.steam_updates import are_steam_updates_blocked, find_steam_cfg, target_steam_cfg

    blocked = are_steam_updates_blocked()
    existing = find_steam_cfg()
    path = existing if existing is not None else target_steam_cfg()

    if blocked:
        btn.setText("Enable Steam Updates")
        if lbl is not None:
            lbl.setText(f"Steam updates are blocked - {path} exists. Steam reads it at startup, so this applies on the next Steam start.")
    else:
        btn.setText("Disable Steam Updates")
        if lbl is not None:
            lbl.setText("Steam updates are allowed - no steam.cfg found. Steam reads it at startup, so this applies on the next Steam start.")

    btn.setToolTip(
        f"Toggles {path}\n"
        "Steam reads steam.cfg when it starts, so the change applies on the next Steam start."
    )


def on_steam_updates_clicked(dialog) -> None:
    """Create or remove steam.cfg, so Steam can (or cannot) update itself."""
    from utils.steam_updates import are_steam_updates_blocked, set_steam_updates_blocked

    blocked = are_steam_updates_blocked()

    if blocked:
        reply = ThemedMessageBox.question(
            dialog,
            "Enable Steam Updates",
            "Enabling Steam updates is NOT recommended!\n\n"
            "Allowing Steam to update itself may overwrite the launcher patches and cause SLSsteam to become incompatible or stop working.\n\n"
            "Make sure you are aware of this before proceeding!\n\n"
            "Are you sure you want to enable Steam updates?",
            ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
            ThemedMessageBox.StandardButton.No,
        )
        if reply != ThemedMessageBox.StandardButton.Yes:
            return

    ok, message = set_steam_updates_blocked(not blocked)

    refresh_steam_updates_control(dialog)
    _refresh_main_window_status(dialog)

    if not ok:
        ThemedMessageBox.critical(dialog, "Steam Updates", message)
        return

    ThemedMessageBox.information(
        dialog,
        "Steam Updates",
        message + "\n\nSteam reads steam.cfg when it starts - restart Steam for this to take effect.",
    )


def _refresh_main_window_status(dialog) -> None:
    """Let the status visor show the new Steam Updates state right away."""
    main_window = getattr(dialog, "main_window", None)
    if main_window is None or not hasattr(main_window, "refresh_system_status"):
        return
    try:
        main_window.refresh_system_status()
    except Exception as e:
        logger.warning("Could not refresh the main window status: %s", e)


# ── smallcrab ───────────────────────────────────────────────────────────

SMALLCRAB_URL = "https://raw.githubusercontent.com/niwia/smallcrab/main/smallcrab.sh"


def _confirm_smallcrab(dialog, backup_note: str = "") -> bool:
    """Explain what smallcrab does, then ask whether to run it."""
    choice = ThemedMessageBox.choices(
        dialog,
        "Run smallcrab",
        "smallcrab is an experimental fork of Headcrab.\n\n"
        "What it does:\n"
        "  - Patches your Steam client so SLSsteam loads.\n"
        "  - Does not downgrade or update Steam.\n"
        "  - Does not edit your configs.\n\n"
        "It is recommended that you only run this if you know what this does.\n\n"
        "The script may also ask to create steam.cfg, which stops Steam from updating "
        "itself. You can undo that with the Steam Updates button in this tab.\n\n"
        "A terminal window will open and ASSella will close, the same as the h3adcrab "
        "button." + backup_note,
        [
            ("Run smallcrab", "run"),
            ("Cancel", "cancel"),
        ],
        default="run",
        severity="warning",
    )
    return choice == "run"


def on_smallcrab_clicked(dialog) -> None:
    """smallcrab installer is temporarily disabled."""
    return

