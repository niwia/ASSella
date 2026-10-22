#!/usr/bin/env python3
"""Behavioural checks for the Steam self-update block (steam.cfg).

Steam reads steam.cfg from its install directory once, at startup. A Headcrab
style installer writes it so Steam does not update the client underneath a
patched launcher, and the Health tab toggle removes it again. These checks pin
the rules that matter:

  * the file is found in any of the locations Steam may read,
  * the two block keys are what counts as "blocked", and nothing else,
  * blocking keeps the settings the user put in the file,
  * unblocking removes only our keys, and deletes the file only when nothing
    else is left in it.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_steam_updates.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.gettempdir())

from utils import steam_updates  # noqa: E402


class SteamCfgTestCase(unittest.TestCase):
    """Every test runs against a temporary HOME."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        from core.steam_helpers import invalidate_steam_env_cache
        invalidate_steam_env_cache()
        self.addCleanup(invalidate_steam_env_cache)
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def write_cfg(self, relative: str, text: str) -> Path:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def native_cfg(self) -> Path:
        return self.home / ".steam" / "steam" / "steam.cfg"


class TestDetection(SteamCfgTestCase):
    def test_no_file_means_updates_are_allowed(self):
        self.assertIsNone(steam_updates.find_steam_cfg())
        self.assertFalse(steam_updates.are_steam_updates_blocked())

    def test_both_block_keys_block_updates(self):
        self.write_cfg(
            ".steam/steam/steam.cfg",
            "BootStrapperInhibitAll=enable\nBootStrapperForceSelfUpdate=disable\n",
        )
        self.assertTrue(steam_updates.are_steam_updates_blocked())

    def test_either_block_key_alone_blocks_updates(self):
        self.write_cfg(".steam/steam/steam.cfg", "BootStrapperInhibitAll=enable\n")
        self.assertTrue(steam_updates.are_steam_updates_blocked())

        self.write_cfg(".steam/steam/steam.cfg", "BootStrapperForceSelfUpdate=disable\n")
        self.assertTrue(steam_updates.are_steam_updates_blocked())

    def test_other_settings_do_not_block_updates(self):
        self.write_cfg(
            ".steam/steam/steam.cfg",
            "DownloadThrottlingEnabled=1\nBootStrapperInhibitAll=disable\n",
        )
        self.assertFalse(steam_updates.are_steam_updates_blocked())

    def test_comments_are_ignored(self):
        self.write_cfg(
            ".steam/steam/steam.cfg",
            "# BootStrapperInhibitAll=enable\n"
            "BootStrapperInhibitAll=enable # blocked on purpose\n",
        )
        self.assertTrue(steam_updates.are_steam_updates_blocked())

    def test_every_known_location_is_checked(self):
        for relative in (
            ".steam/steam/steam.cfg",
            ".steam/root/steam.cfg",
            ".local/share/Steam/steam.cfg",
            ".steam/steam.cfg",
            ".var/app/com.valvesoftware.Steam/.steam/steam/steam.cfg",
        ):
            with self.subTest(location=relative):
                self.write_cfg(relative, "BootStrapperInhibitAll=enable\n")
                self.assertTrue(steam_updates.are_steam_updates_blocked())

    def test_find_steam_cfg_prefers_the_first_candidate(self):
        expected = self.write_cfg(".steam/steam/steam.cfg", "x=1\n")
        self.write_cfg(".steam/root/steam.cfg", "x=1\n")
        self.assertEqual(steam_updates.find_steam_cfg(), expected)


