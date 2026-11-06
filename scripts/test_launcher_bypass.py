#!/usr/bin/env python3
"""Functional and unit test for the Launcher Bypass feature in ASSella.

Tests:
1. Shell command construction and regex extraction.
2. YAML config.yaml persistence (set_bypass_exe, get_bypass_exe, remove_bypass).
3. Executable scanner (directory hierarchy, depth limit, filter rules).
4. BypassPickerDialog UI state, search filtering, and button interactions.
5. InfoTab mutual exclusion between Netsock and Bypass.
"""

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtWidgets import QApplication

# Ensure QApplication exists for Qt tests
app = QApplication.instance() or QApplication(["test_launcher_bypass"])

from utils.yaml_config_manager import (
    build_bypass_launch_command,
    extract_bypass_exe,
    get_bypass_exe,
    set_bypass_exe,
    remove_bypass,
    get_launch_option,
    add_launch_option,
    remove_launch_option,
)
from utils.executable_scanner import scan_game_executables, format_size
from ui.dialogs.game_details.bypass_picker_dialog import BypassPickerDialog

_failures = []
PASSED = 0


def check(cond, label):
    global PASSED
    if cond:
        PASSED += 1
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        _failures.append(label)
    return cond


def test_command_and_regex():
    print("\n=== 1. Shell Command Construction & Extraction ===")
    cmd = build_bypass_launch_command("Binaries/Win64/Game-Shipping.exe")
    check('cmd[-1]=\\"$PWD/Binaries/Win64/Game-Shipping.exe\\"' in cmd, "cmd string contains relative path with $PWD")
    check("bash -c" in cmd, "cmd uses bash -c")

    # Regex extraction
    extracted = extract_bypass_exe(cmd)
    check(extracted == "Binaries/Win64/Game-Shipping.exe", f"Extracted relative exe matches: {extracted}")

    # Root exe
    cmd_root = build_bypass_launch_command("Launcher.exe")
    check(extract_bypass_exe(cmd_root) == "Launcher.exe", "Extracted root exe matches")

    # Spaces in paths
    cmd_spaces = build_bypass_launch_command("My Game Folder/Sub Dir/Game Exe.exe")
    check(extract_bypass_exe(cmd_spaces) == "My Game Folder/Sub Dir/Game Exe.exe", "Extracted path with spaces matches")

    # Windows slashes normalization
    cmd_win = build_bypass_launch_command(r"Binaries\Win64\Game.exe")
    check(extract_bypass_exe(cmd_win) == "Binaries/Win64/Game.exe", "Windows backslashes normalized to forward slashes")

    # Netsock string should not be detected as bypass
    netsock_cmd = '"env LD_AUDIT=\\"/path/to/netsock.so\\" %command%"'
    check(extract_bypass_exe(netsock_cmd) is None, "Netsock command returns None for bypass extraction")


def test_yaml_persistence():
    print("\n=== 2. YAML Config.yaml Persistence ===")
    tmp = Path(tempfile.mkdtemp(prefix="bypass_yaml_"))
    cfg = tmp / "config.yaml"
    cfg.write_text("API: yes\nPlugins: yes\n", encoding="utf-8")

    app_id = "123450"

    # Set bypass
    res = set_bypass_exe(cfg, app_id, "Binaries/Win64/Game.exe")
    check(res, "set_bypass_exe succeeded")

    # Read back
    got = get_bypass_exe(cfg, app_id)
    check(got == "Binaries/Win64/Game.exe", f"get_bypass_exe returned {got}")

    # Change to root exe
    set_bypass_exe(cfg, app_id, "Game.exe")
    got2 = get_bypass_exe(cfg, app_id)
    check(got2 == "Game.exe", f"Updated bypass returned {got2}")

    # Remove bypass
    rem = remove_bypass(cfg, app_id)
    check(rem, "remove_bypass succeeded")
    check(get_bypass_exe(cfg, app_id) is None, "get_bypass_exe is None after removal")


