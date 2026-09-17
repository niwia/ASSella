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
    BLACKLISTED_DEPOTS = {str(d) for d in DEPOT_BLACKLIST} | {"228980", "1034630"}
except Exception:
    BLACKLISTED_DEPOTS = {"228980", "1034630"}

REDIST_NAME_KEYWORDS = (
    "directx",
    "vc redist",
    "vcredist",
    "visual c++",
    "dotnet",
    ".net framework",
    ".net core",
    "openal",
    "physx",
    "installscript",
    "prerequisites",
    "redistributable",
    "steamworks shared",
)


def is_redist_or_dependency(dinfo: Any, did_str: str) -> bool:
    """True if depot is a redistributable, system package, or external dependency."""
    if did_str in BLACKLISTED_DEPOTS:
        return True
    if isinstance(dinfo, dict):
        if str(dinfo.get("sharedinstall")).strip().lower() in ("1", "true", "yes"):
            return True
        if dinfo.get("system") in (1, "1", True):
            return True
        depot_from_app = str(dinfo.get("depotfromapp") or "").strip()
        if depot_from_app and (depot_from_app in BLACKLISTED_DEPOTS or depot_from_app in ("228980", "1034630")):
            return True
        dname = (dinfo.get("name") or "").lower()
        if any(kw in dname for kw in REDIST_NAME_KEYWORDS):
            return True
    return False


def is_zero_byte_depot(dinfo: Any, branch: str = "public") -> bool:
    """True if depot size is explicitly 0 or contains no files/manifest data."""
    if not isinstance(dinfo, dict):
        return False
    size = dinfo.get("size")
    download = dinfo.get("download")
    manifests = dinfo.get("manifests")
    if isinstance(manifests, dict):
        b_entry = manifests.get(branch) or manifests.get("public")
        if isinstance(b_entry, dict):
            if "size" in b_entry and b_entry["size"] is not None:
                size = b_entry["size"]
            if "download" in b_entry and b_entry["download"] is not None:
                download = b_entry["download"]
    for s_val in (size, download):
        if s_val is not None:
            try:
                s_int = int(str(s_val).strip())
                if s_int == 0:
                    return True
                if s_int > 0:
                    return False
            except (ValueError, TypeError):
                pass
    if not manifests and size is None and download is None:
        return True
    return False


def extract_depot_size(dinfo: Any, branch: str = "public") -> Optional[int]:
    """Extract depot size in bytes from size, maxsize, or manifest entries."""
    if not isinstance(dinfo, dict):
        return None
    for k in ("size", "maxsize"):
        val = dinfo.get(k)
        if val is not None:
            try:
                s_int = int(str(val).strip())
                if s_int > 0:
                    return s_int
            except (ValueError, TypeError):
                pass
    manifests = dinfo.get("manifests")
    if isinstance(manifests, dict):
        b_entry = manifests.get(branch) or manifests.get("public")
        if isinstance(b_entry, dict):
            for s_key in ("size", "download"):
                val = b_entry.get(s_key)
                if val is not None:
                    try:
                        s_int = int(str(val).strip())
                        if s_int > 0:
                            return s_int
                    except (ValueError, TypeError):
                        pass
        for m_val in manifests.values():
            if isinstance(m_val, dict):
                for s_key in ("size", "download"):
                    val = m_val.get(s_key)
                    if val is not None:
                        try:
                            s_int = int(str(val).strip())
                            if s_int > 0:
                                return s_int
                        except (ValueError, TypeError):
                            pass
    return None


