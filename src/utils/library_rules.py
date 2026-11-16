"""Shared pure-local rules for library game naming, demo matching, depot checks, and key classification.

Kept lightweight at import time (no SQLite, filesystem, or network work on import) so callers in
GameManager, acf_scanner, filter_sort, and disk caches stay instant on startup.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Iterable, List, Mapping, Optional

logger = logging.getLogger(__name__)

_PLACEHOLDER_NAME_RE = re.compile(
    r"^(?:app(?:id)?[\s_:-]*\d+|unknown(?:\s+app)?[\s_:-]*\d*)$",
    re.IGNORECASE,
)

# Matches demo suffixes like "Plants vs. Zombies Demo", "foo_demo", "foo-demo",
# "foo [Demo]", or "foo (Demo)", without matching titles like "DemonCrawl".
_DEMO_SUFFIX_RE = re.compile(
    r"(?:^|[\s_\-\(\[])demo[\)\]]*$",
    re.IGNORECASE,
)


def is_placeholder_game_name(name: Any, appid: Any = None) -> bool:
    """Return True when *name* is missing or a synthetic placeholder (e.g. 'appid_262060')."""
    if name is None:
        return True
    s = str(name).strip()
    if not s:
        return True
    if appid is not None:
        aid = str(appid).strip()
        if aid and s == aid:
            return True
    return bool(_PLACEHOLDER_NAME_RE.match(s))


def is_demo_game_name(name: Any) -> bool:
    """Return True when *name* ends with 'demo' or '_demo' (case-insensitive)."""
    if not name:
        return False
    s = str(name).strip()
    if not s:
        return False
    return bool(_DEMO_SUFFIX_RE.search(s))


def resolve_best_game_name(
    appid: Any,
    candidates: Iterable[Optional[str]] = (),
    *,
    db_manager: Any = None,
    fallback_installdir: str = "",
) -> str:
    """Resolve the best non-placeholder title for *appid* using local sources only.

    Priority:
      1. First non-placeholder candidate string passed by the caller.
      2. Local DatabaseManager (`steam_headers.db` apps table, bypass_expiration=True).
      3. Non-placeholder *fallback_installdir*.
      4. `'App <appid>'` if *appid* is valid, else `'Unknown Game'`.
    """
    appid_str = str(appid).strip() if appid is not None else ""
    valid_aid = appid_str if appid_str.isdigit() else ""

    for cand in candidates:
        if isinstance(cand, str) and not is_placeholder_game_name(cand, appid_str):
            return cand.strip()

    if valid_aid:
        try:
            db = db_manager
            if db is None:
                from managers.db_manager import DatabaseManager

                db = DatabaseManager()
            info = None
            try:
                info = db.get_app_info(valid_aid, bypass_expiration=True)
            except TypeError:
                info = db.get_app_info(valid_aid)
            if isinstance(info, Mapping):
                db_name = info.get("name")
                if isinstance(db_name, str) and not is_placeholder_game_name(db_name, valid_aid):
                    return db_name.strip()
        except Exception as _silent_err:
            logger.debug("[library_rules] DB name lookup skipped for %s: %s", valid_aid, _silent_err)

    if isinstance(fallback_installdir, str) and not is_placeholder_game_name(fallback_installdir, appid_str):
        cleaned = fallback_installdir.strip()
        if cleaned:
            return cleaned

    return f"App {valid_aid}" if valid_aid else "Unknown Game"


def extract_depot_ids_from_file(depot_file_path: str) -> List[str]:
    """Read ALL valid numeric depot IDs from a `<appid>.depot` file."""
    if not depot_file_path or not os.path.isfile(depot_file_path):
        return []
    depots: List[str] = []
    seen = set()
    try:
        with open(depot_file_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                did = line.split(":", 1)[0].strip()
                if did.isdigit() and did not in seen:
                    seen.add(did)
                    depots.append(did)
    except OSError as _silent_err:
        logger.debug("[library_rules] extract_depot_ids_from_file %s: %s", depot_file_path, _silent_err)
    return depots


def all_depots_are_dlc(depot_ids: Iterable[Any]) -> bool:
    """Return True only when at least one non-shared depot exists and ALL belong to DLCs."""
    try:
        from utils.dlc_helpers import is_dlc_depot
        from utils.yaml_config_manager import SHARED_REDISTS
    except Exception as e:
        logger.debug("[library_rules] all_depots_are_dlc imports failed: %s", e)
        return False

    valid = [
        str(d).strip()
        for d in depot_ids
        if str(d).strip().isdigit() and str(d).strip() not in SHARED_REDISTS
    ]
    if not valid:
        return False
    return all(is_dlc_depot(d) for d in valid)


def has_decryption_key_for_game(
    appid: Any,
    decryption_keys: Any,
    depot_ids: Iterable[Any] = (),
) -> bool:
    """Return True if *decryption_keys* contains the parent *appid* or any non-shared depot key."""
    if not decryption_keys:
        return False

    try:
        from utils.yaml_config_manager import SHARED_REDISTS
    except Exception as _silent_err:
        logger.debug("[library_rules] SHARED_REDISTS unavailable: %s", _silent_err)
        SHARED_REDISTS = set()

    if isinstance(decryption_keys, Mapping):
        key_present = lambda k: bool(decryption_keys.get(k))
    else:
        key_set = {str(k).strip() for k in decryption_keys if k is not None}
        key_present = lambda k: k in key_set

    aid = str(appid).strip() if appid is not None else ""
    if aid and key_present(aid):
        return True

    for did in depot_ids or ():
        did_str = str(did).strip()
        if did_str and did_str not in SHARED_REDISTS and key_present(did_str):
            return True
    return False


def compute_directory_size(root_path: str) -> int:
    """Compute total regular-file size in bytes under *root_path* without following symlinks."""
    if not root_path or not os.path.isdir(root_path):
        return 0
    total = 0
    stack = [root_path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError as _silent_err:
                        logger.debug("[library_rules] skip unreadable entry in %s: %s", current, _silent_err)
                        continue
        except OSError as _silent_err:
            logger.debug("[library_rules] skip unreadable directory %s: %s", current, _silent_err)
            continue
    return total


def repair_cached_game_names(games: Iterable[dict], db_manager: Any = None) -> bool:
    """Repair placeholder names (`appid_<id>`, `App <id>`, empty) in *games* in place.

    Lazily instantiates a single local DatabaseManager only when at least one
    entry actually needs repair so healthy caches pay zero DB overhead.
    """
    if not games:
        return False
    db = db_manager
    db_initialized = db is not None
    repaired = False
    for g in games:
        if not isinstance(g, dict):
            continue
        aid = str(g.get("appid", "")).strip()
        cur_name = g.get("game_name")
        if not is_placeholder_game_name(cur_name, aid):
            continue
        if not db_initialized:
            db_initialized = True
            try:
                from managers.db_manager import DatabaseManager

                db = DatabaseManager()
            except Exception as _silent_err:
                logger.debug("[library_rules] DatabaseManager unavailable during cache repair: %s", _silent_err)
                db = None
        fallback_dir = str(g.get("install_dir") or "").strip()
        new_name = resolve_best_game_name(
            aid,
            (),
            db_manager=db,
            fallback_installdir=fallback_dir,
        )
        if new_name and new_name != cur_name:
            g["game_name"] = new_name
            repaired = True
    return repaired
