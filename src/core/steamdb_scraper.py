"""
SteamDB Scraper & Rollback Metadata Retriever for ACCELA.

Features:
- Byparr integration for automated Cloudflare Turnstile bypass (using stealth Camoufox).
- Fast headless DOM resolution for dynamic SteamDB patchnotes tables.
- Robust HTML parsers for:
    * App patchnotes overview (/app/<appid>/patchnotes/) -> Build IDs, dates, titles
    * Patch detail pages (/patchnotes/<buildid>/) -> Depot IDs, Manifest IDs
"""

import atexit
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import logging
import concurrent.futures
import email.utils
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import requests
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

from utils.settings import get_settings
from utils.helpers import get_base_path

logger = logging.getLogger(__name__)

BYPARR_DEFAULT_PORT = 8191
BYPARR_DEFAULT_URL = f"http://127.0.0.1:{BYPARR_DEFAULT_PORT}"
STEAMDB_BASE_URL = "https://steamdb.info"

# Fast-path race profiles for Cloudflare bypass via direct TLS impersonation
FAST_RACE_PROFILES = [
    {
        "name": "Chrome 152 (Windows)",
        "impersonate": "chrome152",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="152", "Not(A:Brand";v="24", "Google Chrome";v="152"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        },
    },
    {
        "name": "Chrome 152 (macOS)",
        "impersonate": "chrome152_macos",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="152", "Not(A:Brand";v="24", "Google Chrome";v="152"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
        },
    },
    {
        "name": "Safari 17.0 (macOS)",
        "impersonate": "safari17_0",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    },
    {
        "name": "Safari 270 (macOS)",
        "impersonate": "safari270",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0 Safari/605.1.15",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    },
    {
        "name": "Safari 180 (iOS)",
        "impersonate": "safari180_ios",
        "headers": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    },
]


