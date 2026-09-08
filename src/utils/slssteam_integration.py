"""
SLSsteam Integration Module

Centralized handler for SLSsteam-based install/uninstall operations.
When experimental_acf_independent is enabled, ASSella delegates all ACF
manifest management to Steam natively via SLSsteam's API pipe.

Flow for install/update:
  1. Write appid to config.yaml in-place (preserves inode → inotify fires)
  2. Wait for SLSsteam to acknowledge (log polling, capped fallback)
  3. Send install|appid|0 to /tmp/SLSsteam.API
  4. Optionally verify ACF creation (non-blocking)

Flow for uninstall:
  1. Send uninstall|appid to /tmp/SLSsteam.API
  2. Remove appid from config.yaml in-place

If Steam/SLSsteam is unavailable:
  - Config is still written → Steam will process on next launch
  - API calls fail silently → game shows as "installed" via metadata fallback
  - ASSella's metadata.json serves as the source of truth for library scanning

Path resolution:
  All Steam/SLS paths (log file, steamapps dirs, config.yaml) are resolved
  via SteamEnv in steam_helpers.py, which handles both Flatpak and Native
  Steam installations. The API pipe (/tmp/SLSsteam.API) is always in /tmp
  regardless of installation type.
"""

import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# The API pipe is always /tmp/SLSsteam.API regardless of Flatpak or native.
# All other paths (log, config, steamapps) are resolved lazily via SteamEnv.
SLSSTEAM_API_PIPE = "/tmp/SLSsteam.API"
MAX_CONFIG_WAIT_SECONDS = 10
MAX_ACF_VERIFY_SECONDS = 10

# ---------------------------------------------------------------------------
# Shutdown coordination
# ---------------------------------------------------------------------------
# Set this event to signal all background workers to exit immediately.
# Call register_shutdown() from the application's teardown path.
_shutdown_flag = threading.Event()

# Registry of per-install cancel events: appid -> threading.Event
# Lets uninstall_via_sls cancel a live retry worker for the same appid.
_retry_cancel_flags: dict = {}
_retry_lock = threading.Lock()


def register_shutdown() -> None:
    """Signal all SLS background workers to stop. Call this on app exit."""
    _shutdown_flag.set()
    logger.info("SLSsteam integration: shutdown signalled — all retry workers will stop")


# ---------------------------------------------------------------------------
# Path helpers — resolved lazily via SteamEnv
# ---------------------------------------------------------------------------

def _get_sls_log_path() -> Path:
    """Return the SLSsteam log file path for the current Steam installation.

    - Flatpak: ~/.var/app/com.valvesoftware.Steam/.SLSsteam.log
    - Native:  ~/.SLSsteam.log

    Never raises — falls back to the native path if SteamEnv is unavailable.
    """
    try:
        from core.steam_helpers import get_steam_env
        return get_steam_env().sls_log_path
    except Exception as e:
        logger.warning(f"_get_sls_log_path: SteamEnv unavailable ({e}), using native fallback")
        return Path.home() / ".SLSsteam.log"


def _get_steamapps_paths() -> List[Path]:
    """Return all steamapps directories to search for ACF manifests.

    Includes the primary Steam library and any additional libraries from
    libraryfolders.vdf, across both Flatpak and native installations.

    Never raises — falls back to native default paths if SteamEnv is unavailable.
    """
    try:
        from core.steam_helpers import get_steam_env
        paths = get_steam_env().steamapps_paths
        if paths:
            return paths
    except Exception as e:
        logger.warning(f"_get_steamapps_paths: SteamEnv unavailable ({e}), using native fallback")

    # Fallback: best-effort native + Flatpak defaults
    fallback = []
    native = Path.home() / ".local" / "share" / "Steam" / "steamapps"
    flatpak = Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam" / "steamapps"
    for p in (native, flatpak):
        if p.is_dir():
            fallback.append(p)
    return fallback


def _experimental_mode_enabled() -> bool:
    try:
        from utils.settings import get_settings
        return get_settings().value("experimental_acf_independent", False, type=bool)
    except Exception:
        return False


def _is_slssteam_available() -> bool:
    """Check if SLSsteam API pipe exists (proxy for Steam + SLSsteam running)."""
    if sys.platform != "linux":
        return False
    return os.path.exists(SLSSTEAM_API_PIPE)


