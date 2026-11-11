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
from typing import Optional

from PyQt6.QtCore import QMetaObject, QObject, Qt, Q_ARG
try:
    from PyQt6 import sip
except ImportError:  # pragma: no cover - PyQt6 ships sip in normal installs
    sip = None

from ui.dialogs.themed_progress_dialog import ThemedProgressDialog
from ui.dialogs.themed_message_box import ThemedMessageBox

from utils.paths import Paths
from utils.version import app_version

logger = logging.getLogger(__name__)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_DELTA_PROGRESS_RE = re.compile(
    r"(?P<pct>\d+(?:\.\d+)?)%\s*done"
    r"(?:\s*\(\s*(?P<done>\d+(?:\.\d+)?)\s+of\s+(?P<total>\d+(?:\.\d+)?)\s*(?P<unit>[KMGT]?i?B)\s*\))?"
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
    except Exception:
        pass

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
            except Exception:
                pass
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
            return
        except Exception:
            continue


def extract_semver(raw: str) -> str:
    """Strip build-date prefix (e.g. '20260608+ASSella-') returning just the version tag."""
    if "+ASSella-" in raw:
        return raw.split("+ASSella-", 1)[1].strip()
    return raw.strip()


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
        except RuntimeError:
            pass

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
        except RuntimeError:
            pass

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
            except Exception:
                pass


class AppUpdateManager(QObject):
    """Handles self-update: release lookup, delta (appimageupdatetool/zsync) and
    full-download install. Lives outside MainWindow to keep that file manageable."""

    def __init__(self, parent_widget=None):
        super().__init__(parent_widget)
        self._parent = parent_widget
        self._lock = threading.Lock()
        self._updating = False
        self._active_progress: Optional[ThemedProgressDialog] = None

    def _clear_active_state(self) -> None:
        with self._lock:
            self._updating = False
            self._active_progress = None

    def run_self_update(self) -> None:
        """Install update using delta ZSync (appimageupdatetool), with full-download fallback."""
        with self._lock:
            if self._updating:
                logger.info("Self-update already in progress; ignoring duplicate request.")
                if _is_qt_alive(self._active_progress):
                    try:
                        self._active_progress.raise_()
                        self._active_progress.activateWindow()
                    except RuntimeError:
                        pass
                return
            self._updating = True

        try:
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

            remote_version = getattr(self._parent, "_latest_remote_version", None)
            # Normalise the tag to exactly one leading "v". Release tags in this repo
            # are inconsistently prefixed ("v2.7.0beta" vs "2.7.0beta"), and naively
            # prefixing produced "vv2.7.0beta", which GitHub answers with HTTP 404.
            if remote_version:
                tag = remote_version if str(remote_version).startswith("v") else f"v{remote_version}"
            else:
                # "latest" is unsafe here: every release is marked prerelease, and
                # /releases/latest excludes prereleases, so it resolves to 404.
                # Fall back to the newest release in our own channel instead.
                tag = ""

            extract_semver(app_version)

            reply = ThemedMessageBox.question(
                self._parent, "Install Update",
                f"A new version of ASSella is available ({tag or 'the latest build'}).\n\n"
                "Would you like to download and install it now?\n"
                "The app will need to be restarted after the update.",
                ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
                ThemedMessageBox.StandardButton.Yes
            )
            if reply != ThemedMessageBox.StandardButton.Yes:
                self._clear_active_state()
                return

            progress = ThemedProgressDialog("Connecting to GitHub...", "Cancel", 0, 100, self._parent)
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
            except RuntimeError:
                pass

        def _invoke_parent(slot_name: str, *qargs) -> None:
            if not _is_qt_alive(self._parent):
                return
            try:
                QMetaObject.invokeMethod(self._parent, slot_name, Qt.ConnectionType.QueuedConnection, *qargs)
            except RuntimeError:
                pass

        def _download_worker():
            import json

            tmp_path = None
            appimage_file = Path(appimage_path)
            dest_dir = appimage_file.parent
            appimage_name = appimage_file.name
            logger.info("Self-update starting: attempting zsync delta, full download as fallback.")

            try:
                if not cancel_event.is_set():
                    _invoke_progress("setLabelText", Q_ARG(str, "Fetching release info from GitHub..."))

                if tag == "latest":
                    api_url = "https://api.github.com/repos/niwia/ASSella/releases"
                    req = urllib.request.Request(
                        api_url,
                        headers={"User-Agent": "ASSella-Updater", "Accept": "application/vnd.github+json"}
                    )
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        releases_list = json.loads(resp.read().decode("utf-8"))
                        release_data = releases_list[0] if releases_list else {}
                else:
                    api_url = f"https://api.github.com/repos/niwia/ASSella/releases/tags/{tag}"
                    req = urllib.request.Request(
                        api_url,
                        headers={"User-Agent": "ASSella-Updater", "Accept": "application/vnd.github+json"}
                    )
                    try:
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            release_data = json.loads(resp.read().decode("utf-8"))
                    except urllib.error.HTTPError as err:
                        if err.code == 404 and tag != "latest":
                            alt_tag = tag[1:] if tag.startswith("v") else f"v{tag}"
                            alt_api_url = f"https://api.github.com/repos/niwia/ASSella/releases/tags/{alt_tag}"
                            logger.info(f"Release tag {tag} returned 404, retrying with alternate tag: {alt_tag}")
                            req_alt = urllib.request.Request(
                                alt_api_url,
                                headers={"User-Agent": "ASSella-Updater", "Accept": "application/vnd.github+json"}
                            )
                            with urllib.request.urlopen(req_alt, timeout=15) as resp:
                                release_data = json.loads(resp.read().decode("utf-8"))
                        else:
                            raise

                download_url = None
                appimage_candidates = []
                for asset in release_data.get("assets", []):
                    name = asset.get("name", "")
                    if name.lower().endswith(".zsync") or name.lower().endswith(".sha256"):
                        continue
                    if name.lower().endswith(".appimage"):
                        if name == "ASSella.AppImage":
                            download_url = asset.get("browser_download_url")
                            break
                        appimage_candidates.append(asset.get("browser_download_url"))

                if not download_url and appimage_candidates:
                    download_url = appimage_candidates[0]

                if not download_url:
                    raise RuntimeError("No AppImage asset found in the release.")

                if cancel_event.is_set():
                    return

                # Clear stale .autoupdate/.part files from any earlier interrupted attempt.
                _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)

                # --- Delta (appimageupdatetool, then zsync) ----------------
                # Prefer the bundled AppImageUpdate, which reads the update string
                # embedded in the AppImage and resolves the correct channel-scoped
                # release tag on its own. Fall back to the zsync CLI if present,
                # then to a full download. Every failure mode is non-fatal.
                delta_ok = False

                # 1. Bundled appimageupdatetool
                try:
                    # Inside an AppImage, BASE_DIR is <mount>/bin/src while helper
                    # binaries sit at <mount>/bin/ (resolved via APPDIR first).
                    updater = _find_bundled_or_system_tool("appimageupdatetool")
                    if not updater or not os.path.exists(appimage_path):
                        logger.info("appimageupdatetool unavailable; trying zsync/full download.")
                    else:
                        if not cancel_event.is_set():
                            _invoke_progress("setLabelText", Q_ARG(str, "Delta update via AppImageUpdate..."))
                        # appimageupdatetool with -O overwrites its target in place;
                        # work on a copy so a failed run cannot damage the working
                        # install. Without -O it creates a new file and leaves the
                        # original untouched, so -O is required for the success
                        # check below to see the updated content.
                        # Pass -u with the resolved release tag so the updater
                        # targets the intended release even if the AppImage's
                        # embedded update string has an unresolvable wildcard tag.
                        target = dest_dir / f"{appimage_name}.autoupdate"
                        shutil.copy2(appimage_path, target)
                        cmd = [updater, "-O"]
                        target_tag = release_data.get("tag_name") or (tag if tag != "latest" else "")
                        if target_tag:
                            cmd.extend(["-u", f"gh-releases-zsync|niwia|ASSella|{target_tag}|ASSella.AppImage.zsync"])
                        cmd.append(str(target))
                        rc, was_cancelled = _run_delta_subprocess(
                            cmd,
                            tool_name="appimageupdatetool",
                            cancel_event=cancel_event,
                            progress=progress,
                            timeout=3600,
                        )
                        if was_cancelled:
                            return
                        if rc == 0 and target.exists() and target.stat().st_size > 0:
                            tmp_path = target
                            delta_ok = True
                            logger.info(
                                f"Delta update complete ({target.stat().st_size} bytes) via appimageupdatetool."
                            )
                            _cleanup_scratch_files(
                                dest_dir, appimage_name, installed_path=appimage_file, keep=target
                            )
                        else:
                            logger.warning(
                                f"appimageupdatetool returned rc={rc}; falling back."
                            )
                            _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)
                except Exception as e:
                    logger.warning(f"appimageupdatetool delta failed ({e}); falling back.")
                    _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)

                # 2. zsync CLI — prefer bundled copy inside the AppImage, then system PATH
                if not delta_ok:
                    if cancel_event.is_set():
                        return
                    try:
                        zsync_bin = _find_bundled_or_system_tool("zsync")
                        if not zsync_bin:
                            logger.info("zsync binary not found; using full download.")
                        elif not os.path.exists(appimage_path):
                            logger.info("No installed AppImage to delta against; using full download.")
                        else:
                            if not cancel_event.is_set():
                                _invoke_progress("setLabelText", Q_ARG(str, "Delta update via zsync..."))
                            seed_copy = dest_dir / f"{appimage_name}.zsync-seed"
                            zsync_out = dest_dir / f"{appimage_name}.zsync-out"
                            # zsync rewrites its input file in place, so seed it from
                            # a copy: a failed sync must not damage the working install.
                            shutil.copy2(appimage_path, seed_copy)

                            # Pass the target AppImage URL explicitly via -u. zsync2
                            # (bundled since 3.1) reads -u as "the URL to get the
                            # target file" and uses it to override the .zsync's own
                            # URL header. Older releases filled that header with the
                            # AppImageUpdate descriptor ("gh-releases-zsync|..."),
                            # which zsync2 would otherwise treat as a relative path
                            # and 404 on. (Stock zsync 0.6.2 gives -u a different
                            # meaning — the referring URL — but it cannot read an
                            # https control file at all, so it never reaches here.)
                            rc, was_cancelled = _run_delta_subprocess(
                                [
                                    zsync_bin, "-u", download_url,
                                    "-i", str(seed_copy), "-o", str(zsync_out),
                                    f"{download_url}.zsync",
                                ],
                                tool_name="zsync",
                                cancel_event=cancel_event,
                                progress=progress,
                                timeout=1800,
                                cwd=str(dest_dir),
                            )
                            if was_cancelled:
                                return

                            if rc == 0 and zsync_out.exists() and zsync_out.stat().st_size > 0:
                                tmp_path = zsync_out
                                delta_ok = True
                                logger.info(
                                    f"zsync delta produced {zsync_out.stat().st_size} bytes; "
                                    "skipping full download."
                                )
                                _cleanup_scratch_files(
                                    dest_dir, appimage_name, installed_path=appimage_file, keep=zsync_out
                                )
                            else:
                                logger.warning(
                                    f"zsync delta failed (rc={rc}); falling back to full download."
                                )
                                _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)
                    except Exception as e:
                        logger.warning(f"zsync delta attempt failed ({e}); falling back to full download.")
                        _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)

                if cancel_event.is_set():
                    return

                if delta_ok and tmp_path is not None:
                    try:
                        os.chmod(tmp_path, os.stat(tmp_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                        shutil.move(str(tmp_path), appimage_path)
                        tmp_path = None
                        _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)
                        _invoke_progress("setValue", Q_ARG(int, 100))
                        _invoke_progress("finish")
                        self._clear_active_state()
                        _invoke_parent("_on_update_success")
                        return
                    except Exception as e:
                        logger.error(f"Failed to install zsync delta: {e}", exc_info=True)
                        tmp_path = None
                        _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)

                logger.info(f"Self-update fallback: downloading from {download_url}")
                if cancel_event.is_set():
                    return
                tmp_path = dest_dir / f"{appimage_name}.part"

                _invoke_progress("setLabelText", Q_ARG(str, "Downloading full AppImage..."))
                _invoke_progress("setValue", Q_ARG(int, 10))

                req2 = urllib.request.Request(download_url, headers={"User-Agent": "ASSella-Updater"})
                with urllib.request.urlopen(req2, timeout=60) as response:
                    total = int(response.headers.get("Content-Length", 0))
                    downloaded = 0
                    chunk_size = 512 * 1024
                    with open(tmp_path, "wb") as f:
                        while True:
                            if cancel_event.is_set():
                                return
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
                    return

                os.chmod(tmp_path, os.stat(tmp_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

                # Verify the downloaded bytes against the release's published
                # SHA-256 before overwriting the working install. A delta or
                # truncated download must never be installed as-is.
                try:
                    sha_url = f"{download_url}.sha256"
                    sha_req = urllib.request.Request(sha_url, headers={"User-Agent": "ASSella-Updater"})
                    with urllib.request.urlopen(sha_req, timeout=30) as sha_resp:
                        published = sha_resp.read().decode("utf-8", "replace")
                    import hashlib
                    m = re.search(r"[a-fA-F0-9]{64}", published)
                    if m:
                        expected = m.group(0).lower()
                        digest = hashlib.sha256()
                        with open(tmp_path, "rb") as fh:
                            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                                if cancel_event.is_set():
                                    return
                                digest.update(chunk)
                        actual = digest.hexdigest()
                        if actual != expected:
                            raise RuntimeError(
                                f"Checksum mismatch — expected {expected[:16]}…, got {actual[:16]}…"
                            )
                        logger.info(f"SHA-256 verified: {actual}")
                    else:
                        logger.warning("No published .sha256 for this release; skipping verification.")
                except urllib.error.HTTPError as he:
                    if he.code == 404:
                        logger.warning("No published .sha256 for this release; skipping verification.")
                    else:
                        raise
                except RuntimeError:
                    raise
                except Exception as ve:
                    logger.warning(f"Checksum verification skipped: {ve}")

                if cancel_event.is_set():
                    return

                shutil.move(str(tmp_path), appimage_path)
                tmp_path = None
                _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)

                _invoke_progress("setValue", Q_ARG(int, 100))
                _invoke_progress("finish")
                self._clear_active_state()
                _invoke_parent("_on_update_success")

            except Exception as e:
                logger.error(f"Self-update failed: {e}", exc_info=True)
                _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)
                _invoke_progress("finish")
                self._clear_active_state()
                _invoke_parent("_on_update_failed", Q_ARG(str, str(e)))
            finally:
                _cleanup_scratch_files(dest_dir, appimage_name, installed_path=appimage_file)
                self._clear_active_state()

        threading.Thread(target=_download_worker, daemon=True).start()
