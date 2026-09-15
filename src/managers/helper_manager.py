"""
Helper Manager for ASSella - Handles automated, background game downloads and updates.
"""

import os
import sys
import re
import logging
from typing import List, Dict, Any, Optional

from core.morrenus_api import download_manifest, get_selected_branch
from core.tasks.process_zip_task import ProcessZipTask
from core.tasks.download_depots_task import DownloadDepotsTask
from managers.cli_manager import CLITaskManager
from core.steam_helpers import get_steam_libraries
from utils.settings import get_settings


def run_headless_helper(app_id: int, logger: logging.Logger):
    """
    Executes the download, update, and post-processing steps completely headlessly.
    """
    logger.info(f"==================================================")
    logger.info(f"Starting automated helper update for AppID: {app_id}")
    logger.info(f"==================================================")

    # 1. Download manifest ZIP for the branch the user has selected for this game
    branch = get_selected_branch(app_id)
    logger.info(f"Downloading manifest ZIP from Hubcap API (branch: {branch})...")
    zip_path, error = download_manifest(str(app_id), branch=branch)
    if error:
        logger.error(f"Failed to download manifest: {error}")
        sys.exit(1)
    logger.info(f"Downloaded manifest zip to: {zip_path}")

    # 2. Process ZIP manifest
    logger.info("Parsing manifest ZIP file...")
    zip_task = ProcessZipTask()
    game_data = zip_task.run(zip_path)
    if not game_data or not game_data.get("depots"):
        logger.error("No downloadable depots found in manifest.")
        sys.exit(1)
    logger.info(f"Successfully parsed manifest for game: {game_data.get('game_name', 'Unknown')}")

    # 3. Locate installation library & depots
    logger.info("Locating game installation directory across Steam libraries...")
    dest_path = None
    selected_depots = []

    libraries = get_steam_libraries()
    acf_filename = f"appmanifest_{app_id}.acf"
    for lib in libraries:
        acf_path = os.path.join(lib, "steamapps", acf_filename)
        if os.path.exists(acf_path):
            dest_path = lib
            logger.info(f"Found existing installation at: {lib}")
            
            # Parse installed depots from existing ACF
            installed_depots = []
            try:
                with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                in_installed_depots = False
                bracket_count = 0
                for line in lines:
                    line_strip = line.strip()
                    if not in_installed_depots:
                        if line_strip == '"InstalledDepots"':
                            in_installed_depots = True
                            bracket_count = 0
                    else:
                        if '{' in line_strip:
                            bracket_count += line_strip.count('{')
                        if '}' in line_strip:
                            bracket_count -= line_strip.count('}')
                            if bracket_count <= 0:
                                in_installed_depots = False
                                break
                        if bracket_count == 1:
                            depot_match = re.match(r'^\s*"(\d+)"', line_strip)
                            if depot_match:
                                installed_depots.append(depot_match.group(1))
            except Exception as e:
                logger.error(f"Error parsing installed depots from ACF: {e}")

            if installed_depots:
                logger.info(f"Detected previously installed depots: {', '.join(installed_depots)}")
                for dep_id in installed_depots:
                    if dep_id in game_data["depots"]:
                        selected_depots.append(dep_id)
            break

    if not dest_path:
        # Try to find via metadata.json fallback
        try:
            from utils.settings import get_settings
            settings = get_settings()
            experimental_mode = settings.value("experimental_acf_independent", False, type=bool)
        except Exception:
            experimental_mode = False

        if experimental_mode:
            import json
            for lib in libraries:
                common_dir = os.path.join(lib, "steamapps", "common")
                if os.path.exists(common_dir):
                    try:
                        for game_dir in os.listdir(common_dir):
                            game_path = os.path.join(common_dir, game_dir)
                            if os.path.isdir(game_path):
                                meta_path = os.path.join(game_path, ".DepotDownloader", "metadata.json")
                                if os.path.exists(meta_path):
                                    with open(meta_path, "r", encoding="utf-8") as f:
                                        meta = json.load(f)
                                    if str(meta.get("appid")) == str(app_id):
                                        dest_path = lib
                                        logger.info(f"Found existing installation via metadata.json at: {lib}")
                                        
                                        # Extract installed depots
                                        installed_depots = meta.get("selected_depots_list", [])
                                        if not installed_depots:
                                            dd_dir = os.path.join(game_path, ".DepotDownloader")
                                            for f_name in os.listdir(dd_dir):
                                                m = re.match(r'^(\d+)\.manifest$', f_name)
                                                if m:
                                                    installed_depots.append(m.group(1))
                                        
                                        if installed_depots:
                                            logger.info(f"Detected previously installed depots from metadata: {', '.join(installed_depots)}")
                                            for dep_id in installed_depots:
                                                if dep_id in game_data["depots"]:
                                                    selected_depots.append(dep_id)
                                        break
                    except Exception as meta_err:
                        logger.debug(f"Error scanning library {lib} for metadata: {meta_err}")
                if dest_path:
                    break

    if not dest_path:
        if libraries:
            dest_path = libraries[0]
            logger.info(f"No existing installation found. Defaulting to main Steam library: {dest_path}")
        else:
            dest_path = os.path.expanduser("~/.local/share/ACCELA/downloads")
            os.makedirs(dest_path, exist_ok=True)
            logger.info(f"No Steam libraries found. Saving to fallback path: {dest_path}")

    # If no depots selected (e.g. new install or ACF parsing yielded none), select automatically
    if not selected_depots:
        target_platform = "linux"
        has_linux = False
        for d_data in game_data["depots"].values():
            oslist = (d_data.get("oslist") or "").lower()
            desc = (d_data.get("desc") or "").lower()
            if "linux" in oslist or "[linux]" in desc:
                has_linux = True
                break
        
        platform_to_select = "linux" if has_linux else "windows"
        logger.info(f"Auto-selecting depots for platform: {platform_to_select}")

        for depot_id, d_data in game_data["depots"].items():
            os_val = (d_data.get("oslist") or "").lower()
            desc_val = (d_data.get("desc") or "").lower()
            if "macosx" in os_val or "macos" in os_val or "[macos]" in desc_val:
                continue
            if not os_val:
                # Shared depot
                selected_depots.append(depot_id)
            elif platform_to_select in os_val or f"[{platform_to_select}]" in desc_val:
                selected_depots.append(depot_id)

    if not selected_depots:
        logger.error("No compatible depots found for download.")
        sys.exit(1)

    logger.info(f"Target download depots: {', '.join(selected_depots)}")
    game_data["selected_depots_list"] = selected_depots

    # 4. Download depots
    logger.info("Initializing download task...")
    download_task = DownloadDepotsTask()
    
    # Direct download logs to logger
    download_task.progress.connect(logger.info)
    
    # We log progress percentage internally
    def on_progress_percent(percent):
        logger.info(f"Download progress: {percent}%")
    download_task.progress_percentage.connect(on_progress_percent)

    try:
        download_task.run(game_data, selected_depots, dest_path)
    except Exception as e:
        logger.error(f"Depot download failed with error: {e}")
        sys.exit(1)

    # 5. Run post-processing
    logger.info("Running post-processing pipeline (ACF creation, Goldberg emulator, DRM removal)...")
    settings = get_settings()
    cli_task_manager = CLITaskManager(settings, logger)
    cli_task_manager.run_post_processing(game_data, download_task, dest_path)

    logger.info(f"==================================================")
    logger.info(f"Headless helper completed successfully for AppID: {app_id}")
    logger.info(f"==================================================")
    sys.exit(0)


