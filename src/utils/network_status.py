"""
network_status.py
──────────────────
Consolidated network status utility. Checks connection to Steam and Hubcap API.
"""

import logging
import urllib.request
from typing import Tuple

logger = logging.getLogger(__name__)

def run_connection_check(force: bool = False) -> Tuple[bool, bool, str]:
    """
    Performs connection checks for Steam and Hubcap API.
    Returns:
        (steam_ok: bool, hubcap_ok: bool, hubcap_mode: str)
        where hubcap_mode is one of: "Direct", "DoH", "Tor", "Wire", "Offline"
    """
    # 1. Check Steam Status
    steam_ok = False
    try:
        req = urllib.request.Request("https://store.steampowered.com", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                steam_ok = True
    except Exception as e:
        logger.debug(f"Steam status check failed: {e}")

    # 2. Check Hubcap status
    import utils.isp_bypass as isp_bypass

    from core.morrenus_api import check_health
    hubcap_ok = False
    try:
        health = check_health(force=force)
        if health.get("status") == "healthy":
            hubcap_ok = True
    except Exception as e:
        logger.debug(f"Hubcap health check failed: {e}")

    hubcap_mode = isp_bypass.connection_status
    if not hubcap_ok:
        hubcap_mode = "Offline"
    elif hubcap_mode in ("Connecting", "Offline"):
        settings = isp_bypass.get_settings()
        configured_mode = settings.value("isp_bypass_mode", "direct", type=str) if settings else "direct"
        hubcap_mode = configured_mode.capitalize() if configured_mode in ("direct", "doh", "tor", "wire") else "Online"

    return steam_ok, hubcap_ok, hubcap_mode
