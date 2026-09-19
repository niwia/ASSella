"""
NativeSteamDownloadTask
=======================
Backend that uses the native Steam client to download game files
instead of DepotDownloaderMod (DDM).

Workflow:
  1. Reads depot keys and manifest GIDs from Hubcap bundle / cache.
  2. Patches config.yaml with Plugins: yes, AdditionalApps, AdditionalDepots,
     and DecryptionKeys atomically with fsync.
  3. Deploys download.lua and spliced-tickets.lua to ~/.config/SLSsteam/plugins/
     (prioritizing locally bundled copies).
  4. Waits for AppLicensesChanged in .SLSsteam.log for the AppID, followed
     by a 1.5s delay for Steam memory propagation.
  5. Cleans up any stale stub ACF (buildid == 0) from past failed runs.
  6. Resolves library index and sends install|<appid>|<lib_idx> to /tmp/SLSsteam.API.
  7. Monitors content_log.txt and appmanifest_<appid>.acf for download progress,
     completion (StateFlags: 4), and fails fast on errors.
  8. Cleanup:
     - On success: keep AppID in AdditionalApps; remove AdditionalDepots,
       DecryptionKeys, and transient plugins.
     - On failure/cancel: restore config.yaml backup, remove plugins, delete
       incomplete ACF, and send uninstall|<appid> to Steam API.
"""

import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

DOWNLOAD_LUA_URL = (
    "https://github.com/ciscosweater/enter-the-wired/releases/download/latest/plugins-deps.zip"
)
SLSSTEAM_API_PIPE = "/tmp/SLSsteam.API"
MONITOR_TIMEOUT_SECONDS = 3600
POLL_INTERVAL_SECONDS = 1.5

ACF_STATE_UNINSTALLED = 0
ACF_STATE_INSTALLED = 4
ACF_STATE_INSTALLING = 1026
ACF_STATE_UPDATE_REQUIRED = 1028


