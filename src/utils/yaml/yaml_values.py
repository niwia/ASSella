"""Generic scalar getters/setters for top-level config values.

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

from utils.yaml.core import _atomic_write

logger = logging.getLogger(__name__)


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
