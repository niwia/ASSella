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
    QComboBox,
)

from utils.helpers import create_checkbox_setting
from utils.yaml_config_manager import is_slssteam_mode_enabled

logger = logging.getLogger(__name__)


def create_advanced_tab(dialog) -> QWidget:
    """Create the Advanced settings tab with specialized settings."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)

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

    dialog.probe_cdn_checkbox = create_checkbox_setting(
        "Probe CDN edge servers for fastest route",
        "probe_cdn",
        False,
        dialog,
        "Pings Steam CDN edge servers before downloading to select the lowest-latency route.",
        show_description=False,
    )
    dialog.probe_cdn_checkbox.checkbox.toggled.connect(
        lambda val: (dialog.settings.setValue("probe_cdn", val), dialog.settings.sync())
    )
    adv_layout.addWidget(dialog.probe_cdn_checkbox)

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
    dialog.workshop_cell_id_input.setFixedWidth(160)

    ws_cell_layout = QHBoxLayout()
    ws_cell_layout.addWidget(dialog.workshop_cell_id_input)
    ws_cell_layout.addStretch(1)

    ws_grid.addWidget(ws_cell_id_label, 1, 0)
    ws_grid.addLayout(ws_cell_layout, 1, 1)

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

    # 1. prompt_steam_restart is always disabled/off by default
    dialog.settings.setValue("prompt_steam_restart", False)
    if hasattr(dialog, "prompt_steam_restart_checkbox") and dialog.prompt_steam_restart_checkbox is not None:
        dialog.prompt_steam_restart_checkbox.setChecked(False)
        dialog.prompt_steam_restart_checkbox.setLocked(True, "Disabled while 'SLSsteam API' is active.")

    # 2. sls_config_management is linked in lockstep with SLSsteam API
    dialog.settings.setValue("sls_config_management", is_checked)
    if hasattr(dialog, "sls_config_management_checkbox") and dialog.sls_config_management_checkbox is not None:
        dialog.sls_config_management_checkbox.setChecked(is_checked)

    # 3. library_mode_checkbox (Limit Downloads to Steam Libraries)
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


    # 4. Silently ensure SLS prerequisites (API: yes, LogLevels: 0x2) in config.yaml if checked
    if is_checked:
        try:
            from utils.yaml_config_manager import get_user_config_path, ensure_slssteam_prerequisites
            config_path = get_user_config_path()
            if config_path.exists():
                ensure_slssteam_prerequisites(config_path)
        except Exception as e:
            logger.warning(f"Failed to ensure SLS prerequisites in config.yaml: {e}")
