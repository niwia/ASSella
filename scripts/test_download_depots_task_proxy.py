#!/usr/bin/env python3
"""Unit tests for stealth proxy wiring in DownloadDepotsTask."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.tasks.download_depots_task import DownloadDepotsTask


class TestDownloadDepotsTaskProxy(unittest.TestCase):
    def setUp(self):
        self.task = DownloadDepotsTask()

    def tearDown(self):
        self.task._cleanup_stealth_proxy()

    def test_01_proxy_disabled_returns_none(self):
        """When enable_stealth_proxy is False, _setup_stealth_proxy returns None."""
        mock_settings = MagicMock()
        mock_settings.value.side_effect = lambda key, default=None, type=None: {
            "enable_stealth_proxy": False
        }.get(key, default)

        proxy_url = self.task._setup_stealth_proxy(mock_settings)
        self.assertIsNone(proxy_url)
        self.assertIsNone(self.task._wireproxy_manager)

    @patch("utils.kuroko.Kuroko.probe_alive", return_value=True)
    def test_02_custom_proxy_mode(self, mock_probe):
        """When mode is custom, returns the configured proxy URL without spawning wireproxy."""
        mock_settings = MagicMock()
        mock_settings.value.side_effect = lambda key, default=None, type=None: {
            "enable_stealth_proxy": True,
            "stealth_proxy_mode": "custom",
            "stealth_proxy_custom_url": "socks5://127.0.0.1:40000",
        }.get(key, default)

        proxy_url = self.task._setup_stealth_proxy(mock_settings)
        self.assertEqual(proxy_url, "socks5://127.0.0.1:40000")
        self.assertIsNone(self.task._wireproxy_manager)

    def test_03_wireguard_mode_fails_closed_on_missing_file(self):
        """When mode is wireguard and file does not exist, fails closed with RuntimeError."""
        mock_settings = MagicMock()
        mock_settings.value.side_effect = lambda key, default=None, type=None: {
            "enable_stealth_proxy": True,
            "stealth_proxy_mode": "wireguard",
            "stealth_proxy_wg_path": "/nonexistent/path/vpn.conf",
        }.get(key, default)

        with self.assertRaises(RuntimeError) as ctx:
            self.task._setup_stealth_proxy(mock_settings)
        self.assertIn("WireGuard config file not found", str(ctx.exception))
        self.assertIsNone(self.task._wireproxy_manager)

    def test_04_prepare_downloads_injects_proxy_and_suppresses_lancache(self):
        """Verify that _prepare_downloads appends -proxy and omits -use-lancache when proxy_url is set."""
        import tempfile, shutil
        manifest_dir = os.path.join(tempfile.gettempdir(), "mistwalker_manifests")
        os.makedirs(manifest_dir, exist_ok=True)

        depot_id = "1794684"
        manifest_id = "5505101475645329500"
        src_manifest = os.path.expanduser("~/.local/share/ACCELA/manifests/1794684_5505101475645329500.manifest")
        dest_manifest = os.path.join(manifest_dir, f"{depot_id}_{manifest_id}.manifest")
        if os.path.exists(src_manifest):
            shutil.copy2(src_manifest, dest_manifest)

        game_data = {
            "appid": "1794680",
            "branch": "public",
            "manifests": {depot_id: manifest_id},
            "depots": {depot_id: {"size": "1000", "key": "00" * 32}},
        }
        dest_path = "/tmp"

        from utils.settings import get_settings
        settings = get_settings()
        settings.setValue("use_lancache", True)

        # 1. Without proxy: -use-lancache should be present
        commands_no_proxy, _, _ = self.task._prepare_downloads(
            game_data, [depot_id], dest_path, proxy_url=None
        )
        self.assertTrue(len(commands_no_proxy) > 0)
        self.assertIn("-use-lancache", commands_no_proxy[0])
        self.assertNotIn("-proxy", commands_no_proxy[0])

        # 2. With proxy: -proxy should be present, and -use-lancache MUST be suppressed
        commands_with_proxy, _, _ = self.task._prepare_downloads(
            game_data, [depot_id], dest_path, proxy_url="socks5://127.0.0.1:11080"
        )
        self.assertTrue(len(commands_with_proxy) > 0)
        cmd = commands_with_proxy[0]
        self.assertIn("-proxy", cmd)
        proxy_idx = cmd.index("-proxy")
        self.assertEqual(cmd[proxy_idx + 1], "socks5://127.0.0.1:11080")
        self.assertNotIn("-use-lancache", cmd)

    def test_05_warp_proxy_mode_lifecycle(self):
        """When mode is warp, starts wireproxy, sets _wireproxy_started=True, and stops on cleanup."""
        mock_settings = MagicMock()
        mock_settings.value.side_effect = lambda key, default=None, type=None: {
            "enable_stealth_proxy": True,
            "stealth_proxy_mode": "warp",
        }.get(key, default)

        proxy_url = self.task._setup_stealth_proxy(mock_settings)
        self.assertIsNotNone(proxy_url)
        self.assertTrue(proxy_url.startswith("socks5h://"))
        self.assertIsNotNone(self.task._wireproxy_manager)
        self.assertTrue(self.task._wireproxy_manager.is_running)

        # Cleanup should cleanly stop it
        self.task._cleanup_stealth_proxy()
        self.assertIsNone(self.task._wireproxy_manager)


if __name__ == "__main__":
    unittest.main()
