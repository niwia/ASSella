"""
sls_lua_config.py
=================
Utilities to inspect, generate, and synchronize Lua configuration files for
SLSsteam plugins:
  - lobster.conf.lua: Headcrab config recovery settings
  - katamari.conf.lua (hubcap section): the Hubcap API key the plugins use

Strictly NO emojis used anywhere.
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from utils.yaml_config_manager import get_user_config_path

logger = logging.getLogger(__name__)


def get_sls_config_dir() -> Path:
    """Return the directory containing SLSsteam configurations.
    Uses get_user_config_path().parent to respect Flatpak and Native Steam XDG paths.
    """
    try:
        cfg = get_user_config_path()
        if cfg and cfg.parent:
            return cfg.parent
    except Exception as exc:
        logger.debug(f"[SlsLuaConfig] Error getting user config path: {exc}")

    # Fallback to standard XDG / home path
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return Path(xdg_config) / "SLSsteam"
    return Path.home() / ".config" / "SLSsteam"


def get_lobster_conf_path() -> Path:
    """Return canonical path to lobster.conf.lua."""
    return get_sls_config_dir() / "lobster.conf.lua"


def get_hubcap_key_conf_path() -> Path:
    """Where the plugins read the Hubcap API key: katamari.conf.lua (hubcap section)."""
    return get_sls_config_dir() / "katamari.conf.lua"


# ---------------------------------------------------------------------------
# Parsing Helpers
# ---------------------------------------------------------------------------

def _parse_bool(val_str: str) -> Optional[bool]:
    s = val_str.strip().lower()
    if s.startswith("true"):
        return True
    if s.startswith("false"):
        return False
    return None


def escape_lua_string(val: str) -> str:
    """Escape special characters for Lua double-quoted string literals."""
    if not isinstance(val, str):
        val = str(val)
    return (
        val.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("\0", "\\0")
    )


def _unescape_lua_string(s: str) -> str:
    """Unescape special characters from a Lua string literal."""
    def _repl(match):
        ch = match.group(1)
        if ch in ('"', "'", "\\"):
            return ch
        if ch == "n":
            return "\n"
        if ch == "r":
            return "\r"
        if ch == "t":
            return "\t"
        if ch == "0":
            return "\0"
        return ch

    return re.sub(r'\\(.)', _repl, s)


def _parse_string(val_str: str) -> Optional[str]:
    m = re.search(r'"((?:\\.|[^"\\])*)"|\'((?:\\.|[^\'\\])*)\'', val_str)
    if m:
        raw = m.group(1) if m.group(1) is not None else m.group(2)
        return _unescape_lua_string(raw)
    return None


def _parse_int(val_str: str) -> Optional[int]:
    m = re.search(r'(\d+)', val_str)
    if m:
        try:
            return int(m.group(1))
        except ValueError as err:
            logger.debug(f"[SlsLuaConfig] Failed to parse int from {val_str}: {err}")
    return None


# ---------------------------------------------------------------------------
# Lobster Configuration
# ---------------------------------------------------------------------------

DEFAULT_LOBSTER_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "notify": True,
    "auto_recover": True,
    "run_on_reload": False,
}


def read_lobster_conf(conf_path: Optional[Path] = None) -> Dict[str, Any]:
    """Read ~/.config/SLSsteam/lobster.conf.lua, returning dict with defaults."""
    path = conf_path or get_lobster_conf_path()
    cfg = dict(DEFAULT_LOBSTER_CONFIG)
    if not path.is_file():
        return cfg

    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        for key in ("enabled", "notify", "auto_recover", "run_on_reload"):
            m = re.search(rf'\b{key}\s*=\s*([^,\n\r]+)', content)
            if m:
                b = _parse_bool(m.group(1))
                if b is not None:
                    cfg[key] = b
    except Exception as exc:
        logger.error(f"[SlsLuaConfig] Failed to read {path}: {exc}")

    return cfg


def write_lobster_conf(cfg: Dict[str, Any], conf_path: Optional[Path] = None) -> bool:
    """Write ~/.config/SLSsteam/lobster.conf.lua ensuring parent directories exist."""
    path = conf_path or get_lobster_conf_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        enabled = "true" if cfg.get("enabled", True) else "false"
        notify = "true" if cfg.get("notify", True) else "false"
        auto_recover = "true" if cfg.get("auto_recover", True) else "false"
        run_on_reload = "true" if cfg.get("run_on_reload", False) else "false"

        lua_code = (
            "-- lobster.conf.lua\n"
            "-- Configuration for lobster.lua (SLSsteam config recovery)\n"
            "return {\n"
            f"    enabled = {enabled},\n"
            f"    notify = {notify},\n"
            f"    auto_recover = {auto_recover},\n"
            f"    run_on_reload = {run_on_reload},\n"
            "}\n"
        )
        path.write_text(lua_code, encoding="utf-8")
        return True
    except Exception as exc:
        logger.error(f"[SlsLuaConfig] Failed to write {path}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Bidirectional AT0-M Key Sync Logic
# ---------------------------------------------------------------------------

def generate_hubcap_timestamp() -> int:
    """Generate chronological timestamp in YYYYMMDDHHMMSS format (e.g. 20261026224500)."""
    now = time.localtime()
    return int(
        f"{now.tm_year:04d}{now.tm_mon:02d}{now.tm_mday:02d}"
        f"{now.tm_hour:02d}{now.tm_min:02d}{now.tm_sec:02d}"
    )


def _timestamp_to_sort_key(ts: int) -> int:
    """Convert timestamp to a chronologically sortable integer.
    Supports:
      - 14-digit ISO format YYYYMMDDHHMMSS (new standard, returned as-is)
      - Standard Unix epoch timestamps (10-digit, returned as-is)
      - Legacy custom mmhhddyyyy format (converted to sortable integer)
    """
    if ts <= 0:
        return 0
    # 14-digit YYYYMMDDHHMMSS format (>= 20200101000000) is already strictly chronological
    if ts >= 20200101000000:
        return ts

    s = str(ts).strip()
    # Check for legacy mmhhddyyyy format (ending in 2024..2030, length 9 or 10)
    if len(s) in (9, 10) and any(s.endswith(y) for y in ("2024", "2025", "2026", "2027", "2028", "2029", "2030")):
        try:
            padded = s.zfill(10)
            minute = int(padded[0:2])
            hour = int(padded[2:4])
            day = int(padded[4:6])
            year = int(padded[6:10])
            return (year * 100000000) + (day * 1000000) + (hour * 10000) + minute
        except ValueError as exc:
            logger.debug(f"[SlsLuaConfig] Could not parse legacy custom timestamp {s}: {exc}")

    return ts


def is_valid_hubcap_key(key: str) -> bool:
    """Basic validation guard for Hubcap API key. Must be alphanumeric/hyphens/underscores, >= 8 chars."""
    if not key or not isinstance(key, str):
        return False
    k = key.strip()
    if len(k) < 8 or any(c.isspace() for c in k):
        return False
    return bool(re.match(r'^[a-zA-Z0-9_\-]+$', k))


def sync_hubcap_api_key(settings, conf_path: Optional[Path] = None) -> Tuple[bool, str]:
    """Keep the Hubcap API key in ASSella and in katamari.conf.lua (hubcap section) the same.

    The newer side wins, by timestamp:
      - katamari.conf.lua newer (and its key valid): copy it into ASSella.
      - ASSella newer (and its key valid): write it into katamari.conf.lua.
    Returns (synced, message).
    """
    from managers.katamari_config_manager import KatamariConfigManager

    path = conf_path or get_hubcap_key_conf_path()
    if not path.is_file():
        return False, "katamari.conf.lua does not exist"

    local_key = str(settings.value("morrenus_api_key", "", type=str) or "").strip()
    try:
        local_ts = int(str(settings.value("morrenus_api_key_timestamp", 0)).strip() or 0)
    except (ValueError, TypeError):
        local_ts = 0
    if local_key and local_ts == 0:
        local_ts = generate_hubcap_timestamp()
        settings.setValue("morrenus_api_key_timestamp", str(local_ts))

    hub = KatamariConfigManager.load_config(path).get("hubcap", {})
    lua_key = str(hub.get("api_key", "")).strip()
    lua_ts = int(hub.get("api_key_timestamp", 0) or 0)
    lua_sort = _timestamp_to_sort_key(lua_ts)
    local_sort = _timestamp_to_sort_key(local_ts)

    if lua_sort > local_sort and is_valid_hubcap_key(lua_key):
        settings.setValue("morrenus_api_key", lua_key)
        settings.setValue("morrenus_api_key_timestamp", str(lua_ts))
        logger.info(f"[SlsLuaConfig] Synced newer Hubcap API key from katamari.conf.lua (ts: {lua_ts}) to ASSella")
        return True, "Synced API key from katamari.conf.lua to ASSella"

    if (local_sort > lua_sort or (local_sort > 0 and lua_sort == 0)) and is_valid_hubcap_key(local_key):
        ok = KatamariConfigManager.set_hubcap_api_key(local_key, local_ts, path)
        if not ok:
            return False, "Could not write katamari.conf.lua"
        logger.info(f"[SlsLuaConfig] Synced newer Hubcap API key from ASSella (ts: {local_ts}) to katamari.conf.lua")
        return True, "Synced API key from ASSella to katamari.conf.lua"

    return False, "Already in sync"
