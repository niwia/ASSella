import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from utils.settings import get_settings

logger = logging.getLogger(__name__)

# Unified canonical set of Steamworks shared redistributable depot IDs (27 IDs)
SHARED_REDISTS: Set[str] = {
    "228980", "1034630", "228981", "228982", "228983", "228984", "228985",
    "228986", "228987", "228988", "228989", "228990", "229000", "229001",
    "229002", "229003", "229004", "229005", "229006", "229007", "229010",
    "229011", "229012", "229020", "229030", "229031", "229032",
}

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


def _validate_yaml_content(content: str) -> bool:
    """Validate YAML syntax and ensure no duplicate top-level keys exist."""
    try:
        import yaml
        yaml.safe_load(content)
    except Exception as e:
        logger.error(f"YAML validation failed: {e}")
        return False

    # Check for duplicate top-level keys at column 0 (safe_load silently allows duplicate keys)
    top_keys = re.findall(r"^['\"]?([A-Za-z0-9_]+)['\"]?(?=[ \t]*:)", content, re.MULTILINE)
    seen = set()
    dupes = set()
    for k in top_keys:
        if k in seen:
            dupes.add(k)
        seen.add(k)
    if dupes:
        logger.error(f"Duplicate top-level YAML keys detected: {dupes}")
        return False

    return True


HEX_64_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
NUMERIC_ID_PATTERN = re.compile(r"^\d+$")


def _sanitize_comment(comment: Optional[str]) -> str:
    """Sanitize comment string by removing newlines and control characters to prevent line injection."""
    if not comment:
        return ""
    cleaned = str(comment).replace("\r", " ").replace("\n", " ").strip()
    return re.sub(r"[ \t]+", " ", cleaned)


def _sanitize_id(item_id: Union[str, int]) -> Optional[str]:
    """Validate and cast AppID / DepotID to a clean numeric string."""
    if item_id is None:
        return None
    s = str(item_id).strip()
    return s if NUMERIC_ID_PATTERN.match(s) else None


def _is_valid_hex64(key: str) -> bool:
    """Check if AES decryption key is a valid 64-character hexadecimal string."""
    if not key:
        return False
    return bool(HEX_64_PATTERN.match(str(key).strip()))


def _create_backup(config_path: Path) -> bool:
    """Create a backup of the config file.

    Creates config.yaml.bak with the current config content.
    Only creates backup if source file exists and is non-empty.
    """
    try:
        if not config_path.exists():
            return False

        new_size = config_path.stat().st_size
        if new_size == 0:
            logger.warning(f"Skipping backup: config file {config_path} is empty (0 bytes)")
            return False

        backup_path = config_path.with_name(config_path.name + BACKUP_SUFFIX)
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
    if not _validate_yaml_content(content):
        logger.error(f"Refusing to write invalid YAML content to {config_path}")
        return False

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


_write_in_place = _atomic_write


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


def ensure_plugins_enabled(config_path: Optional[Path] = None) -> bool:
    """Ensure 'Plugins: yes' is present and enabled in SLSsteam config.yaml.

    If missing or disabled ('no', 'false'), imposes 'Plugins: yes'.
    Creates a backup if modification is needed and writes atomically.
    """
    if config_path is None:
        config_path = get_user_config_path()

    if not config_path.exists():
        logger.debug(f"ensure_plugins_enabled: Config not found at {config_path}")
        return False

    if not is_slssteam_config_management_enabled():
        logger.debug("ensure_plugins_enabled: SLS config management disabled in settings")
        return False

    return update_yaml_boolean_value(config_path, "Plugins", True)


def ensure_smart_tickets_enabled(config_path: Optional[Path] = None, enable: bool = True) -> bool:
    """Ensure 'SmartTickets: 0x1' (or 0x0 if disabled) is present in SLSsteam config.yaml."""
    if config_path is None:
        config_path = get_user_config_path()

    if not config_path.exists():
        logger.debug(f"ensure_smart_tickets_enabled: Config not found at {config_path}")
        return False

    if not is_slssteam_config_management_enabled():
        logger.debug("ensure_smart_tickets_enabled: SLS config management disabled in settings")
        return False

    val_str = "0x1" if enable else "0x0"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        pattern = re.compile(
            r"^([ \t]*)SmartTickets[ \t]*:[ \t]*([^\r\n#]+)(.*)$",
            re.MULTILINE,
        )
        match = pattern.search(content)
        if not match:
            logger.info(f"Adding 'SmartTickets: {val_str}' to {config_path}")
            new_content = content.rstrip() + f"\n\nSmartTickets: {val_str}\n"
            return _atomic_write(config_path, new_content)

        indent = match.group(1)
        cur_val = match.group(2).strip()
        comment = match.group(3)

        if cur_val.lower() == val_str.lower():
            return True

        comment_str = f" {comment.strip()}" if comment.strip() else ""
        replacement = f"{indent}SmartTickets: {val_str}{comment_str}"
        new_content = pattern.sub(replacement, content, count=1)
        return _atomic_write(config_path, new_content)
    except Exception as e:
        logger.error(f"Failed to update SmartTickets in {config_path}: {e}")
        return False


