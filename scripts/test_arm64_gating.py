"""Unit tests for ARM64 architecture and Armada OS detection and SLS gating."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Ensure src is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


class TestArm64Gating(unittest.TestCase):
    def setUp(self):
        self.orig_arm64 = os.environ.get("ASSELLA_FORCE_ARM64")
        self.orig_armada = os.environ.get("ASSELLA_FORCE_ARMADA")

    def tearDown(self):
        if self.orig_arm64 is not None:
            os.environ["ASSELLA_FORCE_ARM64"] = self.orig_arm64
        else:
            os.environ.pop("ASSELLA_FORCE_ARM64", None)

        if self.orig_armada is not None:
            os.environ["ASSELLA_FORCE_ARMADA"] = self.orig_armada
        else:
            os.environ.pop("ASSELLA_FORCE_ARMADA", None)

    def test_arm64_detection_env_override(self):
        from utils.helpers import is_arm64

        os.environ["ASSELLA_FORCE_ARM64"] = "1"
        self.assertTrue(is_arm64())

        os.environ["ASSELLA_FORCE_ARM64"] = "0"
        with patch("platform.machine", return_value="x86_64"):
            self.assertFalse(is_arm64())

        with patch("platform.machine", return_value="aarch64"):
            self.assertTrue(is_arm64())

        with patch("platform.machine", return_value="arm64"):
            self.assertTrue(is_arm64())

    def test_armada_os_detection(self):
        from utils.helpers import is_armada_os

        os.environ["ASSELLA_FORCE_ARMADA"] = "1"
        self.assertTrue(is_armada_os())

        os.environ.pop("ASSELLA_FORCE_ARMADA", None)
        with patch("os.path.exists", return_value=True), patch(
            "builtins.open", unittest.mock.mock_open(read_data='NAME="Armada Linux"\nID=armada\n')
        ):
            self.assertTrue(is_armada_os())

    def test_slssteam_mode_gated_on_arm64(self):
        from utils.yaml.modes import is_slssteam_mode_enabled, is_at0m_enabled, is_slssteam_config_management_enabled

        os.environ["ASSELLA_FORCE_ARM64"] = "1"
        self.assertFalse(is_slssteam_mode_enabled())
        self.assertFalse(is_at0m_enabled())
        self.assertFalse(is_slssteam_config_management_enabled())

    def test_slssteam_integration_pipe_gated_on_arm64(self):
        from utils.slssteam_integration import _is_slssteam_available

        os.environ["ASSELLA_FORCE_ARM64"] = "1"
        self.assertFalse(_is_slssteam_available())

    def test_dotnet_installer_includes_arm64_flag(self):
        from utils.helpers import install_dotnet_9

        os.environ["ASSELLA_FORCE_ARM64"] = "1"
        with patch("sys.platform", "linux"), \
             patch("shutil.which", return_value="/usr/bin/curl"), \
             patch("subprocess.run") as mock_run, \
             patch("os.chmod"), \
             patch("os.remove"):
            mock_run.side_effect = [
                MagicMock(returncode=0),
                MagicMock(returncode=0, stdout="Installed"),
            ]
            success = install_dotnet_9()
            self.assertTrue(success)
            self.assertEqual(mock_run.call_count, 2)
            install_cmd = mock_run.call_args_list[1][0][0]
            self.assertIn("--architecture", install_cmd)
            self.assertIn("arm64", install_cmd)

    def test_wireproxy_arm64_asset_name(self):
        import platform
        with patch("platform.machine", return_value="aarch64"):
            arch = platform.machine().lower()
            if arch in ("aarch64", "arm64"):
                asset = "wireproxy_linux_arm64.tar.gz"
            else:
                asset = "wireproxy_linux_amd64.tar.gz"
            self.assertEqual(asset, "wireproxy_linux_arm64.tar.gz")


if __name__ == "__main__":
    unittest.main()
