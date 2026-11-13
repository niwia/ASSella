"""Userspace WireGuard-to-SOCKS5 proxy engine (Kuroko) and Cloudflare WARP client.

Enables rootless, leak-free, userspace proxying for DepotDownloaderMod depot downloads
on Linux and SteamOS without modifying system network routing or requiring root / sudo.
Named after Kuroko (黒子) — the invisible, stagehand shadow in traditional Japanese theatre.
"""

import atexit
import base64
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Tuple

from utils.paths import Paths

logger = logging.getLogger(__name__)

# Cloudflare consumer registration endpoint used by wgcf / WARP clients
CF_WARP_REG_URL = "https://api.cloudflareclient.com/v0a2158/reg"

# Upstream static binary releases for wireproxy (userspace WireGuard to SOCKS5)
WIREPROXY_RELEASE_VERSION = "v1.1.3"
WIREPROXY_BASE_URL = f"https://github.com/windtf/wireproxy/releases/download/{WIREPROXY_RELEASE_VERSION}"

# Thread-safe lock for process management
_kuroko_lock = threading.Lock()
_global_kuroko_instance: Optional["Kuroko"] = None


def _generate_x25519_keypair() -> Tuple[str, str]:
    """Generate a base64-encoded WireGuard Curve25519 (private_key, public_key) pair."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import x25519

        private_key = x25519.X25519PrivateKey.generate()
        public_key = private_key.public_key()

        priv_b64 = base64.b64encode(
            private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode("ascii")

        pub_b64 = base64.b64encode(
            public_key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ).decode("ascii")

        return priv_b64, pub_b64
    except Exception as e:
        logger.debug(f"[Kuroko] Pure-python cryptography keygen unavailable ({e}), trying wg CLI fallback...")

    # Fallback to wg binary if installed on the host
    wg_bin = shutil.which("wg")
    if wg_bin:
        try:
            priv_proc = subprocess.run([wg_bin, "genkey"], capture_output=True, text=True, check=True)
            priv_key = priv_proc.stdout.strip()
            pub_proc = subprocess.run([wg_bin, "pubkey"], input=priv_key, capture_output=True, text=True, check=True)
            pub_key = pub_proc.stdout.strip()
            return priv_key, pub_key
        except Exception as e:
            logger.debug(f"[Kuroko] wg CLI keygen failed: {e}")

    raise RuntimeError(
        "Failed to generate Curve25519 WireGuard keypair. "
        "Please ensure the 'cryptography' library or 'wg' CLI tool is available."
    )


def generate_warp_config(timeout: float = 10.0) -> str:
    """
    Register a free client-side Cloudflare WARP account via Cloudflare Client API
    and return a WireGuard .conf content string with private key and assigned IP.
    """
    private_key, public_key = _generate_x25519_keypair()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    payload = {
        "install_id": "",
        "fcm_token": "",
        "tos": now_iso,
        "key": public_key,
        "type": "Android",
        "model": "PC",
        "locale": "en_US",
    }

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        CF_WARP_REG_URL,
        data=req_data,
        headers={
            "User-Agent": "okhttp/3.12.1",
            "Content-Type": "application/json; charset=UTF-8",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error(f"[Kuroko] WARP registration HTTP error {e.code}: {body}")
        raise RuntimeError(f"Cloudflare WARP registration failed (HTTP {e.code}): {body}") from e
    except Exception as e:
        logger.error(f"[Kuroko] WARP registration failed: {e}")
        raise RuntimeError(f"Failed to connect to Cloudflare WARP API: {e}") from e

    config = data.get("config", {})
    interface_cfg = config.get("interface", {})
    addresses = interface_cfg.get("addresses", {})
    v4 = addresses.get("v4")
    v6 = addresses.get("v6")

    peers = config.get("peers", [])
    peer = peers[0] if peers else {}
    peer_pub = peer.get("public_key")
    peer_endpoint = peer.get("endpoint", {}).get("host")

    if not (v4 and peer_pub and peer_endpoint):
        raise RuntimeError(f"Unexpected Cloudflare WARP registration response schema: {data}")

    addr_line = f"{v4}/32"
    if v6:
        addr_line += f", {v6}/128"

    conf_text = (
        "[Interface]\n"
        f"PrivateKey = {private_key}\n"
        f"Address = {addr_line}\n"
        "DNS = 1.1.1.1, 1.0.0.1\n"
        "MTU = 1280\n\n"
        "[Peer]\n"
        f"PublicKey = {peer_pub}\n"
        "AllowedIPs = 0.0.0.0/0, ::/0\n"
        f"Endpoint = {peer_endpoint}\n"
        "PersistentKeepalive = 25\n"
    )
    return conf_text


def get_cached_warp_config_path() -> Path:
    """Return the persistent path where a generated WARP profile is stored."""
    cfg_dir = Path.home() / ".config" / "ASSella"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir / "warp_profile.conf"


def get_or_create_warp_config(force_refresh: bool = False, timeout: float = 10.0) -> str:
    """
    Retrieve cached Cloudflare WARP config or generate a fresh one if missing or requested.
    Saves with 0o600 permissions so private keys are never world-readable.
    """
    conf_path = get_cached_warp_config_path()
    if not force_refresh and conf_path.is_file():
        try:
            content = conf_path.read_text(encoding="utf-8")
            if "[Interface]" in content and "[Peer]" in content:
                return content
        except Exception as e:
            logger.debug(f"[Kuroko] Failed to read cached WARP config: {e}")

    # Generate fresh config
    new_config = generate_warp_config(timeout=timeout)
    try:
        fd, tmp = tempfile.mkstemp(dir=conf_path.parent, prefix="warp_", suffix=".tmp")
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_config)
        os.replace(tmp, conf_path)
    except Exception as e:
        logger.warning(f"[Kuroko] Failed to persist WARP config cache: {e}")

    return new_config


def assemble_kuroko_config(config_text_or_path: str, port: int = 1080, host: str = "127.0.0.1") -> str:
    """
    Takes a raw WireGuard configuration string or file path, strips any existing
    system-level network hooks (PostUp, PreDown, SaveConfig), and appends the
    [Socks5] listener block for Kuroko / wireproxy.
    """
    if os.path.isfile(config_text_or_path):
        with open(config_text_or_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    else:
        lines = config_text_or_path.splitlines(keepends=True)

    cleaned_lines = []
    skip_section = False

    for line in lines:
        stripped = line.strip()

        # Check section headers
        if stripped.startswith("[") and stripped.endswith("]"):
            section_name = stripped[1:-1].strip().lower()
            if section_name in ("socks5", "http"):
                skip_section = True
                continue
            else:
                skip_section = False

        if skip_section:
            continue

        # Strip routing/interface mutating directives that need root/netlink
        if re.match(r"^\s*(postup|predown|postdown|preup|saveconfig)\s*=", stripped, re.IGNORECASE):
            continue

        cleaned_lines.append(line)

    base_config = "\n".join(cleaned_lines).strip()
    socks_block = (
        "\n\n[Socks5]\n"
        f"BindAddress = {host}:{port}\n"
    )
    return base_config + socks_block


# Alias for backward compatibility
assemble_wireproxy_config = assemble_kuroko_config


def find_free_port(preferred: int = 1080, host: str = "127.0.0.1") -> int:
    """Check if preferred port is free; if not, return an OS-assigned ephemeral port."""
    if preferred > 0:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, preferred))
                return preferred
        except OSError:
            pass

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
            except OSError:
                pass

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
    except OSError:
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


# Alias
ensure_kuroko_binary = ensure_wireproxy_binary


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

        resolved = ensure_wireproxy_binary()
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
        with _kuroko_lock:
            self.stop()

            # Ensure binary exists (downloads if missing)
            bin_path = Path(self.binary_path)
            if not bin_path.is_file() or not os.access(bin_path, os.X_OK):
                ensure_wireproxy_binary()
                bin_path = Path(self.binary_path)
            if not bin_path.is_file() or not os.access(bin_path, os.X_OK):
                raise RuntimeError(
                    f"wireproxy executable not found or not executable at '{bin_path}'."
                )

            # Resolve free port if port is busy or 0
            allocated_port = find_free_port(preferred=port, host=host)
            self._bind_host = host
            self._bind_port = allocated_port

            final_config = assemble_kuroko_config(config_text_or_path, port=allocated_port, host=host)

            fd, tmp_name = tempfile.mkstemp(prefix="assella_wp_", suffix=".conf")
            try:
                os.chmod(tmp_name, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(final_config)
            except Exception:
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

            # Active probe: poll until SOCKS5 handshake succeeds or process dies
            deadline = time.time() + startup_timeout
            while time.time() < deadline:
                poll_code = self._process.poll()
                if poll_code is not None:
                    stderr_out = ""
                    if self._process.stderr:
                        try:
                            stderr_out = self._process.stderr.read().strip()
                        except Exception:
                            pass
                    logger.error(f"[Kuroko] wireproxy exited prematurely with code {poll_code}: {stderr_out}")
                    self.stop()
                    return False

                if self._check_socks5_ready(host, allocated_port):
                    logger.info(
                        f"[Kuroko] wireproxy listening on socks5h://{host}:{allocated_port} "
                        f"(PID {self._process.pid})"
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
                except (subprocess.TimeoutExpired, Exception):
                    try:
                        proc.kill()
                        proc.wait(timeout=1.0)
                    except Exception:
                        pass

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
        except OSError:
            return False

    @staticmethod
    def test_connection(proxy_url: str, timeout: float = 8.0) -> Tuple[bool, str]:
        """
        Verify end-to-end tunnel connectivity through the SOCKS5 proxy and return
        (success, '<exit_ip> (<org_or_provider>)').
        Uses multi-endpoint fallbacks to guard against public IP API rate limits (HTTP 429).
        """
        # Primary endpoint candidates with free tier access: Cloudflare /trace first (fastest, never rate-limits)
        endpoints = [
            ("https://1.1.1.1/cdn-cgi/trace", lambda text: _parse_cf_trace(text)),
            ("https://ipwho.is/", lambda d: f"{d.get('ip', 'unknown')} ({d.get('connection', {}).get('org', d.get('org', 'Unknown'))} - {d.get('country', '')})"),
            ("https://ipinfo.io/json", lambda d: f"{d.get('ip', 'unknown')} ({d.get('org', 'Unknown')} - {d.get('country', '')})"),
        ]

        # 1. Prefer curl_cffi if installed
        try:
            from curl_cffi import requests as cffi_requests

            for url, parser in endpoints:
                try:
                    resp = cffi_requests.get(
                        url,
                        proxies={"http": proxy_url, "https": proxy_url},
                        timeout=timeout,
                    )
                    if resp.status_code == 200:
                        if url.endswith("/trace"):
                            label = parser(resp.text)
                        else:
                            label = parser(resp.json())
                        return True, label
                except Exception as endpoint_err:
                    logger.debug(f"[Kuroko] Endpoint {url} failed: {endpoint_err}")
                    continue
        except ImportError:
            pass

        # 2. Fallback: shell out to curl
        curl_bin = shutil.which("curl")
        if curl_bin:
            for url, parser in endpoints:
                try:
                    out = subprocess.check_output(
                        [curl_bin, "-fsSL", "--max-time", str(int(timeout)), "-x", proxy_url, url],
                        text=True,
                    )
                    if url.endswith("/trace"):
                        label = parser(out)
                    else:
                        label = parser(json.loads(out))
                    return True, label
                except Exception as endpoint_err:
                    logger.debug(f"[Kuroko] curl check {url} failed: {endpoint_err}")
                    continue

        return False, "Failed to verify connection through proxy across all verification endpoints."


# Exact alias for backwards compatibility
WireproxyManager = Kuroko


def _parse_cf_trace(trace_text: str) -> str:
    """Parse Cloudflare's /cdn-cgi/trace output into a human-readable string."""
    ip, loc, warp = "unknown", "", ""
    for line in trace_text.splitlines():
        if line.startswith("ip="):
            ip = line.split("=", 1)[1]
        elif line.startswith("loc="):
            loc = line.split("=", 1)[1]
        elif line.startswith("warp="):
            warp = line.split("=", 1)[1]
    tag = "Cloudflare WARP" if warp == "on" else "Cloudflare Edge"
    return f"{ip} ({tag}{f' - {loc}' if loc else ''})"


