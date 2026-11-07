"""Settings -> Companions: manage Uran (Decky) and Epsilon (browser bridge).

Both companions keep their own settings and work without ASSella; this page
only reads and writes their files (utils/companions.py).

Built to open instantly: the page is drawn from local files only. systemctl
and Epsilon's HTTP status run on a QThread and fill in the status line when
they answer.
"""

from __future__ import annotations

import logging
import subprocess
import time
from types import SimpleNamespace

from PyQt6.QtCore import QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui.dialogs.themed_message_box import ThemedMessageBox
from ui.sizes import size_control
from ui.widgets.modern_dropdown import ModernComboBox
from utils import companions as comp
from utils.helpers import create_checkbox_setting

logger = logging.getLogger(__name__)

MUTED = "color: rgba(255,255,255,0.6); font-size: 8.5pt; border: none; background: transparent;"
OK = "#81C784"
WARN = "#FFB74D"
OFF = "rgba(255,255,255,0.45)"


class _Job(QThread):
    """Runs one function off the GUI thread and hands back its result."""

    done = pyqtSignal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:  # never leave the page waiting
            logger.debug(f"[Companions] background job failed: {e}")
            self.done.emit(e)


def _status_label() -> QLabel:
    lbl = QLabel()
    lbl.setWordWrap(True)
    lbl.setStyleSheet(MUTED)
    return lbl


def _set_status(lbl: QLabel, text: str, color: str) -> None:
    lbl.setText(text)
    lbl.setStyleSheet(f"color: {color}; font-size: 8.5pt; font-weight: 600; border: none; background: transparent;")


def _small_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setFixedHeight(28)
    return b


def _toggle(holder, text: str, desc: str, checked: bool, on_change) -> QWidget:
    # A settings row that is not stored in ASSella's own settings: a holder
    # without .settings keeps CheckboxSetting from reading/writing QSettings.
    w = create_checkbox_setting(text, "_companion_unused", checked, holder, desc)
    w.setChecked(bool(checked))
    w.toggled.connect(on_change)
    return w


def _combo(options, current: str, on_change) -> ModernComboBox:
    c = ModernComboBox()
    for value, label in options:
        c.addItem(label, value)
    idx = c.findData(current)
    c.setCurrentIndex(idx if idx >= 0 else 0)
    c.currentIndexChanged.connect(lambda _i: on_change(c.currentData()))
    size_control(c, "lg")
    return c


def _row(label: str, widget: QWidget, desc: str = "") -> QWidget:
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(2, 4, 2, 4)
    text = QVBoxLayout()
    text.setSpacing(2)
    t = QLabel(label)
    t.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    text.addWidget(t)
    if desc:
        d = QLabel(desc)
        d.setWordWrap(True)
        d.setStyleSheet(MUTED)
        text.addWidget(d)
    lay.addLayout(text, 1)
    lay.addWidget(widget)
    return row


def _key_row(dialog, holder_layout, companion_key: str, write_key) -> None:
    """Shows whether the companion has its own Hubcap key; one click copies ASSella's."""
    assella_key = comp.assella_hubcap_key(dialog.settings)
    state = comp.key_state(companion_key, assella_key)
    lbl = QLabel()
    lbl.setWordWrap(True)
    btn = _small_button("Use ASSella's key")
    btn.setVisible(state == "own" and bool(assella_key))
    texts = {
        "same": ("Hubcap key: same as ASSella", OK),
        "none": ("Hubcap key: uses ASSella's key", OK),
        "own": ("Hubcap key: its own key, different from ASSella's", WARN),
    }
    _set_status(lbl, *texts[state])

    def _use():
        ts = int(time.time())
        try:
            ts = int(str(dialog.settings.value("morrenus_api_key_timestamp", ts)) or ts)
        except (TypeError, ValueError):
            pass
        if write_key(assella_key, ts):
            _set_status(lbl, *texts["same"])
            btn.hide()

    btn.clicked.connect(_use)
    row = QHBoxLayout()
    row.addWidget(lbl, 1)
    row.addWidget(btn)
    holder_layout.addLayout(row)


