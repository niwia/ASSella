"""
SteamDB Scraper & Rollback Metadata Retriever for ACCELA.

Features:
- Byparr integration for automated Cloudflare Turnstile bypass (using stealth Camoufox).
- Fast headless DOM resolution for dynamic SteamDB patchnotes tables.
- Robust HTML parsers for:
    * App patchnotes overview (/app/<appid>/patchnotes/) -> Build IDs, dates, titles
    * Patch detail pages (/patchnotes/<buildid>/) -> Depot IDs, Manifest IDs
"""

import atexit
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import requests
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

from utils.settings import get_settings
from utils.helpers import get_base_path

logger = logging.getLogger(__name__)

BYPARR_DEFAULT_PORT = 8191
BYPARR_DEFAULT_URL = f"http://127.0.0.1:{BYPARR_DEFAULT_PORT}"
STEAMDB_BASE_URL = "https://steamdb.info"


class ByparrManager:
    """Manages the background Byparr helper process for Cloudflare clearance.

    Strictly bound to ACCELA's lifecycle: starts when ACCELA needs it,
    and terminates immediately (with all child browsers) when ACCELA closes or crashes.
    """

    _process: Optional[subprocess.Popen] = None
    _managed: bool = False

    @classmethod
    def get_service_url(cls) -> str:
        settings = get_settings()
        return settings.value("byparr_url", BYPARR_DEFAULT_URL, type=str).rstrip("/")

    @classmethod
    def is_running(cls) -> bool:
        """Quickly checks if Byparr is listening on the configured port."""
        try:
            port = BYPARR_DEFAULT_PORT
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            res = s.connect_ex(("127.0.0.1", port))
            s.close()
            return res == 0
        except Exception:
            return False

    @classmethod
    def find_uv_path(cls) -> Optional[str]:
        """Locates the uv executable in PATH or standard user directories."""
        uv = shutil.which("uv")
        if uv:
            return uv
        candidates = [
            Path.home() / ".local" / "bin" / "uv",
            Path.home() / ".cargo" / "bin" / "uv",
        ]
        for c in candidates:
            if c.exists() and os.access(c, os.X_OK):
                return str(c)
        return None

    @classmethod
    def find_byparr_dir(cls) -> Optional[Path]:
        """Locates the Byparr installation directory."""
        candidates = [
            Path.home() / ".local" / "share" / "ACCELA" / "byparr",
            get_base_path() / "byparr",
            Path("/tmp/byparr_test"),
            Path.home() / "Byparr",
        ]
        for c in candidates:
            if c.exists() and (c / "main.py").exists():
                return c
        return None

    @classmethod
    def _rotate_byparr_log(cls, log_path: Path, max_sessions: int = 5) -> None:
        """Limits byparr.log to the last `max_sessions` runs, pruning older sessions."""
        try:
            if not log_path.exists() or log_path.stat().st_size == 0:
                return

            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            pattern = r"(?=(?:\r?\n|^)---\s*Byparr Session Started by\b)"
            sessions = [s for s in re.split(pattern, content) if s.strip()]

            # Keep at most (max_sessions - 1) previous sessions so the new one brings total to max_sessions
            keep_count = max(1, max_sessions - 1)
            if len(sessions) >= max_sessions:
                trimmed_content = "\n".join(s.strip() for s in sessions[-keep_count:]) + "\n"
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(trimmed_content)
                logger.debug(f"[SteamDB] Pruned byparr.log from {len(sessions)} to {keep_count} sessions.")
        except Exception as e:
            logger.debug(f"[SteamDB] Error rotating byparr.log: {e}")

    @classmethod
    def ensure_running(cls) -> bool:
        """Ensures Byparr is listening; launches background process if local repo exists."""
        if cls.is_running():
            return True

        byparr_dir = cls.find_byparr_dir()
        if not byparr_dir:
            logger.warning("[SteamDB] Byparr service not running and local repository not found.")
            return False

        try:
            env = os.environ.copy()
            # Clean AppImage library overrides so uv and Python 3.14 run clean
            env.pop("LD_LIBRARY_PATH", None)
            env.pop("LD_PRELOAD", None)

            # Ensure PATH contains standard user bin dirs
            local_bin = str(Path.home() / ".local" / "bin")
            cargo_bin = str(Path.home() / ".cargo" / "bin")
            env["PATH"] = f"{local_bin}:{cargo_bin}:{env.get('PATH', '')}"

            env["INVPW_TRUE_HEADLESS"] = "1"
            env["PORT"] = str(BYPARR_DEFAULT_PORT)
            env["HOST"] = "127.0.0.1"
            # Pass ASSELLA PID so Byparr supervisor can self-terminate if ASSELLA crashes
            env["ASSELLA_PID"] = str(os.getpid())
            env["ACCELA_PID"] = str(os.getpid())

            uv_path = cls.find_uv_path()
            runner_script = byparr_dir / "assella_runner.py"
            if not runner_script.exists():
                runner_script = byparr_dir / "accela_runner.py"
            target_script = runner_script.name if runner_script.exists() else "main.py"

            if uv_path and (byparr_dir / "uv.lock").exists():
                cmd = [uv_path, "run", "python", target_script]
            else:
                cmd = ["python3", target_script]

            # Set Linux kernel PR_SET_PDEATHSIG so the kernel terminates Byparr if ASSELLA dies
            def _preexec():
                try:
                    import ctypes
                    libc = ctypes.CDLL("libc.so.6")
                    libc.prctl(1, signal.SIGTERM)  # 1 = PR_SET_PDEATHSIG
                except Exception:
                    pass

            logger.info(f"[SteamDB] Starting background Byparr process in {byparr_dir} (ASSELLA PID={os.getpid()})...")
            log_dir = get_base_path() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            byparr_log_path = log_dir / "byparr.log"
            cls._rotate_byparr_log(byparr_log_path, max_sessions=5)
            byparr_logfile = open(byparr_log_path, "a", encoding="utf-8")
            byparr_logfile.write(
                f"\n--- Byparr Session Started by ASSELLA (PID={os.getpid()}) at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n"
            )
            byparr_logfile.flush()

            cls._process = subprocess.Popen(
                cmd,
                cwd=str(byparr_dir),
                env=env,
                stdout=byparr_logfile,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                preexec_fn=_preexec if sys.platform != "win32" else None,
            )
            cls._managed = True

            # Wait up to 10 seconds for service readiness
            for _ in range(20):
                time.sleep(0.5)
                if cls.is_running():
                    logger.info("[SteamDB] Byparr helper process is now running and responsive.")
                    return True

            logger.warning("[SteamDB] Byparr process launched but socket connect timed out.")
            return False
        except Exception as e:
            logger.error(f"[SteamDB] Failed to launch Byparr process: {e}")
            return False

    @classmethod
    def stop(cls):
        """Stops the managed Byparr process and all its child browsers."""
        if cls._process and cls._managed:
            proc = cls._process
            cls._process = None
            cls._managed = False
            logger.info("[SteamDB] Terminating Byparr helper process tree...")
            try:
                if sys.platform != "win32":
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(pgid, signal.SIGKILL)
                else:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                logger.info("[SteamDB] Byparr helper process successfully stopped.")
            except Exception as e:
                logger.debug(f"[SteamDB] Error stopping Byparr process: {e}")

        # Ensure any lingering Byparr process from the ACCELA directory is cleaned up
        byparr_dir = cls.find_byparr_dir()
        if byparr_dir and cls.is_running():
            try:
                subprocess.run(["pkill", "-f", str(byparr_dir)], capture_output=True, timeout=2)
            except Exception:
                pass

    @classmethod
    def verify_steamdb_bypass(cls, timeout_seconds: int = 30) -> bool:
        """
        Actively verifies that Byparr can bypass Cloudflare Turnstile and reach SteamDB.
        Performs a POST to /v1 requesting https://steamdb.info/ with returnOnlyCookies=True.
        Caches cookies (cf_clearance) and userAgent on success.
        Returns True if Cloudflare was bypassed and SteamDB responded with 200 OK.
        """
        if not cls.ensure_running():
            return False

        endpoint = f"{cls.get_service_url()}/v1"
        payload = {
            "cmd": "request.get",
            "url": "https://steamdb.info/",
            "maxTimeout": timeout_seconds * 1000 if timeout_seconds < 1000 else timeout_seconds,
            "returnOnlyCookies": True,
        }
        try:
            r = requests.post(endpoint, json=payload, timeout=timeout_seconds + 10)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "ok":
                    sol = data.get("solution", {})
                    if sol.get("status") == 200:
                        cookies = {c["name"]: c["value"] for c in sol.get("cookies", [])}
                        ua = sol.get("userAgent", "")
                        cf_val = cookies.get("cf_clearance")
                        if cf_val:
                            s = get_settings()
                            s.setValue("steamdb/user_agent", ua)
                            s.setValue("steamdb/cf_clearance", cf_val)
                            s.setValue("steamdb/clearance_timestamp", int(time.time()))
                        logger.info("[SteamDB] Cloudflare Turnstile bypass confirmed working via Byparr.")
                        return True
            logger.warning(f"[SteamDB] SteamDB verification failed (status {r.status_code}): {r.text[:150]}")
        except Exception as e:
            logger.debug(f"[SteamDB] Failed to verify SteamDB bypass via Byparr: {e}")
        return False


