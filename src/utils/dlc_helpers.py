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
from utils.helpers import get_base_path

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


def sync_dlc_only_sls_config(config_path: Path, appid: str, game_name: str) -> bool:
    """
    Syncs a game to SLSsteam config.yaml based on DLC-only mode status.
    If DLC-only mode is active:
      - Ensures the base game AppID is REMOVED from AdditionalApps.
      - Adds each installed DLC AppID with comment '[DLC] {dlc_name} / {base_game_name}'.
    Else:
      - Adds the base game AppID.
    """
    from utils.yaml_config_manager import add_additional_app, remove_additional_app

    dlc_list = get_dlc_only_info(str(appid))
    if dlc_list:
        # Base game AppID MUST NOT be in AdditionalApps when in DLC-only mode
        remove_additional_app(config_path, str(appid))
        added_any = False
        for dlc_entry in dlc_list:
            dlc_appid = dlc_entry["dlc_appid"]
            dlc_name = dlc_entry["dlc_name"]
            base_game_name = dlc_entry["base_game_name"] or game_name
            comment = f"[DLC] {dlc_name or dlc_appid} / {base_game_name}"
            if add_additional_app(config_path, str(dlc_appid), comment):
                added_any = True
        return added_any
    else:
        return add_additional_app(config_path, str(appid), game_name)


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
