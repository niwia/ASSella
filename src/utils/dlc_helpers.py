"""
Modular helper module for managing DLC-Only mode functionality in ASSella.
Provides centralized logic for checking DLC-only mode state, parsing installed DLCs,
syncing SLSsteam config, and formatting user-facing uninstall messages.
"""

import re
import logging
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional

from utils.settings import get_settings

logger = logging.getLogger("ACCELA.dlc_helpers")

_BASE_DEPOT_RE = re.compile(r"^(?:\[(?:WINDOWS|LINUX|MACOS|OSX|ALL)\]\s*)?Depot\s*\d+$", re.IGNORECASE)


def is_base_game_main_depot(depot_id: str, desc: str, base_appid: str) -> bool:
    """
    Returns True if depot_id represents a main base game executable/OS depot,
    rather than a DLC depot.
    """
    if str(depot_id) == str(base_appid):
        return True
    if not desc:
        return False
    return bool(_BASE_DEPOT_RE.match(desc.strip()))


def is_dlc_only_mode(appid: str) -> bool:
    """
    Check if dlc_only_mode is explicitly enabled in settings for a given base game AppID.
    """
    if not appid or appid in ("0", "N/A", "unknown"):
        return False
    try:
        settings = get_settings()
        return settings.value(f"dlc_only_mode/{appid}", False, type=bool)
    except Exception as e:
        logger.error(f"Error checking dlc_only_mode setting for {appid}: {e}")
        return False


def get_dlc_only_info(base_appid: str) -> List[Dict[str, str]]:
    """
    If dlc_only_mode is enabled for base_appid, parses all installed depots
    from '{base_appid}.depot' and returns a list of dictionaries:
    [{'dlc_appid': str, 'dlc_name': str, 'base_game_name': str}]
    for each depot that is not a base game OS depot.
    """
    if not is_dlc_only_mode(base_appid):
        return []

    from utils.helpers import get_base_path
    depot_file = get_base_path() / "depots" / f"{base_appid}.depot"
    if not depot_file.exists():
        return []

    results = []
    try:
        from managers.db_manager import DatabaseManager
        db = DatabaseManager()
        app_info = db.get_app_info(base_appid)
        base_game_name = app_info.get("name", "") if app_info else ""
        base_depots_data = app_info.get("depots", {}) if app_info else {}

        for line in depot_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            if parts and parts[0].strip():
                dlc_appid = parts[0].strip()

                # Skip main OS depots of the base game
                depot_meta = base_depots_data.get(dlc_appid, {})
                desc = depot_meta.get("desc", "")
                if is_base_game_main_depot(dlc_appid, desc, base_appid):
                    continue

                dlc_name = ""
                dlc_info = db.get_app_info(dlc_appid)
                if dlc_info:
                    dlc_name = dlc_info.get("name", "")

                if not dlc_name:
                    # Fallback to direct sqlite query to bypass cache expiration checks
                    try:
                        conn = sqlite3.connect(str(db.db_path))
                        cur = conn.cursor()
                        cur.execute("SELECT name FROM apps WHERE appid = ?", (dlc_appid,))
                        row = cur.fetchone()
                        if row and row[0]:
                            dlc_name = row[0]
                        conn.close()
                    except Exception:
                        pass

                if not dlc_name and desc:
                    dlc_name = desc.replace(" - Depot " + dlc_appid, "").strip()
                    dlc_name = re.sub(r"^\[(?:WINDOWS|LINUX|MACOS|OSX|ALL)\]\s*", "", dlc_name, flags=re.IGNORECASE).strip()

                results.append({
                    "dlc_appid": dlc_appid,
                    "dlc_name": dlc_name,
                    "base_game_name": base_game_name
                })
    except Exception as e:
        logger.error(f"Error checking dlc_only_mode for {base_appid}: {e}")

    return results


def get_all_dlcs_for_app(appid: str, game_data: Optional[dict] = None, allow_network: bool = True) -> list:
    """
    Returns a unified list of DLC dicts for an app:
      [{"dlc_appid": str, "dlc_name": str, "base_game_name": str}, ...]

    Order of resolution:
    1. Saved DLC-only info in QSettings (from a previous DLC toggle or import)
    2. game_data["dlcs"] (from local manifest / SLS config)
    3. Steam Store API appdetails (if allow_network=True)
    """
    appid_str = str(appid).strip()
    results = get_dlc_only_info(appid_str)
    if results:
        return results

    game_name = (game_data.get("game_name") if game_data else "") or ""

    # Check game_data["dlcs"]
    if game_data and game_data.get("dlcs"):
        dlc_map = game_data["dlcs"]
        if isinstance(dlc_map, dict):
            for d_id, d_name in dlc_map.items():
                results.append({
                    "dlc_appid": str(d_id),
                    "dlc_name": str(d_name or f"DLC {d_id}"),
                    "base_game_name": game_name,
                })
        elif isinstance(dlc_map, list):
            for d_id in dlc_map:
                results.append({
                    "dlc_appid": str(d_id),
                    "dlc_name": f"DLC {d_id}",
                    "base_game_name": game_name,
                })
        if results:
            return results

    if not allow_network:
        return results

    # Fetch from Steam Store API (handles uninstalled/owned games with 64+ DLCs like 4678800)
    try:
        import urllib.request
        import json

        url = f"https://store.steampowered.com/api/appdetails?appids={appid_str}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            app_info = data.get(appid_str, {}).get("data", {})
            if not game_name:
                game_name = app_info.get("name", "")
            dlc_ids = app_info.get("dlc", [])
            for d_id in dlc_ids:
                results.append({
                    "dlc_appid": str(d_id),
                    "dlc_name": (
                        f"{game_name} - DLC {d_id}" if game_name else f"DLC {d_id}"
                    ),
                    "base_game_name": game_name,
                })
    except Exception as e:
        logger.debug(f"Could not fetch DLC list for {appid_str} from Steam Store API: {e}")

    return results


