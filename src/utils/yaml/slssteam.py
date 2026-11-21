"""Prerequisites SLSsteam expects to be enabled in config.yaml.

Extracted from ``utils/yaml_config_manager.py``. That module is now a thin
facade that re-exports this package's public names, so every existing
``from utils.yaml_config_manager import ...`` keeps working unchanged.

Do not import across sibling modules in this package except from ``core`` -
dependencies must point in one direction (leaf -> core) to keep the package
import-cycle free.
"""

import logging

# --- imports ---------------------------------------------------------
import re
from pathlib import Path
from typing import Optional

from utils.yaml.core import _atomic_write, _create_backup, batch_config_edit, get_user_config_path
from utils.yaml.modes import is_slssteam_config_management_enabled, is_slssteam_mode_enabled
from utils.yaml.yaml_values import set_yaml_boolean_content, update_yaml_boolean_value

logger = logging.getLogger(__name__)


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

        with open(config_path, "r", encoding="utf-8", errors="replace") as f:
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

def ensure_smart_tickets_enabled(config_path: Optional[Path] = None, enable: bool = True) -> bool:
    """Deprecated: Upstream SLSsteam removed SmartTickets; ASSella no longer manages this flag."""
    logger.debug("ensure_smart_tickets_enabled: SmartTickets is deprecated and no longer managed in SLSsteam config.")
    return True

def is_smart_tickets_enabled(config_path: Optional[Path] = None) -> bool:
    """Check if legacy 'SmartTickets: 0x1' is enabled in SLSsteam config.yaml."""
    if config_path is None:
        config_path = get_user_config_path()

    if not config_path.exists():
        return False

    try:
        with open(config_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        match = re.search(r"^([ \t]*)SmartTickets[ \t]*:[ \t]*([^\r\n#]+)", content, re.MULTILINE)
        if match:
            raw = match.group(2).strip().lower()
            return raw in ("0x1", "1", "yes", "true")
    except Exception:
        pass
    return False

_LOG_LEVELS_PATTERN = re.compile(
    r"^([ \t]*)LogLevels[ \t]*:[ \t]*([^\r\n#]+)(.*)$",
    re.MULTILINE,
)
_LOG_LEVEL_PATTERN = re.compile(
    r"^([ \t]*)LogLevel[ \t]*:[ \t]*([^\r\n#]+)(.*)$",
    re.MULTILINE,
)


def _parse_level(raw_val: str) -> int:
    raw_val = raw_val.strip().strip('"').strip("'")
    try:
        return int(raw_val, 16) if raw_val.lower().startswith("0x") else int(raw_val, 10)
    except ValueError:
        return 0


def _enable_once_flag(content: str) -> Optional[str]:
    """Return ``content`` with the Once log level enabled, or None if it already is.

    New SLSsteam uses a bitmask (`LogLevels`, bit 0x2 = Once); the old format
    used an enum (`LogLevel`, 0 = Once). Only the value span changes, so any
    inline comment and the rest of the file stay as they were.
    """
    match = _LOG_LEVELS_PATTERN.search(content)
    if match:
        current = _parse_level(match.group(2))
        if current & 0x2:
            return None
        indent, comment = match.group(1), match.group(3)
        comment_str = f" {comment.strip()}" if comment.strip() else ""
        replacement = f"{indent}LogLevels: 0x{current | 0x2:X}{comment_str}"
        return _LOG_LEVELS_PATTERN.sub(replacement, content, count=1)

    match = _LOG_LEVEL_PATTERN.search(content)
    if match:
        if _parse_level(match.group(2)) == 0:
            return None
        indent, comment = match.group(1), match.group(3)
        comment_str = f" {comment.strip()}" if comment.strip() else ""
        replacement = f"{indent}LogLevel: 0{comment_str}"
        return _LOG_LEVEL_PATTERN.sub(replacement, content, count=1)

    logger.debug("No LogLevels/LogLevel key found (default enables all)")
    return None


def ensure_slssteam_prerequisites(config_path: Optional[Path] = None) -> bool:
    """Silently ensure all SLSsteam configuration prerequisites are met.

    Specifically ensures:
      1. API: yes (for communication via /tmp/SLSsteam.API)
      2. LogLevels has 0x2 / Once flag enabled (or old LogLevel: 0)
      3. Plugins: yes (for library-inject plugin loader)

    All three are applied to one in-memory copy and committed as a single
    in-place write, so SLSsteam's file watcher reloads once instead of up to
    three times. Creates a backup before applying any modification and never
    shows disruptive UI popups - logs actions at INFO/DEBUG level.
    """
    if config_path is None:
        config_path = get_user_config_path()

    if not config_path.exists():
        logger.debug(f"ensure_slssteam_prerequisites: Config not found at {config_path}")
        return False

    if not is_slssteam_config_management_enabled():
        logger.debug("ensure_slssteam_prerequisites: SLS config management disabled in settings")
        return False

    try:
        with batch_config_edit(config_path) as editor:
            content = editor.content
            changed = []

            new_content = set_yaml_boolean_content(content, "API", True)
            if new_content is not None:
                content = new_content
                changed.append("API: yes")

            new_content = _enable_once_flag(content)
            if new_content is not None:
                content = new_content
                changed.append("LogLevels Once flag")

            new_content = set_yaml_boolean_content(content, "Plugins", True)
            if new_content is not None:
                content = new_content
                changed.append("Plugins: yes")

            if changed:
                # Snapshot the original before this session's first write, the
                # same way the single-key writers do.
                _create_backup(config_path)
                editor.content = content
                editor.has_changes = True
        if not editor.committed_successfully:
            logger.error(f"ensure_slssteam_prerequisites: Could not write {config_path}")
            return False
        if changed:
            logger.info(f"Silently ensured SLSsteam prerequisites in config.yaml ({', '.join(changed)})")
        return bool(changed)
    except Exception as e:
        logger.warning(f"Error ensuring SLSsteam prerequisites: {e}")
        return False
