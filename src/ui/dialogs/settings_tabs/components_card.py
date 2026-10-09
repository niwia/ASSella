"""Optional Components card for Settings -> Tools.

Lets the user download / update the components that are no longer bundled in the
AppImage (Goldberg, Steamless, SLScheevo). Kept out of ``tools_tab.py`` so that
tab does not grow further; it is attached to it by ``create_components_card``.
"""

import logging
import os

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QWidget,
)

from utils.component_manager import (
    download_component,
    fetch_manifest,
    get_status,
    is_component_available,
    remove_component,
)

logger = logging.getLogger(__name__)

COMPONENT_ORDER = ("goldberg", "steamless", "slscheevo")


class _DownloadWorker(QThread):
    """Runs a component download off the GUI thread."""

    progress = pyqtSignal(int, int)
    finished_ok = pyqtSignal(str, str)   # key, message
    failed = pyqtSignal(str, str)       # key, message

    def __init__(self, key: str, manifest: dict, parent=None):
        super().__init__(parent)
        self._key = key
        self._manifest = manifest

    def run(self) -> None:
        try:
            ok, msg = download_component(
                self._key, progress_cb=self._emit_progress, manifest=self._manifest
            )
            (self.finished_ok if ok else self.failed).emit(self._key, msg)
        except Exception as e:  # noqa: BLE001 - surface anything to the UI
            self.failed.emit(self._key, str(e))

    def _emit_progress(self, done: int, total: int) -> None:
        self.progress.emit(int(done), int(total))


class _ManifestWorker(QThread):
    """Fetches component manifest asynchronously so Settings dialog never freezes."""

    manifest_ready = pyqtSignal(object)

    def run(self) -> None:
        try:
            m = fetch_manifest()
            self.manifest_ready.emit(m)
        except Exception:
            self.manifest_ready.emit(None)


def _accent(dialog) -> str:
    try:
        return dialog.accent_color or "#a1c9fd"
    except Exception:
        return "#a1c9fd"


def create_components_card(dialog) -> QWidget:
    """Build the 'Optional Components' card and attach it to ``dialog``."""
    ac = _accent(dialog)

    card, card_layout = dialog._create_card_frame("Optional Components")

    intro = QLabel(
        "These components are downloaded on demand and are not bundled with ASSella. "
        "Only download what you need."
    )
    intro.setWordWrap(True)
    intro.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 9pt;")
    card_layout.addWidget(intro)

    # dialog-owned state so the dialog can refresh/clean up
    dialog._component_rows = {}
    dialog._component_workers = {}
    dialog._component_manifest = None

    for key in COMPONENT_ORDER:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 4, 0, 4)
        row_layout.setSpacing(10)

        name_label = QLabel(key.capitalize())
        name_label.setFixedWidth(96)
        name_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 600;")
        row_layout.addWidget(name_label)

        status_label = QLabel("Checking...")
        status_label.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 9pt;")
        row_layout.addWidget(status_label, 1)

        bar = QProgressBar()
        bar.setFixedWidth(140)
        bar.setVisible(False)
        bar.setTextVisible(False)
        row_layout.addWidget(bar)

        btn = QPushButton("Download")
        btn.setFixedWidth(120)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(_btn_style(ac))
        btn.clicked.connect(lambda _c, k=key: _on_component_button(dialog, k))
        row_layout.addWidget(btn)

        card_layout.addWidget(row)
        dialog._component_rows[key] = {
            "row": row,
            "status": status_label,
            "button": btn,
            "progress": bar,
        }

    # Paint initial local state immediately (no remote manifest fetch on dialog startup)
    _refresh_components(dialog, force=False, fetch_remote=False)

    refresh_btn = QPushButton("Refresh")
    refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    refresh_btn.setStyleSheet(_btn_style(ac, subtle=True))
    refresh_btn.clicked.connect(lambda: _refresh_components(dialog, force=True, fetch_remote=True))
    card_layout.addWidget(refresh_btn, 0, Qt.AlignmentFlag.AlignLeft)

    return card


def _btn_style(accent: str, subtle: bool = False) -> str:
    if subtle:
        return f"""
            QPushButton {{
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 7px; color: rgba(255,255,255,0.75);
                padding: 5px 12px; font-size: 9pt;
            }}
            QPushButton:hover {{ border-color: {accent}; color: {accent}; }}
            QPushButton:disabled {{ color: rgba(255,255,255,0.25); }}
        """
    return f"""
        QPushButton {{
            background-color: rgba(255,255,255,0.08);
            border: 1px solid rgba(255,255,255,0.18);
            border-radius: 7px; color: #FFFFFF;
            padding: 5px 12px; font-size: 9pt; font-weight: 600;
        }}
        QPushButton:hover {{ background-color: rgba(255,255,255,0.16); border-color: {accent}; }}
        QPushButton:disabled {{
            background-color: rgba(255,255,255,0.03);
            border-color: rgba(255,255,255,0.08);
            color: rgba(255,255,255,0.25);
        }}
    """


