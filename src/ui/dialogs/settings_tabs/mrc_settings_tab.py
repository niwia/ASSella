"""Modular Testing tab for controlling MRC providers, race parameters, and developer utilities.

Accessible when unlocked via the Konami cheat code (Testing).
Manages provider hierarchy, fallback rescue order, race/timeout parameters,
synchronizes configuration to ~/.config/SLSsteam/mrc_config.lua,
and provides developer toggles to test one-time dialogs (Welcome screen, TWP, lock menu).
"""

import logging
import threading
from PyQt6.QtCore import Qt, QTimer
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
    layout.addWidget(p_card)

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
    layout.addWidget(t_card)

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
    layout.addLayout(actions_row)

    settings = get_settings()

    # -- 4. Custom Proxy & Wirecutter Overrides Card --
    proxy_card, proxy_layout = dialog._create_card_frame("Custom Proxy & Wirecutter Overrides")
    proxy_desc = QLabel(
        "Configure custom HTTP/SOCKS proxy or override the Wirecutter Cloudflare Worker endpoint for Hubcap requests."
    )
    proxy_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    proxy_desc.setWordWrap(True)
    proxy_layout.addWidget(proxy_desc)

    p_form = QGridLayout()
    p_form.setHorizontalSpacing(8)
    p_form.setVerticalSpacing(6)

    # Custom Proxy URL
    proxy_lbl = QLabel("Custom Proxy:")
    proxy_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")
    dialog.custom_proxy_input = QLineEdit()
    dialog.custom_proxy_input.setPlaceholderText("http://127.0.0.1:7890 or socks5://...")
    dialog.custom_proxy_input.setText(settings.value("custom_proxy_url", "", type=str))
    dialog.custom_proxy_input.textChanged.connect(
        lambda txt: (settings.setValue("custom_proxy_url", txt.strip()), settings.sync())
    )

    test_proxy_btn = QPushButton("Test Proxy")
    test_proxy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    test_proxy_btn.setStyleSheet("font-size: 8.5pt; padding: 3px 8px;")

    proxy_res_lbl = QLabel("")
    proxy_res_lbl.setStyleSheet("font-size: 8pt; color: #81C784;")

    def _test_custom_proxy():
        p_val = dialog.custom_proxy_input.text().strip()
        if not p_val:
            proxy_res_lbl.setText("Enter proxy URL first")
            proxy_res_lbl.setStyleSheet("color: #FFB74D; font-size: 8pt;")
            return
        test_proxy_btn.setEnabled(False)
        test_proxy_btn.setText("Testing...")
        def _bg():
            from utils.isp_bypass import test_gateway_proxy
            ok, msg, lat = test_gateway_proxy(p_val)
            def _ui():
                test_proxy_btn.setEnabled(True)
                test_proxy_btn.setText("Test Proxy")
                color = "#81C784" if ok else "#EF5350"
                proxy_res_lbl.setText(f"{msg}")
                proxy_res_lbl.setStyleSheet(f"color: {color}; font-size: 8pt; font-weight: bold;")
            QTimer.singleShot(0, _ui)
        threading.Thread(target=_bg, daemon=True).start()

    test_proxy_btn.clicked.connect(_test_custom_proxy)

    p_form.addWidget(proxy_lbl, 0, 0)
    p_form.addWidget(dialog.custom_proxy_input, 0, 1)
    p_form.addWidget(test_proxy_btn, 0, 2)
    p_form.addWidget(proxy_res_lbl, 0, 3)

    # Wirecutter URL Override
    wc_lbl = QLabel("Wirecutter Worker:")
    wc_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")
    dialog.wirecutter_url_input = QLineEdit()
    dialog.wirecutter_url_input.setPlaceholderText("https://<worker-name>.workers.dev")
    dialog.wirecutter_url_input.setText(settings.value("wirecutter_url", "", type=str))
    dialog.wirecutter_url_input.textChanged.connect(
        lambda txt: (settings.setValue("wirecutter_url", txt.strip()), settings.sync())
    )

    test_wc_btn = QPushButton("Test Worker")
    test_wc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    test_wc_btn.setStyleSheet("font-size: 8.5pt; padding: 3px 8px;")

    wc_res_lbl = QLabel("")
    wc_res_lbl.setStyleSheet("font-size: 8pt; color: #81C784;")

    def _test_wc():
        test_wc_btn.setEnabled(False)
        test_wc_btn.setText("Testing...")
        def _bg():
            from utils.isp_bypass import test_gateway_wirecutter
            ok, msg, lat = test_gateway_wirecutter()
            def _ui():
                test_wc_btn.setEnabled(True)
                test_wc_btn.setText("Test Worker")
                color = "#81C784" if ok else "#EF5350"
                wc_res_lbl.setText(f"{msg}")
                wc_res_lbl.setStyleSheet(f"color: {color}; font-size: 8pt; font-weight: bold;")
            QTimer.singleShot(0, _ui)
        threading.Thread(target=_bg, daemon=True).start()

    test_wc_btn.clicked.connect(_test_wc)

    p_form.addWidget(wc_lbl, 1, 0)
    p_form.addWidget(dialog.wirecutter_url_input, 1, 1)
    p_form.addWidget(test_wc_btn, 1, 2)
    p_form.addWidget(wc_res_lbl, 1, 3)

    proxy_layout.addLayout(p_form)
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

    test_layout.addLayout(t_grid)
    layout.addWidget(test_card)

    layout.addStretch()

    scroll.setWidget(container)
    outer_layout.addWidget(scroll)

    # The tab is named "Testing"
    dialog.tab_widget.addTab(tab, "Testing")
    return tab
