"""
at0m_update_checker.py
──────────────────────
Intelligent, resource-efficient background update checker for AT0-M and DLC-only games.

Features:
- Discovers AT0-M / DLC-only games across config.yaml, atom_games.json, and GameManager.
- Fast multi-tier evaluation:
  1. Local Cache / TTL / Cooldown check (instant, zero network).
  2. Steam PICS / SteamCMD timeupdated diff (skip games if Steam hasn't pushed updates).
  3. Depot Delta calculation: compares Steam's current depots vs. locally known depots/keys.
  4. Hubcap Preflight check via free /manifest/{appid}/contents endpoint (zero quota cost).
  5. 6h cooldown on missing keys to protect Hubcap API.
  6. Non-blocking background worker with Qt signals.
"""

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QObject, pyqtSignal

from managers.db_manager import DatabaseManager
from managers.depot_key_manager import DepotKeyManager
from utils.helpers import get_base_path
from utils.paths import Paths
from utils.plugin_games import SHARED_REDISTS, load_plugin_library
from utils.settings import get_settings
from utils.sls_bridge import SLSBridge
from utils.yaml_config_manager import (
    batch_config_edit,
    get_additional_apps,
    get_additional_depots,
    get_decryption_keys,
    get_user_config_path,
)

try:
    from ui.assets import DEPOT_BLACKLIST
except ImportError:
    DEPOT_BLACKLIST = set()

logger = logging.getLogger(__name__)

ALL_SHARED_REDISTS: Set[str] = SHARED_REDISTS | {str(d) for d in DEPOT_BLACKLIST}

# Cache TTL for "up_to_date" entries (24 hours)
UP_TO_DATE_TTL_SECONDS = 24 * 3600
# Cooldown for "key_pending" entries before re-checking Hubcap (6 hours)
KEY_PENDING_COOLDOWN_SECONDS = 6 * 3600


