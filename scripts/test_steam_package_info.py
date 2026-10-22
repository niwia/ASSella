#!/usr/bin/env python3
"""Test the package lookup that reads Steam's local appcache.

The appcache is the only offline source that knows which packages a game
belongs to; the store's buy box shows one package and under-reports games that
ship complete editions.  These checks pin the contract without depending on
what happens to be installed on the machine running them.

    python3 scripts/test_steam_package_info.py
"""
import sys
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


def main() -> int:
    from core.steam_package_info import (
        get_depots_for_app_from_packageinfo,
        get_packageinfo_path,
        get_subs_for_app_from_packageinfo,
    )

    print("=== bad input never reaches the parser ===")
    for bad in ("", "not-a-number", None, "12 34"):
        check(get_subs_for_app_from_packageinfo(bad) == [],
              f"no packages for {bad!r}")
        check(get_depots_for_app_from_packageinfo(bad) == set(),
              f"no depots for {bad!r}")

    path = get_packageinfo_path()
    print(f"=== the appcache on this machine: {path} ===")
    if path is None:
        print("  (no appcache here, contract checks only)")
    else:
        # A packaged game that every Steam install knows about keeps the
        # function honest without hard-coding library contents.
        subs = get_subs_for_app_from_packageinfo("228980")  # Steamworks Common
        check(isinstance(subs, list), "packages come back as a list")
        check(all(isinstance(s, str) and s.isdigit() for s in subs),
              "every package id is a digit string")
        check(subs == sorted(subs, key=int), "package ids are sorted")
        again = get_subs_for_app_from_packageinfo("228980")
        check(again == subs, "the second call answers from the cache")

    print()
    if _failures:
        print(f"{len(_failures)} check(s) FAILED:")
        for label in _failures:
            print(f"  - {label}")
        return 1
    print(f"All {PASSED} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