def is_smart_tickets_enabled(config_path: Optional[Path] = None) -> bool:
    """Check if 'SmartTickets: 0x1' is enabled in SLSsteam config.yaml."""
    if config_path is None:
        config_path = get_user_config_path()

    if not config_path.exists():
        return False

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        match = re.search(r"^([ \t]*)SmartTickets[ \t]*:[ \t]*([^\r\n#]+)", content, re.MULTILINE)
        if match:
            raw = match.group(2).strip().lower()
            return raw in ("0x1", "1", "yes", "true")
    except Exception:
        pass
    return False


def ensure_slssteam_prerequisites(config_path: Optional[Path] = None) -> bool:
    """Silently ensure all SLSsteam configuration prerequisites are met.

    Specifically ensures:
      1. API: yes (for communication via /tmp/SLSsteam.API)
      2. LogLevels has 0x2 / Once flag enabled (or old LogLevel: 0)
      3. Plugins: yes (for library-inject plugin loader)

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

        if ensure_plugins_enabled(config_path):
            changed = True
            logger.info("Silently ensured SLSsteam Plugins: yes is enabled in config.yaml")
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
    """Deploy a specific SLSsteam plugin on demand from Cloud / local cache.

    Returns:
        (success: bool, skipped: bool, message: str)
        - skipped=True if all target locations already have the matching SHA-256.
    """
    from utils.plugin_manager import deploy_plugin
    return deploy_plugin(plugin_filename)


def deploy_all_sls_plugins() -> Tuple[bool, List[str]]:
    """Deploy required plugins (download.lua, spliced-tickets.lua) from Cloud on demand."""
    from utils.plugin_manager import deploy_all_plugins
    return deploy_all_plugins()


def are_sls_plugins_deployed() -> bool:
    """Check if the required plugins exist in at least the primary SLSsteam plugins directory."""
    from utils.plugin_manager import are_all_plugins_installed
    return are_all_plugins_installed()


def is_slssteam_plugins_enabled() -> bool:
    """Check if the user has enabled SLSsteam plugins in ASSella or in config.yaml.

    Returns:
        bool: True if plugins are enabled in settings or config.yaml.
    """
    settings = get_settings()

    # 1. Check ASSella explicit user settings (enable_vapor / enable_at0m)
    vapor_val = settings.value("enable_vapor", None)
    if vapor_val is not None:
        return settings.value("enable_vapor", type=bool)

    at0m_val = settings.value("enable_at0m", None)
    if at0m_val is not None:
        return settings.value("enable_at0m", type=bool)

    # 2. Check config.yaml Plugins key
    cfg_path = get_user_config_path()
    if cfg_path.exists():
        return get_yaml_boolean_value(cfg_path, "Plugins", default=False)

    return False


def sync_plugins_on_startup() -> bool:
    """Startup auto-sync disabled to prevent destructive overwriting of custom plugins."""
    return True



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


TOP_LEVEL_KEY_PATTERN = re.compile(r"^['\"]?[A-Za-z0-9_]+['\"]?[ \t]*:", re.MULTILINE)


def _get_section_bounds(content: str, section_name: str) -> Optional[Tuple[int, int, int]]:
    """Return (header_start, content_start, section_end) for a top-level YAML section.

    header_start: index of the first character of the section header line.
    content_start: index of the first character after the newline of the header.
    section_end: index of the start of the next top-level key line or EOF.
    """
    header_pattern = re.compile(
        r"^[ \t]*['\"]?" + re.escape(section_name) + r"['\"]?[ \t]*:[ \t]*(?:\[[ \t]*\]|\{[ \t]*\})?[ \t]*(?:#[^\r\n]*)?$",
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


def _expand_flow_section_if_needed(
    content: str, section_name: str, bounds: Tuple[int, int, int]
) -> Tuple[str, Tuple[int, int, int]]:
    """If a top-level section is written in flow style like 'Section: []', expand to block header 'Section:'."""
    h_start, c_start, s_end = bounds
    header_line = content[h_start:c_start]
    flow_pattern = re.compile(
        r"^([ \t]*" + re.escape(section_name) + r"[ \t]*:)[ \t]*(?:\[[ \t]*\]|\{[ \t]*\})([ \t]*(?:#[^\r\n]*)?[\r\n]*)$"
    )
    m = flow_pattern.match(header_line)
    if m:
        new_header = f"{m.group(1)}{m.group(2)}"
        content = content[:h_start] + new_header + content[c_start:]
        new_bounds = _get_section_bounds(content, section_name)
        if new_bounds:
            return content, new_bounds
    return content, bounds


def _find_section_insert_pos(content: str, bounds: Tuple[int, int, int]) -> int:
    """Find the best position to insert a new entry into a section.
    Inserts right after the last indented list or map item, avoiding placing new entries
    below leading comments or whitespace of the subsequent section.
    """
    _, content_start, section_end = bounds
    section_text = content[content_start:section_end]

    lines = section_text.splitlines(keepends=True)
    last_item_end = 0
    curr_offset = 0

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and (line.startswith(" ") or line.startswith("\t")):
            last_item_end = curr_offset + len(line)
        elif stripped and not stripped.startswith("#") and not line.startswith(" ") and not line.startswith("\t"):
            break
        curr_offset += len(line)

    if last_item_end > 0:
        return content_start + last_item_end

    return content_start


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


def _remove_entry_in_memory(
    content: str,
    section_name: str,
    pattern: re.Pattern,
) -> Tuple[str, bool]:
    """Remove matching lines only within a specific top-level YAML section in memory."""
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

    return content, removed


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

        new_content, removed = _remove_entry_in_memory(content, section_name, pattern)
        if not removed:
            return False

        if not _atomic_write(config_path, new_content):
            return False

        logger.info(success_message)
        return True
    except OSError as e:
        logger.error(error_message.format(e=e), exc_info=True)
        return False


def _add_list_item_in_memory(
    content: str, section_name: str, item_id: str, comment: str = ""
) -> Tuple[str, bool]:
    """Add an item to a YAML list section in memory."""
    bounds = _get_section_bounds(content, section_name)
    comment_suffix = f" # {comment}" if comment else ""
    entry_line = f"  - {item_id}{comment_suffix}\n"

    if bounds:
        content, bounds = _expand_flow_section_if_needed(content, section_name, bounds)
        _, content_start, section_end = bounds
        sec_content = content[content_start:section_end]
        item_pattern = re.compile(
            rf"^[ \t]*-[ \t]*{re.escape(item_id)}[ \t]*(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        m = item_pattern.search(sec_content)
        if m:
            if comment:
                abs_start = content_start + m.start()
                abs_end = content_start + m.end()
                new_line = f"  - {item_id}{comment_suffix}"
                if content[abs_start:abs_end] != new_line:
                    return content[:abs_start] + new_line + content[abs_end:], True
            return content, False

        insert_pos = _find_section_insert_pos(content, bounds)
        if insert_pos > 0 and content[insert_pos - 1] != "\n":
            entry_line = "\n" + entry_line
        return content[:insert_pos] + entry_line + content[insert_pos:], True
    else:
        new_content = content.rstrip() + f"\n\n{section_name}:\n{entry_line}"
        return new_content, True


def _add_map_item_in_memory(
    content: str, section_name: str, key: str, value: str, comment: str = ""
) -> Tuple[str, bool]:
    """Add or update a key-value pair in a YAML map section in memory."""
    bounds = _get_section_bounds(content, section_name)
    comment_suffix = f" # {comment}" if comment else ""
    entry_line = f"  {key}: {value}{comment_suffix}\n"

    if bounds:
        content, bounds = _expand_flow_section_if_needed(content, section_name, bounds)
        _, content_start, section_end = bounds
        sec_content = content[content_start:section_end]
        map_pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(key)}['\"]?[ \t]*:[ \t]*([^\r\n#]+)(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        m = map_pattern.search(sec_content)
        if m:
            existing_val = m.group(1).strip().strip('"').strip("'")
            existing_comment = ""
            m_comm = re.search(r"#[ \t]*(.*)$", m.group(0))
            if m_comm:
                existing_comment = m_comm.group(1).strip()

            final_comment = comment if comment else existing_comment
            final_suffix = f" # {final_comment}" if final_comment else ""
            new_line = f"  {key}: {value}{final_suffix}"

            abs_start = content_start + m.start()
            abs_end = content_start + m.end()
            if content[abs_start:abs_end] != new_line:
                return content[:abs_start] + new_line + content[abs_end:], True
            return content, False

        insert_pos = _find_section_insert_pos(content, bounds)
        if insert_pos > 0 and content[insert_pos - 1] != "\n":
            entry_line = "\n" + entry_line
        return content[:insert_pos] + entry_line + content[insert_pos:], True
    else:
        new_content = content.rstrip() + f"\n\n{section_name}:\n{entry_line}"
        return new_content, True


def _add_dlc_batch_in_memory(
    content: str, parent_app_id: str, dlc_dict: Dict[str, str]
) -> Tuple[str, bool]:
    """Add multiple DLC entries under parent_app_id in DlcData section in memory."""
    if not dlc_dict:
        return content, False

    parent_app_id = str(parent_app_id).strip()
    bounds = _get_section_bounds(content, "DlcData")

    if not bounds:
        lines = ["DlcData:", f"  {parent_app_id}:"]
        for did, dname in dlc_dict.items():
            did_str = _sanitize_id(did) or str(did).strip()
            cname = _sanitize_comment(dname or f"DLC {did_str}").replace('"', '\\"')
            lines.append(f'    {did_str}: "{cname}"')
        new_entry = "\n".join(lines) + "\n"
        new_content = content.rstrip() + "\n\n" + new_entry
        return new_content, True

    content, bounds = _expand_flow_section_if_needed(content, "DlcData", bounds)
    _, dlc_start, dlc_end = bounds
    dlc_section = content[dlc_start:dlc_end]

    parent_pattern = re.compile(rf"^[ \t]+{re.escape(parent_app_id)}:[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
    parent_match = parent_pattern.search(dlc_section)

    if not parent_match:
        lines = [f"  {parent_app_id}:"]
        for did, dname in dlc_dict.items():
            did_str = _sanitize_id(did) or str(did).strip()
            cname = _sanitize_comment(dname or f"DLC {did_str}").replace('"', '\\"')
            lines.append(f'    {did_str}: "{cname}"')
        insert_text = "\n".join(lines) + "\n"
        insert_pos = _find_section_insert_pos(content, bounds)
        new_content = content[:insert_pos].rstrip() + "\n" + insert_text + "\n" + content[insert_pos:].lstrip("\n")
        return new_content, True

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
        did_str = _sanitize_id(did) or str(did).strip()
        check_pat = re.compile(rf'^[ \t]*{re.escape(did_str)}[ \t]*:[ \t]*"', re.MULTILINE)
        if not check_pat.search(parent_block):
            cname = _sanitize_comment(dname or f"DLC {did_str}").replace('"', '\\"')
            new_dlc_lines.append(f'    {did_str}: "{cname}"')

    if not new_dlc_lines:
        return content, False

    insert_text = "\n".join(new_dlc_lines) + "\n"
    new_content = content[:parent_end].rstrip() + "\n" + insert_text + content[parent_end:]
    return new_content, True


class BatchConfigEditor:
    """Context manager and in-memory batch editor for SLSsteam config.yaml.
    Performs all modifications in memory and writes once on context exit,
    preventing filewatcher reload storms and ensuring atomicity.
    """

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.content: Optional[str] = None
        self.has_changes = False
        self.committed_successfully = True

    def __enter__(self):
        self.content = _read_config_content(self.config_path)
        if self.content is None:
            self.content = ""
        self.has_changes = False
        self.committed_successfully = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.committed_successfully = False
            logger.warning(f"Batch config edit on {self.config_path} aborted due to: {exc_val}")
            return False

        if self.has_changes and self.content is not None:
            if not _atomic_write(self.config_path, self.content):
                logger.error(f"Failed to commit batch config changes to {self.config_path}")
                self.committed_successfully = False
                return False
            self.committed_successfully = True
            logger.info(f"Committed batch config changes to {self.config_path}")
        return True

    def add_app(self, app_id: Union[str, int], comment: str = "") -> bool:
        app_id_str = _sanitize_id(app_id)
        if not app_id_str:
            logger.warning(f"Invalid AppID provided: {app_id}")
            return False
        comment_clean = _sanitize_comment(comment)
        self.content, _ = _fix_additional_apps_indentation(self.content)
        new_content, changed = _add_list_item_in_memory(
            self.content, "AdditionalApps", app_id_str, comment_clean
        )
        if changed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_app(self, app_id: Union[str, int]) -> bool:
        app_id_str = _sanitize_id(app_id)
        if not app_id_str:
            return False
        pattern = re.compile(
            rf"^[ \t]*-[ \t]*{re.escape(app_id_str)}[ \t]*(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        new_content, removed = _remove_entry_in_memory(
            self.content, "AdditionalApps", pattern
        )
        if removed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def add_depot(
        self,
        depot_id: Union[str, int],
        comment: str = "",
        app_id: Optional[Union[str, int]] = None,
    ) -> bool:
        depot_id_str = _sanitize_id(depot_id)
        if not depot_id_str:
            logger.warning(f"Invalid DepotID provided: {depot_id}")
            return False

        # Guard: never add a base game AppID into AdditionalDepots
        if app_id and depot_id_str == str(app_id).strip():
            logger.warning(f"Refusing to add base AppID '{depot_id_str}' to AdditionalDepots")
            return False

        # Guard: never add any registered base game AppID from plugin_library
        try:
            from utils.plugin_games import load_plugin_library
            plib = load_plugin_library()
            if depot_id_str in plib and not plib[depot_id_str].get("dlc_only"):
                logger.warning(f"Refusing to add registered base AppID '{depot_id_str}' to AdditionalDepots")
                return False
        except Exception:
            pass

        # Guard: check AdditionalApps (reject base AppIDs, allow verified DLC AppIDs)
        bounds_apps = _get_section_bounds(self.content, "AdditionalApps")
        if bounds_apps:
            apps_text = self.content[bounds_apps[1] : bounds_apps[2]]
            m_app = re.search(
                rf"^[ \t]*-[ \t]*{re.escape(depot_id_str)}(?:[ \t]*#[ \t]*(.*))?$",
                apps_text,
                re.MULTILINE,
            )
            if m_app:
                is_known_dlc = False
                try:
                    from utils.plugin_games import build_dlc_reverse_map
                    if depot_id_str in build_dlc_reverse_map():
                        is_known_dlc = True
                except Exception:
                    pass

                if not is_known_dlc and app_id:
                    try:
                        from utils.dlc_helpers import is_dlc_depot
                        if is_dlc_depot(depot_id_str, base_appid=app_id):
                            is_known_dlc = True
                    except Exception:
                        pass

                if not is_known_dlc:
                    cm = (m_app.group(1) or "").lower()
                    if "dlc" in cm:
                        is_known_dlc = True

                if not is_known_dlc:
                    logger.warning(
                        f"Refusing to add base AppID '{depot_id_str}' from AdditionalApps to AdditionalDepots"
                    )
                    return False

        bounds_main = _get_section_bounds(self.content, "AppIds")
        if bounds_main:
            main_text = self.content[bounds_main[1] : bounds_main[2]]
            if re.search(
                rf"^[ \t]*-[ \t]*{re.escape(depot_id_str)}(?:[ \t]*#[ \t]*(.*))?$",
                main_text,
                re.MULTILINE,
            ):
                logger.warning(
                    f"Refusing to add AppID '{depot_id_str}' from AppIds to AdditionalDepots"
                )
                return False

        if depot_id_str in SHARED_REDISTS:
            comment_clean = "Steamworks Shared"
        else:
            comment_clean = _sanitize_comment(comment)

        new_content, changed = _add_list_item_in_memory(
            self.content, "AdditionalDepots", depot_id_str, comment_clean
        )
        if changed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_depot(
        self,
        depot_id: Union[str, int],
        check_shared: bool = True,
        excluding_appid: Optional[Union[str, int]] = None,
    ) -> bool:
        depot_id_str = _sanitize_id(depot_id)
        if not depot_id_str:
            return False
        if check_shared and is_depot_shared_with_other_games(
            depot_id_str, excluding_appid=excluding_appid
        ):
            logger.info(
                f"Preserving shared depot '{depot_id_str}' in AdditionalDepots (used by other games or redistributable)"
            )
            return False
        pattern = re.compile(
            rf"^[ \t]*-[ \t]*{re.escape(depot_id_str)}[ \t]*(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        new_content, removed = _remove_entry_in_memory(
            self.content, "AdditionalDepots", pattern
        )
        if removed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def add_key(
        self,
        depot_id: Union[str, int],
        key: str,
        comment: str = "",
        app_id: Optional[Union[str, int]] = None,
    ) -> bool:
        depot_id_str = _sanitize_id(depot_id)
        if not depot_id_str:
            logger.warning(f"Invalid DepotID for key: {depot_id}")
            return False
        key_str = str(key).strip().lower()
        if not _is_valid_hex64(key_str):
            logger.warning(f"Invalid AES decryption key (not 64 hex chars) for depot {depot_id_str}")
            return False

        if depot_id_str in SHARED_REDISTS:
            comment_clean = "Steamworks Shared"
        else:
            comment_clean = _sanitize_comment(comment)

        new_content, changed = _add_map_item_in_memory(
            self.content, "DecryptionKeys", depot_id_str, key_str, comment_clean
        )
        if changed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_key(
        self,
        depot_id: Union[str, int],
        check_shared: bool = True,
        excluding_appid: Optional[Union[str, int]] = None,
    ) -> bool:
        depot_id_str = _sanitize_id(depot_id)
        if not depot_id_str:
            return False
        if check_shared and is_depot_shared_with_other_games(
            depot_id_str, excluding_appid=excluding_appid
        ):
            logger.info(
                f"Preserving decryption key for shared depot '{depot_id_str}' (used by other games or redistributable)"
            )
            return False
        pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(depot_id_str)}['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        new_content, removed = _remove_entry_in_memory(
            self.content, "DecryptionKeys", pattern
        )
        if removed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def add_dlc(
        self,
        parent_app_id: Union[str, int],
        dlc_id: Union[str, int],
        dlc_name: str = "",
    ) -> bool:
        parent_str = _sanitize_id(parent_app_id)
        dlc_str = _sanitize_id(dlc_id)
        if not parent_str or not dlc_str:
            return False
        return self.add_dlc_batch(parent_str, {dlc_str: dlc_name})

    def add_dlc_batch(
        self,
        parent_app_id: Union[str, int],
        dlc_dict: Dict[str, str],
    ) -> bool:
        parent_str = _sanitize_id(parent_app_id)
        if not parent_str or not dlc_dict:
            return False
        new_content, changed = _add_dlc_batch_in_memory(
            self.content, parent_str, dlc_dict
        )
        if changed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_dlc(
        self, parent_app_id: Union[str, int], dlc_id: Union[str, int]
    ) -> bool:
        parent_str = _sanitize_id(parent_app_id)
        dlc_str = _sanitize_id(dlc_id)
        if not parent_str or not dlc_str:
            return False
        bounds = _get_section_bounds(self.content, "DlcData")
        if not bounds:
            return False
        _, dlc_start, dlc_end = bounds
        dlc_section = self.content[dlc_start:dlc_end]
        parent_pattern = re.compile(rf"^[ \t]+{re.escape(parent_str)}:[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
        parent_match = parent_pattern.search(dlc_section)
        if not parent_match:
            return False

        p_start = dlc_start + parent_match.end()
        if p_start < len(self.content) and self.content[p_start] == "\r":
            p_start += 1
        if p_start < len(self.content) and self.content[p_start] == "\n":
            p_start += 1

        p_after = self.content[p_start:dlc_end]
        next_parent = re.search(r"^[ \t]+[0-9A-Za-z_]+:[ \t]*(?:#[^\r\n]*)?$", p_after, re.MULTILINE)
        parent_end = (p_start + next_parent.start()) if next_parent else dlc_end

        dlc_item_pattern = re.compile(
            rf'^[ \t]*{re.escape(dlc_str)}[ \t]*:[ \t]*"[^"\r\n]*"(?:#[^\r\n]*)?$',
            re.MULTILINE,
        )
        parent_block = self.content[p_start:parent_end]
        match = dlc_item_pattern.search(parent_block)
        if not match:
            return False

        abs_match_start = p_start + match.start()
        line_start = self.content.rfind("\n", 0, abs_match_start)
        line_start = 0 if line_start == -1 else line_start + 1
        line_end = self.content.find("\n", abs_match_start)
        line_end = len(self.content) if line_end == -1 else line_end + 1

        self.content = self.content[:line_start] + self.content[line_end:]
        self.has_changes = True
        return True

    def add_fake_app_id(
        self,
        app_id: Union[str, int],
        target_id: Union[str, int],
        comment: str = "",
    ) -> bool:
        app_id_str = _sanitize_id(app_id)
        target_id_str = _sanitize_id(target_id)
        if not app_id_str or not target_id_str:
            return False
        comment_clean = _sanitize_comment(comment)
        new_content, changed = _add_map_item_in_memory(
            self.content, "FakeAppIds", app_id_str, target_id_str, comment_clean
        )
        if changed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_fake_app_id(self, app_id: Union[str, int]) -> bool:
        app_id_str = _sanitize_id(app_id)
        if not app_id_str:
            return False
        pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(app_id_str)}['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        new_content, removed = _remove_entry_in_memory(
            self.content, "FakeAppIds", pattern
        )
        if removed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def add_launch_option(self, app_id: Union[str, int], option: str) -> bool:
        app_id_str = _sanitize_id(app_id)
        if not app_id_str:
            return False
        option_clean = _sanitize_comment(option)
        new_content, changed = _add_map_item_in_memory(
            self.content, "LaunchOptions", app_id_str, option_clean
        )
        if changed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_launch_option(self, app_id: Union[str, int]) -> bool:
        app_id_str = _sanitize_id(app_id)
        if not app_id_str:
            return False
        pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(app_id_str)}['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        new_content, removed = _remove_entry_in_memory(
            self.content, "LaunchOptions", pattern
        )
        if removed:
            self.content = new_content
            self.has_changes = True
            return True
        return False


def batch_config_edit(config_path: Optional[Path] = None) -> BatchConfigEditor:
    """Return a BatchConfigEditor context manager to batch multiple additions, updates,
    and removals into a single atomic write."""
    if config_path is None:
        config_path = get_user_config_path()
    return BatchConfigEditor(config_path)


def add_additional_apps_batch(
    config_path: Path, apps: List[Tuple[Union[str, int], str]]
) -> bool:
    """Add multiple AppIDs to AdditionalApps in SLSsteam config.yaml in a single atomic write."""
    if not apps:
        return True
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        any_added = False
        for app_id, comment in apps:
            if editor.add_app(app_id, comment):
                any_added = True
    return any_added


def add_additional_depots_batch(
    config_path: Path, depots: List[Tuple[Union[str, int], str]]
) -> bool:
    """Add multiple DepotIDs to AdditionalDepots in SLSsteam config.yaml in a single atomic write."""
    if not depots:
        return True
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        any_added = False
        for depot_id, comment in depots:
            if editor.add_depot(depot_id, comment):
                any_added = True
    return any_added


def add_decryption_keys_batch(
    config_path: Path,
    keys: Union[Dict[Union[str, int], Union[Tuple[str, str], str]], List[Union[Tuple[Union[str, int], str, str], Tuple[Union[str, int], str]]]],
) -> bool:
    """Add or update multiple depot AES decryption keys in DecryptionKeys in a single atomic write."""
    if not keys:
        return True
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        any_added = False
        if isinstance(keys, dict):
            for depot_id, val in keys.items():
                if isinstance(val, (tuple, list)) and len(val) >= 2:
                    k, comm = val[0], val[1]
                else:
                    k, comm = str(val), ""
                if editor.add_key(depot_id, k, comment=comm):
                    any_added = True
        elif isinstance(keys, (list, tuple)):
            for item in keys:
                if len(item) == 3:
                    did, k, comm = item
                elif len(item) == 2:
                    did, k = item
                    comm = ""
                else:
                    continue
                if editor.add_key(did, k, comment=comm):
                    any_added = True
    return any_added


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
    """Create new config file with Plugins: yes and a single AdditionalApps entry."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if comment:
        new_entry = f"Plugins: yes\nAdditionalApps:\n  - {app_id} # {comment}\n"
    else:
        new_entry = f"Plugins: yes\nAdditionalApps:\n  - {app_id}\n"

    if _atomic_write(config_path, new_entry):
        logger.info(f"Created config file with Plugins: yes and AppID '{app_id}' in {config_path}")
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


