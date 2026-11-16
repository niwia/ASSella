import json
import logging
import re
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from utils.paths import Paths

logger = logging.getLogger(__name__)

R2_RELEASES_PUBLIC_URL = "https://pub-16d0a0f940ee42f89e73b896a22be434.r2.dev"
VALID_UPDATE_SERVERS = ("auto", "github", "r2")


def normalize_update_server(raw: Optional[str] = None) -> str:
    """Return a valid update server preference ('auto', 'github', or 'r2')."""
    val = (raw or "").strip().lower()
    return val if val in VALID_UPDATE_SERVERS else "auto"


def get_r2_channel_manifest_url(channel: str) -> str:
    """Return the Cloudflare R2 release manifest URL for a channel."""
    clean = (channel or "stable").strip().lower() or "stable"
    return f"{R2_RELEASES_PUBLIC_URL.rstrip('/')}/releases/channels/{clean}.json"


def _load_version() -> str:
    """Load the application version from the resource file."""
    version_file = Paths.resource("version")

    if not version_file.exists():
        logger.warning("Version file not found, using unknown version")
        return "unknown version"

    try:
        with open(version_file, "r", encoding="utf-8") as f:
            return f.read().strip() or "unknown version"
    except Exception as e:
        logger.warning(f"Failed to read version file: {e}")
        return "unknown version"


app_version = _load_version()

# Authoritative build date and UTC timestamp (ISO-8601). The release workflow
# stamps both constants into the packaged AppImage; during dev runs they must be
# kept aligned with src/res/version. A dated version stamp ('20261022+ASSella-3.0.1')
# overrides them with its calendar date - see get_build_date() / get_build_timestamp().
BUILD_DATE = "2026-11-14"
BUILD_TIMESTAMP = "2026-11-14T03:00:00Z"


def _stamp_date(raw: str) -> str:
    """Return the ISO date (YYYY-MM-DD) of a build stamp prefix, or '' when absent.

    Handles the 'YYYYMMDD+ASSella-3.0.1' stamp used by dated builds.
    """
    m = re.match(r"^(\d{8})\+", (raw or "").strip())
    if not m:
        return ""
    s = m.group(1)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def _parse_utc_timestamp(raw: str) -> Tuple[Optional[datetime], bool]:
    """Parse an ISO date or UTC ISO-8601 datetime into ``(dt_utc, has_time)``."""
    s = (raw or "").strip()
    if not s:
        return None, False
    if len(s) == 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc), False
        except ValueError:
            return None, False
    try:
        iso = s[:-1] + "+00:00" if s.endswith("Z") else s
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        has_time = ("T" in s or "t" in s or " " in s)
        return dt, has_time
    except ValueError as e:
        logger.debug(f"ISO timestamp parse fallback for {s!r}: {e}")
    if len(s) >= 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s[:10]):
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc), False
        except ValueError:
            return None, False
    return None, False


def get_build_date(version_str: Optional[str] = None) -> str:
    """Return the ISO build date (YYYY-MM-DD) of the running build or version string."""
    return _stamp_date(version_str or app_version) or BUILD_DATE


def get_build_timestamp(version_str: Optional[str] = None) -> str:
    """Return the authoritative UTC ISO-8601 timestamp or ISO date for the running build.

    Prefers an explicit ``YYYYMMDD+`` stamp on ``version_str`` when present.
    If ``BUILD_DATE`` was overridden independently of ``BUILD_TIMESTAMP`` (e.g.
    in unit tests), honors ``BUILD_DATE`` so date-only fixtures keep working.
    """
    stamped = _stamp_date(version_str or app_version)
    if stamped:
        return stamped
    if BUILD_TIMESTAMP and BUILD_DATE == BUILD_TIMESTAMP[:10]:
        return BUILD_TIMESTAMP
    return BUILD_DATE


def get_build_date_display() -> str:
    """Return the build date of the running build as DD/MM/YYYY for UI display."""
    parts = get_build_date().split("-")
    if len(parts) == 3:
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return BUILD_DATE


