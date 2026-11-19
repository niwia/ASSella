#!/usr/bin/env python3
"""Tests for custom folder selection, persistence, and Steam library registration."""

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication

app = QApplication.instance()
if app is None:
    app = QApplication([sys.argv[0], "-platform", "offscreen"])

passed = 0
failed = []


def check(cond: bool, label: str):
    global passed
    if cond:
        passed += 1
        print(f"  [ok] {label}")
    else:
        failed.append(label)
        print(f"  [FAIL] {label}")


TEST_WORK_DIR = REPO_ROOT / ".test_custom_work"


def get_test_dir(name: str) -> Path:
    d = TEST_WORK_DIR / name
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_blacklisted_system_path():
    print("=== 1. is_blacklisted_system_path ===")
    from utils.paths import is_blacklisted_system_path

    check(is_blacklisted_system_path("") is True, "empty string is blacklisted")
    check(is_blacklisted_system_path("   ") is True, "whitespace is blacklisted")
    check(is_blacklisted_system_path(None) is True, "None is blacklisted")
    check(is_blacklisted_system_path("/tmp") is True, "/tmp is blacklisted")
    check(is_blacklisted_system_path("/tmp/test") is True, "/tmp/... is blacklisted")
    check(is_blacklisted_system_path("/var/tmp") is True, "/var/tmp is blacklisted")
    check(is_blacklisted_system_path("/proc/123") is True, "/proc is blacklisted")
    check(is_blacklisted_system_path("/tmp/.mount_ASSellaxxx/usr") is True, "AppImage mount is blacklisted")

    # Legitimate paths (even if not currently mounted on disk)
    check(is_blacklisted_system_path("/run/media/deck/SDCARD") is False, "unmounted /run/media path is NOT blacklisted")
    check(is_blacklisted_system_path("/media/user/Drive1") is False, "unmounted /media path is NOT blacklisted")
    check(is_blacklisted_system_path("/mnt/CustomGames") is False, "unmounted /mnt path is NOT blacklisted")
    check(is_blacklisted_system_path(str(Path.home() / "Games")) is False, "user Games path is NOT blacklisted")


def test_normalize_to_library_root():
    print("=== 2. normalize_to_library_root ===")
    from utils.paths import normalize_to_library_root

    with tempfile.TemporaryDirectory() as td:
        lib_root = os.path.realpath(td)
        sa_dir = os.path.join(lib_root, "steamapps")
        common_dir = os.path.join(sa_dir, "common")
        game_dir = os.path.join(common_dir, "MyGame")
        os.makedirs(game_dir, exist_ok=True)

        check(normalize_to_library_root(game_dir) == lib_root, "game directory normalized to library root")
        check(normalize_to_library_root(common_dir) == lib_root, "common directory normalized to library root")
        check(normalize_to_library_root(sa_dir) == lib_root, "steamapps directory normalized to library root")
        check(normalize_to_library_root(lib_root) == lib_root, "library root remains intact")

    check(normalize_to_library_root("") == "", "empty string handled safely")


def test_ensure_steam_library():
    print("=== 3. ensure_steam_library registration in libraryfolders.vdf ===")
    from core import steam_helpers

    td = str(get_test_dir("ensure_steam_lib"))
    mock_steam = os.path.join(td, "Steam")
    steamapps = os.path.join(mock_steam, "steamapps")
    os.makedirs(steamapps, exist_ok=True)

    vdf_path = os.path.join(steamapps, "libraryfolders.vdf")
    initial_vdf = f'''"libraryfolders"
{{
\t"0"
\t{{
\t\t"path"\t\t"{mock_steam}"
\t\t"label"\t\t""
\t\t"contentid"\t\t"12345"
\t\t"totalsize"\t\t"0"
\t\t"apps"\n\t\t{{\n\t\t}}\n\t}}
}}
'''
    with open(vdf_path, "w", encoding="utf-8") as f:
        f.write(initial_vdf)

    custom_lib = os.path.join(td, "ExternalDrive", "CustomLib")

    # 1. Register new library
    idx = steam_helpers.ensure_steam_library(custom_lib, steam_path=mock_steam)
    check(idx == 1, f"new library assigned index 1 (got {idx})")
    check(os.path.isdir(os.path.join(custom_lib, "steamapps", "common")), "steamapps/common created")
    check(os.path.isfile(os.path.join(custom_lib, "libraryfolder.vdf")), "libraryfolder.vdf sidecar created at library root")

    # Verify get_library_index finds it
    resolved_idx = steam_helpers.get_library_index(custom_lib, steam_path=mock_steam)
    check(resolved_idx == 1, f"get_library_index returns registered index 1 (got {resolved_idx})")

    # 2. Re-registering existing library returns existing index without duplicating
    idx2 = steam_helpers.ensure_steam_library(custom_lib, steam_path=mock_steam)
    check(idx2 == 1, "re-registering returns existing index 1")

    with open(vdf_path, "r", encoding="utf-8") as f:
        vdf_content = f.read()
    indices = [m for m in vdf_content.splitlines() if '"1"' in m]
    check(len(indices) == 1, "no duplicate index '1' in libraryfolders.vdf")

    # 3. Primary steam install returns 0
    idx_primary = steam_helpers.ensure_steam_library(mock_steam, steam_path=mock_steam)
    check(idx_primary == 0, "primary steam install returns index 0")


