#!/usr/bin/env python3
"""Test how a base game's DLCs and their depots are discovered.

Clicking an owned Steam game asks one question before anything opens: does this
game have DLCs, and can any of them actually be downloaded? The answer decides
between the DLC selector, a warning, and turning DLC mode on without a
download. ``utils.dlc_helpers.discover_dlc_depots`` holds that answer and takes
every network hop as an argument, so all of it is driven here against fake
payloads - no Steam, no store, no display.

    python3 scripts/test_dlc_discovery.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from utils.dlc_helpers import (  # noqa: E402
    DlcCandidate,
    DlcDiscovery,
    discover_dlc_depots,
    has_game_dlcs,
)

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


def never(_appid):
    raise AssertionError("this source must not be consulted")


def main() -> int:
    print("=== Depots in memory ===")
    base = "1794680"
    depots = {
        base: {"name": "Game Content", "size": 9000},
        "1794681": {"dlcappid": "1794681", "name": "DLC One", "size": 2048},
        "1794682": {"dlcappid": "1794682", "name": "DLC Two", "size": 12},
    }
    found = discover_dlc_depots(base, depots, depot_info_fetcher=never, store_lookup=never)
    check(isinstance(found, DlcDiscovery), "returns a DlcDiscovery")
    check(found.appid == base, "keeps the base appid")
    check(found.sources == ("memory",), "reports the in-memory source")
    check([c.depot_id for c in found.depots] == ["1794681", "1794682"], "keeps DLC depots only")
    check(base not in found.catalog, "never lists the base game as a DLC")
    check(found.catalog == {"1794681": "DLC One", "1794682": "DLC Two"}, "catalog carries DLC names")
    check([c.is_stub for c in found.depots] == [False, True], "marks the small depot as a stub")
    check(found.has_sized_depots is True, "a real depot makes the game downloadable")
    check(found.is_config_only is False, "so it is not config-only")

    print("=== Config-only DLCs ===")
    stubs = {"1794682": {"dlcappid": "1794682", "name": "DLC Two", "size": 12}}
    only_stubs = discover_dlc_depots(base, stubs, depot_info_fetcher=never, store_lookup=never)
    check(only_stubs.depots[0].is_stub is True, "the only depot is a stub")
    check(only_stubs.has_sized_depots is False, "so nothing is downloadable")
    check(only_stubs.is_config_only is True, "and the game is config-only")
    check(only_stubs.catalog == {"1794682": "DLC Two"}, "the DLC is still known by name")

    listed = discover_dlc_depots(
        base, None, app_info={"listofdlc": "111, 222"}, depot_info_fetcher=never
    )
    check(listed.catalog == {"111": "", "222": ""}, "listofdlc alone populates the catalog")
    check(listed.is_config_only is True, "a name-only list is config-only")
    check(listed.sources == (), "no depots means no depot source")

    unknown = discover_dlc_depots(base, {"77": {"dlcappid": "77", "name": "X", "size": 0}})
    check(unknown.depots[0].is_stub is False, "an unknown size is not called a stub")
    check(unknown.has_sized_depots is True, "and keeps the selection path open")

    print("=== The sixty-four DLC exception ===")
    many = {
        str(9000 + i): {"dlcappid": str(9000 + i), "name": f"Pack {i}", "size": 10}
        for i in range(64)
    }
    big = discover_dlc_depots(base, many, depot_info_fetcher=never)
    check(len(big.depots) == 64, "all sixty-four depots are kept")
    check(big.has_sized_depots is True, "dozens of stubs count as downloadable")
    check(big.is_config_only is False, "so the size rule does not fire")
    almost = discover_dlc_depots(base, dict(list(many.items())[:63]))
    check(almost.has_sized_depots is False, "one below the line still uses the size rule")

    print("=== Names ===")
    tagged = discover_dlc_depots(
        base, {"55": {"dlcappid": "55", "desc": "[DLC 55] Real Name", "size": 5000}}
    )
    check(tagged.depots[0].name == "Real Name", "drops the PICS [DLC n] prefix")
    check(tagged.catalog == {"55": "Real Name"}, "and stores the clean name")
    untagged = discover_dlc_depots(base, {"66": {"name": "[DLC] Pack", "size": 5000}})
    check(untagged.depots[0].dlc_appid == "", "a depot without dlcappid has no DLC id")
    check(untagged.catalog == {}, "so it adds nothing to the catalog")

    print("=== Sources and their order ===")
    cached = discover_dlc_depots(
        base,
        None,
        app_info={
            "depots": {"31": {"dlcappid": "31", "name": "Cache DLC", "size": 5000}},
            "listofdlc": "31,32",
        },
        depot_info_fetcher=never,
    )
    check(cached.sources == ("cache",), "cached app info is used before the network")
    check(cached.catalog == {"31": "Cache DLC", "32": ""}, "cache ids without a depot stay in the catalog")

    def fake_pics(_appid):
        return {"depots": {"41": {"dlcappid": "41", "name": "PICS DLC", "size": 5000}}}

    pics = discover_dlc_depots(base, None, depot_info_fetcher=fake_pics)
    check(pics.sources == ("pics",), "the PICS lookup is the third source")
    check(pics.catalog == {"41": "PICS DLC"}, "and contributes depots and names")

    def explode(_appid):
        raise RuntimeError("boom")

    broken = discover_dlc_depots(base, None, depot_info_fetcher=explode)
    check(broken.depots == () and broken.catalog == {}, "a failed lookup degrades to nothing")

    print("=== The store is opt-in ===")
    store_calls = []

    def fake_store(appid):
        store_calls.append(appid)
        return [
            {"dlc_appid": "91", "dlc_name": "Store Pack"},
            {"dlc_appid": base, "dlc_name": "Base Game"},
            {"dlc_appid": "bad", "dlc_name": "Junk"},
        ]

    without = discover_dlc_depots(base, None, depot_info_fetcher=fake_pics, store_lookup=fake_store)
    check(store_calls == [], "the store is not consulted by default")
    check(without.sources == ("pics",), "and the cheap sources stand alone")

    spare = discover_dlc_depots(
        base, None, allow_store=True, depot_info_fetcher=fake_pics, store_lookup=fake_store
    )
    check(store_calls == [], "a depot that exists keeps the store out of it")
    check(spare.catalog == {"41": "PICS DLC"}, "and the catalog stays as the depots describe it")

    with_store = discover_dlc_depots(
        base,
        None,
        allow_store=True,
        depot_info_fetcher=lambda _appid: {},
        store_lookup=fake_store,
    )
    check(store_calls == [base], "allow_store asks once when nothing else found a depot")
    check(with_store.sources == ("store",), "the store is recorded as a source")
    check(with_store.catalog == {"91": "Store Pack"}, "store names are merged, base id dropped")
    check(with_store.is_config_only is True, "a store-only answer has no depot to download")

    print("=== Edge cases ===")
    check(discover_dlc_depots("").catalog == {}, "an empty appid finds nothing")
    check(discover_dlc_depots("N/A", {"1": {}}).depots == (), "a placeholder appid finds nothing")
    weird = discover_dlc_depots(
        base, {"77": {"dlcappid": "77", "name": "X", "size": "nope"}}, depot_info_fetcher=never
    )
    check(weird.depots[0].size == 0, "a non-numeric size reads as unknown")
    check(isinstance(weird.depots[0], DlcCandidate), "candidates keep their type")
    check(
        discover_dlc_depots(base, {"88": {"dlcappid": "88", "name": "Y", "oslist": "windows, linux"}})
        .depots[0]
        .oslist
        == ("windows", "linux"),
        "a comma string is split into an os list",
    )

    print("=== The old preflight still answers the same question ===")
    check(has_game_dlcs(base, depots) is True, "has_game_dlcs sees the DLC depots")

    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s)")
        for label in _failures:
            print(f"  - {label}")
        return 1
    print(f"All {PASSED} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
