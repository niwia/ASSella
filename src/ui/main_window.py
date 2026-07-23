import atexit
import logging
import random
import re
import sys
from collections import deque
from typing import Dict, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSlot, pyqtSignal, QMetaObject, Q_ARG
from PyQt6.QtGui import (
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QShortcut,
    QColor,
)
from PyQt6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
    QStackedWidget,
    QStackedLayout,
    QFrame,
    QScrollArea,
    QPushButton,
)

from components.custom_widgets import ScaledFontLabel
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
from utils.logger import qt_log_handler
from utils.paths import Paths
from queue import Queue
from utils.web_server import WebServerManager, get_local_ip
from utils.settings import get_settings
from utils.task_runner import TaskRunner
from core.morrenus_api import get_user_stats
from datetime import datetime, timezone
from utils.version import app_version

logger = logging.getLogger(__name__)


class ResizeHandle(QWidget):
    """Transparent widget used to resize the frameless window."""

    def __init__(self, edge_name: str, main_window: "MainWindow"):
        super().__init__(main_window)
        self.edge_name = edge_name
        self.main_window = main_window
        self.resizing = False
        self.resize_start_pos = None
        self.resize_start_geom = None
        self.setStyleSheet("background: transparent;")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        window = self.main_window.windowHandle()
        edge = self._get_qt_edge()

        # Try system resize first (Wayland/Windows native)
        if window and window.isExposed() and window.startSystemResize(edge):
            event.accept()
            return

        # Fallback for X11/other
        self.resizing = True
        self.resize_start_pos = event.globalPosition().toPoint()
        self.resize_start_geom = self.main_window.geometry()
        self.grabMouse()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.resizing:
            return

        delta = event.globalPosition().toPoint() - self.resize_start_pos
        geom = self.resize_start_geom
        x, y, w, h = geom.x(), geom.y(), geom.width(), geom.height()

        if "right" in self.edge_name:
            w += delta.x()
        if "bottom" in self.edge_name:
            h += delta.y()
        if "left" in self.edge_name:
            x += delta.x()
            w -= delta.x()
        if "top" in self.edge_name:
            y += delta.y()
            h -= delta.y()

        w = max(w, self.main_window.minimumWidth())
        h = max(h, self.main_window.minimumHeight())

        self.main_window.setGeometry(x, y, w, h)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.resizing:
            self.releaseMouse()
            self.resizing = False
        event.accept()

    def _get_qt_edge(self) -> Qt.Edge:
        edge_map = {
            "left": Qt.Edge.LeftEdge,
            "right": Qt.Edge.RightEdge,
            "top": Qt.Edge.TopEdge,
            "bottom": Qt.Edge.BottomEdge,
            "top_left": Qt.Edge.LeftEdge,
            "top_right": Qt.Edge.RightEdge,
            "bottom_left": Qt.Edge.LeftEdge,
            "bottom_right": Qt.Edge.RightEdge,
        }
        return edge_map.get(self.edge_name, Qt.Edge.RightEdge)

