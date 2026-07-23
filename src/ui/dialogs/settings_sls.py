import logging
import os
import sys
import subprocess
import urllib.request
import json
import tempfile
import shutil
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QMessageBox,
)

from utils.helpers import create_checkbox_setting
from utils.settings import get_settings

logger = logging.getLogger(__name__)

# SLSsteam default path on SteamOS/Linux
SLS_DIR = os.path.expanduser("~/.local/share/SLSsteam")
SLS_VERSION_FILE = os.path.join(SLS_DIR, "version")
SLS_SO_PATH = os.path.join(SLS_DIR, "SLSsteam.so")
SLS_INJECT_PATH = os.path.join(SLS_DIR, "library-inject.so")

# Global variables for caching boot check results
latest_online_version: Optional[str] = None
latest_download_url: Optional[str] = None
update_checked: bool = False
update_error: Optional[str] = None


def get_sls_paths() -> dict:
    """Scan potential directories for SLSsteam.so and library-inject.so.
    Returns a dict with:
      - 'dir': directory path
      - 'so_path': SLSsteam.so absolute path
      - 'inject_path': library-inject.so absolute path
      - 'version_file': path to version file
      - 'is_system_wide': bool
      - 'detected': bool
    """
    native_dir = os.path.expanduser("~/.local/share/SLSsteam")
    flatpak_dir = os.path.expanduser("~/.var/app/com.valvesoftware.Steam/.local/share/SLSsteam")
    system_dir = "/usr/lib32"

    candidates = [
        {
            "dir": native_dir,
            "so": os.path.join(native_dir, "SLSsteam.so"),
            "inject": os.path.join(native_dir, "library-inject.so"),
            "system": False
        },
        {
            "dir": flatpak_dir,
            "so": os.path.join(flatpak_dir, "SLSsteam.so"),
            "inject": os.path.join(flatpak_dir, "library-inject.so"),
            "system": False
        },
        {
            "dir": system_dir,
            "so": "/usr/lib32/libSLSsteam.so",
            "inject": "/usr/lib32/libSLS-library-inject.so",
            "system": True
        },
    ]

    for cand in candidates:
        if os.path.exists(cand["so"]):
            return {
                "dir": cand["dir"],
                "so_path": cand["so"],
                "inject_path": cand["inject"],
                "version_file": os.path.join(cand["dir"], "version") if not cand["system"] else os.path.join(native_dir, "version"),
                "is_system_wide": cand["system"],
                "detected": True
            }

    # Fallback/Default: Check if Flatpak Steam directory exists
    flatpak_steam_home = os.path.expanduser("~/.var/app/com.valvesoftware.Steam")
    default_dir = flatpak_dir if os.path.exists(flatpak_steam_home) else native_dir
    return {
        "dir": default_dir,
        "so_path": os.path.join(default_dir, "SLSsteam.so"),
        "inject_path": os.path.join(default_dir, "library-inject.so"),
        "version_file": os.path.join(default_dir, "version"),
        "is_system_wide": False,
        "detected": False
    }


def get_local_sls_version() -> str:
    """Helper to detect local SLSsteam installation version."""
    paths = get_sls_paths()
    if not paths["detected"]:
        return "Not Installed"
    
    if os.path.exists(paths["version_file"]):
        try:
            with open(paths["version_file"], "r", encoding="utf-8") as f:
                return f.read().strip() or "Installed (Version Unknown)"
        except Exception:
            return "Installed (Version Unknown)"
    
    return "Installed (Version Unknown)"


