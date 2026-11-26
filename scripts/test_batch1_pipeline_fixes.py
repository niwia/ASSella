"""Regression tests for Batch 1 pipeline, DLC-only, handoff, and cancel fixes."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class TestBatch1PipelineFixes(unittest.TestCase):
    def test_install_via_sls_skips_parent_appid_in_dlc_only_mode(self):
        from utils import slssteam_integration as sls

        with patch.object(sls, "_experimental_mode_enabled", return_value=True), \
             patch("utils.dlc_helpers.is_dlc_only_context", return_value=True), \
             patch.object(sls, "_write_appid_to_config") as mock_write:
            ok = sls.install_via_sls("250900", "Isaac", game_data={"dlc_only": True})
            self.assertTrue(ok)
            mock_write.assert_not_called()

    def test_post_install_skips_accela_markers_and_conversion_for_native_steam(self):
        from core.tasks.native_steam_download_task import NativeSteamDownloadTask
        from managers.finalize.post_install import PostInstallMixin

        class _DummyHost(PostInstallMixin):
            pass

        host = _DummyHost()
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "steamapps" / "common" / "TestGame"
            game_dir.mkdir(parents=True)
            host.download_task = NativeSteamDownloadTask.__new__(NativeSteamDownloadTask)
            host.current_dest_path = tmp
            host.game_data = {"appid": "12345", "game_name": "TestGame", "install_dir": "TestGame"}

            with patch("utils.plugin_games.convert_plugin_game_to_accela") as mock_conv, \
                 patch("utils.dlc_helpers.sync_dlc_only_sls_config") as mock_sync:
                host._seed_ddm_delta_cache()
                host._add_appids_to_slssteam_config()
                self.assertFalse((game_dir / ".assella").exists())
                mock_conv.assert_not_called()
                mock_sync.assert_not_called()

    def test_task_manager_cancel_during_error_calls_job_finished(self):
        from managers.task_manager import TaskManager

        tm = TaskManager.__new__(TaskManager)
        tm.is_processing = True
        tm.is_cancelling = True
        tm._job_failed = False
        tm._error_reported_for_job = False
        tm.main_window = MagicMock()
        tm.job_finished = MagicMock()

        tm._handle_task_error((RuntimeError, RuntimeError("boom"), None))
        self.assertTrue(tm._job_failed)
        tm.job_finished.assert_called_once()


if __name__ == "__main__":
    unittest.main()
