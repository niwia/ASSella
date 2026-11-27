"""Steam Store button: shared switch, .cef-enable-remote-debugging, and CEF detection.

The button itself is a DOM patch injected into Steam's store webview over the
CEF debugging protocol. Either Uran (inside Steam, via Decky) or ASSella's
built-in CEF injector can draw it. When Uran is actively running under Decky
and managing the Store button, ASSella steps back and greys out its own CEF
controls so both never fight over the same webview.

The switch lives in the shared companion settings file, which is the same JSON
Uran's Decky toggle writes.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SETTING_KEY = "steam_store_enabled"
CEF_PORTS = (8080, 8081, 8082)
STORE_HOST = "store.steampowered.com"
CEF_SENTINEL_FILENAME = ".cef-enable-remote-debugging"

_DECKY_RUNNING_TTL = 5.0
_decky_running_cache: Tuple[float, bool] = (0.0, False)


def shared_settings_path() -> Path:
    from utils.companions import companion_settings_path

    return companion_settings_path()


def _read() -> Dict[str, Any]:
    from utils.companions import read_companion_settings

    return read_companion_settings()


def is_enabled() -> bool:
    """The switch as Uran/ASSella wrote it. Defaults to on, like Uran does."""
    return bool(_read().get(SETTING_KEY, True))


def set_enabled(value: bool) -> bool:
    from utils.companions import update_companion_settings

    return bool(update_companion_settings({SETTING_KEY: bool(value)}))


# ------------------------------------------------------- .cef-enable-remote-debugging

def get_cef_sentinel_path() -> Optional[Path]:
    """Return Steam's .cef-enable-remote-debugging sentinel path, or None if Steam is not found."""
    try:
        from core.steam_helpers import get_steam_env

        steam_path = get_steam_env().steam_path
        if steam_path and Path(steam_path).is_dir():
            return Path(steam_path) / CEF_SENTINEL_FILENAME
    except Exception as e:
        logger.debug("[store_patch] get_steam_env failed: %s", e)

    for candidate in (
        Path.home() / ".local" / "share" / "Steam",
        Path.home() / ".steam" / "steam",
        Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam",
    ):
        if candidate.is_dir():
            return candidate / CEF_SENTINEL_FILENAME
    return None


def is_cef_sentinel_present() -> bool:
    """Return True if .cef-enable-remote-debugging exists in Steam's root folder."""
    path = get_cef_sentinel_path()
    return bool(path and path.exists())


def set_cef_sentinel_enabled(enabled: bool) -> Tuple[bool, str]:
    """Create or remove .cef-enable-remote-debugging in Steam's root folder.

    Returns ``(ok, message)``. Steam reads this sentinel at startup, so a
    restart is needed if Steam is already running.
    """
    path = get_cef_sentinel_path()
    if path is None:
        return False, "Steam installation directory could not be located."

    try:
        if enabled:
            path.touch(exist_ok=True)
            return True, f"Created {path}. Restart Steam if it is already running."
        if path.exists():
            path.unlink()
            return True, f"Removed {path}. Restart Steam to close the CEF port."
        return True, "CEF debugging sentinel was already removed."
    except OSError as e:
        logger.warning("[store_patch] Failed to toggle %s: %s", path, e)
        return False, f"Failed to update {path}: {e}"


# --------------------------------------------------------------- Uran gate

def is_decky_running() -> bool:
    """Fast /proc check to see if Decky Loader (PluginLoader) is currently running.

    Cached briefly: callers such as is_uran_managing_store() run from the
    settings UI, and a full /proc walk on every call freezes the GUI thread.
    """
    global _decky_running_cache
    if sys.platform != "linux":
        return False
    now = time.monotonic()
    cached_at, cached_val = _decky_running_cache
    if now - cached_at < _DECKY_RUNNING_TTL:
        return cached_val

    result = False
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                comm = (Path("/proc") / pid / "comm").read_text(encoding="utf-8", errors="ignore").strip()
                if comm == "PluginLoader":
                    result = True
                    break
            except OSError as e:
                logger.debug("[store_patch] /proc/%s/comm read skipped: %s", pid, e)
                continue
    except OSError as e:
        logger.debug("[store_patch] /proc scan failed: %s", e)

    _decky_running_cache = (now, result)
    return result


