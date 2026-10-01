"""
steam_package_info.py
=====================
Extracts package and depot mapping information directly from Steam's local
appcache (packageinfo.vdf and appinfo.vdf) to determine which depots Steam
normally selects and installs for a given AppID.
"""

import logging
import os
import struct
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Union

logger = logging.getLogger(__name__)

# Module-level cache: {mtime: int, data: Dict[int, Set[str]]}
_PACKAGEINFO_CACHE: Dict[str, Union[float, Dict[int, Set[str]]]] = {
    "mtime": 0.0,
    "path": "",
    "data": {},
}


def get_packageinfo_path() -> Optional[Path]:
    """Find the active Steam packageinfo.vdf on Linux / SteamOS."""
    candidates = [
        Path.home() / ".local/share/Steam/appcache/packageinfo.vdf",
        Path.home() / ".steam/steam/appcache/packageinfo.vdf",
        Path.home() / ".steam/root/appcache/packageinfo.vdf",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam/appcache/packageinfo.vdf",
    ]
    for p in candidates:
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def get_depots_for_app_from_packageinfo(app_id: Union[int, str]) -> Set[str]:
    """
    Parse packageinfo.vdf and extract the set of depot IDs belonging to
    packages that contain the given AppID.
    Results are cached in memory based on the file modification time.
    """
    pkg_path = get_packageinfo_path()
    if not pkg_path:
        return set()

    try:
        aid_int = int(str(app_id).strip())
    except (ValueError, TypeError):
        return set()

    try:
        current_mtime = pkg_path.stat().st_mtime
    except OSError:
        return set()

    cached_mtime = _PACKAGEINFO_CACHE.get("mtime", 0.0)
    cached_path = _PACKAGEINFO_CACHE.get("path", "")
    cached_data: Dict[int, Set[str]] = _PACKAGEINFO_CACHE.get("data", {})

    if str(pkg_path) == cached_path and current_mtime == cached_mtime and cached_data:
        return set(cached_data.get(aid_int, set()))

    # Re-parse packageinfo.vdf
    logger.debug(f"[SteamPackageInfo] Parsing {pkg_path}...")
    new_map: Dict[int, Set[str]] = {}

    try:
        import vdf
        with open(pkg_path, "rb") as f:
            f.seek(8)  # Skip magic (4) + universe (4)
            while True:
                b = f.read(4)
                if not b or len(b) < 4:
                    break
                sub_id = struct.unpack("<I", b)[0]
                if sub_id == 0xFFFFFFFF:
                    break
                # Header per package: sha (20) + change_number (4) + pics_token (8) = 32 bytes
                f.seek(32, 1)
                try:
                    data = vdf.binary_load(f)
                except Exception:
                    break

                root = list(data.values())[0] if data else {}
                appids = list(root.get("appids", {}).values())
                depotids = list(root.get("depotids", {}).values())
                if appids and depotids:
                    depot_str_set = {str(d) for d in depotids}
                    for aid in appids:
                        if aid not in new_map:
                            new_map[aid] = set()
                        new_map[aid].update(depot_str_set)

        _PACKAGEINFO_CACHE["mtime"] = current_mtime
        _PACKAGEINFO_CACHE["path"] = str(pkg_path)
        _PACKAGEINFO_CACHE["data"] = new_map
        logger.debug(f"[SteamPackageInfo] Successfully cached {len(new_map)} apps from packageinfo.vdf")
        return set(new_map.get(aid_int, set()))

    except Exception as e:
        logger.warning(f"[SteamPackageInfo] Failed to parse packageinfo.vdf: {e}")
        return set()


def get_steam_recommended_depots(
    app_id: Union[int, str],
    depots: Dict[Union[str, int], dict],
    target_platform: str = "linux",
    language: str = "english",
) -> List[str]:
    """
    Intelligently detect the depots that Steam would normally install:
    1. Reads packageinfo.vdf to find official depots associated with the app's packages.
    2. Matches the discovered depot IDs against the depots available in this game dialog.
    3. Filters out bonus media (soundtracks, artbooks, etc.) and non-target architectures/languages.
    4. Overrides the old Linux-only autoselection if valid Steam package depots are found.
    5. Falls back to get_smart_default_depots if packageinfo has no entry or yields no match.
    """
    from ui.dialogs.depotselection import (
        get_smart_default_depots,
        _depot_is_macos,
        _depot_is_android,
        is_bonus_or_media_depot,
    )

    if not depots:
        return []

    # 1. Query packageinfo.vdf
    pkg_depots = get_depots_for_app_from_packageinfo(app_id)

    # 2. Check if any package depot matches the game's depots dictionary
    matching_depot_ids = set()
    for d_id in pkg_depots:
        d_key = str(d_id)
        if d_key in depots or (d_key.isdigit() and int(d_key) in depots):
            matching_depot_ids.add(d_key)

    if matching_depot_ids:
        # Filter matching depots through sensible safety checks:
        filtered = []
        has_64 = False
        for did in matching_depot_ids:
            d_data = depots.get(did) or depots.get(int(did) if did.isdigit() else did) or {}
            if not isinstance(d_data, dict):
                continue
            osarch = str(d_data.get("osarch") or "").lower()
            desc = (d_data.get("desc") or "").lower()
            if osarch == "64" or "64-bit" in desc or "x64" in desc or "64 bit" in desc or "[64]" in desc:
                has_64 = True
                break

        for did in matching_depot_ids:
            d_data = depots.get(did) or depots.get(int(did) if did.isdigit() else did) or {}
            if not isinstance(d_data, dict):
                continue
            if _depot_is_macos(d_data) or _depot_is_android(d_data):
                continue
            if is_bonus_or_media_depot(d_data):
                continue
            if has_64:
                osarch = str(d_data.get("osarch") or "").lower()
                desc = (d_data.get("desc") or "").lower()
                is_32 = osarch == "32" or "32-bit" in desc or "x86" in desc or "32 bit" in desc or "[32]" in desc
                if is_32:
                    continue
            filtered.append(did)

        if filtered:
            logger.info(
                f"[SteamPackageInfo] Smart Select detected {len(filtered)} depot(s) from packageinfo: {filtered}"
            )
            return sorted(filtered, key=lambda x: int(x) if x.isdigit() else 0)

    # 3. Fallback: use legacy platform-based smart defaults
    logger.debug(
        f"[SteamPackageInfo] No matching depots in packageinfo for AppID {app_id}. Falling back to default heuristics."
    )
    return get_smart_default_depots(depots, target_platform=target_platform, language=language)
