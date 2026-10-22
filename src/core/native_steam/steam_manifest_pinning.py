"""
steam_manifest_pinning.py
=========================
Modular utility for managing ManifestIds overrides in SLSsteam config.yaml.

SLSsteam hooks the Steam client to override the manifest GID requested for
specific depots. By writing depot-to-manifest mappings under the ManifestIds
section of config.yaml, Steam will download historical or pinned versions
via download.lua, and will be locked from auto-updating those depots.
"""

import logging
import os
import re
import zipfile
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from utils.settings import get_settings
from utils.yaml_config_manager import _atomic_write, _get_section_bounds

logger = logging.getLogger(__name__)


def get_manifest_ids_with_comments(config_path: Path) -> Dict[str, Tuple[str, str]]:
    """Parse and return all current ManifestIds mappings with comments from config.yaml: {depot_id: (gid, comment)}."""
    if not config_path.exists():
        return {}

    try:
        content = config_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        logger.error(f"[ManifestPinning] Failed to read {config_path}: {e}")
        return {}

    bounds = _get_section_bounds(content, "ManifestIds")
    if not bounds:
        return {}

    _, content_start, section_end = bounds
    section_text = content[content_start:section_end]

    result: Dict[str, Tuple[str, str]] = {}
    for line in section_text.splitlines():
        comment = ""
        if "#" in line:
            parts = line.split("#", 1)
            line_part = parts[0].strip()
            comment = parts[1].strip()
        else:
            line_part = line.strip()

        if not line_part or ":" not in line_part:
            continue
        parts = line_part.split(":", 1)
        depot_id = parts[0].strip()
        gid = parts[1].strip().strip('"').strip("'")
        if depot_id.isdigit() and gid.isdigit():
            result[depot_id] = (gid, comment)

    return result


def get_manifest_ids(config_path: Path) -> Dict[str, str]:
    """Parse and return all current ManifestIds mappings from config.yaml."""
    full_map = get_manifest_ids_with_comments(config_path)
    return {d: gid for d, (gid, _) in full_map.items()}


def set_manifest_ids(
    config_path: Path,
    manifest_map: Dict[str, str],
    comment: str = "",
) -> bool:
    """
    Atomically add or update depot manifest GIDs under the ManifestIds section.
    Preserves other existing ManifestIds entries, comments, and other sections.
    """
    if not manifest_map:
        return True

    try:
        config_path = Path(config_path)
        if not config_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            content = "Plugins: yes\nManifestIds:\n"
        else:
            content = config_path.read_text(encoding="utf-8", errors="ignore")

        # Read existing ManifestIds with comments
        current_map = get_manifest_ids_with_comments(config_path)
        # Merge new mappings
        for d, gid in manifest_map.items():
            current_map[str(d)] = (str(gid), comment.strip())

        # Format new ManifestIds block preserving comments
        lines = ["ManifestIds:"]
        for d in sorted(current_map.keys(), key=lambda x: int(x) if x.isdigit() else 0):
            gid, c = current_map[d]
            suffix = f" # {c}" if c else ""
            lines.append(f"  {d}: {gid}{suffix}")
        new_block = "\n".join(lines) + "\n"

        bounds = _get_section_bounds(content, "ManifestIds")
        if bounds:
            content = content[: bounds[0]] + new_block + content[bounds[2] :]
        else:
            content = content.rstrip() + "\n\n" + new_block

        if not _atomic_write(config_path, content):
            logger.error(f"[ManifestPinning] Failed to atomic-write {config_path}")
            return False

        logger.info(
            f"[ManifestPinning] Successfully updated {len(manifest_map)} manifest ID(s) in {config_path}"
        )
        return True

    except Exception as e:
        logger.exception(f"[ManifestPinning] Error setting manifest IDs: {e}")
        return False


def remove_manifest_ids(config_path: Path, depot_ids: List[str]) -> bool:
    """Remove specific depot IDs from the ManifestIds section in config.yaml, preserving comments for remaining entries."""
    if not config_path or not depot_ids:
        return True

    try:
        config_path = Path(config_path)
        if not config_path.exists():
            return True
        content = config_path.read_text(encoding="utf-8", errors="ignore")
        bounds = _get_section_bounds(content, "ManifestIds")
        if not bounds:
            return True

        current_map = get_manifest_ids_with_comments(config_path)
        depot_set = set(str(d) for d in depot_ids)

        # Filter out requested depots
        remaining_map = {d: val for d, val in current_map.items() if d not in depot_set}

        if len(remaining_map) == len(current_map):
            return True  # Nothing changed

        if remaining_map:
            lines = ["ManifestIds:"]
            for d in sorted(remaining_map.keys(), key=lambda x: int(x) if x.isdigit() else 0):
                gid, c = remaining_map[d]
                suffix = f" # {c}" if c else ""
                lines.append(f"  {d}: {gid}{suffix}")
            new_block = "\n".join(lines) + "\n"
        else:
            new_block = "ManifestIds:\n"

        content = content[: bounds[0]] + new_block + content[bounds[2] :]
        if not _atomic_write(config_path, content):
            return False

        logger.info(f"[ManifestPinning] Removed {len(depot_ids)} depot manifest(s) from {config_path}")
        return True

    except Exception as e:
        logger.exception(f"[ManifestPinning] Error removing manifest IDs: {e}")
        return False


