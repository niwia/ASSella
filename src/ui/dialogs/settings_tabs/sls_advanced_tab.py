"""Settings -> SLS -> Advanced: SLSsteam's own settings, read from and written to config.yaml.

config.yaml is the source of truth. Every change is written immediately
(switches on toggle, text and numbers when the field is left or Enter is
pressed) through ``utils.yaml.sls_settings.write_sls_setting``, which edits
only that one value in place. The page re-reads the file whenever it changes
on disk, so edits made elsewhere show up here.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from PyQt6.QtCore import QFileSystemWatcher, Qt, QTimer
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from utils.yaml.sls_settings import ADVANCED_SETTINGS, SlsSetting, read_descriptions, read_sls_settings, write_sls_setting

logger = logging.getLogger(__name__)

MUTED = "rgba(255, 255, 255, 0.55)"


class SlsConfigBinder:
    """Keeps controls and config.yaml in step: writes on change, reloads on file change."""

    def __init__(self, owner: QWidget):
        from utils.yaml_config_manager import get_user_config_path

        self.path = get_user_config_path()
        self.owner = owner
        self._setters: Dict[str, Callable[[Any], None]] = {}
        self._settings: Dict[str, SlsSetting] = {}
        self._status: Dict[str, QLabel] = {}
        self.watcher = QFileSystemWatcher(owner)
        if self.path.exists():
            self.watcher.addPath(str(self.path))
        self._reload_timer = QTimer(owner)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(250)
        self._reload_timer.timeout.connect(self.reload)
        self.watcher.fileChanged.connect(lambda _p: self._reload_timer.start())

    def bind(self, setting: SlsSetting, setter: Callable[[Any], None], status: QLabel) -> None:
        self._settings[setting.key] = setting
        self._setters[setting.key] = setter
        self._status[setting.key] = status

    def reload(self) -> None:
        # Editors that replace the file drop the watch; add it back.
        if self.path.exists() and str(self.path) not in self.watcher.files():
            self.watcher.addPath(str(self.path))
        values = read_sls_settings(self.path, list(self._settings))
        for key, setter in self._setters.items():
            try:
                setter(values.get(key))
            except Exception as e:
                logger.debug(f"[SlsAdvanced] Could not show {key}: {e}")

    def write(self, key: str, value: Any) -> bool:
        setting = self._settings[key]
        ok, msg = write_sls_setting(self.path, setting, value)
        lbl = self._status.get(key)
        if lbl is not None:
            if ok and msg == "saved":
                lbl.setText("Saved")
                lbl.setStyleSheet("color: #81C784; font-size: 8pt; background: transparent;")
                QTimer.singleShot(1500, lambda: lbl.setText("") if lbl.text() == "Saved" else None)
            elif not ok:
                lbl.setText(msg)
                lbl.setStyleSheet("color: #E57373; font-size: 8pt; background: transparent;")
        if not ok:
            logger.warning(f"[SlsAdvanced] {key}: {msg}")
            self.reload()  # put the control back to what the file says
        return ok


def _row(setting: SlsSetting, hint: str, tooltip: str, control: QWidget, status: QLabel) -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 4, 0, 4)
    lay.setSpacing(12)
    text = QVBoxLayout()
    text.setSpacing(1)
    title = QLabel(setting.label)
    title.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 600; background: transparent;")
    sub = QLabel(hint)
    sub.setWordWrap(True)
    sub.setStyleSheet(f"color: {MUTED}; font-size: 8.5pt; background: transparent;")
    text.addWidget(title)
    text.addWidget(sub)
    lay.addLayout(text, 1)
    status.setStyleSheet("font-size: 8pt; background: transparent;")
    lay.addWidget(status, 0, Qt.AlignmentFlag.AlignVCenter)
    lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    w.setToolTip(f"{setting.key}\n\n{tooltip}" if tooltip else setting.key)
    return w


def _line_edit_style(accent: str) -> str:
    return (
        "QLineEdit { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.14);"
        " border-radius: 6px; padding: 3px 8px; color: #FFFFFF; font-size: 9pt; }"
        f"QLineEdit:focus {{ border-color: {accent}; }}"
    )


def make_control(binder: SlsConfigBinder, setting: SlsSetting, accent: str, status: QLabel) -> QWidget:
    """A control for one setting, bound to config.yaml."""
    from utils.helpers import Material3Switch

    key = setting.key
    if setting.kind == "bool":
        sw = Material3Switch(checked=False, accent_color=accent)

        def _show(v, sw=sw):
            sw.blockSignals(True)
            sw.setChecked(bool(v))
            sw.blockSignals(False)

        sw.toggled.connect(lambda checked: binder.write(key, checked))
        binder.bind(setting, _show, status)
        return sw

    if setting.kind == "hex" and setting.flags:
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        boxes = []
        for bit, name in setting.flags:
            cb = QCheckBox(name)
            cb.setStyleSheet("color: #FFFFFF; font-size: 9pt; background: transparent;")
            hl.addWidget(cb)
            boxes.append((bit, cb))
        state = {"value": 0}

        def _show(v):
            try:
                state["value"] = int(v or 0)
            except (TypeError, ValueError):
                state["value"] = 0
            for bit, cb in boxes:
                cb.blockSignals(True)
                cb.setChecked(bool(state["value"] & bit))
                cb.blockSignals(False)

        def _changed():
            value = state["value"]
            for bit, cb in boxes:
                value = (value | bit) if cb.isChecked() else (value & ~bit)
            if binder.write(key, value):
                state["value"] = value

        for _bit, cb in boxes:
            cb.toggled.connect(lambda _c: _changed())
        binder.bind(setting, _show, status)
        return holder

    edit = QLineEdit()
    edit.setStyleSheet(_line_edit_style(accent))
    if setting.kind in ("int", "hex"):
        edit.setValidator(QIntValidator(setting.minimum, min(setting.maximum, 2_147_483_647)))
        edit.setFixedWidth(110)
        edit.setAlignment(Qt.AlignmentFlag.AlignRight)
    else:
        edit.setFixedWidth(220)
        edit.setPlaceholderText("(blank)")
    last = {"text": ""}

    def _show(v):
        text = "" if v is None else str(v)
        last["text"] = text
        if not edit.hasFocus():
            edit.setText(text)

    def _commit():
        text = edit.text().strip() if setting.kind != "str" else edit.text()
        if text == last["text"]:
            return
        if setting.kind in ("int", "hex"):
            if text == "":
                edit.setText(last["text"])
                return
            value: Any = int(text)
        else:
            value = text
        if binder.write(key, value):
            last["text"] = text

    edit.editingFinished.connect(_commit)
    binder.bind(setting, _show, status)
    return edit


def create_sls_advanced_tab(dialog) -> QWidget:
    accent = getattr(dialog, "accent_color", "#a1c9fd")
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 8, 0, 0)
    outer.setSpacing(0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
    inner = QWidget()
    inner.setStyleSheet("background: transparent;")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(0, 0, 6, 0)
    lay.setSpacing(12)

    binder = SlsConfigBinder(page)
    dialog.sls_advanced_binder = binder

    intro = QLabel(
        "SLSsteam's own settings, read from and saved straight to config.yaml. "
        "Changes are written as soon as you make them."
    )
    intro.setWordWrap(True)
    intro.setStyleSheet(f"color: {MUTED}; font-size: 8.5pt; background: transparent;")
    lay.addWidget(intro)

    if not binder.path.exists():
        missing = QLabel(f"SLSsteam's config.yaml was not found ({binder.path}). Install SLSsteam first.")
        missing.setWordWrap(True)
        missing.setStyleSheet("color: #FFB74D; font-size: 9pt; background: transparent;")
        lay.addWidget(missing)
        lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return page

    docs = read_descriptions(binder.path)
    groups: Dict[str, QVBoxLayout] = {}
    for setting in ADVANCED_SETTINGS:
        if setting.group not in groups:
            card, card_lay = dialog._create_card_frame(setting.group)
            card_lay.setSpacing(2)
            groups[setting.group] = card_lay
            lay.addWidget(card)
        status = QLabel("")
        control = make_control(binder, setting, accent, status)
        doc = docs.get(setting.key.split(".", 1)[0], "")
        groups[setting.group].addWidget(_row(setting, setting.hint, doc, control, status))

    lay.addStretch(1)
    scroll.setWidget(inner)
    outer.addWidget(scroll)
    binder.reload()
    return page
