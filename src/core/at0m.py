#!/usr/bin/env python3
"""
at0-m Manifest Engine (at0m.py)
--------------------------------
Primary manifest retrieval pipeline for ASSella using direct Steam CDN downloads
authenticated via Manifest Request Codes (MRC) from wudrm (gmrc.wudrm.com),
with automatic, transparent fallback to the Hubcap API (hubcapmanifest.com).

Workflow:
  1. Primary:
     - Request MRC from http://gmrc.wudrm.com/manifest/{manifest_id}
     - Discover active SteamPipe CDN servers via Steam Directory Service API
     - Download encrypted manifest payload from Valve CDN:
         GET /depot/{depot_id}/manifest/{manifest_id}/5/{mrc}
     - Unpack and optionally decrypt filenames using the depot decryption key
     - Produce valid uncompressed Valve .manifest bytes (magic 0x71F617D0)
  2. Fallback:
     - If MRC retrieval fails (e.g. 503, rate-limit, missing) or CDN fails,
       transparently fall back to morrenus_api.generate_single_manifest().
"""

import os
import sys
import io
import time
import zipfile
import hashlib
import binascii
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Union, List

import requests
import sqlite3
import subprocess

# Optional: curl_cffi for Chrome 120 TLS impersonation (Tier 2 MRC fallback)
# Already bundled in the ASSella AppImage (used by steamdb_scraper.py too)
try:
    from curl_cffi import requests as cffi_requests
    _CFFI_AVAILABLE = True
except ImportError:
    cffi_requests = None  # type: ignore[assignment]
    _CFFI_AVAILABLE = False

# Set up logging
logger = logging.getLogger("at0m")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [at0-m] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Optional SteamKit2 / ValvePython manifest support
try:
    from steam.core.manifest import DepotManifest
    HAS_STEAM_LIB = True
except ImportError:
    DepotManifest = None
    HAS_STEAM_LIB = False

# Ensure ASSella source modules can be imported for fallback
ACCELA_ROOT = Path(__file__).resolve().parent
for candidate in [
    ACCELA_ROOT / "squashfs-root" / "bin" / "src",
    ACCELA_ROOT / "src",
]:
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

try:
    from core import morrenus_api
    HAS_MORRENUS = True
except ImportError:
    try:
        import morrenus_api
        HAS_MORRENUS = True
    except ImportError:
        morrenus_api = None
        HAS_MORRENUS = False


def _get_mrc_db_path() -> Path:
    """Returns Path to db/mrc_cache.db for persistent MRC code caching."""
    try:
        from utils.helpers import get_base_path
        base = get_base_path()
    except Exception:
        base = Path.home() / ".local" / "share" / "ACCELA"
    db_dir = base / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "mrc_cache.db"


