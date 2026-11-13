"""Settings subtab for Stealth proxy and VPN routing."""

import logging
import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import json
from ui.dialogs.themed_file_dialog import ThemedFileDialog
from ui.dialogs.themed_message_box import ThemedMessageBox
from ui.sizes import size_control
from ui.widgets.modern_dropdown import ModernComboBox
from utils.helpers import create_checkbox_setting
from utils.kuroko import (
    StealthPoolManager,
    WireguardProfileManager,
    get_endpoint_scores,
    measure_stealth_proxy_speed,
    test_stealth_proxy_connection,
)
from utils.main_thread import post_to_main_thread
from utils.task_runner import TaskRunner

logger = logging.getLogger(__name__)


def _safe_string_list(settings, key: str, default: Optional[list] = None) -> list[str]:
    raw = settings.value(key, default or [])
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        if raw.startswith("[") and raw.endswith("]"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except Exception as e:
                logger.debug("Failed to parse settings string list: %s", e)
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _scrolled(page: QWidget) -> QScrollArea:
    """Wrap a settings page in a transparent scroll area."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
    area.setWidget(page)
    return area


def create_stealth_subtab(dialog) -> QWidget:
    """Create the Stealth settings subtab inside Downloads."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 8, 0, 0)
    layout.setSpacing(12)

    card, card_layout = dialog._create_card_frame("Stealth Settings (beta)")
    card_layout.setSpacing(10)

    # ── Option 1: Master Route Depot Downloads Toggle ──
    dialog.enable_stealth_proxy_checkbox = create_checkbox_setting(
        "Route Depot Downloads Through Proxy / VPN",
        "enable_stealth_proxy",
        False,
        dialog,
        "Routes DepotDownloader through an encrypted tunnel or proxy. Stops downloads immediately if disconnected.",
    )
    card_layout.addWidget(dialog.enable_stealth_proxy_checkbox)

    # ── Option 2: Multi-Endpoint Rotation & Auto-Failover Toggle ──
    dialog.stealth_rotation_checkbox = create_checkbox_setting(
        "Enable Multi-Endpoint Rotation & Auto-Failover",
        "stealth_rotation_enabled",
        False,
        dialog,
        "Pool multiple endpoints (WARP, WireGuard profiles, custom proxies) with automatic dead-peer failover and speed benchmarking.",
    )
    card_layout.addWidget(dialog.stealth_rotation_checkbox)

    LABEL_WIDTH = 90
    CONTROL_TOTAL_WIDTH = 380

    def _trigger_visor_refresh():
        try:
            if hasattr(dialog, "parent") and dialog.parent() and hasattr(dialog.parent(), "refresh_system_status"):
                dialog.parent().refresh_system_status()
        except Exception as e:
            logger.debug(f"[StealthTab] Error refreshing visor: {e}")

    # Listen to toggle changes to refresh bottom visor
    dialog.enable_stealth_proxy_checkbox.toggled.connect(lambda _c: _trigger_visor_refresh())
    dialog.stealth_rotation_checkbox.toggled.connect(lambda _c: _trigger_visor_refresh())

    # Task runner for non-blocking network tests
    if not hasattr(dialog, "_stealth_task_runner") or dialog._stealth_task_runner is None:
        dialog._stealth_task_runner = TaskRunner(dialog)

    # =========================================================================
    # SINGLE MODE CONTAINER (Visible when Rotation is OFF)
    # =========================================================================
    single_container = QWidget()
    single_layout = QVBoxLayout(single_container)
    single_layout.setContentsMargins(0, 4, 0, 0)
    single_layout.setSpacing(10)

    # Single Mode: Proxy Mode Dropdown
    mode_row = QHBoxLayout()
    mode_row.setContentsMargins(2, 2, 2, 2)
    mode_row.setSpacing(10)

    mode_lbl = QLabel("Proxy Mode:")
    mode_lbl.setFixedWidth(LABEL_WIDTH)
    mode_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    mode_lbl.setToolTip("Select how to establish the stealth proxy endpoint.")
    mode_row.addWidget(mode_lbl)

    dialog.stealth_proxy_mode_combo = ModernComboBox()
    dialog.stealth_proxy_mode_combo.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.stealth_proxy_mode_combo.addItem("One-Click Cloudflare WARP (Automatic Userspace VPN)", "warp")
    dialog.stealth_proxy_mode_combo.addItem("WireGuard Configuration (.conf)", "wireguard")
    dialog.stealth_proxy_mode_combo.addItem("Custom SOCKS5 / HTTP Proxy", "custom")
    size_control(dialog.stealth_proxy_mode_combo, width=CONTROL_TOTAL_WIDTH)

    saved_mode = dialog.settings.value("stealth_proxy_mode", "warp", type=str).lower()
    mode_idx = dialog.stealth_proxy_mode_combo.findData(saved_mode)
    if mode_idx >= 0:
        dialog.stealth_proxy_mode_combo.setCurrentIndex(mode_idx)
    else:
        dialog.stealth_proxy_mode_combo.setCurrentIndex(0)

    mode_row.addWidget(dialog.stealth_proxy_mode_combo)
    mode_row.addStretch()
    single_layout.addLayout(mode_row)

    # Dynamic Panel 1: WARP Minimal Description
    warp_widget = QWidget()
    warp_layout = QHBoxLayout(warp_widget)
    warp_layout.setContentsMargins(2, 0, 2, 2)
    warp_layout.setSpacing(10)
    warp_spacer = QLabel("")
    warp_spacer.setFixedWidth(LABEL_WIDTH)
    warp_layout.addWidget(warp_spacer)
    warp_lbl = QLabel("Ephemeral userspace WARP tunnel. Zero accounts or setup required.")
    warp_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt;")
    warp_lbl.setWordWrap(True)
    warp_layout.addWidget(warp_lbl, 1)
    single_layout.addWidget(warp_widget)

    # Dynamic Panel 2: WireGuard (.conf) Selection
    wg_widget = QWidget()
    wg_layout = QHBoxLayout(wg_widget)
    wg_layout.setContentsMargins(2, 0, 2, 2)
    wg_layout.setSpacing(10)

    wg_lbl = QLabel("Config File:")
    wg_lbl.setFixedWidth(LABEL_WIDTH)
    wg_lbl.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500;")
    wg_layout.addWidget(wg_lbl)

    dialog.stealth_proxy_wg_edit = QLineEdit()
    dialog.stealth_proxy_wg_edit.setPlaceholderText("/path/to/wireguard.conf")
    wg_saved_path = dialog.settings.value("stealth_proxy_wg_path", "", type=str)
    dialog.stealth_proxy_wg_edit.setText(wg_saved_path)
    if wg_saved_path:
        dialog.stealth_proxy_wg_edit.setToolTip(wg_saved_path)
    dialog.stealth_proxy_wg_edit.setStyleSheet(f"""
        QLineEdit {{
            background-color: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 6px;
            padding: 5px 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
        }}
        QLineEdit:focus {{
            border-color: {dialog.accent_color};
            background-color: rgba(255, 255, 255, 0.1);
        }}
    """)
    dialog.stealth_proxy_wg_edit.textChanged.connect(
        lambda text: (
            dialog.settings.setValue("stealth_proxy_wg_path", text.strip()),
            dialog.stealth_proxy_wg_edit.setToolTip(text.strip()),
        )
    )
    dialog.stealth_proxy_wg_edit.setFixedWidth(290)
    wg_layout.addWidget(dialog.stealth_proxy_wg_edit)

    wg_browse_btn = QPushButton("Browse...")
    wg_browse_btn.setFixedWidth(82)
    wg_browse_btn.setFixedHeight(30)
    wg_browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    wg_browse_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 5px 12px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {dialog.accent_color};
        }}
    """)

    def _browse_single_wg_conf():
        cur = dialog.stealth_proxy_wg_edit.text().strip()
        start_dir = os.path.dirname(cur) if cur and os.path.isfile(cur) else os.path.expanduser("~")
        path, _ = ThemedFileDialog.getOpenFileName(
            dialog,
            "Select WireGuard Configuration",
            start_dir,
            "WireGuard Config (*.conf);;All Files (*)",
        )
        if path:
            # Import into central storage for security & inode consistency
            imported = WireguardProfileManager.import_profile(path)
            active_path = imported["path"] if imported else path
            dialog.stealth_proxy_wg_edit.setText(active_path)
            dialog.stealth_proxy_wg_edit.setToolTip(active_path)
            dialog.settings.setValue("stealth_proxy_wg_path", active_path)
            if imported:
                dialog.settings.setValue("stealth_active_wg_profile", imported["filename"])

    wg_browse_btn.clicked.connect(_browse_single_wg_conf)
    wg_layout.addWidget(wg_browse_btn)
    wg_layout.addStretch()
    single_layout.addWidget(wg_widget)

    # Dynamic Panel 3: Custom SOCKS5/HTTP URL
    custom_widget = QWidget()
    custom_layout = QHBoxLayout(custom_widget)
    custom_layout.setContentsMargins(2, 0, 2, 2)
    custom_layout.setSpacing(10)

    custom_lbl = QLabel("Proxy URL:")
    custom_lbl.setFixedWidth(LABEL_WIDTH)
    custom_lbl.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500;")
    custom_layout.addWidget(custom_lbl)

    dialog.stealth_proxy_custom_edit = QLineEdit()
    dialog.stealth_proxy_custom_edit.setPlaceholderText("socks5://127.0.0.1:1080 (or socks5://user:pass@host:port)")
    custom_saved_url = dialog.settings.value("stealth_proxy_custom_url", "socks5://127.0.0.1:1080", type=str)
    dialog.stealth_proxy_custom_edit.setText(custom_saved_url)
    if custom_saved_url:
        dialog.stealth_proxy_custom_edit.setToolTip(custom_saved_url)
    dialog.stealth_proxy_custom_edit.setStyleSheet(f"""
        QLineEdit {{
            background-color: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 6px;
            padding: 5px 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
        }}
        QLineEdit:focus {{
            border-color: {dialog.accent_color};
            background-color: rgba(255, 255, 255, 0.1);
        }}
    """)
    dialog.stealth_proxy_custom_edit.textChanged.connect(
        lambda text: (
            dialog.settings.setValue("stealth_proxy_custom_url", text.strip()),
            dialog.stealth_proxy_custom_edit.setToolTip(text.strip()),
        )
    )
    dialog.stealth_proxy_custom_edit.setFixedWidth(CONTROL_TOTAL_WIDTH)
    custom_layout.addWidget(dialog.stealth_proxy_custom_edit)
    custom_layout.addStretch()
    single_layout.addWidget(custom_widget)

    # Sync single mode panel visibility
    def _sync_single_mode_panels():
        sel_mode = dialog.stealth_proxy_mode_combo.currentData() or "warp"
        dialog.settings.setValue("stealth_proxy_mode", sel_mode)
        warp_widget.setVisible(sel_mode == "warp")
        wg_widget.setVisible(sel_mode == "wireguard")
        custom_widget.setVisible(sel_mode == "custom")
        _trigger_visor_refresh()

    dialog.stealth_proxy_mode_combo.currentIndexChanged.connect(lambda _i: _sync_single_mode_panels())
    _sync_single_mode_panels()

    # Single Mode: Action Buttons Row (aligned with controls via spacer)
    single_action_row = QHBoxLayout()
    single_action_row.setContentsMargins(2, 4, 2, 2)
    single_action_row.setSpacing(10)

    single_spacer = QLabel("")
    single_spacer.setFixedWidth(LABEL_WIDTH)
    single_action_row.addWidget(single_spacer)

    proxy_test_btn = QPushButton("Test Connection")
    proxy_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    proxy_test_btn.setFixedWidth(130)
    proxy_test_btn.setFixedHeight(30)
    proxy_test_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 5px 14px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {dialog.accent_color};
        }}
        QPushButton:disabled {{
            background-color: rgba(255, 255, 255, 0.04);
            color: rgba(255, 255, 255, 0.4);
            border-color: rgba(255, 255, 255, 0.08);
        }}
    """)
    single_action_row.addWidget(proxy_test_btn)

    proxy_speed_btn = QPushButton("Speed Test")
    proxy_speed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    proxy_speed_btn.setFixedWidth(110)
    proxy_speed_btn.setFixedHeight(30)
    proxy_speed_btn.setToolTip("Lightweight 5 MB burst throughput & RTT ping benchmark")
    proxy_speed_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 5px 14px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {dialog.accent_color};
        }}
        QPushButton:disabled {{
            background-color: rgba(255, 255, 255, 0.04);
            color: rgba(255, 255, 255, 0.4);
            border-color: rgba(255, 255, 255, 0.08);
        }}
    """)
    single_action_row.addWidget(proxy_speed_btn)

    single_status_lbl = QLabel("")
    single_status_lbl.setStyleSheet("font-size: 8.5pt; font-weight: 500;")
    single_status_lbl.setWordWrap(True)
    single_action_row.addWidget(single_status_lbl, 1)
    single_layout.addLayout(single_action_row)

    # Single Mode: Test Connection Button Handler
    def _on_single_test_clicked():
        sel_mode = dialog.stealth_proxy_mode_combo.currentData() or "warp"
        wg_p = dialog.stealth_proxy_wg_edit.text().strip()
        cust_u = dialog.stealth_proxy_custom_edit.text().strip()

        proxy_test_btn.setEnabled(False)
        proxy_speed_btn.setEnabled(False)
        single_status_lbl.setText("Verifying proxy connection...")
        single_status_lbl.setStyleSheet("color: #90CAF9; font-size: 8.5pt;")

        def _on_test_done(result):
            proxy_test_btn.setEnabled(True)
            proxy_speed_btn.setEnabled(True)
            if isinstance(result, tuple) and len(result) == 2:
                ok, msg = result
                if ok:
                    single_status_lbl.setText(f"Connected: {msg}")
                    single_status_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: bold;")
                else:
                    single_status_lbl.setText(f"Failed: {msg}")
                    single_status_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt; font-weight: bold;")
            else:
                single_status_lbl.setText("Verification failed.")
                single_status_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt; font-weight: bold;")

        def _on_test_error(err_tuple):
            proxy_test_btn.setEnabled(True)
            proxy_speed_btn.setEnabled(True)
            err_msg = str(err_tuple[1]) if isinstance(err_tuple, tuple) and len(err_tuple) > 1 else "Unknown error"
            single_status_lbl.setText(f"Error: {err_msg}")
            single_status_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt; font-weight: bold;")

        worker = dialog._stealth_task_runner.run(
            test_stealth_proxy_connection, sel_mode, wg_p, cust_u
        )
        worker.finished.connect(_on_test_done)
        worker.error.connect(_on_test_error)

    proxy_test_btn.clicked.connect(_on_single_test_clicked)

    # Single Mode: Speed Test Button Handler
    def _on_single_speed_clicked():
        sel_mode = dialog.stealth_proxy_mode_combo.currentData() or "warp"
        wg_p = dialog.stealth_proxy_wg_edit.text().strip()
        cust_u = dialog.stealth_proxy_custom_edit.text().strip()

        proxy_test_btn.setEnabled(False)
        proxy_speed_btn.setEnabled(False)
        single_status_lbl.setText("Measuring latency and 5 MB burst throughput...")
        single_status_lbl.setStyleSheet("color: #90CAF9; font-size: 8.5pt;")

        def _on_speed_done(result):
            proxy_test_btn.setEnabled(True)
            proxy_speed_btn.setEnabled(True)
            if isinstance(result, tuple) and len(result) == 4:
                ok, ping_ms, speed_mbps, msg = result
                if ok:
                    single_status_lbl.setText(f"Ping: {ping_ms} ms | Speed: {speed_mbps} Mbps")
                    single_status_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: bold;")
                else:
                    single_status_lbl.setText(f"Speed test failed: {msg}")
                    single_status_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt; font-weight: bold;")
            else:
                single_status_lbl.setText("Speed test failed.")
                single_status_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt; font-weight: bold;")

        def _on_speed_error(err_tuple):
            proxy_test_btn.setEnabled(True)
            proxy_speed_btn.setEnabled(True)
            err_msg = str(err_tuple[1]) if isinstance(err_tuple, tuple) and len(err_tuple) > 1 else "Unknown error"
            single_status_lbl.setText(f"Speed test error: {err_msg}")
            single_status_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt; font-weight: bold;")

        worker = dialog._stealth_task_runner.run(
            measure_stealth_proxy_speed, sel_mode, wg_p, cust_u
        )
        worker.finished.connect(_on_speed_done)
        worker.error.connect(_on_speed_error)

    proxy_speed_btn.clicked.connect(_on_single_speed_clicked)
    card_layout.addWidget(single_container)

    # =========================================================================
    # MULTI-ENDPOINT CANDIDATE POOL CONTAINER (Visible when Rotation is ON)
    # =========================================================================
    pool_container = QWidget()
    pool_layout = QVBoxLayout(pool_container)
    pool_layout.setContentsMargins(0, 6, 0, 0)
    pool_layout.setSpacing(10)

    # Pool Toolbar Row
    pool_toolbar = QHBoxLayout()
    pool_toolbar.setContentsMargins(2, 2, 2, 2)
    pool_toolbar.setSpacing(10)

    pool_title_lbl = QLabel("Candidate Rotation Pool:")
    pool_title_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 600;")
    pool_toolbar.addWidget(pool_title_lbl)
    pool_toolbar.addStretch()

    add_conf_btn = QPushButton("+ Add .conf")
    add_conf_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    add_conf_btn.setFixedHeight(28)
    add_conf_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 5px;
            padding: 4px 10px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {dialog.accent_color};
        }}
    """)
    pool_toolbar.addWidget(add_conf_btn)

    add_folder_btn = QPushButton("+ Import Folder")
    add_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    add_folder_btn.setFixedHeight(28)
    add_folder_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 5px;
            padding: 4px 10px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {dialog.accent_color};
        }}
    """)
    pool_toolbar.addWidget(add_folder_btn)

    benchmark_all_btn = QPushButton("Benchmark All")
    benchmark_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    benchmark_all_btn.setFixedHeight(28)
    benchmark_all_btn.setToolTip("Sequentially measures ping & 5 MB burst throughput across all active candidates")
    benchmark_all_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 5px;
            padding: 4px 12px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {dialog.accent_color};
        }}
        QPushButton:disabled {{
            background-color: rgba(255, 255, 255, 0.04);
            color: rgba(255, 255, 255, 0.4);
            border-color: rgba(255, 255, 255, 0.08);
        }}
    """)
    pool_toolbar.addWidget(benchmark_all_btn)
    pool_layout.addLayout(pool_toolbar)

    # Benchmark summary status label
    benchmark_summary_lbl = QLabel("")
    benchmark_summary_lbl.setStyleSheet("font-size: 8.5pt; font-weight: 500; color: #90CAF9;")
    benchmark_summary_lbl.setVisible(False)
    pool_layout.addWidget(benchmark_summary_lbl)

    # Container for dynamic pool candidate rows
    pool_items_frame = QFrame()
    pool_items_frame.setStyleSheet("""
        QFrame {
            background-color: rgba(0, 0, 0, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            padding: 6px;
        }
    """)
    pool_items_layout = QVBoxLayout(pool_items_frame)
    pool_items_layout.setContentsMargins(6, 6, 6, 6)
    pool_items_layout.setSpacing(6)
    pool_layout.addWidget(pool_items_frame)

    # In-memory candidate row registry: cand_id -> {status_lbl, test_btn, speed_btn}
    candidate_row_widgets = {}

    def _refresh_pool_ui():
        # Clear existing items
        candidate_row_widgets.clear()
        while pool_items_layout.count() > 0:
            child = pool_items_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        cached_scores = get_endpoint_scores(dialog.settings)

        # Row 1: Cloudflare WARP
        warp_row_widget = QWidget()
        warp_row = QHBoxLayout(warp_row_widget)
        warp_row.setContentsMargins(4, 2, 4, 2)
        warp_row.setSpacing(8)

        warp_cb = create_checkbox_setting(
            "Cloudflare WARP (Automatic Userspace Tunnel)",
            "stealth_pool_warp",
            True,
            dialog,
        )
        warp_cb.toggled.connect(lambda _c: _trigger_visor_refresh())
        warp_row.addWidget(warp_cb, 1)

        w_test_btn = QPushButton("Test")
        w_test_btn.setFixedSize(54, 26)
        w_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        w_test_btn.setStyleSheet("QPushButton { font-size: 8pt; padding: 2px 6px; }")
        warp_row.addWidget(w_test_btn)

        w_speed_btn = QPushButton("Speed Test")
        w_speed_btn.setFixedSize(76, 26)
        w_speed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        w_speed_btn.setStyleSheet("QPushButton { font-size: 8pt; padding: 2px 6px; }")
        warp_row.addWidget(w_speed_btn)

        w_status_lbl = QLabel("Ready")
        w_status_lbl.setFixedWidth(190)
        w_status_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8pt;")
        warp_sc = cached_scores.get("warp")
        if warp_sc and warp_sc.get("ok"):
            w_status_lbl.setText(f"[OK] {warp_sc.get('ping_ms', 0)}ms | {warp_sc.get('speed_mbps', 0.0)} Mbps")
            w_status_lbl.setStyleSheet("color: #81C784; font-size: 8pt; font-weight: bold;")
        warp_row.addWidget(w_status_lbl)

        candidate_row_widgets["warp"] = {
            "status_lbl": w_status_lbl,
            "test_btn": w_test_btn,
            "speed_btn": w_speed_btn,
        }

        def _on_test_warp():
            w_test_btn.setEnabled(False)
            w_speed_btn.setEnabled(False)
            w_status_lbl.setText("Testing WARP...")
            w_status_lbl.setStyleSheet("color: #90CAF9; font-size: 8pt;")

            def _done(res):
                w_test_btn.setEnabled(True)
                w_speed_btn.setEnabled(True)
                if isinstance(res, tuple) and res[0]:
                    w_status_lbl.setText(f"[OK] {res[1]}")
                    w_status_lbl.setStyleSheet("color: #81C784; font-size: 8pt; font-weight: bold;")
                else:
                    err = res[1] if isinstance(res, tuple) else "Failed"
                    w_status_lbl.setText(f"[Error] {err[:28]}")
                    w_status_lbl.setStyleSheet("color: #E57373; font-size: 8pt;")

            worker = dialog._stealth_task_runner.run(test_stealth_proxy_connection, "warp")
            worker.finished.connect(_done)
            worker.error.connect(lambda _e: (w_test_btn.setEnabled(True), w_speed_btn.setEnabled(True), w_status_lbl.setText("[Error]")))

        def _on_speed_warp():
            w_test_btn.setEnabled(False)
            w_speed_btn.setEnabled(False)
            w_status_lbl.setText("Benchmarking...")
            w_status_lbl.setStyleSheet("color: #90CAF9; font-size: 8pt;")

            def _done(res):
                w_test_btn.setEnabled(True)
                w_speed_btn.setEnabled(True)
                if isinstance(res, tuple) and res[0]:
                    w_status_lbl.setText(f"[OK] {res[1]}ms | {res[2]} Mbps")
                    w_status_lbl.setStyleSheet("color: #81C784; font-size: 8pt; font-weight: bold;")
                else:
                    err = res[3] if isinstance(res, tuple) and len(res) > 3 else "Failed"
                    w_status_lbl.setText(f"[Error] {err[:28]}")
                    w_status_lbl.setStyleSheet("color: #E57373; font-size: 8pt;")

            worker = dialog._stealth_task_runner.run(measure_stealth_proxy_speed, "warp")
            worker.finished.connect(_done)
            worker.error.connect(lambda _e: (w_test_btn.setEnabled(True), w_speed_btn.setEnabled(True), w_status_lbl.setText("[Error]")))

        w_test_btn.clicked.connect(_on_test_warp)
        w_speed_btn.clicked.connect(_on_speed_warp)
        pool_items_layout.addWidget(warp_row_widget)

        # Rows 2..N: Imported WireGuard profiles
        profiles = WireguardProfileManager.list_profiles()
        saved_wg_pool = _safe_string_list(dialog.settings, "stealth_pool_wg_profiles")
        active_wg_set = set(str(x) for x in saved_wg_pool)

        for p_info in profiles:
            fname = p_info["filename"]
            fpath = p_info["path"]
            cand_id = f"wg:{fname}"

            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(4, 2, 4, 2)
            row.setSpacing(8)

            from PyQt6.QtWidgets import QCheckBox
            cb = QCheckBox(f"WireGuard: {p_info['name']}.conf")
            cb.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
            cb.setChecked(fname in active_wg_set)

            def _make_wg_cb_handler(_fn, _checkbox):
                def _handler(checked):
                    cur_list = _safe_string_list(dialog.settings, "stealth_pool_wg_profiles")
                    cur_set = set(str(x) for x in cur_list)
                    if checked:
                        cur_set.add(_fn)
                    else:
                        cur_set.discard(_fn)
                    dialog.settings.setValue("stealth_pool_wg_profiles", sorted(list(cur_set)))
                    _trigger_visor_refresh()
                return _handler

            cb.toggled.connect(_make_wg_cb_handler(fname, cb))
            row.addWidget(cb, 1)

            t_btn = QPushButton("Test")
            t_btn.setFixedSize(54, 26)
            t_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            t_btn.setStyleSheet("QPushButton { font-size: 8pt; padding: 2px 6px; }")
            row.addWidget(t_btn)

            s_btn = QPushButton("Speed Test")
            s_btn.setFixedSize(76, 26)
            s_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            s_btn.setStyleSheet("QPushButton { font-size: 8pt; padding: 2px 6px; }")
            row.addWidget(s_btn)

            s_lbl = QLabel("Ready")
            s_lbl.setFixedWidth(160)
            s_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8pt;")
            wg_sc = cached_scores.get(cand_id)
            if wg_sc and wg_sc.get("ok"):
                s_lbl.setText(f"[OK] {wg_sc.get('ping_ms', 0)}ms | {wg_sc.get('speed_mbps', 0.0)} Mbps")
                s_lbl.setStyleSheet("color: #81C784; font-size: 8pt; font-weight: bold;")
            row.addWidget(s_lbl)

            del_btn = QPushButton("X")
            del_btn.setFixedSize(26, 26)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setToolTip("Delete profile from storage")
            del_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(239, 68, 68, 0.15);
                    border: 1px solid rgba(239, 68, 68, 0.3);
                    border-radius: 4px;
                    color: #EF4444;
                    font-size: 9pt;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: rgba(239, 68, 68, 0.35);
                    border-color: #EF4444;
                }
            """)
            row.addWidget(del_btn)

            candidate_row_widgets[cand_id] = {
                "status_lbl": s_lbl,
                "test_btn": t_btn,
                "speed_btn": s_btn,
            }

            def _make_wg_test_handler(_path, _btn_t, _btn_s, _lbl):
                def _test():
                    _btn_t.setEnabled(False)
                    _btn_s.setEnabled(False)
                    _lbl.setText("Testing...")
                    _lbl.setStyleSheet("color: #90CAF9; font-size: 8pt;")

                    def _done(res):
                        _btn_t.setEnabled(True)
                        _btn_s.setEnabled(True)
                        if isinstance(res, tuple) and res[0]:
                            _lbl.setText(f"[OK] {res[1]}")
                            _lbl.setStyleSheet("color: #81C784; font-size: 8pt; font-weight: bold;")
                        else:
                            err = res[1] if isinstance(res, tuple) else "Failed"
                            _lbl.setText(f"[Error] {err[:24]}")
                            _lbl.setStyleSheet("color: #E57373; font-size: 8pt;")

                    worker = dialog._stealth_task_runner.run(test_stealth_proxy_connection, "wireguard", _path)
                    worker.finished.connect(_done)
                    worker.error.connect(lambda _e: (_btn_t.setEnabled(True), _btn_s.setEnabled(True), _lbl.setText("[Error]")))
                return _test

            def _make_wg_speed_handler(_path, _btn_t, _btn_s, _lbl):
                def _speed():
                    _btn_t.setEnabled(False)
                    _btn_s.setEnabled(False)
                    _lbl.setText("Benchmarking...")
                    _lbl.setStyleSheet("color: #90CAF9; font-size: 8pt;")

                    def _done(res):
                        _btn_t.setEnabled(True)
                        _btn_s.setEnabled(True)
                        if isinstance(res, tuple) and res[0]:
                            _lbl.setText(f"[OK] {res[1]}ms | {res[2]} Mbps")
                            _lbl.setStyleSheet("color: #81C784; font-size: 8pt; font-weight: bold;")
                        else:
                            err = res[3] if isinstance(res, tuple) and len(res) > 3 else "Failed"
                            _lbl.setText(f"[Error] {err[:24]}")
                            _lbl.setStyleSheet("color: #E57373; font-size: 8pt;")

                    worker = dialog._stealth_task_runner.run(measure_stealth_proxy_speed, "wireguard", _path)
                    worker.finished.connect(_done)
                    worker.error.connect(lambda _e: (_btn_t.setEnabled(True), _btn_s.setEnabled(True), _lbl.setText("[Error]")))
                return _speed

            def _make_delete_handler(_fn, _name):
                def _delete():
                    reply = ThemedMessageBox.question(
                        dialog,
                        "Delete Profile",
                        f"Are you sure you want to permanently delete WireGuard profile '{_name}'?",
                        ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
                        ThemedMessageBox.StandardButton.No,
                    )
                    if reply == ThemedMessageBox.StandardButton.Yes:
                        WireguardProfileManager.delete_profile(_fn)
                        cur_list = _safe_string_list(dialog.settings, "stealth_pool_wg_profiles")
                        cur_set = set(str(x) for x in cur_list)
                        cur_set.discard(_fn)
                        dialog.settings.setValue("stealth_pool_wg_profiles", sorted(list(cur_set)))
                        _refresh_pool_ui()
                        _trigger_visor_refresh()
                return _delete

            t_btn.clicked.connect(_make_wg_test_handler(fpath, t_btn, s_btn, s_lbl))
            s_btn.clicked.connect(_make_wg_speed_handler(fpath, t_btn, s_btn, s_lbl))
            del_btn.clicked.connect(_make_delete_handler(fname, p_info["name"]))
            pool_items_layout.addWidget(row_widget)

        # Row N+1: Custom Proxy
        custom_row_widget = QWidget()
        custom_row = QHBoxLayout(custom_row_widget)
        custom_row.setContentsMargins(4, 2, 4, 2)
        custom_row.setSpacing(8)

        custom_cb = create_checkbox_setting(
            "Custom Proxy:",
            "stealth_pool_custom",
            False,
            dialog,
        )
        custom_cb.toggled.connect(lambda _c: _trigger_visor_refresh())
        custom_row.addWidget(custom_cb)

        pool_custom_edit = QLineEdit()
        pool_custom_edit.setPlaceholderText("socks5://127.0.0.1:1080")
        p_c_url = dialog.settings.value("stealth_proxy_custom_url", "socks5://127.0.0.1:1080", type=str)
        pool_custom_edit.setText(p_c_url)
        pool_custom_edit.setFixedWidth(210)
        pool_custom_edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 5px;
                padding: 3px 6px;
                color: #FFFFFF;
                font-size: 8pt;
            }}
            QLineEdit:focus {{
                border-color: {dialog.accent_color};
            }}
        """)
        pool_custom_edit.textChanged.connect(
            lambda text: (
                dialog.settings.setValue("stealth_proxy_custom_url", text.strip()),
                dialog.stealth_proxy_custom_edit.setText(text.strip()),
            )
        )
        custom_row.addWidget(pool_custom_edit)

        c_test_btn = QPushButton("Test")
        c_test_btn.setFixedSize(54, 26)
        c_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        c_test_btn.setStyleSheet("QPushButton { font-size: 8pt; padding: 2px 6px; }")
        custom_row.addWidget(c_test_btn)

        c_speed_btn = QPushButton("Speed Test")
        c_speed_btn.setFixedSize(76, 26)
        c_speed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        c_speed_btn.setStyleSheet("QPushButton { font-size: 8pt; padding: 2px 6px; }")
        custom_row.addWidget(c_speed_btn)

        c_status_lbl = QLabel("Ready")
        c_status_lbl.setFixedWidth(190)
        c_status_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8pt;")
        c_sc = cached_scores.get("custom")
        if c_sc and c_sc.get("ok"):
            c_status_lbl.setText(f"[OK] {c_sc.get('ping_ms', 0)}ms | {c_sc.get('speed_mbps', 0.0)} Mbps")
            c_status_lbl.setStyleSheet("color: #81C784; font-size: 8pt; font-weight: bold;")
        custom_row.addWidget(c_status_lbl)

        candidate_row_widgets["custom"] = {
            "status_lbl": c_status_lbl,
            "test_btn": c_test_btn,
            "speed_btn": c_speed_btn,
        }

        def _on_test_custom():
            c_test_btn.setEnabled(False)
            c_speed_btn.setEnabled(False)
            c_status_lbl.setText("Testing...")
            c_status_lbl.setStyleSheet("color: #90CAF9; font-size: 8pt;")

            def _done(res):
                c_test_btn.setEnabled(True)
                c_speed_btn.setEnabled(True)
                if isinstance(res, tuple) and res[0]:
                    c_status_lbl.setText(f"[OK] {res[1]}")
                    c_status_lbl.setStyleSheet("color: #81C784; font-size: 8pt; font-weight: bold;")
                else:
                    err = res[1] if isinstance(res, tuple) else "Failed"
                    c_status_lbl.setText(f"[Error] {err[:28]}")
                    c_status_lbl.setStyleSheet("color: #E57373; font-size: 8pt;")

            worker = dialog._stealth_task_runner.run(test_stealth_proxy_connection, "custom", "", pool_custom_edit.text().strip())
            worker.finished.connect(_done)
            worker.error.connect(lambda _e: (c_test_btn.setEnabled(True), c_speed_btn.setEnabled(True), c_status_lbl.setText("[Error]")))

        def _on_speed_custom():
            c_test_btn.setEnabled(False)
            c_speed_btn.setEnabled(False)
            c_status_lbl.setText("Benchmarking...")
            c_status_lbl.setStyleSheet("color: #90CAF9; font-size: 8pt;")

            def _done(res):
                c_test_btn.setEnabled(True)
                c_speed_btn.setEnabled(True)
                if isinstance(res, tuple) and res[0]:
                    c_status_lbl.setText(f"[OK] {res[1]}ms | {res[2]} Mbps")
                    c_status_lbl.setStyleSheet("color: #81C784; font-size: 8pt; font-weight: bold;")
                else:
                    err = res[3] if isinstance(res, tuple) and len(res) > 3 else "Failed"
                    c_status_lbl.setText(f"[Error] {err[:28]}")
                    c_status_lbl.setStyleSheet("color: #E57373; font-size: 8pt;")

            worker = dialog._stealth_task_runner.run(measure_stealth_proxy_speed, "custom", "", pool_custom_edit.text().strip())
            worker.finished.connect(_done)
            worker.error.connect(lambda _e: (c_test_btn.setEnabled(True), c_speed_btn.setEnabled(True), c_status_lbl.setText("[Error]")))

        c_test_btn.clicked.connect(_on_test_custom)
        c_speed_btn.clicked.connect(_on_speed_custom)
        pool_items_layout.addWidget(custom_row_widget)

    # Initial pool UI population
    _refresh_pool_ui()

    # Toolbar Handler: Add .conf
    def _on_add_conf():
        path, _ = ThemedFileDialog.getOpenFileName(
            dialog,
            "Import WireGuard Configuration",
            os.path.expanduser("~"),
            "WireGuard Config (*.conf);;All Files (*)",
        )
        if path:
            imported = WireguardProfileManager.import_profile(path)
            if imported:
                cur_list = _safe_string_list(dialog.settings, "stealth_pool_wg_profiles")
                cur_set = set(str(x) for x in cur_list)
                cur_set.add(imported["filename"])
                dialog.settings.setValue("stealth_pool_wg_profiles", sorted(list(cur_set)))
                _refresh_pool_ui()
                _trigger_visor_refresh()

    add_conf_btn.clicked.connect(_on_add_conf)

    # Toolbar Handler: Import Folder
    def _on_import_folder():
        folder = ThemedFileDialog.getExistingDirectory(
            dialog,
            "Select Folder with WireGuard Configurations",
            os.path.expanduser("~"),
        )
        if folder and os.path.isdir(folder):
            imported_count = 0
            cur_list = _safe_string_list(dialog.settings, "stealth_pool_wg_profiles")
            cur_set = set(str(x) for x in cur_list)
            for item in sorted(Path(folder).glob("*.conf")):
                if item.is_file():
                    res = WireguardProfileManager.import_profile(str(item.resolve()))
                    if res:
                        cur_set.add(res["filename"])
                        imported_count += 1
            if imported_count > 0:
                dialog.settings.setValue("stealth_pool_wg_profiles", sorted(list(cur_set)))
                _refresh_pool_ui()
                _trigger_visor_refresh()
                ThemedMessageBox.information(
                    dialog,
                    "Profiles Imported",
                    f"Successfully imported {imported_count} WireGuard configuration(s) into your rotation pool.",
                )

    add_folder_btn.clicked.connect(_on_import_folder)

    # Toolbar Handler: Benchmark All (Sequential)
    def _on_benchmark_all():
        benchmark_all_btn.setEnabled(False)
        benchmark_summary_lbl.setVisible(True)
        benchmark_summary_lbl.setText("Benchmarking candidates sequentially (5 MB burst)...")
        benchmark_summary_lbl.setStyleSheet("color: #90CAF9; font-size: 8.5pt;")

        for cand_id, widgets in candidate_row_widgets.items():
            widgets["test_btn"].setEnabled(False)
            widgets["speed_btn"].setEnabled(False)
            widgets["status_lbl"].setText("Waiting...")
            widgets["status_lbl"].setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 8pt;")

        def _progress_cb(cand_id, ok, ping_ms, speed_mbps, msg):
            def _update_row():
                w = candidate_row_widgets.get(cand_id)
                if w:
                    if ok:
                        w["status_lbl"].setText(f"[OK] {ping_ms}ms | {speed_mbps} Mbps")
                        w["status_lbl"].setStyleSheet("color: #81C784; font-size: 8pt; font-weight: bold;")
                    else:
                        w["status_lbl"].setText(f"[Error] {msg[:24]}")
                        w["status_lbl"].setStyleSheet("color: #E57373; font-size: 8pt;")
            post_to_main_thread(_update_row)

        def _on_bench_complete(results):
            benchmark_all_btn.setEnabled(True)
            for w in candidate_row_widgets.values():
                w["test_btn"].setEnabled(True)
                w["speed_btn"].setEnabled(True)

            if isinstance(results, list) and results:
                fastest = results[0]
                if fastest.get("ok"):
                    f_name = fastest.get("name", "Fastest")
                    f_speed = fastest.get("speed_mbps", 0.0)
                    f_ping = fastest.get("ping_ms", 0)
                    benchmark_summary_lbl.setText(f"Benchmark complete. Fastest: {f_name} ({f_speed} Mbps, {f_ping}ms)")
                    benchmark_summary_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: bold;")
                else:
                    benchmark_summary_lbl.setText("All benchmarked endpoints failed connectivity verification.")
                    benchmark_summary_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt; font-weight: bold;")
            else:
                benchmark_summary_lbl.setText("Benchmark finished.")

        def _on_bench_error(err_tuple):
            benchmark_all_btn.setEnabled(True)
            for w in candidate_row_widgets.values():
                w["test_btn"].setEnabled(True)
                w["speed_btn"].setEnabled(True)
            err_msg = str(err_tuple[1]) if isinstance(err_tuple, tuple) and len(err_tuple) > 1 else "Error"
            benchmark_summary_lbl.setText(f"Benchmark error: {err_msg}")
            benchmark_summary_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt; font-weight: bold;")

        worker = dialog._stealth_task_runner.run(
            StealthPoolManager.benchmark_all_candidates, dialog.settings, _progress_cb
        )
        worker.finished.connect(_on_bench_complete)
        worker.error.connect(_on_bench_error)

    benchmark_all_btn.clicked.connect(_on_benchmark_all)
    card_layout.addWidget(pool_container)

    # =========================================================================
    # MASTER VIEW SWITCHER (Single Mode vs Candidate Pool)
    # =========================================================================
    def _sync_master_view():
        rotation_on = dialog.settings.value("stealth_rotation_enabled", False, type=bool)
        single_container.setVisible(not rotation_on)
        pool_container.setVisible(rotation_on)

    dialog.stealth_rotation_checkbox.toggled.connect(lambda _c: _sync_master_view())
    _sync_master_view()

    layout.addWidget(card)
    layout.addStretch()
    return _scrolled(widget)


# Backward-compatibility alias
create_stealth_tab = create_stealth_subtab