def test_scanner():
    print("\n=== 3. Executable Scanner ===")
    tmp = Path(tempfile.mkdtemp(prefix="bypass_scan_"))

    # Root executables
    (tmp / "Game.exe").write_text("dummy")
    (tmp / "Launcher.exe").write_text("dummy")
    (tmp / "uninstall.exe").write_text("dummy")  # Should be filtered
    (tmp / "UnityCrashHandler64.exe").write_text("dummy")  # Should be filtered

    # Subdirectory executables
    b = tmp / "Binaries" / "Win64"
    b.mkdir(parents=True)
    (b / "Game-Win64-Shipping.exe").write_text("dummy")
    (b / "CrashReportClient.exe").write_text("dummy")  # Should be filtered

    # Redist directory
    c = tmp / "_CommonRedist" / "DirectX"
    c.mkdir(parents=True)
    (c / "DXSETUP.exe").write_text("dummy")  # Should be filtered

    # Deep directory (> 3 levels deep)
    deep = tmp / "A" / "B" / "C" / "D"
    deep.mkdir(parents=True)
    (deep / "TooDeep.exe").write_text("dummy")

    roots, subs = scan_game_executables(tmp, max_depth=3)
    root_names = [r["name"] for r in roots]
    sub_rels = [s["relative_path"] for s in subs]

    check("Game.exe" in root_names and "Launcher.exe" in root_names, "Root executables found")
    check("uninstall.exe" not in root_names, "Uninstaller filtered from root")
    check("UnityCrashHandler64.exe" not in root_names, "Unity crash handler filtered")
    check("Binaries/Win64/Game-Win64-Shipping.exe" in sub_rels, "Subdirectory executable found")
    check("Binaries/Win64/CrashReportClient.exe" not in sub_rels, "CrashReportClient filtered")
    check(not any("_CommonRedist" in s for s in sub_rels), "Redist directory ignored")
    check(not any("TooDeep.exe" in s for s in sub_rels), "Directory exceeding max_depth ignored")


def test_dialog_ui():
    print("\n=== 4. BypassPickerDialog UI & Actions ===")
    tmp = Path(tempfile.mkdtemp(prefix="bypass_ui_"))
    (tmp / "Game.exe").write_text("dummy")
    (tmp / "Launcher.exe").write_text("dummy")
    b = tmp / "Binaries" / "Win64"
    b.mkdir(parents=True)
    (b / "Shipping.exe").write_text("dummy")

    # 1. New dialog without existing bypass
    dlg = BypassPickerDialog(install_path=tmp, current_exe=None, accent_color="#38bdf8")
    check(dlg.select_btn.isEnabled() is False, "Select button disabled when nothing is selected")
    check(dlg.clear_btn.isEnabled() is False, "Clear button disabled when no bypass was active")

    # Filter search
    dlg.search_input.setText("Ship")
    check(len(dlg._row_widgets) == 1, "Search filtered list to Shipping.exe")
    check(dlg._row_widgets[0].item["name"] == "Shipping.exe", "Matched row is Shipping.exe")

    # Select row
    dlg._row_widgets[0].setChecked(True)
    check(dlg.selected_exe == "Binaries/Win64/Shipping.exe", "Row selection updated selected_exe")
    check(dlg.select_btn.isEnabled() is True, "Select button enabled after row check")

    # Clear search
    dlg.search_input.clear()
    check(len(dlg._row_widgets) == 3, "List restored after search cleared")

    # Click Select
    dlg._on_select_clicked()
    check(dlg.result_action == "select", "result_action is 'select'")

    # 2. Existing bypass pre-selected
    dlg_active = BypassPickerDialog(install_path=tmp, current_exe="Game.exe", accent_color="#38bdf8")
    check(dlg_active.selected_exe == "Game.exe", "Pre-selected existing bypass")
    check(dlg_active.clear_btn.isEnabled() is True, "Clear button enabled with existing bypass")
    dlg_active._on_clear_clicked()
    check(dlg_active.result_action == "clear", "Clear button triggers 'clear' action")


