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
MIN_WIRE_BYTES_FOR_LEARNING = 1_048_576
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


def should_learn(wire_bytes: float) -> bool:
    """True when a sample moved enough real bytes to train the controller."""
    try:
        return float(wire_bytes) >= MIN_WIRE_BYTES_FOR_LEARNING
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
        except Exception:
            pass
        mac_tail = "nogw"
        try:
            gw_ip = ".".join(str(int(gw_hex[i:i + 2], 16)) for i in (6, 4, 2, 0))
            with open("/proc/net/arp", "r") as f:
                for line in f.read().splitlines()[1:]:
                    cells = line.split()
                    if len(cells) >= 4 and cells[0] == gw_ip and len(cells[3]) == 17:
                        mac_tail = cells[3].replace(":", "")[-6:].lower()
                        break
        except Exception:
            pass
        return f"{kind}-{mac_tail}"
    except Exception:
        return "nonet"


def get_connection_id(settings) -> str:
    """Tuning scope id: lan/wan flag + network fingerprint.

    Each physical connection learns its own best concurrency, so a fast
    home link never inherits the baseline of a throttled hotspot.
    """
    lan = read_bool(settings, "use_lancache", True) if settings is not None else False
    return f"{'lan' if lan else 'wan'}-{_network_fingerprint()}"


def _purge_legacy_keys(settings) -> None:
    """One-time removal of pre-connection best keys (idempotent)."""
    if settings is None:
        return
    try:
        for key in _LEGACY_BEST_KEYS:
            try:
                settings.remove(key)
            except Exception:
                pass
    except Exception:
        pass


def _best_keys(base_best_key: str, settings, lancache_key: str = "use_lancache"):
    """Best concurrency tracked per connection (see get_connection_id)."""
    _purge_legacy_keys(settings)
    conn = get_connection_id(settings)
    return base_best_key + "_" + conn, base_best_key + "_mbps_" + conn


def get_start_concurrency(
    settings,
    cap_key: str = "max_downloads",
    auto_key: str = "auto_max_downloads",
    best_key: str = "auto_tune_best",
    default_start: int = DEFAULT_START,
    default_cap: int = 8,
) -> int:
    """Pick the concurrency for the first depot/item of a job.

    Auto OFF -> slider value exactly (legacy). Auto ON -> stored best,
    clamped to [1, cap] (floored at MIN_CONCURRENCY when the cap allows it).
    """
    cap = get_cap(settings, cap_key, default_cap)
    if not is_auto_enabled(settings, auto_key):
        return cap
    best_n_key, _ = _best_keys(best_key, settings)
    best = read_int(settings, best_n_key, default_start)
    start = max(1, min(cap, best))
    if cap >= MIN_CONCURRENCY:
        start = max(MIN_CONCURRENCY, start)
    return start


def get_learned_display(settings, best_key: str = "auto_tune_best") -> str:
    """Return learned concurrency (e.g. '8') or 'learning...' if not trained yet."""
    if settings is None:
        return "learning..."
    best_n_key, best_m_key = _best_keys(best_key, settings)
    best_mbps = read_float(settings, best_m_key, 0.0)
    if best_mbps > 0.0:
        val = clamp(read_int(settings, best_n_key, DEFAULT_START), 1, MAX_CONCURRENCY)
        return str(val)
    return "learning..."


def decide_next(prev_n: int, prev_mbps: float, best_n: int, best_mbps: float,
                cap: int, errors: int = 0):
    """AIMD step. Returns (next_n, new_best_n, new_best_mbps)."""
    cap = clamp(cap, 1, MAX_CONCURRENCY)
    prev_n = clamp(prev_n, 1, cap)
    best_n = clamp(best_n, 1, cap)
    floor = MIN_CONCURRENCY if cap >= MIN_CONCURRENCY else 1

    if errors > 0:
        # Multiplicative decrease on any chunk-level trouble.
        return max(floor, prev_n // 2), best_n, best_mbps

    if prev_mbps <= SPEED_EPS_BPS:
        # No real download data (validation-only / fully cached) -> hold.
        if best_mbps <= 0:
            return prev_n, best_n, best_mbps
        return prev_n, best_n, best_mbps

    if best_mbps <= 0:
        # First real measurement becomes the baseline.
        return prev_n, prev_n, prev_mbps

    if prev_mbps > best_mbps * IMPROVE_FACTOR:
        return min(cap, prev_n + STEP), prev_n, prev_mbps

    if prev_mbps < best_mbps * DEGRADE_FACTOR:
        return max(floor, prev_n - STEP), best_n, best_mbps

    return prev_n, best_n, best_mbps


def record_result(settings, best_key: str, prev_n: int, prev_mbps: float,
                  cap: int, errors: int = 0) -> int:
    """Persist the outcome of one depot/item and return the next concurrency."""
    if settings is None:
        next_n, _, _ = decide_next(prev_n, prev_mbps, prev_n, 0.0, cap, errors)
        return next_n
    best_n_key, best_m_key = _best_keys(best_key, settings)
    best_n = clamp(read_int(settings, best_n_key, prev_n), 1, MAX_CONCURRENCY)
    best_mbps = read_float(settings, best_m_key, 0.0)
    next_n, new_best_n, new_best_mbps = decide_next(
        prev_n, prev_mbps, best_n, best_mbps, cap, errors
    )
    try:
        settings.setValue(best_n_key, int(new_best_n))
        settings.setValue(best_m_key, float(new_best_mbps))
        settings.sync()
    except Exception as e:
        logger.debug(f"[AutoTune] Failed to persist best concurrency: {e}")
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
