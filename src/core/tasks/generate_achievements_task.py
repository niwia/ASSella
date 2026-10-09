import logging
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from utils.helpers import (
    get_slscheevo_save_path,
    get_schema_grabber_path,
    get_steam_stats_dir,
    get_dotnet_env,
)
from utils.paths import Paths

logger = logging.getLogger(__name__)

# Environment variables understood by newer schema-grabber builds. Passing the
# Steam password here keeps it out of the process list, which is world-readable
# on Linux via /proc/<pid>/cmdline.
SCHEMA_GRABBER_USER_ENV = "SCHEMA_GRABBER_USERNAME"
SCHEMA_GRABBER_PASS_ENV = "SCHEMA_GRABBER_PASSWORD"

_ENV_PROBE_MARKER = "__assella_env_probe__"


def _schema_grabber_supports_env() -> bool:
    """Whether the bundled schema-grabber accepts credentials via environment.

    Conservative on purpose. Defaulting to ``False`` means we keep using argv,
    which is the behaviour that is known to work. We only return ``True`` when
    there is positive evidence that a build reads the environment, so this can
    never silently break Steam authentication for users.
    """
    global _ENV_PROBE_CACHE
    if _ENV_PROBE_CACHE is not None:
        return _ENV_PROBE_CACHE

    result = False
    try:
        grabber = get_schema_grabber_path()
        if grabber.exists():
            cwd = get_slscheevo_save_path() / "data" / "bins"
            cwd.mkdir(parents=True, exist_ok=True)
            probe_env = dict(os.environ)
            probe_env[SCHEMA_GRABBER_USER_ENV] = _ENV_PROBE_MARKER
            probe_env[SCHEMA_GRABBER_PASS_ENV] = _ENV_PROBE_MARKER
            proc = subprocess.run(
                [str(grabber), _ENV_PROBE_MARKER, _ENV_PROBE_MARKER, "0"],
                cwd=str(cwd),
                env=probe_env,
                capture_output=True,
                text=True,
                timeout=45,
            )
            output = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
            # The runtime must have actually executed; otherwise the probe
            # proves nothing about argument handling.
            runtime_missing = any(
                marker in output
                for marker in (
                    "you must install or update .net",
                    "framework 'microsoft.netcore.app'",
                    "no such file or directory",
                    "cannot execute binary file",
                )
            )
            if not runtime_missing and proc.returncode != 0:
                # A genuine argument/usage rejection is the only reliable
                # positive signal that this build needs argv credentials.
                usage_rejected = any(
                    marker in output
                    for marker in ("usage", "too few arguments", "invalid argument", "index was out of range")
                )
                result = usage_rejected
    except Exception as e:
        logger.debug(f"schema-grabber env probe inconclusive: {e}")
        result = False

    _ENV_PROBE_CACHE = result
    logger.info(
        "schema-grabber env-var credential support: "
        f"{result} (False means argv fallback is used, which is known to work)"
    )
    return result


_ENV_PROBE_CACHE: Optional[bool] = None

# Handle optional psutil import
try:
    import psutil
except ImportError:
    psutil = None
    logger.debug("psutil not found, process termination will be less robust.")


