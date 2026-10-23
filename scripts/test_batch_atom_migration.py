#!/usr/bin/env python3
"""
Test Suite for Batch AT0-M Migration:
- Strict exclusion validation (DLC-only mode, already AT0-M, uninstalled)
- Batch conversion engine functionality
- UI Dialog structure & signal connections
- Migration safety: config commit verified, markers retired last
- Depot/key publishing: only mounted depots get a key, owner tag is the parent AppID
"""

import os
import sys
from pathlib import Path

# Add src to PYTHONPATH
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

# Ensure QApplication exists for UI tests
app = QApplication.instance()
if not app:
    app = QApplication(sys.argv)

from ui.dialogs.library.batch_migration_dialog import (
    BatchAtomMigrationConfirmDialog,
    BatchAtomMigrationWorker,
)
from utils.plugin_games import (
    batch_convert_games_to_atom,
    build_depot_comment,
    build_key_comment,
    collect_game_depots_and_keys,
    comment_has_owner_tag,
    migrate_game_to_atom,
)
from utils.steam_acf import (
    ACF_DEFERRED,
    ACF_SKIPPED,
    ACF_WRITTEN,
    atomic_write_acf,
    ensure_acf_has_installed_depots,
)


class TestBatchAtomMigration(unittest.TestCase):
    """Verify batch AT0-M migration rules, dialogs, and engine."""

    def test_confirmation_dialog_eligible_and_skipped(self):
        """Test BatchAtomMigrationConfirmDialog renders eligible and skipped games with correct badges."""
        eligible = [
            {"appid": "12345", "game_name": "Test Game Alpha", "install_path": "/tmp/alpha"},
            {"appid": "67890", "game_name": "Test Game Beta", "install_path": "/tmp/beta"},
        ]
        skipped = [
            ({"appid": "11111", "game_name": "DLC Only Game"}, "DLC-Only Mode"),
            ({"appid": "22222", "game_name": "Pinned Game"}, "Pinned Build / Manifest"),
            ({"appid": "33333", "game_name": "Atom Game"}, "Already in AT0-M"),
            ({"appid": "44444", "game_name": "Missing Game"}, "Not Installed Locally"),
        ]

        dlg = BatchAtomMigrationConfirmDialog(
            eligible_games=eligible,
            skipped_games=skipped,
            accent_color="#38bdf8",
        )
        self.assertIsNotNone(dlg)
        self.assertEqual(len(dlg.eligible_games), 2)
        self.assertEqual(len(dlg.skipped_games), 4)

        # Check badge colors for reasons
        col, bg = dlg._get_badge_colors("DLC-Only Mode")
        self.assertEqual(col, "#f59e0b")

        col, bg = dlg._get_badge_colors("Pinned Build / Manifest")
        self.assertEqual(col, "#06b6d4")

        col, bg = dlg._get_badge_colors("Already in AT0-M")
        self.assertEqual(col, "#a855f7")

        col, bg = dlg._get_badge_colors("Not Installed Locally")
        self.assertEqual(col, "#f43f5e")

    def test_worker_signals_and_cancellation(self):
        """Test BatchAtomMigrationWorker signals and cancel flag."""
        worker = BatchAtomMigrationWorker([{"appid": "100", "game_name": "Cancel Test"}])
        self.assertFalse(worker._is_canceled)
        worker.cancel()
        self.assertTrue(worker._is_canceled)

    def test_worker_survives_engine_exception(self):
        """A raising engine must not leave the progress dialog spinning forever."""
        with patch(
            "utils.plugin_games.batch_convert_games_to_atom",
            side_effect=RuntimeError("boom"),
        ):
            worker = BatchAtomMigrationWorker([{"appid": "100", "game_name": "Crash Test"}])
            received = []
            worker.finished.connect(received.append)
            worker.run()
        self.assertEqual(len(received), 1)
        self.assertEqual(len(received[0]["failed"]), 1)
        self.assertIn("boom", received[0]["failed"][0]["reason"])

    @patch("utils.plugin_games.collect_game_depots_and_keys")
    @patch("utils.plugin_games.save_plugin_library")
    @patch("utils.plugin_games.get_user_config_path")
    @patch("utils.sls_bridge.SLSBridge.notify_reload")
    def test_batch_convert_engine_skips_zero_keys(
        self, mock_reload, mock_cfg_path, mock_save_lib, mock_collect
    ):
        """Games with zero decryption keys are skipped; the rest still migrate."""
        mock_cfg_path.return_value = Path("/tmp/nonexistent_config.yaml")

        def mock_collect_fn(appid, game_data=None, allow_hubcap_fetch=True):
            if str(appid) == "100":
                return (["101"], {}, {})  # no keys
            else:
                return (["201"], {"201": "a" * 64}, {"201": "Content"})

        mock_collect.side_effect = mock_collect_fn

        games = [
            {"appid": "100", "game_name": "No Key Game", "install_path": "/tmp"},
            {"appid": "200", "game_name": "Valid Game", "install_path": "/tmp"},
        ]

        progress_calls = []

        def _prog(cur, tot, name, msg):
            progress_calls.append((cur, tot, name))

        results = batch_convert_games_to_atom(games, progress_callback=_prog)

        # A missing config.yaml aborts the whole batch: the games keep their
        # ACCELA identity instead of being reported as migrated.
        self.assertEqual(len(results["migrated"]), 0)
        self.assertEqual(len(results["failed"]), 2)
        failed_map = {f["appid"]: f["reason"] for f in results["failed"]}
        self.assertIn("No valid decryption keys", failed_map["100"])
        self.assertIn("config.yaml not found", failed_map["200"])
        self.assertTrue(len(progress_calls) > 0)

    @patch("utils.dlc_helpers.is_dlc_only_mode")
    @patch("utils.settings.get_settings")
    def test_strict_exclusion_rules(self, mock_settings, mock_is_dlc):
        """Verify strict exclusion rules for DLC-only mode."""
        mock_is_dlc.side_effect = lambda aid: str(aid) == "300"

        from ui.dialogs.library.filter_sort import game_is_dlc_only

        candidates = [
            {"appid": "100", "game_name": "Valid ASSella Game", "is_accela_install": True, "install_path": "/tmp"},
            {"appid": "200", "game_name": "Game With Pin", "is_accela_install": True, "install_path": "/tmp"},
            {"appid": "300", "game_name": "DLC-Only Game", "is_accela_install": True, "install_path": "/tmp"},
            {"appid": "400", "game_name": "Already AT0-M Game", "is_accela_install": True, "is_atom": True, "install_path": "/tmp"},
            {"appid": "500", "game_name": "Steam Pure Game", "is_accela_install": False, "install_path": "/tmp"},
        ]

        eligible = []
        skipped = []

        for g in candidates:
            aid = str(g.get("appid", ""))
            # 1. Already in AT0-M
            if g.get("is_atom") or g.get("is_vapor") or g.get("is_plugin_game"):
                skipped.append((g, "Already in AT0-M"))
                continue
            # 2. Not ASSella install
            if not g.get("is_accela_install"):
                skipped.append((g, "Not ASSella Install"))
                continue
            # 3. DLC-Only
            if mock_is_dlc(aid) or game_is_dlc_only(g):
                skipped.append((g, "DLC-Only Mode"))
                continue
            eligible.append(g)

        self.assertEqual(len(eligible), 2)
        eligible_aids = [g["appid"] for g in eligible]
        self.assertIn("100", eligible_aids)
        self.assertIn("200", eligible_aids)
        self.assertEqual(len(skipped), 3)
        skipped_map = {item[0]["appid"]: item[1] for item in skipped}
        self.assertEqual(skipped_map["300"], "DLC-Only Mode")
        self.assertEqual(skipped_map["400"], "Already in AT0-M")
        self.assertEqual(skipped_map["500"], "Not ASSella Install")


