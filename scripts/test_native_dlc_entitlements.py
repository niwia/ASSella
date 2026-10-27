#!/usr/bin/env python3
"""Regression tests for full-game DLC entitlements and same-ID content depots."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


class TestNativeDlcEntitlements(unittest.TestCase):
    def test_full_game_adds_dlc_entitlement_and_same_id_content(self):
        from core.tasks.native_steam_download_task import NativeSteamDownloadTask
        from utils.yaml_config_manager import (
            get_additional_apps,
            get_additional_depots,
            get_decryption_keys,
        )

        parent, dlc, other_depot = "1324780", "1888350", "1324781"
        dlc_key = "a" * 64
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.yaml"
            config.write_text(
                "Plugins: yes\nAdditionalApps:\nAdditionalDepots:\nDecryptionKeys:\n",
                encoding="utf-8",
            )
            task = NativeSteamDownloadTask()
            self.assertTrue(task._patch_config(
                config,
                parent,
                "Test Game",
                {parent: "b" * 64, dlc: dlc_key, other_depot: "c" * 64},
                selected_depots=[dlc, other_depot],
                game_data={
                    "appid": parent,
                    "game_name": "Test Game",
                    "dlcs": {dlc: "DLC with content"},
                    "depots": {
                        dlc: {"desc": "DLC content"},
                        other_depot: {"desc": "Base content"},
                    },
                },
                dlc_appids=[dlc],
            ))

            self.assertEqual(set(get_additional_apps(config)), {parent, dlc})
            self.assertEqual(set(get_additional_depots(config)), {dlc, other_depot})
            keys = get_decryption_keys(config)
            self.assertEqual(keys[dlc], dlc_key)
            self.assertNotIn(parent, get_additional_depots(config))

    def test_registration_keeps_full_game_mode_with_dlc_ids(self):
        import utils.plugin_games as plugin_games
        from utils.yaml_config_manager import get_additional_apps, get_additional_depots

        parent, dlc = "1324780", "1888350"
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.yaml"
            config.write_text(
                "Plugins: yes\nAdditionalApps:\nAdditionalDepots:\nDecryptionKeys:\n",
                encoding="utf-8",
            )
            library = {}
            with (
                patch.object(plugin_games, "get_user_config_path", return_value=config),
                patch.object(plugin_games, "load_plugin_library", return_value=library),
                patch.object(plugin_games, "save_plugin_library", return_value=True),
            ):
                self.assertTrue(plugin_games.register_plugin_game(
                    appid=parent,
                    name="Test Game",
                    depot_ids=[dlc],
                    decryption_keys={dlc: "a" * 64},
                    dlc_appids=[dlc],
                    is_dlc_only=False,
                ))
            self.assertFalse(library[parent]["dlc_only"])
            self.assertEqual(set(get_additional_apps(config)), {parent, dlc})
            self.assertEqual(set(get_additional_depots(config)), {dlc})

    def test_sync_dlc_only_sls_config_regular_mode_writes_selected_dlcs(self):
        from utils.dlc_helpers import sync_dlc_only_sls_config
        from utils.yaml_config_manager import get_additional_apps

        parent, dlc, unselected_dlc = "1324780", "1888350", "2317930"
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.yaml"
            config.write_text(f"AdditionalApps:\n  - {unselected_dlc} # Old\n", encoding="utf-8")
            game_data = {
                "appid": parent,
                "game_name": "Test Game",
                "selected_depots_list": [dlc, "1324781"],
                "dlcs": {dlc: "Selected DLC", unselected_dlc: "Unselected DLC"},
                "depots": {
                    dlc: {"dlcappid": dlc, "desc": "Selected DLC"},
                    unselected_dlc: {"dlcappid": unselected_dlc, "desc": "Unselected DLC"},
                    "1324781": {"desc": "Base Game"},
                },
            }
            self.assertTrue(sync_dlc_only_sls_config(config, parent, "Test Game", game_data=game_data))
            apps = set(get_additional_apps(config))
            self.assertIn(parent, apps)
            self.assertIn(dlc, apps)
            self.assertNotIn(unselected_dlc, apps)

    def test_sync_dlc_only_sls_config_respects_unchecked_depotless_dlcs(self):
        from utils.dlc_helpers import sync_dlc_only_sls_config
        from utils.yaml_config_manager import get_additional_apps

        parent, selected_dlc, unchecked_dlc = "1324780", "1888350", "2317930"
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.yaml"
            config.write_text("AdditionalApps:\n", encoding="utf-8")
            game_data = {
                "appid": parent,
                "game_name": "Test Game",
                "selected_depots_list": ["1324781"],
                "selected_dlc_appids": [selected_dlc],
                "dlcs": {
                    selected_dlc: "Selected DLC",
                    unchecked_dlc: "Unchecked DLC",
                },
                "depots": {"1324781": {"desc": "Base Game"}},
            }
            self.assertTrue(sync_dlc_only_sls_config(
                config, parent, "Test Game", game_data=game_data
            ))
            apps = set(get_additional_apps(config))
            self.assertIn(parent, apps)
            self.assertIn(selected_dlc, apps)
            self.assertNotIn(unchecked_dlc, apps)

    def test_finalize_sync_does_not_require_stale_sls_mode_flag(self):
        from managers.finalize.post_install import PostInstallMixin

        class Finalizer(PostInstallMixin):
            def _set_linux_binary_permissions(self):
                pass

        finalizer = Finalizer()
        finalizer.slssteam_mode_was_active = False
        with patch("managers.finalize.post_install.sys.platform", "linux"), \
             patch.object(finalizer, "_add_appids_to_slssteam_config") as sync:
            finalizer._finalize_platform_specifics(config_enabled=True)
    def test_task_manager_copies_preselected_dlc_appids(self):
        from PyQt6.QtCore import QObject
        from managers.task_manager import TaskManager
        tm = TaskManager.__new__(TaskManager)
        QObject.__init__(tm)
        tm.main_window = MagicMock()
        tm.main_window.simplified_terminal = None
        tm.main_window.progress_bar = MagicMock()
        tm.current_job_metadata = {
            "selected_depots_list": ["1324781"],
            "selected_dlc_appids": ["1888350"],
            "library_path": "/tmp/test",
        }
        tm.settings = MagicMock()
        tm._start_download_with_destination = MagicMock()
        tm._on_zip_processed({
            "appid": "1324780",
            "game_name": "Test Game",
            "depots": {"1324781": {"desc": "Base"}},
        })
        self.assertEqual(tm.game_data.get("selected_depots_list"), ["1324781"])
        self.assertEqual(tm.game_data.get("selected_dlc_appids"), ["1888350"])

    def test_parent_appid_never_enters_additional_depots(self):
        from utils.yaml.core import batch_config_edit
        from utils.yaml_config_manager import get_additional_depots

        parent = "1324780"
        other_parent = "1145350"
        dlc_depot = "1888350"
        base_depot = "1324781"

        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.yaml"
            cfg.write_text(
                f"Plugins: yes\nAdditionalApps:\n  - {parent} # Base Game\n  - {other_parent} # Other Game\n  - {dlc_depot} # [DLC] Stalingrad / Base Game\nAdditionalDepots:\n",
                encoding="utf-8"
            )

            with batch_config_edit(cfg) as editor:
                # 1. Direct parent AppID must be refused
                self.assertFalse(editor.add_depot(parent, app_id=parent))
                # 2. Another game's parent AppID from AdditionalApps must be refused
                self.assertFalse(editor.add_depot(other_parent, app_id=parent))
                # 3. Legitimate child DLC depot must be accepted
                self.assertTrue(editor.add_depot(dlc_depot, app_id=parent))
                # 4. Legitimate content depot must be accepted
                self.assertTrue(editor.add_depot(base_depot, app_id=parent))

            depots = set(get_additional_depots(cfg))
            self.assertNotIn(parent, depots)
            self.assertNotIn(other_parent, depots)
            self.assertIn(dlc_depot, depots)
            self.assertIn(base_depot, depots)

if __name__ == "__main__":
    unittest.main()


