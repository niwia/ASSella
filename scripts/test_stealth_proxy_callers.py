#!/usr/bin/env python3
"""
Unit and integration test verifying that EVERY DepotDownloader caller
respects enable_stealth_proxy, passes -proxy <url>, fails closed, and cleans up.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from utils.kuroko import resolve_stealth_proxy_url, test_stealth_proxy_connection


class TestStealthProxyCallers(unittest.TestCase):

    def test_01_dump_runner_injects_proxy_and_cleans_up(self):
        """dump_runner._DumpThread appends -proxy and stops Kuroko manager on completion."""
        from ui.dialogs.depot_selection.dump_runner import _DumpThread

        mock_manager = MagicMock()
        with patch("utils.kuroko.resolve_stealth_proxy_url", return_value=("socks5h://127.0.0.1:11080", mock_manager)):
            with patch("subprocess.run") as mock_sub:
                thread = _DumpThread(["dotnet", "DepotDownloader.dll", "-manifest-only"])
                thread.run()

                # Verify -proxy was appended to command
                called_cmd = mock_sub.call_args[0][0]
                self.assertIn("-proxy", called_cmd)
                self.assertIn("socks5h://127.0.0.1:11080", called_cmd)

                # Verify proxy manager was stopped in finally
                mock_manager.stop.assert_called_once()

    def test_02_dump_runner_fails_closed(self):
        """dump_runner._DumpThread emits failure if proxy setup raises RuntimeError."""
        from ui.dialogs.depot_selection.dump_runner import _DumpThread

        with patch("utils.kuroko.resolve_stealth_proxy_url", side_effect=RuntimeError("WARP failed to bind")):
            with patch("subprocess.run") as mock_sub:
                thread = _DumpThread(["dotnet", "DepotDownloader.dll", "-manifest-only"])
                signal_results = []
                thread.finished_signal.connect(lambda ok, err: signal_results.append((ok, err)))
                thread.run()

                # Subprocess must never be called
                mock_sub.assert_not_called()
                # Signal must emit failure with error message
                self.assertEqual(len(signal_results), 1)
                self.assertFalse(signal_results[0][0])
                self.assertIn("WARP failed to bind", signal_results[0][1])

    def test_03_fileselection_direct_download_injects_proxy_and_cleans_up(self):
        """fileselection.DirectDownloadThread appends -proxy and stops Kuroko manager."""
        from ui.dialogs.fileselection import DirectDownloadThread

        mock_manager = MagicMock()
        mock_proc = MagicMock()
        mock_proc.stdout.readline.side_effect = ["100%", ""]
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0

        with patch("utils.kuroko.resolve_stealth_proxy_url", return_value=("socks5h://127.0.0.1:11080", mock_manager)):
            with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                thread = DirectDownloadThread(["dotnet", "DepotDownloader.dll"])
                thread.run()

                called_cmd = mock_popen.call_args[0][0]
                self.assertIn("-proxy", called_cmd)
                self.assertIn("socks5h://127.0.0.1:11080", called_cmd)
                mock_manager.stop.assert_called_once()

    @patch("core.tasks.download_workshop_task.get_dotnet_path", return_value="/usr/bin/dotnet")
    def test_04_download_workshop_task_injects_proxy_and_cleans_up(self, mock_dotnet):
        """download_workshop_task appends -proxy and cleans up proxy manager in finally."""
        from core.tasks.download_workshop_task import DownloadWorkshopTask

        mock_manager = MagicMock()
        task = DownloadWorkshopTask()

        mock_proc = MagicMock()
        mock_proc.stdout = iter(["100%"])
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0

        with patch("utils.kuroko.resolve_stealth_proxy_url", return_value=("socks5h://127.0.0.1:11080", mock_manager)):
            with patch("core.tasks.download_workshop_task.subprocess.Popen", return_value=mock_proc) as mock_popen:
                with patch.object(task, "fetch_manifest", return_value={"appid": "123", "manifest_id": "456", "depot_key": "abc", "manifest_path": "/tmp/m.manifest"}):
                    with patch.object(task, "key_exists", return_value=True):
                        task.run({
                            "wids": ["9999"],
                            "api_key": "test_key",
                            "max_downloads": 4,
                            "steam_integration": False,
                            "dest_path": "/tmp/dest",
                        })

                        called_cmd = mock_popen.call_args[0][0]
                        self.assertIn("-proxy", called_cmd)
                        self.assertIn("socks5h://127.0.0.1:11080", called_cmd)
                        mock_manager.stop.assert_called_once()
                        self.assertIsNone(task._kuroko_manager)

    @patch("core.tasks.download_workshop_task.get_dotnet_path", return_value="/usr/bin/dotnet")
    def test_05_download_workshop_task_fails_closed(self, mock_dotnet):
        """download_workshop_task emits error signal and halts if proxy setup fails."""
        from core.tasks.download_workshop_task import DownloadWorkshopTask

        task = DownloadWorkshopTask()
        error_results = []
        task.error.connect(lambda err: error_results.append(err))

        with patch("utils.kuroko.resolve_stealth_proxy_url", side_effect=RuntimeError("WireGuard file not found")):
            with patch("core.tasks.download_workshop_task.subprocess.Popen") as mock_popen:
                task.run({
                    "wids": ["9999"],
                    "api_key": "test_key",
                    "max_downloads": 4,
                    "steam_integration": False,
                    "dest_path": "/tmp/dest",
                })

                mock_popen.assert_not_called()
                self.assertEqual(len(error_results), 1)
                self.assertIn("WireGuard file not found", str(error_results[0][1]))


if __name__ == "__main__":
    unittest.main()
