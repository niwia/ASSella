import os
import re
import json
import time
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

def write_accela_metadata(
    dest_path: str,
    game_data: Dict[str, Any],
    size_on_disk: int,
) -> Optional[str]:
    """
    Write metadata.json inside the game's hidden .DepotDownloader folder.
    This serves as a local fallback for ACF manifest data.
    """
    if not dest_path or not game_data:
        return None

    try:
        from utils.steam_manifest import get_install_folder_name
        install_folder_name = get_install_folder_name(game_data)
    except ImportError:
        # Fallback if get_install_folder_name is not importable
        install_folder_name = game_data.get("installdir")
        if not install_folder_name:
            safe_game_name = re.sub(r'[\\/:*?"<>|]', "", game_data.get("game_name", "")).strip()
            install_folder_name = safe_game_name or f"App_{game_data.get('appid')}"

    ddm_dir = os.path.join(
        dest_path, "steamapps", "common", install_folder_name, ".DepotDownloader"
    )
    
    try:
        os.makedirs(ddm_dir, exist_ok=True)
        metadata_path = os.path.join(ddm_dir, "metadata.json")
        
        meta = {
            "appid": str(game_data.get("appid", "")),
            "buildid": str(game_data.get("buildid", "")),
            "game_name": game_data.get("game_name", ""),
            "size_on_disk": int(size_on_disk),
            "last_updated": game_data.get("last_updated", "") or int(time.time()),
        }
        
        # Add selected depots if they exist
        if game_data.get("selected_depots_list"):
            meta["selected_depots_list"] = game_data["selected_depots_list"]
            
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=4)

        # Write .assella marker in the game root folder
        try:
            game_folder_path = os.path.join(dest_path, "steamapps", "common", install_folder_name)
            if os.path.isdir(game_folder_path):
                marker_path = os.path.join(game_folder_path, ".assella")
                with open(marker_path, "w", encoding="utf-8") as mf:
                    mf.write(f"appid={game_data.get('appid')}\n")
        except Exception:
            pass
            
        logger.info(f"Successfully saved ACCELA metadata to {metadata_path}")
        return metadata_path
    except Exception as e:
        logger.error(f"Failed to write metadata.json at {ddm_dir}: {e}", exc_info=True)
        return None

def load_accela_metadata(game_path: str) -> Optional[Dict[str, Any]]:
    """
    Load ACCELA metadata.json from game directory if it exists.
    """
    if not game_path or not os.path.exists(game_path):
        return None
        
    for sub in (".DepotDownloader", ".assella", ".accela"):
        metadata_path = os.path.join(game_path, sub, "metadata.json")
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Ensure basic fields exist
                    if "appid" in data:
                        return data
            except Exception as e:
                logger.error(f"Failed to read/parse metadata at {metadata_path}: {e}")
    return None
        
    return None

def edit_existing_acf_metadata(
    acf_path: str,
    buildid: str,
    size_on_disk: Optional[int],
) -> bool:
    """
    Modifies buildid and SizeOnDisk inline in an existing .acf file
    to preserve any other user-specific or Steam-native configuration values.
    """
    if not acf_path or not os.path.exists(acf_path):
        return False
        
    try:
        with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # Update buildid
        buildid_pat = re.compile(r'(\s*"buildid"\s+)"([^"]*)"')
        if buildid_pat.search(content):
            content = buildid_pat.sub(rf'\1"{buildid}"', content)
        else:
            logger.warning(f"Key 'buildid' not found in ACF: {acf_path}")

        # Update SizeOnDisk
        if size_on_disk is not None:
            size_pat = re.compile(r'(\s*"SizeOnDisk"\s+)"([^"]*)"')
            if size_pat.search(content):
                content = size_pat.sub(rf'\1"{size_on_disk}"', content)
            else:
                logger.warning(f"Key 'SizeOnDisk' not found in ACF: {acf_path}")

        with open(acf_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        logger.info(f"Successfully edited ACF inline at {acf_path} (buildid={buildid}, SizeOnDisk={size_on_disk})")
        return True
    except Exception as e:
        logger.error(f"Failed to edit ACF inline at {acf_path}: {e}", exc_info=True)
        return False
