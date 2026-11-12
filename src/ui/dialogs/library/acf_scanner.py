import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Steam installs its own tools next to games, with real appmanifest files and
# installdirs, so a naive glob lists Proton, the runtime containers and
# redistributables as if they were games. Match the names Valve ships - plus
# the unambiguous AppIDs - rather than trying to guess from StateFlags, which
# is identical for games and tools.
_TOOL_APPIDS = {
    "480",            # Spacewar (Valve's public test app)
    "228980",         # Steamworks Common Redistributables
    "1070560",        # Steam Linux Runtime 1.0 (scout)
    "1391110",        # Steam Linux Runtime 2.0 (soldier)
    "1628350",        # Steam Linux Runtime 3.0 (sniper)
    "4183110",        # Steam Linux Runtime 4.0
    "4185400",        # Steam Linux Runtime 4.0 - Arm64
}

_TOOL_NAME_RE = re.compile(
    r"^(proton\b"
    r"|steamlinuxruntime"
    r"|steam linux runtime\b"
    r"|steamworks (common redistributables|shared)\b"
    r"|steamvr\b"
    r"|steam controller configs\b"
    r"|steam audio\b)",
    re.IGNORECASE,
)


def _is_steam_tool(appid: str, game_name: str, installdir: str) -> bool:
    """True for Valve runtime/tool manifests that are not user games."""
    if appid in _TOOL_APPIDS:
        return True
    return bool(
        _TOOL_NAME_RE.match(game_name)
        or _TOOL_NAME_RE.match(installdir)
    )


_RE_APPID = re.compile(r'"appid"\s+"(\d+)"')
_RE_NAME = re.compile(r'"name"\s+"([^"]+)"')
_RE_INSTALLDIR = re.compile(r'"installdir"\s+"([^"]+)"')
_RE_SIZE = re.compile(r'"SizeOnDisk"\s+"(\d+)"')
_RE_BUILDID = re.compile(r'"buildid"\s+"([^"]+)"')
_RE_LASTUPDATED = re.compile(r'"LastUpdated"\s+"(\d+)"')
_RE_STATEFLAGS = re.compile(r'"StateFlags"\s+"(\d+)"')


def _parse_acf_file(
    acf_path: Path,
    *,
    db_manager: Any = None,
    resolve_name: bool = True,
) -> Optional[Dict[str, Any]]:
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

    if not appid or not appid.isdigit() or appid in _TOOL_APPIDS:
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
    raw_name = m_name.group(1).strip() if m_name else ""

    # Installdir
    m_dir = _RE_INSTALLDIR.search(content)
    installdir = m_dir.group(1).strip() if m_dir else ""
    if not installdir:
        return None

    if _is_steam_tool(appid, raw_name or installdir, installdir):
        return None

    game_name = raw_name or installdir
    if resolve_name:
        from utils.library_rules import is_placeholder_game_name, resolve_best_game_name
        if is_placeholder_game_name(raw_name, appid):
            game_name = resolve_best_game_name(
                appid,
                (raw_name,),
                db_manager=db_manager,
                fallback_installdir=installdir,
            )

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
        "raw_acf_name": raw_name,
        "install_dir": installdir,
        "size_on_disk": size_on_disk,
        "buildid": buildid,
        "last_updated": last_updated,
        "appmanifest_path": str(acf_path),
    }


