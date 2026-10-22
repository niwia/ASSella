"""Finding the game's launch executable, and the depot whose manifest lists it.

Before the dialog accepts a selection it wants to know that the depots ticked
actually contain the file Steam launches. None of that is about widgets: the
name comes from the game record or from Steam's own ``appinfo.vdf`` cache, and
the depot that holds the file is found by searching the manifest bundles on
disk for its name. Those searches live here, Qt-free and testable against
synthetic appinfo caches and manifest bundles; the mixin keeps the warning box
and the row it ticks when the answer is a depot the user did not select.
"""

import logging
import re
import struct
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def launch_executable(app_id, game_data=None, *, paths=None) -> Optional[str]:
    """The launch executable's path, or None when nothing names one.

    The game record wins when it has one; otherwise Steam's ``appinfo.vdf``
    cache is read. That cache is undocumented and may change shape, so a
    failure to read it is a "no executable known" and never an error.
    """
    if isinstance(game_data, dict):
        exe_name = game_data.get("executable") or game_data.get("launch_executable")
        if exe_name:
            return exe_name
    return find_executable_in_appinfo_vdf(app_id, paths)


def executable_filename(exe_name) -> str:
    """The bare, lowercased file name of a launch executable."""
    return Path(str(exe_name or "")).name.lower()


def appinfo_paths() -> List[Path]:
    """Where Steam keeps its appinfo cache, native and Flatpak."""
    return [
        Path.home() / ".local/share/Steam/appcache/appinfo.vdf",
        Path.home() / ".steam/steam/appcache/appinfo.vdf",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam/appcache/appinfo.vdf",
    ]


def find_executable_in_appinfo_vdf(app_id, paths=None) -> Optional[str]:
    """The executable recorded for an app in Steam's appinfo.vdf, or None.

    Entries are a fixed header, a size, and a binary VDF blob; only the
    executable name is picked out, and only from the app asked about.
    """
    try:
        appid_num = int(str(app_id).strip())
    except (TypeError, ValueError):
        return None

    try:
        for path in paths if paths is not None else appinfo_paths():
            path = Path(path)
            if not path.is_file():
                continue
            exe_name = _read_appinfo_app(path, appid_num)
            if exe_name:
                return exe_name
    except Exception as e:
        logger.debug(f"[DepotSelection] appinfo.vdf exe lookup error: {e}")
    return None


def _read_appinfo_app(path: Path, appid_num: int) -> Optional[str]:
    """Walk one appinfo.vdf and return the app's executable name, if listed."""
    with open(path, "rb") as f:
        f.seek(16)
        while True:
            buf = f.read(4)
            if len(buf) < 4:
                return None
            aid = struct.unpack("<I", buf)[0]
            if aid == 0:
                return None
            size = struct.unpack("<I", f.read(4))[0]
            f.seek(48, 1)
            vdf_data = f.read(size - 60)
            if aid == appid_num:
                m = re.search(rb"executable\x00([^\x00]+)\x00", vdf_data)
                if m:
                    return m.group(1).decode("latin1", errors="ignore").replace("\\", "/")


def hubcap_zip_candidates(app_id) -> List[Path]:
    """The manifest bundles a fetch would have left for an app."""
    from utils.helpers import get_base_path

    appid_str = str(app_id)
    return [
        Path(get_base_path()) / "hubcap_manifests" / f"accela_fetch_{appid_str}.zip",
        Path.home() / ".local/share/ACCELA/hubcap_manifests" / f"accela_fetch_{appid_str}.zip",
    ]


def manifest_search_dirs() -> List[Path]:
    """Where loose ``.manifest`` files are kept, ours and Steam's cache."""
    from utils.helpers import get_base_path

    return [
        Path(get_base_path()) / "manifests",
        Path.home() / ".local/share/Steam/steamapps/depotcache",
    ]


def depot_with_executable(app_id, exe_filename, *, zips=None, dirs=None) -> Optional[str]:
    """The id of the depot whose manifest lists this executable, or None.

    The fetched bundle is searched first, then loose manifests; a manifest
    names its depot in the part before the first underscore.
    """
    if not exe_filename:
        return None

    target_bytes = exe_filename.encode("latin1", errors="ignore")

    depot = _depot_in_bundles(
        zips if zips is not None else hubcap_zip_candidates(app_id), target_bytes
    )
    if not depot:
        depot = _depot_in_loose_manifests(
            dirs if dirs is not None else manifest_search_dirs(), target_bytes
        )
    return depot


def _depot_in_bundles(zip_paths, target_bytes: bytes) -> Optional[str]:
    import zipfile

    for z_path in zip_paths:
        z_path = Path(z_path)
        if not z_path.is_file():
            continue
        try:
            with zipfile.ZipFile(z_path, "r") as zf:
                for mf in zf.namelist():
                    if mf.endswith(".manifest"):
                        raw = zf.read(mf)
                        if target_bytes in raw.lower():
                            return mf.split("_")[0]
        except Exception:
            pass
    return None


def _depot_in_loose_manifests(dirs, target_bytes: bytes) -> Optional[str]:
    for m_dir in dirs:
        m_dir = Path(m_dir)
        if not m_dir.is_dir():
            continue
        try:
            for mf in m_dir.glob("*.manifest"):
                parts = mf.stem.split("_")
                if len(parts) >= 1:
                    raw = mf.read_bytes()
                    if target_bytes in raw.lower():
                        return parts[0]
        except Exception:
            pass
    return None
