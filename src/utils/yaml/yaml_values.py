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
from typing import Optional

from utils.yaml.core import _atomic_write

logger = logging.getLogger(__name__)

# Values SLSsteam config accepts for a boolean. Anything outside this set used
# to be treated as "key absent", which made the writer append a duplicate key
# instead of correcting the existing one - and in YAML the FIRST occurrence
# wins, so the write silently did nothing.
_TRUE_SPELLINGS = ("yes", "true", "on", "enabled", "1")
_FALSE_SPELLINGS = ("no", "false", "off", "disabled", "0")
_BOOL_SPELLINGS = _TRUE_SPELLINGS + _FALSE_SPELLINGS


def _bool_pattern(key: str) -> "re.Pattern":
    """Match a *top-level* boolean key, tolerating quoting and any casing.

    * Column-0 anchor. The old `^[ \\t]*` pattern also matched an identically
      named key nested under another mapping, and `sub(..., count=1)` rewrote
      whichever came first - so `Plugins: no` nested under `Parent:` could be
      flipped instead of the real top-level setting.
    * The value is matched with a lookahead rather than consumed, so a trailing
      `# comment` stays outside the match span and survives the rewrite.
    """
    return re.compile(
        r"^" + re.escape(key) + r"[ \t]*:[ \t]*"
        r"(?P<quote>[\"']?)"
        r"(?P<val>" + "|".join(sorted(_BOOL_SPELLINGS, key=len, reverse=True)) + r")"
        r"(?P=quote)"
        # No trailing [ \t]* consumed here: the separator before an inline
        # comment must survive, or `yes # c` is rewritten to `yes# c`, which
        # YAML reads as the literal scalar "yes#".
        r"(?=[ \t]*(?:#.*)?$)",
        re.MULTILINE | re.IGNORECASE,
    )


def _spellings_to_bool(value: str) -> Optional[bool]:
    v = value.lower()
    if v in _TRUE_SPELLINGS:
        return True
    if v in _FALSE_SPELLINGS:
        return False
    return None


# Memo of "this key is already correct in this file", so the guard that runs
# before every config mutation does not re-read and re-parse a 34 KB file two
# hundred times per library scan. Invalidated by mtime + size, so an external
# edit still takes effect on the next call.
_ALREADY_CORRECT: set = set()


def _cache_key(config_path: Path, key: str, value: bool) -> Optional[tuple]:
    try:
        st = config_path.stat()
    except OSError:
        return None
    return (str(config_path), st.st_mtime_ns, st.st_size, key, value)


def get_yaml_boolean_value(config_path: Path, key: str, default: bool = False) -> bool:
    """Get a boolean value from YAML config using regex matching."""
    try:
        if not config_path.exists():
            return default

        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        match = _bool_pattern(key).search(content)
        if match:
            parsed = _spellings_to_bool(match.group("val"))
            if parsed is not None:
                return parsed
        return default
    except Exception as e:
        logger.warning(f"Error reading '{key}' from {config_path}: {e}")
        return default

def update_yaml_boolean_value(config_path: Path, key: str, value: bool) -> bool:
    """Set a top-level boolean in YAML config, preserving layout. Appends if absent.

    Returns True only when the file was actually changed. "Already correct"
    returns False and is memoised, because this runs as a guard in front of
    every SLS config mutation and the library scan fires it ~200 times for a
    value that changes at most once.
    """
    try:
        if not config_path.exists():
            logger.warning(f"Config file not found at {config_path}")
            return False

        memo = _cache_key(config_path, key, value)
        if memo is not None and memo in _ALREADY_CORRECT:
            return False

        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        new_value = "yes" if value else "no"
        pattern = _bool_pattern(key)
        match = pattern.search(content)

        if not match:
            # Genuinely absent. Note the old pattern rejected `TRUE`, `"yes"`
            # and `enabled`, so those all landed here and appended a second
            # copy of the key - which YAML ignores, making the write a no-op.
            logger.info(f"Key '{key}' not found in {config_path}, appending '{key}: {new_value}'")
            new_content = content.rstrip() + f"\n\n{key}: {new_value}\n"
            if not _atomic_write(config_path, new_content):
                return False
            _ALREADY_CORRECT.discard(memo) if memo else None
            return True

        if _spellings_to_bool(match.group("val")) is value:
            if memo is not None:
                _ALREADY_CORRECT.add(memo)
            return False

        # Rewrite only the matched span; anything after it (a trailing comment)
        # is left untouched because the pattern stops before it.
        line_start, line_end = match.span()
        new_content = content[:line_start] + f"{key}: {new_value}" + content[line_end:]

        if not _atomic_write(config_path, new_content):
            return False

        if memo is not None:
            _ALREADY_CORRECT.discard(memo)
        logger.info(f"Updated '{key}' to {new_value} in {config_path}")
        return True

    except OSError as e:
        logger.error(f"Failed to update '{key}' in {config_path}: {e}", exc_info=True)
        return False