def test_library_choice_custom_persistence():
    print("=== 4. library_choice custom folder persistence ===")
    from PyQt6.QtCore import QSettings
    from utils import library_choice

    td = str(get_test_dir("lib_choice"))
    settings_file = os.path.join(td, "test_settings.ini")
    test_settings = QSettings(settings_file, QSettings.Format.IniFormat)

    custom_folder = os.path.join(td, "MyCustomGames")
    os.makedirs(custom_folder, exist_ok=True)

    steam_libs = [os.path.join(td, "DefaultSteam")]

    # Case A: Remember custom library
    library_choice.remember_library(custom_folder, test_settings)
    check(library_choice.preferred_library(test_settings) == custom_folder, "preferred_library saved")
    check(library_choice.ask_each_time(test_settings) is False, "ask_each_time is False after remember")

    # remembered_library must return custom_folder even though not in steam_libs
    rem = library_choice.remembered_library(steam_libs, test_settings)
    check(rem == custom_folder, f"remembered_library returns custom folder (got {rem})")

    # preselect_library must return custom_folder
    pre = library_choice.preselect_library(steam_libs, test_settings)
    check(pre == custom_folder, f"preselect_library returns custom folder (got {pre})")

    # Case B: When ask_each_time is True, remembered_library returns None
    library_choice.set_preferences(custom_folder, ask=True, settings=test_settings)
    check(library_choice.ask_each_time(test_settings) is True, "ask_each_time is True")
    check(library_choice.remembered_library(steam_libs, test_settings) is None, "remembered_library returns None when asking")
    check(library_choice.preselect_library(steam_libs, test_settings) == custom_folder, "preselect_library still preselects custom folder")


def test_dialogs_custom_storage_ui():
    print("=== 5. UI controls in SingleDepotSelectionDialog and DepotSelectionDialog ===")
    from ui.dialogs.depot_selection.dialog import DepotSelectionDialog
    from ui.dialogs.single_depot_dialog import SingleDepotSelectionDialog

    with tempfile.TemporaryDirectory() as td:
        depots_single = {"100": {"desc": "Main Depot", "size": 1024}}
        dlg_single = SingleDepotSelectionDialog(
            "123", "Test Game Single", depots_single, library_path=td
        )
        check(hasattr(dlg_single, "_custom_storage_btn"), "SingleDepot has _custom_storage_btn")
        check(hasattr(dlg_single, "remember_storage_chk"), "SingleDepot has remember_storage_chk")
        check(hasattr(dlg_single, "should_remember_storage"), "SingleDepot has should_remember_storage method")
        check(callable(dlg_single.should_remember_storage), "should_remember_storage is callable")
        check(dlg_single.should_remember_storage() is False, "should_remember_storage starts False")
        dlg_single.remember_storage_chk.setChecked(True)
        check(dlg_single.should_remember_storage() is True, "should_remember_storage is True when checked")
        check(bool(dlg_single.get_selected_storage()), "get_selected_storage returns a valid target")
        dlg_single.close()

        depots_multi = {
            "100": {"desc": "Depot 1", "size": 1024},
            "101": {"desc": "Depot 2", "size": 2048},
        }
        dlg_multi = DepotSelectionDialog(
            "456", "Test Game Multi", depots_multi, library_path=td
        )
        check(hasattr(dlg_multi, "_custom_storage_btn"), "MultiDepot has _custom_storage_btn")
        check(hasattr(dlg_multi, "remember_storage_chk"), "MultiDepot has remember_storage_chk")
        check(hasattr(dlg_multi, "should_remember_storage"), "MultiDepot has should_remember_storage method")
        check(callable(dlg_multi.should_remember_storage), "MultiDepot should_remember_storage is callable")
        check(dlg_multi.should_remember_storage() is False, "MultiDepot should_remember_storage starts False")
        dlg_multi.remember_storage_chk.setChecked(True)
        check(dlg_multi.should_remember_storage() is True, "MultiDepot should_remember_storage is True when checked")
        check(bool(dlg_multi.get_selected_storage()), "MultiDepot get_selected_storage returns valid target")
        dlg_multi.close()


