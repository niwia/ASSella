#!/usr/bin/env python3
"""
Hermetic regression tests for:
  1. Steam-owned items unlock + single-action warning (`confirm_steam_owned_override`)
     across DepotsTab (`details` and `dlc_setup`), DepotSelectionDialog, and
     ContentDepotSelectionDialog, while keeping DLC-Only parent base AppID locked.
  2. GameManager._scan_library showing AdditionalApps entries even if present in owned_appids.
  3. BottomTitleBar GitHub SVG button next to version_label sharing accent_color + opening GitHub URL.
  4. Scalable SVG ASSella logo rendering across BottomTitleBar, DownloadBackendDialog,
     hero_header, and load_assella_app_icon().
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_TMP = tempfile.mkdtemp(prefix="assella-owned-svg-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local" / "share")
os.environ["XDG_CACHE_HOME"] = str(Path(_TMP) / ".cache")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QStackedWidget, QTableWidget, QWidget

_APP = QApplication.instance() or QApplication(["-platform", "offscreen"])


def _dummy_db_manager():
    class _FakeDB:
        def get_depot_enrichments(self, appid):
            return {}

        def get_app_info(self, appid):
            return {}

        def get_at0m_update_status(self, appid):
            return {}

        def clear_missing_hubcap_depot(self, appid, did):
            return None

    return _FakeDB


class TestOwnedOverrideAndSvgUi(unittest.TestCase):
    def setUp(self):
        self.cfg_dir = Path(_TMP) / ".config" / "SLSsteam"
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        self.cfg_path = self.cfg_dir / "config.yaml"
        self.cfg_path.write_text(
            "Plugins: yes\nAdditionalApps: {}\nAdditionalDepots: {}\nDecryptionKeys: {}\nAppTokens: {}\n",
            encoding="utf-8",
        )

    def test_01_confirm_steam_owned_override_helper(self):
        """confirm_steam_owned_override uses Proceed/Cancel choices with the required warning text."""
        from ui.dialogs.themed_message_box import ThemedMessageBox, confirm_steam_owned_override

        with patch.object(ThemedMessageBox, "choices", return_value="proceed") as m_ok:
            self.assertTrue(confirm_steam_owned_override())
            m_ok.assert_called_once()
            args, kwargs = m_ok.call_args
            self.assertIn("ASSella suspects that you own this game or a part of it on Steam", args[2])
            self.assertEqual(args[3], [("Proceed", "proceed"), ("Cancel", "cancel")])
            self.assertEqual(kwargs.get("default"), "cancel")
            self.assertEqual(kwargs.get("severity"), "warning")

        with patch.object(ThemedMessageBox, "choices", return_value="cancel"):
            self.assertFalse(confirm_steam_owned_override())

    def test_02_game_manager_scan_library_keeps_additional_apps_when_owned(self):
        """AdditionalApps entries appear even when their AppID is listed in Steam owned_appids."""
        from managers.game_manager import GameManager

        lib_dir = Path(_TMP) / "SteamLib"
        steamapps = lib_dir / "steamapps"
        common = steamapps / "common"
        managed_dir = common / "ManagedGame"
        unmanaged_dir = common / "PureOwnedGame"
        managed_dir.mkdir(parents=True, exist_ok=True)
        unmanaged_dir.mkdir(parents=True, exist_ok=True)
        (managed_dir / "game.exe").write_text("x", encoding="utf-8")
        (unmanaged_dir / "game.exe").write_text("x", encoding="utf-8")

        (steamapps / "appmanifest_11111.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"11111"\n\t"name"\t\t"Managed Game"\n\t"installdir"\t\t"ManagedGame"\n}\n',
            encoding="utf-8",
        )
        (steamapps / "appmanifest_22222.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"22222"\n\t"name"\t\t"Pure Owned Game"\n\t"installdir"\t\t"PureOwnedGame"\n}\n',
            encoding="utf-8",
        )

        gm = GameManager()
        gm._scan_cancelled = False

        scanned = []
        with patch("utils.plugin_games.get_all_plugin_games", return_value={}), \
             patch("utils.plugin_games.build_dlc_reverse_map", return_value={}):
            gm._scan_library(
                str(lib_dir),
                str(lib_dir),
                scanned,
                owned_appids={"11111", "22222"},
                additional_apps={"11111"},
            )

        found_ids = {g["appid"] for g in scanned}
        self.assertIn("11111", found_ids)
        self.assertNotIn("22222", found_ids)

        # Also verify Pass 2 directly: build real ACF cache, bypass Pass 1 folder scan via empty scandir,
        # and verify Pass 2 discovers AdditionalApps entry even when also in owned_appids.
        pass2_dir = common / "Pass2OwnedGame"
        pass2_dir.mkdir(parents=True, exist_ok=True)
        (pass2_dir / "game.exe").write_text("x", encoding="utf-8")
        acf_33333 = steamapps / "appmanifest_33333.acf"
        acf_33333.write_text(
            '"AppState"\n{\n\t"appid"\t\t"33333"\n\t"name"\t\t"Pass2 Owned Game"\n\t"installdir"\t\t"Pass2OwnedGame"\n}\n',
            encoding="utf-8",
        )
        full_cache = gm._build_acf_cache(str(steamapps))
        empty_cm = MagicMock()
        empty_cm.__enter__.return_value = iter(())
        empty_cm.__exit__.return_value = False
        real_scandir = os.scandir
        scanned_pass2 = []
        with patch("utils.plugin_games.get_all_plugin_games", return_value={}), \
             patch("utils.plugin_games.build_dlc_reverse_map", return_value={}), \
             patch(
                 "managers.game_manager.os.scandir",
                 side_effect=lambda p: empty_cm if str(p) == str(common) else real_scandir(p),
             ):
            gm._scan_library(
                str(lib_dir),
                str(lib_dir),
                scanned_pass2,
                global_acf_cache=full_cache,
                owned_appids={"11111", "22222", "33333"},
                additional_apps={"33333"},
            )
        pass2_ids = {g["appid"] for g in scanned_pass2}
        self.assertIn("33333", pass2_ids)
        self.assertNotIn("22222", pass2_ids)

    def test_03_depots_tab_owned_unlock_and_dlc_base_lock(self):
        """DepotsTab unlocks Steam-owned items with single warning while locking base in DLC-only mode."""
        from ui.dialogs.game_details.depots_tab import build_depots_panel, MiniSwitchToggle
        from managers.db_manager import DatabaseManager

        base_id = "777000"
        dlc_id = "777001"
        base_depot = "777010"
        dlc_depot = "777011"
        key_a = "a" * 64
        key_b = "b" * 64
        key_c = "c" * 64

        dialog = MagicMock()
        dialog.appid = base_id
        dialog.accent_color = "#C06C84"
        dialog.game_data = {
            "appid": base_id,
            "game_name": "Owned Base Game",
            "is_dlc_only": False,
            "dlcs": {dlc_id: "Owned DLC 1"},
            "depots": {
                base_depot: {"name": "Base Content", "key": key_b},
                dlc_depot: {"name": "DLC Content", "dlcappid": dlc_id, "key": key_c},
            },
            "keys": {base_id: key_a, base_depot: key_b, dlc_depot: key_c},
        }

        with patch.object(DatabaseManager, "get_depot_enrichments", return_value={}), \
             patch.object(DatabaseManager, "get_app_info", return_value={}), \
             patch.object(DatabaseManager, "get_at0m_update_status", return_value={}), \
             patch("core.steam_helpers.get_owned_steam_appids", return_value={base_id}), \
             patch("utils.dlc_helpers.get_owned_dlcs_from_steam", return_value={dlc_id}), \
             patch("utils.dlc_helpers.is_dlc_only_mode", return_value=False), \
             patch("utils.dlc_helpers.build_app_to_depots_map", return_value={base_id: {base_depot}, dlc_id: {dlc_depot}}):

            # A) Non-DLC-only setup ("details"): base AppID and owned DLC are unlocked in Edit mode (dlc_setup simulates Edit unlocked)
            panel = build_depots_panel(dialog, mode="dlc_setup")
            tables = panel.findChildren(QTableWidget)
            apps_tbl, depots_tbl, keys_tbl = tables[0], tables[1], tables[2]

            base_toggle = apps_tbl.cellWidget(0, 2).findChild(MiniSwitchToggle)
            dlc_toggle = apps_tbl.cellWidget(1, 2).findChild(MiniSwitchToggle)
            base_depot_toggle = depots_tbl.cellWidget(0, 2).findChild(MiniSwitchToggle)
            dlc_depot_toggle = depots_tbl.cellWidget(1, 2).findChild(MiniSwitchToggle)
            appkey_toggle = keys_tbl.cellWidget(0, 2).findChild(MiniSwitchToggle)

            # In dlc_setup mode, base AppID and base AppKey MUST stay locked per AGENTS.md!
            self.assertFalse(base_toggle.isEnabled())
            self.assertTrue(base_toggle.isReadOnly())
            self.assertFalse(appkey_toggle.isEnabled())

            # Owned DLC starts unchecked and is interactive
            self.assertTrue(dlc_toggle.isEnabled())
            self.assertFalse(dlc_toggle.isGreyscaled())
            self.assertFalse(dlc_toggle.isChecked())
            self.assertTrue(dlc_depot_toggle.isEnabled())
            self.assertFalse(dlc_depot_toggle.isChecked())
            self.assertTrue(base_depot_toggle.isEnabled())
            self.assertFalse(base_depot_toggle.isChecked())

            # Cancel on owned DLC keeps both DLC & depot unchecked
            with patch("ui.dialogs.game_details.depots_tab.confirm_steam_owned_override", return_value=False) as m_cancel:
                dlc_toggle.setChecked(True, animate=False)
                self.assertEqual(m_cancel.call_count, 1)
                self.assertFalse(dlc_toggle.isChecked())
                self.assertFalse(dlc_depot_toggle.isChecked())

            # Proceed on owned DLC prompts ONCE and cascades to its depot without re-prompting
            with patch("ui.dialogs.game_details.depots_tab.confirm_steam_owned_override", return_value=True) as m_ok:
                dlc_toggle.setChecked(True, animate=False)
                self.assertEqual(m_ok.call_count, 1)
                self.assertTrue(dlc_toggle.isChecked())
                self.assertTrue(dlc_depot_toggle.isChecked())

            # Unchecking never prompts
            with patch("ui.dialogs.game_details.depots_tab.confirm_steam_owned_override") as m_off:
                dlc_toggle.setChecked(False, animate=False)
                m_off.assert_not_called()
                self.assertFalse(dlc_toggle.isChecked())

            # B) Non-DLC-only details mode: unlocking Edit makes non-DLC-only owned base AppID & AppKey interactive too
            details_panel = build_depots_panel(dialog, mode="details")
            # Simulate clicking Yes on Edit confirmation
            from ui.dialogs.themed_message_box import ThemedMessageBox
            with patch.object(ThemedMessageBox, "warning", return_value=ThemedMessageBox.StandardButton.Yes):
                for btn in details_panel.findChildren(type(details_panel.findChildren(QWidget)[0])):
                    pass
                from PyQt6.QtWidgets import QPushButton
                edit_btns = [b for b in details_panel.findChildren(QPushButton) if b.text() == "Edit"]
                self.assertTrue(edit_btns)
                edit_btns[0].click()

            d_tables = details_panel.findChildren(QTableWidget)
            d_base_toggle = d_tables[0].cellWidget(0, 2).findChild(MiniSwitchToggle)
            d_appkey_toggle = d_tables[2].cellWidget(0, 2).findChild(MiniSwitchToggle)
            self.assertTrue(d_base_toggle.isEnabled())
            self.assertFalse(d_base_toggle.isReadOnly())
            self.assertTrue(d_appkey_toggle.isEnabled())
            with patch("ui.dialogs.game_details.depots_tab.confirm_steam_owned_override", return_value=True) as m_base:
                d_base_toggle.setChecked(True, animate=False)
                self.assertEqual(m_base.call_count, 1)
                self.assertTrue(d_base_toggle.isChecked())

    def test_04_depot_selection_dialog_owned_unlock_and_content_view(self):
        """DepotSelectionDialog & ContentDepotSelectionDialog allow owned rows after warning while locking DLC-Only base."""
        from ui.dialogs.depot_selection.content_dialog import ContentDepotSelectionDialog
        from managers.db_manager import DatabaseManager

        depots = {
            "880001": {"name": "Base Content", "size": 2_000_000, "oslist": "windows"},
            "880002": {"name": "Owned DLC Pack", "dlcappid": "880010", "size": 3_000_000, "oslist": "windows"},
        }
        FakeDB = _dummy_db_manager()
        with patch("managers.db_manager.DatabaseManager", FakeDB), \
             patch("core.steam_helpers.get_owned_steam_appids", return_value={"880000", "880010"}), \
             patch("utils.dlc_helpers.get_owned_dlcs_from_steam", return_value={"880010"}):
            dlg = ContentDepotSelectionDialog(
                app_id="880000",
                game_name="Owned Test Game",
                depots=depots,
                dlc_catalog={"880010": "Owned DLC Pack"},
                dlc_only_mode=True,
                show_storage=False,
            )

        # Row 0: Base Content in DLC-only mode MUST remain locked (`dlc_base_locked`, Unchecked)
        row0 = dlg.table_widget.item(0, 0)
        self.assertEqual(row0.data(Qt.ItemDataRole.UserRole + 2), "dlc_base_locked")
        self.assertEqual(row0.flags(), Qt.ItemFlag.NoItemFlags)
        self.assertEqual(row0.checkState(), Qt.CheckState.Unchecked)

        # Row 1: Suspected Steam-owned DLC MUST start unchecked and be interactive
        row1 = dlg.table_widget.item(1, 0)
        self.assertTrue(row1.data(Qt.ItemDataRole.UserRole + 25))
        self.assertNotEqual(row1.flags(), Qt.ItemFlag.NoItemFlags)
        self.assertEqual(row1.checkState(), Qt.CheckState.Unchecked)

        # Cancel on click keeps it unchecked
        with patch("ui.dialogs.themed_message_box.confirm_steam_owned_override", return_value=False) as m_c:
            dlg.on_depot_cell_clicked(1, 0)
            self.assertEqual(m_c.call_count, 1)
            self.assertEqual(row1.checkState(), Qt.CheckState.Unchecked)

        # Proceed checks it and exposes it in get_selected_depots()
        with patch("ui.dialogs.themed_message_box.confirm_steam_owned_override", return_value=True) as m_p:
            dlg.on_depot_cell_clicked(1, 0)
            self.assertEqual(m_p.call_count, 1)
            self.assertEqual(row1.checkState(), Qt.CheckState.Checked)
        self.assertEqual(dlg.get_selected_depots(), ["880002"])

        # Unchecking does NOT prompt
        with patch("ui.dialogs.themed_message_box.confirm_steam_owned_override") as m_off:
            dlg.on_depot_cell_clicked(1, 0)
            m_off.assert_not_called()
            self.assertEqual(row1.checkState(), Qt.CheckState.Unchecked)

        # Group toggle in Content View prompts once on Proceed and checks row
        dlg._refresh_view()
        with patch("ui.dialogs.themed_message_box.confirm_steam_owned_override", return_value=True) as m_grp:
            dlg._toggle_group("880010")
            self.assertEqual(m_grp.call_count, 1)
            self.assertEqual(row1.checkState(), Qt.CheckState.Checked)

    def test_05_bottom_titlebar_github_button_accent_and_svg_logo(self):
        """BottomTitleBar puts GitHub icon right of Discord in accent color and blinks version_label."""
        from utils.settings import get_settings
        from ui.bottom_titlebar import BottomTitleBar

        s = get_settings()
        s.setValue("accent_color", "#42A5F5")
        parent = QWidget()
        bar = BottomTitleBar(parent)

        self.assertIsNotNone(bar.github_button)
        self.assertFalse(hasattr(bar, "update_arrow_label"))
        self.assertIsNotNone(bar.logo_label.pixmap())
        self.assertFalse(bar.logo_label.pixmap().isNull())

        # Check right layout widget order: discord_button comes before github_button, before minimize_button
        right_lay = bar.discord_button.parentWidget().layout()
        right_widgets = [right_lay.itemAt(i).widget() for i in range(right_lay.count()) if right_lay.itemAt(i).widget()]
        self.assertLess(right_widgets.index(bar.discord_button), right_widgets.index(bar.github_button))
        self.assertLess(right_widgets.index(bar.github_button), right_widgets.index(bar.minimize_button))

        # Version label uses opacity effect and blinks on show_update_indicator(True)
        self.assertIs(bar.version_label.graphicsEffect(), bar.opacity_effect)
        self.assertEqual(bar.opacity_effect.opacity(), 1.0)
        bar.show_update_indicator(True)
        self.assertIn("Update available", bar.version_label.toolTip())
        self.assertEqual(bar.fade_animation.state(), bar.fade_animation.State.Running)
        bar.show_update_indicator(False)
        self.assertEqual(bar.fade_animation.state(), bar.fade_animation.State.Stopped)
        self.assertEqual(bar.opacity_effect.opacity(), 1.0)
        self.assertEqual(bar.version_label.toolTip(), "View credits")

        # Version label & GitHub icon both carry initial accent_color (#42a5f5)
        self.assertIn("#42A5F5", bar.version_label.styleSheet())
        px1 = bar.github_button.icon().pixmap(16, 16).toImage()
        sample1 = next(
            (px1.pixelColor(x, y) for x in range(16) for y in range(16) if px1.pixelColor(x, y).alpha() > 200),
            None,
        )
        self.assertIsNotNone(sample1)
        self.assertEqual(sample1.name().lower(), "#42a5f5")

        # Dynamic theme update recolors both version_label and github_button identically
        s.setValue("accent_color", "#FF7043")
        bar.update_style()
        self.assertIn("#FF7043", bar.version_label.styleSheet())
        px2 = bar.github_button.icon().pixmap(16, 16).toImage()
        sample2 = next(
            (px2.pixelColor(x, y) for x in range(16) for y in range(16) if px2.pixelColor(x, y).alpha() > 200),
            None,
        )
        self.assertIsNotNone(sample2)
        self.assertEqual(sample2.name().lower(), "#ff7043")

        with patch("PyQt6.QtGui.QDesktopServices.openUrl") as mock_open:
            bar.github_button.click()
            mock_open.assert_called_once_with(QUrl("https://github.com/niwia/ASSella"))

    def test_06_svg_logo_rollout(self):
        """ASSella SVG icon helpers produce non-null icons/pixmaps across UI surfaces."""
        from utils.color_utils import load_assella_app_icon, render_svg_pixmap
        from utils.paths import Paths
        from ui.dialogs.game_details.hero_header import _mode_logo_pixmap
        from ui.dialogs.download_backend_dialog import DownloadBackendDialog

        svg_path = Paths.resource("logo/icon.svg")
        self.assertTrue(svg_path.is_file())
        px = render_svg_pixmap(str(svg_path), 46, dpr=2.0)
        self.assertFalse(px.isNull())
        self.assertEqual(px.width(), 92)

        app_icon = load_assella_app_icon()
        self.assertFalse(app_icon.isNull())

        mode_px = _mode_logo_pixmap(is_atom=False, size=18)
        self.assertFalse(mode_px.isNull())

        backend_dlg = DownloadBackendDialog(app_id="10", game_name="Test", accent_color="#C06C84")
        self.assertIsNotNone(backend_dlg.assella_icon_lbl.pixmap())
        self.assertFalse(backend_dlg.assella_icon_lbl.pixmap().isNull())

    def test_07_credits_dialog_shows_pre_detected_update_and_handles_none_parent(self):
        """CreditsDialog opens cleanly with None parent and shows Install Update if update already detected."""
        from ui.dialogs.credits import CreditsDialog

        dlg_none = CreditsDialog(None)
        self.assertEqual(dlg_none.check_btn.text(), "Check for Updates")

        class _FakeMain(QWidget):
            def __init__(self):
                super().__init__()
                self._tool_update_available_flag = True
                self._latest_remote_version = "3.1.1"
                self._latest_update_source = "github"
                self.update_called = False

            def run_self_update(self):
                self.update_called = True

        fake_main = _FakeMain()
        dlg = CreditsDialog(fake_main)
        self.assertEqual(dlg.status_label.text(), "Update available: v3.1.1")
        self.assertEqual(dlg.check_btn.text(), "Install Update")
        dlg.check_btn.click()
        self.assertTrue(fake_main.update_called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
