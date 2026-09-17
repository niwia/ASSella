import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QSize, Qt, QTimer, QRectF
from PyQt6.QtGui import QColor, QIcon, QPixmap, QPainter, QBrush, QLinearGradient, QPen, QMovie
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
    QTabWidget,
    QTextEdit,
    QSpinBox,
    QGroupBox,
    QCheckBox,
    QPushButton,
    QFileDialog,
    QFrame,
    QSizePolicy,
)


from core import morrenus_api
from utils.image_fetcher import ImageFetcher
from utils.task_runner import TaskRunner
from utils.helpers import get_base_path
from utils.paths import get_jumpscare_gif
from core import steam_helpers
from ui.dialogs.steamlibrary import SteamLibraryDialog

from ui.material_progress import MaterialSpinner

SEARCH_PLACEHOLDERS = (
    "Search: Cyberpunk 2077...",
    "Search: 1091500...",
    "Search: Elden Ring...",
    "Search: 1245620...",
    "Search: Baldur's Gate 3...",
    "Search: 1086940...",
    "Search: RimWorld...",
    "Search: 294100...",
    "Search: Black Myth: Wukong...",
    "Search: 2358720...",
    "Search: Vampire Survivors...",
    "Search: 1794680...",
    "Search: Hollow Knight...",
    "Search: 367520...",
    "Search: Hades II...",
    "Search: 1145350...",
    "Search: Palworld...",
    "Search: 1623730...",
    "Search by game name or AppID...",
)

# Re-exported for backward compatibility across dialogs and managers
from ui.dialogs.single_depot_timer_dialog import SingleDepotTimerDialog
from ui.widgets.search_item_widget import SearchItemWidget
from utils.search_ranking import (
    normalize_for_match,
    extract_game_name,
    extract_app_id,
    manifest_size,
    is_blacklisted,
    is_blacklisted_result,
    is_likely_media_variant,
    relevance_score,
    relevance_sort_key,
    dedupe_results_by_name,
    filter_and_rank_results,
    _BLACKLIST_NAME_RE,
    _BLACKLIST_META_TOKENS,
    _RESULT_NAME_KEYS,
    _RESULT_ID_KEYS,
    _RESULT_FLAG_KEYS,
    _RESULT_META_KEYS,
    _RESULT_META_LIST_KEYS,
)
from utils.workshop_helpers import extract_workshop_id, parse_workshop_ids
from core.manifest_fetcher import verify_or_download_manifest

logger = logging.getLogger(__name__)

# Constants
_API_STATS_CACHE_DURATION = 60  # seconds

# Global Cache State
_api_stats_cache = {"data": None, "timestamp": 0.0}



def _get_cached_stats():
    """Returns cached stats if still valid, otherwise None."""
    if _api_stats_cache["data"] is not None:
        if time.time() - _api_stats_cache["timestamp"] < _API_STATS_CACHE_DURATION:
            return _api_stats_cache["data"]
    return None


def _cache_stats(data):
    """Store stats in cache."""
    _api_stats_cache["data"] = data
    _api_stats_cache["timestamp"] = time.time()


