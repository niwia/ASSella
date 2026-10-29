import os
import sys
import shutil
import logging
from typing import Optional
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from ui.assets import GEAR_SVG
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLabel,
    QComboBox,
    QPushButton,
    QMenu,
    QFileDialog,
    QTabWidget,
    QScrollArea,
)
from ui.dialogs.themed_message_box import ThemedMessageBox

from utils.helpers import create_checkbox_setting
from utils.color_utils import get_semantic_colors
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


def _get_detected_btn_style(dialog) -> str:
    """Generate theme-harmonized button style for detected plugins using color utils."""
    accent = getattr(dialog, "accent_color", "#ff7518")
    c = QColor(accent)
    r, g, b = c.red(), c.green(), c.blue()
    return f"""
        QPushButton {{
            background-color: {accent};
            color: #000000;
            border: 1px solid {accent};
            border-radius: 6px;
            padding: 8px 16px;
            font-size: 9pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: #FFFFFF;
            color: #000000;
        }}
        QPushButton:disabled {{
            background-color: rgba({r}, {g}, {b}, 0.14);
            color: rgba(255, 255, 255, 0.90);
            border: 1px solid rgba({r}, {g}, {b}, 0.35);
        }}
    """


def _get_outdated_btn_style(dialog) -> str:
    """Button style for plugins that are installed but differ from Cloud manifest (custom or outdated)."""
    return """
        QPushButton {
            background-color: rgba(255, 171, 0, 0.18);
            color: #FFD54F;
            border: 1px solid rgba(255, 171, 0, 0.55);
            border-radius: 6px;
            padding: 8px 16px;
            font-size: 9pt;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: rgba(255, 171, 0, 0.32);
            border-color: rgba(255, 171, 0, 0.85);
            color: #FFFFFF;
        }
        QPushButton:disabled {
            background-color: rgba(255, 171, 0, 0.08);
            color: rgba(255, 213, 79, 0.5);
            border-color: rgba(255, 171, 0, 0.2);
        }
    """


def get_active_download_plugin() -> Optional[str]:
    """Find the active download interceptor plugin filename in SLSsteam plugins directory."""
    try:
        from utils.plugin_manager import get_active_interceptor_filename
        return get_active_interceptor_filename()
    except Exception as e:
        logger.debug(f"[at0mTab] Check active download plugin error: {e}")
    return None


def get_available_download_plugins() -> list[tuple[str, str]]:
    """Return available download plugins as list of (display_name, filename)."""
    try:
        from utils.plugin_manager import (
            get_default_interceptor_from_manifest,
            get_plugin_version,
            get_manifest_plugin_info,
        )
        def_interceptor = get_default_interceptor_from_manifest()
        sb_ver = get_plugin_version("spacebunny.lua") or ""
        std_ver = get_plugin_version("download.lua") or ""
    except Exception:
        def_interceptor = "spacebunny.lua"
        sb_ver = ""
        std_ver = ""

    sb_title = f"SpaceBunny {sb_ver}".strip()
    std_title = f"Standard {std_ver}".strip()

    is_sb_default = def_interceptor in ("spacebunny.lua", "spacebunny1.5.lua")
    sb_label = f"{sb_title} (Default)" if is_sb_default else sb_title
    std_label = f"{std_title} (download.lua) (Default)" if def_interceptor == "download.lua" else f"{std_title} (download.lua)"

    sb_filename = "spacebunny.lua" if (def_interceptor == "spacebunny.lua" or get_manifest_plugin_info("spacebunny.lua") or not get_manifest_plugin_info("spacebunny1.5.lua")) else "spacebunny1.5.lua"
    options = [
        (sb_label, sb_filename),
        (std_label, "download.lua"),
    ]
    try:
        from utils.helpers import get_base_path
        from utils.yaml_config_manager import get_sls_plugins_dirs
        known_filenames = {opt[1] for opt in options}
        search_dirs = [
            get_base_path() / "plugins",
            Path.home() / ".local" / "share" / "ACCELA" / "plugins",
        ] + get_sls_plugins_dirs()
        for sdir in search_dirs:
            if sdir.is_dir():
                for f in sdir.glob("*.lua"):
                    if (
                        not f.name.endswith(".bak")
                        and not f.name.startswith(".")
                        and f.name not in ("spliced-tickets.lua", "lobster.lua", "hubcap.lua")
                        and f.name not in known_filenames
                    ):
                        options.append((f"{f.name}", f.name))
                        known_filenames.add(f.name)
    except Exception as e:
        logger.debug(f"[at0mTab] Discover plugins error: {e}")
    return options


