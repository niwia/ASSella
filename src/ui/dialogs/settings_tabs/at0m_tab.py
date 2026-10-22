import os
import sys
import shutil
import logging
from typing import Optional
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
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
    options = [
        ("Standard (download.lua)", "download.lua"),
        ("SpaceBunny 1.5", "spacebunny1.5.lua"),
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
                        and f.name != "spliced-tickets.lua"
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
    Create the dedicated at0-m settings tab for native Steam and SLSsteam integration.
    """
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)

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

    # Separator
    sep2 = QFrame()
    sep2.setFrameShape(QFrame.Shape.HLine)
    sep2.setStyleSheet("color: rgba(255,255,255,0.08); border: none; background: rgba(255,255,255,0.08); max-height: 1px;")
    cfg_layout.addWidget(sep2)

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
        )
    )
    start_row.addWidget(dialog.at0m_start_mode_combo)
    cfg_layout.addLayout(start_row)

    layout.addWidget(cfg_card)

    # -- 2. Deploy Card (3 Dynamic Buttons: Standard, SpaceBunny 1.5, Spliced Tickets) --
    deploy_card, deploy_layout = dialog._create_card_frame("Deploy Lua Plugins")
    deploy_layout.setSpacing(8)

    # Three Dynamic Buttons Row
    buttons_row = QHBoxLayout()
    buttons_row.setContentsMargins(0, 4, 0, 4)
    buttons_row.setSpacing(8)

    # 1. Standard Plugin button
    dialog.btn_standard_plugin = QPushButton("Standard")
    dialog.btn_standard_plugin.setCursor(Qt.CursorShape.PointingHandCursor)
    buttons_row.addWidget(dialog.btn_standard_plugin, 1)

    # 2. SpaceBunny 1.5 button
    dialog.btn_spacebunny_plugin = QPushButton("SpaceBunny 1.5")
    dialog.btn_spacebunny_plugin.setCursor(Qt.CursorShape.PointingHandCursor)
    buttons_row.addWidget(dialog.btn_spacebunny_plugin, 1)

    # 3. Spliced Tickets button
    dialog.btn_spliced_tickets = QPushButton("Spliced Tickets")
    dialog.btn_spliced_tickets.setCursor(Qt.CursorShape.PointingHandCursor)
    buttons_row.addWidget(dialog.btn_spliced_tickets, 1)

    deploy_layout.addLayout(buttons_row)

    # Separator
    deploy_sep = QFrame()
    deploy_sep.setFrameShape(QFrame.Shape.HLine)
    deploy_sep.setStyleSheet("color: rgba(255,255,255,0.06); border: none; background: rgba(255,255,255,0.06); max-height: 1px;")
    deploy_layout.addWidget(deploy_sep)

    # Checkbox for custom plugins (Advanced)
    dialog.custom_plugins_checkbox = create_checkbox_setting(
        "I'm using custom plugins (Advanced)",
        "custom_plugins_advanced",
        False,
        dialog,
        "Bypasses plugin missing checks for game transfers and native downloads. Disables standard download.lua enforcement.",
        show_description=False,
    )
    deploy_layout.addWidget(dialog.custom_plugins_checkbox)

    def _on_custom_plugins_toggled(checked: bool):
        dialog.settings.setValue("custom_plugins_advanced", checked)
        if checked:
            ThemedMessageBox.warning(
                dialog,
                "Custom Plugins (Advanced)",
                "Bypassing plugin presence verification enables native downloads and game transfers with custom or third-party Lua plugins.\n\n"
                "Please note: ASSella cannot guarantee compatibility, functionality, or stability with third-party plugins!"
            )
        _refresh_deploy_ui()

    dialog.custom_plugins_checkbox.checkbox.toggled.connect(_on_custom_plugins_toggled)

    layout.addWidget(deploy_card)

    STYLE_ACTIVE_GREEN = """
        QPushButton {
            background-color: rgba(76, 175, 80, 0.2);
            color: #81C784;
            border: 1px solid rgba(129, 199, 132, 0.5);
            border-radius: 6px;
            padding: 7px 12px;
            font-size: 8.5pt;
            font-weight: 600;
        }
    """
    STYLE_INACTIVE_BUTTON = """
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.22);
            border-radius: 6px;
            color: #FFFFFF;
            padding: 7px 12px;
            font-size: 8.5pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.16);
            border-color: rgba(255, 255, 255, 0.35);
        }
    """
    STYLE_ATTENTION_ORANGE = """
        QPushButton {
            background-color: rgba(255, 171, 0, 0.18);
            color: #FFD54F;
            border: 1px solid rgba(255, 171, 0, 0.55);
            border-radius: 6px;
            padding: 7px 12px;
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
            padding: 7px 12px;
            font-size: 8.5pt;
            font-weight: 500;
        }
    """

    def _refresh_deploy_ui():
        is_custom = dialog.settings.value("custom_plugins_advanced", False, type=bool)
        std_state = get_plugin_state("download.lua")
        space_state = get_plugin_state("spacebunny1.5.lua")
        spliced_state = get_plugin_state("spliced-tickets.lua")
        active_dl = get_active_download_plugin()
        active_lower = (active_dl or "").lower()

        is_space_active = ("spacebunny" in active_lower) or ("spacetest" in active_lower)
        is_std_active = (active_dl == "download.lua")

        if is_custom:
            dialog.btn_standard_plugin.setEnabled(False)
            dialog.btn_standard_plugin.setText("Standard")
            dialog.btn_standard_plugin.setToolTip("Custom plugins option is enabled. Verification is bypassed.")
            dialog.btn_standard_plugin.setStyleSheet(STYLE_DISABLED)

            dialog.btn_spacebunny_plugin.setEnabled(False)
            dialog.btn_spacebunny_plugin.setText("SpaceBunny 1.5")
            dialog.btn_spacebunny_plugin.setToolTip("Custom plugins option is enabled. Verification is bypassed.")
            dialog.btn_spacebunny_plugin.setStyleSheet(STYLE_DISABLED)
        elif is_std_active:
            # Standard is active
            if std_state == "up_to_date":
                dialog.btn_standard_plugin.setEnabled(False)
                dialog.btn_standard_plugin.setText("Standard (Active)")
                dialog.btn_standard_plugin.setToolTip("download.lua is currently active and verified against Cloudflare manifest.")
                dialog.btn_standard_plugin.setStyleSheet(STYLE_ACTIVE_GREEN)
            else:
                dialog.btn_standard_plugin.setEnabled(True)
                dialog.btn_standard_plugin.setText("Standard (Update)")
                dialog.btn_standard_plugin.setToolTip("download.lua is active but outdated. Click to update from Cloudflare.")
                dialog.btn_standard_plugin.setStyleSheet(STYLE_ATTENTION_ORANGE)

            # SpaceBunny is inactive (clickable to switch)
            dialog.btn_spacebunny_plugin.setEnabled(True)
            dialog.btn_spacebunny_plugin.setText("SpaceBunny 1.5")
            dialog.btn_spacebunny_plugin.setToolTip("Click to activate SpaceBunny 1.5 (archives download.lua to .bak).")
            dialog.btn_spacebunny_plugin.setStyleSheet(STYLE_INACTIVE_BUTTON)

        elif is_space_active:
            # SpaceBunny is active
            if space_state == "up_to_date":
                dialog.btn_spacebunny_plugin.setEnabled(False)
                dialog.btn_spacebunny_plugin.setText("SpaceBunny 1.5 (Active)")
                dialog.btn_spacebunny_plugin.setToolTip("SpaceBunny 1.5 is currently active and verified against Cloudflare manifest.")
                dialog.btn_spacebunny_plugin.setStyleSheet(STYLE_ACTIVE_GREEN)
            else:
                dialog.btn_spacebunny_plugin.setEnabled(True)
                dialog.btn_spacebunny_plugin.setText("SpaceBunny 1.5 (Update)")
                dialog.btn_spacebunny_plugin.setToolTip("SpaceBunny 1.5 is active but outdated. Click to update from Cloudflare.")
                dialog.btn_spacebunny_plugin.setStyleSheet(STYLE_ATTENTION_ORANGE)

            # Standard is inactive (clickable to switch)
            dialog.btn_standard_plugin.setEnabled(True)
            dialog.btn_standard_plugin.setText("Standard")
            dialog.btn_standard_plugin.setToolTip("Click to activate Standard download.lua (archives SpaceBunny to .bak).")
            dialog.btn_standard_plugin.setStyleSheet(STYLE_INACTIVE_BUTTON)

        else:
            # Neither is currently active
            dialog.btn_standard_plugin.setEnabled(True)
            dialog.btn_standard_plugin.setText("Standard (Missing)")
            dialog.btn_standard_plugin.setToolTip("download.lua is not installed. Click to download and activate.")
            dialog.btn_standard_plugin.setStyleSheet(STYLE_ATTENTION_ORANGE)

            dialog.btn_spacebunny_plugin.setEnabled(True)
            dialog.btn_spacebunny_plugin.setText("SpaceBunny 1.5 (Missing)")
            dialog.btn_spacebunny_plugin.setToolTip("SpaceBunny 1.5 is not installed. Click to download and activate.")
            dialog.btn_spacebunny_plugin.setStyleSheet(STYLE_ATTENTION_ORANGE)

        # 3. Spliced Tickets button state & style
        if spliced_state == "up_to_date":
            dialog.btn_spliced_tickets.setEnabled(False)
            dialog.btn_spliced_tickets.setText("Spliced Tickets")
            dialog.btn_spliced_tickets.setToolTip("spliced-tickets.lua is deployed and active.")
            dialog.btn_spliced_tickets.setStyleSheet(STYLE_ACTIVE_GREEN)
        else:
            dialog.btn_spliced_tickets.setEnabled(True)
            status_txt = "Spliced Tickets (Missing)" if spliced_state == "missing" else "Spliced Tickets (Update)"
            dialog.btn_spliced_tickets.setText(status_txt)
            dialog.btn_spliced_tickets.setToolTip("spliced-tickets.lua is not deployed. Click to download and install.")
            dialog.btn_spliced_tickets.setStyleSheet(STYLE_ATTENTION_ORANGE)

    class DeployWorker(QThread):
        finished_signal = pyqtSignal(bool, bool, str, str, str)

        def __init__(self, filename: str, title: str):
            super().__init__()
            self.filename = filename
            self.title = title

        def run(self):
            try:
                from utils.plugin_manager import deploy_plugin
                ok, skipped, msg = deploy_plugin(self.filename, force_download=True)
            except Exception as exc:
                logger.error(f"[at0mTab] Deploy {self.filename} error: {exc}")
                ok, skipped, msg = False, False, str(exc)
            self.finished_signal.emit(ok, skipped, msg, self.title, self.filename)

    def _deploy_single(filename: str, title: str, button: QPushButton):
        button.setEnabled(False)
        orig_text = button.text()
        button.setText("Deploying...")

        worker = DeployWorker(filename, title)
        dialog._active_deploy_worker = worker

        def _on_done(ok: bool, skipped: bool, msg: str, t: str, f: str):
            button.setEnabled(True)
            button.setText(orig_text)
            _refresh_deploy_ui()
            if not ok:
                ThemedMessageBox.warning(dialog, t, f"Failed downloading/deploying {f}:\n{msg}")
            elif skipped:
                ThemedMessageBox.information(
                    dialog,
                    t,
                    f"{t} ({f}) is already active and up to date."
                )
            else:
                ThemedMessageBox.information(
                    dialog,
                    t,
                    f"Successfully activated {t} ({f})!\nAny alternative interceptor plugins were safely archived as .bak."
                )
            dialog._active_deploy_worker = None

        worker.finished_signal.connect(_on_done)
        worker.start()

    def _on_standard_clicked():
        _deploy_single("download.lua", "Standard Plugin", dialog.btn_standard_plugin)

    def _on_spacebunny_clicked():
        _deploy_single("spacebunny1.5.lua", "SpaceBunny 1.5", dialog.btn_spacebunny_plugin)

    def _on_spliced_tickets_clicked():
        reply = ThemedMessageBox.warning(
            dialog,
            "Legality Warning",
            "Bypassing DRM is illegal in many countries, make sure you are aware of this!",
            ThemedMessageBox.StandardButton.Ok | ThemedMessageBox.StandardButton.Cancel,
            ThemedMessageBox.StandardButton.Cancel,
        )
        if reply != ThemedMessageBox.StandardButton.Ok:
            return

        try:
            from utils.yaml_config_manager import ensure_plugins_enabled, ensure_smart_tickets_enabled
            ensure_plugins_enabled()
            ensure_smart_tickets_enabled()
        except Exception as ex:
            logger.warning(f"Could not update config.yaml for spliced tickets: {ex}")

        _deploy_single("spliced-tickets.lua", "Spliced Tickets", dialog.btn_spliced_tickets)

    dialog.btn_standard_plugin.clicked.connect(_on_standard_clicked)
    dialog.btn_spacebunny_plugin.clicked.connect(_on_spacebunny_clicked)
    dialog.btn_spliced_tickets.clicked.connect(_on_spliced_tickets_clicked)

    def _update_subwidget_states(enabled: bool):
        if getattr(dialog, "at0m_disable_updates_checkbox", None):
            dialog.at0m_disable_updates_checkbox.setEnabled(enabled)
        dialog.at0m_download_action_combo.setEnabled(enabled)
        dialog.at0m_start_mode_combo.setEnabled(enabled)

    def _on_enable_at0m_toggled(checked: bool):
        dialog.settings.setValue("enable_at0m", checked)
        dialog.settings.setValue("enable_vapor", checked)
        dialog.settings.setValue("use_native_steam_download", checked)
        try:
            update_yaml_boolean_value(cfg_path, "Plugins", checked)
        except Exception as e:
            logger.debug(f"[at0mTab] Could not update Plugins in SLS config: {e}")
        if checked:
            for p_file in ("download.lua", "spliced-tickets.lua"):
                if get_plugin_state(p_file) == "missing":
                    deploy_sls_plugin(p_file)
        _refresh_deploy_ui()
        _update_subwidget_states(checked)

    dialog.enable_at0m_checkbox.toggled.connect(_on_enable_at0m_toggled)
    _update_subwidget_states(dialog.enable_at0m_checkbox.isChecked())

    # Initial state evaluation
    _refresh_deploy_ui()

    layout.addStretch()
    dialog.tab_widget.addTab(tab, "at0-m")
    return tab


# Backward compatibility alias
create_vapor_tab = create_at0m_tab
