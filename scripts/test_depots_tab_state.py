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

    def test_init_depots_tab_renders_missing_pending_depots_as_unclickable(self):
        """Missing or key-pending depots must render with status tags and strictly unclickable toggles."""
        from PyQt6.QtWidgets import QApplication, QTabWidget, QWidget, QStackedWidget, QTableWidget
        from ui.dialogs.game_details.depots_tab import init_depots_tab, MiniSwitchToggle
        from unittest.mock import patch

        _app = QApplication.instance() or QApplication(["-platform", "offscreen"])

        dialog = MagicMock()
        dialog.appid = 2954010
        dialog.game_data = {
            "appid": "2954010",
            "name": "Burn With Me",
            "game_name": "Burn With Me",
            "depots": {"2954011": {"name": "Depot 2954011", "key": "a" * 64}},
            "dlcs": {},
            "keys": {"2954011": "a" * 64},
        }
        dialog.accent_color = "#4DD0E1"
        dialog.background_color = "#1E1E1E"
        dialog.depots_tab = QWidget()
        dialog.tab_widget = QTabWidget()
        dialog.stacked = QStackedWidget()

        mock_stat = {
            "appid": "2954010",
            "status": "key_pending",
            "new_depots": [{"id": "2954012", "name": "Depot 2954012", "has_key": False}],
        }

        with patch("managers.db_manager.DatabaseManager.get_at0m_update_status", return_value=mock_stat):
            init_depots_tab(dialog)

        tables = dialog.stacked.findChildren(QTableWidget)
        self.assertGreaterEqual(len(tables), 3)
        depots_table = tables[1]
        keys_table = tables[2]

        # Verify missing depot 2954012 is present in depots_table
        found_depot_row = None
        for r in range(depots_table.rowCount()):
            if depots_table.item(r, 0) and depots_table.item(r, 0).text() == "2954012":
                found_depot_row = r
                break
        self.assertIsNotNone(found_depot_row, "Missing depot 2954012 should appear in Depots table")

        # Verify status tag and unclickable toggle
        desc_item = depots_table.item(found_depot_row, 1)
        self.assertIn("[Awaiting Key]", desc_item.text())

        cell_widget = depots_table.cellWidget(found_depot_row, 2)
        toggle = cell_widget.findChild(MiniSwitchToggle)
        self.assertFalse(toggle.isChecked())
        self.assertTrue(toggle.isReadOnly())
        self.assertFalse(toggle.isEnabled())

        # Verify missing key 2954012 is present in keys_table
        found_key_row = None
        for r in range(keys_table.rowCount()):
            if keys_table.item(r, 0) and keys_table.item(r, 0).text() == "2954012":
                found_key_row = r
                break
        self.assertIsNotNone(found_key_row, "Missing key 2954012 should appear in Keys table")

        key_col_item = keys_table.item(found_key_row, 1)
        self.assertIn("[Awaiting Key]", key_col_item.text())

        key_cell = keys_table.cellWidget(found_key_row, 2)
        key_toggle = key_cell.findChild(MiniSwitchToggle)
        self.assertFalse(key_toggle.isChecked())
        self.assertTrue(key_toggle.isReadOnly())
        self.assertFalse(key_toggle.isEnabled())


if __name__ == "__main__":
    unittest.main()
