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
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

from utils.settings import get_settings
from utils.yaml_config_manager import _atomic_write, _get_section_bounds

logger = logging.getLogger(__name__)


def get_manifest_ids(config_path: Path) -> Dict[str, str]:
    """Parse and return all current ManifestIds mappings from config.yaml."""
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

    result: Dict[str, str] = {}
    for line in section_text.splitlines():
        line = line.split("#")[0].strip()
        if not line or ":" not in line:
            continue
        parts = line.split(":", 1)
        depot_id = parts[0].strip()
        gid = parts[1].strip().strip('"').strip("'")
        if depot_id.isdigit() and gid.isdigit():
            result[depot_id] = gid

    return result


def set_manifest_ids(
    config_path: Path,
    manifest_map: Dict[str, str],
    comment: str = "",
) -> bool:
    """
    Atomically add or update depot manifest GIDs under the ManifestIds section.
    Preserves other existing ManifestIds entries and other sections.
    """
    if not manifest_map:
        return True

    try:
        if not config_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            content = "Plugins: yes\nManifestIds:\n"
        else:
            content = config_path.read_text(encoding="utf-8", errors="ignore")

        # Read existing ManifestIds
        current_map = get_manifest_ids(config_path)
        # Merge new mappings
        for d, gid in manifest_map.items():
            current_map[str(d)] = str(gid)

        # Format new ManifestIds block
        lines = ["ManifestIds:"]
        suffix = f" # {comment}" if comment else ""
        for d in sorted(current_map.keys(), key=lambda x: int(x) if x.isdigit() else 0):
            lines.append(f"  {d}: {current_map[d]}{suffix if d in manifest_map else ''}")
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
    """Remove specific depot IDs from the ManifestIds section in config.yaml."""
    if not config_path.exists() or not depot_ids:
        return True

    try:
        content = config_path.read_text(encoding="utf-8", errors="ignore")
        bounds = _get_section_bounds(content, "ManifestIds")
        if not bounds:
            return True

        current_map = get_manifest_ids(config_path)
        depot_set = set(str(d) for d in depot_ids)

        # Filter out requested depots
        remaining_map = {d: gid for d, gid in current_map.items() if d not in depot_set}

        if len(remaining_map) == len(current_map):
            return True  # Nothing changed

        if remaining_map:
            lines = ["ManifestIds:"]
            for d in sorted(remaining_map.keys(), key=lambda x: int(x) if x.isdigit() else 0):
                lines.append(f"  {d}: {remaining_map[d]}")
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

    # 4. Fall back to Voices recommended build overrides
    if not manifest_map:
        try:
            from managers.voices_manager import VoicesManager
            rec = VoicesManager.get_instance().get_recommendation(
                appid_str, game_data.get("game_name", "")
            )
            if rec and rec.get("manifest_overrides"):
                for d, gid in rec["manifest_overrides"].items():
                    if d and gid:
                        manifest_map[str(d)] = str(gid)
        except Exception:
            pass

    if manifest_map:
        logger.info(
            f"[ManifestPinning] Resolved {len(manifest_map)} pinned manifest(s) for {appid_str}: {manifest_map}"
        )

    return manifest_map
