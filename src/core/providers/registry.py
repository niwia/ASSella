"""Provider registry.

Providers register themselves once; the rest of the app looks them up by id or
by capability. Selection order is deliberately *not* decided here — the facade
owns routing and fallback.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from core.providers.base import Capability, ManifestProvider

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_providers: Dict[str, ManifestProvider] = {}


def register(provider: ManifestProvider) -> ManifestProvider:
    """Register (or replace) a provider by its id."""
    with _lock:
        _providers[provider.meta.id] = provider
    return provider


def get(provider_id: str) -> Optional[ManifestProvider]:
    with _lock:
        return _providers.get(str(provider_id))


def all_providers() -> List[ManifestProvider]:
    with _lock:
        return list(_providers.values())


def with_capability(capability: Capability) -> List[ManifestProvider]:
    return [p for p in all_providers() if p.supports(capability)]


def ids() -> List[str]:
    with _lock:
        return list(_providers.keys())
