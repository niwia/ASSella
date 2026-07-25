import datetime
import logging
import os
import ssl
import threading
from pathlib import Path
from typing import Optional, Dict, List, Union, Tuple, Any

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from utils.helpers import get_base_path
from utils.settings import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://hubcapmanifest.com/api/v1"
DEFAULT_SEARCH_LIMIT = 100
MAX_SEARCH_LIMIT = 100

# Error messages for specific HTTP status codes
API_ERROR_MESSAGES = {
    401: "Invalid or missing API key. Please check your credentials in Settings.",
    403: "Access denied. Your account may be blocked or the App ID is not accessible.",
    404: "Game not found in library. The App ID may be incorrect or not available.",
    429: "Daily API limit exceeded. Please try again later.",
    500: "Server error. The manifest may be corrupted or temporarily unavailable.",
}


class SSLAdapter(HTTPAdapter):
    """
    Custom HTTPAdapter that uses a more permissive SSL configuration.
    Helps with environments that have outdated CA bundles or SSL issues.
    """

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # Try to set a more compatible set of ciphers
        try:
            ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        except ssl.SSLError:
            pass  # Some systems don't support SECLEVEL
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


_thread_local = threading.local()

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_session() -> requests.Session:
    """Gets or creates a thread-local requests.Session object."""
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        session.verify = False
        session.mount("https://", SSLAdapter())
        _thread_local.session = session
    return _thread_local.session


def _get_headers() -> Optional[Dict[str, str]]:
    """Retrieves API key and constructs headers."""
    settings = get_settings()
    api_key = settings.value("morrenus_api_key", "", type=str)
    if not api_key:
        logger.warning("Hubcab API key is not set in settings.")
        return None
    return {"Authorization": f"Bearer {api_key}"}


def _handle_request_exception(e: Exception, context: str) -> str:
    """Centralized exception handler for request errors."""
    logger.error(f"{context} failed: {e}")
    error_str = str(e).lower()

    if isinstance(e, requests.exceptions.HTTPError):
        response = e.response
        status_code = response.status_code if response else "N/A"

        # Return mapped user-friendly error if exists
        if isinstance(status_code, int) and status_code in API_ERROR_MESSAGES:
            return API_ERROR_MESSAGES[status_code]

        # Try to get detail from API response
        try:
            if response:
                error_detail = response.json().get("detail", response.text)
                return f"API Error ({status_code}): {error_detail}"
        except ValueError:
            pass
        return f"API Error ({status_code})"

    if "ssl" in error_str or "wrong_version_number" in error_str:
        return "SSL connection failed. Check proxy/firewall settings."

    return f"Request Failed: {e}"


def _make_json_request(
    method: str, endpoint: str, params: dict = None
) -> Union[Dict, List]:
    """
    Helper to perform JSON requests (GET/POST) through the ISP bypass pipeline.
    Returns the JSON data (dict or list) on success.
    Returns a dict with {"error": msg} on failure.
    """
    headers = _get_headers()
    if not headers:
        return {"error": "API Key is not set. Please set it in Settings."}

    url = f"{BASE_URL}{endpoint}"

    try:
        from utils.isp_bypass import execute_hubcap_request
        response = execute_hubcap_request(
            get_session(), method, url, headers=headers, params=params, timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        error_msg = _handle_request_exception(e, f"API {method} to {endpoint}")
        return {"error": error_msg}


def search_games(
    query: str, limit: int = DEFAULT_SEARCH_LIMIT
) -> dict | dict[str, list] | dict[str, list[Any]]:

    logger.info(f"Searching Hubcab API for: {query}")
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError):
        normalized_limit = DEFAULT_SEARCH_LIMIT
    normalized_limit = max(1, min(normalized_limit, MAX_SEARCH_LIMIT))

    offset = 0
    all_games: list[Any] = []
    total_count: Optional[int] = None

    while True:
        data = _make_json_request(
            "GET",
            "/library",
            params={
                "search": query,
                "limit": normalized_limit,
                "offset": offset,
                "sort_by": "name",
            },
        )

        if isinstance(data, dict) and "error" in data:
            return data

        page_games: list[Any] = []
        if isinstance(data, dict):
            if isinstance(data.get("games"), list):
                page_games = data["games"]
            elif isinstance(data.get("results"), list):
                page_games = data["results"]

            if total_count is None:
                try:
                    total_count = int(data.get("total_count", 0))
                except (TypeError, ValueError):
                    total_count = None
        elif isinstance(data, list):
            page_games = data

        if not page_games:
            break

        all_games.extend(page_games)
        offset += len(page_games)

        if total_count is not None and offset >= total_count:
            break
        if len(page_games) < normalized_limit:
            break

    return {"results": all_games, "total_count": total_count or len(all_games)}