def extract_semver(raw_version: str) -> str:
    """
    Extract version string by stripping build stamp prefix (e.g. '20261021+ASSella-3.0.0' -> '3.0.0')
    and leading 'v' followed by a digit, while preserving all channel markers and bare suffixes
    (e.g. '3.0.0testing051026003', '3.0Release_candidate111026001', 'v2.7.0beta').
    """
    if not raw_version:
        return ""
    v = raw_version.strip()
    m = re.match(r'^\d{8}\+[a-zA-Z0-9_-]+-(.+)$', v, re.IGNORECASE)
    if m:
        v = m.group(1).strip()
    elif "+ASSella-" in v:
        v = v.split("+ASSella-", 1)[1].strip()
    elif "+assella-" in v:
        v = v.split("+assella-", 1)[1].strip()

    # Strip leading 'v' or 'V' only if followed by a digit
    v = re.sub(r'^[vV](?=\d)', '', v)
    return v


def _normalize_build_num(num_str: str) -> int:
    """
    Normalize build numbers or date-based build stamps into a monotonic comparable integer.

    Dated stamps are decoded onto a single canonical scale (YYYYMMDDNNN) so that the
    different stamp encodings stay comparable with one another:

      - 9-digit DDMMYYNNN (ASSella canary/RC builds): '051026003' -> 20261005003
      - 8-digit YYYYMMDD:                             '20261021'   -> 20261021000
      - 8-digit DDMMYYYY:                             '05102026'   -> 20261005000

    8-digit stamps are ambiguous between the two layouts, so they are disambiguated by
    validity (a plausible year plus a real month/day) rather than by prefix, which would
    reject every DDMMYYYY stamp whose day is 20-31.

    Plain sequential build numbers ('1', '2') carry no date and keep their numeric
    value; they therefore rank below any dated stamp of the same release.
    """
    if not num_str or not num_str.isdigit():
        return 0

    s = num_str.strip()

    def _canonical(year: int, month: int, day: int, seq: int = 0) -> int:
        return year * 10000000 + month * 100000 + day * 1000 + seq

    # 9-digit DDMMYYNNN stamp used in ASSella Canary / testing / RC builds
    if len(s) == 9:
        dd, mm, yy, nnn = int(s[0:2]), int(s[2:4]), int(s[4:6]), int(s[6:9])
        if 1 <= dd <= 31 and 1 <= mm <= 12:
            year = 2000 + yy if yy < 100 else yy
            return _canonical(year, mm, dd, nnn)

    # 8-digit stamps: YYYYMMDD or DDMMYYYY, disambiguated by validity
    if len(s) == 8:
        y1, m1, d1 = int(s[0:4]), int(s[4:6]), int(s[6:8])
        if y1 >= 2020 and 1 <= m1 <= 12 and 1 <= d1 <= 31:
            return _canonical(y1, m1, d1)
        d2, m2, y2 = int(s[0:2]), int(s[2:4]), int(s[4:8])
        if 1 <= d2 <= 31 and 1 <= m2 <= 12 and y2 >= 2020:
            return _canonical(y2, m2, d2)

    try:
        return int(s)
    except ValueError:
        return 0


