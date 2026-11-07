"""Protection against system font setups that crash Qt's font fallback.

When text contains a character the UI font does not have (for example ``●``
or ``→`` with Open Sans), Qt asks fontconfig which installed font has it. On
some systems that lookup crashes the whole process inside fontconfig
(``FcCharSetHasChar``): typically a corrupt font cache or a broken font file.
A user hit this as a segfault every time Settings opened.

Two layers:

1. ``glyph()`` / ``supports()``: pick a character the UI font itself has, so
   common symbols (the API key mask, status dots, arrows) never need a
   fallback font.
2. A probe: a short-lived child process draws every symbol ASSella uses plus
   a few non-Latin scripts with fallback enabled. If the child crashes, this
   system's fallback is broken and the app switches to "safe font mode"
   (``QFont.StyleStrategy.NoFontMerging``): missing characters show as empty
   boxes instead of crashing. The result is cached against a fingerprint of
   the font setup and re-probed only when fonts or the font cache change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SETTING_RESULT = "font_fallback_probe"  # JSON: {"fp": str, "broken": bool}
PROBE_TIMEOUT_S = 20

# Symbols used in ASSella's UI, plus scripts that need a fallback font with
# any Latin UI font (game names, translations).
PROBE_TEXT = (
    "—•─→✓▾━…●⚠·–│✅✗✕➔⟳▲←☑▴↩“”×∞€£¥ℹ☐↻⬆★▼✖⚡⬇⚙┌┐└❌™®"
    " 日本語 한국어 中文 Ελληνικά Русский العربية עברית ไทย 🎮"
)

_probe_done = threading.Event()
_safe_mode = False
_hooked = False


# ── layer 1: characters the UI font really has ─────────────────────────────

def supports(ch: str, font=None) -> bool:
    """True when the UI font itself has ``ch`` (no fallback lookup involved)."""
    try:
        from PyQt6.QtGui import QFont, QRawFont
        from PyQt6.QtWidgets import QApplication

        f = font if font is not None else (QApplication.font() if QApplication.instance() else QFont())
        raw = QRawFont.fromFont(f)
        return raw.isValid() and all(raw.supportsCharacter(ord(c)) for c in ch)
    except Exception:
        return False


def glyph(preferred: str, fallback: str, font=None) -> str:
    """``preferred`` when the UI font has it, else ``fallback`` (plain ASCII)."""
    return preferred if supports(preferred, font) else fallback


def status_dot() -> str:
    """Status dot: ● when the UI font has it, else • or '*'."""
    return glyph("●", glyph("•", "*"))


def mask_char() -> str:
    """Character used to hide API keys: a bullet when the font has one, else '*'."""
    return glyph("•", "*")


# ── layer 2: detect a crashing fallback and avoid it ───────────────────────

def _font_dirs() -> list:
    home = Path.home()
    return [
        Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
        home / ".local/share/fonts", home / ".fonts",
        home / ".cache/fontconfig", Path("/var/cache/fontconfig"),
        Path("/etc/fonts"), home / ".config/fontconfig",
    ]


def _fingerprint() -> str:
    """Changes whenever fonts, the font cache or fontconfig settings change."""
    h = hashlib.sha1()
    for d in _font_dirs():
        try:
            st = d.stat()
            h.update(f"{d}:{st.st_mtime_ns}".encode())
            if d.is_dir():
                for child in sorted(d.iterdir())[:400]:
                    try:
                        h.update(f"{child.name}:{child.stat().st_mtime_ns}".encode())
                    except OSError as _silent_err:
                        logger.debug("[font_safety] _fingerprint: ignored %s", _silent_err, exc_info=False)
        except OSError:
            h.update(f"{d}:-".encode())
    try:
        from PyQt6.QtWidgets import QApplication

        h.update(QApplication.font().family().encode())
    except Exception as _silent_err:
        logger.debug("[font_safety] _fingerprint: ignored %s", _silent_err, exc_info=False)
    return h.hexdigest()


_PROBE_SCRIPT = r"""
import sys
from PyQt6.QtGui import QFont, QFontDatabase, QFontMetrics, QTextLayout
from PyQt6.QtWidgets import QApplication
app = QApplication(["assella-font-probe"])
for path in sys.argv[3:]:
    QFontDatabase.addApplicationFont(path)
