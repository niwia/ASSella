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
    400: "Bad Request. The request parameters were invalid.",
    401: "Invalid or missing API key. Please check your credentials in Settings.",
    403: "Access denied. Your account may be blocked or the App ID is not accessible.",
    404: "Manifest not found on Hubcap. The App ID may be incorrect, unavailable on the server, or belongs to a DLC/depot rather than the base game.",
    429: "Daily API limit exceeded. Please try again later.",
    500: "Server error. The manifest may be corrupted or temporarily unavailable on Hubcap.",
    502: "Bad Gateway. Hubcap server is temporarily unreachable.",
    503: "Service Unavailable. Hubcap server is temporarily offline or undergoing maintenance.",
    504: "Gateway Timeout. Hubcap server took too long to respond.",
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
        response = getattr(e, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code is None:
            for code in (404, 401, 403, 400, 429, 500, 502, 503, 504):
                if str(code) in error_str:
                    status_code = code
                    break

        # Return mapped user-friendly error if exists
        if isinstance(status_code, int) and status_code in API_ERROR_MESSAGES:
            return API_ERROR_MESSAGES[status_code]

        # Try to get detail from API response if JSON
        try:
            if response is not None:
                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type:
                    error_detail = response.json().get("detail")
                    if error_detail:
                        return f"API Error ({status_code or 'Unknown'}): {error_detail}"
        except Exception:
            pass

        if status_code and status_code != "N/A":
            return f"API Error ({status_code})"
        return f"HTTP Request Failed: {e}"

    if "404" in error_str or "not found" in error_str:
        return API_ERROR_MESSAGES[404]

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
    """
    Search Hubcap for games by name or AppID using the free /search endpoint.

    Strategy:
      - Numeric queries: try exact AppID match first (/search?appid=true). If Hubcap
        returns no results (e.g. user typed "007" meaning the game title), transparently
        fall through to a name-based search so titles like "007: First Light" still surface.
      - Text queries: use /search?q=... directly (fast, free, no quota consumed).
      - Fallback: if /search itself errors, fall back to the old paginated /library endpoint.
    """
    logger.info(f"Searching Hubcap API for: {query}")
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError):
        normalized_limit = DEFAULT_SEARCH_LIMIT
    normalized_limit = max(1, min(normalized_limit, MAX_SEARCH_LIMIT))

    # ── Path 1: Numeric query — try AppID exact-match first ──────────────────
    if query.strip().isdigit():
        data = _make_json_request(
            "GET", "/search",
            params={"q": query.strip(), "appid": "true", "limit": normalized_limit},
        )
        if isinstance(data, dict) and "error" not in data:
            results = data.get("results") or []
            total = data.get("total_matches", len(results))
            if results:
                logger.info(f"[search_games] Exact AppID match for '{query}': {len(results)} result(s)")
                return {"results": results, "total_count": total}
            # No AppID match — treat as a game-name number (e.g. "007", "1984")
            logger.info(f"[search_games] AppID '{query}' not found in Hubcap — retrying as name search")

    # ── Path 2: Name-based search via the free /search endpoint ──────────────
    data = _make_json_request(
        "GET", "/search",
        params={"q": query.strip(), "limit": normalized_limit},
    )
    if isinstance(data, dict) and "error" not in data:
        results = data.get("results") or []
        total = data.get("total_matches", len(results))
        logger.info(f"[search_games] Name search for '{query}': {len(results)} result(s) (total_matches={total})")
        return {"results": results, "total_count": total}

    # ── Path 3: Fallback — paginated /library (legacy) ───────────────────────
    logger.warning(f"[search_games] /search failed for '{query}', falling back to /library")
    offset = 0
    all_games: list[Any] = []
    total_count: Optional[int] = None

    while True:
        lib_data = _make_json_request(
            "GET", "/library",
            params={
                "search": query,
                "limit": normalized_limit,
                "offset": offset,
                "sort_by": "name",
            },
        )
        if isinstance(lib_data, dict) and "error" in lib_data:
            return lib_data

        page_games: list[Any] = []
        if isinstance(lib_data, dict):
            if isinstance(lib_data.get("games"), list):
                page_games = lib_data["games"]
            elif isinstance(lib_data.get("results"), list):
                page_games = lib_data["results"]
            if total_count is None:
                try:
                    total_count = int(lib_data.get("total_count", 0))
                except (TypeError, ValueError):
                    total_count = None
        elif isinstance(lib_data, list):
            page_games = lib_data

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