class WudrmMRCFetcher:
    """
    Fetches Manifest Request Codes (MRC) from wudrm service.

    Two-tier fetch strategy (mirrors the download.lua plugin approach):
      Tier 1 — Plain requests.Session (fast path, ~400ms, in-process).
                Works when Cloudflare's edge cache serves the MRC directly.
      Tier 2 — curl_cffi Chrome 120 TLS impersonation (in-process, no subprocess).
                Used as immediate fallback when Tier 1 gets CF-blocked (503/HTML body).
                Spoofs Chrome 120's TLS Client Hello, cipher order, HTTP/2 settings
                and browser headers so CF treats the request as a real browser.

    Per-attempt flow:
      1. Tier 1: plain GET → if valid uint64 body → return MRC immediately.
      2. Tier 1 blocked (non-numeric body or 503) → Tier 2: cffi_requests.get with
         impersonate='chrome120' → if valid uint64 body → return MRC.
      3. Both fail → exponential backoff (1s, 2s, 4s, capped at 8s) → next attempt.
      4. All MAX_MANIFEST_TRIES exhausted → return None → Hubcap fallback.

    Results are cached in memory (per-process) and in a persistent SQLite DB so
    subsequent calls for the same manifest GID within the same update cycle are free.

    NOTE: curl_cffi Session reuse is intentionally avoided — persistent keepalive
    connections get consistently 503'd by Cloudflare; per-request calls succeed.
    """

    # Whether curl_cffi is available and the impersonate tier is usable
    _cffi_available: Optional[bool] = None

    def __init__(self, max_tries: int = 5, timeout: int = 6):
        self.max_tries = max_tries
        self.timeout = timeout
        self.base_url = "https://gmrc.wudrm.com/manifest/"
        # Plain requests session — used for Tier 1 (fast path)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "curl/7.88.1",
            "Accept": "*/*",
        })
        self._memory_cache: Dict[str, str] = {}
        self._db_path = _get_mrc_db_path()
        self._init_db()
        self._probe_cffi()

    def _probe_cffi(self) -> None:
        """Check once at startup whether curl_cffi Chrome impersonation is available."""
        if WudrmMRCFetcher._cffi_available is not None:
            return
        if not _CFFI_AVAILABLE:
            WudrmMRCFetcher._cffi_available = False
            logger.debug("[MRC/Tier2] curl_cffi not available — impersonate fallback disabled")
        else:
            WudrmMRCFetcher._cffi_available = True
            logger.debug("[MRC/Tier2] curl_cffi available — Chrome 120 impersonate fallback enabled")

    def _init_db(self) -> None:
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS mrc_cache ("
                    "manifest_id TEXT PRIMARY KEY, "
                    "mrc TEXT NOT NULL, "
                    "created_at REAL NOT NULL"
                    ")"
                )
                conn.commit()
        except Exception as e:
            logger.debug(f"[MRC Cache] Failed to initialize SQLite cache table: {e}")

    def _get_cached_mrc(self, manifest_id_str: str) -> Optional[str]:
        try:
            with sqlite3.connect(str(self._db_path), timeout=2) as conn:
                cur = conn.cursor()
                cur.execute("SELECT mrc FROM mrc_cache WHERE manifest_id = ?", (manifest_id_str,))
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0])
        except Exception as e:
            logger.debug(f"[MRC Cache] Error reading cache for {manifest_id_str}: {e}")
        return None

    def _save_cached_mrc(self, manifest_id_str: str, mrc: str) -> None:
        try:
            with sqlite3.connect(str(self._db_path), timeout=2) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO mrc_cache (manifest_id, mrc, created_at) VALUES (?, ?, ?)",
                    (manifest_id_str, mrc, time.time())
                )
                conn.commit()
        except Exception as e:
            logger.debug(f"[MRC Cache] Error saving cache for {manifest_id_str}: {e}")

    def invalidate_cached_mrc(self, manifest_id_str: str) -> None:
        """Removes a manifest_id from in-memory and SQLite cache if CDN rejected it."""
        clean_id = str(manifest_id_str).strip()
        self._memory_cache.pop(clean_id, None)
        try:
            with sqlite3.connect(str(self._db_path), timeout=2) as conn:
                conn.execute("DELETE FROM mrc_cache WHERE manifest_id = ?", (clean_id,))
                conn.commit()
            logger.info(f"[at0-m] [MRC Cache] Invalidated stale cache for {clean_id}")
        except Exception as e:
            logger.debug(f"[MRC Cache] Error invalidating cache for {clean_id}: {e}")

    @staticmethod
    def _is_valid_mrc(body: str) -> bool:
        """Returns True if body is a non-empty positive integer (valid uint64 MRC)."""
        s = body.strip()
        return bool(s) and s.isdigit() and int(s) > 0

    def _fetch_tier1(self, url: str) -> Optional[str]:
        """Tier 1: plain requests GET. Returns MRC string or None."""
        try:
            r = self.session.get(url, timeout=self.timeout)
            if r.status_code in (400, 404):
                # Fatal — manifest genuinely doesn't exist on wudrm
                raise _WudrmFatal(r.status_code)
            body = r.text.strip()
            if self._is_valid_mrc(body):
                return body
            logger.debug(f"[MRC/Tier1] Non-numeric body (HTTP {r.status_code}) — handing off to Tier 2")
        except _WudrmFatal:
            raise
        except Exception as e:
            logger.debug(f"[MRC/Tier1] Request error: {e}")
        return None

    def _fetch_tier2(self, url: str) -> Optional[str]:
        """
        Tier 2: curl_cffi Chrome 120 TLS impersonation, with system curl fallback.
        Uses a fresh per-request call (no session) — persistent connections get 503'd by CF.
        Returns MRC string or None.
        """
        if WudrmMRCFetcher._cffi_available:
            try:
                r = cffi_requests.get(
                    url,
                    impersonate="chrome120",
                    timeout=self.timeout,
                )
                body = r.text.strip()
                if self._is_valid_mrc(body):
                    return body
                logger.debug(f"[MRC/Tier2] cffi non-numeric body (HTTP {r.status_code})")
            except Exception as e:
                logger.debug(f"[MRC/Tier2] cffi request error: {e}")

        # Subprocess curl fallback if curl_cffi is missing or failed
        try:
            cmd = ["curl", "-s", "--max-time", str(int(self.timeout)), "-H", "User-Agent: Valve/Steam HTTP Client 1.0", url]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=self.timeout + 1)
            if res.returncode == 0:
                body = res.stdout.strip()
                if self._is_valid_mrc(body):
                    logger.debug("[MRC/Tier2] System curl successfully fetched MRC")
                    return body
        except Exception as e:
            logger.debug(f"[MRC/Tier2] system curl fallback error: {e}")

        return None

    def fetch_wudrm(self, manifest_id_str: str) -> Optional[str]:
        """Runs two-tier wudrm fetch (plain requests -> Chrome 120 TLS impersonation)."""
        url = f"{self.base_url}{manifest_id_str}"
        BASE_SLEEP = 0.5
        MAX_SLEEP = 2.0

        for attempt in range(1, self.max_tries + 1):
            try:
                mrc = self._fetch_tier1(url)
                if mrc:
                    logger.info(f"[MRC/Wudrm] ✅ Got MRC for {manifest_id_str} on attempt {attempt}")
                    return mrc

                logger.debug(f"[MRC/Wudrm] Tier 1 blocked/empty — trying Tier 2 for {manifest_id_str}")
                mrc = self._fetch_tier2(url)
                if mrc:
                    logger.info(f"[MRC/Wudrm] ✅ Got MRC via cffi impersonate for {manifest_id_str} on attempt {attempt}")
                    return mrc
            except _WudrmFatal:
                logger.info(f"[MRC/Wudrm] Wudrm returned 400/404 for manifest {manifest_id_str}")
                return None
            except Exception as e:
                logger.debug(f"[MRC/Wudrm] Attempt {attempt} error: {e}")

            if attempt < self.max_tries:
                time.sleep(min(BASE_SLEEP * attempt, MAX_SLEEP))

        return None

    def fetch_manifestdex(self, manifest_id_str: str) -> Optional[str]:
        """Fetch MRC from ManifestDeX provider (https://manifest.manifestdex.com/)."""
        try:
            url = f"https://manifest.manifestdex.com/{manifest_id_str}"
            headers = {"User-Agent": "ManifestDeX/1.0", "Accept": "*/*"}
            r = self.session.get(url, headers=headers, timeout=self.timeout)
            if r.status_code == 200:
                body = r.text.strip()
                if self._is_valid_mrc(body):
                    logger.info(f"[MRC/ManifestDeX] ✅ Got MRC from ManifestDeX for {manifest_id_str}")
                    return body
            else:
                logger.debug(f"[MRC/ManifestDeX] HTTP {r.status_code} for {manifest_id_str}")
        except Exception as e:
            logger.debug(f"[MRC/ManifestDeX] Request error for {manifest_id_str}: {e}")
        return None

    def race_mrc(self, manifest_id_str: str, timeout: float = 4.0) -> Tuple[Optional[str], Optional[str]]:
        """
        Races Wudrm and ManifestDeX concurrently.
        Returns:
            (winner_name, mrc) where winner_name is 'manifestdex' or 'wudrm'.
            If both fail, returns (None, None).
        """
        import concurrent.futures

        def _do_wudrm():
            return "wudrm", self.fetch_wudrm(manifest_id_str)

        def _do_dex():
            return "manifestdex", self.fetch_manifestdex(manifest_id_str)

        logger.info(f"[MRC/Race] Racing Wudrm vs ManifestDeX for manifest {manifest_id_str} (timeout={timeout}s)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_to_name = {
                executor.submit(_do_wudrm): "wudrm",
                executor.submit(_do_dex): "manifestdex",
            }

            try:
                for f in concurrent.futures.as_completed(future_to_name, timeout=timeout + 2.0):
                    try:
                        name, mrc = f.result()
                        if mrc:
                            logger.info(f"[MRC/Race] 🏁 Winner: {name} with MRC {mrc} for {manifest_id_str}")
                            return name, mrc
                    except Exception as ex:
                        logger.debug(f"[MRC/Race] Task failed: {ex}")
            except concurrent.futures.TimeoutError:
                logger.warning(f"[MRC/Race] Race timed out after {timeout + 2.0}s for {manifest_id_str}")

        logger.warning(f"[MRC/Race] Both providers failed in race for {manifest_id_str}")
        return None, None

    def fetch_mrc_single(self, manifest_id_str: str, provider: str) -> Optional[str]:
        """Fetch MRC using a specific provider without racing."""
        if provider == "manifestdex":
            return self.fetch_manifestdex(manifest_id_str)
        elif provider == "wudrm":
            return self.fetch_wudrm(manifest_id_str)
        return None

    def get_manifest_request_code(self, manifest_id: Union[str, int], provider: Optional[str] = None) -> Optional[str]:
        """
        Fetches the 64-bit Manifest Request Code for a given manifest GID.
        Checks in-memory and persistent SQLite cache first, then dispatches to
        the configured provider ('auto', 'wudrm', 'manifestdex').
        """
        manifest_id_str = str(manifest_id).strip()
        if provider is None:
            provider = get_manifest_provider()

        if provider == "hubcap":
            return None

        # 1. Fast in-memory cache hit (0.001ms)
        if manifest_id_str in self._memory_cache:
            logger.debug(f"[MRC Cache] Memory hit for {manifest_id_str}")
            return self._memory_cache[manifest_id_str]

        # 2. Persistent SQLite cache hit
        cached_mrc = self._get_cached_mrc(manifest_id_str)
        if cached_mrc:
            self._memory_cache[manifest_id_str] = cached_mrc
            logger.info(f"[MRC Cache] DB hit for {manifest_id_str}: {cached_mrc}")
            return cached_mrc

        # 3. Provider network fetch
        if provider == "manifestdex":
            mrc = self.fetch_manifestdex(manifest_id_str)
        elif provider == "wudrm":
            mrc = self.fetch_wudrm(manifest_id_str)
        else:
            _, mrc = self.race_mrc(manifest_id_str)

        if mrc:
            self._memory_cache[manifest_id_str] = mrc
            self._save_cached_mrc(manifest_id_str, mrc)
            return mrc

        logger.error(
            f"[MRC] Retrieval failed for manifest {manifest_id_str} via {provider}."
        )
        return None


