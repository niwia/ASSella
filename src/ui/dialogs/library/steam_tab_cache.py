import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from utils.helpers import get_base_path

logger = logging.getLogger(__name__)

CACHE_FILENAME = "steam_tab_cache.json"


def get_steam_cache_path() -> Path:
    """Return the Path to steam_tab_cache.json in the ACCELA base folder."""
    return get_base_path() / CACHE_FILENAME


def load_steam_cache() -> List[Dict[str, Any]]:
    """
    Load cached Steam games list from disk.
    Returns an empty list if the cache doesn't exist or is invalid.
    """
    cache_path = get_steam_cache_path()
    if not cache_path.exists():
        return []
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data.get("games", [])
        elif isinstance(data, list):
            return data
    except Exception as e:
        logger.warning(f"[SteamTabCache] Error loading cache: {e}")
    return []


def save_steam_cache(games: List[Dict[str, Any]]) -> bool:
    """
    Save Steam games list to disk atomically with timestamp.
    """
    cache_path = get_steam_cache_path()
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "last_scanned": int(time.time()),
            "count": len(games),
            "games": games,
        }
        # Atomic write
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(cache_path.parent), prefix="steam_tab_cache_", suffix=".tmp"
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, str(cache_path))
        return True
    except Exception as e:
        logger.error(f"[SteamTabCache] Failed to save cache: {e}")
        return False
