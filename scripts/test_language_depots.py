#!/usr/bin/env python3
"""Language depots must never be reported as missing content.

Steam PICS lists a game's language carriers as ordinary depots and names them
in a `baselanguages` map. They are localisation files, not manifest-bundle
content, so Hubcap carries no keys for them and they are absent from every
bundle. Comparing "depots Steam lists" against "depots in the bundle" therefore
reported them as missing forever.

Observed on Dragon's Dogma 2 (2054970): 8 permanent greyed-out "[Missing]" rows
plus one WARNING each, every one a ~730 byte language file.

Hermetic: pure functions over literal dicts, no Steam, no network, no DB.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_language_depots.py
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_failures = []
PASSED = 0


def check(cond: bool, label: str) -> bool:
    global PASSED
    if cond:
        PASSED += 1
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        _failures.append(label)
    return bool(cond)


def _depot(name, size, gid):
    return {"name": name, "size": str(size),
            "manifests": {"public": {"gid": str(gid), "size": str(size)}}}


# Dragon's Dogma 2, 2054970: 4 real content depots, 1 DLC, 8 language carriers.
LANG_IDS = ["2757100", "2757110", "2757150", "2757160",
            "2757180", "2757190", "2757200", "2757210"]

DD2_API = {
    "2054971": _depot("Dragons Dogma 2", 154689650, 1),
    "2054972": _depot("Dragons Dogma 2", 154689650, 2),
    "2054973": _depot("Dark Arisen", 69392135623, 3),
    "2054974": _depot("Dark Arisen", 69392135912, 4),
    "2593290": _depot("Dark Arisen Expansion", 26906257761, 9),
}
for _i, _did in enumerate(LANG_IDS):
    DD2_API[_did] = _depot(None, 726 + _i, 100 + _i)

DD2_LOCAL = {"2054971": "1", "2054972": "2", "2054973": "3", "2054974": "4"}

DD2_LANGS = {
    "english": "2054971", "japanese": "2054972",
    "french": LANG_IDS[0], "german": LANG_IDS[1], "italian": LANG_IDS[2],
    "spanish": LANG_IDS[3], "korean": LANG_IDS[4], "portuguese": LANG_IDS[5],
    "russian": LANG_IDS[6], "chinese": LANG_IDS[7],
}


def main() -> int:
    from utils.depot_utils import check_hubcap_vs_steam_depots

    print("\n=== the bug: language depots reported as missing ===")
    before = check_hubcap_vs_steam_depots(DD2_LOCAL, DD2_API, app_id="2054970")
    check(len(before["missing_from_hubcap"]) == 9,
          f"without baselanguages all 9 are reported ({len(before['missing_from_hubcap'])})")

    print("\n=== with baselanguages, only real content is reported ===")
    after = check_hubcap_vs_steam_depots(
        DD2_LOCAL, DD2_API, app_id="2054970", base_languages=DD2_LANGS)
    check(after["missing_from_hubcap"] == ["2593290"],
          f"only the genuine DLC remains ({after['missing_from_hubcap']})")
    for did in LANG_IDS:
        if did in after["missing_from_hubcap"]:
            check(False, f"language depot {did} excluded")
            break
    else:
        check(True, "all 8 language depots excluded")

    print("\n=== the fix must not hide real missing content ===")
    check("2593290" in after["missing_from_hubcap"],
          "a genuinely absent DLC is still reported")
    check(bool(after["missing_depots_info"]), "missing depot metadata is still produced")

    print("\n=== a language depot that is also the base game is not lost ===")
    # 2054971/2054972 carry english/japanese AND are real content depots. They
    # are present locally here, so nothing changes; the guard is that passing
    # baselanguages never removes an entry from the local set.
    check("2054971" in DD2_LOCAL, "base game depot is in the local manifest set")

    # Drop one language from the map and that depot becomes reportable again -
    # the filter is driven entirely by the declaration, nothing is inferred.
    partial = dict(DD2_LANGS)
    partial.pop("german")
    mixed = check_hubcap_vs_steam_depots(
        DD2_LOCAL, DD2_API, app_id="2054970", base_languages=partial)
    expected = sorted(["2593290", LANG_IDS[1]])
    check(sorted(mixed["missing_from_hubcap"]) == expected,
          f"only the undeclared language is reported ({mixed['missing_from_hubcap']})")
    check(LANG_IDS[1] in mixed["missing_from_hubcap"],
          "the depot dropped from baselanguages is reported again")

    print("\n=== filtering never depends on ordering ===")
    a = check_hubcap_vs_steam_depots(DD2_LOCAL, DD2_API, app_id="2054970",
                                     base_languages=DD2_LANGS)
    b = check_hubcap_vs_steam_depots(DD2_LOCAL, DD2_API, app_id="2054970",
                                     base_languages=dict(reversed(list(DD2_LANGS.items()))))
    check(sorted(a["missing_from_hubcap"]) == sorted(b["missing_from_hubcap"]),
          "reordering baselanguages does not change the result")

    print("\n=== degenerate inputs stay safe ===")
    none_langs = check_hubcap_vs_steam_depots(DD2_LOCAL, DD2_API, app_id="2054970",
                                             base_languages=None)
    check(len(none_langs["missing_from_hubcap"]) == 9,
          "base_languages=None behaves exactly as before")

    for bad in ({}, [], "english", 12345, {"english": None}, {"english": "not-a-number"}):
        try:
            out = check_hubcap_vs_steam_depots(DD2_LOCAL, DD2_API,
                                               app_id="2054970", base_languages=bad)
            ok = isinstance(out.get("missing_from_hubcap"), list)
        except Exception as e:
            ok = False
            print(f"        raised: {e!r}")
        if not check(ok, f"tolerates base_languages={bad!r}"):
            break

    print("\n=== the SteamCMD/web-api path has no baselanguages, so it is unchanged ===")
    # No language map available: fall back to the previous behaviour rather
    # than guessing from size and name, which is what produced the false
    # positives in the first place.
    nolang = check_hubcap_vs_steam_depots(DD2_LOCAL, DD2_API, app_id="2054970",
                                         base_languages={})
    check(len(nolang["missing_from_hubcap"]) == 9,
          "empty baselanguages keeps the old behaviour (no guessing)")

    print("\n=== baselanguages survives the DB round trip ===")
    # get_app_info pops it out of depots_json and returns it at top level, so a
    # cache hit still suppresses the false positives.
    import ast
    db_src = (SRC_DIR / "managers/db_manager.py").read_text()
    check('baselanguages = depots_data.pop("baselanguages", None)' in db_src,
          "db_manager pops baselanguages out of the depots blob")
    check('"baselanguages": baselanguages,' in db_src,
          "db_manager returns baselanguages at the top level")

    steam_src = (SRC_DIR / "core/steam_api.py").read_text()
    check('"baselanguages": baselanguages,' in steam_src,
          "steam_api carries baselanguages into api_data")

    print("\n=== both call sites pass it through ===")
    for mod, rel in (("src/core/manifest_fetcher.py", "manifest_fetcher"),
                     ("src/core/tasks/process_zip_task.py", "process_zip_task")):
        src = (REPO_ROOT / mod).read_text()
        check("base_languages=" in src, f"{rel} passes base_languages")

    proc_src = (SRC_DIR / "core/tasks/process_zip_task.py").read_text()
    check("_base_languages = api_data.get(\"baselanguages\") or {}" in proc_src,
          "process_zip_task reads baselanguages off api_data")

    print("\n=== the per-depot key query was hoisted out of the loop ===")
    # It used to call get_depot_keys() once per missing depot - 8 extra SQLite
    # reads on Dragon's Dogma 2 alone.
    anchor = "for m_did, m_mid, m_name in depot_comp.get(\"missing_for_fetch\""
    check(anchor in proc_src, "the missing-depot loop was located")
    tail = proc_src.split(anchor)[1]
    # The loop ends where the refetch summary starts, then the missing-depot
    # block that logs the summary follows.
    end = tail.find("if refetched_depots:")
    check(end > 0, "the end of the missing-depot loop was located")
    loop_body = tail[:end]

    # Exactly one call is correct: the lazy first-miss fetch. The regression
    # this guards is that call being inside the per-depot body unconditionally,
    # which is what issued one SQLite read per missing depot.
    check(loop_body.count("get_depot_keys(") == 1,
          f"exactly one lazy get_depot_keys() call in the loop (got {loop_body.count('get_depot_keys(')})")
    check("cached_depot_keys is None" in loop_body,
          "that call is guarded so the map is fetched at most once")
    check("cached_depot_keys = cached_depot_keys or {}" not in loop_body,
          "the cached map is not re-read inside the loop")
    check("cached_depot_keys" in loop_body,
          "the loop reads the hoisted cached_depot_keys map")
    check("cached_depot_keys = None" in proc_src,
          "cached_depot_keys is initialised before the loop")
    check("if not depot_key and cached_depot_keys is None and DepotKeyManager:" in proc_src,
          "the key map is fetched only on a first miss")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)} of {PASSED + len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"ALL LANGUAGE DEPOT TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
