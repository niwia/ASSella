#!/usr/bin/env python3
"""Regression checks for Steamless counting accuracy, summary UI, and deferred overlay."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QEventLoop, QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QStackedWidget, QVBoxLayout, QWidget

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.tasks.steamless_task import SteamlessIntegration, SteamlessTask
from managers.emulation.steamless import SteamlessMixin
from ui.dialogs.dialog_raiser import (
    DialogRaiser,
    find_topmost_child_dialog,
    resolve_dialog_parent,
)
from ui.dialogs.game_details.tools_tab import init_tools_tab, refresh_drm_emulation_state
from ui.dialogs.steamless_resume import SteamlessResumeDialog
from ui.dialogs.themed_message_box import ThemedMessageBox


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    return app


def _make_dummy_exe(directory: str, rel_path: str, size: int = 40 * 1024) -> str:
    full_path = os.path.join(directory, rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as f:
        f.write(b"MZ" + b"\x00" * (size - 2))
    return full_path


class _FakeProcess:
    def __init__(self, lines, returncode, on_wait=None):
        self.pid = 4242
        self._lines = list(lines)
        self._idx = 0
        self.returncode = returncode
        self._on_wait = on_wait
        self.stdout = self

    def readline(self):
        if self._idx >= len(self._lines):
            return ""
        line = self._lines[self._idx]
        self._idx += 1
        return line

    def poll(self):
        return self.returncode

    def close(self):
        return None

    def wait(self, timeout=None):
        if self._on_wait:
            self._on_wait()
        return self.returncode

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class _FakeTerminal:
    def __init__(self):
        self.stages = []

    def set_stage_status(self, stage, status):
        self.stages.append((stage, status))


class _FakeMainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.simplified_terminal = _FakeTerminal()


class _HarnessManager(SteamlessMixin, QObject):
    steamless_manual_started = pyqtSignal(str)
    steamless_manual_finished = pyqtSignal()

    def __init__(self, parent_widget=None):
        super().__init__()
        self.main_window = parent_widget or _FakeMainWindow()
        self.settings = None
        self.steamless_task = None
        self._steamless_success = None
        self._steamless_manual_run = False
        self._steamless_progress_log = []
        self._steamless_game_name = ""
        self._last_steamless_success = None
        self._last_steamless_summary = None
        self._steamless_ran = True
        self._steamless_error = False
        self._steamless_finalized = False
        self._current_active_step = None
        self.finalize_calls = 0

    def _finalize_job_logic(self):
        self.finalize_calls += 1

    def _update_status_button_color(self):
        return None


def _collect_all_labels(widget) -> list[str]:
    return [lbl.text() for lbl in widget.findChildren(QLabel)]


def _wait_ms(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class TestSteamlessCountingAndSummary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _ensure_qapp()

    def test_clean_game_never_reports_processed_1_or_completed_successfully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_dummy_exe(tmpdir, "MLP.exe")
            _make_dummy_exe(tmpdir, "SecondaryLauncher.exe")

            integration = SteamlessIntegration()
            integration.use_aio = True

            def fake_popen(cmd, **kwargs):
                return _FakeProcess(
                    ["[!] No Steam DRM detected in executable.\n"],
                    returncode=1,
                )

            with (
                patch("core.tasks.steamless_task.subprocess.Popen", side_effect=fake_popen),
                patch.object(integration, "_ensure_aio_deps", return_value=None),
            ):
                ok = integration.process_game_with_steamless(tmpdir)

            self.assertTrue(ok)
            summary = integration.last_summary
            self.assertIsNotNone(summary)
            self.assertEqual(summary["scanned_count"], 2)
            self.assertEqual(summary["fixed_count"], 0)
            self.assertEqual(summary["clean_count"], 2)
            self.assertEqual(summary["failed_count"], 0)
            self.assertEqual(summary["overall_status"], "clean")

            # Feed through SteamlessMixin._show_steamless_resume_dialog and inspect rendered UI
            mgr = _HarnessManager()
            mgr._steamless_game_name = "MY LITTLE PONY: A Maretime Bay Adventure"
            mgr._steamless_appid = "1600780"
            mgr._last_steamless_success = True
            mgr._last_steamless_summary = summary

            shown_dialogs = []

            def fake_exec(dlg):
                shown_dialogs.append(dlg)
                dlg.show()
                return QDialog.DialogCode.Accepted

            with patch.object(SteamlessResumeDialog, "exec", fake_exec):
                mgr._show_steamless_resume_dialog()

            self.assertEqual(len(shown_dialogs), 1)
            dlg = shown_dialogs[0]
            all_labels = _collect_all_labels(dlg)
            self.assertEqual(dlg.game_name_label.text(), "MY LITTLE PONY: A Maretime Bay Adventure")
            self.assertEqual(dlg.appid_label.text(), "AppID: 1600780")
            self.assertNotIn("Steamless Processing Complete", all_labels)
            self.assertNotIn("Steamless AIO (Python)", all_labels)
            self.assertEqual(dlg.stat_values["scanned"].text(), "2")
            self.assertEqual(dlg.stat_values["fixed"].text(), "0")
            self.assertEqual(dlg.stat_values["clean"].text(), "2")
            self.assertNotIn("failed", dlg.stat_values)
            self.assertEqual(dlg.status_headline_label.text(), "No SteamStub DRM Found")
            self.assertNotIn("Completed Successfully", " ".join(all_labels))

            # Clean toggle present, collapsed by default, reveals clean entries when toggled
            self.assertIsNotNone(dlg.clean_toggle_btn)
            self.assertIn("2", dlg.clean_toggle_btn.text())
            self.assertFalse(dlg.clean_container.isVisible())
            dlg.clean_toggle_btn.setChecked(True)
            self.assertTrue(dlg.clean_container.isVisible())
            clean_labels = " ".join(_collect_all_labels(dlg.clean_container))
            self.assertIn("MLP.exe", clean_labels)
            self.assertIn("SecondaryLauncher.exe", clean_labels)
            dlg.close()

    def test_unpacked_executables_are_not_double_counted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exe1 = _make_dummy_exe(tmpdir, "GameA.exe")
            exe2 = _make_dummy_exe(tmpdir, os.path.join("Binaries", "GameB.exe"))

            integration = SteamlessIntegration()
            integration.use_aio = True

            def fake_popen(cmd, **kwargs):
                target = cmd[-1]

                def create_backup():
                    with open(target + ".original", "wb") as bf:
                        bf.write(b"orig")

                return _FakeProcess(
                    [f"[+] Unpacked with Variant 3.1 x64 -> {target}.unpacked\n"],
                    returncode=0,
                    on_wait=create_backup,
                )

            with (
                patch("core.tasks.steamless_task.subprocess.Popen", side_effect=fake_popen),
                patch.object(integration, "_ensure_aio_deps", return_value=None),
            ):
                ok = integration.process_game_with_steamless(tmpdir)

            self.assertTrue(ok)
            summary = integration.last_summary
            self.assertEqual(summary["scanned_count"], 2)
            self.assertEqual(summary["fixed_count"], 2)
            self.assertEqual(summary["clean_count"], 0)
            self.assertEqual(summary["failed_count"], 0)
            self.assertEqual(summary["overall_status"], "fixed")

            for row in summary["executables"]:
                self.assertEqual(row["status"], "fixed")
                self.assertEqual(row["variant"], "Variant 3.1 x64")
                self.assertTrue(row["backup_path"].endswith(".bak"))
                self.assertTrue(os.path.exists(row["backup_path"]))

            self.assertTrue(os.path.exists(exe1 + ".bak"))
            self.assertTrue(os.path.exists(exe2 + ".bak"))

    def test_legacy_log_parser_never_double_counts_or_forces_minimum_one(self):
        mgr = _HarnessManager()
        mgr._steamless_game_name = "TestGame"
        mgr._last_steamless_success = True
        mgr._steamless_progress_log = [
            "Found 2 executable(s) to evaluate",
            "[+] Unpacked with Variant 3.1 x64 -> A.exe",
            "Successfully processed: A.exe",
            "[+] Unpacked with Variant 3.1 x64 -> B.exe",
            "Successfully processed: B.exe",
        ]
        summary = mgr._parse_summary_from_logs()
        self.assertEqual(summary["scanned_count"], 2)
        self.assertEqual(summary["fixed_count"], 2)
        self.assertEqual(summary["clean_count"], 0)

        mgr._steamless_progress_log = [
            "Found 2 executable(s) to evaluate",
            "[!] No Steam DRM detected in executable.",
            "No Steam DRM detected in: MLP.exe (code 1)",
            "[!] No Steam DRM detected in executable.",
            "No Steam DRM detected in: Other.exe (code 1)",
        ]
        clean_summary = mgr._parse_summary_from_logs()
        self.assertEqual(clean_summary["scanned_count"], 2)
        self.assertEqual(clean_summary["fixed_count"], 0)
        self.assertEqual(clean_summary["clean_count"], 2)
        self.assertEqual(clean_summary["overall_status"], "clean")

    def test_mixed_fixed_failed_clean_summary_and_status_strings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_dummy_exe(tmpdir, "MainGame.exe")
            _make_dummy_exe(tmpdir, "ProtectedHelper.exe")
            _make_dummy_exe(tmpdir, "CleanTool1.exe")
            _make_dummy_exe(tmpdir, "CleanTool2.exe")

            integration = SteamlessIntegration()
            integration.use_aio = True

            def fake_popen(cmd, **kwargs):
                target = cmd[-1]
                base = os.path.basename(target)
                if base == "MainGame.exe":
                    return _FakeProcess(
                        [f"[+] Unpacked with Variant 3.1 x64 -> {target}.unpacked\n"],
                        returncode=0,
                        on_wait=lambda: open(target + ".original", "wb").close(),
                    )
                if base == "ProtectedHelper.exe":
                    return _FakeProcess(
                        ["[!] SteamStub DRM detected, but unpacking failed.\n"],
                        returncode=2,
                    )
                return _FakeProcess(
                    ["[!] No Steam DRM detected in executable.\n"],
                    returncode=1,
                )

            with (
                patch("core.tasks.steamless_task.subprocess.Popen", side_effect=fake_popen),
                patch.object(integration, "_ensure_aio_deps", return_value=None),
            ):
                ok = integration.process_game_with_steamless(tmpdir)

            self.assertFalse(ok)
            summary = integration.last_summary
            self.assertEqual(summary["scanned_count"], 4)
            self.assertEqual(summary["fixed_count"], 1)
            self.assertEqual(summary["failed_count"], 1)
            self.assertEqual(summary["clean_count"], 2)
            self.assertEqual(summary["overall_status"], "partial_failure")

            mgr = _HarnessManager()
            mgr._last_steamless_summary = summary
            self.assertEqual(mgr._get_steamless_status_text(), "Partial (1 fixed, 1 failed)")
            self.assertEqual(mgr.parse_steamless_result(), "Partial (1 fixed, 1 failed)")

            dlg = SteamlessResumeDialog(
                "Mixed Game",
                summary=summary,
                logs=["line 1", "line 2"],
                appid="2491330",
            )
            dlg.show()
            self.assertEqual(dlg.appid_label.text(), "AppID: 2491330")
            italic_details = [
                lbl.text()
                for lbl in dlg.findChildren(QLabel)
                if "font-style: italic" in lbl.styleSheet()
            ]
            self.assertTrue(any("Variant 3.1 x64" in t for t in italic_details))
            self.assertTrue(any("unpacking failed (code 2)" in t for t in italic_details))
            self.assertEqual(dlg.stat_values["scanned"].text(), "4")
            self.assertEqual(dlg.stat_values["fixed"].text(), "1")
            self.assertEqual(dlg.stat_values["clean"].text(), "2")
            self.assertEqual(dlg.stat_values["failed"].text(), "1")
            self.assertEqual(dlg.status_headline_label.text(), "Completed with Errors")

            # Raw log and clean exes collapsed by default
            self.assertFalse(dlg.log_view.isVisible())
            self.assertFalse(dlg.clean_container.isVisible())
            dlg.log_toggle_btn.setChecked(True)
            self.assertTrue(dlg.log_view.isVisible())
            dlg.close()

    def test_manual_error_does_not_call_finalize_and_signal_precedes_modal_dialog(self):
        mgr = _HarnessManager()
        events = []

        mgr.steamless_manual_finished.connect(lambda: events.append("signal_finished"))
        mgr._steamless_manual_run = True
        mgr._steamless_game_name = "BrokenGame"
        mgr._handle_steamless_task_error((RuntimeError, "Game directory not found", ""))

        # Manual run must never trigger auto post-install _finalize_job_logic
        self.assertEqual(mgr.finalize_calls, 0)
        self.assertTrue(mgr._steamless_error)

        with patch.object(
            SteamlessResumeDialog,
            "exec",
            lambda dlg: events.append("dialog_exec") or 1,
        ):
            mgr._on_steamless_finished()

        self.assertEqual(events, ["signal_finished", "dialog_exec"])

        # Subsequent run must cleanly reset error state
        with patch.object(SteamlessTask, "start", lambda self: None):
            mgr.run_steamless_aio_for_game("/tmp", "FreshGame")
        self.assertFalse(mgr._steamless_error)
        self.assertIsNone(mgr._last_steamless_summary)

    def test_steamless_task_thread_single_exe_and_directory_summaries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target_exe = _make_dummy_exe(tmpdir, "SingleClean.exe")
            mgr = _HarnessManager()
            events = []

            loop = QEventLoop()
            mgr.steamless_manual_finished.connect(lambda: events.append("manual_finished"))

            def fake_exec(dlg):
                events.append(("dialog_summary", dict(dlg._summary)))
                loop.quit()
                return QDialog.DialogCode.Accepted

            with (
                patch("core.tasks.steamless_task.get_dotnet_path", return_value=None),
                patch(
                    "core.tasks.steamless_task.subprocess.Popen",
                    return_value=_FakeProcess(
                        ["[!] No Steam DRM detected in executable.\n"],
                        returncode=1,
                    ),
                ),
                patch.object(SteamlessIntegration, "_ensure_aio_deps", return_value=None),
                patch.object(SteamlessResumeDialog, "exec", fake_exec),
            ):
                mgr.run_steamless_aio_manually(target_exe, "SingleCleanGame")
                QTimer.singleShot(3000, loop.quit)
                loop.exec()

            self.assertEqual(events[0], "manual_finished")
            self.assertEqual(events[1][0], "dialog_summary")
            summary = events[1][1]
            self.assertEqual(summary["scanned_count"], 1)
            self.assertEqual(summary["fixed_count"], 0)
            self.assertEqual(summary["clean_count"], 1)
            self.assertEqual(summary["failed_count"], 0)
            self.assertEqual(summary["overall_status"], "clean")
            self.assertEqual(summary["engine"], "Steamless AIO (Python)")


class _StubDialog(QDialog):
    def __init__(self, tm, game_dir):
        super().__init__()
        self.resize(640, 480)
        self.appid = "99999"
        self.accent_color = "#a1c9fd"
        self.game_data = {"install_path": game_dir, "game_name": "Test Game"}
        self.parent_window = SimpleNamespace(
            main_window=SimpleNamespace(task_manager=tm),
            _last_goldberg_applied=False,
        )
        self.tab_widget = SimpleNamespace(addTab=lambda widget, label: None)
        self.stacked = QStackedWidget(self)
        lay = QVBoxLayout(self)
        lay.addWidget(self.stacked)
        self.steamdb_scraper = SimpleNamespace(session=None)
        self._steamless_loading_overlay = None
        self._steamless_overlay_timer = None

    def _configure_depots_wrapper(self):
        return None

    def _reset_depots_wrapper(self):
        return None

    def _on_goldberg_check_complete(self, _path, _applied):
        return None


class TestToolsTabDeferredOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _ensure_qapp()

    def test_fast_run_skips_overlay_and_restores_buttons(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = _HarnessManager()
            runs = []
            tm.run_steamless_aio_for_game = lambda path, name, appid=None: runs.append((path, name, appid))

            dlg = _StubDialog(tm, tmpdir)
            with (
                patch("ui.dialogs.game_details.tools_tab.is_dlc_only_mode", return_value=False),
                patch("ui.dialogs.game_details.tools_tab.is_component_available", return_value=True),
                patch("ui.dialogs.game_details.tools_tab._wire_desktop_button", lambda d: None),
                patch("ui.dialogs.game_details.tools_tab.update_depot_label", lambda d: None),
            ):
                init_tools_tab(dlg)

            dlg.b_steamless_aio.click()
            self.assertEqual(runs, [(tmpdir, "Test Game", "99999")])
            self.assertFalse(dlg.b_steamless_aio.isEnabled())
            self.assertFalse(dlg.b_steamless_cli.isEnabled())
            self.assertEqual(dlg.b_steamless_aio.text(), "Scanning...")

            # Async Goldberg refresh mid-run must not re-enable buttons prematurely
            refresh_drm_emulation_state(dlg)
            self.assertFalse(dlg.b_steamless_aio.isEnabled())

            # Fast completion before 400ms threshold
            _wait_ms(40)
            tm.steamless_manual_finished.emit()
            _wait_ms(430)

            self.assertIsNone(dlg._steamless_loading_overlay)
            self.assertTrue(dlg.b_steamless_aio.isEnabled())
            self.assertTrue(dlg.b_steamless_cli.isEnabled())
            self.assertEqual(dlg.b_steamless_aio.text(), "Steamless (Python)")
            dlg.close()

    def test_slow_run_shows_overlay_after_delay_and_dismisses_on_finish(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tm = _HarnessManager()
            tm.run_steamless_aio_for_game = lambda path, name, appid=None: None

            dlg = _StubDialog(tm, tmpdir)
            with (
                patch("ui.dialogs.game_details.tools_tab.is_dlc_only_mode", return_value=False),
                patch("ui.dialogs.game_details.tools_tab.is_component_available", return_value=True),
                patch("ui.dialogs.game_details.tools_tab._wire_desktop_button", lambda d: None),
                patch("ui.dialogs.game_details.tools_tab.update_depot_label", lambda d: None),
            ):
                init_tools_tab(dlg)

            dlg.show()
            dlg.b_steamless_aio.click()

            _wait_ms(460)
            overlay = dlg._steamless_loading_overlay
            self.assertIsNotNone(overlay)
            self.assertTrue(overlay.isVisible())

            tm.steamless_manual_finished.emit()
            _wait_ms(30)

            self.assertIsNone(dlg._steamless_loading_overlay)
            self.assertTrue(sip.isdeleted(overlay) or not overlay.isVisible())
            self.assertTrue(dlg.b_steamless_aio.isEnabled())
            self.assertEqual(dlg.b_steamless_aio.text(), "Steamless (Python)")
            dlg.close()


class TestDialogParentAndRaiser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _ensure_qapp()

    def test_resolve_parent_and_nested_steamless_parenting(self):
        main_win = _FakeMainWindow()
        main_win.show()
        lib_dlg = QDialog(main_win)
        lib_dlg.show()
        details_dlg = QDialog(lib_dlg)
        details_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        details_dlg.show()
        _wait_ms(20)

        # Even if activeModalWidget/activeWindow are None (user Alt-Tabbed away),
        # resolve_dialog_parent walks the visible hierarchy to details_dlg.
        with (
            patch.object(QApplication, "activeModalWidget", return_value=None),
            patch.object(QApplication, "activeWindow", return_value=None),
        ):
            resolved = resolve_dialog_parent(main_win)
            self.assertIs(resolved, details_dlg)

        mgr = _HarnessManager(parent_widget=main_win)
        mgr._steamless_game_name = "Umbraclaw"
        mgr._steamless_appid = "2491330"
        mgr._last_steamless_summary = {
            "scanned_count": 1,
            "fixed_count": 1,
            "clean_count": 0,
            "failed_count": 0,
            "overall_status": "fixed",
            "executables": [],
        }

        captured = []

        def fake_exec(dlg):
            captured.append(dlg)
            return QDialog.DialogCode.Accepted

        with (
            patch.object(QApplication, "activeModalWidget", return_value=details_dlg),
            patch.object(SteamlessResumeDialog, "exec", fake_exec),
        ):
            mgr._show_steamless_resume_dialog()

        self.assertEqual(len(captured), 1)
        resume_dlg = captured[0]
        self.assertIs(resume_dlg.parent(), details_dlg)
        self.assertEqual(resume_dlg.windowModality(), Qt.WindowModality.WindowModal)

        details_dlg.close()
        lib_dlg.close()
        main_win.close()

    def test_dialog_raiser_activates_leaf_modal_only_and_cleans_up(self):
        main_win = _FakeMainWindow()
        main_win.show()
        lib_dlg = QDialog(main_win)
        lib_dlg.show()
        details_dlg = QDialog(lib_dlg)
        details_dlg.show()
        resume_dlg = SteamlessResumeDialog(
            "Umbraclaw",
            exe_count=1,
            processed_count=1,
            success=True,
            parent=details_dlg,
            appid="2491330",
        )
        resume_dlg.show()

        DialogRaiser.attach(main_win, lib_dlg)
        DialogRaiser.attach(lib_dlg, details_dlg)
        # Attaching again must stay idempotent
        r1 = DialogRaiser.attach(details_dlg, resume_dlg)
        r2 = DialogRaiser.attach(details_dlg, resume_dlg)
        self.assertIs(r1, r2)
        self.assertIs(find_topmost_child_dialog(main_win), resume_dlg)

        # Let showEvent/startup WindowActivate settle before asserting Alt-Tab back.
        _wait_ms(60)

        activated = []
        raised = []

        for label, w in (
            ("main", main_win),
            ("lib", lib_dlg),
            ("details", details_dlg),
            ("resume", resume_dlg),
        ):
            orig_raise = w.raise_
            orig_act = w.activateWindow
            w.raise_ = lambda lbl=label, orig=orig_raise: (raised.append(lbl), orig())
            w.activateWindow = lambda lbl=label, orig=orig_act: (activated.append(lbl), orig())

        # Simulate Alt-Tab back activating main_win directly
        QApplication.sendEvent(main_win, QEvent(QEvent.Type.WindowActivate))
        _wait_ms(60)

        self.assertEqual(raised, ["lib", "details", "resume"])
        self.assertEqual(activated, ["resume"])

        # Simulate clicking/activating obscured intermediate details_dlg
        raised.clear()
        activated.clear()
        QApplication.sendEvent(details_dlg, QEvent(QEvent.Type.WindowActivate))
        _wait_ms(60)
        self.assertEqual(raised, ["resume"])
        self.assertEqual(activated, ["resume"])

        # Cleanup on destruction leaves zero stale raisers on details_dlg
        msg = ThemedMessageBox("Info", "Done", parent=details_dlg)
        self.assertGreaterEqual(
            len(details_dlg.findChildren(DialogRaiser, options=Qt.FindChildOption.FindDirectChildrenOnly)),
            2,
        )
        msg.deleteLater()
        resume_dlg.deleteLater()
        _wait_ms(30)
        remaining = [
            r
            for r in details_dlg.findChildren(
                DialogRaiser, options=Qt.FindChildOption.FindDirectChildrenOnly
            )
            if not sip.isdeleted(r) and not r._cleaned
        ]
        self.assertEqual(remaining, [])

        details_dlg.close()
        lib_dlg.close()
        main_win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
