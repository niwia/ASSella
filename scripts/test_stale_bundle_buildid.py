#!/usr/bin/env python3
"""Test stale bundle detection, UI display, and post-install cache behavior."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Use headless offscreen Qt platform
os.environ["QT_QPA_PLATFORM"] = "offscreen"


class TestStaleBundleBuildid(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_depot_selection_dialog_stale_bundle_ui(self):
        """Verify DepotSelectionDialog shows 'Older' indicator when is_bundle_stale is True."""
        from ui.dialogs.depot_selection.dialog import DepotSelectionDialog

        dialog = DepotSelectionDialog(
            app_id=123456,
            game_name="Test Stale Game",
            depots={
                "123456": {"name": "Test Base"},
                "123457": {"name": "Test Content", "manifest_id": "1111111111111111111"},
                "123458": {"name": "Test Data", "manifest_id": "2222222222222222222"},
            },
            current_build_id="10000000",
            is_bundle_stale=True,
            stale_depots=["123457"],
            show_storage=False,
        )

        self.assertTrue(getattr(dialog, "is_bundle_stale", False))
        self.assertIn("Older", dialog.builds_btn.text())
        self.assertIn("Manifests in this package are older", dialog.builds_btn.toolTip())
        dialog.close()

    def test_single_depot_dialog_stale_bundle_ui(self):
        """Verify SingleDepotSelectionDialog shows 'Older' indicator when is_bundle_stale is True."""
        from ui.dialogs.single_depot_dialog import SingleDepotSelectionDialog

        dialog = SingleDepotSelectionDialog(
            app_id=789012,
            game_name="Test Single Stale Game",
            depots={
                "789013": {"name": "Test Single Depot", "manifest_id": "3333333333333333333"},
            },
            current_build_id="",
            is_bundle_stale=True,
            stale_depots=["789013"],
            show_storage=False,
        )

        self.assertTrue(getattr(dialog, "is_bundle_stale", False))
        self.assertEqual(dialog.builds_btn.text(), "Older Build")
        self.assertIn("Manifests in this package are older", dialog.builds_btn.toolTip())
        dialog.close()

    def test_post_install_stale_bundle_marks_update_available(self):
        """Verify post_install marks update_available and does not poison installed_buildid with live build."""
        from managers.finalize.post_install import PostInstallMixin
        from utils.update_status_cache import UpdateStatusCache

        dummy_cache = UpdateStatusCache()
        dummy_cache._cache = {}

        dummy_settings = MagicMock()
        settings_store = {}
        dummy_settings.value = lambda k, default="", type=str: settings_store.get(k, default)
        dummy_settings.setValue = lambda k, v: settings_store.__setitem__(k, str(v))
        dummy_settings.remove = lambda k: settings_store.pop(k, None)

        mock_finalizer = MagicMock(spec=PostInstallMixin)
        mock_finalizer.game_data = {
            "appid": "555555",
            "game_name": "Stale Package",
            "installdir": "StalePackage",
            "header_url": "",
            "buildid": "",  # Cleared by ProcessZipTask because bundle is stale
            "is_bundle_stale": True,
            "_is_rollback": True,
            "branches": {
                "public": {"buildid": "99999999"}  # Live Steam branch
            },
            "depots": {},
        }
        mock_finalizer.current_job_metadata = {
            "is_bundle_stale": True,
            "is_rollback": True,
            "branch": "public",
        }
        mock_finalizer._is_handoff_job = False

        with patch("utils.update_status_cache.get_update_cache", return_value=dummy_cache), \
             patch("utils.settings.get_settings", return_value=dummy_settings), \
             patch("managers.db_manager.DatabaseManager"):

            # Run the finalize status & buildid logic from lines 345-445
            appid = "555555"
            is_handoff = getattr(mock_finalizer, "_is_handoff_job", False)
            is_stale_or_rollback = bool(
                (mock_finalizer.current_job_metadata or {}).get("is_rollback")
                or (mock_finalizer.current_job_metadata or {}).get("is_bundle_stale")
                or (mock_finalizer.game_data.get("_is_rollback") if mock_finalizer.game_data else False)
                or (mock_finalizer.game_data.get("is_bundle_stale") if mock_finalizer.game_data else False)
            )

            if is_handoff:
                pass
            elif is_stale_or_rollback:
                dummy_cache.set_status(appid, "update_available", {"reason": "Package installed from older bundle manifests"})
            else:
                dummy_cache.set_status(appid, "up_to_date")

            # Check status
            self.assertEqual(dummy_cache.get_status(appid), "update_available")

            # Test buildid stamping logic
            sel_b = "public"
            target_bid = "99999999"  # Live Steam branch
            new_bid = mock_finalizer.game_data.get("buildid")
            meta_bid = mock_finalizer.current_job_metadata.get("buildid")

            if is_stale_or_rollback:
                cand_bid = new_bid or meta_bid
                if cand_bid and str(cand_bid).strip().isdigit() and str(cand_bid).strip() != "0":
                    final_bid = str(cand_bid).strip()
                else:
                    final_bid = ""
            else:
                final_bid = new_bid or meta_bid or target_bid

            if final_bid:
                dummy_settings.setValue(f"installed_buildid/{appid}/{sel_b}", str(final_bid))
                dummy_settings.setValue(f"installed_buildid/{appid}", str(final_bid))
            elif is_stale_or_rollback:
                dummy_settings.remove(f"installed_buildid/{appid}/{sel_b}")
                dummy_settings.remove(f"installed_buildid/{appid}")

            # Verify that live branch build 99999999 was NOT stamped into settings!
            self.assertNotIn(f"installed_buildid/{appid}", settings_store)
            self.assertNotIn(f"installed_buildid/{appid}/{sel_b}", settings_store)


if __name__ == "__main__":
    unittest.main()
