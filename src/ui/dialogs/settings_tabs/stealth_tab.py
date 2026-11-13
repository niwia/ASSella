"""Settings subtab for Stealth proxy and VPN routing."""

import logging
import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.themed_file_dialog import ThemedFileDialog
from ui.sizes import size_control
from ui.widgets.modern_dropdown import ModernComboBox
from utils.helpers import create_checkbox_setting
from utils.kuroko import test_stealth_proxy_connection
from utils.task_runner import TaskRunner

logger = logging.getLogger(__name__)


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

    # ── Option 1: Route Depot Downloads Toggle ──
    dialog.enable_stealth_proxy_checkbox = create_checkbox_setting(
        "Route Depot Downloads Through Proxy / VPN",
        "enable_stealth_proxy",
        False,
        dialog,
        "Routes DepotDownloader through an encrypted tunnel or proxy. Stops downloads immediately if disconnected.",
    )
    card_layout.addWidget(dialog.enable_stealth_proxy_checkbox)

    # ── Option 2: Proxy Mode Selection ──
    LABEL_WIDTH = 90
    CONTROL_TOTAL_WIDTH = 380

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
    card_layout.addLayout(mode_row)

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
    card_layout.addWidget(warp_widget)

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
    dialog.stealth_proxy_wg_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
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

    def _browse_wg_conf():
        cur = dialog.stealth_proxy_wg_edit.text().strip()
        start_dir = os.path.dirname(cur) if cur and os.path.isfile(cur) else os.path.expanduser("~")
        path, _ = ThemedFileDialog.getOpenFileName(
            dialog,
            "Select WireGuard Configuration",
            start_dir,
            "WireGuard Config (*.conf);;All Files (*)",
        )
        if path:
            dialog.stealth_proxy_wg_edit.setText(path)
            dialog.stealth_proxy_wg_edit.setToolTip(path)
            dialog.settings.setValue("stealth_proxy_wg_path", path)

    wg_browse_btn.clicked.connect(_browse_wg_conf)
    wg_layout.addWidget(wg_browse_btn)
    wg_layout.addStretch()
    card_layout.addWidget(wg_widget)

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
    dialog.stealth_proxy_custom_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
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
    card_layout.addWidget(custom_widget)

    # Mode switching sync
    def _sync_proxy_mode_ui():
        sel_mode = dialog.stealth_proxy_mode_combo.currentData() or "warp"
        dialog.settings.setValue("stealth_proxy_mode", sel_mode)
        warp_widget.setVisible(sel_mode == "warp")
        wg_widget.setVisible(sel_mode == "wireguard")
        custom_widget.setVisible(sel_mode == "custom")

    dialog.stealth_proxy_mode_combo.currentIndexChanged.connect(lambda _i: _sync_proxy_mode_ui())
    _sync_proxy_mode_ui()

    # Test connection row (aligned with controls via spacer)
    test_row = QHBoxLayout()
    test_row.setContentsMargins(2, 4, 2, 2)
    test_row.setSpacing(10)

    test_spacer = QLabel("")
    test_spacer.setFixedWidth(LABEL_WIDTH)
    test_row.addWidget(test_spacer)

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
    test_row.addWidget(proxy_test_btn)

    proxy_test_status_lbl = QLabel("")
    proxy_test_status_lbl.setStyleSheet("font-size: 8.5pt; font-weight: 500;")
    proxy_test_status_lbl.setWordWrap(True)
    test_row.addWidget(proxy_test_status_lbl, 1)
    card_layout.addLayout(test_row)

    # Test connection click handler
    def _on_test_proxy_clicked():
        sel_mode = dialog.stealth_proxy_mode_combo.currentData() or "warp"
        wg_p = dialog.stealth_proxy_wg_edit.text().strip()
        cust_u = dialog.stealth_proxy_custom_edit.text().strip()

        proxy_test_btn.setEnabled(False)
        proxy_test_status_lbl.setText("Verifying proxy connection...")
        proxy_test_status_lbl.setStyleSheet("color: #90CAF9; font-size: 8.5pt;")

        if not hasattr(dialog, "_proxy_test_runner") or dialog._proxy_test_runner is None:
            dialog._proxy_test_runner = TaskRunner(dialog)

        def _on_proxy_test_done(result):
            proxy_test_btn.setEnabled(True)
            if isinstance(result, tuple) and len(result) == 2:
                ok, msg = result
                if ok:
                    proxy_test_status_lbl.setText(f"Connected: {msg}")
                    proxy_test_status_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: bold;")
                else:
                    proxy_test_status_lbl.setText(f"Failed: {msg}")
                    proxy_test_status_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt; font-weight: bold;")
            else:
                proxy_test_status_lbl.setText("Verification failed.")
                proxy_test_status_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt; font-weight: bold;")

        def _on_proxy_test_error(err_tuple):
            proxy_test_btn.setEnabled(True)
            err_msg = str(err_tuple[1]) if isinstance(err_tuple, tuple) and len(err_tuple) > 1 else "Unknown error"
            proxy_test_status_lbl.setText(f"Error: {err_msg}")
            proxy_test_status_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt; font-weight: bold;")

        worker = dialog._proxy_test_runner.run(
            test_stealth_proxy_connection, sel_mode, wg_p, cust_u
        )
        worker.finished.connect(_on_proxy_test_done)
        worker.error.connect(_on_proxy_test_error)

    proxy_test_btn.clicked.connect(_on_test_proxy_clicked)

    layout.addWidget(card)
    layout.addStretch()
    return _scrolled(widget)


# Backward-compatibility alias
create_stealth_tab = create_stealth_subtab
