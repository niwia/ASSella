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

        backup_path = config_path.with_suffix(BACKUP_SUFFIX)

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
    """Atomically write content to config file using a temporary file."""
    temp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    try:
        # Write to temp file in same directory
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
        # Atomic replace
        os.replace(temp_path, config_path)
        return True
    except OSError as e:
        logger.error(f"Failed to atomically write {config_path}: {e}", exc_info=True)
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
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


def update_yaml_boolean_value(config_path: Path, key: str, value: bool) -> bool:
    """Update a boolean value in YAML config using regex pattern matching."""
    try:
        if not config_path.exists():
            logger.warning(f"Config file not found at {config_path}")
            return False

        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Regex pattern to match the key with its current value
        pattern = re.compile(
            r"^(\s*)"
            + re.escape(key)
            + r"\s*:\s*(yes|no|true|false|Yes|No|True|False)\b",
            re.MULTILINE,
        )

        match = pattern.search(content)
        if not match:
            logger.warning(f"Key '{key}' not found in config file {config_path}")
            return False

        indent = match.group(1)
        old_value = match.group(2)

        # Always use yes/no format for SLSsteam compatibility
        new_value = "yes" if value else "no"

        # Check if already set correctly
        if old_value.lower() == new_value.lower():
            logger.debug(f"Key '{key}' is already set to {new_value}")
            return False

        # Create replacement string preserving indentation
        replacement = f"{indent}{key}: {new_value}"

        # Replace only the matched line
        new_content = pattern.sub(replacement, content)

        if not _atomic_write(config_path, new_content):
            return False

        logger.info(f"Updated '{key}' to {new_value} in {config_path}")
        return True

    except OSError as e:
        logger.error(f"Failed to update '{key}' in {config_path}: {e}", exc_info=True)
        return False


def get_user_config_path() -> Path:
    """Get the path to the user's SLSsteam config.yaml file."""
    xdg_config_home_str = os.environ.get("XDG_CONFIG_HOME", "")
    xdg_config_home = (
        Path(xdg_config_home_str).expanduser() if xdg_config_home_str else Path()
    )

    if xdg_config_home_str and xdg_config_home.is_absolute():
        config_dir = xdg_config_home / "SLSsteam"
    else:
        config_dir = Path.home() / ".config" / "SLSsteam"

    return config_dir / "config.yaml"


def _get_section_start(content: str, pattern: re.Pattern) -> Optional[int]:
    match = pattern.search(content)
    if not match:
        return None
    section_start = match.end()
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


def _remove_line_for_match(content: str, match: re.Match) -> str:
    line_start = content.rfind("\n", 0, match.start()) + 1
    if line_start == 0:
        line_start = 0
    line_end = content.find("\n", match.end())
    if line_end == -1:
        line_end = len(content)

    if line_end < len(content) and content[line_end] == "\n":
        line_end += 1

    return content[:line_start] + content[line_end:]


def _remove_matching_entry(
    config_path: Path, pattern: re.Pattern, success_message: str, error_message: str
) -> bool:
    try:
        content = _get_config_content_if_enabled(config_path)
        if content is _CONFIG_DISABLED or content is None:
            return False

        match = pattern.search(content)
        if not match:
            return False

        new_content = _remove_line_for_match(content, match)
        if not _atomic_write(config_path, new_content):
            return False

        logger.info(success_message)
        return True
    except OSError as e:
        logger.error(error_message.format(e=e), exc_info=True)
        return False


def _fix_additional_apps_indentation(content: str) -> Tuple[str, bool]:
    """Fix indentation of AdditionalApps list items."""
    # Find AdditionalApps section
    additional_apps_pattern = re.compile(r"^AdditionalApps:\s*$", re.MULTILINE)
    section_start = _get_section_start(content, additional_apps_pattern)
    if section_start is None:
        return content, False

    # Look for next top-level key
    next_key_pattern = re.compile(r"^[A-Za-z]", re.MULTILINE)
    section_end = _get_section_end(content, section_start, next_key_pattern)

    section_content = content[section_start:section_end]

    # Pattern to find misaligned items: "- item" or "-item"
    misaligned_item_pattern = re.compile(
        r"(^)(\s*)-(\s*)([^\n#]+?)(?=\s*(?:#|$))", re.MULTILINE
    )

    # Fix items by adding 2-space indentation
    fixed_section = misaligned_item_pattern.sub(r"\1  - \4", section_content)

    if fixed_section != section_content:
        fixed_content = content[:section_start] + fixed_section + content[section_end:]
        logger.debug("Fixed indentation of AdditionalApps list items")
        return fixed_content, True

    return content, False


