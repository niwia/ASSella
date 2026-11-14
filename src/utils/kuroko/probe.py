"""Network diagnostics, live peer probes, and 5 MB burst benchmarking for Kuroko tunnels."""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Tuple

from .config import get_or_create_warp_config as _default_get_or_create_warp_config
from .scoring import save_endpoint_score as _default_save_endpoint_score
from .tunnel import Kuroko as _default_Kuroko

logger = logging.getLogger(__name__)

get_or_create_warp_config = _default_get_or_create_warp_config
save_endpoint_score = _default_save_endpoint_score
Kuroko = _default_Kuroko


def _resolve_symbol(name: str, local_sym, default_sym):
    if local_sym is not default_sym:
        return local_sym
    pkg = sys.modules.get("utils.kuroko")
    if pkg is not None:
        pkg_sym = getattr(pkg, name, default_sym)
        if pkg_sym is not default_sym:
            return pkg_sym
    return local_sym


def _parse_cf_trace(trace_text: str) -> str:
    """Parse Cloudflare's /cdn-cgi/trace output into a human-readable string."""
    ip, loc, warp = "unknown", "", ""
    for line in trace_text.splitlines():
        if line.startswith("ip="):
            ip = line.split("=", 1)[1]
        elif line.startswith("loc="):
            loc = line.split("=", 1)[1]
        elif line.startswith("warp="):
            warp = line.split("=", 1)[1]
    tag = "Cloudflare WARP" if warp == "on" else "Cloudflare Edge"
    return f"{ip} ({tag}{f' - {loc}' if loc else ''})"


def test_connection(proxy_url: str, timeout: float = 8.0) -> Tuple[bool, str]:
    """
    Verify end-to-end tunnel connectivity through the SOCKS5 proxy and return
    (success, '<exit_ip> (<org_or_provider>)').
    Uses multi-endpoint fallbacks to guard against public IP API rate limits (HTTP 429).
    """
    endpoints = [
        ("https://1.1.1.1/cdn-cgi/trace", lambda text: _parse_cf_trace(text)),
        (
            "https://ipwho.is/",
            lambda d: f"{d.get('ip', 'unknown')} ({d.get('connection', {}).get('org', d.get('org', 'Unknown'))} - {d.get('country', '')})",
        ),
        (
            "https://ipinfo.io/json",
            lambda d: f"{d.get('ip', 'unknown')} ({d.get('org', 'Unknown')} - {d.get('country', '')})",
        ),
    ]

    # 1. Prefer curl_cffi if installed
    try:
        from curl_cffi import requests as cffi_requests

        for url, parser in endpoints:
            try:
                resp = cffi_requests.get(
                    url,
                    proxies={"http": proxy_url, "https": proxy_url},
                    timeout=timeout,
                )
                if resp.status_code == 200:
                    if url.endswith("/trace"):
                        label = parser(resp.text)
                    else:
                        label = parser(resp.json())
                    return True, label
            except Exception as endpoint_err:
                logger.debug(f"[Kuroko] Endpoint {url} failed: {endpoint_err}")
                continue
    except ImportError as e:
        logger.debug(f"[Kuroko] curl_cffi not installed: {e}")

    # 2. Fallback: shell out to curl
    curl_bin = shutil.which("curl")
    if curl_bin:
        for url, parser in endpoints:
            try:
                out = subprocess.check_output(
                    [curl_bin, "-fsSL", "--max-time", str(int(timeout)), "-x", proxy_url, url],
                    text=True,
                )
                if url.endswith("/trace"):
                    label = parser(out)
                else:
                    label = parser(json.loads(out))
                return True, label
            except Exception as endpoint_err:
                logger.debug(f"[Kuroko] curl check {url} failed: {endpoint_err}")
                continue

    return False, "Failed to verify connection through proxy across all verification endpoints."


