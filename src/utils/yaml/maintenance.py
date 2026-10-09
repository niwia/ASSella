"""Startup backup, permission hardening, and the Denuvo / Netsock /
LaunchOptions sections.

Extracted from ``utils/yaml_config_manager.py``. That module is now a thin
facade that re-exports this package's public names, so every existing
``from utils.yaml_config_manager import ...`` keeps working unchanged.

Do not import across sibling modules in this package except from ``core`` -
dependencies must point in one direction (leaf -> core) to keep the package
import-cycle free.
"""

import logging

# --- imports ---------------------------------------------------------
import os, re, shutil
from pathlib import Path
from typing import Dict, List, Optional

from utils.yaml.core import _CONFIG_DISABLED, _atomic_write, _create_backup, _get_config_content_if_enabled, _get_section_bounds, _read_config_content, _remove_entry_from_section, _restrict_config_permissions, get_user_config_path

logger = logging.getLogger(__name__)


def backup_config_on_startup(config_path: Path) -> bool:
    """Create a backup of the config file on application startup."""
    return _create_backup(config_path)

def harden_existing_config_permissions(config_path: Optional[Path] = None) -> bool:
    """Apply 0600 to an already-written config.yaml at startup.

    Older versions left it world-readable (0644). Existing installs are never
    rewritten unless their contents change, so tighten them explicitly once.
    Backup copies hold the same keys, so they are tightened too.
    """
    try:
        path = config_path or get_user_config_path()
        if not path:
            return False
        path = Path(path)
        if not path.exists():
            return False
        ok = _restrict_config_permissions(path)
        try:
            for sibling in path.parent.iterdir():
                if sibling.is_file() and (
                    sibling.name.startswith(path.name + ".")
                    or sibling.name.startswith("config.bak")
                    or sibling.name.startswith("config.yaml.bak")
                ):
                    _restrict_config_permissions(sibling)
        except OSError:
            pass
        return ok
    except Exception as e:
        logger.debug(f"Could not harden config permissions: {e}")
        return False

def get_denuvo_games(config_path: Path) -> Dict[str, List[str]]:
    """Get all DenuvoGames mappings from SLSsteam config.yaml.

    Returns:
        Dict mapping SteamID to list of AppIDs.
    """
    if not config_path.exists():
        return {}
    try:
        content = _read_config_content(config_path)
        if not content:
            return {}

        bounds = _get_section_bounds(content, "DenuvoGames")
        if not bounds:
            return {}

        _, content_start, section_end = bounds
        section_content = content[content_start:section_end]

        res = {}
        current_steam_id = None

        for line in section_content.splitlines():
            line_strip = line.strip()
            if not line_strip or line_strip.startswith("#"):
                continue

            steam_id_match = re.match(r"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*$", line)
            if steam_id_match:
                current_steam_id = steam_id_match.group(1)
                res[current_steam_id] = []
                continue

            appid_match = re.match(r"^[ \t]*-[ \t]*['\"]?(\d+)['\"]?[ \t]*(?:#[^\r\n]*)?$", line)
            if appid_match and current_steam_id is not None:
                res[current_steam_id].append(appid_match.group(1))

        return res
    except OSError as e:
        logger.error(f"Failed to read DenuvoGames from {config_path}: {e}")
        return {}

def save_denuvo_games(config_path: Path, steam_id: str, appids: List[str]) -> bool:
    """Deprecated: Denuvo status should never be written to SLS config. Calls clean_denuvo_games_section instead."""
    return clean_denuvo_games_section(config_path)

def clean_denuvo_games_section(config_path: Path) -> bool:
    """
    Remove all entries under DenuvoGames in SLSsteam config.yaml, returning it to an empty block.
    This reverses the unintentional Denuvo blocklist write introduced in v2.5.4.
    """
    if not config_path.exists():
        return False
    try:
        content = _read_config_content(config_path)
        if not content:
            return False

        bounds = _get_section_bounds(content, "DenuvoGames")
        if not bounds:
            return False

        _, content_start, section_end = bounds
        section_content = content[content_start:section_end]
        if section_content.strip():
            new_content = content[:content_start] + "\n" + content[section_end:]
            _create_backup(config_path)
            if _atomic_write(config_path, new_content):
                logger.info(f"Successfully cleaned DenuvoGames block in {config_path}")
                return True
        return False
    except OSError as e:
        logger.error(f"Failed to clean DenuvoGames block in {config_path}: {e}")
        return False

