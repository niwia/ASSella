import logging
import os
import shutil
import subprocess
import sys
import threading
import webbrowser

from PyQt6.QtCore import Qt, QUrl, QMetaObject
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QFileDialog,
    QMessageBox,
)

from utils.helpers import get_venv_python
from utils.paths import Paths
from utils.settings import get_settings

logger = logging.getLogger(__name__)


def create_tools_tab(dialog) -> QWidget:
    """Create the Tools settings tab."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(24)

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

    _log_note = QLabel("Changes take effect immediately when you click OK.")
    _log_note.setStyleSheet("color: #888888; font-size: 11px;")
    _log_note.setWordWrap(True)
    log_layout.addWidget(_log_note)
    layout.addWidget(log_card)

    layout.addStretch()
    dialog.tab_widget.addTab(tab, "Tools")
    return tab


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
    helper_path = Paths.deps("schema-grabber/login_helper.py")
    if not helper_path.exists():
        QMessageBox.critical(dialog, "Error", f"Achievements helper missing at: {helper_path}")
        return

    cmd = []
    py = get_venv_python()
    cmd.append(py if py else ("python" if sys.platform == "win32" else "python3"))
    cmd.append(str(helper_path))
    launch_terminal_command(cmd, str(helper_path.parent))


def launch_terminal_command(cmd: list, cwd: str, needs_env: bool = False) -> None:
    cmd_str = [str(part) for part in cmd]
    cwd = str(cwd)
    if sys.platform == "win32":
        q_cmd = " ".join([f'"{c}"' if " " in str(c) else str(c) for c in cmd_str])
        try:
            subprocess.Popen(f'start cmd /k "cd /d {cwd} && {q_cmd}"', shell=True)
            return
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
                return
            except FileNotFoundError:
                continue

    msg_box = QMessageBox()
    msg_box.setWindowTitle("Terminal Not Found")
    msg_box.setText(
        "Could not automatically launch a terminal.\n"
        "Please open a terminal and run:\n"
    )
    msg_box.setInformativeText(" ".join(cmd_str))
    msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg_box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    msg_box.exec()


def run_steamless_manually(dialog) -> None:
    path, _ = QFileDialog.getOpenFileName(
        dialog, "Select Executable", os.path.expanduser("~"), "*.exe"
    )
    if path and dialog.main_window:
        dialog.main_window.task_manager.run_steamless_manually(path)


def run_steamless_aio_manually(dialog) -> None:
    path, _ = QFileDialog.getOpenFileName(
        dialog, "Select Executable", os.path.expanduser("~"), "*.exe"
    )
    if path and dialog.main_window:
        dialog.main_window.task_manager.run_steamless_aio_manually(path)


def browse_aio_script(dialog) -> None:
    current = getattr(dialog, "steamless_aio_path_edit", None)
    text = current.text() if current else ""
    current_path = text or os.path.expanduser("~/Downloads")
    start_dir = os.path.dirname(current_path) if os.path.isfile(current_path) else current_path
    path, _ = QFileDialog.getOpenFileName(
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
        QMessageBox.critical(None, "Error", f"Missing {filename}")
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
        QMessageBox.information(None, "Success", success_msg)
    except (IOError, OSError, subprocess.SubprocessError) as e:
        QMessageBox.critical(None, "Error", f"Registry error: {e}")


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
        QMessageBox.warning(dialog, "Open Config", "SLSsteam config.yaml does not exist.")
        return

    try:
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(DEFAULT_CONFIG_PATH)))
        if not opened:
            webbrowser.open(DEFAULT_CONFIG_PATH.as_uri())
    except Exception as e:
        QMessageBox.critical(dialog, "Error", f"Failed to open config.yaml:\n{e}")


def restore_sls_backup(dialog) -> None:
    from utils.assfixer import restore_latest_backup, DEFAULT_CONFIG_PATH

    reply = QMessageBox.question(
        dialog, "Restore Backup",
        "Are you sure you want to restore the latest backup? This will overwrite your current config.yaml.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No
    )
    if reply != QMessageBox.StandardButton.Yes:
        return

    success, msg, bak_path = restore_latest_backup(DEFAULT_CONFIG_PATH)
    if success:
        import utils.assfixer
        utils.assfixer.boot_status = "checking"

        def run_check():
            utils.assfixer.run_boot_config_check()
            QMetaObject.invokeMethod(dialog, "_update_asshead_status_ui", Qt.ConnectionType.QueuedConnection)
            if dialog.main_window and hasattr(dialog.main_window, "refresh_system_status"):
                dialog.main_window.refresh_system_status()

        threading.Thread(target=run_check, daemon=True).start()
        QMessageBox.information(dialog, "Restore Backup", msg)
    else:
        QMessageBox.critical(dialog, "Restore Backup Error", msg)


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
        QMessageBox.information(dialog, "ASShead Config Fixer", detail_msg)
    else:
        update_asshead_status_ui(dialog)
        if dialog.main_window and hasattr(dialog.main_window, "refresh_system_status"):
            dialog.main_window.refresh_system_status()
        QMessageBox.critical(dialog, "ASShead Config Fixer Error", f"Failed to fix configuration:\n{msg}")


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
        QMessageBox.information(
            dialog,
            "Denuvo Sync",
            f"Successfully synced Denuvo games to your SLSsteam configuration.\nBlocked games count: {count}"
        )
    else:
        if dialog.main_window and hasattr(dialog.main_window, "refresh_system_status"):
            dialog.main_window.refresh_system_status()
        QMessageBox.critical(
            dialog,
            "Denuvo Sync Error",
            f"Denuvo Sync failed:\n{res.get('error')}"
        )
