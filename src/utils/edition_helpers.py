"""
edition_helpers.py
==================
Centralized helper for detecting and resolving game editions (Complete, Standard,
and Store Package tiers) across target platforms (Linux / Windows) and mapping
them directly to their corresponding depot selections.
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from utils import edition_catalog
from utils.dlc_helpers import is_base_game_main_depot, is_dlc_depot
from utils.settings import get_settings

logger = logging.getLogger("ACCELA.edition_helpers")

# In-memory cache for store packages: {appid_str: [{"packageid": int, "name": str, "apps": list}]}
_STORE_PACKAGES_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def clean_package_name(raw_text: str) -> str:
    """Strip HTML tags and currency/pricing from package option text."""
    return edition_catalog.clean_package_name(raw_text)


def _is_same_game_name(left: str, right: str) -> bool:
    """True when a package name is just the game's own name.

    Steam spells the same title with trademark signs and punctuation in one
    place and without them in another ("Darkest Dungeon®" vs "Darkest
    Dungeon"), so the comparison drops everything but letters and digits.
    """
    def squash(value: str) -> str:
        return "".join(ch for ch in value.casefold() if ch.isalnum())

    a, b = squash(left), squash(right)
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def make_short_edition_name(full_name: str, fallback: str = "Edition", max_len: int = 30) -> str:
    """Create a clean label for the edition selector button, showing as much as possible up to max_len with ellipsis."""
    clean = clean_package_name(full_name).strip()
    if not clean:
        return fallback

    name_lower = clean.lower()
    if "complete" in name_lower or "all dlc" in name_lower:
        return "Complete"
    if "deluxe" in name_lower:
        return "Deluxe"
    if "standard" in name_lower or "base game" in name_lower:
        return "Standard"
    if "ultimate" in name_lower:
        return "Ultimate"
    if "gold" in name_lower:
        return "Gold"
    if "goty" in name_lower or "game of the year" in name_lower:
        return "GOTY"
    if "dark arisen" in name_lower:
        return "Dark Arisen"

    # Strip redundant game title prefix if separated by dash or colon
    if " - " in clean:
        sub = clean.split(" - ")[-1].strip()
        if len(sub) >= 3 and len(sub) <= max_len:
            return sub
    if ":" in clean:
        sub = clean.split(":", 1)[1].strip()
        if len(sub) >= 3 and len(sub) <= max_len:
            return sub

    if len(clean) <= max_len:
        return clean
    return f"{clean[:max_len - 3].rstrip()}..."


def get_available_platforms(
    depots: Dict[Any, Any],
    appid: Optional[str] = None,
    *,
    allow_network: bool = False,
    timeout: float = 2.0,
) -> List[str]:
    """
    Detect which operating systems this title ships for.

    The depot list alone cannot answer this: a game can carry a Linux depot
    while Steam still installs the Windows build, and a DLC-only game may carry
    nothing but Windows depots. When the app id is known the store's app info
    (``common.oslist``) is the authority; the depot scan is the offline guess.

    Returns ['linux', 'windows'], ['windows'], or ['linux'].
    Respects hidden platform settings from Advanced Depot Selection.
    """
    from ui.dialogs.depotselection import _depot_is_macos, _depot_is_android

    supported = (
        edition_catalog.get_supported_platforms(
            appid, allow_network=allow_network, timeout=timeout
        )
        if appid
        else set()
    )

    settings = get_settings()
    hide_linux = settings.value("hide_linux_depots", False, type=bool)
    hide_windows = settings.value("hide_windows_depots", False, type=bool)

    has_linux = False
    has_windows = False

    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict) or _depot_is_macos(d_data) or _depot_is_android(d_data):
            continue
        oslist = (d_data.get("oslist") or "").lower()
        desc = (d_data.get("desc") or "").lower()

        is_lin = "linux" in oslist or "[linux]" in desc
        is_win = "windows" in oslist or "win" in oslist or "[windows]" in desc or ".exe" in desc

        if is_lin and not is_win:
            has_linux = True
        if is_win:
            has_windows = True

    platforms: List[str] = []
    if supported:
        if has_linux and not hide_linux:
            platforms.append("linux")
        if "windows" in supported and not hide_windows:
            platforms.append("windows")
        if not platforms:
            # The user hid what Steam would install, or the depots do not
            # cover the advertised platforms. Offer the advertised platform
            # instead of a wrong one, so the selector stays usable.
            if "linux" in supported:
                platforms.append("linux")
            elif "windows" in supported:
                platforms.append("windows")
        if platforms:
            return platforms

    # No app info: the depot scan is the best answer available.
    if has_linux and not hide_linux:
        platforms.append("linux")
    # Windows is present or fallback for Proton
    if not hide_windows or not platforms:
        platforms.append("windows")

    return platforms if platforms else ["windows"]


def get_default_platform(
    depots: Dict[Any, Any],
    appid: Optional[str] = None,
    *,
    allow_network: bool = False,
    timeout: float = 2.0,
) -> str:
    """Determine the default target platform respecting OS availability and user settings."""
    platforms = get_available_platforms(
        depots, appid, allow_network=allow_network, timeout=timeout
    )
    if "linux" in platforms:
        return "linux"
    return "windows"


def fetch_store_packages(app_id: str, timeout: float = 6.0) -> List[Dict[str, Any]]:
    """Packages the store sells this game through, cached on disk.

    The request and the disk cache live in ``edition_catalog`` so a cold start
    is paid once a week rather than once per launch.
    """
    aid_str = str(app_id).strip()
    if not aid_str or not aid_str.isdigit():
        return []

    if aid_str in _STORE_PACKAGES_CACHE:
        return _STORE_PACKAGES_CACHE[aid_str]

    results = edition_catalog.fetch_store_packages(aid_str, timeout=timeout)
    _STORE_PACKAGES_CACHE[aid_str] = results
    return results


# In-memory cache for package contents: {packageid_str: {"appids": set(), "depotids": set()}}
_PACKAGE_CONTENTS_CACHE: Dict[str, Dict[str, Any]] = {}


def fetch_package_contents_batch(package_ids: List[int], timeout: float = 6.0) -> Dict[str, Dict[str, Any]]:
    """Query Steam PICS for package details (appids and depotids) for a list of package IDs."""
    if not package_ids:
        return {}

    missing_pids = [int(p) for p in package_ids if str(p) not in _PACKAGE_CONTENTS_CACHE]
    if missing_pids:
        try:
            from core.steam_api import get_steam_worker
            worker = get_steam_worker()
            res = worker.execute("get_product_info", packages=missing_pids, timeout=timeout)
            if res and isinstance(res, dict):
                pkgs_data = res.get("packages", {})
                for pid_key, pdata in pkgs_data.items():
                    if not isinstance(pdata, dict):
                        continue
                    pid_str = str(pid_key)
                    # Extract appids
                    raw_apps = pdata.get("appids", {})
                    appids_set = set()
                    if isinstance(raw_apps, dict):
                        appids_set = {str(a) for a in raw_apps.values() if str(a).isdigit()}
                    elif isinstance(raw_apps, (list, tuple)):
                        appids_set = {str(a) for a in raw_apps if str(a).isdigit()}

                    # Extract depotids
                    raw_depots = pdata.get("depotids", {})
                    depotids_set = set()
                    if isinstance(raw_depots, dict):
                        depotids_set = {str(d) for d in raw_depots.values() if str(d).isdigit()}
                    elif isinstance(raw_depots, (list, tuple)):
                        depotids_set = {str(d) for d in raw_depots if str(d).isdigit()}

                    _PACKAGE_CONTENTS_CACHE[pid_str] = {
                        "appids": appids_set,
                        "depotids": depotids_set,
                    }
        except Exception as e:
            logger.debug(f"[EditionHelpers] Failed to query PICS for package contents {missing_pids}: {e}")

    result = {}
    for p in package_ids:
        p_str = str(p)
        if p_str in _PACKAGE_CONTENTS_CACHE:
            result[p_str] = _PACKAGE_CONTENTS_CACHE[p_str]
    return result


def resolve_edition_depots(
    app_id: str,
    depots: Dict[Any, Any],
    target_platform: str = "linux",
    language: str = "english",
    include_store: bool = True,
) -> List[Dict[str, Any]]:
    """
    Computes available editions and their depot mappings for a specific OS platform.
    Always includes 'Complete' (highest, with all valid DLCs for target platform),
    followed by store packages with resolved package appid/depotid mappings,
    and finally 'Standard' (base only).

    ``include_store=False`` skips the Steam Store request and the PICS package
    lookup, so the result only carries the depot-derived Complete/Standard
    editions. Dialog construction uses that, then resolves the store editions
    when the user actually opens the edition menu.
    """
    from ui.dialogs.depotselection import (
        get_smart_default_depots,
        _depot_matches_platform,
        _depot_is_macos,
        _depot_is_android,
    )

    aid_str = str(app_id).strip()
    if not depots:
        return [
            {
                "id": "complete",
                "name": "Complete Edition",
                "short_name": "Complete",
                "platform": target_platform,
                "depot_ids": set(),
                "is_highest": True,
            }
        ]

    # Smart candidates specifically filtered for target_platform (Linux vs Windows)
    smart_candidates = set(get_smart_default_depots(depots, target_platform=target_platform, language=language))

    base_depots: Set[str] = set()
    dlc_depots: Set[str] = set()
    dlc_groups: Dict[str, List[Tuple[str, dict]]] = {}

    for did, d_data in depots.items():
        if not isinstance(d_data, dict):
            continue
        if _depot_is_macos(d_data) or _depot_is_android(d_data):
            continue
        did_str = str(did).strip()
        desc = str(d_data.get("desc") or d_data.get("name") or "")

        is_dlc = is_dlc_depot(did_str, desc=desc, base_appid=aid_str, depot_meta=d_data)
        if is_base_game_main_depot(did_str, desc, aid_str):
            is_dlc = False

        if is_dlc:
            cand = str(d_data.get("dlcappid") or "").strip()
            group_key = cand if cand and cand != aid_str else did_str
            dlc_groups.setdefault(group_key, []).append((did_str, d_data))
        elif _depot_matches_platform(d_data, target_platform):
            if did_str in smart_candidates or not smart_candidates:
                base_depots.add(did_str)

    # For each DLC group, collect depots matching target_platform.
    # If matching depots have 0 total size (stubs) and a content depot exists for this DLC,
    # include the content depot as well so users actually get the DLC data.
    for group_key, group_items in dlc_groups.items():
        matched_dids = [
            did for did, d_data in group_items if _depot_matches_platform(d_data, target_platform)
        ]
        matched_size = sum(
            int(d_data.get("size") or 0)
            for did, d_data in group_items
            if did in matched_dids
        )
        if matched_dids and matched_size > 0:
            for did in matched_dids:
                dlc_depots.add(did)
        else:
            content_dids = [
                did for did, d_data in group_items if int(d_data.get("size") or 0) > 0
            ]
            if content_dids:
                for did in matched_dids:
                    dlc_depots.add(did)
                for did in content_dids:
                    dlc_depots.add(did)
            else:
                for did in matched_dids:
                    dlc_depots.add(did)
                if not matched_dids:
                    for did, _ in group_items:
                        dlc_depots.add(did)

    if not base_depots and dlc_depots:
        base_depots = set(dlc_depots)
        dlc_depots = set()

    complete_depots = base_depots | dlc_depots
    editions: List[Dict[str, Any]] = []

    # Fetch store packages from Steam Store API via GetItems.
    # A single GetItems call returns both packages and bundle IDs for an app.
    store_pkgs = fetch_store_packages(aid_str, timeout=6.0) if include_store else []
    pkg_contents = {}
    if store_pkgs:
        pids = [int(p["packageid"]) for p in store_pkgs if p.get("packageid") and str(p.get("packageid")).isdigit()]
        if pids:
            try:
                pkg_contents = fetch_package_contents_batch(pids, timeout=4.0)
            except Exception as _e:
                logger.debug(f"[EditionHelpers] Package contents batch query failed: {_e}")

    # Complete Edition Name
    complete_edition_name = "Complete Edition"
    if dlc_depots:
        complete_edition_name = "Complete Edition (All DLCs)"

    # A. Highest: Complete Edition (contains all depots for platform)
    editions.append({
        "id": f"{target_platform}_complete",
        "name": complete_edition_name,
        "short_name": "Complete",
        "platform": target_platform,
        "depot_ids": complete_depots,
        "is_highest": True,
    })

    # B. Additional Store packages with exact depot mapping
    game_name = edition_catalog.get_app_name(aid_str) if store_pkgs else ""
    for pkg in store_pkgs:
        pkg_id = pkg.get("packageid")
        pkg_name = pkg.get("name", "")
        if not pkg_name:
            continue
        # The base game's own sub carries the same depots as Complete under the
        # game's own name; it is not an edition, so it would only be noise.
        if game_name and _is_same_game_name(pkg_name, game_name):
            continue
        short_name = make_short_edition_name(pkg_name, fallback=pkg_name)

        pinfo = pkg_contents.get(str(pkg_id)) or {}
        pkg_apps = pinfo.get("appids", set())
        pkg_depots_direct = pinfo.get("depotids", set())

        # If package details were resolved from PICS:
        if pkg_apps or pkg_depots_direct:
            matching_depots = set()
            # If base appid is inside package, include base depots
            if aid_str in pkg_apps:
                matching_depots |= base_depots

            for did in complete_depots:
                d_data = depots.get(did) or depots.get(int(did) if did.isdigit() else 0) or {}
                dlc_cand = str(d_data.get("dlcappid") or "").strip()
                if did in pkg_depots_direct or did in pkg_apps:
                    matching_depots.add(str(did))
                elif dlc_cand and dlc_cand in pkg_apps:
                    matching_depots.add(str(did))

            pkg_depots = matching_depots if matching_depots else complete_depots
        else:
            # Fallback heuristic if offline
            pkg_lower = pkg_name.lower()
            if "standard" in pkg_lower or "base" in pkg_lower:
                pkg_depots = base_depots
            else:
                pkg_depots = complete_depots

        # Only add if not identical to complete edition with the same name
        if not any(e["name"] == pkg_name for e in editions):
            editions.append({
                "id": f"{target_platform}_pkg_{pkg_id}",
                "name": pkg_name,
                "short_name": short_name,
                "platform": target_platform,
                "depot_ids": pkg_depots,
                "is_highest": False,
            })

    # B2. Store bundles — the named editions (Premium, Ancestral, ...) that
    # SteamDB lists. The app page links them, each bundle page lists the apps it
    # carries, and any app the base game does not list as DLC disqualifies it.
    if include_store:
        try:
            dlc_appids = edition_catalog.get_dlc_appids(aid_str, allow_network=True)
            for bundle in edition_catalog.bundle_editions(aid_str, dlc_appids):
                bundle_apps = {str(a).strip() for a in bundle.get("app_ids", []) if str(a).strip()}
                if aid_str not in bundle_apps:
                    continue
                bundle_depots = set(base_depots)
                for did in complete_depots:
                    d_data = depots.get(did) or {}
                    dlc_cand = str((d_data or {}).get("dlcappid") or "").strip()
                    if dlc_cand and dlc_cand != aid_str and dlc_cand in bundle_apps:
                        bundle_depots.add(str(did))
                if not bundle_depots:
                    continue
                name = str(bundle.get("name") or "").strip()
                if not name or any(e["name"] == name for e in editions):
                    continue
                bundle_id = str(bundle.get("id") or "").split(":", 1)[-1]
                editions.append({
                    "id": f"{target_platform}_bundle_{bundle_id}",
                    "name": name,
                    "short_name": make_short_edition_name(name, fallback=name),
                    "platform": target_platform,
                    "depot_ids": bundle_depots,
                    "is_highest": False,
                })
        except Exception as exc:
            logger.debug(f"[EditionHelpers] Bundle editions unavailable for {aid_str}: {exc}")

    # C. Lowest: Standard Edition (Base Game Only)
    if dlc_depots:
        editions.append({
            "id": f"{target_platform}_standard",
            "name": "Standard Edition (Base Game Only)",
            "short_name": "Standard",
            "platform": target_platform,
            "depot_ids": base_depots,
            "is_highest": False,
        })

    return editions


def resolve_multi_platform_editions(
    app_id: str,
    depots: Dict[Any, Any],
    language: str = "english",
    include_store: bool = True,
    allow_network: bool = False,
    timeout: float = 2.0,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Computes editions for all available platforms (e.g. {'linux': [...], 'windows': [...]}).

    ``include_store=False`` keeps the call offline: only the depot-derived
    Complete/Standard editions are returned.
    """
    started = time.perf_counter()
    platforms = get_available_platforms(
        depots, app_id, allow_network=allow_network, timeout=timeout
    )
    result = {}
    for plat in platforms:
        result[plat] = resolve_edition_depots(
            app_id,
            depots,
            target_platform=plat,
            language=language,
            include_store=include_store,
        )
    logger.debug(
        "[Editions] editions for %s: %.2f s, platforms=%s, store=%s",
        app_id,
        time.perf_counter() - started,
        ",".join(platforms) or "none",
        include_store,
    )
    return result


def match_selection_to_edition(
    selected_depots: Set[str],
    multi_platform_editions: Dict[str, List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Checks if a set of selected depot IDs matches any edition across platforms."""
    sel_set = {str(d).strip() for d in selected_depots if str(d).strip()}
    for plat, eds in multi_platform_editions.items():
        for ed in eds:
            ed_depots = {str(d).strip() for d in ed.get("depot_ids", set())}
            if sel_set == ed_depots:
                return ed
    return None
