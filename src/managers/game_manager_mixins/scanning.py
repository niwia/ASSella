"""Steam library scanning, ACF parsing, and background size calculation mixin."""

import json
import logging
import os
import re
import shutil
import threading
from pathlib import Path

from PyQt6.QtCore import QMetaObject, Q_ARG, Qt

from core.steam_helpers import (
    find_steam_install,
    get_library_index,
    get_steam_libraries,
)
from managers.game_manager_mixins.updates import UPDATE_STATUS
from utils.helpers import get_base_path
from utils.settings import get_settings
from utils.task_runner import TaskRunner
from utils.update_status_cache import get_update_cache
from utils.wrapper_metadata import load_selected_dlcs, persist_selected_dlcs

logger = logging.getLogger(__name__)


class GameManagerScanningMixin:
    """Handles Steam library discovery, ACF metadata parsing, and async size calculation."""

    def scan_steam_libraries_async(self):
        """
        Start an async scan of Steam library directories for installed Steam games.
        The UI will update automatically when the scan completes via signals.
        """
        if self.is_scanning or (self.scan_runner is not None and self.scan_runner.is_running()):
            logger.info("Cancelling in-progress Steam library scan before starting a new scan")
            self.cancel_scan()

        logger.info("Starting async scan of Steam libraries for installed Steam games...")

        self._scan_cancelled = False
        self.is_scanning = True
        self._scan_generation = getattr(self, "_scan_generation", 0) + 1
        generation = self._scan_generation

        def do_scan():
            return self._perform_scan(generation=generation)

        if self.scan_runner is None:
            self.scan_runner = TaskRunner()
        self.scan_runner.run(do_scan)

    def cancel_scan(self):
        """Cancel any in-progress library scan and invalidate active size calculations."""
        self._scan_cancelled = True
        self._scan_generation = getattr(self, "_scan_generation", 0) + 1
        if hasattr(self, "_pending_size_calculations") and self._pending_size_calculations is not None:
            self._pending_size_calculations.clear()

        size_lock = getattr(self, "_size_calc_lock", None)
        if size_lock is not None:
            with size_lock:
                self._size_calc_generation = getattr(self, "_size_calc_generation", 0) + 1
                if hasattr(self, "_size_calc_queue"):
                    self._size_calc_queue.clear()

        if self.scan_runner is not None:
            try:
                self.scan_runner.stop(wait_ms=0, terminate_on_timeout=False)
            except Exception as e:
                logger.debug(f"Error stopping scan runner: {e}")
        self.is_scanning = False

    def _perform_scan(self, generation: int | None = None):
        """
        Internal method that performs the actual scan.
        Returns the number of games found.
        """
        if generation is None:
            generation = getattr(self, "_scan_generation", 0)

        steam_libraries = get_steam_libraries()

        if not steam_libraries:
            logger.warning("No Steam libraries found")
            if generation == getattr(self, "_scan_generation", generation):
                QMetaObject.invokeMethod(
                    self,
                    "_emit_scan_signals",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(int, 0),
                )
            return 0

        logger.info(f"Found {len(steam_libraries)} Steam library location(s)")

        games_found = 0
        scanned_libraries = 0

        # Cache the main Steam installation path to avoid repeated lookups
        steam_install_path = find_steam_install()

        # Build a GLOBAL ACF cache across ALL Steam libraries so that a game
        # installed in one library (e.g. external drive) whose ACF manifest sits
        # in another library (e.g. internal) can still be resolved correctly.
        global_acf_cache = {}
        for lib in steam_libraries:
            sp = os.path.join(lib, "steamapps")
            if os.path.exists(sp):
                partial = self._build_acf_cache(sp)
                for k, v in partial.items():
                    if k not in global_acf_cache:
                        global_acf_cache[k] = v
        logger.debug(
            f"Built global ACF cache with {len(global_acf_cache)} entries across {len(steam_libraries)} library(ies)"
        )

        scanned_games = []

        owned_steam_appids = self._get_owned_steam_appids()
        additional_apps = self._get_sls_additional_apps()

        for library_path in steam_libraries:
            if self._scan_cancelled or generation != getattr(self, "_scan_generation", generation):
                logger.info("Scan cancelled before scanning remaining libraries")
                break
            logger.info(f"Scanning library: {library_path}")
            scanned_libraries += 1

            games_found += self._scan_library(
                library_path,
                steam_install_path,
                scanned_games,
                global_acf_cache=global_acf_cache,
                owned_appids=owned_steam_appids,
                additional_apps=additional_apps,
            )

        if self._scan_cancelled or generation != getattr(self, "_scan_generation", generation):
            logger.info("Discarding cancelled or superseded library scan results")
            return games_found

        accela_games_found = sum(
            1 for game in scanned_games if game.get("is_accela_install")
        )
        logger.info(
            "Scan complete. Scanned %s library location(s), found %s installed Steam "
            "game(s) (%s ACCELA-managed).",
            scanned_libraries,
            games_found,
            accela_games_found,
        )

        self.games = self._get_sorted_games(scanned_games)
        self.filtered_games.clear()
        self._apply_filters()

        self._games_by_appid = {
            g["appid"]: g
            for g in self.games
            if g.get("appid") not in ("0", "N/A", "unknown", None)
        }
        logger.debug(f"Rebuilt _games_by_appid with {len(self._games_by_appid)} entries")

        if hasattr(self, "games_cache"):
            self.games_cache.save(self.games)

        self._fix_slssteam_config()
        self._sync_games_to_slssteam_config()
        self._sync_app_tokens_from_manifests()
        self._calculate_pending_sizes_async()

        QMetaObject.invokeMethod(
            self,
            "_emit_scan_signals",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, games_found),
        )

        return games_found

    def _calculate_pending_sizes_async(self):
        """
        Calculate disk sizes asynchronously for games lacking SizeOnDisk in ACF or cache.
        Guarded so repeated scans queue new paths into a single SizeCalculator worker rather
        than spawning overlapping directory walkers.
        """
        if not getattr(self, "_pending_size_calculations", None):
            return

        pending = list(self._pending_size_calculations)
        self._pending_size_calculations = []

        if not hasattr(self, "_size_calc_lock"):
            self._size_calc_lock = threading.Lock()
        if not hasattr(self, "_size_calc_queue"):
            self._size_calc_queue = []

        with self._size_calc_lock:
            existing_paths = {item[0] for item in self._size_calc_queue}
            for item in pending:
                if item[0] not in existing_paths:
                    self._size_calc_queue.append(item)
                    existing_paths.add(item[0])

            active_thread = getattr(self, "_size_calc_thread", None)
            if active_thread is not None and active_thread.is_alive():
                logger.debug(
                    "Queued %d path(s) onto active SizeCalculator thread",
                    len(pending),
                )
                return

            self._size_calc_generation = getattr(self, "_size_calc_generation", 0) + 1
            generation = self._size_calc_generation

            def worker():
                updated = False
                try:
                    while not self._scan_cancelled:
                        with self._size_calc_lock:
                            if generation != self._size_calc_generation or not self._size_calc_queue:
                                break
                            game_path, appid, game_name = self._size_calc_queue.pop(0)

                        try:
                            size = 0
                            for dirpath, _dirnames, filenames in os.walk(game_path):
                                if self._scan_cancelled or generation != self._size_calc_generation:
                                    break
                                for filename in filenames:
                                    if self._scan_cancelled or generation != self._size_calc_generation:
                                        break
                                    filepath = os.path.join(dirpath, filename)
                                    try:
                                        if os.path.isfile(filepath) or os.path.islink(filepath):
                                            size += os.lstat(filepath).st_size
                                    except OSError as e:
                                        logger.debug(f"  Skipped unreadable file {filepath}: {e}")

                            if (
                                size > 0
                                and not self._scan_cancelled
                                and generation == self._size_calc_generation
                            ):
                                if hasattr(self, "games_cache"):
                                    self.games_cache.set_cached_size(game_path, size, appid)
                                for g in list(self.games):
                                    if g.get("install_path") == game_path:
                                        g["size_on_disk"] = size
                                        updated = True
                                        break
                        except Exception as e:
                            logger.debug(f"Error calculating size for {game_name}: {e}")
                finally:
                    with self._size_calc_lock:
                        if getattr(self, "_size_calc_thread", None) is threading.current_thread():
                            self._size_calc_thread = None

                if updated and not self._scan_cancelled:
                    if hasattr(self, "games_cache"):
                        self.games_cache.save(self.games)
                    QMetaObject.invokeMethod(
                        self,
                        "_emit_library_updated",
                        Qt.ConnectionType.QueuedConnection,
                    )

            t = threading.Thread(target=worker, daemon=True, name="SizeCalculator")
            self._size_calc_thread = t
            t.start()

    def _scan_library(
        self,
        library_path,
        steam_install_path,
        scanned_games,
        global_acf_cache=None,
        owned_appids=None,
        additional_apps=None,
    ):
        """Scan a single Steam library for games."""
        games_found = 0
        steamapps_path = os.path.join(library_path, "steamapps")
        if not os.path.exists(steamapps_path):
            logger.warning(f"Steamapps directory not found at: {steamapps_path}")
            return 0

        common_path = os.path.join(steamapps_path, "common")
        if not os.path.exists(common_path):
            logger.warning(f"Common directory not found at: {common_path}")
            return 0

        acf_cache = (
            global_acf_cache
            if global_acf_cache is not None
            else self._build_acf_cache(steamapps_path)
        )
        logger.debug(f"Using ACF cache with {len(acf_cache)} entries for {library_path}")

        seen_paths = {game.get("install_path") for game in scanned_games}

        if additional_apps is None:
            additional_apps = self._get_sls_additional_apps()
        if owned_appids is None:
            owned_appids = self._get_owned_steam_appids()

        decryption_keys = {}
        try:
            from utils.yaml_config_manager import get_decryption_keys, get_user_config_path

            cfg_path = get_user_config_path()
            if cfg_path and cfg_path.exists():
                decryption_keys = dict(get_decryption_keys(cfg_path) or {})
        except Exception as e:
            logger.debug(f"Could not read DecryptionKeys during library scan: {e}")

        plugin_games_dict = {}
        dlc_to_base = {}
        try:
            from utils.plugin_games import build_dlc_reverse_map, get_all_plugin_games

            plugin_games_dict = get_all_plugin_games()
            dlc_to_base = build_dlc_reverse_map()
        except Exception as e:
            logger.warning(f"Plugin game list unavailable, plugin games are not scanned: {e}")

        try:
            with os.scandir(common_path) as entries:
                for entry in entries:
                    if self._scan_cancelled:
                        logger.info("Scan cancelled during library scan")
                        break
                    try:
                        if not entry.is_dir():
                            continue

                        game_name = entry.name
                        game_path = entry.path

                        if game_path in seen_paths:
                            continue

                        if not self._has_game_content(game_path):
                            logger.debug(f"  Skipped empty game folder: {game_name}")
                            continue

                        marker_path = self._get_accela_marker_path(game_path)
                        is_vapor = False
                        in_add_apps = False

                        if not marker_path:
                            acf_entry = acf_cache.get(game_name) or acf_cache.get(
                                game_name.lower()
                            )
                            if acf_entry:
                                appid_str = str(acf_entry[1])
                                is_atom_target = (
                                    appid_str in plugin_games_dict
                                    or appid_str in dlc_to_base.values()
                                )
                                in_add_apps = appid_str in additional_apps
                                if is_atom_target or in_add_apps:
                                    is_vapor = is_atom_target
                                elif appid_str in owned_appids:
                                    logger.debug(
                                        f"  Skipped owned Steam game: {game_name} ({appid_str})"
                                    )
                                    continue
                                else:
                                    logger.debug(f"  Skipped non-ACCELA game: {game_name}")
                                    continue
                            else:
                                logger.debug(f"  Skipped non-ACCELA game: {game_name}")
                                continue

                        game_data = self._collect_game_data(
                            game_path,
                            game_name,
                            library_path,
                            steam_install_path,
                            marker_path=marker_path,
                            acf_cache=acf_cache,
                            is_vapor=is_vapor,
                            in_additional_apps=in_add_apps,
                            owned_appids=owned_appids,
                            cfg_apps=additional_apps,
                            cfg_keys=decryption_keys,
                        )
                        if game_data:
                            scanned_games.append(game_data)
                            seen_paths.add(game_path)
                            games_found += 1
                            logger.debug(
                                "  Found %s game: %s",
                                game_data.get(
                                    "source",
                                    "Vapor"
                                    if is_vapor
                                    else ("ACCELA" if marker_path else "Steam"),
                                ),
                                game_data.get("game_name", game_name),
                            )
                    except OSError as e:
                        logger.debug(f"  Skipped unreadable entry in {common_path}: {e}")
                        continue

        except OSError as e:
            logger.error(f"Error scanning {common_path}: {e}")

        # Second pass: directly discover any game in SLSsteam AdditionalApps or plugin_library
        # whose appmanifest resides in this library, verifying the installdir exists and has content.
        all_target_apps = (
            set(additional_apps)
            | set(plugin_games_dict.keys())
            | set(dlc_to_base.values())
        )
        for appid_str in all_target_apps:
            if self._scan_cancelled:
                break
            acf_info = acf_cache.get(f"appid:{appid_str}")
            if not acf_info:
                continue
            manifest_path, installdir = acf_info
            if not manifest_path.startswith(steamapps_path):
                continue
            game_path = os.path.join(common_path, installdir)
            if game_path in seen_paths:
                continue
            if not os.path.isdir(game_path) or not self._has_game_content(game_path):
                continue

            is_atom_target = (
                appid_str in plugin_games_dict
                or appid_str in dlc_to_base.values()
            )
            game_data = self._collect_game_data(
                game_path,
                installdir,
                library_path,
                steam_install_path,
                marker_path=None,
                acf_cache=acf_cache,
                is_vapor=is_atom_target,
                in_additional_apps=(appid_str in additional_apps),
                owned_appids=owned_appids,
                cfg_apps=additional_apps,
                cfg_keys=decryption_keys,
            )
            if game_data:
                scanned_games.append(game_data)
                seen_paths.add(game_path)
                games_found += 1
                logger.debug(
                    f"  Found {game_data.get('source', 'managed')} game via manifest lookup: "
                    f"{game_data.get('game_name', installdir)} ({appid_str})"
                )

        return games_found

    @staticmethod
    def _build_acf_cache(steamapps_path):
        """
        Scan steamapps/ once and build a dict mapping installdir (lowercased) to
        (manifest_path, appid). Reduces total file reads from O(N×M) to O(M).
        """
        cache = {}
        try:
            with os.scandir(steamapps_path) as entries:
                for entry in entries:
                    manifest_path = entry.path
                    try:
                        if not (
                            entry.name.startswith("appmanifest_")
                            and entry.name.endswith(".acf")
                        ):
                            continue
                        appid = entry.name.replace("appmanifest_", "").replace(".acf", "")
                        with open(manifest_path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        match = re.search(r'"installdir"\s+"([^"]+)"', content)
                        if match:
                            installdir = match.group(1)
                            cache[installdir] = (manifest_path, appid)
                            cache[installdir.lower()] = (manifest_path, appid)
                            cache[f"appid:{appid}"] = (manifest_path, installdir)
                    except OSError as e:
                        logger.debug(f"  Skipped unreadable appmanifest {manifest_path}: {e}")
                        continue
        except OSError as e:
            logger.debug(f"Error building ACF cache for {steamapps_path}: {e}")
        return cache

    @staticmethod
    def _has_game_content(game_path):
        """
        Check if the game folder has actual content beyond ACCELA marker folders.
        Returns True if there are files or folders other than the marker folders.
        """
        try:
            ignore_names = {".accela", ".depotdownloader", "desktop.ini", "thumbs.db"}

            with os.scandir(game_path) as entries:
                for entry in entries:
                    try:
                        name = entry.name
                        lname = name.lower()

                        if lname in ignore_names or name.startswith("."):
                            continue

                        if entry.is_file() or entry.is_dir():
                            return True
                    except OSError as e:
                        logger.debug(f"  Skipped unreadable entry in {game_path}: {e}")
                        continue

            return False
        except OSError:
            return False

    @staticmethod
    def _get_accela_marker_path(game_path):
        """Return the ACCELA/ASSella marker folder/file path for a game, if present."""
        for marker_name in (
            ".assella",
            ".ASSELLA",
            ".ACCELA",
            ".DepotDownloader",
            ".accela",
            ".depotdownloader",
        ):
            marker_path = os.path.join(game_path, marker_name)
            if os.path.exists(marker_path):
                return marker_path
        return None

    def _collect_game_data(
        self,
        game_path,
        game_name,
        library_path,
        steam_path=None,
        marker_path=None,
        acf_cache=None,
        is_vapor=False,
        in_additional_apps=False,
        owned_appids=None,
        cfg_apps=None,
        cfg_keys=None,
    ):
        """
        Collect game data from installation directory.
        Returns a dictionary with game information.
        """
        try:
            if self._scan_cancelled:
                return None

            from utils.library_rules import (
                extract_depot_ids_from_file,
                has_decryption_key_for_game,
                is_placeholder_game_name,
                resolve_best_game_name,
            )

            appmanifest_path, appid = self._parse_acf_for_appid(
                library_path, game_name, acf_cache=acf_cache
            )

            marker_path = marker_path or self._get_accela_marker_path(game_path)

            try:
                settings = get_settings()
                experimental_mode = settings.value(
                    "experimental_acf_independent", False, type=bool
                )
            except Exception as e:
                logger.debug(f"Could not read experimental_acf_independent setting: {e}")
                experimental_mode = False

            meta_data = None
            if experimental_mode:
                try:
                    from utils.assella_metadata import load_accela_metadata

                    meta_data = load_accela_metadata(game_path)
                except Exception as e:
                    logger.debug(f"Failed to load ACCELA metadata fallback for {game_name}: {e}")

            if not appid and meta_data:
                appid = meta_data.get("appid")
                if appid:
                    logger.info(
                        f"Resolved AppID {appid} from ACCELA metadata fallback for '{game_name}'"
                    )

            if not appid:
                logger.warning(
                    f"FAILED to determine AppID for '{game_name}'. Game will have AppID='0' (unknown). "
                    "This may happen if the ACF file's installdir doesn't match the folder name exactly."
                )

            plugin_record = None
            if appid and appid not in ("0", "N/A", "unknown"):
                try:
                    from utils.plugin_games import get_plugin_game

                    plugin_record = get_plugin_game(appid, active_only=True)
                except Exception as e:
                    logger.debug(f"  Plugin library lookup failed for {appid}: {e}")

            is_plugin_game = bool(plugin_record)

            resolved_cfg_apps = {str(x).strip() for x in (cfg_apps or ())}
            resolved_cfg_keys = dict(cfg_keys or {})
            if cfg_apps is None or cfg_keys is None:
                try:
                    from utils.yaml_config_manager import (
                        get_additional_apps,
                        get_decryption_keys,
                        get_user_config_path,
                    )

                    cfg_path = get_user_config_path()
                    if cfg_path and cfg_path.exists():
                        if cfg_apps is None:
                            resolved_cfg_apps = {
                                str(x).strip()
                                for x in (get_additional_apps(cfg_path) or [])
                                if str(x).strip()
                            }
                        if cfg_keys is None:
                            resolved_cfg_keys = dict(get_decryption_keys(cfg_path) or {})
                except Exception as e:
                    logger.debug(f"[GameManager] Config load error for {appid}: {e}")

            valid_appid = bool(appid and appid not in ("0", "N/A", "unknown"))
            in_add = bool(
                in_additional_apps or (valid_appid and str(appid) in resolved_cfg_apps)
            )

            is_dlc_only = False
            if valid_appid:
                try:
                    from utils.dlc_helpers import is_dlc_only_mode

                    raw_dlc_only = bool(is_dlc_only_mode(appid))
                    is_unowned_physical_accela = (
                        bool(marker_path)
                        and owned_appids is not None
                        and str(appid) not in owned_appids
                        and not is_vapor
                        and not plugin_record
                    )
                    is_dlc_only = False if is_unowned_physical_accela else raw_dlc_only
                except Exception as e:
                    logger.debug(f"  DLC-only check failed for {appid}: {e}")

            if is_dlc_only:
                self._scrub_dlc_only_base_appid(appid)
                in_add = False

            depot_file = (
                Path(get_base_path()) / "depots" / f"{appid}.depot"
                if valid_appid
                else None
            )
            depot_file_ids = (
                extract_depot_ids_from_file(str(depot_file)) if depot_file else []
            )

            has_key_in_config = False
            if valid_appid and resolved_cfg_keys:
                known_depot_ids = list(depot_file_ids)
                if isinstance(plugin_record, dict):
                    p_deps = plugin_record.get("depots") or []
                    known_depot_ids.extend(
                        p_deps.keys() if isinstance(p_deps, dict) else p_deps
                    )
                if isinstance(meta_data, dict):
                    m_deps = meta_data.get("depots") or []
                    known_depot_ids.extend(
                        m_deps.keys() if isinstance(m_deps, dict) else m_deps
                    )
                has_key_in_config = has_decryption_key_for_game(
                    appid, resolved_cfg_keys, known_depot_ids
                )
                if not has_key_in_config and in_add:
                    try:
                        from managers.db_manager import DatabaseManager

                        c_info = (
                            DatabaseManager().get_app_info(appid, bypass_expiration=True)
                            or {}
                        )
                        c_deps = c_info.get("depots") or {}
                        if isinstance(c_deps, dict) and c_deps:
                            has_key_in_config = has_decryption_key_for_game(
                                appid, resolved_cfg_keys, c_deps.keys()
                            )
                    except Exception as e:
                        logger.debug(f"[GameManager] Depot DB lookup skipped for {appid}: {e}")

            if is_dlc_only:
                is_accela_install = False
            elif marker_path:
                is_accela_install = True
            else:
                if not is_plugin_game and in_add and has_key_in_config:
                    is_plugin_game = True
                is_accela_install = bool(in_add and not (is_vapor or is_plugin_game))

            if is_accela_install and is_plugin_game and plugin_record:
                try:
                    plugin_ts = float(plugin_record.get("updated_at") or 0)
                except (ValueError, TypeError):
                    plugin_ts = 0

                marker_ts = 0
                meta_json_path = os.path.join(
                    game_path, ".DepotDownloader", "metadata.json"
                )
                if os.path.exists(meta_json_path):
                    try:
                        with open(meta_json_path, "r", encoding="utf-8") as mf:
                            m_data = json.load(mf)
                            marker_ts = float(m_data.get("last_updated") or 0)
                    except (OSError, ValueError, TypeError) as e:
                        logger.debug(f"  Could not read {meta_json_path}: {e}")
                if marker_ts == 0 and marker_path and os.path.exists(marker_path):
                    try:
                        marker_ts = os.path.getmtime(marker_path)
                    except OSError as e:
                        logger.debug(f"  Could not stat marker {marker_path}: {e}")
                        marker_ts = 0

                if marker_ts >= plugin_ts:
                    logger.info(
                        f"[MethodResolution] Game '{game_name}' ({appid}) has newer ACCELA marker "
                        f"({marker_ts} >= {plugin_ts}); converting stale AT0-M record to ACCELA."
                    )
                    try:
                        from utils.plugin_games import convert_plugin_game_to_accela

                        convert_plugin_game_to_accela(appid)
                    except Exception as e:
                        logger.debug(
                            f"Failed to auto-clean stale plugin record for {appid}: {e}"
                        )
                    is_plugin_game = False
                    plugin_record = None
                else:
                    logger.info(
                        f"[MethodResolution] Game '{game_name}' ({appid}) was installed via AT0-M more recently "
                        f"({plugin_ts} > {marker_ts}); removing legacy ACCELA marker."
                    )
                    try:
                        if os.path.isdir(marker_path):
                            shutil.rmtree(marker_path, ignore_errors=True)
                        else:
                            os.remove(marker_path)
                    except Exception as e:
                        logger.debug(f"Failed to clean legacy marker for {appid}: {e}")
                    marker_path = None
                    is_accela_install = False

            is_managed = is_accela_install or is_vapor or is_plugin_game
            install_dir = game_name

            game_data = {
                "appid": appid or "0",
                "game_name": game_name,
                "install_dir": install_dir,
                "install_path": game_path,
                "library_path": library_path,
                "library_index": get_library_index(library_path, steam_path),
                "size_on_disk": 0,
                "source": (
                    "at0-m"
                    if ((is_vapor or is_plugin_game) and not is_accela_install)
                    else ("ACCELA" if is_accela_install else "Steam")
                ),
                "is_accela_install": is_managed,
                "is_vapor": (is_vapor or is_plugin_game) and not is_accela_install,
                "is_atom": (is_vapor or is_plugin_game) and not is_accela_install,
                "is_plugin_game": is_plugin_game and not is_accela_install,
                "plugin_record": plugin_record or {},
                "depot_downloader_path": marker_path or "",
                "accela_marker_path": marker_path or "",
                "appmanifest_path": appmanifest_path or "",
                "is_dlc_only": is_dlc_only,
            }

            is_unowned_marker_install = bool(
                marker_path
                and owned_appids is not None
                and valid_appid
                and str(appid) not in owned_appids
            )
            if (
                (is_accela_install or is_dlc_only)
                and valid_appid
                and not is_unowned_marker_install
            ):
                if depot_file and depot_file.exists() and depot_file_ids:
                    try:
                        from managers.db_manager import DatabaseManager
                        from utils.dlc_helpers import is_dlc_depot
                        from utils.yaml_config_manager import SHARED_REDISTS

                        valid_depots = [
                            did for did in depot_file_ids if did not in SHARED_REDISTS
                        ]
                        db = DatabaseManager()
                        app_info = db.get_app_info(appid, bypass_expiration=True)
                        if valid_depots and app_info and app_info.get("depots"):
                            depots_map = app_info.get("depots", {})
                            all_depots_dlc = True
                            for did in valid_depots:
                                d_meta = depots_map.get(did, {})
                                desc = str(
                                    d_meta.get("desc") or d_meta.get("name") or ""
                                )
                                if did in depots_map and not is_dlc_depot(
                                    did, desc=desc, base_appid=appid, depot_meta=d_meta
                                ):
                                    all_depots_dlc = False
                                    break
                            if all_depots_dlc:
                                main_depot_id = valid_depots[0]
                                game_data["is_dlc_only"] = True
                                game_data["is_accela_install"] = False
                                game_data["is_vapor"] = False
                                game_data["is_atom"] = False
                                game_data["source"] = "Steam"
                                self._scrub_dlc_only_base_appid(appid)
                                try:
                                    get_settings().setValue(f"dlc_only_mode/{appid}", True)
                                except Exception as e:
                                    logger.debug(
                                        f"  Could not persist dlc_only_mode for {appid}: {e}"
                                    )
                                if is_accela_install and not appmanifest_path:
                                    dlc_info = db.get_app_info(
                                        main_depot_id, bypass_expiration=True
                                    )
                                    if dlc_info and dlc_info.get("name"):
                                        game_data["game_name"] = f"{dlc_info['name']} [DLC]"
                                    else:
                                        game_data["game_name"] = f"{game_name} [DLC]"
                    except Exception as e:
                        logger.error(f"Error checking DLC-only status for {appid}: {e}")

            if is_accela_install:
                persisted_selected_dlcs = load_selected_dlcs(game_path)
                if persisted_selected_dlcs:
                    game_data["selected_dlcs"] = persisted_selected_dlcs
                    game_data["dlcs"] = {
                        dlc_id: "" for dlc_id in persisted_selected_dlcs
                    }
                elif os.name == "nt" and appid and appid not in ("0", "N/A", "unknown"):
                    inferred_selected_dlcs = (
                        self._infer_selected_dlcs_from_applist_and_manifest(appid)
                    )
                    if inferred_selected_dlcs:
                        game_data["selected_dlcs"] = inferred_selected_dlcs
                        game_data["dlcs"] = {
                            dlc_id: "" for dlc_id in inferred_selected_dlcs
                        }
                        if persist_selected_dlcs(game_path, inferred_selected_dlcs):
                            logger.debug(
                                f"Migrated and persisted {len(inferred_selected_dlcs)} DLC ID(s) for AppID {appid}"
                            )

            size_on_disk = 0
            acf_size_available = False

            if appmanifest_path and os.path.exists(appmanifest_path):
                acf_size_available = self._parse_acf_for_metadata(
                    appmanifest_path, game_data
                )
                if acf_size_available:
                    size_on_disk = game_data["size_on_disk"]

            if not acf_size_available and meta_data:
                if "game_name" in meta_data and (
                    not game_data.get("game_name")
                    or is_placeholder_game_name(game_data.get("game_name"), appid)
                ):
                    game_data["game_name"] = meta_data["game_name"]
                if meta_data.get("buildid"):
                    game_data["buildid"] = str(meta_data["buildid"]).strip()
                if "last_updated" in meta_data:
                    game_data["last_updated"] = meta_data["last_updated"]
                if meta_data.get("size_on_disk", 0) > 0:
                    size_on_disk = int(meta_data["size_on_disk"])
                    acf_size_available = True
                    game_data["size_on_disk"] = size_on_disk
                    logger.debug(
                        f"Using ACCELA metadata SizeOnDisk for {game_name}: {size_on_disk} bytes"
                    )

            if game_data.pop("_acf_placeholder_name", False) or is_placeholder_game_name(
                game_data.get("game_name"), appid
            ):
                plugin_name = (
                    plugin_record.get("name")
                    if isinstance(plugin_record, dict)
                    else None
                )
                meta_name = (
                    meta_data.get("game_name") if isinstance(meta_data, dict) else None
                )
                resolved_name = resolve_best_game_name(
                    appid,
                    (plugin_name, meta_name),
                    fallback_installdir=install_dir,
                )
                if resolved_name:
                    game_data["game_name"] = resolved_name
                    game_name = resolved_name

            if not acf_size_available and hasattr(self, "games_cache"):
                cached_size = self.games_cache.get_cached_size(game_path, appid)
                if cached_size and cached_size > 0:
                    size_on_disk = cached_size
                    acf_size_available = True
                    game_data["size_on_disk"] = size_on_disk
                    logger.debug(
                        f"Using cached SizeOnDisk for {game_name}: {size_on_disk} bytes"
                    )

            if not acf_size_available:
                if (
                    hasattr(self, "_pending_size_calculations")
                    and self._pending_size_calculations is not None
                ):
                    game_data["size_on_disk"] = 0
                    self._pending_size_calculations.append((game_path, appid, game_name))
                else:
                    logger.debug(
                        f"ACF SizeOnDisk not available, calculating size manually for {game_name}"
                    )
                    try:
                        for dirpath, _dirnames, filenames in os.walk(game_path):
                            if self._scan_cancelled:
                                return None
                            for filename in filenames:
                                if self._scan_cancelled:
                                    return None
                                filepath = os.path.join(dirpath, filename)
                                try:
                                    if os.path.isfile(filepath) or os.path.islink(
                                        filepath
                                    ):
                                        size_on_disk += os.lstat(filepath).st_size
                                except OSError as e:
                                    logger.debug(
                                        f"  Skipped unreadable file {filepath}: {e}"
                                    )
                    except OSError as e:
                        logger.debug(
                            f"  Size scan incomplete for {game_path}, size may be low: {e}"
                        )
                    game_data["size_on_disk"] = size_on_disk
            else:
                game_data["size_on_disk"] = size_on_disk

            if (is_vapor or is_plugin_game) and not is_accela_install:
                at0m_status = UPDATE_STATUS.get("AT0M", "at0m")
                if appid and appid not in ("0", "N/A", "unknown"):
                    try:
                        from managers.db_manager import DatabaseManager

                        stat = DatabaseManager().get_at0m_update_status(appid)
                        if stat and stat.get("status") in (
                            "update_available",
                            "key_pending",
                        ):
                            at0m_status = stat["status"]
                            game_data["at0m_update_details"] = stat
                    except Exception as e:
                        logger.debug(
                            f"  AT0-M update status lookup failed for {appid}: {e}"
                        )
                game_data["update_status"] = at0m_status
                game_data["installed_branch"] = "public"
                game_data["selected_branch"] = "public"
                if appid and appid not in ("0", "N/A", "unknown"):
                    try:
                        _s = get_settings()
                        _s.setValue(f"installed_branch/{appid}", "public")
                        _s.setValue(f"selected_branch/{appid}", "public")
                    except Exception as e:
                        logger.debug(
                            f"  Could not record the public branch for {appid}: {e}"
                        )
                return game_data

            if appid and appid not in ("0", "N/A", "unknown"):
                settings = get_settings()
                if settings.value(f"pin_build/{appid}", False, type=bool):
                    game_data["update_status"] = UPDATE_STATUS["UP_TO_DATE"]
                    return game_data

                cached_status = get_update_cache().get_status(appid)
                if cached_status is not None:
                    game_data["update_status"] = cached_status
                    logger.debug(
                        f"Restored cached update status for {game_name} ({appid}): {cached_status}"
                    )
                else:
                    raw_status = get_update_cache().get_raw_status(appid)
                    if raw_status in (
                        UPDATE_STATUS["UP_TO_DATE"],
                        UPDATE_STATUS["UPDATE_AVAILABLE"],
                    ):
                        game_data["update_status"] = raw_status
                    else:
                        game_data["update_status"] = UPDATE_STATUS["CANNOT_DETERMINE"]
            else:
                game_data["update_status"] = UPDATE_STATUS["CANNOT_DETERMINE"]

            return game_data

        except Exception as e:
            logger.error(
                f"Error collecting game data for {game_name}: {e}", exc_info=True
            )
            return None

    def _parse_acf_for_appid(self, library_path, game_name, acf_cache=None):
        """
        Find the AppID and manifest path for a given game install directory name.
        Uses O(1) lookup when acf_cache is provided, falling back to an O(M) scan.
        """
        if acf_cache is not None:
            result = acf_cache.get(game_name) or acf_cache.get(game_name.lower())
            if result:
                appmanifest_path, appid = result
                logger.debug(f"ACF cache hit for '{game_name}': AppID={appid}")
                return appmanifest_path, appid
            logger.debug(f"ACF cache miss for '{game_name}' — no matching manifest found")
            return None, None

        appmanifest_path = None
        appid = None
        steamapps_path = os.path.join(library_path, "steamapps")
        if os.path.exists(steamapps_path):
            logger.debug(f"Looking for ACF match for game (no cache): '{game_name}'")
            try:
                with os.scandir(steamapps_path) as entries:
                    for entry in entries:
                        if self._scan_cancelled:
                            return None, None
                        try:
                            if not (
                                entry.name.startswith("appmanifest_")
                                and entry.name.endswith(".acf")
                            ):
                                continue
                            test_manifest_path = entry.path
                            try:
                                with open(test_manifest_path, "r", encoding="utf-8") as f:
                                    content = f.read()
                                match = re.search(r'"installdir"\s+"([^"]+)"', content)
                                if match:
                                    installdir = match.group(1)
                                    if (
                                        installdir == game_name
                                        or installdir.lower() == game_name.lower()
                                    ):
                                        appmanifest_path = test_manifest_path
                                        appid = entry.name.replace(
                                            "appmanifest_", ""
                                        ).replace(".acf", "")
                                        logger.debug(f"  ✓ Match found! AppID: {appid}")
                                        break
                            except OSError as e:
                                logger.debug(
                                    f"  Skipped unreadable appmanifest {test_manifest_path}: {e}"
                                )
                                continue
                        except OSError as e:
                            logger.debug(
                                f"  Skipped unreadable entry while matching {game_name}: {e}"
                            )
                            continue
            except OSError as e:
                logger.debug(f"  Error scanning steamapps directory: {e}")

        return appmanifest_path, appid

    @staticmethod
    def _parse_acf_for_metadata(appmanifest_path, game_data):
        """Parse ACF file for metadata like name, buildid, and size."""
        acf_size_available = False
        try:
            with open(appmanifest_path, "r", encoding="utf-8") as f:
                content = f.read()

                name_match = re.search(r'"name"\s+"([^"]+)"', content)
                if name_match:
                    from utils.library_rules import (
                        is_placeholder_game_name,
                        resolve_best_game_name,
                    )

                    acf_name = name_match.group(1).strip()
                    aid_val = game_data.get("appid")
                    if is_placeholder_game_name(acf_name, aid_val):
                        game_data["_acf_placeholder_name"] = True
                        game_data["game_name"] = resolve_best_game_name(
                            aid_val,
                            (),
                            fallback_installdir=str(
                                game_data.get("install_dir")
                                or game_data.get("game_name")
                                or ""
                            ),
                        )
                    else:
                        game_data["game_name"] = acf_name

                buildid_match = re.search(r'"buildid"\s+"([^"]+)"', content)
                if buildid_match:
                    game_data["buildid"] = buildid_match.group(1)

                lastupdated_match = re.search(r'"LastUpdated"\s+"([^"]+)"', content)
                if lastupdated_match:
                    game_data["last_updated"] = lastupdated_match.group(1)

                betakey_match = re.search(
                    r'"betakey"\s+"([^"]+)"', content, re.IGNORECASE
                )
                if betakey_match:
                    betakey_val = betakey_match.group(1).strip()
                    if betakey_val:
                        game_data["installed_branch"] = betakey_val
                        try:
                            _s = get_settings()
                            _appid = str(game_data.get("appid") or "")
                            if _appid:
                                _s.setValue(f"installed_branch/{_appid}", betakey_val)
                        except Exception as e:
                            logger.debug(f"  Could not record the installed branch: {e}")

                sizeon_disk_match = re.search(r'"SizeOnDisk"\s+"([^"]+)"', content)
                if sizeon_disk_match:
                    acf_size = int(sizeon_disk_match.group(1))
                    if acf_size > 0:
                        game_data["size_on_disk"] = acf_size
                        acf_size_available = True
                        logger.debug(
                            f"Using ACF SizeOnDisk for {game_data['game_name']}: {acf_size} bytes"
                        )

                try:
                    import vdf

                    vdf_data = vdf.loads(content)
                    app_state = vdf_data.get("AppState", {})
                    installed_depots = app_state.get("InstalledDepots")
                    if isinstance(installed_depots, dict) and installed_depots:
                        game_data["installed_depots"] = installed_depots
                        if not game_data.get("depots"):
                            game_data["depots"] = {
                                str(k): (
                                    v if isinstance(v, dict) else {"manifest": str(v)}
                                )
                                for k, v in installed_depots.items()
                            }
                except Exception as ex_depots:
                    logger.debug(f"VDF depot parse fallback for {appmanifest_path}: {ex_depots}")
                    depot_matches = re.findall(
                        r'"(\d{4,9})"\s*\{[^}]*"manifest"\s*"(\d+)"', content
                    )
                    if depot_matches:
                        d_dict = {m[0]: {"manifest": m[1]} for m in depot_matches}
                        game_data["installed_depots"] = d_dict
                        if not game_data.get("depots"):
                            game_data["depots"] = d_dict
        except Exception as e:
            logger.debug(f"Could not parse ACF file {appmanifest_path}: {e}")

        return acf_size_available