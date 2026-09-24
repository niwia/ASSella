import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from utils.settings import get_settings

logger = logging.getLogger(__name__)

# Note: helpers keep repeated guard/IO logic centralized for clarity.


def _config_management_enabled() -> bool:
    return is_slssteam_mode_enabled() and is_slssteam_config_management_enabled()


def _read_config_content(config_path: Path, log_missing: bool = False) -> Optional[str]:
    if not config_path.exists():
        if log_missing:
            logger.warning(f"Config file not found at {config_path}")
        return None

    with open(config_path, "r", encoding="utf-8") as f:
        return f.read()


_CONFIG_DISABLED = object()


def _get_config_content_if_enabled(config_path: Path, log_missing: bool = False):
    if not _config_management_enabled():
        return _CONFIG_DISABLED
    return _read_config_content(config_path, log_missing=log_missing)


BACKUP_SUFFIX = ".bak"


def is_slssteam_mode_enabled() -> bool:
    """Check if Steam integration is enabled for the current platform."""
    settings = get_settings()
    if sys.platform == "linux":
        return settings.value("library_mode", False, type=bool)
    return settings.value("slssteam_mode", False, type=bool)


def is_greenluma_wrapper_mode_enabled() -> bool:
    """Check if GreenLuma wrapper mode is enabled on Windows."""
    if sys.platform != "win32":
        return False

    settings = get_settings()
    return settings.value("slssteam_mode", False, type=bool)


def is_slssteam_config_management_enabled() -> bool:
    """Check if SLSsteam config management is enabled in settings."""
    settings = get_settings()
    return settings.value("sls_config_management", True, type=bool)


def get_fake_appid_for_online() -> str:
    """Get the FakeAppId to use for playing games online.

    Returns:
        The appid from settings, or "480" (Spacewar) if not set.
    """
    settings = get_settings()
    fake_appid = settings.value("fake_appid_for_online", "", type=str).strip()
    return fake_appid if fake_appid else "480"


def _create_backup(config_path: Path) -> bool:
    """Create a backup of the config file.

    Creates config.yaml.bak with the current config content.
    Only creates backup if source file exists.
    Does not overwrite existing backup if new file is smaller.
    """
    try:
        if not config_path.exists():
            return False

        backup_path = config_path.with_name(config_path.name + BACKUP_SUFFIX)

        # Check if backup already exists and new file is smaller
        if backup_path.exists():
            new_size = config_path.stat().st_size
            backup_size = backup_path.stat().st_size
            if new_size < backup_size:
                logger.debug(
                    f"Skipping backup: new file ({new_size} bytes) is smaller "
                    f"than existing backup ({backup_size} bytes)"
                )
                return True

        shutil.copy2(config_path, backup_path)
        logger.info(f"Created backup: {backup_path}")
        return True
    except OSError as e:
        logger.error(f"Failed to create backup for {config_path}: {e}", exc_info=True)
        return False


def backup_config_on_startup(config_path: Path) -> bool:
    """Create a backup of the config file on application startup."""
    return _create_backup(config_path)


def _atomic_write(config_path: Path, content: str) -> bool:
    """Write content to config file in-place to preserve inode and trigger inotify FileWatcher without empty-file window."""
    try:
        if config_path.exists():
            with open(config_path, "r+", encoding="utf-8") as f:
                f.seek(0)
                f.write(content)
                f.truncate()
                f.flush()
                os.fsync(f.fileno())
        else:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        return True
    except OSError as e:
        logger.error(f"Failed to write {config_path}: {e}", exc_info=True)
        return False


def ensure_slssteam_api_enabled(config_path: Path) -> bool:
    """Ensure SLSsteam API is enabled in config.yaml."""
    if not is_slssteam_mode_enabled():
        logger.debug("Steam integration is disabled, skipping API enable check")
        return False
    if not is_slssteam_config_management_enabled():
        logger.debug("SLSsteam config management disabled, skipping API enable check")
        return False
    return update_yaml_boolean_value(config_path, "API", True)


def ensure_slssteam_logging_enabled(config_path: Path) -> bool:
    """Ensure SLSsteam has the Once (0x2) log level enabled in config.yaml.

    Checks both:
      - New SLS bitmask format (LogLevels): ensures bit 1 (0x2 / Once) is set via bitwise OR.
      - Old SLS enum format (LogLevel): ensures level is 0 (Once).

    Preserves indentation, surrounding lines, comments, and file inode.
    """
    if not is_slssteam_mode_enabled():
        logger.debug("Steam integration is disabled, skipping logging enable check")
        return False
    if not is_slssteam_config_management_enabled():
        logger.debug("SLSsteam config management disabled, skipping logging enable check")
        return False

    try:
        if not config_path.exists():
            logger.warning(f"Config file not found at {config_path}")
            return False

        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 1. Check for new bitmask format: "LogLevels: <value>"
        pattern_new = re.compile(
            r"^([ \t]*)LogLevels[ \t]*:[ \t]*([^\r\n#]+)(.*)$",
            re.MULTILINE,
        )
        match_new = pattern_new.search(content)
        if match_new:
            indent = match_new.group(1)
            raw_val = match_new.group(2).strip().strip('"').strip("'")
            comment = match_new.group(3)

            try:
                if raw_val.lower().startswith("0x"):
                    current_val = int(raw_val, 16)
                else:
                    current_val = int(raw_val, 10)
            except ValueError:
                current_val = 0

            # Check if Once bit (0x2) is already enabled
            if (current_val & 0x2) != 0:
                logger.debug(f"LogLevels in {config_path} already has Once flag enabled (0x{current_val:X})")
                return False

            new_val = current_val | 0x2
            hex_str = f"0x{new_val:X}"
            comment_str = f" {comment.strip()}" if comment.strip() else ""
            replacement = f"{indent}LogLevels: {hex_str}{comment_str}"
            new_content = pattern_new.sub(replacement, content, count=1)

            if not _atomic_write(config_path, new_content):
                return False

            logger.info(f"Updated SLSsteam LogLevels from 0x{current_val:X} to {hex_str} in {config_path}")
            return True

        # 2. Check for old enum format: "LogLevel: <value>"
        pattern_old = re.compile(
            r"^([ \t]*)LogLevel[ \t]*:[ \t]*([^\r\n#]+)(.*)$",
            re.MULTILINE,
        )
        match_old = pattern_old.search(content)
        if match_old:
            indent = match_old.group(1)
            raw_val = match_old.group(2).strip().strip('"').strip("'")
            comment = match_old.group(3)

            try:
                if raw_val.lower().startswith("0x"):
                    current_val = int(raw_val, 16)
                else:
                    current_val = int(raw_val, 10)
            except ValueError:
                current_val = 0

            # In old enum: Once = 0, Debug = 1, Info = 2, NotifyShort = 3, NotifyLong = 4, Warn = 5, None = 6
            if current_val != 0:
                comment_str = f" {comment.strip()}" if comment.strip() else ""
                replacement = f"{indent}LogLevel: 0{comment_str}"
                new_content = pattern_old.sub(replacement, content, count=1)

                if not _atomic_write(config_path, new_content):
                    return False

                logger.info(f"Updated old SLSsteam LogLevel from {current_val} to 0 in {config_path}")
                return True
            else:
                logger.debug(f"Old LogLevel in {config_path} is already 0 (Once)")
                return False

        logger.debug(f"No LogLevels/LogLevel key found in {config_path} (default enables all)")
        return False

    except OSError as e:
        logger.error(f"Failed to ensure SLSsteam logging in {config_path}: {e}", exc_info=True)
        return False


