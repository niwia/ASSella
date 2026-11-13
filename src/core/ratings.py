"""
ratings.py — Unified Denuvo + ProtonDB ratings module.

Design principles:
  - Pure core module: zero imports of PyQt6 or UI widget classes.
  - In-memory caches: JSON files are read once per session, never per-card.
  - Zero GUI-thread cost at widget construction time.
  - Background daemon workers (queue-based) for ProtonDB HTTP fetches —
    processes one appid at a time with a short inter-request sleep.
  - Lightweight subscriber pattern with debounced main-thread notifications
    per updated appid (or "*" for global Denuvo sync).
"""

import inspect
import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

import requests

from utils.helpers import get_data_file_path
from utils.main_thread import post_to_main_thread

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache paths & expiry
# ---------------------------------------------------------------------------
DENUVO_CACHE_PATH = get_data_file_path("denuvo_cache.json")
PROTONDB_CACHE_PATH = get_data_file_path("protondb_cache.json")

DENUVO_CACHE_EXPIRY           = 21600   # 6 hours
PROTONDB_CACHE_EXPIRY_SUCCESS = 604800  # 7 days
PROTONDB_CACHE_EXPIRY_FAILURE = 86400   # 24 hours

# Inter-request sleep inside the worker (seconds).
_WORKER_SLEEP_S = 0.2

# Debounce window before notifying subscribers after fetches arrive (seconds).
_REFRESH_DEBOUNCE_S = 0.5

# ---------------------------------------------------------------------------
# In-memory caches  (populated on first read, invalidated on write)
# ---------------------------------------------------------------------------
_denuvo_mem_cache: Optional[dict]   = None
_protondb_mem_cache: Optional[dict] = None
_denuvo_mem_lock   = threading.Lock()
_protondb_mem_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Subscriber registry & debounced notification state
# ---------------------------------------------------------------------------
RatingCallback = Callable[[str], None]
_subscribers: List[RatingCallback] = []
_subscribers_lock = threading.Lock()

_pending_updated_appids: Set[str] = set()
_debounce_timer: Optional[threading.Timer] = None
_refresh_lock = threading.Lock()


def subscribe_ratings(callback: RatingCallback) -> None:
    """Register a callback `fn(appid: str)` to be invoked on the main thread when ratings update."""
    with _subscribers_lock:
        if callback not in _subscribers:
            _subscribers.append(callback)


def unsubscribe_ratings(callback: RatingCallback) -> None:
    """Remove a previously registered rating callback."""
    with _subscribers_lock:
        if callback in _subscribers:
            _subscribers.remove(callback)


def _invoke_subscriber(callback: RatingCallback, appid: str) -> None:
    """Invoke subscriber with `appid`, falling back to 0-arg call if needed."""
    try:
        obj = getattr(callback, "__self__", None)
        if obj is not None:
            try:
                from PyQt6 import sip
                if isinstance(obj, sip.simplewrapper) and sip.isdeleted(obj):
                    unsubscribe_ratings(callback)
                    return
            except Exception as e:
                logger.debug(f"[Ratings] Could not check sip lifecycle for subscriber: {e}")
        sig = inspect.signature(callback)
        params = [
            p for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            or p.kind == inspect.Parameter.VAR_POSITIONAL
        ]
        if len(params) == 0:
            callback()  # type: ignore[misc]
        else:
            callback(appid)
    except Exception as e:
        logger.debug(f"Rating subscriber error for appid {appid}: {e}")


def notify_rating_updated(appid: str) -> None:
    """Queue `appid` (or '*' for all) for a debounced main-thread subscriber notification."""
    global _debounce_timer
    if not appid:
        return
    with _refresh_lock:
        _pending_updated_appids.add(str(appid))
        if _debounce_timer is not None:
            _debounce_timer.cancel()
        _debounce_timer = threading.Timer(_REFRESH_DEBOUNCE_S, _on_debounce_timer_fired)
        _debounce_timer.daemon = True
        _debounce_timer.start()


def _on_debounce_timer_fired() -> None:
    """Timer thread callback -> bridges execution to the main GUI thread."""
    try:
        post_to_main_thread(_do_ratings_refresh)
    except Exception as e:
        logger.debug(f"Could not post ratings refresh to main thread: {e}")


def _do_ratings_refresh() -> None:
    """Flush pending updated appids and notify all registered subscribers on the main thread."""
    with _refresh_lock:
        if not _pending_updated_appids:
            return
        appids = set(_pending_updated_appids)
        _pending_updated_appids.clear()

    with _subscribers_lock:
        callbacks = list(_subscribers)

    if not callbacks:
        return

    # If a bulk update ("*") occurred alongside specific appids, "*" covers everything.
    dispatch_targets = ["*"] if "*" in appids else sorted(appids)

    for appid in dispatch_targets:
        for cb in callbacks:
            _invoke_subscriber(cb, appid)


# ===========================================================================
#  Denuvo — helpers
# ===========================================================================

