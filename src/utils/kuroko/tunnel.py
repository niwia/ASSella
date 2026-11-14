"""Core Kuroko tunnel class managing the wireproxy subprocess lifecycle."""

import atexit
import logging
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import weakref
from pathlib import Path
from typing import Optional, Tuple

from .binary import (
    ensure_wireproxy_binary as _default_ensure_binary,
    find_free_port as _default_find_free_port,
)
from .config import assemble_kuroko_config as _default_assemble_config

logger = logging.getLogger(__name__)

# Thread-safe lock for process management
_kuroko_lock = threading.Lock()
_global_kuroko_instance: Optional["Kuroko"] = None

ensure_wireproxy_binary = _default_ensure_binary
find_free_port = _default_find_free_port
assemble_kuroko_config = _default_assemble_config


def _resolve_symbol(name: str, local_sym, default_sym):
    if local_sym is not default_sym:
        return local_sym
    pkg = sys.modules.get("utils.kuroko")
    if pkg is not None:
        pkg_sym = getattr(pkg, name, default_sym)
        if pkg_sym is not default_sym:
            return pkg_sym
    return local_sym


class Kuroko:
    """Manages a rootless userspace stealth proxy subprocess and ephemeral WireGuard configs."""

    def __init__(self, binary_path: Optional[str] = None):
        self._custom_binary_path = binary_path
        self._process: Optional[subprocess.Popen] = None
        self._temp_conf_path: Optional[Path] = None
        self._bind_host: str = "127.0.0.1"
        self._bind_port: int = 1080
        weak_self = weakref.ref(self)
        atexit.register(lambda: (obj := weak_self()) and obj.stop())

    @property
    def binary_path(self) -> str:
        """Locate wireproxy in Paths.deps first, then system PATH, or auto-fetch."""
        if self._custom_binary_path:
            return str(self._custom_binary_path)

        ensure_fn = _resolve_symbol(
            "ensure_wireproxy_binary", ensure_wireproxy_binary, _default_ensure_binary
        )
        resolved = ensure_fn()
        return str(resolved)

    @property
    def proxy_url(self) -> str:
        return f"socks5h://{self._bind_host}:{self._bind_port}"

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(
        self,
        config_text_or_path: str,
        port: int = 1080,
        host: str = "127.0.0.1",
        startup_timeout: float = 5.0,
    ) -> bool:
        """
        Write an ephemeral config with [Socks5] BindAddress, spawn wireproxy,
        and verify that the local SOCKS5 port completes a SOCKS5 greeting handshake.
        """
        ensure_fn = _resolve_symbol(
            "ensure_wireproxy_binary", ensure_wireproxy_binary, _default_ensure_binary
        )
        port_fn = _resolve_symbol("find_free_port", find_free_port, _default_find_free_port)
        assemble_fn = _resolve_symbol(
            "assemble_kuroko_config", assemble_kuroko_config, _default_assemble_config
        )

        with _kuroko_lock:
            self.stop()

            # Ensure binary exists (downloads if missing)
            bin_path = Path(self.binary_path)
            if not bin_path.is_file() or not os.access(bin_path, os.X_OK):
                ensure_fn()
                bin_path = Path(self.binary_path)
            if not bin_path.is_file() or not os.access(bin_path, os.X_OK):
                raise RuntimeError(
                    f"wireproxy executable not found or not executable at '{bin_path}'."
                )

            # Resolve free port if port is busy or 0
            allocated_port = port_fn(preferred=port, host=host)
            self._bind_host = host
            self._bind_port = allocated_port

            final_config = assemble_fn(config_text_or_path, port=allocated_port, host=host)

            fd, tmp_name = tempfile.mkstemp(prefix="assella_wp_", suffix=".conf")
            try:
                os.chmod(tmp_name, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(final_config)
            except Exception as e:
                logger.debug(f"[Kuroko] Failed writing temporary config '{tmp_name}': {e}")
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
                raise

            self._temp_conf_path = Path(tmp_name)

            try:
                # Use silent mode (-s) and DEVNULL to prevent pipe buffer deadlocks on verbose debug logs
                self._process = subprocess.Popen(
                    [str(bin_path), "-s", "-c", str(self._temp_conf_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception as e:
                logger.error(f"[Kuroko] Failed to spawn wireproxy: {e}")
                self.stop()
                raise

        # Active probe outside global lock: poll until SOCKS5 handshake succeeds or process dies
        deadline = time.time() + startup_timeout
        while time.time() < deadline:
            proc = self._process
            if proc is None:
                return False
            poll_code = proc.poll()
            if poll_code is not None:
                stderr_out = ""
                if proc.stderr:
                    try:
                        stderr_out = proc.stderr.read().strip()
                    except Exception as e:
                        logger.debug(f"[Kuroko] Failed to read stderr: {e}")
                logger.error(f"[Kuroko] wireproxy exited prematurely with code {poll_code}: {stderr_out}")
                self.stop()
                return False

            if self._check_socks5_ready(host, allocated_port):
                pid = proc.pid if proc else 0
                logger.info(
                    f"[Kuroko] wireproxy listening on socks5h://{host}:{allocated_port} "
                    f"(PID {pid})"
                )
                return True
            time.sleep(0.05)

        logger.error(f"[Kuroko] Timed out waiting for wireproxy SOCKS5 on {host}:{allocated_port}")
        self.stop()
        return False

    # Alias for backward compatibility
    start_wireproxy = start

    def stop(self) -> None:
        """Terminate the wireproxy subprocess, close pipes, and shred the temp config file."""
        proc = self._process
        self._process = None
        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2.0)
                except (subprocess.TimeoutExpired, Exception) as term_err:
                    logger.debug(f"[Kuroko] wireproxy terminate did not exit cleanly ({term_err}); killing.")
                    try:
                        proc.kill()
                        proc.wait(timeout=1.0)
                    except Exception as e:
                        logger.debug(f"[Kuroko] Failed to kill wireproxy: {e}")

        if self._temp_conf_path is not None:
            conf_path = self._temp_conf_path
            self._temp_conf_path = None
            try:
                if conf_path.exists():
                    size = conf_path.stat().st_size
                    conf_path.write_bytes(b"\x00" * size)
                    conf_path.unlink()
            except Exception as exc:
                logger.debug(f"[Kuroko] Error removing temporary config: {exc}")

    # Alias for backward compatibility
    stop_wireproxy = stop

    @staticmethod
    def _check_socks5_ready(host: str, port: int) -> bool:
        """Perform a minimal SOCKS5 auth negotiation greeting to confirm readiness."""
        try:
            with socket.create_connection((host, port), timeout=0.4) as sock:
                sock.sendall(b"\x05\x01\x00")
                reply = sock.recv(2)
                return reply == b"\x05\x00"
        except OSError as e:
            logger.debug(f"[Kuroko] SOCKS5 readiness probe on {host}:{port} not ready: {e}")
            return False

    @staticmethod
    def test_connection(proxy_url: str, timeout: float = 8.0) -> Tuple[bool, str]:
        """Delegate tunnel connectivity check to probe.test_connection."""
        from .probe import test_connection as _test_connection

        return _test_connection(proxy_url, timeout=timeout)

    @staticmethod
    def probe_alive(proxy_url: str, timeout: float = 3.5) -> bool:
        """Delegate fast live-peer transport check to probe.probe_alive."""
        from .probe import probe_alive as _probe_alive

        return _probe_alive(proxy_url, timeout=timeout)

    @staticmethod
    def measure_speed(proxy_url: str, timeout: float = 8.0) -> Tuple[bool, int, float, str]:
        """Delegate 5 MB burst RTT and speed test to probe.measure_speed."""
        from .probe import measure_speed as _measure_speed

        return _measure_speed(proxy_url, timeout=timeout)


# Exact alias for backwards compatibility
WireproxyManager = Kuroko


def get_kuroko() -> Kuroko:
    """Return the global thread-safe Kuroko instance."""
    global _global_kuroko_instance
    with _kuroko_lock:
        if _global_kuroko_instance is None:
            _global_kuroko_instance = Kuroko()
        return _global_kuroko_instance


# Backward compatibility alias
get_wireproxy_manager = get_kuroko


__all__ = [
    "Kuroko",
    "WireproxyManager",
    "_global_kuroko_instance",
    "_kuroko_lock",
    "get_kuroko",
    "get_wireproxy_manager",
]