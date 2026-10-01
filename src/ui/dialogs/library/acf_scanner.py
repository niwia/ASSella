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

                # Check for physical ACCELA marker on disk
                accela_marker_path = None
                for marker in (".ACCELA", ".accela", ".DepotDownloader", ".depotdownloader"):
                    cand = install_path / marker
                    if cand.exists():
                        accela_marker_path = cand
                        break

                plugin_record = plugin_games.get(appid)
                is_valid_plugin = bool(
                    plugin_record
                    and plugin_record.get("is_atom") is not False
                    and plugin_record.get("mode") != "accela"
                    and plugin_record.get("is_accela") is not True
                )

                if accela_marker_path and is_valid_plugin:
                    # Conflict: both ACCELA marker and plugin record exist.
                    # Compare timestamps to determine the most recent installation method.
                    plugin_ts = 0
                    try:
                        plugin_ts = float(plugin_record.get("updated_at") or 0)
                    except (ValueError, TypeError):
                        plugin_ts = 0

                    marker_ts = 0
                    meta_json = install_path / ".DepotDownloader" / "metadata.json"
                    if meta_json.exists():
                        try:
                            import json
                            meta_data = json.loads(meta_json.read_text(encoding="utf-8"))
                            marker_ts = float(meta_data.get("last_updated") or 0)
                        except Exception:
                            pass
                    if marker_ts == 0:
                        try:
                            marker_ts = accela_marker_path.stat().st_mtime
                        except OSError:
                            marker_ts = 0

                    if marker_ts >= plugin_ts:
                        # Reinstalled via ASSella more recently -> ASSella wins, purge stale plugin record
                        try:
                            from utils.plugin_games import convert_plugin_game_to_accela
                            convert_plugin_game_to_accela(appid)
                        except Exception:
                            pass
                        is_atom = False
                        is_accela = True
                    else:
                        # Reinstalled via AT0-M more recently -> AT0-M wins, purge legacy marker
                        try:
                            import shutil
                            if accela_marker_path.is_dir():
                                shutil.rmtree(accela_marker_path, ignore_errors=True)
                            else:
                                accela_marker_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        is_atom = True
                        is_accela = False
                elif accela_marker_path:
                    is_accela = True
                    is_atom = False
                    if plugin_record and not is_valid_plugin:
                        try:
                            from utils.plugin_games import convert_plugin_game_to_accela
                            convert_plugin_game_to_accela(appid)
                        except Exception:
                            pass
                elif is_valid_plugin:
                    is_atom = True
                    is_accela = False
                else:
                    is_atom = False
                    is_accela = False

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
