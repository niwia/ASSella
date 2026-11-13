"""Settings subtab for Stealth proxy and VPN routing."""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.themed_file_dialog import ThemedFileDialog
from ui.dialogs.themed_message_box import ThemedMessageBox
from ui.sizes import size_control
from utils.helpers import create_checkbox_setting
from utils.kuroko import (
    StealthPoolManager,
    WireguardProfileManager,
    get_endpoint_scores,
    test_and_benchmark_endpoint,
)
from utils.main_thread import post_to_main_thread
from utils.task_runner import TaskRunner

logger = logging.getLogger(__name__)


def _safe_string_list(settings, key: str, default: Optional[list] = None) -> list[str]:
    """Safely retrieve a list of strings from QSettings across versions and platforms."""
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


def _format_cached_score(entry: Optional[dict]) -> str:
    """Format an endpoint score entry into a human-readable summary string."""
    if not entry or not entry.get("ok"):
        return ""
    parts = []
    exit_label = entry.get("exit_label", "").strip()
    if exit_label:
        parts.append(exit_label)
    ping = entry.get("ping_ms", 0)
    if ping > 0:
        parts.append(f"{ping} ms")
    speed = entry.get("speed_mbps", 0.0)
    if speed > 0.0:
        parts.append(f"{speed} Mbps")
    return " · ".join(parts)