def is_os_filtered(dinfo: Any, hide_macos: bool, hide_android: bool) -> bool:
    """True if depot OS matches an OS category the user opted to filter out."""
    if not isinstance(dinfo, dict):
        return False
    os_val = str(dinfo.get("oslist") or "").strip().lower()
    if not os_val:
        return False
    if hide_macos and os_val in ("macos", "macosx"):
        return True
    if hide_android and os_val == "android":
        return True
    return False


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

    Applies strict filtering:
      - Excludes dependencies & redistributables (DirectX, VC++, .NET, etc.).
      - Excludes 0-byte/empty placeholder depots.
      - Excludes platform depots the user has opted to hide (macOS / Android).
      - If the game has <= 1 relevant content depot on this branch, skips missing checks completely.

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

    # User OS preferences
    try:
        from utils.settings import get_settings
        settings = get_settings()
        hide_macos = settings.value("hide_macos_depots", True, type=bool)
        hide_android = settings.value("hide_android_depots", True, type=bool)
    except Exception:
        hide_macos = True
        hide_android = True

    # Normalize zip_depots into a dict mapping depot_id -> manifest_id (if available)
    local_manifest_map: Dict[str, Optional[str]] = {}
    if isinstance(zip_depots, dict):
        for k, v in zip_depots.items():
            did_str = str(k).strip()
            if not did_str.isdigit() or (app_id_str and did_str == app_id_str):
                continue
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

    # Extra in hubcap (present locally, but not on Steam)
    extra_in_hubcap: List[str] = []
    if isinstance(api_depots, dict) and api_depots:
        for did in local_manifest_map:
            if did not in api_depots and (not did.isdigit() or int(did) not in api_depots):
                extra_in_hubcap.append(did)

    # 2. Gather all relevant content depots on Steam for this branch
    relevant_content_depots: List[Tuple[str, Dict[str, Any], str]] = []
    if isinstance(api_depots, dict):
        for did_key, dinfo in api_depots.items():
            did_str = str(did_key).strip()
            if not did_str.isdigit() or did_str in NON_DEPOT_KEYS:
                continue
            if app_id_str and did_str == app_id_str:
                continue
            if is_redist_or_dependency(dinfo, did_str):
                continue
            if is_zero_byte_depot(dinfo, branch=b_key):
                continue
            if is_os_filtered(dinfo, hide_macos, hide_android):
                continue
            if _is_not_in_branch(dinfo, b_key):
                continue

            current_mid = get_depot_manifest_gid(
                dinfo, branch=b_key,
                allow_public_fallback=bool(dinfo.get("from_dlc_app")) if isinstance(dinfo, dict) else False,
            )
            if not current_mid:
                continue

            relevant_content_depots.append((did_str, dict(dinfo) if isinstance(dinfo, dict) else {}, str(current_mid)))

    # 3. Single-depot / logical single-depot rule:
    # If the game only has <= 1 relevant content depot (or none), and Hubcap provided content,
    # skip missing depot reporting completely to avoid false positives.
    if len(relevant_content_depots) <= 1:
        logger.debug(
            f"[depot_utils] App {app_id_str or 'unknown'} has <= 1 relevant content depot ({len(relevant_content_depots)}). "
            f"Skipping missing depot check."
        )
        return {
            "is_up_to_date": is_up_to_date,
            "stale_depots": stale_depots,
            "missing_from_hubcap": [],
            "missing_depots_info": {},
            "missing_for_fetch": [],
            "extra_in_hubcap": extra_in_hubcap,
        }

    # 4. Multi-depot game: identify missing content depots
    missing_from_hubcap: List[str] = []
    missing_depots_info: Dict[str, Any] = {}
    missing_for_fetch: List[Tuple[str, str, str]] = []

    for did_str, dinfo, current_mid in relevant_content_depots:
        if did_str in local_manifest_map:
            continue
        dname = dinfo.get("name")
        if not dname or dname.lower().startswith("depot "):
            try:
                from core.ini_parser import parse_depots_ini
                ini_names = parse_depots_ini()
                if did_str in ini_names:
                    dname = ini_names[did_str]
            except Exception:
                pass
        if not dname:
            dname = f"Depot {did_str}"
        dinfo["name"] = dname

        if not dinfo.get("size"):
            extracted_sz = extract_depot_size(dinfo, branch=b_key)
            if extracted_sz:
                dinfo["size"] = extracted_sz
        missing_from_hubcap.append(did_str)
        missing_depots_info[did_str] = dinfo
        missing_for_fetch.append((did_str, current_mid, dname))

    return {
        "is_up_to_date": is_up_to_date,
        "stale_depots": stale_depots,
        "missing_from_hubcap": missing_from_hubcap,
        "missing_depots_info": missing_depots_info,
        "missing_for_fetch": missing_for_fetch,
        "extra_in_hubcap": extra_in_hubcap,
    }