def is_uran_managing_store() -> bool:
    """True when Uran is installed, Decky is actively running, and Uran's store toggle is on."""
    try:
        from utils import companions

        uran = companions.uran_info()
        if not uran.get("installed"):
            return False
        if not is_decky_running():
            return False
        return bool((uran.get("settings") or {}).get(SETTING_KEY, True))
    except Exception as e:
        logger.debug("[store_patch] is_uran_managing_store check failed: %s", e)
        return False


# --------------------------------------------------------------- detection

def cef_status(timeout: float = 0.45) -> Dict[str, Any]:
    """Look for Steam's CEF debugging port and a Store tab on it.

    Network I/O: call from a background thread, never on the GUI thread.
    """
    import requests

    for port in CEF_PORTS:
        try:
            resp = requests.get(f"http://localhost:{port}/json", timeout=timeout)
            if resp.status_code != 200:
                continue
            tabs = resp.json()
        except Exception as e:
            logger.debug("[store_patch] CEF probe on port %s failed: %s", port, e)
            continue
        if not isinstance(tabs, list):
            continue
        store_tab = next(
            (
                tab
                for tab in tabs
                if STORE_HOST in str(tab.get("url", "")) and tab.get("webSocketDebuggerUrl")
            ),
            None,
        )
        return {
            "available": True,
            "port": port,
            "store_tab": bool(store_tab),
            "url": str((store_tab or {}).get("url", "")),
            "websocket_url": str((store_tab or {}).get("webSocketDebuggerUrl", "")),
            "targets": len(tabs),
        }

    return {
        "available": False,
        "port": None,
        "store_tab": False,
        "url": "",
        "websocket_url": "",
        "targets": 0,
    }


# ------------------------------------------------------------ prerequisites

def blockers(at0m_enabled: bool, config_path: Optional[Path] = None) -> List[str]:
    """Reasons the switch must not be turned on. Empty means it is allowed."""
    reasons: List[str] = []
    if not at0m_enabled:
        reasons.append("Enable the at0-m engine on this tab first — the button feeds it.")
    if config_path is not None and not Path(config_path).exists():
        reasons.append("SLSsteam's config.yaml was not found, so there is nothing to write to.")
    return reasons


def host_status(uran_active: Optional[bool] = None) -> Dict[str, Any]:
    """Which component can draw the button on this machine."""
    if uran_active is None:
        uran_active = is_uran_managing_store()
    return {
        "uran": bool(uran_active),
        "assella": True,
    }


def describe(
    at0m_enabled: bool,
    cef: Optional[Dict[str, Any]] = None,
    uran_active: Optional[bool] = None,
    sentinel_present: Optional[bool] = None,
) -> Tuple[str, str]:
    """(state text, severity) for the settings card.

    Pure formatting using pre-fetched ``cef`` status; never performs network I/O.
    """
    if not at0m_enabled:
        return ("Off — the at0-m engine is disabled", "warn")
    if uran_active is None:
        uran_active = is_uran_managing_store()
    if uran_active:
        if cef and cef.get("available"):
            if cef.get("store_tab"):
                return (f"Managed by Uran (Decky) — CEF {cef['port']} with Store page open", "ok")
            return (f"Managed by Uran (Decky) — CEF {cef['port']} found", "muted")
        return ("Managed by Uran (Decky is running)", "muted")
    if not is_enabled():
        return ("Off — no button is drawn inside Steam", "muted")
    if cef is None:
        return ("Checking Steam CEF port…", "muted")
    if not cef.get("available"):
        if sentinel_present is None:
            sentinel_present = is_cef_sentinel_present()
        if sentinel_present:
            return ("On — .cef-enable-remote-debugging is set; restart Steam to open port", "warn")
        return ("On — CEF debugging is disabled; enable .cef-enable-remote-debugging below", "warn")
    if not cef.get("store_tab"):
        return (f"On — CEF {cef['port']} found; open a Store page to see it", "muted")
    return (f"On — CEF {cef['port']} with a Store page open (ASSella active)", "ok")