def _get_app_tokens_section(content: str) -> str:
    """Extract the AppTokens section from YAML content."""
    app_tokens_pattern = re.compile(r"^AppTokens:\s*$", re.MULTILINE)
    section_start = _get_section_start(content, app_tokens_pattern)
    if section_start is None:
        return ""

    next_key_pattern = re.compile(r"^[A-Za-z][A-Za-z0-9]*:\s*$", re.MULTILINE)
    section_end = _get_section_end(content, section_start, next_key_pattern)
    return content[section_start:section_end]


def _fix_app_tokens_indentation(content: str) -> Tuple[str, bool]:
    """Fix indentation of AppTokens entries to have 2-space indentation."""
    app_tokens_pattern = re.compile(r"^AppTokens:\s*$", re.MULTILINE)
    section_start = _get_section_start(content, app_tokens_pattern)
    if section_start is None:
        return content, False
    after_section = content[section_start:]

    next_key_pattern = re.compile(r"^[A-Za-z][A-Za-z0-9]*:\s*$", re.MULTILINE)
    next_match = next_key_pattern.search(after_section)

    # Find the last token entry to determine end of section
    last_token_pattern = re.compile(r"^\s*\d+\s*:\s*[^\n]*$", re.MULTILINE)
    last_token_matches = list(last_token_pattern.finditer(after_section))

    if last_token_matches:
        last_token_end = last_token_matches[-1].end()
        newline_after_token = after_section.find("\n", last_token_end)
        if newline_after_token != -1:
            section_end = section_start + newline_after_token + 1
        elif next_match:
            section_end = section_start + next_match.start()
        else:
            section_end = len(content)
    elif next_match:
        section_end = section_start + next_match.start()
    else:
        section_end = len(content)

    section_content = content[section_start:section_end]
    token_pattern = re.compile(r"(^)(\s*)(\d+)(\s*:\s*[^\n]*)", re.MULTILINE)
    fixed_section = token_pattern.sub(r"\1  \3\4", section_content)

    if fixed_section != section_content:
        fixed_content = content[:section_start] + fixed_section + content[section_end:]
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
        new_entry = f"AdditionalApps:\n  - {app_id}   # {comment}\n"
    else:
        new_entry = f"AdditionalApps:\n  - {app_id}\n"

    if _atomic_write(config_path, new_entry):
        logger.info(f"Created config file with AppID '{app_id}' in {config_path}")
        return True
    return False


def _append_to_additional_apps(
    content: str, app_id: str, comment: str, match: re.Match
) -> str:
    """Append AppID to existing AdditionalApps section."""
    start_pos = match.end()
    remaining = content[start_pos:]
    lines = remaining.split("\n")

    # Find position after the last list item
    last_item_end = start_pos
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("-"):
            last_item_end = start_pos + sum(len(lines[j]) + 1 for j in range(i + 1))
        elif not stripped or stripped.startswith("#"):
            continue
        else:
            break
    else:
        last_item_end = len(content)

    insert_pos = last_item_end
    if comment:
        new_entry = f"  - {app_id}   # {comment}\n"
    else:
        new_entry = f"  - {app_id}\n"

    return content[:insert_pos] + new_entry + content[insert_pos:]


