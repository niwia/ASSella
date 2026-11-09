import time
import logging
import threading
from typing import Optional, Tuple

import requests
from PyQt6.QtCore import Qt, QUrl, QTimer, QThread, pyqtSignal, QMetaObject, Q_ARG
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QApplication,
    QScrollArea,
    QCheckBox,
    QTabWidget,
    QSizePolicy,
    QFrame,
)
from ui.widgets.modern_dropdown import ModernComboBox

from ui.dialogs.settings_tabs.morrenus_stats_widget import MorrenusStatsWidget
from ui.widgets.switch_toggle import SwitchToggle

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
            from utils.isp_bypass import execute_hubcap_request
            r = execute_hubcap_request(requests.Session(), "GET", url, timeout=4)
            ok = r.status_code == 200
            if not ok:
                err = f"HTTP {r.status_code}"
        elif prov == "steamrun":
            url = "https://manifest.steam.run/api/manifest/2716668420228619833"
            r = requests.get(url, headers={"User-Agent": "Valve/Steam HTTP Client 1.0"}, timeout=4.0)
            ok = r.status_code in (200, 400, 404)
            if not ok:
                err = f"HTTP {r.status_code}"
        elif prov == "opensteamtool":
            url = "https://manifest.opensteamtool.com/manifest/2716668420228619833"
            r = requests.get(url, headers={"User-Agent": "Valve/Steam HTTP Client 1.0"}, timeout=4.0)
            ok = r.status_code in (200, 400, 404)
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


_SMALL_BTN = (
    "QPushButton { background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.15);"
    " border-radius: 6px; color: #E8E8EE; font-size: 8.5pt; font-weight: 500; padding: 4px 12px; min-height: 22px; }"
    "QPushButton:hover { background: rgba(255,255,255,0.14); border-color: rgba(255,255,255,0.28); }"
    "QPushButton:disabled { color: rgba(255,255,255,0.3); }"
)
_LINK_BTN = (
    "QPushButton {{ background: transparent; border: none; color: {accent}; font-size: 8.5pt;"
    " font-weight: 600; padding: 2px 0; text-align: left; }}"
    "QPushButton:hover {{ text-decoration: underline; }}"
)


def set_key_status(label: QLabel, text: str, kind: str = "muted") -> None:
    """Small status text next to a key field: ok | bad | warn | muted."""
    color = {"ok": "#81C784", "bad": "#E57373", "warn": "#FFB74D"}.get(kind, "rgba(255,255,255,0.5)")
    label.setText(text)
    label.setStyleSheet(f"color: {color}; font-size: 8.5pt; font-weight: 600; background: transparent;")


