#!/usr/bin/env python3
"""Test the selection policy the depot dialog's presets share.

Smart Select, the platform presets, the edition menu and DLC-only mode all
replace the current ticks with one set of depot ids. The rules behind those
sets - which depots Steam would install, which a platform preset wants, which
the DLC rules allow - live in ``selection_policy`` and are pinned here against
plain depot dictionaries and fake recommender functions: no display, no Steam
install, no network.

    python3 scripts/test_selection_policy.py
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


def row(sp, depot_id, desc="", config_text="", size=0, **data):
    return sp.DepotRowFacts(str(depot_id), desc, config_text, size, data)


def main() -> int:
    from ui.dialogs.depot_selection import selection_policy as sp

    # ------------------------------------------------- depot records, any key
    print("=== reading a depot record whichever way it is keyed ===")
    check(sp.depot_data({"123": {"size": 1}}, "123") == {"size": 1},
          "a string-keyed record is found by string id")
    check(sp.depot_data({123: {"size": 2}}, "123") == {"size": 2},
          "an int-keyed record is found by string id")
    check(sp.depot_data({"123": {}}, "456") == {},
          "an unknown depot gets an empty record, not a crash")

    # ------------------------------------------------------------ what is DLC
    print("=== what counts as DLC ===")
    check(sp.is_dlc_row("456", "[DLC 7] Extra levels", "", {}, "400") is True,
          "a [DLC] tag in the description is DLC")
    check(sp.is_dlc_row("456", "Extra levels", " [DLC 7] ", {}, "400") is True,
          "a [DLC] tag in the configuration cell is DLC")
    check(sp.is_dlc_row("456", "Bonus content", "", {"is_dlc": True}, "400") is True,
          "the depot record's is_dlc flag is DLC")
    check(sp.is_dlc_row("456", "Bonus content", "", {"dlcappid": "900"}, "400") is True,
          "a dlcappid makes a depot DLC")
    check(sp.is_dlc_row("456", "Depot 456", "", {"is_dlc": True}, "400") is False,
          "a flagged depot still reading only 'Depot N' is treated as base-game data")
    check(sp.is_dlc_row("456", "Depot 456", "", {}, "400") is False,
          "a plain depot is not DLC")
    check(sp.is_dlc_row("400", "[DLC 1] Engine data", "", {}, "400") is False,
          "the base game's own depot is never DLC, whatever its text says")

    # ------------------------------------------- the four DLC-only-mode rules
    print("=== the DLC-only auto-selection rules ===")
    sel = sp.dlc_auto_selection(
        [row(sp, "400", "Game content"), row(sp, "456", "[DLC 7] Pack", size=5_000_000)], "400"
    )
    check(sel.ids == {"456"}, "a real DLC depot is selected")
    check(400 in sel.ids or "400" not in sel.ids, "the base-game depot is never auto-selected")
    check(sel.dlc_count == 1 and sel.os_conflict is False and sel.bypass_size_limit is False,
          "counts and flags describe what was found")

    stub = sp.dlc_auto_selection(
        [row(sp, "457", "[DLC 8] Soundtrack", size=512)], "400"
    )
    check(stub.ids == set(), "a one-KB DLC stub is skipped while sorting real content")

    zero = sp.dlc_auto_selection([row(sp, "458", "[DLC 9] Extra", size=0)], "400")
    check(zero.ids == {"458"}, "an unknown size is treated as content, not a stub")

    conflict = sp.dlc_auto_selection(
        [
            row(sp, "460", "[DLC 10] Windows only", size=9_000_000, oslist="windows"),
            row(sp, "461", "[DLC 11] Linux only", size=9_000_000, oslist="linux"),
        ],
        "400",
    )
    check(conflict.ids == set(), "DLC for both Windows and Linux selects nothing")
    check(conflict.os_conflict is True, "and says why")

    from_cell = sp.dlc_auto_selection(
        [
            row(sp, "462", "[DLC 12] Win", size=9_000_000, config_text="[Windows] x64"),
            row(sp, "463", "[DLC 13] Lin", size=9_000_000, config_text="[Linux] x64"),
        ],
        "400",
    )
    check(from_cell.os_conflict is True,
          "platform tags in the configuration cell count when the record has no oslist")

    many = sp.dlc_auto_selection(
        [row(sp, str(500 + i), f"[DLC {i}] Pack", size=512) for i in range(64)], "400"
    )
    check(len(many.ids) == 64 and many.bypass_size_limit is True,
          "sixty-four DLC depots are all selected, stub sizes included")
    check(many.os_conflict is False, "and a same-platform set is not a conflict")

    # ---------------------------------------------------------- Smart Select
    print("=== the recommender behind Smart Select ===")
    fake_depots = {"400": {"oslist": "linux"}, "401": {"oslist": "linux"}}
    calls = []

    def steam_ok(app_id, depots, target_platform=""):
        calls.append((app_id, target_platform))
        return ["400", "401"]

    check(sp.recommended_depots("400", fake_depots, steam_lookup=steam_ok) == ["400", "401"],
          "what Steam names is what Smart Select uses")
    check(calls[0] == ("400", "linux"), "and the platform is passed through")

    def steam_boom(app_id, depots, target_platform=""):
        raise RuntimeError("no appcache")

    from ui.dialogs.depot_selection import rules

    original = rules.get_smart_default_depots
    rules.get_smart_default_depots = lambda depots, target_platform="": ["999"]
    try:
        check(sp.recommended_depots("400", fake_depots, steam_lookup=steam_boom) == ["999"],
              "a failing Steam lookup falls back to the platform rules")
        check(sp.recommended_depots("400", fake_depots, steam_lookup=lambda *a, **k: []) == ["999"],
              "a Steam lookup that names nothing falls back too")

        def should_not_run(*a, **k):
            raise AssertionError("the platform preset must not ask Steam")

        check(sp.recommended_depots("400", fake_depots, platform="windows",
                                    use_steam=False, steam_lookup=should_not_run) == ["999"],
              "a platform preset asks the rules directly")
    finally:
        rules.get_smart_default_depots = original

    # -------------------------------------------------- Edition button text
    print("=== how the Edition button reads ===")
    complete = {"name": "Complete Edition", "platform": "linux", "short_name": "Complete"}
    check(sp.platform_label("linux") == "Linux (Native)", "linux is called Linux (Native)")
    check(sp.platform_label("windows") == "Windows (Proton)", "windows is called Windows (Proton)")
    check(sp.edition_clean_name(complete) == "Complete Edition", "the clean name keeps the edition name")
    check(sp.edition_clean_name({"name": "Everything (All DLCs)"}) == "Everything",
          "'(All DLCs)' is dropped from the button label")
    check(sp.edition_clean_name({"name": "Base (Base Game Only)"}) == "Base",
          "'(Base Game Only)' is dropped too")
    check(sp.edition_clean_name({}) == "Edition", "an unnamed edition still has a label")
    check(sp.edition_button_text(complete) == "Complete Edition ▾",
          "a single-platform game shows the name alone")
    check(sp.edition_button_text(complete, multi_platform=True) == "Linux: Complete Edition ▾",
          "a multi-platform game shows which platform the edition belongs to")
    check(sp.edition_button_text({"name": "X", "platform": "windows"}, multi_platform=True) == "Win: X ▾",
          "windows editions read 'Win:'")
    long_name = "Super Deluxe Definitive Edition Plus Extras"
    long_text = sp.edition_button_text({"name": long_name})
    check(long_text.endswith("... ▾") and len(long_text) < len(long_name) + 2,
          "a long edition name is truncated to fit the button")
    check(sp.edition_button_tooltip(complete) == "Linux: Complete Edition",
          "the tooltip names the platform and the edition")

    # ------------------------------------------------------- the edition menu
    print("=== the shape of the edition menu ===")
    editions = {
        "linux": [
            {"name": "Complete Edition", "platform": "linux", "depot_ids": {"400", "401"}},
            {"name": "Base Game", "platform": "linux", "depot_ids": {"400"}},
        ],
        "windows": [
            {"name": "Complete Edition", "platform": "windows", "depot_ids": {"402"}},
        ],
    }
    one = sp.edition_menu(["linux"], {"linux": editions["linux"]}, ["400", "401"])
    check(len(one) == 1 and one[0].label is None,
          "a single-platform game leaves the entries at the top level")
    check([c.name for c in one[0].choices] == ["Complete Edition", "Base Game"],
          "every edition of that platform is offered")
    check(one[0].choices[0].checked is True and one[0].choices[1].checked is False,
          "the edition whose depots are exactly selected is ticked")

    both = sp.edition_menu(["linux", "windows"], editions, {"402"})
    check([g.label for g in both] == ["Linux (Native)", "Windows (Proton)"],
          "a multi-platform game gets a submenu per platform")
    check(both[1].choices[0].checked is True, "and the tick follows the selection across platforms")
    check(sp.edition_menu([], editions, [])[0].label is None,
          "an unknown platform list still leaves a usable menu")

    # --------------------------------------------- which rows select-all counts
    print("=== which rows the select-all click may tick ===")
    check(sp.counts_toward_select_all("", False) is True,
          "an ordinary visible depot can be ticked")
    check(sp.counts_toward_select_all("missing", False) is False,
          "a Hubcap-only depot has no tick box and is not counted")
    check(sp.counts_toward_select_all("expander", False) is False,
          "the hidden-depot expander is not counted")
    check(sp.counts_toward_select_all("", True) is False,
          "a hidden row is not on screen and is not counted")
    check(sp.counts_toward_select_all("missing", True) is False,
          "a hidden Hubcap-only depot is still not a tick")

    # ------------------------------------------------------- Qt stays out
    print("=== the module stays Qt-free ===")
    import subprocess

    probe = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('selection_policy_probe', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['selection_policy_probe'] = module\n"
        "spec.loader.exec_module(module)\n"
        "assert callable(module.dlc_auto_selection), 'module did not load'\n"
        "assert 'PyQt6' not in sys.modules, 'PyQt6 was imported: ' + str(sorted(\n"
        "    m for m in sys.modules if m.startswith('PyQt6')))\n"
        "print('clean')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, str(SRC_DIR / "ui" / "dialogs" / "depot_selection" / "selection_policy.py")],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    check(result.returncode == 0 and "clean" in result.stdout,
          "the policy loads on its own without pulling PyQt6 in")
    if result.returncode != 0:
        print(f"    {result.stderr.strip().splitlines()[-1] if result.stderr else 'no output'!s}")

    print(f"\n{'=' * 60}")
    if _failures:
        print(f"FAILED: {len(_failures)} check(s)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"All {PASSED} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
