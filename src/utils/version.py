import json
import logging
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from utils.paths import Paths

logger = logging.getLogger(__name__)


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

# Build date of this release (ISO). The release pipeline does not stamp the
# version file, so this constant is the authoritative build date and has to be
# bumped together with src/res/version. A version file that does carry a build
# stamp ('20261022+ASSella-3.0.1') wins over it - see get_build_date().
BUILD_DATE = "2026-10-26"


def _stamp_date(raw: str) -> str:
    """Return the ISO date (YYYY-MM-DD) of a build stamp prefix, or '' when absent.

    Handles the 'YYYYMMDD+ASSella-3.0.1' stamp used by dated builds.
    """
    m = re.match(r"^(\d{8})\+", (raw or "").strip())
    if not m:
        return ""
    s = m.group(1)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def get_build_date() -> str:
    """Return the ISO build date (YYYY-MM-DD) of the running build."""
    return _stamp_date(app_version) or BUILD_DATE


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
            if any(x in pre_extra for x in ("canary", "testing", "alpha", "test", "devtest")):
                pre_val = -4
            elif "dev" in pre_extra:
                pre_val = -3
            elif "beta" in pre_extra:
                pre_val = -2
            elif "rc" in pre_extra or "release_candidate" in pre_extra:
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
    if any(x in v for x in ("canary", "testing", "alpha", "test", "devtest")):
        return "canary"
    elif any(x in v for x in ("beta", "dev", "rc", "candidate")):
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
    """Return the publication date (YYYY-MM-DD) of a GitHub release, '' when unknown."""
    raw = str(release.get("published_at") or release.get("created_at") or "").strip()
    return raw[:10] if len(raw) >= 10 else ""


def _is_newer_release(
    remote_version: str,
    remote_date: str,
    local_version: str,
    local_date: str,
) -> bool:
    """Decide whether a remote build is a genuine upgrade over the running one.

    Three rules, applied in order:

      1. The same version string is never an upgrade - that is the build that is
         already running, and offering it would nag on every check.
      2. A release published before this build was compiled is never an upgrade,
         whatever its version number is: it is an older build.
      3. Across version lines the base version (major.minor.patch) decides, so a
         channel switch can never offer a downgrade ('2.7.0beta' over '3.0.1').
         Within one base version the publication date decides, because a
         pre-release of that base ranks below the release it precedes.
    """
    if not remote_version:
        return False
    if remote_version == local_version:
        return False

    remote_parsed = parse_version(remote_version)
    local_parsed = parse_version(local_version)

    # Rule 2: published before this build was compiled.
    if remote_date and local_date and remote_date < local_date:
        return False

    # Rule 3: the base version decides across version lines.
    if remote_parsed[:3] != local_parsed[:3]:
        return remote_parsed[:3] > local_parsed[:3]

    # Same base version: the publication date decides, a dated release is newer
    # than the build it was compared against.
    if remote_date and local_date:
        return remote_date >= local_date
    return remote_parsed > local_parsed