def create_api_key_setting(
    dialog,
    label: str,
    placeholder: str,
    setting_key: str,
    help_url: Optional[str] = None,
    help_text: Optional[str] = None,
    hubcap_sync: Optional[bool] = None,
    on_test=None,
) -> Tuple[QVBoxLayout, QLineEdit]:
    """An API key row: masked field (first 5 characters visible), Show / Paste,
    a link to get a key, an optional Test button, and (for Hubcap) AT0-M Sync.

    The key is written to QSettings as it changes. ``on_test(key, status_label,
    button)`` runs when Test is pressed.
    """
    from ui.widgets.api_key_field import ApiKeyField

    if hubcap_sync is None:
        hubcap_sync = setting_key == "morrenus_api_key"
    accent = getattr(dialog, "accent_color", "#a1c9fd")

    layout = QVBoxLayout()
    layout.setSpacing(6)

    head = QHBoxLayout()
    head.setSpacing(8)
    lbl = QLabel(label.rstrip(":"))
    lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 600; border: none; background: transparent;")
    head.addWidget(lbl)
    head.addStretch(1)
    status = QLabel("")
    head.addWidget(status)
    layout.addLayout(head)

    if help_text:
        hint = QLabel(help_text)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 8.5pt; background: transparent;")
        layout.addWidget(hint)

    field_row = QHBoxLayout()
    field_row.setSpacing(6)
    api_key_input = ApiKeyField(placeholder=placeholder, accent=accent)
    api_key_input.setText(dialog.settings.value(setting_key, "", type=str))
    field_row.addWidget(api_key_input, 1)

    toggle_btn = QPushButton("Show")
    toggle_btn.setStyleSheet(_SMALL_BTN)
    toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    toggle_btn.clicked.connect(lambda: toggle_api_key_visibility(api_key_input, toggle_btn))
    field_row.addWidget(toggle_btn)

    paste_btn = QPushButton("Paste")
    paste_btn.setStyleSheet(_SMALL_BTN)
    paste_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    field_row.addWidget(paste_btn)
    layout.addLayout(field_row)

    foot = QHBoxLayout()
    foot.setSpacing(12)
    if help_url:
        get_key_btn = QPushButton("Get an API key \u2197")
        get_key_btn.setStyleSheet(_LINK_BTN.format(accent=accent))
        get_key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        get_key_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(help_url)))
        foot.addWidget(get_key_btn)
    if on_test is not None:
        test_btn = QPushButton("Test key")
        test_btn.setStyleSheet(_SMALL_BTN)
        test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        test_btn.clicked.connect(lambda: on_test(api_key_input.key(), status, test_btn))
        foot.addWidget(test_btn)
    foot.addStretch(1)

    sync_state = {"toggle": None}
    if hubcap_sync:
        from utils.sls_lua_config import (
            generate_hubcap_timestamp,
            get_hubcap_key_conf_path,
            sync_hubcap_api_key,
        )

        is_sync_on = dialog.settings.value("atom_hubcap_sync_enabled", False, type=bool)
        atom_sync_toggle = SwitchToggle(checked=is_sync_on, active_color=accent, width=34, height=18)
        sync_state["toggle"] = atom_sync_toggle
        sync_lbl = QLabel("AT0-M Sync")
        sync_lbl.setToolTip(
            "Keep the Hubcap API key in ASSella and in katamari.conf.lua (used by the AT0-M plugins) the same; "
            "the newer one wins."
        )
        conf_exists = get_hubcap_key_conf_path().is_file()
        if not conf_exists:
            atom_sync_toggle.setEnabled(False)
            atom_sync_toggle.setReadOnly(True)
            sync_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.35); font-size: 8.5pt; font-weight: 500;")
            atom_sync_toggle.setToolTip(
                "AT0-M Sync needs katamari.conf.lua. Save the plugin settings in Settings -> AT0-M -> Advanced first."
            )
        else:
            sync_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-size: 8.5pt; font-weight: 500;")

        def _on_sync_toggled(checked: bool):
            dialog.settings.setValue("atom_hubcap_sync_enabled", checked)
            if checked:
                synced, _msg = sync_hubcap_api_key(dialog.settings)
                if synced:
                    api_key_input.setText(dialog.settings.value(setting_key, "", type=str))

        atom_sync_toggle.toggled.connect(_on_sync_toggled)
        foot.addWidget(sync_lbl)
        foot.addWidget(atom_sync_toggle)

        if is_sync_on and conf_exists:
            synced, _msg = sync_hubcap_api_key(dialog.settings)
            if synced:
                api_key_input.setText(dialog.settings.value(setting_key, "", type=str))
    layout.addLayout(foot)

    def _store(cleaned: str):
        dialog.settings.setValue(setting_key, cleaned)
        if hubcap_sync:
            dialog.settings.setValue("morrenus_api_key_timestamp", str(generate_hubcap_timestamp()))
            tog = sync_state["toggle"]
            if tog is not None and tog.isChecked() and tog.isEnabled():
                sync_hubcap_api_key(dialog.settings)
        set_key_status(status, "Saved" if cleaned else "", "ok")

    def _on_paste():
        clip_text = QApplication.clipboard().text().strip()
        if clip_text:
            api_key_input.setText(clip_text)
            _store(clip_text)

    paste_btn.clicked.connect(_on_paste)
    api_key_input.keyChanged.connect(_store)

    if not api_key_input.key():
        set_key_status(status, "Not set", "muted")

    return layout, api_key_input


def toggle_api_key_visibility(input_field: QLineEdit, toggle_btn: QPushButton) -> None:
    """Toggle API key visibility."""
    if hasattr(input_field, "setRevealed"):
        reveal = not input_field.isRevealed()
        input_field.setRevealed(reveal)
        toggle_btn.setText("Hide" if reveal else "Show")
        return
    if input_field.echoMode() == QLineEdit.EchoMode.Password:
        input_field.setEchoMode(QLineEdit.EchoMode.Normal)
        toggle_btn.setText("Hide")
    else:
        input_field.setEchoMode(QLineEdit.EchoMode.Password)
        toggle_btn.setText("Show")


