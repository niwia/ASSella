"""Binary discovery, architecture detection, executable permissions, and port allocation for Kuroko."""

import logging
import os
import platform
import shutil
import socket
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from utils.paths import Paths

logger = logging.getLogger(__name__)

# Upstream static binary releases for wireproxy (userspace WireGuard to SOCKS5)
WIREPROXY_RELEASE_VERSION = "v1.1.3"
WIREPROXY_BASE_URL = f"https://github.com/windtf/wireproxy/releases/download/{WIREPROXY_RELEASE_VERSION}"


def find_free_port(preferred: int = 1080, host: str = "127.0.0.1") -> int:
    """Check if preferred port is free; if not, return an OS-assigned ephemeral port."""
    if preferred > 0:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, preferred))
                return preferred
        except OSError as e:
            logger.debug(f"[Kuroko] Preferred port {preferred} is unavailable: {e}")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def ensure_wireproxy_binary(progress_callback: Optional[Callable[[str], None]] = None) -> Path:
    """
    Verify wireproxy binary exists in Paths.deps("wireproxy"), system PATH, or fallback.
    If missing, automatically downloads the static Go binary from upstream release.
    Handles read-only AppImage mounts and ensures executable (0o755) permissions.
    """
    # 1. Check system PATH
    sys_bin = shutil.which("wireproxy")
    if sys_bin and os.access(sys_bin, os.X_OK):
        return Path(sys_bin)

    # 2. Check Paths.deps and writable fallback (for read-only AppImage mounts)
    from utils.helpers import get_base_path

    candidate_paths = [
        Paths.deps("wireproxy"),
        Path(get_base_path()) / "deps" / "wireproxy",
    ]
    for candidate in candidate_paths:
        if candidate.is_file():
            if os.access(candidate, os.X_OK):
                return candidate
            try:
                os.chmod(candidate, candidate.stat().st_mode | 0o755)
                if os.access(candidate, os.X_OK):
                    return candidate
            except OSError as e:
                logger.debug(f"[Kuroko] Failed to chmod candidate {candidate}: {e}")

    dest_path = candidate_paths[0]

    # 3. Detect architecture
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        asset_name = "wireproxy_linux_amd64.tar.gz"
    elif arch in ("aarch64", "arm64"):
        asset_name = "wireproxy_linux_arm64.tar.gz"
    else:
        raise RuntimeError(f"Unsupported architecture for automated wireproxy download: {arch}")

    # 4. Resolve a writable destination directory (handles read-only AppImage mounts)
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        probe = dest_path.parent / ".perm_probe"
        probe.touch()
        probe.unlink()
    except OSError as e:
        logger.debug(f"[Kuroko] Primary deps directory not writable ({e}); falling back to base_path.")
        dest_path = Path(get_base_path()) / "deps" / "wireproxy"
        dest_path.parent.mkdir(parents=True, exist_ok=True)

    url = f"{WIREPROXY_BASE_URL}/{asset_name}"
    if progress_callback:
        progress_callback(f"Downloading userspace stealth proxy binary ({arch})...")
    logger.info(f"[Kuroko] Downloading wireproxy binary from {url} to {dest_path}...")

    try:
        with tempfile.TemporaryDirectory(prefix="wireproxy_dl_") as tmp_dir:
            tar_path = Path(tmp_dir) / asset_name
            req = urllib.request.Request(url, headers={"User-Agent": "ASSella/3.0"})
            with urllib.request.urlopen(req, timeout=30.0) as resp, open(tar_path, "wb") as out_f:
                shutil.copyfileobj(resp, out_f)

            with tarfile.open(tar_path, "r:gz") as tar:
                member = tar.getmember("wireproxy")
                tar.extract(member, path=tmp_dir)
                extracted_bin = Path(tmp_dir) / "wireproxy"

                if not extracted_bin.is_file() or extracted_bin.stat().st_size == 0:
                    raise RuntimeError("Extracted wireproxy binary is empty or missing.")

                # Atomically replace destination
                temp_dest = dest_path.with_suffix(".tmp")
                shutil.copy2(extracted_bin, temp_dest)
                os.chmod(temp_dest, 0o755)
                os.replace(temp_dest, dest_path)
                os.chmod(dest_path, 0o755)
    except Exception as e:
        logger.error(f"[Kuroko] Failed to download wireproxy binary: {e}")
        raise RuntimeError(
            f"Failed to acquire stealth proxy binary for {arch}: {e}. "
            "Please ensure internet access or install wireproxy into PATH or src/deps/."
        ) from e

    if progress_callback:
        progress_callback("Kuroko stealth binary ready.")
    logger.info(f"[Kuroko] Successfully installed wireproxy to {dest_path}")
    return dest_path


ensure_kuroko_binary = ensure_wireproxy_binary


def get_kuroko_binary_path(progress_callback: Optional[Callable[[str], None]] = None) -> Path:
    """Return the validated Path to the kuroko / wireproxy binary, downloading if missing."""
    return ensure_wireproxy_binary(progress_callback=progress_callback)


__all__ = [
    "WIREPROXY_BASE_URL",
    "WIREPROXY_RELEASE_VERSION",
    "ensure_kuroko_binary",
    "ensure_wireproxy_binary",
    "find_free_port",
    "get_kuroko_binary_path",
]