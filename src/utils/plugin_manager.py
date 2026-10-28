"""Plugin Manager for on-demand SLSsteam Lua plugin distribution from Cloudflare R2.

Handles fetching plugins_manifest.json, validating SHA-256 hashes, downloading
on-demand to local cache (~/.local/share/ACCELA/plugins), and deploying into
active SLSsteam plugin directories with automatic .bak backup preservation.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from utils.helpers import get_base_path
from utils.paths import Paths
from utils.settings import get_settings
from utils.yaml_config_manager import (
    ensure_plugins_enabled,
    ensure_sls_plugin_config_sections,
    ensure_smart_tickets_enabled,
    get_sls_plugins_dirs,
    is_slssteam_plugins_enabled,
)

logger = logging.getLogger(__name__)

# Cloudflare R2 Public Bucket Endpoint
R2_PLUGINS_BASE_URL = "https://pub-19657b4f385d424b91a909253efdb29c.r2.dev"
MANIFEST_FILENAME = "plugins_manifest.json"
MANIFEST_URL = f"{R2_PLUGINS_BASE_URL}/{MANIFEST_FILENAME}"

DEFAULT_TIMEOUT = 10.0
HTTP_USER_AGENT = "ASSella-PluginManager/1.0"

# Known fallback plugin definitions in case network is offline and no cache exists
FALLBACK_REQUIRED_PLUGINS = [
    "spacebunny.lua",
    "spliced-tickets.lua",
    "lobster.lua",
    "hubcap.lua",
]

# Baseline companion plugins (non-interceptors) when manifest is unreachable
FALLBACK_COMPANION_PLUGINS = {
    "spliced-tickets.lua",
    "lobster.lua",
    "hubcap.lua",
}


def calculate_sha256(file_path: Path) -> Optional[str]:
    """Calculate the SHA-256 checksum of a file."""
    if not file_path.is_file():
        return None
    try:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        logger.warning(f"[PluginManager] Could not compute SHA-256 for {file_path}: {e}")
        return None


def get_plugins_cache_dir() -> Path:
    """Get the local persistent cache directory for downloaded plugins."""
    cache_dir = get_base_path() / "plugins"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_cached_plugin_path(filename: str) -> Path:
    """Return path to a plugin inside the local persistent cache."""
    return get_plugins_cache_dir() / filename


def get_cached_manifest_path() -> Path:
    """Return path to the local cached plugins_manifest.json."""
    return get_plugins_cache_dir() / MANIFEST_FILENAME


def _http_get_bytes(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    headers: Optional[Dict[str, str]] = None,
) -> bytes:
    """Fetch raw bytes from HTTP(S) URL with multiple resilience fallbacks.

    Tries in sequence:
      1. Standard urllib.request (verified TLS)
      2. urllib.request with unverified SSL context (fixes captive portal / ISP SSL hostname mismatch)
      3. curl_cffi with Chrome 120 TLS impersonation (if installed)
      4. Subprocess system curl with -k (insecure TLS fallback)
    """
    req_headers = {"User-Agent": HTTP_USER_AGENT}
    if headers:
        req_headers.update(headers)

    # 1. Standard urllib.request
    try:
        req = urllib.request.Request(url, headers=req_headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return resp.read()
    except Exception as e1:
        logger.debug(f"[PluginManager] Standard urllib fetch failed for {url}: {e1}")

    # 2. urllib with unverified SSL context (bypasses Hostname mismatch / SSL cert verify failed)
    if url.startswith("https"):
        try:
            unverified_ctx = ssl._create_unverified_context()
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, context=unverified_ctx, timeout=timeout) as resp:
                if resp.status == 200:
                    logger.info(f"[PluginManager] Fetched {url} via unverified SSL context fallback")
                    return resp.read()
        except Exception as e2:
            logger.debug(f"[PluginManager] Unverified SSL fetch failed for {url}: {e2}")

    # 3. curl_cffi with Chrome 120 impersonation
    try:
        from curl_cffi import requests as cffi_requests
        cffi_resp = cffi_requests.get(
            url, headers=req_headers, impersonate="chrome120", timeout=timeout, verify=False
        )
        if cffi_resp.status_code == 200 and cffi_resp.content:
            logger.info(f"[PluginManager] Fetched {url} via curl_cffi impersonation fallback")
            return cffi_resp.content
    except Exception as e3:
        logger.debug(f"[PluginManager] curl_cffi fetch failed for {url}: {e3}")

    # 4. System curl subprocess fallback
    try:
        cmd = ["curl", "-s", "-k", "--max-time", str(int(timeout)), "-A", HTTP_USER_AGENT, url]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout + 2)
        if res.returncode == 0 and res.stdout:
            logger.info(f"[PluginManager] Fetched {url} via system curl fallback")
            return res.stdout
    except Exception as e4:
        logger.debug(f"[PluginManager] System curl fetch failed for {url}: {e4}")

    raise RuntimeError(f"All network fetch attempts failed for {url}")


def fetch_plugins_manifest(force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    """Fetch plugins_manifest.json from Cloud, falling back to local cache or bundled manifest."""
    cached_path = get_cached_manifest_path()

    if not force_refresh and cached_path.is_file():
        try:
            with open(cached_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as read_err:
            logger.error(f"[PluginManager] Error reading cached manifest: {read_err}")

    try:
        logger.debug(f"[PluginManager] Fetching manifest from {MANIFEST_URL}")
        raw = _http_get_bytes(MANIFEST_URL, timeout=DEFAULT_TIMEOUT, headers={"Accept": "application/json"})
        if raw:
            data = json.loads(raw.decode("utf-8"))
            # Write to local cache atomically
            tmp_cache = cached_path.with_suffix(".tmp")
            tmp_cache.write_bytes(raw)
            tmp_cache.replace(cached_path)
            logger.debug(f"[PluginManager] Cached fresh manifest ({len(raw)} bytes)")
            return data
    except Exception as e:
        logger.warning(f"[PluginManager] Failed fetching remote manifest: {e}")

    # Fall back to local cached manifest if available
    if cached_path.is_file():
        try:
            with open(cached_path, "r", encoding="utf-8") as f:
                logger.info("[PluginManager] Using locally cached plugins_manifest.json")
                return json.load(f)
        except Exception as read_err:
            logger.error(f"[PluginManager] Error reading cached manifest: {read_err}")

    # Fall back to bundled manifest in res/plugins/
    try:
        bundled_manifest = Paths.resource("plugins/" + MANIFEST_FILENAME)
        if bundled_manifest.is_file():
            with open(bundled_manifest, "r", encoding="utf-8") as f:
                logger.info("[PluginManager] Using bundled fallback plugins_manifest.json")
                data = json.load(f)
                try:
                    shutil.copy2(bundled_manifest, cached_path)
                except Exception:
                    pass
                return data
    except Exception as b_err:
        logger.debug(f"[PluginManager] Error reading bundled manifest: {b_err}")

    return None


# Alias for convenience
get_plugins_manifest = fetch_plugins_manifest


def get_manifest_plugin_info(filename: str) -> Optional[Dict[str, Any]]:
    """Get metadata for a specific plugin from the manifest."""
    manifest = fetch_plugins_manifest()
    if manifest and "plugins" in manifest:
        return manifest["plugins"].get(filename)
    return None


def extract_plugin_version(content_or_path: Union[str, Path, bytes]) -> Optional[str]:
    """Extract semantic version string from Lua file content or path."""
    try:
        if isinstance(content_or_path, Path):
            if not content_or_path.is_file():
                return None
            content = content_or_path.read_text(encoding="utf-8", errors="ignore")
        elif isinstance(content_or_path, bytes):
            content = content_or_path.decode("utf-8", errors="ignore")
        else:
            content = str(content_or_path)

        # 1. Match VERSION = "1.8.2" or table.VERSION = "1.8.2"
        m = re.search(r'\bVERSION\s*=\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
        if m:
            sem = re.search(r'(\d+\.\d+(?:\.\d+)?(?:[a-zA-Z0-9_\.\-]+)?)', m.group(1))
            if sem:
                return sem.group(1)

        # 2. Match header comment e.g. -- spacebunny.lua (1.8.2)
        m2 = re.search(r'^--\s*[\w\.\-]+\.lua\s*\((v?\d+(?:\.\d+)*(?:[a-zA-Z0-9_\.\-]+)?)\)', content, re.MULTILINE | re.IGNORECASE)
        if m2:
            return m2.group(1).lstrip("v")

        # 3. Match header comment e.g. -- Version: 1.8.2
        m3 = re.search(r'^--\s*(?:version|ver|v)\s*[:=]?\s*v?(\d+\.\d+(?:\.\d+)*(?:[a-zA-Z0-9_\.\-]+)?)', content, re.MULTILINE | re.IGNORECASE)
        if m3:
            return m3.group(1)

        # 4. Match header comment e.g. -- spacebunny.lua v1.8.2
        m4 = re.search(r'^--\s*[\w\.\-]+\.lua\s+v?(\d+\.\d+(?:\.\d+)*(?:[a-zA-Z0-9_\.\-]+)?)', content, re.MULTILINE | re.IGNORECASE)
        if m4:
            return m4.group(1)
    except Exception as exc:
        logger.debug(f"[PluginManager] Error extracting plugin version: {exc}")
    return None


def get_plugin_version(filename: str) -> Optional[str]:
    """Get the version string of a plugin.
    Checks in order:
    1. Remote/cached manifest (plugins_manifest.json)
    2. Primary deployed plugin in SLSsteam plugins directory
    3. Cached plugin in ~/.local/share/ACCELA/plugins/
    4. Bundled plugin fallback in res/plugins/
    """
    # 1. Check manifest
    try:
        info = get_manifest_plugin_info(filename)
        if info and info.get("version"):
            return str(info["version"]).strip()
        # Fallback check aliases if spacebunny
        if filename == "spacebunny.lua":
            info15 = get_manifest_plugin_info("spacebunny1.5.lua")
            if info15 and info15.get("version"):
                return str(info15["version"]).strip()
        elif filename == "spacebunny1.5.lua":
            infosb = get_manifest_plugin_info("spacebunny.lua")
            if infosb and infosb.get("version"):
                return str(infosb["version"]).strip()
    except Exception as exc:
        logger.debug(f"[PluginManager] Error checking manifest for {filename} version: {exc}")

    # 2. Check deployed plugin file in SLS plugins dirs
    try:
        target_dirs = get_sls_plugins_dirs()
        for tdir in target_dirs:
            p = tdir / filename
            if p.is_file():
                v = extract_plugin_version(p)
                if v:
                    return v
            bak = tdir / f"{filename}.bak"
            if bak.is_file():
                v = extract_plugin_version(bak)
                if v:
                    return v
            if filename in ("spacebunny.lua", "spacebunny1.5.lua"):
                for alt_name in ("spacebunny.lua", "spacebunny1.5.lua"):
                    alt_p = tdir / alt_name
                    if alt_p.is_file():
                        v = extract_plugin_version(alt_p)
                        if v:
                            return v
    except Exception as exc:
        logger.debug(f"[PluginManager] Error checking deployed plugin {filename} version: {exc}")

    # 3. Check persistent cache
    try:
        c_path = get_cached_plugin_path(filename)
        if c_path.is_file():
            v = extract_plugin_version(c_path)
            if v:
                return v
        if filename in ("spacebunny.lua", "spacebunny1.5.lua"):
            for alt_name in ("spacebunny.lua", "spacebunny1.5.lua"):
                alt_c = get_cached_plugin_path(alt_name)
                if alt_c.is_file():
                    v = extract_plugin_version(alt_c)
                    if v:
                        return v
    except Exception as exc:
        logger.debug(f"[PluginManager] Error checking cache for {filename} version: {exc}")

    # 4. Check bundled fallback
    try:
        bundled = Paths.resource(f"plugins/{filename}")
        if not (bundled and bundled.is_file()) and filename in ("spacebunny.lua", "spacebunny1.5.lua"):
            alt_name = "spacebunny.lua" if filename == "spacebunny1.5.lua" else "spacebunny1.5.lua"
            try:
                alt_bundled = Paths.resource(f"plugins/{alt_name}")
                if alt_bundled.is_file():
                    bundled = alt_bundled
            except Exception as e:
                logger.debug(f"[PluginManager] Could not check bundled fallback alias {alt_name}: {e}")
        if bundled and bundled.is_file():
            v = extract_plugin_version(bundled)
            if v:
                return v
    except Exception as exc:
        logger.debug(f"[PluginManager] Error checking bundled fallback for {filename} version: {exc}")

    return None


def download_plugin(
    filename: str, expected_sha256: Optional[str] = None
) -> Tuple[bool, Optional[Path], str]:
    """Download a plugin file from Cloudflare R2 into the local persistent cache.

    Validates SHA-256 against expected_sha256 if provided.
    Falls back to bundled plugin in res/plugins/ if network or SSL fails.
    Returns:
        (success: bool, cached_path: Optional[Path], message: str)
    """
    cache_path = get_cached_plugin_path(filename)
    url = f"{R2_PLUGINS_BASE_URL}/{filename}"

    # If already cached and valid, reuse
    if cache_path.is_file():
        current_hash = calculate_sha256(cache_path)
        if expected_sha256 and current_hash == expected_sha256:
            logger.debug(f"[PluginManager] {filename} is already cached with matching hash.")
            return True, cache_path, f"{filename} already cached."

    logger.info(f"[PluginManager] Downloading {filename} from {url}...")
    content = None
    try:
        content = _http_get_bytes(url, timeout=DEFAULT_TIMEOUT)
    except Exception as dl_err:
        logger.warning(f"[PluginManager] Network download failed for {filename}: {dl_err}")

    # Validate integrity if downloaded
    if content:
        if expected_sha256:
            dl_hash = hashlib.sha256(content).hexdigest()
            if dl_hash != expected_sha256:
                err_msg = (
                    f"SHA-256 mismatch for {filename}! "
                    f"Expected {expected_sha256[:8]}, got {dl_hash[:8]}"
                )
                logger.error(f"[PluginManager] {err_msg}")
                content = None
            else:
                tmp_path = cache_path.with_suffix(".tmp")
                tmp_path.write_bytes(content)
                tmp_path.replace(cache_path)
                logger.info(f"[PluginManager] Successfully downloaded and cached {filename} ({len(content)} bytes)")
                return True, cache_path, f"Downloaded {filename} successfully."
        else:
            tmp_path = cache_path.with_suffix(".tmp")
            tmp_path.write_bytes(content)
            tmp_path.replace(cache_path)
            logger.info(f"[PluginManager] Successfully downloaded and cached {filename} ({len(content)} bytes)")
            return True, cache_path, f"Downloaded {filename} successfully."

    # Fallback to bundled plugin in res/plugins/
    try:
        bundled_file = Paths.resource(f"plugins/{filename}")
        if not (bundled_file and bundled_file.is_file()) and filename in ("spacebunny.lua", "spacebunny1.5.lua"):
            alt_name = "spacebunny.lua" if filename == "spacebunny1.5.lua" else "spacebunny1.5.lua"
            try:
                alt_file = Paths.resource(f"plugins/{alt_name}")
                if alt_file.is_file():
                    bundled_file = alt_file
            except Exception as e:
                logger.debug(f"[PluginManager] Could not check bundled fallback alias {alt_name}: {e}")
        if bundled_file and bundled_file.is_file():
            b_hash = calculate_sha256(bundled_file)
            if not expected_sha256 or b_hash == expected_sha256:
                shutil.copy2(bundled_file, cache_path)
                logger.info(f"[PluginManager] Recovered {filename} from bundled fallback resources ({bundled_file})")
                return True, cache_path, f"Using bundled fallback {filename}."
            else:
                logger.warning(
                    f"[PluginManager] Bundled {filename} hash {b_hash[:8]} does not match expected {expected_sha256[:8]}"
                )
    except Exception as b_err:
        logger.debug(f"[PluginManager] Error checking bundled fallback for {filename}: {b_err}")

    if cache_path.is_file():
        return True, cache_path, f"Using existing cached {filename}."

    return False, None, f"Failed to download or recover {filename}."


def is_plugin_installed_and_valid(filename: str, expected_sha256: Optional[str] = None) -> bool:
    """Check if the plugin is installed in the primary SLS plugins directory with matching hash."""
    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return False

    primary_file = target_dirs[0] / filename
    try:
        if not primary_file.is_file() or primary_file.stat().st_size == 0:
            return False
    except OSError as err:
        logger.debug(f"[PluginManager] Could not stat primary plugin {primary_file}: {err}")
        return False

    if expected_sha256 is None:
        info = get_manifest_plugin_info(filename)
        if info and "sha256" in info:
            expected_sha256 = info["sha256"]

    if expected_sha256:
        return calculate_sha256(primary_file) == expected_sha256

    return True


def is_plugin_check_bypassed() -> bool:
    """Check if the user has enabled 'I'm using custom plugins (Advanced)'."""
    try:
        from utils.settings import get_settings
        return get_settings().value("custom_plugins_advanced", False, type=bool)
    except Exception:
        return False