def ensure_slssteam_prerequisites(config_path: Optional[Path] = None) -> bool:
    """Silently ensure all SLSsteam configuration prerequisites are met.

    Specifically ensures:
      1. API: yes (for communication via /tmp/SLSsteam.API)
      2. LogLevels has 0x2 / Once flag enabled (or old LogLevel: 0)

    Creates a backup before applying any modifications and performs in-place atomic writes.
    Never shows disruptive UI popups — logs actions at INFO/DEBUG level.
    """
    if config_path is None:
        config_path = get_user_config_path()

    if not config_path.exists():
        logger.debug(f"ensure_slssteam_prerequisites: Config not found at {config_path}")
        return False

    if not is_slssteam_config_management_enabled():
        logger.debug("ensure_slssteam_prerequisites: SLS config management disabled in settings")
        return False

    changed = False
    _create_backup(config_path)

    try:
        if ensure_slssteam_api_enabled(config_path):
            changed = True
            logger.info("Silently ensured SLSsteam API is enabled in config.yaml")

        if ensure_slssteam_logging_enabled(config_path):
            changed = True
            logger.info("Silently ensured SLSsteam LogLevels includes 0x2 (Once) in config.yaml")
    except Exception as e:
        logger.warning(f"Error ensuring SLSsteam prerequisites: {e}")

    return changed



def get_yaml_boolean_value(config_path: Path, key: str, default: bool = False) -> bool:
    """Get a boolean value from YAML config using regex matching."""
    try:
        if not config_path.exists():
            return default

        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        pattern = re.compile(
            r"^[ \t]*"
            + re.escape(key)
            + r"[ \t]*:[ \t]*(yes|no|true|false|Yes|No|True|False)\b",
            re.MULTILINE,
        )
        match = pattern.search(content)
        if match:
            val_str = match.group(1).lower()
            return val_str in ("yes", "true", "1")
        return default
    except Exception as e:
        logger.warning(f"Error reading '{key}' from {config_path}: {e}")
        return default


def update_yaml_boolean_value(config_path: Path, key: str, value: bool) -> bool:
    """Update a boolean value in YAML config using regex pattern matching, appending if missing."""
    try:
        if not config_path.exists():
            logger.warning(f"Config file not found at {config_path}")
            return False

        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Regex pattern to match the key with its current value
        pattern = re.compile(
            r"^([ \t]*)"
            + re.escape(key)
            + r"[ \t]*:[ \t]*(yes|no|true|false|Yes|No|True|False)\b",
            re.MULTILINE,
        )

        new_value = "yes" if value else "no"
        match = pattern.search(content)
        if not match:
            logger.info(f"Key '{key}' not found in {config_path}, appending '{key}: {new_value}'")
            new_content = content.rstrip() + f"\n\n{key}: {new_value}\n"
            if not _atomic_write(config_path, new_content):
                return False
            return True

        indent = match.group(1)
        old_value = match.group(2)

        # Check if already set correctly
        if old_value.lower() == new_value.lower():
            logger.debug(f"Key '{key}' is already set to {new_value}")
            return False

        # Create replacement string preserving indentation
        replacement = f"{indent}{key}: {new_value}"

        # Replace only the matched line
        new_content = pattern.sub(replacement, content, count=1)

        if not _atomic_write(config_path, new_content):
            return False

        logger.info(f"Updated '{key}' to {new_value} in {config_path}")
        return True

    except OSError as e:
        logger.error(f"Failed to update '{key}' in {config_path}: {e}", exc_info=True)
        return False


def calculate_file_sha256(file_path: Path) -> Optional[str]:
    """Calculate SHA256 checksum of a file."""
    if not file_path.is_file():
        return None
    try:
        import hashlib
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        logger.error(f"Failed to calculate SHA256 for {file_path}: {e}")
        return None


def get_sls_plugins_dirs() -> List[Path]:
    """Resolve target plugin directories based on detected Native/Flatpak Steam environments."""
    dirs: List[Path] = []
    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        primary_dir = env.sls_config_dir / "plugins"
        dirs.append(primary_dir)

        # Check if alternate environment exists on disk (Flatpak vs Native)
        alt_base = (
            Path.home() / ".config" / "SLSsteam"
            if env.is_flatpak
            else Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / ".config" / "SLSsteam"
        )
        if alt_base.exists():
            alt_dir = alt_base / "plugins"
            if alt_dir not in dirs:
                dirs.append(alt_dir)
    except Exception as e:
        logger.warning(f"Error resolving SteamEnv for plugins: {e}")
        dirs.append(Path.home() / ".config" / "SLSsteam" / "plugins")

    return dirs


def deploy_sls_plugin(plugin_filename: str) -> Tuple[bool, bool, str]:
    """Deploy a specific bundled plugin file to SLSsteam plugin directories.

    Returns:
        (success: bool, skipped: bool, message: str)
        - skipped=True if all target locations already have the matching SHA-256.
    """
    from utils.paths import Paths
    src_path = Paths.resource(f"plugins/{plugin_filename}")
    if not src_path.is_file():
        fallback = Path(__file__).resolve().parent.parent / "res" / "plugins" / plugin_filename
        if fallback.is_file():
            src_path = fallback
        else:
            return False, False, f"Bundled plugin '{plugin_filename}' not found."

    src_hash = calculate_file_sha256(src_path)
    if not src_hash:
        return False, False, f"Could not compute hash for source plugin '{plugin_filename}'."

    target_dirs = get_sls_plugins_dirs()
    all_matched = True
    any_deployed = False
    errors = []

    for tdir in target_dirs:
        try:
            tdir.mkdir(parents=True, exist_ok=True)
            dst_file = tdir / plugin_filename
            if dst_file.is_file():
                dst_hash = calculate_file_sha256(dst_file)
                if dst_hash == src_hash:
                    logger.debug(f"Plugin {plugin_filename} at {dst_file} has matching hash {src_hash[:8]}, skipping.")
                    continue

            all_matched = False
            shutil.copy2(src_path, dst_file)
            any_deployed = True
            logger.info(f"Deployed {plugin_filename} to {dst_file}")
        except Exception as exc:
            errors.append(f"{tdir}: {exc}")

    if errors:
        return False, False, f"Error deploying {plugin_filename}: {'; '.join(errors)}"

    if all_matched and not any_deployed:
        return True, True, f"{plugin_filename} is already up to date (SHA-256 matched). Skipped deployment."

    return True, False, f"Successfully deployed {plugin_filename} to SLSsteam."


