import logging
import os
import sys
from typing import Any, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QMovie
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.dialog_helpers import create_standard_buttons
from ui.dialogs.settings_sls import create_sls_tab
import ui.dialogs.settings_tabs as tabs
from ui.dialogs.settings_tabs import (
    MorrenusStatsWidget,
    WindowsDepotWarningDialog,
    HealthStatusTile,
)
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """Dialog for configuring application settings."""

    assfixer_done_signal = pyqtSignal(tuple)
    assfixer_repair_done_signal = pyqtSignal(tuple)
    sls_version_check_signal = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None, initial_tab: Optional[str] = None):
        super().__init__(parent)
        self.assfixer_done_signal.connect(self._handle_assfixer_check_done)
        self.assfixer_repair_done_signal.connect(self._handle_assfixer_repair_done)
        self.sls_version_check_signal.connect(self._handle_sls_version_check_done)
        self._initial_tab = initial_tab
        self.setWindowTitle("Settings")
        self.setMinimumWidth(525)
        self.setMinimumHeight(650)
        self.resize(525, 650)
        self.settings = get_settings()
        self.main_window = parent
        self.accent_color = self.settings.value("accent_color", "#C06C84")
        self.main_layout = None
        self.tab_widget = None

        # Widget references populated by tabs
        self.library_mode_checkbox = None
        self.auto_skip_single_choice_checkbox = None
        self.smart_depot_selection_checkbox = None
        self.use_lancache_checkbox = None
        self.use_native_steam_dl_checkbox = None
        self.native_steam_action_combo = None
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
        self.show_hidden_depots_selector_checkbox = None
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
        self._original_morrenus_key = self.settings.value("morrenus_api_key", "", type=str)
        self.settings.sync()
        self._original_steam_username = self.settings.value("steam_username", "", type=str)
        from utils.helpers import decrypt_string
        self._original_steam_password = decrypt_string(self.settings.value("steam_password", "", type=str))

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
        self._original_titlebar_position = self.settings.value("titlebar_position", "bottom", type=str)
        self._original_accent_color = self.settings.value("accent_color", "#C06C84", type=str)
        self._original_background_color = self.settings.value("background_color", "#000000", type=str)
        self._original_font = self.settings.value("font", "TrixieCyrG-Plain", type=str)
        self._original_font_size = self.settings.value("font-size", 10, type=int)
        self._original_font_style = self.settings.value("font-style", "Normal", type=str)
        self._original_material_preset = self.settings.value("material_preset", "ocean", type=str)
        self._original_update_interval = self.settings.value("update_check_interval_minutes", 45, type=int)
        self._original_experimental_acf = self.settings.value("experimental_acf_independent", False, type=bool)

        logger.debug("Opening SettingsDialog.")
        self._setup_ui()

        if self.parent():
            from ui.dialogs.dialog_raiser import DialogRaiser
            DialogRaiser(self.parent(), self)

        if self._initial_tab and self.tab_widget:
            for i in range(self.tab_widget.count()):
                if self.tab_widget.tabText(i).lower() == self._initial_tab.lower():
                    self.tab_widget.setCurrentIndex(i)
                    break

    def _setup_ui(self) -> None:
        """Initialize the UI layout."""
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
        tabs.create_assela_tab(self)
        tabs.create_downloads_tab(self)
        tabs.create_advanced_tab(self)
        tabs.create_morrenus_tab(self)
        create_sls_tab(self)
        tabs.create_health_tab(self)
        tabs.create_tools_tab(self)
        tabs.create_style_tab(self)

        # Tab changed listener
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        # Initialize button state after all tabs have been populated
        self._update_achievements_button_state()

        # Sanity check: Check SLS requirements to enable/disable experimental_acf_independent_checkbox
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

        # Apply initial state
        try:
            if (self.experimental_acf_independent_checkbox is not None
                    and self.experimental_acf_independent_checkbox.isChecked()):
                self._on_experimental_acf_toggled(True)
        except Exception:
            pass

    def _on_tab_changed(self, index: int) -> None:
        """Handle tab change events."""
        if (
            self.tab_widget.tabText(index) == "Integrations"
            and not self.morrenus_tab_initialized
        ):
            self.morrenus_tab_initialized = True
            if self.morrenus_stats_widget:
                QTimer.singleShot(100, self.morrenus_stats_widget.refresh_stats)

    def _create_dialog_buttons(self) -> None:
        """Create standard Ok/Cancel buttons."""
        buttons = create_standard_buttons(self.accept, self.reject)
        self.main_layout.addWidget(buttons)

    # ── Signal Handlers & Delegation Slots ────────────────────────────────
    @pyqtSlot(object)
    def _handle_gateway_test_done(self, data) -> None:
        tabs.handle_gateway_test_done(self, data)

    @pyqtSlot(tuple)
    def _handle_assfixer_check_done(self, result) -> None:
        tabs.handle_assfixer_check_done(self, result)

    @pyqtSlot(object)
    def _handle_assfixer_repair_done(self, result) -> None:
        tabs.handle_assfixer_repair_done(self, result)

    @pyqtSlot(dict)
    def _handle_sls_version_check_done(self, result: dict) -> None:
        tabs.handle_sls_version_check_done(self, result)

    @pyqtSlot()
    def _on_denuvo_sync_finished(self) -> None:
        tabs.on_denuvo_sync_finished(self)

    def _update_asshead_status_ui(self) -> None:
        tabs.update_asshead_status_ui(self)

    def _update_achievements_button_state(self) -> None:
        tabs.update_achievements_button_state(self)

    def _test_single_gateway(self, gateway_key: str, btn, label: str) -> None:
        tabs.test_single_gateway(self, gateway_key, btn, label)

    def _on_isp_gateway_changed(self, index: int) -> None:
        tabs.on_isp_gateway_changed(self, index)

    def _on_experimental_acf_toggled(self, state) -> None:
        tabs.on_experimental_acf_toggled(self, state)

    def _on_clear_update_cache_clicked(self) -> None:
        tabs.on_clear_update_cache_clicked(self)

    def goldberg_checked_warning(self) -> None:
        tabs.goldberg_checked_warning(self)

    def goldberg_checked_warning_from_mode(self, mode_type) -> None:
        tabs.goldberg_checked_warning_from_mode(self, mode_type)

    def goldberg_warning_box(self, checkbox, warning: str) -> bool:
        return tabs.goldberg_warning_box(self, checkbox, warning)

    def uninstall_assela(self) -> None:
        tabs.uninstall_assela(self)

    # ── Style / Appearance Delegations ────────────────────────────────────
    def choose_accent_color(self) -> None:
        tabs.choose_accent_color(self)

    def reset_accent_color(self) -> None:
        tabs.reset_accent_color(self)

    def choose_bg_color(self) -> None:
        tabs.choose_bg_color(self)

    def reset_bg_color(self) -> None:
        tabs.reset_bg_color(self)

    def on_preset_changed(self, index: int) -> None:
        tabs.on_preset_changed(self, index)

    def choose_font(self) -> None:
        tabs.choose_font(self)

    def reset_font(self) -> None:
        tabs.reset_font(self)

    def update_font_button_text(self) -> None:
        tabs.update_font_button_text(self)

    def on_titlebar_position_changed(self, state: int) -> None:
        tabs.on_titlebar_position_changed(self, state)

    def _on_origins_toggled(self, state: int) -> None:
        tabs.on_origins_toggled(self, state)

    def _fade_origins_opacity(self) -> None:
        tabs.fade_origins_opacity(self)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        tabs.paint_origins_overlay(self, event)

    # ── Tools Delegations ─────────────────────────────────────────────────
    def run_schema_grabber_manually(self) -> None:
        tabs.run_schema_grabber_manually(self)

    def run_steamless_manually(self) -> None:
        tabs.run_steamless_manually(self)

    def run_steamless_aio_manually(self) -> None:
        tabs.run_steamless_aio_manually(self)

    def run_asshead_fixer(self) -> None:
        tabs.run_asshead_fixer(self)

    def run_denuvo_sync(self) -> None:
        tabs.run_denuvo_sync(self)

    def open_sls_config(self) -> None:
        tabs.open_sls_config(self)

    def restore_sls_backup(self) -> None:
        tabs.restore_sls_backup(self)

    # ── Static Helper Methods for Backward Compatibility ──────────────────
    @staticmethod
    def _is_too_dark(color: QColor) -> bool:
        return tabs.is_too_dark(color)

    @staticmethod
    def _is_too_close(accent: QColor, bg: QColor, threshold: int = 100) -> bool:
        return tabs.is_too_close(accent, bg, threshold)

    @staticmethod
    def _show_color_warning() -> None:
        tabs.show_color_warning()

    @staticmethod
    def _add_tool_button(layout: QVBoxLayout, text: str, tooltip: str, slot):
        return tabs.add_tool_button(layout, text, tooltip, slot)

    @staticmethod
    def _launch_terminal_command(cmd: list, cwd: str, needs_env: bool = False) -> None:
        tabs.launch_terminal_command(cmd, cwd, needs_env)

    @staticmethod
    def _manage_registry(filename: str, success_msg: str) -> None:
        tabs.manage_registry(filename, success_msg)

    @staticmethod
    def register_registry_entries() -> None:
        tabs.register_registry_entries()

    @staticmethod
    def remove_registry_entries() -> None:
        tabs.remove_registry_entries()

    @staticmethod
    def _toggle_api_key_visibility(input_field, toggle_btn) -> None:
        tabs.toggle_api_key_visibility(input_field, toggle_btn)

    # ── Dialog Lifecycle & Persistence ────────────────────────────────────
    def accept(self) -> None:
        """Save all settings and close."""
        try:
            if hasattr(self, "service_poll_timer") and self.service_poll_timer:
                self.service_poll_timer.stop()
            self._save_general_settings()
            self._save_download_settings()
            if not self._save_style_settings():
                return
            self.settings.sync()
            logger.info("All settings saved.")
            super().accept()
        except Exception as e:
            import traceback
            logger.error(f"Error saving settings: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(
                self,
                "Error Saving Settings",
                f"An error occurred while saving settings:\n{e}\n\nSee log file for details."
            )

    def reject(self) -> None:
        """Revert settings on cancel."""
        self.settings.setValue("morrenus_api_key", self._original_morrenus_key)
        self.settings.setValue("titlebar_position", self._original_titlebar_position)
        if self.main_window and hasattr(self.main_window, "reposition_titlebar"):
            self.main_window.reposition_titlebar(self._original_titlebar_position)

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
            new_pass = self.steam_password_input.text()
            if new_pass != getattr(self, "_original_steam_password", None):
                from utils.helpers import encrypt_string
                encrypted_pass = encrypt_string(new_pass)
                self.settings.setValue("steam_password", encrypted_pass)
        if hasattr(self, "log_level_combo") and self.log_level_combo is not None:
            self.settings.setValue("log_filter_level", self.log_level_combo.currentText())
        if hasattr(self, "log_category_combo") and self.log_category_combo is not None:
            self.settings.setValue("log_filter_category", self.log_category_combo.currentText())

        try:
            from utils.logger import update_log_filters
            update_log_filters()
        except Exception:
            pass

    def _save_download_settings(self) -> None:
        if self.sls_mode_checkbox is not None:
            self.settings.setValue("slssteam_mode", self.sls_mode_checkbox.isChecked())
        if hasattr(self, "sls_config_management_checkbox") and self.sls_config_management_checkbox is not None:
            self.settings.setValue(
                "sls_config_management",
                self.sls_config_management_checkbox.isChecked(),
            )
        if hasattr(self, "dl_location_combo") and self.dl_location_combo is not None:
            self.settings.setValue(
                "default_download_directory", self.dl_location_combo.currentData() or ""
            )
        if hasattr(self, "library_mode_checkbox") and self.library_mode_checkbox is not None:
            self.settings.setValue("library_mode", self.library_mode_checkbox.isChecked())
        if hasattr(self, "auto_skip_single_choice_checkbox") and self.auto_skip_single_choice_checkbox is not None:
            self.settings.setValue(
                "auto_skip_single_choice",
                self.auto_skip_single_choice_checkbox.isChecked(),
            )
        if hasattr(self, "smart_depot_selection_checkbox") and self.smart_depot_selection_checkbox is not None:
            self.settings.setValue(
                "smart_depot_selection",
                self.smart_depot_selection_checkbox.isChecked(),
            )
        if self.use_lancache_checkbox is not None:
            self.settings.setValue(
                "use_lancache",
                self.use_lancache_checkbox.isChecked(),
            )
        if hasattr(self, "use_native_steam_dl_checkbox") and self.use_native_steam_dl_checkbox is not None:
            self.settings.setValue(
                "use_native_steam_download",
                self.use_native_steam_dl_checkbox.isChecked(),
            )
        if hasattr(self, "native_steam_action_combo") and self.native_steam_action_combo is not None:
            self.settings.setValue(
                "native_steam_default_action",
                self.native_steam_action_combo.currentData() or "ask",
            )
        if hasattr(self, "prompt_steam_restart_checkbox") and self.prompt_steam_restart_checkbox is not None:
            self.settings.setValue(
                "prompt_steam_restart",
                self.prompt_steam_restart_checkbox.isChecked(),
            )
        if self.ignore_slssteam_updater_checkbox is not None:
            self.settings.setValue(
                "ignore_slssteam_updater",
                self.ignore_slssteam_updater_checkbox.isChecked(),
            )
        if hasattr(self, "achievements_checkbox") and self.achievements_checkbox is not None:
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

        # Fake AppID DB toggle
        if hasattr(self, "fakeappid_db_integration_checkbox") and self.fakeappid_db_integration_checkbox is not None:
            old_val = self.settings.value("fakeappid_db_integration", False, type=bool)
            new_val = self.fakeappid_db_integration_checkbox.isChecked()
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

        # Remote Web UI toggle
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
                self.main_window.toggle_web_server(False)
                self.main_window.toggle_web_server(True, port=new_port)

        if hasattr(self, "update_interval_slider") and self.update_interval_slider:
            new_interval = self.update_interval_slider.value() * 5
            old_interval = getattr(self, "_original_update_interval", None)
            self.settings.setValue(
                "update_check_interval_minutes", new_interval
            )
            if old_interval is None or new_interval != old_interval:
                if self.main_window and hasattr(self.main_window, "apply_update_timer_settings"):
                    self.main_window.apply_update_timer_settings()

        if hasattr(self, "check_updates_on_boot_checkbox") and self.check_updates_on_boot_checkbox is not None:
            self.settings.setValue(
                "check_updates_on_boot",
                self.check_updates_on_boot_checkbox.isChecked()
            )

        if hasattr(self, "update_provider_combo") and self.update_provider_combo:
            self.settings.setValue(
                "update_check_api_provider",
                self.update_provider_combo.currentData() or "auto"
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
        if hasattr(self, "depot_toggles"):
            for tag, (btn, skey) in self.depot_toggles.items():
                try:
                    is_hidden = not btn.isChecked()
                    self.settings.setValue(skey, is_hidden)
                except Exception as e:
                    logger.warning(f"Error saving depot toggle {skey}: {e}")
        if hasattr(self, "hide_macos_depots_checkbox") and self.hide_macos_depots_checkbox is not None:
            try:
                self.settings.setValue("hide_macos_depots", self.hide_macos_depots_checkbox.isChecked())
            except RuntimeError:
                pass
        if hasattr(self, "hide_android_depots_checkbox") and self.hide_android_depots_checkbox is not None:
            try:
                self.settings.setValue("hide_android_depots", self.hide_android_depots_checkbox.isChecked())
            except RuntimeError:
                pass
        if hasattr(self, "show_hidden_depots_selector_checkbox") and self.show_hidden_depots_selector_checkbox is not None:
            try:
                self.settings.setValue("show_hidden_depots_in_selector", self.show_hidden_depots_selector_checkbox.isChecked())
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
            old_acf = getattr(self, "_original_experimental_acf", None)
            self.settings.setValue("experimental_acf_independent", is_enabled)
            if is_enabled and (old_acf is None or is_enabled != old_acf):
                try:
                    from utils.yaml_config_manager import ensure_slssteam_prerequisites
                    ensure_slssteam_prerequisites()
                except Exception:
                    pass

    def _save_style_settings(self) -> bool:
        return tabs.save_style_settings(self)