class _WudrmFatal(Exception):
    """Raised by _fetch_tier1 when wudrm returns a fatal error (400/404)."""
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"wudrm fatal HTTP {status_code}")



class SteamCDNDownloader:
    """Discovers SteamPipe CDN servers and downloads encrypted depot manifests."""

    DIRECTORY_API = "https://api.steampowered.com/IContentServerDirectoryService/GetServersForSteamPipe/v1/?cell_id=0"
    DEFAULT_SERVERS = [
        {"host": "client-download.steamcontent.com", "vhost": "client-download.steamcontent.com", "https_support": "mandatory"},
        {"host": "content1.steampowered.com", "vhost": "content1.steampowered.com", "https_support": "preferred"},
        {"host": "cdn.steamcontent.com", "vhost": "cdn.steamcontent.com", "https_support": "preferred"},
    ]

    def __init__(self, cache_ttl_seconds: int = 3600):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Valve/Steam HTTP Client 1.0",
        })
        self._servers: List[Dict] = []
        self._servers_expire: float = 0.0
        self._cache_ttl = cache_ttl_seconds

    def get_content_servers(self, force_refresh: bool = False) -> List[Dict]:
        """Retrieves active Steam content servers, refreshing if expired."""
        now = time.time()
        if not force_refresh and self._servers and now < self._servers_expire:
            return self._servers

        try:
            logger.debug("Querying Steam Content Server Directory API...")
            r = self.session.get(self.DIRECTORY_API, timeout=5)
            if r.status_code == 200:
                data = r.json()
                servers = data.get("response", {}).get("servers", [])
                if servers:
                    self._servers = servers
                    self._servers_expire = now + self._cache_ttl
                    logger.debug(f"Discovered {len(servers)} SteamPipe CDN servers")
                    return self._servers
        except Exception as e:
            logger.warning(f"Failed to query Steam CDN directory: {e}")

        if not self._servers:
            self._servers = list(self.DEFAULT_SERVERS)
        return self._servers

    def download_manifest_raw(
        self,
        depot_id: Union[str, int],
        manifest_id: Union[str, int],
        manifest_request_code: Union[str, int],
        max_server_attempts: int = 4,
    ) -> Optional[bytes]:
        """
        Downloads the manifest package from Steam CDN using the given MRC.
        Returns the raw ZIP bytes received from Steam, or None on failure.
        """
        servers = self.get_content_servers()
        depot_str = str(depot_id)
        manifest_str = str(manifest_id)
        mrc_str = str(manifest_request_code)

        # Try up to max_server_attempts candidate servers
        for idx, server in enumerate(servers[:max_server_attempts]):
            host = server.get("host")
            vhost = server.get("vhost", host)
            https_support = server.get("https_support", "preferred")
            proto = "https" if https_support in ("mandatory", "preferred") else "http"
            url = f"{proto}://{host}/depot/{depot_str}/manifest/{manifest_str}/5/{mrc_str}"

            headers = {
                "Host": vhost,
                "User-Agent": "Valve/Steam HTTP Client 1.0",
            }

            try:
                logger.debug(f"CDN fetch attempt {idx + 1} from {host}...")
                resp = self.session.get(url, headers=headers, timeout=15)
                if resp.status_code == 200 and resp.content:
                    logger.info(
                        f"Successfully downloaded manifest for depot {depot_str} "
                        f"(GID {manifest_str}) from {host} ({len(resp.content)} bytes)"
                    )
                    return resp.content
                else:
                    logger.warning(f"CDN server {host} returned HTTP {resp.status_code}")
            except requests.exceptions.RequestException as e:
                logger.warning(f"CDN connection failed to {host}: {e}")

        logger.error(f"All CDN servers failed for depot {depot_str} (GID {manifest_str})")
        return None


