#!/usr/bin/env python3
"""Test the provider seam (registry, capabilities, routing helpers, bundles).

No network, no Qt, no Steam install: providers are inspected and routed against
plain objects, and bundle normalization runs on temp zips.

    python3 scripts/test_provider_registry.py
"""
import io
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_failures = []
PASSED = 0


def check(cond, label):
    global PASSED
    if cond:
        PASSED += 1
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        _failures.append(label)
    return cond


def _zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def main() -> int:
    from core import manifest_provider as mp
    from core.providers import Capability, CapabilityNotSupported, registry
    from core.providers.base import normalize_bundle

    print("=== built-ins are registered with their capabilities ===")
    ids = registry.ids()
    for pid in ("hubcap", "ryuu", "vapor", "luatools"):
        check(pid in ids, f"provider '{pid}' is registered")

    hubcap = registry.get("hubcap")
    ryuu = registry.get("ryuu")
    vapor = registry.get("vapor")
    luatools = registry.get("luatools")

    check(hubcap.supports(Capability.BUNDLE_DOWNLOAD), "hubcap supports bundle download")
    check(hubcap.supports(Capability.SINGLE_MANIFEST), "hubcap supports single manifests")
    check(ryuu.supports(Capability.BUNDLE_DOWNLOAD), "ryuu supports bundle download")
    check(ryuu.supports(Capability.NATIVE_BRANCH), "ryuu supports native branch bundles")
    check(not ryuu.supports(Capability.SINGLE_MANIFEST), "ryuu does not claim single-manifest generation")
    check(vapor.supports(Capability.SINGLE_MANIFEST), "vapor supports single manifests")
    check(luatools.supports(Capability.BUNDLE_DOWNLOAD), "luatools supports bundle download")
    check(not luatools.supports(Capability.SINGLE_MANIFEST), "luatools does not claim single manifests")

    print("=== unsupported operations raise a typed capability error ===")
    try:
        vapor.download_bundle("440")
        check(False, "unsupported download_bundle raises")
    except CapabilityNotSupported:
        check(True, "unsupported download_bundle raises CapabilityNotSupported")

    print("=== routing order defaults to today's behaviour ===")
    original_active = mp.active_provider_id
    original_configured = mp._ryuu_configured
    try:
        mp.active_provider_id = lambda: "auto"
        mp._ryuu_configured = lambda: False
        bundle_order = [p.meta.id for p in mp._ordered(Capability.BUNDLE_DOWNLOAD, mp._BUNDLE_ORDER)]
        check(bundle_order and bundle_order[0] == "hubcap", "bundles try hubcap first by default")
        manifest_order = [p.meta.id for p in mp._ordered(Capability.SINGLE_MANIFEST, mp._MANIFEST_ORDER)]
        check(manifest_order and manifest_order[0] == "vapor", "single manifests try vapor first by default")

        check(mp.get_active_provider_label() == "Hubcap", "default provider short label is Hubcap")
        check(mp.active_provider_supports(Capability.ACCOUNT_STATS), "hubcap supports account stats")

        mp.active_provider_id = lambda: "ryuu"
        forced = [p.meta.id for p in mp._ordered(Capability.BUNDLE_DOWNLOAD, mp._BUNDLE_ORDER)]
        check(forced and forced[0] == "ryuu", "an explicit provider is tried first")
        check(mp.get_active_provider_label(full=False) == "Ryu", "ryuu short label is Ryu")
        check(mp.get_active_provider_label(full=True) == "Ryu (beta)", "ryuu full label is Ryu (beta)")
        check(not mp.active_provider_supports(Capability.ACCOUNT_STATS), "ryuu does not claim account stats")

        mp.active_provider_id = lambda: "luatools"
        lt_forced = [p.meta.id for p in mp._ordered(Capability.BUNDLE_DOWNLOAD, mp._BUNDLE_ORDER)]
        check(lt_forced and lt_forced[0] == "luatools", "luatools provider is tried first when active")
        check(mp.get_active_provider_label(full=False) == "LuaTools", "luatools short label is LuaTools")
        check(mp.get_active_provider_label(full=True) == "LuaTools (Luie)", "luatools full label is LuaTools (Luie)")

        mp.active_provider_id = lambda: "hubcap"
        no_single = [p.meta.id for p in mp._ordered(Capability.SINGLE_MANIFEST, mp._MANIFEST_ORDER)]
        check("ryuu" not in no_single, "capability filtering drops providers that cannot do the work")
    finally:
        mp.active_provider_id = original_active
        mp._ryuu_configured = original_configured

    print("=== ryuu auth headers are optional for bundles, required for requests ===")
    original_key = ryuu._auth_key
    try:
        ryuu._auth_key = lambda: ""
        check(ryuu._headers() == {}, "missing key still allows anonymous bundle download")
        try:
            ryuu._headers(required=True)
            check(False, "request endpoints require the key")
        except Exception as e:
            from core.providers.base import ProviderAuthError
            check(isinstance(e, ProviderAuthError), "request endpoints raise ProviderAuthError without a key")
        ryuu._auth_key = lambda: "abc"
        check(ryuu._headers() == {"X-Auth-Key": "abc"}, "configured key is sent as X-Auth-Key")
    finally:
        ryuu._auth_key = original_key

    print("=== failure detection ===")
    check(mp._is_failure((None, "err")), "a (None, err) tuple is a failure")
    check(not mp._is_failure((b"bytes", None)), "a (bytes, None) tuple is a success")
    check(mp._is_failure(None), "None is a failure")
    check(not mp._is_failure({"status": "success"}), "a plain dict is a success")

    print("=== bundle normalization flattens nested archives ===")
    tmpdir = Path(tempfile.mkdtemp(prefix="assella-provider-test-"))
    nested = tmpdir / "nested.zip"
    nested.write_bytes(_zip_bytes({
        "440/440.lua": "addappid(440)",
        "440/440_123.manifest": b"\xd0\x17\xf6\x71",
    }))
    flat, err = normalize_bundle(nested, "440", "public")
    check(err is None, "nested bundle normalizes without error")
    with zipfile.ZipFile(flat) as zf:
        names = zf.namelist()
    check("440.lua" in names and "440_123.manifest" in names, "members are flattened to the root")

    lua_only = tmpdir / "luaonly.zip"
    lua_only.write_bytes(_zip_bytes({"440.lua": "addappid(440)"}))
    _, err = normalize_bundle(lua_only, "440", "public")
    check(err is None, "a lua-only bundle is accepted")

    broken = tmpdir / "broken.zip"
    broken.write_bytes(b"not a zip")
    _, err = normalize_bundle(broken, "440", "public")
    check(err is not None, "a non-zip bundle is rejected")

    print("=== facade mirrors the legacy morrenus_api surface ===")
    for name in (
        "download_manifest", "generate_single_manifest", "get_manifest_contents",
        "get_manifest_status", "search_games", "get_hubcap_depot_keys",
        "get_selected_branch", "get_manifest_zip_path", "check_health",
    ):
        check(callable(getattr(mp, name, None)), f"facade exposes {name}()")

    print("=== facade failover across providers ===")
    from core.providers import ManifestProvider, ProviderMeta

    class FakeBundle(ManifestProvider):
        meta = ProviderMeta("fake_bundle", "Fake", frozenset({Capability.BUNDLE_DOWNLOAD}))
        def __init__(self, result):
            self.result = result
            self.calls = 0
        def download_bundle(self, *a, **k):
            self.calls += 1
            return self.result

    failing = FakeBundle((None, "boom"))
    working_file = tmpdir / "fake_bundle.zip"
    working_file.write_bytes(_zip_bytes({"999.lua": "addappid(999)"}))
    working = FakeBundle((str(working_file), None))

    original_active = mp.active_provider_id
    original_ordered = mp._ordered
    original_zip_path = mp.get_manifest_zip_path
    try:
        mp.active_provider_id = lambda: "auto"
        mp._ordered = lambda cap, order: [failing, working] if cap == Capability.BUNDLE_DOWNLOAD else []
        target = tmpdir / "canonical" / "bundle.zip"
        mp.get_manifest_zip_path = lambda app_id, branch="public": target
        path, err = mp.download_manifest("999")
        check(err is None and path == str(target), "download falls through to the first successful provider in auto mode")
        check(target.exists(), "the successful bundle is stored at the canonical path")
        check(failing.calls == 1 and working.calls == 1, "both providers were tried in order")

        mp._ordered = lambda cap, order: [failing] if cap == Capability.BUNDLE_DOWNLOAD else []
        path2, err2 = mp.download_manifest("999")
        check(path2 is None and err2 == "boom", "download reports the last provider error when all fail")
    finally:
        mp.active_provider_id = original_active
        mp._ordered = original_ordered
        mp.get_manifest_zip_path = original_zip_path

    print("=== contents fall back to the cached bundle ===")
    original_zip_path = mp.get_manifest_zip_path
    try:
        cache_zip = tmpdir / "cached.zip"
        cache_zip.write_bytes(_zip_bytes({
            "730.lua": "addappid(730)",
            "731_111.manifest": b"x",
            "732_222.manifest": b"y",
        }))
        mp.get_manifest_zip_path = lambda app_id, branch="public": cache_zip
        derived = mp._contents_from_cache("730")
        check(derived.get("manifest_count") == 2, "contents derived from the cached bundle")
        check(derived.get("manifest_map") == {"731": "111", "732": "222"}, "depot/manifest map is correct")
    finally:
        mp.get_manifest_zip_path = original_zip_path

    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {_failures}")
        return 1
    print(f"All {PASSED} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