def on_isp_gateway_changed(dialog, index: int) -> None:
    """Saves selected mode immediately and triggers Tor warm boot if needed."""
    if not hasattr(dialog, "isp_gateway_combo") or not dialog.isp_gateway_combo:
        return
    mode = dialog.isp_gateway_combo.currentData() or "auto"
    dialog.settings.setValue("isp_bypass_mode", mode)
    dialog.settings.setValue("isp_bypass_hubcap", True)

    if mode == "tor":
        from utils.isp_bypass import TorManager
        threading.Thread(target=TorManager.start_tor_if_needed, daemon=True).start()


def test_single_gateway(dialog, gateway_key: str, btn: QPushButton, label: str) -> None:
    """Runs an individual gateway health check in a background thread and updates button."""
    btn.setEnabled(False)
    btn.setText("Testing...")
    btn.setStyleSheet("""
        QPushButton {
            background: #3b4261;
            border: 1px solid #e0af68;
            border-radius: 6px;
            padding: 5px 8px;
            color: #e0af68;
            font-size: 8.5pt;
            font-weight: bold;
            min-height: 26px;
        }
    """)

    def _target():
        import utils.isp_bypass as isp
        if gateway_key == "direct":
            ok, status, lat = isp.test_gateway_direct()
        elif gateway_key == "doh":
            ok, status, lat = isp.test_gateway_doh()
        elif gateway_key == "tor":
            ok, status, lat = isp.test_gateway_tor()
        elif gateway_key == "wirecutter":
            ok, status, lat = isp.test_gateway_wirecutter()
        else:
            ok, status, lat = False, "Unknown", 0

        QMetaObject.invokeMethod(
            dialog,
            "_handle_gateway_test_done",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(object, (btn, label, ok, status, lat)),
        )

    threading.Thread(target=_target, daemon=True).start()


def handle_gateway_test_done(dialog, data) -> None:
    btn, label, ok, status, lat = data
    btn.setEnabled(True)
    if ok:
        btn.setText(f"{label}: OK")
        btn.setToolTip(f"{label} Gateway: {status}")
        btn.setStyleSheet("""
            QPushButton {
                background: #1b5e20;
                border: 1px solid #4caf50;
                border-radius: 6px;
                padding: 5px 8px;
                color: #a5d6a7;
                font-size: 8.5pt;
                font-weight: bold;
                min-height: 26px;
            }
            QPushButton:hover {
                background: #2e7d32;
            }
        """)
    else:
        btn.setText(f"{label}: Fail")
        btn.setToolTip(f"{label} Gateway: {status}")
        btn.setStyleSheet("""
            QPushButton {
                background: #5c1d1d;
                border: 1px solid #e53935;
                border-radius: 6px;
                padding: 5px 8px;
                color: #ef9a9a;
                font-size: 8.5pt;
                font-weight: bold;
                min-height: 26px;
            }
            QPushButton:hover {
                background: #7a2626;
            }
        """)


