#!/usr/bin/env python3
"""Tests for depots_tab _collect_state resilience to different game_data formats."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


class TestDepotsTabState(unittest.TestCase):
    def test_init_depots_tab_handles_list_depots(self):
        """When game_data['depots'] is a list (e.g. after refetch), it must not crash with AttributeError."""
        from PyQt6.QtWidgets import QApplication, QTabWidget, QWidget
        from ui.dialogs.game_details.depots_tab import init_depots_tab

        _app = QApplication.instance() or QApplication(["-platform", "offscreen"])

        dialog = MagicMock()
        dialog.appid = 1722870
        dialog.game_data = {
            "appid": "1722870",
            "name": "Clank!",
            "game_name": "Clank!",
            "depots": ["1722871", "1722872"],
            "dlcs": ["1722875"],
            "keys": {"1722871": "a" * 64},
        }
        dialog.accent_color = "#CE93D8"
        dialog.background_color = "#1E1E1E"
        dialog.depots_tab = QWidget()
        dialog.tab_widget = QTabWidget()

        # Must not raise AttributeError: 'list' object has no attribute 'items'
        init_depots_tab(dialog)

    def test_init_depots_tab_handles_dict_depots(self):
        """When game_data['depots'] is a dict (standard mode), it functions normally."""
        from PyQt6.QtWidgets import QApplication, QTabWidget, QWidget
        from ui.dialogs.game_details.depots_tab import init_depots_tab

        _app = QApplication.instance() or QApplication(["-platform", "offscreen"])

        dialog = MagicMock()
        dialog.appid = 1722870
        dialog.game_data = {
            "appid": "1722870",
            "name": "Clank!",
            "game_name": "Clank!",
            "depots": {"1722871": {"name": "Depot 1", "key": "b" * 64}},
            "dlcs": {"1722875": "DLC 1"},
            "keys": {},
        }
        dialog.accent_color = "#CE93D8"
        dialog.background_color = "#1E1E1E"
        dialog.depots_tab = QWidget()
        dialog.tab_widget = QTabWidget()

        init_depots_tab(dialog)


if __name__ == "__main__":
    unittest.main()
