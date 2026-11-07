"""Auto-tune helper for DepotDownloader chunk concurrency.

The UI slider (`max_downloads` / `workshop_max_downloads`, range 1-30) maps
directly to DepotDownloader's `-max-downloads` flag, i.e. how many chunks are
fetched concurrently. More is not always faster: the optimum shifts with
bandwidth, latency, CPU (decompression/checksums), disk speed and Steam CDN
throttling.

This module implements a small, conservative AIMD (additive-increase /
multiplicative-decrease, TCP-style) controller:

* When the "Auto-tune" toggle is OFF, callers use the slider value exactly
  (legacy behaviour, unchanged).
* When ON, the slider acts as a *cap*. Downloads start at the best
  previously observed concurrency (persisted in QSettings, tracked
  separately for LanCache vs direct connections) and are adjusted only
  *between* depots / workshop items — never mid-download, since
  DepotDownloader offers no live concurrency control.

Speed samples taken during validation-only phases must be excluded by the
caller (validation reports ~0 B/s and would otherwise drag the estimate
down); `decide_next` also treats near-zero samples as "no data" and holds.
"""

import logging
import os

logger = logging.getLogger(__name__)

MIN_CONCURRENCY = 2
MAX_CONCURRENCY = 30
STEP = 2
DEFAULT_START = 8
# Below ~1 KB/s we treat the sample as "no real download happened"
# (e.g. validation-only depot, fully cached files).
SPEED_EPS_BPS = 1024.0
# Learning gate: a depot/item must move at least this many wire bytes for
# its sample to train the controller. Validation-only passes (0 bytes,
# gigabytes/sec of local checksums) must never become the baseline —
# otherwise every real download reads as "degraded" and N ratchets to floor.
MIN_WIRE_BYTES_FOR_LEARNING = 32 * 1_048_576
MIN_SECONDS_FOR_LEARNING = 8.0
# v2 keys: the v1 baseline was trained on progress-bar speed (which counts
# bytes reused from disk) and is ignored.
TABLE_VERSION = "v2"
EWMA_ALPHA = 0.4
REPROBE_EVERY = 6      # re-test the neighbours of the best N every few samples
CLEARLY_BETTER = 1.04  # a step must beat the best by this much to win
IMPROVE_FACTOR = 1.05
DEGRADE_FACTOR = 0.80

# Legacy (pre-connection) best-key names, removed on first read so a
# poisoned global baseline cannot leak into per-connection tuning.
_LEGACY_BEST_KEYS = (
    "auto_tune_best_lan", "auto_tune_best_mbps_lan",
    "auto_tune_best_wan", "auto_tune_best_mbps_wan",
    "workshop_auto_tune_best_lan", "workshop_auto_tune_best_mbps_lan",
    "workshop_auto_tune_best_wan", "workshop_auto_tune_best_mbps_wan",
)