def add_additional_app(config_path: Path, app_id: Union[str, int], comment: str = "") -> bool:
    """Add an AppID to the AdditionalApps list in SLSsteam config.yaml."""
    ensure_plugins_enabled(config_path)
    if not config_path.exists():
        app_id_clean = _sanitize_id(app_id)
        if not app_id_clean:
            return False
        return _init_config_with_app(config_path, app_id_clean, _sanitize_comment(comment))
    with batch_config_edit(config_path) as editor:
        res = editor.add_app(app_id, comment)
    return res and editor.committed_successfully


def remove_additional_app(config_path: Path, app_id: Union[str, int]) -> bool:
    """Remove an AppID from the AdditionalApps list."""
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        res = editor.remove_app(app_id)
    return res and editor.committed_successfully


def replace_additional_app(config_path: Path, old_app_id: Union[str, int], new_app_id: Union[str, int], new_comment: str = "") -> bool:
    """Replace an existing AppID in AdditionalApps with a new AppID and optional comment.
    Also migrates any DlcData or FakeAppIds entries if present.
    """
    content = _read_config_content(config_path)
    if not content:
        return False

    old_aid_str = _sanitize_id(old_app_id)
    new_aid_str = _sanitize_id(new_app_id)
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

    new_comment = _sanitize_comment(new_comment)
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

    # Migrate FakeAppIds section key if present
    fake_bounds = _get_section_bounds(updated_content, "FakeAppIds")
    if fake_bounds:
        _, f_start, f_end = fake_bounds
        f_sec = updated_content[f_start:f_end]
        f_pat = re.compile(rf"^([ \t]*['\"]?){re.escape(old_aid_str)}(['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[^\r\n]*)?)$", re.MULTILINE)
        if f_pat.search(f_sec):
            new_f_sec = f_pat.sub(rf"\g<1>{new_aid_str}\g<2>", f_sec, count=1)
            updated_content = updated_content[:f_start] + new_f_sec + updated_content[f_end:]

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