def _uran_card(dialog) -> QWidget:
    card, lay = dialog._create_card_frame("Uran (Decky plugin)")
    info = comp.uran_info()
    holder = SimpleNamespace(accent_color=dialog.accent_color)
    status = _status_label()
    lay.addWidget(status)

    if not info["installed"]:
        _set_status(status, "Not installed", OFF)
        hint = QLabel("Uran adds games to AT0-M from Steam's Gaming Mode through Decky Loader.")
        hint.setWordWrap(True)
        hint.setStyleSheet(MUTED)
        lay.addWidget(hint)
        get = _small_button("Get Uran ↗")
        get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(comp.URAN_REPO_URL)))
        lay.addWidget(get)
        return card

    _set_status(status, f"Installed · v{info['version']}" if info["version"] else "Installed", OK)
    s = info["settings"]

    def save(key):
        def _apply(value):
            if not comp.set_uran_setting(key, value):
                ThemedMessageBox.warning(dialog, "Uran", f"Could not write {info['settings_path']}.")
        return _apply

    lay.addWidget(_toggle(holder, "Fetch DLCs when adding", "Register the game's DLCs together with it.",
                          s.get("fetch_dlcs_on_inject", True), save("fetch_dlcs_on_inject")))
    lay.addWidget(_toggle(holder, "Add delisted DLCs", "Include delisted and bonus DLC manifests.",
                          s.get("add_delisted_dlcs", True), save("add_delisted_dlcs")))
    lay.addWidget(_toggle(holder, "Steam Store button", "An \"Add to Uran\" button on Steam Store pages.",
                          s.get("steam_store_enabled", True), save("steam_store_enabled")))
    lay.addWidget(_row("Notifications", _combo(comp.NOTIFICATION_OPTIONS, s.get("notification_mode", "auto"),
                                               save("notification_mode"))))
    lay.addWidget(_row("Hubcap route", _combo(comp.GATEWAY_OPTIONS, s.get("hubcap_gateway", "auto"),
                                              save("hubcap_gateway")),
                       "How Uran reaches the Hubcap API."))
    _key_row(dialog, lay, s.get("manifest_api_key", ""),
             lambda k, ts: comp.update_json(comp.uran_settings_path(),
                                            {"manifest_api_key": k, "manifest_api_key_timestamp": ts}))
    note = QLabel("Uran reads these the next time it loads them (reload Uran in Decky to apply at once).")
    note.setWordWrap(True)
    note.setStyleSheet(MUTED)
    lay.addWidget(note)
    return card