def scan_acf_files(
    library_paths: Optional[List[str]] = None,
    *,
    owned_appids: Optional[set] = None,
    additional_apps: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """
    Scans steamapps/*.acf manifest files for legitimately owned Steam games.
    Excludes ASSella/AT0-M AdditionalApps entries and unowned ACFs, resolves
    placeholder `appid_<id>` names via local DB, and computes disk size when
    ACF SizeOnDisk is 0.
    """
    from utils.library_rules import (
        compute_directory_size,
        is_placeholder_game_name,
        resolve_best_game_name,
    )

    if library_paths is None:
        try:
            from core.steam_helpers import get_steam_libraries

            library_paths = get_steam_libraries() or []
        except Exception as e:
            logger.warning(f"[ACFScanner] Could not get steam libraries: {e}")
            library_paths = []

    if owned_appids is None:
        try:
            from core.steam_helpers import get_owned_steam_appids

            owned_appids = {str(a).strip() for a in (get_owned_steam_appids() or ()) if str(a).strip()}
        except Exception as e:
            logger.debug(f"[ACFScanner] Could not get owned Steam appids: {e}")
            owned_appids = set()

    if additional_apps is None:
        additional_apps = set()
        try:
            from utils.yaml_config_manager import get_user_config_path
            from utils.yaml.apps import get_additional_apps

            cfg_path = get_user_config_path()
            if cfg_path and cfg_path.exists():
                additional_apps = {
                    str(a).strip() for a in (get_additional_apps(cfg_path) or []) if str(a).strip()
                }
        except Exception as e:
            logger.debug(f"[ACFScanner] Could not read AdditionalApps: {e}")

    # Plugin library for marking AT0-M status
    plugin_games: Dict[str, Any] = {}
    try:
        from utils.plugin_games import get_all_plugin_games

        plugin_games = get_all_plugin_games()
    except Exception as e:
        logger.debug(f"[ACFScanner] Could not read plugin library: {e}")

    db_mgr = None
    db_attempted = False
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
                data = _parse_acf_file(acf_path, resolve_name=False)
                if not data:
                    continue

                appid = data["appid"]
                if appid in seen_appids:
                    continue
                # AdditionalApps belongs strictly to ASSella/AT0-M; Steam tab is strictly owned games.
                if appid in additional_apps or appid not in owned_appids:
                    continue

                installdir = data["install_dir"]
                install_path = common_path / installdir
                if not install_path.exists():
                    continue

                seen_appids.add(appid)

                # Check for physical ACCELA marker on disk
                accela_marker_path = None
                for marker in (".assella", ".ASSELLA", ".ACCELA", ".accela", ".DepotDownloader", ".depotdownloader"):
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

                meta_data = None
                meta_json = install_path / ".DepotDownloader" / "metadata.json"
                if meta_json.exists():
                    try:
                        import json
                        meta_data = json.loads(meta_json.read_text(encoding="utf-8"))
                    except Exception as e:
                        logger.debug(f"[ACFScanner] Failed reading {meta_json}: {e}")

                if accela_marker_path and is_valid_plugin:
                    plugin_ts = 0.0
                    try:
                        plugin_ts = float(plugin_record.get("updated_at") or 0)
                    except (ValueError, TypeError):
                        plugin_ts = 0.0

                    marker_ts = 0.0
                    if isinstance(meta_data, dict):
                        try:
                            marker_ts = float(meta_data.get("last_updated") or 0)
                        except (ValueError, TypeError):
                            marker_ts = 0.0
                    if marker_ts == 0:
                        try:
                            marker_ts = accela_marker_path.stat().st_mtime
                        except OSError:
                            marker_ts = 0.0

                    if marker_ts >= plugin_ts:
                        is_atom = False
                        is_accela = True
                    else:
                        is_atom = True
                        is_accela = False
                elif accela_marker_path:
                    is_accela = True
                    is_atom = False
                elif is_valid_plugin:
                    is_atom = True
                    is_accela = False
                else:
                    is_atom = False
                    is_accela = False

                raw_name = data.get("raw_acf_name", "")
                plugin_name = plugin_record.get("name") if isinstance(plugin_record, dict) else None
                if is_placeholder_game_name(raw_name, appid):
                    if not db_attempted:
                        db_attempted = True
                        try:
                            from managers.db_manager import DatabaseManager
                            db_mgr = DatabaseManager()
                        except Exception as e:
                            logger.debug(f"[ACFScanner] DatabaseManager unavailable: {e}")
                    game_name = resolve_best_game_name(
                        appid,
                        (plugin_name, raw_name),
                        db_manager=db_mgr,
                        fallback_installdir=installdir,
                    )
                else:
                    game_name = raw_name

                size_on_disk = int(data.get("size_on_disk") or 0)
                if size_on_disk <= 0 and isinstance(meta_data, dict):
                    try:
                        size_on_disk = int(meta_data.get("size_on_disk") or 0)
                    except (ValueError, TypeError):
                        size_on_disk = 0
                if size_on_disk <= 0:
                    size_on_disk = compute_directory_size(str(install_path))

                source = "at0-m" if is_atom else ("ACCELA" if is_accela else "Steam")

                game_data = {
                    "appid": appid,
                    "game_name": game_name,
                    "install_dir": installdir,
                    "install_path": str(install_path),
                    "library_path": str(lib),
                    "size_on_disk": size_on_disk,
                    "appmanifest_path": data["appmanifest_path"],
                    "buildid": data["buildid"],
                    "last_updated": data["last_updated"],
                    "source": source,
                    "is_accela_install": is_accela or is_atom,
                    "is_atom": is_atom,
                    "is_vapor": is_atom,
                    # True when the AT0-M migration could not adopt the Steam
                    # appmanifest (Steam was running). The game is AT0-M but
                    # still needs the manifest adoption, so it must stay
                    # re-runnable.
                    "acf_pending": bool(plugin_record and plugin_record.get("acf_pending")),
                }
                results.append(game_data)
        except OSError as e:
            logger.warning(f"[ACFScanner] Error scanning {steamapps_path}: {e}")

    logger.info(f"[ACFScanner] Scanned {len(results)} owned Steam games across {len(library_paths)} libraries")
    return results
