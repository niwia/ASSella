#!/usr/bin/env python3
"""Test the DLC-only affordances the library and selector show.

Three behaviours keep DLC-only mode honest:

* a DLC whose depots carry no reported size still counts as downloadable, so
  the "nothing to download" warning never fires for a game that has content;
* the selector opens on the Unowned chip when every owned row is unpickable;
* Steam-tab cards without DLC mode are muted, so catalogue entries do not read
  as managed installs.

Hermetic: QSettings and caches are redirected to a temp dir, DatabaseManager is
stubbed, and no background network work starts.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_dlc_only_ux.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="assella-dlcuxtest-")
os.environ["XDG_CONFIG_HOME"] = _TMP
os.environ["XDG_DATA_HOME"] = _TMP
os.environ["XDG_CACHE_HOME"] = _TMP
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
    """Report one known library so the storage branch is deterministic."""
    try:
        import core.steam_helpers as sh
        import utils.paths as paths
    except Exception:
        return
    lib = Path(_TMP) / "SteamLibrary"
    lib.mkdir(parents=True, exist_ok=True)
    sh.get_steam_libraries = lambda: [str(lib)]
    sh.find_steam_install = lambda *a, **kw: str(lib)
    _real_valid = paths.is_valid_download_directory
    paths.is_valid_download_directory = lambda p: (
        _real_valid(p) or (bool(p) and str(lib) in str(p))
    )


class _SteamTabParent:
    """Minimal parent the card reads its active tab from."""

    _active_tab = "steam"


def main() -> int:
    from PyQt6.QtGui import QColor, QPixmap
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841 - keeps Qt alive

    from utils.dlc_helpers import DlcCandidate, DlcDiscovery

    print("=== an unreported size is not a depotless DLC ===")
    zero = DlcDiscovery(
        "100",
        {"200": "Pack A", "201": "Pack B"},
        (
            DlcCandidate("300", "200", "Pack A", 0, ("windows",)),
            DlcCandidate("301", "201", "Pack B", 0, ("windows",)),
        ),
        ("memory",),
    )
    check(zero.has_sized_depots is True, "all-zero sizes still count as downloadable")
    check(zero.is_config_only is False, "so the game is not treated as config-only")

    stubs = DlcDiscovery(
        "100",
        {"200": "Pack A"},
        (DlcCandidate("300", "200", "Pack A", 512, ("windows",)),),
        ("memory",),
    )
    check(stubs.has_sized_depots is False, "a known tiny stub is still depotless")

    empty = DlcDiscovery("100", {"200": "Pack A"}, (), ("memory",))
    check(empty.has_sized_depots is False, "no depots at all stays depotless")

    print("\n=== DLC mode opens the selector on Unowned ===")
    from ui.dialogs.depot_selection.dialog import DepotSelectionDialog

    _stub_db_manager()
    _stub_steam_libraries()

    depots = {
        "300": {"desc": "[DLC] Pack A", "dlcappid": "200", "size": 4096, "oslist": "windows"},
        "301": {"desc": "[DLC] Pack B", "dlcappid": "201", "size": 0, "oslist": "windows"},
    }
    dlc_dlg = DepotSelectionDialog(
        "100", "Probe Game", depots, header_url="", dlc_only_mode=True, lock_dlc_only=True,
    )
    check(dlc_dlg._active_filter == "missing", "a DLC-mode selector opens on the Unowned chip")
    check(dlc_dlg.filter_chips["missing"].isChecked(), "the Unowned chip is the one marked active")

    plain_dlg = DepotSelectionDialog("100", "Probe Game", depots, header_url="")
    check(getattr(plain_dlg, "_active_filter", "all") == "all", "a normal selector opens on All")
    check(not getattr(plain_dlg, "filter_chips", {}), "a normal selector has no filter chips")

    print("\n=== Steam-tab cards are muted without DLC mode ===")
    from ui.dialogs.library.widgets import GameItemWidget

    colour = QPixmap(64, 64)
    colour.fill(QColor("#c0392b"))

    card = GameItemWidget(
        {"appid": "999999", "game_name": "Plain Steam"},
        "1 GB",
        "#c06c84",
        "#111318",
        parent_dialog=_SteamTabParent(),
    )
    card.set_image(colour)
    grey = card.image_label.pixmap().toImage().pixelColor(10, 10)
    check(
        grey.red() == grey.green() == grey.blue(),
        f"a Steam-tab card without DLC mode is grey ({grey.red()},{grey.green()},{grey.blue()})",
    )

    tracked = GameItemWidget(
        {"appid": "999999", "game_name": "Tracked", "is_dlc_only": True},
        "1 GB",
        "#c06c84",
        "#111318",
        parent_dialog=_SteamTabParent(),
    )
    tracked.set_image(colour)
    kept = tracked.image_label.pixmap().toImage().pixelColor(10, 10)
    check(kept.red() > kept.green() and kept.red() > kept.blue(),
          "a DLC-mode card keeps its colour")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)} of {len(_failures) + PASSED}):")
        for item in _failures:
            print(f"  - {item}")
        return 1
    print(f"All {PASSED} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
