"""
ISP Bypass Module for ASSella.

Provides DNS-over-HTTPS (DoH) resolution and managed background Tor/SOCKS5 proxy fallback
strictly for Hubcap API requests (hubcapmanifest.com).
"""

import atexit
import json
import logging
import os
import select
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, Union, List

import requests
from utils.settings import get_settings

logger = logging.getLogger(__name__)

# Connection status tracking for UI
connection_status = "Connecting"

TARGET_DOMAIN = "hubcapmanifest.com"
DOH_CLOUDFLARE_URL = f"https://1.1.1.1/dns-query?name={TARGET_DOMAIN}&type=A"
DOH_GOOGLE_URL = f"https://dns.google/resolve?name={TARGET_DOMAIN}&type=A"

_tor_process: Optional[subprocess.Popen] = None
_tor_lock = threading.Lock()


class TorManager:
    """Manages an optional background Tor process with native HTTP tunneling launched by ASSella."""

    TOR_SOCKS_PORT = 9050
    TOR_HTTP_PORT = 9080

    @classmethod
    def is_proxy_active(cls, port: int = TOR_HTTP_PORT) -> bool:
        """Checks if a proxy is listening on local port."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            res = s.connect_ex(("127.0.0.1", port))
            s.close()
            return res == 0
        except Exception:
            return False

    @classmethod
    def get_tor_proxies(cls) -> Dict[str, str]:
        """Returns proxy configuration for requests (HTTP tunnel or SOCKS5 fallback)."""
        if cls.is_proxy_active(cls.TOR_HTTP_PORT):
            return {
                "http": f"http://127.0.0.1:{cls.TOR_HTTP_PORT}",
                "https": f"http://127.0.0.1:{cls.TOR_HTTP_PORT}",
            }
        return {
            "http": f"socks5h://127.0.0.1:{cls.TOR_SOCKS_PORT}",
            "https": f"socks5h://127.0.0.1:{cls.TOR_SOCKS_PORT}",
        }

    @classmethod
    def start_tor_if_needed(cls) -> bool:
        """
        Ensures a Tor listener is active on 127.0.0.1:9080 (HTTP Tunnel) or 127.0.0.1:9050.
        If no proxy is listening, attempts to launch a background Tor helper process.
        """
        global _tor_process

        if cls.is_proxy_active(cls.TOR_HTTP_PORT) or cls.is_proxy_active(cls.TOR_SOCKS_PORT):
            logger.debug(f"[ISPBypass] Active Tor proxy detected on 127.0.0.1")
            return True

        with _tor_lock:
            if _tor_process and _tor_process.poll() is None:
                return True

            # Locate tor executable
            tor_path = cls._find_tor_binary()
            if not tor_path:
                logger.warning("[ISPBypass] Tor executable not found on system or AppImage.")
                return False

            try:
                from utils.helpers import get_base_path
                tor_data_dir = get_base_path() / "tor_data"
                tor_data_dir.mkdir(parents=True, exist_ok=True)

                pid_file = tor_data_dir / "tor.pid"
                log_file = tor_data_dir / "tor.log"

                cmd = [
                    tor_path,
                    "--SocksPort", str(cls.TOR_SOCKS_PORT),
                    "--HTTPTunnelPort", f"127.0.0.1:{cls.TOR_HTTP_PORT}",
                    "--DataDirectory", str(tor_data_dir),
                    "--PidFile", str(pid_file),
                    "--Log", f"notice file {log_file}"
                ]

                logger.info(f"[ISPBypass] Starting background Tor process: {' '.join(cmd)}")
                _tor_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )

                # Wait up to 6s for listener
                for _ in range(12):
                    time.sleep(0.5)
                    if cls.is_proxy_active(cls.TOR_HTTP_PORT) or cls.is_proxy_active(cls.TOR_SOCKS_PORT):
                        logger.info(
                            f"[ISPBypass] Background Tor process successfully listening on 127.0.0.1:{cls.TOR_HTTP_PORT}"
                        )
                        return True

                logger.warning("[ISPBypass] Tor process started but listener did not open on 9080 within timeout.")
                return False

            except Exception as e:
                logger.error(f"[ISPBypass] Failed to start background Tor process: {e}")
                return False

    @classmethod
    def stop_tor(cls) -> None:
        """Cleanly terminates any background Tor helper process launched by ASSella."""
        global _tor_process
        with _tor_lock:
            if _tor_process:
                try:
                    logger.info("[ISPBypass] Terminating background Tor helper process...")
                    _tor_process.terminate()
                    try:
                        _tor_process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        _tor_process.kill()
                    logger.info("[ISPBypass] Background Tor helper process terminated.")
                except Exception as e:
                    logger.warning(f"[ISPBypass] Error terminating Tor process: {e}")
                finally:
                    _tor_process = None


    @classmethod
    def _find_tor_binary(cls) -> Optional[str]:
        """Searches for tor executable in AppImage bundle, local ACCELA bin, and system PATH."""
        # 1. Check AppImage runtime environment
        appdir = os.environ.get("APPDIR")
        if appdir:
            candidates = [
                Path(appdir) / "bin" / "tor",
                Path(appdir) / "usr" / "bin" / "tor",
            ]
            for c in candidates:
                if c.exists() and os.access(c, os.X_OK):
                    return str(c)

        # 2. Check local ACCELA bin directory
        try:
            from utils.helpers import get_base_path
            local_tor = get_base_path() / "bin" / "tor"
            if local_tor.exists() and os.access(local_tor, os.X_OK):
                return str(local_tor)
        except Exception:
            pass

        # 3. Check system PATH
        import shutil
        sys_tor = shutil.which("tor")
        if sys_tor:
            return sys_tor

        # 4. Standard Linux fallback paths
        for p in ["/usr/bin/tor", "/usr/local/bin/tor"]:
            if os.path.exists(p) and os.access(p, os.X_OK):
                return p

        return None


# Register cleanup at Python exit
atexit.register(TorManager.stop_tor)


# --------------------------
# Wirecutter / Cloudflare Worker Proxy (Encrypted In-Memory)
# --------------------------

_WIRECUTTER_SECRET = "DJLftA68CqIusHL1zjsD3?2m*0df#fR{%CTApj2<6)P<VOjHyTG%FVuPXrt-9als`Z#Om*"
_WIRECUTTER_SALT = b"ASSella_Hubcap_Bypass_v261"

_ASSELLA_TOKEN_SECRET = "ARr(B00000JXS3Y2NP~11{nYiDkl{Q1^@zGT~sd&VO3sQ"
_ASSELLA_TOKEN_SALT = b"ASSella_Worker_Token_Salt_v261"


def get_default_wirecutter_endpoint() -> str:
    """Returns the default embedded Wirecutter Cloudflare Worker endpoint."""
    try:
        import base64
        raw = base64.b85decode(_WIRECUTTER_SECRET.encode("ascii"))
        return bytes(b ^ _WIRECUTTER_SALT[i % len(_WIRECUTTER_SALT)] for i, b in enumerate(raw)).decode("utf-8")
    except Exception:
        return "https://rapid-thunder-fba1wirecutter.7ucking.workers.dev"


def get_assella_proxy_token() -> str:
    """Returns the internal ASSella authorization token for Wirecutter Worker."""
    try:
        from utils.settings import get_settings
        custom_tok = get_settings().value("assella_proxy_token", "", type=str)
        if custom_tok and custom_tok.strip():
            return custom_tok.strip()
    except Exception:
        pass
    try:
        import base64
        raw = base64.b85decode(_ASSELLA_TOKEN_SECRET.encode("ascii"))
        return bytes(b ^ _ASSELLA_TOKEN_SALT[i % len(_ASSELLA_TOKEN_SALT)] for i, b in enumerate(raw)).decode("utf-8")
    except Exception:
        return "assella_k9_fba1wirecutter_token_2026"


_hubcap_request_timestamps: List[float] = []
_hubcap_rate_lock = threading.Lock()
_hubcap_backoff_until: float = 0.0


class HubcapRateLimitError(requests.exceptions.HTTPError):
    """Raised when Hubcap or Wirecutter returns 429 Too Many Requests."""
    def __init__(self, response: requests.Response, scope: str = "unknown", retry_after: float = 3.0):
        super().__init__(f"Hubcap rate limit exceeded ({scope} scope). Retry after {retry_after}s.", response=response)
        self.scope = scope
        self.retry_after = retry_after


def _process_429_response(resp: requests.Response) -> Tuple[float, str]:
    """Extract Retry-After and scope from 429 response and register global backoff."""
    global _hubcap_backoff_until
    retry_after = 3.0
    scope = "unknown"

    # 1. Check Retry-After header
    hdr_retry = resp.headers.get("Retry-After")
    if hdr_retry:
        try:
            retry_after = max(1.0, float(hdr_retry))
        except (ValueError, TypeError):
            pass

    # 2. Check JSON payload
    try:
        body = resp.json()
        if isinstance(body, dict):
            scope = body.get("scope", scope)
            if "retry_after" in body:
                try:
                    retry_after = max(retry_after, float(body["retry_after"]))
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass

    with _hubcap_rate_lock:
        _hubcap_backoff_until = max(_hubcap_backoff_until, time.time() + retry_after)

    logger.warning(
        f"[ISPBypass] Hubcap 429 Too Many Requests (scope: {scope}). "
        f"Backing off for {retry_after:.1f}s. Halting multi-tier fallbacks."
    )
    return retry_after, scope


def _check_and_raise_for_status(resp: requests.Response) -> None:
    """Check status code, handle 429 backoff specifically, or raise HTTPError."""
    if resp.status_code == 429:
        retry_after, scope = _process_429_response(resp)
        raise HubcapRateLimitError(resp, scope=scope, retry_after=retry_after)
    resp.raise_for_status()


def _enforce_hubcap_rate_limit(max_requests: int = 3, per_seconds: float = 3.05) -> None:
    """Ensure outgoing requests to Hubcap never exceed 3 requests per 3 seconds and respect 429 backoff."""
    with _hubcap_rate_lock:
        now = time.time()
        global _hubcap_backoff_until
        if _hubcap_backoff_until > now:
            sleep_needed = _hubcap_backoff_until - now
            logger.warning(f"[ISPBypass] Hubcap 429 backoff active. Waiting {sleep_needed:.2f}s...")
            time.sleep(sleep_needed)
            now = time.time()

        while _hubcap_request_timestamps and _hubcap_request_timestamps[0] <= now - per_seconds:
            _hubcap_request_timestamps.pop(0)

        if len(_hubcap_request_timestamps) >= max_requests:
            sleep_needed = per_seconds - (now - _hubcap_request_timestamps[0])
            if sleep_needed > 0:
                time.sleep(sleep_needed)
            now = time.time()
            while _hubcap_request_timestamps and _hubcap_request_timestamps[0] <= now - per_seconds:
                _hubcap_request_timestamps.pop(0)

        _hubcap_request_timestamps.append(time.time())


def get_wirecutter_endpoint() -> str:
    """Returns custom Wirecutter URL if configured in settings, or decrypts default endpoint."""
    try:
        from utils.settings import get_settings
        custom_wc = get_settings().value("wirecutter_url", "", type=str)
        if custom_wc and custom_wc.strip():
            url = custom_wc.strip().rstrip("/")
            if not url.startswith("http://") and not url.startswith("https://"):
                url = f"https://{url}"
            return url
    except Exception:
        pass
    return get_default_wirecutter_endpoint()


def get_custom_proxy() -> Optional[str]:
    """Returns user-configured custom proxy URL (e.g. http://127.0.0.1:7890 or socks5://...)."""
    try:
        from utils.settings import get_settings
        p = get_settings().value("custom_proxy_url", "", type=str)
        if p and p.strip():
            return p.strip()
    except Exception:
        pass
    return None


def rewrite_url_for_wirecutter(url: str, worker_base: Optional[str] = None) -> str:
    """Rewrites https://hubcapmanifest.com/api/v1/... to {worker_base}/..."""
    base = (worker_base or get_wirecutter_endpoint()).rstrip("/")
    if "hubcapmanifest.com/api/v1" in url:
        return url.replace("https://hubcapmanifest.com/api/v1", base).replace("http://hubcapmanifest.com/api/v1", base)
    if "hubcapmanifest.com" in url:
        return url.replace("https://hubcapmanifest.com", base).replace("http://hubcapmanifest.com", base)
    return url


def categorize_error(e: Exception) -> str:
    """Categorizes network exceptions into compact, informative status codes."""
    if isinstance(e, requests.exceptions.HTTPError):
        code = e.response.status_code if e.response is not None else 0
        if code == 400: return "ERR: 400"
        if code == 401: return "ERR: 401"
        if code == 403: return "ERR: 403"
        if code == 404: return "ERR: 404"
        if code == 428: return "ERR: 428"
        if code == 429: return "ERR: 429"
        if code == 500: return "ERR: 500"
        if code == 502: return "ERR: 502"
        if code == 503: return "ERR: 503"
        if code == 504: return "ERR: 504"
        return f"ERR: {code}"
    if isinstance(e, requests.exceptions.Timeout):
        return "ERR: Timeout"
    if isinstance(e, requests.exceptions.SSLError):
        return "ERR: SSL"
    if isinstance(e, requests.exceptions.ConnectionError):
        msg = str(e).lower()
        if "refused" in msg:
            return "ERR: Refused"
        if "name or service not known" in msg or "nodename" in msg or "dns" in msg:
            return "ERR: DNS"
        if "block" in msg or "html" in msg or "intercept" in msg:
            return "ERR: Blocked"
        return "ERR: Conn"
    msg = str(e)
    if "No Tor" in msg:
        return "ERR: No Tor"
    return "ERR: Failed"


def is_html_or_blocked_payload(resp: Optional[requests.Response]) -> bool:
    """
    Checks if a response is an HTML block page, captive portal, or Cloudflare challenge
    rather than a legitimate JSON or binary API payload.
    """
    if resp is None:
        return True
    try:
        # A 304 legitimately has no body, so the empty-payload rule below
        # must not turn a conditional request into a false "blocked" report.
        if getattr(resp, "status_code", None) == 304:
            return False

        ctype = (resp.headers.get("content-type") or "").lower()
        if "text/html" in ctype:
            return True

        raw = resp.content.strip() if hasattr(resp, "content") else b""
        if raw.startswith(b"\xef\xbb\xbf"):  # Strip UTF-8 BOM
            raw = raw[3:].strip()

        if len(raw) == 0:
            return True

        raw_lower = raw[:1024].lower()
        if (
            raw_lower.startswith(b"<")
            or raw_lower.startswith(b"<!doctype")
            or raw_lower.startswith(b"<html")
            or b"<title>" in raw_lower
            or (b"cloudflare" in raw_lower and b"blocked" in raw_lower)
            or b"access denied" in raw_lower
            or b"just a moment" in raw_lower
        ):
            return True
    except Exception:
        pass
    return False


# --------------------------
# Gateway Health Check API (for UI Buttons)
# --------------------------

def test_gateway_direct() -> Tuple[bool, str, int]:
    """Tests Direct HTTPS gateway. Returns (success, display_status, latency_ms)."""
    t0 = time.time()
    try:
        url = "https://hubcapmanifest.com/api/v1/health"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        resp.raise_for_status()
        if is_html_or_blocked_payload(resp):
            return False, "ERR: Blocked", int((time.time() - t0) * 1000)
        latency = int((time.time() - t0) * 1000)
        return True, f"OK ({latency}ms)", latency
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        return False, categorize_error(e), latency


def test_gateway_doh() -> Tuple[bool, str, int]:
    """Tests DoH HTTPS gateway. Returns (success, display_status, latency_ms)."""
    t0 = time.time()
    try:
        resolved_ip = resolve_doh(TARGET_DOMAIN)
        if not resolved_ip:
            return False, "ERR: DNS Fail", int((time.time() - t0) * 1000)
        orig_getaddrinfo = socket.getaddrinfo
        def doh_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            if host == TARGET_DOMAIN:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (resolved_ip, port))]
            return orig_getaddrinfo(host, port, family, type, proto, flags)

        socket.getaddrinfo = doh_getaddrinfo
        try:
            url = "https://hubcapmanifest.com/api/v1/health"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
            resp.raise_for_status()
            latency = int((time.time() - t0) * 1000)
            return True, f"OK ({latency}ms)", latency
        finally:
            socket.getaddrinfo = orig_getaddrinfo
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        return False, categorize_error(e), latency


