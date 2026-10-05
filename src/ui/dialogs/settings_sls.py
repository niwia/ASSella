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
    QLineEdit,
    QComboBox,
    QPushButton,
    QFileDialog,
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
                val = f.read().strip()
                if val:
                    return val
        except Exception:
            pass

    try:
        from utils.slssteam_integration import _binary_version_cache
        cached = _binary_version_cache.get("data", {})
        if cached.get("release_tag"):
            tag = cached["release_tag"]
            return f"{tag} (Headcrab)" if is_headcrab_installed() else tag
    except Exception:
        pass

    if is_headcrab_installed():
        return "Headcrab Managed"

    return "Installed"


def run_boot_update_check() -> None:
    """Query GitHub releases API once during startup in a background thread."""
    logger.info("SLSsteam updater is disabled, skipping boot update check.")
    return


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


def run_headcrab_and_quit(dialog) -> bool:
    """Run Headcrab curl installer in terminal and gracefully quit ASSella."""
    cmd = ["bash", "-c", "curl -fsSL headcrab.pages.dev | bash; echo; echo '--- Done. Press Enter to close ---'; read _"]
    launched = dialog._launch_terminal_command(cmd, os.path.expanduser("~"))
    if launched:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        QTimer.singleShot(600, QApplication.instance().quit)
        return True
    return False


