"""
native_steam_handoff.py
=======================
Self-contained engine for handing off games directly to the Steam client.

Performs full registration in SLSsteam (AdditionalApps, AdditionalDepots,
DecryptionKeys, and optional ManifestIds for pinned builds) and signals
Steam to install the game natively, then returns control immediately to
ASSella without blocking the task runner.
"""

import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Callable

from core.native_steam.steam_manifest_pinning import (
    resolve_pinned_manifests,
    set_manifest_ids,
)

logger = logging.getLogger(__name__)

SLSSTEAM_API_PIPE = "/tmp/SLSsteam.API"


def check_slssteam_active() -> bool:
    """Check if SLSsteam.so is loaded in a running Steam process."""
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/comm", "r", encoding="utf-8", errors="ignore") as f:
                    if f.read().strip() != "steam":
                        continue
            except OSError:
                continue
            try:
                with open(f"/proc/{pid_dir}/maps", "r", encoding="utf-8", errors="ignore") as f:
                    if "SLSsteam.so" in f.read():
                        return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def get_sls_config_dir() -> Optional[Path]:
    """Return the active SLSsteam config directory."""
    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        if env.sls_config_dir and env.sls_config_dir.is_dir():
            return env.sls_config_dir
    except Exception:
        pass

    flatpak = Path.home() / ".var/app/com.valvesoftware.Steam/.config/SLSsteam"
    native = Path.home() / ".config/SLSsteam"
    flatpak_steam = Path.home() / ".var/app/com.valvesoftware.Steam/.steam/steam"
    if flatpak_steam.is_dir() and flatpak.is_dir():
        return flatpak
    if native.is_dir():
        return native
    try:
        native.mkdir(parents=True, exist_ok=True)
        return native
    except OSError:
        return None


def get_primary_steamapps(dest_path: str) -> Optional[Path]:
    """Resolve steamapps directory for destination path."""
    if dest_path:
        candidate = Path(dest_path) / "steamapps"
        if candidate.is_dir():
            return candidate
        if Path(dest_path).name == "steamapps" and Path(dest_path).is_dir():
            return Path(dest_path)

    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        if env.steamapps_paths:
            return env.steamapps_paths[0]
    except Exception:
        pass

    for p in [
        Path.home() / ".local/share/Steam/steamapps",
        Path.home() / ".steam/steam/steamapps",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam/steamapps",
    ]:
        if p.is_dir():
            return p
    return None


def resolve_library_index(dest_path: str) -> int:
    """Resolve Steam library folder index for dest_path."""
    try:
        from core.steam_helpers import get_library_index, find_steam_install
        steam_path = find_steam_install()
        return get_library_index(dest_path, steam_path)
    except Exception:
        return 0


def send_sls_api(command: str) -> bool:
    """Send command to /tmp/SLSsteam.API."""
    pipe = Path(SLSSTEAM_API_PIPE)
    if not pipe.exists():
        logger.warning(f"[SteamHandoff] SLS pipe not found: {SLSSTEAM_API_PIPE}")
        return False
    try:
        with open(pipe, "w") as f:
            f.write(command)
            f.flush()
        logger.info(f"[SteamHandoff] SLS sent: {command!r}")
        return True
    except OSError as e:
        logger.warning(f"[SteamHandoff] SLS send failed: {e}")
        return False


def deploy_bundled_plugins(plugins_dir: Path) -> bool:
    """Deploy download.lua and spliced-tickets.lua to plugins directory."""
    plugins_dir.mkdir(parents=True, exist_ok=True)
    candidates = [
        Path(__file__).resolve().parents[2] / "res" / "plugins",
        Path.home() / ".local/share/ACCELA/plugins",
        Path("/home/aiwin/.local/share/ACCELA/plugins"),
    ]
    found = {}
    for base in candidates:
        if base.is_dir():
            for name in ["download.lua", "spliced-tickets.lua"]:
                p = base / name
                if p.is_file() and name not in found:
                    found[name] = p

    if "download.lua" in found and "spliced-tickets.lua" in found:
        for name, src in found.items():
            dest = plugins_dir / name
            try:
                if dest.exists() and dest.read_bytes() == src.read_bytes():
                    logger.debug(f"[SteamHandoff] Plugin already up to date: {name}")
                    continue
            except OSError:
                pass
            shutil.copy2(src, dest)
            logger.info(f"[SteamHandoff] Deployed plugin: {name}")
        return True

    logger.warning("[SteamHandoff] Bundled plugins not found locally")
    return False


