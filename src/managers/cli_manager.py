"""
CLI Manager for ACCELA - Handles command-line mode for ZIP file processing.

When ACCELA is invoked with ZIP file arguments, this module takes over
to provide a simplified CLI experience without the main window.
"""

import logging
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, Optional

from PyQt6.QtCore import QEventLoop
from PyQt6.QtWidgets import QApplication

from core.steam_helpers import (
    fix_greenluma_offline_mode,
    get_steam_libraries,
    find_next_applist_number,
    app_id_exists_in_applist,
)
from core.tasks.process_zip_task import ProcessZipTask
from core.tasks.download_depots_task import DownloadDepotsTask
from core.morrenus_api import download_manifest as download_morrenus_manifest

from utils.settings import get_settings
from utils.task_runner import TaskRunner
from utils.paths import Paths
from utils.steam_manifest import get_game_directory, write_acf_file
from utils.helpers import create_font_from_settings
from utils.wrapper_metadata import persist_selected_dlcs
from utils.yaml_config_manager import (
    is_greenluma_wrapper_mode_enabled,
    is_slssteam_mode_enabled,
)

# Import text menus for CLI mode (urwid-based, cross-platform)
from ui.text_menus import (
    select_depots,
    select_dlcs,
    select_steam_library,
    select_destination_path,
)

LINUX_TERMINALS = [
    ["wezterm", "start", "--always-new-process", "--"],
    ["konsole", "-e"],
    ["gnome-terminal", "--"],
    ["ptyxis", "--"],
    ["alacritty", "-e"],
    ["tilix", "-e"],
    ["xfce4-terminal", "-e"],
    ["terminator", "-x"],
    ["mate-terminal", "-e"],
    ["lxterminal", "-e"],
    ["xterm", "-e"],
    ["kitty", "-e"],
]


def _get_terminal_command(
    appid: Optional[int] = None, zip_path: Optional[str] = None
) -> Optional[list[str]]:
    """Get the terminal command to run CLI mode.

    Args:
        appid: AppID to download from Morrenus API
        zip_path: Path to a ZIP file to process

    Returns:
        List of command arguments, or None if no terminal available
    """
    if sys.platform != "linux":
        return None

    # Get the directory where accela is installed
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run_script = str(os.path.join(script_dir, "run.sh"))

    if not os.path.exists(run_script):
        return None

    # Build the base command
    if appid:
        base_cmd = [run_script, "-cli", "--appid", str(appid)]
    elif zip_path:
        base_cmd = [run_script, "-cli", str(zip_path)]
    else:
        return None

    # Find first available terminal
    for terminal in LINUX_TERMINALS:
        term_path = shutil.which(terminal[0])
        if term_path:
            return [str(term_path)] + terminal[1:] + base_cmd

    return None


def open_cli_terminal(
    appid: Optional[int] = None, zip_path: Optional[str] = None
) -> bool:
    """Open a new terminal running ACCELA CLI mode.

    Args:
        appid: AppID to download from Morrenus API
        zip_path: Path to a ZIP file to process
    """
    cmd = _get_terminal_command(appid, zip_path)
    if not cmd:
        return False

    try:
        cmd_str = [str(part) for part in cmd]
        subprocess.Popen(cmd_str, start_new_session=True)
        return True
    except (OSError, ValueError):
        return False