def get_generate_usage() -> Dict:
    """Retrieves cloud generation API quotas and usage limits from /generate/usage."""
    logger.info("Fetching generation usage stats")
    settings = get_settings()
    res = _make_json_request("GET", "/generate/usage")
    if isinstance(res, dict) and "error" not in res and res:
        settings.setValue("last_cached_generate_usage", res)
        return res
    cached = settings.value("last_cached_generate_usage", None)
    if isinstance(cached, dict) and cached:
        logger.info("Using cached generation usage stats due to network request error")
        return cached
    return res


def get_all_hubcap_stats() -> Dict:
    """Fetches both user stats and generation usage limits."""
    user_stats = get_user_stats()
    gen_usage = get_generate_usage()
    return {
        "user_stats": user_stats,
        "gen_usage": gen_usage,
    }


def generate_single_manifest(
    depot_id: Union[str, int], manifest_id: Union[str, int]
) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Generates a single depot manifest directly from Steam via Hubcap API (/generate/manifest).
    Uses the 1,500/day single manifest quota pool.
    Returns (raw_manifest_bytes, None) on success, or (None, error_message) on failure.
    """
    headers = _get_headers()
    if not headers:
        return None, "API Key is not set. Please set it in Settings."

    url = f"{BASE_URL}/generate/manifest?depot_id={depot_id}&manifest_id={manifest_id}"
    try:
        from utils.isp_bypass import execute_hubcap_request
        response = execute_hubcap_request(
            get_session(), "GET", url, headers=headers, stream=True, timeout=60
        )
        response.raise_for_status()
        return response.content, None
    except Exception as e:
        err = _handle_request_exception(e, f"Single manifest generate (depot {depot_id}, gid {manifest_id})")
        return None, err


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


def get_selected_branch(app_id) -> str:
    """
    Returns the branch to fetch for this app:
      1. selected_branch/{app_id} — what the user picked in the UI, when set.
      2. installed_branch/{app_id} — the ACF "betakey" of the current install,
         for games the user never opened the branch selector for.
      3. "public".

    Every fetch/update path that does not receive an explicit branch must go
    through this so a game installed on a beta branch is never silently
    refreshed from the public branch.
    """
    try:
        settings = get_settings()
        if settings:
            selected = (settings.value(f"selected_branch/{app_id}", "", type=str) or "").strip()
            if selected:
                return selected
            installed = (settings.value(f"installed_branch/{app_id}", "", type=str) or "").strip()
            if installed:
                return installed
    except Exception as e:
        logger.debug(f"Could not read selected branch for {app_id}: {e}")
    return "public"


def get_manifest_zip_path(app_id, branch: str = "public") -> Path:
    """
    Returns the local cache path of the Hubcap bundle for an app/branch pair:
      hubcap_manifests/accela_fetch_{app_id}.zip                 (public)
      hubcap_manifests/accela_fetch_{app_id}_branch_{branch}.zip (any other branch)
    The hubcap_manifests directory is created if missing.
    """
    manifests_dir = Path(get_base_path()) / "hubcap_manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    if branch and branch != "public":
        return manifests_dir / f"accela_fetch_{app_id}_branch_{branch}.zip"
    return manifests_dir / f"accela_fetch_{app_id}.zip"


def download_manifest(
    app_id, branch: str = "public", force_update: bool = True
) -> Tuple[Optional[str], Optional[str]]:
    """
    Downloads a manifest zip through the ISP bypass pipeline.
    Always attempts with ?force_update=true by default to trigger a server-side
    refresh before serving. If that fails (e.g. server error), automatically falls
    back to standard download without force_update as a resilient safety net.
    Returns (filepath, None) on success, or (None, error_message) on failure.
    """
    headers = _get_headers()
    if not headers:
        return None, "API Key is not set. Please set it in Settings."

    branch = branch or "public"

    def _build_url(with_force_update: bool) -> str:
        url = f"{BASE_URL}/manifest/{app_id}"
        query_parts = []
        if branch != "public":
            query_parts.append(f"branch={branch}")
        if with_force_update:
            query_parts.append("force_update=true")
        if query_parts:
            url += "?" + "&".join(query_parts)
        return url

    save_path = get_manifest_zip_path(app_id, branch)
    manifests_dir = save_path.parent

    logger.info(f"Downloading manifest {app_id} (branch={branch}) to {save_path} (force_update={force_update})")

    # Backup previous manifest if setting is enabled and old buildid differs
    try:
        settings = get_settings()
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

    # Primary attempt (with force_update if enabled)
    from utils.isp_bypass import execute_hubcap_request
    primary_url = _build_url(force_update)
    try:
        r = execute_hubcap_request(
            get_session(), "GET", primary_url, headers=headers, stream=True, timeout=60
        )
        r.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return str(save_path), None
    except Exception as primary_err:
        logger.warning(
            f"Download manifest with force_update={force_update} failed for {app_id}: {primary_err}"
        )
        # If force_update was attempted and failed, try standard fallback without force_update
        if force_update:
            logger.info(f"Retrying download for {app_id} using standard endpoint (force_update=False)...")
            fallback_url = _build_url(False)
            try:
                r = execute_hubcap_request(
                    get_session(), "GET", fallback_url, headers=headers, stream=True, timeout=60
                )
                r.raise_for_status()
                with open(save_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                return str(save_path), None
            except Exception as fallback_err:
                if save_path.exists():
                    try:
                        os.remove(save_path)
                    except OSError:
                        pass
                error_msg = _handle_request_exception(fallback_err, f"Download {app_id} (fallback)")
                return None, error_msg

        if save_path.exists():
            try:
                os.remove(save_path)
            except OSError:
                pass
        error_msg = _handle_request_exception(primary_err, f"Download {app_id}")
        return None, error_msg


def get_manifest_status(app_id: str) -> Dict:
    """
    Calls /api/v1/status/{app_id} to check Hubcap's manifest freshness.
    Returns dict with keys: status, needs_update, update_in_progress, file_modified, error

    NOTE: this endpoint has no branch parameter — it describes the PUBLIC bundle
    only. Do not use its needs_update / file_size to judge a beta-branch bundle.
    """
    logger.info(f"Fetching manifest status for app {app_id}")
    return _make_json_request("GET", f"/status/{app_id}")


def get_manifest_contents(app_id: Union[str, int], branch: str = "public") -> Dict:
    """
    Calls /api/v1/manifest/{app_id}/contents to fetch the list of depot/manifest IDs
    currently bundled in Hubcap's ZIP for this app.

    This endpoint is FREE (zero generation quota) and fast — use it as a cheap
    pre-flight check before committing to the quota-consuming /generate/manifest call.

    Returns a dict with:
        - zip_exists (bool): True if a bundle ZIP exists for this app.
        - manifest_count (int): Number of manifests in the bundle.
        - manifests (list[dict]): Each entry has "depot_id" and "manifest_id".
        - depot_ids (set[str]): Convenience set of depot IDs Hubcap currently has.
        - error (str, optional): Present on failure.
    """
    params = {}
    if branch and branch != "public":
        params["branch"] = branch

    logger.info(f"Fetching manifest contents for app {app_id} (branch={branch})")
    data = _make_json_request("GET", f"/manifest/{app_id}/contents", params=params or None)

    if isinstance(data, dict) and "error" not in data:
        # Build a convenience set of depot_ids for O(1) membership checks
        manifests = data.get("manifests") or []
        data["depot_ids"] = {str(m.get("depot_id", "")) for m in manifests if m.get("depot_id")}

    return data
