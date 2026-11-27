"""Curve25519 keypair generation, Cloudflare WARP registration, and WireGuard config assembly."""

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

# Cloudflare consumer registration endpoint used by wgcf / WARP clients
CF_WARP_REG_URL = "https://api.cloudflareclient.com/v0a2158/reg"


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


_DEFAULT_KEYGEN = _generate_x25519_keypair


def generate_warp_config(timeout: float = 10.0) -> str:
    """
    Register a free client-side Cloudflare WARP account via Cloudflare Client API
    and return a WireGuard .conf content string with private key and assigned IP.
    """
    keygen_fn = _generate_x25519_keypair
    pkg = sys.modules.get("utils.kuroko")
    if keygen_fn is _DEFAULT_KEYGEN and pkg is not None:
        pkg_fn = getattr(pkg, "_generate_x25519_keypair", _DEFAULT_KEYGEN)
        if pkg_fn is not _DEFAULT_KEYGEN:
            keygen_fn = pkg_fn

    private_key, public_key = keygen_fn()
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

    reg_url = CF_WARP_REG_URL
    if pkg is not None:
        reg_url = getattr(pkg, "CF_WARP_REG_URL", CF_WARP_REG_URL)

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        reg_url,
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


_DEFAULT_GENERATE_WARP = generate_warp_config


def get_cached_warp_config_path() -> Path:
    """Return the persistent path where a generated WARP profile is stored."""
    cfg_dir = Path.home() / ".config" / "ASSella"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir / "warp_profile.conf"


_DEFAULT_GET_CACHED_PATH = get_cached_warp_config_path


def get_or_create_warp_config(force_refresh: bool = False, timeout: float = 10.0) -> str:
    """
    Retrieve cached Cloudflare WARP config or generate a fresh one if missing or requested.
    Saves with 0o600 permissions so private keys are never world-readable.
    """
    pkg = sys.modules.get("utils.kuroko")
    path_fn = get_cached_warp_config_path
    if path_fn is _DEFAULT_GET_CACHED_PATH and pkg is not None:
        pkg_path_fn = getattr(pkg, "get_cached_warp_config_path", _DEFAULT_GET_CACHED_PATH)
        if pkg_path_fn is not _DEFAULT_GET_CACHED_PATH:
            path_fn = pkg_path_fn

    conf_path = path_fn()
    if not force_refresh and conf_path.is_file():
        try:
            content = conf_path.read_text(encoding="utf-8")
            if "[Interface]" in content and "[Peer]" in content:
                return content
        except Exception as e:
            logger.debug(f"[Kuroko] Failed to read cached WARP config: {e}")

    gen_fn = generate_warp_config
    if gen_fn is _DEFAULT_GENERATE_WARP and pkg is not None:
        pkg_gen_fn = getattr(pkg, "generate_warp_config", _DEFAULT_GENERATE_WARP)
        if pkg_gen_fn is not _DEFAULT_GENERATE_WARP:
            gen_fn = pkg_gen_fn

    new_config = gen_fn(timeout=timeout)
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


__all__ = [
    "CF_WARP_REG_URL",
    "_generate_x25519_keypair",
    "assemble_kuroko_config",
    "assemble_wireproxy_config",
    "generate_warp_config",
    "get_cached_warp_config_path",
    "get_or_create_warp_config",
]