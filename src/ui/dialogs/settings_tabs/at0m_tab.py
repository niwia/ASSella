import os
import sys
import shutil
import logging
from typing import Optional
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from ui.assets import GEAR_SVG
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLabel,
    QPushButton,
    QMenu,
    QFileDialog,
    QTabWidget,
    QGridLayout,
    QScrollArea,
    QSizePolicy,
)
from ui.widgets.modern_dropdown import ModernComboBox
from ui.dialogs.themed_message_box import ThemedMessageBox

from utils.helpers import create_checkbox_setting
from utils.color_utils import get_semantic_colors
from utils.yaml_config_manager import (
    get_user_config_path,
    get_yaml_boolean_value,
    update_yaml_boolean_value,
    calculate_file_sha256,
    get_sls_plugins_dirs,
    deploy_sls_plugin,
)
from utils.paths import Paths

logger = logging.getLogger(__name__)


NORMAL_BTN_STYLE = """
    QPushButton {
        background-color: rgba(255, 255, 255, 0.08);
        color: #FFFFFF;
        border: 1px solid rgba(255, 255, 255, 0.18);
        border-radius: 6px;
        padding: 8px 16px;
        font-size: 9pt;
        font-weight: 500;
    }
    QPushButton:hover {
        background-color: rgba(255, 255, 255, 0.16);
        border: 1px solid rgba(255, 255, 255, 0.35);
    }
    QPushButton:disabled {
        background-color: rgba(255, 255, 255, 0.03);
        color: rgba(255, 255, 255, 0.25);
        border-color: rgba(255, 255, 255, 0.06);
    }
"""


def _get_detected_btn_style(dialog) -> str:
    """Generate theme-harmonized button style for detected plugins using color utils."""
    accent = getattr(dialog, "accent_color", "#ff7518")
    c = QColor(accent)
    r, g, b = c.red(), c.green(), c.blue()
    return f"""
        QPushButton {{
            background-color: {accent};
            color: #000000;
            border: 1px solid {accent};
            border-radius: 6px;
            padding: 8px 16px;
            font-size: 9pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: #FFFFFF;
            color: #000000;
        }}
        QPushButton:disabled {{
            background-color: rgba({r}, {g}, {b}, 0.14);
            color: rgba(255, 255, 255, 0.90);
            border: 1px solid rgba({r}, {g}, {b}, 0.35);
        }}
    """


def _get_outdated_btn_style(dialog) -> str:
    """Button style for plugins that are installed but differ from Cloud manifest (custom or outdated)."""
    return """
        QPushButton {
            background-color: rgba(255, 171, 0, 0.18);
            color: #FFD54F;
            border: 1px solid rgba(255, 171, 0, 0.55);
            border-radius: 6px;
            padding: 8px 16px;
            font-size: 9pt;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: rgba(255, 171, 0, 0.32);
            border-color: rgba(255, 171, 0, 0.85);
            color: #FFFFFF;
        }
        QPushButton:disabled {
            background-color: rgba(255, 171, 0, 0.08);
            color: rgba(255, 213, 79, 0.5);
            border-color: rgba(255, 171, 0, 0.2);
        }
    """


def get_active_download_plugin() -> Optional[str]:
    """Find the active download interceptor plugin filename in SLSsteam plugins directory."""
    try:
        from utils.plugin_manager import get_active_interceptor_filename
        return get_active_interceptor_filename()
    except Exception as e:
        logger.debug(f"[at0mTab] Check active download plugin error: {e}")
    return None


