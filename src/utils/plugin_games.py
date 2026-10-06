"""
Plugin Games Manager Module

Provides high-level management for games installed or downloaded via SLSsteam plugins
(Steam native downloader). Counterpart to psyche's library database (.psyche-library.json),
tracking which AppIDs, DepotIDs, and AES DecryptionKeys belong to which game so they
can be injected smartly without queuing unintended games.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from utils.helpers import get_base_path
from utils.sls_bridge import SLSBridge
from utils.yaml_config_manager import (
    add_additional_app,
    add_additional_depot,
    add_decryption_key,
    batch_config_edit,
    get_user_config_path,
    remove_additional_app,
    remove_additional_depot,
    remove_decryption_key,
)

logger = logging.getLogger(__name__)

PLUGIN_LIBRARY_FILENAME = "plugin_library.json"


def get_plugin_library_path() -> Path:
    """Return the path to ACCELA's plugin_library.json database."""
    from utils.helpers import get_data_file_path
    return get_data_file_path(PLUGIN_LIBRARY_FILENAME)


def load_plugin_library() -> Dict[str, Any]:
    """Load the plugin-managed games library from disk."""
    lib_path = get_plugin_library_path()
    if not lib_path.exists():
        return {}
    try:
        with open(lib_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.error(f"[PluginGames] Failed to read {lib_path}: {e}")
    return {}


def save_plugin_library(data: Dict[str, Any]) -> bool:
    """Save the plugin-managed games library to disk."""
    lib_path = get_plugin_library_path()
    try:
        lib_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = lib_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, lib_path)
        return True
    except Exception as e:
        logger.error(f"[PluginGames] Failed to save {lib_path}: {e}")
        return False


def is_valid_plugin_record(record: Optional[Dict[str, Any]]) -> bool:
    """Check if a plugin library record represents an active AT0-M / plugin-native game."""
    if not record or not isinstance(record, dict):
        return False
    if record.get("is_atom") is False or record.get("is_accela") is True or record.get("mode") == "accela":
        return False
    return True


def get_plugin_game(appid: Union[str, int], active_only: bool = True) -> Optional[Dict[str, Any]]:
    """Retrieve metadata for a specific plugin-managed game."""
    lib = load_plugin_library()
    rec = lib.get(str(appid))
    if active_only and not is_valid_plugin_record(rec):
        return None
    return rec


def is_plugin_game(appid: Union[str, int]) -> bool:
    """Check if an AppID is currently registered as an active AT0-M plugin game."""
    return get_plugin_game(appid, active_only=True) is not None


def get_all_plugin_games(active_only: bool = True) -> Dict[str, Any]:
    """Return all currently registered plugin-managed games."""
    lib = load_plugin_library()
    if not active_only:
        return lib
    return {aid: g for aid, g in lib.items() if is_valid_plugin_record(g)}


