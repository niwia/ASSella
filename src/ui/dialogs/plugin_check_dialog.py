"""Startup plugin check: shows what ASSella would change and lets the user decide.

Nothing is installed, updated or moved to .bak unless the user ticks it and
presses Apply. "Not now" asks again on the next start; "Don't ask again"
stays quiet for these items until the plugin manifest changes.
"""

from typing import Any, Dict, List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui.dialogs.themed_dialog import install_themed_frame


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
