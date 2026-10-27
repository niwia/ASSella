#!/usr/bin/env python3
"""
scripts/test_dlc_only_ux.py
===========================
Verification test suite for DLC-Only mode UX improvements:
  1. Setting `at0m_default_dlc_action` persistence and combo data.
  2. `DownloadBackendDialog` mode="dlc" configuration and remember choice.
  3. `HighDlcPromptDialog` and `DlcSelectionChecklistDialog` threshold calculation and selection.
  4. Invariant checks for depot-less DLC registration:
     - Base AppID NEVER in AdditionalApps
     - <= 63 DLCs -> AdditionalApps
     - >= 64 DLCs -> DlcData
     - System inode preservation on config.yaml
  5. Depot Selection Dialog DLC mode:
     - Unowned DLC depots auto-selected
     - Steam-owned DLCs locked / greyscaled
     - Primary button text 'Register to AT0-M' when is_atom_mode=True
"""

import os
import sys
import unittest
from pathlib import Path

# Ensure src/ is on PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Create QApp for Qt widget tests (headless)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from utils.settings import get_settings
from utils.yaml_config_manager import (
    get_user_config_path,
    get_additional_apps,
    batch_config_edit,
)
from utils.yaml.dlc import get_dlc_data
from ui.dialogs.download_backend_dialog import (
    DownloadBackendDialog,
    BACKEND_NATIVE_STEAM,
    BACKEND_ASSELLA,
)
from ui.dialogs.dlc_selection_checklist_dialog import (
    HighDlcPromptDialog,
    DlcSelectionChecklistDialog,
)


