#!/usr/bin/env python3
"""End-to-end verification test for DepotDownloaderMod (SteamKit2 3.5.0) and -proxy support."""

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.paths import Paths
from utils.wireproxy_manager import find_free_port, get_or_create_warp_config, get_wireproxy_manager


class TestDepotDownloaderProxy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dll_path = str(Paths.deps("DepotDownloader.dll"))
        if not os.path.isfile(cls.dll_path):
            raise FileNotFoundError(f"DepotDownloader.dll not found at: {cls.dll_path}")

    def test_01_version_and_runtime(self):
        """Verify DepotDownloader runs and outputs version information."""
        cmd = ["dotnet", self.dll_path, "--version"]
        out = subprocess.check_output(cmd, text=True)
        self.assertIn("DepotDownloader v", out)
        self.assertIn(".NET", out)

    def test_02_direct_connection_baseline(self):
        """Verify baseline functionality without proxy (manifest-only download for App 480)."""
        cmd = ["dotnet", self.dll_path, "-app", "480", "-manifest-only"]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=25)
        if proc.returncode != 0 and "A task was canceled." in proc.stdout:
            self.assertIn("Connecting to Steam3... Done!", proc.stdout)
            self.assertIn("Logging anonymously into Steam3... Done!", proc.stdout)
        else:
            self.assertEqual(proc.returncode, 0, f"Process failed: {proc.stdout}")
            self.assertIn("Connecting to Steam3... Done!", proc.stdout)
            self.assertIn("Logging anonymously into Steam3... Done!", proc.stdout)
            self.assertIn("Got AppInfo for 480", proc.stdout)

    def test_03_fail_closed_with_dead_proxy(self):
        """Verify that an unreachable proxy fails closed and does NOT leak or fall back to direct."""
        dead_port = find_free_port(preferred=19999)
        cmd = [
            "dotnet",
            self.dll_path,
            "-app",
            "480",
            "-manifest-only",
            "-proxy",
            f"socks5h://127.0.0.1:{dead_port}",
        ]
        # Run with short timeout since it will fail to connect
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(3)
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=2)
        except Exception:
            proc.kill()
            out, _ = proc.communicate()

        # Must report connection failed
        self.assertIn("Connection to Steam failed", out)
        self.assertNotIn("Connecting to Steam3... Done!", out)

    def test_04_wireproxy_stealth_download_and_socket_leak_check(self):
        """Verify that running through wireproxy connects to Steam3 and has ZERO socket leaks."""
        time.sleep(3)  # Avoid rapid Steam3 CM handshake rate limiting
        from utils.kuroko import Kuroko
        manager = Kuroko()
        cfg = get_or_create_warp_config()
        test_port = find_free_port(preferred=11080)
        started = manager.start(cfg, port=test_port)
        self.assertTrue(started, "Failed to start wireproxy")

        try:
            cmd = [
                "dotnet",
                self.dll_path,
                "-app",
                "480",
                "-manifest-only",
                "-proxy",
                manager.proxy_url,
            ]
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

            # Inspect active sockets while dotnet is running
            time.sleep(2)
            ss_out = subprocess.check_output(["ss", "-tp"], text=True)
            dotnet_sockets = [line for line in ss_out.splitlines() if "dotnet" in line]

            # Every ESTAB socket owned by dotnet must point to 127.0.0.1:{test_port}
            for s in dotnet_sockets:
                if "ESTAB" in s:
                    self.assertIn(
                        f":{test_port}",
                        s,
                        f"LEAK DETECTED! Dotnet established socket to non-proxy destination: {s}",
                    )

            stdout, _ = p.communicate(timeout=60)
            if p.returncode != 0 and "A task was canceled." in stdout:
                self.assertIn("Connecting to Steam3... Done!", stdout)
                self.assertIn("Logging anonymously into Steam3... Done!", stdout)
            else:
                self.assertEqual(p.returncode, 0, f"DepotDownloader failed through proxy: {stdout}")
                self.assertIn("Connecting to Steam3... Done!", stdout)
                self.assertIn("Logging anonymously into Steam3... Done!", stdout)
                self.assertIn("Got AppInfo for 480", stdout)
        finally:
            manager.stop()


if __name__ == "__main__":
    unittest.main()