def probe_alive(proxy_url: str, timeout: float = 3.5) -> bool:
    """
    Fast probe to verify remote tunnel transport is genuinely passing traffic.
    Because WireGuard is stateless UDP, local SOCKS5 binding alone does not prove
    the remote peer or handshake is active. This sends a zero-byte probe through proxy_url.
    """
    headers = {
        "Referer": "https://speed.cloudflare.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    probe_urls = [
        "https://speed.cloudflare.com/__down?bytes=0",
        "https://1.1.1.1/cdn-cgi/trace",
    ]
    # 1. Prefer curl_cffi if installed (single pass; fallback to curl only on ImportError)
    try:
        from curl_cffi import requests as cffi_requests

        for u in probe_urls:
            try:
                resp = cffi_requests.head(
                    u,
                    headers=headers,
                    proxies={"http": proxy_url, "https": proxy_url},
                    timeout=timeout,
                )
                if resp.status_code < 500:
                    return True
            except Exception as probe_err:
                logger.debug(f"[Kuroko] probe_alive cffi probe to {u} failed: {probe_err}")
                continue
        return False
    except ImportError as e:
        logger.debug(f"[Kuroko] probe_alive curl_cffi unavailable, falling back to curl: {e}")

    # 2. Fallback: shell out to curl only if curl_cffi is missing
    curl_bin = shutil.which("curl")
    if curl_bin:
        for u in probe_urls:
            try:
                res = subprocess.run(
                    [
                        curl_bin,
                        "-sS",
                        "-I",
                        "--max-time",
                        str(int(timeout)),
                        "-x",
                        proxy_url,
                        "-H",
                        "Referer: https://speed.cloudflare.com/",
                        "-H",
                        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "-o",
                        "/dev/null",
                        u,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=timeout + 1.0,
                )
                if res.returncode == 0:
                    return True
            except Exception as curl_err:
                logger.debug(f"[Kuroko] probe_alive curl probe to {u} failed: {curl_err}")
                continue

    return False


def probe_stealth_ready(proxy_url: str, timeout: float = 3.5) -> bool:
    """Check if the remote stealth proxy tunnel is genuinely alive and passing traffic."""
    return Kuroko.probe_alive(proxy_url, timeout=timeout)


def measure_speed(proxy_url: str, timeout: float = 8.0) -> Tuple[bool, int, float, str]:
    """
    Measure ping (RTT ms) and throughput (Mbps) using a lightweight 5 MB burst test.
    1. Sends zero-byte HEAD request to https://speed.cloudflare.com/__down?bytes=0
       with Referer: https://speed.cloudflare.com/ to measure pure RTT without bot rate limits.
    2. Streams 5 MB payload from https://speed.cloudflare.com/__down?bytes=5242880 to compute Mbps.
    Returns (success: bool, ping_ms: int, speed_mbps: float, message_or_error: str).
    """
    headers = {
        "Referer": "https://speed.cloudflare.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    ping_url = "https://speed.cloudflare.com/__down?bytes=0"
    speed_url = "https://speed.cloudflare.com/__down?bytes=5242880"
    ping_ms = 0
    speed_mbps = 0.0

    # Attempt 1: curl_cffi
    try:
        from curl_cffi import requests as cffi_requests

        try:
            t0 = time.perf_counter()
            p_resp = cffi_requests.head(
                ping_url,
                headers=headers,
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=max(2.0, timeout / 2.0),
            )
            if p_resp.status_code < 500:
                ping_ms = max(1, int((time.perf_counter() - t0) * 1000))
        except Exception as pe:
            logger.debug(f"[Kuroko] cffi ping probe failed: {pe}")

        try:
            t1 = time.perf_counter()
            s_resp = cffi_requests.get(
                speed_url,
                headers=headers,
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=timeout,
                stream=True,
            )
            if s_resp.status_code == 200:
                total_bytes = 0
                for chunk in s_resp.iter_content(chunk_size=65536):
                    if chunk:
                        total_bytes += len(chunk)
                elapsed = max(0.001, time.perf_counter() - t1)
                speed_mbps = round((total_bytes * 8.0) / (elapsed * 1_000_000.0), 2)
                if ping_ms == 0:
                    ping_ms = max(1, int(elapsed * 200))
                return True, ping_ms, speed_mbps, f"Ping: {ping_ms}ms | Speed: {speed_mbps} Mbps"
        except Exception as se:
            logger.debug(f"[Kuroko] cffi speed download failed: {se}")
    except ImportError as e:
        logger.debug(f"[Kuroko] curl_cffi not installed for speed test: {e}")

    # Attempt 2: shell curl
    curl_bin = shutil.which("curl")
    if curl_bin:
        try:
            p_out = subprocess.check_output(
                [
                    curl_bin,
                    "-sS",
                    "-I",
                    "--max-time",
                    str(int(max(2.0, timeout / 2.0))),
                    "-x",
                    proxy_url,
                    "-H",
                    "Referer: https://speed.cloudflare.com/",
                    "-H",
                    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "-w",
                    "%{time_starttransfer}",
                    "-o",
                    "/dev/null",
                    ping_url,
                ],
                text=True,
                timeout=timeout,
            )
            try:
                ping_ms = max(1, int(float(p_out.strip()) * 1000))
            except ValueError as ve:
                logger.debug(f"[Kuroko] Could not parse curl ping time: {ve}")
        except Exception as pe:
            logger.debug(f"[Kuroko] curl ping probe failed: {pe}")

        try:
            s_out = subprocess.check_output(
                [
                    curl_bin,
                    "-fsSL",
                    "--max-time",
                    str(int(timeout)),
                    "-x",
                    proxy_url,
                    "-H",
                    "Referer: https://speed.cloudflare.com/",
                    "-H",
                    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "-w",
                    "%{speed_download}",
                    "-o",
                    "/dev/null",
                    speed_url,
                ],
                text=True,
                timeout=timeout + 2.0,
            )
            try:
                bytes_sec = float(s_out.strip())
                speed_mbps = round((bytes_sec * 8.0) / 1_000_000.0, 2)
                return True, ping_ms, speed_mbps, f"Ping: {ping_ms}ms | Speed: {speed_mbps} Mbps"
            except ValueError as ve:
                logger.debug(f"[Kuroko] Could not parse curl speed_download: {ve}")
        except Exception as se:
            logger.debug(f"[Kuroko] curl speed test failed: {se}")

    if ping_ms > 0:
        return True, ping_ms, 0.0, f"Ping: {ping_ms}ms (Speed test rate-limited)"
    return False, 0, 0.0, "Speed test failed to reach endpoint through proxy."


def test_and_benchmark_endpoint(
    mode: str,
    wg_path: str = "",
    custom_url: str = "",
    timeout: float = 8.0,
    settings=None,
) -> Tuple[bool, str, int, float, str]:
    """
    Test connection and benchmark speed for an endpoint in a single ephemeral tunnel session.
    1. Starts an ephemeral Kuroko tunnel once (or uses direct custom proxy).
    2. Fetches exit IP/location via test_connection().
    3. Runs the 5 MB burst measure_speed() test (RTT + throughput).
    4. Stops the tunnel cleanly in finally:.
    5. Persists the score in stealth_endpoint_scores.
    Returns (ok: bool, exit_label: str, ping_ms: int, speed_mbps: float, error_msg: str).
    """
    kuroko_cls = _resolve_symbol("Kuroko", Kuroko, _default_Kuroko)
    warp_cfg_fn = _resolve_symbol(
        "get_or_create_warp_config", get_or_create_warp_config, _default_get_or_create_warp_config
    )
    save_score_fn = _resolve_symbol(
        "save_endpoint_score", save_endpoint_score, _default_save_endpoint_score
    )

    mode = (mode or "warp").lower().strip()
    if mode == "warp":
        manager = kuroko_cls()
        try:
            cfg = warp_cfg_fn()
            if not manager.start(cfg, port=0):
                return False, "", 0, 0.0, "Failed to start Cloudflare WARP tunnel."
            ok_conn, exit_label = manager.test_connection(manager.proxy_url, timeout=timeout)
            if not ok_conn:
                return False, "", 0, 0.0, exit_label
            ok_speed, ping_ms, speed_mbps, speed_msg = manager.measure_speed(
                manager.proxy_url, timeout=timeout
            )
            save_score_fn(
                "warp",
                ping_ms,
                speed_mbps,
                ok_speed or ping_ms > 0,
                settings=settings,
                exit_label=exit_label,
            )
            return True, exit_label, ping_ms, speed_mbps, "" if ok_speed else speed_msg
        except Exception as e:
            logger.debug(f"[Kuroko] WARP test & benchmark error: {e}")
            return False, "", 0, 0.0, f"WARP error: {e}"
        finally:
            manager.stop()

    elif mode == "wireguard":
        conf_path = (wg_path or "").strip()
        if not conf_path or not os.path.isfile(conf_path):
            return False, "", 0, 0.0, f"WireGuard config not found: '{conf_path}'"
        manager = kuroko_cls()
        try:
            if not manager.start(conf_path, port=0):
                return False, "", 0, 0.0, "Failed to start WireGuard tunnel."
            ok_conn, exit_label = manager.test_connection(manager.proxy_url, timeout=timeout)
            if not ok_conn:
                return False, "", 0, 0.0, exit_label
            ok_speed, ping_ms, speed_mbps, speed_msg = manager.measure_speed(
                manager.proxy_url, timeout=timeout
            )
            endpoint_id = f"wg:{Path(conf_path).name}"
            save_score_fn(
                endpoint_id,
                ping_ms,
                speed_mbps,
                ok_speed or ping_ms > 0,
                settings=settings,
                exit_label=exit_label,
            )
            return True, exit_label, ping_ms, speed_mbps, "" if ok_speed else speed_msg
        except Exception as e:
            logger.debug(f"[Kuroko] WireGuard test & benchmark error: {e}")
            return False, "", 0, 0.0, f"WireGuard error: {e}"
        finally:
            manager.stop()

    elif mode == "custom":
        url = (custom_url or "").strip()
        if not url:
            return False, "", 0, 0.0, "No custom proxy URL specified."
        if "://" not in url:
            url = f"socks5://{url}"
        manager = kuroko_cls()
        try:
            ok_conn, exit_label = manager.test_connection(url, timeout=timeout)
            if not ok_conn:
                return False, "", 0, 0.0, exit_label
            ok_speed, ping_ms, speed_mbps, speed_msg = manager.measure_speed(url, timeout=timeout)
            save_score_fn(
                "custom",
                ping_ms,
                speed_mbps,
                ok_speed or ping_ms > 0,
                settings=settings,
                exit_label=exit_label,
            )
            return True, exit_label, ping_ms, speed_mbps, "" if ok_speed else speed_msg
        except Exception as e:
            logger.debug(f"[Kuroko] Custom proxy test & benchmark error: {e}")
            return False, "", 0, 0.0, f"Custom proxy error: {e}"

    else:
        return False, "", 0, 0.0, f"Unsupported stealth mode: '{mode}'"


def test_stealth_proxy_connection(
    mode: str,
    wg_path: str = "",
    custom_url: str = "",
    timeout: float = 8.0,
) -> Tuple[bool, str]:
    """
    Test a candidate stealth proxy setup without affecting global state or running tasks.
    Spawns an ephemeral Kuroko manager if needed, tests connection endpoints, and tears down cleanly.
    Returns (success: bool, status_message_or_endpoint_label: str).
    """
    kuroko_cls = _resolve_symbol("Kuroko", Kuroko, _default_Kuroko)
    warp_cfg_fn = _resolve_symbol(
        "get_or_create_warp_config", get_or_create_warp_config, _default_get_or_create_warp_config
    )

    mode = (mode or "warp").lower().strip()
    if mode == "warp":
        manager = kuroko_cls()
        try:
            cfg = warp_cfg_fn()
            if not manager.start(cfg, port=0):
                return False, "Failed to start stealth proxy for Cloudflare WARP."
            return manager.test_connection(manager.proxy_url, timeout=timeout)
        except Exception as e:
            logger.debug(f"[Kuroko] WARP connection test error: {e}")
            return False, f"WARP error: {e}"
        finally:
            manager.stop()

    elif mode == "wireguard":
        conf_path = (wg_path or "").strip()
        if not conf_path or not os.path.isfile(conf_path):
            return False, f"Config file not found: '{conf_path}'"
        manager = kuroko_cls()
        try:
            if not manager.start(conf_path, port=0):
                return False, "Failed to start stealth proxy with WireGuard config."
            return manager.test_connection(manager.proxy_url, timeout=timeout)
        except Exception as e:
            logger.debug(f"[Kuroko] WireGuard connection test error: {e}")
            return False, f"WireGuard error: {e}"
        finally:
            manager.stop()

    elif mode == "custom":
        url = (custom_url or "").strip()
        if not url:
            return False, "No proxy URL specified."
        if "://" not in url:
            url = f"socks5://{url}"
        manager = kuroko_cls()
        return manager.test_connection(url, timeout=timeout)

    else:
        return False, f"Unsupported proxy mode: '{mode}'"


__all__ = [
    "_parse_cf_trace",
    "measure_speed",
    "probe_alive",
    "probe_stealth_ready",
    "test_and_benchmark_endpoint",
    "test_connection",
    "test_stealth_proxy_connection",
]