def get_available_download_plugins() -> list[tuple[str, str]]:
    """Return available download plugins as list of (display_name, filename)."""
    try:
        from utils.plugin_manager import (
            get_default_interceptor_from_manifest,
            get_plugin_version,
            get_manifest_plugin_info,
        )
        def_interceptor = get_default_interceptor_from_manifest()
        sb_ver = get_plugin_version("spacebunny.lua") or ""
        std_ver = get_plugin_version("download.lua") or ""
    except Exception:
        def_interceptor = "spacebunny.lua"
        sb_ver = ""
        std_ver = ""

    sb_title = f"SpaceBunny {sb_ver}".strip()
    std_title = f"Standard {std_ver}".strip()

    is_sb_default = def_interceptor in ("spacebunny.lua", "spacebunny1.5.lua")
    sb_label = f"{sb_title} (Default)" if is_sb_default else sb_title
    std_label = f"{std_title} (download.lua) (Default)" if def_interceptor == "download.lua" else f"{std_title} (download.lua)"

    sb_filename = "spacebunny.lua" if (def_interceptor == "spacebunny.lua" or get_manifest_plugin_info("spacebunny.lua") or not get_manifest_plugin_info("spacebunny1.5.lua")) else "spacebunny1.5.lua"
    options = [
        (sb_label, sb_filename),
        (std_label, "download.lua"),
    ]
    try:
        from utils.helpers import get_base_path
        from utils.yaml_config_manager import get_sls_plugins_dirs
        from utils.plugin_manager import INTERCEPTOR_ROLES, get_retired_plugins, read_plugin_meta

        # Only real download interceptors belong in this list: the role a
        # plugin registers (or the Steam functions it hooks) decides.
        known_filenames = {opt[1] for opt in options} | set(get_retired_plugins())
        search_dirs = [
            get_base_path() / "plugins",
            Path.home() / ".local" / "share" / "ACCELA" / "plugins",
        ] + get_sls_plugins_dirs()
        for sdir in search_dirs:
            if sdir.is_dir():
                for f in sdir.glob("*.lua"):
                    if (
                        not f.name.endswith(".bak")
                        and not f.name.startswith(".")
                        and f.name not in known_filenames
                        and read_plugin_meta(f).get("role") in INTERCEPTOR_ROLES
                    ):
                        options.append((f"{f.name}", f.name))
                        known_filenames.add(f.name)
    except Exception as e:
        logger.debug(f"[at0mTab] Discover plugins error: {e}")
    return options


def get_plugin_state(filename: str) -> str:
    """Returns 'up_to_date', 'outdated', or 'missing' comparing with Cloud manifest."""
    try:
        from utils.plugin_manager import get_plugin_status
        return get_plugin_status(filename)
    except Exception as e:
        logger.debug(f"[at0mTab] Check plugin status error: {e}")
        return "missing"


def is_plugin_detected(filename: str) -> bool:
    """Check if plugin file exists in SLSsteam plugins directory and is valid."""
    return get_plugin_state(filename) != "missing"


