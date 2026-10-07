import atexit
import logging
import os
import sys
import time
from collections import deque
from typing import Dict, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSlot, pyqtSignal, QMetaObject, Q_ARG
from PyQt6.QtGui import (
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QShortcut,
    QColor,
)
from PyQt6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
    QPushButton,
)

from managers.app_update_manager import AppUpdateManager
from managers.game_manager import GameManager
from managers.job_queue_manager import JobQueueManager
from managers.task_manager import TaskManager
from managers.ui_state_manager import UIStateManager
from ui.bottom_titlebar import BottomTitleBar
from ui.dialogs.credits import CreditsDialog
from ui.dialogs.fetchmanifest import FetchManifestDialog
from ui.dialogs.gamelibrary import GameLibraryDialog
from ui.dialogs.lain import LainMinigameDialog
from ui.dialogs.settings import SettingsDialog
from ui.dialogs.status import StatusDialog
from ui.widgets import (
    ResizeHandle,
    SimplifiedTerminalWidget,
)
from utils.logger import qt_log_handler
from utils.paths import Paths
from queue import Queue
from utils.web_server import WebServerManager, get_local_ip
from utils.settings import get_settings
from utils.task_runner import TaskRunner
from core.morrenus_api import get_all_hubcap_stats
from datetime import datetime, timezone
from utils.version import app_version

logger = logging.getLogger(__name__)

# How long Update All waits for the user to answer a depot-selection dialog
# before giving up on that game. Must comfortably exceed human reaction time —
# a short wait returns while the dialog is still open, which silently skips the
# game while leaving a stale dialog on screen.
DIALOG_WAIT_SECONDS = 300


def _extract_semver(raw: str) -> str:
    """Strip build-date prefix (e.g. '20260608+ASSella-') returning just the version tag."""
    # Format: YYYYMMDD+ASSella-<version>  OR  <version>
    if "+ASSella-" in raw:
        return raw.split("+ASSella-", 1)[1].strip()
    return raw.strip()