def deploy_all_sls_plugins() -> Tuple[bool, List[str]]:
    """Deploy all 3 required plugins: assella_bridge.lua, download.lua, spliced-tickets.lua."""
    plugins = ["assella_bridge.lua", "download.lua", "spliced-tickets.lua"]
    results = []
    overall_ok = True
    for p in plugins:
        ok, skipped, msg = deploy_sls_plugin(p)
        results.append(msg)
        if not ok:
            overall_ok = False
    return overall_ok, results


def are_sls_plugins_deployed() -> bool:
    """Check if all 3 required plugins exist in at least the primary SLSsteam plugins directory."""
    plugins = ["assella_bridge.lua", "download.lua", "spliced-tickets.lua"]
    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return False
    primary = target_dirs[0]
    return all((primary / p).is_file() for p in plugins)



def get_user_config_path() -> Path:
    """Get the path to the user's SLSsteam config.yaml file.

    Delegates to SteamEnv for Flatpak-aware path resolution:
      - Flatpak Steam: ~/.var/app/com.valvesoftware.Steam/.config/SLSsteam/config.yaml
      - Native Steam:  $XDG_CONFIG_HOME/SLSsteam/config.yaml  (or ~/.config/SLSsteam/config.yaml)

    Emits a warning if the resolved config does not exist yet.
    """
    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        config_path = env.sls_config_path
        if not config_path.exists():
            logger.warning(
                f"SLSsteam config.yaml not found at expected location: {config_path}. "
                f"(Steam type: {'Flatpak' if env.is_flatpak else 'Native'}) "
                "SLSsteam may not be installed or configured yet."
            )
        return config_path
    except Exception as e:
        # Graceful fallback: if SteamEnv fails for any reason, use the original native path
        logger.warning(f"get_user_config_path: SteamEnv unavailable ({e}), falling back to native path")
        xdg_config_home_str = os.environ.get("XDG_CONFIG_HOME", "")
        xdg_config_home = (
            Path(xdg_config_home_str).expanduser() if xdg_config_home_str else Path()
        )
        if xdg_config_home_str and Path(xdg_config_home_str).is_absolute():
            config_dir = xdg_config_home / "SLSsteam"
        else:
            config_dir = Path.home() / ".config" / "SLSsteam"
        return config_dir / "config.yaml"


TOP_LEVEL_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_]+[ \t]*:", re.MULTILINE)


def _get_section_bounds(content: str, section_name: str) -> Optional[Tuple[int, int, int]]:
    """Return (header_start, content_start, section_end) for a top-level YAML section.

    header_start: index of the first character of the section header line.
    content_start: index of the first character after the newline of the header.
    section_end: index of the start of the next top-level key line or EOF.
    """
    header_pattern = re.compile(
        rf"^[ \t]*{re.escape(section_name)}[ \t]*:[ \t]*(?:#[^\r\n]*)?$",
        re.MULTILINE,
    )
    match = header_pattern.search(content)
    if not match:
        return None

    header_start = match.start()
    content_start = match.end()
    if content_start < len(content) and content[content_start] == "\r":
        content_start += 1
    if content_start < len(content) and content[content_start] == "\n":
        content_start += 1

    after_section = content[content_start:]
    next_match = TOP_LEVEL_KEY_PATTERN.search(after_section)
    section_end = (content_start + next_match.start()) if next_match else len(content)

    return header_start, content_start, section_end


def _get_section_start(content: str, pattern: re.Pattern) -> Optional[int]:
    match = pattern.search(content)
    if not match:
        return None
    section_start = match.end()
    if section_start < len(content) and content[section_start] == "\r":
        section_start += 1
    if section_start < len(content) and content[section_start] == "\n":
        section_start += 1
    return section_start


def _get_section_end(
    content: str, section_start: int, next_key_pattern: re.Pattern
) -> int:
    after_section = content[section_start:]
    next_match = next_key_pattern.search(after_section)
    if next_match:
        return section_start + next_match.start()
    return len(content)


def _remove_entry_from_section(
    config_path: Path,
    section_name: str,
    pattern: re.Pattern,
    success_message: str,
    error_message: str,
) -> bool:
    """Remove matching lines only within a specific top-level YAML section."""
    try:
        content = _read_config_content(config_path)
        if content is None:
            return False

        removed = False
        while True:
            bounds = _get_section_bounds(content, section_name)
            if not bounds:
                break
            _, content_start, section_end = bounds
            section_content = content[content_start:section_end]
            match = pattern.search(section_content)
            if not match:
                break

            abs_match_start = content_start + match.start()
            line_start = content.rfind("\n", 0, abs_match_start)
            line_start = 0 if line_start == -1 else line_start + 1

            line_end = content.find("\n", abs_match_start)
            if line_end == -1:
                line_end = len(content)
            else:
                line_end += 1

            content = content[:line_start] + content[line_end:]
            removed = True

        if not removed:
            return False

        if not _atomic_write(config_path, content):
            return False

        logger.info(success_message)
        return True
    except OSError as e:
        logger.error(error_message.format(e=e), exc_info=True)
        return False


def _fix_additional_apps_indentation(content: str) -> Tuple[str, bool]:
    """Fix indentation of AdditionalApps list items."""
    bounds = _get_section_bounds(content, "AdditionalApps")
    if not bounds:
        return content, False

    _, content_start, section_end = bounds
    section_content = content[content_start:section_end]

    misaligned_item_pattern = re.compile(
        r"^[ \t]*-[ \t]*([^\r\n#]+?)(?=[ \t]*(?:#|$))", re.MULTILINE
    )
    fixed_section = misaligned_item_pattern.sub(r"  - \1", section_content)

    if fixed_section != section_content:
        fixed_content = content[:content_start] + fixed_section + content[section_end:]
        logger.debug("Fixed indentation of AdditionalApps list items")
        return fixed_content, True

    return content, False


def _get_app_tokens_section(content: str) -> str:
    """Extract the AppTokens section from YAML content."""
    bounds = _get_section_bounds(content, "AppTokens")
    if not bounds:
        return ""
    _, content_start, section_end = bounds
    return content[content_start:section_end]


def _fix_app_tokens_indentation(content: str) -> Tuple[str, bool]:
    """Fix indentation of AppTokens entries to have 2-space indentation."""
    bounds = _get_section_bounds(content, "AppTokens")
    if not bounds:
        return content, False

    _, content_start, section_end = bounds
    section_content = content[content_start:section_end]

    token_pattern = re.compile(r"^[ \t]*(\d+[ \t]*:[^\r\n]*)", re.MULTILINE)
    fixed_section = token_pattern.sub(r"  \1", section_content)

    if fixed_section != section_content:
        fixed_content = content[:content_start] + fixed_section + content[section_end:]
        logger.debug("Fixed indentation of AppTokens entries")
        return fixed_content, True

    return content, False


