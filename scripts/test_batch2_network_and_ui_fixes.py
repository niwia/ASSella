#!/usr/bin/env python3
"""Unit tests for Batch 2 audit fixes (ISP bypass, negative caching, companions cache)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class Batch2FixesTests(unittest.TestCase):
    def test_rewrite_url_for_wirecutter_adds_hubcap_prefix(self):
        from utils.isp_bypass import rewrite_url_for_wirecutter

        self.assertEqual(
            rewrite_url_for_wirecutter(
                "https://hubcapmanifest.com/api/v1/manifest/730",
                "https://wirecutter.example.workers.dev",
            ),
            "https://wirecutter.example.workers.dev/hubcap/manifest/730",
        )
        self.assertEqual(
            rewrite_url_for_wirecutter(
                "https://hubcapmanifest.com/api/v1/health",
                "https://wirecutter.example.workers.dev/hubcap/",
            ),
            "https://wirecutter.example.workers.dev/hubcap/health",
        )

    def test_morrenus_negative_caching(self):
        from core import morrenus_api

        morrenus_api._last_user_stats_fetch = 0.0
        morrenus_api._last_generate_usage_fetch = 0.0
        morrenus_api._user_stats_backoff_until = 0.0
        morrenus_api._generate_usage_backoff_until = 0.0
        morrenus_api._last_health_check_time = 0.0
        morrenus_api._cached_health_check = None
        morrenus_api._contents_cache.clear()

        with tempfile.TemporaryDirectory() as tmp_home, patch.dict(
            os.environ,
            {"HOME": tmp_home, "XDG_DATA_HOME": tmp_home, "XDG_CONFIG_HOME": tmp_home},
            clear=False,
        ), patch.object(morrenus_api, "_make_json_request", return_value={"error": "offline"}) as mock_req, patch(
            "utils.isp_bypass.execute_hubcap_request", side_effect=RuntimeError("offline")
        ) as mock_health_req:
            r1 = morrenus_api.get_user_stats()
            r2 = morrenus_api.get_user_stats()
            self.assertIn("error", r1)
            self.assertIn("error", r2)
            self.assertEqual(mock_req.call_count, 1)

            u1 = morrenus_api.get_generate_usage()
            u2 = morrenus_api.get_generate_usage()
            self.assertIn("error", u1)
            self.assertIn("error", u2)
            self.assertEqual(mock_req.call_count, 2)

            h1 = morrenus_api.check_health()
            h2 = morrenus_api.check_health()
            self.assertIn("error", h1)
            self.assertIn("error", h2)
            self.assertEqual(mock_health_req.call_count, 1)

            c1 = morrenus_api.get_manifest_contents(730)
            c2 = morrenus_api.get_manifest_contents(730)
            self.assertIn("error", c1)
            self.assertIn("error", c2)
            self.assertEqual(mock_req.call_count, 3)

    def test_decky_homes_cached_and_respects_home_change(self):
        from utils import companions

        companions._DECKY_HOMES_CACHE = None
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            with patch.dict(os.environ, {"HOME": d1}, clear=False):
                first = companions._decky_homes()
                second = companions._decky_homes()
                self.assertEqual(first, second)
                self.assertIn(Path(d1) / "homebrew", first)
            with patch.dict(os.environ, {"HOME": d2}, clear=False):
                third = companions._decky_homes()
                self.assertIn(Path(d2) / "homebrew", third)


if __name__ == "__main__":
    unittest.main()
