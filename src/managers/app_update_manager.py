import hashlib
import json
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
from typing import Optional, Tuple

from PyQt6.QtCore import QMetaObject, QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QMessageBox, QProgressDialog

from utils.settings import get_settings
from utils.version import app_version

logger = logging.getLogger(__name__)


def extract_semver(raw: str) -> str:
    """Strip build-date prefix (e.g. '20260608+ASSella-') returning just the version tag."""
    if "+ASSella-" in raw:
        return raw.split("+ASSella-", 1)[1].strip()
    return raw.strip()


def parse_version(v_str: str) -> Tuple[int, int, int, int, int]:
    """Parse version string into a comparable tuple (major, minor, patch, pre_val, pre_num)."""
    v_str = v_str.lstrip("v").strip()
    m = re.match(
        r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[.-]?(dev|alpha|beta|rc|hotfix)[.-]?(\d*)|\-([a-zA-Z0-9.]+))?$",
        v_str,
        re.IGNORECASE,
    )
    if m:
        major = int(m.group(1) or 0)
        minor = int(m.group(2) or 0)
        patch = int(m.group(3) or 0)
        pre_type = m.group(4) or ""
        pre_num_str = m.group(5) or ""
        pre_extra = m.group(6) or ""
        pre_weights = {"hotfix": 1, "rc": -1, "beta": -2, "alpha": -3, "dev": -4}
        if pre_type:
            pre_val = pre_weights.get(pre_type.lower(), 1 if "hotfix" in pre_type.lower() else -4)
            pre_num = int(pre_num_str) if pre_num_str else 1
        elif pre_extra:
            if "hotfix" in pre_extra.lower():
                hm = re.search(r"\d+", pre_extra)
                pre_val = 1
                pre_num = int(hm.group(0)) if hm else 1
            else:
                pre_val = -4
                pre_num = 0
        else:
            pre_val = 0
            pre_num = 0
        return (major, minor, patch, pre_val, pre_num)
    return (0, 0, 0, 0, 0)