class SimplifiedTerminalWidget(QWidget):
    """A simplified terminal widget that displays stats and quotes when idle, and a job progress checklist when active."""

    def __init__(self, main_window: "MainWindow"):
        super().__init__(main_window)
        self.main_window = main_window
        self.settings = main_window.settings
        self.installation_history = []

        # (quote text, game/source title) tuples
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
            ("A hunter must hunt.", "The Witcher 3"),
            ("Hey, you. You're finally awake.", "The Elder Scrolls V: Skyrim"),
            ("Determination.", "Undertale"),
            ("The world fears the inevitable plummet into the abyss.", "NieR: Automata"),
            ("Stay a while and listen.", "Diablo II"),
            ("It's not about the money, it's about sending a message.", "Batman: Arkham City"),
            ("What is a man? A miserable little pile of secrets!", "Castlevania: Symphony of the Night"),
            ("A famous explorer once said that the extraordinary is in what we do, not who we are.", "Tomb Raider"),
            ("I used to be an adventurer like you. Then I took an arrow in the knee.", "The Elder Scrolls V: Skyrim"),
            ("The right man in the wrong place can make all the difference in the world.", "Half-Life 2"),
        ]

        self.setStyleSheet("background: transparent;")
        self.init_ui()

        # Connect signals from GameManager to update stats
        if hasattr(self.main_window, "game_manager") and self.main_window.game_manager:
            gm = self.main_window.game_manager
            gm.library_updated.connect(self.update_stats)
            gm.game_update_status_changed.connect(
                lambda appid, status: self.update_stats()
            )
            # Also connect scan_complete and all_updates_checked so we never miss
            # a refresh if library_updated fires before this widget is constructed
            gm.scan_complete.connect(lambda _: self.update_stats())
            gm.all_updates_checked.connect(self.update_stats)

        self.update_stats()
        self.update_history_display()
        self.update_style()

    def init_ui(self):
        self.layout = QStackedLayout(self)
        self.layout.setContentsMargins(10, 5, 10, 5)
        self.layout.setSpacing(2)

        # --- VIEW 0: IDLE STATE (3-Column Dashboard) ---
        self.idle_widget = QWidget()
        idle_layout = QHBoxLayout(self.idle_widget)
        idle_layout.setContentsMargins(0, 0, 0, 0)
        idle_layout.setSpacing(8)

        panel_style = """
            QFrame {
                background-color: rgba(30, 30, 30, 100);
                border: 1px solid rgba(255, 255, 255, 12);
                border-radius: 6px;
            }
            QLabel {
                border: none;
                background: transparent;
            }
        """

        scrollbar_style = """
            QScrollBar:vertical {
                border: none;
                background: rgba(0, 0, 0, 10);
                width: 4px;
                margin: 0px;
                border-radius: 2px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 30);
                min-height: 20px;
                border-radius: 2px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 60);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """

        # Column 1: System Status & Quotes
        self.panel_left = QFrame()
        self.panel_left.setFrameShape(QFrame.Shape.StyledPanel)
        self.panel_left.setStyleSheet(panel_style)
        left_layout = QVBoxLayout(self.panel_left)
        left_layout.setContentsMargins(8, 6, 8, 6)
        left_layout.setSpacing(4)

        self.stats_title = QLabel("SYSTEM STATUS")
        self.total_games_label = QLabel("Library Size: -- games")
        self.updates_label = QLabel("Updates: -- available")

        # Separator line
        self.separator = QFrame()
        self.separator.setFrameShape(QFrame.Shape.HLine)
        self.separator.setFrameShadow(QFrame.Shadow.Sunken)
        self.separator.setLineWidth(1)
        self.separator.setFixedHeight(1)

        self.quote_label = QLabel(self.quotes[0][0])
        self.quote_label.setWordWrap(True)
        self.quote_source_label = QLabel(f"— {self.quotes[0][1]}")
        self.quote_source_label.setAlignment(Qt.AlignmentFlag.AlignRight)

        left_layout.addWidget(self.stats_title)
        left_layout.addWidget(self.total_games_label)
        left_layout.addWidget(self.updates_label)
        left_layout.addWidget(self.separator)
        left_layout.addWidget(self.quote_label, 1)
        left_layout.addWidget(self.quote_source_label)

        # Column 2: Available Updates
        self.panel_mid = QFrame()
        self.panel_mid.setFrameShape(QFrame.Shape.StyledPanel)
        self.panel_mid.setStyleSheet(panel_style)
        mid_layout = QVBoxLayout(self.panel_mid)
        mid_layout.setContentsMargins(8, 6, 8, 6)
        mid_layout.setSpacing(4)

        self.updates_title = QLabel("PENDING UPDATES")
        self.updates_scroll = QScrollArea()
        self.updates_scroll.setWidgetResizable(True)
        self.updates_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.updates_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.updates_scroll.verticalScrollBar().setStyleSheet(scrollbar_style)

        self.updates_scroll_widget = QWidget()
        self.updates_scroll_widget.setStyleSheet("background: transparent;")
        self.updates_scroll_layout = QVBoxLayout(self.updates_scroll_widget)
        self.updates_scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.updates_scroll_layout.setSpacing(2)
        self.updates_scroll.setWidget(self.updates_scroll_widget)

        mid_layout.addWidget(self.updates_title)
        mid_layout.addWidget(self.updates_scroll, 1)

        # Column 3: Session Activity Log
        self.panel_right = QFrame()
        self.panel_right.setFrameShape(QFrame.Shape.StyledPanel)
        self.panel_right.setStyleSheet(panel_style)
        right_layout = QVBoxLayout(self.panel_right)
        right_layout.setContentsMargins(8, 6, 8, 6)
        right_layout.setSpacing(4)

        self.history_title = QLabel("RECENT ACTIVITY")
        self.history_scroll = QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.history_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_scroll.verticalScrollBar().setStyleSheet(scrollbar_style)

        self.history_scroll_widget = QWidget()
        self.history_scroll_widget.setStyleSheet("background: transparent;")
        self.history_scroll_layout = QVBoxLayout(self.history_scroll_widget)
        self.history_scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.history_scroll_layout.setSpacing(4)
        self.history_scroll.setWidget(self.history_scroll_widget)

        right_layout.addWidget(self.history_title)
        right_layout.addWidget(self.history_scroll, 1)
        
        self.update_history_display()

        # Add panels to idle layout
        idle_layout.addWidget(self.panel_left, 1)
        idle_layout.addWidget(self.panel_mid, 1)
        idle_layout.addWidget(self.panel_right, 1)

        # QTimer for quotes rotation
        self.quote_timer = QTimer(self)
        self.quote_timer.timeout.connect(self.rotate_quote)
        self.quote_timer.start(10000)

        # --- VIEW 1: ACTIVE JOB STATE ---
        self.active_widget = QWidget()
        active_layout = QVBoxLayout(self.active_widget)
        active_layout.setContentsMargins(0, 0, 0, 0)
        active_layout.setSpacing(0)

        # --- ACTIVE 2.0 LAYOUT (Now Permanent) ---
        self.active_2_0_widget = QWidget()
        active_2_0_layout = QVBoxLayout(self.active_2_0_widget)
        active_2_0_layout.setContentsMargins(5, 5, 5, 5)
        active_2_0_layout.setSpacing(6)

        # Header card/frame for game info
        self.game_info_card = QFrame()
        game_info_layout = QVBoxLayout(self.game_info_card)
        game_info_layout.setContentsMargins(10, 8, 10, 8)
        game_info_layout.setSpacing(2)

        self.game_title_label = QLabel("Installing Game...")
        self.game_title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.game_title_label.setWordWrap(True)
        game_info_layout.addWidget(self.game_title_label)

        active_2_0_layout.addWidget(self.game_info_card)

        # Stage cards
        # 1. Download Card
        self.dl_card = QFrame()
        dl_card_layout = QHBoxLayout(self.dl_card)
        dl_card_layout.setContentsMargins(12, 6, 12, 6)
        self.dl_text_2_0 = QLabel("Downloading Game Files")
        self.dl_badge_2_0 = QLabel("Pending")
        self.dl_badge_2_0.setMinimumWidth(60)
        self.dl_badge_2_0.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dl_card_layout.addWidget(self.dl_text_2_0, 1)
        dl_card_layout.addWidget(self.dl_badge_2_0)
        active_2_0_layout.addWidget(self.dl_card)

        # 2. Achievements Card
        self.ach_card = QFrame()
        ach_card_layout = QHBoxLayout(self.ach_card)
        ach_card_layout.setContentsMargins(12, 6, 12, 6)
        self.ach_text_2_0 = QLabel("Generating Achievements")
        self.ach_badge_2_0 = QLabel("Pending")
        self.ach_badge_2_0.setMinimumWidth(60)
        self.ach_badge_2_0.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ach_card_layout.addWidget(self.ach_text_2_0, 1)
        ach_card_layout.addWidget(self.ach_badge_2_0)
        active_2_0_layout.addWidget(self.ach_card)

        # 3. DRM Card
        self.drm_card = QFrame()
        drm_card_layout = QHBoxLayout(self.drm_card)
        drm_card_layout.setContentsMargins(12, 6, 12, 6)
        self.drm_text_2_0 = QLabel("Removing Steam DRM")
        self.drm_badge_2_0 = QLabel("Pending")
        self.drm_badge_2_0.setMinimumWidth(60)
        self.drm_badge_2_0.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drm_card_layout.addWidget(self.drm_text_2_0, 1)
        drm_card_layout.addWidget(self.drm_badge_2_0)
        active_2_0_layout.addWidget(self.drm_card)

        active_2_0_layout.addStretch()

        active_layout.addWidget(self.active_2_0_widget)

        self.layout.addWidget(self.idle_widget)
        self.layout.addWidget(self.active_widget)

        # Set to Idle by default
        self.layout.setCurrentIndex(0)

    def rotate_quote(self):
        # Choose a quote tuple different from the current one
        current_text = self.quote_label.text()
        available_quotes = [q for q in self.quotes if q[0] != current_text]
        if available_quotes:
            quote, source = random.choice(available_quotes)
            self.quote_label.setText(quote)
            self.quote_source_label.setText(f"— {source}")

    def update_stats(self):
        if not hasattr(self.main_window, "game_manager") or not self.main_window.game_manager:
            return

        settings = self.main_window.settings
        gm = self.main_window.game_manager
        games = gm.games
        total_games = len(games)

        # If scan is currently running and no games have been populated yet
        is_scanning = getattr(gm, "is_scanning", False)
        if total_games == 0 and is_scanning:
            self.total_games_label.setText("Library Size: Scanning...")
            self.updates_label.setText("Updates: Scanning...")
            while self.updates_scroll_layout.count():
                child = self.updates_scroll_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            lbl = QLabel("Scanning installed games...")
            lbl.setStyleSheet("color: #888888; font-style: italic; font-size: 9pt;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.updates_scroll_layout.addWidget(lbl)
            return

        # Only include updates for games NOT excluded from "Update All"
        games_with_updates = [
            g for g in games
            if g.get("update_status") == "update_available"
            and not settings.value(
                f"exclude_from_update_all/{g.get('appid', '')}", False, type=bool
            )
        ]
        total_updates = len(games_with_updates)

        self.total_games_label.setText(f"Library Size: {total_games} games")
        self.updates_label.setText(f"Updates: {total_updates} available")

        # Clear existing updates list
        while self.updates_scroll_layout.count():
            child = self.updates_scroll_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if total_updates == 0:
            lbl = QLabel("All games up-to-date")
            lbl.setStyleSheet("color: #888888; font-style: italic; font-size: 9pt;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.updates_scroll_layout.addWidget(lbl)
        else:
            for g in games_with_updates:
                name = g.get("game_name", "Unknown Game")
                appid = str(g.get("appid", ""))
                from utils.dlc_helpers import is_dlc_only_mode
                if appid and is_dlc_only_mode(appid):
                    name = f"{name} [DLC MODE]"

                # Make it look like a little card/row
                row = QFrame()
                row.setStyleSheet("""
                    QFrame {
                        background-color: rgba(255, 255, 255, 5);
                        border-radius: 4px;
                    }
                    QFrame:hover {
                        background-color: rgba(255, 255, 255, 15);
                    }
                """)
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(6, 4, 6, 4)
                
                icon = QLabel("📦")
                icon.setStyleSheet("background: transparent; border: none;")
                
                lbl = QLabel(name)
                lbl.setStyleSheet("color: #FFFFFF; font-size: 9pt; background: transparent; border: none;")
                lbl.setWordWrap(True)
                
                row_layout.addWidget(icon)
                row_layout.addWidget(lbl, 1)
                
                self.updates_scroll_layout.addWidget(row)
        self.updates_scroll_layout.addStretch()

    def add_history_entry(self, entry):
        from utils.history_cache import get_history_cache
        get_history_cache().add_entry(entry)
        self.update_history_display()

    def update_history_display(self):
        from utils.history_cache import get_history_cache
        history = get_history_cache().get_history()

        while self.history_scroll_layout.count():
            child = self.history_scroll_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not history:
            lbl = QLabel("No recent installation activity")
            lbl.setStyleSheet("color: #888888; font-style: italic; font-size: 9pt;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history_scroll_layout.addWidget(lbl)
        else:
            for entry in history:
                t = entry.get("timestamp", 0)
                from datetime import datetime
                time_str = datetime.fromtimestamp(t).strftime('%H:%M')

                game_name = entry.get('game_name', 'Unknown')
                appid = str(entry.get('appid', ''))
                from utils.dlc_helpers import is_dlc_only_mode
                if appid and is_dlc_only_mode(appid):
                    game_name = f"{game_name} [DLC MODE]"

                success = entry.get("success", True)
                if not success:
                    stat_text = "<span style='color: #E74C3C;'>Installation Failed</span>"
                else:
                    dl_size = entry.get("download_size", 0)
                    if dl_size > 0:
                        size_str = self._format_size(dl_size)
                        dur_str = self._format_duration(entry.get("download_duration", 0))
                        speed_str = self._format_speed(entry.get("avg_speed", 0))
                        stat_text = f"<span style='color: #2ECC71;'>Success</span> • {size_str} in {dur_str} ({speed_str})"
                    else:
                        stat_text = "<span style='color: #2ECC71;'>Success</span> • Zip file"

                ach_status = entry.get("ach_status", "Skipped")
                steamless_status = entry.get("steamless_status", "Skipped")

                html = f"""
                <div style="margin-bottom: 2px;">
                    <span style="color: #FFFFFF; font-weight: bold; font-size: 9pt;">{game_name}</span>
                    <span style="color: #888888; font-size: 8pt; float: right;">[{time_str}]</span>
                    <br/>
                    <span style="color: #DDDDDD; font-size: 8pt;">{stat_text}</span>
                    <br/>
                    <span style="color: #AAAAAA; font-size: 8pt;">Ach: {ach_status} • DRM: {steamless_status}</span>
                </div>
                """
                lbl = QLabel()
                lbl.setTextFormat(Qt.TextFormat.RichText)
                lbl.setText(html)
                lbl.setWordWrap(True)
                lbl.setStyleSheet("border: none; background: transparent;")

                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setFrameShadow(QFrame.Shadow.Sunken)
                line.setStyleSheet("background-color: rgba(255, 255, 255, 0.05); border: none; height: 1px;")

                self.history_scroll_layout.addWidget(lbl)
                self.history_scroll_layout.addWidget(line)
        self.history_scroll_layout.addStretch()

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes <= 0:
            return "0 B"
        size_name = ("B", "KB", "MB", "GB", "TB")
        import math
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"

    @staticmethod
    def _format_duration(duration_seconds: float) -> str:
        if duration_seconds < 60:
            return f"{int(duration_seconds)}s"
        minutes = int(duration_seconds // 60)
        seconds = int(duration_seconds % 60)
        return f"{minutes}m {seconds}s"

    @staticmethod
    def _format_speed(speed_bps: float) -> str:
        if speed_bps < 1024:
            return f"{speed_bps:.2f} B/s"
        if speed_bps < 1024**2:
            return f"{(speed_bps / 1024):.2f} KB/s"
        return f"{(speed_bps / 1024**2):.2f} MB/s"

    def update_style(self):
        accent = self.main_window.accent_color or "#C06C84"
        accent_style = f"color: {accent};"

        title_style = f"font-weight: bold; font-size: 8pt; {accent_style} border: none; background: transparent;"
        self.stats_title.setStyleSheet(title_style)
        self.updates_title.setStyleSheet(title_style)
        self.history_title.setStyleSheet(title_style)
        self.total_games_label.setStyleSheet("font-weight: normal; font-size: 9pt; color: #E0E0E0;")
        self.updates_label.setStyleSheet("font-weight: normal; font-size: 9pt; color: #E0E0E0;")
        self.separator.setStyleSheet(f"background-color: {accent}; border: none;")
        self.quote_label.setStyleSheet("font-style: italic; font-size: 9pt; color: #FFFFFF;")
        self.quote_source_label.setStyleSheet("font-size: 8pt; color: #888888;")

        self.game_title_label.setStyleSheet(f"font-weight: bold; font-size: 11pt; {accent_style} border: none; background: transparent;")

        # 2.0 active layout styling
        if hasattr(self, "game_info_card") and self.game_info_card:
            self.game_info_card.setStyleSheet("""
                QFrame {
                    background-color: rgba(30, 30, 30, 120);
                    border: 1px solid rgba(255, 255, 255, 12);
                    border-radius: 6px;
                }
            """)
        if hasattr(self, "dl_text_2_0") and self.dl_text_2_0:
            self.dl_text_2_0.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: bold; border: none; background: transparent;")
        if hasattr(self, "ach_text_2_0") and self.ach_text_2_0:
            self.ach_text_2_0.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: bold; border: none; background: transparent;")
        if hasattr(self, "drm_text_2_0") and self.drm_text_2_0:
            self.drm_text_2_0.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: bold; border: none; background: transparent;")

        # Re-apply 2.0 stages style if statuses exist
        if hasattr(self, "_stage_statuses"):
            for stage, val in self._stage_statuses.items():
                if isinstance(val, tuple):
                    self.set_stage_status(stage, val[0], val[1])
                else:
                    self.set_stage_status(stage, val)

    def update_stage_style(self, icon_label: QLabel, status: str):
        # Green for completed, yellow/orange for active, red for error, gray/accent for pending/skipped
        if status == "✓":
            color = "#2ECC71"  # Nice flat green
        elif status == "▶":
            color = "#F1C40F"  # Nice flat yellow
        elif status == "✗":
            color = "#E74C3C"  # Nice flat red
        elif status == "~":
            color = "#95A5A6"  # Dim gray
        else:  # "○"
            color = "#7F8C8D"  # Darker gray

        icon_label.setText(status)
        icon_label.setFixedWidth(25)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(f"font-weight: bold; font-size: 11pt; color: {color};")

    def update_2_0_stage_style(self, card_widget: QFrame, badge_widget: QLabel, status: str, count: Optional[int] = None):
        accent = self.main_window.accent_color or "#C06C84"
        
        def hex_to_rgba(hex_color, alpha):
            hex_color = hex_color.lstrip('#')
            if len(hex_color) == 3:
                hex_color = ''.join([c*2 for c in hex_color])
            try:
                r = int(hex_color[0:2], 16)
                g = int(hex_color[2:4], 16)
                b = int(hex_color[4:6], 16)
                return f"rgba({r}, {g}, {b}, {alpha})"
            except Exception:
                return f"rgba(255, 255, 255, {alpha})"

        accent_alpha = hex_to_rgba(accent, 20)

        if status == "completed":
            badge_text = f"Done ({count})" if count is not None else "Done"
            badge_style = "background-color: #2ECC71; color: #FFFFFF; font-weight: bold; font-size: 7pt; border-radius: 4px; padding: 2px 6px; border: none;"
            card_style = "background-color: rgba(46, 204, 113, 15); border: 1px solid rgba(46, 204, 113, 40); border-radius: 6px;"
        elif status == "in_progress":
            badge_text = f"Active ({count})" if count is not None else "Active"
            badge_style = f"background-color: {accent}; color: #000000; font-weight: bold; font-size: 7pt; border-radius: 4px; padding: 2px 6px; border: none;"
            card_style = f"background-color: {accent_alpha}; border: 1px solid {accent}; border-radius: 6px;"
        elif status == "error":
            badge_text = "Failed"
            badge_style = "background-color: #E74C3C; color: #FFFFFF; font-weight: bold; font-size: 8pt; border-radius: 4px; padding: 2px 8px; border: none;"
            card_style = "background-color: rgba(231, 76, 60, 15); border: 1px solid rgba(231, 76, 60, 40); border-radius: 6px;"
        elif status == "skipped":
            badge_text = "Skipped"
            badge_style = "background-color: rgba(255, 255, 255, 12); color: #888888; font-weight: bold; font-size: 8pt; border-radius: 4px; padding: 2px 8px; border: none;"
            card_style = "background-color: rgba(255, 255, 255, 3); border: 1px solid rgba(255, 255, 255, 6); border-radius: 6px;"
        elif status == "skipped_linux":
            badge_text = "Linux Skip"
            badge_style = "background-color: rgba(255, 255, 255, 12); color: #888888; font-weight: bold; font-size: 8pt; border-radius: 4px; padding: 2px 8px; border: none;"
            card_style = "background-color: rgba(255, 255, 255, 3); border: 1px solid rgba(255, 255, 255, 6); border-radius: 6px;"
        elif status == "skipped_no_achievements":
            badge_text = "N/A"
            badge_style = "background-color: rgba(255, 255, 255, 12); color: #888888; font-weight: bold; font-size: 7pt; border-radius: 4px; padding: 2px 6px; border: none;"
            card_style = "background-color: rgba(255, 255, 255, 3); border: 1px solid rgba(255, 255, 255, 6); border-radius: 6px;"
        else:  # "pending"
            badge_text = "Queued"
            badge_style = "background-color: rgba(255, 255, 255, 20); color: #BBBBBB; font-weight: bold; font-size: 8pt; border-radius: 4px; padding: 2px 8px; border: none;"
            card_style = "background-color: rgba(255, 255, 255, 5); border: 1px solid rgba(255, 255, 255, 10); border-radius: 6px;"

        badge_widget.setText(badge_text)
        badge_widget.setStyleSheet(badge_style)
        card_widget.setStyleSheet(card_style)

    def set_stage_status(self, stage: str, status: str, count: Optional[int] = None):
        if not hasattr(self, "_stage_statuses"):
            self._stage_statuses = {}
        self._stage_statuses[stage] = (status, count)

        if stage == "download":
            if hasattr(self, "dl_card") and self.dl_card:
                self.update_2_0_stage_style(self.dl_card, self.dl_badge_2_0, status, count)
        elif stage == "achievements":
            if hasattr(self, "ach_card") and self.ach_card:
                self.update_2_0_stage_style(self.ach_card, self.ach_badge_2_0, status, count)
        elif stage == "steamless":
            if hasattr(self, "drm_card") and self.drm_card:
                self.update_2_0_stage_style(self.drm_card, self.drm_badge_2_0, status, count)

    def reset_stages(self):
        self.set_stage_status("download", "pending")
        
        # Hide achievements status card if achievements generation is disabled in Settings
        from utils.settings import get_settings
        settings = get_settings()
        gen_ach = settings.value("generate_achievements", False, type=bool)
        
        if gen_ach:
            if hasattr(self, "ach_card") and self.ach_card:
                self.ach_card.show()
            self.set_stage_status("achievements", "pending")
        else:
            if hasattr(self, "ach_card") and self.ach_card:
                self.ach_card.hide()
            self.set_stage_status("achievements", "skipped")
            
        self.set_stage_status("steamless", "pending")

    def show_idle(self):
        self.layout.setCurrentIndex(0)
        self.update_stats()
        if hasattr(self.main_window, "_update_tool_update_visibility"):
            self.main_window._update_tool_update_visibility()

    def show_active_job(self, game_name: str = "Installing Game..."):
        self.game_title_label.setText(game_name)
        self.layout.setCurrentIndex(1)
        if hasattr(self.main_window, "_update_tool_update_visibility"):
            self.main_window._update_tool_update_visibility()


class MainWindow(QMainWindow):

    """Main application window."""

    refresh_system_status_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.refresh_system_status_signal.connect(self.refresh_system_status)
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
        self.sls_status_value = None
        self.slssteam_status_value = None
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
        self._tool_update_available_flag = False
        self._tool_update_check_running = False
        # Track appids whose manifests have already been auto-fetched in this session
        self._autofetched_appids: set = set()

        self._setup_window_properties()
        self._initialize_managers()
        self._setup_ui()
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
        
        def run_boot_checks():
            run_boot_update_check()
            run_boot_config_check()
            # Safely refresh system status labels on the main window dashboard
            self.refresh_system_status_signal.emit()

        threading.Thread(target=run_boot_checks, daemon=True).start()

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

        self.accent_color = self.settings.value("accent_color", "#C06C84")
        self.background_color = self.settings.value("background_color", "#000000")

        self.task_manager = TaskManager(self)
        self.ui_state = UIStateManager(self)
        self.job_queue = JobQueueManager(self)
        self.game_manager = GameManager(self)

        # Initialize Web Server Manager
        self.web_command_queue = Queue()
        self.web_server_manager = WebServerManager(self, self.web_command_queue)
        self.web_command_timer = QTimer(self)
        self.web_command_timer.timeout.connect(self._process_web_commands)
        # Timer is started/stopped dynamically when web server starts/stops

        logger.info("Starting initial game library scan...")
        self.game_manager.scan_complete.connect(self._on_initial_scan_complete)
        
        # Connect game manager signals to update the dashboard's elements
        self.game_manager.library_updated.connect(self.update_dashboard_elements)
        self.game_manager.game_update_status_changed.connect(
            lambda appid, status: self.update_dashboard_elements()
        )
        self.game_manager.all_updates_checked.connect(self.update_dashboard_elements)
        self.game_manager.all_updates_checked.connect(self.refresh_hubcap_stats)
        self.game_manager.all_updates_checked.connect(self._on_boot_autofetch_manifests)

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
                self.web_server_manager.start(port=port)
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
        
        # 1. Check if the local web server manager is running in this GUI instance
        if self.web_server_manager and self.web_server_manager.is_running():
            port = self.web_server_manager.server.port
            self.web_ui_status_value.setText(f"http://{get_local_ip()}:{port}")
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

        logger.info(f"Initial game library scan completed ({games_found} games found). Triggering immediate game updates check.")
        if self.game_manager:
            self.game_manager.check_game_updates_async()

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


    def _on_boot_autofetch_manifests(self) -> None:
        """Sequential background fetch of update manifests whenever all_updates_checked fires.

        Runs on the first batch check (boot) and also on any subsequent check triggered by
        the periodic timer, ensuring newly-detected updates get their manifest downloaded
        automatically without requiring a tool restart.

        When Smart Update Mode is enabled, routes through SmartUpdateTask (PICS + generate
        endpoint) and skips all Hubcap status/timestamp/Stage-2 verification checks.
        """
        if not self.settings.value("autofetch_manifests_on_boot", False, type=bool):
            return

        # Guard: only mark boot done after the first run, but keep running for periodic checks
        if not self._autofetch_on_boot_done:
            self._autofetch_on_boot_done = True

        smart_mode = True
        if smart_mode:
            logger.info("[Auto-fetch] Smart Update Mode is ON — using SmartUpdateTask path")

        from utils.helpers import get_base_path
        games_to_fetch = []
        for game in self.game_manager.games:
            appid = game.get("appid")
            status = game.get("update_status")
            if appid and appid not in ("0", "N/A", "unknown") and status == "update_available":
                # Check if this game is excluded from background auto-update manifest
                if not self.settings.value(f"auto_update_manifest/{appid}", True, type=bool):
                    logger.debug(f"Auto-fetch background: AppID {appid} is excluded from manifest auto-fetch.")
                    continue
                # Skip if already fetched this session and the file is still fresh
                fpath = get_base_path() / "hubcap_manifests" / f"accela_fetch_{appid}.zip"
                is_fresh = self.settings.value(f"manifest_is_fresh/{appid}", False, type=bool)
                if fpath.exists() and is_fresh:
                    continue
                if appid in self._autofetched_appids:
                    # Already attempted in this session but file is not fresh — retry
                    self._autofetched_appids.discard(appid)
                games_to_fetch.append((appid, game.get("game_name", "Unknown")))
                self._autofetched_appids.add(appid)

        if not games_to_fetch:
            logger.info("Auto-fetch: no update manifests need downloading.")
            return

        logger.info(f"Auto-fetch on boot: starting background fetch for {len(games_to_fetch)} games.")

        from utils.task_runner import TaskRunner
        self._autofetch_runner = TaskRunner(self)

        def run_downloads():
            from core import morrenus_api
            from utils.manifest_verifier import verify_hubcap_freshness

            for appid, name in games_to_fetch:
                try:
                    if smart_mode:
                        # ── Smart Update path: PICS + /generate/appmanifest ──────────────
                        # No status check, no timestamp check, no Stage 2 — trust PICS.
                        logger.info(f"[Auto-fetch Smart] Processing {name} ({appid})")
                        from managers.depot_key_manager import DepotKeyManager
                        from core.tasks.smart_update_task import SmartUpdateTask

                        dkm = DepotKeyManager()
                        if not dkm.has_depot_keys(appid):
                            logger.warning(
                                f"[Auto-fetch Smart] {name} ({appid}): no cached depot keys — "
                                "falling back to full zip fetch"
                            )
                            # Fall through to old path below
                        else:
                            # Run SmartUpdateTask synchronously (we're already in a worker thread)
                            task = SmartUpdateTask(appid, name)
                            _smart_result = {"game_data": None, "fallback": None}

                            task.progress.connect(lambda msg: logger.info(msg))
                            task.finished.connect(lambda gd: _smart_result.__setitem__("game_data", gd))
                            task.needs_full_zip.connect(lambda r: _smart_result.__setitem__("fallback", r))
                            task.error.connect(lambda e: logger.error(f"[Auto-fetch Smart] Error: {e}"))

                            task._execute()  # Call directly since we're in worker thread

                            if _smart_result["game_data"]:
                                self.settings.setValue(f"manifest_is_fresh/{appid}", True)
                                gd = _smart_result["game_data"]
                                if gd.get("buildid"):
                                    self.settings.setValue(f"fetched_buildid/{appid}", gd["buildid"])
                                logger.info(f"[Auto-fetch Smart] SUCCESS for {name} ({appid})")
                            elif _smart_result["fallback"]:
                                logger.info(
                                    f"[Auto-fetch Smart] {name} ({appid}) needs full zip: "
                                    f"{_smart_result['fallback']} — falling back"
                                )
                                # Fall through to old endpoint below by not continuing
                            else:
                                logger.warning(f"[Auto-fetch Smart] {name} ({appid}): no result from SmartUpdateTask")
                            
                            if not _smart_result["fallback"]:
                                import time
                                time.sleep(2.0)
                                continue  # Do not fall through to old path if smart result was obtained or a fallback signal fired

                    # ── Classic path: Hubcap /manifest/{appid} ───────────────────────
                    logger.info(f"Auto-fetch background: checking Hubcap manifest status for {name} ({appid})")
                    status_res = morrenus_api.get_manifest_status(appid)
                    if status_res and isinstance(status_res, dict) and not status_res.get("error"):
                        needs_up = status_res.get("needs_update", False)
                        up_in_prog = status_res.get("update_in_progress", False)

                        # Default check: skip if Hubcap reports needs_update or update_in_progress
                        if needs_up or up_in_prog:
                            logger.info(
                                f"Auto-fetch background: Hubcap manifest for {name} ({appid}) is not ready "
                                f"(needs_update={needs_up}, in_progress={up_in_prog}). Skipping download."
                            )
                            import time
                            time.sleep(2.0)
                            continue

                        # Refined check removed because it is deprecated

                    fpath, error = morrenus_api.download_manifest(appid)
                    if fpath and not error:
                        # Stage 2 Post-Check: Parse zip and verify extracted manifest IDs against Steam
                        from core.tasks.process_zip_task import ProcessZipTask
                        from utils.manifest_verifier import verify_extracted_zip_manifest

                        try:
                            parsed_zip = ProcessZipTask().run(fpath)
                            is_valid, post_reason = verify_extracted_zip_manifest(appid, parsed_zip, is_update=True)
                            if not is_valid:
                                logger.warning(
                                    f"Auto-fetch background: Stage 2 Post-Check failed for {name} ({appid}): {post_reason}. Discarding downloaded manifest zip."
                                )
                                import time
                                time.sleep(2.0)
                                continue
                        except Exception as p_ex:
                            logger.error(f"Auto-fetch background: error during Stage 2 zip processing for {name} ({appid}): {p_ex}")
                            import time
                            time.sleep(2.0)
                            continue

                        self.settings.setValue(f"manifest_is_fresh/{appid}", True)
                        latest_id = self.settings.value(f"latest_steam_manifest_id/{appid}", "", type=str)
                        if latest_id:
                            self.settings.setValue(f"fetched_manifest_id/{appid}", latest_id)
                        logger.info(f"Auto-fetch background: successfully downloaded and verified manifest for {name} ({appid})")
                    else:
                        logger.warning(f"Auto-fetch background: failed for {name} ({appid}): {error}")
                    
                    import time
                    time.sleep(2.0)
                except Exception as ex:
                    logger.error(f"Auto-fetch background error for {name} ({appid}): {ex}", exc_info=True)
                    import time
                    time.sleep(2.0)

        self._autofetch_runner.run(run_downloads)


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
            logger.info("Running periodic game update check")
            # On a periodic check, reset 'up_to_date' games so they get re-verified.
            # 'update_available' games are left as-is (status won't change until downloaded).
            self.game_manager.reset_up_to_date_for_recheck()
            self.game_manager.check_game_updates_async()
        self.check_tool_updates()

    def _setup_ui(self) -> None:
        """Setup the main UI components."""
        self.central_widget = QWidget()
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
            def setText(self, text):
                self.pager.set_status(text)
            def setStyleSheet(self, style):
                pass

        self.drop_text_label = DropTextLabelWrapper(self.status_pager)

        # Dashboard container widget
        self.dashboard_widget = QWidget()
        self.dashboard_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.dashboard_widget.setMinimumHeight(70)
        self.dashboard_widget.setMaximumHeight(85)
        
        dash_layout = QHBoxLayout(self.dashboard_widget)
        dash_layout.setContentsMargins(15, 5, 15, 5)
        dash_layout.setSpacing(10)
        
        card_style = """
            QWidget {
                background-color: rgba(30, 30, 30, 120);
                border: 1px solid rgba(255, 255, 255, 12);
                border-radius: 6px;
            }
        """
        
        # Hubcap API Stats Card (Left)
        self.hubcap_stats_card = QWidget()
        self.hubcap_stats_card.setStyleSheet(card_style)
        hubcap_layout = QVBoxLayout(self.hubcap_stats_card)
        hubcap_layout.setContentsMargins(10, 4, 10, 4)
        hubcap_layout.setSpacing(1)
        
        self.hubcap_title = QLabel("HUBCAP API STATS")
        self.hubcap_title.setStyleSheet("color: rgba(255, 255, 255, 120); font-size: 8px; font-weight: bold; border: none; background: transparent;")
        
        usage_row = QHBoxLayout()
        usage_row.setSpacing(4)
        usage_lbl = QLabel("Usage:")
        usage_lbl.setStyleSheet("color: rgba(255, 255, 255, 160); font-size: 11px; border: none; background: transparent;")
        self.usage_value = QLabel("-- / --")
        self.usage_value.setStyleSheet(f"color: {self.accent_color or '#C06C84'}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        reset_lbl = QLabel("(Resets Daily)")
        reset_lbl.setStyleSheet("color: rgba(255, 255, 255, 80); font-size: 8px; border: none; background: transparent;")
        usage_row.addWidget(usage_lbl)
        usage_row.addWidget(self.usage_value)
        usage_row.addWidget(reset_lbl)
        usage_row.addStretch()
        
        expiry_row = QHBoxLayout()
        expiry_row.setSpacing(4)
        expiry_lbl = QLabel("Expiry:")
        expiry_lbl.setStyleSheet("color: rgba(255, 255, 255, 160); font-size: 11px; border: none; background: transparent;")
        self.expiry_value = QLabel("-- days")
        self.expiry_value.setStyleSheet(f"color: {self.accent_color or '#C06C84'}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        expiry_row.addWidget(expiry_lbl)
        expiry_row.addWidget(self.expiry_value)
        expiry_row.addStretch()
        
        hubcap_layout.addWidget(self.hubcap_title)
        hubcap_layout.addLayout(usage_row)
        hubcap_layout.addLayout(expiry_row)
        
        # Update Action Card (Middle)
        self.update_action_card = QWidget()
        self.update_action_card.setStyleSheet(card_style)
        action_layout = QVBoxLayout(self.update_action_card)
        action_layout.setContentsMargins(10, 4, 10, 4)
        action_layout.setSpacing(4)
        
        self.action_title = QLabel("LIBRARY UPDATE")
        self.action_title.setStyleSheet("color: rgba(255, 255, 255, 120); font-size: 8px; font-weight: bold; border: none; background: transparent;")
        
        self.update_all_btn = QPushButton("Update All")
        self.update_all_btn.setMinimumHeight(28)
        self.update_all_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {self.accent_color or '#C06C84'};
                border: 1px solid {self.accent_color or '#C06C84'};
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 15);
            }}
            QPushButton:disabled {{
                border: 1px solid rgba(255, 255, 255, 15);
                color: rgba(255, 255, 255, 60);
            }}
        """)
        self.update_all_btn.clicked.connect(self.run_update_all_flow)
        
        action_layout.addWidget(self.action_title)
        action_layout.addWidget(self.update_all_btn)
        
        # Steam / SLS Status Card (Right)
        self.steam_sls_status_card = QWidget()
        self.steam_sls_status_card.setStyleSheet(card_style)
        status_layout = QVBoxLayout(self.steam_sls_status_card)
        status_layout.setContentsMargins(10, 4, 10, 4)
        status_layout.setSpacing(1)
        
        self.status_card_title = QLabel("STEAM / SLS STATUS")
        self.status_card_title.setStyleSheet("color: rgba(255, 255, 255, 120); font-size: 8px; font-weight: bold; border: none; background: transparent;")
        
        steam_row = QHBoxLayout()
        steam_row.setSpacing(4)
        steam_lbl = QLabel("Steam Updates:")
        steam_lbl.setStyleSheet("color: rgba(255, 255, 255, 160); font-size: 11px; border: none; background: transparent;")
        self.steam_updates_value = QLabel("Checking...")
        self.steam_updates_value.setStyleSheet(f"color: {self.accent_color or '#C06C84'}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        steam_row.addWidget(steam_lbl)
        steam_row.addWidget(self.steam_updates_value)
        steam_row.addStretch()
        
        sls_row = QHBoxLayout()
        sls_row.setSpacing(4)
        sls_lbl = QLabel("SLS Config:")
        sls_lbl.setStyleSheet("color: rgba(255, 255, 255, 160); font-size: 11px; border: none; background: transparent;")
        self.sls_status_value = QLabel("Healthy")
        self.sls_status_value.setStyleSheet(f"color: {self.accent_color or '#C06C84'}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        sls_row.addWidget(sls_lbl)
        sls_row.addWidget(self.sls_status_value)
        sls_row.addStretch()

        if sys.platform == "linux":
            slssteam_row = QHBoxLayout()
            slssteam_row.setSpacing(4)
            slssteam_lbl = QLabel("SLSsteam:")
            slssteam_lbl.setStyleSheet("color: rgba(255, 255, 255, 160); font-size: 11px; border: none; background: transparent;")
            self.slssteam_status_value = QLabel("Checking...")
            self.slssteam_status_value.setStyleSheet(f"color: {self.accent_color or '#C06C84'}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            slssteam_row.addWidget(slssteam_lbl)
            slssteam_row.addWidget(self.slssteam_status_value)
            slssteam_row.addStretch()
        else:
            self.slssteam_status_value = None

        self.web_ui_status_value = None
        
        status_layout.addWidget(self.status_card_title)
        status_layout.addLayout(steam_row)
        status_layout.addLayout(sls_row)
        if self.slssteam_status_value is not None:
            status_layout.addLayout(slssteam_row)
        
        dash_layout.addWidget(self.hubcap_stats_card, 1)
        dash_layout.addWidget(self.update_action_card, 1)
        dash_layout.addWidget(self.steam_sls_status_card, 1)
        
        self.drop_zone_layout.addWidget(self.status_pager)
        self.drop_zone_layout.addWidget(self.dashboard_widget, 2)
        self.main_layout.addWidget(self.drop_zone_container, 1)

    def _create_progress_section(self) -> None:
        """Create the progress bar, controls and speed label."""
        self.progress_container = QWidget()
        self.progress_layout = QVBoxLayout(self.progress_container)
        self.progress_layout.setContentsMargins(20, 5, 20, 5)

        # Active Hubcap Label (visible only when downloading, left-aligned)
        self.active_hubcap_layout = QHBoxLayout()
        self.active_hubcap_layout.setContentsMargins(2, 0, 2, 0)
        self.active_hubcap_label = QLabel("")
        self.active_hubcap_label.setStyleSheet("color: #888888; font-size: 11px; font-weight: bold;")
        self.active_hubcap_label.setVisible(False)
        self.active_hubcap_layout.addWidget(self.active_hubcap_label)
        self.active_hubcap_layout.addStretch()
        self.progress_layout.addLayout(self.active_hubcap_layout)

        self.progress_bar = QProgressBar()
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
        self.bottom_layout.setContentsMargins(5, 5, 5, 5)

        self.ui_state.setup_queue_panel()
        self.bottom_layout.addWidget(self.ui_state.queue_widget, 1)

        self.stacked_terminal_widget = QStackedWidget()

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(5000)  # cap RAM: keep only last 5000 lines
        qt_log_handler.new_record.connect(self.log_output.appendPlainText)
        self.stacked_terminal_widget.addWidget(self.log_output)

        self.simplified_terminal = SimplifiedTerminalWidget(self)
        self.stacked_terminal_widget.addWidget(self.simplified_terminal)

        self.bottom_layout.addWidget(self.stacked_terminal_widget, 1)

        self.layout.addWidget(self.bottom_widget, 3)
        self.ui_state.queue_widget.setVisible(False)



    def update_nerd_mode(self, nerd: Optional[bool] = None) -> None:
        """Update terminal widget display based on nerd mode setting."""
        if nerd is None:
            nerd = self.settings.value("nerd_mode", False, type=bool)
        if self.stacked_terminal_widget:
            if nerd:
                self.stacked_terminal_widget.setCurrentIndex(0)
            else:
                self.stacked_terminal_widget.setCurrentIndex(1)

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
        self.progress_bar.setStyleSheet(
            f"""
            QProgressBar {{
                max-height: 10px;
                border: 1px solid {self.accent_color};
                border-radius: 5px;
                text-align: center;
                color: #FFFFFF;
            }}
            QProgressBar::chunk {{
                background-color: {self.accent_color};
                border-radius: 5px;
            }}
        """
        )

    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        dialog.exec()

    def open_fetch_dialog(self) -> None:
        self.ui_state.fetch_dialog = FetchManifestDialog(self)
        self.ui_state.fetch_dialog.exec()
        self.ui_state.fetch_dialog = None

    def open_workshop_dialog(self) -> None:
        from ui.dialogs.workshop import WorkshopDialog
        dialog = WorkshopDialog(self)
        dialog.exec()

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
        path = Path("/home/deck/.steam/steam/steam.cfg")
        if not path.exists():
            return False
        try:
            lines = path.read_text().splitlines()
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
            return inhibit and force_disable
        except Exception as e:
            logger.error(f"Error reading steam.cfg: {e}")
            return False

    @pyqtSlot()
    def refresh_system_status(self) -> None:
        """Refresh local Steam updates and SLS status labels."""
        if not self.steam_updates_value or not self.sls_status_value:
            return
            
        blocked = self.check_steam_updates_blocked()
        if blocked:
            self.steam_updates_value.setText("Blocked")
            self.steam_updates_value.setStyleSheet(f"color: #ff3333; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        else:
            self.steam_updates_value.setText("Allowed")
            self.steam_updates_value.setStyleSheet(f"color: #33ff33; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            
        # SLS Config Status based on boot check
        import utils.assfixer
        status = utils.assfixer.boot_status
        if status == "optimal":
            self.sls_status_value.setText("Optimal")
            self.sls_status_value.setStyleSheet("color: #44bb44; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        elif status == "needs_fix":
            self.sls_status_value.setText("Action Needed")
            self.sls_status_value.setStyleSheet("color: #ffaa00; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        elif status == "no_config":
            self.sls_status_value.setText("Missing Config")
            self.sls_status_value.setStyleSheet("color: #cc4444; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        elif status == "failed":
            self.sls_status_value.setText("Failed Check")
            self.sls_status_value.setStyleSheet("color: #cc4444; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        elif status == "checking":
            self.sls_status_value.setText("Checking...")
            self.sls_status_value.setStyleSheet("color: #888888; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        else:
            self.sls_status_value.setText("Not Checked")
            self.sls_status_value.setStyleSheet("color: #888888; font-size: 11px; font-weight: bold; border: none; background: transparent;")

        if self.slssteam_status_value is not None:
            from ui.dialogs.settings_sls import get_local_sls_version
            import ui.dialogs.settings_sls as sls_settings
            
            local_ver = get_local_sls_version()
            if local_ver == "Not Installed":
                self.slssteam_status_value.setText("Configure")
                self.slssteam_status_value.setStyleSheet("color: #cc4444; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            else:
                if sls_settings.update_checked and sls_settings.latest_online_version:
                    local_clean = local_ver.strip()
                    if local_clean == "Installed (Version Unknown)" or local_clean != sls_settings.latest_online_version:
                        self.slssteam_status_value.setText("Update!")
                        self.slssteam_status_value.setStyleSheet("color: #ffaa00; font-size: 11px; font-weight: bold; border: none; background: transparent;")
                    else:
                        self.slssteam_status_value.setText("Latest")
                        self.slssteam_status_value.setStyleSheet("color: #44bb44; font-size: 11px; font-weight: bold; border: none; background: transparent;")
                else:
                    self.slssteam_status_value.setText("Latest")
                    self.slssteam_status_value.setStyleSheet("color: #44bb44; font-size: 11px; font-weight: bold; border: none; background: transparent;")

    def refresh_hubcap_stats(self) -> None:
        """Fetch user statistics from Hubcap API asynchronously."""
        # Also refresh Steam and SLS status locally
        self.refresh_system_status()

        if not self.stats_task_runner:
            self.stats_task_runner = TaskRunner(self)
        
        # Set stats text to loading
        if self.usage_value:
            self.usage_value.setText("Loading...")
        if self.expiry_value:
            self.expiry_value.setText("Loading...")
        if hasattr(self, "active_hubcap_label") and self.active_hubcap_label:
            self.active_hubcap_label.setText("Hubcap stats: Loading...")

        worker = self.stats_task_runner.run(get_user_stats)
        worker.finished.connect(self._on_user_stats_loaded)
        worker.error.connect(self._on_user_stats_error)

    def _on_user_stats_loaded(self, stats: dict) -> None:
        """Handle async hubcap stats load success."""
        if not isinstance(stats, dict) or "error" in stats:
            err_msg = stats.get("error", "Unknown error") if isinstance(stats, dict) else "Invalid response"
            logger.warning(f"Failed to load Hubcap user stats: {err_msg}")
            val = "No Key" if "key is not set" in err_msg.lower() else "Error"
            if self.usage_value:
                self.usage_value.setText(val)
            if self.expiry_value:
                self.expiry_value.setText(val)
            if hasattr(self, "active_hubcap_label") and self.active_hubcap_label:
                self.active_hubcap_label.setText(f"Hubcap stats: {val}")
            return

        # Daily usage
        usage = stats.get("daily_usage", 0)
        limit = stats.get("daily_limit", 45)
        if self.usage_value:
            self.usage_value.setText(f"{usage} / {limit}")
        if hasattr(self, "active_hubcap_label") and self.active_hubcap_label:
            self.active_hubcap_label.setText(f"Hubcap stats: {usage} / {limit}")

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
                    expiry_text = f"{days} day(s)"
            except Exception as e:
                logger.error(f"Failed to parse expiry date '{expires_str}': {e}")
                expiry_text = "Unknown"
        else:
            expiry_text = "Never"

        if self.expiry_value:
            self.expiry_value.setText(expiry_text)

    def _on_user_stats_error(self, err_tuple: tuple) -> None:
        """Handle async hubcap stats load error."""
        logger.error(f"Async user stats load failed: {err_tuple[1]}")
        if self.usage_value:
            self.usage_value.setText("Error")
        if self.expiry_value:
            self.expiry_value.setText("Error")
        if hasattr(self, "active_hubcap_label") and self.active_hubcap_label:
            self.active_hubcap_label.setText("Hubcap stats: Error")

    def run_update_all_flow(self) -> None:
        """Flow for updating all games that have update_available status."""
        if not self.game_manager:
            return

        games = self.game_manager.get_all_games()
        updateable_games = []
        for g in games:
            if g.get("update_status") == "update_available":
                appid = str(g.get("appid", ""))
                if self.settings.value(f"exclude_from_update_all/{appid}", False, type=bool):
                    continue
                updateable_games.append(g)

        if not updateable_games:
            QMessageBox.information(
                self,
                "No Updates Available",
                "All games in your library are up to date!",
            )
            return

        # Enqueue all updates directly on a background thread (no intermediate dialog)
        from ui.dialogs.gamelibrary import GameLibraryDialog
        import threading

        # Create a temporary GameLibraryDialog-like enqueue helper
        # by reusing the standalone enqueue logic
        def _do_update_all():
            from utils.helpers import get_base_path
            from core import morrenus_api as _api
            from utils.settings import get_settings
            from core.tasks.process_zip_task import ProcessZipTask
            from ui.dialogs.gamelibrary import format_game_display_name
            import json

            settings = get_settings()
            queued = 0
            for game_data in updateable_games:
                appid = str(game_data.get("appid", "0"))
                name = format_game_display_name(game_data)
                update_status = game_data.get("update_status")
                try:
                    local_path = None
                    fpath = get_base_path() / "hubcap_manifests" / f"accela_fetch_{appid}.zip"
                    is_fresh = settings.value(f"manifest_is_fresh/{appid}", False, type=bool)
                    if fpath.exists() and (update_status != "update_available" or is_fresh):
                        local_path = str(fpath)
                    
                    parsed_data = None
                    from managers.depot_key_manager import DepotKeyManager
                    dkm = DepotKeyManager()
                    
                    # 1. Try Smart Update Path
                    if dkm.has_depot_keys(appid):
                        from core.tasks.smart_update_task import SmartUpdateTask
                        task = SmartUpdateTask(appid, name)
                        parsed_data = task.run()
                        if parsed_data:
                            local_path = parsed_data.get("zip_path", local_path)
                    
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
                            logger.info(f"Update All: Fetching classic manifest for {name}")
                            if local_path and Path(local_path).exists():
                                Path(local_path).unlink() # Delete bad/lua-less zip
                            fpath, error = _api.download_manifest(appid)
                            if error or not fpath:
                                logger.warning(f"Update All: manifest download failed for {name}: {error}")
                                continue
                            local_path = str(fpath)
                            settings.setValue(f"manifest_is_fresh/{appid}", True)

                        zip_task = ProcessZipTask()
                        try:
                            parsed_data = zip_task.run(local_path)
                        except Exception as e:
                            logger.error(f"Update All: Failed to process zip for {name}: {e}")
                            continue
                    metadata = {
                        "appid": appid,
                        "library_path": game_data.get("library_path"),
                        "install_path": game_data.get("install_path"),
                        "game_name": name,
                    }

                    if parsed_data and parsed_data.get("depots"):
                        depots = parsed_data.get("depots")
                        selected_depots = None
                        smart_active = settings.value("smart_depot_selection", False, type=bool)
                        val = settings.value(f"depot_selection/{appid}", "", type=str)
                        if smart_active and val:
                            try:
                                data = json.loads(val)
                                cached_selected = data.get("selected", [])
                                cached_all = data.get("all_available", [])
                                if not any(d not in cached_all for d in depots):
                                    selected_depots = [d for d in cached_selected if d in depots]
                            except Exception:
                                pass
                        if not selected_depots:
                            auto_skip = settings.value("auto_skip_single_choice", False, type=bool)
                            if auto_skip or len(depots) == 1:
                                selected_depots = list(depots.keys())
                            else:
                                logger.info(f"Update All: skipping {name} — depot selection required")
                                continue
                        metadata["selected_depots_list"] = selected_depots

                    self.job_queue.add_job(local_path, metadata)
                    queued += 1
                    logger.info(f"Update All queued: {name}")
                except Exception as e:
                    logger.error(f"Update All failed for {name}: {e}", exc_info=True)

            logger.info(f"Update All: queued {queued} of {len(updateable_games)} games.")

        threading.Thread(target=_do_update_all, daemon=True).start()

    def update_dashboard_elements(self) -> None:
        """Dynamically update "Update All" button text based on pending updates count."""
        if not self.game_manager or not self.update_all_btn:
            return

        games = self.game_manager.get_all_games()
        updateable_games = []
        for g in games:
            if g.get("update_status") == "update_available":
                appid = str(g.get("appid", ""))
                if self.settings.value(f"exclude_from_update_all/{appid}", False, type=bool):
                    continue
                updateable_games.append(g)
        count = len(updateable_games)

        if count > 0:
            self.update_all_btn.setText(f"🔄 Update All ({count})")
            self.update_all_btn.setEnabled(True)
        else:
            self.update_all_btn.setText("🔄 Update All")
            self.update_all_btn.setEnabled(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if not event.mimeData().hasUrls():
            return

        urls = event.mimeData().urls()
        has_zip = any(
            url.isLocalFile() and url.toLocalFile().lower().endswith(".zip")
            for url in urls
        )

        if has_zip:
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        new_jobs = [
            url.toLocalFile()
            for url in urls
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".zip")
        ]

        if not new_jobs:
            return

        logger.info(f"Added {len(new_jobs)} file(s) to the queue via drag-drop.")
        for job_path in new_jobs:
            self.job_queue.add_job(job_path)

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
            from utils.update_status_cache import get_update_cache
            get_update_cache().save()  # Force synchronous save of status cache before exit
            MainWindow._cleanup_logging()
            self.task_manager.cleanup()
            self.job_queue.clear()
            self.game_manager.cleanup()
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

        super().closeEvent(event)

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

        def _extract_semver(raw: str) -> str:
            """Strip build-date prefix (e.g. '20260608+ASSella-') returning just the version tag."""
            # Format: YYYYMMDD+ASSella-<version>  OR  <version>
            if "+ASSella-" in raw:
                return raw.split("+ASSella-", 1)[1].strip()
            return raw.strip()

        def _parse_version(v_str: str) -> tuple:
            import re as _re
            v_str = v_str.lstrip('v').strip()
            parts = v_str.split('-')
            main_part = parts[0]
            main_numbers = []
            for num in main_part.split('.'):
                try:
                    main_numbers.append(int(num))
                except ValueError:
                    main_numbers.append(0)
            
            while len(main_numbers) < 3:
                main_numbers.append(0)
                
            pre_release_val = 0  # 0 means release version
            pre_release_num = 0
            
            if len(parts) > 1:
                pre_tag = parts[1].lower()
                pre_release_val = -1
                match = _re.search(r'\d+$', pre_tag)
                if match:
                    try:
                        pre_release_num = int(match.group(0))
                    except ValueError:
                        pre_release_num = 0
            
            return tuple(main_numbers) + (pre_release_val, pre_release_num)

        def _check_sync():
            try:
                # Check beta branch if local version is pre-release/beta
                local_clean = _extract_semver(app_version)
                if "alpha" in local_clean.lower():
                    branch = "alpha"
                elif any(x in local_clean.lower() for x in ("beta", "rc")):
                    branch = "beta"
                else:
                    branch = "main"
                url = f"https://raw.githubusercontent.com/niwia/ASSella/{branch}/src/res/version"
                logger.info(f"Checking for tool updates from branch: {branch}")
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
                            QMetaObject.invokeMethod(
                                self,
                                "_on_tool_update_available",
                                Qt.ConnectionType.QueuedConnection,
                                Q_ARG(str, remote_clean)
                            )
            except Exception as e:
                logger.warning(f"Failed to check tool updates from GitHub: {e}")
            finally:
                self._tool_update_check_running = False

        t = threading.Thread(target=_check_sync, daemon=True)
        t.start()

    @pyqtSlot(str)
    def _on_tool_update_available(self, remote_version: str = "") -> None:
        """Slot triggered when a tool update is available."""
        if remote_version:
            self._latest_remote_version = remote_version
        self._tool_update_available_flag = True
        self._update_tool_update_visibility()

    def _update_tool_update_visibility(self) -> None:
        """Only show the update label when we are on the main/idle screen (layout index 0)."""
        if hasattr(self, "bottom_titlebar") and self.bottom_titlebar:
            show = getattr(self, "_tool_update_available_flag", False) and getattr(self, "simplified_terminal", None) and self.simplified_terminal.layout.currentIndex() == 0
            self.bottom_titlebar.show_update_indicator(show)

    def run_self_update(self) -> None:
        """Install update using delta ZSync (appimageupdatetool), with full-download fallback."""
        import os
        from PyQt6.QtWidgets import QProgressDialog

        # Determine where the installed AppImage lives
        appimage_path = os.environ.get("APPIMAGE", "")
        default_appimage = "/home/deck/.local/share/ACCELA/ASSella.AppImage"
        if not appimage_path or not os.path.exists(appimage_path):
            appimage_path = default_appimage
        if not os.path.exists(appimage_path):
            QMessageBox.information(
                self, "Self-Update",
                "Could not locate the installed ASSella.AppImage to update."
            )
            return

        remote_version = getattr(self, "_latest_remote_version", None)
        tag = f"v{remote_version}" if remote_version else "latest"

        reply = QMessageBox.question(
            self, "Install Update",
            f"A new version of ASSella is available ({tag}).\n\n"
            "Would you like to download and install it now?\n"
            "The app will need to be restarted after the update.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        progress = QProgressDialog("Connecting to GitHub...", "Cancel", 0, 100, self)
        progress.setWindowTitle("Downloading Update")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(2)

        def _download_worker():
            import urllib.request
            import json
            import os
            import stat
            import shutil
            import subprocess
            from pathlib import Path

            tmp_path = None
            try:
                # --- Try fast delta update via appimageupdatetool ---
                updater_path = Path("/tmp/assella_updater/appimageupdatetool")

                if not updater_path.exists():
                    QMetaObject.invokeMethod(progress, "setLabelText",
                        Qt.ConnectionType.QueuedConnection, Q_ARG(str, "Downloading delta-update tool..."))
                    updater_url = "https://github.com/AppImage/AppImageUpdate/releases/download/continuous/appimageupdatetool-x86_64.AppImage"
                    updater_path.parent.mkdir(parents=True, exist_ok=True)
                    req0 = urllib.request.Request(updater_url, headers={"User-Agent": "ASSella-Updater"})
                    with urllib.request.urlopen(req0, timeout=30) as r0, open(updater_path, "wb") as f0:
                        f0.write(r0.read())
                    updater_path.chmod(updater_path.stat().st_mode | stat.S_IEXEC)

                update_info = f"gh-releases-zsync|niwia|ASSella|{tag}|ASSella.AppImage.zsync"
                logger.info(f"Running delta update: {updater_path} -u {update_info} {appimage_path}")

                QMetaObject.invokeMethod(progress, "setLabelText",
                    Qt.ConnectionType.QueuedConnection, Q_ARG(str, "Checking for delta update..."))
                QMetaObject.invokeMethod(progress, "setValue",
                    Qt.ConnectionType.QueuedConnection, Q_ARG(int, 10))

                proc = subprocess.Popen(
                    [str(updater_path), "-u", update_info, appimage_path],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                )
                for line in iter(proc.stdout.readline, ""):
                    if progress.wasCanceled():
                        proc.terminate()
                        return
                    line = line.strip()
                    logger.debug(f"Updater: {line}")
                    m = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
                    if m:
                        pct = 10 + int(float(m.group(1)) * 0.88)
                        QMetaObject.invokeMethod(progress, "setValue",
                            Qt.ConnectionType.QueuedConnection, Q_ARG(int, min(pct, 97)))
                    if line:
                        QMetaObject.invokeMethod(progress, "setLabelText",
                            Qt.ConnectionType.QueuedConnection, Q_ARG(str, line))
                proc.wait()

                if proc.returncode == 0:
                    QMetaObject.invokeMethod(progress, "setValue",
                        Qt.ConnectionType.QueuedConnection, Q_ARG(int, 100))
                    QMetaObject.invokeMethod(self, "_on_update_success",
                        Qt.ConnectionType.QueuedConnection)
                    return
                else:
                    logger.warning(f"appimageupdatetool exited {proc.returncode}, falling back to full download.")

            except Exception as e:
                logger.warning(f"Delta updater failed ({e}), falling back to full download.")

            # --- Fallback: full AppImage download ---
            try:
                QMetaObject.invokeMethod(progress, "setLabelText",
                    Qt.ConnectionType.QueuedConnection, Q_ARG(str, "Fetching release info from GitHub..."))

                if tag == "latest":
                    api_url = "https://api.github.com/repos/niwia/ASSella/releases/latest"
                else:
                    api_url = f"https://api.github.com/repos/niwia/ASSella/releases/tags/{tag}"

                req = urllib.request.Request(
                    api_url,
                    headers={"User-Agent": "ASSella-Updater", "Accept": "application/vnd.github+json"}
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    release_data = json.loads(resp.read().decode("utf-8"))

                download_url = None
                for asset in release_data.get("assets", []):
                    name = asset.get("name", "")
                    if name.endswith(".AppImage") and "zsync" not in name:
                        download_url = asset["browser_download_url"]
                        break

                if not download_url:
                    raise RuntimeError("No AppImage asset found in the release.")

                logger.info(f"Self-update fallback: downloading from {download_url}")
                dest_dir = Path(appimage_path).parent
                tmp_path = dest_dir / "ASSella.AppImage.part"

                QMetaObject.invokeMethod(progress, "setLabelText",
                    Qt.ConnectionType.QueuedConnection, Q_ARG(str, "Downloading full AppImage..."))
                QMetaObject.invokeMethod(progress, "setValue",
                    Qt.ConnectionType.QueuedConnection, Q_ARG(int, 10))

                req2 = urllib.request.Request(download_url, headers={"User-Agent": "ASSella-Updater"})
                with urllib.request.urlopen(req2, timeout=60) as response:
                    total = int(response.headers.get("Content-Length", 0))
                    downloaded = 0
                    chunk_size = 512 * 1024
                    with open(tmp_path, "wb") as f:
                        while True:
                            if progress.wasCanceled():
                                try:
                                    os.remove(tmp_path)
                                except OSError:
                                    pass
                                return
                            chunk = response.read(chunk_size)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                pct = 10 + int((downloaded / total) * 85)
                                QMetaObject.invokeMethod(progress, "setValue",
                                    Qt.ConnectionType.QueuedConnection, Q_ARG(int, min(pct, 94)))
                                mb_done = downloaded / 1_048_576
                                mb_total = total / 1_048_576
                                QMetaObject.invokeMethod(progress, "setLabelText",
                                    Qt.ConnectionType.QueuedConnection,
                                    Q_ARG(str, f"Downloading... {mb_done:.1f} / {mb_total:.1f} MB"))

                os.chmod(tmp_path, os.stat(tmp_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                shutil.move(str(tmp_path), appimage_path)

                QMetaObject.invokeMethod(progress, "setValue",
                    Qt.ConnectionType.QueuedConnection, Q_ARG(int, 100))
                QMetaObject.invokeMethod(self, "_on_update_success",
                    Qt.ConnectionType.QueuedConnection)

            except Exception as e:
                logger.error(f"Full download fallback also failed: {e}", exc_info=True)
                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                QMetaObject.invokeMethod(self, "_on_update_failed",
                    Qt.ConnectionType.QueuedConnection, Q_ARG(str, str(e)))

        import threading
        threading.Thread(target=_download_worker, daemon=True).start()

    @pyqtSlot()
    def _on_update_success(self) -> None:
        reply = QMessageBox.information(
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