class GenerateAchievementsTask(QObject):
    """Generate Steam achievement stats using schema-grabber"""

    progress = pyqtSignal(str)
    progress_percentage = pyqtSignal(int)
    completed = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._is_running = True
        self.process = None
        self.process_pid = None

    def run(self, app_ids=None):
        """Run schema-grabber to generate achievement stats"""
        logger.info("Starting achievement generation task using schema-grabber")
        self.progress.emit("Checking Steam credentials...")

        try:
            # Get credentials from settings
            from utils.settings import get_settings
            settings = get_settings()
            settings.sync()
            username = settings.value("steam_username", "", type=str)
            from utils.helpers import decrypt_string
            # The password is not persisted any more. Prefer an in-memory value
            # captured during this session, then the session-only blob written
            # by the settings dialog, then a legacy persisted value (which is
            # actively removed on next save).
            password = ""
            session_pw = getattr(self, "_session_steam_password", "") or ""
            if not session_pw:
                try:
                    session_pw = decrypt_string(
                        settings.value("steam_password_session_only", "", type=str)
                    )
                except Exception:
                    session_pw = ""
            if session_pw:
                password = session_pw
            else:
                password = decrypt_string(settings.value("steam_password", "", type=str))

            if not username or not password:
                error_msg = (
                    "Steam credentials are not configured. The Steam password is "
                    "no longer saved to disk, so it must be entered each session. "
                    "Open Settings -> Tools, enter your Steam password, then retry."
                )
                self.progress.emit(error_msg)
                self.error.emit(error_msg)
                result = {"success": False, "message": error_msg}
                self.completed.emit(result)
                return result

            schema_grabber = get_schema_grabber_path()
            if not schema_grabber.exists():
                error_msg = f"schema-grabber binary not found at {schema_grabber}."
                self.progress.emit(error_msg)
                self.error.emit(error_msg)
                result = {"success": False, "message": error_msg}
                self.completed.emit(result)
                return result

            self.progress.emit("schema-grabber binary found")
            logger.info(f"schema-grabber binary found at: {schema_grabber}")

            # Resolve target app IDs
            target_appids = []
            if app_ids:
                if isinstance(app_ids, list):
                    target_appids = [str(aid) for aid in app_ids]
                else:
                    target_appids = [str(app_ids)]
            else:
                target_appids = ["0"]

            # Run directory
            cwd = get_slscheevo_save_path() / "data" / "bins"
            cwd.mkdir(parents=True, exist_ok=True)

            success_count = 0
            failed_count = 0
            last_error = ""

            for idx, app_id in enumerate(target_appids, 1):
                self.progress.emit(f"Generating stats schema for game ID {app_id}...")
                
                # Credentials are passed through the environment first.
                #
                # Any local user can read another process's command line via
                # /proc/<pid>/cmdline or `ps`, so a password on argv is exposed
                # in plaintext for the life of the process. The environment is
                # readable only by the process owner, so it is the safe path.
                #
                # schema-grabber is deprecated and rarely used, so we default to
                # the safe route and only fall back to argv if the environment
                # attempt demonstrably fails. That way the password is never on
                # argv unless there is no alternative.
                env_extra: Dict[str, str] = {
                    SCHEMA_GRABBER_USER_ENV: username,
                    SCHEMA_GRABBER_PASS_ENV: password,
                }
                if _schema_grabber_supports_env():
                    command = [str(schema_grabber), username, "", app_id]
                else:
                    logger.warning(
                        "[SchemaGrabber] Using argv credential fallback; the Steam "
                        "password will be briefly visible to other local users in "
                        "the process list. schema-grabber is deprecated - consider "
                        "using a build that reads credentials from the environment."
                    )
                    command = [str(schema_grabber), username, password, app_id]
                    env_extra = {}
                logger.info(f"Executing schema-grabber for AppID {app_id}")

                # Resolve dotnet environment settings (cleaning overrides and setting DOTNET_ROOT)
                env = get_dotnet_env()

                self.process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    cwd=str(cwd),
                    env={**env, **env_extra} if env_extra else env,
                    bufsize=1,  # Line buffered
                    creationflags=(
                        subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                    ),
                )
                self.process_pid = self.process.pid

                # Start a watchdog thread to terminate the process if it hangs (e.g. on 2FA prompts)
                import threading
                def watchdog():
                    import time
                    start_time = time.time()
                    while time.time() - start_time < 25:
                        if self.process is None or self.process.poll() is not None:
                            return
                        time.sleep(0.5)
                    if self.process is not None and self.process.poll() is None:
                        logger.warning(f"schema-grabber exceeded 25 seconds timeout for AppID {app_id}. Terminating...")
                        self.stop()

                threading.Thread(target=watchdog, daemon=True).start()

                # Read output
                while True:
                    if not self._is_running:
                        self.process.terminate()
                        break

                    if self.process is None or self.process.stdout is None:
                        break

                    line = self.process.stdout.readline()
                    if not line:
                        return_code = self.process.poll()
                        if return_code is not None:
                            break
                        continue

                    line = line.rstrip()
                    # Hide password if printed in output
                    safe_line = line.replace(password, "********")
                    self.progress.emit(safe_line)

                    # Update percentage roughly
                    percentage = int((idx / len(target_appids)) * 100)
                    self.progress_percentage.emit(percentage)

                return_code = self.process.wait()
                self.process = None
                self.process_pid = None

                if return_code == 0:
                    success_count += 1
                else:
                    failed_count += 1
                    last_error = f"schema-grabber exited with code {return_code}"

            # Copy generated files to Steam directory
            if success_count > 0:
                # Copy template stats file UserGameStats_{account_id}_{appid}.bin for each logged in account
                try:
                    steam_stats_dir = get_steam_stats_dir()
                    if steam_stats_dir:
                        login_users_file = steam_stats_dir.parent / "config" / "loginusers.vdf"
                        if login_users_file.exists():
                            import vdf
                            with open(login_users_file, "r", encoding="utf-8") as f:
                                loginusers = vdf.load(f)
                            accounts = loginusers.get("users", {})

                            template_path = get_slscheevo_save_path() / "data" / "UserGameStats_TEMPLATE.bin"
                            if not template_path.exists():
                                template_path = Paths.deps("SLScheevo/data/UserGameStats_TEMPLATE.bin")

                            if template_path.exists():
                                for steamid64_str in accounts.keys():
                                    for app_id in target_appids:
                                        if app_id == "0":
                                            continue
                                        try:
                                            steamid64 = int(steamid64_str)
                                            account_id = steamid64 & 0xFFFFFFFF
                                            stats_name = f"UserGameStats_{account_id}_{app_id}.bin"
                                            archive_stats = cwd / stats_name
                                            if not archive_stats.exists():
                                                shutil.copy2(template_path, archive_stats)
                                                logger.info(f"Generated user stats from template: {stats_name}")
                                        except Exception as e:
                                            logger.warning(f"Failed to generate template stats: {e}")
                except Exception as e:
                    logger.warning(f"Template stats generator failed: {e}")

                # Copy all generated bin files to Steam stats
                try:
                    steam_stats_dir = get_steam_stats_dir()
                    if steam_stats_dir:
                        steam_stats_dir.mkdir(parents=True, exist_ok=True)
                    
                    bin_files = list(cwd.glob("**/*.bin"))
                    for bin_file in bin_files:
                        if steam_stats_dir:
                            dest_path = steam_stats_dir / bin_file.name
                            if bin_file.name.startswith("UserGameStatsSchema_") or not dest_path.exists():
                                shutil.copy2(bin_file, dest_path)
                                logger.info(f"Copied {bin_file.name} to {dest_path}")
                except Exception as e:
                    logger.warning(f"Failed to copy bins to Steam stats: {e}")

            if failed_count == 0:
                msg = "Achievement generation completed successfully"
                self.progress.emit(msg)
                result = {"success": True, "return_code": 0, "message": msg}
                self.completed.emit(result)
                return result
            else:
                msg = last_error if last_error else "Achievement generation failed"
                self.progress.emit(msg)
                self.error.emit(msg)
                result = {"success": False, "return_code": -1, "message": msg}
                self.completed.emit(result)
                return result

        except Exception as e:
            error_msg = f"Unexpected error during achievement generation: {e}"
            self.progress.emit(f"{error_msg}")
            logger.error(error_msg, exc_info=True)
            if self.process:
                self.process.terminate()
            self.process = None
            self.process_pid = None
            self.error.emit(error_msg)
            result = {"success": False, "return_code": -1, "message": error_msg}
            self.completed.emit(result)
            return result

    def stop(self):
        """Stop the task and terminate the process"""
        logger.debug("Stop signal received by achievement generation task")
        self._is_running = False

        if not self.process_pid:
            self.process = None
            return

        if psutil:
            try:
                parent = psutil.Process(self.process_pid)
                children = parent.children(recursive=True)
                processes = [parent] + children
                for proc in processes:
                    try:
                        proc.terminate()
                    except psutil.NoSuchProcess:
                        pass
                # Wait for graceful termination
                gone, alive = psutil.wait_procs(processes, timeout=3)
                for p in alive:
                    p.kill()  # Force kill stubborn processes
            except psutil.NoSuchProcess:
                pass
            except Exception as e:
                logger.error(f"Error stopping process with psutil: {e}")
        else:
            if self.process:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=3)
                except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                    try:
                        self.process.kill()
                        self.process.wait(timeout=3)
                    except (ProcessLookupError, OSError):
                        pass
        self.process = None
        self.process_pid = None

