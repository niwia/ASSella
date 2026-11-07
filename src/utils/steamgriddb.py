"""SteamGridDB API v2 client (key check and artwork lookups).

API: https://www.steamgriddb.com/api/v2 with ``Authorization: Bearer <key>``.
Keys are made at https://www.steamgriddb.com/profile/preferences/api.

Only the key check is used today; the lookups are here so later features
(fallback art, Steam library art) share one client and the user's style
preferences from Settings -> Integrations -> Others.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.steamgriddb.com/api/v2"
KEY_URL = "https://www.steamgriddb.com/profile/preferences/api"
TIMEOUT = 10

SETTING_KEY = "steamgriddb_api_key"
SETTING_STYLE = "steamgriddb_style"
SETTING_NSFW = "steamgriddb_nsfw"
SETTING_HUMOR = "steamgriddb_humor"
SETTING_FETCH_LOGOS = "steamgriddb_fetch_logos"

# Grid styles SteamGridDB accepts (heroes and logos have their own sets;
# "any" sends no style filter).
STYLES = [
    ("any", "Any style"),
    ("alternate", "Alternate"),
    ("blurred", "Blurred"),
    ("white_logo", "White logo"),
    ("material", "Material"),
    ("no_logo", "No logo"),
]


def get_api_key(settings=None) -> str:
    if settings is None:
        from utils.settings import get_settings

        settings = get_settings()
    return (settings.value(SETTING_KEY, "", type=str) or "").strip()


def _headers(key: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {key}", "User-Agent": "ASSella"}


def check_key(key: str) -> Tuple[bool, str]:
    """(ok, message). One small request: grids for Half-Life 2 (app 220)."""
    key = (key or "").strip()
    if not key:
        return False, "No key entered"
    try:
        resp = requests.get(f"{BASE_URL}/grids/steam/220", headers=_headers(key),
                            params={"limit": 1}, timeout=TIMEOUT)
    except requests.exceptions.Timeout:
        return False, "SteamGridDB did not answer (timeout)"
    except requests.exceptions.RequestException as e:
        return False, f"Could not reach SteamGridDB: {type(e).__name__}"
    if resp.status_code == 200:
        try:
            if resp.json().get("success"):
                return True, "Key works"
        except ValueError:
            pass
        return False, "Unexpected reply from SteamGridDB"
    if resp.status_code in (401, 403):
        return False, "Key rejected"
    if resp.status_code == 429:
        return False, "Rate limited, try again later"
    return False, f"SteamGridDB HTTP {resp.status_code}"


def query_params(settings=None) -> Dict[str, Any]:
    """Style / NSFW / humor filters from Settings -> Integrations -> Others."""
    if settings is None:
        from utils.settings import get_settings

        settings = get_settings()
    return _params(settings)


def _params(settings) -> Dict[str, Any]:
    style = (settings.value(SETTING_STYLE, "any", type=str) or "any") if settings else "any"
    nsfw = settings.value(SETTING_NSFW, False, type=bool) if settings else False
    humor = settings.value(SETTING_HUMOR, False, type=bool) if settings else False
    params: Dict[str, Any] = {"nsfw": "any" if nsfw else "false", "humor": "any" if humor else "false"}
    if style and style != "any":
        params["styles"] = style
    return params


def get_assets(kind: str, steam_appid: str, settings=None, dimensions: Optional[str] = None) -> List[dict]:
    """Assets of ``kind`` (grids | heroes | logos | icons) for a Steam AppID, best first."""
    if settings is None:
        from utils.settings import get_settings

        settings = get_settings()
    key = get_api_key(settings)
    if not key or kind not in ("grids", "heroes", "logos", "icons"):
        return []
    params = _params(settings)
    if kind != "grids":
        params.pop("styles", None)  # grid styles do not apply to the other types
    if dimensions:
        params["dimensions"] = dimensions
    try:
        resp = requests.get(f"{BASE_URL}/{kind}/steam/{steam_appid}", headers=_headers(key),
                            params=params, timeout=TIMEOUT)
        if resp.status_code != 200:
            logger.debug(f"[SteamGridDB] {kind} for {steam_appid}: HTTP {resp.status_code}")
            return []
        data = resp.json().get("data") or []
        return sorted(data, key=lambda a: a.get("score", 0), reverse=True)
    except Exception as e:
        logger.debug(f"[SteamGridDB] {kind} for {steam_appid} failed: {e}")
        return []
