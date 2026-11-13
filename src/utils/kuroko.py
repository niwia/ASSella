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

# In-memory cooldown tracker for failed stealth endpoints: endpoint_id -> timestamp
_endpoint_cooldowns: dict[str, float] = {}
COOLDOWN_DURATION = 180.0  # 3 minutes

# Network transport and proxy error signatures for smart failover
NETWORK_ERROR_SIGNATURES = (
    "ratelimitexceeded",
    "429",
    "httprequestexception",
    "socketexception",
    "connection reset",
    "timed out",
    "timeout",
    "unable to connect to the remote server",
    "proxy error",
    "econnrefused",
    "econnreset",
    "broken pipe",
    "eof reached",
    "network is unreachable",
    "host is unreachable",
    "connection closed abruptly",
)


def is_network_or_proxy_error(error_text: str) -> bool:
    """Return True only if error_text matches transport or rate-limiting error signatures.

    Explicitly excludes non-network errors such as missing decryption keys (AccessDenied)
    or bad manifests so that valid proxies are not erroneously blacklisted.
    """
    if not error_text:
        return False
    lower = error_text.lower()
    if "accessdenied" in lower or "bad decryption key" in lower or "missing depot key" in lower:
        return False
    return any(sig in lower for sig in NETWORK_ERROR_SIGNATURES)


def is_endpoint_in_cooldown(endpoint_id: str) -> bool:
    """Check if an endpoint is temporarily penalized due to recent errors."""
    now = time.time()
    last_err = _endpoint_cooldowns.get(endpoint_id)
    if last_err is None:
        return False
    if now - last_err > COOLDOWN_DURATION:
        _endpoint_cooldowns.pop(endpoint_id, None)
        return False
    return True


def mark_endpoint_cooldown(endpoint_id: str) -> None:
    """Mark an endpoint in cooldown for COOLDOWN_DURATION seconds."""
    _endpoint_cooldowns[endpoint_id] = time.time()
    logger.warning(f"[Kuroko] Endpoint '{endpoint_id}' placed in cooldown for {int(COOLDOWN_DURATION)}s.")


def clear_endpoint_cooldowns() -> None:
    """Clear all active endpoint cooldown penalties."""
    _endpoint_cooldowns.clear()


