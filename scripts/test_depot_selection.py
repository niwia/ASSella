#!/usr/bin/env python3
"""Tests for the depot selection package after the split.

The classification rules are the part most likely to break silently - they
decide which depots get ticked by default - and they are now free of Qt, so
they can be tested directly rather than through a dialog.

Also pins the structural guarantees of the split:
  * the layout stayed thin - a small composer plus one mixin per behaviour
  * the facade still exports every symbol the nine importers use

The Tier 1 split moved definitions byte-for-byte; anything deepened since (the
manifest dump in manifest_dump.py) is covered by its own suite instead of by
identity.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_depot_selection.py
"""
import ast
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
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


# A depot set modelled on Two Point Museum: base depots for two platforms,
# six DLC apps each with three depots, and media to be excluded.
DEPOTS = {
    "2185061": {"name": "Win base", "oslist": "windows", "language": ""},
    "2185062": {"name": "Linux base", "oslist": "linux", "language": ""},
    "2185063": {"name": "macOS base", "oslist": "macos", "language": ""},
    "2185064": {"name": "Soundtrack", "oslist": "windows", "language": ""},
    "2999841": {"name": "DLC one", "oslist": "windows", "dlcappid": "2999840"},
    "2999842": {"name": "DLC two", "oslist": "windows", "dlcappid": "2999840"},
    "228989": {"name": "Steamworks Shared", "oslist": ""},
}


