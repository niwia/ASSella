import codecs
import logging
import os
import re
import select
import shutil
import signal
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PyQt6.QtCore import QMetaObject, QObject, Qt, Q_ARG
try:
    from PyQt6 import sip
except ImportError:  # pragma: no cover - PyQt6 ships sip in normal installs
    sip = None

from ui.dialogs.themed_progress_dialog import ThemedProgressDialog
from ui.dialogs.themed_message_box import ThemedMessageBox

from utils.paths import Paths
from utils.settings import get_settings
from utils.version import (
    _candidate_sort_key,
    _classify_release_entry,
    _release_timestamp,
    app_version,
    extract_semver,
    get_branch_from_version,
    get_effective_update_channel,
    get_r2_channel_manifest_url,
    normalize_update_server,
    parse_version,
)

logger = logging.getLogger(__name__)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_DELTA_PROGRESS_RE = re.compile(
    r"(?P<pct>\d+(?:\.\d+)?)%\s*done"
    r"(?:\s*\(\s*(?P<done>\d+(?:\.\d+)?)\s+of\s+(?P<total>\d+(?:\.\d+)?)\s*(?P<unit>[KMGT]?i?B)\s*\))?"
)
_ZSYNC_BAR_RE = re.compile(
    r"[#-]{3,}\s+(?P<pct>\d+(?:\.\d+)?)%"
    r"(?:\s+(?P<rate>\d+(?:\.\d+)?\s*[kKMGT]?i?B(?:ps|/s)))?"
)


def _is_qt_alive(obj) -> bool:
    """Return True if a QObject reference is still valid and not destroyed."""
    if obj is None:
        return False
    if sip is not None:
        try:
            return not sip.isdeleted(obj)
        except Exception:
            return False
    return True


def _resolve_appimage_path() -> str:
    """Resolve the installed AppImage target path, following any symlink."""
    default_appimage = os.path.expanduser("~/.local/share/ACCELA/ASSella.AppImage")
    appimage_path = os.environ.get("APPIMAGE", "")
    # AppRun/run.sh export APPIMAGE=1; ignore non-path sentinel values.
    if appimage_path and appimage_path != "1" and os.path.exists(appimage_path):
        return os.path.realpath(appimage_path)
    argv0 = os.environ.get("ARGV0", "")
    if argv0 and argv0.lower().endswith(".appimage") and os.path.exists(argv0):
        return os.path.realpath(argv0)
    if os.path.exists(default_appimage):
        return os.path.realpath(default_appimage)
    return appimage_path or default_appimage


def _find_bundled_or_system_tool(tool_name: str) -> str:
    """Locate a bundled helper binary inside the AppImage or fall back to PATH."""
    appdir = os.environ.get("APPDIR")
    candidates = []
    if appdir:
        candidates.append(Path(appdir) / "bin" / tool_name)
    candidates.extend((
        Paths.BASE_DIR.parent / tool_name,
        Paths.BASE_DIR.parent / "bin" / tool_name,
        Paths.BASE_DIR / tool_name,
        Paths.BASE_DIR / "bin" / tool_name,
    ))
    bundled = next(
        (c for c in candidates if c and c.exists() and os.access(c, os.X_OK)),
        None,
    )
    if bundled:
        return str(bundled)
    return shutil.which(tool_name) or ""


def _cleanup_scratch_files(
    dest_dir: Path,
    base_name: str,
    installed_path: Optional[Path] = None,
    keep: Optional[Path] = None,
) -> None:
    """Remove temporary delta and download scratch files (including .part)."""
    prefixes = {base_name, "ASSella.AppImage"}
    suffixes = (
        ".part",
        ".autoupdate",
        ".autoupdate.part",
        ".autoupdate.zs-old",
        ".zsync-seed",
        ".zsync-seed.part",
        ".zsync-seed.zs-old",
        ".zsync-out",
        ".zsync-out.part",
        ".zsync-out.zs-old",
    )
    skip_resolved = set()
    for p in (installed_path, keep):
        if p is not None:
            try:
                skip_resolved.add(p.resolve())
            except OSError:
                skip_resolved.add(p)

    for prefix in prefixes:
        for suffix in suffixes:
            candidate = dest_dir / f"{prefix}{suffix}"
            try:
                if candidate.resolve() in skip_resolved:
                    continue
            except OSError:
                if candidate in (installed_path, keep):
                    continue
            try:
                candidate.unlink(missing_ok=True)
            except OSError as e:
                logger.debug(f"Could not remove scratch file {candidate}: {e}")