_proc_cache = {
    "last_check": 0.0,
    "steam_running": False,
    "slssteam_active": False,
}
_PROC_CACHE_TTL = 2.0  # seconds


def _update_proc_cache_if_needed():
    """Scan /proc at most once every _PROC_CACHE_TTL seconds."""
    import time
    now = time.time()
    if now - _proc_cache["last_check"] < _PROC_CACHE_TTL:
        return

    _proc_cache["last_check"] = now
    _proc_cache["steam_running"] = False
    _proc_cache["slssteam_active"] = False

    if sys.platform != "linux":
        return

    try:
        steam_pids = []
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                comm_path = os.path.join("/proc", pid_dir, "comm")
                with open(comm_path, "r", encoding="utf-8", errors="ignore") as f:
                    comm = f.read().strip()
                if comm == "steam":
                    steam_pids.append(pid_dir)
            except OSError:
                continue

        if steam_pids:
            _proc_cache["steam_running"] = True
            for pid in steam_pids:
                maps_path = os.path.join("/proc", pid, "maps")
                try:
                    if os.path.exists(maps_path):
                        with open(maps_path, "r", encoding="utf-8", errors="ignore") as f:
                            for line in f:
                                if "SLSsteam.so" in line:
                                    _proc_cache["slssteam_active"] = True
                                    return
                except OSError:
                    continue
    except OSError:
        pass


def is_slssteam_process_active() -> bool:
    """Check if SLSsteam.so is loaded in Steam's memory space using /proc maps.
    Cached for 2 seconds to avoid freezing the UI on repeated calls.
    """
    _update_proc_cache_if_needed()
    return _proc_cache["slssteam_active"]


def is_steam_process_running() -> bool:
    """Check if the steam process is currently running on the system.
    Cached for 2 seconds to avoid freezing the UI on repeated calls.
    """
    _update_proc_cache_if_needed()
    return _proc_cache["steam_running"]


_binary_version_cache: dict = {}
_binary_version_cache_lock = threading.Lock()
_BINARY_VERSION_CACHE_TTL = 120.0  # seconds