def get_plugin_state(filename: str) -> str:
    """Returns 'up_to_date', 'outdated', or 'missing' comparing with Cloud manifest."""
    try:
        from utils.plugin_manager import get_plugin_status
        return get_plugin_status(filename)
    except Exception as e:
        logger.debug(f"[at0mTab] Check plugin status error: {e}")
        return "missing"


def is_plugin_detected(filename: str) -> bool:
    """Check if plugin file exists in SLSsteam plugins directory and is valid."""
    return get_plugin_state(filename) != "missing"


def create_at0m_tab(dialog) -> QWidget:
    """
    Create the dedicated at0-m settings tab with General and Advanced (Plugins) sub-tabs.
    """
    tab = QWidget()
    main_layout = QVBoxLayout(tab)
    main_layout.setContentsMargins(12, 10, 12, 10)
    main_layout.setSpacing(8)

    atom_subtabs = QTabWidget()
    atom_subtabs.setUsesScrollButtons(True)
    dialog.atom_subtabs = atom_subtabs
    bg_color = dialog.settings.value("background_color", "#141416")
    atom_subtabs.setStyleSheet(f"""
        QTabWidget::pane {{
            border: none;
        }}
        QTabBar::tab {{
            background: {bg_color};
            color: rgba(255, 255, 255, 0.6);
            padding: 6px 16px;
            border: none;
            font-weight: bold;
            font-size: 9.5pt;
        }}
        QTabBar::tab:selected {{
            color: {dialog.accent_color};
            border-bottom: 2px solid {dialog.accent_color};
        }}
        QTabBar::tab:hover {{
            color: #FFFFFF;
        }}
    """)

    # ── Subtab 1: General ──
    general_tab = QWidget()
    gen_scroll = QScrollArea()
    gen_scroll.setWidgetResizable(True)
    gen_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    gen_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    gen_container = QWidget()
    layout = QVBoxLayout(gen_container)
    layout.setContentsMargins(0, 4, 0, 10)
    layout.setSpacing(8)
    gen_scroll.setWidget(gen_container)

    gen_tab_layout = QVBoxLayout(general_tab)
    gen_tab_layout.setContentsMargins(0, 0, 0, 0)
    gen_tab_layout.addWidget(gen_scroll)

    cfg_path = get_user_config_path()

    # -- 1. General Configuration Card --
    cfg_card, cfg_layout = dialog._create_card_frame("General Configuration")

    # Master Option: Enable Lua Plugins (SLSsteam)
    sls_plugins_val = get_yaml_boolean_value(cfg_path, "Plugins", default=True)
    saved_enable_at0m = dialog.settings.value(
        "enable_at0m",
        dialog.settings.value("enable_vapor", sls_plugins_val, type=bool),
        type=bool,
    )

    dialog.enable_at0m_checkbox = create_checkbox_setting(
        "Enable Lua Plugins (SLSsteam)",
        "enable_at0m",
        saved_enable_at0m,
        dialog,
        tooltip="Master switch for SLSsteam Lua plugins. Automatically deploys bundled plugins and enables Plugins in SLSsteam config.yaml.",
        show_description=False,
    )
    dialog.enable_vapor_checkbox = dialog.enable_at0m_checkbox
    cfg_layout.addWidget(dialog.enable_at0m_checkbox)

    # Top separator
    sep_top = QFrame()
    sep_top.setFrameShape(QFrame.Shape.HLine)
    sep_top.setStyleSheet("color: rgba(255,255,255,0.08); border: none; background: rgba(255,255,255,0.08); max-height: 1px;")
    cfg_layout.addWidget(sep_top)

    # DisableUpdates stays enforced for every install (a Steam update would
    # replace the files SLSsteam patches). src/main.py applies it at startup so
    # merely opening this page never rewrites the SLSsteam config.
    dialog.at0m_disable_updates_checkbox = None
    dialog.vapor_disable_updates_checkbox = None

    # Option 2: Default Download Behavior
    act_row = QHBoxLayout()
    act_row.setContentsMargins(2, 2, 2, 2)
    act_row.setSpacing(10)

    act_title = QLabel("Default Download Behavior")
    act_title.setStyleSheet("color: #FFFFFF; font-weight: bold; font-size: 9pt;")
    act_row.addWidget(act_title, stretch=1)

    dialog.at0m_download_action_combo = QComboBox()
    dialog.at0m_download_action_combo.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.at0m_download_action_combo.setFixedWidth(155)
    dialog.at0m_download_action_combo.addItem("Always ask", "ask")
    dialog.at0m_download_action_combo.addItem("Start Native (at0-m)", "native")
    dialog.at0m_download_action_combo.addItem("Start ASSella", "assella")
    dialog.vapor_download_action_combo = dialog.at0m_download_action_combo

    saved_behavior = dialog.settings.value(
        "at0m_default_download_action",
        dialog.settings.value("vapor_default_download_action", "ask", type=str),
        type=str,
    )
    cur_idx = dialog.at0m_download_action_combo.findData(saved_behavior)
    if cur_idx >= 0:
        dialog.at0m_download_action_combo.setCurrentIndex(cur_idx)

    dialog.at0m_download_action_combo.currentIndexChanged.connect(
        lambda _i: (
            dialog.settings.setValue(
                "at0m_default_download_action",
                dialog.at0m_download_action_combo.currentData(),
            ),
            dialog.settings.setValue(
                "vapor_default_download_action",
                dialog.at0m_download_action_combo.currentData(),
            ),
            dialog.settings.setValue(
                "native_steam_default_action",
                dialog.at0m_download_action_combo.currentData(),
            ),
        )
    )
    act_row.addWidget(dialog.at0m_download_action_combo)
    cfg_layout.addLayout(act_row)

    # Option 3: Start download with Steam
    start_row = QHBoxLayout()
    start_row.setContentsMargins(2, 2, 2, 2)
    start_row.setSpacing(10)

    start_title = QLabel("Start download with Steam")
    start_title.setStyleSheet("color: #FFFFFF; font-weight: bold; font-size: 9pt;")
    start_row.addWidget(start_title, stretch=1)

    dialog.at0m_start_mode_combo = QComboBox()
    dialog.at0m_start_mode_combo.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.at0m_start_mode_combo.setFixedWidth(155)
    dialog.at0m_start_mode_combo.addItem("Start auto", "immediate")
    dialog.at0m_start_mode_combo.addItem("Add only", "add_only")
    dialog.at0m_start_mode_combo.addItem("Always ask", "ask")
    dialog.vapor_start_mode_combo = dialog.at0m_start_mode_combo

    saved_start_action = dialog.settings.value(
        "at0m_start_download_action",
        dialog.settings.value("vapor_start_download_action", "", type=str),
        type=str,
    )
    if not saved_start_action:
        old_imm = dialog.settings.value(
            "at0m_start_download_immediately",
            dialog.settings.value("vapor_start_download_immediately", True, type=bool),
            type=bool,
        )
        saved_start_action = "immediate" if old_imm else "add_only"

    s_idx = dialog.at0m_start_mode_combo.findData(saved_start_action)
    if s_idx >= 0:
        dialog.at0m_start_mode_combo.setCurrentIndex(s_idx)

    dialog.at0m_start_mode_combo.currentIndexChanged.connect(
        lambda _i: (
            dialog.settings.setValue(
                "at0m_start_download_action",
                dialog.at0m_start_mode_combo.currentData(),
            ),
            dialog.settings.setValue(
                "vapor_start_download_action",
                dialog.at0m_start_mode_combo.currentData(),
            ),
            dialog.settings.setValue(
                "at0m_start_download_immediately",
                dialog.at0m_start_mode_combo.currentData() == "immediate",
            ),
            dialog.settings.setValue(
                "vapor_start_download_immediately",
                dialog.at0m_start_mode_combo.currentData() == "immediate",
            ),
            dialog.settings.sync(),
        )
    )
    start_row.addWidget(dialog.at0m_start_mode_combo)
    cfg_layout.addLayout(start_row)

    # Separator
    sep2 = QFrame()
    sep2.setFrameShape(QFrame.Shape.HLine)
    sep2.setStyleSheet("color: rgba(255,255,255,0.08); border: none; background: rgba(255,255,255,0.08); max-height: 1px;")
    cfg_layout.addWidget(sep2)

    # Option 4: Default DLC-Only Behavior (after separator)
    dlc_act_row = QHBoxLayout()
    dlc_act_row.setContentsMargins(2, 2, 2, 2)
    dlc_act_row.setSpacing(10)

    dlc_act_title = QLabel("Default DLC-Only Behavior")
    dlc_act_title.setStyleSheet("color: #FFFFFF; font-weight: bold; font-size: 9pt;")
    dlc_act_row.addWidget(dlc_act_title, stretch=1)

    dialog.at0m_dlc_action_combo = QComboBox()
    dialog.at0m_dlc_action_combo.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.at0m_dlc_action_combo.setFixedWidth(155)
    dialog.at0m_dlc_action_combo.addItem("Always ask", "ask")
    dialog.at0m_dlc_action_combo.addItem("Start Native (at0-m)", "native")
    dialog.at0m_dlc_action_combo.addItem("Start ASSella", "assella")

    saved_dlc_behavior = dialog.settings.value(
        "at0m_default_dlc_action",
        "ask",
        type=str,
    )
    cur_dlc_idx = dialog.at0m_dlc_action_combo.findData(saved_dlc_behavior)
    if cur_dlc_idx >= 0:
        dialog.at0m_dlc_action_combo.setCurrentIndex(cur_dlc_idx)

    def _on_dlc_action_changed(_i):
        dialog.settings.setValue(
            "at0m_default_dlc_action",
            dialog.at0m_dlc_action_combo.currentData(),
        )
        dialog.settings.sync()

    dialog.at0m_dlc_action_combo.currentIndexChanged.connect(_on_dlc_action_changed)
    dlc_act_row.addWidget(dialog.at0m_dlc_action_combo)
    cfg_layout.addLayout(dlc_act_row)


    layout.addWidget(cfg_card)

    # -- 2. Katamari Plugins Status & Updaters Card --
    deploy_card, deploy_layout = dialog._create_card_frame("Lua Plugins (Katamari Suite)")
    deploy_layout.setSpacing(8)

    deploy_desc = QLabel(
        "Status and updaters for Katamari Lua plugins. Click an active plugin to verify against the "
        "manifest, or click an outdated plugin to install the update inline."
    )
    deploy_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    deploy_desc.setWordWrap(True)
    deploy_layout.addWidget(deploy_desc)

    STYLE_ACTIVE_GREEN = """
        QPushButton {
            background-color: rgba(76, 175, 80, 0.2);
            color: #81C784;
            border: 1px solid rgba(129, 199, 132, 0.5);
            border-radius: 6px;
            padding: 7px 10px;
            font-size: 8.5pt;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: rgba(76, 175, 80, 0.32);
            border-color: rgba(129, 199, 132, 0.85);
        }
    """
    STYLE_ATTENTION_ORANGE = """
        QPushButton {
            background-color: rgba(255, 171, 0, 0.18);
            color: #FFD54F;
            border: 1px solid rgba(255, 171, 0, 0.55);
            border-radius: 6px;
            padding: 7px 10px;
            font-size: 8.5pt;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: rgba(255, 171, 0, 0.32);
            border-color: rgba(255, 171, 0, 0.85);
            color: #FFFFFF;
        }
    """
    STYLE_DISABLED = """
        QPushButton {
            background-color: rgba(255, 255, 255, 0.03);
            color: rgba(255, 255, 255, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 6px;
            padding: 7px 10px;
            font-size: 8.5pt;
            font-weight: 500;
        }
    """

    # Top Row: 3 buttons in a single line (SpaceBunny, Bakeneko, Styx)
    primary_row = QHBoxLayout()
    primary_row.setContentsMargins(0, 4, 0, 4)
    primary_row.setSpacing(8)

    dialog.btn_spacebunny_plugin = QPushButton("SpaceBunny")
    dialog.btn_spacebunny_plugin.setCursor(Qt.CursorShape.PointingHandCursor)
    primary_row.addWidget(dialog.btn_spacebunny_plugin, 1)

    dialog.btn_bakeneko_plugin = QPushButton("Bakeneko (Planned)")
    dialog.btn_bakeneko_plugin.setEnabled(False)
    dialog.btn_bakeneko_plugin.setStyleSheet(STYLE_DISABLED)
    dialog.btn_bakeneko_plugin.setToolTip("Bakeneko manifest pre-seeding is currently in testing and will be enabled in a future release.")
    primary_row.addWidget(dialog.btn_bakeneko_plugin, 1)

    dialog.btn_styx_plugin = QPushButton("Styx")
    dialog.btn_styx_plugin.setCursor(Qt.CursorShape.PointingHandCursor)
    primary_row.addWidget(dialog.btn_styx_plugin, 1)

    deploy_layout.addLayout(primary_row)

    # Extras section (Lobster, Katamari, Hubcap)
    extras_lbl = QLabel("Extras")
    extras_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.9); font-size: 9pt; font-weight: 700; margin-top: 6px;")
    deploy_layout.addWidget(extras_lbl)

    extras_row = QHBoxLayout()
    extras_row.setContentsMargins(0, 2, 0, 4)
    extras_row.setSpacing(8)

    dialog.btn_lobster_plugin = QPushButton("Lobster")
    dialog.btn_lobster_plugin.setCursor(Qt.CursorShape.PointingHandCursor)
    extras_row.addWidget(dialog.btn_lobster_plugin, 1)

    dialog.btn_katamari_plugin = QPushButton("Katamari")
    dialog.btn_katamari_plugin.setCursor(Qt.CursorShape.PointingHandCursor)
    extras_row.addWidget(dialog.btn_katamari_plugin, 1)

    dialog.btn_hubcap_plugin = QPushButton("Hubcap")
    dialog.btn_hubcap_plugin.setCursor(Qt.CursorShape.PointingHandCursor)
    extras_row.addWidget(dialog.btn_hubcap_plugin, 1)

    deploy_layout.addLayout(extras_row)

    # Separator
    deploy_sep = QFrame()
    deploy_sep.setFrameShape(QFrame.Shape.HLine)
    deploy_sep.setStyleSheet("color: rgba(255,255,255,0.06); border: none; background: rgba(255,255,255,0.06); max-height: 1px;")
    deploy_layout.addWidget(deploy_sep)

    # Advanced custom plugins toggle
    dialog.custom_plugins_checkbox = create_checkbox_setting(
        "I'm using custom plugins (Advanced)",
        "custom_plugins_advanced",
        False,
        dialog,
        "Bypasses plugin missing checks for game transfers and native downloads. Disables non-family archiving.",
        show_description=False,
    )
    deploy_layout.addWidget(dialog.custom_plugins_checkbox)

    layout.addWidget(deploy_card)

    def _refresh_plugin_btn(btn: QPushButton, filename: str, display_name: str):
        from utils.plugin_manager import (
            get_plugin_status,
            get_plugin_version,
            is_plugin_check_bypassed,
            get_manifest_plugin_info,
        )
        is_custom = is_plugin_check_bypassed()
        meta = get_manifest_plugin_info(filename) or {}
        is_req = meta.get("required", False)
        is_def = meta.get("is_default", False) or meta.get("default", False)

        tag = ""
        if is_def:
            tag = " (Default)"
        elif is_req:
            tag = " (Required)"

        if is_custom:
            btn.setEnabled(False)
            btn.setText(f"{display_name}{tag}")
            btn.setToolTip("Custom plugins option is enabled. Verification is bypassed.")
            btn.setStyleSheet(STYLE_DISABLED)
            btn.setProperty("plugin_state", "custom")
            return

        status = get_plugin_status(filename)
        ver = get_plugin_version(filename) or meta.get("version", "")
        ver_str = f" v{ver}" if ver else ""

        if status == "up_to_date":
            btn.setEnabled(True)
            btn.setText(f"{display_name}{ver_str} (Up to date)")
            btn.setToolTip(f"{filename} is installed and verified against manifest. Click to re-check.")
            btn.setStyleSheet(STYLE_ACTIVE_GREEN)
            btn.setProperty("plugin_state", "up_to_date")
        elif status == "missing":
            btn.setEnabled(True)
            btn.setText(f"{display_name}{tag} (Missing)")
            btn.setToolTip(f"{filename} is not installed. Click to download and install.")
            btn.setStyleSheet(STYLE_ATTENTION_ORANGE)
            btn.setProperty("plugin_state", "missing")
        else:  # outdated
            btn.setEnabled(True)
            btn.setText(f"{display_name} (Update Available)")
            btn.setToolTip(f"{filename} is outdated. Click to download and install the update.")
            btn.setStyleSheet(STYLE_ATTENTION_ORANGE)
            btn.setProperty("plugin_state", "outdated")

    def _handle_plugin_click(btn: QPushButton, filename: str, display_name: str):
        from PyQt6.QtWidgets import QApplication
        from utils.plugin_manager import deploy_plugin, fetch_plugins_manifest

        curr_state = btn.property("plugin_state")
        if curr_state in ("missing", "outdated"):
            # Update/install action
            btn.setEnabled(False)
            btn.setText(f"Updating {display_name}...")
            btn.setStyleSheet(STYLE_DISABLED)
            QApplication.processEvents()

            ok, skipped, msg = deploy_plugin(filename, force_download=True)
            _refresh_plugin_btn(btn, filename, display_name)
        else:
            # Check for update action
            btn.setEnabled(False)
            btn.setText("Checking...")
            btn.setStyleSheet(STYLE_DISABLED)
            QApplication.processEvents()

            try:
                fetch_plugins_manifest(force_refresh=True)
            except Exception as e:
                logger.debug(f"[at0mTab] Manifest refresh failed during check: {e}")

            _refresh_plugin_btn(btn, filename, display_name)

    def _refresh_all_buttons():
        _refresh_plugin_btn(dialog.btn_spacebunny_plugin, "spacebunny.lua", "SpaceBunny")
        _refresh_plugin_btn(dialog.btn_styx_plugin, "styx.lua", "Styx")
        _refresh_plugin_btn(dialog.btn_lobster_plugin, "lobster.lua", "Lobster")
        _refresh_plugin_btn(dialog.btn_katamari_plugin, "katamari.lua", "Katamari")
        _refresh_plugin_btn(dialog.btn_hubcap_plugin, "hubcap.lua", "Hubcap")

    dialog.btn_spacebunny_plugin.clicked.connect(
        lambda: _handle_plugin_click(dialog.btn_spacebunny_plugin, "spacebunny.lua", "SpaceBunny")
    )
    dialog.btn_styx_plugin.clicked.connect(
        lambda: _handle_plugin_click(dialog.btn_styx_plugin, "styx.lua", "Styx")
    )
    dialog.btn_lobster_plugin.clicked.connect(
        lambda: _handle_plugin_click(dialog.btn_lobster_plugin, "lobster.lua", "Lobster")
    )
    dialog.btn_katamari_plugin.clicked.connect(
        lambda: _handle_plugin_click(dialog.btn_katamari_plugin, "katamari.lua", "Katamari")
    )
    dialog.btn_hubcap_plugin.clicked.connect(
        lambda: _handle_plugin_click(dialog.btn_hubcap_plugin, "hubcap.lua", "Hubcap")
    )

    def _on_custom_plugins_toggled(checked: bool):
        dialog.settings.setValue("custom_plugins_advanced", checked)
        dialog.settings.sync()
        if checked:
            ThemedMessageBox.warning(
                dialog,
                "Custom Plugins (Advanced)",
                "Bypassing plugin presence verification enables native downloads and game transfers with custom or third-party Lua plugins.\n\n"
                "Please note: ASSella cannot guarantee compatibility, functionality, or stability with third-party plugins!"
            )
        _refresh_all_buttons()

    dialog.custom_plugins_checkbox.checkbox.toggled.connect(_on_custom_plugins_toggled)

    # Initial populate
    _refresh_all_buttons()

    def _update_subwidget_states(enabled: bool):
        if getattr(dialog, "at0m_disable_updates_checkbox", None):
            dialog.at0m_disable_updates_checkbox.setEnabled(enabled)
        dialog.at0m_download_action_combo.setEnabled(enabled)
        dialog.at0m_start_mode_combo.setEnabled(enabled)
        dialog.at0m_dlc_action_combo.setEnabled(enabled)
        deploy_card.setEnabled(enabled)
        atom_subtabs.setTabEnabled(1, enabled)

    def _on_enable_at0m_toggled(checked: bool):
        dialog.settings.setValue("enable_at0m", checked)
        dialog.settings.setValue("enable_vapor", checked)
        dialog.settings.setValue("use_native_steam_download", checked)
        try:
            update_yaml_boolean_value(cfg_path, "Plugins", checked)
        except Exception as e:
            logger.debug(f"[at0mTab] Could not update Plugins in SLS config: {e}")
        if checked:
            from utils.yaml_config_manager import ensure_sls_plugin_config_sections
            ensure_sls_plugin_config_sections(cfg_path)

        _update_subwidget_states(checked)

    dialog.enable_at0m_checkbox.toggled.connect(_on_enable_at0m_toggled)
    _update_subwidget_states(dialog.enable_at0m_checkbox.isChecked())

    layout.addStretch()

    # ── Subtab 2: Advanced (Plugins configuration) ──
    from ui.dialogs.settings_tabs.plugins_tab import create_plugins_tab
    advanced_tab = create_plugins_tab(dialog, add_to_dialog=False)

    atom_subtabs.addTab(general_tab, "General")
    atom_subtabs.addTab(advanced_tab, "Advanced")
    main_layout.addWidget(atom_subtabs)

    dialog.tab_widget.addTab(tab, "at0-m")
    return tab


# Backward compatibility alias
create_vapor_tab = create_at0m_tab