def parse_version(v_str: str) -> Tuple[int, int, int, int, int]:
    """
    Parse a version string into a comparable 5-element tuple:
    (major, minor, patch, pre_tag_weight, pre_num)
    Weights:
      hotfix: 1 (newer than base stable release)
      clean release: 0
      rc / release_candidate: -1
      beta: -2
      dev: -3
      canary / testing / alpha / test: -4
      other/unknown pre-release: -5
    """
    if not v_str:
        return (0, 0, 0, 0, 0)
    v = extract_semver(v_str).strip()
    m = re.match(
        r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[._-]?(dev|alpha|beta|rc|release[_-]?candidate|hotfix|testing|canary|test)(\d*)|\-([a-zA-Z0-9.]+))?$",
        v,
        re.IGNORECASE,
    )
    if m:
        major = int(m.group(1) or 0)
        minor = int(m.group(2) or 0)
        patch = int(m.group(3) or 0)
        pre_type = (m.group(4) or "").lower().replace("-", "_")
        pre_num_str = m.group(5) or ""
        pre_extra = (m.group(6) or "").lower()

        pre_weights = {
            "hotfix": 1,
            "rc": -1,
            "release_candidate": -1,
            "releasecandidate": -1,
            "beta": -2,
            "dev": -3,
            "canary": -4,
            "testing": -4,
            "alpha": -4,
            "test": -4,
        }

        if pre_type in pre_weights:
            pre_val = pre_weights[pre_type]
            pre_num = _normalize_build_num(pre_num_str) if pre_num_str else 1
        elif "hotfix" in pre_type or "hotfix" in pre_extra:
            pre_val = 1
            hm = re.search(r'\d+', pre_type + pre_extra)
            pre_num = _normalize_build_num(hm.group(0)) if hm else 1
        elif pre_type or pre_extra:
            if _CANARY_TAG_RE.search(pre_extra):
                pre_val = -4
            elif re.search(r"(?<![a-z])dev(?![a-z])", pre_extra):
                pre_val = -3
            elif re.search(r"(?<![a-z])beta(?![a-z])", pre_extra):
                pre_val = -2
            elif re.search(r"(?<![a-z])(?:rc|release[_-]?candidate)(?![a-z])", pre_extra):
                pre_val = -1
            else:
                pre_val = -5
            hm = re.search(r'\d+', pre_extra)
            pre_num = _normalize_build_num(hm.group(0)) if hm else 0
        else:
            pre_val = 0
            pre_num = 0

        return (major, minor, patch, pre_val, pre_num)
    return (0, 0, 0, 0, 0)


def get_branch_from_version(ver_str: str) -> str:
    """Determine the release branch label from a version string."""
    v = extract_semver(ver_str).lower()
    if _CANARY_TAG_RE.search(v):
        return "canary"
    elif _BETA_TAG_RE.search(v):
        return "beta"
    return "main"


def get_effective_update_channel(configured_channel: Optional[str] = None, current_version: Optional[str] = None) -> str:
    """Resolve the effective update channel ('stable', 'beta', 'canary')."""
    ch = (configured_channel or "").strip().lower()
    if ch in ("stable", "beta", "canary"):
        return ch

    branch = get_branch_from_version(current_version or app_version)
    return "stable" if branch == "main" else branch


def _release_date(release: Dict[str, Any]) -> str:
    """Return the publication date (YYYY-MM-DD) of a release, '' when unknown."""
    raw = str(release.get("published_at") or release.get("created_at") or "").strip()
    return raw[:10] if len(raw) >= 10 else ""


def _release_timestamp(release: Dict[str, Any]) -> str:
    """Return the normalized UTC ISO timestamp (or YYYY-MM-DD date) of a release."""
    raw = str(release.get("published_at") or release.get("created_at") or "").strip()
    if len(raw) < 10:
        return ""
    dt, has_time = _parse_utc_timestamp(raw)
    if dt is None:
        return raw[:10]
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if has_time else dt.strftime("%Y-%m-%d")


def _is_newer_release(
    remote_version: str,
    remote_date: str,
    local_version: str,
    local_date: str,
    channel: str = "stable",
) -> bool:
    """Decide whether a remote build is a genuine upgrade over the running one.

    Three rules, applied in order:

      1. The exact same version string (or equal parsed tuple) is never an upgrade —
         that is the build already running, and offering it would nag on every check.
      2. A release published strictly before this build was compiled (full UTC
         date-time when both carry timestamps, otherwise calendar date) is never
         an upgrade, whatever its version number is.
      3. Across version lines the base version (major.minor.patch) decides, so a
         channel switch can never offer a downgrade ('2.7.0beta' over '3.0.1').
         Within one base version a strictly later calendar day decides, while
         same-day releases fall through to parsed semver precedence / build numbers.
    """
    if not remote_version or remote_version == local_version:
        return False

    remote_parsed = parse_version(remote_version)
    local_parsed = parse_version(local_version)
    if remote_parsed == local_parsed:
        return False
    r_dt, r_has_time = _parse_utc_timestamp(remote_date)
    l_dt, l_has_time = _parse_utc_timestamp(local_date)

    # Rule 2: publication order on beta/canary; on stable, block older-dated builds.
    is_pub_ordered = str(channel or "stable").strip().lower() in ("beta", "canary")
    if r_dt and l_dt:
        if r_has_time and l_has_time:
            if r_dt < l_dt:
                return False
            if is_pub_ordered and r_dt > l_dt:
                return True
        else:
            if r_dt.date() < l_dt.date():
                return False
            if is_pub_ordered and r_dt.date() > l_dt.date():
                return True

    # Rule 3: the base version decides across version lines.
    if remote_parsed[:3] != local_parsed[:3]:
        return remote_parsed[:3] > local_parsed[:3]

    # Same base version: only let distinct calendar days override parsed semver;
    # same-day releases fall through to parsed pre-release weights / build numbers.
    if r_dt and l_dt and r_dt.date() != l_dt.date():
        return r_dt.date() > l_dt.date()
    return remote_parsed > local_parsed


