"""Plugin Manager for on-demand SLSsteam Lua plugin distribution from Cloudflare R2.

Handles fetching plugins_manifest.json, validating SHA-256 hashes, downloading
on-demand to local cache (~/.local/share/ACCELA/plugins), and deploying into
active SLSsteam plugin directories with automatic .bak backup preservation.
"""

import hashlib
import json
import logging
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.helpers import get_base_path
from utils.settings import get_settings
from utils.yaml_config_manager import (
    ensure_plugins_enabled,
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
    "download.lua",
    "spliced-tickets.lua",
]


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


def fetch_plugins_manifest(force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    """Fetch plugins_manifest.json from Cloud, falling back to local cache."""
    cached_path = get_cached_manifest_path()

    if not force_refresh and cached_path.is_file():
        try:
            with open(cached_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as read_err:
            logger.error(f"[PluginManager] Error reading cached manifest: {read_err}")

    try:
        logger.debug(f"[PluginManager] Fetching manifest from {MANIFEST_URL}")
        req = urllib.request.Request(
            MANIFEST_URL,
            headers={"User-Agent": HTTP_USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            if resp.status == 200:
                raw = resp.read()
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

    return None


def get_manifest_plugin_info(filename: str) -> Optional[Dict[str, Any]]:
    """Get metadata for a specific plugin from the manifest."""
    manifest = fetch_plugins_manifest()
    if manifest and "plugins" in manifest:
        return manifest["plugins"].get(filename)
    return None


def download_plugin(
    filename: str, expected_sha256: Optional[str] = None
) -> Tuple[bool, Optional[Path], str]:
    """Download a plugin file from Cloudflare R2 into the local persistent cache.

    Validates SHA-256 against expected_sha256 if provided.
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
    try:
        req = urllib.request.Request(url, headers={"User-Agent": HTTP_USER_AGENT})
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            if resp.status != 200:
                return False, None, f"HTTP {resp.status} fetching {filename} from R2."
            content = resp.read()

        # Validate integrity
        if expected_sha256:
            dl_hash = hashlib.sha256(content).hexdigest()
            if dl_hash != expected_sha256:
                err_msg = (
                    f"SHA-256 mismatch for {filename}! "
                    f"Expected {expected_sha256[:8]}, got {dl_hash[:8]}"
                )
                logger.error(f"[PluginManager] {err_msg}")
                return False, None, err_msg

        # Atomic write to cache
        tmp_path = cache_path.with_suffix(".tmp")
        tmp_path.write_bytes(content)
        tmp_path.replace(cache_path)

        logger.info(f"[PluginManager] Successfully downloaded and cached {filename} ({len(content)} bytes)")
        return True, cache_path, f"Downloaded {filename} successfully."

    except Exception as exc:
        err = f"Error downloading {filename} from R2: {exc}"
        logger.warning(f"[PluginManager] {err}. Attempting bundled fallback...")
        bundled = get_bundled_plugin_path(filename)
        if bundled and bundled.is_file():
            try:
                shutil.copy2(bundled, cache_path)
                logger.info(f"[PluginManager] Copied bundled fallback for {filename} to cache.")
                return True, cache_path, f"Used bundled {filename}."
            except Exception as b_err:
                logger.error(f"[PluginManager] Failed copying bundled {filename}: {b_err}")
        return False, None, err


def get_bundled_plugin_path(filename: str) -> Optional[Path]:
    """Return path to bundled plugin asset in src/res/plugins if it exists."""
    try:
        p = Path(__file__).resolve().parents[1] / "res" / "plugins" / filename
        if p.is_file():
            return p
    except Exception:
        pass
    return None



def is_plugin_installed_and_valid(filename: str, expected_sha256: Optional[str] = None) -> bool:
    """Check if the plugin is installed in the primary SLS plugins directory with matching hash."""
    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return False

    primary_file = target_dirs[0] / filename
    if not primary_file.is_file():
        return False

    if expected_sha256 is None:
        info = get_manifest_plugin_info(filename)
        if info and "sha256" in info:
            expected_sha256 = info["sha256"]

    if expected_sha256:
        return calculate_sha256(primary_file) == expected_sha256

    return True


def are_all_plugins_installed() -> bool:
    """Check if all required plugins are installed and up to date in SLSsteam."""
    manifest = fetch_plugins_manifest()
    if manifest and "plugins" in manifest:
        required = [
            fname
            for fname, meta in manifest["plugins"].items()
            if meta.get("required", True)
        ]
    else:
        required = FALLBACK_REQUIRED_PLUGINS

    for p in required:
        if not is_plugin_installed_and_valid(p):
            return False
    return True


def are_plugins_present() -> bool:
    """Check if all required SLSsteam plugins exist locally in the primary plugins directory."""
    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return False
    for fname in FALLBACK_REQUIRED_PLUGINS:
        found = False
        for tdir in target_dirs:
            if (tdir / fname).is_file():
                found = True
                break
        if not found:
            return False
    return True


def deploy_plugin(filename: str, force_download: bool = False) -> Tuple[bool, bool, str]:
    """Ensure a plugin is downloaded from R2 and deployed into SLSsteam plugin directories.

    Returns:
        (success: bool, skipped: bool, message: str)
        - skipped=True if all targets already had the exact matching SHA-256.
    """
    ensure_plugins_enabled()

    meta = get_manifest_plugin_info(filename)
    expected_sha256 = meta.get("sha256") if meta else None

    cache_path = get_cached_plugin_path(filename)
    needs_download = force_download or not cache_path.is_file()

    if not needs_download and expected_sha256:
        # Check if local cache has the expected hash
        if calculate_sha256(cache_path) != expected_sha256:
            needs_download = True

    if needs_download:
        ok, downloaded_path, dl_msg = download_plugin(filename, expected_sha256)
        if not ok or not downloaded_path:
            return False, False, f"Failed downloading {filename}: {dl_msg}"
        cache_path = downloaded_path

    src_hash = calculate_sha256(cache_path)
    if not src_hash:
        return False, False, f"Could not compute hash for cached {filename}"

    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return False, False, "No valid SLSsteam plugin directories found."

    all_matched = True
    any_deployed = False
    errors = []

    for tdir in target_dirs:
        try:
            tdir.mkdir(parents=True, exist_ok=True)
            dst_file = tdir / filename
            if dst_file.is_file():
                dst_hash = calculate_sha256(dst_file)
                if dst_hash == src_hash:
                    logger.debug(f"[PluginManager] {filename} at {dst_file} matches hash {src_hash[:8]}, skipping.")
                    continue

                # Hash mismatch: back up old plugin as .bak
                bak_file = tdir / f"{filename}.bak"
                try:
                    shutil.copy2(dst_file, bak_file)
                    logger.info(
                        f"[PluginManager] Backed up old {filename} ({dst_hash[:8]} -> {bak_file})"
                    )
                except Exception as bak_err:
                    logger.warning(f"[PluginManager] Failed creating backup {bak_file}: {bak_err}")

            all_matched = False
            shutil.copy2(cache_path, dst_file)
            any_deployed = True
            logger.info(f"[PluginManager] Deployed {filename} to {dst_file}")

        except Exception as exc:
            errors.append(f"{tdir}: {exc}")

    if errors:
        return False, False, f"Error deploying {filename}: {'; '.join(errors)}"

    if all_matched and not any_deployed:
        return True, True, f"{filename} is already up to date (SHA-256 matched)."

    return True, False, f"Successfully deployed {filename} to SLSsteam."


def deploy_all_plugins(force_download: bool = False) -> Tuple[bool, List[str]]:
    """Deploy all required SLSsteam plugins from Cloudflare R2 on demand."""
    ensure_plugins_enabled()

    manifest = fetch_plugins_manifest(force_refresh=force_download)
    if manifest and "plugins" in manifest:
        plugin_list = [
            fname
            for fname, meta in manifest["plugins"].items()
            if meta.get("required", True)
        ]
    else:
        plugin_list = FALLBACK_REQUIRED_PLUGINS

    results = []
    overall_ok = True
    for p in plugin_list:
        ok, skipped, msg = deploy_plugin(p, force_download=force_download)
        results.append(msg)
        if not ok:
            overall_ok = False

    return overall_ok, results


def sync_plugins_if_enabled() -> bool:
    """Sync SLSsteam plugins on startup if plugins are enabled by the user."""
    if not is_slssteam_plugins_enabled():
        logger.debug("[PluginManager] SLS plugins are not enabled; skipping sync.")
        return True

    logger.info("[PluginManager] SLS plugins enabled. Checking and syncing from Cloudflare R2...")
    overall_ok, results = deploy_all_plugins(force_download=False)
    for res in results:
        logger.info(f"[PluginManager] {res}")
    return overall_ok
