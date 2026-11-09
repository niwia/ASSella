import logging
import os
import shutil
import subprocess
import sys
import threading

from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QSlider,
    QSizePolicy,
    QScrollArea,
)
from ui.widgets.modern_dropdown import ModernComboBox
from ui.dialogs.themed_message_box import ThemedMessageBox

from utils.helpers import create_checkbox_setting

logger = logging.getLogger(__name__)


def create_assela_tab(dialog) -> QWidget:
    """Create the ASSella settings tab."""
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

    # Section 1 Card: ASSella Settings
    assella_card, assella_lay = dialog._create_card_frame("ASSella Settings")
    if dialog.settings.value("smart_depot_selection") is None:
        dialog.settings.setValue("smart_depot_selection", True)

    # 1. Check Updates on Boot
    dialog.check_updates_on_boot_checkbox = create_checkbox_setting(
        "Check Updates on Boot",
        "check_updates_on_boot",
        True,
        dialog,
        "Automatically check for game updates in the background on startup.",
    )
    assella_lay.addWidget(dialog.check_updates_on_boot_checkbox)

    # 1b. Update Channel Selector
    channel_row = QHBoxLayout()
    channel_row.setContentsMargins(0, 2, 0, 2)
    channel_lbl = QLabel("Update Channel:")
    channel_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    channel_lbl.setToolTip("Select the ASSella release channel: Stable (official releases), Beta (preview channel), or Canary (experimental).")
    channel_row.addWidget(channel_lbl)
    channel_row.addStretch()

    dialog.update_channel_combo = ModernComboBox()
    dialog.update_channel_combo.addItem("Stable", "stable")
    dialog.update_channel_combo.addItem("Beta", "beta")
    dialog.update_channel_combo.addItem("Canary", "canary")
    dialog.update_channel_combo.setFixedWidth(120)

    saved_channel = dialog.settings.value("update_channel", "stable", type=str).lower()
    c_idx = dialog.update_channel_combo.findData(saved_channel)
    if c_idx >= 0:
        dialog.update_channel_combo.setCurrentIndex(c_idx)
    else:
        dialog.update_channel_combo.setCurrentIndex(0)

    def _on_channel_changed(idx):
        chosen = dialog.update_channel_combo.itemData(idx)
        dialog.settings.setValue("update_channel", chosen)
        dialog.settings.sync()
        if hasattr(dialog, "main_window") and dialog.main_window and hasattr(dialog.main_window, "check_tool_updates"):
            dialog.main_window.check_tool_updates()

    dialog.update_channel_combo.currentIndexChanged.connect(_on_channel_changed)
    channel_row.addWidget(dialog.update_channel_combo)
    assella_lay.addLayout(channel_row)

    if dialog.settings.value("use_experimental_ddm") is None:
        dialog.settings.setValue("use_experimental_ddm", True)


    # 6. Title Bar Search Widget
    dialog.title_bar_search_checkbox = create_checkbox_setting(
        "Search Widget in Title Bar",
        "title_bar_search_enabled",
        False,
        dialog,
        "Show a live search bar and quick-results dropdown directly in the top title bar instead of the search button.",
    )

    def _on_search_widget_toggled(state):
        is_checked = (state == Qt.CheckState.Checked.value or state is True)
        dialog.settings.setValue("title_bar_search_enabled", is_checked)
        if hasattr(dialog, "main_window") and dialog.main_window:
            titlebar = getattr(dialog.main_window, "bottom_titlebar", None)
            if titlebar and hasattr(titlebar, "refresh_search_mode"):
                titlebar.refresh_search_mode()

    dialog.title_bar_search_checkbox.stateChanged.connect(_on_search_widget_toggled)
    assella_lay.addWidget(dialog.title_bar_search_checkbox)

    # 4. Download Animation (Test/Halloween styles)
    anim_row = QHBoxLayout()
    anim_row.setContentsMargins(0, 2, 0, 2)
    anim_lbl = QLabel("Download Animation:")
    anim_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    anim_lbl.setToolTip("Choose the animation style rendered on the download progress bar (Standard, Candy Corn, Jack-o'-Lantern, or Fluttering Bat).")
    anim_row.addWidget(anim_lbl)
    anim_row.addStretch()

    dialog.download_animation_combo = ModernComboBox()
    dialog.download_animation_combo.addItem("Standard", "standard")
    dialog.download_animation_combo.addItem("Retro Ghost & Fog", "ghost")
    dialog.download_animation_combo.addItem("Pac-Man & Dots", "pacman")
    dialog.download_animation_combo.addItem("Flying Witch & Stardust", "witch")
    dialog.download_animation_combo.addItem("Goku (Kamehameha)", "goku")
    dialog.download_animation_combo.addItem("Sonic (Green Hill)", "sonic")
    dialog.download_animation_combo.addItem("Super Mario (Coins)", "mario")
    dialog.download_animation_combo.addItem("Shinobi Duel (Ninja Clash)", "shinobi")
    dialog.download_animation_combo.addItem("Nyan Cat (Rainbow)", "nyancat")
    dialog.download_animation_combo.addItem("Mega Man (Buster)", "megaman")
    dialog.download_animation_combo.addItem("Pokémon (Pokéball)", "pokemon")
    dialog.download_animation_combo.addItem("City Drive (Retro Car)", "car")
    dialog.download_animation_combo.addItem("Jack-o'-Lantern", "lantern")
    dialog.download_animation_combo.addItem("Santa & Snowy Village", "santa")
    dialog.download_animation_combo.setFixedWidth(220)

    saved_anim = dialog.settings.value("download_animation", "standard", type=str)
    idx_anim = dialog.download_animation_combo.findData(saved_anim)
    if idx_anim >= 0:
        dialog.download_animation_combo.setCurrentIndex(idx_anim)
    else:
        dialog.download_animation_combo.setCurrentIndex(0)

    anim_row.addWidget(dialog.download_animation_combo)
    assella_lay.addLayout(anim_row)

    # 4. Update Check Interval Slider
    slider_layout = QHBoxLayout()
    slider_layout.setContentsMargins(0, 6, 0, 6)
    slider_label = QLabel("Update Check Interval:")
    slider_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    slider_label.setToolTip("Set how often to check for game updates. Move to the leftmost position (0) to disable.")

    dialog.update_interval_slider = QSlider(Qt.Orientation.Horizontal)
    dialog.update_interval_slider.setRange(0, 20)
    dialog.update_interval_slider.setMinimumHeight(24)

    dialog.update_interval_slider.setStyleSheet("""
        QSlider {
            min-height: 24px;
        }
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

    dialog.update_interval_value_label = QLabel("Disabled")
    dialog.update_interval_value_label.setStyleSheet("color: rgba(255, 255, 255, 0.8); font-size: 9pt; font-weight: bold; border: none; background: transparent;")
    dialog.update_interval_value_label.setFixedWidth(75)

    current_minutes = dialog.settings.value("update_check_interval_minutes", 0, type=int)
    slider_val = min(20, max(0, current_minutes // 5))
    dialog.update_interval_slider.setValue(slider_val)

    def update_slider_label(val):
        if val == 0:
            dialog.update_interval_value_label.setText("Disabled")
        else:
            dialog.update_interval_value_label.setText(f"{val * 5} mins")

    update_slider_label(slider_val)
    dialog.update_interval_slider.valueChanged.connect(update_slider_label)

    slider_layout.addWidget(slider_label)
    slider_layout.addWidget(dialog.update_interval_slider, 1)
    slider_layout.addWidget(dialog.update_interval_value_label)
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

    dialog.update_provider_combo = ModernComboBox()
    dialog.update_provider_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    dialog.update_provider_combo.addItem("Auto (Hybrid)", "auto")
    dialog.update_provider_combo.addItem("SteamPICS", "steampics")
    dialog.update_provider_combo.addItem("SteamcmdAPI", "steamcmd")
    dialog.update_provider_combo.setToolTip(
        "Auto (Hybrid): Queries SteamCMD and live Steam PICS in tandem with highest-build resolution for maximum speed and zero stale data.\n"
        "SteamPICS: Live connection directly to Valve CM servers.\n"
        "SteamcmdAPI: Stateless HTTP requests via CDN."
    )

    current_provider = dialog.settings.value("update_check_api_provider", "auto", type=str)
    p_idx = dialog.update_provider_combo.findData(current_provider)
    if p_idx >= 0:
        dialog.update_provider_combo.setCurrentIndex(p_idx)

    provider_layout.addWidget(provider_label)
    provider_layout.addStretch(1)
    provider_layout.addWidget(dialog.update_provider_combo)
    assella_lay.addLayout(provider_layout)

    # SLSah manual cheevos (Beta)
    dialog.slsah_cheevos_checkbox = create_checkbox_setting(
        "SLSah manual cheevos (Beta)",
        "enable_slsah_cheevos",
        False,
        dialog,
        "Enables viewing and manually managing achievements in the Game Details dialog.\n"
        "When disabled, the achievements tab is hidden and background cache checks are skipped.",
        show_description=False,
    )
    assella_lay.addWidget(dialog.slsah_cheevos_checkbox)

    layout.addWidget(assella_card)
    layout.addStretch()

    scroll.setWidget(container)
    outer_layout.addWidget(scroll)

    dialog.tab_widget.addTab(tab, "ASSella")
    return tab


from ui.dialogs.settings_tabs.morrenus_tab import (
    on_isp_gateway_changed,
    test_single_gateway,
    handle_gateway_test_done,
)


def uninstall_assela(dialog) -> None:
    """Remove ASSella and optionally restore the original ACCELA backup."""
    install_dir = os.path.expanduser("~/.local/share/ACCELA")
    assela_path = os.path.join(install_dir, "ASSella.AppImage")
    symlink_path = os.path.join(install_dir, "ACCELA.AppImage")
    backup_path = os.path.join(install_dir, "ACCELA.AppImage.bak")
    desktop_entry = os.path.expanduser("~/.local/share/applications/accela.desktop")
    has_backup = os.path.isfile(backup_path)

    reply = ThemedMessageBox.question(
        dialog,
        "Uninstall ASSella",
        "This will remove ASSella and revert the desktop shortcut to ACCELA.\n\nAre you sure?",
        ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
    )
    if reply != ThemedMessageBox.StandardButton.Yes:
        return

    restore = False
    if has_backup:
        restore_reply = ThemedMessageBox.question(
            dialog,
            "Restore Original ACCELA?",
            "A backup of the original ACCELA (ACCELA.AppImage.bak) was found.\n\n"
            "Would you like to restore it after uninstalling ASSella?",
            ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
        )
        restore = restore_reply == ThemedMessageBox.StandardButton.Yes

    errors = []

    if os.path.islink(symlink_path):
        try:
            os.remove(symlink_path)
        except OSError as e:
            errors.append(f"Could not remove symlink: {e}")

    if os.path.isfile(assela_path):
        try:
            os.remove(assela_path)
        except OSError as e:
            errors.append(f"Could not remove ASSella.AppImage: {e}")

    cache_dir = os.path.join(install_dir, "image_cache")
    if os.path.exists(cache_dir):
        try:
            shutil.rmtree(cache_dir)
        except OSError as e:
            errors.append(f"Could not remove image cache directory: {e}")

    if restore:
        try:
            shutil.copy2(backup_path, symlink_path)
            os.chmod(symlink_path, 0o755)
        except OSError as e:
            errors.append(f"Could not restore ACCELA backup: {e}")

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
        ThemedMessageBox.warning(dialog, "Uninstall — Partial", "\n".join(errors))
    else:
        msg = "ASSella has been uninstalled."
        if restore:
            msg += "\nOriginal ACCELA has been restored."
        ThemedMessageBox.information(dialog, "Done", msg)