def test_info_tab_mutual_exclusion():
    print("\n=== 5. InfoTab Mutual Exclusion & Tile States ===")
    from ui.dialogs.game_details.widgets import SwitchTile
    from ui.dialogs.game_details.info_tab import update_eos_btn_state
    from PyQt6.QtWidgets import QWidget

    tmp = Path(tempfile.mkdtemp(prefix="bypass_tab_"))
    cfg = tmp / "config.yaml"
    cfg.write_text("API: yes\nLaunchOptions:\n", encoding="utf-8")

    # Patch user config path for this test
    import utils.yaml.core
    import utils.yaml_config_manager
    import utils.dlc_helpers

    orig_path_core = utils.yaml.core.get_user_config_path
    orig_path_mgr = utils.yaml_config_manager.get_user_config_path
    orig_dlc = utils.dlc_helpers.is_dlc_only_mode

    utils.yaml.core.get_user_config_path = lambda: cfg
    utils.yaml_config_manager.get_user_config_path = lambda: cfg
    utils.dlc_helpers.is_dlc_only_mode = lambda appid: False

    try:
        class MockDialog(QWidget):
            def __init__(self):
                super().__init__()
                self.appid = "test_bypass_7788"
                self.accent_color = "#38bdf8"
                self.game_data = {"install_path": str(tmp), "is_vapor": False, "is_atom": False}
                self.sls_tile = SwitchTile("SLSonline", is_toggle=True)
                self.netsock_tile = SwitchTile("Netsock", is_toggle=True)
                self.eos_tile = SwitchTile("EOS Proxy", is_toggle=True)
                self.bypass_tile = SwitchTile("Bypass", is_toggle=True)

        dlg = MockDialog()

        # State A: Fresh (no options configured, SLS active)
        dlg.sls_tile.setChecked(True)
        update_eos_btn_state(dlg)
        check(dlg.bypass_tile.isEnabled() is True, "State A: bypass_tile enabled")
        check(dlg.bypass_tile.isChecked() is False, "State A: bypass_tile unchecked")
        check(dlg.netsock_tile.isEnabled() is True, "State A: netsock_tile enabled")
        check(dlg.netsock_tile.isChecked() is False, "State A: netsock_tile unchecked")

        # State B: Netsock is configured in config.yaml
        add_launch_option(cfg, dlg.appid, '"env LD_AUDIT=\\"/path/to/netsock.so\\" %command%"')
        update_eos_btn_state(dlg)
        check(dlg.netsock_tile.isChecked() is True, "State B: netsock_tile checked")
        check(dlg.bypass_tile.isEnabled() is False, "State B: bypass_tile disabled by Netsock")
        check("Disable Netsock" in dlg.bypass_tile.toolTip(), "State B: bypass_tile tooltip explains Netsock conflict")

        # State C: Bypass is configured in config.yaml
        set_bypass_exe(cfg, dlg.appid, "Binaries/Win64/Game.exe")
        update_eos_btn_state(dlg)
        check(dlg.bypass_tile.isChecked() is True, "State C: bypass_tile checked")
        check(dlg.bypass_tile.isEnabled() is True, "State C: bypass_tile enabled")
        check("Launcher bypass active" in dlg.bypass_tile.toolTip(), "State C: bypass_tile tooltip shows active path")
        check(dlg.netsock_tile.isEnabled() is False, "State C: netsock_tile disabled by Bypass")
        check("Disable Launcher Bypass" in dlg.netsock_tile.toolTip(), "State C: netsock_tile tooltip explains Bypass conflict")

        # State D: Cleared
        remove_bypass(cfg, dlg.appid)
        update_eos_btn_state(dlg)
        check(dlg.bypass_tile.isChecked() is False, "State D: bypass_tile unchecked after clear")
        check(dlg.bypass_tile.isEnabled() is True, "State D: bypass_tile re-enabled after clear")
        check(dlg.netsock_tile.isEnabled() is True, "State D: netsock_tile re-enabled after clear")

        # State E: Missing folder with active bypass vs without
        dlg.game_data["install_path"] = "/non/existent/path"
        update_eos_btn_state(dlg)
        check(dlg.bypass_tile.isEnabled() is False, "State E: bypass_tile disabled when folder missing & no bypass")
        check("not found on disk" in dlg.bypass_tile.toolTip(), "State E: bypass_tile tooltip notes missing folder")

        set_bypass_exe(cfg, dlg.appid, "Game.exe")
        update_eos_btn_state(dlg)
        check(dlg.bypass_tile.isEnabled() is True, "State E: bypass_tile remains enabled when folder missing IF active bypass exists")

    finally:
        utils.yaml.core.get_user_config_path = orig_path_core
        utils.yaml_config_manager.get_user_config_path = orig_path_mgr
        utils.dlc_helpers.is_dlc_only_mode = orig_dlc


def main() -> int:
    test_command_and_regex()
    test_yaml_persistence()
    test_scanner()
    test_dialog_ui()
    test_info_tab_mutual_exclusion()

    print(f"\n============================================================")
    if _failures:
        print(f"FAILED ({len(_failures)} failures):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"ALL LAUNCHER BYPASS TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
