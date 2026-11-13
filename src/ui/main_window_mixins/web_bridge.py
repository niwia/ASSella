import logging
import subprocess
import sys

from utils.web_server import get_local_ip

logger = logging.getLogger(__name__)


class MainWindowWebBridgeMixin:
    """Mixin handling the remote Web UI command queue, status snapshots, and server lifecycle."""

    def _process_web_commands(self) -> None:
        while not self.web_command_queue.empty():
            try:
                cmd = self.web_command_queue.get_nowait()
                cmd_type = cmd.get("type")
                if cmd_type == "enqueue_job":
                    path = cmd.get("path")
                    metadata = cmd.get("metadata")
                    logger.info(f"Main Window: Enqueueing job from Web UI for {metadata.get('game_name')}")
                    self.job_queue.add_job(path, metadata)
                elif cmd_type == "check_updates":
                    logger.info("Main Window: Checking updates triggered from Web UI")
                    if self.game_manager:
                        self.game_manager.reset_up_to_date_for_recheck()
                        self.game_manager.check_game_updates_async()
            except Exception as e:
                logger.error(f"Error processing web command: {e}")

    def _refresh_web_status_snapshot(self) -> None:
        """Publish the progress widgets' state for the web UI.

        Runs on the GUI thread (driven by ``web_command_timer``) so the web
        server never has to touch QWidget state from its request threads.
        """
        progress = 0
        progress_visible = False
        speed = ""
        speed_visible = False

        try:
            if self.progress_bar is not None:
                progress_visible = bool(self.progress_bar.isVisible())
                progress = int(self.progress_bar.value())
            if self.speed_label is not None:
                speed_visible = bool(self.speed_label.isVisible())
                # The label elides what it displays; the web UI wants the whole
                # speed string.
                speed = getattr(self.speed_label, "full_text", "") or self.speed_label.text()
        except RuntimeError as exc:
            # Widgets already destroyed (window tearing down).
            logger.debug(f"Web status snapshot refresh skipped: {exc}")
            return

        self._web_status_snapshot = {
            "progress": progress,
            "progress_visible": progress_visible,
            "speed": str(speed),
            "speed_visible": speed_visible,
        }

    def get_web_status_snapshot(self) -> dict:
        """Return a plain-Python copy of the published status snapshot.

        Safe to call from any thread: it only reads a dict of primitives.
        """
        snapshot = getattr(self, "_web_status_snapshot", None) or {}
        return dict(snapshot)

    def toggle_web_server(self, enabled: bool, port: int = 8765) -> None:
        if enabled:
            if not self.web_server_manager.is_running():
                # Loopback by default; LAN binding is an explicit opt-in.
                allow_lan = self.settings.value("web_ui_allow_lan", False, type=bool)
                host = "0.0.0.0" if allow_lan else "127.0.0.1"
                if self.web_server_manager.start(host=host, port=port):
                    self.web_command_timer.start(200)
        else:
            if self.web_server_manager.is_running():
                self.web_server_manager.stop()
                self.web_command_timer.stop()
        self._update_web_ui_status_label()

    def _update_web_ui_status_label(self) -> None:
        if not hasattr(self, "web_ui_status_value") or not self.web_ui_status_value:
            return

        port = self.settings.value("web_ui_port", 8765, type=int)
        allow_lan = self.settings.value("web_ui_allow_lan", False, type=bool)

        # 1. Check if the local web server manager is running in this GUI instance
        if self.web_server_manager and self.web_server_manager.is_running():
            port = self.web_server_manager.server.port
            token = self.web_server_manager.auth_token
            # The UI is same-origin and already carries the token, so the plain
            # loopback URL is enough here. LAN access needs the token in the URL.
            if allow_lan:
                self.web_ui_status_value.setText(f"http://{get_local_ip()}:{port}/?token={token}")
                self.web_ui_status_value.setToolTip(
                    "Anyone on your local network can open this link while the server is running.\n"
                    "The token is regenerated every time the server starts."
                )
            else:
                self.web_ui_status_value.setText(f"http://127.0.0.1:{port}")
                self.web_ui_status_value.setToolTip(
                    "Bound to localhost only. Enable LAN access in settings to reach this from another device."
                )
            self.web_ui_status_value.setStyleSheet(
                f"color: {self.accent_color or '#C06C84'}; font-size: 11px; font-weight: bold; border: none; background: transparent;"
            )
            return

        # 2. Check if the systemd background user service is active (Linux only)
        is_bg_active = False
        if sys.platform == "linux":
            try:
                res = subprocess.run(
                    ["systemctl", "--user", "is-active", "assella-testing.service"],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                if res.stdout.strip() == "active":
                    is_bg_active = True
            except Exception as e:
                logger.debug(f"[WebBridge] Could not check assella-testing.service status: {e}")

        if is_bg_active:
            self.web_ui_status_value.setText(f"http://{get_local_ip()}:{port} (Service)")
            self.web_ui_status_value.setStyleSheet(
                "color: #44cc44; font-size: 11px; font-weight: bold; border: none; background: transparent;"
            )
        else:
            self.web_ui_status_value.setText("Disabled")
            self.web_ui_status_value.setStyleSheet(
                f"color: {self.accent_color or '#C06C84'}; font-size: 11px; font-weight: bold; border: none; background: transparent;"
            )