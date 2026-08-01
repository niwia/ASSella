"""
ratings.py — Unified Denuvo + ProtonDB ratings module.

Design principles:
  - In-memory caches: JSON files are read once per session, never per-card.
  - Zero GUI-thread cost at widget construction time.
  - Single background daemon worker (queue-based) for ProtonDB HTTP fetches —
    no per-game thread spawning. Processes one appid at a time with a short
    inter-request sleep to stay gentle on the network.
  - Debounced UI refresh: all completed fetches coalesce into one repaint pass.
"""

import logging
import queue
import time
import json
import requests
import threading
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache paths & expiry
# ---------------------------------------------------------------------------
DENUVO_CACHE_PATH  = Path.home() / ".local" / "share" / "ACCELA" / "denuvo_cache.json"
PROTONDB_CACHE_PATH = Path.home() / ".local" / "share" / "ACCELA" / "protondb_cache.json"

DENUVO_CACHE_EXPIRY          = 21600    # 6 hours
PROTONDB_CACHE_EXPIRY_SUCCESS = 604800  # 7 days
PROTONDB_CACHE_EXPIRY_FAILURE = 86400   # 24 hours

# Inter-request sleep inside the worker (milliseconds → seconds).
# Keeps network impact minimal; 200 ms means ~5 req/s max.
_WORKER_SLEEP_S = 0.2

# Debounce window before triggering a UI repaint after fetches arrive.
_REFRESH_DEBOUNCE_MS = 500

# ---------------------------------------------------------------------------
# In-memory caches  (populated on first read, invalidated on write)
# ---------------------------------------------------------------------------
_denuvo_mem_cache: Optional[dict]  = None
_protondb_mem_cache: Optional[dict] = None
_denuvo_mem_lock   = threading.Lock()
_protondb_mem_lock = threading.Lock()


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
    """Fetch Denuvo statuses, cache them, and update SLSsteam's DenuvoGames config."""
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
            else:
                success   = False
                error_msg = f"HTTP {resp.status_code}"
                logger.warning(f"Denuvo API error: HTTP {resp.status_code}")
        except Exception as e:
            success   = False
            error_msg = str(e)
            logger.warning(f"Denuvo API request failed: {e}")

    from utils.yaml_config_manager import (
        get_user_config_path,
        clean_denuvo_games_section,
    )

    config_path = get_user_config_path()
    if config_path and config_path.exists():
        clean_denuvo_games_section(config_path)

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
# Single background worker queue  (created lazily, lives for the session)
# ---------------------------------------------------------------------------
_fetch_queue: queue.Queue = queue.Queue()
_worker_threads: list = []
_worker_lock = threading.Lock()
_WORKER_COUNT = 10   # parallel fetch workers; each sleeps between requests



# Set of appids already queued (prevents duplicates without searching the queue)
_queued_appids: set = set()
_queued_lock = threading.Lock()

# Debounce timer handle
_refresh_timer = None
_refresh_timer_lock = threading.Lock()


def _ensure_worker_running() -> None:
    """Ensure up to _WORKER_COUNT background fetch workers are running."""
    global _worker_threads
    with _worker_lock:
        # Prune finished threads
        _worker_threads = [t for t in _worker_threads if t.is_alive()]
        # Start new workers up to the limit
        while len(_worker_threads) < _WORKER_COUNT:
            t = threading.Thread(
                target=_worker_loop, daemon=True,
                name=f"ProtonDB-fetcher-{len(_worker_threads)}"
            )
            t.start()
            _worker_threads.append(t)



def _worker_loop() -> None:
    """
    Daemon loop: dequeue one appid, fetch ProtonDB, save, sleep, repeat.
    Exits when queue stays empty for > 10 s (naturally dies, restarted lazily).
    """
    while True:
        try:
            appid = _fetch_queue.get(timeout=10)
        except queue.Empty:
            # No work for 10 s — let the thread die; it will restart next request.
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
            # Schedule a debounced UI refresh on the GUI thread
            _schedule_debounced_refresh()
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

    cache = _load_protondb_cache()
    entry = cache.get(str(appid))
    now   = time.time()

    if entry:
        ts      = entry.get("timestamp", 0)
        expiry  = (PROTONDB_CACHE_EXPIRY_SUCCESS if entry.get("success", True)
                   else PROTONDB_CACHE_EXPIRY_FAILURE)
        if now - ts < expiry:
            return entry.get("tier", "unknown")   # fresh hit — instant, no network
        # Expired stale entry: return old value AND queue a refresh
        _enqueue_fetch(appid)
        return entry.get("tier", "unknown")

    # Cache miss: queue fetch, caller gets None → badge stays hidden for now
    _enqueue_fetch(appid)
    return None


def _enqueue_fetch(appid: str) -> None:
    """Add *appid* to the worker queue if not already queued/in-flight."""
    with _queued_lock:
        if appid in _queued_appids:
            return
        _queued_appids.add(appid)
    _fetch_queue.put(appid)
    _ensure_worker_running()


# ===========================================================================
#  Batch pre-warm  (called by the library after the list is fully built)
# ===========================================================================

def prefetch_protondb_for_appids(appids: list) -> None:
    """
    Enqueue ProtonDB fetches for a list of appids that are NOT yet cached.
    Called once after the library list is drawn — zero cost at widget-init time.
    Only uncached/expired items are queued; cached items are skipped entirely.
    """
    now   = time.time()
    cache = _load_protondb_cache()
    count = 0
    for appid in appids:
        if not appid or appid in ("0", "N/A", "unknown") or not str(appid).isdigit():
            continue
        entry = cache.get(str(appid))
        if entry:
            ts     = entry.get("timestamp", 0)
            expiry = (PROTONDB_CACHE_EXPIRY_SUCCESS if entry.get("success", True)
                      else PROTONDB_CACHE_EXPIRY_FAILURE)
            if now - ts < expiry:
                continue   # still fresh, skip
        _enqueue_fetch(appid)
        count += 1
    if count:
        logger.debug(f"Queued {count} ProtonDB fetches (sequential worker).")


# ===========================================================================
#  Debounced UI refresh  (safe to call from any thread)
# ===========================================================================

def _schedule_debounced_refresh() -> None:
    """Post a debounced ratings-refresh onto the Qt main thread."""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QTimer

    app = QApplication.instance()
    if not app:
        return

    # We need to interact with QTimer from the GUI thread only.
    # Use QTimer.singleShot(0, ...) as a thread-safe event post.
    def _on_main_thread():
        global _refresh_timer
        with _refresh_timer_lock:
            if _refresh_timer is None:
                _refresh_timer = QTimer()
                _refresh_timer.setSingleShot(True)
                _refresh_timer.timeout.connect(_do_ratings_refresh)
            _refresh_timer.start(_REFRESH_DEBOUNCE_MS)  # restart window each time

    QTimer.singleShot(0, app, _on_main_thread)


def _do_ratings_refresh() -> None:
    """Repaint all visible rating badges. Runs on the GUI thread."""
    from PyQt6.QtWidgets import QApplication
    from ui.dialogs.gamelibrary import GameItemWidget
    from ui.dialogs.gamelibrary_v2 import GameDetailsDialogV2
    from ui.dialogs.fetchmanifest import SearchItemWidget

    app = QApplication.instance()
    if not app:
        return

    for w in app.allWidgets():
        if isinstance(w, GameItemWidget):
            w.update_proton_badge()
        elif isinstance(w, GameDetailsDialogV2):
            w.update_title()
        elif isinstance(w, SearchItemWidget):
            w.update_ratings()
