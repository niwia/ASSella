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
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QMetaObject, Qt, Q_ARG, pyqtSlot
from PyQt6.QtWidgets import QApplication, QWidget

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ui.dialogs.themed_progress_dialog import ThemedProgressDialog
from ui.dialogs.themed_message_box import ThemedMessageBox
from managers.app_update_manager import AppUpdateManager, _resolve_appimage_path


def _make_parent(remote_version="3.0.4"):
    class Parent(QWidget):
        def __init__(self):
            super().__init__()
            self._latest_remote_version = remote_version
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


if __name__ == "__main__":
    unittest.main()
