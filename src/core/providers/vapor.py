"""Local Vapor (at0-m) provider adapter.

The at0-m engine retrieves a single depot manifest for free by racing Manifest
Request Code providers and downloading directly from the Steam CDN. It is the
preferred path for per-depot generation; remote providers are only the fallback.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from core.providers.base import Capability, ManifestProvider, ProviderMeta


class VaporProvider(ManifestProvider):
    meta = ProviderMeta(
        id="vapor",
        label="Local Vapor (at0-m)",
        capabilities=frozenset({Capability.SINGLE_MANIFEST}),
    )

    def generate_manifest(
        self, depot_id: Any, manifest_id: Any, depot_key: Optional[Any] = None
    ) -> Tuple[Optional[bytes], Optional[str]]:
        from core import at0m

        return at0m.generate_single_manifest(
            depot_id, manifest_id, depot_key=depot_key, force_fallback=False
        )
