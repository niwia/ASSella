import logging
import os
import re
import shutil
import sys
import time
import tempfile
import threading
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Set, List
import json
import urllib.request
import urllib.error

# QObject and pyqtSlot for robust threading
from PyQt6.QtCore import QTimer, QMetaObject, Qt, QObject, pyqtSlot, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QMessageBox

try:
    import psutil
except ImportError:
    psutil = None

from core import steam_helpers
from core.tasks.download_depots_task import DownloadDepotsTask
from core.tasks.download_workshop_task import DownloadWorkshopTask
from core.tasks.generate_achievements_task import GenerateAchievementsTask
from core.tasks.process_zip_task import ProcessZipTask
from core.tasks.steamless_task import SteamlessTask

from utils.helpers import get_base_path
from utils.steam_manifest import get_game_directory, write_acf_file
from utils.wrapper_metadata import persist_selected_dlcs
from utils.yaml_config_manager import (
    get_user_config_path,
    add_additional_app,
    add_dlc_data,
    is_slssteam_mode_enabled,
    is_slssteam_config_management_enabled,
)

from utils.paths import Paths
from utils.task_runner import TaskRunner

logger = logging.getLogger(__name__)


class TaskManager(QObject):
    achievements_checked = pyqtSignal(bool, int)

    def __init__(self, main_window):
        super().__init__(parent=main_window)
        self.main_window = main_window
        self.settings = main_window.settings

        self.achievements_checked.connect(
            self._on_achievements_checked, Qt.ConnectionType.QueuedConnection
        )

        # Task state
        self.speed_monitor_task = None
        self.speed_monitor_runner = None
        self.is_awaiting_speed_monitor_stop = False

        self.zip_task = None
        self.zip_task_runner = None
        self.is_awaiting_zip_task_stop = False

        self.download_task = None
        self.download_runner = None
        self.is_awaiting_download_stop = False
        self.workshop_task = None
        self.workshop_runner = None
        self.is_awaiting_workshop_stop = False
        self.achievement_task = None
        self.achievement_task_runner = None
        self.achievement_worker = None
        self.steamless_task = None
        self.slssteam_download_task = None
        self.slssteam_download_runner = None


        # Processing state
        self.is_processing = False
        self.is_download_paused = False
        self.is_cancelling = False
        self.current_job: Optional[str] = None
        self.current_job_metadata: Optional[Dict[str, Any]] = None
        self.game_data: Optional[Dict[str, Any]] = None
        self.current_dest_path: Optional[str] = None
        self.slssteam_mode_was_active = False
        self.library_mode_was_active = False
        self._steamless_success = None
        self._steamless_manual_run = False

        # Job step states
        self._job_steps_completed: Set[str] = set()

        # Progress tracking
        self._steamless_progress_log = []
        self._steamless_game_name = ""

        # Status tracking
        self._last_steamless_success = None
        self._steamless_ran = False
        self._steamless_error = False
        self._last_slscheevo_success = None
        self._last_slscheevo_message = ""
        self._slscheevo_ran = False
        self._slscheevo_error = False
        self._slscheevo_completed = False
        self._waiting_for_achievements = False
        self._game_achievements_count = None

        self._last_ddm_status = "not_run"
        self._last_ddm_status_text = "N/A"
        self._last_slscheevo_status = "not_run"
        self._last_slscheevo_status_text = "N/A"
        self._last_steamless_status = "not_run"
        self._last_steamless_status_text = "N/A"
        self._last_installed_game = None

        # Download metrics and timing
        self._download_start_time = 0.0
        self._download_end_time = 0.0
        self._last_download_duration = 0.0
        self._last_download_size = 0
        self._last_download_avg_speed = 0.0

        self._delete_files_on_cancel: Optional[bool] = None

        # Guards for post-download finalization
        self._current_active_step = None
        # Event used to abort the finalize IO thread early on job cancellation.
        self._finalize_cancel_event = threading.Event()

        # Status colors
        self.STATUS_OK = "#00FF00"
        self.STATUS_IN_PROGRESS = "#FFA500"
        self.STATUS_ERROR = "#FF0000"
        self.STATUS_NOT_RUN = "accent"

    @property
    def last_installed_game(self):
        return self._last_installed_game

    def _is_selected_depots_linux(self, selected_depots) -> bool:
        if not self.game_data or not selected_depots:
            return False
        depots = self.game_data.get("depots") or {}
        found_any = False
        for d_id in selected_depots:
            d_data = depots.get(str(d_id)) or {}
            if not d_data:
                # Depot not found in game_data — cannot confirm Linux, skip it
                continue
            found_any = True
            oslist = (d_data.get("oslist") or "").lower()
            desc = (d_data.get("desc") or "").lower()
            is_linux = (oslist == "linux") or ("[linux]" in desc) or ("linux" in desc)
            if not is_linux:
                return False
        # Only return True if we actually verified at least one depot is Linux
        return found_any

    def _is_current_job_linux(self) -> bool:
        if not self.game_data:
            return False
        selected_depots = self.game_data.get("selected_depots_list")
        return self._is_selected_depots_linux(selected_depots)

    def _init_simplified_stages(self, selected_depots=None):
        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            st = self.main_window.simplified_terminal
            st.reset_stages()

            # Check Steamless status
            steamless_enabled = self.settings.value("use_steamless", False, type=bool)
            steamless_aio_enabled = self.settings.value("use_steamless_aio", False, type=bool)
            
            depots_to_check = selected_depots or (self.game_data.get("selected_depots_list") if self.game_data else None)
            
            if not (steamless_enabled or steamless_aio_enabled):
                st.set_stage_status("steamless", "skipped")
            elif depots_to_check and self._is_selected_depots_linux(depots_to_check):
                st.set_stage_status("steamless", "skipped_linux")
            else:
                st.set_stage_status("steamless", "pending")

            # Check Achievements status
            achievements_enabled = self.settings.value("generate_achievements", False, type=bool)
            if not achievements_enabled:
                st.set_stage_status("achievements", "skipped")
            else:
                st.set_stage_status("achievements", "pending")

    def start_zip_processing(self, zip_path, metadata=None):
        self.is_processing = True
        self.current_job = zip_path
        self.current_job_metadata = metadata or {}

        self._job_steps_completed.clear()

        # If package was already preprocessed in ZipImportConfirmationDialog,
        # proceed directly to depot selection without flashing the download screen
        preprocessed = (metadata or {}).get("preprocessed_game_data")
        if preprocessed:
            logger.info(f"Using preprocessed package data for {zip_path}; launching depot selection directly.")
            QTimer.singleShot(0, lambda data=preprocessed: self._on_zip_processed(data))
            return

        self._init_simplified_stages()
        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            st = self.main_window.simplified_terminal
            if hasattr(st, "dl_text_2_0") and st.dl_text_2_0:
                st.dl_text_2_0.setText("Extracting Manifest Files")
            game_name = (metadata or {}).get("game_name") or os.path.basename(zip_path)
            st.set_stage_status("download", "in_progress")
            st.show_active_job(game_name)

        if self.main_window:
            self.main_window.progress_bar.setVisible(True)
            self.main_window.progress_bar.setRange(0, 0)
            self.main_window.drop_text_label.setText(
                f"Processing: {os.path.basename(zip_path)}"
            )

        self.zip_task = ProcessZipTask()
        self.zip_task_runner = TaskRunner()
        self.is_awaiting_zip_task_stop = True
        self.zip_task_runner.cleanup_complete.connect(self._on_zip_task_stopped)

        worker = self.zip_task_runner.run(self.zip_task.run, zip_path)
        worker.finished.connect(self._on_zip_processed)
        worker.error.connect(self._handle_task_error)

    def _on_zip_processed(self, game_data):
        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.set_stage_status("download", "completed")

        self.main_window.progress_bar.setRange(0, 100)
        self.main_window.progress_bar.setValue(100)

        # Merge pre-assembled metadata (from SmartUpdateTask or JobQueueManager) if present
        if self.current_job_metadata:
            merged = dict(self.current_job_metadata)
            if game_data:
                if "manifests" in game_data:
                    if not merged.get("_smart_update"):
                        merged.setdefault("manifests", {}).update(game_data.get("manifests", {}))
                    else:
                        for m_k, m_v in game_data.get("manifests", {}).items():
                            merged.setdefault("manifests", {}).setdefault(m_k, m_v)

                # Deep merge for depots: ensure freshly parsed depot keys and info from game_data
                # are always preserved and never clobbered by keyless metadata (e.g. from Steam .acf)
                if "depots" in game_data and game_data["depots"]:
                    merged_depots = dict(merged.get("depots") or {})
                    for d_id, d_info in game_data["depots"].items():
                        if d_id not in merged_depots:
                            merged_depots[d_id] = dict(d_info)
                        else:
                            merged_item = dict(merged_depots[d_id])
                            merged_item.update({k: v for k, v in d_info.items() if v is not None})
                            if not merged_item.get("key") and d_info.get("key"):
                                merged_item["key"] = d_info["key"]
                            merged_depots[d_id] = merged_item
                    merged["depots"] = merged_depots

                for k, v in game_data.items():
                    if k in ("manifests", "depots"):
                        continue
                    if v and (k not in merged or not merged[k]):
                        merged[k] = v
            game_data = merged

        self.game_data = game_data

        if self.game_data and self.game_data.get("depots"):

            pre_selected = (self.current_job_metadata or {}).get("selected_depots_list")
            if pre_selected:
                self.game_data["selected_depots_list"] = pre_selected
                library_dest = (self.current_job_metadata or {}).get("library_path") or (self.game_data or {}).get("library_path")
                # Persist confirmed pre-selection so future updates recall it
                appid = str((self.game_data or {}).get("appid", ""))
                depots = (self.game_data or {}).get("depots") or {}
                if appid and pre_selected:
                    try:
                        import json
                        self.settings.setValue(
                            f"depot_selection/{appid}",
                            json.dumps({
                                "selected": pre_selected,
                                "all_available": list(depots.keys()),
                                "descriptions": {d: depots.get(d, {}).get("desc", "") for d in pre_selected}
                            })
                        )
                        logger.info(f"Persisted pre-selected depot selection for AppID {appid}: {pre_selected}")
                    except Exception as e:
                        logger.warning(f"Failed to cache pre-selected depot selection: {e}")
                self._start_download_with_destination(pre_selected, library_dest)
            else:
                self._show_depot_selection_dialog()
        else:
            QMessageBox.warning(
                self.main_window,
                "No Depots Found",
                "Zip file processed, but no downloadable depots were found.",
            )
            self.job_finished()

    def _show_depot_selection_dialog(self):
        # Deferred import to prevent circular dependency
        from ui.dialogs.depotselection import DepotSelectionDialog
        import json

        game_data = self.game_data
        if not game_data:
            self.job_finished()
            return

        appid = str(game_data.get("appid", ""))
        depots = game_data.get("depots") or {}

        # Load any previously saved depot selection so the dialog restores ticks
        saved_selection = None
        if appid:
            raw = self.settings.value(f"depot_selection/{appid}", "", type=str)
            if raw:
                try:
                    saved_data = json.loads(raw)
                    saved_selection = saved_data.get("selected", [])
                except Exception:
                    pass
            # Fallback: if not in QSettings, check if the game is already installed with an ACF containing InstalledDepots
            if not saved_selection:
                acf_installed = (self.game_data or {}).get("installed_depots")
                if acf_installed and isinstance(acf_installed, list):
                    saved_selection = [str(d) for d in acf_installed]

        auto_skip_single_choice = self.settings.value(
            "auto_skip_single_choice", False, type=bool
        )
        if auto_skip_single_choice and len(depots) == 1:
            from ui.dialogs.fetchmanifest import SingleDepotTimerDialog
            from PyQt6.QtWidgets import QDialog
            dlg = SingleDepotTimerDialog(self.main_window, "Single Depot Option", "Game has only one depot.\n\nProceed to download and add it to queue?", seconds=3)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                selected_depots = list(depots.keys())
                if self.game_data:
                    self.game_data["selected_depots_list"] = selected_depots
                # Persist single-depot selection
                if appid and selected_depots:
                    try:
                        self.settings.setValue(
                            f"depot_selection/{appid}",
                            json.dumps({
                                "selected": selected_depots,
                                "all_available": list(depots.keys()),
                                "descriptions": {d: depots.get(d, {}).get("desc", "") for d in selected_depots}
                            })
                        )
                    except Exception as e:
                        logger.warning(f"Failed to cache single-depot selection: {e}")
                single_dest = (self.current_job_metadata or {}).get("library_path") or (self.game_data or {}).get("library_path")
                self._start_download_with_destination(selected_depots, single_dest)
            else:
                self.job_finished()
            return

        self.main_window.ui_state.depot_dialog = DepotSelectionDialog(
            game_data["appid"],
            game_data["game_name"],
            game_data["depots"],
            game_data.get("header_url"),
            self.main_window,
            selected_depots=saved_selection,
        )

        if self.main_window.ui_state.depot_dialog.exec():
            selected_depots = (
                self.main_window.ui_state.depot_dialog.get_selected_depots()
            )
            selected_files = (
                self.main_window.ui_state.depot_dialog.get_selected_files()
            )
            selected_storage = (
                self.main_window.ui_state.depot_dialog.get_selected_storage()
            )
            if self.game_data:
                self.game_data["selected_depots_list"] = selected_depots
                if selected_files:
                    self.game_data["selected_files_list"] = selected_files

            if not selected_depots:
                self.job_finished()
                return

            # Persist the confirmed selection to QSettings so future updates recall it
            if appid and selected_depots:
                try:
                    self.settings.setValue(
                        f"depot_selection/{appid}",
                        json.dumps({
                            "selected": selected_depots,
                            "all_available": list(depots.keys()),
                            "descriptions": {d: depots.get(d, {}).get("desc", "") for d in selected_depots}
                        })
                    )
                    logger.info(f"Saved depot selection for AppID {appid}: {selected_depots}")
                except Exception as e:
                    logger.warning(f"Failed to cache depot selection: {e}")

            self._start_download_with_destination(selected_depots, selected_storage)
        else:
            # User cancelled — do NOT save anything
            self.job_finished()

    def _start_download_with_destination(self, selected_depots, dest_path=None):
        if not dest_path:
            dest_path = self._get_destination_path()
        if dest_path:
            self._start_download(selected_depots, dest_path)
        else:
            self.job_finished()

    def _get_destination_path(self):
        current_job_metadata = self.current_job_metadata or {}
        existing_library_path = current_job_metadata.get("library_path") or (self.game_data or {}).get("library_path")
        if existing_library_path and os.path.isdir(existing_library_path):
            if is_slssteam_mode_enabled():
                self._handle_slssteam_mode()
            return existing_library_path

        default_dl_dir = self.settings.value("default_download_directory", "")
        if default_dl_dir and os.path.isdir(default_dl_dir):
            if is_slssteam_mode_enabled():
                self._handle_slssteam_mode()
            return default_dl_dir

        slssteam_mode = is_slssteam_mode_enabled()
        library_mode = self.settings.value("library_mode", False, type=bool)
        from_web = current_job_metadata.get("from_web_ui", False)
        is_headless = os.environ.get("QT_QPA_PLATFORM") == "offscreen" or (self.main_window and not self.main_window.isVisible())

        if slssteam_mode:
            self._handle_slssteam_mode()
            return self._get_library_destination_path()
        elif library_mode:
            return self._get_library_destination_path()
        else:
            if from_web or is_headless:
                libraries = steam_helpers.get_steam_libraries()
                if libraries:
                    return libraries[0]
                default_dir = os.path.expanduser("~/.local/share/ACCELA/downloads")
                os.makedirs(default_dir, exist_ok=True)
                return default_dir
            return QFileDialog.getExistingDirectory(
                self.main_window, "Select Destination Folder"
            )

    def _get_library_destination_path(self):
        # Deferred import
        from ui.dialogs.steamlibrary import SteamLibraryDialog

        libraries = steam_helpers.get_steam_libraries()
        if libraries:
            auto_skip_single_choice = self.settings.value(
                "auto_skip_single_choice", False, type=bool
            )
            if auto_skip_single_choice and len(libraries) == 1:
                return libraries[0]
            
            current_job_metadata = self.current_job_metadata or {}
            from_web = current_job_metadata.get("from_web_ui", False)
            is_headless = os.environ.get("QT_QPA_PLATFORM") == "offscreen" or (self.main_window and not self.main_window.isVisible())
            if from_web or is_headless:
                return libraries[0]

            dialog = SteamLibraryDialog(libraries, self.main_window)
            if dialog.exec():
                return dialog.get_selected_path()
            else:
                return None
        else:
            current_job_metadata = self.current_job_metadata or {}
            from_web = current_job_metadata.get("from_web_ui", False)
            is_headless = os.environ.get("QT_QPA_PLATFORM") == "offscreen" or (self.main_window and not self.main_window.isVisible())
            if from_web or is_headless:
                default_dir = os.path.expanduser("~/.local/share/ACCELA/downloads")
                os.makedirs(default_dir, exist_ok=True)
                return default_dir
            return QFileDialog.getExistingDirectory(
                self.main_window, "Select Destination Folder"
            )

    def _handle_slssteam_mode(self):
        # Deferred import
        from ui.dialogs.dlcselection import DlcSelectionDialog

        game_data = self.game_data
        if not game_data:
            return

        if sys.platform == "win32" and game_data.get("dlcs"):
            dlc_dialog = DlcSelectionDialog(game_data["dlcs"], self.main_window)
            if dlc_dialog.exec():
                game_data["selected_dlcs"] = dlc_dialog.get_selected_dlcs()

    def _start_download(self, selected_depots, dest_path):
        if not self.game_data:
            self.job_finished()
            return

        # Reset step tracker for this new download phase
        self._job_steps_completed.clear()

        self.current_dest_path = dest_path
        self.slssteam_mode_was_active = is_slssteam_mode_enabled()
        self.library_mode_was_active = self.settings.value(
            "library_mode", False, type=bool
        )
        self.is_cancelling = False

        # Determine if this is an update to a pre-existing installation
        self._pre_existing_install = False
        if dest_path and self.game_data:
            try:
                # Check for appmanifest file
                steamapps_dir = os.path.join(dest_path, "steamapps")
                acf_path = os.path.join(
                    steamapps_dir,
                    f"appmanifest_{self.game_data.get('appid', '')}.acf",
                )
                if os.path.exists(acf_path):
                    self._pre_existing_install = True
                else:
                    # Check if game folder exists and is non-empty
                    game_dir = get_game_directory(dest_path, self.game_data)
                    if os.path.isdir(game_dir):
                        with os.scandir(game_dir) as entries:
                            if any(entries):
                                self._pre_existing_install = True
            except Exception as e:
                logger.error(f"Error checking pre-existing installation status: {e}")

        self._last_steamless_success = None
        self._last_slscheevo_success = None
        self._last_slscheevo_message = ""
        self._steamless_ran = False
        self._steamless_error = False
        self._steamless_progress_log = []
        self._slscheevo_ran = False
        self._slscheevo_error = False
        self._slscheevo_completed = False
        self._waiting_for_achievements = False
        self._game_achievements_count = None

        # Reset per-job finalize guards
        self._current_active_step = None
        self._finalize_cancel_event.clear()

        self._download_start_time = time.time()
        self._download_end_time = 0.0
        self._last_download_duration = 0.0
        self._last_download_size = 0
        self._last_download_avg_speed = 0.0
        # Determine labels based on job type
        job_type = self.game_data.get("job_type", "download") if self.game_data else "download"
        action_verb = "Validating" if job_type == "verify" else "Downloading"
        action_noun = "Validating Game Files" if job_type == "verify" else "Downloading Game Files"

        self._last_ddm_status = "in_progress"
        self._last_ddm_status_text = f"{action_verb}..."
        self._last_slscheevo_status = "not_run"
        self._last_slscheevo_status_text = "N/A"
        self._last_steamless_status = "not_run"
        self._last_steamless_status_text = "N/A"

        logger.debug(f"{action_verb} started; GIF animation removed.")
        self._update_status_button_color()
        self.main_window.drop_text_label.setText(
            f"{action_verb}: {self.game_data.get('game_name', '')}"
        )

        self._init_simplified_stages(selected_depots)
        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            st = self.main_window.simplified_terminal
            if hasattr(st, "dl_text_2_0") and st.dl_text_2_0:
                st.dl_text_2_0.setText(action_noun)
            game_name = self.game_data.get("game_name", "Game")
            st.set_stage_status("download", "in_progress")
            st.show_active_job(game_name)

        self.main_window.progress_bar.setVisible(True)
        self.main_window.progress_bar.setValue(0)
        self.main_window.speed_label.setVisible(True)

        self.download_task = DownloadDepotsTask()
        self.download_task.progress.connect(logger.info)
        self.download_task.progress_percentage.connect(
            self.main_window.progress_bar.setValue,
            Qt.ConnectionType.QueuedConnection
        )
        self.download_task.speed_update.connect(
            self.main_window.speed_label.setText,
            Qt.ConnectionType.QueuedConnection
        )
        self.download_task.completed.connect(
            self._on_download_complete,
            Qt.ConnectionType.QueuedConnection
        )
        self.download_task.error.connect(
            self._handle_task_error,
            Qt.ConnectionType.QueuedConnection
        )

        self.download_runner = TaskRunner()
        self.is_awaiting_download_stop = True
        self.download_runner.cleanup_complete.connect(self._on_download_task_stopped)
        worker = self.download_runner.run(
            self.download_task.run, self.game_data, selected_depots, dest_path
        )
        worker.error.connect(self._handle_task_error)

        self._start_speed_monitor()
        self.is_download_paused = False
        self.main_window.ui_state.set_pause_button_text("Pause")
        self.main_window.ui_state.set_download_controls_visible(True)

        # Start achievement generation in parallel if enabled
        achievements_enabled = self.settings.value(
            "generate_achievements", False, type=bool
        )
        if achievements_enabled and not self.is_cancelling:
            self._start_achievement_generation()

        if not self.slssteam_mode_was_active:
            app_token = self.game_data.get("app_token")
            if app_token:
                game_dir = get_game_directory(dest_path, self.game_data)
                token_file = os.path.join(game_dir, "apptoken.txt")
                try:
                    os.makedirs(game_dir, exist_ok=True)
                    with open(token_file, "w") as f:
                        f.write(app_token)
                except OSError as e:
                    logger.error(f"Failed to write app token: {e}")

    def _start_speed_monitor(self):
        pass

    def _stop_speed_monitor(self):
        if self.main_window and hasattr(self.main_window, "speed_label") and self.main_window.speed_label:
            self.main_window.speed_label.setText("")
        self.is_awaiting_speed_monitor_stop = False

    def _on_speed_monitor_stopped(self):
        self.is_awaiting_speed_monitor_stop = False

    def _on_zip_task_stopped(self):
        self.zip_task_runner = None
        self.is_awaiting_zip_task_stop = False
        self.main_window.job_queue.check_if_safe_to_start_next_job()

    def _on_download_task_stopped(self):
        self.download_runner = None
        self.is_awaiting_download_stop = False
        self.main_window.job_queue.check_if_safe_to_start_next_job()

    def _on_workshop_task_stopped(self):
        self.workshop_runner = None
        self.is_awaiting_workshop_stop = False
        self.main_window.job_queue.check_if_safe_to_start_next_job()

    def _on_workshop_download_complete(self):
        if self.is_cancelling:
            self.job_finished()
            return
        self.main_window.progress_bar.setValue(100)
        self.job_finished()

    def start_workshop_download(self, workshop_data):
        self.is_processing = True
        display_name = workshop_data.get("display_name", "Workshop Items")
        self.current_job = display_name
        self.current_job_metadata = {"game_name": display_name}
        self.game_data = {"game_name": display_name, "appid": "Workshop"}
        self._job_steps_completed.clear()

        self._init_simplified_stages()
        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            st = self.main_window.simplified_terminal
            if hasattr(st, "dl_text_2_0") and st.dl_text_2_0:
                st.dl_text_2_0.setText(f"Downloading {display_name}")
            st.set_stage_status("download", "in_progress")
            st.show_active_job(display_name)

        self.main_window.progress_bar.setVisible(True)
        self.main_window.progress_bar.setValue(0)
        self.main_window.speed_label.setVisible(False)

        self.workshop_task = DownloadWorkshopTask()
        self.workshop_task.progress.connect(logger.info)
        self.workshop_task.progress_percentage.connect(
            self.main_window.progress_bar.setValue,
            Qt.ConnectionType.QueuedConnection
        )
        self.workshop_task.completed.connect(
            self._on_workshop_download_complete,
            Qt.ConnectionType.QueuedConnection
        )
        self.workshop_task.error.connect(
            self._handle_task_error,
            Qt.ConnectionType.QueuedConnection
        )

        self.workshop_runner = TaskRunner()
        self.is_awaiting_workshop_stop = True
        self.workshop_runner.cleanup_complete.connect(self._on_workshop_task_stopped)
        worker = self.workshop_runner.run(
            self.workshop_task.run, workshop_data
        )
        worker.error.connect(self._handle_task_error)

        self.is_download_paused = False
        self.main_window.ui_state.set_download_controls_visible(True)
        self.main_window.ui_state.set_pause_button_text("Pause")

    def _on_download_complete(self):
        """Handle download completion"""
        if self.is_cancelling:
            if self._delete_files_on_cancel:
                self._cleanup_cancelled_job_files()
            self.job_finished()
            return

        self._stop_speed_monitor()
        self.main_window.progress_bar.setValue(100)

        # Record download completion time and metrics
        self._download_end_time = time.time()
        duration = self._download_end_time - self._download_start_time
        if duration <= 0:
            duration = 0.1

        total_size = 0
        if self.download_task:
            total_size = self.download_task.total_download_size_for_this_job

        self._last_download_duration = duration
        self._last_download_size = total_size
        self._last_download_avg_speed = total_size / duration

        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.set_stage_status("download", "completed")

        if not self.game_data:
            if self.is_processing:
                self.job_finished()
            return

        self.main_window.drop_text_label.setText("Finalizing installation...")
        logger.info("Starting post-download I/O processing in background thread...")

        size_on_disk = 0
        if self.download_task:
            size_on_disk = self.download_task.total_download_size_for_this_job

        # Capture settings
        auto_apply_goldberg_val = self.settings.value(
            "auto_apply_goldberg", False, type=bool
        )
        config_management_enabled_val = False
        try:
            config_management_enabled_val = is_slssteam_config_management_enabled()
        except OSError as e:
            logger.error(f"Error checking config management status: {e}")

        # Signal the finalize thread to abort if it's still running
        self._finalize_cancel_event.clear()
        # Start the worker thread
        threading.Thread(
            target=self._run_finalize_io_worker,
            args=(size_on_disk, auto_apply_goldberg_val, config_management_enabled_val),
            daemon=True,
            name="FinalizeIOWorker",
        ).start()

    def _run_finalize_io_worker(
        self, size_on_disk: int, auto_apply_goldberg: bool, config_enabled: bool
    ):
        """Background thread worker for post-download I/O.

        Checks _finalize_cancel_event at each major step so the thread can exit
        cleanly when the user cancels before finalization completes.
        """
        try:
            if self._finalize_cancel_event.is_set() or self.is_cancelling:
                return
            self._finalize_acf_and_manifests(size_on_disk)

            if self._finalize_cancel_event.is_set() or self.is_cancelling:
                return
            self._persist_wrapper_metadata()

            if self._finalize_cancel_event.is_set() or self.is_cancelling:
                return
            self._finalize_platform_specifics(config_enabled)

            if self._finalize_cancel_event.is_set() or self.is_cancelling:
                return
            self._finalize_goldberg(auto_apply_goldberg)

            if self._finalize_cancel_event.is_set() or self.is_cancelling:
                return
            self._finalize_greenluma(config_enabled)

        except Exception as e:
            logger.error(
                f"Critical error in post-processing thread: {e}", exc_info=True
            )
        finally:
            # Only invoke the main-thread slot if we weren't cancelled mid-way
            if not self._finalize_cancel_event.is_set():
                QMetaObject.invokeMethod(
                    self, "_finalize_job_logic", Qt.ConnectionType.QueuedConnection
                )

    def _finalize_acf_and_manifests(self, size_on_disk: int):
        # 1. Manifests → Steam's central depotcache FIRST, so they are in place
        #    before the SLS install|appid|index command fires. Without this,
        #    Steam has no depot fingerprints to verify local files against and
        #    falls back to "needs download" (blue Install button instead of Play).
        self._move_manifests_to_depotcache()

        # 2. Seed DDM delta cache (.DepotDownloader/ folder with .sha sidecars)
        #    This lets DepotDownloaderMod use the installed manifest as the
        #    "old" manifest on the next update, enabling true delta downloads.
        self._seed_ddm_delta_cache()

        # 3. ACF / SLS install trigger (must be last — depotcache must be ready)
        self._create_acf_file(size_on_disk)

        # 4. Depot Info
        selected_depots = self.game_data.get("selected_depots_list", [])
        all_manifests = self.game_data.get("manifests", {})
        if selected_depots and all_manifests:
            self._save_main_depot_info(self.game_data, selected_depots, all_manifests)

    def _persist_wrapper_metadata(self):
        """
        Persist wrapper metadata in the game's .DepotDownloader folder.
        Stores selected DLC IDs so uninstall can clean up AppList entries later.
        """
        if sys.platform != "win32":
            return

        if not self.game_data or not self.current_dest_path:
            return

        game_directory = get_game_directory(self.current_dest_path, self.game_data)
        selected_dlcs: List[str] = self.game_data.get("selected_dlcs") or []

        if persist_selected_dlcs(game_directory, selected_dlcs):
            appid = self.game_data.get("appid", "unknown")
            logger.debug(
                f"Persisted wrapper metadata for AppID {appid} with {len(selected_dlcs)} DLC ID(s)"
            )

    def _finalize_platform_specifics(self, config_enabled: bool):
        # 4. Linux Permissions
        if sys.platform != "linux":
            return

        self._set_linux_binary_permissions()
        if self.slssteam_mode_was_active and config_enabled:
            self._add_appids_to_slssteam_config()

    def _finalize_goldberg(self, auto_apply: bool):
        # 5. Goldberg
        if not (auto_apply and not self.is_cancelling and self.current_dest_path):
            return

        game_dir = get_game_directory(self.current_dest_path, self.game_data)

        try:
            self.apply_goldberg_to_game(
                game_directory=game_dir,
                appid=str(self.game_data.get("appid", "")),
                game_name=self.game_data.get("game_name", ""),
                show_dialog=False,
            )
        except OSError as e:
            logger.error(f"Error applying Goldberg: {e}")

    def _finalize_greenluma(self, config_enabled: bool):
        # 6. GreenLuma Files (Win32)
        if not (self.slssteam_mode_was_active and sys.platform == "win32"):
            return

        try:
            logger.info("Looking for Steam installation...")
            steam_path = steam_helpers.find_steam_install()
            if steam_path:
                logger.info(
                    f"Steam found at {steam_path}. Checking GreenLuma config..."
                )
                self._create_greenluma_applist_files(
                    steam_path, config_enabled=config_enabled
                )
                self._copy_greenluma_bin_files(
                    steam_path, config_enabled=config_enabled
                )
                logger.info("GreenLuma configuration check complete.")
            else:
                logger.warning(
                    "Steam installation not found, skipping GreenLuma config."
                )
        except OSError as e:
            logger.error(f"GreenLuma configuration failed: {e}", exc_info=True)

    @pyqtSlot()
    def _finalize_job_logic(self):
        """Called on Main Thread. Acts as a State Machine Conductor.

        Guard against being invoked more than once per step or when no job is active.
        """
        if not self.is_processing:
            logger.warning("_finalize_job_logic called when no job is processing — ignoring duplicate callback")
            return

        if self._current_active_step is not None:
            logger.warning(f"_finalize_job_logic called while step '{self._current_active_step}' is still running — ignoring duplicate callback")
            return

        if self._should_prompt_for_steam_restart():
            self.main_window.job_queue.steam_restart_prompt_pending = True

        steamless_enabled = self.settings.value("use_steamless", False, type=bool)
        steamless_aio_enabled = self.settings.value("use_steamless_aio", False, type=bool)
        
        # Skip Steamless entirely if this is a DLC Only installation
        appid = self.game_data.get("appid") if self.game_data else None
        is_dlc_only = False
        if appid:
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc_only = is_dlc_only_mode(str(appid))

        if (steamless_enabled or steamless_aio_enabled) and not self.is_cancelling and not self._is_current_job_linux() and not is_dlc_only:
            if "steamless" not in self._job_steps_completed:
                self._job_steps_completed.add("steamless")
                self._current_active_step = "steamless"
                self.main_window.drop_text_label.setText(
                    f"Running Steamless: {self.game_data.get('game_name', '')}"
                )
                self._start_steamless_processing(use_aio=steamless_aio_enabled)
                return

        achievements_enabled = self.settings.value(
            "generate_achievements", False, type=bool
        )
        if achievements_enabled and not self.is_cancelling:
            if "achievements" not in self._job_steps_completed:
                if not self._slscheevo_completed:
                    logger.info("Waiting for parallel Steam achievement generation to complete...")
                    if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
                        self.main_window.simplified_terminal.set_stage_status("achievements", "in_progress", getattr(self, "_game_achievements_count", None))
                    self.main_window.drop_text_label.setText(
                        f"Waiting for achievements: {self.game_data.get('game_name', '')}"
                    )
                    self._current_active_step = "achievements"
                    self._waiting_for_achievements = True
                    return
                else:
                    self._job_steps_completed.add("achievements")

        # --- FINISH ---
        logger.info("All post-processing steps complete. Finishing job.")
        self.main_window.job_queue.jobs_completed_count += 1
        if not self.is_cancelling:
            # Clear the cached update status for this game so it gets re-checked
            # (it may have gone from "update_available" to "up_to_date")
            if self.game_data:
                from utils.update_status_cache import get_update_cache
                appid = self.game_data.get("appid", "")
                if appid and appid not in ("0", "N/A", "unknown"):
                    # Set status to up_to_date so the post-download rescan restores
                    # the correct status immediately. Without this, the game would stay
                    # at "checking" indefinitely because _on_initial_scan_complete
                    # (the only slot that calls check_game_updates_async) disconnects
                    # itself after boot and never runs again for subsequent rescans.
                    # Skip for rollback installs — user chose an older build, so keep
                    # the game showing "update_available".
                    if not self.game_data.get("_is_rollback"):
                        cache = get_update_cache()
                        cache.set_status(appid, "up_to_date")
                        cache.save_async()
                        logger.debug(f"Set update cache to up_to_date for freshly installed appid={appid}")
                    else:
                        logger.debug(f"Rollback install for appid={appid} — keeping current update status")

                    # Upsert the new manifest/depot data to SQLite DB to refresh cache age and content
                    try:
                        from managers.db_manager import DatabaseManager
                        from utils.settings import get_settings
                        db = DatabaseManager()
                        new_bid = self.game_data.get("buildid")
                        db_data = {
                            "appid": appid,
                            "name": self.game_data.get("game_name"),
                            "installdir": self.game_data.get("installdir"),
                            "header_url": self.game_data.get("header_url"),
                            "buildid": new_bid,
                            "depots": self.game_data.get("depots", {}),
                        }
                        db.upsert_app_info(appid, db_data)
                        logger.debug(f"Upserted updated app/manifest info to SQLite DB for appid={appid}")

                        if appid:
                            settings = get_settings()
                            # Priority for which branch to stamp as installed:
                            #  1. job_metadata["branch"] — explicitly set by fetchmanifest /
                            #     SmartUpdateTask / game details update button. Most authoritative.
                            #  2. selected_branch/{appid} from QSettings — what the user chose
                            #     in the UI combo/dialog for THIS operation. Beats game_data
                            #     because game_data["branch"] can carry a stale value from a
                            #     previous install on a different branch.
                            #  3. game_data["branch"] — derived from ProcessZipTask filename
                            #     parsing or PICS lookup. Last resort only.
                            #  4. "public" — safe default.
                            job_meta_branch = (self.current_job_metadata or {}).get("branch")
                            qsettings_branch = settings.value(f"selected_branch/{appid}", "", type=str)
                            game_data_branch = (self.game_data or {}).get("branch")
                            sel_b = job_meta_branch or qsettings_branch or game_data_branch or "public"
                            logger.info(
                                f"Branch stamp for {appid}: "
                                f"job_meta={job_meta_branch!r}, "
                                f"qsettings={qsettings_branch!r}, "
                                f"game_data={game_data_branch!r} "
                                f"→ selected='{sel_b}'"
                            )
                            settings.setValue(f"selected_branch/{appid}", sel_b)
                            settings.setValue(f"installed_branch/{appid}", sel_b)
                            b_dict = getattr(self, "game_data", {}).get("branches", {}) if hasattr(self, "game_data") else {}
                            target_bid = ""
                            if isinstance(b_dict, dict) and sel_b in b_dict:
                                b_entry = b_dict[sel_b]
                                if isinstance(b_entry, dict):
                                    target_bid = str(b_entry.get("buildid", ""))

                            is_rollback_job = (self.current_job_metadata or {}).get("is_rollback") or (self.game_data.get("_is_rollback") if self.game_data else False)
                            meta_bid = (self.current_job_metadata or {}).get("buildid")
                            if is_rollback_job:
                                final_bid = meta_bid or new_bid
                                logger.info(f"[DEBUG_DEV] Rollback/manual install detected. Using manual build ID as final_bid: {final_bid}")
                            else:
                                final_bid = meta_bid or target_bid or new_bid
                                logger.info(f"[DEBUG_DEV] Standard install completed. final_bid: {final_bid}")
                            if final_bid:
                                # Store per-branch: installed_buildid/appid/branch
                                settings.setValue(f"installed_buildid/{appid}/{sel_b}", str(final_bid))
                                # Also store legacy flat key for backward compat
                                settings.setValue(f"installed_buildid/{appid}", str(final_bid))

                                # If pin_build is specified in job metadata, apply user choice
                                if self.current_job_metadata and "pin_build" in self.current_job_metadata:
                                    should_pin = bool(self.current_job_metadata["pin_build"])
                                    settings.setValue(f"pin_build/{appid}", should_pin)
                                    if should_pin:
                                        settings.setValue(f"exclude_from_update_all/{appid}", False)
                                        try:
                                            import shutil
                                            manifests_dir = Path(get_base_path()) / "hubcap_manifests"
                                            manifests_dir.mkdir(parents=True, exist_ok=True)
                                            dest_zip = manifests_dir / f"accela_fetch_{appid}_build_{final_bid}.zip"
                                            if self.current_job and os.path.exists(self.current_job):
                                                shutil.copy(self.current_job, dest_zip)
                                                logger.info(f"Cached pinned manifest zip to {dest_zip}")
                                        except Exception as e:
                                            logger.warning(f"Failed to cache pinned manifest zip: {e}")
                    except Exception as e:
                        logger.error(f"Failed to upsert app info on job completion: {e}")

            self.main_window.game_manager.scan_steam_libraries_async()

            # Refresh title in the open game-details dialog immediately so the
            # branch suffix (e.g. "(beta)") updates live without a close/reopen.
            try:
                details_dlg = getattr(self.main_window, "_details_dialog", None)
                if details_dlg and str(getattr(details_dlg, "appid", "")) == str(appid):
                    details_dlg.update_title()
            except Exception as _dt_err:
                logger.debug(f"Could not refresh details dialog title after install: {_dt_err}")

        self.job_finished()

    def _should_prompt_for_steam_restart(self) -> bool:
        if self.is_cancelling:
            return False

        return self.slssteam_mode_was_active or self.library_mode_was_active

    @staticmethod
    def _save_main_depot_info(game_data, selected_depots, all_manifests):
        try:
            appid = game_data.get("appid")
            if not appid or not selected_depots:
                return

            depots_dir = Path(get_base_path()) / "depots"
            depots_dir.mkdir(parents=True, exist_ok=True)
            depot_file = depots_dir / f"{appid}.depot"
            access_token = game_data.get("app_token", "")

            # Read existing entries to support multiple DLCs/depots
            existing_entries = {}
            if depot_file.exists():
                try:
                    for line in depot_file.read_text().splitlines():
                        parts = [p.strip() for p in line.split(":")]
                        if len(parts) >= 2:
                            existing_entries[parts[0]] = line
                except Exception:
                    pass

            # Add or update entries for ALL selected depots
            for depot_id_raw in selected_depots:
                depot_id = str(depot_id_raw)
                manifest_id = all_manifests.get(depot_id)
                if not manifest_id:
                    continue
                if access_token:
                    existing_entries[depot_id] = f"{depot_id}: {manifest_id}: {access_token}"
                else:
                    existing_entries[depot_id] = f"{depot_id}: {manifest_id}"

            with open(depot_file, "w") as f:
                for entry_line in existing_entries.values():
                    f.write(entry_line + "\n")
        except OSError as e:
            logger.error(f"Failed to save depot info: {e}")

    def _create_acf_file(self, size_on_disk):
        if not self.game_data or not self.current_dest_path:
            return

        appid = self.game_data.get("appid")

        # 1. Always write/update our local metadata.json fallback
        try:
            from utils.assella_metadata import write_accela_metadata
            write_accela_metadata(self.current_dest_path, self.game_data, size_on_disk)
        except Exception as e:
            logger.error(f"Failed to write metadata JSON file: {e}")

        # 2. If ACF-Independent mode is active, delegate manifest creation entirely to Steam natively.
        #    Exception: pinned/older builds must use the fallback ACF writer so the pinned buildid
        #    is preserved — the SLS pipe triggers Steam to fetch the latest PICS data, which would
        #    overwrite the pinned buildid and potentially queue an unwanted auto-update.
        try:
            from utils.slssteam_integration import (
                install_via_sls,
                _experimental_mode_enabled,
                _is_slssteam_available,
                warn_sls_unavailable,
            )
            if _experimental_mode_enabled():
                is_pinned = False
                if appid and appid not in ("0", "N/A", "unknown"):
                    try:
                        from utils.settings import get_settings as _gs
                        is_pinned = _gs().value(f"pin_build/{appid}", False, type=bool)
                    except Exception:
                        pass

                if is_pinned:
                    logger.info(
                        f"ACF-Independent Mode active but build is pinned for {appid} — "
                        "using fallback ACF writer to preserve pinned buildid"
                    )
                    # Fall through to step 3 (write_acf_file with pinned buildid)
                else:
                    logger.info("ACF-Independent Mode is active. Delegating manifest creation to Steam natively.")

                    # Precondition check: warn the user if SLSsteam is not running
                    if not _is_slssteam_available():
                        warning_msg = warn_sls_unavailable(context="post-install")
                        # Emit as a visible warning in the task progress output
                        self.progress.emit(f"⚠️ WARNING: {warning_msg}")

                    job_type = self.game_data.get("job_type", "download") if self.game_data else "download"
                    if job_type == "verify":
                        logger.info("Skipping SLS install API call for verify job — ACF already exists.")
                        return
                    if appid and appid not in ("0", "N/A", "unknown"):
                        install_via_sls(
                            appid=str(appid),
                            game_name=self.game_data.get("game_name", ""),
                            library_path=self.current_dest_path or "",
                        )
                    return
        except Exception as e:
            logger.error(f"Error in SLSsteam install flow for {appid}: {e}")


        # 3. Fallback: Write standard Steam .acf manifest file when experimental mode is disabled
        #    or when the build is pinned (to lock the buildid/InstalledDepots).
        if appid:
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc_only = is_dlc_only_mode(str(appid))
            if is_dlc_only:
                logger.info("DLC Only mode active. Skipping base game .acf manifest generation.")
                return

        try:
            write_acf_file(
                self.current_dest_path,
                self.game_data,
                size_on_disk,
                include_depots=sys.platform == "win32",
            )
            logger.info(f"Generated .acf manifest file for {appid}")
        except Exception as e:
            logger.error(f"Error creating .acf file for {appid}: {e}")

    def _move_manifests_to_depotcache(self):
        if not self.game_data or not self.current_dest_path:
            return

        temp_manifest_dir = os.path.join(tempfile.gettempdir(), "mistwalker_manifests")
        if not os.path.exists(temp_manifest_dir):
            return

        target_depotcache_dir = os.path.join(self.current_dest_path, "depotcache")

        # Copy to Steam's central depotcache if Let SLS handle ACF (experimental_acf_independent) is enabled
        try:
            from utils.settings import get_settings
            settings = get_settings()
            experimental_mode = settings.value("experimental_acf_independent", False, type=bool)
        except Exception:
            experimental_mode = False

        central_depotcache_dir = None
        if experimental_mode:
            from core.steam_helpers import find_steam_install
            steam_path = find_steam_install()
            if steam_path:
                central_depotcache_dir = os.path.join(steam_path, "depotcache")
                try:
                    os.makedirs(central_depotcache_dir, exist_ok=True)
                except Exception as e:
                    logger.error(f"Failed to create central depotcache directory: {e}")
                    central_depotcache_dir = None

        try:
            os.makedirs(target_depotcache_dir, exist_ok=True)
            manifests_map = self.game_data.get("manifests", {})
            if not manifests_map:
                shutil.rmtree(temp_manifest_dir)
                return

            for depot_id, manifest_gid in manifests_map.items():
                manifest_filename = f"{depot_id}_{manifest_gid}.manifest"
                source_path = os.path.join(temp_manifest_dir, manifest_filename)
                dest_path = os.path.join(target_depotcache_dir, manifest_filename)
                if os.path.exists(source_path):
                    if central_depotcache_dir:
                        try:
                            shutil.copy2(source_path, os.path.join(central_depotcache_dir, manifest_filename))
                            logger.info(f"Copied manifest {manifest_filename} to Steam's central depotcache")
                        except Exception as e:
                            logger.error(f"Failed to copy manifest to central depotcache: {e}")
                    shutil.move(source_path, dest_path)

            shutil.rmtree(temp_manifest_dir)
        except OSError as e:
            logger.error(f"Failed to move manifests to depotcache: {e}")

    def _seed_ddm_delta_cache(self):
        """
        Copy the newly installed manifest files into the game's .DepotDownloader/
        hidden folder and write .sha sidecar files alongside each one.

        DepotDownloaderMod-patched reads this folder to find the "old" manifest
        from the previously installed build. With this in place, the next update
        triggers a proper incremental delta download — only changed chunks are
        fetched instead of re-validating every file from scratch.

        File layout expected by DDM:
          {install_dir}/.DepotDownloader/{depotId}_{manifestId}.manifest
          {install_dir}/.DepotDownloader/{depotId}_{manifestId}.manifest.sha
        """
        import hashlib

        if not self.game_data or not self.current_dest_path:
            return

        manifests_map = self.game_data.get("manifests", {})
        if not manifests_map:
            return

        # Source: depotcache/ (manifests were just moved there)
        depotcache_dir = os.path.join(self.current_dest_path, "depotcache")
        # Derive the game install dir (steamapps/common/{installdir})
        from utils.steam_manifest import get_install_folder_name
        install_folder = get_install_folder_name(self.game_data)
        game_install_dir = os.path.join(
            self.current_dest_path, "steamapps", "common", install_folder
        )
        ddm_dir = os.path.join(game_install_dir, ".DepotDownloader")

        try:
            os.makedirs(ddm_dir, exist_ok=True)
        except OSError as e:
            logger.warning(f"Could not create .DepotDownloader dir for delta cache: {e}")
            return

        seeded = 0
        for depot_id, manifest_gid in manifests_map.items():
            manifest_filename = f"{depot_id}_{manifest_gid}.manifest"
            src = os.path.join(depotcache_dir, manifest_filename)
            if not os.path.exists(src):
                logger.debug(f"Delta cache: manifest not found in depotcache, skipping: {manifest_filename}")
                continue

            dst = os.path.join(ddm_dir, manifest_filename)
            sha_dst = dst + ".sha"
            try:
                shutil.copy2(src, dst)
                # Compute SHA1 of the manifest file — DDM validates this sidecar
                # to confirm the cached manifest hasn't been corrupted.
                sha1 = hashlib.sha1()
                with open(dst, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        sha1.update(chunk)
                with open(sha_dst, "wb") as f:
                    f.write(sha1.digest())  # raw bytes, not hex — matches DDM's FileSHAHash()
                seeded += 1
                logger.debug(f"Delta cache seeded: {manifest_filename} → .DepotDownloader/")
            except OSError as e:
                logger.warning(f"Failed to seed delta cache for {manifest_filename}: {e}")

        if seeded:
            logger.info(
                f"DDM delta cache seeded for {self.game_data.get('game_name', '?')} "
                f"({seeded} depot manifest(s)). Next update will use incremental delta download."
            )

    def _set_linux_binary_permissions(self):
        if not self.game_data or not self.current_dest_path:
            return

        game_directory = get_game_directory(self.current_dest_path, self.game_data)

        if os.path.exists(game_directory):
            self._run_chmod_recursive(game_directory)

    def _create_steamless_task(self, progress_handler):
        self.steamless_task = SteamlessTask()
        self.steamless_task.use_aio = True
        self.steamless_task.progress.connect(progress_handler)
        self.steamless_task.result.connect(self._on_steamless_complete)
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
        if not self.current_dest_path or not self.game_data:
            self._finalize_job_logic()
            return

        game_directory = get_game_directory(self.current_dest_path, self.game_data)

        if not os.path.exists(game_directory):
            self._finalize_job_logic()
            return

        self._reset_steamless_task()

        logger.info("\n" + "=" * 40)
        logger.info("Starting Steamless DRM Removal...")

        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.set_stage_status("steamless", "in_progress")

        steamless_task = self._create_steamless_task(self._on_steamless_progress)
        steamless_task.use_aio = True
        steamless_task.set_game_directory(game_directory)
        steamless_task.start()

        self._steamless_ran = True
        self._update_status_button_color()

    def run_steamless_manually(self, exe_path: str, game_name: Optional[str] = None):
        self._reset_steamless_task()

        self._steamless_game_name = game_name or os.path.basename(exe_path)
        self._steamless_progress_log = []
        self._steamless_manual_run = True

        logger.info(f"Starting manual Steamless (.NET CLI) processing for: {exe_path}")
        steamless_task = self._create_steamless_task(self._on_steamless_progress)
        steamless_task.use_aio = False
        steamless_task.set_target_exe(exe_path)
        steamless_task.start()

    def run_steamless_aio_manually(self, exe_path: str, game_name: Optional[str] = None):
        self._reset_steamless_task()

        self._steamless_game_name = game_name or os.path.basename(exe_path)
        self._steamless_progress_log = []
        self._steamless_manual_run = True

        logger.info(f"Starting manual Steamless (Python AIO) processing for: {exe_path}")
        steamless_task = self._create_steamless_task(self._on_steamless_progress)
        steamless_task.use_aio = True
        steamless_task.set_target_exe(exe_path)
        steamless_task.start()

    def run_steamless_for_game(self, game_directory: str, game_name: str):
        self._reset_steamless_task()

        self._steamless_game_name = game_name
        self._steamless_progress_log = []
        self._steamless_manual_run = True

        logger.info(f"Starting manual Steamless (.NET CLI) processing for game: {game_name}")
        steamless_task = self._create_steamless_task(self._on_steamless_progress)
        steamless_task.use_aio = False
        steamless_task.set_game_directory(game_directory)
        steamless_task.start()

    def run_steamless_aio_for_game(self, game_directory: str, game_name: str):
        self._reset_steamless_task()

        self._steamless_game_name = game_name
        self._steamless_progress_log = []
        self._steamless_manual_run = True

        logger.info(f"Starting manual Steamless AIO processing for game: {game_name}")
        steamless_task = self._create_steamless_task(self._on_steamless_progress)
        steamless_task.use_aio = True
        steamless_task.set_game_directory(game_directory)
        steamless_task.start()

    def run_chmod_for_game(
        self, game_directory: str, game_name: str, show_dialog: bool = False
    ):
        logger.info(f"Starting chmod for game: {game_name}")

        def chmod_worker():
            count = self._run_chmod_recursive(game_directory)
            logger.info(f"Chmod completed: {count} files processed")

            if show_dialog:
                # Deferred import
                from ui.dialogs.chmod_resume import ChmodResumeDialog

                def show():
                    self._show_chmod_resume_dialog(game_name, count, ChmodResumeDialog)

                QTimer.singleShot(0, show)

        threading.Thread(target=chmod_worker, daemon=True).start()

    def _ensure_game_directory(self, game_directory: str, show_dialog: bool) -> bool:
        if game_directory and os.path.exists(game_directory):
            return True
        if show_dialog:
            QMessageBox.warning(
                self.main_window,
                "Directory Not Found",
                f"Game directory not found: {game_directory}",
            )
        return False

    @staticmethod
    def _detect_elf_architecture(file_path: str) -> Optional[str]:
        """
        Detect if an ELF file is 32-bit or 64-bit.
        Returns '32', '64', or None if not detectable.
        """
        try:
            with open(file_path, "rb") as f:
                # ELF magic bytes
                magic = f.read(4)
                if magic != b"\x7fELF":
                    return None

                # Byte 4 = class (1 = 32-bit, 2 = 64-bit)
                elf_class = f.read(1)
                if elf_class == b"\x01":
                    return "32"
                elif elf_class == b"\x02":
                    return "64"
        except (IOError, OSError):
            pass
        return None

    def apply_goldberg_to_game(
        self, game_directory: str, appid: str, game_name: str, show_dialog: bool = True
    ) -> bool:
        """Apply Goldberg emulator to a game directory."""
        logger.info(f"Applying Goldberg for game: {game_name} (AppID: {appid})")

        if not self._ensure_game_directory(game_directory, show_dialog):
            return False

        target_dirs = self._find_steam_api_dirs(game_directory)
        if not target_dirs:
            if show_dialog:
                QMessageBox.information(
                    self.main_window,
                    "No Steam API Files Found",
                    "No steam_api.dll, steam_api64.dll, libsteam_api.so or libsteam_api64.so files were found.",
                )
            return False

        goldberg_src = Paths.deps("Goldberg")
        if not goldberg_src.exists():
            if show_dialog:
                QMessageBox.critical(
                    self.main_window,
                    "Source Missing",
                    f"Goldberg folder not found: {goldberg_src}",
                )
            return False

        processed = 0
        for target_dir in target_dirs:
            try:
                self._apply_goldberg_to_single_dir(target_dir, appid, goldberg_src)
                processed += 1
            except Exception as e:
                logger.error(f"Failed to apply Goldberg in {target_dir}: {e}")

        if show_dialog:
            QMessageBox.information(
                self.main_window,
                "Apply Goldberg",
                f"Applied Goldberg files to {processed} folder(s).",
            )
        return True

    def remove_goldberg_from_game(
        self, game_directory: str, appid: str, game_name: str, show_dialog: bool = True
    ) -> bool:
        """Remove Goldberg emulator from a game directory."""
        logger.info(f"Removing Goldberg for game: {game_name}")

        if not self._ensure_game_directory(game_directory, show_dialog):
            return False

        target_dirs = self._find_steam_api_backup_dirs(game_directory)
        if not target_dirs:
            if show_dialog:
                QMessageBox.information(
                    self.main_window,
                    "No Backups Found",
                    "No .valve backup files were found.",
                )
            return False

        processed = 0
        for target_dir in target_dirs:
            try:
                self._remove_goldberg_from_single_dir(target_dir)
                processed += 1
            except Exception as e:
                logger.error(f"Failed to remove Goldberg from {target_dir}: {e}")

        if show_dialog:
            QMessageBox.information(
                self.main_window,
                "Remove Goldberg",
                f"Restored originals in {processed} folder(s).",
            )
        return True

    def _find_steam_api_dirs(self, root_dir: str) -> Set[str]:
        """Return set of directories containing any steam_api file."""
        targets = set()
        steam_api_names = {
            "steam_api.dll",
            "steam_api64.dll",
            "libsteam_api.so",
            "libsteam_api64.so",
        }
        for root, _, files in os.walk(root_dir):
            if any(fname.lower() in steam_api_names for fname in files):
                targets.add(root)
        return targets

    def _find_steam_api_backup_dirs(self, root_dir: str) -> Set[str]:
        """Return set of directories containing .valve backup files."""
        targets = set()
        backup_suffixes = (".dll.valve", ".so.valve")
        for root, _, files in os.walk(root_dir):
            if any(fname.lower().endswith(backup_suffixes) for fname in files):
                targets.add(root)
        return targets

    @staticmethod
    def _safe_remove(file_path: str) -> bool:
        """Remove a file safely, making it writable first if read-only."""
        import stat
        if not os.path.exists(file_path):
            return True
        try:
            os.chmod(file_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        except Exception:
            pass
        try:
            os.remove(file_path)
            return True
        except Exception as e:
            logger.warning(f"Could not remove file {file_path}: {e}")
            return False

    @staticmethod
    def _safe_rmtree(dir_path: str) -> bool:
        """Safely remove a directory tree, fixing read-only permissions if necessary."""
        import stat

        def _remove_readonly(func, p, excinfo):
            try:
                os.chmod(os.path.dirname(p), stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
            except Exception:
                pass
            try:
                os.chmod(p, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
            except Exception:
                pass
            try:
                func(p)
            except Exception as e:
                logger.debug(f"_safe_rmtree handler failed on {p}: {e}")

        if not os.path.exists(dir_path):
            return True

        # Pre-emptively make tree writable
        try:
            for root, dirs, files in os.walk(dir_path):
                for d in dirs:
                    try:
                        os.chmod(os.path.join(root, d), stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
                    except Exception:
                        pass
                for f in files:
                    try:
                        os.chmod(os.path.join(root, f), stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
                    except Exception:
                        pass
            os.chmod(dir_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        except Exception:
            pass

        try:
            try:
                shutil.rmtree(dir_path, onexc=_remove_readonly)
            except TypeError:
                shutil.rmtree(dir_path, onerror=_remove_readonly)
            return True
        except Exception as e:
            logger.error(f"Failed to remove directory {dir_path}: {e}")
            return False

    def _apply_goldberg_to_single_dir(
        self, target_dir: str, appid: str, goldberg_src: Path
    ):
        """Apply Goldberg to one directory."""
        renamed_files = self._backup_steam_api_files(target_dir)
        self._copy_goldberg_matching_files(target_dir, goldberg_src, renamed_files)
        self._copy_goldberg_common_files(target_dir, goldberg_src)
        self._write_appid_file(target_dir, appid)
        self._generate_interfaces_for_valve_files(target_dir, goldberg_src)

    def _backup_steam_api_files(self, directory: str) -> List[str]:
        """Rename all steam_api files to .valve. Return list of original filenames."""
        renamed = []
        patterns = [
            "steam_api.dll",
            "steam_api64.dll",
            "libsteam_api.so",
            "libsteam_api64.so",
        ]
        for name in patterns:
            src = os.path.join(directory, name)
            if os.path.exists(src):
                dst = src + ".valve"
                if not os.path.exists(dst):
                    try:
                        os.rename(src, dst)
                        renamed.append(name)
                    except Exception as e:
                        logger.error(f"Failed to rename {src} to {dst}: {e}")
                else:
                    # .valve backup already exists (e.g. re-applying Goldberg)
                    renamed.append(name)
        return renamed

    def _copy_goldberg_matching_files(
        self, target_dir: str, goldberg_src: Path, renamed_files: List[str]
    ):
        """For each renamed file, copy the Goldberg replacement from the goldberg deps folder."""
        import stat
        for name in renamed_files:
            dst_file = os.path.join(target_dir, name)
            if os.path.exists(dst_file):
                try:
                    os.chmod(dst_file, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
                except Exception:
                    pass

            if name.endswith(".dll"):
                src = goldberg_src / "windows" / name
                if src.exists():
                    shutil.copy2(str(src), dst_file)
                    logger.info(f"Copied Goldberg {name} to {target_dir}")
                else:
                    logger.warning(f"Goldberg file not found: {src}")

            elif name.endswith(".so"):
                backup_path = os.path.join(target_dir, name + ".valve")
                if not os.path.exists(backup_path):
                    logger.error(f"Backup file missing: {backup_path}")
                    continue

                arch = self._detect_elf_architecture(backup_path)
                if arch is None:
                    logger.warning(
                        f"Could not detect architecture of {backup_path}, skipping"
                    )
                    continue

                if arch == "32":
                    src_filename = "libsteam_api.so"
                elif arch == "64":
                    src_filename = "libsteam_api64.so"
                else:
                    logger.warning(f"Unknown architecture '{arch}' for {backup_path}")
                    continue

                src = goldberg_src / "linux" / src_filename
                if src.exists():
                    shutil.copy2(str(src), dst_file)
                    logger.info(
                        f"Copied {src_filename} (detected {arch}-bit) to {name}"
                    )
                else:
                    logger.error(
                        f"Goldberg file not found: {src} (needed for {arch}-bit)"
                    )
            else:
                continue

    def _copy_goldberg_common_files(self, target_dir: str, goldberg_src: Path):
        """Copy steam_settings folder."""
        src_settings = goldberg_src / "steam_settings"
        if src_settings.exists():
            dst_settings = os.path.join(target_dir, "steam_settings")
            if os.path.exists(dst_settings):
                self._safe_rmtree(dst_settings)
            try:
                shutil.copytree(str(src_settings), dst_settings)
            except Exception as e:
                logger.error(f"Failed to copy steam_settings to {dst_settings}: {e}")

    def _write_appid_file(self, target_dir: str, appid: str):
        """Write steam_appid.txt"""
        import stat
        appid_path = os.path.join(target_dir, "steam_appid.txt")
        if os.path.exists(appid_path):
            try:
                os.chmod(appid_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
            except Exception:
                pass
        try:
            with open(appid_path, "w", encoding="utf-8") as f:
                f.write(str(appid))
        except OSError as e:
            logger.warning(f"Failed to write steam_appid.txt: {e}")

    def _generate_interfaces_for_valve_files(self, target_dir: str, goldberg_src: Path):
        """
        For each .valve file that is a steam_api backup, run generate_interfaces
        and move the resulting steam_interfaces.txt to steam_settings.
        """
        steam_settings_dir = os.path.join(target_dir, "steam_settings")
        for fname in os.listdir(target_dir):
            if not fname.lower().endswith((".dll.valve", ".so.valve")):
                continue
            if "steam_api" not in fname.lower():
                continue

            # Determine bitness from filename
            is_64bit = "64" in fname
            valve_path = os.path.join(target_dir, fname)
            if self._run_generate_interfaces_for_file(
                target_dir, valve_path, is_64bit, goldberg_src
            ):
                # Move generated interfaces file if it exists
                interfaces_src = os.path.join(target_dir, "steam_interfaces.txt")
                if os.path.exists(interfaces_src):
                    os.makedirs(steam_settings_dir, exist_ok=True)
                    interfaces_dst = os.path.join(
                        steam_settings_dir, "steam_interfaces.txt"
                    )
                    if os.path.exists(interfaces_dst):
                        self._safe_remove(interfaces_dst)
                    shutil.move(interfaces_src, interfaces_dst)
                    logger.info(f"Moved steam_interfaces.txt to {steam_settings_dir}")

    @staticmethod
    def _run_generate_interfaces_for_file(
        game_directory: str,
        valve_file_path: str,
        is_64bit: bool,
        goldberg_src: Optional[Path] = None,
    ) -> bool:
        """Run generate_interfaces for one .valve file. Return True on success."""
        if goldberg_src is None:
            goldberg_src = Paths.deps("Goldberg")
        is_windows = sys.platform == "win32"
        exe_name = f"generate_interfaces_x{'64' if is_64bit else '32'}"
        if is_windows:
            exe_name += ".exe"

        exe_path = goldberg_src / "genints" / exe_name
        if not exe_path.exists():
            logger.error(f"generate_interfaces executable not found: {exe_path}")
            return False

        valve_file_name = os.path.basename(valve_file_path)
        cmd = [str(exe_path), valve_file_name]

        try:
            result = subprocess.run(
                cmd,
                cwd=game_directory,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            if result.stdout:
                logger.info(result.stdout.strip())
            if result.stderr:
                logger.debug(f"generate_interfaces stderr: {result.stderr.strip()}")

            if result.returncode == 0:
                logger.info(f"Generate interfaces completed for {valve_file_name}")
                return True
            else:
                logger.error(
                    f"Generate interfaces failed (code {result.returncode}) for {valve_file_name}"
                )
                return False
        except Exception as e:
            logger.error(f"Error running generate_interfaces: {e}")
            return False

    def _remove_goldberg_from_single_dir(self, target_dir: str):
        """Remove Goldberg from one directory."""
        self._delete_goldberg_added_files(target_dir)
        self._restore_original_files(target_dir)

    def _delete_goldberg_added_files(self, target_dir: str):
        """Delete files/folders that were added by Goldberg."""
        # Remove steam_settings
        settings_path = os.path.join(target_dir, "steam_settings")
        if os.path.exists(settings_path):
            self._safe_rmtree(settings_path)

        # Remove steam_appid.txt
        appid_path = os.path.join(target_dir, "steam_appid.txt")
        if os.path.exists(appid_path):
            self._safe_remove(appid_path)

        # Remove any Goldberg DLLs/SOs that are not .valve
        for name in [
            "steam_api.dll",
            "steam_api64.dll",
            "libsteam_api.so",
            "libsteam_api64.so",
        ]:
            full = os.path.join(target_dir, name)
            if os.path.exists(full) and not os.path.exists(full + ".valve"):
                self._safe_remove(full)

    def _restore_original_files(self, target_dir: str):
        """Rename .valve backups back to original names."""
        for fname in os.listdir(target_dir):
            if not fname.lower().endswith((".dll.valve", ".so.valve")):
                continue
            original = fname[:-6]  # remove .valve
            backup_path = os.path.join(target_dir, fname)
            original_path = os.path.join(target_dir, original)
            if os.path.exists(original_path):
                self._safe_remove(original_path)
            try:
                os.rename(backup_path, original_path)
            except Exception as e:
                logger.error(f"Failed to restore {backup_path} to {original_path}: {e}")

    @staticmethod
    def _run_chmod_recursive(game_directory) -> int:
        import stat

        linux_binary_extensions = {
            ".sh",
            ".bash",
            ".x86",
            ".x86_64",
            ".bin",
            ".run",
            ".elf",
            ".pck",
        }
        elf_magic = b"\x7fELF"
        shebang_magic = b"#!"

        chmod_count = 0

        for root, _, filenames in os.walk(game_directory):
            for filename in filenames:
                file_path = os.path.join(root, filename)

                if os.path.islink(file_path):
                    continue

                should_chmod = False
                filename_lower = filename.lower()

                if any(filename_lower.endswith(ext) for ext in linux_binary_extensions):
                    should_chmod = True
                elif "." not in filename:
                    try:
                        with open(file_path, "rb") as f:
                            header = f.read(4)
                            if header.startswith(elf_magic) or header.startswith(
                                shebang_magic
                            ):
                                should_chmod = True
                    except (IOError, OSError):
                        continue

                if should_chmod:
                    try:
                        file_stat = os.stat(file_path)
                        current_mode = file_stat.st_mode
                        if not (current_mode & stat.S_IXUSR):
                            new_mode = current_mode | 0o755
                            os.chmod(file_path, new_mode)
                            chmod_count += 1
                    except OSError:
                        pass

        return chmod_count

    def _show_chmod_resume_dialog(self, game_name: str, file_count: int, dialog_class):
        dialog = dialog_class(
            game_name=game_name,
            file_count=file_count,
            success=True,
            parent=self.main_window,
        )
        dialog.exec()

    def _on_steamless_progress(self, message):
        self._steamless_progress_log.append(message)
        logger.info(message)

    def _on_steamless_complete(self, success):
        logger.info("\n" + "=" * 40)
        if success:
            logger.info("Steamless processing completed successfully")
        else:
            logger.info("Steamless processing completed with warnings or no DRM found")


        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.set_stage_status("steamless", "completed" if success else "error")

        self._steamless_success = success
        self._last_steamless_success = success

    def _on_steamless_finished(self):
        if self.steamless_task:
            QTimer.singleShot(0, self._clear_steamless_task)

        if self._steamless_manual_run:
            self._show_steamless_resume_dialog()
            self._steamless_manual_run = False
            self._steamless_success = None
            return

        if self._steamless_success is not None:
            self._steamless_success = None

        self._current_active_step = None

        QMetaObject.invokeMethod(
            self, "_finalize_job_logic", Qt.ConnectionType.QueuedConnection
        )

    def _clear_steamless_task(self):
        self.steamless_task = None

    def _show_steamless_resume_dialog(self):
        # Deferred import
        from ui.dialogs.steamless_resume import SteamlessResumeDialog

        exe_count = 0
        processed_count = 0
        had_error = self._steamless_error

        # If a single file was targeted directly, default exe_count to at least 1
        if self._steamless_game_name and self._steamless_game_name.lower().endswith(".exe"):
            exe_count = 1

        for message in self._steamless_progress_log:
            if "Found " in message and "executable(s)" in message:
                try:
                    parts = message.split()
                    for i, part in enumerate(parts):
                        if part == "Found" and i + 1 < len(parts):
                            exe_count = int(parts[i + 1])
                            break
                except ValueError:
                    pass

            if (
                "Successfully processed:" in message
                or "Successfully unpacked file!" in message
                or "Unpacked with SteamStub" in message
                or "[+] Unpacked with" in message
            ):
                processed_count += 1
                exe_count = max(exe_count, 1)

            if (
                "No Steam DRM detected" in message
                or "No variant matched" in message
                or "[-] No Steam DRM" in message
                or "[!] No variant matched" in message
            ):
                exe_count = max(exe_count, 1)

        # Fallback if _last_steamless_success was True
        if getattr(self, "_last_steamless_success", False) and not had_error:
            processed_count = max(processed_count, 1)
            exe_count = max(exe_count, 1)

        actual_success = (processed_count > 0 or getattr(self, "_last_steamless_success", False)) and not had_error

        dialog = SteamlessResumeDialog(
            game_name=self._steamless_game_name,
            exe_count=exe_count,
            processed_count=processed_count,
            success=actual_success,
            parent=self.main_window,
        )
        dialog.exec()
        self._steamless_progress_log = []
        self._steamless_game_name = ""

    def _handle_steamless_task_error(self, error_info):
        _, error_value, _ = error_info
        error_str = str(error_value)
        logger.error(f"Steamless processing failed: {error_str}")

        is_linux_no_drm = "no suitable game executables found" in error_str.lower()
        is_no_steam_drm = "no steam drm detected" in error_str.lower()

        if is_linux_no_drm or is_no_steam_drm:
            self._steamless_progress_log.append(error_str)
            self._steamless_error = False
            self._last_steamless_success = False
            if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
                self.main_window.simplified_terminal.set_stage_status("steamless", "completed")
        else:
            self._steamless_error = True
            if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
                self.main_window.simplified_terminal.set_stage_status("steamless", "error")

        if self.steamless_task:
            QTimer.singleShot(0, self._clear_steamless_task)

        self._current_active_step = None

        QMetaObject.invokeMethod(
            self, "_finalize_job_logic", Qt.ConnectionType.QueuedConnection
        )

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

    def _add_appids_to_slssteam_config(self):
        if not self.game_data:
            return

        try:
            config_path = get_user_config_path()
            if not config_path.exists():
                return

            main_appid = self.game_data.get("appid")
            game_name = self.game_data.get("game_name", "")
            if main_appid:
                from utils.dlc_helpers import sync_dlc_only_sls_config
                sync_dlc_only_sls_config(config_path, str(main_appid), game_name, self.game_data)

        except OSError as e:
            logger.warning(f"Failed to add AppIDs to SLSsteam config: {e}")

    def _create_greenluma_applist_files(self, steam_path, config_enabled=True):
        if not config_enabled:
            return

        try:
            app_list_dir = os.path.join(steam_path, "AppList")
            if not os.path.exists(app_list_dir):
                os.makedirs(app_list_dir)

            if not self.game_data:
                return

            game_appid = self.game_data.get("appid")
            if not game_appid:
                return

            if not self._app_id_exists_in_applist(app_list_dir, game_appid):
                next_num = self._find_next_applist_number(app_list_dir)
                filepath = os.path.join(app_list_dir, f"{next_num}.txt")
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(game_appid)
                logger.info(
                    f"Created GreenLuma file: {filepath} for AppID: {game_appid}"
                )

            selected_dlcs: List[str] = self.game_data.get("selected_dlcs") or []
            for dlc_id in selected_dlcs:
                if not self._app_id_exists_in_applist(app_list_dir, dlc_id):
                    next_num = self._find_next_applist_number(app_list_dir)
                    filepath = os.path.join(app_list_dir, f"{next_num}.txt")
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(str(dlc_id))
                    logger.info(f"Created GreenLuma file: {filepath} for DLC: {dlc_id}")

        except OSError as e:
            logger.error(f"Failed to create GreenLuma AppList files: {e}")

    @staticmethod
    def _copy_greenluma_bin_files(steam_path, config_enabled=True):
        if sys.platform != "win32":
            return
        if not config_enabled:
            return

        source_dir = Paths.deps()
        files_to_copy = ["NoQuestion.bin", "StealthMode.bin"]

        for filename in files_to_copy:
            source_path = os.path.join(source_dir, filename)
            dest_path = os.path.join(steam_path, filename)

            try:
                if os.path.exists(source_path):
                    if not os.path.exists(dest_path):
                        shutil.copy2(source_path, dest_path)
                        logger.info(f"Copied {filename} to Steam folder")
            except OSError:
                pass

    @staticmethod
    def _find_next_applist_number(app_list_dir):
        if not os.path.exists(app_list_dir):
            os.makedirs(app_list_dir)
            return 1
        max_num = 0
        try:
            for filename in os.listdir(app_list_dir):
                match = re.match(r"^(\d+)\.txt$", filename)
                if match:
                    num = int(match.group(1))
                    if num > max_num:
                        max_num = num
        except (OSError, ValueError):
            pass
        return max_num + 1

    @staticmethod
    def _app_id_exists_in_applist(app_list_dir, app_id_to_check):
        if not os.path.exists(app_list_dir):
            return False
        try:
            for filename in os.listdir(app_list_dir):
                if filename.lower().endswith(".txt"):
                    filepath = os.path.join(app_list_dir, filename)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            if f.read().strip() == app_id_to_check:
                                return True
                    except OSError:
                        pass
        except OSError:
            pass
        return False

    def _handle_task_error(self, error_info):
        if self.is_cancelling:
            return
        if not self.is_processing:
            return

        _, error_value, _ = error_info
        QMessageBox.critical(
            self.main_window, "Error", f"An error occurred: {error_value}"
        )
        if not self.is_cancelling:
            self.job_finished()

    def job_finished(self):
        """Clean up after job completion"""
        if not self.is_processing:
            return

        logger.info(
            f"Job '{os.path.basename(self.current_job or 'Unknown')}' finished."
        )

        if self.game_data:
            self._last_installed_game = self.game_data.get("game_name", "Unknown")

        ddm_ok = not self.is_cancelling

        if not self._slscheevo_ran:
            slscheevo_ok = None
        elif self._slscheevo_error:
            slscheevo_ok = False
        else:
            slscheevo_ok = True

        if not self._steamless_ran:
            steamless_ok = None
        elif self._steamless_error:
            steamless_ok = False
        else:
            steamless_ok = True

        self._update_status_for_job(
            ddm_ok=ddm_ok,
            slscheevo_ok=slscheevo_ok,
            steamless_ok=steamless_ok,
        )

        # Record installation log entry for SimplifiedTerminalWidget
        if self.game_data:
            game_name = self.game_data.get("game_name", "Unknown")
            appid = self.game_data.get("appid", "N/A")

            # Format download duration
            download_duration = getattr(self, "_last_download_duration", 0)
            download_size = getattr(self, "_last_download_size", 0)
            avg_speed_bps = getattr(self, "_last_download_avg_speed", 0)

            # Reset these variables for the next job
            self._last_download_duration = 0.0
            self._last_download_size = 0
            self._last_download_avg_speed = 0.0

            # Format achievements
            last_ach_status = getattr(self, "_last_slscheevo_status", "")
            ach_count = getattr(self, "_game_achievements_count", None)
            if last_ach_status == "skipped_no_ach":
                ach_status = "N/A"
            elif not self._slscheevo_ran:
                ach_status = "Skipped"
            else:
                if self._last_slscheevo_success:
                    ach_status = f"Generated ({ach_count})" if ach_count else "Generated"
                else:
                    ach_status = "Failed"
                # Check for "no missing stats files"
                sl_msg = getattr(self, "_last_slscheevo_message", "")
                if "no missing stats" in sl_msg.lower() or "already exist" in sl_msg.lower():
                    ach_status = f"Up-to-date ({ach_count})" if ach_count else "Up-to-date"

            # Format steamless
            steamless_status = self.parse_steamless_result()

            history_entry = {
                "game_name": game_name,
                "appid": appid,
                "download_size": download_size,
                "download_duration": download_duration,
                "avg_speed": avg_speed_bps,
                "ach_status": ach_status,
                "steamless_status": steamless_status,
                "timestamp": time.time(),
                "success": ddm_ok
            }

            # Add to simplified terminal
            if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
                self.main_window.simplified_terminal.add_history_entry(history_entry)

        logger.debug("Job finished; GIF animation removed.")
        self.main_window.progress_bar.setVisible(False)
        self.main_window.speed_label.setVisible(False)
        self.game_data = None
        self.current_dest_path = None
        self.current_job_metadata = None
        self.slssteam_mode_was_active = False
        self.library_mode_was_active = False
        self.is_processing = False

        # Release the shared Steam connection so it doesn't linger after download
        try:
            from core.steam_api import disconnect_shared_client
            disconnect_shared_client()
        except Exception as e:
            logger.debug(f"Error disconnecting shared client after job: {e}")

        # Refresh stats immediately after a job finishes to keep API limits in sync
        if self.main_window and hasattr(self.main_window, "refresh_hubcap_stats"):
            self.main_window.refresh_hubcap_stats()

        self._update_status_button_color()
        self.current_job = None

        self.is_download_paused = False
        self.main_window.ui_state.set_download_controls_visible(False)
        self.download_task = None
        self.is_cancelling = False
        self._delete_files_on_cancel = None

        if self.speed_monitor_task:
            self.is_awaiting_speed_monitor_stop = True
            self._stop_speed_monitor()
        else:
            self.is_awaiting_speed_monitor_stop = False

        if self.download_runner is None:
            self.is_awaiting_download_stop = False

        if self.workshop_runner is None:
            self.is_awaiting_workshop_stop = False

        self.workshop_task = None

        if self.zip_task_runner is None:
            self.is_awaiting_zip_task_stop = False

        if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
            self.main_window.simplified_terminal.show_idle()

        self.main_window.job_queue.check_if_safe_to_start_next_job()

    def _update_status_button_color(self):
        status = self.get_component_status()
        settings = self.main_window.settings
        accent_color = settings.value("accent_color", "#C06C84")

        ddm_status = status["ddm_status"]
        slscheevo_status = status["slscheevo_status"]
        steamless_status = status["steamless_status"]

        if (
            ddm_status == "error"
            or slscheevo_status == "error"
            or steamless_status == "error"
        ):
            overall_color = self.STATUS_ERROR
        elif (
            ddm_status == "in_progress"
            or slscheevo_status == "in_progress"
            or steamless_status == "in_progress"
        ):
            overall_color = self.STATUS_IN_PROGRESS
        elif ddm_status == "ok" or slscheevo_status == "ok" or steamless_status == "ok":
            overall_color = self.STATUS_OK
        else:
            overall_color = accent_color

        if (
            self.main_window
            and hasattr(self.main_window, "bottom_titlebar")
            and self.main_window.bottom_titlebar
        ):
            self.main_window.bottom_titlebar.update_colored_circle_button(
                self.main_window.bottom_titlebar.status_button, overall_color
            )
            self.main_window.bottom_titlebar.no_previous_state = False

    def toggle_pause(self):
        if not self.download_task:
            return

        self.is_download_paused = not self.is_download_paused

        try:
            self.download_task.toggle_pause(self.is_download_paused)
            if self.is_download_paused:
                self.main_window.ui_state.set_pause_button_text("Resume")
                self.main_window.drop_text_label.setText(
                    f"Paused: {os.path.basename(self.current_job)}"
                )
                self._stop_speed_monitor()
            else:
                self.main_window.ui_state.set_pause_button_text("Pause")
                job_type = self.game_data.get("job_type", "download") if self.game_data else "download"
                action_verb = "Validating" if job_type == "verify" else "Downloading"
                self.main_window.drop_text_label.setText(
                    f"{action_verb}: {os.path.basename(self.current_job)}"
                )
                self._start_speed_monitor()
        except Exception as e:
            logger.error(f"Failed to toggle pause: {e}")

    def cancel_current_job(self):
        if self.workshop_task and self.current_job:
            reply = QMessageBox.question(
                self.main_window,
                "Cancel Job",
                "Are you sure you want to cancel the Workshop download?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return
            logger.info("--- Cancelling Workshop job ---")
            self.is_cancelling = True
            if self.workshop_runner is not None:
                self.is_awaiting_workshop_stop = True
            if self.workshop_task:
                self.workshop_task.stop()
            return

        if not self.download_task or not self.current_job:
            return

        reply = QMessageBox.question(
            self.main_window,
            "Cancel Job",
            f"Are you sure you want to cancel the download for '{
                os.path.basename(self.current_job)
            }'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.No:
            return

        logger.info(f"--- Cancelling job: {os.path.basename(self.current_job)} ---")
        self.is_cancelling = True
        # Signal the finalize IO thread (if running) to abort immediately
        self._finalize_cancel_event.set()
        if self.download_runner is not None:
            self.is_awaiting_download_stop = True

        existing_install = getattr(self, "_pre_existing_install", False)
        if existing_install:
            self._delete_files_on_cancel = False
        else:
            self._delete_files_on_cancel = self._confirm_delete_on_cancel(existing_install)

        if self.download_task:
            self.download_task.stop()
        self._kill_download_process()

        if self.achievement_task:
            self.achievement_task.stop()

        if self.steamless_task:
            self.steamless_task.stop()

    def _detect_existing_installation(self) -> bool:
        if not self.current_dest_path or not self.game_data:
            return False

        current_job_metadata = self.current_job_metadata or {}
        install_path = current_job_metadata.get("install_path")
        if install_path and os.path.exists(install_path):
            return True

        steamapps_dir = os.path.join(self.current_dest_path, "steamapps")
        appmanifest_path = os.path.join(
            steamapps_dir,
            f"appmanifest_{self.game_data.get('appid', '')}.acf",
        )
        if os.path.exists(appmanifest_path):
            return True

        game_dir = get_game_directory(self.current_dest_path, self.game_data)
        if os.path.isdir(game_dir):
            try:
                with os.scandir(game_dir) as entries:
                    for _ in entries:
                        return True
            except OSError:
                return True

        return False

    def _confirm_delete_on_cancel(self, existing_install: bool) -> bool:
        if existing_install:
            message = (
                "Existing installation detected. Delete files for this canceled job?"
            )
            default_button = QMessageBox.StandardButton.No
        else:
            message = "Delete partially downloaded files for this job?"
            default_button = QMessageBox.StandardButton.Yes

        reply = QMessageBox.question(
            self.main_window,
            "Cancel Download",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            default_button,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _kill_download_process(self):
        if self.download_task and self.download_task.process:
            if psutil is None:
                logger.error("psutil unavailable; cannot terminate process safely.")
                return
            try:
                p = psutil.Process(self.download_task.process.pid)
                for child in p.children(recursive=True):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                p.kill()
            except psutil.NoSuchProcess:
                pass
            except Exception as e:
                logger.error(f"Failed to kill process: {e}")

            self.download_task.process = None
            self.download_task.process_pid = None

    def _cleanup_cancelled_job_files(self):
        if not self.game_data or not self.current_dest_path:
            return

        if getattr(self, "_pre_existing_install", False):
            logger.info("Skipping cancelled job file cleanup: Pre-existing installation detected.")
            return

        try:
            steamapps_dir = os.path.join(self.current_dest_path, "steamapps")
            common_dir = os.path.join(steamapps_dir, "common")
            game_dir = get_game_directory(self.current_dest_path, self.game_data)
            acf_path = os.path.join(
                steamapps_dir, f"appmanifest_{self.game_data['appid']}.acf"
            )

            if os.path.exists(game_dir):
                shutil.rmtree(game_dir)
            if os.path.exists(acf_path):
                os.remove(acf_path)

            temp_manifest_dir = os.path.join(
                tempfile.gettempdir(), "mistwalker_manifests"
            )
            if os.path.exists(temp_manifest_dir):
                shutil.rmtree(temp_manifest_dir)

            if not self.slssteam_mode_was_active:
                try:
                    if os.path.exists(common_dir):
                        os.rmdir(common_dir)
                    if os.path.exists(steamapps_dir):
                        os.rmdir(steamapps_dir)
                except OSError:
                    pass

        except OSError as e:
            logger.error(f"Failed during cancel cleanup: {e}")

    def download_slssteam(self, steam_path=None):
        if (
            self.slssteam_download_task is not None
            and self.slssteam_download_runner is not None
        ):
            return

        self.slssteam_download_task = DownloadSLSsteamTask(steam_path=steam_path)
        self.slssteam_download_task.progress.connect(self._handle_slssteam_progress)
        self.slssteam_download_task.progress_percentage.connect(
            self._handle_slssteam_progress_percentage
        )
        self.slssteam_download_task.completed.connect(
            self._on_slssteam_download_complete
        )
        self.slssteam_download_task.error.connect(self._handle_slssteam_download_error)

        self.slssteam_download_runner = TaskRunner()
        worker = self.slssteam_download_runner.run(self.slssteam_download_task.run)
        worker.error.connect(self._handle_task_error)

    @staticmethod
    def _handle_slssteam_progress(message):
        logger.info(f"SLSsteam: {message}")

    def _handle_slssteam_progress_percentage(self, percentage):
        pass

    def _on_slssteam_download_complete(self, message):
        logger.info(f"SLSsteam download completed: {message}")
        QMessageBox.information(
            self.main_window, "SLSsteam Installation Complete", message
        )
        self.slssteam_download_task = None
        self.slssteam_download_runner = None

    def _handle_slssteam_download_error(self):
        logger.error("SLSsteam download failed")
        QMessageBox.critical(
            self.main_window,
            "Error",
            "Failed to download SLSsteam. Check internet connection.",
        )
        self.slssteam_download_task = None
        self.slssteam_download_runner = None

    def cleanup(self):
        """Clean up all tasks during shutdown"""
        self._stop_speed_monitor()

        if self.download_task and self.download_task.process:
            self.download_task.stop()
            self._kill_download_process()

        if self.achievement_task:
            self.achievement_task.stop()

        if self.steamless_task:
            self.steamless_task.stop()

        TaskRunner.stop_all_active()

    def get_component_status(self):
        if self.is_processing:
            if self.download_task or self.zip_task:
                ddm_status = "in_progress"
                ddm_status_text = "Downloading..."
                slscheevo_status = self._last_slscheevo_status
                slscheevo_status_text = self._last_slscheevo_status_text
                steamless_status = self._last_steamless_status
                steamless_status_text = self._last_steamless_status_text
            elif self.steamless_task:
                ddm_status = "ok"
                ddm_status_text = "Completed"
                slscheevo_status = self._last_slscheevo_status
                slscheevo_status_text = self._last_slscheevo_status_text
                steamless_status = "in_progress"
                steamless_status_text = "Running..."
            elif self.achievement_task:
                ddm_status = "ok"
                ddm_status_text = "Completed"
                slscheevo_status = "in_progress"
                slscheevo_status_text = "Generating achievements..."
                steamless_status = self._last_steamless_status
                steamless_status_text = self._last_steamless_status_text
            else:
                ddm_status = self._last_ddm_status
                ddm_status_text = self._last_ddm_status_text
                slscheevo_status = self._last_slscheevo_status
                slscheevo_status_text = self._last_slscheevo_status_text
                steamless_status = self._last_steamless_status
                steamless_status_text = self._last_steamless_status_text
        else:
            ddm_status = self._last_ddm_status
            ddm_status_text = self._last_ddm_status_text
            slscheevo_status = self._last_slscheevo_status
            slscheevo_status_text = self._last_slscheevo_status_text
            steamless_status = self._last_steamless_status
            steamless_status_text = self._last_steamless_status_text

        return {
            "ddm_status": ddm_status,
            "ddm_status_text": ddm_status_text,
            "slscheevo_status": slscheevo_status,
            "slscheevo_status_text": slscheevo_status_text,
            "steamless_status": steamless_status,
            "steamless_status_text": steamless_status_text,
        }

    def _get_steamless_status_text(self):
        log_text = "\n".join(self._steamless_progress_log).lower()
        if "no suitable game executables found" in log_text:
            return "None (Linux Native)"
        if "no steam drm detected" in log_text:
            return "None"
        if self._last_steamless_success is None:
            return "Ready"
        elif self._last_steamless_success:
            return "Success"
        else:
            if "no steam drm detected" in log_text:
                return "None"
            return "Error"

    def parse_steamless_result(self) -> str:
        """Parse the steamless logs to determine what it did."""
        if not self._steamless_ran:
            return "Skipped"

        log_text = "\n".join(self._steamless_progress_log).lower()
        if "no suitable game executables found" in log_text:
            return "Skipped (Linux Native)"

        if "no steam drm detected" in log_text or "no drm found" in log_text or "not encrypted" in log_text:
            return "None (No DRM)"

        # Check if there was an error
        if self._steamless_error or not self._last_steamless_success:
            return "Failed / Error"

        # If successful, find the variant/version
        log_text = "\n".join(self._steamless_progress_log)
        
        # Try AIO log format first: "[+] Unpacked with V3.0 ->"
        match = re.search(r'Unpacked with\s+V?([\d\.]+x?)', log_text)
        if match:
            version = match.group(1)
            return f"Removed SteamStub v{version}"

        # Fallback to older C# CLI format
        match = re.search(r'[Vv]ariant[\s:]+([\d\.]+)', log_text)
        if match:
            version = match.group(1)
            return f"Removed SteamStub v{version}"

        return "Removed DRM"

    def _update_status_for_job(self, ddm_ok=True, slscheevo_ok=None, steamless_ok=None):
        self._last_ddm_status = "ok" if ddm_ok else "error"
        self._last_ddm_status_text = "Completed" if ddm_ok else "Failed"

        if slscheevo_ok is None:
            self._last_slscheevo_status = "not_run"
            self._last_slscheevo_status_text = "N/A"
        else:
            self._last_slscheevo_status = "ok" if slscheevo_ok else "error"
            if slscheevo_ok:
                ach_count = getattr(self, "_game_achievements_count", None)
                sl_msg = getattr(self, "_last_slscheevo_message", "")
                if "already exist" in sl_msg.lower() or "no missing stats" in sl_msg.lower() or "already exists" in sl_msg.lower():
                    self._last_slscheevo_status_text = f"Up-to-date ({ach_count})" if ach_count else "Up-to-date"
                else:
                    self._last_slscheevo_status_text = f"Generated ({ach_count})" if ach_count else "Generated"
            else:
                self._last_slscheevo_status_text = "Failed"

        if steamless_ok is None:
            self._last_steamless_status = "not_run"
            self._last_steamless_status_text = "N/A"
        else:
            log_text = "\n".join(self._steamless_progress_log).lower()
            is_linux_no_drm = "no suitable game executables found" in log_text
            self._last_steamless_status = "ok" if (steamless_ok or is_linux_no_drm) else "error"
            self._last_steamless_status_text = self._get_steamless_status_text()
