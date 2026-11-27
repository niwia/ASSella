"""Hubcap provider adapter.

Phase 1 keeps the battle-tested implementation in ``core.morrenus_api`` and
exposes it through the provider interface. The adapter imports the legacy module
lazily inside each method, so importing this module never creates a cycle while
the legacy module still owns the orchestration helpers.

``generate_manifest`` forces the remote path (``force_hubcap=True``): the facade
already tries the local Vapor engine first, and letting the legacy function
re-try it here would duplicate work.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.providers.base import Capability, ManifestProvider, ProviderMeta


class HubcapProvider(ManifestProvider):
    meta = ProviderMeta(
        id="hubcap",
        label="Hubcap Manifest",
        capabilities=frozenset({
            Capability.BUNDLE_DOWNLOAD,
            Capability.SINGLE_MANIFEST,
            Capability.CONTENTS,
            Capability.DEPOT_KEY_INDEX,
            Capability.SEARCH,
            Capability.ACCOUNT_STATS,
            Capability.HEALTH,
        }),
    )
    rate_limit = (3, 3.05)
    bypass_domains = ("hubcapmanifest.com",)

    @staticmethod
    def _impl():
        from core.providers import hubcap_impl

        return hubcap_impl

    def download_bundle(self, app_id, branch="public", dest_path=None, force_update=True):
        return self._impl().download_manifest(app_id, branch=branch, force_update=force_update)

    def generate_manifest(self, depot_id, manifest_id, depot_key=None):
        return self._impl().generate_single_manifest(
            depot_id, manifest_id, depot_key=depot_key, force_hubcap=True
        )

    def get_contents(self, app_id, branch="public"):
        return self._impl().get_manifest_contents(app_id, branch=branch)

    def get_depot_key_index(self, etag: Optional[str] = None):
        return self._impl().fetch_depot_keys_index(etag=etag)

    def get_hubcap_depot_keys(self, force: bool = False, allow_network: bool = False):
        return self._impl().get_hubcap_depot_keys(force=force, allow_network=allow_network)

    def search(self, query: str, limit: int = 100):
        return self._impl().search_games(query, limit=limit)

    def get_account_stats(self):
        return self._impl().get_all_hubcap_stats()

    def get_user_stats(self, force: bool = False):
        return self._impl().get_user_stats(force=force)

    def get_generate_usage(self, force: bool = False):
        return self._impl().get_generate_usage(force=force)

    def get_manifest_status(self, app_id: Any) -> Dict[str, Any]:
        return self._impl().get_manifest_status(app_id)

    def check_health(self, force: bool = False):
        return self._impl().check_health(force=force)
