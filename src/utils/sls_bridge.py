"""
SLSBridge Module

Provides bidirectional communication between ASSella and SLSsteam
via the assella_bridge.lua plugin.
"""

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ASSELLA_IPC_SOCKET = "/tmp/assella_ipc.sock"
ASSELLA_CMD_FILE = "/tmp/assella_cmd.json"
SLS_API_PIPE = "/tmp/SLSsteam.API"


class SLSBridge:
    """Manager and IPC server for communicating with SLSsteam plugins."""

    _server_thread: Optional[threading.Thread] = None
    _server_socket: Optional[socket.socket] = None
    _running: bool = False
    _latest_status: Dict[str, Any] = {}
    _last_seen: float = 0.0

    @classmethod
    def start(cls) -> None:
        """Start the background Unix domain socket server to listen for SLSsteam events."""
        if cls._running:
            return

        cls._cleanup_socket()
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(ASSELLA_IPC_SOCKET)
            sock.listen(5)
            sock.settimeout(1.0)
            cls._server_socket = sock
            cls._running = True

            cls._server_thread = threading.Thread(target=cls._listen_loop, daemon=True, name="SLSBridgeIPC")
            cls._server_thread.start()
            logger.info(f"[SLSBridge] IPC server listening at {ASSELLA_IPC_SOCKET}")
        except Exception as e:
            logger.warning(f"[SLSBridge] Failed to start IPC server: {e}")

    @classmethod
    def stop(cls) -> None:
        """Stop the background IPC server and clean up socket file."""
        cls._running = False
        if cls._server_socket:
            try:
                cls._server_socket.close()
            except Exception:
                pass
            cls._server_socket = None
        cls._cleanup_socket()

    @classmethod
    def _cleanup_socket(cls) -> None:
        try:
            if os.path.exists(ASSELLA_IPC_SOCKET):
                os.unlink(ASSELLA_IPC_SOCKET)
        except Exception:
            pass

    @classmethod
    def _listen_loop(cls) -> None:
        while cls._running and cls._server_socket:
            try:
                conn, _ = cls._server_socket.accept()
                with conn:
                    conn.settimeout(1.0)
                    data = conn.recv(4096)
                    if data:
                        text = data.decode("utf-8", errors="replace").strip()
                        cls._handle_incoming_message(text, conn)
            except socket.timeout:
                continue
            except Exception as e:
                if cls._running:
                    logger.debug(f"[SLSBridge] Accept error: {e}")

    @classmethod
    def _handle_incoming_message(cls, text: str, conn: socket.socket) -> None:
        try:
            payload = json.loads(text)
            cls._latest_status = payload
            cls._last_seen = time.time()
            logger.debug(f"[SLSBridge] Received from Steam plugin: {payload}")

            # Send back acknowledgment
            resp = json.dumps({"status": "ok", "ack": payload.get("event", "event")}) + "\n"
            conn.sendall(resp.encode("utf-8"))
        except Exception as e:
            logger.debug(f"[SLSBridge] Failed to parse bridge message: {e}")

    @classmethod
    def is_bridge_active(cls, timeout: float = 30.0) -> bool:
        """Return True if the bridge plugin has reported within the last timeout seconds."""
        return cls._running and (time.time() - cls._last_seen < timeout)

    @classmethod
    def get_latest_status(cls) -> Dict[str, Any]:
        """Return the most recent status received from SLSsteam."""
        return cls._latest_status.copy()

    @classmethod
    def notify_reload(cls) -> bool:
        """Notify SLSsteam of updates.
        SLSsteam's built-in CFileWatcher automatically detects modifications to
        config.yaml and hot-reloads settings without needing forced API signals.
        """
        logger.debug("[SLSBridge] SLSsteam inotify watcher handles config reload automatically")
        return True

    @classmethod
    def send_command(cls, cmd: str, extra: Optional[Dict[str, Any]] = None) -> bool:
        """Write a command for the bridge plugin and trigger SLS reload."""
        payload = {"cmd": cmd}
        if extra:
            payload.update(extra)

        try:
            with open(ASSELLA_CMD_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            return cls.notify_reload()
        except Exception as e:
            logger.error(f"[SLSBridge] Failed to send command {cmd}: {e}")
            return False