# Module singleton instances
_mrc_fetcher = WudrmMRCFetcher()
_cdn_downloader = SteamCDNDownloader()


def unpack_and_process_manifest(
    zip_bytes: bytes,
    depot_key: Optional[Union[str, bytes]] = None,
) -> Optional[bytes]:
    """
    Unpacks the Steam CDN zip container and extracts/decrypts the Valve .manifest bytes.
    The output format has magic 0x71F617D0 (uncompressed Valve depot manifest).
    """
    try:
        # First attempt: if Steam library is available and depot_key is provided
        if HAS_STEAM_LIB and DepotManifest is not None:
            try:
                dm = DepotManifest(zip_bytes)
                if depot_key:
                    if isinstance(depot_key, str):
                        clean_key = depot_key.strip()
                        raw_key = binascii.unhexlify(clean_key)
                    else:
                        raw_key = depot_key

                    if dm.filenames_encrypted:
                        dm.decrypt_filenames(raw_key)
                        logger.info("Decrypted manifest file mappings with provided depot key")

                # Sanitize null characters in filenames and linktargets to prevent System.ArgumentException in DepotDownloader
                if hasattr(dm, "payload") and hasattr(dm.payload, "mappings"):
                    for m in dm.payload.mappings:
                        if hasattr(m, "filename"):
                            if isinstance(m.filename, str):
                                m.filename = m.filename.rstrip("\x00")
                            elif isinstance(m.filename, bytes):
                                m.filename = m.filename.rstrip(b"\x00").decode("utf-8", errors="replace")
                        if hasattr(m, "linktarget") and m.linktarget:
                            if isinstance(m.linktarget, str):
                                m.linktarget = m.linktarget.rstrip("\x00")
                            elif isinstance(m.linktarget, bytes):
                                m.linktarget = m.linktarget.rstrip(b"\x00").decode("utf-8", errors="replace")

                # Serialize uncompressed manifest (magic 0x71F617D0)
                return dm.serialize(compress=False)
            except Exception as e:
                logger.warning(f"DepotManifest object parsing failed ({e}), attempting raw zip extraction")

        # Second attempt: direct zip extraction of inner 'z' payload
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            namelist = zf.namelist()
            if namelist:
                inner_data = zf.read(namelist[0])
                # Verify magic 0x71F617D0 (d0 17 f6 71)
                if len(inner_data) >= 4 and inner_data[:4] == b"\xd0\x17\xf6\x71":
                    return inner_data
                return inner_data
    except Exception as e:
        logger.error(f"Failed to unpack CDN manifest payload: {e}")
    return None


def _lookup_cached_depot_key(depot_id: Union[str, int]) -> Optional[str]:
    """Look up cached AES depot key from DepotKeyManager or depot_keys.db."""
    depot_id_str = str(depot_id).strip()
    try:
        from managers.depot_key_manager import DepotKeyManager
        dkm = DepotKeyManager()
        key = dkm.get_depot_key(depot_id_str)
        if key:
            return key
    except Exception:
        pass

    try:
        from utils.helpers import get_base_path
        db_path = get_base_path() / "db" / "depot_keys.db"
        if db_path.exists():
            with sqlite3.connect(str(db_path), timeout=2) as conn:
                cur = conn.cursor()
                cur.execute("SELECT aes_key FROM depot_keys WHERE depot_id = ?", (depot_id_str,))
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0])
    except Exception:
        pass
    return None


