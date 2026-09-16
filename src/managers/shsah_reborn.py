"""
SHSAH Reborn - Steam Achievement Manager for ASSella.
Handles reading and writing Steam UserGameStats binary files and schemas
across both Flatpak and Native Steam installations.
"""

import copy
import io
import logging
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import vdf

from core.steam_helpers import get_steam_env, get_most_recent_steam_id

logger = logging.getLogger(__name__)

STEAM_CDN_ICON_URL = "https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/apps/{appid}/{icon_hash}"


class AchievementItem:
    """Represents a single Steam achievement."""

    def __init__(
        self,
        api_name: str,
        display_name: str,
        description: str,
        stat_id: str,
        bit_id: int,
        hidden: bool = False,
        icon: Optional[str] = None,
        icon_gray: Optional[str] = None,
        unlocked: bool = False,
        unlock_time: int = 0,
    ):
        self.api_name = api_name
        self.display_name = display_name
        self.description = description
        self.stat_id = stat_id
        self.bit_id = bit_id
        self.hidden = hidden
        self.icon = icon
        self.icon_gray = icon_gray
        self.unlocked = unlocked
        self.unlock_time = unlock_time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "api_name": self.api_name,
            "display_name": self.display_name,
            "description": self.description,
            "stat_id": self.stat_id,
            "bit_id": self.bit_id,
            "hidden": self.hidden,
            "icon": self.icon,
            "icon_gray": self.icon_gray,
            "unlocked": self.unlocked,
            "unlock_time": self.unlock_time,
        }