class TestOwnerTags(unittest.TestCase):
    """The trailing tag of a config comment must be the OWNER AppID.

    The game details view attributes a depot or key to a game by looking for
    the AppID inside the comment, so a depot tagged with its own id is never
    matched back to the game it belongs to.
    """

    def test_depot_comment_carries_parent_appid(self):
        comment = build_depot_comment("Some Game", "555", "Content", "111")
        self.assertIn("(111)", comment)
        self.assertNotIn("(555)", comment)

    def test_depot_comment_without_depot_name(self):
        comment = build_depot_comment("Some Game", "555", "", "111")
        self.assertEqual(comment, "Some Game (111)")

    def test_key_comment_marks_root_appkey(self):
        comment = build_key_comment("Some Game", "111", "", "111")
        self.assertIn("[AppKey]", comment)
        self.assertIn("(111)", comment)

    def test_key_comment_carries_parent_appid(self):
        comment = build_key_comment("Some Game", "555", "Content", "111")
        self.assertIn("(111)", comment)
        self.assertNotIn("(555)", comment)

    def test_substring_appid_is_not_an_owner_match(self):
        """AppID 220 must not claim a depot whose tag is 2200.

        A false owner match lets unregister collect another game's depots and
        delete that game's ManifestIds pins (remove_manifest_ids).
        """
        comment = build_depot_comment("Other Game", "777", "", "2200")
        self.assertTrue(comment_has_owner_tag(comment, "2200"))
        self.assertFalse(comment_has_owner_tag(comment, "220"))

    def test_owner_tag_matches_the_whole_tag(self):
        self.assertTrue(comment_has_owner_tag("Game - DLC (220)", "220"))
        self.assertTrue(comment_has_owner_tag("Game [AppKey] (220)", 220))
        self.assertFalse(comment_has_owner_tag("Game (2200)", "220"))
        self.assertFalse(comment_has_owner_tag("Game", "220"))


