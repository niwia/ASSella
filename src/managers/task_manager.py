import logging
import os
import re
import shutil
import sys
import time
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Set
import json
import urllib.request
import urllib.error

# QObject and pyqtSlot for robust threading
from PyQt6.QtCore import QTimer, Qt, QObject, pyqtSlot, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QMessageBox

try:
    import psutil
except ImportError:
    psutil = None

from core import steam_helpers
from core.tasks.download_depots_task import DownloadDepotsTask
from core.tasks.download_workshop_task import DownloadWorkshopTask
from core.tasks.native_steam_download_task import NativeSteamDownloadTask
from core.tasks.process_zip_task import ProcessZipTask

from utils.steam_manifest import get_game_directory
from utils.yaml_config_manager import is_slssteam_mode_enabled

from utils.task_runner import TaskRunner

from managers.achievements import AchievementsMixin
from managers.emulation.chmod import ChmodMixin
from managers.emulation.goldberg import GoldbergMixin
from managers.emulation.steamless import SteamlessMixin
from managers.finalize import PostInstallMixin

logger = logging.getLogger(__name__)


class TaskManager(
    PostInstallMixin,
    AchievementsMixin,
    GoldbergMixin,
    ChmodMixin,
    SteamlessMixin,
    QObject,
):
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
        self._last_total_size = 0
        self._last_uncompressed_size = 0
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
            appid_val = str((metadata or {}).get("appid") or "")
            st.set_stage_status("download", "in_progress")
            st.show_active_job(game_name, appid=appid_val)

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

        worker = self.zip_task_runner.run(self.zip_task.run, zip_path, self.current_job_metadata)
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
                    if k not in merged or merged[k] is None:
                        merged[k] = v
            game_data = merged

            if "manifest_overrides" in merged and merged["manifest_overrides"]:
                overrides = merged["manifest_overrides"]
                if isinstance(overrides, dict) and overrides:
                    if "manifests" not in game_data:
                        game_data["manifests"] = {}
                    for d_id, m_id in overrides.items():
                        game_data["manifests"][str(d_id)] = str(m_id)
                        if "depots" in game_data and str(d_id) in game_data["depots"]:
                            if isinstance(game_data["depots"][str(d_id)], dict):
                                game_data["depots"][str(d_id)]["manifest_id"] = str(m_id)
                    logger.info(f"[TaskManager] Applied manifest overrides: {overrides}")

            if merged.get("pin_build"):
                pinned_bid = merged.get("pinned_build_id") or merged.get("buildid")
                if pinned_bid:
                    game_data["buildid"] = str(pinned_bid)
                    game_data["_is_rollback"] = True

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

        from utils.paths import is_valid_download_directory
        auto_skip_single_choice = self.settings.value(
            "auto_skip_single_choice", False, type=bool
        )
        default_dl = self.settings.value("default_download_directory", "", type=str)
        has_default_dl = bool(default_dl and is_valid_download_directory(default_dl))
        missing_depots = (self.game_data or {}).get("missing_depots_from_hubcap") or []
        is_single = (len(depots) == 1)

        enable_at0m = (
            sys.platform == "linux"
            and self.settings.value(
                "enable_at0m",
                self.settings.value("enable_vapor", True, type=bool),
                type=bool,
            )
        )
        at0m_action = self.settings.value(
            "at0m_default_download_action",
            self.settings.value("vapor_default_download_action", "ask", type=str),
            type=str,
        )
        is_vapor_native = enable_at0m and (at0m_action == "native")

        # In AT0-M native mode, immediately hand off without dialog or timer
        if is_vapor_native:
            selected_depots = list(depots.keys())
            if self.game_data:
                self.game_data["selected_depots_list"] = selected_depots
            self._start_download_with_destination(selected_depots, "")
            return

        # Show 3-second countdown timer ONLY if single depot AND auto_skip is enabled AND a default download directory is set!
        # If user chose "Ask Every Time" (has_default_dl is False), always open minimal depot selection directly so they can choose their drive.
        show_timer = (is_single and auto_skip_single_choice and has_default_dl)

        if show_timer:
            from ui.dialogs.single_depot_timer_dialog import SingleDepotTimerDialog
            dlg = SingleDepotTimerDialog(
                self.main_window,
                "Single Depot Option",
                "Game has only one depot.\n\nProceed to download and add it to queue?",
                seconds=3,
            )
            res = dlg.exec()
            if res == SingleDepotTimerDialog.ACTION_YES:
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
                single_dest = (self.current_job_metadata or {}).get("library_path") or (self.game_data or {}).get("library_path") or default_dl
                self._start_download_with_destination(selected_depots, single_dest)
                return
            elif res == SingleDepotTimerDialog.ACTION_MANUAL:
                # User selected Manual: continue below to open minimal DepotSelectionDialog
                pass
            else:
                self.job_finished()
                return

        pref_lib = (self.current_job_metadata or {}).get("library_path") or (self.game_data or {}).get("library_path")
        show_storage = not is_vapor_native
        self.main_window.ui_state.depot_dialog = DepotSelectionDialog(
            game_data["appid"],
            game_data["game_name"],
            game_data["depots"],
            game_data.get("header_url"),
            self.main_window,
            selected_depots=saved_selection,
            is_single_depot=is_single,
            missing_hubcap_depots=missing_depots,
            missing_depots_info=(self.game_data or {}).get("missing_depots_info"),
            library_path=pref_lib,
            refetched_depots=(self.game_data or {}).get("refetched_depots"),
            show_storage=show_storage,
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

            if self.settings.value("demo_mode", False, type=bool):
                logger.info("[DemoMode] Demo Mode active: reached depot selection successfully. Skipping download and caching.")
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.information(
                    self.main_window,
                    "Demo Mode Active",
                    "Demo Mode: Reached the depot selection screen successfully!\n\n"
                    "Local caching and downloads are disabled in Demo Mode.",
                )
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
        from utils.paths import is_valid_download_directory
        current_job_metadata = self.current_job_metadata or {}
        existing_library_path = current_job_metadata.get("library_path") or (self.game_data or {}).get("library_path")
        if existing_library_path and is_valid_download_directory(existing_library_path):
            if is_slssteam_mode_enabled():
                self._handle_slssteam_mode()
            return existing_library_path

        default_dl_dir = self.settings.value("default_download_directory", "")
        if default_dl_dir and is_valid_download_directory(default_dl_dir):
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
        self._last_total_size = 0
        self._last_uncompressed_size = 0
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
            appid_val = str(self.game_data.get("appid") or "")
            st.set_stage_status("download", "in_progress")
            st.show_active_job(game_name, appid=appid_val)

        self.main_window.progress_bar.setVisible(True)
        self.main_window.progress_bar.setValue(0)
        self.main_window.speed_label.setVisible(True)

        # ── Choose download backend ──────────────────────────────────────────
        enable_at0m = (
            sys.platform == "linux"
            and self.settings.value(
                "enable_at0m",
                self.settings.value("enable_vapor", True, type=bool),
                type=bool,
            )
        )
        at0m_action = self.settings.value(
            "at0m_default_download_action",
            self.settings.value("vapor_default_download_action", "ask", type=str),
            type=str,
        )
        legacy_native = self.settings.value("use_native_steam_download", True, type=bool)

        explicit_backend = self.game_data.get("download_backend") if self.game_data else None
        appid_str = str(self.game_data.get("appid", "")).strip() if self.game_data else ""
        game_title = self.game_data.get("game_name", f"App {appid_str}") if self.game_data else f"App {appid_str}"
        accent_c = getattr(self.main_window, "accent_color", "#6c5ce7")

        use_native_steam = False
        action_mode = "ask"

        # Check explicit requested backend or rollback / downgrade flags first
        is_rollback_requested = bool(
            (self.game_data and self.game_data.get("_is_rollback"))
            or (self.current_job_metadata and self.current_job_metadata.get("is_rollback"))
        )

        if explicit_backend == "assella" or is_rollback_requested:
            logger.info(
                f"[TaskManager] Job explicitly requested ASSella Downloader "
                f"{'(Rollback/Downgrade)' if is_rollback_requested else ''} for '{game_title}'"
            )
            use_native_steam = False
        elif explicit_backend == "native":
            logger.info(f"[TaskManager] Job explicitly requested Native Steam for '{game_title}'")
            use_native_steam = True
            action_mode = "handoff"
        elif getattr(self, "_pre_existing_install", False):
            # Check how this existing installation was managed
            is_plugin_game = False
            if self.game_data:
                if (
                    self.game_data.get("is_plugin_game")
                    or self.game_data.get("source") == "Plugin/Native"
                    or self.game_data.get("is_vapor")
                    or self.game_data.get("is_atom")
                ):
                    is_plugin_game = True
                elif appid_str and appid_str not in ("0", "N/A", "unknown"):
                    try:
                        from utils.plugin_games import get_plugin_game
                        if get_plugin_game(appid_str):
                            is_plugin_game = True
                    except Exception:
                        pass

            is_accela_game = False
            if self.game_data:
                if (
                    self.game_data.get("source") == "ACCELA"
                    or self.game_data.get("accela_marker_path")
                ):
                    is_accela_game = True
                else:
                    try:
                        game_dir = get_game_directory(dest_path, self.game_data)
                        if game_dir and os.path.isdir(game_dir):
                            for m in (".assella", ".ASSELLA", ".accela", ".depotdownloader", ".ACCELA", ".DepotDownloader"):
                                if os.path.exists(os.path.join(game_dir, m)):
                                    is_accela_game = True
                                    break
                    except Exception:
                        pass

            if is_plugin_game and not is_accela_game:
                logger.info(
                    f"[TaskManager] Update for pre-existing Plugin/Native Steam game '{game_title}' — "
                    "defaulting to handoff"
                )
                use_native_steam = True
                action_mode = "handoff"
            else:
                logger.info(
                    f"[TaskManager] Update for pre-existing ACCELA game '{game_title}' — "
                    "using ASSella Downloader backend"
                )
                use_native_steam = False
        elif enable_at0m:
            if at0m_action == "native":
                use_native_steam = True
                action_mode = "handoff"
            elif at0m_action == "assella":
                use_native_steam = False
            else:  # "ask"
                from ui.dialogs.download_backend_dialog import (
                    DownloadBackendDialog,
                    BACKEND_CANCEL,
                    BACKEND_ASSELLA,
                    BACKEND_NATIVE_STEAM,
                )
                dlg = DownloadBackendDialog(
                    parent=self.main_window,
                    app_id=appid_str,
                    game_name=game_title,
                    accent_color=accent_c,
                )
                dlg.exec()
                choice = dlg.get_choice()
                if choice == BACKEND_CANCEL:
                    logger.info("[TaskManager] User cancelled download backend selection")
                    self.job_finished()
                    return
                elif choice == BACKEND_ASSELLA:
                    use_native_steam = False
                elif choice == BACKEND_NATIVE_STEAM:
                    use_native_steam = True
                    start_mode = self.settings.value(
                        "at0m_start_download_action",
                        self.settings.value("vapor_start_download_action", "ask", type=str),
                        type=str,
                    )
                    action_mode = start_mode or "ask"
        elif legacy_native:
            use_native_steam = True
            action_mode = self.settings.value("at0m_start_download_action", self.settings.value("vapor_start_download_action", "ask", type=str), type=str)

        if use_native_steam:
            if action_mode == "ask":
                from ui.dialogs.native_steam_action_dialog import (
                    NativeSteamActionDialog,
                    ACTION_TRACK,
                    ACTION_CANCEL,
                )
                dlg = NativeSteamActionDialog(
                    parent=self.main_window,
                    app_id=appid_str,
                    game_name=game_title,
                    accent_color=accent_c,
                )
                dlg.exec()
                action = dlg.get_action()
                if action == ACTION_CANCEL:
                    logger.info("[TaskManager] User cancelled native Steam download action dialog")
                    self.job_finished()
                    return
                if dlg.should_remember():
                    saved_val = "immediate" if action == ACTION_TRACK else "add_only"
                    self.settings.setValue("at0m_start_download_action", saved_val)
                    self.settings.setValue("vapor_start_download_action", saved_val)
                    logger.info(f"[TaskManager] Remembered default native Steam action: {saved_val}")
                chosen_action = "track" if action == ACTION_TRACK else "handoff"
            else:
                chosen_action = "track" if action_mode == "immediate" else "handoff"

            if chosen_action == "handoff":
                from core.native_steam.native_steam_handoff import perform_steam_handoff
                logger.info("[TaskManager] Initiating instant Steam handoff")
                game_name = self.game_data.get("game_name", "Game") if self.game_data else "Game"

                def _update_handoff_progress(msg: str):
                    logger.info(f"[Handoff] {msg}")
                    if self.main_window and hasattr(self.main_window, "speed_label") and self.main_window.speed_label:
                        self.main_window.speed_label.setText(msg)
                    if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
                        st = self.main_window.simplified_terminal
                        if hasattr(st, "active_game_card") and st.active_game_card:
                            st.active_game_card.set_sub_status(msg)
                    from PyQt6.QtWidgets import QApplication
                    app = QApplication.instance()
                    if app:
                        app.processEvents()

                _update_handoff_progress(f"Handing off {game_name} to Steam...")
                auto_inst = self.settings.value(
                    "at0m_start_download_immediately",
                    self.settings.value("vapor_start_download_immediately", True, type=bool),
                    type=bool,
                )
                ok, msg = perform_steam_handoff(
                    self.game_data,
                    selected_depots,
                    dest_path,
                    progress_cb=_update_handoff_progress,
                    auto_install=auto_inst,
                )
                self._last_handoff_success = ok
                self._is_handoff_job = True
                if ok:
                    logger.info(f"[TaskManager] Handoff complete: {msg}")
                    if auto_inst and self.game_data and self.game_data.get("appid"):
                        self._start_vapor_install_watcher(str(self.game_data.get("appid")))
                else:
                    logger.error(f"[TaskManager] Handoff failed: {msg}")

                self.job_finished()
                return
            else:
                logger.info("[TaskManager] Using monitored native Steam client download backend")
                self.download_task = NativeSteamDownloadTask()
        else:
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

    def _start_vapor_install_watcher(self, appid: str):
        """
        Spawn a background thread watching for Steam to complete downloading a Vapor game.
        When Steam marks the game StateFlags=4 (Fully Installed) in appmanifest_<appid>.acf,
        automatically trigger an async library scan so the game appears in the Library tab.
        """
        def _watch():
            try:
                from core.steam_helpers import get_steam_env
                env = get_steam_env()
                steamapps_dirs = [Path(p) for p in env.steamapps_paths if os.path.exists(p)]
                # Poll every 3 seconds for up to 30 minutes (600 iterations)
                for _ in range(600):
                    time.sleep(3)
                    for s_dir in steamapps_dirs:
                        acf_path = s_dir / f"appmanifest_{appid}.acf"
                        if acf_path.exists():
                            try:
                                text = acf_path.read_text(encoding="utf-8", errors="ignore")
                                m = re.search(r'"StateFlags"\s+"(\d+)"', text)
                                if m and m.group(1) == "4":
                                    logger.info(
                                        f"[VaporWatcher] Steam download completed for AppID {appid} (StateFlags=4). "
                                        "Marking up_to_date and refreshing library..."
                                    )
                                    # Now that Steam confirmed the download, set the status.
                                    # This is deferred from job_finished() so that wudrm/MRC
                                    # failures during download don't produce a false up_to_date.
                                    try:
                                        from utils.update_status_cache import get_update_cache
                                        cache = get_update_cache()
                                        cache.set_status(appid, "up_to_date")
                                        cache.save_async()
                                        logger.info(f"[VaporWatcher] Set up_to_date for appid={appid} after confirmed Steam download")
                                    except Exception as _cache_err:
                                        logger.debug(f"[VaporWatcher] Error updating status cache for {appid}: {_cache_err}")
                                    if (
                                        self.main_window
                                        and hasattr(self.main_window, "game_manager")
                                        and self.main_window.game_manager
                                    ):
                                        self.main_window.game_manager.scan_steam_libraries_async()
                                    return
                            except Exception:
                                pass
                # Watcher timed out — Steam never reached StateFlags=4 within 30 minutes.
                # The download likely failed (e.g. wudrm/MRC unavailable). Do NOT mark up_to_date.
                logger.warning(
                    f"[VaporWatcher] Timed out waiting for Steam to complete AppID {appid} download. "
                    "Update status will remain as-is (not marked up_to_date)."
                )
            except Exception as e:
                logger.debug(f"[VaporWatcher] Error in watcher thread for AppID {appid}: {e}")

        t = threading.Thread(target=_watch, daemon=True)
        t.start()

    def start_native_steam_handoff(
        self,
        app_id: str,
        game_name: str = "",
        auto_install: bool = True,
        library_path: str = "",
    ):
        """
        Direct asynchronous handoff to Steam client without manifest zip preprocessing
        or depot selection dialogs.
        """
        app_id_str = str(app_id).strip()
        name = game_name or f"App {app_id_str}"
        logger.info(f"[TaskManager] start_native_steam_handoff: {name} ({app_id_str}), auto_install={auto_install}")

        self.is_processing = True
        self._is_handoff_job = True
        self.game_data = {
            "appid": app_id_str,
            "game_name": name,
            "library_path": library_path,
        }
        self.current_job = f"steam_handoff_{app_id_str}"
        self.current_job_metadata = {"appid": app_id_str, "game_name": name, "auto_install": auto_install}

        if self.main_window:
            if hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
                st = self.main_window.simplified_terminal
                st.show_active_job(name, appid=app_id_str)
                st.set_stage_status("download", "running")
                if hasattr(st, "active_game_card") and st.active_game_card:
                    st.active_game_card.set_sub_status("Handing off to Steam...")
            if hasattr(self.main_window, "progress_bar") and self.main_window.progress_bar:
                self.main_window.progress_bar.setVisible(True)
                self.main_window.progress_bar.setRange(0, 0)
            if hasattr(self.main_window, "speed_label") and self.main_window.speed_label:
                self.main_window.speed_label.setVisible(True)
                self.main_window.speed_label.setText(f"Handing off {name} to Steam...")

        def _do_handoff():
            from core.native_steam.native_steam_handoff import perform_steam_handoff
            def _update_msg(msg: str):
                logger.info(f"[Handoff] {msg}")
                if self.main_window and hasattr(self.main_window, "speed_label") and self.main_window.speed_label:
                    self.main_window.speed_label.setText(msg)
                if self.main_window and hasattr(self.main_window, "simplified_terminal") and self.main_window.simplified_terminal:
                    st = self.main_window.simplified_terminal
                    if hasattr(st, "active_game_card") and st.active_game_card:
                        st.active_game_card.set_sub_status(msg)
            return perform_steam_handoff(
                self.game_data,
                selected_depots=None,
                dest_path=library_path,
                progress_cb=_update_msg,
                auto_install=auto_install,
            )

        def _on_handoff_finished(result):
            ok, msg = result
            self._last_handoff_success = ok
            if ok:
                logger.info(f"[TaskManager] Handoff succeeded: {msg}")
                if auto_install:
                    self._start_vapor_install_watcher(app_id_str)
            else:
                logger.error(f"[TaskManager] Handoff failed: {msg}")

            if self.main_window and hasattr(self.main_window, "progress_bar") and self.main_window.progress_bar:
                self.main_window.progress_bar.setRange(0, 100)
                self.main_window.progress_bar.setValue(100 if ok else 0)

            self.job_finished()

        from utils.task_runner import TaskRunner
        self._handoff_runner = TaskRunner(self)
        worker = self._handoff_runner.run(_do_handoff)
        worker.finished.connect(_on_handoff_finished)
        worker.error.connect(lambda err: (logger.error(f"[TaskManager] Handoff error: {err}"), _on_handoff_finished((False, str(err)))))

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

        is_handoff = getattr(self, "_is_handoff_job", False)
        if is_handoff:
            ddm_ok = getattr(self, "_last_handoff_success", True)
            self._last_handoff_success = None
            self._is_handoff_job = False
        else:
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
        if is_handoff and ddm_ok:
            self._last_ddm_status_text = "Handed Off"

        # Record installation log entry for SimplifiedTerminalWidget
        if self.game_data:
            game_name = self.game_data.get("game_name", "Unknown")
            appid = self.game_data.get("appid", "N/A")

            # Format download metrics
            download_duration = getattr(self, "_last_download_duration", 0)
            download_size = getattr(self, "_last_download_size", 0)
            total_size = getattr(self, "_last_total_size", 0)
            uncompressed_size = getattr(self, "_last_uncompressed_size", 0)
            avg_speed_bps = getattr(self, "_last_download_avg_speed", 0)

            # Reset these variables for the next job
            self._last_download_duration = 0.0
            self._last_download_size = 0
            self._last_total_size = 0
            self._last_uncompressed_size = 0
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
                "total_size": total_size,
                "uncompressed_download_size": uncompressed_size,
                "download_duration": download_duration,
                "avg_speed": avg_speed_bps,
                "ach_status": ach_status,
                "steamless_status": steamless_status,
                "timestamp": time.time(),
                "success": ddm_ok,
                "handed_off": is_handoff,
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

        # An optional component was requested automatically but is not
        # downloaded. Offer it now that the job is done and the GUI is idle.
        pending_component = getattr(self, "_pending_component_prompt", None)
        if pending_component:
            self._pending_component_prompt = None
            try:
                from ui.dialogs.settings_tabs.components_card import (
                    prompt_component_missing,
                )

                prompt_component_missing(self.main_window, pending_component)
            except Exception as e:
                logger.debug(f"Could not prompt for optional component: {e}")

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
