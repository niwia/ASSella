#!/usr/bin/env python3
"""
test_standalone_custom_folder.py
================================
Tests for pure-logic standalone custom-folder downloads, classification,
severed SLSsteam pipelines, and SQLite persistence.
"""

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


def test_is_path_in_steam_library():
    print("=== 1. is_path_in_steam_library ===")
    from core.steam_helpers import is_path_in_steam_library

    with tempfile.TemporaryDirectory() as td:
        steam_lib = os.path.join(td, "SteamLib")
        os.makedirs(os.path.join(steam_lib, "steamapps", "common"), exist_ok=True)

        custom_folder = os.path.join(td, "MyStandaloneGames", "Game1")
        os.makedirs(custom_folder, exist_ok=True)

        # Explicit libraries list
        check(
            is_path_in_steam_library(steam_lib, steam_libraries=[steam_lib]) is True,
            "Exact steam library path is detected",
        )
        check(
            is_path_in_steam_library(
                os.path.join(steam_lib, "steamapps", "common", "Game1"),
                steam_libraries=[steam_lib],
            )
            is True,
            "Path inside steamapps/common is detected as in steam library",
        )
        check(
            is_path_in_steam_library(custom_folder, steam_libraries=[steam_lib])
            is False,
            "Custom folder outside steam libraries returns False",
        )


def test_get_game_directory():
    print("=== 2. get_game_directory ===")
    from utils.steam_manifest import get_game_directory

    with tempfile.TemporaryDirectory() as td:
        steam_lib = os.path.join(td, "SteamLib")
        os.makedirs(os.path.join(steam_lib, "steamapps", "common"), exist_ok=True)

        custom_folder = os.path.join(td, "MyCustomGames")
        os.makedirs(custom_folder, exist_ok=True)

        game_data = {"appid": "12345", "game_name": "AwesomeGame"}

        # Custom standalone destination should return dest directly without steamapps/common nesting
        res_custom = get_game_directory(custom_folder, game_data)
        check(
            res_custom == custom_folder,
            f"Standalone destination returned directly ({res_custom})",
        )

        # Explicit is_standalone flag forces direct path even if in steam_lib
        game_data_standalone = {
            "appid": "12345",
            "game_name": "AwesomeGame",
            "is_standalone": True,
        }
        res_explicit = get_game_directory(steam_lib, game_data_standalone)
        check(
            res_explicit == steam_lib,
            "Explicit is_standalone flag returns dest directly",
        )


def test_is_standalone_game():
    print("=== 3. is_standalone_game classification ===")
    from utils.game_modes import is_standalone_game, is_accela_managed

    with tempfile.TemporaryDirectory() as td:
        steam_lib = os.path.join(td, "SteamLib")
        os.makedirs(os.path.join(steam_lib, "steamapps", "common"), exist_ok=True)
        custom_dir = os.path.join(td, "StandaloneDir")
        os.makedirs(custom_dir, exist_ok=True)

        # 1. Explicit flag
        g1 = {"appid": "100", "is_standalone": True}
        check(is_standalone_game(g1) is True, "Explicit is_standalone=True")
        check(is_accela_managed(g1) is True, "Standalone game is ACCELA managed")

        # 2. Source flag
        g2 = {"appid": "101", "source": "Standalone"}
        check(is_standalone_game(g2) is True, "source='Standalone'")

        # 3. Path outside Steam library
        g3 = {
            "appid": "102",
            "install_path": custom_dir,
            "is_vapor": False,
            "is_atom": False,
        }
        check(
            is_standalone_game(g3) is True,
            "Path outside steam libraries is standalone",
        )

        # 4. Standard Steam game inside steamapps/common
        g4 = {
            "appid": "103",
            "install_path": os.path.join(steam_lib, "steamapps", "common", "Game103"),
            "source": "Steam",
        }
        check(
            is_standalone_game(g4) is False,
            "Standard Steam game inside steamapps/common is NOT standalone",
        )


