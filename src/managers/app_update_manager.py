import logging
import os
import re
import shutil
import stat
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

from PyQt6.QtCore import QMetaObject, QObject, Qt, Q_ARG
from PyQt6.QtWidgets import QMessageBox, QProgressDialog

from utils.paths import Paths
from utils.version import app_version

logger = logging.getLogger(__name__)


def extract_semver(raw: str) -> str:
    """Strip build-date prefix (e.g. '20260608+ASSella-') returning just the version tag."""
    if "+ASSella-" in raw:
        return raw.split("+ASSella-", 1)[1].strip()
    return raw.strip()


class AppUpdateManager(QObject):
    """Handles self-update: release lookup, delta (appimageupdatetool/zsync) and
    full-download install. Lives outside MainWindow to keep that file manageable."""

    def __init__(self, parent_widget=None):
        super().__init__(parent_widget)
        self._parent = parent_widget

    def run_self_update(self) -> None:
        """Install update using delta ZSync (appimageupdatetool), with full-download fallback."""

        # Determine where the installed AppImage lives
        appimage_path = os.environ.get("APPIMAGE", "")
        default_appimage = os.path.expanduser("~/.local/share/ACCELA/ASSella.AppImage")
        if not appimage_path or not os.path.exists(appimage_path):
            appimage_path = default_appimage
        if not os.path.exists(appimage_path):
            QMessageBox.information(
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

        # Channel detection drives both the asset pick and the release lookup
        # below. Derive it here so the worker thread does not depend on a local
        # that only exists in check_tool_updates().
        local_clean = extract_semver(app_version)
        is_local_canary = (
            "canary" in local_clean.lower()
            or "testing" in local_clean.lower()
            or local_clean.startswith("3.")
        )

        reply = QMessageBox.question(
            self._parent, "Install Update",
            f"A new version of ASSella is available ({tag or 'the latest build'}).\n\n"
            "Would you like to download and install it now?\n"
            "The app will need to be restarted after the update.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        progress = QProgressDialog("Connecting to GitHub...", "Cancel", 0, 100, self._parent)
        progress.setWindowTitle("Downloading Update")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(2)

        def _download_worker():
            import json

            tmp_path = None
            logger.info("Self-update starting: attempting zsync delta, full download as fallback.")

            try:
                QMetaObject.invokeMethod(progress, "setLabelText",
                    Qt.ConnectionType.QueuedConnection, Q_ARG(str, "Fetching release info from GitHub..."))

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
                is_local_canary = (
                    "canary" in local_clean.lower()
                    or "testing" in local_clean.lower()
                    or local_clean.startswith("3.")
                )
                for asset in release_data.get("assets", []):
                    name = asset.get("name", "")
                    if "zsync" in name.lower():
                        continue
                    if is_local_canary:
                        if name.lower().endswith(".appimage"):
                            download_url = asset["browser_download_url"]
                            break
                    else:
                        if name == "ASSella.AppImage" or (name.endswith(".AppImage") and "canary" not in name.lower()):
                            download_url = asset["browser_download_url"]
                            break

                if not download_url:
                    raise RuntimeError("No AppImage asset found in the release.")

                # --- Delta (appimageupdatetool, then zsync) ----------------
                # Prefer the bundled AppImageUpdate, which reads the update string
                # embedded in the AppImage and resolves the correct channel-scoped
                # release tag on its own. Fall back to the zsync CLI if present,
                # then to a full download. Every failure mode is non-fatal.
                delta_ok = False
                dest_dir = Path(appimage_path).parent

                # 1. Bundled appimageupdatetool
                try:
                    # Locate the bundled updater. Paths.BASE_DIR is <root>/src in a source tree but
                    # the AppImage puts the launcher in <root>/bin, one level up, so
                    # check both. Walking up from __file__ used to assume the source
                    # layout and silently miss the bundled binary inside the AppImage.
                    bundled = next(
                        (
                            candidate
                            for candidate in (
                                Paths.BASE_DIR / "appimageupdatetool",
                                Paths.BASE_DIR.parent / "bin" / "appimageupdatetool",
                            )
                            if candidate.exists() and os.access(candidate, os.X_OK)
                        ),
                        None,
                    )
                    updater = str(bundled) if bundled else ""
                    if not updater:
                        found = shutil.which("appimageupdatetool")
                        updater = found or ""
                    if not updater or not os.path.exists(appimage_path):
                        logger.info("appimageupdatetool unavailable; trying zsync/full download.")
                    else:
                        QMetaObject.invokeMethod(progress, "setLabelText",
                            Qt.ConnectionType.QueuedConnection, Q_ARG(str, "Delta update via AppImageUpdate..."))
                        # appimageupdatetool rewrites its target in place and creates
                        # a .zs-old backup; work on a copy so a failed run cannot
                        # damage the working install.
                        target = dest_dir / "ASSella.AppImage.autoupdate"
                        shutil.copy2(appimage_path, target)
                        proc = subprocess.run(
                            [updater, "--self-update", str(target)],
                            capture_output=True, text=True, timeout=3600,
                        )
                        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
                        for line in out.splitlines():
                            if line.strip():
                                logger.info(f"[appimageupdatetool] {line}")
                        if proc.returncode == 0 and target.exists() and target.stat().st_size > 0:
                            tmp_path = target
                            delta_ok = True
                            logger.info(
                                f"Delta update complete ({target.stat().st_size} bytes) via appimageupdatetool."
                            )
                        else:
                            logger.warning(
                                f"appimageupdatetool returned rc={proc.returncode}; falling back."
                            )
                            for leftover in (target, Path(str(target) + ".zs-old")):
                                leftover.unlink(missing_ok=True)
                except Exception as e:
                    logger.warning(f"appimageupdatetool delta failed ({e}); falling back.")

                # 2. zsync CLI (system install, or already on PATH)
                if not delta_ok:
                    try:
                        zsync_bin = shutil.which("zsync")
                        if not zsync_bin:
                            logger.info("zsync binary not found; using full download.")
                        elif not os.path.exists(appimage_path):
                            logger.info("No installed AppImage to delta against; using full download.")
                        else:
                            seed_copy = dest_dir / "ASSella.AppImage.zsync-seed"
                            zsync_out = dest_dir / "ASSella.AppImage.zsync-out"
                            # zsync rewrites its input file in place, so seed it from
                            # a copy: a failed sync must not damage the working install.
                            shutil.copy2(appimage_path, seed_copy)

                            proc = subprocess.run(
                                [zsync_bin, "-i", str(seed_copy), "-o", str(zsync_out),
                                 f"{download_url}.zsync"],
                                capture_output=True, text=True, timeout=1800,
                            )
                            for line in ((proc.stdout or "") + (proc.stderr or "")).splitlines():
                                if line.strip():
                                    logger.info(f"[zsync] {line}")

                            if proc.returncode == 0 and zsync_out.exists() and zsync_out.stat().st_size > 0:
                                tmp_path = zsync_out
                                delta_ok = True
                                logger.info(
                                    f"zsync delta produced {zsync_out.stat().st_size} bytes; "
                                    "skipping full download."
                                )
                            else:
                                logger.warning(
                                    f"zsync delta failed (rc={proc.returncode}); falling back to full download."
                                )
                                zsync_out.unlink(missing_ok=True)
                            seed_copy.unlink(missing_ok=True)
                    except Exception as e:
                        logger.warning(f"zsync delta attempt failed ({e}); falling back to full download.")

                if delta_ok and tmp_path is not None:
                    try:
                        os.chmod(tmp_path, os.stat(tmp_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                        shutil.move(str(tmp_path), appimage_path)
                        QMetaObject.invokeMethod(progress, "setValue",
                            Qt.ConnectionType.QueuedConnection, Q_ARG(int, 100))
                        QMetaObject.invokeMethod(self._parent, "_on_update_success",
                            Qt.ConnectionType.QueuedConnection)
                        return
                    except Exception as e:
                        logger.error(f"Failed to install zsync delta: {e}", exc_info=True)
                        tmp_path = None

                logger.info(f"Self-update fallback: downloading from {download_url}")
                tmp_path = dest_dir / "ASSella.AppImage.part"

                QMetaObject.invokeMethod(progress, "setLabelText",
                    Qt.ConnectionType.QueuedConnection, Q_ARG(str, "Downloading full AppImage..."))
                QMetaObject.invokeMethod(progress, "setValue",
                    Qt.ConnectionType.QueuedConnection, Q_ARG(int, 10))

                req2 = urllib.request.Request(download_url, headers={"User-Agent": "ASSella-Updater"})
                with urllib.request.urlopen(req2, timeout=60) as response:
                    total = int(response.headers.get("Content-Length", 0))
                    downloaded = 0
                    chunk_size = 512 * 1024
                    with open(tmp_path, "wb") as f:
                        while True:
                            if progress.wasCanceled():
                                try:
                                    os.remove(tmp_path)
                                except OSError:
                                    pass
                                return
                            chunk = response.read(chunk_size)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                pct = 10 + int((downloaded / total) * 85)
                                QMetaObject.invokeMethod(progress, "setValue",
                                    Qt.ConnectionType.QueuedConnection, Q_ARG(int, min(pct, 94)))
                                mb_done = downloaded / 1_048_576
                                mb_total = total / 1_048_576
                                QMetaObject.invokeMethod(progress, "setLabelText",
                                    Qt.ConnectionType.QueuedConnection,
                                    Q_ARG(str, f"Downloading... {mb_done:.1f} / {mb_total:.1f} MB"))

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

                shutil.move(str(tmp_path), appimage_path)

                # Clean up any delta scratch files this run may have left behind.
                for leftover_name in (
                    "ASSella.AppImage.autoupdate",
                    "ASSella.AppImage.autoupdate.zs-old",
                    "ASSella.AppImage.zsync-seed",
                    "ASSella.AppImage.zsync-out",
                    "ASSella.AppImage.zsync-seed.zs-old",
                ):
                    (dest_dir / leftover_name).unlink(missing_ok=True)

                QMetaObject.invokeMethod(progress, "setValue",
                    Qt.ConnectionType.QueuedConnection, Q_ARG(int, 100))
                QMetaObject.invokeMethod(self._parent, "_on_update_success",
                    Qt.ConnectionType.QueuedConnection)

            except Exception as e:
                logger.error(f"Full download fallback also failed: {e}", exc_info=True)
                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                QMetaObject.invokeMethod(self._parent, "_on_update_failed",
                    Qt.ConnectionType.QueuedConnection, Q_ARG(str, str(e)))

        threading.Thread(target=_download_worker, daemon=True).start()
