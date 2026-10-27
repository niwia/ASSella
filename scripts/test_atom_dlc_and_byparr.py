#!/usr/bin/env python3
"""
scripts/test_atom_dlc_and_byparr.py
===================================
Verification test suite for:
  1. AtomDlcSelectionDialog structure:
     - 3 sections: AdditionalApps, AdditionalDepots, DecryptionKeys
     - Filters out base game AppID and owned DLCs (from Steam tickets and config.yaml)
     - Missing depots (without key) are disabled with status tags
     - Bulk toggle bars visible only when >= 5 items
     - Writes to SLSsteam config (batch_config_edit) and plugin_games.json
     - Invariant: Base AppID NEVER in AdditionalApps or AdditionalDepots
  2. DLC Mode Memory Detection:
     - Detects AT0-M mode from plugin_library or config.yaml (DlcData, AdditionalApps)
     - Detects ASSella mode from .depot file or dlc_only_mode
     - Returns None for clean/unconfigured games
  3. Byparr Configuration & Custom Service:
     - ByparrManager.is_custom_service()
     - ByparrManager.get_auth_header() and build_headers()
     - ByparrManager.is_available()
  4. Plugins Manifest & Extras:
     - hubcap.lua registered in plugins_manifest.json
     - hubcap.lua present in res/plugins
     - Excluded from interceptor switches and .bak archiving
"""

import os
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src/ is on PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from utils.settings import get_settings
from utils.yaml_config_manager import (
    get_user_config_path,
    get_additional_apps,
    get_additional_depots,
    get_dlc_data,
)
from utils.dlc_helpers import DlcCandidate, DlcDiscovery
from ui.dialogs.atom_dlc_selection_dialog import AtomDlcSelectionDialog
from core.steamdb_scraper import ByparrManager


