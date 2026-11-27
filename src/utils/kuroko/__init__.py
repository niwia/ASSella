"""Userspace WireGuard-to-SOCKS5 proxy engine (Kuroko) and Cloudflare WARP client.

Enables rootless, leak-free, userspace proxying for DepotDownloaderMod depot downloads
on Linux and SteamOS without modifying system network routing or requiring root / sudo.
Named after Kuroko — the invisible, stagehand shadow in traditional Japanese theatre.
"""

import os  # noqa: F401
import platform  # noqa: F401
import re  # noqa: F401
import shutil  # noqa: F401
import socket  # noqa: F401
import subprocess  # noqa: F401
import tarfile  # noqa: F401
import tempfile  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
import urllib.error  # noqa: F401
import urllib.request  # noqa: F401

from .binary import (
    WIREPROXY_BASE_URL,
    WIREPROXY_RELEASE_VERSION,
    ensure_kuroko_binary,
    ensure_wireproxy_binary,
    find_free_port,
    get_kuroko_binary_path,
)
from .config import (
    CF_WARP_REG_URL,
    _generate_x25519_keypair,
    assemble_kuroko_config,
    assemble_wireproxy_config,
    generate_warp_config,
    get_cached_warp_config_path,
    get_or_create_warp_config,
)
from .pool import (
    NETWORK_ERROR_SIGNATURES,
    StealthChoice,
    StealthPoolManager,
    is_network_or_proxy_error,
    prompt_stealth_fallback,
    resolve_stealth_proxy_url,
    get_stealth_fallback_info,
    get_stealth_warmup_state,
    stealth_http_request,
    warmup_stealth_proxy_async,
)
from .probe import (
    _parse_cf_trace,
    measure_speed,
    probe_alive,
    probe_stealth_ready,
    test_and_benchmark_endpoint,
    test_connection,
    test_stealth_proxy_connection,
)
from .profiles import (
    WireguardProfileManager,
    validate_wireguard_config,
)
from .scoring import (
    COOLDOWN_DURATION,
    _endpoint_cooldowns,
    _safe_wg_profiles,
    clear_endpoint_cooldowns,
    get_endpoint_scores,
    is_endpoint_in_cooldown,
    mark_endpoint_cooldown,
    safe_settings_string_list,
    save_endpoint_score,
)
from .tunnel import (
    Kuroko,
    WireproxyManager,
    _global_kuroko_instance,
    _kuroko_lock,
    get_kuroko,
    get_wireproxy_manager,
)

__all__ = [
    "CF_WARP_REG_URL",
    "COOLDOWN_DURATION",
    "Kuroko",
    "NETWORK_ERROR_SIGNATURES",
    "StealthChoice",
    "StealthPoolManager",
    "WIREPROXY_BASE_URL",
    "WIREPROXY_RELEASE_VERSION",
    "WireguardProfileManager",
    "WireproxyManager",
    "_endpoint_cooldowns",
    "_generate_x25519_keypair",
    "_global_kuroko_instance",
    "_kuroko_lock",
    "_parse_cf_trace",
    "_safe_wg_profiles",
    "assemble_kuroko_config",
    "assemble_wireproxy_config",
    "clear_endpoint_cooldowns",
    "ensure_kuroko_binary",
    "ensure_wireproxy_binary",
    "find_free_port",
    "generate_warp_config",
    "get_cached_warp_config_path",
    "get_endpoint_scores",
    "get_kuroko",
    "get_stealth_fallback_info",
    "get_stealth_warmup_state",
    "get_kuroko_binary_path",
    "get_or_create_warp_config",
    "get_wireproxy_manager",
    "is_endpoint_in_cooldown",
    "is_network_or_proxy_error",
    "mark_endpoint_cooldown",
    "measure_speed",
    "probe_alive",
    "probe_stealth_ready",
    "prompt_stealth_fallback",
    "resolve_stealth_proxy_url",
    "safe_settings_string_list",
    "save_endpoint_score",
    "stealth_http_request",
    "test_and_benchmark_endpoint",
    "test_connection",
    "test_stealth_proxy_connection",
    "validate_wireguard_config",
    "warmup_stealth_proxy_async",
]