class AppUpdateManager(QObject):
    """Manages ASSella self-updates, releases checking, and AppImage delta/full downloads."""

    update_checked = pyqtSignal(str, str)  # status ("update_available", "up_to_date", "offline"), remote_version
    update_started = pyqtSignal()
    update_progress = pyqtSignal(int, str)  # percentage (0-100), message
    update_completed = pyqtSignal(bool, str)  # success, message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_checking = False
        self.status = "idle"
        self.latest_remote_version = ""
        self.download_url = ""
        self.sha256_url = ""

    def get_active_channel(self, local_ver_str: str) -> str:
        settings = get_settings()
        saved = ""
        if settings:
            saved = str(settings.value("app_update_channel", "auto") or "auto").lower()
        if saved in ("canary", "beta", "stable"):
            return saved
        lv = local_ver_str.lower()
        if any(x in lv for x in ("canary", "testing")):
            return "canary"
        if any(x in lv for x in ("beta", "dev", "rc")):
            return "beta"
        return "stable"

    @staticmethod
    def tag_matches_channel(tag_name: str, channel: str) -> bool:
        tv = tag_name.lower()
        if channel == "canary":
            return any(x in tv for x in ("canary", "testing"))
        if channel == "beta":
            return any(x in tv for x in ("beta", "dev", "rc"))
        is_c = any(x in tv for x in ("canary", "testing"))
        is_b = any(x in tv for x in ("beta", "dev", "rc"))
        return ("stable" in tv) or (not is_c and not is_b)

    @staticmethod
    def asset_matches_channel(asset_name: str, channel: str) -> bool:
        an = asset_name.lower()
        if not an.endswith(".appimage") or "zsync" in an:
            return False
        if channel == "canary":
            return any(x in an for x in ("canary", "testing")) or not any(x in an for x in ("beta", "stable"))
        if channel == "beta":
            return any(x in an for x in ("beta", "dev", "rc")) or (an == "assella.appimage")
        return ("stable" in an) or (an == "assella.appimage") or not any(x in an for x in ("canary", "testing", "beta", "dev", "rc"))

    def check_updates_async(self) -> None:
        """Start a background thread to check for tool self-updates from GitHub."""
        if self.is_checking:
            logger.debug("Tool update check already in progress, skipping.")
            return

        self.is_checking = True
        self.status = "checking"

        def _check_sync():
            status = "offline"
            remote_clean = None
            try:
                local_clean = extract_semver(app_version)
                active_channel = self.get_active_channel(local_clean)
                logger.info(f"Checking for updates on channel: '{active_channel}' (local: '{local_clean}')")

                # 1. Primary check: GitHub Releases API
                try:
                    api_url = "https://api.github.com/repos/niwia/ASSella/releases"
                    req = urllib.request.Request(
                        api_url,
                        headers={"User-Agent": "ASSella-Updater", "Accept": "application/vnd.github+json"},
                    )
                    with urllib.request.urlopen(req, timeout=10) as response:
                        releases = json.loads(response.read().decode("utf-8"))
                        for r in releases:
                            tag = r.get("tag_name", "").strip()
                            title = r.get("name", "").strip()
                            if not (self.tag_matches_channel(tag, active_channel) or self.tag_matches_channel(title, active_channel)):
                                continue

                            assets = [a.get("name", "") for a in r.get("assets", [])]
                            has_appimage = any(self.asset_matches_channel(a, active_channel) for a in assets)

                            if has_appimage:
                                remote_clean = extract_semver(tag)
                                break
                except Exception as api_err:
                    logger.debug(f"GitHub Releases API check error: {api_err}")

                # 2. Fallback check: raw version file on GitHub branch
                if not remote_clean:
                    if active_channel == "canary":
                        branch = "canary"
                    elif active_channel == "beta":
                        branch = "beta"
                    else:
                        branch = "main"
                    url = f"https://raw.githubusercontent.com/niwia/ASSella/{branch}/src/res/version"
                    logger.info(f"Checking for tool updates from raw branch: {branch}")
                    req = urllib.request.Request(url, headers={"User-Agent": "ASSella-Updater"})
                    with urllib.request.urlopen(req, timeout=10) as response:
                        remote_raw = response.read().decode("utf-8").strip()
                        remote_clean = extract_semver(remote_raw)

                logger.info(f"Tool update check: remote='{remote_clean}', local='{local_clean}'")
                if remote_clean:
                    if parse_version(remote_clean) > parse_version(local_clean):
                        logger.info(f"Tool update available: {remote_clean} (current: {local_clean})")
                        status = "update_available"
                        self.latest_remote_version = remote_clean
                    else:
                        logger.info(f"Tool is up to date ({local_clean})")
                        status = "up_to_date"
                else:
                    status = "offline"
            except Exception as e:
                logger.warning(f"Failed to check tool updates from GitHub: {e}")
                status = "offline"
            finally:
                self.is_checking = False
                self.status = status
                self.update_checked.emit(status, remote_clean or "")

        threading.Thread(target=_check_sync, daemon=True).start()

    def run_self_update(self, parent_widget=None) -> None:
        """Prompt user and install update using delta ZSync or full AppImage download fallback."""
        appimage_path = os.environ.get("APPIMAGE", "")
        default_appimage = os.path.expanduser("~/.local/share/ACCELA/ASSella.AppImage")
        if not appimage_path or not os.path.exists(appimage_path):
            appimage_path = default_appimage
        if not os.path.exists(appimage_path):
            if parent_widget:
                QMessageBox.information(
                    parent_widget, "Self-Update",
                    "Could not locate the installed ASSella.AppImage to update."
                )
            return

        tag = f"v{self.latest_remote_version}" if self.latest_remote_version else "latest"

        if parent_widget:
            reply = QMessageBox.question(
                parent_widget, "Install Update",
                f"A new version of ASSella is available ({tag}).\n\n"
                "Would you like to download and install it now?\n"
                "The app will need to be restarted after the update.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        progress = QProgressDialog("Connecting to GitHub...", "Cancel", 0, 100, parent_widget)
        progress.setWindowTitle("Downloading Update")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(2)

        def _download_worker():
            tmp_path = None
            local_clean = extract_semver(app_version)
            active_ch = self.get_active_channel(local_clean)
            logger.info(f"Self-update starting: channel={active_ch}, tag={tag}")

            dest_dir = Path(appimage_path).parent

            # ── Phase 1: Try zsync delta update ──────────────────────────────
            update_tool = shutil.which("appimageupdatetool") or shutil.which("AppImageUpdate")
            if update_tool:
                try:
                    QMetaObject.invokeMethod(progress, "setLabelText",
                        Qt.ConnectionType.QueuedConnection, "Attempting delta (zsync) update...")
                    QMetaObject.invokeMethod(progress, "setValue",
                        Qt.ConnectionType.QueuedConnection, 5)
                    result = subprocess.run(
                        [update_tool, appimage_path],
                        capture_output=True, text=True, timeout=300
                    )
                    if result.returncode == 0:
                        logger.info("ZSync delta update succeeded.")
                        QMetaObject.invokeMethod(progress, "setValue",
                            Qt.ConnectionType.QueuedConnection, 100)
                        self._handle_update_success(parent_widget)
                        return
                    else:
                        logger.warning(f"ZSync failed (rc={result.returncode}), falling back to full download.")
                except Exception as zsync_err:
                    logger.warning(f"ZSync update tool error: {zsync_err}. Falling back to full download.")
            else:
                logger.info("appimageupdatetool not found — using full download path.")

            # ── Phase 2: Resolve download URL ────────────────────────────────
            try:
                QMetaObject.invokeMethod(progress, "setLabelText",
                    Qt.ConnectionType.QueuedConnection, "Fetching release info from GitHub...")
                QMetaObject.invokeMethod(progress, "setValue",
                    Qt.ConnectionType.QueuedConnection, 8)

                release_data = {}
                try:
                    api_url = "https://api.github.com/repos/niwia/ASSella/releases"
                    req = urllib.request.Request(api_url,
                        headers={"User-Agent": "ASSella-Updater", "Accept": "application/vnd.github+json"})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        releases_list = json.loads(resp.read().decode("utf-8"))
                        for rel in releases_list:
                            t = rel.get("tag_name", "").lower()
                            if active_ch == "canary" and any(x in t for x in ("canary", "testing")):
                                release_data = rel
                                break
                            elif active_ch == "beta" and any(x in t for x in ("beta", "dev", "rc")):
                                release_data = rel
                                break
                            elif active_ch == "stable":
                                if not any(x in t for x in ("canary", "testing", "beta", "dev", "rc")):
                                    release_data = rel
                                    break
                        if not release_data and releases_list:
                            release_data = releases_list[0]
                except Exception as err:
                    logger.warning(f"Failed to fetch releases list, trying tag directly: {err}")
                    req_tag = urllib.request.Request(
                        f"https://api.github.com/repos/niwia/ASSella/releases/tags/{tag}",
                        headers={"User-Agent": "ASSella-Updater", "Accept": "application/vnd.github+json"}
                    )
                    with urllib.request.urlopen(req_tag, timeout=15) as resp:
                        release_data = json.loads(resp.read().decode("utf-8"))

                download_url = None
                sha256_url = None
                assets = release_data.get("assets", [])
                for asset in assets:
                    aname = asset.get("name", "")
                    if self.asset_matches_channel(aname, active_ch):
                        download_url = asset.get("browser_download_url")
                    elif aname.endswith(".sha256") and "zsync" not in aname:
                        sha256_url = asset.get("browser_download_url")

                if not download_url:
                    for asset in assets:
                        aname = asset.get("name", "")
                        if aname.endswith(".AppImage") and "zsync" not in aname:
                            download_url = asset.get("browser_download_url")
                            break

                if not download_url:
                    raise RuntimeError(f"Could not find an AppImage asset in release '{tag}' on channel '{active_ch}'.")

                # ── Phase 3: Download full AppImage with progress ─────────────
                tmp_path = dest_dir / "ASSella.AppImage.part"

                QMetaObject.invokeMethod(progress, "setLabelText",
                    Qt.ConnectionType.QueuedConnection, "Downloading full AppImage...")
                QMetaObject.invokeMethod(progress, "setValue",
                    Qt.ConnectionType.QueuedConnection, 12)

                req2 = urllib.request.Request(download_url, headers={"User-Agent": "ASSella-Updater"})
                with urllib.request.urlopen(req2, timeout=120) as response:
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
                                pct = 12 + int((downloaded / total) * 78)
                                QMetaObject.invokeMethod(progress, "setValue",
                                    Qt.ConnectionType.QueuedConnection, min(pct, 90))
                                mb_done = downloaded / 1_048_576
                                mb_total = total / 1_048_576
                                QMetaObject.invokeMethod(progress, "setLabelText",
                                    Qt.ConnectionType.QueuedConnection,
                                    f"Downloading... {mb_done:.1f} / {mb_total:.1f} MB")

                # ── Phase 4: SHA-256 integrity verification ───────────────────
                QMetaObject.invokeMethod(progress, "setLabelText",
                    Qt.ConnectionType.QueuedConnection, "Verifying integrity...")
                QMetaObject.invokeMethod(progress, "setValue",
                    Qt.ConnectionType.QueuedConnection, 92)

                if sha256_url:
                    try:
                        req_sha = urllib.request.Request(sha256_url, headers={"User-Agent": "ASSella-Updater"})
                        with urllib.request.urlopen(req_sha, timeout=20) as resp_sha:
                            sha_content = resp_sha.read().decode("utf-8").strip()
                        expected_sha = sha_content.split()[0].lower() if sha_content else ""
                        if expected_sha and len(expected_sha) == 64:
                            h = hashlib.sha256()
                            with open(tmp_path, "rb") as f:
                                for chunk in iter(lambda: f.read(1 << 20), b""):
                                    h.update(chunk)
                            actual_sha = h.hexdigest().lower()
                            if actual_sha != expected_sha:
                                try:
                                    os.remove(tmp_path)
                                except OSError:
                                    pass
                                raise RuntimeError(
                                    f"SHA-256 mismatch — download may be corrupt.\n"
                                    f"Expected: {expected_sha}\nGot: {actual_sha}"
                                )
                            logger.info(f"SHA-256 verified OK: {actual_sha}")
                    except RuntimeError:
                        raise
                    except Exception as sha_err:
                        logger.warning(f"SHA-256 verification skipped: {sha_err}")

                # ── Phase 5: Atomic replace ──────────────────────────────────
                QMetaObject.invokeMethod(progress, "setLabelText",
                    Qt.ConnectionType.QueuedConnection, "Installing update...")
                os.chmod(tmp_path, os.stat(tmp_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                bak_path = dest_dir / "ASSella.AppImage.bak"
                try:
                    if Path(appimage_path).exists():
                        shutil.copy2(appimage_path, bak_path)
                except Exception:
                    pass
                shutil.move(str(tmp_path), appimage_path)

                QMetaObject.invokeMethod(progress, "setValue",
                    Qt.ConnectionType.QueuedConnection, 100)
                self._handle_update_success(parent_widget)

            except Exception as e:
                logger.error(f"Update failed: {e}", exc_info=True)
                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                self._handle_update_failure(parent_widget, str(e))

        threading.Thread(target=_download_worker, daemon=True).start()

    def _handle_update_success(self, parent_widget=None) -> None:
        self.update_completed.emit(True, "Update installed successfully.")
        if parent_widget:
            def _show_msg():
                QMessageBox.information(
                    parent_widget,
                    "Update Installed",
                    "The update has been downloaded and installed successfully.\n\n"
                    "Please restart ASSella to use the new version.",
                )
            QMetaObject.invokeMethod(parent_widget, _show_msg, Qt.ConnectionType.QueuedConnection)

    def _handle_update_failure(self, parent_widget=None, error_msg: str = "") -> None:
        self.update_completed.emit(False, error_msg)
        if parent_widget:
            def _show_err():
                QMessageBox.warning(
                    parent_widget,
                    "Update Failed",
                    f"Failed to download or apply the update.\n\nError: {error_msg}"
                )
            QMetaObject.invokeMethod(parent_widget, _show_err, Qt.ConnectionType.QueuedConnection)
