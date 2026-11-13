"""Game update checking, AT0-M stage coordination, and Hubcap status mixin."""

import logging
import threading

from PyQt6.QtCore import QTimer

from utils.settings import get_settings
from utils.task_runner import TaskRunner
from utils.update_status_cache import get_update_cache

logger = logging.getLogger(__name__)

# Update status constants
UPDATE_STATUS = {
    "UPDATE_AVAILABLE": "update_available",
    "UP_TO_DATE": "up_to_date",
    "CANNOT_DETERMINE": "cannot_determine",
    "CHECKING": "checking",  # While async update check is running
    "VAPOR": "at0m",
    "AT0M": "at0m",
}

_SINGLE_CHECK_TIMEOUT_MS = 90_000


class GameManagerUpdatesMixin:
    """Handles batch and single-game update checks, AT0-M follow-up checks, and Hubcap status."""

    def _register_single_check_runner(
        self,
        runner: TaskRunner,
        worker=None,
        timeout_ms: int = _SINGLE_CHECK_TIMEOUT_MS,
    ):
        """
        Register a single-check TaskRunner and return an idempotent unregister callback
        that removes it on completion, error, or timeout.
        """
        if not hasattr(self, "_runners_lock"):
            self._runners_lock = threading.Lock()

        with self._runners_lock:
            self._single_check_runners.append(runner)

        cleaned = {"done": False}
        timer_holder = {"timer": None}

        def _unregister() -> None:
            with self._runners_lock:
                if cleaned["done"]:
                    return
                cleaned["done"] = True
                self._single_check_runners[:] = [
                    r for r in self._single_check_runners if r is not runner
                ]

            timer = timer_holder.get("timer")
            if timer is not None:
                try:
                    timer.stop()
                    timer.deleteLater()
                except Exception as e:
                    logger.debug(f"Error cleaning up single-check timeout timer: {e}")

        def _on_timeout() -> None:
            logger.warning("Single-game check runner timed out; stopping and unregistering")
            try:
                runner.stop(wait_ms=0, terminate_on_timeout=False)
            except Exception as e:
                logger.debug(f"Error stopping timed-out single-check runner: {e}")
            _unregister()

        try:
            runner.cleanup_complete.connect(_unregister)
        except Exception as e:
            logger.debug(f"Could not connect cleanup_complete on single-check runner: {e}")

        if worker is not None:
            try:
                worker.finished.connect(_unregister)
            except Exception as e:
                logger.debug(f"Could not connect worker.finished on single-check runner: {e}")

        if timeout_ms > 0:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(_on_timeout)
            timer_holder["timer"] = timer
            timer.start(timeout_ms)

        return _unregister

    def _stop_single_check_runners(self) -> None:
        """Stop and clear all in-flight single-game check runners."""
        if not hasattr(self, "_runners_lock"):
            self._runners_lock = threading.Lock()

        with self._runners_lock:
            runners = list(self._single_check_runners)
            self._single_check_runners.clear()

        for runner in runners:
            try:
                runner.stop(wait_ms=0, terminate_on_timeout=False)
            except Exception as e:
                logger.debug(f"Error stopping single-check runner: {e}")

    def reset_up_to_date_for_recheck(self) -> None:
        """
        Reset 'up_to_date' games back to 'checking' so the next batch check
        re-validates them. Called exclusively by the periodic update timer.
        Games with 'update_available' are left untouched.
        """
        reset_count = 0
        settings = get_settings()
        for game in self.games:
            appid = game.get("appid")
            if appid and settings.value(f"pin_build/{appid}", False, type=bool):
                continue
            if game.get("update_status") == UPDATE_STATUS["UP_TO_DATE"]:
                game["update_status"] = UPDATE_STATUS["CHECKING"]
                reset_count += 1
        if reset_count:
            logger.info(
                f"Periodic recheck: reset {reset_count} 'up_to_date' game(s) to 'checking'"
            )

    def check_game_updates_async(self, is_periodic: bool = False, force_refresh: bool = False):
        """
        Start async update checking for games that still need a check.

        Smart skip logic:
        - If force_refresh is True, we check ALL games (ignoring cache/update status).
        - Otherwise, we only skip games that are already marked 'update_available'.
        - 'up_to_date' entries in the cache are ignored for checks but act as a UI middleman.
        """
        if (
            self.manifest_check_task is not None
            or self.manifest_check_runner is not None
        ):
            logger.info("Cancelling previous manifest check task")
            self.cancel_update_checks()

        active_appids = set()
        main_win = self.main_window or self.parent()
        if main_win:
            if getattr(main_win, "task_manager", None) and getattr(
                main_win.task_manager, "is_processing", False
            ):
                gd = getattr(main_win.task_manager, "game_data", None)
                if gd and gd.get("appid"):
                    active_appids.add(str(gd.get("appid")))
            if getattr(main_win, "job_queue", None):
                for job in getattr(main_win.job_queue, "job_queue", []):
                    meta_aid = job.get("metadata", {}).get("appid")
                    if meta_aid:
                        active_appids.add(str(meta_aid))

        games_to_check = []
        settings = get_settings()
        for g in self.games:
            appid = g.get("appid")
            if appid in ("0", "N/A", "unknown"):
                continue

            if str(appid) in active_appids:
                logger.debug(
                    f"Skipping update check for actively downloading/queued appid={appid}"
                )
                continue

            is_atom = g.get("source") == "at0-m" or (
                g.get("is_atom") and not g.get("accela_marker_path")
            )
            if is_atom and not (
                g.get("accela_marker_path") or g.get("source") == "ACCELA"
            ):
                logger.debug(
                    f"Skipping update check for AT0-M/Plugin game: {g.get('game_name')} ({appid})"
                )
                continue

            if settings.value(f"pin_build/{appid}", False, type=bool):
                g["update_status"] = UPDATE_STATUS["UP_TO_DATE"]
                self.game_update_status_changed.emit(appid, UPDATE_STATUS["UP_TO_DATE"])
                continue

            status = g.get("update_status", "")
            if not force_refresh and status == UPDATE_STATUS["UPDATE_AVAILABLE"]:
                continue
            games_to_check.append(g)

        self._games_to_check = games_to_check

        if not self._games_to_check:
            logger.info("No games need an update check at this time")
            self.all_updates_checked.emit()
            self._update_checks_finished_emitted = False
            self._start_at0m_update_check(force_refresh=force_refresh)
            return

        logger.info(
            f"Starting async update check for {len(self._games_to_check)} game(s) "
            f"(skipped {len(self.games) - len(self._games_to_check)} with known status)"
        )

        self.update_check_started.emit()

        self._pending_at0m_check = force_refresh
        self._update_checks_finished_emitted = False

        from core.tasks.manifest_check_task import ManifestCheckTask

        trigger = (
            "USER_MANUAL"
            if force_refresh
            else ("AUTO_TIMER" if is_periodic else "BACKGROUND_SCAN")
        )
        self.manifest_check_task = ManifestCheckTask(
            self._games_to_check, trigger=trigger
        )
        self.update_check_progress.emit(0, len(self._games_to_check))

        self.manifest_check_task.game_update_checked.connect(
            self._on_game_update_checked
        )
        self.manifest_check_task.progress.connect(self._on_update_check_progress)
        self.manifest_check_task.completed.connect(self._on_update_check_completed)
        self.manifest_check_task.error.connect(self._on_update_check_error)

        self.manifest_check_runner = TaskRunner()
        self.manifest_check_runner.cleanup_complete.connect(
            self._on_manifest_check_runner_cleanup
        )
        self.manifest_check_runner.run(self.manifest_check_task.run)

    def _start_at0m_update_check(self, force_refresh: bool = False) -> None:
        """Launch the AT0-M / DLC update checker (it runs in its own thread)."""
        try:
            from managers.at0m_update_checker import At0mUpdateChecker

            at0m_checker = At0mUpdateChecker.get_instance()
            try:
                at0m_checker.game_checked.disconnect(self._on_at0m_game_checked)
            except TypeError:
                logger.debug("AT0-M update signal was not connected")
            at0m_checker.game_checked.connect(self._on_at0m_game_checked)
            try:
                at0m_checker.all_checked.disconnect(self._on_at0m_stage_finished)
            except TypeError:
                logger.debug("AT0-M completion signal was not connected")
            at0m_checker.all_checked.connect(self._on_at0m_stage_finished)
            try:
                at0m_checker.check_all_games_async(
                    force_refresh=force_refresh, live_games=list(self.games)
                )
            except TypeError:
                at0m_checker.check_all_games_async(force_refresh=force_refresh)
            if not at0m_checker.is_checking:
                self._on_at0m_stage_finished()
        except Exception as e:
            logger.debug(f"Error launching At0mUpdateChecker: {e}")
            self._on_at0m_stage_finished()

    def _on_at0m_game_checked(self, appid: str, status: str, details: dict):
        """Handle results from At0mUpdateChecker for an AT0-M / DLC game."""
        appid_str = str(appid)
        game = self._games_by_appid.get(appid_str) or self.get_game(appid_str)
        if game:
            from utils.game_modes import is_accela_managed

            if is_accela_managed(game):
                logger.warning(
                    f"Refusing to apply AT0-M update status to ACCELA game {game.get('game_name')} ({appid_str})"
                )
                return
            game["update_status"] = status
            if details:
                game["at0m_update_details"] = details
            logger.info(
                f"AT0-M check result for {game.get('game_name', appid_str)} ({appid_str}): {status}"
            )
            self.game_update_status_changed.emit(appid_str, status)

    def check_single_game_update(self, appid: str) -> None:
        """
        Trigger an update check for a single game by appid.
        Resets its status to 'checking', then runs ManifestCheckTask for just that game.
        """
        game = self._games_by_appid.get(appid) or self.get_game(appid)
        if not game:
            logger.warning(f"check_single_game_update: appid {appid} not found")
            return

        is_atom = game.get("source") == "at0-m" or (
            game.get("is_atom") and not game.get("accela_marker_path")
        )
        if is_atom and not (
            game.get("accela_marker_path") or game.get("source") == "ACCELA"
        ):
            if not hasattr(self, "_single_at0m_checks"):
                self._single_at0m_checks = set()
            if appid in self._single_at0m_checks:
                logger.debug(f"check_single_game_update: check already in flight for AT0-M appid {appid}")
                return
            self._single_at0m_checks.add(appid)

            logger.info(
                f"check_single_game_update: appid {appid} is AT0-M/Plugin game, running At0mUpdateChecker."
            )
            game["update_status"] = UPDATE_STATUS["CHECKING"]
            self.game_update_status_changed.emit(appid, UPDATE_STATUS["CHECKING"])

            def _check_single_at0m():
                try:
                    from managers.at0m_update_checker import At0mUpdateChecker

                    status, details = At0mUpdateChecker.get_instance().check_game(
                        appid, game_title=game.get("game_name", ""), force_refresh=True
                    )
                    self._on_at0m_game_checked(appid, status, details)
                except Exception as _e:
                    logger.debug(f"Error in single AT0-M check for {appid}: {_e}")
                    self._on_at0m_game_checked(appid, "up_to_date", {})
                finally:
                    if hasattr(self, "_single_at0m_checks"):
                        self._single_at0m_checks.discard(appid)

            threading.Thread(
                target=_check_single_at0m,
                daemon=True,
                name=f"SingleAt0mCheck-{appid}",
            ).start()
            return

        settings = get_settings()
        if settings.value(f"pin_build/{appid}", False, type=bool):
            logger.info(
                f"check_single_game_update: appid {appid} is pinned. Bypassing check."
            )
            game["update_status"] = UPDATE_STATUS["UP_TO_DATE"]
            self.game_update_status_changed.emit(appid, UPDATE_STATUS["UP_TO_DATE"])
            return

        if (
            self.manifest_check_task is not None
            and self.manifest_check_runner is not None
        ):
            logger.info(f"Batch check running — adding {appid} to existing batch")
            game["update_status"] = UPDATE_STATUS["CHECKING"]
            game["hubcap_needs_update"] = False
            game["hubcap_update_in_progress"] = False
            self.game_update_status_changed.emit(appid, UPDATE_STATUS["CHECKING"])
            if game not in self._games_to_check:
                self._games_to_check.append(game)
            return

        game["update_status"] = UPDATE_STATUS["CHECKING"]
        game["hubcap_needs_update"] = False
        game["hubcap_update_in_progress"] = False
        self.game_update_status_changed.emit(appid, UPDATE_STATUS["CHECKING"])

        from core.tasks.manifest_check_task import ManifestCheckTask

        task = ManifestCheckTask([game])
        runner = TaskRunner()
        unregister = self._register_single_check_runner(runner)

        def _on_checked(checked_appid, status):
            self._on_game_update_checked(checked_appid, status)
            if status == UPDATE_STATUS["UPDATE_AVAILABLE"]:
                self._check_hubcap_status_async(checked_appid)

        def _on_done():
            unregister()

        def _on_error(error_info):
            try:
                self._on_update_check_error(error_info)
            finally:
                unregister()

        task.game_update_checked.connect(_on_checked)
        task.completed.connect(_on_done)
        task.error.connect(_on_error)

        worker = runner.run(task.run)
        if worker is not None:
            try:
                worker.finished.connect(unregister)
            except Exception as e:
                logger.debug(f"Could not connect worker.finished for {appid}: {e}")
        logger.info(f"Single update check started for appid {appid}")

    def _check_hubcap_status_async(self, appid: str) -> None:
        """Fetch Hubcap status asynchronously and update game dict/UI."""
        from core import morrenus_api

        runner = TaskRunner()

        def _fetch_status():
            try:
                return morrenus_api.get_manifest_status(appid)
            except Exception as e:
                logger.error(f"Error fetching Hubcap status for {appid}: {e}")
                return {"error": str(e)}

        worker = runner.run(_fetch_status)
        unregister = self._register_single_check_runner(runner, worker=worker)

        def _on_status_done(result=None):
            unregister()
            if not isinstance(result, dict):
                return
            if not result or "error" in result:
                logger.warning(
                    f"Failed to get Hubcap status for {appid}: {result.get('error') if result else 'empty'}"
                )
                return

            needs_update = result.get("needs_update", False)
            update_in_progress = result.get("update_in_progress", False)

            is_refined = False
            if is_refined and not needs_update and not update_in_progress:
                from utils.manifest_verifier import verify_hubcap_freshness

                ver_status, _reason, _ = verify_hubcap_freshness(appid, result)
                if ver_status == "stale":
                    needs_update = True

            game = self._games_by_appid.get(appid)
            if game:
                game["hubcap_needs_update"] = needs_update
                game["hubcap_update_in_progress"] = update_in_progress

            self.game_hubcap_status_checked.emit(
                appid, needs_update, update_in_progress
            )

        if worker is not None:
            worker.finished.connect(_on_status_done)
        else:
            unregister()

    def _on_game_update_checked(self, appid: str, update_status: str) -> None:
        """Handle individual game update check result — O(1) via _games_by_appid dict."""
        game = self._games_by_appid.get(appid)
        if game is None:
            for g in self.games:
                if g.get("appid") == appid:
                    game = g
                    break

        if game is not None:
            old_status = game.get("update_status")
            game_title = game.get("name", f"AppID {appid}")

            if (
                update_status == UPDATE_STATUS["CANNOT_DETERMINE"]
                and old_status == UPDATE_STATUS["UPDATE_AVAILABLE"]
            ):
                logger.info(
                    f"[UpdateCheck {appid}] ({game_title}): Keeping existing 'update_available' — "
                    "ignoring cannot_determine result (likely a transient network error)"
                )
                return

            game["update_status"] = update_status
            _checking = UPDATE_STATUS["CHECKING"]
            real_change = (
                old_status != update_status
                and old_status != _checking
                and update_status != _checking
            )
            if real_change:
                if update_status == UPDATE_STATUS["CANNOT_DETERMINE"]:
                    logger.info(
                        f"Updated status for game {appid} ({game_title}): {update_status}"
                    )
                else:
                    logger.debug(
                        f"Updated status for game {appid} ({game_title}): "
                        f"{old_status} -> {update_status}"
                    )
            self.game_update_status_changed.emit(appid, update_status)

            diag_meta = self._build_diag_metadata(appid, update_status)
            get_update_cache().set_status(appid, update_status, diag_meta)
            get_update_cache().save_async()

            if update_status == UPDATE_STATUS["UPDATE_AVAILABLE"]:
                self.settings.setValue(f"manifest_is_fresh/{appid}", False)
            elif update_status == UPDATE_STATUS["UP_TO_DATE"]:
                self.settings.remove(f"manifest_is_fresh/{appid}")
                self.settings.remove(f"fetched_manifest_id/{appid}")
                self.settings.remove(f"latest_steam_manifest_id/{appid}")

    def _build_diag_metadata(self, appid: str, status: str) -> dict:
        """Build diagnostic metadata for cache entry from the last check context."""
        meta = {
            "branch": str(
                self.settings.value(f"last_checked_branch/{appid}", "public")
            ),
            "branch_buildid": str(
                self.settings.value(f"last_checked_branch_buildid/{appid}", "")
            ),
            "local_buildid": str(
                self.settings.value(f"last_checked_local_buildid/{appid}", "")
            ),
        }

        recorded_reason = str(
            self.settings.value(f"last_check_reason/{appid}", "")
        ).strip()
        if status == UPDATE_STATUS["UPDATE_AVAILABLE"]:
            meta["reason"] = recorded_reason or "depot_manifest_mismatch"
            dep_diff = {}
            i = 0
            while True:
                dk = f"last_check_depot_diff/{appid}/{i}"
                diff_str = self.settings.value(dk, "", type=str)
                if not diff_str:
                    break
                parts = diff_str.split("|", 2)
                if len(parts) >= 2:
                    dep_diff[parts[0]] = {
                        "saved": parts[1],
                        "current": parts[2] if len(parts) > 2 else "",
                    }
                i += 1
            if dep_diff:
                meta["depot_diffs"] = dep_diff
        elif status == UPDATE_STATUS["UP_TO_DATE"]:
            meta["reason"] = recorded_reason or "manifests_match"

        return meta

    def _on_update_check_progress(self, current, total):
        """Handle update check progress."""
        if total > 0:
            step = max(1, total // 10)
            if current == total or current % step == 0:
                logger.debug(f"Update check progress: {current}/{total}")
        self.update_check_progress.emit(current, total)

    def _on_update_check_completed(self):
        """Handle update check completion."""
        logger.info("All game updates checked")
        self.manifest_check_task = None
        self.manifest_check_runner = None
        self._games_to_check = []
        self._run_pending_at0m_check()
        self.all_updates_checked.emit()

    def _run_pending_at0m_check(self) -> None:
        """Start the AT0-M check that was queued behind the manifest check."""
        pending = getattr(self, "_pending_at0m_check", None)
        if pending is None:
            self._on_at0m_stage_finished()
            return
        self._pending_at0m_check = None
        self._start_at0m_update_check(force_refresh=bool(pending))

    def _on_at0m_stage_finished(self) -> None:
        """The AT0-M stage is done: the whole update check is over."""
        if getattr(self, "_update_checks_finished_emitted", False):
            return
        self._update_checks_finished_emitted = True
        self.update_checks_finished.emit()

    def _on_update_check_error(self, error_info):
        """Handle update check error."""
        exc_type, exc_msg, exc_traceback = error_info
        logger.error(
            f"Error during update check: {exc_msg}",
            exc_info=(exc_type, exc_msg, exc_traceback),
        )
        self._run_pending_at0m_check()
        self.manifest_check_task = None
        self.manifest_check_runner = None
        self._games_to_check = []
        self.all_updates_checked.emit()

    def _on_manifest_check_runner_cleanup(self):
        """Handle TaskRunner cleanup completion - called when thread finishes."""
        logger.debug("TaskRunner cleanup complete, clearing references")
        self.manifest_check_task = None
        self.manifest_check_runner = None
        self._games_to_check = []

    def cancel_update_checks(self):
        """Cancel any in-progress update checks and clean up task/runner references."""
        if self.manifest_check_task is not None:
            try:
                self.manifest_check_task.stop()
            except Exception as e:
                logger.debug(f"Error stopping manifest check task: {e}")

        if self.manifest_check_runner is not None:
            try:
                self.manifest_check_runner.stop(wait_ms=0, terminate_on_timeout=False)
            except Exception as e:
                logger.debug(f"Error stopping manifest check runner: {e}")

        self._stop_single_check_runners()

        self.manifest_check_task = None
        self.manifest_check_runner = None
        self._games_to_check = []
        self._pending_at0m_check = None
        self._on_at0m_stage_finished()