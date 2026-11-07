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


# ---------------------------------------------------------------- environment

def in_flatpak() -> bool:
    """ASSella itself sandboxed: host commands go through flatpak-spawn."""
    return Path("/.flatpak-info").exists()


def host_cmd(args: list) -> list:
    return ["flatpak-spawn", "--host", *args] if in_flatpak() else list(args)


def _run(args: list, timeout: float = 8.0) -> subprocess.CompletedProcess:
    return subprocess.run(host_cmd(args), capture_output=True, text=True, timeout=timeout)


def have_systemd_user() -> bool:
    try:
        r = _run(["systemctl", "--user", "--version"], timeout=4)
        return r.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------- Uran (Decky)

DECKY_URL = "https://decky.xyz"


def _decky_homes() -> list:
    """Decky's homebrew folders, most likely first.

    Decky installs as a system service (plugin_loader.service) that runs from
    <user home>/homebrew; its unit names the real path on every distro
    (SteamOS, Bazzite, ChimeraOS, Arch/Fedora/Ubuntu installs). Steam itself
    may be native or Flatpak: Decky's folder is the same either way.
    """
    found = []
    for unit in (
        Path("/etc/systemd/system/plugin_loader.service"),
        Path("/usr/lib/systemd/system/plugin_loader.service"),
        Path("/lib/systemd/system/plugin_loader.service"),
    ):
        try:
            text = unit.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in re.finditer(r"(/[^\s\"'=]*?/homebrew)(?:/|\b)", text):
            found.append(Path(m.group(1)))
    env = os.environ.get("DECKY_HOME")
    if env:
        found.append(Path(env))
    found.append(_home() / "homebrew")
    out, seen = [], set()
    for p in found:
        if str(p) not in seen:
            seen.add(str(p))
            out.append(p)
    return out


def decky_home() -> Optional[Path]:
    for p in _decky_homes():
        if (p / "plugins").is_dir() or (p / "services").is_dir():
            return p
    return None


def decky_installed() -> bool:
    return decky_home() is not None


def _uran_dir(home: Optional[Path]) -> Optional[Path]:
    if home is None:
        return None
    plugins = home / "plugins"
    direct = plugins / "Uran"
    if direct.is_dir():
        return direct
    # Store installs can use another folder name: find it by plugin.json.
    try:
        for d in plugins.iterdir():
            meta = read_json(d / "plugin.json")
            if str(meta.get("name", "")).lower() == "uran":
                return d
    except OSError:
        pass
    return None


def uran_plugin_dir() -> Path:
    return _uran_dir(decky_home()) or (_home() / "homebrew" / "plugins" / "Uran")


def uran_settings_path() -> Path:
    home = decky_home() or (_home() / "homebrew")
    name = uran_plugin_dir().name or "Uran"
    return home / "settings" / name / "settings.json"


def uran_info() -> Dict[str, Any]:
    """Local facts only (fast): Decky, installed, version, settings."""
    home = decky_home()
    pdir = _uran_dir(home)
    meta = read_json(pdir / "plugin.json") if pdir else {}
    settings = dict(URAN_DEFAULTS)
    settings.update(read_json(uran_settings_path()))
    return {
        "decky": home is not None,
        "decky_home": str(home) if home else "",
        "installed": pdir is not None,
        "plugin_dir": str(pdir) if pdir else "",
        "version": str(meta.get("version") or ""),
        "settings_path": str(uran_settings_path()),
        "settings": settings,
    }


def set_uran_setting(key: str, value: Any) -> bool:
    return update_json(uran_settings_path(), {key: value})


# ---------------------------------------------------------------- Epsilon

EPSILON_INSTALL_URL = "https://raw.githubusercontent.com/niwia/Uran/main/install_epsilon.sh"
EPSILON_GUIDE_URL = "https://assella.pages.dev/epsilon"


def _systemd_user_dirs() -> list:
    return [_config_home() / "systemd" / "user", _home() / ".local" / "share" / "systemd" / "user"]


