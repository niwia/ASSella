#!/usr/bin/env python3
"""Unit tests for retry, failover, and cleanup control-flow in DownloadDepotsTask."""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import core.tasks.download_depots_task as ddt
from core.tasks.download_depots_task import DownloadDepotsTask


class TestDownloadDepotsTaskRetries(unittest.TestCase):
    def setUp(self):
        self.task = DownloadDepotsTask()

    def tearDown(self):
        self.task._cleanup_stealth_proxy()
        self.task._cleanup_temp_files()

    def test_01_execute_depot_cmd_resets_flags(self):
        """_execute_depot_cmd must reset per-attempt failure flags before spawning."""
        self.task.last_percentage = 50
        self.task._is_validating = True
        self.task._lancache_error_detected = True
        self.task._current_depot_key_denied = True
        self.task._current_depot_error_reason = "Stale error"
        self.task._current_depot_zero_downloaded = True
        self.task._current_depot_network_error = "Stale net err"
        self.task._current_depot_downloaded_bytes = 1000
        self.task._current_depot_uncompressed_bytes = 2000
        self.task._current_depot_errors = 5
        self.task._dl_phase_start = 123.0
        self.task._dl_phase_end = 456.0

        mock_proc = MagicMock()
        mock_proc.stdout = None
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc):
            with patch.object(self.task, "_read_process_output"):
                rc = self.task._execute_depot_cmd(["cmd"], 0, "12345")

        self.assertEqual(rc, 0)
        self.assertFalse(self.task._current_depot_key_denied)
        self.assertFalse(self.task._current_depot_zero_downloaded)
        self.assertEqual(self.task._current_depot_error_reason, "")
        self.assertEqual(self.task._current_depot_network_error, "")
        self.assertEqual(self.task._current_depot_downloaded_bytes, 0)
        self.assertEqual(self.task._current_depot_errors, 0)

    @patch("core.morrenus_api.generate_single_manifest", return_value=(b"dummy", None))
    @patch("utils.manifest_resolver.is_valid_manifest", return_value=True)
    def test_02_keys_path_created_with_0600_mode(self, mock_is_valid, mock_gen):
        """mistwalker_keys.vdf must be created owner-only (0o600) via os.open."""
        temp_dir = tempfile.gettempdir()
        keys_path = os.path.join(temp_dir, "mistwalker_keys.vdf")
        if os.path.exists(keys_path):
            os.remove(keys_path)

        game_data = {
            "appid": "12345",
            "branch": "public",
            "manifests": {"12346": "99999"},
            "depots": {"12346": {"size": "1000", "key": "00" * 32}},
        }
        mf_dir = os.path.join(temp_dir, "mistwalker_manifests")
        os.makedirs(mf_dir, exist_ok=True)
        mf_path = os.path.join(mf_dir, "12346_99999.manifest")
        with open(mf_path, "wb") as f:
            f.write(b"\x00" * 64)

        try:
            self.task._prepare_downloads(game_data, ["12346"], "/tmp")
            self.assertTrue(os.path.exists(keys_path))
            file_mode = stat.S_IMODE(os.stat(keys_path).st_mode)
            # In Linux, 0o600 means owner read/write only
            self.assertEqual(file_mode & 0o777, 0o600)
        finally:
            if os.path.exists(keys_path):
                os.remove(keys_path)
            if os.path.exists(mf_path):
                os.remove(mf_path)

    def test_03_temp_file_lists_cleanup(self):
        """_cleanup_temp_files must clean all selective filelists in self.temp_file_lists."""
        temp_dir = tempfile.gettempdir()
        f1 = os.path.join(temp_dir, "selective_filelist_depot1.txt")
        f2 = os.path.join(temp_dir, "selective_filelist_depot2.txt")
        with open(f1, "w") as f:
            f.write("file1\n")
        with open(f2, "w") as f:
            f.write("file2\n")

        self.task.temp_file_lists = [f1, f2]
        self.task.temp_file_list = f2

        self.task._cleanup_temp_files()

        self.assertFalse(os.path.exists(f1))
        self.assertFalse(os.path.exists(f2))
        self.assertEqual(self.task.temp_file_lists, [])
        self.assertIsNone(self.task.temp_file_list)

    def test_04_stealth_failover_succeeds_skips_wudrm_and_runs_success_block(self):
        """When stealth connection drops and failover succeeds:
        - _execute_depot_cmd is re-called with new proxy
        - wudrm recovery is NOT executed
        - completed_so_far_for_this_job is updated with depot size
        """
        mock_settings = MagicMock()
        mock_settings.value.side_effect = lambda key, default=None, **kwargs: {
            "enable_stealth_proxy": True,
            "stealth_rotation_enabled": True,
            "stealth_proxy_mode": "warp",
        }.get(key, default)

        temp_dir = tempfile.gettempdir()
        depot_id = "100"
        manifest_id = "200"
        mf_dir = os.path.join(temp_dir, "mistwalker_manifests")
        os.makedirs(mf_dir, exist_ok=True)
        mf_path = os.path.join(mf_dir, f"{depot_id}_{manifest_id}.manifest")
        with open(mf_path, "wb") as f:
            f.write(b"mock_manifest_bytes")

        dest_dir = tempfile.mkdtemp()
        self.task.download_dir = dest_dir

        cmd = [
            "dotnet", "DepotDownloaderMod.dll",
            "-app", "10",
            "-depot", depot_id,
            "-manifest", manifest_id,
            "-manifestfile", mf_path,
            "-proxy", "socks5://127.0.0.1:1000",
        ]

        attempt = 0
        def fake_exec(cmd_arg, flags, d_id):
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                # First attempt fails with stealth network drop
                self.task._current_depot_network_error = "Connection reset by peer"
                return 1
            else:
                # Retry attempt succeeds
                self.task._current_depot_downloaded_bytes = 5000
                return 0

        self.task._execute_depot_cmd = MagicMock(side_effect=fake_exec)

        mock_kuroko_mgr = MagicMock()
        mock_kuroko_mgr.proxy_url = "socks5://127.0.0.1:2000"
        mock_kuroko_mgr.endpoint_id = "ep_1"
        mock_kuroko_mgr.is_running = True

        with patch.object(ddt, "get_settings", return_value=mock_settings):
            with patch("utils.kuroko.resolve_stealth_proxy_url", return_value=("socks5://127.0.0.1:2000", mock_kuroko_mgr)):
                with patch("core.morrenus_api.generate_single_manifest") as mock_wudrm:
                    with patch("utils.helpers.ensure_dotnet_availability", return_value=True):
                        with patch.object(self.task, "_prepare_downloads", return_value=([cmd], [], [5000])):
                            self.task.run(
                                game_data={"appid": "10", "branch": "public", "depots": {depot_id: {"size": 5000}}},
                                selected_depots=[depot_id],
                                dest_path=dest_dir,
                            )

        # 1. _execute_depot_cmd called twice (initial + failover)
        self.assertEqual(self.task._execute_depot_cmd.call_count, 2)
        # 2. wudrm was NOT called because failover succeeded
        mock_wudrm.assert_not_called()
        # 3. Post-depot success block ran: completed_so_far_for_this_job tracked the 5000 bytes
        self.assertEqual(self.task.completed_so_far_for_this_job, 5000)
        # 4. Sidecar .sha was written
        sidecar_sha = os.path.join(dest_dir, ".DepotDownloader", f"{depot_id}_{manifest_id}.manifest.sha")
        self.assertTrue(os.path.exists(sidecar_sha))

        import shutil
        shutil.rmtree(dest_dir, ignore_errors=True)
        if os.path.exists(mf_path):
            os.remove(mf_path)

    def test_05_key_denied_aborts_without_wudrm_or_stealth(self):
        """When a depot fails due to key denial, it must abort immediately without trying wudrm or stealth failover."""
        mock_settings = MagicMock()
        mock_settings.value.side_effect = lambda key, default=None, **kwargs: {
            "enable_stealth_proxy": False,
        }.get(key, default)

        temp_dir = tempfile.gettempdir()
        depot_id = "101"
        manifest_id = "201"
        mf_dir = os.path.join(temp_dir, "mistwalker_manifests")
        os.makedirs(mf_dir, exist_ok=True)
        mf_path = os.path.join(mf_dir, f"{depot_id}_{manifest_id}.manifest")
        with open(mf_path, "wb") as f:
            f.write(b"mock_bytes")

        cmd = [
            "dotnet", "DepotDownloaderMod.dll",
            "-app", "10",
            "-depot", depot_id,
            "-manifest", manifest_id,
            "-manifestfile", mf_path,
        ]

        def fake_exec(cmd_arg, flags, d_id):
            self.task._current_depot_key_denied = True
            self.task._current_depot_error_reason = "AccessDenied depot key"
            return 1

        self.task._execute_depot_cmd = MagicMock(side_effect=fake_exec)
        error_emitted = None
        def on_error(err):
            nonlocal error_emitted
            error_emitted = err
        self.task.error.connect(on_error)

        with patch.object(ddt, "get_settings", return_value=mock_settings):
            with patch("core.morrenus_api.generate_single_manifest") as mock_wudrm:
                with patch("utils.kuroko.resolve_stealth_proxy_url", return_value=(None, None)) as mock_failover:
                    with patch("utils.helpers.ensure_dotnet_availability", return_value=True):
                        with patch.object(self.task, "_prepare_downloads", return_value=([cmd], [], [1000])):
                            self.task.run(
                                game_data={"appid": "10", "branch": "public", "depots": {depot_id: {"size": 1000}}},
                                selected_depots=[depot_id],
                                dest_path="/tmp",
                            )

        self.assertEqual(self.task._execute_depot_cmd.call_count, 1)
        mock_wudrm.assert_not_called()
        # resolve_stealth_proxy_url is called once at task startup, but never for failover on key denial
        mock_failover.assert_called_once_with(mock_settings, failover_from=None)
        self.assertIsNotNone(error_emitted)
        self.assertIn("No valid decryption key", str(error_emitted[1]))

        if os.path.exists(mf_path):
            os.remove(mf_path)


if __name__ == "__main__":
    unittest.main()
