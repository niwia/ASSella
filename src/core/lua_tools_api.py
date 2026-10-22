"""
lua_tools_api.py - Integration with lua.tools API and Supabase authentication.
Provides game listings, fix listings, PKCE automated browser login, code redemption,
and presigned fix download resolution.
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.helpers import get_base_path
from utils.safe_paths import safe_path_component

logger = logging.getLogger(__name__)

API_BASE_URL = "https://lua.tools"
SUPABASE_URL = "https://db.lua.tools"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpYXQiOjE3NzYwMzkzNzYsImV4cCI6MTg5MzQ1NjAwMCwicm9sZSI6ImFub24iLCJpc3MiOiJzdXBhYmFzZSJ9."
    "f_-K38u3odjltP-g_67FVmG32Vg-_-k-lNBvIaVUVBM"
)
OAUTH_CALLBACK = "http://localhost:53789/callback"
OAUTH_PORT = 53789
OAUTH_TIMEOUT = 300  # 5 minutes


FIXES_CACHE_TTL = 7 * 86400  # 7 days in seconds

# Cached fix archives are re-fetched on the same schedule as the fix listings,
# so a fix that was updated in place by its author is picked up eventually.
FIX_ZIP_CACHE_TTL = FIXES_CACHE_TTL


def _get_session_file() -> Path:
    p = get_base_path() / "luatools" / "session.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _get_fixes_cache_dir() -> Path:
    p = get_base_path() / "luatools" / "fixes"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get_fixes_meta_cache_file() -> Path:
    p = get_base_path() / "luatools" / "fixes_meta_cache.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


class LuaToolsAPI:
    """Core client for lua.tools fixes API and Supabase authentication."""

    _pkce_lock = threading.Lock()
    _active_pkce: Optional[Dict[str, Any]] = None
    _fixes_mem_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
    _cache_lock = threading.Lock()

    # --------------------------------------------------------------------------
    # Cache Helpers
    # --------------------------------------------------------------------------

    @classmethod
    def _load_disk_cache(cls) -> Dict[str, Dict[str, Any]]:
        cache_file = _get_fixes_meta_cache_file()
        if not cache_file.exists():
            return {}
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.debug(f"[LuaTools] Error reading fixes meta cache: {e}")
        return {}

    @classmethod
    def _save_disk_cache(cls, cache_data: Dict[str, Dict[str, Any]]) -> None:
        cache_file = _get_fixes_meta_cache_file()
        try:
            temp_file = cache_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2)
            temp_file.replace(cache_file)
        except Exception as e:
            logger.warning(f"[LuaTools] Failed to save fixes meta cache: {e}")

    # --------------------------------------------------------------------------
    # Public Endpoints (No Auth Required)
    # --------------------------------------------------------------------------

    @classmethod
    def is_unlocked(cls) -> bool:
        """Check if LuaTools feature has been unlocked via the secret search keyword."""
        try:
            from utils.settings import get_settings
            s = get_settings()
            return s.value("luatools_unlocked", False, type=bool)
        except Exception:
            return False

    @classmethod
    def list_games(cls) -> Dict[str, Any]:
        """Fetch the full catalogue of games with available fixes (1800+ games)."""
        if not cls.is_unlocked():
            return {"success": False, "error": "LuaTools feature is not unlocked.", "games": []}
        url = f"{API_BASE_URL}/api/denuvo/listings"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ASSella/3.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {"success": True, "games": data.get("games", []), "tags": data.get("tags", [])}
        except Exception as e:
            logger.warning(f"[LuaTools] Failed to list games: {e}")
            return {"success": False, "error": str(e), "games": []}

    @classmethod
    def get_game_fixes(cls, appid: str | int, force_refresh: bool = False) -> Dict[str, Any]:
        """Fetch all available fixes, descriptions, and tags for a specific AppID.

        Uses a 7-day persistent disk cache to prevent rate-limiting and redundant network requests.
        Pass force_refresh=True to bypass cache and fetch fresh data from lua.tools.
        """
        appid_str = str(appid)
        if not cls.is_unlocked():
            return {"success": False, "error": "LuaTools feature is not unlocked.", "appid": appid_str, "fixes": []}
        now = time.time()

        if not force_refresh:
            # 1. Check in-memory cache
            with cls._cache_lock:
                if appid_str in cls._fixes_mem_cache:
                    ts, data = cls._fixes_mem_cache[appid_str]
                    if (now - ts) < FIXES_CACHE_TTL:
                        logger.debug(f"[LuaTools] In-memory cache hit for {appid_str}")
                        return dict(data)

            # 2. Check disk cache
            disk_cache = cls._load_disk_cache()
            if appid_str in disk_cache:
                entry = disk_cache[appid_str]
                ts = entry.get("timestamp", 0)
                data = entry.get("data")
                if isinstance(data, dict) and (now - ts) < FIXES_CACHE_TTL:
                    with cls._cache_lock:
                        cls._fixes_mem_cache[appid_str] = (ts, data)
                    logger.debug(f"[LuaTools] Disk cache hit for {appid_str}")
                    return dict(data)

        # 3. Perform network request
        url = f"{API_BASE_URL}/api/denuvo/fixes?appid={appid_str}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ASSella/3.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                res = {
                    "success": True,
                    "appid": appid_str,
                    "name": data.get("name", f"App {appid_str}"),
                    "header_image": data.get("header_image", ""),
                    "fixes": data.get("fixes", []),
                }
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # AppID has no fixes on lua.tools (cache 404s too so we don't query repeatedly)
                res = {
                    "success": True,
                    "appid": appid_str,
                    "name": f"App {appid_str}",
                    "header_image": "",
                    "fixes": [],
                }
            else:
                logger.warning(f"[LuaTools] Failed to get fixes for {appid_str}: {e}")
                # Fallback to cached data if available
                disk_cache = cls._load_disk_cache()
                if appid_str in disk_cache and isinstance(disk_cache[appid_str].get("data"), dict):
                    logger.info(f"[LuaTools] Returning stale cached data for {appid_str} after HTTP {e.code}")
                    return dict(disk_cache[appid_str]["data"])
                return {"success": False, "error": str(e), "appid": appid_str, "fixes": []}
        except Exception as e:
            logger.warning(f"[LuaTools] Failed to get fixes for {appid_str}: {e}")
            disk_cache = cls._load_disk_cache()
            if appid_str in disk_cache and isinstance(disk_cache[appid_str].get("data"), dict):
                logger.info(f"[LuaTools] Returning stale cached data for {appid_str} after network failure")
                return dict(disk_cache[appid_str]["data"])
            return {"success": False, "error": str(e), "appid": appid_str, "fixes": []}

        # Cache successful response (including 404-empty-fixes)
        with cls._cache_lock:
            cls._fixes_mem_cache[appid_str] = (now, res)
            disk_cache = cls._load_disk_cache()
            disk_cache[appid_str] = {"timestamp": now, "data": res}
            cls._save_disk_cache(disk_cache)

        return dict(res)

    # --------------------------------------------------------------------------
    # Session & Authentication Storage
    # --------------------------------------------------------------------------

    @classmethod
    def load_session(cls) -> Optional[Dict[str, Any]]:
        file_path = _get_session_file()
        if not file_path.exists():
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                session = json.load(f)
            if isinstance(session, dict) and session.get("refresh_token"):
                return session
        except Exception as e:
            logger.warning(f"[LuaTools] Error reading session file: {e}")
        return None

    @classmethod
    def save_session(cls, session: Dict[str, Any]) -> bool:
        file_path = _get_session_file()
        try:
            temp_file = file_path.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(session, f, indent=2)
            os.chmod(temp_file, 0o600)
            temp_file.replace(file_path)
            os.chmod(file_path, 0o600)
            return True
        except Exception as e:
            logger.error(f"[LuaTools] Failed to save session: {e}")
            return False

    @classmethod
    def clear_session(cls) -> bool:
        cls.cancel_pkce()
        file_path = _get_session_file()
        if file_path.exists():
            try:
                file_path.unlink()
                return True
            except Exception as e:
                logger.error(f"[LuaTools] Failed to remove session file: {e}")
                return False
        return True

    @classmethod
    def is_authenticated(cls, allow_cached: bool = True) -> bool:
        """Check if user has an authenticated lua.tools session.

        Requires that LuaTools is unlocked first. Uses local cached session by default
        to prevent blocking network requests on UI threads.
        """
        if not cls.is_unlocked():
            return False
        session = cls.load_session()
        if not session or not session.get("refresh_token"):
            return False
        if allow_cached:
            return True
        return cls.get_valid_access_token() is not None

    @classmethod
    def get_auth_status(cls, allow_cached: bool = True) -> Dict[str, Any]:
        """Check if a valid session exists and return account display info.

        Requires that LuaTools is unlocked first. Defaults to reading the local
        cached session without making blocking network calls.
        """
        if not cls.is_unlocked():
            return {"authenticated": False}

        session = cls.load_session()
        if not session or not session.get("refresh_token"):
            return {"authenticated": False}

        if not allow_cached:
            token = cls.get_valid_access_token()
            if not token:
                return {"authenticated": False}
            session = cls.load_session() or {}

        user = session.get("user", {})
        meta = user.get("user_metadata", {})
        claims = meta.get("custom_claims", {})
        display_name = claims.get("global_name") or meta.get("full_name") or meta.get("name") or user.get("email") or "lua.tools account"
        avatar_url = meta.get("avatar_url", "")
        return {
            "authenticated": True,
            "display_name": display_name,
            "avatar_url": avatar_url,
        }

    @classmethod
    def get_valid_access_token(cls) -> Optional[str]:
        """Return a valid access token, auto-refreshing via Supabase if expired."""
        if not cls.is_unlocked():
            return None
        session = cls.load_session()
        if not session or not session.get("refresh_token"):
            return None

        now = int(time.time())
        expires_at = session.get("expires_at", 0)

        # Still valid for at least 60 seconds
        if session.get("access_token") and expires_at > now + 60:
            return session["access_token"]

        # Token expired or about to expire -> refresh using refresh_token
        refresh_token = session.get("refresh_token")
        logger.info("[LuaTools] Access token expired, refreshing via Supabase...")
        try:
            req_data = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
            req = urllib.request.Request(
                f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "apikey": SUPABASE_ANON_KEY,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                new_session = json.loads(resp.read().decode("utf-8"))
                if not new_session.get("access_token"):
                    logger.warning("[LuaTools] Refresh response missing access_token")
                    return None

                expires_in = int(new_session.get("expires_in", 3600))
                new_session["expires_at"] = now + expires_in
                if not new_session.get("refresh_token"):
                    new_session["refresh_token"] = refresh_token
                if not new_session.get("user"):
                    new_session["user"] = session.get("user", {})

                cls.save_session(new_session)
                return new_session["access_token"]
        except urllib.error.HTTPError as e:
            logger.warning(f"[LuaTools] Session refresh HTTP error {e.code}: {e}")
            if e.code in (400, 401):
                cls.clear_session()
            return None
        except Exception as e:
            logger.error(f"[LuaTools] Session refresh failed: {e}")
            return None

    # --------------------------------------------------------------------------
    # Login Method 1: 6-Character Redeem Code
    # --------------------------------------------------------------------------

    @classmethod
    def sign_in_with_code(cls, code: str) -> Tuple[bool, str]:
        """Redeem a 6-character code from lua.tools."""
        if not cls.is_unlocked():
            return False, "LuaTools feature is not unlocked."
        code = code.strip().upper()
        if len(code) != 6:
            return False, "Login code must be exactly 6 characters."

        # 1. Redeem on lua.tools
        try:
            redeem_data = json.dumps({"code": code}).encode("utf-8")
            req = urllib.request.Request(
                f"{API_BASE_URL}/api/auth/code/redeem",
                data=redeem_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                redeem_res = json.loads(resp.read().decode("utf-8"))
                token_hash = redeem_res.get("token")
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return False, "Invalid login code."
            if e.code == 410:
                return False, "Login code has expired or was already used."
            return False, f"Code redemption failed (HTTP {e.code})."
        except Exception as e:
            return False, f"Connection error: {e}"

        if not token_hash:
            return False, "Invalid response from lua.tools."

        # 2. Verify with Supabase magiclink
        try:
            verify_data = json.dumps({"type": "magiclink", "token_hash": token_hash}).encode("utf-8")
            vreq = urllib.request.Request(
                f"{SUPABASE_URL}/auth/v1/verify",
                data=verify_data,
                headers={
                    "Content-Type": "application/json",
                    "apikey": SUPABASE_ANON_KEY,
                },
                method="POST",
            )
            with urllib.request.urlopen(vreq, timeout=15) as vresp:
                session = json.loads(vresp.read().decode("utf-8"))
        except Exception as e:
            return False, f"Supabase verification failed: {e}"

        if "session" in session and isinstance(session["session"], dict):
            user = session.get("user")
            session = session["session"]
            if user and not session.get("user"):
                session["user"] = user

        if not session.get("access_token") or not session.get("refresh_token"):
            return False, "Supabase returned an incomplete session."

        now = int(time.time())
        session["expires_at"] = now + int(session.get("expires_in", 3600))
        if cls.save_session(session):
            return True, "Successfully signed in to lua.tools!"
        return False, "Failed to save session to disk."

    @staticmethod
    def filter_fixes(fixes: List[Dict[str, Any]], ignore_online: bool = False) -> List[Dict[str, Any]]:
        """Filter out online fixes if ignore_online is True."""
        if not ignore_online:
            return list(fixes)
        filtered = []
        for fix in fixes:
            tags = fix.get("tags") or []
            is_online = any(
                t.get("slug") == "online-fix" or "online fix" in (t.get("name") or "").lower()
                for t in tags
            )
            if str(fix.get("id", "")).startswith("online-fix:"):
                is_online = True
            if not is_online:
                filtered.append(fix)
        return filtered

    # --------------------------------------------------------------------------
    # Login Method 2: 1-Click Automated PKCE Browser Login
    # --------------------------------------------------------------------------

    @classmethod
    def start_pkce_login(cls) -> Tuple[bool, str]:
        """Start a local HTTP server on port 53789 and return the authorization URL."""
        if not cls.is_unlocked():
            return False, "LuaTools feature is not unlocked."
        with cls._pkce_lock:
            cls.cancel_pkce()

            try:
                server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server_socket.bind(("127.0.0.1", OAUTH_PORT))
                server_socket.listen(1)
                server_socket.settimeout(0.5)
            except Exception as e:
                logger.error(f"[LuaTools] Failed to bind PKCE listener on port {OAUTH_PORT}: {e}")
                return False, f"Could not start local login listener: {e}"

            # Generate PKCE verifier and challenge
            verifier = _base64url(secrets.token_bytes(64))
            challenge = _base64url(hashlib.sha256(verifier.encode("utf-8")).digest())

            auth_url = (
                f"{SUPABASE_URL}/auth/v1/authorize?provider=discord"
                f"&redirect_to={urllib.parse.quote(OAUTH_CALLBACK)}"
                f"&code_challenge={urllib.parse.quote(challenge)}"
                f"&code_challenge_method=s256"
            )

            cls._active_pkce = {
                "server": server_socket,
                "verifier": verifier,
                "deadline": time.time() + OAUTH_TIMEOUT,
                "thread": None,
                "status": "waiting",
                "result": None,
            }

            return True, auth_url

    @classmethod
    def poll_pkce_login(cls) -> Dict[str, Any]:
        """Poll the active PKCE listener for incoming OAuth callbacks."""
        with cls._pkce_lock:
            if not cls._active_pkce:
                return {"status": "idle"}

            if time.time() > cls._active_pkce["deadline"]:
                cls.cancel_pkce()
                return {"status": "timeout", "error": "Login timed out after 5 minutes."}

            server: socket.socket = cls._active_pkce["server"]
            try:
                client, _ = server.accept()
            except socket.timeout:
                return {"status": "waiting"}
            except Exception as e:
                cls.cancel_pkce()
                return {"status": "error", "error": f"Listener error: {e}"}

            # Read the HTTP GET request
            try:
                client.settimeout(2.0)
                raw_request = client.recv(4096).decode("utf-8", errors="ignore")
                
                # Send responsive HTML to browser
                response_html = (
                    "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n"
                    "<!doctype html><html><body style='font-family:system-ui,-apple-system,sans-serif;"
                    "text-align:center;padding:60px;background:#12121a;color:#fff'>"
                    "<h1 style='color:#3b82f6;'>Signed in to lua.tools!</h1>"
                    "<p style='color:#9ca3af;'>You can close this tab and return to ASSella.</p>"
                    "<script>setTimeout(function(){ window.close(); }, 1200);</script>"
                    "</body></html>"
                )
                client.sendall(response_html.encode("utf-8"))
                client.close()
            except Exception as e:
                logger.warning(f"[LuaTools] Error handling PKCE HTTP response: {e}")

            # Extract auth code from request
            # GET /callback?code=XXXX HTTP/1.1
            first_line = raw_request.splitlines()[0] if raw_request else ""
            parsed = urllib.parse.urlparse(first_line.split()[1] if len(first_line.split()) > 1 else "")
            params = urllib.parse.parse_qs(parsed.query)

            code_list = params.get("code")
            if not code_list or not code_list[0]:
                err = params.get("error_description", ["Discord did not return a valid login code."])[0]
                cls.cancel_pkce()
                return {"status": "error", "error": err}

            code = code_list[0]
            verifier = cls._active_pkce["verifier"]

        # Exchange code for tokens outside lock
        try:
            exchange_data = json.dumps({"auth_code": code, "code_verifier": verifier}).encode("utf-8")
            req = urllib.request.Request(
                f"{SUPABASE_URL}/auth/v1/token?grant_type=pkce",
                data=exchange_data,
                headers={
                    "Content-Type": "application/json",
                    "apikey": SUPABASE_ANON_KEY,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                session = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            cls.cancel_pkce()
            return {"status": "error", "error": f"Token exchange failed: {e}"}

        if not session.get("access_token") or not session.get("refresh_token"):
            cls.cancel_pkce()
            return {"status": "error", "error": "Invalid token payload received."}

        now = int(time.time())
        session["expires_at"] = now + int(session.get("expires_in", 3600))
        cls.save_session(session)
        cls.cancel_pkce()
        return {"status": "success", "session": session}

    @classmethod
    def cancel_pkce(cls) -> None:
        if cls._active_pkce and cls._active_pkce.get("server"):
            try:
                cls._active_pkce["server"].close()
            except Exception:
                pass
        cls._active_pkce = None

    # --------------------------------------------------------------------------
    # Download Resolution & Fetching
    # --------------------------------------------------------------------------

    @classmethod
    def resolve_fix_download(cls, fix_id: str, slot: str = "fix") -> Tuple[Optional[str], Optional[str]]:
        """Resolve signed download URL from lua.tools."""
        if not cls.is_unlocked():
            return None, "LuaTools feature is not unlocked."
        token = cls.get_valid_access_token()
        if not token:
            return None, "Not authenticated with lua.tools. Please connect in Settings -> LuaTools."

        url = f"{API_BASE_URL}/api/denuvo/download?fix={urllib.parse.quote(fix_id)}&slot={urllib.parse.quote(slot)}"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "ASSella/3.0",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                download_url = payload.get("url")
                if download_url and download_url.startswith("https://"):
                    return download_url, None
                return None, "lua.tools returned an invalid download URL."
        except urllib.error.HTTPError as e:
            if e.code == 401:
                cls.clear_session()
                return None, "lua.tools session expired. Please re-authenticate in Settings."
            return None, f"Failed to resolve download link (HTTP {e.code})."
        except Exception as e:
            return None, f"Network error: {e}"

    @classmethod
    def _is_valid_fix_archive(cls, path: Path) -> bool:
        """Return True when the file is a readable zip holding at least one file."""
        try:
            with zipfile.ZipFile(path) as zf:
                return any(not info.is_dir() for info in zf.infolist())
        except (OSError, zipfile.BadZipFile) as e:
            logger.debug(f"[LuaTools] Invalid fix archive {path}: {e}")
            return False

    @classmethod
    def _cached_fix_zip(cls, path: Path) -> bool:
        """Return True when the cached archive exists, is fresh and is readable."""
        try:
            if not path.is_file():
                return False
            age = time.time() - path.stat().st_mtime
            if age > FIX_ZIP_CACHE_TTL:
                logger.info(f"[LuaTools] Cached fix archive expired ({int(age)}s old): {path}")
                return False
            if not cls._is_valid_fix_archive(path):
                logger.warning(f"[LuaTools] Cached fix archive is corrupt, refetching: {path}")
                return False
            return True
        except OSError as e:
            logger.debug(f"[LuaTools] Could not inspect cached archive {path}: {e}")
            return False

    @classmethod
    def download_fix_zip(
        cls,
        fix_id: str,
        appid: str | int,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[Optional[Path], Optional[str]]:
        """Resolve and download the fix zip to local cache (~/.local/share/ACCELA/luatools/fixes/)."""
        # The fix id arrives from the API, so it must not be able to steer the
        # cache file out of the cache directory.
        stem = (
            f"{safe_path_component(appid, fallback_prefix='app')}"
            f"_{safe_path_component(fix_id, fallback_prefix='fix')}"
        )
        target_file = _get_fixes_cache_dir() / f"{stem}.zip"

        if cls._cached_fix_zip(target_file):
            logger.info(f"[LuaTools] Found cached fix archive: {target_file}")
            return target_file, None

        download_url, err = cls.resolve_fix_download(fix_id, slot="fix")
        if not download_url:
            return None, err or "Download resolution failed."

        # A unique part name keeps two concurrent downloads of the same fix
        # from writing over each other.
        temp_target = target_file.with_suffix(f".{secrets.token_hex(4)}.part")
        try:
            req = urllib.request.Request(download_url, headers={"User-Agent": "ASSella/3.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                total_size = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 64 * 1024
                with open(temp_target, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb and total_size > 0:
                            progress_cb(downloaded, total_size)

            if total_size > 0 and downloaded != total_size:
                raise IOError(f"Incomplete download: got {downloaded} of {total_size} bytes")

            if not cls._is_valid_fix_archive(temp_target):
                raise IOError("Downloaded file is not a valid fix archive")

            temp_target.replace(target_file)
            return target_file, None
        except Exception as e:
            if temp_target.exists():
                temp_target.unlink()
            return None, f"Download failed: {e}"
