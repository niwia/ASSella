import atexit
import logging
import os
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from collections import deque
from queue import Queue
from typing import Dict, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from managers.app_update_manager import AppUpdateManager
from managers.game_manager import GameManager
from managers.job_queue_manager import JobQueueManager
from managers.task_manager import TaskManager
from managers.ui_state_manager import UIStateManager
from ui.bottom_titlebar import BottomTitleBar
from ui.dialogs.themed_message_box import ThemedMessageBox
from ui.main_window_mixins import (
    DIALOG_WAIT_SECONDS,
    MainWindowSelfUpdateMixin,
    MainWindowSystemStatusMixin,
    MainWindowUpdateAllMixin,
    MainWindowWebBridgeMixin,
)
from ui.widgets import (
    ResizeHandle,
    SimplifiedTerminalWidget,
)
from ui.widgets.elided_label import ElidedLabel
from utils.logger import qt_log_handler
from utils.paths import Paths
from utils.settings import get_settings, is_canary_welcome_needed
from utils.task_runner import TaskRunner
from utils.web_server import WebServerManager

logger = logging.getLogger(__name__)

# Re-export backward-compatible base class alias if imported externally
FramelessWindow = QMainWindow


class MainWindow(
    FramelessWindow,
    MainWindowUpdateAllMixin,
    MainWindowSystemStatusMixin,
    MainWindowSelfUpdateMixin,
    MainWindowWebBridgeMixin,
):
    """Main application window."""

    refresh_system_status_signal = pyqtSignal()
    plugin_check_ready_signal = pyqtSignal(list)
    notify_sls_watcher_crashed_signal = pyqtSignal()
    # Signal used to safely invoke a callable on the main thread from a background thread.
    # Using a typed signal instead of Q_ARG(object, fn) avoids PyQt6 GIL/metatype crashes.
    _main_thread_callable = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.refresh_system_status_signal.connect(self.refresh_system_status)
        self.plugin_check_ready_signal.connect(self._on_plugin_check_ready)
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
        self.stealth_status_value = None
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
        self._tool_update_recheck = False
        self._latest_remote_version = ""
        self._latest_update_source = ""
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
        from utils.assfixer import run_boot_config_check

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
            run_boot_config_check()

            # Plugin check (background thread): only works out what would
            # change (retired / remotely disabled plugins, missing family
            # plugins, updates). The user is asked before anything happens.
            try:
                from utils.plugin_manager import plan_plugin_maintenance
                actions = plan_plugin_maintenance()
                if actions:
                    self.plugin_check_ready_signal.emit(actions)
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
                    from utils.yaml_config_manager import (
                        clean_denuvo_games_section,
                        get_user_config_path,
                    )
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
        if is_canary_welcome_needed():
            QTimer.singleShot(1000, self._show_canary_welcome)
            QTimer.singleShot(4000, self._check_legacy_plugins_startup)
        else:
            QTimer.singleShot(1500, self._check_legacy_plugins_startup)

    def _on_plugin_check_ready(self, actions: list) -> None:
        """Ask the user what to do about the plugin check findings."""
        try:
            from ui.dialogs.plugin_check_dialog import PluginCheckDialog
            from utils.plugin_manager import apply_plugin_actions, snooze_plugin_actions

            dlg = PluginCheckDialog(actions, parent=self, accent_color=getattr(self, "accent_color", "#a1c9fd"))
            dlg.exec()
            if dlg.choice == "apply":
                chosen = dlg.selected()
                for msg in apply_plugin_actions(chosen):
                    logger.info(f"[PluginCheck] {msg}")
                snooze_plugin_actions(dlg.unselected())  # unticked = declined
            elif dlg.choice == "never":
                snooze_plugin_actions(actions)
        except Exception as e:
            logger.warning(f"[MainWindow] Plugin check prompt failed: {e}")

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

    def _prompt_experimental_features_once_if_needed(self) -> None:
        """Prompt user once on startup to try experimental features if not already enabled."""
        has_prompted = self.settings.value("has_prompted_experimental", False, type=bool)
        isp_bypass = self.settings.value("isp_bypass_hubcap", False, type=bool)
        sls_acf = self.settings.value("experimental_acf_independent", False, type=bool)

        if not has_prompted and (not isp_bypass or not sls_acf):
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
                dlg,
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
                dlg,
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
                dlg,
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
                    except Exception as e:
                        logger.debug(f"[MainWindow] Could not ensure SLSsteam prerequisites: {e}")

    def _setup_window_properties(self) -> None:
        """Configure basic window properties."""
        self.setWindowTitle("ASSELA")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setGeometry(100, 100, 800, 350)

        from utils.color_utils import load_assella_app_icon
        app_icon = load_assella_app_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        else:
            logger.warning("Could not load window icon from logo/icon.svg or fallbacks.")

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

        # AT0-M Sync: share the newest Hubcap key with katamari.conf.lua (which
        # Uran, Epsilon and the plugins read). Small file reads, off the GUI thread.
        if self.settings.value("atom_hubcap_sync_enabled", False, type=bool):
            def _sync_hubcap_key():
                try:
                    from utils.sls_lua_config import sync_hubcap_api_key

                    synced, msg = sync_hubcap_api_key(get_settings())
                    if synced:
                        logger.info(f"[AT0-M Sync] {msg}")
                except Exception as e:
                    logger.debug(f"[AT0-M Sync] startup sync skipped: {e}")

            threading.Thread(target=_sync_hubcap_key, name="hubcap-key-sync", daemon=True).start()

        # Initial stats fetch
        self.refresh_hubcap_stats()

        self.game_manager.scan_steam_libraries_async()

    def _on_initial_scan_complete(self, games_found: int) -> None:
        """Slot triggered when the initial library scan completes."""
        try:
            self.game_manager.scan_complete.disconnect(self._on_initial_scan_complete)
        except TypeError as e:
            logger.debug(f"[MainWindow] scan_complete signal already disconnected: {e}")

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
        # Periodically refresh Hubcap connection & stats (every 5 mins)
        self.refresh_hubcap_stats()

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
        except Exception as e:
            logger.debug(f"[MainWindow] Could not check Byparr directory during UI setup: {e}")
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

        # 2. Stealth Status
        stealth_lbl = QLabel("Stealth:")
        stealth_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.70); font-size: 11px; background: transparent; border: none;")
        stealth_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        stealth_lbl.setToolTip("Click to open Stealth settings")
        stealth_lbl.mousePressEvent = lambda e: self.open_settings(initial_tab="stealth")
        self.stealth_status_value = QLabel("Checking...")
        self.stealth_status_value.setStyleSheet(self._get_status_style("neutral"))
        self.stealth_status_value.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stealth_status_value.setToolTip("Click to open Stealth settings")
        self.stealth_status_value.mousePressEvent = lambda e: self.open_settings(initial_tab="stealth")
        stealth_item = QHBoxLayout()
        stealth_item.setSpacing(4)
        stealth_item.addWidget(stealth_lbl)
        stealth_item.addWidget(self.stealth_status_value)
        row2_layout.addLayout(stealth_item)

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
        sep_style = "color: rgba(255,255,255,60); font-size: 9pt; padding: 0;"
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
        from utils.font_safety import wait_for_probe

        wait_for_probe()  # normally long finished; see utils/font_safety.py
        dialog = SettingsDialog(self, initial_tab=initial_tab)
        dialog.exec()
        self._check_network_connections_async()
        self.refresh_hubcap_stats()

    def open_fetch_dialog(self, initial_query: str = "") -> None:
        from ui.dialogs.fetchmanifest import FetchManifestDialog

        self.ui_state.fetch_dialog = FetchManifestDialog(self, initial_query=initial_query)
        self.ui_state.fetch_dialog.exec()
        self.ui_state.fetch_dialog = None

    def open_game_library(self, show_details_for_appid: Optional[str] = None) -> None:
        from ui.dialogs.gamelibrary import GameLibraryDialog

        dialog = GameLibraryDialog(self, show_details_for_appid=show_details_for_appid)
        dialog.exec()

    def open_status_dialog(self) -> None:
        from ui.dialogs.status import StatusDialog

        dialog = StatusDialog(self)
        dialog.exec()

    def open_credits_dialog(self) -> None:
        from ui.dialogs.credits import CreditsDialog

        dialog = CreditsDialog(self)
        dialog.exec()

    @pyqtSlot()
    def update_dashboard_elements(self) -> None:
        """Dynamically update dashboard elements and floating action button."""
        if hasattr(self, "simplified_terminal") and self.simplified_terminal:
            self.simplified_terminal.update_stats()
        self.refresh_system_status()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
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
                with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as zip_ref:
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
            except Exception as e:
                logger.debug(f"[MainWindow] Error stopping TorManager on exit: {e}")
            try:
                from core.steamdb_scraper import ByparrManager
                ByparrManager.stop()
            except Exception as e:
                logger.debug(f"[MainWindow] Error stopping ByparrManager on exit: {e}")
            # Signal SLS background retry workers to stop before teardown
            try:
                from utils.slssteam_integration import register_shutdown
                register_shutdown()
            except Exception as e:
                logger.debug(f"[MainWindow] Error registering SLS shutdown: {e}")
            from utils.update_status_cache import get_update_cache
            get_update_cache().save()  # Force synchronous save of status cache before exit
            try:
                from utils.task_runner import TaskRunner
                TaskRunner.stop_all_active()
            except Exception as e:
                logger.debug(f"[MainWindow] Error stopping active TaskRunners: {e}")
            self.task_manager.cleanup()
            self.job_queue.clear()
            self.game_manager.cleanup()
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        finally:
            MainWindow._cleanup_logging()

        super().closeEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
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
        elif preset == "christmas":
            christmas_bg = Paths.resource("christmas/christmas_bg.jpg")
            if christmas_bg.exists():
                bg_path_to_draw = str(christmas_bg)

        if bg_path_to_draw:
            if (
                not hasattr(self, "_active_bg_path")
                or self._active_bg_path != bg_path_to_draw
                or not hasattr(self, "_custom_bg_pixmap")
                or self._custom_bg_pixmap is None
            ):
                self._active_bg_path = bg_path_to_draw
                self._custom_bg_pixmap = QPixmap(bg_path_to_draw)

            # Deep dark background base
            base_col = QColor(self.background_color or "#0d0d0f")
            painter.fillRect(rect, base_col)

            if hasattr(self, "_custom_bg_pixmap") and self._custom_bg_pixmap and not self._custom_bg_pixmap.isNull():
                if (
                    not hasattr(self, "_cached_scaled_bg")
                    or self._cached_scaled_bg is None
                    or getattr(self, "_cached_scaled_size", None) != rect.size()
                    or getattr(self, "_cached_scaled_path", None) != bg_path_to_draw
                ):
                    self._cached_scaled_size = rect.size()
                    self._cached_scaled_path = bg_path_to_draw
                    self._cached_scaled_bg = self._custom_bg_pixmap.scaled(
                        rect.size(),
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )

                scaled = self._cached_scaled_bg
                x = (rect.width() - scaled.width()) // 2
                y = (rect.height() - scaled.height()) // 2

                # Atmospheric opacity
                bg_opacity = 0.35 if preset == "christmas" else 0.50
                painter.setOpacity(bg_opacity)
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

        # Christmas snowfall particle simulation overlay
        if preset == "christmas":
            if not hasattr(self, "_snow_engine") or self._snow_engine is None:
                from ui.particles.snow_engine import SnowEngine
                self._snow_engine = SnowEngine(self, flake_count=100)
                self._snow_engine.start()
            elif not self._snow_engine.is_running():
                self._snow_engine.start()
            self._snow_engine.paint(painter, rect.width(), rect.height())
        else:
            if hasattr(self, "_snow_engine") and self._snow_engine and self._snow_engine.is_running():
                self._snow_engine.stop()

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