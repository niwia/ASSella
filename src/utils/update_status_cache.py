"""
update_status_cache.py
─────────────────────
Persistent, disk-backed cache for game update statuses.

Design goals:
 • Survives restarts — data written to a JSON file in the user's local data dir.
 • Smart expiry — "up_to_date" entries expire after CACHE_TTL_SECONDS (default 24h).
   "update_available" entries never expire (cleared only after user downloads update).
 • Thread-safe — uses a threading.Lock for all read/write access.
 • Non-blocking — cache file I/O is best-effort; failures are logged but do not crash.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# --- Constants ---
# How long (in seconds) an "up_to_date" result is trusted before we re-check.
# Default: 24 hours.  "update_available" never expires automatically.
CACHE_TTL_SECONDS = 24 * 60 * 60

# Status values that are considered "conclusively known" and worth persisting
PERSISTENT_STATUSES = {"update_available", "up_to_date"}

# Status values that should never be persisted (transient/incomplete)
TRANSIENT_STATUSES = {"checking", "cannot_determine"}


def _get_cache_path() -> Path:
    """Return the path to the on-disk update-status cache JSON file."""
    from utils.helpers import get_data_file_path
    return get_data_file_path("update_status_cache.json")


class UpdateStatusCache:
    """
    Disk-backed cache for per-AppID update status.

    Cache entry format (stored in JSON):
    {
        "<appid>": {
            "status":     "up_to_date" | "update_available",
            "updated_at": <unix timestamp float>
        },
        ...
    }
    """

    def __init__(self):
        self._lock = threading.Lock()
        # In-memory store: appid -> {"status": str, "updated_at": float}
        self._cache: Dict[str, dict] = {}
        self._dirty = False  # True when in-memory != on-disk
        self._save_timer: Optional[threading.Timer] = None
        self._load()

    # ──────────────────────────────── Public API ────────────────────────────────

    def get_status(self, appid: str) -> Optional[str]:
        """
        Return the cached status for *appid*, or None if:
         - not in cache
         - entry is expired (up_to_date entries older than CACHE_TTL_SECONDS)
        """
        with self._lock:
            entry = self._cache.get(str(appid))
            if entry is None:
                return None

            status = entry.get("status")
            if status not in PERSISTENT_STATUSES:
                return None

            # "update_available" never expires — user must download the update to clear it
            if status == "update_available":
                return status

            # "up_to_date" expires after TTL
            age = time.time() - entry.get("updated_at", 0)
            if age > CACHE_TTL_SECONDS:
                logger.debug(
                    f"Cache TTL expired for appid {appid} "
                    f"(age={age:.0f}s, ttl={CACHE_TTL_SECONDS}s)"
                )
                return None  # Let the checker re-validate

            return status

    def get_raw_status(self, appid: str) -> Optional[str]:
        """
        Return the last known persistent status regardless of age/TTL,
        used for UI display fallback when no active update check is running.
        """
        with self._lock:
            entry = self._cache.get(str(appid))
            if entry is None:
                return None
            status = entry.get("status")
            if status in PERSISTENT_STATUSES:
                return status
            return None

    def set_status(self, appid: str, status: str, metadata: dict = None) -> None:
        """
        Update the cache for *appid*.  Only PERSISTENT_STATUSES are stored;
        transient statuses are silently ignored.

        Optional *metadata* dict can carry diagnostic fields:
          - depot_diffs: dict of {depot_id: {"saved": X, "current": Y, "branch": Z}}
          - branch_buildid: remote branch build ID at check time
          - local_buildid:  local/installed build ID
          - branch:         branch name used for comparison
          - reason:         human-readable reason string
        """
        if status in TRANSIENT_STATUSES:
            return  # Don't persist transient states

        if status not in PERSISTENT_STATUSES:
            logger.debug(f"Ignoring unknown status '{status}' for cache appid={appid}")
            return

        with self._lock:
            entry = {
                "status": status,
                "updated_at": time.time(),
            }
            if metadata and isinstance(metadata, dict):
                entry.update(metadata)
            self._cache[str(appid)] = entry
            self._dirty = True

    def clear_status(self, appid: str) -> None:
        """Remove a specific appid from the cache (e.g. after downloading an update)."""
        with self._lock:
            if str(appid) in self._cache:
                del self._cache[str(appid)]
                self._dirty = True

    def clear_all(self) -> None:
        """Remove all entries from the update status cache and persist immediately."""
        with self._lock:
            self._cache.clear()
            self._dirty = True
        self.save()

    def save(self) -> None:
        """Write the in-memory cache to disk.  Non-blocking; errors are logged."""
        with self._lock:
            if not self._dirty:
                return
            data_to_write = dict(self._cache)
            self._dirty = False

        try:
            cache_path = _get_cache_path()
            tmp_path = cache_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data_to_write, f, indent=2)
            tmp_path.replace(cache_path)
            logger.debug(f"Update status cache saved ({len(data_to_write)} entries)")
        except Exception as e:
            logger.warning(f"Failed to save update status cache: {e}")
            # Re-mark dirty so next save attempt retries
            with self._lock:
                self._dirty = True

    def save_async(self) -> None:
        """Save cache to disk in a background thread (coalesced via debounce)."""
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(1.0, self._do_async_save)
            self._save_timer.daemon = True
            self._save_timer.name = "UpdateCacheSaveDebounce"
            self._save_timer.start()

    def _do_async_save(self) -> None:
        self.save()
        with self._lock:
            self._save_timer = None

    def purge_expired(self) -> int:
        """Remove stale 'up_to_date' entries that have exceeded TTL.  Returns count removed."""
        now = time.time()
        to_remove = []
        with self._lock:
            for appid, entry in list(self._cache.items()):
                if entry.get("status") == "up_to_date":
                    age = now - entry.get("updated_at", 0)
                    if age > CACHE_TTL_SECONDS:
                        to_remove.append(appid)
            for appid in to_remove:
                del self._cache[appid]
            if to_remove:
                self._dirty = True
        if to_remove:
            logger.info(f"Purged {len(to_remove)} expired update-status cache entries")
        return len(to_remove)

    def stats(self) -> dict:
        """Return a summary dict for logging/debugging."""
        with self._lock:
            total = len(self._cache)
            by_status: Dict[str, int] = {}
            for entry in self._cache.values():
                s = entry.get("status", "unknown")
                by_status[s] = by_status.get(s, 0) + 1
        return {"total": total, "by_status": by_status}

    # ──────────────────────────────── Private ───────────────────────────────────

    def _load(self) -> None:
        """Load cache from disk on startup."""
        try:
            cache_path = _get_cache_path()
            if not cache_path.exists():
                return

            with open(cache_path, "r", encoding="utf-8") as f:
                raw = json.load(f)

            # Validate structure — must be a dict of dicts
            if not isinstance(raw, dict):
                logger.warning("Update status cache file has unexpected format; discarding")
                return

            loaded = {}
            for appid, entry in raw.items():
                if not isinstance(entry, dict):
                    continue
                status = entry.get("status")
                if status not in PERSISTENT_STATUSES:
                    continue
                loaded_entry = {
                    "status": status,
                    "updated_at": float(entry.get("updated_at", 0)),
                }
                # Carry forward diagnostic metadata if present
                for diag_key in ("depot_diffs", "branch_buildid", "local_buildid",
                                 "branch", "reason"):
                    if diag_key in entry:
                        loaded_entry[diag_key] = entry[diag_key]
                loaded[str(appid)] = loaded_entry

            with self._lock:
                self._cache = loaded

            stats = self.stats()
            logger.info(
                f"Loaded update status cache: {stats['total']} entries "
                f"({stats['by_status']})"
            )
        except json.JSONDecodeError as e:
            logger.warning(f"Update status cache file is corrupt, discarding: {e}")
        except Exception as e:
            logger.warning(f"Failed to load update status cache: {e}")


# Module-level singleton — import and use directly
_instance: Optional[UpdateStatusCache] = None
_instance_lock = threading.Lock()


def get_update_cache() -> UpdateStatusCache:
    """Return the application-wide UpdateStatusCache singleton."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = UpdateStatusCache()
    return _instance