def add_additional_app(config_path: Path, app_id: str, comment: str = "") -> bool:
    """Add an AppID to the AdditionalApps list in SLSsteam config.yaml."""
    try:
        content = _get_config_content_if_enabled(config_path)
        if content is _CONFIG_DISABLED:
            return False
        if content is None:
            return _init_config_with_app(config_path, app_id, comment)

        fixed_content, _ = _fix_additional_apps_indentation(content)

        # Check if AppID already exists
        app_id_pattern = re.compile(
            rf"^\s*-\s*{re.escape(app_id)}\s*(?:#.*)?$", re.MULTILINE
        )
        if app_id_pattern.search(fixed_content):
            logger.debug(f"AppID '{app_id}' already exists in AdditionalApps")
            return False

        additional_apps_pattern = re.compile(r"^AdditionalApps:\s*$", re.MULTILINE)
        match = additional_apps_pattern.search(fixed_content)

        if match:
            new_content = _append_to_additional_apps(
                fixed_content, app_id, comment, match
            )
        else:
            # Create new AdditionalApps section
            if comment:
                entry = f"AdditionalApps:\n  - {app_id}   # {comment}\n"
            else:
                entry = f"AdditionalApps:\n  - {app_id}\n"
            new_content = fixed_content + "\n" + entry

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
        rf"^\s*-\s*{re.escape(app_id)}\s*(?:#.*)?$", re.MULTILINE
    )
    return _remove_matching_entry(
        config_path,
        app_id_pattern,
        f"Removed AppID '{app_id}' from AdditionalApps in {config_path}",
        f"Failed to remove AppID '{app_id}': {{e}}",
    )


def add_dlc_data(
    config_path: Path, parent_app_id: str, dlc_id: str, dlc_name: str
) -> bool:
    """Add a DLC entry to DlcData section in SLSsteam config.yaml."""
    try:
        content = _get_config_content_if_enabled(config_path, log_missing=True)
        if content is _CONFIG_DISABLED or content is None:
            return False

        dlc_data_pattern = re.compile(r"^DlcData:\s*$", re.MULTILINE)
        match = dlc_data_pattern.search(content)

        if not match:
            # Create new DlcData section
            new_entry = f'DlcData:\n  {parent_app_id}:\n    {dlc_id}: "{dlc_name}"\n'
            new_content = content + "\n" + new_entry
            return _atomic_write_and_log(
                config_path, new_content, dlc_name, dlc_id, parent_app_id
            )

        dlc_data_end = match.end()

        # Find parent AppID under DlcData
        parent_pattern = re.compile(
            rf"^(\s*){re.escape(parent_app_id)}:\s*$", re.MULTILINE
        )
        parent_match = parent_pattern.search(content, dlc_data_end)

        if not parent_match:
            # Add new parent AppID section under DlcData
            remaining = content[dlc_data_end:]
            next_key_pattern = re.compile(r"^[A-Za-z]", re.MULTILINE)
            next_match = next_key_pattern.search(remaining)
            insert_pos = (
                dlc_data_end + next_match.start() if next_match else len(content)
            )

            new_entry = f'  {parent_app_id}:\n    {dlc_id}: "{dlc_name}"\n'
            new_content = content[:insert_pos] + new_entry + content[insert_pos:]
            return _atomic_write_and_log(
                config_path, new_content, dlc_name, dlc_id, parent_app_id
            )

        # Parent exists, check for DLC
        parent_line_end = parent_match.end()
        parent_indent = len(parent_match.group(1))
        remaining = content[parent_line_end:]

        # Find scope of this parent block
        next_parent_pattern = re.compile(
            rf"^(\s{{{parent_indent}}})[0-9]", re.MULTILINE
        )
        next_match = next_parent_pattern.search(remaining)

        if next_match:
            parent_section = remaining[: next_match.start()]
            insert_pos = parent_line_end + next_match.start()
        else:
            # Until end of DlcData or EOF
            after_dlcdata = content[dlc_data_end:]
            end_match = re.compile(r"^[A-Za-z]", re.MULTILINE).search(after_dlcdata)
            if end_match:
                limit = dlc_data_end + end_match.start() - parent_line_end
                parent_section = remaining[:limit]
                insert_pos = dlc_data_end + end_match.start()
            else:
                parent_section = remaining
                insert_pos = len(content)

        dlc_check_pattern = re.compile(rf'^\s*{re.escape(dlc_id)}:\s*"', re.MULTILINE)
        if dlc_check_pattern.search(parent_section):
            logger.debug(f"DLC '{dlc_id}' already exists under AppID '{parent_app_id}'")
            return False

        # Build new DLC entry with proper indentation
        new_entry = f'{" " * (parent_indent + 2)}{dlc_id}: "{dlc_name}"\n'
        new_content = content[:insert_pos] + new_entry + content[insert_pos:]

        return _atomic_write_and_log(
            config_path, new_content, dlc_name, dlc_id, parent_app_id
        )

    except OSError as e:
        logger.error(f"Failed to add DLC '{dlc_id}': {e}", exc_info=True)
        return False


