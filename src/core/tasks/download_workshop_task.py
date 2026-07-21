import os
import re
import subprocess
import time
import logging
import requests
from typing import List, Dict, Any

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
    error = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._is_running = True
        self.process: Optional[subprocess.Popen] = None
        self.ddm_exe = str(Paths.deps("DepotDownloader.dll"))
        self.manifests_dir = os.path.join(get_base_path(), "manifests")
        self.keys_file = os.path.join(get_base_path(), "workshop_keys.txt")
        os.makedirs(self.manifests_dir, exist_ok=True)

    @property
    def is_running_flag(self) -> bool:
        return self._is_running

    def stop(self):
        logger.info("Stopping Workshop Download Task...")
        self._is_running = False
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception as e:
                logger.error(f"Failed to terminate Workshop DepotDownloader: {e}")

    def log(self, msg: str):
        self.progress.emit(msg)

    def fetch_manifest(self, wid: str, api_key: str):
        try:
            url = f"{BASE_URL}/generate/workshopmanifest/{wid}"
            r = requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
            r.raise_for_status()
        except Exception as e:
            self.log(f"  ✗ Manifest request failed: {e}")
            return None
        appid = r.headers.get("X-App-Id")
        manifest_id = r.headers.get("X-Manifest-Id")
        depot_key = r.headers.get("X-Depot-Key")
        if not appid or not manifest_id:
            self.log("  ✗ Missing required headers in manifest response.")
            return None
        manifest_path = os.path.join(self.manifests_dir, f"{appid}_{manifest_id}.manifest")
        with open(manifest_path, "wb") as f:
            f.write(r.content)
        return {"appid": appid, "manifest_id": manifest_id, "depot_key": depot_key, "manifest_path": manifest_path}

    def key_exists(self, appid: str):
        if not os.path.exists(self.keys_file): return False
        with open(self.keys_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(f"{appid};"): return True
        return False

    def save_key(self, appid: str, key: str):
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

    def _write_acf(self, path: str, appid: str, items: dict):
        existing = {}
        root_meta = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
            parsed = self._parse_acf_block(raw)
            aw = parsed.get("AppWorkshop", {})
            root_meta = {k: v for k, v in aw.items() if k not in ("WorkshopItemsInstalled", "WorkshopItemDetails", "NeedsUpdate", "NeedsDownload") and isinstance(v, str)}
            installed = aw.get("WorkshopItemsInstalled", {})
            details = aw.get("WorkshopItemDetails", {})
            for wid, data in installed.items():
                existing[wid] = {"size": data.get("size", "0"), "timeupdated": data.get("timeupdated", "0"), "manifest": data.get("manifest", ""), "timetouched": details.get(wid, {}).get("timetouched", "0"), "subscribedby": details.get(wid, {}).get("subscribedby", "0")}
        for wid, info in items.items():
            existing[wid] = {"size": str(info["size"]), "timeupdated": str(info["timeupdated"]), "manifest": str(info["manifest"]), "timetouched": existing.get(wid, {}).get("timetouched", "0"), "subscribedby": existing.get(wid, {}).get("subscribedby", "0")}
        
        def q(v): return f'"{v}"'
        lines = ['"AppWorkshop"', '{', f'\t"appid"\t\t{q(appid)}']
        for k, v in root_meta.items():
            if k != "appid": lines.append(f'\t{q(k)}\t\t{q(v)}')
        lines.extend(['\t"NeedsUpdate"\t\t"0"', '\t"NeedsDownload"\t\t"0"', '\t"WorkshopItemsInstalled"', '\t{'])
        for wid, d in existing.items():
            lines.extend([f'\t\t{q(wid)}', '\t\t{', f'\t\t\t"size"\t\t{q(d["size"])}', f'\t\t\t"timeupdated"\t\t{q(d["timeupdated"])}', f'\t\t\t"manifest"\t\t{q(d["manifest"])}', '\t\t}'])
        lines.extend(['\t}', '\t"WorkshopItemDetails"', '\t{'])
        for wid, d in existing.items():
            lines.extend([f'\t\t{q(wid)}', '\t\t{', f'\t\t\t"manifest"\t\t{q(d["manifest"])}', f'\t\t\t"timeupdated"\t\t{q(d["timeupdated"])}', f'\t\t\t"timetouched"\t\t{q(d["timetouched"])}', f'\t\t\t"subscribedby"\t\t{q(d["subscribedby"])}', f'\t\t\t"latest_timeupdated"\t\t{q(d["timeupdated"])}', f'\t\t\t"latest_manifest"\t\t{q(d["manifest"])}', '\t\t}'])
        lines.extend(['\t}', '}'])
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def apply_steam_integration(self, appid: str, wid: str, manifest_id: str, mod_dir: str, dest_path: str):
        acf_path = os.path.join(dest_path, "steamapps", "workshop", f"appworkshop_{appid}.acf")
        size = self._get_dir_size(mod_dir)
        now = int(time.time())
        try:
            self._write_acf(acf_path, appid, {wid: {"size": size, "timeupdated": now, "manifest": manifest_id}})
            self.log(f"  ✓ ACF updated → {acf_path}")
        except Exception as e:
            self.log(f"  ✗ Failed to update ACF: {e}")

    def run(self, workshop_data: Dict[str, Any]):
        wids = workshop_data["wids"]
        api_key = workshop_data["api_key"]
        max_downloads = workshop_data["max_downloads"]
        cellid = workshop_data.get("cellid")
        steam_integration = workshop_data["steam_integration"]
        dest_path = workshop_data["dest_path"]

        total_items = len(wids)
        percentage_regex = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)%")

        dotnet_exe = get_dotnet_path()
        if not dotnet_exe:
            self.log("  ✗ dotnet runtime not found. Please install .NET 9 runtime.")
            self.error.emit()
            return

        for idx, wid in enumerate(wids):
            if not self._is_running:
                break

            self.log(f"\n{'─' * 52}\n  Workshop ID : {wid}\n  → Fetching manifest...")
            info = self.fetch_manifest(wid, api_key)
            if not info:
                self.log("  ✗ Could not fetch manifest. Skipping.")
                continue

            appid = info["appid"]
            manifest_id = info["manifest_id"]
            depot_key = info["depot_key"]
            manifest_path = info["manifest_path"]

            self.log(f"  ✓ App ID     : {appid}\n  ✓ Manifest ID: {manifest_id}\n  ✓ Manifest   : {manifest_path}")

            if not depot_key:
                self.log("  ✗ No depot key in response. Skipping.")
                continue

            if not self.key_exists(appid):
                self.save_key(appid, depot_key)
                self.log(f"  ✓ Key saved  : {appid};{depot_key[:10]}…")
            else:
                self.log(f"  ✓ Depot key for App ID {appid} already cached.")

            if steam_integration and dest_path:
                out_dir = os.path.join(dest_path, "steamapps", "workshop", "content", appid, wid)
            else:
                out_dir = os.path.join(dest_path, "mods", appid, wid)

            cmd = [
                dotnet_exe, self.ddm_exe,
                "-app", appid,
                "-ugc", wid,
                "-manifestfile", manifest_path,
                "-depotkeys", self.keys_file,
                "-dir", out_dir,
                "-max-downloads", str(max_downloads),
            ]
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
                    errors="replace"
                )
                self.process = proc
                for line in proc.stdout:
                    if not self._is_running:
                        proc.terminate()
                        break
                    line_str = line.rstrip()
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
                    self.log(f"  ✓ Download complete → {out_dir}")
                else:
                    self.log(f"  ✗ DepotDownloader exited with code {proc.returncode}.")
                    continue
            except Exception as e:
                self.log(f"  ✗ Failed to launch DepotDownloader: {e}")
                continue

            if steam_integration and dest_path:
                self.log("  → Applying Steam integration...")
                self.apply_steam_integration(appid, wid, manifest_id, out_dir, dest_path)

        if not self._is_running:
            self.log("\n  ✗ Download task cancelled by user.")
            self.error.emit()
            return

        self.log(f"\n{'─' * 52}\n  All tasks finished.")
        self.progress_percentage.emit(100)
        self.completed.emit()