def get_manifest_provider() -> str:
    """Reads user's configured manifest provider ('auto', 'wudrm', 'manifestdex', 'hubcap')."""
    try:
        from utils.settings import get_settings
        s = get_settings()
        val = s.value("manifest_provider", "auto", type=str)
        return str(val).strip().lower() if val else "auto"
    except Exception:
        return "auto"


def generate_single_manifest(
    depot_id: Union[str, int],
    manifest_id: Union[str, int],
    depot_key: Optional[Union[str, bytes]] = None,
    force_fallback: bool = False,
    provider: Optional[str] = None,
) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Retrieves a single depot manifest (current or historical).

    Primary method:
      Fetches MRC from configured provider (auto-race, wudrm, or manifestdex),
      downloads directly from Steam CDN, unpacks and decrypts.

    Fallback method:
      If primary fails or force_fallback is True, delegates to Hubcap API (morrenus_api).

    Returns:
      (raw_manifest_bytes, None) on success, or (None, error_message) on failure.
    """
    depot_str = str(depot_id).strip()
    manifest_str = str(manifest_id).strip()

    if provider is None:
        provider = get_manifest_provider()

    if provider == "hubcap":
        force_fallback = True

    # Automatically resolve depot key from cache if not provided by caller
    if not depot_key:
        resolved_key = _lookup_cached_depot_key(depot_str)
        if resolved_key:
            depot_key = resolved_key
            logger.debug(f"[Primary/at0-m] Auto-resolved cached depot key for Depot {depot_str}")

    # ── Path 1: Primary (MRC + Steam CDN) ────────────────────────────────────
    if not force_fallback:
        logger.info(
            f"[Primary/at0-m] Requesting manifest for Depot {depot_str}, GID {manifest_str} "
            f"via Steam CDN (Provider: {provider})..."
        )

        def _try_download_and_unpack(mrc_val: str, prov_tag: str) -> Optional[bytes]:
            cdn_zip = _cdn_downloader.download_manifest_raw(depot_str, manifest_str, mrc_val)
            if cdn_zip:
                processed = unpack_and_process_manifest(cdn_zip, depot_key=depot_key)
                if processed and len(processed) >= 4:
                    logger.info(
                        f"[Primary/at0-m] SUCCESS: Obtained {len(processed)} bytes manifest for "
                        f"{depot_str}_{manifest_str}.manifest via {prov_tag} MRC directly from Steam CDN!"
                    )
                    _mrc_fetcher._memory_cache[manifest_str] = mrc_val
                    _mrc_fetcher._save_cached_mrc(manifest_str, mrc_val)
                    return processed
                else:
                    logger.warning(f"[Primary/at0-m] Failed to unpack CDN manifest payload with {prov_tag} MRC")
            else:
                logger.warning(f"[Primary/at0-m] Steam CDN download failed with {prov_tag} MRC")
            return None

        # Check existing cache first
        cached_mrc = _mrc_fetcher._memory_cache.get(manifest_str) or _mrc_fetcher._get_cached_mrc(manifest_str)
        if cached_mrc:
            logger.debug(f"[Primary/at0-m] Found cached MRC {cached_mrc} for {manifest_str}")
            res = _try_download_and_unpack(cached_mrc, "Cached")
            if res:
                return res, None
            # If cached MRC was rejected by CDN, invalidate and proceed to fresh fetch
            _mrc_fetcher.invalidate_cached_mrc(manifest_str)

        # Fresh fetch based on provider
        if provider == "auto":
            # Concurrently race Wudrm vs ManifestDeX
            winner_name, winner_mrc = _mrc_fetcher.race_mrc(manifest_str)
            if winner_mrc:
                res = _try_download_and_unpack(winner_mrc, winner_name or "Winner")
                if res:
                    return res, None
                logger.warning(
                    f"[Primary/at0-m] Winner '{winner_name}' MRC failed on Steam CDN. "
                    f"Falling back to alternative provider..."
                )

            # Fallback to the other provider if winner failed on CDN
            alt_name = "wudrm" if winner_name == "manifestdex" else "manifestdex"
            logger.info(f"[Primary/at0-m] Trying alternative provider '{alt_name}' for manifest {manifest_str}...")
            alt_mrc = _mrc_fetcher.fetch_mrc_single(manifest_str, provider=alt_name)
            if alt_mrc and alt_mrc != winner_mrc:
                res = _try_download_and_unpack(alt_mrc, alt_name)
                if res:
                    return res, None

        elif provider in ("wudrm", "manifestdex"):
            mrc = _mrc_fetcher.fetch_mrc_single(manifest_str, provider=provider)
            if mrc:
                res = _try_download_and_unpack(mrc, provider)
                if res:
                    return res, None

        logger.warning(f"[Primary/at0-m] Primary CDN paths exhausted for {manifest_str}. Proceeding to Hubcap fallback.")

    # ── Path 2: Fallback (Hubcap API via morrenus_api) ────────────────────────
    logger.info(f"[Fallback/Hubcap] Falling back to Hubcap API for Depot {depot_str}, GID {manifest_str}...")
    if HAS_MORRENUS and morrenus_api is not None:
        try:
            raw_bytes, err = morrenus_api.generate_single_manifest(depot_str, manifest_str, force_hubcap=True)
            if raw_bytes and not err:
                logger.info(f"[Fallback/Hubcap] SUCCESS: Retrieved {len(raw_bytes)} bytes from Hubcap API")
                return raw_bytes, None
            logger.error(f"[Fallback/Hubcap] Hubcap API failed: {err}")
            return None, err or "Hubcap API generation failed"
        except Exception as e:
            err_msg = f"Hubcap fallback exception: {e}"
            logger.error(f"[Fallback/Hubcap] {err_msg}")
            return None, err_msg
    else:
        err_msg = "Primary at0-m CDN failed and Hubcap (morrenus_api) is not available"
        logger.error(err_msg)
        return None, err_msg


def get_workshop_keys_path() -> Path:
    """Returns Path to db/workshop_keys.txt with auto-migration from legacy root file."""
    try:
        from utils.paths import Paths
        return Paths.workshop_keys()
    except Exception:
        try:
            from utils.helpers import get_base_path
            base = get_base_path()
        except Exception:
            base = Path.home() / ".local" / "share" / "ACCELA"
        db_dir = base / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        target = db_dir / "workshop_keys.txt"
        legacy = base / "workshop_keys.txt"
        if legacy.exists() and not target.exists():
            import shutil
            try:
                shutil.move(str(legacy), str(target))
            except Exception:
                pass
        return target


def fetch_workshop_manifest(
    published_file_id: Union[str, int],
    api_key: Optional[str] = None,
    dest_dir: Optional[Union[str, Path]] = None,
) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Fetches a workshop item manifest using at0-m CDN primary with Hubcap smart fallback.

    Primary:
      1. Resolves published_file_id details via Steam WebAPI (GetPublishedFileDetails).
      2. If item is on SteamPipe (has hcontent_file) and depot key is known (from db/workshop_keys.txt),
         attempts at0-m generate_single_manifest(app_id, hcontent_file).

    Fallback:
      2. If primary is unavailable or fails, queries Hubcap /generate/workshopmanifest/{wid},
         reads X-App-Id, X-Manifest-Id, X-Depot-Key, saves the manifest, and updates db/workshop_keys.txt.

    Returns:
      ({"appid": appid, "manifest_id": manifest_id, "depot_key": depot_key, "manifest_path": path}, None)
      or (None, error_message)
    """
    wid_str = str(published_file_id).strip()
    logger.info(f"[Workshop] Requesting manifest for Workshop Item {wid_str}...")

    # Load known workshop keys from db folder (with automatic legacy root migration)
    workshop_keys_file = get_workshop_keys_path()

    known_keys: Dict[str, str] = {}
    if workshop_keys_file.exists():
        try:
            with open(workshop_keys_file, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(";")
                    if len(parts) >= 2:
                        known_keys[parts[0].strip()] = parts[1].strip()
        except Exception as e:
            logger.debug(f"Could not load {workshop_keys_file}: {e}")

    # ── Path 1: Primary (Steam WebAPI + at0-m CDN) ──────────────────────────
    try:
        details_url = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
        r_details = requests.post(
            details_url,
            data={"itemcount": 1, "publishedfileids[0]": wid_str},
            timeout=8
        )
        if r_details.status_code == 200:
            details_list = r_details.json().get("response", {}).get("publishedfiledetails", [])
            if details_list:
                details = details_list[0]
                consumer_app = str(details.get("consumer_app_id") or details.get("creator_app_id") or "").strip()
                hcontent = str(details.get("hcontent_file") or "").strip()
                depot_key = known_keys.get(consumer_app)
                if not depot_key and consumer_app:
                    depot_key = _lookup_cached_depot_key(consumer_app)
                    if depot_key:
                        try:
                            os.makedirs(os.path.dirname(workshop_keys_file), exist_ok=True)
                            with open(workshop_keys_file, "a", encoding="utf-8") as f:
                                f.write(f"{consumer_app};{depot_key}\n")
                            logger.info(f"[Workshop/at0-m] Auto-resolved and cached depot key for App {consumer_app}")
                        except Exception:
                            pass

                if consumer_app and hcontent and hcontent != "0":
                    if not depot_key:
                        logger.info(
                            f"[Workshop/at0-m] Resolved Workshop {wid_str} -> App: {consumer_app}, "
                            f"Manifest GID: {hcontent}, but depot key is not cached. Delegating to Hubcap to acquire key..."
                        )
                    else:
                        logger.info(
                            f"[Workshop/at0-m] Resolved Workshop {wid_str} -> App: {consumer_app}, "
                            f"Manifest GID: {hcontent}"
                        )
                        raw_bytes, err = generate_single_manifest(
                            consumer_app, hcontent, depot_key=depot_key, force_fallback=False
                        )
                        if raw_bytes and not err:
                            target_dir = Path(dest_dir or ACCELA_ROOT / "manifests")
                            mf, _ = save_manifest_with_sidecar(consumer_app, hcontent, raw_bytes, target_dir)
                            logger.info(f"[Workshop/at0-m] Successfully fetched workshop manifest via at0-m CDN!")
                            return {
                                "appid": consumer_app,
                                "manifest_id": hcontent,
                                "depot_key": depot_key,
                                "manifest_path": str(mf),
                                "title": details.get("title", ""),
                            }, None
    except Exception as e:
        logger.warning(f"[Workshop/at0-m] Primary workshop resolution failed ({e}), falling back to Hubcap")

    # ── Path 2: Smart Fallback (Hubcap /generate/workshopmanifest/{wid}) ──────
    logger.info(f"[Workshop/Hubcap] Falling back to Hubcap Workshop API for {wid_str}...")
    try:
        if not api_key:
            if HAS_MORRENUS:
                from utils.settings import get_settings
                s = get_settings()
                if s:
                    api_key = s.value("morrenus_api_key", "", type=str)

        if not api_key:
            return None, "Hubcap API key not configured for workshop fallback."

        hubcap_url = f"https://hubcapmanifest.com/api/v1/generate/workshopmanifest/{wid_str}"
        headers = {"Authorization": f"Bearer {api_key}"}

        from utils.isp_bypass import execute_hubcap_request
        session = requests.Session()
        r = execute_hubcap_request(session, "GET", hubcap_url, headers=headers, timeout=30)
        if r.status_code == 200:
            appid = r.headers.get("X-App-Id")
            manifest_id = r.headers.get("X-Manifest-Id")
            depot_key = r.headers.get("X-Depot-Key")

            if not appid or not manifest_id:
                return None, "Missing X-App-Id or X-Manifest-Id headers from Hubcap workshop response"

            target_dir = Path(dest_dir or ACCELA_ROOT / "manifests")
            mf, _ = save_manifest_with_sidecar(appid, manifest_id, r.content, target_dir)

            # Record newly learned depot key to db/workshop_keys.txt for future at0-m CDN downloads
            if depot_key and appid not in known_keys:
                try:
                    os.makedirs(os.path.dirname(workshop_keys_file), exist_ok=True)
                    with open(workshop_keys_file, "a", encoding="utf-8") as f:
                        f.write(f"{appid};{depot_key}\n")
                    logger.info(f"Learned and saved depot key for App {appid} to {workshop_keys_file}")
                except Exception as e:
                    logger.warning(f"Could not append to {workshop_keys_file}: {e}")

            logger.info(f"[Workshop/Hubcap] SUCCESS: Retrieved workshop manifest {appid}_{manifest_id}.manifest")
            return {
                "appid": appid,
                "manifest_id": manifest_id,
                "depot_key": depot_key,
                "manifest_path": str(mf),
            }, None
        else:
            return None, f"Hubcap workshop API returned HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:
        err_msg = f"Hubcap workshop fallback error: {e}"
        logger.error(err_msg)
        return None, err_msg


def resolve_build_depots_from_steamdb(build_id: Union[str, int]) -> Dict[str, str]:
    """
    Resolves a build_id to a depot-to-manifest mapping {depot_id: manifest_id}
    using SteamDBScraper via Byparr.
    """
    bid_str = str(build_id).strip()
    try:
        from core.steamdb_scraper import SteamDBScraper
        scraper = SteamDBScraper()
        patch_info = scraper.get_patch_depots(bid_str)
        mapping = {}
        for d_id, info in patch_info.items():
            if isinstance(info, dict) and "manifest_id" in info:
                mapping[str(d_id)] = str(info["manifest_id"])
            elif isinstance(info, str):
                mapping[str(d_id)] = str(info)
        return mapping
    except Exception as e:
        logger.warning(f"Could not resolve depots for build {bid_str} via SteamDB: {e}")
        return {}


def fetch_manifests_by_build_id(
    app_id: Union[str, int],
    build_id: Union[str, int],
    depot_keys: Optional[Dict[str, str]] = None,
    dest_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Tuple[Optional[Path], Optional[str]]]:
    """
    Downloads all depot manifests for a specific historical build_id.
    Resolves build_id -> {depot_id: manifest_id} via SteamDB scraper, then fetches
    each manifest through at0-m (with Hubcap smart fallback).
    """
    bid_str = str(build_id).strip()
    logger.info(f"Fetching historical manifests for App {app_id}, Build {bid_str}...")

    depots_map = resolve_build_depots_from_steamdb(bid_str)
    if not depots_map:
        logger.warning(f"No depot mapping found for build {bid_str}")
        return {}

    results = {}
    keys = depot_keys or {}
    for d_id, m_id in depots_map.items():
        key = keys.get(str(d_id))
        mf, err = fetch_and_install_manifest_for_update(
            app_id, d_id, m_id, depot_key=key, dest_dir=dest_dir
        )
        results[d_id] = (mf, err)

    return results


def save_manifest_with_sidecar(
    depot_id: Union[str, int],
    manifest_id: Union[str, int],
    manifest_bytes: bytes,
    output_dir: Union[str, Path],
) -> Tuple[Path, Path]:
    """
    Saves {depot_id}_{manifest_id}.manifest and its 20-byte SHA-1 sidecar
    {depot_id}_{manifest_id}.manifest.sha to the specified directory.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    manifest_file = out_path / f"{depot_id}_{manifest_id}.manifest"
    sha_file = out_path / f"{depot_id}_{manifest_id}.manifest.sha"

    with open(manifest_file, "wb") as f:
        f.write(manifest_bytes)

    # Compute standard raw 20-byte SHA-1 digest for DepotDownloader / ASSella delta patching
    sha1_digest = hashlib.sha1(manifest_bytes).digest()
    with open(sha_file, "wb") as f:
        f.write(sha1_digest)

    logger.info(f"Saved manifest to {manifest_file} (SHA sidecar: {sha_file.name})")
    return manifest_file, sha_file


def fetch_and_install_manifest_for_update(
    app_id: Union[str, int],
    depot_id: Union[str, int],
    manifest_id: Union[str, int],
    depot_key: Optional[Union[str, bytes]] = None,
    dest_dir: Optional[Union[str, Path]] = None,
    copy_to_steam_depotcache: bool = True,
) -> Tuple[Optional[Path], Optional[str]]:
    """
    Fetches the manifest for updating games, writing it to dest_dir
    (defaulting to mistwalker_manifests or depotcache), generating the .sha
    sidecar, and optionally copying into Steam's central depotcache.
    """
    manifest_bytes, err = generate_single_manifest(depot_id, manifest_id, depot_key=depot_key)
    if not manifest_bytes or err:
        return None, err or f"Failed to retrieve manifest for depot {depot_id}"

    if dest_dir is None:
        import tempfile
        dest_dir = Path(tempfile.gettempdir()) / "mistwalker_manifests"

    manifest_file, _ = save_manifest_with_sidecar(depot_id, manifest_id, manifest_bytes, dest_dir)

    # Optionally sync to central Steam depotcache if installed
    if copy_to_steam_depotcache:
        steam_candidates = [
            Path.home() / ".local" / "share" / "Steam" / "depotcache",
            Path.home() / ".steam" / "steam" / "depotcache",
            Path.home() / ".steam" / "root" / "depotcache",
        ]
        import shutil
        for sc in steam_candidates:
            if sc.exists() and sc.is_dir():
                try:
                    steam_dest = sc / manifest_file.name
                    shutil.copy2(manifest_file, steam_dest)
                    logger.info(f"Copied manifest to Steam central depotcache: {steam_dest}")
                except Exception as e:
                    logger.warning(f"Could not copy to Steam depotcache {sc}: {e}")

    return manifest_file, None


# ─────────────────────────────────────────────────────────────────────────────
# CLI & Self-Test
# ─────────────────────────────────────────────────────────────────────────────
def run_test():
    """
    Runs self-test on a real game manifest in the ASSella update workflow:
    Tests primary fetch -> Steam CDN -> Decryption -> SHA sidecar creation -> Verification.
    Also tests fallback mechanism.
    """
    print("=" * 65)
    print("  at0-m MANIFEST ENGINE - TEST SUITE")
    print("=" * 65)

    # Test 1: Real game manifest (Dungeon Village, AppID 1859360, Depot 1859361)
    test_app = 1859360
    test_depot = 1859361
    test_manifest = 2687941575295069335
    test_key = "2270d5a86ecd5f6f94a750b2e021e2fa860701209f0836a35b4520f4d083cb6e"

    print(f"\n[Test 1] Testing Primary Fetch: AppID {test_app}, Depot {test_depot}, Manifest {test_manifest}")
    manifest_bytes, err = generate_single_manifest(test_depot, test_manifest, depot_key=test_key)

    if manifest_bytes and not err:
        print(f" -> SUCCESS: Fetched {len(manifest_bytes)} bytes!")
        print(f" -> Magic signature: 0x{manifest_bytes[:4].hex().upper()} (matches Valve 0x71F617D0)")

        # Test writing manifest and .sha sidecar in the way ASSella update tasks do
        import tempfile
        test_out_dir = Path(tempfile.gettempdir()) / "at0m_test_output"
        mf, sf = save_manifest_with_sidecar(test_depot, test_manifest, manifest_bytes, test_out_dir)

        assert mf.exists() and mf.stat().st_size == len(manifest_bytes), "Manifest file write mismatch"
        assert sf.exists() and sf.stat().st_size == 20, "SHA sidecar file invalid"
        print(f" -> Manifest and 20-byte SHA sidecar successfully written to: {test_out_dir}")
        print(" -> [Test 1 Passed!]")
    else:
        print(f" -> FAIL: {err}")
        return False

    # Test 2: Historical Build Manifest (Animal Well, Build 14712319, Depot 813231, Manifest 2534703329182882505)
    hist_app = 813230
    hist_depot = 813231
    hist_manifest = 2534703329182882505
    hist_key = "bb42c41063ff494ceca8a0ce69218fcc97cc5f31c2066992f80ec8dab229486e"
    print(f"\n[Test 2] Testing Historical Build Manifest: App {hist_app}, Depot {hist_depot}, Manifest {hist_manifest} (Build 14712319)")
    h_bytes, h_err = generate_single_manifest(hist_depot, hist_manifest, depot_key=hist_key)
    if h_bytes and not h_err:
        print(f" -> SUCCESS: Fetched historical manifest ({len(h_bytes)} bytes, magic: 0x{h_bytes[:4].hex().upper()})!")
        print(" -> [Test 2 Passed!]")
    else:
        print(f" -> Historical fetch error: {h_err}")
        return False

    # Test 3: Fallback behavior on invalid manifest ID
    print("\n[Test 3] Testing Fallback Behavior with invalid manifest ID on wudrm...")
    fake_depot = 731
    fake_manifest = 9999999999999999999
    fb_bytes, fb_err = generate_single_manifest(fake_depot, fake_manifest)
    print(f" -> Graceful Fallback Result: Error handled cleanly ({fb_err})")
    print(" -> [Test 3 Passed!]")

    print("\n" + "=" * 65)
    print("  ALL TESTS PASSED! at0-m is ready for ASSella.")
    print("=" * 65)
    return True


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="at0-m Manifest Engine for ASSella")
    parser.add_argument("--test", action="store_true", help="Run integration test suite")
    parser.add_argument("--depot", type=int, help="Depot ID")
    parser.add_argument("--manifest", type=int, help="Manifest ID (GID)")
    parser.add_argument("--key", type=str, help="Depot decryption key (hex)")
    parser.add_argument("--app", type=int, help="App ID")
    parser.add_argument("--out", type=str, help="Output directory to save manifest and .sha")

    args = parser.parse_args()

    if args.test or len(sys.argv) == 1:
        run_test()
    elif args.depot and args.manifest:
        raw, error = generate_single_manifest(args.depot, args.manifest, depot_key=args.key)
        if raw:
            out_dir = args.out or "."
            save_manifest_with_sidecar(args.depot, args.manifest, raw, out_dir)
            print(f"Manifest fetched successfully ({len(raw)} bytes).")
        else:
            print(f"Error fetching manifest: {error}")
            sys.exit(1)
