#!/usr/bin/env python3
"""Unit tests for MainWindow modular mixin decomposition."""

import sys
import unittest
from pathlib import Path

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ui.main_window import (
    DIALOG_WAIT_SECONDS,
    FramelessWindow,
    MainWindow,
)
from ui.main_window_mixins import (
    MainWindowSelfUpdateMixin,
    MainWindowSystemStatusMixin,
    MainWindowUpdateAllMixin,
    MainWindowWebBridgeMixin,
)


class TestMainWindowMixins(unittest.TestCase):
    def test_reexports(self):
        self.assertIsInstance(DIALOG_WAIT_SECONDS, int)
        self.assertIsNotNone(FramelessWindow)

    def test_inheritance(self):
        self.assertTrue(issubclass(MainWindow, MainWindowUpdateAllMixin))
        self.assertTrue(issubclass(MainWindow, MainWindowSystemStatusMixin))
        self.assertTrue(issubclass(MainWindow, MainWindowSelfUpdateMixin))
        self.assertTrue(issubclass(MainWindow, MainWindowWebBridgeMixin))

    def test_mixin_method_presence(self):
        # Update All mixin
        self.assertTrue(hasattr(MainWindow, "run_update_all_flow"))
        self.assertTrue(hasattr(MainWindow, "sync_at0m_keys"))
        self.assertTrue(hasattr(MainWindow, "force_check_all_updates"))
        self.assertTrue(hasattr(MainWindow, "position_update_all_btn"))

        # System Status mixin
        self.assertTrue(hasattr(MainWindow, "refresh_system_status"))
        self.assertTrue(hasattr(MainWindow, "refresh_system_status_now"))
        self.assertTrue(hasattr(MainWindow, "_collect_system_status"))
        self.assertTrue(hasattr(MainWindow, "rotate_quote"))
        self.assertTrue(hasattr(MainWindow, "check_steamdb_status"))
        self.assertTrue(hasattr(MainWindow, "refresh_hubcap_stats"))

        # Self Update mixin
        self.assertTrue(hasattr(MainWindow, "check_tool_updates"))
        self.assertTrue(hasattr(MainWindow, "run_self_update"))
        self.assertTrue(hasattr(MainWindow, "_on_update_success"))

        # Web Bridge mixin
        self.assertTrue(hasattr(MainWindow, "_process_web_commands"))
        self.assertTrue(hasattr(MainWindow, "get_web_status_snapshot"))
        self.assertTrue(hasattr(MainWindow, "toggle_web_server"))


if __name__ == "__main__":
    unittest.main()
