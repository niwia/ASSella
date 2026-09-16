import logging
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QMessageBox,
)

from utils.helpers import create_checkbox_setting
from utils.yaml_config_manager import is_slssteam_mode_enabled

logger = logging.getLogger(__name__)


def create_advanced_tab(dialog) -> QWidget:
    """Create the Advanced settings tab with specialized settings."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(24)

    # Advanced Downloads Card
    adv_card, adv_layout = dialog._create_card_frame("Advanced Download Settings")

    dialog.auto_skip_single_choice_checkbox = create_checkbox_setting(
        "Skip single-choice selection",
        "auto_skip_single_choice",
        False,
        dialog,
        "Automatically skip selection when only one option exists.",
        show_description=False,
    )
    adv_layout.addWidget(dialog.auto_skip_single_choice_checkbox)

    # Clear Update & Build ID Cache Row
    cache_layout = QHBoxLayout()
    cache_layout.setContentsMargins(0, 6, 0, 2)
    cache_desc = QLabel("Update & Build ID Cache:")
    cache_desc.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    cache_desc.setToolTip("Clears local caches so game build IDs, branches, and update statuses are queried fresh from Steam.")
    dialog.clear_update_cache_btn = QPushButton("Clear Cache")
    dialog.clear_update_cache_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.clear_update_cache_btn.setToolTip("Purges cached build IDs, branch manifests, and update status entries.")
    dialog.clear_update_cache_btn.setStyleSheet("""
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
    dialog.clear_update_cache_btn.clicked.connect(lambda: on_clear_update_cache_clicked(dialog))
    cache_layout.addWidget(cache_desc)
    cache_layout.addStretch(1)
    cache_layout.addWidget(dialog.clear_update_cache_btn)
    adv_layout.addLayout(cache_layout)

    layout.addWidget(adv_card)

    # Workshop Downloader Settings Card
    ws_card, ws_layout = dialog._create_card_frame("Advanced Workshop Settings")

    dialog.workshop_steam_checkbox = create_checkbox_setting(
        "Enable Steam Integration for Workshop Downloads",
        "workshop_steam_enabled",
        True,
        dialog,
        "Directs workshop downloads to your detected Steam library directories.",
        show_description=False,
    )
    ws_layout.addWidget(dialog.workshop_steam_checkbox)

    ws_grid = QGridLayout()
    ws_grid.setContentsMargins(4, 4, 4, 4)
    ws_grid.setSpacing(12)
    ws_grid.setColumnStretch(0, 0)
    ws_grid.setColumnStretch(1, 1)

    ws_max_dl_label = QLabel("Max Concurrent Workshop Downloads:")
    ws_max_dl_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")

    dialog.workshop_max_dl_slider = QSlider(Qt.Orientation.Horizontal)
    dialog.workshop_max_dl_slider.setRange(1, 30)
    dialog.workshop_max_dl_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
    dialog.workshop_max_dl_slider.setTickInterval(1)
    dialog.workshop_max_dl_slider.setStyleSheet("""
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
    """ % (dialog.accent_color, dialog.accent_color))

    current_ws_max = dialog.settings.value("workshop_max_downloads", 8, type=int)
    if current_ws_max < 1 or current_ws_max > 30:
        current_ws_max = 8
    dialog.workshop_max_dl_slider.setValue(current_ws_max)

    dialog.workshop_max_dl_val_lbl = QLabel(str(current_ws_max))
    dialog.workshop_max_dl_val_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.8); font-size: 9pt; font-weight: bold; border: none; background: transparent;")
    dialog.workshop_max_dl_val_lbl.setFixedWidth(30)
    dialog.workshop_max_dl_slider.valueChanged.connect(lambda val: dialog.workshop_max_dl_val_lbl.setText(str(val)))

    ws_slider_layout = QHBoxLayout()
    ws_slider_layout.addWidget(dialog.workshop_max_dl_slider, 1)
    ws_slider_layout.addWidget(dialog.workshop_max_dl_val_lbl)

    ws_grid.addWidget(ws_max_dl_label, 0, 0)
    ws_grid.addLayout(ws_slider_layout, 0, 1)

    ws_cell_id_label = QLabel("Cell ID:")
    ws_cell_id_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    dialog.workshop_cell_id_input = QLineEdit()
    dialog.workshop_cell_id_input.setPlaceholderText("Optional")
    dialog.workshop_cell_id_input.setText(dialog.settings.value("workshop_cell_id", "", type=str))

    ws_grid.addWidget(ws_cell_id_label, 1, 0)
    ws_grid.addWidget(dialog.workshop_cell_id_input, 1, 1)

    ws_layout.addLayout(ws_grid)
    layout.addWidget(ws_card)

    # Post-Processing Card
    pp_card, pp_layout = dialog._create_card_frame("Advanced Post-Processing")
    dialog.achievements_checkbox = create_checkbox_setting(
        "Generate Achievements (Recommended Off)",
        "generate_achievements",
        False,
        dialog,
        "Automatically generate achievement configuration files during post-processing.",
        show_description=False,
    )
    pp_layout.addWidget(dialog.achievements_checkbox)

    dialog.auto_apply_goldberg_checkbox = create_checkbox_setting(
        "Auto-apply Goldberg on Install (Experimental)",
        "auto_apply_goldberg",
        False,
        dialog,
        "Automatically apply Goldberg Steam Emulator after game download finishes.",
        show_description=False,
    )
    pp_layout.addWidget(dialog.auto_apply_goldberg_checkbox)

    layout.addWidget(pp_card)
    layout.addStretch()

    dialog.tab_widget.addTab(tab, "Advanced")
    return tab