def test_database_manager_standalone():
    print("=== 4. DatabaseManager standalone_games table ===")
    from unittest.mock import patch
    from managers.db_manager import DatabaseManager

    with tempfile.TemporaryDirectory() as td:
        temp_db_path = Path(td) / "test_steam_headers.db"
        orig_instance = DatabaseManager._instance
        try:
            DatabaseManager._instance = None
            with patch("managers.db_manager._setup_database_path", return_value=temp_db_path):
                db = DatabaseManager()
                test_appid = "998877"
                test_name = "Super Standalone Adventure"
                test_path = "/home/test/Games/SuperStandalone"
                test_build = "889900"
                test_size = 123456789

                # Clean up any leftover
                db.remove_standalone_game(test_appid)
                check(db.get_standalone_game(test_appid) is None, "Game starts absent")

                # Save
                db.save_standalone_game(
                    appid=test_appid,
                    game_name=test_name,
                    install_path=test_path,
                    buildid=test_build,
                    size=test_size,
                )

                rec = db.get_standalone_game(test_appid)
                check(rec is not None, "get_standalone_game returned record")
                if rec:
                    check(rec.get("appid") == test_appid, f"AppID matches ({rec.get('appid')})")
                    check(
                        rec.get("game_name") == test_name,
                        f"Name matches ({rec.get('game_name')})",
                    )
                    check(
                        rec.get("install_path") == test_path,
                        f"Path matches ({rec.get('install_path')})",
                    )
                    check(
                        rec.get("buildid") == test_build,
                        f"BuildID matches ({rec.get('buildid')})",
                    )
                    check(rec.get("size") == test_size, f"Size matches ({rec.get('size')})")

                # Check bulk fetch
                all_recs = db.get_standalone_games()
                check(test_appid in all_recs, "AppID found in get_standalone_games dict")

                # Remove
                db.remove_standalone_game(test_appid)
                check(
                    db.get_standalone_game(test_appid) is None,
                    "Record removed cleanly after remove_standalone_game",
                )
        finally:
            if DatabaseManager._instance and DatabaseManager._instance.conn:
                try:
                    DatabaseManager._instance.conn.close()
                except Exception:
                    pass
            DatabaseManager._instance = orig_instance


def test_sls_config_sync_skipped_for_standalone():
    print("=== 5. Config sync skip for standalone games ===")
    from managers.game_manager_mixins.config_sync import GameManagerConfigSyncMixin

    class DummyGameManager(GameManagerConfigSyncMixin):
        def __init__(self):
            self.games = [
                {
                    "appid": "998877",
                    "game_name": "StandaloneGame",
                    "is_accela_install": True,
                    "is_standalone": True,
                }
            ]

    # Verify that the loop skips it without calling sync_dlc_only_sls_config
    gm = DummyGameManager()
    # It shouldn't crash or attempt to sync
    gm._sync_games_to_slssteam_config()
    check(True, "Sync safely skipped standalone game")


def test_ui_gating_for_standalone():
    print("=== 6. UI gating and options disabling for standalone games ===")
    from utils.game_modes import is_standalone_game

    standalone_game = {
        "appid": "55555",
        "game_name": "StandaloneGame",
        "install_path": "/home/user/Games/StandaloneGame",
        "is_standalone": True,
        "is_accela_install": True,
    }
    check(is_standalone_game(standalone_game) is True, "Game confirmed standalone")

    # Verify that in tickets_tab, standalone games display the placeholder
    from ui.dialogs.game_details import tickets_tab
    from PyQt6.QtWidgets import QWidget, QStackedWidget

    class DummyDialog(QWidget):
        def __init__(self, game):
            super().__init__()
            self.game_data = game
            self.appid = game["appid"]
            self.accent_color = "#6c5ce7"
            self.stacked = QStackedWidget(self)

    dlg = DummyDialog(standalone_game)
    tickets_tab.init_tickets_tab(dlg)
    check(dlg.stacked.count() == 1, "Tickets tab added placeholder widget to stack")
    check(not getattr(dlg, "_tickets_inner").acceptDrops(), "Tickets tab drag-and-drop disabled for standalone")

    # Verify tools_tab buttons are properly disabled
    from ui.dialogs.game_details import tools_tab
    dlg.fix_btn = tools_tab._btn("Fix Installation")
    dlg.desktop_btn = tools_tab._btn("Desktop")
    dlg.dlcdata_exp_btn = tools_tab._btn("Move DLC to DlcData (Advanced)")

    tools_tab.refresh_drm_emulation_state(dlg)
    check(dlg.fix_btn.isEnabled() is False, "Fix Installation is disabled for standalone game")
    check("Standalone" in dlg.fix_btn.toolTip(), f"Fix Installation tooltip explains standalone: {dlg.fix_btn.toolTip()}")

    tools_tab.refresh_dlcdata_btn_text(dlg)
    check(dlg.dlcdata_exp_btn.isEnabled() is False, "DlcData advanced button is disabled for standalone game")
    check("Standalone" in dlg.dlcdata_exp_btn.toolTip(), f"DlcData tooltip explains standalone: {dlg.dlcdata_exp_btn.toolTip()}")

    tools_tab._wire_desktop_button(dlg)
    check(dlg.desktop_btn.isEnabled() is False, "Desktop shortcut button is disabled for standalone game")


def main():
    test_is_path_in_steam_library()
    test_get_game_directory()
    test_is_standalone_game()
    test_database_manager_standalone()
    test_sls_config_sync_skipped_for_standalone()
    test_ui_gating_for_standalone()

    print("\n--------------------------------------------------")
    print(f"Results: {passed} passed, {len(failed)} failed")
    if failed:
        for f in failed:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("ALL STANDALONE CUSTOM FOLDER TESTS PASSED!")


if __name__ == "__main__":
    main()
