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
    calculate_file_sha256,
    get_sls_plugins_dirs,
    deploy_sls_plugin,
)
from utils.paths import Paths

logger = logging.getLogger(__name__)


GREEN_BTN_STYLE = """
    QPushButton {
        background-color: #2e7d32;
        color: #FFFFFF;
        border: 1px solid #4caf50;
        border-radius: 6px;
        padding: 8px 16px;
        font-size: 9pt;
        font-weight: bold;
    }
    QPushButton:disabled {
        background-color: #2e7d32;
        color: #E8F5E9;
        border: 1px solid #4caf50;
    }
"""

NORMAL_BTN_STYLE = """
    QPushButton {
        background-color: rgba(255, 255, 255, 0.08);
        color: #FFFFFF;
        border: 1px solid rgba(255, 255, 255, 0.18);
        border-radius: 6px;
        padding: 8px 16px;
        font-size: 9pt;
        font-weight: 500;
    }
    QPushButton:hover {
        background-color: rgba(255, 255, 255, 0.16);
        border: 1px solid rgba(255, 255, 255, 0.35);
    }
    QPushButton:disabled {
        background-color: rgba(255, 255, 255, 0.03);
        color: rgba(255, 255, 255, 0.25);
        border-color: rgba(255, 255, 255, 0.06);
    }
"""


def is_plugin_detected(filename: str) -> bool:
    """Check if plugin file exists in SLSsteam plugins directory and its SHA-256 matches the bundled version."""
    src_path = Paths.resource(f"plugins/{filename}")
    if not src_path.is_file():
        fallback = Path(__file__).resolve().parent.parent.parent / "res" / "plugins" / filename
        if fallback.is_file():
            src_path = fallback
        else:
            return False

    src_hash = calculate_file_sha256(src_path)
    if not src_hash:
        return False

    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return False

    primary_dst = target_dirs[0] / filename
    if not primary_dst.is_file():
        return False

    return calculate_file_sha256(primary_dst) == src_hash


