import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Common AppIDs to ignore (Steamworks redistributables, etc.)
IGNORED_APPIDS = {"228980", "105600"}

_RE_APPID = re.compile(r'"appid"\s+"(\d+)"')
_RE_NAME = re.compile(r'"name"\s+"([^"]+)"')
_RE_INSTALLDIR = re.compile(r'"installdir"\s+"([^"]+)"')
_RE_SIZE = re.compile(r'"SizeOnDisk"\s+"(\d+)"')
_RE_BUILDID = re.compile(r'"buildid"\s+"([^"]+)"')
_RE_LASTUPDATED = re.compile(r'"LastUpdated"\s+"(\d+)"')
_RE_STATEFLAGS = re.compile(r'"StateFlags"\s+"(\d+)"')


def _parse_acf_file(acf_path: Path) -> Optional[Dict[str, Any]]:
    """Parse essential fields from a single appmanifest_*.acf file."""
    try:
        content = acf_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, IOError, PermissionError):
        return None

    # AppID
    m_appid = _RE_APPID.search(content)
    if m_appid:
        appid = m_appid.group(1)
    else:
        # Fallback to parsing from filename
        fname = acf_path.stem  # appmanifest_12345
        appid = fname.replace("appmanifest_", "")

    if not appid or not appid.isdigit() or appid in IGNORED_APPIDS:
        return None

    # StateFlags (4 = fully installed; bitmask 4 must be set)
    m_state = _RE_STATEFLAGS.search(content)
    if m_state:
        state_flags = int(m_state.group(1))
        # 4 is STATE_FLAG_FULLY_INSTALLED; 0 means uninstalled
        if (state_flags & 4) == 0 and state_flags not in (4, 6, 1028):
            return None

    # Name
    m_name = _RE_NAME.search(content)
    game_name = m_name.group(1).strip() if m_name else ""

    # Installdir
    m_dir = _RE_INSTALLDIR.search(content)
    installdir = m_dir.group(1).strip() if m_dir else ""
    if not installdir:
        return None

    if not game_name:
        game_name = installdir

    # Size on disk
    m_size = _RE_SIZE.search(content)
    size_on_disk = int(m_size.group(1)) if m_size else 0

    # Build ID
    m_build = _RE_BUILDID.search(content)
    buildid = m_build.group(1).strip() if m_build else ""

    # Last Updated
    m_updated = _RE_LASTUPDATED.search(content)
    last_updated = int(m_updated.group(1)) if m_updated else 0

    return {
        "appid": appid,
        "game_name": game_name,
        "install_dir": installdir,
        "size_on_disk": size_on_disk,
        "buildid": buildid,
        "last_updated": last_updated,
        "appmanifest_path": str(acf_path),
    }


def scan_acf_files(
    library_paths: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Rapidly scans steamapps/*.acf manifest files across all known Steam libraries.
    Does not traverse game directories. Reads size, build ID, and install info directly from ACF.
    Returns a list of game data dicts ready for display.
    """
    if library_paths is None:
        try:
            from core.steam_helpers import get_steam_libraries

            library_paths = get_steam_libraries() or []
        except Exception as e:
            logger.warning(f"[ACFScanner] Could not get steam libraries: {e}")
            library_paths = []

    # Plugin library for marking AT0-M status
    plugin_games: Dict[str, Any] = {}
    try:
        from utils.plugin_games import get_all_plugin_games

        plugin_games = get_all_plugin_games()
    except Exception:
        pass

    results: List[Dict[str, Any]] = []
    seen_appids: set = set()

    for lib in library_paths:
        lib_path = Path(lib)
        steamapps_path = lib_path / "steamapps"
        if not steamapps_path.is_dir():
            continue

        common_path = steamapps_path / "common"

        try:
            for entry in os.scandir(steamapps_path):
                if not (entry.name.startswith("appmanifest_") and entry.name.endswith(".acf")):
                    continue

                acf_path = Path(entry.path)
                data = _parse_acf_file(acf_path)
                if not data:
                    continue

                appid = data["appid"]
                if appid in seen_appids:
                    continue

                installdir = data["install_dir"]
                install_path = common_path / installdir
                if not install_path.exists():
                    continue

                seen_appids.add(appid)

                # Check if it has an ACCELA marker or is in AT0-M plugin library
                is_atom = appid in plugin_games
                is_accela = False
                for marker in (".ACCELA", ".accela", ".DepotDownloader", ".depotdownloader"):
                    if (install_path / marker).exists():
                        is_accela = True
                        break

                source = "at0-m" if is_atom else ("ACCELA" if is_accela else "Steam")

                game_data = {
                    "appid": appid,
                    "game_name": data["game_name"],
                    "install_dir": installdir,
                    "install_path": str(install_path),
                    "library_path": str(lib),
                    "size_on_disk": data["size_on_disk"],
                    "appmanifest_path": data["appmanifest_path"],
                    "buildid": data["buildid"],
                    "last_updated": data["last_updated"],
                    "source": source,
                    "is_accela_install": is_accela or is_atom,
                    "is_atom": is_atom,
                    "is_vapor": is_atom,
                }
                results.append(game_data)
        except OSError as e:
            logger.warning(f"[ACFScanner] Error scanning {steamapps_path}: {e}")

    logger.info(f"[ACFScanner] Scanned {len(results)} installed Steam games across {len(library_paths)} libraries")
    return results
