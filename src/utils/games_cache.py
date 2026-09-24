"""
games_cache.py
──────────────
Persistent, disk-backed cache for scanned games and calculated sizes.

Design goals:
 • Instant startup: On app boot, games are loaded from disk in <10ms and
   displayed immediately without waiting for the asynchronous library scan.
 • Non-blocking size cache: Saves calculated sizes on disk so slow storage (SD cards,
   HDDs) never blocks repeated library scans with synchronous os.walk traversals.
 • Thread-safe: Atomic write using temp file rename + threading.Lock.
"""

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_games_cache_path() -> Path:
    from utils.helpers import get_data_file_path
    return get_data_file_path("games_cache.json")


class GamesCache:
    """
    Disk-backed cache for game library entries and folder sizes.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._games: List[dict] = []
        self._size_by_path: Dict[str, int] = {}
        self._size_by_appid: Dict[str, int] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        cache_path = _get_games_cache_path()
        if not cache_path.exists():
            return

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                raw_games = data.get("games", [])
            elif isinstance(data, list):
                raw_games = data
            else:
                raw_games = []

            with self._lock:
                self._games = raw_games
                for g in raw_games:
                    size = g.get("size_on_disk", 0)
                    install_path = g.get("install_path")
                    appid = str(g.get("appid", ""))
                    if size and size > 0:
                        if install_path:
                            self._size_by_path[install_path] = size
                        if appid and appid not in ("0", "N/A", "unknown"):
                            self._size_by_appid[appid] = size

            logger.info(f"Loaded games cache: {len(self._games)} games ({len(self._size_by_path)} cached sizes)")
        except Exception as e:
            logger.warning(f"Failed to load games cache from {cache_path}: {e}")

    def get_games(self) -> List[dict]:
        """Return a copy of the cached games list."""
        with self._lock:
            return [dict(g) for g in self._games]

    def get_cached_size(self, install_path: str = None, appid: str = None) -> Optional[int]:
        """Return cached size_on_disk if available and > 0."""
        with self._lock:
            if install_path and install_path in self._size_by_path:
                return self._size_by_path[install_path]
            if appid and str(appid) in self._size_by_appid:
                return self._size_by_appid[str(appid)]
        return None

    def set_cached_size(self, install_path: str, size: int, appid: str = None) -> None:
        """Update cached size for an install path and optional appid."""
        if not size or size <= 0:
            return
        with self._lock:
            if install_path:
                self._size_by_path[install_path] = size
            if appid and str(appid) not in ("0", "N/A", "unknown"):
                self._size_by_appid[str(appid)] = size
            self._dirty = True

    def save(self, games: List[dict] = None) -> None:
        """Save games to disk atomically."""
        with self._lock:
            if games is not None:
                self._games = games
                for g in games:
                    size = g.get("size_on_disk", 0)
                    install_path = g.get("install_path")
                    appid = str(g.get("appid", ""))
                    if size and size > 0:
                        if install_path:
                            self._size_by_path[install_path] = size
                        if appid and appid not in ("0", "N/A", "unknown"):
                            self._size_by_appid[appid] = size
                self._dirty = True

            if not self._dirty:
                return

            games_to_save = list(self._games)
            self._dirty = False

        cache_path = _get_games_cache_path()
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "saved_at": time.time(),
                "games": games_to_save,
            }
            dir_path = cache_path.parent
            with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, encoding="utf-8") as tf:
                json.dump(payload, tf, indent=2)
                temp_name = tf.name
            os.replace(temp_name, cache_path)
            logger.debug(f"Saved {len(games_to_save)} games to {cache_path}")
        except Exception as e:
            logger.warning(f"Failed to save games cache: {e}")


_instance: Optional[GamesCache] = None
_instance_lock = threading.Lock()


def get_games_cache() -> GamesCache:
    """Return the global GamesCache singleton."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = GamesCache()
    return _instance