def perform_steam_handoff(
    game_data: Dict[str, Any],
    selected_depots: List[str],
    dest_path: str,
    progress_cb: Optional[Callable[[str], None]] = None,
    auto_install: bool = False,
) -> Tuple[bool, str]:
    """
    Perform the complete handoff flow to the Steam client:
      1. Verify Steam + SLSsteam active.
      2. Fetch depot keys & manifest GIDs (using cache / DB first).
      3. Resolve pinned manifests if applicable.
      4. Patch config.yaml atomically with AdditionalApps, AdditionalDepots, and DecryptionKeys.
      5. Deploy plugins.
      6. Wait for SLS license propagation.
      7. Remove stale stub ACF.
      8. If auto_install is True, signal Steam via install|<appid>|<library_index>.
         If auto_install is False (default), unlock in Steam library and return.
    """
    def _emit(msg: str):
        logger.info(f"[SteamHandoff] {msg}")
        if progress_cb:
            progress_cb(msg)

    appid = str(game_data.get("appid", "")).strip()
    game_name = game_data.get("game_name", f"App {appid}")

    if not appid or appid in ("0", "N/A", "unknown"):
        return False, "Invalid AppID provided."

    _emit(f"Initiating Steam handoff for {game_name} ({appid})...")

    # 1. Preflight
    if not check_slssteam_active():
        return False, "Steam is not running with SLSsteam active. Please start Steam first."

    sls_config_dir = get_sls_config_dir()
    if not sls_config_dir:
        return False, "SLSsteam config directory not found."

    steamapps_dir = get_primary_steamapps(dest_path)
    if not steamapps_dir:
        return False, f"Could not resolve steamapps directory for: {dest_path}"

    # 2. Check and clean stale stub ACF
    acf_path = steamapps_dir / f"appmanifest_{appid}.acf"
    if acf_path.exists():
        try:
            txt = acf_path.read_text(encoding="utf-8", errors="ignore")
            if '"buildid"\t\t"0"' in txt or '"buildid"\t"0"' in txt or '"StateFlags"\t\t"1026"' in txt:
                _emit(f"Cleaning up stale stub ACF: {acf_path}")
                acf_path.unlink()
        except OSError as e:
            logger.warning(f"[SteamHandoff] Error checking existing ACF: {e}")

    # 3. Retrieve depot keys and manifest GIDs (cache-first via _fetch_hubcap_keys)
    from core.tasks.native_steam_download_task import NativeSteamDownloadTask
    task_helper = NativeSteamDownloadTask()
    depot_keys, manifest_gids = task_helper._fetch_hubcap_keys(game_data, appid)

    if not depot_keys:
        return False, f"No depot keys available for {game_name} ({appid})."

    # Retain all keys (both main AppID key and all depots) in DecryptionKeys so Steam client
    # and download.lua never fail with Missing Decryption Key / UpdateResult 8
    active_keys = dict(depot_keys)
    if game_data.get("app_key") and str(appid) not in active_keys:
        active_keys[str(appid)] = game_data["app_key"]

    # 4. Check pinned manifests
    pinned_manifests = resolve_pinned_manifests(game_data, appid)
    if pinned_manifests:
        _emit(f"Pinning {len(pinned_manifests)} depot manifest(s) for build stability")

    # 5. Patch config.yaml atomically in a single pass
    config_path = sls_config_dir / "config.yaml"
    _emit("Patching SLSsteam config.yaml with game and depot keys...")

    # Record SLS log offset
    sls_log = sls_config_dir.parent / ".SLSsteam.log"
    if not sls_log.exists():
        sls_log = Path.home() / ".SLSsteam.log"
    log_offset = sls_log.stat().st_size if sls_log.exists() else 0

    patch_ok = task_helper._patch_config(
        config_path, appid, game_name, active_keys, selected_depots=selected_depots
    )
    if not patch_ok:
        return False, "Failed to patch SLSsteam config.yaml."

    # If pinned, write ManifestIds section
    if pinned_manifests:
        set_manifest_ids(config_path, pinned_manifests, comment=f"{game_name} Pinned Build")

    # 6. Deploy plugins
    plugins_dir = sls_config_dir / "plugins"
    _emit("Deploying download.lua plugins...")
    deploy_bundled_plugins(plugins_dir)

    # 7. Wait for license event
    _emit("Waiting for Steam license propagation...")
    task_helper._sls_config_dir = sls_config_dir
    task_helper._poll_license_unlocked(appid, log_offset, timeout_sec=15.0)
    time.sleep(1.5)

    # 8. Send install to Steam API only if auto_install is requested
    if auto_install:
        library_index = resolve_library_index(dest_path)
        _emit(f"Signalling Steam to install {game_name} into library folder {library_index}...")
        sent = send_sls_api(f"install|{appid}|{library_index}")
        if not sent:
            return False, "Failed to send install command to /tmp/SLSsteam.API."

        success_msg = (
            f"Successfully handed off {game_name} ({appid}) to Steam! "
            f"The download has been queued directly inside the Steam client."
        )
    else:
        success_msg = (
            f"Successfully added {game_name} ({appid}) to Steam! "
            f"The game and depot keys are unlocked in your Steam library, ready to install."
        )

    _emit(success_msg)
    return True, success_msg