f = QFont(sys.argv[1])
app.setFont(f)
text = sys.argv[2]
fm = QFontMetrics(f)
for ch in text:
    fm.boundingRect(ch)
fm.boundingRect(text)
lay = QTextLayout(text, f)
lay.beginLayout(); lay.createLine(); lay.endLayout()
print("ok")
"""


def _bundled_font_files() -> list:
    try:
        from utils.paths import Paths

        d = Paths.resource("fonts")
        return [str(p) for p in d.glob("*.[ot]tf")] if d.is_dir() else []
    except Exception:
        return []


def _run_probe(family: str) -> Optional[bool]:
    """True: fallback crashed; False: fine; None: could not tell."""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop("PYTHONFAULTHANDLER", None)
    try:
        r = subprocess.run(
            [sys.executable, "-I", "-c", _PROBE_SCRIPT, family, PROBE_TEXT, *_bundled_font_files()],
            env=env, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        logger.debug("[FontSafety] probe timed out")
        return None
    except Exception as e:
        logger.debug(f"[FontSafety] probe could not run: {e}")
        return None
    if r.returncode < 0:  # killed by a signal (SIGSEGV, SIGABRT)
        logger.warning(f"[FontSafety] font fallback probe crashed (signal {-r.returncode})")
        return True
    if r.returncode == 0 and "ok" in r.stdout:
        return False
    logger.debug(f"[FontSafety] probe exited {r.returncode}: {r.stderr.strip()[-300:]}")
    return None


def is_safe_mode() -> bool:
    return _safe_mode


def _apply_no_merging(app) -> None:
    from PyQt6.QtGui import QFont

    f = app.font()
    if not (f.styleStrategy() & QFont.StyleStrategy.NoFontMerging):
        f.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
        app.setFont(f)


def enable_safe_mode(app=None, reason: str = "") -> None:
    """Turn off font fallback for the whole app (and keep it off when the font changes)."""
    global _safe_mode, _hooked
    from PyQt6.QtWidgets import QApplication

    app = app or QApplication.instance()
    if app is None:
        return
    if not _safe_mode:
        logger.warning(
            "[FontSafety] Safe font mode on: this system's font fallback crashes "
            f"({reason or 'probe'}). Characters the UI font lacks show as boxes. "
            "Rebuilding the font cache usually fixes it: fc-cache -rv"
        )
    _safe_mode = True
    _apply_no_merging(app)
    if not _hooked:
        _hooked = True
        try:
            app.fontChanged.connect(lambda _f: _apply_no_merging(app) if _safe_mode else None)
        except Exception as _silent_err:
            logger.debug("[font_safety] enable_safe_mode: ignored %s", _silent_err, exc_info=False)


def init(app, settings=None) -> None:
    """Call once after the UI font is set. Never blocks the UI thread."""
    if not sys.platform.startswith("linux"):
        _probe_done.set()
        return
    if settings is None:
        from utils.settings import get_settings

        settings = get_settings()

    fp = _fingerprint()
    try:
        cached = json.loads(settings.value(SETTING_RESULT, "", type=str) or "{}")
    except ValueError:
        cached = {}
    if cached.get("fp") == fp:
        if cached.get("broken"):
            enable_safe_mode(app, "cached probe result")
        _probe_done.set()
        return

    family = app.font().family()

    def _worker():
        broken = _run_probe(family)
        if broken is not None:
            try:
                from utils.settings import get_settings

                get_settings().setValue(SETTING_RESULT, json.dumps({"fp": fp, "broken": broken}))
            except Exception as _silent_err:
                logger.debug("[font_safety] init: ignored %s", _silent_err, exc_info=False)
        if broken:
            from utils.main_thread import post_to_main_thread

            post_to_main_thread(enable_safe_mode, None, "probe crashed")
        _probe_done.set()

    threading.Thread(target=_worker, name="font-fallback-probe", daemon=True).start()


def wait_for_probe(timeout: float = 8.0) -> None:
    """Wait (briefly) for a running probe before building text-heavy windows.

    Pumps the event loop so a safe-mode switch posted by the probe lands first.
    """
    if _probe_done.is_set():
        return
    try:
        import time

        from PyQt6.QtWidgets import QApplication

        end = time.monotonic() + timeout
        while not _probe_done.is_set() and time.monotonic() < end:
            QApplication.processEvents()
            _probe_done.wait(0.05)
        QApplication.processEvents()
    except Exception:
        _probe_done.wait(timeout)
