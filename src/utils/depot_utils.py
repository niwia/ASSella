"""
depot_utils.py — Consolidated utility for comparing and validating Hubcap manifests against Steam API depot data.
"""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from utils.branch_helpers import resolve_branch_manifest_gid

logger = logging.getLogger(__name__)

NON_DEPOT_KEYS = {
    "branches",
    "hasdepotsindlc",
    "listofdlc",
    "dlcs_expanded",
    "status",
    "depot_from_app",
}

try:
    from ui.assets import DEPOT_BLACKLIST
    BLACKLISTED_DEPOTS = {str(d) for d in DEPOT_BLACKLIST}
except Exception:
    BLACKLISTED_DEPOTS = set()


def get_depot_manifest_gid(d_info: Any, branch: str = "public", allow_public_fallback: bool = False) -> Optional[str]:
    """
    Extracts the manifest GID for a given branch from a Steam depot info dictionary.

    The branch entry (``manifests[branch]``) always wins. ``manifest_id`` is the
    PUBLIC GID, so it is only used for the public branch or when
    ``allow_public_fallback`` is set (depots that live in a DLC app, which carry
    no beta branches of their own). A base-app depot with no manifest on the
    branch returns None — it is not part of that branch's build.
    """
    return resolve_branch_manifest_gid(d_info, branch, allow_public_fallback=allow_public_fallback)


def _is_not_in_branch(d_info: Any, branch: str) -> bool:
    """True when Steam lists manifests for this depot but none for `branch`
    (and the depot is not a DLC-app depot that may use public)."""
    if branch == "public" or not isinstance(d_info, dict):
        return False
    manifests = d_info.get("manifests")
    if not isinstance(manifests, dict) or not manifests:
        return False
    if d_info.get("from_dlc_app"):
        return False
    return branch not in manifests


def check_hubcap_vs_steam_depots(
    zip_depots: Union[Dict[str, Any], List[Union[str, int]], Set[Union[str, int]]],
    api_depots: Dict[str, Any],
    app_id: Optional[Union[str, int]] = None,
    branch: str = "public",
) -> Dict[str, Any]:
    """
    Compares Hubcap/local depots against official Steam API depots for an app and branch.

    Args:
        zip_depots: Dict of {depot_id: manifest_id} or {depot_id: lua_data},
                    or a list/set of depot IDs.
        api_depots: Dict of {depot_id: depot_info} from Steam API.
        app_id: Optional base AppID to exclude from depot comparisons.
        branch: Target Steam branch (default "public").

    Returns:
        dict:
            - is_up_to_date (bool): True if no cached depots have stale manifest GIDs.
            - stale_depots (list): [{'depot_id', 'cached_manifest_id', 'current_manifest_id', 'name'}]
            - missing_from_hubcap (list[str]): Depot IDs present on Steam for this branch but missing locally.
            - missing_depots_info (dict): Metadata dictionaries for the missing depots.
            - missing_for_fetch (list[tuple]): [(depot_id, manifest_id, name)] ready for auto-fetch.
            - extra_in_hubcap (list[str]): Depot IDs present locally but not listed in Steam API.
    """
    app_id_str = str(app_id).strip() if app_id is not None else ""
    b_key = branch if branch else "public"

    # Normalize zip_depots into a dict mapping depot_id -> manifest_id (if available)
    local_manifest_map: Dict[str, Optional[str]] = {}
    if isinstance(zip_depots, dict):
        for k, v in zip_depots.items():
            did_str = str(k).strip()
            if not did_str.isdigit() or (app_id_str and did_str == app_id_str):
                continue
            # v might be a string manifest_id, int, or dict
            if isinstance(v, (str, int)):
                local_manifest_map[did_str] = str(v).strip()
            elif isinstance(v, dict):
                m = v.get("manifest_id") or v.get("gid")
                local_manifest_map[did_str] = str(m).strip() if m else None
            else:
                local_manifest_map[did_str] = None
    elif isinstance(zip_depots, (list, set, tuple)):
        for item in zip_depots:
            did_str = str(item).strip()
            if did_str.isdigit() and (not app_id_str or did_str != app_id_str):
                local_manifest_map[did_str] = None

    is_up_to_date = True
    stale_depots: List[Dict[str, Any]] = []

    # 1. Staleness check: compare local manifest GIDs against Steam API
    if isinstance(api_depots, dict):
        for did, local_mid in local_manifest_map.items():
            if not local_mid:
                continue
            dinfo = api_depots.get(did) or api_depots.get(int(did))
            if dinfo and isinstance(dinfo, dict):
                current_mid = get_depot_manifest_gid(
                    dinfo, branch=b_key, allow_public_fallback=bool(dinfo.get("from_dlc_app"))
                )
                if current_mid and str(local_mid) != str(current_mid):
                    dname = dinfo.get("name") or f"Depot {did}"
                    logger.info(
                        f"[depot_utils] Stale depot detected: {did} (cached={local_mid}, current={current_mid})"
                    )
                    is_up_to_date = False
                    stale_depots.append({
                        "depot_id": did,
                        "cached_manifest_id": str(local_mid),
                        "current_manifest_id": str(current_mid),
                        "name": dname,
                    })

    # 2. Bidirectional check: find official depots on Steam missing from zip/cache
    missing_from_hubcap: List[str] = []
    missing_depots_info: Dict[str, Any] = {}
    missing_for_fetch: List[Tuple[str, str, str]] = []

    if isinstance(api_depots, dict):
        for did_key, dinfo in api_depots.items():
            did_str = str(did_key).strip()
            if not did_str.isdigit():
                continue
            if did_str in NON_DEPOT_KEYS or did_str in BLACKLISTED_DEPOTS:
                continue
            if app_id_str and did_str == app_id_str:
                continue
            if did_str in local_manifest_map:
                continue
            if _is_not_in_branch(dinfo, b_key):
                # Not shipped on this branch at all — not "missing from Hubcap".
                continue

            current_mid = get_depot_manifest_gid(
                dinfo, branch=b_key,
                allow_public_fallback=bool(dinfo.get("from_dlc_app")) if isinstance(dinfo, dict) else False,
            )
            dname = dinfo.get("name") or f"Depot {did_str}" if isinstance(dinfo, dict) else f"Depot {did_str}"

            missing_from_hubcap.append(did_str)
            missing_depots_info[did_str] = dict(dinfo) if isinstance(dinfo, dict) else {}

            if current_mid:
                missing_for_fetch.append((did_str, str(current_mid), dname))

    # 3. Extra in hubcap (present locally, but not on Steam)
    extra_in_hubcap: List[str] = []
    if isinstance(api_depots, dict) and api_depots:
        for did in local_manifest_map:
            if did not in api_depots and (not did.isdigit() or int(did) not in api_depots):
                extra_in_hubcap.append(did)

    return {
        "is_up_to_date": is_up_to_date,
        "stale_depots": stale_depots,
        "missing_from_hubcap": missing_from_hubcap,
        "missing_depots_info": missing_depots_info,
        "missing_for_fetch": missing_for_fetch,
        "extra_in_hubcap": extra_in_hubcap,
    }
