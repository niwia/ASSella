"""
edition_helpers.py
==================
Centralized helper for detecting and resolving game editions (Complete, Standard,
and Store Package tiers) across target platforms (Linux / Windows) and mapping
them directly to their corresponding depot selections.
"""

import json
import logging
import re
import urllib.request
from typing import Any, Dict, List, Optional, Set

from utils.dlc_helpers import is_base_game_main_depot, is_dlc_depot
from utils.settings import get_settings

logger = logging.getLogger("ACCELA.edition_helpers")

# In-memory cache for store packages: {appid_str: [{"packageid": int, "name": str, "apps": list}]}
_STORE_PACKAGES_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def clean_package_name(raw_text: str) -> str:
    """Strip HTML tags and currency/pricing from package option text."""
    if not raw_text:
        return ""
    clean = re.sub(r"<[^>]+>", "", str(raw_text)).strip()
    clean = re.sub(r"\s*-\s*[\$€£¥\d\.,\s]+$", "", clean).strip()
    return clean


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


def get_available_platforms(depots: Dict[Any, Any]) -> List[str]:
    """
    Detect which operating systems have dedicated depots in this title.
    Returns ['linux', 'windows'], ['windows'], or ['linux'].
    Respects hidden platform settings from Advanced Depot Selection.
    """
    from ui.dialogs.depotselection import _depot_is_macos, _depot_is_android

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

        if "linux" in oslist or "[linux]" in desc:
            has_linux = True
        if "windows" in oslist or "[windows]" in desc or ".exe" in desc:
            has_windows = True

    platforms: List[str] = []
    # If explicit native Linux exists and not hidden in settings
    if has_linux and not hide_linux:
        platforms.append("linux")
    # Windows is present or fallback for Proton
    if not hide_windows or not platforms:
        platforms.append("windows")

    return platforms if platforms else ["windows"]


def get_default_platform(depots: Dict[Any, Any]) -> str:
    """Determine the default target platform respecting OS availability and user settings."""
    platforms = get_available_platforms(depots)
    if "linux" in platforms:
        return "linux"
    return "windows"


def fetch_store_packages(app_id: str, timeout: float = 2.5) -> List[Dict[str, Any]]:
    """Query Steam Store API appdetails for package_groups."""
    aid_str = str(app_id).strip()
    if not aid_str or not aid_str.isdigit():
        return []

    if aid_str in _STORE_PACKAGES_CACHE:
        return _STORE_PACKAGES_CACHE[aid_str]

    results: List[Dict[str, Any]] = []
    try:
        url = f"https://store.steampowered.com/api/appdetails?appids={aid_str}&cc=us&l=en"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SteamStoreAPI"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            app_data = data.get(aid_str, {}).get("data", {})
            pkg_groups = app_data.get("package_groups", [])
            for pg in pkg_groups:
                for sub in pg.get("subs", []):
                    pkg_id = sub.get("packageid")
                    if not pkg_id:
                        continue
                    desc = sub.get("option_description") or ""
                    text = sub.get("option_text") or ""
                    cleaned = clean_package_name(desc) or clean_package_name(text) or f"Package {pkg_id}"
                    results.append({
                        "packageid": pkg_id,
                        "name": cleaned,
                        "price": sub.get("price_in_cents_with_discount", 0) or 0,
                        "apps": [],
                    })
    except Exception as e:
        logger.debug(f"[EditionHelpers] Store package fetch for {aid_str} failed: {e}")

    _STORE_PACKAGES_CACHE[aid_str] = results
    return results


def resolve_edition_depots(
    app_id: str,
    depots: Dict[Any, Any],
    target_platform: str = "linux",
    language: str = "english",
) -> List[Dict[str, Any]]:
    """
    Computes available editions and their depot mappings for a specific OS platform.
    Always includes 'Complete' (highest, using official Steam package name if available,
    with all valid DLCs for target platform), followed by any store packages,
    and finally 'Standard' (base only).
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

    for did in smart_candidates:
        d_data = depots.get(did) or depots.get(int(did) if did.isdigit() else 0) or {}
        desc = str(d_data.get("desc") or d_data.get("name") or "")

        is_dlc = is_dlc_depot(did, desc=desc, base_appid=aid_str, depot_meta=d_data)
        if is_base_game_main_depot(did, desc, aid_str):
            is_dlc = False

        if is_dlc:
            dlc_depots.add(str(did))
        else:
            base_depots.add(str(did))

    if not base_depots and dlc_depots:
        base_depots = set(dlc_depots)
        dlc_depots = set()

    complete_depots = base_depots | dlc_depots
    editions: List[Dict[str, Any]] = []

    # Fetch store packages from Steam store API
    store_pkgs = fetch_store_packages(aid_str, timeout=1.5)

    # Determine highest complete edition name
    complete_edition_name = "Complete Edition"
    if dlc_depots:
        complete_edition_name = "Complete Edition (All DLCs)"

    best_pkg = None
    if store_pkgs:
        # Prioritize packages with edition or bundle keywords
        keywords = ("complete", "deluxe", "ultimate", "gold", "goty", "game of the year", "collector", "edition", "bundle")
        matching_pkgs = [p for p in store_pkgs if any(kw in p.get("name", "").lower() for kw in keywords)]
        if matching_pkgs:
            matching_pkgs.sort(key=lambda p: p.get("price", 0), reverse=True)
            best_pkg = matching_pkgs[0]
        elif len(store_pkgs) > 1:
            sorted_pkgs = sorted(store_pkgs, key=lambda p: p.get("price", 0), reverse=True)
            best_pkg = sorted_pkgs[0]

    if best_pkg and best_pkg.get("name"):
        complete_edition_name = best_pkg["name"]

    # A. Highest: Complete Edition
    editions.append({
        "id": f"{target_platform}_complete",
        "name": complete_edition_name,
        "short_name": complete_edition_name,
        "platform": target_platform,
        "depot_ids": complete_depots,
        "is_highest": True,
    })

    # B. Additional Store packages
    for pkg in store_pkgs:
        pkg_name = pkg.get("name", "")
        if not pkg_name or pkg_name == complete_edition_name:
            continue
        short_name = pkg_name

        pkg_lower = pkg_name.lower()
        if "standard" in pkg_lower or "base" in pkg_lower:
            continue
        elif "deluxe" in pkg_lower or "complete" in pkg_lower or "ultimate" in pkg_lower or "gold" in pkg_lower:
            pkg_depots = complete_depots
        else:
            pkg_depots = complete_depots

        if not any(e["name"] == pkg_name for e in editions):
            editions.append({
                "id": f"{target_platform}_pkg_{pkg.get('packageid')}",
                "name": pkg_name,
                "short_name": short_name,
                "platform": target_platform,
                "depot_ids": pkg_depots,
                "is_highest": False,
            })

    # C. Lowest: Standard Edition
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
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Computes editions for all available platforms (e.g. {'linux': [...], 'windows': [...]}).
    """
    platforms = get_available_platforms(depots)
    result = {}
    for plat in platforms:
        result[plat] = resolve_edition_depots(app_id, depots, target_platform=plat, language=language)
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
