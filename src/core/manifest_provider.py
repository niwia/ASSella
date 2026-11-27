"""Provider facade.

This is the seam the rest of Assella should talk to. It owns provider selection,
automatic fallback, canonical cache paths and capability aggregation; the
providers themselves only do their own acquisition.

Phase 1 keeps behavior identical: with no ``manifest_bundle_provider`` override
the order is Hubcap first (for bundles) and Vapor first (for single manifests),
matching today's ``core.morrenus_api`` behavior. The legacy module remains the
implementation for Hubcap and is still imported by existing call sites; it can
be flipped to a re-export shim once the router is exercised everywhere.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.providers import Capability, CapabilityNotSupported, ManifestProvider
from core.providers import registry

logger = logging.getLogger(__name__)

_BUNDLE_ORDER = ("hubcap", "ryuu")
_MANIFEST_ORDER = ("vapor", "hubcap", "ryuu")


def available_providers() -> List[ManifestProvider]:
    return registry.all_providers()


def get_provider(provider_id: str) -> Optional[ManifestProvider]:
    return registry.get(provider_id)


def providers_with(capability: Capability) -> List[ManifestProvider]:
    return registry.with_capability(capability)


def active_provider_id() -> str:
    """The configured provider id, or ``auto`` (fallback chain)."""
    try:
        from utils.settings import get_settings

        return str(get_settings().value("manifest_bundle_provider", "auto", type=str) or "auto").strip().lower()
    except Exception:
        return "auto"


def _ordered(capability: Capability, order: Tuple[str, ...]) -> List[ManifestProvider]:
    """Providers to try for ``capability``, explicit setting first."""
    if order is _BUNDLE_ORDER:
        order = _bundle_order()
    explicit = active_provider_id()
    ordered_ids: List[str] = []
    if explicit and explicit != "auto":
        ordered_ids.append(explicit)
    for pid in order:
        if pid not in ordered_ids:
            ordered_ids.append(pid)

    result: List[ManifestProvider] = []
    for pid in ordered_ids:
        provider = registry.get(pid)
        if provider is not None and provider.supports(capability):
            result.append(provider)
    return result


def _first(capability: Capability, order: Tuple[str, ...], call_name: str, *args, **kwargs):
    """Call ``call_name`` on the first capable provider, falling back on error.

    Returns the provider result; raises :class:`CapabilityNotSupported` when no
    provider can do it, and re-raises the last provider error otherwise.
    """
    candidates = _ordered(capability, order)
    if not candidates:
        raise CapabilityNotSupported(f"no provider supports {capability.value}")

    last_error: Optional[Exception] = None
    for provider in candidates:
        method = getattr(provider, call_name)
        try:
            result = method(*args, **kwargs)
        except CapabilityNotSupported as e:
            last_error = e
            logger.debug("[Providers] %s cannot do %s", provider.meta.id, capability.value)
            continue
        except Exception as e:
            logger.warning("[Providers] %s.%s failed: %s", provider.meta.id, call_name, e)
            last_error = e
            continue

        if _is_failure(result):
            logger.info("[Providers] %s.%s returned no result; trying the next provider", provider.meta.id, call_name)
            continue
        return result

    if last_error is not None:
        raise last_error
    return None


def _is_failure(result: Any) -> bool:
    """True when a provider result means 'try the next provider'."""
    if result is None:
        return True
    if isinstance(result, tuple):
        return not result or not result[0]
    if isinstance(result, dict):
        return "error" in result and not result.get("results")
    return False


# -- facade API (mirrors core.morrenus_api) -------------------------------


def get_selected_branch(app_id) -> str:
    """The branch the user picked for this app, else its installed branch."""
    try:
        from utils.settings import get_settings

        settings = get_settings()
        if settings:
            selected = (settings.value(f"selected_branch/{app_id}", "", type=str) or "").strip()
            if selected:
                return selected
            installed = (settings.value(f"installed_branch/{app_id}", "", type=str) or "").strip()
            if installed:
                return installed
    except Exception as e:
        logger.debug("Could not read selected branch for %s: %s", app_id, e)
    return "public"


def get_manifest_zip_path(app_id, branch: str = "public") -> Path:
    """Canonical cache path for an app/branch bundle (provider-independent)."""
    from utils.helpers import get_base_path

    manifests_dir = Path(get_base_path()) / "hubcap_manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    if branch and branch != "public":
        return manifests_dir / f"accela_fetch_{app_id}_branch_{branch}.zip"
    return manifests_dir / f"accela_fetch_{app_id}.zip"


def _ryuu_configured() -> bool:
    try:
        from utils.settings import get_settings

        return bool(str(get_settings().value("ryuu_auth_key", "", type=str) or "").strip())
    except Exception:
        return False


def _bundle_order() -> Tuple[str, ...]:
    """Bundle provider order. Prefer Ryuu only when a key is configured."""
    if _ryuu_configured():
        return ("ryuu", "hubcap")
    return _BUNDLE_ORDER


def download_manifest(app_id, branch: str = "public", force_update: bool = True) -> Tuple[Optional[str], Optional[str]]:
    branch = branch or "public"
    candidates = _ordered(Capability.BUNDLE_DOWNLOAD, _BUNDLE_ORDER)
    if not candidates:
        return None, "No manifest provider is configured."

    last_error: Optional[str] = None
    for provider in candidates:
        try:
            path, err = provider.download_bundle(app_id, branch=branch, force_update=force_update)
        except CapabilityNotSupported:
            logger.debug("[Providers] %s cannot download bundles", provider.meta.id)
            continue
        except Exception as e:
            logger.warning("[Providers] %s bundle download failed: %s", provider.meta.id, e)
            last_error = str(e)
            continue

        if not path or err:
            last_error = err or last_error
            continue

        target = get_manifest_zip_path(app_id, branch)
        if Path(path) != target:
            try:
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(target))
            except OSError as e:
                return None, f"Could not store manifest bundle: {e}"
        return str(target), None

    return None, last_error or "Manifest download failed."


def generate_single_manifest(
    depot_id,
    manifest_id,
    depot_key: Optional[Any] = None,
    force_hubcap: bool = False,
) -> Tuple[Optional[bytes], Optional[str]]:
    order = _MANIFEST_ORDER[1:] if force_hubcap else _MANIFEST_ORDER
    try:
        result = _first(
            Capability.SINGLE_MANIFEST,
            order,
            "generate_manifest",
            depot_id,
            manifest_id,
            depot_key=depot_key,
        )
    except CapabilityNotSupported:
        return None, "No provider can generate a single manifest."
    except Exception as e:
        return None, str(e)
    return result if result is not None else (None, "Manifest generation failed.")


def _contents_from_cache(app_id, branch: str = "public") -> Dict[str, Any]:
    """Derive bundle contents from the cached ZIP (for providers without /contents)."""
    import zipfile

    path = get_manifest_zip_path(app_id, branch)
    if not path.is_file():
        return {}
    try:
        with zipfile.ZipFile(path) as zf:
            names = [os.path.basename(n) for n in zf.namelist() if n.endswith(".manifest")]
    except (OSError, zipfile.BadZipFile):
        return {}

    manifests = []
    for name in names:
        parts = name[: -len(".manifest")].split("_")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            manifests.append({"depot_id": parts[0], "manifest_id": parts[1]})
    return {
        "zip_exists": True,
        "manifest_count": len(manifests),
        "manifests": manifests,
        "depot_ids": {m["depot_id"] for m in manifests},
        "manifest_map": {m["depot_id"]: m["manifest_id"] for m in manifests},
    }


def get_manifest_contents(app_id, branch: str = "public") -> Dict[str, Any]:
    try:
        result = _first(Capability.CONTENTS, _BUNDLE_ORDER, "get_contents", app_id, branch=branch) or {}
    except CapabilityNotSupported:
        result = {}
    except Exception as e:
        result = {"error": str(e)}

    if not result or "error" in result:
        derived = _contents_from_cache(app_id, branch)
        if derived:
            return derived
    return result


def fetch_depot_keys_index(etag: Optional[str] = None) -> Dict[str, Any]:
    try:
        return _first(Capability.DEPOT_KEY_INDEX, _BUNDLE_ORDER, "get_depot_key_index", etag=etag) or {}
    except CapabilityNotSupported:
        return {"ok": False, "error": "No provider exposes a depot-key index."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_hubcap_depot_keys(force: bool = False, allow_network: bool = False) -> Dict[str, Any]:
    try:
        return _first(
            Capability.DEPOT_KEY_INDEX,
            _BUNDLE_ORDER,
            "get_hubcap_depot_keys",
            force=force,
            allow_network=allow_network,
        ) or {}
    except CapabilityNotSupported:
        return {"error": "No provider exposes a depot-key index."}
    except Exception as e:
        return {"error": str(e)}


def get_manifest_status(app_id) -> Dict[str, Any]:
    try:
        return _first(Capability.CONTENTS, _BUNDLE_ORDER, "get_manifest_status", app_id) or {}
    except CapabilityNotSupported:
        return {"status": "unknown"}
    except Exception:
        return {"status": "unknown"}


def _search_steam_store(query: str, limit: int = 100) -> Dict[str, Any]:
    """Search fallback when no provider exposes a search endpoint (e.g. Hubcap off)."""
    try:
        import requests

        resp = requests.get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": query, "l": "en", "cc": "us"},
            timeout=8,
        )
        items = (resp.json() or {}).get("items") or []
    except Exception as e:
        return {"error": f"Search failed: {e}"}

    results = [
        {
            "game_id": str(item.get("id")),
            "game_name": item.get("name") or "Unknown",
            "manifest_available": True,
        }
        for item in items
        if item.get("id")
    ]
    return {"results": results[:limit], "total_count": len(results)}


def search_games(query: str, limit: int = 100) -> Dict[str, Any]:
    try:
        result = _first(Capability.SEARCH, _BUNDLE_ORDER, "search", query, limit=limit)
        if result and "error" not in result:
            return result
    except CapabilityNotSupported:
        logger.debug("[Providers] No provider exposes search; using the Steam Store fallback.")
    except Exception as e:
        logger.debug("[Providers] Provider search failed, falling back to Steam: %s", e)
    return _search_steam_store(query, limit)


def get_user_stats(force: bool = False) -> Dict[str, Any]:
    try:
        return _first(Capability.ACCOUNT_STATS, _BUNDLE_ORDER, "get_user_stats", force=force) or {}
    except CapabilityNotSupported:
        return {}
    except Exception as e:
        return {"error": str(e)}


def get_generate_usage(force: bool = False) -> Dict[str, Any]:
    try:
        return _first(Capability.ACCOUNT_STATS, _BUNDLE_ORDER, "get_generate_usage", force=force) or {}
    except CapabilityNotSupported:
        return {}
    except Exception as e:
        return {"error": str(e)}


def get_all_hubcap_stats(force: bool = False) -> Dict[str, Any]:
    try:
        return _first(Capability.ACCOUNT_STATS, _BUNDLE_ORDER, "get_account_stats") or {}
    except CapabilityNotSupported:
        return {}
    except Exception as e:
        return {"error": str(e)}


def check_health(force: bool = False) -> Dict[str, Any]:
    try:
        return _first(Capability.HEALTH, _BUNDLE_ORDER, "check_health", force=force) or {}
    except CapabilityNotSupported:
        return {"status": "unknown"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def mark_hubcap_manifest_used() -> None:
    """Compatibility passthrough (the flag is Hubcap-specific)."""
    from core.providers import hubcap_impl

    hubcap_impl.mark_hubcap_manifest_used()


def pop_hubcap_manifest_used() -> bool:
    """Compatibility passthrough (the flag is Hubcap-specific)."""
    from core.providers import hubcap_impl

    return hubcap_impl.pop_hubcap_manifest_used()


__all__ = [
    "available_providers",
    "get_provider",
    "providers_with",
    "active_provider_id",
    "get_selected_branch",
    "get_manifest_zip_path",
    "download_manifest",
    "generate_single_manifest",
    "get_manifest_contents",
    "fetch_depot_keys_index",
    "get_hubcap_depot_keys",
    "get_manifest_status",
    "search_games",
    "get_user_stats",
    "get_generate_usage",
    "get_all_hubcap_stats",
    "check_health",
    "mark_hubcap_manifest_used",
    "pop_hubcap_manifest_used",
]