REDUNDANT_LEGACY_PLUGINS = {
    "spacebunny1.5.lua": "spacebunny.lua",
}


def archive_redundant_legacy_plugins(auto_deploy_replacement: bool = True) -> List[str]:
    """Archive any obsolete/redundant plugins (such as spacebunny1.5.lua) to .bak.

    If spacebunny1.5.lua is found in any SLSsteam plugins directory, rename it
    to spacebunny1.5.lua.bak so the canonical spacebunny.lua is used instead.
    If auto_deploy_replacement is True and the replacement is not yet present,
    it automatically deploys the replacement to ensure continuous operation.
    """
    archived = []
    try:
        target_dirs = get_sls_plugins_dirs()
        for tdir in target_dirs:
            if not tdir.is_dir():
                continue
            for legacy_name, replacement_name in REDUNDANT_LEGACY_PLUGINS.items():
                legacy_file = tdir / legacy_name
                if legacy_file.is_file():
                    bak_file = tdir / f"{legacy_name}.bak"
                    try:
                        if bak_file.is_file():
                            bak_file.unlink()
                        legacy_file.replace(bak_file)
                        archived.append(str(bak_file))
                        logger.info(
                            f"[PluginManager] Archived redundant legacy plugin {legacy_file.name} -> {bak_file.name}"
                        )
                        if auto_deploy_replacement and replacement_name:
                            repl_file = tdir / replacement_name
                            if not repl_file.is_file():
                                deploy_plugin(replacement_name, only_if_missing=True)
                    except Exception as err:
                        logger.warning(
                            f"[PluginManager] Could not archive redundant plugin {legacy_file}: {err}"
                        )
    except Exception as exc:
        logger.debug(f"[PluginManager] Error scanning for redundant legacy plugins: {exc}")
    return archived


