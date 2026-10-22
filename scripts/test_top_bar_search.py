#!/usr/bin/env python3
"""Hermetic tests for the main-window game search.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_top_bar_search.py
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMainWindow

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core import morrenus_api
from ui.bottom_titlebar import BottomTitleBar

_failures = []
PASSED = 0


def check(cond, label):
    global PASSED
    if cond:
        PASSED += 1
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        _failures.append(label)


def main():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.resize(900, 600)
    window.game_manager = SimpleNamespace(
        games={"10": {"appid": "10", "game_name": "Local Game"}},
        get_game=lambda app_id: {"appid": app_id} if app_id == "10" else None,
    )
    window.fetch_requests = []
    window.open_fetch_dialog = lambda initial_query="": window.fetch_requests.append(initial_query)
    titlebar = BottomTitleBar(window)
    titlebar.setGeometry(0, 0, 900, 32)
    search = titlebar.search_controller

    print("=== search field and dropdown belong to the main window ===")
    check(search.input.parentWidget() is titlebar, "search field is embedded in the actual title bar")
    check(search.panel.parentWidget() is window, "results panel is a main-window child")
    search._position_panel()
    check(search.panel.x() >= 0 and search.panel.y() > 0, "results panel positions below its field")
    check(search.panel.width() == search.input.width(), "results panel width strictly matches the search bar width")

    print("=== results panel stays inside a bottom-docked title bar ===")
    titlebar.setGeometry(0, window.height() - 32, window.width(), 32)
    app.processEvents()
    field_top = search.input.mapTo(window, search.input.rect().topLeft()).y()
    search._position_panel()
    check(
        search.panel.y() + search.panel.height() <= window.height(),
        "results panel is not clipped when the title bar sits at the bottom (the default)",
    )
    check(
        search.panel.y() + search.panel.height() <= field_top,
        "results panel opens upwards above the field when there is no room below",
    )
    titlebar.setGeometry(0, 0, 900, 32)
    app.processEvents()

    print("=== API and local library entries share the existing ranking policy ===")
    old_search = morrenus_api.search_games
    morrenus_api.search_games = lambda query: {
        "results": [
            {"appid": "20", "name": "Local Game Deluxe"},
            {"appid": "30", "name": "Other Game"},
        ],
        "raw_total": 2,
    }
    try:
        result = search._search_games(
            "Local Game", [{"appid": "10", "game_name": "Local Game"}]
        )
    finally:
        morrenus_api.search_games = old_search
    ids = [game.get("appid") for game in result["results"]]
    check("10" in ids, "local game data comes from game_manager.games snapshot")
    check("20" in ids, "API suggestions are included")
    check("raw_total" in result, "ranking returns the established search result shape")

    print("=== selecting a result hands off to the existing fetch workflow ===")
    from PyQt6.QtWidgets import QListWidgetItem
    item = QListWidgetItem("Local Game")
    item.setData(Qt.ItemDataRole.UserRole, ("10", "Local Game"))
    search._on_item_clicked(item)
    check(window.fetch_requests == ["10"], "selected AppID starts the existing fetch path")

    from utils.settings import get_settings
    settings = get_settings()

    print("=== title bar search optional toggle test ===")
    window.show()
    # 1. When toggle is disabled (default): title label and classic search button visible, widget hidden
    settings.remove("title_bar_search_enabled")
    titlebar.refresh_search_mode()
    check(titlebar.search_controller.input.isHidden(), "search input is hidden when the setting is unset (the default)")
    check(not titlebar.title_label.isHidden(), "ASSella title label is visible by default")
    check(not titlebar.search_button.isHidden(), "Search button is visible by default")

    # 2. When toggle is disabled: title label and classic search button visible, widget hidden
    settings.setValue("title_bar_search_enabled", False)
    titlebar.refresh_search_mode()
    check(titlebar.search_controller.input.isHidden(), "search input is hidden when title_bar_search_enabled is False")
    check(not titlebar.title_label.isHidden(), "ASSella title label is visible when title_bar_search_enabled is False")
    check(not titlebar.search_button.isHidden(), "Search button is visible when title_bar_search_enabled is False")

    # 3. When toggle is enabled: search widget visible, title label and search button hidden
    settings.setValue("title_bar_search_enabled", True)
    titlebar.refresh_search_mode()
    check(not titlebar.search_controller.input.isHidden(), "search input is visible when title_bar_search_enabled is True")
    check(titlebar.title_label.isHidden(), "ASSella title label is hidden when title_bar_search_enabled is True")
    check(titlebar.search_button.isHidden(), "Search button is hidden when title_bar_search_enabled is True")

    # Leave the developer's setting unset so the next run starts from the default.
    settings.remove("title_bar_search_enabled")
    titlebar.refresh_search_mode()

    main_source = (SRC_DIR / "ui" / "main_window.py").read_text()
    check("def open_fetch_dialog(self, initial_query: str = \"\")" in main_source,
          "fetch workflow accepts a selected AppID")

    generation = search.generation
    search.close()
    check(search.generation == generation + 1, "closing invalidates any outstanding search response")
    window.close()
    del app
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {', '.join(_failures)}")
        return 1
    print(f"ALL TOP BAR SEARCH TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
