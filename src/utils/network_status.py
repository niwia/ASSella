"""
network_status.py
──────────────────
Consolidated network status utility. Checks connection to Steam and the active manifest provider.
"""

import logging
import time
import urllib.request
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

_PROVIDER_HEALTH_CACHE: Dict[str, Tuple[float, bool, str]] = {}
_HEALTH_OK_TTL = 300.0
_HEALTH_ERR_TTL = 60.0


def run_connection_check(force: bool = False, provider_id: str = "hubcap") -> Tuple[bool, bool, str]:
    """
    Performs connection checks for Steam and the active manifest provider.
    Returns:
        (steam_ok: bool, prov_ok: bool, prov_mode: str)
    """
    prov_id = (provider_id or "hubcap").strip().lower()

    # 1. Check Steam Status
    steam_ok = False
    try:
        req = urllib.request.Request("https://store.steampowered.com", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                steam_ok = True
    except Exception as e:
        logger.debug(f"Steam status check failed: {e}")

    # 2. Check active provider status (with 300s healthy / 60s unhealthy cache)
    now = time.time()
    if not force and prov_id in _PROVIDER_HEALTH_CACHE:
        ts, cached_ok, cached_mode = _PROVIDER_HEALTH_CACHE[prov_id]
        ttl = _HEALTH_OK_TTL if cached_ok else _HEALTH_ERR_TTL
        if (now - ts) < ttl:
            return steam_ok, cached_ok, cached_mode

    prov_ok = False
    prov_mode = "Offline"

    if prov_id == "hubcap":
        import utils.isp_bypass as isp_bypass
        from core.morrenus_api import check_health

        try:
            health = check_health(force=force)
            if health.get("status") == "healthy":
                prov_ok = True
        except Exception as e:
            logger.debug(f"Hubcap health check failed: {e}")

        prov_mode = isp_bypass.connection_status
        if not prov_ok:
            prov_mode = "Offline"
        elif prov_mode in ("Connecting", "Offline"):
            settings = isp_bypass.get_settings()
            configured_mode = settings.value("isp_bypass_mode", "direct", type=str) if settings else "direct"
            prov_mode = configured_mode.capitalize() if configured_mode in ("direct", "doh", "tor", "wire") else "Online"
    else:
        try:
            from core.providers.registry import get as get_provider
            prov = get_provider(prov_id)
            if prov is not None:
                health = prov.check_health(force=force)
                prov_ok = health.get("status") == "healthy"
                prov_mode = "Direct" if prov_ok else "Offline"
        except Exception as e:
            logger.debug(f"Provider '{prov_id}' health check failed: {e}")

    _PROVIDER_HEALTH_CACHE[prov_id] = (now, prov_ok, prov_mode)
    return steam_ok, prov_ok, prov_mode