def get_companion_plugin_filenames() -> Set[str]:
    """Return set of filenames for plugins categorized as companions (non-interceptors).

    Derives dynamically from the plugins manifest if available, falling back
    to FALLBACK_COMPANION_PLUGINS.
    """
    companions = set(FALLBACK_COMPANION_PLUGINS)
    try:
        manifest = fetch_plugins_manifest()
        if manifest and "plugins" in manifest:
            for fname, pdata in manifest["plugins"].items():
                if isinstance(pdata, dict) and pdata.get("category") == "companion":
                    companions.add(fname)
    except Exception as e:
        logger.debug(f"[PluginManager] Error deriving companion plugins from manifest: {e}")
    return companions


def is_interceptor_plugin(filename: str) -> bool:
    """Check if a plugin acts as a download interceptor (enforcing single active plugin)."""
    if filename in get_companion_plugin_filenames():
        return False
    try:
        meta = get_manifest_plugin_info(filename)
        if meta and isinstance(meta, dict) and "category" in meta:
            return meta.get("category") == "interceptor"
    except Exception as e:
        logger.debug(f"[PluginManager] Error checking interceptor category for {filename}: {e}")
    # Any custom or unlisted .lua (excluding .bak and redundant legacy aliases) is treated as an interceptor
    return (
        filename.endswith(".lua")
        and not filename.endswith(".bak")
        and not filename.startswith(".")
        and filename not in REDUNDANT_LEGACY_PLUGINS
    )