# Register clean process termination on normal or unexpected Python exit
atexit.register(ByparrManager.stop)


class SteamDBScraper:
    """Scrapes SteamDB for patchnotes and depot manifest IDs."""

    def __init__(self, solver_url: Optional[str] = None):
        self.solver_url = solver_url or ByparrManager.get_service_url()

    def fetch_rendered_html(self, url: str, timeout_seconds: int = 60) -> str:
        """
        Fetches the fully-rendered DOM from SteamDB via Byparr (which executes JS & bypasses Turnstile).
        """
        if not ByparrManager.is_running():
            ByparrManager.ensure_running()

        endpoint = f"{self.solver_url}/v1"
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": timeout_seconds * 1000 if timeout_seconds < 1000 else timeout_seconds,
        }

        try:
            r = requests.post(endpoint, json=payload, timeout=timeout_seconds + 15)
            if r.status_code == 200:
                data = r.json()
                sol = data.get("solution", {})
                html = sol.get("response", "")
                
                # Cache cookies & user-agent for fast static requests
                cookies = {c["name"]: c["value"] for c in sol.get("cookies", [])}
                ua = sol.get("userAgent", "")
                cf_val = cookies.get("cf_clearance")
                if cf_val:
                    s = get_settings()
                    s.setValue("steamdb/user_agent", ua)
                    s.setValue("steamdb/cf_clearance", cf_val)
                    s.setValue("steamdb/clearance_timestamp", int(time.time()))
                
                return html
            else:
                logger.error(f"[SteamDB] Byparr returned status {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"[SteamDB] Failed to fetch {url} via Byparr: {e}")

        return ""

    def get_patchnotes(self, appid: int, limit: int = 40) -> List[Dict[str, Any]]:
        """
        Scrapes https://steamdb.info/app/<appid>/patchnotes/
        Returns list of patch dictionaries:
        [{date, day, time, title, buildid, patchnotes_url}]
        """
        url = f"{STEAMDB_BASE_URL}/app/{appid}/patchnotes/"
        html = self.fetch_rendered_html(url)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        results = []

        # Find the table containing "Patch Title"
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            if any("patch title" in h.lower() for h in headers):
                for row in table.find_all("tr")[1:]:
                    cols = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                    if len(cols) >= 4:
                        date_str = cols[0]
                        day_str = cols[1] if len(cols) > 1 else ""
                        time_str = cols[2] if len(cols) > 2 else ""
                        title_str = cols[3] if len(cols) > 3 else ""
                        build_id = cols[-1] if len(cols) > 4 else ""

                        # Extract clean build ID (digits only)
                        clean_bid = "".join(re.findall(r"\d+", build_id))
                        if not clean_bid:
                            for a in row.find_all("a", href=True):
                                m = re.search(r"/patchnotes/(\d+)", a["href"])
                                if m:
                                    clean_bid = m.group(1)
                                    break

                        patch_url = f"{STEAMDB_BASE_URL}/patchnotes/{clean_bid}/" if clean_bid else ""
                        for a in row.find_all("a", href=True):
                            if "/patchnotes/" in a["href"]:
                                patch_url = f"{STEAMDB_BASE_URL}{a['href']}" if a["href"].startswith("/") else a["href"]
                                break

                        if clean_bid:
                            results.append({
                                "date": date_str,
                                "day": day_str,
                                "time": time_str,
                                "title": title_str or f"Update (Build {clean_bid})",
                                "buildid": clean_bid,
                                "patchnotes_url": patch_url,
                            })
                            if len(results) >= limit:
                                break
                if results:
                    break

        logger.info(f"[SteamDB] Parsed {len(results)} patchnotes entries for App {appid}")
        return results

    def get_patch_depots(self, buildid_or_url: str) -> Dict[str, Dict[str, str]]:
        """
        Scrapes https://steamdb.info/patchnotes/<buildid>/
        Returns dictionary mapping depot_id -> {depot_id, manifest_id, old_manifest_id}
        """
        if str(buildid_or_url).startswith("http"):
            url = str(buildid_or_url)
        else:
            url = f"{STEAMDB_BASE_URL}/patchnotes/{str(buildid_or_url).strip('/')}/"

        html = self.fetch_rendered_html(url)
        if not html:
            return {}

        soup = BeautifulSoup(html, "html.parser")
        depots = {}

        # Look for depot headers and history links
        for heading in soup.select(".panel-heading"):
            depot_link = heading.find("a", href=lambda h: h and "/depot/" in h)
            if depot_link:
                d_match = re.search(r"/depot/(\d+)", depot_link["href"])
                if d_match:
                    depot_id = d_match.group(1)
                    
                    # Target manifest ID is in changeid=M:<id> on the depot link
                    m_target = re.search(r"changeid=M:(\d+)", depot_link["href"])
                    manifest_id = m_target.group(1) if m_target else ""
                    
                    old_manifest_id = ""
                    panel = heading.find_parent("div")
                    if panel:
                        manifest_links = [
                            re.search(r"changeid=M:(\d+)", a["href"]).group(1)
                            for a in panel.find_all("a", href=True)
                            if "/history/?changeid=M:" in a["href"] and re.search(r"changeid=M:(\d+)", a["href"])
                        ]
                        # If there are two manifest IDs, the other one is old
                        for mid in manifest_links:
                            if mid != manifest_id:
                                old_manifest_id = mid
                                break

                    depots[depot_id] = {
                        "depot_id": depot_id,
                        "manifest_id": manifest_id,
                        "old_manifest_id": old_manifest_id,
                    }

        logger.info(f"[SteamDB] Extracted {len(depots)} depots for {url}")
        return depots

    def get_app_depots(self, appid: str | int) -> Dict[str, Dict[str, Any]]:
        """
        Scrapes https://steamdb.info/app/<appid>/depots/
        Extracts all depots (including DLC depots) with their names, configurations, and sizes.
        """
        appid_str = str(appid)
        url = f"{STEAMDB_BASE_URL}/app/{appid_str}/depots/"
        html = self.fetch_rendered_html(url)
        if not html:
            return self._fetch_depots_fallback_steam_store(appid_str)

        soup = BeautifulSoup(html, "html.parser")
        depots = {}

        for table in soup.find_all("table"):
            header_row = table.find("tr")
            if not header_row:
                continue
            headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]
            if not ("id" in headers and "configuration" in headers and "size" in headers):
                continue

            for row in table.find_all("tr")[1:]:
                cols = row.find_all(["td", "th"])
                if len(cols) >= 3:
                    depot_id = cols[0].get_text(strip=True)
                    if not depot_id.isdigit():
                        continue

                    config_cell = cols[1]
                    size_cell = cols[2]
                    dl_cell = cols[3] if len(cols) > 3 else None

                    muted_span = config_cell.find("span", class_=re.compile(r"muted"))
                    if muted_span:
                        name = muted_span.get_text(strip=True)
                    else:
                        name = config_cell.get_text(" ", strip=True)
                        name = re.sub(r"^DLC\s+\d+\s*", "", name).strip()

                    oslist = None
                    os_span = config_cell.find(class_=re.compile(r"depot-os"))
                    if os_span:
                        os_text = os_span.get_text(strip=True).lower()
                        if "windows" in os_text:
                            oslist = "windows"
                        elif "linux" in os_text:
                            oslist = "linux"
                        elif "mac" in os_text:
                            oslist = "macos"

                    is_dlc = bool(config_cell.find(string=re.compile(r"DLC\s+\d+")) or "dlc" in config_cell.get_text().lower())
                    size_str = size_cell.get_text(strip=True) if size_cell else ""
                    dl_str = dl_cell.get_text(strip=True) if dl_cell else ""
                    size_bytes = parse_size_to_bytes(size_str)

                    depots[depot_id] = {
                        "depot_id": depot_id,
                        "name": name,
                        "oslist": oslist,
                        "size_str": size_str,
                        "size_bytes": size_bytes,
                        "dl_str": dl_str,
                        "is_dlc": is_dlc,
                    }

        logger.info(f"[SteamDB] Parsed {len(depots)} depots for App {appid_str}")
        return depots

    def _fetch_depots_fallback_steam_store(self, appid: str) -> Dict[str, Dict[str, Any]]:
        """Fallback when SteamDB is unavailable: queries Steam Store API for DLC names."""
        results = {}
        try:
            resp = requests.get(
                f"https://store.steampowered.com/api/appdetails?appids={appid}",
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if resp.status_code == 200:
                data = resp.json()
                dlcs = data.get(str(appid), {}).get("data", {}).get("dlc", [])
                for dlc_id in dlcs:
                    try:
                        d_resp = requests.get(
                            f"https://store.steampowered.com/api/appdetails?appids={dlc_id}",
                            timeout=6,
                            headers={"User-Agent": "Mozilla/5.0"}
                        )
                        if d_resp.status_code == 200:
                            dlc_data = d_resp.json()
                            dlc_name = dlc_data.get(str(dlc_id), {}).get("data", {}).get("name")
                            if dlc_name:
                                results[str(dlc_id)] = {
                                    "depot_id": str(dlc_id),
                                    "name": dlc_name,
                                    "oslist": None,
                                    "size_str": "",
                                    "size_bytes": 0,
                                    "dl_str": "",
                                    "is_dlc": True,
                                }
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"[SteamDB Fallback] Store API fetch failed for {appid}: {e}")
        return results