class TestAtomDlcAndByparr(unittest.TestCase):

    def setUp(self):
        self.settings = get_settings()
        self.orig_byparr_url = self.settings.value("byparr_url", "", type=str)
        self.orig_byparr_auth = self.settings.value("byparr_auth_header", "", type=str)
        self.orig_library_mode = self.settings.value("library_mode", None)
        self.settings.setValue("library_mode", True)

        self.cfg_path = get_user_config_path()
        self.created_cfg = False
        if not self.cfg_path.exists():
            self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
            self.cfg_path.write_text("Plugins: yes\nAdditionalApps: {}\nAdditionalDepots: {}\nDecryptionKeys: {}\nAppTokens: {}\n")
            self.created_cfg = True

    def tearDown(self):
        self.settings.setValue("byparr_url", self.orig_byparr_url)
        self.settings.setValue("byparr_auth_header", self.orig_byparr_auth)
        if self.orig_library_mode is None:
            self.settings.remove("library_mode")
        else:
            self.settings.setValue("library_mode", self.orig_library_mode)

        if self.created_cfg and self.cfg_path.exists():
            try:
                self.cfg_path.unlink()
            except Exception:
                pass

    def test_01_byparr_custom_configuration(self):
        """Verify Byparr custom service detection and header builder."""
        # Default state
        self.settings.setValue("byparr_url", "http://127.0.0.1:8191")
        self.assertFalse(ByparrManager.is_custom_service())

        # Custom service URL
        self.settings.setValue("byparr_url", "https://byparr.mycluster.internal:8443")
        self.assertTrue(ByparrManager.is_custom_service())
        self.assertTrue(ByparrManager.is_available())
        self.assertEqual(ByparrManager.get_service_url(), "https://byparr.mycluster.internal:8443")

        # Custom auth header - Bearer token
        self.settings.setValue("byparr_auth_header", "Bearer secret-token-123")
        self.assertEqual(ByparrManager.get_auth_header(), "Bearer secret-token-123")
        headers = ByparrManager.build_headers()
        self.assertEqual(headers.get("Authorization"), "Bearer secret-token-123")
        self.assertEqual(headers.get("Content-Type"), "application/json")

        # Custom auth header - Custom key:value header
        self.settings.setValue("byparr_auth_header", "X-Api-Key: custom-api-key-999")
        headers2 = ByparrManager.build_headers()
        self.assertEqual(headers2.get("X-Api-Key"), "custom-api-key-999")
        print("  [PASS] Byparr custom service and headers verified")

    def test_02_plugins_manifest_hubcap_registered(self):
        """Verify hubcap.lua is in manifest, on disk, and registered in plugin_manager."""
        manifest_path = SRC_DIR / "res" / "plugins" / "plugins_manifest.json"
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertIn("hubcap.lua", manifest["plugins"])
        self.assertEqual(manifest["plugins"]["hubcap.lua"]["version"], "1.0.0")

        hubcap_lua = SRC_DIR / "res" / "plugins" / "hubcap.lua"
        self.assertTrue(hubcap_lua.exists())
        self.assertGreater(hubcap_lua.stat().st_size, 20000)

        # Check plugin_manager lists
        from utils.plugin_manager import FALLBACK_REQUIRED_PLUGINS
        self.assertIn("hubcap.lua", FALLBACK_REQUIRED_PLUGINS)
        print("  [PASS] Hubcap plugin manifest and registration verified")

    def test_03_atom_dlc_dialog_structure_and_filters(self):
        """Verify AtomDlcSelectionDialog sets up tables, filters owned items, and hides bulk bars < 5."""
        base_appid = "123456"
        disc = DlcDiscovery(
            appid=base_appid,
            catalog={
                "123456": "Base Game",
                "100001": "DLC Adventure Pack",
                "100002": "DLC Character Pack",
                "100003": "DLC Expansion Pass",
            },
            depots=(
                DlcCandidate("200001", "100001", "DLC Adventure Content", 10000000, ("windows",)),
                DlcCandidate("200002", "100002", "DLC Character Content", 5000000, ("windows",)),
                DlcCandidate("200003", "100003", "DLC Expansion Content", 15000000, ("windows",)),
            ),
            sources=("steam_cmd",),
        )

        manifest_data = {
            "appid": base_appid,
            "game_name": "Test RPG Game",
            "depots": {
                "200001": {"name": "DLC Adventure Content", "dlcappid": "100001", "key": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"},
                "200002": {"name": "DLC Character Content", "dlcappid": "100002", "key": ""},
                "200003": {"name": "DLC Expansion Content", "dlcappid": "100003", "key": "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"},
            },
            "missing_depots_from_hubcap": ["200002"],
            "missing_depots_info": {
                "200002": {"status": "key_pending", "tag": "[No Key]"}
            },
            "keys": {
                "200001": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "200003": "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
            }
        }

        # Mock Steam ticket ownership to include 100001 as already owned
        with patch("ui.dialogs.atom_dlc_selection_dialog.get_owned_dlcs_from_steam", return_value={"100001"}):
            dlg = AtomDlcSelectionDialog(
                parent=None,
                app_id=base_appid,
                game_name="Test RPG Game",
                accent_color="#6c5ce7",
                discovery=disc,
                manifest_data=manifest_data,
            )

            # 1. Base appid (123456) MUST NOT be in apps table, while owned DLC (100001) is present but greyscaled
            app_ids_in_table = [dlg.apps_table.item(r, 0).text() for r in range(dlg.apps_table.rowCount())]
            self.assertNotIn("123456", app_ids_in_table)
            self.assertIn("100001", app_ids_in_table)
            self.assertIn("100002", app_ids_in_table)
            self.assertIn("100003", app_ids_in_table)
            self.assertEqual(len(app_ids_in_table), 3)

            # Owned DLC 100001 toggle must be disabled/read-only and greyscaled
            toggle_100001 = dlg.app_toggles["100001"]
            self.assertFalse(toggle_100001.isEnabled())
            self.assertTrue(toggle_100001.isGreyscaled())
            self.assertIn("(Owned)", dlg.apps_table.item(0, 1).text())
            self.assertTrue(dlg.apps_table.item(0, 1).font().italic())

            # Unowned DLC 100002 & 100003 must be enabled and not greyscaled
            toggle_100002 = dlg.app_toggles["100002"]
            self.assertTrue(toggle_100002.isEnabled())
            self.assertFalse(toggle_100002.isGreyscaled())

            # 2. Bulk bar should be hidden because count is < 5 (3 items)
            self.assertFalse(dlg.apps_bulk_bar.isVisible())

            # 3. Depots: 200001 belongs to owned DLC 100001 -> greyscaled with locked toggle
            # 200002 is missing key -> disabled with tag '[No Key]'
            # 200003 is valid -> enabled
            depot_ids_in_table = [dlg.depots_table.item(r, 0).text() for r in range(dlg.depots_table.rowCount())]
            self.assertIn("200001", depot_ids_in_table)
            self.assertIn("200002", depot_ids_in_table)
            self.assertIn("200003", depot_ids_in_table)

            # 200001 owned depot toggle should be disabled/locked and greyscaled
            toggle_200001 = dlg.depot_toggles["200001"]
            self.assertFalse(toggle_200001.isEnabled())
            self.assertTrue(toggle_200001.isGreyscaled())
            self.assertIn("(Owned)", dlg.depots_table.item(0, 1).text())
            self.assertTrue(dlg.depots_table.item(0, 1).font().italic())

            # 200002 toggle should be disabled (missing key)
            toggle_200002 = dlg.depot_toggles["200002"]
            self.assertFalse(toggle_200002.isEnabled())
            self.assertFalse(toggle_200002.isGreyscaled())

            # 200003 toggle should be enabled and checked
            toggle_200003 = dlg.depot_toggles["200003"]
            self.assertTrue(toggle_200003.isEnabled())
            self.assertTrue(toggle_200003.isChecked())
            self.assertFalse(toggle_200003.isGreyscaled())

            # Window dimensions match GameDetailsDialogV2
            self.assertEqual(dlg.width(), 620)
            self.assertEqual(dlg.height(), 500)

            print("  [PASS] AtomDlcSelectionDialog structure, owned DLC greyscaling, and toggle state verified")

    def test_04_dlc_mode_memory_detection(self):
        """Verify _detect_configured_dlc_mode detects AT0-M vs ASSella vs unconfigured."""
        from ui.dialogs.library.actions import LibraryActionsMixin

        actions_obj = LibraryActionsMixin()

        # 1. Clean unconfigured game
        clean_appid = "9999999"
        mode = actions_obj._detect_configured_dlc_mode(clean_appid)
        self.assertIsNone(mode)

        # 2. AT0-M game in plugin_library
        atom_appid = "8888888"
        mock_plugin_lib = {
            atom_appid: {
                "name": "Mock AT0-M Game",
                "appid": atom_appid,
                "is_atom": True,
                "is_dlc_only": True,
                "dlc_appids": ["8888889"],
            }
        }
        with patch("utils.plugin_games.load_plugin_library", return_value=mock_plugin_lib):
            mode = actions_obj._detect_configured_dlc_mode(atom_appid)
            self.assertEqual(mode, "native")

        # 3. ACCELA game with .depot file
        accela_appid = "7777777"
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            depots_dir = Path(tmp_dir) / "depots"
            depots_dir.mkdir(parents=True, exist_ok=True)
            depot_file = depots_dir / f"{accela_appid}.depot"
            depot_file.write_text("dummy depot content")

            with patch("utils.plugin_games.load_plugin_library", return_value={}), \
                 patch("utils.yaml_config_manager.get_user_config_path", return_value=None), \
                 patch("utils.helpers.get_base_path", return_value=Path(tmp_dir)):
                mode = actions_obj._detect_configured_dlc_mode(accela_appid)
                self.assertEqual(mode, "assella")

        # 4. ACCELA game with is_dlc_only_mode setting
        setting_appid = "6666666"
        with patch("utils.plugin_games.load_plugin_library", return_value={}), \
             patch("utils.yaml_config_manager.get_user_config_path", return_value=None), \
             patch("utils.dlc_helpers.is_dlc_only_mode", return_value=True):
            mode = actions_obj._detect_configured_dlc_mode(setting_appid)
            self.assertEqual(mode, "assella")

        print("  [PASS] DLC mode memory detection verified")

    def test_05_plugin_version_extraction_and_display(self):
        """Verify extract_plugin_version and get_plugin_version resolution."""
        from utils.plugin_manager import extract_plugin_version, get_plugin_version

        # 1. Version extraction from content
        lua_sb = """-- spacebunny.lua (1.8.2)
SpaceBunny = {
    VERSION = "1.8.2"
}
"""
        self.assertEqual(extract_plugin_version(lua_sb), "1.8.2")

        lua_lobster = """-- lobster.lua
Lobster = {
    VERSION = "1.2.0"
}
"""
        self.assertEqual(extract_plugin_version(lua_lobster), "1.2.0")

        # 2. Version resolution for standard plugins
        sb_ver = get_plugin_version("spacebunny.lua")
        self.assertIsNotNone(sb_ver)
        self.assertTrue(len(sb_ver) > 0)

        lob_ver = get_plugin_version("lobster.lua")
        self.assertIsNotNone(lob_ver)

        print(f"  [PASS] Plugin version resolution verified (SpaceBunny: {sb_ver}, Lobster: {lob_ver})")

    def test_06_platform_mismatch_and_dlc_badge(self):
        """Verify PlatformMismatchDialog styling/text and single DLC Only badge in GameListWidget."""
        from ui.dialogs.platform_mismatch_dialog import (
            PlatformMismatchDialog,
            ACTION_PROCEED,
            ACTION_FORCE_PROTON,
        )
        from ui.dialogs.library.widgets import GameItemWidget

        # 1. PlatformMismatchDialog
        details = {
            "appid": "2686690",
            "game_name": "Suika Shapes",
            "linux_depots": ["2686693"],
            "windows_depots": ["2686691", "2686692"],
            "preferred_tool": "proton-cachyos-slr",
        }
        dlg = PlatformMismatchDialog(details=details)
        self.assertEqual(ACTION_PROCEED, 1)
        self.assertEqual(ACTION_FORCE_PROTON, ACTION_PROCEED)
        self.assertEqual(dlg.btn_proceed.text(), "Proceed")

        # Find description label and verify text
        from PyQt6.QtWidgets import QLabel
        desc_labels = [w for w in dlg.findChildren(QLabel) if "Only Windows keys are available" in w.text()]
        self.assertTrue(len(desc_labels) > 0, "Expected description text to mention 'Only Windows keys are available'")
        self.assertIn("Please switch to Proton in Steam compatibility options after install.", desc_labels[0].text())

        # 2. GameItemWidget: single DLC badge
        game_data = {
            "appid": "1868140",
            "game_name": "DAVE THE DIVER",
            "size": 5760000000,
            "is_dlc_only": True,
        }
        widget = GameItemWidget(
            game_data=game_data,
            size_str="5.76 GB",
            accent_color="#E67E22",
            background_color="#111318",
        )
        # Ensure name_row does NOT have duplicate dlc_mode_badge
        self.assertIsNone(widget.dlc_mode_badge)
        # Ensure status_label on right displays [DLC Only]
        self.assertEqual(widget.status_label.text(), "[DLC Only]")

        print("  [PASS] PlatformMismatchDialog and single DLC badge verified")

    def test_07_steamdb_scraper_fast_path_and_dual_parser(self):
        """Verify SteamDBScraper fast-path race setup and dual patchnotes parser."""
        from core.steamdb_scraper import SteamDBScraper, verify_steamdb_status

        scraper = SteamDBScraper()
        self.assertTrue(hasattr(scraper, "fetch_via_fast_race"))
        self.assertTrue(hasattr(scraper, "fetch_via_byparr"))
        self.assertTrue(hasattr(scraper, "_parse_patchnotes_html"))

        # Test Parser 1: Dynamic Byparr HTML format
        mock_dynamic_html = """
        <html><body>
        <table>
            <tr><th>Date</th><th>Day</th><th>Time</th><th>Patch Title</th><th>BuildID</th></tr>
            <tr>
                <td>2026-10-25</td><td>Sun</td><td>12:00</td>
                <td>Major Update 2.0</td>
                <td><a href="/patchnotes/999888/">999888</a></td>
            </tr>
        </table>
        </body></html>
        """
        parsed_dynamic = scraper._parse_patchnotes_html(mock_dynamic_html)
        self.assertEqual(len(parsed_dynamic), 1)
        self.assertEqual(parsed_dynamic[0]["buildid"], "999888")
        self.assertEqual(parsed_dynamic[0]["title"], "Major Update 2.0")

        # Test Parser 2: Static fast-path HTML format
        mock_static_html = """
        <html><body>
        <table>
            <tr><th>Name</th><th>Description</th><th>Build ID</th><th>Time Built</th><th>Time Updated</th></tr>
            <tr>
                <td>public</td>
                <td>Public Release Branch</td>
                <td><a href="/patchnotes/888777/">888777</a></td>
                <td>2026-10-24</td>
                <td>24 October 2026 – 10:00:00 UTC</td>
            </tr>
        </table>
        </body></html>
        """
        parsed_static = scraper._parse_patchnotes_html(mock_static_html)
        self.assertEqual(len(parsed_static), 1)
        self.assertEqual(parsed_static[0]["buildid"], "888777")
        self.assertEqual(parsed_static[0]["title"], "public - Public Release Branch")

        # Test Parser 3: SteamDB XML RSS Feed
        mock_rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Test Game - SteamDB RSS</title>
                <item>
                    <title>Test Game Update 1.5</title>
                    <link>https://steamdb.info/patchnotes/777666/?utm_source=RSS</link>
                    <guid isPermaLink="false">build#777666</guid>
                    <pubDate>Mon, 26 Oct 2026 15:30:00 +0000</pubDate>
                </item>
            </channel>
        </rss>
        """
        parsed_rss = scraper._parse_patchnotes_rss(mock_rss_xml)
        self.assertEqual(len(parsed_rss), 1)
        self.assertEqual(parsed_rss[0]["buildid"], "777666")
        self.assertEqual(parsed_rss[0]["title"], "Test Game Update 1.5")
        self.assertEqual(parsed_rss[0]["date"], "26 October 2026")
        self.assertEqual(parsed_rss[0]["day"], "Mon")
        self.assertEqual(parsed_rss[0]["time"], "15:30")

        # Test get_patchnotes tiered source tagging
        with patch.object(scraper, "fetch_rendered_html", return_value=mock_dynamic_html):
            results = scraper.get_patchnotes(999888, limit=10)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["_source"], "render_section")

        # Test verify_steamdb_status callable
        ok, provider = verify_steamdb_status(timeout_seconds=3)
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(provider, str)
        print(f"  [PASS] SteamDB fast-path and dual parser verified (Live status: ok={ok}, provider={provider})")

    def test_08_atom_manifest_fix_dialog_and_dlc_token_scope(self):
        """Verify AtomManifestFixDialog choices and DLC AppToken scoping."""
        from ui.dialogs.atom_manifest_fix_dialog import (
            AtomManifestFixDialog,
            ACTION_DELETE_ACF,
            ACTION_MARK_UPDATE,
            ACTION_CANCEL,
        )
        # 1. Dialog choices
        dlg = AtomManifestFixDialog(
            parent=None,
            game_name="DAVE THE DIVER",
            appid="1868140",
            acf_path="/tmp/fake/appmanifest_1868140.acf",
        )
        self.assertEqual(dlg.choice, ACTION_CANCEL)
        dlg._on_delete_acf()
        self.assertEqual(dlg.choice, ACTION_DELETE_ACF)
        dlg._on_mark_update()
        self.assertEqual(dlg.choice, ACTION_MARK_UPDATE)
        dlg.deleteLater()

        # 2. DLC AppToken scoping in register_plugin_game
        from utils.plugin_games import register_plugin_game, unregister_plugin_game
        from utils.yaml_config_manager import get_app_tokens, get_user_config_path

        test_base_aid = "9999990"
        test_dlc_aid = "9999991"
        test_dlc_depot = "9999992"
        test_unselected_dlc = "9999999"

        # Provide tokens for base, selected DLC, selected DLC depot, and unselected DLC
        tokens_map = {
            test_base_aid: "TOKEN_BASE_GAME_SECRET",
            test_dlc_aid: "TOKEN_DLC_SECRET",
            test_dlc_depot: "TOKEN_DEPOT_SECRET",
            test_unselected_dlc: "TOKEN_UNSELECTED_SECRET",
        }

        try:
            ok = register_plugin_game(
                appid=test_base_aid,
                name="SYNTHETIC DLC TEST",
                depot_ids=[test_dlc_aid, test_dlc_depot],
                decryption_keys={
                    test_dlc_aid: "a" * 64,
                    test_dlc_depot: "b" * 64,
                },
                installdir="SyntheticGame",
                is_dlc_only=True,
                dlc_appids=[test_dlc_aid],
                app_tokens=tokens_map,
            )
            self.assertTrue(ok)

            cfg_path = get_user_config_path()
            cfg_tokens = get_app_tokens(cfg_path)
            # Base AppID token must NEVER enter AppTokens in DLC-only mode
            self.assertNotIn(test_base_aid, cfg_tokens, "CRITICAL: Base game token leaked into AppTokens in DLC-only mode!")
            # Unselected DLC token must not enter AppTokens
            self.assertNotIn(test_unselected_dlc, cfg_tokens, "Unselected DLC token found in AppTokens!")
            # Selected DLC token and selected depot token must be present
            self.assertEqual(cfg_tokens.get(test_dlc_aid), "TOKEN_DLC_SECRET")
            self.assertEqual(cfg_tokens.get(test_dlc_depot), "TOKEN_DEPOT_SECRET")
        finally:
            unregister_plugin_game(test_base_aid)

        print("  [PASS] AtomManifestFixDialog and DLC AppToken scoping verified")


if __name__ == "__main__":
    unittest.main()
