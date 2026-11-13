#!/usr/bin/env python3
"""Integration tests for WireproxyManager and Cloudflare WARP key generation."""

import os
import sys
import unittest
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.wireproxy_manager import (
    _generate_x25519_keypair,
    assemble_wireproxy_config,
    find_free_port,
    get_or_create_warp_config,
    get_wireproxy_manager,
    WireproxyManager,
)


class TestWireproxyManager(unittest.TestCase):
    def test_01_x25519_keypair(self):
        """Verify Curve25519 key generation produces valid 44-char base64 keys."""
        priv, pub = _generate_x25519_keypair()
        self.assertEqual(len(priv), 44)
        self.assertEqual(len(pub), 44)
        self.assertTrue(priv.endswith("="))
        self.assertTrue(pub.endswith("="))

    def test_02_assemble_wireproxy_config(self):
        """Verify that assemble_wireproxy_config cleanly strips conflicting sections and injects [Socks5]."""
        raw_conf = """
[Interface]
PrivateKey = fake_key
Address = 10.0.0.2/24
PostUp = iptables -A FORWARD -i %i -j ACCEPT

[Peer]
PublicKey = fake_peer
Endpoint = 1.2.3.4:51820
AllowedIPs = 0.0.0.0/0

[Socks5]
BindAddress = 0.0.0.0:9999
"""
        assembled = assemble_wireproxy_config(raw_conf, port=1080, host="127.0.0.1")
        self.assertIn("[Interface]", assembled)
        self.assertIn("[Peer]", assembled)
        self.assertNotIn("PostUp", assembled)
        self.assertNotIn("0.0.0.0:9999", assembled)
        self.assertIn("[Socks5]\nBindAddress = 127.0.0.1:1080", assembled)

    def test_03_find_free_port(self):
        """Verify free port detection."""
        port = find_free_port(preferred=1080)
        self.assertGreater(port, 0)
        self.assertLess(port, 65536)

    def test_04_warp_registration_and_wireproxy_e2e(self):
        """End-to-end test: register WARP, spawn wireproxy, verify SOCKS5 and exit IP, then cleanly shut down."""
        print("\n[E2E] 1. Generating / retrieving WARP config...")
        cfg = get_or_create_warp_config()
        self.assertIn("[Interface]", cfg)
        self.assertIn("[Peer]", cfg)
        self.assertIn("PrivateKey", cfg)
        self.assertIn("PublicKey", cfg)

        manager = WireproxyManager()
        test_port = find_free_port(preferred=0)
        print(f"[E2E] 2. Spawning wireproxy on 127.0.0.1:{test_port}...")
        started = manager.start_wireproxy(cfg, port=test_port)
        self.assertTrue(started, "wireproxy failed to start or SOCKS5 greeting failed")
        self.assertTrue(manager.is_running)

        try:
            print(f"[E2E] 3. Testing connection through {manager.proxy_url}...")
            ok, info = WireproxyManager.test_connection(manager.proxy_url, timeout=10.0)
            print(f"[E2E] Result: success={ok}, info='{info}'")
            self.assertTrue(ok, f"Connection test failed: {info}")
            self.assertIn("Cloudflare", info)
        finally:
            print("[E2E] 4. Stopping wireproxy...")
            manager.stop_wireproxy()
            self.assertFalse(manager.is_running)
            print("[E2E] Stopped successfully and cleaned up.")

    def test_05_profile_manager(self):
        """Verify WireguardProfileManager imports with 0o600 permissions, lists, and deletes profiles."""
        import tempfile
        from utils.kuroko import WireguardProfileManager

        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as tf:
            tf.write("[Interface]\nPrivateKey = fake\nAddress = 10.0.0.1/32\n")
            temp_path = tf.name

        try:
            profile = WireguardProfileManager.import_profile(temp_path)
            self.assertIsNotNone(profile)
            fname = profile["filename"]
            p_path = Path(profile["path"])
            self.assertTrue(p_path.is_file())
            # Enforce 0o600 permissions check
            mode = p_path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600, f"Expected 0o600 permissions on profile, got {oct(mode)}")

            # Verify listed
            profiles = WireguardProfileManager.list_profiles()
            self.assertTrue(any(p["filename"] == fname for p in profiles))

            # Delete
            deleted = WireguardProfileManager.delete_profile(fname)
            self.assertTrue(deleted)
            self.assertFalse(p_path.exists())
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_06_error_signatures(self):
        """Verify is_network_or_proxy_error only triggers on transport/rate-limit errors."""
        from utils.kuroko import is_network_or_proxy_error

        # True cases
        self.assertTrue(is_network_or_proxy_error("System.Net.Http.HttpRequestException: Connection reset by peer"))
        self.assertTrue(is_network_or_proxy_error("RateLimitExceeded"))
        self.assertTrue(is_network_or_proxy_error("HTTP 429 Too Many Requests"))
        self.assertTrue(is_network_or_proxy_error("Unable to connect to the remote server"))
        self.assertTrue(is_network_or_proxy_error("System.Net.Sockets.SocketException: Connection refused"))

        # False cases (non-network)
        self.assertFalse(is_network_or_proxy_error("AccessDenied: Depot decryption key is invalid"))
        self.assertFalse(is_network_or_proxy_error("Missing depot key for depot 481"))
        self.assertFalse(is_network_or_proxy_error("File already exists on disk"))

    def test_07_cooldown_registry(self):
        """Verify cooldown tracking and clearing."""
        from utils.kuroko import is_endpoint_in_cooldown, mark_endpoint_cooldown, clear_endpoint_cooldowns

        clear_endpoint_cooldowns()
        self.assertFalse(is_endpoint_in_cooldown("test_ep"))
        mark_endpoint_cooldown("test_ep")
        self.assertTrue(is_endpoint_in_cooldown("test_ep"))
        clear_endpoint_cooldowns()
        self.assertFalse(is_endpoint_in_cooldown("test_ep"))

    def test_08_test_and_benchmark_endpoint(self):
        """Verify unified test_and_benchmark_endpoint and StealthPoolManager candidate runner."""
        from unittest.mock import MagicMock, patch
        from utils.kuroko import StealthPoolManager, test_and_benchmark_endpoint

        mock_manager = MagicMock()
        mock_manager.proxy_url = "socks5h://127.0.0.1:40001"
        mock_manager.test_connection.return_value = (True, "104.28.1.1 (Cloudflare WARP - US)")
        mock_manager.measure_speed.return_value = (True, 42, 115.5, "Ping: 42ms | Speed: 115.5 Mbps")

        with patch("utils.kuroko.Kuroko", return_value=mock_manager):
            with patch("utils.kuroko.get_or_create_warp_config", return_value="[Interface]\nPrivateKey=abc\n"):
                ok, exit_lbl, ping, speed, err = test_and_benchmark_endpoint("warp")
                self.assertTrue(ok)
                self.assertIn("Cloudflare WARP", exit_lbl)
                self.assertEqual(ping, 42)
                self.assertEqual(speed, 115.5)
                self.assertEqual(err, "")
                mock_manager.stop.assert_called_once()

        # Test candidate benchmark runner
        candidate = {
            "id": "warp",
            "type": "warp",
            "name": "Cloudflare WARP (Auto)",
            "config_path": "",
            "url": "",
        }
        with patch("utils.kuroko.test_and_benchmark_endpoint", return_value=(True, "1.2.3.4 (Edge)", 30, 88.0, "")):
            res = StealthPoolManager.test_and_benchmark_candidate(candidate)
            self.assertEqual(len(res), 5)
            self.assertTrue(res[0])
            self.assertEqual(res[1], "1.2.3.4 (Edge)")
            self.assertEqual(res[2], 30)
            self.assertEqual(res[3], 88.0)


if __name__ == "__main__":
    unittest.main()