def create_isp_gateway_card(dialog) -> Tuple[QFrame, QVBoxLayout]:
    """Create the Hubcap Gateway configuration and health tester card."""
    gateway_card, gateway_layout = dialog._create_card_frame("Hubcap Gateway")
    gateway_layout.setSpacing(8)

    isp_row = QHBoxLayout()
    isp_row.setContentsMargins(0, 0, 0, 0)
    isp_lbl = QLabel("Hubcap Gateway Mode:")
    isp_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    isp_lbl.setToolTip("Select how ASSella routes requests to Hubcap API (Auto Smart Fallback, Direct, DoH, Tor, or Wire).")
    isp_row.addWidget(isp_lbl)
    isp_row.addStretch()

    dialog.isp_gateway_combo = ModernComboBox()
    dialog.isp_gateway_combo.addItem("Auto", "auto")
    dialog.isp_gateway_combo.addItem("Direct", "direct")
    dialog.isp_gateway_combo.addItem("DoH", "doh")
    dialog.isp_gateway_combo.addItem("Tor", "tor")
    dialog.isp_gateway_combo.addItem("Wire", "wirecutter")
    dialog.isp_gateway_combo.setFixedWidth(115)

    saved_mode = dialog.settings.value("isp_bypass_mode", "auto", type=str)
    if not saved_mode:
        saved_mode = "auto"

    idx = dialog.isp_gateway_combo.findData(saved_mode)
    if idx >= 0:
        dialog.isp_gateway_combo.setCurrentIndex(idx)
    else:
        dialog.isp_gateway_combo.setCurrentIndex(0)

    dialog.isp_gateway_combo.currentIndexChanged.connect(
        lambda idx: on_isp_gateway_changed(dialog, idx)
    )
    isp_row.addWidget(dialog.isp_gateway_combo)
    gateway_layout.addLayout(isp_row)

    # 4-Button Gateway Health Tester Row
    test_bar_box = QVBoxLayout()
    test_bar_box.setSpacing(4)
    test_bar_box.setContentsMargins(0, 2, 0, 0)

    test_bar_lbl = QLabel("Gateway Health Check:")
    test_bar_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; border: none; background: transparent;")
    test_bar_box.addWidget(test_bar_lbl)

    dialog.gateway_btn_row = QHBoxLayout()
    dialog.gateway_btn_row.setSpacing(8)

    base_btn_css = """
        QPushButton {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 6px;
            padding: 5px 8px;
            color: #e0e0e0;
            font-size: 8.5pt;
            font-weight: 500;
            min-height: 26px;
        }
        QPushButton:hover {
            background: rgba(255, 255, 255, 0.15);
            border: 1px solid rgba(255, 255, 255, 0.25);
        }
        QPushButton:disabled {
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.05);
            color: rgba(255, 255, 255, 0.25);
        }
    """

    dialog.test_direct_btn = QPushButton("Direct")
    dialog.test_direct_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.test_direct_btn.setStyleSheet(base_btn_css)
    dialog.test_direct_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    dialog.test_direct_btn.clicked.connect(lambda: test_single_gateway(dialog, "direct", dialog.test_direct_btn, "Direct"))
    dialog.gateway_btn_row.addWidget(dialog.test_direct_btn, 1)

    dialog.test_doh_btn = QPushButton("DoH")
    dialog.test_doh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.test_doh_btn.setStyleSheet(base_btn_css)
    dialog.test_doh_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    dialog.test_doh_btn.clicked.connect(lambda: test_single_gateway(dialog, "doh", dialog.test_doh_btn, "DoH"))
    dialog.gateway_btn_row.addWidget(dialog.test_doh_btn, 1)

    dialog.test_tor_btn = QPushButton("Tor")
    dialog.test_tor_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.test_tor_btn.setStyleSheet(base_btn_css)
    dialog.test_tor_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    dialog.test_tor_btn.clicked.connect(lambda: test_single_gateway(dialog, "tor", dialog.test_tor_btn, "Tor"))
    dialog.gateway_btn_row.addWidget(dialog.test_tor_btn, 1)

    dialog.test_wirecutter_btn = QPushButton("Wirecutter")
    dialog.test_wirecutter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.test_wirecutter_btn.setStyleSheet(base_btn_css)
    dialog.test_wirecutter_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    dialog.test_wirecutter_btn.clicked.connect(lambda: test_single_gateway(dialog, "wirecutter", dialog.test_wirecutter_btn, "Wirecutter"))
    dialog.gateway_btn_row.addWidget(dialog.test_wirecutter_btn, 1)

    test_bar_box.addLayout(dialog.gateway_btn_row)
    gateway_layout.addLayout(test_bar_box)

    if saved_mode == "disabled":
        dialog.test_direct_btn.setEnabled(False)
        dialog.test_doh_btn.setEnabled(False)
        dialog.test_tor_btn.setEnabled(False)
        dialog.test_wirecutter_btn.setEnabled(False)

    return gateway_card, gateway_layout