def run_cli_mode(
    app: QApplication,
    command_line_zips: list[str],
    logger: logging.Logger,
    appid: Optional[int] = None,
):
    """Run ACCELA in CLI mode - show only DepotSelectionDialog for ZIP files.

    Args:
        app: QApplication instance
        command_line_zips: List of ZIP file paths to process
        logger: Logger instance
        appid: Optional AppID to download manifest from Morrenus API
    """
    # Load settings for CLI mode
    settings = get_settings()

    logger.info("=" * 50)
    logger.info("ACCELA CLI Mode")
    logger.info("=" * 50)

    # Fix GreenLuma offline mode
    fix_greenluma_offline_mode()

    # Apply ACCELA theme
    from main import update_appearance

    accent_color = settings.value("accent_color", "#C06C84")
    bg_color = settings.value("background_color", "#000000")

    font = create_font_from_settings(settings)

    font_ok, font_info = update_appearance(app, accent_color, bg_color, font)
    if font_ok:
        logger.info(f"CLI mode: theme applied, font '{font_info}' loaded")
    else:
        logger.warning("CLI mode: failed to load custom font")

    # If appid is provided, download manifest from Morrenus API
    if appid:
        logger.info(f"Downloading manifest for AppID {appid} from Morrenus API")
        zip_path, error = download_morrenus_manifest(str(appid))
        if error:
            logger.error(f"Failed to download manifest: {error}")
            logger.info("Exiting CLI mode.")
            return
        command_line_zips = [zip_path]
        logger.info(
            f"Manifest downloaded: {os.path.basename(zip_path) if zip_path else 'Unknown'}"
        )

    def get_destination_path_cli() -> Optional[str]:
        """Get destination path based on settings (same logic as TaskManager)"""
        slssteam_mode_local = is_slssteam_mode_enabled()
        library_mode = settings.value("library_mode", False, type=bool)

        if slssteam_mode_local or library_mode:
            libraries = get_steam_libraries()
            if libraries:
                path = select_steam_library(libraries)
                if path:
                    return path
                return None

        # Use text-based destination path selection
        default_path = os.path.expanduser("~")
        path = select_destination_path(default_path)
        return path

    total_zips = len(command_line_zips)

    for index, zip_path in enumerate(command_line_zips):
        current_job = index + 1
        logger.info(
            f"\n[{current_job}/{total_zips}] Processing: {os.path.basename(zip_path) if zip_path else 'Unknown'}"
        )

        # Step 1: Process ZIP file
        logger.info("Parsing ZIP file...")
        zip_task = ProcessZipTask()

        game_data_holder: list[Optional[Dict[str, Any]]] = [None]

        def on_zip_processed(result: Optional[Dict[str, Any]]):
            game_data_holder[0] = result
            loop.quit()

        # Create a local event loop to wait for ZIP processing
        loop = QEventLoop()
        zip_task_runner = TaskRunner()
        zip_task_runner.cleanup_complete.connect(loop.quit)
        zip_task_runner.run(zip_task.run, zip_path).finished.connect(on_zip_processed)
        loop.exec()

        game_data = game_data_holder[0]

        if not game_data or not game_data.get("depots"):
            logger.warning(
                f"No depots found in {os.path.basename(zip_path) if zip_path else 'Unknown'}"
            )
            continue

        # Step 2: Show depot selection menu
        logger.info(
            f"Showing depot selection for: {game_data.get('game_name', 'Unknown')}"
        )

        selected_depots = select_depots(
            game_data["appid"],
            game_data["game_name"],
            game_data["depots"],
            game_data.get("header_url"),
        )

        if not selected_depots:
            logger.info(
                "Depot selection cancelled or no depots selected, skipping this ZIP"
            )
            continue

        logger.info(f"Selected {len(selected_depots)} depots")

        # Step 2.5: DLC selection (Windows + GreenLuma wrapper only)
        if (
            sys.platform == "win32"
            and is_greenluma_wrapper_mode_enabled()
            and game_data.get("dlcs")
        ):
            logger.info(
                "Windows + GreenLuma wrapper detected, showing DLC selection..."
            )
            selected_dlcs = select_dlcs(game_data["dlcs"])
            if selected_dlcs:
                game_data["selected_dlcs"] = selected_dlcs
                logger.info(f"Selected {len(selected_dlcs)} DLCs")

        # Step 3: Get destination path (respects Steam integration settings)
        dest_path = get_destination_path_cli()

        if not dest_path:
            logger.info("Destination folder not selected, skipping this ZIP")
            continue

        logger.info(f"Destination: {dest_path}")

        # Step 4: Start download
        logger.info("\n" + "=" * 40)
        logger.info("Starting download...")
        logger.info("=" * 40)

        game_data["selected_depots_list"] = selected_depots

        download_task = DownloadDepotsTask()
        download_task.progress.connect(logger.info)

        # TaskRunner.run() returns the worker, store it
        download_task_runner = TaskRunner()
        download_worker = download_task_runner.run(
            download_task.run, game_data, selected_depots, dest_path
        )

        # Wait for download to complete
        download_loop = QEventLoop()

        def on_download_complete():
            download_loop.quit()

        download_worker.finished.connect(on_download_complete)
        download_loop.exec()

        # Step 5: Run all post-processing steps
        logger.info("\n" + "=" * 40)
        logger.info("Running post-processing...")
        logger.info("=" * 40)

        # Create a CLI task manager to handle all post-processing
        cli_task_manager = CLITaskManager(settings, logger)
        cli_task_manager.run_post_processing(game_data, download_task, dest_path)

        logger.info("\n" + "=" * 40)
        if game_data:
            logger.info(f"Download complete: {game_data.get('game_name', 'Unknown')}")
        logger.info("=" * 40)

    logger.info(f"\n{'=' * 50}")
    logger.info(f"All {total_zips} ZIP(s) processed")
    logger.info(f"{'=' * 50}")

    # Stop all active TaskRunners to prevent QThread errors on exit
    TaskRunner.stop_all_active()

    # Process pending events to ensure clean Qt shutdown
    app.processEvents()

    # Remove Qt log handler and shutdown logging before exit to prevent atexit callback errors
    # (the Qt C++ object may be deleted before Python's logging shutdown)
    from utils.logger import qt_log_handler

    root_logger = logging.getLogger()
    try:
        root_logger.removeHandler(qt_log_handler)
    except (RuntimeError, TypeError):
        pass  # Handler may already be deleted

    # Manually shutdown logging to prevent atexit from trying to access deleted Qt objects
    logging.shutdown()

    sys.exit(0)


