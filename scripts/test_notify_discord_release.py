#!/usr/bin/env python3
"""Unit tests for scripts/notify_discord_release.py."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import notify_discord_release as ndr


class TestNotifyDiscordRelease(unittest.TestCase):
    def test_webhook_resolution_beta(self):
        """Verify beta channel picks DISCORD_WEBHOOK_BETA_URL."""
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_BETA_URL": "https://discord.com/api/webhooks/beta_test",
            "DISCORD_WEBHOOK_MAIN_URL": "https://discord.com/api/webhooks/main_test",
        }, clear=True):
            self.assertEqual(ndr.resolve_webhook_url("beta"), "https://discord.com/api/webhooks/beta_test")

    def test_webhook_resolution_main(self):
        """Verify stable / main channel picks DISCORD_WEBHOOK_MAIN_URL."""
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_BETA_URL": "https://discord.com/api/webhooks/beta_test",
            "DISCORD_WEBHOOK_MAIN_URL": "https://discord.com/api/webhooks/main_test",
        }, clear=True):
            self.assertEqual(ndr.resolve_webhook_url("stable"), "https://discord.com/api/webhooks/main_test")
            self.assertEqual(ndr.resolve_webhook_url("main"), "https://discord.com/api/webhooks/main_test")

    def test_webhook_resolution_fallback(self):
        """Verify fallback to DISCORD_WEBHOOK_URL when channel-specific env is missing."""
        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/fallback_test",
        }, clear=True):
            self.assertEqual(ndr.resolve_webhook_url("beta"), "https://discord.com/api/webhooks/fallback_test")
            self.assertEqual(ndr.resolve_webhook_url("stable"), "https://discord.com/api/webhooks/fallback_test")

    def test_webhook_resolution_empty(self):
        """Verify returns empty string when no webhooks are set."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ndr.resolve_webhook_url("beta"), "")
            self.assertEqual(ndr.resolve_webhook_url("stable"), "")

    def test_format_changelog_truncation(self):
        """Verify changelog formatting and truncation rules."""
        empty_res = ndr.format_changelog("")
        self.assertIn("ready for installation", empty_res)

        short_body = "Fixed bug A.\nAdded feature B."
        self.assertEqual(ndr.format_changelog(short_body), short_body)

        long_body = "Line entry\n" * 200
        truncated = ndr.format_changelog(long_body, max_chars=500)
        self.assertLessEqual(len(truncated), 600)
        self.assertIn("[Full changelog available on GitHub]", truncated)

    def test_build_payload(self):
        """Verify payload structure, colors, and embed fields."""
        payload_beta = ndr.build_payload(
            tag="v3.1.0-beta.1",
            channel="beta",
            body="Awesome beta features",
            repo="niwia/ASSella",
            commit="abcdef1234567890",
            ping="@here",
        )
        self.assertEqual(payload_beta["username"], "ASSella Releases")
        self.assertEqual(payload_beta.get("content"), "@here")
        self.assertEqual(len(payload_beta["embeds"]), 1)
        embed = payload_beta["embeds"][0]
        self.assertEqual(embed["color"], ndr.COLOR_BETA)
        self.assertIn("v3.1.0-beta.1", embed["title"])
        self.assertIn("https://github.com/niwia/ASSella/releases/tag/v3.1.0-beta.1", embed["url"])

        # Check fields
        field_names = [f["name"] for f in embed["fields"]]
        self.assertIn("📥 Downloads", field_names)
        self.assertIn("🏷️ Channel", field_names)
        self.assertIn("🔗 Commit", field_names)
        self.assertIn("📄 Release Page", field_names)

        # Check stable color
        payload_stable = ndr.build_payload(
            tag="v3.0.0",
            channel="stable",
            body="Stable release",
            repo="niwia/ASSella",
            commit="1234567",
        )
        self.assertEqual(payload_stable["embeds"][0]["color"], ndr.COLOR_STABLE)

    @patch("urllib.request.urlopen")
    def test_send_discord_notification_success(self, mock_urlopen):
        """Verify HTTP 204 or 200 is treated as successful delivery."""
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        ok = ndr.send_discord_notification("https://fake.url", {"content": "hello"})
        self.assertTrue(ok)

    def test_main_missing_webhook_safe_exit(self):
        """Verify main() returns 0 cleanly when no webhook is set (never breaks CI)."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.argv", ["notify_discord_release.py", "--channel", "beta"]):
                ret = ndr.main()
                self.assertEqual(ret, 0)


if __name__ == "__main__":
    unittest.main()
