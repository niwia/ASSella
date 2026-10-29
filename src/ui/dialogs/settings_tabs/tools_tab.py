import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import webbrowser

from PyQt6.QtCore import Qt, QUrl, QMetaObject
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
)
from ui.dialogs.themed_file_dialog import ThemedFileDialog
from ui.dialogs.themed_message_box import ThemedMessageBox

from ui.dialogs.settings_tabs.components_card import (
    create_components_card,
    require_component,
)

from utils.helpers import get_venv_python
from utils.paths import Paths
from utils.settings import get_settings

logger = logging.getLogger(__name__)


def create_tools_tab(dialog) -> QWidget:
    """Create the Tools settings tab."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)

    # Tools Card
    tools_card, tools_layout = dialog._create_card_frame("Tools")

    tools_btn_row = QHBoxLayout()
    tools_btn_row.setContentsMargins(0, 4, 0, 4)
    tools_btn_row.setSpacing(10)

    tool_btn_style = """
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 8px;
            color: #FFFFFF;
            padding: 7px 14px;
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

    dialog.configure_achievements_btn = QPushButton("Achievements")
    dialog.configure_achievements_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.configure_achievements_btn.setStyleSheet(tool_btn_style)
    dialog.configure_achievements_btn.setToolTip("Perform one-time setup and authenticate Steam for achievements.")
    dialog.configure_achievements_btn.clicked.connect(lambda: run_schema_grabber_manually(dialog))
    tools_btn_row.addWidget(dialog.configure_achievements_btn)

    dialog.steamless_py_btn = QPushButton("Steamless (Python)")
    dialog.steamless_py_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.steamless_py_btn.setStyleSheet(tool_btn_style)
    dialog.steamless_py_btn.setToolTip("Run Steamless AIO (Python) manually on a game .exe.")
    dialog.steamless_py_btn.clicked.connect(lambda: run_steamless_aio_manually(dialog))
    tools_btn_row.addWidget(dialog.steamless_py_btn)

    dialog.steamless_legacy_btn = QPushButton("Steamless (.NET CLI)")
    dialog.steamless_legacy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.steamless_legacy_btn.setStyleSheet(tool_btn_style)
    dialog.steamless_legacy_btn.setToolTip("Run Steamless (.NET 9 CLI) manually on a game .exe.")
    dialog.steamless_legacy_btn.clicked.connect(lambda: run_steamless_manually(dialog))
    tools_btn_row.addWidget(dialog.steamless_legacy_btn)

    tools_btn_row.addStretch()
    tools_layout.addLayout(tools_btn_row)
    layout.addWidget(tools_card)

    # Windows Registry Card
    if sys.platform == "win32":
        reg_card, reg_layout = dialog._create_card_frame("Windows Registry")

        add_tool_button(
            reg_layout,
            "Register Registry Entries",
            "Register accela:// URL protocol and .zip context menu entries.",
            register_registry_entries,
        )

        add_tool_button(
            reg_layout,
            "Remove Registry Entries",
            "Remove accela:// URL protocol and .zip context menu entries.",
            remove_registry_entries,
        )

        layout.addWidget(reg_card)

    # Logging Configuration Card
    log_card, log_layout = dialog._create_card_frame("Logging Configuration")

    log_row = QHBoxLayout()
    log_row.setContentsMargins(0, 4, 0, 4)
    log_row.setSpacing(24)

    level_box = QHBoxLayout()
    level_box.setSpacing(8)
    level_label = QLabel("Log Level:")
    level_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500;")
    level_label.setToolTip(
        "Minimum severity of messages to log.\n"
        "Select NONE to disable all logging (improves performance)."
    )
    dialog.log_level_combo = QComboBox()
    dialog.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "NONE"])
    _current_level = dialog.settings.value("log_filter_level", "DEBUG") or "DEBUG"
    idx = dialog.log_level_combo.findText(_current_level)
    dialog.log_level_combo.setCurrentIndex(idx if idx >= 0 else 0)
    level_box.addWidget(level_label)
    level_box.addWidget(dialog.log_level_combo)
    log_row.addLayout(level_box)

    cat_box = QHBoxLayout()
    cat_box.setSpacing(8)
    cat_label = QLabel("Log Filter:")
    cat_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500;")
    cat_label.setToolTip("Restrict logs to a specific module group.")
    dialog.log_category_combo = QComboBox()
    dialog.log_category_combo.addItems([
        "All Modules",
        "Only Steam Client & API",
        "Only Downloads & Manifests",
        "Only Database & Library",
    ])
    _current_cat = dialog.settings.value("log_filter_category", "All Modules") or "All Modules"
    cat_idx = dialog.log_category_combo.findText(_current_cat)
    dialog.log_category_combo.setCurrentIndex(cat_idx if cat_idx >= 0 else 0)
    cat_box.addWidget(cat_label)
    cat_box.addWidget(dialog.log_category_combo)
    log_row.addLayout(cat_box)

    log_row.addStretch()
    log_layout.addLayout(log_row)

    btn_row = QHBoxLayout()
    btn_row.setContentsMargins(0, 4, 0, 0)
    btn_row.setSpacing(10)

    btn_style = f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 6px;
            color: #FFFFFF;
            padding: 6px 14px;
            font-size: 9pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {dialog.accent_color};
        }}
    """

    open_log_btn = QPushButton("Open Log Folder")
    open_log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    open_log_btn.setStyleSheet(btn_style)
    from utils.logger import open_log_directory
    open_log_btn.clicked.connect(lambda: open_log_directory())
    open_log_btn.setToolTip("Open the ASSella log directory in your file manager.")
    btn_row.addWidget(open_log_btn)

    export_logs_btn = QPushButton("Export Diagnostic Logs")
    export_logs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    export_logs_btn.setStyleSheet(btn_style)
    export_logs_btn.clicked.connect(lambda: export_diagnostic_logs(dialog))
    export_logs_btn.setToolTip("Bundle recent ASSella logs, SLSsteam logs, config.yaml, and plugin details into a ZIP archive.")
    btn_row.addWidget(export_logs_btn)

    btn_row.addStretch()
    log_layout.addLayout(btn_row)
    layout.addWidget(log_card)

    # Optional components (Goldberg / Steamless / SLScheevo) are no longer
    # bundled - this is the one place they can be downloaded or updated.
    layout.addWidget(create_components_card(dialog))

    layout.addStretch()
    dialog.tab_widget.addTab(tab, "Tools")
    return tab


def export_diagnostic_logs(dialog) -> None:
    """Bundle latest 2 ASSella logs, SLSsteam log, config.yaml, and plugins info into a ZIP archive."""
    import zipfile
    from datetime import datetime
    from utils.logger import get_base_path
    from utils.slssteam_integration import _get_sls_log_path
    from utils.yaml_config_manager import get_user_config_path
    from managers.katamari_config_manager import get_sls_config_dir, get_katamari_conf_path

    # Flush current log handlers so in-memory logs are committed to disk
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            pass

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"assella_diagnostics_{timestamp_str}.zip"
    default_dest = str(Path.home() / default_name)

    save_path, _ = ThemedFileDialog.getSaveFileName(
        dialog,
        "Export Diagnostic Logs",
        default_dest,
        "Zip Archives (*.zip)",
    )
    if not save_path:
        return

    collected = []
    try:
        with zipfile.ZipFile(save_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # 1. Latest 2 ASSella logs
            log_dir = get_base_path() / "logs"
            if log_dir.is_dir():
                log_files = sorted(
                    log_dir.glob("assella_*.log"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                for log_file in log_files[:2]:
                    zf.write(log_file, arcname=f"logs/{log_file.name}")
                    collected.append(f"ASSella Log: {log_file.name}")

            # 2. SLSsteam log from user home
            try:
                sls_log = _get_sls_log_path()
                if sls_log.exists():
                    zf.write(sls_log, arcname=f"slssteam/{sls_log.name}")
                    collected.append(f"SLSsteam Log: {sls_log.name}")
            except Exception as e:
                logger.debug(f"[ExportDiag] Error collecting SLS log: {e}")

            # 3. SLSsteam config.yaml
            try:
                cfg_path = get_user_config_path()
                if cfg_path.exists():
                    zf.write(cfg_path, arcname="slssteam/config.yaml")
                    collected.append("SLSsteam Config: config.yaml")
            except Exception as e:
                logger.debug(f"[ExportDiag] Error collecting config.yaml: {e}")

            # 4. Plugins directory listing & katamari.conf.lua
            try:
                sls_cfg_dir = get_sls_config_dir()
                plugins_dir = sls_cfg_dir / "plugins"
                plugin_names = []
                if plugins_dir.is_dir():
                    plugin_names = sorted([p.name for p in plugins_dir.iterdir() if not p.name.startswith(".")])
                plugins_listing = "SLSsteam Installed Plugins:\n" + ("\n".join(plugin_names) if plugin_names else "(none)") + "\n"
                zf.writestr("slssteam/plugins_list.txt", plugins_listing)
                collected.append(f"Plugins List ({len(plugin_names)} items)")

                kat_conf = get_katamari_conf_path()
                if kat_conf.exists():
                    zf.write(kat_conf, arcname="slssteam/katamari.conf.lua")
                    collected.append("Katamari Config: katamari.conf.lua")
            except Exception as e:
                logger.debug(f"[ExportDiag] Error collecting plugins list: {e}")

        summary = "\n• " + "\n• ".join(collected) if collected else "No files collected."
        ThemedMessageBox.information(
            dialog,
            "Diagnostics Exported",
            f"Diagnostic log archive successfully saved to:\n{save_path}\n\nContents:{summary}",
        )
    except Exception as e:
        logger.error(f"Failed to export diagnostic archive: {e}", exc_info=True)
        ThemedMessageBox.critical(
            dialog,
            "Export Failed",
            f"Failed to create diagnostic archive:\n{e}",
        )


def add_tool_button(layout: QVBoxLayout, text: str, tooltip: str, slot) -> QPushButton:
    """Helper to add a left-aligned tool button with an always-visible description label stacked below."""
    row_box = QVBoxLayout()
    row_box.setContentsMargins(0, 2, 0, 6)
    row_box.setSpacing(4)

    btn_row = QHBoxLayout()
    btn_row.setContentsMargins(0, 0, 0, 0)

    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet("""
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
            background-color: rgba(255, 255, 255, 0.06) !important;
            border: 1px solid rgba(255, 255, 255, 0.12) !important;
            color: rgba(255, 255, 255, 0.38) !important;
        }
    """)
    btn.clicked.connect(slot)
    btn_row.addWidget(btn)
    btn_row.addStretch()
    row_box.addLayout(btn_row)

    if tooltip:
        desc_lbl = QLabel(tooltip)
        desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;")
        desc_lbl.setWordWrap(True)
        row_box.addWidget(desc_lbl)

    layout.addLayout(row_box)
    return btn


def run_schema_grabber_manually(dialog) -> None:
    from ui.dialogs.settings_tabs.components_card import require_component
    if not require_component(dialog, "slscheevo"):
        return

    helper_path = Paths.deps("SLScheevo/login_helper.py")
    if not helper_path.exists():
        helper_path = Paths.deps("schema-grabber/login_helper.py")
    if not helper_path.exists():
        ThemedMessageBox.critical(dialog, "Error", f"Achievements helper missing at: {helper_path}")
        return

    cmd = []
    py = get_venv_python()
    cmd.append(py if py else ("python" if sys.platform == "win32" else "python3"))
    cmd.append(str(helper_path))
    os.environ["ASSELLA_SRC"] = str(Paths.BASE_DIR)
    launch_terminal_command(cmd, str(helper_path.parent))


def launch_terminal_command(cmd: list, cwd: str, needs_env: bool = False) -> bool:
    cmd_str = [str(part) for part in cmd]
    cwd = str(cwd)
    if sys.platform == "win32":
        q_cmd = " ".join([f'"{c}"' if " " in str(c) else str(c) for c in cmd_str])
        try:
            subprocess.Popen(f'start cmd /k "cd /d {cwd} && {q_cmd}"', shell=True)
            return True
        except OSError:
            pass
    else:
        terms = [
            ["wezterm", "start", "--always-new-process", "--"] + cmd_str,
            ["konsole", "-e"] + cmd_str,
            ["gnome-terminal", "--"] + cmd_str,
            ["ptyxis", "--"] + cmd_str,
            ["alacritty", "-e"] + cmd_str,
            ["tilix", "-e"] + cmd_str,
            ["xfce4-terminal", "-e"] + cmd_str,
            ["terminator", "-x"] + cmd_str,
            ["mate-terminal", "-e"] + cmd_str,
            ["lxterminal", "-e"] + cmd_str,
            ["xterm", "-e"] + cmd_str,
            ["kitty", "-e"] + cmd_str,
        ]
        for t in terms:
            try:
                t_cmd = [str(part) for part in t]
                subprocess.Popen(t_cmd, cwd=cwd)
                return True
            except FileNotFoundError:
                continue

    ThemedMessageBox.information(
        None,
        "Terminal Not Found",
        "Could not automatically launch a terminal.\n"
        "Please open a terminal and run:",
        informative=" ".join(cmd_str),
    )
    return False


def run_steamless_manually(dialog) -> None:
    if not require_component(dialog, "steamless"):
        return
    path, _ = ThemedFileDialog.getOpenFileName(
        dialog, "Select Executable", os.path.expanduser("~"), "*.exe"
    )
    if path and dialog.main_window:
        dialog.main_window.task_manager.run_steamless_manually(path)


def run_steamless_aio_manually(dialog) -> None:
    if not require_component(dialog, "steamless"):
        return
    path, _ = ThemedFileDialog.getOpenFileName(
        dialog, "Select Executable", os.path.expanduser("~"), "*.exe"
    )
    if path and dialog.main_window:
        dialog.main_window.task_manager.run_steamless_aio_manually(path)


def browse_aio_script(dialog) -> None:
    current = getattr(dialog, "steamless_aio_path_edit", None)
    text = current.text() if current else ""
    current_path = text or os.path.expanduser("~/Downloads")
    start_dir = os.path.dirname(current_path) if os.path.isfile(current_path) else current_path
    path, _ = ThemedFileDialog.getOpenFileName(
        dialog,
        "Select Steamless AIO Script",
        start_dir,
        "Shell Scripts (*.sh);;All Files (*)",
    )
    if path and current:
        current.setText(path)
        get_settings().setValue("steamless_aio_path", path)


def register_registry_entries() -> None:
    manage_registry("ACCELA.reg", "Registered successfully")


def remove_registry_entries() -> None:
    manage_registry("ACCELA_uninstall.reg", "Removed successfully")


def manage_registry(filename: str, success_msg: str) -> None:
    if sys.platform != "win32":
        return

    base = (
        os.path.join(getattr(sys, "_MEIPASS"), "deps")
        if getattr(sys, "frozen", False)
        else os.path.join(os.path.dirname(__file__), "..", "..", "..", "deps")
    )
    reg_path = os.path.join(base, filename)

    if not os.path.exists(reg_path):
        ThemedMessageBox.critical(None, "Error", f"Missing {filename}")
        return

    try:
        with open(reg_path, "r", encoding="utf-8-sig") as f:
            content = f.read().replace("[INSTALL_PATH]", sys.executable.replace("\\", "\\\\"))

        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".reg", delete=False) as tmp:
            tmp.write(content)
            tmp_name = tmp.name

        subprocess.run(["regedit", "/s", str(tmp_name)], check=True, shell=True)
        os.unlink(tmp_name)
        ThemedMessageBox.information(None, "Success", success_msg)
    except (IOError, OSError, subprocess.SubprocessError) as e:
        ThemedMessageBox.critical(None, "Error", f"Registry error: {e}")


def update_achievements_button_state(dialog) -> None:
    if hasattr(dialog, "configure_achievements_btn") and dialog.configure_achievements_btn:
        is_enabled = dialog.achievements_checkbox.isChecked() if dialog.achievements_checkbox else False
        dialog.configure_achievements_btn.setEnabled(is_enabled)


def update_asshead_status_ui(dialog) -> None:
    if not hasattr(dialog, "asshead_status_label") or not dialog.asshead_status_label:
        return


def open_sls_config(dialog) -> None:
    from utils.assfixer import DEFAULT_CONFIG_PATH
    if not DEFAULT_CONFIG_PATH.exists():
        ThemedMessageBox.warning(dialog, "Open Config", "SLSsteam config.yaml does not exist.")
        return

    try:
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(DEFAULT_CONFIG_PATH)))
        if not opened:
            webbrowser.open(DEFAULT_CONFIG_PATH.as_uri())
    except Exception as e:
        ThemedMessageBox.critical(dialog, "Error", f"Failed to open config.yaml:\n{e}")


def restore_sls_backup(dialog) -> None:
    from utils.assfixer import restore_latest_backup, DEFAULT_CONFIG_PATH

    reply = ThemedMessageBox.question(
        dialog, "Restore Backup",
        "Are you sure you want to restore the latest backup? This will overwrite your current config.yaml.",
        ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
        ThemedMessageBox.StandardButton.No
    )
    if reply != ThemedMessageBox.StandardButton.Yes:
        return

    success, msg, bak_path = restore_latest_backup(DEFAULT_CONFIG_PATH)
    if success:
        import utils.assfixer
        utils.assfixer.boot_status = "checking"

        def run_check():
            utils.assfixer.run_boot_config_check()
            QMetaObject.invokeMethod(dialog, "_update_asshead_status_ui", Qt.ConnectionType.QueuedConnection)
            if dialog.main_window and hasattr(dialog.main_window, "refresh_system_status"):
                QMetaObject.invokeMethod(dialog.main_window, "refresh_system_status", Qt.ConnectionType.QueuedConnection)

        threading.Thread(target=run_check, daemon=True).start()
        ThemedMessageBox.information(dialog, "Restore Backup", msg)
    else:
        ThemedMessageBox.critical(dialog, "Restore Backup Error", msg)


def run_asshead_fixer(dialog) -> None:
    from utils.assfixer import run_asshead_migration, DEFAULT_CONFIG_PATH

    dialog.run_asshead_btn.setEnabled(False)
    dialog.asshead_status_label.setText("Status: Running fixer...")
    dialog.asshead_status_label.setStyleSheet("color: #888888;")

    success, msg, bak_path = run_asshead_migration(DEFAULT_CONFIG_PATH)
    dialog.run_asshead_btn.setEnabled(True)

    if success:
        import utils.assfixer
        utils.assfixer.boot_status = "optimal"
        utils.assfixer.boot_issues = []

        update_asshead_status_ui(dialog)
        if dialog.main_window and hasattr(dialog.main_window, "refresh_system_status"):
            dialog.main_window.refresh_system_status()

        detail_msg = msg
        if bak_path:
            detail_msg += f"\n\nA backup of your previous config has been saved to:\n{bak_path}"
        ThemedMessageBox.information(dialog, "ASShead Config Fixer", detail_msg)
    else:
        update_asshead_status_ui(dialog)
        if dialog.main_window and hasattr(dialog.main_window, "refresh_system_status"):
            dialog.main_window.refresh_system_status()
        ThemedMessageBox.critical(dialog, "ASShead Config Fixer Error", f"Failed to fix configuration:\n{msg}")


def run_denuvo_sync(dialog) -> None:
    if not hasattr(dialog, "run_denuvo_sync_btn") or not dialog.run_denuvo_sync_btn:
        return
    dialog.run_denuvo_sync_btn.setEnabled(False)
    dialog.asshead_status_label.setText("Status: Syncing Denuvo games...")
    dialog.asshead_status_label.setStyleSheet("color: #ffaa00;")

    from core.ratings import sync_denuvo_cache_and_config

    def do_sync():
        res = sync_denuvo_cache_and_config(main_window=dialog.main_window, force=True)
        dialog._last_denuvo_sync_result = res
        QMetaObject.invokeMethod(dialog, "_on_denuvo_sync_finished", Qt.ConnectionType.QueuedConnection)

    threading.Thread(target=do_sync, daemon=True).start()


def on_denuvo_sync_finished(dialog) -> None:
    res = getattr(dialog, "_last_denuvo_sync_result", {"success": False, "error": "Unknown error"})
    if hasattr(dialog, "run_denuvo_sync_btn") and dialog.run_denuvo_sync_btn:
        dialog.run_denuvo_sync_btn.setEnabled(True)
    update_asshead_status_ui(dialog)

    if res.get("success"):
        count = res.get("count", 0)
        if dialog.main_window and hasattr(dialog.main_window, "refresh_system_status"):
            dialog.main_window.refresh_system_status()
        ThemedMessageBox.information(
            dialog,
            "Denuvo Sync",
            f"Successfully synced Denuvo games to your SLSsteam configuration.\nBlocked games count: {count}"
        )
    else:
        if dialog.main_window and hasattr(dialog.main_window, "refresh_system_status"):
            dialog.main_window.refresh_system_status()
        ThemedMessageBox.critical(
            dialog,
            "Denuvo Sync Error",
            f"Denuvo Sync failed:\n{res.get('error')}"
        )