def test_steam_library_dialog_custom():
    print("=== 6. SteamLibraryDialog custom folder support ===")
    from ui.dialogs.steamlibrary import SteamLibraryDialog

    with tempfile.TemporaryDirectory() as td:
        lib1 = os.path.join(td, "Lib1")
        os.makedirs(lib1, exist_ok=True)

        dlg = SteamLibraryDialog([lib1])
        check(hasattr(dlg, "add_custom_btn"), "SteamLibraryDialog has add_custom_btn")
        check(dlg.add_custom_btn is not None, "add_custom_btn is added to UI")
        check(hasattr(dlg, "remember_chk"), "SteamLibraryDialog has remember_chk")
        dlg.close()


def test_zero_libraries_custom_ui():
    print("=== 7. Zero libraries detected still allows Custom folder ===")
    from unittest.mock import patch
    from ui.dialogs.depot_selection.dialog import DepotSelectionDialog
    from ui.dialogs.single_depot_dialog import SingleDepotSelectionDialog

    with patch("core.steam_helpers.get_steam_libraries", return_value=[]):
        depots_single = {"100": {"desc": "Main Depot", "size": 1024}}
        dlg_single = SingleDepotSelectionDialog(
            "999", "Zero Lib Single", depots_single
        )
        check(hasattr(dlg_single, "_custom_storage_btn"), "SingleDepot has _custom_storage_btn even with 0 libs")
        check(hasattr(dlg_single, "remember_storage_chk"), "SingleDepot has remember_storage_chk even with 0 libs")
        check(dlg_single._custom_storage_btn.text() == "Custom...", "SingleDepot custom button has clean text without emoji")
        dlg_single.close()

        depots_multi = {
            "100": {"desc": "Depot 1", "size": 1024},
            "101": {"desc": "Depot 2", "size": 2048},
        }
        dlg_multi = DepotSelectionDialog(
            "998", "Zero Lib Multi", depots_multi
        )
        check(hasattr(dlg_multi, "_custom_storage_btn"), "MultiDepot has _custom_storage_btn even with 0 libs")
        check(hasattr(dlg_multi, "remember_storage_chk"), "MultiDepot has remember_storage_chk even with 0 libs")
        check(dlg_multi._custom_storage_btn.text() == "Custom...", "MultiDepot custom button has clean text without emoji")
        dlg_multi.close()


def test_offline_custom_path_cancel_in_settings():
    print("=== 8. [Offline] custom path + Cancel in Settings -> Downloads ===")
    from unittest.mock import patch
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QDialog, QFrame, QVBoxLayout, QTabWidget
    from ui.dialogs.settings_tabs.downloads_tab import create_downloads_tab

    td = str(get_test_dir("settings_offline"))
    settings_file = os.path.join(td, "test_settings.ini")
    test_settings = QSettings(settings_file, QSettings.Format.IniFormat)

    offline_path = "/run/media/deck/OFFLINE_CARD/Games"
    test_settings.setValue("default_download_directory", offline_path)
    test_settings.setValue("ask_library_each_time", False)

    mock_dialog = QDialog()
    mock_dialog.settings = test_settings
    mock_dialog.accent_color = "#6c5ce7"
    mock_dialog.tab_widget = QTabWidget()
    mock_dialog._create_card_frame = lambda title="": (QFrame(), QVBoxLayout())

    tab_widget = create_downloads_tab(mock_dialog)

    # 1. Verify combo initialized with [Offline] path at index 1
    combo = mock_dialog.dl_location_combo
    check(combo.currentIndex() == 1, f"Offline path preselected at index 1 (got {combo.currentIndex()})")
    check(combo.currentData() == offline_path, f"Combo currentData matches offline path (got {combo.currentData()})")
    check("[Offline]" in combo.currentText(), f"Combo currentText displays [Offline] tag (got {combo.currentText()})")
    check(test_settings.value("default_download_directory") == offline_path, "default_download_directory preserved initially")

    # 2. User selects "Custom Folder..." then Cancels in ThemedFileDialog
    custom_idx = combo.findData("custom")
    check(custom_idx >= 0, "Custom Folder... option found in combo")

    with patch("ui.dialogs.themed_file_dialog.ThemedFileDialog.getExistingDirectory", return_value=""):
        combo.setCurrentIndex(custom_idx)

    # 3. Verify previous index restored, offline path intact, setting not cleared
    check(combo.currentIndex() == 1, f"Combo restored to index 1 after cancel (got {combo.currentIndex()})")
    check(combo.currentData() == offline_path, f"Combo data still matches offline path (got {combo.currentData()})")
    check(test_settings.value("default_download_directory") == offline_path, f"default_download_directory NOT cleared after cancel (got {test_settings.value('default_download_directory')})")
    check(test_settings.value("ask_library_each_time", type=bool) is False, "ask_library_each_time NOT reset to True")

    mock_dialog.close()