def get_user_stats() -> Dict:
    """Retrieves user statistics with cached fallback on network timeout."""
    logger.info("Fetching user stats")
    settings = get_settings()
    api_key = settings.value("morrenus_api_key", "", type=str)
    res = _make_json_request("GET", "/user/stats", params={"api_key": api_key})
    if isinstance(res, dict) and "error" not in res and res:
        settings.setValue("last_cached_user_stats", res)
        return res
    cached = settings.value("last_cached_user_stats", None)
    if isinstance(cached, dict) and cached:
        logger.info("Using cached user stats due to network request error")
        return cached
    return res


def check_health() -> Dict:
    """
    Checks if the Hubcab API is healthy using the ISP bypass pipeline,
    so users with ISP bypass enabled still get accurate health status.
    Does not require an API key.
    """
    url = f"{BASE_URL}/health"
    try:
        from utils.isp_bypass import execute_hubcap_request
        response = execute_hubcap_request(get_session(), "GET", url, timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        error_msg = _handle_request_exception(e, "Health check")
        return {"status": "unhealthy", "error": error_msg}


def download_manifest(app_id, branch: str = "public") -> Tuple[Optional[str], Optional[str]]:
    """
    Downloads a manifest zip through the ISP bypass pipeline.
    Returns (filepath, None) on success, or (None, error_message) on failure.
    """
    headers = _get_headers()
    if not headers:
        return None, "API Key is not set. Please set it in Settings."

    url = f"{BASE_URL}/manifest/{app_id}"
    if branch and branch != "public":
        url += f"?branch={branch}"

    manifests_dir = Path(get_base_path()) / "hubcap_manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    if branch and branch != "public":
        save_path = manifests_dir / f"accela_fetch_{app_id}_branch_{branch}.zip"
    else:
        save_path = manifests_dir / f"accela_fetch_{app_id}.zip"

    logger.info(f"Downloading manifest {app_id} to {save_path}")

    # Backup previous manifest if setting is enabled and old buildid differs
    try:
        settings = get_settings()
        # Force save_old_manifests to False to disable backup behavior
        save_old_manifests = False
        if save_path.exists() and settings and save_old_manifests:
            old_buildid = settings.value(f"fetched_buildid/{app_id}", "", type=str) if settings else ""
            if old_buildid:
                backup_path = manifests_dir / f"accela_fetch_{app_id}_build_{old_buildid}.zip"
                try:
                    if backup_path.exists():
                        backup_path.unlink()
                    os.rename(save_path, backup_path)
                    logger.info(f"Backed up previous manifest (build {old_buildid}) to {backup_path.name}")
                except OSError as e:
                    logger.warning(f"Failed to backup old manifest: {e}")

                # Cleanup older backups to respect the limit
                limit = settings.value("max_old_manifests", 3, type=int)
                backups = list(manifests_dir.glob(f"accela_fetch_{app_id}_*.zip"))
                if len(backups) > limit:
                    backups.sort(key=lambda p: p.stat().st_mtime)
                    to_delete = len(backups) - limit
                    for b in backups[:to_delete]:
                        try:
                            os.remove(b)
                            logger.info(f"Deleted old manifest backup {b.name}")
                        except OSError as e:
                            logger.warning(f"Failed to delete old manifest backup {b.name}: {e}")
    except Exception as e:
        logger.warning(f"Error during manifest backup routine: {e}")

    try:
        from utils.isp_bypass import execute_hubcap_request
        r = execute_hubcap_request(
            get_session(), "GET", url, headers=headers, stream=True, timeout=60
        )
        r.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return str(save_path), None

    except Exception as e:
        # Cleanup partial download
        if save_path.exists():
            try:
                os.remove(save_path)
            except OSError:
                pass
        error_msg = _handle_request_exception(e, f"Download {app_id}")
        return None, error_msg


def get_manifest_status(app_id: str) -> Dict:
    """
    Calls /api/v1/status/{app_id} to check Hubcap's manifest freshness.
    Returns dict with keys: status, needs_update, update_in_progress, file_modified, error
    """
    logger.info(f"Fetching manifest status for app {app_id}")
    return _make_json_request("GET", f"/status/{app_id}")
