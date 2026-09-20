import logging
import os
import sys
import re
import psutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


logger = logging.getLogger(__name__)

_slssteam_so_path_cache = None
_library_inject_so_path_cache = None

# ---------------------------------------------------------------------------
# SteamEnv — single source of truth for Steam/SLS path resolution
# ---------------------------------------------------------------------------

@dataclass
class SteamEnv:
    """Resolved paths for Steam and SLSsteam for the current user environment.

    Detection is Flatpak vs Native only (per SLS dev guidance).
    The OS/distro never changes these paths — only how Steam was installed does.

    Based on headcrab.sh's flatpakcheck() pattern:
        flatpakcheck() { [ -d "$FlatpakSteamInstallDir" ] }
    where FlatpakSteamInstallDir = ~/.var/app/com.valvesoftware.Steam/.steam/steam
    """
    is_flatpak: bool
    steam_path: Optional[str]              # Steam root dir (resolved)
    sls_config_path: Path                  # config.yaml for SLSsteam
    sls_log_path: Path                     # .SLSsteam.log
    sls_install_dir: Path                  # directory containing SLSsteam.so
    sls_config_dir: Path                   # ~/.config/SLSsteam or Flatpak equiv
    steamapps_paths: List[Path]            # all steamapps dirs to search (ACF etc.)
    loginusers_path: Optional[Path]        # loginusers.vdf path, None if not found

    def steam_found(self) -> bool:
        return self.steam_path is not None

    def sls_config_exists(self) -> bool:
        return self.sls_config_path.exists()

    def sls_log_exists(self) -> bool:
        return self.sls_log_path.exists()

    def sls_installed(self) -> bool:
        """Return True if SLSsteam.so is present in the expected install dir."""
        return (self.sls_install_dir / "SLSsteam.so").exists()

    def get_diagnostic_summary(self) -> str:
        """Human-readable summary for logging/warnings."""
        lines = [
            f"Steam type   : {'Flatpak' if self.is_flatpak else 'Native'}",
            f"Steam path   : {self.steam_path or 'NOT FOUND'}",
            f"SLS config   : {self.sls_config_path} ({'exists' if self.sls_config_exists() else 'MISSING'})",
            f"SLS log      : {self.sls_log_path} ({'exists' if self.sls_log_exists() else 'MISSING'})",
            f"SLS installed: {'yes' if self.sls_installed() else 'NO'}",
            f"Steamapps    : {[str(p) for p in self.steamapps_paths]}",
            f"loginusers   : {self.loginusers_path or 'NOT FOUND'}",
        ]
        return "\n".join(lines)


# Module-level cache — populated lazily, invalidated by invalidate_steam_env_cache()
_steam_env_cache: Optional[SteamEnv] = None
_steam_env_lock = threading.Lock()