class At0mUpdateChecker(QObject):
    """Background checker for AT0-M and DLC-only depot/key updates."""

    # Signals
    # appid, status ('up_to_date' | 'update_available' | 'key_pending'), details dict
    game_checked = pyqtSignal(str, str, dict)
    progress = pyqtSignal(int, int)  # current, total
    all_checked = pyqtSignal()

    _instance: Optional["At0mUpdateChecker"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "At0mUpdateChecker":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.db = DatabaseManager()
        self._is_checking = False
        self._cancel_requested = False

    @property
    def is_checking(self) -> bool:
        return self._is_checking

    def cancel(self) -> None:
        """Cancel an in-progress scan."""
        self._cancel_requested = True

    # ── Discovery ────────────────────────────────────────────────────────────

    def get_all_at0m_games(self) -> Dict[str, Dict[str, Any]]:
        """
        Discovers all AT0-M and DLC-mode games across:
        1. SLSsteam config.yaml (AdditionalApps)
        2. plugins/atom_games.json (plugin library)
        3. Local cached Luas (cached_luas/)
        4. DLC-only mode settings

        Returns dict: {appid: {"name": str, "source": str, "is_dlc_only": bool}}
        """
        discovered: Dict[str, Dict[str, Any]] = {}

        # 1. SLSsteam config.yaml AdditionalApps
        cfg_path = get_user_config_path()
        if cfg_path and cfg_path.exists():
            try:
                content = cfg_path.read_text(encoding="utf-8", errors="ignore")
                for line in content.splitlines():
                    # Parse line like: "  - 1091500 # Cyberpunk 2077"
                    m = re.match(r"^[ \t]*-[ \t]*(\d+)[ \t]*(?:#[ \t]*(.*))?$", line)
                    if m:
                        aid, comment = m.group(1), (m.group(2) or "").strip()
                        # Exclude shared redists or obvious non-game entries
                        if aid not in ALL_SHARED_REDISTS and aid != "0":
                            name = comment or f"App {aid}"
                            # Strip tags like [DLC]
                            is_dlc = "[dlc]" in name.lower()
                            clean_name = re.sub(r"^\[DLC\]\s*", "", name, flags=re.IGNORECASE)
                            discovered[aid] = {
                                "appid": aid,
                                "name": clean_name,
                                "source": "config.yaml",
                                "is_dlc_only": is_dlc,
                            }
            except Exception as e:
                logger.debug(f"[At0mChecker] Error reading config.yaml: {e}")

        # 2. plugins/atom_games.json
        try:
            plugin_lib = load_plugin_library()
            for aid_str, prec in plugin_lib.items():
                if aid_str and aid_str.isdigit() and aid_str not in ALL_SHARED_REDISTS:
                    name = prec.get("name") or prec.get("game_name") or f"App {aid_str}"
                    discovered.setdefault(aid_str, {
                        "appid": aid_str,
                        "name": name,
                        "source": "plugin_library",
                        "is_dlc_only": False,
                    })
        except Exception as e:
            logger.debug(f"[At0mChecker] Error reading plugin library: {e}")

        # 3. Local cached_luas directory
        try:
            luas_dir = Path(get_base_path()) / "cached_luas"
            if luas_dir.exists():
                for f in luas_dir.glob("*.lua"):
                    aid = f.stem
                    if aid.isdigit() and aid not in ALL_SHARED_REDISTS:
                        discovered.setdefault(aid, {
                            "appid": aid,
                            "name": f"App {aid}",
                            "source": "cached_lua",
                            "is_dlc_only": False,
                        })
        except Exception as e:
            logger.debug(f"[At0mChecker] Error scanning cached_luas: {e}")

        # 4. Check DLC-only flags in settings
        settings = get_settings()
        for aid in list(discovered.keys()):
            if settings.value(f"dlc_only_mode/{aid}", False, type=bool):
                discovered[aid]["is_dlc_only"] = True

        return discovered

    def get_locally_known_depots(self, appid: str) -> Tuple[Set[str], Dict[str, str]]:
        """
        Gathers all currently known depot IDs and keys for this AppID locally.
        Zero network calls.
        Returns: (known_depot_ids, known_keys_dict)
        """
        appid_str = str(appid)
        known_depots: Set[str] = set()
        known_keys: Dict[str, str] = {}

        # A. keys.db (DepotKeyManager)
        try:
            db_keys = DepotKeyManager.get_instance().get_all_keys(appid_str)
            for d, k in db_keys.items():
                d_str = str(d)
                known_depots.add(d_str)
                if k and len(str(k)) == 64:
                    known_keys[d_str] = str(k).lower()
        except Exception as e:
            logger.debug(f"[At0mChecker] Error reading DepotKeyManager for {appid_str}: {e}")

        # B. cached_luas/{appid}.lua
        try:
            lua_file = Path(get_base_path()) / "cached_luas" / f"{appid_str}.lua"
            if lua_file.exists():
                lua_text = lua_file.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)', lua_text):
                    did, key = m.group(1), m.group(2).lower()
                    known_depots.add(did)
                    known_keys[did] = key
                for m in re.finditer(r'addappid\((\d+)', lua_text):
                    known_depots.add(m.group(1))
        except Exception as e:
            logger.debug(f"[At0mChecker] Error reading cached lua for {appid_str}: {e}")

        # C. SLSsteam config.yaml DecryptionKeys & AdditionalDepots
        try:
            cfg_path = get_user_config_path()
            if cfg_path and cfg_path.exists():
                cfg_keys = get_decryption_keys(cfg_path)
                for d, k in cfg_keys.items():
                    d_str = str(d)
                    known_depots.add(d_str)
                    if k and len(str(k)) == 64:
                        known_keys[d_str] = str(k).lower()

                cfg_depots = get_additional_depots(cfg_path)
                for d in cfg_depots:
                    known_depots.add(str(d))
        except Exception as e:
            logger.debug(f"[At0mChecker] Error reading config.yaml depots/keys: {e}")

        # D. plugins/atom_games.json
        try:
            plugin_lib = load_plugin_library()
            prec = plugin_lib.get(appid_str, {})
            p_depots = prec.get("depots", {})
            for d, dinfo in p_depots.items():
                d_str = str(d)
                known_depots.add(d_str)
                if isinstance(dinfo, dict):
                    k = dinfo.get("key") or dinfo.get("decryption_key")
                    if k and len(str(k)) == 64:
                        known_keys[d_str] = str(k).lower()
        except Exception as e:
            logger.debug(f"[At0mChecker] Error reading plugin_games for {appid_str}: {e}")

        # Remove shared redists
        known_depots.difference_update(ALL_SHARED_REDISTS)
        for r in ALL_SHARED_REDISTS:
            known_keys.pop(r, None)

        return known_depots, known_keys

    # ── Single Game Verification ─────────────────────────────────────────────

    def check_game(
        self,
        appid: str,
        game_title: str = "",
        force_refresh: bool = False,
    ) -> Tuple[str, dict]:
        """
        Evaluates update / key status for a single AT0-M game.

        Returns: (status, details)
        status can be:
          - 'up_to_date': Game is up to date, no missing keys.
          - 'update_available': Steam has new depots AND Hubcap has the keys ready!
          - 'key_pending': Steam has new depots, but Hubcap does not have keys yet.
        """
        appid_str = str(appid)
        now = int(time.time())

        # Step 1: Check SQLite Cache (at0m_update_status)
        cached = self.db.get_at0m_update_status(appid_str)
        if cached and not force_refresh:
            c_status = cached.get("status", "up_to_date")
            c_checked = cached.get("last_checked", 0)
            c_cooldown = cached.get("cooldown_until", 0)

            # A. If keys were pending and cooldown is still active, honor cooldown
            if c_status == "key_pending" and now < c_cooldown:
                logger.debug(f"[At0mChecker] {appid_str} key_pending in cooldown ({c_cooldown - now}s left)")
                return "key_pending", cached

            # B. If up_to_date and checked recently, skip
            if c_status == "up_to_date" and (now - c_checked) < UP_TO_DATE_TTL_SECONDS:
                logger.debug(f"[At0mChecker] {appid_str} up_to_date cached (fresh)")
                return "up_to_date", cached

        # Step 2: Fetch Steam app metadata (Steam PICS / SteamCMD)
        from core.steam_api import fetch_steamcmd_info
        steam_info = fetch_steamcmd_info(appid_str, include_header=False)
        if not steam_info or not steam_info.get("depots"):
            # Fallback to DB app info if remote fetch failed
            steam_info = self.db.get_app_info(appid_str)

        if not steam_info or not steam_info.get("depots"):
            logger.debug(f"[At0mChecker] Could not fetch Steam depots for {appid_str}")
            return "up_to_date", (cached or {})

        time_updated = int(steam_info.get("timeupdated") or steam_info.get("buildid") or 0)
        app_name = steam_info.get("name") or game_title or (cached.get("game_title") if cached else "") or f"App {appid_str}"

        # Step 3: Steam timeupdated diff check
        if cached and not force_refresh:
            last_tu = cached.get("last_timeupdated", 0)
            if last_tu > 0 and time_updated > 0 and time_updated == last_tu:
                # Steam timestamp hasn't changed at all! Zero further work.
                self.db.save_at0m_update_status(
                    appid=appid_str,
                    game_title=app_name,
                    last_timeupdated=time_updated,
                    status="up_to_date",
                    new_depots=[],
                )
                logger.debug(f"[At0mChecker] {appid_str} timeupdated unchanged ({time_updated}), skipping.")
                return "up_to_date", {"appid": appid_str, "status": "up_to_date"}

        # Step 4: Depot Delta calculation
        steam_depots: Set[str] = set()
        depots_dict = steam_info.get("depots") or {}
        for did, ddata in depots_dict.items():
            did_str = str(did)
            if not did_str.isdigit() or did_str in ALL_SHARED_REDISTS:
                continue
            # Keep meaningful content depots
            if isinstance(ddata, dict):
                # If depot has manifest or size, it is a content depot
                if ddata.get("manifest_id") or ddata.get("manifests") or ddata.get("size") or ddata.get("is_dlc"):
                    steam_depots.add(did_str)
            else:
                steam_depots.add(did_str)

        known_depots, known_keys = self.get_locally_known_depots(appid_str)
        missing_depots = steam_depots - known_depots

        # If no missing depots, game is fully up to date!
        if not missing_depots:
            self.db.save_at0m_update_status(
                appid=appid_str,
                game_title=app_name,
                last_timeupdated=time_updated,
                status="up_to_date",
                new_depots=[],
            )
            return "up_to_date", {"appid": appid_str, "status": "up_to_date"}

        # Step 5: Check Hubcap Preflight for missing depots
        logger.info(f"[At0mChecker] App {appid_str} ({app_name}) has {len(missing_depots)} missing depot(s): {missing_depots}")
        from core import morrenus_api
        hubcap_contents = morrenus_api.get_manifest_contents(appid_str)

        hubcap_depots = set()
        if isinstance(hubcap_contents, dict) and not hubcap_contents.get("error"):
            hubcap_depots = set(hubcap_contents.get("depot_ids") or [])

        # Check if Hubcap has keys/manifests for any of the missing depots
        ready_depots = missing_depots.intersection(hubcap_depots)

        if ready_depots:
            # Hubcap has the missing depots ready!
            status = "update_available"
            depots_info = []
            for did in ready_depots:
                dname = depots_dict.get(did, {}).get("name") if isinstance(depots_dict.get(did), dict) else ""
                depots_info.append({"id": did, "name": dname or f"Depot {did}", "has_key": True})

            self.db.save_at0m_update_status(
                appid=appid_str,
                game_title=app_name,
                last_timeupdated=time_updated,
                status=status,
                new_depots=depots_info,
                cooldown_until=0,
            )
            details = {
                "appid": appid_str,
                "game_title": app_name,
                "status": status,
                "new_depots": depots_info,
            }
            logger.info(f"[At0mChecker] Update Available for {appid_str}: {len(depots_info)} depots ready on Hubcap.")
            return status, details
        else:
            # Steam has new depots, but Hubcap does not have keys yet -> key_pending
            status = "key_pending"
            cooldown_until = now + KEY_PENDING_COOLDOWN_SECONDS
            depots_info = []
            for did in missing_depots:
                dname = depots_dict.get(did, {}).get("name") if isinstance(depots_dict.get(did), dict) else ""
                depots_info.append({"id": did, "name": dname or f"Depot {did}", "has_key": False})

            self.db.save_at0m_update_status(
                appid=appid_str,
                game_title=app_name,
                last_timeupdated=time_updated,
                status=status,
                new_depots=depots_info,
                cooldown_until=cooldown_until,
            )
            details = {
                "appid": appid_str,
                "game_title": app_name,
                "status": status,
                "new_depots": depots_info,
                "cooldown_until": cooldown_until,
            }
            logger.info(f"[At0mChecker] Keys Pending for {appid_str}: {len(missing_depots)} depots pending on Hubcap (cooldown 6h).")
            return status, details

    # ── Background Scan Runner ───────────────────────────────────────────────

    def check_all_games_async(self, force_refresh: bool = False) -> None:
        """Runs the update check asynchronously on a background thread."""
        if self._is_checking:
            logger.info("[At0mChecker] Scan already running.")
            return

        self._is_checking = True
        self._cancel_requested = False

        def _worker():
            try:
                all_games = self.get_all_at0m_games()
                total = len(all_games)
                logger.info(f"[At0mChecker] Starting check for {total} AT0-M / DLC game(s)...")

                for idx, (aid, ginfo) in enumerate(all_games.items()):
                    if self._cancel_requested:
                        logger.info("[At0mChecker] Scan cancelled.")
                        break

                    name = ginfo.get("name", "")
                    try:
                        status, details = self.check_game(aid, game_title=name, force_refresh=force_refresh)
                        self.game_checked.emit(aid, status, details)
                    except Exception as e:
                        logger.error(f"[At0mChecker] Error checking game {aid}: {e}")

                    self.progress.emit(idx + 1, total)

                self.all_checked.emit()
            finally:
                self._is_checking = False
                self._cancel_requested = False

        threading.Thread(target=_worker, daemon=True, name="At0mUpdateCheckerThread").start()

    # ── Apply / Sync Action ──────────────────────────────────────────────────

    def apply_update(self, appid: str) -> Tuple[bool, str]:
        """
        Applies new keys/depots for an AT0-M game by reusing the proven refetch + sync flow:
        1. Downloads fresh manifest ZIP and Lua from Hubcap.
        2. Extracts decryption keys into keys.db.
        3. Updates AdditionalDepots and DecryptionKeys in config.yaml.
        4. Notifies SLSsteam file watcher.
        5. Updates SQLite status back to 'up_to_date'.
        """
        appid_str = str(appid)
        logger.info(f"[At0mChecker] Applying update for {appid_str}...")

        try:
            from core import morrenus_api
            zip_path, err = morrenus_api.download_manifest(appid_str, force_update=True)
            if not zip_path or not os.path.exists(zip_path):
                return False, f"Failed to download manifest from Hubcap: {err or 'Unknown error'}"

            import zipfile
            fresh_keys: Dict[str, str] = {}
            with zipfile.ZipFile(zip_path, "r") as zf:
                lua_files = [f for f in zf.namelist() if f.endswith(".lua")]
                if lua_files:
                    dest_lua = Path(get_base_path()) / "cached_luas" / f"{appid_str}.lua"
                    dest_lua.parent.mkdir(parents=True, exist_ok=True)
                    lua_data = zf.read(lua_files[0]).decode("utf-8", errors="ignore")
                    dest_lua.write_text(lua_data, encoding="utf-8")

                    for m in re.finditer(r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)', lua_data):
                        fresh_keys[m.group(1)] = m.group(2).lower()

                    if fresh_keys:
                        DepotKeyManager.get_instance().save_depot_keys(appid_str, fresh_keys)

            # Sync new keys and depots to config.yaml
            cfg_path = get_user_config_path()
            if cfg_path and cfg_path.exists():
                cached_status = self.db.get_at0m_update_status(appid_str)
                game_title = (cached_status.get("game_title") if cached_status else "") or f"App {appid_str}"

                with batch_config_edit(cfg_path) as editor:
                    for did, k in fresh_keys.items():
                        if did not in ALL_SHARED_REDISTS:
                            editor.add_depot(did, comment=f"{game_title} ({did})")
                            editor.add_key(did, k, comment=f"{game_title} ({did})")

                if editor.has_changes:
                    SLSBridge.notify_reload()

            # Mark status back to up_to_date
            self.db.save_at0m_update_status(
                appid=appid_str,
                status="up_to_date",
                new_depots=[],
                cooldown_until=0,
            )

            return True, "Successfully updated keys and configuration."
        except Exception as e:
            logger.error(f"[At0mChecker] Error applying update for {appid_str}: {e}")
            return False, str(e)