def test_gateway_tor() -> Tuple[bool, str, int]:
    """Tests Tor gateway (127.0.0.1:9080). Returns (success, display_status, latency_ms)."""
    t0 = time.time()
    if not TorManager.start_tor_if_needed():
        return False, "ERR: No Tor", int((time.time() - t0) * 1000)

    try:
        proxies = TorManager.get_tor_proxies()
        url = "https://hubcapmanifest.com/api/v1/health"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, proxies=proxies, timeout=15)
        resp.raise_for_status()
        latency = int((time.time() - t0) * 1000)
        return True, f"OK ({latency}ms)", latency
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        return False, categorize_error(e), latency


def test_gateway_wirecutter(custom_url: Optional[str] = None) -> Tuple[bool, str, int]:
    """Tests Wirecutter Cloudflare Worker proxy connectivity. Returns (success, display_status, latency_ms)."""
    t0 = time.time()
    try:
        worker_base = (custom_url.strip().rstrip("/") if custom_url and custom_url.strip() else get_wirecutter_endpoint())
        wc_test_headers = {"User-Agent": "Mozilla/5.0"}
        wc_token = get_assella_proxy_token()
        if wc_token:
            wc_test_headers["X-Assella-Token"] = wc_token

        if worker_base.startswith("socks5://") or worker_base.startswith("socks4://"):
            proxies = {"http": worker_base, "https": worker_base}
            resp = requests.get("https://hubcapmanifest.com/api/v1/health", headers=wc_test_headers, proxies=proxies, timeout=8)
            resp.raise_for_status()
            latency = int((time.time() - t0) * 1000)
            return True, f"OK ({latency}ms)", latency

        if not worker_base.startswith("http://") and not worker_base.startswith("https://"):
            worker_base = "https://" + worker_base

        health_url = f"{worker_base}/health"

        try:
            resp = requests.get(health_url, headers=wc_test_headers, timeout=8)
            resp.raise_for_status()
        except requests.exceptions.HTTPError:
            # Fallback to root endpoint if /health isn't mapped
            resp = requests.get(worker_base, headers=wc_test_headers, timeout=8)
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            # Check if user specified a forward HTTP proxy (e.g. http://127.0.0.1:7890)
            proxies = {"http": worker_base, "https": worker_base}
            resp = requests.get("https://hubcapmanifest.com/api/v1/health", headers=wc_test_headers, proxies=proxies, timeout=8)
            resp.raise_for_status()

        latency = int((time.time() - t0) * 1000)
        return True, f"OK ({latency}ms)", latency
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        return False, categorize_error(e), latency


