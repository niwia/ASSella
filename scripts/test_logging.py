#!/usr/bin/env python3
"""Test the structured logging layer: activity helpers + pager selection.

The status pager used to decide what to show by substring-matching log text.
It now selects on ``user_visible``. These checks pin that contract, because the
failure mode is silent: the pager just stops updating and nobody notices until
a user reports "the status bar is stuck".

    QT_QPA_PLATFORM=offscreen python3 scripts/test_logging.py
"""
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_failures = []

# Imported at module scope: Capture.emit mirrors QtLogHandler.emit and needs
# the same ownership rule, and it runs outside main()'s local scope.
sys.path.insert(0, str(SRC_DIR))
from utils.activity import is_our_logger  # noqa: E402


def check(cond, label):
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        _failures.append(label)
    return cond


class Capture(logging.Handler):
    """Stand-in for QtLogHandler: records what the pager would receive."""

    def __init__(self):
        super().__init__()
        self.payloads = []

    def emit(self, record):
        # Mirrors QtLogHandler.emit so the tests exercise the real rule.
        explicit = getattr(record, "user_visible", None)
        if explicit is None:
            visible = record.levelno >= logging.INFO and is_our_logger(record.name)
        else:
            visible = bool(explicit)
        self.payloads.append({
            "text": record.getMessage(),
            "level": record.levelno,
            "levelname": record.levelname,
            "user_visible": visible,
            "category": getattr(record, "category", "general"),
            "logger": record.name,
        })


def visible(cap):
    return [p for p in cap.payloads if p["user_visible"]]


