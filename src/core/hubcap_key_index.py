"""
hubcap_key_index.py
───────────────────
Cached index of which depots Hubcap can and cannot serve.

The Hubcap ``/depot-keys`` endpoint lists every depot id in existence — about
380 000 ids, roughly 7 MB of JSON. Fetching it costs no quota but it is not
free either: parsing that payload takes seconds and enough memory to be
noticed, and the window must never be the thread that does it.

This module owns the payload. One copy lives on disk, tagged with the ETag
the server handed out, so a refresh costs a conditional request instead of a
full download. The parsed depot ids stay in memory, so answering "does
Hubcap have a key for this depot?" is a set lookup instead of a round trip.

The index distinguishes four states, and they must never be collapsed:

``available``
    Hubcap holds a decryption key for the depot.
``pending``
    Hubcap knows the depot but has not published a key for it yet.
``unknown``
    Hubcap has not indexed the depot at all.
``unavailable``
    No snapshot could be loaded, so nothing can be said about the depot.

``pending`` and ``unknown`` both mean "no key yet, wait for Hubcap".
``unavailable`` means "we could not look" and must never be reported as
"Hubcap does not have it".
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

CACHE_FILENAME = "hubcap_depot_keys.json"
CACHE_TTL_SECONDS = 2 * 3600
CACHE_VERSION = 1

# A failed fetch must not be retried for every game in a scan round.
FAILURE_BACKOFF_SECONDS = 300

STATE_AVAILABLE = "available"
STATE_PENDING = "pending"
STATE_UNKNOWN = "unknown"
STATE_UNAVAILABLE = "unavailable"


def _to_id_set(values: Any) -> Set[str]:
    """Turn a list of depot ids into a set of strings, fast.

    The ids arrive as JSON strings, so the common path is a plain set()
    build; the conversion only runs when they are not.
    """
    try:
        result = set(values or ())
    except TypeError:
        return set()
    if result and not isinstance(next(iter(result)), str):
        result = {str(v) for v in result}
    return result


def _on_gui_thread() -> bool:
    """True when we run on the Qt main thread of a live application.

    The payload is several megabytes and takes seconds to parse, so the
    window must never be the thread that fetches it. Headless runs (no
    QApplication) have no GUI thread, so the check stays out of the way.
    """
    try:
        from PyQt6.QtCore import QThread
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        return False

    app = QApplication.instance()
    if app is None:
        return False
    return QThread.currentThread() == app.thread()


class HubcapKeyIndex:
    """An immutable snapshot of the Hubcap depot-key index."""

    def __init__(
        self,
        existing: Optional[Any] = None,
        pending: Optional[Any] = None,
        fetched_at: float = 0.0,
        etag: Optional[str] = None,
        error: Optional[str] = None,
    ):
        self.existing: Set[str] = _to_id_set(existing)
        self.pending: Set[str] = _to_id_set(pending)
        self.fetched_at = float(fetched_at or 0.0)
        self.etag = etag
        self.error = error
        # Built once so callers can share the sets instead of copying ~380k
        # references every time they ask for the dict shape.
        self._as_dict: Dict[str, Any] = {
            "status": "success",
            "total_depot_ids": len(self.existing) + len(self.pending),
            "pending_count": len(self.pending),
            "existing_count": len(self.existing),
            "pending_depot_ids": self.pending,
            "existing_depot_ids": self.existing,
        }

    @property
    def loaded(self) -> bool:
        """True when a snapshot is available (fresh or stale)."""
        return self.error is None

    @property
    def age(self) -> float:
        if not self.fetched_at:
            return float("inf")
        return max(0.0, time.time() - self.fetched_at)

    def is_fresh(self, ttl: float = CACHE_TTL_SECONDS) -> bool:
        return self.loaded and self.age < ttl

    def state(self, depot_id: Any) -> str:
        """Return one of available / pending / unknown / unavailable."""
        if not self.loaded:
            return STATE_UNAVAILABLE
        did = str(depot_id)
        if did in self.existing:
            return STATE_AVAILABLE
        if did in self.pending:
            return STATE_PENDING
        return STATE_UNKNOWN

    def has_key(self, depot_id: Any) -> bool:
        return self.state(depot_id) == STATE_AVAILABLE

    def to_payload(self) -> Dict[str, Any]:
        """The on-disk representation."""
        return {
            "version": CACHE_VERSION,
            "fetched_at": self.fetched_at,
            "etag": self.etag,
            "existing_depot_ids": sorted(self.existing),
            "pending_depot_ids": sorted(self.pending),
        }

    def to_dict(self) -> Dict[str, Any]:
        """The dict shape the rest of the app expects from get_hubcap_depot_keys."""
        if not self.loaded:
            return {"error": self.error}
        return dict(self._as_dict)


_lock = threading.RLock()
_index: Optional[HubcapKeyIndex] = None
_last_failure_at: float = 0.0


def get_cache_path() -> Path:
    """Where the index snapshot lives on disk."""
    from utils.helpers import get_base_path

    return Path(get_base_path()) / "db" / CACHE_FILENAME


def _load_from_disk() -> Optional[HubcapKeyIndex]:
    path = get_cache_path()
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception as e:
        logger.debug(f"[HubcapKeyIndex] Could not read the cached index: {e}")
        return None

    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        logger.debug("[HubcapKeyIndex] Cached index has an unexpected layout; ignoring it.")
        return None

    try:
        return HubcapKeyIndex(
            existing=data.get("existing_depot_ids") or (),
            pending=data.get("pending_depot_ids") or (),
            fetched_at=float(data.get("fetched_at") or 0.0),
            etag=data.get("etag"),
        )
    except Exception as e:
        logger.debug(f"[HubcapKeyIndex] Cached index is malformed: {e}")
        return None


def _save_to_disk(index: HubcapKeyIndex) -> None:
    path = get_cache_path()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(index.to_payload()), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f"[HubcapKeyIndex] Could not write the index cache: {e}")
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError as e:
            logger.debug(f"[HubcapKeyIndex] Could not remove the temporary cache file: {e}")


def get_index(*, allow_network: bool = False, force: bool = False) -> HubcapKeyIndex:
    """Return the depot-key index.

    ``allow_network`` must stay False for anything that runs on the GUI
    thread: the payload is several megabytes and takes seconds to parse.
    """
    global _index

    with _lock:
        current = _index

    if current is not None and not force and current.is_fresh():
        return current

    if not allow_network:
        if current is not None:
            return current
        cached = _load_from_disk()
        if cached is not None:
            with _lock:
                _index = cached
            return cached
        return HubcapKeyIndex(error="the Hubcap depot-key index has not been loaded yet")

    return refresh(force=force)


def refresh(force: bool = False) -> HubcapKeyIndex:
    """Fetch the index from Hubcap, conditionally when an ETag is known.

    A failed fetch is remembered for a short while so that a scan over many
    games does not turn into one failed request per game.
    """
    global _index, _last_failure_at

    with _lock:
        previous = _index
    if previous is None:
        previous = _load_from_disk()

    if previous is not None and not force and previous.is_fresh():
        with _lock:
            _index = previous
        return previous

    if not force and _last_failure_at and (time.time() - _last_failure_at) < FAILURE_BACKOFF_SECONDS:
        logger.debug("[HubcapKeyIndex] Skipping the refresh; the last attempt failed recently.")
        if previous is not None:
            with _lock:
                _index = previous
            return previous
        return HubcapKeyIndex(error="the Hubcap depot-key index could not be loaded")

    if _on_gui_thread():
        # The window must never pay for this payload.
        logger.warning(
            "[HubcapKeyIndex] Refusing to fetch the depot-key index on the GUI thread."
        )
        if previous is not None:
            with _lock:
                _index = previous
            return previous
        return HubcapKeyIndex(error="the Hubcap depot-key index cannot be loaded on the GUI thread")

    from core.morrenus_api import fetch_depot_keys_index

    etag = previous.etag if previous is not None else None
    result = fetch_depot_keys_index(etag=etag)

    index: HubcapKeyIndex
    if result.get("not_modified"):
        if previous is not None:
            previous.fetched_at = time.time()
            _save_to_disk(previous)
            index = previous
        else:
            index = HubcapKeyIndex(error="Hubcap reported no change but no cached index exists")
    elif not result.get("ok"):
        _last_failure_at = time.time()
        error = result.get("error") or "the Hubcap depot-key index could not be fetched"
        if previous is not None:
            # Keep the stale snapshot: the keys that were published stay
            # published, so the snapshot still answers most questions.
            logger.warning(f"[HubcapKeyIndex] Refresh failed ({error}); keeping the cached snapshot.")
            index = previous
        else:
            index = HubcapKeyIndex(error=error)
    else:
        data = result.get("data") or {}
        index = HubcapKeyIndex(
            existing=data.get("existing_depot_ids") or (),
            pending=data.get("pending_depot_ids") or (),
            fetched_at=time.time(),
            etag=result.get("etag"),
        )
        _save_to_disk(index)
        logger.info(
            f"[HubcapKeyIndex] Index loaded: {len(index.existing)} depot(s) with a key, "
            f"{len(index.pending)} awaiting a key."
        )

    with _lock:
        _index = index
    return index


def reset() -> None:
    """Drop the in-memory snapshot (tests)."""
    global _index, _last_failure_at
    with _lock:
        _index = None
        _last_failure_at = 0.0
