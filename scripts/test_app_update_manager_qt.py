#!/usr/bin/env python3
"""Regression checks for the AppImage updater's worker-to-Qt progress bridge."""

import sys
import json
import hashlib
import os
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
from managers.app_update_manager import AppUpdateManager


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
        class Parent(QWidget):
            def __init__(self):
                super().__init__()
                self._latest_remote_version = "3.0.3"
                self.finished = threading.Event()
                self.error = None

            @pyqtSlot()
            def _on_update_success(self):
                self.finished.set()

            @pyqtSlot(str)
            def _on_update_failed(self, error):
                self.error = error
                self.finished.set()

        class Response:
            def __init__(self, payload):
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
                return Response(json.dumps(release).encode())
            if url.endswith(".sha256"):
                return Response(hashlib.sha256(payload).hexdigest().encode())
            return Response(payload)

        parent = Parent()
        with tempfile.TemporaryDirectory() as temp_dir:
            appimage = Path(temp_dir) / "ASSella.AppImage"
            appimage.write_bytes(b"old AppImage")
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
                deadline = time.monotonic() + 5
                while not parent.finished.is_set() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)

            self.assertTrue(parent.finished.is_set(), "updater worker did not finish")
            self.assertIsNone(parent.error)
            self.assertEqual(appimage.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