def is_depot_shared_with_other_games(
    depot_id: Union[str, int], excluding_appid: Optional[Union[str, int]] = None
) -> bool:
    """Check if a depot ID is shared with other installed/registered games or is a common redistributable.

    Returns True if:
      1. It is a known Steam common redistributable depot (Steamworks Shared, DirectX, VC++, etc.).
      2. It is referenced by any other registered game in plugin_library.json.
      3. It is listed under InstalledDepots in any other game's appmanifest_*.acf on disk.
      4. It is tagged for another AppID or marked as Steamworks Shared in SLSsteam config.yaml comments.
    """
    depot_id_str = str(depot_id).strip()
    if not depot_id_str:
        return False

    # 1. Known redistributables / shared runtime depots
    try:
        from ui.assets import DEPOT_BLACKLIST
        shared_known = {str(d) for d in DEPOT_BLACKLIST} | SHARED_REDISTS
    except Exception:
        shared_known = SHARED_REDISTS

    if depot_id_str in shared_known:
        logger.debug(f"[SharedDepotCheck] Depot {depot_id_str} is a known common redistributable.")
        return True

    ex_aid_str = str(excluding_appid).strip() if excluding_appid is not None else None

    # 2. Check plugin_library.json
    try:
        from utils.plugin_games import load_plugin_library
        lib = load_plugin_library()
        for aid, g in lib.items():
            if ex_aid_str and str(aid) == ex_aid_str:
                continue
            for did in g.get("depots", []):
                if str(did) == depot_id_str:
                    logger.debug(
                        f"[SharedDepotCheck] Depot {depot_id_str} is used by registered game "
                        f"'{g.get('name')}' ({aid})"
                    )
                    return True
            for kid in g.get("keys", {}).keys():
                if str(kid) == depot_id_str:
                    logger.debug(
                        f"[SharedDepotCheck] Depot {depot_id_str} key is used by registered game "
                        f"'{g.get('name')}' ({aid})"
                    )
                    return True
    except Exception as e:
        logger.debug(f"[SharedDepotCheck] Error checking plugin library: {e}")

    # 3. Check Steam library ACF manifests
    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        steamapps_dirs = getattr(env, "steamapps_paths", [])
        for sdir in steamapps_dirs:
            sdir_path = Path(sdir)
            if not sdir_path.is_dir():
                continue
            for acf in sdir_path.glob("appmanifest_*.acf"):
                if ex_aid_str and acf.name == f"appmanifest_{ex_aid_str}.acf":
                    continue
                try:
                    txt = acf.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r'"InstalledDepots"\s*\{([^}]*)\}', txt, re.DOTALL)
                    if m and f'"{depot_id_str}"' in m.group(1):
                        logger.debug(
                            f"[SharedDepotCheck] Depot {depot_id_str} found in InstalledDepots of {acf.name}"
                        )
                        return True
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"[SharedDepotCheck] Error checking Steam ACF manifests: {e}")

    # 4. Check SLSsteam config.yaml comments strictly for explicit Steamworks Shared marker
    try:
        cfg_path = get_user_config_path()
        if cfg_path and cfg_path.exists():
            cfg_text = cfg_path.read_text(encoding="utf-8", errors="ignore")
            # Check AdditionalDepots section
            depot_bounds = _get_section_bounds(cfg_text, "AdditionalDepots")
            if depot_bounds:
                sec_depots = cfg_text[depot_bounds[1]:depot_bounds[2]]
                m = re.search(
                    rf"^[ \t]*-[ \t]*['\"]?{re.escape(depot_id_str)}['\"]?[ \t]*(?:#[ \t]*(.*))?$",
                    sec_depots,
                    re.MULTILINE,
                )
                if m:
                    comment = (m.group(1) or "").strip()
                    if "steamworks shared" in comment.lower():
                        return True

            # Check DecryptionKeys section
            key_bounds = _get_section_bounds(cfg_text, "DecryptionKeys")
            if key_bounds:
                sec_keys = cfg_text[key_bounds[1]:key_bounds[2]]
                m = re.search(
                    rf"^[ \t]*['\"]?{re.escape(depot_id_str)}['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[ \t]*(.*))?$",
                    sec_keys,
                    re.MULTILINE,
                )
                if m:
                    comment = (m.group(1) or "").strip()
                    if "steamworks shared" in comment.lower():
                        return True
    except Exception as e:
        logger.debug(f"[SharedDepotCheck] Error checking config.yaml comments: {e}")

    return False


