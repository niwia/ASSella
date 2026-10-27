#!/usr/bin/env python3
"""Behavioural tests for the update checker in utils.version.

Covers the rules that decide whether a remote build is offered as an update:

  * the base version (major.minor.patch) decides across version lines, so a
    channel switch can never offer a downgrade ('2.7.0beta' over '3.0.1'),
  * a release published before this build was compiled is never offered,
  * within one base version the publication date decides,
  * the same version is never offered (no nagging on every check),
  * the raw version file on the channel branch is only a fallback for when the
    releases API yields nothing.

The network is faked by patching urllib.request.urlopen, so the suite runs
offline and deterministically.

Run standalone:
    QT_QPA_PLATFORM=offscreen python3 scripts/test_update_checker.py
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import utils.version as version_mod
from utils.version import (
    app_version,
    check_for_updates,
    get_build_date,
    get_build_date_display,
)

BUILD_DATE = "2026-10-23"  # build date of the release under test
CURRENT_VERSION = "3.0.3"


class _FakeResponse:
    """Minimal stand-in for the object returned by urllib.request.urlopen."""

    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _release(tag: str, published: str = "2026-10-25T10:00:00Z", assets=("ASSella-3.0.1.AppImage",)) -> dict:
    """Build a GitHub release payload for the fake API."""
    return {
        "tag_name": tag,
        "published_at": published,
        "created_at": published,
        "assets": [{"name": name} for name in assets],
    }


class UpdateCheckerTestCase(unittest.TestCase):
    """Shared helper that runs check_for_updates against a faked network."""

    def run_check(
        self,
        releases=None,
        raw=None,
        channel="stable",
        current=CURRENT_VERSION,
        build_date=BUILD_DATE,
    ) -> dict:
        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", "") or ""
            if "api.github.com" in url:
                if releases is None:
                    raise OSError("network unreachable")
                return _FakeResponse(json.dumps(releases))
            # raw.githubusercontent.com fallback
            if raw is None:
                raise OSError("network unreachable")
            return _FakeResponse(raw)

        with mock.patch.object(version_mod, "BUILD_DATE", build_date):
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                return check_for_updates(configured_channel=channel, current_version=current)


class TestUpdateCheckerRules(UpdateCheckerTestCase):
    def test_beta_channel_with_older_release_is_not_offered(self):
        """The reported bug: switching to Beta offered '2.7.0beta' over '3.0.1'."""
        result = self.run_check(
            releases=[_release("v2.7.0beta", published="2026-10-01T08:00:00Z")],
            channel="beta",
        )
        self.assertEqual(result["status"], "up_to_date")
        self.assertFalse(result["is_cross_channel"])

    def test_canary_channel_without_newer_release_is_not_offered(self):
        result = self.run_check(
            releases=[_release("v3.0.0testing051026003", published="2026-10-05T08:00:00Z")],
            channel="canary",
        )
        self.assertEqual(result["status"], "up_to_date")
        self.assertFalse(result["is_cross_channel"])

    def test_newer_canary_of_same_base_is_offered(self):
        result = self.run_check(
            releases=[_release("v3.0.3testing241026001", published="2026-10-25T10:00:00Z")],
            channel="canary",
        )
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["remote_version"], "3.0.3testing241026001")
        self.assertEqual(result["local_version"], CURRENT_VERSION)
        self.assertTrue(result["is_cross_channel"])

    def test_cross_channel_lower_base_is_never_offered(self):
        """The base version floor holds even when the release is newer than the build."""
        result = self.run_check(
            releases=[_release("v2.7.0beta", published="2026-10-25T10:00:00Z")],
            channel="beta",
        )
        self.assertEqual(result["status"], "up_to_date")

    def test_release_published_before_this_build_is_not_offered(self):
        """A release published before this build was compiled is an older build."""
        result = self.run_check(
            releases=[_release("v3.1.0", published="2026-10-01T08:00:00Z")],
            channel="stable",
        )
        self.assertEqual(result["status"], "up_to_date")

    def test_same_version_is_not_offered(self):
        """The running build must not be offered back as an update."""
        result = self.run_check(
            releases=[_release("v3.0.3", published="2026-10-25T10:00:00Z")],
            channel="stable",
        )
        self.assertEqual(result["status"], "up_to_date")

    def test_newer_stable_release_is_offered(self):
        result = self.run_check(
            releases=[_release("v3.0.4", published="2026-10-25T10:00:00Z")],
            channel="stable",
        )
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["remote_version"], "3.0.4")
        self.assertFalse(result["is_cross_channel"])

    def test_release_without_appimage_asset_is_ignored(self):
        result = self.run_check(
            releases=[_release("v3.0.4", published="2026-10-25T10:00:00Z", assets=("ASSella-3.0.4.AppImage.zsync",))],
            channel="stable",
        )
        self.assertEqual(result["status"], "offline")


class TestUpdateCheckerFallback(UpdateCheckerTestCase):
    def test_fallback_raw_file_with_older_version_is_not_offered(self):
        """The raw branch file is a fallback, not a downgrade offer."""
        result = self.run_check(releases=None, raw="2.7.0beta", channel="beta")
        self.assertEqual(result["status"], "up_to_date")
        self.assertEqual(result["remote_version"], "2.7.0beta")

    def test_fallback_raw_file_with_newer_version_is_offered(self):
        result = self.run_check(releases=None, raw="3.1.0", channel="stable")
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["remote_version"], "3.1.0")

    def test_offline_when_no_source_answers(self):
        result = self.run_check(releases=None, raw=None, channel="stable")
        self.assertEqual(result["status"], "offline")
        self.assertIsNone(result["remote_version"])
        self.assertTrue(result["error"])


class TestBuildDateHelpers(unittest.TestCase):
    def test_default_build_date(self):
        self.assertEqual(version_mod.BUILD_DATE, "2026-10-26")
        self.assertEqual(get_build_date(), "2026-10-26")
        self.assertEqual(get_build_date_display(), "26/10/2026")

    def test_version_file_matches_the_release(self):
        self.assertEqual(app_version, "3.0.4")

    def test_build_stamp_in_version_file_wins(self):
        """A dated build ('20261024+ASSella-3.0.1') carries its own build date."""
        with mock.patch.object(version_mod, "app_version", "20261024+ASSella-3.0.1"):
            self.assertEqual(get_build_date(), "2026-10-24")
            self.assertEqual(get_build_date_display(), "24/10/2026")


if __name__ == "__main__":
    unittest.main()