class ByparrManager:
    """Manages the background Byparr helper process for Cloudflare clearance.

    Strictly bound to ACCELA's lifecycle: starts when ACCELA needs it,
    and terminates immediately (with all child browsers) when ACCELA closes or crashes.
    """

    _process: Optional[subprocess.Popen] = None
    _managed: bool = False

    @classmethod
    def get_service_url(cls) -> str:
        try:
            settings = get_settings()
            url = settings.value("byparr_url", BYPARR_DEFAULT_URL, type=str).rstrip("/")
            return url or BYPARR_DEFAULT_URL
        except Exception:
            return BYPARR_DEFAULT_URL

    @classmethod
    def get_auth_header(cls) -> str:
        try:
            settings = get_settings()
            return settings.value("byparr_auth_header", "", type=str).strip()
        except Exception:
            return ""

    @classmethod
    def is_custom_service(cls) -> bool:
        try:
            settings = get_settings()
            url = settings.value("byparr_url", "", type=str).strip()
            return bool(url and url.rstrip("/") != BYPARR_DEFAULT_URL.rstrip("/"))
        except Exception:
            return False

    @classmethod
    def is_local_enabled(cls) -> bool:
        """True if local byparr exists and has not been explicitly disabled by user."""
        if cls.is_custom_service():
            return False
        if not cls.find_byparr_dir():
            return False
        try:
            return get_settings().value("byparr_enabled", True, type=bool)
        except Exception:
            return True

    @classmethod
    def is_available(cls) -> bool:
        """True if custom byparr URL is configured, or local byparr is installed and enabled."""
        if cls.is_custom_service():
            return True
        return cls.is_local_enabled()

    @classmethod
    def is_running(cls) -> bool:
        """Quickly checks if Byparr is listening on the configured port or endpoint."""
        if cls.is_custom_service():
            try:
                import urllib.parse
                url = cls.get_service_url()
                parsed = urllib.parse.urlparse(url)
                host = parsed.hostname or "127.0.0.1"
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.7)
                res = s.connect_ex((host, port))
                s.close()
                return res == 0
            except Exception:
                return False

        try:
            port = BYPARR_DEFAULT_PORT
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            res = s.connect_ex(("127.0.0.1", port))
            s.close()
            return res == 0
        except Exception:
            return False

    @classmethod
    def find_uv_path(cls) -> Optional[str]:
        """Locates the uv executable in PATH or standard user directories."""
        uv = shutil.which("uv")
        if uv:
            return uv
        candidates = [
            Path.home() / ".local" / "bin" / "uv",
            Path.home() / ".cargo" / "bin" / "uv",
        ]
        for c in candidates:
            if c.exists() and os.access(c, os.X_OK):
                return str(c)
        return None

    @classmethod
    def find_byparr_dir(cls) -> Optional[Path]:
        """Locates the Byparr installation directory."""
        candidates = [
            Path.home() / ".local" / "share" / "ACCELA" / "byparr",
            get_base_path() / "byparr",
            Path("/tmp/byparr_test"),
            Path.home() / "Byparr",
        ]
        for c in candidates:
            if c.exists() and (c / "main.py").exists():
                return c
        return None

    @classmethod
    def _rotate_byparr_log(cls, log_path: Path, max_sessions: int = 5) -> None:
        """Limits byparr.log to the last `max_sessions` runs, pruning older sessions."""
        try:
            if not log_path.exists() or log_path.stat().st_size == 0:
                return

            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            pattern = r"(?=(?:\r?\n|^)---\s*Byparr Session Started by\b)"
            sessions = [s for s in re.split(pattern, content) if s.strip()]

            # Keep at most (max_sessions - 1) previous sessions so the new one brings total to max_sessions
            keep_count = max(1, max_sessions - 1)
            if len(sessions) >= max_sessions:
                trimmed_content = "\n".join(s.strip() for s in sessions[-keep_count:]) + "\n"
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(trimmed_content)
                logger.debug(f"[SteamDB] Pruned byparr.log from {len(sessions)} to {keep_count} sessions.")
        except Exception as e:
            logger.debug(f"[SteamDB] Error rotating byparr.log: {e}")

    @classmethod
    def ensure_running(cls) -> bool:
        """Ensures Byparr is listening; launches background process if local repo exists."""
        if get_settings().value("disable_steamdb_scraping", False, type=bool):
            logger.info("[SteamDB] Scraping disabled in Testing settings; will not start Byparr.")
            return False

        if cls.is_custom_service():
            return cls.is_running()

        if not cls.is_local_enabled():
            logger.info("[SteamDB] Local Byparr is disabled by user.")
            return False

        if cls.is_running():
            return True

        byparr_dir = cls.find_byparr_dir()
        if not byparr_dir:
            logger.warning("[SteamDB] Byparr service not running and local repository not found.")
            return False

        try:
            env = os.environ.copy()
            # Clean AppImage library overrides so uv and Python 3.14 run clean
            env.pop("LD_LIBRARY_PATH", None)
            env.pop("LD_PRELOAD", None)

            # Ensure PATH contains standard user bin dirs
            local_bin = str(Path.home() / ".local" / "bin")
            cargo_bin = str(Path.home() / ".cargo" / "bin")
            env["PATH"] = f"{local_bin}:{cargo_bin}:{env.get('PATH', '')}"

            env["INVPW_TRUE_HEADLESS"] = "1"
            env["PORT"] = str(BYPARR_DEFAULT_PORT)
            env["HOST"] = "127.0.0.1"
            # Pass ASSELLA PID so Byparr supervisor can self-terminate if ASSELLA crashes
            env["ASSELLA_PID"] = str(os.getpid())
            env["ACCELA_PID"] = str(os.getpid())

            uv_path = cls.find_uv_path()
            runner_script = byparr_dir / "assella_runner.py"
            if not runner_script.exists():
                runner_script = byparr_dir / "accela_runner.py"
            target_script = runner_script.name if runner_script.exists() else "main.py"

            if uv_path and (byparr_dir / "uv.lock").exists():
                cmd = [uv_path, "run", "python", target_script]
            else:
                cmd = ["python3", target_script]

            # Set Linux kernel PR_SET_PDEATHSIG so the kernel terminates Byparr if ASSELLA dies.
            # The helper also starts at a lower priority: it spins up a headless
            # Chromium, and that warm-up must not take CPU away from the window.
            def _preexec():
                try:
                    import ctypes
                    libc = ctypes.CDLL("libc.so.6")
                    libc.prctl(1, signal.SIGTERM)  # 1 = PR_SET_PDEATHSIG
                except Exception:
                    pass
                try:
                    os.setpriority(os.PRIO_PROCESS, 0, 10)
                except OSError as e:
                    logger.debug(f"[SteamDB] Could not lower the helper priority: {e}")

            logger.info(f"[SteamDB] Starting background Byparr process in {byparr_dir} (ASSELLA PID={os.getpid()})...")
            log_dir = get_base_path() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            byparr_log_path = log_dir / "byparr.log"
            cls._rotate_byparr_log(byparr_log_path, max_sessions=5)
            byparr_logfile = open(byparr_log_path, "a", encoding="utf-8")
            byparr_logfile.write(
                f"\n--- Byparr Session Started by ASSELLA (PID={os.getpid()}) at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n"
            )
            byparr_logfile.flush()

            cls._process = subprocess.Popen(
                cmd,
                cwd=str(byparr_dir),
                env=env,
                stdout=byparr_logfile,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                preexec_fn=_preexec if sys.platform != "win32" else None,
            )
            try:
                byparr_logfile.close()
            except Exception as e:
                logger.debug(f"[SteamDB] Could not close byparr logfile handle in parent: {e}")
            cls._managed = True

            # Wait up to 10 seconds for service readiness
            for _ in range(20):
                time.sleep(0.5)
                if cls.is_running():
                    logger.info("[SteamDB] Byparr helper process is now running and responsive.")
                    return True

            logger.warning("[SteamDB] Byparr process launched but socket connect timed out.")
            return False
        except Exception as e:
            logger.error(f"[SteamDB] Failed to launch Byparr process: {e}")
            return False

    @classmethod
    def stop(cls):
        """Stops the managed Byparr process and all its child browsers."""
        if cls._process and cls._managed:
            proc = cls._process
            cls._process = None
            cls._managed = False
            logger.info("[SteamDB] Terminating Byparr helper process tree...")
            try:
                if sys.platform != "win32":
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(pgid, signal.SIGKILL)
                else:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                logger.info("[SteamDB] Byparr helper process successfully stopped.")
            except Exception as e:
                logger.debug(f"[SteamDB] Error stopping Byparr process: {e}")

        # Ensure any lingering Byparr process from the ACCELA directory is cleaned up
        byparr_dir = cls.find_byparr_dir()
        if byparr_dir and cls.is_running():
            try:
                subprocess.run(["pkill", "-f", str(byparr_dir)], capture_output=True, timeout=2)
            except Exception:
                pass

    @classmethod
    def build_headers(cls, auth_header: Optional[str] = None) -> Dict[str, str]:
        auth = auth_header if auth_header is not None else cls.get_auth_header()
        headers = {"Content-Type": "application/json"}
        if auth:
            if ":" in auth:
                k, v = auth.split(":", 1)
                headers[k.strip()] = v.strip()
            elif auth.lower().startswith("bearer "):
                headers["Authorization"] = auth.strip()
            else:
                headers["Authorization"] = f"Bearer {auth.strip()}"
        return headers

    @classmethod
    def test_connection(cls, service_url: str, auth_header: str = "", timeout_seconds: int = 15) -> Tuple[bool, str, float]:
        """Test connection to Byparr or custom Cloudflare solver instance.
        Sends a test Turnstile bypass request for steamdb.info.
        Returns (success, message, latency_seconds).
        """
        url = (service_url or "").strip().rstrip("/")
        if not url:
            return False, "Service URL cannot be empty.", 0.0
        if not (url.startswith("http://") or url.startswith("https://")):
            url = f"http://{url}"

        endpoint = f"{url}/v1"
        headers = cls.build_headers(auth_header)
        payload = {
            "cmd": "request.get",
            "url": "https://steamdb.info/",
            "maxTimeout": timeout_seconds * 1000,
            "returnOnlyCookies": True,
        }

        t0 = time.time()
        try:
            r = requests.post(endpoint, json=payload, headers=headers, timeout=timeout_seconds + 5)
            elapsed = time.time() - t0
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    return False, "Server replied 200 OK, but response was not valid JSON.", elapsed

                if data.get("status") == "ok":
                    sol = data.get("solution", {})
                    sol_status = sol.get("status")
                    if sol_status == 200:
                        return True, f"Verified! SteamDB Turnstile solved successfully in {elapsed:.2f}s.", elapsed
                    return False, f"Solver answered but SteamDB returned status {sol_status}.", elapsed
                return False, f"Solver error: {data.get('message', 'Unknown solver status')}", elapsed
            return False, f"Server returned HTTP {r.status_code}: {r.text[:120]}", elapsed
        except requests.exceptions.Timeout:
            return False, f"Connection timed out after {timeout_seconds} seconds.", time.time() - t0
        except requests.exceptions.ConnectionError as ce:
            return False, f"Failed to connect to {url}: {ce}", time.time() - t0
        except Exception as exc:
            return False, f"Request failed: {exc}", time.time() - t0

    @classmethod
    def verify_steamdb_bypass(cls, timeout_seconds: int = 30) -> bool:
        """
        Actively verifies that Byparr can bypass Cloudflare Turnstile and reach SteamDB.
        Performs a POST to /v1 requesting https://steamdb.info/ with returnOnlyCookies=True.
        Caches cookies (cf_clearance) and userAgent on success.
        Returns True if Cloudflare was bypassed and SteamDB responded with 200 OK.
        """
        if not cls.ensure_running():
            return False

        endpoint = f"{cls.get_service_url()}/v1"
        payload = {
            "cmd": "request.get",
            "url": "https://steamdb.info/",
            "maxTimeout": timeout_seconds * 1000 if timeout_seconds < 1000 else timeout_seconds,
            "returnOnlyCookies": True,
        }
        headers = cls.build_headers()
        try:
            r = requests.post(endpoint, json=payload, headers=headers, timeout=timeout_seconds + 10)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "ok":
                    sol = data.get("solution", {})
                    if sol.get("status") == 200:
                        cookies = {c["name"]: c["value"] for c in sol.get("cookies", [])}
                        ua = sol.get("userAgent", "")
                        cf_val = cookies.get("cf_clearance")
                        if cf_val:
                            s = get_settings()
                            s.setValue("steamdb/user_agent", ua)
                            s.setValue("steamdb/cf_clearance", cf_val)
                            s.setValue("steamdb/clearance_timestamp", int(time.time()))
                        logger.info("[SteamDB] Cloudflare Turnstile bypass confirmed working via Byparr.")
                        return True
            logger.warning(f"[SteamDB] SteamDB verification failed (status {r.status_code}): {r.text[:150]}")
        except Exception as e:
            logger.debug(f"[SteamDB] Failed to verify SteamDB bypass via Byparr: {e}")
        return False


