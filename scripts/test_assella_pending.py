#!/usr/bin/env python3
"""Tests for utils/assella_pending.py discovery, filtering, and clean removal."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from utils.assella_pending import (
    _extract_additional_apps_with_metadata,
    get_pending_assella_games,
    remove_pending_assella_game,
)


class TestAssellaPending(unittest.TestCase):
    def test_extract_metadata(self):
        mock_cfg = MagicMock()
        mock_cfg.exists.return_value = True
        mock_cfg.read_text.return_value = """
Plugins: yes
AdditionalApps:
  - 1091500 # Cyberpunk 2077
  - 480
  - 252490 # Rust
GameTitles:
  480: "Spacewar"
DecryptionKeys:
"""
        appids, comments, titles = _extract_additional_apps_with_metadata(mock_cfg)
        self.assertEqual(appids, ["1091500", "480", "252490"])
        self.assertEqual(comments.get("1091500"), "Cyberpunk 2077")
        self.assertEqual(comments.get("252490"), "Rust")
        self.assertEqual(titles.get("480"), "Spacewar")

    @patch("utils.yaml_config_manager.get_user_config_path")
    @patch("utils.plugin_games.get_all_plugin_games")
    @patch("utils.dlc_helpers.is_dlc_only_mode")
    @patch("utils.library_rules.resolve_best_game_name")
    def test_get_pending_assella_games(self, mock_resolve, mock_dlc_only, mock_plugin, mock_cfg_path):
        mock_cfg = MagicMock()
        mock_cfg.exists.return_value = True
        mock_cfg.read_text.return_value = """
AdditionalApps:
  - 100 # Installed
  - 200 # Atom game
  - 300 # DLC only
  - 400 # Real pending ASSella game
"""
        mock_cfg_path.return_value = mock_cfg
        mock_plugin.return_value = {"200": {"name": "Atom Title"}}
        mock_dlc_only.side_effect = lambda aid: aid == "300"
        mock_resolve.side_effect = lambda aid, candidates=None: f"Resolved {aid}"

        installed_aids = {"100"}
        pending = get_pending_assella_games(installed_aids)

        self.assertEqual(len(pending), 1)
        game = pending[0]
        self.assertEqual(game["appid"], "400")
        self.assertEqual(game["game_name"], "Resolved 400")
        self.assertTrue(game["assella_pending"])
        self.assertEqual(game["update_status"], "not_downloaded")
        self.assertEqual(game["source"], "ACCELA")

    @patch("core.steam_helpers.slssteam_api_send")
    @patch("utils.yaml_config_manager.get_user_config_path")
    @patch("utils.yaml_config_manager.get_dlc_data")
    @patch("utils.yaml_config_manager.batch_config_edit")
    @patch("utils.settings.get_settings")
    def test_remove_pending_assella_game(self, mock_settings, mock_batch_edit, mock_get_dlc, mock_cfg_path, mock_sls_send):
        mock_cfg = MagicMock()
        mock_cfg.exists.return_value = True
        mock_cfg_path.return_value = mock_cfg
        mock_get_dlc.return_value = {}

        mock_editor = MagicMock()
        mock_batch_edit.return_value.__enter__.return_value = mock_editor

        mock_s = MagicMock()
        mock_s.allKeys.return_value = ["pin_build/400", "other_key"]
        mock_settings.return_value = mock_s

        ok, msg = remove_pending_assella_game({"appid": "400", "game_name": "Test Game"})
        self.assertTrue(ok)
        mock_sls_send.assert_called_with("uninstall|400")
        mock_editor.remove_app.assert_called_with("400")
        mock_s.remove.assert_called_with("pin_build/400")

    def test_assella_pending_dialog_ui(self):
        from PyQt6.QtWidgets import QApplication
        from ui.dialogs.assella_pending_dialog import AssellaPendingDialog

        _app = QApplication.instance() or QApplication(["-platform", "offscreen"])

        game = {
            "appid": "1003590",
            "game_name": "Tetris® Effect: Connected",
            "assella_pending": True,
        }
        dlg = AssellaPendingDialog(game, accent_color="#4CAF50")
        self.assertFalse(dlg.removed)
        self.assertFalse(dlg.download_requested)

        # Trigger download
        dlg._download()
        self.assertTrue(dlg.download_requested)


if __name__ == "__main__":
    unittest.main()