def parse_size_to_bytes(size_str: str) -> int:
    """Converts human readable size string like '21.64 MiB' or '1.31 GiB' to byte count."""
    if not size_str or "no size" in size_str.lower():
        return 0
    units = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000 * 1000,
        "mib": 1024 * 1024,
        "gb": 1000 * 1000 * 1000,
        "gib": 1024 * 1024 * 1024,
        "tb": 1024 * 1024 * 1024 * 1024,
        "tib": 1024 * 1024 * 1024 * 1024,
    }
    m = re.match(r"^([\d.]+)\s*([a-zA-Z]+)", size_str.strip())
    if m:
        try:
            val = float(m.group(1))
            unit = m.group(2).lower()
            return int(val * units.get(unit, 1))
        except Exception:
            pass
    return 0


try:
    import zstandard as zstd
except ImportError:
    zstd = None
import sqlite3
import json
import threading


class SteamDBBuildsCache:
    """Manages persistent zstandard-compressed SQLite caching of the last 6 builds in its own database (steamdb_builds.db)."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SteamDBBuildsCache, cls).__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        from utils.helpers import get_data_file_path
        # Migrate legacy root file if present
        legacy_path = get_base_path() / "steamdb_builds.db"
        target_path = get_base_path() / "db" / "steamdb_builds.db"
        if legacy_path.exists() and not target_path.exists():
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.move(str(legacy_path), str(target_path))
                logger.info(f"[SteamDB Cache] Migrated legacy steamdb_builds.db from root to {target_path}")
            except Exception as e:
                logger.warning(f"[SteamDB Cache] Failed to migrate legacy steamdb_builds.db: {e}")

        self.db_path = get_data_file_path("steamdb_builds.db")
        self._conn_lock = threading.RLock()
        self.cctx = zstd.ZstdCompressor(level=3) if zstd else None
        self.dctx = zstd.ZstdDecompressor() if zstd else None
        self._init_db()
        self._initialized = True

    def _init_db(self):
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._conn_lock, sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS builds (
                        appid INTEGER PRIMARY KEY,
                        builds_blob BLOB,
                        last_updated INTEGER
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"[SteamDB Cache] Failed to init database at {self.db_path}: {e}")

    def get_builds(self, appid: int) -> List[Dict[str, Any]]:
        """Retrieves cached builds (up to 6) for appid, decompressed from zstd."""
        return self.get_builds_with_age(appid)[0]

    def get_builds_with_age(self, appid: int) -> tuple:
        """Returns (builds: list, age_seconds: float). age_seconds is -1 if not cached."""
        try:
            with self._conn_lock, sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT builds_blob, last_updated FROM builds WHERE appid = ?", (int(appid),))
                row = cur.fetchone()
                if row and row[0]:
                    raw_bytes = row[0]
                    last_updated = row[1] or 0
                    if self.dctx:
                        try:
                            decompressed = self.dctx.decompress(raw_bytes)
                        except Exception:
                            decompressed = raw_bytes
                    else:
                        decompressed = raw_bytes
                    builds = json.loads(decompressed.decode("utf-8"))
                    age = time.time() - last_updated
                    return builds, age
        except Exception as e:
            logger.debug(f"[SteamDB Cache] Failed to read cached builds for {appid}: {e}")
        return [], -1

    def save_builds(self, appid: int, builds: List[Dict[str, Any]]):
        """Saves up to the 6 most recent builds into steamdb_builds.db with zstandard compression."""
        if not builds:
            return
        top_6 = builds[:6]
        try:
            json_bytes = json.dumps(top_6).encode("utf-8")
            compressed_blob = self.cctx.compress(json_bytes) if self.cctx else json_bytes

            with self._conn_lock, sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO builds (appid, builds_blob, last_updated)
                    VALUES (?, ?, ?)
                    ON CONFLICT(appid) DO UPDATE SET
                        builds_blob = excluded.builds_blob,
                        last_updated = excluded.last_updated
                """, (int(appid), compressed_blob, int(time.time())))
                conn.commit()
                logger.info(f"[SteamDB Cache] Successfully cached {len(top_6)} builds for App {appid} in {self.db_path.name}")
        except Exception as e:
            logger.error(f"[SteamDB Cache] Failed to save builds for {appid}: {e}")

    def update_build_depots(self, appid: int, buildid: str, depots: Dict[str, Any]):
        """Updates depot information for a cached build entry."""
        builds = self.get_builds(appid)
        updated = False
        for b in builds:
            if str(b.get("buildid")) == str(buildid):
                b["depots"] = depots
                updated = True
                break
        if updated:
            self.save_builds(appid, builds)