def _terminate_process(proc, started_new_session: bool = False) -> None:
    """Terminate an active subprocess (and its process group if isolated) without blocking."""
    try:
        if proc.poll() is not None:
            return
    except Exception as e:
        logger.debug(f"Could not poll delta updater before termination: {e}")

    pid = getattr(proc, "pid", None)
    use_pgid = False
    if started_new_session and os.name == "posix" and isinstance(pid, int) and pid > 0:
        try:
            use_pgid = os.getpgid(pid) == pid and pid != os.getpgid(0)
        except OSError:
            use_pgid = False

    for sig, fallback_method in ((signal.SIGTERM, "terminate"), (signal.SIGKILL, "kill")):
        try:
            if use_pgid:
                os.killpg(pid, sig)
            else:
                getattr(proc, fallback_method)()
        except (ProcessLookupError, PermissionError, OSError):
            try:
                getattr(proc, fallback_method)()
            except Exception as e:
                logger.debug(f"Fallback process {fallback_method} failed: {e}")
        except Exception as e:
            logger.debug(f"Process signal {sig} failed: {e}")
        try:
            proc.wait(timeout=2)
            return
        except Exception as e:
            logger.debug(f"Process wait after signal {sig} did not complete cleanly: {e}")
            continue


class _DeltaProgressTracker:
    """Parses streamed appimageupdatetool/zsync2 output into throttled UI updates."""

    def __init__(self, tool_name: str, progress, cancel_event: threading.Event):
        self.tool_name = tool_name
        self.progress = progress
        self.cancel_event = cancel_event
        self.last_bar_val = 2
        self.last_label = ""
        self.last_label_ts = 0.0
        self.last_milestone = -1
        self.last_status_line = ""

    def _emit_bar(self, val: int) -> None:
        if val == self.last_bar_val or self.cancel_event.is_set() or not _is_qt_alive(self.progress):
            return
        self.last_bar_val = val
        try:
            QMetaObject.invokeMethod(
                self.progress,
                "setValue",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(int, val),
            )
        except RuntimeError as e:
            logger.debug(f"Progress dialog unavailable during setValue: {e}")

    def _emit_label(self, text: str, force: bool = False) -> None:
        if not text or text == self.last_label or self.cancel_event.is_set() or not _is_qt_alive(self.progress):
            return
        now = time.monotonic()
        if not force and (now - self.last_label_ts) < 0.15:
            return
        self.last_label = text
        self.last_label_ts = now
        try:
            QMetaObject.invokeMethod(
                self.progress,
                "setLabelText",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, text),
            )
        except RuntimeError as e:
            logger.debug(f"Progress dialog unavailable during setLabelText: {e}")

    def feed(self, raw_segment: str, final_line: bool = True) -> None:
        clean = _ANSI_ESCAPE_RE.sub("", raw_segment).strip()
        if not clean:
            return

        match = _DELTA_PROGRESS_RE.search(clean)
        if match:
            # Avoid parsing an incomplete parenthesized MiB block while streaming mid-frame.
            if not final_line and "(" in clean and ")" not in clean:
                return
            pct = min(100.0, max(0.0, float(match.group("pct"))))
            bar_val = max(self.last_bar_val, min(98, 2 + int(pct * 0.96)))
            self._emit_bar(bar_val)

            done_s = match.group("done")
            total_s = match.group("total")
            unit = match.group("unit")
            if done_s and total_s and float(total_s) > 0:
                label = f"Delta update: {pct:.1f}% ({float(done_s):.1f} / {float(total_s):.1f} {unit})"
                self._emit_label(label, force=(pct >= 99.9))
            elif pct > 0:
                self._emit_label(f"Delta update: {pct:.1f}%", force=(pct >= 99.9))

            if final_line:
                milestone = (int(pct) // 25) * 25
                if milestone in (25, 50, 75, 100) and milestone > self.last_milestone:
                    logger.info(f"[{self.tool_name}] {clean}")
                    self.last_milestone = milestone
            return

        bar_match = _ZSYNC_BAR_RE.search(clean)
        if bar_match:
            pct = min(100.0, max(0.0, float(bar_match.group("pct"))))
            bar_val = max(self.last_bar_val, min(98, 2 + int(pct * 0.96)))
            self._emit_bar(bar_val)
            rate = (bar_match.group("rate") or "").strip()
            label = f"Delta update: {pct:.1f}% ({rate})" if rate else f"Delta update: {pct:.1f}%"
            self._emit_label(label, force=(pct >= 99.9))
            return

        if not final_line:
            return

        if clean != self.last_status_line:
            logger.info(f"[{self.tool_name}] {clean}")
            self.last_status_line = clean

        if "Reading seed file" in clean:
            self._emit_bar(max(self.last_bar_val, 4))
            self._emit_label("Delta update: scanning existing AppImage...", force=True)
        elif "Fetching remaining blocks" in clean or "Downloading from" in clean:
            self._emit_bar(max(self.last_bar_val, 5))
            self._emit_label("Delta update: downloading blocks...", force=True)
        elif "Verifying downloaded file" in clean:
            self._emit_bar(max(self.last_bar_val, 99))
            self._emit_label("Delta update: verifying checksum...", force=True)


def _run_delta_subprocess(
    cmd: list[str],
    tool_name: str,
    cancel_event: threading.Event,
    progress,
    timeout: int,
    cwd: Optional[str] = None,
) -> tuple[Optional[int], bool]:
    """Run a delta-update command with live progress streaming and cooperative cancel."""
    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
    }
    if cwd is not None:
        popen_kwargs["cwd"] = cwd
    started_new_session = os.name == "posix"
    if started_new_session:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    tracker = _DeltaProgressTracker(tool_name, progress, cancel_event)
    deadline = time.monotonic() + timeout
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf = ""
    last_idle_buf = ""

    def _consume_text(text: str) -> None:
        nonlocal buf
        buf += text
        parts = re.split(r"[\r\n]+", buf)
        buf = parts.pop()
        for segment in parts:
            tracker.feed(segment, final_line=True)

    fd = None
    if proc.stdout is not None:
        try:
            fd = proc.stdout.fileno()
        except (AttributeError, ValueError, OSError):
            fd = None

    try:
        if fd is not None:
            while True:
                if cancel_event.is_set():
                    _terminate_process(proc, started_new_session)
                    return None, True
                if time.monotonic() > deadline:
                    _terminate_process(proc, started_new_session)
                    raise subprocess.TimeoutExpired(cmd, timeout)

                ready, _, _ = select.select([fd], [], [], 0.15)
                if ready:
                    raw = os.read(fd, 4096)
                    if not raw:
                        buf += decoder.decode(b"", final=True)
                        if buf:
                            tracker.feed(buf, final_line=True)
                            buf = ""
                        break
                    _consume_text(decoder.decode(raw, final=False))
                else:
                    if buf and buf != last_idle_buf:
                        tracker.feed(buf, final_line=False)
                        last_idle_buf = buf
                    if proc.poll() is not None:
                        trailing_ready, _, _ = select.select([fd], [], [], 0)
                        if not trailing_ready:
                            buf += decoder.decode(b"", final=True)
                            if buf:
                                tracker.feed(buf, final_line=True)
                                buf = ""
                            break
        elif proc.stdout is not None:
            while True:
                if cancel_event.is_set():
                    _terminate_process(proc, started_new_session)
                    return None, True
                if time.monotonic() > deadline:
                    _terminate_process(proc, started_new_session)
                    raise subprocess.TimeoutExpired(cmd, timeout)
                chunk = proc.stdout.read(4096)
                if not chunk:
                    buf += decoder.decode(b"", final=True)
                    if buf:
                        tracker.feed(buf, final_line=True)
                        buf = ""
                    break
                text = decoder.decode(chunk, final=False) if isinstance(chunk, (bytes, bytearray)) else str(chunk)
                _consume_text(text)

        while proc.poll() is None:
            if cancel_event.is_set():
                _terminate_process(proc, started_new_session)
                return None, True
            if time.monotonic() > deadline:
                _terminate_process(proc, started_new_session)
                raise subprocess.TimeoutExpired(cmd, timeout)
            time.sleep(0.05)

        return proc.returncode, cancel_event.is_set()
    finally:
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except Exception as e:
                logger.debug(f"Failed to close updater stdout pipe: {e}")


