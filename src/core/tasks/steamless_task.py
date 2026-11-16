import logging
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QMutex, QObject, QThread, pyqtSignal

from utils.helpers import ensure_dotnet_availability, get_dotnet_path
from utils.paths import Paths
from utils.settings import get_settings

logger = logging.getLogger(__name__)

_AIO_VARIANT_RE = re.compile(r"\[\+\]\s*Unpacked with\s+(.+?)\s*(?:->|$)", re.IGNORECASE)
_CLI_VARIANT_RE = re.compile(
    r"(?:Unpacked with|Detected Packer:|Unpacking file with)\s+([^\r\n!]+)",
    re.IGNORECASE,
)


def _extract_steamstub_variant(line: str) -> Optional[str]:
    """Extract the reported SteamStub / packer name from Steamless output."""
    if not line:
        return None
    match = _AIO_VARIANT_RE.search(line)
    if match:
        variant = match.group(1).strip()
        return variant or None
    match = _CLI_VARIANT_RE.search(line)
    if match:
        variant = match.group(1).strip().rstrip(".")
        if variant and "file" not in variant.lower()[:6]:
            return variant
    return None


def _make_rel_path(file_path: str, base_dir: Optional[str]) -> str:
    """Return path relative to base_dir when inside it, otherwise basename."""
    if not base_dir:
        return os.path.basename(file_path)
    try:
        rel = os.path.relpath(file_path, base_dir)
        if not rel.startswith(".."):
            return rel
    except ValueError as e:
        logger.debug("Relative path computation fallback for %s: %s", file_path, e)
    return os.path.basename(file_path)