class TestAcfHelper(unittest.TestCase):
    """The shared ACF helper owns the InstalledDepots merge."""

    def _write_acf(self, tmpdir, body):
        acf = Path(tmpdir) / "appmanifest_111.acf"
        acf.write_text(body, encoding="utf-8")
        return str(acf)

    def test_missing_depots_are_added_to_existing_block(self):
        """A depot missing from InstalledDepots must be added, not skipped."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            acf = self._write_acf(
                tmp,
                '"AppState"\n'
                '\t{\n'
                '\t\t"InstalledDepots"\n'
                '\t\t{\n'
                '\t\t\t"555"\n'
                '\t\t\t{\n'
                '\t\t\t\t"manifest"\t\t"0"\n'
                '\t\t\t}\n'
                '\t\t}\n'
                '\t}\n',
            )
            status = ensure_acf_has_installed_depots(
                acf, "111", ["555", "666"], {}, steam_running=False
            )
            self.assertEqual(status, ACF_WRITTEN)
            content = Path(acf).read_text(encoding="utf-8")
            self.assertIn('"555"', content)
            self.assertIn('"666"', content)

    def test_write_is_deferred_while_steam_runs(self):
        """Steam rewrites appmanifest files on exit - never write while it runs."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            acf = self._write_acf(tmp, '"AppState"\n\t{\n\t}\n')
            before = Path(acf).read_text(encoding="utf-8")
            status = ensure_acf_has_installed_depots(
                acf, "111", ["555"], {}, steam_running=True
            )
            self.assertEqual(status, ACF_DEFERRED)
            self.assertEqual(Path(acf).read_text(encoding="utf-8"), before)

    def _complete_acf(self, tmpdir):
        """An ACF that already lists the depot and has a real SizeOnDisk."""
        return self._write_acf(
            tmpdir,
            '"AppState"\n'
            '\t{\n'
            '\t\t"SizeOnDisk"\t\t"1234"\n'
            '\t\t"InstalledDepots"\n'
            '\t\t{\n'
            '\t\t\t"555"\n'
            '\t\t\t{\n'
            '\t\t\t\t"manifest"\t\t"0"\n'
            '\t\t\t\t"size"\t\t"1234"\n'
            '\t\t\t}\n'
            '\t\t}\n'
            '\t}\n',
        )

    def test_correct_manifest_is_skipped_even_while_steam_runs(self):
        """Nothing to change must not be reported as deferred.

        Steam is almost always running. Deferring an already-correct manifest
        would flag every game of a migration as "run it again", which is how a
        no-op used to look like a required re-run.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            acf = self._complete_acf(tmp)
            status = ensure_acf_has_installed_depots(
                acf, "111", ["555"], {}, steam_running=True
            )
            self.assertEqual(status, ACF_SKIPPED)

    def test_no_op_merge_does_not_write_or_back_up(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            acf = self._complete_acf(tmp)
            status = ensure_acf_has_installed_depots(
                acf, "111", ["555"], {}, steam_running=False
            )
            self.assertEqual(status, ACF_SKIPPED)
            self.assertEqual(list(Path(tmp).glob("appmanifest_111.acf.assella-bak-*")), [])

    def test_atomic_write_keeps_a_backup(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            acf = Path(tmp) / "appmanifest_111.acf"
            acf.write_text("original", encoding="utf-8")
            self.assertTrue(atomic_write_acf(str(acf), "updated"))
            self.assertEqual(acf.read_text(encoding="utf-8"), "updated")
            backups = list(Path(tmp).glob("appmanifest_111.acf.assella-bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "original")

    def test_real_manifest_is_written_and_stateflags_restored(self):
        """When manifest GID is known, write it instead of '0' and restore StateFlags to 4."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            acf = self._write_acf(
                tmp,
                '"AppState"\n'
                '\t{\n'
                '\t\t"StateFlags"\t\t"518"\n'
                '\t\t"InstalledDepots"\n'
                '\t\t{\n'
                '\t\t\t"555"\n'
                '\t\t\t{\n'
                '\t\t\t\t"manifest"\t\t"0"\n'
                '\t\t\t}\n'
                '\t\t}\n'
                '\t}\n',
            )
            game_data = {"manifests": {"555": "987654321012345"}}
            status = ensure_acf_has_installed_depots(
                acf, "111", ["555"], game_data, steam_running=False
            )
            self.assertEqual(status, ACF_WRITTEN)
            content = Path(acf).read_text(encoding="utf-8")
            self.assertIn('"manifest"\t\t"987654321012345"', content)
            self.assertIn('"StateFlags"\t\t"4"', content)