def check_for_updates(
    configured_channel: Optional[str] = None,
    current_version: Optional[str] = None,
    timeout: int = 10,
) -> Dict[str, Any]:
    """
    Check GitHub for ASSella application updates.

    A release is only offered when it genuinely ranks above the running build
    (see _is_newer_release): a channel switch never offers a downgrade, and a
    release published before this build was compiled is ignored.

    Returns dict:
      - status: 'update_available' | 'up_to_date' | 'offline'
      - remote_version: str or None
      - local_version: str
      - channel: str ('stable' | 'beta' | 'canary')
      - is_cross_channel: bool (label: the configured channel differs from the
        channel of the running build)
      - error: str or None
    """
    cur = current_version or app_version
    local_clean = extract_semver(cur)
    local_date = get_build_date()
    channel = get_effective_update_channel(configured_channel, cur)
    candidates: List[Tuple[str, str]] = []  # (version, publication date)
    last_error = None

    # 1. Primary check: GitHub Releases API
    try:
        api_url = "https://api.github.com/repos/niwia/ASSella/releases"
        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": "ASSella-Updater",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            releases = json.loads(response.read().decode("utf-8"))
            for r in releases:
                tag = str(r.get("tag_name", "")).strip()
                tag_lower = tag.lower()

                is_canary = any(x in tag_lower for x in ("canary", "testing", "alpha", "test", "devtest"))
                is_beta = any(x in tag_lower for x in ("beta", "dev", "rc", "candidate")) and not is_canary

                # Decouple stable release detection from GitHub's manual 'prerelease' flag,
                # while allowing hotfixes on the stable channel.
                clean_tag = tag.lstrip("vV")
                is_clean_semver = bool(re.match(r"^\d+\.\d+(\.\d+)?$", clean_tag))
                is_hotfix = bool(re.search(r"\bhotfix\b|[-._]hotfix\d*", tag_lower))
                is_stable = (is_clean_semver or is_hotfix) and not is_canary and not is_beta

                # Channel filtering
                if channel == "stable" and not is_stable:
                    continue
                elif channel == "beta" and not is_beta and not is_stable:
                    continue
                elif channel == "canary" and not is_canary:
                    continue

                assets = [a.get("name", "") for a in r.get("assets", []) if isinstance(a, dict)]
                has_appimage = any(
                    a.lower().endswith(".appimage")
                    and not a.lower().endswith(".zsync")
                    and not a.lower().endswith(".sha256")
                    for a in assets
                )

                if has_appimage:
                    candidates.append((extract_semver(tag), _release_date(r)))
    except Exception as e:
        last_error = str(e)
        logger.debug(f"[Updater] GitHub Releases API check error: {e}")

    # 2. Fallback check: raw version file on GitHub branch. Only reached when the
    #    releases API yielded nothing for this channel; the file carries no
    #    publication date, so the version comparison has to carry the decision.
    if not candidates:
        try:
            branch = channel if channel in ("beta", "canary") else "main"
            url = f"https://raw.githubusercontent.com/niwia/ASSella/{branch}/src/res/version"
            req = urllib.request.Request(url, headers={"User-Agent": "ASSella-Updater"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                remote_raw = response.read().decode("utf-8").strip()
                candidates.append((extract_semver(remote_raw), _stamp_date(remote_raw)))
        except Exception as e:
            last_error = str(e)
            logger.debug(f"[Updater] Fallback raw version check error: {e}")

    if not candidates:
        return {
            "status": "offline",
            "remote_version": None,
            "local_version": local_clean,
            "channel": channel,
            "is_cross_channel": False,
            "error": last_error or "Could not determine latest version.",
        }

    # The newest candidate that is a genuine upgrade over the running build. A
    # candidate that only looks newer (older build date, lower base version) is
    # never offered - that is what used to make a channel switch ask the user to
    # "update" to an older build.
    offerable = [c for c in candidates if _is_newer_release(c[0], c[1], local_clean, local_date)]
    best = max(
        offerable or candidates,
        key=lambda c: (parse_version(c[0])[:3], parse_version(c[0]), c[1]),
    )
    remote_clean = best[0]

    is_local_canary = any(x in local_clean.lower() for x in ("canary", "testing", "devtest"))
    is_local_beta = any(x in local_clean.lower() for x in ("beta", "dev", "rc")) and not is_local_canary
    is_local_stable = not is_local_canary and not is_local_beta

    channel_mismatch = (
        (channel == "stable" and not is_local_stable)
        or (channel == "beta" and not is_local_beta)
        or (channel == "canary" and not is_local_canary)
    )

    if offerable:
        return {
            "status": "update_available",
            "remote_version": remote_clean,
            "local_version": local_clean,
            "channel": channel,
            "is_cross_channel": bool(channel_mismatch),
            "error": None,
        }

    return {
        "status": "up_to_date",
        "remote_version": remote_clean,
        "local_version": local_clean,
        "channel": channel,
        "is_cross_channel": False,
        "error": None,
    }

