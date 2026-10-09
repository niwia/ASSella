"""Structured, human-facing application events.

Two distinct audiences read the log:

* **The status pager** (the 36px strip under the main window) shows at most one
  line at a time and replaces it on every update. It should show what the user
  would care about right now - nothing else.
* **The log file** is what gets attached to a bug report, so it must stay
  complete.

Those are different jobs, and using raw ``logger.info`` for both is what made
the pager unreadable. Previously the pager tried to recover by substring-matching
log text against a list of "interesting" keywords, which meant real events were
dropped when the wording drifted and noise flickered in whenever a message
happened to contain a word like "run".

So the decision is recorded on the record instead of re-derived from the text:

    log_event("Downloading Elden Ring", category="download")
    log_event("Could not reach Steam", category="network", level=logging.ERROR)

A record reaches the pager when it is ``user_visible``, which defaults to true
for WARNING and above from our own packages and false otherwise. Nothing needs
to guess from the message text.

Rules of thumb for the two helpers here:

* ``log_event`` - something a user would want to know happened. Keep it short.
* ``log_step`` - bracket a multi-second operation so the log reads as a
  narrative and the pager shows progress rather than going stale.
"""

import logging
import time
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# Loggers belonging to ASSella itself. Third-party packages are excluded so
# urllib3/SteamClient chatter cannot reach the pager.
_OWN_PACKAGES = ("utils", "core", "managers", "ui", "ACCELA")


def is_our_logger(name: str) -> bool:
    """True when a logger name belongs to ASSella rather than a dependency."""
    return any(name == p or name.startswith(p + ".") for p in _OWN_PACKAGES)

# Categories, so callers and future UI can group or filter without parsing text.
GENERAL = "general"
DOWNLOAD = "download"
MANIFEST = "manifest"
CONFIG = "config"
COMPONENT = "component"
NETWORK = "network"
SYSTEM = "system"


def _is_ours(name: str) -> bool:
    return is_our_logger(name)


def log_event(
    message: str,
    *,
    category: str = GENERAL,
    level: int = logging.INFO,
    visible: Optional[bool] = None,
) -> None:
    """Log a user-facing event and let it reach the status pager.

    Args:
        message: Short, present tense, no trailing period. The pager truncates
            anything long, so aim for well under 90 characters.
        category: One of the module constants; purely for grouping.
        level: Normally INFO. Use WARNING/ERROR for things that need attention,
            which also makes the pager style them in the warning colour.
        visible: Force showing or hiding. Defaults to showing for WARNING and
            above from our own code, hiding otherwise.
    """
    if visible is None:
        # INFO and above from our own code reaches the pager; DEBUG does not,
        # and neither does anything from a third-party package. That measured
        # out at 89 pager updates for a full session, which is appropriate for a
        # strip that shows one line at a time, while the 681 lines of
        # urllib3/SteamClient chatter are excluded entirely.
        visible = level >= logging.INFO and _is_ours(logger.name)

    logger.log(
        level,
        message,
        extra={"user_visible": bool(visible), "category": category},
    )


def log_quiet(
    message: str,
    *,
    level: int = logging.INFO,
    category: str = GENERAL,
) -> None:
    """Log something useful for diagnostics that should never touch the pager.

    Use for per-item bookkeeping that would otherwise flood a one-line status
    bar - "checked 4 of 300 games", cache hits, unchanged status writes.
    """
    logger.log(
        level, message, extra={"user_visible": False, "category": category}
    )


@contextmanager
def log_step(
    name: str,
    *,
    category: str = GENERAL,
    failure_level: int = logging.ERROR,
    skipped_message: Optional[str] = None,
) -> Iterator[dict]:
    """Bracket a long operation so the log reads as a narrative.

    Emits a start line, then a success or failure line with the elapsed time.
    The pager shows the start immediately, so a slow operation shows progress
    instead of leaving the last unrelated message on screen.

    Yields a mutable dict for optional per-step fields, which are merged into
    the closing message as ``key=value``.

    Set ``skipped_message`` (e.g. "cancelled") in the yielded dict to record
    that the work did not complete. Without it, breaking out of the body early
    would report success for a job that never finished.
    """
    log_event(f"{name}...", category=category)
    extra: dict = {}
    start = time.monotonic()

    def _detail() -> str:
        parts = " ".join(f"{k}={v}" for k, v in extra.items())
        return f" ({parts})" if parts else ""

    try:
        yield extra
    except Exception as exc:
        log_event(
            f"{name} failed after {time.monotonic() - start:.1f}s{_detail()}: {exc}",
            category=category,
            level=failure_level,
        )
        raise

    elapsed = time.monotonic() - start
    if skipped_message and extra.get("result") == skipped_message:
        log_event(
            f"{name} {skipped_message} after {elapsed:.1f}s{_detail()}",
            category=category,
            level=logging.WARNING,
        )
        return
    log_event(f"{name} done in {elapsed:.1f}s{_detail()}", category=category)