def get_netsock_tools_dir() -> Path:
    """Return the tools/netsock directory for the active Steam/SLS environment."""
    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        return env.sls_config_dir / "tools" / "netsock"
    except Exception:
        return Path.home() / ".config" / "SLSsteam" / "tools" / "netsock"

def find_existing_netsock_so() -> Optional[Path]:
    """Search candidate locations across distros and Flatpak for an existing netsock.so."""
    home = Path.home()
    candidates: List[Path] = []

    # 1. Active environment sls_config_dir and sls_install_dir
    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        candidates.append(env.sls_config_dir / "tools" / "netsock" / "netsock.so")
        candidates.append(env.sls_install_dir / "tools" / "netsock" / "netsock.so")
    except Exception:
        pass

    # 2. Native XDG / standard paths
    xdg_cfg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg_cfg and Path(xdg_cfg).is_absolute():
        candidates.append(Path(xdg_cfg) / "SLSsteam" / "tools" / "netsock" / "netsock.so")
    candidates.append(home / ".config" / "SLSsteam" / "tools" / "netsock" / "netsock.so")
    candidates.append(home / ".local" / "share" / "SLSsteam" / "tools" / "netsock" / "netsock.so")

    # 3. Flatpak paths
    flatpak_base = home / ".var" / "app" / "com.valvesoftware.Steam"
    candidates.append(flatpak_base / ".config" / "SLSsteam" / "tools" / "netsock" / "netsock.so")
    candidates.append(flatpak_base / ".local" / "share" / "SLSsteam" / "tools" / "netsock" / "netsock.so")

    for cand in candidates:
        if cand.is_file() and cand.stat().st_size > 0:
            return cand
    return None

def get_netsock_so_launch_path() -> str:
    """Return the path to netsock.so to use inside Steam LaunchOptions.

    Inside Steam runtime (both Native and inside Flatpak sandbox), ~/.config/SLSsteam
    is mapped to the SLS config directory.
    """
    xdg_cfg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg_cfg and Path(xdg_cfg).is_absolute():
        return str(Path(xdg_cfg) / "SLSsteam" / "tools" / "netsock" / "netsock.so")
    return str(Path.home() / ".config" / "SLSsteam" / "tools" / "netsock" / "netsock.so")

