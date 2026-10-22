"""
Helpers for turning untrusted strings into safe filesystem names.

Identifiers that reach the filesystem from the network (lua.tools fix ids, app
ids, depot ids, branch names) must never be able to escape the directory they
are meant to live in. A fix id of ".." resolves to the parent directory, so a
backup path built from it pointed at the game folder itself and the cleanup
step deleted the whole install.

Two rules are enforced here:

* ``safe_path_component`` produces exactly one path component. It is never
  empty, never "." or "..", never contains a separator, and keeps distinct
  inputs distinct (a hash suffix carries the uniqueness).
* ``contained_path`` joins a relative path onto a base directory and refuses
  anything that resolves outside of it, which also catches symlink escapes.
"""

import hashlib
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ACCELA.safe_paths")

# Characters that are illegal in a file name on at least one supported platform.
_INVALID_CHARS = '<>:"/\\|?*'

# Longest single path component we are willing to produce.
_MAX_COMPONENT_LEN = 100


def safe_path_component(
    value: object,
    *,
    fallback_prefix: str = "item",
    max_len: int = _MAX_COMPONENT_LEN,
) -> str:
    """
    Convert an arbitrary value into a single, traversal-free path component.

    Args:
        value: The untrusted value (e.g. a fix id from the lua.tools API).
        fallback_prefix: Prefix used when the value carries no usable characters.
        max_len: Maximum length of the returned component.

    Returns:
        A non-empty component that is safe to join onto a directory path.
        Values that are empty or consist only of dots fall back to a
        ``<prefix>_<hash>`` name so that two different inputs never collapse
        onto the same directory.
    """
    raw = "" if value is None else str(value)
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()

    cleaned = "".join(
        "_" if (ch in _INVALID_CHARS or ch.isspace() or ord(ch) < 32) else ch
        for ch in raw
    )
    # Leading and trailing dots are meaningless at best and hostile at worst:
    # they turn the name into "." or ".." and break on Windows.
    cleaned = cleaned.strip(". ")

    if not cleaned:
        return f"{fallback_prefix}_{digest[:16]}"

    if len(cleaned) > max_len:
        # Keep the readable prefix, but let the hash carry the identity so two
        # long ids that share a prefix still get separate directories.
        cleaned = f"{cleaned[:max_len - 17]}_{digest[:12]}"

    return cleaned


def is_within(base: Path, candidate: Path) -> bool:
    """Return True when ``candidate`` resolves to ``base`` itself or inside it."""
    try:
        base_res = Path(base).resolve()
        cand_res = Path(candidate).resolve()
    except OSError as e:
        logger.debug(f"[safe_paths] Could not resolve {candidate}: {e}")
        return False
    return cand_res == base_res or base_res in cand_res.parents


def contained_path(base: Path, *parts: str) -> Optional[Path]:
    """
    Join ``parts`` onto ``base`` and return the result only if it stays inside.

    Args:
        base: The directory the result must live in.
        *parts: Relative path parts to join onto ``base``.

    Returns:
        The joined path, or None when it would escape ``base`` (via "..", an
        absolute part, or a symlink pointing elsewhere).
    """
    candidate = Path(base).joinpath(*parts)
    if is_within(base, candidate):
        return candidate
    logger.warning(f"[safe_paths] Refusing path outside {base}: {candidate}")
    return None