def get_active_interceptor_filename() -> Optional[str]:
    """Find the active download interceptor plugin filename in SLSsteam plugins directory."""
    try:
        archive_redundant_legacy_plugins(auto_deploy_replacement=False)
        companion_plugins = get_companion_plugin_filenames()
        target_dirs = get_sls_plugins_dirs()
        for tdir in target_dirs:
            if tdir.is_dir():
                for p in tdir.glob("*.lua"):
                    if (
                        p.is_file()
                        and not p.name.endswith(".bak")
                        and not p.name.startswith(".")
                        and p.name not in companion_plugins
                        and p.name not in REDUNDANT_LEGACY_PLUGINS
                    ):
                        return p.name
    except Exception as e:
        logger.debug(f"[PluginManager] Error detecting active interceptor: {e}")
    return None


# Alias for backward compatibility with existing callers
get_active_download_plugin = get_active_interceptor_filename


def are_all_plugins_installed(ignore_bypass: bool = False) -> bool:
    """Check if the active interceptor and spliced-tickets plugins are installed and up to date."""
    if not ignore_bypass and is_plugin_check_bypassed():
        return True

    active_interceptor = (
        get_active_interceptor_filename()
        or get_settings().value("selected_download_plugin", "")
        or get_default_interceptor_from_manifest()
    )
    if not is_plugin_installed_and_valid(active_interceptor):
        return False

    if not is_plugin_installed_and_valid("spliced-tickets.lua"):
        return False

    return True


