"""Plugins Settings tab for managing Katamari Family SLSsteam plugins.

Controls katamari.conf.lua (Mother Config) including:
  - Plugin deployment (Katamari, SpaceBunny, Bakeneko, Styx, Lobster)
  - Manifest delivery mode arbitration (Auto, Bakeneko-first, SpaceBunny-only, Bakeneko-only)
  - MRC Provider Hierarchy and tuning parameters
  - Styx ticket splicing and SteamStub bypass
  - Lobster config recovery
  - Cloudflare Worker proxy and abuse protection tokens

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

from managers.katamari_config_manager import KatamariConfigManager
from ui.dialogs.themed_message_box import ThemedMessageBox
from utils.plugin_manager import (
    archive_non_katamari_plugins,
    deploy_plugin,
    get_plugin_status,
    get_plugin_version,
    is_plugin_check_bypassed,
    KATAMARI_FAMILY_PLUGINS,
)
from utils.settings import get_settings
from utils.yaml_config_manager import (
    ensure_plugins_enabled,
    ensure_smart_tickets_enabled,
)

logger = logging.getLogger(__name__)


def create_plugins_tab(dialog) -> QWidget:
    """Create the Katamari Plugins settings tab."""
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

    cfg = KatamariConfigManager.load_config()
    settings = get_settings()

    STYLE_ACTIVE_GREEN = """
        QPushButton {
            background-color: rgba(76, 175, 80, 0.2);
            color: #81C784;
            border: 1px solid rgba(129, 199, 132, 0.5);
            border-radius: 6px;
            padding: 6px 12px;
            font-size: 8.5pt;
            font-weight: 600;
        }
    """
    STYLE_ATTENTION_ORANGE = """
        QPushButton {
            background-color: rgba(255, 171, 0, 0.18);
            color: #FFD54F;
            border: 1px solid rgba(255, 171, 0, 0.55);
            border-radius: 6px;
            padding: 6px 12px;
            font-size: 8.5pt;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: rgba(255, 171, 0, 0.32);
            border-color: rgba(255, 171, 0, 0.85);
            color: #FFFFFF;
        }
    """
    STYLE_INACTIVE = """
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.22);
            border-radius: 6px;
            color: #FFFFFF;
            padding: 6px 12px;
            font-size: 8.5pt;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.16);
            border-color: rgba(255, 255, 255, 0.35);
        }
    """

    # -- 1. Family Status & Deployment Card --
    deploy_card, deploy_layout = dialog._create_card_frame("Katamari Plugin Suite")
    deploy_desc = QLabel(
        "Manage the Katamari Lua plugin family in ~/.config/SLSsteam/plugins/. "
        "Katamari coordinates manifest resolution, ownership ticket splicing, and config recovery."
    )
    deploy_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    deploy_desc.setWordWrap(True)
    deploy_layout.addWidget(deploy_desc)

    tiles_layout = QGridLayout()
    tiles_layout.setSpacing(6)

    plugin_tiles = {}
    family_members = [
        ("katamari.lua", "Katamari (Coordinator)"),
        ("spacebunny.lua", "SpaceBunny (MRC Interceptor)"),
        ("bakeneko.lua", "Bakeneko (Manifest Pre-Seeder)"),
        ("styx.lua", "Styx (Ticket Splicer)"),
        ("lobster.lua", "Lobster (Config Recovery)"),
    ]

    for idx, (p_file, p_name) in enumerate(family_members):
        btn = QPushButton(p_name)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plugin_tiles[p_file] = btn
        tiles_layout.addWidget(btn, idx // 2, idx % 2)

    deploy_layout.addLayout(tiles_layout)

    # Actions: Deploy Suite, Clean Non-Family, Custom Plugins checkbox
    deploy_actions = QHBoxLayout()
    deploy_actions.setSpacing(8)

    btn_sync_suite = QPushButton("Deploy / Update Katamari Suite")
    btn_sync_suite.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_sync_suite.setStyleSheet(f"""
        QPushButton {{
            background-color: {getattr(dialog, 'accent_color', '#ff7518')};
            color: #000000;
            border: 1px solid {getattr(dialog, 'accent_color', '#ff7518')};
            border-radius: 6px;
            padding: 6px 14px;
            font-size: 8.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: #FFFFFF;
            color: #000000;
        }}
    """)

    btn_clean_foreign = QPushButton("Clean Non-Family Plugins")
    btn_clean_foreign.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_clean_foreign.setStyleSheet(STYLE_INACTIVE)
    btn_clean_foreign.setToolTip("Deactivates any legacy or non-Katamari plugins by renaming them to .bak.")

    deploy_actions.addWidget(btn_sync_suite)
    deploy_actions.addWidget(btn_clean_foreign)
    deploy_actions.addStretch()
    deploy_layout.addLayout(deploy_actions)

    custom_plugins_cb = QCheckBox("I'm using custom plugins (Advanced)")
    custom_plugins_cb.setChecked(settings.value("custom_plugins_advanced", False, type=bool))
    custom_plugins_cb.setToolTip(
        "Bypasses plugin missing checks for game transfers and native downloads. "
        "Disables automatic hygiene and deactivation of third-party plugins."
    )
    custom_plugins_cb.setStyleSheet("""
        QCheckBox {
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: 500;
            spacing: 8px;
            margin-top: 4px;
        }
    """)

    def _on_custom_toggled(checked: bool):
        settings.setValue("custom_plugins_advanced", checked)
        settings.sync()
        if checked:
            ThemedMessageBox.warning(
                dialog,
                "Custom Plugins (Advanced)",
                "Bypassing plugin presence verification enables native downloads and game transfers with custom or third-party Lua plugins.\n\n"
                "Please note: ASSella cannot guarantee compatibility or stability with third-party plugins!"
            )
        _refresh_tiles()

    custom_plugins_cb.toggled.connect(_on_custom_toggled)
    deploy_layout.addWidget(custom_plugins_cb)

    layout.addWidget(deploy_card)

    def _refresh_tiles():
        for p_file, btn in plugin_tiles.items():
            st = get_plugin_status(p_file)
            ver = get_plugin_version(p_file) or ""
            base_title = p_file.replace(".lua", "").capitalize()
            if p_file == "spacebunny.lua": base_title = "SpaceBunny"
            elif p_file == "styx.lua": base_title = "Styx"
            elif p_file == "katamari.lua": base_title = "Katamari"
            elif p_file == "bakeneko.lua": base_title = "Bakeneko"
            elif p_file == "lobster.lua": base_title = "Lobster"

            if st == "up_to_date":
                btn.setText(f"{base_title} v{ver}" if ver else base_title)
                btn.setStyleSheet(STYLE_ACTIVE_GREEN)
                btn.setToolTip(f"{p_file} is active and verified.")
            elif st == "missing":
                btn.setText(f"{base_title} (Missing)")
                btn.setStyleSheet(STYLE_ATTENTION_ORANGE)
                btn.setToolTip(f"{p_file} is missing. Click to deploy.")
            else:
                btn.setText(f"{base_title} (Update Available)")
                btn.setStyleSheet(STYLE_ATTENTION_ORANGE)
                btn.setToolTip(f"{p_file} has an update available.")

    def _deploy_single(p_file: str):
        ok, skipped, msg = deploy_plugin(p_file)
        _refresh_tiles()
        if ok:
            ThemedMessageBox.information(dialog, "Plugin Deployed", f"{p_file} deployed successfully.")
        else:
            ThemedMessageBox.warning(dialog, "Deploy Failed", f"Failed to deploy {p_file}:\n{msg}")

    for p_file, btn in plugin_tiles.items():
        btn.clicked.connect(lambda _, pf=p_file: _deploy_single(pf))

    def _on_sync_suite():
        results = []
        for p_file, _ in family_members:
            ok, skipped, msg = deploy_plugin(p_file)
            results.append((p_file, ok, msg))
        archive_non_katamari_plugins()
        _refresh_tiles()
        ThemedMessageBox.information(
            dialog,
            "Katamari Suite Deployed",
            "The Katamari plugin suite has been deployed and synchronized.\n\n"
            "Restart Steam for changes to take effect."
        )

    btn_sync_suite.clicked.connect(_on_sync_suite)

    def _on_clean_foreign():
        archived = archive_non_katamari_plugins()
        _refresh_tiles()
        if archived:
            ThemedMessageBox.information(
                dialog,
                "Plugins Cleaned",
                f"The following foreign or legacy plugins were safely deactivated:\n\n"
                + "\n".join(f"• {a}" for a in archived)
            )
        else:
            ThemedMessageBox.information(
                dialog,
                "Clean",
                "No foreign or legacy plugins were detected in ~/.config/SLSsteam/plugins/."
            )

    btn_clean_foreign.clicked.connect(_on_clean_foreign)

    # -- 2. Manifest Delivery Mode Card --
    manifest_card, manifest_layout = dialog._create_card_frame("Manifest Delivery & Resolution (family / bakeneko)")
    manifest_desc = QLabel(
        "Katamari arbitrates how manifest files and request codes are resolved. "
        "Bakeneko seeds manifest files directly into depotcache/, while SpaceBunny resolves MRCs dynamically at runtime."
    )
    manifest_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    manifest_desc.setWordWrap(True)
    manifest_layout.addWidget(manifest_desc)

    m_grid = QGridLayout()
    m_grid.setHorizontalSpacing(12)
    m_grid.setVerticalSpacing(6)

    lbl_mode = QLabel("Manifest Delivery Mode:")
    lbl_mode.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500;")
    mode_combo = QComboBox()
    mode_combo.setFixedWidth(240)
    mode_combo.addItem("Auto (Derive from Bakeneko & MRC)", "auto")
    mode_combo.addItem("Bakeneko First (Pre-seed, MRC fallback)", "bakeneko-first")
    mode_combo.addItem("SpaceBunny Only (Runtime MRC only)", "spacebunny-only")
    mode_combo.addItem("Bakeneko Only (Pre-seed only, no MRC)", "bakeneko-only")

    cur_fam_mode = cfg.get("family", {}).get("manifest_mode", "auto")
    idx = mode_combo.findData(cur_fam_mode)
    if idx >= 0:
        mode_combo.setCurrentIndex(idx)
    m_grid.addWidget(lbl_mode, 0, 0)
    m_grid.addWidget(mode_combo, 0, 1)

    lbl_bak_mode = QLabel("Bakeneko Mode:")
    lbl_bak_mode.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500;")
    bak_mode_combo = QComboBox()
    bak_mode_combo.setFixedWidth(240)
    bak_mode_combo.addItem("Before (Seed before download)", "before")
    bak_mode_combo.addItem("Only (Seed only, disable MRC)", "only")
    bak_mode_combo.addItem("Off (Disabled)", "off")

    cur_bak_mode = cfg.get("bakeneko", {}).get("mode", "before")
    idx2 = bak_mode_combo.findData(cur_bak_mode)
    if idx2 >= 0:
        bak_mode_combo.setCurrentIndex(idx2)
    m_grid.addWidget(lbl_bak_mode, 1, 0)
    m_grid.addWidget(bak_mode_combo, 1, 1)

    manifest_layout.addLayout(m_grid)

    bak_toggles_layout = QHBoxLayout()
    bak_toggles_layout.setSpacing(16)

    bak_autopending_cb = QCheckBox("Auto-seed updates (StateFlags pending)")
    bak_autopending_cb.setChecked(cfg.get("bakeneko", {}).get("auto_pending", False))
    bak_autopending_cb.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    bak_toggles_layout.addWidget(bak_autopending_cb)

    bak_dryrun_cb = QCheckBox("Bakeneko Dry Run (Inspect without writing)")
    bak_dryrun_cb.setChecked(cfg.get("bakeneko", {}).get("dry_run", True))
    bak_dryrun_cb.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    bak_toggles_layout.addWidget(bak_dryrun_cb)
    bak_toggles_layout.addStretch()

    manifest_layout.addLayout(bak_toggles_layout)
    layout.addWidget(manifest_card)

    # -- 3. SpaceBunny MRC Provider Hierarchy Card --
    mrc_card, mrc_layout = dialog._create_card_frame("SpaceBunny MRC Provider Racing")
    mrc_desc = QLabel(
        "Define primary authority and secondary rescue providers when SpaceBunny resolves "
        "Manifest Request Codes (MRC) via Steam network interception."
    )
    mrc_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    mrc_desc.setWordWrap(True)
    mrc_layout.addWidget(mrc_desc)

    sb_cfg = cfg.get("spacebunny", {})
    sb_grid = QGridLayout()
    sb_grid.setHorizontalSpacing(12)
    sb_grid.setVerticalSpacing(6)

    auth_lbl = QLabel("Primary Authority:")
    auth_lbl.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500;")
    mrc_auth_combo = QComboBox()
    mrc_auth_combo.setFixedWidth(200)
    mrc_auth_combo.addItem("ManifestDeX (Primary)", "manifestdex")
    mrc_auth_combo.addItem("Wudrm", "wudrm")
    idx = mrc_auth_combo.findData(sb_cfg.get("authority", "manifestdex"))
    if idx >= 0:
        mrc_auth_combo.setCurrentIndex(idx)
    sb_grid.addWidget(auth_lbl, 0, 0)
    sb_grid.addWidget(mrc_auth_combo, 0, 1)

    rescue_lbl = QLabel("Secondary Rescue:")
    rescue_lbl.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500;")
    mrc_rescue_combo = QComboBox()
    mrc_rescue_combo.setFixedWidth(200)
    mrc_rescue_combo.addItem("Wudrm", "wudrm")
    mrc_rescue_combo.addItem("ManifestDeX", "manifestdex")
    idx = mrc_rescue_combo.findData(sb_cfg.get("rescue", "wudrm"))
    if idx >= 0:
        mrc_rescue_combo.setCurrentIndex(idx)
    sb_grid.addWidget(rescue_lbl, 0, 2)
    sb_grid.addWidget(mrc_rescue_combo, 0, 3)

    full_lbl = QLabel("Authority Budget (ms):")
    full_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    mrc_full_spin = QSpinBox()
    mrc_full_spin.setRange(500, 10000)
    mrc_full_spin.setSingleStep(100)
    mrc_full_spin.setValue(int(sb_cfg.get("full_timeout_ms", 2000)))
    mrc_full_spin.setFixedWidth(85)
    sb_grid.addWidget(full_lbl, 1, 0)
    sb_grid.addWidget(mrc_full_spin, 1, 1)

    probe_lbl = QLabel("Probe Timeout (ms):")
    probe_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    mrc_probe_spin = QSpinBox()
    mrc_probe_spin.setRange(200, 3000)
    mrc_probe_spin.setSingleStep(50)
    mrc_probe_spin.setValue(int(sb_cfg.get("probe_timeout_ms", 700)))
    mrc_probe_spin.setFixedWidth(85)
    sb_grid.addWidget(probe_lbl, 1, 2)
    sb_grid.addWidget(mrc_probe_spin, 1, 3)

    mrc_layout.addLayout(sb_grid)
    layout.addWidget(mrc_card)

    # -- 4. Styx Ownership Ticket Splicing Card --
    styx_card, styx_layout = dialog._create_card_frame("Styx Ownership Ticket Splicer")
    styx_desc = QLabel(
        "Styx splices valid spacewar (480) ownership tickets into requested AppIDs, "
        "allowing token-only and unowned apps to satisfy SteamStub DRM."
    )
    styx_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    styx_desc.setWordWrap(True)
    styx_layout.addWidget(styx_desc)

    sty_cfg = cfg.get("styx", {})
    sty_grid = QGridLayout()
    sty_grid.setHorizontalSpacing(12)
    sty_grid.setVerticalSpacing(6)

    styx_enable_cb = QCheckBox("Enable Styx Ticket Splicing")
    styx_enable_cb.setChecked(sty_cfg.get("enabled", True))
    styx_enable_cb.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500;")

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
    sty_grid.addWidget(styx_enable_cb, 0, 0, 1, 2)

    fb_lbl = QLabel("Fallback AppID:")
    fb_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    styx_fb_spin = QSpinBox()
    styx_fb_spin.setRange(1, 99999999)
    styx_fb_spin.setValue(int(sty_cfg.get("fallback_app", 480)))
    styx_fb_spin.setFixedWidth(100)
    sty_grid.addWidget(fb_lbl, 1, 0)
    sty_grid.addWidget(styx_fb_spin, 1, 1)

    sm_lbl = QLabel("Splice Mode:")
    sm_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    styx_mode_combo = QComboBox()
    styx_mode_combo.setFixedWidth(180)
    styx_mode_combo.addItem("Insert (SteamStub verified)", "insert")
    styx_mode_combo.addItem("Overwrite", "overwrite")
    idx = styx_mode_combo.findData(sty_cfg.get("splice_mode", "insert"))
    if idx >= 0:
        styx_mode_combo.setCurrentIndex(idx)
    sty_grid.addWidget(sm_lbl, 1, 2)
    sty_grid.addWidget(styx_mode_combo, 1, 3)

    styx_layout.addLayout(sty_grid)
    layout.addWidget(styx_card)

    # -- 5. Lobster Config Recovery Card --
    lob_card, lob_layout = dialog._create_card_frame("Lobster Config Recovery")
    lob_desc = QLabel(
        "Lobster auto-recovers missing AdditionalDepots and DecryptionKeys in config.yaml "
        "if Headcrab or native updates strip them."
    )
    lob_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    lob_desc.setWordWrap(True)
    lob_layout.addWidget(lob_desc)

    lob_cfg = cfg.get("lobster", {})
    lob_row = QHBoxLayout()
    lob_row.setSpacing(16)

    lob_enable_cb = QCheckBox("Enable Lobster Recovery")
    lob_enable_cb.setChecked(lob_cfg.get("enabled", True))
    lob_enable_cb.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")
    lob_row.addWidget(lob_enable_cb)

    lob_notify_cb = QCheckBox("Desktop Notifications")
    lob_notify_cb.setChecked(lob_cfg.get("notify", True))
    lob_notify_cb.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    lob_row.addWidget(lob_notify_cb)
    lob_row.addStretch()

    lob_layout.addLayout(lob_row)
    layout.addWidget(lob_card)

    # -- 6. Proxy & Cloudflare Worker Card --
    proxy_card, proxy_layout = dialog._create_card_frame("Proxy & Worker Fallback (Bakeneko / Hubcap)")
    proxy_desc = QLabel(
        "Configure custom Cloudflare Worker endpoints for Bakeneko Hubcap fallback and ISP bypass. "
        "Includes token-bucket abuse protection (X-Katamari-Token)."
    )
    proxy_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    proxy_desc.setWordWrap(True)
    proxy_layout.addWidget(proxy_desc)

    prx_cfg = cfg.get("proxy", {})
    p_grid = QGridLayout()
    p_grid.setHorizontalSpacing(10)
    p_grid.setVerticalSpacing(6)

    wurl_lbl = QLabel("Worker /hubcap URL:")
    wurl_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    proxy_wurl_input = QLineEdit()
    proxy_wurl_input.setPlaceholderText("https://...workers.dev/hubcap")
    proxy_wurl_input.setText(prx_cfg.get("worker_url", ""))
    proxy_wurl_input.setStyleSheet("""
        QLineEdit {
            background-color: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 5px 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
        }
    """)
    p_grid.addWidget(wurl_lbl, 0, 0)
    p_grid.addWidget(proxy_wurl_input, 0, 1)

    wtok_lbl = QLabel("Worker Token:")
    wtok_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    proxy_token_input = QLineEdit()
    proxy_token_input.setPlaceholderText("X-Katamari-Token secret")
    proxy_token_input.setText(prx_cfg.get("worker_token", ""))
    proxy_token_input.setStyleSheet("""
        QLineEdit {
            background-color: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 5px 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
        }
    """)
    p_grid.addWidget(wtok_lbl, 1, 0)
    p_grid.addWidget(proxy_token_input, 1, 1)

    pmode_lbl = QLabel("Proxy Mode:")
    pmode_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")
    proxy_mode_combo = QComboBox()
    proxy_mode_combo.setFixedWidth(180)
    proxy_mode_combo.addItem("Fallback (Direct first)", "fallback")
    proxy_mode_combo.addItem("Always (Worker first)", "always")
    proxy_mode_combo.addItem("Never (Direct only)", "never")
    idx = proxy_mode_combo.findData(prx_cfg.get("mode", "fallback"))
    if idx >= 0:
        proxy_mode_combo.setCurrentIndex(idx)
    p_grid.addWidget(pmode_lbl, 2, 0)
    p_grid.addWidget(proxy_mode_combo, 2, 1)

    proxy_layout.addLayout(p_grid)
    layout.addWidget(proxy_card)

    # -- 7. Save & Sync Actions Row --
    actions_row = QHBoxLayout()
    actions_row.setSpacing(10)

    save_btn = QPushButton("Save & Sync to katamari.conf.lua")
    save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    save_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {getattr(dialog, 'accent_color', '#ff7518')};
            color: #000000;
            border: 1px solid {getattr(dialog, 'accent_color', '#ff7518')};
            border-radius: 6px;
            padding: 7px 18px;
            font-size: 9pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: #FFFFFF;
            color: #000000;
        }}
    """)

    reset_btn = QPushButton("Reset Defaults")
    reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reset_btn.setStyleSheet(STYLE_INACTIVE)

    status_lbl = QLabel("")
    status_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt; font-weight: 500;")

    def _on_save_clicked():
        save_cfg = {
            "family": {
                "manifest_mode": mode_combo.currentData(),
            },
            "proxy": {
                "worker_url": proxy_wurl_input.text().strip(),
                "worker_token": proxy_token_input.text().strip(),
                "mode": proxy_mode_combo.currentData(),
            },
            "bakeneko": {
                "enabled": True,
                "mode": bak_mode_combo.currentData(),
                "auto_pending": bak_autopending_cb.isChecked(),
                "dry_run": bak_dryrun_cb.isChecked(),
                "max_manifests": 32,
            },
            "spacebunny": {
                "authority": mrc_auth_combo.currentData(),
                "rescue": mrc_rescue_combo.currentData(),
                "full_timeout_ms": mrc_full_spin.value(),
                "probe_timeout_ms": mrc_probe_spin.value(),
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
            status_lbl.setText("✓ Saved to katamari.conf.lua (Restart Steam to apply)")
            status_lbl.setStyleSheet("color: #81C784; font-size: 8.5pt;")
        else:
            status_lbl.setText("⚠ Failed to save katamari.conf.lua")
            status_lbl.setStyleSheet("color: #EF5350; font-size: 8.5pt;")

    def _on_reset_clicked():
        d = KatamariConfigManager.load_config(Path("/nonexistent"))
        idx1 = mode_combo.findData(d["family"]["manifest_mode"])
        if idx1 >= 0: mode_combo.setCurrentIndex(idx1)
        idx2 = bak_mode_combo.findData(d["bakeneko"]["mode"])
        if idx2 >= 0: bak_mode_combo.setCurrentIndex(idx2)
        bak_autopending_cb.setChecked(d["bakeneko"]["auto_pending"])
        bak_dryrun_cb.setChecked(d["bakeneko"]["dry_run"])

        idx3 = mrc_auth_combo.findData(d["spacebunny"]["authority"])
        if idx3 >= 0: mrc_auth_combo.setCurrentIndex(idx3)
        idx4 = mrc_rescue_combo.findData(d["spacebunny"]["rescue"])
        if idx4 >= 0: mrc_rescue_combo.setCurrentIndex(idx4)
        mrc_full_spin.setValue(d["spacebunny"]["full_timeout_ms"])
        mrc_probe_spin.setValue(d["spacebunny"]["probe_timeout_ms"])

        styx_enable_cb.setChecked(d["styx"]["enabled"])
        styx_fb_spin.setValue(d["styx"]["fallback_app"])
        idx5 = styx_mode_combo.findData(d["styx"]["splice_mode"])
        if idx5 >= 0: styx_mode_combo.setCurrentIndex(idx5)

        lob_enable_cb.setChecked(d["lobster"]["enabled"])
        lob_notify_cb.setChecked(d["lobster"]["notify"])

        proxy_wurl_input.setText("")
        proxy_token_input.setText("")
        proxy_mode_combo.setCurrentIndex(0)

        status_lbl.setText("Reset to defaults (Click Save to apply).")
        status_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt;")

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

    tab.showEvent = lambda ev: (_refresh_tiles(), QWidget.showEvent(tab, ev))
    _refresh_tiles()

    dialog.tab_widget.addTab(tab, "Plugins")
    return tab

