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

    except urllib.error.HTTPError as he:
        err = f"HTTP Error {he.code} downloading {filename} from R2"
        logger.error(f"[PluginManager] {err}")
        return False, None, err
    except urllib.error.URLError as ue:
        err = f"Network connection error downloading {filename}: {ue.reason}"
        logger.error(f"[PluginManager] {err}")
        return False, None, err
    except Exception as exc:
        err = f"Unexpected error downloading {filename}: {exc}"
        logger.error(f"[PluginManager] {err}", exc_info=True)
        return False, None, err


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


def is_plugin_check_bypassed() -> bool:
    """Check if the user has enabled 'I'm using custom plugins (Advanced)'."""
    try:
        from utils.settings import get_settings
        return get_settings().value("custom_plugins_advanced", False, type=bool)
    except Exception:
        return False


def get_active_interceptor_filename() -> Optional[str]:
    """Find the active download interceptor plugin filename in SLSsteam plugins directory."""
    try:
        target_dirs = get_sls_plugins_dirs()
        for tdir in target_dirs:
            if tdir.is_dir():
                for p in tdir.glob("*.lua"):
                    if (
                        p.is_file()
                        and not p.name.endswith(".bak")
                        and not p.name.startswith(".")
                        and p.name != "spliced-tickets.lua"
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

    active_interceptor = get_active_interceptor_filename() or get_settings().value("selected_download_plugin", "download.lua")
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
    for tdir in target_dirs:
        if not tdir.is_dir():
            continue
        for p in tdir.glob("*.lua"):
            if not p.name.endswith(".bak") and not p.name.startswith("."):
                if p.name == "spliced-tickets.lua":
                    has_spliced = True
                else:
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


def deploy_plugin(filename: str, force_download: bool = False, only_if_missing: bool = False) -> Tuple[bool, bool, str]:
    """Ensure a plugin is downloaded from R2 and deployed into SLSsteam plugin directories.

    For interceptor plugins (anything other than spliced-tickets.lua), enforces single active plugin:
    any other interceptors are safely archived as .bak, and any stale .bak of the incoming plugin is cleaned up.

    Returns:
        (success: bool, skipped: bool, message: str)
        - skipped=True if all targets already had the exact matching SHA-256 or were already present when only_if_missing is True.
    """
    ensure_plugins_enabled()

    # spliced-tickets.lua requires SmartTickets: 0x1 (SteamDRM) in SLS config
    if filename == "spliced-tickets.lua":
        ensure_smart_tickets_enabled()

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
        # Check if file exists in local plugins cache or known local paths first
        alt_locations = [
            get_base_path() / "plugins" / filename,
            Path.home() / ".local" / "share" / "ACCELA" / "plugins" / filename,
            Path.home() / ".config" / "SLSsteam" / "plugins" / filename,
        ]
        # Also check for spacebunnny spelling variations if looking for spacebunny
        if filename == "spacebunny1.5.lua":
            alt_locations.extend([
                Path.home() / ".local" / "share" / "ACCELA" / "plugins" / "spacebunnny1.5.lua",
                Path.home() / ".config" / "SLSsteam" / "plugins" / "spacebunnny1.5beta.lua",
            ])

        found_local = None
        for alt in alt_locations:
            if alt.is_file():
                found_local = alt
                break
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

    is_interceptor = (filename != "spliced-tickets.lua")
    all_matched = True
    any_deployed = False
    errors = []

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
                        and existing.name != "spliced-tickets.lua"
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
        except Exception:
            pass

    if all_matched and not any_deployed:
        return True, True, f"{filename} is already up to date (SHA-256 matched)."

    return True, False, f"Successfully deployed {filename} to SLSsteam."


def deploy_all_plugins(force_download: bool = False, only_if_missing: bool = False) -> Tuple[bool, List[str]]:
    """Deploy the selected download interceptor and spliced-tickets plugins."""
    ensure_plugins_enabled()

    active_interceptor = (
        get_active_interceptor_filename()
        or get_settings().value("selected_download_plugin", "download.lua")
        or "download.lua"
    )

    plugin_list = [active_interceptor, "spliced-tickets.lua"]

    results = []
    overall_ok = True
    for p in plugin_list:
        ok, skipped, msg = deploy_plugin(p, force_download=force_download, only_if_missing=only_if_missing)
        results.append(msg)
        if not ok:
            overall_ok = False

    return overall_ok, results


def sync_plugins_if_enabled() -> bool:
    """Startup auto-sync disabled to prevent destructive overwriting of custom plugins."""
    logger.debug("[PluginManager] Startup plugin auto-sync is disabled to preserve custom user plugins.")
    return True
