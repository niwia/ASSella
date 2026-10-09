"""Post-download finalisation phase (ACF, depotcache, integration files).

Extracted from ``managers/task_manager.py``. Composed back into TaskManager via
``PostInstallMixin`` so the public API is unchanged.
"""

import logging
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QTimer, QMetaObject, Qt, pyqtSlot, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from core import steam_helpers
from utils.helpers import get_base_path
from utils.paths import Paths
from utils.steam_manifest import get_game_directory, write_acf_file
from utils.wrapper_metadata import persist_selected_dlcs
from utils.yaml_config_manager import (
    get_user_config_path,
    add_additional_app,
    add_dlc_data,
    is_slssteam_mode_enabled,
    is_slssteam_config_management_enabled,
)

logger = logging.getLogger(__name__)


class PostInstallMixin:
    """Runs the finalisation phase once a download has completed."""

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
        actual_size = 0
        actual_uncompressed = 0
        if self.download_task:
            total_size = getattr(self.download_task, "total_download_size_for_this_job", 0)
            actual_size = getattr(self.download_task, "actual_download_bytes_for_this_job", 0)
            actual_uncompressed = getattr(self.download_task, "actual_uncompressed_bytes_for_this_job", 0)

        effective_download_size = actual_size if actual_size > 0 else total_size
        self._last_download_duration = duration
        self._last_download_size = effective_download_size
        self._last_total_size = total_size
        self._last_uncompressed_size = actual_uncompressed
        self._last_download_avg_speed = effective_download_size / duration

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
            self._finalize_eosproxy()

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
    def _finalize_eosproxy(self):
        """Automatically detect and apply EOS proxy if enabled in settings."""
        if not (self.settings.value("enable_eosproxy_default", False, type=bool) and not self.is_cancelling and self.current_dest_path):
            return

        try:
            game_dir = get_game_directory(self.current_dest_path, self.game_data)
            if game_dir and os.path.isdir(game_dir):
                from utils.eos_detector import EOSDetector
                status = EOSDetector.get_proxy_status(game_dir)
                if status.get("has_eos") and not status.get("has_proxy"):
                    if EOSDetector.apply_proxy(game_dir):
                        logger.info(f"[TaskManager] Automatically applied EOSProxy to {game_dir}")
        except Exception as e:
            logger.debug(f"[TaskManager] Error applying EOSProxy: {e}")
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

        # Ensure any manifest symlinks (especially on Linux) are properly established
        try:
            from utils.manifest_resolver import restore_all_game_symlinks
            install_dir = None
            if self.game_data:
                install_dir = self.game_data.get("install_dir") or self.game_data.get("game_path")
            if install_dir and os.path.isdir(install_dir):
                restored_links = restore_all_game_symlinks(install_dir)
                if restored_links:
                    logger.info(f"Restored {len(restored_links)} symlinks for {self.game_data.get('game_name', 'game')}")
        except Exception as e:
            logger.debug(f"Failed to restore symlinks in finalize_job_logic: {e}")

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
                    # For handoff jobs (native Steam download), we do NOT mark up_to_date here.
                    # Steam is still downloading in the background. The VaporWatcher will set
                    # up_to_date once StateFlags=4 is confirmed in the ACF. Without this guard,
                    # if gmrc.wudrm.com is unreachable Steam's MRC fetch fails and the download
                    # stalls, yet ASSella would incorrectly show the game as up_to_date.
                    is_handoff = getattr(self, "_is_handoff_job", False)
                    if is_handoff:
                        logger.info(
                            f"[JobFinished] Handoff job for appid={appid} — deferring up_to_date "
                            "to VaporWatcher (Steam download still in progress)"
                        )
                    # Set status to up_to_date so the post-download rescan restores
                    # the correct status immediately. Without this, the game would stay
                    # at "checking" indefinitely because _on_initial_scan_complete
                    # (the only slot that calls check_game_updates_async) disconnects
                    # itself after boot and never runs again for subsequent rescans.
                    # Skip for rollback installs — user chose an older build, so keep
                    # the game showing "update_available".
                    elif not self.game_data.get("_is_rollback"):
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

                    # If game is managed via AT0-M (either pre-existing, or requested via register_at0m / is_atom),
                    # ensure its depot keys, installdir, and AppID are synced with SLSsteam config.yaml and plugin_library.json
                    should_sync_atom = bool(
                        (self.current_job_metadata or {}).get("register_at0m")
                        or (self.game_data or {}).get("register_at0m")
                        or (self.game_data or {}).get("is_atom")
                        or (self.game_data or {}).get("is_plugin_game")
                    )
                    if should_sync_atom and appid and appid not in ("0", "N/A", "unknown"):
                        try:
                            from utils.plugin_games import register_plugin_game
                            depots_meta = (self.game_data.get("depots") or {}) if self.game_data else {}
                            depot_keys = (self.game_data.get("depot_keys") or {}) if self.game_data else {}
                            if not depot_keys and isinstance(depots_meta, dict):
                                for did, dinfo in depots_meta.items():
                                    if isinstance(dinfo, dict) and dinfo.get("key"):
                                        depot_keys[str(did)] = dinfo["key"]

                            selected_depots = (
                                (self.game_data.get("selected_depots_list") or [])
                                if self.game_data
                                else []
                            )
                            if not selected_depots:
                                selected_depots = list(depots_meta.keys()) or list(depot_keys.keys())

                            installdir = (
                                self.game_data.get("installdir")
                                or self.game_data.get("install_dir")
                                or ""
                            )
                            register_plugin_game(
                                appid=str(appid),
                                name=self.game_data.get("game_name", f"App {appid}"),
                                depot_ids=selected_depots,
                                decryption_keys=depot_keys,
                                installdir=installdir,
                            )
                            logger.info(f"[TaskManager] Successfully synchronized AT0-M / SLSsteam registration for {appid}")
                        except Exception as _reg_err:
                            logger.warning(f"[TaskManager] Could not synchronize AT0-M registration: {_reg_err}")

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

        # If NativeSteamDownloadTask was used, Steam already natively generated the ACF
        # and manages the installation. Skip writing custom ACF and ACCELA metadata markers
        # to prevent subsequent library scans from misidentifying native games as ACCELA.
        try:
            from core.tasks.native_steam_download_task import NativeSteamDownloadTask
            if isinstance(self.download_task, NativeSteamDownloadTask):
                logger.info(
                    f"Native Steam download backend was used for {appid} - "
                    "Steam natively generated the manifest. Skipping custom ACF and ACCELA metadata writing."
                )
                return
        except Exception:
            pass

        # 1. Always write/update our local metadata.json fallback for non-native downloads
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
                is_sls_filewatcher_dead,
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

                    # Precondition check: warn the user if SLSsteam filewatcher is dead or SLSsteam is not running
                    if is_sls_filewatcher_dead():
                        warning_msg = "SLSsteam Filewatcher crashed in Steam. Restart Steam"
                        logger.warning(f"WARNING: {warning_msg}")
                        if hasattr(self.main_window, "notify_sls_watcher_crashed_signal"):
                            self.main_window.notify_sls_watcher_crashed_signal.emit()
                    elif not _is_slssteam_available():
                        warning_msg = warn_sls_unavailable(context="post-install")
                        logger.warning(f"WARNING: {warning_msg}")

                    job_type = self.game_data.get("job_type", "download") if self.game_data else "download"
                    if job_type == "verify":
                        logger.info("Skipping SLS install API call for verify job — ACF already exists.")
                        return
                    if appid and appid not in ("0", "N/A", "unknown"):
                        install_via_sls(
                            appid=str(appid),
                            game_name=self.game_data.get("game_name", ""),
                            library_path=self.current_dest_path or "",
                            main_window=self.main_window,
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

        from utils.steam_manifest import get_install_folder_name
        install_folder = get_install_folder_name(self.game_data)
        game_ddm_dir = os.path.join(self.current_dest_path, "steamapps", "common", install_folder, ".DepotDownloader") if install_folder else ""

        global_manifests_dir = str(get_base_path() / "manifests")
        candidate_dirs = [d for d in (temp_manifest_dir, game_ddm_dir, global_manifests_dir) if d and os.path.exists(d)]

        try:
            os.makedirs(target_depotcache_dir, exist_ok=True)
            manifests_map = self.game_data.get("manifests", {})
            if not manifests_map:
                if os.path.exists(temp_manifest_dir):
                    shutil.rmtree(temp_manifest_dir, ignore_errors=True)
                return

            for depot_id, manifest_gid in manifests_map.items():
                manifest_filename = f"{depot_id}_{manifest_gid}.manifest"
                dest_path = os.path.join(target_depotcache_dir, manifest_filename)

                # Find source file in candidate directories
                source_path = None
                for c_dir in candidate_dirs:
                    cand = os.path.join(c_dir, manifest_filename)
                    if os.path.exists(cand):
                        source_path = cand
                        break

                if source_path:
                    if central_depotcache_dir:
                        try:
                            shutil.copy2(source_path, os.path.join(central_depotcache_dir, manifest_filename))
                            logger.info(f"Copied manifest {manifest_filename} to Steam's central depotcache")
                        except Exception as e:
                            logger.error(f"Failed to copy manifest to central depotcache: {e}")

                    if not os.path.exists(dest_path) or not os.path.samefile(source_path, dest_path):
                        try:
                            shutil.copy2(source_path, dest_path)
                        except Exception as e:
                            logger.error(f"Failed to copy manifest to target depotcache: {e}")

            if os.path.exists(temp_manifest_dir):
                shutil.rmtree(temp_manifest_dir, ignore_errors=True)
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
                fallback_src = get_base_path() / "manifests" / manifest_filename
                if fallback_src.exists():
                    src = str(fallback_src)
                else:
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

        # Ensure .assella marker is written for ASSella managed games
        try:
            marker_file = os.path.join(game_install_dir, ".assella")
            if not os.path.exists(marker_file):
                with open(marker_file, "w", encoding="utf-8") as f:
                    f.write(f"appid={self.game_data.get('appid')}\n")
        except Exception:
            pass

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
                # If game was previously tracked in AT0-M plugin_games, convert it to ACCELA mode
                try:
                    from utils.plugin_games import convert_plugin_game_to_accela, get_plugin_game
                    if get_plugin_game(str(main_appid), active_only=False):
                        logger.info(f"Converting previously registered plugin game {main_appid} to ACCELA managed mode")
                        convert_plugin_game_to_accela(str(main_appid))
                except Exception as e:
                    logger.debug(f"Could not convert plugin game {main_appid}: {e}")

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
