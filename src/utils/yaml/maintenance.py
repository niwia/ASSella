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

from utils.yaml.core import (
    _CONFIG_DISABLED,
    _add_map_item_in_memory,
    _atomic_write,
    _config_management_enabled,
    _create_backup,
    _get_config_content_if_enabled,
    _get_section_bounds,
    _read_config_content,
    _remove_entry_from_section,
    _restrict_config_permissions,
    batch_config_edit,
    get_user_config_path,
)

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

def _strip_yaml_comment(value: str) -> str:
    """Drop an inline comment that is outside a quoted scalar."""
    text = value.strip()
    out = []
    quote = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and quote == '"':
                i += 1
                if i < len(text):
                    out.append(text[i])
            elif ch == quote:
                quote = None
        else:
            if ch in "\"'":
                quote = ch
                out.append(ch)
            elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
                break
            else:
                out.append(ch)
        i += 1
    return "".join(out).strip()


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
        re.MULTILINE,
    )
    m = entry_pattern.search(section_content)
    if m:
        return _yaml_unquote(_strip_yaml_comment(m.group(1)))
    return None

def add_launch_option(config_path: Path, app_id: str, command: str) -> bool:
    """Add or replace an AppID entry in the LaunchOptions section of SLSsteam config.yaml."""
    try:
        if not _config_management_enabled():
            return False
        app_id_str = str(app_id).strip()
        if not app_id_str:
            return False

        with batch_config_edit(config_path) as editor:
            editor.ensure_plugins()
            content = editor.content
            if not content.strip():
                editor.content = f"LaunchOptions:\n  {app_id_str}: {command}\n"
                editor.has_changes = True
                changed = True
            else:
                new_content, changed = _add_map_item_in_memory(
                    content, "LaunchOptions", app_id_str, command
                )
                if changed:
                    editor.content = new_content
                    editor.has_changes = True

        if not editor.committed_successfully:
            return False
        if changed:
            logger.info(f"Added LaunchOptions for AppID '{app_id_str}' in {config_path}")
        # Idempotent success: the requested command is configured either way.
        return True
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


BYPASS_MARK = "#assella-bypass"


def _bash_dq(text: str) -> str:
    """Escape for use inside bash double quotes."""
    return re.sub(r'([\\"$`])', r"\\\1", text)


def _bash_sq(text: str) -> str:
    """Escape for use inside a bash single-quoted string."""
    return text.replace("'", "'\\''")


def build_bypass_launch_command(relative_exe_path: str) -> str:
    """SLSsteam LaunchOptions value that starts <relative_exe_path> instead of the launcher.

    Steam's arguments arrive as positional parameters (``-- %command%``), so
    paths with spaces or quotes are never re-parsed by a shell. The last one
    is the executable Steam would start (for Proton: the .exe after
    ``waitforexitandrun``); it is swapped for the chosen one, resolved from
    the game's install folder (``.../steamapps/common/<dir>``) taken from that
    path. ``$PWD`` is only a fallback: Steam starts a game in the launcher's
    own folder, which is not the install root when the launcher is in a
    subfolder.
    """
    rel = relative_exe_path.replace("\\", "/").strip().lstrip("/")
    script = (
        'g="${@: -1}"; r="${g#*/steamapps/common/}"; '
        'if [ "$r" != "$g" ]; then root="${g%%/steamapps/common/*}/steamapps/common/${r%%/*}"; '
        'else root="$PWD"; fi; '
        f'set -- "${{@:1:$#-1}}" "$root/{_bash_dq(rel)}"; exec "$@" {BYPASS_MARK}'
    )
    raw = f"bash -c '{_bash_sq(script)}' -- %command%"
    return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _yaml_unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return re.sub(r'\\(.)', r'\1', v[1:-1])
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def extract_bypass_exe(launch_option_str: Optional[str]) -> Optional[str]:
    """Relative executable of a bypass LaunchOptions value, or None.

    Reads the current format and the first one (``cmd[-1]="$PWD/<exe>"``).
    """
    if not launch_option_str:
        return None
    plain = _yaml_unquote(launch_option_str)
    if BYPASS_MARK in plain:
        script = plain.replace("'\\''", "'")
        m = re.search(r'"\$root/((?:[^"\\]|\\.)*)"', script)
        if m:
            return re.sub(r'\\(.)', r'\1', m.group(1)).strip() or None
        return None
    m = re.search(r'cmd\[-1\]=\\?["\']?\$PWD/([^";\'\n\\]+)', launch_option_str)
    if m:
        return m.group(1).strip()
    return None


def is_legacy_bypass(launch_option_str: Optional[str]) -> bool:
    """True for the first bypass format, which relied on $PWD being the install root."""
    return bool(launch_option_str) and BYPASS_MARK not in _yaml_unquote(launch_option_str) \
        and extract_bypass_exe(launch_option_str) is not None


def get_bypass_exe(config_path: Path, app_id: str) -> Optional[str]:
    """Get the bypassed relative executable for an AppID, if configured."""
    opt = get_launch_option(config_path, app_id)
    return extract_bypass_exe(opt)


def set_bypass_exe(config_path: Path, app_id: str, relative_exe_path: str) -> bool:
    """Configure launcher bypass for an AppID in SLSsteam config.yaml."""
    cmd = build_bypass_launch_command(relative_exe_path)
    return add_launch_option(config_path, app_id, cmd)


def remove_bypass(config_path: Path, app_id: str) -> bool:
    """Remove launcher bypass for an AppID from SLSsteam config.yaml."""
    return remove_launch_option(config_path, app_id)

