import logging
import os
import ssl
from pathlib import Path
from typing import Optional, Dict, List, Union, Tuple, Any

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from utils.helpers import get_base_path
from utils.settings import get_settings

logger = logging.getLogger(__name__)

_use_proxy_fallback = False

def is_using_proxy() -> bool:
    """Returns True if the proxy is currently being used (either forced in settings or due to fallback)."""
    # Deactivated for now as per website team feedback. Always returns False.
    return False

def _get_base_url(force_direct: bool = False) -> str:
    """Gets the base API URL dynamically, checking for Wirecutter proxy settings."""
    if not force_direct and is_using_proxy():
        settings = get_settings()
        proxy_url = settings.value("wirecutter_url", "https://rapid-thunder-fba1wirecutter.7ucking.workers.dev", type=str).strip()
        if proxy_url:
            return proxy_url.rstrip("/")
    return "https://hubcapmanifest.com/api/v1"
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


import threading

_thread_local = threading.local()


def get_session() -> requests.Session:
    """Gets or creates a thread-local requests.Session object."""
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        session.verify = False
        session.mount("https://", SSLAdapter())
        _thread_local.session = session
    return _thread_local.session


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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
    Helper to perform JSON requests (GET/POST) with unified error handling.
    Returns the JSON data (dict or list) on success.
    Returns a dict with {"error": msg} on failure.
    """
    global _use_proxy_fallback
    headers = _get_headers()
    if not headers:
        return {"error": "API Key is not set. Please set it in Settings."}

    url = f"{_get_base_url()}{endpoint}"

    try:
        response = get_session().request(
            method, url, headers=headers, params=params, timeout=10
        )
        response.raise_for_status()
        return response.json()
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        if False: # was: if not is_using_proxy():
            logger.warning(f"Direct connection to {url} failed: {e}. Attempting auto-fallback to Wirecutter proxy...")
            _use_proxy_fallback = True
            url = f"{_get_base_url()}{endpoint}"
            try:
                response = get_session().request(
                    method, url, headers=headers, params=params, timeout=10
                )
                response.raise_for_status()
                return response.json()
            except Exception as retry_e:
                error_msg = _handle_request_exception(retry_e, f"API {method} to {endpoint} (Proxied Fallback)")
                return {"error": error_msg}
        else:
            error_msg = _handle_request_exception(e, f"API {method} to {endpoint}")
            return {"error": error_msg}
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
    """
    Retrieves user statistics.
    """
    logger.info("Fetching user stats")
    # API key is passed in headers by _make_json_request, but some endpoints
    # might require it in query params (legacy). Adding both to be safe based on original code.
    settings = get_settings()
    api_key = settings.value("morrenus_api_key", "", type=str)

    return _make_json_request("GET", "/user/stats", params={"api_key": api_key})


def check_health() -> Dict:
    """
    Checks if the Hubcab API is healthy.
    Note: Health check often doesn't need Auth, but we use the shared session.
    """
    global _use_proxy_fallback
    url = f"{_get_base_url()}/health"
    try:
        response = get_session().get(url, timeout=5)
        response.raise_for_status()
        return response.json()
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        if False: # was: if not is_using_proxy():
            logger.warning(f"Health check direct connection failed: {e}. Retrying via proxy fallback...")
            _use_proxy_fallback = True
            url = f"{_get_base_url()}/health"
            try:
                response = get_session().get(url, timeout=5)
                response.raise_for_status()
                return response.json()
            except Exception as retry_e:
                error_msg = _handle_request_exception(retry_e, "Health check (Proxied Fallback)")
                return {"status": "unhealthy", "error": error_msg}
        else:
            error_msg = _handle_request_exception(e, "Health check")
            return {"status": "unhealthy", "error": error_msg}
    except Exception as e:
        error_msg = _handle_request_exception(e, "Health check")
        return {"status": "unhealthy", "error": error_msg}


def download_manifest(app_id: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Downloads a manifest zip.
    Returns (filepath, None) on success, or (None, error_message) on failure.
    """
    global _use_proxy_fallback
    headers = _get_headers()
    if not headers:
        return None, "API Key is not set. Please set it in Settings."

    url = f"{_get_base_url()}/manifest/{app_id}"
    manifests_dir = Path(get_base_path()) / "hubcap_manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    save_path = manifests_dir / f"accela_fetch_{app_id}.zip"

    logger.info(f"Downloading manifest {app_id} to {save_path}")

    try:
        from utils.settings import get_settings
        import datetime
        settings = get_settings()
        if settings and settings.value("save_old_manifests", True, type=bool):
            if save_path.exists():
                mod_time = save_path.stat().st_mtime
                dt = datetime.datetime.fromtimestamp(mod_time)
                ts_str = dt.strftime("%Y%m%d_%H%M%S")
                backup_path = manifests_dir / f"accela_fetch_{app_id}_{ts_str}.zip"
                try:
                    os.rename(save_path, backup_path)
                    logger.info(f"Backed up previous manifest to {backup_path.name}")
                except OSError as e:
                    logger.warning(f"Failed to backup old manifest: {e}")
                
                # Cleanup older backups to respect the limit
                limit = settings.value("max_old_manifests", 3, type=int)
                backups = list(manifests_dir.glob(f"accela_fetch_{app_id}_*.zip"))
                if len(backups) > limit:
                    # Sort by modification time (oldest first)
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
        def do_download(download_url):
            with get_session().get(download_url, headers=headers, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(save_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

        try:
            do_download(url)
            return str(save_path), None
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if False: # was: if not is_using_proxy():
                logger.warning(f"Download manifest direct connection failed: {e}. Retrying via proxy fallback...")
                _use_proxy_fallback = True
                url = f"{_get_base_url()}/manifest/{app_id}"
                do_download(url)
                return str(save_path), None
            else:
                raise e

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

