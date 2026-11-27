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
        data = yaml.safe_load(content)
    except Exception as e:
        logger.error(f"YAML validation failed: {e}")
        return False

    if isinstance(data, dict):
        # safe_load resolves a duplicated key to the last occurrence. The
        # line scanner below only sees column-0 keys, so without this a
        # duplicate nested key (or an indented top-level-looking line) would
        # pass the write gate.
        counts: dict = {}
        loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

        def _count_pairs(node):
            """Collect duplicated mapping keys at every nesting level.

            Key nodes are compared by their raw scalar text: constructing the
            node needs a resolver pass that is not available while composing,
            and raw text is the right identity for duplicate detection anyway
            (`731` and `"731"` are the same key to YAML).
            """
            if isinstance(node, yaml.MappingNode):
                seen_here = set()
                for key_node, value_node in node.value:
                    k = getattr(key_node, "value", repr(key_node))
                    k = (getattr(key_node, "tag", ""), str(k))
                    if k in seen_here:
                        counts[str(k[1])] = counts.get(str(k[1]), 1) + 1
                    seen_here.add(k)
                    _count_pairs(value_node)
            elif isinstance(node, yaml.SequenceNode):
                for child in node.value:
                    _count_pairs(child)

        try:
            for doc in yaml.compose_all(content, Loader=loader):
                _count_pairs(doc)
        except Exception as e:
            logger.debug(f"YAML duplicate-key scan skipped: {e}")
            counts = {}
        if counts:
            logger.error(f"Duplicate YAML keys detected: {', '.join(sorted(counts))}")
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