class NativeSteamDownloadTask(QObject):
    """
    Manages the native Steam client download pipeline for a game.

    Emits signals compatible with DownloadDepotsTask so task_manager
    and UI can track progress seamlessly.
    """

    progress = pyqtSignal(str)
    progress_percentage = pyqtSignal(int)
    speed_update = pyqtSignal(str)
    completed = pyqtSignal()
    error = pyqtSignal(tuple)

    def __init__(self):
        super().__init__()
        self._is_running = True
        self.process = None
        self.process_pid = None
        self.total_download_size_for_this_job = 0
        self.completed_so_far_for_this_job = 0
        self.current_depot_size = 0
        self._config_backup_path: Optional[Path] = None
        self._deployed_plugins: List[Path] = []
        self._sls_config_dir: Optional[Path] = None
        self._appid: str = ""
        self._game_name: str = ""
        self._acf_path: Optional[Path] = None

    @property
    def is_running_flag(self) -> bool:
        return self._is_running

    def stop(self):
        self._is_running = False

    def toggle_pause(self, pause: bool):
        logger.info(f"[NativeSteamDL] toggle_pause called (pause={pause}) - managed via Steam client")

    def run(self, game_data: Dict[str, Any], selected_depots: List[str], dest_path: str):
        appid = str(game_data.get("appid", ""))
        game_name = game_data.get("game_name", f"App {appid}")
        self._appid = appid
        self._game_name = game_name
        self._game_data = game_data

        logger.info(f"[NativeSteamDL] Starting native Steam download: {game_name} ({appid})")
        self.progress.emit(f"[Native Steam] Starting download for {game_name} ({appid})")

        try:
            self._run_impl(game_data, selected_depots, dest_path, appid, game_name)
        except Exception as e:
            logger.exception(f"[NativeSteamDL] Unexpected error: {e}")
            self._cleanup_failure()
            self.error.emit((type(e), str(e), None))

    def _run_impl(
        self,
        game_data: Dict[str, Any],
        selected_depots: List[str],
        dest_path: str,
        appid: str,
        game_name: str,
    ):
        # 1. Preflight checks
        self.progress.emit("[Native Steam] Checking prerequisites...")

        if not self._check_slssteam_active():
            msg = (
                "Steam is not running with SLSsteam active. "
                "Please start Steam first, then try again."
            )
            self.progress.emit(f"[Native Steam] ERROR: {msg}")
            self.error.emit((RuntimeError, msg, None))
            return

        sls_config_dir = self._get_config_dir()
        if not sls_config_dir:
            msg = "SLSsteam config directory not found."
            self.progress.emit(f"[Native Steam] ERROR: {msg}")
            self.error.emit((RuntimeError, msg, None))
            return
        self._sls_config_dir = sls_config_dir

        steamapps_dir = self._get_primary_steamapps(dest_path)
        if not steamapps_dir:
            msg = f"Could not resolve a steamapps directory in: {dest_path}"
            self.progress.emit(f"[Native Steam] ERROR: {msg}")
            self.error.emit((RuntimeError, msg, None))
            return

        acf_path = steamapps_dir / f"appmanifest_{appid}.acf"
        self._acf_path = acf_path

        # Check and remove stale stub ACF from previous failed attempts
        if acf_path.exists():
            try:
                txt = acf_path.read_text(encoding="utf-8", errors="ignore")
                if '"StateFlags"\t\t"4"' in txt or '"StateFlags"\t"4"' in txt:
                    logger.info(f"[NativeSteamDL] Game {appid} is already installed with StateFlags=4")
                elif '"buildid"\t\t"0"' in txt or '"buildid"\t"0"' in txt or '"StateFlags"\t\t"1026"' in txt:
                    logger.info(f"[NativeSteamDL] Removing stale stub ACF: {acf_path}")
                    acf_path.unlink()
            except OSError as e:
                logger.warning(f"[NativeSteamDL] Error checking existing ACF: {e}")

        self.progress.emit("[Native Steam] Prerequisites OK")
        if not self._is_running:
            return

        # 2. Fetch Hubcap bundle
        self.progress.emit("[Native Steam] Fetching depot keys from Hubcap...")
        depot_keys, manifest_gids = self._fetch_hubcap_keys(game_data, appid)
        if not depot_keys:
            msg = "No depot keys available from Hubcap for this game."
            self.progress.emit(f"[Native Steam] ERROR: {msg}")
            self.error.emit((RuntimeError, msg, None))
            return
        self.progress.emit(f"[Native Steam] Got {len(depot_keys)} depot key(s) from Hubcap")
        if not self._is_running:
            return

        # Record SLS log offset before patching config so we only detect new license events
        sls_log = self._get_sls_log_path()
        log_offset = sls_log.stat().st_size if sls_log.exists() else 0

        # 3. Patch config.yaml (BEFORE deploying plugins)
        config_path = sls_config_dir / "config.yaml"
        self.progress.emit("[Native Steam] Patching SLSsteam config.yaml...")
        patch_ok = self._patch_config(config_path, appid, game_name, depot_keys)
        if not patch_ok:
            msg = "Failed to patch SLSsteam config.yaml."
            self.progress.emit(f"[Native Steam] ERROR: {msg}")
            self._cleanup_failure()
            self.error.emit((RuntimeError, msg, None))
            return
        self.progress.emit("[Native Steam] config.yaml patched with depot keys")

        # 3b. Check and apply pinned manifest GIDs (for recommended/pinned builds)
        try:
            from core.native_steam.steam_manifest_pinning import (
                resolve_pinned_manifests,
                set_manifest_ids,
            )
            pinned_manifests = resolve_pinned_manifests(game_data, appid)
            if pinned_manifests:
                self.progress.emit(
                    f"[Native Steam] Pinning {len(pinned_manifests)} depot manifest(s) for build stability..."
                )
                set_manifest_ids(config_path, pinned_manifests, comment=f"{game_name} Pinned Build")
        except Exception as e:
            logger.warning(f"[NativeSteamDL] Error applying pinned manifests: {e}")

        if not self._is_running:
            self._cleanup_failure()
            return

        # 4. Deploy download.lua and spliced-tickets.lua plugins
        plugins_dir = sls_config_dir / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        self.progress.emit("[Native Steam] Deploying download.lua plugin...")
        plugin_ok = self._deploy_plugin(plugins_dir)
        if not plugin_ok:
            msg = "Failed to deploy download.lua plugin."
            self.progress.emit(f"[Native Steam] ERROR: {msg}")
            self._cleanup_failure()
            self.error.emit((RuntimeError, msg, None))
            return
        self.progress.emit("[Native Steam] download.lua plugin deployed")
        if not self._is_running:
            self._cleanup_failure()
            return

        # 5. Wait for SLSsteam to unlock license
        self.progress.emit("[Native Steam] Waiting for SLSsteam license unlock...")
        license_ok = self._poll_license_unlocked(appid, log_offset, timeout_sec=20.0)
        if not license_ok:
            logger.warning(
                f"[NativeSteamDL] License event for {appid} not seen in SLS log within 20s. Proceeding anyway."
            )
        else:
            self.progress.emit("[Native Steam] License unlocked in Steam")

        # Sleep 1.5s for Steam internal memory propagation
        time.sleep(1.5)
        if not self._is_running:
            self._cleanup_failure()
            return

        # 6. Resolve library index and trigger install
        library_index = self._resolve_library_index(dest_path)
        self.progress.emit(f"[Native Steam] Triggering Steam install (library {library_index})...")
        sent = self._send_sls(f"install|{appid}|{library_index}")
        if not sent:
            msg = "Failed to communicate with /tmp/SLSsteam.API"
            self.progress.emit(f"[Native Steam] ERROR: {msg}")
            self._cleanup_failure()
            self.error.emit((RuntimeError, msg, None))
            return

        # 7. Monitor download progress
        self.progress.emit("[Native Steam] Monitoring Steam download progress...")
        self.progress_percentage.emit(0)
        success = self._monitor_download(acf_path, appid, steamapps_dir, game_name)

        # 8. Finalize / Cleanup
        if success and self._is_running:
            self.progress.emit(f"[Native Steam] Download complete: {game_name}")
            self._cleanup_success(config_path, plugins_dir)
            self.completed.emit()
        elif not self._is_running:
            self.progress.emit("[Native Steam] Download cancelled")
            self._cleanup_failure()
        else:
            msg = "Steam download did not complete successfully. Check Steam and logs for details."
            self.progress.emit(f"[Native Steam] ERROR: {msg}")
            self._cleanup_failure()
            self.error.emit((RuntimeError, msg, None))

    def _check_slssteam_active(self) -> bool:
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

    def _get_config_dir(self) -> Optional[Path]:
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

    def _get_primary_steamapps(self, dest_path: str) -> Optional[Path]:
        """Resolve the steamapps directory for the destination library."""
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

    def _resolve_library_index(self, dest_path: str) -> int:
        """Resolve the Steam library folder index for dest_path."""
        try:
            from core.steam_helpers import get_library_index, find_steam_install
            steam_path = find_steam_install()
            idx = get_library_index(dest_path, steam_path)
            logger.info(f"[NativeSteamDL] Resolved library index {idx} for path: {dest_path}")
            return idx
        except Exception as e:
            logger.warning(f"[NativeSteamDL] Failed to resolve library index: {e}, using 0")
            return 0

    def _fetch_hubcap_keys(
        self, game_data: Dict[str, Any], appid: str
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Fetch depot decryption keys and manifest GIDs."""
        depot_keys: Dict[str, str] = {}
        manifest_gids: Dict[str, str] = {}

        for depot_id, depot_info in (game_data.get("depots") or {}).items():
            if isinstance(depot_info, dict):
                k = depot_info.get("key") or depot_info.get("decryption_key")
                if k:
                    depot_keys[str(depot_id)] = k
                m = depot_info.get("manifest_id") or depot_info.get("gid")
                if m:
                    manifest_gids[str(depot_id)] = str(m)

        if depot_keys:
            logger.info(f"[NativeSteamDL] Got {len(depot_keys)} keys from game_data cache")
            return depot_keys, manifest_gids

        try:
            from core import morrenus_api
            from core.tasks.process_zip_task import ProcessZipTask

            branch = morrenus_api.get_selected_branch(appid)
            zip_path, err = morrenus_api.download_manifest(appid, branch=branch)
            if not zip_path or not os.path.exists(zip_path):
                logger.error(f"[NativeSteamDL] Hubcap download failed: {err}")
                return depot_keys, manifest_gids

            with zipfile.ZipFile(zip_path, "r") as zf:
                lua_files = [f for f in zf.namelist() if f.endswith(".lua")]
                manifest_files = [f for f in zf.namelist() if f.endswith(".manifest")]

                if lua_files:
                    lua = zf.read(lua_files[0]).decode("utf-8", errors="ignore")
                    gd: Dict = {}
                    ProcessZipTask._parse_lua(lua, gd)
                    for d, info in gd.get("depots", {}).items():
                        if isinstance(info, dict) and info.get("key"):
                            depot_keys[str(d)] = info["key"]
                    for d, gid in gd.get("manifests", {}).items():
                        manifest_gids[str(d)] = str(gid)

                for mf in manifest_files:
                    base = os.path.basename(mf).replace(".manifest", "")
                    parts = base.split("_")
                    if len(parts) == 2:
                        manifest_gids.setdefault(parts[0], parts[1])

        except Exception as e:
            logger.exception(f"[NativeSteamDL] Hubcap fetch error: {e}")

        return depot_keys, manifest_gids

    def _find_bundled_plugins(self) -> Dict[str, Path]:
        """Look for bundled download.lua and spliced-tickets.lua files locally."""
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
        return found

    def _deploy_plugin(self, plugins_dir: Path) -> bool:
        """Deploy download.lua and spliced-tickets.lua to SLSsteam plugins directory."""
        bundled = self._find_bundled_plugins()
        deployed_any = False

        if "download.lua" in bundled and "spliced-tickets.lua" in bundled:
            logger.info("[NativeSteamDL] Deploying plugins from local bundled files")
            for name, src_path in bundled.items():
                dest = plugins_dir / name
                shutil.copy2(src_path, dest)
                self._deployed_plugins.append(dest)
                deployed_any = True
                logger.info(f"[NativeSteamDL] Deployed bundled {name} to {dest}")
            return deployed_any

        # Fall back to remote download
        logger.info("[NativeSteamDL] Bundled plugins missing, fetching from GitHub...")
        self.progress.emit("[Native Steam] Downloading download.lua from enter-the-wired...")
        try:
            req = urllib.request.Request(
                DOWNLOAD_LUA_URL, headers={"User-Agent": "ASSella-NativeSteam/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                zip_data = r.read()

            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                for name in ["download.lua", "spliced-tickets.lua"]:
                    if name in zf.namelist():
                        content = zf.read(name).decode("utf-8", errors="ignore")
                        dest = plugins_dir / name
                        dest.write_text(content, encoding="utf-8")
                        self._deployed_plugins.append(dest)
                        logger.info(f"[NativeSteamDL] Deployed remote {name} ({len(content)} chars)")
                        deployed_any = True

            return deployed_any
        except Exception as e:
            logger.error(f"[NativeSteamDL] Plugin deploy failed: {e}")
            return False

    def _patch_config(
        self,
        config_path: Path,
        appid: str,
        game_name: str,
        depot_keys: Dict[str, str],
    ) -> bool:
        """
        Patch SLSsteam config.yaml atomically with:
          - Plugins: yes
          - AdditionalApps: [appid]
          - AdditionalDepots: [depot_ids]
          - DecryptionKeys: {depot: key}
        """
        from utils.yaml_config_manager import (
            _atomic_write,
            _get_section_bounds,
            add_additional_app,
            update_yaml_boolean_value,
        )

        if not config_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text("Plugins: yes\nAdditionalApps:\n", encoding="utf-8")

        # Create backup if not already present
        backup = config_path.parent / "config.yaml.native_steam_backup"
        if not backup.exists():
            shutil.copy2(config_path, backup)
            self._config_backup_path = backup
            logger.info(f"[NativeSteamDL] Backed up config.yaml to {backup}")

        # 1. Enable Plugins: yes
        text = config_path.read_text(encoding="utf-8", errors="ignore")
        if not re.search(r"^[ \t]*Plugins[ \t]*:[ \t]*(?:yes|true)\b", text, re.MULTILINE | re.IGNORECASE):
            if re.search(r"^[ \t]*Plugins[ \t]*:", text, re.MULTILINE):
                update_yaml_boolean_value(config_path, "Plugins", True)
            else:
                text = "Plugins: yes\n" + text
                _atomic_write(config_path, text)

        # 2. Add AppID to AdditionalApps
        add_additional_app(config_path, appid, game_name)

        # 3. Format and inject AdditionalDepots and DecryptionKeys
        depot_ids = [str(d) for d in depot_keys.keys()]
        depot_lines = ["AdditionalDepots:"]
        for d in depot_ids:
            depot_lines.append(f"  - {d}")
        depot_block = "\n".join(depot_lines) + "\n"

        key_lines = ["DecryptionKeys:"]
        for d, k in depot_keys.items():
            key_lines.append(f"  {d}: {k}")
        key_block = "\n".join(key_lines) + "\n"

        content = config_path.read_text(encoding="utf-8", errors="ignore")

        bounds_depots = _get_section_bounds(content, "AdditionalDepots")
        if bounds_depots:
            content = content[: bounds_depots[0]] + depot_block + content[bounds_depots[2] :]
        else:
            content = content.rstrip() + "\n\n" + depot_block

        bounds_keys = _get_section_bounds(content, "DecryptionKeys")
        if bounds_keys:
            content = content[: bounds_keys[0]] + key_block + content[bounds_keys[2] :]
        else:
            content = content.rstrip() + "\n\n" + key_block

        if not _atomic_write(config_path, content):
            logger.error(f"[NativeSteamDL] Failed to atomic-write {config_path}")
            return False

        logger.info(f"[NativeSteamDL] config.yaml successfully patched ({len(content)} chars)")
        return True

    def _poll_license_unlocked(self, appid: str, start_offset: int, timeout_sec: float = 20.0) -> bool:
        """Poll SLSsteam log for AppLicensesChanged or Unlocked for this AppID."""
        sls_log = self._get_sls_log_path()
        if not sls_log.exists():
            return False

        pattern = re.compile(
            rf"(?:AppLicensesChanged.*?\b{re.escape(str(appid))}\b|Unlocked.*?\b{re.escape(str(appid))}\b)"
        )
        deadline = time.time() + timeout_sec
        curr_offset = start_offset

        while time.time() < deadline and self._is_running:
            try:
                sz = sls_log.stat().st_size
                if sz > curr_offset:
                    with open(sls_log, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(curr_offset)
                        for line in f:
                            if pattern.search(line):
                                logger.info(f"[NativeSteamDL] License event confirmed: {line.strip()}")
                                return True
                    curr_offset = sz
            except OSError:
                pass
            time.sleep(0.2)
        return False

    def _send_sls(self, command: str) -> bool:
        """Send a command to the SLSsteam API pipe."""
        pipe = Path(SLSSTEAM_API_PIPE)
        if not pipe.exists():
            logger.warning(f"[NativeSteamDL] SLS pipe not found: {SLSSTEAM_API_PIPE}")
            return False
        try:
            with open(pipe, "w") as f:
                f.write(command)
                f.flush()
            logger.info(f"[NativeSteamDL] SLS sent: {command!r}")
            return True
        except OSError as e:
            logger.warning(f"[NativeSteamDL] SLS send failed: {e}")
            return False

    def _monitor_download(
        self,
        acf_path: Path,
        appid: str,
        steamapps_dir: Path,
        game_name: str,
    ) -> bool:
        """
        Poll ACF StateFlags, content_log.txt, and SLS log to track download progress.
        Returns True if download completed successfully (StateFlags: 4).
        """
        content_log = self._get_content_log_path()
        content_log_offset = content_log.stat().st_size if content_log and content_log.exists() else 0
        sls_log = self._get_sls_log_path()
        sls_log_offset = sls_log.stat().st_size if sls_log.exists() else 0

        deadline = time.time() + MONITOR_TIMEOUT_SECONDS
        last_pct = -1

        err_pattern = re.compile(
            rf"(?:Failed installing AppID {re.escape(str(appid))}|AppID {re.escape(str(appid))} update canceled : (?:No subscription|Missing configuration))"
        )
        started_pattern = re.compile(
            rf"AppID {re.escape(str(appid))} update started : download"
        )

        while time.time() < deadline and self._is_running:
            time.sleep(POLL_INTERVAL_SECONDS)

            # 1. Check content_log.txt for fast error detection
            if content_log and content_log.exists():
                try:
                    c_size = content_log.stat().st_size
                    if c_size > content_log_offset:
                        with open(content_log, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(content_log_offset)
                            for line in f:
                                if err_pattern.search(line):
                                    msg = f"Steam error: {line.strip()}"
                                    logger.error(f"[NativeSteamDL] {msg}")
                                    self.progress.emit(f"[Native Steam] {msg}")
                                    return False
                                if started_pattern.search(line):
                                    self.progress.emit("[Native Steam] Steam download started...")
                        content_log_offset = c_size
                except OSError:
                    pass

            # 2. Check ACF file
            try:
                if acf_path.exists():
                    acf_text = acf_path.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r'"StateFlags"\s+"(\d+)"', acf_text)
                    if m:
                        state = int(m.group(1))
                        if state == ACF_STATE_INSTALLED:
                            self.progress.emit(
                                f"[Native Steam] StateFlags=4: {game_name} fully installed"
                            )
                            self.progress_percentage.emit(100)
                            return True

                        total_m = re.search(r'"BytesToDownload"\s+"(\d+)"', acf_text)
                        done_m = re.search(r'"BytesDownloaded"\s+"(\d+)"', acf_text)
                        if total_m and done_m:
                            total = int(total_m.group(1))
                            done = int(done_m.group(1))
                            self.total_download_size_for_this_job = total
                            self.completed_so_far_for_this_job = done
                            if total > 0:
                                pct = min(99, int(done * 100 / total))
                                if pct != last_pct:
                                    last_pct = pct
                                    self.progress_percentage.emit(pct)
                                    done_mb = done / (1024 * 1024)
                                    total_mb = total / (1024 * 1024)
                                    self.progress.emit(
                                        f"[Native Steam] Downloading... {done_mb:.1f} / {total_mb:.1f} MB ({pct}%)"
                                    )
                                    self.speed_update.emit(
                                        f"{done_mb:.1f}/{total_mb:.1f} MB"
                                    )
            except OSError:
                pass

            # 3. Check SLS log for debug lines
            if sls_log.exists():
                try:
                    s_size = sls_log.stat().st_size
                    if s_size > sls_log_offset:
                        with open(sls_log, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(sls_log_offset)
                            for line in f:
                                if "AuthenticateDepotID" in line or "download.lua" in line:
                                    self.progress.emit(f"[SLS] {line.strip()}")
                        sls_log_offset = s_size
                except OSError:
                    pass

        if not self._is_running:
            return False

        if acf_path.exists():
            try:
                acf_text = acf_path.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r'"StateFlags"\s+"(\d+)"', acf_text)
                if m and int(m.group(1)) == ACF_STATE_INSTALLED:
                    self.progress_percentage.emit(100)
                    return True
            except OSError:
                pass

        return False

    def _get_sls_log_path(self) -> Path:
        """Return the SLSsteam log path."""
        try:
            from core.steam_helpers import get_steam_env
            return get_steam_env().sls_log_path
        except Exception:
            pass
        flatpak_log = Path.home() / ".var/app/com.valvesoftware.Steam/.SLSsteam.log"
        native_log = Path.home() / ".SLSsteam.log"
        return flatpak_log if flatpak_log.exists() else native_log

    def _get_content_log_path(self) -> Optional[Path]:
        """Return Steam content_log.txt path."""
        try:
            from core.steam_helpers import get_steam_env
            env = get_steam_env()
            if env.steam_path:
                p = Path(env.steam_path) / "logs" / "content_log.txt"
                if p.exists():
                    return p
        except Exception:
            pass

        for candidate in [
            Path.home() / ".local/share/Steam/logs/content_log.txt",
            Path.home() / ".steam/steam/logs/content_log.txt",
            Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam/logs/content_log.txt",
        ]:
            if candidate.exists():
                return candidate
        return None

    def _cleanup_success(self, config_path: Path, plugins_dir: Path):
        """
        Clean up after a successful download:
        - Keep AppID in AdditionalApps (so the game stays unlocked).
        - Remove AdditionalDepots and DecryptionKeys sections.
        - Delete deployed Lua plugins.
        - Delete backup config file.
        """
        logger.info("[NativeSteamDL] Performing success cleanup...")
        from utils.yaml_config_manager import _atomic_write, _get_section_bounds

        try:
            if config_path.exists():
                content = config_path.read_text(encoding="utf-8", errors="ignore")
                changed = False
                bounds_keys = _get_section_bounds(content, "DecryptionKeys")
                if bounds_keys:
                    content = content[: bounds_keys[0]] + content[bounds_keys[2] :]
                    changed = True
                bounds_depots = _get_section_bounds(content, "AdditionalDepots")
                if bounds_depots:
                    content = content[: bounds_depots[0]] + content[bounds_depots[2] :]
                    changed = True
                if changed:
                    _atomic_write(config_path, content)
                    logger.info("[NativeSteamDL] Removed AdditionalDepots and DecryptionKeys from config.yaml")
        except Exception as e:
            logger.warning(f"[NativeSteamDL] Error removing keys from config: {e}")

        # Remove deployed plugins
        for p in self._deployed_plugins:
            if p.exists():
                try:
                    p.unlink()
                    logger.info(f"[NativeSteamDL] Removed plugin: {p.name}")
                except OSError:
                    pass
        self._deployed_plugins.clear()

        # Delete backup
        if self._config_backup_path and self._config_backup_path.exists():
            try:
                self._config_backup_path.unlink()
            except OSError:
                pass
            self._config_backup_path = None
        logger.info("[NativeSteamDL] Success cleanup done")

    def _cleanup_failure(self):
        """
        Clean up after failure or cancellation:
        - Restore config.yaml from backup.
        - Delete deployed plugins.
        - Delete incomplete ACF if present.
        - Send uninstall|<appid> to SLSsteam API to reset Steam's download state.
        """
        logger.info("[NativeSteamDL] Performing failure/cancel rollback...")
        if self._sls_config_dir:
            config_path = self._sls_config_dir / "config.yaml"
            if self._config_backup_path and self._config_backup_path.exists():
                try:
                    shutil.copy2(self._config_backup_path, config_path)
                    self._config_backup_path.unlink()
                    logger.info("[NativeSteamDL] Restored config.yaml from backup")
                except OSError as e:
                    logger.warning(f"[NativeSteamDL] Failed restoring backup config: {e}")
                self._config_backup_path = None
            elif self._appid:
                try:
                    from utils.yaml_config_manager import remove_additional_app
                    remove_additional_app(config_path, self._appid)
                except Exception:
                    pass

        # Remove deployed plugins
        for p in self._deployed_plugins:
            if p.exists():
                try:
                    p.unlink()
                    logger.info(f"[NativeSteamDL] Removed plugin: {p.name}")
                except OSError:
                    pass
        self._deployed_plugins.clear()

        # Remove incomplete ACF
        if self._acf_path and self._acf_path.exists():
            try:
                txt = self._acf_path.read_text(encoding="utf-8", errors="ignore")
                if '"StateFlags"\t\t"4"' not in txt and '"StateFlags"\t"4"' not in txt:
                    self._acf_path.unlink()
                    logger.info(f"[NativeSteamDL] Removed incomplete ACF: {self._acf_path}")
            except OSError:
                pass

        # Send uninstall to SLSsteam API to clean Steam memory state
        if self._appid:
            self._send_sls(f"uninstall|{self._appid}")

        logger.info("[NativeSteamDL] Rollback complete")
