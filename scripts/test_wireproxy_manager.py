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


if __name__ == "__main__":
    unittest.main()

