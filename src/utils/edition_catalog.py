"""Edition catalog: names, contents and platform facts for the depot selector.

The modern Steam Store API ``IStoreBrowseService/GetItems`` returns packages,
bundles and release state in one JSON round-trip per app or bundle, replacing
the old two-step dance of scraping the app page for bundle links and then
scraping each bundle page for its name and contained apps.

* ``GetItems`` for the base app gives its packages and bundle references,
* ``GetItems`` for a bundle gives its name and included app ids,
* the game's own app info (PICS via api.steamcmd.net) still gives platforms
  and the DLC list used to filter out crossover bundles.

Every answer is cached on disk for a week. A failed fetch keeps the last
known-good answer instead of replacing it with a shorter list.
"""

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from utils.helpers import get_cache_dir

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 7 * 24 * 60 * 60

_APPINFO_URL = "https://api.steamcmd.net/v1/info/{appid}"
_STORE_API_BASE = "https://api.steampowered.com/IStoreBrowseService"
_STORE_UA = "standalone-addapp/1.0"
_STORE_CONTEXT = {"language": "english", "country_code": "US"}


def _cache_file() -> Path:
    return Path(get_cache_dir("editions")) / "catalog.json"


def _read_cache() -> Dict[str, Any]:
    path = _cache_file()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"apps": {}, "bundles": {}, "bundle_data": {}, "packages": {}}
    except (OSError, ValueError) as exc:
        logger.debug("Edition cache unreadable, starting fresh: %s", exc)
        return {"apps": {}, "bundles": {}, "bundle_data": {}, "packages": {}}
    if not isinstance(data, dict):
        return {"apps": {}, "bundles": {}, "bundle_data": {}, "packages": {}}
    for bucket in ("apps", "bundles", "bundle_data", "packages"):
        data.setdefault(bucket, {})
        if not isinstance(data[bucket], dict):
            data[bucket] = {}
    return data


def _write_cache(data: Dict[str, Any]) -> None:
    path = _cache_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
        tmp.replace(path)
    except OSError as exc:
        logger.debug("Edition cache could not be written: %s", exc)


_CACHE_LOCK = threading.Lock()


def _update_cache(bucket: str, key: str, value: Dict[str, Any]) -> None:
    """Merge one entry into the cache file without clobbering a concurrent one.

    Bundle pages are fetched in parallel, so each writer re-reads the file and
    adds only its own key instead of writing a snapshot taken before the others
    finished.
    """
    with _CACHE_LOCK:
        data = _read_cache()
        data.setdefault(bucket, {})[key] = value
        _write_cache(data)


def _fresh(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    stamp = entry.get("ts")
    if not isinstance(stamp, (int, float)):
        return False
    return (time.time() - stamp) < CACHE_TTL_SECONDS


def _get_json(url: str, timeout: float = 20.0) -> Optional[Dict[str, Any]]:
    """Fetch a JSON URL and return the parsed payload, or ``None`` on failure."""
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _STORE_UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, ValueError, OSError) as exc:
            logger.debug("JSON fetch %s failed: %s", url, exc)
    return None