def on_clear_update_cache_clicked(dialog):
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

    for key in list(dialog.settings.allKeys()):
        if key.startswith("last_checked_") or key.startswith("installed_buildid/"):
            dialog.settings.remove(key)
    dialog.settings.sync()

    dialog.clear_update_cache_btn.setText("Cleared!")
    dialog.clear_update_cache_btn.setEnabled(False)
    QTimer.singleShot(2500, lambda: (
        dialog.clear_update_cache_btn.setText("Clear Cache"),
        dialog.clear_update_cache_btn.setEnabled(True)
    ))
    QMessageBox.information(
        dialog,
        "Cache Cleared",
        "Update status, build ID, and branch caches have been cleared successfully.\n\n"
        "Fresh live data will be queried next time you check for updates or open game details."
    )


def goldberg_checked_warning(dialog) -> None:
    """Warn when Goldberg is enabled alongside Steam integration."""
    checkbox = dialog.auto_apply_goldberg_checkbox
    if not checkbox.isChecked():
        return

    integration_enabled = (
        dialog.sls_mode_checkbox.isChecked()
        if dialog.sls_mode_checkbox is not None
        else is_slssteam_mode_enabled()
    )
    if not integration_enabled:
        return

    warning = "You are about to enable Goldberg integration which is meant to be able to play your downloaded games WITHOUT Steam. If you are going to use Steam to play your games keep this disabled, otherwise things will break. You have been warned. Continue?"
    if goldberg_warning_box(dialog, checkbox, warning):
        return


def goldberg_checked_warning_from_mode(dialog, type) -> None:
    """Warn when Steam integration is enabled while Goldberg is active."""
    checkbox = dialog.sls_mode_checkbox
    if not checkbox.isChecked():
        return
    try:
        if not dialog.auto_apply_goldberg_checkbox.isChecked():
            return
    except AttributeError:
        if not dialog.settings.value("auto_apply_goldberg", False):
            return

    warning = f"You are about to enable {type} integration which is meant to be able to play your downloaded games WITH Steam. But you have Goldberg enabled, which is meant to be able to play your games WITHOUT Steam, if you are going to use Steam to play your games disable Goldberg in settings."
    if goldberg_warning_box(dialog, checkbox, warning):
        return


def goldberg_warning_box(dialog, checkbox, warning) -> bool:
    msg_box = QMessageBox(dialog)
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

    confirm_box = QMessageBox(dialog)
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


def on_experimental_acf_toggled(dialog, state):
    is_checked = (state == Qt.CheckState.Checked.value or state == True or state == 2)

    # 1. prompt_steam_restart_checkbox
    if hasattr(dialog, "prompt_steam_restart_checkbox") and dialog.prompt_steam_restart_checkbox is not None:
        if is_checked:
            if not hasattr(dialog, "_saved_prompt_restart_pref"):
                dialog._saved_prompt_restart_pref = dialog.prompt_steam_restart_checkbox.isChecked()
            dialog.prompt_steam_restart_checkbox.setChecked(False)
            dialog.prompt_steam_restart_checkbox.setLocked(True, "Disabled while 'SLSsteam API' is active.")
        else:
            dialog.prompt_steam_restart_checkbox.setLocked(False)
            if hasattr(dialog, "_saved_prompt_restart_pref"):
                dialog.prompt_steam_restart_checkbox.setChecked(dialog._saved_prompt_restart_pref)

    # 2. library_mode_checkbox (Limit Downloads to Steam Libraries)
    if hasattr(dialog, "library_mode_checkbox") and dialog.library_mode_checkbox is not None:
        if is_checked:
            if not hasattr(dialog, "_saved_library_mode_pref"):
                dialog._saved_library_mode_pref = dialog.library_mode_checkbox.isChecked()
            dialog.library_mode_checkbox.setChecked(True)
            dialog.library_mode_checkbox.setLocked(True, "Must be enabled when 'SLSsteam API' is active.")
        else:
            dialog.library_mode_checkbox.setLocked(False)
            if hasattr(dialog, "_saved_library_mode_pref"):
                dialog.library_mode_checkbox.setChecked(dialog._saved_library_mode_pref)

    # 3. sls_config_management_checkbox (SLS Config Management)
    if hasattr(dialog, "sls_config_management_checkbox") and dialog.sls_config_management_checkbox is not None:
        if is_checked:
            if not hasattr(dialog, "_saved_sls_config_mgmt_pref"):
                dialog._saved_sls_config_mgmt_pref = dialog.sls_config_management_checkbox.isChecked()
            dialog.sls_config_management_checkbox.setChecked(True)
            dialog.sls_config_management_checkbox.setLocked(True, "Must be enabled when 'SLSsteam API' is active.")
        else:
            is_sls_detected = False
            try:
                from ui.dialogs.settings_sls import get_sls_paths
                is_sls_detected = get_sls_paths()["detected"]
            except Exception:
                pass
            if sys.platform == "linux" and is_sls_detected:
                dialog.sls_config_management_checkbox.setChecked(True)
                dialog.sls_config_management_checkbox.setLocked(True, "Enabled because SLSsteam installation was detected.")
            else:
                dialog.sls_config_management_checkbox.setLocked(False)
                if hasattr(dialog, "_saved_sls_config_mgmt_pref"):
                    dialog.sls_config_management_checkbox.setChecked(dialog._saved_sls_config_mgmt_pref)

    # 4. Silently ensure SLS prerequisites (API: yes, LogLevels: 0x2) in config.yaml if checked
    if is_checked:
        try:
            from utils.yaml_config_manager import get_user_config_path, ensure_slssteam_prerequisites
            config_path = get_user_config_path()
            if config_path.exists():
                ensure_slssteam_prerequisites(config_path)
        except Exception as e:
            logger.warning(f"Failed to ensure SLS prerequisites in config.yaml: {e}")