def clamp(value: int, low: int, high: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = low
    return max(low, min(high, value))


def read_int(settings, key: str, default: int) -> int:
    try:
        return int(settings.value(key, default, type=int))
    except (TypeError, ValueError, AttributeError):
        return default


def read_bool(settings, key: str, default: bool) -> bool:
    try:
        return bool(settings.value(key, default, type=bool))
    except (TypeError, ValueError, AttributeError):
        return default


def read_float(settings, key: str, default: float) -> float:
    try:
        return float(settings.value(key, default, type=float))
    except (TypeError, ValueError, AttributeError):
        return default


def is_auto_enabled(settings, auto_key: str = "auto_max_downloads") -> bool:
    """True when the Auto-tune toggle is on (defaults to ON)."""
    if settings is None:
        return False
    return read_bool(settings, auto_key, True)


def get_cap(settings, cap_key: str = "max_downloads", default: int = 8) -> int:
    """Read the slider value (used as exact N when auto is off, cap when on)."""
    if settings is None:
        return default
    return clamp(read_int(settings, cap_key, default), 1, MAX_CONCURRENCY)


def should_learn(wire_bytes: float, seconds: float = None) -> bool:
    """True when a sample is long and large enough to say something about N.

    Short bursts are dominated by connection ramp-up and latency, so they
    neither prove nor disprove a concurrency level.
    """
    try:
        if float(wire_bytes) < MIN_WIRE_BYTES_FOR_LEARNING:
            return False
        return seconds is None or float(seconds) >= MIN_SECONDS_FOR_LEARNING
    except (TypeError, ValueError):
        return False


def _network_fingerprint() -> str:
    """Short stable id for the current network path, no external tools.

    Uses the default-route interface type (wl/eth) plus the gateway MAC's
    last 3 bytes from the kernel ARP table — stable per network, changes
    when the user moves to a new connection. Falls back to 'nonet'.
    """
    try:
        iface = None
        with open("/proc/net/route", "r") as f:
            for line in f.read().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 8 and parts[1] == "00000000" and int(parts[3], 16) & 0x2:
                    iface = parts[0]
                    gw_hex = parts[2]
                    break
        if not iface:
            return "nonet"
        kind = "wl"
        try:
            import os as _os
            kind = "wl" if _os.path.isdir(f"/sys/class/net/{iface}/wireless") else "eth"
        except Exception as _silent_err:
            logger.debug("[auto_tune] _network_fingerprint: ignored %s", _silent_err, exc_info=False)
        mac_tail = "nogw"
        try:
            gw_ip = ".".join(str(int(gw_hex[i:i + 2], 16)) for i in (6, 4, 2, 0))
            with open("/proc/net/arp", "r") as f:
                for line in f.read().splitlines()[1:]:
                    cells = line.split()
                    if len(cells) >= 4 and cells[0] == gw_ip and len(cells[3]) == 17:
                        mac_tail = cells[3].replace(":", "")[-6:].lower()
                        break
        except Exception as _silent_err:
            logger.debug("[auto_tune] _network_fingerprint: ignored %s", _silent_err, exc_info=False)
        return f"{kind}-{mac_tail}"
    except Exception:
        return "nonet"


def get_storage_tag(dest_path: str = "") -> str:
    """Classify destination storage into 'sdcard' (removable/external) vs 'internal'."""
    if not dest_path:
        return "internal"
    try:
        norm = os.path.abspath(dest_path)
        if norm.startswith(("/run/media/", "/media/", "/mnt/")):
            return "sdcard"
    except Exception as _silent_err:
        logger.debug("[auto_tune] get_storage_tag: ignored %s", _silent_err, exc_info=False)
    return "internal"


def get_connection_id(settings, dest_path: str = "") -> str:
    """Tuning scope id: lan/wan flag + network fingerprint + storage tag.

    Each physical connection and storage medium learns its own best concurrency,
    so a slow microSD card does not poison the baseline of an internal NVMe SSD.
    """
    lan = read_bool(settings, "use_lancache", True) if settings is not None else False
    storage_tag = get_storage_tag(dest_path)
    return f"{'lan' if lan else 'wan'}-{_network_fingerprint()}_{storage_tag}"


def _purge_legacy_keys(settings) -> None:
    """One-time removal of pre-connection best keys (idempotent)."""
    if settings is None:
        return
    try:
        for key in _LEGACY_BEST_KEYS:
            try:
                settings.remove(key)
            except Exception as _silent_err:
                logger.debug("[auto_tune] _purge_legacy_keys: ignored %s", _silent_err, exc_info=False)
    except Exception as _silent_err:
        logger.debug("[auto_tune] _purge_legacy_keys: ignored %s", _silent_err, exc_info=False)


def _best_keys(base_best_key: str, settings, dest_path: str = "", lancache_key: str = "use_lancache"):
    """Settings key of the per-N speed table for this connection and storage medium."""
    _purge_legacy_keys(settings)
    conn = get_connection_id(settings, dest_path=dest_path)
    return f"{base_best_key}_{TABLE_VERSION}_{conn}"


def _load_table(settings, key: str) -> dict:
    import json

    try:
        raw = settings.value(key, "", type=str) if settings is not None else ""
        data = json.loads(raw) if raw else {}
        stats = {int(n): v for n, v in (data.get("stats") or {}).items()}
        return {"stats": stats, "samples": int(data.get("samples", 0))}
    except Exception:
        return {"stats": {}, "samples": 0}


def _save_table(settings, key: str, table: dict) -> None:
    import json

    if settings is None:
        return
    try:
        settings.setValue(key, json.dumps({"stats": {str(n): v for n, v in table["stats"].items()},
                                           "samples": table["samples"]}))
        settings.sync()
    except Exception as e:
        logger.debug(f"[AutoTune] Failed to persist table: {e}")


def _score(entry: dict) -> float:
    """Speed, discounted for Ns that keep producing chunk errors."""
    if not entry or entry.get("count", 0) <= 0:
        return 0.0
    err_rate = entry.get("errors", 0) / max(1, entry.get("count", 1))
    return float(entry.get("mbps", 0.0)) * (1.0 - min(0.5, 0.25 * err_rate))


def _best_n(stats: dict, default: int) -> int:
    scored = [(n, _score(e)) for n, e in stats.items() if e.get("count", 0) > 0]
    if not scored:
        return default
    top = max(sc for _, sc in scored)
    # Within measurement noise, prefer the N nearest the default: fewer
    # connections risk stalls, more just add load for no gain.
    near = [n for n, sc in scored if sc >= top * 0.97]
    return min(near, key=lambda n: (abs(n - DEFAULT_START), n))


def get_start_concurrency(
    settings,
    cap_key: str = "max_downloads",
    auto_key: str = "auto_max_downloads",
    best_key: str = "auto_tune_best",
    default_start: int = DEFAULT_START,
    default_cap: int = 8,
    dest_path: str = "",
) -> int:
    """Concurrency for the first depot of a job.

    Auto OFF -> slider value exactly. Auto ON -> the fastest N measured on this
    connection + storage so far, clamped to [MIN_CONCURRENCY, cap].
    """
    cap = get_cap(settings, cap_key, default_cap)
    if not is_auto_enabled(settings, auto_key):
        return cap
    table = _load_table(settings, _best_keys(best_key, settings, dest_path=dest_path))
    start = min(cap, _best_n(table["stats"], default_start))
    if cap >= MIN_CONCURRENCY:
        start = max(MIN_CONCURRENCY, start)
    return start


def get_learned_display(settings, best_key: str = "auto_tune_best", dest_path: str = "") -> str:
    """Learned concurrency (e.g. '12') or 'learning...' before the first real sample."""
    if settings is None:
        return "learning..."
    table = _load_table(settings, _best_keys(best_key, settings, dest_path=dest_path))
    if not table["stats"]:
        return "learning..."
    return str(clamp(_best_n(table["stats"], DEFAULT_START), 1, MAX_CONCURRENCY))


def decide_next(table: dict, used_n: int, speed_bps: float, cap: int, errors: int = 0) -> int:
    """Update the per-N table with one sample and pick the next N.

    Hill-climb on measured speed per N: try one step up; if that is not
    clearly faster, try one step down; otherwise settle on the best and
    re-check its neighbours every few samples (networks change). Chunk
    errors halve N at once and count against that N.
    """
    cap = clamp(cap, 1, MAX_CONCURRENCY)
    floor = MIN_CONCURRENCY if cap >= MIN_CONCURRENCY else 1
    used_n = clamp(used_n, 1, cap)
    stats = table["stats"]
    if float(speed_bps) <= SPEED_EPS_BPS:
        # No usable measurement (failed run / nothing downloaded).
        if errors > 0:
            en = stats.get(used_n)
            if en:
                en["errors"] = en.get("errors", 0) + 1
            return max(floor, used_n // 2)
        return used_n
    e = stats.setdefault(used_n, {"mbps": 0.0, "count": 0, "errors": 0})
    mbps = float(speed_bps) / 1_048_576
    e["mbps"] = mbps if e["count"] == 0 else EWMA_ALPHA * mbps + (1 - EWMA_ALPHA) * e["mbps"]
    e["count"] += 1
    if errors > 0:
        e["errors"] = e.get("errors", 0) + 1
    table["samples"] += 1

    if errors > 0 and errors >= 3:
        return max(floor, used_n // 2)

    best = min(cap, _best_n(stats, used_n))
    best_score = _score(stats.get(best, {}))
    # Never explore below 4 on our own: too few connections stall on one slow chunk.
    up, down = min(cap, best + STEP), max(min(cap, max(floor, 4)), best - STEP)

    def _untested_or_stale(n):
        en = stats.get(n)
        return en is None or (table["samples"] % REPROBE_EVERY == 0)

    # Did the step we just took win clearly? Keep climbing in that direction.
    if used_n == best and used_n != up and _untested_or_stale(up):
        return up
    if used_n > best and _score(e) < best_score * CLEARLY_BETTER:
        # Going up did not help; try below the best once.
        return down if down != best and down not in stats else best
    if used_n < best and _score(e) < best_score * CLEARLY_BETTER:
        return best
    return best


def record_result(settings, best_key: str, prev_n: int, prev_mbps: float,
                  cap: int, errors: int = 0, dest_path: str = "") -> int:
    """Record one depot's real download speed (bytes/s) at prev_n; return the next N."""
    key = _best_keys(best_key, settings, dest_path=dest_path) if settings is not None else ""
    table = _load_table(settings, key) if settings is not None else {"stats": {}, "samples": 0}
    next_n = decide_next(table, prev_n, prev_mbps, cap, errors)
    if settings is not None:
        _save_table(settings, key, table)
    return next_n


def get_cmd_concurrency(cmd, default: int = 8) -> int:
    """Read the -max-downloads value out of a prepared DDM command list."""
    try:
        return int(cmd[cmd.index("-max-downloads") + 1])
    except (ValueError, IndexError, TypeError):
        return default


def set_cmd_concurrency(cmd, value: int) -> bool:
    """Rewrite the -max-downloads value in a prepared DDM command list."""
    try:
        cmd[cmd.index("-max-downloads") + 1] = str(int(value))
        return True
    except (ValueError, IndexError, TypeError):
        return False


def format_speed(bps: float) -> str:
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024**2:
        return f"{bps / 1024:.2f} KB/s"
    return f"{bps / (1024**2):.2f} MB/s"
