"""Plugins Settings tab for managing Katamari Family SLSsteam plugins.

Controls katamari.conf.lua (Mother Config) including:
  - SpaceBunny MRC provider hierarchy and racing
  - Styx ownership ticket splicing and SteamStub bypass
  - Lobster automatic config.yaml recovery
  - Cloudflare Worker proxy and abuse protection tokens

Note: Bakeneko settings are hidden / hashed out for testing and will be
re-introduced in a future production release.

Strictly NO emojis used anywhere.
"""

import logging
from pathlib import Path
from PyQt6.QtCore import Qt
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
    QScrollArea,
    QFrame,
    QCheckBox,
)

from managers.katamari_config_manager import (
    KatamariConfigManager,
    DEFAULT_KATAMARI_CONFIG,
)
from ui.dialogs.themed_message_box import ThemedMessageBox
from utils.settings import get_settings
from utils.yaml_config_manager import (
    ensure_plugins_enabled,
    ensure_smart_tickets_enabled,
)

logger = logging.getLogger(__name__)


def create_plugins_tab(dialog, add_to_dialog: bool = False) -> QWidget:
    """Create the Katamari Plugins settings tab with streamlined, user-friendly controls."""
    tab = QWidget()
    outer_layout = QVBoxLayout(tab)
    outer_layout.setContentsMargins(0, 0, 0, 0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(14, 12, 14, 20)
    layout.setSpacing(12)

    cfg = KatamariConfigManager.load_config()

    STYLE_LABEL = "color: #FFFFFF; font-size: 8.5pt; font-weight: 500;"
    STYLE_DESC = "color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;"
    STYLE_CHECKBOX = """
        QCheckBox {
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 500;
            spacing: 8px;
        }
    """
    STYLE_INPUT = """
        QLineEdit {
            background-color: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 5px 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
        }
        QLineEdit:focus {
            border-color: rgba(255, 255, 255, 0.4);
            background-color: rgba(255, 255, 255, 0.08);
        }
    """
    STYLE_COMBO = """
        QComboBox {
            background-color: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 4px 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
        }
    """
    STYLE_INACTIVE = """
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.22);
            border-radius: 6px;
            color: #FFFFFF;
            padding: 6px 14px;
            font-size: 8.5pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.16);
            border-color: rgba(255, 255, 255, 0.35);
        }
    """

    # -------------------------------------------------------------------------
    # Card 1: SpaceBunny MRC Provider Configuration
    # -------------------------------------------------------------------------
    sb_card, sb_layout = dialog._create_card_frame("SpaceBunny MRC Provider Configuration")
    sb_layout.setSpacing(8)

    sb_desc = QLabel(
        "SpaceBunny intercepts native Steam downloads and dynamically resolves Manifest Request Codes (MRC) "
        "using configured authority and rescue providers."
    )
    sb_desc.setStyleSheet(STYLE_DESC)
    sb_desc.setWordWrap(True)
    sb_layout.addWidget(sb_desc)

    sb_cfg = cfg.get("spacebunny", {})

    sb_enable_cb = QCheckBox("Enable Manifest Request Code (MRC) Resolution")
    sb_enable_cb.setChecked(sb_cfg.get("mrc_enabled", True))
    sb_enable_cb.setStyleSheet(STYLE_CHECKBOX)
    sb_layout.addWidget(sb_enable_cb)

    sb_grid = QGridLayout()
    sb_grid.setHorizontalSpacing(14)
    sb_grid.setVerticalSpacing(8)

    auth_lbl = QLabel("Primary Authority:")
    auth_lbl.setStyleSheet(STYLE_LABEL)
    mrc_auth_combo = QComboBox()
    mrc_auth_combo.setStyleSheet(STYLE_COMBO)
    mrc_auth_combo.setFixedWidth(200)
    mrc_auth_combo.addItem("ManifestDeX (Primary)", "manifestdex")
    mrc_auth_combo.addItem("Wudrm", "wudrm")
    idx_auth = mrc_auth_combo.findData(sb_cfg.get("authority", "manifestdex"))
    if idx_auth >= 0:
        mrc_auth_combo.setCurrentIndex(idx_auth)

    sb_grid.addWidget(auth_lbl, 0, 0)
    sb_grid.addWidget(mrc_auth_combo, 0, 1)

    rescue_lbl = QLabel("Secondary Rescue:")
    rescue_lbl.setStyleSheet(STYLE_LABEL)
    mrc_rescue_combo = QComboBox()
    mrc_rescue_combo.setStyleSheet(STYLE_COMBO)
    mrc_rescue_combo.setFixedWidth(200)
    mrc_rescue_combo.addItem("Wudrm (Default)", "wudrm")
    mrc_rescue_combo.addItem("ManifestDeX", "manifestdex")
    mrc_rescue_combo.addItem("None (Disabled)", "none")
    idx_rescue = mrc_rescue_combo.findData(sb_cfg.get("rescue", "wudrm"))
    if idx_rescue >= 0:
        mrc_rescue_combo.setCurrentIndex(idx_rescue)

    sb_grid.addWidget(rescue_lbl, 1, 0)
    sb_grid.addWidget(mrc_rescue_combo, 1, 1)

    # Empty column to keep grid neatly left-aligned
    sb_grid.setColumnStretch(2, 1)
    sb_layout.addLayout(sb_grid)
    layout.addWidget(sb_card)

    # -------------------------------------------------------------------------
    # [Hashed Out / In Testing: Bakeneko Manifest Pre-Seeding Card]
    # Bakeneko is currently disabled by default and will be enabled in a future release.
    # -------------------------------------------------------------------------
    # manifest_card, manifest_layout = dialog._create_card_frame("Manifest Delivery & Resolution (family / bakeneko)")
    # ... (Bakeneko settings hidden from UI while in testing)

    # -------------------------------------------------------------------------
    # Card 2: Styx Ownership Ticket Splicing
    # -------------------------------------------------------------------------
    styx_card, styx_layout = dialog._create_card_frame("Styx Ownership Ticket Splicer")
    styx_layout.setSpacing(8)

    styx_desc = QLabel(
        "Styx splices valid spacewar (480) ownership tickets into requested AppIDs, "
        "allowing token-only and unowned apps to satisfy SteamStub DRM."
    )
    styx_desc.setStyleSheet(STYLE_DESC)
    styx_desc.setWordWrap(True)
    styx_layout.addWidget(styx_desc)

    sty_cfg = cfg.get("styx", {})

    styx_enable_cb = QCheckBox("Enable Styx Ownership Ticket Splicing")
    styx_enable_cb.setChecked(sty_cfg.get("enabled", True))
    styx_enable_cb.setStyleSheet(STYLE_CHECKBOX)

    def _on_styx_toggled(checked: bool):
        if checked:
            ThemedMessageBox.warning(
                dialog,
                "Warning",
                "Bypassing DRM is illegal in many countries, make sure you are aware of this!",
            )
            ensure_plugins_enabled()
            ensure_smart_tickets_enabled()

    styx_enable_cb.toggled.connect(_on_styx_toggled)
    styx_layout.addWidget(styx_enable_cb)

    sty_grid = QGridLayout()
    sty_grid.setHorizontalSpacing(14)
    sty_grid.setVerticalSpacing(8)

    sm_lbl = QLabel("Splice Mode:")
    sm_lbl.setStyleSheet(STYLE_LABEL)
    styx_mode_combo = QComboBox()
    styx_mode_combo.setStyleSheet(STYLE_COMBO)
    styx_mode_combo.setFixedWidth(200)
    styx_mode_combo.addItem("Insert (Recommended)", "insert")
    styx_mode_combo.addItem("Overwrite", "overwrite")
    idx_sm = styx_mode_combo.findData(sty_cfg.get("splice_mode", "insert"))
    if idx_sm >= 0:
        styx_mode_combo.setCurrentIndex(idx_sm)
    sty_grid.addWidget(sm_lbl, 0, 0)
    sty_grid.addWidget(styx_mode_combo, 0, 1)

    fb_lbl = QLabel("Fallback AppID:")
    fb_lbl.setStyleSheet(STYLE_LABEL)
    styx_fb_spin = QSpinBox()
    styx_fb_spin.setStyleSheet("""
        QSpinBox {
            background-color: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 4px 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
        }
    """)
    styx_fb_spin.setRange(1, 99999999)
    styx_fb_spin.setValue(int(sty_cfg.get("fallback_app", 480)))
    styx_fb_spin.setFixedWidth(120)
    sty_grid.addWidget(fb_lbl, 1, 0)
    sty_grid.addWidget(styx_fb_spin, 1, 1)

    sty_grid.setColumnStretch(2, 1)
    styx_layout.addLayout(sty_grid)
    layout.addWidget(styx_card)

    # -------------------------------------------------------------------------
    # Card 3: Lobster Config Recovery
    # -------------------------------------------------------------------------
    lob_card, lob_layout = dialog._create_card_frame("Lobster Config Recovery")
    lob_layout.setSpacing(8)

    lob_desc = QLabel(
        "Lobster auto-recovers missing AdditionalDepots and DecryptionKeys in config.yaml "
        "if Headcrab or native updates strip them."
    )
    lob_desc.setStyleSheet(STYLE_DESC)
    lob_desc.setWordWrap(True)
    lob_layout.addWidget(lob_desc)

    lob_cfg = cfg.get("lobster", {})

    lob_enable_cb = QCheckBox("Enable Lobster Config Recovery")
    lob_enable_cb.setChecked(lob_cfg.get("enabled", True))
    lob_enable_cb.setStyleSheet(STYLE_CHECKBOX)
    lob_layout.addWidget(lob_enable_cb)

    lob_notify_cb = QCheckBox("Show Desktop Notifications on Config Recovery")
    lob_notify_cb.setChecked(lob_cfg.get("notify", True))
    lob_notify_cb.setStyleSheet(STYLE_CHECKBOX)
    lob_layout.addWidget(lob_notify_cb)

    layout.addWidget(lob_card)

    # -------------------------------------------------------------------------
    # Card 4: Proxy & Cloudflare Worker Configuration
    # -------------------------------------------------------------------------
    proxy_card, proxy_layout = dialog._create_card_frame("Cloudflare Worker & Abuse Protection")
    proxy_layout.setSpacing(8)

    proxy_desc = QLabel(
        "Configure custom Cloudflare Worker endpoints for Hubcap fallback and ISP bypass. "
        "Includes token-bucket abuse protection (X-Assella-Token)."
    )
    proxy_desc.setStyleSheet(STYLE_DESC)
    proxy_desc.setWordWrap(True)
    proxy_layout.addWidget(proxy_desc)

    prx_cfg = cfg.get("proxy", {})
    p_grid = QGridLayout()
    p_grid.setHorizontalSpacing(14)
    p_grid.setVerticalSpacing(8)

    wurl_lbl = QLabel("Worker /hubcap URL:")
    wurl_lbl.setStyleSheet(STYLE_LABEL)
    proxy_wurl_input = QLineEdit()
    proxy_wurl_input.setStyleSheet(STYLE_INPUT)
    proxy_wurl_input.setPlaceholderText("https://...workers.dev/hubcap")
    proxy_wurl_input.setText(prx_cfg.get("worker_url", ""))
    proxy_wurl_input.setMaximumWidth(320)
    p_grid.addWidget(wurl_lbl, 0, 0)
    p_grid.addWidget(proxy_wurl_input, 0, 1)

    wtok_lbl = QLabel("ASSella Token:")
    wtok_lbl.setStyleSheet(STYLE_LABEL)
    proxy_token_input = QLineEdit()
    proxy_token_input.setStyleSheet(STYLE_INPUT)
    proxy_token_input.setPlaceholderText("Optional custom token override")
    proxy_token_input.setText(prx_cfg.get("worker_token", ""))
    proxy_token_input.setMaximumWidth(320)
    p_grid.addWidget(wtok_lbl, 1, 0)
    p_grid.addWidget(proxy_token_input, 1, 1)

    pmode_lbl = QLabel("Proxy Mode:")
    pmode_lbl.setStyleSheet(STYLE_LABEL)
    proxy_mode_combo = QComboBox()
    proxy_mode_combo.setStyleSheet(STYLE_COMBO)
    proxy_mode_combo.setFixedWidth(200)
    proxy_mode_combo.addItem("Fallback (Direct first)", "fallback")
    proxy_mode_combo.addItem("Always (Worker first)", "always")
    proxy_mode_combo.addItem("Never (Direct only)", "never")
    idx_pm = proxy_mode_combo.findData(prx_cfg.get("mode", "fallback"))
    if idx_pm >= 0:
        proxy_mode_combo.setCurrentIndex(idx_pm)
    p_grid.addWidget(pmode_lbl, 2, 0)
    p_grid.addWidget(proxy_mode_combo, 2, 1)

    p_grid.setColumnStretch(2, 1)
    proxy_layout.addLayout(p_grid)
    layout.addWidget(proxy_card)

    # -------------------------------------------------------------------------
    # Bottom Left Action Bar (Save Changes & Restore Defaults)
    # -------------------------------------------------------------------------
    actions_row = QHBoxLayout()
    actions_row.setContentsMargins(0, 8, 0, 0)
    actions_row.setSpacing(10)

    save_btn = QPushButton("Save Changes")
    save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    save_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {getattr(dialog, 'accent_color', '#ff7518')};
            color: #000000;
            border: 1px solid {getattr(dialog, 'accent_color', '#ff7518')};
            border-radius: 6px;
            padding: 7px 20px;
            font-size: 9pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: #FFFFFF;
            color: #000000;
        }}
        QPushButton:pressed {{
            background-color: rgba(255, 255, 255, 0.85);
        }}
    """)

    reset_btn = QPushButton("Restore Defaults")
    reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reset_btn.setStyleSheet(STYLE_INACTIVE)

    status_lbl = QLabel("")
    status_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: 500;")

    def _on_save_clicked():
        current_cfg = KatamariConfigManager.load_config()
        save_cfg = {
            "family": {
                "manifest_mode": current_cfg.get("family", {}).get("manifest_mode", "auto"),
            },
            "proxy": {
                "worker_url": proxy_wurl_input.text().strip(),
                "worker_token": proxy_token_input.text().strip(),
                "mode": proxy_mode_combo.currentData(),
            },
            # Bakeneko is preserved as-is from current config
            "bakeneko": current_cfg.get("bakeneko", {
                "enabled": True,
                "mode": "before",
                "auto_pending": False,
                "dry_run": True,
                "max_manifests": 32,
            }),
            "spacebunny": {
                "mrc_enabled": sb_enable_cb.isChecked(),
                "authority": mrc_auth_combo.currentData(),
                "rescue": mrc_rescue_combo.currentData(),
                "full_timeout_ms": current_cfg.get("spacebunny", {}).get("full_timeout_ms", 2000),
                "probe_timeout_ms": current_cfg.get("spacebunny", {}).get("probe_timeout_ms", 700),
            },
            "styx": {
                "enabled": styx_enable_cb.isChecked(),
                "fallback_app": styx_fb_spin.value(),
                "splice_mode": styx_mode_combo.currentData(),
            },
            "lobster": {
                "enabled": lob_enable_cb.isChecked(),
                "auto_recover": lob_enable_cb.isChecked(),
                "notify": lob_notify_cb.isChecked(),
            },
        }

        ok = KatamariConfigManager.save_config(save_cfg)
        if ok:
            status_lbl.setText("✓ Changes saved (restart Steam to apply)")
            status_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: 600;")
        else:
            status_lbl.setText("⚠ Failed to save katamari.conf.lua")
            status_lbl.setStyleSheet("color: #EF5350; font-size: 8.5pt; font-weight: 600;")

    def _on_reset_clicked():
        # Reset UI controls to defaults
        d = DEFAULT_KATAMARI_CONFIG

        sb_enable_cb.setChecked(d["spacebunny"].get("mrc_enabled", True))
        idx_auth = mrc_auth_combo.findData(d["spacebunny"]["authority"])
        if idx_auth >= 0:
            mrc_auth_combo.setCurrentIndex(idx_auth)
        idx_resc = mrc_rescue_combo.findData(d["spacebunny"]["rescue"])
        if idx_resc >= 0:
            mrc_rescue_combo.setCurrentIndex(idx_resc)

        styx_enable_cb.setChecked(d["styx"]["enabled"])
        styx_fb_spin.setValue(d["styx"]["fallback_app"])
        idx_sm = styx_mode_combo.findData(d["styx"]["splice_mode"])
        if idx_sm >= 0:
            styx_mode_combo.setCurrentIndex(idx_sm)

        lob_enable_cb.setChecked(d["lobster"]["enabled"])
        lob_notify_cb.setChecked(d["lobster"]["notify"])

        proxy_wurl_input.setText("")
        proxy_token_input.setText("")
        proxy_mode_combo.setCurrentIndex(0)

        status_lbl.setText("Defaults restored (click Save to apply).")
        status_lbl.setStyleSheet("color: #FFD54F; font-size: 8.5pt; font-weight: 500;")

    save_btn.clicked.connect(_on_save_clicked)
    reset_btn.clicked.connect(_on_reset_clicked)

    actions_row.addWidget(save_btn)
    actions_row.addWidget(reset_btn)
    actions_row.addWidget(status_lbl)
    actions_row.addStretch()

    layout.addLayout(actions_row)
    layout.addStretch()

    scroll.setWidget(container)
    outer_layout.addWidget(scroll)

    if add_to_dialog and hasattr(dialog, "tab_widget") and dialog.tab_widget is not None:
        dialog.tab_widget.addTab(tab, "Plugins")
    return tab
