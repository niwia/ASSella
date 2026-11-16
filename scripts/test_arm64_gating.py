"""Unit tests for ARM64 architecture and Armada OS detection and SLS gating."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

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
            os.environ.pop("ASSELLA_FORCE_ARMADA", None)

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

    def test_slssteam_mode_gated_when_sls_missing_on_arm64(self):
        from utils.yaml.modes import is_slssteam_mode_enabled, is_slssteam_config_management_enabled

        os.environ["ASSELLA_FORCE_ARM64"] = "1"
        mock_env = MagicMock()
        mock_env.sls_installed.return_value = False

        with patch("core.steam_helpers.get_steam_env", return_value=mock_env):
            self.assertFalse(is_slssteam_mode_enabled())
            self.assertFalse(is_slssteam_config_management_enabled())

    def test_slssteam_mode_allowed_when_sls_installed_on_arm64(self):
        from utils.yaml.modes import is_slssteam_mode_enabled

        os.environ["ASSELLA_FORCE_ARM64"] = "1"
        mock_env = MagicMock()
        mock_env.sls_installed.return_value = True

        mock_settings = MagicMock()
        mock_settings.value.return_value = True

        with patch("core.steam_helpers.get_steam_env", return_value=mock_env), \
             patch("utils.yaml.modes.get_settings", return_value=mock_settings):
            self.assertTrue(is_slssteam_mode_enabled())

    def test_slssteam_pipe_gated_when_sls_missing_on_arm64(self):
        from utils.slssteam_integration import _is_slssteam_available

        os.environ["ASSELLA_FORCE_ARM64"] = "1"
        mock_env = MagicMock()
        mock_env.sls_installed.return_value = False

        with patch("core.steam_helpers.get_steam_env", return_value=mock_env):
            self.assertFalse(_is_slssteam_available())

    def test_bundled_dotnet_priority(self):
        from utils.helpers import get_dotnet_path, get_dotnet_env
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_dotnet = Path(tmp_dir) / ".dotnet"
            tmp_dotnet.mkdir()
            exe = tmp_dotnet / ("dotnet.exe" if sys.platform == "win32" else "dotnet")
            exe.write_text("#!/bin/sh\nexit 0\n")
            exe.chmod(0o755)

            with patch("utils.paths.Paths.BASE_DIR", Path(tmp_dir) / "src"), \
                 patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="Microsoft.NETCore.App 9.0.0")):
                found_path = get_dotnet_path()
                self.assertIsNotNone(found_path)
                self.assertEqual(os.path.realpath(found_path), os.path.realpath(str(exe)))

                env = get_dotnet_env()
                self.assertIn("DOTNET_ROOT", env)
                self.assertEqual(os.path.realpath(env["DOTNET_ROOT"]), os.path.realpath(str(tmp_dotnet)))

    def test_steam_detection_finds_steamrtarm64(self):
        from core.steam_helpers import _find_steam_linux
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_home:
            steam_dir = Path(tmp_home) / ".local" / "share" / "Steam"
            arm_dir = steam_dir / "steamrtarm64"
            arm_dir.mkdir(parents=True)
            (arm_dir / "steam").write_text("#!/bin/sh\n")

            with patch("os.path.expanduser", return_value=tmp_home):
                found = _find_steam_linux()
                self.assertIsNotNone(found)
                self.assertEqual(os.path.realpath(found), os.path.realpath(str(steam_dir)))

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


if __name__ == "__main__":
    unittest.main()