def main() -> int:
    from ui.dialogs.depot_selection import rules

    # ------------------------------------------------------------- facade
    print("=== facade still exports what the importers use ===")
    import ui.dialogs.depotselection as facade

    needed = ["DepotSelectionDialog", "_depot_is_android", "_depot_is_macos",
              "_depot_matches_platform", "format_size", "get_smart_default_depots",
              "is_bonus_or_media_depot"]
    missing = [n for n in needed if not hasattr(facade, n)]
    check(not missing, f"all {len(needed)} imported symbols resolve (missing {missing})")

    print("\n=== package layout ===")
    pkg = SRC_DIR / "ui" / "dialogs" / "depot_selection"
    for name in ("rules.py", "items.py", "views.py", "dialog.py"):
        p = pkg / name
        n = len(p.read_text().splitlines()) if p.is_file() else 0
        check(p.is_file(), f"{name} exists ({n} lines)")
    check(
        len((pkg / "dialog.py").read_text().splitlines()) < 600,
        "dialog.py is a thin composer - behaviours live in _mixins/",
    )
    mixins = sorted(p.stem for p in (pkg / "_mixins").glob("*.py"))
    check(len(mixins) >= 10, f"_mixins/ present ({len(mixins)} modules, {', '.join(mixins)})")
    check(
        len((facade.__file__ and (SRC_DIR/'ui'/'dialogs'/'depotselection.py')).read_text().splitlines()) < 80,
        "facade is small (re-export surface only)",
    )
    check((pkg / "storage_targets.py").is_file(),
          "storage_targets.py holds the drive rules both depot dialogs share")
    single_src = (SRC_DIR / "ui" / "dialogs" / "single_depot_dialog.py").read_text()
    check("storage_targets" in single_src and "_format_storage_info" not in single_src,
          "the single-depot dialog takes its drives from storage_targets")
    check((pkg / "depot_metadata.py").is_file(),
          "depot_metadata.py holds the depot facts the metadata mixin renders")
    metadata_src = (pkg / "_mixins" / "metadata.py").read_text()
    check("depot_metadata" in metadata_src and "manifests.get(self.branch) or manifests.get" not in metadata_src,
          "the metadata mixin asks depot_metadata instead of walking manifests itself")
    mixins_src = (pkg / "_mixins" / "table.py").read_text()
    check("depot_metadata.manifest_size" in mixins_src,
          "the missing-depot rows take their size from depot_metadata")
    check((pkg / "executable_probe.py").is_file(),
          "executable_probe.py holds the search for the game's launch executable")
    executable_src = (pkg / "_mixins" / "executable.py").read_text()
    check("executable_probe" in executable_src and "import struct" not in executable_src,
          "the executable mixin asks executable_probe instead of parsing Steam's cache itself")
    check((pkg / "selection_policy.py").is_file(),
          "selection_policy.py holds which depots each preset ticks")
    selection_src = (pkg / "_mixins" / "selection.py").read_text()
    check("selection_policy" in selection_src and "get_smart_default_depots" not in selection_src,
          "the selection mixin asks selection_policy instead of choosing depots itself")
    check(selection_src.count("setCheckState(Qt.CheckState.Checked)") == 1,
          "every preset ticks rows through one helper")

    # -------------------------------------------------------------- rules
    print("\n=== platform rules ===")
    check(rules._depot_matches_platform({"oslist": "windows"}, "windows"), "windows depot matches windows")
    check(not rules._depot_matches_platform({"oslist": "windows"}, "linux"), "windows depot does not match linux")
    check(rules._depot_matches_platform({"oslist": ""}, "linux"),
          "empty oslist (shared) matches every platform")
    check(rules._depot_matches_platform({"oslist": "x", "desc": "[LINUX] build"}, "linux"),
          "description tag [LINUX] is honoured")
    check(rules._depot_is_macos({"oslist": "macosx"}), "macosx counts as macOS")
    check(not rules._depot_is_macos({"oslist": "windows"}), "windows is not macOS")
    check(rules._depot_is_android({"oslist": "android"}), "android detected from oslist")
    check(rules._depot_is_android({"desc": "[Android] data"}), "android detected from desc")

    print("\n=== media / bonus exclusion ===")
    check(rules.is_bonus_or_media_depot({"name": "Soundtrack"}), "soundtrack excluded")
    check(rules.is_bonus_or_media_depot({"name": "x", "desc": "Original Soundtrack"}), "desc OST excluded")
    check(rules.is_bonus_or_media_depot({"name": "Dedicated Server"}), "dedicated server excluded")
    check(not rules.is_bonus_or_media_depot({"name": "Linux base"}), "base game not excluded")

    print("\n=== smart defaults ===")
    sel = rules.get_smart_default_depots(DEPOTS, target_platform="linux", language="english")
    check("2185062" in sel, "native linux base depot selected")
    check("2185063" not in sel, "macOS base depot excluded")
    check("2185064" not in sel, "soundtrack excluded")
    check("228989" in sel, "shared depot included")
    check("2185061" not in sel,
          "windows-only base excluded when native linux exists")
    check(all(isinstance(x, str) for x in sel), "returns string ids")

    # No native linux -> falls back to windows (Proton) rather than selecting nothing
    win_only = {"100": {"oslist": "windows"}, "101": {"oslist": "macos"}}
    sel2 = rules.get_smart_default_depots(win_only, target_platform="linux")
    check("100" in sel2, "falls back to windows when no native linux depots exist")

    check(rules.get_smart_default_depots({}, "linux") == [], "empty input -> empty output")

    print("\n=== size formatting (units matter: KiB not KB) ===")
    check(rules.format_size(0) == "0.00 B", "zero -> 0.00 B")
    check(rules.format_size(1024) == "1.00 KiB", "1024 -> 1.00 KiB")
    check(rules.format_size(1536) == "1.50 KiB", "1536 -> 1.50 KiB")
    check(rules.format_size(None) == "0.00 B", "None -> 0.00 B")
    check(rules.format_size("junk") == "Unknown", "junk -> Unknown")

    # ------------------------------------------------------- no click-sort
    print("\n=== table is not click-sortable; select-all still wired ===")
    # The table code now lives in _mixins/. These are guarantees about the
    # shipped dialog as a whole, so scan the whole package, not one file.
    dlg_src = "\n".join(p.read_text() for p in sorted(pkg.rglob("*.py")))
    check("setSortingEnabled(True)" not in dlg_src,
          "click-to-sort is never enabled")
    check("setSortingEnabled(False)" in dlg_src, "sorting explicitly disabled")
    check("_on_header_section_clicked" in dlg_src, "header select-all handler present")
    check("set_check_state_and_count" in dlg_src, "header checkbox state/count still updated")
    check("_selectable_rows" in dlg_src, "select-all walks one shared set of selectable rows")
    check(dlg_src.count("_selectable_rows()") == 2,
          "the header tally and the ticking both go through that one walk")
    check("counts_toward_select_all" in dlg_src,
          "whether a row may carry a tick is the policy module's rule")
    check("def __lt__" not in (pkg / "items.py").read_text(),
          "item classes no longer implement __lt__")

    items_src = (pkg / "items.py").read_text()
    check("sort_value" in items_src,
          "sort_value kept - it carries the byte count for size totals")

    # --------------------------------------------------------- rules purity
    print("\n=== rules module has no Qt dependency ===")
    rules_src = (pkg / "rules.py").read_text()
    check("PyQt6" not in rules_src, "rules.py imports no Qt")
    rtree = ast.parse(rules_src)
    rfns = [n.name for n in rtree.body if isinstance(n, ast.FunctionDef)]
    rimports = [n for n in rtree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    check(len(rfns) >= 6, f"rules.py defines {len(rfns)} functions")
    check(not rimports, "rules.py has no imports at all - pure functions over a dict")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)} of {PASSED + len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"ALL DEPOT SELECTION TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())