def run_boot_update_check() -> None:
    """Query GitHub releases API once during startup in a background thread."""
    global latest_online_version, latest_download_url, update_checked, update_error
    if update_checked:
        return
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/AceSLS/SLSsteam/releases/latest",
            headers={"User-Agent": "ASSella-SLS-Updater"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            latest_online_version = data.get("tag_name")
            
            # Find SLSsteam-Any.7z
            for asset in data.get("assets", []):
                if asset.get("name") == "SLSsteam-Any.7z":
                    latest_download_url = asset.get("browser_download_url")
                    break

        if not latest_download_url:
            raise ValueError("Could not find SLSsteam-Any.7z in the latest release assets.")

        update_checked = True
        update_error = None
        logger.info(f"SLSsteam boot check successful: latest={latest_online_version}")
    except Exception as e:
        update_error = str(e)
        logger.warning(f"Failed to check SLSsteam updates on boot: {e}")


class SlsUpdaterWorker(QThread):
    """Background worker for update checking and downloading SLSsteam."""
    check_completed = pyqtSignal(str, str)  # latest_tag, download_url
    update_completed = pyqtSignal(str)     # latest_tag
    error_occurred = pyqtSignal(str)        # error_msg

    def __init__(self, action: str):
        super().__init__()
        self.action = action  # 'check' or 'update'

    def run(self):
        global latest_online_version, latest_download_url, update_checked, update_error
        try:
            # Query GitHub API
            req = urllib.request.Request(
                "https://api.github.com/repos/AceSLS/SLSsteam/releases/latest",
                headers={"User-Agent": "ASSella-SLS-Updater"}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                latest_tag = data.get("tag_name")
                
                # Find SLSsteam-Any.7z
                download_url = None
                for asset in data.get("assets", []):
                    if asset.get("name") == "SLSsteam-Any.7z":
                        download_url = asset.get("browser_download_url")
                        break

            if not download_url:
                raise ValueError("Could not find SLSsteam-Any.7z in the latest release assets.")

            # Update cache
            latest_online_version = latest_tag
            latest_download_url = download_url
            update_checked = True
            update_error = None

            if self.action == "check":
                self.check_completed.emit(latest_tag, download_url)
                return

            if self.action == "update":
                # Download the 7z archive
                temp_dir = tempfile.mkdtemp()
                archive_path = os.path.join(temp_dir, "SLSsteam-Any.7z")
                
                req_dl = urllib.request.Request(
                    download_url,
                    headers={"User-Agent": "ASSella-SLS-Updater"}
                )
                with urllib.request.urlopen(req_dl, timeout=30) as dl_resp, open(archive_path, "wb") as f_out:
                    f_out.write(dl_resp.read())

                # Extract the 7z archive
                paths = get_sls_paths()
                target_dir = paths["dir"]
                os.makedirs(target_dir, exist_ok=True)
                
                # We know 7z is present on SteamOS.
                # Extract directly to the target directory and overwrite (-y)
                cmd = ["7z", "x", archive_path, f"-o{target_dir}", "-y"]
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                # Clean up temp archive
                shutil.rmtree(temp_dir, ignore_errors=True)

                if result.returncode != 0:
                    raise RuntimeError(f"7z extraction failed: {result.stderr or result.stdout}")

                # Write version tag file
                with open(paths["version_file"], "w", encoding="utf-8") as f_ver:
                    f_ver.write(latest_tag)

                self.update_completed.emit(latest_tag)

        except Exception as e:
            logger.error(f"SLSsteam updater worker failed: {e}", exc_info=True)
            self.error_occurred.emit(str(e))


def is_headcrab_installed() -> bool:
    """Detect whether Headcrab is installed using binaries and desktop shortcut."""
    dgsc_path = os.path.expanduser("~/.headcrab/dgsc")
    dlm_path = os.path.expanduser("~/.headcrab/dlm")
    desktop_path = os.path.expanduser("~/.local/share/applications/headcrab.desktop")
    return (os.path.exists(dgsc_path) and os.path.exists(dlm_path)) or os.path.exists(desktop_path)


def run_headcrab(dialog, callback) -> None:
    """Prompt and run Headcrab installation script inside a terminal."""
    already = is_headcrab_installed()
    verb = "re-run" if already else "install"
    reply = QMessageBox.question(
        dialog,
        "Run Headcrab",
        f"This will {verb} Headcrab via:\n"
        "  curl -fsSL headcrab.pages.dev | bash\n\n"
        "A terminal window will open. Close it when finished.",
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
    )
    if reply != QMessageBox.StandardButton.Ok:
        return
    cmd = ["bash", "-c", "curl -fsSL headcrab.pages.dev | bash; echo; echo '--- Done. Press Enter to close ---'; read _"]
    dialog._launch_terminal_command(cmd, os.path.expanduser("~"))
    callback()


def create_sls_tab(dialog) -> QWidget:
    """Relocate Steam/SLS settings, ASShead fixer, and implement updater UI."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(15, 15, 15, 15)

    # 1. Integration Group
    int_group = QGroupBox("SLS Settings")
    int_layout = QVBoxLayout()

    if sys.platform == "linux":
        wrapper_name = "SLSsteam"
        dialog.sls_mode_checkbox = None
        linux_hint = QLabel(
            "SLSsteam is enabled automatically for Steam library installs on Linux."
        )
        linux_hint.setWordWrap(True)
        int_layout.addWidget(linux_hint)
    else:
        wrapper_name = "GreenLuma"
        wrapper_full = "GreenLuma Wrapper Mode"
        tooltip = (
            "Integrate games with Steam using GreenLuma.\n"
            "Games appear in your Steam library automatically."
        )
        dialog.sls_mode_checkbox = create_checkbox_setting(
            wrapper_full, "slssteam_mode", False, dialog, tooltip
        )
        dialog.sls_mode_checkbox.stateChanged.connect(
            lambda: dialog.goldberg_checked_warning_from_mode(wrapper_name)
        )
        int_layout.addWidget(dialog.sls_mode_checkbox)

    dialog.sls_config_management_checkbox = create_checkbox_setting(
        f"{wrapper_name} Config Management",
        "sls_config_management",
        True,
        dialog,
        f"Allow ACCELA to manage {wrapper_name} configuration files.",
    )
    
    if sys.platform == "linux":
        paths = get_sls_paths()
        if paths["detected"]:
            dialog.sls_config_management_checkbox.setChecked(True)
            dialog.sls_config_management_checkbox.setEnabled(False)
            dialog.sls_config_management_checkbox.setToolTip("Permanently enabled because SLSsteam installation was detected.")
            
    int_layout.addWidget(dialog.sls_config_management_checkbox)

    dialog.prompt_steam_restart_checkbox = create_checkbox_setting(
        "Prompt Steam Restart",
        "prompt_steam_restart",
        True,
        dialog,
        "Show prompt to restart Steam after Steam-integrated downloads.",
    )
    int_layout.addWidget(dialog.prompt_steam_restart_checkbox)

    int_group.setLayout(int_layout)
    layout.addWidget(int_group)

    # 2. SLS Config Fixer (ASShead) Group
    fixer_group = QGroupBox("SLS Config Fixer (ASShead)")
    fixer_layout = QVBoxLayout()

    dialog.asshead_status_label = QLabel()
    dialog.asshead_status_label.setWordWrap(True)
    fixer_layout.addWidget(dialog.asshead_status_label)

    fixer_btn_layout = QHBoxLayout()

    dialog.run_asshead_btn = QPushButton("Run SLS Config Fixer")
    dialog.run_asshead_btn.setToolTip(
        "Scan, format, deduplicate, and merge latest upstream keys into your SLSsteam config.yaml."
    )
    dialog.run_asshead_btn.clicked.connect(dialog.run_asshead_fixer)
    fixer_btn_layout.addWidget(dialog.run_asshead_btn)

    dialog.open_config_btn = QPushButton("Open Config")
    dialog.open_config_btn.setToolTip("Open the config.yaml file in the system default text editor.")
    dialog.open_config_btn.clicked.connect(dialog.open_sls_config)
    fixer_btn_layout.addWidget(dialog.open_config_btn)

    dialog.restore_backup_btn = QPushButton("Restore Backup")
    dialog.restore_backup_btn.setToolTip("Restore the last backup copy of config.yaml.")
    dialog.restore_backup_btn.clicked.connect(dialog.restore_sls_backup)
    fixer_btn_layout.addWidget(dialog.restore_backup_btn)

    fixer_layout.addLayout(fixer_btn_layout)

    fixer_group.setLayout(fixer_layout)
    layout.addWidget(fixer_group)

    # Update status UI via helper reference
    dialog._update_asshead_status_ui()

    # 3. SLSsteam Updater Group (Linux only)
    if sys.platform == "linux":
        updater_group = QGroupBox("SLSsteam Updater")
        updater_layout = QVBoxLayout()

        local_ver = get_local_sls_version()
        local_ver_label = QLabel(f"Local Version: {local_ver}")
        updater_layout.addWidget(local_ver_label)

        online_ver_label = QLabel("Latest Online: Not Checked")
        updater_layout.addWidget(online_ver_label)

        btn_layout = QHBoxLayout()
        check_btn = QPushButton("Check for Updates")
        update_btn = QPushButton("Update / Reinstall SLSsteam")
        update_btn.setEnabled(False)

        # Helper variables to reference inside slots
        download_url_holder = [latest_download_url]

        def on_check_success(latest_tag, download_url):
            online_ver_label.setText(f"Latest Online: {latest_tag}")
            download_url_holder[0] = download_url
            
            local_clean = local_ver_label.text().split("Local Version: ")[1].strip()
            if local_clean in ("Not Installed", "Installed (Version Unknown)") or local_clean != latest_tag:
                online_ver_label.setStyleSheet("color: #ffaa00;")
                update_btn.setEnabled(True)
                check_btn.setText("Update Available")
            else:
                online_ver_label.setStyleSheet("color: #44bb44;")
                update_btn.setEnabled(True)  # Still allow reinstall
                check_btn.setText("Up to Date")
            check_btn.setEnabled(True)

            # Refresh Main Window status label when manual check finishes
            if dialog.main_window and hasattr(dialog.main_window, "refresh_system_status"):
                dialog.main_window.refresh_system_status()

        def on_update_success(latest_tag):
            local_ver_label.setText(f"Local Version: {latest_tag}")
            online_ver_label.setText(f"Latest Online: {latest_tag}")
            online_ver_label.setStyleSheet("color: #44bb44;")
            check_btn.setText("Up to Date")
            check_btn.setEnabled(True)
            update_btn.setEnabled(True)
            update_btn.setText("Update / Reinstall SLSsteam")
            
            # Refresh main window display if active
            if dialog.main_window and hasattr(dialog.main_window, "refresh_system_status"):
                dialog.main_window.refresh_system_status()

            QMessageBox.information(
                dialog,
                "SLSsteam Updated",
                f"Successfully installed and configured SLSsteam version {latest_tag}!"
            )

        def on_worker_error(error_msg):
            check_btn.setEnabled(True)
            check_btn.setText("Check for Updates")
            update_btn.setEnabled(True)
            update_btn.setText("Update / Reinstall SLSsteam")
            QMessageBox.critical(
                dialog,
                "SLSsteam Update Error",
                f"Action failed:\n{error_msg}"
            )

        def trigger_check():
            check_btn.setEnabled(False)
            check_btn.setText("Checking...")
            dialog.updater_worker = SlsUpdaterWorker("check")
            dialog.updater_worker.check_completed.connect(on_check_success)
            dialog.updater_worker.error_occurred.connect(on_worker_error)
            dialog.updater_worker.start()

        def trigger_update():
            # If we don't have internet or no version checked online, let updater do check & update
            check_btn.setEnabled(False)
            update_btn.setEnabled(False)
            update_btn.setText("Updating...")
            dialog.updater_worker = SlsUpdaterWorker("update")
            dialog.updater_worker.update_completed.connect(on_update_success)
            dialog.updater_worker.error_occurred.connect(on_worker_error)
            dialog.updater_worker.start()

        check_btn.clicked.connect(trigger_check)
        update_btn.clicked.connect(trigger_update)

        btn_layout.addWidget(check_btn)
        btn_layout.addWidget(update_btn)
        
        # ── Headcrab Button Placement ────────────────────────────────────
        headcrab_btn = QPushButton()
        def update_headcrab_btn_text():
            if is_headcrab_installed():
                headcrab_btn.setText("Rerun Headcrab Setup")
                headcrab_btn.setToolTip("Re-run the Headcrab setup script (curl -fsSL headcrab.pages.dev | bash)")
            else:
                headcrab_btn.setText("Install Headcrab")
                headcrab_btn.setToolTip("Run the Headcrab setup script (curl -fsSL headcrab.pages.dev | bash)")

        update_headcrab_btn_text()
        headcrab_btn.clicked.connect(lambda: run_headcrab(dialog, update_headcrab_btn_text))
        btn_layout.addWidget(headcrab_btn)

        updater_layout.addLayout(btn_layout)

        # Smart version prompt: if the local installation is missing a version tracking file
        # but exists on the system, suggest installing to create the register file.
        if local_ver == "Installed (Version Unknown)":
            hint_label = QLabel(
                "A local SLSsteam installation was detected, but its version is unknown. "
                "You can click 'Update / Reinstall SLSsteam' to install the latest build and register it."
            )
            hint_label.setWordWrap(True)
            hint_label.setStyleSheet("color: #ffaa00; font-size: 11px;")
            updater_layout.addWidget(hint_label)
            update_btn.setEnabled(True)
        elif local_ver == "Not Installed":
            hint_label = QLabel(
                "SLSsteam is not detected in your user directories. "
                "Click 'Update / Reinstall' to download and configure SLSsteam automatically."
            )
            hint_label.setWordWrap(True)
            hint_label.setStyleSheet("color: #cc4444; font-size: 11px;")
            updater_layout.addWidget(hint_label)
            update_btn.setEnabled(True)

        # Use pre-checked boot version if available
        if update_checked and latest_online_version:
            online_ver_label.setText(f"Latest Online: {latest_online_version}")
            local_clean = local_ver.strip()
            if local_clean in ("Not Installed", "Installed (Version Unknown)") or local_clean != latest_online_version:
                online_ver_label.setStyleSheet("color: #ffaa00;")
                update_btn.setEnabled(True)
                check_btn.setText("Update Available")
            else:
                online_ver_label.setStyleSheet("color: #44bb44;")
                update_btn.setEnabled(True)
                check_btn.setText("Up to Date")
        elif update_error:
            online_ver_label.setText(f"Latest Online: Error ({update_error[:30]}...)")
            online_ver_label.setStyleSheet("color: #cc4444;")

        updater_group.setLayout(updater_layout)
        layout.addWidget(updater_group)

    layout.addStretch()
    dialog.tab_widget.addTab(tab, "SLS")
    return tab
