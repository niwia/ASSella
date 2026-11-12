"""AT0-M games that are registered but not (fully) downloaded.

AT0-M registers a game in two places when the user adds or starts a download:
``plugin_library.json`` (ASSella's record: depots, keys, DLC, install dir) and
SLSsteam's ``config.yaml`` (AdditionalApps / DecryptionKeys, what Steam reads).
The library scan only finds games with files on disk, so a game that was only
added - or is still queued in Steam - was invisible and could not be removed.

This module lists those games from the plugin library, cross-checked against
config.yaml and Steam's appmanifests, and removes them cleanly.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Steam StateFlags bit 4 = fully installed.
_STATE_FULLY_INSTALLED = 4


def _read_acf(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for m in re.finditer(r'^\s*"(\w+)"\s+"([^"]*)"', f.read(), re.MULTILINE):
                out.setdefault(m.group(1), m.group(2))
    except OSError as _silent_err:
        logger.debug("[atom_pending] _read_acf: ignored %s", _silent_err, exc_info=False)
    return out


def _find_appmanifest(appid: str, libraries: Iterable[str]) -> Tuple[str, str]:
    """(acf_path, library_path) for appid, or ("", "")."""
    for lib in libraries:
        p = os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf")
        if os.path.isfile(p):
            return p, lib
    return "", ""


def _dir_has_files(path: str) -> bool:
    try:
        for _root, _dirs, files in os.walk(path):
            if files:
                return True
    except OSError as _silent_err:
        logger.debug("[atom_pending] _dir_has_files: ignored %s", _silent_err, exc_info=False)
    return False


def _config_state(cfg_path) -> Tuple[set, Dict[str, str]]:
    try:
        from utils.yaml.apps import get_additional_apps
        from utils.yaml.keys import get_decryption_keys

        if not cfg_path.exists():
            return set(), {}
        return set(get_additional_apps(cfg_path)), dict(get_decryption_keys(cfg_path) or {})
    except Exception as e:
        logger.debug(f"[AtomPending] Could not read config.yaml: {e}")
        return set(), {}


def get_pending_atom_games(installed_appids: Iterable[str]) -> List[Dict[str, Any]]:
    """Plugin-library games that the library scan did not find on disk.

    ``pending_state``:
      * ``"queued"`` - Steam has an appmanifest (download added / in progress)
      * ``"added"``  - registered for SLSsteam, Steam has not started it
    ``pending_issues`` lists mapping problems (e.g. missing from AdditionalApps)
    so the user can see a broken registration instead of a silent no-op.
    """
    from core.steam_helpers import get_steam_libraries
    from utils.plugin_games import get_all_plugin_games
    from utils.yaml_config_manager import get_user_config_path

    installed = {str(a) for a in installed_appids}
    try:
        lib = get_all_plugin_games(active_only=True)
    except Exception as e:
        logger.debug(f"[AtomPending] plugin library unreadable: {e}")
        return []
    if not lib:
        return []

    try:
        libraries = [str(p) for p in (get_steam_libraries() or [])]
    except Exception:
        libraries = []
    apps, keys = _config_state(get_user_config_path())

    out: List[Dict[str, Any]] = []
    for appid, rec in lib.items():
        appid = str(appid).strip()
        if not appid.isdigit() or appid in installed or not isinstance(rec, dict):
            continue
        dlc_only = bool(rec.get("dlc_only"))
        raw_rec_name = (rec.get("name") or "").strip()
        installdir = (rec.get("installdir") or "").strip()

        acf_path, acf_lib = _find_appmanifest(appid, libraries)
        acf = _read_acf(acf_path) if acf_path else {}
        installdir = installdir or acf.get("installdir", "")
        from utils.library_rules import resolve_best_game_name
        name = resolve_best_game_name(
            appid,
            (raw_rec_name, acf.get("name")),
            fallback_installdir=installdir,
        )
        install_path = ""
        if acf_lib and installdir:
            install_path = os.path.join(acf_lib, "steamapps", "common", installdir)
        try:
            flags = int(acf.get("StateFlags", "0") or 0)
        except ValueError:
            flags = 0
        if acf_path and flags & _STATE_FULLY_INSTALLED and install_path and _dir_has_files(install_path):
            # Installed, but the scan skipped it (e.g. owned elsewhere). Not ours to show.
            continue

        issues: List[str] = []
        if dlc_only:
            dlcs = [str(d) for d in rec.get("dlc_appids", [])]
            if dlcs and not any(d in apps for d in dlcs):
                issues.append("DLC not in AdditionalApps")
        elif appid not in apps:
            issues.append("not in AdditionalApps")
        depots = [str(d) for d in rec.get("depots", []) if str(d) != appid]
        missing_keys = [d for d in depots if d in (rec.get("keys") or {}) and d not in keys]
        if missing_keys:
            issues.append(f"{len(missing_keys)} key(s) missing from config.yaml")

        try:
            to_dl = int(acf.get("BytesToDownload", "0") or 0)
            done = int(acf.get("BytesDownloaded", "0") or 0)
        except ValueError:
            to_dl = done = 0

        out.append({
            "appid": appid,
            "game_name": name,
            "install_dir": installdir,
            "install_path": install_path,
            "library_path": acf_lib,
            "size_on_disk": 0,
            "source": "at0-m",
            "is_accela_install": False,
            "is_atom": True,
            "is_vapor": True,
            "is_plugin_game": True,
            "plugin_record": rec,
            "appmanifest_path": acf_path,
            "is_dlc_only": dlc_only,
            "update_status": "at0m",
            "atom_pending": True,
            "pending_state": "queued" if acf_path else "added",
            "pending_issues": issues,
            "pending_bytes": (done, to_dl),
            "pending_updated_at": int(rec.get("updated_at") or 0),
            "last_updated": int(rec.get("updated_at") or 0),
        })
    return out


def pending_label(game: Dict[str, Any]) -> Tuple[str, str]:
    """(text, colour) for the library status badge."""
    if game.get("pending_issues"):
        return "AT0-M: Incomplete", "#FFB74D"
    if game.get("pending_state") == "queued":
        done, total = game.get("pending_bytes") or (0, 0)
        if total and done:
            return f"Downloading in Steam · {int(done * 100 / total)}%", "#64B5F6"
        return "Queued in Steam", "#64B5F6"
    return "Added · not downloaded", "#CE93D8"


def remove_pending_atom_game(game: Dict[str, Any]) -> Tuple[bool, str]:
    """Remove a not-downloaded AT0-M game from plugin_library.json and config.yaml.

    Uses the same unregister path as a full AT0-M uninstall, so depots, keys and
    DLC shared with other registered games stay. Steam's appmanifest is removed
    only when no game files exist, so a partial download is never orphaned.
    """
    from utils.plugin_games import unregister_plugin_game

    appid = str(game.get("appid") or "").strip()
    if not appid.isdigit():
        return False, "Unknown AppID"

    install_path = game.get("install_path") or ""
    if install_path and _dir_has_files(install_path):
        return False, ("Steam has already downloaded files for this game. "
                       "Uninstall it from Game Details instead.")

    try:
        unregister_plugin_game(appid)
    except Exception as e:
        logger.warning(f"[AtomPending] unregister {appid} failed: {e}")
        return False, f"Could not update the SLSsteam config: {e}"

    acf = game.get("appmanifest_path") or ""
    if acf and os.path.isfile(acf):
        try:
            os.remove(acf)
            logger.info(f"[AtomPending] Removed appmanifest {acf}")
        except OSError as e:
            logger.warning(f"[AtomPending] Could not remove {acf}: {e}")
    if install_path and os.path.isdir(install_path):
        try:
            for root, dirs, _files in os.walk(install_path, topdown=False):
                for d in dirs:
                    os.rmdir(os.path.join(root, d))
            os.rmdir(install_path)
        except OSError as _silent_err:
            logger.debug("[atom_pending] remove_pending_atom_game: ignored %s", _silent_err, exc_info=False)
    _clear_settings(appid)
    return True, "Removed"


def repair_pending_atom_game(game: Dict[str, Any]) -> Tuple[bool, str]:
    """Write the game's plugin_library record back into config.yaml (AdditionalApps, depots, keys)."""
    from utils.plugin_games import sync_plugin_game_to_config

    appid = str(game.get("appid") or "").strip()
    try:
        ok = sync_plugin_game_to_config(appid)
    except Exception as e:
        logger.warning(f"[AtomPending] repair {appid} failed: {e}")
        return False, f"Could not update the SLSsteam config: {e}"
    return (True, "Repaired") if ok else (False, "This game is no longer in plugin_library.json.")


def _clear_settings(appid: str) -> None:
    try:
        from utils.settings import get_settings

        s = get_settings()
        for k in list(s.allKeys()):
            if k.endswith(f"/{appid}") and k.split("/", 1)[0] in (
                "dlc_only_mode", "pin_build", "installed_buildid", "update_status", "branch",
            ):
                s.remove(k)
    except Exception as _silent_err:
        logger.debug("[atom_pending] _clear_settings: ignored %s", _silent_err, exc_info=False)


def find_pending(appid: str, installed_appids: Iterable[str]) -> Optional[Dict[str, Any]]:
    for g in get_pending_atom_games(installed_appids):
        if g["appid"] == str(appid):
            return g
    return None
