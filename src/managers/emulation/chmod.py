"""Execute-permission fixing for Linux games (Steam Linux Runtime helper).

Extracted from ``managers/task_manager.py``. Composed back into TaskManager via
``ChmodMixin`` so the public API is unchanged.
"""

import logging
import os
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

logger = logging.getLogger(__name__)


class ChmodMixin:
    """Recursively repairs execute permissions on a game directory."""

    def run_chmod_for_game(
        self, game_directory: str, game_name: str, show_dialog: bool = False
    ):
        logger.info(f"Starting chmod for game: {game_name}")

        def chmod_worker():
            count = self._run_chmod_recursive(game_directory)
            logger.info(f"Chmod completed: {count} files processed")

            if show_dialog:
                # Deferred import
                from ui.dialogs.chmod_resume import ChmodResumeDialog

                def show():
                    self._show_chmod_resume_dialog(game_name, count, ChmodResumeDialog)

                QTimer.singleShot(0, show)

        threading.Thread(target=chmod_worker, daemon=True).start()
    def _ensure_game_directory(self, game_directory: str, show_dialog: bool) -> bool:
        if game_directory and os.path.exists(game_directory):
            return True
        if show_dialog:
            QMessageBox.warning(
                self.main_window,
                "Directory Not Found",
                f"Game directory not found: {game_directory}",
            )
        return False
    @staticmethod
    def _detect_elf_architecture(file_path: str) -> Optional[str]:
        """
        Detect if an ELF file is 32-bit or 64-bit.
        Returns '32', '64', or None if not detectable.
        """
        try:
            with open(file_path, "rb") as f:
                # ELF magic bytes
                magic = f.read(4)
                if magic != b"\x7fELF":
                    return None

                # Byte 4 = class (1 = 32-bit, 2 = 64-bit)
                elf_class = f.read(1)
                if elf_class == b"\x01":
                    return "32"
                elif elf_class == b"\x02":
                    return "64"
        except (IOError, OSError):
            pass
        return None
    @staticmethod
    def _run_chmod_recursive(game_directory) -> int:
        linux_binary_extensions = {
            ".sh",
            ".bash",
            ".x86",
            ".x86_64",
            ".bin",
            ".run",
            ".elf",
            ".pck",
        }
        elf_magic = b"\x7fELF"
        shebang_magic = b"#!"

        chmod_count = 0

        for root, _, filenames in os.walk(game_directory):
            for filename in filenames:
                file_path = os.path.join(root, filename)

                if os.path.islink(file_path):
                    continue

                should_chmod = False
                filename_lower = filename.lower()

                if any(filename_lower.endswith(ext) for ext in linux_binary_extensions):
                    should_chmod = True
                elif "." not in filename:
                    try:
                        with open(file_path, "rb") as f:
                            header = f.read(4)
                            if header.startswith(elf_magic) or header.startswith(
                                shebang_magic
                            ):
                                should_chmod = True
                    except (IOError, OSError):
                        continue

                if should_chmod:
                    try:
                        file_stat = os.stat(file_path)
                        current_mode = file_stat.st_mode
                        if not (current_mode & stat.S_IXUSR):
                            new_mode = current_mode | 0o755
                            os.chmod(file_path, new_mode)
                            chmod_count += 1
                    except OSError:
                        pass

        return chmod_count
    def _show_chmod_resume_dialog(self, game_name: str, file_count: int, dialog_class):
        dialog = dialog_class(
            game_name=game_name,
            file_count=file_count,
            success=True,
            parent=self.main_window,
        )
        dialog.exec()
