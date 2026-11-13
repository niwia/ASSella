import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import QObject

# Ensure src is on python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class DummyTask(PostInstallMixin := __import__("managers.finalize.post_install", fromlist=["PostInstallMixin"]).PostInstallMixin):
    def __init__(self, main_window, game_data):
        self.main_window = main_window
        self.game_data = game_data
        self.download_dir = "/tmp/test"
        self.is_processing = True
        self.is_cancelling = False
        self._current_active_step = None
        self._job_steps_completed = set()
        self.slssteam_mode_was_active = False
        self.library_mode_was_active = False
        self.settings = MagicMock()
        self.settings.value.return_value = False
        self.current_job_metadata = {}
        self.current_task_info = {}
        self.task_type = "install"

    def job_finished(self):
        pass


class TestRescanLibrarySetting(unittest.TestCase):
    def test_post_install_skips_rescan_when_disabled(self):
        mock_main_window = MagicMock()
        mock_main_window.game_manager = MagicMock()
        mock_main_window.job_queue = MagicMock()
        mock_main_window.job_queue_manager = None
        mock_main_window.task_manager = MagicMock()
        mock_main_window._details_dialog = None

        job = DummyTask(
            main_window=mock_main_window,
            game_data={"appid": "12345", "game_name": "Test Game", "buildid": "100"},
        )

        with patch("utils.settings.get_settings") as mock_get_settings, \
             patch.object(job, "job_finished"), \
             patch.object(job, "_apply_install_update_status"), \
             patch.object(job, "_compute_final_buildid", return_value="100"), \
             patch.object(job, "_apply_install_buildid_and_pin"), \
             patch("managers.db_manager.DatabaseManager"):

            mock_settings = MagicMock()
            mock_settings.value.side_effect = lambda key, default=None, type=None: False if key == "rescan_library_after_download" else default
            mock_get_settings.return_value = mock_settings

            job._finalize_job_logic()

            # scan_steam_libraries_async must NOT be called!
            mock_main_window.game_manager.scan_steam_libraries_async.assert_not_called()
            # But the game should be registered/updated in memory in game_manager
            self.assertTrue(
                mock_main_window.game_manager.update_game.called or mock_main_window.game_manager.add_game.called
            )

    def test_post_install_runs_rescan_when_enabled(self):
        mock_main_window = MagicMock()
        mock_main_window.game_manager = MagicMock()
        mock_main_window.job_queue = MagicMock()
        mock_main_window.job_queue_manager = None
        mock_main_window.task_manager = MagicMock()
        mock_main_window._details_dialog = None

        job = DummyTask(
            main_window=mock_main_window,
            game_data={"appid": "12345", "game_name": "Test Game", "buildid": "100"},
        )

        with patch("utils.settings.get_settings") as mock_get_settings, \
             patch.object(job, "job_finished"), \
             patch.object(job, "_apply_install_update_status"), \
             patch.object(job, "_compute_final_buildid", return_value="100"), \
             patch.object(job, "_apply_install_buildid_and_pin"), \
             patch("managers.db_manager.DatabaseManager"):

            mock_settings = MagicMock()
            mock_settings.value.side_effect = lambda key, default=None, type=None: True if key == "rescan_library_after_download" else default
            mock_get_settings.return_value = mock_settings

            job._finalize_job_logic()

            # scan_steam_libraries_async MUST be called
            mock_main_window.game_manager.scan_steam_libraries_async.assert_called_once()

    def test_job_queue_manager_respects_setting(self):
        from managers.job_queue_manager import JobQueueManager

        parent_obj = QObject()
        mgr = JobQueueManager(main_window=parent_obj)
        mock_mw = MagicMock()
        mock_mw.game_manager = MagicMock()
        mgr.main_window = mock_mw
        mgr.jobs_completed_count = 1

        with patch("utils.settings.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.value.side_effect = lambda key, default=None, type=None: False if key == "rescan_library_after_download" else default
            mock_get_settings.return_value = mock_settings

            mgr._handle_queue_completion()
            mock_mw.game_manager.scan_steam_libraries_async.assert_not_called()

            # When enabled
            mock_settings.value.side_effect = lambda key, default=None, type=None: True if key == "rescan_library_after_download" else default
            mgr.jobs_completed_count = 1
            mgr._handle_queue_completion()
            mock_mw.game_manager.scan_steam_libraries_async.assert_called_once()


if __name__ == "__main__":
    unittest.main()