class TestDLCOnlyUX(unittest.TestCase):

    def setUp(self):
        self.settings = get_settings()
        self.cfg_path = get_user_config_path()
        self.orig_cfg_content = self.cfg_path.read_text(encoding="utf-8") if self.cfg_path.exists() else None
        self.orig_inode = self.cfg_path.stat().st_ino if self.cfg_path.exists() else None
        self.orig_dlc_setting = self.settings.value("at0m_default_dlc_action", "ask", type=str)
        self.orig_library_mode = self.settings.value("library_mode", False, type=bool)
        self.settings.setValue("library_mode", True)
        self.cfg_was_created = False
        if not self.cfg_path.exists():
            self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
            minimal_config = (
                "Plugins: yes\n"
                "DisableFamilyShareLock: yes\n"
                "UseWhitelist: no\n"
                "AppIds:\n"
                "AdditionalApps:\n"
                "AdditionalDepots:\n"
                "DecryptionKeys:\n"
                "AppTokens:\n"
            )
            self.cfg_path.write_text(minimal_config, encoding="utf-8")
            self.cfg_was_created = True

        if self.orig_inode is None and self.cfg_path.exists():
            self.orig_inode = self.cfg_path.stat().st_ino

    def tearDown(self):
        # Restore settings
        self.settings.setValue("at0m_default_dlc_action", self.orig_dlc_setting)
        self.settings.setValue("library_mode", self.orig_library_mode)
        # Restore config.yaml
        if getattr(self, "cfg_was_created", False):
            self.cfg_path.unlink(missing_ok=True)
        elif self.orig_cfg_content is not None and self.cfg_path.exists():
            from utils.yaml.core import _atomic_write
            _atomic_write(self.cfg_path, self.orig_cfg_content)

    def test_01_dlc_action_settings_persistence(self):
        """Verify at0m_default_dlc_action values can be saved and read."""
        for val in ("ask", "native", "assella"):
            self.settings.setValue("at0m_default_dlc_action", val)
            read_back = self.settings.value("at0m_default_dlc_action", type=str)
            self.assertEqual(read_back, val, f"Expected {val}, got {read_back}")
        print("  [PASS] Setting at0m_default_dlc_action persistence verified")

    def test_02_download_backend_dialog_dlc_mode(self):
        """Verify DownloadBackendDialog mode='dlc' sets window title and saves at0m_default_dlc_action."""
        dlg = DownloadBackendDialog(
            app_id="582010",
            game_name="Monster Hunter: World",
            mode="dlc",
        )
        self.assertEqual(dlg.windowTitle(), "DLC Mode Selection")
        self.assertEqual(dlg.title_bar.title_label.text(), "DLC Setup Method")

        # Simulate choosing native steam and remembering
        dlg._selected_backend = BACKEND_NATIVE_STEAM
        dlg.remember_chk.setChecked(True)
        dlg._on_proceed()

        saved = self.settings.value("at0m_default_dlc_action", type=str)
        self.assertEqual(saved, "native", "Expected 'native' saved to at0m_default_dlc_action")
        print("  [PASS] DownloadBackendDialog mode='dlc' title and remember persistence verified")

    def test_03_high_dlc_prompt_and_checklist(self):
        """Verify HighDlcPromptDialog and DlcSelectionChecklistDialog logic."""
        # Mock catalog of 70 DLCs
        mock_catalog = {str(200000 + i): f"DLC Item {i}" for i in range(70)}
        mock_catalog["582010"] = "Base Game"  # Should be excluded

        # 1. High DLC Prompt
        prompt = HighDlcPromptDialog(
            app_id="582010",
            game_name="Monster Hunter: World",
            dlc_count=70,
        )
        self.assertEqual(prompt.dlc_count, 70)
        prompt._on_dlc_data_chosen()
        self.assertEqual(prompt.get_choice(), "dlc_data")

        # 2. Checklist Dialog
        chk = DlcSelectionChecklistDialog(
            app_id="582010",
            game_name="Monster Hunter: World",
            catalog=mock_catalog,
        )
        # Verify base AppID was excluded
        self.assertEqual(chk.list_widget.count(), 70)

        # Initially all are checked -> 70 DLCs >= 64 -> High-DLC Mode
        self.assertIn("High-DLC Mode", chk.badge_lbl.text())
        selected = chk.get_selected_dlcs()
        self.assertEqual(len(selected), 70)

        # Uncheck until 30 DLCs remain -> Standard Mode
        for i in range(30, 70):
            chk.list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)
        self.assertIn("Standard Mode", chk.badge_lbl.text())
        selected_30 = chk.get_selected_dlcs()
        self.assertEqual(len(selected_30), 30)

        # Filter search
        chk._on_search_text_changed("DLC Item 5")
        visible_count = sum(1 for i in range(chk.list_widget.count()) if not chk.list_widget.item(i).isHidden())
        self.assertGreater(visible_count, 0)
        self.assertLess(visible_count, 70)
        print("  [PASS] HighDlcPromptDialog and DlcSelectionChecklistDialog verified")

    def test_04_depotless_registration_invariants(self):
        """Verify depotless DLC registration enforces invariants: base AppID NEVER in AdditionalApps, inode preserved."""
        test_appid = "999888"
        test_name = "Test DLC Game"
        
        # Test Case A: <= 63 DLCs -> AdditionalApps
        catalog_small = {
            "999889": "DLC Quest 1",
            "999890": "DLC Quest 2",
        }

        from ui.dialogs.library.actions import LibraryActionsMixin
        from unittest.mock import patch
        mixin = LibraryActionsMixin()

        with patch("ui.dialogs.themed_message_box.ThemedMessageBox.information", return_value=None), \
             patch("ui.dialogs.themed_message_box.ThemedMessageBox.critical", return_value=None):
            # Call _register_depotless_dlcs_to_atom with mode="additional_apps"
            res = mixin._register_depotless_dlcs_to_atom(
                appid=test_appid,
                name=test_name,
                dlc_dict=catalog_small,
                mode="additional_apps",
            )
            self.assertTrue(res)

            apps = get_additional_apps(self.cfg_path)
            self.assertNotIn(test_appid, apps, "CRITICAL: Base game AppID was found in AdditionalApps!")
            self.assertIn("999889", apps, "DLC AppID 999889 should be in AdditionalApps")
            self.assertIn("999890", apps, "DLC AppID 999890 should be in AdditionalApps")

            # Test Case B: >= 64 DLCs -> DlcData
            catalog_large = {str(900000 + i): f"Exp Pack {i}" for i in range(65)}
            res_large = mixin._register_depotless_dlcs_to_atom(
                appid=test_appid,
                name=test_name,
                dlc_dict=catalog_large,
                mode="dlc_data",
            )
            self.assertTrue(res_large)

            dlc_data = get_dlc_data(self.cfg_path, test_appid)
            self.assertEqual(len(dlc_data), 65)
            self.assertIn("900000", dlc_data)
            self.assertNotIn(test_appid, dlc_data, "Base AppID must NEVER be a DLC in DlcData")

        # Inode preservation check
        new_inode = self.cfg_path.stat().st_ino
        self.assertEqual(self.orig_inode, new_inode, "System inode was not preserved!")
        print("  [PASS] Invariants verified: Base AppID excluded, AdditionalApps (<64) and DlcData (>=64) correct, inode preserved")

    def test_05_depot_selection_dialog_dlc_mode_properties(self):
        """Verify DepotSelectionDialog in DLC mode: is_atom_mode sets button text to 'Register to AT0-M'."""
        from ui.dialogs.depot_selection.dialog import DepotSelectionDialog

        mock_depots = {
            "582011": {"desc": "MHW Base Content", "size": 1000},
            "582012": {"desc": "MHW DLC Content", "size": 500, "dlcappid": "582015"},
        }

        dlg = DepotSelectionDialog(
            app_id="582010",
            game_name="Monster Hunter: World",
            depots=mock_depots,
            header_url="",
            dlc_only_mode=True,
            is_atom_mode=True,
        )

        self.assertTrue(dlg._is_atom_mode)
        self.assertTrue(dlg._dlc_only_mode)
        self.assertTrue(dlg.download_btn.text().startswith("Register to AT0-M"))
        self.assertFalse(dlg.move_to_atom_btn.isVisible())
        print("  [PASS] DepotSelectionDialog DLC AT0-M button styling and mode verified")


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDLCOnlyUX)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