def _build_steam_env() -> SteamEnv:
    """Internal: detect Steam type and resolve all paths.

    Detection order (mirrors headcrab flatpakcheck):
      1. ~/.var/app/com.valvesoftware.Steam/.steam/steam  →  Flatpak
      2. ~/.local/share/Steam / ~/.steam/steam            →  Native
      3. Neither found                                    →  Native paths used, warnings emitted
    """
    home = Path.home()

    # --- Flatpak detection (headcrab's flatpakcheck pattern) ---
    flatpak_steam_sentinel = home / ".var" / "app" / "com.valvesoftware.Steam" / ".steam" / "steam"
    is_flatpak = flatpak_steam_sentinel.is_dir()

    if is_flatpak:
        # ── Flatpak paths ──────────────────────────────────────────────────
        flatpak_base = home / ".var" / "app" / "com.valvesoftware.Steam"

        # Steam root: data/Steam is the real install; .steam/steam is the symlink
        steam_candidate = flatpak_base / "data" / "Steam"
        steam_path = str(steam_candidate.resolve()) if (steam_candidate / "steamapps").is_dir() else None

        sls_config_dir = flatpak_base / ".config" / "SLSsteam"
        sls_config_path = sls_config_dir / "config.yaml"
        sls_log_path = flatpak_base / ".SLSsteam.log"
        sls_install_dir = flatpak_base / ".local" / "share" / "SLSsteam"

        # Build steamapps search list
        steamapps_paths: List[Path] = []
        if steam_path:
            steamapps_paths.append(Path(steam_path) / "steamapps")

        # loginusers.vdf
        loginusers_candidate = (
            Path(steam_path) / "config" / "loginusers.vdf"
            if steam_path else None
        )
        loginusers_path = loginusers_candidate if (loginusers_candidate and loginusers_candidate.exists()) else None

        logger.info(
            f"SteamEnv: Flatpak Steam detected. "
            f"SLS config: {sls_config_path}, log: {sls_log_path}"
        )

    else:
        # ── Native paths ───────────────────────────────────────────────────
        # Steam root: prefer ~/.local/share/Steam, fall back to ~/.steam/steam symlink
        native_candidates = [
            home / ".local" / "share" / "Steam",
            home / ".steam" / "steam",
        ]
        steam_path = None
        for candidate in native_candidates:
            if (candidate / "steamapps").is_dir():
                steam_path = str(candidate.resolve())
                break

        # SLS config: respect XDG_CONFIG_HOME, else ~/.config/SLSsteam
        xdg_cfg = os.environ.get("XDG_CONFIG_HOME", "")
        if xdg_cfg and Path(xdg_cfg).is_absolute():
            sls_config_dir = Path(xdg_cfg) / "SLSsteam"
        else:
            sls_config_dir = home / ".config" / "SLSsteam"
        sls_config_path = sls_config_dir / "config.yaml"

        sls_log_path = home / ".SLSsteam.log"
        sls_install_dir = home / ".local" / "share" / "SLSsteam"

        # Build steamapps search list
        steamapps_paths = []
        if steam_path:
            steamapps_paths.append(Path(steam_path) / "steamapps")

        # loginusers.vdf
        loginusers_candidates = [
            home / ".local" / "share" / "Steam" / "config" / "loginusers.vdf",
            home / ".steam" / "steam" / "config" / "loginusers.vdf",
            home / ".steam" / "root" / "config" / "loginusers.vdf",
        ]
        loginusers_path = next(
            (p for p in loginusers_candidates if p.exists()), None
        )

        logger.info(
            f"SteamEnv: Native Steam detected. "
            f"SLS config: {sls_config_path}, log: {sls_log_path}"
        )

    # --- Additional steamapps from libraryfolders.vdf ---
    if steam_path:
        try:
            vdf_path = Path(steam_path) / "steamapps" / "libraryfolders.vdf"
            if vdf_path.exists():
                content = vdf_path.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r'"path"\s+"([^"]+)"', content):
                    lib = Path(m.group(1).replace("\\\\", "\\"))
                    candidate_sa = lib / "steamapps"
                    if candidate_sa.is_dir() and candidate_sa not in steamapps_paths:
                        steamapps_paths.append(candidate_sa)
        except (OSError, IOError) as e:
            logger.warning(f"SteamEnv: could not parse libraryfolders.vdf: {e}")

    # --- Emit warnings for missing critical paths ---
    if not steam_path:
        logger.warning(
            "SteamEnv: Steam installation not found in any expected location. "
            "SLSsteam features that require Steam (ACF creation, library index) will use fallbacks."
        )

    env = SteamEnv(
        is_flatpak=is_flatpak,
        steam_path=steam_path,
        sls_config_path=sls_config_path,
        sls_log_path=sls_log_path,
        sls_install_dir=sls_install_dir,
        sls_config_dir=sls_config_dir,
        steamapps_paths=steamapps_paths,
        loginusers_path=loginusers_path,
    )
    logger.debug("SteamEnv diagnostic:\n" + env.get_diagnostic_summary())
    return env


def get_steam_env() -> SteamEnv:
    """Return the cached SteamEnv, building it on first call.

    Thread-safe. Call invalidate_steam_env_cache() to force re-detection
    (e.g. when the user changes paths in Settings).
    """
    global _steam_env_cache
    if _steam_env_cache is not None:
        return _steam_env_cache
    with _steam_env_lock:
        if _steam_env_cache is None:  # double-checked locking
            _steam_env_cache = _build_steam_env()
    return _steam_env_cache


def invalidate_steam_env_cache() -> None:
    """Force SteamEnv to be re-detected on the next call to get_steam_env().

    Call this when the user changes Steam or SLSsteam paths in Settings.
    """
    global _steam_env_cache
    with _steam_env_lock:
        _steam_env_cache = None
    logger.info("SteamEnv cache invalidated — will re-detect on next access")


