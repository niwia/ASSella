"""
Filesystem discovery helpers for Steam games and prefixes.
Pure Python module with no GUI/PyQt dependencies.
"""

import logging
import os
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def find_compatdata_path(
    appid: str,
    install_path: Optional[str] = None,
    library_path: Optional[str] = None,
) -> Optional[str]:
    """Discover the Proton prefix (compatdata) path on disk for a given AppID."""
    if not appid or appid in ("0", "N/A", "unknown"):
        return None

    candidates = []
    if library_path:
        candidates.append(os.path.join(library_path, "steamapps", "compatdata", str(appid)))

    if install_path:
        try:
            p = Path(install_path).resolve()
            for parent in p.parents:
                if parent.name.lower() == "steamapps":
                    candidates.append(str(parent / "compatdata" / str(appid)))
                    break
        except Exception as e:
            logger.debug(f"[GamePaths] Path traversal error checking compatdata: {e}")

    try:
        from core.steam_helpers import get_steam_libraries

        for lib in get_steam_libraries() or []:
            candidates.append(os.path.join(lib, "steamapps", "compatdata", str(appid)))
    except Exception as e:
        logger.debug(f"[GamePaths] Error reading steam libraries for compatdata: {e}")

    for cp in candidates:
        if os.path.exists(cp):
            return cp
    return None


def find_acf_path(appid: str, library_path: Optional[str] = None) -> Optional[str]:
    """Locate the steamapps/appmanifest_{appid}.acf file if it exists."""
    if not appid or appid in ("0", "N/A", "unknown"):
        return None
    try:
        from core.steam_helpers import get_steam_libraries

        libs = list(get_steam_libraries() or [])
        if library_path and library_path not in libs:
            libs.append(library_path)
        for lib in libs:
            p = Path(lib) / "steamapps" / f"appmanifest_{appid}.acf"
            if p.exists():
                return str(p)
    except Exception as e:
        logger.debug(f"[GamePaths] Error finding ACF file: {e}")
    return None


def find_userdata_saves_paths(appid: str, library_path: Optional[str] = None) -> List[str]:
    """Locate local Steam Cloud userdata saves (remote folders) for all users."""
    if not appid or appid in ("0", "N/A", "unknown"):
        return []
    results = []
    try:
        from core.steam_helpers import get_steam_libraries

        libs = list(get_steam_libraries() or [])
        if library_path and library_path not in libs:
            libs.append(library_path)
        for lib in libs:
            userdata_dir = os.path.join(lib, "userdata")
            if os.path.isdir(userdata_dir):
                for uid in os.listdir(userdata_dir):
                    p = os.path.join(userdata_dir, uid, str(appid), "remote")
                    if os.path.exists(p):
                        results.append(p)
    except Exception as e:
        logger.debug(f"[GamePaths] Error finding userdata saves: {e}")
    return results
