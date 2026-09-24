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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from utils.helpers import get_base_path
from utils.sls_bridge import SLSBridge
from utils.yaml_config_manager import (
    add_additional_app,
    add_additional_depot,
    add_decryption_key,
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


def get_plugin_game(appid: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Retrieve metadata for a specific plugin-managed game."""
    lib = load_plugin_library()
    return lib.get(str(appid))


def get_all_plugin_games() -> Dict[str, Any]:
    """Return all currently registered plugin-managed games."""
    return load_plugin_library()


def get_game_for_depot(depot_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Find which registered plugin game owns a given depot_id."""
    did_str = str(depot_id).strip()
    lib = load_plugin_library()
    for g in lib.values():
        if did_str in g.get("depots", []) or did_str in g.get("keys", {}):
            return g
    return None


def register_plugin_game(
    appid: Union[str, int],
    name: str,
    depot_ids: List[Union[str, int]],
    decryption_keys: Optional[Dict[Union[str, int], str]] = None,
    installdir: str = "",
    depot_names: Optional[Dict[str, str]] = None,
) -> bool:
    """
    Register a game for plugin / Steam-native download and smartly inject only its
    required AppID, DepotIDs, and AES keys into SLSsteam config.yaml.
    """
    appid_str = str(appid).strip()
    if not appid_str or appid_str in ("0", "N/A", "unknown"):
        logger.warning(f"[PluginGames] Invalid AppID '{appid}' for registration")
        return False

    clean_depots = [str(d).strip() for d in depot_ids if str(d).strip().isdigit()]
    clean_keys = {}
    if decryption_keys:
        for did, key in decryption_keys.items():
            did_str = str(did).strip()
            key_str = str(key).strip().lower()
            if did_str.isdigit() and len(key_str) == 64:
                clean_keys[did_str] = key_str

    clean_depot_names = {}
    if depot_names:
        for did, dname in depot_names.items():
            did_str = str(did).strip()
            if did_str.isdigit() and dname:
                clean_depot_names[did_str] = str(dname).strip()

    lib = load_plugin_library()
    game_record = {
        "appid": appid_str,
        "name": name,
        "installdir": installdir,
        "depots": clean_depots,
        "keys": clean_keys,
        "depot_names": clean_depot_names,
        "updated_at": int(time.time()),
        "source": "plugin_native",
    }
    lib[appid_str] = game_record
    if not save_plugin_library(lib):
        return False

    # Smart injection into SLSsteam config.yaml
    cfg_path = get_user_config_path()
    if cfg_path.exists():
        # 1. Add AppID with game name comment
        add_additional_app(cfg_path, appid_str, comment=name)

        # 2. Add only the required depots with clear comments
        for did in clean_depots:
            d_name = clean_depot_names.get(did, "")
            depot_comment = f"{name} - {d_name} ({did})" if d_name else f"{name} ({did})"
            add_additional_depot(cfg_path, did, comment=depot_comment)

        # 3. Add decryption keys with game comment
        for did, key in clean_keys.items():
            d_name = clean_depot_names.get(did, "")
            key_comment = f"{name} - {d_name}" if d_name else name
            add_decryption_key(cfg_path, did, key, comment=key_comment)

        # 4. Notify bridge / SLSsteam of update
        SLSBridge.notify_reload()

    logger.info(f"[PluginGames] Successfully registered '{name}' ({appid_str}) with {len(clean_depots)} depot(s)")
    return True


def unregister_plugin_game(appid: Union[str, int]) -> bool:
    """
    Unregister a plugin-managed game and clean up its entries in config.yaml.
    Depots and keys shared with other registered games will be safely preserved.
    """
    appid_str = str(appid).strip()
    lib = load_plugin_library()
    if appid_str not in lib:
        logger.debug(f"[PluginGames] AppID '{appid_str}' not in plugin library")
        return False

    target_game = lib.pop(appid_str)
    save_plugin_library(lib)

    # Collect depots and keys still required by remaining games
    remaining_depots: Set[str] = set()
    remaining_keys: Set[str] = set()
    for g in lib.values():
        remaining_depots.update(g.get("depots", []))
        remaining_keys.update(g.get("keys", {}).keys())

    cfg_path = get_user_config_path()
    if cfg_path.exists():
        # 1. Remove from AdditionalApps
        remove_additional_app(cfg_path, appid_str)

        # 2. Remove depots that are not shared with any other registered game
        for did in target_game.get("depots", []):
            if did not in remaining_depots:
                remove_additional_depot(cfg_path, did)

        # 3. Remove decryption keys that are not shared
        for did in target_game.get("keys", {}).keys():
            if did not in remaining_keys:
                remove_decryption_key(cfg_path, did)

        # 4. Notify bridge / SLSsteam of update
        SLSBridge.notify_reload()

    logger.info(f"[PluginGames] Successfully unregistered '{target_game.get('name')}' ({appid_str})")
    return True


def sync_all_plugin_games_to_config() -> None:
    """Ensure all registered plugin games have their AppID, depots, and keys in config.yaml with comments."""
    lib = load_plugin_library()
    if not lib:
        return

    cfg_path = get_user_config_path()
    if not cfg_path.exists():
        return

    modified = False
    for appid_str, game in lib.items():
        name = game.get("name", "")
        depot_names = game.get("depot_names", {})
        if add_additional_app(cfg_path, appid_str, comment=name):
            modified = True
        for did in game.get("depots", []):
            d_name = depot_names.get(did, "")
            depot_comment = f"{name} - {d_name} ({did})" if d_name else f"{name} ({did})"
            if add_additional_depot(cfg_path, did, comment=depot_comment):
                modified = True
        for did, key in game.get("keys", {}).items():
            d_name = depot_names.get(did, "")
            key_comment = f"{name} - {d_name}" if d_name else name
            if add_decryption_key(cfg_path, did, key, comment=key_comment):
                modified = True

    if modified:
        SLSBridge.notify_reload()
        logger.info("[PluginGames] Synced all registered plugin games to config.yaml")