def create_vapor_tab(dialog) -> QWidget:
    """
    Create the dedicated Vapor settings tab for native Steam and SLSsteam integration.
    Native Steam download is enabled by default.
    """
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(16)

    cfg_path = get_user_config_path()

    # Automatically ensure Vapor & Native Steam are enabled by default
    dialog.settings.setValue("enable_vapor", True)
    dialog.settings.setValue("use_native_steam_download", True)
    try:
        update_yaml_boolean_value(cfg_path, "Plugins", True)
    except Exception as e:
        logger.debug(f"[VaporTab] Could not ensure Plugins: yes in config: {e}")

    # No enable_vapor_checkbox in UI (default turned on)
    dialog.enable_vapor_checkbox = None

    # -- 1. General Configuration Card --
    cfg_card, cfg_layout = dialog._create_card_frame("General Configuration")

    # Option 1: Disable updates (SLSsteam AdditionalApps)
    proxy_active = False
    try:
        from utils.isp_bypass import TorManager
        proxy_active = TorManager.is_proxy_active()
    except Exception:
        pass

    sls_disable_updates = get_yaml_boolean_value(cfg_path, "DisableUpdates", default=(proxy_active or True))
    saved_disable_updates = dialog.settings.value("vapor_disable_updates", sls_disable_updates, type=bool)

    dialog.vapor_disable_updates_checkbox = create_checkbox_setting(
        "Disable updates (SLSsteam AdditionalApps)",
        "vapor_disable_updates",
        saved_disable_updates,
        dialog,
        tooltip="Recommended. Prevents Steam from attempting to update unowned AdditionalApps. Automatically enabled when proxy or plugins are active.",
        show_description=False,
    )
    cfg_layout.addWidget(dialog.vapor_disable_updates_checkbox)

    def _on_disable_updates_toggled(checked: bool):
        dialog.settings.setValue("vapor_disable_updates", checked)
        update_yaml_boolean_value(cfg_path, "DisableUpdates", checked)

    dialog.vapor_disable_updates_checkbox.toggled.connect(_on_disable_updates_toggled)

    # Separator
    sep1 = QFrame()
    sep1.setFrameShape(QFrame.Shape.HLine)
    sep1.setStyleSheet("color: rgba(255,255,255,0.08); border: none; background: rgba(255,255,255,0.08); max-height: 1px;")
    cfg_layout.addWidget(sep1)

    # Option 2: Default Download Behavior
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
    sep2 = QFrame()
    sep2.setFrameShape(QFrame.Shape.HLine)
    sep2.setStyleSheet("color: rgba(255,255,255,0.08); border: none; background: rgba(255,255,255,0.08); max-height: 1px;")
    cfg_layout.addWidget(sep2)

    # Option 3: Always Start Installation Immediately Toggle (Default: True)
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

    # -- 2. Deploy Card (Bottom Card) --
    deploy_card, deploy_layout = dialog._create_card_frame("Deploy")

    deploy_desc = QLabel(
        "Deploy bundled Lua plugins to your SLSsteam plugins directory. "
        "When detected with a matching SHA-256 hash, buttons turn green and are locked."
    )
    deploy_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    deploy_desc.setWordWrap(True)
    deploy_layout.addWidget(deploy_desc)

    deploy_btns_row = QHBoxLayout()
    deploy_btns_row.setSpacing(12)

    # Button 1: Lua Plugin
    lua_plugin_btn = QPushButton("Lua Plugin")
    deploy_btns_row.addWidget(lua_plugin_btn)

    # Button 2: Spliced Plugin
    spliced_plugin_btn = QPushButton("Spliced Plugin")
    deploy_btns_row.addWidget(spliced_plugin_btn)

    # Button 3: ASSella
    assella_btn = QPushButton("ASSella")
    deploy_btns_row.addWidget(assella_btn)

    deploy_layout.addLayout(deploy_btns_row)
    layout.addWidget(deploy_card)

    def _refresh_deploy_buttons():
        items = [
            (lua_plugin_btn, "download.lua", "Lua Plugin"),
            (spliced_plugin_btn, "spliced-tickets.lua", "Spliced Plugin"),
            (assella_btn, "assella_bridge.lua", "ASSella"),
        ]
        for btn, fname, label in items:
            detected = is_plugin_detected(fname)
            if detected:
                btn.setText(f"✓ {label}")
                btn.setStyleSheet(GREEN_BTN_STYLE)
                btn.setEnabled(False)
                btn.setCursor(Qt.CursorShape.ArrowCursor)
                btn.setToolTip(f"{label} ({fname}) is installed and SHA-256 matches bundled plugin.")
            else:
                btn.setText(f"Deploy {label}")
                btn.setStyleSheet(NORMAL_BTN_STYLE)
                btn.setEnabled(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setToolTip(f"Click to deploy {fname} to SLSsteam plugins directory.")

    def _deploy_single(filename: str, title: str):
        try:
            ok, skipped, msg = deploy_sls_plugin(filename)
            _refresh_deploy_buttons()
            if not ok:
                QMessageBox.warning(dialog, f"{title}", f"Failed deploying {filename}:\n{msg}")
            elif skipped:
                QMessageBox.information(
                    dialog,
                    f"{title}",
                    f"{title} ({filename}) is already up to date.\nSHA-256 checksum matched — skipped deployment."
                )
            else:
                QMessageBox.information(
                    dialog,
                    f"{title}",
                    f"Successfully deployed {title} ({filename}) to SLSsteam plugins folder!"
                )
        except Exception as exc:
            logger.error(f"[VaporTab] Deploy {filename} error: {exc}")
            QMessageBox.critical(dialog, "Deploy Error", f"Error deploying {filename}: {exc}")

    lua_plugin_btn.clicked.connect(lambda: _deploy_single("download.lua", "Lua Plugin"))
    spliced_plugin_btn.clicked.connect(lambda: _deploy_single("spliced-tickets.lua", "Spliced Plugin"))
    assella_btn.clicked.connect(lambda: _deploy_single("assella_bridge.lua", "ASSella"))

    # Initial state evaluation
    _refresh_deploy_buttons()

    layout.addStretch()
    dialog.tab_widget.addTab(tab, "Vapor (Beta)")
    return tab