def get_game_for_depot(depot_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Find which registered plugin game owns a given depot_id."""
    did_str = str(depot_id).strip()
    lib = load_plugin_library()
    for g in lib.values():
        if did_str in g.get("depots", []) or did_str in g.get("keys", {}):
            return g
    return None


def build_dlc_reverse_map() -> Dict[str, str]:
    """
    Build a reverse mapping {dlc_appid: base_appid} for all DLC-only registered games.
    Used by the library scanner to discover base games when only DLC AppIDs are in
    AdditionalApps (AT0-M DLC-only mode).
    """
    result: Dict[str, str] = {}
    for appid_str, record in load_plugin_library().items():
        for dlc_id in record.get("dlc_appids", []):
            result[str(dlc_id)] = appid_str
    return result


SHARED_REDISTS: Set[str] = {
    "228980", "1034630", "228981", "228982", "228983", "228984", "228985",
    "228986", "228987", "228988", "228989", "228990", "229000", "229001",
    "229002", "229003", "229004", "229005", "229006", "229007", "229010",
    "229011", "229012", "229020", "229030", "229031", "229032"
}


def register_plugin_game(
    appid: Union[str, int],
    name: str,
    depot_ids: List[Union[str, int]],
    decryption_keys: Optional[Dict[Union[str, int], str]] = None,
    installdir: str = "",
    depot_names: Optional[Dict[Union[str, int], str]] = None,
    dlc_appids: Optional[List[Union[str, int]]] = None,
) -> bool:
    """
    Register a newly installed or downloaded game as plugin-managed.
    AppIDs are NEVER added to AdditionalDepots or DecryptionKeys.
    """
    appid_str = str(appid).strip()
    if not appid_str or appid_str in ("0", "N/A", "unknown"):
        logger.warning(f"[PluginGames] Invalid AppID '{appid}' for registration")
        return False

    # Normalise DLC AppIDs (if DLC-only mode was used)
    clean_dlc_appids = [str(d).strip() for d in (dlc_appids or []) if str(d).strip().isdigit()]
    is_dlc_only = bool(clean_dlc_appids)

    # AdditionalDepots: Base AppIDs and DLC AppIDs must NEVER be in clean_depots
    if is_dlc_only:
        from utils.dlc_helpers import filter_dlc_depots_only
        clean_depots = filter_dlc_depots_only(depot_ids, appid_str, dlc_appids=clean_dlc_appids)
    else:
        clean_depots = [
            str(d).strip() for d in depot_ids
            if str(d).strip().isdigit() and str(d).strip() != appid_str and str(d).strip() not in clean_dlc_appids
        ]
    clean_keys = {}
    if decryption_keys:
        for did, key in decryption_keys.items():
            did_str = str(did).strip()
            key_str = str(key).strip().lower()
            if did_str.isdigit() and len(key_str) == 64 and did_str not in clean_dlc_appids:
                clean_keys[did_str] = key_str

    # Safeguard AT0-M mode: Only include depots that have decryption keys (or are shared redists)
    valid_depot_keys = set(clean_keys.keys()) | SHARED_REDISTS
    excluded_unkeyed = [d for d in clean_depots if d not in valid_depot_keys]
    if excluded_unkeyed:
        logger.warning(
            f"[PluginGames] Excluded {len(excluded_unkeyed)} unkeyed depot(s) from AdditionalDepots for {appid_str}: {excluded_unkeyed}"
        )
        clean_depots = [d for d in clean_depots if d in valid_depot_keys]

    clean_depot_names = {}
    if depot_names:
        for did, dname in depot_names.items():
            did_str = str(did).strip()
            if did_str.isdigit() and dname and did_str != appid_str:
                clean_depot_names[did_str] = str(dname).strip()

    lib = load_plugin_library()
    game_record = {
        "appid": appid_str,
        "name": name,
        "installdir": installdir,
        "depots": clean_depots,
        "keys": clean_keys,
        "depot_names": clean_depot_names,
        "dlc_only": is_dlc_only,
        "dlc_appids": clean_dlc_appids,
        "updated_at": int(time.time()),
        "source": "plugin_native",
    }
    lib[appid_str] = game_record
    if not save_plugin_library(lib):
        return False

    # Smart injection into SLSsteam config.yaml
    cfg_path = get_user_config_path()
    if cfg_path.exists():
        with batch_config_edit(cfg_path) as editor:
            if is_dlc_only:
                # DLC-only: add DLC AppIDs to AdditionalApps, NOT the base game appid.
                for dlc_id in clean_dlc_appids:
                    editor.add_app(dlc_id, comment=f"{name} DLC ({dlc_id})")
            else:
                # Normal mode: add base AppID
                editor.add_app(appid_str, comment=name)

            # Add only the required depots with clear comments (never AppIDs!)
            for did in clean_depots:
                if did in SHARED_REDISTS:
                    depot_comment = "Steamworks Shared"
                else:
                    d_name = clean_depot_names.get(did, "")
                    depot_comment = f"{name} - {d_name} ({did})" if d_name else f"{name} ({did})"
                editor.add_depot(did, comment=depot_comment)

            # Add decryption keys with game comment
            for did, key in clean_keys.items():
                if did == appid_str:
                    key_comment = f"{name} [AppKey] ({appid_str})"
                elif did in SHARED_REDISTS:
                    key_comment = "Steamworks Shared"
                else:
                    d_name = clean_depot_names.get(did, "")
                    key_comment = f"{name} - {d_name}" if d_name else name
                editor.add_key(did, key, comment=key_comment)

        # Notify bridge / SLSsteam of update
        SLSBridge.notify_reload()

    mode_str = "DLC-only" if is_dlc_only else "normal"
    logger.info(
        f"[PluginGames] Successfully registered '{name}' ({appid_str}) "
        f"in {mode_str} mode with {len(clean_depots)} depot(s)"
    )
    return True


def unregister_plugin_game(appid: Union[str, int], keep_in_additional_apps: bool = False) -> bool:
    """
    Unregister a plugin-managed game and clean up its entries in config.yaml.
    Depots, keys, and DLC AppIDs shared with other registered games are safely preserved.

    If keep_in_additional_apps is True (e.g. converting from AT0-M to ACCELA mode):
      - The game's AppID is kept in AdditionalApps.
      - Game-specific depots and keys are pruned from AdditionalDepots and DecryptionKeys.
    """
    appid_str = str(appid).strip()
    lib = load_plugin_library()
    if appid_str not in lib:
        logger.debug(f"[PluginGames] AppID '{appid_str}' not in plugin library")
        return False

    target_game = lib.pop(appid_str)
    save_plugin_library(lib)

    # Collect depots, keys, and DLC AppIDs still required by remaining games
    remaining_depots: Set[str] = set()
    remaining_keys: Set[str] = set()
    remaining_dlc_appids: Set[str] = set()
    for g in lib.values():
        remaining_depots.update(g.get("depots", []))
        remaining_keys.update(g.get("keys", {}).keys())
        remaining_dlc_appids.update(g.get("dlc_appids", []))

    cfg_path = get_user_config_path()
    if cfg_path.exists():
        with batch_config_edit(cfg_path) as editor:
            if keep_in_additional_apps:
                # Ensure the game remains in AdditionalApps for ACCELA mode
                if not target_game.get("dlc_only"):
                    editor.add_app(appid_str, comment=target_game.get("name", ""))
                # Prune DLC AppIDs so regular ACCELA mode doesn't leave unnecessary DLC AppIDs
                for dlc_id in target_game.get("dlc_appids", []):
                    if dlc_id not in remaining_dlc_appids:
                        editor.remove_app(dlc_id)
            else:
                # Full unregistration/uninstall: remove from AdditionalApps
                # 1a. Remove DLC AppIDs from AdditionalApps (DLC-only mode)
                for dlc_id in target_game.get("dlc_appids", []):
                    if dlc_id not in remaining_dlc_appids:
                        editor.remove_app(dlc_id)

                # 1b. Remove base AppID from AdditionalApps (normal mode only)
                if not target_game.get("dlc_only"):
                    editor.remove_app(appid_str)

            # 2. Remove depots that are not shared with any other registered game
            for did in target_game.get("depots", []):
                if did not in remaining_depots:
                    editor.remove_depot(did, check_shared=True, excluding_appid=appid_str)

            # 3. Remove decryption keys that are not shared
            for did in target_game.get("keys", {}).keys():
                if did not in remaining_keys:
                    editor.remove_key(did, check_shared=True, excluding_appid=appid_str)

        # 4. Notify bridge / SLSsteam of update
        SLSBridge.notify_reload()

    logger.info(f"[PluginGames] Successfully unregistered '{target_game.get('name')}' ({appid_str}) [keep_apps={keep_in_additional_apps}]")
    return True


def convert_plugin_game_to_accela(appid: Union[str, int]) -> bool:
    """
    Convert a game from AT0-M (plugin mode) to ACCELA Managed mode.
    - AppID is PRESERVED in AdditionalApps (ensuring the game stays unlocked in Steam).
    - Game-specific depots and keys are removed from AdditionalDepots and DecryptionKeys.
    - Shared depots/keys (used by other registered games or common redistributables) are preserved.
    - Unregisters the game from plugin_library.json.
    """
    return unregister_plugin_game(appid, keep_in_additional_apps=True)


def sync_all_plugin_games_to_config() -> None:
    """Ensure all registered plugin games have their AppID, depots, and keys in config.yaml with comments."""
    lib = load_plugin_library()
    if not lib:
        return

    cfg_path = get_user_config_path()
    if not cfg_path.exists():
        return

    with batch_config_edit(cfg_path) as editor:
        for appid_str, game in lib.items():
            name = game.get("name", "")
            depot_names = game.get("depot_names", {})

            if game.get("dlc_only") and game.get("dlc_appids"):
                # DLC-only: sync DLC AppIDs (not base appid) to AdditionalApps
                for dlc_id in game["dlc_appids"]:
                    editor.add_app(dlc_id, comment=f"{name} DLC ({dlc_id})")
            else:
                editor.add_app(appid_str, comment=name)

            for did in game.get("depots", []):
                if str(did) == appid_str:
                    continue  # Never add AppID to AdditionalDepots
                if str(did) in SHARED_REDISTS:
                    depot_comment = "Steamworks Shared"
                else:
                    d_name = depot_names.get(did, "")
                    depot_comment = f"{name} - {d_name} ({did})" if d_name else f"{name} ({did})"
                editor.add_depot(did, comment=depot_comment)

            for did, key in game.get("keys", {}).items():
                did_str = str(did)
                if did_str in game.get("dlc_appids", []):
                    continue  # Never add DLC AppIDs to DecryptionKeys
                if did_str == appid_str:
                    key_comment = f"{name} [AppKey] ({appid_str})"
                elif did_str in SHARED_REDISTS:
                    key_comment = "Steamworks Shared"
                else:
                    d_name = depot_names.get(did_str, "")
                    key_comment = f"{name} - {d_name}" if d_name else name
                editor.add_key(did_str, key, comment=key_comment)

        if editor.has_changes:
            SLSBridge.notify_reload()
            logger.info("[PluginGames] Synced all registered plugin games to config.yaml in a single batch")


def get_atom_game_install_info(appid: Union[str, int]) -> Optional[Dict[str, Any]]:
    """
    Locate where a game is installed (or currently downloading) when managed via AT0-M mode.
    Since download and installation are handled directly by Steam, this inspects:
      1. All Steam library folders from libraryfolders.vdf.
      2. The appmanifest_<appid>.acf file in each library.
      3. The 'installdir' property pointing to steamapps/common/<installdir>.
      4. Staged downloads in steamapps/downloading/<appid>.

    Returns a dict with install paths, state flags, size, build ID, and download state,
    or None if not found in any Steam library.
    """
    aid_str = str(appid).strip()
    if not aid_str or not aid_str.isdigit():
        return None

    try:
        from core.steam_helpers import get_steam_libraries
        libs = get_steam_libraries()
    except Exception:
        libs = []

    fallback_libs = [
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam",
    ]
    for fb in fallback_libs:
        if fb.is_dir() and fb not in libs:
            libs.append(fb)

    for lib_path in libs:
        lib = Path(lib_path)
        steamapps = lib / "steamapps"
        if not steamapps.is_dir():
            continue

        acf_path = steamapps / f"appmanifest_{aid_str}.acf"
        common_dir = steamapps / "common"
        downloading_dir = steamapps / "downloading" / aid_str

        installdir = ""
        game_name = ""
        state_flags = 0
        size_on_disk = 0
        buildid = ""
        bytes_downloaded = 0
        bytes_to_download = 0

        if acf_path.is_file():
            try:
                content = acf_path.read_text(encoding="utf-8", errors="replace")
                m_dir = re.search(r'"installdir"\s+"([^"]+)"', content)
                if m_dir:
                    installdir = m_dir.group(1).strip()
                m_name = re.search(r'"name"\s+"([^"]+)"', content)
                if m_name:
                    game_name = m_name.group(1).strip()
                m_state = re.search(r'"StateFlags"\s+"(\d+)"', content)
                if m_state:
                    state_flags = int(m_state.group(1))
                m_size = re.search(r'"SizeOnDisk"\s+"(\d+)"', content)
                if m_size:
                    size_on_disk = int(m_size.group(1))
                m_build = re.search(r'"buildid"\s+"([^"]+)"', content)
                if m_build:
                    buildid = m_build.group(1).strip()
                m_b_dl = re.search(r'"BytesDownloaded"\s+"(\d+)"', content)
                if m_b_dl:
                    bytes_downloaded = int(m_b_dl.group(1))
                m_b_td = re.search(r'"BytesToDownload"\s+"(\d+)"', content)
                if m_b_td:
                    bytes_to_download = int(m_b_td.group(1))
            except Exception as e:
                logger.debug(f"[PluginGames] Error reading {acf_path}: {e}")

        # If installdir wasn't in ACF, check plugin_library record
        if not installdir:
            record = get_plugin_game(aid_str)
            if record:
                installdir = record.get("installdir") or ""
                if not game_name:
                    game_name = record.get("name") or ""

        target_folder = (common_dir / installdir) if installdir else None
        folder_exists = bool(target_folder and target_folder.is_dir())
        is_dl = bool(downloading_dir.is_dir() or (state_flags & 1024 != 0))
        is_installed = bool((state_flags & 4 != 0) and folder_exists)

        if folder_exists or is_dl or acf_path.is_file():
            resolved_install_path = str(target_folder) if folder_exists else (str(downloading_dir) if downloading_dir.is_dir() else None)
            return {
                "appid": aid_str,
                "game_name": game_name or installdir or aid_str,
                "installed": is_installed,
                "is_downloading": is_dl,
                "install_path": resolved_install_path,
                "common_path": str(target_folder) if target_folder else None,
                "downloading_path": str(downloading_dir) if downloading_dir.is_dir() else None,
                "library_path": str(lib),
                "appmanifest_path": str(acf_path) if acf_path.is_file() else None,
                "installdir": installdir,
                "state_flags": state_flags,
                "size_on_disk": size_on_disk,
                "buildid": buildid,
                "bytes_downloaded": bytes_downloaded,
                "bytes_to_download": bytes_to_download,
            }

    return None


def get_atom_game_install_path(appid: Union[str, int]) -> Optional[str]:
    """Return the absolute filesystem path where the AT0-M game is installed, or None."""
    info = get_atom_game_install_info(appid)
    if info:
        return info.get("install_path")
    return None

