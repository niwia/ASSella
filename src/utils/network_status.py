"""
network_status.py
──────────────────
Consolidated network status utility. Checks connection to Steam and Hubcap API.
"""

import logging
import urllib.request
from typing import Tuple

logger = logging.getLogger(__name__)

def run_connection_check() -> Tuple[bool, bool, str]:
    """
    Performs connection checks for Steam and Hubcap API.
    Returns:
        (steam_ok: bool, hubcap_ok: bool, hubcap_mode: str)
        where hubcap_mode is one of: "Online", "DoH", "Tor", "Offline"
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
    isp_bypass.connection_status = "Connecting"
    
    from core.morrenus_api import check_health
    hubcap_ok = False
    try:
        health = check_health()
        if health.get("status") == "healthy":
            hubcap_ok = True
    except Exception as e:
        logger.debug(f"Hubcap health check failed: {e}")

    hubcap_mode = isp_bypass.connection_status
    if not hubcap_ok:
        hubcap_mode = "Offline"

    return steam_ok, hubcap_ok, hubcap_mode
