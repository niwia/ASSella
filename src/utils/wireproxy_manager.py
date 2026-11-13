"""Backward-compatibility shim for utils.wireproxy_manager.

Canonical implementation has moved to utils.kuroko.
All functions and classes are re-exported here for backwards compatibility.
"""

from utils.kuroko import *  # noqa: F401, F403
from utils.kuroko import (
    Kuroko,
    WireproxyManager,
    _generate_x25519_keypair,
    assemble_kuroko_config,
    assemble_wireproxy_config,
    ensure_kuroko_binary,
    ensure_wireproxy_binary,
    find_free_port,
    generate_warp_config,
    get_kuroko,
    get_or_create_warp_config,
    get_wireproxy_manager,
    resolve_stealth_proxy_url,
    test_stealth_proxy_connection,
)

__all__ = [
    "Kuroko",
    "WireproxyManager",
    "_generate_x25519_keypair",
    "assemble_kuroko_config",
    "assemble_wireproxy_config",
    "ensure_kuroko_binary",
    "ensure_wireproxy_binary",
    "find_free_port",
    "generate_warp_config",
    "get_kuroko",
    "get_or_create_warp_config",
    "get_wireproxy_manager",
    "resolve_stealth_proxy_url",
    "test_stealth_proxy_connection",
]
