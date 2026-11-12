import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


class TestPackage5Refactor(unittest.TestCase):
    def test_helpers_custom_widgets_export(self):
        """Verify custom UI widgets are properly accessible and typed from helpers and custom_widgets."""
        from ui.widgets.custom_widgets import CheckboxSetting, FontSelectionDialog, Material3Switch
        from utils.helpers import (
            CheckboxSetting as HCheckboxSetting,
            FontSelectionDialog as HFontSelectionDialog,
            Material3Switch as HMaterial3Switch,
        )

        self.assertIs(CheckboxSetting, HCheckboxSetting)
        self.assertIs(FontSelectionDialog, HFontSelectionDialog)
        self.assertIs(Material3Switch, HMaterial3Switch)

    def test_task_manager_dead_code_removed(self):
        """Verify task_manager no longer has DownloadSLSsteamTask references or attributes."""
        import managers.task_manager as tm

        self.assertFalse(hasattr(tm, "DownloadSLSsteamTask"))
        with open(tm.__file__, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("DownloadSLSsteamTask", src)
        self.assertNotIn("slssteam_download_task", src)
        self.assertNotIn("slssteam_download_runner", src)

    def test_job_queue_manager_workshop_resolution(self):
        """Verify workshop title resolution slot updates job display names without errors."""
        from PyQt6.QtCore import QObject
        from managers.job_queue_manager import JobQueueManager

        class DummyWindow(QObject):
            def __init__(self):
                super().__init__()
                self.task_manager = MagicMock()
                self.task_manager.is_processing = False
                self.game_manager = None
                self.ui_state = MagicMock()

        mock_window = DummyWindow()
        jqm = JobQueueManager(mock_window)

        # Enqueue a dummy job
        jqm.job_queue = [
            {
                "id": "job-123",
                "type": "workshop",
                "metadata": {"game_name": "Workshop Mod (1 items)"},
                "workshop_data": {"display_name": "Workshop Mod (1 items)"},
            }
        ]

        # Call the slot
        jqm._on_workshop_title_resolved("job-123", "Cool Weapon Pack", "12345", 1)

        self.assertEqual(jqm.job_queue[0]["metadata"]["game_name"], "Workshop: Cool Weapon Pack")
        self.assertEqual(jqm.job_queue[0]["workshop_data"]["display_name"], "Workshop: Cool Weapon Pack")


if __name__ == "__main__":
    unittest.main()
