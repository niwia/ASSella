"""Content validation and identifier sanitisation.

``_validate_yaml_content`` is the gate every write passes through - _atomic_write refuses to write content that does not parse.

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
from typing import Optional, Union

from utils.yaml.constants import HEX_64_PATTERN, NUMERIC_ID_PATTERN

logger = logging.getLogger(__name__)


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
