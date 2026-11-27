# Manifest & key providers

Assella fetches Steam manifests and depot keys through a pluggable backend. A
provider declares what it can do with `Capability` flags; the facade picks a
provider and falls back automatically when one is unavailable or lacks an
endpoint.

`core.morrenus_api` is now a **compatibility shim** that re-exports
`core.manifest_provider`; every existing call site keeps working unchanged. New
code should import `core.manifest_provider`.

## Layers

- `core/providers/base.py` — `Capability`, `ManifestProvider`, typed errors,
  `normalize_bundle()` (flattens nested ZIPs into the canonical layout).
- `core/providers/registry.py` — provider registration and capability lookup.
- `core/providers/hubcap_impl.py` — Hubcap HTTP client (the old
  `core.morrenus_api` internals).
- `core/providers/hubcap.py` — Hubcap provider adapter.
- `core/providers/vapor.py` — local at0-m engine (free single manifest by GID).
- `core/providers/ryuu.py` — Ryuu's Manifests.
- `core/providers/luatools.py` — LuaTools (Luie) + optional Sushi fallback.
- `core/manifest_provider.py` — facade: selection, canonical cache
  paths, branch selection, capability aggregation.
- `utils/isp_bypass.py` — `execute_provider_request(...)` (domain/rate-limit
  parameterized); `execute_hubcap_request` is a Hubcap-preserving wrapper.

## Capability matrix (verified)

| Capability | vapor | hubcap | ryuu | luatools |
|---|---|---|---|---|
| `SINGLE_MANIFEST` | ✅ | ✅ | ❌ | ❌ |
| `BUNDLE_DOWNLOAD` | ❌ | ✅ | ✅ | ✅ |
| `NATIVE_BRANCH` | n/a | ❌ | ✅ | ❌ |
| `CONTENTS` | ❌ | ✅ | ❌ (derived from ZIP) | ❌ (derived from ZIP) |
| `DEPOT_KEY_INDEX` | ❌ | ✅ | ❌ | ❌ |
| `SEARCH` | ❌ | ✅ | ❌ (optional local `ryuu_games.json`) | ❌ |
| `ACCOUNT_STATS` | ❌ | ✅ | ❌ | ❌ |
| `HEALTH` | ❌ | ✅ | ✅ | ✅ |
| `REQUEST_MISSING` | ❌ | ❌ | ✅ | ❌ |

## Selection & strictness

- Setting `manifest_bundle_provider` (Settings → Integrations):
  `hubcap` (default), `ryuu`, or `luatools`.
- Explicit provider selection is strict (no silent failover to another provider on error).
- Single manifests always try the local Vapor engine first, then Hubcap.
- Bundles without a provider `/contents` endpoint derive contents from the cached
  ZIP; with no search provider, search falls back to Steam Community SearchApps.

## Ryuu notes (verified 2026-11-27)

- Base URL `https://generator.ryuu.lol`; auth via `X-Auth-Key`
  (`ryuu_auth_key`, permanent code from `/login`).
- `GET /api/download/{appid}` returns a ZIP with `{appid}.lua` and flat
  `{depot}_{gid}.manifest` members — already canonical, no adapter needed.
  `file_type=manifest` is identical; `file_type=lua` returns the lua as text.
- Branch bundles are native: `?branch=<name>` returns a canonical branch ZIP.
  **Never send `branch=public`** — it 404s ("Game not found").
- Downloads work anonymously; an invalid/expired key must not block them, so the
  client retries once without the header on 401/403. `/request*` require the key.
- `/api/manifest-keys` is 403 even with a valid key → no depot-key index.
- Rate limiting is tight (~5 burst → HTTP 429). The shared transport paces and
  backs off per domain; the provider adds a 60s cooldown after a 429.
- No search, quota/stats, or per-depot generate endpoint.

## Adding a provider

1. Subclass `ManifestProvider` in `core/providers/<name>.py`.
2. Declare `meta = ProviderMeta(id=..., label=..., capabilities=frozenset({...}))`.
3. Implement only the methods for the declared capabilities. Defaults raise
   `CapabilityNotSupported`, which the facade treats as "try the next provider".
4. Set `rate_limit` and `bypass_domains` if applicable, and route requests through
   `execute_provider_request`.
5. Register it in `core/providers/__init__.py::register_builtins`.
6. Add credentials under a new setting and extend `utils/logger.py` redaction.
7. Add a script under `scripts/test_*.py` that exercises it with fakes — no
   network in CI.