def get_kuroko() -> Kuroko:
    """Return the global thread-safe Kuroko instance."""
    global _global_kuroko_instance
    with _kuroko_lock:
        if _global_kuroko_instance is None:
            _global_kuroko_instance = Kuroko()
        return _global_kuroko_instance


# Backward compatibility alias
get_wireproxy_manager = get_kuroko


def resolve_stealth_proxy_url(settings=None) -> Tuple[Optional[str], Optional[Kuroko]]:
    """
    Check if stealth proxy is enabled in settings. If enabled:
    - If mode is 'warp': spawns an isolated userspace Kuroko bridge with Cloudflare WARP.
    - If mode is 'wireguard': spawns Kuroko with the user's .conf file.
    - If mode is 'custom': returns the custom SOCKS5/HTTP URL.
    Returns (proxy_url, kuroko_manager_instance_or_none).
    Fails closed: raises RuntimeError on configuration or initialization errors.
    """
    if settings is None:
        from utils.settings import get_settings
        settings = get_settings()

    enable_proxy = settings.value("enable_stealth_proxy", False, type=bool)
    if not enable_proxy:
        return None, None

    mode = settings.value("stealth_proxy_mode", "warp")
    manager = Kuroko()

    if mode == "warp":
        cfg = get_or_create_warp_config()
        if not manager.start(cfg, port=0):
            raise RuntimeError("Failed to negotiate SOCKS5 greeting with Kuroko (WARP mode).")
        return manager.proxy_url, manager

    elif mode == "wireguard":
        conf_path = settings.value("stealth_proxy_wg_path", "")
        if isinstance(conf_path, str):
            conf_path = conf_path.strip()
        if not conf_path or not os.path.isfile(conf_path):
            raise RuntimeError(
                f"WireGuard config file not found: '{conf_path}'. "
                "Please select a valid .conf file in Settings -> Stealth."
            )
        if not manager.start(conf_path, port=0):
            raise RuntimeError("Failed to negotiate SOCKS5 greeting with Kuroko (WireGuard mode).")
        return manager.proxy_url, manager

    elif mode == "custom":
        custom_url = settings.value("stealth_proxy_custom_url", "socks5://127.0.0.1:1080")
        if isinstance(custom_url, str):
            custom_url = custom_url.strip()
        if not custom_url:
            raise RuntimeError("Custom proxy mode is enabled, but no proxy URL was specified.")
        return custom_url, None

    else:
        raise RuntimeError(f"Unsupported stealth proxy mode: '{mode}'.")


