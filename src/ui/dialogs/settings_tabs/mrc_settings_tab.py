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
    QScrollArea,
    QFrame,
    QCheckBox,
)
from ui.dialogs.themed_message_box import ThemedMessageBox

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

    settings = get_settings()

    # Informational notice regarding graduation of manifest/MRC controls to main Plugins tab
    info_card, info_layout = dialog._create_card_frame("Developer Testing Menu")
    info_desc = QLabel(
        "Manifest delivery modes, SpaceBunny MRC hierarchies, and worker proxy settings have "
        "been moved to the primary <b>Plugins</b> tab in Settings.<br><br>"
        "This secret Testing tab is now exclusively dedicated to cache diagnostics, demo toggles, and developer tools."
    )
    info_desc.setStyleSheet("color: rgba(255, 255, 255, 0.75); font-size: 8.5pt;")
    info_desc.setWordWrap(True)
    info_layout.addWidget(info_desc)
    layout.addWidget(info_card)

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
        ThemedMessageBox.information(dialog, "Welcome Screen", "Welcome screen reset!\nIt will automatically show on the next launch.")
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

    # 2. Clear Build ID & Update Cache
    cache_lbl = QLabel("Update & Build ID Cache:")
    cache_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")
    t_grid.addWidget(cache_lbl, 1, 0)

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
        ThemedMessageBox.information(
            dialog,
            "Cache Cleared",
            "Update status, build ID, and branch caches have been cleared successfully.\n\n"
            "Fresh live data will be queried next time you check for updates or open game details."
        )
    clear_cache_btn.clicked.connect(_on_clear_cache)
    t_grid.addWidget(clear_cache_btn, 1, 1)

    # 3. Lock Secret Menu & Seasonal Theme
    lock_lbl = QLabel("Secret Menu:")
    lock_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")
    t_grid.addWidget(lock_lbl, 2, 0)

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
        ThemedMessageBox.information(
            dialog,
            "Testing Menu Locked",
            "The secret Testing menu has been locked!\nIt will be hidden when Settings is reopened.\n(Enter the cheatcode again to unlock)"
        )
    lock_menu_btn.clicked.connect(_on_lock_menu)
    t_grid.addWidget(lock_menu_btn, 2, 1)

    reset_theme_btn = QPushButton("Reset Seasonal Theme")
    reset_theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reset_theme_btn.setStyleSheet("font-size: 8.5pt; padding: 4px 10px;")
    def _on_reset_theme():
        settings.setValue("halloween_auto_applied", False)
        settings.sync()
        ThemedMessageBox.information(dialog, "Seasonal Theme", "Seasonal theme auto-activation flag reset.")
    reset_theme_btn.clicked.connect(_on_reset_theme)
    t_grid.addWidget(reset_theme_btn, 2, 2)

    # 4. Byparr Cloudflare Solver
    byparr_lbl = QLabel("Byparr Solver:")
    byparr_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")
    t_grid.addWidget(byparr_lbl, 3, 0)

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
    t_grid.addWidget(install_byparr_btn, 3, 1, 1, 2)

    # 5. LuaTools Secret (Reset & Lock)
    luatools_lbl = QLabel("LuaTools Secret:")
    from utils.color_utils import get_raised_surface_tokens
    _label_tokens = get_raised_surface_tokens(settings.value("background_color", "#111318", type=str))
    luatools_lbl.setStyleSheet(f"color: {_label_tokens['ink']}; font-size: 8.5pt; font-weight: 500;")
    t_grid.addWidget(luatools_lbl, 4, 0)

    reset_luatools_btn = QPushButton("Reset & Lock LuaTools")
    reset_luatools_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    reset_luatools_btn.setStyleSheet("""
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
    def _on_reset_luatools():
        settings.setValue("luatools_unlocked", False)
        try:
            from core.lua_tools_api import LuaToolsAPI
            LuaToolsAPI.clear_session()
        except Exception as e:
            logger.warning(f"Error clearing LuaTools session during reset: {e}")
        settings.sync()

        # If LuaTools tab is open in SettingsDialog, remove it dynamically
        for i in range(dialog.tab_widget.count()):
            if dialog.tab_widget.tabText(i).lower() == "luatools":
                dialog.tab_widget.removeTab(i)
                break

        ThemedMessageBox.information(
            dialog,
            "LuaTools Reset & Locked",
            "The LuaTools secret feature has been disabled and its session reset.\n\n"
            "The tab will be hidden until you type the 'luatools' keyword in the library search bar again."
        )
    reset_luatools_btn.clicked.connect(_on_reset_luatools)
    t_grid.addWidget(reset_luatools_btn, 4, 1, 1, 2)

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
