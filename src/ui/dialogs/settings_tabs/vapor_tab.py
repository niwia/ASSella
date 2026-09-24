import os
import sys
import logging
from typing import Optional
from pathlib import Path

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
from utils.yaml_config_manager import (
    get_user_config_path,
    get_yaml_boolean_value,
    update_yaml_boolean_value,
    deploy_sls_plugin,
    deploy_all_sls_plugins,
    are_sls_plugins_deployed,
)

logger = logging.getLogger(__name__)


def create_vapor_tab(dialog) -> QWidget:
    """
    Create the dedicated Vapor settings tab for native Steam and SLSsteam integration.
    """
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(16)

    cfg_path = get_user_config_path()

    # -- 1. General Configuration Card --
    cfg_card, cfg_layout = dialog._create_card_frame("General Configuration")

    # Read current state from SLSsteam config.yaml
    sls_plugins_enabled = get_yaml_boolean_value(cfg_path, "Plugins", default=False)
    saved_vapor_enabled = dialog.settings.value("enable_vapor", sls_plugins_enabled, type=bool)
    initial_plugins_val = sls_plugins_enabled or saved_vapor_enabled

    # Option 1: Plugin (SLSsteam Plugins Toggle)
    dialog.enable_vapor_checkbox = create_checkbox_setting(
        "Plugins (Enable Lua Plugins in SLSsteam)",
        "enable_vapor",
        initial_plugins_val,
        dialog,
        tooltip="Enables Lua plugins in SLSsteam required for Vapor downloads. Automatically deploys required plugins to your plugins folder.",
        show_description=False,
    )

    cfg_layout.addWidget(dialog.enable_vapor_checkbox)

    # Separator
    sep1 = QFrame()
    sep1.setFrameShape(QFrame.Shape.HLine)
    sep1.setStyleSheet("color: rgba(255,255,255,0.08); border: none; background: rgba(255,255,255,0.08); max-height: 1px;")
    cfg_layout.addWidget(sep1)

    # Option 2: Disable updates (SLSsteam AdditionalApps)
    # Recommended to have on. Also auto-on if proxy is active.
    proxy_active = False
    try:
        from utils.isp_bypass import TorManager
        proxy_active = TorManager.is_proxy_active()
    except Exception:
        pass

    sls_disable_updates = get_yaml_boolean_value(cfg_path, "DisableUpdates", default=(proxy_active or initial_plugins_val or True))
    saved_disable_updates = dialog.settings.value("vapor_disable_updates", sls_disable_updates, type=bool)

    dialog.vapor_disable_updates_checkbox = create_checkbox_setting(
        "Disable updates (SLSsteam AdditionalApps)",
        "vapor_disable_updates",
        saved_disable_updates,
        dialog,
        tooltip="Recommended. Prevents Steam from attempting to update unowned AdditionalApps. Automatically turns on when plugins or proxy are enabled.",
        show_description=False,
    )
    cfg_layout.addWidget(dialog.vapor_disable_updates_checkbox)

    # Wire up Disable updates toggle
    def _on_disable_updates_toggled(checked: bool):
        dialog.settings.setValue("vapor_disable_updates", checked)
        update_yaml_boolean_value(cfg_path, "DisableUpdates", checked)

    dialog.vapor_disable_updates_checkbox.toggled.connect(_on_disable_updates_toggled)

    # Wire up Plugin toggle (replaces old enable_vapor behavior)
    def _on_plugin_toggled(checked: bool):
        dialog.settings.setValue("enable_vapor", checked)
        dialog.settings.setValue("use_native_steam_download", checked)
        update_yaml_boolean_value(cfg_path, "Plugins", checked)

        if checked:
            # Condition: deploy the three required plugins into plugins folder
            deploy_all_sls_plugins()

            # Disable updates has to turn on if user enabled plugins / proxy
            if hasattr(dialog, "vapor_disable_updates_checkbox") and dialog.vapor_disable_updates_checkbox:
                if not dialog.vapor_disable_updates_checkbox.isChecked():
                    dialog.vapor_disable_updates_checkbox.setChecked(True)
                    dialog.settings.setValue("vapor_disable_updates", True)
                    update_yaml_boolean_value(cfg_path, "DisableUpdates", True)

        _update_plugin_status()

    dialog.enable_vapor_checkbox.toggled.connect(_on_plugin_toggled)

    # Separator
    sep2 = QFrame()
    sep2.setFrameShape(QFrame.Shape.HLine)
    sep2.setStyleSheet("color: rgba(255,255,255,0.08); border: none; background: rgba(255,255,255,0.08); max-height: 1px;")
    cfg_layout.addWidget(sep2)

    # Option 3: Default Download Behavior
    act_row = QHBoxLayout()
    act_row.setContentsMargins(4, 4, 4, 4)
    act_row.setSpacing(12)

    act_title = QLabel("Default Download Behavior")
    act_title.setStyleSheet("color: #FFFFFF; font-weight: bold; font-size: 9.5pt;")
    act_row.addWidget(act_title, stretch=1)

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
        lambda _i: (
            dialog.settings.setValue(
                "vapor_default_download_action",
                dialog.vapor_download_action_combo.currentData(),
            ),
            dialog.settings.setValue(
                "native_steam_default_action",
                dialog.vapor_download_action_combo.currentData(),
            ),
        )
    )
    act_row.addWidget(dialog.vapor_download_action_combo)
    cfg_layout.addLayout(act_row)

    # Separator
    sep3 = QFrame()
    sep3.setFrameShape(QFrame.Shape.HLine)
    sep3.setStyleSheet("color: rgba(255,255,255,0.08); border: none; background: rgba(255,255,255,0.08); max-height: 1px;")
    cfg_layout.addWidget(sep3)

    # Option 4: Always Start Installation Immediately Toggle (Default: True)
    dialog.vapor_start_immediate_checkbox = create_checkbox_setting(
        "Always start installation immediately in Steam",
        "vapor_start_download_immediately",
        True,
        dialog,
        tooltip=None,
        show_description=False,
    )
    dialog.vapor_start_immediate_checkbox.toggled.connect(
        lambda checked: dialog.settings.setValue("vapor_start_download_immediately", checked)
    )
    cfg_layout.addWidget(dialog.vapor_start_immediate_checkbox)

    layout.addWidget(cfg_card)

    # -- 2. Subsystem Status & Health Card --
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

    # Plugin status check for all 3 plugins
    plugin_status_lbl = QLabel()
    plugin_status_lbl.setStyleSheet("font-size: 9pt;")
    status_row.addWidget(plugin_status_lbl)

    def _update_plugin_status():
        all_deployed = are_sls_plugins_deployed()
        plugin_status_lbl.setText(
            f"<b>Plugins:</b> {'<font color=\"#44bb44\">All Deployed</font>' if all_deployed else '<font color=\"#ffaa00\">Incomplete</font>'}"
        )

    _update_plugin_status()
    diag_layout.addLayout(status_row)

    # Actions row for repair/sync
    action_btns_layout = QHBoxLayout()
    action_btns_layout.setSpacing(10)

    sync_cfg_btn = QPushButton("Repair / Sync SLSsteam Config")
    sync_cfg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    sync_cfg_btn.setStyleSheet("padding: 6px 14px; font-size: 9pt;")

    def _on_sync_clicked():
        try:
            from utils.assfixer import repair_and_sync_config, get_user_config_path
            cfg_p = get_user_config_path()
            if not cfg_p.exists():
                QMessageBox.warning(dialog, "Config Not Found", f"SLSsteam config file not found at: {cfg_p}")
                return
            ok, msg = repair_and_sync_config(cfg_p, online=True)
            if ok:
                QMessageBox.information(dialog, "Config Synced", "SLSsteam config.yaml verified and synced with upstream template!")
            else:
                QMessageBox.warning(dialog, "Sync Notice", f"Sync finished with notice: {msg}")
        except Exception as exc:
            logger.error(f"[VaporTab] Failed syncing config: {exc}")
            QMessageBox.critical(dialog, "Sync Error", f"Failed to sync config: {exc}")

    sync_cfg_btn.clicked.connect(_on_sync_clicked)
    action_btns_layout.addWidget(sync_cfg_btn)
    action_btns_layout.addStretch()

    diag_layout.addLayout(action_btns_layout)
    layout.addWidget(diag_card)

    # -- 3. Deploy Card (Bottom Card) --
    deploy_card, deploy_layout = dialog._create_card_frame("Deploy")

    deploy_desc = QLabel(
        "Deploy bundled Lua plugins to your SLSsteam plugins folder. "
        "Files with matching SHA-256 checksums are automatically skipped."
    )
    deploy_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    deploy_desc.setWordWrap(True)
    deploy_layout.addWidget(deploy_desc)

    deploy_btns_row = QHBoxLayout()
    deploy_btns_row.setSpacing(12)

    btn_style = """
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            color: #FFFFFF;
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 6px;
            padding: 7px 16px;
            font-size: 9pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.15);
            border: 1px solid rgba(255, 255, 255, 0.3);
        }
        QPushButton:disabled {
            background-color: rgba(255, 255, 255, 0.02);
            color: rgba(255, 255, 255, 0.2);
            border-color: rgba(255, 255, 255, 0.05);
        }
    """

    def _deploy_single(filename: str, title: str):
        try:
            ok, skipped, msg = deploy_sls_plugin(filename)
            _update_plugin_status()
            if not ok:
                QMessageBox.warning(dialog, f"{title}", f"Failed deploying {filename}:\n{msg}")
            elif skipped:
                QMessageBox.information(
                    dialog,
                    f"{title}",
                    f"{title} ({filename}) is already up to date.\nSHA-256 checksum matches — skipped deployment."
                )
            else:
                QMessageBox.information(
                    dialog,
                    f"{title}",
                    f"Successfully deployed {title} ({filename}) to SLSsteam plugins folder."
                )
        except Exception as exc:
            logger.error(f"[VaporTab] Deploy {filename} error: {exc}")
            QMessageBox.critical(dialog, "Deploy Error", f"Error deploying {filename}: {exc}")

    # Button 1: Lua Plugin (download.lua)
    lua_plugin_btn = QPushButton("Lua Plugin")
    lua_plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    lua_plugin_btn.setStyleSheet(btn_style)
    lua_plugin_btn.setToolTip("Deploy download.lua to SLSsteam plugins directory")
    lua_plugin_btn.clicked.connect(lambda: _deploy_single("download.lua", "Lua Plugin"))
    deploy_btns_row.addWidget(lua_plugin_btn)

    # Button 2: Spliced Plugin (spliced-tickets.lua)
    spliced_plugin_btn = QPushButton("Spliced Plugin")
    spliced_plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    spliced_plugin_btn.setStyleSheet(btn_style)
    spliced_plugin_btn.setToolTip("Deploy spliced-tickets.lua to SLSsteam plugins directory")
    spliced_plugin_btn.clicked.connect(lambda: _deploy_single("spliced-tickets.lua", "Spliced Plugin"))
    deploy_btns_row.addWidget(spliced_plugin_btn)

    # Button 3: ASSella (assella_bridge.lua)
    assella_btn = QPushButton("ASSella")
    assella_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    assella_btn.setStyleSheet(btn_style)
    assella_btn.setToolTip("Deploy assella_bridge.lua to SLSsteam plugins directory")
    assella_btn.clicked.connect(lambda: _deploy_single("assella_bridge.lua", "ASSella Plugin"))
    deploy_btns_row.addWidget(assella_btn)

    deploy_layout.addLayout(deploy_btns_row)
    layout.addWidget(deploy_card)

    # Dynamic widget enablement based on enable_vapor / plugins checkbox
    def _update_enabled_states(enabled: bool):
        dialog.vapor_download_action_combo.setEnabled(enabled)
        dialog.vapor_start_immediate_checkbox.setEnabled(enabled)
        dialog.vapor_disable_updates_checkbox.setEnabled(enabled)

    dialog.enable_vapor_checkbox.toggled.connect(_update_enabled_states)
    _update_enabled_states(dialog.enable_vapor_checkbox.isChecked())

    layout.addStretch()
    dialog.tab_widget.addTab(tab, "Vapor (Beta)")
    return tab