def _load_denuvo_cache() -> dict:
    global _denuvo_mem_cache
    with _denuvo_mem_lock:
        if _denuvo_mem_cache is not None:
            return _denuvo_mem_cache
        if not DENUVO_CACHE_PATH.exists():
            _denuvo_mem_cache = {}
            return {}
        try:
            with open(DENUVO_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _denuvo_mem_cache = data
                return data
        except Exception as e:
            logger.warning(f"Failed to load Denuvo cache: {e}")
        _denuvo_mem_cache = {}
        return {}


def _invalidate_denuvo_cache() -> None:
    global _denuvo_mem_cache
    with _denuvo_mem_lock:
        _denuvo_mem_cache = None


def _save_denuvo_cache_data(games_map: Dict[str, str]) -> None:
    _invalidate_denuvo_cache()
    try:
        DENUVO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"last_updated": time.time(), "games": games_map}
        tmp = DENUVO_CACHE_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(DENUVO_CACHE_PATH)
        logger.debug(f"Saved Denuvo cache with {len(games_map)} entries.")
    except Exception as e:
        logger.warning(f"Failed to write Denuvo cache: {e}")


# ===========================================================================
#  Denuvo — public API
# ===========================================================================

def get_denuvo_status(appid: str) -> Optional[str]:
    """Return Denuvo status for *appid* from in-memory cache. Always instant."""
    if not appid or appid in ("0", "N/A", "unknown"):
        return None
    cache  = _load_denuvo_cache()
    status = cache.get("games", {}).get(str(appid))
    if not status:
        return None
    from utils.settings import get_settings
    if get_settings().value("simplify_denuvo_status", False, type=bool):
        if status == "hypervisor":
            return "uncracked"
    return status


def sync_denuvo_cache_and_config(main_window=None, force: bool = False) -> dict:
    """Fetch Denuvo statuses, cache them, and notify subscribers."""
    cache        = _load_denuvo_cache()
    last_updated = cache.get("last_updated", 0)
    games_map    = cache.get("games", {})
    now          = time.time()

    should_fetch = force or (now - last_updated > DENUVO_CACHE_EXPIRY) or not games_map
    success      = True
    error_msg    = None

    if should_fetch:
        logger.info("Fetching Denuvo statuses from remote API...")
        url = "https://lhvknkrfhehcclzlabsl.supabase.co/rest/v1/games"
        token = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imxodmtua3JmaGVoY2NsemxhYnNsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzg4NzM0MTksImV4cCI6MjA5NDQ0OTQxOX0"
            ".B7YOW_hpn2zHxR-sfHgiNgqidpfESwJpixLrh-MevE8"
        )
        headers = {"apikey": token, "Authorization": f"Bearer {token}"}
        params  = {"drm_protection": "eq.Denuvo", "select": "steam_appid,status", "limit": 1000}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                new_map = {}
                for g in resp.json():
                    aid = g.get("steam_appid")
                    if aid and str(aid).isdigit():
                        s = g.get("status", "uncracked")
                        new_map[str(aid)] = (
                            "cracked"     if s == "cracked"    else
                            "hypervisor"  if s == "hypervisor" else
                            "uncracked"
                        )
                games_map = new_map
                _save_denuvo_cache_data(games_map)
                logger.info(f"Fetched {len(games_map)} Denuvo entries.")
                notify_rating_updated("*")
            else:
                success   = False
                error_msg = f"HTTP {resp.status_code}"
                logger.warning(f"Denuvo API error: HTTP {resp.status_code}")
        except Exception as e:
            success   = False
            error_msg = str(e)
            logger.warning(f"Denuvo API request failed: {e}")

    count = sum(1 for s in games_map.values() if s in ("uncracked", "hypervisor"))
    try:
        from core.steam_helpers import get_most_recent_steam_id
        steam_id = get_most_recent_steam_id() or "N/A"
    except Exception:
        steam_id = "N/A"

    if not success and not games_map:
        return {"success": False, "error": error_msg or "No cache and fetch failed."}
    return {"success": True, "count": count, "steam_id": steam_id}


# ===========================================================================
#  ProtonDB — helpers
# ===========================================================================

