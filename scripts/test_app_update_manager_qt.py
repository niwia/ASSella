#!/usr/bin/env python3
"""Regression checks for the AppImage updater's worker-to-Qt progress bridge."""

import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QMetaObject, Qt, QUrl, Q_ARG, pyqtSlot
from PyQt6.QtWidgets import QApplication, QWidget

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ui.dialogs.themed_progress_dialog import ThemedProgressDialog
from ui.dialogs.themed_message_box import ThemedMessageBox
from ui.main_window import MainWindow
from managers.app_update_manager import AppUpdateManager, _resolve_appimage_path


class _FakeSettings:
    """Hermetic in-memory settings stub so tests never read ~/.config."""

    def __init__(self, values=None):
        self._values = dict(values or {})

    def value(self, key, default=None, type=None):
        val = self._values.get(key, default)
        if type is bool and not isinstance(val, bool):
            return str(val).lower() in ("1", "true", "yes")
        return val


def _make_parent(remote_version="3.0.4", update_server="auto", update_channel="stable"):
    class Parent(QWidget):
        def __init__(self):
            super().__init__()
            self._latest_remote_version = remote_version
            self.settings = _FakeSettings({
                "update_server": update_server,
                "update_channel": update_channel,
            })
            self.finished = threading.Event()
            self.succeeded = False
            self.error = None

        @pyqtSlot()
        def _on_update_success(self):
            self.succeeded = True
            self.finished.set()

        @pyqtSlot(str)
        def _on_update_failed(self, error):
            self.error = error
            self.finished.set()

    return Parent()


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        if size < 0:
            payload, self.payload = self.payload, b""
        else:
            payload, self.payload = self.payload[:size], self.payload[size:]
        return payload


def _release_urlopen(tag_name="v3.0.4"):
    def _fake_urlopen(request, timeout=None):
        release = {
            "tag_name": tag_name,
            "assets": [{
                "name": "ASSella.AppImage",
                "browser_download_url": "https://release.test/ASSella.AppImage",
            }],
        }
        return _FakeResponse(json.dumps(release).encode())

    return _fake_urlopen