def are_plugins_present(ignore_bypass: bool = False) -> bool:
    """Check if required SLSsteam plugins exist locally in the primary plugins directory.
    Requires at least one download interceptor (.lua) and spliced-tickets.lua.
    If 'custom_plugins_advanced' setting is enabled and ignore_bypass is False, returns True.
    """
    if not ignore_bypass and is_plugin_check_bypassed():
        return True

    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return False

    has_interceptor = False
    has_spliced = False
    companion_plugins = get_companion_plugin_filenames()
    for tdir in target_dirs:
        if not tdir.is_dir():
            continue
        for p in tdir.glob("*.lua"):
            if not p.name.endswith(".bak") and not p.name.startswith("."):
                if p.name == "spliced-tickets.lua":
                    has_spliced = True
                elif p.name not in companion_plugins:
                    has_interceptor = True

    return has_interceptor and has_spliced


def get_plugin_status(filename: str) -> str:
    """
    Returns the status of a plugin relative to the Cloud manifest:
      - 'missing': Plugin file does not exist in any SLS plugins directory.
      - 'outdated': Plugin file exists, but its SHA-256 does not match the Cloud manifest
                    (either custom/locally modified or an older version).
      - 'up_to_date': Plugin file exists and its SHA-256 matches the Cloud manifest.
    """
    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return "missing"

    found_file = None
    for tdir in target_dirs:
        candidate = tdir / filename
        if candidate.is_file():
            # Treat 0-byte stub files as missing rather than valid installations
            try:
                if candidate.stat().st_size == 0:
                    continue
            except OSError as e:
                logger.debug(f"[PluginManager] Could not stat candidate {candidate}: {e}")
                continue
            found_file = candidate
            break

    if not found_file:
        return "missing"

    info = get_manifest_plugin_info(filename)
    if not info or "sha256" not in info:
        # If manifest is unreachable or lacks sha256, but file exists, treat as installed
        return "up_to_date"

    expected_sha = str(info["sha256"]).strip().lower()
    local_sha = calculate_sha256(found_file)
    if local_sha and local_sha.strip().lower() == expected_sha:
        return "up_to_date"

    return "outdated"