class TestDepotScopeResolution(unittest.TestCase):
    """The depot scope must never drop a depot that is on disk.

    ``depot_selection/{appid}`` is rewritten by the update flow as a
    pre-selection for the *next* download, so trusting it to narrow the
    mounted set silently skips depots the user already owns.
    """

    def _patch_scope(self, saved, installed):
        return (
            patch("utils.dlc_helpers.get_saved_depot_selection", return_value=list(saved)),
            patch("utils.dlc_helpers.get_installed_depot_ids", return_value=list(installed)),
        )

    def test_stale_scope_does_not_drop_installed_depots(self):
        from utils.dlc_helpers import resolve_depot_scope

        saved_patch, installed_patch = self._patch_scope(
            saved=["555"], installed=["555", "666"]
        )
        with saved_patch, installed_patch:
            kept, dropped = resolve_depot_scope("111", ["555", "666", "777"])
        self.assertIn("555", kept)
        self.assertIn("666", kept)  # installed, so it survives the stale scope
        self.assertEqual(dropped, ["777"])  # outside the scope and not installed

    def test_scope_without_intersection_keeps_everything(self):
        from utils.dlc_helpers import resolve_depot_scope

        saved_patch, installed_patch = self._patch_scope(saved=["999"], installed=[])
        with saved_patch, installed_patch:
            kept, dropped = resolve_depot_scope("111", ["555", "666"])
        self.assertEqual(kept, ["555", "666"])
        self.assertEqual(dropped, [])

    def test_scope_matching_keeps_selection(self):
        from utils.dlc_helpers import resolve_depot_scope

        saved_patch, installed_patch = self._patch_scope(
            saved=["555", "666"], installed=["555", "666"]
        )
        with saved_patch, installed_patch:
            kept, dropped = resolve_depot_scope("111", ["555", "666", "777"])
        self.assertEqual(kept, ["555", "666"])
        self.assertEqual(dropped, ["777"])

    def test_no_scope_keeps_everything(self):
        from utils.dlc_helpers import resolve_depot_scope

        saved_patch, installed_patch = self._patch_scope(saved=[], installed=[])
        with saved_patch, installed_patch:
            kept, dropped = resolve_depot_scope("111", ["555", "666"])
        self.assertEqual(kept, ["555", "666"])
        self.assertEqual(dropped, [])


