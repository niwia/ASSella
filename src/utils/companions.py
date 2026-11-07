"""Uran (Decky plugin) and Epsilon (browser bridge): find them, read and write their settings.

Both companions work on their own and keep their own ``settings.json``;
ASSella only manages them from Settings -> Companions:

* Uran: ``~/homebrew/plugins/Uran`` (Decky), settings in
  ``~/homebrew/settings/Uran/settings.json``.
* Epsilon: installed from a checkout (``~/epsilon`` by default) as the
  systemd --user units ``epsilon.socket`` / ``epsilon.service`` (+ the
  ``epsilon-updater.timer``). Without Decky it keeps its settings in
  ``~/.config/assella-decky/settings.json``. It answers on 127.0.0.1:1952.

Writes keep every key ASSella does not know and replace the file
atomically. Anything slow (systemctl, the HTTP status) is meant to be called
from a background thread.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

EPSILON_PORT = 1952
EPSILON_URL = f"http://127.0.0.1:{EPSILON_PORT}"
PHOTAR_URL = "https://raw.githubusercontent.com/niwia/Uran/main/photar/photar.user.js"
URAN_REPO_URL = "https://github.com/niwia/Uran"

# Settings both companions understand (same names, separate files).
SHARED_DEFAULTS: Dict[str, Any] = {
    "fetch_dlcs_on_inject": True,
    "add_delisted_dlcs": True,
    "hubcap_gateway": "auto",
}
URAN_DEFAULTS: Dict[str, Any] = dict(SHARED_DEFAULTS, steam_store_enabled=True, notification_mode="auto")

GATEWAY_OPTIONS = [("auto", "Automatic (direct, proxy if blocked)"), ("direct", "Direct only"), ("proxy", "Worker proxy only")]
NOTIFICATION_OPTIONS = [("auto", "Automatic"), ("steam", "Steam toasts"), ("desktop", "Desktop notifications"), ("none", "Off")]


def _home() -> Path:
    return Path.home()


def _config_home() -> Path:
    x = os.environ.get("XDG_CONFIG_HOME")
    return Path(x) if x else _home() / ".config"


# ---------------------------------------------------------------- files

def read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def update_json(path: Path, changes: Dict[str, Any]) -> bool:
    """Merge ``changes`` into a JSON settings file, keeping every other key."""
    try:
        data = read_json(path)
        data.update(changes)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".assella-tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if path.exists():
            try:
                os.chmod(tmp, path.stat().st_mode & 0o777)
            except OSError:
                pass
        tmp.replace(path)
        return True
    except OSError as e:
        logger.warning(f"[Companions] Could not write {path}: {e}")
        return False


# ---------------------------------------------------------------- Uran

def uran_plugin_dir() -> Path:
    return _home() / "homebrew" / "plugins" / "Uran"


def uran_settings_path() -> Path:
    return _home() / "homebrew" / "settings" / "Uran" / "settings.json"


def uran_info() -> Dict[str, Any]:
    """Local facts only (fast): installed, version, settings."""
    pdir = uran_plugin_dir()
    meta = read_json(pdir / "plugin.json")
    installed = bool(meta) or (pdir / "main.py").is_file()
    settings = dict(URAN_DEFAULTS)
    settings.update(read_json(uran_settings_path()))
    return {
        "installed": installed,
        "version": str(meta.get("version") or ""),
        "settings_path": str(uran_settings_path()),
        "settings": settings,
    }


def set_uran_setting(key: str, value: Any) -> bool:
    return update_json(uran_settings_path(), {key: value})


# ---------------------------------------------------------------- Epsilon

def _systemd_user_dir() -> Path:
    return _config_home() / "systemd" / "user"


def epsilon_settings_path() -> Path:
    return _config_home() / "assella-decky" / "settings.json"


def epsilon_root() -> Optional[Path]:
    """The Epsilon checkout: from the installed unit's ExecStart, else ~/epsilon."""
    unit = _systemd_user_dir() / "epsilon.service"
    try:
        m = re.search(r"^ExecStart=\S+\s+(\S+daemon\.py)", unit.read_text(encoding="utf-8"), re.M)
        if m:
            daemon = Path(m.group(1))
            if daemon.is_file():
                return daemon.parent.parent
    except OSError:
        pass
    default = _home() / "epsilon"
    return default if (default / "epsilon" / "daemon.py").is_file() else None


