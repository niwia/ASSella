"""
sls_lua_config.py
=================
Utilities to inspect, generate, and synchronize Lua configuration files for
SLSsteam plugins:
  - lobster.conf.lua: Headcrab config recovery settings
  - spacebunny.conf.lua: SpaceBunny interceptor and Hubcap auto-fetch settings

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


def get_spacebunny_conf_path() -> Path:
    """Return canonical path to spacebunny.conf.lua."""
    return get_sls_config_dir() / "spacebunny.conf.lua"


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
# SpaceBunny / Hubcap Configuration
# ---------------------------------------------------------------------------

DEFAULT_HUBCAP_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "base_url": "https://hubcapmanifest.com/api/v1",
    "api_key": "",
    "api_key_timestamp": 0,
    "timeout_sec": 5,
    "write_config": True,
    "notify": True,
    "preflight": "contents",
    "depot_index": False,
    "write_depot_comments": False,
}


def read_spacebunny_conf(conf_path: Optional[Path] = None) -> Dict[str, Any]:
    """Read ~/.config/SLSsteam/spacebunny.conf.lua, returning outer dict containing 'hubcap' sub-dict."""
    path = conf_path or get_spacebunny_conf_path()
    res: Dict[str, Any] = {
        "standalone": False,
        "hubcap": dict(DEFAULT_HUBCAP_CONFIG),
    }
    if not path.is_file():
        return res

    try:
        content = path.read_text(encoding="utf-8", errors="ignore")

        # Outer standalone
        m_st = re.search(r'\bstandalone\s*=\s*([^,\n\r]+)', content)
        if m_st:
            b = _parse_bool(m_st.group(1))
            if b is not None:
                res["standalone"] = b

        # Extract hubcap table block
        m_hub = re.search(r'\bhubcap\s*=\s*\{([^}]+)\}', content, re.DOTALL)
        if m_hub:
            hub_content = m_hub.group(1)
            h = res["hubcap"]

            m = re.search(r'\benabled\s*=\s*([^,\n\r]+)', hub_content)
            if m:
                b = _parse_bool(m.group(1))
                if b is not None:
                    h["enabled"] = b

            m = re.search(r'\bapi_key\s*=\s*([^,\n\r]+)', hub_content)
            if m:
                s = _parse_string(m.group(1))
                if s is not None:
                    h["api_key"] = s

            m = re.search(r'\bapi_key_timestamp\s*=\s*([^,\n\r]+)', hub_content)
            if m:
                ts = _parse_int(m.group(1))
                if ts is not None:
                    h["api_key_timestamp"] = ts

            m = re.search(r'\bbase_url\s*=\s*([^,\n\r]+)', hub_content)
            if m:
                s = _parse_string(m.group(1))
                if s is not None:
                    h["base_url"] = s

            for key in ("write_config", "notify", "depot_index", "write_depot_comments"):
                m = re.search(rf'\b{key}\s*=\s*([^,\n\r]+)', hub_content)
                if m:
                    b = _parse_bool(m.group(1))
                    if b is not None:
                        h[key] = b

            m = re.search(r'\btimeout_sec\s*=\s*([^,\n\r]+)', hub_content)
            if m:
                num = _parse_int(m.group(1))
                if num is not None:
                    h["timeout_sec"] = num

            m = re.search(r'\bpreflight\s*=\s*([^,\n\r]+)', hub_content)
            if m:
                s = _parse_string(m.group(1))
                if s is not None:
                    h["preflight"] = s
    except Exception as exc:
        logger.error(f"[SlsLuaConfig] Failed to read {path}: {exc}")

    return res


def write_spacebunny_conf(cfg: Dict[str, Any], conf_path: Optional[Path] = None) -> bool:
    """Write ~/.config/SLSsteam/spacebunny.conf.lua preserving standalone and hubcap options."""
    path = conf_path or get_spacebunny_conf_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        standalone = "true" if cfg.get("standalone", False) else "false"
        hub = cfg.get("hubcap", {})

        h_enabled = "true" if hub.get("enabled", True) else "false"
        h_api_key = str(hub.get("api_key", "")).strip()
        h_ts = int(hub.get("api_key_timestamp", 0) or 0)
        h_base_url = str(hub.get("base_url", "https://hubcapmanifest.com/api/v1")).strip()
        h_timeout = int(hub.get("timeout_sec", 5) or 5)
        h_write_config = "true" if hub.get("write_config", True) else "false"
        h_notify = "true" if hub.get("notify", True) else "false"
        h_preflight = str(hub.get("preflight", "contents")).strip()
        h_depot_index = "true" if hub.get("depot_index", False) else "false"
        h_comments = "true" if hub.get("write_depot_comments", False) else "false"

        lua_code = (
            "-- spacebunny.conf.lua\n"
            "-- Configuration for spacebunny.lua and hubcap.lua (Hubcap key resolver)\n"
            "return {\n"
            f"    standalone = {standalone},\n"
            "    hubcap = {\n"
            f"        enabled = {h_enabled},\n"
            f'        api_key = "{escape_lua_string(h_api_key)}",\n'
            f"        api_key_timestamp = {h_ts},\n"
            f'        base_url = "{escape_lua_string(h_base_url)}",\n'
            f"        timeout_sec = {h_timeout},\n"
            f"        write_config = {h_write_config},\n"
            f"        notify = {h_notify},\n"
            f'        preflight = "{escape_lua_string(h_preflight)}",\n'
            f"        depot_index = {h_depot_index},\n"
            f"        write_depot_comments = {h_comments},\n"
            "    },\n"
            "}\n"
        )
        path.write_text(lua_code, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except (OSError, AttributeError) as exc:
            logger.debug(f"[SlsLuaConfig] Could not restrict permissions on {path}: {exc}")
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


def sync_hubcap_api_key(settings, sb_conf_path: Optional[Path] = None) -> Tuple[bool, str]:
    """Bidirectionally synchronize Hubcap API key between ASSella settings and spacebunny.conf.lua.
    Condition:
      - If Lua timestamp > local timestamp (and Lua key valid): copy Lua key to local settings.
      - If local timestamp > Lua timestamp (and local key valid): copy local key to spacebunny.conf.lua.
      - If timestamps are equal or unset: initial sync local -> Lua if local key exists.
    Returns (synced, message).
    """
    sb_path = sb_conf_path or get_spacebunny_conf_path()
    if not sb_path.is_file():
        return False, "spacebunny.conf.lua does not exist"

    local_key = str(settings.value("morrenus_api_key", "", type=str) or "").strip()
    try:
        raw_val = settings.value("morrenus_api_key_timestamp", 0)
        local_ts = int(str(raw_val).strip() or 0)
    except (ValueError, TypeError):
        local_ts = 0

    # If user has a local key but no timestamp set yet, initialize it
    if local_key and local_ts == 0:
        local_ts = generate_hubcap_timestamp()
        settings.setValue("morrenus_api_key_timestamp", str(local_ts))

    sb_cfg = read_spacebunny_conf(sb_path)
    hub = sb_cfg.get("hubcap", {})
    lua_key = str(hub.get("api_key", "")).strip()
    lua_ts = int(hub.get("api_key_timestamp", 0) or 0)

    lua_sort = _timestamp_to_sort_key(lua_ts)
    local_sort = _timestamp_to_sort_key(local_ts)

    # Decision logic
    if lua_sort > local_sort and is_valid_hubcap_key(lua_key):
        # Lua is newer: update ASSella
        settings.setValue("morrenus_api_key", lua_key)
        settings.setValue("morrenus_api_key_timestamp", str(lua_ts))
        logger.info(f"[SlsLuaConfig] Synced newer Hubcap API key from Lua (ts: {lua_ts}) to ASSella")
        return True, "Synced API key from spacebunny.conf.lua to ASSella"

    elif (local_sort > lua_sort or (local_sort > 0 and lua_sort == 0)) and is_valid_hubcap_key(local_key):
        # ASSella is newer: update Lua
        hub["api_key"] = local_key
        hub["api_key_timestamp"] = local_ts
        sb_cfg["hubcap"] = hub
        write_spacebunny_conf(sb_cfg, sb_path)
        logger.info(f"[SlsLuaConfig] Synced newer Hubcap API key from ASSella (ts: {local_ts}) to Lua")
        return True, "Synced API key from ASSella to spacebunny.conf.lua"

    return False, "Already in sync"
