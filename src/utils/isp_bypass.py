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
from typing import Optional, Dict, Any, Tuple, Union

import requests
from utils.settings import get_settings

logger = logging.getLogger(__name__)

TARGET_DOMAIN = "hubcapmanifest.com"
DOH_CLOUDFLARE_URL = f"https://1.1.1.1/dns-query?name={TARGET_DOMAIN}&type=A"
DOH_GOOGLE_URL = f"https://dns.google/resolve?name={TARGET_DOMAIN}&type=A"

_tor_process: Optional[subprocess.Popen] = None
_tor_lock = threading.Lock()


class TorManager:
    """Manages an optional background Tor process launched by ASSella."""

    TOR_PORT = 9050

    @classmethod
    def is_proxy_active(cls, port: int = TOR_PORT) -> bool:
        """Checks if a SOCKS or HTTP proxy is listening on local port."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            res = s.connect_ex(("127.0.0.1", port))
            s.close()
            return res == 0
        except Exception:
            return False

    @classmethod
    def start_tor_if_needed(cls) -> bool:
        """
        Ensures a Tor listener is active on 127.0.0.1:9050.
        If no proxy is listening, attempts to launch a background Tor helper process.
        """
        global _tor_process

        if cls.is_proxy_active(cls.TOR_PORT):
            logger.debug(f"[ISPBypass] Active Tor/SOCKS proxy detected on 127.0.0.1:{cls.TOR_PORT}")
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
                tor_data_dir = Path("/tmp/assella_tor")
                tor_data_dir.mkdir(parents=True, exist_ok=True)

                cmd = [
                    tor_path,
                    "--SocksPort", str(cls.TOR_PORT),
                    "--DataDirectory", str(tor_data_dir),
                    "--Log", "notice file /tmp/assella_tor/tor.log"
                ]

                logger.info(f"[ISPBypass] Starting background Tor process: {' '.join(cmd)}")
                _tor_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )

                # Wait up to 5s for listener
                for _ in range(10):
                    time.sleep(0.5)
                    if cls.is_proxy_active(cls.TOR_PORT):
                        logger.info(f"[ISPBypass] Background Tor process successfully listening on 127.0.0.1:{cls.TOR_PORT}")
                        return True

                logger.warning("[ISPBypass] Tor process started but listener did not open on 9050 within timeout.")
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
        # Check inside AppImage squashfs or PATH
        appimage_tor = Path("/tmp/assella_repack/squashfs-root/bin/tor")
        if appimage_tor.exists() and os.access(appimage_tor, os.X_OK):
            return str(appimage_tor)

        # Check system PATH
        import shutil
        sys_tor = shutil.which("tor")
        if sys_tor:
            return sys_tor

        return None


# Register cleanup at Python exit
atexit.register(TorManager.stop_tor)


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
    Executes a Hubcap request with ISP Bypass pipeline:
    1. Direct Connection
    2. DoH (DNS-over-HTTPS) Socket Override
    3. Tor / SOCKS5 Proxy Fallback
    """
    settings = get_settings()
    isp_bypass_enabled = settings.value("isp_bypass_hubcap", False, type=bool) if settings else False

    # 1. Always try direct connection first
    try:
        logger.debug(f"[ISPBypass] Trying direct request to {url}")
        resp = session.request(method, url, headers=headers, params=params, timeout=timeout, stream=stream)
        resp.raise_for_status()
        return resp
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.SSLError) as e:
        if not isp_bypass_enabled:
            raise e
        logger.warning(f"[ISPBypass] Direct request to {url} failed: {e}. ISP Bypass enabled — initiating DoH fallback...")

    # 2. Phase 2: DoH (DNS-over-HTTPS) resolution
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
                doh_headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64; ASSella/2.3.2)"
            
            resp = session.request(method, url, headers=doh_headers, params=params, timeout=timeout, stream=stream)
            resp.raise_for_status()
            logger.info(f"[ISPBypass] DoH request to {url} SUCCESSFUL!")
            return resp
        except Exception as doh_err:
            logger.warning(f"[ISPBypass] DoH request failed: {doh_err}. Initiating Tor fallback...")
        finally:
            socket.getaddrinfo = orig_getaddrinfo

    # 3. Phase 3: Tor / SOCKS5 Proxy Fallback
    if TorManager.start_tor_if_needed():
        try:
            tor_proxies = {
                "http": "http://127.0.0.1:9050",
                "https": "http://127.0.0.1:9050"
            }
            logger.info(f"[ISPBypass] Attempting request via Tor/Proxy (127.0.0.1:9050)...")
            resp = session.request(
                method, url, headers=headers, params=params, proxies=tor_proxies, timeout=timeout + 5, stream=stream
            )
            resp.raise_for_status()
            logger.info(f"[ISPBypass] Tor/Proxy request to {url} SUCCESSFUL!")
            return resp
        except Exception as tor_err:
            logger.error(f"[ISPBypass] Tor request failed: {tor_err}")

    # If all fallbacks failed, raise original direct error
    raise requests.exceptions.ConnectionError(f"ISP Bypass failed to connect to Hubcap API for {url}.")