def _pump_until(app, condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


class AppUpdateProgressBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_progress_methods_are_queued_invokable_slots(self):
        progress = ThemedProgressDialog("starting", None, 0, 100)
        QMetaObject.invokeMethod(
            progress,
            "setLabelText",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, "Fetching release info from GitHub..."),
        )
        QMetaObject.invokeMethod(
            progress,
            "setValue",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, 42),
        )
        progress.show()
        QMetaObject.invokeMethod(
            progress, "finish", Qt.ConnectionType.QueuedConnection
        )
        self.app.processEvents()
        self.assertEqual(progress.label.text(), "Fetching release info from GitHub...")
        self.assertEqual(progress.progress_bar.value(), 42)
        self.assertFalse(progress.isVisible())

    def test_missing_delta_tools_fall_back_to_verified_full_download(self):
        parent = _make_parent("3.0.3")
        payload = b"complete fake AppImage payload"

        def fake_urlopen(request, timeout=None):
            url = request.full_url
            if "api.github.com" in url:
                release = {
                    "assets": [{
                        "name": "ASSella.AppImage",
                        "browser_download_url": "https://release.test/ASSella.AppImage",
                    }]
                }
                return _FakeResponse(json.dumps(release).encode())
            if url.endswith(".sha256"):
                return _FakeResponse(hashlib.sha256(payload).hexdigest().encode())
            return _FakeResponse(payload)

        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"old AppImage")
            stale_part = Path(temp_dir) / "ASSella.AppImage.autoupdate.part"
            stale_part.write_bytes(b"stale partial data")
            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen),
                patch("managers.app_update_manager.shutil.which", return_value=None),
            ):
                AppUpdateManager(parent).run_self_update()
                _pump_until(self.app, parent.finished.is_set)

            self.assertTrue(parent.finished.is_set(), "updater worker did not finish")
            self.assertIsNone(parent.error)
            self.assertEqual(appimage.read_bytes(), payload)
            self.assertFalse(stale_part.exists())

    def test_appimageupdatetool_invoked_with_overwrite_and_target(self):
        parent = _make_parent("3.0.4")
        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"old AppImage")
            fake_updater = Path(temp_dir) / "fake_appimageupdatetool"
            fake_updater.write_text("#!/bin/sh\nexit 0\n")
            fake_updater.chmod(0o755)

            invoked_cmds = []

            class FakePopen:
                def __init__(self, cmd, *args, **kwargs):
                    invoked_cmds.append(cmd)
                    target_path = Path(cmd[-1])
                    target_path.write_bytes(b"new updated AppImage")
                    Path(f"{target_path}.zs-old").write_bytes(b"old seed")
                    Path(f"{target_path}.part").write_bytes(b"leftover part")
                    self.stdout = io.BytesIO(
                        b"Checking for updates...\n"
                        b"\x1b[2K\r50.00% done (102.50 of 205.00 MiB)...\r"
                        b"\x1b[2K\r100.00% done (205.00 of 205.00 MiB)...\n"
                        b"zsync2: checksum matches OK\n"
                    )
                    self.returncode = 0
                    self.pid = 999999

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    return self.returncode

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=_release_urlopen("v3.0.4")),
                patch("managers.app_update_manager.shutil.which", return_value=str(fake_updater)),
                patch("managers.app_update_manager.subprocess.Popen", side_effect=FakePopen),
            ):
                AppUpdateManager(parent).run_self_update()
                _pump_until(self.app, parent.finished.is_set)

            self.assertTrue(parent.finished.is_set())
            self.assertIsNone(parent.error)
            self.assertEqual(appimage.read_bytes(), b"new updated AppImage")
            self.assertFalse((Path(temp_dir) / "ASSella.AppImage.autoupdate").exists())
            self.assertFalse((Path(temp_dir) / "ASSella.AppImage.autoupdate.zs-old").exists())
            self.assertFalse((Path(temp_dir) / "ASSella.AppImage.autoupdate.part").exists())
            self.assertTrue(len(invoked_cmds) > 0)
            self.assertIn("-O", invoked_cmds[0])
            self.assertIn("-u", invoked_cmds[0])
            self.assertIn("gh-releases-zsync|niwia|ASSella|v3.0.4|ASSella.AppImage.zsync", invoked_cmds[0])
            self.assertNotIn("--self-update", invoked_cmds[0])

    def test_delta_streams_live_progress_over_pipe(self):
        parent = _make_parent("3.1.0")
        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"old AppImage")

            fake_updater = Path(temp_dir) / "stream_updater.py"
            fake_updater.write_text(
                f"#!{sys.executable}\n"
                "import pathlib, sys, time\n"
                "target = pathlib.Path(sys.argv[-1])\n"
                "sys.stdout.write('zsync2: Reading seed file: ' + str(target) + '\\n'); sys.stdout.flush()\n"
                "time.sleep(0.05)\n"
                "sys.stdout.write('\\x1b[2K\\r25.00% done (51.25 of 205.00 MiB)...'); sys.stdout.flush()\n"
                "time.sleep(0.20)\n"
                "sys.stdout.write('\\x1b[2K\\r75.00% done (153.75 of 205.00 MiB)...'); sys.stdout.flush()\n"
                "time.sleep(0.20)\n"
                "sys.stdout.write('\\nzsync2: Verifying downloaded file\\n'); sys.stdout.flush()\n"
                "target.write_bytes(b'streamed 3.1.0 AppImage')\n"
                "sys.stdout.write('\\x1b[2K\\r100.00% done (205.00 of 205.00 MiB)...\\n'); sys.stdout.flush()\n"
            )
            fake_updater.chmod(0o755)

            seen_values = []
            seen_labels = []
            mgr = AppUpdateManager(parent)

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=_release_urlopen("v3.1.0")),
                patch("managers.app_update_manager.shutil.which", return_value=str(fake_updater)),
            ):
                mgr.run_self_update()
                deadline = time.monotonic() + 8.0
                while not parent.finished.is_set() and time.monotonic() < deadline:
                    self.app.processEvents()
                    prog = mgr._active_progress
                    if prog is not None:
                        seen_values.append(prog.progress_bar.value())
                        seen_labels.append(prog.label.text())
                    time.sleep(0.02)

            self.assertTrue(parent.finished.is_set(), "stream updater did not finish")
            self.assertTrue(parent.succeeded)
            self.assertEqual(appimage.read_bytes(), b"streamed 3.1.0 AppImage")
            # Confirm progress bar moved past initial 2% mid-stream before 100%.
            self.assertTrue(any(10 < v < 100 for v in seen_values), f"unexpected progress values: {seen_values}")
            self.assertTrue(
                any("MiB)" in lbl for lbl in seen_labels),
                f"expected streamed MiB progress label, got: {seen_labels}",
            )

    def test_cancel_terminates_delta_subprocess_and_cleans_part_files(self):
        parent = _make_parent("3.1.0")
        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"original 3.0.6 bytes")
            pid_file = Path(temp_dir) / "updater.pid"

            fake_updater = Path(temp_dir) / "blocking_updater.py"
            fake_updater.write_text(
                f"#!{sys.executable}\n"
                "import os, pathlib, sys, time\n"
                "target = pathlib.Path(sys.argv[-1])\n"
                f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
                "pathlib.Path(str(target) + '.part').write_bytes(b'partial download')\n"
                "sys.stdout.write('\\x1b[2K\\r35.00% done (70.00 of 200.00 MiB)...'); sys.stdout.flush()\n"
                "time.sleep(30)\n"
            )
            fake_updater.chmod(0o755)

            mgr = AppUpdateManager(parent)
            fallback_called = False

            def fake_urlopen(request, timeout=None):
                nonlocal fallback_called
                if "api.github.com" in request.full_url:
                    return _release_urlopen("v3.1.0")(request, timeout)
                fallback_called = True
                return _FakeResponse(b"should not download")

            def _which(name):
                return str(fake_updater) if name == "appimageupdatetool" else None

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen),
                patch("managers.app_update_manager.shutil.which", side_effect=_which),
            ):
                mgr.run_self_update()
                _pump_until(
                    self.app,
                    lambda: pid_file.exists() and (Path(temp_dir) / "ASSella.AppImage.autoupdate.part").exists(),
                    timeout=5.0,
                )
                self.assertTrue(pid_file.exists())
                child_pid = int(pid_file.read_text().strip())

                # Trigger user Cancel via the active progress dialog.
                self.assertIsNotNone(mgr._active_progress)
                mgr._active_progress.cancel()

                _pump_until(self.app, lambda: not mgr._updating, timeout=5.0)

            self.assertFalse(mgr._updating, "updater remained locked after cancellation")
            self.assertFalse(fallback_called, "cancelled delta fell through to full download")
            self.assertFalse(parent.finished.is_set(), "cancel should not emit success or failure dialog")
            self.assertEqual(appimage.read_bytes(), b"original 3.0.6 bytes")
            self.assertFalse((Path(temp_dir) / "ASSella.AppImage.autoupdate").exists())
            self.assertFalse((Path(temp_dir) / "ASSella.AppImage.autoupdate.part").exists())

            # Verify child process was actually killed and reaped (doesn't exist).
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)

    def test_concurrent_run_self_update_is_ignored(self):
        parent = _make_parent("3.1.0")
        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"old bytes")
            fake_updater = Path(temp_dir) / "slow_updater.py"
            fake_updater.write_text(
                f"#!{sys.executable}\n"
                "import pathlib, sys, time\n"
                "sys.stdout.write('\\x1b[2K\\r20.00% done (40.00 of 200.00 MiB)...'); sys.stdout.flush()\n"
                "time.sleep(0.35)\n"
                "pathlib.Path(sys.argv[-1]).write_bytes(b'updated bytes')\n"
            )
            fake_updater.chmod(0o755)

            question_calls = []

            def fake_question(*args, **kwargs):
                question_calls.append(1)
                return ThemedMessageBox.StandardButton.Yes

            mgr = AppUpdateManager(parent)
            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch("managers.app_update_manager.ThemedMessageBox.question", side_effect=fake_question),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=_release_urlopen("v3.1.0")),
                patch("managers.app_update_manager.shutil.which", return_value=str(fake_updater)),
            ):
                mgr.run_self_update()
                # Immediate second invocation while first update is in progress.
                mgr.run_self_update()
                self.assertEqual(len(question_calls), 1)
                _pump_until(self.app, parent.finished.is_set, timeout=5.0)

            self.assertTrue(parent.succeeded)
            self.assertEqual(len(question_calls), 1)
            self.assertEqual(appimage.read_bytes(), b"updated bytes")

    def test_symlinked_appimage_updates_target_and_preserves_symlink(self):
        parent = _make_parent("3.1.0")
        with tempfile.TemporaryDirectory() as temp_dir:
            real_appimage = Path(temp_dir) / "ASSella.AppImage"
            real_appimage.write_bytes(b"old target bytes")
            symlink_appimage = Path(temp_dir) / "ACCELA.AppImage"
            symlink_appimage.symlink_to(real_appimage.name)

            fake_updater = Path(temp_dir) / "fake_updater"
            fake_updater.write_text("#!/bin/sh\nexit 0\n")
            fake_updater.chmod(0o755)

            class FakePopen:
                def __init__(self, cmd, *args, **kwargs):
                    Path(cmd[-1]).write_bytes(b"new target bytes")
                    self.stdout = io.BytesIO(b"Update successful.\n")
                    self.returncode = 0
                    self.pid = 999998

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    return self.returncode

            with (
                patch.dict(os.environ, {"APPIMAGE": str(symlink_appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=_release_urlopen("v3.1.0")),
                patch("managers.app_update_manager.shutil.which", return_value=str(fake_updater)),
                patch("managers.app_update_manager.subprocess.Popen", side_effect=FakePopen),
            ):
                AppUpdateManager(parent).run_self_update()
                _pump_until(self.app, parent.finished.is_set)

            self.assertTrue(parent.succeeded)
            self.assertTrue(symlink_appimage.is_symlink(), "launcher symlink should remain a symlink")
            self.assertEqual(real_appimage.read_bytes(), b"new target bytes")

    def test_resolve_appimage_path_ignores_apprun_sentinel_and_follows_argv0_symlink(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            real_appimage = Path(temp_dir) / "ASSella.AppImage"
            real_appimage.write_bytes(b"img")
            symlink_appimage = Path(temp_dir) / "ACCELA.AppImage"
            symlink_appimage.symlink_to(real_appimage.name)

            with patch.dict(os.environ, {"APPIMAGE": "1", "ARGV0": str(symlink_appimage)}, clear=False):
                self.assertEqual(_resolve_appimage_path(), str(real_appimage.resolve()))

    def test_zsync_fallback_cleans_failed_autoupdate_and_streams_delta(self):
        parent = _make_parent("3.1.0")
        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"old bytes")
            fake_updater = Path(temp_dir) / "fake_updater"
            fake_zsync = Path(temp_dir) / "fake_zsync"
            for bin_path in (fake_updater, fake_zsync):
                bin_path.write_text("#!/bin/sh\nexit 0\n")
                bin_path.chmod(0o755)

            cmds = []

            class FakePopen:
                def __init__(self, cmd, *args, **kwargs):
                    cmds.append(cmd)
                    if Path(cmd[0]).name == "fake_updater":
                        Path(f"{cmd[-1]}.part").write_bytes(b"aborted autoupdate part")
                        self.stdout = io.BytesIO(b"network error\n")
                        self.returncode = 1
                    else:
                        out_idx = cmd.index("-o") + 1
                        out_path = Path(cmd[out_idx])
                        out_path.write_bytes(b"zsync updated image")
                        Path(f"{out_path}.part").write_bytes(b"zsync part")
                        self.stdout = io.BytesIO(
                            b"Reading seed file: seed\n"
                            b"\x1b[2K\r80.00% done (164.00 of 205.00 MiB)...\n"
                            b"zsync2: checksum matches OK\n"
                        )
                        self.returncode = 0
                    self.pid = 999997

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    return self.returncode

            def _which(name):
                if name == "appimageupdatetool":
                    return str(fake_updater)
                if name == "zsync":
                    return str(fake_zsync)
                return None

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=_release_urlopen("v3.1.0")),
                patch("managers.app_update_manager.shutil.which", side_effect=_which),
                patch("managers.app_update_manager.subprocess.Popen", side_effect=FakePopen),
            ):
                AppUpdateManager(parent).run_self_update()
                _pump_until(self.app, parent.finished.is_set)

            self.assertTrue(parent.succeeded)
            self.assertEqual(len(cmds), 2)
            self.assertEqual(appimage.read_bytes(), b"zsync updated image")
            for leftover in (
                "ASSella.AppImage.autoupdate",
                "ASSella.AppImage.autoupdate.part",
                "ASSella.AppImage.zsync-seed",
                "ASSella.AppImage.zsync-out",
                "ASSella.AppImage.zsync-out.part",
            ):
                self.assertFalse((Path(temp_dir) / leftover).exists(), f"leftover not removed: {leftover}")

    def test_r2_mode_skips_github_and_appimageupdatetool_and_verifies_sha(self):
        parent = _make_parent("3.1.0", update_server="r2", update_channel="stable")
        expected_bytes = b"r2 zsync synced image bytes"
        expected_sha = hashlib.sha256(expected_bytes).hexdigest()

        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"old seed bytes")

            cmds = []
            requested_urls = []

            class FakePopen:
                def __init__(self, cmd, *args, **kwargs):
                    cmds.append(cmd)
                    out_idx = cmd.index("-o") + 1
                    Path(cmd[out_idx]).write_bytes(expected_bytes)
                    self.stdout = io.BytesIO(
                        b"\x1b[2K\r100.00% done (1.00 of 1.00 MiB)...\nzsync2: checksum matches OK\n"
                    )
                    self.returncode = 0
                    self.pid = 999996

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    return self.returncode

            def fake_urlopen(request, timeout=None):
                url = getattr(request, "full_url", "") or ""
                requested_urls.append(url)
                if "releases/channels/stable.json" in url:
                    manifest = {
                        "tag_name": "v3.1.0",
                        "assets": [
                            {
                                "name": "ASSella.AppImage",
                                "browser_download_url": "https://r2.test/releases/stable/v3.1.0/ASSella.AppImage",
                                "sha256": expected_sha,
                            },
                            {
                                "name": "ASSella.AppImage.zsync",
                                "browser_download_url": "https://r2.test/releases/stable/v3.1.0/ASSella.AppImage.zsync",
                            },
                        ],
                    }
                    return _FakeResponse(json.dumps(manifest).encode())
                raise AssertionError(f"Unexpected HTTP call: {url}")

            def _tool(name):
                if name == "appimageupdatetool":
                    return "/usr/bin/appimageupdatetool"
                if name == "zsync":
                    return "/usr/bin/zsync"
                return ""

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen),
                patch("managers.app_update_manager._find_bundled_or_system_tool", side_effect=_tool),
                patch("managers.app_update_manager.subprocess.Popen", side_effect=FakePopen),
            ):
                AppUpdateManager(parent).run_self_update()
                _pump_until(self.app, parent.finished.is_set)

            self.assertTrue(parent.succeeded, f"unexpected error: {parent.error}")
            self.assertFalse(any("github" in u for u in requested_urls))
            self.assertEqual(len(cmds), 1)
            self.assertEqual(cmds[0][0], "/usr/bin/zsync")
            self.assertIn("-u", cmds[0])
            self.assertIn("https://r2.test/releases/stable/v3.1.0/ASSella.AppImage", cmds[0])
            self.assertEqual(cmds[0][-1], "https://r2.test/releases/stable/v3.1.0/ASSella.AppImage.zsync")
            self.assertEqual(appimage.read_bytes(), expected_bytes)

    def test_r2_sha_mismatch_rejects_corrupt_download(self):
        parent = _make_parent("3.1.0", update_server="r2", update_channel="stable")
        expected_sha = hashlib.sha256(b"authentic binary").hexdigest()

        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"original binary")

            class FakePopen:
                def __init__(self, cmd, *args, **kwargs):
                    out_idx = cmd.index("-o") + 1
                    Path(cmd[out_idx]).write_bytes(b"corrupt delta binary")
                    self.stdout = io.BytesIO(b"100.00% done (1.00 of 1.00 MiB)...\n")
                    self.returncode = 0
                    self.pid = 999995

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    return self.returncode

            def fake_urlopen(request, timeout=None):
                url = getattr(request, "full_url", "") or ""
                if "releases/channels/stable.json" in url:
                    manifest = {
                        "tag_name": "v3.1.0",
                        "assets": [{
                            "name": "ASSella.AppImage",
                            "browser_download_url": "https://r2.test/releases/stable/v3.1.0/ASSella.AppImage",
                            "sha256": expected_sha,
                        }],
                    }
                    return _FakeResponse(json.dumps(manifest).encode())
                if url == "https://r2.test/releases/stable/v3.1.0/ASSella.AppImage":
                    return _FakeResponse(b"corrupt full binary")
                raise AssertionError(f"Unexpected URL: {url}")

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen),
                patch(
                    "managers.app_update_manager._find_bundled_or_system_tool",
                    side_effect=lambda name: "/usr/bin/zsync" if name == "zsync" else "",
                ),
                patch("managers.app_update_manager.subprocess.Popen", side_effect=FakePopen),
            ):
                AppUpdateManager(parent).run_self_update()
                _pump_until(self.app, parent.finished.is_set)

            self.assertFalse(parent.succeeded)
            self.assertIn("Checksum mismatch", parent.error or "")
            self.assertEqual(appimage.read_bytes(), b"original binary")

    def test_auto_mode_falls_back_to_r2_when_github_fails(self):
        parent = _make_parent("3.1.0", update_server="auto", update_channel="stable")
        expected_bytes = b"r2 fallback payload"
        expected_sha = hashlib.sha256(expected_bytes).hexdigest()

        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"old seed")

            requested_urls = []

            class FakePopen:
                def __init__(self, cmd, *args, **kwargs):
                    out_idx = cmd.index("-o") + 1
                    Path(cmd[out_idx]).write_bytes(expected_bytes)
                    self.stdout = io.BytesIO(b"100.00% done (1.00 of 1.00 MiB)...\n")
                    self.returncode = 0
                    self.pid = 999994

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    return self.returncode

            def fake_urlopen(request, timeout=None):
                url = getattr(request, "full_url", "") or ""
                requested_urls.append(url)
                if "api.github.com" in url:
                    raise OSError("github offline")
                if "releases/channels/stable.json" in url:
                    manifest = {
                        "tag_name": "v3.1.0",
                        "assets": [{
                            "name": "ASSella.AppImage",
                            "browser_download_url": "https://r2.test/releases/stable/v3.1.0/ASSella.AppImage",
                            "sha256": expected_sha,
                        }],
                    }
                    return _FakeResponse(json.dumps(manifest).encode())
                raise AssertionError(f"Unexpected URL: {url}")

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen),
                patch(
                    "managers.app_update_manager._find_bundled_or_system_tool",
                    side_effect=lambda name: "/usr/bin/zsync" if name == "zsync" else "",
                ),
                patch("managers.app_update_manager.subprocess.Popen", side_effect=FakePopen),
            ):
                AppUpdateManager(parent).run_self_update()
                _pump_until(self.app, parent.finished.is_set)

            self.assertTrue(parent.succeeded, f"unexpected error: {parent.error}")
            self.assertTrue(any("api.github.com" in u for u in requested_urls))
            self.assertTrue(any("releases/channels/stable.json" in u for u in requested_urls))
            self.assertEqual(appimage.read_bytes(), expected_bytes)

    def test_auto_mode_does_not_fallback_to_r2_when_cancelled(self):
        parent = _make_parent("3.1.0", update_server="auto", update_channel="stable")
        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"original bytes")
            pid_file = Path(temp_dir) / "child.pid"
            fake_updater = Path(temp_dir) / "slow_github_updater.py"
            fake_updater.write_text(
                f"#!{sys.executable}\n"
                "import os, pathlib, sys, time\n"
                f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
                "sys.stdout.write('\\x1b[2K\\r25.00% done (50.00 of 200.00 MiB)...'); sys.stdout.flush()\n"
                "time.sleep(30)\n"
            )
            fake_updater.chmod(0o755)

            r2_called = False

            def fake_urlopen(request, timeout=None):
                nonlocal r2_called
                url = getattr(request, "full_url", "") or ""
                if "releases/channels/" in url or "r2" in url:
                    r2_called = True
                return _release_urlopen("v3.1.0")(request, timeout)

            mgr = AppUpdateManager(parent)
            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen),
                patch(
                    "managers.app_update_manager._find_bundled_or_system_tool",
                    side_effect=lambda name: str(fake_updater) if name == "appimageupdatetool" else "",
                ),
            ):
                mgr.run_self_update()
                _pump_until(self.app, pid_file.exists, timeout=5.0)
                mgr._active_progress.cancel()
                _pump_until(self.app, lambda: not mgr._updating, timeout=5.0)

            self.assertFalse(r2_called, "cancelled GitHub update fell back to R2")
            self.assertFalse(parent.finished.is_set())

    def test_github_mode_does_not_fallback_to_r2_on_error(self):
        parent = _make_parent("3.1.0", update_server="github", update_channel="stable")
        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"original bytes")

            requested_urls = []

            def fake_urlopen(request, timeout=None):
                url = getattr(request, "full_url", "") or ""
                requested_urls.append(url)
                if "api.github.com" in url:
                    raise OSError("github offline")
                raise AssertionError(f"Should not fall back in github-only mode: {url}")

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                AppUpdateManager(parent).run_self_update()
                _pump_until(self.app, parent.finished.is_set)

            self.assertFalse(parent.succeeded)
            self.assertIn("github offline", parent.error or "")
            self.assertFalse(any("releases/channels/" in u for u in requested_urls))

    def test_delta_progress_tracker_parses_zsync2_bars(self):
        from managers.app_update_manager import _DeltaProgressTracker

        progress = ThemedProgressDialog("starting", None, 0, 100)
        tracker = _DeltaProgressTracker("zsync", progress, threading.Event())
        tracker.feed("##########---------- 50.0% 180.2 kBps", final_line=False)
        self.app.processEvents()
        self.assertGreaterEqual(progress.progress_bar.value(), 50)
        self.assertEqual(progress.label.text(), "Delta update: 50.0% (180.2 kBps)")

    def test_r2_mode_beta_only_uses_beta_manifest(self):
        parent = _make_parent("3.2.0beta", update_server="r2", update_channel="beta")
        expected_bytes = b"r2 strict beta bytes"
        expected_sha = hashlib.sha256(expected_bytes).hexdigest()

        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"2.9.9beta seed bytes")
            requested_urls = []

            class FakePopen:
                def __init__(self, cmd, *args, **kwargs):
                    out_idx = cmd.index("-o") + 1
                    Path(cmd[out_idx]).write_bytes(expected_bytes)
                    self.stdout = io.BytesIO(b"100.00% done (1.00 of 1.00 MiB)...\n")
                    self.returncode = 0
                    self.pid = 999993

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    return self.returncode

            def fake_urlopen(request, timeout=None):
                url = getattr(request, "full_url", "") or ""
                requested_urls.append(url)
                if "releases/channels/beta.json" in url:
                    manifest = {
                        "tag_name": "v3.2.0beta",
                        "prerelease": True,
                        "channel": "beta",
                        "assets": [{
                            "name": "ASSella.AppImage",
                            "browser_download_url": "https://r2.test/releases/beta/v3.2.0beta/ASSella.AppImage",
                            "sha256": expected_sha,
                        }],
                    }
                    return _FakeResponse(json.dumps(manifest).encode())
                raise AssertionError(f"Unexpected HTTP call: {url}")

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen),
                patch(
                    "managers.app_update_manager._find_bundled_or_system_tool",
                    side_effect=lambda name: "/usr/bin/zsync" if name == "zsync" else "",
                ),
                patch("managers.app_update_manager.subprocess.Popen", side_effect=FakePopen),
            ):
                AppUpdateManager(parent).run_self_update()
                _pump_until(self.app, parent.finished.is_set)

            self.assertTrue(parent.succeeded, f"unexpected error: {parent.error}")
            self.assertTrue(any("releases/channels/beta.json" in u for u in requested_urls))
            self.assertFalse(any("releases/channels/stable.json" in u for u in requested_urls))
            self.assertEqual(appimage.read_bytes(), expected_bytes)

    def test_stale_cached_channel_tag_ignored_and_resolves_channel_specific_zsync(self):
        """Switching from stable to beta discards cached stable tag and pins zsync -u to beta release."""
        parent = _make_parent("3.1.1", update_server="github", update_channel="beta")
        parent._latest_update_channel = "stable"

        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"2.9.9beta local bytes")
            fake_updater = Path(temp_dir) / "fake_appimageupdatetool"
            fake_updater.write_text("#!/bin/sh\nexit 0\n")
            fake_updater.chmod(0o755)
            invoked_cmds = []

            class FakePopen:
                def __init__(self, cmd, *args, **kwargs):
                    invoked_cmds.append(cmd)
                    Path(cmd[-1]).write_bytes(b"updated beta bytes")
                    self.stdout = io.BytesIO(b"100.00% done (1.00 of 1.00 MiB)...\n")
                    self.returncode = 0
                    self.pid = 999994

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    return self.returncode

            def fake_urlopen(request, timeout=None):
                url = getattr(request, "full_url", "") or ""
                if "/releases?per_page=100" in url:
                    releases = [
                        {
                            "tag_name": "v3.1.1",
                            "prerelease": False,
                            "target_commitish": "main",
                            "published_at": "2026-11-11T22:00:00Z",
                            "assets": [{
                                "name": "ASSella.AppImage",
                                "browser_download_url": "https://github.com/stable.AppImage",
                            }],
                        },
                        {
                            "tag_name": "v3.0.0-beta.2",
                            "prerelease": True,
                            "target_commitish": "beta",
                            "published_at": "2026-11-11T21:30:00Z",
                            "assets": [{
                                "name": "ASSella.AppImage",
                                "browser_download_url": "https://github.com/beta.AppImage",
                            }],
                        },
                    ]
                    return _FakeResponse(json.dumps(releases).encode())
                if url == "https://github.com/beta.AppImage.sha256":
                    digest = hashlib.sha256(b"updated beta bytes").hexdigest()
                    return _FakeResponse(f"{digest}  ASSella.AppImage\n".encode())
                raise AssertionError(f"Unexpected HTTP call: {url}")

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen),
                patch(
                    "managers.app_update_manager._find_bundled_or_system_tool",
                    side_effect=lambda name: str(fake_updater) if name == "appimageupdatetool" else "",
                ),
                patch("managers.app_update_manager.subprocess.Popen", side_effect=FakePopen),
            ):
                AppUpdateManager(parent).run_self_update()
                _pump_until(self.app, parent.finished.is_set)

            self.assertTrue(parent.succeeded, f"unexpected error: {parent.error}")
            self.assertEqual(len(invoked_cmds), 1)
            self.assertIn(
                "gh-releases-zsync|niwia|ASSella|v3.0.0-beta.2|ASSella.AppImage.zsync",
                invoked_cmds[0],
            )

    def test_auto_mode_prefers_github_over_differing_r2_and_rejects_stale_r2_fallback(self):
        """Auto prioritizes GitHub even if R2 differs, and refuses stale R2 fallback."""
        # Part 1: Auto resolves and downloads from GitHub without consulting R2 when GitHub succeeds.
        parent = _make_parent("3.1.1", update_server="auto", update_channel="stable")
        expected_gh = b"fresh github 3.1.1 binary"
        expected_sha = hashlib.sha256(expected_gh).hexdigest()

        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"3.1.0 old binary")
            requested_urls = []

            def fake_urlopen(request, timeout=None):
                url = getattr(request, "full_url", "") or ""
                requested_urls.append(url)
                rel = {
                    "tag_name": "v3.1.1",
                    "prerelease": False,
                    "target_commitish": "main",
                    "published_at": "2026-11-11T16:00:00Z",
                    "assets": [{
                        "name": "ASSella.AppImage",
                        "browser_download_url": "https://github.com/niwia/ASSella/releases/download/v3.1.1/ASSella.AppImage",
                    }],
                }
                if url.endswith("/releases/tags/v3.1.1"):
                    return _FakeResponse(json.dumps(rel).encode())
                if "/releases?per_page=100" in url:
                    return _FakeResponse(json.dumps([rel]).encode())
                if url == "https://github.com/niwia/ASSella/releases/download/v3.1.1/ASSella.AppImage":
                    return _FakeResponse(expected_gh)
                if url == "https://github.com/niwia/ASSella/releases/download/v3.1.1/ASSella.AppImage.sha256":
                    return _FakeResponse(f"{expected_sha}  ASSella.AppImage\n".encode())
                if "releases/channels/" in url:
                    raise AssertionError(f"Auto must not prefer R2 when GitHub succeeds: {url}")
                raise AssertionError(f"Unexpected HTTP call: {url}")

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen),
                patch("managers.app_update_manager._find_bundled_or_system_tool", return_value=""),
            ):
                AppUpdateManager(parent).run_self_update()
                _pump_until(self.app, parent.finished.is_set)

            self.assertTrue(parent.succeeded, f"unexpected error: {parent.error}")
            self.assertEqual(appimage.read_bytes(), expected_gh)
            self.assertFalse(any("releases/channels/" in u for u in requested_urls))

        # Part 2: GitHub download fails 403 and R2 holds older v3.1.0 -> rejects stale R2 fallback cleanly.
        parent_fallback = _make_parent("3.1.1", update_server="auto", update_channel="stable")
        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"3.1.0 old binary")

            def fake_urlopen_fail(request, timeout=None):
                url = getattr(request, "full_url", "") or ""
                rel = {
                    "tag_name": "v3.1.1",
                    "prerelease": False,
                    "target_commitish": "main",
                    "published_at": "2026-11-11T16:00:00Z",
                    "assets": [{
                        "name": "ASSella.AppImage",
                        "browser_download_url": "https://github.com/niwia/ASSella/releases/download/v3.1.1/ASSella.AppImage",
                    }],
                }
                if url.endswith("/releases/tags/v3.1.1"):
                    return _FakeResponse(json.dumps(rel).encode())
                if "/releases?per_page=100" in url:
                    return _FakeResponse(json.dumps([rel]).encode())
                if "github.com/niwia/ASSella/releases/download" in url:
                    raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)
                if "releases/channels/stable.json" in url:
                    manifest = {
                        "tag_name": "v3.1.0",
                        "prerelease": False,
                        "channel": "stable",
                        "assets": [{
                            "name": "ASSella.AppImage",
                            "browser_download_url": "https://r2.test/releases/stable/v3.1.0/ASSella.AppImage",
                        }],
                    }
                    return _FakeResponse(json.dumps(manifest).encode())
                raise AssertionError(f"Unexpected HTTP call: {url}")

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen_fail),
                patch("managers.app_update_manager._find_bundled_or_system_tool", return_value=""),
            ):
                mgr = AppUpdateManager(parent_fallback)
                mgr.run_self_update()
                _pump_until(self.app, parent_fallback.finished.is_set)

            self.assertFalse(parent_fallback.succeeded)
            self.assertIsNotNone(parent_fallback.error)
            self.assertIn("older than requested v3.1.1", parent_fallback.error)
            self.assertEqual(mgr._last_failed_server, "auto")

    def test_github_failure_allows_r2_override_and_update_failed_recovery_ui(self):
        """GitHub 403 tracks _last_failed_server='github', MainWindow offers Try R2 Mirror + GitHub link."""
        parent = _make_parent("3.1.1", update_server="github", update_channel="stable")
        r2_bytes = b"r2 mirror binary bytes"
        r2_sha = hashlib.sha256(r2_bytes).hexdigest()

        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"3.1.0 old binary")

            def fake_urlopen(request, timeout=None):
                url = getattr(request, "full_url", "") or ""
                if "/releases/tags/" in url or "/releases?per_page=100" in url:
                    raise urllib.error.HTTPError(url, 403, "rate limit", None, None)
                if "releases/channels/stable.json" in url:
                    manifest = {
                        "tag_name": "v3.1.1",
                        "prerelease": False,
                        "channel": "stable",
                        "assets": [{
                            "name": "ASSella.AppImage",
                            "browser_download_url": "https://r2.test/releases/stable/v3.1.1/ASSella.AppImage",
                            "sha256": r2_sha,
                        }],
                    }
                    return _FakeResponse(json.dumps(manifest).encode())
                if url == "https://r2.test/releases/stable/v3.1.1/ASSella.AppImage":
                    return _FakeResponse(r2_bytes)
                raise AssertionError(f"Unexpected HTTP call: {url}")

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ) as q_mock,
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen),
                patch("managers.app_update_manager._find_bundled_or_system_tool", return_value=""),
            ):
                mgr = AppUpdateManager(parent)
                mgr.run_self_update()
                _pump_until(self.app, parent.finished.is_set)

                self.assertFalse(parent.succeeded)
                self.assertEqual(mgr._last_failed_server, "github")

                # Reset completion flags and retry with explicit R2 override + skip_prompt=True
                parent.finished.clear()
                parent.error = None
                q_mock.reset_mock()

                mgr.run_self_update(server_override="r2", skip_prompt=True)
                _pump_until(self.app, parent.finished.is_set)

                self.assertTrue(parent.succeeded, f"unexpected error: {parent.error}")
                self.assertEqual(appimage.read_bytes(), r2_bytes)
                q_mock.assert_not_called()

        # Verify MainWindow._on_update_failed buttons and actions for failed_server='github' vs 'auto'
        class _FakeMgr:
            def __init__(self, failed_server: str):
                self._last_failed_server = failed_server
                self.calls = []

            def run_self_update(self, server_override=None, skip_prompt=False):
                self.calls.append((server_override, skip_prompt))

        ui_parent = _make_parent("2.9.9beta", update_server="github", update_channel="beta")
        ui_parent.app_update_manager = _FakeMgr("github")
        captured_prompts = []

        def fake_choices(parent_arg, title, body, buttons, **kwargs):
            captured_prompts.append((title, body, buttons, kwargs))
            return "r2"

        with (
            patch("ui.main_window.ThemedMessageBox.choices", side_effect=fake_choices),
            patch("ui.main_window.QDesktopServices.openUrl") as open_mock,
        ):
            MainWindow._on_update_failed(ui_parent, "HTTP 403 rate limit")

        self.assertEqual(len(captured_prompts), 1)
        _, body_gh, btns_gh, _ = captured_prompts[0]
        self.assertIn("https://github.com/niwia/ASSella/releases", body_gh)
        self.assertIn("https://raw.githubusercontent.com/niwia/ASSella/beta/install.sh", body_gh)
        self.assertEqual([b[1] for b in btns_gh], ["r2", "releases", "close"])
        self.assertEqual(ui_parent.app_update_manager.calls, [("r2", True)])
        open_mock.assert_not_called()

        # For failed_server='auto', Try R2 Mirror is omitted and 'releases' opens GitHub Releases
        ui_auto = _make_parent("3.1.1", update_server="auto", update_channel="stable")
        ui_auto.app_update_manager = _FakeMgr("auto")
        captured_auto = []

        def fake_choices_releases(parent_arg, title, body, buttons, **kwargs):
            captured_auto.append((title, body, buttons, kwargs))
            return "releases"

        with (
            patch("ui.main_window.ThemedMessageBox.choices", side_effect=fake_choices_releases),
            patch("ui.main_window.QDesktopServices.openUrl") as open_mock,
        ):
            MainWindow._on_update_failed(ui_auto, "All mirrors failed")

        _, body_auto, btns_auto, _ = captured_auto[0]
        self.assertNotIn("r2", [b[1] for b in btns_auto])
        self.assertIn("https://raw.githubusercontent.com/niwia/ASSella/main/install.sh", body_auto)
        open_mock.assert_called_once_with(QUrl("https://github.com/niwia/ASSella/releases"))

    def test_github_appimageupdatetool_sha256_mismatch_falls_back_to_verified_zsync(self):
        """GitHub delta tools verify SHA-256; mismatch rejects delta and probes zsync2 before zsync."""
        parent = _make_parent("3.0.4", update_server="github", update_channel="stable")
        verified_bytes = b"verified zsync2 delta bytes"
        verified_sha = hashlib.sha256(verified_bytes).hexdigest()

        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"old appimage")
            fake_ait = Path(temp_dir) / "appimageupdatetool"
            fake_ait.write_text("#!/bin/sh\nexit 0\n")
            fake_ait.chmod(0o755)
            fake_zsync2 = Path(temp_dir) / "zsync2"
            fake_zsync2.write_text("#!/bin/sh\nexit 0\n")
            fake_zsync2.chmod(0o755)

            probed_tools = []

            def fake_finder(name: str) -> str:
                probed_tools.append(name)
                if name == "appimageupdatetool":
                    return str(fake_ait)
                if name == "zsync2":
                    return str(fake_zsync2)
                return ""

            class FakePopen:
                def __init__(self, cmd, *args, **kwargs):
                    if cmd[0] == str(fake_ait):
                        Path(cmd[-1]).write_bytes(b"corrupt appimageupdatetool bytes")
                    else:
                        out_idx = cmd.index("-o") + 1
                        Path(cmd[out_idx]).write_bytes(verified_bytes)
                    self.stdout = io.BytesIO(b"100.00% done (1.00 of 1.00 MiB)...\n")
                    self.returncode = 0
                    self.pid = 999995

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    return self.returncode

            def fake_urlopen(request, timeout=None):
                url = getattr(request, "full_url", "") or ""
                release_obj = {
                    "tag_name": "v3.0.4",
                    "prerelease": False,
                    "target_commitish": "main",
                    "published_at": "2026-11-11T12:00:00Z",
                    "assets": [{
                        "name": "ASSella.AppImage",
                        "browser_download_url": "https://github.com/v3.0.4/ASSella.AppImage",
                    }],
                }
                if "/releases/tags/v3.0.4" in url:
                    return _FakeResponse(json.dumps(release_obj).encode())
                if "/releases?per_page=100" in url:
                    return _FakeResponse(json.dumps([release_obj]).encode())
                if url == "https://github.com/v3.0.4/ASSella.AppImage.sha256":
                    return _FakeResponse(f"{verified_sha}  ASSella.AppImage\n".encode())
                raise AssertionError(f"Full download should not run when verified zsync2 succeeds: {url}")

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen),
                patch("managers.app_update_manager._find_bundled_or_system_tool", side_effect=fake_finder),
                patch("managers.app_update_manager.subprocess.Popen", side_effect=FakePopen),
            ):
                AppUpdateManager(parent).run_self_update()
                _pump_until(self.app, parent.finished.is_set)

            self.assertTrue(parent.succeeded, f"unexpected error: {parent.error}")
            self.assertEqual(appimage.read_bytes(), verified_bytes)
            self.assertIn("zsync2", probed_tools)

    def test_github_zsync_sha256_mismatch_falls_back_to_full_download(self):
        """When GitHub zsync output fails .sha256 verification, full verified download replaces it."""
        parent = _make_parent("3.0.4", update_server="github", update_channel="stable")
        full_bytes = b"clean verified full download bytes"
        full_sha = hashlib.sha256(full_bytes).hexdigest()

        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"old appimage")
            fake_zsync2 = Path(temp_dir) / "zsync2"
            fake_zsync2.write_text("#!/bin/sh\nexit 0\n")
            fake_zsync2.chmod(0o755)

            class BadZsyncPopen:
                def __init__(self, cmd, *args, **kwargs):
                    out_idx = cmd.index("-o") + 1
                    Path(cmd[out_idx]).write_bytes(b"tampered zsync2 output")
                    self.stdout = io.BytesIO(b"100.00% done (1.00 of 1.00 MiB)...\n")
                    self.returncode = 0
                    self.pid = 999996

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    return self.returncode

            def fake_urlopen(request, timeout=None):
                url = getattr(request, "full_url", "") or ""
                release_obj = {
                    "tag_name": "v3.0.4",
                    "prerelease": False,
                    "target_commitish": "main",
                    "published_at": "2026-11-11T12:00:00Z",
                    "assets": [{
                        "name": "ASSella.AppImage",
                        "browser_download_url": "https://github.com/v3.0.4/ASSella.AppImage",
                    }],
                }
                if "/releases/tags/v3.0.4" in url:
                    return _FakeResponse(json.dumps(release_obj).encode())
                if "/releases?per_page=100" in url:
                    return _FakeResponse(json.dumps([release_obj]).encode())
                if url == "https://github.com/v3.0.4/ASSella.AppImage.sha256":
                    return _FakeResponse(f"{full_sha}  ASSella.AppImage\n".encode())
                if url == "https://github.com/v3.0.4/ASSella.AppImage":
                    return _FakeResponse(full_bytes)
                raise AssertionError(f"Unexpected HTTP call: {url}")

            with (
                patch.dict(os.environ, {"APPIMAGE": str(appimage)}),
                patch(
                    "managers.app_update_manager.ThemedMessageBox.question",
                    return_value=ThemedMessageBox.StandardButton.Yes,
                ),
                patch("managers.app_update_manager.urllib.request.urlopen", side_effect=fake_urlopen),
                patch(
                    "managers.app_update_manager._find_bundled_or_system_tool",
                    side_effect=lambda name: str(fake_zsync2) if name == "zsync2" else "",
                ),
                patch("managers.app_update_manager.subprocess.Popen", side_effect=BadZsyncPopen),
            ):
                AppUpdateManager(parent).run_self_update()
                _pump_until(self.app, parent.finished.is_set)

            self.assertTrue(parent.succeeded, f"unexpected error: {parent.error}")
            self.assertEqual(appimage.read_bytes(), full_bytes)


if __name__ == "__main__":
    unittest.main()
