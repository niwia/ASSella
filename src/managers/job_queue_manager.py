import os
import sys
import logging
import time
import threading
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, QTimer, QObject, pyqtSlot

from core import steam_helpers

logger = logging.getLogger(__name__)


class JobQueueManager(QObject):
    def __init__(self, main_window):
        super().__init__(parent=main_window)
        self.main_window = main_window
        self.job_queue = []
        self.jobs_completed_count = 0
        self.steam_restart_prompt_pending = False
        self.is_showing_completion_dialog = False

    def add_workshop_job(self, wids, api_key, max_downloads, cellid, steam_integration, dest_path):
        """Add a Workshop job to the queue with descriptive name resolution"""
        display_name = f"Workshop Mod ({len(wids)} items)"
        parent_game_name = ""

        try:
            if wids:
                import requests
                wid_first = wids[0]
                url = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
                r = requests.post(url, data={"itemcount": 1, "publishedfileids[0]": wid_first}, timeout=4)
                if r.status_code == 200:
                    det = r.json().get("response", {}).get("publishedfiledetails", [{}])[0]
                    title = det.get("title")
                    consumer_appid = str(det.get("consumer_app_id", ""))
                    if self.main_window and getattr(self.main_window, "game_manager", None):
                        game = self.main_window.game_manager.get_game(consumer_appid)
                        if game:
                            parent_game_name = game.get("game_name", "")

                    if title:
                        if len(wids) > 1:
                            display_name = f"Workshop: {title} & {len(wids) - 1} more"
                        else:
                            display_name = f"Workshop: {title}"
                        if parent_game_name:
                            display_name += f" ({parent_game_name})"
        except Exception as e:
            logger.debug(f"Could not resolve workshop title for queue display: {e}")

        job = {
            "type": "workshop",
            "metadata": {"game_name": display_name},
            "workshop_data": {
                "wids": wids,
                "api_key": api_key,
                "max_downloads": max_downloads,
                "cellid": cellid,
                "steam_integration": steam_integration,
                "dest_path": dest_path,
                "display_name": display_name,
            }
        }
        self.job_queue.append(job)
        logger.info(f"Added new Workshop job '{display_name}' to queue with {len(wids)} items.")

        self._update_ui_state()

        if not self.main_window.task_manager.is_processing:
            logger.info("Not processing, starting new Workshop job from queue.")
            if hasattr(self.main_window, "log_output") and self.main_window.log_output:
                self.main_window.log_output.clear()
            self._start_next_job()
        else:
            logger.info("App is busy, Workshop job added to queue.")

    def add_job(self, file_path, metadata=None):
        """Add a job to the queue (Thread-Safe)"""
        if threading.current_thread() is not threading.main_thread():
            # Marshal to main thread, passing metadata as well
            import json as _json
            try:
                meta_str = _json.dumps(metadata or {})
            except Exception:
                meta_str = "{}"
            QMetaObject.invokeMethod(
                self,
                "_add_job_on_main_thread",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, file_path),
                Q_ARG(str, meta_str),
            )
            return

        self._do_add_job(file_path, metadata)

    @pyqtSlot(str, str)
    def _add_job_on_main_thread(self, file_path: str, meta_str: str) -> None:
        """Slot that runs on the main thread to safely add a job."""
        import json as _json
        try:
            metadata = _json.loads(meta_str)
        except Exception:
            metadata = {}
        self._do_add_job(file_path, metadata)

    def _do_add_job(self, file_path: str, metadata: dict) -> None:
        """Internal: actually add the job on the main thread."""
        if not os.path.exists(file_path):
            logger.error(f"Failed to add job: file {file_path} does not exist.")
            QMessageBox.critical(
                self.main_window,
                "Error",
                f"Could not add job: File not found at {file_path}",
            )
            return

        job = {"path": file_path, "metadata": metadata or {}}
        self.job_queue.append(job)
        logger.info(f"Added new job to queue: {os.path.basename(file_path)}")

        self._update_ui_state()

        if not self.main_window.task_manager.is_processing:
            logger.info("Not processing, starting new job from queue.")
            if hasattr(self.main_window, "log_output") and self.main_window.log_output:
                self.main_window.log_output.clear()
            self._start_next_job()
        else:
            logger.info("App is busy, job added to queue.")

    def move_item_up(self):
        """Move selected queue item up"""
        current_row = self.main_window.ui_state.queue_list_widget.currentRow()
        if current_row > 0:
            item = self.job_queue.pop(current_row)
            self.job_queue.insert(current_row - 1, item)
            self._update_queue_display()
            self.main_window.ui_state.queue_list_widget.setCurrentRow(current_row - 1)

    def move_item_down(self):
        """Move selected queue item down"""
        current_row = self.main_window.ui_state.queue_list_widget.currentRow()
        if current_row != -1 and current_row < len(self.job_queue) - 1:
            item = self.job_queue.pop(current_row)
            self.job_queue.insert(current_row + 1, item)
            self._update_queue_display()
            self.main_window.ui_state.queue_list_widget.setCurrentRow(current_row + 1)

    def remove_item(self):
        """Remove selected queue item"""
        current_row = self.main_window.ui_state.queue_list_widget.currentRow()
        if current_row == -1:
            logger.debug("Remove item clicked, but no item is selected.")
            return

        try:
            removed_job = self.job_queue.pop(current_row)
            logger.info(
                f"Removed job from queue: {os.path.basename(removed_job['path'])}"
            )
            self._update_queue_display()

            if current_row < self.main_window.ui_state.queue_list_widget.count():
                self.main_window.ui_state.queue_list_widget.setCurrentRow(current_row)
            elif self.main_window.ui_state.queue_list_widget.count() > 0:
                self.main_window.ui_state.queue_list_widget.setCurrentRow(
                    current_row - 1
                )

        except Exception as e:
            logger.error(f"Error removing queue item: {e}", exc_info=True)

    def _start_next_job(self):
        """Start the next job in queue"""
        self._update_ui_state()

        if not self.job_queue:
            self._handle_queue_completion()
            return

        next_job = self.job_queue.pop(0)
        self._update_ui_state()

        if next_job.get("type") == "workshop":
            workshop_data = next_job["workshop_data"]
            self.main_window.task_manager.start_workshop_download(workshop_data)
        else:
            file_path = next_job["path"]
            metadata = next_job.get("metadata", {})
            self.main_window.task_manager.start_zip_processing(file_path, metadata)

    def _handle_queue_completion(self):
        """Handle when queue is empty"""
        if self.is_showing_completion_dialog:
            return

        self.is_showing_completion_dialog = True
        try:
            was_pending = self.steam_restart_prompt_pending
            self.steam_restart_prompt_pending = False

            if was_pending:
                from utils.settings import get_settings

                settings = get_settings()
                prompt_steam_restart = settings.value(
                    "prompt_steam_restart", True, type=bool
                )

                if prompt_steam_restart:
                    QTimer.singleShot(0, lambda count=self.jobs_completed_count: self._prompt_for_steam_restart(count))
                else:
                    logger.info(
                        "Steam restart prompt disabled by settings. Skipping prompt."
                    )
            elif self.jobs_completed_count > 0:
                logger.info(f"Queue Finished: All {self.jobs_completed_count} job(s) have finished successfully!")

            self.jobs_completed_count = 0
        finally:
            self.is_showing_completion_dialog = False

    def _update_ui_state(self):
        """Update UI based on queue state"""
        if not self.main_window or not self.main_window.isVisible():
            return

        has_jobs = len(self.job_queue) > 0
        is_processing = self.main_window.task_manager.is_processing

        self.main_window.ui_state.update_queue_visibility(is_processing, has_jobs)
        self._update_queue_display()

    def _update_queue_display(self):
        """Update the queue list widget"""
        self.main_window.ui_state.queue_list_widget.clear()
        display_names = []
        for job in self.job_queue:
            if job.get("type") == "workshop":
                game_name = job.get("workshop_data", {}).get("display_name")
                if not game_name:
                    wids_count = len(job["workshop_data"]["wids"])
                    game_name = f"Workshop Downloader ({wids_count} items)"
            else:
                game_name = job.get("metadata", {}).get("game_name")
                if not game_name:
                    filename = os.path.basename(job["path"])
                    if filename.startswith("accela_fetch_") and filename.endswith(".zip"):
                        appid = filename[13:-4]
                        if self.main_window.game_manager:
                            game = self.main_window.game_manager.get_game(appid)
                            if game:
                                game_name = game.get("game_name")
                if not game_name:
                    game_name = os.path.basename(job["path"])
            display_names.append(game_name)
        self.main_window.ui_state.queue_list_widget.addItems(display_names)

    def _check_if_safe_to_start_next_job(self):
        """Check if it's safe to start the next job"""
        if (
            not self.main_window.task_manager.is_processing
            and not self.main_window.task_manager.is_awaiting_zip_task_stop
            and not self.main_window.task_manager.is_awaiting_speed_monitor_stop
            and not self.main_window.task_manager.is_awaiting_download_stop
            and not getattr(self.main_window.task_manager, "is_awaiting_workshop_stop", False)
            and not self.main_window.task_manager.achievement_task_runner
        ):
            logger.debug("All thread cleanup flags are clear. Safe to start next job.")
            self._start_next_job()
        else:
            logger.debug(
                f"Not starting next job yet. State: "
                f"is_processing={self.main_window.task_manager.is_processing}, "
                f"awaiting_zip={self.main_window.task_manager.is_awaiting_zip_task_stop}, "
                f"awaiting_speed={self.main_window.task_manager.is_awaiting_speed_monitor_stop}, "
                f"awaiting_download={self.main_window.task_manager.is_awaiting_download_stop}, "
                f"achievement_runner={self.main_window.task_manager.achievement_task_runner is not None}"
            )

    def check_if_safe_to_start_next_job(self):
        self._check_if_safe_to_start_next_job()

    def _prompt_for_steam_restart(self, completed_count=0):
        """Prompt user to restart Steam (Run via QTimer on Main Thread)"""
        is_running = steam_helpers.is_steam_running()
        action_word = "restart" if is_running else "start"
        Title = "Restart Steam" if is_running else "Start Steam"

        prefix = ""
        if completed_count > 0:
            prefix = f"All {completed_count} job(s) have finished successfully!\n\n"

        prompt_text = f"{prefix}Steam-integrated changes were created. Would you like to {action_word} Steam now to apply them?"

        reply = QMessageBox.question(
            self.main_window,
            Title,
            prompt_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            logger.info(f"User agreed to {action_word} Steam.")
            # Run heavy lifting in background
            threading.Thread(target=self._perform_steam_restart, daemon=True).start()

    def _perform_steam_restart(self):
        """Execute Steam restart logic in background thread"""
        try:
            if sys.platform == "linux":
                logger.info("Attempting to kill Steam process...")
                steam_helpers.kill_steam_process()
                time.sleep(1)

                result = steam_helpers.start_steam()

                if result == "NEEDS_USER_PATH":
                    QMetaObject.invokeMethod(
                        self.main_window,
                        "handle_linux_steam_path_selection",
                        Qt.ConnectionType.QueuedConnection,
                    )
                elif result == "SUCCESS":
                    logger.info("Steam started successfully with cached libraries.")
                else:
                    logger.warning("Failed to start Steam.")
                    self._show_message_safe(
                        "Execution Failed",
                        "Could not start Steam.",
                    )

            else:
                # Windows
                steam_path = steam_helpers.find_steam_install()
                if steam_path:
                    logger.info("Closing Steam...")
                    if not steam_helpers.kill_steam_process():
                        logger.info(
                            "Steam process was not running or could not be killed."
                        )

                    time.sleep(1)

                    injector_path = os.path.join(steam_path, "DLLInjector.exe")
                    if os.path.exists(injector_path):
                        logger.info(
                            "Windows Wrapper Mode: Launching DLLInjector.exe..."
                        )
                        if not steam_helpers.run_dll_injector(steam_path):
                            self._show_message_safe(
                                "Injector Failed",
                                f"Could not launch DLLInjector.exe from {steam_path}.",
                            )
                    else:
                        user32_path = os.path.join(steam_path, "user32.dll")
                        if os.path.exists(user32_path):
                            logger.info(
                                "DLLInjector.exe not found, but user32.dll exists. Starting Steam normally..."
                            )
                            steam_helpers.start_steam()
                        else:
                            self._show_message_safe(
                                "Injector Not Found",
                                "DLLInjector.exe not found in Steam folder.",
                            )
                else:
                    self._show_message_safe(
                        "Error",
                        "Could not find Steam installation path.",
                    )

        except Exception as e:
            logger.error(f"Error during Steam restart: {e}")

    @staticmethod
    def _show_message_safe(title, text):
        """Helper to show MessageBox from background thread"""
        logger.error(f"MSG: {title} - {text}")

    def clear(self):
        """Clear the job queue"""
        self.job_queue.clear()
        self._update_ui_state()