def ensure_netsock_binary(log_cb=None) -> bool:
    """Check if netsock.so is present; if missing, download fix.so from upstream GitHub releases.

    Ensures netsock.so is available in the target tools/netsock/ directory.
    Also syncs to ~/.config/SLSsteam/tools/netsock/netsock.so if in Flatpak.
    """
    target_dir = get_netsock_tools_dir()
    target_so = target_dir / "netsock.so"

    # 1. Check if already present in target or existing candidate paths
    existing = find_existing_netsock_so()
    if existing and existing.is_file() and existing.stat().st_size > 0:
        if existing != target_so:
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(existing, target_so)
                os.chmod(target_so, 0o755)
            except Exception as e:
                logger.warning(f"Could not copy existing netsock.so to target: {e}")
        return True

    # 2. Missing: download from GitHub release
    try:
        import urllib.request
        target_dir.mkdir(parents=True, exist_ok=True)
        msg = "Downloading latest netsock.so from GitHub..."
        if log_cb:
            log_cb(msg)
        logger.info(msg)

        url = "https://github.com/yesyes0649/steamnetsock-patch/releases/download/latest/fix.so"
        req = urllib.request.Request(url, headers={"User-Agent": "ASSella/2.6.5"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()

        if data:
            target_so.write_bytes(data)
            os.chmod(target_so, 0o755)
            logger.info(f"Successfully downloaded netsock.so ({len(data)} bytes) to {target_so}")

            # Also ensure ~/.config/SLSsteam/tools/netsock/netsock.so exists for Flatpak / host parity
            native_so = Path.home() / ".config" / "SLSsteam" / "tools" / "netsock" / "netsock.so"
            if native_so != target_so and not native_so.exists():
                try:
                    native_so.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target_so, native_so)
                    os.chmod(native_so, 0o755)
                except Exception:
                    pass
            return True
        return False
    except Exception as exc:
        err = f"Failed to download netsock fix.so: {exc}"
        if log_cb:
            log_cb(err)
        logger.error(err)
        return False

def get_launch_option(config_path: Path, app_id: str) -> Optional[str]:
    """Get the launch option command for a specific AppID from LaunchOptions: in config.yaml."""
    content = _read_config_content(config_path)
    if not content:
        return None

    bounds = _get_section_bounds(content, "LaunchOptions")
    if not bounds:
        return None

    _, content_start, section_end = bounds
    section_content = content[content_start:section_end]

    entry_pattern = re.compile(
        rf"^[ \t]*['\"]?{re.escape(str(app_id))}['\"]?[ \t]*:[ \t]*(.+)$",
        re.MULTILINE
    )
    m = entry_pattern.search(section_content)
    if m:
        return m.group(1).strip()
    return None

def add_launch_option(config_path: Path, app_id: str, command: str) -> bool:
    """Add or replace an AppID entry in the LaunchOptions section of SLSsteam config.yaml."""
    try:
        content = _get_config_content_if_enabled(config_path)
        if content is _CONFIG_DISABLED:
            return False
        if content is None:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            entry = f"LaunchOptions:\n  {app_id}: {command}\n"
            return _atomic_write(config_path, entry)

        bounds = _get_section_bounds(content, "LaunchOptions")
        new_line = f"  {app_id}: {command}\n"

        if bounds:
            _, content_start, section_end = bounds
            section_content = content[content_start:section_end]

            app_id_line_pattern = re.compile(
                rf"^[ \t]*['\"]?{re.escape(str(app_id))}['\"]?[ \t]*:.*$",
                re.MULTILINE
            )
            existing_m = app_id_line_pattern.search(section_content)
            if existing_m:
                abs_m_start = content_start + existing_m.start()
                l_start = content.rfind("\n", 0, abs_m_start)
                l_start = 0 if l_start == -1 else l_start + 1
                l_end = content.find("\n", abs_m_start)
                l_end = len(content) if l_end == -1 else l_end + 1
                new_content = content[:l_start] + new_line + content[l_end:]
            else:
                lines = section_content.splitlines(keepends=True)
                last_idx_offset = 0
                curr_offset = 0
                for line in lines:
                    s = line.strip()
                    if s and not s.startswith("#"):
                        last_idx_offset = curr_offset + len(line)
                    curr_offset += len(line)

                if last_idx_offset > 0:
                    pos = content_start + last_idx_offset
                else:
                    pos = content_start
                new_content = content[:pos] + new_line + content[pos:]
        else:
            new_content = content.rstrip() + f"\n\nLaunchOptions:\n{new_line}"

        if _atomic_write(config_path, new_content):
            logger.info(f"Added LaunchOptions for AppID '{app_id}' in {config_path}")
            return True
        return False
    except OSError as e:
        logger.error(f"Failed to add LaunchOptions for '{app_id}': {e}", exc_info=True)
        return False

def remove_launch_option(config_path: Path, app_id: str) -> bool:
    """Remove an AppID entry from the LaunchOptions section in SLSsteam config.yaml."""
    app_id_pattern = re.compile(
        rf"^[ \t]*['\"]?{re.escape(str(app_id))}['\"]?[ \t]*:.*$",
        re.MULTILINE
    )
    return _remove_entry_from_section(
        config_path,
        "LaunchOptions",
        app_id_pattern,
        f"Removed AppID '{app_id}' from LaunchOptions in {config_path}",
        f"Failed to remove LaunchOptions for AppID '{app_id}': {{e}}"
    )