class CLITaskManager:
    """CLI version of TaskManager that handles all post-processing steps without UI."""

    def __init__(self, settings, logger):
        self.settings = settings
        self.logger = logger
        self.game_data: Optional[Dict[str, Any]] = None
        self.download_task: Optional[DownloadDepotsTask] = None
        self.current_dest_path: Optional[str] = None
        self.slssteam_mode_was_active = False

    def run_post_processing(
        self,
        game_data: Optional[Dict[str, Any]],
        download_task: DownloadDepotsTask,
        dest_path: str,
    ):
        """Run all post-processing steps after download completion."""
        if not game_data:
            return

        self.game_data = game_data
        self.download_task = download_task
        self.current_dest_path = dest_path
        self.slssteam_mode_was_active = is_slssteam_mode_enabled()

        # Get size on disk
        size_on_disk = 0
        if self.download_task:
            size_on_disk = self.download_task.total_download_size_for_this_job
            self.logger.info(f"Retrieved SizeOnDisk from download task: {size_on_disk}")

        # Move manifests to depotcache FIRST — depotcache must be populated before
        # the SLS install|appid|index command fires so Steam can verify local files.
        self._move_manifests_to_depotcache()

        # Write app token to file for non-integrated installs
        self._write_app_token(dest_path)

        # Create ACF / trigger SLS install (after depotcache is ready)
        self._create_acf_file(size_on_disk)

        # Save main depot info
        self._save_main_depot_info()

        # Persist selected DLC metadata for future uninstall cleanup
        self._persist_wrapper_metadata()

        # Set Linux binary permissions
        if sys.platform == "linux":
            self._set_linux_binary_permissions()


        # Auto-apply Goldberg after download completion
        auto_apply_goldberg = self.settings.value(
            "auto_apply_goldberg", False, type=bool
        )
        if auto_apply_goldberg and self.game_data and self.current_dest_path:
            game_directory = get_game_directory(self.current_dest_path, self.game_data)
            self.logger.info("Auto-applying Goldberg after download completion")
            self._apply_goldberg(
                game_directory,
                str(self.game_data.get("appid", "")),
                self.game_data.get("game_name", ""),
            )
            if sys.platform == "linux":
                self._set_linux_permissions(game_directory)

        # Steamless processing
        steamless_enabled = self.settings.value(
            "use_steamless", False, type=bool
        ) or self.settings.value("use_steamless_aio", False, type=bool)
        appid = self.game_data.get("appid") if self.game_data else None
        is_dlc_only = False
        if appid:
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc_only = is_dlc_only_mode(str(appid))

        if steamless_enabled and not is_dlc_only:
            self.logger.info("Steamless is enabled, starting DRM removal...")
            self._run_steamless()

        # Achievement generation
        achievements_enabled = self.settings.value(
            "generate_achievements", False, type=bool
        )
        if achievements_enabled:
            self.logger.info("Achievement generation is enabled...")
            self._run_achievement_generation()

        # GreenLuma AppList files (Windows + GreenLuma wrapper)
        if self.slssteam_mode_was_active:
            if sys.platform == "win32":
                self.logger.info(
                    "Windows GreenLuma wrapper active. Creating AppList files..."
                )
                self._create_greenluma_files()
            elif sys.platform == "linux":
                self.logger.info(
                    "Linux Steam integration active, GreenLuma files skipped"
                )

        self.logger.info("All post-processing steps completed")

    def _create_acf_file(self, size_on_disk: int):
        """Create Steam ACF manifest file"""
        if not self.game_data or not self.current_dest_path:
            return

        appid = self.game_data.get("appid")

        # 1. Always write/update our local metadata.json fallback
        try:
            from utils.assella_metadata import write_accela_metadata
            write_accela_metadata(self.current_dest_path, self.game_data, size_on_disk)
        except Exception as e:
            self.logger.error(f"Failed to write metadata JSON file: {e}")

        # 2. If ACF-Independent mode is active, delegate manifest creation entirely to Steam natively
        try:
            from utils.slssteam_integration import install_via_sls, _experimental_mode_enabled
            if _experimental_mode_enabled():
                self.logger.info("ACF-Independent Mode is active. Delegating manifest creation to Steam natively.")
                if appid and appid not in ("0", "N/A", "unknown"):
                    install_via_sls(
                        appid=str(appid),
                        game_name=self.game_data.get("game_name", ""),
                        library_path=self.current_dest_path or "",
                    )
                return
        except Exception as e:
            self.logger.error(f"Error in SLSsteam install flow for {appid}: {e}")

        # 3. Fallback: Write standard Steam .acf manifest file when experimental mode is disabled
        if appid:
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc_only = is_dlc_only_mode(str(appid))
            if is_dlc_only:
                self.logger.info("DLC Only mode active. Skipping base game .acf manifest generation.")
                return

        self.logger.info("Generating Steam .acf manifest file...")

        try:
            acf_path = write_acf_file(
                self.current_dest_path,
                self.game_data,
                size_on_disk,
                include_depots=True,
                log_proton=True,
            )
            if acf_path:
                self.logger.info(f"Created .acf file at {acf_path}")
        except OSError as e:
            self.logger.error(f"Error creating .acf file: {e}")

    def _write_app_token(self, dest_path: str):
        """Write app token to file for non-integrated installs."""
        if self.slssteam_mode_was_active:
            return

        if not self.game_data:
            return

        app_token = self.game_data.get("app_token")
        if not app_token:
            return

        game_dir = get_game_directory(dest_path, self.game_data)
        token_file = os.path.join(game_dir, "apptoken.txt")

        try:
            os.makedirs(game_dir, exist_ok=True)
            with open(token_file, "w") as f:
                f.write(app_token)
            self.logger.info(f"Wrote app token to {token_file}")
        except OSError as e:
            self.logger.error(f"Failed to write app token to file: {e}")

    def _move_manifests_to_depotcache(self):
        """Move manifests from temp to depotcache"""
        if not self.game_data or not self.current_dest_path:
            return

        import tempfile

        temp_manifest_dir = os.path.join(tempfile.gettempdir(), "mistwalker_manifests")
        if not os.path.exists(temp_manifest_dir):
            return

        target_depotcache_dir = os.path.join(self.current_dest_path, "depotcache")

        try:
            os.makedirs(target_depotcache_dir, exist_ok=True)
            manifests_map = self.game_data.get("manifests", {})

            if not manifests_map:
                shutil.rmtree(temp_manifest_dir)
                return

            moved_count = 0
            for depot_id, manifest_gid in manifests_map.items():
                manifest_filename = f"{depot_id}_{manifest_gid}.manifest"
                source_path = os.path.join(temp_manifest_dir, manifest_filename)
                dest = os.path.join(target_depotcache_dir, manifest_filename)
                if os.path.exists(source_path):
                    shutil.move(source_path, dest)
                    moved_count += 1

            self.logger.info(f"Moved {moved_count} manifest files to depotcache.")
            shutil.rmtree(temp_manifest_dir)
        except (OSError, shutil.Error) as e:
            self.logger.error(f"Failed to move manifests to depotcache: {e}")

    def _save_main_depot_info(self):
        """Save main depot ID and manifest to persistent file"""
        from pathlib import Path
        from utils.helpers import get_base_path

        if not self.game_data:
            return

        appid = self.game_data.get("appid")
        if not appid:
            return

        selected_depots = self.game_data.get("selected_depots_list", [])
        all_manifests = self.game_data.get("manifests", {})

        if not selected_depots or not all_manifests:
            return

        try:
            depots_dir = Path(get_base_path()) / "depots"
            depots_dir.mkdir(parents=True, exist_ok=True)
            depot_file = depots_dir / f"{appid}.depot"

            existing_entries = {}
            if depot_file.exists():
                try:
                    for line in depot_file.read_text().splitlines():
                        parts = [p.strip() for p in line.split(":")]
                        if len(parts) >= 2:
                            existing_entries[parts[0]] = line
                except Exception:
                    pass

            for depot_id_raw in selected_depots:
                depot_id = str(depot_id_raw)
                manifest_id = all_manifests.get(depot_id)
                if manifest_id:
                    existing_entries[depot_id] = f"{depot_id}: {manifest_id}"

            with open(depot_file, "w") as f:
                for entry_line in existing_entries.values():
                    f.write(entry_line + "\n")

            self.logger.info(f"Saved depot info for app {appid}: {len(existing_entries)} depot(s)")
        except OSError as e:
            self.logger.error(f"Failed to save depot info: {e}")

    def _persist_wrapper_metadata(self):
        """Persist selected DLC IDs into game metadata for uninstall cleanup."""
        if sys.platform != "win32":
            return

        if not self.game_data or not self.current_dest_path:
            return

        game_directory = get_game_directory(self.current_dest_path, self.game_data)
        selected_dlcs = self.game_data.get("selected_dlcs", [])

        if persist_selected_dlcs(game_directory, selected_dlcs):
            appid = self.game_data.get("appid", "unknown")
            self.logger.debug(
                f"Persisted wrapper metadata for AppID {appid} with {len(selected_dlcs)} DLC ID(s)"
            )

    def _set_linux_binary_permissions(self):
        """Set executable permissions for Linux binaries"""
        if not self.game_data or not self.current_dest_path:
            return

        game_directory = get_game_directory(self.current_dest_path, self.game_data)

        if not os.path.exists(game_directory):
            return

    def _apply_goldberg(self, game_directory: str, appid: str, game_name: str) -> bool:
        """Apply Goldberg files to a game directory (CLI mode)."""
        self.logger.info(
            f"Applying Goldberg for game: {game_name} (AppID: {appid}) in {game_directory}"
        )

        if not game_directory or not os.path.exists(game_directory):
            self.logger.warning(f"Game directory not found: {game_directory}")
            return False

        is_windows = sys.platform == "win32"
        is_linux = sys.platform == "linux"

        found_dirs = set()

        if is_windows:
            for root, _, files in os.walk(game_directory):
                for fname in files:
                    if fname.lower() in ("steam_api.dll", "steam_api64.dll"):
                        found_dirs.add(root)
        elif is_linux:
            for root, _, files in os.walk(game_directory):
                for fname in files:
                    fname_lower = fname.lower()
                    if fname_lower in ("libsteam_api.so", "steam_api.dll", "steam_api64.dll"):
                        found_dirs.add(root)

        if not found_dirs:
            if is_windows:
                self.logger.info("No steam_api DLLs found in game directory tree")
            else:
                self.logger.info("No libsteam_api.so or steam_api DLL found in game directory tree")
            return False

        # Source Goldberg directory in bundled deps
        goldberg_src = Paths.deps("Goldberg")
        if not goldberg_src.exists():
            self.logger.error(f"Goldberg source not found: {goldberg_src}")
            return False

        def detect_elf_architecture(file_path: str):
            try:
                with open(file_path, "rb") as f:
                    magic = f.read(4)
                    if magic != b"\x7fELF":
                        return None
                    elf_class = f.read(1)
                    if elf_class == b"\x01":
                        return "32"
                    elif elf_class == b"\x02":
                        return "64"
            except (IOError, OSError):
                pass
            return None

        processed = 0
        try:
            for dest_dir in found_dirs:
                if is_windows:
                    original_dlls_in_dir = set()
                    for base in ("steam_api.dll", "steam_api64.dll"):
                        if os.path.exists(os.path.join(dest_dir, base)):
                            original_dlls_in_dir.add(base)

                    for base in ("steam_api.dll", "steam_api64.dll"):
                        src_path = os.path.join(dest_dir, base)
                        if os.path.exists(src_path):
                            try:
                                target_path = src_path + ".valve"
                                if not os.path.exists(target_path):
                                    os.replace(src_path, target_path)
                                    self.logger.info(f"Renamed {src_path} -> {target_path}")
                            except OSError as e:
                                self.logger.warning(f"Failed to rename {src_path}: {e}")

                    for base in original_dlls_in_dir:
                        src_dll = goldberg_src / "windows" / base
                        dest_dll = os.path.join(dest_dir, base)
                        try:
                            if src_dll.exists():
                                shutil.copy2(str(src_dll), dest_dll)
                                self.logger.info(f"Copied Goldberg DLL {src_dll} -> {dest_dll}")
                            else:
                                self.logger.warning(f"Goldberg DLL not found in deps: {src_dll}")
                        except (OSError, shutil.Error) as e:
                            self.logger.warning(f"Failed to copy Goldberg DLL: {e}")
                
                elif is_linux:
                    # Check if this directory has native .so files or DLLs
                    has_native_so = os.path.exists(os.path.join(dest_dir, "libsteam_api.so"))
                    has_dll = any(os.path.exists(os.path.join(dest_dir, base))
                                  for base in ("steam_api.dll", "steam_api64.dll"))

                    if has_native_so:
                        # Native Linux: .so file logic with architecture detection
                        original_so_path = os.path.join(dest_dir, "libsteam_api.so")
                        if os.path.exists(original_so_path):
                            detected_arch = detect_elf_architecture(original_so_path)
                            self.logger.info(f"Detected {detected_arch}-bit ELF in {dest_dir}")

                            try:
                                target_path = original_so_path + ".valve"
                                if not os.path.exists(target_path):
                                    os.replace(original_so_path, target_path)
                                    self.logger.info(f"Renamed {original_so_path} -> {target_path}")
                            except OSError as e:
                                self.logger.warning(f"Failed to rename {original_so_path}: {e}")

                            if detected_arch == "64":
                                src_so = goldberg_src / "linux" / "libsteam_api64.so"
                            else:
                                src_so = goldberg_src / "linux" / "libsteam_api.so"
                            dest_so = os.path.join(dest_dir, "libsteam_api.so")

                            try:
                                if src_so.exists():
                                    shutil.copy2(str(src_so), dest_so)
                                    self.logger.info(f"Copied {src_so.name} to {dest_so}")
                                else:
                                    self.logger.warning(f"Source .so not found: {src_so}")
                            except OSError as e:
                                self.logger.warning(f"Failed to copy Goldberg SO: {e}")

                    if has_dll:
                        # DLL logic (Proton/Wine)
                        original_dlls_in_dir = set()
                        for base in ("steam_api.dll", "steam_api64.dll"):
                            dll_path = os.path.join(dest_dir, base)
                            if os.path.exists(dll_path):
                                original_dlls_in_dir.add(base)

                        for base in ("steam_api.dll", "steam_api64.dll"):
                            src_dll_path = os.path.join(dest_dir, base)
                            if os.path.exists(src_dll_path):
                                try:
                                    target_path = src_dll_path + ".valve"
                                    if not os.path.exists(target_path):
                                        os.replace(src_dll_path, target_path)
                                        self.logger.info(f"Renamed {src_dll_path} -> {target_path}")
                                except OSError as e:
                                    self.logger.warning(f"Failed to rename {src_dll_path}: {e}")

                        for base in original_dlls_in_dir:
                            src_dll = goldberg_src / "windows" / base
                            dest_dll = os.path.join(dest_dir, base)
                            try:
                                if src_dll.exists():
                                    shutil.copy2(str(src_dll), dest_dll)
                                    self.logger.info(f"Copied Goldberg DLL {src_dll} -> {dest_dll}")
                                else:
                                    self.logger.warning(f"Goldberg DLL not found in deps: {src_dll}")
                            except (OSError, shutil.Error) as e:
                                self.logger.warning(f"Failed to copy Goldberg DLL: {e}")

                goldberg_platform_src = goldberg_src / ("windows" if is_windows else "linux")
                
                for item in goldberg_src.iterdir():
                    if item.name.lower() in (
                        "windows", "linux", "genints",
                        "steam_api.dll", "steam_api64.dll",
                        "libsteam_api.so", "libsteam_api64.so",
                        "steam_appid.txt",
                    ):
                        continue
                    dest_path = os.path.join(dest_dir, item.name)
                    try:
                        if item.is_dir():
                            shutil.copytree(str(item), dest_path, dirs_exist_ok=True)
                            self.logger.info(f"Copied dir {item} -> {dest_path}")
                        else:
                            shutil.copy2(str(item), dest_path)
                            self.logger.info(f"Copied file {item} -> {dest_path}")
                    except (OSError, shutil.Error) as e:
                        self.logger.warning(f"Failed to copy {item} to {dest_path}: {e}")

                if goldberg_platform_src.exists():
                    for item in goldberg_platform_src.iterdir():
                        item_name_lower = item.name.lower()
                        if item_name_lower == "steam_appid.txt":
                            continue
                        # On Linux, only copy .so files if the game has native .so files
                        # On Windows, only copy .dll files if the game has .dll files
                        if is_linux and item_name_lower.endswith(".so"):
                            if not has_native_so:
                                continue
                        elif is_windows and item_name_lower.endswith(".dll"):
                            if not has_dll:
                                continue
                        dest_path = os.path.join(dest_dir, item.name)
                        try:
                            if item.is_dir():
                                shutil.copytree(str(item), dest_path, dirs_exist_ok=True)
                            else:
                                shutil.copy2(str(item), dest_path)
                        except (OSError, shutil.Error) as e:
                            self.logger.warning(f"Failed to copy {item}: {e}")

                try:
                    appid_file = os.path.join(dest_dir, "steam_appid.txt")
                    with open(appid_file, "w", encoding="utf-8") as f:
                        f.write(str(appid))
                    self.logger.info(f"Wrote steam_appid.txt to {appid_file}")
                except OSError as e:
                    self.logger.warning(f"Failed to write steam_appid.txt: {e}")

                processed += 1

            self.logger.info(f"Applied Goldberg files to {processed} folder(s).")
            
            self.logger.info("Generating interface headers for Goldberg...")
            try:
                from managers.task_manager import TaskManager
                
                for dest_dir in found_dirs:
                    valve_files = []
                    for fname in os.listdir(dest_dir):
                        fname_lower = fname.lower()
                        if fname_lower.endswith(".valve") and "steam_api" in fname_lower:
                            is_64bit = "64" in fname_lower
                            valve_files.append((os.path.join(dest_dir, fname), is_64bit))
                    
                    if valve_files:
                        steam_settings_dir = os.path.join(dest_dir, "steam_settings")
                        
                        for valve_path, is_64bit in valve_files:
                            if is_windows and not valve_path.lower().endswith(".dll.valve"):
                                continue
                            
                            fname_lower = valve_path.lower()
                            if not (fname_lower.endswith(".dll.valve") or fname_lower.endswith(".so.valve")):
                                continue
                            
                            success = TaskManager._run_generate_interfaces_for_file(
                                dest_dir,
                                valve_path,
                                is_64bit,
                                goldberg_src,
                            )
                            
                            if success:
                                interfaces_file = os.path.join(dest_dir, "steam_interfaces.txt")
                                if os.path.exists(interfaces_file):
                                    try:
                                        os.makedirs(steam_settings_dir, exist_ok=True)
                                        dest_interfaces = os.path.join(steam_settings_dir, "steam_interfaces.txt")
                                        shutil.move(interfaces_file, dest_interfaces)
                                        self.logger.info(f"Moved steam_interfaces.txt to {steam_settings_dir}")
                                    except OSError as e:
                                        self.logger.warning(f"Failed to move steam_interfaces.txt: {e}")
            except Exception as e:
                self.logger.warning(f"Failed to generate interfaces: {e}")
            
            return True

        except (OSError, shutil.Error) as e:
            self.logger.exception(f"Error applying Goldberg: {e}")
            return False

    def _set_linux_permissions(self, game_directory: str):
        """Set executable permissions for Linux binaries."""
        self.logger.info(f"Setting executable permissions in: {game_directory}")

        linux_binary_extensions = {".sh", ".x86", ".x86_64", ".bin"}
        elf_magic = b"\x7fELF"
        chmod_count = 0

        for root, dirs, files in os.walk(game_directory):
            for file in files:
                file_path = os.path.join(root, file)
                file_lower = file.lower()

                should_chmod = False
                if any(file_lower.endswith(ext) for ext in linux_binary_extensions):
                    should_chmod = True
                elif "." not in file:
                    try:
                        file_size = os.path.getsize(file_path)
                        if file_size >= 1024:
                            with open(file_path, "rb") as f:
                                if f.read(4) == elf_magic:
                                    should_chmod = True
                    except (IOError, OSError):
                        continue

                if should_chmod:
                    try:
                        current_mode = os.stat(file_path).st_mode
                        if not (current_mode & 0o111):
                            os.chmod(file_path, current_mode | 0o755)
                            chmod_count += 1
                    except OSError:
                        pass

        if chmod_count > 0:
            self.logger.info(
                f"Set executable permissions for {chmod_count} Linux binaries"
            )

    def _add_appids_to_slssteam_config(self):
        """Add downloaded AppIDs to SLSsteam config.yaml on Linux"""
        from utils.yaml_config_manager import (
            get_user_config_path,
            add_additional_app,
            add_dlc_data,
        )

        if not self.game_data:
            return

        try:
            config_path = get_user_config_path()
            if not config_path.exists():
                return

            main_appid = self.game_data.get("appid")
            game_name = self.game_data.get("game_name", "")
            if main_appid:
                from utils.dlc_helpers import sync_dlc_only_sls_config
                sync_dlc_only_sls_config(config_path, str(main_appid), game_name, self.game_data)

            self.logger.info("AppIDs added to SLSsteam config")
        except (OSError, ValueError) as e:
            self.logger.warning(f"Failed to add AppIDs to SLSsteam config: {e}")

    def _run_steamless(self):
        """Run Steamless DRM removal"""
        if not self.current_dest_path or not self.game_data:
            return

        game_directory = get_game_directory(self.current_dest_path, self.game_data)

        if not os.path.exists(game_directory):
            return

        from core.tasks.steamless_task import SteamlessTask

        self.logger.info("Starting Steamless DRM Removal...")
        self.logger.info(f"Processing directory: {game_directory}")

        steamless_task = SteamlessTask()
        steamless_task.progress.connect(self.logger.info)

        loop = QEventLoop()
        steamless_task.result.connect(lambda success: loop.quit())
        steamless_task.finished.connect(loop.quit)
        steamless_task.set_game_directory(game_directory)
        steamless_task.start()
        loop.exec()

        self.logger.info("Steamless processing completed")

    def _run_achievement_generation(self):
        """Generate achievements using SLScheevo"""
        if not self.game_data:
            return

        app_id = self.game_data.get("appid")
        if not app_id:
            return

        from core.tasks.generate_achievements_task import GenerateAchievementsTask

        self.logger.info("Generating achievements...")

        achievement_task = GenerateAchievementsTask()
        achievement_task.progress.connect(self.logger.info)

        loop = QEventLoop()

        def on_achievement_complete(result):
            if result and result.get("success"):
                self.logger.info(
                    f"Achievement generation completed: {result.get('message')}"
                )
            else:
                self.logger.info(
                    f"Achievement generation failed: {result.get('message') if result else 'Unknown error'}"
                )

        achievement_runner = TaskRunner()
        achievement_runner.cleanup_complete.connect(loop.quit)
        achievement_runner.run(achievement_task.run, app_id).finished.connect(
            lambda r: on_achievement_complete(r)
        )
        loop.exec()

    def _create_greenluma_files(self):
        """Create GreenLuma AppList files on Windows"""
        if not self.game_data:
            return

        from core import steam_helpers
        from utils.yaml_config_manager import is_slssteam_config_management_enabled

        try:
            # Check if config management is enabled
            if not is_slssteam_config_management_enabled():
                self.logger.debug(
                    "GreenLuma config management is disabled, skipping AppList file creation"
                )
                return

            steam_path = steam_helpers.find_steam_install()
            if not steam_path:
                self.logger.error(
                    "Could not find Steam path. Skipping GreenLuma file creation."
                )
                return

            app_list_dir = os.path.join(steam_path, "AppList")
            if not os.path.exists(app_list_dir):
                os.makedirs(app_list_dir)
                self.logger.info(f"Created AppList directory at: {app_list_dir}")

            game_appid = self.game_data.get("appid")
            if not game_appid:
                return

            # Create main AppList file
            self._create_applist_file(app_list_dir, game_appid)

            # Create DLC AppList files
            selected_dlcs: list = self.game_data.get("selected_dlcs", [])
            for dlc_id in selected_dlcs:
                self._create_applist_file(app_list_dir, dlc_id, is_dlc=True)

            # Copy NoQuestion.bin and StealthMode.bin
            source_dir = Paths.deps()
            for filename in ["NoQuestion.bin", "StealthMode.bin"]:
                source_path = os.path.join(source_dir, filename)
                dest_path = os.path.join(steam_path, filename)
                try:
                    if os.path.exists(source_path):
                        if not os.path.exists(dest_path):
                            shutil.copy2(source_path, dest_path)
                            self.logger.info(f"Copied {filename} to Steam folder")
                        else:
                            self.logger.info(
                                f"{filename} already exists in Steam folder, skipping"
                            )
                    else:
                        self.logger.warning(
                            f"Source {filename} not found in deps folder"
                        )
                except (OSError, shutil.Error) as e:
                    self.logger.error(f"Failed to copy {filename}: {e}")

        except (OSError, shutil.Error, ValueError) as e:
            self.logger.error(f"Failed to create GreenLuma files: {e}", exc_info=True)

    def _create_applist_file(self, app_list_dir: str, appid: str, is_dlc: bool = False):
        """Helper function to create a GreenLuma AppList file."""
        if not app_id_exists_in_applist(app_list_dir, appid):
            next_num = find_next_applist_number(app_list_dir, self.logger)
            filepath = os.path.join(app_list_dir, f"{next_num}.txt")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(str(appid))
            log_msg = f"Created GreenLuma file: {filepath} for "
            log_msg += f"DLC: {appid}" if is_dlc else f"AppID: {appid}"
            self.logger.info(log_msg)
        else:
            log_msg = f"AppID {appid} already exists in AppList folder. Skipping file creation."
            if is_dlc:
                log_msg = f"DLC {log_msg}"
            self.logger.info(log_msg)