_CANARY_TAG_TOKENS = ("canary", "testing", "devtest", "release_candidate", "releasecandidate")
_BETA_TAG_TOKENS = ("beta", "dev", "rc", "candidate")
_CANARY_TAG_RE = re.compile(
    r"(?<![a-z])(canary|testing|devtest|release[_-]?candidate)(?![a-z])",
    re.IGNORECASE,
)
_BETA_TAG_RE = re.compile(
    r"(?<![a-z])(beta|dev|rc|candidate)(?![a-z])",
    re.IGNORECASE,
)


def _normalize_release_branch(target_commitish: Any) -> str:
    """Return a normalized branch name from ``target_commitish``, or ``''`` for unset/SHAs."""
    raw = str(target_commitish or "").strip().lower()
    if not raw:
        return ""
    if raw.startswith("refs/heads/"):
        raw = raw[len("refs/heads/"):]
    if raw in ("main", "master"):
        return "main"
    # Full or abbreviated commit SHAs identify commits, not release branches.
    if len(raw) >= 7 and re.fullmatch(r"[0-9a-f]{7,40}", raw):
        return ""
    return raw


def _classify_release_entry(release: Dict[str, Any]) -> Optional[str]:
    """Classify a GitHub release dict or R2 channel manifest into a channel.

    Combines tag tokens, GitHub's ``prerelease`` boolean, ``target_commitish``
    branch metadata, and optional R2 manifest ``channel`` so releases stay
    strictly isolated between ``stable``, ``beta``, and ``canary``.
    """
    if not isinstance(release, dict) or release.get("draft"):
        return None
    tag = str(release.get("tag_name") or release.get("version") or "").strip()
    if not tag:
        return None
    tag_lower = tag.lower()
    prerelease = bool(release.get("prerelease", False))
    target_branch = _normalize_release_branch(release.get("target_commitish"))
    manifest_channel = str(release.get("channel") or "").strip().lower()

    has_canary_tag = bool(_CANARY_TAG_RE.search(tag_lower))
    has_beta_tag = bool(_BETA_TAG_RE.search(tag_lower)) and not has_canary_tag

    if has_canary_tag or target_branch == "canary" or manifest_channel == "canary":
        return "canary" if prerelease else None

    if has_beta_tag or manifest_channel == "beta" or (prerelease and target_branch in ("", "beta", "main")):
        return "beta"

    if target_branch and target_branch != "main":
        return None
    if manifest_channel and manifest_channel not in ("stable", "main"):
        return None

    clean_tag = tag.lstrip("vV")
    is_clean_semver = bool(re.match(r"^\d+\.\d+(\.\d+)?$", clean_tag))
    is_hotfix = bool(re.search(r"\bhotfix\b|[-._]hotfix\d*", tag_lower))
    if is_clean_semver or is_hotfix:
        return "stable"
    return None


