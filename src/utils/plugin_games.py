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
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from utils.helpers import get_base_path
from utils.sls_bridge import SLSBridge
from utils.yaml_config_manager import (
    SHARED_REDISTS,
    _get_section_bounds,
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


# SHARED_REDISTS is imported above canonically from utils.yaml_config_manager


def build_depot_comment(name: str, depot_id: str, depot_name: str, appid: str) -> str:
    """Build the comment written next to an AdditionalDepots entry.

    The trailing tag carries the *owner* AppID, never the depot id. The game
    details view attributes a depot to a game by looking for the AppID inside
    the comment (see ui/dialogs/game_details/depots_tab.py), so a depot tagged
    with its own id is never matched back to the game it belongs to.
    """
    if depot_name:
        return f"{name} - {depot_name} ({appid})"
    return f"{name} ({appid})"


def build_key_comment(name: str, depot_id: str, depot_name: str, appid: str) -> str:
    """Build the comment written next to a DecryptionKeys entry.

    Same owner-tag rule as the depots: the tag is the parent AppID.
    """
    if str(depot_id) == str(appid):
        return f"{name} [AppKey] ({appid})"
    if depot_name:
        return f"{name} - {depot_name} ({appid})"
    return f"{name} ({appid})"


_OWNER_TAG_RE = re.compile(r"\(\s*(\d+)\s*\)")


def comment_has_owner_tag(comment: str, appid: Union[str, int]) -> bool:
    """True only when ``comment`` carries this game's exact ``(appid)`` owner tag.

    Every depot/key comment written since the migration ends with the parent
    AppID, so ``appid_str in comment`` is not an identity test: it also matches
    another game's tag when one AppID is a prefix of another (220 vs 2200).
    Attributing a depot to the wrong game let unregister delete the other
    game's ManifestIds pins, so the tag is compared as a whole number instead.
    """
    appid_str = str(appid).strip()
    if not appid_str or not comment:
        return False
    return any(m.group(1) == appid_str for m in _OWNER_TAG_RE.finditer(comment))


def register_plugin_game(
    appid: Union[str, int],
    name: str,
    depot_ids: List[Union[str, int]],
    decryption_keys: Optional[Dict[Union[str, int], str]] = None,
    installdir: str = "",
    depot_names: Optional[Dict[Union[str, int], str]] = None,
    dlc_appids: Optional[List[Union[str, int]]] = None,
    is_dlc_only: Optional[bool] = None,
    app_token: Optional[Union[str, int]] = None,
    app_tokens: Optional[Dict[Union[str, int], str]] = None,
) -> bool:
    """
    Register a newly installed or downloaded game as plugin-managed.
    AppIDs are NEVER added to AdditionalDepots or DecryptionKeys.
    """
    appid_str = str(appid).strip()
    if not appid_str or appid_str in ("0", "N/A", "unknown"):
        logger.warning(f"[PluginGames] Invalid AppID '{appid}' for registration")
        return False

    # Entitlements and mode are separate: full games can have DLC AppIDs too.
    clean_dlc_appids = list(dict.fromkeys(
        str(d).strip() for d in (dlc_appids or [])
        if str(d).strip().isdigit() and str(d).strip() != appid_str
    ))
    is_dlc_only = bool(clean_dlc_appids) if is_dlc_only is None else bool(is_dlc_only)

    # AdditionalDepots: Base AppIDs and DLC AppIDs must NEVER be in clean_depots
    if is_dlc_only:
        from utils.dlc_helpers import filter_dlc_depots_only
        clean_depots = filter_dlc_depots_only(depot_ids, appid_str, dlc_appids=clean_dlc_appids)
    else:
        clean_depots = [
            str(d).strip() for d in depot_ids
            if str(d).strip().isdigit() and str(d).strip() != appid_str
        ]
    clean_keys = {}
    if decryption_keys:
        for did, key in decryption_keys.items():
            did_str = str(did).strip()
            key_str = str(key).strip().lower()
            if did_str.isdigit() and len(key_str) == 64:
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

    # Only publish the keys of the depots this game actually mounts, plus the
    # root AppKey. A key for an unmounted depot is dead weight: Steam has
    # nothing to decrypt with it, and its presence in DecryptionKeys makes the
    # config look like the depot is served when it is not mounted at all.
    published_ids = set(clean_depots) | set(clean_dlc_appids) | {appid_str} | set(SHARED_REDISTS)
    published_keys = {did: key for did, key in clean_keys.items() if did in published_ids}

    # Collect AppTokens to inject
    tokens_to_inject: Dict[str, str] = {}
    if app_token:
        tokens_to_inject[appid_str] = str(app_token).strip()
    if app_tokens:
        for aid, tok in app_tokens.items():
            aid_s = str(aid).strip()
            tok_s = str(tok).strip()
            if aid_s and tok_s:
                tokens_to_inject[aid_s] = tok_s

    # If base token not provided, query DepotKeyManager
    if appid_str not in tokens_to_inject:
        try:
            from managers.depot_key_manager import DepotKeyManager
            dkm_tok = DepotKeyManager.get_instance().get_app_token(appid_str)
            if dkm_tok:
                tokens_to_inject[appid_str] = str(dkm_tok).strip()
        except Exception as e:
            logger.debug(f"[PluginGames] Could not query DepotKeyManager for token: {e}")

    # Query DLC tokens in DepotKeyManager if DLC AppIDs are present
    for dlc_id in clean_dlc_appids:
        if dlc_id not in tokens_to_inject:
            try:
                from managers.depot_key_manager import DepotKeyManager
                dlc_tok = DepotKeyManager.get_instance().get_app_token(dlc_id)
                if dlc_tok:
                    tokens_to_inject[dlc_id] = str(dlc_tok).strip()
            except Exception as e:
                logger.debug(f"[PluginGames] Could not query DepotKeyManager for DLC token {dlc_id}: {e}")

    # Query depot tokens in DepotKeyManager for selected depots
    for did in clean_depots:
        if did not in tokens_to_inject and did != appid_str:
            try:
                from managers.depot_key_manager import DepotKeyManager
                depot_tok = DepotKeyManager.get_instance().get_app_token(did)
                if depot_tok:
                    tokens_to_inject[did] = str(depot_tok).strip()
            except Exception as e:
                logger.debug(f"[PluginGames] Could not query DepotKeyManager for depot token {did}: {e}")

    if is_dlc_only:
        allowed_token_ids = set(clean_dlc_appids) | set(clean_depots)
        tokens_to_inject = {aid: tok for aid, tok in tokens_to_inject.items() if aid in allowed_token_ids}
        tokens_to_inject.pop(appid_str, None)

    lib = load_plugin_library()
    game_record = {
        "appid": appid_str,
        "name": name,
        "installdir": installdir,
        "depots": clean_depots,
        "keys": published_keys,
        "depot_names": clean_depot_names,
        "dlc_only": is_dlc_only,
        "dlc_appids": clean_dlc_appids,
        "token": tokens_to_inject.get(appid_str, ""),
        "app_tokens": tokens_to_inject,
        "updated_at": int(time.time()),
        "source": "plugin_native",
    }
    previous_record = lib.get(appid_str)
    lib[appid_str] = game_record
    if not save_plugin_library(lib):
        return False

    # Smart injection into SLSsteam config.yaml
    cfg_path = get_user_config_path()
    if not cfg_path.exists():
        logger.error(
            f"[PluginGames] SLSsteam config not found at {cfg_path} - '{name}' "
            f"({appid_str}) was NOT registered in SLSsteam."
        )
        _rollback_plugin_library_record(appid_str, previous_record)
        return False

    with batch_config_edit(cfg_path) as editor:
        if is_dlc_only:
            # DLC-only: add DLC AppIDs to AdditionalApps, NOT the base game appid.
            for dlc_id in clean_dlc_appids:
                editor.add_app(dlc_id, comment=f"{name} DLC ({dlc_id})")
            editor.remove_app_token(appid_str)
        else:
            # Full game owns its base and discovered DLC entitlements.
            editor.add_app(appid_str, comment=name)
            for dlc_id in clean_dlc_appids:
                editor.add_app(dlc_id, comment=f"[DLC] {name} ({dlc_id})")

        # Add only the required depots with clear comments (never AppIDs!)
        # A depot can be refused by the editor's guards (it is already a
        # registered base AppID, it lives in AdditionalApps, ...). Only a
        # depot that actually ends up mounted may carry a key, otherwise
        # DecryptionKeys claims to serve a depot that is not there.
        mounted_depots: Set[str] = set()
        for did in clean_depots:
            if did in SHARED_REDISTS:
                depot_comment = "Steamworks Shared"
            else:
                depot_comment = build_depot_comment(name, did, clean_depot_names.get(did, ""), appid_str)
            editor.add_depot(did, comment=depot_comment, app_id=appid_str)
            if editor.contains_depot(did):
                mounted_depots.add(did)
            else:
                logger.warning(
                    f"[PluginGames] Depot {did} was not mounted for '{name}' ({appid_str}); "
                    "its decryption key is not published."
                )

        # Add decryption keys with game comment
        publishable = mounted_depots | set(clean_dlc_appids) | {appid_str} | set(SHARED_REDISTS)
        for did, key in published_keys.items():
            if did not in publishable:
                continue
            if did in SHARED_REDISTS:
                key_comment = "Steamworks Shared"
            else:
                key_comment = build_key_comment(name, did, clean_depot_names.get(did, ""), appid_str)
            editor.add_key(did, key, comment=key_comment)

        # Add AppTokens with game comment
        for tok_aid, tok_val in tokens_to_inject.items():
            if is_dlc_only and tok_aid == appid_str:
                continue
            tok_comment = f"[DLC] {name} ({tok_aid})" if tok_aid != appid_str else name
            editor.add_token(tok_aid, tok_val, comment=tok_comment)

    if not editor.committed_successfully:
        logger.error(
            f"[PluginGames] Failed to commit SLSsteam config changes for '{name}' ({appid_str})"
        )
        _rollback_plugin_library_record(appid_str, previous_record)
        return False

    # The library was written before the config edit, so it still claims every
    # requested depot. Trim it to the depots that actually mounted: the
    # library is what the depots view and the re-run path read, and a record
    # that claims a depot the config does not serve is the same lie the
    # DecryptionKeys section used to tell.
    mounted_list = [d for d in clean_depots if d in mounted_depots]
    mounted_keys = {
        did: key for did, key in published_keys.items()
        if did in (mounted_depots | set(clean_dlc_appids) | {appid_str} | set(SHARED_REDISTS))
    }
    if mounted_list != clean_depots or mounted_keys != published_keys:
        lib = load_plugin_library()
        record = lib.get(appid_str)
        if record:
            record["depots"] = mounted_list
            record["keys"] = mounted_keys
            lib[appid_str] = record
            if not save_plugin_library(lib):
                # The config is already committed and correct; only the library
                # record still over-claims. Say so instead of failing the whole
                # registration, which would leave the markers in place.
                logger.warning(
                    f"[PluginGames] Could not record the mounted depot set for '{name}' "
                    f"({appid_str}); the library record may list refused depots."
                )

    # Notify bridge / SLSsteam of update
    SLSBridge.notify_reload()

    mode_str = "DLC-only" if is_dlc_only else "normal"
    logger.info(
        f"[PluginGames] Successfully registered '{name}' ({appid_str}) "
        f"in {mode_str} mode with {len(clean_depots)} depot(s)"
    )
    return True


def _rollback_plugin_library_record(appid_str: str, previous_record: Optional[Dict[str, Any]]) -> None:
    """Undo a plugin library entry that was written for a failed registration.

    A half-registered game is worse than an unregistered one: the library
    claims the game is AT0-M managed while SLSsteam never heard of it.
    """
    lib = load_plugin_library()
    if previous_record is None:
        lib.pop(appid_str, None)
    else:
        lib[appid_str] = previous_record
    save_plugin_library(lib)


def _rollback_plugin_library_records(previous_records: Dict[str, Optional[Dict[str, Any]]]) -> None:
    """Undo a batch of plugin library entries in a single write."""
    lib = load_plugin_library()
    for appid_str, previous_record in previous_records.items():
        if previous_record is None:
            lib.pop(appid_str, None)
        else:
            lib[appid_str] = previous_record
    save_plugin_library(lib)


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
    target_game = lib.pop(appid_str, None)
    if target_game:
        save_plugin_library(lib)
    else:
        target_game = {"appid": appid_str, "name": ""}

    # Collect depots, keys, and DLC AppIDs still required by remaining games
    remaining_depots: Set[str] = set()
    remaining_keys: Set[str] = set()
    remaining_dlc_appids: Set[str] = set()
    for g in lib.values():
        remaining_depots.update(str(d) for d in g.get("depots", []))
        remaining_keys.update(str(k) for k in g.get("keys", {}).keys())
        remaining_dlc_appids.update(str(a) for a in g.get("dlc_appids", []))

    # Discover all candidate depots and keys for the target game
    target_depots: Set[str] = {str(d) for d in target_game.get("depots", [])}
    target_keys: Set[str] = {str(d) for d in target_game.get("keys", {}).keys()}
    target_keys.add(appid_str)  # Root AppKey in DecryptionKeys

    # Also discover depots from local SQLite DB for this appid
    try:
        from managers.depot_key_manager import DepotKeyManager
        dkm = DepotKeyManager.get_instance()
        local_keys = dkm.get_keys_for_app(appid_str) or {}
        for did in local_keys.keys():
            did_str = str(did).strip()
            if did_str:
                target_depots.add(did_str)
                target_keys.add(did_str)
    except Exception:
        pass

    cfg_path = get_user_config_path()
    if cfg_path.exists():
        # Scan config.yaml comments for depots/keys mentioning appid_str or game_name
        try:
            txt = cfg_path.read_text(encoding="utf-8", errors="ignore")
            gname = (target_game.get("name") or "").strip().lower()
            for sec in ("AdditionalDepots", "DecryptionKeys"):
                bounds = _get_section_bounds(txt, sec)
                if not bounds:
                    continue
                for line in txt[bounds[1]:bounds[2]].splitlines():
                    m = re.match(r"^[ \t]*-[ \t]*['\"]?(\d+)['\"]?[ \t]*(?:#[ \t]*(.*))?$", line)
                    if not m:
                        m = re.match(r"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[ \t]*(.*))?$", line)
                    if m:
                        item_id, comment = m.group(1), (m.group(2) or "").lower()
                        if comment_has_owner_tag(comment, appid_str) or (
                            gname and len(gname) > 3 and gname in comment
                        ):
                            if sec == "AdditionalDepots":
                                target_depots.add(item_id)
                            else:
                                target_keys.add(item_id)
        except Exception as e:
            logger.debug(f"[PluginGames] Error scanning config comments: {e}")

        with batch_config_edit(cfg_path) as editor:
            if keep_in_additional_apps:
                if target_game.get("dlc_only"):
                    # DLC-only mode: keep DLC AppIDs in AdditionalApps; NEVER add base AppID!
                    for dlc_id in target_game.get("dlc_appids", []):
                        editor.add_app(dlc_id, comment=f"{target_game.get('name', '')} DLC ({dlc_id})")
                else:
                    # Normal mode: ensure the game remains/is added in AdditionalApps for ACCELA mode
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

                # 1c. Remove FakeAppIds, AppTokens, LaunchOptions, and DlcData
                editor.remove_fake_app_id(appid_str)
                editor.remove_app_token(appid_str)
                editor.remove_launch_option(appid_str)
                editor.remove_dlc(appid_str)
                for dlc_id in target_game.get("dlc_appids", []):
                    editor.remove_fake_app_id(dlc_id)
                    editor.remove_app_token(dlc_id)
                    editor.remove_launch_option(dlc_id)
                for did in target_game.get("depot_ids", []):
                    editor.remove_app_token(did)

                # 1d. Remove secondary sections (GameTitles, ManifestIds, CDKeys, etc.)
                for sec in (
                    "GameTitles",
                    "ManifestIds",
                    "DepotBlacklist",
                    "CDKeys",
                    "FakeOffline",
                    "SubscriptionTimestamps",
                    "DenuvoGames",
                ):
                    editor.remove_secondary_entry(sec, appid_str)
                    for dlc_id in target_game.get("dlc_appids", []):
                        editor.remove_secondary_entry(sec, dlc_id)
                    for did in target_depots:
                        editor.remove_secondary_entry(sec, did)

            # 2. Remove depots that are not shared with any other registered game
            for did in target_depots:
                if did not in remaining_depots:
                    editor.remove_depot(did, check_shared=True, excluding_appid=appid_str)

            # 3. Remove decryption keys that are not shared
            for did in target_keys:
                if did not in remaining_keys:
                    editor.remove_key(did, check_shared=True, excluding_appid=appid_str)

        # 4. Remove any pinned build manifests for this game's depots
        try:
            from core.native_steam.steam_manifest_pinning import remove_manifest_ids
            remove_manifest_ids(cfg_path, list(target_depots))
        except Exception as e:
            logger.debug(f"[PluginGames] Error removing pinned manifests for {appid_str}: {e}")

        # 5. Notify bridge / SLSsteam of update
        SLSBridge.notify_reload()

    game_label = target_game.get("name") or appid_str
    logger.info(f"[PluginGames] Successfully unregistered '{game_label}' ({appid_str}) [keep_apps={keep_in_additional_apps}]")
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
                # A full game can have DLC entitlements independently of its
                # DLC-only mode; restore them before adding dual-identity depots.
                for dlc_id in game.get("dlc_appids", []):
                    editor.add_app(dlc_id, comment=f"[DLC] {name} ({dlc_id})")

            for did in game.get("depots", []):
                if str(did) == appid_str:
                    continue  # Never add AppID to AdditionalDepots
                if str(did) in SHARED_REDISTS:
                    depot_comment = "Steamworks Shared"
                else:
                    d_name = depot_names.get(did, "")
                    depot_comment = build_depot_comment(name, did, d_name, appid_str)
                editor.add_depot(did, comment=depot_comment, app_id=appid_str)

            for did, key in game.get("keys", {}).items():
                did_str = str(did)
                if did_str in SHARED_REDISTS:
                    key_comment = "Steamworks Shared"
                else:
                    d_name = depot_names.get(did_str, "")
                    key_comment = build_key_comment(name, did_str, d_name, appid_str)
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


def collect_game_depots_and_keys(
    appid: Union[str, int],
    game_data: Optional[Dict[str, Any]] = None,
    allow_hubcap_fetch: bool = True,
) -> Tuple[List[str], Dict[str, str], Dict[str, str]]:
    """
    Collect all known depot IDs, decryption keys, and depot names for a game.
    Queries DatabaseManager, local .depot files, SQLite DepotKeyManager,
    cached luas, manifest zips, and falls back to Hubcap API manifest fetch if needed.

    The result is everything that is *known* about the game. Narrowing it to
    the user's depot scope is the caller's decision, so the caller can report
    what it left out.
    """
    from utils.lua_parsing import iter_live_matches, extract_all_tokens, extract_token

    appid_str = str(appid).strip()
    depot_ids: List[str] = []
    decryption_keys: Dict[str, str] = {}
    depot_names: Dict[str, str] = {}

    # 1. DatabaseManager lookup
    try:
        from managers.db_manager import DatabaseManager
        db = DatabaseManager()
        app_info = db.get_app_info(appid_str, bypass_expiration=True)
        if app_info and app_info.get("depots"):
            for did, dinfo in app_info["depots"].items():
                did_str = str(did)
                if did_str not in depot_ids:
                    depot_ids.append(did_str)
                if isinstance(dinfo, dict) and dinfo.get("name"):
                    depot_names[did_str] = str(dinfo["name"])
    except Exception as e:
        logger.debug(f"[PluginGames] DB lookup error for {appid_str}: {e}")

    # 2. Local .depot files
    depot_file = get_base_path() / "depots" / f"{appid_str}.depot"
    if depot_file.exists():
        try:
            with open(depot_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parts = line.strip().split(":")
                    if parts and parts[0].strip().isdigit():
                        did = parts[0].strip()
                        if did not in depot_ids:
                            depot_ids.append(did)
                        if len(parts) > 1 and parts[1].strip() and not parts[1].strip().isdigit():
                            depot_names[did] = parts[1].strip()
        except Exception as e:
            logger.debug(f"[PluginGames] Could not read .depot file for {appid_str}: {e}")

    # 3. Authoritative local SQLite DepotKeyManager
    try:
        from managers.depot_key_manager import DepotKeyManager
        stored_keys = DepotKeyManager.get_instance().get_depot_keys(appid_str)
        for did, key in stored_keys.items():
            did_str = str(did).strip()
            key_str = str(key).strip().lower()
            if key_str and len(key_str) == 64:
                decryption_keys[did_str] = key_str
                if did_str != appid_str and did_str not in depot_ids:
                    depot_ids.append(did_str)
    except Exception as e:
        logger.debug(f"[PluginGames] DepotKeyManager lookup error for {appid_str}: {e}")

    # 4. Extract from cached Lua or manifest zip
    def _extract_keys_and_tokens_from_lua(lua_text: str):
        for m in iter_live_matches(lua_text, r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)'):
            did, key = m.group(1), m.group(2).lower()
            decryption_keys[did] = key
            if did != appid_str and did not in depot_ids:
                depot_ids.append(did)
        try:
            tokens = extract_all_tokens(lua_text)
            if not tokens:
                s_tok = extract_token(lua_text, appid_str)
                if s_tok:
                    tokens[appid_str] = s_tok
            if tokens:
                from managers.depot_key_manager import DepotKeyManager
                dkm = DepotKeyManager.get_instance()
                for aid, tok in tokens.items():
                    dkm.save_app_token(aid, tok)
        except Exception as e:
            logger.debug(f"[PluginGames] Could not save app token for {appid_str}: {e}")

    lua_file = get_base_path() / "cached_luas" / f"{appid_str}.lua"
    if lua_file.exists():
        try:
            with open(lua_file, "r", encoding="utf-8", errors="ignore") as lf:
                _extract_keys_and_tokens_from_lua(lf.read())
        except Exception as e:
            logger.debug(f"[PluginGames] Could not read cached Lua for {appid_str}: {e}")

    zip_files = [
        get_base_path() / "hubcap_manifests" / f"accela_fetch_{appid_str}.zip",
        Path.home() / ".local" / "share" / "ACCELA" / "hubcap_manifests" / f"accela_fetch_{appid_str}.zip",
    ]
    zip_file = next((candidate for candidate in zip_files if candidate.exists()), None)
    if zip_file:
        try:
            with zipfile.ZipFile(zip_file, "r") as zf:
                for name in zf.namelist():
                    if name.endswith(".lua"):
                        _extract_keys_and_tokens_from_lua(zf.read(name).decode("utf-8", errors="ignore"))
        except Exception as e:
            logger.debug(f"[PluginGames] Could not read manifest zip for {appid_str}: {e}")

    # 5. Filter out appid itself from depot_ids
    depot_ids = [d for d in depot_ids if str(d) != appid_str]

    # 7. If any depot is missing keys, attempt emergency Hubcap fetch
    missing_key_depots = [d for d in depot_ids if str(d) not in decryption_keys]
    if missing_key_depots and allow_hubcap_fetch:
        try:
            logger.info(f"[PluginGames] Missing keys for depot(s) {missing_key_depots} of {appid_str}. Fetching from Hubcap...")
            from core import morrenus_api
            zip_res, _ = morrenus_api.download_manifest(appid_str, force_update=True)
            if zip_res and os.path.exists(zip_res):
                fresh_keys = {}
                with zipfile.ZipFile(zip_res, "r") as zf:
                    for name in zf.namelist():
                        if name.endswith(".lua"):
                            lua_txt = zf.read(name).decode("utf-8", errors="ignore")
                            for m in iter_live_matches(lua_txt, r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)'):
                                did_f, k_f = m.group(1), m.group(2).lower()
                                fresh_keys[did_f] = k_f
                if fresh_keys:
                    from managers.depot_key_manager import DepotKeyManager
                    dkm = DepotKeyManager.get_instance()
                    dkm.save_depot_keys(appid_str, fresh_keys)
                    for did_f, k_f in fresh_keys.items():
                        decryption_keys[did_f] = k_f
                        if did_f != appid_str and did_f not in depot_ids:
                            depot_ids.append(did_f)
                    try:
                        fresh_tokens = extract_all_tokens(lua_txt)
                        if not fresh_tokens:
                            s_tok = extract_token(lua_txt, appid_str)
                            if s_tok:
                                fresh_tokens[appid_str] = s_tok
                        for aid, tok in fresh_tokens.items():
                            dkm.save_app_token(aid, tok)
                    except Exception as e:
                        logger.debug(f"[PluginGames] Emergency token save error: {e}")
                    logger.info(f"[PluginGames] Recovered {len(fresh_keys)} fresh key(s) from Hubcap for {appid_str}.")
        except Exception as e:
            logger.warning(f"[PluginGames] Hubcap emergency key recovery failed for {appid_str}: {e}")

    return depot_ids, decryption_keys, depot_names


def _resolve_install_path(game_data: Dict[str, Any]) -> Optional[str]:
    """Resolve the on-disk install folder of a game.

    Callers may not know where the game lives (the depot selection dialog only
    has an appid), and the migration engine owns marker retirement and emulator
    reversion, so the lookup falls back to the Steam libraries by appid instead
    of silently skipping those steps.
    """
    install_path = game_data.get("install_path")
    if install_path and os.path.isdir(install_path):
        return str(install_path)
    if game_data.get("library_path"):
        folder_name = game_data.get("installdir") or game_data.get("game_name")
        if folder_name:
            cand = Path(game_data["library_path"]) / "steamapps" / "common" / folder_name
            if cand.is_dir():
                return str(cand)
    appid_str = str(game_data.get("appid", "")).strip()
    if appid_str.isdigit():
        try:
            info = get_atom_game_install_info(appid_str)
            if info and info.get("install_path"):
                return str(info["install_path"])
        except Exception as e:
            logger.debug(f"[PluginGames] Install path lookup for {appid_str} failed: {e}")
    return None


def _remove_accela_markers(install_path: Optional[str]) -> List[str]:
    """Retire the ASSella/ACCELA marker files from an install folder.

    Returns the marker paths that were actually removed. The marker is what
    identifies a game as ASSella-managed, so it is only retired once the AT0-M
    identity (config.yaml + plugin library) is safely committed. A removal
    that silently failed is not reported as a removal.
    """
    removed: List[str] = []
    if not install_path or not os.path.isdir(install_path):
        return removed
    for marker_name in (".assella", ".ASSELLA", ".ACCELA", ".accela", ".DepotDownloader", ".depotdownloader"):
        m_path = os.path.join(install_path, marker_name)
        if not os.path.exists(m_path):
            continue
        try:
            if os.path.isdir(m_path):
                shutil.rmtree(m_path, ignore_errors=True)
            else:
                os.remove(m_path)
        except Exception as e:
            logger.warning(f"[PluginGames] Marker cleanup failed for {m_path}: {e}")
            continue
        if os.path.exists(m_path):
            logger.warning(f"[PluginGames] Marker still present after cleanup: {m_path}")
            continue
        removed.append(m_path)
    return removed


def revert_goldberg_if_applied(install_path: Optional[str]) -> bool:
    """If Goldberg emulator was applied, restore original .dll.valve / .so.valve backups and clean steam_settings."""
    if not install_path or not os.path.isdir(install_path):
        return True
    try:
        from ui.dialogs.library.actions import LibraryActionsMixin

        if not LibraryActionsMixin._is_goldberg_applied(install_path):
            return True
        restore_failures: List[str] = []
        restored_roots: Set[str] = set()
        failed_roots: Set[str] = set()
        for root, _, files in os.walk(install_path):
            if any(f.lower().endswith((".dll.valve", ".so.valve")) for f in files):
                for fname in files:
                    if fname.lower().endswith((".dll.valve", ".so.valve")):
                        orig_name = fname[:-6]
                        bak_path = os.path.join(root, fname)
                        orig_path = os.path.join(root, orig_name)
                        # os.replace overwrites the (emulated) original in one
                        # step: deleting it first would leave the game with no
                        # binary at all if the restore then failed.
                        try:
                            os.replace(bak_path, orig_path)
                            restored_roots.add(root)
                            logger.info(f"[GoldbergRevert] Restored Goldberg backup {orig_name} in {root}")
                        except Exception as r_err:
                            restore_failures.append(bak_path)
                            failed_roots.add(root)
                            logger.warning(f"[GoldbergRevert] Failed to restore {bak_path}: {r_err}")

        # Only remove Goldberg settings after every binary backup was restored.
        # If a restore fails, retaining these files makes a retry diagnosable.
        cleanup_failures: List[str] = []
        for root in restored_roots - failed_roots:
            st_dir = os.path.join(root, "steam_settings")
            if os.path.isdir(st_dir):
                try:
                    shutil.rmtree(st_dir)
                except Exception as e:
                    cleanup_failures.append(st_dir)
                    logger.warning(f"[GoldbergRevert] Could not remove {st_dir}: {e}")
            aid_txt = os.path.join(root, "steam_appid.txt")
            if os.path.exists(aid_txt):
                try:
                    os.remove(aid_txt)
                except Exception as e:
                    cleanup_failures.append(aid_txt)
                    logger.warning(f"[GoldbergRevert] Could not remove {aid_txt}: {e}")
        if restore_failures:
            logger.warning(f"[GoldbergRevert] {len(restore_failures)} backup(s) could not be restored.")
        return not restore_failures and not cleanup_failures
    except Exception as e:
        logger.warning(f"[GoldbergRevert] Error restoring Goldberg backups: {e}")
        return False


def revert_eos_proxy_if_applied(install_path: Optional[str]) -> bool:
    """If EOS proxy is active or stale, restore the original EOS DLL."""
    if not install_path or not os.path.isdir(install_path):
        return False
    try:
        from utils.eos_detector import EOSDetector

        status = EOSDetector.get_proxy_status(install_path)
        if status in ("active", "stale"):
            removed = EOSDetector.remove_proxy(install_path)
            if removed:
                logger.info(f"[EOSRevert] Removed EOS proxy and restored original DLL in {install_path}")
            return removed
    except Exception as e:
        logger.warning(f"[EOSRevert] Error removing EOS proxy: {e}")
    return False


def detect_migration_complications(game_data: Dict[str, Any], settings: Any = None) -> Dict[str, Any]:
    """Detect if this game has any complications when migrating to AT0-M:
    - Pinned build (buildid, manifest_map)
    - DLC-only mode (mapped DLC AppIDs, DLC depots, DLC keys)
    - EOS proxy active
    - Goldberg emulator active
    """
    appid_str = str(game_data.get("appid", "")).strip()
    from utils.settings import get_settings

    settings_obj = settings or get_settings()

    # 1. Pinned build check
    is_pinned = bool(
        game_data.get("pin_build")
        or game_data.get("_pin_build")
        or game_data.get("is_rollback")
        or (settings_obj and settings_obj.value(f"pin_build/{appid_str}", False, type=bool))
    )
    pinned_info = None
    if is_pinned:
        from core.native_steam.steam_manifest_pinning import resolve_pinned_manifests

        manifest_map = resolve_pinned_manifests(game_data, appid_str)
        build_id = str(
            game_data.get("buildid")
            or (settings_obj.value(f"installed_buildid/{appid_str}", "") if settings_obj else "")
        ).strip()
        pinned_info = {
            "build_id": build_id,
            "manifest_map": manifest_map,
        }

    # 2. DLC-Only check
    from utils.dlc_helpers import get_saved_depot_selection, is_dlc_only_mode, resolve_dlc_mapping_for_selection

    is_dlc = is_dlc_only_mode(appid_str) or bool(game_data.get("is_dlc_only"))
    dlc_info = None
    if is_dlc:
        scope = get_saved_depot_selection(appid_str) or game_data.get("selected_depots") or (
            game_data.get("metadata", {}).get("selected_depots_list")
        )
        selected_dlc_apps, selected_dlc_depots = resolve_dlc_mapping_for_selection(
            appid_str, selected_items=scope, game_data=game_data
        )
        from managers.depot_key_manager import DepotKeyManager

        stored_keys = DepotKeyManager.get_instance().get_depot_keys(appid_str)
        key_depot_ids = [d for d in selected_dlc_depots if d in stored_keys or d in selected_dlc_apps]
        dlc_info = {
            "dlc_appids": list(selected_dlc_apps.keys()),
            "depots": sorted(list(selected_dlc_depots), key=lambda x: int(x) if x.isdigit() else 0),
            "key_depot_ids": sorted(list(key_depot_ids), key=lambda x: int(x) if x.isdigit() else 0),
        }

    # 3. EOS Proxy check
    install_path = _resolve_install_path(game_data)
    eos_active = False
    if install_path and os.path.isdir(install_path):
        try:
            from utils.eos_detector import EOSDetector

            status = EOSDetector.get_proxy_status(install_path)
            eos_active = status in ("active", "stale")
        except Exception as e:
            logger.debug(f"[PluginGames] Could not check EOS proxy status for {appid_str}: {e}")

    # 4. Goldberg check
    goldberg_active = False
    if install_path and os.path.isdir(install_path):
        try:
            from ui.dialogs.library.actions import LibraryActionsMixin

            goldberg_active = LibraryActionsMixin._is_goldberg_applied(install_path)
        except Exception as e:
            logger.debug(f"[PluginGames] Could not check Goldberg status for {appid_str}: {e}")

    has_complications = bool(pinned_info or dlc_info or eos_active or goldberg_active)
    return {
        "has_complications": has_complications,
        "pinned": pinned_info,
        "dlc_only": dlc_info,
        "eos": eos_active,
        "goldberg": goldberg_active,
    }


def can_use_atom_mode(config_path: Optional[Path] = None) -> Tuple[bool, str]:
    """Check whether the environment is ready for AT0-M mode.

    Verifies:
      1. SLSsteam config.yaml exists.
      2. Plugins: yes is enabled in config.yaml.
      3. AdditionalDepots section exists in config.yaml.
      4. DecryptionKeys section exists in config.yaml.
      5. An active interceptor plugin (e.g. download.lua or SpaceBunny) is deployed.

    Returns:
      (True, "") if ready, or (False, "Reason...") if not.
    """
    from utils.yaml.core import _get_section_bounds
    from utils.yaml.yaml_values import get_yaml_boolean_value
    from utils.plugin_manager import get_active_interceptor_filename, is_plugin_check_bypassed
    from utils.yaml.plugins import ensure_sls_plugin_config_sections

    cfg_path = config_path if config_path is not None else get_user_config_path()
    if not cfg_path.exists():
        return False, f"SLSsteam config not found at {cfg_path}"

    # Auto-seed required sections if config exists
    ensure_sls_plugin_config_sections(cfg_path)

    if not get_yaml_boolean_value(cfg_path, "Plugins", default=False):
        return False, "Plugins are not enabled in SLSsteam config ('Plugins: yes' missing or false)"

    try:
        content = cfg_path.read_text(encoding="utf-8")
        if _get_section_bounds(content, "AdditionalDepots") is None:
            return False, "Missing 'AdditionalDepots:' section in SLSsteam config"
        if _get_section_bounds(content, "DecryptionKeys") is None:
            return False, "Missing 'DecryptionKeys:' section in SLSsteam config"
    except Exception as e:
        return False, f"Could not read SLSsteam config: {e}"

    if not is_plugin_check_bypassed():
        interceptor = get_active_interceptor_filename()
        if not interceptor:
            return False, "No active download interceptor plugin (e.g. download.lua or SpaceBunny) is deployed"

    return True, ""


def migrate_game_to_atom(
    game_data: Dict[str, Any],
    settings: Any = None,
    auto_revert_goldberg: bool = True,
    auto_revert_eos: bool = True,
) -> Dict[str, Any]:
    """
    Core single-game migration engine: converts an ASSella-managed game to AT0-M (Steam Native) mode.
    Handles:
    - DLC-only mapped entitlements (only user-selected DLC depots, DLC AppIDs, DLC keys)
    - Full game entitlements (all depots and keys from Hubcap bundle without smart depot filtering)
    - Reverting Goldberg emulator to genuine Steam binaries
    - Reverting EOS proxy to genuine DLL
    - In-place atomic config.yaml updates (preserving inode)
    - Removal of stale appmanifest ACF without calling SLSsteam install API pipe
    - Depotcache manifest pre-seeding
    - Removal of .DepotDownloader / .ACCELA markers
    """
    from utils.settings import get_settings

    settings_obj = settings or get_settings()

    appid_str = str(game_data.get("appid", "")).strip()
    if not appid_str or appid_str in ("0", "N/A", "unknown"):
        return {"success": False, "error": f"Invalid AppID '{appid_str}'"}

    game_name = game_data.get("game_name") or f"App {appid_str}"
    installdir = game_data.get("installdir") or game_data.get("game_name") or ""
    install_path = _resolve_install_path(game_data)

    # 1. Goldberg & EOS Reversion (undo before Steam inspects/adopts files)
    if auto_revert_goldberg and install_path:
        if not revert_goldberg_if_applied(install_path):
            return {
                "success": False,
                "error": "Goldberg files could not be fully restored; the AT0-M migration was stopped and ASSella markers were kept.",
            }
    if auto_revert_eos and install_path:
        revert_eos_proxy_if_applied(install_path)

    # 2. Check DLC-only mode
    from utils.dlc_helpers import get_saved_depot_selection, is_dlc_only_mode, resolve_dlc_mapping_for_selection

    is_dlc = is_dlc_only_mode(appid_str) or bool(game_data.get("is_dlc_only"))

    if is_dlc:
        explicit_scope = game_data.get("selected_depots")
        scope = explicit_scope if explicit_scope is not None else (
            get_saved_depot_selection(appid_str)
            or game_data.get("metadata", {}).get("selected_depots_list")
        )
        selected_dlc_apps, selected_dlc_depots = resolve_dlc_mapping_for_selection(
            appid_str, selected_items=scope, game_data=game_data
        )
        dlc_appids = [str(a) for a in selected_dlc_apps.keys() if str(a) != appid_str]
        depot_ids = [str(d) for d in selected_dlc_depots if str(d) != appid_str and str(d) not in dlc_appids]

        # Collect keys for DLC depots
        all_depots, all_keys, all_dnames = collect_game_depots_and_keys(
            appid_str, game_data=game_data, allow_hubcap_fetch=True
        )
        depot_names = {d: all_dnames.get(d, "") for d in depot_ids}
        decryption_keys = {
            d: k for d, k in all_keys.items()
            if (d in depot_ids or d in dlc_appids or d == appid_str)
        }

        # Emergency fetch if any DLC depot lacks a key
        missing_keys = [d for d in depot_ids if d not in decryption_keys]
        if missing_keys:
            try:
                from core import morrenus_api

                zip_res, _ = morrenus_api.download_manifest(appid_str, force_update=True)
                if zip_res and os.path.exists(zip_res):
                    fresh_keys = {}
                    with zipfile.ZipFile(zip_res, "r") as zf:
                        for name in zf.namelist():
                            if name.endswith(".lua"):
                                lua_txt = zf.read(name).decode("utf-8", errors="ignore")
                                for m in re.finditer(r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)', lua_txt):
                                    did_f, k_f = m.group(1), m.group(2).lower()
                                    fresh_keys[did_f] = k_f
                    if fresh_keys:
                        from managers.depot_key_manager import DepotKeyManager

                        DepotKeyManager.get_instance().save_depot_keys(appid_str, fresh_keys)
                        for d_f, k_f in fresh_keys.items():
                            if d_f in depot_ids or d_f in dlc_appids or d_f == appid_str:
                                decryption_keys[d_f] = k_f
            except Exception as e:
                logger.warning(f"[PluginGames] DLC emergency key fetch failed: {e}")
    else:
        # Full game: inject all depots and keys from Hubcap bundle without smart depot filtering
        dlc_appids = []
        depot_ids, decryption_keys, depot_names = collect_game_depots_and_keys(
            appid_str, game_data=game_data, allow_hubcap_fetch=True
        )


    # 3. Guard against unkeyed depots (prevent DepotWithoutKey / partial migration)
    valid_keys_and_redists = set(decryption_keys.keys()) | SHARED_REDISTS
    excluded_unkeyed = [d for d in depot_ids if d not in valid_keys_and_redists]
    if excluded_unkeyed:
        logger.error(
            f"[PluginGames] Cannot migrate '{game_name}' ({appid_str}) to AT0-M: "
            f"Missing decryption keys for {len(excluded_unkeyed)} depot(s): {excluded_unkeyed}. "
            "Migration aborted to prevent partial installation."
        )
        err_msg = (
            "No valid decryption keys found"
            if not decryption_keys
            else f"Missing decryption keys for depot(s): {', '.join(excluded_unkeyed)}"
        )
        return {
            "success": False,
            "error": err_msg,
            "appid": appid_str,
            "name": game_name,
        }

    if not depot_ids and not dlc_appids:
        logger.warning(
            f"[PluginGames] Skipping '{game_name}' ({appid_str}): 0 valid decryption keys or depots available."
        )
        return {"success": False, "error": "No valid decryption keys or depots found"}

    # 4. Config pre-check and registration into SLSsteam config.yaml and plugin_library.json
    cfg_path = get_user_config_path()
    if not cfg_path.exists():
        logger.error(
            f"[PluginGames] SLSsteam config not found at {cfg_path}; aborting AT0-M migration."
        )
        return {
            "success": False,
            "error": f"SLSsteam config.yaml not found at {cfg_path} - migration aborted, nothing was changed",
        }

    registered = register_plugin_game(
        appid=appid_str,
        name=game_name,
        depot_ids=depot_ids,
        decryption_keys=decryption_keys,
        installdir=installdir,
        depot_names=depot_names,
        dlc_appids=dlc_appids if is_dlc else None,
        is_dlc_only=is_dlc,
        app_token=game_data.get("app_token") or game_data.get("token"),
    )
    if not registered:
        return {
            "success": False,
            "error": "Failed to write SLSsteam config.yaml - migration aborted, nothing was changed",
        }

    # 4b. Write pinned build manifest overrides if applicable
    is_pinned = bool(
        game_data.get("pin_build")
        or game_data.get("_pin_build")
        or game_data.get("is_rollback")
        or (settings_obj and settings_obj.value(f"pin_build/{appid_str}", False, type=bool))
    )
    if is_pinned:
        from core.native_steam.steam_manifest_pinning import resolve_pinned_manifests, set_manifest_ids

        pinned_manifests = resolve_pinned_manifests(game_data, appid_str)
        if pinned_manifests:
            try:
                set_manifest_ids(cfg_path, pinned_manifests, comment=f"{game_name} Pinned Build")
            except Exception as e:
                logger.warning(f"[PluginGames] Failed to write pinned manifest IDs for {appid_str}: {e}")

    # 5. Update QSettings
    settings_obj.setValue(f"exclude_from_update_all/{appid_str}", True)

    # 7. Adopt Steam appmanifest ACF
    from utils.steam_acf import find_appmanifest_path

    # The ACF may only advertise the depots registration actually mounted: a
    # depot the config editor refused is not served by SLSsteam, and listing it
    # in the appmanifest would make Steam look for content that has no key.
    record = load_plugin_library().get(appid_str) or {}
    candidate_depots = (
        record.get("depots", depot_ids) if "depots" in record else depot_ids
    )
    candidate_keys = (
        record.get("keys", decryption_keys) if "keys" in record else decryption_keys
    )
    registered_dlc_apps = {str(d) for d in record.get("dlc_appids", [])}
    allowed_key_ids = (
        {str(d) for d in candidate_depots}
        | registered_dlc_apps
        | {appid_str}
        | SHARED_REDISTS
    )
    try:
        with batch_config_edit(cfg_path) as editor:
            adopted_depots = [
                str(d) for d in candidate_depots if editor.contains_depot(d)
            ]
            adopted_keys = {
                str(d): key
                for d, key in candidate_keys.items()
                if str(d) in allowed_key_ids and editor.contains_key(d)
            }
    except Exception as e:
        logger.exception(
            f"[PluginGames] Could not verify mounted depots/keys for {appid_str}: {e}; "
            "falling back to known depots for ACF adoption."
        )
        # Even if config verification failed, try with known depots — the ACF
        # repair logic will discover missing depots from disk anyway.
        adopted_depots = [str(d) for d in candidate_depots if str(d) != appid_str]
        adopted_keys = dict(candidate_keys)

    # If config verification passed but all depots were filtered out
    # (e.g. depot IDs don't match what SLSsteam expects), fall back to
    # known depots so the ACF can still be repaired.
    if not adopted_depots and candidate_depots:
        logger.warning(
            f"[PluginGames] No depots passed config verification for {appid_str}; "
            f"using {len(candidate_depots)} known depot(s) as fallback for ACF repair."
        )
        adopted_depots = [str(d) for d in candidate_depots if str(d) != appid_str]
        adopted_keys = dict(candidate_keys)

    acf_path = find_appmanifest_path(game_data, appid_str)
    acf_updated = False
    if acf_path and os.path.exists(acf_path):
        # Update StateFlags to 6 (UpdateRequired | FullyInstalled) instead of deleting.
        # This signals to Steam that files exist on disk but an update/verification pass is required.
        try:
            from utils.steam_acf import set_acf_state_flags

            if set_acf_state_flags(acf_path, 6):
                acf_updated = True
                logger.info(
                    f"[PluginGames] Set StateFlags=6 on ACF {acf_path} for {appid_str}; "
                    "Steam will see update available and verify/adopt files."
                )
            else:
                logger.warning(
                    f"[PluginGames] Could not update StateFlags on ACF {acf_path}"
                )
        except Exception as e:
            logger.warning(
                f"[PluginGames] Error updating StateFlags on ACF {acf_path}: {e}"
            )

    # 6. Sync manifests to central depotcache
    try:
        from core.native_steam.native_steam_handoff import sync_manifests_to_depotcache

        dest_lib = game_data.get("library_path") or install_path or ""
        sync_manifests_to_depotcache(appid_str, dest_lib)
    except Exception as e:
        logger.warning(f"[PluginGames] Depotcache sync failed for {appid_str}: {e}")

    # 7. Retire ASSella markers
    marker_retained = False
    marker_retained_reasons: List[str] = []

    if not marker_retained:
        _remove_accela_markers(install_path)

    # Explicitly DO NOT trigger delayed install API pipe! User will restart Steam.
    return {
        "success": True,
        "appid": appid_str,
        "name": game_name,
        "acf_updated": acf_updated,
        "acf_deleted": False,
        "marker_retained": marker_retained,
        "marker_retained_reason": "; ".join(marker_retained_reasons),
        "depots_count": len(adopted_depots),
        "keys_count": len(adopted_keys),
        "dlc_only": is_dlc,
    }


def batch_convert_games_to_atom(
    games: List[Dict[str, Any]],
    progress_callback: Optional[Callable[[int, int, str, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """
    Batch convert multiple eligible games from ASSella to AT0-M (Steam Native) mode.
    Executes per-game migration sequentially using the single-game migration engine.
    """
    from utils.settings import get_settings

    settings = get_settings()

    migrated: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    markers_retained: List[Dict[str, Any]] = []
    total = len(games)

    for idx, game_data in enumerate(games):
        if cancel_check and cancel_check():
            logger.info("[PluginGames] Batch AT0-M conversion canceled by user.")
            break

        step_num = idx + 1
        appid_str = str(game_data.get("appid", "")).strip()
        game_name = game_data.get("game_name") or f"App {appid_str}"

        if progress_callback:
            progress_callback(step_num, total, game_name, "Migrating to AT0-M mode...")

        res = migrate_game_to_atom(
            game_data,
            settings=settings,
            auto_revert_goldberg=True,
            auto_revert_eos=True,
        )

        if res.get("success"):
            migrated.append({
                "appid": appid_str,
                "name": game_name,
                "depots": res.get("depots_count", 0),
                "keys": res.get("keys_count", 0),
            })
            if res.get("marker_retained"):
                markers_retained.append({
                    "appid": appid_str,
                    "name": game_name,
                    "reason": res.get("marker_retained_reason")
                    or "migration state could not be fully saved - marker kept for a retry",
                })
        else:
            failed.append({
                "appid": appid_str,
                "name": game_name,
                "reason": res.get("error", "Migration failed"),
            })

    result: Dict[str, Any] = {"migrated": migrated, "failed": failed}
    if markers_retained:
        result["markers_retained"] = markers_retained
    return result
