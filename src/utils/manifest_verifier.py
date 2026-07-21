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