def _tags_match_or_newer(remote_tag: str, requested_tag: str) -> bool:
    """True when ``remote_tag`` satisfies ``requested_tag`` without downgrading."""
    clean_req = str(requested_tag or "").strip()
    if not clean_req or clean_req == "latest":
        return True
    clean_rem = str(remote_tag or "").strip()
    if not clean_rem:
        return False
    if clean_rem.lstrip("vV").lower() == clean_req.lstrip("vV").lower():
        return True
    return parse_version(clean_rem) >= parse_version(clean_req)


def _extract_release_assets(release_data: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[str]]:
    """Extract (appimage_url, zsync_url, expected_sha256) from a GitHub/R2 release dict."""
    download_url: Optional[str] = None
    appimage_sha: Optional[str] = None
    appimage_candidates: list[Tuple[str, Optional[str]]] = []
    zsync_url: Optional[str] = None

    for asset in release_data.get("assets", []):
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", "") or "").strip()
        if not name or not url:
            continue
        low = name.lower()
        if low == "assella.appimage.zsync" or (low.endswith(".appimage.zsync") and not zsync_url):
            zsync_url = url
            continue
        if low.endswith(".zsync") or low.endswith(".sha256"):
            continue
        if low.endswith(".appimage"):
            raw_sha = str(asset.get("sha256") or "").strip().lower()
            if not raw_sha:
                raw_digest = str(asset.get("digest") or "").strip().lower()
                if raw_digest.startswith("sha256:"):
                    raw_sha = raw_digest[len("sha256:"):].strip()
            sha = raw_sha if re.fullmatch(r"[0-9a-f]{64}", raw_sha) else None
            if name == "ASSella.AppImage":
                download_url = url
                appimage_sha = sha
                continue
            appimage_candidates.append((url, sha))

    if not download_url and appimage_candidates:
        download_url, appimage_sha = appimage_candidates[0]

    if not download_url:
        raise RuntimeError("No AppImage asset found in the release.")

    if not zsync_url:
        zsync_url = f"{download_url}.zsync"

    return download_url, zsync_url, appimage_sha


