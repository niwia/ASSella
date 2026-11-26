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


class _FakeDump:
    """Minimal ManifestDump stand-in for _DumpThread (command/txt_path/keys_path)."""

    def __init__(self, txt_path="", keys_path=""):
        self.txt_path = txt_path
        self.keys_path = keys_path
        self.app_id = "730"
        self.depot_id = "731"
        self.manifest_id = "1"
        self.manifest_file = None

    def command(self, dotnet, dll):
        return [dotnet, dll, "-manifest-only"]


class TestStealthProxyCallers(unittest.TestCase):

    def test_01_dump_runner_injects_proxy_and_cleans_up(self):
        """dump_runner._DumpThread appends -proxy and stops Kuroko manager on completion."""
        import tempfile

        from ui.dialogs.depot_selection.dump_runner import _DumpThread

        with tempfile.TemporaryDirectory() as tmp:
            txt = os.path.join(tmp, "listing.txt")
            with open(txt, "w", encoding="utf-8") as f:
                f.write("listing")

            mock_manager = MagicMock()
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = ("", "")
            results = []

            with patch("utils.kuroko.resolve_stealth_proxy_url", return_value=("socks5h://127.0.0.1:11080", mock_manager)), \
                 patch("ui.dialogs.depot_selection.dump_runner.subprocess.Popen", return_value=mock_proc) as mock_popen, \
                 patch("utils.helpers.get_dotnet_path", return_value="/usr/bin/dotnet"), \
                 patch("utils.helpers.resource_path", return_value="/tmp/DepotDownloader.dll"), \
                 patch("utils.helpers.get_dotnet_env", return_value={}):
                thread = _DumpThread(dump=_FakeDump(txt, os.path.join(tmp, "k.keys")))
                thread.finished_signal.connect(lambda ok, err, d: results.append((ok, err)))
                thread.run()

                called_cmd = mock_popen.call_args[0][0]
                self.assertIn("-proxy", called_cmd)
                self.assertIn("socks5h://127.0.0.1:11080", called_cmd)
                mock_manager.stop.assert_called_once()
                self.assertTrue(results[0][0])

    def test_02_dump_runner_proceeds_direct_on_proxy_error(self):
        """dump_runner._DumpThread proceeds without -proxy if stealth setup fails (fail-open, unlike downloads)."""
        import tempfile

        from ui.dialogs.depot_selection.dump_runner import _DumpThread

        with tempfile.TemporaryDirectory() as tmp:
            txt = os.path.join(tmp, "listing.txt")
            with open(txt, "w", encoding="utf-8") as f:
                f.write("listing")

            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = ("", "")
            results = []

            with patch("utils.kuroko.resolve_stealth_proxy_url", side_effect=RuntimeError("WARP failed to bind")), \
                 patch("ui.dialogs.depot_selection.dump_runner.subprocess.Popen", return_value=mock_proc) as mock_popen, \
                 patch("utils.helpers.get_dotnet_path", return_value="/usr/bin/dotnet"), \
                 patch("utils.helpers.resource_path", return_value="/tmp/DepotDownloader.dll"), \
                 patch("utils.helpers.get_dotnet_env", return_value={}):
                thread = _DumpThread(dump=_FakeDump(txt, os.path.join(tmp, "k.keys")))
                thread.finished_signal.connect(lambda ok, err, d: results.append((ok, err)))
                thread.run()

                mock_popen.assert_called_once()
                self.assertNotIn("-proxy", mock_popen.call_args[0][0])
                self.assertTrue(results[0][0])

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

    def test_06_dump_runner_cancel_terminates_and_emits_blank(self):
        """Cancelling the dump terminates DepotDownloader and emits a blank (no error) result."""
        import tempfile

        from ui.dialogs.depot_selection.dump_runner import _DumpThread

        with tempfile.TemporaryDirectory() as tmp:
            mock_proc = MagicMock()
            mock_proc.returncode = -15
            mock_proc.communicate.return_value = ("", "")
            results = []

            with patch("utils.kuroko.resolve_stealth_proxy_url", return_value=(None, None)), \
                 patch("ui.dialogs.depot_selection.dump_runner.subprocess.Popen", return_value=mock_proc), \
                 patch("ui.dialogs.depot_selection.dump_runner.discard_keys_file"), \
                 patch("utils.helpers.get_dotnet_path", return_value="/usr/bin/dotnet"), \
                 patch("utils.helpers.resource_path", return_value="/tmp/DepotDownloader.dll"), \
                 patch("utils.helpers.get_dotnet_env", return_value={}):
                thread = _DumpThread(dump=_FakeDump(os.path.join(tmp, "listing.txt"), os.path.join(tmp, "k.keys")))
                thread._cancel_event.set()
                thread.finished_signal.connect(lambda ok, err, d: results.append((ok, err)))
                thread.run()

                mock_proc.terminate.assert_called_once()
                self.assertFalse(results[0][0])
                self.assertEqual(results[0][1], "")


if __name__ == "__main__":
    unittest.main()
