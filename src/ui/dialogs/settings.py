import logging
import os
import shutil
import subprocess
import sys
import webbrowser

from datetime import datetime
from typing import Any, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QDesktopServices, QMovie, QPainter
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFontDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import morrenus_api
from ui.dialogs.dialog_helpers import create_standard_buttons
from utils.helpers import (
    create_checkbox_setting,
    create_font_setting,
    create_slider_setting,
    get_base_path,
    get_slscheevo_path,
    get_slscheevo_save_path,
    get_schema_grabber_path,
    get_venv_python,
)
from utils.paths import Paths
from utils.settings import get_settings
from utils.yaml_config_manager import is_slssteam_mode_enabled
from ui.dialogs.settings_sls import create_sls_tab

logger = logging.getLogger(__name__)


class MorrenusStatsWidget(QWidget):
    """Widget displaying Morrenus API user statistics."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.settings = get_settings()
        self.username_label = None
        self.daily_usage_bar = None
        self.expiration_label = None
        self.total_calls_label = None
        self.status_label = None
        self.refresh_button = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Initialize the UI components."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 5, 0, 5)

        # Row 1: Username
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        self.username_label = QLabel("User: --")
        self.username_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row1.addWidget(self.username_label)
        main_layout.addLayout(row1)

        # Progress Bar
        self.daily_usage_bar = QProgressBar()
        self.daily_usage_bar.setRange(0, 100)
        self.daily_usage_bar.setValue(0)
        self.daily_usage_bar.setFormat("Daily: --")
        self.daily_usage_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        accent_color = self.settings.value("accent_color", "#C06C84")
        self.daily_usage_bar.setStyleSheet(
            f"""
            QProgressBar {{
                border: 1px solid #444;
                border-radius: 0px;
                text-align: center;
                color: #fff;
                background-color: #222;
                height: 20px;
            }}
            QProgressBar::chunk {{
                background-color: {accent_color};
            }}
        """
        )
        main_layout.addWidget(self.daily_usage_bar)

        # Row 2: Stats
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        self.expiration_label = QLabel("Expires: --")
        self.expiration_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row2.addWidget(self.expiration_label)

        self.total_calls_label = QLabel("Total: --")
        self.total_calls_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row2.addWidget(self.total_calls_label)

        self.status_label = QLabel("Status: --")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row2.addWidget(self.status_label)

        main_layout.addLayout(row2)

        # Refresh button
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.refresh_button.clicked.connect(self.refresh_stats)
        main_layout.addWidget(self.refresh_button)

    def refresh_stats(self) -> None:
        """Fetch and display latest stats from the API."""
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Loading...")

        stats = morrenus_api.get_user_stats()

        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Refresh")

        if stats.get("error"):
            self._display_error_state()
        else:
            self._display_stats(stats)

    def _display_error_state(self) -> None:
        """Update UI to show error state."""
        self.username_label.setText("User: Error")
        self.total_calls_label.setText("Total: --")
        self.daily_usage_bar.setFormat("Daily: Error")
        self.daily_usage_bar.setValue(0)
        self.expiration_label.setText("Expires: --")
        self.status_label.setText("Status: Error")

    def _display_stats(self, stats: dict) -> None:
        """Update UI with fetched statistics."""
        self.username_label.setText(f"User: {stats.get('username', 'Unknown')}")
        self.total_calls_label.setText(f"Total: {stats.get('api_key_usage_count', 0)}")

        daily_usage = MorrenusStatsWidget._parse_int(stats.get("daily_usage", 0))
        daily_limit = MorrenusStatsWidget._parse_int(stats.get("daily_limit", 100))
        if daily_limit == 0:
            daily_limit = 100

        self.daily_usage_bar.setRange(0, daily_limit)
        self.daily_usage_bar.setValue(daily_usage)
        self.daily_usage_bar.setFormat(f"Daily: {daily_usage}/{daily_limit}")

        self._update_expiration_label(stats.get("api_key_expires_at", ""))

        status = "Active" if stats.get("can_make_requests", False) else "Blocked"
        self.status_label.setText(f"Status: {status}")

    @staticmethod
    def _parse_int(value: Any, default: int = 0) -> int:
        """Safely parse an integer value."""
        try:
            return int(value or default)
        except (TypeError, ValueError):
            return default

    def _update_expiration_label(self, expires_at: str) -> None:
        """Format and update the expiration label."""
        if not expires_at:
            self.expiration_label.setText("Expires: Never")
            return

        try:
            dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            self.expiration_label.setText(f"Expires: {dt.strftime('%d/%m/%Y')}")
        except ValueError:
            self.expiration_label.setText(f"Expires: {expires_at[:10]}")


class SettingsDialog(QDialog):
    """Dialog for configuring application settings."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(525)
        self.setMinimumHeight(650)
        self.resize(525, 650)
        self.settings = get_settings()
        self.main_window = parent
        self.accent_color = self.settings.value("accent_color", "#C06C84")
        self.main_layout = None
        self.tab_widget = None
        self.library_mode_checkbox = None
        self.auto_skip_single_choice_checkbox = None
        self.smart_depot_selection_checkbox = None
        self.autofetch_manifests_checkbox = None
        self.use_lancache_checkbox = None
        self.smart_update_mode_checkbox = None
        self.refined_update_check_checkbox = None
        self.isp_bypass_hubcap_checkbox = None
        self.fakeappid_db_integration_checkbox = None
        self.remote_web_ui_checkbox = None
        self.max_downloads_spinbox = None
        self.steamless_remover_combo = None
        self.filter_soundtracks_checkbox = None
        self.filter_search_blacklist_checkbox = None
        self.achievements_checkbox = None
        self.auto_apply_goldberg_checkbox = None
        self.sls_mode_checkbox = None
        self.sls_config_management_checkbox = None
        self.prompt_steam_restart_checkbox = None
        self.ignore_slssteam_updater_checkbox = None
        self.block_steam_updates_checkbox = None
        self.download_slssteam_button = None
        self.slssteam_status_label = None
        self.slssteam_hash_warning_label = None
        self.accent_color_button = None
        self.accent_reset_button = None
        self.bg_color_button = None
        self.bg_reset_button = None
        self.titlebar_position_checkbox = None
        self.sonic_mode_checkbox = None
        self.workshop_steam_checkbox = None
        self.workshop_max_dl_spinbox = None
        self.workshop_cell_id_input = None
        self.current_font = QFont()
        self.morrenus_stats_widget = None
        self.morrenus_tab_initialized = False

        # Origins easter egg setup
        self._origins_movie = None
        self._fade_timer = None
        self._flash_opacity = 0.18
        self._original_remember_origins = self.settings.value("remember_origins", False, type=bool)
        self._original_simplify_denuvo_status = self.settings.value("simplify_denuvo_status", False, type=bool)
        if self._original_remember_origins:

            gif_path = "/home/deck/.local/share/ACCELA/jumpscare/lain.gif"
            if os.path.exists(gif_path):
                self._origins_movie = QMovie(gif_path)
                self._origins_movie.frameChanged.connect(self.update)
                self._origins_movie.start()

        # Save original API keys for restore on cancel
        self._original_morrenus_key = self.settings.value(
            "morrenus_api_key", "", type=str
        )
        self.settings.sync()
        self._original_steam_username = self.settings.value(
            "steam_username", "", type=str
        )
        from utils.helpers import decrypt_string
        self._original_steam_password = decrypt_string(
            self.settings.value("steam_password", "", type=str)
        )

        self._user_accent_color = self.settings.value(
            "user_accent_color",
            self.settings.value("accent_color", "#C06C84"),
            type=str,
        )
        self._user_background_color = self.settings.value(
            "user_background_color",
            self.settings.value("background_color", "#000000"),
            type=str,
        )
        self._original_titlebar_position = self.settings.value(
            "titlebar_position", "bottom", type=str
        )

        logger.debug("Opening SettingsDialog.")
        self._setup_ui()

        if self.parent():
            from ui.dialogs.dialog_raiser import DialogRaiser
            DialogRaiser(self.parent(), self)

    def _setup_ui(self) -> None:
        """Initialize the UI layout."""
        self.main_layout = QVBoxLayout(self)

        self._create_tab_widget()
        self._setup_tabs()
        self.main_layout.addWidget(self.tab_widget)

        self._create_dialog_buttons()

    def _create_tab_widget(self) -> None:
        """Create and style the tab widget."""
        self.tab_widget = QTabWidget()
        bg_color = self.settings.value("background_color", "#1E1E1E")
        self.tab_widget.setStyleSheet(
            f"""
            QTabWidget::pane {{
                border: none;
            }}
            QTabBar::tab {{
                background: {bg_color};
                color: #888888;
                padding: 8px 16px;
                border: none;
            }}
            QTabBar::tab:selected {{
                color: {self.accent_color};
                border-bottom: 2px solid {self.accent_color};
            }}
            QTabBar::tab:!selected {{
                color: #888888;
            }}
        """
        )

    def _setup_tabs(self) -> None:
        """Initialize and add all settings tabs."""
        self._create_assela_tab()
        self._create_downloads_tab()
        self._create_morrenus_tab()
        # self._create_webui_tab()
        create_sls_tab(self)
        self._create_tools_tab()
        self._create_style_tab()

        # Initialize button state after all tabs have been populated
        self._update_achievements_button_state()

    def _create_dialog_buttons(self) -> None:
        """Create standard Ok/Cancel buttons."""
        buttons = create_standard_buttons(self.accept, self.reject)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.main_layout.addWidget(buttons)

    def _create_api_key_setting(
        self,
        label: str,
        placeholder: str,
        setting_key: str,
        help_url: Optional[str] = None,
        help_text: Optional[str] = None,
    ) -> Tuple[QVBoxLayout, QLineEdit]:
        """Create an API key input field with password toggle and help link."""
        layout = QVBoxLayout()
        layout.setSpacing(5)

        layout.addWidget(QLabel(label))

        input_layout = QHBoxLayout()
        input_layout.setSpacing(5)

        api_key_input = QLineEdit()
        api_key_input.setPlaceholderText(placeholder)
        api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        current_key = self.settings.value(setting_key, "", type=str)
        api_key_input.setText(current_key)

        toggle_btn = QPushButton("Show")
        toggle_btn.clicked.connect(
            lambda: SettingsDialog._toggle_api_key_visibility(api_key_input, toggle_btn)
        )

        input_layout.addWidget(api_key_input)
        input_layout.addWidget(toggle_btn)
        layout.addLayout(input_layout)

        accent_color = self.settings.value("accent_color", "#C06C84")
        if help_url:
            help_label = QLabel(
                f'<a href="{help_url}" style="color: {accent_color};">Get API key</a>'
            )
            help_label.setOpenExternalLinks(True)
            layout.addWidget(help_label)
        elif help_text:
            help_label = QLabel(help_text)
            help_label.setStyleSheet("color: #888888; font-size: 11px;")
            layout.addWidget(help_label)

        return layout, api_key_input

    @staticmethod
    def _toggle_api_key_visibility(
        input_field: QLineEdit, toggle_btn: QPushButton
    ) -> None:
        """Toggle API key visibility."""
        if input_field.echoMode() == QLineEdit.EchoMode.Password:
            input_field.setEchoMode(QLineEdit.EchoMode.Normal)
            toggle_btn.setText("Hide")
        else:
            input_field.setEchoMode(QLineEdit.EchoMode.Password)
            toggle_btn.setText("Show")

    def _create_assela_tab(self) -> None:
        """Create the ASSella settings tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)

        group = QGroupBox("ASSella Core Settings")
        group_layout = QVBoxLayout()

        self.smart_depot_selection_checkbox = create_checkbox_setting(
            "Smart Selection",
            "smart_depot_selection",
            False,
            self,
            "Automatically reuse previously chosen depots on update, unless a brand new depot is added.",
        )
        group_layout.addWidget(self.smart_depot_selection_checkbox)

        self.autofetch_manifests_checkbox = create_checkbox_setting(
            "Auto-fetch update manifests on boot",
            "autofetch_manifests_on_boot",
            False,
            self,
            "Pre-download manifest zip files in the background on startup for all games needing updates.",
        )
        group_layout.addWidget(self.autofetch_manifests_checkbox)

        self.use_lancache_checkbox = create_checkbox_setting(
            "Enable LanCache Detection",
            "use_lancache",
            False,
            self,
            "Direct DepotDownloader downloads through a local LanCache server if detected on the local network (speeds up LAN downloads).",
        )
        group_layout.addWidget(self.use_lancache_checkbox)

        group.setLayout(group_layout)
        layout.addWidget(group)
        
        # Rollback / Manifest Backups Group
        rollback_group = QGroupBox("Manifest Rollback Settings")
        rollback_layout = QVBoxLayout()
        
        self.save_old_manifests_checkbox = QCheckBox("Keep old manifests (Rollback)")
        self.save_old_manifests_checkbox.setToolTip("Save older manifest versions to allow rolling back to previous builds.")
        # Robustly parse bool from QSettings — type=bool can silently fail on
        # Linux when the stored value is the string "true"/"false".
        _som_raw = self.settings.value("save_old_manifests", True)
        if isinstance(_som_raw, str):
            _som_val = _som_raw.lower() in ("true", "1", "yes")
        else:
            _som_val = bool(_som_raw)
        self.save_old_manifests_checkbox.setChecked(_som_val)
        rollback_layout.addWidget(self.save_old_manifests_checkbox)
        
        limit_layout = QHBoxLayout()
        rollback_limit_label = QLabel("Max to keep:")
        rollback_limit_label.setToolTip("Maximum number of older manifests to keep per game.")
        self.max_old_manifests_spinbox = QSpinBox()
        self.max_old_manifests_spinbox.setRange(1, 100)
        try:
            current_rollback_max = int(self.settings.value("max_old_manifests", 3))
        except (ValueError, TypeError):
            current_rollback_max = 3
        self.max_old_manifests_spinbox.setValue(current_rollback_max)
        
        limit_layout.addWidget(rollback_limit_label)
        limit_layout.addWidget(self.max_old_manifests_spinbox)
        limit_layout.addStretch()
        rollback_layout.addLayout(limit_layout)
        
        rollback_group.setLayout(rollback_layout)
        # layout.addWidget(rollback_group)

        # Experimental Group
        experimental_group = QGroupBox("Experimental")
        experimental_layout = QVBoxLayout()

        self.isp_bypass_hubcap_checkbox = create_checkbox_setting(
            "ISP Bypass (Hubcap API)",
            "isp_bypass_hubcap",
            False,
            self,
            "Bypasses ISP DNS blocking/censorship for Hubcap API requests using DoH (DNS-over-HTTPS) with automatic background Tor helper fallback.",
        )
        self.isp_bypass_hubcap_checkbox.stateChanged.connect(self._on_isp_bypass_toggled)
        experimental_layout.addWidget(self.isp_bypass_hubcap_checkbox)
        experimental_group.setLayout(experimental_layout)
        layout.addWidget(experimental_group)

        layout.addStretch()

        # ── Uninstall (Linux only) ────────────────────────────────────────
        if sys.platform != "win32":
            uninstall_btn = QPushButton("Uninstall ASSella")
            uninstall_btn.setToolTip("Remove ASSella and optionally restore the original ACCELA.")
            uninstall_btn.setStyleSheet("color: #cc4444;")
            uninstall_btn.clicked.connect(self.uninstall_assela)
            layout.addWidget(uninstall_btn)

        self.tab_widget.addTab(tab, "ASSella")

    def _create_downloads_tab(self) -> None:
        """Create the Downloads settings tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)

        # Download Settings Group
        dl_group = QGroupBox("Download Settings")
        dl_layout = QVBoxLayout()

        library_tooltip = "Detect Steam libraries and let you choose where to install games."
        if sys.platform == "linux":
            library_tooltip += " On Linux, this also enables SLSsteam integration for those installs."

        self.library_mode_checkbox = create_checkbox_setting(
            "Limit Downloads to Steam Libraries",
            "library_mode",
            False,
            self,
            library_tooltip,
        )
        dl_layout.addWidget(self.library_mode_checkbox)

        self.auto_skip_single_choice_checkbox = create_checkbox_setting(
            "Skip single-choice selection",
            "auto_skip_single_choice",
            False,
            self,
            "Automatically skip selection when only one option exists.",
        )
        dl_layout.addWidget(self.auto_skip_single_choice_checkbox)

        self.hide_macos_depots_checkbox = create_checkbox_setting(
            "Hide macOS depots in depot selection",
            "hide_macos_depots",
            True,
            self,
            "Hide macOS platform depots to reduce clutter.",
        )
        dl_layout.addWidget(self.hide_macos_depots_checkbox)

        # Soundtrack filtering
        self.filter_soundtracks_checkbox = create_checkbox_setting(
            "Filter Soundtracks and OSTs from Depots",
            "filter_soundtracks",
            True,
            self,
            "Filter out soundtrack and OST depots when downloading game files.",
        )
        dl_layout.addWidget(self.filter_soundtracks_checkbox)

        # Search blacklist filtering
        self.filter_search_blacklist_checkbox = create_checkbox_setting(
            "Filter Blacklisted Keywords in Search",
            "filter_search_blacklist",
            False,
            self,
            "Hide soundtracks, artbooks, tools, and demos from manifest search results.",
        )
        dl_layout.addWidget(self.filter_search_blacklist_checkbox)

        # Default Download Location
        dl_dir_layout = QHBoxLayout()
        dl_dir_label = QLabel("Default Download Location:")
        dl_dir_label.setToolTip("Direct downloads to this folder/library instead of prompting for every game.")
        
        self.dl_location_combo = QComboBox()
        self.dl_location_combo.addItem("Ask Every Time", "")
        
        # Load detected Steam libraries
        from core import steam_helpers
        detected_libs = steam_helpers.get_steam_libraries()
        for lib in detected_libs:
            self.dl_location_combo.addItem(lib, lib)
            
        self.dl_location_combo.addItem("Custom Folder...", "custom")
        
        # Load saved value
        current_val = self.settings.value("default_download_directory", "")
        if not current_val:
            self.dl_location_combo.setCurrentIndex(0)
        elif current_val in detected_libs:
            idx = self.dl_location_combo.findData(current_val)
            if idx >= 0:
                self.dl_location_combo.setCurrentIndex(idx)
        else:
            # Custom folder path
            self.dl_location_combo.insertItem(1, current_val, current_val)
            self.dl_location_combo.setCurrentIndex(1)
            
        def on_dl_location_changed(index):
            data = self.dl_location_combo.itemData(index)
            if data == "custom":
                path = QFileDialog.getExistingDirectory(self, "Select Custom Download Location")
                if path:
                    existing_idx = self.dl_location_combo.findData(path)
                    if existing_idx >= 0:
                        self.dl_location_combo.setCurrentIndex(existing_idx)
                    else:
                        # Insert custom path before the "Custom Folder..." item
                        insert_pos = self.dl_location_combo.count() - 1
                        self.dl_location_combo.insertItem(insert_pos, path, path)
                        self.dl_location_combo.setCurrentIndex(insert_pos)
                else:
                    # Cancelled, revert to first item
                    self.dl_location_combo.setCurrentIndex(0)
                    
        self.dl_location_combo.currentIndexChanged.connect(on_dl_location_changed)
        
        dl_dir_layout.addWidget(dl_dir_label)
        dl_dir_layout.addWidget(self.dl_location_combo, 1)
        dl_layout.addLayout(dl_dir_layout)

        # Max Downloads & Update Check Interval in a compact horizontal layout
        spin_layout = QHBoxLayout()
        
        max_dl_label = QLabel("Concurrent Downloads:")
        max_dl_label.setToolTip("Set maximum concurrent downloads (1-30). Lower values (e.g. 1-2) reduce network usage.")
        self.max_downloads_spinbox = QSpinBox()
        self.max_downloads_spinbox.setRange(1, 30)
        current_max = self.settings.value("max_downloads", 4, type=int)
        if current_max < 1 or current_max > 30:
            current_max = 4
        self.max_downloads_spinbox.setValue(current_max)
        
        update_interval_label = QLabel("Update Interval (mins):")
        update_interval_label.setToolTip("Set how often to check for game updates in minutes. Set to 0 to disable automatic checks.")
        self.update_interval_spinbox = QSpinBox()
        self.update_interval_spinbox.setRange(0, 1440)
        current_interval = self.settings.value("update_check_interval_minutes", 5, type=int)
        self.update_interval_spinbox.setValue(current_interval)
        
        spin_layout.addWidget(max_dl_label)
        spin_layout.addWidget(self.max_downloads_spinbox)
        spin_layout.addSpacing(20)
        spin_layout.addWidget(update_interval_label)
        spin_layout.addWidget(self.update_interval_spinbox)
        spin_layout.addStretch()
        dl_layout.addLayout(spin_layout)

        self.check_updates_on_boot_checkbox = create_checkbox_setting(
            "Check Updates on Boot",
            "check_updates_on_boot",
            True,
            self,
            "Automatically check for game updates in the background on startup."
        )
        dl_layout.addWidget(self.check_updates_on_boot_checkbox)

        dl_group.setLayout(dl_layout)
        layout.addWidget(dl_group)

        # Post-Processing Group
        pp_group = QGroupBox("Post-Processing")
        pp_layout = QVBoxLayout()

        self.achievements_checkbox = create_checkbox_setting(
            "Generate Achievements (Recommended Off)",
            "generate_achievements",
            False,
            self,
            "After 07/11/2026 update of SLSsteam, achievements are generated by SLS by default.",
        )
        self.achievements_checkbox.stateChanged.connect(self._update_achievements_button_state)
        pp_layout.addWidget(self.achievements_checkbox)

        # Steamless DRM Remover Combobox
        drm_layout = QHBoxLayout()
        drm_label = QLabel("Steamless DRM Remover:")
        drm_label.setToolTip("Select the method to automatically remove Steam DRM from game executables.")
        
        self.steamless_remover_combo = QComboBox()
        self.steamless_remover_combo.addItem("Disabled", "disabled")
        self.steamless_remover_combo.addItem("Steamless AIO (Built-in)", "aio")
        self.steamless_remover_combo.addItem("Steamless CLI (WINE/Proton)", "cli")
        
        # Load saved DRM Remover mode
        use_aio = self.settings.value("use_steamless_aio", True, type=bool)
        use_cli = self.settings.value("use_steamless", False, type=bool)
        
        if use_aio:
            self.steamless_remover_combo.setCurrentIndex(1)
        elif use_cli:
            self.steamless_remover_combo.setCurrentIndex(2)
        else:
            self.steamless_remover_combo.setCurrentIndex(0)
            
        drm_layout.addWidget(drm_label)
        drm_layout.addWidget(self.steamless_remover_combo, 1)
        pp_layout.addLayout(drm_layout)

        pp_group.setLayout(pp_layout)
        layout.addWidget(pp_group)

        # Workshop Downloader Settings Group
        ws_group = QGroupBox("Workshop Downloader Settings")
        ws_layout = QVBoxLayout()

        self.workshop_steam_checkbox = create_checkbox_setting(
            "Enable Steam Integration for Workshop Downloads",
            "workshop_steam_enabled",
            True,
            self,
            "Directs workshop downloads to your detected Steam library directories.",
        )
        ws_layout.addWidget(self.workshop_steam_checkbox)

        # Max downloads & Cell ID row
        ws_row = QHBoxLayout()
        ws_row.addWidget(QLabel("Max Concurrent Workshop Downloads:"))
        self.workshop_max_dl_spinbox = QSpinBox()
        self.workshop_max_dl_spinbox.setRange(1, 30)
        current_ws_max = self.settings.value("workshop_max_downloads", 4, type=int)
        self.workshop_max_dl_spinbox.setValue(current_ws_max if 1 <= current_ws_max <= 30 else 4)
        ws_row.addWidget(self.workshop_max_dl_spinbox)
        ws_row.addSpacing(20)

        ws_row.addWidget(QLabel("Cell ID:"))
        self.workshop_cell_id_input = QLineEdit()
        self.workshop_cell_id_input.setPlaceholderText("Optional")
        self.workshop_cell_id_input.setText(self.settings.value("workshop_cell_id", "", type=str))
        self.workshop_cell_id_input.setFixedWidth(100)
        ws_row.addWidget(self.workshop_cell_id_input)
        ws_row.addStretch()

        ws_layout.addLayout(ws_row)
        ws_group.setLayout(ws_layout)
        layout.addWidget(ws_group)

        layout.addStretch()
        self.tab_widget.addTab(tab, "Downloads")

    def goldberg_checked_warning(self) -> None:
        """Warn when Goldberg is enabled alongside Steam integration."""
        checkbox = self.auto_apply_goldberg_checkbox
        if not checkbox.isChecked():
            return

        integration_enabled = (
            self.sls_mode_checkbox.isChecked()
            if self.sls_mode_checkbox is not None
            else is_slssteam_mode_enabled()
        )
        if not integration_enabled:
            return

        warning = "You are about to enable Goldberg integration which is meant to be able to play your downloaded games WITHOUT Steam. If you are going to use Steam to play your games keep this disabled, otherwise things will break. You have been warned. Continue?"

        if self.goldberg_warning_box(checkbox, warning):
            return

    def goldberg_checked_warning_from_mode(self, type) -> None:
        """Warn when Steam integration is enabled while Goldberg is active."""
        checkbox = self.sls_mode_checkbox
        if not checkbox.isChecked():
            return
        try:
            if not self.auto_apply_goldberg_checkbox.isChecked():
                return
        except AttributeError:
            if not self.settings.value("auto_apply_goldberg", False):
                return

        warning = f"You are about to enable {type} integration which is meant to be able to play your downloaded games WITH Steam. But you have Goldberg enabled, which is meant to be able to play your games WITHOUT Steam, if you are going to use Steam to play your games disable Goldberg in settings."

        if self.goldberg_warning_box(checkbox, warning):
            return

    def goldberg_warning_box(self, checkbox, warning) -> bool:
        # First
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Warning")
        msg_box.setText(warning)
        msg_box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msg_box.setDefaultButton(QMessageBox.StandardButton.No)
        reply = msg_box.exec()

        if reply == QMessageBox.StandardButton.No:
            checkbox.setChecked(False)
            checkbox.checkbox.setCheckState(Qt.CheckState.Unchecked)
            return True

        # Second
        confirm_box = QMessageBox(self)
        confirm_box.setWindowTitle("Warning")
        confirm_box.setText(warning + " \n\nAre you sure?")
        confirm_box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        confirm_box.setDefaultButton(QMessageBox.StandardButton.No)
        second_reply = confirm_box.exec()

        if second_reply == QMessageBox.StandardButton.No:
            checkbox.setChecked(False)
            checkbox.checkbox.setCheckState(Qt.CheckState.Unchecked)
            return True

        return False

    def _create_morrenus_tab(self) -> None:
        """Create the Morrenus API settings tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)

        # API Keys Group
        key_group = QGroupBox("API Keys")
        key_layout = QVBoxLayout()
        key_layout.setSpacing(10)

        morrenus_layout, self.api_key_input = self._create_api_key_setting(
            "Hubcap API Key:",
            "Paste your Hubcap API key",
            "morrenus_api_key",
            help_url="https://hubcapmanifest.com/",
        )
        key_layout.addLayout(morrenus_layout)

        key_group.setLayout(key_layout)
        layout.addWidget(key_group)

        # Proxy Settings Group
        proxy_group = QGroupBox("Wirecutter Proxy (ISP Bypass)")
        proxy_layout = QVBoxLayout()
        proxy_layout.setSpacing(10)

        self.use_wirecutter_checkbox = create_checkbox_setting(
            "Use Wirecutter Proxy",
            "use_wirecutter",
            False,
            self,
            "Bypass ISP blocks by proxying Hubcap API requests through a Cloudflare Worker."
        )
        proxy_layout.addWidget(self.use_wirecutter_checkbox)

        # Proxy URL input
        url_layout = QHBoxLayout()
        url_layout.setSpacing(5)
        url_layout.addWidget(QLabel("Proxy URL:"))
        self.wirecutter_url_input = QLineEdit()
        self.wirecutter_url_input.setPlaceholderText("https://your-worker.workers.dev")
        self.wirecutter_url_input.setEchoMode(QLineEdit.EchoMode.Password)
        current_url = self.settings.value("wirecutter_url", "https://rapid-thunder-fba1wirecutter.7ucking.workers.dev", type=str)
        self.wirecutter_url_input.setText(current_url)
        url_layout.addWidget(self.wirecutter_url_input)

        self.show_url_btn = QPushButton("Show")
        self.show_url_btn.clicked.connect(self._toggle_proxy_url_visibility)
        url_layout.addWidget(self.show_url_btn)

        proxy_layout.addLayout(url_layout)

        # Connect checkbox to toggle URL editability
        self.wirecutter_url_input.setEnabled(self.use_wirecutter_checkbox.isChecked())
        self.use_wirecutter_checkbox.checkbox.toggled.connect(self.wirecutter_url_input.setEnabled)

        proxy_group.setLayout(proxy_layout)
        # layout.addWidget(proxy_group)

        # Stats Group
        stats_group = QGroupBox("Hubcap Stats")
        stats_layout = QVBoxLayout()
        stats_layout.setContentsMargins(5, 10, 5, 10)

        self.morrenus_stats_widget = MorrenusStatsWidget()
        stats_layout.addWidget(self.morrenus_stats_widget)

        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        layout.addStretch()

        # Connect tab change for lazy loading stats
        self.morrenus_tab_initialized = False
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        self.tab_widget.addTab(tab, "Integrations")

    def _toggle_proxy_url_visibility(self) -> None:
        """Toggles the visibility of the Wirecutter proxy URL, prompting with confirmation on show."""
        if self.wirecutter_url_input.echoMode() == QLineEdit.EchoMode.Password:
            reply = QMessageBox.question(
                self,
                "Show Proxy URL",
                "Warning: Exposing your proxy URL could lead to third-party abuse and exhaust your daily request limits.\n\nAre you sure you want to show it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.wirecutter_url_input.setEchoMode(QLineEdit.EchoMode.Normal)
                self.show_url_btn.setText("Hide")
        else:
            self.wirecutter_url_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_url_btn.setText("Show")

    def _on_tab_changed(self, index: int) -> None:
        """Handle tab change events."""
        if (
            self.tab_widget.tabText(index) == "Integrations"
            and not self.morrenus_tab_initialized
        ):
            self.morrenus_tab_initialized = True
            QTimer.singleShot(100, self.morrenus_stats_widget.refresh_stats)



    def _create_tools_tab(self) -> None:
        """Create the Tools settings tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)

        # Tools Group
        tools_group = QGroupBox("Tools")
        tools_layout = QVBoxLayout()

        self.configure_achievements_btn = SettingsDialog._add_tool_button(
            tools_layout,
            "Configure Achievements",
            "Perform one-time setup and authenticate Steam for achievements.",
            self.run_schema_grabber_manually,
        )

        SettingsDialog._add_tool_button(
            tools_layout,
            "Remove DRM",
            "Run Steamless manually on a game .exe.",
            self.run_steamless_manually,
        )

        SettingsDialog._add_tool_button(
            tools_layout,
            "Remove DRM (AIO)",
            "Run Steamless-AIO manually on a game .exe.",
            self.run_steamless_aio_manually,
        )

        self.download_slssteam_button = QPushButton("Open SLSsteam installer")
        self.download_slssteam_button.setToolTip(
            "Open the recommended SLSsteam installer page (GitHub)."
        )
        self.download_slssteam_button.clicked.connect(self.download_slssteam)

        tools_group.setLayout(tools_layout)
        layout.addWidget(tools_group)



        # Windows Registry Group
        if sys.platform == "win32":
            reg_group = QGroupBox("Windows Registry")
            reg_layout = QVBoxLayout()

            SettingsDialog._add_tool_button(
                reg_layout,
                "Register Registry Entries",
                "Register accela:// URL protocol and .zip context menu entries.",
                SettingsDialog.register_registry_entries,
            )

            SettingsDialog._add_tool_button(
                reg_layout,
                "Remove Registry Entries",
                "Remove accela:// URL protocol and .zip context menu entries.",
                SettingsDialog.remove_registry_entries,
            )

            reg_group.setLayout(reg_layout)
            layout.addWidget(reg_group)

        layout.addStretch()

        # ── Logging Configuration ─────────────────────────────────────────
        log_group = QGroupBox("Logging Configuration")
        log_layout = QVBoxLayout()

        level_row = QHBoxLayout()
        level_label = QLabel("Log Level:")
        level_label.setToolTip(
            "Minimum severity of messages to log.\n"
            "Select NONE to disable all logging (improves performance)."
        )
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "NONE"])
        _current_level = self.settings.value("log_filter_level", "DEBUG") or "DEBUG"
        idx = self.log_level_combo.findText(_current_level)
        self.log_level_combo.setCurrentIndex(idx if idx >= 0 else 0)
        level_row.addWidget(level_label)
        level_row.addWidget(self.log_level_combo)
        level_row.addStretch()
        log_layout.addLayout(level_row)

        cat_row = QHBoxLayout()
        cat_label = QLabel("Log Filter:")
        cat_label.setToolTip("Restrict logs to a specific module group.")
        self.log_category_combo = QComboBox()
        self.log_category_combo.addItems([
            "All Modules",
            "Only Steam Client & API",
            "Only Downloads & Manifests",
            "Only Database & Library",
        ])
        _current_cat = self.settings.value("log_filter_category", "All Modules") or "All Modules"
        cat_idx = self.log_category_combo.findText(_current_cat)
        self.log_category_combo.setCurrentIndex(cat_idx if cat_idx >= 0 else 0)
        cat_row.addWidget(cat_label)
        cat_row.addWidget(self.log_category_combo)
        cat_row.addStretch()
        log_layout.addLayout(cat_row)

        _log_note = QLabel(
            "Changes take effect immediately when you click OK."
        )
        _log_note.setStyleSheet("color: #888888; font-size: 11px;")
        _log_note.setWordWrap(True)
        log_layout.addWidget(_log_note)

        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

        layout.addStretch()
        self.tab_widget.addTab(tab, "Tools")



    # ── ASSella Manager helpers ───────────────────────────────────────────

    def uninstall_assela(self) -> None:
        """Remove ASSella and optionally restore the original ACCELA backup."""
        install_dir = os.path.expanduser("~/.local/share/ACCELA")
        assela_path = os.path.join(install_dir, "ASSella.AppImage")
        symlink_path = os.path.join(install_dir, "ACCELA.AppImage")
        backup_path = os.path.join(install_dir, "ACCELA.AppImage.bak")
        desktop_entry = os.path.expanduser("~/.local/share/applications/accela.desktop")
        has_backup = os.path.isfile(backup_path)

        # Step 1 — Confirm uninstall
        reply = QMessageBox.question(
            self,
            "Uninstall ASSella",
            "This will remove ASSella and revert the desktop shortcut to ACCELA.\n\nAre you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Step 2 — Offer to restore ACCELA backup (if one exists)
        restore = False
        if has_backup:
            restore_reply = QMessageBox.question(
                self,
                "Restore Original ACCELA?",
                "A backup of the original ACCELA (ACCELA.AppImage.bak) was found.\n\n"
                "Would you like to restore it after uninstalling ASSella?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            restore = restore_reply == QMessageBox.StandardButton.Yes

        errors = []

        # Remove symlink
        if os.path.islink(symlink_path):
            try:
                os.remove(symlink_path)
            except OSError as e:
                errors.append(f"Could not remove symlink: {e}")

        # Remove ASSella AppImage
        if os.path.isfile(assela_path):
            try:
                os.remove(assela_path)
            except OSError as e:
                errors.append(f"Could not remove ASSella.AppImage: {e}")

        # Remove image cache if it exists
        cache_dir = os.path.join(install_dir, "image_cache")
        if os.path.exists(cache_dir):
            try:
                shutil.rmtree(cache_dir)
            except OSError as e:
                errors.append(f"Could not remove image cache directory: {e}")

        # Restore backup if requested
        if restore:
            try:
                shutil.copy2(backup_path, symlink_path)
                os.chmod(symlink_path, 0o755)
            except OSError as e:
                errors.append(f"Could not restore ACCELA backup: {e}")

        # Revert desktop entry name
        if os.path.isfile(desktop_entry):
            try:
                with open(desktop_entry, "r") as f:
                    content = f.read()
                content = content.replace("Name=ASSella", "Name=ACCELA")
                with open(desktop_entry, "w") as f:
                    f.write(content)
                try:
                    subprocess.run(
                        ["update-desktop-database", os.path.dirname(desktop_entry)],
                        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    pass
            except OSError as e:
                errors.append(f"Could not update desktop entry: {e}")

        if errors:
            QMessageBox.warning(self, "Uninstall — Partial", "\n".join(errors))
        else:
            msg = "ASSella has been uninstalled."
            if restore:
                msg += "\nOriginal ACCELA has been restored."
            QMessageBox.information(self, "Done", msg)


    @staticmethod
    def _add_tool_button(layout: QVBoxLayout, text: str, tooltip: str, slot) -> QPushButton:
        """Helper to add a tool button with explanation text."""
        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.clicked.connect(slot)
        layout.addWidget(btn)
        SettingsDialog._add_tool_explanation(layout, tooltip)
        return btn

    @staticmethod
    def _add_tool_explanation(layout: QVBoxLayout, text: str) -> None:
        """Helper to add explanation label."""
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #888888; font-size: 11px;")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)



    def _create_style_tab(self) -> None:
        """Create the Theme settings tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)

        desc_lbl = QLabel("Customize the visual appearance, accent colors, and font settings of ASSella.")
        desc_lbl.setStyleSheet("color: #a0a0ab; font-size: 11px; margin-bottom: 5px;")
        layout.addWidget(desc_lbl)

        # Colors & Font combined in a neat group
        theme_group = QGroupBox("Theme")
        theme_layout = QGridLayout()
        theme_layout.setContentsMargins(15, 15, 15, 15)
        theme_layout.setSpacing(10)

        # Accent color swatch row
        theme_layout.addWidget(QLabel("Accent Color:"), 0, 0)
        self.accent_color_button = QPushButton()
        self.accent_color_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.accent_color_button.setFixedSize(60, 24)
        self.accent_color_button.setStyleSheet(
            f"background-color: {self._user_accent_color}; border: 1px solid #444; border-radius: 4px;"
        )
        self.accent_reset_button = QPushButton("Reset")
        self.accent_reset_button.setFixedWidth(70)
        theme_layout.addWidget(self.accent_color_button, 0, 1)
        theme_layout.addWidget(self.accent_reset_button, 0, 2)

        # Background color swatch row
        theme_layout.addWidget(QLabel("Background Color:"), 1, 0)
        self.bg_color_button = QPushButton()
        self.bg_color_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bg_color_button.setFixedSize(60, 24)
        self.bg_color_button.setStyleSheet(
            f"background-color: {self._user_background_color}; border: 1px solid #444; border-radius: 4px;"
        )
        self.bg_reset_button = QPushButton("Reset")
        self.bg_reset_button.setFixedWidth(70)
        theme_layout.addWidget(self.bg_color_button, 1, 1)
        theme_layout.addWidget(self.bg_reset_button, 1, 2)

        # Font row
        font_children, self.font_button, self.font_reset_button = create_font_setting(self)
        self.font_button.clicked.connect(self.choose_font)
        self.font_reset_button.clicked.connect(self.reset_font)

        self.font_button.setMinimumWidth(150)
        self.font_reset_button.setFixedWidth(70)

        theme_layout.addWidget(QLabel("System Font:"), 2, 0)
        theme_layout.addWidget(self.font_button, 2, 1)
        theme_layout.addWidget(self.font_reset_button, 2, 2)

        # Material presets row
        theme_layout.addWidget(QLabel("Material Presets:"), 3, 0)
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Custom (Select Color)", "custom")
        self.preset_combo.addItem("Ocean Breeze (Monet Blue)", "ocean")
        self.preset_combo.addItem("Forest Sage (Mint Green)", "forest")
        self.preset_combo.addItem("Lavender Mist (Orchid Purple)", "lavender")
        self.preset_combo.setMinimumWidth(150)

        saved_preset = self.settings.value("material_preset", "ocean", type=str)
        idx = self.preset_combo.findData(saved_preset)
        if idx != -1:
            self.preset_combo.setCurrentIndex(idx)

        self.preset_combo.currentIndexChanged.connect(self.on_preset_changed)
        theme_layout.addWidget(self.preset_combo, 3, 1, 1, 2)

        self.accent_color_button.clicked.connect(self.choose_accent_color)
        self.accent_reset_button.clicked.connect(self.reset_accent_color)
        self.bg_color_button.clicked.connect(self.choose_bg_color)
        self.bg_reset_button.clicked.connect(self.reset_bg_color)

        theme_group.setLayout(theme_layout)
        layout.addWidget(theme_group)

        # Interface Options Group
        disp_group = QGroupBox("Interface Options")
        disp_layout = QVBoxLayout()
        disp_layout.setContentsMargins(15, 15, 15, 15)
        disp_layout.setSpacing(10)

        self.titlebar_position_checkbox = QCheckBox("Move Titlebar to Window Top")
        is_top = self.settings.value("titlebar_position", "bottom", type=str) == "top"
        self.titlebar_position_checkbox.setChecked(is_top)
        self.titlebar_position_checkbox.setToolTip("Places the navigation bar / titlebar at the top of the window instead of the bottom.")
        self.titlebar_position_checkbox.stateChanged.connect(self.on_titlebar_position_changed)
        disp_layout.addWidget(self.titlebar_position_checkbox)



        self.remember_origins_checkbox = QCheckBox("Remember your origins")
        is_origins = self.settings.value("remember_origins", False, type=bool)
        self.remember_origins_checkbox.setChecked(is_origins)
        self.remember_origins_checkbox.setToolTip("Subtly displays the Wired layout background.")
        self.remember_origins_checkbox.stateChanged.connect(self._on_origins_toggled)
        disp_layout.addWidget(self.remember_origins_checkbox)

        self.simplify_denuvo_status_checkbox = QCheckBox("Show hypervisor and uncracked as Not Cracked")
        is_simplify = self.settings.value("simplify_denuvo_status", False, type=bool)
        self.simplify_denuvo_status_checkbox.setChecked(is_simplify)
        self.simplify_denuvo_status_checkbox.setToolTip("Displays both Denuvo Hypervisor and Denuvo Uncracked games as simply Denuvo Uncracked.")
        disp_layout.addWidget(self.simplify_denuvo_status_checkbox)


        disp_group.setLayout(disp_layout)
        layout.addWidget(disp_group)

        layout.addStretch(1)

        self.tab_widget.addTab(tab, "Theme")

    def _create_webui_tab(self) -> None:
        """Create the WebUI settings tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)

        # Web Server Group
        server_group = QGroupBox("Web Server Configuration")
        server_layout = QVBoxLayout()

        self.remote_web_ui_checkbox = create_checkbox_setting(
            "Enable Remote Web UI",
            "enable_remote_web_ui",
            False,
            self,
            "Access and queue updates for your game library from a mobile browser on your local network.",
        )
        server_layout.addWidget(self.remote_web_ui_checkbox)

        # Port Layout
        port_layout = QHBoxLayout()
        port_label = QLabel("Web UI Port:")
        self.web_ui_port_spinbox = QSpinBox()
        self.web_ui_port_spinbox.setRange(1024, 65535)
        self.web_ui_port_spinbox.setValue(self.settings.value("web_ui_port", 8765, type=int))
        self.web_ui_port_spinbox.setFixedWidth(100)

        check_port_btn = QPushButton("Check Availability")
        check_port_btn.clicked.connect(self._check_port_availability)

        port_layout.addWidget(port_label)
        port_layout.addWidget(self.web_ui_port_spinbox)
        port_layout.addWidget(check_port_btn)
        port_layout.addStretch()
        server_layout.addLayout(port_layout)

        server_group.setLayout(server_layout)
        layout.addWidget(server_group)

        # Background Service Group (Linux only)
        if sys.platform != "win32":
            service_group = QGroupBox("Background Service (systemd)")
            service_layout = QVBoxLayout()

            self.service_status_label = QLabel("Background Service: Checking...")
            self.service_boot_label = QLabel("Start on Boot: Checking...")
            service_layout.addWidget(self.service_status_label)
            service_layout.addWidget(self.service_boot_label)

            # Control buttons
            control_layout = QHBoxLayout()
            self.start_service_btn = QPushButton("Start Service")
            self.start_service_btn.clicked.connect(self._start_service)
            self.stop_service_btn = QPushButton("Stop Service")
            self.stop_service_btn.clicked.connect(self._stop_service)
            control_layout.addWidget(self.start_service_btn)
            control_layout.addWidget(self.stop_service_btn)
            service_layout.addLayout(control_layout)

            # Boot buttons
            boot_layout = QHBoxLayout()
            self.enable_boot_btn = QPushButton("Enable on Boot")
            self.enable_boot_btn.clicked.connect(self._enable_boot)
            self.disable_boot_btn = QPushButton("Disable on Boot")
            self.disable_boot_btn.clicked.connect(self._disable_boot)
            boot_layout.addWidget(self.enable_boot_btn)
            boot_layout.addWidget(self.disable_boot_btn)
            service_layout.addLayout(boot_layout)

            service_group.setLayout(service_layout)
            layout.addWidget(service_group)

            self.service_poll_timer = QTimer(self)
            self.service_poll_timer.timeout.connect(self._update_service_status)
            self.service_poll_timer.start(2000)
            self._update_service_status()

        layout.addStretch()
        self.tab_widget.addTab(tab, "WebUI")

    def _check_port_availability(self) -> None:
        """Check if the configured port is open/available for use."""
        port = self.web_ui_port_spinbox.value()
        
        is_our_running_port = False
        if self.main_window and hasattr(self.main_window, "web_server_manager"):
            if self.main_window.web_server_manager.is_running():
                if self.main_window.web_server_manager.server.port == port:
                    is_our_running_port = True

        if is_our_running_port:
            QMessageBox.information(
                self,
                "Port Check",
                f"Port {port} is currently in use by this instance of ASSella (Active)."
            )
            return

        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            s.close()
            QMessageBox.information(
                self,
                "Port Check",
                f"Port {port} is free and available!"
            )
        except OSError:
            QMessageBox.warning(
                self,
                "Port Check",
                f"Port {port} is already in use by another application or the background service."
            )

    def _get_service_status(self) -> str:
        """Return 'running', 'stopped', 'not_installed', or 'unknown'."""
        try:
            service_path = os.path.expanduser("~/.config/systemd/user/assella-testing.service")
            if not os.path.exists(service_path):
                return "not_installed"

            res = subprocess.run(
                ["systemctl", "--user", "is-active", "assella-testing.service"],
                capture_output=True,
                text=True,
            )
            status = res.stdout.strip()
            if status == "active":
                return "running"
            else:
                return "stopped"
        except Exception as e:
            logger.error(f"Error checking service status: {e}")
            return "unknown"

    def _is_service_enabled(self) -> bool:
        """Return True if enabled to start on boot."""
        try:
            res = subprocess.run(
                ["systemctl", "--user", "is-enabled", "assella-testing.service"],
                capture_output=True,
                text=True,
            )
            return res.stdout.strip() == "enabled"
        except Exception:
            return False

    def _update_service_status(self) -> None:
        """Update systemd status label and enable/disable control buttons."""
        if sys.platform == "win32":
            return

        status = self._get_service_status()
        enabled = self._is_service_enabled()

        if status == "running":
            self.service_status_label.setText("Background Service: <font color='#44cc44'>Active (Running)</font>")
            self.start_service_btn.setEnabled(False)
            self.stop_service_btn.setEnabled(True)
        elif status == "stopped":
            self.service_status_label.setText("Background Service: <font color='#cc4444'>Inactive (Stopped)</font>")
            self.start_service_btn.setEnabled(True)
            self.stop_service_btn.setEnabled(False)
        elif status == "not_installed":
            self.service_status_label.setText("Background Service: <font color='#888888'>Not Configured / Installed</font>")
            self.start_service_btn.setEnabled(False)
            self.stop_service_btn.setEnabled(False)
        else:
            self.service_status_label.setText("Background Service: Unknown Status")
            self.start_service_btn.setEnabled(False)
            self.stop_service_btn.setEnabled(False)

        if status != "not_installed":
            self.enable_boot_btn.setEnabled(not enabled)
            self.disable_boot_btn.setEnabled(enabled)
            boot_text = "Enabled" if enabled else "Disabled"
            self.service_boot_label.setText(f"Start on Boot: <b>{boot_text}</b>")
        else:
            self.enable_boot_btn.setEnabled(False)
            self.disable_boot_btn.setEnabled(False)
            self.service_boot_label.setText("Start on Boot: N/A")

        if self.main_window and hasattr(self.main_window, "_update_web_ui_status_label"):
            self.main_window._update_web_ui_status_label()


    def _start_service(self) -> None:
        """Start the systemd user service."""
        try:
            subprocess.run(["systemctl", "--user", "start", "assella-testing.service"])
            self._update_service_status()
        except Exception as e:
            QMessageBox.critical(self, "Service Error", f"Failed to start service: {e}")

    def _stop_service(self) -> None:
        """Stop the systemd user service."""
        try:
            subprocess.run(["systemctl", "--user", "stop", "assella-testing.service"])
            self._update_service_status()
        except Exception as e:
            QMessageBox.critical(self, "Service Error", f"Failed to stop service: {e}")

    def _enable_boot(self) -> None:
        """Enable service on boot."""
        try:
            subprocess.run(["systemctl", "--user", "enable", "assella-testing.service"])
            self._update_service_status()
        except Exception as e:
            QMessageBox.critical(self, "Service Error", f"Failed to enable service on boot: {e}")

    def _disable_boot(self) -> None:
        """Disable service on boot."""
        try:
            subprocess.run(["systemctl", "--user", "disable", "assella-testing.service"])
            self._update_service_status()
        except Exception as e:
            QMessageBox.critical(self, "Service Error", f"Failed to disable service on boot: {e}")

    @staticmethod
    def _add_checkbox_explanation(layout: QVBoxLayout, text: str) -> None:
        """Add indented explanation text for checkboxes."""
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #888888; font-size: 11px;")
        lbl.setWordWrap(True)
        h_layout = QHBoxLayout()
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.addSpacing(14)
        h_layout.addWidget(lbl)
        layout.addLayout(h_layout)

    # Color Handlers
    def choose_accent_color(self) -> None:
        color = QColorDialog.getColor()
        if not color.isValid():
            return
        if SettingsDialog._is_too_dark(color):
            SettingsDialog._show_color_warning()
            return
        hex_c = color.name()
        self.accent_color_button.setStyleSheet(f"background-color: {hex_c};")
        if hasattr(self, "preset_combo"):
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(0)
            self.preset_combo.blockSignals(False)

    def reset_accent_color(self) -> None:
        default = "#C06C84"
        self.settings.setValue("accent_color", default)
        self.accent_color_button.setStyleSheet(f"background-color: {default};")
        if hasattr(self, "preset_combo"):
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(0)
            self.preset_combo.blockSignals(False)

    def choose_bg_color(self) -> None:
        color = QColorDialog.getColor()
        if not color.isValid():
            return
        hex_c = color.name()
        self.bg_color_button.setStyleSheet(f"background-color: {hex_c};")
        if hasattr(self, "preset_combo"):
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(0)
            self.preset_combo.blockSignals(False)

    def reset_bg_color(self) -> None:
        default = "#000000"
        self.settings.setValue("background_color", default)
        self.bg_color_button.setStyleSheet(f"background-color: {default};")
        if hasattr(self, "preset_combo"):
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(0)
            self.preset_combo.blockSignals(False)

    def on_preset_changed(self, index: int) -> None:
        preset_type = self.preset_combo.itemData(index)
        if preset_type == "custom":
            return

        presets = {
            "ocean": ("#a1c9fd", "#111318"),
            "forest": ("#b1ecbe", "#0f1511"),
            "lavender": ("#e7bdfb", "#141217")
        }

        if preset_type in presets:
            accent_hex, bg_hex = presets[preset_type]
            self.settings.setValue("material_preset", preset_type)

            self.accent_color_button.setStyleSheet(f"background-color: {accent_hex};")
            self.bg_color_button.setStyleSheet(f"background-color: {bg_hex};")

            self.settings.setValue("user_accent_color", accent_hex)
            self.settings.setValue("user_background_color", bg_hex)
            self.settings.setValue("accent_color", accent_hex)
            self.settings.setValue("background_color", bg_hex)

            from PyQt6.QtWidgets import QApplication
            from ui.theme import update_appearance
            app = QApplication.instance()
            if app:
                update_appearance(app, accent_hex, bg_hex)

    @staticmethod
    def _is_too_dark(color: QColor) -> bool:
        brightness = color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114
        return brightness < 15

    @staticmethod
    def _is_too_close(accent: QColor, bg: QColor, threshold: int = 100) -> bool:
        r_diff = bg.red() - accent.red()
        g_diff = bg.green() - accent.green()
        b_diff = bg.blue() - accent.blue()
        return (r_diff**2 + g_diff**2 + b_diff**2) ** 0.5 < threshold

    @staticmethod
    def _show_color_warning() -> None:
        QMessageBox.warning(
            None,
            "Invalid Color",
            "This color is too dark and will make the interface unusable.",
        )

    # Font Handlers
    def choose_font(self) -> None:
        font, ok = QFontDialog.getFont(self.current_font, self)
        if ok:
            self.current_font = font
            self.update_font_button_text()

    def reset_font(self) -> None:
        default = QFont("TrixieCyrG-Plain", 10)
        default.setBold(False)
        default.setItalic(False)
        self.current_font = default
        self.update_font_button_text()

    def update_font_button_text(self) -> None:
        if hasattr(self, "font_button") and hasattr(self, "current_font"):
            fam = self.current_font.family()
            size = self.current_font.pointSize()
            text = f"{fam} {size}pt"
            if self.current_font.bold():
                text += " Bold"
            if self.current_font.italic():
                text += " Italic"
            self.font_button.setText(text)
            self.font_button.setFont(self.current_font)

    # Display Handlers
    def on_titlebar_position_changed(self, state: int) -> None:
        pos = "top" if state == 2 else "bottom"
        self.settings.setValue("titlebar_position", pos)
        if self.main_window and hasattr(self.main_window, "reposition_titlebar"):
            # noinspection PyUnresolvedReferences
            self.main_window.reposition_titlebar(pos)



    def accept(self) -> None:
        """Save all settings and close."""
        try:
            if hasattr(self, "service_poll_timer") and self.service_poll_timer:
                self.service_poll_timer.stop()
            self._save_general_settings()
            self._save_download_settings()
            if not self._save_style_settings():
                return  # Style validation failed
            self.settings.sync()  # Flush to disk immediately
            logger.info("All settings saved.")
            super().accept()
        except Exception as e:
            import traceback
            from PyQt6.QtWidgets import QMessageBox
            logger.error(f"Error saving settings: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(
                self,
                "Error Saving Settings",
                f"An error occurred while saving settings:\n{e}\n\nSee log file for details."
            )

    def _save_general_settings(self) -> None:
        api_key = self.api_key_input.text().strip()
        self.settings.setValue("morrenus_api_key", api_key)
        try:
            self.settings.setValue("use_wirecutter", self.use_wirecutter_checkbox.isChecked())
        except RuntimeError:
            pass
        try:
            self.settings.setValue("wirecutter_url", self.wirecutter_url_input.text().strip())
        except RuntimeError:
            pass
        if hasattr(self, "steam_username_input") and self.steam_username_input:
            self.settings.setValue("steam_username", self.steam_username_input.text().strip())
        if hasattr(self, "steam_password_input") and self.steam_password_input:
            from utils.helpers import encrypt_string
            encrypted_pass = encrypt_string(self.steam_password_input.text())
            self.settings.setValue("steam_password", encrypted_pass)

    def _save_download_settings(self) -> None:
        if self.sls_mode_checkbox is not None:
            self.settings.setValue("slssteam_mode", self.sls_mode_checkbox.isChecked())
        self.settings.setValue(
            "sls_config_management",
            self.sls_config_management_checkbox.isChecked(),
        )
        self.settings.setValue(
            "default_download_directory", self.dl_location_combo.currentData() or ""
        )
        self.settings.setValue("library_mode", self.library_mode_checkbox.isChecked())
        self.settings.setValue(
            "auto_skip_single_choice",
            self.auto_skip_single_choice_checkbox.isChecked(),
        )
        self.settings.setValue(
            "smart_depot_selection",
            self.smart_depot_selection_checkbox.isChecked(),
        )
        self.settings.setValue(
            "autofetch_manifests_on_boot",
            self.autofetch_manifests_checkbox.isChecked(),
        )
        self.settings.setValue(
            "use_lancache",
            self.use_lancache_checkbox.isChecked(),
        )
        self.settings.setValue(
            "prompt_steam_restart",
            self.prompt_steam_restart_checkbox.isChecked(),
        )
        if self.ignore_slssteam_updater_checkbox is not None:
            self.settings.setValue(
                "ignore_slssteam_updater",
                self.ignore_slssteam_updater_checkbox.isChecked(),
            )
        self.settings.setValue(
            "generate_achievements", self.achievements_checkbox.isChecked()
        )

        if self.workshop_steam_checkbox is not None:
            self.settings.setValue(
                "workshop_steam_enabled",
                self.workshop_steam_checkbox.isChecked(),
            )
        if self.workshop_max_dl_spinbox is not None:
            self.settings.setValue(
                "workshop_max_downloads",
                self.workshop_max_dl_spinbox.value(),
            )
        if self.workshop_cell_id_input is not None:
            self.settings.setValue(
                "workshop_cell_id",
                self.workshop_cell_id_input.text().strip(),
            )
        
        # Save Consolidated Steamless DRM Remover settings
        drm_mode = self.steamless_remover_combo.currentData()
        if drm_mode == "aio":
            self.settings.setValue("use_steamless_aio", True)
            self.settings.setValue("use_steamless", False)
        elif drm_mode == "cli":
            self.settings.setValue("use_steamless_aio", False)
            self.settings.setValue("use_steamless", True)
        else:
            self.settings.setValue("use_steamless_aio", False)
            self.settings.setValue("use_steamless", False)

        if hasattr(self, "enable_denuvo_sync_checkbox") and self.enable_denuvo_sync_checkbox is not None:
            self.settings.setValue("enable_denuvo_sync", self.enable_denuvo_sync_checkbox.isChecked())


        # Save Soundtrack and Search Blacklist filtering toggles
        self.settings.setValue("filter_soundtracks", self.filter_soundtracks_checkbox.isChecked())
        self.settings.setValue("filter_search_blacklist", self.filter_search_blacklist_checkbox.isChecked())

        # Check if the toggle changed
        old_val = self.settings.value("fakeappid_db_integration", False, type=bool)
        new_val = self.fakeappid_db_integration_checkbox.isChecked() if self.fakeappid_db_integration_checkbox is not None else False
        self.settings.setValue("fakeappid_db_integration", new_val)

        if old_val != new_val:
            from utils.yaml_config_manager import get_user_config_path
            config_path = get_user_config_path()
            if config_path.exists():
                try:
                    from utils.yaml_config_manager import check_and_merge_fakeappid_db, clean_fakeappid_db
                    if new_val:
                        check_and_merge_fakeappid_db(config_path)
                    else:
                        clean_fakeappid_db(config_path)
                except Exception as ex:
                    logger.error(f"Failed to apply Fake AppID database integration changes: {ex}")

        # Check if Remote Web UI toggle changed
        old_web_ui = self.settings.value("enable_remote_web_ui", False, type=bool)
        new_web_ui = self.remote_web_ui_checkbox.isChecked() if self.remote_web_ui_checkbox is not None else False
        
        old_port = self.settings.value("web_ui_port", 8765, type=int)
        new_port = self.web_ui_port_spinbox.value() if hasattr(self, "web_ui_port_spinbox") and self.web_ui_port_spinbox is not None else old_port
        
        self.settings.setValue("enable_remote_web_ui", new_web_ui)
        self.settings.setValue("web_ui_port", new_port)

        if self.main_window and hasattr(self.main_window, "toggle_web_server"):
            if old_web_ui != new_web_ui:
                if new_web_ui:
                    self.main_window.toggle_web_server(True, port=new_port)
                else:
                    self.main_window.toggle_web_server(False)
            elif new_web_ui and old_port != new_port:
                # Port changed while running -> restart web server on the new port
                self.main_window.toggle_web_server(False)
                self.main_window.toggle_web_server(True, port=new_port)
        
        if hasattr(self, "update_interval_spinbox"):
            self.settings.setValue(
                "update_check_interval_minutes", self.update_interval_spinbox.value()
            )
            if self.main_window and hasattr(self.main_window, "apply_update_timer_settings"):
                self.main_window.apply_update_timer_settings()

        if hasattr(self, "check_updates_on_boot_checkbox"):
            self.settings.setValue(
                "check_updates_on_boot",
                self.check_updates_on_boot_checkbox.isChecked()
            )

        val = 4
        if hasattr(self, "max_downloads_spinbox"):
            try:
                val = max(1, min(30, int(self.max_downloads_spinbox.value())))
            except (ValueError, TypeError):
                pass
        self.settings.setValue("max_downloads", val)

        if hasattr(self, "save_old_manifests_checkbox"):
            try:
                self.settings.setValue("save_old_manifests", self.save_old_manifests_checkbox.isChecked())
            except RuntimeError:
                pass
        if hasattr(self, "max_old_manifests_spinbox"):
            try:
                self.settings.setValue("max_old_manifests", self.max_old_manifests_spinbox.value())
            except RuntimeError:
                pass
        if hasattr(self, "hide_macos_depots_checkbox"):
            try:
                self.settings.setValue("hide_macos_depots", self.hide_macos_depots_checkbox.isChecked())
            except RuntimeError:
                pass
        if hasattr(self, "isp_bypass_hubcap_checkbox") and self.isp_bypass_hubcap_checkbox is not None:
            new_val = self.isp_bypass_hubcap_checkbox.isChecked()
            self.settings.setValue("isp_bypass_hubcap", new_val)
            if not new_val:
                try:
                    from utils.isp_bypass import TorManager
                    TorManager.stop_tor()
                except Exception:
                    pass

    def _on_isp_bypass_toggled(self, state) -> None:
        """Stops background Tor process if user unchecks ISP Bypass."""
        if hasattr(self, "isp_bypass_hubcap_checkbox") and self.isp_bypass_hubcap_checkbox is not None:
            if not self.isp_bypass_hubcap_checkbox.isChecked():
                try:
                    from utils.isp_bypass import TorManager
                    TorManager.stop_tor()
                except Exception as e:
                    logger.warning(f"Error stopping Tor on toggle untick: {e}")

        if hasattr(self, "log_level_combo"):
            self.settings.setValue("log_filter_level", self.log_level_combo.currentText())
        if hasattr(self, "log_category_combo"):
            self.settings.setValue("log_filter_category", self.log_category_combo.currentText())

        # Apply logging changes immediately
        try:
            from utils.logger import update_log_filters
            update_log_filters()
        except Exception:
            pass



    def _save_style_settings(self) -> bool:
        acc_s = self.accent_color_button.styleSheet()
        bg_s = self.bg_color_button.styleSheet()
        u_accent = acc_s.split("background-color: ")[1].split(";")[0]
        u_bg = bg_s.split("background-color: ")[1].split(";")[0]

        self.settings.setValue("user_accent_color", u_accent)
        self.settings.setValue("user_background_color", u_bg)
        if hasattr(self, "preset_combo"):
            preset_type = self.preset_combo.itemData(self.preset_combo.currentIndex())
            self.settings.setValue("material_preset", preset_type)

        prev_mode = self.settings.value("ui_mode", "default")
        applied_accent = u_accent
        applied_bg = u_bg
        self.settings.setValue("font-file", "")



        self.settings.setValue("nerd_mode", False)
        if self.main_window and hasattr(self.main_window, "update_nerd_mode"):
            self.main_window.update_nerd_mode(False)
        if SettingsDialog._is_too_close(QColor(u_accent), QColor(u_bg)):
                QMessageBox.warning(
                    self,
                    "Invalid Color",
                    "Background too similar to accent color.",
                )
                return False

        self.settings.setValue("accent_color", applied_accent)
        self.settings.setValue("background_color", applied_bg)

        self.settings.setValue("font", self.current_font.family())
        self.settings.setValue("font-size", self.current_font.pointSize())

        style = "Normal"
        if self.current_font.bold():
            style = "Bold"
        if self.current_font.italic():
            style = "Italic"
        if self.current_font.bold() and self.current_font.italic():
            style = "Bold Italic"
        self.settings.setValue("font-style", style)

        origins = self.remember_origins_checkbox.isChecked()
        self.settings.setValue("remember_origins", origins)

        if hasattr(self, "simplify_denuvo_status_checkbox") and self.simplify_denuvo_status_checkbox is not None:
            simplify = self.simplify_denuvo_status_checkbox.isChecked()
            self.settings.setValue("simplify_denuvo_status", simplify)

            from PyQt6.QtWidgets import QApplication
            from ui.dialogs.gamelibrary import GameItemWidget
            from ui.dialogs.gamelibrary_v2 import GameDetailsDialogV2
            from ui.dialogs.fetchmanifest import SearchItemWidget
            for w in QApplication.instance().allWidgets():
                if isinstance(w, GameItemWidget):
                    w.update_denuvo_badge()
                    w.update_proton_badge()
                elif isinstance(w, GameDetailsDialogV2):
                    w.update_title()
                elif isinstance(w, SearchItemWidget):
                    w.update_ratings()



        if self.main_window and hasattr(self.main_window, "ui_state"):

            # noinspection PyUnresolvedReferences
            self.main_window.ui_state.apply_style_settings()

        return True

    def _on_origins_toggled(self, state: int) -> None:
        checked = bool(state)
        self.settings.setValue("remember_origins", checked)

        # Stop existing movie/fade
        if self._origins_movie:
            self._origins_movie.stop()
            self._origins_movie = None

        if self._fade_timer:
            self._fade_timer.stop()
            self._fade_timer = None

        if checked:
            gif_path = "/home/deck/.local/share/ACCELA/jumpscare/lain.gif"
            if os.path.exists(gif_path):
                self._origins_movie = QMovie(gif_path)
                self._origins_movie.frameChanged.connect(self.update)
                self._origins_movie.start()

                # Start flash animation (fade from high opacity down to watermark level)
                self._flash_opacity = 0.85
                self._fade_timer = QTimer(self)
                self._fade_timer.timeout.connect(self._fade_origins_opacity)
                self._fade_timer.start(50)
            else:
                self._origins_movie = None
        else:
            self._origins_movie = None

        self.update()

    def _fade_origins_opacity(self) -> None:
        self._flash_opacity = max(0.18, self._flash_opacity - 0.04)
        self.update()
        if self._flash_opacity <= 0.18:
            if self._fade_timer:
                self._fade_timer.stop()
                self._fade_timer = None

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if hasattr(self, "_origins_movie") and self._origins_movie and self._origins_movie.state() == QMovie.MovieState.Running:
            painter = QPainter(self)
            current_pixmap = self._origins_movie.currentPixmap()
            if not current_pixmap.isNull():
                painter.setOpacity(self._flash_opacity)

                # Scale keeping aspect ratio to fit the dialog size
                scaled_pixmap = current_pixmap.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )

                # Center the scaled image in the dialog area
                x = (self.width() - scaled_pixmap.width()) // 2
                y = (self.height() - scaled_pixmap.height()) // 2
                
                # Fill borders with background color matching the GIF edges (dark blue/black)
                # Background of lain.gif is approximately #001020 or #011b33. We can just fill the rest of the canvas
                # with the default background color of the dialog or a matching dark color
                painter.drawPixmap(x, y, scaled_pixmap)

    def reject(self) -> None:
        """Revert settings on cancel."""
        self.settings.setValue("morrenus_api_key", self._original_morrenus_key)

        # Revert live-previewed settings that were saved immediately
        self.settings.setValue("titlebar_position", self._original_titlebar_position)
        if self.main_window and hasattr(self.main_window, "reposition_titlebar"):
            # noinspection PyUnresolvedReferences
            self.main_window.reposition_titlebar(self._original_titlebar_position)

        # Revert origins settings and stop movie if running
        if hasattr(self, "_original_remember_origins"):
            self.settings.setValue("remember_origins", self._original_remember_origins)
        if hasattr(self, "_original_simplify_denuvo_status"):
            self.settings.setValue("simplify_denuvo_status", self._original_simplify_denuvo_status)

        if hasattr(self, "_origins_movie") and self._origins_movie:
            self._origins_movie.stop()
            self._origins_movie = None


        
        if hasattr(self, "service_poll_timer") and self.service_poll_timer:
            self.service_poll_timer.stop()
        super().reject()

    @staticmethod
    def _is_steam_updates_blocked() -> bool:
        """Check if steam.cfg exists."""
        try:
            from core.steam_helpers import find_steam_install

            path = find_steam_install()
            if not path:
                return False
            return os.path.exists(os.path.join(path, "steam.cfg"))
        except ImportError:
            return False

    @staticmethod
    def _apply_steam_updates_block(enabled: bool) -> None:
        """Manage steam.cfg file."""
        try:
            from core.steam_helpers import find_steam_install

            path = find_steam_install()
            if not path:
                logger.warning("Steam not found, skipping steam.cfg")
                return

            dest = os.path.join(path, "steam.cfg")
            src = Paths.deps("steam.cfg")

            if enabled:
                if not src.exists():
                    logger.error(f"Source steam.cfg missing: {src}")
                    return
                shutil.copy2(str(src), dest)
                logger.info(f"Copied steam.cfg to {dest}")
            elif os.path.exists(dest):
                os.remove(dest)
                logger.info(f"Removed steam.cfg from {dest}")

        except (ImportError, IOError) as e:
            logger.error(f"Failed to apply steam.cfg: {e}", exc_info=True)

    def _update_slssteam_status(self) -> None:
        """Check status update in background."""
        from core.tasks.download_slssteam_task import DownloadSLSsteamTask

        vf = get_base_path() / "SLSsteam" / "VERSION"
        if not vf.exists():
            self._set_label_viz("slssteam_status_label", False)
            self._set_label_viz("slssteam_hash_warning_label", False)
            return

        self._set_label_viz("slssteam_status_label", True)
        self._set_label_viz("slssteam_hash_warning_label", True)

        import threading

        def check() -> None:
            st = DownloadSLSsteamTask.check_update_available()
            if hasattr(self, "slssteam_status_label"):
                self.slssteam_status_label.setText(
                    SettingsDialog._format_status_text(st)
                )
            if hasattr(self, "slssteam_hash_warning_label"):
                self._update_slssteam_hash_warning(st)

        threading.Thread(target=check, daemon=True).start()

    def _set_label_viz(self, name: str, viz: bool) -> None:
        if hasattr(self, name):
            getattr(self, name).setVisible(viz)

    def _update_slssteam_hash_warning(self, status: dict) -> None:
        """Update hash warning text."""
        if not hasattr(self, "slssteam_hash_warning_label"):
            return

        lbl = self.slssteam_hash_warning_label
        mis = status.get("steamclient_mismatch")
        fnd = status.get("steamclient_found")
        err = status.get("steamclient_error")
        pink = "color: #C06C84; font-size: 11px;"
        green = "color: #7FC97F; font-size: 11px;"

        if mis:
            lbl.setText("Your Steam client is not compatible.")
            lbl.setStyleSheet(pink)
        elif err and fnd:
            lbl.setText("Could not verify compatibility.")
            lbl.setStyleSheet(pink)
        elif not fnd:
            lbl.setText("Steam client not found.")
            lbl.setStyleSheet(pink)
        elif mis is False:
            lbl.setText("Your Steam client is compatible.")
            lbl.setStyleSheet(green)
        lbl.setVisible(True)

    @staticmethod
    def _format_status_text(status: dict) -> str:
        if status.get("error"):
            return "Status unknown (error checking)"
        ver = status.get("latest_version", "Unknown")
        if not status.get("installed", False):
            return f"Not installed • Latest: {ver}"
        if status.get("update_available", False):
            return f"Update available • Latest: {ver}"
        return f"Up to date • Version: {status.get('installed_version', '?')}"

    def download_slssteam(self):
        """Open external recommended SLSsteam installer page instead of installing."""
        url = "https://github.com/Deadboy666/h3adcr-b?tab=readme-ov-file#headcrab"
        opened = False

        if sys.platform == "linux" and shutil.which("xdg-open"):
            try:
                result = subprocess.run(
                    ["xdg-open", url],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                opened = result.returncode == 0
            except Exception as e:
                logger.error(f"xdg-open failed: {e}")

        if not opened:
            try:
                browser = webbrowser.get()
                opened = browser.open_new_tab(url)
                logger.info("webbrowser.open_new_tab returned: %s", opened)
            except Exception as e:
                logger.warning(f"Webbrowser fallback failed: {e}")

        if not opened:
            try:
                opened = QDesktopServices.openUrl(QUrl(url))
                logger.info("QDesktopServices.openUrl returned: %s", opened)
            except Exception as e:
                logger.warning(f"QDesktopServices failed: {e}")

        if opened:
            try:
                self.accept()
            except Exception:
                pass
        else:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to open external installer page. Please visit:\n{url}",
            )

    def _update_achievements_button_state(self) -> None:
        """Enables/disables the Configure Achievements button based on toggle state."""
        if hasattr(self, "configure_achievements_btn") and self.configure_achievements_btn:
            is_enabled = self.achievements_checkbox.isChecked()
            self.configure_achievements_btn.setEnabled(is_enabled)

    def _update_asshead_status_ui(self) -> None:
        """Updates the status display for ASShead."""
        import utils.assfixer
        status = utils.assfixer.boot_status
        issues = utils.assfixer.boot_issues

        if status == "optimal":
            self.asshead_status_label.setText("Status: Config Optimal. All settings are clean and matches upstream.")
            self.asshead_status_label.setStyleSheet("color: #44bb44;")
        elif status == "needs_fix":
            issues_summary = "\n".join(f"• {issue}" for issue in issues[:3])
            if len(issues) > 3:
                issues_summary += f"\n• ...and {len(issues) - 3} more issues."
            self.asshead_status_label.setText(f"Status: Updates/Repairs needed.\n{issues_summary}")
            self.asshead_status_label.setStyleSheet("color: #ffaa00;")
        elif status == "no_config":
            self.asshead_status_label.setText("Status: No config found. SLSsteam config.yaml does not exist.")
            self.asshead_status_label.setStyleSheet("color: #cc4444;")
        elif status == "checking":
            self.asshead_status_label.setText("Status: Checking configuration status...")
            self.asshead_status_label.setStyleSheet("color: #888888;")
        elif status == "failed":
            self.asshead_status_label.setText(f"Status: Failed to check upstream template.\nError: {issues[0] if issues else 'Unknown'}")
            self.asshead_status_label.setStyleSheet("color: #cc4444;")
        else:
            self.asshead_status_label.setText("Status: Not checked.")
            self.asshead_status_label.setStyleSheet("color: #888888;")

        # Enable/disable restore backup button based on backup existence
        from utils.assfixer import get_latest_backup_path, DEFAULT_CONFIG_PATH
        if hasattr(self, "restore_backup_btn") and self.restore_backup_btn:
            has_bak = get_latest_backup_path(DEFAULT_CONFIG_PATH) is not None
            self.restore_backup_btn.setEnabled(has_bak)

    def open_sls_config(self) -> None:
        """Open the SLSsteam config.yaml file."""
        from utils.assfixer import DEFAULT_CONFIG_PATH
        if not DEFAULT_CONFIG_PATH.exists():
            QMessageBox.warning(self, "Open Config", "SLSsteam config.yaml does not exist.")
            return

        # Attempt to open using the default system handler
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        try:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(DEFAULT_CONFIG_PATH)))
            if not opened:
                import webbrowser
                webbrowser.open(DEFAULT_CONFIG_PATH.as_uri())
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open config.yaml:\n{e}")

    def restore_sls_backup(self) -> None:
        """Restores the last backup copy of config.yaml."""
        from utils.assfixer import restore_latest_backup, DEFAULT_CONFIG_PATH

        reply = QMessageBox.question(
            self, "Restore Backup",
            "Are you sure you want to restore the latest backup? This will overwrite your current config.yaml.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        success, msg, bak_path = restore_latest_backup(DEFAULT_CONFIG_PATH)
        if success:
            import utils.assfixer
            utils.assfixer.boot_status = "checking"

            import threading
            def run_check():
                utils.assfixer.run_boot_config_check()
                # Safely update status label
                from PyQt6.QtCore import QMetaObject, Qt
                QMetaObject.invokeMethod(self, "_update_asshead_status_ui", Qt.ConnectionType.QueuedConnection)
                if self.main_window and hasattr(self.main_window, "refresh_system_status"):
                    self.main_window.refresh_system_status()

            threading.Thread(target=run_check, daemon=True).start()
            QMessageBox.information(self, "Restore Backup", msg)
        else:
            QMessageBox.critical(self, "Restore Backup Error", msg)

    def run_asshead_fixer(self) -> None:
        """Runs the ASShead config fixer and shows outcomes."""
        from utils.assfixer import run_asshead_migration, DEFAULT_CONFIG_PATH

        self.run_asshead_btn.setEnabled(False)
        self.asshead_status_label.setText("Status: Running fixer...")
        self.asshead_status_label.setStyleSheet("color: #888888;")

        success, msg, bak_path = run_asshead_migration(DEFAULT_CONFIG_PATH)

        self.run_asshead_btn.setEnabled(True)

        if success:
            import utils.assfixer
            utils.assfixer.boot_status = "optimal"
            utils.assfixer.boot_issues = []

            self._update_asshead_status_ui()

            # Refresh system status on the main window dashboard to update the status color
            if self.main_window and hasattr(self.main_window, "refresh_system_status"):
                self.main_window.refresh_system_status()

            detail_msg = msg
            if bak_path:
                detail_msg += f"\n\nA backup of your previous config has been saved to:\n{bak_path}"

            QMessageBox.information(self, "ASShead Config Fixer", detail_msg)
        else:
            self._update_asshead_status_ui()

            # Refresh system status on the main window dashboard in case check failed
            if self.main_window and hasattr(self.main_window, "refresh_system_status"):
                self.main_window.refresh_system_status()

            QMessageBox.critical(self, "ASShead Config Fixer Error", f"Failed to fix configuration:\n{msg}")

    def run_denuvo_sync(self) -> None:
        """Runs the Denuvo games sync in a background thread."""
        if not hasattr(self, "run_denuvo_sync_btn") or not self.run_denuvo_sync_btn:
            return
        self.run_denuvo_sync_btn.setEnabled(False)
        self.asshead_status_label.setText("Status: Syncing Denuvo games...")
        self.asshead_status_label.setStyleSheet("color: #ffaa00;")

        import threading
        from core.ratings import sync_denuvo_cache_and_config


        def do_sync():
            res = sync_denuvo_cache_and_config(main_window=self.main_window, force=True)
            self._last_denuvo_sync_result = res
            from PyQt6.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(self, "_on_denuvo_sync_finished", Qt.ConnectionType.QueuedConnection)

        threading.Thread(target=do_sync, daemon=True).start()

    @pyqtSlot()
    def _on_denuvo_sync_finished(self) -> None:
        res = getattr(self, "_last_denuvo_sync_result", {"success": False, "error": "Unknown error"})
        if hasattr(self, "run_denuvo_sync_btn") and self.run_denuvo_sync_btn:
            self.run_denuvo_sync_btn.setEnabled(True)
        self._update_asshead_status_ui()

        if res.get("success"):
            count = res.get("count", 0)
            if self.main_window and hasattr(self.main_window, "refresh_system_status"):
                self.main_window.refresh_system_status()
            QMessageBox.information(
                self,
                "Denuvo Sync",
                f"Successfully synced Denuvo games to your SLSsteam configuration.\nBlocked games count: {count}"
            )
        else:
            if self.main_window and hasattr(self.main_window, "refresh_system_status"):
                self.main_window.refresh_system_status()
            QMessageBox.critical(
                self,
                "Denuvo Sync Error",
                f"Denuvo Sync failed:\n{res.get('error')}"
            )


    def run_schema_grabber_manually(self) -> None:
        """Launch schema-grabber manually in a terminal."""
        helper_path = Paths.deps("schema-grabber/login_helper.py")
        if not helper_path.exists():
            QMessageBox.critical(self, "Error", f"Achievements helper missing at: {helper_path}")
            return

        cmd = []
        py = get_venv_python()
        cmd.append(
            py if py else ("python" if sys.platform == "win32" else "python3")
        )
        cmd.append(str(helper_path))

        SettingsDialog._launch_terminal_command(cmd, str(helper_path.parent))

    @staticmethod
    def _launch_terminal_command(
        cmd: list[str], cwd: str, needs_env: bool = False
    ) -> None:
        """Try to launch a command in a visible terminal."""
        cmd: list[str] = [str(part) for part in cmd]
        cwd = str(cwd)
        if sys.platform == "win32":
            q_cmd = " ".join([f'"{c}"' if " " in str(c) else str(c) for c in cmd])
            try:
                subprocess.Popen(
                    f'start cmd /k "cd /d {cwd} && {q_cmd}"',
                    shell=True,
                )
                return
            except OSError:
                pass
        else:
            terms = [
                ["wezterm", "start", "--always-new-process", "--"] + cmd,
                ["konsole", "-e"] + cmd,
                ["gnome-terminal", "--"] + cmd,
                ["ptyxis", "--"] + cmd,
                ["alacritty", "-e"] + cmd,
                ["tilix", "-e"] + cmd,
                ["xfce4-terminal", "-e"] + cmd,
                ["terminator", "-x"] + cmd,
                ["mate-terminal", "-e"] + cmd,
                ["lxterminal", "-e"] + cmd,
                ["xterm", "-e"] + cmd,
                ["kitty", "-e"] + cmd,
            ]
            for t in terms:
                try:
                    t_cmd: list[str] = [str(part) for part in t]
                    subprocess.Popen(t_cmd, cwd=cwd)
                    return
                except FileNotFoundError:
                    continue

        # Fallback dialog
        msg_box = QMessageBox()
        msg_box.setWindowTitle("Terminal Not Found")
        msg_box.setText(
            "Could not automatically launch a terminal.\n"
            "Please open a terminal and run:\n"
        )
        msg_box.setInformativeText(" ".join(cmd))
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg_box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        msg_box.exec()

    def run_steamless_manually(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Executable", os.path.expanduser("~"), "*.exe"
        )
        if path and self.main_window:
            # noinspection PyUnresolvedReferences
            self.main_window.task_manager.run_steamless_manually(path)

    def run_steamless_aio_manually(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Executable", os.path.expanduser("~"), "*.exe"
        )
        if path and self.main_window:
            # noinspection PyUnresolvedReferences
            self.main_window.task_manager.run_steamless_aio_manually(path)

    def _browse_aio_script(self) -> None:
        """Browse for the Steamless AIO shell script."""
        current = self.steamless_aio_path_edit.text() or os.path.expanduser("~/Downloads")
        start_dir = os.path.dirname(current) if os.path.isfile(current) else current
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Steamless AIO Script",
            start_dir,
            "Shell Scripts (*.sh);;All Files (*)",
        )
        if path:
            self.steamless_aio_path_edit.setText(path)
            get_settings().setValue("steamless_aio_path", path)



    @staticmethod
    def register_registry_entries() -> None:
        SettingsDialog._manage_registry("ACCELA.reg", "Registered successfully")

    @staticmethod
    def remove_registry_entries() -> None:
        SettingsDialog._manage_registry("ACCELA_uninstall.reg", "Removed successfully")

    @staticmethod
    def _manage_registry(filename: str, success_msg: str) -> None:
        if sys.platform != "win32":
            return

        # Locate registry file
        base = (
            os.path.join(getattr(sys, "_MEIPASS"), "deps")
            if getattr(sys, "frozen", False)
            else os.path.join(os.path.dirname(__file__), "..", "..", "deps")
        )
        reg_path = os.path.join(base, filename)

        if not os.path.exists(reg_path):
            QMessageBox.critical(None, "Error", f"Missing {filename}")
            return

        try:
            # Process template
            with open(reg_path, "r", encoding="utf-8-sig") as f:
                content = f.read().replace(
                    "[INSTALL_PATH]", sys.executable.replace("\\", "\\\\")
                )

            # Write temp file
            import tempfile

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".reg", delete=False
            ) as tmp:
                tmp.write(content)
                tmp_name = tmp.name

            # Import
            subprocess.run(["regedit", "/s", str(tmp_name)], check=True, shell=True)
            os.unlink(tmp_name)
            QMessageBox.information(None, "Success", success_msg)

        except (IOError, OSError, subprocess.SubprocessError) as e:
            QMessageBox.critical(None, "Error", f"Registry error: {e}")
