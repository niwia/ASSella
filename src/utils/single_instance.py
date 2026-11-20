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
                # Newline-framed so the server can reassemble partial reads.
                data = json.dumps(payload).encode("utf-8") + b"\n"
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
            self.server = QLocalServer(self)
            self.server.newConnection.connect(self._on_new_connection)
            if self.server.listen(self.server_name):
                logger.debug(
                    f"[SingleInstance] IPC server listening on '{self.server_name}'"
                )
                return True
            # Only purge the socket node after a failed bind: an unconditional
            # removeServer() would unlink a live instance's socket during the
            # startup race, leaving both instances running with IPC dead.
            QLocalServer.removeServer(self.server_name)
            if self.server.listen(self.server_name):
                logger.debug(
                    f"[SingleInstance] IPC server listening on '{self.server_name}' after stale-socket cleanup"
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
        while self.server.hasPendingConnections():
            client = self.server.nextPendingConnection()
            if not client:
                break
            self._wire_client(client)

    def _wire_client(self, client):
        buffer = bytearray()

        def _read_and_process():
            nonlocal buffer
            try:
                buffer.extend(bytes(client.readAll()))
                # Messages are newline-framed; process every complete line and
                # keep any partial tail buffered for the next readyRead.
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    if not line.strip():
                        continue
                    payload = json.loads(line.decode("utf-8"))
                    logger.info(
                        f"[SingleInstance] Received IPC message from secondary launch: {payload}"
                    )
                    if self.on_command_received:
                        self.on_command_received(payload)
                    client.disconnectFromServer()
            except Exception as e:
                logger.debug(f"[SingleInstance] Error parsing IPC payload: {e}")
                client.disconnectFromServer()

        def _cleanup():
            client.deleteLater()

        client.readyRead.connect(_read_and_process)
        client.disconnected.connect(_cleanup)

    def close(self):
        """Clean up the local server."""
        if self.server:
            try:
                self.server.close()
                QLocalServer.removeServer(self.server_name)
            except Exception as e:
                logger.debug(f"[SingleInstance] Error shutting down IPC server: {e}")
            self.server = None