def _scrolled(page: QWidget) -> QScrollArea:
    """Wrap a settings page in a transparent scroll area."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
    area.setWidget(page)
    return area


class StealthSetupGuideDialog(QDialog):
    """Clean themed dialog explaining the three supported stealth proxy methods with action buttons."""

    def __init__(self, parent=None, on_profile_imported=None):
        super().__init__(parent)
        self.on_profile_imported = on_profile_imported
        self.setWindowTitle("Stealth Setup Guide & Options")
        self.setMinimumWidth(600)
        self.setModal(True)

        accent = getattr(parent, "accent_color", "#89B4FA") if parent else "#89B4FA"
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #1A1A20;
                color: #E0E0E0;
            }}
            QLabel {{
                color: #CCCCCC;
                font-size: 8.5pt;
                line-height: 1.4;
            }}
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 5px;
                padding: 5px 12px;
                color: #FFFFFF;
                font-size: 8.5pt;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.16);
                border-color: {accent};
            }}
            QPushButton:pressed {{
                background-color: rgba(255, 255, 255, 0.04);
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

        # Title Header
        title_lbl = QLabel("Stealth Proxy Setup Guide & Options")
        title_lbl.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {accent};")
        layout.addWidget(title_lbl)

        desc_lbl = QLabel(
            "ASSella routes depot downloads through rootless userspace tunnels without altering system routing "
            "or requiring sudo privileges. Choose the option that fits your setup:"
        )
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        # Card 1: Cloudflare WARP
        c1 = QFrame()
        c1.setStyleSheet("QFrame { background-color: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 8px; padding: 10px; }")
        c1_lay = QVBoxLayout(c1)
        c1_lay.setSpacing(6)
        c1_title = QLabel("1. Automatic Cloudflare WARP (Zero-Config)")
        c1_title.setStyleSheet("font-size: 9.5pt; font-weight: bold; color: #FFFFFF;")
        c1_desc = QLabel(
            "Built-in encrypted VPN connecting directly to Cloudflare's global edge network via userspace WireGuard. "
            "Requires no account or configuration. Select Cloudflare WARP and start downloading immediately."
        )
        c1_desc.setWordWrap(True)
        c1_lay.addWidget(c1_title)
        c1_lay.addWidget(c1_desc)
        layout.addWidget(c1)

        # Card 2: Free WireGuard Configurations
        c2 = QFrame()
        c2.setStyleSheet("QFrame { background-color: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 8px; padding: 10px; }")
        c2_lay = QVBoxLayout(c2)
        c2_lay.setSpacing(8)
        c2_title = QLabel("2. Free WireGuard Configurations (.conf)")
        c2_title.setStyleSheet("font-size: 9.5pt; font-weight: bold; color: #FFFFFF;")
        c2_desc = QLabel(
            "Download free or commercial WireGuard configuration files (.conf) from providers such as Proton VPN "
            "(free tier includes Netherlands, USA, Japan, Romania, and Poland with unlimited data), Windscribe, or Mullvad. "
            "Import them into ASSella to route traffic through specific regions."
        )
        c2_desc.setWordWrap(True)
        c2_lay.addWidget(c2_title)
        c2_lay.addWidget(c2_desc)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        import_conf_btn = QPushButton("+ Import .conf File")
        import_conf_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        action_row.addWidget(import_conf_btn)

        copy_url_btn = QPushButton("Copy Proton VPN URL")
        copy_url_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        action_row.addWidget(copy_url_btn)

        open_url_btn = QPushButton("Open in Browser")
        open_url_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        action_row.addWidget(open_url_btn)

        action_row.addStretch()
        c2_lay.addLayout(action_row)

        self.guide_status_lbl = QLabel("")
        self.guide_status_lbl.setStyleSheet(f"font-size: 8pt; color: {accent}; font-weight: 500;")
        c2_lay.addWidget(self.guide_status_lbl)

        layout.addWidget(c2)

        # Card 3: Custom SOCKS5 / HTTP Proxy
        c3 = QFrame()
        c3.setStyleSheet("QFrame { background-color: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 8px; padding: 10px; }")
        c3_lay = QVBoxLayout(c3)
        c3_lay.setSpacing(6)
        c3_title = QLabel("3. Custom SOCKS5 / HTTP Proxy")
        c3_title.setStyleSheet("font-size: 9.5pt; font-weight: bold; color: #FFFFFF;")
        c3_desc = QLabel(
            "Route traffic through an existing local or remote proxy. Supports Tor (socks5://127.0.0.1:9050), "
            "SSH dynamic port forwards (ssh -D 1080 user@host), and authenticated corporate or private SOCKS5 proxies."
        )
        c3_desc.setWordWrap(True)
        c3_lay.addWidget(c3_title)
        c3_lay.addWidget(c3_desc)
        layout.addWidget(c3)

        # Connections
        proton_url = "https://account.protonvpn.com/downloads#wireguard-configuration"

        def _on_import():
            path, _ = ThemedFileDialog.getOpenFileName(
                self,
                "Select WireGuard Configuration",
                os.path.expanduser("~"),
                "WireGuard Config (*.conf);;All Files (*)",
            )
            if path:
                imported = WireguardProfileManager.import_profile(path)
                if imported:
                    self.guide_status_lbl.setText(f"Successfully imported: {imported['filename']}")
                    if self.on_profile_imported:
                        try:
                            self.on_profile_imported(imported["filename"])
                        except Exception as e:
                            logger.debug(f"[StealthGuide] Callback error: {e}")

        def _on_copy():
            try:
                cb = QApplication.clipboard()
                if cb:
                    cb.setText(proton_url)
                self.guide_status_lbl.setText("Copied Proton VPN URL to clipboard.")
            except Exception as e:
                logger.debug(f"[StealthGuide] Copy error: {e}")

        def _on_open():
            try:
                QDesktopServices.openUrl(QUrl(proton_url))
            except Exception as e:
                logger.debug(f"[StealthGuide] Open URL error: {e}")

        import_conf_btn.clicked.connect(_on_import)
        copy_url_btn.clicked.connect(_on_copy)
        open_url_btn.clicked.connect(_on_open)

        # Close Button
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedSize(90, 30)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_box.addWidget(close_btn)
        layout.addLayout(btn_box)


def create_stealth_subtab(dialog) -> QWidget:
    """Create the dedicated Stealth settings subtab inside Downloads."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 8, 0, 0)
    layout.setSpacing(12)

    accent_color = getattr(dialog, "accent_color", "#89B4FA")

    # Task runner for non-blocking network verification
    if not hasattr(dialog, "_stealth_task_runner") or dialog._stealth_task_runner is None:
        dialog._stealth_task_runner = TaskRunner(dialog)

    def _trigger_visor_refresh():
        try:
            if hasattr(dialog, "parent") and dialog.parent() and hasattr(dialog.parent(), "refresh_system_status"):
                dialog.parent().refresh_system_status()
        except Exception as e:
            logger.debug(f"[StealthTab] Error refreshing visor: {e}")

    # Hidden/internal line edit to preserve legacy setting getters
    dialog.stealth_proxy_wg_edit = QLineEdit()
    dialog.stealth_proxy_wg_edit.setVisible(False)
    dialog.stealth_proxy_wg_edit.setText(dialog.settings.value("stealth_proxy_wg_path", "", type=str))

    # Single-mode mutually exclusive radio button group
    single_mode_group = QButtonGroup(dialog)
    single_mode_group.setExclusive(True)

    # In-memory candidate row registry for Benchmark All: cand_id -> {status_lbl, test_btn}
    candidate_row_widgets = {}

    # =========================================================================
    # CARD 1: Stealth Routing (Master Switches + Options + Benchmark)
    # =========================================================================
    card_routing, card_routing_layout = dialog._create_card_frame("")
    card_routing_layout.setSpacing(10)

    # Header Row with Setup Guide Button
    routing_hdr = QHBoxLayout()
    routing_hdr.setContentsMargins(0, 0, 0, 0)
    routing_title = QLabel("Stealth Routing (beta)")
    routing_title.setStyleSheet(f"font-size: 9.5pt; font-weight: bold; color: {accent_color}; border: none; background: transparent;")
    routing_hdr.addWidget(routing_title)
    routing_hdr.addStretch()

    setup_guide_btn = QPushButton("Setup Options...")
    setup_guide_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    setup_guide_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 5px;
            padding: 3px 10px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {accent_color};
        }}
    """)
    routing_hdr.addWidget(setup_guide_btn)
    card_routing_layout.addLayout(routing_hdr)

    # Master Toggle
    dialog.enable_stealth_proxy_checkbox = create_checkbox_setting(
        "Route Depot Downloads Through Proxy / VPN",
        "enable_stealth_proxy",
        False,
        dialog,
        "Routes DepotDownloader through an encrypted tunnel or proxy. Stops downloads immediately if disconnected.",
    )
    dialog.enable_stealth_proxy_checkbox.toggled.connect(lambda _c: _trigger_visor_refresh())
    card_routing_layout.addWidget(dialog.enable_stealth_proxy_checkbox)

    # Multi-Endpoint Rotation Toggle
    dialog.stealth_rotation_checkbox = create_checkbox_setting(
        "Enable Multi-Endpoint Rotation & Auto-Failover",
        "stealth_rotation_enabled",
        False,
        dialog,
        "Pool multiple endpoints (WARP, WireGuard profiles, custom proxies) with automatic dead-peer failover and speed benchmarking.",
    )
    card_routing_layout.addWidget(dialog.stealth_rotation_checkbox)

    # Benchmark All Container (visible when Rotation is ON)
    benchmark_container = QWidget()
    benchmark_layout = QVBoxLayout(benchmark_container)
    benchmark_layout.setContentsMargins(0, 4, 0, 0)
    benchmark_layout.setSpacing(6)

    benchmark_btn_row = QHBoxLayout()
    benchmark_btn_row.setSpacing(10)

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
            border-color: {accent_color};
        }}
        QPushButton:disabled {{
            background-color: rgba(255, 255, 255, 0.04);
            color: rgba(255, 255, 255, 0.4);
            border-color: rgba(255, 255, 255, 0.08);
        }}
    """)
    benchmark_btn_row.addWidget(benchmark_all_btn)

    benchmark_summary_lbl = QLabel("")
    benchmark_summary_lbl.setStyleSheet("font-size: 8.5pt; font-weight: 500; color: #90CAF9;")
    benchmark_summary_lbl.setVisible(False)
    benchmark_btn_row.addWidget(benchmark_summary_lbl, 1)

    benchmark_layout.addLayout(benchmark_btn_row)
    card_routing_layout.addWidget(benchmark_container)
    layout.addWidget(card_routing)

    # Generic Unified Test Handler
    def _run_unified_test(mode: str, wg_path: str, custom_url: str, test_btn: QPushButton, status_lbl: QLabel):
        test_btn.setEnabled(False)
        status_lbl.setText("Testing connection & speed...")
        status_lbl.setStyleSheet("color: #90CAF9; font-size: 8.5pt;")

        def _done(result):
            test_btn.setEnabled(True)
            if isinstance(result, tuple) and len(result) == 5:
                ok, exit_label, ping_ms, speed_mbps, err_msg = result
                if ok:
                    parts = []
                    if exit_label:
                        parts.append(exit_label)
                    if ping_ms > 0:
                        parts.append(f"{ping_ms} ms")
                    if speed_mbps > 0.0:
                        parts.append(f"{speed_mbps} Mbps")
                    txt = " · ".join(parts) if parts else "Connected"
                    status_lbl.setText(f"[OK] {txt}")
                    status_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: 500;")
                else:
                    err = err_msg or "Connection verification failed"
                    status_lbl.setText(f"[Error] {err[:45]}")
                    status_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt;")
            else:
                status_lbl.setText("[Error] Unexpected test result")
                status_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt;")

        worker = dialog._stealth_task_runner.run(
            test_and_benchmark_endpoint, mode, wg_path, custom_url, 8.0, dialog.settings
        )
        worker.finished.connect(_done)
        worker.error.connect(
            lambda _e: (
                test_btn.setEnabled(True),
                status_lbl.setText("[Error] Test execution failed"),
                status_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt;"),
            )
        )

    # =========================================================================
    # CARD A: Cloudflare WARP
    # =========================================================================
    card_warp, card_warp_layout = dialog._create_card_frame("")
    card_warp_layout.setSpacing(8)

    warp_hdr = QHBoxLayout()
    warp_hdr.setSpacing(10)

    warp_radio = QRadioButton("Cloudflare WARP (Automatic Userspace VPN)")
    warp_radio.setCursor(Qt.CursorShape.PointingHandCursor)
    warp_radio.setStyleSheet("font-size: 9.5pt; font-weight: bold; color: #FFFFFF;")
    single_mode_group.addButton(warp_radio)
    warp_hdr.addWidget(warp_radio)

    warp_check = QCheckBox("Cloudflare WARP (Automatic Userspace VPN)")
    warp_check.setCursor(Qt.CursorShape.PointingHandCursor)
    warp_check.setStyleSheet("font-size: 9.5pt; font-weight: bold; color: #FFFFFF;")
    warp_check.setChecked(dialog.settings.value("stealth_pool_warp", True, type=bool))
    warp_check.toggled.connect(lambda c: (dialog.settings.setValue("stealth_pool_warp", c), _trigger_visor_refresh()))
    warp_hdr.addWidget(warp_check)

    warp_hdr.addStretch()

    warp_badge = QLabel("Built-in")
    warp_badge.setStyleSheet("background-color: rgba(129, 199, 132, 0.15); border: 1px solid rgba(129, 199, 132, 0.4); border-radius: 4px; padding: 2px 6px; font-size: 7.5pt; font-weight: bold; color: #81C784;")
    warp_hdr.addWidget(warp_badge)
    card_warp_layout.addLayout(warp_hdr)

    warp_desc = QLabel(
        "Routes downloads through Cloudflare WARP using userspace WireGuard. Zero setup required, no account or root privileges needed."
    )
    warp_desc.setWordWrap(True)
    warp_desc.setStyleSheet("color: #AAAAAA; font-size: 8.5pt;")
    card_warp_layout.addWidget(warp_desc)

    warp_act_row = QHBoxLayout()
    warp_act_row.setSpacing(10)

    warp_test_btn = QPushButton("Test Connection")
    warp_test_btn.setFixedSize(120, 28)
    warp_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    warp_act_row.addWidget(warp_test_btn)

    warp_status_lbl = QLabel("")
    warp_status_lbl.setStyleSheet("font-size: 8.5pt;")
    warp_act_row.addWidget(warp_status_lbl, 1)
    card_warp_layout.addLayout(warp_act_row)

    warp_test_btn.clicked.connect(lambda: _run_unified_test("warp", "", "", warp_test_btn, warp_status_lbl))
    candidate_row_widgets["warp"] = {"status_lbl": warp_status_lbl, "test_btn": warp_test_btn}
    layout.addWidget(card_warp)

    # =========================================================================
    # CARD B: WireGuard Configurations (.conf)
    # =========================================================================
    card_wg, card_wg_layout = dialog._create_card_frame("")
    card_wg_layout.setSpacing(10)

    wg_hdr = QHBoxLayout()
    wg_hdr.setSpacing(10)

    wg_title = QLabel("WireGuard Configurations (.conf)")
    wg_title.setStyleSheet(f"font-size: 9.5pt; font-weight: bold; color: {accent_color}; border: none; background: transparent;")
    wg_hdr.addWidget(wg_title)
    wg_hdr.addStretch()

    add_conf_btn = QPushButton("+ Add .conf")
    add_conf_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    add_conf_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 5px;
            padding: 3px 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {accent_color};
        }}
    """)
    wg_hdr.addWidget(add_conf_btn)

    import_folder_btn = QPushButton("+ Import Folder")
    import_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    import_folder_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 5px;
            padding: 3px 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background-color: rgba(255, 255, 255, 0.16);
            border-color: {accent_color};
        }}
    """)
    wg_hdr.addWidget(import_folder_btn)
    card_wg_layout.addLayout(wg_hdr)

    wg_desc = QLabel(
        "Userspace WireGuard tunnels (Proton VPN, Windscribe, Mullvad, etc.). Import free or custom .conf files."
    )
    wg_desc.setWordWrap(True)
    wg_desc.setStyleSheet("color: #AAAAAA; font-size: 8.5pt;")
    card_wg_layout.addWidget(wg_desc)

    # Container for dynamic profiles list
    profiles_container = QWidget()
    profiles_layout = QVBoxLayout(profiles_container)
    profiles_layout.setContentsMargins(0, 0, 0, 0)
    profiles_layout.setSpacing(6)
    card_wg_layout.addWidget(profiles_container)
    layout.addWidget(card_wg)

    # Registry of WG profile row UI elements: filename -> {radio, check, row_widget, status_lbl, test_btn}
    profile_row_items = {}

    def _refresh_wg_profiles_ui():
        # Clear existing rows
        for fname, item in list(profile_row_items.items()):
            candidate_row_widgets.pop(f"wg:{fname}", None)
            if "radio" in item and item["radio"]:
                single_mode_group.removeButton(item["radio"])
        while profiles_layout.count() > 0:
            child = profiles_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        profile_row_items.clear()

        profiles = WireguardProfileManager.list_profiles()
        saved_active = dialog.settings.value("stealth_active_wg_profile", "", type=str).strip()
        pool_wg_set = set(_safe_string_list(dialog.settings, "stealth_pool_wg_profiles"))
        scores = get_endpoint_scores(dialog.settings)
        is_rot = dialog.stealth_rotation_checkbox.isChecked()

        if not profiles:
            empty_lbl = QLabel("No WireGuard profiles imported yet. Click '+ Add .conf' above to import.")
            empty_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 8.5pt; font-style: italic; padding: 6px;")
            profiles_layout.addWidget(empty_lbl)
            return

        for p in profiles:
            fname = p["filename"]
            fpath = p["path"]

            row_frame = QFrame()
            row_frame.setStyleSheet("""
                QFrame {
                    background-color: rgba(255, 255, 255, 0.025);
                    border: 1px solid rgba(255, 255, 255, 0.07);
                    border-radius: 6px;
                }
            """)
            r_lay = QHBoxLayout(row_frame)
            r_lay.setContentsMargins(8, 6, 8, 6)
            r_lay.setSpacing(10)

            # Single mode Radio
            p_radio = QRadioButton(fname)
            p_radio.setCursor(Qt.CursorShape.PointingHandCursor)
            p_radio.setStyleSheet("font-size: 9pt; font-weight: 500; color: #FFFFFF;")
            p_radio.setVisible(not is_rot)
            single_mode_group.addButton(p_radio)
            if fname == saved_active and dialog.settings.value("stealth_proxy_mode", "warp", type=str) == "wireguard":
                p_radio.setChecked(True)
            r_lay.addWidget(p_radio)

            # Rotation mode Checkbox
            p_check = QCheckBox(fname)
            p_check.setCursor(Qt.CursorShape.PointingHandCursor)
            p_check.setStyleSheet("font-size: 9pt; font-weight: 500; color: #FFFFFF;")
            p_check.setVisible(is_rot)
            p_check.setChecked(fname in pool_wg_set)
            r_lay.addWidget(p_check)

            r_lay.addStretch()

            # Unified Test Button
            p_test_btn = QPushButton("Test Connection")
            p_test_btn.setFixedSize(110, 26)
            p_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            r_lay.addWidget(p_test_btn)

            # Status Label
            p_status_lbl = QLabel("")
            p_status_lbl.setStyleSheet("font-size: 8.5pt;")
            cand_score = scores.get(f"wg:{fname}")
            cached_text = _format_cached_score(cand_score)
            if cached_text:
                p_status_lbl.setText(f"[OK] {cached_text}")
                p_status_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: 500;")
            r_lay.addWidget(p_status_lbl)

            # Delete Button
            p_del_btn = QPushButton("Remove")
            p_del_btn.setFixedSize(68, 26)
            p_del_btn.setToolTip(f"Remove profile '{fname}'")
            p_del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            p_del_btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    border: 1px solid rgba(229, 115, 115, 0.35);
                    border-radius: 4px;
                    color: #E57373;
                    font-size: 8pt;
                    font-weight: 500;
                }
                QPushButton:hover {
                    background-color: rgba(229, 115, 115, 0.2);
                    border-color: #E57373;
                }
            """)
            r_lay.addWidget(p_del_btn)

            profiles_layout.addWidget(row_frame)

            # Connect Radio Toggle
            def _make_radio_cb(profile_fname, profile_path):
                return lambda checked: _on_wg_radio_toggled(profile_fname, profile_path, checked)

            p_radio.toggled.connect(_make_radio_cb(fname, fpath))

            # Connect Checkbox Toggle
            def _make_check_cb(profile_fname):
                return lambda checked: _on_wg_check_toggled(profile_fname, checked)

            p_check.toggled.connect(_make_check_cb(fname))

            # Connect Test Button
            def _make_test_cb(profile_path, test_btn_widget, status_lbl_widget):
                return lambda: _run_unified_test("wireguard", profile_path, "", test_btn_widget, status_lbl_widget)

            p_test_btn.clicked.connect(_make_test_cb(fpath, p_test_btn, p_status_lbl))

            # Connect Delete Button
            def _make_del_cb(profile_fname):
                return lambda: _on_delete_profile(profile_fname)

            p_del_btn.clicked.connect(_make_del_cb(fname))

            profile_row_items[fname] = {
                "radio": p_radio,
                "check": p_check,
                "status_lbl": p_status_lbl,
                "test_btn": p_test_btn,
            }
            candidate_row_widgets[f"wg:{fname}"] = {
                "status_lbl": p_status_lbl,
                "test_btn": p_test_btn,
            }

    def _on_wg_radio_toggled(profile_fname: str, profile_path: str, checked: bool):
        if checked:
            dialog.settings.setValue("stealth_proxy_mode", "wireguard")
            dialog.settings.setValue("stealth_active_wg_profile", profile_fname)
            dialog.settings.setValue("stealth_proxy_wg_path", profile_path)
            dialog.stealth_proxy_wg_edit.setText(profile_path)
            _trigger_visor_refresh()

    def _on_wg_check_toggled(profile_fname: str, checked: bool):
        cur_list = _safe_string_list(dialog.settings, "stealth_pool_wg_profiles")
        cur_set = set(str(x) for x in cur_list)
        if checked:
            cur_set.add(profile_fname)
        else:
            cur_set.discard(profile_fname)
        dialog.settings.setValue("stealth_pool_wg_profiles", sorted(list(cur_set)))
        _trigger_visor_refresh()

    def _on_delete_profile(profile_fname: str):
        confirmed = ThemedMessageBox.question(
            dialog,
            "Delete Profile",
            f"Are you sure you want to delete WireGuard configuration '{profile_fname}'?",
        )
        if confirmed:
            WireguardProfileManager.delete_profile(profile_fname)
            # Remove from pool settings
            cur_list = _safe_string_list(dialog.settings, "stealth_pool_wg_profiles")
            cur_set = set(str(x) for x in cur_list)
            cur_set.discard(profile_fname)
            dialog.settings.setValue("stealth_pool_wg_profiles", sorted(list(cur_set)))

            # If active single profile was deleted, fall back to WARP
            if dialog.settings.value("stealth_active_wg_profile", "", type=str) == profile_fname:
                dialog.settings.setValue("stealth_active_wg_profile", "")
                dialog.settings.setValue("stealth_proxy_wg_path", "")
                dialog.settings.setValue("stealth_proxy_mode", "warp")
                warp_radio.setChecked(True)

            _refresh_wg_profiles_ui()
            _trigger_visor_refresh()

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
                _refresh_wg_profiles_ui()
                _trigger_visor_refresh()

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
                _refresh_wg_profiles_ui()
                _trigger_visor_refresh()
                ThemedMessageBox.information(
                    dialog,
                    "Profiles Imported",
                    f"Successfully imported {imported_count} WireGuard configuration(s).",
                )

    add_conf_btn.clicked.connect(_on_add_conf)
    import_folder_btn.clicked.connect(_on_import_folder)

    # =========================================================================
    # CARD C: Custom SOCKS5 / HTTP Proxy
    # =========================================================================
    card_custom, card_custom_layout = dialog._create_card_frame("")
    card_custom_layout.setSpacing(8)

    custom_hdr = QHBoxLayout()
    custom_hdr.setSpacing(10)

    custom_radio = QRadioButton("Custom SOCKS5 / HTTP Proxy")
    custom_radio.setCursor(Qt.CursorShape.PointingHandCursor)
    custom_radio.setStyleSheet("font-size: 9.5pt; font-weight: bold; color: #FFFFFF;")
    single_mode_group.addButton(custom_radio)
    custom_hdr.addWidget(custom_radio)

    custom_check = QCheckBox("Custom SOCKS5 / HTTP Proxy")
    custom_check.setCursor(Qt.CursorShape.PointingHandCursor)
    custom_check.setStyleSheet("font-size: 9.5pt; font-weight: bold; color: #FFFFFF;")
    custom_check.setChecked(dialog.settings.value("stealth_pool_custom", False, type=bool))
    custom_check.toggled.connect(lambda c: (dialog.settings.setValue("stealth_pool_custom", c), _trigger_visor_refresh()))
    custom_hdr.addWidget(custom_check)

    custom_hdr.addStretch()
    card_custom_layout.addLayout(custom_hdr)

    custom_desc = QLabel(
        "Route downloads through an existing local or remote SOCKS5 / HTTP proxy (e.g. Tor or SSH dynamic tunnel)."
    )
    custom_desc.setWordWrap(True)
    custom_desc.setStyleSheet("color: #AAAAAA; font-size: 8.5pt;")
    card_custom_layout.addWidget(custom_desc)

    # Left-aligned URL input row
    url_row = QHBoxLayout()
    url_row.setSpacing(8)

    url_lbl = QLabel("Proxy URL:")
    url_lbl.setFixedWidth(80)
    url_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")
    url_row.addWidget(url_lbl)

    dialog.stealth_proxy_custom_edit = QLineEdit()
    dialog.stealth_proxy_custom_edit.setPlaceholderText("socks5://127.0.0.1:1080 (or socks5://user:pass@host:port)")
    custom_saved_url = dialog.settings.value("stealth_proxy_custom_url", "socks5://127.0.0.1:1080", type=str)
    dialog.stealth_proxy_custom_edit.setText(custom_saved_url)
    dialog.stealth_proxy_custom_edit.setStyleSheet(f"""
        QLineEdit {{
            background-color: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 5px;
            padding: 4px 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
        }}
        QLineEdit:focus {{
            border-color: {accent_color};
            background-color: rgba(255, 255, 255, 0.08);
        }}
    """)
    dialog.stealth_proxy_custom_edit.textChanged.connect(
        lambda text: (
            dialog.settings.setValue("stealth_proxy_custom_url", text.strip()),
            _trigger_visor_refresh(),
        )
    )
    url_row.addWidget(dialog.stealth_proxy_custom_edit, 1)
    card_custom_layout.addLayout(url_row)

    custom_act_row = QHBoxLayout()
    custom_act_row.setSpacing(10)

    custom_test_btn = QPushButton("Test Connection")
    custom_test_btn.setFixedSize(120, 28)
    custom_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    custom_act_row.addWidget(custom_test_btn)

    custom_status_lbl = QLabel("")
    custom_status_lbl.setStyleSheet("font-size: 8.5pt;")
    custom_act_row.addWidget(custom_status_lbl, 1)
    card_custom_layout.addLayout(custom_act_row)

    custom_test_btn.clicked.connect(
        lambda: _run_unified_test("custom", "", dialog.stealth_proxy_custom_edit.text().strip(), custom_test_btn, custom_status_lbl)
    )
    candidate_row_widgets["custom"] = {"status_lbl": custom_status_lbl, "test_btn": custom_test_btn}
    layout.addWidget(card_custom)

    # Initial Population of WG Profiles
    _refresh_wg_profiles_ui()

    # Pre-populate status labels with cached scores
    init_scores = get_endpoint_scores(dialog.settings)
    warp_cached = _format_cached_score(init_scores.get("warp"))
    if warp_cached:
        warp_status_lbl.setText(f"[OK] {warp_cached}")
        warp_status_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: 500;")

    custom_cached = _format_cached_score(init_scores.get("custom"))
    if custom_cached:
        custom_status_lbl.setText(f"[OK] {custom_cached}")
        custom_status_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: 500;")

    # Radio button selection handler for WARP and Custom
    def _on_warp_radio_toggled(checked):
        if checked:
            dialog.settings.setValue("stealth_proxy_mode", "warp")
            _trigger_visor_refresh()

    def _on_custom_radio_toggled(checked):
        if checked:
            dialog.settings.setValue("stealth_proxy_mode", "custom")
            _trigger_visor_refresh()

    warp_radio.toggled.connect(_on_warp_radio_toggled)
    custom_radio.toggled.connect(_on_custom_radio_toggled)

    # Single Mode Initial Selection
    saved_mode = dialog.settings.value("stealth_proxy_mode", "warp", type=str).lower().strip()
    if saved_mode == "warp":
        warp_radio.setChecked(True)
    elif saved_mode == "custom":
        custom_radio.setChecked(True)
    elif saved_mode == "wireguard":
        active_prof = dialog.settings.value("stealth_active_wg_profile", "", type=str).strip()
        if active_prof in profile_row_items:
            profile_row_items[active_prof]["radio"].setChecked(True)
        else:
            warp_radio.setChecked(True)

    # Mode Switcher (Single Mode vs Rotation Mode)
    def _update_mode_visibility():
        is_rot = dialog.stealth_rotation_checkbox.isChecked()
        benchmark_container.setVisible(is_rot)

        warp_radio.setVisible(not is_rot)
        warp_check.setVisible(is_rot)

        custom_radio.setVisible(not is_rot)
        custom_check.setVisible(is_rot)

        for p_data in profile_row_items.values():
            if "radio" in p_data and p_data["radio"]:
                p_data["radio"].setVisible(not is_rot)
            if "check" in p_data and p_data["check"]:
                p_data["check"].setVisible(is_rot)

        _trigger_visor_refresh()

    dialog.stealth_rotation_checkbox.toggled.connect(lambda _c: _update_mode_visibility())
    _update_mode_visibility()

    # Benchmark All Handler
    def _on_benchmark_all():
        benchmark_all_btn.setEnabled(False)
        benchmark_summary_lbl.setVisible(True)
        benchmark_summary_lbl.setText("Testing candidates sequentially (5 MB burst)...")
        benchmark_summary_lbl.setStyleSheet("color: #90CAF9; font-size: 8.5pt;")

        for cand_id, widgets in candidate_row_widgets.items():
            if "status_lbl" in widgets:
                widgets["status_lbl"].setText("Waiting...")
                widgets["status_lbl"].setStyleSheet("color: #B0BEC5; font-size: 8pt;")

        def _progress_cb(cand_id, ok, exit_label, ping_ms, speed_mbps, msg):
            def _update_row():
                w = candidate_row_widgets.get(cand_id)
                if w and "status_lbl" in w:
                    if ok:
                        parts = []
                        if exit_label:
                            parts.append(exit_label)
                        if ping_ms > 0:
                            parts.append(f"{ping_ms} ms")
                        if speed_mbps > 0.0:
                            parts.append(f"{speed_mbps} Mbps")
                        txt = " · ".join(parts) if parts else "OK"
                        w["status_lbl"].setText(f"[OK] {txt}")
                        w["status_lbl"].setStyleSheet("color: #81C784; font-size: 8pt; font-weight: 500;")
                    else:
                        w["status_lbl"].setText(f"[Error] {msg[:35]}")
                        w["status_lbl"].setStyleSheet("color: #E57373; font-size: 8pt;")
            post_to_main_thread(_update_row)

        def _done_all(results):
            benchmark_all_btn.setEnabled(True)
            if isinstance(results, list) and results:
                successful = [r for r in results if r.get("ok")]
                if successful:
                    fastest = successful[0]
                    f_name = fastest.get("name", "Fastest")
                    f_speed = fastest.get("speed_mbps", 0.0)
                    f_ping = fastest.get("ping_ms", 0)
                    benchmark_summary_lbl.setText(f"Benchmark complete. Fastest: {f_name} ({f_speed} Mbps, {f_ping} ms)")
                    benchmark_summary_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: bold;")
                else:
                    benchmark_summary_lbl.setText("All benchmarked endpoints failed connectivity verification.")
                    benchmark_summary_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt; font-weight: bold;")
            else:
                benchmark_summary_lbl.setText("Benchmark finished.")

        worker = dialog._stealth_task_runner.run(
            StealthPoolManager.benchmark_all_candidates, dialog.settings, _progress_cb
        )
        worker.finished.connect(_done_all)
        worker.error.connect(lambda _e: (benchmark_all_btn.setEnabled(True), benchmark_summary_lbl.setText("Benchmark encountered an error.")))

    benchmark_all_btn.clicked.connect(_on_benchmark_all)

    # Setup Guide Button Handler
    def _open_setup_guide():
        dlg = StealthSetupGuideDialog(dialog, on_profile_imported=lambda _fname: _refresh_wg_profiles_ui())
        dlg.exec()

    setup_guide_btn.clicked.connect(_open_setup_guide)

    layout.addStretch()
    return _scrolled(widget)


# Backward compatibility alias
create_stealth_tab = create_stealth_subtab