# Register clean process termination on normal or unexpected Python exit
atexit.register(ByparrManager.stop)

# Public alias for connection test
test_byparr_connection = ByparrManager.test_connection


def verify_steamdb_status(timeout_seconds: int = 5) -> Tuple[bool, str]:
    """
    Checks SteamDB accessibility.
    First tests direct Fast-Path racing (<1s). If fast-path passes, returns (True, "Fast-Path (Direct TLS)").
    If fast-path fails and Byparr is configured, verifies Byparr.
    Returns (True/False, provider_name).
    """
    if cffi_requests:
        try:
            scraper = SteamDBScraper()
            html = scraper.fetch_via_fast_race(f"{STEAMDB_BASE_URL}/", timeout_seconds=min(3.5, float(timeout_seconds)))
            if html and len(html) > 500:
                logger.info("[SteamDB] SteamDB status verified active via Fast-Path.")
                return True, "Fast-Path (Direct TLS)"
        except Exception as e:
            logger.debug(f"[SteamDB] Fast-path status check error: {e}")

    if ByparrManager.is_available():
        ok = ByparrManager.verify_steamdb_bypass(timeout_seconds=timeout_seconds)
        if ok:
            return True, "Byparr"

    return False, "Offline"


class SteamDBScraper:
    """Scrapes SteamDB for patchnotes and depot manifest IDs."""

    def __init__(self, solver_url: Optional[str] = None):
        self.solver_url = solver_url or ByparrManager.get_service_url()

    def fetch_via_fast_race(self, url: str, timeout_seconds: float = 4.0, extra_headers: Optional[Dict[str, str]] = None) -> str:
        """
        Races multiple browser TLS / HTTP/2 impersonation profiles in parallel using curl_cffi.
        Returns the HTML from the first profile that succeeds with HTTP 200 and no Cloudflare challenge.
        """
        if not cffi_requests:
            return ""

        def _try_profile(profile: Dict[str, Any]) -> Tuple[bool, str, str]:
            try:
                headers = profile["headers"].copy()
                if extra_headers:
                    headers.update(extra_headers)
                r = cffi_requests.get(
                    url,
                    impersonate=profile["impersonate"],
                    headers=headers,
                    timeout=timeout_seconds,
                )
                cf_mitigated = r.headers.get("Cf-Mitigated", "").lower()
                is_challenge = cf_mitigated == "challenge" or "challenges.cloudflare.com" in r.text
                if r.status_code == 200 and not is_challenge and len(r.text) > 500:
                    return True, profile["name"], r.text
                return False, profile["name"], ""
            except Exception:
                return False, profile["name"], ""

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(FAST_RACE_PROFILES)) as executor:
                futures = {executor.submit(_try_profile, p): p for p in FAST_RACE_PROFILES}
                for f in concurrent.futures.as_completed(futures, timeout=timeout_seconds + 1.0):
                    success, name, html = f.result()
                    if success:
                        logger.info(f"[SteamDB] Fast-path race won by profile '{name}' for {url}")
                        for remaining in futures:
                            remaining.cancel()
                        return html
        except concurrent.futures.TimeoutError:
            logger.debug(f"[SteamDB] Fast-path race timed out after {timeout_seconds}s for {url}")
        except Exception as e:
            logger.debug(f"[SteamDB] Error during fast-path race: {e}")

        return ""

    def fetch_via_byparr(self, url: str, timeout_seconds: int = 60) -> str:
        """
        Fetches the fully-rendered DOM from SteamDB via Byparr (which executes JS & bypasses Turnstile).
        """
        if not ByparrManager.is_running():
            ByparrManager.ensure_running()

        endpoint = f"{self.solver_url}/v1"
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": timeout_seconds * 1000 if timeout_seconds < 1000 else timeout_seconds,
        }
        headers = ByparrManager.build_headers()

        try:
            r = requests.post(endpoint, json=payload, headers=headers, timeout=timeout_seconds + 15)
            if r.status_code == 200:
                data = r.json()
                sol = data.get("solution", {})
                html = sol.get("response", "")

                # Cache cookies & user-agent for fast static requests
                cookies = {c["name"]: c["value"] for c in sol.get("cookies", [])}
                ua = sol.get("userAgent", "")
                cf_val = cookies.get("cf_clearance")
                if cf_val:
                    s = get_settings()
                    s.setValue("steamdb/user_agent", ua)
                    s.setValue("steamdb/cf_clearance", cf_val)
                    s.setValue("steamdb/clearance_timestamp", int(time.time()))

                return html
            else:
                logger.error(f"[SteamDB] Byparr returned status {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"[SteamDB] Failed to fetch {url} via Byparr: {e}")

        return ""

    def fetch_rendered_html(self, url: str, timeout_seconds: int = 60, allow_fast_path: bool = True, extra_headers: Optional[Dict[str, str]] = None) -> str:
        """
        Fetches SteamDB HTML. First races multiple direct TLS profiles (fast-path).
        If fast-path is challenged or unavailable and Byparr is configured, falls back to Byparr.
        """
        if get_settings().value("disable_steamdb_scraping", False, type=bool):
            logger.info("[SteamDB] Scraping disabled in Testing settings; skipping fetch.")
            return ""

        # 1. Fast-path multi-profile race
        if allow_fast_path and cffi_requests:
            has_byparr = ByparrManager.is_available()
            fast_timeout = 4.0 if has_byparr else 8.0
            t0 = time.time()
            html = self.fetch_via_fast_race(url, timeout_seconds=fast_timeout, extra_headers=extra_headers)
            if html:
                return html
            elapsed = time.time() - t0
            logger.info(f"[SteamDB] Fast-path did not resolve cleanly ({elapsed:.2f}s). Byparr available: {has_byparr}")

        # 2. Byparr fallback (if available)
        if ByparrManager.is_available():
            return self.fetch_via_byparr(url, timeout_seconds=timeout_seconds)

        return ""

    def _parse_patchnotes_html(self, html: str, limit: int = 40) -> List[Dict[str, Any]]:
        """Parses builds from either dynamic client-side JS table or static HTML branches table."""
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        results = []

        # Parser 1: Find the table containing "Patch Title" (rendered by Byparr / browser JS)
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            if any("patch title" in h.lower() for h in headers):
                for row in table.find_all("tr")[1:]:
                    cols = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                    if len(cols) >= 4:
                        date_str = cols[0]
                        day_str = cols[1] if len(cols) > 1 else ""
                        time_str = cols[2] if len(cols) > 2 else ""
                        title_str = cols[3] if len(cols) > 3 else ""
                        build_id = cols[-1] if len(cols) > 4 else ""

                        clean_bid = "".join(re.findall(r"\d+", build_id))
                        if not clean_bid:
                            for a in row.find_all("a", href=True):
                                m = re.search(r"/patchnotes/(\d+)", a["href"])
                                if m:
                                    clean_bid = m.group(1)
                                    break

                        patch_url = f"{STEAMDB_BASE_URL}/patchnotes/{clean_bid}/" if clean_bid else ""
                        for a in row.find_all("a", href=True):
                            if "/patchnotes/" in a["href"]:
                                patch_url = f"{STEAMDB_BASE_URL}{a['href']}" if a["href"].startswith("/") else a["href"]
                                break

                        if clean_bid:
                            results.append({
                                "date": date_str,
                                "day": day_str,
                                "time": time_str,
                                "title": title_str or f"Update (Build {clean_bid})",
                                "buildid": clean_bid,
                                "patchnotes_url": patch_url,
                            })
                            if len(results) >= limit:
                                break
                if results:
                    return results

        # Parser 2: Parse static branches/builds table (static HTML from fast-path)
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if "build id" in headers or any("build" in h for h in headers):
                for row in table.find_all("tr")[1:]:
                    cols = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                    clean_bid = ""
                    patch_url = ""
                    for a in row.find_all("a", href=True):
                        m = re.search(r"/patchnotes/(\d+)", a["href"])
                        if m:
                            clean_bid = m.group(1)
                            patch_url = f"{STEAMDB_BASE_URL}{a['href']}" if a["href"].startswith("/") else a["href"]
                            break
                    if not clean_bid and cols:
                        for col in cols:
                            digits = "".join(re.findall(r"^\d{7,10}$", col))
                            if digits:
                                clean_bid = digits
                                patch_url = f"{STEAMDB_BASE_URL}/patchnotes/{clean_bid}/"
                                break
                    if clean_bid:
                        branch_name = cols[0] if cols else ""
                        desc = cols[1] if len(cols) > 1 and cols[1] != branch_name else ""
                        title = f"{branch_name} - {desc}".strip(" -") if desc else (branch_name or f"Build {clean_bid}")
                        time_str = cols[-1] if len(cols) > 3 else ""
                        results.append({
                            "date": time_str.split("–")[0].strip() if "–" in time_str else time_str,
                            "day": "",
                            "time": time_str.split("–")[1].strip() if "–" in time_str else "",
                            "title": title or f"Update (Build {clean_bid})",
                            "buildid": clean_bid,
                            "patchnotes_url": patch_url,
                        })
                        if len(results) >= limit:
                            break
                if results:
                    return results

        return results

    def _parse_patchnotes_rss(self, xml_text: str, limit: int = 40) -> List[Dict[str, Any]]:
        """
        Parses official SteamDB XML RSS feed (https://steamdb.info/api/PatchnotesRSS/?appid=<appid>).
        Returns list of patch dictionaries:
        [{date, day, time, title, buildid, patchnotes_url}]
        """
        if not xml_text:
            return []
        try:
            root = ET.fromstring(xml_text)
        except Exception as e:
            logger.debug(f"[SteamDB] Failed to parse RSS XML: {e}")
            return []

        results = []
        for item in root.findall(".//item"):
            guid = (item.findtext("guid") or "").strip()
            # guid is formatted like 'build#12345678'
            m = re.search(r"\d+", guid)
            build_id = m.group(0) if m else ""

            link = (item.findtext("link") or "").strip()
            if not build_id and link:
                m_link = re.search(r"/patchnotes/(\d+)", link)
                if m_link:
                    build_id = m_link.group(1)

            if not build_id:
                continue

            title = (item.findtext("title") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            date_str = ""
            day_str = ""
            time_str = ""
            if pub_date:
                try:
                    dt = email.utils.parsedate_to_datetime(pub_date)
                    date_str = dt.strftime("%d %B %Y")
                    day_str = dt.strftime("%a")
                    time_str = dt.strftime("%H:%M")
                except Exception:
                    date_str = pub_date

            patch_url = f"{STEAMDB_BASE_URL}/patchnotes/{build_id}/"

            results.append({
                "date": date_str,
                "day": day_str,
                "time": time_str,
                "title": title or f"Update (Build {build_id})",
                "buildid": build_id,
                "patchnotes_url": patch_url,
            })
            if len(results) >= limit:
                break

        return results

    def get_patchnotes(self, appid: int, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Scrapes patchnotes for an app.
        1. Fast-Path AJAX section (RenderAppSection): queries SteamDB's pre-rendered section endpoint
           (https://steamdb.info/api/RenderAppSection/?section=patchnotes&appid=<appid>) via Direct TLS
           with X-Requested-With: XMLHttpRequest. Returns up to 50 builds with patch titles in ~200ms.
        2. Fast-Path XML RSS feed (PatchnotesRSS): fallback providing the 10 most recent release builds.
        3. Fast-Path static HTML page: fallback parsing the branches table.
        4. Byparr fallback: headless browser rendering if Byparr is configured and fast-path yielded no builds.
        """
        # 1. Try RenderAppSection endpoint first (provides up to 50 rich builds with patch titles)
        render_url = f"{STEAMDB_BASE_URL}/api/RenderAppSection/?section=patchnotes&appid={appid}"
        ajax_headers = {
            "Accept": "text/html",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{STEAMDB_BASE_URL}/app/{appid}/patchnotes/",
        }
        ajax_html = self.fetch_rendered_html(render_url, extra_headers=ajax_headers)
        if ajax_html:
            results = self._parse_patchnotes_html(ajax_html, limit=limit)
            if results:
                for r in results:
                    r["_source"] = "render_section"
                logger.info(f"[SteamDB] Parsed {len(results)} patchnotes entries from RenderAppSection for App {appid}")
                return results

        # 2. Try official XML RSS feed fallback (provides the 10 most recent release builds)
        rss_url = f"{STEAMDB_BASE_URL}/api/PatchnotesRSS/?appid={appid}"
        xml_data = self.fetch_rendered_html(rss_url)
        results = self._parse_patchnotes_rss(xml_data, limit=limit)
        if results:
            for r in results:
                r["_source"] = "rss"
            logger.info(f"[SteamDB] Parsed {len(results)} patchnotes entries from RSS for App {appid}")
            return results

        # 3. Fall back to static HTML page (branches table)
        url = f"{STEAMDB_BASE_URL}/app/{appid}/patchnotes/"
        html = self.fetch_rendered_html(url)
        if html:
            results = self._parse_patchnotes_html(html, limit=limit)
            if results:
                for r in results:
                    r["_source"] = "static_html"

        # 4. Fall back to Byparr (if fast-path yielded no builds and Byparr is available)
        if not results and ByparrManager.is_available():
            logger.info(f"[SteamDB] Fast-path did not yield patchnotes for App {appid}. Falling back to Byparr...")
            byparr_html = self.fetch_via_byparr(url, timeout_seconds=60)
            if byparr_html:
                results = self._parse_patchnotes_html(byparr_html, limit=limit)
                for r in results:
                    r["_source"] = "byparr"

        logger.info(f"[SteamDB] Parsed {len(results)} patchnotes entries for App {appid}")
        return results

    def get_patch_depots(self, buildid_or_url: str) -> Dict[str, Dict[str, str]]:
        """
        Scrapes https://steamdb.info/patchnotes/<buildid>/
        Returns dictionary mapping depot_id -> {depot_id, manifest_id, old_manifest_id}
        """
        if str(buildid_or_url).startswith("http"):
            url = str(buildid_or_url)
        else:
            url = f"{STEAMDB_BASE_URL}/patchnotes/{str(buildid_or_url).strip('/')}/"

        html = self.fetch_rendered_html(url)
        if not html:
            return {}

        soup = BeautifulSoup(html, "html.parser")
        depots = {}

        # Look for depot headers and history links
        for heading in soup.select(".panel-heading"):
            depot_link = heading.find("a", href=lambda h: h and "/depot/" in h)
            if depot_link:
                d_match = re.search(r"/depot/(\d+)", depot_link["href"])
                if d_match:
                    depot_id = d_match.group(1)
                    
                    # Target manifest ID is in changeid=M:<id> on the depot link
                    m_target = re.search(r"changeid=M:(\d+)", depot_link["href"])
                    manifest_id = m_target.group(1) if m_target else ""
                    
                    old_manifest_id = ""
                    panel = heading.find_parent("div")
                    if panel:
                        manifest_links = [
                            re.search(r"changeid=M:(\d+)", a["href"]).group(1)
                            for a in panel.find_all("a", href=True)
                            if "/history/?changeid=M:" in a["href"] and re.search(r"changeid=M:(\d+)", a["href"])
                        ]
                        # If there are two manifest IDs, the other one is old
                        for mid in manifest_links:
                            if mid != manifest_id:
                                old_manifest_id = mid
                                break

                    depots[depot_id] = {
                        "depot_id": depot_id,
                        "manifest_id": manifest_id,
                        "old_manifest_id": old_manifest_id,
                    }

        logger.info(f"[SteamDB] Extracted {len(depots)} depots for {url}")
        return depots

    def get_app_depots(self, appid: str | int) -> Dict[str, Dict[str, Any]]:
        """
        Scrapes https://steamdb.info/app/<appid>/depots/
        Extracts all depots (including DLC depots) with their names, configurations, and sizes.
        """
        appid_str = str(appid)
        url = f"{STEAMDB_BASE_URL}/app/{appid_str}/depots/"
        html = self.fetch_rendered_html(url)
        if not html:
            return self._fetch_depots_fallback_steam_store(appid_str)

        soup = BeautifulSoup(html, "html.parser")
        depots = {}

        for table in soup.find_all("table"):
            header_row = table.find("tr")
            if not header_row:
                continue
            headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]
            if not ("id" in headers and "configuration" in headers and "size" in headers):
                continue

            for row in table.find_all("tr")[1:]:
                cols = row.find_all(["td", "th"])
                if len(cols) >= 3:
                    depot_id = cols[0].get_text(strip=True)
                    if not depot_id.isdigit():
                        continue

                    config_cell = cols[1]
                    size_cell = cols[2]
                    dl_cell = cols[3] if len(cols) > 3 else None

                    # Check for OS badges (may have multiple: Windows, Linux, macOS)
                    os_spans = config_cell.find_all(class_=re.compile(r"depot-os"))
                    detected_oses = []
                    for os_s in os_spans:
                        os_t = os_s.get_text(strip=True).lower()
                        if ("windows" in os_t or "win" in os_t) and "windows" not in detected_oses:
                            detected_oses.append("windows")
                        if "linux" in os_t and "linux" not in detected_oses:
                            detected_oses.append("linux")
                        if ("mac" in os_t or "apple" in os_t) and "macos" not in detected_oses:
                            detected_oses.append("macos")

                    oslist = ",".join(detected_oses) if detected_oses else None

                    # Check for architecture
                    osarch = None
                    text_lower = config_cell.get_text(" ", strip=True).lower()
                    if "64-bit" in text_lower or "x64" in text_lower or "64 bit" in text_lower:
                        osarch = "64"
                    elif "32-bit" in text_lower or "x86" in text_lower or "32 bit" in text_lower:
                        osarch = "32"

                    muted_span = config_cell.find("span", class_=re.compile(r"muted"))
                    if muted_span:
                        name = muted_span.get_text(strip=True)
                    else:
                        raw_name = config_cell.get_text(" ", strip=True)
                        raw_name = re.sub(r"^DLC\s+\d+\s*", "", raw_name).strip()
                        # If raw_name only contains OS and arch tokens, it's not a real depot name!
                        cleaned_words = re.sub(
                            r"\b(windows|linux|macos|64-bit|32-bit|x64|x86|64|32)\b",
                            "",
                            raw_name,
                            flags=re.IGNORECASE,
                        ).strip()
                        if cleaned_words and not re.match(r"^[,\s\-_/]+$", cleaned_words):
                            name = raw_name
                        else:
                            name = None

                    is_dlc = bool(config_cell.find(string=re.compile(r"DLC\s+\d+")) or "dlc" in config_cell.get_text().lower())
                    size_str = size_cell.get_text(strip=True) if size_cell else ""
                    dl_str = dl_cell.get_text(strip=True) if dl_cell else ""
                    size_bytes = parse_size_to_bytes(size_str)

                    depots[depot_id] = {
                        "depot_id": depot_id,
                        "name": name,
                        "oslist": oslist,
                        "osarch": osarch,
                        "size_str": size_str,
                        "size_bytes": size_bytes,
                        "dl_str": dl_str,
                        "is_dlc": is_dlc,
                    }

        logger.info(f"[SteamDB] Parsed {len(depots)} depots for App {appid_str}")
        return depots

    def _fetch_depots_fallback_steam_store(self, appid: str) -> Dict[str, Dict[str, Any]]:
        """Fallback when SteamDB is unavailable: queries Steam Store API for DLC names."""
        results = {}
        try:
            resp = requests.get(
                f"https://store.steampowered.com/api/appdetails?appids={appid}",
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if resp.status_code == 200:
                data = resp.json()
                dlcs = data.get(str(appid), {}).get("data", {}).get("dlc", [])
                for dlc_id in dlcs:
                    try:
                        d_resp = requests.get(
                            f"https://store.steampowered.com/api/appdetails?appids={dlc_id}",
                            timeout=6,
                            headers={"User-Agent": "Mozilla/5.0"}
                        )
                        if d_resp.status_code == 200:
                            dlc_data = d_resp.json()
                            dlc_name = dlc_data.get(str(dlc_id), {}).get("data", {}).get("name")
                            if dlc_name:
                                results[str(dlc_id)] = {
                                    "depot_id": str(dlc_id),
                                    "name": dlc_name,
                                    "dlcappid": str(dlc_id),
                                    "oslist": None,
                                    "size_str": "",
                                    "size_bytes": 0,
                                    "dl_str": "",
                                    "is_dlc": True,
                                }
                                try:
                                    from managers.db_manager import DatabaseManager
                                    db_info = DatabaseManager().get_app_info(appid) or {}
                                    for d_id, d_data in db_info.get("depots", {}).items():
                                        if isinstance(d_data, dict) and str(d_data.get("dlcappid") or "") == str(dlc_id):
                                            results[str(d_id)] = {
                                                "depot_id": str(d_id),
                                                "name": f"[DLC {dlc_id}] {dlc_name}",
                                                "dlcappid": str(dlc_id),
                                                "oslist": d_data.get("oslist"),
                                                "size_str": "",
                                                "size_bytes": 0,
                                                "dl_str": "",
                                                "is_dlc": True,
                                            }
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"[SteamDB Fallback] Store API fetch failed for {appid}: {e}")
        return results


def parse_size_to_bytes(size_str: str) -> int:
    """Converts human readable size string like '21.64 MiB' or '1.31 GiB' to byte count."""
    if not size_str or "no size" in size_str.lower():
        return 0
    units = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000 * 1000,
        "mib": 1024 * 1024,
        "gb": 1000 * 1000 * 1000,
        "gib": 1024 * 1024 * 1024,
        "tb": 1024 * 1024 * 1024 * 1024,
        "tib": 1024 * 1024 * 1024 * 1024,
    }
    m = re.match(r"^([\d.]+)\s*([a-zA-Z]+)", size_str.strip())
    if m:
        try:
            val = float(m.group(1))
            unit = m.group(2).lower()
            return int(val * units.get(unit, 1))
        except Exception:
            pass
    return 0


try:
    import zstandard as zstd
except ImportError:
    zstd = None
import sqlite3
import json
import threading


class SteamDBBuildsCache:
    """Manages persistent zstandard-compressed SQLite caching of the last 6 builds in its own database (steamdb_builds.db)."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SteamDBBuildsCache, cls).__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        with self._lock:
            if self._initialized:
                return

            from utils.helpers import get_data_file_path
            # Migrate legacy root file if present
            legacy_path = get_base_path() / "steamdb_builds.db"
            target_path = get_base_path() / "db" / "steamdb_builds.db"
            if legacy_path.exists() and not target_path.exists():
                try:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.move(str(legacy_path), str(target_path))
                    logger.info(f"[SteamDB Cache] Migrated legacy steamdb_builds.db from root to {target_path}")
                except Exception as e:
                    logger.warning(f"[SteamDB Cache] Failed to migrate legacy steamdb_builds.db: {e}")

            self.db_path = get_data_file_path("steamdb_builds.db")
            self._conn_lock = threading.RLock()
            self.cctx = zstd.ZstdCompressor(level=3) if zstd else None
            self.dctx = zstd.ZstdDecompressor() if zstd else None
            self._init_db()
            self._initialized = True

    def _init_db(self):
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._conn_lock, sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS builds (
                        appid INTEGER PRIMARY KEY,
                        builds_blob BLOB,
                        last_updated INTEGER
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"[SteamDB Cache] Failed to init database at {self.db_path}: {e}")

    def get_builds(self, appid: int) -> List[Dict[str, Any]]:
        """Retrieves cached builds (up to 50) for appid, decompressed from zstd."""
        return self.get_builds_with_age(appid)[0]

    def get_builds_with_age(self, appid: int) -> tuple:
        """Returns (builds: list, age_seconds: float). age_seconds is -1 if not cached."""
        try:
            with self._conn_lock, sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT builds_blob, last_updated FROM builds WHERE appid = ?", (int(appid),))
                row = cur.fetchone()
                if row and row[0]:
                    raw_bytes = row[0]
                    last_updated = row[1] or 0
                    if self.dctx:
                        try:
                            decompressed = self.dctx.decompress(raw_bytes)
                        except Exception:
                            decompressed = raw_bytes
                    else:
                        decompressed = raw_bytes
                    builds = json.loads(decompressed.decode("utf-8"))
                    age = time.time() - last_updated
                    return builds, age
        except Exception as e:
            logger.debug(f"[SteamDB Cache] Failed to read cached builds for {appid}: {e}")
        return [], -1

    def save_builds(self, appid: int, builds: List[Dict[str, Any]]):
        """Saves up to 50 most recent builds into steamdb_builds.db with zstandard compression."""
        if not builds:
            return
        top_builds = builds[:50]
        try:
            json_bytes = json.dumps(top_builds).encode("utf-8")
            compressed_blob = self.cctx.compress(json_bytes) if self.cctx else json_bytes

            with self._conn_lock, sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO builds (appid, builds_blob, last_updated)
                    VALUES (?, ?, ?)
                    ON CONFLICT(appid) DO UPDATE SET
                        builds_blob = excluded.builds_blob,
                        last_updated = excluded.last_updated
                """, (int(appid), compressed_blob, int(time.time())))
                conn.commit()
                logger.info(f"[SteamDB Cache] Successfully cached {len(top_builds)} builds for App {appid} in {self.db_path.name}")
        except Exception as e:
            logger.error(f"[SteamDB Cache] Failed to save builds for {appid}: {e}")

    def update_build_depots(self, appid: int, buildid: str, depots: Dict[str, Any]):
        """Updates depot information for a cached build entry."""
        builds = self.get_builds(appid)
        updated = False
        for b in builds:
            if str(b.get("buildid")) == str(buildid):
                b["depots"] = depots
                updated = True
                break
        if updated:
            self.save_builds(appid, builds)

