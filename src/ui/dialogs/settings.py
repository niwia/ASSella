import logging
import os
import shutil
import subprocess
import sys
import webbrowser

from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal, pyqtSlot, QMetaObject, Q_ARG
from PyQt6.QtGui import QColor, QFont, QDesktopServices, QMovie, QPainter
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFontDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
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
    FontSelectionDialog,
)
from utils.paths import Paths
from utils.settings import get_settings
from utils.yaml_config_manager import is_slssteam_mode_enabled
from ui.dialogs.settings_sls import create_sls_tab

logger = logging.getLogger(__name__)


class MorrenusStatsWidget(QWidget):
    """Widget displaying Hubcap API user statistics and cloud generation quotas."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.settings = get_settings()
        self.username_label = None
        self.expiration_label = None
        self.total_calls_label = None
        self.account_status_lbl = None
        self.steam_service_lbl = None
        self.refresh_button = None
        self._setup_ui()

    def _create_stat_bar(self, title: str, is_muted: bool = False):
        """Helper to create a titled progress bar row with value label."""
        container = QVBoxLayout()
        container.setSpacing(3)
        container.setContentsMargins(0, 2, 0, 2)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        title_lbl = QLabel(title)
        if is_muted:
            title_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 8.5pt;")
        else:
            title_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")

        val_lbl = QLabel("--")
        if is_muted:
            val_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.35); font-size: 8.5pt;")
        else:
            val_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;")

        header_layout.addWidget(title_lbl)
        header_layout.addStretch()
        header_layout.addWidget(val_lbl)
        container.addLayout(header_layout)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)

        accent_color = self.settings.value("accent_color", "#C06C84")
        from utils.color_utils import get_dark_container_color
        track_bg = get_dark_container_color(accent_color)

        if is_muted:
            bar.setStyleSheet("""
                QProgressBar {
                    background-color: rgba(255, 255, 255, 0.05);
                    border: none;
                    border-radius: 4px;
                }
                QProgressBar::chunk {
                    background-color: rgba(255, 255, 255, 0.2);
                    border-radius: 4px;
                    margin: 0px;
                }
            """)
        else:
            bar.setStyleSheet(
                f"""
                QProgressBar {{
                    background-color: {track_bg};
                    border: none;
                    border-radius: 4px;
                }}
                QProgressBar::chunk {{
                    background-color: {accent_color};
                    border-radius: 4px;
                    margin: 0px;
                }}
                """
            )

        container.addWidget(bar)
        return container, title_lbl, val_lbl, bar

    def _setup_ui(self) -> None:
        """Initialize the UI components."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 5, 0, 5)
        main_layout.setSpacing(8)

        # Row 1: User info row (Username, Expires, Total API Calls)
        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 2)
        info_row.setSpacing(12)

        self.username_label = QLabel("User: --")
        self.username_label.setStyleSheet("font-weight: bold; color: #FFFFFF; font-size: 9pt;")
        info_row.addWidget(self.username_label)
        info_row.addStretch()

        self.expiration_label = QLabel("Expires: --")
        self.expiration_label.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;")
        info_row.addWidget(self.expiration_label)

        self.total_calls_label = QLabel("Total: --")
        self.total_calls_label.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;")
        info_row.addWidget(self.total_calls_label)

        main_layout.addLayout(info_row)

        # Progress Bars Section
        # 1. Daily API Usage (Manifests)
        c1, self.daily_title_lbl, self.daily_val_lbl, self.daily_usage_bar = self._create_stat_bar("Daily API Usage (Manifests):")
        main_layout.addLayout(c1)

        # 2. Bundle Generation / Updates Quota
        c2, self.bundle_title_lbl, self.bundle_val_lbl, self.bundle_bar = self._create_stat_bar("Bundle Generation / Updates Quota:")
        main_layout.addLayout(c2)

        # 3. Workshop Generation Quota
        c3, self.workshop_title_lbl, self.workshop_val_lbl, self.workshop_bar = self._create_stat_bar("Workshop Generation Quota:")
        main_layout.addLayout(c3)

        # 4. Single Generation Quota
        c4, self.single_title_lbl, self.single_val_lbl, self.single_bar = self._create_stat_bar("Single Generation:")
        main_layout.addLayout(c4)

        # Bottom status row (Account Status on left, Steam Gen Service on right)
        status_bottom_row = QHBoxLayout()
        status_bottom_row.setContentsMargins(0, 4, 0, 0)

        self.account_status_lbl = QLabel("● Account: --")
        self.account_status_lbl.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.6);")
        status_bottom_row.addWidget(self.account_status_lbl)

        status_bottom_row.addStretch()

        self.steam_service_lbl = QLabel("● Steam Gen Service: --")
        self.steam_service_lbl.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.6);")
        status_bottom_row.addWidget(self.steam_service_lbl)

        main_layout.addLayout(status_bottom_row)

        # Refresh button
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.refresh_button.clicked.connect(self.refresh_stats)
        main_layout.addWidget(self.refresh_button)

    def refresh_stats(self) -> None:
        """Fetch and display latest stats from both user/stats and generate/usage."""
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Loading...")

        from utils.task_runner import TaskRunner
        self._stats_runner = TaskRunner(self)
        worker = self._stats_runner.run(morrenus_api.get_all_hubcap_stats)

        def on_stats_finished(result):
            self.refresh_button.setEnabled(True)
            self.refresh_button.setText("Refresh")
            if not result:
                self._display_error_state()
            else:
                self._display_all_stats(result.get("user_stats", {}), result.get("gen_usage", {}))

        def on_stats_error(err_tuple):
            self.refresh_button.setEnabled(True)
            self.refresh_button.setText("Refresh")
            self._display_error_state()

        worker.finished.connect(on_stats_finished)
        worker.error.connect(on_stats_error)

    def _display_error_state(self) -> None:
        """Update UI to show error state."""
        self.username_label.setText("User: Error")
        self.total_calls_label.setText("Total: --")
        self.expiration_label.setText("Expires: --")
        self.expiration_label.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;")
        self.account_status_lbl.setText("● Account: Error")
        self.account_status_lbl.setStyleSheet("font-size: 8pt; color: #e57373;")
        self.daily_val_lbl.setText("Error")
        self.daily_usage_bar.setValue(0)
        self.bundle_val_lbl.setText("Error")
        self.bundle_bar.setValue(0)
        self.workshop_val_lbl.setText("Error")
        self.workshop_bar.setValue(0)
        self.single_val_lbl.setText("Error")
        self.single_bar.setValue(0)
        self.steam_service_lbl.setText("● Steam Gen Service: --")
        self.steam_service_lbl.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.5);")

    def _display_all_stats(self, user_stats: dict, gen_usage: dict) -> None:
        """Update UI with fetched statistics and quotas."""
        accent_color = self.settings.value("accent_color", "#C06C84")
        from utils.color_utils import get_semantic_colors
        semantic = get_semantic_colors(accent_color)

        # 1. User stats
        if user_stats and not user_stats.get("error"):
            self.username_label.setText(f"User: {user_stats.get('username', 'Unknown')}")
            self.total_calls_label.setText(f"Total: {user_stats.get('api_key_usage_count', 0)}")

            daily_usage = MorrenusStatsWidget._parse_int(user_stats.get("daily_usage", 0))
            daily_limit = MorrenusStatsWidget._parse_int(user_stats.get("daily_limit", 55))
            if daily_limit <= 0:
                daily_limit = 55

            self.daily_usage_bar.setRange(0, daily_limit)
            self.daily_usage_bar.setValue(daily_usage)
            self.daily_val_lbl.setText(f"{daily_usage} / {daily_limit}")

            self._update_expiration_label(user_stats.get("api_key_expires_at", ""))

            can_req = user_stats.get("can_make_requests", False)
            if can_req:
                self.account_status_lbl.setText("● Account: Active")
                self.account_status_lbl.setStyleSheet(f"font-size: 8pt; color: {semantic.get('success', '#81c784')}; font-weight: bold;")
            else:
                self.account_status_lbl.setText("● Account: Inactive")
                self.account_status_lbl.setStyleSheet(f"font-size: 8pt; color: {semantic.get('error', '#e57373')}; font-weight: bold;")
        else:
            self.username_label.setText("User: Error")
            self.daily_val_lbl.setText("Error")
            self.account_status_lbl.setText("● Account: Error")
            self.account_status_lbl.setStyleSheet(f"font-size: 8pt; color: {semantic.get('error', '#e57373')}; font-weight: bold;")

        # 2. Generation usage limits
        if gen_usage and not gen_usage.get("error"):
            bundle = gen_usage.get("bundle", {})
            b_usage = MorrenusStatsWidget._parse_int(bundle.get("usage", 0))
            b_limit = MorrenusStatsWidget._parse_int(bundle.get("limit", 100))
            if b_limit <= 0:
                b_limit = 100
            self.bundle_bar.setRange(0, b_limit)
            self.bundle_bar.setValue(b_usage)
            self.bundle_val_lbl.setText(f"{b_usage} / {b_limit}")

            workshop = gen_usage.get("workshop", {})
            w_usage = MorrenusStatsWidget._parse_int(workshop.get("usage", 0))
            w_limit = MorrenusStatsWidget._parse_int(workshop.get("limit", 500))
            if w_limit <= 0:
                w_limit = 500
            self.workshop_bar.setRange(0, w_limit)
            self.workshop_bar.setValue(w_usage)
            self.workshop_val_lbl.setText(f"{w_usage} / {w_limit}")

            single = gen_usage.get("single", {})
            s_usage = MorrenusStatsWidget._parse_int(single.get("usage", 0))
            s_limit = MorrenusStatsWidget._parse_int(single.get("limit", 1500))
            if s_limit <= 0:
                s_limit = 1500
            self.single_bar.setRange(0, s_limit)
            self.single_bar.setValue(s_usage)
            self.single_val_lbl.setText(f"{s_usage} / {s_limit}")

            ready = gen_usage.get("steam_service_ready", True)
            if ready:
                self.steam_service_lbl.setText("● Steam Gen Service: Ready")
                self.steam_service_lbl.setStyleSheet(f"font-size: 8pt; color: {semantic.get('success', '#81c784')};")
            else:
                self.steam_service_lbl.setText("● Steam Gen Service: Offline")
                self.steam_service_lbl.setStyleSheet(f"font-size: 8pt; color: {semantic.get('error', '#e57373')};")
        else:
            self.bundle_val_lbl.setText("--")
            self.workshop_val_lbl.setText("--")
            self.single_val_lbl.setText("--")
            self.steam_service_lbl.setText("● Steam Gen Service: --")
            self.steam_service_lbl.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.5);")

    @staticmethod
    def _parse_int(value: Any, default: int = 0) -> int:
        """Safely parse an integer value."""
        try:
            return int(value or default)
        except (TypeError, ValueError):
            return default

    def _update_expiration_label(self, expires_at: str) -> None:
        """Format and update the expiration label with theme-adaptive warning colors."""
        if not expires_at:
            self.expiration_label.setText("Expires: Never")
            self.expiration_label.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;")
            return

        formatted_date = expires_at[:10]
        days_left = None
        try:
            exp_clean = expires_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(exp_clean)
            formatted_date = dt.strftime("%d/%m/%Y")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days_left = (dt - now).days
        except Exception as e:
            logger.debug(f"Failed to parse expiry date: {e}")

        accent_color = self.settings.value("accent_color", "#C06C84")
        from utils.color_utils import get_semantic_colors
        semantic = get_semantic_colors(accent_color)

        if days_left is not None:
            if days_left < 0:
                color = semantic.get("error", "#e57373")
                status_text = f"Expires: {formatted_date} (Expired)"
            elif days_left <= 7:
                # Urgent warning
                color = semantic.get("error", "#e57373")
                status_text = f"Expires: {formatted_date} ({days_left}d left)"
            elif days_left <= 30:
                # Moderate warning (~70% through validity or <30 days remaining)
                color = semantic.get("warning", "#ffd54f")
                status_text = f"Expires: {formatted_date} ({days_left}d left)"
            elif days_left <= 60:
                # Light advisory warning
                color = semantic.get("warning", "#ffd54f")
                status_text = f"Expires: {formatted_date}"
            else:
                # Plenty of time left -> standard theme-harmonized muted text
                color = "rgba(255, 255, 255, 0.75)"
                status_text = f"Expires: {formatted_date}"
        else:
            color = "rgba(255, 255, 255, 0.75)"
            status_text = f"Expires: {formatted_date}"

        self.expiration_label.setText(status_text)
        self.expiration_label.setStyleSheet(f"color: {color}; font-size: 8.5pt; font-weight: 500;")


