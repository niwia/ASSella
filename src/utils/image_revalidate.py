"""Cache freshness metadata for Steam box art ("header" images).

Why this exists
---------------
``image_cache/{appid}.jpg`` had no expiry at all: once a box was downloaded it
was served forever, so a game whose Steam capsule got redesigned kept showing
the old artwork indefinitely.

The obvious fix - re-download every time - is wasteful, and the usual
conditional-request trick (ETag / ``If-None-Match``) does not work here.
Verified live against Steam's CDN:

    If-None-Match:     -> HTTP 200, 34296 bytes   (header ignored)
    If-Modified-Since: -> HTTP 304, 0 bytes       (works)

So this stores the ``Last-Modified`` value the server sent, and sends it back
as ``If-Modified-Since`` on the next check. If the artwork is unchanged the
server replies 304 with no body and we keep the file we already have. That
turns "check whether it changed" from a 34 KB download into a ~200 byte
header exchange.

Rate limiting is a non-issue: the CDN serves these with
``cache-control: public, max-age=315331556`` (about ten years). They are public
storefront assets, not an API, and there is no documented request budget.
Revalidating the whole local library costs one tiny request per game per
``REVALIDATE_DAYS``, which is negligible.

Storage is a single JSON sidecar rather than a table: it is written once per
image, never queried in bulk, and keeping it out of the SQLite database means
a corrupt sidecar cannot affect the appinfo cache.
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# How old a check must be before we bother asking the server again. A week
# means a typical library is revalidated ~4 times a year.
REVALIDATE_DAYS = 7

_META_FILENAME = ".revalidate.json"


def _meta_path(cache_dir: Path) -> Path:
    return cache_dir / _META_FILENAME


def _load(cache_dir: Path) -> Dict[str, dict]:
    path = _meta_path(cache_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        # A corrupt sidecar is not worth failing over; we just re-check
        # everything once and it rewrites itself.
        logger.debug("Box-art revalidation metadata unreadable; starting fresh")
        return {}


def _save(cache_dir: Path, data: Dict[str, dict]) -> None:
    path = _meta_path(cache_dir)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.debug(f"Could not persist box-art metadata: {e}")


def due_for_check(app_id: str, cache_dir: Path) -> Optional[str]:
    """Return the stored ``Last-Modified`` if this game is due a re-check.

    Returns ``None`` when no check is needed - never checked, or checked within
    the last ``REVALIDATE_DAYS`` - which is the caller's signal to do nothing
    and skip the network entirely.
    """
    entry = _load(cache_dir).get(str(app_id))
    if not isinstance(entry, dict):
        return None
    last_modified = entry.get("last_modified")
    if not last_modified:
        return None
    checked = float(entry.get("checked_at", 0) or 0)
    if (time.time() - checked) < REVALIDATE_DAYS * 86400:
        # Checked recently. Skip - this is the rate limit.
        return None
    return str(last_modified)


def record(app_id: str, cache_dir: Path, last_modified: Optional[str], url: str) -> None:
    """Remember what the server told us about this box."""
    data = _load(cache_dir)
    data[str(app_id)] = {
        "last_modified": last_modified,
        "url": url,
        "checked_at": time.time(),
    }
    _save(cache_dir, data)


def mark_checked(app_id: str, cache_dir: Path) -> None:
    """Record a successful 304 so we do not re-check on the very next launch."""
    data = _load(cache_dir)
    entry = data.get(str(app_id))
    if isinstance(entry, dict):
        # Mutate the dict we already loaded. Reloading here would discard the
        # change and leave checked_at stale, which means the next launch asks
        # the server again - exactly what this is meant to prevent.
        entry["checked_at"] = time.time()
        _save(cache_dir, data)


def prune(valid_appids: set) -> int:
    """Drop metadata for games that are no longer in the local cache."""
    from utils.image_fetcher import ImageFetcher

    cache_dir = ImageFetcher.get_cache_dir()
    data = _load(cache_dir)
    stale = [k for k in data if k not in valid_appids]
    if not stale:
        return 0
    for k in stale:
        data.pop(k, None)
    _save(cache_dir, data)
    return len(stale)