def create_morrenus_tab(dialog) -> QWidget:
    """Create the Morrenus / Hubcap Integrations settings tab with Hubcap and SteamDB sub-tabs."""
    tab = QWidget()
    outer_layout = QVBoxLayout(tab)
    outer_layout.setContentsMargins(12, 10, 12, 10)
    outer_layout.setSpacing(8)

    integ_subtabs = QTabWidget()
    integ_subtabs.setUsesScrollButtons(True)
    bg_color = dialog.settings.value("background_color", "#141416")
    integ_subtabs.setStyleSheet(f"""
        QTabWidget::pane {{
            border: none;
        }}
        QTabBar::tab {{
            background: {bg_color};
            color: rgba(255, 255, 255, 0.6);
            padding: 6px 14px;
            border: none;
            font-weight: bold;
            font-size: 9pt;
        }}
        QTabBar::tab:selected {{
            color: {dialog.accent_color};
            border-bottom: 2px solid {dialog.accent_color};
        }}
        QTabBar::tab:hover {{
            color: #FFFFFF;
        }}
    """)

    # ── Subtab 1: Hubcap ──
    hubcap_scroll = QScrollArea()
    hubcap_scroll.setWidgetResizable(True)
    hubcap_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    hubcap_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    hubcap_container = QWidget()
    hubcap_layout = QVBoxLayout(hubcap_container)
    hubcap_layout.setContentsMargins(0, 8, 0, 0)
    hubcap_layout.setSpacing(12)

    # 1. API Key Card
    key_card, key_layout = dialog._create_card_frame("API Key")
    key_layout.setSpacing(8)

    morrenus_layout, dialog.api_key_input = create_api_key_setting(
        dialog,
        "Hubcap",
        "Paste your Hubcap API key",
        "morrenus_api_key",
        help_url="https://hubcapmanifest.com/",
        help_text="Used to fetch manifests and depot keys.",
    )
    key_layout.addLayout(morrenus_layout)
    hubcap_layout.addWidget(key_card)

    # 2. Hubcap Gateway & Health Check Card (moved from ASSella tab)
    gateway_card, _ = create_isp_gateway_card(dialog)
    hubcap_layout.addWidget(gateway_card)

    # 3. Stats Card
    stats_card, stats_layout = dialog._create_card_frame("Hubcap Stats")
    stats_layout.setContentsMargins(10, 8, 10, 8)

    dialog.morrenus_stats_widget = MorrenusStatsWidget()
    stats_layout.addWidget(dialog.morrenus_stats_widget)
    hubcap_layout.addWidget(stats_card)

    hubcap_layout.addStretch()
    hubcap_scroll.setWidget(hubcap_container)
    integ_subtabs.addTab(hubcap_scroll, "Hubcap")

    # ── Subtab 2: SteamDB ──
    steamdb_scroll = QScrollArea()
    steamdb_scroll.setWidgetResizable(True)
    steamdb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    steamdb_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    steamdb_container = QWidget()
    steamdb_layout = QVBoxLayout(steamdb_container)
    steamdb_layout.setContentsMargins(0, 8, 0, 0)
    steamdb_layout.setSpacing(12)

    # Byparr Cloudflare Solver Card
    from core.steamdb_scraper import ByparrManager

    byparr_card, byparr_layout = dialog._create_card_frame("Cloudflare Solver (Byparr)")
    byparr_layout.setContentsMargins(10, 8, 10, 10)
    byparr_layout.setSpacing(8)

    byparr_desc = QLabel(
        "Bypasses Cloudflare Turnstile protection on SteamDB to discover build history, "
        "patch metadata, and depot manifests."
    )
    byparr_desc.setWordWrap(True)
    byparr_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt; line-height: 1.3;")
    byparr_layout.addWidget(byparr_desc)

    status_row = QHBoxLayout()
    status_row.setSpacing(8)
    status_tag = QLabel("Status:")
    status_tag.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.5pt; font-weight: 600;")
    status_row.addWidget(status_tag)

    byparr_status_badge = QLabel("")
    status_row.addWidget(byparr_status_badge)
    status_row.addStretch()
    byparr_layout.addLayout(status_row)

    # 2 Buttons Row
    byparr_btn_row = QHBoxLayout()
    byparr_btn_row.setSpacing(8)

    btn_toggle_local = QPushButton("Enable Byparr")
    btn_toggle_local.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_toggle_local.setStyleSheet("""
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 6px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 500;
            padding: 6px 14px;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.16);
            border-color: rgba(255, 255, 255, 0.3);
        }
    """)

    btn_configure_custom = QPushButton("Configure Custom Bypasser")
    btn_configure_custom.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_configure_custom.setStyleSheet("""
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 6px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 500;
            padding: 6px 14px;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.16);
            border-color: rgba(255, 255, 255, 0.3);
        }
    """)

    def _update_byparr_ui():
        if ByparrManager.is_custom_service():
            url = ByparrManager.get_service_url()
            running = ByparrManager.is_running()
            stat_text = "Online" if running else "Configured"
            color = "#81C784" if running else "#FFB74D"
            byparr_status_badge.setText(f"Custom Bypasser ({stat_text}: {url})")
            byparr_status_badge.setStyleSheet(f"color: {color}; font-size: 8.5pt; font-weight: bold;")
            btn_toggle_local.setText("Enable Byparr")
        elif ByparrManager.find_byparr_dir():
            if ByparrManager.is_local_enabled():
                running = ByparrManager.is_running()
                stat_text = "Online" if running else "Ready on Demand"
                byparr_status_badge.setText(f"Local Managed ({stat_text})")
                byparr_status_badge.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: bold;")
                btn_toggle_local.setText("Disable Byparr")
            else:
                byparr_status_badge.setText("Local Managed (Disabled)")
                byparr_status_badge.setStyleSheet("color: rgba(255, 255, 255, 0.45); font-size: 8.5pt; font-weight: bold;")
                btn_toggle_local.setText("Enable Byparr")
        else:
            byparr_status_badge.setText("Not Configured / Missing")
            byparr_status_badge.setStyleSheet("color: #FFB74D; font-size: 8.5pt; font-weight: bold;")
            btn_toggle_local.setText("Enable Byparr")

    def _on_toggle_local():
        if not ByparrManager.find_byparr_dir():
            # Not deployed -> launch setup script
            try:
                from managers.cli_manager import open_cli_terminal
                dialog.settings.setValue("byparr_enabled", True)
                dialog.settings.setValue("disable_steamdb_scraping", False)
                open_cli_terminal(
                    "curl -sSL https://raw.githubusercontent.com/niwia/ASSella/beta/scripts/setup_byparr.sh | bash; "
                    "echo ''; echo 'Setup finished. Press Enter to close...'; read _"
                )
            except Exception as _ex:
                logger.warning(f"Could not launch local byparr setup: {_ex}")
        else:
            if ByparrManager.is_local_enabled():
                # Currently enabled -> disable it and stop process
                dialog.settings.setValue("byparr_enabled", False)
                ByparrManager.stop()
                logger.info("[ByparrConfig] Local Byparr disabled and stopped.")
            else:
                # Currently disabled -> enable it (mutual exclusion: clears custom URL)
                dialog.settings.setValue("byparr_url", "")
                dialog.settings.setValue("byparr_auth_header", "")
                dialog.settings.setValue("byparr_enabled", True)
                dialog.settings.setValue("disable_steamdb_scraping", False)
                logger.info("[ByparrConfig] Local Byparr enabled (custom URL cleared).")
                import threading
                threading.Thread(target=ByparrManager.ensure_running, daemon=True).start()

        _update_byparr_ui()
        if hasattr(dialog, "main_window") and hasattr(dialog.main_window, "check_steamdb_status"):
            dialog.main_window.check_steamdb_status()

    def _on_configure_custom():
        from ui.dialogs.custom_byparr_dialog import CustomByparrDialog
        dlg = CustomByparrDialog(dialog, accent_color=dialog.accent_color)
        if dlg.exec():
            _update_byparr_ui()
            if hasattr(dialog, "main_window") and hasattr(dialog.main_window, "check_steamdb_status"):
                dialog.main_window.check_steamdb_status()

    btn_toggle_local.clicked.connect(_on_toggle_local)
    btn_configure_custom.clicked.connect(_on_configure_custom)

    byparr_btn_row.addWidget(btn_toggle_local)
    byparr_btn_row.addWidget(btn_configure_custom)
    byparr_btn_row.addStretch()
    byparr_layout.addLayout(byparr_btn_row)

    _update_byparr_ui()
    steamdb_layout.addWidget(byparr_card)
    steamdb_layout.addStretch()
    steamdb_scroll.setWidget(steamdb_container)
    integ_subtabs.addTab(steamdb_scroll, "SteamDB")

    integ_subtabs.addTab(create_others_subtab(dialog), "Others")

    outer_layout.addWidget(integ_subtabs)

    # Connect tab change for lazy loading stats
    dialog.morrenus_tab_initialized = False

    dialog.tab_widget.addTab(tab, "Integrations")
    return tab


