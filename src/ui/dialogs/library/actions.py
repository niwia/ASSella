import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QMetaObject, Q_ARG, pyqtSlot, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QGraphicsOpacityEffect,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QListWidgetItem,
)

from ui.dialogs.library.widgets import GameItemWidget

try:
    from core import morrenus_api
except ImportError:
    morrenus_api = None

try:
    from core.steam_helpers import slssteam_api_send
except ImportError:
    def slssteam_api_send(_cmd):
        return None

try:
    from utils.helpers import get_base_path
except ImportError:
    def get_base_path():
        return Path(".")

logger = logging.getLogger(__name__)


class LibraryActionsMixin:
    """Mixin combining Multi-Selection FAB, Batch Actions, and Game Operations."""

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Floating Action Button (FAB) & Selection Management
    # ─────────────────────────────────────────────────────────────────────────

    def _position_selection_fab(self) -> None:
        if hasattr(self, "selection_fab") and self.selection_fab and self.selection_fab.isVisible():
            self.selection_fab.adjustSize()
            x = self.games_list.x() + self.games_list.width() - self.selection_fab.width() - 25
            y = self.games_list.y() + self.games_list.height() - self.selection_fab.height() - 25
            self.selection_fab.move(x, y)
            self.selection_fab.raise_()

            # Align the animated FAB menu container above the selection FAB
            if hasattr(self, "fab_menu_container") and self.fab_menu_container:
                self.fab_menu_container.adjustSize()
                mx = x + self.selection_fab.width() - self.fab_menu_container.width()
                my = y - self.fab_menu_container.height() - 10
                self.fab_menu_container.move(mx, my)
                self.fab_menu_container.raise_()

    def _show_fab_menu(self) -> None:
        is_visible = self.fab_menu_container.isVisible()
        if not is_visible:
            self.fab_menu_container.setVisible(True)
            self._position_selection_fab()

            # Material Design 3 menu entry fade-in animation
            op = QGraphicsOpacityEffect(self.fab_menu_container)
            self.fab_menu_container.setGraphicsEffect(op)
            self.anim = QPropertyAnimation(op, b"opacity")
            self.anim.setDuration(150)
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
            self.anim.setEasingCurve(QEasingCurve.Type.OutQuad)
            self.anim.start()

            self.selection_fab.setText("✕  Close")
        else:
            self._close_fab_menu()

    def _close_fab_menu(self) -> None:
        if hasattr(self, "fab_menu_container") and self.fab_menu_container.isVisible():
            self.fab_menu_container.setVisible(False)
            count = len(self._selected_appids)
            self.selection_fab.setText(f"Actions ({count})  ▼" if count else "Actions  ▼")

    def _toggle_select_mode(self) -> None:
        """Enable or disable multi-select mode."""
        self._select_mode = self.select_mode_button.isChecked()
        if not self._select_mode:
            self._selected_appids.clear()

        self._update_selection_footer()
        self._refresh_game_list()

    def _update_selection_footer(self) -> None:
        """Update the selection FAB state and count."""
        count = len(self._selected_appids)
        if self._select_mode and count > 0:
            self.selection_fab.setText(f"Actions ({count})  ▼")
            self.selection_fab.setVisible(True)
            self._position_selection_fab()
        else:
            self.selection_fab.setVisible(False)

    def _clear_selection(self) -> None:
        """Clear all selections and refresh visual state."""
        self._selected_appids.clear()
        self._update_selection_footer()
        # Update all visible checkboxes
        for i in range(self.games_list.count()):
            item = self.games_list.item(i)
            widget = self.games_list.itemWidget(item)
            if isinstance(widget, GameItemWidget):
                widget.set_selected(False)

    def _on_item_selected(self, item: QListWidgetItem) -> None:
        """Handle click on list item."""
        if self._refreshing or not item:
            return

        game_data = item.data(Qt.ItemDataRole.UserRole)
        if not game_data:
            return

        # In select mode: toggle selection, don't open dialog
        if self._select_mode:
            appid = str(game_data.get("appid", "0"))
            if appid in self._selected_appids:
                self._selected_appids.discard(appid)
            else:
                self._selected_appids.add(appid)
            # Update checkbox visual on the widget
            widget = self.games_list.itemWidget(item)
            if isinstance(widget, GameItemWidget):
                widget.set_selected(appid in self._selected_appids)
            self._update_selection_footer()
            return

        if self._dialog_open:
            return

        # Debounce
        self._dialog_open = True
        QTimer.singleShot(500, lambda: setattr(self, "_dialog_open", False))

        self._show_game_details_dialog(game_data)

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Batch Operations (Update / Uninstall / Enqueue)
    # ─────────────────────────────────────────────────────────────────────────

    def _on_update_action_clicked(self) -> None:
        self._close_fab_menu()
        self._on_queue_selected()

    def _on_uninstall_action_clicked(self) -> None:
        self._close_fab_menu()
        self._on_uninstall_selected()

    def _on_uninstall_selected(self) -> None:
        """Batch uninstall all selected games."""
        if not self._selected_appids:
            return

        # Gather game data for selected appids
        selected_games = []
        for i in range(self.games_list.count()):
            item = self.games_list.item(i)
            if not item:
                continue
            game_data = item.data(Qt.ItemDataRole.UserRole)
            if not game_data:
                continue
            appid = str(game_data.get("appid", "0"))
            if appid in self._selected_appids:
                selected_games.append(game_data)

        if not selected_games:
            return

        game_names_str = "\n".join(f"\u2022 {g.get('game_name', 'Unknown')}" for g in selected_games[:10])
        if len(selected_games) > 10:
            game_names_str += f"\n\u2022 ...and {len(selected_games) - 10} more."

        confirm_msg = (
            f"Are you sure you want to uninstall the following {len(selected_games)} games?\n\n"
            f"{game_names_str}\n\n"
            "This will delete all files in the game directories and their Steam .acf files!"
        )

        reply = QMessageBox.question(
            self,
            "Confirm Batch Uninstall",
            confirm_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        progress = QProgressDialog("Uninstalling games...", None, 0, len(selected_games), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()

        success_count = 0
        failed_names = []

        for idx, game_data in enumerate(selected_games):
            progress.setLabelText(f"Uninstalling {game_data.get('game_name', 'game')}...")
            progress.setValue(idx)
            QApplication.processEvents()

            try:
                success, err = self.game_manager.uninstall_game(
                    game_data, remove_compatdata=False, remove_saves=False, remove_sls=False
                )
                if success:
                    success_count += 1
                else:
                    failed_names.append(f"{game_data.get('game_name')} ({err})")
            except Exception as e:
                failed_names.append(f"{game_data.get('game_name')} ({e})")

        progress.setValue(len(selected_games))
        progress.close()

        # Exit select mode and refresh list
        self.select_mode_button.setChecked(False)
        self._toggle_select_mode()
        self._refresh_game_list()

        if failed_names:
            failed_str = "\n".join(failed_names[:10])
            if len(failed_names) > 10:
                failed_str += f"\n\u2022 ...and {len(failed_names) - 10} more."
            QMessageBox.warning(
                self,
                "Uninstall Summary",
                f"Successfully uninstalled {success_count} games.\n\n"
                f"Failed to uninstall:\n{failed_str}"
            )
        else:
            QMessageBox.information(
                self,
                "Uninstall Complete",
                f"Successfully uninstalled all {success_count} selected games."
            )

    def _on_queue_selected(self) -> None:
        """Directly enqueue selected games without any intermediate dialog."""
        if not self._selected_appids:
            return

        # Gather game data for selected appids
        selected_games = []
        for i in range(self.games_list.count()):
            item = self.games_list.item(i)
            if not item:
                continue
            game_data = item.data(Qt.ItemDataRole.UserRole)
            if not game_data:
                continue
            appid = str(game_data.get("appid", "0"))
            if appid in self._selected_appids:
                selected_games.append(game_data)

        if not selected_games:
            return

        # Disable the button so it can't be double-clicked
        self.selection_fab.setEnabled(False)
        self.selection_fab.setText("Queueing...")
        self.info_label.setText(f"Queueing {len(selected_games)} game(s)...")

        # Exit select mode immediately
        self.select_mode_button.setChecked(False)
        self._toggle_select_mode()

        # Run the heavy work (zip parse + depot resolve) off the main thread
        threading.Thread(
            target=self._enqueue_games_background,
            args=(selected_games,),
            daemon=True
        ).start()

    def _enqueue_games_background(self, selected_games: list) -> None:
        """Background thread: enqueue each game using the normal download+depot flow."""
        queued_count = 0
        total = len(selected_games)
        for i, game_data in enumerate(selected_games):
            appid = str(game_data.get("appid", "0"))
            if appid in ("0", "N/A", "unknown"):
                logger.warning(f"Skipping batch queue for game with invalid appid: {game_data.get('game_name')}")
                continue
            try:
                success = self._enqueue_single_game(game_data)
                if success:
                    queued_count += 1
            except Exception as e:
                logger.error(f"Batch queue failed for {game_data.get('game_name')}: {e}", exc_info=True)

        # Update UI back on main thread
        QMetaObject.invokeMethod(
            self,
            "_on_enqueue_finished",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, queued_count),
            Q_ARG(int, total),
        )

    @pyqtSlot(int, int)
    def _on_enqueue_finished(self, queued_count: int, total: int) -> None:
        """Called on main thread after background enqueueing is done."""
        if queued_count > 0:
            self.info_label.setText(f"\u2713 Queued {queued_count} of {total} game(s) \u2014 downloads starting.")
        else:
            self.info_label.setText("Nothing queued. Check App IDs are valid.")

    def _enqueue_single_game(self, game_data: dict) -> bool:
        """Enqueue a single game through the manifest fetch + depot selection flow."""
        try:
            appid = str(game_data.get("appid", "0"))
            name = game_data.get("game_name", "Unknown")
            update_status = game_data.get("update_status")

            from core import morrenus_api as _api
            from utils.settings import get_settings
            settings = get_settings()

            branch = _api.get_selected_branch(appid)

            # Check local cache first
            local_path = None
            fpath = _api.get_manifest_zip_path(appid, branch)
            is_fresh = settings.value(f"manifest_is_fresh/{appid}", False, type=bool)

            if fpath.exists() and (update_status != "update_available" or is_fresh):
                local_path = str(fpath)

            if not local_path:
                # Download manifest
                fpath, error = _api.download_manifest(appid, branch=branch)
                if error or not fpath:
                    logger.warning(f"Batch queue: manifest download failed for {name} (branch={branch}): {error}")
                    return False
                local_path = str(fpath)
                settings.setValue(f"manifest_is_fresh/{appid}", True)
                latest_id = settings.value(f"latest_steam_manifest_id/{appid}", "", type=str)
                if latest_id:
                    settings.setValue(f"fetched_manifest_id/{appid}", latest_id)

            # Parse for depots
            from core.tasks.process_zip_task import ProcessZipTask

            zip_task = ProcessZipTask()
            parsed_data = zip_task.run(local_path)

            metadata = {
                "appid": appid,
                "library_path": game_data.get("library_path"),
                "install_path": game_data.get("install_path"),
                "game_name": name,
                "branch": branch,
            }

            if parsed_data and parsed_data.get("depots"):
                from ui.dialogs.depotselection import DepotSelectionDialog
                auto_skip = settings.value("auto_skip_single_choice", False, type=bool)
                depots = parsed_data.get("depots")

                selected_depots = None

                smart_active = settings.value("smart_depot_selection", False, type=bool)
                val = settings.value(f"depot_selection/{appid}", "", type=str)
                should_prompt = True

                cached_selected = None
                if val:
                    try:
                        data = json.loads(val)
                        cached_selected = data.get("selected", [])
                        cached_all = data.get("all_available", [])
                        current_depots = list(depots.keys())
                        has_new_depot = any(d not in cached_all for d in current_depots)
                        if smart_active and not has_new_depot:
                            selected_depots = [d for d in cached_selected if d in depots]
                            should_prompt = False
                            logger.info(f"Smart selection active (batch). Reusing cached depots for {appid}: {selected_depots}")
                    except Exception as e:
                        logger.warning(f"Error parsing cached depot selection: {e}")

                if not cached_selected:
                    acf_installed = game_data.get("installed_depots") if isinstance(game_data, dict) else None
                    if acf_installed and isinstance(acf_installed, list):
                        cached_selected = [str(d) for d in acf_installed]

                if should_prompt:
                    if auto_skip and len(depots) == 1:
                        selected_depots = list(depots.keys())
                    else:
                        result_holder = [None]
                        storage_holder = [None]
                        done_event = threading.Event()

                        def _show_depot_dialog():
                            try:
                                depot_dialog = DepotSelectionDialog(
                                    parsed_data["appid"],
                                    parsed_data.get("game_name", name),
                                    depots,
                                    parsed_data.get("header_url"),
                                    self.main_window,
                                    selected_depots=cached_selected,
                                    is_single_depot=(len(depots) == 1),
                                    missing_hubcap_depots=parsed_data.get("missing_depots_from_hubcap"),
                                    missing_depots_info=parsed_data.get("missing_depots_info"),
                                    current_build_id=str(game_data.get("buildid") or "").strip() if isinstance(game_data, dict) else "",
                                )
                                if depot_dialog.exec():
                                    result_holder[0] = depot_dialog.get_selected_depots()
                                    storage_holder[0] = depot_dialog.get_selected_storage()
                            finally:
                                done_event.set()

                        QMetaObject.invokeMethod(
                            self,
                            "_run_on_main_thread",
                            Qt.ConnectionType.QueuedConnection,
                            Q_ARG(object, _show_depot_dialog),
                        )
                        done_event.wait(timeout=120)
                        selected_depots = result_holder[0]
                        if storage_holder[0]:
                            metadata["library_path"] = storage_holder[0]

                if not selected_depots:
                    logger.info(f"Batch queue: user cancelled depot selection for {name}")
                    return False

                metadata["selected_depots_list"] = selected_depots
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

            self.main_window.job_queue.add_job(local_path, metadata)
            logger.info(f"Batch queued: {name} (appid={appid})")
            return True

        except Exception as e:
            logger.error(f"Batch queue failed for {game_data.get('game_name')}: {e}", exc_info=True)
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Context Menu & Game Operations
    # ─────────────────────────────────────────────────────────────────────────

    def _show_games_list_context_menu(self, pos) -> None:
        """Show context menu for a game item."""
        item = self.games_list.itemAt(pos)
        if not item:
            return

        game_data = item.data(Qt.ItemDataRole.UserRole)
        if not game_data:
            return

        menu = QMenu(self)
        menu.setStyleSheet(
            f"""
            QMenu {{
                background-color: #111111;
                color: #FFFFFF;
                border: 1px solid #333333;
            }}
            QMenu::item:selected {{
                background-color: {self.accent_color};
                color: #000000;
            }}
            """
        )

        is_vapor_mode = bool(
            game_data.get("is_vapor")
            or game_data.get("is_plugin_game")
            or game_data.get("update_status") == "vapor"
        )

        verify_action = QAction("Verify Game Files", self)
        verify_action.triggered.connect(lambda: self._fetch_game_manifest(game_data))
        if is_vapor_mode:
            verify_action.setEnabled(False)
        menu.addAction(verify_action)

        open_folder_action = QAction("Open Install Folder", self)
        install_path = game_data.get("install_path")
        open_folder_action.triggered.connect(lambda: self._open_folder(install_path))
        menu.addAction(open_folder_action)

        reset_depots_action = QAction("Reset Depot Selection", self)
        reset_depots_action.triggered.connect(lambda: self._reset_depot_selection(game_data))
        if is_vapor_mode:
            reset_depots_action.setEnabled(False)
        menu.addAction(reset_depots_action)

        uninstall_action = QAction("Uninstall Game", self)
        uninstall_action.triggered.connect(lambda: self._uninstall_game(game_data, None, {}))
        if is_vapor_mode:
            uninstall_action.setEnabled(False)
        menu.addAction(uninstall_action)

        menu.exec(self.games_list.mapToGlobal(pos))

    @staticmethod
    def _open_folder(path: str) -> None:
        if not path or not os.path.exists(path):
            return
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":
                subprocess.call(["open", path])
            else:
                subprocess.call(["xdg-open", path])
        except OSError:
            pass

    def _confirm_action(self, title: str, message: str) -> bool:
        reply = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _uninstall_game(self, game_data: dict, dialog: QDialog = None, opts: dict = None) -> None:
        if not self.game_manager:
            return

        opts = opts or {}
        parent = dialog if dialog else self
        c_wipe_sls_only = opts.get("wipe_sls_only", False)
        game_name = game_data.get("game_name", "this game")

        is_dlc_only = False
        appid = str(game_data.get("appid", "0"))
        if appid and appid not in ("0", "N/A", "unknown"):
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc_only = is_dlc_only_mode(appid)

        if c_wipe_sls_only:
            confirm_title = "Take away my sins"
            confirm_msg = (
                f"Are you sure you want to remove SLS integrations for '{game_name}'?\n\n"
                "• Removes game from SLS config (SLS Online and EOS Proxy integrations will be reset as well)\n"
                "• Removes AppTokens, .DepotDownloader folder, and .depot tracking\n\n"
                "🛡️ Game files, saves, achievements, and Proton prefix will NOT be deleted."
            )
            progress_title = "Removing integrations..."
        else:
            confirm_title = "Confirm Uninstall"
            confirm_msg = self.game_manager.get_uninstall_confirmation_message(game_data)
            progress_title = "Uninstalling DLC..." if is_dlc_only else "Uninstalling game..."

        reply = QMessageBox.question(
            parent,
            confirm_title,
            confirm_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        c_data = opts.get("compat", False)
        c_saves = opts.get("saves", False)
        c_wipe_sls = opts.get("wipe_sls", False)

        self._uninstall_progress_dialog = QProgressDialog(
            progress_title, None, 0, 0, parent
        )
        self._uninstall_progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._uninstall_progress_dialog.show()

        self.executor.submit(self._uninstall_game_async, game_data, c_data, c_saves, c_wipe_sls, c_wipe_sls_only)

    def _uninstall_game_async(
        self, game_data: dict, c_data: bool, c_saves: bool, c_wipe_sls: bool = False, c_wipe_sls_only: bool = False
    ) -> None:
        """Background task to uninstall game."""
        try:
            if c_wipe_sls_only:
                success, err = self._wipe_sls_only(game_data)
                if success and self.game_manager:
                    appid = str(game_data.get("appid", ""))
                    if appid:
                        self.game_manager.remove_game(appid)
            else:
                success, err = self.game_manager.uninstall_game(
                    game_data, remove_compatdata=c_data, remove_saves=c_saves,
                    remove_sls=c_wipe_sls
                )
            self.uninstall_complete.emit(success, str(err) if err else "")
        except Exception as e:
            self.uninstall_complete.emit(False, str(e))

    def _on_uninstall_complete(self, success: bool, error: str) -> None:
        """Slot to handle uninstall completion."""
        if self._uninstall_progress_dialog:
            self._uninstall_progress_dialog.close()
            self._uninstall_progress_dialog = None

        parent = self._details_dialog if self._details_dialog else self
        if success:
            QMessageBox.information(parent, "Success", "Operation completed.")
            if self._details_dialog:
                try:
                    self._details_dialog.accept()
                except RuntimeError:
                    pass
            self._refresh_game_list()
        else:
            QMessageBox.critical(parent, "Error", f"Failed: {error}")

    @staticmethod
    def _wipe_sls_only(game_data: dict) -> tuple:
        """
        'I bought the game': Cleans all ACCELA & SLS modifications, leaving game files,
        saves, achievements, and Proton prefix 100% intact.
        """
        appid = str(game_data.get("appid", "")).strip()
        install_path = game_data.get("install_path", "")
        errors = []

        # 1. Discover all associated DLC AppIDs and Depot IDs
        target_ids = {appid} if appid and appid not in ("0", "N/A", "unknown") else set()
        dlc_ids = set()
        depot_ids = set()

        try:
            from utils.yaml_config_manager import get_user_config_path, get_dlc_data
            cfg_p = get_user_config_path()
            if cfg_p.exists() and appid:
                sls_dlcs = get_dlc_data(cfg_p, appid)
                if sls_dlcs:
                    for d_id in sls_dlcs.keys():
                        if str(d_id).strip().isdigit():
                            dlc_ids.add(str(d_id).strip())
        except Exception as e:
            logger.debug(f"Could not read DlcData for AppID {appid}: {e}")

        try:
            from utils.dlc_helpers import get_all_dlcs_for_app
            known_dlcs = get_all_dlcs_for_app(appid, game_data, allow_network=True)
            if known_dlcs:
                for d_entry in known_dlcs:
                    did = str(d_entry.get("dlc_appid", "")).strip()
                    if did.isdigit():
                        dlc_ids.add(did)
        except Exception as e:
            logger.debug(f"Could not fetch known DLCs for AppID {appid}: {e}")

        try:
            from utils.helpers import get_base_path
            depot_file = Path(get_base_path()) / "depots" / f"{appid}.depot"
            if depot_file.exists():
                for line in depot_file.read_text().splitlines():
                    parts = line.split(":")
                    if parts and parts[0].strip().isdigit():
                        depot_ids.add(parts[0].strip())
        except Exception as e:
            logger.debug(f"Could not read depot IDs from {appid}.depot: {e}")

        target_ids.update(dlc_ids)
        target_ids.update(depot_ids)
        target_ids.discard("")
        target_ids.discard("0")
        target_ids.discard("N/A")
        target_ids.discard("unknown")

        # 2. SLSsteam config cleanups
        try:
            from utils.yaml_config_manager import (
                get_user_config_path,
                remove_additional_app,
                remove_fake_app_id,
                remove_app_token,
                remove_launch_option,
                remove_dlc_data,
                _remove_entry_from_section,
            )
            config_path = get_user_config_path()
            if config_path.exists():
                for tid in target_ids:
                    remove_additional_app(config_path, tid)
                    remove_fake_app_id(config_path, tid)
                    remove_app_token(config_path, tid)
                    remove_launch_option(config_path, tid)

                if appid:
                    remove_dlc_data(config_path, appid)

                for sec in (
                    "GameTitles",
                    "ManifestIds",
                    "DepotBlacklist",
                    "CDKeys",
                    "FakeOffline",
                    "SubscriptionTimestamps",
                    "DenuvoGames",
                ):
                    for tid in target_ids:
                        pattern = re.compile(
                            rf"^[ \t]*['\"]?{re.escape(str(tid))}['\"]?[ \t]*(?::|$)[\s\S]*?$",
                            re.MULTILINE,
                        )
                        _remove_entry_from_section(
                            config_path,
                            sec,
                            pattern,
                            f"Removed {tid} from {sec}",
                            f"Failed to remove {tid} from {sec}: {{e}}",
                        )

                logger.info(
                    f"I bought the game: Removed AppID {appid} and related IDs {target_ids} from SLS config"
                )

            if platform.system() == "Linux":
                try:
                    for aid in ({appid} | dlc_ids):
                        if aid and aid.isdigit():
                            slssteam_api_send(f"uninstall|{aid}")
                except Exception as api_err:
                    logger.warning(f"Failed to send SLSsteam uninstall API trigger: {api_err}")
        except Exception as e:
            logger.error(f"Error cleaning SLS config for {appid}: {e}", exc_info=True)
            errors.append(f"SLS config: {e}")

        # 3. Restore original EOS binaries and remove EOS proxy (if applied)
        if install_path and os.path.isdir(install_path):
            try:
                from utils.eos_detector import EOSDetector
                status = EOSDetector.get_proxy_status(install_path)
                if status in ("active", "stale"):
                    EOSDetector.remove_proxy(install_path)
                    logger.info(f"I bought the game: Removed EOS proxy and restored original DLL in {install_path}")
            except Exception as e:
                logger.error(f"Error removing EOS proxy for {appid}: {e}", exc_info=True)
                errors.append(f"EOS proxy: {e}")

        # 4. Restore Goldberg emulator backups (if applied)
        if install_path and os.path.isdir(install_path):
            try:
                if LibraryActionsMixin._is_goldberg_applied(install_path):
                    for root, _, files in os.walk(install_path):
                        if any(f.lower().endswith((".dll.valve", ".so.valve")) for f in files):
                            st_dir = os.path.join(root, "steam_settings")
                            if os.path.isdir(st_dir):
                                shutil.rmtree(st_dir, ignore_errors=True)
                            aid_txt = os.path.join(root, "steam_appid.txt")
                            if os.path.exists(aid_txt):
                                try:
                                    os.remove(aid_txt)
                                except Exception:
                                    pass
                            for fname in files:
                                if fname.lower().endswith((".dll.valve", ".so.valve")):
                                    orig_name = fname[:-6]
                                    bak_path = os.path.join(root, fname)
                                    orig_path = os.path.join(root, orig_name)
                                    if os.path.exists(orig_path):
                                        try:
                                            os.remove(orig_path)
                                        except Exception:
                                            pass
                                    try:
                                        os.rename(bak_path, orig_path)
                                        logger.info(f"I bought the game: Restored Goldberg backup {orig_name} in {root}")
                                    except Exception as r_err:
                                        logger.warning(f"Failed to restore {bak_path}: {r_err}")
            except Exception as gb_err:
                logger.error(f"Error restoring Goldberg backups for {appid}: {gb_err}")
                errors.append(f"Goldberg restore: {gb_err}")

        # 5. Remove .DepotDownloader and .ACCELA folders (leave game files intact)
        if install_path and os.path.isdir(install_path):
            for marker_name in (".DepotDownloader", ".ACCELA"):
                marker_path = os.path.join(install_path, marker_name)
                if os.path.exists(marker_path):
                    try:
                        shutil.rmtree(marker_path)
                        logger.info(f"I bought the game: Removed {marker_name} from {install_path}")
                    except Exception as e:
                        logger.error(f"Error removing {marker_name}: {e}")
                        errors.append(f"{marker_name}: {e}")

        # 6. Remove .depot tracking file and custom depot definitions
        try:
            depot_file = Path(get_base_path()) / "depots" / f"{appid}.depot"
            if depot_file.exists():
                depot_file.unlink()
                logger.info(f"I bought the game: Removed depot file {depot_file}")
            custom_depot = Path(get_base_path()) / "depots" / f"{appid}_custom.json"
            if custom_depot.exists():
                custom_depot.unlink()
        except Exception as e:
            logger.error(f"Error removing depot files for {appid}: {e}")
            errors.append(f".depot tracking: {e}")

        # 7. Clear QSettings branch, build, and manifest cache keys
        try:
            from utils.settings import get_settings
            settings = get_settings()
            for tid in target_ids:
                for key in (
                    f"manifest_is_fresh/{tid}",
                    f"fetched_manifest_id/{tid}",
                    f"latest_steam_manifest_id/{tid}",
                    f"installed_branch/{tid}",
                    f"selected_branch/{tid}",
                    f"installed_buildid/{tid}",
                    f"fetched_buildid/{tid}",
                    f"pin_build/{tid}",
                    f"exclude_from_update_all/{tid}",
                    f"auto_update_manifest/{tid}",
                    f"dlc_only_mode/{tid}",
                    f"depot_selection/{tid}",
                ):
                    settings.remove(key)
        except Exception as _set_err:
            logger.debug(f"Failed to clear settings keys for appid {appid}: {_set_err}")

        if errors:
            return False, "; ".join(errors)
        return True, None

    def _fix_game_install(self, game_data: dict) -> None:
        path = game_data.get("library_path")
        appid = str(game_data.get("appid", ""))

        if not path or not appid or appid == "0":
            return

        acf = os.path.join(path, "steamapps", f"appmanifest_{appid}.acf")
        acf_existed = os.path.exists(acf)

        if acf_existed:
            if not self._confirm_action(
                "Confirm", "Remove manifest file? Steam will re-verify files."
            ):
                return
            try:
                os.remove(acf)
            except Exception as e:
                logger.warning(f"Could not remove manifest file {acf}: {e}")
        else:
            if not self._confirm_action(
                "Confirm", "Manifest file is missing. Repair installation and register with Steam?"
            ):
                return

        # Trigger SLSsteam install pipe and registration
        if sys.platform == "linux":
            try:
                from utils.slssteam_integration import install_via_sls, _experimental_mode_enabled, patch_acf_via_sls
                if _experimental_mode_enabled():
                    install_via_sls(
                        appid=appid,
                        game_name=game_data.get("game_name", ""),
                        library_path=path,
                    )
                else:
                    patch_acf_via_sls(appid, library_path=path)
            except Exception as sls_err:
                logger.warning(f"SLS install error during fix for {appid}: {sls_err}")

        # Fallback ACF generation if manifest still missing
        if not os.path.exists(acf):
            try:
                from utils.steam_manifest import write_acf_file
                info = None
                try:
                    from core.steam_api import get_depot_info_from_api
                    info = get_depot_info_from_api(appid)
                except Exception:
                    pass
                target_info = info or game_data
                write_acf_file(
                    path,
                    target_info,
                    size_on_disk=game_data.get("size_on_disk", 0),
                    include_depots=True,
                    logger=logger,
                )
                logger.info(f"Fallback ACF generated during fix for {appid}")
            except Exception as acf_err:
                logger.warning(f"Fallback ACF generation failed during fix for {appid}: {acf_err}")

        QMessageBox.information(
            self,
            "Done",
            "Installation repaired. Steam will verify game files."
            if not acf_existed
            else "Manifest removed. Steam will re-verify files.",
        )

        if hasattr(self, "game_manager") and self.game_manager:
            try:
                self.game_manager.scan_steam_libraries_async()
            except Exception:
                pass

    @staticmethod
    def _is_goldberg_applied(game_dir: str) -> bool:
        """Check for Goldberg backup files (.valve)."""
        if not game_dir or game_dir == "N/A" or not os.path.exists(game_dir):
            return False

        for root, _, files in os.walk(game_dir):
            for fname in files:
                if fname.lower() in (
                    "steam_api.dll.valve",
                    "steam_api64.dll.valve",
                    "libsteam_api.so.valve",
                    "libsteam_api64.so.valve",
                ):
                    return True
        return False

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Depot Selection & Configuration
    # ─────────────────────────────────────────────────────────────────────────

    def _update_depot_status_label(self, appid: str) -> None:
        if not hasattr(self, "depot_status_lbl") or not self.depot_status_lbl:
            return
        val = self.settings.value(f"depot_selection/{appid}", "", type=str)
        if val:
            try:
                data = json.loads(val)
                selected = data.get("selected", [])
                descriptions = data.get("descriptions", {})
                
                depot_names = []
                for d_id in selected:
                    desc = descriptions.get(d_id, "")
                    if not desc and morrenus_api:
                        fpath = morrenus_api.get_manifest_zip_path(appid, morrenus_api.get_selected_branch(appid))
                        if fpath.exists():
                            try:
                                from core.tasks.process_zip_task import ProcessZipTask
                                zip_task = ProcessZipTask()
                                parsed_data = zip_task.run(str(fpath))
                                desc = parsed_data.get("depots", {}).get(d_id, {}).get("desc", "")
                            except Exception:
                                pass
                    
                    if desc:
                        desc_clean = re.sub(r"\s*-\s*Depot\s*" + re.escape(d_id), "", desc, flags=re.IGNORECASE).strip()
                        depot_names.append(f"{d_id} ({desc_clean})")
                    else:
                        depot_names.append(d_id)
                
                names_str = ", ".join(depot_names)
                self.depot_status_lbl.setText(f"Status: {len(selected)} depot(s) manually chosen:\n{names_str}")
            except Exception:
                self.depot_status_lbl.setText("Status: Error reading saved selection.")
        else:
            self.depot_status_lbl.setText("Status: Default (automatic download of all depots).")

    def _reset_depot_selection(self, game_data: dict) -> None:
        appid = str(game_data.get("appid", ""))
        if not appid or appid in ("0", "N/A", "unknown"):
            return
        
        self.settings.remove(f"depot_selection/{appid}")
        self._update_depot_status_label(appid)
        QMessageBox.information(self, "Reset Successful", "Depot selection has been reset to default.")

    def _configure_depots(self, game_data: dict) -> None:
        appid = str(game_data.get("appid", "0"))
        if appid in ("0", "N/A", "unknown"):
            QMessageBox.warning(self, "Error", "Invalid App ID.")
            return

        if not morrenus_api:
            QMessageBox.critical(self, "Error", "API module missing.")
            return

        name = game_data.get("game_name", "Unknown")
        branch = morrenus_api.get_selected_branch(appid)
        fpath = morrenus_api.get_manifest_zip_path(appid, branch)

        if fpath.exists():
            self._show_depot_selection_dialog(str(fpath), game_data)
        else:
            self._download_progress_dialog = QProgressDialog(
                f"Downloading manifest for {name}...", "Cancel", 0, 0, self
            )
            self._download_progress_dialog.setWindowTitle("Downloading Manifest")
            self._download_progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            self._download_progress_dialog.show()

            self._configure_depots_after_download = game_data
            self.executor.submit(self._download_manifest_async, appid, game_data, branch)

    def _show_depot_selection_dialog(self, filepath: str, game_data: dict) -> None:
        try:
            from core.tasks.process_zip_task import ProcessZipTask
            zip_task = ProcessZipTask()
            parsed_data = zip_task.run(filepath)
            
            if parsed_data and parsed_data.get("depots"):
                from ui.dialogs.depotselection import DepotSelectionDialog
                depots = parsed_data.get("depots")
                appid = str(parsed_data["appid"])
                
                saved_val = self.settings.value(f"depot_selection/{appid}", "", type=str)
                selected_depots = None
                if saved_val:
                    try:
                        data = json.loads(saved_val)
                        selected_depots = data.get("selected", [])
                    except Exception:
                        pass

                depot_dialog = DepotSelectionDialog(
                    parsed_data["appid"],
                    parsed_data.get("game_name", ""),
                    depots,
                    parsed_data.get("header_url"),
                    self,
                    selected_depots=selected_depots,
                    is_single_depot=(len(depots) == 1),
                    missing_hubcap_depots=parsed_data.get("missing_depots_from_hubcap"),
                    missing_depots_info=parsed_data.get("missing_depots_info"),
                    current_build_id=str(game_data.get("buildid") or "").strip() if isinstance(game_data, dict) else "",
                )
                if depot_dialog.exec():
                    chosen = depot_dialog.get_selected_depots()
                    if chosen:
                        self.settings.setValue(
                            f"depot_selection/{appid}",
                            json.dumps({
                                "selected": chosen,
                                "all_available": list(depots.keys()),
                                "descriptions": {d_id: depots.get(d_id, {}).get("desc", "") for d_id in chosen}
                            })
                        )
                        self._update_depot_status_label(appid)
                        QMessageBox.information(self, "Success", "Depot selection saved successfully.")
                    else:
                        QMessageBox.warning(self, "Warning", "No depots selected. Depot selection not saved.")
        except Exception as e:
            logger.error(f"Failed to show depot selection dialog: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to load depots: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Manifest Fetch, Smart Update & Job Submissions
    # ─────────────────────────────────────────────────────────────────────────

    def _check_goldberg_async(self, path: str) -> None:
        """Background task to check Goldberg status."""
        is_applied = LibraryActionsMixin._is_goldberg_applied(path)
        self.goldberg_check_complete.emit(is_applied)

    def _on_goldberg_check_complete(self, is_applied: bool) -> None:
        """Slot to update UI after background check."""
        if not hasattr(self, "gb_btn"):
            return

        self.gb_btn.setText("Remove Goldberg" if is_applied else "Apply Goldberg")
        self.gb_btn.setEnabled(True)

        if is_applied:
            self.gb_btn.setStyleSheet(
                f"border: 1px solid {self.accent_color}; color: {self.accent_color};"
            )
        else:
            self.gb_btn.setStyleSheet("")

    def _fetch_game_manifest(
        self, game_data: dict, dialog: QDialog = None, download_only: bool = False,
        local_path_override: str = None, branch: str = None
    ) -> None:
        """Trigger background manifest download and show progress."""
        api_key = self.settings.value("morrenus_api_key", "", type=str).strip()
        if not api_key:
            QMessageBox.critical(self, "API Key Missing", "Please configure your Hubcap API key in Settings before downloading updates/manifests.")
            return
        if not morrenus_api:
            QMessageBox.critical(self, "Error", "API module missing.")
            return

        app_id = str(game_data.get("appid"))
        if app_id in ("0", "N/A", "unknown"):
            QMessageBox.critical(self, "Error", f"Invalid AppID: {app_id}")
            return

        if not hasattr(self, "_active_fetches"):
            self._active_fetches = set()
        if app_id in self._active_fetches:
            logger.warning(f"Fetch/update already in progress for AppID {app_id}, ignoring duplicate request.")
            return
        self._active_fetches.add(app_id)

        name = game_data.get("game_name", "Unknown")
        status = game_data.get("update_status")

        if not branch:
            branch = morrenus_api.get_selected_branch(app_id)
        logger.info(f"Fetching manifest for {name} ({app_id}) on branch '{branch}'")

        game_data = dict(game_data)
        game_data["branch"] = branch
        self.settings.setValue(f"selected_branch/{app_id}", branch)

        is_rollback = local_path_override is not None

        # Curated / Recommended Builds Check ("Voices")
        if not download_only and not is_rollback:
            try:
                from managers.voices_manager import VoicesManager
                vm = VoicesManager.get_instance()
                rec = vm.get_recommendation(app_id, name)
                if rec and rec.get("recommended_build_id"):
                    rec_bid = str(rec["recommended_build_id"]).strip()
                    current_bid = str(game_data.get("buildid") or "").strip()
                    is_pinned = self.settings.value(f"pin_build/{app_id}", False, type=bool)
                    if not is_pinned or (current_bid and current_bid != rec_bid):
                        from ui.dialogs.recommended_build_dialog import (
                            RecommendedBuildPromptDialog,
                            ACTION_RECOMMENDED,
                            ACTION_LATEST,
                            ACTION_BROWSE,
                        )
                        accent = getattr(self, "accent_color", "#4c8df5")
                        prompt = RecommendedBuildPromptDialog(
                            parent=self,
                            app_id=str(app_id),
                            game_name=name,
                            recommended_build_id=rec_bid,
                            current_build_id=current_bid,
                            reason=rec.get("reason", ""),
                            accent_color=accent,
                        )
                        prompt.exec()
                        action = prompt.get_action()
                        if action == ACTION_RECOMMENDED:
                            game_data["_pin_build"] = True
                            game_data["_recommended_build_id"] = rec_bid
                            game_data["_rec_depots"] = rec.get("depots", [])
                            game_data["_rec_overrides"] = rec.get("manifest_overrides", {})
                            logger.info(f"[LibraryActions] User chose recommended build {rec_bid} for {name} ({app_id})")
                        elif action == ACTION_BROWSE:
                            from ui.dialogs.build_selection_dialog import BuildSelectionDialog
                            dlg = BuildSelectionDialog(
                                parent=self,
                                app_id=str(app_id),
                                game_name=name,
                                current_build_id=current_bid,
                                accent_color=accent,
                            )
                            if dlg.exec():
                                chosen_bid, patch_depots = dlg.get_selected_build()
                                if chosen_bid:
                                    game_data["_pin_build"] = True
                                    game_data["_recommended_build_id"] = chosen_bid
                                    game_data["_rec_depots"] = []
                                    game_data["_rec_overrides"] = patch_depots
                            else:
                                if hasattr(self, "_active_fetches"):
                                    self._active_fetches.discard(app_id)
                                return
                        elif action == ACTION_LATEST:
                            logger.info(f"[LibraryActions] User chose latest manifest build for {name} ({app_id})")
                        else:
                            if hasattr(self, "_active_fetches"):
                                self._active_fetches.discard(app_id)
                            return
            except Exception as _rec_err:
                logger.warning(f"[LibraryActions] Error checking voices recommendation: {_rec_err}")

        # Local zip path (for Verify or Rollback)
        fpath = morrenus_api.get_manifest_zip_path(app_id, branch)
        is_fresh = self.settings.value(f"manifest_is_fresh/{app_id}", False, type=bool)

        local_path = None
        if is_rollback and local_path_override and Path(local_path_override).exists():
            local_path = local_path_override
        elif fpath.exists() and (status != "update_available" or is_fresh):
            local_path = str(fpath)

        if local_path and not download_only:
            logger.info(f"Using local manifest zip for verify/rollback: {local_path}")
            if hasattr(self, "_active_fetches"):
                self._active_fetches.discard(app_id)
            self._submit_job(local_path, game_data, dialog)
            return

        # Smart Update Mode routing
        smart_mode = True
        if smart_mode and not is_rollback and not download_only and status == "update_available":
            try:
                from managers.depot_key_manager import DepotKeyManager
                dkm = DepotKeyManager()
                if dkm.has_depot_keys(app_id):
                    logger.info(f"[Smart Update] Routing {name} ({app_id}) branch '{branch}' through SmartUpdateTask")
                    self._handle_smart_update(app_id, name, game_data, dialog, branch=branch)
                    return
                else:
                    logger.warning(
                        f"[Smart Update] {name} ({app_id}): no cached keys — "
                        "falling back to classic path. Run a full manifest fetch to enable Smart Mode."
                    )
                    QTimer.singleShot(0, lambda: QMessageBox.information(
                        self,
                        "Smart Update Mode",
                        f"Smart Update Mode is enabled but {name} has no cached depot keys yet.\n\n"
                        "Using classic fetch. Smart Mode will activate automatically after the first successful fetch."
                    ))
            except Exception as _smart_err:
                logger.error(f"[Smart Update] Smart mode routing error for {app_id}: {_smart_err}")

        if not local_path:
            if download_only:
                game_data = game_data.copy()
                game_data["_download_only"] = True
            self._handle_download_manifest(app_id, name, game_data, dialog, branch=branch)
        else:
            if not download_only:
                if is_rollback:
                    game_data = game_data.copy()
                    game_data["_is_rollback"] = True
                self._submit_job(local_path, game_data, dialog)

    def _handle_smart_update(self, app_id: str, name: str, game_data: dict, dialog, branch: str = "public") -> None:
        """Handles a Smart Update Mode fetch via SmartUpdateTask."""
        from core.tasks.smart_update_task import SmartUpdateTask
        from utils.task_runner import TaskRunner

        logger.info(f"[Smart Update] Starting SmartUpdateTask for {name} ({app_id})")

        task = SmartUpdateTask(app_id, name, branch=branch)
        runner = TaskRunner(self)

        if not hasattr(self, "_smart_runners"):
            self._smart_runners = []
        self._smart_runners.append(runner)

        def on_smart_finished(assembled_game_data: dict):
            logger.info(f"[Smart Update] SmartUpdateTask finished for {name} ({app_id}) — submitting job")
            merged = dict(game_data)
            merged.update(assembled_game_data)
            tmp_path = assembled_game_data.get("zip_path") or str(morrenus_api.get_manifest_zip_path(app_id, branch))
            self._submit_job(tmp_path, merged, dialog)
            self.settings.setValue(f"manifest_is_fresh/{app_id}", True)
            if assembled_game_data.get("buildid"):
                self.settings.setValue(f"fetched_buildid/{app_id}", assembled_game_data["buildid"])
            if runner in self._smart_runners:
                self._smart_runners.remove(runner)

        def on_needs_full_zip(reason: str):
            logger.warning(f"[Smart Update] {name} ({app_id}) needs full zip: {reason}")
            self._handle_download_manifest(app_id, name, game_data, dialog, branch=branch)
            if runner in self._smart_runners:
                self._smart_runners.remove(runner)

        def on_error(err_msg: str):
            logger.error(f"[Smart Update] Error for {name} ({app_id}): {err_msg}")
            QMessageBox.warning(self, "Smart Update Failed", f"Smart update failed for {name}:\n{err_msg}\n\nFalling back to classic fetch.")
            self._handle_download_manifest(app_id, name, game_data, dialog, branch=branch)
            if runner in self._smart_runners:
                self._smart_runners.remove(runner)

        task.finished.connect(on_smart_finished)
        task.needs_full_zip.connect(on_needs_full_zip)
        task.error.connect(on_error)
        task.progress.connect(lambda msg: logger.info(msg))

        runner.run(task.run)

    def _handle_download_manifest(self, app_id, name, game_data, dialog, branch: str = None) -> None:
        """Logic separated to flatten nesting in fetch_game_manifest."""
        if not morrenus_api:
            QMessageBox.critical(self, "Error", "API module missing.")
            return

        if not branch:
            branch = game_data.get("branch") or morrenus_api.get_selected_branch(app_id)

        download_only = game_data.get("_download_only", False)

        if not download_only:
            self._download_progress_dialog = QProgressDialog(
                f"Downloading {name}...", "Cancel", 0, 0, self
            )
            self._download_progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            self._download_progress_dialog.show()

        self.executor.submit(self._download_manifest_async, app_id, game_data, branch)

    def _download_manifest_async(self, app_id: str, game_data: dict, branch: str = "public") -> None:
        """Background task to download manifest."""
        try:
            fpath, error = morrenus_api.download_manifest(app_id, branch=branch)
            self.manifest_download_complete.emit(
                str(fpath) if fpath else "", str(error) if error else "", game_data
            )
        except Exception as e:
            self.manifest_download_complete.emit("", str(e), game_data)

    def _on_manifest_download_complete(
        self, fpath: str, error: str, game_data: dict
    ) -> None:
        """Slot to handle manifest download completion."""
        appid = str(game_data.get("appid"))
        if hasattr(self, "_active_fetches"):
            self._active_fetches.discard(appid)

        if self._download_progress_dialog:
            self._download_progress_dialog.close()
            self._download_progress_dialog = None

        download_only = game_data.get("_download_only", False)

        if hasattr(self, "_configure_depots_after_download") and self._configure_depots_after_download:
            target_game = self._configure_depots_after_download
            self._configure_depots_after_download = None
            if fpath:
                appid = str(target_game.get("appid"))
                self.settings.setValue(f"manifest_is_fresh/{appid}", True)
                latest_id = self.settings.value(f"latest_steam_manifest_id/{appid}", "", type=str)
                if latest_id:
                    self.settings.setValue(f"fetched_manifest_id/{appid}", latest_id)
                self._show_depot_selection_dialog(fpath, target_game)
            else:
                if not download_only:
                    QMessageBox.critical(self, "Error", f"Failed to download manifest: {error}")
                else:
                    logger.error(f"Background manifest download failed: {error}")
            return

        if fpath:
            appid = str(game_data.get("appid"))
            self.settings.setValue(f"manifest_is_fresh/{appid}", True)
            latest_id = self.settings.value(f"latest_steam_manifest_id/{appid}", "", type=str)
            if latest_id:
                self.settings.setValue(f"fetched_manifest_id/{appid}", latest_id)

            if download_only:
                try:
                    from core.tasks.process_zip_task import ProcessZipTask
                    zip_task = ProcessZipTask()
                    zip_task.run(fpath)
                    logger.info(f"Refetch complete: parsed zip and imported keys/token for appid {appid}")
                except Exception as e:
                    logger.error(f"Failed to parse zip and import keys/token for appid {appid}: {e}")
                
                name = game_data.get("game_name", f"App {appid}")
                QMessageBox.information(self, "Refetch Manifest", f"Refetched manifest zip successfully for {name}!")
            
            if not download_only and self._details_dialog:
                try:
                    self._submit_job(fpath, game_data, self._details_dialog)
                except RuntimeError:
                    pass
            elif download_only and self._details_dialog:
                try:
                    if hasattr(self._details_dialog, "validate_btn") and self._details_dialog.validate_btn:
                        self._details_dialog.validate_btn.set_loading(False)
                        self._details_dialog.validate_btn.setEnabled(True)
                    self._details_dialog._update_validate_button()
                except RuntimeError:
                    pass
        else:
            if not download_only:
                QMessageBox.critical(self, "Error", f"Failed: {error}")
            else:
                name = game_data.get("game_name", f"App {game_data.get('appid')}")
                QMessageBox.critical(self, "Refetch Manifest Error", f"Failed to refetch manifest for {name}:\n{error}")
                logger.error(f"Background manifest download failed for appid {game_data.get('appid')}: {error}")
            if self._details_dialog:
                try:
                    if hasattr(self._details_dialog, "validate_btn") and self._details_dialog.validate_btn:
                        self._details_dialog.validate_btn.set_loading(False)
                        self._details_dialog.validate_btn.setEnabled(True)
                    self._details_dialog._update_validate_button()
                except RuntimeError:
                    pass

    def _check_hubcap_status_first(self, app_id: str, game_data: dict, dialog: QDialog) -> None:
        """Runs the Stage 1 pre-download Hubcap status check asynchronously."""
        check_progress = QProgressDialog("Checking Hubcap status...", None, 0, 0, self)
        check_progress.setWindowModality(Qt.WindowModality.WindowModal)
        check_progress.setCancelButton(None)
        check_progress.show()

        def _check_in_background():
            try:
                res = morrenus_api.get_manifest_status(app_id)
            except Exception as e:
                logger.error(f"Error checking Hubcap status in background: {e}")
                res = {"error": str(e)}
            self.hubcap_status_check_complete.emit(res, game_data, dialog, check_progress)

        self.executor.submit(_check_in_background)

    def _on_hubcap_status_check_complete(self, result: dict, game_data: dict, dialog: QDialog, check_progress: object) -> None:
        """Handles completion of Stage 1 check on the main thread."""
        if check_progress:
            try:
                check_progress.close()
            except Exception:
                pass

        app_id = str(game_data.get("appid"))
        name = game_data.get("game_name", "Unknown")

        needs_update = result.get("needs_update", False) if isinstance(result, dict) else False
        update_in_progress = result.get("update_in_progress", False) if isinstance(result, dict) else False
        is_error = "error" in result if isinstance(result, dict) else True

        if is_error:
            logger.warning(f"Hubcap status check returned error or invalid result for {app_id}: {result}")
            self._handle_download_manifest(app_id, name, game_data, dialog)
            return

        is_refined = False
        is_timestamp_stale = False
        timestamp_reason = ""
        if not (needs_update or update_in_progress) and is_refined:
            from utils.manifest_verifier import verify_hubcap_freshness
            ver_status, reason, _ = verify_hubcap_freshness(app_id, result)
            if ver_status == "stale":
                is_timestamp_stale = True
                timestamp_reason = reason

        if needs_update or update_in_progress or is_timestamp_stale:
            msg = (
                f"Hubcap reports that its manifest for '{name}' is currently outdated/stale.\n\n"
                "If you proceed, you might download the old version instead of the latest Steam update.\n\n"
            )
            if update_in_progress:
                msg += "Hubcap is currently processing/fetching the update. Please try again in a few minutes.\n\n"
            elif is_timestamp_stale:
                msg += f"Refined Update Check: {timestamp_reason}\n\n"
            else:
                msg += "Hubcap is aware of the new Steam update, but has not ingested the new manifest yet.\n\n"

            msg += "Do you want to continue anyway?"
            
            btn = QMessageBox.warning(
                self, 
                "Hubcap Manifest Not Ready", 
                msg, 
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if btn != QMessageBox.StandardButton.Yes:
                logger.info("User cancelled download due to stale Hubcap manifest")
                if hasattr(self, "_active_fetches"):
                    self._active_fetches.discard(app_id)
                return

        self._handle_download_manifest(app_id, name, game_data, dialog)

    def _submit_job(self, filepath: str, game_data: dict, dialog: QDialog) -> None:
        """Submit the job to the main window queue."""
        if game_data.get("_smart_update"):
            logger.info(f"[Smart Update] _submit_job: using pre-assembled game_data, skipping zip parse for AppID {game_data.get('appid')}")
            parse_progress = QProgressDialog("Applying manifest...", None, 0, 0, self)
            parse_progress.setWindowModality(Qt.WindowModality.WindowModal)
            parse_progress.setMinimumDuration(0)
            parse_progress.setCancelButton(None)
            parse_progress.show()
            self.zip_parse_complete.emit(game_data, filepath, game_data, dialog, parse_progress)
            return

        parse_progress = QProgressDialog("Reading manifest...", None, 0, 0, self)
        parse_progress.setWindowModality(Qt.WindowModality.WindowModal)
        parse_progress.setMinimumDuration(200)
        parse_progress.setCancelButton(None)
        parse_progress.show()

        def _parse_in_background():
            try:
                from core.tasks.process_zip_task import ProcessZipTask
                zip_task = ProcessZipTask()
                parsed = zip_task.run(filepath)
            except Exception as e:
                logger.warning(f"Failed to pre-parse zip for depot selection: {e}", exc_info=True)
                parsed = None
            self.zip_parse_complete.emit(parsed, filepath, game_data, dialog, parse_progress)

        self.executor.submit(_parse_in_background)

    def _on_zip_parse_complete(
        self, parsed_data: object, filepath: str, game_data: dict, dialog: QDialog,
        parse_progress: object = None
    ) -> None:
        """Slot called on the main thread when background zip parsing is done."""
        appid = str(game_data.get("appid"))
        if hasattr(self, "_active_fetches"):
            self._active_fetches.discard(appid)
        if parse_progress is not None:
            try:
                parse_progress.close()
            except Exception:
                pass

        is_verify = (game_data.get("update_status") != "update_available")
        target_branch = game_data.get("branch") or (parsed_data.get("branch") if isinstance(parsed_data, dict) else "public") or "public"
        metadata = dict(game_data)
        metadata.update({
            "appid": game_data.get("appid"),
            "library_path": game_data.get("library_path"),
            "install_path": game_data.get("install_path"),
            "game_name": game_data.get("game_name", "Unknown"),
            "job_type": "verify" if is_verify else "download",
            "branch": target_branch,
        })
        if game_data.get("_is_rollback"):
            metadata["_is_rollback"] = True
            metadata["job_type"] = "verify"

        if parsed_data:
            is_smart = parsed_data.get("_smart_update") or game_data.get("_smart_update")
            if is_smart:
                logger.info(f"[Smart Update] Skipping Stage 2 manifest verification for AppID {game_data.get('appid')} (trust PICS)")
            else:
                from utils.manifest_verifier import verify_extracted_zip_manifest
                appid = str(game_data.get("appid"))
                is_update = game_data.get("update_status") == "update_available"
                is_valid_stage2, warning_reason = verify_extracted_zip_manifest(appid, parsed_data, is_update=is_update)

                should_warn = False
                if not game_data.get("_is_rollback") and not is_valid_stage2:
                    should_warn = True

                if should_warn:
                    msg = (
                        f"ASSella detected that the manifest is older than the latest Steam version.\n\n"
                        f"Reason: {warning_reason}\n\n"
                        "This usually means Hubcap has not yet ingested the latest Steam update.\n\n"
                        "Do you want to continue with this version anyway?"
                    )
                    btn = QMessageBox.warning(
                        self,
                        "Manifest Outdated",
                        msg,
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No
                    )
                    if btn != QMessageBox.StandardButton.Yes:
                        logger.info("User aborted task due to post-download manifest mismatch")
                        if hasattr(self, "_active_fetches"):
                            self._active_fetches.discard(appid)
                        if dialog and hasattr(dialog, "validate_btn") and dialog.validate_btn:
                            dialog.validate_btn.set_loading(False)
                            dialog.validate_btn.setEnabled(True)
                            if hasattr(dialog, "_update_validate_button_state"):
                                dialog._update_validate_button_state()
                        return

        if parsed_data and parsed_data.get("depots"):
            from ui.dialogs.depotselection import DepotSelectionDialog
            from utils.settings import get_settings
            settings = get_settings()
            auto_skip = settings.value("auto_skip_single_choice", False, type=bool)
            depots = parsed_data.get("depots")
            appid = str(parsed_data["appid"])

            selected_depots = None

            smart_active = settings.value("smart_depot_selection", False, type=bool)
            val = settings.value(f"depot_selection/{appid}", "", type=str)
            should_prompt = True

            cached_selected = None
            if val:
                try:
                    data = json.loads(val)
                    cached_selected = data.get("selected", [])
                    cached_all = data.get("all_available", [])
                    current_depots = list(depots.keys())
                    has_new_depot = any(d not in cached_all for d in current_depots)
                    if smart_active and not has_new_depot:
                        selected_depots = [d for d in cached_selected if d in depots]
                        should_prompt = False
                        logger.info(f"Smart selection active. Reusing cached depots for {appid}: {selected_depots}")
                except Exception as e:
                    logger.warning(f"Error parsing cached depot selection: {e}")

            game_info = (metadata or {}).get("game_data") or {}
            if not cached_selected:
                acf_installed = game_info.get("installed_depots") if isinstance(game_info, dict) else None
                if acf_installed and isinstance(acf_installed, list):
                    cached_selected = [str(d) for d in acf_installed]

            if should_prompt:
                if auto_skip and len(depots) == 1:
                    selected_depots = list(depots.keys())
                else:
                    depot_dialog = DepotSelectionDialog(
                        parsed_data["appid"],
                        parsed_data.get("game_name", ""),
                        depots,
                        parsed_data.get("header_url"),
                        self.main_window,
                        selected_depots=cached_selected,
                        is_single_depot=(len(depots) == 1),
                        missing_hubcap_depots=parsed_data.get("missing_depots_from_hubcap"),
                        missing_depots_info=parsed_data.get("missing_depots_info"),
                        current_build_id=str(game_info.get("buildid") or "").strip() if isinstance(game_info, dict) else "",
                    )

                    # Apply recommended build selection if set
                    if game_data.get("_recommended_build_id"):
                        rec_bid = game_data["_recommended_build_id"]
                        patch_depots = game_data.get("_rec_overrides") or {}
                        if not patch_depots:
                            try:
                                from core.steamdb_scraper import SteamDBBuildsCache, SteamDBScraper
                                cache = SteamDBBuildsCache()
                                c_depots = cache.get_build_depots(rec_bid)
                                if c_depots:
                                    patch_depots = c_depots
                                else:
                                    patch_depots = SteamDBScraper().get_patch_depots(rec_bid) or {}
                            except Exception as _e:
                                logger.warning(f"Failed to resolve SteamDB depots for {rec_bid}: {_e}")
                        depot_dialog._apply_build_selection(rec_bid, patch_depots)

                    if depot_dialog.exec():
                        selected_depots = depot_dialog.get_selected_depots()
                        selected_storage = depot_dialog.get_selected_storage()
                        if selected_storage:
                            metadata["library_path"] = selected_storage

                        if hasattr(depot_dialog, "is_build_pinned") and depot_dialog.is_build_pinned():
                            pinned_bid = depot_dialog.get_selected_build()
                            metadata["pin_build"] = True
                            metadata["pinned_build_id"] = pinned_bid
                            metadata["buildid"] = pinned_bid
                            metadata["is_rollback"] = True
                            if appid:
                                settings.setValue(f"pin_build/{appid}", True)
                                logger.info(f"Pinned build {pinned_bid} for AppID {appid} in library actions")

                        if hasattr(depot_dialog, "get_manifest_overrides"):
                            overrides = depot_dialog.get_manifest_overrides()
                            if overrides:
                                metadata["manifest_overrides"] = overrides

            if game_data.get("_pin_build"):
                rec_bid = game_data.get("_recommended_build_id")
                if rec_bid:
                    metadata["pin_build"] = True
                    metadata["pinned_build_id"] = rec_bid
                    metadata["buildid"] = rec_bid
                    metadata["is_rollback"] = True
                    if appid:
                        settings.setValue(f"pin_build/{appid}", True)
                if game_data.get("_rec_overrides"):
                    metadata.setdefault("manifest_overrides", {}).update(game_data["_rec_overrides"])

            if selected_depots:
                metadata["selected_depots_list"] = selected_depots
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
            else:
                logger.info("User cancelled depot selection.")
                if hasattr(self, "_active_fetches"):
                    self._active_fetches.discard(appid)
                if dialog:
                    try:
                        dialog.accept()
                    except RuntimeError:
                        pass
                try:
                    self.accept()
                except RuntimeError:
                    pass
                return

        self.main_window.job_queue.add_job(filepath, metadata)
        if dialog:
            try:
                dialog.accept()
            except RuntimeError:
                pass
        try:
            self.accept()
        except RuntimeError:
            pass
