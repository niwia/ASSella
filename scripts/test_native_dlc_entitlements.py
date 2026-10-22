#!/usr/bin/env python3
"""Regression tests for full-game DLC entitlements and same-ID content depots."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

if __name__ == "__main__":
    unittest.main()
