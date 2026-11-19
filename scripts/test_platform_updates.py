#!/usr/bin/env python3
"""Unit tests for platform-strict release asset resolution and update filtering."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure src is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.version import (
    get_platform_key,
    get_expected_platform_assets,
    release_has_matching_assets,
    _parse_release_candidates,
)
from managers.app_update_manager import (
    _extract_release_assets,
    _resolve_sha256_sidecar,
)


class TestPlatformStrictUpdates(unittest.TestCase):
    def setUp(self):
        self.orig_force_arm64 = os.environ.get("ASSELLA_FORCE_ARM64")
        os.environ.pop("ASSELLA_FORCE_ARM64", None)

    def tearDown(self):
        if self.orig_force_arm64 is not None:
            os.environ["ASSELLA_FORCE_ARM64"] = self.orig_force_arm64
        else:
            os.environ.pop("ASSELLA_FORCE_ARM64", None)

    def test_platform_key_resolution(self):
        self.assertEqual(get_platform_key("linux", "x86_64"), "linux-x86_64")
        self.assertEqual(get_platform_key("linux", "aarch64"), "linux-aarch64")
        self.assertEqual(get_platform_key("linux", "arm64"), "linux-aarch64")
        self.assertEqual(get_platform_key("win32", "AMD64"), "win32-x64")

        # Test ASSELLA_FORCE_ARM64 override when target_machine is None
        os.environ["ASSELLA_FORCE_ARM64"] = "1"
        self.assertEqual(get_platform_key("linux", None), "linux-aarch64")
        # But explicit target_machine="x86_64" is respected even when env var is set
        self.assertEqual(get_platform_key("linux", "x86_64"), "linux-x86_64")
        os.environ["ASSELLA_FORCE_ARM64"] = "0"
        self.assertEqual(get_platform_key("linux", None), "linux-x86_64")

    def test_x86_requires_assella_appimage_and_ignores_arm64_only(self):
        arm64_only_release = {
            "tag_name": "v3.1.0",
            "prerelease": False,
            "assets": [
                {"name": "ASSella-arm64.AppImage", "browser_download_url": "https://example.com/arm"},
                {"name": "ASSella-arm64.AppImage.zsync", "browser_download_url": "https://example.com/arm.zsync"},
            ],
        }
        # x86_64 client must reject ARM64-only release
        self.assertFalse(release_has_matching_assets(arm64_only_release, "linux", "x86_64"))

        x86_release = {
            "tag_name": "v3.1.0",
            "prerelease": False,
            "assets": [
                {"name": "ASSella.AppImage", "browser_download_url": "https://example.com/x86"},
                {"name": "ASSella.AppImage.zsync", "browser_download_url": "https://example.com/x86.zsync"},
            ],
        }
        self.assertTrue(release_has_matching_assets(x86_release, "linux", "x86_64"))

    def test_aarch64_requires_both_appimage_and_zsync(self):
        # Missing zsync
        arm_no_zsync = {
            "tag_name": "v3.1.0",
            "prerelease": False,
            "assets": [
                {"name": "ASSella-arm64.AppImage", "browser_download_url": "https://example.com/arm"},
            ],
        }
        self.assertFalse(release_has_matching_assets(arm_no_zsync, "linux", "aarch64"))

        # x86_64 only
        x86_only = {
            "tag_name": "v3.1.0",
            "prerelease": False,
            "assets": [
                {"name": "ASSella.AppImage", "browser_download_url": "https://example.com/x86"},
                {"name": "ASSella.AppImage.zsync", "browser_download_url": "https://example.com/x86.zsync"},
            ],
        }
        self.assertFalse(release_has_matching_assets(x86_only, "linux", "aarch64"))

        # Complete ARM64 release
        arm_complete = {
            "tag_name": "v3.1.0",
            "prerelease": False,
            "assets": [
                {"name": "ASSella-arm64.AppImage", "browser_download_url": "https://example.com/arm"},
                {"name": "ASSella-arm64.AppImage.zsync", "browser_download_url": "https://example.com/arm.zsync"},
            ],
        }
        self.assertTrue(release_has_matching_assets(arm_complete, "linux", "aarch64"))

    def test_parse_release_candidates_filters_by_arch(self):
        releases = [
            {
                "tag_name": "v3.0.0",
                "prerelease": False,
                "assets": [
                    {"name": "ASSella.AppImage", "browser_download_url": "https://example.com/3.0.0"},
                ],
            },
            {
                "tag_name": "v3.1.0",
                "prerelease": False,
                "assets": [
                    {"name": "ASSella-arm64.AppImage", "browser_download_url": "https://example.com/3.1.0-arm"},
                ],
            },
            {
                "tag_name": "v3.2.0",
                "prerelease": False,
                "assets": [
                    {"name": "ASSella-arm64.AppImage", "browser_download_url": "https://example.com/3.2.0-arm"},
                    {"name": "ASSella-arm64.AppImage.zsync", "browser_download_url": "https://example.com/3.2.0-arm.zsync"},
                ],
            },
            {
                "tag_name": "v3.3.0",
                "prerelease": False,
                "assets": [
                    {"name": "ASSella.AppImage", "browser_download_url": "https://example.com/3.3.0"},
                    {"name": "ASSella-arm64.AppImage", "browser_download_url": "https://example.com/3.3.0-arm"},
                    {"name": "ASSella-arm64.AppImage.zsync", "browser_download_url": "https://example.com/3.3.0-arm.zsync"},
                ],
            },
        ]

        x86_cands = _parse_release_candidates(releases, "stable", target_platform="linux", target_machine="x86_64")
        x86_versions = [c[0] for c in x86_cands]
        self.assertEqual(x86_versions, ["3.0.0", "3.3.0"])

        arm_cands = _parse_release_candidates(releases, "stable", target_platform="linux", target_machine="aarch64")
        arm_versions = [c[0] for c in arm_cands]
        self.assertEqual(arm_versions, ["3.2.0", "3.3.0"])

    def test_app_update_manager_extract_release_assets(self):
        multi_release = {
            "tag_name": "v3.3.0",
            "assets": [
                {
                    "name": "ASSella.AppImage",
                    "browser_download_url": "https://example.com/ASSella.AppImage",
                    "sha256": "a" * 64,
                },
                {
                    "name": "ASSella.AppImage.zsync",
                    "browser_download_url": "https://example.com/ASSella.AppImage.zsync",
                },
                {
                    "name": "ASSella-arm64.AppImage",
                    "browser_download_url": "https://example.com/ASSella-arm64.AppImage",
                    "sha256": "b" * 64,
                },
                {
                    "name": "ASSella-arm64.AppImage.zsync",
                    "browser_download_url": "https://example.com/ASSella-arm64.AppImage.zsync",
                },
            ],
        }

        # On x86_64:
        with patch("utils.version.get_platform_key", return_value="linux-x86_64"):
            dl, zs, sha = _extract_release_assets(multi_release)
            self.assertEqual(dl, "https://example.com/ASSella.AppImage")
            self.assertEqual(zs, "https://example.com/ASSella.AppImage.zsync")
            self.assertEqual(sha, "a" * 64)

        # On aarch64:
        with patch("utils.version.get_platform_key", return_value="linux-aarch64"):
            dl, zs, sha = _extract_release_assets(multi_release)
            self.assertEqual(dl, "https://example.com/ASSella-arm64.AppImage")
            self.assertEqual(zs, "https://example.com/ASSella-arm64.AppImage.zsync")
            self.assertEqual(sha, "b" * 64)

        # On x86_64, missing zsync is allowed (falls back to full download):
        x86_no_zsync = {
            "tag_name": "v3.3.0",
            "assets": [
                {
                    "name": "ASSella.AppImage",
                    "browser_download_url": "https://example.com/ASSella.AppImage",
                    "sha256": "a" * 64,
                },
            ],
        }
        with patch("utils.version.get_platform_key", return_value="linux-x86_64"):
            dl, zs, sha = _extract_release_assets(x86_no_zsync)
            self.assertEqual(dl, "https://example.com/ASSella.AppImage")
            self.assertIsNone(zs)

        # On aarch64, missing zsync raises RuntimeError:
        arm_no_zsync = {
            "tag_name": "v3.3.0",
            "assets": [
                {
                    "name": "ASSella-arm64.AppImage",
                    "browser_download_url": "https://example.com/ASSella-arm64.AppImage",
                    "sha256": "b" * 64,
                },
            ],
        }
        with patch("utils.version.get_platform_key", return_value="linux-aarch64"):
            with self.assertRaises(RuntimeError):
                _extract_release_assets(arm_no_zsync)

        # Incompatible release raises RuntimeError
        arm_only_release = {
            "tag_name": "v3.2.0",
            "assets": [
                {
                    "name": "ASSella-arm64.AppImage",
                    "browser_download_url": "https://example.com/ASSella-arm64.AppImage",
                },
                {
                    "name": "ASSella-arm64.AppImage.zsync",
                    "browser_download_url": "https://example.com/ASSella-arm64.AppImage.zsync",
                },
            ],
        }
        with patch("utils.version.get_platform_key", return_value="linux-x86_64"):
            with self.assertRaises(RuntimeError):
                _extract_release_assets(arm_only_release)

    def test_sidecar_resolution_is_strict(self):
        release_data = {
            "assets": [
                {"name": "ASSella.AppImage.sha256", "browser_download_url": "https://example.com/x86.sha256"},
                {"name": "ASSella-arm64.AppImage.sha256", "browser_download_url": "https://example.com/arm.sha256"},
            ]
        }
        url, adv = _resolve_sha256_sidecar(release_data, "https://example.com/ASSella.AppImage")
        self.assertEqual(url, "https://example.com/x86.sha256")
        self.assertTrue(adv)

        url_arm, adv_arm = _resolve_sha256_sidecar(release_data, "https://example.com/ASSella-arm64.AppImage")
        self.assertEqual(url_arm, "https://example.com/arm.sha256")
        self.assertTrue(adv_arm)


if __name__ == "__main__":
    unittest.main()
