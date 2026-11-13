#!/usr/bin/env python3
"""Regression test suite for decomposed GameManager and its mixins."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src/ is on PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PyQt6.QtCore import QCoreApplication

# Ensure QCoreApplication exists for QObject tests
_app = QCoreApplication.instance() or QCoreApplication([])

from managers.game_manager import GameManager, UPDATE_STATUS as GM_UPDATE_STATUS
from managers.game_manager_mixins import (
    GameManagerScanningMixin,
    GameManagerUpdatesMixin,
    GameManagerUninstallMixin,
    GameManagerConfigSyncMixin,
    UPDATE_STATUS as MIXIN_UPDATE_STATUS,
)


class TestGameManagerMixins(unittest.TestCase):
    def setUp(self):
        self.gm = GameManager()

    def test_01_composition_and_mro(self):
        """GameManager inherits from QObject and all 4 domain mixins."""
        self.assertTrue(issubclass(GameManager, GameManagerScanningMixin))
        self.assertTrue(issubclass(GameManager, GameManagerUpdatesMixin))
        self.assertTrue(issubclass(GameManager, GameManagerUninstallMixin))
        self.assertTrue(issubclass(GameManager, GameManagerConfigSyncMixin))
        self.assertIsInstance(self.gm, GameManager)

    def test_02_update_status_constants(self):
        """UPDATE_STATUS constants match and are exported from both modules."""
        self.assertEqual(GM_UPDATE_STATUS, MIXIN_UPDATE_STATUS)
        self.assertEqual(GM_UPDATE_STATUS["UPDATE_AVAILABLE"], "update_available")
        self.assertEqual(GM_UPDATE_STATUS["UP_TO_DATE"], "up_to_date")
        self.assertEqual(GM_UPDATE_STATUS["CANNOT_DETERMINE"], "cannot_determine")
        self.assertEqual(GM_UPDATE_STATUS["CHECKING"], "checking")
        self.assertEqual(GM_UPDATE_STATUS["AT0M"], "at0m")

    def test_03_main_window_weakref_cycle_prevention(self):
        """MainWindow setter uses weakref to prevent circular reference memory leaks."""
        class MockMainWindow:
            pass

        mw = MockMainWindow()
        self.gm.main_window = mw
        self.assertIs(self.gm.main_window, mw)

        del mw
        self.assertIsNone(self.gm.main_window)

    def test_04_backward_compatible_module_exports(self):
        """Legacy helpers and symbols are cleanly re-exported from managers.game_manager."""
        import managers.game_manager as gm_mod

        self.assertTrue(hasattr(gm_mod, "get_steam_libraries"))
        self.assertTrue(hasattr(gm_mod, "get_library_index"))
        self.assertTrue(hasattr(gm_mod, "find_steam_install"))
        self.assertTrue(hasattr(gm_mod, "get_user_config_path"))
        self.assertTrue(hasattr(gm_mod, "load_selected_dlcs"))
        self.assertTrue(hasattr(gm_mod, "persist_selected_dlcs"))
        self.assertTrue(hasattr(gm_mod, "UPDATE_STATUS"))
        self.assertTrue(hasattr(gm_mod, "os"))

    def test_05_single_at0m_check_deduplication(self):
        """Duplicate simultaneous AT0-M checks for the same appid are discarded."""
        self.assertFalse(hasattr(self.gm, "_single_at0m_checks") and "12345" in self.gm._single_at0m_checks)

        # Mock game data
        game_data = {"appid": "12345", "is_atom": True, "source": "at0-m", "game_name": "Test AT0M Game"}
        self.gm.games = [game_data]
        self.gm._games_by_appid = {"12345": game_data}

        with patch("threading.Thread") as mock_thread_cls:
            mock_thread_inst = MagicMock()
            mock_thread_cls.return_value = mock_thread_inst

            # First check initiates and spawns thread
            self.gm.check_single_game_update("12345")
            self.assertEqual(mock_thread_cls.call_count, 1)
            self.assertIn("12345", self.gm._single_at0m_checks)

            # Second call while in-flight is discarded
            self.gm.check_single_game_update("12345")
            self.assertEqual(mock_thread_cls.call_count, 1)

            # Once check finishes, discarded from in-flight set
            self.gm._single_at0m_checks.discard("12345")
            self.assertNotIn("12345", self.gm._single_at0m_checks)

    def test_06_scrub_dlc_only_base_appid_validation(self):
        """Non-numeric or empty appids are safely ignored without config access."""
        with patch("utils.yaml_config_manager.get_user_config_path") as mock_get_cfg:
            self.gm._scrub_dlc_only_base_appid("")
            self.gm._scrub_dlc_only_base_appid("unknown")
            self.gm._scrub_dlc_only_base_appid("0")
            self.gm._scrub_dlc_only_base_appid(None)
            self.assertEqual(mock_get_cfg.call_count, 0)

    def test_07_cancel_scan_cleans_pending_and_threads(self):
        """cancel_scan clears pending calculations and resets active flag."""
        self.gm.is_scanning = True
        self.gm._pending_size_calculations = [("123", "/path/to/game")]
        self.gm.cancel_scan()
        self.assertFalse(self.gm.is_scanning)
        self.assertEqual(self.gm._pending_size_calculations, [])


if __name__ == "__main__":
    unittest.main()
