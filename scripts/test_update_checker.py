#!/usr/bin/env python3
"""Behavioural tests for the update checker in utils.version.

Covers the rules that decide whether a remote build is offered as an update:

  * switching to a different branch (cross-channel) offers that branch's latest
    published build even if its base version or build date is older,
  * staying on the same branch rejects releases published before this build was
    compiled and prevents same-day or same-version downgrades,
  * legacy non-prerelease clean tags on beta (e.g. v3.0.1) are excluded from
    beta, and releases within a channel are ordered by UTC published_at first,
  * the raw version file on the channel branch is only a fallback when the
    releases API errors/fails (never overriding an empty online result).

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


def _release(
    tag: str,
    published: str = "2026-10-25T10:00:00Z",
    assets=("ASSella-3.0.1.AppImage",),
    prerelease: bool = False,
    target_commitish: str = "",
    channel: str = "",
) -> dict:
    """Build a GitHub release payload (or R2 manifest) for the fake API."""
    payload = {
        "tag_name": tag,
        "published_at": published,
        "created_at": published,
        "prerelease": prerelease,
        "assets": [{"name": name} for name in assets],
    }
    if target_commitish:
        payload["target_commitish"] = target_commitish
    if channel:
        payload["channel"] = channel
    return payload


class UpdateCheckerTestCase(unittest.TestCase):
    """Shared helper that runs check_for_updates against a faked network."""

    def run_check(
        self,
        releases=None,
        raw=None,
        r2_manifest=None,
        channel="stable",
        current=CURRENT_VERSION,
        build_date=BUILD_DATE,
        update_server=None,
        build_timestamp=None,
    ) -> dict:
        self.requested_urls = []

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", "") or ""
            self.requested_urls.append(url)
            if "api.github.com" in url:
                if releases is None:
                    raise OSError("network unreachable")
                return _FakeResponse(json.dumps(releases))
            if "/releases/channels/" in url:
                if r2_manifest is None:
                    raise OSError("r2 unreachable")
                if isinstance(r2_manifest, dict) and any(
                    k in ("stable", "beta", "canary") for k in r2_manifest
                ):
                    matched = None
                    for ch_name, ch_payload in r2_manifest.items():
                        if url.endswith(f"/releases/channels/{ch_name}.json"):
                            matched = ch_payload
                            break
                    if matched is None:
                        raise OSError(f"r2 channel 404: {url}")
                    return _FakeResponse(json.dumps(matched))
                return _FakeResponse(json.dumps(r2_manifest))
            # raw.githubusercontent.com fallback
            if raw is None:
                raise OSError("network unreachable")
            return _FakeResponse(raw)

        effective_date = build_timestamp[:10] if build_timestamp else build_date
        with mock.patch.object(version_mod, "BUILD_DATE", effective_date):
            with mock.patch.object(
                version_mod,
                "BUILD_TIMESTAMP",
                build_timestamp if build_timestamp is not None else "",
            ):
                with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                    return check_for_updates(
                        configured_channel=channel,
                        current_version=current,
                        update_server=update_server,
                    )


class TestUpdateCheckerRules(UpdateCheckerTestCase):
    def test_same_branch_beta_with_older_release_is_not_offered_but_cross_channel_switches(self):
        """Same-branch stays up_to_date for older releases; cross-channel offers the target branch."""
        same_branch = self.run_check(
            releases=[_release("v2.7.0beta", published="2026-10-01T08:00:00Z", prerelease=True, target_commitish="beta")],
            channel="beta",
            current="2.9.9beta",
            build_date="2026-11-11",
        )
        self.assertEqual(same_branch["status"], "up_to_date")
        self.assertFalse(same_branch["is_cross_channel"])

        cross_branch = self.run_check(
            releases=[_release("v2.7.0beta", published="2026-10-01T08:00:00Z", prerelease=True, target_commitish="beta")],
            channel="beta",
        )
        self.assertEqual(cross_branch["status"], "update_available")
        self.assertEqual(cross_branch["remote_version"], "2.7.0beta")
        self.assertTrue(cross_branch["is_cross_channel"])

    def test_canary_channel_without_newer_release_is_not_offered(self):
        non_pre_canary = self.run_check(
            releases=[_release("v3.0.0testing051026003", published="2026-10-05T08:00:00Z")],
            channel="canary",
        )
        self.assertEqual(non_pre_canary["status"], "up_to_date")
        self.assertFalse(non_pre_canary["is_cross_channel"])

        same_canary = self.run_check(
            releases=[_release("v3.0.0testing051026003", published="2026-10-05T08:00:00Z", prerelease=True)],
            channel="canary",
            current="3.0.3testing241026001",
            build_date="2026-10-23",
        )
        self.assertEqual(same_canary["status"], "up_to_date")
        self.assertFalse(same_canary["is_cross_channel"])

    def test_newer_canary_of_same_base_is_offered(self):
        result = self.run_check(
            releases=[_release("v3.0.3testing241026001", published="2026-10-25T10:00:00Z", prerelease=True)],
            channel="canary",
        )
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["remote_version"], "3.0.3testing241026001")
        self.assertEqual(result["local_version"], CURRENT_VERSION)
        self.assertTrue(result["is_cross_channel"])

        non_pre = self.run_check(
            releases=[_release("v3.0.3canary241026002", published="2026-10-25T10:00:00Z", prerelease=False)],
            channel="canary",
        )
        self.assertEqual(non_pre["status"], "up_to_date")
        self.assertIsNone(non_pre["remote_version"])

    def test_same_branch_lower_base_is_not_offered_while_cross_channel_switches(self):
        """Same-branch rejects lower base even with a newer timestamp; cross-channel allows branch switching."""
        same_branch = self.run_check(
            releases=[_release("v3.0.2", published="2026-10-25T10:00:00Z", target_commitish="main")],
            channel="stable",
            current="3.0.3",
            build_date="2026-10-23",
        )
        self.assertEqual(same_branch["status"], "up_to_date")
        self.assertFalse(same_branch["is_cross_channel"])

        cross_branch = self.run_check(
            releases=[_release("v2.7.0beta", published="2026-10-25T10:00:00Z", prerelease=True, target_commitish="beta")],
            channel="beta",
        )
        self.assertEqual(cross_branch["status"], "update_available")
        self.assertEqual(cross_branch["remote_version"], "2.7.0beta")
        self.assertTrue(cross_branch["is_cross_channel"])

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
        self.assertEqual(result["status"], "up_to_date")
        self.assertIsNone(result["remote_version"])
        self.assertEqual(result["source"], "github")
        self.assertFalse(any("raw.githubusercontent.com" in u for u in self.requested_urls))


class TestUpdateCheckerFallback(UpdateCheckerTestCase):
    def test_fallback_raw_file_with_older_version_is_not_offered(self):
        """The raw branch file is a fallback, not a same-branch downgrade offer."""
        result_beta = self.run_check(
            releases=None,
            raw="2.7.0beta",
            channel="beta",
            current="2.9.9beta",
            build_date="2026-11-11",
        )
        self.assertEqual(result_beta["status"], "up_to_date")
        self.assertEqual(result_beta["remote_version"], "2.7.0beta")

        result_stable = self.run_check(
            releases=None,
            raw="3.0.5",
            channel="stable",
            current="3.1.0",
            build_date="2026-11-09",
        )
        self.assertEqual(result_stable["status"], "up_to_date")
        self.assertEqual(result_stable["remote_version"], "3.0.5")

    def test_fallback_raw_file_with_newer_version_is_offered(self):
        result = self.run_check(releases=None, raw="3.1.0", channel="stable")
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["remote_version"], "3.1.0")

    def test_offline_when_no_source_answers(self):
        result = self.run_check(releases=None, raw=None, channel="stable")
        self.assertEqual(result["status"], "offline")
        self.assertIsNone(result["remote_version"])
        self.assertTrue(result["error"])


class TestUpdateServerSelection(UpdateCheckerTestCase):
    def test_auto_uses_github_first_without_hitting_r2(self):
        result = self.run_check(
            releases=[_release("v3.0.4", published="2026-10-25T10:00:00Z")],
            r2_manifest=_release("v3.0.5", published="2026-10-26T10:00:00Z"),
            channel="stable",
            update_server="auto",
        )
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["remote_version"], "3.0.4")
        self.assertEqual(result["source"], "github")
        self.assertEqual(result["update_server"], "auto")
        self.assertFalse(any("/releases/channels/" in u for u in self.requested_urls))

    def test_auto_falls_back_to_r2_when_github_fails(self):
        result = self.run_check(
            releases=None,
            raw="3.0.4",
            r2_manifest=_release("v3.0.5", published="2026-10-26T10:00:00Z"),
            channel="stable",
            update_server="auto",
        )
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["remote_version"], "3.0.5")
        self.assertEqual(result["source"], "r2")
        self.assertTrue(any("api.github.com" in u for u in self.requested_urls))
        self.assertTrue(any("/releases/channels/stable.json" in u for u in self.requested_urls))

    def test_r2_mode_skips_github_and_raw_completely(self):
        result = self.run_check(
            releases=[_release("v3.9.9", published="2026-10-28T10:00:00Z")],
            raw="3.9.9",
            r2_manifest=_release("v3.0.5", published="2026-10-26T10:00:00Z"),
            channel="stable",
            update_server="r2",
        )
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["remote_version"], "3.0.5")
        self.assertEqual(result["source"], "r2")
        self.assertEqual(result["update_server"], "r2")
        self.assertFalse(any("github" in u for u in self.requested_urls))

    def test_r2_mode_offline_does_not_fallback_to_github_raw(self):
        result = self.run_check(
            releases=None,
            raw="3.9.9",
            r2_manifest=None,
            channel="stable",
            update_server="r2",
        )
        self.assertEqual(result["status"], "offline")
        self.assertIsNone(result["source"])
        self.assertFalse(any("github" in u for u in self.requested_urls))

    def test_github_mode_ignores_r2_even_when_github_fails(self):
        result = self.run_check(
            releases=None,
            raw="3.0.4",
            r2_manifest=_release("v3.9.9", published="2026-10-26T10:00:00Z"),
            channel="stable",
            update_server="github",
        )
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["remote_version"], "3.0.4")
        self.assertEqual(result["source"], "github_raw")
        self.assertEqual(result["update_server"], "github")
        self.assertFalse(any("/releases/channels/" in u for u in self.requested_urls))


class TestBranchAndPrereleaseAwareness(UpdateCheckerTestCase):
    def test_prerelease_without_beta_suffix_is_rejected_on_stable(self):
        """A clean-tagged GitHub Pre-release (e.g. v2.9.9 prerelease=True) must never hit stable."""
        for branch in ("", "main", "beta"):
            with self.subTest(target_commitish=branch):
                # Without any stable release present, stable stays up_to_date online rather than offering 2.9.9 or hitting raw.
                result = self.run_check(
                    releases=[
                        _release(
                            "v2.9.9",
                            published="2026-11-11T10:00:00Z",
                            prerelease=True,
                            target_commitish=branch,
                        )
                    ],
                    raw="3.9.9",
                    channel="stable",
                    current="2.6.5",
                    build_date="2026-09-01",
                )
                self.assertEqual(result["status"], "up_to_date")
                self.assertIsNone(result["remote_version"])
                self.assertEqual(result["source"], "github")
                self.assertFalse(any("raw.githubusercontent.com" in u for u in self.requested_urls))

                # With v3.1.0 stable also present, stable selects 3.1.0 and ignores 2.9.9.
                with_stable = self.run_check(
                    releases=[
                        _release(
                            "v2.9.9",
                            published="2026-11-11T10:00:00Z",
                            prerelease=True,
                            target_commitish=branch,
                        ),
                        _release(
                            "v3.1.0",
                            published="2026-11-09T10:00:00Z",
                            prerelease=False,
                            target_commitish="main",
                        ),
                    ],
                    channel="stable",
                    current="3.0.5",
                    build_date="2026-10-25",
                )
                self.assertEqual(with_stable["status"], "update_available")
                self.assertEqual(with_stable["remote_version"], "3.1.0")

    def test_non_main_target_branch_is_rejected_on_stable(self):
        """Releases targeting beta, canary, or non-main branches never offer on stable."""
        result = self.run_check(
            releases=[
                _release("v3.1.2", published="2026-11-11T10:00:00Z", prerelease=False, target_commitish="beta"),
                _release("v3.1.3", published="2026-11-11T11:00:00Z", prerelease=False, target_commitish="refs/heads/beta"),
                _release("v3.1.4", published="2026-11-11T12:00:00Z", prerelease=False, target_commitish="canary"),
                _release("v3.1.5", published="2026-11-11T13:00:00Z", prerelease=False, target_commitish="arm64"),
                _release("v3.1.0", published="2026-11-09T10:00:00Z", prerelease=False, target_commitish="main"),
            ],
            channel="stable",
            current="3.1.0",
            build_date="2026-11-01",
        )
        self.assertEqual(result["status"], "up_to_date")
        self.assertEqual(result["remote_version"], "3.1.0")

    def test_beta_channel_accepts_beta_branch_and_prereleases_without_suffix(self):
        res_pre = self.run_check(
            releases=[
                _release("v2.9.9", published="2026-11-11T10:00:00Z", prerelease=True, target_commitish="beta"),
            ],
            channel="beta",
            current="2.6.5",
            build_date="2026-09-01",
        )
        self.assertEqual(res_pre["status"], "update_available")
        self.assertEqual(res_pre["remote_version"], "2.9.9")

        res_branch = self.run_check(
            releases=[
                _release("v2.9.9", published="2026-11-11T09:00:00Z", prerelease=True, target_commitish="beta"),
                _release("v3.1.2", published="2026-11-11T10:00:00Z", prerelease=True, target_commitish="beta"),
                _release("v3.5.0", published="2026-11-11T11:00:00Z", prerelease=False, target_commitish="main"),
            ],
            channel="beta",
            current="3.1.0",
            build_date="2026-11-01",
        )
        self.assertEqual(res_branch["status"], "update_available")
        self.assertEqual(res_branch["remote_version"], "3.1.2")

        # Historical v3.0.1 (prerelease=False, target_commitish="beta") is excluded from BOTH beta and stable,
        # and within beta, UTC published_at ordering picks Nov 11 v2.9.9beta over older Oct 15 v3.0.5-beta.1.
        res_pub_order = self.run_check(
            releases=[
                _release("v3.0.1", published="2026-10-22T15:04:23Z", prerelease=False, target_commitish="beta"),
                _release("v3.0.5-beta.1", published="2026-10-15T10:00:00Z", prerelease=True, target_commitish="beta"),
                _release("v2.9.9beta", published="2026-11-11T15:19:00Z", prerelease=True, target_commitish="beta"),
            ],
            channel="beta",
            current="2.7.0beta",
            build_date="2026-10-01",
        )
        self.assertEqual(res_pub_order["status"], "update_available")
        self.assertEqual(res_pub_order["remote_version"], "2.9.9beta")

        res_v301_stable = self.run_check(
            releases=[
                _release("v3.0.1", published="2026-10-22T15:04:23Z", prerelease=False, target_commitish="beta"),
            ],
            channel="stable",
            current="2.7.0beta",
            build_date="2026-10-01",
        )
        self.assertEqual(res_v301_stable["status"], "up_to_date")
        self.assertIsNone(res_v301_stable["remote_version"])

    def test_canary_branch_isolation(self):
        releases = [
            _release("v3.2.0", published="2026-11-11T10:00:00Z", prerelease=True, target_commitish="canary"),
            _release("v3.3.0-canary", published="2026-11-11T11:00:00Z", prerelease=False, target_commitish="canary"),
            _release("v3.1.0", published="2026-11-09T10:00:00Z", prerelease=False, target_commitish="main"),
        ]
        for ch in ("stable", "beta"):
            res = self.run_check(releases=releases, channel=ch, current="3.1.0", build_date="2026-11-01")
            self.assertEqual(res["status"], "up_to_date", msg=f"canary leaked to {ch}")

        res_canary = self.run_check(releases=releases, channel="canary", current="3.1.0", build_date="2026-11-01")
        self.assertEqual(res_canary["status"], "update_available")
        self.assertEqual(res_canary["remote_version"], "3.2.0")

    def test_commit_sha_target_commitish_does_not_block_stable(self):
        res = self.run_check(
            releases=[
                _release(
                    "v3.1.2",
                    published="2026-11-11T10:00:00Z",
                    prerelease=False,
                    target_commitish="0123456789abcdef0123456789abcdef01234567",
                )
            ],
            channel="stable",
            current="3.1.0",
            build_date="2026-11-01",
        )
        self.assertEqual(res["status"], "update_available")
        self.assertEqual(res["remote_version"], "3.1.2")

    def test_r2_manifest_channel_field_prevents_stable_leak(self):
        res = self.run_check(
            r2_manifest=_release(
                "v3.1.2",
                published="2026-11-11T10:00:00Z",
                prerelease=True,
                channel="beta",
            ),
            channel="stable",
            current="3.1.0",
            build_date="2026-11-01",
            update_server="r2",
        )
        self.assertEqual(res["status"], "up_to_date")
        self.assertIsNone(res["remote_version"])
        self.assertEqual(res["source"], "r2")

    def test_github_releases_query_requests_100_per_page(self):
        self.run_check(releases=[], channel="stable", update_server="github")
        gh_urls = [u for u in self.requested_urls if "api.github.com/repos/" in u]
        self.assertTrue(gh_urls)
        self.assertIn("per_page=100", gh_urls[0])

    def test_same_day_tiebreaker_prefers_higher_semver(self):
        """Same-day same-branch builds break ties by semver instead of downgrading."""
        # User already on 3.0.1-rc2 (or 3.0.1-beta.2) built today must NOT be downgraded
        # to same-day 3.0.1-rc1 (or 3.0.1-beta.1) on beta.
        res_no_downgrade = self.run_check(
            releases=[
                _release("v3.0.1-beta.1", published="2026-11-11T10:00:00Z", prerelease=True, target_commitish="beta"),
            ],
            channel="beta",
            current="3.0.1-beta.2",
            build_date="2026-11-11",
        )
        self.assertEqual(res_no_downgrade["status"], "up_to_date")

        res_rc_no_downgrade = self.run_check(
            releases=[
                _release("v3.0.1-rc1", published="2026-11-11T10:00:00Z", prerelease=True, target_commitish="beta"),
            ],
            channel="beta",
            current="3.0.1-rc2",
            build_date="2026-11-11",
        )
        self.assertEqual(res_rc_no_downgrade["status"], "up_to_date")

        # User on 3.0.1-beta.1 sees same-day 3.0.1-beta.2 on beta; 3.0.1-rc1 sees 3.0.1 on stable.
        res_beta = self.run_check(
            releases=[_release("v3.0.1-beta.2", published="2026-11-11T12:00:00Z", prerelease=True, target_commitish="beta")],
            channel="beta",
            current="3.0.1-beta.1",
            build_date="2026-11-11",
        )
        self.assertEqual(res_beta["status"], "update_available")
        self.assertEqual(res_beta["remote_version"], "3.0.1-beta.2")

        res_rc = self.run_check(
            releases=[_release("v3.0.1", published="2026-11-11T12:00:00Z", prerelease=False, target_commitish="main")],
            channel="stable",
            current="3.0.1-rc1",
            build_date="2026-11-11",
        )
        self.assertEqual(res_rc["status"], "update_available")
        self.assertEqual(res_rc["remote_version"], "3.0.1")

    def test_custom_current_version_build_stamp_used(self):
        """Date stamps inside passed current_version override module BUILD_DATE."""
        res = self.run_check(
            releases=[_release("v3.0.2", published="2026-10-25T10:00:00Z", prerelease=False, target_commitish="main")],
            channel="stable",
            current="20261021+ASSella-3.0.0",
            build_date="2026-11-09",
        )
        self.assertEqual(res["status"], "update_available")
        self.assertEqual(res["remote_version"], "3.0.2")

    def test_r2_beta_channel_is_strictly_beta_only(self):
        """Beta in R2 mode checks ONLY beta.json and never reads stable.json."""
        res_strict_beta = self.run_check(
            r2_manifest={
                "beta": _release("v3.0.0-beta.2", published="2026-11-11T10:00:00Z", prerelease=True, channel="beta"),
                "stable": _release("v3.1.1", published="2026-11-11T10:05:00Z", prerelease=False, channel="stable"),
            },
            channel="beta",
            current="2.9.9beta",
            build_date="2026-11-11",
            update_server="r2",
        )
        self.assertEqual(res_strict_beta["status"], "update_available")
        self.assertEqual(res_strict_beta["remote_version"], "3.0.0-beta.2")
        self.assertEqual(res_strict_beta["source"], "r2")
        self.assertTrue(any(u.endswith("/releases/channels/beta.json") for u in self.requested_urls))
        self.assertFalse(any(u.endswith("/releases/channels/stable.json") for u in self.requested_urls))

        # A stable release misplaced in beta.json must be ignored cleanly (up_to_date).
        res_wrong_manifest = self.run_check(
            r2_manifest={
                "beta": _release("v3.1.1", published="2026-11-11T10:05:00Z", prerelease=False, channel="stable"),
            },
            channel="beta",
            current="2.9.9beta",
            build_date="2026-11-11",
            update_server="r2",
        )
        self.assertEqual(res_wrong_manifest["status"], "up_to_date")
        self.assertIsNone(res_wrong_manifest["remote_version"])
        self.assertEqual(res_wrong_manifest["source"], "r2")

    def test_full_utc_timestamp_cutoff_rejects_earlier_same_day_release(self):
        """Exact UTC timestamps reject earlier same-day same-branch builds; cross-branch still switches."""
        local_ts = "2026-11-11T20:35:00Z"
        earlier_releases = [
            _release("v3.0.0-beta.1", published="2026-11-11T19:00:00Z", prerelease=True, target_commitish="beta"),
            _release("v3.1.0", published="2026-11-11T20:30:00Z", prerelease=False, target_commitish="main"),
        ]
        res_beta = self.run_check(
            releases=earlier_releases,
            channel="beta",
            current="2.9.9beta",
            build_timestamp=local_ts,
        )
        self.assertEqual(res_beta["status"], "up_to_date")

        res_stable_same = self.run_check(
            releases=earlier_releases,
            channel="stable",
            current="3.0.5",
            build_timestamp=local_ts,
        )
        self.assertEqual(res_stable_same["status"], "up_to_date")

        res_stable_cross = self.run_check(
            releases=earlier_releases,
            channel="stable",
            current="2.9.9beta",
            build_timestamp=local_ts,
        )
        self.assertEqual(res_stable_cross["status"], "update_available")
        self.assertEqual(res_stable_cross["remote_version"], "3.1.0")
        self.assertTrue(res_stable_cross["is_cross_channel"])

        later_releases = [
            _release("v3.0.0-beta.2", published="2026-11-11T21:00:00Z", prerelease=True, target_commitish="beta"),
            _release("v3.1.1", published="2026-11-11T21:05:00Z", prerelease=False, target_commitish="main"),
        ]
        res_beta_ok = self.run_check(
            releases=later_releases,
            channel="beta",
            current="2.9.9beta",
            build_timestamp=local_ts,
        )
        self.assertEqual(res_beta_ok["status"], "update_available")
        self.assertEqual(res_beta_ok["remote_version"], "3.0.0-beta.2")

        res_stable_ok = self.run_check(
            releases=later_releases,
            channel="stable",
            current="2.9.9beta",
            build_timestamp=local_ts,
        )
        self.assertEqual(res_stable_ok["status"], "update_available")
        self.assertEqual(res_stable_ok["remote_version"], "3.1.1")

    def test_cross_channel_switch_299beta_to_stable_offers_v310(self):
        """Running 2.9.9beta (Nov 11) stays up_to_date on Beta, but switching to Stable offers Nov 9 v3.1.0."""
        releases = [
            _release("v3.1.0", published="2026-11-09T22:45:12Z", prerelease=False, target_commitish="main"),
            _release("v3.0.1", published="2026-10-22T15:04:23Z", prerelease=False, target_commitish="beta"),
            _release("v2.7.0beta", published="2026-10-01T10:43:42Z", prerelease=True, target_commitish="beta"),
        ]
        local_ts = "2026-11-11T15:19:00Z"

        on_beta = self.run_check(
            releases=releases,
            channel="beta",
            current="2.9.9beta",
            build_timestamp=local_ts,
        )
        self.assertEqual(on_beta["status"], "up_to_date")
        self.assertEqual(on_beta["remote_version"], "2.7.0beta")
        self.assertFalse(on_beta["is_cross_channel"])

        on_stable = self.run_check(
            releases=releases,
            channel="stable",
            current="2.9.9beta",
            build_timestamp=local_ts,
        )
        self.assertEqual(on_stable["status"], "update_available")
        self.assertEqual(on_stable["remote_version"], "3.1.0")
        self.assertTrue(on_stable["is_cross_channel"])

    def test_canary_prefers_explicit_canary_tag_and_skips_r2(self):
        releases = [
            _release("v3.1.0-testing.9", published="2026-11-12T10:00:00Z", prerelease=True, target_commitish="canary"),
            _release("v3.1.0-canary.1", published="2026-11-12T10:05:00Z", prerelease=True, target_commitish="canary"),
        ]
        res = self.run_check(
            releases=releases,
            channel="canary",
            current="3.1.0-canary.0",
            build_timestamp="2026-11-11T20:35:00Z",
        )
        self.assertEqual(res["status"], "update_available")
        self.assertEqual(res["remote_version"], "3.1.0-canary.1")

        # Canary is GitHub-only; explicit R2 mode returns offline without hitting R2 URLs.
        res_r2 = self.run_check(
            r2_manifest={"stable": _release("v3.1.1", published="2026-11-12T10:00:00Z")},
            channel="canary",
            current="3.1.0-canary.0",
            build_timestamp="2026-11-11T20:35:00Z",
            update_server="r2",
        )
        self.assertEqual(res_r2["status"], "offline")
        self.assertEqual(self.requested_urls, [])

    def test_online_empty_github_does_not_hit_raw_fallback(self):
        res = self.run_check(
            releases=[],
            raw="3.9.9",
            channel="stable",
            current="3.0.3",
            build_date="2026-10-23",
            update_server="auto",
        )
        self.assertEqual(res["status"], "up_to_date")
        self.assertIsNone(res["remote_version"])
        self.assertEqual(res["source"], "github")
        self.assertFalse(any("raw.githubusercontent.com" in u for u in self.requested_urls))

    def test_upgrade_path_older_builds_to_299beta_and_311(self):
        """Legacy 2.6.x / 2.7beta builds see v2.9.9beta (beta) and v3.1.1 (stable); 3.1.0 does not see 2.9.9beta."""
        releases = [
            _release("v2.9.9beta", published="2026-11-11T10:00:00Z", prerelease=True, target_commitish="beta"),
            _release("v3.1.1", published="2026-11-11T10:05:00Z", prerelease=False, target_commitish="main"),
        ]
        for old_ver in ("2.6.2", "2.6.2-hotfix1", "2.6.5", "2.7.0beta"):
            res_b = self.run_check(releases=[releases[0]], channel="beta", current=old_ver, build_date="2026-06-01")
            self.assertEqual(res_b["status"], "update_available", msg=old_ver)
            self.assertEqual(res_b["remote_version"], "2.9.9beta", msg=old_ver)

            res_s = self.run_check(releases=releases, channel="stable", current=old_ver, build_date="2026-06-01")
            self.assertEqual(res_s["status"], "update_available", msg=old_ver)
            self.assertEqual(res_s["remote_version"], "3.1.1", msg=old_ver)

        res_310 = self.run_check(releases=releases, channel="stable", current="3.1.0", build_date="2026-11-09")
        self.assertEqual(res_310["status"], "update_available")
        self.assertEqual(res_310["remote_version"], "3.1.1")

    def test_auto_falls_back_to_r2_when_github_has_no_usable_candidates(self):
        """Under server='auto', empty or mismatched GitHub 200 OK falls back to R2."""
        res = self.run_check(
            releases=[
                _release("v3.2.0-canary.1", published="2026-11-12T10:00:00Z", prerelease=True, target_commitish="canary"),
            ],
            r2_manifest={
                "beta": _release("v2.9.9beta", published="2026-11-11T10:00:00Z", prerelease=True, channel="beta"),
            },
            raw="9.9.9",
            channel="beta",
            current="2.7.0beta",
            build_date="2026-06-01",
            update_server="auto",
        )
        self.assertEqual(res["status"], "update_available")
        self.assertEqual(res["remote_version"], "2.9.9beta")
        self.assertEqual(res["source"], "r2")
        self.assertTrue(any("api.github.com" in u for u in self.requested_urls))
        self.assertTrue(any("/releases/channels/beta.json" in u for u in self.requested_urls))
        self.assertFalse(any("raw.githubusercontent.com" in u for u in self.requested_urls))

        # When GitHub DOES return a usable candidate, 'auto' stays on GitHub and skips R2.
        res_gh = self.run_check(
            releases=[
                _release("v2.9.9beta", published="2026-11-11T10:00:00Z", prerelease=True, target_commitish="beta"),
            ],
            r2_manifest={
                "beta": _release("v3.0.0-beta.9", published="2026-11-12T10:00:00Z", prerelease=True, channel="beta"),
            },
            channel="beta",
            current="2.7.0beta",
            build_date="2026-06-01",
            update_server="auto",
        )
        self.assertEqual(res_gh["status"], "update_available")
        self.assertEqual(res_gh["remote_version"], "2.9.9beta")
        self.assertEqual(res_gh["source"], "github")
        self.assertFalse(any("/releases/channels/" in u for u in self.requested_urls))

        # Strict 'github' mode must NOT hit R2 when GitHub returns 200 OK with zero candidates.
        res_strict = self.run_check(
            releases=[],
            r2_manifest={
                "beta": _release("v2.9.9beta", published="2026-11-11T10:00:00Z", prerelease=True, channel="beta"),
            },
            channel="beta",
            current="2.7.0beta",
            build_date="2026-06-01",
            update_server="github",
        )
        self.assertEqual(res_strict["status"], "up_to_date")
        self.assertEqual(res_strict["source"], "github")
        self.assertFalse(any("/releases/channels/" in u for u in self.requested_urls))

    def test_token_aware_canary_and_beta_channel_classifiers(self):
        """'alpha' and substring 'latest'/'contest' never trigger canary; token-bounded tags do."""
        self.assertEqual(version_mod.get_branch_from_version("3.1.0-alpha.1"), "main")
        self.assertEqual(version_mod.get_branch_from_version("3.1.0-latest"), "main")
        self.assertEqual(version_mod.get_branch_from_version("3.1.0-contest"), "main")
        self.assertEqual(version_mod.get_branch_from_version("3.0.0testing051026003"), "canary")
        self.assertEqual(version_mod.get_branch_from_version("3.0Release_candidate111026001"), "canary")
        self.assertEqual(version_mod.get_branch_from_version("3.2.0-canary.1"), "canary")
        self.assertEqual(version_mod.get_branch_from_version("2.9.9beta"), "beta")
        self.assertEqual(version_mod.get_branch_from_version("3.1.0-rc1"), "beta")

        # Canary release filtering rejects 'alpha' and 'latest' even with prerelease=True,
        # while accepting genuine canary tags like Release_candidate.
        res_canary = self.run_check(
            releases=[
                _release("v3.9.9-alpha.1", published="2026-11-12T10:00:00Z", prerelease=True),
                _release("v3.9.9-latest", published="2026-11-12T10:05:00Z", prerelease=True),
                _release("v3.0Release_candidate111026001", published="2026-11-11T10:00:00Z", prerelease=True),
            ],
            channel="canary",
            current="3.0.0-canary.0",
            build_date="2026-11-01",
            update_server="github",
        )
        self.assertEqual(res_canary["status"], "update_available")
        self.assertEqual(res_canary["remote_version"], "3.0Release_candidate111026001")


class TestBuildDateHelpers(unittest.TestCase):
    def test_default_build_date(self):
        self.assertEqual(version_mod.BUILD_DATE, "2026-11-11")
        self.assertTrue(version_mod.BUILD_TIMESTAMP.startswith("2026-11-11T"))
        self.assertEqual(get_build_date(), "2026-11-11")
        self.assertEqual(version_mod.get_build_timestamp(), version_mod.BUILD_TIMESTAMP)
        self.assertEqual(get_build_date_display(), "11/11/2026")

    def test_version_file_matches_the_release(self):
        self.assertEqual(app_version, "2.9.9beta")

    def test_build_stamp_in_version_file_wins(self):
        """A dated build ('20261024+ASSella-3.0.1') carries its own build date."""
        with mock.patch.object(version_mod, "app_version", "20261024+ASSella-3.0.1"):
            self.assertEqual(get_build_date(), "2026-10-24")
            self.assertEqual(get_build_date_display(), "24/10/2026")


if __name__ == "__main__":
    unittest.main()
