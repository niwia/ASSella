import time
import logging
from typing import Optional, Tuple

import requests
from PyQt6.QtCore import Qt, QUrl, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QApplication,
    QScrollArea,
)

from ui.dialogs.settings_tabs.morrenus_stats_widget import MorrenusStatsWidget

logger = logging.getLogger(__name__)


def benchmark_mrc_provider(provider: str) -> Tuple[bool, int, str]:
    """Background helper to benchmark Manifest / MRC provider connectivity and latency."""
    start = time.perf_counter()
    ok = False
    err = ""
    prov = (provider or "auto").lower()
    try:
        if prov == "wudrm":
            url = "http://gmrc.wudrm.com/manifest/"
            r = requests.get(url, headers={"User-Agent": "Valve/Steam HTTP Client 1.0"}, timeout=4.0)
            ok = r.status_code in (200, 400, 404, 405)
            if not ok:
                err = f"HTTP {r.status_code}"
        elif prov == "manifestdex":
            url = "https://manifest.manifestdex.com/"
            headers = {"User-Agent": "ManifestDeX/1.0", "Accept": "*/*"}
            r = requests.get(url, headers=headers, timeout=4.0)
            ok = r.status_code < 500
            if not ok:
                err = f"HTTP {r.status_code}"
        elif prov == "hubcap":
            url = "https://hubcapmanifest.com/api/v1/health"
            r = requests.get(url, timeout=4.0)
            ok = r.status_code == 200
            if not ok:
                err = f"HTTP {r.status_code}"
        else:  # auto / race
            try:
                r1 = requests.get(
                    "http://gmrc.wudrm.com/manifest/",
                    headers={"User-Agent": "Valve/Steam HTTP Client 1.0"},
                    timeout=3.0,
                )
                if r1.status_code in (200, 400, 404, 405):
                    ok = True
            except Exception:
                pass

            if not ok:
                try:
                    r2 = requests.get(
                        "https://manifest.manifestdex.com/",
                        headers={"User-Agent": "ManifestDeX/1.0"},
                        timeout=3.0,
                    )
                    ok = r2.status_code < 500
                    if not ok:
                        err = f"HTTP {r2.status_code}"
                except Exception as e2:
                    err = str(e2)[:25]
    except requests.exceptions.Timeout:
        err = "Timeout"
    except requests.exceptions.ConnectionError:
        err = "Connection Refused"
    except Exception as exc:
        err = str(exc)[:25]

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return (ok, elapsed_ms, err)


def create_api_key_setting(
    dialog,
    label: str,
    placeholder: str,
    setting_key: str,
    help_url: Optional[str] = None,
    help_text: Optional[str] = None,
) -> Tuple[QVBoxLayout, QLineEdit]:
    """Create an API key input field with Get API Key, Show, and Paste buttons in a row below."""
    layout = QVBoxLayout()
    layout.setSpacing(6)

    lbl = QLabel(label)
    lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    layout.addWidget(lbl)

    api_key_input = QLineEdit()
    api_key_input.setPlaceholderText(placeholder)
    api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
    current_key = dialog.settings.value(setting_key, "", type=str)
    api_key_input.setText(current_key)
    layout.addWidget(api_key_input)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)

    if help_url:
        get_key_btn = QPushButton("Get API Key")
        get_key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        get_key_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(help_url)))
        btn_row.addWidget(get_key_btn)

    toggle_btn = QPushButton("Show")
    toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    toggle_btn.clicked.connect(lambda: toggle_api_key_visibility(api_key_input, toggle_btn))
    btn_row.addWidget(toggle_btn)

    paste_btn = QPushButton("Paste")
    paste_btn.setCursor(Qt.CursorShape.PointingHandCursor)

    def _on_paste():
        clip_text = QApplication.clipboard().text()
        if clip_text:
            api_key_input.setText(clip_text.strip())

    paste_btn.clicked.connect(_on_paste)
    btn_row.addWidget(paste_btn)

    btn_row.addStretch()
    layout.addLayout(btn_row)

    return layout, api_key_input


def toggle_api_key_visibility(input_field: QLineEdit, toggle_btn: QPushButton) -> None:
    """Toggle API key visibility."""
    if input_field.echoMode() == QLineEdit.EchoMode.Password:
        input_field.setEchoMode(QLineEdit.EchoMode.Normal)
        toggle_btn.setText("Hide")
    else:
        input_field.setEchoMode(QLineEdit.EchoMode.Password)
        toggle_btn.setText("Show")


