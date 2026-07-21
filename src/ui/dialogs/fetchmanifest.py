import logging
import math
import re
import time
from typing import Any, Dict

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPixmap
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
)

from core import morrenus_api
from utils.image_fetcher import ImageFetcher
from utils.task_runner import TaskRunner

logger = logging.getLogger(__name__)

# Constants
_API_STATS_CACHE_DURATION = 60  # seconds
_BLACKLIST_PATTERNS = [
    r"soundtracks?",
    r"sound tracks?",
    r"ost",
    r"original soundtrack",
    r"piano collections?",
    r"orchestras?",
    r"orchestral",
    r"world tour",
    r"concerts?",
    r"videos?",
    r"artbooks?",
    r"graphic novels?",
    r"dlcs?",
    r"demos?",
    r"dedicated server",
    r"servers?",
    r"tools?",
    r"sdks?",
    r"3d print model",
    r"wallpapers?",
    r"digital contents?",
    r"mod organizer",
    r"ultimate collections?",
    r"seekers edition",
    r"trailers?",
    r"shorts?",
    r"teasers?",
    r"the final hours",
    r"season pass(?:es)?",
    r"content packs?",
    r"free editions?",
    r"upgrades?",
    r"mini soundtrack",
    r"extra tracks?",
    r"trial versions?",
    r"beta(?:\s+test)?",
    r"benchmarks?",
]
_BLACKLIST_NAME_RE = re.compile(r"\b(?:" + "|".join(_BLACKLIST_PATTERNS) + r")\b")
_BLACKLIST_META_TOKENS = {
    "dlc",
    "demo",
    "soundtrack",
    "music",
    "video",
    "ost",
    "piano",
    "orchestra",
    "tool",
    "server",
    "sdk",
    "artbook",
    "graphic novel",
    "trailer",
    "short",
    "teaser",
    "season pass",
    "content pack",
    "free edition",
    "upgrade",
    "trial",
    "beta",
    "benchmark",
    "extra tracks",
}
_RESULT_NAME_KEYS = ("game_name", "name", "title")
_RESULT_ID_KEYS = ("game_id", "appid", "app_id", "id")
_RESULT_FLAG_KEYS = ("is_dlc", "is_demo", "is_tool", "is_server", "is_soundtrack")
_RESULT_META_KEYS = ("type", "app_type", "content_type", "category", "kind")
_RESULT_META_LIST_KEYS = ("tags", "genres", "categories", "types")
_LANGUAGE_VARIANT_RE = re.compile(
    r"\((english|french|german|russian|spanish|italian|japanese|korean|portuguese|polish|turkish|chinese)\)$",
    re.IGNORECASE,
)

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setWindowTitle("Fetch Manifest from Hubcap API")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)

        self.settings = getattr(parent, "settings", None)
        if not self.settings:
            from utils.settings import get_settings
            self.settings = get_settings()

        self.accent_color = "#C06C84"
        self.background_color = "#000000"
        if self.settings:
            self.accent_color = self.settings.value("accent_color", "#C06C84")
            self.background_color = self.settings.value("background_color", "#000000")

        self.task_runner = TaskRunner()
        self._active_image_fetchers = {}

        self._init_ui()
        logger.debug("FetchManifestDialog initialized.")

        self._request_api_status_update()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        applist_2_0_enabled = True

        if applist_2_0_enabled:
            self.setStyleSheet(
                f"""
                QDialog {{
                    background-color: {self.background_color};
                    color: {self.accent_color};
                }}
                QLabel {{
                    color: {self.accent_color};
                }}
                QLineEdit {{
                    background-color: #111111;
                    color: {self.accent_color};
                    border: 1px solid #333333;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-size: 12px;
                }}
                QLineEdit:focus {{
                    border: 1px solid {self.accent_color};
                }}
                QListWidget {{
                    background-color: {self.background_color};
                    border: 1px solid #333333;
                    border-radius: 6px;
                }}
                QListWidget::item {{
                    background-color: {self.background_color};
                    border-bottom: 1px solid #222222;
                    color: {self.accent_color};
                    padding: 6px;
                    border-radius: 4px;
                }}
                QListWidget::item:hover {{
                    background-color: #111111;
                }}
                QListWidget::item:selected {{
                    background-color: #1a1a1a;
                    color: {self.accent_color};
                }}
                """
            )

        # 1. API Status Bar
        self._create_api_status_bar(layout)

        # 2. Search Input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search for a game...")
        self.search_input.returnPressed.connect(self.on_search)
        layout.addWidget(self.search_input)

        # 3. Results List
        self.results_list = QListWidget()
        # Aspect Ratio approx 2.15 (230x108)
        self.results_list.setIconSize(QSize(230, 108))
        self.results_list.setSpacing(5)
        self.results_list.itemDoubleClicked.connect(self.on_item_double_clicked)
        layout.addWidget(self.results_list)

        # 4. Status Label
        self.status_label = QLabel("Search for a game to begin")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

    def _create_api_status_bar(self, parent_layout):
        """Builds the top status bar widget."""
        status_widget = QWidget()
        container = QHBoxLayout(status_widget)
        container.setContentsMargins(0, 0, 0, 5)
        container.setSpacing(10)

        # Connection Dot
        self.api_status_dot = QLabel()
        self.api_status_dot.setFixedSize(12, 12)
        self.api_status_dot.setStyleSheet(
            "border-radius: 6px; background-color: #95a5a6;"
        )  # Gray

        self.api_status_text = QLabel("Checking...")

        # Group Dot + Text
        conn_layout = QHBoxLayout()
        conn_layout.setSpacing(5)
        conn_layout.addWidget(self.api_status_dot)
        conn_layout.addWidget(self.api_status_text)

        container.addLayout(conn_layout)
        container.addStretch(1)

        # User Info
        self.username_label = QLabel("User: --")
        container.addWidget(self.username_label)

        self.usage_label = QLabel("Daily: --")
        container.addWidget(self.usage_label)

        parent_layout.addWidget(status_widget)

    # --------------------------
    # Status / Health Logic
    # --------------------------

    def _request_api_status_update(self):
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
        health = result.get("health", {})
        is_healthy = health.get("status") == "healthy"

        # Update Connection Status
        if is_healthy:
            self.api_status_dot.setStyleSheet(
                "border-radius: 6px; background-color: #2ecc71;"
            )  # Green
            self.api_status_text.setText("Online")
        else:
            self.api_status_dot.setStyleSheet(
                "border-radius: 6px; background-color: #e74c3c;"
            )  # Red
            self.api_status_text.setText("Offline")

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
        logger.error(f"Status check failed: {error_info}")
        self.api_status_dot.setStyleSheet(
            "border-radius: 6px; background-color: #e74c3c;"
        )
        self.api_status_text.setText("Offline")

    # --------------------------
    # Search Logic
    # --------------------------

    def on_search(self):
        query = self.search_input.text().strip()
        if len(query) < 2:
            self.status_label.setText("Enter at least 2 characters")
            return

        # Reset UI
        self.results_list.clear()
        self._stop_active_image_fetchers()
        self._toggle_inputs(False)
        self.status_label.setText("Searching...")

        # Run search + filtering in a worker thread.
        worker = self.task_runner.run(self._search_and_filter_results, query)
        worker.finished.connect(self.on_search_finished)
        worker.error.connect(self.on_task_error)

    def _search_and_filter_results(self, query: str) -> Dict[str, Any]:
        """Worker: search Morrenus and apply fast local relevance filtering."""
        results = morrenus_api.search_games(query)
        if isinstance(results, dict) and "error" in results:
            return results

        game_results = results.get("results", []) if isinstance(results, dict) else []
        if not isinstance(game_results, list):
            return {"results": [], "raw_total": 0}

        from utils.settings import get_settings
        filter_blacklist = get_settings().value("filter_search_blacklist", False, type=bool)

        filtered = [
            g
            for g in game_results
            if isinstance(g, dict)
            and (not filter_blacklist or not self._is_blacklisted_result(g))
            and g.get("manifest_available", True) is not False
        ]

        ranked = sorted(filtered, key=lambda g: self._relevance_sort_key(g, query))
        deduped = self._dedupe_results_by_name(ranked)
        return {"results": deduped, "raw_total": len(game_results)}

    @staticmethod
    def _normalize_for_match(value: str) -> str:
        lowered = (value or "").strip().lower()
        if not lowered:
            return ""
        return re.sub(r"[^a-z0-9]+", " ", lowered).strip()

    @classmethod
    def _relevance_score(cls, game: Dict[str, Any], query: str) -> int:
        name = cls._extract_game_name(game)
        if not name:
            return -100000

        normalized_name = cls._normalize_for_match(name)
        normalized_query = cls._normalize_for_match(query)
        if not normalized_query:
            return 0

        score = 0
        if normalized_name == normalized_query:
            score += 5000
        if normalized_name.startswith(normalized_query):
            score += 2500
        if f" {normalized_query} " in f" {normalized_name} ":
            score += 1500

        name_tokens = normalized_name.split()
        query_tokens = normalized_query.split()
        token_hits = 0
        prefix_hits = 0
        for token in query_tokens:
            if token in name_tokens:
                token_hits += 1
            elif any(part.startswith(token) for part in name_tokens):
                prefix_hits += 1

        score += token_hits * 350
        score += prefix_hits * 120
        score -= min(700, max(0, len(normalized_name) - len(normalized_query)) * 5)

        try:
            manifest_size = int(game.get("manifest_size") or 0)
        except (TypeError, ValueError):
            manifest_size = 0
        if manifest_size > 0:
            score += min(120, int(math.log10(manifest_size + 1) * 20))

        return score

    @classmethod
    def _relevance_sort_key(cls, game: Dict[str, Any], query: str) -> tuple:
        score = cls._relevance_score(game, query)
        name = cls._extract_game_name(game).lower()
        return -score, name

    @classmethod
    def _dedupe_results_by_name(
        cls, games: list[Dict[str, Any]]
    ) -> list[Dict[str, Any]]:
        deduped = []
        seen_names = set()
        for game in games:
            normalized_name = cls._normalize_for_match(cls._extract_game_name(game))
            if not normalized_name or normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            deduped.append(game)
        return deduped

    @staticmethod
    def _manifest_size(game: Dict[str, Any]) -> int:
        try:
            return int(game.get("manifest_size") or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _is_likely_media_variant(cls, game: Dict[str, Any]) -> bool:
        name = cls._extract_game_name(game).lower()
        if not name:
            return False

        size = cls._manifest_size(game)
        if size <= 0:
            return False

        if size <= 5000 and _LANGUAGE_VARIANT_RE.search(name):
            return True

        if size <= 2000 and any(
            token in name
            for token in ("trailer", "short", "teaser", "turrets", "behind the scenes")
        ):
            return True

        return False

    def on_search_finished(self, results):
        self._toggle_inputs(True)

        if "error" in results:
            self._handle_error(results["error"])
            return

        filtered_results = results.get("results", [])
        raw_total = int(results.get("raw_total", len(filtered_results)))

        if not filtered_results:
            self.status_label.setText("No results found")
            return

        for game in filtered_results:
            self._add_game_to_list(game)

        hidden_count = max(0, raw_total - len(filtered_results))
        status_msg = f"Found {len(filtered_results)} games"
        if hidden_count > 0:
            status_msg += f" ({hidden_count} hidden)"
        self.status_label.setText(status_msg + ". Double-click to download")

    @staticmethod
    def _is_blacklisted(game_name: str) -> bool:
        """Checks if a game name contains blacklisted keywords."""
        return bool(_BLACKLIST_NAME_RE.search((game_name or "").lower()))

    @staticmethod
    def _extract_game_name(game: Dict[str, Any]) -> str:
        for key in _RESULT_NAME_KEYS:
            value = game.get(key)
            if isinstance(value, str):
                name = value.strip()
                if name:
                    return name
        return ""

    @staticmethod
    def _extract_app_id(game: Dict[str, Any]) -> str:
        for key in _RESULT_ID_KEYS:
            value = game.get(key)
            if value is None:
                continue
            app_id = str(value).strip()
            if app_id:
                return app_id
        return ""

    @classmethod
    def _is_blacklisted_result(cls, game: Dict[str, Any]) -> bool:
        if not isinstance(game, dict):
            return False

        for flag_key in _RESULT_FLAG_KEYS:
            if bool(game.get(flag_key)):
                return True

        meta_parts = []
        for key in _RESULT_META_KEYS:
            value = game.get(key)
            if isinstance(value, str) and value:
                meta_parts.append(value.lower())

        for key in _RESULT_META_LIST_KEYS:
            value = game.get(key)
            if isinstance(value, str):
                meta_parts.append(value.lower())
            elif isinstance(value, (list, tuple, set)):
                meta_parts.extend(
                    str(item).lower() for item in value if item is not None
                )

        meta_text = " ".join(meta_parts)
        if meta_text and any(token in meta_text for token in _BLACKLIST_META_TOKENS):
            return True

        return cls._is_blacklisted(cls._extract_game_name(game))

    def _add_game_to_list(self, game: Dict):
        # Support both legacy and newer API response keys.
        app_id = self._extract_app_id(game)
        if not app_id:
            logger.debug(f"Skipping search result without AppID: {game}")
            return

        name = self._extract_game_name(game) or "Unknown"

        applist_2_0_enabled = True

        in_library = False
        if self.parent_window and hasattr(self.parent_window, "game_manager") and self.parent_window.game_manager:
            if self.parent_window.game_manager.get_game(app_id) is not None:
                in_library = True

        if applist_2_0_enabled and in_library:
            item = QListWidgetItem(f"{name} [In Library] (AppID: {app_id})")
            item.setForeground(QColor("#77DD77"))
        else:
            item = QListWidgetItem(f"{name} (AppID: {app_id})")

        item.setData(Qt.ItemDataRole.UserRole, app_id)
        self.results_list.addItem(item)

        self._fetch_item_image(item, app_id)

    # --------------------------
    # Image Fetching
    # --------------------------

    def _fetch_item_image(self, item, app_id):
        # app_id is passed as string here
        url = ImageFetcher.get_header_image_url(app_id)
        fetcher = ImageFetcher(url)

        self._active_image_fetchers[app_id] = fetcher

        # Connect signals
        fetcher.finished.connect(lambda data: self._on_image_ready(data, item, app_id))
        fetcher.start()

    def _on_image_ready(self, image_data, item, app_id):
        # Cleanup fetcher reference
        self._active_image_fetchers.pop(app_id, None)

        if image_data:
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            if not pixmap.isNull():
                item.setIcon(QIcon(pixmap))

        # Find fetcher and delete it safely
        sender = self.sender()
        if sender:
            sender.deleteLater()

    # --------------------------
    # Download Logic
    # --------------------------

    def check_and_download_manifest(self, app_id):
        """
        Worker function to check if cached manifest zip exists and is up to date with Steam.
        If up to date, returns (cached_path, None).
        Otherwise, downloads the manifest via morrenus_api.download_manifest and returns the result.
        """
        try:
            import os
            import zipfile
            from pathlib import Path
            from utils.helpers import get_base_path
            from core.steam_api import _fetch_with_steam_client, _fetch_with_web_api
            from managers.db_manager import DatabaseManager

            manifests_dir = Path(get_base_path()) / "hubcap_manifests"
            cached_path = manifests_dir / f"accela_fetch_{app_id}.zip"

            if cached_path.exists():
                logger.info(f"Checking updates for cached manifest {app_id}")
                # 1. Parse the zip to find the manifests inside it and the app token
                local_manifests = {}
                app_token = None
                try:
                    with zipfile.ZipFile(cached_path, "r") as zip_ref:
                        # Find LUA file for token
                        lua_files = [f for f in zip_ref.namelist() if f.endswith(".lua")]
                        if lua_files:
                            try:
                                lua_content = zip_ref.read(lua_files[0]).decode("utf-8", errors="ignore")
                                token_match = re.search(r'addtoken\s*\(\s*\d+\s*,\s*"([^"]+)"\s*\)', lua_content, re.IGNORECASE)
                                if token_match:
                                    app_token = token_match.group(1)
                            except Exception as e:
                                logger.debug(f"Failed to read LUA from cached zip: {e}")

                        # Find manifest files
                        manifest_files = [
                            os.path.basename(f)
                            for f in zip_ref.namelist()
                            if f.endswith(".manifest")
                        ]
                        for filename in manifest_files:
                            parts = filename.replace(".manifest", "").split("_")
                            if len(parts) == 2:
                                local_manifests[parts[0]] = parts[1]
                except Exception as e:
                    logger.warning(f"Failed to parse cached zip {cached_path}: {e}. Will redownload.")
                    return morrenus_api.download_manifest(app_id)

                if not local_manifests:
                    logger.warning(f"No manifests found in cached zip {cached_path}. Will redownload.")
                    return morrenus_api.download_manifest(app_id)

                # 2. Get current depot info from Steam API (bypassing DB cache)
                try:
                    steam_client_data = _fetch_with_steam_client(app_id, app_token)
                    if not steam_client_data or not steam_client_data.get("depots"):
                        logger.debug("steam.client fetch failed or empty depots, falling back to Web API")
                        steam_client_data = _fetch_with_web_api(app_id)
                    
                    if steam_client_data:
                        # Update database with the latest info
                        try:
                            db = DatabaseManager()
                            db.upsert_app_info(app_id, steam_client_data)
                        except Exception as db_err:
                            logger.debug(f"Failed to update database with fresh app info: {db_err}")
                            
                    api_depots = steam_client_data.get("depots", {}) if steam_client_data else {}
                except Exception as e:
                    logger.error(f"Failed to fetch depot info from Steam API for {app_id}: {e}")
                    api_depots = {}

                if not api_depots:
                    # If we couldn't get API data, we cannot verify if there was an update.
                    # In this case, to be safe and avoid unnecessary downloads, use the cached manifest.
                    logger.info(f"Could not check Steam API for updates. Reusing cached manifest for {app_id}.")
                    return str(cached_path), None

                # 3. Compare local manifests with API manifests
                is_up_to_date = True
                for depot_id, local_manifest_id in local_manifests.items():
                    if depot_id in api_depots:
                        current_manifest_id = api_depots[depot_id].get("manifest_id")
                        if current_manifest_id and local_manifest_id != current_manifest_id:
                            logger.info(
                                f"Update detected for depot {depot_id} of app {app_id}: "
                                f"cached={local_manifest_id}, current={current_manifest_id}"
                            )
                            is_up_to_date = False
                            break

                if is_up_to_date:
                    logger.info(f"Cached manifest for {app_id} is up-to-date. Using cache.")
                    return str(cached_path), None
                else:
                    logger.info(f"Cached manifest for {app_id} is stale. Redownloading.")
            else:
                logger.info(f"No cached manifest found for {app_id}. Downloading.")

        except Exception as e:
            logger.error(f"Error checking manifest cache for {app_id}: {e}", exc_info=True)

        return morrenus_api.download_manifest(app_id)

    def on_item_double_clicked(self, item):
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
        self.status_label.setText(f"Checking for updates and fetching manifest for App ID {app_id}...")

        worker = self.task_runner.run(self.check_and_download_manifest, app_id)
        worker.finished.connect(self.on_download_finished)
        worker.error.connect(self.on_task_error)

    def on_download_finished(self, result):
        filepath, error_msg = result

        if error_msg:
            self._handle_error(error_msg)
            self._toggle_inputs(True)
            return

        logger.info(f"Manifest downloaded: {filepath}")
        
        metadata = {}
        if self.parent_window:
            try:
                from core.tasks.process_zip_task import ProcessZipTask
                zip_task = ProcessZipTask()
                parsed_data = zip_task.run(filepath)
                
                if parsed_data and parsed_data.get("depots"):
                    from ui.dialogs.depotselection import DepotSelectionDialog
                    from utils.settings import get_settings
                    settings = get_settings()
                    auto_skip = settings.value("auto_skip_single_choice", False, type=bool)
                    depots = parsed_data.get("depots")
                    
                    selected_depots = None
                    if auto_skip and len(depots) == 1:
                        reply = QMessageBox.question(
                            self,
                            "Single Depot Option",
                            "Game has only one depot.\n\nProceed to download and add it to queue?",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.Yes,
                        )
                        if reply == QMessageBox.StandardButton.Yes:
                            selected_depots = list(depots.keys())
                        else:
                            logger.info("User cancelled single-depot download from search window.")
                            self._toggle_inputs(True)
                            self.status_label.setText("Download cancelled.")
                            return
                    else:
                        depot_dialog = DepotSelectionDialog(
                            parsed_data["appid"],
                            parsed_data.get("game_name", ""),
                            depots,
                            parsed_data.get("header_url"),
                            self.parent_window,
                        )
                        if depot_dialog.exec():
                            selected_depots = depot_dialog.get_selected_depots()
                    
                    if selected_depots:
                        metadata["selected_depots_list"] = selected_depots
                        metadata["game_name"] = parsed_data.get("game_name", "")
                    else:
                        # User cancelled depot selection
                        logger.info("User cancelled depot selection.")
                        self._toggle_inputs(True)
                        self.status_label.setText("Download cancelled.")
                        return
            except Exception as e:
                logger.warning(f"Failed to pre-parse zip for depot selection: {e}", exc_info=True)

        self.status_label.setText("Download complete! Adding to queue")

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
        QMessageBox.critical(self, "Error", message)
        self.status_label.setText("Error occurred")

    def on_task_error(self, error_info):
        _, error_value, _ = error_info
        self._handle_error(str(error_value))
        self._toggle_inputs(True)

    def _stop_active_image_fetchers(self):
        """Safely stops all running image fetchers."""
        for fetcher in list(self._active_image_fetchers.values()):
            try:
                fetcher.stop()
            except RuntimeError:
                pass
        self._active_image_fetchers.clear()

    def closeEvent(self, event):
        self._stop_active_image_fetchers()

        if self.task_runner:
            try:
                self.task_runner.stop()
            except RuntimeError as e:
                logger.debug(f"Error stopping task runner: {e}")

        super().closeEvent(event)