class MainWindow(QMainWindow):

    """Main application window."""

    refresh_system_status_signal = pyqtSignal()
    notify_sls_watcher_crashed_signal = pyqtSignal()
    # Signal used to safely invoke a callable on the main thread from a background thread.
    # Using a typed signal instead of Q_ARG(object, fn) avoids PyQt6 GIL/metatype crashes.
    _main_thread_callable = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.refresh_system_status_signal.connect(self.refresh_system_status)
        self.notify_sls_watcher_crashed_signal.connect(self.notify_sls_filewatcher_crashed)
        self._main_thread_callable.connect(self._run_on_main_thread)
        self._steamdb_verified = False
        self._steamdb_check_running = False
        self.resize_handles: Dict[str, ResizeHandle] = {}
        self.key_sequence = deque(maxlen=4)
        self.target_sequence = ["l", "a", "i", "n"]
        self.settings = None
        self.accent_color = None
        self.background_color = None
        self.task_manager = None
        self.ui_state = None
        self.job_queue = None
        self.game_manager = None
        self.exit_shortcut = None
        self.sequence_timeout = None
        self.central_widget = None
        self.layout = None
        self.titlebar_position = None
        self.bottom_titlebar = None

        self.quotes = [
            ("The cake is a lie.", "Portal"),
            ("Would you kindly?", "BioShock"),
            ("War. War never changes.", "Fallout"),
            ("Praise the Sun! \\o/", "Dark Souls"),
            ("It's dangerous to go alone! Take this.", "The Legend of Zelda"),
            ("A man chooses, a slave obeys.", "BioShock"),
            ("Snake? Snake?! SNAAAAAAKE!!!", "Metal Gear Solid"),
            ("Thank you Mario! But our princess is in another castle!", "Super Mario Bros."),
            ("All your base are belong to us.", "Zero Wing"),
            ("Nothing is true, everything is permitted.", "Assassin's Creed"),
            ("It's time to kick ass and chew bubblegum... and I'm all outta gum.", "Duke Nukem 3D"),
            ("Wake up, Mister Freeman. Wake up and smell the ashes.", "Half-Life 2"),
            ("You Died.", "Dark Souls"),
            ("Do you know the definition of insanity?", "Far Cry 3"),
            ("Protocol 3: Protect the Pilot.", "Titanfall 2"),
            ("A hunter must hunt.", "Bloodborne"),
            ("Hey, you. You're finally awake.", "The Elder Scrolls V: Skyrim"),
            ("Determination.", "Undertale"),
            ("The world fears the inevitable plummet into the abyss.", "NieR: Automata"),
            ("Stay a while and listen.", "Diablo II"),
            ("It's not about the money, it's about sending a message.", "Batman: Arkham City"),
            ("What is a man? A miserable little pile of secrets!", "Castlevania: Symphony of the Night"),
            ("I used to be an adventurer like you. Then I took an arrow in the knee.", "The Elder Scrolls V: Skyrim"),
            ("The right man in the wrong place can make all the difference in the world.", "Half-Life 2"),
            ("Wake the fuck up, Samurai. We have a city to burn.", "Cyberpunk 2077"),
            ("Don't be sorry. Be better.", "God of War"),
            ("Hesitation is defeat.", "Sekiro: Shadows Die Twice"),
            ("I am Malenia, Blade of Miquella.", "Elden Ring"),
            ("Rip and tear, until it is done.", "DOOM"),
            ("Had to be me. Someone else might have gotten it wrong.", "Mass Effect 3"),
            ("I'm Commander Shepard, and this is my favorite store on the Citadel.", "Mass Effect 2"),
            ("Truth is... the game was rigged from the start.", "Fallout: New Vegas"),
            ("Patrolling the Mojave almost makes you wish for a nuclear winter.", "Fallout: New Vegas"),
            ("There is no escape.", "Hades"),
            ("When life gives you lemons, don't make lemonade. Make life take the lemons back!", "Portal 2"),
            ("Nanomachines, son.", "Metal Gear Rising: Revengeance"),
            ("Memes, the DNA of the soul.", "Metal Gear Rising: Revengeance"),
            ("No cost too great. No mind to think. No will to break.", "Hollow Knight"),
            ("Mankind is dead. Blood is fuel. Hell is full.", "ULTRAKILL"),
            ("Sunrise, Parabellum.", "Disco Elysium"),
            ("There's always a lighthouse, there's always a man, there's always a city.", "BioShock Infinite"),
            ("Ah shit, here we go again.", "Grand Theft Auto: San Andreas"),
            ("I never asked for this.", "Deus Ex: Human Revolution"),
            ("What is better – to be born good, or to overcome your evil nature through great effort?", "The Elder Scrolls V: Skyrim"),
            ("You are a worm through time.", "Control"),
            ("Good hunting, Stalker.", "S.T.A.L.K.E.R."),
            ("If not us, then who?", "Metro 2033"),
            ("Keep on keeping on!", "Death Stranding"),
            ("I need a weapon.", "Halo 2"),
            ("In my restless dreams, I see that town. Silent Hill.", "Silent Hill 2"),
            ("Make us whole again.", "Dead Space"),
            ("We can't change what's done, we can only move on.", "Red Dead Redemption 2"),
            ("Foul Tarnished, in search of the Elden Ring.", "Elden Ring"),
            ("Fear the old blood.", "Bloodborne"),
            ("Tonight, Gehrman joins the hunt.", "Bloodborne"),
        ]
        self.quote_timer = None
        self.quote_label = None
        self.quote_source_label = None
        self.footer_widget = None
        self.main_container = None
        self.main_layout = None
        self.drop_zone_container = None
        self.drop_zone_layout = None
        self.status_pager = None
        self.drop_text_label = None
        self.active_hubcap_label = None
        self.dashboard_widget = None
        self.usage_value = None
        self.expiry_value = None
        self.update_all_btn = None
        self.steam_updates_value = None
        self.sls_lbl = None
        self.sls_status_value = None
        self.slssteam_lbl = None
        self.slssteam_status_value = None
        self.denuvo_sync_lbl = None
        self.denuvo_sync_value = None
        self._denuvo_sync_status = "Idle"


        self.progress_container = None
        self.progress_layout = None
        self.progress_bar = None
        self.speed_label = None
        self.progress_controls_widget = None
        self.media_pause_button = None
        self.media_cancel_button = None
        self.bottom_widget = None
        self.bottom_layout = None
        self.log_output = None
        self.stacked_terminal_widget = None
        self.simplified_terminal = None
        self.stats_task_runner = None
        self._autofetch_on_boot_done = False
        self._autofetch_runner = None

        self.update_check_timer = None
        self._tool_update_status = "checking"
        self._tool_update_available_flag = False
        self._tool_update_check_running = False
        self._latest_remote_version = ""
        # Track appids whose manifests have already been auto-fetched in this session
        self._autofetched_appids: set = set()

        self._setup_window_properties()
        self._initialize_managers()
        self._setup_ui()
        
        # Connect update progress signals after UI is initialized
        self.game_manager.update_check_progress.connect(
            self.simplified_terminal.set_updates_checking_progress
        )
        self.game_manager.all_updates_checked.connect(
            lambda: self.simplified_terminal.set_updates_checking_progress(-1, -1)
        )

        # Deferred refresh: run after the event loop processes the UI construction
        # so update_stats and refresh_system_status always see fully built widgets
        QTimer.singleShot(0, self._deferred_post_init_refresh)
        self._setup_resize_handles()
        if self.ui_state:
            self.ui_state.apply_style_settings()
        self.update_nerd_mode()
        self._setup_key_sequence_detector()
        self._setup_exit_shortcut()
        self._setup_update_timer()
        self.check_tool_updates()

        # Start Web Server on startup if enabled
        enable_web_ui = self.settings.value("enable_remote_web_ui", False, type=bool)
        if enable_web_ui:
            port = self.settings.value("web_ui_port", 8765, type=int)
            self.toggle_web_server(True, port=port)
        else:
            self._update_web_ui_status_label()

        # Trigger SLSsteam boot updates and config checks sequentially in a background thread
        import threading
        from utils.assfixer import run_boot_config_check
        from ui.dialogs.settings_sls import run_boot_update_check

        # Warm up Byparr and verify SteamDB Cloudflare bypass in the background
        try:
            self.check_steamdb_status()
        except Exception:
            pass
        
        def run_boot_checks():
            try:
                from utils.slssteam_integration import check_slssteam_binary_is_latest
                check_slssteam_binary_is_latest()
            except Exception as e:
                logger.debug(f"SLS binary boot version check skipped/failed: {e}")
            run_boot_update_check()
            run_boot_config_check()

            # Queue ProtonDB prefetch for all installed games, just before Denuvo sync.
            # This runs in background (worker threads) so it never blocks startup.
            try:
                from core.ratings import prefetch_protondb_for_appids
                if self.game_manager:
                    all_appids = [
                        str(g.get("appid", "0"))
                        for g in self.game_manager.get_all_games()
                        if g.get("appid") and str(g.get("appid")) not in ("0", "N/A", "unknown")
                    ]
                    if all_appids:
                        prefetch_protondb_for_appids(all_appids)
            except Exception as e:
                logger.debug(f"ProtonDB boot prefetch failed: {e}")

            # Run Denuvo cache prefetch and clean SLS config of any accidental blocklists (runs ONCE on v2.5.5 launch)
            try:
                from core.ratings import sync_denuvo_cache_and_config
                if self.settings and not self.settings.value("denuvo_config_cleaned_v255", False, type=bool):
                    from utils.yaml_config_manager import get_user_config_path, clean_denuvo_games_section
                    cfg = get_user_config_path()
                    if cfg and cfg.exists():
                        clean_denuvo_games_section(cfg)
                    self.settings.setValue("denuvo_config_cleaned_v255", True)
                    logger.info("Executed one-time SLS Denuvo blocklist cleanup for v2.5.5")
                sync_denuvo_cache_and_config(main_window=self, force=False)
            except Exception as e:
                logger.debug(f"Denuvo boot prefetch skipped/failed: {e}")

            # Safely refresh system status labels on the main window dashboard
            self.refresh_system_status_signal.emit()

            # Clean up orphaned non-installed search thumbnails from disk cache
            try:
                from utils.image_fetcher import ImageFetcher
                ImageFetcher.cleanup_uninstalled_cache()
            except Exception as e:
                logger.debug(f"Image cache cleanup skipped/failed: {e}")

        threading.Thread(target=run_boot_checks, daemon=True).start()

        # Check if Canary Welcome Slideshow or Training Wheels Protocol should be shown
        from utils.settings import is_canary_welcome_needed, is_twp_needed
        if is_canary_welcome_needed():
            QTimer.singleShot(1000, self._show_canary_welcome)
        elif is_twp_needed():
            QTimer.singleShot(1000, self._show_training_wheels)

    def _show_canary_welcome(self) -> None:
        """Display the Canary Welcome Slideshow onboarding dialog."""
        try:
            from ui.dialogs.canary_welcome_dialog import CanaryWelcomeDialog
            dlg = CanaryWelcomeDialog(parent=self, manual=False)
            dlg.exec()
        except Exception as e:
            logger.error(f"Failed to display Canary Welcome dialog: {e}", exc_info=True)

    def _show_training_wheels(self) -> None:
        """Display the Training Wheels Protocol transition and quickstart dialog."""
        try:
            from ui.dialogs.training_wheels import TrainingWheelsDialog
            dlg = TrainingWheelsDialog(parent=self, manual=False)
            dlg.exec()
        except Exception as e:
            logger.error(f"Failed to display Training Wheels Protocol dialog: {e}", exc_info=True)


    def _prompt_experimental_features_once_if_needed(self):
        """Prompt user once on startup to try experimental features if not already enabled."""
        has_prompted = self.settings.value("has_prompted_experimental", False, type=bool)
        isp_bypass = self.settings.value("isp_bypass_hubcap", False, type=bool)
        sls_acf = self.settings.value("experimental_acf_independent", False, type=bool)

        if not has_prompted and (not isp_bypass or not sls_acf):
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QCheckBox, QPushButton, QHBoxLayout

            dlg = QDialog(self)
            dlg.setWindowTitle("Experimental Features — Try & Feedback")
            dlg.setFixedWidth(540)
            dlg.setStyleSheet("""
                QDialog {
                    background-color: #1a1b26;
                    color: #a9b1d6;
                    border: 1px solid #3b4261;
                    border-radius: 8px;
                }
                QLabel { color: #c0caf5; }
                QCheckBox { color: #7aa2f7; font-size: 13px; font-weight: bold; }
                QCheckBox::indicator { width: 16px; height: 16px; }
                QPushButton {
                    background-color: #3b4261;
                    color: #c0caf5;
                    border: none;
                    border-radius: 4px;
                    padding: 8px 16px;
                    font-weight: bold;
                }
                QPushButton:hover { background-color: #7aa2f7; color: #15161e; }
            """)

            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(20, 20, 20, 20)
            layout.setSpacing(10)

            title_lbl = QLabel("🚀 Try ASSella Experimental Features!", dlg)
            title_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #7aa2f7;")
            layout.addWidget(title_lbl)

            desc_lbl = QLabel(
                "Enhance your download stability and Steam integration by testing our experimental features. "
                "You can change these anytime in Settings -> Experimental.",
                dlg
            )
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet("color: #a9b1d6; font-size: 12px; margin-bottom: 6px;")
            layout.addWidget(desc_lbl)

            chk_isp = QCheckBox("Enable ISP Bypass (Hubcap API)", dlg)
            chk_isp.setChecked(True)
            layout.addWidget(chk_isp)

            isp_sub = QLabel(
                "Bypasses ISP DNS censorship on Hubcap API requests using Google/Cloudflare DNS (1.1.1.1/8.8.8.8) "
                "with background Tor fallback. (Only affects API calls, not game file downloads).",
                dlg
            )
            isp_sub.setWordWrap(True)
            isp_sub.setStyleSheet("color: #565f89; font-size: 11px; margin-left: 22px; margin-bottom: 8px;")
            layout.addWidget(isp_sub)

            chk_acf = QCheckBox("Enable 'Let SLS handle ACF' (Native ACF Mode)", dlg)
            chk_acf.setChecked(True)
            layout.addWidget(chk_acf)

            acf_sub = QLabel(
                "Delegates .acf file creation and updates directly to Steam via SLSsteam API instead of writing them manually. "
                "Fixes 'Content Encrypted' errors, play instantly without Steam restarts, and clean native uninstallation.",
                dlg
            )
            acf_sub.setWordWrap(True)
            acf_sub.setStyleSheet("color: #565f89; font-size: 11px; margin-left: 22px; margin-bottom: 12px;")
            layout.addWidget(acf_sub)

            btn_box = QHBoxLayout()
            btn_box.addStretch()

            skip_btn = QPushButton("Skip for Now", dlg)
            skip_btn.setStyleSheet("background-color: transparent; color: #565f89;")
            skip_btn.clicked.connect(dlg.reject)

            save_btn = QPushButton("Enable Selected & Continue", dlg)
            save_btn.clicked.connect(dlg.accept)

            btn_box.addWidget(skip_btn)
            btn_box.addWidget(save_btn)
            layout.addLayout(btn_box)

            res = dlg.exec()
            self.settings.setValue("has_prompted_experimental", True)
            if res == QDialog.DialogCode.Accepted:
                self.settings.setValue("isp_bypass_hubcap", chk_isp.isChecked())
                self.settings.setValue("experimental_acf_independent", chk_acf.isChecked())
                if chk_acf.isChecked():
                    try:
                        from utils.yaml_config_manager import ensure_slssteam_prerequisites
                        ensure_slssteam_prerequisites()
                    except Exception:
                        pass


    def _setup_window_properties(self) -> None:
        """Configure basic window properties."""
        self.setWindowTitle("ASSELA")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setGeometry(100, 100, 800, 350)

        icon_path = Paths.resource("logo/icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            logger.warning(f"Could not find window icon at: {icon_path}")

        if sys.platform == "win32":
            MainWindow._setup_windows_taskbar()

    def _setup_exit_shortcut(self) -> None:
        """Setup Ctrl+Q shortcut to exit the application."""
        self.exit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        self.exit_shortcut.activated.connect(self.close)
        logger.info("Ctrl+Q exit shortcut registered")

    def _setup_key_sequence_detector(self) -> None:
        """Setup key sequence detection for Easter egg."""
        self.sequence_timeout = QTimer(self)
        self.sequence_timeout.setSingleShot(True)
        self.sequence_timeout.timeout.connect(self.key_sequence.clear)

    def keyPressEvent(self, event) -> None:
        """Override keyPressEvent to detect key sequences."""
        key_text = event.text().lower()

        if key_text:
            self.key_sequence.append(key_text)
            # Reset sequence after 3 seconds of inactivity
            self.sequence_timeout.start(3000)

            if list(self.key_sequence) == self.target_sequence:
                self._on_lain_sequence_activated()
                self.key_sequence.clear()

        super().keyPressEvent(event)

    def _on_lain_sequence_activated(self) -> None:
        """Handle L->A->I->N sequence activation."""
        logger.info("LAIN sequence detected!")
        self.open_lain_minigame()

    def open_lain_minigame(self) -> None:
        """Open the Serial Experiments Lain minigame."""
        dialog = LainMinigameDialog(self)
        dialog.game_completed.connect(self.on_minigame_completed)
        dialog.exec()

    def on_minigame_completed(self, score: int) -> None:
        """Handle minigame completion."""
        logger.info(f"Lain minigame completed with score: {score}")
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("The Wired")
        msg_box.setText(f"Connection Terminated\n\nFinal Score: {score}")
        msg_box.exec()

    @staticmethod
    def _setup_windows_taskbar() -> None:
        """Windows-specific taskbar configuration."""
        try:
            import ctypes

            app_id = "god.is.in.the.wired.accela"
            # noinspection PyUnresolvedReferences
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except (ImportError, AttributeError) as e:
            logger.warning(f"Could not set AppUserModelID: {e}")

    def _deferred_post_init_refresh(self) -> None:
        """Runs one event-loop tick after full UI construction.

        Guarantees update_stats and refresh_system_status always see fully
        built widgets even if the library scan completed before the UI was ready.
        """
        if self.simplified_terminal:
            self.simplified_terminal.update_stats()
        self.refresh_system_status()

    def _initialize_managers(self) -> None:
        """Initialize all manager classes."""
        self.settings = get_settings()
        if not self.settings.contains("update_check_api_provider"):
            self.settings.setValue("update_check_api_provider", "auto")

        self.accent_color = self.settings.value("accent_color", "#a1c9fd")
        self.background_color = self.settings.value("background_color", "#111318")

        self.task_manager = TaskManager(self)
        self.ui_state = UIStateManager(self)
        self.job_queue = JobQueueManager(self)
        self.game_manager = GameManager(self)
        self.app_update_manager = AppUpdateManager(self)

        # Initialize Web Server Manager
        self.web_command_queue = Queue()
        self.web_server_manager = WebServerManager(self, self.web_command_queue)
        self.web_command_timer = QTimer(self)
        self.web_command_timer.timeout.connect(self._process_web_commands)
        # Timer is started/stopped dynamically when web server starts/stops

        logger.info("Starting initial game library scan...")
        self.game_manager.scan_complete.connect(self._on_initial_scan_complete)
        
        # Connect game manager signals to update the dashboard's elements & FAB loading state
        self.game_manager.library_updated.connect(self.update_dashboard_elements)
        self.game_manager.update_check_started.connect(
            lambda: self.simplified_terminal._on_update_check_started() if hasattr(self, "simplified_terminal") and self.simplified_terminal else None
        )
        self.game_manager.all_updates_checked.connect(self.update_dashboard_elements)
        self.game_manager.all_updates_checked.connect(
            lambda: self.simplified_terminal._on_update_check_finished() if hasattr(self, "simplified_terminal") and self.simplified_terminal else None
        )
        self.game_manager.all_updates_checked.connect(self.refresh_hubcap_stats)

        # Initial stats fetch
        self.refresh_hubcap_stats()

        self.game_manager.scan_steam_libraries_async()

    def _process_web_commands(self) -> None:
        while not self.web_command_queue.empty():
            try:
                cmd = self.web_command_queue.get_nowait()
                cmd_type = cmd.get("type")
                if cmd_type == "enqueue_job":
                    path = cmd.get("path")
                    metadata = cmd.get("metadata")
                    logger.info(f"Main Window: Enqueueing job from Web UI for {metadata.get('game_name')}")
                    self.job_queue.add_job(path, metadata)
                elif cmd_type == "check_updates":
                    logger.info("Main Window: Checking updates triggered from Web UI")
                    if self.game_manager:
                        self.game_manager.reset_up_to_date_for_recheck()
                        self.game_manager.check_game_updates_async()
            except Exception as e:
                logger.error(f"Error processing web command: {e}")

    def toggle_web_server(self, enabled: bool, port: int = 8765) -> None:
        if enabled:
            if not self.web_server_manager.is_running():
                # Loopback by default; LAN binding is an explicit opt-in.
                allow_lan = self.settings.value("web_ui_allow_lan", False, type=bool)
                host = "0.0.0.0" if allow_lan else "127.0.0.1"
                if self.web_server_manager.start(host=host, port=port):
                    self.web_command_timer.start(200)
        else:
            if self.web_server_manager.is_running():
                self.web_server_manager.stop()
                self.web_command_timer.stop()
        self._update_web_ui_status_label()

    def _update_web_ui_status_label(self) -> None:
        if not hasattr(self, "web_ui_status_value") or not self.web_ui_status_value:
            return
        
        port = self.settings.value("web_ui_port", 8765, type=int)
        allow_lan = self.settings.value("web_ui_allow_lan", False, type=bool)
        
        # 1. Check if the local web server manager is running in this GUI instance
        if self.web_server_manager and self.web_server_manager.is_running():
            port = self.web_server_manager.server.port
            token = self.web_server_manager.auth_token
            # The UI is same-origin and already carries the token, so the plain
            # loopback URL is enough here. LAN access needs the token in the URL.
            if allow_lan:
                self.web_ui_status_value.setText(f"http://{get_local_ip()}:{port}/?token={token}")
                self.web_ui_status_value.setToolTip(
                    "Anyone on your local network can open this link while the server is running.\n"
                    "The token is regenerated every time the server starts."
                )
            else:
                self.web_ui_status_value.setText(f"http://127.0.0.1:{port}")
                self.web_ui_status_value.setToolTip(
                    "Bound to localhost only. Enable LAN access in settings to reach this from another device."
                )
            self.web_ui_status_value.setStyleSheet(f"color: {self.accent_color or '#C06C84'}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            return

        # 2. Check if the systemd background user service is active (Linux only)
        is_bg_active = False
        if sys.platform == "linux":
            try:
                import subprocess
                res = subprocess.run(
                    ["systemctl", "--user", "is-active", "assella-testing.service"],
                    capture_output=True,
                    text=True,
                )
                if res.stdout.strip() == "active":
                    is_bg_active = True
            except Exception:
                pass

        if is_bg_active:
            self.web_ui_status_value.setText(f"http://{get_local_ip()}:{port} (Service)")
            self.web_ui_status_value.setStyleSheet("color: #44cc44; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        else:
            self.web_ui_status_value.setText("Disabled")
            self.web_ui_status_value.setStyleSheet(f"color: {self.accent_color or '#C06C84'}; font-size: 11px; font-weight: bold; border: none; background: transparent;")


    def _on_initial_scan_complete(self, games_found: int) -> None:
        """Slot triggered when the initial library scan completes."""
        try:
            self.game_manager.scan_complete.disconnect(self._on_initial_scan_complete)
        except TypeError:
            pass  # Already disconnected or not connected

        if self.settings.value("check_updates_on_boot", True, type=bool):
            logger.info(f"Initial game library scan completed ({games_found} games found). Triggering staggered game updates check.")
            if self.game_manager:
                # Stagger by 2 seconds so the UI finishes rendering before Steam API storms begin
                QTimer.singleShot(2000, lambda: self.game_manager.check_game_updates_async())
        else:
            logger.info(f"Initial game library scan completed ({games_found} games found). Background boot updates check is disabled.")

        # Run depot key migration in the background (populates depot_keys.db from cached zips)
        self._run_depot_key_migration()

    def _run_depot_key_migration(self) -> None:
        """
        One-time background migration: extracts AES keys and AppTokens from all cached
        hubcap_manifests/*.zip files and persists them to depot_keys.db.
        Progress messages are logged at INFO level so they appear in the main window pager.
        """
        from core.tasks.depot_key_migration_task import DepotKeyMigrationTask
        from utils.task_runner import TaskRunner

        migration_task = DepotKeyMigrationTask()

        # Route progress messages through the standard logger so they appear in the pager
        migration_task.progress.connect(lambda msg: logger.info(msg))
        migration_task.finished.connect(self._on_depot_key_migration_finished)

        self._migration_runner = TaskRunner(self)
        self._migration_runner.run(migration_task.run)
        logger.info("[Depot Key Cache] Background migration started...")

    def _on_depot_key_migration_finished(self, migrated: int, skipped: int) -> None:
        """Called when depot key migration completes."""
        if migrated > 0:
            logger.info(
                f"[Depot Key Cache] Migration done: {migrated} game(s) migrated, "
                f"{skipped} skipped. Smart Update Mode is now available."
            )



    def _setup_update_timer(self) -> None:
        """Setup a timer to check for game updates periodically."""
        self.update_check_timer = QTimer(self)
        self.update_check_timer.timeout.connect(self._on_update_timer_timeout)
        self.apply_update_timer_settings()

    def apply_update_timer_settings(self) -> None:
        """Apply the interval setting for the update check timer."""
        interval_mins = self.settings.value("update_check_interval_minutes", 5, type=int)
        if interval_mins > 0:
            self.update_check_timer.start(interval_mins * 60 * 1000)
            logger.info(f"Update check timer started with interval: {interval_mins} minutes")
        else:
            self.update_check_timer.stop()
            logger.info("Update check timer disabled")

    def _on_update_timer_timeout(self) -> None:
        if self.game_manager:
            if getattr(self, "task_manager", None) and getattr(self.task_manager, "is_processing", False):
                logger.info("Download in progress — deferring periodic game update check")
                return
            logger.info("Running periodic game update check")
            # On a periodic check, reset 'up_to_date' games so they get re-verified.
            # 'update_available' games are left as-is (status won't change until downloaded).
            self.game_manager.reset_up_to_date_for_recheck()
            self.game_manager.check_game_updates_async(is_periodic=True)
        self.check_tool_updates()

    def _setup_ui(self) -> None:
        """Setup the main UI components."""
        self.central_widget = QWidget()
        self.central_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.central_widget.setStyleSheet("background: transparent;")
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.titlebar_position = self.settings.value(
            "titlebar_position", "bottom", type=str
        )

        if self.titlebar_position == "top":
            self.bottom_titlebar = BottomTitleBar(self)
            self.layout.addWidget(self.bottom_titlebar)

        self._create_main_content()
        self._create_bottom_section()

        # Create Footer for game quotes
        self.footer_widget = QWidget()
        self.footer_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.footer_widget.setFixedHeight(28)
        self.footer_widget.setStyleSheet("background: transparent; border: none;")
        
        footer_layout = QHBoxLayout(self.footer_widget)
        footer_layout.setContentsMargins(15, 0, 15, 0)
        footer_layout.setSpacing(8)
        
        self.quote_label = QLabel(self.quotes[0][0])
        self.quote_label.setStyleSheet("font-style: italic; font-size: 12px; color: rgba(255, 255, 255, 0.92); background: transparent; border: none;")
        
        self.quote_source_label = QLabel(f"— {self.quotes[0][1]}")
        self.quote_source_label.setStyleSheet("font-size: 11px; font-weight: bold; color: rgba(255, 255, 255, 0.55); background: transparent; border: none;")
        
        footer_layout.addStretch()
        footer_layout.addWidget(self.quote_label)
        footer_layout.addWidget(self.quote_source_label)
        footer_layout.addStretch()
        
        self.layout.addWidget(self.footer_widget)

        # Start quote rotation timer
        self.quote_timer = QTimer(self)
        self.quote_timer.timeout.connect(self.rotate_quote)
        self.quote_timer.start(10000)

        if self.titlebar_position != "top":
            self.bottom_titlebar = BottomTitleBar(self)
            self.layout.addWidget(self.bottom_titlebar)

        self.setAcceptDrops(True)

    def _setup_resize_handles(self) -> None:
        """Setup invisible resize handles for all edges and corners."""
        edges = [
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
            "left",
            "right",
            "top",
            "bottom",
        ]

        for name in edges:
            handle = ResizeHandle(name, self)
            handle.setCursor(MainWindow._get_cursor_for_edge(name))
            self.resize_handles[name] = handle

        self._update_resize_handles_geometry()

    @staticmethod
    def _get_cursor_for_edge(edge: str) -> Qt.CursorShape:
        """Get appropriate cursor for each resize edge."""
        cursors = {
            "left": Qt.CursorShape.SizeHorCursor,
            "right": Qt.CursorShape.SizeHorCursor,
            "top": Qt.CursorShape.SizeVerCursor,
            "bottom": Qt.CursorShape.SizeVerCursor,
            "top_left": Qt.CursorShape.SizeFDiagCursor,
            "top_right": Qt.CursorShape.SizeBDiagCursor,
            "bottom_left": Qt.CursorShape.SizeBDiagCursor,
            "bottom_right": Qt.CursorShape.SizeFDiagCursor,
        }
        return cursors.get(edge, Qt.CursorShape.ArrowCursor)

    def _update_resize_handles_geometry(self) -> None:
        """Calculate and set geometry for all resize handles."""
        if not self.resize_handles:
            return

        w, h = self.width(), self.height()
        hw = 6  # Handle width

        # Define geometry calculations for each handle type
        geometries = {
            "top_left": (0, 0, hw, hw),
            "top_right": (w - hw, 0, hw, hw),
            "bottom_left": (0, h - hw, hw, hw),
            "bottom_right": (w - hw, h - hw, hw, hw),
            "left": (0, hw, hw, h - 2 * hw),
            "right": (w - hw, hw, hw, h - 2 * hw),
            "top": (hw, 0, w - 2 * hw, hw),
            "bottom": (hw, h - hw, w - 2 * hw, hw),
        }

        for name, (x, y, width, height) in geometries.items():
            if name in self.resize_handles:
                self.resize_handles[name].setGeometry(x, y, width, height)

    def resizeEvent(self, event) -> None:
        """Update resize handle positions when window is resized."""
        super().resizeEvent(event)
        self._update_resize_handles_geometry()
        self.position_update_all_btn()

    def position_update_all_btn(self):
        if hasattr(self, "simplified_terminal") and self.simplified_terminal:
            term = self.simplified_terminal

            # Don't reposition FABs when the active-job view is shown —
            # panel_mid has zero/altered geometry then, causing incorrect placement.
            if hasattr(term, "layout") and term.layout.currentIndex() != 0:
                return

            if not hasattr(term, "panel_mid") or not term.panel_mid:
                return

            w = term.panel_mid.width()
            h = term.panel_mid.height()
            if w < 50 or h < 50:
                return

            # Position refresh button
            if hasattr(term, "refresh_updates_btn") and term.refresh_updates_btn:
                # If update_all_btn is visible, place refresh to its left
                if hasattr(term, "update_all_btn") and term.update_all_btn and term.update_all_btn.isVisible():
                    term.update_all_btn.adjustSize()
                    x_update = max(8, w - term.update_all_btn.width() - 16)
                    y_update = max(8, h - term.update_all_btn.height() - 16)
                    term.update_all_btn.move(x_update, y_update)
                    term.update_all_btn.raise_()

                    x_refresh = max(8, x_update - 36 - 8)
                    y_refresh = max(8, h - 36 - 16)
                    term.refresh_updates_btn.move(x_refresh, y_refresh)
                else:
                    x_refresh = max(8, w - 36 - 16)
                    y_refresh = max(8, h - 36 - 16)
                    term.refresh_updates_btn.move(x_refresh, y_refresh)

                term.refresh_updates_btn.raise_()

    def force_check_all_updates(self):
        """Forces a clean re-check of updates for all games, ignoring cache."""
        # Guard: don't allow update check while Update All is actively queueing
        if getattr(self, "_update_all_running", False):
            logger.info("Update All is running — ignoring manual update check request")
            return
        if self.game_manager:
            logger.info("Forcing full updates check for all games (bypassing cache)")
            if hasattr(self, "simplified_terminal") and self.simplified_terminal:
                if hasattr(self.simplified_terminal, "refresh_updates_btn") and self.simplified_terminal.refresh_updates_btn:
                    self.simplified_terminal.refresh_updates_btn.set_loading(True)
            # Reset 'up_to_date' / 'cannot_determine' / 'checking' games to 'checking'
            # so they are re-queried. Games already marked 'update_available' are
            # intentionally left intact so the pending-updates list stays populated
            # during the refresh — cache acts as a display layer throughout.
            for g in self.game_manager.games:
                if g.get("update_status") != "update_available":
                    g["update_status"] = "checking"
            self.game_manager.library_updated.emit()

            # Start checks with force_refresh = True so update_available games are
            # re-verified too, even though we kept their visual status intact above.
            self.game_manager.check_game_updates_async(force_refresh=True)

    def _create_main_content(self) -> None:
        """Create the main content area with drop zone."""
        self.main_container = QWidget()
        self.main_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.layout.addWidget(self.main_container, 1)

        self.main_layout = QVBoxLayout(self.main_container)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self._create_drop_zone()
        self._create_progress_section()

    def _create_drop_zone(self) -> None:
        """Create the drag and drop area and stats dashboard."""
        self.drop_zone_container = QWidget()
        self.drop_zone_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.drop_zone_layout = QVBoxLayout(self.drop_zone_container)
        self.drop_zone_layout.setContentsMargins(0, 0, 0, 0)
        self.drop_zone_layout.setSpacing(0)



        # Status Pager Display
        from ui.status_pager import StatusPagerWidget
        self.status_pager = StatusPagerWidget(self)

        # Backward compatibility wrapper for other modules setting drop_text_label text
        class DropTextLabelWrapper:
            def __init__(self, pager):
                self.pager = pager
            def setText(self, text, is_warning=False):
                if is_warning:
                    self.pager.show_warning(text)
                else:
                    self.pager.set_status(text)
            def setStyleSheet(self, style):
                pass

        self.drop_text_label = DropTextLabelWrapper(self.status_pager)

        # Dashboard container widget
        self.dashboard_widget = QWidget()
        self.dashboard_widget.setObjectName("dashboard_widget")
        self.dashboard_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.dashboard_widget.setMinimumHeight(65)
        self.dashboard_widget.setMaximumHeight(85)
        
        self.dashboard_widget.setStyleSheet("""
            #dashboard_widget {
                background-color: rgba(18, 18, 22, 235);
                border: 1px solid rgba(255, 255, 255, 12);
                border-radius: 8px;
                margin: 4px 15px;
            }
        """)

        dash_main_layout = QVBoxLayout(self.dashboard_widget)
        dash_main_layout.setContentsMargins(15, 6, 15, 6)
        dash_main_layout.setSpacing(4)

        row_item_style = "color: rgba(255, 255, 255, 0.90); font-size: 11px; font-weight: bold; background: transparent; border: none;"

        # --- ROW 1 ---
        row1_layout = QHBoxLayout()
        row1_layout.setSpacing(20)
        row1_layout.addStretch()

        # 1. Hubcap API Stats
        hubcap_api_lbl = QLabel("Hubcap:")
        hubcap_api_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.70); font-size: 11px; background: transparent; border: none;")
        self.hubcap_api_value = QLabel("API: --/-- | Single: --/--")
        self.hubcap_api_value.setStyleSheet(row_item_style)
        hubcap_api_item = QHBoxLayout()
        hubcap_api_item.setSpacing(4)
        hubcap_api_item.addWidget(hubcap_api_lbl)
        hubcap_api_item.addWidget(self.hubcap_api_value)
        row1_layout.addLayout(hubcap_api_item)

        # 2. SLS Config (hidden)
        self.sls_lbl = QLabel("SLS Config:")
        self.sls_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.70); font-size: 11px; background: transparent; border: none;")
        self.sls_status_value = QLabel("Checking...")
        self.sls_status_value.setStyleSheet(self._get_status_style("neutral"))
        sls_item = QHBoxLayout()
        sls_item.setSpacing(4)
        sls_item.addWidget(self.sls_lbl)
        sls_item.addWidget(self.sls_status_value)

        # 3. SLSsteam Status (Online/Offline)
        self.slssteam_lbl = QLabel("SLSsteam:")
        self.slssteam_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.70); font-size: 11px; background: transparent; border: none;")
        self.slssteam_status_value = QLabel("Checking...")
        self.slssteam_status_value.setStyleSheet(self._get_status_style("neutral"))
        slssteam_item = QHBoxLayout()
        slssteam_item.setSpacing(4)
        slssteam_item.addWidget(self.slssteam_lbl)
        slssteam_item.addWidget(self.slssteam_status_value)
        row1_layout.addLayout(slssteam_item)

        # 4. Steam Client Process Status (Online/Offline)
        steam_conn_lbl = QLabel("Steam:")
        steam_conn_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.70); font-size: 11px; background: transparent; border: none;")
        self.steam_conn_value = QLabel("Checking...")
        self.steam_conn_value.setStyleSheet(self._get_status_style("neutral"))
        steam_conn_item = QHBoxLayout()
        steam_conn_item.setSpacing(4)
        steam_conn_item.addWidget(steam_conn_lbl)
        steam_conn_item.addWidget(self.steam_conn_value)
        row1_layout.addLayout(steam_conn_item)

        # 5. Overall System & SLS Health Status (lies after steam status thing but before steamdb status)
        health_lbl = QLabel("Health:")
        health_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.70); font-size: 11px; background: transparent; border: none;")
        self.health_status_value = QLabel("Checking...")
        self.health_status_value.setStyleSheet(self._get_status_style("neutral"))
        self.health_status_value.setCursor(Qt.CursorShape.PointingHandCursor)
        self.health_status_value.setToolTip("System & SLSsteam Health Status. Click to open Health settings.")
        self.health_status_value.mousePressEvent = lambda e: self.open_settings(initial_tab="Health")
        health_item = QHBoxLayout()
        health_item.setSpacing(4)
        health_item.addWidget(health_lbl)
        health_item.addWidget(self.health_status_value)
        row1_layout.addLayout(health_item)

        # 6. SteamDB Solver Status (Right next to Health) - only visible if Byparr is installed
        self.steamdb_container = QWidget()
        self.steamdb_container.setStyleSheet("background: transparent; border: none;")
        self.steamdb_item = QHBoxLayout(self.steamdb_container)
        self.steamdb_item.setContentsMargins(0, 0, 0, 0)
        self.steamdb_item.setSpacing(4)

        self.steamdb_lbl = QLabel("SteamDB:")
        self.steamdb_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.70); font-size: 11px; background: transparent; border: none;")
        self.steamdb_status_value = QLabel("Checking...")
        self.steamdb_status_value.setStyleSheet(self._get_status_style("neutral"))
        self.steamdb_item.addWidget(self.steamdb_lbl)
        self.steamdb_item.addWidget(self.steamdb_status_value)
        row1_layout.addWidget(self.steamdb_container)

        # Only show SteamDB if Byparr directory exists
        try:
            from core.steamdb_scraper import ByparrManager
            has_byparr = ByparrManager.find_byparr_dir() is not None
        except Exception:
            has_byparr = False
        self.steamdb_container.setVisible(has_byparr)

        row1_layout.addStretch()
        dash_main_layout.addLayout(row1_layout)

        # --- ROW 2 ---
        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(20)
        row2_layout.addStretch()

        # 1. Hubcap Connection Status
        hubcap_conn_lbl = QLabel("Hubcap:")
        hubcap_conn_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.70); font-size: 11px; background: transparent; border: none;")
        self.hubcap_conn_value = QLabel("Connecting...")
        self.hubcap_conn_value.setStyleSheet(self._get_status_style("neutral"))
        hubcap_conn_item = QHBoxLayout()
        hubcap_conn_item.setSpacing(4)
        hubcap_conn_item.addWidget(hubcap_conn_lbl)
        hubcap_conn_item.addWidget(self.hubcap_conn_value)
        row2_layout.addLayout(hubcap_conn_item)

        # 2. Steam Updates
        steam_updates_lbl = QLabel("Steam Updates:")
        steam_updates_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.70); font-size: 11px; background: transparent; border: none;")
        self.steam_updates_value = QLabel("Checking...")
        self.steam_updates_value.setStyleSheet(self._get_status_style("neutral"))
        steam_updates_item = QHBoxLayout()
        steam_updates_item.setSpacing(4)
        steam_updates_item.addWidget(steam_updates_lbl)
        steam_updates_item.addWidget(self.steam_updates_value)
        row2_layout.addLayout(steam_updates_item)

        # 3. ASSella Status
        assella_lbl = QLabel("ASSella:")
        assella_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.70); font-size: 11px; background: transparent; border: none;")
        self.assella_status_value = QLabel("Checking...")
        self.assella_status_value.setStyleSheet(self._get_status_style("neutral"))
        assella_item = QHBoxLayout()
        assella_item.setSpacing(4)
        assella_item.addWidget(assella_lbl)
        assella_item.addWidget(self.assella_status_value)
        row2_layout.addLayout(assella_item)

        # 4. Library Size
        library_lbl = QLabel("Library:")
        library_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.70); font-size: 11px; background: transparent; border: none;")
        self.library_size_value = QLabel("-- GB (-- games)")
        self.library_size_value.setStyleSheet(row_item_style)
        library_item = QHBoxLayout()
        library_item.setSpacing(4)
        library_item.addWidget(library_lbl)
        library_item.addWidget(self.library_size_value)
        row2_layout.addLayout(library_item)



        row2_layout.addStretch()
        dash_main_layout.addLayout(row2_layout)
        
        self.drop_zone_layout.addWidget(self.status_pager)
        self.drop_zone_layout.addWidget(self.dashboard_widget, 2)
        self.main_layout.addWidget(self.drop_zone_container, 1)

    def _create_progress_section(self) -> None:
        """Create the progress bar, controls and speed label."""
        self.progress_container = QWidget()
        self.progress_layout = QVBoxLayout(self.progress_container)
        self.progress_layout.setContentsMargins(15, 4, 15, 4)

        # Active Hubcap Label (visible only when downloading, left-aligned)
        self.active_hubcap_layout = QHBoxLayout()
        self.active_hubcap_layout.setContentsMargins(2, 0, 2, 0)
        self.active_hubcap_label = QLabel("")
        self.active_hubcap_label.setStyleSheet("color: #888888; font-size: 11px; font-weight: bold;")
        self.active_hubcap_label.setVisible(False)
        self.active_hubcap_layout.addWidget(self.active_hubcap_label)
        self.active_hubcap_layout.addStretch()
        self.progress_layout.addLayout(self.active_hubcap_layout)

        from ui.animated_progress_bar import AnimatedProgressBar
        self.progress_bar = AnimatedProgressBar(self)
        self.progress_bar.setVisible(False)
        self._update_progress_bar_style()
        self.progress_layout.addWidget(self.progress_bar)

        # Inline text controls row: speed label (left) · Pause · Stop (right)
        self.progress_controls_widget = QWidget()
        self.progress_controls_layout = QHBoxLayout(self.progress_controls_widget)
        self.progress_controls_layout.setContentsMargins(2, 1, 2, 1)
        self.progress_controls_layout.setSpacing(6)

        self.speed_label = QLabel("")
        self.speed_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.speed_label.setVisible(False)
        self.progress_controls_layout.addWidget(self.speed_label)

        self.progress_controls_layout.addStretch()

        # Separator dot between controls
        self._sep_label = QLabel("·")
        self._sep_label.setVisible(False)

        self.media_pause_button = QPushButton("Pause")
        self.media_cancel_button = QPushButton("Stop")

        self.media_pause_button.clicked.connect(self.task_manager.toggle_pause)
        self.media_cancel_button.clicked.connect(self.task_manager.cancel_current_job)

        self.media_pause_button.setVisible(False)
        self.media_cancel_button.setVisible(False)

        self.progress_controls_layout.addWidget(self.media_pause_button)
        self.progress_controls_layout.addWidget(self._sep_label)
        self.progress_controls_layout.addWidget(self.media_cancel_button)

        self.progress_layout.addWidget(self.progress_controls_widget)
        self.main_layout.addWidget(self.progress_container, 1)

    def _create_bottom_section(self) -> None:
        """Create the bottom section with queue and logs."""
        self.bottom_widget = QWidget()
        self.bottom_layout = QHBoxLayout(self.bottom_widget)
        self.bottom_layout.setContentsMargins(15, 4, 15, 4)

        self.ui_state.setup_queue_panel()
        self.bottom_layout.addWidget(self.ui_state.queue_widget, 1)

        self.simplified_terminal = SimplifiedTerminalWidget(self)
        self.bottom_layout.addWidget(self.simplified_terminal, 1)

        self.layout.addWidget(self.bottom_widget, 3)
        self.ui_state.queue_widget.setVisible(False)

    def update_nerd_mode(self, nerd: Optional[bool] = None) -> None:
        """Nerd mode has been permanently deprecated."""
        pass

    def update_progress_bar_style(self) -> None:
        self._update_progress_bar_style()
        self.update_media_buttons_style()

    def update_media_buttons_style(self) -> None:
        accent = self.accent_color or "#C06C84"
        # Flat text style — matches the tool's link/label aesthetic
        btn_style = f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                color: {accent};
                font-size: 9pt;
                padding: 0 2px;
                text-decoration: none;
            }}
            QPushButton:hover {{
                color: #FFFFFF;
                text-decoration: underline;
            }}
            QPushButton:pressed {{
                color: rgba(255, 255, 255, 160);
            }}
        """
        sep_style = f"color: rgba(255,255,255,60); font-size: 9pt; padding: 0;"
        if self.media_pause_button:
            self.media_pause_button.setStyleSheet(btn_style)
        if self.media_cancel_button:
            self.media_cancel_button.setStyleSheet(btn_style)
        if hasattr(self, "_sep_label") and self._sep_label:
            self._sep_label.setStyleSheet(sep_style)

    def _update_progress_bar_style(self) -> None:
        """Update progress bar styling."""
        if hasattr(self, "progress_bar") and self.progress_bar:
            self.progress_bar.setStyleSheet("background: transparent; border: none;")
            self.progress_bar.update()

    def open_settings(self, initial_tab: Optional[str] = None) -> None:
        dialog = SettingsDialog(self, initial_tab=initial_tab)
        dialog.exec()
        self._check_network_connections_async()
        self.refresh_hubcap_stats()

    def open_fetch_dialog(self) -> None:
        self.ui_state.fetch_dialog = FetchManifestDialog(self)
        self.ui_state.fetch_dialog.exec()
        self.ui_state.fetch_dialog = None


    def open_game_library(self) -> None:
        dialog = GameLibraryDialog(self)
        dialog.exec()

    def open_status_dialog(self) -> None:
        dialog = StatusDialog(self)
        dialog.exec()

    def open_credits_dialog(self) -> None:
        dialog = CreditsDialog(self)
        dialog.exec()

    def check_steam_updates_blocked(self) -> bool:
        """Check if steam updates are blocked via steam.cfg."""
        from pathlib import Path
        possible_paths = [
            Path.home() / ".steam/steam/steam.cfg",
            Path.home() / ".steam/root/steam.cfg",
            Path.home() / ".local/share/Steam/steam.cfg",
            Path.home() / ".steam/steam.cfg",
        ]
        target_path = None
        for p in possible_paths:
            if p.exists():
                target_path = p
                break
        if not target_path:
            return False
        try:
            lines = target_path.read_text().splitlines()
            inhibit = False
            force_disable = False
            for line in lines:
                # strip comments
                line = line.split('#')[0].split(';')[0].strip()
                if '=' in line:
                    k, v = line.split('=', 1)
                    k = k.strip().lower()
                    v = v.strip().lower()
                    if k == "bootstrapperinhibitall" and v in ("enable", "enabled", "true", "1"):
                        inhibit = True
                    if k == "bootstrapperforceselfupdate" and v in ("disable", "disabled", "false", "0"):
                        force_disable = True
            return inhibit or force_disable
        except Exception as e:
            logger.error(f"Error reading steam.cfg: {e}")
            return False

    def _get_status_style(self, state: str) -> str:
        """
        Returns the stylesheet string for bottom visor status values 
        using theme-harmonized semantic colors.
        """
        accent_color = self.settings.value("accent_color", "#C06C84", type=str)
        from utils.color_utils import get_semantic_colors
        sem_colors = get_semantic_colors(accent_color)
        
        color_map = {
            "success": sem_colors["success"],
            "warning": sem_colors["warning"],
            "error": sem_colors["error"],
            "info": sem_colors["info"],
            "neutral": "#888888"
        }
        
        c = color_map.get(state.lower(), "#888888")
        return f"color: {c} !important; font-size: 11px; font-weight: bold; border: none; background: transparent;"

    @pyqtSlot()
    def notify_sls_filewatcher_crashed(self) -> None:
        """Update UI when SLSsteam filewatcher has crashed in Steam."""
        warning_msg = "SLSsteam Filewatcher crashed in Steam . Restart Steam"
        if hasattr(self, "status_pager") and self.status_pager:
            self.status_pager.show_warning(warning_msg, 30)
        elif hasattr(self, "drop_text_label") and self.drop_text_label:
            self.drop_text_label.setText(warning_msg)

        if hasattr(self, "active_game_card") and self.active_game_card:
            self.active_game_card.set_sub_status(warning_msg, is_warning=True)

        self.refresh_system_status()

    @pyqtSlot()
    def refresh_system_status(self) -> None:
        """Refresh local Steam updates, SLS, and ASSella status labels."""
        if not hasattr(self, "steam_updates_value") or not self.steam_updates_value:
            return
            
        blocked = self.check_steam_updates_blocked()
        if blocked:
            self.steam_updates_value.setText("Blocked")
            self.steam_updates_value.setStyleSheet(self._get_status_style("success"))
        else:
            self.steam_updates_value.setText("Allowed")
            self.steam_updates_value.setStyleSheet(self._get_status_style("warning"))
            
        # --- SLS Detection ---
        from ui.dialogs.settings_sls import get_sls_paths
        import os

        sls_paths = get_sls_paths()
        sls_detected = sls_paths.get("detected", False)
        version_file_exists = os.path.exists(sls_paths.get("version_file", "")) if sls_detected else False
        ignore_updater = self.settings.value("ignore_slssteam_updater", False, type=bool) if self.settings else False

        # --- SLS Detection & API Integration ---
        from utils.slssteam_integration import (
            is_slssteam_process_active,
            is_steam_process_running,
            is_sls_filewatcher_dead,
        )
        sls_active = self.settings.value("experimental_acf_independent", False, type=bool) if self.settings else False

        # SLS Config Status (SLS Integration Status)
        if hasattr(self, "sls_lbl") and self.sls_lbl:
            self.sls_lbl.setEnabled(sls_active)
        if hasattr(self, "sls_status_value") and self.sls_status_value:
            self.sls_status_value.setEnabled(True)
            if sls_active:
                self.sls_status_value.setText("Active")
                self.sls_status_value.setStyleSheet(self._get_status_style("success"))
            else:
                self.sls_status_value.setText("Disabled")
                self.sls_status_value.setStyleSheet(self._get_status_style("neutral"))

        # SLSsteam API Status (Active check of process)
        if hasattr(self, "slssteam_lbl") and self.slssteam_lbl:
            self.slssteam_lbl.setEnabled(True)
        if hasattr(self, "slssteam_status_value") and self.slssteam_status_value:
            self.slssteam_status_value.setEnabled(True)
            if sls_active and is_slssteam_process_active():
                self.slssteam_status_value.setText("Online")
                self.slssteam_status_value.setStyleSheet(self._get_status_style("success"))
            else:
                self.slssteam_status_value.setText("Offline")
                self.slssteam_status_value.setStyleSheet(self._get_status_style("error"))

        # Steam Client Process Status
        if hasattr(self, "steam_conn_value") and self.steam_conn_value:
            self.steam_conn_value.setEnabled(True)
            if not is_steam_process_running():
                self.steam_conn_value.setText("Offline")
                self.steam_conn_value.setStyleSheet(self._get_status_style("error"))
                self.steam_conn_value.setToolTip("Steam client is not running.")
            elif is_sls_filewatcher_dead():
                self.steam_conn_value.setText("Restart")
                self.steam_conn_value.setStyleSheet(self._get_status_style("warning"))
                self.steam_conn_value.setToolTip(
                    "Steam: Restart required\n"
                    "• SLSsteam file watcher has crashed in Steam.\n"
                    "• Restart Steam to reload game configs and licenses."
                )
            else:
                self.steam_conn_value.setText("Online")
                self.steam_conn_value.setStyleSheet(self._get_status_style("success"))
                self.steam_conn_value.setToolTip("Steam client is running normally.")

        # Health Visor Status (SLS binary + version + config + SLS API)
        if hasattr(self, "health_status_value") and self.health_status_value:
            try:
                import utils.assfixer as assfixer
                from utils.slssteam_integration import check_slssteam_binary_is_latest

                sls_detected = sls_paths.get("detected", False)
                ver_res = check_slssteam_binary_is_latest()
                ver_status = ver_res.get("status", "error")
                version_ok = (ver_status == "up_to_date")

                cfg_boot_status = getattr(assfixer, "boot_status", None)
                config_ok = (cfg_boot_status == "optimal")

                sls_api_enabled = self.settings.value("experimental_acf_independent", False, type=bool) if self.settings else False
                sls_api_active = sls_api_enabled and is_slssteam_process_active()
                watcher_dead = is_sls_filewatcher_dead()

                if cfg_boot_status in ("checking", None) or ver_status == "checking":
                    self.health_status_value.setText("Checking...")
                    self.health_status_value.setStyleSheet(self._get_status_style("neutral"))
                    self.health_status_value.setToolTip("Checking System & SLS Health status...")
                elif sls_detected and version_ok and config_ok and sls_api_active and not watcher_dead:
                    self.health_status_value.setText("Good")
                    self.health_status_value.setStyleSheet(self._get_status_style("success"))
                    self.health_status_value.setToolTip(
                        "Health: Good\n"
                        "• SLSsteam Binary: Detected\n"
                        f"• SLSsteam Version: Up to date ({ver_res.get('release_tag', '')})\n"
                        "• SLS Config: Optimal\n"
                        "• SLS Native API: Active\n\n"
                        "Click to open Health settings."
                    )
                else:
                    issues = []
                    if not sls_detected:
                        issues.append("SLSsteam binary not found")
                    elif not version_ok:
                        tag = ver_res.get("release_tag", "")
                        issues.append(f"SLSsteam update available ({tag})" if tag else "SLSsteam outdated")
                    if not config_ok:
                        issues.append("SLS config needs repair/resync")
                    if not sls_api_active:
                        if not sls_api_enabled:
                            issues.append("SLS Native API disabled in settings")
                        elif not is_slssteam_process_active():
                            if not is_steam_process_running():
                                issues.append("Steam not running (SLS not injected)")
                            else:
                                issues.append("SLS process not injected into Steam")
                    if watcher_dead:
                        issues.append("SLS filewatcher crashed (Restart Steam)")

                    self.health_status_value.setText("Attention" if issues else "Checking...")
                    self.health_status_value.setStyleSheet(self._get_status_style("warning"))
                    tooltip = "Health: Attention required\n" + "\n".join(f"• {iss}" for iss in issues) + "\n\nClick to open Health settings."
                    self.health_status_value.setToolTip(tooltip)
            except Exception as e:
                logger.debug(f"Error updating Health visor status: {e}")
                self.health_status_value.setText("Checking...")
                self.health_status_value.setStyleSheet(self._get_status_style("neutral"))





        # SteamDB Visor Sync
        if hasattr(self, "steamdb_container") and hasattr(self, "steamdb_status_value"):
            try:
                from core.steamdb_scraper import ByparrManager
                has_byparr = ByparrManager.find_byparr_dir() is not None
                if not has_byparr:
                    self.steamdb_container.setVisible(False)
                else:
                    self.steamdb_container.setVisible(True)
                    if ByparrManager.is_running():
                        self.steamdb_status_value.setText("Ready")
                        self.steamdb_status_value.setStyleSheet(self._get_status_style("success"))
                        self.steamdb_status_value.setToolTip(
                            "SteamDB: Cloudflare Turnstile bypass active via Byparr\n"
                            "Version history, patch notes & depot enrichment enabled."
                        )
                    else:
                        self.steamdb_status_value.setText("Offline")
                        self.steamdb_status_value.setStyleSheet(self._get_status_style("neutral"))
                        self.steamdb_status_value.setToolTip(
                            "SteamDB: Solver is currently offline.\n"
                            "It will start automatically when version history or builds are accessed."
                        )
            except Exception:
                pass

        # ASSella Status
        if hasattr(self, "assella_status_value") and self.assella_status_value:
            tool_status = getattr(self, "_tool_update_status", "checking")
            if tool_status == "update_available":
                self.assella_status_value.setText("Update!")
                self.assella_status_value.setStyleSheet(self._get_status_style("warning"))
                self.assella_status_value.setToolTip(
                    f"New version available: {getattr(self, '_latest_remote_version', '')}"
                )
            elif tool_status == "up_to_date":
                self.assella_status_value.setText("Latest")
                self.assella_status_value.setStyleSheet(self._get_status_style("success"))
                self.assella_status_value.setToolTip(f"ASSella is up to date (v{app_version})")
            elif tool_status in ("offline", "error", "cant_check"):
                self.assella_status_value.setText("Can't Check")
                self.assella_status_value.setStyleSheet(self._get_status_style("error"))
                self.assella_status_value.setToolTip(
                    f"Could not check for ASSella updates (Offline or GitHub unreachable)\nCurrent version: v{app_version}"
                )
            else:
                self.assella_status_value.setText("Checking...")
                self.assella_status_value.setStyleSheet(self._get_status_style("neutral"))
                self.assella_status_value.setToolTip("Checking GitHub for tool updates...")

        # Library Size Stats
        if hasattr(self, "library_size_value") and self.library_size_value:
            if hasattr(self, "game_manager") and self.game_manager:
                stats = self.game_manager.get_library_stats()
                total_games = stats.get("total_games", 0)
                total_bytes = stats.get("total_size", 0)
                total_gb = total_bytes / 1_073_741_824
                self.library_size_value.setText(f"{total_gb:.1f} GB ({total_games} games)")

    def rotate_quote(self):
        if not hasattr(self, "quote_label") or not self.quote_label:
            return
        current_text = self.quote_label.text()
        available_quotes = [q for q in self.quotes if q[0] != current_text]
        if available_quotes:
            import random
            quote, source = random.choice(available_quotes)
            self.quote_label.setText(quote)
            self.quote_source_label.setText(f"— {source}")

    def check_steamdb_status(self) -> None:
        """Asynchronously verify that Byparr is running and SteamDB Cloudflare bypass works."""
        if getattr(self, "_steamdb_check_running", False):
            return

        try:
            from core.steamdb_scraper import ByparrManager
            if not ByparrManager.find_byparr_dir():
                if hasattr(self, "steamdb_container"):
                    self.steamdb_container.setVisible(False)
                return
        except Exception:
            return

        self._steamdb_check_running = True
        if hasattr(self, "steamdb_status_value") and self.steamdb_status_value:
            self.steamdb_status_value.setText("Checking...")
            self.steamdb_status_value.setStyleSheet(self._get_status_style("neutral"))
            self.steamdb_status_value.setToolTip("Verifying Byparr solver and SteamDB connection...")

        def _worker():
            try:
                from core.steamdb_scraper import ByparrManager
                if not ByparrManager.find_byparr_dir():
                    self._main_thread_callable.emit(lambda: self._on_steamdb_status_result(False))
                    return
                ok = ByparrManager.verify_steamdb_bypass(timeout_seconds=30)
                logger.info(f"[SteamDB Visor] verify_steamdb_bypass result: {ok}")
                self._main_thread_callable.emit(lambda: self._on_steamdb_status_result(ok))
            except Exception as e:
                logger.debug(f"[SteamDB] Error during SteamDB status verification: {e}")
                self._main_thread_callable.emit(lambda: self._on_steamdb_status_result(False))
            finally:
                self._steamdb_check_running = False

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    def _on_steamdb_status_result(self, is_working: bool) -> None:
        """Display SteamDB visor item if Byparr is installed and working/ready."""
        self._steamdb_verified = is_working
        if hasattr(self, "steamdb_container") and hasattr(self, "steamdb_status_value"):
            try:
                from core.steamdb_scraper import ByparrManager
                has_byparr = ByparrManager.find_byparr_dir() is not None
            except Exception:
                has_byparr = False

            if not has_byparr:
                self.steamdb_container.setVisible(False)
                return

            self.steamdb_container.setVisible(True)
            if is_working:
                self.steamdb_status_value.setText("Ready")
                self.steamdb_status_value.setStyleSheet(self._get_status_style("success"))
                self.steamdb_status_value.setToolTip(
                    "SteamDB: Cloudflare Turnstile bypass active via Byparr\n"
                    "Version history, patch notes & depot enrichment enabled."
                )
                logger.info("[SteamDB Visor] SteamDB status updated to: Ready")
            else:
                self.steamdb_status_value.setText("Offline")
                self.steamdb_status_value.setStyleSheet(self._get_status_style("neutral"))
                self.steamdb_status_value.setToolTip(
                    "SteamDB: Solver is currently offline.\n"
                    "It will start automatically when version history or builds are accessed."
                )
                logger.info("[SteamDB Visor] SteamDB status updated to: Offline")

    def refresh_hubcap_stats(self, force: bool = False) -> None:
        """Fetch user statistics from Hubcap API asynchronously."""
        now = time.time()
        last_refresh = getattr(self, "_last_hubcap_refresh_time", 0.0)
        if not force and (now - last_refresh < 30.0):
            logger.debug("[Hubcap] Skipping stats refresh - throttled (called <30s ago)")
            return
        self._last_hubcap_refresh_time = now

        # Also refresh Steam and SLS status locally
        self.refresh_system_status()

        # Trigger background network checks if offline/connecting
        current_hubcap = self.hubcap_conn_value.text() if hasattr(self, "hubcap_conn_value") else "Connecting..."
        current_steam = self.steam_conn_value.text() if hasattr(self, "steam_conn_value") else "Connecting..."
        if current_hubcap in ("Connecting...", "Offline") or current_steam in ("Connecting...", "Offline"):
            self._check_network_connections_async()

        if not self.stats_task_runner:
            self.stats_task_runner = TaskRunner(self)
        
        # Set stats text to loading
        if hasattr(self, "hubcap_api_value") and self.hubcap_api_value:
            self.hubcap_api_value.setText("Loading...")
        if hasattr(self, "active_hubcap_label") and self.active_hubcap_label:
            self.active_hubcap_label.setText("Hubcap: Loading...")

        worker = self.stats_task_runner.run(get_all_hubcap_stats)
        worker.finished.connect(self._on_user_stats_loaded)
        worker.error.connect(self._on_user_stats_error)

    def _check_network_connections_async(self) -> None:
        """Run connection check once in background thread."""
        def run_check():
            try:
                from utils.network_status import run_connection_check
                steam_ok, hubcap_ok, hubcap_mode = run_connection_check()
            except Exception as e:
                logger.error(f"Error running network connection check: {e}")
                steam_ok, hubcap_ok, hubcap_mode = False, False, "Offline"

            # Update values thread-safely via QMetaObject.invokeMethod
            QMetaObject.invokeMethod(
                self,
                "_on_connections_checked",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(bool, steam_ok),
                Q_ARG(bool, hubcap_ok),
                Q_ARG(str, hubcap_mode)
            )

        import threading
        threading.Thread(target=run_check, daemon=True).start()

    @pyqtSlot(bool, bool, str)
    def _on_connections_checked(self, steam_ok: bool, hubcap_ok: bool, hubcap_mode: str) -> None:
        """Called thread-safely when background connection check completes."""
        # 1. Update Hubcap connection label
        if hasattr(self, "hubcap_conn_value") and self.hubcap_conn_value:
            if hubcap_ok:
                lbl = f"Online ({hubcap_mode})" if hubcap_mode and hubcap_mode not in ("Online", "Direct") else "Online"
                if hubcap_mode == "Tor":
                    lbl = "Online (Tor)"
                elif hubcap_mode == "Wire":
                    lbl = "Online (Wire)"
                elif hubcap_mode == "DoH":
                    lbl = "Online (DoH)"
                elif hubcap_mode == "Direct":
                    lbl = "Online (Direct)"
                self.hubcap_conn_value.setText(lbl)
                self.hubcap_conn_value.setStyleSheet(self._get_status_style("success"))
            else:
                self.hubcap_conn_value.setText("Offline")
                self.hubcap_conn_value.setStyleSheet(self._get_status_style("error"))

        # If online and tool update check is in checking/offline state, trigger check
        if (steam_ok or hubcap_ok) and getattr(self, "_tool_update_status", "checking") in ("checking", "offline"):
            if not getattr(self, "_tool_update_check_running", False):
                self.check_tool_updates()



    def _on_user_stats_loaded(self, result: dict) -> None:
        """Handle async hubcap stats load success."""
        if not isinstance(result, dict):
            return

        stats = result.get("user_stats", {})
        gen_usage = result.get("gen_usage", {})

        if not isinstance(stats, dict) or "error" in stats:
            err_msg = stats.get("error", "Unknown error") if isinstance(stats, dict) else "Invalid response"
            logger.warning(f"Failed to load Hubcap user stats: {err_msg}")
            val = "No Key" if "key is not set" in err_msg.lower() else "Error"
            if hasattr(self, "hubcap_api_value") and self.hubcap_api_value:
                self.hubcap_api_value.setText(val)
            if hasattr(self, "active_hubcap_label") and self.active_hubcap_label:
                self.active_hubcap_label.setText(f"Hubcap: {val}")
            return

        # 1. Daily usage (Manifests)
        usage = stats.get("daily_usage", 0)
        limit = stats.get("daily_limit", 55)

        # 2. Single depot generation
        single_info = gen_usage.get("single", {}) if isinstance(gen_usage, dict) else {}
        s_usage = single_info.get("usage", 0)
        s_limit = single_info.get("limit", 1500)

        # Key Expiry
        expires_str = stats.get("api_key_expires_at")
        if expires_str:
            if expires_str.endswith('Z'):
                expires_str = expires_str[:-1] + '+00:00'
            try:
                expires_at = datetime.fromisoformat(expires_str)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                delta = expires_at - now
                days = delta.days
                if days < 0:
                    expiry_text = "Expired"
                elif days == 0:
                    expiry_text = "Expires today"
                else:
                    expiry_text = f"{days}d"
            except Exception as e:
                logger.error(f"Failed to parse expiry date '{expires_str}': {e}")
                expiry_text = "Unknown"
        else:
            expiry_text = "Never"

        quota_str = f"API: {usage}/{limit} | Single: {s_usage}/{s_limit}"
        tooltip_str = (
            f"Hubcap API Quotas & Limits:\n"
            f"• API / Daily Manifests: {usage} / {limit}\n"
            f"• Single Depot Generations: {s_usage} / {s_limit}\n"
            f"• API Key Expiry: {expiry_text}"
        )

        if hasattr(self, "hubcap_api_value") and self.hubcap_api_value:
            self.hubcap_api_value.setText(quota_str)
            self.hubcap_api_value.setToolTip(tooltip_str)
        if hasattr(self, "active_hubcap_label") and self.active_hubcap_label:
            self.active_hubcap_label.setText(f"Hubcap: {quota_str}")
            self.active_hubcap_label.setToolTip(tooltip_str)

    def _on_user_stats_error(self, err_tuple: tuple) -> None:
        """Handle async hubcap stats load error."""
        logger.error(f"Async user stats load failed: {err_tuple[1]}")
        if hasattr(self, "hubcap_api_value") and self.hubcap_api_value:
            self.hubcap_api_value.setText("Error")
        if hasattr(self, "active_hubcap_label") and self.active_hubcap_label:
            self.active_hubcap_label.setText("Hubcap: Error")

    def run_update_all_flow(self) -> None:
        """Flow for updating all games that have update_available status."""
        # Guard: prevent re-entrancy if a cycle is already running
        if getattr(self, "_update_all_running", False):
            return
        # Guard: don't start Update All while an update check is still running
        gm = self.game_manager
        if gm and (getattr(gm, "manifest_check_task", None) is not None or getattr(gm, "manifest_check_runner", None) is not None):
            QMessageBox.information(self, "Update Check Running", "Please wait for the update check to finish before queuing updates.")
            return
        self._update_all_running = True

        if not self.game_manager:
            self._update_all_running = False
            return

        games = self.game_manager.get_all_games()
        updateable_games = []
        for g in games:
            if g.get("update_status") == "update_available":
                appid = str(g.get("appid", ""))
                if self.settings.value(f"exclude_from_update_all/{appid}", False, type=bool):
                    continue
                if self.settings.value(f"pin_build/{appid}", False, type=bool):
                    continue
                updateable_games.append(g)

        if not updateable_games:
            self._update_all_running = False
            QMessageBox.information(
                self,
                "No Updates Available",
                "All games in your library are up to date!",
            )
            return

        total = len(updateable_games)

        # --- Immediate feedback: update button before the thread even starts ---
        self._set_update_all_btn_preparing(0, total)

        # Enqueue all updates directly on a background thread (no intermediate dialog)
        import threading

        def _do_update_all():
            from pathlib import Path
            from core import morrenus_api as _api
            from utils.settings import get_settings
            from core.tasks.process_zip_task import ProcessZipTask
            from ui.dialogs.gamelibrary import format_game_display_name
            import json

            settings = get_settings()
            queued = 0
            skipped_names = []

            for idx, game_data in enumerate(updateable_games):
                appid = str(game_data.get("appid", "0"))
                name = format_game_display_name(game_data)
                update_status = game_data.get("update_status")
                self._set_update_all_btn_preparing(idx, total, current_name=name)
                logger.info(f"[Update All] Preparing {name} ({idx + 1}/{total})...")
                try:
                    local_path = None
                    branch = _api.get_selected_branch(appid)
                    fpath = _api.get_manifest_zip_path(appid, branch)

                    is_fresh = settings.value(f"manifest_is_fresh/{appid}", False, type=bool)
                    if fpath.exists() and (update_status != "update_available" or is_fresh):
                        local_path = str(fpath)

                    parsed_data = None
                    from managers.depot_key_manager import DepotKeyManager
                    dkm = DepotKeyManager()

                    # 1. Try Smart Update Path
                    if dkm.has_depot_keys(appid):
                        from core.tasks.smart_update_task import SmartUpdateTask
                        task = SmartUpdateTask(appid, name, branch=branch)

                        def on_finished(assembled):
                            nonlocal parsed_data
                            parsed_data = assembled

                        task.finished.connect(on_finished)
                        task.progress.connect(lambda msg: logger.info(f"[Update All] {msg}"))
                        try:
                            task.run()
                        except Exception as e:
                            logger.error(f"Smart update failed in Update All: {e}")

                        if parsed_data:
                            local_path = str(fpath)

                    # 2. Fallback to Classic Path if Smart failed or no keys
                    if not parsed_data:
                        # Ensure we have a valid classic zip (must contain .lua)
                        import zipfile
                        def has_lua(zp):
                            try:
                                with zipfile.ZipFile(zp, "r") as z:
                                    return any(f.endswith(".lua") for f in z.namelist())
                            except Exception:
                                return False

                        if not local_path or not has_lua(local_path):
                            logger.info(f"Update All: Fetching classic manifest for {name} (branch={branch})")
                            if local_path and Path(local_path).exists():
                                Path(local_path).unlink()  # Delete bad/lua-less zip
                            fpath_val, error = _api.download_manifest(appid, branch=branch)
                            if error or not fpath_val:
                                logger.warning(f"Update All: manifest download failed for {name}: {error}")
                                skipped_names.append(name)
                                continue
                            local_path = str(fpath_val)
                            settings.setValue(f"manifest_is_fresh/{appid}", True)

                        zip_task = ProcessZipTask()
                        try:
                            parsed_data = zip_task.run(local_path)
                        except Exception as e:
                            logger.error(f"Update All: Failed to process zip for {name}: {e}")
                            skipped_names.append(name)
                            continue

                    metadata = dict(game_data)
                    metadata.update({
                        "appid": appid,
                        "library_path": game_data.get("library_path"),
                        "install_path": game_data.get("install_path"),
                        "game_name": name,
                        "branch": branch,
                    })

                    if parsed_data and parsed_data.get("depots"):
                        depots = parsed_data.get("depots")
                        selected_depots = None
                        val = settings.value(f"depot_selection/{appid}", "", type=str)
                        should_prompt = True
                        prev_selected = None

                        if val:
                            try:
                                data = json.loads(val)
                                cached_selected = data.get("selected", [])
                                cached_all = data.get("all_available", [])
                                prev_selected = cached_selected
                                has_new_depot = any(d not in cached_all for d in depots)
                                if has_new_depot:
                                    # New depot added since last selection — clear cache and force re-prompt
                                    logger.info(f"Update All: new depot detected for {name} (appid={appid}), prompting user")
                                    settings.remove(f"depot_selection/{appid}")
                                else:
                                    selected_depots = [d for d in cached_selected if d in depots]
                                    if selected_depots:
                                        should_prompt = False
                            except Exception:
                                pass

                        if should_prompt:
                            auto_skip = settings.value("auto_skip_single_choice", False, type=bool)
                            if auto_skip and len(depots) == 1:
                                selected_depots = list(depots.keys())
                            else:
                                # Depot dialog must run on the main thread
                                from ui.dialogs.depotselection import DepotSelectionDialog
                                result_holder = [None]
                                done_event = threading.Event()

                                # Bind every loop-scoped value as a default
                                # argument. The emit below is a queued
                                # cross-thread signal, so if the 300ms wait below
                                # times out the callback can still run after this
                                # iteration has finished and the loop has moved
                                # on — a plain closure would then build the dialog
                                # from the *last* iteration's game.
                                def _show_depot_dialog(
                                    _appid=appid,
                                    _game_name=parsed_data.get("game_name", name),
                                    _depots=depots,
                                    _header_url=parsed_data.get("header_url"),
                                    _prev_selected=prev_selected,
                                    _missing_hubcap=parsed_data.get("missing_depots_from_hubcap"),
                                    _missing_info=parsed_data.get("missing_depots_info"),
                                    _refetched=parsed_data.get("refetched_depots"),
                                    _result_holder=result_holder,
                                    _done_event=done_event,
                                ):
                                    try:
                                        depot_dialog = DepotSelectionDialog(
                                            _appid,
                                            _game_name,
                                            _depots,
                                            _header_url,
                                            self,
                                            selected_depots=_prev_selected,
                                            is_single_depot=(len(_depots) == 1),
                                            missing_hubcap_depots=_missing_hubcap,
                                            missing_depots_info=_missing_info,
                                            refetched_depots=_refetched,
                                        )
                                        if depot_dialog.exec():
                                            _result_holder[0] = depot_dialog.get_selected_depots()
                                    except Exception as err:
                                        logger.error(
                                            f"Error displaying depot selection dialog for {_appid} "
                                            f"in Update All: {err}"
                                        )
                                    finally:
                                        _done_event.set()

                                self._main_thread_callable.emit(_show_depot_dialog)
                                # The dialog is modal and needs real user
                                # interaction, so this must wait far longer than
                                # a dispatch timeout. A short wait here returned
                                # before the user had chosen anything, which made
                                # the game look uncancellable-free but actually
                                # skipped it ("no depots selected") and left the
                                # dialog running against a stale iteration.
                                # Only a hard failure should give up early;
                                # otherwise wait for the user.
                                if not done_event.wait(timeout=DIALOG_WAIT_SECONDS):
                                    logger.warning(
                                        f"Update All: depot dialog for {appid} did not report back "
                                        f"within {DIALOG_WAIT_SECONDS}s; skipping this game"
                                    )
                                    selected_depots = None
                                else:
                                    selected_depots = result_holder[0]

                        if not selected_depots:
                            logger.info(f"Update All: skipping {name} — depot selection cancelled or no depots selected")
                            skipped_names.append(name)
                            continue

                        # Persist confirmed selection
                        try:
                            settings.setValue(
                                f"depot_selection/{appid}",
                                json.dumps({
                                    "selected": selected_depots,
                                    "all_available": list(depots.keys()),
                                    "descriptions": {d_id: depots.get(d_id, {}).get("desc", "") for d_id in selected_depots}
                                })
                            )
                        except Exception as e:
                            logger.warning(f"Failed to cache depot selection: {e}")

                        metadata["selected_depots_list"] = selected_depots

                    self.job_queue.add_job(local_path, metadata)
                    queued += 1
                    logger.info(f"Update All queued: {name}")

                    # Update button progress after each successful queue
                    self._set_update_all_btn_preparing(queued, total)

                except Exception as e:
                    logger.error(f"Update All failed for {name}: {e}", exc_info=True)
                    skipped_names.append(name)

            logger.info(f"Update All: queued {queued} of {total} games.")

            # Release guard and refresh UI
            self._update_all_running = False
            from PyQt6.QtCore import QMetaObject, Qt
            from PyQt6.QtCore import Q_ARG
            QMetaObject.invokeMethod(self, "update_dashboard_elements", Qt.ConnectionType.QueuedConnection)

            # Notify user about any skipped games
            if skipped_names and queued == 0:
                QMetaObject.invokeMethod(
                    self,
                    "_show_update_all_skip_notice",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, "\n".join(f"• {n}" for n in skipped_names)),
                    Q_ARG(bool, True),
                )
            elif skipped_names:
                QMetaObject.invokeMethod(
                    self,
                    "_show_update_all_skip_notice",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, "\n".join(f"• {n}" for n in skipped_names)),
                    Q_ARG(bool, False),
                )

        threading.Thread(target=_do_update_all, daemon=True).start()

    @pyqtSlot(object)
    def _run_on_main_thread(self, fn) -> None:
        """Slot to execute a callable on the main thread (connected via _main_thread_callable signal)."""
        try:
            fn()
        except Exception as e:
            logger.error(f"Error executing on main thread: {e}", exc_info=True)

    def _set_update_all_btn_preparing(self, done: int, total: int, current_name: str = "") -> None:
        """Thread-safe: update the Update All button to show preparation progress."""
        import threading
        if threading.current_thread() is not threading.main_thread():
            QMetaObject.invokeMethod(
                self,
                "_set_update_all_btn_preparing_slot",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(int, done),
                Q_ARG(int, total),
                Q_ARG(str, current_name),
            )
        else:
            self._set_update_all_btn_preparing_slot(done, total, current_name)

    @pyqtSlot(int, int)
    @pyqtSlot(int, int, str)
    def _set_update_all_btn_preparing_slot(self, done: int, total: int, current_name: str = "") -> None:
        """Main-thread slot: update the Update All button text to show preparation progress."""
        try:
            ui = getattr(self, "ui_state", None)
            btn = getattr(ui, "update_all_btn", None) if ui else None
            if btn:
                if done < total:
                    if current_name:
                        short_name = current_name if len(current_name) <= 16 else f"{current_name[:13]}..."
                        btn.setText(f" Preparing {short_name} ({done + 1}/{total})...")
                    else:
                        btn.setText(f" Preparing... ({done}/{total})")
                else:
                    btn.setText(f" Queued ({done}/{total})")
                btn.setEnabled(False)
                btn.setToolTip(f"Preparing updates... ({done} of {total} ready)")
        except Exception:
            pass

    @pyqtSlot(str, bool)
    def _show_update_all_skip_notice(self, names_text: str, all_skipped: bool) -> None:
        """Show a non-blocking notice when some games were skipped in Update All."""
        if all_skipped:
            QMessageBox.warning(
                self,
                "Update All — Manual Action Required",
                f"All pending updates were skipped because they require manual depot selection.\n\n"
                f"Please update these games individually:\n{names_text}",
            )
        else:
            QMessageBox.information(
                self,
                "Update All — Some Games Skipped",
                f"Updates were queued, but the following game(s) need manual depot selection:\n\n"
                f"{names_text}\n\nOpen them individually to install the update.",
            )

    @pyqtSlot()
    def update_dashboard_elements(self) -> None:
        """Dynamically update dashboard elements and floating action button."""
        if hasattr(self, "simplified_terminal") and self.simplified_terminal:
            self.simplified_terminal.update_stats()
        self.refresh_system_status()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        import os
        if not event.mimeData().hasUrls():
            return

        urls = event.mimeData().urls()
        acceptable = False
        for url in urls:
            if url.isLocalFile():
                path = url.toLocalFile()
                if (
                    path.lower().endswith(".zip")
                    or path.lower().endswith(".lua")
                    or path.lower().endswith(".manifest")
                    or os.path.isdir(path)
                ):
                    acceptable = True
                    break

        if acceptable:
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        import os
        import tempfile
        import zipfile
        
        urls = event.mimeData().urls()
        
        zips_to_queue = []
        files_to_zip = []
        
        for url in urls:
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if path.lower().endswith(".zip"):
                zips_to_queue.append(path)
            elif path.lower().endswith(".lua") or path.lower().endswith(".manifest"):
                files_to_zip.append(path)
            elif os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for file in files:
                        if file.lower().endswith(".lua") or file.lower().endswith(".manifest"):
                            files_to_zip.append(os.path.join(root, file))

        if files_to_zip:
            try:
                temp_fd, temp_path = tempfile.mkstemp(suffix=".zip")
                os.close(temp_fd)
                with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zip_ref:
                    added_names = set()
                    for fpath in files_to_zip:
                        bname = os.path.basename(fpath)
                        if bname not in added_names:
                            zip_ref.write(fpath, arcname=bname)
                            added_names.add(bname)
                zips_to_queue.append(temp_path)
                logger.info(f"Packaged {len(files_to_zip)} loose files into temporary zip: {temp_path}")
            except Exception as e:
                logger.error(f"Failed to create temporary zip for dropped files: {e}")
                QMessageBox.critical(self, "Error", f"Failed to package dropped files: {e}")

        if not zips_to_queue:
            return

        from ui.dialogs.zip_confirm_dialog import ZipImportConfirmationDialog
        from PyQt6.QtWidgets import QDialog

        logger.info(f"Inspecting {len(zips_to_queue)} file(s) for import via drag-drop.")
        for job_path in zips_to_queue:
            dlg = ZipImportConfirmationDialog(
                parent=self,
                zip_path=job_path,
                accent_color=self.accent_color,
            )
            if dlg.exec() == QDialog.DialogCode.Accepted:
                meta = dlg.get_metadata()
                logger.info(f"User confirmed import for {job_path} with metadata: {meta}")
                self.job_queue.add_job(job_path, metadata=meta)
            else:
                logger.info(f"User cancelled import for {job_path}")

    def closeEvent(self, event) -> None:
        """Handle application shutdown."""
        try:
            if hasattr(self, "web_server_manager") and self.web_server_manager:
                self.web_server_manager.stop()
            try:
                from utils.isp_bypass import TorManager
                TorManager.stop_tor()
            except Exception:
                pass
            try:
                from core.steamdb_scraper import ByparrManager
                ByparrManager.stop()
            except Exception:
                pass
            # Signal SLS background retry workers to stop before teardown
            try:
                from utils.slssteam_integration import register_shutdown
                register_shutdown()
            except Exception:
                pass
            from utils.update_status_cache import get_update_cache
            get_update_cache().save()  # Force synchronous save of status cache before exit
            MainWindow._cleanup_logging()
            self.task_manager.cleanup()
            self.job_queue.clear()
            self.game_manager.cleanup()
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

        super().closeEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        from PyQt6.QtGui import QPainter, QLinearGradient, QPixmap
        from PyQt6.QtCore import Qt
        from utils.paths import Paths

        settings = self.settings
        preset = settings.value("material_preset", "ocean", type=str) if settings else "ocean"
        custom_bg = settings.value("theme_background_image", "", type=str) if settings else ""

        painter = QPainter(self)
        rect = self.rect()

        bg_path_to_draw = None
        if custom_bg and os.path.exists(custom_bg):
            bg_path_to_draw = custom_bg
        elif preset == "halloween":
            bg_candidates = [
                os.path.expanduser("~/Pictures/halloween-scary-zombie-horror-graveyard-background-vector.jpg"),
                Paths.resource("halloween_bg.jpg"),
            ]
            for p in bg_candidates:
                if p and os.path.exists(str(p)):
                    bg_path_to_draw = str(p)
                    break

        if bg_path_to_draw:
            if not hasattr(self, "_active_bg_path") or self._active_bg_path != bg_path_to_draw or not hasattr(self, "_custom_bg_pixmap") or self._custom_bg_pixmap is None:
                self._active_bg_path = bg_path_to_draw
                self._custom_bg_pixmap = QPixmap(bg_path_to_draw)

            # Deep dark background base
            base_col = QColor(self.background_color or "#0d0d0f")
            painter.fillRect(rect, base_col)

            if hasattr(self, "_custom_bg_pixmap") and self._custom_bg_pixmap and not self._custom_bg_pixmap.isNull():
                scaled = self._custom_bg_pixmap.scaled(
                    rect.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = (rect.width() - scaled.width()) // 2
                y = (rect.height() - scaled.height()) // 2

                # Atmospheric opacity
                painter.setOpacity(0.50)
                painter.drawPixmap(x, y, scaled)
                painter.setOpacity(1.0)

                # Vertical gradient overlay: strong top vignette, gradually decreasing towards bottom
                gradient = QLinearGradient(0, 0, 0, rect.height())
                gradient.setColorAt(0.0, QColor(base_col.red(), base_col.green(), base_col.blue(), 235))
                gradient.setColorAt(0.18, QColor(base_col.red(), base_col.green(), base_col.blue(), 175))
                gradient.setColorAt(0.40, QColor(base_col.red(), base_col.green(), base_col.blue(), 115))
                gradient.setColorAt(0.65, QColor(base_col.red(), base_col.green(), base_col.blue(), 65))
                gradient.setColorAt(0.85, QColor(base_col.red(), base_col.green(), base_col.blue(), 35))
                gradient.setColorAt(1.0, QColor(base_col.red(), base_col.green(), base_col.blue(), 20))
                painter.fillRect(rect, gradient)
        else:
            bg_col = QColor(self.background_color or "#000000")
            painter.fillRect(rect, bg_col)

    def reposition_titlebar(self, position: str) -> None:
        """Dynamically reposition the titlebar without restart."""
        if not hasattr(self, "bottom_titlebar") or not self.bottom_titlebar:
            return

        self.layout.removeWidget(self.bottom_titlebar)
        self.bottom_titlebar.setParent(None)

        if position == "top":
            self.layout.insertWidget(0, self.bottom_titlebar)
        else:
            self.layout.addWidget(self.bottom_titlebar)

        self.titlebar_position = position
        logger.info(f"Titlebar repositioned to: {position}")

    def check_tool_updates(self) -> None:
        """Start a background thread to check for tool self-updates from GitHub."""
        import threading
        import urllib.request

        # Prevent multiple concurrent checks from running at the same time
        if self._tool_update_check_running:
            logger.debug("Tool update check already in progress, skipping.")
            return
        self._tool_update_check_running = True
        self._tool_update_status = "checking"
        self.refresh_system_status()

        def _parse_version(v_str: str) -> tuple:
            import re as _re
            v_str = v_str.lstrip('v').strip()
            m = _re.match(r'^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[.-]?(dev|alpha|beta|rc|hotfix|testing|canary)(\d*)|\-([a-zA-Z0-9.]+))?$', v_str, _re.IGNORECASE)
            if m:
                major = int(m.group(1) or 0)
                minor = int(m.group(2) or 0)
                patch = int(m.group(3) or 0)
                pre_type = m.group(4) or ""
                pre_num_str = m.group(5) or ""
                pre_extra = m.group(6) or ""
                pre_weights = {"hotfix": 1, "rc": -1, "beta": -2, "alpha": -3, "dev": -4, "testing": -4, "canary": -5}
                if pre_type:
                    pre_val = pre_weights.get(pre_type.lower(), 1 if "hotfix" in pre_type.lower() else -4)
                    pre_num = int(pre_num_str) if pre_num_str else 1
                elif pre_extra:
                    if "hotfix" in pre_extra.lower():
                        hm = _re.search(r'\d+', pre_extra)
                        pre_val = 1
                        pre_num = int(hm.group(0)) if hm else 1
                    else:
                        pre_val = -4
                        pre_num = 0
                else:
                    pre_val = 0
                    pre_num = 0
                return (major, minor, patch, pre_val, pre_num)
            return (0, 0, 0, 0, 0)

        def _check_sync():
            status = "offline"
            remote_clean = None
            try:
                import json
                local_clean = _extract_semver(app_version)

                is_local_canary = (
                    "canary" in local_clean.lower()
                    or "testing" in local_clean.lower()
                    or local_clean.startswith("3.")
                )

                # 1. Primary check: GitHub Releases API (detects full releases + pre-releases with AppImage)
                try:
                    api_url = "https://api.github.com/repos/niwia/ASSella/releases"
                    req = urllib.request.Request(
                        api_url,
                        headers={"User-Agent": "ASSella-Updater", "Accept": "application/vnd.github+json"}
                    )
                    with urllib.request.urlopen(req, timeout=10) as response:
                        releases = json.loads(response.read().decode("utf-8"))
                        for r in releases:
                            tag = r.get("tag_name", "").strip()
                            is_remote_canary = (
                                "canary" in tag.lower()
                                or "testing" in tag.lower()
                                or tag.lstrip("v").startswith("3.")
                            )
                            # Strict channel isolation:
                            # Stable/Beta (2.x) must NEVER pick up Canary/Testing (3.x)
                            # Canary (3.x) must NEVER pick up Stable/Beta (2.x)
                            if is_local_canary != is_remote_canary:
                                continue

                            assets = [a.get("name", "") for a in r.get("assets", [])]
                            if is_local_canary:
                                has_appimage = any(a.lower().endswith(".appimage") for a in assets)
                            else:
                                has_appimage = any(a == "ASSella.AppImage" or (a.endswith(".AppImage") and "canary" not in a.lower()) for a in assets)

                            if has_appimage:
                                remote_clean = _extract_semver(tag)
                                break
                except Exception as api_err:
                    logger.debug(f"GitHub Releases API check error: {api_err}")

                # 2. Fallback check: raw version file on GitHub branch
                if not remote_clean:
                    if is_local_canary:
                        branch = "canary"
                    elif "alpha" in local_clean.lower():
                        branch = "alpha"
                    elif any(x in local_clean.lower() for x in ("beta", "rc")):
                        branch = "beta"
                    else:
                        branch = "main"
                    url = f"https://raw.githubusercontent.com/niwia/ASSella/{branch}/src/res/version"
                    logger.info(f"Checking for tool updates from raw branch: {branch}")
                    req = urllib.request.Request(
                        url,
                        headers={"User-Agent": "ASSella-Updater"}
                    )
                    with urllib.request.urlopen(req, timeout=10) as response:
                        remote_raw = response.read().decode("utf-8").strip()
                        remote_clean = _extract_semver(remote_raw)

                logger.info(
                    f"Tool update check: remote='{remote_clean}', local='{local_clean}'"
                )
                if remote_clean:
                    # Only notify update if remote is strictly newer than local
                    if _parse_version(remote_clean) > _parse_version(local_clean):
                        logger.info(f"Tool update available: {remote_clean} (current: {local_clean})")
                        status = "update_available"
                    else:
                        logger.info(f"Tool is up to date ({local_clean})")
                        status = "up_to_date"
                else:
                    status = "offline"
            except Exception as e:
                logger.warning(f"Failed to check tool updates from GitHub: {e}")
                status = "offline"
            finally:
                self._tool_update_check_running = False
                QMetaObject.invokeMethod(
                    self,
                    "_on_tool_update_result",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, status),
                    Q_ARG(str, remote_clean or "")
                )

        t = threading.Thread(target=_check_sync, daemon=True)
        t.start()

    @pyqtSlot(str, str)
    def _on_tool_update_result(self, status: str, remote_version: str = "") -> None:
        """Slot triggered when tool self-update check finishes."""
        self._tool_update_status = status
        if status == "update_available":
            self._latest_remote_version = remote_version
            self._tool_update_available_flag = True
        else:
            self._tool_update_available_flag = False
        self._update_tool_update_visibility()
        self.refresh_system_status()

    @pyqtSlot(str)
    def _on_tool_update_available(self, remote_version: str = "") -> None:
        """Slot triggered when a tool update is available (backward-compatibility)."""
        self._on_tool_update_result("update_available", remote_version)

    def _update_tool_update_visibility(self) -> None:
        """Only show the update label when we are on the main/idle screen (layout index 0)."""
        if hasattr(self, "bottom_titlebar") and self.bottom_titlebar:
            show = getattr(self, "_tool_update_available_flag", False) and getattr(self, "simplified_terminal", None) and self.simplified_terminal.layout.currentIndex() == 0
            self.bottom_titlebar.show_update_indicator(show)

    def run_self_update(self) -> None:
        """Install update via AppUpdateManager (delta first, full download fallback)."""
        if self.app_update_manager:
            self.app_update_manager.run_self_update()

    @pyqtSlot()
    def _on_update_success(self) -> None:
        QMessageBox.information(
            self,
            "Update Installed",
            "The update has been downloaded and installed successfully.\n\n"
            "Please restart ASSella to use the new version.",
        )

    @pyqtSlot(str)
    def _on_update_failed(self, error_msg: str) -> None:
        QMessageBox.warning(
            self,
            "Update Failed",
            f"Failed to download or apply the update.\n\nError: {error_msg}"
        )

    @staticmethod
    def _cleanup_logging() -> None:
        """Clean up logging system."""
        try:
            atexit.unregister(logging.shutdown)
            logging.getLogger().removeHandler(qt_log_handler)
            qt_log_handler.close()
            logger.info("QtLogHandler removed and atexit hook unregistered.")
            logging.shutdown()
        except Exception as e:
            print(f"Error during custom logger shutdown: {e}")
