import json
import logging
import threading
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from PyQt6.QtCore import QMetaObject, QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QMessageBox

from core import morrenus_api as _api
from core.tasks.process_zip_task import ProcessZipTask
from core.tasks.smart_update_task import SmartUpdateTask
from managers.depot_key_manager import DepotKeyManager
from ui.dialogs.gamelibrary import format_game_display_name
from utils.settings import get_settings

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)


class BulkUpdateManager(QObject):
    """Manages Update All workflow: manifest preparation, depot selection, and queueing updates."""

    progress_updated = pyqtSignal(int, int, str)  # done, total, current_name
    skip_notice = pyqtSignal(str, bool)  # names_text, all_skipped
    flow_finished = pyqtSignal()

    def __init__(self, main_window: "MainWindow"):
        super().__init__(main_window)
        self.main_window = main_window
        self.is_running = False

    def run_flow(self) -> None:
        """Flow for updating all games that have update_available status."""
        if self.is_running:
            return

        gm = getattr(self.main_window, "game_manager", None)
        if not gm:
            return

        # Guard: don't start Update All while an update check is still running
        if getattr(gm, "manifest_check_task", None) is not None or getattr(gm, "manifest_check_runner", None) is not None:
            QMessageBox.information(
                self.main_window,
                "Update Check Running",
                "Please wait for the update check to finish before queuing updates.",
            )
            return

        settings = getattr(self.main_window, "settings", None) or get_settings()
        games = gm.get_all_games()
        updateable_games = []
        for g in games:
            if g.get("update_status") == "update_available":
                appid = str(g.get("appid", ""))
                if settings.value(f"exclude_from_update_all/{appid}", False, type=bool):
                    continue
                if settings.value(f"pin_build/{appid}", False, type=bool):
                    continue
                updateable_games.append(g)

        if not updateable_games:
            QMessageBox.information(
                self.main_window,
                "No Updates Available",
                "All games in your library are up to date!",
            )
            return

        self.is_running = True
        total = len(updateable_games)

        # Immediate feedback
        self.progress_updated.emit(0, total, "")

        def _do_update_all():
            queued = 0
            skipped_names = []
            dkm = DepotKeyManager()

            for idx, game_data in enumerate(updateable_games):
                appid = str(game_data.get("appid", "0"))
                name = format_game_display_name(game_data)
                update_status = game_data.get("update_status")
                self.progress_updated.emit(idx, total, name)
                logger.info(f"[Update All] Preparing {name} ({idx + 1}/{total})...")
                try:
                    local_path = None
                    branch = _api.get_selected_branch(appid)
                    fpath = _api.get_manifest_zip_path(appid, branch)

                    is_fresh = settings.value(f"manifest_is_fresh/{appid}", False, type=bool)
                    if fpath.exists() and (update_status != "update_available" or is_fresh):
                        local_path = str(fpath)

                    parsed_data = None

                    # 1. Try Smart Update Path
                    if dkm.has_depot_keys(appid):
                        task = SmartUpdateTask(appid, name, branch=branch)

                        def on_finished(assembled):
                            nonlocal parsed_data
                            parsed_data = assembled

                        task.finished.connect(on_finished)
                        task.progress.connect(lambda msg: logger.info(f"[Update All] {msg}"))
                        try:
                            task.run()
                        except Exception as e:
                            logger.error(f"Smart update failed in Update All: {e}")

                        if parsed_data:
                            local_path = str(fpath)

                    # 2. Fallback to Classic Path if Smart failed or no keys
                    if not parsed_data:
                        def has_lua(zp):
                            try:
                                with zipfile.ZipFile(zp, "r") as z:
                                    return any(f.endswith(".lua") for f in z.namelist())
                            except Exception:
                                return False

                        if not local_path or not has_lua(local_path):
                            logger.info(f"Update All: Fetching classic manifest for {name} (branch={branch})")
                            if local_path and Path(local_path).exists():
                                Path(local_path).unlink()
                            fpath_val, error = _api.download_manifest(appid, branch=branch)
                            if error or not fpath_val:
                                logger.warning(f"Update All: manifest download failed for {name}: {error}")
                                skipped_names.append(name)
                                continue
                            local_path = str(fpath_val)
                            settings.setValue(f"manifest_is_fresh/{appid}", True)

                        zip_task = ProcessZipTask()
                        try:
                            parsed_data = zip_task.run(local_path)
                        except Exception as e:
                            logger.error(f"Update All: Failed to process zip for {name}: {e}")
                            skipped_names.append(name)
                            continue

                    metadata = {
                        "appid": appid,
                        "library_path": game_data.get("library_path"),
                        "install_path": game_data.get("install_path"),
                        "game_name": name,
                        "branch": branch,
                    }

                    if parsed_data and parsed_data.get("depots"):
                        depots = parsed_data.get("depots")
                        selected_depots = None
                        val = settings.value(f"depot_selection/{appid}", "", type=str)
                        should_prompt = True
                        prev_selected = None

                        if val:
                            try:
                                data = json.loads(val)
                                cached_selected = data.get("selected", [])
                                cached_all = data.get("all_available", [])
                                prev_selected = cached_selected
                                has_new_depot = any(d not in cached_all for d in depots)
                                if has_new_depot:
                                    logger.info(f"Update All: new depot detected for {name} (appid={appid}), prompting user")
                                    settings.remove(f"depot_selection/{appid}")
                                else:
                                    selected_depots = [d for d in cached_selected if d in depots]
                                    if selected_depots:
                                        should_prompt = False
                            except Exception:
                                pass

                        if should_prompt:
                            auto_skip = settings.value("auto_skip_single_choice", False, type=bool)
                            if auto_skip and len(depots) == 1:
                                selected_depots = list(depots.keys())
                            else:
                                from ui.dialogs.depotselection import DepotSelectionDialog
                                result_holder = [None]
                                done_event = threading.Event()

                                def _show_depot_dialog():
                                    try:
                                        depot_dialog = DepotSelectionDialog(
                                            appid,
                                            parsed_data.get("game_name", name),
                                            depots,
                                            parsed_data.get("header_url"),
                                            self.main_window,
                                            selected_depots=prev_selected,
                                            is_single_depot=(len(depots) == 1),
                                            missing_hubcap_depots=parsed_data.get("missing_depots_from_hubcap"),
                                            missing_depots_info=parsed_data.get("missing_depots_info"),
                                            refetched_depots=parsed_data.get("refetched_depots"),
                                        )
                                        if depot_dialog.exec():
                                            result_holder[0] = depot_dialog.get_selected_depots()
                                    except Exception as err:
                                        logger.error(f"Error displaying depot selection dialog in Update All: {err}")
                                    finally:
                                        done_event.set()

                                if hasattr(self.main_window, "_main_thread_callable"):
                                    self.main_window._main_thread_callable.emit(_show_depot_dialog)
                                else:
                                    _show_depot_dialog()
                                done_event.wait(timeout=300)
                                selected_depots = result_holder[0]

                        if not selected_depots:
                            logger.info(f"Update All: skipping {name} — depot selection cancelled or no depots selected")
                            skipped_names.append(name)
                            continue

                        try:
                            settings.setValue(
                                f"depot_selection/{appid}",
                                json.dumps({
                                    "selected": selected_depots,
                                    "all_available": list(depots.keys()),
                                    "descriptions": {d_id: depots.get(d_id, {}).get("desc", "") for d_id in selected_depots}
                                })
                            )
                        except Exception as e:
                            logger.warning(f"Failed to cache depot selection: {e}")

                        metadata["selected_depots_list"] = selected_depots

                    if hasattr(self.main_window, "job_queue") and self.main_window.job_queue:
                        self.main_window.job_queue.add_job(local_path, metadata)
                    queued += 1
                    logger.info(f"Update All queued: {name}")

                    self.progress_updated.emit(queued, total, "")

                except Exception as e:
                    logger.error(f"Update All failed for {name}: {e}", exc_info=True)
                    skipped_names.append(name)

            logger.info(f"Update All: queued {queued} of {total} games.")
            self.is_running = False
            self.flow_finished.emit()

            if skipped_names and queued == 0:
                self.skip_notice.emit("\n".join(f"• {n}" for n in skipped_names), True)
            elif skipped_names:
                self.skip_notice.emit("\n".join(f"• {n}" for n in skipped_names), False)

        threading.Thread(target=_do_update_all, daemon=True).start()
