"""Regression tests for NixOS/ARM64 Headcrab gating, NixOS SLSsteam detection, and config gating."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class TestNixOSAndGatingFixes(unittest.TestCase):
    def test_is_nixos_env_override(self):
        from utils.helpers import is_nixos

        with patch.dict(os.environ, {"ASSELLA_FORCE_NIXOS": "1"}):
            self.assertTrue(is_nixos())

    def test_add_launch_option_and_fake_appid_ignore_library_mode(self):
        from utils.yaml_config_manager import add_fake_app_id, add_launch_option, get_launch_option, get_fake_appid
        from utils.yaml import core as _core

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text("Plugins: yes\nLaunchOptions:\nFakeAppIds:\n", encoding="utf-8")

            with patch.object(_core, "is_slssteam_mode_enabled", return_value=False), \
                 patch.object(_core, "is_slssteam_config_management_enabled", return_value=True), \
                 patch("utils.yaml.fakeappid.is_slssteam_config_management_enabled", return_value=True):
                self.assertTrue(add_launch_option(cfg, "1174180", '"bash -c \'true\' -- %command%"'))
                self.assertIsNotNone(get_launch_option(cfg, "1174180"))
                self.assertTrue(add_fake_app_id(cfg, "1174180", "RDR2", "480"))
                self.assertEqual(get_fake_appid(cfg, "1174180"), "480")

            with patch.object(_core, "is_slssteam_config_management_enabled", return_value=False), \
                 patch("utils.yaml.fakeappid.is_slssteam_config_management_enabled", return_value=False):
                self.assertFalse(add_launch_option(cfg, "999", '"echo hi"'))
                self.assertFalse(add_fake_app_id(cfg, "999", "Test", "480"))

    def test_nix_store_mtime_skips_github_and_reports_up_to_date(self):
        from utils import slssteam_integration as sls

        with tempfile.TemporaryDirectory() as tmp:
            so_file = Path(tmp) / "SLSsteam.so"
            so_file.write_bytes(b"ELF")
            os.utime(so_file, (1, 1))

            with sls._binary_version_cache_lock:
                sls._binary_version_cache.clear()

            with patch("ui.dialogs.settings_sls.get_sls_paths", return_value={"so_path": str(so_file), "version_file": ""}), \
                 patch("urllib.request.urlopen", side_effect=AssertionError("Should not hit GitHub for Nix store binary")):
                res = sls.check_slssteam_binary_is_latest(force_refresh=True)
                self.assertEqual(res["status"], "up_to_date")
                self.assertEqual(res["release_tag"], "Nix-managed")

    def test_bwrap_eacces_falls_back_to_sls_log_matching_pid(self):
        from utils import slssteam_integration as sls

        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / ".SLSsteam.log"
            log_file.write_text(
                "[Info] SLSsteam loading in steam (12345)\n[Notify|Info] Loaded successfully\n",
                encoding="utf-8",
            )
            api_file = Path(tmp) / "SLSsteam.API"
            api_file.write_text("", encoding="utf-8")

            sls._proc_cache["last_check"] = 0.0
            real_open = open

            def fake_open(path, *args, **kwargs):
                if str(path) == "/proc/12345/comm":
                    return real_open(__file__, *args, **kwargs)  # replaced below
                if str(path) == "/proc/12345/maps":
                    raise PermissionError(13, "Permission denied")
                return real_open(path, *args, **kwargs)

            comm_file = Path(tmp) / "comm"
            comm_file.write_text("steam\n", encoding="utf-8")

            def fake_open2(path, *args, **kwargs):
                if str(path) == "/proc/12345/comm":
                    return real_open(comm_file, *args, **kwargs)
                if str(path) == "/proc/12345/maps":
                    raise PermissionError(13, "Permission denied")
                return real_open(path, *args, **kwargs)

            with patch.object(sls, "SLSSTEAM_API_PIPE", str(api_file)), \
                 patch.object(sls, "_get_sls_log_path", return_value=log_file), \
                 patch("os.listdir", return_value=["12345"]), \
                 patch("os.path.exists", side_effect=lambda p: True if str(p) in (str(api_file), "/proc/12345/maps") else os.path.lexists(p)), \
                 patch("builtins.open", side_effect=fake_open2):
                self.assertTrue(sls.is_slssteam_process_active())

            # Stale log with a different PID must NOT match
            log_file.write_text(
                "[Info] SLSsteam loading in steam (99999)\n[Notify|Info] Loaded successfully\n",
                encoding="utf-8",
            )
            sls._proc_cache["last_check"] = 0.0
            with patch.object(sls, "SLSSTEAM_API_PIPE", str(api_file)), \
                 patch.object(sls, "_get_sls_log_path", return_value=log_file), \
                 patch("os.listdir", return_value=["12345"]), \
                 patch("os.path.exists", side_effect=lambda p: True if str(p) in (str(api_file), "/proc/12345/maps") else os.path.lexists(p)), \
                 patch("builtins.open", side_effect=fake_open2):
                self.assertFalse(sls.is_slssteam_process_active())

    def test_health_tab_disables_crab_on_nixos_and_runs_headcrab_on_other_distros(self):
        from PyQt6.QtWidgets import QApplication, QDialog, QFrame, QTabWidget, QVBoxLayout, QWidget
        from ui.dialogs.settings_tabs import health_tab

        app = QApplication.instance() or QApplication([])

        class _FakeSettings:
            def value(self, key, default=None, type=None):
                return default

        class _FakeDialog(QDialog):
            def __init__(self):
                super().__init__()
                self.accent_color = "#ff6b35"
                self.settings = _FakeSettings()
                self.tab_widget = QTabWidget()
                self.tab_widget.addTab(QWidget(), "Health")
                self.tab_widget.addTab(QWidget(), "SLS")

            def _create_card_frame(self, title=""):
                frame = QFrame()
                lay = QVBoxLayout(frame)
                return frame, lay

        dlg = _FakeDialog()

        with patch("utils.helpers.is_nixos", return_value=True), \
             patch("utils.helpers.is_arm64", return_value=False), \
             patch("ui.dialogs.settings_tabs.health_tab.refresh_health_tab_status"):
            health_tab.create_health_tab(dlg)
            self.assertFalse(dlg.health_h3adcrab_btn.isEnabled())
            self.assertFalse(dlg.health_r3set_crab_btn.isEnabled())

        with patch("utils.helpers.is_nixos", return_value=False), \
             patch("utils.helpers.is_arm64", return_value=False), \
             patch("ui.dialogs.settings_sls.get_sls_paths", return_value={"detected": False}), \
             patch("ui.dialogs.settings_tabs.health_tab.on_h3adcrab_clicked") as mock_hc:
            health_tab.on_sls_bin_tile_clicked(dlg)
            mock_hc.assert_called_once_with(dlg)
            self.assertEqual(dlg.tab_widget.currentIndex(), 0)


if __name__ == "__main__":
    unittest.main()
