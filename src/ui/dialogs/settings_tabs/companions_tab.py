"""Settings -> Companions: Uran (Decky plugin) and Epsilon (browser companion), one tab each.

Both companions work on their own and with ASSella, and keep their own
settings; this page only reads and writes their files (utils/companions.py).
When one is not installed its tab explains what it is and how to get it.

Opens instantly: everything is drawn from local files. systemctl, the
journal and Epsilon's /status run on a QThread and fill in when they answer.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.themed_message_box import ThemedMessageBox
from ui.sizes import size_control
from ui.widgets.modern_dropdown import ModernComboBox
from utils import companions as comp
from utils.helpers import create_checkbox_setting

logger = logging.getLogger(__name__)

MUTED = "color: rgba(255,255,255,0.6); font-size: 8.5pt; border: none; background: transparent;"
OK = "#81C784"
WARN = "#FFB74D"
BAD = "#E57373"
OFF = "rgba(255,255,255,0.45)"


# ---------------------------------------------------------------- helpers

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


def _start(dialog, fn, on_done) -> None:
    job = _Job(fn, dialog)
    job.done.connect(on_done)
    job.finished.connect(job.deleteLater)
    dialog._companions_jobs = [j for j in getattr(dialog, "_companions_jobs", []) if not sip.isdeleted(j)] + [job]
    job.start()


def _label(text: str = "", style: str = MUTED, wrap: bool = True) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(wrap)
    lbl.setStyleSheet(style)
    return lbl


def _colored(lbl: QLabel, text: str, color: str, bold: bool = True) -> None:
    lbl.setText(text)
    lbl.setStyleSheet(f"color: {color}; font-size: 9pt; {'font-weight: 600;' if bold else ''}"
                      " border: none; background: transparent;")


def _button(text: str, primary: bool = False, accent: str = "#a1c9fd") -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    size_control(b, None)
    if primary:
        b.setStyleSheet(f"QPushButton {{ background: {accent}; color: #000; font-weight: 600; border: none;"
                        " border-radius: 6px; padding: 0 16px; }")
    return b


def _toggle(dialog, text: str, desc: str, checked: bool, on_change) -> QWidget:
    # Not an ASSella setting: a holder without .settings keeps CheckboxSetting
    # from reading or writing QSettings.
    holder = SimpleNamespace(accent_color=dialog.accent_color)
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
    text.addWidget(_label(label, "color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;"))
    if desc:
        text.addWidget(_label(desc))
    lay.addLayout(text, 1)
    lay.addWidget(widget)
    return row


class _StatGrid(QWidget):
    """Small two-column grid of "name  value" stats."""

    def __init__(self, names):
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self._values = {}
        for i, name in enumerate(names):
            r, c = divmod(i, 2)
            grid.addWidget(_label(name, MUTED, wrap=False), r, c * 2)
            v = _label("…", "color: #FFFFFF; font-size: 9pt; border: none; background: transparent;", wrap=False)
            grid.addWidget(v, r, c * 2 + 1)
            self._values[name] = v
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

    def set(self, name: str, text: str, color: str = "#FFFFFF") -> None:
        v = self._values.get(name)
        if v is not None:
            _colored(v, text, color, bold=False)


def _key_row(dialog, lay, companion_key: str, write_key) -> None:
    """Only shown when the companion's Hubcap key differs from ASSella's.

    Normally the key is shared automatically (newest wins, through
    katamari.conf.lua), so a matching key needs no row at all.
    """
    assella_key = comp.assella_hubcap_key(dialog.settings)
    if comp.key_state(companion_key, assella_key) != "own" or not assella_key:
        return
    lbl = _label()
    _colored(lbl, "Hubcap key differs from ASSella's (turn on AT0-M Sync to share the newest one)", WARN)
    btn = _button("Use ASSella's key")

    def _use():
        try:
            ts = int(str(dialog.settings.value("morrenus_api_key_timestamp", 0)) or 0)
        except (TypeError, ValueError):
            ts = 0
        if write_key(assella_key, ts):
            _colored(lbl, "Hubcap key: now the same as ASSella's", OK)
            btn.hide()

    btn.clicked.connect(_use)
    row = QHBoxLayout()
    row.addWidget(lbl, 1)
    row.addWidget(btn)
    lay.addLayout(row)


def _show_output(dialog, title: str, ok: bool, text: str) -> None:
    if ok:
        ThemedMessageBox.information(dialog, title, text[-1200:] if text else "Done.")
    else:
        ThemedMessageBox.warning(dialog, title, text[-1200:] if text else "It did not finish.")


# ---------------------------------------------------------------- Uran

def _uran_page(dialog) -> QWidget:
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(4, 8, 4, 4)
    lay.setSpacing(10)
    info = comp.uran_info()

    if not info["installed"]:
        card, cl = dialog._create_card_frame("Install Uran")
        cl.addWidget(_label(
            "Uran is the Decky Loader companion for ASSella's AT0-M mode: search and add games, manage "
            "your AT0-M library, DLCs and per-game fixes, and share your Hubcap key - all from Steam's "
            "Gaming Mode, no keyboard needed."))
        if not info["decky"]:
            note = _label()
            _colored(note, "Decky Loader was not found. Uran runs inside Decky, so install Decky first.", WARN)
            cl.addWidget(note)
        row = QHBoxLayout()
        install = _button("Install Uran ↗", primary=True, accent=dialog.accent_color)
        install.setToolTip("Opens Uran's page with the installation steps.")
        install.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(comp.URAN_REPO_URL)))
        row.addWidget(install)
        if not info["decky"]:
            decky = _button("Get Decky Loader ↗")
            decky.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(comp.DECKY_URL)))
            row.addWidget(decky)
        row.addStretch(1)
        cl.addLayout(row)
        lay.addWidget(card)
        lay.addStretch(1)
        return page

    # Status
    card, cl = dialog._create_card_frame("Uran")
    stats = _StatGrid(["Status", "Version"])
    stats.set("Status", "Installed", OK)
    stats.set("Version", info["version"] or "unknown")
    cl.addWidget(stats)
    row = QHBoxLayout()
    check_btn = _button("Check for updates")
    guide_btn = _button("Guide ↗")
    guide_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(comp.URAN_GUIDE_URL)))
    get_btn = _button("Get update ↗", primary=True, accent=dialog.accent_color)
    get_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(comp.URAN_RELEASES_URL)))
    get_btn.hide()
    row.addWidget(check_btn)
    row.addWidget(guide_btn)
    row.addStretch(1)
    row.addWidget(get_btn)
    cl.addLayout(row)
    update_lbl = _label()
    update_lbl.hide()
    cl.addWidget(update_lbl)

    def _check():
        check_btn.setEnabled(False)
        check_btn.setText("Checking…")

        def _done(res):
            if sip.isdeleted(check_btn):
                return
            check_btn.setEnabled(True)
            check_btn.setText("Check for updates")
            update_lbl.show()
            if isinstance(res, Exception) or res.get("error"):
                _colored(update_lbl, "Could not reach GitHub. Try again later.", WARN)
            elif res["newer"]:
                _colored(update_lbl, f"Uran {res['latest']} is available. Update it from Decky Loader "
                                     "or install the new Uran.zip.", OK)
                get_btn.show()
            else:
                _colored(update_lbl, f"Up to date ({res['latest']}).", OK, bold=False)

        _start(dialog, lambda: comp.uran_update_state(info["version"]), _done)

    check_btn.clicked.connect(_check)
    lay.addWidget(card)

    # Settings
    card, cl = dialog._create_card_frame("Uran settings")
    s = info["settings"]

    def save(key):
        def _apply(value):
            if not comp.set_uran_setting(key, value):
                ThemedMessageBox.warning(dialog, "Uran", f"Could not write {info['settings_path']}.")
        return _apply

    cl.addWidget(_toggle(dialog, "Fetch DLCs when adding", "Register the game's DLCs together with it.",
                         s.get("fetch_dlcs_on_inject", True), save("fetch_dlcs_on_inject")))
    cl.addWidget(_toggle(dialog, "Add delisted DLCs", "Include delisted and bonus DLC manifests.",
                         s.get("add_delisted_dlcs", True), save("add_delisted_dlcs")))
    cl.addWidget(_toggle(dialog, "Steam Store button", "An \"Add to Uran\" button on Steam Store pages.",
                         s.get("steam_store_enabled", True), save("steam_store_enabled")))
    cl.addWidget(_row("Notifications", _combo(comp.NOTIFICATION_OPTIONS, s.get("notification_mode", "auto"),
                                              save("notification_mode"))))
    cl.addWidget(_row("Hubcap route", _combo(comp.GATEWAY_OPTIONS, s.get("hubcap_gateway", "auto"),
                                             save("hubcap_gateway")), "How Uran reaches the Hubcap API."))
    _key_row(dialog, cl, s.get("manifest_api_key", ""),
             lambda k, ts: comp.update_json(comp.uran_settings_path(),
                                            {"manifest_api_key": k, "manifest_api_key_timestamp": ts}))
    cl.addWidget(_label("Saved to Uran's own settings file; Uran picks changes up the next time it reads them."))
    lay.addWidget(card)
    lay.addStretch(1)
    return page


# ---------------------------------------------------------------- Epsilon

EPSILON_ABOUT = (
    "Epsilon is the browser companion for AT0-M. With the Photar userscript, Steam Store and SteamDB "
    "pages get an \"Add to at0-m\" button; Epsilon receives the click on your computer "
    "(127.0.0.1:1952) and adds the game to SLSsteam, the same way ASSella does. It sleeps until "
    "it is used, so it costs nothing while idle."
)


def _epsilon_install_page(dialog, page_lay, rebuild) -> None:
    card, cl = dialog._create_card_frame("Install Epsilon - browser companion for AT0-M")
    cl.addWidget(_label(EPSILON_ABOUT))
    cl.addWidget(_label("Installs to ~/epsilon and sets up a small systemd user service. "
                        "Then install Photar in your browser's userscript manager."))
    row = QHBoxLayout()
    install = _button("Install Epsilon", primary=True, accent=dialog.accent_color)
    install.setToolTip(f"Runs: curl -fsSL {comp.EPSILON_INSTALL_URL} | bash")
    guides = _button("Guides ↗")
    guides.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(comp.EPSILON_GUIDE_URL)))
    row.addWidget(install)
    row.addWidget(guides)
    row.addStretch(1)
    cl.addLayout(row)
    status = _label()
    cl.addWidget(status)

    def _install():
        install.setEnabled(False)
        install.setText("Installing…")
        _colored(status, "Downloading and setting up Epsilon…", OFF)

        def _done(res):
            if sip.isdeleted(install):
                return
            install.setEnabled(True)
            install.setText("Install Epsilon")
            ok, out = (False, str(res)) if isinstance(res, Exception) else res
            _show_output(dialog, "Install Epsilon", ok, out)
            if ok:
                rebuild()
            else:
                _colored(status, "The installer did not finish (details shown).", BAD)

        _start(dialog, lambda: comp.run_epsilon_action("install"), _done)

    install.clicked.connect(_install)
    page_lay.addWidget(card)
    page_lay.addStretch(1)


def _epsilon_installed_page(dialog, page_lay, info, rebuild) -> None:
    # 1. Live status
    card, cl = dialog._create_card_frame("Epsilon")
    stats = _StatGrid(["Bridge", "Version", "Auto-update", "Games added (7 days)", "Last added"])
    stats.set("Version", info["version"] or "unknown")
    cl.addWidget(stats)

    actions = QHBoxLayout()
    refresh_btn = _button("Refresh")
    restart_btn = _button("Restart")
    update_btn = _button("Update")
    logs_btn = _button("Logs")
    guides_btn = _button("Guides ↗")
    guides_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(comp.EPSILON_GUIDE_URL)))
    for b in (refresh_btn, restart_btn, update_btn, logs_btn):
        actions.addWidget(b)
    actions.addStretch(1)
    actions.addWidget(guides_btn)
    cl.addLayout(actions)
    page_lay.addWidget(card)

    # 2. Service switches + Photar
    card2, cl2 = dialog._create_card_frame("Service")
    enabled_sw = _toggle(dialog, "Epsilon bridge", "Listen for Photar on 127.0.0.1:1952 (wakes on demand).",
                         False, lambda v: _unit(comp.set_epsilon_enabled, v))
    updater_sw = _toggle(dialog, "Daily auto-update", "Epsilon's own update timer.",
                         False, lambda v: _unit(comp.set_epsilon_updater, v))
    for w in (enabled_sw, updater_sw):
        w.setEnabled(False)
        cl2.addWidget(w)
    row = QHBoxLayout()
    photar_btn = _button("Install Photar ↗")
    photar_btn.setToolTip("Opens the userscript; Tampermonkey or Violentmonkey installs it.")
    photar_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(comp.PHOTAR_URL)))
    repair_btn = _button("Repair service")
    repair_btn.setToolTip("Runs Epsilon's install.sh again (systemd units, socket).")
    uninstall_btn = _button("Uninstall")
    uninstall_btn.setStyleSheet(f"QPushButton {{ color: {BAD}; }}")
    row.addWidget(photar_btn)
    row.addStretch(1)
    row.addWidget(repair_btn)
    row.addWidget(uninstall_btn)
    cl2.addLayout(row)
    page_lay.addWidget(card2)

    # 3. Settings (bottom)
    card3, cl3 = dialog._create_card_frame("Epsilon settings")
    s = info["settings"]

    def save(key):
        def _apply(value):
            if not comp.set_epsilon_setting(key, value):
                ThemedMessageBox.warning(dialog, "Epsilon", f"Could not write {info['settings_path']}.")
        return _apply

    cl3.addWidget(_toggle(dialog, "Fetch DLCs when adding", "Register the game's DLCs together with it.",
                          s.get("fetch_dlcs_on_inject", True), save("fetch_dlcs_on_inject")))
    cl3.addWidget(_toggle(dialog, "Add delisted DLCs", "Include delisted and bonus DLC manifests.",
                          s.get("add_delisted_dlcs", True), save("add_delisted_dlcs")))
    cl3.addWidget(_row("Hubcap route", _combo(comp.GATEWAY_OPTIONS, s.get("hubcap_gateway", "auto"),
                                              save("hubcap_gateway")), "How Epsilon reaches the Hubcap API."))
    _key_row(dialog, cl3, s.get("manifest_api_key", ""),
             lambda k, ts: comp.update_json(comp.epsilon_settings_path(),
                                            {"manifest_api_key": k, "manifest_api_key_timestamp": ts}))
    cl3.addWidget(_label("Saved to Epsilon's own settings file; used the next time the bridge wakes."))
    page_lay.addWidget(card3)
    page_lay.addStretch(1)

    busy_buttons = (refresh_btn, restart_btn, update_btn, repair_btn, uninstall_btn)

    def _busy(on: bool):
        for b in busy_buttons:
            if not sip.isdeleted(b):
                b.setEnabled(not on)

    def _show(live):
        if sip.isdeleted(stats):
            return
        _busy(False)
        if isinstance(live, Exception):
            stats.set("Bridge", "unknown", WARN)
            return
        sock, upd, st, sd = live["socket"], live["updater"], live["status"] or {}, live["stats"] or {}
        if not live["systemd"]:
            stats.set("Bridge", "no systemd user session", WARN)
        elif st.get("ok"):
            stats.set("Bridge", "running", OK)
        elif sock["active"]:
            stats.set("Bridge", "listening (asleep)", OK)
        else:
            stats.set("Bridge", "off", OFF)
        if st.get("version"):
            stats.set("Version", str(st["version"]))
        stats.set("Auto-update", "on" if (upd["enabled"] or upd["active"]) else "off",
                  OK if (upd["enabled"] or upd["active"]) else OFF)
        if sd.get("available"):
            added = sd.get("added", 0)
            failed = sd.get("failed", 0)
            stats.set("Games added (7 days)", f"{added}" + (f" ({failed} failed)" if failed else ""),
                      WARN if failed else "#FFFFFF")
            stats.set("Last added", sd.get("last") or "—")
        else:
            stats.set("Games added (7 days)", "—", OFF)
            stats.set("Last added", "—", OFF)
        for w, state in ((enabled_sw, sock), (updater_sw, upd)):
            w.blockSignals(True)
            w.setChecked(state["enabled"] or state["active"])
            w.blockSignals(False)
            w.setEnabled(live["systemd"] and state["known"])
        if not sock["known"]:
            enabled_sw.setToolTip("The service is not set up: use Repair service.")

    def refresh():
        _busy(True)
        _start(dialog, comp.epsilon_live_status, _show)

    def _unit(fn, value):
        def _done(res):
            if isinstance(res, Exception) or not res[0]:
                msg = str(res) if isinstance(res, Exception) else res[1]
                ThemedMessageBox.warning(dialog, "Epsilon", f"systemctl did not accept the change:\n\n{msg}")
            refresh()

        _start(dialog, lambda: fn(bool(value)), _done)

    def _action(name: str, title: str):
        def _go():
            _busy(True)

            def _done(res):
                if sip.isdeleted(stats):
                    return
                ok, out = (False, str(res)) if isinstance(res, Exception) else res
                _show_output(dialog, title, ok, out)
                refresh()

            _start(dialog, lambda: comp.run_epsilon_action(name), _done)
        return _go

    def _uninstall():
        choice = ThemedMessageBox.choices(
            dialog, "Uninstall Epsilon",
            "Stop Epsilon and remove its systemd service?\n\n"
            "You can also delete its files (the Epsilon folder). Your settings file is kept either way.",
            [("Cancel", "cancel"), ("Remove service", "service"), ("Remove service and files", "all")],
            default="cancel", severity="warning",
        )
        if choice not in ("service", "all"):
            return
        _busy(True)

        def _work():
            ok, out = comp.run_epsilon_action("uninstall")
            if ok and choice == "all":
                gone, where = comp.remove_epsilon_files()
                out += ("\n\nDeleted " + where) if gone and where else ("\n\n" + where if where else "")
            return ok, out

        def _done(res):
            ok, out = (False, str(res)) if isinstance(res, Exception) else res
            _show_output(dialog, "Uninstall Epsilon", ok, out)
            rebuild()

        _start(dialog, _work, _done)

    def _logs():
        def _done(text):
            from PyQt6.QtWidgets import QDialog

            from ui.dialogs.themed_dialog import install_themed_frame

            d = QDialog(dialog)
            d.setWindowTitle("Epsilon log")
            d.resize(720, 420)
            body = install_themed_frame(d, margins=(12, 8, 12, 12), spacing=8)
            view = QPlainTextEdit(str(text))
            view.setReadOnly(True)
            view.setStyleSheet("font-family: 'JetBrains Mono','DejaVu Sans Mono',monospace; font-size: 8.5pt;")
            body.addWidget(view)
            view.moveCursor(view.textCursor().MoveOperation.End)
            d.exec()

        _start(dialog, comp.epsilon_log, _done)

    refresh_btn.clicked.connect(refresh)
    restart_btn.clicked.connect(_action("restart", "Restart Epsilon"))
    update_btn.clicked.connect(_action("update", "Update Epsilon"))
    repair_btn.clicked.connect(_action("repair", "Repair Epsilon"))
    uninstall_btn.clicked.connect(_uninstall)
    logs_btn.clicked.connect(_logs)
    refresh()


def _epsilon_page(dialog) -> QWidget:
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(4, 8, 4, 4)
    lay.setSpacing(10)

    def rebuild():
        if sip.isdeleted(page):
            return
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        _fill()

    def _fill():
        info = comp.epsilon_info()
        if info["installed"]:
            _epsilon_installed_page(dialog, lay, info, rebuild)
        else:
            _epsilon_install_page(dialog, lay, rebuild)

    _fill()
    return page


# ---------------------------------------------------------------- page

def create_companions_tab(dialog) -> QWidget:
    from ui.dialogs.settings_tabs.downloads_tab import _scrolled

    tab = QWidget()
    main = QVBoxLayout(tab)
    main.setContentsMargins(12, 10, 12, 10)
    main.setSpacing(8)

    sub = QTabWidget()
    sub.setUsesScrollButtons(True)
    bg = dialog.settings.value("background_color", "#141416")
    sub.setStyleSheet(f"""
        QTabWidget::pane {{ border: none; }}
        QTabBar::tab {{ background: {bg}; color: rgba(255,255,255,0.6); padding: 6px 16px;
                        border: none; font-weight: bold; font-size: 9.5pt; }}
        QTabBar::tab:selected {{ color: {dialog.accent_color}; border-bottom: 2px solid {dialog.accent_color}; }}
        QTabBar::tab:hover {{ color: #FFFFFF; }}
    """)
    # Each sub-page is built when first shown, like the Settings pages.
    builders = {0: lambda: _uran_page(dialog), 1: lambda: _epsilon_page(dialog)}
    holders = []
    for name in ("Uran", "Epsilon"):
        holder = QWidget()
        QVBoxLayout(holder).setContentsMargins(0, 0, 0, 0)
        holders.append(holder)
        sub.addTab(holder, name)

    def _ensure(index: int):
        build = builders.pop(index, None)
        if build is not None:
            holders[index].layout().addWidget(_scrolled(build()))

    sub.currentChanged.connect(_ensure)
    _ensure(0)
    main.addWidget(sub)
    return tab
