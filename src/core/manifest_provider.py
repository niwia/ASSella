"""Provider facade.

The seam the rest of Assella talks to. It owns provider selection, capability
aggregation and canonical cache paths; the providers themselves only do their
own acquisition.

Selection is strict: an explicitly chosen provider serves every bundle and its
errors are reported to the user instead of being answered by a different
provider. The fallback chain in ``_ordered``/``_first`` is used for read-only
aggregations (contents, health, account stats, search, depot-key index) and for
the ``auto`` value, which no UI writes.

``core.morrenus_api`` is a compatibility shim over this module.
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

_BUNDLE_ORDER = ("hubcap", "ryuu", "luatools")
_MANIFEST_ORDER = ("vapor", "hubcap", "ryuu", "luatools")


def available_providers() -> List[ManifestProvider]:
    return registry.all_providers()


def get_provider(provider_id: str) -> Optional[ManifestProvider]:
    return registry.get(provider_id)


def providers_with(capability: Capability) -> List[ManifestProvider]:
    return registry.with_capability(capability)


def active_provider_id() -> str:
    """The configured provider id (defaults to ``hubcap``)."""
    try:
        from utils.settings import get_settings

        return str(get_settings().value("manifest_bundle_provider", "hubcap", type=str) or "hubcap").strip().lower()
    except Exception:
        return "hubcap"


_PROVIDER_SHORT_LABELS = {
    "hubcap": "Hubcap",
    "ryuu": "Ryu",
    "vapor": "Vapor",
    "luatools": "LuaTools",
}

_PROVIDER_FULL_LABELS = {
    "hubcap": "Hubcap",
    "ryuu": "Ryu (beta)",
    "vapor": "Vapor",
    "luatools": "LuaTools (Luie)",
}


def get_provider_label(provider_id: str, full: bool = False) -> str:
    """Human-friendly label for ``provider_id`` (``auto`` resolves to its first pick)."""
    pid = (provider_id or "hubcap").strip().lower()
    if pid == "auto":
        order = _bundle_order()
        pid = order[0] if order else "hubcap"

    table = _PROVIDER_FULL_LABELS if full else _PROVIDER_SHORT_LABELS
    if pid in table:
        return table[pid]

    provider = get_provider(pid)
    if provider is not None and getattr(provider, "meta", None):
        return provider.meta.label
    return pid.title()


def get_active_provider_label(full: bool = False) -> str:
    """Returns the human-friendly display label of the active manifest provider."""
    return get_provider_label(active_provider_id(), full=full)


def active_provider_supports(capability: Capability) -> bool:
    """Check if the currently active provider supports a given capability."""
    pid = active_provider_id()
    if pid == "auto":
        order = _bundle_order()
        pid = order[0] if order else "hubcap"
    provider = get_provider(pid)
    if provider is not None:
        return provider.supports(capability)
    return False


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
    explicit = active_provider_id()
    if explicit and explicit != "auto":
        provider = registry.get(explicit)
        if provider is None or not provider.supports(Capability.BUNDLE_DOWNLOAD):
            return None, f"Manifest provider '{explicit}' is not available."
        candidates = [provider]
    else:
        candidates = _ordered(Capability.BUNDLE_DOWNLOAD, _BUNDLE_ORDER)

    if not candidates:
        return None, "No manifest provider is configured."

    target = get_manifest_zip_path(app_id, branch)
    last_error: Optional[str] = None
    for provider in candidates:
        try:
            path, err = provider.download_bundle(
                app_id, branch=branch, dest_path=target, force_update=force_update
            )
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


def _search_steam_community(query: str, limit: int = 100) -> Dict[str, Any]:
    """Search Steam Community SearchApps endpoint for instant catalog search."""
    import urllib.parse
    import requests

    try:
        encoded = urllib.parse.quote(query.strip())
        url = f"https://steamcommunity.com/actions/SearchApps/{encoded}"
        resp = requests.get(url, timeout=8, headers={"User-Agent": "ASSella/3.0"})
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                results = [
                    {
                        "game_id": str(item.get("appid")),
                        "game_name": item.get("name") or "Unknown",
                        "header_image": item.get("logo") or item.get("icon") or "",
                        "manifest_available": True,
                    }
                    for item in data
                    if item.get("appid")
                ]
                return {"results": results[:limit], "total_count": len(results)}
    except Exception as e:
        logger.debug(f"[SteamSearch] Community search failed: {e}")
    return _search_steam_store(query, limit)


_ryuu_catalog_cache: Dict[str, Any] = {"mtime": 0.0, "entries": None}


def _search_ryuu_local_catalog(query: str, limit: int = 100) -> Optional[Dict[str, Any]]:
    """Search the optional user-downloaded ryuu_games.json if present on disk."""
    try:
        from utils.helpers import get_base_path
        import json

        db_path = Path(get_base_path()) / "ryuu_games.json"
        if not db_path.is_file():
            return None

        mtime = db_path.stat().st_mtime
        if _ryuu_catalog_cache["entries"] is None or _ryuu_catalog_cache["mtime"] != mtime:
            raw = json.loads(db_path.read_text(encoding="utf-8", errors="ignore"))
            entries = []
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict):
                        aid = str(item.get("appid") or item.get("id") or "").strip()
                        name = str(item.get("name") or item.get("title") or "").strip()
                        if aid and name:
                            entries.append((aid, name, name.lower()))
            elif isinstance(raw, dict):
                for aid, val in raw.items():
                    if isinstance(val, dict):
                        name = str(val.get("name") or val.get("title") or "").strip()
                    else:
                        name = str(val or "").strip()
                    if aid and name:
                        entries.append((str(aid), name, name.lower()))
            _ryuu_catalog_cache["entries"] = entries
            _ryuu_catalog_cache["mtime"] = mtime

        q_lower = query.strip().lower()
        if not q_lower:
            return None

        matches = []
        for aid, name, name_lower in _ryuu_catalog_cache["entries"]:
            if q_lower in name_lower or q_lower == aid:
                matches.append({
                    "game_id": aid,
                    "game_name": name,
                    "header_image": f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{aid}/header.jpg",
                    "manifest_available": True,
                })
                if len(matches) >= limit:
                    break
        return {"results": matches, "total_count": len(matches)}
    except Exception as e:
        logger.debug(f"[RyuuCatalog] Offline catalog search error: {e}")
        return None


def search_games(query: str, limit: int = 100) -> Dict[str, Any]:
    active = active_provider_id()
    if active == "hubcap":
        try:
            result = _first(Capability.SEARCH, _BUNDLE_ORDER, "search", query, limit=limit)
            if result and "error" not in result:
                return result
        except Exception as e:
            logger.debug("[Providers] Hubcap search failed: %s", e)
    elif active == "ryuu":
        local_res = _search_ryuu_local_catalog(query, limit=limit)
        if local_res is not None and local_res.get("results"):
            return local_res
    return _search_steam_community(query, limit)


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
        return _first(Capability.ACCOUNT_STATS, _BUNDLE_ORDER, "get_account_stats", force=force) or {}
    except CapabilityNotSupported:
        return {}
    except Exception as e:
        return {"error": str(e)}


_HEALTH_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_HEALTH_OK_TTL = 300.0
_HEALTH_ERR_TTL = 60.0


def get_cached_health(provider_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return cached health dict for provider_id if still within TTL, else None."""
    import time
    pid = (provider_id or active_provider_id() or "hubcap").strip().lower()
    entry = _HEALTH_CACHE.get(pid)
    if not entry:
        return None
    ts, payload = entry
    ttl = _HEALTH_OK_TTL if payload.get("status") == "healthy" else _HEALTH_ERR_TTL
    if (time.time() - ts) < ttl:
        return dict(payload)
    return None


def check_health(force: bool = False) -> Dict[str, Any]:
    """Health of the provider the user actually selected.

    Deliberately not routed through ``_first``: caching a fallback provider's
    answer under the active id would tell the UI that e.g. LuaTools is online
    when Hubcap is the one that responded.
    """
    import time
    pid = active_provider_id() or "hubcap"
    if not force:
        cached = get_cached_health(pid)
        if cached is not None:
            return cached
    res: Dict[str, Any]
    try:
        provider = registry.get(pid)
        if provider is None or not provider.supports(Capability.HEALTH):
            res = {"status": "unknown"}
        else:
            res = provider.check_health(force=force) or {"status": "unknown"}
    except CapabilityNotSupported:
        res = {"status": "unknown"}
    except Exception as e:
        res = {"status": "unhealthy", "error": str(e)}
    _HEALTH_CACHE[pid] = (time.time(), dict(res))
    return res


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
    "get_provider_label",
    "get_active_provider_label",
    "active_provider_supports",
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
    "get_cached_health",
    "mark_hubcap_manifest_used",
    "pop_hubcap_manifest_used",
]
