"""
Modular Manifest Verifier for ASSella.

Compares Hubcap's manifest modification timestamp against Steam's live build release timestamp.
All calculations take place strictly in UTC and avoid any fake/simulated fallback or local system clock dependencies.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple, Union, Dict, Any

from utils.settings import get_settings

logger = logging.getLogger(__name__)

BUFFER_SECONDS = 60  # 60 seconds clock drift buffer between Steam and Hubcap servers


def parse_utc_timestamp(val: Union[str, int, float, None]) -> Optional[datetime]:
    """
    Parses a string (ISO 8601), integer, or float timestamp into a timezone-aware UTC datetime object.
    Returns None if parsing fails or input is None/empty.
    """
    if val is None or val == "":
        return None

    try:
        # Case 1: Epoch integer or float (or numeric string)
        if isinstance(val, (int, float)):
            return datetime.fromtimestamp(float(val), tz=timezone.utc)
        
        val_str = str(val).strip()
        if val_str.isdigit():
            return datetime.fromtimestamp(float(val_str), tz=timezone.utc)

        # Case 2: ISO 8601 format string (e.g., "2026-07-21T03:49:39.407081" or "2026-07-21T03:49:39Z")
        if val_str.endswith("Z"):
            val_str = val_str[:-1] + "+00:00"

        dt = datetime.fromisoformat(val_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt

    except Exception as e:
        logger.warning(f"[ManifestVerifier] Failed to parse UTC timestamp '{val}': {e}")
        return None


def verify_hubcap_freshness(
    app_id: str,
    hubcap_status_data: Optional[Dict[str, Any]],
    steam_timeupdated: Optional[Union[str, int, float]] = None
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Compares Hubcap's file_modified date against Steam's timeupdated timestamp.

    Args:
        app_id: App ID string
        hubcap_status_data: Dictionary returned from morrenus_api.get_manifest_status()
        steam_timeupdated: Optional Steam branch timeupdated. If None, reads latest_steam_timeupdated/{appid} from QSettings.

    Returns:
        Tuple of (status_code, reason_message, debug_info)
        status_code can be:
          - "fresh": Hubcap file is newer than or equal to Steam build
          - "stale": Hubcap file is older than Steam build
          - "cannot_determine": Timestamps missing or unparseable (graceful bypass)
    """
    settings = get_settings()

    # Resolve Steam timeupdated
    if steam_timeupdated is None and settings:
        steam_timeupdated = settings.value(f"latest_steam_timeupdated/{app_id}", None)

    if not steam_timeupdated:
        logger.debug(f"[ManifestVerifier] AppID {app_id}: Steam timeupdated is missing. Cannot perform timestamp check.")
        return (
            "cannot_determine",
            "Steam release timestamp unavailable.",
            {"app_id": app_id, "reason": "missing_steam_timestamp"}
        )

    steam_dt = parse_utc_timestamp(steam_timeupdated)
    if not steam_dt:
        logger.warning(f"[ManifestVerifier] AppID {app_id}: Could not parse Steam timeupdated '{steam_timeupdated}'.")
        return (
            "cannot_determine",
            "Invalid Steam timestamp.",
            {"app_id": app_id, "raw_steam_timeupdated": steam_timeupdated}
        )

    # Resolve Hubcap file_modified
    if not hubcap_status_data or not isinstance(hubcap_status_data, dict):
        logger.debug(f"[ManifestVerifier] AppID {app_id}: Hubcap status data is missing.")
        return (
            "cannot_determine",
            "Hubcap status data unavailable.",
            {"app_id": app_id, "reason": "missing_hubcap_data"}
        )

    file_modified_raw = hubcap_status_data.get("file_modified")
    if not file_modified_raw:
        logger.debug(f"[ManifestVerifier] AppID {app_id}: Hubcap file_modified is missing in status response.")
        return (
            "cannot_determine",
            "Hubcap file modification timestamp missing.",
            {"app_id": app_id, "hubcap_response": hubcap_status_data}
        )

    hubcap_dt = parse_utc_timestamp(file_modified_raw)
    if not hubcap_dt:
        logger.warning(f"[ManifestVerifier] AppID {app_id}: Could not parse Hubcap file_modified '{file_modified_raw}'.")
        return (
            "cannot_determine",
            "Invalid Hubcap timestamp.",
            {"app_id": app_id, "raw_file_modified": file_modified_raw}
        )

    # Calculate time difference
    # Diff > 0 means Steam updated AFTER Hubcap file was modified (stale)
    diff_seconds = (steam_dt - hubcap_dt).total_seconds()
    
    debug_info = {
        "app_id": app_id,
        "steam_utc": steam_dt.isoformat(),
        "hubcap_utc": hubcap_dt.isoformat(),
        "diff_seconds": diff_seconds,
        "buffer_seconds": BUFFER_SECONDS,
    }

    if diff_seconds > BUFFER_SECONDS:
        reason = f"Hubcap manifest ({hubcap_dt.strftime('%Y-%m-%d %H:%M UTC')}) is older than Steam release ({steam_dt.strftime('%Y-%m-%d %H:%M UTC')})."
        logger.info(f"[ManifestVerifier] AppID {app_id}: STALE — {reason}")
        return ("stale", reason, debug_info)
    else:
        reason = f"Hubcap manifest ({hubcap_dt.strftime('%Y-%m-%d %H:%M UTC')}) is up-to-date with Steam release ({steam_dt.strftime('%Y-%m-%d %H:%M UTC')})."
        logger.info(f"[ManifestVerifier] AppID {app_id}: FRESH — {reason}")
        return ("fresh", reason, debug_info)


