import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


class TestPackage5Refactor(unittest.TestCase):
    def test_helpers_lazy_qt_import(self):
        """Verify importing helpers does not eagerly load PyQt6.QtWidgets if not loaded."""
        # Check if PyQt6.QtWidgets is already in sys.modules, or test with clean subprocess
        cmd = [
            sys.executable,
            "-c",
            (
                "import sys; "
                "sys.path.insert(0, 'src'); "
                "import utils.helpers as h; "
                "assert 'PyQt6.QtWidgets' not in sys.modules, 'PyQt6.QtWidgets was loaded eagerly!';"
                "from utils.helpers import Material3Switch, CheckboxSetting, FontSelectionDialog; "
                "assert Material3Switch is not None; "
                "assert 'PyQt6.QtWidgets' in sys.modules, 'PyQt6.QtWidgets should be loaded lazily!';"
            ),
        ]
        import subprocess

        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Subprocess failed:\n{res.stderr}")

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