def _resolve_sha256_sidecar(release_data: Dict[str, Any], download_url: str) -> Tuple[Optional[str], bool]:
    """Return ``(sidecar_url, advertised)`` for the selected release AppImage asset."""
    if not download_url:
        return None, False
    dl_basename = download_url.rstrip("/").rsplit("/", 1)[-1].lower()
    preferred = f"{dl_basename}.sha256" if dl_basename else "assella.appimage.sha256"
    fallback_sha_url: Optional[str] = None
    for asset in release_data.get("assets", []):
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", "")).strip().lower()
        url = str(asset.get("browser_download_url", "") or "").strip()
        if not name or not url or not name.endswith(".sha256") or name.endswith(".zsync.sha256"):
            continue
        if name == preferred:
            return url, True
        if name == "assella.appimage.sha256" and fallback_sha_url is None:
            fallback_sha_url = url
    if fallback_sha_url:
        return fallback_sha_url, True
    return f"{download_url}.sha256", False


def _fetch_github_release_data(tag: str, channel: Optional[str] = None) -> Dict[str, Any]:
    """Fetch target release metadata from the GitHub Releases API, scoped to ``channel``."""
    import json

    norm_channel = str(channel or "").strip().lower()
    if norm_channel in ("main", "master"):
        norm_channel = "stable"

    headers = {"User-Agent": "ASSella-Updater", "Accept": "application/vnd.github+json"}
    if not tag or tag == "latest":
        api_url = "https://api.github.com/repos/niwia/ASSella/releases?per_page=100"
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            releases_list = json.loads(resp.read().decode("utf-8"))
        if not isinstance(releases_list, list):
            return {}
        eligible = [r for r in releases_list if isinstance(r, dict) and not r.get("draft")]
        if norm_channel:
            eligible = [r for r in eligible if _classify_release_entry(r) == norm_channel]
            with_appimage = []
            for r in eligible:
                try:
                    _extract_release_assets(r)
                    with_appimage.append(r)
                except RuntimeError:
                    pass
            if with_appimage:
                eligible = with_appimage
            if not eligible:
                raise RuntimeError(f"No GitHub release found for channel '{norm_channel}'.")
            return max(
                eligible,
                key=lambda r: _candidate_sort_key(
                    (extract_semver(str(r.get("tag_name") or "")), _release_timestamp(r)),
                    norm_channel,
                ),
            )
        return eligible[0] if eligible else {}

    def _fetch_by_tag(t: str) -> Dict[str, Any]:
        url = f"https://api.github.com/repos/niwia/ASSella/releases/tags/{t}"
        r_req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(r_req, timeout=15) as r_resp:
            return json.loads(r_resp.read().decode("utf-8"))

    try:
        data = _fetch_by_tag(tag)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            alt_tag = tag[1:] if tag.startswith("v") else f"v{tag}"
            logger.info(f"Release tag {tag} returned 404, retrying with alternate tag: {alt_tag}")
            data = _fetch_by_tag(alt_tag)
        else:
            raise

    if isinstance(data, dict) and tag and not data.get("tag_name"):
        data = {**data, "tag_name": tag}

    if norm_channel and isinstance(data, dict):
        actual_channel = _classify_release_entry(data)
        if actual_channel != norm_channel:
            raise RuntimeError(
                f"GitHub release '{data.get('tag_name') or tag}' belongs to channel "
                f"'{actual_channel or 'unknown'}', expected '{norm_channel}'."
            )
    return data