class TestMountedDepotKeys(unittest.TestCase):
    """Only a depot that is actually mounted may get a decryption key.

    A key for an unmounted depot makes DecryptionKeys advertise content the
    config does not serve - the exact mismatch reported by the other tool.
    """

    def _config_with_app(self, tmpdir, appid="111"):
        cfg = Path(tmpdir) / "config.yaml"
        cfg.write_text(
            "AdditionalApps:\n"
            "  - 999 # Another Game\n"
            "AdditionalDepots:\n"
            "  - 888 # Another Depot\n"
            "DecryptionKeys:\n"
            "  888: " + ("b" * 64) + " # Another Depot\n",
            encoding="utf-8",
        )
        return cfg

    def test_refused_depot_gets_no_key(self):
        """A depot the editor refuses must not appear in DecryptionKeys."""
        from utils.yaml.core import batch_config_edit

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config_with_app(tmp)
            with batch_config_edit(cfg) as editor:
                # 999 is a registered base AppID in AdditionalApps and is not a
                # known DLC -> the editor refuses to mount it as a depot.
                editor.add_depot("999", comment="Other Game (111)", app_id="111")
                self.assertFalse(editor.contains_depot("999"))
                editor.add_depot("555", comment="Game - Content (111)", app_id="111")
                self.assertTrue(editor.contains_depot("555"))
                # Only the mounted depot gets a key.
                for did in ("999", "555"):
                    if editor.contains_depot(did):
                        editor.add_key(did, "a" * 64, comment="Game (111)")

            content = cfg.read_text(encoding="utf-8")
            self.assertIn("555", content)
            self.assertNotIn("  999:", content)  # no key for the refused depot

    def test_root_appkey_is_published_under_the_appid(self):
        """The root AppKey must be written under the AppID in DecryptionKeys."""
        from utils.yaml.core import batch_config_edit

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._config_with_app(tmp)
            with batch_config_edit(cfg) as editor:
                editor.add_app("111", comment="Game")
                editor.add_depot("555", comment="Game - Content (111)", app_id="111")
                editor.add_key("111", "c" * 64, comment="Game [AppKey] (111)")

            content = cfg.read_text(encoding="utf-8")
            self.assertIn("111: " + "c" * 64, content)
            self.assertIn("[AppKey] (111)", content)