class FetchManifestDialog(QDialog):
    """
    A dialog for searching and downloading manifests from the Morrenus API.
    """

    def __init__(self, parent=None, select_tab: int = 0, initial_query: str = ""):
        super().__init__(parent)
        self.parent_window = parent
        self.setWindowTitle("Fetch Manifest from Hubcap API")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)

        self.settings = getattr(parent, "settings", None)
        if not self.settings:
            from utils.settings import get_settings
            self.settings = get_settings()

        self.accent_color = "#a1c9fd"
        self.background_color = "#111318"
        if self.settings:
            self.accent_color = self.settings.value("accent_color", "#a1c9fd")
            self.background_color = self.settings.value("background_color", "#111318")

        self.task_runner = TaskRunner()
        self._active_image_fetchers = {}
        self._pending_image_timers = []
        self._search_generation = 0
        self._cached_app_ids = self._scan_cached_app_ids()

        self._origins_movie = None
        if self.settings and self.settings.value("remember_origins", False, type=bool):
            gif_path = get_jumpscare_gif("lain3.gif")
            if gif_path and os.path.exists(gif_path):
                self._origins_movie = QMovie(gif_path)
                self._origins_movie.frameChanged.connect(self.update)
                self._origins_movie.start()

        self._init_ui()
        logger.debug("FetchManifestDialog initialized.")

        self._request_api_status_update()

        if hasattr(self, "tab_widget") and self.tab_widget:
            self.tab_widget.setCurrentIndex(select_tab)

        if initial_query and hasattr(self, "search_input") and self.search_input:
            self.search_input.setText(str(initial_query))
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(200, self.on_search)

        if self.parent():
            from ui.dialogs.dialog_raiser import DialogRaiser
            DialogRaiser(self.parent(), self)

    def _scan_cached_app_ids(self) -> set:
        """Scan disk for cached manifest zips and lua files."""
        cached = set()
        try:
            from utils.helpers import get_base_path
            base = Path(get_base_path())
            hubcap_dir = base / "hubcap_manifests"
            if hubcap_dir.exists():
                for fname in os.listdir(hubcap_dir):
                    m = re.match(r"^accela_fetch_(\d+)", fname)
                    if m:
                        cached.add(m.group(1))
            lua_dir = base / "cached_luas"
            if lua_dir.exists():
                for fname in os.listdir(lua_dir):
                    m = re.match(r"^(\d+)\.lua$", fname)
                    if m:
                        cached.add(m.group(1))
        except Exception as e:
            logger.debug(f"[FetchManifest] Error scanning cached appids: {e}")
        return cached

    def _is_app_cached(self, app_id: str) -> bool:
        if not app_id:
            return False
        aid = str(app_id).strip()
        if not aid.isdigit():
            return False
        if hasattr(self, "_cached_app_ids") and aid in self._cached_app_ids:
            return True
        try:
            from utils.helpers import get_base_path
            base = Path(get_base_path())
            if any((base / "hubcap_manifests").glob(f"accela_fetch_{aid}*.zip")):
                if hasattr(self, "_cached_app_ids"):
                    self._cached_app_ids.add(aid)
                return True
            if (base / "cached_luas" / f"{aid}.lua").exists():
                if hasattr(self, "_cached_app_ids"):
                    self._cached_app_ids.add(aid)
                return True
        except Exception:
            pass
        return False

    def _init_ui(self):
        layout = QVBoxLayout(self)

        applist_2_0_enabled = True

        if applist_2_0_enabled:
            self.setStyleSheet(
                f"""
                QDialog {{
                    background-color: {self.background_color};
                    color: #FFFFFF;
                }}
                QLabel {{
                    color: rgba(255, 255, 255, 0.85);
                }}
                QLineEdit {{
                    background-color: rgba(255, 255, 255, 0.05);
                    color: #FFFFFF;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 18px;
                    padding: 0px 16px;
                    height: 36px;
                    font-size: 12px;
                }}
                QLineEdit:focus {{
                    border: 2px solid {self.accent_color};
                    background-color: rgba(255, 255, 255, 0.08);
                    padding: 0px 15px;
                }}
                QListWidget {{
                    background: transparent;
                    border: none;
                    padding: 10px 0px;
                }}
                QListWidget::item {{
                    background-color: rgba(255, 255, 255, 0.03);
                    border: 1px solid transparent;
                    border-radius: 10px;
                    margin: 2px 16px;
                    color: #FFFFFF;
                    padding: 8px;
                }}
                QListWidget::item:hover {{
                    background-color: rgba(255, 255, 255, 0.08);
                    border: 1px solid rgba(255, 255, 255, 0.14);
                }}
                QListWidget::item:selected {{
                    background-color: rgba(255, 255, 255, 0.14);
                    border: 1px solid {self.accent_color};
                    color: #FFFFFF;
                }}
                QTabWidget::pane {{
                    border: none;
                    background-color: transparent;
                }}
                QTabBar::tab {{
                    background-color: rgba(255, 255, 255, 0.04);
                    color: rgba(255, 255, 255, 0.6);
                    padding: 8px 18px;
                    margin-right: 4px;
                    border-top-left-radius: 6px;
                    border-top-right-radius: 6px;
                    font-weight: bold;
                }}
                QTabBar::tab:selected {{
                    color: {self.accent_color};
                    background-color: rgba(255, 255, 255, 0.09);
                    border-bottom: 2px solid {self.accent_color};
                }}
                QTabBar::tab:hover {{
                    background-color: rgba(255, 255, 255, 0.07);
                }}
                QGroupBox {{
                    color: {self.accent_color};
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    margin-top: 10px;
                    padding-top: 15px;
                    border-radius: 6px;
                    font-weight: bold;
                }}
                QGroupBox::title {{
                    subcontrol-origin: margin;
                    left: 10px;
                    padding: 0 4px;
                }}
                QTextEdit {{
                    background-color: rgba(255, 255, 255, 0.04);
                    color: #FFFFFF;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 8px;
                    padding: 6px;
                }}
                QSpinBox {{
                    background-color: rgba(255, 255, 255, 0.04);
                    color: #FFFFFF;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    padding: 4px;
                    border-radius: 6px;
                }}
                QPushButton#workshopDlBtn {{
                    background-color: {self.accent_color};
                    color: #000000;
                    border: none;
                    font-weight: bold;
                    border-radius: 6px;
                    padding: 8px 16px;
                }}
                QPushButton#workshopDlBtn:hover {{
                    background-color: #FFFFFF;
                }}
                QPushButton#workshopDlBtn:disabled {{
                    background-color: rgba(255, 255, 255, 0.1);
                    color: rgba(255, 255, 255, 0.3);
                }}
                QPushButton#workshopSaveBtn {{
                    background-color: rgba(255, 255, 255, 0.05);
                    color: #FFFFFF;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 6px;
                    padding: 8px 16px;
                }}
                QPushButton#workshopSaveBtn:hover {{
                    background-color: rgba(255, 255, 255, 0.1);
                }}
                """
            )

        # 1. API Status Bar
        self._create_api_status_bar(layout)

        # Create Tab Widget
        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget)

        # --- Tab 1: Game Manifests ---
        self.games_tab = QWidget()
        games_layout = QVBoxLayout(self.games_tab)
        games_layout.setContentsMargins(0, 8, 0, 0)

        self._placeholder_idx = 0
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(SEARCH_PLACEHOLDERS[0])
        self.search_input.returnPressed.connect(self.on_search)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        games_layout.addWidget(self.search_input)

        # Debounce timer for live search suggestions (400ms, min 3 chars)
        self._live_search_timer = QTimer(self)
        self._live_search_timer.setSingleShot(True)
        self._live_search_timer.timeout.connect(self._fire_live_search)

        # Dynamic lightweight placeholder rotation timer
        self._placeholder_timer = QTimer(self)
        self._placeholder_timer.timeout.connect(self._rotate_placeholder)
        self._placeholder_timer.start(3500)

        self.results_list = QListWidget()
        self.results_list.setIconSize(QSize(230, 108))
        self.results_list.setSpacing(5)
        self.results_list.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.results_list.itemClicked.connect(self._on_item_clicked)
        self.results_list.currentItemChanged.connect(lambda cur, _prev: self._on_item_clicked(cur) if cur else None)
        games_layout.addWidget(self.results_list)



        # Bottom Status Footer Layout (Horizontal, Centered)
        status_footer_container = QWidget()
        status_footer_layout = QHBoxLayout(status_footer_container)
        status_footer_layout.setContentsMargins(0, 5, 0, 5)
        status_footer_layout.setSpacing(8)
        status_footer_layout.addStretch()

        self.footer_spinner = MaterialSpinner(self, size=14, color=self.accent_color, thickness=2)
        status_footer_layout.addWidget(self.footer_spinner)

        self.status_label = QLabel("Search for a game to begin")
        status_footer_layout.addWidget(self.status_label)

        status_footer_layout.addStretch()
        games_layout.addWidget(status_footer_container)

        self.footer_spinner.setVisible(False)

        self.tab_widget.addTab(self.games_tab, "Game Manifests")

        # --- Tab 2: Workshop Downloader ---
        self.workshop_tab = QWidget()
        workshop_layout = QVBoxLayout(self.workshop_tab)
        workshop_layout.setContentsMargins(14, 12, 14, 12)
        workshop_layout.setSpacing(10)

        # Material Header Banner Card
        ws_card = QFrame()
        ws_card.setObjectName("WorkshopCard")
        ws_card.setStyleSheet("""
            QFrame#WorkshopCard {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }
            QFrame#WorkshopCard QLabel {
                border: none !important;
                background: transparent !important;
                padding: 0px !important;
            }
        """)
        ws_card_lay = QVBoxLayout(ws_card)
        ws_card_lay.setContentsMargins(14, 14, 14, 14)
        ws_card_lay.setSpacing(8)

        ws_title = QLabel("Workshop Batch Downloader")
        ws_title.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {self.accent_color}; border: none; background: transparent;")
        ws_sub = QLabel("Paste Workshop item URLs or IDs below (one per line, or comma-separated):")
        ws_sub.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.5pt; border: none; background: transparent;")

        ws_card_lay.addWidget(ws_title)
        ws_card_lay.addWidget(ws_sub)

        self.ids_input = QTextEdit()
        self.ids_input.setFixedHeight(120)
        self.ids_input.setPlaceholderText("e.g. https://steamcommunity.com/sharedfiles/filedetails/?id=3772598164\nor 3772598164, 12345678")
        self.ids_input.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(0, 0, 0, 0.25);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 8px;
                color: #FFFFFF;
                font-size: 9.5pt;
                padding: 8px;
            }}
            QTextEdit:focus {{
                border: 2px solid {self.accent_color};
            }}
        """)
        ws_card_lay.addWidget(self.ids_input)

        # Action Buttons
        btns_layout = QHBoxLayout()
        btns_layout.setContentsMargins(0, 4, 0, 0)
        btns_layout.setSpacing(10)

        from utils.color_utils import get_best_foreground_color
        text_color = get_best_foreground_color(self.accent_color)

        self.dl_btn = QPushButton("⬇ Download (Add to Queue)")
        self.dl_btn.setFixedHeight(36)
        self.dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dl_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: {text_color};
                border: none;
                font-weight: bold;
                font-size: 9.5pt;
                border-radius: 8px;
                padding: 0 18px;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.06) !important;
                border: 1px solid rgba(255, 255, 255, 0.12) !important;
                color: rgba(255, 255, 255, 0.38) !important;
            }}
        """)
        self.dl_btn.clicked.connect(self._download_workshop)
        
        self.workshop_status_label = QLabel()
        self.workshop_status_label.setStyleSheet(f"color: {self.accent_color}; font-weight: bold; font-size: 8.5pt;")

        btns_layout.addWidget(self.dl_btn)
        btns_layout.addWidget(self.workshop_status_label)
        btns_layout.addStretch()
        ws_card_lay.addLayout(btns_layout)

        workshop_layout.addWidget(ws_card)
        workshop_layout.addStretch()

        self.tab_widget.addTab(self.workshop_tab, "Workshop Downloader")

    def _create_api_status_bar(self, parent_layout):
        """Builds the top status bar widget."""
        status_widget = QWidget()
        container = QHBoxLayout(status_widget)
        container.setContentsMargins(0, 0, 0, 5)
        container.setSpacing(10)

        # Connection Dot & Top Spinner
        self.api_status_dot = QLabel()
        self.api_status_dot.setFixedSize(10, 10)
        self.api_status_dot.setStyleSheet(
            "border-radius: 5px; background-color: #7f8c8d;"
        )
        self.api_status_dot.setVisible(False)  # Hidden while checking

        self.top_spinner = MaterialSpinner(parent=status_widget, size=14, color=self.accent_color, thickness=2)
        self.top_spinner.setVisible(True)  # Visible while checking

        self.api_status_text = QLabel("Checking...")
        self.api_status_text.setStyleSheet("font-weight: bold; color: rgba(255, 255, 255, 0.6);")

        # Group Dot + Spinner + Text
        conn_layout = QHBoxLayout()
        conn_layout.setSpacing(6)
        conn_layout.addWidget(self.top_spinner)
        conn_layout.addWidget(self.api_status_dot)
        conn_layout.addWidget(self.api_status_text)

        container.addLayout(conn_layout)
        container.addStretch(1)

        # User Info
        self.username_label = QLabel("User: --")
        self.username_label.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 11px;")
        container.addWidget(self.username_label)

        self.usage_label = QLabel("Daily: --")
        self.usage_label.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 11px;")
        container.addWidget(self.usage_label)

        parent_layout.addWidget(status_widget)

    # --------------------------
    # Status / Health Logic
    # --------------------------

    def _set_loading_active(self, active: bool):
        """Toggle loading spinners inside the dialog synchronously."""
        if hasattr(self, "footer_spinner") and self.footer_spinner:
            self.footer_spinner.setVisible(active)

    def _request_api_status_update(self):
        self.top_spinner.setVisible(True)
        self.api_status_dot.setVisible(False)
        worker = self.task_runner.run(self._fetch_api_status)
        worker.finished.connect(self._apply_api_status)
        worker.error.connect(self._on_api_status_error)

    @staticmethod
    def _fetch_api_status():
        """Worker: Fetch health and user stats."""
        health = morrenus_api.check_health()

        stats = _get_cached_stats()
        if not stats or stats.get("error"):
            stats = morrenus_api.get_user_stats()
            if "error" not in stats:
                _cache_stats(stats)

        return {"health": health, "stats": stats}

    def _apply_api_status(self, result):
        """UI: Update status bar."""
        self.top_spinner.setVisible(False)
        self.api_status_dot.setVisible(True)
        health = result.get("health", {})
        is_healthy = health.get("status") == "healthy"

        # Update Connection Status
        if is_healthy:
            self.api_status_dot.setStyleSheet(
                "border-radius: 5px; background-color: #81C784;"
            )  # Green
            self.api_status_text.setText("Online")
            self.api_status_text.setStyleSheet("font-weight: bold; color: #81C784;")
        else:
            self.api_status_dot.setStyleSheet(
                "border-radius: 5px; background-color: #FF8A80;"
            )  # Red
            self.api_status_text.setText("Offline")
            self.api_status_text.setStyleSheet("font-weight: bold; color: #FF8A80;")

        # Update User Stats
        stats = result.get("stats", {})
        if stats.get("error"):
            self.username_label.setText("User: Error")
            self.usage_label.setText("Daily: --")
        else:
            self.username_label.setText(f"User: {stats.get('username', 'Unknown')}")
            usage = stats.get("daily_usage", 0)
            limit = stats.get("daily_limit", 0)
            self.usage_label.setText(f"Daily: {usage}/{limit}")

    def _on_api_status_error(self, error_info):
        """Handle errors during status check silently."""
        self.top_spinner.setVisible(False)
        self.api_status_dot.setVisible(True)
        self.api_status_dot.setStyleSheet("border-radius: 5px; background-color: #FF8A80;")
        self.api_status_text.setText("Offline")
        self.api_status_text.setStyleSheet("font-weight: bold; color: #FF8A80;")
        logger.error(f"Status check failed: {error_info}")
        self.api_status_dot.setStyleSheet(
            "border-radius: 6px; background-color: #e74c3c;"
        )
        self.api_status_text.setText("Offline")

    # --------------------------
    # Search Logic
    # --------------------------

    def _on_search_text_changed(self, text: str) -> None:
        """Slot called on every keystroke — starts/restarts the 400ms debounce timer."""
        text = text.strip()
        # Don't fire live suggestions for numeric-only queries (those go via AppID
        # branch-check on Enter/Search press, not live suggestion mode)
        if len(text) < 3 or text.isdigit():
            self._live_search_timer.stop()
            return
        # Restart the debounce window
        self._live_search_timer.start(400)

    def _fire_live_search(self) -> None:
        """Called 400ms after the last keystroke — runs a lightweight suggestion search."""
        query = self.search_input.text().strip()
        if len(query) < 3 or query.isdigit():
            return
        # Don't clobber an in-progress explicit search or download
        if not self.search_input.isEnabled():
            return

        self._search_generation += 1
        gen = self._search_generation

        logger.debug(f"[LiveSearch] Firing suggestion search for '{query}' (gen {gen})")
        self._stop_active_image_fetchers()
        self.status_label.setText(f"Searching for '{query}'…")
        self._set_loading_active(True)
        worker = self.task_runner.run(self._search_and_filter_results, query)
        worker.finished.connect(lambda res, g=gen: self.on_search_finished(res, g, is_live=True))
        worker.error.connect(self._on_live_search_error)

    def _on_live_search_error(self, error_info):
        """Silently handles background live-search errors without interrupting typing."""
        self._set_loading_active(False)
        self._toggle_inputs(True)
        self.status_label.setText("Search error. Press Enter to retry.")

    def on_search(self):
        # Cancel any active background update checks to free up the Steam connection
        if self.parent_window and hasattr(self.parent_window, "game_manager") and self.parent_window.game_manager:
            try:
                self.parent_window.game_manager.cancel_update_checks()
            except Exception as e:
                logger.error(f"Error cancelling background update checks: {e}")

        # Stop debounce timer if user pressed Enter
        if hasattr(self, "_live_search_timer"):
            self._live_search_timer.stop()

        query = self.search_input.text().strip()
        if len(query) < 2:
            self.status_label.setText("Enter at least 2 characters")
            self._set_loading_active(False)
            return

        self._set_loading_active(True)

        # Direct AppID bypass: check library status or fetch branches, fallback to text search
        if query.isdigit():
            in_library = False
            if self.parent_window and hasattr(self.parent_window, "game_manager") and self.parent_window.game_manager:
                if self.parent_window.game_manager.get_game(query) is not None:
                    in_library = True

            if in_library:
                self.accept()
                from ui.dialogs.gamelibrary import GameLibraryDialog
                dialog = GameLibraryDialog(self.parent_window, show_details_for_appid=query)
                dialog.exec()
                return

            logger.info(f"Numeric AppID query '{query}' — fetching branches first...")
            self._toggle_inputs(False)
            self.status_label.setText(f"Checking branches for App ID {query}...")
            worker = self.task_runner.run(self._fetch_branches_and_prompt, query)
            worker.finished.connect(self._on_branches_fetched)
            worker.error.connect(lambda err, q=query: self._on_direct_manifest_error(err, q))
            return

        self._search_generation += 1
        gen = self._search_generation

        # Reset UI
        self.results_list.clear()
        self._stop_active_image_fetchers()
        self._toggle_inputs(False)
        self.status_label.setText("Searching...")

        # Run search + filtering in a worker thread.
        worker = self.task_runner.run(self._search_and_filter_results, query)
        worker.finished.connect(lambda res, g=gen: self.on_search_finished(res, g))
        worker.error.connect(self.on_task_error)

    def _on_direct_manifest_finished(self, result, query: str):
        filepath, error_msg = result
        if filepath and not error_msg:
            self.on_download_finished(result)
        else:
            logger.info(f"Direct AppID lookup for '{query}' failed — falling back to standard text search.")
            self._search_generation += 1
            gen = self._search_generation
            self.results_list.clear()
            self._stop_active_image_fetchers()
            self.status_label.setText(f"Searching for '{query}'...")
            worker = self.task_runner.run(self._search_and_filter_results, query)
            worker.finished.connect(lambda res, g=gen: self.on_search_finished(res, g))
            worker.error.connect(self.on_task_error)

    def _on_direct_manifest_error(self, error_info, query: str):
        logger.info(f"Direct AppID lookup for '{query}' errored — falling back to standard text search.")
        self._search_generation += 1
        gen = self._search_generation
        self.results_list.clear()
        self._stop_active_image_fetchers()
        self.status_label.setText(f"Searching for '{query}'...")
        worker = self.task_runner.run(self._search_and_filter_results, query)
        worker.finished.connect(lambda res, g=gen: self.on_search_finished(res, g))
        worker.error.connect(self.on_task_error)

    def _search_and_filter_results(self, query: str) -> Dict[str, Any]:
        """Worker: search Morrenus and apply fast local relevance filtering."""
        results = morrenus_api.search_games(query)
        if isinstance(results, dict) and "error" in results:
            return results

        game_results = results.get("results", []) if isinstance(results, dict) else []
        from utils.settings import get_settings
        filter_blacklist = get_settings().value("filter_search_blacklist", False, type=bool)
        return filter_and_rank_results(game_results, query, filter_blacklist=filter_blacklist)

    # Static / Class method aliases for backward-compatibility
    _normalize_for_match = staticmethod(normalize_for_match)
    _relevance_score = staticmethod(relevance_score)
    _relevance_sort_key = staticmethod(relevance_sort_key)
    _dedupe_results_by_name = staticmethod(dedupe_results_by_name)
    _manifest_size = staticmethod(manifest_size)
    _is_likely_media_variant = staticmethod(is_likely_media_variant)


    def on_search_finished(self, results, gen: int = 0, is_live: bool = False):
        # Ignore stale search results from previous queries
        if gen and gen != self._search_generation:
            logger.debug(f"Ignoring stale search results (gen {gen} != current {self._search_generation})")
            return

        self._toggle_inputs(True)
        self._set_loading_active(False)

        if "error" in results:
            if is_live:
                self.status_label.setText("No results found")
                return
            self._handle_error(results["error"])
            return

        self.results_list.clear()
        self._stop_active_image_fetchers()

        filtered_results = results.get("results", [])
        raw_total = int(results.get("raw_total", len(filtered_results)))

        if not filtered_results:
            self.status_label.setText("No results found")
            return

        for idx, game in enumerate(filtered_results):
            self._add_game_to_list(game, gen=gen, delay_fetch=(idx >= 4))

        hidden_count = max(0, raw_total - len(filtered_results))
        status_msg = f"Found {len(filtered_results)} games"
        if hidden_count > 0:
            status_msg += f" ({hidden_count} hidden)"
        self.status_label.setText(status_msg + ". Double-click to download")

    _is_blacklisted = staticmethod(is_blacklisted)
    _extract_game_name = staticmethod(extract_game_name)
    _extract_app_id = staticmethod(extract_app_id)
    _is_blacklisted_result = staticmethod(is_blacklisted_result)

    def _add_game_to_list(self, game: Dict, gen: int = 0, delay_fetch: bool = False):
        # Support both legacy and newer API response keys.
        app_id = self._extract_app_id(game)
        if not app_id:
            logger.debug(f"Skipping search result without AppID: {game}")
            return

        name = self._extract_game_name(game) or "Unknown"

        in_library = False
        if self.parent_window and hasattr(self.parent_window, "game_manager") and self.parent_window.game_manager:
            if self.parent_window.game_manager.get_game(app_id) is not None:
                in_library = True

        is_cached = self._is_app_cached(app_id)

        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 98))

        item.setData(Qt.ItemDataRole.UserRole, app_id)
        self.results_list.addItem(item)

        widget = SearchItemWidget(name, app_id, in_library, is_cached=is_cached, parent=self)
        self.results_list.setItemWidget(item, widget)

        if delay_fetch:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda w=widget, a=app_id, g=gen, t=timer: self._delayed_fetch_callback(w, a, g, t))
            self._pending_image_timers.append(timer)
            timer.start(1200)
        else:
            self._fetch_item_image(widget, app_id, gen)

    def _delayed_fetch_callback(self, widget, app_id: str, gen: int, timer: QTimer):
        if timer in self._pending_image_timers:
            self._pending_image_timers.remove(timer)
        if gen != self._search_generation:
            return
        try:
            from PyQt6 import sip
            if sip.isdeleted(widget):
                return
        except Exception:
            pass
        self._fetch_item_image(widget, app_id, gen)

    # --------------------------
    # Image Fetching
    # --------------------------

    def _fetch_item_image(self, widget, app_id: str, gen: int = 0):
        url = ImageFetcher.get_header_image_url(app_id)
        fetcher = ImageFetcher(url, ephemeral=True)

        self._active_image_fetchers[app_id] = fetcher

        # Connect signals directly with widget and generation check
        fetcher.finished.connect(lambda data, w=widget, a=app_id, g=gen: self._on_image_ready(data, w, a, g))
        fetcher.start()

    def _on_image_ready(self, image_data, widget, app_id: str, gen: int = 0):
        # Cleanup fetcher reference
        self._active_image_fetchers.pop(app_id, None)

        if gen != self._search_generation:
            return

        try:
            from PyQt6 import sip
            if sip.isdeleted(widget):
                return
        except Exception:
            pass

        if image_data:
            try:
                pixmap = QPixmap()
                pixmap.loadFromData(image_data)
                if not pixmap.isNull():
                    target_size = QSize(230, 108)
                    scaled = pixmap.scaled(
                        target_size,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )

                    cropped_faded = QPixmap(target_size)
                    cropped_faded.fill(Qt.GlobalColor.transparent)

                    painter = QPainter(cropped_faded)
                    dx = (target_size.width() - scaled.width()) // 2
                    dy = (target_size.height() - scaled.height()) // 2
                    painter.drawPixmap(dx, dy, scaled)

                    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                    gradient = QLinearGradient(0, 0, target_size.width(), 0)
                    gradient.setColorAt(0.0, QColor(0, 0, 0, 255))
                    gradient.setColorAt(0.5, QColor(0, 0, 0, 255))
                    gradient.setColorAt(1.0, QColor(0, 0, 0, 0))

                    painter.fillRect(cropped_faded.rect(), QBrush(gradient))
                    painter.end()

                    widget.set_image(cropped_faded)
            except Exception as e:
                logger.debug(f"Failed to process image for AppID {app_id}: {e}")

        # Find fetcher and delete it safely
        sender = self.sender()
        if sender:
            sender.deleteLater()

    # --------------------------
    # Download Logic
    # --------------------------

    def check_and_download_manifest(self, app_id, branch: str = "public"):
        """
        Worker function to check if cached manifest zip exists and is up to date with Steam.
        If up to date, returns (cached_path, None).
        Otherwise, downloads the manifest via morrenus_api.download_manifest and returns the result.
        """
        self._current_fetching_appid = str(app_id)
        res_tuple, refetched_depots, missing_depots_info_patch = verify_or_download_manifest(
            app_id, branch=branch, discovered_branches=getattr(self, "_discovered_branches", None)
        )
        self._last_refetched_depots = refetched_depots
        self._last_missing_depots_info_patch = missing_depots_info_patch
        return res_tuple

    def _fetch_branches_and_prompt(self, app_id: str):
        """Worker function to fetch branches for an AppID."""
        from core.steam_api import get_app_branches
        try:
            branches = get_app_branches(app_id, force_refresh=True)
            return app_id, branches
        except BaseException as e:
            logger.error(f"Failed to fetch branches for AppID {app_id}: {e}")
            return app_id, {"public": {"buildid": ""}}

    def _on_branches_fetched(self, result):
        app_id, branches = result
        self._toggle_inputs(True)
        self._set_loading_active(False)

        selected_branch = "public"
        if branches and len(branches) > 1:
            from PyQt6.QtWidgets import QInputDialog
            items = sorted(branches.keys(), key=lambda k: (0 if k == "public" else 1, k))

            display_items = []
            for b in items:
                b_info = branches[b]
                bid = b_info.get("buildid", "") if isinstance(b_info, dict) else ""
                display_items.append(f"{b} (Build: {bid})" if bid else b)

            item, ok = QInputDialog.getItem(
                self,
                "Select Branch",
                f"Multiple branches found for AppID {app_id}.\nSelect which branch manifest to fetch:",
                display_items,
                0,
                False
            )
            if ok and item:
                idx = display_items.index(item)
                selected_branch = items[idx]
            else:
                self.status_label.setText("Fetch cancelled.")
                return

        self._discovered_branches = branches or {"public": {}}
        self._current_selected_branch = selected_branch
        self.settings.setValue(f"selected_branch/{app_id}", selected_branch)
        self._toggle_inputs(False)
        self._set_loading_active(True)
        self.status_label.setText(f"Checking updates & fetching manifest for App ID {app_id} (Branch: {selected_branch})...")

        worker = self.task_runner.run(self.check_and_download_manifest, app_id, selected_branch)
        worker.finished.connect(self.on_download_finished)
        worker.error.connect(self.on_task_error)

    def _on_item_clicked(self, item):
        if not item:
            return
        app_id = item.data(Qt.ItemDataRole.UserRole)
        if not app_id:
            return
        widget = self.results_list.itemWidget(item)
        if not widget or not hasattr(widget, "set_proton_fetching"):
            return

        from core.ratings import _load_protondb_cache
        cache = _load_protondb_cache()
        if str(app_id) in cache:
            widget.update_ratings()
            return

        widget.set_proton_fetching()

        def _worker():
            try:
                import requests
                from core.ratings import _save_protondb_entry
                url = f"https://www.protondb.com/api/v1/reports/summaries/{app_id}.json"
                resp = requests.get(url, timeout=10)
                tier = "unknown"
                ok = False
                if resp.status_code == 200:
                    tier = resp.json().get("tier", "unknown").lower()
                    ok = True
                _save_protondb_entry(str(app_id), tier, ok)
            except Exception as e:
                logger.debug(f"ProtonDB on-demand fetch failed for {app_id}: {e}")

            from PyQt6.QtCore import QTimer
            def _finish(w=widget):
                if w:
                    w._is_fetching_proton = False
                    w.update_ratings()
            QTimer.singleShot(0, _finish)

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    def on_item_double_clicked(self, item):
        self._on_item_clicked(item)
        # Cancel any active background update checks to free up the Steam connection
        if self.parent_window and hasattr(self.parent_window, "game_manager") and self.parent_window.game_manager:
            try:
                self.parent_window.game_manager.cancel_update_checks()
            except Exception as e:
                logger.error(f"Error cancelling background update checks: {e}")

        app_id = item.data(Qt.ItemDataRole.UserRole)
        if not app_id:
            return

        applist_2_0_enabled = True

        in_library = False
        if self.parent_window and hasattr(self.parent_window, "game_manager") and self.parent_window.game_manager:
            if self.parent_window.game_manager.get_game(app_id) is not None:
                in_library = True

        if applist_2_0_enabled and in_library:
            self.accept()
            from ui.dialogs.gamelibrary import GameLibraryDialog
            dialog = GameLibraryDialog(self.parent_window, show_details_for_appid=app_id)
            dialog.exec()
            return

        self._toggle_inputs(False)
        self._set_loading_active(True)
        self.status_label.setText(f"Checking branches for App ID {app_id}...")
        worker = self.task_runner.run(self._fetch_branches_and_prompt, app_id)
        worker.finished.connect(self._on_branches_fetched)
        worker.error.connect(self.on_task_error)

    def on_download_finished(self, result):
        filepath, error_msg = result

        if error_msg:
            self._handle_error(error_msg)
            self._toggle_inputs(True)
            return

        logger.info(f"Manifest downloaded: {filepath}")
        if filepath:
            m = re.search(r"accela_fetch_(\d+)", str(filepath))
            if m and hasattr(self, "_cached_app_ids"):
                self._cached_app_ids.add(m.group(1))
        
        branch = getattr(self, "_current_selected_branch", "public")
        metadata = {"branch": branch}
        
        self.status_label.setText("Processing manifest data...")
        self._toggle_inputs(False)

        def _parse_task():
            try:
                from core.tasks.process_zip_task import ProcessZipTask
                zip_task = ProcessZipTask()
                return zip_task.run(filepath, metadata=metadata)
            except Exception as e:
                logger.warning(f"Failed to pre-parse zip for depot selection: {e}", exc_info=True)
                return None

        self._parse_task_runner = TaskRunner(self)
        worker = self._parse_task_runner.run(_parse_task)
        worker.finished.connect(lambda parsed_data: self._on_parse_finished(parsed_data, filepath, branch, metadata))

    def _on_parse_finished(self, parsed_data, filepath, branch, metadata):
        self._toggle_inputs(True)
        self._set_loading_active(False)

        if not parsed_data or not parsed_data.get("depots"):
            app_id = str(self._current_fetching_appid or (parsed_data and parsed_data.get("appid")) or "")
            if not getattr(self, "_is_manifest_retry", False) and app_id:
                logger.warning(
                    f"[FetchManifest] Manifest data for App {app_id} was empty or unparseable ({filepath}). "
                    "Deleting bad cache and retrying fresh full download from Hubcap..."
                )
                self._is_manifest_retry = True
                try:
                    if filepath and os.path.exists(filepath):
                        os.remove(filepath)
                except Exception as _del_err:
                    logger.debug(f"Could not remove corrupt zip {filepath}: {_del_err}")

                self.status_label.setText(f"Cached manifest was invalid. Re-downloading fresh bundle for App {app_id}...")
                self._toggle_inputs(False)
                self._set_loading_active(True)
                worker = self.task_runner.run(self.check_and_download_manifest, app_id, branch)
                worker.finished.connect(self.on_download_finished)
                worker.error.connect(self.on_task_error)
                return
            else:
                self._is_manifest_retry = False
                logger.error(f"[FetchManifest] Failed to parse valid manifest data for {filepath}")
                self.status_label.setText("Error: Could not retrieve valid depot manifests for game.")
                return

        self._is_manifest_retry = False
        if parsed_data.get("appid"):
            appid = str(parsed_data["appid"])
            self.settings.setValue(f"selected_branch/{appid}", branch)

        if parsed_data.get("depots"):
            from ui.dialogs.depotselection import DepotSelectionDialog
            from utils.settings import get_settings
            from utils.paths import is_valid_download_directory
            settings = get_settings()
            auto_skip = settings.value("auto_skip_single_choice", False, type=bool)
            default_dl = settings.value("default_download_directory", "", type=str)
            has_default_dl = bool(default_dl and is_valid_download_directory(default_dl))
            depots = parsed_data.get("depots")
            appid = str(parsed_data.get("appid", ""))
            missing_depots = parsed_data.get("missing_depots_from_hubcap") or []
            
            selected_depots = None
            is_single = (len(depots) == 1 and not missing_depots)

            show_timer = (is_single and auto_skip and has_default_dl)
            open_dialog = False

            if show_timer:
                dlg = SingleDepotTimerDialog(self, "Single Depot Option", "Game has only one depot.\n\nProceed to download and add it to queue?", seconds=3)
                res = dlg.exec()
                if res == SingleDepotTimerDialog.ACTION_YES:
                    selected_depots = list(depots.keys())
                    metadata["library_path"] = default_dl
                elif res == SingleDepotTimerDialog.ACTION_MANUAL:
                    open_dialog = True
                else:
                    logger.info("User cancelled single-depot download from search window.")
                    self.status_label.setText("Download cancelled.")
                    return
            else:
                open_dialog = True

            if open_dialog:
                saved_selection = None
                if appid:
                    raw = settings.value(f"depot_selection/{appid}", "", type=str)
                    if raw:
                        try:
                            import json
                            saved_data = json.loads(raw)
                            saved_selection = saved_data.get("selected", [])
                        except Exception:
                            pass

                combined_missing_info = dict(parsed_data.get("missing_depots_info") or {})
                if hasattr(self, "_last_missing_depots_info_patch") and self._last_missing_depots_info_patch:
                    for p_did, p_val in self._last_missing_depots_info_patch.items():
                        combined_missing_info.setdefault(str(p_did), {}).update(p_val)

                combined_refetched = list(set(
                    (parsed_data.get("refetched_depots") or []) + getattr(self, "_last_refetched_depots", [])
                ))

                branches_dict = getattr(self, "_discovered_branches", None) or {"public": {}}
                current_bid = parsed_data.get("buildid") or ""

                depot_dialog = DepotSelectionDialog(
                    parsed_data["appid"],
                    parsed_data.get("game_name", ""),
                    depots,
                    parsed_data.get("header_url"),
                    self,
                    selected_depots=saved_selection,
                    is_single_depot=is_single,
                    missing_hubcap_depots=missing_depots,
                    missing_depots_info=combined_missing_info,
                    library_path=metadata.get("library_path"),
                    refetched_depots=combined_refetched,
                    branch=branch,
                    branches=branches_dict,
                    current_build_id=current_bid,
                )
                depot_dialog.raise_()
                depot_dialog.activateWindow()

                depot_res = depot_dialog.exec()
                if not depot_res:
                    logger.info("User cancelled depot selection.")
                    self.status_label.setText("Download cancelled.")
                    return

                selected_depots = depot_dialog.get_selected_depots()
                selected_storage = depot_dialog.get_selected_storage()
                if selected_storage:
                    metadata["library_path"] = selected_storage

                chosen_branch = depot_dialog.get_selected_branch() if hasattr(depot_dialog, "get_selected_branch") else None
                if chosen_branch:
                    metadata["branch"] = chosen_branch
                    self.settings.setValue(f"selected_branch/{appid}", chosen_branch)

                if hasattr(depot_dialog, "is_build_pinned") and depot_dialog.is_build_pinned():
                    pinned_bid = depot_dialog.get_selected_build()
                    metadata["pin_build"] = True
                    metadata["pinned_build_id"] = pinned_bid
                    metadata["buildid"] = pinned_bid
                    metadata["is_rollback"] = True
                    if appid:
                        self.settings.setValue(f"pin_build/{appid}", True)
                        logger.info(f"Pinned build {pinned_bid} for AppID {appid} in fetchmanifest")

                if hasattr(depot_dialog, "get_manifest_overrides"):
                    overrides = depot_dialog.get_manifest_overrides()
                    if overrides:
                        metadata["manifest_overrides"] = overrides
            
            if selected_depots:
                metadata["selected_depots_list"] = selected_depots
                metadata["game_name"] = parsed_data.get("game_name", "")
                if appid:
                    metadata["appid"] = str(appid)
                    metadata["app_id"] = str(appid)
                    try:
                        import json
                        settings.setValue(
                            f"depot_selection/{appid}",
                            json.dumps({
                                "selected": selected_depots,
                                "all_available": list(depots.keys()),
                                "descriptions": {d: depots.get(d, {}).get("desc", "") for d in selected_depots}
                            })
                        )
                        logger.info(f"Persisted depot selection for AppID {appid} in fetchmanifest: {selected_depots}")
                    except Exception as e:
                        logger.warning(f"Failed to cache depot selection in fetchmanifest: {e}")
            else:
                logger.info("User cancelled depot selection.")
                self.status_label.setText("Download cancelled.")
                return

        self.status_label.setText("Download complete! Adding to queue")

        if parsed_data and parsed_data.get("appid"):
            ImageFetcher.promote_to_permanent_cache(parsed_data["appid"])

        if self.parent_window and hasattr(self.parent_window, "job_queue"):
            self.parent_window.job_queue.add_job(filepath, metadata)

        self.accept()

    # --------------------------
    # Helpers & Cleanup
    # --------------------------

    def _toggle_inputs(self, enabled: bool):
        self.search_input.setEnabled(enabled)
        self.results_list.setEnabled(enabled)

    def _handle_error(self, message):
        logger.error(f"Operation failed: {message}")
        self._set_loading_active(False)
        QMessageBox.critical(self, "Error", message)
        self.status_label.setText("Error occurred")

    def on_task_error(self, error_info):
        _, error_value, _ = error_info
        self._handle_error(str(error_value))
        self._toggle_inputs(True)

    def _stop_active_image_fetchers(self):
        """Safely stops all running image fetchers and cancels pending timers."""
        if hasattr(self, "_pending_image_timers"):
            for timer in list(self._pending_image_timers):
                try:
                    timer.stop()
                except Exception:
                    pass
            self._pending_image_timers.clear()

        for fetcher in list(self._active_image_fetchers.values()):
            try:
                fetcher.stop()
            except RuntimeError:
                pass
        self._active_image_fetchers.clear()

    def _download_workshop(self):
        api_key = self.settings.value("morrenus_api_key", "", type=str) if self.settings else ""
        if not api_key:
            QMessageBox.warning(self, "No API Key", "Please enter your Hubcab API key in ACCELA Settings first.")
            return

        raw = self.ids_input.toPlainText().strip()
        if not raw:
            QMessageBox.warning(self, "No IDs", "Please enter at least one Workshop ID or URL.")
            return

        wids = parse_workshop_ids(raw)
        if not wids:
            QMessageBox.warning(self, "No Valid IDs", "Could not parse any Workshop IDs from the input.")
            return

        max_downloads = self.settings.value("workshop_max_downloads", 4, type=int) if self.settings else 4
        cellid = self.settings.value("workshop_cell_id", "", type=str) if self.settings else ""
        steam_integration = self.settings.value("workshop_steam_enabled", True, type=bool) if self.settings else True

        dest_path = ""
        if steam_integration:
            libraries = steam_helpers.get_steam_libraries()
            if libraries:
                auto_skip_single_choice = self.settings.value("auto_skip_single_choice", False, type=bool) if self.settings else False
                if auto_skip_single_choice and len(libraries) == 1:
                    dest_path = libraries[0]
                else:
                    dialog = SteamLibraryDialog(libraries, self)
                    if dialog.exec():
                        dest_path = dialog.get_selected_path()
                    else:
                        return
            else:
                dest_path = QFileDialog.getExistingDirectory(self, "Select Steam Library Folder")
                if not dest_path:
                    return
        else:
            dest_path = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
            if not dest_path:
                return


        main_window = self.parent_window
        if main_window and hasattr(main_window, "job_queue") and main_window.job_queue:
            main_window.job_queue.add_workshop_job(wids, api_key, max_downloads, cellid, steam_integration, dest_path)
            QMessageBox.information(self, "Job Queued", f"Successfully added Workshop download job with {len(wids)} items to the queue.")
            self.accept()
        else:
            QMessageBox.critical(self, "Error", "Could not access the application job queue.")

    def _rotate_placeholder(self) -> None:
        if not hasattr(self, "search_input") or not self.search_input:
            return
        if self.search_input.text() or self.search_input.hasFocus():
            return
        self._placeholder_idx = (self._placeholder_idx + 1) % len(SEARCH_PLACEHOLDERS)
        self.search_input.setPlaceholderText(SEARCH_PLACEHOLDERS[self._placeholder_idx])

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if hasattr(self, "_origins_movie") and self._origins_movie and self._origins_movie.state() == QMovie.MovieState.Running:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            current_pixmap = self._origins_movie.currentPixmap()
            if not current_pixmap.isNull():
                painter.setOpacity(0.18)
                scaled_pixmap = current_pixmap.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation
                )
                x = (self.width() - scaled_pixmap.width()) // 2
                y = (self.height() - scaled_pixmap.height()) // 2
                painter.drawPixmap(x, y, scaled_pixmap)

    def accept(self):
        if hasattr(self, "_origins_movie") and self._origins_movie:
            self._origins_movie.stop()
            self._origins_movie = None
        if hasattr(self, "_placeholder_timer") and self._placeholder_timer:
            self._placeholder_timer.stop()
        self._stop_active_image_fetchers()
        super().accept()

    def reject(self):
        if hasattr(self, "_origins_movie") and self._origins_movie:
            self._origins_movie.stop()
            self._origins_movie = None
        if hasattr(self, "_placeholder_timer") and self._placeholder_timer:
            self._placeholder_timer.stop()
        self._stop_active_image_fetchers()
        super().reject()

    def closeEvent(self, event):
        if hasattr(self, "_origins_movie") and self._origins_movie:
            self._origins_movie.stop()
            self._origins_movie = None
        if hasattr(self, "_placeholder_timer") and self._placeholder_timer:
            self._placeholder_timer.stop()
        self._stop_active_image_fetchers()

        if self.task_runner:
            try:
                self.task_runner.stop()
            except RuntimeError as e:
                logger.debug(f"Error stopping task runner: {e}")

        super().closeEvent(event)

