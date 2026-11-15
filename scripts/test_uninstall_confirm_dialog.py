"""
Tests for UninstallConfirmDialog, prefix discovery/scanning, and API gate.
"""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])


class TestUninstallConfirmDialog(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.library_path = self.tmp_dir / "SteamLibrary"
        self.install_path = self.library_path / "steamapps" / "common" / "TestGame"
        self.compatdata_path = self.library_path / "steamapps" / "compatdata" / "99999"

        self.install_path.mkdir(parents=True, exist_ok=True)
        self.compatdata_path.mkdir(parents=True, exist_ok=True)

        (self.install_path / "game.bin").write_text("dummy binary")
        (self.compatdata_path / "pfx_file.dat").write_bytes(b"X" * 1024 * 1024)  # 1 MB

        self.game_data = {
            "game_name": "Test Game",
            "appid": "99999",
            "install_path": str(self.install_path),
            "library_path": str(self.library_path),
        }

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_find_compatdata_path(self):
        from ui.dialogs.uninstall_confirm_dialog import find_compatdata_path

        found = find_compatdata_path("99999", str(self.install_path), str(self.library_path))
        self.assertEqual(found, str(self.compatdata_path))

        not_found = find_compatdata_path("88888", str(self.install_path), str(self.library_path))
        self.assertIsNone(not_found)

    def test_dialog_ui_initialization_and_toggle(self):
        from ui.dialogs.uninstall_confirm_dialog import UninstallConfirmDialog

        dlg = UninstallConfirmDialog(None, game_data=self.game_data)
        self.assertFalse(dlg.wipe_prefix_cb.isHidden())
        self.assertFalse(dlg.should_wipe_prefix())
        self.assertIn("Wipe Proton prefix (with saves)", dlg.wipe_prefix_cb.text())

        # Toggle on
        dlg.wipe_prefix_cb.setChecked(True)
        self.assertTrue(dlg.should_wipe_prefix())
        self.assertIn("Proton prefix (with saves):", dlg.summary_lbl.text())

        # Toggle off
        dlg.wipe_prefix_cb.setChecked(False)
        self.assertFalse(dlg.should_wipe_prefix())

        dlg._cleanup_scanner()

    def test_dialog_dlc_only_mode(self):
        from ui.dialogs.uninstall_confirm_dialog import UninstallConfirmDialog

        dlc_game_data = dict(self.game_data)
        dlc_game_data["dlc_only"] = True

        dlg = UninstallConfirmDialog(None, game_data=dlc_game_data)
        self.assertTrue(dlg.is_dlc_only)
        self.assertFalse(dlg.wipe_prefix_cb.isVisible())
        self.assertIn("Base game directory (100% intact)", dlg.summary_lbl.text())
        dlg._cleanup_scanner()

    @patch("core.native_steam.native_steam_handoff.send_sls_api")
    def test_uninstall_game_api_gate_on_wipe_prefix(self, mock_send_sls):
        from managers.game_manager_mixins.uninstall import GameManagerUninstallMixin

        class MockGM(GameManagerUninstallMixin):
            def __init__(self):
                self.games = []
                self.last_uninstall_summary = None

            def remove_game(self, appid):
                pass

        gm = MockGM()

        # 1. Standard uninstall (remove_compatdata=False) -> MUST NOT call send_sls_api
        gm.uninstall_game(self.game_data, remove_compatdata=False)
        mock_send_sls.assert_not_called()

        # Recreate test paths for second run
        self.install_path.mkdir(parents=True, exist_ok=True)
        (self.install_path / "game.bin").write_text("dummy binary")

        # 2. Wipe prefix requested (remove_compatdata=True) -> MUST call send_sls_api
        gm.uninstall_game(self.game_data, remove_compatdata=True)
        mock_send_sls.assert_called_with("uninstall|99999")


if __name__ == "__main__":
    unittest.main()