def _systemd_user_dir() -> Path:
    for d in _systemd_user_dirs():
        if (d / "epsilon.socket").is_file() or (d / "epsilon.service").is_file():
            return d
    return _systemd_user_dirs()[0]


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
    return _run(["systemctl", "--user", *args], timeout=timeout)


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
        "systemd": True,
        "stats": {},
    }
    if not have_systemd_user():
        out["systemd"] = False
    # Asking /status wakes a socket-activated bridge, so only ask when it is on.
    if out["socket"]["active"] or not out["systemd"]:
        try:
            with urllib.request.urlopen(f"{EPSILON_URL}/status", timeout=3) as r:
                out["status"] = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            out["error"] = str(getattr(e, "reason", e))
    out["stats"] = epsilon_stats()
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


def epsilon_script(name: str) -> Optional[Path]:
    root = epsilon_root()
    script = root / "epsilon" / name if root else None
    return script if script and script.is_file() else None


def run_epsilon_action(action: str, timeout: float = 300) -> tuple[bool, str]:
    """install | repair | update | uninstall | restart. Returns (ok, output tail)."""
    try:
        if action == "install":
            cmd = ["bash", "-c", f"curl -fsSL {EPSILON_INSTALL_URL} | bash"]
        elif action in ("repair", "update", "uninstall"):
            script = epsilon_script({"repair": "install.sh", "update": "update.sh", "uninstall": "uninstall.sh"}[action])
            if script is None:
                return False, "Epsilon's scripts were not found."
            cmd = ["bash", str(script)]
        elif action == "restart":
            _systemctl("stop", "epsilon.service")
            r = _systemctl("restart", "epsilon.socket")
            return r.returncode == 0, (r.stderr or r.stdout).strip()
        else:
            return False, f"unknown action {action}"
        r = _run(cmd, timeout=timeout)
        out = ((r.stdout or "") + ("\n" + r.stderr if r.stderr else "")).strip()
        return r.returncode == 0, out[-2000:]
    except subprocess.TimeoutExpired:
        return False, "Timed out."
    except Exception as e:
        return False, str(e)


def remove_epsilon_files() -> tuple[bool, str]:
    """After uninstall: delete the Epsilon folder (only a folder that looks like Epsilon)."""
    root = epsilon_root()
    if root is None:
        return True, ""
    if not (root / "epsilon" / "daemon.py").is_file() or root == _home():
        return False, f"{root} does not look like an Epsilon folder; left alone."
    try:
        shutil.rmtree(root)
        return True, str(root)
    except OSError as e:
        return False, str(e)


def epsilon_stats(days: int = 7) -> Dict[str, Any]:
    """Recent activity from the service journal (background thread).

    Counts what Epsilon logs at INFO: "Background inject complete" (a game
    added), "Background inject failed", and "Epsilon bridge active" (the
    socket woke the bridge).
    """
    out: Dict[str, Any] = {"added": 0, "failed": 0, "wakes": 0, "last": "", "available": False}
    try:
        r = _run(["journalctl", "--user", "-u", "epsilon.service", "--since", f"{days} days ago",
                  "-o", "short-iso", "--no-pager"], timeout=6)
    except Exception:
        return out
    lines = [ln for ln in (r.stdout or "").splitlines() if ln and not ln.startswith("--")]
    out["available"] = r.returncode == 0
    for ln in lines:
        if "Background inject complete" in ln:
            out["added"] += 1
            out["last"] = ln.split(" ", 1)[0]
        elif "Background inject failed" in ln:
            out["failed"] += 1
        elif "Epsilon bridge active" in ln:
            out["wakes"] += 1
    return out


def epsilon_log(lines: int = 80) -> str:
    try:
        r = _run(["journalctl", "--user", "-u", "epsilon.service", "-u", "epsilon-updater.service",
                  "-n", str(lines), "-o", "short", "--no-pager"], timeout=6)
        return (r.stdout or r.stderr or "").strip() or "No log entries."
    except Exception as e:
        return str(e)


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
