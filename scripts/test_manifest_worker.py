#!/usr/bin/env python3
"""Stress the async manifest worker: does closing Settings mid-fetch crash?

The card fetches the manifest on a QThread when the user opens the Tools tab.
That fetch can take seconds on a slow link. If the dialog is destroyed while
the thread is still running, Qt aborts the process with
"QThread: Destroyed while thread is still running" - which is a hard crash,
not an exception, so nothing upstream can catch it.

Run standalone (a crash shows up as a non-zero exit / SIGABRT):
    QT_QPA_PLATFORM=offscreen python3 scripts/test_manifest_worker.py
"""
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

MODE = sys.argv[1] if len(sys.argv) > 1 else "close-while-running"


def main() -> int:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget

    app = QApplication.instance() or QApplication([])

    from ui.dialogs.settings_tabs import components_card as cc

    # Make the fetch hang so we can reliably close the dialog mid-request.
    def slow_fetch(*a, **k):
        import time
        time.sleep(6)
        return None

    cc.fetch_manifest = slow_fetch

    class FakeDialog(QWidget):
        accent_color = "#a1c9fd"

        def __init__(self):
            super().__init__()
            self.main_window = None

        def _create_card_frame(self, title_text=""):
            from PyQt6.QtWidgets import QLabel
            card = QFrame()
            lay = QVBoxLayout(card)
            if title_text:
                lay.addWidget(QLabel(title_text))
            return card, lay

    dlg = FakeDialog()
    # Keep the reference: the real settings tab does this via layout.addWidget.
    # Dropping it lets Python collect the card and Qt destroys its children.
    card = cc.create_components_card(dlg)
    assert card is not None

    # Kick off the background manifest fetch (same call the Tools tab makes).
    cc._refresh_components(dlg, force=True, fetch_remote=True)
    worker = getattr(dlg, "_manifest_worker", None)
    # The ref now lives in components_card._live_workers, not on the dialog.
    if worker is None:
        worker = next(iter(cc._live_workers), None)
    if worker is None:
        print("FAIL: no manifest worker was created")
        return 1
    # QThread.start() is asynchronous; give it a tick to actually be running.
    QTimer.singleShot(200, app.quit)
    app.exec()
    print(f"worker started: {worker.isRunning()}")
    if not worker.isRunning():
        print("FAIL: worker never started")
        return 1

    if MODE == "close-while-running":
        # Destroy the dialog the harshest way possible while the fetch is in
        # flight: deleteLater() does not emit closeEvent, so no dialog-level
        # teardown hook can rescue us. The worker must survive this.
        QTimer.singleShot(200, dlg.deleteLater)
        QTimer.singleShot(500, app.quit)
        app.exec()
        print("dialog hard-deleted while worker was running")
        print(f"worker still running: {worker.isRunning()}")
        print("kept alive by components_card._live_workers:",
              worker in cc._live_workers)
        del dlg
    else:
        QTimer.singleShot(8000, app.quit)
        app.exec()

    print("SURVIVED")
    return 0


if __name__ == "__main__":
    sys.exit(main())