def find_steam_install():
    if sys.platform == "win32":
        return _find_steam_windows()
    elif sys.platform == "linux":
        return _find_steam_linux()
    else:
        logger.warning(
            f"Automatic Steam path detection is not supported on this OS: {sys.platform}."
        )
        return None


def _find_steam_windows():
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
        steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
        winreg.CloseKey(key)
        logger.info(f"Found Steam installation at: {steam_path}")
        return os.path.normpath(steam_path)
    except OSError:
        logger.error("Failed to read Steam path from registry.")
        return None


def _find_steam_linux():
    home_dir = os.path.expanduser("~")
    potential_paths = [
        os.path.join(home_dir, ".steam", "steam"),
        os.path.join(home_dir, ".local", "share", "Steam"),
        os.path.join(
            home_dir, ".var", "app", "com.valvesoftware.Steam", "data", "Steam"
        ),
    ]
    # os.path.join(home_dir, "snap", "steam", "common", ".steam", "steam"),

    for path in potential_paths:
        if os.path.isdir(os.path.join(path, "steamapps")):
            real_path = os.path.realpath(path)
            logger.info(f"Found Steam installation at: {real_path} (from {path})")
            return real_path

    logger.error("Could not find Steam installation in common Linux directories.")
    return None


def get_owned_steam_appids() -> set:
    """Return set of AppIDs that the user legitimately owns on Steam (via localconfig.vdf apptickets)."""
    owned = set()
    home = Path.home()
    candidates = [
        home / ".local" / "share" / "Steam" / "userdata",
        home / ".steam" / "steam" / "userdata",
        home / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam" / "userdata",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam" / "userdata",
    ]
    try:
        env = get_steam_env()
        if env.steam_path:
            p = Path(env.steam_path) / "userdata"
            if p not in candidates:
                candidates.append(p)
    except Exception:
        pass

    for userdata_dir in candidates:
        if not userdata_dir.is_dir():
            continue
        try:
            for entry in userdata_dir.iterdir():
                if entry.is_dir() and entry.name.isdigit():
                    cfg = entry / "config" / "localconfig.vdf"
                    if cfg.is_file():
                        try:
                            txt = cfg.read_text(encoding="utf-8", errors="ignore")
                            m = re.search(r'"apptickets"\s*\{([^}]+)\}', txt)
                            if m:
                                owned.update(re.findall(r'"(\d+)"', m.group(1)))
                        except Exception as e:
                            logger.debug(f"Error reading apptickets from {cfg}: {e}")
        except Exception as e:
            logger.debug(f"Error iterating userdata dir {userdata_dir}: {e}")

    return owned


def parse_library_folders(vdf_path):
    library_paths = []
    try:
        with open(vdf_path, "r", encoding="utf-8") as f:
            content = f.read()
        matches = re.findall(r"^\s*\"(?:path|\d+)\"\s*\"(.*?)\"", content, re.MULTILINE)
        for path in matches:
            normalized_path = path.replace("\\\\", "\\")
            if os.path.isdir(os.path.join(normalized_path, "steamapps")):
                library_paths.append(normalized_path)
    except OSError as e:
        logger.error(f"Failed to parse libraryfolders.vdf: {e}")
    return library_paths


def get_steam_libraries():
    steam_path = find_steam_install()
    if not steam_path:
        return []

    all_libraries = {os.path.realpath(steam_path)}
    vdf_path = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")

    if os.path.exists(vdf_path):
        additional_libraries = parse_library_folders(vdf_path)
        for lib_path in additional_libraries:
            all_libraries.add(os.path.realpath(lib_path))

    return list(all_libraries)


def is_steam_running():
    process_name = "steam.exe" if sys.platform == "win32" else "steam"
    return any(
        (p.info.get("name") or "").lower() == process_name
        for p in psutil.process_iter(["name"])
    )


