import logging
import os
import hashlib
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import List, Tuple, Optional, Dict, Any

from PyQt6.QtCore import QObject, pyqtSignal

from utils.helpers import (
    resource_path,
    ensure_dotnet_availability,
    get_dotnet_path,
    get_dotnet_env,
)
from utils.settings import get_settings

# Third-party imports
try:
    import psutil
except ImportError:
    logging.critical(
        "Failed to import 'psutil'. Pausing/resuming downloads will not work."
    )
    psutil = None

logger = logging.getLogger(__name__)


class DownloadDepotsTask(QObject):
    """
    Manages the downloading of Steam depots. Handles process management,
    progress tracking, and pause/resume functionality.
    """

    progress = pyqtSignal(str)
    progress_percentage = pyqtSignal(int)
    speed_update = pyqtSignal(str)
    completed = pyqtSignal()
    error = pyqtSignal(tuple)

    def __init__(self):
        super().__init__()
        self._is_running = True
        self.percentage_regex = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)%")
        self.last_percentage = -1
        self.process: Optional[subprocess.Popen] = None

        self.total_download_size_for_this_job = 0
        self.completed_so_far_for_this_job = 0
        self.current_depot_size = 0
        self._last_log_time = 0
        self._log_buffer = []
        self.temp_file_list = None
        self._last_speed_calc_time = 0.0
        self._last_downloaded_bytes = 0.0
        self._smooth_speed_bps = 0.0
        self._is_validating = False
        self._lancache_error_detected = False

    @property
    def is_running_flag(self) -> bool:
        """Property to access the private running state safely."""
        return self._is_running

    def run(
        self, game_data: Dict[str, Any], selected_depots: List[str], dest_path: str
    ):
        """
        Main execution method to download selected depots.
        """
        logger.info(f"Download task starting for {len(selected_depots)} depots.")
        current_cmd: Optional[List[str]] = None

        try:
            # Check for .NET 9 availability before proceeding (will attempt auto-install if missing)
            self.progress.emit("Checking .NET 9 runtime availability...")
            if not ensure_dotnet_availability():
                self.progress.emit(
                    "ERROR: .NET 9 runtime is required and could not be installed automatically."
                )
                logger.critical(".NET 9 runtime not available")
                self.error.emit((RuntimeError, ".NET 9 runtime not available", None))
                return

            commands, skipped_depots, depot_sizes = self._prepare_downloads(
                game_data, selected_depots, dest_path
            )

            if not commands:
                self.progress.emit(
                    "No valid download commands to execute. Task finished."
                )
                self.completed.emit()
                return

            total_depots = len(commands)
            self.total_download_size_for_this_job = sum(depot_sizes)
            self.completed_so_far_for_this_job = 0

            logger.info(
                f"Task tracking total download size: {self.total_download_size_for_this_job} bytes"
            )

            # Track sidecar writes across all depots — determines DD_DELTA vs DD_FULL
            _sidecar_written_count = 0

            for i, current_cmd in enumerate(commands):
                if not self._is_running:
                    logger.info("Download task stopping before next depot.")
                    break

                try:
                    depot_id = current_cmd[current_cmd.index("-depot") + 1]
                except (ValueError, IndexError):
                    depot_id = f"depot_{i}"
                self.current_depot_size = depot_sizes[i]

                self.progress.emit(
                    f"--- Starting download for depot {depot_id} "
                    f"({i + 1}/{total_depots}) [Size: {self.current_depot_size} bytes] ---"
                )
                self.last_percentage = -1

                # Determine creation flags for Windows to hide the console window
                creation_flags = 0
                if sys.platform == "win32":
                    creation_flags = subprocess.CREATE_NO_WINDOW

                # Use binary mode to handle \r correctly
                self.process = subprocess.Popen(
                    current_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=False,  # Binary mode
                    creationflags=creation_flags,
                    env=get_dotnet_env(),
                )

                # Read output directly in this thread
                self._read_process_output()

                # Close the stdout pipe now that the reader loop has exited.
                # The process is either finished or about to be killed — releasing
                # the read end of the pipe here frees the OS file descriptor
                # immediately rather than waiting for GC.
                if self.process and self.process.stdout:
                    try:
                        self.process.stdout.close()
                    except OSError:
                        pass

                # Flush any remaining logs
                self._flush_log_buffer()

                if not self._is_running:
                    if self.process and self.process.poll() is None:
                        self.process.terminate()
                    logger.info("Download task stopping because stop() was called.")
                    self.completed.emit()
                    return

                return_code = 0
                if self.process:
                    return_code = self.process.poll()
                    self.process = None

                # Automatic LanCache fallback: if LanCache was used and failed, retry directly
                if return_code != 0 and "-use-lancache" in current_cmd and self._is_running:
                    fallback_msg = (
                        f"⚠️ LanCache download issue encountered for depot {depot_id} (exit code {return_code}). "
                        "Automatically falling back to direct connection without LanCache..."
                    )
                    self.progress.emit(fallback_msg)
                    logger.warning(fallback_msg)

                    fallback_cmd = [arg for arg in current_cmd if arg != "-use-lancache"]
                    self.last_percentage = -1
                    self._is_validating = False
                    self._lancache_error_detected = False

                    self.process = subprocess.Popen(
                        fallback_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=False,
                        creationflags=creation_flags,
                        env=get_dotnet_env(),
                    )
                    self._read_process_output()

                    if self.process and self.process.stdout:
                        try:
                            self.process.stdout.close()
                        except OSError:
                            pass

                    self._flush_log_buffer()

                    if not self._is_running:
                        if self.process and self.process.poll() is None:
                            self.process.terminate()
                        logger.info("Download task stopping because stop() was called.")
                        self.completed.emit()
                        return

                    if self.process:
                        return_code = self.process.poll()
                        self.process = None

                if return_code != 0:
                    msg = (
                        f"Warning: DepotDownloader exited with code "
                        f"{return_code} for depot {depot_id}."
                    )
                    self.progress.emit(msg)
                    logger.warning(msg)
                else:
                    self.completed_so_far_for_this_job += self.current_depot_size
                    self._last_speed_calc_time = 0.0
                    self._smooth_speed_bps = 0.0
                    # Copy manifest and create .sha sidecar in .DepotDownloader to enable future delta updates
                    try:
                        # Safely extract manifest_id and manifest_file_path via flag names
                        manifest_idx = current_cmd.index("-manifest")
                        manifest_id = current_cmd[manifest_idx + 1]
                        mf_idx = current_cmd.index("-manifestfile")
                        manifest_file_path = current_cmd[mf_idx + 1]

                        dest_depot_downloader = os.path.join(self.download_dir, ".DepotDownloader")
                        os.makedirs(dest_depot_downloader, exist_ok=True)

                        dest_manifest_path = os.path.join(dest_depot_downloader, f"{depot_id}_{manifest_id}.manifest")

                        if os.path.exists(manifest_file_path):
                            shutil.copy2(manifest_file_path, dest_manifest_path)

                            # Calculate SHA1 hash of the manifest file
                            sha1 = hashlib.sha1()
                            with open(dest_manifest_path, "rb") as f:
                                while chunk := f.read(8192):
                                    sha1.update(chunk)

                            # Write the raw bytes of the SHA1 hash to the .sha file (as expected by DDM's Util.LoadManifestFromFile)
                            with open(dest_manifest_path + ".sha", "wb") as f:
                                f.write(sha1.digest())

                            _sidecar_written_count += 1
                            logger.info(f"Successfully copied manifest and created SHA sidecar for depot {depot_id} to enable delta patching.")
                        else:
                            logger.warning(f"Manifest file not found at {manifest_file_path}, skipping delta manifest setup.")
                    except Exception as e:
                        logger.warning(f"Failed to copy manifest for depot {depot_id}: {e}", exc_info=True)

            if skipped_depots:
                self.progress.emit(
                    f"Skipped {len(skipped_depots)} depots due to missing manifests: "
                    f"{', '.join(skipped_depots)}"
                )

            if not self._is_running:
                logger.info("Download task stopped before cleanup.")
                self.completed.emit()
                return

            self._copy_manifests_to_steam_depotcache()
            self._cleanup_temp_files()

            self.completed.emit()

        except FileNotFoundError:
            binary = "executable"
            if current_cmd:
                binary = current_cmd[0]

            error_msg = (
                f"ERROR: '{binary}' command not found. "
                "Ensure .NET Runtime is installed and 'dotnet' is in your PATH."
            )
            self.progress.emit(error_msg)
            logger.critical(f"'{binary}' not found.")
            self.error.emit((FileNotFoundError, f"'{binary}' not found", None))
            raise

        except (OSError, subprocess.SubprocessError) as e:
            self.progress.emit(f"An unexpected error occurred during download: {e}")
            logger.error(f"Download subprocess failed: {e}", exc_info=True)
            self.process = None
            self.error.emit((type(e), str(e), None))
            raise
    def _read_process_output(self):
        """Reads process output in chunks, splitting on \\r and \\n for progress updates."""
        if not self.process or not self.process.stdout:
            return

        buffer = bytearray()
        while self._is_running:
            if self.process is None:
                break

            # Read in chunks to avoid one syscall per byte
            try:
                chunk = self.process.stdout.read1(4096)
            except (OSError, AttributeError):
                try:
                    chunk = self.process.stdout.read(4096)
                except OSError:
                    break

            if not chunk:
                if self.process and self.process.poll() is not None:
                    break
                time.sleep(0.01)
                continue

            for byte in chunk:
                b = bytes([byte])
                if b == b"\r" or b == b"\n":
                    if buffer:
                        try:
                            line = buffer.decode("utf-8", errors="replace")
                            self._handle_downloader_output(line)
                        except UnicodeDecodeError:
                            pass
                        buffer.clear()
                else:
                    buffer.extend(b)

        # Process remaining buffer
        if buffer:
            try:
                line = buffer.decode("utf-8", errors="replace")
                self._handle_downloader_output(line)
            except UnicodeDecodeError:
                pass

    def _copy_manifests_to_steam_depotcache(self):
        """Copy downloaded manifests to Steam's central depotcache for native ACF creation via SLSsteam.

        Must be called BEFORE _cleanup_temp_files() because cleanup deletes
        /tmp/mistwalker_manifests/.  Without these manifests in Steam's depotcache,
        the install|appid|index API command cannot verify local files and Steam
        falls back to 'needs download' state (blue Install button).
        """
        try:
            from utils.slssteam_integration import _experimental_mode_enabled
            if not _experimental_mode_enabled():
                return
        except Exception:
            return

        from core.steam_helpers import find_steam_install
        temp_manifest_dir = os.path.join(tempfile.gettempdir(), "mistwalker_manifests")
        if not os.path.exists(temp_manifest_dir):
            return

        steam_path = find_steam_install()
        if not steam_path:
            return

        central_depotcache_dir = os.path.join(steam_path, "depotcache")
        try:
            os.makedirs(central_depotcache_dir, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create central depotcache directory: {e}")
            return

        try:
            for fname in os.listdir(temp_manifest_dir):
                if fname.endswith(".manifest"):
                    src = os.path.join(temp_manifest_dir, fname)
                    dst = os.path.join(central_depotcache_dir, fname)
                    shutil.copy2(src, dst)
                    logger.info(f"Copied manifest {fname} to Steam's central depotcache")
        except Exception as e:
            logger.error(f"Failed to copy manifests to central depotcache: {e}")

    def _cleanup_temp_files(self):
        """Cleans up temporary files created during the download process."""
        self.progress.emit("--- Cleaning up temporary files ---")
        temp_dir = tempfile.gettempdir()
        items_to_clean = {
            "mistwalker_keys.vdf": os.path.join(temp_dir, "mistwalker_keys.vdf"),
            "mistwalker_manifests": os.path.join(temp_dir, "mistwalker_manifests"),
        }

        for name, path in items_to_clean.items():
            if os.path.exists(path):
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                        self.progress.emit(f"Removed temp directory '{name}'.")
                    else:
                        os.remove(path)
                        self.progress.emit(f"Removed temp file '{name}'.")
                except OSError as e:
                    self.progress.emit(f"Error removing temp item '{name}': {e}")

        if self.temp_file_list and os.path.exists(self.temp_file_list):
            try:
                os.remove(self.temp_file_list)
                self.progress.emit("Removed temporary selective filelist.")
                self.temp_file_list = None
            except OSError as e:
                self.progress.emit(f"Error removing selective filelist temp file: {e}")

    def _flush_log_buffer(self):
        """Emits any buffered log lines."""
        if self._log_buffer:
            # Join buffered lines and emit as a single signal
            # This reduces the number of signals sent to the main thread
            combined_log = "\n".join(self._log_buffer)
            self.progress.emit(combined_log)
            self._log_buffer.clear()
            self._last_log_time = time.time()

    def _handle_downloader_output(self, line: str):
        """Parses output lines from the downloader to update progress."""
        if not self._is_running:
            return

        line = line.strip()
        if not line:
            return

        # Add to buffer
        self._log_buffer.append(line)

        # Check for LanCache CDN error lines
        if "Got CDN auth token=" in line and "result: Fail" in line:
            self._lancache_error_detected = True

        # Check validation phase vs real chunk download
        is_validation_line = line.startswith("Validating ") or line.startswith("Checking ")
        if is_validation_line:
            self._is_validating = True
            current_time = time.time()
            if current_time - self._last_speed_calc_time >= 1.0:
                self._last_speed_calc_time = current_time
                self._smooth_speed_bps = 0.0
                self.speed_update.emit("Validating local files... | ETA: Calculating...")

        # Check for percentage update
        match = self.percentage_regex.search(line)
        if match:
            self._is_validating = False
            try:
                percentage = float(match.group(1))
                current_time = time.time()

                if self.total_download_size_for_this_job > 0:
                    progress_of_current_depot = (
                        percentage / 100.0
                    ) * self.current_depot_size

                    total_progress_bytes = (
                        self.completed_so_far_for_this_job + progress_of_current_depot
                    )

                    total_percentage = int(
                        (total_progress_bytes / self.total_download_size_for_this_job)
                        * 100
                    )

                    total_percentage = max(0, min(100, total_percentage))

                    if total_percentage != self.last_percentage:
                        self.progress_percentage.emit(total_percentage)
                        self.last_percentage = total_percentage

                    # Real network chunk downloading phase
                    if self._last_speed_calc_time == 0.0:
                        self._last_speed_calc_time = current_time
                        self._last_downloaded_bytes = total_progress_bytes
                        self.speed_update.emit("Speed: 0.00 B/s | ETA: Calculating...")
                    elif current_time - self._last_speed_calc_time >= 1.0:
                        elapsed = current_time - self._last_speed_calc_time
                        bytes_diff = total_progress_bytes - self._last_downloaded_bytes
                        self._last_downloaded_bytes = total_progress_bytes
                        self._last_speed_calc_time = current_time

                        # Guard against negative diffs (e.g. depot switch or validation transition)
                        if bytes_diff < 0:
                            bytes_diff = 0.0

                        inst_speed_bps = bytes_diff / elapsed

                        # Exponential moving average for smooth speed display (alpha = 0.35)
                        if self._smooth_speed_bps == 0.0:
                            self._smooth_speed_bps = inst_speed_bps
                        else:
                            self._smooth_speed_bps = 0.35 * inst_speed_bps + 0.65 * self._smooth_speed_bps

                        speed_bps = self._smooth_speed_bps
                        remaining_bytes = max(0.0, float(self.total_download_size_for_this_job - total_progress_bytes))

                        # Format Speed
                        if speed_bps < 1024:
                            speed_str = f"{speed_bps:.2f} B/s"
                        elif speed_bps < 1024**2:
                            speed_str = f"{(speed_bps / 1024):.2f} KB/s"
                        else:
                            speed_str = f"{(speed_bps / (1024**2)):.2f} MB/s"

                        # Format ETA (always included, never hidden)
                        if speed_bps > 1024:  # At least 1 KB/s to give meaningful ETA
                            eta_seconds = int(remaining_bytes / speed_bps)
                            if eta_seconds < 60:
                                eta_str = f"{eta_seconds}s remaining"
                            elif eta_seconds < 3600:
                                eta_str = f"{eta_seconds // 60}m {eta_seconds % 60}s remaining"
                            else:
                                eta_str = f"{eta_seconds // 3600}h {(eta_seconds % 3600) // 60}m remaining"
                        else:
                            eta_str = "Calculating..."

                        speed_display = f"Speed: {speed_str} | ETA: {eta_str}"
                        self.speed_update.emit(speed_display)
                        logger.debug(f"Download Progress: {total_percentage}% | {speed_display} | Completed: {total_progress_bytes:.0f} bytes")
                else:
                    # Fallback when total download size is unannounced
                    int_percentage = int(percentage)
                    if int_percentage != self.last_percentage:
                        self.progress_percentage.emit(int_percentage)
                        self.last_percentage = int_percentage

                    if self._last_speed_calc_time == 0.0:
                        self._last_speed_calc_time = current_time
                        self._last_downloaded_bytes = percentage
                        self.speed_update.emit("Downloading game files... | ETA: Calculating...")
                    elif current_time - self._last_speed_calc_time >= 1.0:
                        elapsed = current_time - self._last_speed_calc_time
                        pct_diff = percentage - self._last_downloaded_bytes
                        self._last_downloaded_bytes = percentage
                        self._last_speed_calc_time = current_time

                        if pct_diff > 0:
                            pct_per_sec = pct_diff / elapsed
                            remaining_pct = max(0.0, 100.0 - percentage)
                            eta_seconds = int(remaining_pct / pct_per_sec) if pct_per_sec > 0 else 0
                            if eta_seconds < 60:
                                eta_str = f"{eta_seconds}s remaining"
                            elif eta_seconds < 3600:
                                eta_str = f"{eta_seconds // 60}m {eta_seconds % 60}s remaining"
                            else:
                                eta_str = f"{eta_seconds // 3600}h {(eta_seconds % 3600) // 60}m remaining"
                        else:
                            eta_str = "Calculating..."

                        self.speed_update.emit(f"Downloading... ({percentage:.1f}%) | ETA: {eta_str}")
            except ValueError:
                pass

        # Check if we should flush the buffer
        is_important = "error" in line.lower() or "warning" in line.lower()
        current_time = time.time()

        # Flush if important message or time interval passed (80ms)
        if is_important or (current_time - self._last_log_time > 0.08):
            self._flush_log_buffer()

    def _prepare_downloads(
        self, game_data: Dict[str, Any], selected_depots: List[str], dest_path: str
    ) -> Tuple[List[List[str]], List[str], List[int]]:
        """
        Prepares the list of commands, identifies skipped depots, and gathers sizes.
        """
        temp_dir = tempfile.gettempdir()
        keys_path = os.path.join(temp_dir, "mistwalker_keys.vdf")
        manifest_dir = os.path.join(temp_dir, "mistwalker_manifests")

        self.progress.emit(f"Generating depot keys file at {keys_path}")
        appid_str = str(game_data.get("appid", ""))
        cached_dkm_keys = None

        with open(keys_path, "w") as f:
            for depot_id in selected_depots:
                depots_map = game_data.get("depots", {})
                depot_info = depots_map.get(str(depot_id))
                if depot_info is None and str(depot_id).isdigit():
                    depot_info = depots_map.get(int(depot_id))
                if not isinstance(depot_info, dict):
                    depot_info = {}

                key = depot_info.get("key")

                # Fallback: query DepotKeyManager directly if key is missing from in-memory game_data
                if not key and appid_str:
                    try:
                        if cached_dkm_keys is None:
                            from managers.depot_key_manager import DepotKeyManager
                            cached_dkm_keys = DepotKeyManager().get_depot_keys(appid_str) or {}
                        key = cached_dkm_keys.get(str(depot_id))
                        if key:
                            logger.info(
                                f"[DownloadDepotsTask] Recovered missing key for depot {depot_id} "
                                f"from DepotKeyManager"
                            )
                    except Exception as e:
                        logger.warning(
                            f"[DownloadDepotsTask] Failed to query DepotKeyManager for depot {depot_id}: {e}"
                        )

                if key:
                    f.write(f"{depot_id};{key}\n")
                else:
                    logger.warning(
                        f"[DownloadDepotsTask] No decryption key available for depot {depot_id}"
                    )

        from utils.steam_manifest import get_install_folder_name
        install_folder_name = get_install_folder_name(game_data)

        download_dir = os.path.join(
            dest_path, "steamapps", "common", install_folder_name
        )
        self.download_dir = download_dir
        os.makedirs(download_dir, exist_ok=True)
        self.progress.emit(f"Download destination set to: {download_dir}")

        # Use dotnet to run the .NET 9 DLL (multiplatform, like Steamless)
        # Get the full path to dotnet, checking both PATH and default install location
        dotnet_path = get_dotnet_path()
        if not dotnet_path:
            raise RuntimeError(
                "dotnet command not found. Please install .NET 9 runtime manually."
            )
        dotnet_cmd = dotnet_path
        dll_path = resource_path(os.path.join("deps", "DepotDownloader.dll"))

        # Get max downloads from settings
        settings = get_settings()
        max_downloads = settings.value("max_downloads", 4, type=int)

        commands = []
        skipped_depots = []
        depot_sizes = []

        for depot_id in selected_depots:
            manifest_id = game_data["manifests"].get(depot_id)
            if not manifest_id:
                self.progress.emit(
                    f"Warning: No manifest ID for depot {depot_id}. Skipping."
                )
                skipped_depots.append(str(depot_id))
                continue

            try:
                size_str = game_data["depots"][depot_id].get("size")
                if size_str:
                    depot_sizes.append(int(size_str))
                else:
                    depot_sizes.append(0)
                    self.progress.emit(
                        f"Warning: No size data for depot {depot_id}. "
                        "Total progress may be inaccurate."
                    )
            except (ValueError, TypeError):
                depot_sizes.append(0)
                self.progress.emit(
                    f"Warning: Invalid size data for depot {depot_id}. "
                    "Total progress may be inaccurate."
                )

            manifest_file_path = os.path.join(
                manifest_dir, f"{depot_id}_{manifest_id}.manifest"
            )

            # Fallback 1: check if the manifest exists in the library's local depotcache folder
            if not os.path.exists(manifest_file_path) or os.path.getsize(manifest_file_path) == 0:
                local_depotcache_path = os.path.join(dest_path, "depotcache", f"{depot_id}_{manifest_id}.manifest")
                if os.path.exists(local_depotcache_path) and os.path.getsize(local_depotcache_path) > 0:
                    try:
                        import shutil
                        os.makedirs(manifest_dir, exist_ok=True)
                        shutil.copy(local_depotcache_path, manifest_file_path)
                        self.progress.emit(f"Recovered manifest from local depotcache: {depot_id}_{manifest_id}.manifest")
                    except Exception as e:
                        self.progress.emit(f"Warning: Failed to copy local depotcache manifest: {e}")

            # Fallback 2: if manifest is missing or empty, download and extract it from Hubcap API
            if not os.path.exists(manifest_file_path) or os.path.getsize(manifest_file_path) == 0:
                self.progress.emit(f"Manifest file {os.path.basename(manifest_file_path)} is missing/invalid. Requesting fallback download from Hubcap...")
                try:
                    from core import morrenus_api
                    import zipfile
                    
                    app_id = str(game_data["appid"])
                    target_branch = game_data.get("branch") or settings.value(f"selected_branch/{app_id}", "public", type=str)
                    fpath, err = morrenus_api.download_manifest(app_id, target_branch)
                    if fpath and os.path.exists(fpath):
                        with zipfile.ZipFile(fpath, "r") as zip_ref:
                            for item_name in zip_ref.namelist():
                                if item_name.endswith(".manifest"):
                                    dest_item_path = os.path.join(manifest_dir, os.path.basename(item_name))
                                    with open(dest_item_path, "wb") as mf:
                                        mf.write(zip_ref.read(item_name))
                                    self.progress.emit(f"Successfully extracted fallback manifest: {os.path.basename(dest_item_path)}")
                except Exception as e:
                    self.progress.emit(f"Warning: Failed to fetch fallback manifest from Hubcap: {e}")

            cmd_args = [
                dotnet_cmd,
                dll_path,
                "-app",
                str(game_data["appid"]),
                "-depot",
                str(depot_id),
                "-manifest",
                str(manifest_id),
                "-manifestfile",
                manifest_file_path,
                "-depotkeys",
                keys_path,
                "-max-downloads",
                str(max_downloads),
                "-dir",
                download_dir,
                "-validate",
            ]

            # Check selected branch
            target_branch = game_data.get("branch") or settings.value(f"selected_branch/{game_data['appid']}", "public", type=str)
            if target_branch and target_branch != "public":
                cmd_args.extend(["-branch", str(target_branch)])

            # 1. LanCache support (configurable via settings; falls back to direct if LanCache fails)
            use_lancache = settings.value("use_lancache", True, type=bool)
            if use_lancache:
                cmd_args.append("-use-lancache")

            # 2. LoginID session isolation (randomized 32-bit integer)
            login_id = random.randint(1, 2147483647)
            cmd_args.extend(["-loginid", str(login_id)])

            # 3. Selective files list support (-filelist)
            selected_files = game_data.get("selected_files_list")
            if selected_files:
                # Write filelist to temp file
                fl_path = os.path.join(temp_dir, f"selective_filelist_{depot_id}.txt")
                try:
                    with open(fl_path, "w", encoding="utf-8") as fl:
                        for file_path in selected_files:
                            # Normalize paths to use forward slashes for DDM
                            fl.write(file_path.replace("\\", "/") + "\n")
                    self.temp_file_list = fl_path
                    cmd_args.extend(["-filelist", fl_path])
                    self.progress.emit(f"Using selective filelist: {len(selected_files)} file(s) checked")
                except OSError as e:
                    self.progress.emit(f"Warning: Failed to write selective filelist: {e}")

            commands.append(cmd_args)

        return commands, skipped_depots, depot_sizes

    def stop(self):
        """Signals the task to stop."""
        logger.debug("Stop signal received by download task.")
        self._is_running = False

    def toggle_pause(self, pause: bool):
        """
        Pauses or resumes the download process tree.
        """
        if not psutil:
            logger.error("psutil not found. Cannot pause or resume.")
            raise RuntimeError("psutil library is not loaded.")

        if not self.process:
            logger.warning("Attempted to pause/resume, but no process is running.")
            return

        target_action = "pausing" if pause else "resuming"

        try:
            parent = psutil.Process(self.process.pid)
            children = parent.children(recursive=True)
            processes = [parent] + children

            for proc in processes:
                try:
                    if pause:
                        proc.suspend()
                    else:
                        proc.resume()
                except psutil.NoSuchProcess:
                    logger.warning(f"Process {proc.pid} no longer exists. Skipping.")

            result_status = "paused" if pause else "resumed"
            logger.info(f"Download process tree {result_status}.")

        except psutil.NoSuchProcess:
            logger.error(
                f"Main process {self.process.pid} not found. Cannot pause/resume."
            )
            self.process = None
        except psutil.Error as e:
            logger.error(f"An error occurred while {target_action} process: {e}")
            raise
