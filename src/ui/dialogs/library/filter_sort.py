import logging
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QListWidgetItem

from ui.dialogs.library.widgets import GameItemWidget, format_size

try:
    from managers.db_manager import DatabaseManager
except ImportError:
    DatabaseManager = None

logger = logging.getLogger(__name__)


class LibraryFilterSortMixin:
    """Mixin for game list sorting, searching, populating, and badge caching."""

    _current_pinned_cache = None

    @classmethod
    def _get_sort_key(cls, game: dict, sort_option: str):
        """Helper for sorting keys."""
        if sort_option in ("name_asc", "name_desc"):
            return str(game.get("game_name", "")).lower()
        if sort_option in ("size_asc", "size_desc"):
            try:
                return int(game.get("size_on_disk") or 0)
            except (ValueError, TypeError):
                return 0
        if sort_option == "appid":
            try:
                return int(game.get("appid", 0))
            except (ValueError, TypeError):
                return 0
        if sort_option == "recently_installed":
            lu = game.get("last_updated")
            if lu:
                try:
                    val = float(lu)
                    if val > 0:
                        return val
                except (ValueError, TypeError):
                    pass
            path = (
                game.get("accela_marker_path")
                or game.get("depot_downloader_path")
                or game.get("appmanifest_path")
                or game.get("install_path", "")
            )
            if path and os.path.exists(path):
                try:
                    return float(os.path.getmtime(path))
                except (OSError, TypeError):
                    pass
            return 0.0
        if sort_option == "pinned_first":
            appid = str(game.get("appid", "0"))
            pinned_cache = getattr(cls, "_current_pinned_cache", None)
            if pinned_cache is not None:
                is_pinned = appid in pinned_cache
            else:
                from utils.settings import get_settings
                is_pinned = get_settings().value(f"pin_build/{appid}", False, type=bool)
            return (0 if is_pinned else 1, str(game.get("game_name", "")).lower())
        if sort_option == "update_first":
            # Games with an update available sort first (0), then everything else (1)
            has_update = game.get("update_status") == "update_available"
            return (0 if has_update else 1, str(game.get("game_name", "")).lower())
        if sort_option == "dlc_only_first":
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc = is_dlc_only_mode(str(game.get("appid", "")))
            return (0 if is_dlc else 1, str(game.get("game_name", "")).lower())
        return str(game.get("game_name", "")).lower()

    def _sort_games(self, games: list) -> list:
        sort_option = self.sort_combo.currentData()
        reverse = sort_option in ("name_desc", "size_desc", "recently_installed")
        LibraryFilterSortMixin._current_pinned_cache = getattr(self, "_pinned_cache", {})
        try:
            return sorted(
                games,
                key=lambda g: self._get_sort_key(g, sort_option),
                reverse=reverse,
            )
        finally:
            LibraryFilterSortMixin._current_pinned_cache = None

    def _on_sort_changed(self) -> None:
        if self.settings:
            sort_option = self.sort_combo.currentData()
            self.settings.setValue("library_sort_option", sort_option)
        self._refresh_game_list()

    def _on_search_changed(self) -> None:
        self.search_timer.start(300)

    def _refresh_game_list(self) -> None:
        if self._closing:
            return

        self._refreshing = True

        # Cancel any active image fetches and clear the queue
        for fetcher in list(self._active_fetchers.values()):
            try:
                fetcher.stop()
            except Exception:
                pass
        self._active_fetchers.clear()
        self._image_fetch_queue.clear()
        self._pending_image_fetches.clear()
        self._current_fetches = 0

        self.games_list.clear()
        self._items_by_appid.clear()
        self._manifest_mtimes.clear()
        self._pinned_cache = {}

        # Quick in-memory scan of pinned games to avoid per-game disk/ACF latency
        if self.settings:
            try:
                for k in self.settings.allKeys():
                    if k.startswith("pin_build/"):
                        if self.settings.value(k, False, type=bool):
                            appid = k[len("pin_build/"):]
                            bid = self.settings.value(f"installed_buildid/{appid}", "", type=str)
                            self._pinned_cache[appid] = bid
            except Exception as e:
                logger.warning(f"Failed to populate pinned cache: {e}")

        # Pre-scan manifests directory for mtimes
        try:
            from utils.helpers import get_base_path
            manifests_dir = get_base_path() / "hubcap_manifests"
            if manifests_dir.exists():
                with os.scandir(manifests_dir) as entries:
                    for entry in entries:
                        if entry.is_file() and entry.name.startswith("accela_fetch_") and entry.name.endswith(".zip"):
                            parts = entry.name.split("_")
                            if len(parts) >= 3:
                                appid_part = parts[2].split(".")[0]
                                try:
                                    self._manifest_mtimes[appid_part] = entry.stat().st_mtime
                                except Exception:
                                    pass
        except Exception as e:
            logger.warning(f"Failed to pre-scan hubcap_manifests: {e}")

        from ui.dialogs.library.tab_bar import TAB_ACCELA, TAB_ATOM, TAB_STEAM

        active_tab = getattr(self, "_active_tab", TAB_ACCELA)
        all_manager_games = self.game_manager.get_all_games() if self.game_manager else []
        steam_games = getattr(self, "_steam_tab_games", [])

        # Collect all AppIDs managed by ACCELA or AT0-M so they don't appear in Steam tab
        managed_appids = set()
        for g in all_manager_games:
            aid = str(g.get("appid", "")).strip()
            if aid and aid not in ("0", "N/A", "unknown"):
                if g.get("is_accela_install") or g.get("is_atom") or g.get("is_vapor") or g.get("is_plugin_game"):
                    managed_appids.add(aid)
        try:
            from utils.plugin_games import load_plugin_library
            managed_appids.update(load_plugin_library().keys())
        except Exception:
            pass

        # Unmanaged Steam games: strictly exclude any game in ACCELA or AT0-M
        steam_unmanaged = [
            g
            for g in steam_games
            if str(g.get("appid", "")).strip() not in managed_appids
            and not g.get("is_accela_install")
            and not g.get("is_atom")
        ]

        # Update tab counts on the bar
        accela_total = sum(
            1
            for g in all_manager_games
            if g.get("is_accela_install") and not (g.get("is_atom") or g.get("is_vapor") or g.get("is_plugin_game"))
        )
        atom_total = sum(
            1
            for g in all_manager_games
            if g.get("is_atom") or g.get("is_vapor") or g.get("is_plugin_game")
        )
        steam_total = len(steam_unmanaged)

        if hasattr(self, "tab_bar"):
            self.tab_bar.update_counts(accela=accela_total, atom=atom_total, steam=steam_total)

        # Select game dataset based on active tab
        if active_tab == TAB_STEAM:
            for g in steam_unmanaged:
                g["is_steam_tab"] = True
            games = list(steam_unmanaged)
        elif active_tab == TAB_ATOM:
            games = [
                g
                for g in all_manager_games
                if g.get("is_atom") or g.get("is_vapor") or g.get("is_plugin_game")
            ]
        else:
            games = [
                g
                for g in all_manager_games
                if g.get("is_accela_install") and not (g.get("is_atom") or g.get("is_vapor") or g.get("is_plugin_game"))
            ]
        
        # Filter games by search term (case-insensitive)
        has_filter = False
        if hasattr(self, "search_input"):
            query = self.search_input.text().strip().lower()
            if query:
                games = [g for g in games if query in g.get("game_name", "").lower()]
                has_filter = True

        games = self._sort_games(games)
        
        # Limit displayed games count when filtering to prevent heavy UI layout lag
        truncated = False
        if has_filter and len(games) > 150:
            showing_games = games[:150]
            truncated = True
        else:
            showing_games = games

        # Optimize: freeze widget repaints during batch insertion
        self.games_list.setUpdatesEnabled(False)
        total_size = 0
        try:
            for game in showing_games:
                total_size += self._add_game_to_list(game)
        finally:
            self.games_list.setUpdatesEnabled(True)

        if active_tab == TAB_STEAM:
            tab_label = "Steam (Beta)"
        elif active_tab == TAB_ATOM:
            tab_label = "AT0-M"
        else:
            tab_label = "ACCELA"

        if active_tab == TAB_STEAM and getattr(self, "_steam_scan_in_progress", False) and len(games) == 0:
            self.info_label.setText("Scanning Steam libraries for installed games... Please wait.")
        elif truncated:
            self.info_label.setText(
                f"Showing top 150 of {len(games)} {tab_label} game(s) - "
                f"Please refine your search query."
            )
        else:
            self.info_label.setText(
                f"Found {len(games)} {tab_label} game(s) - "
                f"Total Size: {format_size(total_size)}"
            )
        self._refreshing = False

        # Defer image downloads; skip ratings badges entirely for Steam tab
        if active_tab != TAB_STEAM:
            QTimer.singleShot(100, self._start_pending_image_fetches)
            QTimer.singleShot(300, self._populate_ratings_badges)
        else:
            QTimer.singleShot(50, self._start_pending_image_fetches)

    def _add_game_to_list(self, game: dict) -> int:
        """Creates and adds a single game widget to the list. Returns size."""
        size = game.get("size_on_disk", 0)
        appid = str(game.get("appid", "0"))
        is_selected = appid in self._selected_appids
        widget = GameItemWidget(
            game,
            format_size(size),
            self.accent_color,
            self.background_color,
            select_mode=self._select_mode,
            is_selected=is_selected,
            applist_2_0_enabled=self.applist_2_0_enabled,
            parent_dialog=self,
        )
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, game)
        item.setSizeHint(widget.sizeHint())
        self.games_list.addItem(item)
        self.games_list.setItemWidget(item, widget)

        # Save to lookup index
        if appid not in ("0", "N/A", "unknown"):
            self._items_by_appid[appid] = item

        app_id = str(game.get("appid", "0"))
        if app_id in ("0", "N/A", "unknown"):
            self.executor.submit(self._resolve_and_update_item, item, game)
        else:
            self._pending_image_fetches.append((item, app_id))

        return size

    def _resolve_and_update_item(self, item: QListWidgetItem, game_data: dict) -> None:
        """Resolve AppID in a background thread and update the item."""
        name = game_data.get("game_name")
        resolved_appid = self._resolve_appid_by_name(name)
        if resolved_appid:
            game_data["appid"] = resolved_appid
            QTimer.singleShot(
                0, lambda: self._update_item_with_resolved_id(item, game_data)
            )

    def _populate_ratings_badges(self) -> None:
        if self._closing or getattr(self, "_active_tab", "") == "steam":
            return

        from core.ratings import prefetch_protondb_for_appids

        appids_to_prefetch = []
        count = self.games_list.count()
        for i in range(count):
            item = self.games_list.item(i)
            if not item:
                continue
            widget = self.games_list.itemWidget(item)
            if not isinstance(widget, GameItemWidget):
                continue
            # Paint Denuvo badge immediately (in-memory, instant)
            widget.update_denuvo_badge()
            # Paint ProtonDB badge if already cached (in-memory, instant);
            # if not cached, update_proton_badge() returns after hiding the badge.
            widget.update_proton_badge()
            # Collect appid for background prefetch
            appid = str(widget.game_data.get("appid", "0"))
            if appid and appid not in ("0", "N/A", "unknown"):
                appids_to_prefetch.append(appid)

        # Hand off uncached appids to the background worker queue.
        # The worker will process them sequentially with a 200ms sleep between
        # each request, then fire a debounced UI refresh when done.
        if appids_to_prefetch:
            prefetch_protondb_for_appids(appids_to_prefetch)

    @staticmethod
    def _resolve_appid_by_name(name: str) -> str | None:
        """Search the local database for an AppID by name."""
        if not name or not DatabaseManager:
            return None
        try:
            db = DatabaseManager()
            if not db.conn:
                return None

            cur = db.conn.cursor()
            cur.execute("SELECT appid FROM apps WHERE name = ? COLLATE NOCASE", (name,))
            row = cur.fetchone()
            if row:
                return str(row[0])
        except Exception as e:
            logger.debug(f"DB lookup failed for '{name}': {e}")
        return None

    def _update_item_with_resolved_id(
        self, item: QListWidgetItem, game_data: dict
    ) -> None:
        """Update the item on the main thread with the resolved AppID."""
        if self._closing:
            return
        item.setData(Qt.ItemDataRole.UserRole, game_data)
        appid = game_data.get("appid")
        if appid and appid not in ("0", "N/A", "unknown"):
            self._items_by_appid[appid] = item
        self._fetch_item_image(item, game_data["appid"])