def kill_steam_process():
    global _slssteam_so_path_cache, _library_inject_so_path_cache
    _slssteam_so_path_cache = None
    _library_inject_so_path_cache = None

    process_name = "steam.exe" if sys.platform == "win32" else "steam"
    steam_proc = next(
        (
            p
            for p in psutil.process_iter(["pid", "name"])
            if (p.info.get("name") or "").lower() == process_name
        ),
        None,
    )

    if not steam_proc:
        logger.warning(f"{process_name} process not found.")
        return False

    if sys.platform == "linux":
        pid = steam_proc.pid
        maps_file = f"/proc/{pid}/maps"
        try:
            with open(maps_file, "r") as f:
                for line in f:
                    if "SLSsteam.so" in line:
                        parts = line.split()
                        if len(parts) > 5 and os.path.exists(parts[-1]):
                            _slssteam_so_path_cache = parts[-1]
                            logger.info(
                                f"Found and cached SLSsteam.so path: {_slssteam_so_path_cache}"
                            )
                    elif (
                        "library-inject.so" in line
                        or "libSLS-library-inject.so" in line
                    ):
                        parts = line.split()
                        if len(parts) > 5 and os.path.exists(parts[-1]):
                            _library_inject_so_path_cache = parts[-1]
                            logger.info(
                                f"Found and cached library-inject.so path: {_library_inject_so_path_cache}"
                            )
        except OSError as e:
            logger.error(f"Error reading process maps for library paths: {e}")

    try:
        steam_proc.kill()
        steam_proc.wait(timeout=5)
        logger.info(f"Successfully terminated {process_name} (PID: {steam_proc.pid}).")
        return True
    except psutil.Error as e:
        logger.error(f"Failed to terminate {process_name}: {e}")
        return False


def start_steam():
    """Start Steam on Windows, or attempt to start Steam with SLSsteam integration on Linux
    Returns: "SUCCESS", "FAILED", or "NEEDS_USER_PATH"
    """
    global _slssteam_so_path_cache, _library_inject_so_path_cache
    logger.info("Attempting to start Steam...")

    try:
        if sys.platform == "win32":
            steam_path = find_steam_install()
            if not steam_path:
                return "FAILED"
            exe_path = os.path.join(steam_path, "steam.exe")
            if not os.path.exists(exe_path):
                return "FAILED"
            subprocess.Popen([exe_path])
            return "SUCCESS"

        elif sys.platform == "linux":
            # For Linux, we now need to handle SLSsteam.so AND library-inject.so
            slssteam_path = _slssteam_so_path_cache
            library_inject_path = _library_inject_so_path_cache

            # Try default locations if not cached
            if not slssteam_path:
                default_slssteam_paths = [
                    "/usr/lib32/libSLSsteam.so",
                    os.path.expanduser("~/.local/share/SLSsteam/SLSsteam.so"),
                    os.path.expanduser(
                        "~/.var/app/com.valvesoftware.Steam/.local/share/SLSsteam/SLSsteam.so"
                    ),
                ]
                for path in default_slssteam_paths:
                    if os.path.exists(path):
                        slssteam_path = path
                        logger.info(f"Found SLSsteam.so at: {path}")
                        break

            if not library_inject_path:
                default_library_inject_paths = [
                    "/usr/lib32/libSLS-library-inject.so",
                    os.path.expanduser("~/.local/share/SLSsteam/library-inject.so"),
                    os.path.expanduser(
                        "~/.var/app/com.valvesoftware.Steam/.local/share/SLSsteam/library-inject.so"
                    ),
                ]
                for path in default_library_inject_paths:
                    if os.path.exists(path):
                        library_inject_path = path
                        logger.info(f"Found library-inject.so at: {path}")
                        break

            # If we have both libraries, start with them
            if slssteam_path and library_inject_path:
                if os.path.exists(slssteam_path) and os.path.exists(
                    library_inject_path
                ):
                    # Start Steam with both libraries
                    success = start_steam_with_slssteam(
                        slssteam_path, library_inject_path
                    )
                    # Only clear caches if successful
                    if success == "SUCCESS":
                        _slssteam_so_path_cache = None
                        _library_inject_so_path_cache = None
                    return success
                else:
                    logger.warning("Cached library paths no longer exist")
                    return "NEEDS_USER_PATH"
            else:
                # Missing one or both libraries
                missing = []
                if not slssteam_path:
                    missing.append("SLSsteam.so")
                if not library_inject_path:
                    missing.append("library-inject.so")
                logger.warning(f"Missing libraries: {', '.join(missing)}")
                return "NEEDS_USER_PATH"
        else:
            return "FAILED"
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"Failed to execute Steam: {e}", exc_info=True)
        return "FAILED"