def _fetch_r2_release_data(channel: str, requested_tag: str) -> Dict[str, Any]:
    """Fetch target release metadata strictly from the active channel's Cloudflare R2 manifest."""
    import json

    norm_channel = str(channel or "stable").strip().lower()
    if norm_channel in ("main", "master", ""):
        norm_channel = "stable"
    if norm_channel not in ("stable", "beta"):
        raise RuntimeError(f"Cloudflare R2 does not host '{norm_channel}' releases.")

    manifest_url = get_r2_channel_manifest_url(norm_channel)
    req = urllib.request.Request(
        manifest_url,
        headers={"User-Agent": "ASSella-Updater", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if not isinstance(data, dict) or not data.get("assets"):
        raise RuntimeError(f"Invalid R2 release manifest for channel '{norm_channel}'.")

    actual_channel = _classify_release_entry(data)
    if actual_channel != norm_channel:
        raise RuntimeError(
            f"R2 '{norm_channel}' manifest holds '{data.get('tag_name') or 'unknown'}' "
            f"(channel '{actual_channel or 'unknown'}'), expected '{norm_channel}'."
        )

    if requested_tag:
        remote_tag = str(data.get("tag_name") or "").strip()
        if not _tags_match_or_newer(remote_tag, requested_tag):
            raise RuntimeError(
                f"R2 mirror currently holds {remote_tag or 'unknown'}, older than requested {requested_tag}."
            )

    return data


def _compute_file_sha256(file_path: Path, cancel_event: threading.Event) -> Optional[str]:
    """Compute lowercase SHA-256 hex digest of ``file_path``, or None if cancelled."""
    import hashlib

    digest = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            if cancel_event.is_set():
                return None
            digest.update(chunk)
    return digest.hexdigest()


def _verify_sha256_file(
    file_path: Path,
    *,
    expected_hex: Optional[str],
    sidecar_url: Optional[str],
    cancel_event: threading.Event,
    require_checksum: bool = False,
) -> None:
    """Verify ``file_path`` against manifest SHA-256 or a remote ``.sha256`` sidecar."""
    expected = (expected_hex or "").strip().lower() or None
    strict = bool(require_checksum or (os.environ.get("ASSELLA_REQUIRE_SHA256") == "1"))

    if not expected and sidecar_url:
        try:
            sha_req = urllib.request.Request(sidecar_url, headers={"User-Agent": "ASSella-Updater"})
            with urllib.request.urlopen(sha_req, timeout=30) as sha_resp:
                published = sha_resp.read().decode("utf-8", "replace")
            match = re.search(r"(?<![a-fA-F0-9])[a-fA-F0-9]{64}(?![a-fA-F0-9])", published)
            if not match:
                raise RuntimeError("Checksum file is malformed or missing a SHA-256 digest.")
            expected = match.group(0).lower()
        except urllib.error.HTTPError as he:
            if he.code == 404 and not strict:
                logger.warning("No published .sha256 for this release; skipping verification.")
                return
            if he.code == 404:
                raise RuntimeError("Published .sha256 checksum was not found (HTTP 404).") from he
            raise RuntimeError(f"Checksum fetch failed: HTTP {he.code} ({he.reason}).") from he
        except RuntimeError:
            raise
        except Exception as ve:
            raise RuntimeError(f"Could not fetch SHA-256 checksum ({ve}).") from ve

    if not expected:
        if strict:
            raise RuntimeError("SHA-256 verification required, but no checksum is available.")
        logger.warning("No published .sha256 for this release; skipping verification.")
        return

    actual = _compute_file_sha256(file_path, cancel_event)
    if actual is None:
        return
    if actual != expected:
        raise RuntimeError(f"Checksum mismatch — expected {expected[:16]}…, got {actual[:16]}…")
    logger.info(f"SHA-256 verified: {actual}")


class AppUpdateManager(QObject):
    """Handles self-update: release lookup, delta (appimageupdatetool/zsync) and
    full-download install. Lives outside MainWindow to keep that file manageable."""

    def __init__(self, parent_widget=None):
        super().__init__(parent_widget)
        self._parent = parent_widget
        self._lock = threading.Lock()
        self._updating = False
        self._active_progress: Optional[ThemedProgressDialog] = None
        self._last_failed_server: Optional[str] = None

    def _clear_active_state(self) -> None:
        with self._lock:
            self._updating = False
            self._active_progress = None

    def _get_update_preferences(self) -> Tuple[str, str]:
        """Resolve effective (channel, update_server) from parent settings on the GUI thread."""
        settings = getattr(self._parent, "settings", None)
        if settings is None:
            settings = get_settings()
        try:
            raw_channel = str(settings.value("update_channel", "") or "").strip().lower()
        except Exception as e:
            logger.debug(f"Could not read update_channel setting: {e}")
            raw_channel = ""
        try:
            raw_server = str(settings.value("update_server", "auto") or "auto").strip().lower()
        except Exception as e:
            logger.debug(f"Could not read update_server setting: {e}")
            raw_server = "auto"

        channel = get_effective_update_channel(raw_channel, app_version)
        server = normalize_update_server(raw_server)
        return channel, server

    def run_self_update(self, server_override: Optional[str] = None, skip_prompt: bool = False) -> None:
        """Install update using delta ZSync (appimageupdatetool/zsync2), with full-download fallback."""
        with self._lock:
            if self._updating:
                logger.info("Self-update already in progress; ignoring duplicate request.")
                if _is_qt_alive(self._active_progress):
                    try:
                        self._active_progress.raise_()
                        self._active_progress.activateWindow()
                    except RuntimeError as e:
                        logger.debug(f"Could not raise active progress dialog: {e}")
                return
            self._updating = True

        try:
            self._last_failed_server = None
            # Resolve the installed AppImage target (following symlinks so shutil.move
            # updates the real binary instead of overwriting a launcher symlink).
            appimage_path = _resolve_appimage_path()
            if not os.path.exists(appimage_path):
                self._clear_active_state()
                ThemedMessageBox.information(
                    self._parent, "Self-Update",
                    "Could not locate the installed ASSella.AppImage to update."
                )
                return

            channel, update_server = self._get_update_preferences()
            if server_override:
                update_server = normalize_update_server(server_override)
            remote_version = getattr(self._parent, "_latest_remote_version", None)
            remote_channel = str(getattr(self._parent, "_latest_update_channel", "") or "").strip().lower()
            # Normalise the tag to exactly one leading "v" and discard stale cached tags
            # whose channel does not match the currently selected update channel.
            tag = ""
            if remote_version and (not remote_channel or remote_channel == channel):
                synthetic_channel = _classify_release_entry({
                    "tag_name": str(remote_version),
                    "prerelease": (channel != "stable"),
                    "target_commitish": ("main" if channel == "stable" else channel),
                })
                if synthetic_channel == channel:
                    tag = remote_version if str(remote_version).startswith("v") else f"v{remote_version}"
                else:
                    logger.info(
                        f"Ignoring cached tag '{remote_version}' ({synthetic_channel}) for active channel '{channel}'."
                    )
            elif remote_version and remote_channel != channel:
                logger.info(
                    f"Ignoring cached tag '{remote_version}' from channel '{remote_channel}' for active channel '{channel}'."
                )

            if not skip_prompt:
                local_clean = extract_semver(app_version)
                local_branch = get_branch_from_version(app_version)
                local_channel = "stable" if local_branch == "main" else local_branch
                is_cross_channel = local_channel != channel
                is_downgrade = False
                if tag and local_clean:
                    t_ver = parse_version(tag)
                    l_ver = parse_version(local_clean)
                    is_downgrade = (t_ver[:3] < l_ver[:3]) or (t_ver[:3] == l_ver[:3] and t_ver < l_ver)

                if is_cross_channel and is_downgrade:
                    dlg_title = "Switch Channel & Downgrade"
                elif is_cross_channel:
                    dlg_title = "Switch Update Channel"
                else:
                    dlg_title = "Install Update"

                target_label = tag or f"latest ({channel})"
                downgrade_note = (
                    "\nWarning: This switches to an older version line (downgrade).\n"
                    if is_downgrade
                    else ""
                )
                reply = ThemedMessageBox.question(
                    self._parent,
                    dlg_title,
                    f"Update ASSella from v{local_clean} ({local_channel}) to {target_label} ({channel})?\n"
                    f"{downgrade_note}\n"
                    "Would you like to download and install it now?\n"
                    "The app will need to be restarted after the update.",
                    ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
                    ThemedMessageBox.StandardButton.Yes,
                )
                if reply != ThemedMessageBox.StandardButton.Yes:
                    self._clear_active_state()
                    return

            initial_label = "Connecting to Cloudflare R2..." if update_server == "r2" else "Connecting to GitHub..."
            progress = ThemedProgressDialog(initial_label, "Cancel", 0, 100, self._parent)
            progress.setWindowTitle("Downloading Update")
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)
            progress.setValue(2)
            # The application event loop owns the dialog; show it before the worker
            # starts and never inspect widget state from that worker.
            progress.show()
            cancel_event = threading.Event()
            progress.canceled.connect(cancel_event.set)
            progress.rejected.connect(cancel_event.set)
            progress.destroyed.connect(lambda *_: cancel_event.set())
            with self._lock:
                self._active_progress = progress
        except Exception:
            self._clear_active_state()
            raise

        def _invoke_progress(method: str, *qargs) -> None:
            if not _is_qt_alive(progress):
                return
            try:
                QMetaObject.invokeMethod(progress, method, Qt.ConnectionType.QueuedConnection, *qargs)
            except RuntimeError as e:
                logger.debug(f"Progress dialog gone before {method}: {e}")

        def _invoke_parent(slot_name: str, *qargs) -> None:
            if not _is_qt_alive(self._parent):
                return
            try:
                QMetaObject.invokeMethod(self._parent, slot_name, Qt.ConnectionType.QueuedConnection, *qargs)
            except RuntimeError as e:
                logger.debug(f"Parent window gone before {slot_name}: {e}")

        def _apply_from_release(
            source: str,
            release_data: Dict[str, Any],
            dest_dir: Path,
            appimage_file: Path,
            appimage_name: str,
        ) -> str:
            """Apply delta or full download from a single resolved source ('github' or 'r2')."""
            rel_ch = _classify_release_entry(release_data)
            if rel_ch != channel:
                raise RuntimeError(
                    f"Refusing {source} release '{release_data.get('tag_name') or 'unknown'}' "
                    f"(channel '{rel_ch or 'unknown'}') while active channel is '{channel}'."
                )
            download_url, zsync_url, expected_sha = _extract_release_assets(release_data)
            sidecar_url, sha_advertised = _resolve_sha256_sidecar(release_data, download_url)
            if cancel_event.is_set():
                return "cancelled"

            _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)

            delta_ok = False
            tmp_path: Optional[Path] = None

            # 1. Bundled appimageupdatetool (GitHub transport only: explicit channel-verified -u tag)
            if source == "github":
                try:
                    updater = _find_bundled_or_system_tool("appimageupdatetool")
                    target_tag = str(release_data.get("tag_name") or (tag if tag != "latest" else "")).strip()
                    if not updater or not os.path.exists(appimage_path):
                        logger.info("appimageupdatetool unavailable; trying zsync/full download.")
                    elif not target_tag:
                        logger.info("No channel-verified release tag for appimageupdatetool -u; trying zsync/full download.")
                    else:
                        if not cancel_event.is_set():
                            _invoke_progress("setLabelText", Q_ARG(str, "Delta update via AppImageUpdate..."))
                        target = dest_dir / f"{appimage_name}.autoupdate"
                        shutil.copy2(appimage_path, target)
                        cmd = [
                            updater,
                            "-O",
                            "-u",
                            f"gh-releases-zsync|niwia|ASSella|{target_tag}|ASSella.AppImage.zsync",
                            str(target),
                        ]
                        rc, was_cancelled = _run_delta_subprocess(
                            cmd,
                            tool_name="appimageupdatetool",
                            cancel_event=cancel_event,
                            progress=progress,
                            timeout=3600,
                        )
                        if was_cancelled:
                            return "cancelled"
                        if rc == 0 and target.exists() and target.stat().st_size > 0:
                            _verify_sha256_file(
                                target,
                                expected_hex=expected_sha,
                                sidecar_url=sidecar_url,
                                cancel_event=cancel_event,
                                require_checksum=sha_advertised,
                            )
                            if cancel_event.is_set():
                                return "cancelled"
                            tmp_path = target
                            delta_ok = True
                            logger.info(
                                f"Delta update complete ({target.stat().st_size} bytes) via appimageupdatetool."
                            )
                            _cleanup_scratch_files(
                                dest_dir, appimage_name, installed_path=appimage_file, keep=target
                            )
                        else:
                            logger.warning(f"appimageupdatetool returned rc={rc}; falling back.")
                            _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)
                except Exception as e:
                    logger.warning(f"appimageupdatetool delta failed ({e}); falling back.")
                    _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)

            # 2. Bundled zsync2 CLI — works with both GitHub Releases and Cloudflare R2
            if not delta_ok:
                if cancel_event.is_set():
                    return "cancelled"
                try:
                    zsync_bin = (
                        _find_bundled_or_system_tool("zsync2")
                        or _find_bundled_or_system_tool("zsync")
                    )
                    if not zsync_bin:
                        logger.info("zsync binary not found; using full download.")
                    elif not os.path.exists(appimage_path):
                        logger.info("No installed AppImage to delta against; using full download.")
                    else:
                        if not cancel_event.is_set():
                            src_label = "Cloudflare R2" if source == "r2" else "zsync"
                            _invoke_progress("setLabelText", Q_ARG(str, f"Delta update via {src_label}..."))
                        seed_copy = dest_dir / f"{appimage_name}.zsync-seed"
                        zsync_out = dest_dir / f"{appimage_name}.zsync-out"
                        shutil.copy2(appimage_path, seed_copy)

                        # Pass the target AppImage URL explicitly via -u so zsync2
                        # overrides the URL inside .zsync regardless of host/mirror.
                        rc, was_cancelled = _run_delta_subprocess(
                            [
                                zsync_bin, "-u", download_url,
                                "-i", str(seed_copy), "-o", str(zsync_out),
                                zsync_url or f"{download_url}.zsync",
                            ],
                            tool_name="zsync",
                            cancel_event=cancel_event,
                            progress=progress,
                            timeout=1800,
                            cwd=str(dest_dir),
                        )
                        if was_cancelled:
                            return "cancelled"

                        if rc == 0 and zsync_out.exists() and zsync_out.stat().st_size > 0:
                            _verify_sha256_file(
                                zsync_out,
                                expected_hex=expected_sha,
                                sidecar_url=sidecar_url,
                                cancel_event=cancel_event,
                                require_checksum=sha_advertised,
                            )
                            if cancel_event.is_set():
                                return "cancelled"
                            tmp_path = zsync_out
                            delta_ok = True
                            logger.info(
                                f"zsync delta produced {zsync_out.stat().st_size} bytes ({source}); "
                                "skipping full download."
                            )
                            _cleanup_scratch_files(
                                dest_dir, appimage_name, installed_path=appimage_file, keep=zsync_out
                            )
                        else:
                            logger.warning(f"zsync delta failed (rc={rc}, source={source}); falling back to full download.")
                            _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)
                except Exception as e:
                    logger.warning(f"zsync delta attempt failed ({e}, source={source}); falling back to full download.")
                    _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)

            if cancel_event.is_set():
                return "cancelled"

            if delta_ok and tmp_path is not None:
                os.chmod(tmp_path, os.stat(tmp_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                shutil.move(str(tmp_path), appimage_path)
                _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)
                return "installed"

            logger.info(f"Self-update full download ({source}): {download_url}")
            tmp_path = dest_dir / f"{appimage_name}.part"

            dl_label = "Downloading full AppImage from R2..." if source == "r2" else "Downloading full AppImage..."
            _invoke_progress("setLabelText", Q_ARG(str, dl_label))
            _invoke_progress("setValue", Q_ARG(int, 10))

            req2 = urllib.request.Request(download_url, headers={"User-Agent": "ASSella-Updater"})
            with urllib.request.urlopen(req2, timeout=60) as response:
                total = int(response.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 512 * 1024
                with open(tmp_path, "wb") as f:
                    while True:
                        if cancel_event.is_set():
                            return "cancelled"
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0 and not cancel_event.is_set():
                            pct = 10 + int((downloaded / total) * 85)
                            _invoke_progress("setValue", Q_ARG(int, min(pct, 94)))
                            mb_done = downloaded / 1_048_576
                            mb_total = total / 1_048_576
                            _invoke_progress(
                                "setLabelText",
                                Q_ARG(str, f"Downloading... {mb_done:.1f} / {mb_total:.1f} MB"),
                            )

            if cancel_event.is_set():
                return "cancelled"

            os.chmod(tmp_path, os.stat(tmp_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            _verify_sha256_file(
                tmp_path,
                expected_hex=expected_sha,
                sidecar_url=sidecar_url,
                cancel_event=cancel_event,
                require_checksum=sha_advertised,
            )
            if cancel_event.is_set():
                return "cancelled"

            shutil.move(str(tmp_path), appimage_path)
            _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)
            return "installed"

        def _download_worker():
            appimage_file = Path(appimage_path)
            dest_dir = appimage_file.parent
            appimage_name = appimage_file.name
            logger.info(
                f"Self-update starting (server={update_server}, channel={channel}): "
                "attempting delta update, full download as fallback."
            )

            try:
                outcome = None
                gh_error: Optional[Exception] = None

                if update_server in ("auto", "github"):
                    try:
                        if not cancel_event.is_set():
                            _invoke_progress("setLabelText", Q_ARG(str, "Fetching release info from GitHub..."))
                        gh_release = _fetch_github_release_data(tag, channel=channel)
                        outcome = _apply_from_release("github", gh_release, dest_dir, appimage_file, appimage_name)
                    except Exception as e:
                        if cancel_event.is_set() or update_server == "github":
                            raise
                        gh_error = e
                        logger.warning(f"GitHub update failed ({e}); falling back to Cloudflare R2.")
                        _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)

                if outcome == "cancelled" or cancel_event.is_set():
                    return

                if outcome != "installed" and update_server in ("auto", "r2"):
                    if not cancel_event.is_set():
                        msg = (
                            "GitHub unavailable, fetching from Cloudflare R2..."
                            if gh_error is not None
                            else "Fetching release info from Cloudflare R2..."
                        )
                        _invoke_progress("setLabelText", Q_ARG(str, msg))
                        _invoke_progress("setValue", Q_ARG(int, 4))
                    try:
                        r2_release = _fetch_r2_release_data(channel, tag)
                        outcome = _apply_from_release("r2", r2_release, dest_dir, appimage_file, appimage_name)
                    except Exception as r2_err:
                        if gh_error is not None:
                            raise RuntimeError(f"GitHub failed ({gh_error}); R2 fallback failed ({r2_err})") from r2_err
                        raise

                if outcome == "cancelled" or cancel_event.is_set():
                    return

                _invoke_progress("setValue", Q_ARG(int, 100))
                _invoke_progress("finish")
                self._clear_active_state()
                _invoke_parent("_on_update_success")

            except Exception as e:
                logger.error(f"Self-update failed: {e}", exc_info=True)
                self._last_failed_server = update_server
                _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)
                _invoke_progress("finish")
                self._clear_active_state()
                _invoke_parent("_on_update_failed", Q_ARG(str, str(e)))
            finally:
                _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)
                self._clear_active_state()

        threading.Thread(target=_download_worker, daemon=True).start()