def resolve_pinned_manifests(game_data: Dict[str, Any], appid: str) -> Dict[str, str]:
    """
    Check if this game or job has a pinned build / manifest overrides,
    and return the mapping of {depot_id_str: manifest_gid_str}.

    Returns empty dict if the game is set to download latest unpinned version.
    """
    settings = get_settings()
    appid_str = str(appid)

    # 1. Determine if this job is pinned
    is_pinned = (
        game_data.get("pin_build") is True
        or game_data.get("_pin_build") is True
        or game_data.get("is_rollback") is True
        or settings.value(f"pin_build/{appid_str}", False, type=bool)
    )

    if not is_pinned:
        return {}

    manifest_map: Dict[str, str] = {}

    # 2. Check explicit manifest_overrides in game_data
    overrides = game_data.get("manifest_overrides") or {}
    for d, gid in overrides.items():
        if d and gid:
            manifest_map[str(d)] = str(gid)

    # 3. Check depots structure in game_data
    for depot_id, depot_info in (game_data.get("depots") or {}).items():
        if isinstance(depot_info, dict):
            gid = depot_info.get("manifest_id") or depot_info.get("gid")
            if gid and str(gid).isdigit() and str(depot_id) not in manifest_map:
                manifest_map[str(depot_id)] = str(gid)

    # 4. Check installed appmanifest acf on disk if available
    try:
        from utils.steam_acf import find_appmanifest_path, parse_acf_file
        acf_path = find_appmanifest_path(game_data, appid_str)
        if acf_path and os.path.exists(acf_path):
            acf_data = parse_acf_file(acf_path)
            for did, dinfo in (acf_data.get("AppState", {}).get("InstalledDepots") or {}).items():
                if isinstance(dinfo, dict):
                    mid = dinfo.get("manifest")
                    if mid and str(mid).isdigit() and str(did) not in manifest_map and str(did) != appid_str:
                        manifest_map[str(did)] = str(mid)
    except Exception as e:
        logger.debug(f"[ManifestPinning] ACF manifest scan fallback error: {e}")

    # 5. Check local .DepotDownloader directory for *.manifest files
    try:
        from utils.plugin_games import _resolve_install_path
        install_path = _resolve_install_path(game_data)
        if install_path:
            for sub in (".DepotDownloader", ".assella", ".accela"):
                dd_dir = os.path.join(install_path, sub)
                if os.path.isdir(dd_dir):
                    for fname in os.listdir(dd_dir):
                        m_file = re.match(r"^(\d+)_(\d+)\.manifest$", fname)
                        if m_file:
                            did, mid = m_file.group(1), m_file.group(2)
                            if did not in manifest_map and did != appid_str:
                                manifest_map[did] = mid
    except Exception as e:
        logger.debug(f"[ManifestPinning] .DepotDownloader manifest scan error: {e}")

    # 6. Supplement / discover from cached hubcap manifest bundles for this build/game
    try:
        from utils.helpers import get_base_path
        hubcap_dir = Path(get_base_path()) / "hubcap_manifests"
        if hubcap_dir.exists():
            build_id = str(
                game_data.get("buildid")
                or (settings.value(f"installed_buildid/{appid_str}", "") if settings else "")
            ).strip()
            zips = []
            if build_id:
                specific = hubcap_dir / f"accela_fetch_{appid_str}_build_{build_id}.zip"
                if specific.exists():
                    zips.append(specific)
            zips.extend(hubcap_dir.glob(f"accela_fetch_{appid_str}*.zip"))
            for z_path in zips:
                try:
                    with zipfile.ZipFile(z_path, "r") as zf:
                        for name in zf.namelist():
                            base_name = os.path.basename(name)
                            m_file = re.match(r"^(\d+)_(\d+)\.manifest$", base_name)
                            if m_file:
                                did, mid = m_file.group(1), m_file.group(2)
                                if did not in manifest_map and did != appid_str:
                                    manifest_map[did] = mid
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"[ManifestPinning] Zip manifest scan fallback error: {e}")

    if manifest_map:
        logger.info(
            f"[ManifestPinning] Resolved {len(manifest_map)} pinned manifest(s) for {appid_str}: {manifest_map}"
        )

    return manifest_map

