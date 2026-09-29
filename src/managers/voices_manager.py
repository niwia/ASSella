"""
Voices Manager
==============
Manages curated and tested game builds ("Voices"), allowing games with known
compatibility or release quirks to recommend specific build IDs, depot selections,
and manifest overrides.

Features:
- Boot-time asynchronous sync from GitHub (beta branch) with cached fallback.
- Dual lookup: exact AppID match or normalized title/alias matching.
- Manifest overrides and depot pre-selection support.
"""

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from utils.paths import Paths

logger = logging.getLogger("ACCELA.voices_manager")

DEFAULT_GITHUB_VOICES_URL = "https://raw.githubusercontent.com/niwia/ASSella/beta/voices.json"


def normalize_title(title: str) -> str:
    """Normalize game title for resilient matching."""
    cleaned = re.sub(r"[^\w\s]", "", str(title).lower())
    return " ".join(cleaned.split())


class VoicesManager:
    """Singleton manager for tested game builds and manifest overrides."""

    _instance: Optional["VoicesManager"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._data: Dict[str, Any] = {"version": 1, "games": {}, "named_games": {}}
        from utils.helpers import get_data_file_path
        self._cache_file = get_data_file_path("voices.json")
        self._cache_dir = self._cache_file.parent
        self._is_loaded = False

        self._load_local_data()
        self.sync_remote_async()

    @classmethod
    def get_instance(cls) -> "VoicesManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _load_local_data(self) -> None:
        """Load data from user cache, falling back to bundled res/voices.json."""
        # 1. Try user cache first
        if self._cache_file.exists():
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    if isinstance(cached, dict) and "games" in cached:
                        self._data = cached
                        self._is_loaded = True
                        logger.debug(f"Loaded voices database from cache ({len(cached.get('games', {}))} games)")
                        return
            except Exception as e:
                logger.warning(f"Failed to read cached voices.json: {e}")

        # 2. Fall back to bundled resource
        bundled_path = Paths.resource("voices.json")
        if bundled_path.exists():
            try:
                with open(bundled_path, "r", encoding="utf-8") as f:
                    bundled = json.load(f)
                    if isinstance(bundled, dict) and "games" in bundled:
                        self._data = bundled
                        self._is_loaded = True
                        logger.debug(f"Loaded voices database from bundled resource ({len(bundled.get('games', {}))} games)")
                        return
            except Exception as e:
                logger.warning(f"Failed to read bundled voices.json: {e}")

    def sync_remote_async(self) -> None:
        """Asynchronously check and sync the latest voices.json from GitHub."""
        def _worker():
            url = os.environ.get(
                "ASSELLA_VOICES_URL",
                f"{DEFAULT_GITHUB_VOICES_URL}?t={int(time.time())}",
            )
            try:
                if url.startswith("file://") or (os.name != "nt" and url.startswith("/")):
                    local_path = url.replace("file://", "")
                    with open(local_path, "r", encoding="utf-8") as f:
                        remote_data = json.load(f)
                else:
                    req = urllib.request.Request(
                        url,
                        headers={"User-Agent": "ASSella-Client", "Cache-Control": "no-cache"},
                    )
                    with urllib.request.urlopen(req, timeout=3.0) as resp:
                        if resp.status == 200:
                            remote_data = json.loads(resp.read().decode("utf-8"))
                        else:
                            return

                if isinstance(remote_data, dict) and "games" in remote_data:
                    with self._lock:
                        self._data = remote_data
                        self._is_loaded = True

                    try:
                        self._cache_dir.mkdir(parents=True, exist_ok=True)
                        with open(self._cache_file, "w", encoding="utf-8") as f:
                            json.dump(remote_data, f, indent=2)
                        logger.info(f"Voices database synced from remote ({len(remote_data.get('games', {}))} games)")
                    except Exception as ce:
                        logger.warning(f"Failed to save voices cache: {ce}")
            except Exception as e:
                logger.debug(f"Remote voices sync skipped/failed: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def get_recommendation(
        self, app_id: Union[str, int], game_name: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Query recommendation for a game by AppID or normalized title.
        Returns dictionary containing recommended_build_id, game_name, reason,
        manifest_overrides, and depots list if present; otherwise None.
        """
        aid_str = str(app_id).strip()
        games = self._data.get("games", {})

        # 1. Match by exact AppID
        if aid_str and aid_str != "0" and aid_str in games:
            rec = dict(games[aid_str])
            rec.setdefault("app_id", aid_str)
            return rec

        # 2. Match by normalized game title or aliases
        if game_name:
            norm_name = normalize_title(game_name)
            if norm_name:
                for candidate_aid, entry in games.items():
                    c_title = normalize_title(entry.get("game_name", ""))
                    if c_title and c_title == norm_name:
                        rec = dict(entry)
                        rec.setdefault("app_id", candidate_aid)
                        return rec

                    aliases = entry.get("aliases", [])
                    if isinstance(aliases, list):
                        for a in aliases:
                            if normalize_title(a) == norm_name:
                                rec = dict(entry)
                                rec.setdefault("app_id", candidate_aid)
                                return rec

                # 3. Check named_games
                named = self._data.get("named_games", {})
                if norm_name in named:
                    rec = dict(named[norm_name])
                    rec.setdefault("app_id", aid_str or "0")
                    return rec

        return None

    def has_recommendation(self, app_id: Union[str, int], game_name: str = "") -> bool:
        """Check if a recommended build exists for the given AppID or name."""
        return self.get_recommendation(app_id, game_name) is not None

    def get_recommended_build_id(
        self, app_id: Union[str, int], game_name: str = ""
    ) -> Optional[str]:
        """Convenience method returning the recommended build ID as string, or None."""
        rec = self.get_recommendation(app_id, game_name)
        if rec and rec.get("recommended_build_id"):
            return str(rec["recommended_build_id"]).strip()
        return None

    def get_manifest_overrides(
        self, app_id: Union[str, int], game_name: str = ""
    ) -> Dict[str, str]:
        """Returns depot_id -> manifest_id mapping if defined in recommendation."""
        rec = self.get_recommendation(app_id, game_name)
        if rec and isinstance(rec.get("manifest_overrides"), dict):
            return {str(k): str(v) for k, v in rec["manifest_overrides"].items()}
        return {}

    def get_recommended_depots(
        self, app_id: Union[str, int], game_name: str = ""
    ) -> List[str]:
        """Returns list of pre-selected depot IDs if defined in recommendation."""
        rec = self.get_recommendation(app_id, game_name)
        if rec and isinstance(rec.get("depots"), list):
            return [str(d) for d in rec["depots"]]
        return []