def _store_api(method: str, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Call an IStoreBrowseService method and return its JSON response."""
    url = (
        f"{_STORE_API_BASE}/{method}/v1/"
        f"?input_json={urllib.parse.quote(json.dumps(body))}"
    )
    return _get_json(url)


# --------------------------------------------------------------------- legacy title cleaning
# Kept for the test suite; bundle names arrive as plain strings from GetItems
# now, so this is no longer used in the hot path.


def _clean_title(raw: str) -> str:
    name = re.sub(r"<[^>]+>", " ", raw or "")
    name = name.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    name = re.sub(r"\s+", " ", name).strip()
    suffix = " on Steam"
    if name.endswith(suffix):
        name = name[: -len(suffix)].strip()
    name = re.sub(r"^Save\s+\d+%\s+on\s+", "", name, flags=re.IGNORECASE).strip()
    return name


# --------------------------------------------------------------------- app facts


def fetch_app_facts(
    appid: str,
    *,
    timeout: float = 6.0,
    allow_network: bool = True,
) -> Optional[Dict[str, Any]]:
    """Return ``{"name", "platforms", "dlc_appids"}`` for an app.

    ``None`` means "nothing known"; an empty list means "asked, and the answer
    was empty". The distinction lets callers keep a depot-based guess rather
    than pretending the game supports no platform at all.
    """
    key = str(appid).strip()
    if not key or key in ("0", "N/A", "unknown"):
        return None

    cache = _read_cache()
    entry = cache["apps"].get(key)
    if _fresh(entry):
        return {"name": str(entry.get("name") or ""),
                "platforms": list(entry.get("platforms") or []),
                "dlc_appids": list(entry.get("dlc_appids") or [])}
    if not allow_network:
        return None

    started = time.perf_counter()
    payload = _get_json(_APPINFO_URL.format(appid=key), timeout=timeout)
    if not isinstance(payload, dict):
        return None

    node = ((payload or {}).get("data") or {}).get(key) or {}
    common = node.get("common") or {}
    extended = node.get("extended") or {}
    raw_os = common.get("oslist")
    if isinstance(raw_os, str):
        platforms = [p.strip().lower() for p in raw_os.split(",") if p.strip()]
    elif isinstance(raw_os, list):
        platforms = [str(p).strip().lower() for p in raw_os if str(p).strip()]
    else:
        platforms = []
    raw_dlc = extended.get("listofdlc")
    if isinstance(raw_dlc, str):
        dlc_appids = [d.strip() for d in raw_dlc.split(",") if d.strip()]
    elif isinstance(raw_dlc, list):
        dlc_appids = [str(d).strip() for d in raw_dlc if str(d).strip()]
    else:
        dlc_appids = []

    if not platforms and not dlc_appids:
        return None

    name = str(common.get("name") or "")
    _update_cache("apps", key, {
        "ts": int(time.time()),
        "name": name,
        "platforms": platforms,
        "dlc_appids": dlc_appids,
    })
    logger.debug(
        "[Editions] app facts for %s: %.2f s, platforms=%s, dlc=%d",
        key, time.perf_counter() - started, platforms, len(dlc_appids),
    )
    return {"name": name, "platforms": platforms, "dlc_appids": dlc_appids}


def get_app_name(appid: str, *, allow_network: bool = True) -> str:
    """The game's own name, or ``""`` when it is not cached and offline."""
    facts = fetch_app_facts(appid, allow_network=allow_network)
    return str((facts or {}).get("name") or "").strip()


def get_supported_platforms(
    appid: str, *, allow_network: bool = True, timeout: float = 6.0
) -> Set[str]:
    """Platforms the game itself says it supports (linux, windows, macos)."""
    facts = fetch_app_facts(appid, timeout=timeout, allow_network=allow_network)
    return set(facts.get("platforms") or []) if facts else set()


def get_dlc_appids(
    appid: str, *, allow_network: bool = True, timeout: float = 6.0
) -> Set[str]:
    """The app's own DLC app ids, as published in its app info."""
    facts = fetch_app_facts(appid, timeout=timeout, allow_network=allow_network)
    return set(facts.get("dlc_appids") or []) if facts else set()


def _fetch_app_store_items(
    appid: str, timeout: float = 6.0
) -> Tuple[List[Dict[str, Any]], List[int]]:
    """Query GetItems for an app and extract both its packages and bundle IDs.

    Populates both the 'packages' and 'bundles' cache buckets in one round-trip
    so the depot selector does not ask Steam for the same app twice.
    """
    key = str(appid).strip()
    started = time.perf_counter()
    data = _store_api("GetItems", {
        "ids": [{"appid": int(key)}],
        "context": _STORE_CONTEXT,
        "data_request": {"include_all_purchase_options": True, "include_release": True},
    })
    if data is None:
        return [], []

    store_items = (data.get("response") or {}).get("store_items") or []
    packages: List[Dict[str, Any]] = []
    bundle_ids: List[int] = []

    for item in store_items:
        for opt in (item.get("purchase_options") or []):
            bid = opt.get("bundleid")
            if bid is not None:
                bid_int = int(bid)
                if bid_int not in bundle_ids:
                    bundle_ids.append(bid_int)

            pid = opt.get("packageid")
            if pid is not None:
                package_id = int(pid)
                option_text = (opt.get("purchase_option_name") or "").strip()
                name = clean_package_name(option_text) or f"Package {package_id}"
                price_cents = opt.get("final_price_in_cents")
                try:
                    price_cents = int(price_cents) if price_cents not in (None, "") else 0
                except (TypeError, ValueError):
                    price_cents = 0
                packages.append({
                    "packageid": package_id,
                    "name": name,
                    "price": int(price_cents),
                    "apps": [],
                })

    now = int(time.time())
    _update_cache("packages", key, {
        "ts": now,
        "packages": packages,
    })
    _update_cache("bundles", key, {
        "ts": now,
        "ids": bundle_ids,
    })
    logger.debug(
        "[Editions] app store items for %s: %.2f s, %d package(s), %d bundle(s)",
        key, time.perf_counter() - started, len(packages), len(bundle_ids),
    )
    return packages, bundle_ids


def discover_bundle_ids(
    appid: str, *, timeout: float = 6.0, allow_network: bool = True
) -> List[int]:
    """Bundle ids the game's store page links to as its editions.

    Uses ``GetItems`` on the app to extract bundle references from
    ``purchase_options`` instead of scraping HTML.
    """
    key = str(appid).strip()
    if not key or key in ("0", "N/A", "unknown") or not key.isdigit():
        return []

    cache = _read_cache()
    entry = cache["bundles"].get(key)
    if _fresh(entry):
        return [int(b) for b in entry.get("ids") or []]
    if not allow_network:
        return []

    _, bundle_ids = _fetch_app_store_items(key, timeout=timeout)
    return bundle_ids


def fetch_bundles_batch(
    bundle_ids: List[int], *, timeout: float = 6.0, allow_network: bool = True
) -> List[Optional[Dict[str, Any]]]:
    """Return ``[{"id", "name", "app_ids"}, ...]`` for a list of store bundles.

    Batches missing bundle IDs into a single ``GetItems`` call instead of
    querying Steam once per bundle across threads.
    """
    if not bundle_ids:
        return []

    cache = _read_cache()
    results: Dict[int, Optional[Dict[str, Any]]] = {}
    missing_ids: List[int] = []

    for raw_id in bundle_ids:
        try:
            bid = int(raw_id)
        except (TypeError, ValueError):
            continue
        key = str(bid)
        entry = cache["bundle_data"].get(key)
        if _fresh(entry):
            name = str(entry.get("name") or "")
            app_ids = [str(a) for a in entry.get("app_ids") or []]
            if name and app_ids:
                results[bid] = {"id": bid, "name": name, "app_ids": app_ids}
            else:
                results[bid] = None
        elif not allow_network:
            results[bid] = None
        else:
            missing_ids.append(bid)

    if missing_ids and allow_network:
        started = time.perf_counter()
        now = int(time.time())
        # Steam 400s past ~8KB in URL; 100 ids is safe.
        for i in range(0, len(missing_ids), 100):
            chunk = missing_ids[i:i + 100]
            data = _store_api("GetItems", {
                "ids": [{"bundleid": int(b)} for b in chunk],
                "context": _STORE_CONTEXT,
                "data_request": {"include_basic_info": True, "include_release": True},
            })
            store_items = (data.get("response") or {}).get("store_items") or [] if data else []
            found_bids = set()
            for item in store_items:
                try:
                    bid = int(item.get("id"))
                except (TypeError, ValueError):
                    continue
                found_bids.add(bid)
                name = str(item.get("name") or "").strip()
                included = item.get("included_appids") or []
                app_ids = (
                    [str(a) for a in included if str(a).strip() and str(a) != "0"]
                    if isinstance(included, list)
                    else []
                )
                if name and app_ids:
                    bundle_data = {"id": bid, "name": name, "app_ids": app_ids}
                    _update_cache("bundle_data", str(bid), {
                        "ts": now,
                        "name": name,
                        "app_ids": app_ids,
                    })
                    results[bid] = bundle_data
                else:
                    results[bid] = None

            for b in chunk:
                if b not in found_bids:
                    results[b] = None

        logger.debug(
            "[Editions] batched %d bundle(s): %.2f s",
            len(missing_ids), time.perf_counter() - started,
        )

    return [results.get(int(bid)) for bid in bundle_ids]


def fetch_bundle(
    bundle_id: int, *, timeout: float = 6.0, allow_network: bool = True
) -> Optional[Dict[str, Any]]:
    """Return ``{"id", "name", "app_ids"}`` for a store bundle.

    Uses ``GetItems`` queried by ``bundleid`` instead of downloading the
    bundle store page and parsing HTML.
    """
    res = fetch_bundles_batch([bundle_id], timeout=timeout, allow_network=allow_network)
    return res[0] if res else None


_ORIGINAL_FETCH_BUNDLE = fetch_bundle


def bundle_editions(
    appid: str,
    dlc_appids: Optional[Iterable[str]] = None,
    *,
    allow_network: bool = True,
    timeout: float = 6.0,
) -> List[Dict[str, Any]]:
    """Editions the store sells as bundles, minus pure crossovers.

    A bundle qualifies when it includes this game and its DLC apps are at least
    as numerous as the apps outside the game's own family. Without the DLC list
    a deluxe edition cannot be told from a crossover, so callers pass the list
    when they have it.
    """
    base = str(appid).strip()
    dlc_set: Optional[Set[str]] = None
    if dlc_appids is not None:
        dlc_set = {str(d).strip() for d in dlc_appids if str(d).strip()}

    bundle_ids = discover_bundle_ids(base, timeout=timeout, allow_network=allow_network)
    if not bundle_ids:
        return []

    # If fetch_bundle was monkeypatched (e.g. in test suite), use it; otherwise batch.
    if fetch_bundle != _ORIGINAL_FETCH_BUNDLE:
        bundles = [fetch_bundle(bid, timeout=timeout, allow_network=allow_network) for bid in bundle_ids]
    else:
        bundles = fetch_bundles_batch(bundle_ids, timeout=timeout, allow_network=allow_network)

    editions: List[Dict[str, Any]] = []
    for bundle in bundles:
        if not bundle:
            continue
        apps = {str(a) for a in bundle["app_ids"]}
        if base not in apps:
            continue
        if dlc_set is not None:
            family = (apps - {base}) & dlc_set
            others = apps - {base} - family
            if not family or len(family) < len(others):
                logger.debug(
                    "Bundle %s skipped for %s: it carries %d DLC app(s) and %d outsider(s)",
                    bundle["id"], base, len(family), len(others),
                )
                continue
        editions.append({
            "id": f"bundle:{bundle['id']}",
            "name": bundle["name"],
            "app_ids": sorted(apps, key=lambda value: (len(value), value)),
            "source": "bundle",
        })
    return editions


def clean_package_name(raw_text: str) -> str:
    """Strip markup and the price the store appends to a package option."""
    if not raw_text:
        return ""
    text = re.sub(r"<[^>]+>", "", str(raw_text)).strip()
    return re.sub(
        r"\s*-\s*[\$€£¥]\s?\d[\d\.,]*(?:\s+[\$€£¥]?\s?\d[\d\.,]*)*\s*$", "", text
    ).strip()


def fetch_store_packages(
    appid: str, *, timeout: float = 6.0, allow_network: bool = True
) -> List[Dict[str, Any]]:
    """Packages (subs) the store sells this game through, with name.

    Uses ``GetItems`` instead of the legacy ``appdetails`` endpoint. The API
    returns a flat ``purchase_options`` list; ``packageid`` entries are the
    subs and ``bundleid`` entries are skipped here (they are handled by
    ``discover_bundle_ids`` / ``fetch_bundle``).
    """
    key = str(appid).strip()
    if not key or not key.isdigit():
        return []
    entry = _read_cache()["packages"].get(key)
    if _fresh(entry):
        return [dict(p) for p in (entry or {}).get("packages") or []]
    if not allow_network:
        return []

    packages, _ = _fetch_app_store_items(key, timeout=timeout)
    return packages