def has_game_config_entries(
    appid: Union[str, int], game_data: Optional[Dict[str, Any]] = None
) -> bool:
    """Check if an ASSella-mode game has active entries in SLSsteam config.yaml.
    Strictly ignores universal shared redistributables.

    Returns True if the game qualifies by having:
      - Its AppID in AdditionalApps
      - Any DLC AppID in AdditionalApps or dlc_data
      - Any non-shared depot in AdditionalDepots
      - Any non-shared decryption key in DecryptionKeys (including root AppKey)
      - Explicitly tagged comments referencing the AppID or game name in config.yaml
      - Or registration in plugin_library.json
    """
    appid_str = str(appid).strip()
    if not appid_str or not appid_str.isdigit():
        return False

    cfg_path = get_user_config_path()
    if not cfg_path or not cfg_path.exists():
        return False

    try:
        from ui.assets import DEPOT_BLACKLIST
        all_shared = {str(d) for d in DEPOT_BLACKLIST} | SHARED_REDISTS
    except Exception:
        all_shared = SHARED_REDISTS

    # 1. Plugin library registration
    try:
        from utils.plugin_games import load_plugin_library
        lib = load_plugin_library()
        if appid_str in lib:
            return True
    except Exception:
        pass

    # 2. Check dlc_data batch block in config.yaml
    try:
        dlc_data = get_dlc_data(cfg_path)
        if appid_str in dlc_data:
            return True
    except Exception:
        pass

    # 3. Check AdditionalApps
    try:
        live_apps = get_additional_apps(cfg_path)
        if appid_str in live_apps:
            return True
        gd = game_data or {}
        gd_dlcs = gd.get("dlcs") or {}
        for dlc_id in gd_dlcs.keys():
            if str(dlc_id).strip() in live_apps:
                return True
    except Exception:
        pass

    # Gather known depots for this game
    game_depots: Set[str] = set()
    gd = game_data or {}
    if gd.get("depots"):
        game_depots.update(str(d).strip() for d in gd["depots"].keys())

    try:
        from managers.depot_key_manager import DepotKeyManager
        dkm = DepotKeyManager.get_instance()
        local_keys = dkm.get_keys_for_app(appid_str)
        if local_keys:
            game_depots.update(str(d).strip() for d in local_keys.keys())
    except Exception:
        pass

    non_shared_game_depots = (game_depots - all_shared) - {appid_str}

    # 4. Check AdditionalDepots (strictly non-shared)
    try:
        live_depots = get_additional_depots(cfg_path)
        if any(d in live_depots for d in non_shared_game_depots):
            return True
    except Exception:
        pass

    # 5. Check DecryptionKeys (strictly non-shared)
    try:
        live_keys = get_decryption_keys(cfg_path)
        if appid_str in live_keys:
            return True
        if any(d in live_keys for d in non_shared_game_depots):
            return True
    except Exception:
        pass

    # 6. Check tagged comments specifically mentioning appid_str or game_name
    try:
        txt = cfg_path.read_text(encoding="utf-8", errors="ignore")
        game_name = (
            (game_data.get("game_name") or game_data.get("name") or "").strip().lower()
            if game_data
            else ""
        )

        for sec_name in ("AdditionalApps", "AdditionalDepots", "DecryptionKeys"):
            bounds = _get_section_bounds(txt, sec_name)
            if not bounds:
                continue
            sec_text = txt[bounds[1]:bounds[2]]
            for line in sec_text.splitlines():
                m = re.match(r"^[ \t]*-[ \t]*['\"]?(\d+)['\"]?[ \t]*(?:#[ \t]*(.*))?$", line)
                if not m:
                    m = re.match(r"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[ \t]*(.*))?$", line)
                if m:
                    item_id, comment = m.group(1), (m.group(2) or "").lower()
                    if sec_name in ("AdditionalDepots", "DecryptionKeys") and item_id in all_shared:
                        continue
                    if appid_str in comment or (game_name and len(game_name) > 3 and game_name in comment):
                        return True
    except Exception:
        pass

    return False



