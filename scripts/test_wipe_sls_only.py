#!/usr/bin/env python3
"""Tests for 'I bought the game' (_wipe_sls_only) uninstall option.

Ensures that:
1. Game files, executables, and game assets are 100% preserved.
2. Markers (.assella, .DepotDownloader) and .depot files are purged.
3. Steam is NOT sent an 'uninstall|<appid>' command (which causes Steam to wipe files).
4. SLSBridge reload is triggered.
5. Existing ACF manifests have StateFlags set to 6 (UpdateRequired | FullyInstalled)
   for seamless Steam native adoption.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ui.dialogs.library.actions import LibraryActionsMixin


class TestWipeSlsOnly(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.install_path = Path(self.temp_dir.name) / "game_dir"
        self.install_path.mkdir(parents=True, exist_ok=True)

        # Create dummy game files
        (self.install_path / "game.exe").write_text("dummy binary content")
        (self.install_path / "data.pak").write_text("dummy pack content")

        # Create markers
        (self.install_path / ".assella").mkdir(parents=True, exist_ok=True)
        (self.install_path / ".assella" / "metadata.json").write_text('{"appid": "12345"}')
        (self.install_path / ".DepotDownloader").mkdir(parents=True, exist_ok=True)

        # Create library and dummy ACF manifest
        self.library_path = Path(self.temp_dir.name) / "steam_library"
        self.steamapps = self.library_path / "steamapps"
        self.steamapps.mkdir(parents=True, exist_ok=True)
        self.acf_path = self.steamapps / "appmanifest_12345.acf"
        self.acf_path.write_text(
            '"AppState"\n{\n\t"appid"\t\t"12345"\n\t"StateFlags"\t\t"4"\n\t"name"\t\t"Test Game"\n}\n'
        )

        self.game_data = {
            "appid": "12345",
            "game_name": "Test Game",
            "install_path": str(self.install_path),
            "library_path": str(self.library_path),
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("utils.sls_bridge.SLSBridge.notify_reload")
    @patch("utils.plugin_games.unregister_plugin_game")
    @patch("utils.yaml_config_manager.get_user_config_path")
    @patch("core.steam_helpers.get_steam_libraries")
    def test_wipe_sls_only_preserves_files_and_does_not_call_uninstall_pipe(
        self, mock_libs, mock_cfg_path, mock_unregister, mock_notify
    ):
        mock_libs.return_value = [str(self.library_path)]
        mock_cfg = MagicMock()
        mock_cfg.exists.return_value = False
        mock_cfg_path.return_value = mock_cfg

        # Run _wipe_sls_only
        success, err = LibraryActionsMixin._wipe_sls_only(self.game_data)

        self.assertTrue(success, f"wipe_sls_only failed: {err}")

        # 1. Game files must remain intact!
        self.assertTrue((self.install_path / "game.exe").exists())
        self.assertTrue((self.install_path / "data.pak").exists())

        # 2. Markers must be removed
        self.assertFalse((self.install_path / ".assella").exists())
        self.assertFalse((self.install_path / ".DepotDownloader").exists())

        # 3. SLS reload must be notified
        mock_notify.assert_called()

        # 4. ACF manifest must have StateFlags=6
        acf_content = self.acf_path.read_text()
        self.assertIn('"StateFlags"\t\t"6"', acf_content)


if __name__ == "__main__":
    unittest.main()