def _atomic_write_and_log(
    config_path: Path,
    content: str,
    dlc_name: str,
    dlc_id: str,
    parent_app_id: str,
) -> bool:
    """Helper to write file and log success for DLC addition."""
    if not _atomic_write(config_path, content):
        return False
    logger.info(
        f"Added DLC '{dlc_name}' ({dlc_id}) to DlcData under "
        f"AppID '{parent_app_id}' in {config_path}"
    )
    return True


def add_app_token(config_path: Path, app_id: str, token: str) -> bool:
    """Add an AppToken to the AppTokens section in SLSsteam config.yaml."""
    try:
        content = _get_config_content_if_enabled(config_path)
        if content is _CONFIG_DISABLED or content is None:
            return False

        app_tokens_pattern = re.compile(r"^AppTokens:\s*$", re.MULTILINE)
        if not app_tokens_pattern.search(content):
            new_entry = f"AppTokens:\n  {app_id}: {token}\n"
            return _atomic_write(config_path, content + new_entry)

        # Fix indentation FIRST
        fixed_content, _ = _fix_app_tokens_indentation(content)
        content = fixed_content

        # Search for any existing entries for this app_id (with or without quotes)
        dup_pattern = re.compile(
            rf"^ {{2}}['\"]?{re.escape(app_id)}['\"]?\s*:\s*.*(?:\r?\n)?", re.MULTILINE
        )
        
        matches = list(dup_pattern.finditer(content))
        
        if len(matches) == 1:
            match_val_pat = re.compile(
                rf"^ {{2}}['\"]?{re.escape(app_id)}['\"]?\s*:\s*(.+)$", re.MULTILINE
            )
            m = match_val_pat.search(matches[0].group(0))
            if m and m.group(1).strip() == token:
                # Token matches exactly. No update needed.
                return False

        # Remove all existing occurrences of this app_id
        new_content = content
        for m in reversed(matches):
            new_content = new_content[:m.start()] + new_content[m.end():]

        # Insert the single correct entry under AppTokens
        tokens_start = new_content.find("AppTokens:")
        if tokens_start != -1:
            insert_pos = tokens_start + len("AppTokens:")
            if insert_pos < len(new_content) and new_content[insert_pos] == "\n":
                insert_pos += 1
            elif insert_pos < len(new_content) and new_content[insert_pos] == "\r":
                insert_pos += 2
                
            new_token_line = f"  {app_id}: {token}\n"
            new_content = new_content[:insert_pos] + new_token_line + new_content[insert_pos:]
            
            if _atomic_write(config_path, new_content):
                if len(matches) > 1:
                    logger.info(f"Updated AppToken for '{app_id}' and removed duplicates")
                elif len(matches) == 1:
                    logger.info(f"Updated AppToken for '{app_id}'")
                else:
                    logger.info(f"Added AppToken for '{app_id}'")
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

        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        section_content = _get_app_tokens_section(content)
        token_pattern = re.compile(r"^\s*(\d+)\s*:\s*(.+)$", re.MULTILINE)

        for token_match in token_pattern.finditer(section_content):
            app_id = token_match.group(1).strip()
            token = token_match.group(2).strip()
            tokens[app_id] = token

    except OSError as e:
        logger.error(f"Failed to read AppTokens from {config_path}: {e}", exc_info=True)

    return tokens


