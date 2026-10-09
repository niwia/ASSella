#!/usr/bin/env python3
"""Drive the real StatusPagerWidget and assert what it displays.

The pager is a single-line strip that replaces its text on every update. This
constructs the actual widget, feeds it the actual QtLogHandler signal, and
reads back the label - so it catches a broken signal signature or a filter that
silently drops everything, which a unit test of the filter alone would miss.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_pager_display.py
"""
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_failures = []


def check(cond, label):
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        _failures.append(label)
    return cond


def main() -> int:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    # The widget fetches a remote broadcast on construction and would overwrite
    # the label mid-assertion. Neutralise it before instantiating.
    os.environ["ASSELLA_BROADCAST_URL"] = "file:///nonexistent/broadcast.json"

    from ui.status_pager import StatusPagerWidget
    from utils.logger import (
        QtLogHandler, _THIRD_PARTY_LOGGERS, _quiet_third_party, qt_log_handler,
    )

    StatusPagerWidget._fetch_remote_broadcast = lambda self: None

    pager = StatusPagerWidget()
    pager._broadcast_active = False

    # Must be the *global* handler: the pager connects to qt_log_handler, so a
    # freshly constructed one would never reach the widget.
    assert isinstance(qt_log_handler, QtLogHandler), "expected the global Qt handler"
    handler = qt_log_handler
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG)
    _quiet_third_party(logging.WARNING)

    def shown_text():
        # set_status() upper-cases for the retro LCD look, so compare
        # case-insensitively and never assert on exact casing.
        return pager.label.text().lower()

    def pump():
        """Let queued cross-thread signals (and the broadcast timer) settle."""
        from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer
        loop = QEventLoop()
        QTimer.singleShot(60, loop.quit)
        loop.exec()
        app.processEvents()

    print("=== widget is a single replacing strip ===")
    check(pager.label.text() != "", f"starts with a message: {pager.label.text()!r}")
    first = shown_text()

    print("\n=== a user-facing INFO event updates the label ===")
    logging.getLogger("managers.task_manager").info(
        "Downloading Elden Ring (depot 1 of 12)"
    )
    pump()
    shown = shown_text()
    check("downloading elden ring" in shown, f"INFO shown: {shown!r}")
    check(shown != first, "label changed from its initial text")

    print("\n=== each new event replaces the previous one ===")
    logging.getLogger("managers.task_manager").info("Downloading Elden Ring (depot 2 of 12)")
    pump()
    second = shown_text()
    check("depot 2" in second, "second event replaced the first")
    check("depot 1" not in second, "previous line is gone, not appended")

    print("\n=== DEBUG does not disturb the strip ===")
    before = shown_text()
    logging.getLogger("managers.task_manager").debug("cache hit for 12345")
    logging.getLogger("core.steam_api").debug("worker tick")
    pump()
    check(shown_text() == before, "DEBUG left the label untouched")

    print("\n=== warnings show and are styled as warnings ===")
    logging.getLogger("core.morrenus_api").warning("Could not reach the manifest server")
    pump()
    style = pager.label.styleSheet()
    check("could not reach" in shown_text(), f"warning shown: {shown_text()!r}")
    check("#FFB84D" in style, "warning uses the amber warning colour")

    print("\n=== third-party chatter never reaches the strip ===")
    before = shown_text()
    for pkg in _THIRD_PARTY_LOGGERS:
        logging.getLogger(pkg).setLevel(logging.DEBUG)
    logging.getLogger("urllib3.connectionpool").info(
        "Starting new HTTPS connection (1): hubcapmanifest.com:443"
    )
    logging.getLogger("SteamClient").error("internal transport failure detail")
    pump()
    check(shown_text() == before,
          f"strip unchanged by dependencies (still {shown_text()!r})")
    _quiet_third_party(logging.WARNING)

    print("\n=== long messages are truncated to one line ===")
    logging.getLogger("managers.task_manager").info("Z" * 300)
    pump()
    t = shown_text()
    check(len(t) <= 90, f"truncated to {len(t)} chars")
    check(t.endswith("..."), "truncation is marked")

    print("\n=== an explicit broadcast still wins ===")
    before = shown_text()
    pager.show_warning("Disk almost full", duration=1)
    pump()
    check("disk almost full" in shown_text(),
          f"broadcast overrides the log stream: {shown_text()!r}")
    logging.getLogger("managers.task_manager").info("should be ignored during broadcast")
    pump()
    check("should be ignored" not in shown_text(),
          "log stream suppressed while a broadcast is showing")

    pager._on_broadcast_expired()
    pump()
    check("should be ignored" not in shown_text(),
          "log stream resumes after the broadcast expires")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL PAGER DISPLAY TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())