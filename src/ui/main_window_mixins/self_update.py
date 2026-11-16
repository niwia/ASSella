import logging
import threading

from PyQt6.QtCore import Q_ARG, QMetaObject, Qt, QUrl, pyqtSlot
from PyQt6.QtGui import QDesktopServices

from ui.dialogs.themed_message_box import ThemedMessageBox
from utils.version import (
    app_version,
    check_for_updates,
    get_effective_update_channel,
    normalize_update_server,
)

logger = logging.getLogger(__name__)


class MainWindowSelfUpdateMixin:
    """Mixin handling tool update checks and self-update installation callbacks."""

    def check_tool_updates(self, force: bool = False) -> None:
        """Start a background thread to check for tool self-updates."""
        # Prevent multiple concurrent checks from running at the same time
        if self._tool_update_check_running:
            if force:
                self._tool_update_recheck = True
            logger.debug("Tool update check already in progress, skipping.")
            return
        self._tool_update_recheck = False
        self._tool_update_check_running = True
        self._tool_update_status = "checking"
        self.refresh_system_status()

        configured_channel = self.settings.value("update_channel", "").strip().lower()
        configured_server = self.settings.value("update_server", "auto", type=str).strip().lower()
        target_channel = get_effective_update_channel(configured_channel, app_version)
        prev_channel = str(getattr(self, "_latest_update_channel", "") or "").strip().lower()
        if prev_channel and prev_channel != target_channel:
            self._latest_remote_version = ""
            self._latest_update_source = ""
            self._tool_update_available_flag = False
            self._latest_update_channel = target_channel
            self._update_tool_update_visibility()

        def _check_sync():
            status = "offline"
            remote_clean = None
            remote_source = ""
            remote_channel = target_channel
            try:
                res = check_for_updates(
                    configured_channel=configured_channel,
                    current_version=app_version,
                    update_server=configured_server,
                )
                status = res["status"]
                remote_clean = res["remote_version"]
                remote_source = res.get("source") or ""
                remote_channel = res.get("channel") or target_channel
                if not configured_channel or configured_channel not in ("stable", "beta", "canary"):
                    self.settings.setValue("update_channel", res["channel"])

                if status == "update_available":
                    if res.get("is_cross_channel"):
                        logger.info(
                            f"Cross-channel update available: {remote_clean} (channel: {res['channel']}, server: {remote_source or configured_server}, current: {res['local_version']})"
                        )
                    else:
                        logger.info(
                            f"Tool update available: {remote_clean} (server: {remote_source or configured_server}, current: {res['local_version']})"
                        )
                elif status == "up_to_date":
                    logger.info(f"Tool is up to date ({res['local_version']}, via {remote_source or configured_server})")
                else:
                    logger.debug(f"Tool update check offline/unresolved: {res.get('error')}")
            except Exception as e:
                logger.warning(f"Failed to check tool updates: {e}")
                status = "offline"
            finally:
                self._tool_update_check_running = False
                QMetaObject.invokeMethod(
                    self,
                    "_on_tool_update_result",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, status),
                    Q_ARG(str, remote_clean or ""),
                    Q_ARG(str, remote_source),
                    Q_ARG(str, remote_channel),
                )

        t = threading.Thread(target=_check_sync, daemon=True)
        t.start()

    @pyqtSlot(str, str)
    @pyqtSlot(str, str, str)
    @pyqtSlot(str, str, str, str)
    def _on_tool_update_result(
        self,
        status: str,
        remote_version: str = "",
        remote_source: str = "",
        remote_channel: str = "",
    ) -> None:
        """Slot triggered when tool self-update check finishes."""
        active_channel = get_effective_update_channel(
            str(self.settings.value("update_channel", "") or "").strip().lower(),
            app_version,
        )
        if remote_channel and remote_channel != active_channel:
            if getattr(self, "_tool_update_recheck", False):
                self._tool_update_recheck = False
                self.check_tool_updates()
            return
        self._tool_update_status = status
        self._latest_update_source = remote_source
        if remote_channel:
            self._latest_update_channel = remote_channel
        if status == "update_available":
            self._latest_remote_version = remote_version
            self._tool_update_available_flag = True
        else:
            self._latest_remote_version = ""
            self._tool_update_available_flag = False
        self._update_tool_update_visibility()
        self.refresh_system_status()
        if getattr(self, "_tool_update_recheck", False):
            self._tool_update_recheck = False
            self.check_tool_updates()

    @pyqtSlot(str)
    def _on_tool_update_available(self, remote_version: str = "") -> None:
        """Slot triggered when a tool update is available (backward-compatibility)."""
        self._on_tool_update_result("update_available", remote_version)

    def _update_tool_update_visibility(self) -> None:
        """Only show the update label when we are on the main/idle screen (layout index 0)."""
        if hasattr(self, "bottom_titlebar") and self.bottom_titlebar:
            show = (
                getattr(self, "_tool_update_available_flag", False)
                and getattr(self, "simplified_terminal", None)
                and self.simplified_terminal.layout.currentIndex() == 0
            )
            self.bottom_titlebar.show_update_indicator(show)

    def run_self_update(self) -> None:
        """Install update via AppUpdateManager (delta first, full download fallback)."""
        if self.app_update_manager:
            self.app_update_manager.run_self_update()

    @pyqtSlot()
    def _on_update_success(self) -> None:
        ThemedMessageBox.information(
            self,
            "Update Installed",
            "The update has been downloaded and installed successfully.\n\n"
            "Please restart ASSella to use the new version.",
        )

    @pyqtSlot(str)
    def _on_update_failed(self, error_msg: str) -> None:
        mgr = getattr(self, "app_update_manager", None)
        failed_server = getattr(mgr, "_last_failed_server", None) or normalize_update_server(
            str(self.settings.value("update_server", "auto") or "auto")
        )
        channel = get_effective_update_channel(
            str(self.settings.value("update_channel", "") or "").strip().lower(),
            app_version,
        )
        inst_branch = "main" if channel == "stable" else "beta"
        releases_url = "https://github.com/niwia/ASSella/releases"
        install_cmd = f"curl -fsSL https://raw.githubusercontent.com/niwia/ASSella/{inst_branch}/install.sh | bash"
        body = (
            f"Failed to download or apply the update.\n\nError: {error_msg}\n\n"
            f"GitHub Releases:\n{releases_url}\n\n"
            f"Manual installer command:\n{install_cmd}"
        )

        if failed_server == "github" and channel in ("stable", "beta"):
            choice = ThemedMessageBox.choices(
                self,
                "Update Failed",
                body,
                [("Try R2 Mirror", "r2"), ("Open GitHub Releases", "releases"), ("Close", "close")],
                default="r2",
                severity="warning",
                informative=(
                    "Update Server is set to GitHub only. You can retry with Cloudflare R2, "
                    "or download manually / run install.sh from GitHub."
                ),
            )
            if choice == "r2" and mgr is not None:
                mgr.run_self_update(server_override="r2", skip_prompt=True)
                return
        else:
            choice = ThemedMessageBox.choices(
                self,
                "Update Failed",
                body,
                [("Open GitHub Releases", "releases"), ("Close", "close")],
                default="close",
                severity="warning",
                informative=(
                    "Automatic delta and full-download sources failed. Please download the latest "
                    "AppImage manually or run the install script from GitHub."
                ),
            )

        if choice == "releases":
            QDesktopServices.openUrl(QUrl(releases_url))