def fix_slssteam_config_indentation(config_path: Path) -> bool:
    """Fix indentation of AdditionalApps and AppTokens entries."""
    try:
        content = _get_config_content_if_enabled(config_path)
        if content is _CONFIG_DISABLED or content is None:
            return False

        fixed_content, mod_apps = _fix_additional_apps_indentation(content)
        fixed_content, mod_tokens = _fix_app_tokens_indentation(fixed_content)

        if mod_apps or mod_tokens:
            if not _atomic_write(config_path, fixed_content):
                return False
            logger.info(f"Fixed indentation in {config_path}")
            return True

        return False

    except OSError as e:
        logger.error(f"Failed to fix indentation in {config_path}: {e}", exc_info=True)
        return False


def _init_config_with_app(config_path: Path, app_id: str, comment: str) -> bool:
    """Create new config file with a single AdditionalApps entry."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if comment:
        new_entry = f"AdditionalApps:\n  - {app_id} # {comment}\n"
    else:
        new_entry = f"AdditionalApps:\n  - {app_id}\n"

    if _atomic_write(config_path, new_entry):
        logger.info(f"Created config file with AppID '{app_id}' in {config_path}")
        return True
    return False


def _append_to_additional_apps(
    content: str, app_id: str, comment: str, bounds: Tuple[int, int, int]
) -> str:
    """Append AppID to existing AdditionalApps section directly after the last list item."""
    _, content_start, section_end = bounds
    sec_content = content[content_start:section_end]
    lines = sec_content.splitlines(keepends=True)

    last_item_end_offset = 0
    curr_offset = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("-"):
            last_item_end_offset = curr_offset + len(line)
        elif stripped and not stripped.startswith("#") and not line.startswith(" ") and not line.startswith("\t"):
            break
        curr_offset += len(line)

    entry_line = f"  - {app_id} # {comment}\n" if comment else f"  - {app_id}\n"
    if last_item_end_offset > 0:
        insert_pos = content_start + last_item_end_offset
    else:
        insert_pos = content_start

    return content[:insert_pos] + entry_line + content[insert_pos:]


def add_additional_app(config_path: Path, app_id: str, comment: str = "") -> bool:
    """Add an AppID to the AdditionalApps list in SLSsteam config.yaml."""
    try:
        content = _read_config_content(config_path)
        if content is None:
            return _init_config_with_app(config_path, app_id, comment)

        fixed_content, _ = _fix_additional_apps_indentation(content)

        bounds = _get_section_bounds(fixed_content, "AdditionalApps")
        app_id_pattern = re.compile(
            rf"^[ \t]*-[ \t]*{re.escape(app_id)}[ \t]*(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )

        entry_line = f"  - {app_id} # {comment}\n" if comment else f"  - {app_id}\n"

        if bounds:
            _, content_start, section_end = bounds
            sec_content = fixed_content[content_start:section_end]
            if app_id_pattern.search(sec_content):
                logger.debug(f"AppID '{app_id}' already exists in AdditionalApps")
                return False

            new_content = _append_to_additional_apps(
                fixed_content, app_id, comment, bounds
            )
        else:
            new_content = fixed_content.rstrip() + f"\n\nAdditionalApps:\n{entry_line}"

        if not _atomic_write(config_path, new_content):
            return False

        logger.info(f"Added AppID '{app_id}' to AdditionalApps in {config_path}")
        return True

    except OSError as e:
        logger.error(
            f"Failed to add AppID '{app_id}' to {config_path}: {e}",
            exc_info=True,
        )
        return False


def remove_additional_app(config_path: Path, app_id: str) -> bool:
    """Remove an AppID from the AdditionalApps list."""
    app_id_pattern = re.compile(
        rf"^[ \t]*-[ \t]*{re.escape(app_id)}[ \t]*(?:#[^\r\n]*)?$",
        re.MULTILINE,
    )
    return _remove_entry_from_section(
        config_path,
        "AdditionalApps",
        app_id_pattern,
        f"Removed AppID '{app_id}' from AdditionalApps in {config_path}",
        f"Failed to remove AppID '{app_id}': {{e}}",
    )


def replace_additional_app(config_path: Path, old_app_id: str, new_app_id: str, new_comment: str = "") -> bool:
    """Replace an existing AppID in AdditionalApps with a new AppID and optional comment.
    Also migrates any DlcData or FakeAppIds entries if present.
    """
    content = _read_config_content(config_path)
    if not content:
        return False

    old_aid_str = str(old_app_id).strip()
    new_aid_str = str(new_app_id).strip()
    if not old_aid_str or not new_aid_str:
        return False

    bounds = _get_section_bounds(content, "AdditionalApps")
    if not bounds:
        return False

    _, content_start, section_end = bounds
    sec_content = content[content_start:section_end]
    pattern = re.compile(rf"^[ \t]*-[ \t]*{re.escape(old_aid_str)}[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
    match = pattern.search(sec_content)
    if not match:
        logger.warning(f"AppID '{old_aid_str}' not found in AdditionalApps section of {config_path}")
        return False

    # If new_comment not explicitly provided, preserve existing comment if any
    if not new_comment:
        m_comm = re.search(rf"^[ \t]*-[ \t]*{re.escape(old_aid_str)}[ \t]*#[ \t]*(.+)$", sec_content, re.MULTILINE)
        if m_comm:
            new_comment = m_comm.group(1).strip()

    replacement_line = f"  - {new_aid_str} # {new_comment}" if new_comment else f"  - {new_aid_str}"
    new_sec_content = pattern.sub(replacement_line, sec_content, count=1)
    updated_content = content[:content_start] + new_sec_content + content[section_end:]

    # Migrate DlcData section key if present
    dlc_bounds = _get_section_bounds(updated_content, "DlcData")
    if dlc_bounds:
        _, d_start, d_end = dlc_bounds
        d_sec = updated_content[d_start:d_end]
        d_pat = re.compile(rf"^[ \t]+{re.escape(old_aid_str)}:[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
        if d_pat.search(d_sec):
            new_d_sec = d_pat.sub(f"  {new_aid_str}:", d_sec, count=1)
            updated_content = updated_content[:d_start] + new_d_sec + updated_content[d_end:]

    if _atomic_write(config_path, updated_content):
        logger.info(f"Successfully replaced AppID '{old_aid_str}' with '{new_aid_str}' in {config_path}")
        return True
    return False


def get_additional_apps(config_path: Path) -> List[str]:
    """Get list of AppIDs currently in AdditionalApps section."""
    content = _read_config_content(config_path)
    if not content:
        return []
    bounds = _get_section_bounds(content, "AdditionalApps")
    if not bounds:
        return []
    _, content_start, section_end = bounds
    sec = content[content_start:section_end]
    results = []
    for line in sec.splitlines():
        m = re.match(r"^[ \t]*-[ \t]*([0-9]+)", line)
        if m:
            results.append(m.group(1))
    return results


def get_additional_depots(config_path: Path) -> List[str]:
    """Get list of DepotIDs currently in AdditionalDepots section."""
    content = _read_config_content(config_path)
    if not content:
        return []
    bounds = _get_section_bounds(content, "AdditionalDepots")
    if not bounds:
        return []
    _, content_start, section_end = bounds
    sec = content[content_start:section_end]
    results = []
    for line in sec.splitlines():
        m = re.match(r"^[ \t]*-[ \t]*([0-9]+)", line)
        if m:
            results.append(m.group(1))
    return results


def add_additional_depot(config_path: Path, depot_id: str, comment: str = "") -> bool:
    """Add a DepotID to the AdditionalDepots list in SLSsteam config.yaml."""
    try:
        content = _read_config_content(config_path)
        if content is None:
            return False

        depot_id_str = str(depot_id).strip()
        bounds = _get_section_bounds(content, "AdditionalDepots")
        depot_pattern = re.compile(
            rf"^[ \t]*-[ \t]*{re.escape(depot_id_str)}[ \t]*(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )

        comment_suffix = f" # {comment.strip()}" if comment and comment.strip() else ""
        entry_line = f"  - {depot_id_str}{comment_suffix}\n"

        if bounds:
            _, content_start, section_end = bounds
            sec_content = content[content_start:section_end]
            m = depot_pattern.search(sec_content)
            if m:
                if comment and comment.strip():
                    abs_start = content_start + m.start()
                    abs_end = content_start + m.end()
                    new_content = content[:abs_start] + f"  - {depot_id_str}{comment_suffix}" + content[abs_end:]
                    if _atomic_write(config_path, new_content):
                        logger.info(f"Updated comment for DepotID '{depot_id_str}' in AdditionalDepots")
                        return True
                return False

            insert_pos = section_end
            if insert_pos > 0 and content[insert_pos - 1] != "\n":
                entry_line = "\n" + entry_line
            new_content = content[:insert_pos] + entry_line + content[insert_pos:]
        else:
            new_content = content.rstrip() + f"\n\nAdditionalDepots:\n{entry_line}"

        if not _atomic_write(config_path, new_content):
            return False

        logger.info(f"Added DepotID '{depot_id_str}' to AdditionalDepots in {config_path}")
        return True
    except OSError as e:
        logger.error(f"Failed to add DepotID '{depot_id}': {e}", exc_info=True)
        return False


def remove_additional_depot(config_path: Path, depot_id: str) -> bool:
    """Remove a DepotID from the AdditionalDepots list in SLSsteam config.yaml."""
    depot_id_str = str(depot_id).strip()
    depot_pattern = re.compile(
        rf"^[ \t]*-[ \t]*{re.escape(depot_id_str)}[ \t]*(?:#[^\r\n]*)?$",
        re.MULTILINE,
    )
    return _remove_entry_from_section(
        config_path,
        "AdditionalDepots",
        depot_pattern,
        f"Removed DepotID '{depot_id_str}' from AdditionalDepots in {config_path}",
        f"Failed to remove DepotID '{depot_id_str}': {{e}}",
    )


def get_decryption_keys(config_path: Path) -> Dict[str, str]:
    """Get mapping of {depot_id: key} currently in DecryptionKeys section."""
    content = _read_config_content(config_path)
    if not content:
        return {}
    bounds = _get_section_bounds(content, "DecryptionKeys")
    if not bounds:
        return {}
    _, content_start, section_end = bounds
    sec = content[content_start:section_end]
    results = {}
    key_pattern = re.compile(
        r"^[ \t]*['\"]?([0-9]+)['\"]?[ \t]*:[ \t]*['\"]?([a-fA-F0-9]{64})['\"]?",
        re.MULTILINE,
    )
    for m in key_pattern.finditer(sec):
        results[m.group(1)] = m.group(2)
    return results


def add_decryption_key(config_path: Path, depot_id: str, key: str, comment: str = "") -> bool:
    """Add or update a depot AES decryption key in DecryptionKeys section in SLSsteam config.yaml."""
    try:
        content = _read_config_content(config_path)
        if content is None:
            return False

        depot_id_str = str(depot_id).strip()
        key_str = str(key).strip().lower()
        if len(key_str) != 64:
            logger.warning(f"Invalid AES key length ({len(key_str)}) for depot {depot_id_str}")
            return False

        bounds = _get_section_bounds(content, "DecryptionKeys")
        comment_suffix = f" # {comment.strip()}" if comment and comment.strip() else ""
        new_key_line = f"  {depot_id_str}: {key_str}{comment_suffix}\n"

        if not bounds:
            new_content = content.rstrip() + f"\n\nDecryptionKeys:\n{new_key_line}"
            if _atomic_write(config_path, new_content):
                logger.info(f"Added DecryptionKey for depot '{depot_id_str}' in new DecryptionKeys section")
                return True
            return False

        _, content_start, section_end = bounds
        sec_content = content[content_start:section_end]

        dup_pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(depot_id_str)}['\"]?[ \t]*:[ \t]*([^\r\n#]+)(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        match = dup_pattern.search(sec_content)
        if match:
            abs_start = content_start + match.start()
            abs_end = content_start + match.end()
            new_content = content[:abs_start] + f"  {depot_id_str}: {key_str}{comment_suffix}" + content[abs_end:]
        else:
            insert_pos = section_end
            if insert_pos > 0 and content[insert_pos - 1] != "\n":
                new_key_line = "\n" + new_key_line
            new_content = content[:insert_pos] + new_key_line + content[insert_pos:]

        if not _atomic_write(config_path, new_content):
            return False

        logger.info(f"Saved DecryptionKey for depot '{depot_id_str}' in {config_path}")
        return True
    except OSError as e:
        logger.error(f"Failed to add DecryptionKey for depot '{depot_id}': {e}", exc_info=True)
        return False


def remove_decryption_key(config_path: Path, depot_id: str) -> bool:
    """Remove a depot key from DecryptionKeys section in SLSsteam config.yaml."""
    depot_id_str = str(depot_id).strip()
    pattern = re.compile(
        rf"^[ \t]*['\"]?{re.escape(depot_id_str)}['\"]?[ \t]*:[ \t]*[^\r\n]+$",
        re.MULTILINE,
    )
    return _remove_entry_from_section(
        config_path,
        "DecryptionKeys",
        pattern,
        f"Removed DecryptionKey for depot '{depot_id_str}' in {config_path}",
        f"Failed to remove DecryptionKey for depot '{depot_id_str}': {{e}}",
    )


def add_dlc_data(
    config_path: Path, parent_app_id: str, dlc_id: str, dlc_name: str
) -> bool:
    """Add a DLC entry to DlcData section in SLSsteam config.yaml."""
    return add_dlc_data_batch(config_path, parent_app_id, {str(dlc_id): dlc_name})


def add_dlc_data_batch(
    config_path: Path, parent_app_id: str, dlc_dict: Dict[str, str]
) -> bool:
    """Add multiple DLC entries under parent_app_id in DlcData section in SLSsteam config.yaml."""
    if not dlc_dict:
        return True
    try:
        content = _get_config_content_if_enabled(config_path, log_missing=True)
        if content is _CONFIG_DISABLED or content is None:
            return False

        parent_app_id = str(parent_app_id).strip()
        bounds = _get_section_bounds(content, "DlcData")

        if not bounds:
            # Create new DlcData section
            lines = ["DlcData:", f"  {parent_app_id}:"]
            for did, dname in dlc_dict.items():
                cname = str(dname or f"DLC {did}").replace('"', '\\"')
                lines.append(f'    {did}: "{cname}"')
            new_entry = "\n".join(lines) + "\n"
            new_content = content.rstrip() + "\n\n" + new_entry
            return _atomic_write(config_path, new_content)

        _, dlc_start, dlc_end = bounds
        dlc_section = content[dlc_start:dlc_end]

        parent_pattern = re.compile(rf"^[ \t]+{re.escape(parent_app_id)}:[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
        parent_match = parent_pattern.search(dlc_section)

        if not parent_match:
            # Parent AppID does not exist under DlcData yet. Insert it.
            lines = [f"  {parent_app_id}:"]
            for did, dname in dlc_dict.items():
                cname = str(dname or f"DLC {did}").replace('"', '\\"')
                lines.append(f'    {did}: "{cname}"')
            insert_text = "\n".join(lines) + "\n"
            insert_pos = dlc_end
            new_content = content[:insert_pos].rstrip() + "\n" + insert_text + "\n" + content[insert_pos:].lstrip("\n")
            return _atomic_write(config_path, new_content)

        # Parent exists in DlcData section.
        p_start = dlc_start + parent_match.end()
        if p_start < len(content) and content[p_start] == "\r":
            p_start += 1
        if p_start < len(content) and content[p_start] == "\n":
            p_start += 1

        p_after = content[p_start:dlc_end]
        next_parent = re.search(r"^[ \t]+[0-9A-Za-z_]+:[ \t]*(?:#[^\r\n]*)?$", p_after, re.MULTILINE)
        parent_end = (p_start + next_parent.start()) if next_parent else dlc_end

        parent_block = content[p_start:parent_end]
        new_dlc_lines = []
        for did, dname in dlc_dict.items():
            check_pat = re.compile(rf'^[ \t]*{re.escape(str(did))}[ \t]*:[ \t]*"', re.MULTILINE)
            if not check_pat.search(parent_block):
                cname = str(dname or f"DLC {did}").replace('"', '\\"')
                new_dlc_lines.append(f'    {did}: "{cname}"')

        if not new_dlc_lines:
            return True  # All already exist

        insert_text = "\n".join(new_dlc_lines) + "\n"
        new_content = content[:parent_end].rstrip() + "\n" + insert_text + content[parent_end:]
        return _atomic_write(config_path, new_content)

    except Exception as e:
        logger.error(f"Failed to add DLC batch for '{parent_app_id}': {e}", exc_info=True)
        return False


def remove_dlc_data(
    config_path: Path, parent_app_id: str, dlc_id: Optional[str] = None
) -> bool:
    """Remove a DLC or entire parent_app_id from DlcData section in SLSsteam config.yaml."""
    try:
        content = _get_config_content_if_enabled(config_path, log_missing=True)
        if content is _CONFIG_DISABLED or content is None:
            return False

        parent_app_id = str(parent_app_id).strip()
        bounds = _get_section_bounds(content, "DlcData")
        if not bounds:
            return True

        _, dlc_start, dlc_end = bounds
        dlc_section = content[dlc_start:dlc_end]
        parent_pattern = re.compile(rf"^[ \t]+{re.escape(parent_app_id)}:[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
        parent_match = parent_pattern.search(dlc_section)
        if not parent_match:
            return True

        p_line_start = dlc_start + parent_match.start()
        p_start = dlc_start + parent_match.end()
        if p_start < len(content) and content[p_start] == "\r":
            p_start += 1
        if p_start < len(content) and content[p_start] == "\n":
            p_start += 1

        p_after = content[p_start:dlc_end]
        next_parent = re.search(r"^[ \t]+[0-9A-Za-z_]+:[ \t]*(?:#[^\r\n]*)?$", p_after, re.MULTILINE)
        p_block_end = (p_start + next_parent.start()) if next_parent else dlc_end

        if dlc_id is None:
            # Remove entire parent section
            new_content = content[:p_line_start] + content[p_block_end:]
            return _atomic_write(config_path, new_content)
        else:
            # Remove specific DLC line
            dlc_pattern = re.compile(
                rf"^[ \t]*{re.escape(str(dlc_id))}[ \t]*:[^\r\n]*\r?\n?", re.MULTILINE
            )
            target_block = content[p_start:p_block_end]
            new_block, count = dlc_pattern.subn("", target_block)
            if count > 0:
                new_content = content[:p_start] + new_block + content[p_block_end:]
                return _atomic_write(config_path, new_content)
            return True

    except Exception as e:
        logger.error(f"Failed to remove DLC data for '{parent_app_id}': {e}", exc_info=True)
        return False


def get_dlc_data(config_path: Path, parent_app_id: str) -> Dict[str, str]:
    """Retrieve all DLC entries for a given parent_app_id under DlcData in SLSsteam config.yaml."""
    try:
        content = _get_config_content_if_enabled(config_path)
        if content is _CONFIG_DISABLED or content is None:
            return {}

        parent_app_id = str(parent_app_id).strip()
        bounds = _get_section_bounds(content, "DlcData")
        if not bounds:
            return {}

        _, dlc_start, dlc_end = bounds
        dlc_section = content[dlc_start:dlc_end]
        parent_pattern = re.compile(rf"^[ \t]+{re.escape(parent_app_id)}:[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
        parent_match = parent_pattern.search(dlc_section)
        if not parent_match:
            return {}

        p_start = dlc_start + parent_match.end()
        if p_start < len(content) and content[p_start] == "\r":
            p_start += 1
        if p_start < len(content) and content[p_start] == "\n":
            p_start += 1

        p_after = content[p_start:dlc_end]
        next_parent = re.search(r"^[ \t]+[0-9A-Za-z_]+:[ \t]*(?:#[^\r\n]*)?$", p_after, re.MULTILINE)
        p_end = (p_start + next_parent.start()) if next_parent else dlc_end

        result = {}
        for line in content[p_start:p_end].splitlines():
            m = re.match(r'^[ \t]*([0-9]+)[ \t]*:[ \t]*"(.*)"[ \t]*(?:#[^\r\n]*)?$', line)
            if m:
                result[m.group(1)] = m.group(2)
        return result
    except Exception:
        return {}


def add_app_token(config_path: Path, app_id: str, token: str) -> bool:
    """Add or update an AppToken in the AppTokens section in SLSsteam config.yaml."""
    try:
        content = _get_config_content_if_enabled(config_path)
        if content is _CONFIG_DISABLED or content is None:
            return False

        fixed_content, _ = _fix_app_tokens_indentation(content)
        content = fixed_content

        app_id_str = str(app_id).strip()
        token_str = str(token).strip()

        bounds = _get_section_bounds(content, "AppTokens")
        new_token_line = f"  {app_id_str}: {token_str}\n"

        if not bounds:
            new_content = content.rstrip() + f"\n\nAppTokens:\n{new_token_line}"
            if _atomic_write(config_path, new_content):
                logger.info(f"Added AppToken for '{app_id_str}' in new AppTokens section")
                return True
            return False

        _, content_start, section_end = bounds
        sec_content = content[content_start:section_end]

        dup_pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(app_id_str)}['\"]?[ \t]*:[ \t]*([^\r\n#]+)(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        matches = list(dup_pattern.finditer(sec_content))

        if len(matches) == 1:
            existing_val = matches[0].group(1).strip().strip('"').strip("'")
            if existing_val == token_str:
                return False

        if matches:
            while True:
                b = _get_section_bounds(content, "AppTokens")
                if not b:
                    break
                _, cs, se = b
                sc = content[cs:se]
                m = dup_pattern.search(sc)
                if not m:
                    break
                abs_m_start = cs + m.start()
                l_start = content.rfind("\n", 0, abs_m_start)
                l_start = 0 if l_start == -1 else l_start + 1
                l_end = content.find("\n", abs_m_start)
                l_end = len(content) if l_end == -1 else l_end + 1
                content = content[:l_start] + content[l_end:]

        bounds = _get_section_bounds(content, "AppTokens")
        if not bounds:
            return False
        _, content_start, section_end = bounds
        sec_content = content[content_start:section_end]

        lines = sec_content.splitlines(keepends=True)
        last_item_end_offset = 0
        curr_offset = 0
        token_entry_pat = re.compile(r"^[ \t]*\d+[ \t]*:")
        for line in lines:
            if token_entry_pat.match(line):
                last_item_end_offset = curr_offset + len(line)
            elif line.strip() and not line.strip().startswith("#") and not line.startswith(" ") and not line.startswith("\t"):
                break
            curr_offset += len(line)

        if last_item_end_offset > 0:
            insert_pos = content_start + last_item_end_offset
        else:
            insert_pos = content_start

        new_content = content[:insert_pos] + new_token_line + content[insert_pos:]

        if _atomic_write(config_path, new_content):
            if len(matches) > 1:
                logger.info(f"Updated AppToken for '{app_id_str}' and removed duplicates")
            elif len(matches) == 1:
                logger.info(f"Updated AppToken for '{app_id_str}'")
            else:
                logger.info(f"Added AppToken for '{app_id_str}'")
            return True
        return False

    except OSError as e:
        logger.error(f"Failed to add AppToken '{app_id}': {e}", exc_info=True)
        return False


def get_app_tokens(config_path: Path) -> Dict[str, str]:
    """Get all AppTokens from SLSsteam config.yaml."""
    tokens = {}
    try:
        if not config_path.exists():
            return tokens

        content = _read_config_content(config_path)
        if not content:
            return tokens

        bounds = _get_section_bounds(content, "AppTokens")
        if not bounds:
            return tokens

        _, content_start, section_end = bounds
        section_content = content[content_start:section_end]

        token_pattern = re.compile(
            r"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*([^\r\n#]+)",
            re.MULTILINE,
        )

        for token_match in token_pattern.finditer(section_content):
            app_id = token_match.group(1).strip()
            token = token_match.group(2).strip().strip('"').strip("'")
            tokens[app_id] = token

    except OSError as e:
        logger.error(f"Failed to read AppTokens from {config_path}: {e}", exc_info=True)

    return tokens


def remove_app_token(config_path: Path, app_id: str) -> bool:
    """Remove an AppID entry from the AppTokens section in SLSsteam config.yaml."""
    app_id_pattern = re.compile(
        rf"^[ \t]*['\"]?{re.escape(str(app_id))}['\"]?[ \t]*:[ \t]*[^\r\n#]+[ \t]*(?:#[^\r\n]*)?$",
        re.MULTILINE,
    )
    return _remove_entry_from_section(
        config_path,
        "AppTokens",
        app_id_pattern,
        f"Removed AppID '{app_id}' from AppTokens in {config_path}",
        f"Failed to remove AppToken for '{app_id}': {{e}}",
    )


def get_fake_app_ids(config_path: Path, fake_appid: str = "") -> Set[str]:
    """Get all FakeAppIds from SLSsteam config.yaml."""
    fake_app_ids = set()

    if not fake_appid:
        fake_appid = get_fake_appid_for_online()

    try:
        content = _read_config_content(config_path)
        if not content:
            return fake_app_ids

        bounds = _get_section_bounds(content, "FakeAppIds")
        if not bounds:
            return fake_app_ids

        _, content_start, section_end = bounds
        section_content = content[content_start:section_end]
        entry_pattern = re.compile(
            rf"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*{re.escape(fake_appid)}(?:[ \t]+#[^\r\n]*|[ \t]*)$",
            re.MULTILINE,
        )

        for entry_match in entry_pattern.finditer(section_content):
            app_id = entry_match.group(1).strip()
            fake_app_ids.add(app_id)

    except OSError as e:
        logger.error(
            f"Failed to read FakeAppIds from {config_path}: {e}",
            exc_info=True,
        )

    return fake_app_ids


def get_fake_appid(config_path: Path, app_id: str) -> Optional[str]:
    """Get the FakeAppId for a specific AppID from SLSsteam config.yaml."""
    try:
        content = _read_config_content(config_path)
        if not content:
            return None

        bounds = _get_section_bounds(content, "FakeAppIds")
        if not bounds:
            return None

        _, content_start, section_end = bounds
        section_content = content[content_start:section_end]
        entry_pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(str(app_id))}['\"]?[ \t]*:[ \t]*(\d+)",
            re.MULTILINE,
        )

        entry_match = entry_pattern.search(section_content)
        if entry_match:
            return entry_match.group(1).strip()

    except OSError as e:
        logger.error(
            f"Failed to read FakeAppId for '{app_id}' from {config_path}: {e}",
            exc_info=True,
        )

    return None


def add_fake_app_id(
    config_path: Path,
    app_id: str,
    game_name: str = "",
    fake_appid: str = "",
) -> bool:
    """Add an AppID to the FakeAppIds list in SLSsteam config.yaml."""
    if not fake_appid:
        fake_appid = get_fake_appid_for_online()

    suffix = "Spacewar" if fake_appid == "480" else "SLSonline"

    try:
        content = _get_config_content_if_enabled(config_path)
        if content is _CONFIG_DISABLED:
            return False
        if content is None:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            entry = f"FakeAppIds:\n  {app_id}: {fake_appid}"
            if game_name:
                entry += f"  # {game_name} -> {suffix}\n"
            else:
                entry += "\n"
            return _atomic_write(config_path, entry)

        bounds = _get_section_bounds(content, "FakeAppIds")

        entry_line = f"  {app_id}: {fake_appid}"
        if game_name:
            entry_line += f"  # {game_name} -> {suffix}\n"
        else:
            entry_line += "\n"

        if bounds:
            _, content_start, section_end = bounds
            section_content = content[content_start:section_end]
            existing_pattern = re.compile(
                rf"^[ \t]*['\"]?{re.escape(str(app_id))}['\"]?[ \t]*:[ \t]*{re.escape(str(fake_appid))}(?:[ \t]+#[^\r\n]*|[ \t]*)$",
                re.MULTILINE,
            )
            if existing_pattern.search(section_content):
                return False

            lines = section_content.splitlines(keepends=True)
            last_entry_end_offset = 0
            curr_offset = 0
            for line in lines:
                s = line.strip()
                if s and (s[0].isdigit() or s[0] in ('"', "'")):
                    last_entry_end_offset = curr_offset + len(line)
                elif not s or s.startswith("#"):
                    pass
                else:
                    break
                curr_offset += len(line)

            if last_entry_end_offset > 0:
                insert_pos = content_start + last_entry_end_offset
            else:
                insert_pos = content_start

            new_content = content[:insert_pos] + entry_line + content[insert_pos:]
        else:
            new_content = content.rstrip() + f"\n\nFakeAppIds:\n{entry_line}"

        if not _atomic_write(config_path, new_content):
            return False

        logger.info(f"Added AppID '{app_id}' to FakeAppIds in {config_path}")
        return True

    except OSError as e:
        logger.error(f"Failed to add FakeAppId '{app_id}': {e}", exc_info=True)
        return False


def remove_fake_app_id(config_path: Path, app_id: str, fake_appid: str = "") -> bool:
    """Remove an AppID from the FakeAppIds list in SLSsteam config.yaml."""
    if fake_appid:
        app_id_pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(str(app_id))}['\"]?[ \t]*:[ \t]*{re.escape(str(fake_appid))}[ \t]*(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
    else:
        app_id_pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(str(app_id))}['\"]?[ \t]*:[ \t]*[^\r\n#]+[ \t]*(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
    return _remove_entry_from_section(
        config_path,
        "FakeAppIds",
        app_id_pattern,
        f"Removed AppID '{app_id}' from FakeAppIds in {config_path}",
        f"Failed to remove FakeAppId '{app_id}': {{e}}",
    )


def check_and_merge_fakeappid_db(config_path: Path) -> bool:
    """Check if fakeappid database integration is enabled and merge it if so.

    Returns:
        True if changes were written, False otherwise.
    """
    settings = get_settings()
    if not settings.value("fakeappid_db_integration", False, type=bool):
        return False

    if not is_slssteam_mode_enabled():
        return False

    if not is_slssteam_config_management_enabled():
        return False

    # Get database file path
    from utils.paths import Paths
    db_path = Paths.resource("fakeapps_accela.yaml")
    if not db_path.exists():
        logger.warning(f"Fake AppID database not found at {db_path}")
        return False

    # Parse database FakeAppIds
    db_fakeapps = {}
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line == "FakeAppIds:":
                    continue
                # Split at comment if any
                comment = ""
                if "#" in line:
                    line, comment = line.split("#", 1)
                    comment = comment.strip()
                if ":" in line:
                    k, v = line.split(":", 1)
                    k, v = k.strip(), v.strip()
                    if k.isdigit() and v.isdigit():
                        db_fakeapps[k] = (v, comment)
    except Exception as e:
        logger.error(f"Failed to parse Fake AppID database: {e}")
        return False

    if not db_fakeapps:
        return False

    # Load current content
    try:
        content = _read_config_content(config_path)
        if content is None:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            content = ""
    except OSError as e:
        logger.error(f"Failed to read config file {config_path}: {e}")
        return False
    bounds = _get_section_bounds(content, "FakeAppIds")

    # Let's collect existing FakeAppIds
    existing_fake_apps = {}
    if bounds:
        _, content_start, section_end = bounds
        section_content = content[content_start:section_end]
        entry_pattern = re.compile(r"^[ \t]*(\d+)[ \t]*:[ \t]*(\d+)", re.MULTILINE)
        for m in entry_pattern.finditer(section_content):
            existing_fake_apps[m.group(1).strip()] = m.group(2).strip()

    # Determine which entries are missing
    missing_entries = {}
    for appid, (fake_appid, comment) in db_fakeapps.items():
        if appid not in existing_fake_apps:
            missing_entries[appid] = (fake_appid, comment)

    if not missing_entries:
        logger.debug("No missing FakeAppIds to merge.")
        return False

    logger.info(f"Merging {len(missing_entries)} entries from database into SLSsteam FakeAppIds...")
    
    # Create the text block to insert
    insert_text = ""
    for appid, (fake_appid, comment) in missing_entries.items():
        comment_suffix = f" # {comment}" if comment else ""
        insert_text += f"  {appid}: {fake_appid}{comment_suffix}\n"

    if bounds:
        _, content_start, section_end = bounds
        new_content = content[:content_start] + insert_text + content[content_start:]
    else:
        new_content = content.rstrip() + "\n\nFakeAppIds:\n" + insert_text

    # Write atomically
    _create_backup(config_path)
    if _atomic_write(config_path, new_content):
        logger.info(f"Successfully merged Fake AppID database into {config_path}")
        return True
    return False


def clean_fakeappid_db(config_path: Path) -> bool:
    """Remove all FakeAppIds that belong to the database from SLSsteam config.yaml.

    Returns:
        True if changes were written, False otherwise.
    """
    if not config_path.exists():
        return False

    from utils.paths import Paths
    db_path = Paths.resource("fakeapps_accela.yaml")
    if not db_path.exists():
        return False

    # Parse database AppIDs
    db_appids = set()
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line == "FakeAppIds:":
                    continue
                if "#" in line:
                    line, _ = line.split("#", 1)
                if ":" in line:
                    k, _ = line.split(":", 1)
                    k = k.strip()
                    if k.isdigit():
                        db_appids.add(k)
    except Exception as e:
        logger.error(f"Failed to parse Fake AppID database: {e}")
        return False

    if not db_appids:
        return False

    try:
        content = _read_config_content(config_path)
        if not content:
            return False
    except OSError as e:
        logger.error(f"Failed to read config file {config_path}: {e}")
        return False

    bounds = _get_section_bounds(content, "FakeAppIds")
    if not bounds:
        return False

    _, content_start, section_end = bounds
    section_content = content[content_start:section_end]

    # Rebuild section content, omitting any lines that match db_appids
    new_section_lines = []
    removed_count = 0
    entry_pattern = re.compile(r"^[ \t]*(\d+)[ \t]*:")
    for line in section_content.splitlines():
        m = entry_pattern.match(line)
        if m:
            appid = m.group(1).strip()
            if appid in db_appids:
                removed_count += 1
                continue
        new_section_lines.append(line)

    if removed_count == 0:
        return False

    new_section_content = "\n".join(new_section_lines)
    if new_section_content and not new_section_content.endswith("\n"):
        new_section_content += "\n"
    new_content = content[:content_start] + new_section_content + content[section_end:]

    _create_backup(config_path)
    if _atomic_write(config_path, new_content):
        logger.info(f"Successfully cleaned {removed_count} database FakeAppIds from {config_path}")
        return True
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


# ── LaunchOptions & netsock.so for Online Play ──────────────────────────────

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