# Alias for backward compatibility
get_plugin_state = get_plugin_status


def deploy_plugin(filename: str, force_download: bool = False, only_if_missing: bool = False) -> Tuple[bool, bool, str]:
    """Ensure a plugin is downloaded from R2 and deployed into SLSsteam plugin directories.

    For interceptor plugins (anything other than spliced-tickets.lua), enforces single active plugin:
    any other interceptors are safely archived as .bak, and any stale .bak of the incoming plugin is cleaned up.

    Returns:
        (success: bool, skipped: bool, message: str)
        - skipped=True if all targets already had the exact matching SHA-256 or were already present when only_if_missing is True.
    """
    ensure_sls_plugin_config_sections()

    # spliced-tickets.lua requires SmartTickets: 0x1 (SteamDRM) in SLS config
    if filename == "spliced-tickets.lua":
        ensure_smart_tickets_enabled()

    set_plugin_user_disabled(filename, False)

    if only_if_missing:
        status = get_plugin_status(filename)
        if status != "missing":
            return True, True, f"{filename} is already present; preserving local version."

    meta = get_manifest_plugin_info(filename)
    expected_sha256 = meta.get("sha256") if meta else None

    cache_path = get_cached_plugin_path(filename)
    needs_download = force_download or not cache_path.is_file()

    if not needs_download and expected_sha256:
        # Check if local cache has the expected hash
        if calculate_sha256(cache_path) != expected_sha256:
            needs_download = True

    if needs_download:
        # Check if file exists in bundled resources, local plugins cache, or known local paths first
        try:
            bundled_alt = Paths.resource(f"plugins/{filename}")
            if not (bundled_alt and bundled_alt.is_file()) and filename in ("spacebunny.lua", "spacebunny1.5.lua"):
                alt_name = "spacebunny.lua" if filename == "spacebunny1.5.lua" else "spacebunny1.5.lua"
                alt_p = Paths.resource(f"plugins/{alt_name}")
                if alt_p.is_file():
                    bundled_alt = alt_p
        except Exception:
            bundled_alt = None

        alt_locations = [
            loc for loc in [
                bundled_alt,
                get_base_path() / "plugins" / filename,
                Path.home() / ".local" / "share" / "ACCELA" / "plugins" / filename,
                Path.home() / ".config" / "SLSsteam" / "plugins" / filename,
            ] if loc is not None
        ]
        found_local = None
        for alt in alt_locations:
            if alt.is_file():
                # Prefer an exact matching hash if expected_sha256 is known
                if expected_sha256:
                    if calculate_sha256(alt) == expected_sha256:
                        found_local = alt
                        break
                elif found_local is None:
                    found_local = alt

        if found_local and found_local != cache_path:
            try:
                shutil.copy2(found_local, cache_path)
                needs_download = False
            except Exception:
                pass

        if needs_download:
            ok, downloaded_path, dl_msg = download_plugin(filename, expected_sha256)
            if not ok or not downloaded_path:
                if not cache_path.is_file():
                    return False, False, f"Failed downloading {filename}: {dl_msg}"
            else:
                cache_path = downloaded_path

    src_hash = calculate_sha256(cache_path)
    if not src_hash:
        return False, False, f"Could not compute hash for cached {filename}"

    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return False, False, "No valid SLSsteam plugin directories found."

    is_interceptor = is_interceptor_plugin(filename)
    all_matched = True
    any_deployed = False
    errors = []
    companion_plugins = get_companion_plugin_filenames()

    for tdir in target_dirs:
        try:
            tdir.mkdir(parents=True, exist_ok=True)

            if is_interceptor:
                # 1. Clean up old .bak of the incoming plugin if it exists
                incoming_bak = tdir / f"{filename}.bak"
                if incoming_bak.is_file():
                    try:
                        incoming_bak.unlink()
                        logger.debug(f"[PluginManager] Removed old backup {incoming_bak}")
                    except Exception as e:
                        logger.warning(f"[PluginManager] Could not remove old backup {incoming_bak}: {e}")

                # 2. Deactivate other active interceptor plugins:
                #    If other .bak exists, delete old .bak and replace with new .bak
                for existing in tdir.glob("*.lua"):
                    if (
                        existing.is_file()
                        and not existing.name.endswith(".bak")
                        and not existing.name.startswith(".")
                        and existing.name not in companion_plugins
                        and existing.name != filename
                    ):
                        try:
                            other_bak = tdir / f"{existing.name}.bak"
                            if other_bak.is_file():
                                other_bak.unlink()
                            existing.replace(other_bak)
                            logger.info(
                                f"[PluginManager] Deactivated alternative interceptor plugin {existing.name} -> {other_bak.name}"
                            )
                        except Exception as rem_err:
                            logger.warning(f"[PluginManager] Could not deactivate {existing.name}: {rem_err}")

            dst_file = tdir / filename
            if dst_file.is_file():
                if dst_file.stat().st_size > 0:
                    dst_hash = calculate_sha256(dst_file)
                    if dst_hash == src_hash:
                        logger.debug(f"[PluginManager] {filename} at {dst_file} matches hash {src_hash[:8]}, skipping.")
                        continue

                    # Hash mismatch: back up old plugin as .bak
                    bak_file = tdir / f"{filename}.bak"
                    try:
                        if bak_file.is_file():
                            bak_file.unlink()
                        shutil.copy2(dst_file, bak_file)
                        logger.info(
                            f"[PluginManager] Backed up old {filename} ({dst_hash[:8]} -> {bak_file})"
                        )
                    except Exception as bak_err:
                        logger.warning(f"[PluginManager] Failed creating backup {bak_file}: {bak_err}")
                else:
                    logger.info(f"[PluginManager] Overwriting 0-byte stub for {filename} at {dst_file}")

            all_matched = False
            shutil.copy2(cache_path, dst_file)
            any_deployed = True
            logger.info(f"[PluginManager] Deployed {filename} to {dst_file}")

        except Exception as exc:
            errors.append(f"{tdir}: {exc}")

    if errors:
        return False, False, f"Error deploying {filename}: {'; '.join(errors)}"

    # Record the active interceptor in user settings
    if is_interceptor:
        try:
            get_settings().setValue("selected_download_plugin", filename)
        except Exception as exc:
            logger.debug(f"[PluginManager] Could not save selected_download_plugin setting: {exc}")

    return True, False, f"Successfully deployed {filename} to SLSsteam."