def _load_protondb_cache() -> dict:
    global _protondb_mem_cache
    with _protondb_mem_lock:
        if _protondb_mem_cache is not None:
            return _protondb_mem_cache
        if not PROTONDB_CACHE_PATH.exists():
            _protondb_mem_cache = {}
            return {}
        try:
            with open(PROTONDB_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _protondb_mem_cache = data
                return data
        except Exception as e:
            logger.warning(f"Failed to load ProtonDB cache: {e}")
        _protondb_mem_cache = {}
        return {}


def _invalidate_protondb_cache() -> None:
    global _protondb_mem_cache
    with _protondb_mem_lock:
        _protondb_mem_cache = None


def _save_protondb_entry(appid: str, tier: str, success: bool = True) -> None:
    """Persist one ProtonDB result to disk safely with locking to prevent concurrent clobbering."""
    global _protondb_mem_cache
    with _protondb_mem_lock:
        cache: dict = {}
        if PROTONDB_CACHE_PATH.exists():
            try:
                with open(PROTONDB_CACHE_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    cache = loaded
            except Exception as e:
                logger.warning(f"Error reading ProtonDB cache for merging: {e}")

        cache[str(appid)] = {"tier": tier, "timestamp": time.time(), "success": success}

        # Update memory cache in place to avoid disk read on next lookup
        _protondb_mem_cache = cache

        try:
            PROTONDB_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = PROTONDB_CACHE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)
            tmp.replace(PROTONDB_CACHE_PATH)
        except Exception as e:
            logger.warning(f"Failed to write ProtonDB cache entry for {appid}: {e}")


# ---------------------------------------------------------------------------
# Background worker queue  (created lazily, lives for the session)
# ---------------------------------------------------------------------------
_fetch_queue: queue.Queue = queue.Queue()
_worker_threads: list = []
_worker_lock = threading.Lock()
_WORKER_COUNT = 10   # parallel fetch workers; each sleeps between requests

# Set of appids already queued (prevents duplicates without searching the queue)
_queued_appids: set = set()
_queued_lock = threading.Lock()


def _ensure_worker_running() -> None:
    """Ensure up to _WORKER_COUNT background fetch workers are running."""
    global _worker_threads
    with _worker_lock:
        _worker_threads = [t for t in _worker_threads if t.is_alive()]
        while len(_worker_threads) < _WORKER_COUNT:
            t = threading.Thread(
                target=_worker_loop, daemon=True,
                name=f"ProtonDB-fetcher-{len(_worker_threads)}"
            )
            t.start()
            _worker_threads.append(t)


def _worker_loop() -> None:
    """
    Daemon loop: dequeue one appid, fetch ProtonDB, save, notify, sleep, repeat.
    Exits when queue stays empty for > 10 s (naturally dies, restarted lazily).
    """
    while True:
        try:
            appid = _fetch_queue.get(timeout=10)
        except queue.Empty:
            break

        try:
            logger.debug(f"ProtonDB fetch: {appid}")
            url  = f"https://www.protondb.com/api/v1/reports/summaries/{appid}.json"
            tier = "unknown"
            ok   = True
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    tier = resp.json().get("tier", "unknown").lower()
                else:
                    ok = False
            except Exception as e:
                logger.debug(f"ProtonDB fetch failed for {appid}: {e}")
                ok = False

            _save_protondb_entry(appid, tier, ok)
        finally:
            _fetch_queue.task_done()
            with _queued_lock:
                _queued_appids.discard(appid)
            # Schedule a debounced subscriber notification for this specific appid
            notify_rating_updated(str(appid))
            # Brief sleep between requests — keeps network gentle
            time.sleep(_WORKER_SLEEP_S)


# ===========================================================================
#  ProtonDB — public API
# ===========================================================================

def get_protondb_tier(appid: str) -> Optional[str]:
    """
    Return cached ProtonDB tier for *appid*.

    Returns:
      str  — cached tier ('platinum', 'gold', … 'unknown')  → render badge.
      None → not cached yet; fetch queued in background. Caller shows nothing.
    """
    if not appid or appid in ("0", "N/A", "unknown") or not str(appid).isdigit():
        return "unknown"

    appid_str = str(appid)
    cache = _load_protondb_cache()
    entry = cache.get(appid_str)
    now   = time.time()

    if entry:
        ts      = entry.get("timestamp", 0)
        expiry  = (PROTONDB_CACHE_EXPIRY_SUCCESS if entry.get("success", True)
                   else PROTONDB_CACHE_EXPIRY_FAILURE)
        if now - ts < expiry:
            return entry.get("tier", "unknown")
        _enqueue_fetch(appid_str)
        return entry.get("tier", "unknown")

    _enqueue_fetch(appid_str)
    return None


def is_protondb_fetching(appid: str) -> bool:
    """Return True if a ProtonDB fetch for *appid* is currently queued or in-flight."""
    if not appid or not str(appid).isdigit():
        return False
    with _queued_lock:
        return str(appid) in _queued_appids


def _enqueue_fetch(appid: str) -> None:
    """Add *appid* to the worker queue if not already queued/in-flight."""
    appid_str = str(appid)
    with _queued_lock:
        if appid_str in _queued_appids:
            return
        _queued_appids.add(appid_str)
    _fetch_queue.put(appid_str)
    _ensure_worker_running()


# ===========================================================================
#  Batch pre-warm  (called by the library after the list is fully built)
# ===========================================================================

def prefetch_protondb_for_appids(appids: list) -> None:
    """
    Enqueue ProtonDB fetches for a list of appids that are NOT yet cached.
    Called once after the library list is drawn — zero cost at widget-init time.
    """
    now   = time.time()
    cache = _load_protondb_cache()
    count = 0
    for appid in appids:
        if not appid or appid in ("0", "N/A", "unknown") or not str(appid).isdigit():
            continue
        appid_str = str(appid)
        entry = cache.get(appid_str)
        if entry:
            ts     = entry.get("timestamp", 0)
            expiry = (PROTONDB_CACHE_EXPIRY_SUCCESS if entry.get("success", True)
                      else PROTONDB_CACHE_EXPIRY_FAILURE)
            if now - ts < expiry:
                continue
        _enqueue_fetch(appid_str)
        count += 1
    if count:
        logger.debug(f"Queued {count} ProtonDB fetches (sequential worker).")