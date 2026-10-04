"""
DownloadResumeManager — Centralized manager for persistent download pause/resume states.

Manages download state persistence inside {install_dir}/.DepotDownloader/download_state.json,
discovers paused downloads, checks for completed depots, and prevents updates on paused games.
"""

import os
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

STATE_FILENAME = "download_state.json"
DEPOT_DIRNAME = ".DepotDownloader"


class DownloadResumeManager:
    """Manages download state persistence, depot completion checks, and paused games."""

    @staticmethod
    def get_state_file_path(install_dir: str) -> str:
        """Returns the absolute path to download_state.json inside install_dir/.DepotDownloader/."""
        return os.path.join(install_dir, DEPOT_DIRNAME, STATE_FILENAME)

    @staticmethod
    def save_download_state(
        install_dir: str,
        appid: str,
        game_name: str,
        status: str = "paused",
        branch: str = "public",
        total_size: int = 0,
        completed_size: int = 0,
        completed_depots: Optional[List[str]] = None,
        selected_depots: Optional[List[str]] = None,
        archive_path: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Saves or updates download_state.json in {install_dir}/.DepotDownloader/.
        Writes atomically via temporary file to prevent corruption.
        """
        if not install_dir or not os.path.exists(install_dir):
            return False

        depot_dir = os.path.join(install_dir, DEPOT_DIRNAME)
        os.makedirs(depot_dir, exist_ok=True)
        target_path = os.path.join(depot_dir, STATE_FILENAME)

        state = {
            "appid": str(appid),
            "game_name": str(game_name),
            "status": str(status),
            "branch": str(branch or "public"),
            "install_dir": str(install_dir),
            "total_size": int(total_size),
            "completed_size": int(completed_size),
            "completed_depots": [str(d) for d in (completed_depots or [])],
            "selected_depots": [str(d) for d in (selected_depots or [])],
            "archive_path": str(archive_path or ""),
            "timestamp": int(time.time()),
            "extra_metadata": dict(extra_metadata or {}),
        }

        try:
            # Atomic write
            temp_file = target_path + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.replace(temp_file, target_path)
            logger.info(f"[DownloadResumeManager] Saved download state for {game_name} ({appid}) [Status: {status}]")
            return True
        except Exception as e:
            logger.error(f"[DownloadResumeManager] Failed to save download state for {appid}: {e}")
            return False

    @staticmethod
    def get_download_state(install_dir: str) -> Optional[Dict[str, Any]]:
        """Reads download_state.json from install_dir/.DepotDownloader/ if valid."""
        if not install_dir:
            return None
        target_path = os.path.join(install_dir, DEPOT_DIRNAME, STATE_FILENAME)
        if not os.path.isfile(target_path):
            return None

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("appid"):
                return data
        except Exception as e:
            logger.debug(f"[DownloadResumeManager] Could not read download state from {target_path}: {e}")
        return None

    @staticmethod
    def clear_download_state(install_dir: str) -> bool:
        """Removes download_state.json when installation is completed or discarded."""
        if not install_dir:
            return False
        target_path = os.path.join(install_dir, DEPOT_DIRNAME, STATE_FILENAME)
        if os.path.exists(target_path):
            try:
                os.remove(target_path)
                logger.info(f"[DownloadResumeManager] Cleared download state at {target_path}")
                return True
            except OSError as e:
                logger.warning(f"[DownloadResumeManager] Failed to remove download state file: {e}")
        return False

    @staticmethod
    def is_depot_completed(install_dir: str, depot_id: str, manifest_id: str) -> bool:
        """
        Checks if a depot was previously 100% completed.
        A depot is ONLY considered complete if both its manifest AND its .sha sidecar
        exist in .DepotDownloader and are non-empty. (These are only written upon exit code 0).
        """
        if not install_dir or not depot_id or not manifest_id:
            return False

        depot_dir = os.path.join(install_dir, DEPOT_DIRNAME)
        manifest_path = os.path.join(depot_dir, f"{depot_id}_{manifest_id}.manifest")
        sha_path = manifest_path + ".sha"

        try:
            if (
                os.path.isfile(manifest_path)
                and os.path.getsize(manifest_path) > 0
                and os.path.isfile(sha_path)
                and os.path.getsize(sha_path) > 0
            ):
                return True
        except OSError:
            pass
        return False

    @classmethod
    def is_game_download_paused(cls, appid: str, install_dir: Optional[str] = None) -> bool:
        """Checks if a game by appid has an active paused download state."""
        if not appid:
            return False

        appid_str = str(appid).strip()

        # Check explicit install_dir if provided
        if install_dir:
            st = cls.get_download_state(install_dir)
            if st and str(st.get("appid")) == appid_str and st.get("status") == "paused":
                return True

        # Search across all Steam library directories
        try:
            from core.steam_helpers import get_steam_libraries
            libraries = get_steam_libraries() or []
            for lib in libraries:
                common_dir = os.path.join(lib, "steamapps", "common")
                if not os.path.isdir(common_dir):
                    continue
                try:
                    for entry in os.scandir(common_dir):
                        if entry.is_dir():
                            st = cls.get_download_state(entry.path)
                            if st and str(st.get("appid")) == appid_str and st.get("status") == "paused":
                                return True
                except (OSError, PermissionError):
                    continue
        except Exception as e:
            logger.debug(f"[DownloadResumeManager] Error scanning libraries for paused appid {appid}: {e}")

        return False

    @classmethod
    def get_paused_game_state(cls, appid: str) -> Optional[Dict[str, Any]]:
        """Returns the full paused state dict for an appid if found in any library."""
        if not appid:
            return None
        appid_str = str(appid).strip()

        try:
            from core.steam_helpers import get_steam_libraries
            libraries = get_steam_libraries() or []
            for lib in libraries:
                common_dir = os.path.join(lib, "steamapps", "common")
                if not os.path.isdir(common_dir):
                    continue
                try:
                    for entry in os.scandir(common_dir):
                        if entry.is_dir():
                            st = cls.get_download_state(entry.path)
                            if st and str(st.get("appid")) == appid_str and st.get("status") == "paused":
                                tot = st.get("total_size", 0)
                                done = st.get("completed_size", 0)
                                st["progress_pct"] = (done / tot * 100) if tot > 0 else 0
                                return st
                except (OSError, PermissionError):
                    continue
        except Exception as e:
            logger.debug(f"[DownloadResumeManager] Error finding paused state for appid {appid}: {e}")

        return None
