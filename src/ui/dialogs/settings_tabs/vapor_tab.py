import os
import sys
import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLabel,
    QComboBox,
    QPushButton,
    QMessageBox,
)

from utils.helpers import create_checkbox_setting

logger = logging.getLogger(__name__)


def create_vapor_tab(dialog) -> QWidget:
    """
    Create the dedicated Vapor (Beta) settings tab for native Steam and SLSsteam integration.
    """
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(16)

    # -- 1. Header Banner Card --
    header_card, header_layout = dialog._create_card_frame("Vapor (Beta) - Native Steam Suite")
    header_desc = QLabel(
        "Vapor integrates ASSella directly with the native Steam client and SLSsteam. "
        "When enabled, game licenses, manifest versions, and depot decryption keys are injected "
        "straight into your Steam client so you can download or manage games natively."
    )
    header_desc.setWordWrap(True)
    header_desc.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9pt; line-height: 1.4;")
    header_layout.addWidget(header_desc)
    layout.addWidget(header_card)

    # -- 2. General Configuration Card --
    cfg_card, cfg_layout = dialog._create_card_frame("General Configuration")

    # Option 1: Master Enable/Disable Toggle
    dialog.enable_vapor_checkbox = create_checkbox_setting(
        "Enable Vapor (Native Steam Integration)",
        "enable_vapor",
        False,
        dialog,
        "Master toggle for the Vapor subsystem. When disabled, ASSella operates in classic mode "
        "with standard downloaders. When enabled, native Steam options and library features become active.",
    )
    cfg_layout.addWidget(dialog.enable_vapor_checkbox)

    # Separator
    sep1 = QFrame()
    sep1.setFrameShape(QFrame.Shape.HLine)
    sep1.setStyleSheet("color: rgba(255,255,255,0.08); border: none; background: rgba(255,255,255,0.08); max-height: 1px;")
    cfg_layout.addWidget(sep1)

    # Option 2: Default Download Behavior
    act_row = QHBoxLayout()
    act_row.setContentsMargins(4, 4, 4, 4)
    act_row.setSpacing(12)

    act_label_col = QVBoxLayout()
    act_title = QLabel("Default Download Behavior")
    act_title.setStyleSheet("color: #FFFFFF; font-weight: bold; font-size: 9.5pt;")
    act_desc = QLabel("Choose which download backend to use when initiating downloads:")
    act_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt;")
    act_label_col.addWidget(act_title)
    act_label_col.addWidget(act_desc)
    act_row.addLayout(act_label_col, stretch=1)

    dialog.vapor_download_action_combo = QComboBox()
    dialog.vapor_download_action_combo.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.vapor_download_action_combo.addItem("Ask every time", "ask")
    dialog.vapor_download_action_combo.addItem("Always Native Steam (Vapor)", "native")
    dialog.vapor_download_action_combo.addItem("Always ASSella Downloader", "assella")

    saved_behavior = dialog.settings.value("vapor_default_download_action", "ask", type=str)
    cur_idx = dialog.vapor_download_action_combo.findData(saved_behavior)
    if cur_idx >= 0:
        dialog.vapor_download_action_combo.setCurrentIndex(cur_idx)

    dialog.vapor_download_action_combo.currentIndexChanged.connect(
        lambda _i: dialog.settings.setValue(
            "vapor_default_download_action",
            dialog.vapor_download_action_combo.currentData(),
        )
    )
    act_row.addWidget(dialog.vapor_download_action_combo)
    cfg_layout.addLayout(act_row)

    # Separator
    sep2 = QFrame()
    sep2.setFrameShape(QFrame.Shape.HLine)
    sep2.setStyleSheet("color: rgba(255,255,255,0.08); border: none; background: rgba(255,255,255,0.08); max-height: 1px;")
    cfg_layout.addWidget(sep2)

    # Option 3: Always Start Installation Immediately Toggle
    dialog.vapor_start_immediate_checkbox = create_checkbox_setting(
        "Always start installation immediately in Steam",
        "vapor_start_download_immediately",
        True,
        dialog,
        "When enabled, Steam automatically begins downloading files immediately upon handoff. "
        "When disabled, the game is added to your Steam library with all licenses and decryption keys ready, "
        "letting you press 'Install' in Steam whenever you want.",
    )
    cfg_layout.addWidget(dialog.vapor_start_immediate_checkbox)

    # Option 4: Simplified Depot Selection for Vapor
    dialog.vapor_depot_checklist_checkbox = create_checkbox_setting(
        "Show depot checklist for multi-depot games (No storage picker)",
        "vapor_show_depot_checklist",
        True,
        dialog,
        "When enabled, games with multiple optional depots/DLCs show a lightweight checklist "
        "allowing you to choose what to install. Steam manages drive storage automatically.",
    )
    cfg_layout.addWidget(dialog.vapor_depot_checklist_checkbox)

    layout.addWidget(cfg_card)

    # -- 3. Subsystem Health & Diagnostics Card --
    diag_card, diag_layout = dialog._create_card_frame("Subsystem Status & Health")

    from ui.dialogs.settings_sls import get_sls_paths
    paths = get_sls_paths()
    sls_detected = paths.get("detected", False)

    status_row = QHBoxLayout()
    sls_status_lbl = QLabel(
        f"<b>SLSsteam Daemon:</b> {'<font color=\"#44bb44\">Installed</font>' if sls_detected else '<font color=\"#ffaa00\">Not Detected</font>'}"
    )
    sls_status_lbl.setStyleSheet("font-size: 9pt;")
    status_row.addWidget(sls_status_lbl)

    # Plugin status check
    plugin_path = os.path.expanduser("~/.config/SLSsteam/plugins/download.lua")
    flatpak_plugin_path = os.path.expanduser("~/.var/app/com.valvesoftware.Steam/.config/SLSsteam/plugins/download.lua")
    plugin_present = os.path.exists(plugin_path) or os.path.exists(flatpak_plugin_path)
    plugin_status_lbl = QLabel(
        f"<b>Download Plugin:</b> {'<font color=\"#44bb44\">Deployed</font>' if plugin_present else '<font color=\"#ffaa00\">Missing</font>'}"
    )
    plugin_status_lbl.setStyleSheet("font-size: 9pt;")
    status_row.addWidget(plugin_status_lbl)
    diag_layout.addLayout(status_row)

    # Actions row
    action_btns_layout = QHBoxLayout()
    action_btns_layout.setSpacing(10)

    sync_cfg_btn = QPushButton("Repair / Sync SLSsteam Config")
    sync_cfg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    sync_cfg_btn.setStyleSheet("padding: 6px 14px; font-size: 9pt;")

    def _on_sync_clicked():
        try:
            from utils.assfixer import repair_and_sync_config, get_user_config_path
            cfg_path = get_user_config_path()
            if not cfg_path.exists():
                QMessageBox.warning(dialog, "Config Not Found", f"SLSsteam config file not found at: {cfg_path}")
                return
            ok, msg = repair_and_sync_config(cfg_path, online=True)
            if ok:
                QMessageBox.information(dialog, "Config Synced", "SLSsteam config.yaml verified and synced with upstream template!")
            else:
                QMessageBox.warning(dialog, "Sync Notice", f"Sync finished with notice: {msg}")
        except Exception as exc:
            logger.error(f"[VaporTab] Failed syncing config: {exc}")
            QMessageBox.critical(dialog, "Sync Error", f"Failed to sync config: {exc}")

    sync_cfg_btn.clicked.connect(_on_sync_clicked)
    action_btns_layout.addWidget(sync_cfg_btn)

    deploy_plugin_btn = QPushButton("Re-deploy Download Plugins")
    deploy_plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    deploy_plugin_btn.setStyleSheet("padding: 6px 14px; font-size: 9pt;")

    def _on_deploy_plugins():
        try:
            import shutil
            from pathlib import Path
            src_plugin = Path(__file__).resolve().parents[3] / "res" / "plugins" / "download.lua"
            if not src_plugin.exists():
                QMessageBox.warning(dialog, "Plugin Missing", "Bundled download.lua plugin not found in application assets.")
                return

            targets = [
                Path.home() / ".config/SLSsteam/plugins",
                Path.home() / ".var/app/com.valvesoftware.Steam/.config/SLSsteam/plugins",
            ]
            deployed = 0
            for tgt in targets:
                if tgt.parent.exists():
                    tgt.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_plugin, tgt / "download.lua")
                    deployed += 1

            QMessageBox.information(
                dialog,
                "Plugins Deployed",
                f"Successfully deployed download.lua to {deployed} SLSsteam location(s)."
            )
        except Exception as exc:
            logger.error(f"[VaporTab] Failed deploying plugins: {exc}")
            QMessageBox.critical(dialog, "Deploy Error", f"Failed to deploy plugins: {exc}")

    deploy_plugin_btn.clicked.connect(_on_deploy_plugins)
    action_btns_layout.addWidget(deploy_plugin_btn)

    diag_layout.addLayout(action_btns_layout)
    layout.addWidget(diag_card)

    # Dynamic widget enablement based on enable_vapor checkbox
    def _update_enabled_states(enabled: bool):
        dialog.vapor_download_action_combo.setEnabled(enabled)
        dialog.vapor_start_immediate_checkbox.setEnabled(enabled)
        dialog.vapor_depot_checklist_checkbox.setEnabled(enabled)

    dialog.enable_vapor_checkbox.toggled.connect(_update_enabled_states)
    _update_enabled_states(dialog.enable_vapor_checkbox.isChecked())

    layout.addStretch()
    dialog.tab_widget.addTab(tab, "Vapor (Beta)")
    return tab
