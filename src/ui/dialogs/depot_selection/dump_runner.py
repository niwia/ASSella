"""Run a prepared manifest dump and report back.

:mod:`manifest_dump` does the preparation - newest bundle, depot key, the
``.manifest`` inside it, the DepotDownloader command line. This is the other
half: show progress while preparing the manifest (including on-demand network
fetching) and running DepotDownloader to write the file listing, off the GUI
thread.

Dialogs offering the Browse Files button differ only in what they do with the
outcome. The run itself lives here once: preparation and downloading happen off
the GUI thread so the UI is never frozen, ``finished`` signals the outcome, and
the key file is cleaned up on failure.
"""

import logging
import os
import subprocess
from typing import Optional

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal

from ui.dialogs.themed_progress_dialog import ThemedProgressDialog

from ui.dialogs.depot_selection.manifest_dump import ManifestDump, ManifestDumpError, discard_keys_file

logger = logging.getLogger(__name__)

_MISSING_LISTING = "Failed to locate generated file list text file."


class _DumpThread(QThread):
    """Manifest preparation and DepotDownloader, off the GUI thread."""

    finished_signal = pyqtSignal(bool, str, object)

    def __init__(self, dump: Optional[ManifestDump] = None, app_id: str = "", depot_id: str = "", manifest_id: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._dump = dump
        self._app_id = app_id
        self._depot_id = depot_id
        self._manifest_id = manifest_id

    def run(self):
        proxy_mgr = None
        dump = self._dump
        try:
            if dump is None:
                from ui.dialogs.depot_selection.manifest_dump import prepare_manifest_dump
                try:
                    dump = prepare_manifest_dump(self._app_id, self._depot_id, manifest_id=self._manifest_id)
                except ManifestDumpError as me:
                    self.finished_signal.emit(False, str(me), None)
                    return

            from utils.helpers import get_dotnet_path, resource_path, get_dotnet_env
            cmd = dump.command(
                get_dotnet_path(),
                resource_path(os.path.join("deps", "DepotDownloader.dll")),
            )

            from utils.kuroko import resolve_stealth_proxy_url
            proxy_url, proxy_mgr = resolve_stealth_proxy_url()
            if proxy_url:
                cmd.extend(["-proxy", proxy_url])

            env = get_dotnet_env()
            subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)

            if not os.path.exists(dump.txt_path):
                discard_keys_file(dump.keys_path)
                self.finished_signal.emit(False, _MISSING_LISTING, None)
                return

            self.finished_signal.emit(True, "", dump)
        except Exception as ex:
            if dump and hasattr(dump, "keys_path"):
                discard_keys_file(dump.keys_path)
            self.finished_signal.emit(False, f"Failed to load file list: {ex}", None)
        finally:
            mgr, proxy_mgr = proxy_mgr, None
            if mgr is not None:
                try:
                    mgr.stop()
                except Exception as e:
                    logger.debug(f"[DumpRunner] Error stopping stealth proxy manager: {e}")


class ManifestDumpRunner(QObject):
    """Prepares the manifest and writes the file listing, showing progress while it runs.

    Parent it to the dialog: the dialog then owns it, so closing the dialog
    leaves nothing running. Preparation and downloading run off the GUI thread
    so the UI never freezes.
    """

    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        parent,
        dump: Optional[ManifestDump] = None,
        app_id: str = "",
        depot_id: str = "",
        manifest_id: Optional[str] = None,
    ):
        super().__init__(parent)
        self.dump = dump
        self._app_id = str(app_id)
        self._depot_id = str(depot_id)
        self._manifest_id = str(manifest_id) if manifest_id else None

    def start(self):
        progress = ThemedProgressDialog(
            "Loading file list from manifest...", "Cancel", 0, 0, self.parent()
        )
        progress.setWindowTitle("Loading Manifest")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        self._progress = progress

        thread = _DumpThread(
            dump=self.dump,
            app_id=self._app_id,
            depot_id=self._depot_id,
            manifest_id=self._manifest_id,
            parent=self,
        )
        thread.finished_signal.connect(self._on_thread_finished)
        self._thread = thread
        thread.start()

    def _on_thread_finished(self, ok: bool, err: str, dump: Optional[ManifestDump]):
        self._progress.close()
        self.dump = dump

        message = ""
        if not ok:
            message = err
        elif not dump or not os.path.exists(dump.txt_path):
            message = _MISSING_LISTING

        if message and dump and hasattr(dump, "keys_path"):
            discard_keys_file(dump.keys_path)
        self.finished.emit(not message, message)
