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
    * Tolerates quoting around the key (`"Plugins": yes`, `'Plugins': yes`).
    * Tolerates CRLF (`\\r\\n`) and LF (`\\n`) line endings.
    * The value is matched with a lookahead rather than consumed, so a trailing
      `# comment` stays outside the match span and survives the rewrite.
    """
    return re.compile(
        r"^['\"]?" + re.escape(key) + r"['\"]?[ \t]*:(?P<sp>[ \t]*)"
        r"(?P<quote>[\"']?)"
        r"(?P<val>" + "|".join(sorted(_BOOL_SPELLINGS, key=len, reverse=True)) + r")"
        r"(?P=quote)"
        # The value must start where the separator ends and occupy the whole
        # scalar: `no#tight` is one scalar, not `no` plus a comment, and must
        # not be mistaken for a boolean.
        r"(?=[ \t]*#[^\r\n]*\r?$|[ \t]*\r?$)",
        re.MULTILINE | re.IGNORECASE,
    )


# Top-level key at column 0, value optional, trailing comment kept apart.
# The `^` anchor matters: without a column-0 constraint this matched an
# identically named key nested under another mapping, and the rewrite then
# appended a second top-level key (a duplicate, which YAML rejects or which
# silently loses to the first one).
_KEY_LINE_PATTERN_TEMPLATE = (
    r"^(?P<key>['\"]?{key}['\"]?[ \t]*:[ \t]*)"
    r"(?:\"(?:[^\"\\\r\n]|\\.)*\"|'(?:[^'\r\n]|'')*'|[^#\r\n]*?)?"
    r"(?P<tail>[ \t]+#[^\r\n]*)?\r?$"
)


def _rewrite_key_line(content: str, key: str, new_value: str) -> Optional[str]:
    """Rewrite the single column-0 line for ``key`` to carry ``new_value``.

    Keeps the key's quoting style and the separator before any inline comment,
    so `"Plugins":  # note` becomes `"Plugins": no  # note` and never
    `Plugins: no# note` (which YAML reads as the literal string "no# note").
    Returns None when the key has no column-0 line.
    """
    key_line_pattern = re.compile(
        _KEY_LINE_PATTERN_TEMPLATE.format(key=re.escape(key)), re.MULTILINE
    )
    key_match = key_line_pattern.search(content)
    if not key_match:
        return None

    tail = key_match.group("tail") or ""
    key_part = key_match.group("key")          # e.g. `Plugins:` or `"Plugins":  `
    # `key_part` keeps any whitespace that followed the colon, so an empty value
    # ('Plugins:\t# note') would otherwise produce `Plugins:yes\t# note` - no
    # separator is left before the value.
    separator = "" if key_part[-1:] in (" ", "\t") else " "
    if tail.startswith("\t"):
        # A tab before an inline comment is not valid YAML, and the validator
        # (correctly) refuses the whole write. Normalise it to a space.
        tail = " " + tail.lstrip("\t")
    replacement = f"{key_part}{separator}{new_value}{tail}"
    return content[:key_match.start()] + replacement + content[key_match.end():]


def _append_key_line(content: str, key: str, new_value: str) -> str:
    """Append a missing top-level key after a blank line, keeping one final newline."""
    head = content.rstrip("\r\n")
    newline = "\r\n" if "\r\n" in content else "\n"
    if not head:
        return f"{key}: {new_value}{newline}"
    return f"{head}{newline}{newline}{key}: {new_value}{newline}"


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

        if match and re.split(r"\r?\n", content[match.end():], maxsplit=1)[0].strip():
            # `val` is only the first word of a longer scalar ('no#tight',
            # which YAML reads as the string "no#tight"). Rewriting the boolean
            # prefix would change the scalar without setting the flag, so leave
            # an unrecognised value for a human to fix.
            logger.warning(
                f"Leaving '{key}' alone in {config_path}: its value is a single scalar "
                f"that cannot be rewritten in place safely"
            )
            return False

        if not match:
            # The key exists but not as a boolean we can rewrite in place
            # (empty value, malformed scalar, an unexpected spelling). Rewrite
            # that one line rather than appending a duplicate key, which YAML
            # would resolve in favour of whichever came first.
            new_content = _rewrite_key_line(content, key, new_value)
            if new_content is not None:
                logger.info(f"Rewrote existing '{key}' line to '{new_value}' in {config_path}")
            else:
                logger.info(f"Key '{key}' not found in {config_path}, appending '{key}: {new_value}'")
                new_content = _append_key_line(content, key, new_value)

            if not _atomic_write(config_path, new_content):
                return False
            _ALREADY_CORRECT.discard(memo) if memo else None
            return True

        if _spellings_to_bool(match.group("val")) is value:
            if memo is not None:
                _ALREADY_CORRECT.add(memo)
            return False

        # The value may be a longer scalar that merely starts with a boolean
        # spelling ('no#tight', which YAML reads as the string "no#tight").
        # Rewriting the boolean prefix would change the scalar without setting
        # the flag, so leave an unrecognised value to a human.
        line_start, line_end = match.span()
        after_value = content[line_start + match.end("val"): line_end]
        if not re.fullmatch(r"[ \t]*#[^\r\n]*|", after_value):
            logger.warning(
                f"Leaving '{key}' alone in {config_path}: its value is a single scalar "
                f"that cannot be rewritten in place safely"
            )
            return False

        # Rewrite only the matched span; anything after it (a trailing comment)
        # is left untouched because the pattern stops before it. The span is
        # re-expressed as `key:` + separator + value, so both the key's quoting
        # and the whitespace before the comment survive.
        line_start, line_end = match.span()
        matched = content[line_start:line_end]
        prefix = matched[: len(matched) - len(match.group("val"))]
        new_content = content[:line_start] + prefix + new_value + content[line_end:]

        if not _atomic_write(config_path, new_content):
            return False

        if memo is not None:
            _ALREADY_CORRECT.discard(memo)
        logger.info(f"Updated '{key}' to {new_value} in {config_path}")
        return True

    except OSError as e:
        logger.error(f"Failed to update '{key}' in {config_path}: {e}", exc_info=True)
        return False
