import os
from pathlib import Path
import re
import subprocess
import time
import logging
import requests
from typing import List, Dict, Any, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from utils.settings import get_settings
from utils.paths import Paths
from utils.helpers import get_base_path, get_dotnet_path
from core import steam_helpers
from core.morrenus_api import BASE_URL

logger = logging.getLogger(__name__)

class DownloadWorkshopTask(QObject):
    progress = pyqtSignal(str)
    progress_percentage = pyqtSignal(int)
    completed = pyqtSignal()
    error = pyqtSignal(tuple)

    def __init__(self):
        super().__init__()
        self._is_running = True
        self.process: Optional[subprocess.Popen] = None
        self._kuroko_manager = None
        self.ddm_exe = str(Paths.depot_downloader_dll())
        self.manifests_dir = os.path.join(get_base_path(), "manifests")
        self.keys_file = str(Paths.workshop_keys())
        os.makedirs(self.manifests_dir, exist_ok=True)

    @property
    def is_running_flag(self) -> bool:
        return self._is_running

    @property
    def _wireproxy_manager(self):
        """Backward-compatibility alias for _kuroko_manager."""
        return self._kuroko_manager

    @_wireproxy_manager.setter
    def _wireproxy_manager(self, value):
        self._kuroko_manager = value

    def stop(self):
        logger.info("Stopping Workshop Download Task...")
        self._is_running = False
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception as e:
                logger.error(f"Failed to terminate Workshop DepotDownloader: {e}")
        manager, self._kuroko_manager = self._kuroko_manager, None
        if manager is not None:
            try:
                manager.stop()
            except Exception as e:
                logger.debug(f"[WorkshopDownload] Error stopping stealth proxy manager on cancel: {e}")

    def log(self, msg: str):
        self.progress.emit(msg)

    def fetch_manifest(self, wid: str, api_key: str):
        try:
            from core import at0m
            result, err = at0m.fetch_workshop_manifest(wid, api_key=api_key, dest_dir=self.manifests_dir)
            if result and not err:
                return result
            if err:
                self.log(f"  [Error] Manifest request failed: {err}")
            return None
        except Exception as e:
            self.log(f"  [Error] Manifest request error: {e}")
            return None

    def key_exists(self, appid: str):
        if not os.path.exists(self.keys_file): return False
        with open(self.keys_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(f"{appid};"): return True
        return False

    def save_key(self, appid: str, key: str):
        os.makedirs(os.path.dirname(self.keys_file), exist_ok=True)
        with open(self.keys_file, "a", encoding="utf-8") as f:
            f.write(f"{appid};{key}\n")

    def _get_dir_size(self, path: str) -> int:
        total = 0
        for dirpath, _, filenames in os.walk(path):
            for fn in filenames:
                try: total += os.path.getsize(os.path.join(dirpath, fn))
                except OSError: pass
        return total

    def _parse_acf_block(self, text: str) -> dict:
        result = {}
        stack = [result]
        current_key = None
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("//"): continue
            tokens = re.findall(r'"[^"]*"|\{|\}', s)
            for tok in tokens:
                if tok == "{":
                    new = {}
                    if current_key is not None:
                        stack[-1][current_key] = new
                        stack.append(new)
                        current_key = None
                elif tok == "}":
                    if len(stack) > 1: stack.pop()
                else:
                    val = tok.strip('"')
                    if current_key is None: current_key = val
                    else:
                        stack[-1][current_key] = val
                        current_key = None
        return result

    def _get_steam_account_id(self) -> str:
        loginusers_candidates = [
            os.path.expanduser("~/.local/share/Steam/config/loginusers.vdf"),
            os.path.expanduser("~/.steam/steam/config/loginusers.vdf"),
            os.path.expanduser("~/.steam/root/config/loginusers.vdf"),
        ]
        for p in loginusers_candidates:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    match = re.search(r'"(7656119[0-9]+)"', content)
                    if match:
                        steamid64 = int(match.group(1))
                        return str(steamid64 - 76561197960265728)
                except Exception:
                    pass
        return "0"

    def _write_acf(self, path: str, appid: str, items: dict):
        existing = {}
        root_meta = {}
        now = str(int(time.time()))
        account_id = self._get_steam_account_id()

        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
            parsed = self._parse_acf_block(raw)
            aw = parsed.get("AppWorkshop", {})
            root_meta = {
                k: v for k, v in aw.items()
                if k not in ("WorkshopItemsInstalled", "WorkshopItemDetails", "NeedsUpdate", "NeedsDownload", "SizeOnDisk")
                and isinstance(v, str)
            }
            installed = aw.get("WorkshopItemsInstalled", {})
            details = aw.get("WorkshopItemDetails", {})

            for wid, d in details.items():
                existing[wid] = {
                    "size": str(installed.get(wid, {}).get("size", "0")),
                    "timeupdated": str(d.get("timeupdated", now)),
                    "manifest": str(d.get("manifest", installed.get(wid, {}).get("manifest", ""))),
                    "timetouched": str(d.get("timetouched", now)) if str(d.get("timetouched", "0")) != "0" else now,
                    "subscribedby": str(d.get("subscribedby") or account_id),
                }

            for wid, d in installed.items():
                if wid not in existing:
                    existing[wid] = {
                        "size": str(d.get("size", "0")),
                        "timeupdated": str(d.get("timeupdated", now)),
                        "manifest": str(d.get("manifest", "")),
                        "timetouched": now,
                        "subscribedby": account_id,
                    }
                else:
                    if d.get("size") and str(d.get("size")) != "0":
                        existing[wid]["size"] = str(d["size"])
                    if d.get("manifest"):
                        existing[wid]["manifest"] = str(d["manifest"])

        # Check existing content directories on disk if sizes are 0
        content_parent = os.path.join(os.path.dirname(path), "content", appid)
        if os.path.exists(content_parent):
            for wid in existing:
                if existing[wid]["size"] in ("0", ""):
                    wid_dir = os.path.join(content_parent, wid)
                    if os.path.exists(wid_dir):
                        existing[wid]["size"] = str(self._get_dir_size(wid_dir))

        for wid, info in items.items():
            prev = existing.get(wid, {})
            existing[wid] = {
                "size": str(info.get("size", prev.get("size", "0"))),
                "timeupdated": str(info.get("timeupdated", prev.get("timeupdated", now))),
                "manifest": str(info.get("manifest", prev.get("manifest", ""))),
                "timetouched": now,
                "subscribedby": str(prev.get("subscribedby") or account_id),
            }

        total_size = sum(int(d["size"]) for d in existing.values() if str(d.get("size", "")).isdigit())

        def q(v): return f'"{v}"'
        lines = ['"AppWorkshop"', '{', f'\t"appid"\t\t{q(appid)}']
        lines.append(f'\t"SizeOnDisk"\t\t{q(str(total_size))}')
        lines.append('\t"NeedsUpdate"\t\t"0"')
        lines.append('\t"NeedsDownload"\t\t"0"')
        lines.append(f'\t"TimeLastUpdated"\t\t{q(now)}')

        for k, v in root_meta.items():
            if k not in ("appid", "SizeOnDisk", "NeedsUpdate", "NeedsDownload", "TimeLastUpdated"):
                lines.append(f'\t{q(k)}\t\t{q(v)}')

        lines.extend(['\t"WorkshopItemsInstalled"', '\t{'])
        for wid, d in existing.items():
            lines.extend([
                f'\t\t{q(wid)}',
                '\t\t{',
                f'\t\t\t"size"\t\t{q(d["size"])}',
                f'\t\t\t"timeupdated"\t\t{q(d["timeupdated"])}',
                f'\t\t\t"manifest"\t\t{q(d["manifest"])}',
                '\t\t}'
            ])
        lines.extend(['\t}', '\t"WorkshopItemDetails"', '\t{'])
        for wid, d in existing.items():
            lines.extend([
                f'\t\t{q(wid)}',
                '\t\t{',
                f'\t\t\t"manifest"\t\t{q(d["manifest"])}',
                f'\t\t\t"timeupdated"\t\t{q(d["timeupdated"])}',
                f'\t\t\t"timetouched"\t\t{q(d["timetouched"])}',
                f'\t\t\t"subscribedby"\t\t{q(d["subscribedby"])}',
                f'\t\t\t"latest_timeupdated"\t\t{q(d["timeupdated"])}',
                f'\t\t\t"latest_manifest"\t\t{q(d["manifest"])}',
                '\t\t}'
            ])
        lines.extend(['\t}', '}'])
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def apply_steam_integration(self, appid: str, wid: str, manifest_id: str, mod_dir: str, dest_path: str):
        acf_path = os.path.join(dest_path, "steamapps", "workshop", f"appworkshop_{appid}.acf")
        size = self._get_dir_size(mod_dir)
        now = int(time.time())
        try:
            self._write_acf(acf_path, appid, {wid: {"size": size, "timeupdated": now, "manifest": manifest_id}})
            self.log(f"  [OK] ACF updated -> {acf_path}")
        except Exception as e:
            self.log(f"  [Error] Failed to update ACF: {e}")



    def run(self, workshop_data: Dict[str, Any]):
        wids = workshop_data["wids"]
        api_key = workshop_data["api_key"]
        max_downloads = workshop_data["max_downloads"]
        cellid = workshop_data.get("cellid")
        steam_integration = workshop_data["steam_integration"]
        dest_path = workshop_data["dest_path"]

        total_items = len(wids)
        percentage_regex = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)%")

        # Auto-tune: slider value is the cap when on, exact value when off.
        from utils import auto_tune as _auto_tune
        _settings = get_settings()
        _cap = _auto_tune.clamp(int(max_downloads) if str(max_downloads).isdigit() else 8, 1, 30)
        _auto_on = _auto_tune.is_auto_enabled(_settings, "workshop_auto_max_downloads")
        _best_key = "workshop_auto_tune_best"
        if _auto_on:
            current_n = _auto_tune.get_start_concurrency(
                _settings,
                cap_key="workshop_max_downloads",
                auto_key="workshop_auto_max_downloads",
                best_key=_best_key,
                default_start=_cap,
                default_cap=_cap,
                dest_path=dest_path,
            )
            # Keep the queued cap if the stored best predates it.
            current_n = min(_cap, max(current_n, 1))
            self.log(f"  -> Auto-tune ON: starting at {current_n} concurrent chunks (cap {_cap}).")
        else:
            current_n = _cap

        dotnet_exe = get_dotnet_path()
        if not dotnet_exe:
            self.log("  [Error] dotnet runtime not found. Please install .NET 9 runtime.")
            self.error.emit((RuntimeError, ".NET 9 runtime not found", None))
            return

        proxy_url = None
        self._proxy_info_tag = None
        try:
            from utils.kuroko import resolve_stealth_proxy_url
            proxy_url, self._kuroko_manager = resolve_stealth_proxy_url(_settings)
            if self._kuroko_manager:
                endpoint_id = getattr(self._kuroko_manager, "endpoint_id", None)
                if endpoint_id:
                    if endpoint_id == "warp":
                        mode_display = "WARP"
                    elif endpoint_id.startswith("wg:"):
                        prof_name = Path(endpoint_id[3:]).stem
                        mode_display = f"WireGuard ({prof_name})"
                    elif endpoint_id.startswith("custom"):
                        mode_display = "Custom"
                    else:
                        mode_display = endpoint_id.capitalize()
                else:
                    mode = _settings.value("stealth_proxy_mode", "warp", type=str).strip().lower()
                    mode_display = "WARP" if mode == "warp" else ("WireGuard" if mode == "wireguard" else mode.capitalize())
                endpoint = self._kuroko_manager.proxy_url.split("://")[-1]
                self._proxy_info_tag = f"[via Stealth: {mode_display} ({endpoint})]"
                self.log(f"  Stealth proxy active: {mode_display} ({self._kuroko_manager.proxy_url})")
            elif proxy_url:
                endpoint = proxy_url.split("://")[-1]
                self._proxy_info_tag = f"[via Stealth: Proxy ({endpoint})]"
                self.log(f"  Routing download through custom proxy: {proxy_url}")
        except Exception as e:
            self.log(f"  [Error] Stealth proxy setup failed: {e}")
            if not self._is_running:
                return
            from utils.kuroko import prompt_stealth_fallback
            if prompt_stealth_fallback(str(e)):
                self.log("  [Warning] Stealth proxy failed. Proceeding with direct download per user request.")
                proxy_url = None
                self._kuroko_manager = None
            else:
                self._is_running = False
                self.log("  Download cancelled due to stealth connection failure.")
                self.error.emit((type(e), f"Stealth proxy error: {e}", None))
                return

        from utils.helpers import get_dotnet_env
        dotnet_env = get_dotnet_env()

        try:
            for idx, wid in enumerate(wids):
                if not self._is_running:
                    break

                self.log(f"\n{'─' * 52}\n  Workshop ID : {wid}\n  -> Fetching manifest...")
                info = self.fetch_manifest(wid, api_key)
                if not info:
                    self.log("  [Error] Could not fetch manifest. Skipping.")
                    continue

                appid = info["appid"]
                manifest_id = info["manifest_id"]
                depot_key = info["depot_key"]
                manifest_path = info["manifest_path"]

                self.log(f"  [OK] App ID     : {appid}\n  [OK] Manifest ID: {manifest_id}\n  [OK] Manifest   : {manifest_path}")

                if not depot_key:
                    from core.at0m import _lookup_cached_depot_key
                    depot_key = _lookup_cached_depot_key(appid)

                if not depot_key:
                    if self.key_exists(appid):
                        self.log(f"  [OK] Depot key for App ID {appid} already cached in keys file.")
                    else:
                        self.log("  [Error] No depot key found for App ID. Skipping.")
                        continue
                else:
                    if not self.key_exists(appid):
                        self.save_key(appid, depot_key)
                        self.log(f"  [OK] Key saved  : {appid};{depot_key[:10]}...")
                    else:
                        self.log(f"  [OK] Depot key for App ID {appid} already cached.")

                if steam_integration and dest_path:
                    out_dir = os.path.join(dest_path, "steamapps", "workshop", "content", appid, wid)
                else:
                    out_dir = os.path.join(dest_path, "mods", appid, wid)

                _size_before = self._get_dir_size(out_dir) if os.path.isdir(out_dir) else 0
                _t0 = time.time()
                _errors = 0

                cmd = [
                    dotnet_exe, self.ddm_exe,
                    "-app", appid,
                    "-ugc", wid,
                    "-manifestfile", manifest_path,
                    "-depotkeys", self.keys_file,
                    "-dir", out_dir,
                    "-max-downloads", str(current_n),
                ]
                if proxy_url:
                    cmd.extend(["-proxy", str(proxy_url)])
                if cellid:
                    cmd += ["-cellid", str(cellid)]

                self.log(f"  → Running    : {' '.join(cmd)}")

                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=dotnet_env,
                    )
                    self.process = proc
                    for line in proc.stdout:
                        if not self._is_running:
                            proc.terminate()
                            break
                        line_str = line.rstrip()
                        if line_str.strip().startswith("Downloading depot ") and getattr(self, "_proxy_info_tag", None):
                            line_str = f"{line_str.strip()} {self._proxy_info_tag}"
                        self.log(f"    {line_str}")

                        # Calculate dynamic multi-item progress
                        match = percentage_regex.search(line_str)
                        if match:
                            try:
                                item_percent = float(match.group(1))
                                total_percent = int(((idx + (item_percent / 100.0)) / total_items) * 100)
                                total_percent = max(0, min(100, total_percent))
                                self.progress_percentage.emit(total_percent)
                            except ValueError:
                                pass

                    proc.wait()
                    self.process = None

                    if proc.returncode == 0:
                        self.log(f"  [OK] Download complete -> {out_dir}")
                    else:
                        self.log(f"  [Error] DepotDownloader exited with code {proc.returncode}.")
                        _errors = 1
                        if _auto_on:
                            try:
                                current_n = _auto_tune.record_result(
                                    get_settings(), _best_key, current_n, 0.0, _cap, _errors, dest_path=dest_path
                                )
                                self.log(f"  -> Auto-tune: trouble seen, next: {current_n} (cap {_cap}).")
                            except Exception as _at_err:
                                logger.debug(f"Auto-tune step failed: {_at_err}")
                        continue
                except Exception as e:
                    self.log(f"  [Error] Failed to launch DepotDownloader: {e}")
                    if _auto_on:
                        try:
                            current_n = _auto_tune.record_result(
                                get_settings(), _best_key, current_n, 0.0, _cap, 1, dest_path=dest_path
                            )
                        except Exception as _at_err:
                            logger.debug(f"Auto-tune step failed: {_at_err}")
                    continue

            if _auto_on:
                try:
                    _elapsed = max(0.001, time.time() - _t0)
                    _delta = max(0, self._get_dir_size(out_dir) - _size_before)
                    if not _auto_tune.should_learn(_delta):
                        logger.debug(
                            f"Auto-tune: workshop {wid} moved {_delta} bytes — "
                            f"validation-only, holding at {current_n}"
                        )
                        self.log(f"  → Auto-tune: validation-only, holding at {current_n} (cap {_cap}).")
                    else:
                        _mbps = _delta / _elapsed
                        current_n = _auto_tune.record_result(
                            get_settings(), _best_key, current_n, float(_mbps), _cap, _errors, dest_path=dest_path
                        )
                        self.log(
                            f"  → Auto-tune: {_auto_tune.format_speed(_mbps)} "
                            f"→ next: {current_n} (cap {_cap})."
                        )
                except Exception as _at_err:
                    logger.debug(f"Auto-tune step failed: {_at_err}")

            if steam_integration and dest_path:
                self.log("  → Applying Steam integration...")
                self.apply_steam_integration(appid, wid, manifest_id, out_dir, dest_path)
        finally:
            manager, self._kuroko_manager = self._kuroko_manager, None
            if manager is not None:
                try:
                    manager.stop()
                except Exception as e:
                    logger.debug(f"[WorkshopDownload] Error stopping stealth proxy manager: {e}")

        if not self._is_running:
            self.log("\n  Download task cancelled by user.")
            self.completed.emit()
            return

        self.log(f"\n{'─' * 52}\n  All tasks finished.")
        self.progress_percentage.emit(100)
        self.completed.emit()