def test_gateway_proxy(proxy_url: Optional[str] = None) -> Tuple[bool, str, int]:
    """Tests custom proxy connectivity. Returns (success, display_status, latency_ms)."""
    p = proxy_url or get_custom_proxy()
    if not p:
        return False, "ERR: No Proxy", 0
    t0 = time.time()
    try:
        proxies = {"http": p, "https": p}
        resp = requests.get("https://hubcapmanifest.com/api/v1/health", headers={"User-Agent": "Mozilla/5.0"}, proxies=proxies, timeout=8)
        resp.raise_for_status()
        latency = int((time.time() - t0) * 1000)
        return True, f"OK ({latency}ms)", latency
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        return False, categorize_error(e), latency


def resolve_doh(domain: str = TARGET_DOMAIN) -> Optional[str]:
    """
    Resolves a domain name to an IPv4 string using Cloudflare or Google DoH over HTTPS.
    Returns resolved IP string or None if resolution fails.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # 1. Try Cloudflare DoH (1.1.1.1)
    try:
        req = urllib.request.Request(DOH_CLOUDFLARE_URL, headers={"Accept": "application/dns-json", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=5) as r:
            data = json.loads(r.read().decode())
            answers = data.get("Answer", [])
            ips = [a["data"] for a in answers if a.get("type") == 1]
            if ips:
                logger.info(f"[ISPBypass] Cloudflare DoH resolved {domain} -> {ips[0]}")
                return ips[0]
    except Exception as e:
        logger.debug(f"[ISPBypass] Cloudflare DoH failed for {domain}: {e}")

    # 2. Try Google DoH (dns.google)
    try:
        req = urllib.request.Request(DOH_GOOGLE_URL, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=5) as r:
            data = json.loads(r.read().decode())
            answers = data.get("Answer", [])
            ips = [a["data"] for a in answers if a.get("type") == 1]
            if ips:
                logger.info(f"[ISPBypass] Google DoH resolved {domain} -> {ips[0]}")
                return ips[0]
    except Exception as e:
        logger.debug(f"[ISPBypass] Google DoH failed for {domain}: {e}")

    return None


_direct_connection_failed = False
_last_direct_check_time = 0.0
# Budget for the direct tier of the auto pipeline. The direct route is only a
# probe; a blocked or poisoned connection is detected by the timeout and the
# DoH tier resolves the same host successfully a moment later. Ten seconds of
# waiting before that handover is dead time on every boot.
_DIRECT_PROBE_TIMEOUT = 3.0

def execute_hubcap_request(
    session: requests.Session,
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
    stream: bool = False
) -> requests.Response:
    """
    Executes a Hubcap request with user-selected mode and smart 4-tier fallback:
    Modes:
      - 'auto'       : Direct -> DoH -> Tor -> Wirecutter
      - 'direct'     : Direct HTTPS only
      - 'doh'        : DoH only
      - 'tor'        : Tor HTTP Tunnel only
      - 'wirecutter' : Wirecutter Proxy only
      - 'disabled'   : Direct HTTPS only
    """
    global connection_status, _direct_connection_failed, _last_direct_check_time

    # 1. Enforce GET/HEAD only on Hubcap routes per worker policy
    method_upper = method.upper()
    if method_upper not in ("GET", "HEAD"):
        logger.warning(f"[ISPBypass] Non-GET/HEAD method '{method}' requested on Hubcap route: {url}")

    # 2. Attach X-Assella-Token to every Hubcap request that can go through the worker
    headers = dict(headers or {})
    tok = get_assella_proxy_token()
    if tok and "X-Assella-Token" not in headers:
        headers["X-Assella-Token"] = tok

    # 3. Enforce Hubcap 3 requests per 3 seconds rate limit ceiling across all execution threads
    _enforce_hubcap_rate_limit(max_requests=3, per_seconds=3.05)

    settings = get_settings()
    mode = "auto"
    if settings:
        mode = settings.value("isp_bypass_mode", "auto", type=str)

    # --- Mode: Explicit Single Gateway Selections ---
    if mode == "direct" or mode == "disabled":
        resp = session.request(method, url, headers=headers, params=params, timeout=timeout, stream=stream)
        _check_and_raise_for_status(resp)
        if not stream and is_html_or_blocked_payload(resp):
            raise requests.exceptions.ConnectionError("Direct connection returned HTML/blocked payload (ISP block detected).")
        connection_status = "Direct"
        return resp

    if mode == "doh":
        resolved_ip = resolve_doh(TARGET_DOMAIN)
        if not resolved_ip:
            raise requests.exceptions.ConnectionError("DoH resolution failed for hubcapmanifest.com")
        orig_getaddrinfo = socket.getaddrinfo
        def doh_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            if host == TARGET_DOMAIN:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (resolved_ip, port))]
            return orig_getaddrinfo(host, port, family, type, proto, flags)
        socket.getaddrinfo = doh_getaddrinfo
        try:
            resp = session.request(method, url, headers=headers, params=params,
                                   timeout=timeout, stream=stream, verify=False)
            _check_and_raise_for_status(resp)
            if not stream and is_html_or_blocked_payload(resp):
                raise requests.exceptions.ConnectionError("DoH request returned empty or HTML payload.")
            connection_status = "DoH"
            return resp
        finally:
            socket.getaddrinfo = orig_getaddrinfo

    if mode == "tor":
        if not TorManager.start_tor_if_needed():
            raise requests.exceptions.ConnectionError("Tor process is not available/listening.")
        tor_proxies = TorManager.get_tor_proxies()
        resp = session.request(method, url, headers=headers, params=params,
                               proxies=tor_proxies, timeout=timeout + 5, stream=stream, verify=False)
        _check_and_raise_for_status(resp)
        if not stream and is_html_or_blocked_payload(resp):
            raise requests.exceptions.ConnectionError("Tor request returned empty or HTML payload.")
        connection_status = "Tor"
        return resp

    if mode == "wirecutter":
        worker_url = rewrite_url_for_wirecutter(url)
        resp = session.request(method, worker_url, headers=headers, params=params,
                               timeout=timeout + 3, stream=stream, verify=False)
        _check_and_raise_for_status(resp)
        if not stream and is_html_or_blocked_payload(resp):
            raise requests.exceptions.ConnectionError("Wirecutter request returned empty or HTML payload.")
        connection_status = "Wire"
        return resp

    if mode in ("proxy", "custom_proxy"):
        custom_p = get_custom_proxy()
        if not custom_p:
            raise requests.exceptions.ConnectionError("Custom proxy URL is not configured.")
        proxies = {"http": custom_p, "https": custom_p}
        resp = session.request(method, url, headers=headers, params=params, proxies=proxies, timeout=timeout + 5, stream=stream)
        _check_and_raise_for_status(resp)
        if not stream and is_html_or_blocked_payload(resp):
            raise requests.exceptions.ConnectionError("Custom proxy request returned empty or HTML payload.")
        connection_status = "Proxy"
        return resp

    # --- Mode: 'auto' (Smart 4-Tier Fallback Pipeline) ---
    now = time.time()
    if _direct_connection_failed and now - _last_direct_check_time < 300:
        use_direct = False
    else:
        use_direct = True

    # 1. Tier 1: Direct connection
    if use_direct:
        try:
            logger.debug(f"[ISPBypass] Trying direct request to {url}")
            probe_timeout = min(timeout, _DIRECT_PROBE_TIMEOUT)
            resp = session.request(method, url, headers=headers, params=params, timeout=probe_timeout, stream=stream)
            _check_and_raise_for_status(resp)

            if not stream and is_html_or_blocked_payload(resp):
                raise requests.exceptions.ConnectionError("Direct connection returned empty or HTML payload (ISP block detected).")

            connection_status = "Direct"
            _direct_connection_failed = False
            return resp
        except HubcapRateLimitError:
            # 429 rate limit applies to API key / IP — do not burn remaining fallback tiers
            raise
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.SSLError) as e:
            _direct_connection_failed = True
            _last_direct_check_time = now
            logger.warning(f"[ISPBypass] Direct request to {url} failed: {e}. Initiating automatic DoH fallback...")

    # Tiers 2-4: bypass TLS verification for circumvention proxies
    bypass_kwargs = {"verify": False}

    # 2. Tier 2: DoH (DNS-over-HTTPS) resolution
    resolved_ip = resolve_doh(TARGET_DOMAIN)
    if resolved_ip:
        orig_getaddrinfo = socket.getaddrinfo

        def doh_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            if host == TARGET_DOMAIN:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (resolved_ip, port))]
            return orig_getaddrinfo(host, port, family, type, proto, flags)

        socket.getaddrinfo = doh_getaddrinfo
        try:
            logger.info(f"[ISPBypass] Attempting request via DoH IP override ({resolved_ip})...")
            doh_headers = dict(headers or {})
            if "User-Agent" not in doh_headers:
                try:
                    from utils.version import app_version
                    doh_headers["User-Agent"] = f"Mozilla/5.0 (X11; Linux x86_64; ASSella/{app_version})"
                except Exception:
                    doh_headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64; ASSella/2.6.2)"

            resp = session.request(method, url, headers=doh_headers, params=params, timeout=timeout, stream=stream, **bypass_kwargs)
            _check_and_raise_for_status(resp)

            if not stream and is_html_or_blocked_payload(resp):
                raise requests.exceptions.ConnectionError("DoH request returned empty or HTML payload.")

            logger.info(f"[ISPBypass] DoH request to {url} SUCCESSFUL!")
            connection_status = "DoH"
            return resp
        except HubcapRateLimitError:
            raise
        except Exception as doh_err:
            logger.warning(f"[ISPBypass] DoH request failed: {doh_err}. Initiating Tor fallback...")
        finally:
            socket.getaddrinfo = orig_getaddrinfo

    # 3. Tier 3: Tor HTTP Tunnel Proxy Fallback
    if TorManager.start_tor_if_needed():
        try:
            tor_proxies = TorManager.get_tor_proxies()
            logger.info(f"[ISPBypass] Attempting request via Tor (127.0.0.1:{TorManager.TOR_HTTP_PORT})...")
            resp = session.request(
                method, url, headers=headers, params=params, proxies=tor_proxies,
                timeout=timeout + 5, stream=stream, **bypass_kwargs
            )
            _check_and_raise_for_status(resp)
            logger.info(f"[ISPBypass] Tor request to {url} SUCCESSFUL!")
            connection_status = "Tor"
            return resp
        except HubcapRateLimitError:
            raise
        except Exception as tor_err:
            logger.error(f"[ISPBypass] Tor request failed: {tor_err}. Checking Wirecutter proxy...")

    # 4. Tier 4: Wirecutter Cloudflare Worker Proxy Fallback
    try:
        worker_url = rewrite_url_for_wirecutter(url)
        logger.info(f"[ISPBypass] Attempting request via Wirecutter/Worker proxy: {worker_url}...")
        resp = session.request(
            method, worker_url, headers=headers, params=params,
            timeout=timeout + 3, stream=stream, **bypass_kwargs
        )
        _check_and_raise_for_status(resp)
        logger.info(f"[ISPBypass] Wirecutter request to {worker_url} SUCCESSFUL!")
        connection_status = "Wire"
        return resp
    except HubcapRateLimitError:
        raise
    except Exception as wc_err:
        logger.error(f"[ISPBypass] Wirecutter proxy request failed: {wc_err}")

    # If all fallbacks failed, raise connection error
    connection_status = "Offline"
    raise requests.exceptions.ConnectionError(f"All ISP Bypass fallbacks (Direct, DoH, Tor, Wirecutter) failed for {url}.")
