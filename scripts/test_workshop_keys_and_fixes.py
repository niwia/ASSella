#!/usr/bin/env python3
"""
Test suite for Steam Workshop AppKey indicators, complications, depot extractor,
and Cached Tab CPU fix.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)


class TestUniversalDepotExtractor(unittest.TestCase):
    def test_extract_depot_ids(self):
        from utils.depot_utils import extract_depot_ids

        # Empty / None
        self.assertEqual(extract_depot_ids(None), set())
        self.assertEqual(extract_depot_ids({}), set())
        self.assertEqual(extract_depot_ids([]), set())
        self.assertEqual(extract_depot_ids(()), set())

        # Dict input
        d_dict = {"100": {"name": "test"}, 200: {}, "": None}
        self.assertEqual(extract_depot_ids(d_dict), {"100", "200"})

        # List input (prevents 'list' object has no attribute 'keys')
        d_list = [100, "200", 300, "", " "]
        self.assertEqual(extract_depot_ids(d_list), {"100", "200", "300"})

        # Set / tuple input
        self.assertEqual(extract_depot_ids({400, "500"}), {"400", "500"})
        self.assertEqual(extract_depot_ids((600, 700)), {"600", "700"})

    def test_safe_call_sites_with_list_depots(self):
        from utils.yaml.keys import has_game_decryption_keys
        from utils.yaml.apps import has_game_config_entries
        from utils.dlc_helpers import filter_dlc_depots_only
        from core.native_steam.steam_manifest_pinning import resolve_pinned_manifests

        # Should never raise AttributeError: 'list' object has no attribute 'keys' or 'get' or 'items'
        res1 = has_game_decryption_keys("12345", game_data={"depots": [12346, 12347]})
        self.assertIsInstance(res1, bool)

        res2 = has_game_config_entries("12345", game_data={"depots": [12346, 12347], "dlcs": [9999]})
        self.assertIsInstance(res2, bool)

        res3 = filter_dlc_depots_only(["12346", "12347"], "12345", depots_meta=[12346, 12347])
        self.assertIsInstance(res3, list)

        res4 = resolve_pinned_manifests({"depots": [12346, 12347]}, "12345")
        self.assertIsInstance(res4, dict)


class TestWorkshopHelpersAndTypo(unittest.TestCase):
    def test_workshopdepot_singular_and_workshop_visible(self):
        from utils.workshop_helpers import check_game_has_workshop

        # 1. Singular workshopdepot in depots
        gd1 = {"depots": {"workshopdepot": "12345"}}
        self.assertTrue(check_game_has_workshop("12345", game_data=gd1, allow_network=False))

        # 2. workshop_visible flag
        gd2 = {"depots": {"workshop_visible": True}}
        self.assertTrue(check_game_has_workshop("12345", game_data=gd2, allow_network=False))

        # 3. Legacy workshopdepots
        gd3 = {"depots": {"workshopdepots": "12345"}}
        self.assertTrue(check_game_has_workshop("12345", game_data=gd3, allow_network=False))

    def test_is_workshop_key_missing(self):
        from utils.workshop_helpers import is_workshop_key_missing
        from utils.settings import get_settings

        settings = get_settings()
        clean_no_ws_id = "88888801"
        settings.remove(f"has_workshop/{clean_no_ws_id}")

        # No workshop -> not missing
        gd_no_ws = {"depots": {}}
        self.assertFalse(is_workshop_key_missing(clean_no_ws_id, game_data=gd_no_ws))

        # Workshop supported, no key -> True (missing)
        gd_ws = {"depots": {"workshopdepot": clean_no_ws_id}}
        self.assertTrue(is_workshop_key_missing(clean_no_ws_id, game_data=gd_ws))

        # Workshop supported with valid 64-hex app_key in game_data -> False (not missing)
        valid_key = "a" * 64
        gd_ws_with_key = {"depots": {"workshopdepot": clean_no_ws_id}, "app_key": valid_key}
        self.assertFalse(is_workshop_key_missing(clean_no_ws_id, game_data=gd_ws_with_key))

    def test_dedicated_workshopdepot_id(self):
        from utils.workshop_helpers import is_workshop_key_missing
        from utils.paths import Paths
        import tempfile

        # Game where workshop depot ID is different from base AppID (e.g. AppID 700000, workshopdepot 700005)
        ws_depot_id = "700005"
        base_id = "700000"
        gd = {"depots": {"workshopdepot": ws_depot_id}}

        # Without key -> missing
        self.assertTrue(is_workshop_key_missing(base_id, game_data=gd))

        # With dedicated depot key in game_data["keys"] -> not missing
        valid_key = "f" * 64
        gd_with_depot_key = {"depots": {"workshopdepot": ws_depot_id}, "keys": {ws_depot_id: valid_key}}
        self.assertFalse(is_workshop_key_missing(base_id, game_data=gd_with_depot_key))


class TestPreflightComplications(unittest.TestCase):
    def test_detect_migration_complications_unkeyed_workshop(self):
        from utils.plugin_games import detect_migration_complications

        # Clean game without workshop (provide depots so Hubcap network fetch is bypassed)
        valid_k = "b" * 64
        gd_clean = {"appid": "111", "game_name": "Clean Game", "depots": {"112": {"key": valid_k}}}
        comp_clean = detect_migration_complications(gd_clean)
        self.assertFalse(comp_clean["has_complications"])
        self.assertIsNone(comp_clean["unkeyed_workshop"])

        # Game with workshop but no AppKey
        gd_ws = {"appid": "222", "game_name": "Workshop Game", "depots": {"workshopdepot": "222", "223": {"key": valid_k}}}
        comp_ws = detect_migration_complications(gd_ws)
        self.assertTrue(comp_ws["has_complications"])
        self.assertIsNotNone(comp_ws["unkeyed_workshop"])

        # Also supports calling detect_migration_complications(appid, game_data)
        comp_ws_alt = detect_migration_complications("222", gd_ws)
        self.assertTrue(comp_ws_alt["has_complications"])
        self.assertIsNotNone(comp_ws_alt["unkeyed_workshop"])

    def test_complications_dialog_workshop_card(self):
        from ui.dialogs.atom_complications_dialog import AtomMigrationComplicationsDialog

        comp = {
            "has_complications": True,
            "pinned": None,
            "dlc_only": None,
            "eos": False,
            "goldberg": False,
            "unkeyed": None,
            "unkeyed_workshop": {"appid": "222", "has_workshop": True},
        }
        dlg = AtomMigrationComplicationsDialog(None, "Workshop Game", "222", comp)
        self.assertIsNotNone(dlg)


class TestDepotsTabStateWorkshop(unittest.TestCase):
    def test_depots_state_unkeyed_workshop(self):
        from ui.dialogs.game_details.depots_tab_mixins.state import DepotsStateMixin

        class MockController(DepotsStateMixin):
            def __init__(self, appid_str, gd):
                self.appid_str = appid_str
                self.dialog = MagicMock()
                self.dialog.game_data = gd
                self.dialog.settings = MagicMock()
                self.dialog.settings.value.side_effect = lambda k, d=None, **kw: "#a1c9fd" if "accent" in k else d
                self.is_dlc_setup = False
                self.key_toggles = {}
                self.app_toggles = {}
                self.depot_toggles = {}

        gd = {
            "appid": "333333",
            "game_name": "WS Test Game",
            "depots": {"workshopdepot": "333333", "333334": {"name": "Content Depot"}},
        }
        ctrl = MockController("333333", gd)
        state = ctrl._collect_state()

        # Check keys table: AppKey must be present with is_missing: True
        app_keys = [k for k in state["keys"] if k["id"] == "333333"]
        self.assertEqual(len(app_keys), 1)
        k_item = app_keys[0]
        self.assertTrue(k_item["is_missing"])
        self.assertEqual(k_item["missing_tag"], "[No Workshop Key]")
        self.assertIn("Root AppKey is missing", k_item["missing_tooltip"])

        # Ensure root AppID is NEVER added as a content depot in depots table
        root_depots = [d for d in state["depots"] if d["id"] == "333333"]
        self.assertEqual(len(root_depots), 0)


class TestLibraryWidgetsBadges(unittest.TestCase):
    def test_widget_amber_badges(self):
        from ui.dialogs.library.widgets import GameItemWidget, GameTileWidget, GameCompactWidget

        # Case A: Normal AT0-M game, missing workshop key
        gd_normal_ws = {
            "appid": "444444",
            "game_name": "AT0M WS Game",
            "source": "at0-m",
            "update_status": "at0m",
            "depots": {"workshopdepot": "444444"},
        }
        w_item = GameItemWidget(gd_normal_ws, "10 GB", "#a1c9fd", "#121214")
        self.assertEqual(w_item.status_label.text(), "No workshop key")
        self.assertIn("#FFB74D", w_item.status_label.styleSheet())
        self.assertTrue(w_item.workshop_badge.isHidden())

        # Case B: Key pending AT0-M game, missing workshop key
        gd_pending_ws = {
            "appid": "555555",
            "game_name": "AT0M Pending Game",
            "source": "at0-m",
            "update_status": "key_pending",
            "depots": {"workshopdepot": "555555"},
        }
        w_pending = GameItemWidget(gd_pending_ws, "10 GB", "#a1c9fd", "#121214")
        self.assertEqual(w_pending.status_label.text(), "AT0-M: Awaiting Hubcap Key")
        self.assertFalse(w_pending.workshop_badge.isHidden())
        self.assertEqual(w_pending.workshop_badge.text(), "No workshop key")
        self.assertIn("#FFB74D", w_pending.workshop_badge.styleSheet())

        # Case C: Tile Widget layout structure
        tile = GameTileWidget(gd_pending_ws, "10 GB", "#a1c9fd", "#121214")
        self.assertEqual(tile.status_label.text(), "AT0-M: Awaiting Hubcap Key")
        self.assertFalse(tile.workshop_badge.isHidden())
        self.assertEqual(tile.workshop_badge.text(), "No workshop key")

        # Case D: Compact Widget layout structure
        compact = GameCompactWidget(gd_pending_ws, "10 GB", "#a1c9fd", "#121214")
        self.assertEqual(compact.status_label.text(), "AT0-M: Awaiting Hubcap Key")
        self.assertFalse(compact.workshop_badge.isHidden())
        self.assertEqual(compact.workshop_badge.text(), "No workshop key")

    def test_dlc_only_mode_preservation_in_update_status(self):
        from ui.dialogs.library.widgets import GameItemWidget
        from utils.settings import get_settings

        settings = get_settings()
        appid = "666666"
        settings.setValue(f"dlc_only_mode/{appid}", True)

        try:
            gd_dlc = {
                "appid": appid,
                "game_name": "DLC Only Game",
                "source": "at0-m",
                "update_status": "at0m",
                "is_dlc_only": True,
            }
            w_item = GameItemWidget(gd_dlc, "5 GB", "#a1c9fd", "#121214")
            self.assertEqual(w_item.status_label.text(), "[DLC Only]")

            # Call update_status with "at0m"
            w_item.update_status("at0m")
            self.assertEqual(w_item.status_label.text(), "[DLC Only]")
        finally:
            settings.remove(f"dlc_only_mode/{appid}")


class TestDatabaseManagerAndCachedTab(unittest.TestCase):
    def test_db_manager_startup_indexes(self):
        from managers.db_manager import DatabaseManager
        db = DatabaseManager()
        self.assertTrue(db.is_initialized)

        # Check indexes exist
        with db._conn_lock:
            cur = db.conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = {r[0] for r in cur.fetchall()}
            self.assertIn("idx_apps_depots_cached", indexes)
            self.assertIn("idx_depot_enrichments_appid", indexes)

    def test_cached_tab_page_lifecycle_and_take_item(self):
        from ui.dialogs.settings_tabs.cached_tab import INITIAL_BATCH, CachedTabPage

        tab = CachedTabPage()
        self.assertFalse(tab._cancel_token.is_set())
        self.assertEqual(INITIAL_BATCH, 25)

        # Test pagination count decrement on item removal
        tab._all_rows = [("10", "Game 10"), ("20", "Game 20"), ("30", "Game 30")]
        tab._render_next_batch(2)
        self.assertEqual(tab._rendered_count, 2)
        self.assertEqual(tab.lst.count(), 2)

        # Simulate clear removing row 0
        item = tab.lst.item(0)
        row = tab.lst.row(item)
        tab.lst.takeItem(row)
        tab._rendered_count = max(0, tab._rendered_count - 1)
        tab._all_rows = [(a, n) for a, n in tab._all_rows if a != "10"]
        self.assertEqual(tab._rendered_count, 1)
        self.assertEqual(len(tab._all_rows), 2)

        # Simulate hideEvent
        tab.hideEvent(None)
        self.assertTrue(tab._cancel_token.is_set())

        # Simulate showEvent
        tab.showEvent(None)
        self.assertFalse(tab._cancel_token.is_set())


if __name__ == "__main__":
    unittest.main()
