import logging
import os
import re
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.active_game_card import ActiveGameCard
from ui.widgets.recent_activity_item import RecentActivityItemWidget
from ui.widgets.updates_panel import UpdatesPanel, UpdateItemWidget
from utils.paths import Paths

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)


class SimplifiedTerminalWidget(QWidget):
    """A simplified terminal widget that displays stats and quotes when idle, and a job progress checklist when active."""

    def __init__(self, main_window: "MainWindow"):
        super().__init__(main_window)
        self.main_window = main_window
        self.settings = main_window.settings
        self.installation_history = []
        self._total_updates = 0

        self._stats_timer = QTimer(self)
        self._stats_timer.setSingleShot(True)
        self._stats_timer.setInterval(120)
        self._stats_timer.timeout.connect(self._do_update_stats)

        self.setStyleSheet("background: transparent;")
        self.init_ui()

        # Connect signals from GameManager to update stats & loading state
        if hasattr(self.main_window, "game_manager") and self.main_window.game_manager:
            gm = self.main_window.game_manager
            gm.library_updated.connect(lambda: self.update_stats(immediate=True))
            gm.game_update_status_changed.connect(
                lambda appid, status: self.update_stats(immediate=False)
            )
            gm.scan_complete.connect(lambda _: self.update_stats(immediate=True))
            gm.update_check_started.connect(self._on_update_check_started)
            gm.all_updates_checked.connect(self._on_update_check_finished)

        self._do_update_stats()
        self.update_history_display()
        self.update_style()

    def _on_update_check_started(self):
        if hasattr(self, "refresh_updates_btn") and self.refresh_updates_btn:
            self.refresh_updates_btn.set_loading(True)
            self.refresh_updates_btn.setToolTip("Checking for updates...")

    def _on_update_check_finished(self):
        if hasattr(self, "refresh_updates_btn") and self.refresh_updates_btn:
            self.refresh_updates_btn.set_loading(False)
            self.refresh_updates_btn.setToolTip("Force re-check updates for all games")
        self.update_stats(immediate=True)

    def init_ui(self):
        self.layout = QStackedLayout(self)
        self.layout.setContentsMargins(5, 0, 0, 0)
        self.layout.setSpacing(4)

        # --- VIEW 0: IDLE STATE (3-Column Dashboard) ---
        self.idle_widget = QWidget()
        idle_layout = QHBoxLayout(self.idle_widget)
        idle_layout.setContentsMargins(15, 0, 15, 0)
        idle_layout.setSpacing(8)

        panel_style = """
            QFrame {
                background-color: rgba(18, 18, 22, 230);
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

        # Column 1: Available Updates (formerly Column 2)
        self.panel_mid = UpdatesPanel(self)
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

        # Floating Update All Action Button
        self.update_all_btn = QPushButton("⟳ Update All (0)", self.panel_mid)
        self.update_all_btn.setFixedHeight(36)
        self.update_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_all_btn.clicked.connect(self.main_window.run_update_all_flow)
        self.update_all_btn.hide()

        # Floating Refresh Updates Button (ProgressButton with loading animation)
        from ui.progress_button import ProgressButton
        from utils.color_utils import get_best_foreground_color, get_dark_container_color, make_svg_icon
        from PyQt6.QtCore import QSize

        accent = getattr(self.main_window, "accent_color", "#C06C84") or "#C06C84"
        fg = get_best_foreground_color(accent, dark_color="#121214", light_color="#FFFFFF")
        disabled_bg = get_dark_container_color(accent)

        self.refresh_updates_btn = ProgressButton("", self.panel_mid)
        self.refresh_updates_btn.setFixedSize(36, 36)
        self.refresh_updates_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_updates_btn.clicked.connect(self.main_window.force_check_all_updates)
        icon_ref = make_svg_icon(Paths.icon("ref3.svg"), fg, size=20)
        self.refresh_updates_btn.setIcon(icon_ref)
        self.refresh_updates_btn.setIconSize(QSize(20, 20))
        self.refresh_updates_btn.setToolTip("Force re-check updates for all games")
        self.refresh_updates_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: {fg};
                border: none;
                border-radius: 18px;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: #FFFFFF;
                color: #000000;
            }}
            QPushButton:pressed {{
                background-color: {disabled_bg};
            }}
            QPushButton:disabled {{
                background-color: {disabled_bg};
                opacity: 0.5;
            }}
        """)
        self.refresh_updates_btn.show()
        self.refresh_updates_btn.raise_()

        # Column 2: Session Activity Log (formerly Column 3)
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

        # Add panels to idle layout (now 2 columns instead of 3)
        idle_layout.addWidget(self.panel_mid, 1)
        idle_layout.addWidget(self.panel_right, 1)

        # --- VIEW 1: ACTIVE JOB STATE ---
        self.active_widget = QWidget()
        active_layout = QVBoxLayout(self.active_widget)
        active_layout.setContentsMargins(0, 0, 0, 0)
        active_layout.setSpacing(4)

        # Header aligned with "Download Queue" on the left
        self.active_title = QLabel("Active Download")
        active_layout.addWidget(self.active_title)

        # Hero Game Banner Card with Thumbnail Background
        self.active_game_card = ActiveGameCard(self)
        active_layout.addWidget(self.active_game_card, 1)

        self.layout.addWidget(self.idle_widget)
        self.layout.addWidget(self.active_widget)

        # Set to Idle by default
        self.layout.setCurrentIndex(0)

    def update_stats(self, immediate: bool = False):
        """Request stats update. Debounced by default to prevent UI freezes during batch checks."""
        if immediate:
            self._stats_timer.stop()
            self._do_update_stats()
        else:
            if not self._stats_timer.isActive():
                self._stats_timer.start(120)

    def _do_update_stats(self):
        if not hasattr(self.main_window, "game_manager") or not self.main_window.game_manager:
            return

        settings = self.main_window.settings
        gm = self.main_window.game_manager
        games = gm.games
        total_games = len(games)

        # Trigger system status refresh in the main window to update Row 1/2 size and updates count!
        if hasattr(self.main_window, "refresh_system_status"):
            self.main_window.refresh_system_status()

        # If scan is currently running and no games have been populated yet
        is_scanning = getattr(gm, "is_scanning", False)
        if total_games == 0 and is_scanning:
            while self.updates_scroll_layout.count():
                child = self.updates_scroll_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            lbl = QLabel("Scanning installed games...")
            lbl.setStyleSheet("color: #888888; font-style: italic; font-size: 9pt;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.updates_scroll_layout.addWidget(lbl)
            return

        # Only include updates for games NOT excluded from "Update All" and NOT pinned (AT0-M games are managed by Steam)
        games_with_updates = [
            g for g in games
            if g.get("update_status") == "update_available"
            and not (g.get("is_atom") or g.get("is_vapor") or g.get("is_plugin_game"))
            and not settings.value(
                f"exclude_from_update_all/{g.get('appid', '')}", False, type=bool
            )
            and not settings.value(
                f"pin_build/{g.get('appid', '')}", False, type=bool
            )
        ]
        total_updates = len(games_with_updates)

        # Show/Hide/Configure the Floating Update All Action Button
        accent = getattr(self.main_window, "accent_color", "#C06C84") or "#C06C84"
        bg = getattr(self.main_window, "background_color", "#000000") or "#000000"

        from utils.color_utils import get_best_foreground_color, get_dark_container_color, make_svg_icon
        from PyQt6.QtCore import QSize

        fg = get_best_foreground_color(accent, dark_color="#121214", light_color="#FFFFFF")
        disabled_bg = get_dark_container_color(accent)

        if hasattr(self, "update_all_btn") and self.update_all_btn:
            if total_updates > 0:
                self.update_all_btn.setText(f" Update All ({total_updates})")
                icon_cloud = make_svg_icon(Paths.icon("dl_cloud.svg"), fg, size=18)
                self.update_all_btn.setIcon(icon_cloud)
                self.update_all_btn.setIconSize(QSize(18, 18))

                is_all_running = getattr(self.main_window, "_update_all_running", False)
                self.update_all_btn.setEnabled(not is_all_running)
                if is_all_running:
                    self.update_all_btn.setToolTip("Queueing updates...")
                else:
                    self.update_all_btn.setToolTip("Queue updates for all pending games")

                self.update_all_btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {accent};
                        color: {fg};
                        border: none;
                        border-radius: 18px;
                        font-weight: bold;
                        font-size: 9.5pt;
                        padding-left: 10px;
                        padding-right: 14px;
                    }}
                    QPushButton:hover {{
                        background-color: #FFFFFF;
                        color: #000000;
                    }}
                    QPushButton:pressed {{
                        background-color: {disabled_bg};
                        color: #FFFFFF;
                    }}
                    QPushButton:disabled {{
                        background-color: {disabled_bg};
                        color: rgba(255, 255, 255, 0.4);
                    }}
                """)
                self.update_all_btn.show()
            else:
                self.update_all_btn.hide()

        # Show/Configure the Floating Refresh Updates Button (Always visible)
        if hasattr(self, "refresh_updates_btn") and self.refresh_updates_btn:
            is_checking = False
            if hasattr(self.main_window, "game_manager") and self.main_window.game_manager:
                gm = self.main_window.game_manager
                is_checking = (getattr(gm, "manifest_check_task", None) is not None or getattr(gm, "manifest_check_runner", None) is not None)

            if is_checking:
                self.refresh_updates_btn.set_loading(True)
                self.refresh_updates_btn.setToolTip("Checking for updates...")
            else:
                self.refresh_updates_btn.set_loading(False)
                self.refresh_updates_btn.setText("")
                icon_ref = make_svg_icon(Paths.icon("ref3.svg"), fg, size=20)
                self.refresh_updates_btn.setIcon(icon_ref)
                self.refresh_updates_btn.setIconSize(QSize(20, 20))
                self.refresh_updates_btn.setToolTip("Force re-check updates for all games")

            self.refresh_updates_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {accent};
                    color: {fg};
                    border: none;
                    border-radius: 18px;
                    padding: 0px;
                }}
                QPushButton:hover {{
                    background-color: #FFFFFF;
                    color: #000000;
                }}
                QPushButton:pressed {{
                    background-color: {disabled_bg};
                }}
                QPushButton:disabled {{
                    background-color: {disabled_bg};
                    opacity: 0.5;
                }}
            """)
            self.refresh_updates_btn.show()

        # Trigger positioning layout check
        self.main_window.position_update_all_btn()

        # Clear existing updates list
        while self.updates_scroll_layout.count():
            child = self.updates_scroll_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        self._total_updates = total_updates
        if hasattr(self, "panel_mid") and self.panel_mid:
            self.panel_mid.update()

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

                accent = getattr(self.main_window, "accent_color", "#C06C84") or "#C06C84"
                row = UpdateItemWidget(appid, name, accent, self)
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
            for i, entry in enumerate(history):
                item_widget = RecentActivityItemWidget(entry, parent_terminal=self)
                self.history_scroll_layout.addWidget(item_widget)

                if i < len(history) - 1:
                    line = QFrame()
                    line.setFrameShape(QFrame.Shape.HLine)
                    line.setFrameShadow(QFrame.Shadow.Sunken)
                    line.setStyleSheet("background-color: rgba(255, 255, 255, 0.05); border: none; height: 1px;")
                    self.history_scroll_layout.addWidget(line)
        self.history_scroll_layout.addStretch()

    def _open_game_details(self, appid: str):
        """Open Game Details page for a game clicked in the recent activity list."""
        if not appid or not str(appid).isdigit() or str(appid).lower() == "workshop":
            return
        try:
            from ui.dialogs.gamelibrary import GameLibraryDialog
            dialog = GameLibraryDialog(self.main_window, show_details_for_appid=str(appid))
            dialog.exec()
        except Exception as e:
            logger.error(f"Failed to open game details from recent activity for {appid}: {e}")

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

    def set_updates_checking_progress(self, current: int, total: int):
        if hasattr(self, "updates_title") and self.updates_title:
            if current >= 0 and total > 0:
                self.updates_title.setText(f"PENDING UPDATES (CHECKING {current}/{total})")
            else:
                self.updates_title.setText("PENDING UPDATES")

    def update_style(self):
        accent = self.main_window.accent_color or "#C06C84"
        accent_style = f"color: {accent};"

        hdr_sz = self.main_window.settings.value("font-size-headers", 11, type=int) if hasattr(self.main_window, "settings") and self.main_window.settings else 11
        title_style = f"font-weight: bold; font-size: {hdr_sz}pt; {accent_style} border: none; background: transparent;"
        if hasattr(self, "updates_title") and self.updates_title:
            self.updates_title.setStyleSheet(title_style)
        if hasattr(self, "history_title") and self.history_title:
            self.history_title.setStyleSheet(title_style)
        if hasattr(self, "active_title") and self.active_title:
            self.active_title.setStyleSheet(title_style)

    def set_stage_status(self, stage: str, status: str, count: Optional[int] = None):
        if not hasattr(self, "_stage_statuses"):
            self._stage_statuses = {}
        self._stage_statuses[stage] = (status, count)

        # Update subtitle text on active game card
        if hasattr(self, "active_game_card") and self.active_game_card:
            if stage == "download":
                if status == "in_progress":
                    self.active_game_card.set_sub_status("Downloading game files...")
                elif status == "completed":
                    self.active_game_card.set_sub_status("Download complete · Post-processing...")
            elif stage == "achievements" and status == "in_progress":
                self.active_game_card.set_sub_status("Generating achievements...")
            elif stage == "steamless" and status == "in_progress":
                self.active_game_card.set_sub_status("Applying game fixes...")

    def reset_stages(self):
        if hasattr(self, "active_game_card") and self.active_game_card:
            self.active_game_card.set_sub_status("Preparing download...")

    def show_idle(self):
        self.layout.setCurrentIndex(0)
        self.update_stats()
        # Re-position FABs now that idle view geometry is restored
        self.main_window.position_update_all_btn()
        if hasattr(self.main_window, "_update_tool_update_visibility"):
            self.main_window._update_tool_update_visibility()

    def show_active_job(self, game_name: str = "Installing Game...", appid: str = ""):
        if not appid and hasattr(self.main_window, "task_manager") and self.main_window.task_manager:
            gd = getattr(self.main_window.task_manager, "game_data", {}) or {}
            meta = getattr(self.main_window.task_manager, "current_job_metadata", {}) or {}
            appid = str(gd.get("appid") or meta.get("appid") or "")

        accent = getattr(self.main_window, "accent_color", "#C06C84") or "#C06C84"
        if hasattr(self, "active_game_card") and self.active_game_card:
            self.active_game_card.set_game(appid, game_name, accent)

            # Resolve installed and target build IDs
            installed_bid = ""
            target_bid = ""

            tm = getattr(self.main_window, "task_manager", None)
            gd = getattr(tm, "game_data", {}) or {} if tm else {}
            meta = getattr(tm, "current_job_metadata", {}) or {} if tm else {}

            target_candidates = [
                meta.get("pinned_build_id"),
                meta.get("target_buildid"),
                meta.get("buildid"),
                gd.get("target_buildid"),
                gd.get("branch_buildid"),
                gd.get("buildid"),
            ]
            for cand in target_candidates:
                if cand and str(cand).isdigit() and str(cand) != "0":
                    target_bid = str(cand).strip()
                    break

            installed_candidates = [
                meta.get("installed_buildid"),
                meta.get("current_buildid"),
                gd.get("installed_buildid"),
            ]
            for cand in installed_candidates:
                if cand and str(cand).isdigit() and str(cand) != "0":
                    installed_bid = str(cand).strip()
                    break

            if not installed_bid and appid and appid not in ("0", "unknown", "N/A", "Workshop"):
                settings = getattr(self.main_window, "settings", None)
                if settings:
                    branch = meta.get("branch") or gd.get("branch") or "public"
                    if branch and branch != "public":
                        saved = settings.value(f"installed_buildid/{appid}/{branch}", "", type=str)
                        if saved and str(saved).isdigit() and str(saved) != "0":
                            installed_bid = str(saved).strip()
                    if not installed_bid:
                        saved = settings.value(f"installed_buildid/{appid}", "", type=str)
                        if saved and str(saved).isdigit() and str(saved) != "0":
                            installed_bid = str(saved).strip()

                if not installed_bid:
                    gm = getattr(self.main_window, "game_manager", None)
                    if gm and hasattr(gm, "games") and isinstance(gm.games, dict):
                        g_info = gm.games.get(str(appid))
                        if g_info and isinstance(g_info, dict):
                            g_bid = g_info.get("buildid")
                            if g_bid and str(g_bid).isdigit() and str(g_bid) != "0":
                                installed_bid = str(g_bid).strip()

                if not installed_bid:
                    try:
                        from core.steam_helpers import get_steam_libraries
                        for lib in get_steam_libraries():
                            acf_p = os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf")
                            if os.path.exists(acf_p):
                                with open(acf_p, "r", encoding="utf-8", errors="ignore") as f:
                                    m = re.search(r'"buildid"\s+"([^"]+)"', f.read())
                                    if m and m.group(1).isdigit() and m.group(1) != "0":
                                        installed_bid = m.group(1).strip()
                                        break
                    except Exception:
                        pass

            self.active_game_card.set_version_info(installed_bid, target_bid, appid)

        self.layout.setCurrentIndex(1)
        # Hide FABs while active — they live inside panel_mid (idle-only view)
        if hasattr(self, "update_all_btn") and self.update_all_btn:
            self.update_all_btn.hide()
        if hasattr(self, "refresh_updates_btn") and self.refresh_updates_btn:
            self.refresh_updates_btn.hide()
        if hasattr(self.main_window, "_update_tool_update_visibility"):
            self.main_window._update_tool_update_visibility()