class SettingsDialog(QDialog):
    """Dialog for configuring application settings."""

    assfixer_done_signal = pyqtSignal(tuple)
    assfixer_repair_done_signal = pyqtSignal(tuple)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.assfixer_done_signal.connect(self._handle_assfixer_check_done)
        self.assfixer_repair_done_signal.connect(self._handle_assfixer_repair_done)
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
        self.smart_update_mode_checkbox = None
        self.refined_update_check_checkbox = None
        self.update_provider_combo = None
        self.isp_bypass_hubcap_checkbox = None
        self.experimental_acf_independent_checkbox = None
        self.fakeappid_db_integration_checkbox = None
        self.remote_web_ui_checkbox = None
        self.max_downloads_spinbox = None
        self.steamless_remover_combo = None
        self.filter_soundtracks_checkbox = None
        self.filter_search_blacklist_checkbox = None
        self.hide_macos_depots_checkbox = None
        self.hide_android_depots_checkbox = None
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

            gif_path = os.path.expanduser("~/.local/share/ACCELA/jumpscare/lain.gif")
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
        # Apply premium Material You / M3 styles for QComboBoxes in settings
        ac = self.accent_color
        from utils.color_utils import get_dark_container_color
        sel_bg_hex = get_dark_container_color(ac)
        
        self.setStyleSheet(f"""
            QComboBox {{
                background-color: rgba(255, 255, 255, 0.08) !important;
                border: 1px solid rgba(255, 255, 255, 0.22) !important;
                border-radius: 8px !important;
                color: #FFFFFF !important;
                padding: 6px 30px 6px 12px !important;
                font-size: 9.5pt !important;
                font-weight: 500 !important;
                min-height: 22px !important;
            }}
            QComboBox:hover {{
                background-color: rgba(255, 255, 255, 0.14) !important;
                border-color: rgba(255, 255, 255, 0.38) !important;
            }}
            QComboBox:focus {{
                border: 2px solid {self.accent_color} !important;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border: none;
                background: transparent;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid rgba(255, 255, 255, 0.85);
                width: 0;
                height: 0;
                margin-right: 8px;
            }}
            QComboBox QAbstractItemView {{
                background-color: #1b1b1f;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 8px;
                selection-background-color: {sel_bg_hex};
                selection-color: #FFFFFF;
                outline: 0px;
                padding: 4px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 28px;
                padding: 4px 12px;
                color: #E0E0E0;
            }}
            QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{
                background-color: {sel_bg_hex} !important;
                color: #FFFFFF !important;
            }}
            QLineEdit {{
                background-color: rgba(255, 255, 255, 0.07) !important;
                border: 1px solid rgba(255, 255, 255, 0.2) !important;
                border-radius: 8px !important;
                color: #FFFFFF !important;
                padding: 7px 12px !important;
                font-size: 9.5pt !important;
            }}
            QLineEdit:focus {{
                border: 2px solid {self.accent_color} !important;
            }}
            QTextEdit {{
                background-color: rgba(255, 255, 255, 0.07) !important;
                border: 1px solid rgba(255, 255, 255, 0.2) !important;
                border-radius: 8px !important;
                color: #FFFFFF !important;
                padding: 8px !important;
                font-size: 9.5pt !important;
            }}
            QTextEdit:focus {{
                border: 2px solid {self.accent_color} !important;
            }}
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.09) !important;
                border: 1px solid rgba(255, 255, 255, 0.2) !important;
                border-radius: 8px !important;
                color: #FFFFFF !important;
                padding: 7px 16px !important;
                font-size: 9.5pt !important;
                font-weight: 500 !important;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.18) !important;
                border-color: {self.accent_color} !important;
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.08) !important;
                border: 1px solid rgba(255, 255, 255, 0.12) !important;
                color: rgba(255, 255, 255, 0.38) !important;
            }}
        """)

        self.main_layout = QVBoxLayout(self)

        self._create_tab_widget()
        self.main_layout.addWidget(self.tab_widget)

        self._setup_tabs()

        self._create_dialog_buttons()

    def _create_tab_widget(self) -> None:
        """Create and style the tab widget with scroll buttons and clean spacing."""
        self.tab_widget = QTabWidget()
        self.tab_widget.setUsesScrollButtons(True)
        bg_color = self.settings.value("background_color", "#141416")
        self.tab_widget.setStyleSheet(
            f"""
            QTabWidget::pane {{
                border: none;
            }}
            QTabBar::tab {{
                background: {bg_color};
                color: rgba(255, 255, 255, 0.6);
                padding: 8px 14px;
                border: none;
                font-weight: bold;
                font-size: 9.5pt;
            }}
            QTabBar::tab:selected {{
                color: {self.accent_color};
                border-bottom: 2px solid {self.accent_color};
            }}
            QTabBar::tab:hover {{
                color: #FFFFFF;
            }}
            QTabBar QToolButton {{
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 4px;
            }}
        """
        )

    def _create_card_frame(self, title_text: str = "") -> Tuple[QFrame, QVBoxLayout]:
        """Helper to create a compact Material 3 card container."""
        card = QFrame()
        card.setObjectName("SectionCard")
        card.setStyleSheet("""
            QFrame#SectionCard {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }
            QFrame#SectionCard > QLabel {
                border: none !important;
                background: transparent !important;
                padding: 0px !important;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(6)

        if title_text:
            title_lbl = QLabel(title_text)
            title_lbl.setStyleSheet(f"font-size: 10pt; font-weight: bold; color: {self.accent_color}; margin-bottom: 2px; border: none; background: transparent;")
            card_layout.addWidget(title_lbl)

        return card, card_layout

    def _setup_tabs(self) -> None:
        """Initialize and add all settings tabs."""
        self._create_assela_tab()
        self._create_downloads_tab()
        self._create_advanced_tab()
        self._create_morrenus_tab()
        # self._create_webui_tab()
        create_sls_tab(self)
        self._create_tools_tab()
        self._create_style_tab()

        # Initialize button state after all tabs have been populated
        self._update_achievements_button_state()

        # Sanity check: Check SLS requirements to enable/disable the experimental_acf_independent_checkbox
        try:
            from utils.yaml_config_manager import get_user_config_path
            from ui.dialogs.settings_sls import get_sls_paths
            config_path = get_user_config_path()
            sls_paths = get_sls_paths()
            
            sls_detected = config_path.exists() and sls_paths.get("detected", False)
            
            if self.experimental_acf_independent_checkbox is not None:
                if not sls_detected:
                    self.experimental_acf_independent_checkbox.setChecked(False)
                    self.experimental_acf_independent_checkbox.setEnabled(False)
                    tooltip_msg = "Disabled: SLSsteam config.yaml or SLSsteam installation not detected."
                    if not config_path.exists():
                        tooltip_msg = "Disabled: SLSsteam config.yaml not found."
                    elif not sls_paths.get("detected", False):
                        tooltip_msg = "Disabled: SLSsteam installation not detected."
                    self.experimental_acf_independent_checkbox.setToolTip(tooltip_msg)
                else:
                    self.experimental_acf_independent_checkbox.setEnabled(True)
        except Exception as e:
            logger.warning(f"Error checking SLS requirements: {e}")

        # Apply initial state: if Let SLS handle ACF is already enabled, apply side effects (disables Prompt Steam Restart, library_mode, config management)
        try:
            if (self.experimental_acf_independent_checkbox is not None
                    and self.experimental_acf_independent_checkbox.isChecked()):
                self._on_experimental_acf_toggled(True)
        except Exception:
            pass

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
        """Create an API key input field with Get API Key, Show, and Paste buttons in a row below."""
        layout = QVBoxLayout()
        layout.setSpacing(6)

        lbl = QLabel(label)
        lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
        layout.addWidget(lbl)

        api_key_input = QLineEdit()
        api_key_input.setPlaceholderText(placeholder)
        api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        current_key = self.settings.value(setting_key, "", type=str)
        api_key_input.setText(current_key)
        layout.addWidget(api_key_input)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        if help_url:
            get_key_btn = QPushButton("Get API Key")
            get_key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            get_key_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(help_url)))
            btn_row.addWidget(get_key_btn)

        toggle_btn = QPushButton("Show")
        toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle_btn.clicked.connect(
            lambda: SettingsDialog._toggle_api_key_visibility(api_key_input, toggle_btn)
        )
        btn_row.addWidget(toggle_btn)

        paste_btn = QPushButton("Paste")
        paste_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        def _on_paste():
            from PyQt6.QtWidgets import QApplication
            clip_text = QApplication.clipboard().text()
            if clip_text:
                api_key_input.setText(clip_text.strip())
        paste_btn.clicked.connect(_on_paste)
        btn_row.addWidget(paste_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

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
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(24)

        # Section 1 Card: ASSella Settings
        assella_card, assella_lay = self._create_card_frame("ASSella Settings")

        # 1. Smart Selection
        self.smart_depot_selection_checkbox = create_checkbox_setting(
            "Smart Selection",
            "smart_depot_selection",
            False,
            self,
            "Automatically reuse previously chosen depots on update, unless a brand new depot is added.",
        )
        assella_lay.addWidget(self.smart_depot_selection_checkbox)

        # 2. ISP Bypass & Hubcap Gateway Selector
        isp_group = QVBoxLayout()
        isp_group.setSpacing(8)
        isp_group.setContentsMargins(0, 2, 0, 2)

        isp_row = QHBoxLayout()
        isp_row.setContentsMargins(0, 0, 0, 0)
        isp_lbl = QLabel("Hubcap Gateway:")
        isp_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
        isp_lbl.setToolTip("Select how ASSella routes requests to Hubcap API (Auto Smart Fallback, Direct, DoH, Tor, or Wire).")
        isp_row.addWidget(isp_lbl)
        isp_row.addStretch()

        self.isp_gateway_combo = QComboBox()
        self.isp_gateway_combo.addItem("Auto", "auto")
        self.isp_gateway_combo.addItem("Direct", "direct")
        self.isp_gateway_combo.addItem("DoH", "doh")
        self.isp_gateway_combo.addItem("Tor", "tor")
        self.isp_gateway_combo.addItem("Wire", "wirecutter")
        self.isp_gateway_combo.setFixedWidth(115)

        # Load saved mode
        saved_mode = self.settings.value("isp_bypass_mode", "auto", type=str)
        if not saved_mode:
            saved_mode = "auto"

        idx = self.isp_gateway_combo.findData(saved_mode)
        if idx >= 0:
            self.isp_gateway_combo.setCurrentIndex(idx)
        else:
            self.isp_gateway_combo.setCurrentIndex(0)

        self.isp_gateway_combo.currentIndexChanged.connect(self._on_isp_gateway_changed)
        isp_row.addWidget(self.isp_gateway_combo)
        isp_group.addLayout(isp_row)

        # 4-Button Gateway Health Tester Row
        test_bar_box = QVBoxLayout()
        test_bar_box.setSpacing(4)
        test_bar_box.setContentsMargins(0, 2, 0, 0)

        test_bar_lbl = QLabel("Hubcap Gateway Health Check:")
        test_bar_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; border: none; background: transparent;")
        test_bar_box.addWidget(test_bar_lbl)

        self.gateway_btn_row = QHBoxLayout()
        self.gateway_btn_row.setSpacing(8)

        base_btn_css = """
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 5px 8px;
                color: #e0e0e0;
                font-size: 8.5pt;
                font-weight: 500;
                min-height: 26px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.15);
                border: 1px solid rgba(255, 255, 255, 0.25);
            }
            QPushButton:disabled {
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.05);
                color: rgba(255, 255, 255, 0.25);
            }
        """

        self.test_direct_btn = QPushButton("Direct")
        self.test_direct_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_direct_btn.setStyleSheet(base_btn_css)
        self.test_direct_btn.clicked.connect(lambda: self._test_single_gateway("direct", self.test_direct_btn, "Direct"))
        self.gateway_btn_row.addWidget(self.test_direct_btn)

        self.test_doh_btn = QPushButton("DoH")
        self.test_doh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_doh_btn.setStyleSheet(base_btn_css)
        self.test_doh_btn.clicked.connect(lambda: self._test_single_gateway("doh", self.test_doh_btn, "DoH"))
        self.gateway_btn_row.addWidget(self.test_doh_btn)

        self.test_tor_btn = QPushButton("Tor")
        self.test_tor_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_tor_btn.setStyleSheet(base_btn_css)
        self.test_tor_btn.clicked.connect(lambda: self._test_single_gateway("tor", self.test_tor_btn, "Tor"))
        self.gateway_btn_row.addWidget(self.test_tor_btn)

        self.test_wirecutter_btn = QPushButton("Wirecutter")
        self.test_wirecutter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_wirecutter_btn.setStyleSheet(base_btn_css)
        self.test_wirecutter_btn.clicked.connect(lambda: self._test_single_gateway("wirecutter", self.test_wirecutter_btn, "Wirecutter"))
        self.gateway_btn_row.addWidget(self.test_wirecutter_btn)

        test_bar_box.addLayout(self.gateway_btn_row)
        isp_group.addLayout(test_bar_box)
        assella_lay.addLayout(isp_group)

        # Initial button state check
        if saved_mode == "disabled":
            self.test_direct_btn.setEnabled(False)
            self.test_doh_btn.setEnabled(False)
            self.test_tor_btn.setEnabled(False)
            self.test_wirecutter_btn.setEnabled(False)

        # 4. Update Check Interval Slider
        slider_layout = QHBoxLayout()
        slider_layout.setContentsMargins(2, 2, 2, 2)
        slider_label = QLabel("Update Check Interval:")
        slider_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
        slider_label.setToolTip("Set how often to check for game updates. Move to the leftmost position (0) to disable.")
        
        from PyQt6.QtWidgets import QSlider
        self.update_interval_slider = QSlider(Qt.Orientation.Horizontal)
        self.update_interval_slider.setRange(0, 20)
        self.update_interval_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.update_interval_slider.setTickInterval(1)
        
        self.update_interval_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: none;
                height: 6px;
                background: rgba(255, 255, 255, 0.1);
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: %s;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: %s;
                border: none;
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: white;
            }
        """ % (self.accent_color, self.accent_color))
        
        self.update_interval_value_label = QLabel("Disabled")
        self.update_interval_value_label.setStyleSheet("color: rgba(255, 255, 255, 0.8); font-size: 9pt; font-weight: bold; border: none; background: transparent;")
        self.update_interval_value_label.setFixedWidth(75)
        
        current_minutes = self.settings.value("update_check_interval_minutes", 0, type=int)
        slider_val = min(20, max(0, current_minutes // 5))
        self.update_interval_slider.setValue(slider_val)
        
        def update_slider_label(val):
            if val == 0:
                self.update_interval_value_label.setText("Disabled")
            else:
                self.update_interval_value_label.setText(f"{val * 5} mins")
                
        update_slider_label(slider_val)
        self.update_interval_slider.valueChanged.connect(update_slider_label)
        
        slider_layout.addWidget(slider_label)
        slider_layout.addWidget(self.update_interval_slider, 1)
        slider_layout.addWidget(self.update_interval_value_label)
        assella_lay.addLayout(slider_layout)

        # SteamAPI provider selector
        provider_layout = QHBoxLayout()
        provider_layout.setSpacing(12)
        provider_label = QLabel("SteamAPI Provider:")
        provider_label.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500; border: none; background: transparent;")
        provider_label.setToolTip(
            "Select which API service is prioritized for library game update checks.\n\n"
            "• SteamPICS: Queries Valve servers directly. Authoritative, live data with zero stale caching.\n"
            "• SteamcmdAPI: Fast parallel scan via CDN mirror."
        )

        self.update_provider_combo = QComboBox()
        self.update_provider_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.update_provider_combo.setStyleSheet("""
            QComboBox {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 4px 12px;
                color: #FFFFFF;
                font-size: 9pt;
                min-width: 130px;
            }
            QComboBox:hover {
                background: rgba(255, 255, 255, 0.08);
                border-color: rgba(255, 255, 255, 0.25);
            }
            QComboBox QAbstractItemView {
                background: #1e1e24;
                border: 1px solid rgba(255, 255, 255, 0.15);
                color: #FFFFFF;
                selection-background-color: %s;
            }
        """ % self.accent_color)
        self.update_provider_combo.addItem("SteamPICS", "steampics")
        self.update_provider_combo.addItem("SteamcmdAPI", "steamcmd")

        current_provider = self.settings.value("update_check_api_provider", "steampics", type=str)
        p_idx = self.update_provider_combo.findData(current_provider)
        if p_idx >= 0:
            self.update_provider_combo.setCurrentIndex(p_idx)

        provider_layout.addWidget(provider_label)
        provider_layout.addStretch(1)
        provider_layout.addWidget(self.update_provider_combo)
        assella_lay.addLayout(provider_layout)

        # Clear Update & Build ID Cache Row
        cache_layout = QHBoxLayout()
        cache_desc = QLabel("Update & Build ID Cache:")
        cache_desc.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
        cache_desc.setToolTip("Clears local caches so game build IDs, branches, and update statuses are queried fresh from Steam.")
        self.clear_update_cache_btn = QPushButton("Clear Cache")
        self.clear_update_cache_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_update_cache_btn.setToolTip("Purges cached build IDs, branch manifests, and update status entries.")
        self.clear_update_cache_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 6px;
                padding: 4px 14px;
                color: #FFFFFF;
                font-size: 9pt;
                font-weight: 500;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.16);
                border-color: rgba(255, 255, 255, 0.35);
            }
        """)
        self.clear_update_cache_btn.clicked.connect(self._on_clear_update_cache_clicked)
        cache_layout.addWidget(cache_desc)
        cache_layout.addStretch(1)
        cache_layout.addWidget(self.clear_update_cache_btn)
        assella_lay.addLayout(cache_layout)

        layout.addWidget(assella_card)

        # ── Training Wheels Protocol ──────────────────────────────────────
        twp_btn = QPushButton("Training Wheels Protocol (Beta)")
        twp_btn.setToolTip("Launch the first-time setup guide to configure recommended settings for ASSella.")
        twp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        twp_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                padding: 10px 16px;
                color: #FFFFFF;
                font-size: 9.5pt;
                font-weight: 500;
                text-align: center;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.1);
                border-color: {self.accent_color};
            }}
        """)
        twp_btn.clicked.connect(self._run_training_wheels)
        layout.addWidget(twp_btn)

        layout.addStretch()

        # ── Uninstall (Linux only) ────────────────────────────────────────
        if sys.platform != "win32":
            uninstall_btn = QPushButton("Uninstall ASSella")
            uninstall_btn.setToolTip("Remove ASSella and optionally restore the original ACCELA.")
            
            accent_color = self.settings.value("accent_color", "#C06C84")
            from utils.color_utils import get_semantic_colors
            sem_colors = get_semantic_colors(accent_color)
            err_color = sem_colors["error"]
            
            uninstall_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {err_color} !important;
                    background: transparent;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 8px;
                    padding: 8px 16px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background: rgba(235, 87, 87, 0.12);
                    border-color: {err_color};
                }}
            """)
            uninstall_btn.clicked.connect(self.uninstall_assela)
            layout.addWidget(uninstall_btn)

        self.tab_widget.addTab(tab, "ASSella")

    def _create_downloads_tab(self) -> None:
        """Create the Downloads settings tab with primary settings."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(24)

        # Download Settings Card
        dl_card, dl_layout = self._create_card_frame("Download Settings")

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

        self.check_updates_on_boot_checkbox = create_checkbox_setting(
            "Check Updates on Boot",
            "check_updates_on_boot",
            True,
            self,
            "Automatically check for game updates in the background on startup."
        )
        dl_layout.addWidget(self.check_updates_on_boot_checkbox)

        dl_layout.addSpacing(8)

        # Inputs Grid for Download Settings (Download Location, Max Downloads)
        loc_row = QHBoxLayout()
        loc_row.setContentsMargins(2, 2, 2, 2)
        loc_row.setSpacing(10)

        dl_dir_label = QLabel("Default Download Location:")
        dl_dir_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
        dl_dir_label.setToolTip("Direct downloads to this folder/library instead of prompting for every game.")
        loc_row.addWidget(dl_dir_label, 1)

        self.dl_location_combo = QComboBox()
        self.dl_location_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dl_location_combo.addItem("Ask Every Time", "")

        def _fmt_path(p: str) -> str:
            if not p or p == "Ask Every Time":
                return "Ask Every Time"
            norm = os.path.normpath(p)
            parts = [x for x in norm.split(os.sep) if x]
            if len(parts) >= 2:
                short = f".{parts[-2]}/{parts[-1]}"
            elif parts:
                short = f".{parts[-1]}"
            else:
                short = norm
            if len(short) > 22:
                short = short[:19] + "..."
            return short

        # Load detected Steam libraries
        from core import steam_helpers
        detected_libs = steam_helpers.get_steam_libraries()
        for lib in detected_libs:
            self.dl_location_combo.addItem(_fmt_path(lib), lib)
            
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
            self.dl_location_combo.insertItem(1, _fmt_path(current_val), current_val)
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
                        insert_pos = self.dl_location_combo.count() - 1
                        self.dl_location_combo.insertItem(insert_pos, _fmt_path(path), path)
                        self.dl_location_combo.setCurrentIndex(insert_pos)
                else:
                    self.dl_location_combo.setCurrentIndex(0)
                    
        self.dl_location_combo.currentIndexChanged.connect(on_dl_location_changed)
        loc_row.addWidget(self.dl_location_combo)
        dl_layout.addLayout(loc_row)

        slider_layout = QHBoxLayout()
        slider_layout.setContentsMargins(2, 2, 2, 2)
        slider_layout.setSpacing(10)

        max_dl_label = QLabel("Concurrent Downloads:")
        max_dl_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
        max_dl_label.setToolTip("Set maximum concurrent downloads (1-30). Lower values (e.g. 1-2) reduce network usage.")
        slider_layout.addWidget(max_dl_label)

        self.max_downloads_slider = QSlider(Qt.Orientation.Horizontal)
        self.max_downloads_slider.setRange(1, 30)
        self.max_downloads_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.max_downloads_slider.setTickInterval(1)
        self.max_downloads_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: none;
                height: 6px;
                background: rgba(255, 255, 255, 0.1);
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: %s;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: %s;
                border: none;
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: white;
            }
        """ % (self.accent_color, self.accent_color))
        
        current_max = self.settings.value("max_downloads", 8, type=int)
        self.max_downloads_slider.setValue(current_max)
        
        self.max_downloads_val_lbl = QLabel(str(current_max))
        self.max_downloads_val_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.8); font-size: 9pt; font-weight: bold; border: none; background: transparent;")
        self.max_downloads_val_lbl.setFixedWidth(30)
        self.max_downloads_slider.valueChanged.connect(lambda val: self.max_downloads_val_lbl.setText(str(val)))
        
        slider_layout.addWidget(self.max_downloads_slider, 1)
        slider_layout.addWidget(self.max_downloads_val_lbl)
        
        dl_layout.addLayout(slider_layout)
        layout.addWidget(dl_card)
        layout.addStretch()

        self.tab_widget.addTab(tab, "Downloads")

    def _create_advanced_tab(self) -> None:
        """Create the Advanced settings tab with niche/specialized settings."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(24)

        # Advanced Downloads Card
        adv_card, adv_layout = self._create_card_frame("Advanced Download Settings")

        self.auto_skip_single_choice_checkbox = create_checkbox_setting(
            "Skip single-choice selection",
            "auto_skip_single_choice",
            False,
            self,
            "Automatically skip selection when only one option exists.",
            show_description=False,
        )
        adv_layout.addWidget(self.auto_skip_single_choice_checkbox)

        self.hide_macos_depots_checkbox = create_checkbox_setting(
            "Hide macOS depots in depot selection",
            "hide_macos_depots",
            True,
            self,
            "Hide macOS platform depots to reduce clutter.",
            show_description=False,
        )
        adv_layout.addWidget(self.hide_macos_depots_checkbox)

        self.hide_android_depots_checkbox = create_checkbox_setting(
            "Hide Android depots in depot selection",
            "hide_android_depots",
            True,
            self,
            "Hide Android platform depots to reduce clutter.",
            show_description=False,
        )
        adv_layout.addWidget(self.hide_android_depots_checkbox)

        self.filter_soundtracks_checkbox = create_checkbox_setting(
            "Filter Soundtracks and OSTs from Depots",
            "filter_soundtracks",
            True,
            self,
            "Filter out soundtrack and OST depots when downloading game files.",
            show_description=False,
        )
        adv_layout.addWidget(self.filter_soundtracks_checkbox)

        self.filter_search_blacklist_checkbox = create_checkbox_setting(
            "Filter Blacklisted Keywords in Search",
            "filter_search_blacklist",
            False,
            self,
            "Hide soundtracks, artbooks, tools, and demos from manifest search results.",
            show_description=False,
        )
        adv_layout.addWidget(self.filter_search_blacklist_checkbox)

        layout.addWidget(adv_card)

        # Workshop Downloader Settings Card
        ws_card, ws_layout = self._create_card_frame("Advanced Workshop Settings")

        self.workshop_steam_checkbox = create_checkbox_setting(
            "Enable Steam Integration for Workshop Downloads",
            "workshop_steam_enabled",
            True,
            self,
            "Directs workshop downloads to your detected Steam library directories.",
            show_description=False,
        )
        ws_layout.addWidget(self.workshop_steam_checkbox)

        # Workshop grid layout for clean alignment
        ws_grid = QGridLayout()
        ws_grid.setContentsMargins(4, 4, 4, 4)
        ws_grid.setSpacing(12)
        ws_grid.setColumnStretch(0, 0)
        ws_grid.setColumnStretch(1, 1)

        ws_max_dl_label = QLabel("Max Concurrent Workshop Downloads:")
        ws_max_dl_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
        
        from PyQt6.QtWidgets import QSlider
        self.workshop_max_dl_slider = QSlider(Qt.Orientation.Horizontal)
        self.workshop_max_dl_slider.setRange(1, 30)
        self.workshop_max_dl_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.workshop_max_dl_slider.setTickInterval(1)
        self.workshop_max_dl_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: none;
                height: 6px;
                background: rgba(255, 255, 255, 0.1);
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: %s;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: %s;
                border: none;
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: white;
            }
        """ % (self.accent_color, self.accent_color))
        
        current_ws_max = self.settings.value("workshop_max_downloads", 8, type=int)
        if current_ws_max < 1 or current_ws_max > 30:
            current_ws_max = 8
        self.workshop_max_dl_slider.setValue(current_ws_max)
        
        self.workshop_max_dl_val_lbl = QLabel(str(current_ws_max))
        self.workshop_max_dl_val_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.8); font-size: 9pt; font-weight: bold; border: none; background: transparent;")
        self.workshop_max_dl_val_lbl.setFixedWidth(30)
        self.workshop_max_dl_slider.valueChanged.connect(lambda val: self.workshop_max_dl_val_lbl.setText(str(val)))
        
        ws_slider_layout = QHBoxLayout()
        ws_slider_layout.addWidget(self.workshop_max_dl_slider, 1)
        ws_slider_layout.addWidget(self.workshop_max_dl_val_lbl)
        
        ws_grid.addWidget(ws_max_dl_label, 0, 0)
        ws_grid.addLayout(ws_slider_layout, 0, 1)

        ws_cell_id_label = QLabel("Cell ID:")
        ws_cell_id_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
        self.workshop_cell_id_input = QLineEdit()
        self.workshop_cell_id_input.setPlaceholderText("Optional")
        self.workshop_cell_id_input.setText(self.settings.value("workshop_cell_id", "", type=str))

        ws_grid.addWidget(ws_cell_id_label, 1, 0)
        ws_grid.addWidget(self.workshop_cell_id_input, 1, 1)

        ws_layout.addLayout(ws_grid)
        layout.addWidget(ws_card)

        # Achievements Card
        pp_card, pp_layout = self._create_card_frame("Advanced Post-Processing")
        self.achievements_checkbox = create_checkbox_setting(
            "Generate Achievements (Recommended Off)",
            "generate_achievements",
            False,
            self,
            "Automatically generate achievement configuration files during post-processing.",
            show_description=False,
        )
        pp_layout.addWidget(self.achievements_checkbox)

        self.auto_apply_goldberg_checkbox = create_checkbox_setting(
            "Auto-apply Goldberg on Install (Experimental)",
            "auto_apply_goldberg",
            False,
            self,
            "Automatically apply Goldberg Steam Emulator after game download finishes.",
            show_description=False,
        )
        pp_layout.addWidget(self.auto_apply_goldberg_checkbox)

        layout.addWidget(pp_card)
        layout.addStretch()

        self.tab_widget.addTab(tab, "Advanced")

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
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(24)

        # API Keys Card
        key_card, key_layout = self._create_card_frame("API Keys")
        key_layout.setSpacing(12)

        morrenus_layout, self.api_key_input = self._create_api_key_setting(
            "Hubcap API Key:",
            "Paste your Hubcap API key",
            "morrenus_api_key",
            help_url="https://hubcapmanifest.com/",
        )
        key_layout.addLayout(morrenus_layout)
        layout.addWidget(key_card)

        # Stats Card
        stats_card, stats_layout = self._create_card_frame("Hubcap Stats")
        stats_layout.setContentsMargins(12, 12, 12, 12)

        self.morrenus_stats_widget = MorrenusStatsWidget()
        stats_layout.addWidget(self.morrenus_stats_widget)
        layout.addWidget(stats_card)

        layout.addStretch()

        # Connect tab change for lazy loading stats
        self.morrenus_tab_initialized = False
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        self.tab_widget.addTab(tab, "Integrations")

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
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(24)

        # Tools Card
        tools_card, tools_layout = self._create_card_frame("Tools")

        tools_desc = QLabel("Run Steam achievement configurator or Steamless DRM removal utilities.")
        tools_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;")
        tools_desc.setWordWrap(True)
        tools_layout.addWidget(tools_desc)

        tools_btn_row = QHBoxLayout()
        tools_btn_row.setContentsMargins(0, 4, 0, 4)
        tools_btn_row.setSpacing(10)

        tool_btn_style = """
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 8px;
                color: #FFFFFF;
                padding: 7px 14px;
                font-size: 9.5pt;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.16);
                border-color: rgba(255, 255, 255, 0.32);
            }
            QPushButton:disabled {
                background-color: rgba(255, 255, 255, 0.03) !important;
                border: 1px solid rgba(255, 255, 255, 0.08) !important;
                color: rgba(255, 255, 255, 0.3) !important;
            }
        """

        self.configure_achievements_btn = QPushButton("Achievements")
        self.configure_achievements_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.configure_achievements_btn.setStyleSheet(tool_btn_style)
        self.configure_achievements_btn.setToolTip("Perform one-time setup and authenticate Steam for achievements.")
        self.configure_achievements_btn.clicked.connect(self.run_schema_grabber_manually)
        tools_btn_row.addWidget(self.configure_achievements_btn)

        self.steamless_py_btn = QPushButton("Steamless (Python)")
        self.steamless_py_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.steamless_py_btn.setStyleSheet(tool_btn_style)
        self.steamless_py_btn.setToolTip("Run Steamless AIO (Python) manually on a game .exe.")
        self.steamless_py_btn.clicked.connect(self.run_steamless_aio_manually)
        tools_btn_row.addWidget(self.steamless_py_btn)

        self.steamless_legacy_btn = QPushButton("Steamless (.NET CLI)")
        self.steamless_legacy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.steamless_legacy_btn.setStyleSheet(tool_btn_style)
        self.steamless_legacy_btn.setToolTip("Run Steamless (.NET 9 CLI) manually on a game .exe.")
        self.steamless_legacy_btn.clicked.connect(self.run_steamless_manually)
        tools_btn_row.addWidget(self.steamless_legacy_btn)

        tools_btn_row.addStretch()
        tools_layout.addLayout(tools_btn_row)

        layout.addWidget(tools_card)

        # ASSfixer Card (Linux/Steam Deck)
        if sys.platform == "linux":
            assfixer_card, assfixer_layout = self._create_card_frame("ASSfixer")

            assfixer_desc = QLabel("Validate, repair and update slsconfig")
            assfixer_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;")
            assfixer_desc.setWordWrap(True)
            assfixer_layout.addWidget(assfixer_desc)

            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 4, 0, 4)
            btn_row.setSpacing(10)

            btn_style = """
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.08);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    border-radius: 8px;
                    color: #FFFFFF;
                    padding: 7px 16px;
                    font-size: 9.5pt;
                    font-weight: 500;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 0.16);
                    border-color: rgba(255, 255, 255, 0.32);
                }
                QPushButton:disabled {
                    background-color: rgba(255, 255, 255, 0.03) !important;
                    border: 1px solid rgba(255, 255, 255, 0.08) !important;
                    color: rgba(255, 255, 255, 0.3) !important;
                }
            """

            self.assfixer_check_btn = QPushButton("Check")
            self.assfixer_check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.assfixer_check_btn.setStyleSheet(btn_style)
            self.assfixer_check_btn.clicked.connect(self._run_assfixer_check)
            btn_row.addWidget(self.assfixer_check_btn)

            self.assfixer_repair_btn = QPushButton("Repair / Resync")
            self.assfixer_repair_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.assfixer_repair_btn.setStyleSheet(btn_style)
            self.assfixer_repair_btn.setEnabled(False)
            self.assfixer_repair_btn.clicked.connect(self._run_assfixer_repair)
            btn_row.addWidget(self.assfixer_repair_btn)

            self.assfixer_restore_btn = QPushButton("Restore Backup")
            self.assfixer_restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.assfixer_restore_btn.setStyleSheet(btn_style)

            try:
                from utils.assfixer import has_config_backup
                self.assfixer_restore_btn.setEnabled(has_config_backup())
            except Exception:
                self.assfixer_restore_btn.setEnabled(False)
            self.assfixer_restore_btn.clicked.connect(self._run_assfixer_restore)
            btn_row.addWidget(self.assfixer_restore_btn)

            btn_row.addStretch()
            assfixer_layout.addLayout(btn_row)

            self.assfixer_status_lbl = QLabel("")
            self.assfixer_status_lbl.setStyleSheet("color: #a9b1d6; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
            self.assfixer_status_lbl.setWordWrap(True)
            self.assfixer_status_lbl.hide()
            assfixer_layout.addWidget(self.assfixer_status_lbl)

            layout.addWidget(assfixer_card)

        # SLS Inheritance Card (Testing)
        if sys.platform == "linux":
            sls_inh_card, sls_inh_layout = self._create_card_frame("SLS inheritance (testing)")

            sls_inh_desc = QLabel("orphan configs and external installtion manger (beta testing)")
            sls_inh_desc.setStyleSheet(
                "color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;"
            )
            sls_inh_desc.setWordWrap(True)
            sls_inh_layout.addWidget(sls_inh_desc)

            sls_inh_row = QHBoxLayout()
            sls_inh_row.setContentsMargins(0, 4, 0, 4)
            sls_inh_row.setSpacing(10)

            self.sls_inh_btn = QPushButton("Manage SLS Inheritance")
            self.sls_inh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.sls_inh_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.08);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    border-radius: 8px;
                    color: #FFFFFF;
                    padding: 7px 16px;
                    font-size: 9.5pt;
                    font-weight: 500;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 0.16);
                    border-color: rgba(255, 255, 255, 0.32);
                }
            """)
            self.sls_inh_btn.clicked.connect(self._open_sls_inheritance_dialog)
            sls_inh_row.addWidget(self.sls_inh_btn)
            sls_inh_row.addStretch()
            sls_inh_layout.addLayout(sls_inh_row)

            layout.addWidget(sls_inh_card)

        # Windows Registry Card
        if sys.platform == "win32":
            reg_card, reg_layout = self._create_card_frame("Windows Registry")

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

            layout.addWidget(reg_card)

        # Logging Configuration Card (Single Row Layout)
        log_card, log_layout = self._create_card_frame("Logging Configuration")

        log_row = QHBoxLayout()
        log_row.setContentsMargins(0, 4, 0, 4)
        log_row.setSpacing(24)

        # Log Level
        level_box = QHBoxLayout()
        level_box.setSpacing(8)
        level_label = QLabel("Log Level:")
        level_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500;")
        level_label.setToolTip(
            "Minimum severity of messages to log.\n"
            "Select NONE to disable all logging (improves performance)."
        )
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "NONE"])
        _current_level = self.settings.value("log_filter_level", "DEBUG") or "DEBUG"
        idx = self.log_level_combo.findText(_current_level)
        self.log_level_combo.setCurrentIndex(idx if idx >= 0 else 0)
        level_box.addWidget(level_label)
        level_box.addWidget(self.log_level_combo)
        log_row.addLayout(level_box)

        # Log Filter
        cat_box = QHBoxLayout()
        cat_box.setSpacing(8)
        cat_label = QLabel("Log Filter:")
        cat_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500;")
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
        cat_box.addWidget(cat_label)
        cat_box.addWidget(self.log_category_combo)
        log_row.addLayout(cat_box)

        log_row.addStretch()
        log_layout.addLayout(log_row)

        _log_note = QLabel(
            "Changes take effect immediately when you click OK."
        )
        _log_note.setStyleSheet("color: #888888; font-size: 11px;")
        _log_note.setWordWrap(True)
        log_layout.addWidget(_log_note)
        layout.addWidget(log_card)

        layout.addStretch()
        self.tab_widget.addTab(tab, "Tools")



    # ── ASSella Manager helpers ───────────────────────────────────────────

    def _run_training_wheels(self) -> None:
        """Manually trigger the Training Wheels Protocol setup guide."""
        from ui.dialogs.training_wheels import TrainingWheelsDialog
        dlg = TrainingWheelsDialog(parent=self, manual=True)
        if dlg.exec():
            # Refresh local UI controls to match applied settings
            if hasattr(self, "smart_depot_selection_checkbox") and self.smart_depot_selection_checkbox:
                self.smart_depot_selection_checkbox.setChecked(self.settings.value("smart_depot_selection", True, type=bool))
            if hasattr(self, "isp_gateway_combo") and self.isp_gateway_combo:
                idx = self.isp_gateway_combo.findData(self.settings.value("isp_bypass_mode", "auto", type=str))
                if idx >= 0:
                    self.isp_gateway_combo.setCurrentIndex(idx)
            if hasattr(self, "experimental_acf_independent_checkbox") and self.experimental_acf_independent_checkbox:
                self.experimental_acf_independent_checkbox.setChecked(self.settings.value("experimental_acf_independent", False, type=bool))
            if hasattr(self, "achievements_checkbox") and self.achievements_checkbox:
                self.achievements_checkbox.setChecked(self.settings.value("generate_achievements", False, type=bool))
            if hasattr(self, "hide_macos_depots_checkbox") and self.hide_macos_depots_checkbox:
                self.hide_macos_depots_checkbox.setChecked(self.settings.value("hide_macos_depots", True, type=bool))
            if hasattr(self, "hide_android_depots_checkbox") and self.hide_android_depots_checkbox:
                self.hide_android_depots_checkbox.setChecked(self.settings.value("hide_android_depots", True, type=bool))

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
        """Helper to add a left-aligned tool button with an always-visible description label stacked below."""
        row_box = QVBoxLayout()
        row_box.setContentsMargins(0, 2, 0, 6)
        row_box.setSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)

        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 8px;
                color: #FFFFFF;
                padding: 7px 16px;
                font-size: 9.5pt;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.16);
                border-color: rgba(255, 255, 255, 0.32);
            }
            QPushButton:disabled {
                background-color: rgba(255, 255, 255, 0.06) !important;
                border: 1px solid rgba(255, 255, 255, 0.12) !important;
                color: rgba(255, 255, 255, 0.38) !important;
            }
        """)
        btn.clicked.connect(slot)
        btn_row.addWidget(btn)
        btn_row.addStretch()
        row_box.addLayout(btn_row)

        if tooltip:
            desc_lbl = QLabel(tooltip)
            desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;")
            desc_lbl.setWordWrap(True)
            row_box.addWidget(desc_lbl)

        layout.addLayout(row_box)
        return btn


    def _open_sls_inheritance_dialog(self) -> None:
        try:
            from ui.dialogs.sls_inheritance import SlsInheritanceDialog
            parent = self.parent() if self.parent() else self
            dlg = SlsInheritanceDialog(parent)
            dlg.exec()
        except Exception as e:
            logger.error(f"Error opening SLS Inheritance dialog: {e}", exc_info=True)

    def _run_assfixer_check(self) -> None:
        logger.info("ASSfixer check triggered from Settings -> Tools tab.")
        self.assfixer_check_btn.setEnabled(False)
        self.assfixer_status_lbl.setText("Checking config against upstream template...")
        self.assfixer_status_lbl.setStyleSheet("color: #7aa2f7; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
        self.assfixer_status_lbl.show()

        import threading
        def _target():
            try:
                from utils.assfixer import check_config_status
                res = check_config_status(online=True)
            except Exception as e:
                res = (True, f"Check failed: {e}", [str(e)])
            self.assfixer_done_signal.emit(res)

        t = threading.Thread(target=_target, daemon=True)
        t.start()

    @pyqtSlot(tuple)
    def _handle_assfixer_check_done(self, result) -> None:
        needs_repair, summary, details = result
        logger.info(f"ASSfixer check UI handler received result: needs_repair={needs_repair}, summary='{summary}', details_count={len(details)}")
        self.assfixer_check_btn.setEnabled(True)
        if needs_repair:
            self.assfixer_repair_btn.setEnabled(True)
            self.assfixer_status_lbl.setText(f"🟡 {summary}")
            self.assfixer_status_lbl.setStyleSheet("color: #e0af68; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
            if details:
                self.assfixer_status_lbl.setToolTip("\n".join(details))
        else:
            self.assfixer_repair_btn.setEnabled(True)
            self.assfixer_status_lbl.setText(f"🟢 {summary}")
            self.assfixer_status_lbl.setStyleSheet("color: #9ece6a; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
            self.assfixer_status_lbl.setToolTip("")


    def _run_assfixer_repair(self) -> None:
        from ui.dialogs.assfixer_confirm import AssfixerConfirmDialog
        accent = self.settings.value("accent_color", "#C06C84")
        bg = self.settings.value("background_color", "#111318")
        confirm_dlg = AssfixerConfirmDialog(parent=self, accent_color=accent, bg_color=bg)
        if confirm_dlg.exec() != AssfixerConfirmDialog.DialogCode.Accepted:
            return

        self.assfixer_repair_btn.setEnabled(False)
        self.assfixer_status_lbl.setText("Repairing and synchronizing config...")
        self.assfixer_status_lbl.setStyleSheet("color: #7aa2f7; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
        self.assfixer_status_lbl.show()

        import threading
        def _target():
            try:
                from utils.assfixer import repair_and_sync_config, has_config_backup
                success, msg, bak_path = repair_and_sync_config(online=True)
                has_bak = has_config_backup()
                res = (success, msg, has_bak)
            except Exception as e:
                res = (False, f"Repair error: {e}", False)
            self.assfixer_repair_done_signal.emit(res)

        t = threading.Thread(target=_target, daemon=True)
        t.start()

    @pyqtSlot(object)
    def _handle_assfixer_repair_done(self, result) -> None:
        success, msg, has_bak = result
        self.assfixer_repair_btn.setEnabled(True)
        self.assfixer_restore_btn.setEnabled(has_bak)
        if success:
            self.assfixer_status_lbl.setText(f"🟢 {msg}")
            self.assfixer_status_lbl.setStyleSheet("color: #9ece6a; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
            QMessageBox.information(self, "ASSfixer", msg)
        else:
            self.assfixer_status_lbl.setText(f"🔴 {msg}")
            self.assfixer_status_lbl.setStyleSheet("color: #f7768e; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
            QMessageBox.warning(self, "ASSfixer", msg)


    def _run_assfixer_restore(self) -> None:
        reply = QMessageBox.question(
            self,
            "Restore Config Backup",
            "Are you sure you want to restore the latest config backup?\nThis will overwrite current config.yaml.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            from utils.assfixer import restore_config_backup, has_config_backup
            success, msg, bak_path = restore_config_backup()
            self.assfixer_restore_btn.setEnabled(has_config_backup())
            if success:
                self.assfixer_status_lbl.setText(f"🟢 {msg}")
                self.assfixer_status_lbl.setStyleSheet("color: #9ece6a; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
                self.assfixer_status_lbl.show()
                QMessageBox.information(self, "Backup Restored", msg)
            else:
                self.assfixer_status_lbl.setText(f"🔴 {msg}")
                self.assfixer_status_lbl.setStyleSheet("color: #f7768e; font-size: 8.5pt; margin-top: 2px; border: none; background: transparent;")
                self.assfixer_status_lbl.show()
                QMessageBox.warning(self, "Restore Failed", msg)

        except Exception as e:
            QMessageBox.critical(self, "Restore Error", str(e))



    def _create_style_tab(self) -> None:
        """Create the Theme settings tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(24)

        desc_lbl = QLabel("Customize the visual appearance, accent colors, and font settings of ASSella.")
        desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; border: none; background: transparent;")
        layout.addWidget(desc_lbl)

        # Colors & Font Card
        theme_card, theme_card_lay = self._create_card_frame("Theme")
        theme_layout = QGridLayout()
        theme_layout.setContentsMargins(4, 4, 4, 4)
        theme_layout.setSpacing(10)

        # Accent color swatch row
        lbl_acc = QLabel("Accent Color:")
        lbl_acc.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
        theme_layout.addWidget(lbl_acc, 0, 0)

        self.accent_color_button = QPushButton()
        self.accent_color_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.accent_color_button.setFixedSize(60, 24)
        self.accent_color_button.setStyleSheet(
            f"background-color: {self._user_accent_color}; border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 4px;"
        )
        self.accent_reset_button = QPushButton("Reset")
        self.accent_reset_button.setFixedWidth(70)
        theme_layout.addWidget(self.accent_color_button, 0, 1)
        theme_layout.addWidget(self.accent_reset_button, 0, 2)

        # Background color swatch row
        lbl_bg = QLabel("Background Color:")
        lbl_bg.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
        theme_layout.addWidget(lbl_bg, 1, 0)

        self.bg_color_button = QPushButton()
        self.bg_color_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bg_color_button.setFixedSize(60, 24)
        self.bg_color_button.setStyleSheet(
            f"background-color: {self._user_background_color}; border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 4px;"
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

        lbl_font = QLabel("System Font:")
        lbl_font.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
        theme_layout.addWidget(lbl_font, 2, 0)
        theme_layout.addWidget(self.font_button, 2, 1)
        theme_layout.addWidget(self.font_reset_button, 2, 2)

        # Material presets row
        lbl_preset = QLabel("Material Presets:")
        lbl_preset.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
        theme_layout.addWidget(lbl_preset, 3, 0)

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

        theme_card_lay.addLayout(theme_layout)
        layout.addWidget(theme_card)

        # Interface Options Card
        disp_card, disp_layout = self._create_card_frame("Interface Options")

        is_top = self.settings.value("titlebar_position", "bottom", type=str) == "top"
        self.titlebar_position_checkbox = create_checkbox_setting(
            "Move Titlebar to Window Top",
            "titlebar_position_top",
            is_top,
            self,
            "Places the navigation bar / titlebar at the top of the window instead of the bottom.",
            show_description=False,
        )
        def _on_top_toggled(checked: bool):
            val = "top" if checked else "bottom"
            self.settings.setValue("titlebar_position", val)
            self.on_titlebar_position_changed(2 if checked else 0)

        self.titlebar_position_checkbox.toggled.connect(_on_top_toggled)
        disp_layout.addWidget(self.titlebar_position_checkbox)

        self.remember_origins_checkbox = create_checkbox_setting(
            "Remember your origins",
            "remember_origins",
            False,
            self,
            "Subtly displays the Wired layout background.",
            show_description=False,
        )
        self.remember_origins_checkbox.toggled.connect(self._on_origins_toggled)
        disp_layout.addWidget(self.remember_origins_checkbox)

        self.simplify_denuvo_status_checkbox = create_checkbox_setting(
            "Show hypervisor and uncracked as Not Cracked",
            "simplify_denuvo_status",
            False,
            self,
            "Displays both Denuvo Hypervisor and Denuvo Uncracked games as simply Denuvo Uncracked.",
            show_description=False,
        )
        disp_layout.addWidget(self.simplify_denuvo_status_checkbox)

        layout.addWidget(disp_card)
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
        font, ok = FontSelectionDialog.get_font(self.current_font, self)
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
        if hasattr(self, "api_key_input") and self.api_key_input is not None:
            api_key = self.api_key_input.text().strip()
            self.settings.setValue("morrenus_api_key", api_key)
        if hasattr(self, "use_wirecutter_checkbox") and self.use_wirecutter_checkbox is not None:
            try:
                self.settings.setValue("use_wirecutter", self.use_wirecutter_checkbox.isChecked())
            except Exception:
                pass
        if hasattr(self, "wirecutter_url_input") and self.wirecutter_url_input is not None:
            try:
                self.settings.setValue("wirecutter_url", self.wirecutter_url_input.text().strip())
            except Exception:
                pass
        if hasattr(self, "steam_username_input") and self.steam_username_input is not None:
            self.settings.setValue("steam_username", self.steam_username_input.text().strip())
        if hasattr(self, "steam_password_input") and self.steam_password_input is not None:
            from utils.helpers import encrypt_string
            encrypted_pass = encrypt_string(self.steam_password_input.text())
            self.settings.setValue("steam_password", encrypted_pass)
        if hasattr(self, "log_level_combo") and self.log_level_combo is not None:
            self.settings.setValue("log_filter_level", self.log_level_combo.currentText())
        if hasattr(self, "log_category_combo") and self.log_category_combo is not None:
            self.settings.setValue("log_filter_category", self.log_category_combo.currentText())

        # Apply logging changes immediately
        try:
            from utils.logger import update_log_filters
            update_log_filters()
        except Exception:
            pass

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
        if self.auto_apply_goldberg_checkbox is not None:
            self.settings.setValue(
                "auto_apply_goldberg", self.auto_apply_goldberg_checkbox.isChecked()
            )

        if self.workshop_steam_checkbox is not None:
            self.settings.setValue(
                "workshop_steam_enabled",
                self.workshop_steam_checkbox.isChecked(),
            )
        if hasattr(self, "workshop_max_dl_slider") and self.workshop_max_dl_slider is not None:
            self.settings.setValue(
                "workshop_max_downloads",
                self.workshop_max_dl_slider.value(),
            )
        if self.workshop_cell_id_input is not None:
            self.settings.setValue(
                "workshop_cell_id",
                self.workshop_cell_id_input.text().strip(),
            )
        
        # Save Consolidated Steamless DRM Remover settings
        if hasattr(self, "steamless_remover_combo") and self.steamless_remover_combo is not None:
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
        if hasattr(self, "filter_soundtracks_checkbox") and self.filter_soundtracks_checkbox is not None:
            self.settings.setValue("filter_soundtracks", self.filter_soundtracks_checkbox.isChecked())
        if hasattr(self, "filter_search_blacklist_checkbox") and self.filter_search_blacklist_checkbox is not None:
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
        
        if hasattr(self, "update_interval_slider") and self.update_interval_slider:
            self.settings.setValue(
                "update_check_interval_minutes", self.update_interval_slider.value() * 5
            )
            if self.main_window and hasattr(self.main_window, "apply_update_timer_settings"):
                self.main_window.apply_update_timer_settings()

        if hasattr(self, "check_updates_on_boot_checkbox"):
            self.settings.setValue(
                "check_updates_on_boot",
                self.check_updates_on_boot_checkbox.isChecked()
            )

        if hasattr(self, "update_provider_combo") and self.update_provider_combo:
            self.settings.setValue(
                "update_check_api_provider",
                self.update_provider_combo.currentData() or "steampics"
            )

        val = 8
        if hasattr(self, "max_downloads_slider") and self.max_downloads_slider:
            try:
                val = max(1, min(30, int(self.max_downloads_slider.value())))
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
        if hasattr(self, "hide_android_depots_checkbox"):
            try:
                self.settings.setValue("hide_android_depots", self.hide_android_depots_checkbox.isChecked())
            except RuntimeError:
                pass
        if hasattr(self, "isp_gateway_combo") and self.isp_gateway_combo is not None:
            mode = self.isp_gateway_combo.currentData() or "auto"
            self.settings.setValue("isp_bypass_mode", mode)
            self.settings.setValue("isp_bypass_hubcap", mode != "disabled")
            if mode == "disabled":
                try:
                    from utils.isp_bypass import TorManager
                    TorManager.stop_tor()
                except Exception:
                    pass

        if hasattr(self, "experimental_acf_independent_checkbox") and self.experimental_acf_independent_checkbox is not None:
            is_enabled = self.experimental_acf_independent_checkbox.isChecked()
            self.settings.setValue("experimental_acf_independent", is_enabled)
            if is_enabled:
                try:
                    from utils.yaml_config_manager import ensure_slssteam_prerequisites
                    ensure_slssteam_prerequisites()
                except Exception:
                    pass

    def _on_clear_update_cache_clicked(self):
        """Purges local update status cache, branch cache, and stored build IDs."""
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

        # Clear QSettings last_checked and installed_buildid keys
        for key in list(self.settings.allKeys()):
            if key.startswith("last_checked_") or key.startswith("installed_buildid/"):
                self.settings.remove(key)
        self.settings.sync()

        self.clear_update_cache_btn.setText("Cleared!")
        self.clear_update_cache_btn.setEnabled(False)
        QTimer.singleShot(2500, lambda: (
            self.clear_update_cache_btn.setText("Clear Cache"),
            self.clear_update_cache_btn.setEnabled(True)
        ))
        QMessageBox.information(
            self,
            "Cache Cleared",
            "Update status, build ID, and branch caches have been cleared successfully.\n\n"
            "Fresh live data will be queried next time you check for updates or open game details."
        )

    def _on_isp_gateway_changed(self, index: int) -> None:
        """Saves selected mode immediately and triggers Tor warm boot if needed."""
        if not hasattr(self, "isp_gateway_combo") or not self.isp_gateway_combo:
            return
        mode = self.isp_gateway_combo.currentData() or "auto"
        self.settings.setValue("isp_bypass_mode", mode)
        self.settings.setValue("isp_bypass_hubcap", True)

        if mode == "tor":
            import threading
            from utils.isp_bypass import TorManager
            threading.Thread(target=TorManager.start_tor_if_needed, daemon=True).start()


    def _test_single_gateway(self, gateway_key: str, btn: QPushButton, label: str) -> None:
        """Runs an individual gateway health check in a background thread and updates button."""
        btn.setEnabled(False)
        btn.setText("Testing...")
        btn.setStyleSheet("""
            QPushButton {
                background: #3b4261;
                border: 1px solid #e0af68;
                border-radius: 6px;
                padding: 5px 8px;
                color: #e0af68;
                font-size: 8.5pt;
                font-weight: bold;
                min-height: 26px;
            }
        """)

        import threading
        def _target():
            import utils.isp_bypass as isp
            if gateway_key == "direct":
                ok, status, lat = isp.test_gateway_direct()
            elif gateway_key == "doh":
                ok, status, lat = isp.test_gateway_doh()
            elif gateway_key == "tor":
                ok, status, lat = isp.test_gateway_tor()
            elif gateway_key == "wirecutter":
                ok, status, lat = isp.test_gateway_wirecutter()
            else:
                ok, status, lat = False, "Unknown", 0

            QMetaObject.invokeMethod(
                self,
                "_handle_gateway_test_done",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(object, (btn, label, ok, status, lat)),
            )

        threading.Thread(target=_target, daemon=True).start()

    @pyqtSlot(object)
    def _handle_gateway_test_done(self, data) -> None:
        btn, label, ok, status, lat = data
        btn.setEnabled(True)
        if ok:
            btn.setText(f"{label}: OK")
            btn.setToolTip(f"{label} Gateway: {status}")
            btn.setStyleSheet("""
                QPushButton {
                    background: #1b5e20;
                    border: 1px solid #4caf50;
                    border-radius: 6px;
                    padding: 5px 8px;
                    color: #a5d6a7;
                    font-size: 8.5pt;
                    font-weight: bold;
                    min-height: 26px;
                }
                QPushButton:hover {
                    background: #2e7d32;
                }
            """)
        else:
            btn.setText(f"{label}: {status}")
            btn.setToolTip(f"{label} Gateway Failed: {status}")
            btn.setStyleSheet("""
                QPushButton {
                    background: #b71c1c;
                    border: 1px solid #ef5350;
                    border-radius: 6px;
                    padding: 5px 8px;
                    color: #ffcdd2;
                    font-size: 8.5pt;
                    font-weight: bold;
                    min-height: 26px;
                }
                QPushButton:hover {
                    background: #c62828;
                }
            """)

    def _on_isp_bypass_toggled(self, state) -> None:
        pass

    def _on_experimental_acf_toggled(self, state):
        is_checked = (state == Qt.CheckState.Checked.value or state == True or state == 2)
        
        # 1. prompt_steam_restart_checkbox
        if hasattr(self, "prompt_steam_restart_checkbox") and self.prompt_steam_restart_checkbox is not None:
            if is_checked:
                if not hasattr(self, "_saved_prompt_restart_pref"):
                    self._saved_prompt_restart_pref = self.prompt_steam_restart_checkbox.isChecked()
                self.prompt_steam_restart_checkbox.setChecked(False)
                self.prompt_steam_restart_checkbox.setLocked(True, "Disabled while 'SLSsteam API' is active.")
            else:
                self.prompt_steam_restart_checkbox.setLocked(False)
                if hasattr(self, "_saved_prompt_restart_pref"):
                    self.prompt_steam_restart_checkbox.setChecked(self._saved_prompt_restart_pref)

        # 2. library_mode_checkbox (Limit Downloads to Steam Libraries)
        if hasattr(self, "library_mode_checkbox") and self.library_mode_checkbox is not None:
            if is_checked:
                if not hasattr(self, "_saved_library_mode_pref"):
                    self._saved_library_mode_pref = self.library_mode_checkbox.isChecked()
                self.library_mode_checkbox.setChecked(True)
                self.library_mode_checkbox.setLocked(True, "Must be enabled when 'SLSsteam API' is active.")
            else:
                self.library_mode_checkbox.setLocked(False)
                if hasattr(self, "_saved_library_mode_pref"):
                    self.library_mode_checkbox.setChecked(self._saved_library_mode_pref)

        # 3. sls_config_management_checkbox (SLS Config Management)
        if hasattr(self, "sls_config_management_checkbox") and self.sls_config_management_checkbox is not None:
            if is_checked:
                if not hasattr(self, "_saved_sls_config_mgmt_pref"):
                    self._saved_sls_config_mgmt_pref = self.sls_config_management_checkbox.isChecked()
                self.sls_config_management_checkbox.setChecked(True)
                self.sls_config_management_checkbox.setLocked(True, "Must be enabled when 'SLSsteam API' is active.")
            else:
                is_sls_detected = False
                try:
                    from ui.dialogs.settings_sls import get_sls_paths
                    is_sls_detected = get_sls_paths()["detected"]
                except Exception:
                    pass
                if sys.platform == "linux" and is_sls_detected:
                    self.sls_config_management_checkbox.setChecked(True)
                    self.sls_config_management_checkbox.setLocked(True, "Enabled because SLSsteam installation was detected.")
                else:
                    self.sls_config_management_checkbox.setLocked(False)
                    if hasattr(self, "_saved_sls_config_mgmt_pref"):
                        self.sls_config_management_checkbox.setChecked(self._saved_sls_config_mgmt_pref)

        # 4. Silently ensure SLS prerequisites (API: yes, LogLevels: 0x2) in config.yaml if checked
        if is_checked:
            try:
                from utils.yaml_config_manager import get_user_config_path, ensure_slssteam_prerequisites
                config_path = get_user_config_path()
                if config_path.exists():
                    ensure_slssteam_prerequisites(config_path)
            except Exception as e:
                logger.warning(f"Failed to ensure SLS prerequisites in config.yaml: {e}")



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
            gif_path = os.path.expanduser("~/.local/share/ACCELA/jumpscare/lain.gif")
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
        if not hasattr(self, "asshead_status_label") or not self.asshead_status_label:
            return

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