def test_notify_steam_restart_when_steam_running():
    print("=== 9. Steam running notice when allocating new library index ===")
    from unittest.mock import patch, MagicMock
    from core import steam_helpers

    td = str(get_test_dir("steam_running_notice"))
    mock_steam = os.path.join(td, "Steam")
    steamapps = os.path.join(mock_steam, "steamapps")
    os.makedirs(steamapps, exist_ok=True)

    vdf_path = os.path.join(steamapps, "libraryfolders.vdf")
    with open(vdf_path, "w", encoding="utf-8") as f:
        f.write('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"' + mock_steam + '"\n\t}\n}\n')

    new_lib1 = os.path.join(td, "NewLib1")
    new_lib2 = os.path.join(td, "NewLib2")
    new_lib3 = os.path.join(td, "NewLib3")

    # Case A: Steam running -> allocating new index calls _notify_steam_restart_for_new_library
    with patch("core.steam_helpers.is_steam_running", return_value=True):
        with patch("core.steam_helpers._notify_steam_restart_for_new_library") as mock_notify:
            idx = steam_helpers.ensure_steam_library(new_lib1, steam_path=mock_steam)
            check(idx == 1, f"new library allocated index 1 (got {idx})")
            check(mock_notify.called, "notice called when allocating new index while Steam is running")

            # Re-registering existing library must NOT trigger notice again
            mock_notify.reset_mock()
            idx_again = steam_helpers.ensure_steam_library(new_lib1, steam_path=mock_steam)
            check(idx_again == 1, "re-registering returns existing index 1")
            check(not mock_notify.called, "notice NOT called for already registered library")

    # Case B: Steam NOT running -> no notice triggered
    with patch("core.steam_helpers.is_steam_running", return_value=False):
        with patch("core.steam_helpers._notify_steam_restart_for_new_library") as mock_notify:
            idx2 = steam_helpers.ensure_steam_library(new_lib2, steam_path=mock_steam)
            check(idx2 == 2, f"new library allocated index 2 (got {idx2})")
            check(not mock_notify.called, "notice NOT called when Steam is not running")

    # Case C: notify_if_steam_running=False suppresses notice even if Steam is running
    with patch("core.steam_helpers.is_steam_running", return_value=True):
        with patch("core.steam_helpers._notify_steam_restart_for_new_library") as mock_notify:
            idx3 = steam_helpers.ensure_steam_library(new_lib3, steam_path=mock_steam, notify_if_steam_running=False)
            check(idx3 == 3, f"new library allocated index 3 (got {idx3})")
            check(not mock_notify.called, "notice suppressed when notify_if_steam_running=False")


def main() -> int:
    test_blacklisted_system_path()
    test_normalize_to_library_root()
    test_ensure_steam_library()
    test_library_choice_custom_persistence()
    test_dialogs_custom_storage_ui()
    test_steam_library_dialog_custom()
    test_zero_libraries_custom_ui()
    test_offline_custom_path_cancel_in_settings()
    test_notify_steam_restart_when_steam_running()

    # Clean up test work dir
    if TEST_WORK_DIR.exists():
        shutil.rmtree(TEST_WORK_DIR, ignore_errors=True)

    print("\n--------------------------------------------------")
    print(f"Results: {passed} passed, {len(failed)} failed")
    if failed:
        print("Failures:")
        for f in failed:
            print(f"  - {f}")
        return 1
    print("ALL CUSTOM FOLDER WORKFLOW TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
