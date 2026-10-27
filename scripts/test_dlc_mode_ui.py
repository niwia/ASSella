#!/usr/bin/env python3
"""Test how DLC mode is shown to the user.

Two pieces are covered here:

* The mode marker is split off the display name, so the library card can draw
  it as a chip instead of the plain " [DLC MODE]" text that used to ride along
  inside the game name.
* The library card shows the mode as an accented chip.

    python3 scripts/test_dlc_mode_ui.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.gettempdir())

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
    from PyQt6.QtWidgets import QApplication

    from ui.dialogs.library.widgets import (
        DLC_MODE_SUFFIX,
        GameItemWidget,
        GameTileWidget,
        format_game_display_name,
        split_dlc_mode,
    )
    from utils.settings import get_settings

    # Keep the reference: a temporary QApplication is collected immediately and
    # Qt then refuses to construct widgets ("Must construct a QApplication").
    app = QApplication.instance() or QApplication([])
    settings = get_settings()
    probe_appid = "990001"

    # ------------------------------------------------------------------ marker
    print("=== the mode marker is split off the name ===")
    check(split_dlc_mode(f"Probe Game{DLC_MODE_SUFFIX}") == ("Probe Game", True),
          "marker detected and stripped")
    check(split_dlc_mode("Probe Game") == ("Probe Game", False),
          "a plain name is left alone")
    check(split_dlc_mode(DLC_MODE_SUFFIX.strip()) == (DLC_MODE_SUFFIX.strip(), False),
          "the bare marker is not a name suffix")
    check(split_dlc_mode(f"Probe {DLC_MODE_SUFFIX.strip()} Game") ==
          (f"Probe {DLC_MODE_SUFFIX.strip()} Game", False),
          "a marker in the middle does not count")

    # -------------------------------------------------------------------- card
    print("=== the library card shows an accented chip ===")
    settings.setValue(f"dlc_only_mode/{probe_appid}", True)
    try:
        display = format_game_display_name(
            {"appid": probe_appid, "game_name": "Probe Game"}
        )
        check(display == f"Probe Game{DLC_MODE_SUFFIX}",
              f"the shared name still carries the marker (got {display!r})")

        card = GameItemWidget(
            {"appid": probe_appid, "game_name": "Probe Game"},
            "1 GiB",
            "#a1c9fd",
            "#111318",
        )
        check(card.name_label.text() == "Probe Game",
              f"the marker is not part of the elided name (got {card.name_label.text()!r})")
        check(card.dlc_mode_badge is None, "GameItemWidget avoids duplicate title badge")
        check(card.status_label.text() == "[DLC Only]",
              f"the status label says [DLC Only] (got {card.status_label.text()!r})")
        check("#a1c9fd" in card.status_label.styleSheet(),
              "the status badge uses the accent colour")

        grid_card = GameTileWidget(
            {"appid": probe_appid, "game_name": "Probe Game"},
            "1 GiB",
            "#a1c9fd",
            "#111318",
        )
        check(grid_card.dlc_mode_badge is not None, "a DLC-mode tile card gets a badge")
        check("DLC" in (grid_card.dlc_mode_badge.text() if grid_card.dlc_mode_badge else ""),
              "the tile badge says DLC")
        check("#a1c9fd" in (grid_card.dlc_mode_badge.styleSheet() if grid_card.dlc_mode_badge else ""),
              "the tile badge uses the accent colour")
        grid_card.deleteLater()
        card.deleteLater()
    finally:
        settings.remove(f"dlc_only_mode/{probe_appid}")

    plain = GameItemWidget({"appid": "990002", "game_name": "Plain Game"},
                           "1 GiB", "#a1c9fd", "#111318")
    check(plain.status_label.text() != "[DLC Only]", "a normal card gets no DLC badge")
    check(plain.dlc_mode_badge is None, "a normal card gets no title badge")
    plain.deleteLater()

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
