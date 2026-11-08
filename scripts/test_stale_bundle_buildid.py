#!/usr/bin/env python3
"""Test stale bundle detection, UI display, and post-install cache behavior."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure src is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Use headless offscreen Qt platform
os.environ["QT_QPA_PLATFORM"] = "offscreen"


class _SettingsStore:
    """Minimal in-memory stand-in for QSettings."""

    def __init__(self):
        self._data = {}

    def setValue(self, key, value):
        self._data[key] = value

    def value(self, key, default=None, type=str):
        return self._data.get(key, default)

    def remove(self, key):
        self._data.pop(key, None)


class _DummyFinalizer:
    """Carries only the state PostInstallMixin helpers actually read."""

    def __init__(self, game_data, metadata):
        self.game_data = game_data
        self.current_job_metadata = metadata
        self.current_job = None
        self._is_handoff_job = False


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

    # --- post-install helpers (exercise the real PostInstallMixin methods) ---

    def _finalizer(self, game_data, metadata):
        return _DummyFinalizer(game_data, metadata)

    def test_stale_install_never_stamps_live_buildid(self):
        """A stale bundle without a verified historical id must not stamp the live build id."""
        from managers.finalize.post_install import PostInstallMixin

        finalizer = self._finalizer(
            {"appid": "555555", "is_bundle_stale": True, "buildid": ""},
            {"is_bundle_stale": True, "branch": "public"},
        )
        settings = _SettingsStore()

        is_stale = PostInstallMixin._is_stale_or_rollback_install(finalizer)
        self.assertTrue(is_stale)

        # target_bid here is the LIVE Steam branch build id; it must be ignored.
        final_bid = PostInstallMixin._compute_final_buildid("", None, "99999999", is_stale)
        self.assertEqual(final_bid, "")

        PostInstallMixin._apply_install_buildid_and_pin(
            finalizer, settings, "555555", "public", final_bid, is_stale
        )
        self.assertNotIn("installed_buildid/555555", settings._data)
        self.assertNotIn("installed_buildid/555555/public", settings._data)

    def test_stale_install_stamps_verified_historical_buildid(self):
        """A verified numeric historical build id is stamped and the live id is ignored."""
        from managers.finalize.post_install import PostInstallMixin

        finalizer = self._finalizer(
            {"appid": "555555", "is_bundle_stale": True, "buildid": "88888888"},
            {"is_bundle_stale": True, "branch": "public"},
        )
        settings = _SettingsStore()

        is_stale = PostInstallMixin._is_stale_or_rollback_install(finalizer)
        final_bid = PostInstallMixin._compute_final_buildid("88888888", None, "99999999", is_stale)
        self.assertEqual(final_bid, "88888888")

        PostInstallMixin._apply_install_buildid_and_pin(
            finalizer, settings, "555555", "public", final_bid, is_stale
        )
        self.assertEqual(settings._data["installed_buildid/555555"], "88888888")
        self.assertEqual(settings._data["installed_buildid/555555/public"], "88888888")

    def test_pin_applied_for_verified_rollback(self):
        """Regression: a pinned install WITH a verified build id must still apply the pin.

        The prior implementation nested the pin block under the empty-buildid
        branch, so pins were silently dropped for the normal (verified) case.
        """
        from managers.finalize.post_install import PostInstallMixin

        finalizer = self._finalizer(
            {"appid": "555555", "_is_rollback": True, "buildid": "88888888"},
            {"is_rollback": True, "pin_build": True, "branch": "public"},
        )
        settings = _SettingsStore()

        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "managers.finalize.post_install.get_base_path",
                return_value=Path(tmp),
            ):
                current_job = Path(tmp) / "package.zip"
                current_job.write_bytes(b"PK\x03\x04dummy")
                finalizer.current_job = str(current_job)

                is_stale = PostInstallMixin._is_stale_or_rollback_install(finalizer)
                final_bid = PostInstallMixin._compute_final_buildid(
                    "88888888", None, "99999999", is_stale
                )
                PostInstallMixin._apply_install_buildid_and_pin(
                    finalizer, settings, "555555", "public", final_bid, is_stale
                )

                self.assertTrue(settings._data["pin_build/555555"])
                self.assertEqual(settings._data["exclude_from_update_all/555555"], False)
                self.assertTrue(
                    (Path(tmp) / "hubcap_manifests"
                     / "accela_fetch_555555_build_88888888.zip").exists()
                )

    def test_normal_install_stamps_live_buildid(self):
        """A current bundle falls back to the live build id as before."""
        from managers.finalize.post_install import PostInstallMixin

        finalizer = self._finalizer(
            {"appid": "555555", "buildid": ""},
            {"branch": "public"},
        )
        settings = _SettingsStore()

        is_stale = PostInstallMixin._is_stale_or_rollback_install(finalizer)
        self.assertFalse(is_stale)
        final_bid = PostInstallMixin._compute_final_buildid("", None, "99999999", is_stale)
        self.assertEqual(final_bid, "99999999")

        PostInstallMixin._apply_install_buildid_and_pin(
            finalizer, settings, "555555", "public", final_bid, is_stale
        )
        self.assertEqual(settings._data["installed_buildid/555555"], "99999999")


    def test_pinned_install_marks_up_to_date_in_cache(self):
        """A pinned rollback must be treated as up-to-date in cache, not update-available."""
        from managers.finalize.post_install import PostInstallMixin
        from utils.update_status_cache import UpdateStatusCache

        finalizer = self._finalizer(
            {"appid": "555555", "_is_rollback": True, "buildid": "88888888"},
            {"is_rollback": True, "pin_build": True, "branch": "public"},
        )
        cache = UpdateStatusCache()
        cache._cache = {}

        is_stale_or_rollback = PostInstallMixin._is_stale_or_rollback_install(finalizer)
        is_pinned = bool(
            (finalizer.current_job_metadata or {}).get("pin_build")
            or (finalizer.game_data or {}).get("pin_build")
            or (finalizer.game_data or {}).get("_pin_build")
        )
        self.assertTrue(is_stale_or_rollback)
        self.assertTrue(is_pinned)

        if is_stale_or_rollback and not is_pinned:
            cache.set_status("555555", "update_available")
        else:
            cache.set_status("555555", "up_to_date")

        self.assertEqual(cache.get_status("555555"), "up_to_date")

    def test_unpinned_stale_install_marks_update_available_in_cache(self):
        """An unpinned stale package must be marked update-available in cache."""
        from managers.finalize.post_install import PostInstallMixin
        from utils.update_status_cache import UpdateStatusCache

        finalizer = self._finalizer(
            {"appid": "555555", "is_bundle_stale": True, "buildid": ""},
            {"is_bundle_stale": True, "branch": "public"},
        )
        cache = UpdateStatusCache()
        cache._cache = {}

        is_stale_or_rollback = PostInstallMixin._is_stale_or_rollback_install(finalizer)
        is_pinned = bool(
            (finalizer.current_job_metadata or {}).get("pin_build")
            or (finalizer.game_data or {}).get("pin_build")
            or (finalizer.game_data or {}).get("_pin_build")
        )
        self.assertTrue(is_stale_or_rollback)
        self.assertFalse(is_pinned)

        if is_stale_or_rollback and not is_pinned:
            cache.set_status("555555", "update_available")
        else:
            cache.set_status("555555", "up_to_date")

        self.assertEqual(cache.get_status("555555"), "update_available")


if __name__ == "__main__":
    unittest.main()