def test_stealth_proxy_connection(
    mode: str,
    wg_path: str = "",
    custom_url: str = "",
    timeout: float = 8.0,
) -> Tuple[bool, str]:
    """
    Test a candidate stealth proxy setup without affecting global state or running tasks.
    Spawns an ephemeral Kuroko manager if needed, tests connection endpoints, and tears down cleanly.
    Returns (success: bool, status_message_or_endpoint_label: str).
    """
    mode = (mode or "warp").lower().strip()
    if mode == "warp":
        manager = Kuroko()
        try:
            cfg = get_or_create_warp_config()
            if not manager.start(cfg, port=0):
                return False, "Failed to start stealth proxy for Cloudflare WARP."
            return manager.test_connection(manager.proxy_url, timeout=timeout)
        except Exception as e:
            return False, f"WARP error: {e}"
        finally:
            manager.stop()

    elif mode == "wireguard":
        conf_path = (wg_path or "").strip()
        if not conf_path or not os.path.isfile(conf_path):
            return False, f"Config file not found: '{conf_path}'"
        manager = Kuroko()
        try:
            if not manager.start(conf_path, port=0):
                return False, "Failed to start stealth proxy with WireGuard config."
            return manager.test_connection(manager.proxy_url, timeout=timeout)
        except Exception as e:
            return False, f"WireGuard error: {e}"
        finally:
            manager.stop()

    elif mode == "custom":
        url = (custom_url or "").strip()
        if not url:
            return False, "No proxy URL specified."
        if "://" not in url:
            url = f"socks5://{url}"
        manager = Kuroko()
        return manager.test_connection(url, timeout=timeout)

    else:
        return False, f"Unsupported proxy mode: '{mode}'"