def epsilon_info() -> Dict[str, Any]:
    """Local facts only (fast)."""
    root = epsilon_root()
    meta = read_json(root / "plugin.json") if root else {}
    settings = dict(SHARED_DEFAULTS)
    settings.update(read_json(epsilon_settings_path()))
    return {
        "installed": root is not None or (_systemd_user_dir() / "epsilon.socket").is_file(),
        "units_installed": (_systemd_user_dir() / "epsilon.socket").is_file(),
        "root": str(root) if root else "",
        "version": str(meta.get("version") or ""),
        "settings_path": str(epsilon_settings_path()),
        "settings": settings,
    }


def set_epsilon_setting(key: str, value: Any) -> bool:
    return update_json(epsilon_settings_path(), {key: value})


def _systemctl(*args: str, timeout: float = 8.0) -> subprocess.CompletedProcess:
    exe = shutil.which("systemctl") or "systemctl"
    return subprocess.run([exe, "--user", *args], capture_output=True, text=True, timeout=timeout)


def _unit_state(unit: str) -> Dict[str, Any]:
    try:
        active = _systemctl("is-active", unit, timeout=4).stdout.strip()
        enabled = _systemctl("is-enabled", unit, timeout=4).stdout.strip()
    except Exception:
        return {"active": False, "enabled": False, "known": False}
    return {"active": active == "active", "enabled": enabled == "enabled", "known": enabled not in ("", "not-found")}


def epsilon_live_status() -> Dict[str, Any]:
    """systemd state of the units and the bridge's own /status (slow: background thread)."""
    out: Dict[str, Any] = {
        "socket": _unit_state("epsilon.socket"),
        "updater": _unit_state("epsilon-updater.timer"),
        "status": None,
        "error": "",
    }
    try:
        with urllib.request.urlopen(f"{EPSILON_URL}/status", timeout=3) as r:
            out["status"] = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        out["error"] = str(getattr(e, "reason", e))
    return out


def set_epsilon_enabled(enabled: bool) -> tuple[bool, str]:
    """Start and enable the socket (the bridge wakes on demand), or stop and disable both units."""
    try:
        if enabled:
            r = _systemctl("enable", "--now", "epsilon.socket")
        else:
            r = _systemctl("disable", "--now", "epsilon.socket")
            _systemctl("stop", "epsilon.service")
    except Exception as e:
        return False, str(e)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def set_epsilon_updater(enabled: bool) -> tuple[bool, str]:
    try:
        r = _systemctl("enable" if enabled else "disable", "--now", "epsilon-updater.timer")
    except Exception as e:
        return False, str(e)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def install_epsilon_command() -> Optional[list]:
    """The installer of the found checkout, for a terminal (it prints its progress)."""
    root = epsilon_root()
    script = root / "epsilon" / "install.sh" if root else None
    return ["bash", str(script)] if script and script.is_file() else None


# ---------------------------------------------------------------- Hubcap key

def assella_hubcap_key(settings=None) -> str:
    if settings is None:
        from utils.settings import get_settings

        settings = get_settings()
    return (settings.value("morrenus_api_key", "", type=str) or "").strip()


def key_state(companion_key: str, assella_key: str) -> str:
    """'same' | 'own' (a different key) | 'none' (uses ASSella's / not set)."""
    c = (companion_key or "").strip()
    if not c:
        return "none"
    return "same" if c == (assella_key or "").strip() else "own"
