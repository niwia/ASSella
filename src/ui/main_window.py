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
    QFontMetrics,
)
from PyQt6.QtWidgets import (
    QLabel,
    QMainWindow,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
    QPushButton,
)
from ui.dialogs.themed_message_box import ThemedMessageBox

from managers.app_update_manager import AppUpdateManager
from managers.game_manager import GameManager
from managers.job_queue_manager import JobQueueManager
from managers.task_manager import TaskManager
from managers.ui_state_manager import UIStateManager
from ui.bottom_titlebar import BottomTitleBar
from ui.widgets import (
    ResizeHandle,
    SimplifiedTerminalWidget,
)
from ui.widgets.elided_label import ElidedLabel
from utils.logger import qt_log_handler
from utils.paths import Paths
from queue import Queue
from utils.web_server import WebServerManager, get_local_ip
from utils.settings import get_settings
from utils.task_runner import TaskRunner
from datetime import datetime, timezone
from utils.version import app_version, check_for_updates

logger = logging.getLogger(__name__)

# How long Update All waits for the user to answer a depot-selection dialog
# before giving up on that game. Must comfortably exceed human reaction time —
# a short wait returns while the dialog is still open, which silently skips the
# game while leaving a stale dialog on screen.
DIALOG_WAIT_SECONDS = 300


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
            ('FINISH HIM!', 'Mortal Kombat'),
            ("It's dangerous to go alone, take this!", 'The Legend of Zelda'),
            ('Thank you, Mario! But our Princess is in another castle!', 'Super Mario'),
            ('Do a barrel roll!', 'Star Fox 64'),
            ('Hey! Look! Listen!', 'The Legend of Zelda: Ocarina of Time'),
            ('I used to be an adventurer like you until I took an arrow to the knee.', 'The Elder Scrolls V: Skyrim'),
            ('You Have Died', 'Dark Souls'),
            ('Waka Waka Waka', 'Pacman'),
            ('Stay awhile and listen!', 'Diablo 2'),
            ('The cake is a lie', 'Portal'),
            ('You require more vespene gas.', 'StarCraft'),
            ('Snake? Snake? SNAKE!', 'Metal Gear Solid'),
            ('Praise the sun!', 'Dark Souls'),
            ("It's a-me, Mario!", "Assassin's Creed II"),
            ('Get over here!', 'Mortal Kombat'),
            ('No Russian.', 'Call of Duty: Modern Warfare 2'),
            ('War...war never changes', 'Fallout'),
            ('A man chooses; a slave obeys.', 'Bioshock'),
            ('Would you kindly...', 'Bioshock'),
            ('The right man in the wrong place can make all the difference in the world.', 'Half-Life 2'),
            ("You've met with a terrible fate, haven't you?", "The Legend of Zelda: Majora's Mask"),
            ("It's time to kick ass and chew bubble gum...and I'm all outta gum.", 'Duke Nukem'),
            ('All your base are belong to us.', 'Zero Wing'),
            ('Stop right there, criminal scum!', 'The Elder Scrolls IV: Oblivion'),
            ('Endure and survive.', 'The Last of Us'),
            ('Hope is what makes us strong. It is why we are here. It is what we fight with when all else is lost.', 'God of War 3'),
            ('A hero need not speak. When he is gone, the world will speak for him', 'Halo'),
            ('Good! Now we can fight as warriors. Hand-to-hand, it is the basis of all combat. Only a fool trusts his life to a weapon.', 'Metal Gear Solid'),
            ('The world fears the inevitable plummet into the abyss. Watch for that moment and when it comes, do not hesitate to leap. It is only when you fall that you learn whether you can fly.', 'Dragon Age: Origins'),
            ('What is bravery, without a dash of recklessness?', 'Dark Souls'),
            ("I don't need a weapon; my friends are my power!", 'Kingdom Hearts'),
            ('Kept you waiting, huh?', 'Metal Gear Solid 5: Ground Zeroes'),
            ("Don't make a girl a promise if you know you can't keep it.", 'Halo 3'),
            ('Now is the time to choose! Die and be free of pain, or live and fight your sorrow. Now is the time to shape your stories! Your fate is in your hands.', 'Final Fantasy X'),
            ("The thing about happiness is that you only know you had it when it's gone...", 'Fallout 4'),
            ("Life isn't just about passing on your genes... Building the future and keeping the past alive are one and the same thing.", 'Metal Gear Solid 2: Sons of Liberty'),
            ('Men are but flesh and blood. They know their doom, but not the hour.', 'The Elder Scrolls: Oblivion'),
            ("Dreams have a nasty habit of going bad when you're not looking.", 'Max Payne'),
            ("Right and wrong are not what separate us and our enemies... There's no good or bad side. Just two sides holding different views.", 'Final Fantasy VIII'),
            ('At the end of the day, as long as there are two people left on the planet, someone is gonna want someone dead.', 'Team Fortress 2'),
            ("My past is not a memory. It's a force at my back... What's past is prologue.", 'Metroid: The Other M'),
            ('Our world... has trained us to be miserly with forgiveness...', 'Braid'),
            ("Some trees flourish, others die... Ain't nothing fair.", 'Red Dead Redemption'),
            ("You can't break a man the way you break a dog or a horse... The harder you beat a man, the taller he stands.", 'Far Cry 2'),
            ('War is where the young and stupid are tricked by the old and bitter into killing each other.', 'Grand Theft Auto IV'),
            ('Why walk when you can ride?', 'The Elder Scrolls III: Morrowind'),
            ("You mustn't allow yourself to be chained to fate... Human beings can choose the kind of life that they want to live.", 'Metal Gear Solid'),
            ('All men dream – but not equally...', 'Uncharted 3'),
            ('There are two ways of arguing with a woman, and neither one works.', 'Red Dead Redemption'),
            ('Nothing is true, everything is permitted.', "Assassin's Creed franchise"),
            ('What is a man but the sum of his memories? We are the stories we live, the tales we tell ourselves.', "Assassin's Creed: Revelations"),
            ('Only a mind free of impediments is capable of grasping the chaotic beauty of the world.', "Assassin's Creed"),
            ('You gave them the one thing that was stolen from them... a family.', 'Bioshock'),
            ('Grass grows, birds fly, the sun shines, and brother, I hurt people.', 'Team Fortress 2'),
            ("The healthy human mind doesn't wake up in the morning thinking this is its last day on Earth... To know you're close to the end is a kind of freedom.", 'Call of Duty: Modern Warfare'),
            ('What is a drop of rain, compared to the storm? What is a thought, compared to the mind?', 'System Shock 2'),
            ("So, I guess I'd become what they wanted me to be, a killer... I wouldn't know right from wrong if one of them was helping the poor and the other was banging my sister...", 'Max Payne 3'),
            ("I am Andrew Ryan, and I'm here to ask you a question... I chose the impossible. I chose... Rapture.", 'Bioshock'),
            ('They were all dead... The final gunshot... And then it was over.', 'Max Payne'),
            ("I've struggled a long time with survivin', but no matter what you have to find something to fight for.", 'The Last of Us'),
            ("I raised you, and loved you... All that's left for you to take is my life.", 'Metal Gear Solid 3: Snake Eater'),
            ("Boss... you were right... It's about doing our best to leave the world... the way it is.", 'Metal Gear Solid IV'),
            ('Stand in the ashes of a trillion dead souls and ask the ghosts if honor matters. Their silence is your answer.', 'Mass Effect 3'),
            ("The truth, Walker, is that you're here because you wanted to feel like something you're not: A hero.", 'Spec Ops: The Line'),
            ('Did I ever tell you the definition of insanity?', 'Far Cry 3'),
            ('Force answers force, war breeds war, and death only brings death...', 'Metro 2033'),
            ("You want to know something funny? Even after everything you've done, I would have saved you.", 'Batman: Arkham City'),
            ("We're not tools of the government or anyone else... I always fought for what I believed in.", 'Metal Gear Solid'),
            ('Most test subjects do experience some cognitive deterioration... try to hold onto that feeling...', 'Portal 2'),
            ('It is a good life we lead, brother... And may it never change us.', "Assassin's Creed II"),
            ('Do you like hurting other people?', 'Hotline Miami'),
            ('Tell me Bats... Me, in a thong?', 'Batman: Arkham Asylum'),
            ('The sun went down with practiced bravado... Front row center.', 'Max Payne'),
            ('A famous explorer once said that the extraordinary is in what we do, not who we are.', 'Tomb Raider'),
            ("I'm Commander Shepard, and this is my favorite store on the Citadel!", 'Mass Effect 2'),
            ('What is a man? A Miserable little pile of secrets!', 'Castlevania: Symphony of the Night'),
            ('The right man in the wrong place...', 'Half-Life 2'),
            ("Good men mean well. We just don't always end up doing well.", 'Dead Space 3'),
            ('Why walk when you can ride?', 'The Elder Scrolls III: Morrowind'),
            ('You have died of dysentery.', 'Oregon Trail'),
            ('Men are but flesh and blood...', 'Oblivion'),
            ('Time passes, people move...', 'Ocarina of Time'),
            ('At the end of the day, as long as there are two people left...', 'Team Fortress 2'),
            ('The only thing that can defeat power, is more power.', 'Resident Evil: Umbrella Chronicles'),
            ("Someday you're bound to lose everything. Everybody around you will be gone.", 'Final Fantasy VIII'),
            ('Are you a bad enough dude to rescue the president?', 'Bad Dudes'),
            ('Shadow and light are two sides of the same coin. One cannot exist without the other.', 'Twilight Princess'),
            ('DEATH IS A PREFERABLE ALTERNATIVE TO COMMUNISM', 'Fallout 3'),
            ('...it is merely an observation on the nature of reality... nothing is true... everything is permitted.', 'AC: Revelations'),
            ("It ain't no secret I didn't get these scars falling over in church.", 'Red Dead Redemption'),
            ('What is better – to be born good, or to overcome your evil nature through great effort?', 'Skyrim'),
            ('I am Andrew Ryan... I chose the impossible. I chose... Rapture.', 'Bioshock'),
            ('Love is just a chemical. We give it meaning by choice.', 'Bioshock 2'),
            ('War has changed', 'Metal Gear Solid IV'),
            ('If our lives are already written, it would take a courageous man to change the script.', 'Alan Wake'),
            ('Had to be me... Someone else might have got it wrong.', 'Mass Effect 3'),
            ('[silence]', 'The Legend of Zelda'),
            ('NOTHING IS MORE BADASS THAN TREATING A WOMAN WITH RESPECT!', 'Borderlands 2'),
            ('Frostmourne hungers', 'WarCraft III'),
            ('Kept you waiting, huh?', 'Metal Gear Solid 5: Ground Zeroes'),
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
        # Plain-Python mirror of the progress widgets, published for the web UI.
        # The web server answers requests on its own threads and must never read
        # QWidget state, so the GUI thread refreshes this snapshot instead.
        self._web_status_snapshot = {
            "progress": 0,
            "progress_visible": False,
            "speed": "",
            "speed_visible": False,
        }
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
        self._setup_system_status_timer()
        self._setup_gui_stall_watchdog()
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

        # Warm up Byparr and verify SteamDB Cloudflare bypass in the background.
        # Deferred on purpose: Byparr spawns a headless Chromium, which costs
        # seconds of CPU and RAM. Doing that while the library scan and the
        # update check are still running is what made the window feel stuck
        # right after boot.
        self._schedule_steamdb_warmup()
        
        def run_boot_checks():
            try:
                from utils.slssteam_integration import check_slssteam_binary_is_latest
                check_slssteam_binary_is_latest()
            except Exception as e:
                logger.debug(f"SLS binary boot version check skipped/failed: {e}")
            run_boot_update_check()
            run_boot_config_check()

            # Non-blocking autodeploy for lobster.lua if AT0-M is enabled and archiving of redundant plugins
            try:
                from utils.plugin_manager import archive_redundant_legacy_plugins, autodeploy_lobster_if_needed
                archive_redundant_legacy_plugins()
                autodeploy_lobster_if_needed()
            except Exception as e:
                logger.debug(f"Plugin boot check skipped/failed: {e}")

            # The ProtonDB prefetch is queued behind the update check instead
            # of running here: it issues one HTTP request per installed game,
            # and doing that while the manifest check is still hammering Steam
            # is what made the first minute after boot feel crowded.

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

        # Show the Canary welcome slideshow on first launch. This replaced the
        # older Training Wheels Protocol dialog, which was removed: every action
        # it offered (recommended settings, config check/repair, SLS install +
        # version tiles) already exists in Settings -> Health.
        from utils.settings import is_canary_welcome_needed
        if is_canary_welcome_needed():
            QTimer.singleShot(1000, self._show_canary_welcome)
            QTimer.singleShot(4000, self._check_legacy_plugins_startup)
        else:
            QTimer.singleShot(1500, self._check_legacy_plugins_startup)

    def _check_legacy_plugins_startup(self) -> None:
        """Check for legacy or foreign plugins on startup and prompt user if needed."""
        try:
            from utils.plugin_manager import check_and_prompt_legacy_plugins
            check_and_prompt_legacy_plugins(parent_window=self)
        except Exception as e:
            logger.debug(f"[MainWindow] Error checking legacy plugins on startup: {e}")

    def _show_canary_welcome(self) -> None:
        """Display the Canary Welcome Slideshow onboarding dialog."""
        try:
            from ui.dialogs.canary_welcome_dialog import CanaryWelcomeDialog
            dlg = CanaryWelcomeDialog(parent=self, manual=False)
            dlg.exec()
        except Exception as e:
            logger.error(f"Failed to display Canary Welcome dialog: {e}", exc_info=True)

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

            title_lbl = QLabel("Try ASSella Experimental Features!", dlg)
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
        from ui.dialogs.lain import LainMinigameDialog

        dialog = LainMinigameDialog(self)
        dialog.game_completed.connect(self.on_minigame_completed)
        dialog.exec()

    def on_minigame_completed(self, score: int) -> None:
        """Handle minigame completion."""
        logger.info(f"Lain minigame completed with score: {score}")
        ThemedMessageBox.information(
            self,
            "The Wired",
            f"Connection Terminated\n\nFinal Score: {score}",
        )

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
        # The same GUI-thread timer publishes the progress state for the web UI.
        self.web_command_timer.timeout.connect(self._refresh_web_status_snapshot)
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

    def _refresh_web_status_snapshot(self) -> None:
        """Publish the progress widgets' state for the web UI.

        Runs on the GUI thread (driven by ``web_command_timer``) so the web
        server never has to touch QWidget state from its request threads.
        """
        progress = 0
        progress_visible = False
        speed = ""
        speed_visible = False

        try:
            if self.progress_bar is not None:
                progress_visible = bool(self.progress_bar.isVisible())
                progress = int(self.progress_bar.value())
            if self.speed_label is not None:
                speed_visible = bool(self.speed_label.isVisible())
                # The label elides what it displays; the web UI wants the whole
                # speed string.
                speed = getattr(self.speed_label, "full_text", "") or self.speed_label.text()
        except RuntimeError as exc:
            # Widgets already destroyed (window tearing down).
            logger.debug(f"Web status snapshot refresh skipped: {exc}")
            return

        self._web_status_snapshot = {
            "progress": progress,
            "progress_visible": progress_visible,
            "speed": str(speed),
            "speed_visible": speed_visible,
        }

    def get_web_status_snapshot(self) -> dict:
        """Return a plain-Python copy of the published status snapshot.

        Safe to call from any thread: it only reads a dict of primitives.
        """
        snapshot = getattr(self, "_web_status_snapshot", None) or {}
        return dict(snapshot)

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
                    timeout=2.0,
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

        # Routine network work that used to run next to the update check is
        # queued behind it, so the window settles first and the network is used
        # by one thing at a time.
        self._queue_after_update_check(self._run_boot_protondb_prefetch)

        if self.settings.value("check_updates_on_boot", True, type=bool):
            logger.info(f"Initial game library scan completed ({games_found} games found). Triggering staggered game updates check.")
            if self.game_manager:
                # Stagger by 2 seconds so the UI finishes rendering before Steam API storms begin
                self._gui_phase = "boot update check"
                QTimer.singleShot(2000, lambda: self.game_manager.check_game_updates_async())
        else:
            logger.info(f"Initial game library scan completed ({games_found} games found). Background boot updates check is disabled.")
            # No update check will run, so nothing will drain the queue.
            self._drain_after_update_check_queue()

        # Run depot key migration in the background (populates depot_keys.db from cached zips)
        self._run_depot_key_migration()

    def _queue_after_update_check(self, callback) -> None:
        """Run `callback` once, after the running (or next) update check ends.

        Work that is routine but network bound - the ProtonDB prefetch, the
        SteamDB warm-up - used to start in parallel with the boot update check.
        One thing at a time keeps the machine quiet while the window is still
        settling, which is the whole point of the calm boot.
        """
        queue = getattr(self, "_after_update_check_queue", None)
        if queue is None:
            queue = []
            self._after_update_check_queue = queue
        queue.append(callback)
        logger.debug(f"Queued {len(queue)} task(s) to run after the update check.")

        if getattr(self, "_after_update_check_connected", False):
            return
        self._after_update_check_connected = True
        try:
            # update_checks_finished covers the AT0-M stage too, so the queued
            # work really starts after every check of this cycle is over.
            self.game_manager.update_checks_finished.connect(
                self._drain_after_update_check_queue,
                Qt.ConnectionType.QueuedConnection,
            )
        except Exception as e:
            logger.debug(f"Could not connect the post-update-check queue: {e}")
            self._after_update_check_connected = False

    def _drain_after_update_check_queue(self) -> None:
        """Run everything that was waiting for the update check to finish."""
        queue = getattr(self, "_after_update_check_queue", None) or []
        self._after_update_check_queue = []
        if queue:
            logger.info(f"Running {len(queue)} routine task(s) queued behind the update check.")
        for callback in queue:
            try:
                callback()
            except Exception as e:
                logger.debug(f"Queued post-update-check task failed: {e}")

    def _run_boot_protondb_prefetch(self) -> None:
        """Queue ProtonDB ratings fetches for every installed game.

        Enqueue only: the fetches themselves run in a sequential worker thread,
        so this returns immediately.
        """
        try:
            from core.ratings import prefetch_protondb_for_appids

            if not self.game_manager:
                return
            all_appids = [
                str(g.get("appid", "0"))
                for g in self.game_manager.get_all_games()
                if g.get("appid") and str(g.get("appid")) not in ("0", "N/A", "unknown")
            ]
            if all_appids:
                prefetch_protondb_for_appids(all_appids)
                logger.debug(f"ProtonDB ratings prefetch queued for {len(all_appids)} game(s).")
        except Exception as e:
            logger.debug(f"ProtonDB boot prefetch failed: {e}")

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



    def _setup_system_status_timer(self) -> None:
        """Coalesce status-panel refreshes into one GUI pass per second.

        The dashboard rebuilds itself hundreds of times during a bulk update
        check, and every rebuild used to re-probe the filesystem and re-style
        every status label. One pass per second is indistinguishable to the eye
        and leaves the event loop free for actual input.
        """
        self._system_status_timer = QTimer(self)
        self._system_status_timer.setSingleShot(True)
        self._system_status_timer.setInterval(1000)
        self._system_status_timer.timeout.connect(self._apply_system_status_refresh)
        self._gui_phase = "startup"

    def _setup_gui_stall_watchdog(self) -> None:
        """Report event-loop stalls in the log.

        A 250 ms heartbeat on the Qt event loop. If the loop cannot run for
        more than half a second the elapsed time is logged together with the
        phase the UI was in, which turns "the UI felt stuck" into a number.
        """
        self._gui_watchdog_timer = QTimer(self)
        self._gui_watchdog_timer.setInterval(250)
        self._gui_watchdog_timer.timeout.connect(self._on_gui_heartbeat)
        self._gui_watchdog_timer.start()
        self._gui_last_heartbeat = time.monotonic()

    def _on_gui_heartbeat(self) -> None:
        """Watchdog tick: report a long event-loop stall."""
        now = time.monotonic()
        last = getattr(self, "_gui_last_heartbeat", None)
        if last is not None:
            gap = now - last
            if gap > 0.5:
                logger.warning(
                    f"[GUI] Main thread stalled for {gap:.2f}s "
                    f"(phase: {getattr(self, '_gui_phase', 'unknown')})"
                )
                if gap > 1.0:
                    self._log_gui_stall_stack(gap)
        self._gui_last_heartbeat = now

    def _log_gui_stall_stack(self, gap: float) -> None:
        """Print where the GUI thread was while the window was unresponsive."""
        import threading
        import traceback

        try:
            frame = sys._current_frames().get(threading.get_ident())
            if frame is None:
                return
            stack = "".join(traceback.format_stack(frame))
            logger.warning(
                f"[GUI] Stall of {gap:.2f}s - main thread stack:\n{stack}"
            )
        except Exception as e:
            logger.debug(f"[GUI] Could not capture the stall stack: {e}")

    def _schedule_steamdb_warmup(self) -> None:
        """Start the Byparr/Chromium solver once the boot update storm settles."""
        if getattr(self, "_steamdb_warmup_scheduled", False):
            return
        self._steamdb_warmup_scheduled = True

        def _start() -> None:
            if getattr(self, "_steamdb_warmup_started", False):
                return
            self._steamdb_warmup_started = True
            try:
                self.check_steamdb_status()
            except Exception as e:
                logger.debug(f"[SteamDB] Warm-up failed: {e}")

        def _begin() -> None:
            """Let the window settle before the solver warms up.

            Byparr starts a headless Chromium, and that warm-up wants every
            core it can get. Starting it a few seconds after the update check
            keeps it away from the first render and the first interactions.
            """
            self._steamdb_warmup_settle = QTimer(self)
            self._steamdb_warmup_settle.setSingleShot(True)
            self._steamdb_warmup_settle.setInterval(3000)
            self._steamdb_warmup_settle.timeout.connect(_start)
            self._steamdb_warmup_settle.start()

        self._steamdb_warmup_fallback = QTimer(self)
        self._steamdb_warmup_fallback.setSingleShot(True)
        self._steamdb_warmup_fallback.setInterval(20000)
        self._steamdb_warmup_fallback.timeout.connect(_start)

        # Fire as soon as every check of the boot cycle is done.
        try:
            self.game_manager.update_checks_finished.connect(
                _begin, Qt.ConnectionType.QueuedConnection
            )
        except Exception as e:
            logger.debug(f"[SteamDB] Could not schedule the warm-up: {e}")
        self._steamdb_warmup_fallback.start()

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
        
        self._quote_text, self._quote_source = self.quotes[0]

        self.quote_label = QLabel()
        self.quote_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.quote_label.setStyleSheet(
            "font-size: 11.5px; color: rgba(255, 255, 255, 0.92); background: transparent; border: none;"
        )
        self.quote_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.quote_label.setMinimumWidth(0)

        self.quote_source_label = None

        footer_layout.addWidget(self.quote_label, 1)

        self.layout.addWidget(self.footer_widget)
        self._refresh_quote_display()

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
        self._refresh_quote_display()
        titlebar = getattr(self, "bottom_titlebar", None)
        if titlebar and titlebar.search_controller:
            titlebar.search_controller.reposition()

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
        self.hubcap_api_value = QLabel("API: --/-- | --")
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

        self.speed_label = ElidedLabel("", enable_tooltip=False)
        self.speed_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.speed_label.setVisible(False)
        self.progress_controls_layout.addWidget(self.speed_label, 1)

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
        from ui.dialogs.settings import SettingsDialog

        dialog = SettingsDialog(self, initial_tab=initial_tab)
        dialog.exec()
        self._check_network_connections_async()
        self.refresh_hubcap_stats()

    def open_fetch_dialog(self, initial_query: str = "") -> None:
        from ui.dialogs.fetchmanifest import FetchManifestDialog

        self.ui_state.fetch_dialog = FetchManifestDialog(self, initial_query=initial_query)
        self.ui_state.fetch_dialog.exec()
        self.ui_state.fetch_dialog = None


    def open_game_library(self) -> None:
        from ui.dialogs.gamelibrary import GameLibraryDialog

        dialog = GameLibraryDialog(self)
        dialog.exec()

    def open_status_dialog(self) -> None:
        from ui.dialogs.status import StatusDialog

        dialog = StatusDialog(self)
        dialog.exec()

    def open_credits_dialog(self) -> None:
        from ui.dialogs.credits import CreditsDialog

        dialog = CreditsDialog(self)
        dialog.exec()

    def check_steam_updates_blocked(self) -> bool:
        """Check if steam updates are blocked via steam.cfg."""
        from utils.steam_updates import are_steam_updates_blocked
        return are_steam_updates_blocked()

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

        self.refresh_system_status_now()

    @pyqtSlot()
    def refresh_system_status(self) -> None:
        """Schedule a refresh of the local Steam/SLS/ASSella status labels.

        The actual work runs in _apply_system_status_refresh(), coalesced to at
        most one pass per second. A bulk update check rebuilds the dashboard
        hundreds of times and every rebuild re-probed the filesystem, which
        starved the event loop exactly when the user is most likely to click
        something.
        """
        import threading
        if threading.current_thread() is not threading.main_thread():
            if hasattr(self, "refresh_system_status_signal"):
                self.refresh_system_status_signal.emit()
            return

        timer = getattr(self, "_system_status_timer", None)
        if timer is None:
            self._apply_system_status_refresh()
            return
        timer.start()

    def refresh_system_status_now(self) -> None:
        """Refresh the status labels right away, cancelling any pending pass."""
        timer = getattr(self, "_system_status_timer", None)
        if timer is not None:
            timer.stop()
        self._apply_system_status_refresh()

    def _apply_system_status_refresh(self) -> None:
        """Refresh local Steam updates, SLS, and ASSella status labels."""
        if not hasattr(self, "steam_updates_value") or not self.steam_updates_value:
            return

        self._gui_phase = "status refresh"
        try:
            self._collect_system_status()
        finally:
            self._gui_phase = "idle"

    def _collect_system_status(self) -> None:
        """Collect and render the Steam/SLS/ASSella status labels."""
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
                from core.steam_helpers import get_installed_steam_client_version
                client_ver = get_installed_steam_client_version()
                disp_text = client_ver if client_ver else "Online"
                self.steam_conn_value.setText(disp_text)
                self.steam_conn_value.setStyleSheet(self._get_status_style("success"))
                self.steam_conn_value.setToolTip(f"Steam client is running (Version: {disp_text}).")

        # Health Visor Status (SLS binary + version + config + SLS API)
        if hasattr(self, "health_status_value") and self.health_status_value:
            try:
                import utils.assfixer as assfixer
                from utils.slssteam_integration import check_slssteam_binary_is_latest

                sls_detected = sls_paths.get("detected", False)
                # cached_only: this runs on the GUI thread and must never wait
                # on the network. The background boot check fills the cache.
                ver_res = check_slssteam_binary_is_latest(cached_only=True)
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
                has_byparr = ByparrManager.is_available()
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
        current_text = self._quote_text
        available_quotes = [q for q in self.quotes if q[0] != current_text]
        if available_quotes:
            import random
            self._quote_text, self._quote_source = random.choice(available_quotes)
            if hasattr(self, "quote_source_label") and self.quote_source_label:
                self.quote_source_label.setText(f"— {self._quote_source}")
            self._refresh_quote_display()

    def _refresh_quote_display(self) -> None:
        """Draw the rotating quote elided to the footer width.

        Formats the quote inside typographic quotation marks and the source game in italics.
        Eliding ensures long quotes never force minimum width on the window.
        """
        if not getattr(self, "quote_label", None) or not getattr(
            self, "footer_widget", None
        ):
            return

        layout = self.footer_widget.layout()
        margins = layout.contentsMargins() if layout else None
        left_m = margins.left() if margins else 15
        right_m = margins.right() if margins else 15

        # Available horizontal space inside the footer widget
        available = max(100, self.footer_widget.width() - left_m - right_m)
        metrics = QFontMetrics(self.quote_label.font())

        source_str = f" — {self._quote_source}"
        source_width = metrics.horizontalAdvance(source_str) + 30
        max_quote_width = max(50, available - source_width)

        elided_quote = metrics.elidedText(
            self._quote_text, Qt.TextElideMode.ElideRight, max_quote_width
        )
        html = (
            f'“{elided_quote}” &nbsp;—&nbsp; '
            f'<i style="color: rgba(255, 255, 255, 0.55); font-style: italic;">{self._quote_source}</i>'
        )
        self.quote_label.setText(html)
        tooltip = f'“{self._quote_text}” — {self._quote_source}'
        self.quote_label.setToolTip(tooltip)

    def check_steamdb_status(self) -> None:
        """Asynchronously verify that SteamDB is accessible via fast-path or Byparr."""
        if getattr(self, "_steamdb_check_running", False):
            return

        try:
            from core.steamdb_scraper import ByparrManager, cffi_requests
            has_solver = ByparrManager.is_available() or (cffi_requests is not None)
            if not has_solver:
                if hasattr(self, "steamdb_container"):
                    self.steamdb_container.setVisible(False)
                return
        except Exception:
            return

        self._steamdb_check_running = True
        if hasattr(self, "steamdb_status_value") and self.steamdb_status_value:
            self.steamdb_status_value.setText("Checking...")
            self.steamdb_status_value.setStyleSheet(self._get_status_style("neutral"))
            self.steamdb_status_value.setToolTip("Verifying SteamDB connection and bypass status...")

        def _worker():
            try:
                from core.steamdb_scraper import verify_steamdb_status
                ok, provider = verify_steamdb_status(timeout_seconds=5)
                logger.info(f"[SteamDB Visor] verify_steamdb_status result: {ok} ({provider})")
                self._main_thread_callable.emit(lambda: self._on_steamdb_status_result(ok, provider))
            except Exception as e:
                logger.debug(f"[SteamDB] Error during SteamDB status verification: {e}")
                self._main_thread_callable.emit(lambda: self._on_steamdb_status_result(False, "Offline"))
            finally:
                self._steamdb_check_running = False

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    def _on_steamdb_status_result(self, is_working: bool, provider: str = "") -> None:
        """Display SteamDB visor item if SteamDB is working/ready."""
        self._steamdb_verified = is_working
        if hasattr(self, "steamdb_container") and hasattr(self, "steamdb_status_value"):
            try:
                from core.steamdb_scraper import ByparrManager, cffi_requests
                has_solver = ByparrManager.is_available() or (cffi_requests is not None)
            except Exception:
                has_solver = False

            if not has_solver:
                self.steamdb_container.setVisible(False)
                return

            self.steamdb_container.setVisible(True)
            if is_working:
                self.steamdb_status_value.setText("Ready")
                self.steamdb_status_value.setStyleSheet(self._get_status_style("success"))
                mode_info = f" via {provider}" if provider else ""
                self.steamdb_status_value.setToolTip(
                    f"SteamDB: Active{mode_info}\n"
                    "Version history, patch notes & depot enrichment enabled."
                )
                logger.info(f"[SteamDB Visor] SteamDB status updated to: Ready ({provider})")
            else:
                self.steamdb_status_value.setText("Offline")
                self.steamdb_status_value.setStyleSheet(self._get_status_style("neutral"))
                self.steamdb_status_value.setToolTip(
                    "SteamDB: Service or solver is currently offline.\n"
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

        from core.morrenus_api import get_all_hubcap_stats

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

        # 2. Key Expiry and color-coded status
        from core.morrenus_api import compute_api_key_expiry_info

        api_key = self.settings.value("morrenus_api_key", "", type=str) if self.settings else ""
        expiry_info = compute_api_key_expiry_info(
            stats.get("api_key_expires_at"),
            api_key=api_key,
            settings=self.settings,
        )

        time_text = expiry_info["text"]
        accent_color = self.settings.value("accent_color", "#C06C84", type=str) if self.settings else "#C06C84"
        from utils.color_utils import get_semantic_colors
        sem_colors = get_semantic_colors(accent_color)
        status_key = expiry_info.get("status", "success")
        color = sem_colors.get(status_key, expiry_info.get("color", "#a8dab5"))

        quota_str = f'API: {usage}/{limit} | <span style="color: {color}; font-weight: bold;">{time_text}</span>'

        if expiry_info["is_lifetime"]:
            exp_desc = "Lifetime key (Never expires)"
        elif expiry_info["is_expired"]:
            exp_desc = f"{expiry_info['formatted_date']} (Expired)"
        elif expiry_info["hours"] <= 24:
            exp_desc = f"{expiry_info['formatted_date']} ({time_text} remaining, {expiry_info['percentage']:.0f}% left)"
        else:
            exp_desc = f"{expiry_info['formatted_date']} ({expiry_info['days']}d remaining, {expiry_info['percentage']:.0f}% left)"

        tooltip_str = (
            f"Hubcap API:\n"
            f"• Daily Manifests: {usage} / {limit} (resets at 00:00 UTC)\n"
            f"• API Key Expiry: {exp_desc}"
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

    def sync_at0m_keys(self, appid: str) -> None:
        """Syncs the Hubcap decryption keys of a Steam-managed (AT0-M) game.

        ASSella never downloads an AT0-M game - Steam does. What ASSella does
        is make sure SLSsteam holds the decryption keys of the depots the game
        needs, so Steam can actually decrypt them.
        """
        import threading

        appid = str(appid)
        running = getattr(self, "_at0m_key_syncs_running", None)
        if running is None:
            running = set()
            self._at0m_key_syncs_running = running
        if appid in running:
            logger.info(f"[AT0-M] A key sync for {appid} is already running.")
            return

        gm = self.game_manager
        game = gm.get_game(appid) if gm else None
        name = (game or {}).get("game_name") or f"App {appid}"

        running.add(appid)
        self._refresh_at0m_sync_rows()

        def _worker():
            try:
                from managers.at0m_update_checker import At0mUpdateChecker
                ok, message = At0mUpdateChecker.get_instance().sync_keys(appid)
            except Exception as e:
                logger.error(f"[AT0-M] Key sync failed for {appid}: {e}", exc_info=True)
                ok, message = False, str(e)
            self._main_thread_callable.emit(
                lambda: self._on_at0m_key_sync_finished(appid, ok, message, name)
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_at0m_sync_rows(self) -> None:
        """Repaints the pending-updates list so a running sync shows as busy."""
        if hasattr(self, "simplified_terminal") and self.simplified_terminal:
            self.simplified_terminal.update_stats(immediate=True)

    def _on_at0m_key_sync_finished(self, appid: str, ok: bool, message: str, name: str) -> None:
        """Reports the outcome of a key sync to the user."""
        running = getattr(self, "_at0m_key_syncs_running", None)
        if running is not None:
            running.discard(str(appid))

        if ok:
            if hasattr(self, "status_pager") and self.status_pager:
                self.status_pager.set_status(f"KEYS SYNCED · {name}")
            ThemedMessageBox.information(self, "Keys Synced", message)
        else:
            logger.warning(f"[AT0-M] Key sync failed for {name}: {message}")
            ThemedMessageBox.warning(self, "Key Sync Failed", message)

        self._refresh_at0m_sync_rows()

    @pyqtSlot(str)
    def _show_key_sync_notice(self, names_text: str) -> None:
        """Tells the user that the keys are in place but Steam does the download."""
        ThemedMessageBox.information(
            self,
            "Keys Synced",
            f"Keys synced for:\n{names_text}\n\n"
            f"Launch or update the game(s) in Steam to finish.",
        )

    def run_update_all_flow(self) -> None:
        """Flow for updating all games that have update_available status."""
        # Guard: prevent re-entrancy if a cycle is already running
        if getattr(self, "_update_all_running", False):
            return
        # Guard: don't start Update All while an update check is still running
        gm = self.game_manager
        if gm and (getattr(gm, "manifest_check_task", None) is not None or getattr(gm, "manifest_check_runner", None) is not None):
            ThemedMessageBox.information(self, "Update Check Running", "Please wait for the update check to finish before queuing updates.")
            return
        self._update_all_running = True

        if not self.game_manager:
            self._update_all_running = False
            return

        games = self.game_manager.get_all_games()
        updateable_games = []
        at0m_key_games = []
        from utils.game_modes import is_atom_managed
        for g in games:
            if g.get("update_status") == "update_available":
                appid = str(g.get("appid", ""))
                if self.settings.value(f"exclude_from_update_all/{appid}", False, type=bool):
                    continue
                if self.settings.value(f"pin_build/{appid}", False, type=bool):
                    continue
                if is_atom_managed(g):
                    # Steam-managed games are downloaded by Steam, never by
                    # DepotDownloader. All ASSella does is sync the keys.
                    at0m_key_games.append(g)
                else:
                    updateable_games.append(g)

        if not updateable_games and not at0m_key_games:
            self._update_all_running = False
            ThemedMessageBox.information(
                self,
                "No Updates Available",
                "All games in your library are up to date!",
            )
            return

        total = len(updateable_games) + len(at0m_key_games)

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
            synced_names = []
            key_processed = 0

            # --- Phase 1: sync the keys of the Steam-managed games ---
            # Steam downloads those games itself, so all ASSella can do is
            # make sure SLSsteam holds the decryption keys.
            if at0m_key_games:
                from managers.at0m_update_checker import At0mUpdateChecker
                checker = At0mUpdateChecker.get_instance()
                for key_game in at0m_key_games:
                    key_appid = str(key_game.get("appid", "0"))
                    key_name = format_game_display_name(key_game)
                    self._set_update_all_btn_preparing(
                        key_processed, total, current_name=key_name
                    )
                    logger.info(f"[Update All] Syncing keys for {key_name} ({key_appid})...")
                    try:
                        ok, message = checker.sync_keys(key_appid)
                    except Exception as e:
                        logger.error(f"[Update All] Key sync failed for {key_name}: {e}")
                        ok, message = False, str(e)
                    key_processed += 1
                    if ok:
                        synced_names.append(key_name)
                        logger.info(f"[Update All] Keys synced for {key_name}")
                    else:
                        logger.warning(f"[Update All] Key sync failed for {key_name}: {message}")
                        skipped_names.append(key_name)

            for idx, game_data in enumerate(updateable_games):
                appid = str(game_data.get("appid", "0"))
                name = format_game_display_name(game_data)
                update_status = game_data.get("update_status")
                self._set_update_all_btn_preparing(key_processed + idx, total, current_name=name)
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
                        selected_dlc_appids = None
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
                                result_holder = [None, None]
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
                                    _lib_path=game_data.get("library_path") or game_data.get("install_path"),
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
                                            library_path=_lib_path,
                                            lock_storage=True,
                                        )
                                        if depot_dialog.exec():
                                            _result_holder[0] = depot_dialog.get_selected_depots()
                                            _result_holder[1] = list(
                                                depot_dialog.get_selected_dlc_data().keys()
                                            )
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
                                    selected_dlc_appids = result_holder[1] or []

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
                        if selected_dlc_appids is not None:
                            metadata["selected_dlc_appids"] = selected_dlc_appids

                    self.job_queue.add_job(local_path, metadata)
                    queued += 1
                    logger.info(f"Update All queued: {name}")

                    # Update button progress after each successful queue
                    self._set_update_all_btn_preparing(key_processed + queued, total)

                except Exception as e:
                    logger.error(f"Update All failed for {name}: {e}", exc_info=True)
                    skipped_names.append(name)

            logger.info(f"[Update All]: queued {queued} of {total} games.")

            # Release guard and refresh UI
            self._update_all_running = False
            from PyQt6.QtCore import QMetaObject, Qt
            from PyQt6.QtCore import Q_ARG
            QMetaObject.invokeMethod(self, "update_dashboard_elements", Qt.ConnectionType.QueuedConnection)

            # Tell the user that the keys are in place but Steam does the download
            if synced_names:
                QMetaObject.invokeMethod(
                    self,
                    "_show_key_sync_notice",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, "\n".join(f"• {n}" for n in synced_names)),
                )

            # Notify user about any skipped games
            if skipped_names and queued == 0:
                QMetaObject.invokeMethod(
                    self,
                    "_show_update_all_skip_notice",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, "\n".join(f"• {n}" for n in skipped_names)),
                    Q_ARG(bool, True),
                    Q_ARG(bool, bool(at0m_key_games) and not updateable_games),
                )
            elif skipped_names:
                QMetaObject.invokeMethod(
                    self,
                    "_show_update_all_skip_notice",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, "\n".join(f"• {n}" for n in skipped_names)),
                    Q_ARG(bool, False),
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
        """Main-thread slot: keep the Update All button disabled and greyed out while queueing updates."""
        try:
            # The button lives on the dashboard widget, not on the ui_state.
            term = getattr(self, "simplified_terminal", None)
            btn = getattr(term, "update_all_btn", None) if term else None
            if btn:
                btn.setEnabled(False)
                if done < total:
                    if current_name:
                        btn.setToolTip(f"Preparing updates... ({done} of {total} ready - {current_name})")
                    else:
                        btn.setToolTip(f"Preparing updates... ({done} of {total} ready)")
                else:
                    btn.setToolTip("Queueing updates...")
        except Exception:
            pass

    @pyqtSlot(str, bool)
    @pyqtSlot(str, bool, bool)
    def _show_update_all_skip_notice(self, names_text: str, all_skipped: bool, key_sync_only: bool = False) -> None:
        """Show a non-blocking notice when some games were skipped in Update All."""
        if key_sync_only:
            ThemedMessageBox.warning(
                self,
                "Update All — Keys Not Synced",
                f"The decryption keys could not be synced for:\n{names_text}\n\n"
                f"Open the game individually to retry.",
            )
        elif all_skipped:
            ThemedMessageBox.warning(
                self,
                "Update All — Manual Action Required",
                f"All pending updates were skipped because they require manual depot selection.\n\n"
                f"Please update these games individually:\n{names_text}",
            )
        else:
            ThemedMessageBox.information(
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
                ThemedMessageBox.critical(self, "Error", f"Failed to package dropped files: {e}")

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
        titlebar = getattr(self, "bottom_titlebar", None)
        if titlebar and titlebar.search_controller:
            titlebar.search_controller.close()
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

        def _check_sync():
            status = "offline"
            remote_clean = None
            try:
                configured_channel = self.settings.value("update_channel", "").strip().lower()
                res = check_for_updates(configured_channel=configured_channel, current_version=app_version)
                status = res["status"]
                remote_clean = res["remote_version"]
                if not configured_channel or configured_channel not in ("stable", "beta", "canary"):
                    self.settings.setValue("update_channel", res["channel"])

                if status == "update_available":
                    if res.get("is_cross_channel"):
                        logger.info(
                            f"Cross-channel update available: {remote_clean} (channel: {res['channel']}, current: {res['local_version']})"
                        )
                    else:
                        logger.info(f"Tool update available: {remote_clean} (current: {res['local_version']})")
                elif status == "up_to_date":
                    logger.info(f"Tool is up to date ({res['local_version']})")
                else:
                    logger.debug(f"Tool update check offline/unresolved: {res.get('error')}")
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
        ThemedMessageBox.information(
            self,
            "Update Installed",
            "The update has been downloaded and installed successfully.\n\n"
            "Please restart ASSella to use the new version.",
        )

    @pyqtSlot(str)
    def _on_update_failed(self, error_msg: str) -> None:
        ThemedMessageBox.warning(
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
            # Last resort: the logger is being torn down here, so emit to stderr
            # directly rather than through it.
            print(f"Error during custom logger shutdown: {e}", file=sys.stderr)  # noqa: T201
