"""Goldberg emulation-runtime patching (apply / remove / restore).

Extracted from ``managers/task_manager.py``. Composed back into TaskManager via
``GoldbergMixin`` so the public API is unchanged.
"""

import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Set

from PyQt6.QtWidgets import QMessageBox

from utils.helpers import get_base_path
from utils.paths import Paths
from utils.steam_manifest import get_game_directory

logger = logging.getLogger(__name__)


class GoldbergMixin:
    """Patches an installed game with a local Goldberg emulation runtime."""

    _pending_component_prompt: Optional[str] = None

    def _prompt_missing_component(self, key: str) -> None:
        """Show the 'download it in Settings -> Tools' dialog for a component.

        The settings dialog imports this module's manager, so it is imported
        lazily to keep the dependency one-directional.
        """
        from ui.dialogs.settings_tabs.components_card import prompt_component_missing

        prompt_component_missing(self.main_window, key)

    def _finalize_goldberg(self, auto_apply: bool):
        # 5. Goldberg
        if not (auto_apply and not self.is_cancelling and self.current_dest_path):
            return

        # Goldberg is an optional download now. If the user asked for it
        # automatically but it is not installed, remember that so the GUI can
        # offer the download button once the job finishes - silently skipping
        # would leave the user wondering why the option did nothing.
        from utils.component_manager import is_component_available

        if not is_component_available("goldberg"):
            logger.info(
                "[Goldberg] 'Apply Goldberg automatically' is on but Goldberg is "
                "not downloaded; skipping and prompting after the job."
            )
            self._pending_component_prompt = "goldberg"
            return

        game_dir = get_game_directory(self.current_dest_path, self.game_data)

        try:
            self.apply_goldberg_to_game(
                game_directory=game_dir,
                appid=str(self.game_data.get("appid", "")),
                game_name=self.game_data.get("game_name", ""),
                show_dialog=False,
            )
        except OSError as e:
            logger.error(f"Error applying Goldberg: {e}")
    def apply_goldberg_to_game(
        self, game_directory: str, appid: str, game_name: str, show_dialog: bool = True
    ) -> bool:
        """Apply Goldberg emulator to a game directory."""
        logger.info(f"Applying Goldberg for game: {game_name} (AppID: {appid})")

        if not self._ensure_game_directory(game_directory, show_dialog):
            return False

        target_dirs = self._find_steam_api_dirs(game_directory)
        if not target_dirs:
            if show_dialog:
                QMessageBox.information(
                    self.main_window,
                    "No Steam API Files Found",
                    "No steam_api.dll, steam_api64.dll, libsteam_api.so or libsteam_api64.so files were found.",
                )
            return False

        goldberg_src = Paths.deps("Goldberg")
        if not goldberg_src.exists():
            if show_dialog:
                self._prompt_missing_component("goldberg")
            return False

        processed = 0
        for target_dir in target_dirs:
            try:
                self._apply_goldberg_to_single_dir(target_dir, appid, goldberg_src)
                processed += 1
            except Exception as e:
                logger.error(f"Failed to apply Goldberg in {target_dir}: {e}")

        if show_dialog:
            QMessageBox.information(
                self.main_window,
                "Apply Goldberg",
                f"Applied Goldberg files to {processed} folder(s).",
            )
        return True
    def remove_goldberg_from_game(
        self, game_directory: str, appid: str, game_name: str, show_dialog: bool = True
    ) -> bool:
        """Remove Goldberg emulator from a game directory."""
        logger.info(f"Removing Goldberg for game: {game_name}")

        if not self._ensure_game_directory(game_directory, show_dialog):
            return False

        target_dirs = self._find_steam_api_backup_dirs(game_directory)
        if not target_dirs:
            if show_dialog:
                QMessageBox.information(
                    self.main_window,
                    "No Backups Found",
                    "No .valve backup files were found.",
                )
            return False

        processed = 0
        for target_dir in target_dirs:
            try:
                self._remove_goldberg_from_single_dir(target_dir)
                processed += 1
            except Exception as e:
                logger.error(f"Failed to remove Goldberg from {target_dir}: {e}")

        if show_dialog:
            QMessageBox.information(
                self.main_window,
                "Remove Goldberg",
                f"Restored originals in {processed} folder(s).",
            )
        return True
    def _find_steam_api_dirs(self, root_dir: str) -> Set[str]:
        """Return set of directories containing any steam_api file."""
        targets = set()
        steam_api_names = {
            "steam_api.dll",
            "steam_api64.dll",
            "libsteam_api.so",
            "libsteam_api64.so",
        }
        for root, _, files in os.walk(root_dir):
            if any(fname.lower() in steam_api_names for fname in files):
                targets.add(root)
        return targets
    def _find_steam_api_backup_dirs(self, root_dir: str) -> Set[str]:
        """Return set of directories containing .valve backup files."""
        targets = set()
        backup_suffixes = (".dll.valve", ".so.valve")
        for root, _, files in os.walk(root_dir):
            if any(fname.lower().endswith(backup_suffixes) for fname in files):
                targets.add(root)
        return targets
    @staticmethod
    def _safe_remove(file_path: str) -> bool:
        """Remove a file safely, making it writable first if read-only."""
        if not os.path.exists(file_path):
            return True
        try:
            os.chmod(file_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        except Exception:
            pass
        try:
            os.remove(file_path)
            return True
        except Exception as e:
            logger.warning(f"Could not remove file {file_path}: {e}")
            return False
    @staticmethod
    def _safe_rmtree(dir_path: str) -> bool:
        """Safely remove a directory tree, fixing read-only permissions if necessary."""

        def _remove_readonly(func, p, excinfo):
            try:
                os.chmod(os.path.dirname(p), stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
            except Exception:
                pass
            try:
                os.chmod(p, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
            except Exception:
                pass
            try:
                func(p)
            except Exception as e:
                logger.debug(f"_safe_rmtree handler failed on {p}: {e}")

        if not os.path.exists(dir_path):
            return True

        # Pre-emptively make tree writable
        try:
            for root, dirs, files in os.walk(dir_path):
                for d in dirs:
                    try:
                        os.chmod(os.path.join(root, d), stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
                    except Exception:
                        pass
                for f in files:
                    try:
                        os.chmod(os.path.join(root, f), stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
                    except Exception:
                        pass
            os.chmod(dir_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        except Exception:
            pass

        try:
            try:
                shutil.rmtree(dir_path, onexc=_remove_readonly)
            except TypeError:
                shutil.rmtree(dir_path, onerror=_remove_readonly)
            return True
        except Exception as e:
            logger.error(f"Failed to remove directory {dir_path}: {e}")
            return False
    def _apply_goldberg_to_single_dir(
        self, target_dir: str, appid: str, goldberg_src: Path
    ):
        """Apply Goldberg to one directory."""
        renamed_files = self._backup_steam_api_files(target_dir)
        self._copy_goldberg_matching_files(target_dir, goldberg_src, renamed_files)
        self._copy_goldberg_common_files(target_dir, goldberg_src)
        self._write_appid_file(target_dir, appid)
        self._generate_interfaces_for_valve_files(target_dir, goldberg_src)
    def _backup_steam_api_files(self, directory: str) -> List[str]:
        """Rename all steam_api files to .valve. Return list of original filenames."""
        renamed = []
        patterns = [
            "steam_api.dll",
            "steam_api64.dll",
            "libsteam_api.so",
            "libsteam_api64.so",
        ]
        for name in patterns:
            src = os.path.join(directory, name)
            if os.path.exists(src):
                dst = src + ".valve"
                if not os.path.exists(dst):
                    try:
                        os.rename(src, dst)
                        renamed.append(name)
                    except Exception as e:
                        logger.error(f"Failed to rename {src} to {dst}: {e}")
                else:
                    # .valve backup already exists (e.g. re-applying Goldberg)
                    renamed.append(name)
        return renamed
    def _copy_goldberg_matching_files(
        self, target_dir: str, goldberg_src: Path, renamed_files: List[str]
    ):
        """For each renamed file, copy the Goldberg replacement from the goldberg deps folder."""
        for name in renamed_files:
            dst_file = os.path.join(target_dir, name)
            if os.path.exists(dst_file):
                try:
                    os.chmod(dst_file, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
                except Exception:
                    pass

            if name.endswith(".dll"):
                src = goldberg_src / "windows" / name
                if src.exists():
                    shutil.copy2(str(src), dst_file)
                    logger.info(f"Copied Goldberg {name} to {target_dir}")
                else:
                    logger.warning(f"Goldberg file not found: {src}")

            elif name.endswith(".so"):
                backup_path = os.path.join(target_dir, name + ".valve")
                if not os.path.exists(backup_path):
                    logger.error(f"Backup file missing: {backup_path}")
                    continue

                arch = self._detect_elf_architecture(backup_path)
                if arch is None:
                    logger.warning(
                        f"Could not detect architecture of {backup_path}, skipping"
                    )
                    continue

                if arch == "32":
                    src_filename = "libsteam_api.so"
                elif arch == "64":
                    src_filename = "libsteam_api64.so"
                else:
                    logger.warning(f"Unknown architecture '{arch}' for {backup_path}")
                    continue

                src = goldberg_src / "linux" / src_filename
                if src.exists():
                    shutil.copy2(str(src), dst_file)
                    logger.info(
                        f"Copied {src_filename} (detected {arch}-bit) to {name}"
                    )
                else:
                    logger.error(
                        f"Goldberg file not found: {src} (needed for {arch}-bit)"
                    )
            else:
                continue
    def _copy_goldberg_common_files(self, target_dir: str, goldberg_src: Path):
        """Copy steam_settings folder.
        Checks for custom user template in ~/.local/share/ACCELA/steam_settings,
        otherwise falls back to bundled Goldberg steam_settings.
        """
        user_custom_settings = Path(get_base_path()) / "steam_settings"
        if user_custom_settings.exists() and user_custom_settings.is_dir():
            src_settings = user_custom_settings
            logger.info(f"Using custom Goldberg steam_settings from {user_custom_settings}")
        else:
            src_settings = goldberg_src / "steam_settings"

        if src_settings.exists():
            dst_settings = os.path.join(target_dir, "steam_settings")
            if os.path.exists(dst_settings):
                self._safe_rmtree(dst_settings)
            try:
                shutil.copytree(str(src_settings), dst_settings)
            except Exception as e:
                logger.error(f"Failed to copy steam_settings to {dst_settings}: {e}")
    def _write_appid_file(self, target_dir: str, appid: str):
        """Write steam_appid.txt"""
        appid_path = os.path.join(target_dir, "steam_appid.txt")
        if os.path.exists(appid_path):
            try:
                os.chmod(appid_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
            except Exception:
                pass
        try:
            with open(appid_path, "w", encoding="utf-8") as f:
                f.write(str(appid))
        except OSError as e:
            logger.warning(f"Failed to write steam_appid.txt: {e}")
    def _generate_interfaces_for_valve_files(self, target_dir: str, goldberg_src: Path):
        """
        For each .valve file that is a steam_api backup, run generate_interfaces
        and move the resulting steam_interfaces.txt to steam_settings.
        """
        steam_settings_dir = os.path.join(target_dir, "steam_settings")
        for fname in os.listdir(target_dir):
            if not fname.lower().endswith((".dll.valve", ".so.valve")):
                continue
            if "steam_api" not in fname.lower():
                continue

            # Determine bitness from filename
            is_64bit = "64" in fname
            valve_path = os.path.join(target_dir, fname)
            if self._run_generate_interfaces_for_file(
                target_dir, valve_path, is_64bit, goldberg_src
            ):
                # Move generated interfaces file if it exists
                interfaces_src = os.path.join(target_dir, "steam_interfaces.txt")
                if os.path.exists(interfaces_src):
                    os.makedirs(steam_settings_dir, exist_ok=True)
                    interfaces_dst = os.path.join(
                        steam_settings_dir, "steam_interfaces.txt"
                    )
                    if os.path.exists(interfaces_dst):
                        self._safe_remove(interfaces_dst)
                    shutil.move(interfaces_src, interfaces_dst)
                    logger.info(f"Moved steam_interfaces.txt to {steam_settings_dir}")
    @staticmethod
    def _run_generate_interfaces_for_file(
        game_directory: str,
        valve_file_path: str,
        is_64bit: bool,
        goldberg_src: Optional[Path] = None,
    ) -> bool:
        """Run generate_interfaces for one .valve file. Return True on success."""
        if goldberg_src is None:
            goldberg_src = Paths.deps("Goldberg")
        is_windows = sys.platform == "win32"
        exe_name = f"generate_interfaces_x{'64' if is_64bit else '32'}"
        if is_windows:
            exe_name += ".exe"

        exe_path = goldberg_src / "genints" / exe_name
        if not exe_path.exists():
            logger.error(f"generate_interfaces executable not found: {exe_path}")
            return False

        valve_file_name = os.path.basename(valve_file_path)
        cmd = [str(exe_path), valve_file_name]

        try:
            result = subprocess.run(
                cmd,
                cwd=game_directory,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            if result.stdout:
                logger.info(result.stdout.strip())
            if result.stderr:
                logger.debug(f"generate_interfaces stderr: {result.stderr.strip()}")

            if result.returncode == 0:
                logger.info(f"Generate interfaces completed for {valve_file_name}")
                return True
            else:
                logger.error(
                    f"Generate interfaces failed (code {result.returncode}) for {valve_file_name}"
                )
                return False
        except Exception as e:
            logger.error(f"Error running generate_interfaces: {e}")
            return False
    def _remove_goldberg_from_single_dir(self, target_dir: str):
        """Remove Goldberg from one directory."""
        self._delete_goldberg_added_files(target_dir)
        self._restore_original_files(target_dir)
    def _delete_goldberg_added_files(self, target_dir: str):
        """Delete files/folders that were added by Goldberg."""
        # Remove steam_settings
        settings_path = os.path.join(target_dir, "steam_settings")
        if os.path.exists(settings_path):
            self._safe_rmtree(settings_path)

        # Remove steam_appid.txt
        appid_path = os.path.join(target_dir, "steam_appid.txt")
        if os.path.exists(appid_path):
            self._safe_remove(appid_path)

        # Remove any Goldberg DLLs/SOs that are not .valve
        for name in [
            "steam_api.dll",
            "steam_api64.dll",
            "libsteam_api.so",
            "libsteam_api64.so",
        ]:
            full = os.path.join(target_dir, name)
            if os.path.exists(full) and not os.path.exists(full + ".valve"):
                self._safe_remove(full)
    def _restore_original_files(self, target_dir: str):
        """Rename .valve backups back to original names."""
        for fname in os.listdir(target_dir):
            if not fname.lower().endswith((".dll.valve", ".so.valve")):
                continue
            original = fname[:-6]  # remove .valve
            backup_path = os.path.join(target_dir, fname)
            original_path = os.path.join(target_dir, original)
            if os.path.exists(original_path):
                self._safe_remove(original_path)
            try:
                os.rename(backup_path, original_path)
            except Exception as e:
                logger.error(f"Failed to restore {backup_path} to {original_path}: {e}")
