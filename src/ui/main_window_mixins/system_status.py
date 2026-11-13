import logging
import os
import random
import threading
import time

from PyQt6.QtCore import Q_ARG, QMetaObject, Qt, pyqtSlot
from PyQt6.QtGui import QFontMetrics

from utils.task_runner import TaskRunner
from utils.version import app_version

logger = logging.getLogger(__name__)


class MainWindowSystemStatusMixin:
    """Mixin handling system status telemetry, Hubcap stats, SteamDB status, and footer quotes."""

    def check_steam_updates_blocked(self) -> bool:
        """Check if steam updates are blocked via steam.cfg."""
        from utils.steam_updates import are_steam_updates_blocked
        return are_steam_updates_blocked()

    def _get_status_style(self, state: str) -> str:
        """
        Returns the stylesheet string for bottom visor status values
        using theme-harmonized semantic colors.
        """
        accent_color = self.settings.value("accent_color", "#C06C84", type=str)
        from utils.color_utils import get_semantic_colors
        sem_colors = get_semantic_colors(accent_color)

        color_map = {
            "success": sem_colors["success"],
            "warning": sem_colors["warning"],
            "error": sem_colors["error"],
            "info": sem_colors["info"],
            "neutral": "#888888",
        }

        c = color_map.get(state.lower(), "#888888")
        return f"color: {c} !important; font-size: 11px; font-weight: bold; border: none; background: transparent;"

    @pyqtSlot()
    def notify_sls_filewatcher_crashed(self) -> None:
        """Update UI when SLSsteam filewatcher has crashed in Steam."""
        warning_msg = "SLSsteam Filewatcher crashed in Steam . Restart Steam"
        if hasattr(self, "status_pager") and self.status_pager:
            self.status_pager.show_warning(warning_msg, 30)
        elif hasattr(self, "drop_text_label") and self.drop_text_label:
            self.drop_text_label.setText(warning_msg)

        if hasattr(self, "active_game_card") and self.active_game_card:
            self.active_game_card.set_sub_status(warning_msg, is_warning=True)

        self.refresh_system_status_now()

    @pyqtSlot()
    def refresh_system_status(self) -> None:
        """Schedule a refresh of the local Steam/SLS/ASSella status labels.

        The actual work runs in _apply_system_status_refresh(), coalesced to at
        most one pass per second. A bulk update check rebuilds the dashboard
        hundreds of times and every rebuild re-probed the filesystem, which
        starved the event loop exactly when the user is most likely to click
        something.
        """
        if threading.current_thread() is not threading.main_thread():
            if hasattr(self, "refresh_system_status_signal"):
                self.refresh_system_status_signal.emit()
            return

        timer = getattr(self, "_system_status_timer", None)
        if timer is None:
            self._apply_system_status_refresh()
            return
        timer.start()

    def refresh_system_status_now(self) -> None:
        """Refresh the status labels right away, cancelling any pending pass."""
        timer = getattr(self, "_system_status_timer", None)
        if timer is not None:
            timer.stop()
        self._apply_system_status_refresh()

    def _apply_system_status_refresh(self) -> None:
        """Refresh local Steam updates, SLS, and ASSella status labels."""
        if not hasattr(self, "steam_updates_value") or not self.steam_updates_value:
            return

        self._gui_phase = "status refresh"
        try:
            self._collect_system_status()
        finally:
            self._gui_phase = "idle"

    def _collect_system_status(self) -> None:
        """Collect and render the Steam/SLS/ASSella status labels."""
        if not hasattr(self, "steam_updates_value") or not self.steam_updates_value:
            return

        blocked = self.check_steam_updates_blocked()
        if blocked:
            self.steam_updates_value.setText("Blocked")
            self.steam_updates_value.setStyleSheet(self._get_status_style("success"))
        else:
            self.steam_updates_value.setText("Allowed")
            self.steam_updates_value.setStyleSheet(self._get_status_style("warning"))

        # --- SLS Detection ---
        from ui.dialogs.settings_sls import get_sls_paths

        sls_paths = get_sls_paths()
        sls_detected = sls_paths.get("detected", False)
        _version_file_exists = os.path.exists(sls_paths.get("version_file", "")) if sls_detected else False

        # --- SLS Detection & API Integration ---
        from utils.slssteam_integration import (
            is_sls_filewatcher_dead,
            is_slssteam_process_active,
            is_steam_process_running,
        )
        sls_active = self.settings.value("experimental_acf_independent", False, type=bool) if self.settings else False

        # SLS Config Status (SLS Integration Status)
        if hasattr(self, "sls_lbl") and self.sls_lbl:
            self.sls_lbl.setEnabled(sls_active)
        if hasattr(self, "sls_status_value") and self.sls_status_value:
            self.sls_status_value.setEnabled(True)
            if sls_active:
                self.sls_status_value.setText("Active")
                self.sls_status_value.setStyleSheet(self._get_status_style("success"))
            else:
                self.sls_status_value.setText("Disabled")
                self.sls_status_value.setStyleSheet(self._get_status_style("neutral"))

        # SLSsteam API Status (Active check of process)
        if hasattr(self, "slssteam_lbl") and self.slssteam_lbl:
            self.slssteam_lbl.setEnabled(True)
        if hasattr(self, "slssteam_status_value") and self.slssteam_status_value:
            self.slssteam_status_value.setEnabled(True)
            if sls_active and is_slssteam_process_active():
                self.slssteam_status_value.setText("Online")
                self.slssteam_status_value.setStyleSheet(self._get_status_style("success"))
            else:
                self.slssteam_status_value.setText("Offline")
                self.slssteam_status_value.setStyleSheet(self._get_status_style("error"))

        # Steam Client Process Status
        if hasattr(self, "steam_conn_value") and self.steam_conn_value:
            self.steam_conn_value.setEnabled(True)
            if not is_steam_process_running():
                self.steam_conn_value.setText("Offline")
                self.steam_conn_value.setStyleSheet(self._get_status_style("error"))
                self.steam_conn_value.setToolTip("Steam client is not running.")
            elif is_sls_filewatcher_dead():
                self.steam_conn_value.setText("Restart")
                self.steam_conn_value.setStyleSheet(self._get_status_style("warning"))
                self.steam_conn_value.setToolTip(
                    "Steam: Restart required\n"
                    "• SLSsteam file watcher has crashed in Steam.\n"
                    "• Restart Steam to reload game configs and licenses."
                )
            else:
                from core.steam_helpers import get_installed_steam_client_version
                client_ver = get_installed_steam_client_version()
                disp_text = client_ver if client_ver else "Online"
                self.steam_conn_value.setText(disp_text)
                self.steam_conn_value.setStyleSheet(self._get_status_style("success"))
                self.steam_conn_value.setToolTip(f"Steam client is running (Version: {disp_text}).")

        # Health Visor Status (SLS binary + version + config + SLS API)
        if hasattr(self, "health_status_value") and self.health_status_value:
            try:
                import utils.assfixer as assfixer
                from utils.slssteam_integration import check_slssteam_binary_is_latest

                sls_detected = sls_paths.get("detected", False)
                # cached_only: this runs on the GUI thread and must never wait
                # on the network. The background boot check fills the cache.
                ver_res = check_slssteam_binary_is_latest(cached_only=True)
                ver_status = ver_res.get("status", "error")
                version_ok = (ver_status == "up_to_date")

                cfg_boot_status = getattr(assfixer, "boot_status", None)
                config_ok = (cfg_boot_status == "optimal")

                sls_api_enabled = self.settings.value("experimental_acf_independent", False, type=bool) if self.settings else False
                sls_api_active = sls_api_enabled and is_slssteam_process_active()
                watcher_dead = is_sls_filewatcher_dead()

                if cfg_boot_status in ("checking", None) or ver_status == "checking":
                    self.health_status_value.setText("Checking...")
                    self.health_status_value.setStyleSheet(self._get_status_style("neutral"))
                    self.health_status_value.setToolTip("Checking System & SLS Health status...")
                elif sls_detected and version_ok and config_ok and sls_api_active and not watcher_dead:
                    self.health_status_value.setText("Good")
                    self.health_status_value.setStyleSheet(self._get_status_style("success"))
                    self.health_status_value.setToolTip(
                        "Health: Good\n"
                        "• SLSsteam Binary: Detected\n"
                        f"• SLSsteam Version: Up to date ({ver_res.get('release_tag', '')})\n"
                        "• SLS Config: Optimal\n"
                        "• SLS Native API: Active\n\n"
                        "Click to open Health settings."
                    )
                else:
                    issues = []
                    if not sls_detected:
                        issues.append("SLSsteam binary not found")
                    elif not version_ok:
                        tag = ver_res.get("release_tag", "")
                        issues.append(f"SLSsteam update available ({tag})" if tag else "SLSsteam outdated")
                    if not config_ok:
                        issues.append("SLS config needs repair/resync")
                    if not sls_api_active:
                        if not sls_api_enabled:
                            issues.append("SLS Native API disabled in settings")
                        elif not is_slssteam_process_active():
                            if not is_steam_process_running():
                                issues.append("Steam not running (SLS not injected)")
                            else:
                                issues.append("SLS process not injected into Steam")
                    if watcher_dead:
                        issues.append("SLS filewatcher crashed (Restart Steam)")

                    self.health_status_value.setText("Attention" if issues else "Checking...")
                    self.health_status_value.setStyleSheet(self._get_status_style("warning"))
                    tooltip = "Health: Attention required\n" + "\n".join(f"• {iss}" for iss in issues) + "\n\nClick to open Health settings."
                    self.health_status_value.setToolTip(tooltip)
            except Exception as e:
                logger.debug(f"Error updating Health visor status: {e}")
                self.health_status_value.setText("Checking...")
                self.health_status_value.setStyleSheet(self._get_status_style("neutral"))

        # SteamDB Visor Sync
        if hasattr(self, "steamdb_container") and hasattr(self, "steamdb_status_value"):
            try:
                from core.steamdb_scraper import ByparrManager
                has_byparr = ByparrManager.is_available()
                if not has_byparr:
                    self.steamdb_container.setVisible(False)
                else:
                    self.steamdb_container.setVisible(True)
                    if ByparrManager.is_running():
                        self.steamdb_status_value.setText("Ready")
                        self.steamdb_status_value.setStyleSheet(self._get_status_style("success"))
                        self.steamdb_status_value.setToolTip(
                            "SteamDB: Cloudflare Turnstile bypass active via Byparr\n"
                            "Version history, patch notes & depot enrichment enabled."
                        )
                    else:
                        self.steamdb_status_value.setText("Offline")
                        self.steamdb_status_value.setStyleSheet(self._get_status_style("neutral"))
                        self.steamdb_status_value.setToolTip(
                            "SteamDB: Solver is currently offline.\n"
                            "It will start automatically when version history or builds are accessed."
                        )
            except Exception as e:
                logger.debug(f"[SteamDB] Error syncing SteamDB visor status: {e}")

        # ASSella Status
        if hasattr(self, "assella_status_value") and self.assella_status_value:
            tool_status = getattr(self, "_tool_update_status", "checking")
            source_label = {
                "github": "GitHub",
                "r2": "R2",
                "github_raw": "GitHub Raw",
            }.get(getattr(self, "_latest_update_source", ""), "")
            via_suffix = f" (via {source_label})" if source_label else ""
            if tool_status == "update_available":
                self.assella_status_value.setText("Update!")
                self.assella_status_value.setStyleSheet(self._get_status_style("warning"))
                self.assella_status_value.setToolTip(
                    f"New version available: {getattr(self, '_latest_remote_version', '')}{via_suffix}"
                )
            elif tool_status == "up_to_date":
                self.assella_status_value.setText("Latest")
                self.assella_status_value.setStyleSheet(self._get_status_style("success"))
                self.assella_status_value.setToolTip(f"ASSella is up to date (v{app_version}{via_suffix})")
            elif tool_status in ("offline", "error", "cant_check"):
                self.assella_status_value.setText("Can't Check")
                self.assella_status_value.setStyleSheet(self._get_status_style("error"))
                self.assella_status_value.setToolTip(
                    f"Could not check for ASSella updates (update server unreachable)\nCurrent version: v{app_version}"
                )
            else:
                self.assella_status_value.setText("Checking...")
                self.assella_status_value.setStyleSheet(self._get_status_style("neutral"))
                self.assella_status_value.setToolTip("Checking for tool updates...")

        # Library Size Stats
        if hasattr(self, "library_size_value") and self.library_size_value:
            if hasattr(self, "game_manager") and self.game_manager:
                stats = self.game_manager.get_library_stats()
                total_games = stats.get("total_games", 0)
                total_bytes = stats.get("total_size", 0)
                total_gb = total_bytes / 1_073_741_824
                self.library_size_value.setText(f"{total_gb:.1f} GB ({total_games} games)")

    def rotate_quote(self) -> None:
        if not hasattr(self, "quote_label") or not self.quote_label:
            return
        current_text = self._quote_text
        available_quotes = [q for q in self.quotes if q[0] != current_text]
        if available_quotes:
            self._quote_text, self._quote_source = random.choice(available_quotes)
            if hasattr(self, "quote_source_label") and self.quote_source_label:
                self.quote_source_label.setText(f"— {self._quote_source}")
            self._refresh_quote_display()

    def _refresh_quote_display(self) -> None:
        """Draw the rotating quote elided to the footer width.

        Formats the quote inside typographic quotation marks and the source game in italics.
        Eliding ensures long quotes never force minimum width on the window.
        """
        if not getattr(self, "quote_label", None) or not getattr(
            self, "footer_widget", None
        ):
            return

        layout = self.footer_widget.layout()
        margins = layout.contentsMargins() if layout else None
        left_m = margins.left() if margins else 15
        right_m = margins.right() if margins else 15

        # Available horizontal space inside the footer widget
        available = max(100, self.footer_widget.width() - left_m - right_m)
        metrics = QFontMetrics(self.quote_label.font())

        source_str = f" — {self._quote_source}"
        source_width = metrics.horizontalAdvance(source_str) + 30
        max_quote_width = max(50, available - source_width)

        elided_quote = metrics.elidedText(
            self._quote_text, Qt.TextElideMode.ElideRight, max_quote_width
        )
        html = (
            f'“{elided_quote}” &nbsp;—&nbsp; '
            f'<i style="color: rgba(255, 255, 255, 0.55); font-style: italic;">{self._quote_source}</i>'
        )
        self.quote_label.setText(html)
        tooltip = f'“{self._quote_text}” — {self._quote_source}'
        self.quote_label.setToolTip(tooltip)

    def check_steamdb_status(self) -> None:
        """Asynchronously verify that SteamDB is accessible via fast-path or Byparr."""
        if getattr(self, "_steamdb_check_running", False):
            return

        try:
            from core.steamdb_scraper import ByparrManager, cffi_requests
            has_solver = ByparrManager.is_available() or bool(cffi_requests)
            if not has_solver:
                if hasattr(self, "steamdb_container"):
                    self.steamdb_container.setVisible(False)
                return
        except Exception as e:
            logger.debug(f"[SteamDB] Solver availability check failed: {e}")
            return

        self._steamdb_check_running = True
        if hasattr(self, "steamdb_status_value") and self.steamdb_status_value:
            self.steamdb_status_value.setText("Checking...")
            self.steamdb_status_value.setStyleSheet(self._get_status_style("neutral"))
            self.steamdb_status_value.setToolTip("Verifying SteamDB connection and bypass status...")

        def _worker():
            try:
                from core.steamdb_scraper import verify_steamdb_status
                ok, provider = verify_steamdb_status(timeout_seconds=5)
                logger.info(f"[SteamDB Visor] verify_steamdb_status result: {ok} ({provider})")
                self._main_thread_callable.emit(lambda: self._on_steamdb_status_result(ok, provider))
            except Exception as e:
                logger.debug(f"[SteamDB] Error during SteamDB status verification: {e}")
                self._main_thread_callable.emit(lambda: self._on_steamdb_status_result(False, "Offline"))
            finally:
                self._steamdb_check_running = False

        threading.Thread(target=_worker, daemon=True).start()

    def _on_steamdb_status_result(self, is_working: bool, provider: str = "") -> None:
        """Display SteamDB visor item if SteamDB is working/ready."""
        self._steamdb_verified = is_working
        if hasattr(self, "steamdb_container") and hasattr(self, "steamdb_status_value"):
            try:
                from core.steamdb_scraper import ByparrManager, cffi_requests
                has_solver = ByparrManager.is_available() or bool(cffi_requests)
            except Exception as e:
                logger.debug(f"[SteamDB] Could not check solver availability: {e}")
                has_solver = False

            if not has_solver:
                self.steamdb_container.setVisible(False)
                return

            self.steamdb_container.setVisible(True)
            if is_working:
                self.steamdb_status_value.setText("Ready")
                self.steamdb_status_value.setStyleSheet(self._get_status_style("success"))
                mode_info = f" via {provider}" if provider else ""
                self.steamdb_status_value.setToolTip(
                    f"SteamDB: Active{mode_info}\n"
                    "Version history, patch notes & depot enrichment enabled."
                )
                logger.info(f"[SteamDB Visor] SteamDB status updated to: Ready ({provider})")
            else:
                self.steamdb_status_value.setText("Offline")
                self.steamdb_status_value.setStyleSheet(self._get_status_style("neutral"))
                self.steamdb_status_value.setToolTip(
                    "SteamDB: Service or solver is currently offline.\n"
                    "It will start automatically when version history or builds are accessed."
                )
                logger.info("[SteamDB Visor] SteamDB status updated to: Offline")

    def refresh_hubcap_stats(self, force: bool = False) -> None:
        """Fetch user statistics from Hubcap API asynchronously.
        When online, queries are throttled to once every 5 minutes (300s).
        """
        now = time.time()
        last_refresh = getattr(self, "_last_hubcap_refresh_time", 0.0)
        current_hubcap = self.hubcap_conn_value.text() if hasattr(self, "hubcap_conn_value") else "Connecting..."
        is_online = current_hubcap.startswith("Online")
        throttle_interval = 300.0 if is_online else 30.0
        if not force and (now - last_refresh < throttle_interval):
            logger.debug(f"[Hubcap] Skipping stats refresh - throttled (called <{int(throttle_interval)}s ago)")
            return
        self._last_hubcap_refresh_time = now

        # Also refresh Steam and SLS status locally
        self.refresh_system_status()

        # Trigger background network checks if offline/connecting or forced
        current_hubcap = self.hubcap_conn_value.text() if hasattr(self, "hubcap_conn_value") else "Connecting..."
        current_steam = self.steam_conn_value.text() if hasattr(self, "steam_conn_value") else "Connecting..."
        if force or current_hubcap in ("Connecting...", "Offline") or current_steam in ("Connecting...", "Offline"):
            self._check_network_connections_async(force=force)

        if not self.stats_task_runner:
            self.stats_task_runner = TaskRunner(self)

        # Set stats text to loading
        if hasattr(self, "hubcap_api_value") and self.hubcap_api_value:
            self.hubcap_api_value.setText("Loading...")
        if hasattr(self, "active_hubcap_label") and self.active_hubcap_label:
            self.active_hubcap_label.setText("Hubcap: Loading...")

        def get_all_hubcap_stats():
            # Imported on the worker thread: morrenus_api takes ~0.1s to import.
            from core.morrenus_api import get_all_hubcap_stats as _fetch

            return _fetch(force=force)

        worker = self.stats_task_runner.run(get_all_hubcap_stats)
        worker.finished.connect(self._on_user_stats_loaded)
        worker.error.connect(self._on_user_stats_error)

    def _check_network_connections_async(self, force: bool = False) -> None:
        """Run connection check once in background thread."""
        def run_check():
            try:
                from utils.network_status import run_connection_check
                steam_ok, hubcap_ok, hubcap_mode = run_connection_check(force=force)
            except Exception as e:
                logger.error(f"Error running network connection check: {e}")
                steam_ok, hubcap_ok, hubcap_mode = False, False, "Offline"

            # Update values thread-safely via QMetaObject.invokeMethod
            QMetaObject.invokeMethod(
                self,
                "_on_connections_checked",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(bool, steam_ok),
                Q_ARG(bool, hubcap_ok),
                Q_ARG(str, hubcap_mode),
            )

        threading.Thread(target=run_check, daemon=True).start()

    @pyqtSlot(bool, bool, str)
    def _on_connections_checked(self, steam_ok: bool, hubcap_ok: bool, hubcap_mode: str) -> None:
        """Called thread-safely when background connection check completes."""
        # 1. Update Hubcap connection label
        if hasattr(self, "hubcap_conn_value") and self.hubcap_conn_value:
            if hubcap_ok:
                lbl = f"Online ({hubcap_mode})" if hubcap_mode and hubcap_mode not in ("Online", "Direct") else "Online"
                if hubcap_mode == "Tor":
                    lbl = "Online (Tor)"
                elif hubcap_mode == "Wire":
                    lbl = "Online (Wire)"
                elif hubcap_mode == "DoH":
                    lbl = "Online (DoH)"
                elif hubcap_mode == "Direct":
                    lbl = "Online (Direct)"
                self.hubcap_conn_value.setText(lbl)
                self.hubcap_conn_value.setStyleSheet(self._get_status_style("success"))
            else:
                self.hubcap_conn_value.setText("Offline")
                self.hubcap_conn_value.setStyleSheet(self._get_status_style("error"))

        # If online and tool update check is in checking/offline state, trigger check
        if (steam_ok or hubcap_ok) and getattr(self, "_tool_update_status", "checking") in ("checking", "offline"):
            if not getattr(self, "_tool_update_check_running", False):
                self.check_tool_updates()

    def _on_user_stats_loaded(self, result: dict) -> None:
        """Handle async hubcap stats load success."""
        if not isinstance(result, dict):
            return

        stats = result.get("user_stats", {})
        _gen_usage = result.get("gen_usage", {})

        if not isinstance(stats, dict) or "error" in stats:
            err_msg = stats.get("error", "Unknown error") if isinstance(stats, dict) else "Invalid response"
            logger.warning(f"Failed to load Hubcap user stats: {err_msg}")
            val = "No Key" if "key is not set" in err_msg.lower() else "Error"
            if hasattr(self, "hubcap_api_value") and self.hubcap_api_value:
                self.hubcap_api_value.setText(val)
            if hasattr(self, "active_hubcap_label") and self.active_hubcap_label:
                self.active_hubcap_label.setText(f"Hubcap: {val}")
            return

        # 1. Daily usage (Manifests)
        usage = stats.get("daily_usage", 0)
        limit = stats.get("daily_limit", 55)

        # 2. Key Expiry and color-coded status
        from core.morrenus_api import compute_api_key_expiry_info

        api_key = self.settings.value("morrenus_api_key", "", type=str) if self.settings else ""
        expiry_info = compute_api_key_expiry_info(
            stats.get("api_key_expires_at"),
            api_key=api_key,
            settings=self.settings,
        )

        time_text = expiry_info["text"]
        accent_color = self.settings.value("accent_color", "#C06C84", type=str) if self.settings else "#C06C84"
        from utils.color_utils import get_semantic_colors
        sem_colors = get_semantic_colors(accent_color)
        status_key = expiry_info.get("status", "success")
        color = sem_colors.get(status_key, expiry_info.get("color", "#a8dab5"))

        quota_str = f'API: {usage}/{limit} | <span style="color: {color}; font-weight: bold;">{time_text}</span>'

        if expiry_info["is_lifetime"]:
            exp_desc = "Lifetime key (Never expires)"
        elif expiry_info["is_expired"]:
            exp_desc = f"{expiry_info['formatted_date']} (Expired)"
        elif expiry_info["hours"] <= 24:
            exp_desc = f"{expiry_info['formatted_date']} ({time_text} remaining, {expiry_info['percentage']:.0f}% left)"
        else:
            exp_desc = f"{expiry_info['formatted_date']} ({expiry_info['days']}d remaining, {expiry_info['percentage']:.0f}% left)"

        tooltip_str = (
            f"Hubcap API:\n"
            f"• Daily Manifests: {usage} / {limit} (resets at 00:00 UTC)\n"
            f"• API Key Expiry: {exp_desc}"
        )

        if hasattr(self, "hubcap_api_value") and self.hubcap_api_value:
            self.hubcap_api_value.setText(quota_str)
            self.hubcap_api_value.setToolTip(tooltip_str)
        if hasattr(self, "active_hubcap_label") and self.active_hubcap_label:
            self.active_hubcap_label.setText(f"Hubcap: {quota_str}")
            self.active_hubcap_label.setToolTip(tooltip_str)

        # Hubcap is confirmed reachable and functional: ensure visor connection label reflects Online
        if hasattr(self, "hubcap_conn_value") and self.hubcap_conn_value:
            from utils.isp_bypass import connection_status
            mode = connection_status if connection_status in ("Direct", "DoH", "Tor", "Wire") else "Online"
            lbl = f"Online ({mode})" if mode not in ("Online", "Direct") else "Online"
            self.hubcap_conn_value.setText(lbl)
            self.hubcap_conn_value.setStyleSheet(self._get_status_style("success"))

        if getattr(self, "_tool_update_status", "checking") in ("checking", "offline"):
            if not getattr(self, "_tool_update_check_running", False):
                self.check_tool_updates()

    def _on_user_stats_error(self, err_tuple: tuple) -> None:
        """Handle async hubcap stats load error."""
        logger.error(f"Async user stats load failed: {err_tuple[1]}")
        if hasattr(self, "hubcap_api_value") and self.hubcap_api_value:
            self.hubcap_api_value.setText("Error")
        if hasattr(self, "active_hubcap_label") and self.active_hubcap_label:
            self.active_hubcap_label.setText("Hubcap: Error")