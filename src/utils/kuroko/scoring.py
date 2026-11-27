"""Endpoint cooldown management and benchmark score persistence in QSettings."""

import json
import logging
import sys
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# In-memory cooldown tracker for failed stealth endpoints: endpoint_id -> timestamp
_endpoint_cooldowns: dict[str, float] = {}
COOLDOWN_DURATION = 180.0  # 3 minutes
_DEFAULT_COOLDOWN_DURATION = COOLDOWN_DURATION


def _get_cooldown_duration() -> float:
    if COOLDOWN_DURATION != _DEFAULT_COOLDOWN_DURATION:
        return COOLDOWN_DURATION
    pkg = sys.modules.get("utils.kuroko")
    if pkg is not None:
        return float(getattr(pkg, "COOLDOWN_DURATION", COOLDOWN_DURATION))
    return COOLDOWN_DURATION


def is_endpoint_in_cooldown(endpoint_id: str) -> bool:
    """Check if an endpoint is temporarily penalized due to recent errors."""
    now = time.time()
    last_err = _endpoint_cooldowns.get(endpoint_id)
    if last_err is None:
        return False
    if now - last_err > _get_cooldown_duration():
        _endpoint_cooldowns.pop(endpoint_id, None)
        return False
    return True


def mark_endpoint_cooldown(endpoint_id: str) -> None:
    """Mark an endpoint in cooldown for COOLDOWN_DURATION seconds."""
    duration = _get_cooldown_duration()
    _endpoint_cooldowns[endpoint_id] = time.time()
    logger.warning(f"[Kuroko] Endpoint '{endpoint_id}' placed in cooldown for {int(duration)}s.")


def clear_endpoint_cooldowns() -> None:
    """Clear all active endpoint cooldown penalties."""
    _endpoint_cooldowns.clear()


def save_endpoint_score(
    endpoint_id: str,
    ping_ms: int,
    speed_mbps: float,
    ok: bool,
    settings=None,
    exit_label: str = "",
) -> None:
    """Persist benchmark metrics for a candidate endpoint in QSettings."""
    if not endpoint_id:
        return
    if settings is None:
        from utils.settings import get_settings

        settings = get_settings()
    try:
        raw = settings.value("stealth_endpoint_scores", "{}", type=str)
        scores = json.loads(raw) if raw else {}
    except Exception as e:
        logger.debug(f"[Kuroko] Failed to load endpoint scores: {e}")
        scores = {}

    entry = {
        "ping_ms": ping_ms,
        "speed_mbps": speed_mbps,
        "ok": ok,
        "timestamp": int(time.time()),
    }
    if exit_label:
        entry["exit_label"] = exit_label
    elif endpoint_id in scores and "exit_label" in scores[endpoint_id]:
        entry["exit_label"] = scores[endpoint_id]["exit_label"]

    scores[endpoint_id] = entry
    try:
        settings.setValue("stealth_endpoint_scores", json.dumps(scores))
    except Exception as e:
        logger.debug(f"[Kuroko] Failed to persist endpoint scores: {e}")


def get_endpoint_scores(settings=None) -> Dict[str, Dict[str, Any]]:
    """Return all cached endpoint benchmark scores."""
    if settings is None:
        from utils.settings import get_settings

        settings = get_settings()
    try:
        raw = settings.value("stealth_endpoint_scores", "{}", type=str)
        return json.loads(raw) if raw else {}
    except Exception as e:
        logger.debug(f"[Kuroko] Failed to read cached endpoint scores: {e}")
        return {}


def safe_settings_string_list(settings, key: str, default: Optional[list] = None) -> list[str]:
    """Safely retrieve a list of strings from QSettings across versions and platforms."""
    raw = settings.value(key, default or [])
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        if raw.startswith("[") and raw.endswith("]"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except Exception as e:
                logger.debug(f"[Kuroko] Failed to parse settings string list for key '{key}': {e}")
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _safe_wg_profiles(settings) -> list[str]:
    """Safely extract the list of WireGuard profile filenames from QSettings."""
    return safe_settings_string_list(settings, "stealth_pool_wg_profiles", [])


__all__ = [
    "COOLDOWN_DURATION",
    "_endpoint_cooldowns",
    "_safe_wg_profiles",
    "clear_endpoint_cooldowns",
    "get_endpoint_scores",
    "is_endpoint_in_cooldown",
    "mark_endpoint_cooldown",
    "safe_settings_string_list",
    "save_endpoint_score",
]