def start_steam_with_slssteam(slssteam_path=None, library_inject_path=None):
    """Start Steam on Linux with SLSsteam.so AND library-inject.so via LD_AUDIT
    Returns: "SUCCESS", "FAILED", or "NEEDS_USER_PATH"
    """

    if sys.platform != "linux":
        logger.error("start_steam_with_slssteam is only supported on Linux")
        return "FAILED"

    # Validate paths
    if not slssteam_path or not os.path.exists(slssteam_path):
        logger.error(f"SLSsteam.so path is invalid or does not exist: {slssteam_path}")
        return "NEEDS_USER_PATH"

    if not library_inject_path or not os.path.exists(library_inject_path):
        logger.error(
            f"library-inject.so path is invalid or does not exist: {library_inject_path}"
        )
        return "NEEDS_USER_PATH"

    try:
        logger.info(
            f"Executing Steam with LD_AUDIT: {library_inject_path}:{slssteam_path}"
        )
        env = os.environ.copy()
        env["LD_AUDIT"] = f"{library_inject_path}:{slssteam_path}"
        subprocess.Popen(["steam"], env=env)
        return "SUCCESS"
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(
            f"Failed to execute steam with provided libraries: {e}", exc_info=True
        )
        return "FAILED"


def run_dll_injector(steam_path):
    if sys.platform != "win32":
        return False
    injector_path = os.path.join(steam_path, "DLLInjector.exe")
    if not os.path.exists(injector_path):
        return False
    try:
        subprocess.Popen(
            [injector_path], cwd=steam_path, creationflags=subprocess.CREATE_NO_WINDOW
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def get_library_index(library_path: str, steam_path: str | None = None) -> int:
    """Get the library index from libraryfolders.vdf for a given library path.

    If `steam_path` is provided, it will be used instead of calling
    `find_steam_install()` (useful to avoid repeated lookups).
    """
    if not steam_path:
        steam_path = find_steam_install()
    if not steam_path:
        return 0

    vdf_path = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
    if not os.path.exists(vdf_path):
        return 0

    try:
        with open(vdf_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Parse each library entry - format is like:
        # "1"
        # {
        #     "path"  "path/to/library"
        #     ...
        # }
        lines = content.split("\n")
        current_index = None

        for line in lines:
            # Match numeric indices like "0", "1", "2"
            index_match = re.match(r'^\s*"(\d+)"\s*$', line)
            if index_match:
                current_index = int(index_match.group(1))
                continue

            # Match path line
            path_match = re.match(r'^\s*"path"\s*"([^"]+)"', line)
            if path_match and current_index is not None:
                path = path_match.group(1).replace("\\\\", "\\")
                if os.path.realpath(path) == os.path.realpath(library_path):
                    return current_index

        # Default to 0 if not found (main library)
        return 0
    except (OSError, ValueError) as e:
        logger.error(f"Failed to get library index: {e}")
        return 0


def slssteam_api_send(command: str) -> bool:
    """Send a command to SLSsteam API via named pipe."""
    if sys.platform != "linux":
        return False

    pipe_path = "/tmp/SLSsteam.API"

    try:
        with open(pipe_path, "w") as f:
            f.write(command)
            f.flush()
        logger.info(f"SLSsteam API command sent: {command}")
        return True
    except OSError:
        # Silently fail - API may not be available
        return False


def fix_greenluma_offline_mode():
    """Fix WantsOfflineMode in loginusers.vdf to prevent Steam breakage with GreenLuma.

    When Steam is closed with Offline Mode enabled and then launched with GreenLuma,
    it can break Steam. This function automatically changes WantsOfflineMode from 1 to 0.
    """
    if sys.platform != "win32":
        return

    try:
        from utils.settings import get_settings
        from utils.yaml_config_manager import is_greenluma_wrapper_mode_enabled
    except ImportError:
        return

    settings = get_settings()
    if not is_greenluma_wrapper_mode_enabled():
        return

    # Check if config management is enabled
    if not settings.value("sls_config_management", True, type=bool):
        return

    steam_path = find_steam_install()
    if not steam_path:
        return

    login_file = os.path.join(steam_path, "config", "loginusers.vdf")
    if not os.path.exists(login_file):
        return

    try:
        import vdf

        with open(login_file, "r", encoding="utf-8", errors="ignore") as f:
            data = vdf.load(f)

        fixed = False
        for user in data.get("users", {}).values():
            if user.get("WantsOfflineMode") == "1":
                user["WantsOfflineMode"] = "0"
                fixed = True

        if fixed:
            with open(login_file, "w", encoding="utf-8") as f:
                vdf.dump(data, f)
            logger.info(
                "Fixed WantsOfflineMode in loginusers.vdf to prevent GreenLuma issues"
            )
    except ImportError:
        logger.warning("vdf library not installed, cannot fix offline mode")
    except OSError as e:
        logger.error(f"Failed to fix offline mode: {e}")


def find_next_applist_number(app_list_dir):
    """Find the next available AppList number"""
    if not os.path.exists(app_list_dir):
        os.makedirs(app_list_dir)
        return 1

    max_num = 0
    try:
        for filename in os.listdir(app_list_dir):
            match = re.match(r"^(\d+)\.txt$", filename)
            if match:
                num = int(match.group(1))
                if num > max_num:
                    max_num = num
    except OSError as e:
        logger.error(f"Error scanning AppList directory: {e}")

    return max_num + 1


def app_id_exists_in_applist(app_list_dir, app_id_to_check):
    """Check if AppID already exists in AppList"""
    if not os.path.exists(app_list_dir):
        return False

    try:
        for filename in os.listdir(app_list_dir):
            if filename.lower().endswith(".txt"):
                filepath = os.path.join(app_list_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content == app_id_to_check:
                            return True
                except (OSError, UnicodeDecodeError):
                    continue
    except OSError:
        pass


def get_most_recent_steam_id() -> Optional[str]:
    """Find the most recently logged in Steam ID64 from loginusers.vdf.

    Uses SteamEnv for Flatpak-aware path detection, with additional
    fallback candidates in case the environment hasn't been built yet.
    """
    # Primary: use SteamEnv (handles both Flatpak and native)
    try:
        env = get_steam_env()
        candidate_paths = []
        if env.loginusers_path:
            candidate_paths.append(env.loginusers_path)
        # Safety fallback: extra native paths in case env resolution missed one
        candidate_paths += [
            Path.home() / ".steam" / "steam" / "config" / "loginusers.vdf",
            Path.home() / ".steam" / "root" / "config" / "loginusers.vdf",
            Path.home() / ".local" / "share" / "Steam" / "config" / "loginusers.vdf",
            Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam" / "config" / "loginusers.vdf",
        ]
    except Exception:
        candidate_paths = [
            Path.home() / ".steam" / "steam" / "config" / "loginusers.vdf",
            Path.home() / ".steam" / "root" / "config" / "loginusers.vdf",
            Path.home() / ".local" / "share" / "Steam" / "config" / "loginusers.vdf",
            Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam" / "config" / "loginusers.vdf",
        ]

    # Deduplicate while preserving order (env path takes priority)
    seen: set = set()
    unique_paths = []
    for p in candidate_paths:
        rp = str(p.resolve()) if p.exists() else str(p)
        if rp not in seen:
            seen.add(rp)
            unique_paths.append(p)

    for path in unique_paths:
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            user_pattern = re.compile(r'"(7656\d+)"\s*\{([^}]+)\}', re.MULTILINE | re.DOTALL)
            users = []
            for match in user_pattern.finditer(content):
                steam_id = match.group(1)
                body = match.group(2)

                ts_match = re.search(r'"Timestamp"\s+"(\d+)"', body)
                timestamp = int(ts_match.group(1)) if ts_match else 0

                al_match = re.search(r'"AutoLogin"\s+"(\d+)"', body)
                auto_login = int(al_match.group(1)) if al_match else 0

                mr_match = re.search(r'"MostRecent"\s+"(\d+)"', body)
                most_recent = int(mr_match.group(1)) if mr_match else 0

                users.append({
                    "steam_id": steam_id,
                    "timestamp": timestamp,
                    "auto_login": auto_login,
                    "most_recent": most_recent,
                })

            if not users:
                continue

            users.sort(
                key=lambda u: (u["auto_login"], u["most_recent"], u["timestamp"]),
                reverse=True,
            )
            logger.debug(f"get_most_recent_steam_id: found {users[0]['steam_id']} from {path}")
            return users[0]["steam_id"]
        except Exception as e:
            logger.error(f"Error parsing loginusers.vdf at {path}: {e}")

    logger.warning("get_most_recent_steam_id: loginusers.vdf not found in any known location")
    return None
