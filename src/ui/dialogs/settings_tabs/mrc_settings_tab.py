"""Modular Testing tab for controlling MRC providers, race parameters, and developer utilities.

Accessible when unlocked via the Konami cheat code (Testing).
Manages provider hierarchy, fallback rescue order, race/timeout parameters,
synchronizes configuration to ~/.config/SLSsteam/mrc_config.lua,
and provides developer toggles to test one-time dialogs (Welcome screen, TWP, lock menu).
"""

import logging
import threading
from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QComboBox,
    QSpinBox,
    QLineEdit,
    QPushButton,
    QMessageBox,
    QScrollArea,
    QFrame,
    QCheckBox,
)

from managers.mrc_config_manager import MRCConfigManager
from utils.settings import get_settings

logger = logging.getLogger(__name__)


def create_mrc_settings_tab(dialog) -> QWidget:
    """Create the modular MRC Testing tab."""
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

    cfg = MRCConfigManager.load_config()

    from ui.dialogs.settings_tabs.at0m_tab import get_active_download_plugin
    active_plugin = get_active_download_plugin() or ""
    active_lower = active_plugin.lower()
    is_spacetest = ("spacetest" in active_lower) or ("spacebunny" in active_lower)

    # MRC Guard Card (shown when active plugin is not download-1.4.0-spacetest.lua / spacebunny)
    guard_card, guard_layout = dialog._create_card_frame("MRC Configuration Guard")
    guard_desc = QLabel(
        f"<b>MRC Customization Inactive:</b> Active plugin is currently <code>{active_plugin or 'None'}</code>.<br><br>"
        "Manifest Request Code (MRC) authority hierarchy, secondary rescue routing, and race parameters require "
        "<b><code>download-1.4.0-spacetest.lua</code></b> (SpaceBunny / SpaceTest). Standard <code>download.lua</code> "
        "does not read these parameters.<br><br>"
        "These options are hidden to prevent incompatible configuration overrides."
    )
    guard_desc.setStyleSheet("color: rgba(255, 255, 255, 0.75); font-size: 8.5pt;")
    guard_desc.setWordWrap(True)
    guard_layout.addWidget(guard_desc)

    goto_atom_btn = QPushButton("Go to at0-m Settings (Deploy SpaceTest)")
    goto_atom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    goto_atom_btn.setStyleSheet("""
        QPushButton {
            background-color: rgba(33, 150, 243, 0.15);
            color: #64B5F6;
            border: 1px solid rgba(100, 181, 246, 0.45);
            border-radius: 6px;
            padding: 5px 12px;
            font-size: 8.5pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(33, 150, 243, 0.28);
        }
    """)
    def _go_to_atom():
        for i in range(dialog.tab_widget.count()):
            if dialog.tab_widget.tabText(i).lower() in ("at0-m", "atom"):
                dialog.tab_widget.setCurrentIndex(i)
                break
    goto_atom_btn.clicked.connect(_go_to_atom)
    guard_layout.addWidget(goto_atom_btn)

    layout.addWidget(guard_card)

    # Container for Cards 1, 2, 3 and actions_row
    mrc_box = QWidget()
    mrc_layout = QVBoxLayout(mrc_box)
    mrc_layout.setContentsMargins(0, 0, 0, 0)
    mrc_layout.setSpacing(8)

    # -- 1. Provider Hierarchy Card --
    p_card, p_layout = dialog._create_card_frame("MRC Provider Hierarchy (Lua)")
    p_desc = QLabel(
        "Control how SLSsteam Lua plugins resolve Manifest Request Codes (MRC). "
        "Define primary authority and secondary fallback rescue paths."
    )
    p_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    p_desc.setWordWrap(True)
    p_layout.addWidget(p_desc)

    p_grid = QGridLayout()
    p_grid.setHorizontalSpacing(12)
    p_grid.setVerticalSpacing(6)

    # Authority Provider
    auth_lbl = QLabel("Primary Authority:")
    auth_lbl.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500;")
    dialog.mrc_auth_combo = QComboBox()
    dialog.mrc_auth_combo.setFixedWidth(160)
    dialog.mrc_auth_combo.addItem("Wudrm (Primary)", "wudrm")
    dialog.mrc_auth_combo.addItem("ManifestDeX", "manifestdex")
    idx = dialog.mrc_auth_combo.findData(cfg.get("authority", "wudrm"))
    if idx >= 0:
        dialog.mrc_auth_combo.setCurrentIndex(idx)
    p_grid.addWidget(auth_lbl, 0, 0)
    p_grid.addWidget(dialog.mrc_auth_combo, 0, 1)

    # Rescue Provider
    rescue_lbl = QLabel("Secondary Rescue:")
    rescue_lbl.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500;")
    dialog.mrc_rescue_combo = QComboBox()
    dialog.mrc_rescue_combo.setFixedWidth(160)
    dialog.mrc_rescue_combo.addItem("ManifestDeX", "manifestdex")
    dialog.mrc_rescue_combo.addItem("Wudrm", "wudrm")
    idx = dialog.mrc_rescue_combo.findData(cfg.get("rescue", "manifestdex"))
    if idx >= 0:
        dialog.mrc_rescue_combo.setCurrentIndex(idx)
    p_grid.addWidget(rescue_lbl, 1, 0)
    p_grid.addWidget(dialog.mrc_rescue_combo, 1, 1)

    p_layout.addLayout(p_grid)
    mrc_layout.addWidget(p_card)

    # -- 2. Tuning & Race Parameters Card --
    t_card, t_layout = dialog._create_card_frame("Race & Tuning Parameters")
    t_desc = QLabel("Fine-tune network latency thresholds and timeout budgets for Lua manifest requests.")
    t_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    t_desc.setWordWrap(True)
    t_layout.addWidget(t_desc)

    t_grid = QGridLayout()
    t_grid.setHorizontalSpacing(12)
    t_grid.setVerticalSpacing(6)

    # Full Timeout MS
    full_lbl = QLabel("Authority Budget (ms):")
    full_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    dialog.mrc_full_spin = QSpinBox()
    dialog.mrc_full_spin.setRange(500, 10000)
    dialog.mrc_full_spin.setSingleStep(100)
    dialog.mrc_full_spin.setValue(int(cfg.get("full_timeout_ms", 2000)))
    dialog.mrc_full_spin.setFixedWidth(85)
    t_grid.addWidget(full_lbl, 0, 0)
    t_grid.addWidget(dialog.mrc_full_spin, 0, 1)

    # Probe Timeout MS
    probe_lbl = QLabel("Probe Timeout (ms):")
    probe_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    dialog.mrc_probe_spin = QSpinBox()
    dialog.mrc_probe_spin.setRange(200, 3000)
    dialog.mrc_probe_spin.setSingleStep(50)
    dialog.mrc_probe_spin.setValue(int(cfg.get("probe_timeout_ms", 700)))
    dialog.mrc_probe_spin.setFixedWidth(85)
    t_grid.addWidget(probe_lbl, 0, 2)
    t_grid.addWidget(dialog.mrc_probe_spin, 0, 3)

    # Mark Down Timeouts
    down_thresh_lbl = QLabel("Timeouts to Mark Down:")
    down_thresh_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    dialog.mrc_down_spin = QSpinBox()
    dialog.mrc_down_spin.setRange(1, 10)
    dialog.mrc_down_spin.setValue(int(cfg.get("probe_timeouts_to_mark_down", 2)))
    dialog.mrc_down_spin.setFixedWidth(85)
    t_grid.addWidget(down_thresh_lbl, 1, 0)
    t_grid.addWidget(dialog.mrc_down_spin, 1, 1)

    # Provisional TTL
    prov_lbl = QLabel("Provisional Rescue TTL (s):")
    prov_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    dialog.mrc_prov_spin = QSpinBox()
    dialog.mrc_prov_spin.setRange(10, 3600)
    dialog.mrc_prov_spin.setSingleStep(30)
    dialog.mrc_prov_spin.setValue(int(cfg.get("provisional_ttl_s", 300)))
    dialog.mrc_prov_spin.setFixedWidth(85)
    t_grid.addWidget(prov_lbl, 1, 2)
    t_grid.addWidget(dialog.mrc_prov_spin, 1, 3)

    t_layout.addLayout(t_grid)
    mrc_layout.addWidget(t_card)

    # -- 3. Actions Row --
    actions_row = QHBoxLayout()
    actions_row.setSpacing(8)

    sync_btn = QPushButton("Sync Settings to Lua")
    sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    sync_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {getattr(dialog, 'accent_color', '#ff7518')};
            color: #000000;
            border: 1px solid {getattr(dialog, 'accent_color', '#ff7518')};
            border-radius: 6px;
            padding: 5px 14px;
            font-size: 8.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: #FFFFFF;
            color: #000000;
        }}
    """)

    reset_btn = QPushButton("Reset Defaults")
    reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reset_btn.setStyleSheet("""
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 5px 12px;
            font-size: 8.5pt;
            color: #FFFFFF;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.16);
        }
    """)

    status_lbl = QLabel("")
    status_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: 500;")

    def _on_sync_clicked():
        cur_cfg = {
            "authority": dialog.mrc_auth_combo.currentData(),
            "rescue": dialog.mrc_rescue_combo.currentData(),
            "full_timeout_ms": dialog.mrc_full_spin.value(),
            "probe_timeout_ms": dialog.mrc_probe_spin.value(),
            "probe_timeouts_to_mark_down": dialog.mrc_down_spin.value(),
            "down_ttl_ms": 60000,
            "provisional_ttl_s": dialog.mrc_prov_spin.value(),
            "fail_ttl_s": 120,
        }
        MRCConfigManager.save_config(cur_cfg)
        ok = MRCConfigManager.sync_to_slssteam(cur_cfg)
        if ok:
            status_lbl.setText("✓ Synced to mrc_config.lua (Restart Steam to apply)")
            status_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt;")
        else:
            status_lbl.setText("⚠ Failed to write SLSsteam config")
            status_lbl.setStyleSheet("color: #E57373; font-size: 8.5pt;")

    def _on_reset_clicked():
        d = MRCConfigManager.DEFAULT_CONFIG
        idx1 = dialog.mrc_auth_combo.findData(d["authority"])
        if idx1 >= 0:
            dialog.mrc_auth_combo.setCurrentIndex(idx1)
        idx2 = dialog.mrc_rescue_combo.findData(d["rescue"])
        if idx2 >= 0:
            dialog.mrc_rescue_combo.setCurrentIndex(idx2)
        dialog.mrc_full_spin.setValue(d["full_timeout_ms"])
        dialog.mrc_probe_spin.setValue(d["probe_timeout_ms"])
        dialog.mrc_down_spin.setValue(d["probe_timeouts_to_mark_down"])
        dialog.mrc_prov_spin.setValue(d["provisional_ttl_s"])
        status_lbl.setText("Reset to default values.")
        status_lbl.setStyleSheet("color: #FFF; font-size: 8.5pt;")

    sync_btn.clicked.connect(_on_sync_clicked)
    reset_btn.clicked.connect(_on_reset_clicked)

    actions_row.addWidget(sync_btn)
    actions_row.addWidget(reset_btn)
    actions_row.addWidget(status_lbl)
    actions_row.addStretch()
    mrc_layout.addLayout(actions_row)

    layout.addWidget(mrc_box)
    mrc_box.setVisible(is_spacetest)
    guard_card.setVisible(not is_spacetest)

    def _refresh_mrc_guard():
        cur_act = get_active_download_plugin() or ""
        cur_low = cur_act.lower()
        cur_st = ("spacetest" in cur_low) or ("spacebunny" in cur_low)
        mrc_box.setVisible(cur_st)
        guard_card.setVisible(not cur_st)
        if not cur_st:
            guard_desc.setText(
                f"<b>MRC Customization Inactive:</b> Active plugin is currently <code>{cur_act or 'None'}</code>.<br><br>"
                "Manifest Request Code (MRC) authority hierarchy, secondary rescue routing, and race parameters require "
                "<b><code>download-1.4.0-spacetest.lua</code></b> (SpaceBunny / SpaceTest). Standard <code>download.lua</code> "
                "does not read these parameters.<br><br>"
                "These options are hidden to prevent incompatible configuration overrides."
            )

    tab.showEvent = lambda ev: (_refresh_mrc_guard(), QWidget.showEvent(tab, ev))

    settings = get_settings()

    # -- 4. Custom Proxy / Cloudflare Worker Card --
    proxy_card, proxy_layout = dialog._create_card_frame("Custom Proxy / Cloudflare Worker")
    proxy_desc = QLabel(
        "Configure a custom proxy or Cloudflare Worker endpoint for bypassing ISP restrictions. "
        "Any applied endpoint replaces the standard default and persists across ASSella updates."
    )
    proxy_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    proxy_desc.setWordWrap(True)
    proxy_layout.addWidget(proxy_desc)

    from utils.isp_bypass import get_default_wirecutter_endpoint, test_gateway_wirecutter

    dialog.proxy_url_input = QLineEdit()
    dialog.proxy_url_input.setPlaceholderText(get_default_wirecutter_endpoint())
    saved_proxy = settings.value("wirecutter_url", "", type=str).strip()
    dialog.proxy_url_input.setText(saved_proxy or get_default_wirecutter_endpoint())
    dialog.proxy_url_input.setStyleSheet("""
        QLineEdit {
            background-color: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 5px 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
        }
        QLineEdit:focus {
            border: 1px solid #64B5F6;
        }
    """)
    proxy_layout.addWidget(dialog.proxy_url_input)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)

    NEUTRAL_STYLE = """
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            color: #FFFFFF;
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 5px 14px;
            font-size: 8.5pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.16);
        }
        QPushButton:disabled {
            color: rgba(255, 255, 255, 0.3);
            border-color: rgba(255, 255, 255, 0.08);
            background-color: rgba(255, 255, 255, 0.03);
        }
    """

    SUCCESS_STYLE = """
        QPushButton {
            background-color: rgba(76, 175, 80, 0.22);
            color: #81C784;
            border: 1px solid #81C784;
            border-radius: 6px;
            padding: 5px 14px;
            font-size: 8.5pt;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: rgba(76, 175, 80, 0.32);
        }
        QPushButton:disabled {
            color: rgba(129, 199, 132, 0.4);
            border-color: rgba(129, 199, 132, 0.2);
        }
    """

    FAIL_STYLE = """
        QPushButton {
            background-color: rgba(244, 67, 54, 0.22);
            color: #EF5350;
            border: 1px solid #EF5350;
            border-radius: 6px;
            padding: 5px 14px;
            font-size: 8.5pt;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: rgba(244, 67, 54, 0.32);
        }
        QPushButton:disabled {
            color: rgba(239, 83, 80, 0.4);
            border-color: rgba(239, 83, 80, 0.2);
        }
    """

    reset_proxy_btn = QPushButton("Reset (Default Proxy)")
    reset_proxy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reset_proxy_btn.setStyleSheet(NEUTRAL_STYLE)

    test_proxy_btn = QPushButton("Test")
    test_proxy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    test_proxy_btn.setStyleSheet(NEUTRAL_STYLE)

    apply_proxy_btn = QPushButton("Apply")
    apply_proxy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    apply_proxy_btn.setStyleSheet(NEUTRAL_STYLE)

    has_init_text = bool(dialog.proxy_url_input.text().strip())
    test_proxy_btn.setEnabled(has_init_text)
    apply_proxy_btn.setEnabled(has_init_text)

    class _ProxyBridge(QObject):
        sig = pyqtSignal(bool, str, int)

    test_bridge = _ProxyBridge(tab)
    apply_bridge = _ProxyBridge(tab)

    def _on_proxy_url_changed(txt: str):
        valid = bool(txt.strip())
        test_proxy_btn.setEnabled(valid)
        apply_proxy_btn.setEnabled(valid)
        test_proxy_btn.setStyleSheet(NEUTRAL_STYLE)
        test_proxy_btn.setText("Test")
        apply_proxy_btn.setStyleSheet(NEUTRAL_STYLE)
        apply_proxy_btn.setText("Apply")

    dialog.proxy_url_input.textChanged.connect(_on_proxy_url_changed)

    def _on_reset_proxy():
        def_url = get_default_wirecutter_endpoint()
        dialog.proxy_url_input.setText(def_url)
        settings.remove("wirecutter_url")
        settings.sync()
        test_proxy_btn.setStyleSheet(NEUTRAL_STYLE)
        test_proxy_btn.setText("Test")
        test_proxy_btn.setEnabled(True)
        apply_proxy_btn.setStyleSheet(NEUTRAL_STYLE)
        apply_proxy_btn.setText("Apply")
        apply_proxy_btn.setEnabled(True)

    reset_proxy_btn.clicked.connect(_on_reset_proxy)

    def _on_test_done(ok: bool, msg: str, lat: int):
        test_proxy_btn.setEnabled(True)
        apply_proxy_btn.setEnabled(bool(dialog.proxy_url_input.text().strip()))
        if ok:
            test_proxy_btn.setStyleSheet(SUCCESS_STYLE)
            test_proxy_btn.setText(f"OK ({lat}ms) ✓")
        else:
            test_proxy_btn.setStyleSheet(FAIL_STYLE)
            test_proxy_btn.setText(f"Failed ({msg}) ✗")

    test_bridge.sig.connect(_on_test_done)

    def _on_test_proxy():
        url = dialog.proxy_url_input.text().strip()
        if not url:
            return
        test_proxy_btn.setEnabled(False)
        test_proxy_btn.setText("Testing...")
        apply_proxy_btn.setEnabled(False)

        def _bg():
            ok, msg, lat = test_gateway_wirecutter(url)
            test_bridge.sig.emit(ok, msg, lat)

        threading.Thread(target=_bg, daemon=True).start()

    test_proxy_btn.clicked.connect(_on_test_proxy)

    def _on_apply_done(ok: bool, msg: str, lat: int):
        test_proxy_btn.setEnabled(True)
        apply_proxy_btn.setEnabled(True)
        url = dialog.proxy_url_input.text().strip()
        if ok:
            # Stage 2: Test = Success -> Apply to config
            if url == get_default_wirecutter_endpoint():
                settings.remove("wirecutter_url")
            else:
                settings.setValue("wirecutter_url", url)
            settings.sync()

            test_proxy_btn.setStyleSheet(SUCCESS_STYLE)
            test_proxy_btn.setText(f"OK ({lat}ms) ✓")
            apply_proxy_btn.setStyleSheet(SUCCESS_STYLE)
            apply_proxy_btn.setText("Applied ✓")
        else:
            # Stage 2: Test = Failed -> Do nothing to settings
            test_proxy_btn.setStyleSheet(FAIL_STYLE)
            test_proxy_btn.setText(f"Failed ({msg}) ✗")
            apply_proxy_btn.setStyleSheet(FAIL_STYLE)
            apply_proxy_btn.setText("Test Failed (Not Applied) ✗")

    apply_bridge.sig.connect(_on_apply_done)

    def _on_apply_proxy():
        url = dialog.proxy_url_input.text().strip()
        if not url:
            return
        # Stage 1: Test the endpoint first
        apply_proxy_btn.setEnabled(False)
        apply_proxy_btn.setText("Testing...")
        test_proxy_btn.setEnabled(False)
        test_proxy_btn.setText("Testing...")

        def _bg():
            ok, msg, lat = test_gateway_wirecutter(url)
            apply_bridge.sig.emit(ok, msg, lat)

        threading.Thread(target=_bg, daemon=True).start()

    apply_proxy_btn.clicked.connect(_on_apply_proxy)

    btn_row.addWidget(reset_proxy_btn)
    btn_row.addWidget(test_proxy_btn)
    btn_row.addWidget(apply_proxy_btn)
    btn_row.addStretch()

    proxy_layout.addLayout(btn_row)
    layout.addWidget(proxy_card)

    # -- 5. Testing & Developer Utilities Card --
    test_card, test_layout = dialog._create_card_frame("Testing & Developer Utilities")
    test_desc = QLabel(
        "Manage one-time onboarding states, purge caches, launch test dialogs, and control secret menu access."
    )
    test_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    test_desc.setWordWrap(True)
    test_layout.addWidget(test_desc)

    t_grid = QGridLayout()
    t_grid.setHorizontalSpacing(10)
    t_grid.setVerticalSpacing(8)

    # 1. Welcome Screen
    welcome_seen = settings.value("canary_welcome_seen", False, type=bool)
    welcome_status = QLabel("Welcome: " + ("Seen" if welcome_seen else "Will show next boot"))
    welcome_status.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")
    t_grid.addWidget(welcome_status, 0, 0)

    reset_welcome_btn = QPushButton("Reset (Show Next Boot)")
    reset_welcome_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reset_welcome_btn.setStyleSheet("font-size: 8.5pt; padding: 4px 10px;")
    def _on_reset_welcome():
        settings.setValue("canary_welcome_seen", False)
        settings.sync()
        welcome_status.setText("Welcome: Will show next boot")
        welcome_status.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: 500;")
        QMessageBox.information(dialog, "Welcome Screen", "Welcome screen reset!\nIt will automatically show on the next launch.")
    reset_welcome_btn.clicked.connect(_on_reset_welcome)
    t_grid.addWidget(reset_welcome_btn, 0, 1)

    launch_welcome_btn = QPushButton("Launch Welcome Now")
    launch_welcome_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    launch_welcome_btn.setStyleSheet("font-size: 8.5pt; padding: 4px 10px;")
    def _on_launch_welcome():
        from ui.dialogs.canary_welcome_dialog import CanaryWelcomeDialog
        w_dlg = CanaryWelcomeDialog(dialog)
        w_dlg.exec()
    launch_welcome_btn.clicked.connect(_on_launch_welcome)
    t_grid.addWidget(launch_welcome_btn, 0, 2)

    # 2. Training Wheels Protocol (TWP)
    twp_seen = settings.value("assella_twp_seen", False, type=bool)
    twp_status = QLabel("TWP: " + ("Seen" if twp_seen else "Will show next boot"))
    twp_status.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")
    t_grid.addWidget(twp_status, 1, 0)

    reset_twp_btn = QPushButton("Reset (Show Next Boot)")
    reset_twp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reset_twp_btn.setStyleSheet("font-size: 8.5pt; padding: 4px 10px;")
    def _on_reset_twp():
        settings.setValue("assella_twp_seen", False)
        settings.sync()
        twp_status.setText("TWP: Will show next boot")
        twp_status.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: 500;")
        QMessageBox.information(dialog, "Training Wheels", "Training Wheels Protocol reset!\nIt will trigger on the next launch.")
    reset_twp_btn.clicked.connect(_on_reset_twp)
    t_grid.addWidget(reset_twp_btn, 1, 1)

    launch_twp_btn = QPushButton("Launch TWP Now")
    launch_twp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    launch_twp_btn.setStyleSheet("font-size: 8.5pt; padding: 4px 10px;")
    def _on_launch_twp():
        from ui.dialogs.training_wheels import TrainingWheelsDialog
        t_dlg = TrainingWheelsDialog(dialog)
        t_dlg.exec()
    launch_twp_btn.clicked.connect(_on_launch_twp)
    t_grid.addWidget(launch_twp_btn, 1, 2)

    # 3. Clear Build ID & Update Cache
    cache_lbl = QLabel("Update & Build ID Cache:")
    cache_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")
    t_grid.addWidget(cache_lbl, 2, 0)

    clear_cache_btn = QPushButton("Clear Cache")
    clear_cache_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    clear_cache_btn.setStyleSheet("font-size: 8.5pt; padding: 4px 10px;")
    clear_cache_btn.setToolTip("Purges cached build IDs, branch manifests, and update status entries.")
    def _on_clear_cache():
        try:
            from utils.update_status_cache import get_update_cache
            get_update_cache().clear_all()
        except Exception as e:
            logger.warning(f"Error clearing update_status_cache: {e}")
        try:
            from core.steam_api import clear_branch_cache
            clear_branch_cache()
        except Exception as e:
            logger.warning(f"Error clearing branch cache: {e}")
        for key in list(settings.allKeys()):
            if key.startswith("last_checked_") or key.startswith("installed_buildid/"):
                settings.remove(key)
        settings.sync()
        clear_cache_btn.setText("Cleared!")
        clear_cache_btn.setEnabled(False)
        QTimer.singleShot(2500, lambda: (
            clear_cache_btn.setText("Clear Cache"),
            clear_cache_btn.setEnabled(True)
        ))
        QMessageBox.information(
            dialog,
            "Cache Cleared",
            "Update status, build ID, and branch caches have been cleared successfully.\n\n"
            "Fresh live data will be queried next time you check for updates or open game details."
        )
    clear_cache_btn.clicked.connect(_on_clear_cache)
    t_grid.addWidget(clear_cache_btn, 2, 1)

    # 4. Lock Secret Menu & Seasonal Theme
    lock_lbl = QLabel("Secret Menu:")
    lock_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")
    t_grid.addWidget(lock_lbl, 3, 0)

    lock_menu_btn = QPushButton("Lock Testing Menu")
    lock_menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    lock_menu_btn.setStyleSheet("""
        QPushButton {
            background-color: rgba(244, 67, 54, 0.15);
            color: #EF5350;
            border: 1px solid rgba(239, 83, 80, 0.45);
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 8.5pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(244, 67, 54, 0.28);
        }
    """)
    def _on_lock_menu():
        settings.setValue("konami_settings_unlocked", False)
        settings.sync()
        QMessageBox.information(
            dialog,
            "Testing Menu Locked",
            "The secret Testing menu has been locked!\nIt will be hidden when Settings is reopened.\n(Enter the cheatcode again to unlock)"
        )
    lock_menu_btn.clicked.connect(_on_lock_menu)
    t_grid.addWidget(lock_menu_btn, 3, 1)

    reset_theme_btn = QPushButton("Reset Seasonal Theme")
    reset_theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reset_theme_btn.setStyleSheet("font-size: 8.5pt; padding: 4px 10px;")
    def _on_reset_theme():
        settings.setValue("halloween_auto_applied", False)
        settings.sync()
        QMessageBox.information(dialog, "Seasonal Theme", "Seasonal theme auto-activation flag reset.")
    reset_theme_btn.clicked.connect(_on_reset_theme)
    t_grid.addWidget(reset_theme_btn, 3, 2)

    # 5. Byparr Cloudflare Solver
    byparr_lbl = QLabel("Byparr Solver:")
    byparr_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")
    t_grid.addWidget(byparr_lbl, 4, 0)

    install_byparr_btn = QPushButton("Install Byparr (Cloudflare Solver)")
    install_byparr_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    install_byparr_btn.setToolTip("Downloads and runs setup_byparr.sh in a terminal to install Byparr solver.")
    install_byparr_btn.setStyleSheet("""
        QPushButton {
            background-color: rgba(33, 150, 243, 0.15);
            color: #64B5F6;
            border: 1px solid rgba(100, 181, 246, 0.45);
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 8.5pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(33, 150, 243, 0.28);
        }
    """)
    def _on_install_byparr():
        import os
        from ui.dialogs.settings_tabs.tools_tab import launch_terminal_command
        cmd = [
            "bash",
            "-c",
            "curl -sSL https://raw.githubusercontent.com/niwia/ASSella/c447a8a/scripts/setup_byparr.sh | bash; echo ''; echo 'Setup finished. Press Enter to close...'; read _"
        ]
        launch_terminal_command(cmd, os.path.expanduser("~"))
    install_byparr_btn.clicked.connect(_on_install_byparr)
    t_grid.addWidget(install_byparr_btn, 4, 1, 1, 2)

    test_layout.addLayout(t_grid)

    # 6. Testing Toggles: SteamDB Scraping & Demo Mode
    sep_line = QFrame()
    sep_line.setFrameShape(QFrame.Shape.HLine)
    sep_line.setStyleSheet("background-color: rgba(255, 255, 255, 0.08); margin: 6px 0;")
    test_layout.addWidget(sep_line)

    toggles_box = QVBoxLayout()
    toggles_box.setSpacing(6)

    disable_sdb_cb = QCheckBox("Disable SteamDB / Byparr Scraping")
    disable_sdb_cb.setChecked(settings.value("disable_steamdb_scraping", False, type=bool))
    disable_sdb_cb.setToolTip("Bypasses SteamDB scraping and Byparr solver during package inspection and patch queries.")
    disable_sdb_cb.setStyleSheet("""
        QCheckBox {
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 500;
            spacing: 8px;
        }
    """)
    def _on_toggle_sdb(checked):
        settings.setValue("disable_steamdb_scraping", checked)
        settings.sync()
    disable_sdb_cb.toggled.connect(_on_toggle_sdb)
    toggles_box.addWidget(disable_sdb_cb)

    demo_mode_cb = QCheckBox("Demo Mode (Disable local caching & downloads)")
    demo_mode_cb.setChecked(settings.value("demo_mode", False, type=bool))
    demo_mode_cb.setToolTip("Reaches the depot selection screen without caching LUA/keys locally or downloading files. Perfect for testing import workflows repeatedly.")
    demo_mode_cb.setStyleSheet("""
        QCheckBox {
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 500;
            spacing: 8px;
        }
    """)
    def _on_toggle_demo(checked):
        settings.setValue("demo_mode", checked)
        settings.sync()
    demo_mode_cb.toggled.connect(_on_toggle_demo)
    toggles_box.addWidget(demo_mode_cb)

    test_layout.addLayout(toggles_box)
    layout.addWidget(test_card)

    layout.addStretch()

    scroll.setWidget(container)
    outer_layout.addWidget(scroll)

    # The tab is named "Testing"
    dialog.tab_widget.addTab(tab, "Testing")
    return tab