def create_morrenus_tab(dialog) -> QWidget:
    """Create the Morrenus / Hubcap Integrations settings tab with scrollable container."""
    tab = QWidget()
    outer_layout = QVBoxLayout(tab)
    outer_layout.setContentsMargins(0, 0, 0, 0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)

    # 1. API Keys Card
    key_card, key_layout = dialog._create_card_frame("API Keys")
    key_layout.setSpacing(8)

    morrenus_layout, dialog.api_key_input = create_api_key_setting(
        dialog,
        "Hubcap API Key:",
        "Paste your Hubcap API key",
        "morrenus_api_key",
        help_url="https://hubcapmanifest.com/",
    )
    key_layout.addLayout(morrenus_layout)
    layout.addWidget(key_card)

    # 2. Stats Card
    stats_card, stats_layout = dialog._create_card_frame("Hubcap Stats")
    stats_layout.setContentsMargins(10, 8, 10, 8)

    dialog.morrenus_stats_widget = MorrenusStatsWidget()
    stats_layout.addWidget(dialog.morrenus_stats_widget)
    layout.addWidget(stats_card)

    # 3. Manifest / MRC Provider Card (Relocated below Hubcap)
    mrc_card, mrc_layout = dialog._create_card_frame("Manifest & MRC Provider")
    mrc_layout.setSpacing(6)

    mrc_desc = QLabel(
        "Select the provider for resolving Steam manifest decryption codes (MRC). "
        "Use 'Test Server' to benchmark provider responsiveness and health."
    )
    mrc_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    mrc_desc.setWordWrap(True)
    mrc_layout.addWidget(mrc_desc)

    row = QHBoxLayout()
    row.setSpacing(8)

    mrc_lbl = QLabel("Provider:")
    mrc_lbl.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500;")
    row.addWidget(mrc_lbl)

    dialog.manifest_provider_combo = QComboBox()
    dialog.manifest_provider_combo.setFixedWidth(160)
    dialog.manifest_provider_combo.addItem("Auto (Race Wudrm & ManifestDeX)", "auto")
    dialog.manifest_provider_combo.addItem("Wudrm (Primary)", "wudrm")
    dialog.manifest_provider_combo.addItem("ManifestDeX", "manifestdex")
    dialog.manifest_provider_combo.addItem("Hubcap Manifest (Direct API)", "hubcap")

    current_provider = dialog.settings.value("manifest_provider", "auto", type=str).lower()
    idx = dialog.manifest_provider_combo.findData(current_provider)
    if idx >= 0:
        dialog.manifest_provider_combo.setCurrentIndex(idx)
    else:
        dialog.manifest_provider_combo.setCurrentIndex(0)

    def _on_provider_changed(index):
        val = dialog.manifest_provider_combo.currentData()
        dialog.settings.setValue("manifest_provider", val)

    dialog.manifest_provider_combo.currentIndexChanged.connect(_on_provider_changed)
    row.addWidget(dialog.manifest_provider_combo)

    test_btn = QPushButton("Test Server")
    test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    row.addWidget(test_btn)

    test_status_lbl = QLabel("")
    test_status_lbl.setStyleSheet("font-size: 9pt; font-weight: 500;")
    row.addWidget(test_status_lbl)
    row.addStretch()

    def _on_test_clicked():
        provider = dialog.manifest_provider_combo.currentData() or "auto"
        test_btn.setEnabled(False)
        test_status_lbl.setText("Testing latency...")
        test_status_lbl.setStyleSheet("color: #90CAF9; font-size: 9pt;")

        from utils.task_runner import TaskRunner
        if not hasattr(dialog, "_mrc_test_runner") or dialog._mrc_test_runner is None:
            dialog._mrc_test_runner = TaskRunner(dialog)

        def _on_test_done(result):
            test_btn.setEnabled(True)
            if isinstance(result, tuple) and len(result) == 3:
                ok, elapsed_ms, err_msg = result
                if ok:
                    test_status_lbl.setText(f"✓ Operational ({elapsed_ms} ms)")
                    test_status_lbl.setStyleSheet("color: #81C784; font-size: 9pt; font-weight: bold;")
                else:
                    test_status_lbl.setText(f"⚠ Failed ({err_msg or 'Error'})")
                    test_status_lbl.setStyleSheet("color: #E57373; font-size: 9pt; font-weight: bold;")
            else:
                test_status_lbl.setText("⚠ Failed (Error)")
                test_status_lbl.setStyleSheet("color: #E57373; font-size: 9pt; font-weight: bold;")

        def _on_test_error(err_tuple):
            test_btn.setEnabled(True)
            test_status_lbl.setText("⚠ Failed (Error)")
            test_status_lbl.setStyleSheet("color: #E57373; font-size: 9pt; font-weight: bold;")

        worker = dialog._mrc_test_runner.run(benchmark_mrc_provider, provider)
        worker.finished.connect(_on_test_done)
        worker.error.connect(_on_test_error)

    test_btn.clicked.connect(_on_test_clicked)

    mrc_layout.addLayout(row)
    layout.addWidget(mrc_card)

    layout.addStretch()

    scroll.setWidget(container)
    outer_layout.addWidget(scroll)

    # Connect tab change for lazy loading stats
    dialog.morrenus_tab_initialized = False

    dialog.tab_widget.addTab(tab, "Integrations")
    return tab