class WireguardProfileManager:
    """Manages imported WireGuard .conf files stored in ~/.local/share/ACCELA/stealth/wireguard/."""

    @staticmethod
    def get_profiles_dir() -> Path:
        """Return ~/.local/share/ACCELA/stealth/wireguard/ with directory mode 0o700."""
        from utils.helpers import get_base_path
        p = Path(get_base_path()) / "stealth" / "wireguard"
        p.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(p, 0o700)
        except OSError as e:
            logger.debug(f"[Kuroko] Could not set 0700 on profiles dir: {e}")
        return p

    @classmethod
    def list_profiles(cls) -> list[dict]:
        """List all imported .conf profiles sorted alphabetically."""
        p_dir = cls.get_profiles_dir()
        profiles = []
        for f in sorted(p_dir.glob("*.conf")):
            if f.is_file():
                profiles.append({
                    "filename": f.name,
                    "name": f.stem,
                    "path": str(f.resolve()),
                    "size": f.stat().st_size,
                    "modified": f.stat().st_mtime,
                })
        return profiles

    @classmethod
    def import_profile(cls, src_path: str) -> Optional[dict]:
        """Import a .conf file into storage with strict 0o600 file permissions."""
        src = Path(src_path)
        if not src.is_file():
            return None
        dest_dir = cls.get_profiles_dir()
        base_stem = re.sub(r"[^\w\-.]", "_", src.stem).strip("_") or "wg_profile"
        target_name = f"{base_stem}.conf"
        target_path = dest_dir / target_name
        counter = 1
        while target_path.exists():
            target_name = f"{base_stem}_{counter}.conf"
            target_path = dest_dir / target_name
            counter += 1

        shutil.copy2(src, target_path)
        try:
            os.chmod(target_path, 0o600)
        except OSError as e:
            logger.debug(f"[Kuroko] Could not set 0600 on {target_path}: {e}")

        return {
            "filename": target_name,
            "name": target_path.stem,
            "path": str(target_path.resolve()),
            "size": target_path.stat().st_size,
            "modified": target_path.stat().st_mtime,
        }

    @classmethod
    def delete_profile(cls, filename: str) -> bool:
        """Permanently delete an imported profile from storage."""
        p_dir = cls.get_profiles_dir()
        clean_name = Path(filename).name
        target = p_dir / clean_name
        if target.is_file():
            try:
                target.unlink()
                logger.info(f"[Kuroko] Deleted WireGuard profile: {clean_name}")
                return True
            except OSError as e:
                logger.error(f"[Kuroko] Failed to delete profile {clean_name}: {e}")
                return False
        return False


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
                        except Exception as e:
                            logger.debug(f"[Kuroko] Failed to read stderr: {e}")
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
        except ImportError as e:
            logger.debug(f"[Kuroko] curl_cffi not installed: {e}")

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

    @staticmethod
    def probe_alive(proxy_url: str, timeout: float = 3.5) -> bool:
        """
        Fast probe to verify remote tunnel transport is genuinely passing traffic.
        Because WireGuard is stateless UDP, local SOCKS5 binding alone does not prove
        the remote peer or handshake is active. This sends a zero-byte probe through proxy_url.
        """
        headers = {
            "Referer": "https://speed.cloudflare.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        probe_urls = [
            "https://speed.cloudflare.com/__down?bytes=0",
            "https://1.1.1.1/cdn-cgi/trace",
        ]
        try:
            from curl_cffi import requests as cffi_requests
            for u in probe_urls:
                try:
                    resp = cffi_requests.head(
                        u,
                        headers=headers,
                        proxies={"http": proxy_url, "https": proxy_url},
                        timeout=timeout,
                    )
                    if resp.status_code < 500:
                        return True
                except Exception as probe_err:
                    logger.debug(f"[Kuroko] probe_alive cffi probe to {u} failed: {probe_err}")
                    continue
        except ImportError as e:
            logger.debug(f"[Kuroko] probe_alive curl_cffi unavailable: {e}")

        curl_bin = shutil.which("curl")
        if curl_bin:
            for u in probe_urls:
                try:
                    res = subprocess.run(
                        [
                            curl_bin, "-sS", "-I", "--max-time", str(int(timeout)),
                            "-x", proxy_url,
                            "-H", "Referer: https://speed.cloudflare.com/",
                            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                            "-o", "/dev/null",
                            u,
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=timeout + 1.0,
                    )
                    if res.returncode == 0:
                        return True
                except Exception as curl_err:
                    logger.debug(f"[Kuroko] probe_alive curl probe to {u} failed: {curl_err}")
                    continue

        return False

    @staticmethod
    def measure_speed(proxy_url: str, timeout: float = 8.0) -> Tuple[bool, int, float, str]:
        """
        Measure ping (RTT ms) and throughput (Mbps) using a lightweight 5 MB burst test.
        1. Sends zero-byte HEAD request to https://speed.cloudflare.com/__down?bytes=0
           with Referer: https://speed.cloudflare.com/ to measure pure RTT without bot rate limits.
        2. Streams 5 MB payload from https://speed.cloudflare.com/__down?bytes=5242880 to compute Mbps.
        Returns (success: bool, ping_ms: int, speed_mbps: float, message_or_error: str).
        """
        headers = {
            "Referer": "https://speed.cloudflare.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        ping_url = "https://speed.cloudflare.com/__down?bytes=0"
        speed_url = "https://speed.cloudflare.com/__down?bytes=5242880"
        ping_ms = 0
        speed_mbps = 0.0

        # Attempt 1: curl_cffi
        try:
            from curl_cffi import requests as cffi_requests
            try:
                t0 = time.perf_counter()
                p_resp = cffi_requests.head(
                    ping_url,
                    headers=headers,
                    proxies={"http": proxy_url, "https": proxy_url},
                    timeout=max(2.0, timeout / 2.0),
                )
                if p_resp.status_code < 500:
                    ping_ms = max(1, int((time.perf_counter() - t0) * 1000))
            except Exception as pe:
                logger.debug(f"[Kuroko] cffi ping probe failed: {pe}")

            try:
                t1 = time.perf_counter()
                s_resp = cffi_requests.get(
                    speed_url,
                    headers=headers,
                    proxies={"http": proxy_url, "https": proxy_url},
                    timeout=timeout,
                    stream=True,
                )
                if s_resp.status_code == 200:
                    total_bytes = 0
                    for chunk in s_resp.iter_content(chunk_size=65536):
                        if chunk:
                            total_bytes += len(chunk)
                    elapsed = max(0.001, time.perf_counter() - t1)
                    speed_mbps = round((total_bytes * 8.0) / (elapsed * 1_000_000.0), 2)
                    if ping_ms == 0:
                        ping_ms = max(1, int(elapsed * 200))
                    return True, ping_ms, speed_mbps, f"Ping: {ping_ms}ms | Speed: {speed_mbps} Mbps"
            except Exception as se:
                logger.debug(f"[Kuroko] cffi speed download failed: {se}")
        except ImportError as e:
            logger.debug(f"[Kuroko] curl_cffi not installed for speed test: {e}")

        # Attempt 2: shell curl
        curl_bin = shutil.which("curl")
        if curl_bin:
            try:
                p_out = subprocess.check_output(
                    [
                        curl_bin, "-sS", "-I", "--max-time", str(int(max(2.0, timeout / 2.0))),
                        "-x", proxy_url,
                        "-H", "Referer: https://speed.cloudflare.com/",
                        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "-w", "%{time_starttransfer}",
                        "-o", "/dev/null",
                        ping_url,
                    ],
                    text=True,
                    timeout=timeout,
                )
                try:
                    ping_ms = max(1, int(float(p_out.strip()) * 1000))
                except ValueError as ve:
                    logger.debug(f"[Kuroko] Could not parse curl ping time: {ve}")
            except Exception as pe:
                logger.debug(f"[Kuroko] curl ping probe failed: {pe}")

            try:
                s_out = subprocess.check_output(
                    [
                        curl_bin, "-fsSL", "--max-time", str(int(timeout)),
                        "-x", proxy_url,
                        "-H", "Referer: https://speed.cloudflare.com/",
                        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "-w", "%{speed_download}",
                        "-o", "/dev/null",
                        speed_url,
                    ],
                    text=True,
                    timeout=timeout + 2.0,
                )
                try:
                    bytes_sec = float(s_out.strip())
                    speed_mbps = round((bytes_sec * 8.0) / 1_000_000.0, 2)
                    return True, ping_ms, speed_mbps, f"Ping: {ping_ms}ms | Speed: {speed_mbps} Mbps"
                except ValueError as ve:
                    logger.debug(f"[Kuroko] Could not parse curl speed_download: {ve}")
            except Exception as se:
                logger.debug(f"[Kuroko] curl speed test failed: {se}")

        if ping_ms > 0:
            return True, ping_ms, 0.0, f"Ping: {ping_ms}ms (Speed test rate-limited)"
        return False, 0, 0.0, "Speed test failed to reach endpoint through proxy."


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


def probe_stealth_ready(proxy_url: str, timeout: float = 3.5) -> bool:
    """Check if the remote stealth proxy tunnel is genuinely alive and passing traffic."""
    return Kuroko.probe_alive(proxy_url, timeout=timeout)


def measure_stealth_proxy_speed(
    mode: str,
    wg_path: str = "",
    custom_url: str = "",
    timeout: float = 8.0,
) -> Tuple[bool, int, float, str]:
    """
    Perform a lightweight 5 MB burst speed and ping test on a candidate proxy setting.
    Spawns an ephemeral Kuroko manager if needed, measures RTT and Mbps, and stops cleanly.
    Returns (success: bool, ping_ms: int, speed_mbps: float, message: str).
    """
    mode = (mode or "warp").lower().strip()
    if mode == "warp":
        manager = Kuroko()
        try:
            cfg = get_or_create_warp_config()
            if not manager.start(cfg, port=0):
                return False, 0, 0.0, "Failed to initialize Cloudflare WARP tunnel."
            return manager.measure_speed(manager.proxy_url, timeout=timeout)
        except Exception as e:
            logger.debug(f"[Kuroko] WARP speed test failed: {e}")
            return False, 0, 0.0, f"WARP error: {e}"
        finally:
            manager.stop()

    elif mode == "wireguard":
        conf_path = (wg_path or "").strip()
        if not conf_path or not os.path.isfile(conf_path):
            return False, 0, 0.0, f"WireGuard config not found: '{conf_path}'"
        manager = Kuroko()
        try:
            if not manager.start(conf_path, port=0):
                return False, 0, 0.0, "Failed to start wireproxy with WireGuard config."
            return manager.measure_speed(manager.proxy_url, timeout=timeout)
        except Exception as e:
            logger.debug(f"[Kuroko] WireGuard speed test failed: {e}")
            return False, 0, 0.0, f"WireGuard error: {e}"
        finally:
            manager.stop()

    elif mode == "custom":
        url = (custom_url or "").strip()
        if not url:
            return False, 0, 0.0, "No custom proxy URL specified."
        if "://" not in url:
            url = f"socks5://{url}"
        manager = Kuroko()
        return manager.measure_speed(url, timeout=timeout)

    else:
        return False, 0, 0.0, f"Unsupported stealth mode: '{mode}'"


class StealthPoolManager:
    """Manages multi-endpoint rotation, sequential benchmarking, and smart failover."""

    @staticmethod
    def get_pool_candidates(settings=None) -> list[dict]:
        """Return all configured and enabled candidates in the stealth pool."""
        if settings is None:
            from utils.settings import get_settings
            settings = get_settings()

        candidates = []

        # 1. Cloudflare WARP
        include_warp = settings.value("stealth_pool_warp", True, type=bool)
        if include_warp:
            candidates.append({
                "id": "warp",
                "type": "warp",
                "name": "Cloudflare WARP (Auto)",
                "config_path": "",
                "url": "",
            })

        # 2. Imported WireGuard Profiles
        raw_profiles = settings.value("stealth_pool_wg_profiles", [], type=list)
        p_dir = WireguardProfileManager.get_profiles_dir()
        for item in raw_profiles:
            fname = str(item).strip()
            if not fname:
                continue
            conf_file = p_dir / fname
            if conf_file.is_file():
                candidates.append({
                    "id": f"wg:{fname}",
                    "type": "wireguard",
                    "name": f"WG: {conf_file.stem}",
                    "config_path": str(conf_file.resolve()),
                    "url": "",
                })

        # 3. Custom SOCKS5/HTTP Proxy
        include_custom = settings.value("stealth_pool_custom", False, type=bool)
        if include_custom:
            custom_url = settings.value("stealth_proxy_custom_url", "").strip()
            if custom_url:
                if "://" not in custom_url:
                    custom_url = f"socks5://{custom_url}"
                candidates.append({
                    "id": "custom",
                    "type": "custom",
                    "name": "Custom Proxy",
                    "config_path": "",
                    "url": custom_url,
                })

        return candidates

    @classmethod
    def get_eligible_candidates(cls, settings=None) -> list[dict]:
        """Return candidates that are not currently penalized by a recent failure cooldown."""
        candidates = cls.get_pool_candidates(settings)
        return [c for c in candidates if not is_endpoint_in_cooldown(c["id"])]

    @classmethod
    def benchmark_candidate(cls, candidate: dict, timeout: float = 8.0) -> Tuple[bool, int, float, str]:
        """Benchmark a candidate endpoint in isolation and return (ok, ping_ms, speed_mbps, msg)."""
        c_type = candidate.get("type", "warp")
        if c_type == "warp":
            return measure_stealth_proxy_speed("warp", timeout=timeout)
        elif c_type == "wireguard":
            return measure_stealth_proxy_speed("wireguard", wg_path=candidate.get("config_path", ""), timeout=timeout)
        elif c_type == "custom":
            return measure_stealth_proxy_speed("custom", custom_url=candidate.get("url", ""), timeout=timeout)
        return False, 0, 0.0, f"Unknown candidate type: '{c_type}'"

    @classmethod
    def benchmark_all_candidates(cls, settings=None, progress_callback: Optional[Callable[[str, bool, int, float, str], None]] = None) -> list[dict]:
        """
        Sequentially benchmark all pool candidates to prevent home network saturation.
        Calls progress_callback(candidate_id, success, ping_ms, speed_mbps, message) on each step.
        Returns the candidates list sorted by highest speed_mbps (and lowest ping_ms).
        """
        candidates = cls.get_pool_candidates(settings)
        results = []
        for c in candidates:
            c_id = c["id"]
            ok, ping, speed, msg = cls.benchmark_candidate(c)
            c_copy = dict(c)
            c_copy["ok"] = ok
            c_copy["ping_ms"] = ping
            c_copy["speed_mbps"] = speed
            c_copy["msg"] = msg
            results.append(c_copy)
            if progress_callback is not None:
                try:
                    progress_callback(c_id, ok, ping, speed, msg)
                except Exception as cb_err:
                    logger.debug(f"[Kuroko] Benchmark progress callback error: {cb_err}")

        # Sort: healthy first, then highest speed, then lowest ping
        results.sort(key=lambda x: (not x.get("ok", False), -x.get("speed_mbps", 0.0), x.get("ping_ms", 9999)))
        return results

    @classmethod
    def resolve_best_stealth_endpoint(
        cls, settings=None, previous_failed_id: Optional[str] = None
    ) -> Tuple[str, Optional[Kuroko], str]:
        """
        Select and initialize the best available stealth endpoint.
        If rotation is enabled: tests candidates, verifies live peer reachability,
        and automatically falls over to the next candidate if dead.
        Returns (proxy_url, manager_instance, endpoint_id).
        Raises RuntimeError if all candidates fail or rotation pool is empty.
        """
        if settings is None:
            from utils.settings import get_settings
            settings = get_settings()

        if previous_failed_id:
            mark_endpoint_cooldown(previous_failed_id)

        rotation_on = settings.value("stealth_rotation_enabled", False, type=bool)

        if not rotation_on:
            # Single mode selection
            mode = settings.value("stealth_proxy_mode", "warp")
            manager = Kuroko()
            if mode == "warp":
                cfg = get_or_create_warp_config()
                if not manager.start(cfg, port=0):
                    raise RuntimeError("Failed to start wireproxy for Cloudflare WARP.")
                if not manager.probe_alive(manager.proxy_url):
                    manager.stop()
                    raise RuntimeError("Cloudflare WARP endpoint failed live peer connectivity probe.")
                return manager.proxy_url, manager, "warp"

            elif mode == "wireguard":
                # Check active profile first, then fallback to wg_path
                active_profile = settings.value("stealth_active_wg_profile", "", type=str).strip()
                p_dir = WireguardProfileManager.get_profiles_dir()
                conf_path = ""
                if active_profile and (p_dir / active_profile).is_file():
                    conf_path = str((p_dir / active_profile).resolve())
                else:
                    conf_path = settings.value("stealth_proxy_wg_path", "", type=str).strip()

                if not conf_path or not os.path.isfile(conf_path):
                    raise RuntimeError(
                        f"WireGuard config file not found: '{conf_path}'. "
                        "Please select or import a valid .conf file in Settings -> Stealth."
                    )
                if not manager.start(conf_path, port=0):
                    raise RuntimeError("Failed to start wireproxy with WireGuard config.")
                if not manager.probe_alive(manager.proxy_url):
                    manager.stop()
                    raise RuntimeError(f"WireGuard profile '{Path(conf_path).stem}' failed live peer connectivity probe.")
                return manager.proxy_url, manager, f"wg:{Path(conf_path).name}"

            elif mode == "custom":
                custom_url = settings.value("stealth_proxy_custom_url", "socks5://127.0.0.1:1080").strip()
                if not custom_url:
                    raise RuntimeError("Custom proxy mode is enabled, but no proxy URL was specified.")
                if "://" not in custom_url:
                    custom_url = f"socks5://{custom_url}"
                if not Kuroko.probe_alive(custom_url):
                    raise RuntimeError(f"Custom proxy at '{custom_url}' failed live connectivity probe.")
                return custom_url, None, "custom"

            else:
                raise RuntimeError(f"Unsupported stealth proxy mode: '{mode}'.")

        # Rotation enabled: query eligible pool candidates
        candidates = cls.get_eligible_candidates(settings)
        if not candidates:
            # If all are in cooldown, reset cooldowns once as a last resort
            if _endpoint_cooldowns:
                logger.info("[Kuroko] All endpoints were in cooldown; clearing cooldowns to retry pool.")
                clear_endpoint_cooldowns()
                candidates = cls.get_pool_candidates(settings)

        if not candidates:
            raise RuntimeError(
                "Stealth Endpoint Rotation is enabled, but no pool candidates are checked. "
                "Please enable at least one candidate (WARP, WireGuard, or Custom) in Settings -> Stealth."
            )

        last_error = ""
        for cand in candidates:
            c_id = cand["id"]
            c_type = cand["type"]
            manager = Kuroko()
            try:
                if c_type == "warp":
                    cfg = get_or_create_warp_config()
                    if not manager.start(cfg, port=0):
                        mark_endpoint_cooldown(c_id)
                        last_error = "WARP wireproxy start failed"
                        continue
                    if not manager.probe_alive(manager.proxy_url):
                        logger.warning(f"[Kuroko] Candidate '{c_id}' failed dead peer probe; rotating.")
                        mark_endpoint_cooldown(c_id)
                        manager.stop()
                        last_error = "WARP peer unreachable"
                        continue
                    return manager.proxy_url, manager, c_id

                elif c_type == "wireguard":
                    cfg_path = cand.get("config_path", "")
                    if not cfg_path or not os.path.isfile(cfg_path):
                        mark_endpoint_cooldown(c_id)
                        last_error = f"Config file missing: {cfg_path}"
                        continue
                    if not manager.start(cfg_path, port=0):
                        mark_endpoint_cooldown(c_id)
                        last_error = f"WireGuard start failed for {cand['name']}"
                        continue
                    if not manager.probe_alive(manager.proxy_url):
                        logger.warning(f"[Kuroko] Candidate '{c_id}' failed dead peer probe; rotating.")
                        mark_endpoint_cooldown(c_id)
                        manager.stop()
                        last_error = f"WireGuard peer '{cand['name']}' unreachable"
                        continue
                    return manager.proxy_url, manager, c_id

                elif c_type == "custom":
                    c_url = cand.get("url", "")
                    if not c_url or not Kuroko.probe_alive(c_url):
                        mark_endpoint_cooldown(c_id)
                        last_error = "Custom proxy unreachable"
                        continue
                    return c_url, None, c_id

            except Exception as e:
                logger.warning(f"[Kuroko] Failed initializing candidate '{c_id}': {e}")
                mark_endpoint_cooldown(c_id)
                manager.stop()
                last_error = str(e)
                continue

        raise RuntimeError(f"All stealth pool candidates failed live connectivity verification. Last error: {last_error}")


def resolve_stealth_proxy_url(settings=None, failover_from: Optional[str] = None) -> Tuple[Optional[str], Optional[Kuroko]]:
    """
    Check if stealth proxy is enabled in settings and resolve the active proxy URL and manager.
    Supports single mode, multi-endpoint rotation, and smart dead-peer failover.
    Returns (proxy_url, kuroko_manager_or_none).
    """
    if settings is None:
        from utils.settings import get_settings
        settings = get_settings()

    enable_proxy = settings.value("enable_stealth_proxy", False, type=bool)
    if not enable_proxy:
        return None, None

    url, mgr, cand_id = StealthPoolManager.resolve_best_stealth_endpoint(
        settings=settings, previous_failed_id=failover_from
    )
    if mgr is not None:
        setattr(mgr, "endpoint_id", cand_id)
    return url, mgr


def prompt_stealth_fallback(error_message: str) -> bool:
    """Prompt user on Qt main GUI thread when stealth proxy / VPN connection fails.

    Offers two choices:
      1. 'Download Without Stealth' (Direct non-stealth download)
      2. 'Cancel Download'

    If user selects Direct:
      - Sets 'enable_stealth_proxy' to False in QSettings
      - Refreshes MainWindow system status visor
      - Returns True
    If user selects Cancel or closes dialog:
      - Leaves settings unchanged
      - Returns False
    """
    import threading
    from utils.main_thread import post_to_main_thread

    choice = ["cancel"]
    done_event = threading.Event()

    def _show_dialog():
        try:
            from PyQt6.QtWidgets import QApplication

            if QApplication.instance() is None:
                # Headless / test environment without Qt event loop: do not construct QWidget
                return

            from ui.dialogs.themed_message_box import ThemedMessageBox

            clean_err = (error_message or "").strip()
            if not clean_err:
                clean_err = "The stealth proxy endpoint is unavailable or disconnected."

            res = ThemedMessageBox.choices(
                None,
                "Stealth Connection Failed",
                f"Unable to connect through stealth proxy / VPN:\n\n{clean_err}\n\n"
                "Would you like to proceed with a direct (non-stealth) download, or cancel the download?",
                buttons=[
                    ("Download Without Stealth", "direct"),
                    ("Cancel Download", "cancel"),
                ],
                default="cancel",
                severity="warning",
            )
            if res == "direct":
                choice[0] = "direct"
        except Exception as e:
            logger.error(f"[Kuroko] Error showing stealth fallback prompt: {e}")
        finally:
            done_event.set()

    post_to_main_thread(_show_dialog)
    done_event.wait()

    if choice[0] == "direct":
        logger.warning("[Kuroko] User opted to bypass failed stealth proxy and proceed with direct download.")
        from utils.settings import get_settings

        settings = get_settings()
        settings.setValue("enable_stealth_proxy", False)

        def _refresh_visor():
            try:
                from PyQt6.QtWidgets import QApplication

                app = QApplication.instance()
                if app:
                    for widget in app.topLevelWidgets():
                        if hasattr(widget, "refresh_system_status"):
                            widget.refresh_system_status()
            except Exception as e:
                logger.debug(f"[Kuroko] Error refreshing visor after direct fallback: {e}")

        post_to_main_thread(_refresh_visor)
        return True

    return False


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

