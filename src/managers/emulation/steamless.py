"""Steamless emulator task orchestration.

Extracted from ``managers/task_manager.py``. Composed back into TaskManager via
``SteamlessMixin`` so the public API is unchanged.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QMetaObject, QTimer, Qt

from core.tasks.steamless_task import SteamlessIntegration, SteamlessTask
from utils.steam_manifest import get_game_directory

logger = logging.getLogger(__name__)


class SteamlessMixin:
    """Runs the Steamless emulator for a game and reports its result."""

    def _reset_steamless_state(self) -> None:
        """Clear transient per-run Steamless tracking fields before a new run."""
        self._steamless_error = False
        self._steamless_success = None
        self._last_steamless_success = None
        self._last_steamless_summary: Optional[Dict[str, Any]] = None
        self._steamless_progress_log = []
        self._steamless_appid = ""
        self._steamless_finalized = False

    def _emit_manual_signal(self, signal_name: str, *args: Any) -> None:
        """Emit optional TaskManager Steamless lifecycle signals if defined."""
        sig = getattr(self, signal_name, None)
        if sig is not None:
            try:
                sig.emit(*args)
            except (RuntimeError, TypeError) as e:
                logger.debug(f"Could not emit {signal_name}: {e}")

    def _create_steamless_task(self, progress_handler, *, use_aio: bool = True):
        self.steamless_task = SteamlessTask()
        self.steamless_task.use_aio = use_aio
        self.steamless_task.progress.connect(progress_handler)
        self.steamless_task.result.connect(self._on_steamless_complete)
        self.steamless_task.summary_ready.connect(self._on_steamless_summary)
        self.steamless_task.finished.connect(self._on_steamless_finished)
        self.steamless_task.error.connect(self._handle_steamless_task_error)
        return self.steamless_task

    def _reset_steamless_task(self):
        if self.steamless_task:
            if self.steamless_task.isRunning():
                self.steamless_task.stop()
                self.steamless_task.wait(2000)
            self.steamless_task = None

    def _start_steamless_processing(self, use_aio=True):
        if hasattr(self, "settings") and not self.settings.value("auto_run_steamless_post_install", True, type=bool):
            logger.info("Auto-run Steamless is disabled in settings. Skipping post-processing...")
            self._finalize_job_logic()
            return

        if not self.current_dest_path or not self.game_data:
            self._finalize_job_logic()
            return

        game_directory = get_game_directory(self.current_dest_path, self.game_data)

        if not os.path.exists(game_directory):
            self._finalize_job_logic()
            return

        self._reset_steamless_task()
        self._reset_steamless_state()
        self._steamless_manual_run = False

        logger.info("\n" + "=" * 40)
        logger.info("Starting Steamless DRM Removal...")

        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.set_stage_status("steamless", "in_progress")

        steamless_task = self._create_steamless_task(self._on_steamless_progress, use_aio=True)
        steamless_task.set_game_directory(game_directory)
        steamless_task.start()

        self._steamless_ran = True
        self._update_status_button_color()

    def _start_manual_steamless(
        self,
        *,
        game_name: str,
        use_aio: bool,
        appid: Optional[str] = None,
        game_directory: Optional[str] = None,
        exe_path: Optional[str] = None,
    ) -> None:
        """Start a manual Steamless run on a game directory or single executable."""
        self._reset_steamless_task()
        self._reset_steamless_state()

        self._steamless_game_name = game_name
        self._steamless_appid = str(appid).strip() if appid else ""
        self._steamless_manual_run = True

        engine = "Python AIO" if use_aio else ".NET CLI"
        target_desc = exe_path or game_directory or game_name
        logger.info(f"Starting manual Steamless ({engine}) processing for: {target_desc}")

        self._emit_manual_signal("steamless_manual_started", engine)

        steamless_task = self._create_steamless_task(
            self._on_steamless_progress,
            use_aio=use_aio,
        )
        if exe_path:
            steamless_task.set_target_exe(exe_path)
        elif game_directory:
            steamless_task.set_game_directory(game_directory)
        steamless_task.start()

    def run_steamless_manually(
        self, exe_path: str, game_name: Optional[str] = None, appid: Optional[str] = None
    ):
        self._start_manual_steamless(
            game_name=game_name or os.path.basename(exe_path),
            use_aio=False,
            appid=appid,
            exe_path=exe_path,
        )

    def run_steamless_aio_manually(
        self, exe_path: str, game_name: Optional[str] = None, appid: Optional[str] = None
    ):
        self._start_manual_steamless(
            game_name=game_name or os.path.basename(exe_path),
            use_aio=True,
            appid=appid,
            exe_path=exe_path,
        )

    def run_steamless_for_game(
        self, game_directory: str, game_name: str, appid: Optional[str] = None
    ):
        self._start_manual_steamless(
            game_name=game_name,
            use_aio=False,
            appid=appid,
            game_directory=game_directory,
        )

    def run_steamless_aio_for_game(
        self, game_directory: str, game_name: str, appid: Optional[str] = None
    ):
        self._start_manual_steamless(
            game_name=game_name,
            use_aio=True,
            appid=appid,
            game_directory=game_directory,
        )

    def _on_steamless_progress(self, message):
        self._steamless_progress_log.append(message)
        logger.info(message)

    def _on_steamless_summary(self, summary: Dict[str, Any]):
        self._last_steamless_summary = summary

    def _on_steamless_complete(self, success):
        logger.info("\n" + "=" * 40)
        summary = getattr(self, "_last_steamless_summary", None)
        if summary and summary.get("overall_status") == "clean":
            logger.info("Steamless completed: no SteamStub DRM detected")
        elif success:
            logger.info("Steamless processing completed successfully")
        else:
            logger.info("Steamless processing completed with errors or warnings")

        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.set_stage_status("steamless", "completed" if success else "error")

        self._steamless_success = success
        self._last_steamless_success = success

    def _schedule_post_steamless_finalize(self) -> None:
        """Queue job finalization at most once for an automated Steamless run."""
        if getattr(self, "_steamless_finalized", False):
            return
        self._steamless_finalized = True
        self._current_active_step = None
        QMetaObject.invokeMethod(
            self, "_finalize_job_logic", Qt.ConnectionType.QueuedConnection
        )

    def _on_steamless_finished(self):
        if self.steamless_task:
            QTimer.singleShot(0, self._clear_steamless_task)

        if self._steamless_manual_run:
            self._emit_manual_signal("steamless_manual_finished")
            self._show_steamless_resume_dialog()
            self._steamless_manual_run = False
            self._steamless_success = None
            return

        if self._steamless_success is not None:
            self._steamless_success = None

        self._schedule_post_steamless_finalize()

    def _clear_steamless_task(self):
        self.steamless_task = None

    def _parse_summary_from_logs(self) -> Dict[str, Any]:
        """Reconstruct a structured Steamless summary from log lines when needed."""
        scanned_count = 0
        has_explicit_processed = any(
            "Successfully processed:" in msg for msg in self._steamless_progress_log
        )
        has_explicit_no_drm = any(
            "No Steam DRM detected in:" in msg or msg == "No Steam DRM detected in executable"
            for msg in self._steamless_progress_log
        )
        processed_count = 0
        no_drm_count = 0
        failed_count = 0

        if self._steamless_game_name and self._steamless_game_name.lower().endswith(".exe"):
            scanned_count = 1

        for message in self._steamless_progress_log:
            if "Found " in message and "executable(s)" in message:
                try:
                    parts = message.split()
                    for i, part in enumerate(parts):
                        if part == "Found" and i + 1 < len(parts):
                            scanned_count = int(parts[i + 1])
                            break
                except ValueError:
                    pass

            if has_explicit_processed:
                if "Successfully processed:" in message:
                    processed_count += 1
            elif (
                "Successfully unpacked file!" in message
                or "Unpacked with SteamStub" in message
                or "[+] Unpacked with" in message
            ):
                processed_count += 1

            if has_explicit_no_drm:
                if "No Steam DRM detected in:" in message or message == "No Steam DRM detected in executable":
                    no_drm_count += 1
            elif (
                "No Steam DRM detected in executable" in message
                or "No variant matched" in message
                or "[-] No Steam DRM" in message
            ):
                no_drm_count += 1

            if "unpacking failed (code 2)" in message.lower() or "steamless failed" in message.lower():
                failed_count += 1

        scanned_count = max(scanned_count, processed_count + no_drm_count + failed_count)
        clean_count = max(no_drm_count, max(0, scanned_count - processed_count - failed_count))

        if getattr(self, "_steamless_error", False) and failed_count == 0 and processed_count == 0:
            if scanned_count == 0:
                overall_status = "failed"
            else:
                failed_count = 1
                clean_count = max(0, scanned_count - failed_count)
                overall_status = "failed"
        elif scanned_count == 0:
            overall_status = "no_executables"
        elif failed_count > 0 and processed_count > 0:
            overall_status = "partial_failure"
        elif failed_count > 0:
            overall_status = "failed"
        elif processed_count > 0:
            overall_status = "fixed"
        else:
            overall_status = "clean"

        return {
            "game_directory": "",
            "target_exe": None,
            "engine": "",
            "scanned_count": scanned_count,
            "fixed_count": processed_count,
            "clean_count": clean_count,
            "failed_count": failed_count,
            "overall_status": overall_status,
            "error": None,
            "executables": [],
        }

    def _show_steamless_resume_dialog(self):
        # Deferred imports
        from ui.dialogs.dialog_raiser import resolve_dialog_parent
        from ui.dialogs.steamless_resume import SteamlessResumeDialog

        summary = getattr(self, "_last_steamless_summary", None)
        if summary is None:
            summary = self._parse_summary_from_logs()

        exe_count = int(summary.get("scanned_count", 0))
        processed_count = int(summary.get("fixed_count", 0))
        had_error = bool(getattr(self, "_steamless_error", False)) or summary.get("failed_count", 0) > 0
        actual_success = processed_count > 0 and not had_error
        parent_widget = resolve_dialog_parent(self.main_window)

        dialog = SteamlessResumeDialog(
            game_name=self._steamless_game_name,
            exe_count=exe_count,
            processed_count=processed_count,
            success=actual_success,
            parent=parent_widget,
            summary=summary,
            logs=list(self._steamless_progress_log),
            appid=getattr(self, "_steamless_appid", ""),
        )
        dialog.exec()
        self._steamless_progress_log = []
        self._steamless_game_name = ""
        self._steamless_appid = ""

    def _handle_steamless_task_error(self, error_info):
        _, error_value, _ = error_info
        error_str = str(error_value)
        logger.error(f"Steamless processing failed: {error_str}")

        is_linux_no_drm = "no suitable game executables found" in error_str.lower()
        is_no_steam_drm = "no steam drm detected" in error_str.lower()

        self._steamless_progress_log.append(error_str)

        if is_linux_no_drm or is_no_steam_drm:
            self._steamless_error = False
            self._last_steamless_success = True if is_no_steam_drm else False
            if self._last_steamless_summary is None and is_linux_no_drm:
                self._last_steamless_summary = SteamlessIntegration.build_summary(
                    "",
                    [],
                    error=error_str,
                )
            if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
                self.main_window.simplified_terminal.set_stage_status("steamless", "completed")
        else:
            self._steamless_error = True
            self._last_steamless_success = False
            if self._last_steamless_summary is None:
                self._last_steamless_summary = SteamlessIntegration.build_summary(
                    "",
                    [],
                    error=error_str,
                )
            if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
                self.main_window.simplified_terminal.set_stage_status("steamless", "error")

        if self._steamless_manual_run:
            # QThread.finished will trigger _on_steamless_finished -> summary dialog.
            return

        if self.steamless_task:
            QTimer.singleShot(0, self._clear_steamless_task)

        self._schedule_post_steamless_finalize()

    def _get_steamless_status_text(self):
        summary = getattr(self, "_last_steamless_summary", None)
        if summary:
            overall = summary.get("overall_status")
            err = (summary.get("error") or "").lower()
            if overall == "no_executables" or "no suitable game executables found" in err:
                return "None (Linux Native)"
            if summary.get("fixed_count", 0) > 0 and summary.get("failed_count", 0) == 0:
                return "Success (code 0)"
            if summary.get("fixed_count", 0) > 0 and summary.get("failed_count", 0) > 0:
                return f"Partial ({summary['fixed_count']} fixed, {summary['failed_count']} failed)"
            if overall == "clean":
                return "None (Clean, code 1)"
            if overall == "failed":
                failed_codes = [
                    e.get("exit_code")
                    for e in summary.get("executables", [])
                    if e.get("status") == "failed" and e.get("exit_code") is not None
                ]
                if 2 in failed_codes:
                    return "Failed (code 2)"
                if failed_codes:
                    return f"Error (code {failed_codes[0]})"
                return "Error"

        log_text = "\n".join(self._steamless_progress_log).lower()
        if "no suitable game executables found" in log_text:
            return "None (Linux Native)"

        has_unpacked = (
            "unpacked successfully" in log_text
            or "successfully unpacked" in log_text
            or "successfully processed:" in log_text
            or "[+] unpacked with" in log_text
            or "unpacked with steamstub" in log_text
        )
        if has_unpacked or (self._last_steamless_success and not self._steamless_error and "code 0" in log_text):
            return "Success (code 0)"

        if "no steam drm detected in any of the" in log_text:
            return "None (Clean, code 1)"
        if "no steam drm detected" in log_text or "(code 1)" in log_text:
            return "None (Clean, code 1)"
        if self._last_steamless_success is None:
            return "Ready"
        elif self._last_steamless_success:
            return "Success (code 0)"
        else:
            if "code 2" in log_text or "unpacking failed" in log_text:
                return "Failed (code 2)"
            match_code = re.search(r"exit code\s*:?\s*(\d+)", log_text)
            if match_code:
                return f"Error (code {match_code.group(1)})"
            return "Error"

    def parse_steamless_result(self) -> str:
        """Parse the steamless summary or logs to determine what it did."""
        if not self._steamless_ran:
            return "Skipped"

        summary = getattr(self, "_last_steamless_summary", None)
        if summary:
            overall = summary.get("overall_status")
            err = (summary.get("error") or "").lower()
            if overall == "no_executables" or "no suitable game executables found" in err:
                return "Skipped (Linux Native)"

            fixed_items = [
                e for e in summary.get("executables", []) if e.get("status") == "fixed"
            ]
            failed_items = [
                e for e in summary.get("executables", []) if e.get("status") == "failed"
            ]
            if fixed_items and not failed_items:
                variants = []
                for item in fixed_items:
                    v = item.get("variant")
                    if v and v not in variants:
                        variants.append(v)
                if len(variants) == 1:
                    return f"Removed {variants[0]} (code 0)"
                return f"Removed DRM ({len(fixed_items)} EXE(s), code 0)"

            if fixed_items and failed_items:
                return f"Partial ({len(fixed_items)} fixed, {len(failed_items)} failed)"

            if failed_items:
                codes = [e.get("exit_code") for e in failed_items if e.get("exit_code") is not None]
                if 2 in codes:
                    return "Failed (DRM Unpack Error, code 2)"
                if codes:
                    return f"Failed (Error, code {codes[0]})"
                return "Failed / Error"

            if overall == "clean":
                return "None (Clean, code 1)"

        log_text = "\n".join(self._steamless_progress_log).lower()
        log_text_raw = "\n".join(self._steamless_progress_log)

        if "no suitable game executables found" in log_text:
            return "Skipped (Linux Native)"

        has_unpacked = (
            "unpacked successfully" in log_text
            or "successfully unpacked" in log_text
            or "successfully processed:" in log_text
            or "unpacked with steamstub" in log_text
            or "[+] unpacked with" in log_text
        )

        if has_unpacked or (self._last_steamless_success and not self._steamless_error and "code 0" in log_text):
            match = re.search(r"(?:SteamStub\s+)?Variant\s+([\d\.]+)", log_text_raw)
            if not match:
                match = re.search(r"Unpacked with\s+V?([\d\.]+x?)", log_text_raw)
            if match:
                version = match.group(1)
                return f"Removed SteamStub v{version} (code 0)"
            return "Removed DRM (code 0)"

        if "code 2" in log_text or "unpacking failed" in log_text:
            return "Failed (DRM Unpack Error, code 2)"

        if "no steam drm detected" in log_text or "no drm found" in log_text or "not encrypted" in log_text or "(code 1)" in log_text:
            return "None (Clean, code 1)"

        if self._steamless_error or self._last_steamless_success is False:
            match_code = re.search(r"exit code\s*:?\s*(\d+)", log_text)
            if match_code:
                return f"Failed (Error, code {match_code.group(1)})"
            return "Failed / Error"

        return "Unknown"
