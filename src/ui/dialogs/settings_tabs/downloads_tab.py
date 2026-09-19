import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFrame,
    QLabel,
    QComboBox,
    QPushButton,
    QSlider,
    QTabWidget,
    QFileDialog,
    QMessageBox,
    QDialog,
)

from ui.dialogs.settings_tabs.windows_depot_warning import WindowsDepotWarningDialog
from utils.helpers import create_checkbox_setting
from utils.paths import is_valid_download_directory


def create_downloads_tab(dialog) -> QWidget:
    """Create the Downloads settings tab with General and Depot selection sub-tabs."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(16)

    # Create sub-tabs inside Downloads
    dl_subtabs = QTabWidget()
    dl_subtabs.setUsesScrollButtons(True)
    bg_color = dialog.settings.value("background_color", "#141416")
    dl_subtabs.setStyleSheet(f"""
        QTabWidget::pane {{
            border: none;
        }}
        QTabBar::tab {{
            background: {bg_color};
            color: rgba(255, 255, 255, 0.6);
            padding: 6px 14px;
            border: none;
            font-weight: bold;
            font-size: 9pt;
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
    general_widget = QWidget()
    gen_layout = QVBoxLayout(general_widget)
    gen_layout.setContentsMargins(0, 8, 0, 0)
    gen_layout.setSpacing(16)

    # Download Settings Card
    dl_card, dl_layout = dialog._create_card_frame("Download Settings")

    library_tooltip = "Detect Steam libraries and let you choose where to install games."
    if sys.platform == "linux":
        library_tooltip += " On Linux, this also enables SLSsteam integration for those installs."

    dialog.library_mode_checkbox = create_checkbox_setting(
        "Limit Downloads to Steam Libraries",
        "library_mode",
        False,
        dialog,
        library_tooltip,
    )
    dl_layout.addWidget(dialog.library_mode_checkbox)

    # LanCache Detection Toggle
    dialog.use_lancache_checkbox = create_checkbox_setting(
        "Enable Lan Cache",
        "use_lancache",
        True,
        dialog,
        "Use network connected steam game files first",
    )
    dl_layout.addWidget(dialog.use_lancache_checkbox)

    dl_layout.addSpacing(8)

    # Inputs Grid for Download Settings (Download Location, Max Downloads)
    loc_row = QHBoxLayout()
    loc_row.setContentsMargins(2, 2, 2, 2)
    loc_row.setSpacing(10)

    dl_dir_label = QLabel("Default Download Location:")
    dl_dir_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    dl_dir_label.setToolTip("Direct downloads to this folder/library instead of prompting for every game. Defaults to Ask Every Time.")
    loc_row.addWidget(dl_dir_label, 1)

    dialog.dl_location_combo = QComboBox()
    dialog.dl_location_combo.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.dl_location_combo.addItem("Ask Every Time", "")

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
        dialog.dl_location_combo.addItem(_fmt_path(lib), lib)

    dialog.dl_location_combo.addItem("Custom Folder...", "custom")

    # Load saved value (default is "Ask Every Time" / "")
    current_val = dialog.settings.value("default_download_directory", "")
    if not current_val or not is_valid_download_directory(current_val):
        if current_val:
            dialog.settings.setValue("default_download_directory", "")
        dialog.dl_location_combo.setCurrentIndex(0)
    elif current_val in detected_libs:
        idx = dialog.dl_location_combo.findData(current_val)
        if idx >= 0:
            dialog.dl_location_combo.setCurrentIndex(idx)
    else:
        dialog.dl_location_combo.insertItem(1, _fmt_path(current_val), current_val)
        dialog.dl_location_combo.setCurrentIndex(1)

    def on_dl_location_changed(index):
        data = dialog.dl_location_combo.itemData(index)
        if data == "custom":
            path = QFileDialog.getExistingDirectory(dialog, "Select Custom Download Location")
            if path and is_valid_download_directory(path):
                existing_idx = dialog.dl_location_combo.findData(path)
                if existing_idx >= 0:
                    dialog.dl_location_combo.setCurrentIndex(existing_idx)
                else:
                    insert_pos = dialog.dl_location_combo.count() - 1
                    dialog.dl_location_combo.insertItem(insert_pos, _fmt_path(path), path)
                    dialog.dl_location_combo.setCurrentIndex(insert_pos)
            elif path:
                QMessageBox.warning(
                    dialog,
                    "Invalid Location",
                    "System temporary directories (/tmp, AppImage mounts, etc.) cannot be used as a download location.",
                )
                dialog.dl_location_combo.setCurrentIndex(0)
            else:
                dialog.dl_location_combo.setCurrentIndex(0)

    dialog.dl_location_combo.currentIndexChanged.connect(on_dl_location_changed)
    loc_row.addWidget(dialog.dl_location_combo)
    dl_layout.addLayout(loc_row)

    slider_layout = QHBoxLayout()
    slider_layout.setContentsMargins(2, 2, 2, 2)
    slider_layout.setSpacing(10)

    max_dl_label = QLabel("Concurrent Downloads:")
    max_dl_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    max_dl_label.setToolTip("Set maximum concurrent downloads (1-30). Lower values (e.g. 1-2) reduce network usage.")
    slider_layout.addWidget(max_dl_label)

    dialog.max_downloads_slider = QSlider(Qt.Orientation.Horizontal)
    dialog.max_downloads_slider.setRange(1, 30)
    dialog.max_downloads_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
    dialog.max_downloads_slider.setTickInterval(1)
    dialog.max_downloads_slider.setStyleSheet("""
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

    current_max = dialog.settings.value("max_downloads", 8, type=int)
    dialog.max_downloads_slider.setValue(current_max)

    dialog.max_downloads_val_lbl = QLabel(str(current_max))
    dialog.max_downloads_val_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.8); font-size: 9pt; font-weight: bold; border: none; background: transparent;")
    dialog.max_downloads_val_lbl.setFixedWidth(30)
    dialog.max_downloads_slider.valueChanged.connect(lambda val: dialog.max_downloads_val_lbl.setText(str(val)))

    slider_layout.addWidget(dialog.max_downloads_slider, 1)
    slider_layout.addWidget(dialog.max_downloads_val_lbl)

    dl_layout.addLayout(slider_layout)
    gen_layout.addWidget(dl_card)
    gen_layout.addStretch()
    dl_subtabs.addTab(general_widget, "General")

    # ── Subtab 2: Depot selection ──
    hide_widget = QWidget()
    hide_layout = QVBoxLayout(hide_widget)
    hide_layout.setContentsMargins(0, 8, 0, 0)
    hide_layout.setSpacing(16)

    hide_card, hide_card_lay = dialog._create_card_frame("Depot Visibility Filter")

    desc_lbl = QLabel(
        "Highlighed buttons are Shown while unhighlited ones are hidden."
    )
    desc_lbl.setWordWrap(True)
    desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9pt; border: none; background: transparent;")
    hide_card_lay.addWidget(desc_lbl)
    hide_card_lay.addSpacing(6)

    grid = QGridLayout()
    grid.setSpacing(10)
    grid.setContentsMargins(4, 4, 4, 4)

    from utils.color_utils import get_best_foreground_color
    text_on_accent = get_best_foreground_color(dialog.accent_color, dark_color="#121214", light_color="#FFFFFF")

    def _apply_depot_btn_style(btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {dialog.accent_color};
                    color: {text_on_accent};
                    border: 1px solid {dialog.accent_color};
                    border-radius: 8px;
                    font-weight: bold;
                    font-size: 9pt;
                    padding: 10px 4px;
                }}
                QPushButton:hover {{
                    border-color: #FFFFFF;
                }}
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.05);
                    color: rgba(255, 255, 255, 0.65);
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 8px;
                    font-weight: 500;
                    font-size: 9pt;
                    padding: 10px 4px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 0.09);
                    border-color: rgba(255, 255, 255, 0.22);
                    color: #FFFFFF;
                }
            """)

    depot_defs = [
        ("macos", "macOS", "hide_macos_depots", True, "Show or hide macOS platform depots"),
        ("android", "Android", "hide_android_depots", True, "Show or hide Android platform depots"),
        ("soundtracks", "Soundtracks / OST", "filter_soundtracks", True, "Show or hide soundtrack & OST depots"),
        ("windows", "Windows", "hide_windows_depots", False, "Show or hide Windows platform depots"),
        ("linux", "Linux", "hide_linux_depots", False, "Show or hide Linux platform depots"),
        ("artbooks", "Artbooks / Extras", "hide_artbooks_depots", True, "Show or hide artbook, wallpaper, and extra content depots"),
        ("demos", "Demos / Trials", "hide_demos_depots", True, "Show or hide demo and trial depots"),
        ("tools", "Tools / SDKs", "hide_tools_depots", True, "Show or hide developer tools, dedicated servers, and SDK depots"),
        ("search_blacklist", "Search Blacklist", "filter_search_blacklist", False, "Filter blacklisted keywords from manifest search"),
    ]

    platform_tags = ("windows", "linux", "macos", "android")
    dialog.depot_toggles = {}

    def make_toggle_handler(t, b):
        def handler(checked):
            if not checked and t in platform_tags:
                other_active = any(
                    dialog.depot_toggles[pt][0].isChecked()
                    for pt in platform_tags
                    if pt != t and pt in dialog.depot_toggles
                )
                if not other_active:
                    QMessageBox.warning(
                        dialog,
                        "Platform Required",
                        "At least one platform depot type (Windows, Linux, macOS, or Android) must remain enabled so game files can be detected."
                    )
                    b.blockSignals(True)
                    b.setChecked(True)
                    b.blockSignals(False)
                    _apply_depot_btn_style(b, True)
                    return

                if t == "windows":
                    dlg = WindowsDepotWarningDialog(dialog, accent_color=dialog.accent_color)
                    if dlg.exec() != QDialog.DialogCode.Accepted:
                        b.blockSignals(True)
                        b.setChecked(True)
                        b.blockSignals(False)
                        _apply_depot_btn_style(b, True)
                        return

            _apply_depot_btn_style(b, checked)
        return handler

    for idx, (tag, label_text, skey, def_hidden, tip) in enumerate(depot_defs):
        r = idx // 3
        c = idx % 3

        is_hidden = dialog.settings.value(skey, def_hidden, type=bool)
        is_shown = not is_hidden

        btn = QPushButton(label_text)
        btn.setCheckable(True)
        btn.setChecked(is_shown)
        btn.setFixedHeight(44)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tip)
        _apply_depot_btn_style(btn, is_shown)

        btn.toggled.connect(make_toggle_handler(tag, btn))
        grid.addWidget(btn, r, c)
        dialog.depot_toggles[tag] = (btn, skey)

    if not any(dialog.depot_toggles[pt][0].isChecked() for pt in platform_tags if pt in dialog.depot_toggles):
        if "windows" in dialog.depot_toggles:
            win_btn = dialog.depot_toggles["windows"][0]
            win_btn.setChecked(True)
            _apply_depot_btn_style(win_btn, True)

    hide_card_lay.addLayout(grid)
    hide_layout.addWidget(hide_card)

    # ── Card 2: Depot Selection Dialog Display ──
    sel_card, sel_card_lay = dialog._create_card_frame("Depot Selection Dialog Display")
    dialog.show_hidden_depots_selector_checkbox = create_checkbox_setting(
        "Show Hidden Depots section by default",
        "show_hidden_depots_in_selector",
        False,
        dialog,
        "When enabled, the '▾ Hidden Depots' dropdown expander and filtered depots are visible by default in the depot selection window.",
    )
    sel_card_lay.addWidget(dialog.show_hidden_depots_selector_checkbox)
    hide_layout.addWidget(sel_card)

    hide_layout.addStretch()
    dl_subtabs.addTab(hide_widget, "Depot selection")

    layout.addWidget(dl_subtabs)
    dialog.tab_widget.addTab(tab, "Downloads")
    return tab
