#!/usr/bin/env python3
"""
scripts/test_sls_lua_config_and_dialogs.py
===========================================
Verification test suite for:
  1. SLS Lua Config reading and writing:
     - lobster.conf.lua parser & serializer
     - spacebunny.conf.lua parser & serializer
  2. Hubcap API Key validation & Bidirectional Sync:
     - is_valid_hubcap_key
     - sync_hubcap_api_key (timestamp comparison)
  3. Dialog theming and construction:
     - LobsterConfigDialog frameless themed layout
     - HubcapConfigDialog frameless themed layout
  4. SingleDepotSelectionDialog:
     - get_selected_dlc_data regression test
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure src/ is on PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from utils.settings import get_settings
from utils.sls_lua_config import (
    read_lobster_conf,
    write_lobster_conf,
    read_spacebunny_conf,
    write_spacebunny_conf,
    is_valid_hubcap_key,
    sync_hubcap_api_key,
)
from ui.dialogs.lobster_config_dialog import LobsterConfigDialog
from ui.dialogs.hubcap_config_dialog import HubcapConfigDialog
from ui.dialogs.single_depot_dialog import SingleDepotSelectionDialog


class TestSlsLuaConfigAndDialogs(unittest.TestCase):

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._tmp_dir.name
        try:
            from core.steam_helpers import invalidate_steam_env_cache
            invalidate_steam_env_cache()
        except Exception:
            pass

    def tearDown(self):
        if self._orig_xdg is not None:
            os.environ["XDG_CONFIG_HOME"] = self._orig_xdg
        else:
            os.environ.pop("XDG_CONFIG_HOME", None)
        try:
            from core.steam_helpers import invalidate_steam_env_cache
            invalidate_steam_env_cache()
        except Exception:
            pass
        self._tmp_dir.cleanup()

    def test_single_depot_dialog_dlc_method(self):
        """SingleDepotSelectionDialog must implement get_selected_dlc_data returning empty dict."""
        self.assertTrue(hasattr(SingleDepotSelectionDialog, "get_selected_dlc_data"))

    def test_lobster_config_read_write(self):
        """Verify lobster.conf.lua reading, modifying, and writing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conf_path = Path(tmpdir) / "lobster.conf.lua"

            # Default config when file does not exist
            cfg = read_lobster_conf(conf_path)
            self.assertTrue(cfg.get("enabled"))
            self.assertTrue(cfg.get("notify"))
            self.assertTrue(cfg.get("auto_recover"))
            self.assertFalse(cfg.get("run_on_reload"))

            # Write custom config
            cfg["notify"] = False
            cfg["run_on_reload"] = True
            ok = write_lobster_conf(cfg, conf_path)
            self.assertTrue(ok)
            self.assertTrue(conf_path.is_file())

            # Read back
            cfg2 = read_lobster_conf(conf_path)
            self.assertTrue(cfg2.get("enabled"))
            self.assertFalse(cfg2.get("notify"))
            self.assertTrue(cfg2.get("auto_recover"))
            self.assertTrue(cfg2.get("run_on_reload"))

    def test_spacebunny_config_read_write(self):
        """Verify spacebunny.conf.lua reading, modifying, and writing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conf_path = Path(tmpdir) / "spacebunny.conf.lua"

            cfg = read_spacebunny_conf(conf_path)
            self.assertFalse(cfg.get("standalone"))
            self.assertIn("hubcap", cfg)
            hub = cfg["hubcap"]
            self.assertTrue(hub.get("enabled"))
            self.assertEqual(hub.get("api_key"), "")

            # Modify hubcap table
            test_key = "abc12345-6789-def0-1234-56789abcdef0"
            test_ts = 1845242026
            hub["api_key"] = test_key
            hub["api_key_timestamp"] = test_ts
            hub["enabled"] = False
            ok = write_spacebunny_conf(cfg, conf_path)
            self.assertTrue(ok)
            self.assertTrue(conf_path.is_file())
            # File permission check (0600 on POSIX)
            mode = conf_path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

            # Read back
            cfg2 = read_spacebunny_conf(conf_path)
            self.assertFalse(cfg2["hubcap"]["enabled"])
            self.assertEqual(cfg2["hubcap"]["api_key"], test_key)
            self.assertEqual(cfg2["hubcap"]["api_key_timestamp"], test_ts)

    def test_hubcap_key_validation(self):
        """Verify Hubcap API key validator."""
        self.assertTrue(is_valid_hubcap_key("12345678-1234-1234-1234-123456789abc"))
        self.assertTrue(is_valid_hubcap_key("hubcap_live_token_1234567890"))
        self.assertFalse(is_valid_hubcap_key(""))
        self.assertFalse(is_valid_hubcap_key("short"))
        self.assertFalse(is_valid_hubcap_key("invalid key with spaces!"))

    def test_sync_hubcap_api_key(self):
        """Verify bidirectional timestamp-based synchronization."""
        settings = get_settings()
        orig_key = settings.value("morrenus_api_key", "", type=str)
        orig_ts = settings.value("morrenus_api_key_timestamp", 0, type=int)

        with tempfile.TemporaryDirectory() as tmpdir:
            sb_path = Path(tmpdir) / "spacebunny.conf.lua"
            try:
                # 1. Lua newer than settings -> sync to settings
                lua_key = "lua-newer-key-1234567890"
                lua_ts = 4518242026  # minute 45, hour 18, day 24, year 2026
                settings.setValue("morrenus_api_key", "old-key-1234567890")
                settings.setValue("morrenus_api_key_timestamp", "1012232026")  # day 23

                lua_cfg = {
                    "standalone": False,
                    "hubcap": {
                        "enabled": True,
                        "api_key": lua_key,
                        "api_key_timestamp": lua_ts,
                    }
                }
                write_spacebunny_conf(lua_cfg, sb_path)

                synced, msg = sync_hubcap_api_key(settings, sb_path)
                self.assertTrue(synced)
                self.assertIn("spacebunny.conf.lua to ASSella", msg)
                self.assertEqual(settings.value("morrenus_api_key"), lua_key)
                self.assertEqual(int(settings.value("morrenus_api_key_timestamp")), lua_ts)

                # 2. Settings newer than Lua -> sync to Lua
                local_key = "local-newer-key-9876543210"
                local_ts = 5519262026  # day 26
                settings.setValue("morrenus_api_key", local_key)
                settings.setValue("morrenus_api_key_timestamp", str(local_ts))

                synced2, msg2 = sync_hubcap_api_key(settings, sb_path)
                self.assertTrue(synced2)
                self.assertIn("ASSella to spacebunny.conf.lua", msg2)

                read_back = read_spacebunny_conf(sb_path)
                self.assertEqual(read_back["hubcap"]["api_key"], local_key)
                self.assertEqual(read_back["hubcap"]["api_key_timestamp"], local_ts)
            finally:
                settings.setValue("morrenus_api_key", orig_key)
                settings.setValue("morrenus_api_key_timestamp", orig_ts)

    def test_dialogs_instantiation(self):
        """Ensure LobsterConfigDialog and HubcapConfigDialog instantiate cleanly."""
        ldlg = LobsterConfigDialog()
        self.assertIn("enabled", ldlg.switches)
        self.assertIn("notify", ldlg.switches)

        hdlg = HubcapConfigDialog()
        self.assertIsNotNone(hdlg.key_input)
    def test_settings_dialog_tabs_initialization(self):
        """Ensure SettingsDialog loads all tabs without runtime import crashes and without touching live host config."""
        from unittest.mock import patch
        from ui.dialogs.settings import SettingsDialog

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_cfg = Path(tmpdir) / "config.yaml"
            fake_cfg.write_text("Plugins: yes\nAdditionalDepots:\nDecryptionKeys:\n", encoding="utf-8")
            with patch("utils.yaml_config_manager.get_user_config_path", return_value=fake_cfg), \
                 patch("utils.yaml.core.get_user_config_path", return_value=fake_cfg), \
                 patch("utils.plugin_games.get_user_config_path", return_value=fake_cfg):
                for tab_name in ("general", "downloads", "assella", "at0m", "morrenus", "tools", "advanced"):
                    dlg = SettingsDialog(initial_tab=tab_name)
                    self.assertIsNotNone(dlg)
                    dlg.close()

    def test_generate_hubcap_timestamp_chronological(self):
        """Verify that Hubcap timestamp is 14-digit YYYYMMDDHHMMSS, strictly monotonic, and sortable."""
        from utils.sls_lua_config import generate_hubcap_timestamp, _timestamp_to_sort_key
        ts = generate_hubcap_timestamp()
        s = str(ts)
        self.assertEqual(len(s), 14)
        self.assertTrue(s.startswith("202"))
        self.assertEqual(_timestamp_to_sort_key(ts), ts)

        # Legacy timestamps (e.g. day 23 vs day 24 in 2026) sort cleanly
        legacy_older = 1012232026
        legacy_newer = 4518242026
        self.assertLess(_timestamp_to_sort_key(legacy_older), _timestamp_to_sort_key(legacy_newer))
        # New 14-digit timestamps always sort newer than legacy 10-digit
        self.assertGreater(_timestamp_to_sort_key(ts), _timestamp_to_sort_key(legacy_newer))

    def test_atom_migration_excludes_unkeyed_depots(self):
        """AT0-M migration safely excludes unkeyed depots from AdditionalDepots to prevent Steam errors."""
        from unittest.mock import patch, MagicMock
        from utils.plugin_games import migrate_game_to_atom

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            cfg = tmp_p / "config.yaml"
            cfg.write_text("Plugins: yes\nAdditionalDepots:\nDecryptionKeys:\n", encoding="utf-8")
            acf = tmp_p / "appmanifest_12345.acf"
            acf.write_text('"AppState" {}\n', encoding="utf-8")

            game_data = {
                "appid": "12345",
                "game_name": "Test Unkeyed Game",
                "install_path": str(tmp_p),
                "appmanifest_path": str(acf),
            }
            settings = MagicMock()
            settings.value.return_value = False

            # Depots 1001 and 1002, but key ONLY provided for 1001 (1002 is unkeyed)
            mock_depots = ["1001", "1002"]
            mock_keys = {"1001": "a" * 64}

            with patch("utils.plugin_games.get_user_config_path", return_value=cfg), \
                 patch("utils.plugin_games.can_use_atom_mode", return_value=(True, "")), \
                 patch("utils.plugin_games.collect_game_depots_and_keys", return_value=(mock_depots, mock_keys, {})), \
                 patch("utils.plugin_games._remove_accela_markers") as remove_markers:
                res = migrate_game_to_atom(game_data, settings=settings)

            self.assertTrue(res["success"])
            self.assertIn("1002", res.get("excluded_depots", []))
            # Markers should be removed since keyed depots were migrated cleanly
            remove_markers.assert_called_once()
            cfg_text = cfg.read_text(encoding="utf-8")
            self.assertIn("1001", cfg_text)
            self.assertNotIn("1002", cfg_text)

    def test_collect_game_depots_filters_phantom_and_prefers_hubcap(self):
        """collect_game_depots_and_keys must prefer Hubcap and filter phantom/0-byte/manifestless depots on DB fallback."""
        from unittest.mock import patch, MagicMock
        from utils.plugin_games import collect_game_depots_and_keys

        # 1. Test fallback filters phantom depot (size: None, manifest_id: None)
        mock_db = MagicMock()
        mock_db.get_app_info.return_value = {
            "depots": {
                "400751": {"name": "RealContent", "size": "1000", "manifest_id": "111222333"},
                "2447597": {"name": "UnusedCzech", "size": None, "manifest_id": None},
            }
        }
        with patch("managers.db_manager.DatabaseManager", return_value=mock_db), \
             patch("managers.depot_key_manager.DepotKeyManager.get_instance") as mock_dkm_cls, \
             patch("pathlib.Path.exists", return_value=False):
            mock_dkm = MagicMock()
            mock_dkm.get_depot_keys.return_value = {"400751": "f" * 64}
            mock_dkm_cls.return_value = mock_dkm

            depots, keys, names = collect_game_depots_and_keys("400750", allow_hubcap_fetch=False)

            self.assertIn("400751", depots)
            self.assertNotIn("2447597", depots)
            self.assertEqual(keys.get("400751"), "f" * 64)

    def test_lua_string_escaping_roundtrip(self):
        """Ensure special characters in Lua config strings are escaped and roundtrip safely."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conf_path = Path(tmpdir) / "spacebunny.conf.lua"
            special_url = 'https://api.example.com/v1?query="val"&path=foo\\bar'
            special_preflight = 'preflight"quoted"\\backslash'
            cfg = {
                "standalone": False,
                "hubcap": {
                    "enabled": True,
                    "api_key": "valid_key_12345",
                    "api_key_timestamp": 123456,
                    "base_url": special_url,
                    "preflight": special_preflight,
                }
            }
            ok = write_spacebunny_conf(cfg, conf_path)
            self.assertTrue(ok)

            # Check raw file contents to ensure quotes and backslashes are escaped
            raw_text = conf_path.read_text(encoding="utf-8")
            self.assertIn('\\"val\\"', raw_text)
            self.assertIn('foo\\\\bar', raw_text)

            # Read back and verify exact unescaping
            read_back = read_spacebunny_conf(conf_path)
            self.assertEqual(read_back["hubcap"]["base_url"], special_url)
            self.assertEqual(read_back["hubcap"]["preflight"], special_preflight)

    def test_undeploy_plugin_preserves_bak(self):
        """Undeploying a plugin removes the active .lua file but preserves any .bak file."""
        from unittest.mock import patch
        from utils.plugin_manager import undeploy_plugin

        with tempfile.TemporaryDirectory() as tmpdir:
            p_dir = Path(tmpdir)
            active_lua = p_dir / "myplugin.lua"
            bak_lua = p_dir / "myplugin.lua.bak"

            active_lua.write_text("-- active code", encoding="utf-8")
            bak_lua.write_text("-- backup code", encoding="utf-8")

            with patch("utils.plugin_manager.get_sls_plugins_dirs", return_value=[p_dir]):
                ok, msg = undeploy_plugin("myplugin.lua")
                self.assertTrue(ok)
                self.assertFalse(active_lua.exists())
                self.assertTrue(bak_lua.exists())
                self.assertEqual(bak_lua.read_text(encoding="utf-8"), "-- backup code")

    def test_companion_plugins_manifest_filtering(self):
        """Companion plugins are derived from manifest and never treated as interceptors."""
        from utils.plugin_manager import (
            get_companion_plugin_filenames,
            is_interceptor_plugin,
            get_active_interceptor_filename,
        )
        from unittest.mock import patch

        companions = get_companion_plugin_filenames()
        self.assertIn("lobster.lua", companions)
        self.assertIn("hubcap.lua", companions)
        self.assertIn("spliced-tickets.lua", companions)

        self.assertFalse(is_interceptor_plugin("lobster.lua"))
        self.assertFalse(is_interceptor_plugin("hubcap.lua"))
        self.assertFalse(is_interceptor_plugin("spliced-tickets.lua"))
        self.assertTrue(is_interceptor_plugin("spacebunny.lua"))
        self.assertTrue(is_interceptor_plugin("download.lua"))
        # Custom unknown .lua defaults to interceptor
        self.assertTrue(is_interceptor_plugin("my_custom_interceptor.lua"))

        with tempfile.TemporaryDirectory() as tmpdir:
            p_dir = Path(tmpdir)
            (p_dir / "lobster.lua").write_text("code", encoding="utf-8")
            (p_dir / "spliced-tickets.lua").write_text("code", encoding="utf-8")
            (p_dir / "spacebunny.lua").write_text("code", encoding="utf-8")

            with patch("utils.plugin_manager.get_sls_plugins_dirs", return_value=[p_dir]):
                active = get_active_interceptor_filename()
                self.assertEqual(active, "spacebunny.lua")

    def test_0byte_plugin_handling(self):
        """0-byte files are treated as missing and overwritten rather than preserved."""
        from utils.plugin_manager import (
            get_plugin_status,
            is_plugin_installed_and_valid,
            deploy_plugin,
        )
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            p_dir = Path(tmpdir)
            zero_byte_file = p_dir / "spacebunny.lua"
            zero_byte_file.touch()
            self.assertEqual(zero_byte_file.stat().st_size, 0)

            with patch("utils.plugin_manager.get_sls_plugins_dirs", return_value=[p_dir]):
                # Status treats 0-byte file as missing
                self.assertEqual(get_plugin_status("spacebunny.lua"), "missing")
                self.assertFalse(is_plugin_installed_and_valid("spacebunny.lua"))

                # deploy with only_if_missing does not preserve 0-byte stub
                ok, skipped, msg = deploy_plugin("spacebunny.lua", only_if_missing=True)
                self.assertTrue(ok)
                # Did not skip as already present
                self.assertFalse(skipped)
                self.assertGreater(zero_byte_file.stat().st_size, 0)
                # 0-byte file was not backed up to .bak
                self.assertFalse((p_dir / "spacebunny.lua.bak").exists())


if __name__ == "__main__":
    unittest.main()
