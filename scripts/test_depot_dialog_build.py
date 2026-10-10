#!/usr/bin/env python3
"""Construct DepotSelectionDialog for real and assert its widget tree is intact.

The 509-line __init__ was split into focused builder methods. This builds the
dialog headlessly and checks the result, so a builder that forgets to wire a
widget, or reorders one that depends on another, fails here instead of in the
AppImage.

Hermetic: QSettings is redirected to a temp dir, DatabaseManager is stubbed,
and depots are shaped so no background enrichment or network work starts.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_depot_dialog_build.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

# Redirect QSettings before Qt is imported so the test cannot touch real config.
_TMP = tempfile.mkdtemp(prefix="assella-dlgtest-")
os.environ["XDG_CONFIG_HOME"] = _TMP
os.environ["XDG_DATA_HOME"] = _TMP
os.environ["XDG_CACHE_HOME"] = _TMP

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


def _stub_db_manager() -> None:
    """Replace DatabaseManager so enrichment preloading touches no real database."""
    if "managers.db_manager" in sys.modules:
        return

    class _Conn:
        def cursor(self):
            raise RuntimeError("stub")

    class _FakeDatabaseManager:
        def __init__(self, *a, **kw):
            self.conn = _Conn()

        def get_depot_enrichments(self, app_id):
            return {}

    mod = types.ModuleType("managers.db_manager")
    mod.DatabaseManager = _FakeDatabaseManager
    sys.modules["managers.db_manager"] = mod


def _stub_steam_libraries() -> None:
    """Make Steam library discovery deterministic.

    Without this the storage section reflects whatever Steam libraries the
    machine running the test happens to have, which differs between a laptop
    and a CI runner. One known-good path is reported so the storage branch is
    actually exercised rather than skipped.
    """
    try:
        import core.steam_helpers as sh
        import utils.paths as paths
    except Exception:
        return
    lib = Path(_TMP) / "SteamLibrary"
    lib.mkdir(parents=True, exist_ok=True)
    sh.get_steam_libraries = lambda: [str(lib)]
    sh.find_steam_install = lambda *a, **kw: str(lib)

    # is_valid_download_directory rejects anything under /tmp by design, so the
    # temp library just made needs that one predicate relaxed to be usable.
    _real_valid = paths.is_valid_download_directory
    paths.is_valid_download_directory = lambda p: (
        _real_valid(p) or (bool(p) and str(lib) in str(p))
    )


def make_depots(n: int) -> dict:
    """Plain depots that no visibility filter should ever remove."""
    return {
        str(1000 + i): {
            "name": f"Content {i}",
            "desc": "Game Content",
            "size": 1024 * (i + 1),
            "oslist": "all",
            "maxsize": 1024 * (i + 1),
        }
        for i in range(n)
    }


def find_buttons(widget, text: str) -> list:
    """Every button whose text contains `text`, searched depth-first."""
    from PyQt6.QtWidgets import QPushButton

    out, stack = [], [widget]
    while stack:
        w = stack.pop()
        if isinstance(w, QPushButton) and text.lower() in w.text().lower():
            out.append(w)
        stack.extend(w.children())
    return out


def main() -> int:
    from PyQt6.QtWidgets import QApplication, QTableWidget

    app = QApplication.instance() or QApplication([])

    from ui.dialogs.depot_selection.dialog import DepotSelectionDialog
    from ui.dialogs.depot_selection.views import DepotHeaderView

    _stub_db_manager()
    _stub_steam_libraries()

    # ------------------------------------------------ multi-depot, full chrome
    print("\n=== multi-depot app builds its whole tree ===")
    depots = make_depots(5)
    dlg = DepotSelectionDialog(
        "2185060",
        "Two Point Museum",
        depots,
        header_url="",
        selected_depots=["1000", "1001"],
        show_storage=True,
        is_single_depot=False,
        branch="public",
        branches={"public": {}, "public_beta": {}},
        current_build_id="25799969",
    )

    check(isinstance(dlg.table_widget, QTableWidget), "table_widget created")
    check(dlg.table_widget.columnCount() == 3, "three columns")
    labels = [dlg.table_widget.horizontalHeaderItem(i).text()
              for i in range(dlg.table_widget.columnCount())]
    # Column 0 is deliberately blank: DepotHeaderView draws the select-all
    # checkbox there. Column 2 carries a live byte total.
    check(labels[0] == "", f"column 0 left blank for the header checkbox ({labels[0]!r})")
    check(labels[1] == "Configuration", f"column 1 is Configuration ({labels[1]!r})")
    check(labels[2].startswith("Size"), f"column 2 is the size column ({labels[2]!r})")
    check(isinstance(dlg.header_view, DepotHeaderView), "custom header view installed")
    check(dlg.table_widget.rowCount() == 5, f"all 5 depots listed (got {dlg.table_widget.rowCount()})")

    check(dlg.title_label is not None, "title label created")
    check("Two Point Museum" in dlg.title_label.text(), "game name on the title label")
    check(dlg.builds_btn is not None, "Build button created")
    check("25799969" in dlg.builds_btn.text(), "current build id on the Build button")

    check(bool(find_buttons(dlg, "OK")), "OK button present")
    check(bool(find_buttons(dlg, "Cancel")), "Cancel button present")

    check(dlg.title_bar is not None, "titlebar created")
    check(dlg.anchor_row == -1, "anchor_row initialised")

    # state that the builders depend on
    check(dlg._has_saved_selection is True, "preselected depots recorded as a saved selection")
    check(dlg._dlc_only_mode is False, "dlc_only_mode loaded as off")
    check(dlg._user_interacted is False, "user interaction flag starts False")
    check(dlg.current_build_id == "25799969", "current_build_id stored")
    check(dlg.selected_depots == ["1000", "1001"], "selected_depots stored")

    # accent colour resolution must survive the extraction
    from utils.color_utils import get_dark_container_color
    check(isinstance(dlg.accent_color, str) and dlg.accent_color.startswith("#"),
          f"accent colour resolved ({dlg.accent_color})")
    check(get_dark_container_color(dlg.accent_color).startswith("#"),
          "accent colour is parseable by color_utils")

    # the stylesheet that consumes the resolved RGB triple must still be applied
    sheet = dlg.table_widget.styleSheet()
    check("QTableWidget" in sheet, "table stylesheet applied")
    hexc = dlg.accent_color.lstrip("#")
    expected_rgb = f"{int(hexc[0:2], 16)}, {int(hexc[2:4], 16)}, {int(hexc[4:6], 16)}"
    check(expected_rgb in sheet,
          "table stylesheet used the RGB triple returned by _resolve_accent_colors")

    print("\n=== branch selector appears when the app has extra branches ===")
    combo = getattr(dlg, "branch_combo", None)
    check(combo is not None, "branch dropdown created for a multi-branch app")
    if combo is not None:
        items = [combo.itemText(i) for i in range(combo.count())]
        check("public" in items, "public branch offered")
        check("public_beta" in items, "beta branch offered")
        check(items[0] == "public", "public listed first")

    # ------------------------------------------------ single depot
    print("\n=== a single-depot app routes to the single-depot dialog ===")
    from ui.dialogs.single_depot_dialog import SingleDepotSelectionDialog

    dlg2 = DepotSelectionDialog(
        "730", "Single Game", make_depots(1),
        show_storage=True, is_single_depot=True,
        branch="public", branches={"public": {}},
    )
    check(isinstance(dlg2, SingleDepotSelectionDialog),
          "one depot and no missing depots selects the single-depot dialog")
    check(bool(find_buttons(dlg2, "OK")), "OK button present on the single-depot dialog")
    dlg2.close()

    # a missing Hubcap depot must keep the full dialog even for one depot
    dlg2b = DepotSelectionDialog(
        "730", "Single Game", make_depots(1),
        missing_hubcap_depots={"1000": "no key"},
        show_storage=True, is_single_depot=True,
        branch="public", branches={"public": {}},
    )
    check(isinstance(dlg2b, DepotSelectionDialog),
          "a missing depot keeps the full dialog so the user can see it")
    dlg2b.close()

    # ------------------------------------------------ storage hidden
    print("\n=== show_storage=False omits the storage picker ===")
    dlg3 = DepotSelectionDialog(
        "400", "No Storage", make_depots(3),
        show_storage=False, is_single_depot=False,
        branch="public", branches={"public": {}},
    )
    check(dlg3.table_widget.rowCount() == 3, "depots listed with storage hidden")
    # The storage widgets are only built when show_storage is set, and the rest
    # of the class guards on show_storage/hasattr accordingly.
    check(not hasattr(dlg3, "_storage_buttons"), "no storage buttons built when storage is hidden")
    check(dlg3.get_selected_storage() is None, "no storage path selected when storage is hidden")
    check(bool(find_buttons(dlg3, "OK")), "OK button still present")
    dlg3.close()

    # ------------------------------------------------ storage shown
    print("\n=== show_storage=True adds the storage picker ===")
    dlg4 = DepotSelectionDialog(
        "500", "With Storage", make_depots(3),
        show_storage=True, is_single_depot=False,
        library_path="/tmp/assella-test-library",
        branch="public", branches={"public": {}},
    )
    check(isinstance(dlg4._storage_buttons, dict), "storage button map built")
    check(len(dlg4._storage_buttons) >= 1, "at least one storage button offered")
    check(str(Path(_TMP) / "SteamLibrary") in dlg4._storage_paths,
          f"the stubbed library is listed ({dlg4._storage_paths})")
    check(dlg4._more_storage_combo is None or dlg4._more_storage_combo.count() >= 1,
          "More Drives combo either absent or populated")
    check(dlg4.preferred_library_path == "/tmp/assella-test-library",
          "preferred library path stored")
    check(isinstance(dlg4.get_selected_storage(), (str, type(None))),
          "get_selected_storage returns a path or None")
    dlg4.close()

    # ------------------------------------------------ missing hubcap depots
    print("\n=== missing Hubcap depots surface a banner ===")
    dlg5 = DepotSelectionDialog(
        "2185060", "Two Point Museum", make_depots(5),
        missing_hubcap_depots={"1004": "no key"},
        missing_depots_info={"1004": {"name": "Missing DLC"}},
        show_storage=True, is_single_depot=False,
        branch="public", branches={"public": {}},
    )
    check(dlg5.missing_hubcap_depots == ["1004"], "missing depot ids recorded as a list")
    check(dlg5.missing_depots_info.get("1004", {}).get("name") == "Missing DLC",
          "supplied missing depot metadata preserved")
    check(dlg5.table_widget.rowCount() == 6,
          f"the missing depot is listed too (got {dlg5.table_widget.rowCount()} rows)")
    dlg5.close()

    # ------------------------------------------- missing depots, no metadata
    print("\n=== missing depots without metadata must not crash ===")
    # Regression: missing_depots_info used to be aliased to missing_hubcap_depots,
    # whose values are reason strings. Every consumer calls .get() on them, so
    # the dialog died with AttributeError on the first row.
    dlg6 = DepotSelectionDialog(
        "2185060", "Two Point Museum", make_depots(5),
        missing_hubcap_depots={"1004": "no key"},
        show_storage=True, is_single_depot=False,
        branch="public", branches={"public": {}},
    )
    check("1004" in dlg6.missing_depots_info, "missing depot has a metadata entry")
    check(all(isinstance(v, dict) for v in dlg6.missing_depots_info.values()),
          "every missing_depots_info value is a dict, never a reason string")
    check(dlg6.table_widget.rowCount() == 6, "missing depot still rendered")
    dlg6.close()

    # ----------------------------- hostile input: dict values that are not dicts
    print("\n=== non-dict metadata is normalised rather than trusted ===")
    dlg7 = DepotSelectionDialog(
        "2185060", "Two Point Museum", make_depots(5),
        missing_hubcap_depots={"1004": "no key"},
        missing_depots_info={"1004": "also a string"},
        show_storage=True, is_single_depot=False,
        branch="public", branches={"public": {}},
    )
    check(all(isinstance(v, dict) for v in dlg7.missing_depots_info.values()),
          "string metadata coerced to a dict")
    dlg7.close()

    dlg.close()
    app.processEvents()

    print()
    if _failures:
        print(f"FAILED ({len(_failures)} of {PASSED + len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"ALL DEPOT DIALOG BUILD TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