def get_fake_app_ids(config_path: Path, fake_appid: str = "") -> Set[str]:
    """Get all FakeAppIds from SLSsteam config.yaml."""
    fake_app_ids = set()

    if not fake_appid:
        fake_appid = get_fake_appid_for_online()

    try:
        if not config_path.exists():
            return fake_app_ids

        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        fake_appids_pattern = re.compile(r"^FakeAppIds:\s*$", re.MULTILINE)
        match = fake_appids_pattern.search(content)

        if not match:
            return fake_app_ids

        section_start = match.end()
        after_section = content[section_start:]
        next_key_pattern = re.compile(r"^[A-Za-z]", re.MULTILINE)
        next_match = next_key_pattern.search(after_section)

        if next_match:
            section_end = section_start + next_match.start()
        else:
            section_end = len(content)

        section_content = content[section_start:section_end]
        entry_pattern = re.compile(
            rf"^\s*(\d+)\s*:\s*{re.escape(fake_appid)}", re.MULTILINE
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
        if not config_path.exists():
            return None

        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        fake_appids_pattern = re.compile(r"^FakeAppIds:\s*$", re.MULTILINE)
        match = fake_appids_pattern.search(content)

        if not match:
            return None

        section_start = match.end()
        after_section = content[section_start:]
        next_key_pattern = re.compile(r"^[A-Za-z]", re.MULTILINE)
        next_match = next_key_pattern.search(after_section)

        if next_match:
            section_end = section_start + next_match.start()
        else:
            section_end = len(content)

        section_content = content[section_start:section_end]
        entry_pattern = re.compile(
            rf"^\s*{re.escape(app_id)}\s*:\s*(\d+)", re.MULTILINE
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

        existing_pattern = re.compile(
            rf"^\s*{re.escape(app_id)}\s*:\s*{re.escape(fake_appid)}",
            re.MULTILINE,
        )

        if existing_pattern.search(content):
            return False

        fake_appids_pattern = re.compile(r"^FakeAppIds:\s*$", re.MULTILINE)
        match = fake_appids_pattern.search(content)

        if match:
            # Append to existing section
            section_start = match.end()
            if section_start < len(content) and content[section_start] == "\n":
                section_start += 1
            remaining = content[section_start:]
            lines = remaining.split("\n")

            last_entry_end = section_start
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped and stripped[0].isdigit():
                    last_entry_end = section_start + sum(
                        len(lines[j]) + 1 for j in range(i + 1)
                    )
                elif not stripped or stripped.startswith("#"):
                    continue
                else:
                    break
            else:
                last_entry_end = len(content)

            insert_pos = last_entry_end
            entry = f"  {app_id}: {fake_appid}"
            if game_name:
                entry += f"  # {game_name} -> {suffix}\n"
            else:
                entry += "\n"

            new_content = content[:insert_pos] + entry + content[insert_pos:]
        else:
            entry = f"FakeAppIds:\n  {app_id}: {fake_appid}"
            if game_name:
                entry += f"  # {game_name} -> {suffix}\n"
            else:
                entry += "\n"
            new_content = content + "\n" + entry

        if not _atomic_write(config_path, new_content):
            return False

        logger.info(f"Added AppID '{app_id}' to FakeAppIds in {config_path}")
        return True

    except OSError as e:
        logger.error(f"Failed to add FakeAppId '{app_id}': {e}", exc_info=True)
        return False


def remove_fake_app_id(config_path: Path, app_id: str, fake_appid: str = "") -> bool:
    """Remove an AppID from the FakeAppIds list in SLSsteam config.yaml."""
    if not fake_appid:
        fake_appid = get_fake_appid_for_online()

    app_id_pattern = re.compile(
        rf"^\s*{re.escape(app_id)}\s*:\s*{re.escape(fake_appid)}" r"(?:\s*#.*)?$",
        re.MULTILINE,
    )
    return _remove_matching_entry(
        config_path,
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

    # Find FakeAppIds section
    fake_appids_pattern = re.compile(r"^FakeAppIds:\s*$", re.MULTILINE)
    match = fake_appids_pattern.search(content)

    # Let's collect existing FakeAppIds
    existing_fake_apps = {}
    if match:
        section_start = match.end()
        after_section = content[section_start:]
        next_key_pattern = re.compile(r"^[A-Za-z]", re.MULTILINE)
        next_match = next_key_pattern.search(after_section)
        section_end = section_start + next_match.start() if next_match else len(content)
        section_content = content[section_start:section_end]
        
        entry_pattern = re.compile(r"^\s*(\d+)\s*:\s*(\d+)", re.MULTILINE)
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
        comment_suffix = f"  # {comment}" if comment else ""
        insert_text += f"  {appid}: {fake_appid}{comment_suffix}\n"

    new_content = ""
    if match:
        # Find where to insert. We insert right after "FakeAppIds:\n"
        insert_pos = match.end()
        if insert_pos < len(content) and content[insert_pos] == "\n":
            insert_pos += 1
        new_content = content[:insert_pos] + insert_text + content[insert_pos:]
    else:
        # Section doesn't exist, append it
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
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        logger.error(f"Failed to read config file {config_path}: {e}")
        return False

    # Find FakeAppIds section
    fake_appids_pattern = re.compile(r"^FakeAppIds:\s*$", re.MULTILINE)
    match = fake_appids_pattern.search(content)
    if not match:
        return False

    section_start = match.end()
    after_section = content[section_start:]
    next_key_pattern = re.compile(r"^[A-Za-z]", re.MULTILINE)
    next_match = next_key_pattern.search(after_section)
    section_end = section_start + next_match.start() if next_match else len(content)
    section_content = content[section_start:section_end]

    # Rebuild section content, omitting any lines that match db_appids
    new_section_lines = []
    removed_count = 0
    entry_pattern = re.compile(r"^\s*(\d+)\s*:")
    for line in section_content.split("\n"):
        m = entry_pattern.match(line)
        if m:
            appid = m.group(1).strip()
            if appid in db_appids:
                removed_count += 1
                continue  # skip/remove this line
        new_section_lines.append(line)

    if removed_count == 0:
        return False

    new_section_content = "\n".join(new_section_lines)
    new_content = content[:section_start] + new_section_content + content[section_end:]

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
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        logger.error(f"Failed to read config file {config_path}: {e}")
        return {}

    # Find DenuvoGames section
    denuvo_games_pattern = re.compile(r"^DenuvoGames:\s*$", re.MULTILINE)
    match = denuvo_games_pattern.search(content)
    if not match:
        return {}

    section_start = match.end()
    after_section = content[section_start:]
    next_key_pattern = re.compile(r"^[A-Za-z]", re.MULTILINE)
    next_match = next_key_pattern.search(after_section)
    section_end = section_start + next_match.start() if next_match else len(content)
    section_content = content[section_start:section_end]

    res = {}
    current_steam_id = None

    for line in section_content.split("\n"):
        line_strip = line.strip()
        if not line_strip or line_strip.startswith("#"):
            continue

        steam_id_match = re.match(r"^\s*['\"]?(\d+)['\"]?:\s*$", line)
        if steam_id_match:
            current_steam_id = steam_id_match.group(1)
            res[current_steam_id] = []
            continue

        appid_match = re.match(r"^\s*-\s*['\"]?(\d+)['\"]?\s*(?:#.*)?$", line)
        if appid_match and current_steam_id is not None:
            res[current_steam_id].append(appid_match.group(1))

    return res


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
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        logger.error(f"Failed to read config file {config_path}: {e}")
        return False

    denuvo_games_pattern = re.compile(r"^DenuvoGames:\s*$", re.MULTILINE)
    match = denuvo_games_pattern.search(content)
    if not match:
        return False

    section_start = match.end()
    after_section = content[section_start:]
    next_key_pattern = re.compile(r"^[A-Za-z]", re.MULTILINE)
    next_match = next_key_pattern.search(after_section)
    section_end = section_start + next_match.start() if next_match else len(content)

    section_content = content[section_start:section_end]
    # If there is content (indented keys or appids) under DenuvoGames, strip it
    if section_content.strip():
        new_content = content[:section_start] + "\n" + content[section_end:]
        _create_backup(config_path)
        if _atomic_write(config_path, new_content):
            logger.info(f"Successfully cleaned DenuvoGames block in {config_path}")
            return True
    return False


