"""Steamless emulator task orchestration.

Extracted from ``managers/task_manager.py``. Composed back into TaskManager via
``SteamlessMixin`` so the public API is unchanged.
"""

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QMetaObject, QTimer, Qt

from core.tasks.steamless_task import SteamlessTask
from utils.steam_manifest import get_game_directory

logger = logging.getLogger(__name__)


class SteamlessMixin:
    """Runs the Steamless emulator for a game and reports its result."""

    def _create_steamless_task(self, progress_handler):
        self.steamless_task = SteamlessTask()
        self.steamless_task.use_aio = True
        self.steamless_task.progress.connect(progress_handler)
        self.steamless_task.result.connect(self._on_steamless_complete)
        self.steamless_task.finished.connect(self._on_steamless_finished)
        self.steamless_task.error.connect(self._handle_steamless_task_error)
        return self.steamless_task
    def _reset_steamless_task(self):
        if self.steamless_task:
            if self.steamless_task.isRunning():
                self.steamless_task.stop()
                self.steamless_task.wait(2000)
            self.steamless_task = None
    def _start_steamless_processing(self, use_aio=True):
        if not self.current_dest_path or not self.game_data:
            self._finalize_job_logic()
            return

        game_directory = get_game_directory(self.current_dest_path, self.game_data)

        if not os.path.exists(game_directory):
            self._finalize_job_logic()
            return

        self._reset_steamless_task()

        logger.info("\n" + "=" * 40)
        logger.info("Starting Steamless DRM Removal...")

        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.set_stage_status("steamless", "in_progress")

        steamless_task = self._create_steamless_task(self._on_steamless_progress)
        steamless_task.use_aio = True
        steamless_task.set_game_directory(game_directory)
        steamless_task.start()

        self._steamless_ran = True
        self._update_status_button_color()
    def run_steamless_manually(self, exe_path: str, game_name: Optional[str] = None):
        self._reset_steamless_task()

        self._steamless_game_name = game_name or os.path.basename(exe_path)
        self._steamless_progress_log = []
        self._steamless_manual_run = True

        logger.info(f"Starting manual Steamless (.NET CLI) processing for: {exe_path}")
        steamless_task = self._create_steamless_task(self._on_steamless_progress)
        steamless_task.use_aio = False
        steamless_task.set_target_exe(exe_path)
        steamless_task.start()
    def run_steamless_aio_manually(self, exe_path: str, game_name: Optional[str] = None):
        self._reset_steamless_task()

        self._steamless_game_name = game_name or os.path.basename(exe_path)
        self._steamless_progress_log = []
        self._steamless_manual_run = True

        logger.info(f"Starting manual Steamless (Python AIO) processing for: {exe_path}")
        steamless_task = self._create_steamless_task(self._on_steamless_progress)
        steamless_task.use_aio = True
        steamless_task.set_target_exe(exe_path)
        steamless_task.start()
    def run_steamless_for_game(self, game_directory: str, game_name: str):
        self._reset_steamless_task()

        self._steamless_game_name = game_name
        self._steamless_progress_log = []
        self._steamless_manual_run = True

        logger.info(f"Starting manual Steamless (.NET CLI) processing for game: {game_name}")
        steamless_task = self._create_steamless_task(self._on_steamless_progress)
        steamless_task.use_aio = False
        steamless_task.set_game_directory(game_directory)
        steamless_task.start()
    def run_steamless_aio_for_game(self, game_directory: str, game_name: str):
        self._reset_steamless_task()

        self._steamless_game_name = game_name
        self._steamless_progress_log = []
        self._steamless_manual_run = True

        logger.info(f"Starting manual Steamless AIO processing for game: {game_name}")
        steamless_task = self._create_steamless_task(self._on_steamless_progress)
        steamless_task.use_aio = True
        steamless_task.set_game_directory(game_directory)
        steamless_task.start()
    def _on_steamless_progress(self, message):
        self._steamless_progress_log.append(message)
        logger.info(message)
    def _on_steamless_complete(self, success):
        logger.info("\n" + "=" * 40)
        if success:
            logger.info("Steamless processing completed successfully")
        else:
            logger.info("Steamless processing completed with warnings or no DRM found")


        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.set_stage_status("steamless", "completed" if success else "error")

        self._steamless_success = success
        self._last_steamless_success = success
    def _on_steamless_finished(self):
        if self.steamless_task:
            QTimer.singleShot(0, self._clear_steamless_task)

        if self._steamless_manual_run:
            self._show_steamless_resume_dialog()
            self._steamless_manual_run = False
            self._steamless_success = None
            return

        if self._steamless_success is not None:
            self._steamless_success = None

        self._current_active_step = None

        QMetaObject.invokeMethod(
            self, "_finalize_job_logic", Qt.ConnectionType.QueuedConnection
        )
    def _clear_steamless_task(self):
        self.steamless_task = None
    def _show_steamless_resume_dialog(self):
        # Deferred import
        from ui.dialogs.steamless_resume import SteamlessResumeDialog

        exe_count = 0
        processed_count = 0
        had_error = self._steamless_error

        # If a single file was targeted directly, default exe_count to at least 1
        if self._steamless_game_name and self._steamless_game_name.lower().endswith(".exe"):
            exe_count = 1

        for message in self._steamless_progress_log:
            if "Found " in message and "executable(s)" in message:
                try:
                    parts = message.split()
                    for i, part in enumerate(parts):
                        if part == "Found" and i + 1 < len(parts):
                            exe_count = int(parts[i + 1])
                            break
                except ValueError:
                    pass

            if (
                "Successfully processed:" in message
                or "Successfully unpacked file!" in message
                or "Unpacked with SteamStub" in message
                or "[+] Unpacked with" in message
            ):
                processed_count += 1
                exe_count = max(exe_count, 1)

            if (
                "No Steam DRM detected" in message
                or "No variant matched" in message
                or "[-] No Steam DRM" in message
                or "[!] No variant matched" in message
            ):
                exe_count = max(exe_count, 1)

        # Fallback if _last_steamless_success was True
        if getattr(self, "_last_steamless_success", False) and not had_error:
            processed_count = max(processed_count, 1)
            exe_count = max(exe_count, 1)

        actual_success = (processed_count > 0 or getattr(self, "_last_steamless_success", False)) and not had_error

        dialog = SteamlessResumeDialog(
            game_name=self._steamless_game_name,
            exe_count=exe_count,
            processed_count=processed_count,
            success=actual_success,
            parent=self.main_window,
        )
        dialog.exec()
        self._steamless_progress_log = []
        self._steamless_game_name = ""
    def _handle_steamless_task_error(self, error_info):
        _, error_value, _ = error_info
        error_str = str(error_value)
        logger.error(f"Steamless processing failed: {error_str}")

        is_linux_no_drm = "no suitable game executables found" in error_str.lower()
        is_no_steam_drm = "no steam drm detected" in error_str.lower()

        if is_linux_no_drm or is_no_steam_drm:
            self._steamless_progress_log.append(error_str)
            self._steamless_error = False
            self._last_steamless_success = False
            if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
                self.main_window.simplified_terminal.set_stage_status("steamless", "completed")
        else:
            self._steamless_error = True
            if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
                self.main_window.simplified_terminal.set_stage_status("steamless", "error")

        if self.steamless_task:
            QTimer.singleShot(0, self._clear_steamless_task)

        self._current_active_step = None

        QMetaObject.invokeMethod(
            self, "_finalize_job_logic", Qt.ConnectionType.QueuedConnection
        )
    def _get_steamless_status_text(self):
        log_text = "\n".join(self._steamless_progress_log).lower()
        if "no suitable game executables found" in log_text:
            return "None (Linux Native)"
        if "no steam drm detected" in log_text or "(code 1)" in log_text:
            return "None (Clean, code 1)"
        if self._last_steamless_success is None:
            return "Ready"
        elif self._last_steamless_success:
            return "Success (code 0)"
        else:
            if "code 2" in log_text or "unpacking failed" in log_text:
                return "Failed (code 2)"
            match_code = re.search(r'exit code\s*:?\s*(\d+)', log_text)
            if match_code:
                return f"Error (code {match_code.group(1)})"
            return "Error"
    def parse_steamless_result(self) -> str:
        """Parse the steamless logs to determine what it did."""
        if not self._steamless_ran:
            return "Skipped"

        log_text = "\n".join(self._steamless_progress_log).lower()
        if "no suitable game executables found" in log_text:
            return "Skipped (Linux Native)"

        if "code 2" in log_text or "unpacking failed" in log_text:
            return "Failed (DRM Unpack Error, code 2)"

        if self._steamless_error or not self._last_steamless_success:
            match_code = re.search(r'exit code\s*:?\s*(\d+)', log_text)
            if match_code:
                return f"Failed (Error, code {match_code.group(1)})"
            return "Failed / Error"

        if "no steam drm detected" in log_text or "no drm found" in log_text or "not encrypted" in log_text or "(code 1)" in log_text:
            return "None (Clean, code 1)"

        # If successful, find the variant/version
        log_text_raw = "\n".join(self._steamless_progress_log)
        
        # Try AIO log format first: "[+] Unpacked with V3.0 ->"
        match = re.search(r'Unpacked with\s+V?([\d\.]+x?)', log_text_raw)
        if match:
            version = match.group(1)
            return f"Removed SteamStub v{version} (code 0)"

        # Fallback to older C# CLI format
        match = re.search(r'[Vv]ariant[\s:]+([\d\.]+)', log_text_raw)
        if match:
            version = match.group(1)
            return f"Removed SteamStub v{version} (code 0)"

        return "Removed DRM (code 0)"