def check_slssteam_binary_is_latest(force_refresh: bool = False) -> dict:
    """Compare local SLSsteam.so timestamp/version against upstream GitHub release.

    Checks the upstream GitHub release timestamp ('published_at') against the
    local SLSsteam.so modification time ('mtime') or local version tracker file.
    If the local binary is older than the release timestamp (with 2-minute build
    tolerance), it is marked 'outdated'. This operates instantly without
    downloading the full multi-megabyte 7z archive.

    Cached for 120 seconds to prevent redundant network calls.

    Returns a dict:
        {
            "status":        "up_to_date" | "outdated" | "no_local" | "error",
            "release_tag":   str | None,   # e.g. "20260903114323"
            "release_time":  float | None, # UTC timestamp
            "local_mtime":   float | None, # local file mtime
            "local_hash":    None,
            "remote_hash":   None,
            "error":         str | None,
        }

    This function is blocking and should be called from a background thread.
    """
    global _binary_version_cache
    now = time.time()
    if not force_refresh:
        with _binary_version_cache_lock:
            if _binary_version_cache and (now - _binary_version_cache.get("timestamp", 0) < _BINARY_VERSION_CACHE_TTL):
                return dict(_binary_version_cache["data"])

    import json
    import urllib.request
    from datetime import datetime, timezone

    result: dict = {
        "status": "error",
        "release_tag": None,
        "release_time": None,
        "local_mtime": None,
        "local_hash": None,
        "remote_hash": None,
        "error": None,
    }

    # ── 1. Find local .so ──────────────────────────────────────────────────
    try:
        from ui.dialogs.settings_sls import get_sls_paths
        paths = get_sls_paths()
        local_so = paths.get("so_path", "")
        version_file = paths.get("version_file", "")
    except Exception:
        local_so = os.path.expanduser("~/.local/share/SLSsteam/SLSsteam.so")
        version_file = os.path.expanduser("~/.local/share/SLSsteam/version")

    if not local_so or not os.path.exists(local_so):
        result["status"] = "no_local"
        result["error"] = "SLSsteam.so not found locally"
        return result

    try:
        local_mtime = os.path.getmtime(local_so)
        result["local_mtime"] = local_mtime
    except Exception as exc:
        result["error"] = f"Failed to inspect local binary mtime: {exc}"
        return result

    # ── 2. Fetch latest GitHub release metadata ────────────────────────────
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/AceSLS/SLSsteam/releases/latest",
            headers={"User-Agent": "ASSella-SLS-Updater"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        release_tag = data.get("tag_name")
        result["release_tag"] = release_tag

        pub_str = data.get("published_at") or data.get("created_at")
        release_time = 0.0
        if pub_str:
            try:
                dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                release_time = dt.timestamp()
            except Exception:
                pass

        if not release_time and release_tag:
            try:
                dt = datetime.strptime(release_tag[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                release_time = dt.timestamp()
            except Exception:
                pass

        result["release_time"] = release_time

        # If version tracker matches exact release tag, definitely up to date
        if version_file and os.path.exists(version_file):
            try:
                with open(version_file, "r", encoding="utf-8") as vf:
                    local_ver = vf.read().strip()
                if local_ver and release_tag and local_ver == release_tag:
                    result["status"] = "up_to_date"
                    with _binary_version_cache_lock:
                        _binary_version_cache = {"timestamp": time.time(), "data": dict(result)}
                    return result
            except Exception:
                pass

        # Time-based comparison with 2-minute build/publication tolerance
        tolerance_sec = 120.0
        if release_time > 0 and local_mtime < (release_time - tolerance_sec):
            result["status"] = "outdated"
        else:
            result["status"] = "up_to_date"

        with _binary_version_cache_lock:
            _binary_version_cache = {"timestamp": time.time(), "data": dict(result)}

    except Exception as exc:
        result["error"] = f"GitHub API error: {exc}"

    return result



def _slssteam_api_send(command: str) -> bool:
    """Send a raw command to SLSsteam via the named pipe.

    Returns True on success.  Returns False (never raises) on any failure —
    including the pipe being gone because Steam/SLSsteam closed mid-write.
    """
    if not _is_slssteam_available():
        return False
    try:
        with open(SLSSTEAM_API_PIPE, "w") as f:
            f.write(command)
            f.flush()
        logger.info(f"SLSsteam API command sent: {command}")
        return True
    except OSError as e:
        # ENXIO / EPIPE = pipe exists on disk but reader (SLSsteam) has gone away.
        # Log at warning level so it's visible but doesn't crash anything.
        logger.warning(
            f"SLSsteam API pipe write failed for '{command}': {e} "
            "(Steam/SLSsteam may have closed — command will be retried or skipped)"
        )
        return False


def _poll_sls_log_for(
    pattern: str,
    timeout_seconds: int = MAX_CONFIG_WAIT_SECONDS,
    start_offset: int = 0,
) -> bool:
    """Poll SLSsteam log for a regex pattern in NEW content only.

    The log path is resolved via SteamEnv (Flatpak or native).
    Returns True if found before deadline.
    Exits early if the global shutdown flag is set.
    """
    log_path = _get_sls_log_path()
    if not log_path.exists():
        if _experimental_mode_enabled():
            logger.warning(
                f"SLSsteam log not found at {log_path}. "
                "Cannot poll for license events. Proceeding without confirmation."
            )
        return False

    compiled = re.compile(pattern)
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        if _shutdown_flag.is_set():
            logger.debug("_poll_sls_log_for: shutdown flag set — aborting poll")
            return False
        try:
            current_size = log_path.stat().st_size
            if current_size > start_offset:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(start_offset)
                    for line in f:
                        if compiled.search(line):
                            return True
                start_offset = current_size
        except (OSError, IOError):
            pass
        time.sleep(0.3)

    return False


def _get_sls_log_size() -> int:
    """Return current SLS log file size for offset-based polling."""
    log_path = _get_sls_log_path()
    try:
        return log_path.stat().st_size if log_path.exists() else 0
    except (OSError, IOError):
        return 0


def _write_appid_to_config(appid: str, game_name: str = "") -> bool:
    """Write appid into SLSsteam config.yaml AdditionalApps in-place."""
    from utils.yaml_config_manager import get_user_config_path, add_additional_app
    config_path = get_user_config_path()
    comment = game_name if game_name else ""
    return add_additional_app(config_path, str(appid), comment)


def _remove_appid_from_config(appid: str) -> bool:
    """Remove appid from SLSsteam config.yaml AdditionalApps in-place."""
    from utils.yaml_config_manager import get_user_config_path, remove_additional_app
    config_path = get_user_config_path()
    return remove_additional_app(config_path, str(appid))


def _wait_for_sls_license(appid: str, log_offset: int) -> bool:
    """Wait for SLSsteam to fully process config change AND unlock the license.

    Polls ~/.SLSsteam.log for AppLicensesChanged or Unlocked lines, then
    sleeps 1.5 s to let SLSsteam propagate the license into Steam's memory
    before the pipe command is sent.

    Returns True if the license event was confirmed in the log.
    """
    pattern = rf"(?:AppLicensesChanged callback invoked for {re.escape(str(appid))}|Unlocked {re.escape(str(appid))})"
    found = _poll_sls_log_for(
        pattern,
        timeout_seconds=MAX_CONFIG_WAIT_SECONDS,
        start_offset=log_offset,
    )
    if found:
        logger.info(
            f"SLSsteam license for {appid} confirmed via log — sleeping 1.5s for memory propagation"
        )
        # Interruptible sleep: wake early on shutdown
        _shutdown_flag.wait(timeout=1.5)
    else:
        logger.info(f"SLSsteam license for {appid} not confirmed via log — proceeding anyway")
    return found


def _verify_acf_created(appid: str, timeout: Optional[float] = None) -> bool:
    """Poll for ACF manifest creation by Steam across all known library paths.

    Searches all steamapps directories from SteamEnv, which covers:
    - Primary Steam library (Flatpak or native)
    - Any additional libraries from libraryfolders.vdf
    Both native and Flatpak paths are handled automatically.
    """
    acf_filename = f"appmanifest_{appid}.acf"

    # Get all steamapps paths from SteamEnv (Flatpak-aware, includes extra libraries)
    steamapps_dirs = _get_steamapps_paths()

    # Build candidate ACF paths
    candidate_paths = [sa / acf_filename for sa in steamapps_dirs if sa.is_dir()]

    if not candidate_paths:
        logger.warning(
            f"_verify_acf_created: no valid steamapps directories found to search for {appid}. "
            "Steam may not be installed or SteamEnv detection failed."
        )

    verify_seconds = timeout if timeout is not None else MAX_ACF_VERIFY_SECONDS
    deadline = time.time() + verify_seconds
    while time.time() < deadline:
        for p in candidate_paths:
            if p.exists():
                logger.info(f"Steam created ACF manifest for {appid} at {p}")
                return True
        time.sleep(0.5)

    searched = ", ".join(str(p) for p in candidate_paths)
    logger.warning(
        f"ACF manifest for {appid} not found after {verify_seconds}s. "
        f"Searched: {searched} — Steam may create it later"
    )
    return False


def _silent_background_retry_pipe(
    appid: str,
    library_index: int,
    max_retries: int = 5,
    library_path: str = "",
) -> None:
    """Start a daemon thread that retries the install pipe command if Steam was slow.

    The worker stops as soon as any of the following is true:
      • The ACF file appears on disk (success — no more retries needed).
      • The per-install cancel event is set (uninstall_via_sls was called for this appid).
      • The global shutdown flag is set (application is closing).
      • SLSsteam pipe disappears (Steam closed mid-retry).
      • max_retries iterations are exhausted.

    Each retry waits 5 s between attempts (interruptible by the events above).
    """
    cancel_event = threading.Event()
    with _retry_lock:
        # Cancel any previous retry worker for this appid before starting a new one
        old_event = _retry_cancel_flags.get(appid)
        if old_event:
            old_event.set()
        _retry_cancel_flags[appid] = cancel_event

    def _retry_worker():
        try:
            for i in range(max_retries):
                # Interruptible 5-second wait — wakes on cancel or shutdown
                cancelled = cancel_event.wait(timeout=5)
                if cancelled or _shutdown_flag.is_set():
                    logger.info(
                        f"SLS retry worker for {appid}: "
                        f"{'cancelled (uninstall called)' if cancelled else 'app shutdown'} — stopping"
                    )
                    return

                if _verify_acf_created(appid, timeout=1.0):
                    logger.info(
                        f"Silent retry: ACF confirmed for AppID {appid} on attempt {i + 1} — done"
                    )
                    return

                if not _is_slssteam_available():
                    logger.info(
                        f"SLS retry worker for {appid}: SLSsteam pipe gone (Steam closed) — stopping"
                    )
                    return

                if i >= 4 and library_path:
                    try:
                        from core.steam_api import get_depot_info_from_api
                        from utils.steam_manifest import write_acf_file
                        info = get_depot_info_from_api(appid)
                        if info:
                            acf_file = write_acf_file(library_path, info, size_on_disk=0, include_depots=True, logger=logger)
                            logger.info(f"Fallback ACF file generated by ACCELA at {acf_file}")
                            if _verify_acf_created(appid, timeout=1.0):
                                return
                    except Exception as acf_err:
                        logger.warning(f"Fallback ACF generation failed for {appid}: {acf_err}")

                logger.info(
                    f"Silent background retry ({i + 1}/{max_retries}): "
                    f"sending install|{appid}|{library_index}..."
                )
                _slssteam_api_send(f"install|{appid}|{library_index}")

            logger.warning(
                f"SLS retry worker for {appid}: exhausted {max_retries} retries without ACF confirmation"
            )
        finally:
            # Clean up the registry entry so it doesn't leak
            with _retry_lock:
                if _retry_cancel_flags.get(appid) is cancel_event:
                    del _retry_cancel_flags[appid]

    t = threading.Thread(target=_retry_worker, daemon=True, name=f"SLSRetry-{appid}")
    t.start()


def install_via_sls(appid: str, game_name: str = "", library_path: str = "") -> bool:
    """Register a game with Steam via SLSsteam.

    Writes appid to SLS config, waits for SLS to process,
    then sends install API command to Steam.

    Returns True if the operation completed (even partially — config write alone
    is sufficient for Steam to register the game on next launch).
    Never raises — all failure paths are logged and handled gracefully.

    Fallback behaviour when Steam/SLSsteam is unavailable:
      • AppID is still written to config.yaml so Steam processes it on next launch.
      • Pipe failures are caught and logged — no crash, no exception propagated.
      • metadata.json written by the caller is the authoritative library source.
    """
    if not _experimental_mode_enabled():
        logger.debug("SLSsteam experimental mode disabled — skipping install")
        return False

    if not appid or appid in ("0", "N/A", "unknown"):
        return False

    if _shutdown_flag.is_set():
        logger.debug(f"install_via_sls: shutdown in progress — skipping for {appid}")
        return False

    # 0. Just-in-time check: ensure API: yes and LogLevels (0x2) if externally modified
    try:
        from utils.yaml_config_manager import ensure_slssteam_prerequisites
        ensure_slssteam_prerequisites()
    except Exception as e:
        logger.debug(f"install_via_sls: prerequisites check error: {e}")

    # 1. Write to config in-place (always, even if Steam is closed)
    try:
        written = _write_appid_to_config(appid, game_name)
    except Exception as e:
        logger.error(f"install_via_sls: failed to write {appid} to SLS config: {e}")
        written = False

    if not written:
        logger.info(f"AppID {appid} already in SLS config or write failed")
    else:
        logger.info(f"Wrote AppID {appid} to SLS config")

    # 2. If SLSsteam/Steam is not available, stop here — config write is sufficient
    if not _is_slssteam_available():
        logger.info(
            f"SLSsteam/Steam not available — AppID {appid} recorded in config.yaml. "
            "Steam will register the game on next launch."
        )
        return True  # Graceful: config write alone is valid

    # 3. Wait for SLS to unlock license — only if we freshly wrote this appid
    if written:
        log_offset = _get_sls_log_size()
        _wait_for_sls_license(appid, log_offset)

    if _shutdown_flag.is_set():
        logger.debug(f"install_via_sls: shutdown during license wait — skipping pipe for {appid}")
        return True  # Config was written; that's enough

    # 4. Resolve the Steam library index for this install path
    library_index = 0
    if library_path:
        try:
            from core.steam_helpers import get_library_index, find_steam_install
            steam_path = find_steam_install()
            library_index = get_library_index(library_path, steam_path)
            logger.info(f"Resolved library index {library_index} for path: {library_path}")
        except Exception as e:
            logger.warning(f"Could not resolve library index, defaulting to 0: {e}")

    # 5. Send install to Steam (failure is non-fatal)
    t_pipe_0 = time.time()
    sent = _slssteam_api_send(f"install|{appid}|{library_index}")
    pipe_latency = (time.time() - t_pipe_0) * 1000.0

    if not sent:
        logger.warning(
            f"install_via_sls: pipe send failed for {appid} — "
            "config is written, Steam will handle it on next launch"
        )
        return True  # Config is still written; not a hard failure

    # 6. Verify ACF creation. If delayed, launch cancellable background retry worker
    acf_created = _verify_acf_created(appid)


    if not acf_created:
        logger.info(
            f"ACF creation delayed for {appid} — "
            "launching cancellable background retry worker..."
        )
        _silent_background_retry_pipe(appid, library_index, library_path=library_path)

    return True


def uninstall_via_sls(appid: str) -> bool:
    """Unregister a game from Steam via SLSsteam.

    Sends uninstall API command to Steam to delete the ACF,
    then removes the appid from SLS config.

    Also cancels any live install-retry worker for this appid so a
    rapid install→uninstall sequence doesn't leave orphaned pipe commands.

    Returns True if both operations were attempted without error.
    Never raises — all failures are logged.
    """
    if not _experimental_mode_enabled():
        logger.debug("SLSsteam experimental mode disabled — skipping uninstall")
        return False

    if not appid or appid in ("0", "N/A", "unknown"):
        return False

    # Cancel any live retry worker for this appid immediately
    with _retry_lock:
        cancel_event = _retry_cancel_flags.get(appid)
    if cancel_event:
        cancel_event.set()
        logger.info(f"uninstall_via_sls: cancelled live retry worker for {appid}")

    success = True

    # 1. Send uninstall to Steam first (before file deletion so Steam sees the event)
    if _is_slssteam_available():
        sent = _slssteam_api_send(f"uninstall|{appid}")
        if not sent:
            logger.warning(
                f"uninstall_via_sls: pipe send failed for {appid} — "
                "ACF may need manual cleanup if Steam was running"
            )
            success = False
    else:
        logger.info(
            f"SLSsteam not available — {appid} removed from config.yaml. "
            "Steam will unregister on next launch."
        )

    # 2. Remove from config regardless of pipe result
    try:
        removed = _remove_appid_from_config(appid)
    except Exception as e:
        logger.error(f"uninstall_via_sls: failed to remove {appid} from SLS config: {e}")
        removed = False

    if not removed:
        logger.debug(f"AppID {appid} not found in SLS config (already removed?)")
    else:
        logger.info(f"Removed AppID {appid} from SLS config")

    return success


def patch_acf_via_sls(appid: str, library_path: str = "") -> bool:
    """Re-install an existing game via SLSsteam to fix a missing or stale ACF.

    Used by the 'Fix Manifest' feature in the library UI.
    Returns False (does not raise) if SLSsteam is unavailable.
    """
    if not _experimental_mode_enabled():
        return False

    if not appid or appid in ("0", "N/A", "unknown"):
        return False

    # Just-in-time check: ensure API: yes and LogLevels (0x2) if externally modified
    try:
        from utils.yaml_config_manager import ensure_slssteam_prerequisites
        ensure_slssteam_prerequisites()
    except Exception as e:
        logger.debug(f"patch_acf_via_sls: prerequisites check error: {e}")

    if not _is_slssteam_available():
        logger.warning(
            f"patch_acf_via_sls: SLSsteam not available for {appid} — "
            "ensure Steam and SLSsteam are running, then try again"
        )
        return False

    library_index = 0
    if library_path:
        try:
            from core.steam_helpers import get_library_index, find_steam_install
            steam_path = find_steam_install()
            library_index = get_library_index(library_path, steam_path)
        except Exception as e:
            logger.warning(f"Could not resolve library index for patch, defaulting to 0: {e}")

    return _slssteam_api_send(f"install|{appid}|{library_index}")


# ---------------------------------------------------------------------------
# Diagnostics & user-facing warnings
# ---------------------------------------------------------------------------

def get_sls_diagnostic() -> Dict[str, Any]:
    """Return a structured dict describing the current SLS environment state.

    Useful for surfacing issues in the UI, logs, or settings panel.

    Keys:
        is_flatpak        (bool)  True if Flatpak Steam was detected
        steam_found       (bool)  True if a Steam installation was located
        steam_path        (str|None)  Resolved Steam root, or None
        steam_type        (str)   "Flatpak" or "Native"
        config_exists     (bool)  True if config.yaml exists on disk
        config_path       (str)   Resolved config.yaml path
        log_exists        (bool)  True if .SLSsteam.log exists on disk
        log_path          (str)   Resolved log path
        pipe_available    (bool)  True if /tmp/SLSsteam.API exists (SLS running)
        sls_installed     (bool)  True if SLSsteam.so found in install dir
        sls_install_dir   (str)   Expected SLSsteam install directory
        steamapps_paths   (list)  All steamapps dirs found
    """
    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        return {
            "is_flatpak": env.is_flatpak,
            "steam_found": env.steam_found(),
            "steam_path": env.steam_path,
            "steam_type": "Flatpak" if env.is_flatpak else "Native",
            "config_exists": env.sls_config_exists(),
            "config_path": str(env.sls_config_path),
            "log_exists": env.sls_log_exists(),
            "log_path": str(env.sls_log_path),
            "pipe_available": _is_slssteam_available(),
            "sls_installed": env.sls_installed(),
            "sls_install_dir": str(env.sls_install_dir),
            "steamapps_paths": [str(p) for p in env.steamapps_paths],
        }
    except Exception as e:
        logger.error(f"get_sls_diagnostic: SteamEnv unavailable: {e}")
        return {
            "is_flatpak": False,
            "steam_found": False,
            "steam_path": None,
            "steam_type": "Unknown",
            "config_exists": False,
            "config_path": str(Path.home() / ".config" / "SLSsteam" / "config.yaml"),
            "log_exists": False,
            "log_path": str(Path.home() / ".SLSsteam.log"),
            "pipe_available": _is_slssteam_available(),
            "sls_installed": False,
            "sls_install_dir": str(Path.home() / ".local" / "share" / "SLSsteam"),
            "steamapps_paths": [],
        }


def warn_sls_unavailable(context: str = "") -> str:
    """Build and log a user-facing warning message when SLSsteam is unavailable.

    Returns the warning text so callers can surface it via Qt signals or
    status bar messages.  Also logs the full diagnostic at WARNING level.

    Args:
        context: short description of what operation triggered the check
                 (e.g. "post-install", "patch ACF", "uninstall").
    """
    diag = get_sls_diagnostic()
    ctx_prefix = f"[{context}] " if context else ""

    issues = []
    if not diag["steam_found"]:
        issues.append("Steam installation not found")
    if not diag["sls_installed"]:
        issues.append(f"SLSsteam not installed (expected: {diag['sls_install_dir']})")
    if not diag["config_exists"]:
        issues.append(f"config.yaml missing (expected: {diag['config_path']})")
    if not diag["pipe_available"]:
        issues.append("SLSsteam API pipe not available — Steam may not be running with SLSsteam loaded")

    if issues:
        issues_text = "; ".join(issues)
        msg = (
            f"{ctx_prefix}SLSsteam not available — {issues_text}. "
            "Falling back to manual ACF write. "
            "Make sure Steam is launched with SLSsteam loaded."
        )
    else:
        # Pipe just went away mid-operation
        msg = (
            f"{ctx_prefix}SLSsteam API pipe unexpectedly unavailable. "
            "Steam may have closed. Config was written; Steam will register the game on next launch."
        )

    logger.warning(msg)
    logger.warning(
        f"SLS diagnostic ({diag['steam_type']} Steam): "
        f"steam={diag['steam_path'] or 'NOT FOUND'}, "
        f"config={'OK' if diag['config_exists'] else 'MISSING'}, "
        f"log={'OK' if diag['log_exists'] else 'MISSING'}, "
        f"pipe={'OK' if diag['pipe_available'] else 'MISSING'}, "
        f"sls_so={'OK' if diag['sls_installed'] else 'MISSING'}"
    )
    return msg