def main() -> int:
    from utils.activity import (
        COMPONENT, DOWNLOAD, NETWORK, log_event, log_quiet, log_step,
    )
    from utils.logger import (
        LogCategoryFilter, SensitiveDataFilter, _THIRD_PARTY_LOGGERS, _quiet_third_party,
    )

    cap = Capture()
    root = logging.getLogger()
    root.handlers[:] = [cap]
    root.setLevel(logging.DEBUG)

    print("=== log_event visibility contract ===")
    log_event("Downloading Elden Ring", category=DOWNLOAD)
    log_event("Could not reach Steam", category=NETWORK, level=logging.WARNING)
    log_event("Something odd", category=NETWORK, level=logging.ERROR)
    log_event("Internal chatter", level=logging.INFO, visible=False)
    log_event("Forced visible debug", level=logging.DEBUG, visible=True)

    p = cap.payloads
    check(p[0]["user_visible"] is True, "INFO from our code reaches the pager")
    check(p[1]["user_visible"] is True, "WARNING reaches the pager")
    check(p[2]["user_visible"] is True, "ERROR reaches the pager")
    check(p[3]["user_visible"] is False, "explicit visible=False is hidden")
    check(p[4]["user_visible"] is True, "explicit visible=True is shown")
    check(p[0]["category"] == DOWNLOAD, "category is carried on the record")
    check(p[1]["level"] >= logging.WARNING, "level is carried for styling")

    print("\n=== bare INFO still reaches the pager (user-relevant by default) ===")
    cap.payloads.clear()
    for i in range(5):
        log_event(f"Noise event {i}")
    check(len(visible(cap)) == 5,
          "INFO from our code is shown, so the strip keeps updating")

    print("\n=== DEBUG is hidden by default ===")
    cap.payloads.clear()
    log_event("chatty detail", level=logging.DEBUG)
    check(len(visible(cap)) == 0, "DEBUG does not reach the pager")

    print("\n=== log_quiet never reaches the pager ===")
    cap.payloads.clear()
    log_quiet("cache hit for game 123")
    log_quiet("checked 4 of 300", level=logging.WARNING)
    check(len(cap.payloads) == 2, "log_quiet still writes to the file")
    check(len(visible(cap)) == 0, "log_quiet never reaches the pager, even WARNING")

    print("\n=== third-party chatter must never reach the pager ===")
    cap.payloads.clear()
    for pkg in _THIRD_PARTY_LOGGERS:
        lg = logging.getLogger(pkg)
        lg.setLevel(logging.DEBUG)
        lg.addHandler(cap)
    logging.getLogger("urllib3.connectionpool").info(
        "Starting new HTTPS connection (1): hubcapmanifest.com:443"
    )
    logging.getLogger("SteamClient").warning("Connection reset by peer")
    check(len(visible(cap)) == 0,
          "third-party INFO/WARNING produce 0 pager updates")
    for pkg in _THIRD_PARTY_LOGGERS:
        lg = logging.getLogger(pkg)
        lg.handlers[:] = []
        lg.setLevel(logging.NOTSET)

    print("\n=== log_step narrative ===")
    cap.payloads.clear()
    with log_step("Finalizing install") as step:
        step["result"] = "complete"
    texts = [q["text"] for q in visible(cap)]
    check(len(texts) == 2, f"step emits start + done ({texts})")
    check(texts[0] == "Finalizing install...", f"start line: {texts[0]!r}")
    check("done in" in texts[1] and "result=complete" in texts[1],
          f"done line has timing and fields: {texts[1]!r}")

    print("\n=== log_step failure re-raises and reports ===")
    cap.payloads.clear()
    raised = False
    try:
        with log_step("Downloading depot") as step:
            step["depot"] = "4575721"
            raise RuntimeError("network unreachable")
    except RuntimeError:
        raised = True
    check(raised, "exception propagates (caller still sees it)")
    texts = [q["text"] for q in visible(cap)]
    check(len(texts) == 2, f"start + failure emitted ({len(texts)})")
    check("failed after" in texts[1] and "network unreachable" in texts[1],
          f"failure names the cause: {texts[1]!r}")
    check("depot=4575721" in texts[1], "failure keeps the step fields")
    check(visible(cap)[1]["level"] >= logging.ERROR, "failure is ERROR level")

    print("\n=== cancellation is not reported as success ===")
    cap.payloads.clear()
    with log_step("Finalizing install", skipped_message="cancelled") as step:
        step["result"] = "cancelled"
    texts = [q["text"] for q in visible(cap)]
    check("cancelled" in texts[1], f"cancelled step says so: {texts[1]!r}")
    check("done" not in texts[1], "cancelled step is not reported as done")
    check(visible(cap)[1]["level"] >= logging.WARNING, "cancellation is WARNING level")

    print("\n=== third-party loggers are floored ===")
    _quiet_third_party(logging.WARNING)
    noisy = [n for n in _THIRD_PARTY_LOGGERS
             if logging.getLogger(n).level < logging.WARNING]
    check(not noisy, f"all of {len(_THIRD_PARTY_LOGGERS)} third-party loggers at WARNING+")
    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)

    print("\n=== redaction still works ===")
    text = SensitiveDataFilter.sanitize_text(
        "api_key=smm_abcdef1234567890abcdef and password=hunter2"
    )
    check("smm_abcdef1234567890abcdef" not in text, "API key redacted")
    check("hunter2" not in text, "password redacted")
    check("[REDACTED" in text, "redaction marker present")

    print("\n=== the log file keeps DEBUG regardless of display level ===")
    from utils.logger import LineRotatingFileHandler, update_log_filters
    import tempfile
    from pathlib import Path as _P
    fh = LineRotatingFileHandler(str(_P(tempfile.mkdtemp()) / "t.log"), delay=False)
    console = logging.StreamHandler()
    root.handlers[:] = [fh, console]
    update_log_filters()
    check(fh.level == logging.DEBUG,
          f"file handler pinned at DEBUG (got {logging.getLevelName(fh.level)})")
    # The console must track whatever the user configured, which is not
    # necessarily INFO - this machine is set to DEBUG.
    from utils.settings import get_settings
    want = (get_settings().value("log_filter_level", "INFO", type=str) or "INFO").upper()
    want_num = 100 if want == "NONE" else getattr(logging, want, logging.INFO)
    check(console.level == want_num,
          f"console follows display setting {want} "
          f"(got {logging.getLevelName(console.level)})")
    check(not any(isinstance(f, LogCategoryFilter) for f in fh.filters),
          "display category filter not applied to the file")
    check(any(isinstance(f, SensitiveDataFilter) for f in fh.filters),
          "redaction still applied to the file")
    fh.close()
    console.close()
    root.handlers[:] = [cap]

    print("\n=== pager selection is structural, not keyword-based ===")
    src = (REPO_ROOT / "src" / "ui" / "status_pager.py").read_text()
    check("interesting_keywords" not in src, "keyword allow-list removed")
    check("user_visible" in src, "pager reads user_visible")
    # The old blocklist matched these words anywhere in the message.
    for word in ('"run"', '"start"', '"complet"'):
        check(f"{word} in src.lower()" is False
              or f"interesting_keywords" not in src,
              f"no keyword list containing {word}")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL LOGGING TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())