def run_headcrab(dialog, callback=None) -> None:
    """Prompt and run Headcrab installation script inside a terminal, quitting ASSella upon launch."""
    already = is_headcrab_installed()
    verb = "re-run" if already else "install"
    reply = QMessageBox.question(
        dialog,
        "Run Headcrab",
        f"This will {verb} Headcrab via:\n"
        "  curl -fsSL headcrab.pages.dev | bash\n\n"
        "A terminal window will open and ASSella will close so Headcrab can safely update SLSsteam without file conflicts.\n\n"
        "Proceed?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return
    if not run_headcrab_and_quit(dialog) and callback:
        callback()


def create_sls_tab(dialog) -> QWidget:
    """Relocate Steam/SLS settings, ASShead fixer, and implement updater UI."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(24)

    # 1. Integration Card
    int_card, int_layout = dialog._create_card_frame("SLS Settings")

    if sys.platform == "linux":
        wrapper_name = "SLSsteam"
        dialog.sls_mode_checkbox = None
        linux_hint = QLabel(
            "SLSsteam is enabled automatically for Steam library installs on Linux."
        )
        linux_hint.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt;")
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

    dialog.experimental_acf_independent_checkbox = create_checkbox_setting(
        "SLSsteam API",
        "experimental_acf_independent",
        True,
        dialog,
        "Use SLSsteam native API for installation/uninstallation with native ACF generation.",
    )
    dialog.experimental_acf_independent_checkbox.stateChanged.connect(dialog._on_experimental_acf_toggled)
    int_layout.addWidget(dialog.experimental_acf_independent_checkbox)

    # All games online (beta)
    from utils.yaml_config_manager import get_user_config_path, get_fake_appid, add_fake_app_id, remove_fake_app_id
    cfg_path = get_user_config_path()
    current_all_online = (
        get_fake_appid(cfg_path, "0") == "480"
        if cfg_path.exists()
        else dialog.settings.value("all_games_online_beta", False, type=bool)
    )

    dialog.all_games_online_checkbox = create_checkbox_setting(
        "All games online (beta)",
        "all_games_online_beta",
        current_all_online,
        dialog,
        "Uses 0: 480 in SLSsteam FakeAppIds to map all unowned games to Spacewar (480) for online networking features.",
    )

    def _on_all_games_online_toggled(state):
        is_checked = bool(state)
        dialog.settings.setValue("all_games_online_beta", is_checked)
        c_path = get_user_config_path()
        if is_checked:
            add_fake_app_id(c_path, "0", game_name="All Unowned Apps", fake_appid="480")
        else:
            remove_fake_app_id(c_path, "0", fake_appid="480")
            remove_fake_app_id(c_path, "0")

    dialog.all_games_online_checkbox.stateChanged.connect(_on_all_games_online_toggled)
    int_layout.addWidget(dialog.all_games_online_checkbox)

    # Cleaned up: only single SLSsteam API toggle is shown.
    # Steam restart is disabled by default / forced off.
    # SLS config management is ON by default and toggles in lockstep with SLSsteam API.
    dialog.sls_config_management_checkbox = None
    dialog.prompt_steam_restart_checkbox = None
    dialog.settings.setValue("prompt_steam_restart", False)
    if dialog.settings.value("sls_config_management") is None:
        dialog.settings.setValue("sls_config_management", True)


    dialog.ignore_slssteam_updater_checkbox = create_checkbox_setting(
        "Ignore SLSsteam Updater (managed by Headcrab)",
        "ignore_slssteam_updater",
        False,
        dialog,
        "Disable boot check and update functionality for SLSsteam.",
    )
    dialog.ignore_slssteam_updater_checkbox.hide()

    layout.addWidget(int_card)

    # 2. SLSsteam Updater Card (Linux only)
    if sys.platform == "linux":
        updater_card, updater_layout = dialog._create_card_frame("SLSsteam Updater")

        local_ver = get_local_sls_version()
        local_ver_label = QLabel(f"Local Version: {local_ver}")
        local_ver_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt;")
        updater_layout.addWidget(local_ver_label)

        online_ver_label = QLabel("Latest Online: Not Checked")
        online_ver_label.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 9.5pt;")
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

        def on_check_btn_clicked():
            if "Update Available" in check_btn.text():
                reply = QMessageBox.question(
                    dialog,
                    "SLSsteam Update Available",
                    "A newer SLSsteam build is available.\n\n"
                    "Would you like to run the Headcrab setup script to update SLSsteam now?\n"
                    "  curl -fsSL headcrab.pages.dev | bash\n\n"
                    "ASSella will close while Headcrab runs in the terminal.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes
                )
                if reply == QMessageBox.StandardButton.Yes:
                    run_headcrab_and_quit(dialog)
                    return
            trigger_check()

        check_btn.clicked.connect(on_check_btn_clicked)
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

    # 4. Generate & Share Tickets (Experimental) Card
    ticket_card, ticket_layout = dialog._create_card_frame("Generate & Share Tickets (Experimental)")
    ticket_layout.setSpacing(12)

    # Experimental Warning Banner
    warning_lbl = QLabel(
        "<b>[Experimental Feature]</b> Export ticket tokens from owned Steam titles.\n"
        "<b>Notice:</b> Generated tickets are temporary auth tokens issued by Steam for sharing ownership. "
        "If not imported or used within <b>1 to 10 hours</b> of generation, Steam ownership tokens may expire and require re-exporting."
    )
    warning_lbl.setWordWrap(True)
    warning_lbl.setStyleSheet(
        "background: rgba(255, 170, 0, 0.08); "
        "border: 1px solid rgba(255, 170, 0, 0.3); "
        "border-radius: 6px; "
        "padding: 10px; "
        "color: #ffca28; "
        "font-size: 9pt; "
        "line-height: 1.4;"
    )
    ticket_layout.addWidget(warning_lbl)

    # Dropdown Selection for Available Games with Tickets
    from utils.ticket_manager import get_available_ticket_games, export_ticket, export_all_tickets

    combo_row = QHBoxLayout()
    combo_row.setSpacing(6)

    game_combo = QComboBox()
    game_combo.setStyleSheet("""
        QComboBox {
            background: rgba(255, 255, 255, 0.08);
            color: #FFFFFF;
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 4px;
            padding: 4px 8px;
        }
    """)

    available_games = get_available_ticket_games()
    if available_games:
        game_combo.addItem("Select from detected tickets...", "")
        for g in available_games:
            game_combo.addItem(g.get("display", f"AppID {g.get('appid')}"), str(g.get("appid")))
    else:
        game_combo.addItem("No tickets detected in Steam/SLS folder", "")

    combo_row.addWidget(game_combo, 1)

    refresh_btn = QPushButton("Refresh")
    refresh_btn.setFixedWidth(70)

    def _refresh_tickets():
        game_combo.clear()
        games = get_available_ticket_games()
        if games:
            game_combo.addItem("Select from detected tickets...", "")
            for g in games:
                game_combo.addItem(g.get("display", f"AppID {g.get('appid')}"), str(g.get("appid")))
        else:
            game_combo.addItem("No tickets detected in Steam/SLS folder", "")

    refresh_btn.clicked.connect(_refresh_tickets)
    combo_row.addWidget(refresh_btn)
    ticket_layout.addLayout(combo_row)

    # Custom AppID Export Row
    app_export_lay = QHBoxLayout()
    app_export_lay.setSpacing(6)

    appid_input = QLineEdit()
    appid_input.setPlaceholderText("Or enter custom AppID (e.g. 1086940)")
    appid_input.setStyleSheet("""
        QLineEdit {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 4px;
            padding: 4px 8px;
            color: #FFFFFF;
        }
    """)
    app_export_lay.addWidget(appid_input, 1)

    def _on_game_selected(idx):
        val = game_combo.itemData(idx)
        if val:
            appid_input.setText(str(val))

    game_combo.currentIndexChanged.connect(_on_game_selected)

    export_single_btn = QPushButton("Export Selected Ticket")
    export_single_btn.setStyleSheet("font-weight: bold; background: rgba(255, 255, 255, 0.15); color: #FFFFFF; border-radius: 4px; padding: 6px 12px;")

    def _do_export_single():
        try:
            appid = appid_input.text().strip()
            if not appid or not appid.isdigit():
                QMessageBox.warning(dialog, "Invalid AppID", "Please select a game from dropdown or enter a valid numeric Steam AppID.")
                return
            save_path, _ = QFileDialog.getSaveFileName(dialog, "Save Ownership Ticket YAML", f"ticket_{appid}.yaml", "YAML Files (*.yaml)")
            if save_path:
                ok, msg = export_ticket(appid, save_path)
                if ok:
                    QMessageBox.information(dialog, "Ticket Exported", msg)
                else:
                    QMessageBox.critical(dialog, "Export Failed", msg)
        except Exception as err:
            logger.error(f"Error exporting single ticket: {err}")
            QMessageBox.critical(dialog, "Export Error", f"Failed to export ticket: {err}")

    export_single_btn.clicked.connect(lambda _c=False: _do_export_single())
    app_export_lay.addWidget(export_single_btn)
    ticket_layout.addLayout(app_export_lay)

    # Batch Export Row
    batch_btn = QPushButton("Export All Available Tickets (Batch)")
    batch_btn.setStyleSheet("font-weight: bold; background: rgba(255, 255, 255, 0.12); color: #FFFFFF; border-radius: 4px; padding: 6px 12px;")

    def _do_export_all(_checked=False):
        try:
            dest_dir = QFileDialog.getExistingDirectory(dialog, "Select Output Directory for Ticket Batch Export")
            if dest_dir:
                from utils.ticket_manager import export_all_tickets
                ok, msg, count = export_all_tickets(dest_dir)
                if ok:
                    QMessageBox.information(dialog, "Batch Export Complete", msg)
                else:
                    QMessageBox.warning(dialog, "Batch Export", msg)
        except Exception as err:
            logger.error(f"Error exporting all tickets: {err}")
            QMessageBox.critical(dialog, "Export Error", f"Failed batch export: {err}")

    batch_btn.clicked.connect(lambda _c=False: _do_export_all())
    ticket_layout.addWidget(batch_btn)

    layout.addWidget(ticket_card)

    layout.addStretch()
    dialog.tab_widget.addTab(tab, "SLS")
    return tab