def sync_dlc_only_sls_config(
    config_path: Path, appid: str, game_name: str, game_data: Optional[dict] = None
) -> bool:
    """
    Syncs a game to SLSsteam config.yaml based on DLC-only mode status.
    If DLC-only mode is active:
      - Ensures the base game AppID is REMOVED from AdditionalApps.
      - Adds each DLC AppID with comment '[DLC] {dlc_name} / {base_game_name}'.
      - If the game has 64 or more DLCs, adds them under DlcData to bypass Steam's 64 DLC limit.
    Else:
      - Adds the base game AppID to AdditionalApps.
      - Removes DLC AppIDs from AdditionalApps.
      - If the game has 64 or more DLCs, adds them under DlcData to bypass Steam's 64 DLC limit.
    """
    from utils.yaml_config_manager import (
        add_additional_app,
        remove_additional_app,
        add_dlc_data_batch,
        remove_dlc_data,
    )

    appid_str = str(appid).strip()
    dlc_mode = is_dlc_only_mode(appid_str)
    # Only allow slow network lookups if the game is in DLC-only mode or specifically requested
    dlc_list = get_all_dlcs_for_app(appid_str, game_data, allow_network=dlc_mode)

    if dlc_mode:
        # Base game AppID MUST NOT be in AdditionalApps when in DLC-only mode
        remove_additional_app(config_path, appid_str)
        added_any = False
        if dlc_list:
            for dlc_entry in dlc_list:
                dlc_appid = str(dlc_entry["dlc_appid"])
                dlc_name = dlc_entry["dlc_name"]
                base_name = dlc_entry["base_game_name"] or game_name
                comment = f"[DLC] {dlc_name or dlc_appid} / {base_name}"
                if add_additional_app(config_path, dlc_appid, comment):
                    added_any = True

            # If 64 or more DLCs, add them under DlcData to bypass Steam's 64 DLC limit
            if len(dlc_list) >= 64:
                dlc_dict = {str(d["dlc_appid"]): d["dlc_name"] for d in dlc_list}
                add_dlc_data_batch(config_path, appid_str, dlc_dict)
            else:
                remove_dlc_data(config_path, appid_str)
        return added_any
    else:
        # Regular game mode - ensure base game in AdditionalApps
        added = add_additional_app(config_path, appid_str, game_name)
        # Remove individual DLCs from AdditionalApps if they were present
        if dlc_list:
            for dlc_entry in dlc_list:
                remove_additional_app(config_path, str(dlc_entry["dlc_appid"]))

        # If the game has 64 or more DLCs, ensure DlcData is populated
        if dlc_list and len(dlc_list) >= 64:
            dlc_dict = {str(d["dlc_appid"]): d["dlc_name"] for d in dlc_list}
            add_dlc_data_batch(config_path, appid_str, dlc_dict)
        elif not dlc_list or len(dlc_list) < 64:
            remove_dlc_data(config_path, appid_str)
        return added


def get_dlc_uninstall_message(game_data: dict) -> str:
    """
    Build a polished, user-friendly confirmation message for DLC-Only uninstall,
    including a bulleted list of target DLC names and AppIDs.
    """
    game_name = game_data.get("game_name", "Unknown")
    appid = str(game_data.get("appid", "0"))
    dlc_list = get_dlc_only_info(appid)

    confirm_msg = f"Are you sure you want to uninstall DLC(s) for '{game_name}'?\n\n"
    confirm_msg += "Since this is a DLC Only installation, the base game files will NOT be deleted.\n"
    confirm_msg += "Only downloaded DLC depot files and SLS configuration entries will be removed.\n\n"

    if dlc_list:
        confirm_msg += "Target DLC(s) to remove:\n"
        for dlc in dlc_list:
            d_name = dlc.get("dlc_name") or dlc.get("dlc_appid")
            confirm_msg += f"  • {d_name} (AppID: {dlc.get('dlc_appid')})\n"
        confirm_msg += "\n"
    else:
        confirm_msg += "Target: Installed DLC depot files\n\n"

    confirm_msg += "This action cannot be undone!"
    return confirm_msg
