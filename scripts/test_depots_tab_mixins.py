#!/usr/bin/env python3
"""Regression test suite for decomposed DepotsTabController and its mixins."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src/ is on PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget

# Ensure QApplication exists for UI tests
_app = QApplication.instance() or QApplication(["-platform", "offscreen"])

from ui.dialogs.game_details.depots_tab import (
    ALL_SHARED_REDISTS,
    DepotsTabController,
    MiniSwitchToggle,
    build_depots_panel,
    init_depots_tab,
)
from ui.dialogs.game_details.depots_tab_mixins import (
    DepotsEnrichmentMixin,
    DepotsStateMixin,
    DepotsSyncMixin,
    DepotsTablesMixin,
    _EnrichmentBridge,
    _QuotaBridge,
    _WorkerBridge,
)


class TestDepotsTabMixins(unittest.TestCase):
    def setUp(self):
        self.mock_dialog = MagicMock()
        self.mock_dialog.appid = "123456"
        self.mock_dialog.game_data = {
            "appid": "123456",
            "name": "Test Game",
            "depots": {"123457": {"name": "Test Depot", "key": "a" * 64}},
            "dlcs": {},
            "keys": {},
        }
        self.mock_dialog.accent_color = "#4CAF50"
        self.mock_dialog.stacked = MagicMock()

    def test_01_composition_and_mro(self):
        """DepotsTabController inherits from QObject and all 4 domain mixins."""
        self.assertTrue(issubclass(DepotsTabController, DepotsStateMixin))
        self.assertTrue(issubclass(DepotsTabController, DepotsTablesMixin))
        self.assertTrue(issubclass(DepotsTabController, DepotsSyncMixin))
        self.assertTrue(issubclass(DepotsTabController, DepotsEnrichmentMixin))

        ctrl = DepotsTabController(self.mock_dialog)
        self.assertIsInstance(ctrl, DepotsTabController)
        self.assertIsInstance(ctrl, QObject)

    def test_02_dialog_weakref_cycle_elimination(self):
        """Dialog property uses weakref to prevent circular references while caching fallback data."""
        class MockDialog(QObject):
            def __init__(self):
                super().__init__()
                self.appid = "9999"
                self.game_data = {"appid": "9999", "name": "Ephemeral"}
                self.accent_color = "#FF0000"

        dlg = MockDialog()
        ctrl = DepotsTabController(dlg)
        self.assertIs(ctrl.dialog, dlg)
        self.assertEqual(ctrl._get_game_data()["appid"], "9999")

        # When dialog is deleted, weakref clears cleanly
        del dlg
        self.assertIsNone(ctrl.dialog)
        # Fallback game data remains available
        self.assertEqual(ctrl._get_game_data()["appid"], "9999")

    def test_03_bridge_signal_emission_guard(self):
        """_emit_bridge_signal safely handles None, destroyed objects, and exceptions."""
        ctrl = DepotsTabController(self.mock_dialog)

        # None bridge does not raise
        ctrl._emit_bridge_signal(None, "finished", True, "ok")

        # Valid bridge emits signal
        class DummyBridge(QObject):
            test_signal = pyqtSignal(str)

        bridge = DummyBridge()
        received = []
        bridge.test_signal.connect(lambda msg: received.append(msg))

        ctrl._emit_bridge_signal(bridge, "test_signal", "hello")
        self.assertEqual(received, ["hello"])

        # When _is_destroyed returns True, emission is blocked
        with patch.object(ctrl, "_is_destroyed", return_value=True):
            ctrl._emit_bridge_signal(bridge, "test_signal", "blocked")
            self.assertEqual(received, ["hello"])

    def test_04_background_tasks_scheduled_in_init(self):
        """self._schedule_background_tasks() is called during __init__ rather than stranded."""
        with patch.object(DepotsTabController, "_schedule_background_tasks") as mock_sched:
            ctrl = DepotsTabController(self.mock_dialog)
            self.assertEqual(mock_sched.call_count, 1)

    def test_05_module_exports_parity(self):
        """All public symbols from original depots_tab are exported cleanly."""
        import ui.dialogs.game_details.depots_tab as dt_mod

        for sym in [
            "init_depots_tab",
            "build_depots_panel",
            "DepotsTabController",
            "MiniSwitchToggle",
            "ALL_SHARED_REDISTS",
            "_WorkerBridge",
            "_EnrichmentBridge",
            "_QuotaBridge",
            "_ToggleContainer",
            "_format_enriched_depot_desc",
            "_make_bulk_toggle_bar",
            "_make_local_indicator",
            "_make_table",
            "_set_table_height",
        ]:
            self.assertTrue(hasattr(dt_mod, sym), f"Missing export {sym}")


if __name__ == "__main__":
    unittest.main()
