"""Run a prepared manifest dump and report back.

:mod:`manifest_dump` does the preparation - newest bundle, depot key, the
``.manifest`` inside it, the DepotDownloader command line. This is the other
half: show progress while DepotDownloader writes the file listing, then say how
it went.

Two dialogs offer the same Browse Files button, the multi-depot selector and
the single-depot selector, and they differ only in what they do with the
outcome. So the run itself lives here once: ``finished`` carries the sentence
the dialog should show when it did not work out, and the key file is cleaned up
on every failure.
"""

import os
import subprocess

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal

from ui.dialogs.themed_progress_dialog import ThemedProgressDialog

from ui.dialogs.depot_selection.manifest_dump import ManifestDump, discard_keys_file

_MISSING_LISTING = "Failed to locate generated file list text file."


class _DumpThread(QThread):
    """DepotDownloader, off the GUI thread."""

    finished_signal = pyqtSignal(bool, str)

    def __init__(self, cmd, parent=None):
        super().__init__(parent)
        self._cmd = cmd

    def run(self):
        try:
            subprocess.run(self._cmd, capture_output=True, text=True, check=True)
            self.finished_signal.emit(True, "")
        except Exception as ex:
            self.finished_signal.emit(False, str(ex))


class ManifestDumpRunner(QObject):
    """Writes the file listing for ``dump``, showing progress while it runs.

    Parent it to the dialog: the dialog then owns it, so closing the dialog
    leaves nothing running. ``finished(ok, failure_message)`` fires on the GUI
    thread; when ``ok`` is set, ``dump.txt_path`` holds the listing and
    ``dump.keys_path`` is still there for the file-selection dialog to use.
    """

    finished = pyqtSignal(bool, str)

    def __init__(self, parent, dump: ManifestDump):
        super().__init__(parent)
        self.dump = dump

    def start(self):
        from utils.helpers import get_dotnet_path, resource_path

        progress = ThemedProgressDialog(
            "Loading file list from manifest...", "Cancel", 0, 0, self.parent()
        )
        progress.setWindowTitle("Loading Manifest")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        self._progress = progress

        cmd = self.dump.command(
            get_dotnet_path(),
            resource_path(os.path.join("deps", "DepotDownloader.dll")),
        )

        thread = _DumpThread(cmd, self)
        thread.finished_signal.connect(self._on_thread_finished)
        self._thread = thread
        thread.start()

    def _on_thread_finished(self, ok, err):
        self._progress.close()

        message = ""
        if not ok:
            message = f"Failed to load file list: {err}"
        elif not os.path.exists(self.dump.txt_path):
            # DepotDownloader can exit cleanly without writing the listing.
            message = _MISSING_LISTING

        if message:
            discard_keys_file(self.dump.keys_path)
        self.finished.emit(not message, message)
