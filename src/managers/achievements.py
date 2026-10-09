"""Steam achievements / SLScheevo schema generation.

Extracted from ``managers/task_manager.py``. Composed back into TaskManager via
``AchievementsMixin`` so the public API is unchanged.
"""

import logging
import os
import time
import urllib.request
import urllib.error
from typing import Optional, Tuple

from PyQt6.QtCore import QTimer, QMetaObject, Qt, pyqtSlot, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from core.tasks.generate_achievements_task import GenerateAchievementsTask
from utils.helpers import get_slscheevo_save_path
from utils.paths import Paths
from utils.task_runner import TaskRunner

logger = logging.getLogger(__name__)


class AchievementsMixin:
    """Detects achievements for a game and fetches their stat schemas."""

    def _check_appdetails(self, appid: str) -> tuple[Optional[bool], int]:
        url = f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=achievements"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                if data and str(appid) in data:
                    app_res = data[str(appid)]
                    if not app_res.get("success"):
                        return False, 0
                    app_data = app_res.get("data", {})
                    if "achievements" in app_data:
                        total = app_data["achievements"].get("total", 0)
                        if total > 0:
                            return True, total
                    return False, 0
                return False, 0
        except Exception as e:
            logger.warning(f"AppDetails API check failed for {appid}: {e}")
            return None, 0
    def _check_steamcommunity(self, appid: str) -> tuple[Optional[bool], int]:
        url = f"https://steamcommunity.com/stats/{appid}/achievements/"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode("utf-8")
                title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
                title = title_match.group(1) if title_match else ""
                if "Error" in title:
                    return False, 0
                
                count_match = re.search(r"Total achievements:\s*<span class=\"wt\">(\d+)</span>", html, re.IGNORECASE)
                if count_match:
                    total = int(count_match.group(1))
                    if total > 0:
                        return True, total
                
                if "Achievements" in title or "achievements" in html.lower():
                    return True, 0
                return False, 0
        except Exception as e:
            logger.warning(f"Steam Community page check failed for {appid}: {e}")
            return None, 0
    def _check_game_achievements_info(self, appid: str) -> tuple[bool, int]:
        ad_res, ad_count = self._check_appdetails(appid)
        if ad_res is True:
            logger.info(f"[Store API] Confirmed game has achievements: {ad_count}")
            return True, ad_count

        sc_res, sc_count = self._check_steamcommunity(appid)
        if sc_res is True:
            logger.info(f"[Steam Community] Confirmed game has achievements: {sc_count}")
            return True, sc_count

        if ad_res is False and sc_res is False:
            logger.info(f"Both API and Community check confirmed game {appid} has NO achievements.")
            return False, 0

        logger.warning(f"Could not determine achievements status for appid {appid} due to network errors. Defaulting to True.")
        return True, 0
    def _start_achievement_generation(self):
        if not self.game_data:
            self._finalize_job_logic()
            return

        app_id = self.game_data.get("appid")
        if not app_id:
            self._finalize_job_logic()
            return

        # Check if achievements generation is disabled in settings
        from utils.settings import get_settings
        settings = get_settings()
        if not settings.value("generate_achievements", False, type=bool):
            logger.info("Achievements generation is disabled in settings. Skipping step.")
            if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
                self.main_window.simplified_terminal.set_stage_status("achievements", "skipped")

            self._slscheevo_ran = False
            self._slscheevo_error = False
            self._slscheevo_completed = True
            self._last_slscheevo_status = "skipped_no_ach"
            self._last_slscheevo_status_text = "Skipped"

            if self._current_active_step == "achievements":
                self._current_active_step = None
                self._waiting_for_achievements = False
                self._finalize_job_logic()
            elif self._waiting_for_achievements:
                self._waiting_for_achievements = False
                QMetaObject.invokeMethod(
                    self, "_finalize_job_logic", Qt.ConnectionType.QueuedConnection
                )
            return

        logger.info(f"Checking achievements availability for AppID: {app_id}...")

        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.set_stage_status("achievements", "in_progress")

        def check_thread():
            try:
                has_ach, count = self._check_game_achievements_info(str(app_id))
            except Exception as e:
                # If the network check raises unexpectedly, default to True/0
                # so the job continues rather than stalling the queue forever.
                logger.error(
                    f"Achievement check thread raised unexpectedly for {app_id}: {e}",
                    exc_info=True,
                )
                has_ach, count = True, 0
            self.achievements_checked.emit(has_ach, count)

        threading.Thread(target=check_thread, daemon=True).start()
    @pyqtSlot(bool, int)
    def _on_achievements_checked(self, has_achievements: bool, count: int):
        if not self.is_processing:
            return

        # Re-check the setting here: the Store API thread was already in flight
        # when this callback fires, so the user may have disabled achievements
        # mid-session between when the thread was spawned and now.
        from utils.settings import get_settings
        if not get_settings().value("generate_achievements", False, type=bool):
            logger.info("Achievements generation is disabled in settings (re-checked in callback). Skipping.")
            self._slscheevo_ran = False
            self._slscheevo_error = False
            self._slscheevo_completed = True
            self._last_slscheevo_status = "skipped_no_ach"
            self._last_slscheevo_status_text = "Skipped"
            if self._current_active_step == "achievements":
                self._current_active_step = None
                self._waiting_for_achievements = False
                self._finalize_job_logic()
            elif self._waiting_for_achievements:
                self._waiting_for_achievements = False
                QMetaObject.invokeMethod(
                    self, "_finalize_job_logic", Qt.ConnectionType.QueuedConnection
                )
            return

        if not has_achievements:
            logger.info("Game has no achievements. Skipping achievements generation step.")
            if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
                self.main_window.simplified_terminal.set_stage_status("achievements", "skipped_no_achievements")

            self._slscheevo_ran = False
            self._slscheevo_error = False
            self._slscheevo_completed = True
            self._last_slscheevo_status = "skipped_no_ach"
            self._last_slscheevo_status_text = "No Achievements"

            if self._current_active_step == "achievements":
                self._current_active_step = None
                self._waiting_for_achievements = False
                self._finalize_job_logic()
            elif self._waiting_for_achievements:
                self._waiting_for_achievements = False
                QMetaObject.invokeMethod(
                    self, "_finalize_job_logic", Qt.ConnectionType.QueuedConnection
                )
        else:
            logger.info(f"Game has achievements (count: {count}). Starting achievements generation...")
            self._game_achievements_count = count if count > 0 else None
            self._run_slscheevo_task()
    def _run_slscheevo_task(self):
        if not self.game_data:
            self._finalize_job_logic()
            return

        app_id = self.game_data.get("appid")
        if not app_id:
            self._finalize_job_logic()
            return

        logger.info("\n" + "=" * 40)
        logger.info("Starting Steam Achievement Generation...")

        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.set_stage_status(
                "achievements", "in_progress", self._game_achievements_count
            )

        self.achievement_task = GenerateAchievementsTask()
        self.achievement_task.progress.connect(logger.info)

        self.achievement_task_runner = TaskRunner()
        self.achievement_task_runner.cleanup_complete.connect(
            self._on_achievement_task_cleanup
        )
        self.achievement_worker = self.achievement_task_runner.run(
            self.achievement_task.run, app_id
        )

        self._update_status_button_color()
        self._slscheevo_ran = True

        self.achievement_worker.finished.connect(
            self._on_achievement_generation_complete
        )
        self.achievement_worker.error.connect(self._handle_achievement_error)
    def _on_achievement_generation_complete(self, result):
        if result is None:
            success = False
            message = "Unknown error"
        else:
            success = result.get("success", False)
            message = result.get("message", "Unknown status")

        self._last_slscheevo_success = success
        self._last_slscheevo_message = message
        if success:
            logger.info(f"Achievement generation completed: {message}")
            self._last_slscheevo_status = "ok"
            ach_count = getattr(self, "_game_achievements_count", None)
            if "already exist" in message.lower() or "no missing stats" in message.lower() or "already exists" in message.lower():
                self._last_slscheevo_status_text = f"Up-to-date ({ach_count})" if ach_count else "Up-to-date"
            else:
                self._last_slscheevo_status_text = f"Generated ({ach_count})" if ach_count else "Generated"
        else:
            logger.info(f"Achievement generation failed: {message}")
            self._last_slscheevo_status = "error"
            self._last_slscheevo_status_text = "Failed"

        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.set_stage_status("achievements", "completed" if success else "error", getattr(self, "_game_achievements_count", None))

        self._slscheevo_completed = True

        if self._waiting_for_achievements:
            self._waiting_for_achievements = False
            if self._current_active_step == "achievements":
                self._current_active_step = None
            QMetaObject.invokeMethod(
                self, "_finalize_job_logic", Qt.ConnectionType.QueuedConnection
            )
    def _handle_achievement_error(self, error_info):
        _, error_value, _ = error_info
        logger.error(f"Achievement generation failed: {error_value}")
        self._last_slscheevo_success = False
        self._slscheevo_error = True
        self._last_slscheevo_message = str(error_value)
        self._last_slscheevo_status = "error"
        self._last_slscheevo_status_text = "Failed"

        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.set_stage_status("achievements", "error", getattr(self, "_game_achievements_count", None))

        self._slscheevo_completed = True

        if self._waiting_for_achievements:
            self._waiting_for_achievements = False
            if self._current_active_step == "achievements":
                self._current_active_step = None
            QMetaObject.invokeMethod(
                self, "_finalize_job_logic", Qt.ConnectionType.QueuedConnection
            )
    def _on_achievement_task_cleanup(self):
        self.achievement_task_runner = None
        self.achievement_task = None
        self.achievement_worker = None
        self.main_window.job_queue.check_if_safe_to_start_next_job()
