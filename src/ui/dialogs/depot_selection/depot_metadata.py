"""What the dialog knows about a depot before any of it reaches a table cell.

The metadata mixin mixes two kinds of work: reading facts about depots (from the
bundle, the local database, the ini names and the enrichment cache) and painting
them into rows. This module is the first half - the facts and the rules over
them, with no Qt and no widgets:

* whether a depot still needs a name or a size,
* the byte size recorded for a branch (or for any branch, as a last resort),
* merging enrichment records into the depot dicts, matching a record both by
  depot id and by the DLC app id it belongs to,
* resolving the metadata of depots the bundle is missing, and saying which of
  them still need a network lookup,
* whether a downloaded ``.manifest`` is already on disk.

Everything here mutates or returns plain dicts, so the mixin keeps only the
threading, the signals and the widget updates.
"""

import logging
import re
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: A description that carries no name yet: "", "Depot 123", "[LINUX] DLC 45".
_PLACEHOLDER_DESC = re.compile(r"^(?:\[.*?\]\s*)?(?:Depot|DLC)\s+\d+$", re.IGNORECASE)


def is_placeholder_desc(desc) -> bool:
    """Whether a depot description says nothing but "Depot <n>" / "DLC <n>"."""
    return bool(_PLACEHOLDER_DESC.match(str(desc or "").strip()))


def needs_name(depot: dict) -> bool:
    """Whether a depot still has no real description."""
    if not isinstance(depot, dict):
        return False
    desc = str(depot.get("desc") or "").strip()
    return not desc or is_placeholder_desc(desc)


def needs_size(depot: dict) -> bool:
    """Whether a depot still has no size at all."""
    if not isinstance(depot, dict):
        return False
    return not depot.get("size") and not depot.get("size_str")


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def manifest_size(depot: dict, branch: str = "") -> int:
    """The byte size recorded for a depot, or 0 when no manifest records one.

    The branch asked for wins; ``public`` comes next; any branch that recorded a
    size is better than nothing, which is what the missing-depot rows already
    show.
    """
    if not isinstance(depot, dict):
        return 0

    size = _as_int(depot.get("size"))
    if size:
        return size

    manifests = depot.get("manifests")
    if not isinstance(manifests, dict):
        return 0

    entry = manifests.get(branch) or manifests.get("public")
    if isinstance(entry, dict):
        size = _as_int(entry.get("size") or entry.get("download"))
        if size:
            return size

    for value in manifests.values():
        if isinstance(value, dict):
            size = _as_int(value.get("size") or value.get("download"))
            if size:
                return size
    return 0


def merge_enrichments(depots: Dict, enrichments: Dict, base_appid: Optional[str] = None) -> None:
    """Merge enrichment records into depot dicts, in place.

    A record is matched by depot id, and also by the DLC app id on a depot, so
    an enrichment keyed by the DLC id still reaches the depot that carries it.
    Only blanks are filled: a description that already says something is kept,
    and a size is only taken when the depot has none.
    """
    if not enrichments or not depots:
        return

    base_aid_str = str(base_appid).strip() if base_appid else ""

    for did, info in enrichments.items():
        if not isinstance(info, dict):
            continue

        target_depot_ids = []
        if did in depots:
            target_depot_ids.append(did)
        for depot_key, depot_value in depots.items():
            if isinstance(depot_value, dict) and str(depot_value.get("dlcappid") or "") == str(did):
                if depot_key not in target_depot_ids:
                    target_depot_ids.append(depot_key)

        for target_did in target_depot_ids:
            d_data = depots[target_did]
            if not isinstance(d_data, dict):
                continue

            is_base = bool(
                base_aid_str
                and (
                    str(target_did) == base_aid_str
                    or str(info.get("dlcappid") or "") == base_aid_str
                    or str(d_data.get("dlcappid") or "") == base_aid_str
                )
            )

            dlc_tag = "[DLC]"
            if needs_name(d_data) and info.get("name"):
                if not is_base and (info.get("is_dlc") or (d_data.get("dlcappid") and str(d_data.get("dlcappid")) != base_aid_str)):
                    d_data["desc"] = f"{dlc_tag} {info['name']}"
                else:
                    d_data["desc"] = info["name"]
                d_data["name"] = info["name"]
            if (not d_data.get("size") or d_data.get("size") == 0) and info.get("size_bytes"):
                d_data["size"] = info["size_bytes"]
            if info.get("size_str"):
                d_data["size_str"] = info["size_str"]
            if info.get("oslist") and not d_data.get("oslist"):
                d_data["oslist"] = info["oslist"]
            if info.get("osarch") and not d_data.get("osarch"):
                d_data["osarch"] = info["osarch"]
            if not is_base and (info.get("is_dlc") or (d_data.get("dlcappid") and str(d_data.get("dlcappid")) != base_aid_str)):
                d_data["is_dlc"] = True


def load_ini_names() -> Dict[str, str]:
    """Depot names from the bundled ini, or {} when it cannot be read."""
    try:
        from core.ini_parser import parse_depots_ini

        return parse_depots_ini() or {}
    except Exception:
        return {}


def resolve_missing_metadata(
    missing_ids: Iterable,
    *,
    app_depots: Optional[Dict] = None,
    cached_enrichments: Optional[Dict] = None,
    ini_names: Optional[Dict] = None,
    branch: str = "public",
) -> Tuple[Dict[str, Dict], List[str]]:
    """Describe depots the local bundle is missing, from local sources only.

    Answers with the metadata found per depot id, and the ids that still lack a
    name or a size and therefore need a network lookup. ``ini_names`` is loaded
    lazily, so the dependency only pays for itself when a name is actually
    missing.
    """
    app_depots = app_depots or {}
    cached_enrichments = cached_enrichments or {}
    info_by_depot: Dict[str, Dict] = {}
    missing_to_fetch: List[str] = []

    for did in missing_ids:
        did_str = str(did)
        info: Dict = {}

        source = app_depots.get(did_str)
        if isinstance(source, dict):
            size = manifest_size(source, branch)
            if size:
                info["size"] = size
            for field in ("name", "desc", "oslist", "dlcappid", "is_dlc"):
                if not info.get(field) and source.get(field):
                    info[field] = source[field]

        source = cached_enrichments.get(did_str)
        if isinstance(source, dict):
            for field in ("name", "desc", "oslist", "dlcappid", "is_dlc"):
                if not info.get(field) and source.get(field):
                    info[field] = source[field]
            if not info.get("size") and source.get("size_bytes"):
                info["size"] = source["size_bytes"]

        if not info.get("name"):
            names = ini_names if ini_names is not None else load_ini_names()
            if did_str in names:
                info["name"] = names[did_str]

        info_by_depot[did_str] = info
        if not info.get("name") or not info.get("size"):
            missing_to_fetch.append(did_str)

    return info_by_depot, missing_to_fetch


def manifest_dirs() -> List[Path]:
    """Where a downloaded ``.manifest`` may already sit."""
    from utils.helpers import get_base_path

    return [
        Path(tempfile.gettempdir()) / "mistwalker_manifests",
        Path(get_base_path()) / "manifests",
    ]


def manifest_on_disk(did: str, mid: str, dirs: Optional[Iterable] = None) -> bool:
    """Whether ``{did}_{mid}.manifest`` is already saved in one of the dirs."""
    for search_dir in dirs if dirs is not None else manifest_dirs():
        if (Path(search_dir) / f"{did}_{mid}.manifest").exists():
            return True
    return False