def run_headless_update_check(logger):
    """
    Scans for ASSella-managed games and checks for updates headlessly, updating the update cache.
    """
    logger.info("==================================================")
    logger.info("Starting headless update check...")
    logger.info("==================================================")

    from managers.game_manager import GameManager
    from core.tasks.manifest_check_task import ManifestCheckTask
    from utils.update_status_cache import get_update_cache

    class MockMainWindow:
        def __init__(self):
            self.settings = get_settings()

    mock_win = MockMainWindow()
    game_manager = GameManager(mock_win)

    # 1. Scan libraries synchronously
    logger.info("Scanning library for games...")
    game_manager._perform_scan()

    accela_games = [g for g in game_manager.games if g.get("is_accela_install")]
    logger.info(f"Found {len(accela_games)} ASSella-managed game(s).")
    if not accela_games:
        logger.info("No ASSella games found to check.")
        sys.exit(0)

    # Filter to games with valid appids
    games_to_check = [
        g for g in accela_games
        if g.get("appid") not in ("0", "N/A", "unknown", None)
    ]

    if not games_to_check:
        logger.info("No games with valid AppID to check.")
        sys.exit(0)

    logger.info(f"Valid games to check: {len(games_to_check)}")

    # 2. Run ManifestCheckTask synchronously
    task = ManifestCheckTask(games_to_check)
    task.game_update_checked.connect(game_manager._on_game_update_checked)

    def on_progress(current, total):
        logger.info(f"Update check progress: {current}/{total}")
    task.progress.connect(on_progress)

    try:
        task.run()
    except Exception as e:
        logger.error(f"Headless update check failed: {e}")
        sys.exit(1)

    # Force save of update status cache
    get_update_cache().save()

    logger.info("==================================================")
    logger.info("Headless update check completed successfully.")
    logger.info("==================================================")
    sys.exit(0)