class SteamlessIntegration(QObject):
    """
    Integration module for Steamless CLI to remove Steam DRM from games.
    Uses .NET 9 runtime directly (no Wine/Proton needed).
    """

    progress = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal(bool)
    summary_ready = pyqtSignal(dict)

    def __init__(self, steamless_path: Optional[str] = None):
        super().__init__()
        self.steamless_path = steamless_path or str(Paths.deps("Steamless"))
        self.is_windows = sys.platform == "win32"
        self._current_process = None
        self._process_mutex = QMutex()
        self.use_aio = False
        self.engine_label = ""
        self._last_exe_status: Optional[str] = None
        self._last_exe_variant: Optional[str] = None
        self._last_exe_backup_path: Optional[str] = None
        self._last_exe_error: Optional[str] = None
        self._last_exe_returncode: Optional[int] = None
        self.last_summary: Optional[Dict[str, Any]] = None

        # Check for dotnet availability and get full path
        self.dotnet_path = get_dotnet_path()
        self.dotnet_available = self.dotnet_path is not None

    def get_engine_label(self) -> str:
        """Return a human-readable engine name for summary display."""
        if self.engine_label:
            return self.engine_label
        return "Steamless AIO (Python)" if getattr(self, "use_aio", False) else "Steamless (.NET CLI)"

    @staticmethod
    def build_summary(
        game_directory: str,
        executables: List[Dict[str, Any]],
        *,
        target_exe: Optional[str] = None,
        engine: str = "",
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a canonical structured summary from per-executable results."""
        fixed_count = sum(1 for item in executables if item.get("status") == "fixed")
        clean_count = sum(1 for item in executables if item.get("status") == "clean")
        failed_count = sum(1 for item in executables if item.get("status") == "failed")
        scanned_count = len(executables)

        if error and scanned_count == 0:
            overall_status = "failed"
        elif scanned_count == 0:
            overall_status = "no_executables"
        elif failed_count > 0 and fixed_count > 0:
            overall_status = "partial_failure"
        elif failed_count > 0:
            overall_status = "failed"
        elif fixed_count > 0:
            overall_status = "fixed"
        else:
            overall_status = "clean"

        return {
            "game_directory": game_directory,
            "target_exe": target_exe,
            "engine": engine,
            "scanned_count": scanned_count,
            "fixed_count": fixed_count,
            "clean_count": clean_count,
            "failed_count": failed_count,
            "overall_status": overall_status,
            "error": error,
            "executables": list(executables),
        }

    def _build_exe_entry(
        self,
        exe_path: str,
        game_directory: Optional[str],
        ok: bool,
        size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build a structured result row for the last evaluated executable."""
        if ok:
            status = "fixed"
        elif self._last_exe_status == "no_drm":
            status = "clean"
        else:
            status = "failed"

        resolved_size = size
        if resolved_size is None:
            try:
                resolved_size = os.path.getsize(exe_path)
            except OSError:
                resolved_size = 0

        return {
            "name": os.path.basename(exe_path),
            "path": exe_path,
            "rel_path": _make_rel_path(exe_path, game_directory),
            "size": int(resolved_size or 0),
            "status": status,
            "variant": self._last_exe_variant,
            "backup_path": self._last_exe_backup_path,
            "error": self._last_exe_error,
            "exit_code": self._last_exe_returncode,
        }

    def find_game_executables(self, game_directory: str) -> List[dict]:
        """
        Find all executable files in the game directory and subdirectories.
        Returns a list of .exe files sorted by priority.
        """
        try:
            if not os.path.exists(game_directory):
                logger.error(f"Game directory not found: {game_directory}")
                return []

            exe_files = []
            game_name = os.path.basename(game_directory.rstrip("/"))

            # Walk through all subdirectories
            logger.debug(f"Searching for executables in: {game_directory}")
            all_files_found = []

            try:
                for root, dirs, files in os.walk(game_directory):
                    try:
                        logger.debug(
                            f"Scanning directory: {root} - Found {len(files)} files"
                        )
                        all_files_found.extend(files)

                        for file in files:
                            try:
                                if file.lower().endswith(".exe"):
                                    file_path = os.path.join(root, file)
                                    logger.debug(f"Found executable: {file_path}")

                                    # Skip system/uninstaller files
                                    if self._should_skip_exe(file, file_path):
                                        logger.info(
                                            f"Skipping executable (system/utility): {file}"
                                        )
                                        continue

                                    # Get file size for priority calculation and size check
                                    try:
                                        file_size = os.path.getsize(file_path)
                                        # Additional check: ensure the file is not a broken symlink
                                        if file_size == 0 and os.path.islink(file_path):
                                            logger.warning(
                                                f"Skipping broken symlink: {file_path}"
                                            )
                                            continue
                                    except (OSError, FileNotFoundError) as e:
                                        logger.warning(
                                            f"Cannot access file {file_path}: {e}"
                                        )
                                        # Skip files we can't read (permissions, broken symlinks, etc.)
                                        continue

                                    # Skip very small files (likely utilities) - threshold lowered to 30KB
                                    if file_size < 30 * 1024:  # < 30KB
                                        logger.info(
                                            f"Skipping executable (too small, < 30KB): {file} ({file_size} bytes)"
                                        )
                                        continue

                                    exe_files.append(
                                        {
                                            "path": file_path,
                                            "name": file,
                                            "size": file_size,
                                            "priority": self._calculate_exe_priority(
                                                file, game_name, file_size
                                            ),
                                        }
                                    )
                                else:
                                    # Log non-exe files for debugging
                                    if file.lower().endswith((".dll", ".so", ".bin")):
                                        logger.debug(f"Found binary file: {file}")
                            except Exception as e:
                                logger.warning(
                                    f"Error processing file {file} in {root}: {e}"
                                )
                                continue
                    except Exception as e:
                        logger.warning(f"Error scanning directory {root}: {e}")
                        continue
            except Exception as e:
                logger.error(
                    f"Critical error during directory walk: {e}", exc_info=True
                )
                return []

            # Log summary of what was found
            exe_count = len([f for f in all_files_found if f.lower().endswith(".exe")])
            logger.debug(
                f"Directory scan complete. Total files: {len(all_files_found)}, EXE files: {exe_count}, After filtering: {len(exe_files)}"
            )

            if exe_count == 0:
                logger.warning(f"No .exe files found in {game_directory}")
                logger.debug(f"First 10 files found: {all_files_found[:10]}")
            elif len(exe_files) == 0:
                logger.warning(
                    f"Found {exe_count} .exe files but all were filtered out"
                )
                try:
                    for root, dirs, files in os.walk(game_directory):
                        for file in files:
                            if file.lower().endswith(".exe"):
                                logger.debug(
                                    f"Filtered EXE: {os.path.join(root, file)}"
                                )
                except OSError:
                    pass  # Don't crash on logging errors

            # Sort by priority (higher first)
            exe_files.sort(key=lambda x: x["priority"], reverse=True)

            if len(exe_files) == 0:
                logger.warning(f"No executables found in {game_directory}")
            else:
                logger.debug(
                    f"Found {len(exe_files)} executable(s) in {game_directory}"
                )
                for exe in exe_files[:3]:  # Log top 3 candidates only in debug
                    logger.debug(
                        f"  - {exe['name']} ({exe['size']} bytes, priority: {exe['priority']})"
                    )

            return exe_files  # Return full dictionaries with path, name, size, priority

        except Exception as e:
            logger.error(f"Critical error in find_game_executables: {e}", exc_info=True)
            return []

    def _ensure_aio_deps(self, python_exe: str) -> None:
        """
        Install pycryptodome and capstone into the tool's venv if missing.
        Silent on success; logs warnings on failure (AIO will still attempt to run).
        """
        deps = [
            ("Crypto", "pycryptodome"),
            ("capstone", "capstone"),
        ]
        for import_name, pip_name in deps:
            try:
                result = subprocess.run(
                    [python_exe, "-c", f"import {import_name}"],
                    capture_output=True,
                )
                if result.returncode != 0:
                    self.progress.emit(f"Installing {pip_name} for Steamless AIO (one-time setup)...")
                    install = subprocess.run(
                        [python_exe, "-m", "pip", "install", "--quiet", pip_name],
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if install.returncode == 0:
                        self.progress.emit(f"Installed {pip_name} OK")
                    else:
                        logger.warning(
                            f"pip install {pip_name} failed: {install.stderr.strip()}"
                        )
            except Exception as e:
                logger.warning(f"Failed to ensure AIO dep {pip_name}: {e}")

    @staticmethod
    def _should_skip_exe(filename: str, file_path: Optional[str] = None) -> bool:
        """Check if an executable should be skipped based on name patterns."""
        try:
            # NOTE FOR FUTURE AI:
            # All name-based regex skip patterns (launchers, unity, config, crash handlers, etc.)
            # were TEMPORARILY REMOVED per user request to ensure Steamless scans and evaluates
            # all .exe files without prematurely excluding any possible game executables or loaders.
            # Only Steamless's own backup files (.original.exe / .bak) are skipped to avoid infinite re-unpacking.
            if filename.lower().endswith((".original.exe", ".bak")):
                return True

            # Skip very small files (likely utilities) - threshold lowered to 30KB
            try:
                # Use full path if available, otherwise assume it's a relative path
                path_to_check = file_path if file_path else filename
                file_size = os.path.getsize(path_to_check)
                # Only skip if smaller than 30KB
                if file_size < 30 * 1024:  # < 30KB
                    return True
            except OSError:
                if file_path is None:
                    return True
                logger.debug(f"Cannot read file size for {filename}, but not skipping")
                return False

            return False
        except Exception as e:
            logger.warning(f"Error in _should_skip_exe for {filename}: {e}")
            return False  # Don't skip on error - let it be processed

    @staticmethod
    def _calculate_exe_priority(filename: str, game_name: str, file_size: int) -> int:
        """Calculate priority score for an executable file."""
        try:
            filename_lower = filename.lower()
            game_name_lower = game_name.lower()

            priority = 0

            # High priority: exact match with game name (remove spaces and special chars)
            game_name_clean = "".join(c for c in game_name_lower if c.isalnum())
            game_name_with_spaces = game_name_lower.replace(" ", "")

            if filename_lower.startswith(game_name_clean):
                priority += 100
            elif filename_lower.startswith(game_name_with_spaces):
                priority += 90
            elif game_name_clean in filename_lower:
                priority += 80  # Partial match still gets good priority
            elif game_name_with_spaces in filename_lower:
                priority += 70

            # Medium priority: common main executable names
            main_exe_patterns = ["game.exe", "main.exe", "play.exe", "start.exe"]
            if filename_lower in main_exe_patterns:
                priority += 50

            # Bonus for larger files (likely the main game)
            if file_size > 50 * 1024 * 1024:  # > 50MB
                priority += 30
            elif file_size > 10 * 1024 * 1024:  # > 10MB
                priority += 20
            elif file_size > 5 * 1024 * 1024:  # > 5MB
                priority += 10

            # Penalty for common non-game executables
            if any(
                word in filename_lower
                for word in ["editor", "tool", "config", "settings"]
            ):
                priority -= 20

            # High penalty for crash handlers and utilities
            if any(
                word in filename_lower
                for word in ["crash", "handler", "debug", "unitycrash"]
            ):
                priority -= 50

            # Very high penalty for Unity system files (extra safety)
            if any(
                word in filename_lower
                for word in ["unityplayer", "unity crash", "crash handler"]
            ):
                priority -= 100  # Effectively exclude these files

            return max(0, priority)
        except Exception as e:
            logger.warning(f"Error calculating priority for {filename}: {e}")
            return 0  # Return lowest priority on error

    def _reset_exe_state(self) -> None:
        """Clear per-executable tracking fields before evaluating one binary."""
        self._last_exe_status = None
        self._last_exe_variant = None
        self._last_exe_backup_path = None
        self._last_exe_error = None
        self._last_exe_returncode = None

    def _emit_error_summary(self, game_directory: str, message: str) -> None:
        """Record and emit a structured error summary before emitting error."""
        self.last_summary = self.build_summary(
            game_directory,
            [],
            engine=self.get_engine_label(),
            error=message,
        )
        self.summary_ready.emit(self.last_summary)
        self.error.emit(message)

    def process_game_with_steamless(self, game_directory: str) -> bool:
        """
        Main method to process a game directory with Steamless.
        Returns True if successful, False otherwise.
        """
        try:
            self.last_summary = None
            if not getattr(self, "use_aio", False):
                if not self.dotnet_available:
                    self._emit_error_summary(
                        game_directory,
                        ".NET 9 runtime is not available. Please install .NET 9 runtime.",
                    )
                    return False

                if not os.path.exists(self.steamless_path):
                    self._emit_error_summary(
                        game_directory,
                        f"Steamless is not installed (looked in {self.steamless_path}). "
                        "Download it from Settings -> Tools -> Optional Components.",
                    )
                    return False

                steamless_dll = os.path.join(self.steamless_path, "Steamless.CLI.dll")
                if not os.path.exists(steamless_dll):
                    steamless_dll = os.path.join(self.steamless_path, "Steamless.CLI")
                if not os.path.exists(steamless_dll):
                    self._emit_error_summary(
                        game_directory,
                        f"Steamless.CLI.dll not found in {self.steamless_path}",
                    )
                    return False

            # Validate game_directory is actually a directory
            if not os.path.isdir(game_directory):
                self._emit_error_summary(
                    game_directory,
                    f"Game path is not a directory: {game_directory}",
                )
                return False

            # Ensure game_directory is an absolute path
            if not os.path.isabs(game_directory):
                game_directory = os.path.abspath(game_directory)
                logger.debug(f"Converted to absolute path: {game_directory}")

            # Check if directory is readable
            if not os.access(game_directory, os.R_OK):
                self._emit_error_summary(
                    game_directory,
                    f"Game directory is not readable: {game_directory}",
                )
                return False

            self.progress.emit("Searching for game executables...")
            exe_files = self.find_game_executables(game_directory)

            if not exe_files:
                self._emit_error_summary(
                    game_directory,
                    "No suitable game executables found.",
                )
                return False

            # Process ALL executables - no limit
            self.progress.emit(f"Found {len(exe_files)} executable(s) to evaluate")

            # Log all candidates for user transparency
            for i, exe_info in enumerate(exe_files[:5]):  # Show top 5 candidates
                self.progress.emit(
                    f"  Candidate {i + 1}: {exe_info['name']} (priority: {exe_info['priority']}, size: {exe_info['size']:,} bytes)"
                )

            exe_results: List[Dict[str, Any]] = []

            # Process all executables without stopping on first success
            for i, exe_info in enumerate(exe_files):
                target_exe = exe_info["path"]
                exe_name = exe_info["name"]
                priority = exe_info["priority"]

                self.progress.emit(
                    f"Processing executable {i + 1}/{len(exe_files)}: {exe_name} (priority: {priority})"
                )

                self._reset_exe_state()
                ok = self._run_steamless_on_exe(target_exe)
                if ok:
                    self.progress.emit(f"Successfully processed: {exe_name}")
                exe_results.append(
                    self._build_exe_entry(
                        target_exe,
                        game_directory,
                        ok,
                        size=exe_info.get("size"),
                    )
                )

            summary = self.build_summary(
                game_directory,
                exe_results,
                engine=self.get_engine_label(),
            )
            self.last_summary = summary
            self.summary_ready.emit(summary)

            success_count = summary["fixed_count"]
            error_count = summary["failed_count"]

            # Final summary
            if error_count > 0:
                if success_count > 0:
                    msg = (
                        f"Steamless unpacked {success_count} executable(s), "
                        f"but failed on {error_count} executable(s)"
                    )
                else:
                    msg = f"Steamless completed with errors on {error_count} executable(s)"
                self.error.emit(msg)
                return False
            elif success_count > 0:
                self.progress.emit(
                    f"Steamless completed: {success_count}/{len(exe_files)} executables unpacked successfully (code 0)"
                )
                self.finished.emit(True)  # emit once for the whole game, not per-exe
                return True
            else:
                self.progress.emit(
                    f"No Steam DRM detected in any of the {len(exe_files)} evaluated executable(s) (code 1)"
                )
                self.finished.emit(True)
                return True

        except Exception as e:
            logger.error(
                f"Critical error in process_game_with_steamless: {e}", exc_info=True
            )
            self._emit_error_summary(
                game_directory,
                f"Unexpected error during Steamless processing: {str(e)}",
            )
            return False

    def _run_steamless_on_exe(self, exe_path: str) -> bool:
        """Run Steamless CLI on a specific executable."""
        try:
            target_path = exe_path

            if getattr(self, "use_aio", False):
                # ── Self-contained AIO path ──────────────────────────────────────
                # steamless.py is bundled in deps/ — no external script needed.
                steamless_py = str(Paths.deps("steamless.py"))
                if not os.path.exists(steamless_py):
                    err_msg = (
                        f"Bundled steamless.py not found at: {steamless_py}. "
                        "The AppImage may be corrupted — please reinstall."
                    )
                    self._last_exe_error = err_msg
                    self.error.emit(err_msg)
                    self._last_exe_status = "error"
                    return False

                # Use the tool's own venv Python or current interpreter
                from utils.helpers import get_venv_python
                python_exe = get_venv_python() or sys.executable
                if not python_exe or not os.path.exists(python_exe):
                    err_msg = (
                        "Cannot find the tool's Python interpreter. "
                        "Please reinstall ASSella."
                    )
                    self._last_exe_error = err_msg
                    self.error.emit(err_msg)
                    self._last_exe_status = "error"
                    return False

                # Ensure pycryptodome and capstone are installed (first-run only)
                self._ensure_aio_deps(python_exe)

                cmd = [python_exe, steamless_py, target_path]
                self.progress.emit(f"Running Steamless AIO (built-in): {os.path.basename(target_path)}")

                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    bufsize=0,
                )
            else:
                steamless_dll = os.path.join(self.steamless_path, "Steamless.CLI.dll")
                if not os.path.exists(steamless_dll):
                    steamless_dll = os.path.join(self.steamless_path, "Steamless.CLI")

                # Prepare command for dotnet
                dotnet_cmd = self.dotnet_path or (
                    "dotnet.exe" if self.is_windows else "dotnet"
                )
                cmd = [
                    dotnet_cmd,
                    steamless_dll,
                    "--quiet",
                    "--realign",
                    target_path,
                ]
                # Only add --recalcchecksum on Windows (imagehlp.dll is Windows-only)
                if self.is_windows:
                    cmd.append("--recalcchecksum")

                self.progress.emit(f"Running Steamless (.NET CLI): {os.path.basename(target_path)}")

                # Run Steamless CLI
                if self.is_windows:
                    # Windows: just run directly
                    creationflags = (
                        subprocess.CREATE_NO_WINDOW
                        if hasattr(subprocess, "CREATE_NO_WINDOW")
                        else 0
                    )
                    process = subprocess.Popen(
                        cmd,
                        cwd=self.steamless_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        bufsize=0,
                        creationflags=creationflags,
                    )
                else:
                    # Linux: run with dotnet
                    # Set DOTNET_ROOT if using ~/.dotnet
                    env = None
                    if self.dotnet_path and self.dotnet_path.startswith(
                        os.path.expanduser("~")
                    ):
                        env = os.environ.copy()
                        env["DOTNET_ROOT"] = os.path.expanduser("~/.dotnet")

                    process = subprocess.Popen(
                        cmd,
                        cwd=self.steamless_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        bufsize=0,
                        env=env,
                    )

            # Store process for cleanup
            self._process_mutex.lock()
            self._current_process = process
            self._process_mutex.unlock()

            # Monitor output
            output_lines = []

            logger.debug(
                f"Starting to read output from Steamless process (PID: {process.pid})"
            )
            logger.debug(f"Process stdout exists: {process.stdout is not None}")

            if process.stdout:
                try:
                    for line in iter(process.stdout.readline, ""):
                        if self._current_process != process:
                            logger.debug(
                                "Process was terminated, stopping output monitoring"
                            )
                            break

                        if not line:
                            break

                        line = line.strip()
                        if line:
                            self.progress.emit(f"{line}")
                            output_lines.append(line)
                            variant = _extract_steamstub_variant(line)
                            if variant:
                                self._last_exe_variant = variant

                except ValueError as e:
                    logger.debug(f"stdout closed during read: {e}")
                except Exception as e:
                    logger.debug(f"Error reading process output: {e}")

            process.wait()
            self._last_exe_returncode = process.returncode

            # Log output summary
            if output_lines:
                logger.debug(
                    f"Steamless output ({len(output_lines)} lines): {output_lines[:3]}..."
                )
            else:
                logger.warning(
                    f"No output captured from Steamless (return code: {process.returncode})"
                )
                self.progress.emit("Warning: No output captured from Steamless")

            # Check exit codes
            # 0 = success, DRM removed
            # 1 = no Steam DRM (not an error, try next executable)
            # 2 = SteamStub detected but unpacking failed
            # >2 = error
            exe_name = os.path.basename(exe_path)
            if process.returncode == 0:
                self.progress.emit(f"Successfully unpacked Steam DRM: {exe_name} (code 0)")
            elif process.returncode == 1:
                self.progress.emit(f"No Steam DRM detected in: {exe_name} (code 1)")
                self._last_exe_status = "no_drm"
                return False
            elif process.returncode == 2:
                err_msg = (
                    f"SteamStub DRM detected in {exe_name}, but unpacking failed (code 2)."
                )
                self._last_exe_error = err_msg
                self.error.emit(err_msg)
                self._last_exe_status = "unpack_error"
                return False
            else:
                detail = next(
                    (
                        ln
                        for ln in reversed(output_lines)
                        if ln.startswith("[!]") or "error" in ln.lower()
                    ),
                    "",
                )
                err_msg = f"Steamless failed with exit code {process.returncode} on {exe_name}"
                if detail:
                    err_msg = f"{err_msg}: {detail}"
                self._last_exe_error = err_msg
                self.error.emit(err_msg)
                self._last_exe_status = "error"
                return False

            # Exit code 0 - Steamless was successful
            try:
                if getattr(self, "use_aio", False):
                    # AIO creates a .original backup
                    original_backup = exe_path + ".original"
                    bak_file = exe_path + ".bak"
                    if os.path.exists(original_backup):
                        if os.path.exists(bak_file):
                            os.remove(bak_file)
                        os.rename(original_backup, bak_file)
                        self._last_exe_backup_path = bak_file
                        self.progress.emit(f"Backed up original executable to: {bak_file}")
                else:
                    # Steamless CLI creates an .unpacked.exe
                    unpacked_exe = exe_path + ".unpacked.exe"
                    bak_file = exe_path + ".bak"
                    if os.path.exists(unpacked_exe):
                        if os.path.exists(bak_file):
                            os.remove(bak_file)
                        os.rename(exe_path, bak_file)
                        os.rename(unpacked_exe, exe_path)
                        self._last_exe_backup_path = bak_file
                        self.progress.emit(f"Backed up original executable to: {bak_file}")
            except Exception as e:
                self.progress.emit(f"Warning: Failed to rename backup files: {e}")

            self._last_exe_status = "success"
            return True

        except Exception as e:
            logger.error(f"Error running Steamless: {e}", exc_info=True)
            err_msg = f"Error running Steamless: {str(e)}"
            self._last_exe_error = err_msg
            self.error.emit(err_msg)
            self._last_exe_status = "error"
            return False
        finally:
            # Clean up process reference
            self._process_mutex.lock()
            self._current_process = None
            self._process_mutex.unlock()

    def run_steamless_on_exe(self, exe_path: str) -> bool:
        self._reset_exe_state()
        return self._run_steamless_on_exe(exe_path)

    def terminate_process(self):
        """Terminate any running Steamless process (thread-safe)."""
        self._process_mutex.lock()
        try:
            process = self._current_process
            if process and process.poll() is None:
                logger.info("Terminating running Steamless process...")
                try:
                    # CRITICAL: Close stdout first to unblock any readline() calls
                    if process.stdout:
                        try:
                            process.stdout.close()
                        except OSError:
                            pass

                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                        except OSError:
                            pass
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            logger.error(
                                "Failed to kill Steamless process even with SIGKILL"
                            )
                    logger.info("Steamless process terminated")
                except Exception as e:
                    logger.error(f"Error terminating Steamless process: {e}")
                finally:
                    self._current_process = None
            else:
                self._current_process = None
        finally:
            self._process_mutex.unlock()


class SteamlessTask(QThread):
    """Task for removing Steam DRM using Steamless"""

    progress = pyqtSignal(str)
    progress_percentage = pyqtSignal(int)
    completed = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(bool)
    summary_ready = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self._is_running = True
        self._thread_completed = False
        self._game_directory = None
        self._target_exe = None  # Optional: specific exe to process

        # Steamless configuration
        self.steamless_path = self._get_steamless_path()
        self.steamless_integration = None
        self._integration_mutex = QMutex()
        self.use_aio = True
        self.fallback_used = False
        self.dotnet_available = False
        self.process = None
        self.last_summary: Optional[Dict[str, Any]] = None

    def _get_engine_label(self) -> str:
        """Return display name for the active Steamless backend."""
        if getattr(self, "use_aio", True):
            return (
                "Steamless AIO (Fallback)"
                if getattr(self, "fallback_used", False)
                else "Steamless AIO (Python)"
            )
        return "Steamless (.NET CLI)"

    def _emit_summary(self, summary: Dict[str, Any]) -> None:
        """Record and broadcast the canonical Steamless run summary."""
        self.last_summary = summary
        self.summary_ready.emit(summary)

    @staticmethod
    def _get_steamless_path():
        """Get path to Steamless directory"""
        relative_path = "Steamless"
        return Paths.deps(relative_path)

    def _setup_steamless_integration(self):
        """Initialize Steamless integration and check prerequisites"""
        self.fallback_used = False
        if getattr(self, "use_aio", True):
            self.progress.emit("Using Steamless AIO...")
            self.dotnet_available = True
            return True

        # Check if Steamless directory exists (legacy fallback)
        if not self.steamless_path.exists():
            logger.info("Legacy Steamless directory not found — falling back to Steamless AIO")
            self.use_aio = True
            self.fallback_used = True
            self.progress.emit("Using Steamless AIO...")
            self.dotnet_available = True
            return True

        # Check if Steamless.CLI.dll or Steamless.CLI exists
        steamless_dll = self.steamless_path / "Steamless.CLI.dll"
        if not steamless_dll.exists():
            steamless_dll = self.steamless_path / "Steamless.CLI"
        if not steamless_dll.exists():
            logger.info("Steamless.CLI.dll not found — falling back to Steamless AIO")
            self.use_aio = True
            self.fallback_used = True
            self.progress.emit("Using Steamless AIO...")
            self.dotnet_available = True
            return True

        # Check and ensure dotnet availability (will attempt auto-install if missing)
        self.progress.emit("Checking .NET 9 runtime availability...")
        self.dotnet_available = ensure_dotnet_availability()

        if not self.dotnet_available:
            logger.info(".NET 9 runtime not found — falling back to Steamless AIO")
            self.use_aio = True
            self.fallback_used = True
            self.progress.emit("Using Steamless AIO...")
            self.dotnet_available = True
            return True

        self.progress.emit(".NET 9 runtime is available")
        self.progress.emit("Steamless integration initialized successfully")
        logger.info(f"Steamless initialized at: {self.steamless_path}")
        return True

    def set_game_directory(self, game_directory: str):
        """Set the game directory to process (called before start())"""
        self._game_directory = game_directory

    def set_target_exe(self, exe_path: str):
        """Set a specific exe file to process (instead of scanning directory)"""
        self._target_exe = exe_path
        # Extract directory from exe path for prerequisite checks
        self._game_directory = os.path.dirname(exe_path)

    def run(self):
        """Run Steamless on the game directory (QThread main loop)"""
        try:
            success = False
            self.last_summary = None

            if not self._game_directory:
                error_msg = "Game directory not set"
                self._emit_summary(
                    SteamlessIntegration.build_summary(
                        "",
                        [],
                        target_exe=self._target_exe,
                        engine=self._get_engine_label(),
                        error=error_msg,
                    )
                )
                self.progress.emit(error_msg)
                self.error.emit((Exception, error_msg, ""))
                self.result.emit(success)
                self.completed.emit()
                self._thread_completed = True
                return

            logger.info(
                f"Starting Steamless task for directory: {self._game_directory}"
            )

            try:
                # Check if directory exists
                if not os.path.exists(self._game_directory):
                    error_msg = f"Game directory not found: {self._game_directory}"
                    self._emit_summary(
                        SteamlessIntegration.build_summary(
                            self._game_directory,
                            [],
                            target_exe=self._target_exe,
                            engine=self._get_engine_label(),
                            error=error_msg,
                        )
                    )
                    self.progress.emit(error_msg)
                    self.error.emit((Exception, error_msg, ""))
                    self.result.emit(success)
                    self.completed.emit()
                    self._thread_completed = True
                    return

                # Check prerequisites (dotnet, Steamless files)
                if not self._setup_steamless_integration():
                    error_msg = "Steamless prerequisites check failed."
                    self._emit_summary(
                        SteamlessIntegration.build_summary(
                            self._game_directory,
                            [],
                            target_exe=self._target_exe,
                            engine=self._get_engine_label(),
                            error=error_msg,
                        )
                    )
                    self.result.emit(success)
                    self.completed.emit()
                    self._thread_completed = True
                    return

                # Create SteamlessIntegration instance
                self._integration_mutex.lock()
                try:
                    self.steamless_integration = SteamlessIntegration(
                        steamless_path=str(self.steamless_path),
                    )
                    self.steamless_integration.use_aio = bool(
                        getattr(self, "use_aio", True)
                    )
                    self.steamless_integration.engine_label = self._get_engine_label()
                    self.steamless_integration.progress.connect(self._handle_progress)
                    self.steamless_integration.error.connect(
                        self._handle_integration_error
                    )
                    self.steamless_integration.finished.connect(
                        self._handle_integration_finished
                    )
                    self.steamless_integration.summary_ready.connect(
                        self._handle_integration_summary
                    )
                finally:
                    self._integration_mutex.unlock()

                # Process the game with Steamless
                logger.info(
                    f"Processing game directory with Steamless: {self._game_directory}"
                )

                if self._target_exe:
                    # Process only the specific exe
                    exe_name = os.path.basename(self._target_exe)
                    logger.info(f"Processing specific executable: {self._target_exe}")
                    self.progress.emit("Found 1 executable(s) to evaluate")
                    self.progress.emit(f"Processing: {exe_name}")
                    unpacked = self.steamless_integration.run_steamless_on_exe(
                        self._target_exe
                    )
                    entry = self.steamless_integration._build_exe_entry(
                        self._target_exe,
                        self._game_directory,
                        unpacked,
                    )
                    summary = self.steamless_integration.build_summary(
                        self._game_directory,
                        [entry],
                        target_exe=self._target_exe,
                        engine=self._get_engine_label(),
                    )
                    self._emit_summary(summary)

                    if unpacked:
                        self.progress.emit(f"Successfully processed: {exe_name}")
                        self.progress.emit("Steamless processing completed successfully")
                        final_success = True
                    elif entry["status"] == "clean":
                        self.progress.emit("No Steam DRM detected in executable")
                        self.progress.emit("Steamless processing completed successfully")
                        final_success = True
                    else:
                        self.progress.emit(
                            entry.get("error") or "Steamless failed to process executable"
                        )
                        final_success = False
                else:
                    # Scan directory for all executables
                    final_success = self.steamless_integration.process_game_with_steamless(
                        self._game_directory
                    )

                self.result.emit(final_success)
                self.completed.emit()
                self._thread_completed = True

            except Exception as e:
                error_msg = f"Unexpected error during Steamless processing: {e}"
                self.progress.emit(error_msg)
                logger.error(error_msg, exc_info=True)
                import traceback

                if self.last_summary is None:
                    self._emit_summary(
                        SteamlessIntegration.build_summary(
                            self._game_directory or "",
                            [],
                            target_exe=self._target_exe,
                            engine=self._get_engine_label(),
                            error=error_msg,
                        )
                    )
                self.error.emit((type(e), str(e), traceback.format_exc()))
                self.result.emit(False)
                self.completed.emit()
                self._thread_completed = True
                return

        except Exception as e:
            error_msg = f"CRITICAL: Thread crashed on startup: {e}"
            logger.critical(error_msg, exc_info=True)

            try:
                if self.last_summary is None:
                    self._emit_summary(
                        SteamlessIntegration.build_summary(
                            self._game_directory or "",
                            [],
                            target_exe=self._target_exe,
                            engine=self._get_engine_label(),
                            error=error_msg,
                        )
                    )
                self.result.emit(False)
                self.completed.emit()
            except (RuntimeError, TypeError):
                pass
            return

    def _handle_progress(self, message):
        """Handle progress messages from Steamless integration"""
        if not self._is_running:
            return
        self.progress.emit(message)

    def _handle_integration_error(self, message):
        """Handle error messages from SteamlessIntegration"""
        if not self._is_running:
            return
        logger.error(f"Steamless error: {message}")
        self.progress.emit(f"Error: {message}")

    def _handle_integration_summary(self, summary):
        """Relay structured SteamlessIntegration summary to task listeners."""
        if not self._is_running:
            return
        self._emit_summary(summary)

    def _handle_integration_finished(self, success):
        """Handle completion signal from SteamlessIntegration"""
        if not self._is_running:
            return
        if success:
            self.progress.emit("Steamless processing completed successfully")
        else:
            self.progress.emit("Steamless processing completed with warnings")

    def stop(self):
        """Stop the Steamless task (thread-safe)"""
        logger.debug("Stop signal received by Steamless task")

        if not self._is_running:
            return

        self._is_running = False

        self._integration_mutex.lock()
        try:
            if self.steamless_integration:
                try:
                    self.steamless_integration.terminate_process()
                except Exception as e:
                    logger.error(f"Error during process termination: {e}")
        finally:
            self._integration_mutex.unlock()

        self._integration_mutex.lock()
        try:
            if self.steamless_integration:
                try:
                    self.steamless_integration.progress.disconnect(
                        self._handle_progress
                    )
                    self.steamless_integration.error.disconnect(
                        self._handle_integration_error
                    )
                    self.steamless_integration.finished.disconnect(
                        self._handle_integration_finished
                    )
                    self.steamless_integration.summary_ready.disconnect(
                        self._handle_integration_summary
                    )
                    logger.debug("SteamlessIntegration signals disconnected")
                except (TypeError, RuntimeError) as e:
                    logger.debug(f"Signal disconnect during stop (expected): {e}")
        except Exception as e:
            logger.error(f"Error during signal cleanup: {e}")
        finally:
            self._integration_mutex.unlock()

        if self.isRunning():
            logger.debug("Waiting for SteamlessTask thread to finish...")
            self.quit()
            if not self.wait(600000):
                logger.warning("SteamlessTask thread did not finish within timeout")
                self.terminate()
                self.wait(5000)
        logger.debug("SteamlessTask thread has finished")

        self._integration_mutex.lock()
        try:
            self.steamless_integration = None
        finally:
            self._integration_mutex.unlock()

        self.process = None

    def is_dotnet_available(self):
        """Check if .NET 9 is available for Steamless execution"""
        return self.dotnet_available

    def get_steamless_path(self):
        """Get the path to the Steamless directory"""
        return str(self.steamless_path)