def _parse_release_candidates(releases: List[Dict[str, Any]], channel: str) -> List[Tuple[str, str]]:
    """Filter release entries strictly to the requested channel and require a real AppImage asset."""
    norm_channel = str(channel or "stable").strip().lower()
    if norm_channel in ("main", "master", ""):
        norm_channel = "stable"

    candidates: List[Tuple[str, str]] = []
    for r in releases:
        if not isinstance(r, dict) or r.get("draft"):
            continue
        tag = str(r.get("tag_name") or r.get("version") or "").strip()
        if not tag:
            continue

        release_channel = _classify_release_entry(r)
        if release_channel != norm_channel:
            continue

        assets = [a.get("name", "") for a in r.get("assets", []) if isinstance(a, dict)]
        has_appimage = any(
            a.lower().endswith(".appimage")
            and not a.lower().endswith(".zsync")
            and not a.lower().endswith(".sha256")
            for a in assets
        )

        if has_appimage:
            candidates.append((extract_semver(tag), _release_timestamp(r) or _release_date(r)))
    return candidates


def _fetch_github_releases(timeout: int) -> List[Dict[str, Any]]:
    api_url = "https://api.github.com/repos/niwia/ASSella/releases?per_page=100"
    req = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": "ASSella-Updater",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError("Unexpected response format from GitHub Releases API")
        return data


