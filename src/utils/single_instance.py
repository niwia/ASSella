import json
import logging
import os
from typing import Any, Callable, Dict, Optional

from PyQt6.QtCore import QObject
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)


def get_ipc_server_name() -> str:
    """Return a unique local server pipe name per operating system user."""
    uid = getattr(os, "getuid", lambda: "win")()
    return f"assella_desktop_ipc_{uid}"


class SingleInstanceIPC(QObject):
    """Manages single-instance application IPC using Qt local sockets.

    If an instance of ASSella is already running, routes command-line
    arguments (e.g. --appid flags or dropped ZIP paths) to the active window
    and informs the secondary process to exit.
    """

    def __init__(
        self,
        on_command_received: Callable[[Dict[str, Any]], None],
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.server_name = get_ipc_server_name()
        self.on_command_received = on_command_received
        self.server: Optional[QLocalServer] = None

    @classmethod
    def send_and_exit_if_running(
        cls, payload: Dict[str, Any], timeout_ms: int = 500
    ) -> bool:
        """Attempt to connect to an existing running ASSella instance.

        Returns True if connected and payload was sent (secondary process should exit),
        or False if no active instance was reachable.
        """
        server_name = get_ipc_server_name()
        socket = QLocalSocket()
        socket.connectToServer(server_name)
        if socket.waitForConnected(timeout_ms):
            try:
                data = json.dumps(payload).encode("utf-8")
                socket.write(data)
                socket.waitForBytesWritten(1000)
                socket.disconnectFromServer()
                logger.info(
                    "[SingleInstance] Sent command payload to existing instance; exiting secondary process."
                )
                return True
            except Exception as e:
                logger.debug(
                    f"[SingleInstance] Error transmitting to running instance: {e}"
                )
            finally:
                socket.close()
        return False

    def start_server(self) -> bool:
        """Start listening for commands from future secondary instances."""
        try:
            # Purge any stale socket node left over from an unclean termination (SIGKILL)
            QLocalServer.removeServer(self.server_name)
            self.server = QLocalServer(self)
            self.server.newConnection.connect(self._on_new_connection)
            if self.server.listen(self.server_name):
                logger.debug(
                    f"[SingleInstance] IPC server listening on '{self.server_name}'"
                )
                return True
            logger.warning(
                f"[SingleInstance] Failed to bind IPC server '{self.server_name}': {self.server.errorString()}"
            )
        except Exception as e:
            logger.debug(f"[SingleInstance] Error starting IPC server: {e}")
        return False

    def _on_new_connection(self):
        if not self.server:
            return
        client = self.server.nextPendingConnection()
        if not client:
            return

        def _read_and_process():
            try:
                raw = bytes(client.readAll())
                if raw:
                    payload = json.loads(raw.decode("utf-8"))
                    logger.info(
                        f"[SingleInstance] Received IPC message from secondary launch: {payload}"
                    )
                    if self.on_command_received:
                        self.on_command_received(payload)
            except Exception as e:
                logger.debug(f"[SingleInstance] Error parsing IPC payload: {e}")
            finally:
                client.disconnectFromServer()
                client.deleteLater()

        client.readyRead.connect(_read_and_process)

    def close(self):
        """Clean up the local server."""
        if self.server:
            try:
                self.server.close()
                QLocalServer.removeServer(self.server_name)
            except Exception as e:
                logger.debug(f"[SingleInstance] Error shutting down IPC server: {e}")
            self.server = None

