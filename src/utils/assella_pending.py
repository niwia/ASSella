"""ASSella games registered in SLSsteam AdditionalApps but not downloaded on disk.

Cross-checks SLSsteam config.yaml AdditionalApps against:
  1. Installed games on disk (from library scan)
  2. AT0-M plugin library records (plugin_library.json)
  3. DLC-only configured games

Provides detection and clean single-click unregister/purge functionality.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _extract_additional_apps_with_metadata(cfg_path) -> Tuple[List[str], Dict[str, str], Dict[str, str]]:
    """Parse AdditionalApps items, inline comments, and GameTitles mapping from config.yaml.

    Returns:
      (appids_list, inline_comments_dict, game_titles_dict)
    """
    appids: List[str] = []
    inline_comments: Dict[str, str] = {}
    game_titles: Dict[str, str] = {}

    if not cfg_path.exists():
        return appids, inline_comments, game_titles

    try:
        content = cfg_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        logger.debug(f"[AssellaPending] Could not read config.yaml: {e}")
        return appids, inline_comments, game_titles

    from utils.yaml.core import _get_section_bounds

    # 1. Parse AdditionalApps
    bounds = _get_section_bounds(content, "AdditionalApps")
    if bounds:
        _, start, end = bounds
        sec = content[start:end]
        for line in sec.splitlines():
            m = re.match(r"^[ \t]*-[ \t]*([0-9]+)(?:[ \t]*#[ \t]*(.*))?", line)
            if m:
                aid = m.group(1).strip()
                comment = (m.group(2) or "").strip()
                appids.append(aid)
                if comment:
                    inline_comments[aid] = comment

    # 2. Parse GameTitles section
    gt_bounds = _get_section_bounds(content, "GameTitles")
    if gt_bounds:
        _, gt_start, gt_end = gt_bounds
        gt_sec = content[gt_start:gt_end]
        for line in gt_sec.splitlines():
            m = re.match(r"^[ \t]*([0-9]+)[ \t]*:[ \t]*[\"']?([^\"'#\r\n]+)[\"']?", line)
            if m:
                aid = m.group(1).strip()
                title = m.group(2).strip()
                if title:
                    game_titles[aid] = title

    return appids, inline_comments, game_titles


def get_pending_assella_games(installed_appids: Iterable[str]) -> List[Dict[str, Any]]:
    """Return games listed in AdditionalApps that are not downloaded and not AT0-M/DLC-only."""
    from utils.yaml_config_manager import get_user_config_path
    from utils.plugin_games import get_all_plugin_games
    from utils.dlc_helpers import is_dlc_only_mode
    from utils.library_rules import resolve_best_game_name

    cfg_path = get_user_config_path()
    if not cfg_path.exists():
        return []

    installed = {str(a).strip() for a in installed_appids if str(a).strip()}

    try:
        plugin_lib = get_all_plugin_games(active_only=True) or {}
    except Exception as e:
        logger.debug(f"[AssellaPending] Could not load plugin library: {e}")
        plugin_lib = {}

    plugin_appids = {str(a).strip() for a in plugin_lib.keys()}

    appids, comments, titles = _extract_additional_apps_with_metadata(cfg_path)
    if not appids:
        return []

    out: List[Dict[str, Any]] = []
    seen: set = set()

    for appid in appids:
        if appid in seen or appid in installed or appid in plugin_appids:
            continue
        if is_dlc_only_mode(appid):
            continue

        seen.add(appid)

        # Name resolution: inline comment -> GameTitles -> resolve_best_game_name
        cand_comment = comments.get(appid)
        cand_title = titles.get(appid)
        candidates = [c for c in (cand_comment, cand_title) if c]

        name = resolve_best_game_name(appid, candidates=candidates)

        out.append({
            "appid": appid,
            "game_name": name,
            "install_dir": "",
            "install_path": "",
            "library_path": "",
            "size_on_disk": 0,
            "source": "ACCELA",
            "is_accela_install": True,
            "is_atom": False,
            "is_vapor": False,
            "is_plugin_game": False,
            "is_dlc_only": False,
            "assella_pending": True,
            "pending_state": "added",
            "update_status": "not_downloaded",
            "last_updated": 0,
        })

    return out


def remove_pending_assella_game(game: Dict[str, Any]) -> Tuple[bool, str]:
    """Cleanly purge an uninstalled ASSella game from SLSsteam API pipe, config.yaml, and settings."""
    appid = str(game.get("appid") or "").strip()
    if not appid.isdigit():
        return False, "Unknown AppID"

    # 1. Send uninstall pipe command to SLSsteam if available
    try:
        from core.steam_helpers import slssteam_api_send
        slssteam_api_send(f"uninstall|{appid}")
    except Exception as e:
        logger.debug(f"[AssellaPending] SLSsteam pipe uninstall error: {e}")

    # 2. Discover associated DLC AppIDs or Depots from config.yaml
    target_ids = {appid}
    from utils.yaml_config_manager import get_user_config_path, get_dlc_data, batch_config_edit

    cfg_p = get_user_config_path()
    if cfg_p.exists():
        try:
            sls_dlcs = get_dlc_data(cfg_p, appid)
            if sls_dlcs:
                for d_id in sls_dlcs.keys():
                    if str(d_id).strip().isdigit():
                        target_ids.add(str(d_id).strip())
        except Exception as e:
            logger.debug(f"[AssellaPending] Could not read DlcData for {appid}: {e}")

        try:
            with batch_config_edit(cfg_p) as editor:
                for tid in target_ids:
                    editor.remove_app(tid)
                    editor.remove_depot(tid)
                    editor.remove_key(tid)
                    editor.remove_fake_app_id(tid)
                    editor.remove_app_token(tid)
                    editor.remove_launch_option(tid)
                    editor.remove_secondary_entry("GameTitles", tid)
                    editor.remove_secondary_entry("ManifestIds", tid)
                    editor.remove_secondary_entry("SubscriptionTimestamps", tid)
                editor.remove_dlc(appid)
        except Exception as e:
            logger.warning(f"[AssellaPending] Failed to update config.yaml for {appid}: {e}")
            return False, f"Could not update config.yaml: {e}"

    # 3. Clear settings keys
    try:
        from utils.settings import get_settings

        s = get_settings()
        for k in list(s.allKeys()):
            if k.endswith(f"/{appid}") and k.split("/", 1)[0] in (
                "dlc_only_mode", "pin_build", "installed_buildid", "update_status", "branch", "depot_selection"
            ):
                s.remove(k)
    except Exception as _silent_err:
        logger.debug("[AssellaPending] _clear_settings: ignored %s", _silent_err, exc_info=False)

    return True, "Removed"
