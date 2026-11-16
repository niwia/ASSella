import json
import logging
import threading
import zipfile
from pathlib import Path

from PyQt6.QtCore import Q_ARG, QMetaObject, Qt, pyqtSlot

from ui.dialogs.themed_message_box import ThemedMessageBox

logger = logging.getLogger(__name__)

# How long Update All waits for the user to answer a depot-selection dialog
# before giving up on that game. Must comfortably exceed human reaction time —
# a short wait returns while the dialog is still open, which silently skips the
# game while leaving a stale dialog on screen.
DIALOG_WAIT_SECONDS = 300


class MainWindowUpdateAllMixin:
    """Mixin handling Update All batch flows, AT0-M key syncing, and FAB positioning."""

    def position_update_all_btn(self) -> None:
        if hasattr(self, "simplified_terminal") and self.simplified_terminal:
            term = self.simplified_terminal

            # Don't reposition FABs when the active-job view is shown —
            # panel_mid has zero/altered geometry then, causing incorrect placement.
            if hasattr(term, "layout") and term.layout.currentIndex() != 0:
                return

            if not hasattr(term, "panel_mid") or not term.panel_mid:
                return

            w = term.panel_mid.width()
            h = term.panel_mid.height()
            if w < 50 or h < 50:
                return

            # Position refresh button
            if hasattr(term, "refresh_updates_btn") and term.refresh_updates_btn:
                # If update_all_btn is visible, place refresh to its left
                if hasattr(term, "update_all_btn") and term.update_all_btn and term.update_all_btn.isVisible():
                    term.update_all_btn.adjustSize()
                    x_update = max(8, w - term.update_all_btn.width() - 16)
                    y_update = max(8, h - term.update_all_btn.height() - 16)
                    term.update_all_btn.move(x_update, y_update)
                    term.update_all_btn.raise_()

                    x_refresh = max(8, x_update - 36 - 8)
                    y_refresh = max(8, h - 36 - 16)
                    term.refresh_updates_btn.move(x_refresh, y_refresh)
                else:
                    x_refresh = max(8, w - 36 - 16)
                    y_refresh = max(8, h - 36 - 16)
                    term.refresh_updates_btn.move(x_refresh, y_refresh)

                term.refresh_updates_btn.raise_()

    def force_check_all_updates(self) -> None:
        """Forces a clean re-check of updates for all games, ignoring cache."""
        # Guard: don't allow update check while Update All is actively queueing
        if getattr(self, "_update_all_running", False):
            logger.info("Update All is running — ignoring manual update check request")
            return
        if self.game_manager:
            logger.info("Forcing full updates check for all games (bypassing cache)")
            if hasattr(self, "simplified_terminal") and self.simplified_terminal:
                if hasattr(self.simplified_terminal, "refresh_updates_btn") and self.simplified_terminal.refresh_updates_btn:
                    self.simplified_terminal.refresh_updates_btn.set_loading(True)
            # Reset 'up_to_date' / 'cannot_determine' / 'checking' games to 'checking'
            # so they are re-queried. Games already marked 'update_available' are
            # intentionally left intact so the pending-updates list stays populated
            # during the refresh — cache acts as a display layer throughout.
            for g in self.game_manager.games:
                if g.get("update_status") != "update_available":
                    g["update_status"] = "checking"
            self.game_manager.library_updated.emit()

            # Start checks with force_refresh = True so update_available games are
            # re-verified too, even though we kept their visual status intact above.
            self.game_manager.check_game_updates_async(force_refresh=True)

    def sync_at0m_keys(self, appid: str) -> None:
        """Syncs the Hubcap decryption keys of a Steam-managed (AT0-M) game.

        ASSella never downloads an AT0-M game - Steam does. What ASSella does
        is make sure SLSsteam holds the decryption keys of the depots the game
        needs, so Steam can actually decrypt them.
        """
        appid = str(appid)
        running = getattr(self, "_at0m_key_syncs_running", None)
        if running is None:
            running = set()
            self._at0m_key_syncs_running = running
        if appid in running:
            logger.info(f"[AT0-M] A key sync for {appid} is already running.")
            return

        gm = self.game_manager
        game = gm.get_game(appid) if gm else None
        name = (game or {}).get("game_name") or f"App {appid}"

        running.add(appid)
        self._refresh_at0m_sync_rows()

        def _worker():
            try:
                from managers.at0m_update_checker import At0mUpdateChecker
                ok, message = At0mUpdateChecker.get_instance().sync_keys(appid)
            except Exception as e:
                logger.error(f"[AT0-M] Key sync failed for {appid}: {e}", exc_info=True)
                ok, message = False, str(e)
            self._main_thread_callable.emit(
                lambda: self._on_at0m_key_sync_finished(appid, ok, message, name)
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_at0m_sync_rows(self) -> None:
        """Repaints the pending-updates list so a running sync shows as busy."""
        if hasattr(self, "simplified_terminal") and self.simplified_terminal:
            self.simplified_terminal.update_stats(immediate=True)

    def _on_at0m_key_sync_finished(self, appid: str, ok: bool, message: str, name: str) -> None:
        """Reports the outcome of a key sync to the user."""
        running = getattr(self, "_at0m_key_syncs_running", None)
        if running is not None:
            running.discard(str(appid))

        if ok:
            if hasattr(self, "status_pager") and self.status_pager:
                self.status_pager.set_status(f"KEYS SYNCED · {name}")
            ThemedMessageBox.information(self, "Keys Synced", message)
        else:
            logger.warning(f"[AT0-M] Key sync failed for {name}: {message}")
            ThemedMessageBox.warning(self, "Key Sync Failed", message)

        self._refresh_at0m_sync_rows()

    @pyqtSlot(str)
    def _show_key_sync_notice(self, names_text: str) -> None:
        """Tells the user that the keys are in place but Steam does the download."""
        ThemedMessageBox.information(
            self,
            "Keys Synced",
            f"Keys synced for:\n{names_text}\n\n"
            f"Launch or update the game(s) in Steam to finish.",
        )

    def run_update_all_flow(self) -> None:
        """Flow for updating all games that have update_available status."""
        # Guard: prevent re-entrancy if a cycle is already running
        if getattr(self, "_update_all_running", False):
            return
        # Guard: don't start Update All while an update check is still running
        gm = self.game_manager
        if gm and (getattr(gm, "manifest_check_task", None) is not None or getattr(gm, "manifest_check_runner", None) is not None):
            ThemedMessageBox.information(
                self,
                "Update Check Running",
                "Please wait for the update check to finish before queuing updates.",
            )
            return
        self._update_all_running = True

        if not self.game_manager:
            self._update_all_running = False
            return

        games = self.game_manager.get_all_games()
        updateable_games = []
        at0m_key_games = []
        from utils.game_modes import is_atom_managed
        for g in games:
            if g.get("update_status") == "update_available":
                appid = str(g.get("appid", ""))
                if self.settings.value(f"exclude_from_update_all/{appid}", False, type=bool):
                    continue
                if self.settings.value(f"pin_build/{appid}", False, type=bool):
                    continue
                if is_atom_managed(g):
                    # Steam-managed games are downloaded by Steam, never by
                    # DepotDownloader. All ASSella does is sync the keys.
                    at0m_key_games.append(g)
                else:
                    updateable_games.append(g)

        if not updateable_games and not at0m_key_games:
            self._update_all_running = False
            ThemedMessageBox.information(
                self,
                "No Updates Available",
                "All games in your library are up to date!",
            )
            return

        total = len(updateable_games) + len(at0m_key_games)

        # --- Immediate feedback: update button before the thread even starts ---
        self._set_update_all_btn_preparing(0, total)

        # Enqueue all updates directly on a background thread (no intermediate dialog)
        def _do_update_all():
            from core import morrenus_api as _api
            from core.tasks.process_zip_task import ProcessZipTask
            from ui.dialogs.gamelibrary import format_game_display_name
            from utils.settings import get_settings

            settings = get_settings()
            queued = 0
            skipped_names = []
            synced_names = []
            key_processed = 0

            # --- Phase 1: sync the keys of the Steam-managed games ---
            # Steam downloads those games itself, so all ASSella can do is
            # make sure SLSsteam holds the decryption keys.
            if at0m_key_games:
                from managers.at0m_update_checker import At0mUpdateChecker
                checker = At0mUpdateChecker.get_instance()
                for key_game in at0m_key_games:
                    key_appid = str(key_game.get("appid", "0"))
                    key_name = format_game_display_name(key_game)
                    self._set_update_all_btn_preparing(
                        key_processed, total, current_name=key_name
                    )
                    logger.info(f"[Update All] Syncing keys for {key_name} ({key_appid})...")
                    try:
                        ok, message = checker.sync_keys(key_appid)
                    except Exception as e:
                        logger.error(f"[Update All] Key sync failed for {key_name}: {e}")
                        ok, message = False, str(e)
                    key_processed += 1
                    if ok:
                        synced_names.append(key_name)
                        logger.info(f"[Update All] Keys synced for {key_name}")
                    else:
                        logger.warning(f"[Update All] Key sync failed for {key_name}: {message}")
                        skipped_names.append(key_name)

            for idx, game_data in enumerate(updateable_games):
                appid = str(game_data.get("appid", "0"))
                name = format_game_display_name(game_data)
                update_status = game_data.get("update_status")
                self._set_update_all_btn_preparing(key_processed + idx, total, current_name=name)
                logger.info(f"[Update All] Preparing {name} ({idx + 1}/{total})...")
                try:
                    local_path = None
                    branch = _api.get_selected_branch(appid)
                    fpath = _api.get_manifest_zip_path(appid, branch)

                    is_fresh = settings.value(f"manifest_is_fresh/{appid}", False, type=bool)
                    if fpath.exists() and (update_status != "update_available" or is_fresh):
                        local_path = str(fpath)

                    parsed_data = None
                    from managers.depot_key_manager import DepotKeyManager
                    dkm = DepotKeyManager()

                    # 1. Try Smart Update Path
                    if dkm.has_depot_keys(appid):
                        from core.tasks.smart_update_task import SmartUpdateTask
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
                        # Ensure we have a valid classic zip (must contain .lua)
                        def has_lua(zp):
                            try:
                                with zipfile.ZipFile(zp, "r") as z:
                                    return any(f.endswith(".lua") for f in z.namelist())
                            except Exception as e:
                                logger.debug(f"[Update All] Could not inspect zip {zp} for .lua files: {e}")
                                return False

                        if not local_path or not has_lua(local_path):
                            logger.info(f"Update All: Fetching classic manifest for {name} (branch={branch})")
                            if local_path and Path(local_path).exists():
                                Path(local_path).unlink()  # Delete bad/lua-less zip
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

                    metadata = dict(game_data)
                    metadata.update({
                        "appid": appid,
                        "library_path": game_data.get("library_path"),
                        "install_path": game_data.get("install_path"),
                        "game_name": name,
                        "branch": branch,
                    })

                    if parsed_data and parsed_data.get("depots"):
                        depots = parsed_data.get("depots")
                        selected_depots = None
                        selected_dlc_appids = None
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
                                    # New depot added since last selection — clear cache and force re-prompt
                                    logger.info(f"Update All: new depot detected for {name} (appid={appid}), prompting user")
                                    settings.remove(f"depot_selection/{appid}")
                                else:
                                    selected_depots = [d for d in cached_selected if d in depots]
                                    if selected_depots:
                                        should_prompt = False
                            except Exception as e:
                                logger.debug(f"[Update All] Could not parse cached depot selection for {appid}: {e}")

                        if should_prompt:
                            auto_skip = settings.value("auto_skip_single_choice", True, type=bool)
                            from utils.dlc_helpers import is_dlc_only_context
                            if auto_skip and len(depots) == 1 and not is_dlc_only_context(appid):
                                selected_depots = list(depots.keys())
                            else:
                                # Depot dialog must run on the main thread
                                from ui.dialogs.depotselection import DepotSelectionDialog
                                result_holder = [None, None]
                                done_event = threading.Event()

                                # Bind every loop-scoped value as a default
                                # argument. The emit below is a queued
                                # cross-thread signal, so if the wait below
                                # times out the callback can still run after this
                                # iteration has finished and the loop has moved
                                # on — a plain closure would then build the dialog
                                # from the *last* iteration's game.
                                def _show_depot_dialog(
                                    _appid=appid,
                                    _game_name=parsed_data.get("game_name", name),
                                    _depots=depots,
                                    _header_url=parsed_data.get("header_url"),
                                    _prev_selected=prev_selected,
                                    _missing_hubcap=parsed_data.get("missing_depots_from_hubcap"),
                                    _missing_info=parsed_data.get("missing_depots_info"),
                                    _refetched=parsed_data.get("refetched_depots"),
                                    _lib_path=game_data.get("library_path") or game_data.get("install_path"),
                                    _result_holder=result_holder,
                                    _done_event=done_event,
                                ):
                                    try:
                                        depot_dialog = DepotSelectionDialog(
                                            _appid,
                                            _game_name,
                                            _depots,
                                            _header_url,
                                            self,
                                            selected_depots=_prev_selected,
                                            is_single_depot=(len(_depots) == 1),
                                            missing_hubcap_depots=_missing_hubcap,
                                            missing_depots_info=_missing_info,
                                            refetched_depots=_refetched,
                                            library_path=_lib_path,
                                            lock_storage=True,
                                        )
                                        if depot_dialog.exec():
                                            _result_holder[0] = depot_dialog.get_selected_depots()
                                            _result_holder[1] = list(
                                                depot_dialog.get_selected_dlc_data().keys()
                                            )
                                    except Exception as err:
                                        logger.error(
                                            f"Error displaying depot selection dialog for {_appid} "
                                            f"in Update All: {err}"
                                        )
                                    finally:
                                        _done_event.set()

                                self._main_thread_callable.emit(_show_depot_dialog)
                                # The dialog is modal and needs real user
                                # interaction, so this must wait far longer than
                                # a dispatch timeout. A short wait here returned
                                # before the user had chosen anything, which made
                                # the game look uncancellable-free but actually
                                # skipped it ("no depots selected") and left the
                                # dialog running against a stale iteration.
                                # Only a hard failure should give up early;
                                # otherwise wait for the user.
                                if not done_event.wait(timeout=DIALOG_WAIT_SECONDS):
                                    logger.warning(
                                        f"Update All: depot dialog for {appid} did not report back "
                                        f"within {DIALOG_WAIT_SECONDS}s; skipping this game"
                                    )
                                    selected_depots = None
                                else:
                                    selected_depots = result_holder[0]
                                    selected_dlc_appids = result_holder[1] or []

                        if not selected_depots:
                            logger.info(f"Update All: skipping {name} — depot selection cancelled or no depots selected")
                            skipped_names.append(name)
                            continue

                        # Persist confirmed selection
                        try:
                            settings.setValue(
                                f"depot_selection/{appid}",
                                json.dumps({
                                    "selected": selected_depots,
                                    "all_available": list(depots.keys()),
                                    "descriptions": {d_id: depots.get(d_id, {}).get("desc", "") for d_id in selected_depots},
                                }),
                            )
                        except Exception as e:
                            logger.warning(f"Failed to cache depot selection: {e}")

                        metadata["selected_depots_list"] = selected_depots
                        if selected_dlc_appids is not None:
                            metadata["selected_dlc_appids"] = selected_dlc_appids

                    self.job_queue.add_job(local_path, metadata)
                    queued += 1
                    logger.info(f"Update All queued: {name}")

                    # Update button progress after each successful queue
                    self._set_update_all_btn_preparing(key_processed + queued, total)

                except Exception as e:
                    logger.error(f"Update All failed for {name}: {e}", exc_info=True)
                    skipped_names.append(name)

            logger.info(f"[Update All]: queued {queued} of {total} games.")

            # Release guard and refresh UI
            self._update_all_running = False
            QMetaObject.invokeMethod(self, "update_dashboard_elements", Qt.ConnectionType.QueuedConnection)

            # Tell the user that the keys are in place but Steam does the download
            if synced_names:
                QMetaObject.invokeMethod(
                    self,
                    "_show_key_sync_notice",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, "\n".join(f"• {n}" for n in synced_names)),
                )

            # Notify user about any skipped games
            if skipped_names and queued == 0:
                QMetaObject.invokeMethod(
                    self,
                    "_show_update_all_skip_notice",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, "\n".join(f"• {n}" for n in skipped_names)),
                    Q_ARG(bool, True),
                    Q_ARG(bool, bool(at0m_key_games) and not updateable_games),
                )
            elif skipped_names:
                QMetaObject.invokeMethod(
                    self,
                    "_show_update_all_skip_notice",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, "\n".join(f"• {n}" for n in skipped_names)),
                    Q_ARG(bool, False),
                    Q_ARG(bool, False),
                )

        threading.Thread(target=_do_update_all, daemon=True).start()

    @pyqtSlot(object)
    def _run_on_main_thread(self, fn) -> None:
        """Slot to execute a callable on the main thread (connected via _main_thread_callable signal)."""
        try:
            fn()
        except Exception as e:
            logger.error(f"Error executing on main thread: {e}", exc_info=True)

    def _set_update_all_btn_preparing(self, done: int, total: int, current_name: str = "") -> None:
        """Thread-safe: update the Update All button to show preparation progress."""
        if threading.current_thread() is not threading.main_thread():
            QMetaObject.invokeMethod(
                self,
                "_set_update_all_btn_preparing_slot",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(int, done),
                Q_ARG(int, total),
                Q_ARG(str, current_name),
            )
        else:
            self._set_update_all_btn_preparing_slot(done, total, current_name)

    @pyqtSlot(int, int)
    @pyqtSlot(int, int, str)
    def _set_update_all_btn_preparing_slot(self, done: int, total: int, current_name: str = "") -> None:
        """Main-thread slot: keep the Update All button disabled and greyed out while queueing updates."""
        try:
            # The button lives on the dashboard widget, not on the ui_state.
            term = getattr(self, "simplified_terminal", None)
            btn = getattr(term, "update_all_btn", None) if term else None
            if btn:
                btn.setEnabled(False)
                if done < total:
                    if current_name:
                        btn.setToolTip(f"Preparing updates... ({done} of {total} ready - {current_name})")
                    else:
                        btn.setToolTip(f"Preparing updates... ({done} of {total} ready)")
                else:
                    btn.setToolTip("Queueing updates...")
        except Exception as e:
            logger.debug(f"[Update All] Could not update button preparing state: {e}")

    @pyqtSlot(str, bool)
    @pyqtSlot(str, bool, bool)
    def _show_update_all_skip_notice(self, names_text: str, all_skipped: bool, key_sync_only: bool = False) -> None:
        """Show a non-blocking notice when some games were skipped in Update All."""
        if key_sync_only:
            ThemedMessageBox.warning(
                self,
                "Update All — Keys Not Synced",
                f"The decryption keys could not be synced for:\n{names_text}\n\n"
                f"Open the game individually to retry.",
            )
        elif all_skipped:
            ThemedMessageBox.warning(
                self,
                "Update All — Manual Action Required",
                f"All pending updates were skipped because they require manual depot selection.\n\n"
                f"Please update these games individually:\n{names_text}",
            )
        else:
            ThemedMessageBox.information(
                self,
                "Update All — Some Games Skipped",
                f"Updates were queued, but the following game(s) need manual depot selection:\n\n"
                f"{names_text}\n\nOpen them individually to install the update.",
            )