def verify_extracted_zip_manifest(app_id: str, parsed_zip_data: dict, is_update: bool = False) -> Tuple[bool, str]:
    """
    Stage 2 Verification:
    Compares the manifest IDs inside an extracted zip archive against Steam's latest manifest ID
    and the locally installed depot file manifest IDs.

    Args:
        app_id: App ID string
        parsed_zip_data: Dictionary returned from ProcessZipTask().run(zip_path)
        is_update: Whether this operation is an expected game update (True) or a verify/repair (False)

    Returns:
        Tuple of (is_valid: bool, warning_reason: str)
    """
    if not parsed_zip_data:
        return (False, "Parsed zip data is empty or invalid.")

    settings = get_settings()
    appid_str = str(app_id)
    latest_steam_id = settings.value(f"latest_steam_manifest_id/{appid_str}", "", type=str) if settings else ""

    # Read old installed manifest IDs from depots/{appid}.depot
    from utils.helpers import get_base_path
    depot_file = get_base_path() / "depots" / f"{appid_str}.depot"
    old_manifest_ids = set()
    if depot_file.exists():
        try:
            with open(depot_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or ":" not in line:
                        continue
                    parts = line.split(":", 2)
                    if len(parts) >= 2:
                        old_manifest_ids.add(parts[1].strip())
        except Exception as e:
            logger.error(f"[ManifestVerifier] Error reading old depot file for comparison: {e}")

    # Extract new manifest IDs from parsed zip data
    new_manifests = parsed_zip_data.get("manifests", {})
    new_manifest_ids = set(new_manifests.values())

    # Check 1: If we have a known latest Steam manifest ID, it MUST be present in the new zip
    if latest_steam_id and latest_steam_id not in new_manifest_ids:
        reason = f"The downloaded manifest does not contain the latest Steam manifest ID ({latest_steam_id})."
        logger.warning(f"[ManifestVerifier] AppID {appid_str}: Stage 2 STALE — {reason}")
        return (False, reason)

    # Check 2: If all new manifest IDs are identical to the old ones (and there are old ones)
    # ONLY warn if an update was expected (is_update is True). When verifying/repairing, identical manifests are expected!
    if is_update and old_manifest_ids and new_manifest_ids.issubset(old_manifest_ids):
        reason = "The downloaded manifest is identical to the currently installed version."
        logger.warning(f"[ManifestVerifier] AppID {appid_str}: Stage 2 STALE — {reason}")
        return (False, reason)

    logger.info(f"[ManifestVerifier] AppID {appid_str}: Stage 2 FRESH — Extracted zip manifest matches expected Steam build.")
    return (True, "")
