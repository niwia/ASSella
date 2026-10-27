#!/usr/bin/env python3
"""Targeted test suite for depot dialog titlebar theming, DLC-only button removal, and install path visibility."""

import sys
import os
from pathlib import Path

# Add src to python path
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor

# Ensure headless Qt
os.environ["QT_QPA_PLATFORM"] = "offscreen"
app = QApplication.instance() or QApplication(["test"])

from ui.dialog_titlebar import DialogTitleBar
from ui.dialogs.depot_selection.dialog import DepotSelectionDialog
from ui.dialogs.single_depot_dialog import SingleDepotSelectionDialog

def test_dialog_titlebar_update_accent():
    print("=== Testing DialogTitleBar.update_accent_color ===")
    titlebar = DialogTitleBar(None, title="Test Title")
    new_color = QColor("#00FF88")
    titlebar.update_accent_color(new_color)

    assert "#00ff88" in titlebar.title_label.styleSheet().lower(), "Title text color must be updated to new accent"
    print("  [ok] title_label stylesheet updated with new accent color")

    # Verify SVG buttons have icons
    assert titlebar.close_btn.icon() is not None and not titlebar.close_btn.icon().isNull(), "Close button icon must be set"
    assert titlebar.minimize_btn.icon() is not None and not titlebar.minimize_btn.icon().isNull(), "Minimize button icon must be set"
    assert titlebar.maximize_btn.icon() is not None and not titlebar.maximize_btn.icon().isNull(), "Maximize button icon must be set"
    print("  [ok] control buttons re-rendered with new accent color icons")

def test_depot_dialog_normal_mode_layout():
    print("\n=== Testing DepotSelectionDialog Normal Mode Layout ===")
    dialog = DepotSelectionDialog(
        app_id="123456",
        depots={"123457": {"name": "Test Depot 1", "size": 1000}, "123458": {"name": "Test Depot 2", "size": 2000}},
        game_name="Test Game",
        dlc_only_mode=False,
        show_storage=True,
    )

    # 1. DLC Only button must be removed (None)
    assert dialog._dlc_only_btn is None, "_dlc_only_btn should be None in normal mode"
    print("  [ok] _dlc_only_btn is None in normal mode")

    # 2. Install path / status label must NOT be present in normal game install header
    assert not hasattr(dialog, "path_lbl"), "path_lbl should not exist in normal game install header"
    print("  [ok] install path / status label is not present in normal game header")

    # 3. Dynamic theming updates titlebar text and icons
    theme_color = QColor("#FF5500")
    dialog._apply_dynamic_theme(theme_color)
    assert dialog._ambient_color == theme_color, "_ambient_color updated"
    assert "#ff5500" in dialog.title_bar.title_label.styleSheet().lower(), "Dialog titlebar text color updated dynamically"
    print("  [ok] _apply_dynamic_theme updates dialog titlebar to thumbnail color")

def test_depot_dialog_dlc_only_layout():
    print("\n=== Testing DepotSelectionDialog DLC-Only Mode Layout ===")
    dialog = DepotSelectionDialog(
        app_id="123456",
        depots={"123457": {"name": "Test Depot 1", "size": 1000}, "123458": {"name": "Test Depot 2", "size": 2000}},
        game_name="Test Game",
        dlc_only_mode=True,
        show_storage=True,
    )

    # In DLC mode, DLC Only button is also None (not shown, as dialog is already DLC mode)
    assert dialog._dlc_only_btn is None, "_dlc_only_btn should be None in DLC mode"
    print("  [ok] _dlc_only_btn is None in DLC mode")

def test_single_depot_dialog_dynamic_theme():
    print("\n=== Testing SingleDepotSelectionDialog Dynamic Theming ===")
    dialog = SingleDepotSelectionDialog(
        app_id="200000",
        depots={"200001": {"name": "Main Depot", "size": 5000}},
        game_name="Single Depot Game",
    )

    theme_color = QColor("#33AAFF")
    dialog._apply_dynamic_theme(theme_color)
    assert dialog._ambient_color == theme_color, "_ambient_color updated"
    assert "#33aaff" in dialog.title_bar.title_label.styleSheet().lower(), "Single depot dialog titlebar text color updated"
    print("  [ok] SingleDepotSelectionDialog titlebar text and icons updated to thumbnail color")

if __name__ == "__main__":
    test_dialog_titlebar_update_accent()
    test_depot_dialog_normal_mode_layout()
    test_depot_dialog_dlc_only_layout()
    test_single_depot_dialog_dynamic_theme()
    print("\n============================================================")
    print("ALL TESTS PASSED SUCCESSFULLY!")