class _KeyCheckThread(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, check, key, parent=None):
        super().__init__(parent)
        self._check, self._key = check, key

    def run(self):
        try:
            ok, msg = self._check(self._key)
        except Exception as e:  # pragma: no cover - defensive
            ok, msg = False, str(e)
        self.done.emit(ok, msg)


def create_others_subtab(dialog) -> QWidget:
    """Integrations -> Others: SteamGridDB key and artwork preferences."""
    from utils import steamgriddb as sgdb

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
    container = QWidget()
    lay = QVBoxLayout(container)
    lay.setContentsMargins(0, 8, 0, 0)
    lay.setSpacing(12)

    card, card_lay = dialog._create_card_frame("SteamGridDB")
    card_lay.setSpacing(8)

    def _test(key, status, btn):
        set_key_status(status, "Checking...", "muted")
        btn.setEnabled(False)
        thread = _KeyCheckThread(sgdb.check_key, key, dialog)

        def _done(ok, msg):
            set_key_status(status, msg, "ok" if ok else "bad")
            btn.setEnabled(True)
            thread.deleteLater()

        thread.done.connect(_done)
        dialog._sgdb_check_thread = thread
        thread.start()

    key_layout, dialog.sgdb_key_input = create_api_key_setting(
        dialog,
        "API key",
        "Paste your SteamGridDB API key",
        sgdb.SETTING_KEY,
        help_url=sgdb.KEY_URL,
        help_text="Free with a SteamGridDB account (Profile -> Preferences -> API). "
                  "Community artwork for grids, heroes, logos and icons.",
        hubcap_sync=False,
        on_test=_test,
    )
    card_lay.addLayout(key_layout)

    sep = QFrame()
    sep.setFixedHeight(1)
    sep.setStyleSheet("background: rgba(255,255,255,0.08); border: none;")
    card_lay.addWidget(sep)

    pref_title = QLabel("Artwork preferences")
    pref_title.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 600; background: transparent;")
    card_lay.addWidget(pref_title)

    style_row = QHBoxLayout()
    style_lbl = QLabel("Grid style")
    style_lbl.setStyleSheet("color: rgba(255,255,255,0.85); font-size: 9pt; background: transparent;")
    style_row.addWidget(style_lbl)
    style_row.addStretch(1)
    style_combo = ModernComboBox()
    for value, text in sgdb.STYLES:
        style_combo.addItem(text, value)
    style_combo.setFixedWidth(150)
    idx = style_combo.findData(dialog.settings.value(sgdb.SETTING_STYLE, "any", type=str))
    style_combo.setCurrentIndex(idx if idx >= 0 else 0)
    style_combo.currentIndexChanged.connect(
        lambda _i: dialog.settings.setValue(sgdb.SETTING_STYLE, style_combo.currentData()))
    style_row.addWidget(style_combo)
    card_lay.addLayout(style_row)

    for setting, text, tip in (
        (sgdb.SETTING_FETCH_LOGOS, "Fetch game title graphics (Logos)",
         "On: lazy-loads official transparent title logos from SteamGridDB inside Game Details."),
        (sgdb.SETTING_NSFW, "Allow NSFW artwork", "Off: adult artwork is filtered out."),
        (sgdb.SETTING_HUMOR, "Allow humor artwork", "Off: joke / meme artwork is filtered out."),
    ):
        row = QHBoxLayout()
        lbl = QLabel(text)
        lbl.setToolTip(tip)
        lbl.setStyleSheet("color: rgba(255,255,255,0.85); font-size: 9pt; background: transparent;")
        row.addWidget(lbl)
        row.addStretch(1)
        tog = SwitchToggle(checked=dialog.settings.value(setting, False, type=bool),
                           active_color=dialog.accent_color, width=34, height=18)
        tog.toggled.connect(lambda checked, k=setting: dialog.settings.setValue(k, checked))
        row.addWidget(tog)
        card_lay.addLayout(row)

    note = QLabel("SteamGridDB provides community cover art, hero banners, and game logos for installed titles.")
    note.setWordWrap(True)
    note.setStyleSheet("color: rgba(255,255,255,0.45); font-size: 8pt; background: transparent;")
    card_lay.addWidget(note)

    lay.addWidget(card)
    lay.addStretch(1)
    scroll.setWidget(container)
    return scroll
