#!/usr/bin/env python3
"""Test which tab a DLC-only install lands on.

DLC-only mode downloads a game's DLC depots while the game itself stays on
Steam.  That makes it a Steam-tab game: it must never be counted or listed as
an ACCELA install, and the AT0-M correlation must not pull it over either.

    python3 scripts/test_library_tabs.py
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
    from ui.dialogs.library.filter_sort import game_is_dlc_only

    print("=== the record's own flag decides ===")
    check(game_is_dlc_only({"appid": "262060", "is_dlc_only": True}) is True,
          "a marked record is DLC-only")
    check(game_is_dlc_only({"appid": "262060", "is_dlc_only": False}) is False,
          "an explicitly unmarked record is a normal install")

    print("=== records from before the field existed fall back to the setting ===")
    check(game_is_dlc_only({"appid": ""}) is False, "no app id is never DLC-only")
    check(game_is_dlc_only({"appid": "N/A"}) is False, "a placeholder id is never DLC-only")
    check(game_is_dlc_only({"appid": "not-a-number"}) is False,
          "a non-numeric id is never DLC-only")
    # A real id with no field asks the persisted setting; the answer is a bool
    # either way, and this suite must not depend on what is set on this machine.
    answered = game_is_dlc_only({"appid": "228980"})
    check(isinstance(answered, bool), "a real id answers yes or no, never None")

    print("=== both tabs skip DLC-only records ===")
    source = (SRC_DIR / "ui" / "dialogs" / "library" / "filter_sort.py").read_text()
    check(source.count("and not game_is_dlc_only(g)") >= 1,
          "the managed set leaves DLC-only games out")
    check("if game_is_dlc_only(g):" in source,
          "the ACCELA tab list filters DLC-only games")
    check("if g.get(\"is_accela_install\") and not (" not in source,
          "no ACCELA tab branch is left without the DLC-only filter")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)} of {PASSED + len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"ALL LIBRARY TAB TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
