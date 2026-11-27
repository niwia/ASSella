"""Manifest/key provider backends.

Importing this package registers the built-in providers exactly once. New
providers only need to subclass :class:`~core.providers.base.ManifestProvider`,
declare their capabilities, and be added to ``register_builtins``.
"""

from __future__ import annotations

from core.providers.base import (
    Capability,
    CapabilityNotSupported,
    ManifestProvider,
    ProviderAuthError,
    ProviderError,
    ProviderMeta,
    normalize_bundle,
)
from core.providers import registry

_REGISTERED = False


def register_builtins() -> None:
    """Register the providers that ship with Assella (idempotent)."""
    global _REGISTERED
    if _REGISTERED:
        return

    from core.providers.hubcap import HubcapProvider
    from core.providers.ryuu import RyuuProvider
    from core.providers.vapor import VaporProvider

    for provider in (HubcapProvider(), VaporProvider(), RyuuProvider()):
        registry.register(provider)
    _REGISTERED = True


register_builtins()

__all__ = [
    "Capability",
    "CapabilityNotSupported",
    "ManifestProvider",
    "ProviderAuthError",
    "ProviderError",
    "ProviderMeta",
    "normalize_bundle",
    "registry",
    "register_builtins",
]