class SHSAHRebornManager:
    """Manager for reading, modifying, and saving Steam game achievements."""

    def __init__(self, steam_path: Optional[Path] = None):
        self._custom_steam_path = steam_path
        self._icon_cache_dir = Path.home() / ".cache" / "assella" / "achievements"
        self._icon_cache_dir.mkdir(parents=True, exist_ok=True)
        self._download_lock = threading.Lock()
        self._active_downloads: set = set()

    def get_stats_dir(self) -> Optional[Path]:
        """Find the Steam appcache/stats directory for Flatpak or Native Steam."""
        if self._custom_steam_path:
            p = self._custom_steam_path / "appcache" / "stats"
            if p.is_dir():
                return p

        # Check via SteamEnv (handles Flatpak & Native detection)
        try:
            env = get_steam_env()
            if env.steam_path:
                candidate = Path(env.steam_path) / "appcache" / "stats"
                if candidate.is_dir():
                    return candidate
        except Exception as e:
            logger.debug(f"Error checking SteamEnv for stats dir: {e}")

        # Candidate fallback paths
        home = Path.home()
        candidates = [
            home / ".steam" / "steam" / "appcache" / "stats",
            home / ".local" / "share" / "Steam" / "appcache" / "stats",
            home / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam" / "appcache" / "stats",
            home / ".var" / "app" / "com.valvesoftware.Steam" / ".steam" / "steam" / "appcache" / "stats",
        ]
        for c in candidates:
            if c.is_dir():
                return c

        return None

    def get_account_id(self, appid: Optional[int | str] = None) -> Optional[int]:
        """Resolve current Steam Account ID (32-bit).

        1. Looks up most recent SteamID64 from loginusers.vdf.
        2. If appid given, checks for existing UserGameStats_<accountid>_<appid>.bin.
        """
        stats_dir = self.get_stats_dir()

        # If appid given and existing file is present in stats_dir, prioritize that account
        if appid and stats_dir and stats_dir.is_dir():
            pattern = f"UserGameStats_*_{appid}.bin"
            matches = list(stats_dir.glob(pattern))
            if matches:
                m = re.search(r"UserGameStats_(\d+)_", matches[0].name)
                if m:
                    acc_id = int(m.group(1))
                    logger.debug(f"Found account ID {acc_id} from existing stats file for appid {appid}")
                    return acc_id

        # Look up via get_most_recent_steam_id()
        try:
            sid64 = get_most_recent_steam_id()
            if sid64 and str(sid64).isdigit():
                return int(sid64) & 0xFFFFFFFF
        except Exception as e:
            logger.debug(f"Error fetching steam id from loginusers: {e}")

        # Scan any UserGameStats_*.bin in stats_dir to deduce active account
        if stats_dir and stats_dir.is_dir():
            all_stats = list(stats_dir.glob("UserGameStats_*.bin"))
            if all_stats:
                m = re.search(r"UserGameStats_(\d+)_", all_stats[0].name)
                if m:
                    return int(m.group(1))

        return None

    def load_schema(self, appid: int | str, language: str = "english") -> List[AchievementItem]:
        """Load and parse UserGameStatsSchema_<appid>.bin.

        Returns list of AchievementItem objects.
        """
        stats_dir = self.get_stats_dir()
        if not stats_dir:
            logger.warning("Stats directory not found")
            return []

        schema_file = stats_dir / f"UserGameStatsSchema_{appid}.bin"
        if not schema_file.exists():
            logger.info(f"Schema file not found: {schema_file}")
            return []

        try:
            with open(schema_file, "rb") as f:
                data = vdf.binary_load(f)
        except Exception as e:
            logger.error(f"Failed to parse schema file {schema_file}: {e}")
            return []

        app_info = data.get(str(appid), {})
        if not app_info and data:
            # Fallback: take first key
            app_info = list(data.values())[0]

        items: List[AchievementItem] = []
        stats = app_info.get("stats", {})

        for stat_id, stat_data in stats.items():
            stat_type = str(stat_data.get("type", ""))
            # Valve marks achievement blocks as type 4 / "ACHIEVEMENTS"
            if stat_type not in ("4", "ACHIEVEMENTS"):
                continue

            bits = stat_data.get("bits", {})
            for bit_id_str, bit_info in bits.items():
                try:
                    bit_id = int(bit_id_str)
                except ValueError:
                    continue

                disp = bit_info.get("display", {})
                name_obj = disp.get("name", {})
                if isinstance(name_obj, dict):
                    display_name = name_obj.get(language) or name_obj.get("english") or next(iter(name_obj.values()), "")
                else:
                    display_name = str(name_obj)

                desc_obj = disp.get("desc", {})
                if isinstance(desc_obj, dict):
                    description = desc_obj.get(language) or desc_obj.get("english") or next(iter(desc_obj.values()), "")
                else:
                    description = str(desc_obj) if desc_obj else ""

                api_name = bit_info.get("name", f"ach_{stat_id}_{bit_id}")
                hidden = str(disp.get("hidden", "0")) in ("1", "true", "True")
                icon = disp.get("icon")
                icon_gray = disp.get("icon_gray")

                items.append(
                    AchievementItem(
                        api_name=api_name,
                        display_name=display_name or api_name,
                        description=description,
                        stat_id=str(stat_id),
                        bit_id=bit_id,
                        hidden=hidden,
                        icon=icon,
                        icon_gray=icon_gray,
                    )
                )

        return items

    def load_user_stats(self, account_id: int | str, appid: int | str) -> Dict[str, Any]:
        """Load UserGameStats_<account_id>_<appid>.bin.

        If file does not exist, returns a new blank structure with crc: 0.
        """
        stats_dir = self.get_stats_dir()
        if not stats_dir:
            return {"cache": {"crc": 0, "PendingChanges": 0}}

        stats_file = stats_dir / f"UserGameStats_{account_id}_{appid}.bin"
        if not stats_file.exists():
            return {"cache": {"crc": 0, "PendingChanges": 0}}

        try:
            with open(stats_file, "rb") as f:
                return vdf.binary_load(f)
        except Exception as e:
            logger.error(f"Failed to parse user stats {stats_file}: {e}")
            return {"cache": {"crc": 0, "PendingChanges": 0}}

    def get_achievements_with_status(
        self,
        account_id: int | str,
        appid: int | str,
        language: str = "english",
    ) -> Tuple[List[AchievementItem], Dict[str, Any]]:
        """Return list of AchievementItem updated with user's unlocked status, plus stats_data dict."""
        achievements = self.load_schema(appid, language=language)
        user_stats = self.load_user_stats(account_id, appid)

        cache = user_stats.get("cache", {})

        for item in achievements:
            stat_block = cache.get(item.stat_id, {})
            data_val = stat_block.get("data", 0)
            is_unlocked = bool(data_val & (1 << item.bit_id))
            item.unlocked = is_unlocked

            times = stat_block.get("AchievementTimes", {})
            item.unlock_time = int(times.get(str(item.bit_id), 0))

        return achievements, user_stats

    def set_achievement_unlocked(
        self,
        stats_data: Dict[str, Any],
        stat_id: str,
        bit_id: int,
        unlocked: bool,
        timestamp: Optional[int] = None,
    ) -> None:
        """Update single achievement bit and timestamp in stats_data."""
        cache = stats_data.setdefault("cache", {})
        cache["crc"] = 0
        cache["PendingChanges"] = 0

        stat_block = cache.setdefault(str(stat_id), {})
        current_data = stat_block.get("data", 0)
        times = stat_block.setdefault("AchievementTimes", {})

        if unlocked:
            stat_block["data"] = current_data | (1 << bit_id)
            if timestamp is None:
                timestamp = int(time.time())
            times[str(bit_id)] = timestamp
        else:
            stat_block["data"] = current_data & ~(1 << bit_id)
            if str(bit_id) in times:
                del times[str(bit_id)]

    def apply_batch_states(
        self,
        stats_data: Dict[str, Any],
        achievements: List[AchievementItem],
        states: Dict[str, bool],
    ) -> None:
        """Apply a mapping of {api_name: bool} to stats_data and achievements."""
        now = int(time.time())
        for item in achievements:
            if item.api_name in states:
                new_state = states[item.api_name]
                item.unlocked = new_state
                self.set_achievement_unlocked(
                    stats_data,
                    stat_id=item.stat_id,
                    bit_id=item.bit_id,
                    unlocked=new_state,
                    timestamp=now if new_state else 0,
                )

    def save_user_stats(
        self,
        account_id: int | str,
        appid: int | str,
        stats_data: Dict[str, Any],
    ) -> Path:
        """Save binary VDF to UserGameStats_<account_id>_<appid>.bin."""
        stats_dir = self.get_stats_dir()
        if not stats_dir:
            raise RuntimeError("Steam stats directory not found")

        stats_dir.mkdir(parents=True, exist_ok=True)
        dest_file = stats_dir / f"UserGameStats_{account_id}_{appid}.bin"

        # Ensure CRC is 0 so Steam re-validates without rejection
        if "cache" in stats_data:
            stats_data["cache"]["crc"] = 0

        raw_bytes = vdf.binary_dumps(stats_data)
        temp_file = dest_file.with_suffix(".tmp")
        with open(temp_file, "wb") as f:
            f.write(raw_bytes)
        temp_file.replace(dest_file)
        logger.info(f"Saved stats file: {dest_file} ({len(raw_bytes)} bytes)")
        return dest_file

    def get_cached_icon_path(self, appid: int | str, icon_hash: Optional[str]) -> Optional[Path]:
        """Return local path to cached icon if present."""
        if not icon_hash:
            return None
        cached = self._icon_cache_dir / str(appid) / icon_hash
        if cached.is_file() and cached.stat().st_size > 0:
            return cached
        return None

    def download_icon_async(
        self,
        appid: int | str,
        icon_hash: Optional[str],
        on_complete: Optional[Callable[[Optional[Path]], None]] = None,
    ) -> None:
        """Download icon asynchronously from Steam CDN and call callback when ready."""
        if not icon_hash:
            if on_complete:
                on_complete(None)
            return

        app_cache_dir = self._icon_cache_dir / str(appid)
        app_cache_dir.mkdir(parents=True, exist_ok=True)
        dest = app_cache_dir / icon_hash

        if dest.is_file() and dest.stat().st_size > 0:
            if on_complete:
                on_complete(dest)
            return

        with self._download_lock:
            key = f"{appid}_{icon_hash}"
            if key in self._active_downloads:
                return
            self._active_downloads.add(key)

        def _fetch():
            try:
                url = STEAM_CDN_ICON_URL.format(appid=appid, icon_hash=icon_hash)
                req = urllib.request.Request(url, headers={"User-Agent": "Valve/SteamHttp"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        content = resp.read()
                        with open(dest, "wb") as out:
                            out.write(content)
                        if on_complete:
                            on_complete(dest)
                        return
            except Exception as e:
                logger.debug(f"Failed to download icon {icon_hash} for app {appid}: {e}")
            finally:
                with self._download_lock:
                    self._active_downloads.discard(key)

            if on_complete:
                on_complete(None)

        threading.Thread(target=_fetch, daemon=True).start()