def create_at0m_tab(dialog) -> QWidget:
    """
    Create the dedicated at0-m settings tab with General and Advanced (Plugins) sub-tabs.
    """
    tab = QWidget()
    main_layout = QVBoxLayout(tab)
    main_layout.setContentsMargins(12, 10, 12, 10)
    main_layout.setSpacing(8)

    atom_subtabs = QTabWidget()
    atom_subtabs.setUsesScrollButtons(True)
    dialog.atom_subtabs = atom_subtabs
    bg_color = dialog.settings.value("background_color", "#141416")
    atom_subtabs.setStyleSheet(f"""
        QTabWidget::pane {{
            border: none;
        }}
        QTabBar::tab {{
            background: {bg_color};
            color: rgba(255, 255, 255, 0.6);
            padding: 6px 16px;
            border: none;
            font-weight: bold;
            font-size: 9.5pt;
        }}
        QTabBar::tab:selected {{
            color: {dialog.accent_color};
            border-bottom: 2px solid {dialog.accent_color};
        }}
        QTabBar::tab:hover {{
            color: #FFFFFF;
        }}
    """)

    # ── Subtab 1: General ──
    general_tab = QWidget()
    gen_scroll = QScrollArea()
    gen_scroll.setWidgetResizable(True)
    gen_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    gen_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    gen_container = QWidget()
    layout = QVBoxLayout(gen_container)
    layout.setContentsMargins(0, 4, 0, 10)
    layout.setSpacing(8)
    gen_scroll.setWidget(gen_container)

    gen_tab_layout = QVBoxLayout(general_tab)
    gen_tab_layout.setContentsMargins(0, 0, 0, 0)
    gen_tab_layout.addWidget(gen_scroll)

    cfg_path = get_user_config_path()

    # -- 1. General Configuration Card --
    cfg_card, cfg_layout = dialog._create_card_frame("General Configuration")

    # Master Option: Enable plugins (SLSsteam)
    sls_plugins_val = get_yaml_boolean_value(cfg_path, "Plugins", default=True)
    saved_enable_at0m = dialog.settings.value(
        "enable_at0m",
        dialog.settings.value("enable_vapor", sls_plugins_val, type=bool),
        type=bool,
    )

    dialog.enable_at0m_checkbox = create_checkbox_setting(
        "Enable plugins",
        "enable_at0m",
        saved_enable_at0m,
        dialog,
        tooltip="Master switch for SLSsteam plugins. Automatically deploys bundled plugins and enables Plugins in SLSsteam config.yaml.",
        show_description=False,
    )
    dialog.enable_vapor_checkbox = dialog.enable_at0m_checkbox
    cfg_layout.addWidget(dialog.enable_at0m_checkbox)

    # Top separator
    sep_top = QFrame()
    sep_top.setFrameShape(QFrame.Shape.HLine)
    sep_top.setStyleSheet("color: rgba(255,255,255,0.08); border: none; background: rgba(255,255,255,0.08); max-height: 1px;")
    cfg_layout.addWidget(sep_top)

    # DisableUpdates stays enforced for every install (a Steam update would
    # replace the files SLSsteam patches). src/main.py applies it at startup so
    # merely opening this page never rewrites the SLSsteam config.
    dialog.at0m_disable_updates_checkbox = None
    dialog.vapor_disable_updates_checkbox = None

    # Default download behavior lives on the Downloads tab (dialog.at0m_download_action_combo).

    # Option 3: Start download with Steam
    start_row = QHBoxLayout()
    start_row.setContentsMargins(2, 2, 2, 2)
    start_row.setSpacing(10)

    start_title = QLabel("Start download with Steam")
    start_title.setStyleSheet("color: #FFFFFF; font-weight: bold; font-size: 9pt;")
    start_row.addWidget(start_title, stretch=1)

    dialog.at0m_start_mode_combo = ModernComboBox()
    dialog.at0m_start_mode_combo.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.at0m_start_mode_combo.setFixedWidth(155)
    dialog.at0m_start_mode_combo.addItem("Start auto", "immediate")
    dialog.at0m_start_mode_combo.addItem("Add only", "add_only")
    dialog.at0m_start_mode_combo.addItem("Always ask", "ask")
    dialog.vapor_start_mode_combo = dialog.at0m_start_mode_combo

    saved_start_action = dialog.settings.value(
        "at0m_start_download_action",
        dialog.settings.value("vapor_start_download_action", "", type=str),
        type=str,
    )
    if not saved_start_action:
        old_imm = dialog.settings.value(
            "at0m_start_download_immediately",
            dialog.settings.value("vapor_start_download_immediately", True, type=bool),
            type=bool,
        )
        saved_start_action = "immediate" if old_imm else "add_only"

    s_idx = dialog.at0m_start_mode_combo.findData(saved_start_action)
    if s_idx >= 0:
        dialog.at0m_start_mode_combo.setCurrentIndex(s_idx)

    dialog.at0m_start_mode_combo.currentIndexChanged.connect(
        lambda _i: (
            dialog.settings.setValue(
                "at0m_start_download_action",
                dialog.at0m_start_mode_combo.currentData(),
            ),
            dialog.settings.setValue(
                "vapor_start_download_action",
                dialog.at0m_start_mode_combo.currentData(),
            ),
            dialog.settings.setValue(
                "at0m_start_download_immediately",
                dialog.at0m_start_mode_combo.currentData() == "immediate",
            ),
            dialog.settings.setValue(
                "vapor_start_download_immediately",
                dialog.at0m_start_mode_combo.currentData() == "immediate",
            ),
            dialog.settings.sync(),
        )
    )
    start_row.addWidget(dialog.at0m_start_mode_combo)
    cfg_layout.addLayout(start_row)

    # Separator
    sep2 = QFrame()
    sep2.setFrameShape(QFrame.Shape.HLine)
    sep2.setStyleSheet("color: rgba(255,255,255,0.08); border: none; background: rgba(255,255,255,0.08); max-height: 1px;")
    cfg_layout.addWidget(sep2)

    # Option 4: DLC-Only Download behaviour (after separator)
    dlc_act_row = QHBoxLayout()
    dlc_act_row.setContentsMargins(2, 2, 2, 2)
    dlc_act_row.setSpacing(10)

    dlc_act_title = QLabel("DLC-Only Download behaviour")
    dlc_act_title.setStyleSheet("color: #FFFFFF; font-weight: bold; font-size: 9pt;")
    dlc_act_row.addWidget(dlc_act_title, stretch=1)

    dialog.at0m_dlc_action_combo = ModernComboBox()
    dialog.at0m_dlc_action_combo.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.at0m_dlc_action_combo.setFixedWidth(155)
    dialog.at0m_dlc_action_combo.addItem("Always ask", "ask")
    dialog.at0m_dlc_action_combo.addItem("Start Native (at0-m)", "native")
    dialog.at0m_dlc_action_combo.addItem("Start ASSella", "assella")

    saved_dlc_behavior = dialog.settings.value(
        "at0m_default_dlc_action",
        "ask",
        type=str,
    )
    cur_dlc_idx = dialog.at0m_dlc_action_combo.findData(saved_dlc_behavior)
    if cur_dlc_idx >= 0:
        dialog.at0m_dlc_action_combo.setCurrentIndex(cur_dlc_idx)

    def _on_dlc_action_changed(_i):
        dialog.settings.setValue(
            "at0m_default_dlc_action",
            dialog.at0m_dlc_action_combo.currentData(),
        )
        dialog.settings.sync()

    dialog.at0m_dlc_action_combo.currentIndexChanged.connect(_on_dlc_action_changed)
    dlc_act_row.addWidget(dialog.at0m_dlc_action_combo)
    cfg_layout.addLayout(dlc_act_row)


    layout.addWidget(cfg_card)

    # -- 2. Katamari Plugins Status & Updaters Card --
    deploy_card, deploy_layout = dialog._create_card_frame("Manage plugins")
    deploy_layout.setSpacing(8)

    deploy_desc = QLabel("Click to deploy and update")
    deploy_desc.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    deploy_desc.setWordWrap(True)
    deploy_layout.addWidget(deploy_desc)

    STYLE_ACTIVE_GREEN = """
        QPushButton {
            background-color: rgba(76, 175, 80, 0.2);
            color: #81C784;
            border: 1px solid rgba(129, 199, 132, 0.5);
            border-radius: 6px;
            padding: 7px 10px;
            font-size: 8.5pt;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: rgba(76, 175, 80, 0.32);
            border-color: rgba(129, 199, 132, 0.85);
        }
    """
    STYLE_ATTENTION_ORANGE = """
        QPushButton {
            background-color: rgba(255, 171, 0, 0.18);
            color: #FFD54F;
            border: 1px solid rgba(255, 171, 0, 0.55);
            border-radius: 6px;
            padding: 7px 10px;
            font-size: 8.5pt;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: rgba(255, 171, 0, 0.32);
            border-color: rgba(255, 171, 0, 0.85);
            color: #FFFFFF;
        }
    """
    STYLE_DISABLED = """
        QPushButton {
            background-color: rgba(255, 255, 255, 0.03);
            color: rgba(255, 255, 255, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 6px;
            padding: 7px 10px;
            font-size: 8.5pt;
            font-weight: 500;
        }
    """

    # Plugin buttons. Read dynamically from manifest when available, falling back
    # to the 6 Katamari family defaults. One click fetches the manifest and
    # installs or updates that plugin. The manifest decides which plugins can
    # be installed at all (Bakeneko stays greyed out until it allows it).
    DEFAULT_PLUGIN_BUTTONS = [
        # (attribute, filename, display name, row)
        ("btn_spacebunny_plugin", "spacebunny.lua", "SpaceBunny", 0),
        ("btn_styx_plugin", "styx.lua", "Styx", 0),
        ("btn_lobster_plugin", "lobster.lua", "Lobster", 0),
        ("btn_lettuce_plugin", "lettuce.lua", "Lettuce", 1),
        ("btn_pineapple_plugin", "pineapple.lua", "Pineapple", 1),
    ]

    class _PluginButton(QPushButton):
        """Plugin button whose text elides instead of widening the page."""

        def __init__(self, text: str):
            super().__init__(text)
            self._full = text
            self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            self.setMinimumWidth(60)

        def setText(self, text: str) -> None:  # noqa: N802 - Qt override
            self._full = text
            self._elide()

        def text(self) -> str:  # noqa: D401 - Qt override
            return self._full

        def resizeEvent(self, e):  # noqa: N802
            super().resizeEvent(e)
            self._elide()

        def _elide(self) -> None:
            avail = max(10, self.width() - 22)
            shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, avail)
            QPushButton.setText(self, shown)
            tip = self.toolTip()
            if shown != self._full and self._full not in tip:
                self.setToolTip(self._full + ("\n\n" + tip if tip else ""))

    def _build_plugin_button_defs():
        from utils.plugin_manager import (
            fetch_plugins_manifest,
            get_retired_plugins,
            get_manifest_plugin_info,
        )

        manifest = fetch_plugins_manifest()
        retired = get_retired_plugins() or {}

        button_defs = []
        seen_files = set()

        # 1. Base Katamari family plugins (display names from manifest if present)
        for attr, fname, default_name, row_idx in DEFAULT_PLUGIN_BUTTONS:
            if fname in retired:
                continue
            meta = get_manifest_plugin_info(fname) or {}
            display_name = meta.get("name") or default_name
            button_defs.append((attr, fname, display_name, row_idx))
            seen_files.add(fname)

        # 2. Additional dynamic plugins from manifest if present
        if manifest and isinstance(manifest.get("plugins"), dict):
            for fname, meta in manifest["plugins"].items():
                if fname in seen_files or fname in retired:
                    continue
                if meta.get("retired"):
                    continue
                is_core = meta.get("required") or meta.get("category") == "interceptor"
                row_idx = 0 if is_core else 1
                display_name = meta.get("name") or fname.replace(".lua", "").title()
                attr = f"btn_{fname.replace('.', '_').replace('-', '_')}"
                button_defs.append((attr, fname, display_name, row_idx))
                seen_files.add(fname)

        return button_defs

    buttons_container = QWidget()
    buttons_container_layout = QVBoxLayout(buttons_container)
    buttons_container_layout.setContentsMargins(0, 0, 0, 0)
    buttons_container_layout.setSpacing(6)
    deploy_layout.addWidget(buttons_container)

    def _render_plugin_buttons():
        while buttons_container_layout.count():
            item = buttons_container_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            l = item.layout()
            if l:
                while l.count():
                    sub = l.takeAt(0)
                    sw = sub.widget()
                    if sw:
                        sw.deleteLater()

        button_defs = _build_plugin_button_defs()
        dialog._plugin_button_defs = button_defs

        # A fixed 3-column grid: buttons share the width and their text is
        # elided, so a long label ("Download Interceptor (Install)") can never
        # push the page wider than the dialog (it used to clip everything).
        COLS = 3
        core_grid, extras_grid = QGridLayout(), QGridLayout()
        for g in (core_grid, extras_grid):
            g.setContentsMargins(0, 2, 0, 4)
            g.setHorizontalSpacing(8)
            g.setVerticalSpacing(6)
            for c in range(COLS):
                g.setColumnStretch(c, 1)
        counts = [0, 0]
        dialog._plugin_btn_entries = []

        for attr, fname, label, row_idx in button_defs:
            btn = _PluginButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            setattr(dialog, attr, btn)
            btn.clicked.connect(lambda _c=False, b=btn, f=fname, n=label: _handle_plugin_click(b, f, n))
            which = 0 if row_idx == 0 else 1
            grid = core_grid if which == 0 else extras_grid
            n = counts[which]
            grid.addWidget(btn, n // COLS, n % COLS)
            counts[which] += 1
            dialog._plugin_btn_entries.append((btn, fname, label))

        buttons_container_layout.addLayout(core_grid)
        if counts[1]:
            extras_lbl = QLabel("Extras")
            extras_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.9); font-size: 9pt; font-weight: 700; margin-top: 6px;")
            buttons_container_layout.addWidget(extras_lbl)
            buttons_container_layout.addLayout(extras_grid)

    # Separator
    deploy_sep = QFrame()
    deploy_sep.setFrameShape(QFrame.Shape.HLine)
    deploy_sep.setStyleSheet("color: rgba(255,255,255,0.06); border: none; background: rgba(255,255,255,0.06); max-height: 1px;")
    deploy_layout.addWidget(deploy_sep)

    # Advanced custom plugins toggle
    dialog.custom_plugins_checkbox = create_checkbox_setting(
        "I'm using custom plugins (Advanced)",
        "custom_plugins_advanced",
        False,
        dialog,
        "Bypasses plugin missing checks for game transfers and native downloads. Disables non-family archiving.",
        show_description=False,
    )
    deploy_layout.addWidget(dialog.custom_plugins_checkbox)

    layout.addWidget(deploy_card)

    def _refresh_plugin_btn(btn: QPushButton, filename: str, display_name: str):
        from utils.plugin_manager import (
            get_manifest_plugin_info,
            get_plugin_status,
            get_plugin_version,
            is_plugin_check_bypassed,
            is_plugin_deployable,
        )

        btn.setProperty("plugin_busy", False)
        if is_plugin_check_bypassed():
            btn.setEnabled(False)
            btn.setText(display_name)
            btn.setToolTip("Custom plugins option is enabled. Plugins are managed by you.")
            btn.setStyleSheet(STYLE_DISABLED)
            return

        meta = get_manifest_plugin_info(filename) or {}
        manifest_name = meta.get("name")
        if manifest_name:
            display_name = manifest_name

        deployable, why = is_plugin_deployable(filename)
        if not deployable:
            btn.setEnabled(False)
            btn.setText(f"{display_name} · unavailable")
            btn.setToolTip(why)
            btn.setStyleSheet(STYLE_DISABLED)
            return

        status = get_plugin_status(filename)
        desc = str(meta.get("description") or "")
        if status == "up_to_date":
            ver = get_plugin_version(filename) or meta.get("version", "")
            btn.setText(f"{display_name} v{ver}" if ver else display_name)
            btn.setToolTip((desc + "\n\n" if desc else "") + "Installed and up to date. Click to check for updates.")
            btn.setStyleSheet(STYLE_ACTIVE_GREEN)
        elif status == "missing":
            btn.setText(f"{display_name} · install")
            btn.setToolTip((desc + "\n\n" if desc else "") + "Not installed. Click to install.")
            btn.setStyleSheet(STYLE_ATTENTION_ORANGE)
        else:
            btn.setText(f"{display_name} · update")
            btn.setToolTip(
                (desc + "\n\n" if desc else "")
                + "A different version is installed. Click to install the published one "
                "(the current file is kept as .bak)."
            )
            btn.setStyleSheet(STYLE_ATTENTION_ORANGE)
        btn.setEnabled(True)

    class _PluginJob(QThread):
        done = pyqtSignal(bool, str)

        def __init__(self, filename: str, parent=None):
            super().__init__(parent)
            self.filename = filename

        def run(self):
            try:
                from utils.plugin_manager import deploy_plugin, fetch_plugins_manifest

                fetch_plugins_manifest(force_refresh=True)
                ok, _skipped, msg = deploy_plugin(self.filename)
                self.done.emit(ok, msg)
            except Exception as e:  # never leave the button stuck
                self.done.emit(False, str(e))

    def _handle_plugin_click(btn: QPushButton, filename: str, display_name: str):
        """Click and forget: refresh the manifest, then install or update."""
        if btn.property("plugin_busy"):
            return
        btn.setProperty("plugin_busy", True)
        btn.setEnabled(False)
        btn.setText(f"Updating {display_name}...")
        btn.setStyleSheet(STYLE_DISABLED)

        job = _PluginJob(filename, dialog)

        def _finished(ok: bool, msg: str):
            _refresh_all_buttons()
            if not ok:
                logger.warning(f"[at0mTab] {display_name}: {msg}")
                btn.setToolTip(msg)
            job.deleteLater()

        job.done.connect(_finished)
        job.start()

    def _refresh_all_buttons():
        for btn, fname, label in getattr(dialog, "_plugin_btn_entries", []):
            _refresh_plugin_btn(btn, fname, label)

    def _on_custom_plugins_toggled(checked: bool):
        dialog.settings.setValue("custom_plugins_advanced", checked)
        dialog.settings.sync()
        if checked:
            ThemedMessageBox.warning(
                dialog,
                "Custom Plugins (Advanced)",
                "Bypassing plugin presence verification enables native downloads and game transfers with custom or third-party Lua plugins.\n\n"
                "Please note: ASSella cannot guarantee compatibility, functionality, or stability with third-party plugins!"
            )
        _refresh_all_buttons()

    dialog.custom_plugins_checkbox.checkbox.toggled.connect(_on_custom_plugins_toggled)

    # Initial populate from the local manifest (instant), then check the
    # plugin sources in the background and refresh the buttons when done.
    # Fetching on the GUI thread here was most of Settings' opening delay.
    from utils.plugin_manager import offline_manifest

    with offline_manifest():
        _render_plugin_buttons()
        _refresh_all_buttons()

    class _ManifestRefresh(QThread):
        def run(self):
            try:
                from utils.plugin_manager import fetch_plugins_manifest

                fetch_plugins_manifest()
            except Exception as e:
                logger.debug(f"[at0mTab] Background manifest refresh failed: {e}")

    def _after_refresh():
        try:
            current_fnames = [fname for _, fname, _, _ in getattr(dialog, "_plugin_button_defs", [])]
            new_defs = _build_plugin_button_defs()
            new_fnames = [fname for _, fname, _, _ in new_defs]
            if current_fnames != new_fnames:
                _render_plugin_buttons()
            _refresh_all_buttons()
        except RuntimeError:
            pass  # dialog already closed

    refresh = _ManifestRefresh(dialog)
    refresh.finished.connect(_after_refresh)
    dialog._plugin_manifest_worker = refresh
    refresh.start()

    def _update_subwidget_states(enabled: bool):
        if getattr(dialog, "at0m_disable_updates_checkbox", None):
            dialog.at0m_disable_updates_checkbox.setEnabled(enabled)
        if getattr(dialog, "at0m_download_action_combo", None) is not None:  # Downloads page may not be built yet
            dialog.at0m_download_action_combo.setEnabled(enabled)
        dialog.at0m_start_mode_combo.setEnabled(enabled)
        dialog.at0m_dlc_action_combo.setEnabled(enabled)
        deploy_card.setEnabled(enabled)
        atom_subtabs.setTabEnabled(1, enabled)

    def _on_enable_at0m_toggled(checked: bool):
        dialog.settings.setValue("enable_at0m", checked)
        dialog.settings.setValue("enable_vapor", checked)
        dialog.settings.setValue("use_native_steam_download", checked)
        try:
            update_yaml_boolean_value(cfg_path, "Plugins", checked)
        except Exception as e:
            logger.debug(f"[at0mTab] Could not update Plugins in SLS config: {e}")
        if checked:
            from utils.yaml_config_manager import ensure_sls_plugin_config_sections
            ensure_sls_plugin_config_sections(cfg_path)

        _update_subwidget_states(checked)
        combo = getattr(dialog, "at0m_download_action_combo", None)
        if combo is not None:
            idx = combo.findData("ask" if checked else "assella")
            if idx >= 0:
                combo.setCurrentIndex(idx)
        elif not checked:
            for k in ("at0m_default_download_action", "vapor_default_download_action", "native_steam_default_action"):
                dialog.settings.setValue(k, "assella")

    dialog.enable_at0m_checkbox.toggled.connect(_on_enable_at0m_toggled)
    _update_subwidget_states(dialog.enable_at0m_checkbox.isChecked())

    layout.addStretch()

    # ── Subtab 2: Advanced (Plugins configuration) ──
    from ui.dialogs.settings_tabs.plugins_tab import create_plugins_tab
    advanced_tab = create_plugins_tab(dialog, add_to_dialog=False)

    atom_subtabs.addTab(general_tab, "General")
    atom_subtabs.addTab(advanced_tab, "Advanced")
    main_layout.addWidget(atom_subtabs)

    dialog.tab_widget.addTab(tab, "at0-m")
    return tab


# Backward compatibility alias
create_vapor_tab = create_at0m_tab
