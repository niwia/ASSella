"""Modular Testing tab for controlling MRC providers and parameters in Lua.

Accessible when unlocked via the Konami cheat code (Testing).
Manages provider hierarchy, fallback rescue order, race/timeout parameters,
and synchronizes configuration to ~/.config/SLSsteam/mrc_config.lua.
"""

import logging
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QComboBox,
    QSpinBox,
    QPushButton,
    QMessageBox,
)

from managers.mrc_config_manager import MRCConfigManager

logger = logging.getLogger(__name__)


def create_mrc_settings_tab(dialog) -> QWidget:
    """Create the modular MRC Testing tab."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
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

    layout.addStretch()

    # The tab is named "Testing"
    dialog.tab_widget.addTab(tab, "Testing")
    return tab