def _epsilon_card(dialog) -> QWidget:
    card, lay = dialog._create_card_frame("Epsilon (browser bridge) + Photar")
    info = comp.epsilon_info()
    holder = SimpleNamespace(accent_color=dialog.accent_color)
    status = _status_label()
    lay.addWidget(status)

    desc = QLabel("Photar adds \"Add to at0-m\" buttons on the Steam Store and SteamDB; Epsilon receives "
                  "them on 127.0.0.1:1952 and adds the game. It only runs while it is being used.")
    desc.setWordWrap(True)
    desc.setStyleSheet(MUTED)
    lay.addWidget(desc)

    if not info["installed"]:
        _set_status(status, "Not installed", OFF)
        get = _small_button("Get Uran / Epsilon ↗")
        get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(comp.URAN_REPO_URL)))
        lay.addWidget(get)
        return card

    ver = f" · v{info['version']}" if info["version"] else ""
    _set_status(status, f"Installed{ver} · checking…", OFF)

    # Service switches start disabled until systemctl answered.
    enabled_sw = _toggle(holder, "Epsilon bridge", "Start on demand when Photar sends a game (systemd socket).",
                         False, lambda v: _run_unit(dialog, comp.set_epsilon_enabled, v, refresh))
    updater_sw = _toggle(holder, "Daily auto-update", "Epsilon's own updater timer.",
                         False, lambda v: _run_unit(dialog, comp.set_epsilon_updater, v, refresh))
    for w in (enabled_sw, updater_sw):
        w.setEnabled(False)
        lay.addWidget(w)
    if not info["units_installed"]:
        enabled_sw.setToolTip("The systemd units are not installed yet: run the installer below.")

    s = info["settings"]

    def save(key):
        def _apply(value):
            if not comp.set_epsilon_setting(key, value):
                ThemedMessageBox.warning(dialog, "Epsilon", f"Could not write {info['settings_path']}.")
        return _apply

    lay.addWidget(_toggle(holder, "Fetch DLCs when adding", "Register the game's DLCs together with it.",
                          s.get("fetch_dlcs_on_inject", True), save("fetch_dlcs_on_inject")))
    lay.addWidget(_toggle(holder, "Add delisted DLCs", "Include delisted and bonus DLC manifests.",
                          s.get("add_delisted_dlcs", True), save("add_delisted_dlcs")))
    lay.addWidget(_row("Hubcap route", _combo(comp.GATEWAY_OPTIONS, s.get("hubcap_gateway", "auto"),
                                              save("hubcap_gateway"))))
    _key_row(dialog, lay, s.get("manifest_api_key", ""),
             lambda k, ts: comp.update_json(comp.epsilon_settings_path(),
                                            {"manifest_api_key": k, "manifest_api_key_timestamp": ts}))

    btns = QHBoxLayout()
    test_btn = _small_button("Test")
    photar_btn = _small_button("Install Photar ↗")
    photar_btn.setToolTip("Opens the userscript; your userscript manager (Tampermonkey, Violentmonkey) installs it.")
    photar_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(comp.PHOTAR_URL)))
    install_btn = _small_button("Install / repair service")
    install_btn.setToolTip("Runs Epsilon's install.sh (sets up the systemd user units).")
    btns.addWidget(test_btn)
    btns.addWidget(photar_btn)
    btns.addStretch(1)
    btns.addWidget(install_btn)
    lay.addLayout(btns)

    def _show(live):
        from PyQt6 import sip

        if sip.isdeleted(status) or sip.isdeleted(enabled_sw):
            return  # the page was closed before systemctl answered
        if isinstance(live, Exception):
            _set_status(status, f"Installed{ver} · status unknown", WARN)
            return
        sock, upd, st = live["socket"], live["updater"], live["status"]
        for w, state in ((enabled_sw, sock), (updater_sw, upd)):
            w.blockSignals(True)
            w.setChecked(state["enabled"] or state["active"])
            w.blockSignals(False)
            w.setEnabled(state["known"])
        if st and st.get("ok"):
            v = st.get("version") or info["version"]
            parts = [f"Running · v{v}" if v else "Running"]
            if not st.get("ready", True):
                parts.append(st.get("error") or "not ready")
            if not st.get("has_hubcap_key", True):
                parts.append("no Hubcap key")
            _set_status(status, " · ".join(parts), OK if st.get("ready", True) else WARN)
        elif sock["active"]:
            _set_status(status, f"Installed{ver} · waiting for requests", OK)
        else:
            _set_status(status, f"Installed{ver} · off", OFF)

    def refresh():
        job = _Job(comp.epsilon_live_status, dialog)
        job.done.connect(_show)
        job.finished.connect(job.deleteLater)
        dialog._companions_jobs = getattr(dialog, "_companions_jobs", []) + [job]
        job.start()

    test_btn.clicked.connect(refresh)

    def _install():
        cmd = comp.install_epsilon_command()
        if not cmd:
            ThemedMessageBox.warning(dialog, "Epsilon", "Epsilon's install.sh was not found.")
            return
        install_btn.setEnabled(False)
        install_btn.setText("Installing…")

        def _run():
            return subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        job = _Job(_run, dialog)

        def _done(res):
            from PyQt6 import sip

            if sip.isdeleted(install_btn):
                return
            install_btn.setEnabled(True)
            install_btn.setText("Install / repair service")
            if isinstance(res, Exception) or res.returncode != 0:
                tail = str(res) if isinstance(res, Exception) else (res.stderr or res.stdout)[-600:]
                ThemedMessageBox.warning(dialog, "Epsilon", f"The installer failed:\n\n{tail}")
            refresh()

        job.done.connect(_done)
        job.finished.connect(job.deleteLater)
        dialog._companions_jobs = getattr(dialog, "_companions_jobs", []) + [job]
        job.start()

    install_btn.clicked.connect(_install)
    refresh()
    return card


def _run_unit(dialog, fn, value, then) -> None:
    job = _Job(lambda: fn(bool(value)), dialog)

    def _done(res):
        if isinstance(res, Exception) or not res[0]:
            msg = str(res) if isinstance(res, Exception) else res[1]
            ThemedMessageBox.warning(dialog, "Epsilon", f"systemctl did not accept the change:\n\n{msg}")
        then()

    job.done.connect(_done)
    job.finished.connect(job.deleteLater)
    dialog._companions_jobs = getattr(dialog, "_companions_jobs", []) + [job]
    job.start()


def create_companions_tab(dialog) -> QWidget:
    from ui.dialogs.settings_tabs.downloads_tab import _scrolled

    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(10)
    intro = QLabel("Uran and Epsilon work on their own and with ASSella. Each keeps its own settings; "
                   "change them here.")
    intro.setWordWrap(True)
    intro.setStyleSheet(MUTED)
    layout.addWidget(intro)
    layout.addWidget(_uran_card(dialog))
    layout.addWidget(_epsilon_card(dialog))
    layout.addStretch(1)
    return _scrolled(page)