def _describe(dialog, key: str) -> tuple:
    """(status text, button label, button enabled) for one component."""
    manifest = getattr(dialog, "_component_manifest", None)
    if not manifest:
        if is_component_available(key):
            return "Installed.", "Update", False
        return "Not installed.", "Download", True

    st = get_status(key, manifest)

    if not st.get("known"):
        return "Not listed in the remote manifest.", "Unavailable", False

    mb = (st.get("size_bytes") or 0) / 1048576
    size_txt = f"{mb:.1f} MB" if mb else "unknown size"

    if st.get("current") is True:
        return f"Installed and up to date ({size_txt}).", "Update", False
    if st.get("current") is None:
        return (
            "Bundled with an older ASSella build; download to get the current version.",
            "Download", True,
        )
    if st.get("installed"):
        return f"Update available ({size_txt}).", "Update", True
    return f"Not installed ({size_txt}).", "Download", True


def on_tools_tab_opened(dialog) -> None:
    """Fetch/update components remote manifest when the user enters the Tools tab."""
    if hasattr(dialog, "_component_rows"):
        _refresh_components(dialog, force=False, fetch_remote=True)


def _refresh_components(dialog, force: bool = False, fetch_remote: bool = True) -> None:
    """Re-read status for every row and update labels/buttons."""
    # Instantly paint current known state without blocking
    for key, widgets in dialog._component_rows.items():
        text, label, enabled = _describe(dialog, key)
        widgets["status"].setText(text)
        widgets["button"].setText(label)
        widgets["button"].setEnabled(enabled and not _busy(dialog, key))

    if fetch_remote and (force or getattr(dialog, "_component_manifest", None) is None):
        worker = getattr(dialog, "_manifest_worker", None)
        if worker and worker.isRunning():
            return
        worker = _ManifestWorker(dialog)
        dialog._manifest_worker = worker

        def _on_manifest_ready(m):
            dialog._component_manifest = m
            for k, w in dialog._component_rows.items():
                t, l, en = _describe(dialog, k)
                w["status"].setText(t)
                w["button"].setText(l)
                w["button"].setEnabled(en and not _busy(dialog, k))

        worker.manifest_ready.connect(_on_manifest_ready)
        worker.start()


def _busy(dialog, key: str) -> bool:
    worker = dialog._component_workers.get(key)
    return bool(worker and worker.isRunning())


def _on_component_button(dialog, key: str) -> None:
    """Start a download/update for one component."""
    if _busy(dialog, key):
        return

    manifest = getattr(dialog, "_component_manifest", None) or fetch_manifest()
    if not manifest:
        QMessageBox.warning(
            dialog, "Offline",
            "Could not reach the component server. Check your connection and try again.",
        )
        return

    widgets = dialog._component_rows[key]
    bar = widgets["progress"]
    bar.setVisible(True)
    bar.setRange(0, 0)          # indeterminate until Content-Length is known
    widgets["button"].setEnabled(False)
    widgets["status"].setText("Downloading...")

    worker = _DownloadWorker(key, manifest, dialog)
    dialog._component_workers[key] = worker
    worker.progress.connect(lambda d, t, b=bar: _on_progress(b, d, t))
    worker.finished_ok.connect(lambda k, m: _on_done(dialog, k, m, True))
    worker.failed.connect(lambda k, m: _on_done(dialog, k, m, False))
    worker.finished.connect(lambda k=key: _on_worker_finished(dialog, k))
    worker.start()


def _on_progress(bar: QProgressBar, done: int, total: int) -> None:
    if total > 0:
        bar.setRange(0, total)
        bar.setValue(done)
    else:
        bar.setRange(0, 0)


def _on_done(dialog, key: str, message: str, ok: bool) -> None:
    widgets = dialog._component_rows.get(key)
    if widgets:
        widgets["status"].setText(message if ok else f"Failed: {message}")
        if ok:
            widgets["progress"].setVisible(False)


def _on_worker_finished(dialog, key: str) -> None:
    dialog._component_workers.pop(key, None)
    _refresh_components(dialog)


# --------------------------------------------------------------------------
# Guards for callers outside this card
# --------------------------------------------------------------------------
COMPONENT_LABELS = {
    "goldberg": "Goldberg Emulator",
    "steamless": "Steamless",
    "slscheevo": "SLScheevo",
}


def _open_tools_tab(dialog) -> None:
    """Open (or re-focus) Settings -> Tools, where the download button lives."""
    mw = getattr(dialog, "main_window", None)
    if mw is None:
        pw = getattr(dialog, "parent_window", None)
        if pw is not None:
            mw = getattr(pw, "main_window", pw)
    if mw is not None and hasattr(mw, "open_settings"):
        mw.open_settings(initial_tab="Tools")
    else:
        from ui.dialogs.settings import SettingsDialog
        SettingsDialog(dialog, initial_tab="Tools").exec()


def prompt_component_missing(dialog, key: str) -> None:
    """Tell the user a component is not downloaded and send them to the button."""
    label = COMPONENT_LABELS.get(key, key)
    box = QMessageBox(dialog)
    box.setWindowTitle(f"{label} not installed")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(
        f"{label} is not downloaded yet.\n\n"
        "It is no longer bundled with ASSella - you only need it for the "
        "handful of games that require it."
    )
    go = box.addButton("Open Settings -> Tools", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is go:
        _open_tools_tab(dialog)


def require_component(dialog, key: str) -> bool:
    """Return True when the component's files exist, otherwise guide the user.

    Call this before any action that needs Goldberg/Steamless/SLScheevo so the
    failure surfaces as 'here is the download button' rather than a missing-file
    error deep in a subprocess call.
    """
    if is_component_available(key):
        return True
    prompt_component_missing(dialog, key)
    return False