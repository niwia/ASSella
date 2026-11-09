"""Startup plugin check: shows what ASSella would change and lets the user decide.

Nothing is installed, updated or moved to .bak unless the user ticks it and
presses Apply. "Not now" asks again on the next start; "Don't ask again"
stays quiet for these items until the plugin manifest changes.
"""

from typing import Any, Dict, List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui.dialogs.themed_dialog import install_themed_frame

import logging as _logging

logger = _logging.getLogger(__name__)


class PluginCheckDialog(QDialog):
    def __init__(self, actions: List[Dict[str, Any]], parent=None, accent_color: str = "#a1c9fd"):
        super().__init__(parent)
        self.setWindowTitle("Plugin check")
        self.setMinimumWidth(480)
        self.actions = actions
        self.choice = "later"  # "apply" | "later" | "never"
        self._boxes: List[QCheckBox] = []

        body = install_themed_frame(self, margins=(16, 10, 16, 14), spacing=10)
        intro = QLabel("Some SLSsteam plugins need attention. Tick what you want ASSella to do.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: rgba(255,255,255,0.85); font-size: 9pt;")
        body.addWidget(intro)

        for a in actions:
            row = QWidget()
            rl = QVBoxLayout(row)
            rl.setContentsMargins(0, 2, 0, 2)
            rl.setSpacing(0)
            cb = QCheckBox(a["title"])
            cb.setChecked(bool(a.get("default")))
            cb.setStyleSheet("font-size: 9pt;")
            rl.addWidget(cb)
            if a.get("detail"):
                d = QLabel(a["detail"])
                d.setWordWrap(True)
                d.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 8pt; margin-left: 26px;")
                rl.addWidget(d)
            body.addWidget(row)
            self._boxes.append(cb)

        btns = QHBoxLayout()
        never = QPushButton("Don't ask again")
        never.setToolTip("Stay quiet about these until the plugin manifest changes.")
        later = QPushButton("Not now")
        apply = QPushButton("Apply")
        apply.setDefault(True)
        apply.setStyleSheet(f"background: {accent_color}; color: #000; font-weight: 600; border: none;"
                            " border-radius: 6px; padding: 5px 16px;")
        for b in (never, later, apply):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(30)
        never.clicked.connect(lambda: self._done("never"))
        later.clicked.connect(lambda: self._done("later"))
        apply.clicked.connect(lambda: self._done("apply"))
        btns.addWidget(never)
        btns.addStretch(1)
        btns.addWidget(later)
        btns.addWidget(apply)
        body.addLayout(btns)

    def _done(self, choice: str) -> None:
        self.choice = choice
        self.accept()

    def selected(self) -> List[Dict[str, Any]]:
        return [a for a, cb in zip(self.actions, self._boxes) if cb.isChecked()]

    def unselected(self) -> List[Dict[str, Any]]:
        return [a for a, cb in zip(self.actions, self._boxes) if not cb.isChecked()]


class UnlistedPluginsDialog(QDialog):
    """Plugins in the SLSsteam folder that ASSella does not publish.

    Two answers: turn them off (moved to .bak, nothing deleted) or keep them
    and switch on "I'm using custom plugins". Closing asks again next start.
    """

    def __init__(self, paths, parent=None, accent_color: str = "#a1c9fd"):
        super().__init__(parent)
        self.setWindowTitle("Other SLSsteam plugins found")
        self.setMinimumWidth(500)
        self.choice = "later"  # "disable" | "custom" | "later"

        body = install_themed_frame(self, margins=(16, 10, 16, 14), spacing=10)

        intro = QLabel(
            "These plugins are in your SLSsteam plugins folder but are not part of the set "
            "ASSella installs and tests together:"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: rgba(255,255,255,0.85); font-size: 9pt;")
        body.addWidget(intro)

        names = sorted({p.name for p in paths})
        shown = names[:10]
        listing = QLabel("\n".join(shown) + (f"\n…and {len(names) - len(shown)} more" if len(names) > len(shown) else ""))
        listing.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        listing.setStyleSheet(
            "color: #FFFFFF; font-family: 'JetBrains Mono','DejaVu Sans Mono',monospace; font-size: 9pt;"
            " background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.12);"
            " border-radius: 6px; padding: 8px 10px;"
        )
        try:
            folders = sorted({str(p.parent) for p in paths})
            listing.setToolTip("\n".join(folders))
        except Exception as _silent_err:
            logger.debug("[plugin_check_dialog] __init__: ignored %s", _silent_err, exc_info=False)
        body.addWidget(listing)

        why = QLabel(
            "Other plugins can hook the same Steam functions as SpaceBunny and Styx. Two plugins "
            "doing that at once is a common cause of downloads that stall, fail with missing keys, "
            "or games that will not launch."
        )
        why.setWordWrap(True)
        why.setStyleSheet("color: rgba(255,255,255,0.6); font-size: 8.5pt;")
        body.addWidget(why)

        disable_hint = QLabel(
            "<b>Turn them off</b>: moved to <i>.bak</i> in the same folder, nothing is deleted. "
            "Rename a file back to turn it on again.<br>"
            "<b>Keep them</b>: switches on “I'm using custom plugins”. ASSella then stops managing "
            "and checking plugins; change it any time in Settings → AT0-M."
        )
        disable_hint.setWordWrap(True)
        disable_hint.setStyleSheet("color: rgba(255,255,255,0.75); font-size: 8.5pt;")
        body.addWidget(disable_hint)

        btns = QHBoxLayout()
        later = QPushButton("Not now")
        later.setToolTip("Ask again next time ASSella starts.")
        keep = QPushButton("Keep them (custom plugins)")
        off = QPushButton("Turn them off")
        off.setDefault(True)
        off.setStyleSheet(f"background: {accent_color}; color: #000; font-weight: 600; border: none;"
                          " border-radius: 6px; padding: 5px 16px;")
        for b in (later, keep, off):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(30)
        later.clicked.connect(lambda: self._done("later"))
        keep.clicked.connect(lambda: self._done("custom"))
        off.clicked.connect(lambda: self._done("disable"))
        btns.addWidget(later)
        btns.addStretch(1)
        btns.addWidget(keep)
        btns.addWidget(off)
        body.addLayout(btns)

    def _done(self, choice: str) -> None:
        self.choice = choice
        self.accept()