class TestBlocking(SteamCfgTestCase):
    def test_blocking_writes_both_keys(self):
        ok, message = steam_updates.set_steam_updates_blocked(True)
        self.assertTrue(ok, message)

        cfg = self.native_cfg()
        self.assertTrue(cfg.is_file())
        text = cfg.read_text()
        self.assertIn("BootStrapperInhibitAll=enable", text)
        self.assertIn("BootStrapperForceSelfUpdate=disable", text)
        self.assertTrue(steam_updates.are_steam_updates_blocked())

    def test_blocking_twice_does_not_duplicate_the_keys(self):
        steam_updates.set_steam_updates_blocked(True)
        steam_updates.set_steam_updates_blocked(True)

        lines = self.native_cfg().read_text().splitlines()
        self.assertEqual(lines.count("BootStrapperInhibitAll=enable"), 1)
        self.assertEqual(lines.count("BootStrapperForceSelfUpdate=disable"), 1)

    def test_blocking_keeps_the_settings_the_user_added(self):
        self.write_cfg(".steam/steam/steam.cfg", "DownloadThrottlingEnabled=1\n")
        steam_updates.set_steam_updates_blocked(True)

        text = self.native_cfg().read_text()
        self.assertIn("DownloadThrottlingEnabled=1", text)
        self.assertIn("BootStrapperInhibitAll=enable", text)

    def test_unblocking_removes_the_file_when_nothing_else_is_left(self):
        steam_updates.set_steam_updates_blocked(True)
        ok, message = steam_updates.set_steam_updates_blocked(False)

        self.assertTrue(ok, message)
        self.assertFalse(self.native_cfg().exists())
        self.assertFalse(steam_updates.are_steam_updates_blocked())

    def test_unblocking_keeps_the_other_settings(self):
        self.write_cfg(
            ".steam/steam/steam.cfg",
            "DownloadThrottlingEnabled=1\nBootStrapperInhibitAll=enable\n",
        )
        ok, message = steam_updates.set_steam_updates_blocked(False)

        self.assertTrue(ok, message)
        cfg = self.native_cfg()
        self.assertTrue(cfg.is_file())
        text = cfg.read_text()
        self.assertIn("DownloadThrottlingEnabled=1", text)
        self.assertNotIn("BootStrapperInhibitAll", text)
        self.assertFalse(steam_updates.are_steam_updates_blocked())

    def test_unblocking_without_a_file_changes_nothing(self):
        ok, message = steam_updates.set_steam_updates_blocked(False)

        self.assertTrue(ok, message)
        self.assertIn("already enabled", message)
        self.assertFalse(self.native_cfg().exists())

    def test_unblocking_leaves_a_file_without_our_keys_alone(self):
        cfg = self.write_cfg(".steam/steam/steam.cfg", "DownloadThrottlingEnabled=1\n")
        ok, message = steam_updates.set_steam_updates_blocked(False)

        self.assertTrue(ok, message)
        self.assertTrue(cfg.is_file())
        self.assertIn("DownloadThrottlingEnabled=1", cfg.read_text())

    def test_target_follows_an_existing_file(self):
        existing = self.write_cfg(".steam/root/steam.cfg", "x=1\n")
        self.assertEqual(steam_updates.target_steam_cfg(), existing)

    def test_target_follows_the_flatpak_steam_directory(self):
        flatpak_steam = self.home / ".var" / "app" / "com.valvesoftware.Steam" / ".steam" / "steam"
        flatpak_steam.mkdir(parents=True)
        self.assertEqual(steam_updates.target_steam_cfg(), flatpak_steam / "steam.cfg")

    def test_target_defaults_to_the_native_steam_directory(self):
        self.assertEqual(steam_updates.target_steam_cfg(), self.native_cfg())


class TestHealthTabControl(unittest.TestCase):
    """The Health tab button follows the file, not a stored preference."""

    def setUp(self) -> None:
        from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

        self.app = QApplication.instance() or QApplication([])
        self.addCleanup(self.app.quit)

        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

        from ui.dialogs.settings_tabs import health_tab

        self.health_tab = health_tab
        self.dialog = mock.MagicMock()
        self.dialog.health_steam_updates_btn = QPushButton()
        self.dialog.health_steam_updates_lbl = QLabel()
        self.dialog.main_window = None

    def test_button_follows_the_file_state(self):
        health_tab = self.health_tab

        health_tab.refresh_steam_updates_control(self.dialog)
        self.assertEqual(self.dialog.health_steam_updates_btn.text(), "Disable Steam Updates")

        cfg = self.home / ".steam" / "steam" / "steam.cfg"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("BootStrapperInhibitAll=enable\n")
        health_tab.refresh_steam_updates_control(self.dialog)
        self.assertEqual(self.dialog.health_steam_updates_btn.text(), "Enable Steam Updates")
        self.assertIn(str(cfg), self.dialog.health_steam_updates_lbl.text())

    def test_enabling_steam_updates_shows_warning_and_cancels_on_no(self):
        from ui.dialogs.themed_message_box import ThemedMessageBox

        cfg = self.home / ".steam" / "steam" / "steam.cfg"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("BootStrapperInhibitAll=enable\n")

        with mock.patch.object(ThemedMessageBox, "question", return_value=ThemedMessageBox.StandardButton.No) as mock_q:
            self.health_tab.on_steam_updates_clicked(self.dialog)
            self.assertTrue(mock_q.called)
            # Must remain blocked
            self.assertTrue(steam_updates.are_steam_updates_blocked())

    def test_enabling_steam_updates_unblocks_on_yes(self):
        from ui.dialogs.themed_message_box import ThemedMessageBox

        cfg = self.home / ".steam" / "steam" / "steam.cfg"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("BootStrapperInhibitAll=enable\n")

        with mock.patch.object(ThemedMessageBox, "question", return_value=ThemedMessageBox.StandardButton.Yes) as mock_q:
            with mock.patch.object(ThemedMessageBox, "information") as mock_info:
                self.health_tab.on_steam_updates_clicked(self.dialog)
                self.assertTrue(mock_q.called)
                self.assertTrue(mock_info.called)
                self.assertFalse(steam_updates.are_steam_updates_blocked())


if __name__ == "__main__":
    unittest.main()