def _fetch_r2_channel_manifest(channel: str, timeout: int) -> Dict[str, Any]:
    manifest_url = get_r2_channel_manifest_url(channel)
    req = urllib.request.Request(
        manifest_url,
        headers={
            "User-Agent": "ASSella-Updater",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict) or not data.get("tag_name"):
            raise ValueError("Invalid R2 channel manifest payload")
        return data


def _timestamp_sort_key(ts: str) -> float:
    """Convert an ISO timestamp or date string into a sortable UTC epoch float."""
    dt, _ = _parse_utc_timestamp(ts)
    return dt.timestamp() if dt else 0.0


def _candidate_sort_key(cand: Tuple[str, str], channel: str) -> Tuple[Any, ...]:
    """Rank candidates per channel: SemVer-first for stable, timestamp-first for beta/canary."""
    parsed = parse_version(cand[0])
    ts = _timestamp_sort_key(cand[1])
    if channel == "stable":
        return (parsed[:3], parsed[3], parsed[4], ts)
    prefer_canary = 1 if (channel == "canary" and "canary" in cand[0].lower()) else 0
    return (ts, parsed[:3], parsed[3], prefer_canary, parsed[4])


def check_for_updates(
    configured_channel: Optional[str] = None,
    current_version: Optional[str] = None,
    timeout: int = 10,
    update_server: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Check for ASSella application updates using GitHub and/or Cloudflare R2.

    update_server controls source selection:
      - 'auto': try GitHub Releases API first, fall back to R2 channel manifest,
        and finally GitHub raw branch version file.
      - 'github': try GitHub Releases API -> GitHub raw branch version file only.
      - 'r2': query Cloudflare R2 channel manifest only.

    A release is only offered when it belongs strictly to the selected channel.
    When staying on the running build's own channel, both full UTC build timestamp
    and version precedence are enforced (see _is_newer_release). When actively
    switching to a different branch, the target branch's latest release is
    offered directly (is_cross_channel=True).

    Returns dict:
      - status: 'update_available' | 'up_to_date' | 'offline'
      - remote_version: str or None
      - local_version: str
      - channel: str ('stable' | 'beta' | 'canary')
      - is_cross_channel: bool (label: the configured channel differs from the
        channel of the running build)
      - source: 'github' | 'r2' | 'github_raw' | None
      - update_server: 'auto' | 'github' | 'r2'
      - error: str or None
    """
    cur = current_version or app_version
    local_clean = extract_semver(cur)
    local_timestamp = get_build_timestamp(cur)
    channel = get_effective_update_channel(configured_channel, cur)
    server = normalize_update_server(update_server)
    candidates: List[Tuple[str, str]] = []  # (version, UTC publication timestamp)
    resolved_source: Optional[str] = None
    gh_responded = False
    api_responded = False
    last_error = None

    # 1. GitHub Releases API (skipped in explicit 'r2' mode)
    if server in ("auto", "github"):
        try:
            releases = _fetch_github_releases(timeout)
            gh_responded = True
            api_responded = True
            resolved_source = "github"
            candidates = _parse_release_candidates(releases, channel)
        except Exception as e:
            last_error = str(e)
            logger.debug(f"[Updater] GitHub Releases API check error: {e}")

    # 2. Cloudflare R2 channel manifest (primary in 'r2' mode; fallback in 'auto'
    #    when GitHub errored OR yielded no usable candidates for this channel).
    #    R2 only mirrors 'stable' and 'beta'; 'canary' remains GitHub-only.
    if not candidates and server in ("auto", "r2"):
        if channel in ("stable", "beta"):
            try:
                manifest = _fetch_r2_channel_manifest(channel, timeout)
                if isinstance(manifest, dict):
                    r2_candidates = _parse_release_candidates([manifest], channel)
                    if not gh_responded or r2_candidates:
                        api_responded = True
                        resolved_source = "r2"
                        candidates = r2_candidates
            except Exception as e:
                last_error = str(e)
                logger.debug(f"[Updater] R2 release check error ({channel}): {e}")
        elif server == "r2":
            last_error = "Cloudflare R2 does not host canary releases."

    # 3. Fallback check: raw version file on GitHub branch. Only reached in
    #    'auto'/'github' modes when releases endpoints errored/failed to respond.
    if not api_responded and server in ("auto", "github"):
        try:
            branch = channel if channel in ("beta", "canary") else "main"
            url = f"https://raw.githubusercontent.com/niwia/ASSella/{branch}/src/res/version"
            req = urllib.request.Request(url, headers={"User-Agent": "ASSella-Updater"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                remote_raw = response.read().decode("utf-8").strip()
                api_responded = True
                resolved_source = "github_raw"
                remote_ver = extract_semver(remote_raw)
                if remote_ver:
                    candidates.append((remote_ver, _stamp_date(remote_raw)))
        except Exception as e:
            last_error = str(e)
            logger.debug(f"[Updater] Fallback raw version check error: {e}")

    if not candidates:
        if api_responded:
            return {
                "status": "up_to_date",
                "remote_version": None,
                "local_version": local_clean,
                "channel": channel,
                "is_cross_channel": False,
                "source": resolved_source,
                "update_server": server,
                "error": None,
            }
        return {
            "status": "offline",
            "remote_version": None,
            "local_version": local_clean,
            "channel": channel,
            "is_cross_channel": False,
            "source": None,
            "update_server": server,
            "error": last_error or "Could not determine latest version.",
        }

    best = max(candidates, key=lambda c: _candidate_sort_key(c, channel))
    remote_clean = best[0]

    local_branch = get_branch_from_version(local_clean)
    is_local_canary = local_branch == "canary"
    is_local_beta = local_branch == "beta"
    is_local_stable = local_branch == "main"

    channel_mismatch = (
        (channel == "stable" and not is_local_stable)
        or (channel == "beta" and not is_local_beta)
        or (channel == "canary" and not is_local_canary)
    )

    if channel_mismatch:
        same_ver = (remote_clean == local_clean or parse_version(remote_clean) == parse_version(local_clean))
        if not same_ver:
            return {
                "status": "update_available",
                "remote_version": remote_clean,
                "local_version": local_clean,
                "channel": channel,
                "is_cross_channel": True,
                "source": resolved_source,
                "update_server": server,
                "error": None,
            }
    else:
        offerable = [
            c
            for c in candidates
            if _is_newer_release(c[0], c[1], local_clean, local_timestamp, channel=channel)
        ]
        if offerable:
            best_offerable = max(offerable, key=lambda c: _candidate_sort_key(c, channel))
            return {
                "status": "update_available",
                "remote_version": best_offerable[0],
                "local_version": local_clean,
                "channel": channel,
                "is_cross_channel": False,
                "source": resolved_source,
                "update_server": server,
                "error": None,
            }

    return {
        "status": "up_to_date",
        "remote_version": remote_clean,
        "local_version": local_clean,
        "channel": channel,
        "is_cross_channel": False,
        "source": resolved_source,
        "update_server": server,
        "error": None,
    }

