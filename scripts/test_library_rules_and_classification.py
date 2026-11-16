#!/usr/bin/env python3
"""
Hermetic regression suite for:
  1. Beta `install.sh` (with R2, SVG & Beta/Stable chooser) + `install_stable.sh` (strict stable-only) without leftover `install_new.sh`.
  2. Settings -> Downloads -> Depot selection `Ignore demos from Steam DLC mode`
     (default OFF, suffix demo/_demo filter without false-positiving "DemonCrawl").
  3. Placeholder `appid_<id>` name healing across GameManager, acf_scanner,
     filter_sort, GamesCache, steam_tab_cache, and atom_pending.
  4. Size: 0 B fallback in acf_scanner & filter_sort.
  5. Multi-line `.depot` DLC-only guard (line-1 DLC + line-2 base stays non-DLC).
  6. Q4 ownership & classification matrix:
     - AdditionalApps without DecryptionKeys -> ACCELA (Pass 1 & Pass 2)
     - AdditionalApps with parent OR depot key in DecryptionKeys -> at0-m
     - Unowned physical `.assella` marker with leftover dlc_only_mode=True -> ACCELA fallback without mutating QSettings
     - Steam tab strictly owned (`localconfig.vdf` apptickets) and excludes AdditionalApps.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_TMP = tempfile.mkdtemp(prefix="assella-lib-rules-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local" / "share")
os.environ["XDG_CACHE_HOME"] = str(Path(_TMP) / ".cache")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QListWidget

_APP = QApplication.instance() or QApplication(["-platform", "offscreen"])


class _FakeDB:
    NAMES = {
        "108600": "Project Zomboid",
        "262060": "Darkest Dungeon®",
        "1942280": "Brotato",
        "2185060": "Two Point Museum",
        "3473250": "BroomSweeper",
    }
    DEPOTS = {
        "2185060": {
            "2999843": {"name": "Two Point Museum: Zooseum", "dlcappid": "2999840"},
            "2185063": {"name": "Two Point Museum Linux", "config": {"oslist": "linux"}},
        },
        "3473250": {
            "3473251": {"name": "BroomSweeper Content"},
        },
    }

    def get_app_info(self, appid, bypass_expiration=False):
        aid = str(appid)
        out = {}
        if aid in self.NAMES:
            out["name"] = self.NAMES[aid]
        if aid in self.DEPOTS:
            out["depots"] = self.DEPOTS[aid]
        return out or None

    def get_at0m_update_status(self, appid):
        return None


class TestLibraryRulesAndClassification(unittest.TestCase):
    def setUp(self):
        self.cfg_dir = Path(_TMP) / ".config" / "SLSsteam"
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        self.cfg_path = self.cfg_dir / "config.yaml"
        self.cfg_path.write_text(
            "Plugins: yes\nAdditionalApps: {}\nAdditionalDepots: {}\nDecryptionKeys: {}\n",
            encoding="utf-8",
        )
        self.data_dir = Path(_TMP) / ".local" / "share" / "ACCELA"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        from utils.settings import get_settings
        s = get_settings()
        s.clear()
        s.sync()

    def test_01_beta_and_stable_installers(self):
        """Beta install.sh preserves R2/SVG/chooser (replacing install_new.sh) and install_stable.sh is strict stable-only."""
        self.assertFalse((REPO_ROOT / "install_new.sh").exists())

        beta_path = REPO_ROOT / "install.sh"
        self.assertTrue(beta_path.is_file())
        self.assertTrue(os.access(beta_path, os.X_OK))
        beta_text = beta_path.read_text(encoding="utf-8")
        self.assertIn("R2_RELEASES_PUBLIC_URL", beta_text)
        self.assertIn("SCALABLE_ICON_DIR", beta_text)
        self.assertIn("resolve_beta_and_stable_releases", beta_text)

        stable_path = REPO_ROOT / "install_stable.sh"
        self.assertTrue(stable_path.is_file())
        self.assertTrue(os.access(stable_path, os.X_OK))
        stable_text = stable_path.read_text(encoding="utf-8")
        self.assertIn("R2_RELEASES_PUBLIC_URL", stable_text)
        self.assertIn("is_strict_stable_release", stable_text)

    def test_02_library_rules_helpers(self):
        """Placeholder detection, demo suffix filter, multi-line depot check, and key matching work accurately."""
        from utils.library_rules import (
            all_depots_are_dlc,
            extract_depot_ids_from_file,
            has_decryption_key_for_game,
            is_demo_game_name,
            is_placeholder_game_name,
        )

        self.assertTrue(is_placeholder_game_name("appid_262060", "262060"))
        self.assertTrue(is_placeholder_game_name("App 1942280", "1942280"))
        self.assertTrue(is_placeholder_game_name("262060", "262060"))
        self.assertTrue(is_placeholder_game_name("", "262060"))
        self.assertFalse(is_placeholder_game_name("Darkest Dungeon®", "262060"))
        self.assertFalse(is_placeholder_game_name("Appalachian Trail", "262060"))

        self.assertTrue(is_demo_game_name("Plants vs. Zombies Demo"))
        self.assertTrue(is_demo_game_name("Game_demo"))
        self.assertTrue(is_demo_game_name("Game-Demo"))
        self.assertTrue(is_demo_game_name("Game [Demo]"))
        self.assertTrue(is_demo_game_name("Demo"))
        self.assertFalse(is_demo_game_name("DemonCrawl"))
        self.assertFalse(is_demo_game_name("Pandemonium"))

        with tempfile.NamedTemporaryFile("w", suffix=".depot", delete=False) as tf:
            tf.write("2999843: 111\n2185063: 222\n228980: 333\n")
            depot_path = tf.name
        try:
            ids = extract_depot_ids_from_file(depot_path)
            self.assertEqual(ids, ["2999843", "2185063", "228980"])
            with patch("utils.dlc_helpers.is_dlc_depot", side_effect=lambda d, **kw: str(d) == "2999843"):
                self.assertFalse(all_depots_are_dlc(ids))
                self.assertTrue(all_depots_are_dlc(["2999843", "228980"]))
        finally:
            os.unlink(depot_path)

        keys = {"3473251": "deadbeef" * 8, "228980": "1111" * 16}
        self.assertTrue(has_decryption_key_for_game("3473250", keys, ["3473251"]))
        self.assertTrue(has_decryption_key_for_game("3473251", keys, []))
        self.assertFalse(has_decryption_key_for_game("108600", keys, ["228980"]))
        self.assertFalse(has_decryption_key_for_game("108600", keys, ["108601"]))

    def test_03_ignore_steam_demos_setting_ui_and_persistence(self):
        """Settings -> Downloads -> Depot selection exposes ignore_steam_demos defaulting to False."""
        from ui.dialogs.settings_tabs.downloads_tab import create_downloads_tab
        from ui.dialogs.settings import SettingsDialog

        dlg = SettingsDialog()
        self.addCleanup(dlg.close)
        # Switch to Downloads tab (lazily built) so checkbox widget instantiates
        dlg.tab_widget.setCurrentIndex(1)
        _APP.processEvents()

        cb = getattr(dlg, "ignore_steam_demos_checkbox", None)
        if cb is None:
            # Force build directly if lazy index differed
            page = create_downloads_tab(dlg)
            self.addCleanup(page.deleteLater)
            cb = dlg.ignore_steam_demos_checkbox

        self.assertIsNotNone(cb)
        self.assertEqual(cb.label.text(), "Ignore demos from Steam DLC mode")
        self.assertFalse(cb.isChecked())

        cb.setChecked(True)
        dlg._save_download_settings()
        from utils.settings import get_settings
        self.assertTrue(get_settings().value("ignore_steam_demos", False, type=bool))

    def test_04_game_manager_classification_and_multiline_depot(self):
        """GameManager handles AdditionalApps vs DecryptionKeys, multi-line .depot, unowned .assella fallback, and appid_ names."""
        from managers.game_manager import GameManager
        from utils.settings import get_settings

        lib_dir = Path(_TMP) / "SteamLibGM"
        steamapps = lib_dir / "steamapps"
        common = steamapps / "common"
        depots_dir = self.data_dir / "depots"
        depots_dir.mkdir(parents=True, exist_ok=True)

        # 1) AdditionalApps WITHOUT DecryptionKeys -> ASSella + placeholder name fixed
        pz_dir = common / "ProjectZomboid"
        pz_dir.mkdir(parents=True, exist_ok=True)
        (pz_dir / "zomboid.bin").write_text("x", encoding="utf-8")
        (steamapps / "appmanifest_108600.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"108600"\n\t"name"\t\t"appid_108600"\n\t"installdir"\t\t"ProjectZomboid"\n\t"SizeOnDisk"\t\t"1024"\n}\n',
            encoding="utf-8",
        )

        # 2) AdditionalApps WITH ONLY depot key in DecryptionKeys -> AT0-M
        bs_dir = common / "BroomSweeper"
        bs_dir.mkdir(parents=True, exist_ok=True)
        (bs_dir / "broom.exe").write_text("x", encoding="utf-8")
        (steamapps / "appmanifest_3473250.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"3473250"\n\t"name"\t\t"BroomSweeper"\n\t"installdir"\t\t"BroomSweeper"\n\t"SizeOnDisk"\t\t"2048"\n}\n',
            encoding="utf-8",
        )

        # 3) Multi-line .depot with DLC depot on line 1 and base depot on line 2 + .assella marker
        #    Must NOT auto-flip to dlc_only or scrub 2185060 from AdditionalApps!
        tpm_dir = common / "Two Point Museum"
        tpm_dir.mkdir(parents=True, exist_ok=True)
        (tpm_dir / ".assella").write_text("", encoding="utf-8")
        (tpm_dir / "TPM.bin").write_text("x" * 500, encoding="utf-8")
        (depots_dir / "2185060.depot").write_text(
            "2999843: 11111\n2185063: 22222\n",
            encoding="utf-8",
        )
        (steamapps / "appmanifest_2185060.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"2185060"\n\t"name"\t\t"Two Point Museum"\n\t"installdir"\t\t"Two Point Museum"\n\t"SizeOnDisk"\t\t"0"\n}\n',
            encoding="utf-8",
        )

        # 4) Unowned physical .assella marker with stale dlc_only_mode=True in QSettings
        #    Must fall back to ACCELA WITHOUT modifying QSettings!
        get_settings().setValue("dlc_only_mode/2185060", True)

        self.cfg_path.write_text(
            "Plugins: yes\nAdditionalApps:\n  - 108600\n  - 3473250\n  - 2185060\n"
            "AdditionalDepots:\n  - 3473251\nDecryptionKeys:\n  3473251: " + ("a" * 64) + "\n",
            encoding="utf-8",
        )

        gm = GameManager()
        gm._scan_cancelled = False
        scanned = []
        with patch("utils.plugin_games.get_all_plugin_games", return_value={}), \
             patch("utils.plugin_games.build_dlc_reverse_map", return_value={}), \
             patch("utils.plugin_games.get_plugin_game", return_value=None), \
             patch("managers.db_manager.DatabaseManager", return_value=_FakeDB()):
            gm._scan_library(
                str(lib_dir),
                str(lib_dir),
                scanned,
                owned_appids={"262060"},
                additional_apps={"108600", "3473250", "2185060"},
            )

        by_id = {g["appid"]: g for g in scanned}
        self.assertIn("108600", by_id)
        self.assertEqual(by_id["108600"]["source"], "ACCELA")
        self.assertFalse(by_id["108600"]["is_atom"])
        self.assertEqual(by_id["108600"]["game_name"], "Project Zomboid")

        self.assertIn("3473250", by_id)
        self.assertEqual(by_id["3473250"]["source"], "at0-m")
        self.assertTrue(by_id["3473250"]["is_atom"])

        self.assertIn("2185060", by_id)
        self.assertEqual(by_id["2185060"]["source"], "ACCELA")
        self.assertFalse(by_id["2185060"]["is_dlc_only"])
        # Persisted QSettings untouched per user request
        self.assertTrue(get_settings().value("dlc_only_mode/2185060", False, type=bool))
        # 2185060 was NOT scrubbed from AdditionalApps by multi-line .depot check
        from utils.yaml.apps import get_additional_apps
        self.assertIn("2185060", get_additional_apps(self.cfg_path))

    def test_05_acf_scanner_and_filter_sort_and_cache_healing(self):
        """acf_scanner & filter_sort enforce owned-only, size fallback, name resolution, and demo filter."""
        from ui.dialogs.library.acf_scanner import scan_acf_files
        from ui.dialogs.library.filter_sort import LibraryFilterSortMixin
        from ui.dialogs.library.steam_tab_cache import load_steam_cache, save_steam_cache
        from ui.dialogs.library.tab_bar import TAB_STEAM
        from utils.games_cache import GamesCache
        from utils.settings import get_settings

        lib_dir = Path(_TMP) / "SteamLibACF"
        steamapps = lib_dir / "steamapps"
        common = steamapps / "common"
        for folder in ("DarkestDungeon", "Two Point Museum", "DemonCrawl", "PvZ Demo"):
            d = common / folder
            d.mkdir(parents=True, exist_ok=True)
            (d / "data.bin").write_bytes(b"z" * 4096)

        # Owned with appid_<id> placeholder + 0 ACF size
        (steamapps / "appmanifest_262060.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"262060"\n\t"name"\t\t"appid_262060"\n\t"StateFlags"\t\t"4"\n\t"installdir"\t\t"DarkestDungeon"\n\t"SizeOnDisk"\t\t"0"\n}\n',
            encoding="utf-8",
        )
        # Unowned ACF outside AdditionalApps -> must NOT show in Steam tab
        (steamapps / "appmanifest_2185060.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"2185060"\n\t"name"\t\t"Two Point Museum"\n\t"StateFlags"\t\t"4"\n\t"installdir"\t\t"Two Point Museum"\n\t"SizeOnDisk"\t\t"0"\n}\n',
            encoding="utf-8",
        )
        # Owned demo game
        (steamapps / "appmanifest_3591.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"3591"\n\t"name"\t\t"Plants vs. Zombies Demo"\n\t"StateFlags"\t\t"4"\n\t"installdir"\t\t"PvZ Demo"\n\t"SizeOnDisk"\t\t"4096"\n}\n',
            encoding="utf-8",
        )
        # Owned title containing "Demon" (NOT a demo!)
        (steamapps / "appmanifest_1141220.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"1141220"\n\t"name"\t\t"DemonCrawl"\n\t"StateFlags"\t\t"4"\n\t"installdir"\t\t"DemonCrawl"\n\t"SizeOnDisk"\t\t"4096"\n}\n',
            encoding="utf-8",
        )

        with patch("utils.plugin_games.get_all_plugin_games", return_value={}), \
             patch("managers.db_manager.DatabaseManager", return_value=_FakeDB()):
            scanned = scan_acf_files(
                library_paths=[str(lib_dir)],
                owned_appids={"262060", "3591", "1141220"},
                additional_apps=set(),
            )

        by_id = {g["appid"]: g for g in scanned}
        self.assertEqual(set(by_id.keys()), {"262060", "3591", "1141220"})
        self.assertEqual(by_id["262060"]["game_name"], "Darkest Dungeon®")
        self.assertEqual(by_id["262060"]["size_on_disk"], 4096)

        # Verify steam_tab_cache & GamesCache heal stale appid_<id> on load
        save_steam_cache([{"appid": "262060", "game_name": "appid_262060", "install_dir": "DarkestDungeon", "size_on_disk": 0}])
        with patch("managers.db_manager.DatabaseManager", return_value=_FakeDB()):
            healed_cache = load_steam_cache()
        self.assertEqual(healed_cache[0]["game_name"], "Darkest Dungeon®")

        gc_file = self.data_dir / "db" / "games_cache.json"
        gc_file.parent.mkdir(parents=True, exist_ok=True)
        gc_file.write_text(
            json.dumps({"games": [{"appid": "108600", "game_name": "appid_108600", "install_dir": "ProjectZomboid", "size_on_disk": 100}]}),
            encoding="utf-8",
        )
        with patch("utils.games_cache._get_games_cache_path", return_value=gc_file), \
             patch("managers.db_manager.DatabaseManager", return_value=_FakeDB()):
            healed_gc = GamesCache()
        self.assertEqual(healed_gc.get_games()[0]["game_name"], "Project Zomboid")
        self.assertTrue(healed_gc._dirty)

        # Verify atom_pending heals placeholder name too
        from utils.atom_pending import get_pending_atom_games
        plugin_lib_data = {
            "108600": {
                "name": "appid_108600",
                "depots": ["108604"],
                "keys": {"108604": "deadbeef" * 8},
                "is_atom": True,
            }
        }
        with patch("utils.plugin_games.get_all_plugin_games", return_value=plugin_lib_data), \
             patch("core.steam_helpers.get_steam_libraries", return_value=[]), \
             patch("utils.atom_pending._config_state", return_value=({"108600"}, {"108604": "deadbeef" * 8})), \
             patch("managers.db_manager.DatabaseManager", return_value=_FakeDB()):
            pending = get_pending_atom_games(installed_appids=[])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["game_name"], "Project Zomboid")

        # Verify LibraryFilterSortMixin respects owned_appids, merges size from manager, and applies ignore_steam_demos
        class _DummyLib(LibraryFilterSortMixin):
            def __init__(self, steam_items, mgr_items):
                self._closing = False
                self._active_tab = TAB_STEAM
                self._steam_tab_games = steam_items
                self.game_manager = SimpleNamespace(get_all_games=lambda: mgr_items)
                self.tab_bar = SimpleNamespace(
                    current_tab=lambda: TAB_STEAM,
                    update_counts=MagicMock(),
                )
                self.settings = None
                self._active_fetchers = {}
                self._image_fetch_queue = []
                self._pending_image_fetches = []
                self._items_by_appid = {}
                self._manifest_mtimes = {}
                self.search_input = QLineEdit()
                self.sort_combo = QComboBox()
                self.sort_combo.addItem("Pinned First", "pinned_first")
                self.games_list = QListWidget()
                self.info_label = QLabel()
                self._rendered = []

            def _add_game_to_list(self, game):
                self._rendered.append(game)
                return int(game.get("size_on_disk") or 0)

            def _apply_search_filter(self):
                return None

            def _start_pending_image_fetches(self):
                return None

        cached_steam = [
            {"appid": "262060", "game_name": "appid_262060", "install_dir": "DarkestDungeon", "size_on_disk": 0},
            {"appid": "2185060", "game_name": "Two Point Museum", "install_dir": "Two Point Museum", "size_on_disk": 0},
            {"appid": "3591", "game_name": "Plants vs. Zombies Demo", "install_dir": "PvZ Demo", "size_on_disk": 4096},
            {"appid": "1141220", "game_name": "DemonCrawl", "install_dir": "DemonCrawl", "size_on_disk": 4096},
        ]
        mgr_items = [
            {"appid": "262060", "game_name": "Darkest Dungeon®", "size_on_disk": 99999, "is_dlc_only": True, "is_accela_install": False},
        ]

        lib_obj = _DummyLib(cached_steam, mgr_items)
        with patch("core.steam_helpers.get_owned_steam_appids", return_value={"262060", "3591", "1141220"}), \
             patch("utils.plugin_games.load_plugin_library", return_value={}), \
             patch("utils.atom_pending.get_pending_atom_games", return_value=[]), \
             patch("managers.db_manager.DatabaseManager", return_value=_FakeDB()):
            lib_obj._refresh_game_list()
            ids_off = {g["appid"]: g for g in lib_obj._rendered}
            self.assertEqual(set(ids_off.keys()), {"262060", "3591", "1141220"})
            self.assertEqual(ids_off["262060"]["game_name"], "Darkest Dungeon®")
            self.assertEqual(ids_off["262060"]["size_on_disk"], 99999)

            lib_obj._rendered = []
            get_settings().setValue("ignore_steam_demos", True)
            lib_obj._refresh_game_list()
            ids_on = {g["appid"]: g for g in lib_obj._rendered}
            self.assertEqual(set(ids_on.keys()), {"262060", "1141220"})


if __name__ == "__main__":
    unittest.main()