def undeploy_plugin(filename: str) -> Tuple[bool, str]:
    """Remove a plugin from all SLSsteam plugin directories while preserving any .bak backup.
    Also records the plugin as user-disabled in settings so startup does not auto-redeploy it.
    """
    set_plugin_user_disabled(filename, True)
    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return False, "No valid SLSsteam plugin directories found."

    errors = []
    for tdir in target_dirs:
        try:
            target_file = tdir / filename
            if target_file.is_file():
                target_file.unlink()
                logger.info(f"[PluginManager] Removed plugin {target_file}")
        except Exception as exc:
            errors.append(f"{tdir}: {exc}")

    if errors:
        return False, f"Errors undeploying {filename}: {'; '.join(errors)}"
    return True, f"Successfully removed {filename}."


def is_plugin_user_disabled(filename: str) -> bool:
    """Check if the user has explicitly disabled this plugin in settings."""
    try:
        disabled_list = get_settings().value("disabled_plugins", [], type=list)
        return filename in (disabled_list or [])
    except Exception:
        return False


def set_plugin_user_disabled(filename: str, disabled: bool) -> None:
    """Record whether a plugin has been explicitly disabled by user."""
    try:
        settings = get_settings()
        disabled_list = list(settings.value("disabled_plugins", [], type=list) or [])
        if disabled and filename not in disabled_list:
            disabled_list.append(filename)
        elif not disabled and filename in disabled_list:
            disabled_list = [f for f in disabled_list if f != filename]
        settings.setValue("disabled_plugins", disabled_list)
        settings.sync()
    except Exception as exc:
        logger.warning(f"[PluginManager] Could not update disabled_plugins setting: {exc}")