def add_additional_depot(
    config_path: Path,
    depot_id: Union[str, int],
    comment: str = "",
    app_id: Optional[Union[str, int]] = None,
) -> bool:
    """Add a DepotID to the AdditionalDepots list in SLSsteam config.yaml."""
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        res = editor.add_depot(depot_id, comment, app_id=app_id)
    return res and editor.committed_successfully


def remove_additional_depot(
    config_path: Path,
    depot_id: Union[str, int],
    check_shared: bool = True,
    excluding_appid: Optional[Union[str, int]] = None,
) -> bool:
    """Remove a DepotID from the AdditionalDepots list in SLSsteam config.yaml.
    If check_shared is True, skips removal if the depot is shared with another game or is a common redistributable.
    """
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        res = editor.remove_depot(depot_id, check_shared=check_shared, excluding_appid=excluding_appid)
    return res and editor.committed_successfully


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


def add_decryption_key(config_path: Path, depot_id: Union[str, int], key: str, comment: str = "") -> bool:
    """Add or update a depot AES decryption key in DecryptionKeys section in SLSsteam config.yaml."""
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        res = editor.add_key(depot_id, key, comment)
    return res and editor.committed_successfully


def remove_decryption_key(
    config_path: Path,
    depot_id: Union[str, int],
    check_shared: bool = True,
    excluding_appid: Optional[Union[str, int]] = None,
) -> bool:
    """Remove a depot key from DecryptionKeys section in SLSsteam config.yaml.
    If check_shared is True, skips removal if the depot is shared with another game or is a common redistributable.
    """
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        res = editor.remove_key(depot_id, check_shared=check_shared, excluding_appid=excluding_appid)
    return res and editor.committed_successfully


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



