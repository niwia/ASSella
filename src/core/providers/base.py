"""
Provider-agnostic contract for manifest and depot-key acquisition backends.

A provider declares what it can do through :class:`Capability` flags. Anything a
provider cannot do raises :class:`CapabilityNotSupported`, so callers can fall
back to another provider instead of treating the gap as a hard failure.

This module deliberately has no imports from ``core.morrenus_api`` or any other
acquisition code: it is the bottom of the dependency graph.
"""

from __future__ import annotations

import logging
import os
import zipfile
from abc import ABC
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional, Tuple

logger = logging.getLogger(__name__)


class Capability(str, Enum):
    """One thing a provider may be able to do."""

    BUNDLE_DOWNLOAD = "bundle_download"
    SINGLE_MANIFEST = "single_manifest"
    CONTENTS = "contents"
    DEPOT_KEY_INDEX = "depot_key_index"
    SEARCH = "search"
    ACCOUNT_STATS = "account_stats"
    HEALTH = "health"
    NATIVE_BRANCH = "native_branch"
    REQUEST_MISSING = "request_missing"


class ProviderError(Exception):
    """Base error for provider operations."""


class CapabilityNotSupported(ProviderError):
    """Raised when a provider is asked for something it cannot do."""


class ProviderAuthError(ProviderError):
    """Raised when a provider rejects or is missing its credentials."""


@dataclass(frozen=True)
class ProviderMeta:
    """Static description of a provider."""

    id: str
    label: str
    capabilities: FrozenSet[Capability] = frozenset()


class ManifestProvider(ABC):
    """Interface every acquisition backend implements.

    Defaults raise :class:`CapabilityNotSupported`; a provider only overrides the
    methods it advertises in ``meta.capabilities``. Providers must never call
    back into the orchestration facade (that would recurse); they do their own
    work or talk to their own transport.
    """

    meta: ProviderMeta
    #: (max_requests, per_seconds) ceiling, or None when unlimited/unknown.
    rate_limit: Optional[Tuple[int, float]] = None
    #: Domains this provider needs ISP-bypass routing for.
    bypass_domains: Tuple[str, ...] = ()

    def supports(self, capability: Capability) -> bool:
        return capability in self.meta.capabilities

    def _unsupported(self, capability: Capability) -> CapabilityNotSupported:
        return CapabilityNotSupported(
            f"provider '{self.meta.id}' does not support {capability.value}"
        )

    def download_bundle(
        self,
        app_id: Any,
        branch: str = "public",
        dest_path: Optional[Path] = None,
        force_update: bool = True,
    ) -> Tuple[Optional[str], Optional[str]]:
        raise self._unsupported(Capability.BUNDLE_DOWNLOAD)

    def generate_manifest(
        self,
        depot_id: Any,
        manifest_id: Any,
        depot_key: Optional[Any] = None,
    ) -> Tuple[Optional[bytes], Optional[str]]:
        raise self._unsupported(Capability.SINGLE_MANIFEST)

    def get_contents(self, app_id: Any, branch: str = "public") -> Dict[str, Any]:
        raise self._unsupported(Capability.CONTENTS)

    def get_depot_key_index(self, etag: Optional[str] = None) -> Dict[str, Any]:
        raise self._unsupported(Capability.DEPOT_KEY_INDEX)

    def get_hubcap_depot_keys(self, force: bool = False, allow_network: bool = False) -> Dict[str, Any]:
        raise self._unsupported(Capability.DEPOT_KEY_INDEX)

    def search(self, query: str, limit: int = 100) -> Dict[str, Any]:
        raise self._unsupported(Capability.SEARCH)

    def get_account_stats(self) -> Dict[str, Any]:
        raise self._unsupported(Capability.ACCOUNT_STATS)

    def get_user_stats(self, force: bool = False) -> Dict[str, Any]:
        raise self._unsupported(Capability.ACCOUNT_STATS)

    def get_generate_usage(self, force: bool = False) -> Dict[str, Any]:
        raise self._unsupported(Capability.ACCOUNT_STATS)

    def get_manifest_status(self, app_id: Any) -> Dict[str, Any]:
        raise self._unsupported(Capability.CONTENTS)

    def check_health(self, force: bool = False) -> Dict[str, Any]:
        raise self._unsupported(Capability.HEALTH)

    def request_refresh(self, app_id: Any, branch: str = "public") -> Dict[str, Any]:
        raise self._unsupported(Capability.REQUEST_MISSING)

    def request_game(self, app_id: Any) -> Dict[str, Any]:
        raise self._unsupported(Capability.REQUEST_MISSING)

    def request_branch(self, app_id: Any, branch: str) -> Dict[str, Any]:
        raise self._unsupported(Capability.REQUEST_MISSING)


def normalize_bundle(path: Path, app_id: Any = "", branch: str = "public") -> Tuple[Path, Optional[str]]:
    """Verify a downloaded bundle and flatten nested members.

    Downstream consumers (``ProcessZipTask``) expect a flat archive holding the
    ``.lua`` and ``.manifest`` files at the root. A provider that hands back a
    nested layout (a folder per app, which is common) is repacked in place so
    the rest of the pipeline keeps working unchanged.
    """
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return p, "bundle is missing or empty"

    try:
        with zipfile.ZipFile(p, "r") as zf:
            names = [n for n in zf.namelist() if n and not n.endswith("/")]
    except (zipfile.BadZipFile, OSError) as e:
        return p, f"bundle is not a valid zip: {e}"

    if not names:
        return p, "bundle contains no files"

    lowered = [n.lower() for n in names]
    if not any(n.endswith((".lua", ".manifest")) for n in lowered):
        return p, "bundle contains no .lua or .manifest entries"

    if not any("/" in n or "\\" in n for n in names):
        return p, None

    tmp = p.with_name(f"{p.name}.flat")
    try:
        with zipfile.ZipFile(p, "r") as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            seen = set()
            for name in names:
                base = os.path.basename(name.replace("\\", "/"))
                if not base or base in seen:
                    continue
                seen.add(base)
                zout.writestr(base, zin.read(name))
        os.replace(tmp, p)
        logger.info("[Providers] Flattened nested bundle %s (%s -> %s files)", p.name, len(names), len(seen))
    except (OSError, zipfile.BadZipFile) as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup_err:
            logger.debug("[Providers] could not remove temporary bundle %s: %s", tmp, cleanup_err)
        return p, f"could not flatten bundle: {e}"

    return p, None