def get_default_interceptor_from_manifest() -> str:
    """Determine the default download interceptor from R2 manifest (or cached manifest).
    Falls back to 'spacebunny.lua' if manifest cannot be read or doesn't specify.
    """
    try:
        manifest = fetch_plugins_manifest()
        if manifest:
            if manifest.get("default_interceptor"):
                return str(manifest["default_interceptor"]).strip()

            plugins = manifest.get("plugins", {})
            for fname, pdata in plugins.items():
                if isinstance(pdata, dict) and pdata.get("category") == "interceptor":
                    if pdata.get("is_default") or pdata.get("default") or pdata.get("required"):
                        return fname
    except Exception as exc:
        logger.debug(f"[PluginManager] Error resolving default interceptor from manifest: {exc}")
    return "spacebunny.lua"


def deploy_all_plugins(force_download: bool = False, only_if_missing: bool = False) -> Tuple[bool, List[str]]:
    """Deploy the selected download interceptor, spliced-tickets, and extras (if AT0-M enabled and not disabled)."""
    ensure_sls_plugin_config_sections()

    active_interceptor = (
        get_active_interceptor_filename()
        or get_settings().value("selected_download_plugin", "")
        or get_default_interceptor_from_manifest()
    )

    plugin_list = [active_interceptor, "spliced-tickets.lua"]

    results = []
    overall_ok = True
    for p in plugin_list:
        ok, skipped, msg = deploy_plugin(p, force_download=force_download, only_if_missing=only_if_missing)
        results.append(msg)
        if not ok:
            overall_ok = False

    # Extras autodeployment: only if AT0-M enabled and plugin is not user-disabled
    try:
        from utils.yaml_config_manager import is_at0m_enabled
        if is_at0m_enabled():
            for extra_plugin in ("lobster.lua", "hubcap.lua"):
                if is_plugin_user_disabled(extra_plugin):
                    logger.debug(f"[PluginManager] {extra_plugin} disabled by user, skipping deployment.")
                    continue
                status = get_plugin_status(extra_plugin)
                if status == "missing":
                    ok, skipped, msg = deploy_plugin(extra_plugin, force_download=force_download, only_if_missing=True)
                    results.append(msg)
                    if not ok:
                        overall_ok = False
                else:
                    logger.debug(f"[PluginManager] {extra_plugin} already present, preserving local version.")
        else:
            logger.debug("[PluginManager] AT0-M disabled, skipping extras deployment.")
    except Exception as e:
        logger.warning(f"[PluginManager] Error checking extras autodeploy in deploy_all_plugins: {e}")

    return overall_ok, results


def autodeploy_lobster_if_needed() -> bool:
    """Auto-deploy lobster.lua only if AT0-M mode is enabled, not user-disabled, and lobster.lua is missing."""
    try:
        from utils.yaml_config_manager import is_at0m_enabled
        if not is_at0m_enabled():
            logger.debug("[PluginManager] AT0-M mode is disabled, skipping lobster autodeploy.")
            return False

        if is_plugin_user_disabled("lobster.lua"):
            logger.debug("[PluginManager] lobster.lua disabled by user, skipping autodeploy.")
            return False

        status = get_plugin_status("lobster.lua")
        if status != "missing":
            logger.debug("[PluginManager] lobster.lua already present in plugins dir, ignoring autodeploy.")
            return False

        logger.info("[PluginManager] AT0-M enabled and lobster.lua missing. Autodeploying lobster.lua...")
        ok, skipped, msg = deploy_plugin("lobster.lua", only_if_missing=True)
        if ok:
            logger.info(f"[PluginManager] Successfully autodeployed lobster.lua: {msg}")
        else:
            logger.warning(f"[PluginManager] Failed autodeploying lobster.lua: {msg}")
        return ok
    except Exception as exc:
        logger.warning(f"[PluginManager] Exception during autodeploy_lobster_if_needed: {exc}")
        return False


def autodeploy_hubcap_if_needed() -> bool:
    """Auto-deploy hubcap.lua if AT0-M mode is enabled, not user-disabled, and hubcap.lua is missing."""
    try:
        from utils.yaml_config_manager import is_at0m_enabled
        if not is_at0m_enabled():
            return False

        if is_plugin_user_disabled("hubcap.lua"):
            return False

        status = get_plugin_status("hubcap.lua")
        if status != "missing":
            return False

        logger.info("[PluginManager] AT0-M enabled and hubcap.lua missing. Autodeploying hubcap.lua...")
        ok, skipped, msg = deploy_plugin("hubcap.lua", only_if_missing=True)
        return ok
    except Exception as exc:
        logger.warning(f"[PluginManager] Exception during autodeploy_hubcap_if_needed: {exc}")
        return False


def sync_plugins_if_enabled() -> bool:
    """Startup auto-sync disabled to prevent destructive overwriting of custom plugins."""
    logger.debug("[PluginManager] Startup plugin auto-sync is disabled to preserve custom user plugins.")
    return True