class TestMigrationOrdering(unittest.TestCase):
    """The migration must not leave a game without an identity.

    The ASSella marker is the physical proof that a game is ASSella-managed.
    It may only be retired once the AT0-M identity is committed, and the
    branch the user pinned must survive the migration.
    """

    def _install_dir(self, tmpdir):
        install = Path(tmpdir) / "GameDir"
        install.mkdir(parents=True, exist_ok=True)
        (install / ".DepotDownloader").mkdir(exist_ok=True)
        return install

    def _game(self, install):
        return {
            "appid": "111",
            "game_name": "Ordered Game",
            "install_path": str(install),
        }

    def _run_batch(self, games, tmpdir):
        """Run the batch against a real config file and library, patched only at the edges."""
        import tempfile

        cfg = Path(tmpdir) / "config.yaml"
        cfg.write_text("AdditionalApps:\nAdditionalDepots:\nDecryptionKeys:\n", encoding="utf-8")

        lib_path = Path(tmpdir) / "plugin_library.json"
        lib_path.write_text("{}", encoding="utf-8")

        settings = MagicMock()
        settings.value.return_value = False
        with patch("utils.plugin_games.get_user_config_path", return_value=cfg), patch(
            "utils.plugin_games.get_plugin_library_path", return_value=lib_path
        ), patch("utils.plugin_games.collect_game_depots_and_keys", return_value=(
            ["555"], {"555": "a" * 64, "111": "b" * 64}, {"555": "Content"}
        )), patch("utils.steam_acf.steam_is_running", return_value=False), patch(
            "utils.sls_bridge.SLSBridge.notify_reload"
        ), patch("utils.settings.get_settings", return_value=settings):
            return batch_convert_games_to_atom(games)

    def test_markers_survive_a_failed_config_commit(self):
        """A config that cannot be written must leave the game ASSella-managed."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            install = self._install_dir(tmp)
            marker = install / ".DepotDownloader"

            cfg = Path(tmp) / "config.yaml"
            cfg.write_text("AdditionalApps:\n", encoding="utf-8")
            lib_path = Path(tmp) / "plugin_library.json"
            lib_path.write_text("{}", encoding="utf-8")

            class _FailingEditor:
                """A config editor whose write always fails."""

                def __init__(self, *a, **kw):
                    self.committed_successfully = False
                    self.content = ""

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def add_app(self, *a, **kw):
                    return False

                def add_depot(self, *a, **kw):
                    return False

                def contains_depot(self, *a, **kw):
                    return False

                def add_key(self, *a, **kw):
                    return False

            settings = MagicMock()
            settings.value.return_value = False
            with patch("utils.plugin_games.get_user_config_path", return_value=cfg), patch(
                "utils.plugin_games.get_plugin_library_path", return_value=lib_path
            ), patch("utils.plugin_games.collect_game_depots_and_keys", return_value=(
                ["555"], {"555": "a" * 64}, {"555": "Content"}
            )), patch("utils.plugin_games.batch_config_edit", return_value=_FailingEditor()), patch(
                "utils.steam_acf.steam_is_running", return_value=False
            ), patch("utils.sls_bridge.SLSBridge.notify_reload"), patch(
                "utils.settings.get_settings", return_value=MagicMock()
            ):
                results = batch_convert_games_to_atom([self._game(install)])

            self.assertEqual(len(results["migrated"]), 0)
            self.assertEqual(len(results["failed"]), 1)
            self.assertIn("nothing was changed", results["failed"][0]["reason"])
            # The marker is still there: the game keeps its ASSella identity.
            self.assertTrue(marker.exists())

    def test_markers_are_retired_after_a_successful_migration(self):
        """The happy path removes the marker and registers the game."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            install = self._install_dir(tmp)
            marker = install / ".DepotDownloader"

            results = self._run_batch([self._game(install)], tmp)

            self.assertEqual(len(results["failed"]), 0)
            self.assertEqual(len(results["migrated"]), 1)
            self.assertFalse(marker.exists())

            # The AT0-M identity is committed before the marker goes away.
            content = (Path(tmp) / "config.yaml").read_text(encoding="utf-8")
            self.assertIn("- 111", content)  # AdditionalApps
            self.assertIn("- 555", content)  # AdditionalDepots
            self.assertIn("111: " + "b" * 64, content)  # the root AppKey
            self.assertIn("555: " + "a" * 64, content)  # the depot key

    def test_markers_survive_a_failed_final_library_save(self):
        """A pending ACF flag that cannot be persisted must keep the marker.

        The game is already in SLSsteam at that point, but without the library
        record the migration cannot be re-run, so the ASSella identity is left
        intact and the failure is reported instead of silently dropping it.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            install = self._install_dir(tmp)
            marker = install / ".DepotDownloader"
            acf = Path(tmp) / "appmanifest_111.acf"
            acf.write_text('"AppState"\n\t{\n\t}\n', encoding="utf-8")

            game = self._game(install)
            game["appmanifest_path"] = str(acf)

            cfg = Path(tmp) / "config.yaml"
            cfg.write_text("AdditionalApps:\n", encoding="utf-8")
            lib_path = Path(tmp) / "plugin_library.json"
            lib_path.write_text("{}", encoding="utf-8")
            settings = MagicMock()
            settings.value.return_value = False

            with patch("utils.plugin_games.get_user_config_path", return_value=cfg), patch(
                "utils.plugin_games.get_plugin_library_path", return_value=lib_path
            ), patch("utils.plugin_games.collect_game_depots_and_keys", return_value=(
                ["555"], {"555": "a" * 64}, {"555": "Content"}
            )), patch(
                "utils.plugin_games.save_plugin_library", side_effect=[True, False]
            ), patch("utils.steam_acf.steam_is_running", return_value=True), patch(
                "utils.sls_bridge.SLSBridge.notify_reload"
            ), patch("utils.settings.get_settings", return_value=settings):
                results = batch_convert_games_to_atom([game])

            self.assertEqual(len(results["migrated"]), 1)
            self.assertTrue(marker.exists())
            self.assertEqual(len(results["markers_retained"]), 1)
            self.assertEqual(results["markers_retained"][0]["appid"], "111")

    def test_acf_and_counts_use_only_config_mounted_depots_and_keys(self):
        """A stale library overclaim must not reach ACF or migration reporting."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            install = self._install_dir(tmp)
            cfg = Path(tmp) / "config.yaml"
            cfg.write_text(
                "AdditionalApps:\n- 111\nAdditionalDepots:\n- 555\n"
                "DecryptionKeys:\n  111: " + "b" * 64 + "\n  555: " + "a" * 64 + "\n",
                encoding="utf-8",
            )
            acf = Path(tmp) / "appmanifest_111.acf"
            acf.write_text('"AppState"\n{\n}\n', encoding="utf-8")
            game = self._game(install)
            game["appmanifest_path"] = str(acf)
            settings = MagicMock()
            settings.value.return_value = False
            record = {
                "depots": ["555", "999"],
                "keys": {"111": "b" * 64, "555": "a" * 64, "999": "c" * 64},
                "dlc_appids": [],
            }

            with patch("utils.plugin_games.get_user_config_path", return_value=cfg), patch(
                "utils.plugin_games.collect_game_depots_and_keys",
                return_value=(["555", "999"], record["keys"], {}),
            ), patch("utils.plugin_games.register_plugin_game", return_value=True), patch(
                "utils.plugin_games.load_plugin_library", return_value={"111": record}
            ), patch("utils.steam_acf.steam_is_running", return_value=False), patch(
                "utils.steam_acf.ensure_acf_has_installed_depots", return_value=ACF_WRITTEN
            ) as ensure_acf, patch(
                "utils.steam_acf.find_appmanifest_path", return_value=acf
            ), patch("utils.plugin_games._remove_accela_markers"), patch(
                "core.native_steam.native_steam_handoff.sync_manifests_to_depotcache"
            ):
                result = migrate_game_to_atom(game, settings=settings)

            self.assertTrue(result["success"])
            self.assertEqual(result["depots_count"], 1)
            self.assertEqual(result["keys_count"], 2)
            self.assertEqual(ensure_acf.call_args.args[2], ["555"])

    def test_explicit_depot_selection_wins_over_saved_selection(self):
        """A current dialog selection must not be replaced by stale QSettings."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            install = self._install_dir(tmp)
            game = self._game(install)
            game["selected_depots"] = ["555"]
            with patch("utils.dlc_helpers.get_saved_depot_selection", return_value=["999"]), patch(
                "utils.dlc_helpers.resolve_depot_scope", return_value=(["555"], ["999"])
            ) as resolve_scope:
                results = self._run_batch([game], tmp)

            self.assertEqual(len(results["migrated"]), 1)
            resolve_scope.assert_called_once_with("111", ["555"], extra_scope=["555"])

    def test_goldberg_restore_failure_preserves_original_binary(self):
        """A failed restore must not first delete the installed game binary."""
        import tempfile
        from utils.plugin_games import revert_goldberg_if_applied

        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            original = install / "steam_api64.dll"
            backup = install / "steam_api64.dll.valve"
            original.write_bytes(b"emulator")
            backup.write_bytes(b"original")
            with patch(
                "ui.dialogs.library.actions.LibraryActionsMixin._is_goldberg_applied",
                return_value=True,
            ), patch("utils.plugin_games.os.replace", side_effect=OSError("locked")):
                self.assertFalse(revert_goldberg_if_applied(str(install)))
            self.assertEqual(original.read_bytes(), b"emulator")
            self.assertEqual(backup.read_bytes(), b"original")


if __name__ == "__main__":
    print("=== Running Batch AT0-M Migration Test Suite ===")
    unittest.main()
