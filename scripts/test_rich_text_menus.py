#!/usr/bin/env python3
"""Test the Rich-based CLI selection menus in ui/text_menus.py.

Verifies:
1. Depot sorting priorities (OS, language, depot ID).
2. Numbered selection parser (single numbers, ranges, comma-separated, all, none, q).
3. Single item auto-selection for depots and Steam libraries.
4. Menu execution with mocked console inputs for depots, DLCs, libraries, and paths.
5. Backward compatibility classes (DepotSelectionMenu, SteamLibraryMenu, etc.).

Run with:
    python3 scripts/test_rich_text_menus.py
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch
from io import StringIO

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rich.console import Console
from ui.text_menus import (
    select_depots,
    select_dlcs,
    select_steam_library,
    select_destination_path,
    select_branch,
    _parse_numeric_selection,
    _calculate_os_priority,
    _calculate_lang_priority,
    _sort_depots,
    _format_size,
    DepotSelectionMenu,
    DlcSelectionMenu,
    BranchSelectionMenu,
    SteamLibraryMenu,
    DestinationPathMenu,
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


def test_sorting_and_formatting():
    print("\n-- Depot Sorting and Formatting --")
    # OS priorities: Windows (1) < Linux (2) < All (3) < Unknown (4) < Mac (5)
    check(_calculate_os_priority("windows") == 1, "Windows OS priority is 1")
    check(_calculate_os_priority("linux") == 2, "Linux OS priority is 2")
    check(_calculate_os_priority("all") == 3, "All OS priority is 3")
    check(_calculate_os_priority(None) == 4, "None OS priority is 4")
    check(_calculate_os_priority("macos") == 5, "macOS priority is 5")

    # Language priorities
    check(_calculate_lang_priority("english", "english voice")[0] == 1, "English language priority is 1")
    check(_calculate_lang_priority("japanese", "japanese voice")[0] == 2, "Japanese language priority is 2")
    check(_calculate_lang_priority("german", "german voice")[0] == 3, "Other language priority is 3")

    # Size formatting
    check(_format_size(1073741824) == "1.00 GB", "1 GB formatted accurately")
    check(_format_size(0) == "-", "0 bytes returns '-'")
    check(_format_size(None) == "-", "None returns '-'")

    # Sort order
    depots = {
        "1003": {"desc": "Linux Binary", "oslist": "linux", "size": 100},
        "1001": {"desc": "Windows Main", "oslist": "windows", "size": 200},
        "1002": {"desc": "Mac Content", "oslist": "macos", "size": 50},
    }
    sorted_items = _sort_depots(depots)
    check([d[0] for d in sorted_items] == ["1001", "1003", "1002"], "Depots sorted Windows -> Linux -> Mac")


def test_numeric_selection_parser():
    print("\n-- Numeric Selection Parsing --")
    max_count = 5

    # 'all' variants
    check(_parse_numeric_selection("all", max_count) == [0, 1, 2, 3, 4], "Parse 'all'")
    check(_parse_numeric_selection("a", max_count) == [0, 1, 2, 3, 4], "Parse 'a'")
    check(_parse_numeric_selection("*", max_count) == [0, 1, 2, 3, 4], "Parse '*'")
    check(_parse_numeric_selection("", max_count, default_all=True) == [0, 1, 2, 3, 4], "Empty string with default_all=True")

    # 'none' variants
    check(_parse_numeric_selection("none", max_count) == [], "Parse 'none'")
    check(_parse_numeric_selection("0", max_count) == [], "Parse '0'")
    check(_parse_numeric_selection("n", max_count) == [], "Parse 'n'")

    # Cancel variants
    check(_parse_numeric_selection("q", max_count) is None, "Parse 'q' returns None")
    check(_parse_numeric_selection("quit", max_count) is None, "Parse 'quit' returns None")
    check(_parse_numeric_selection("cancel", max_count) is None, "Parse 'cancel' returns None")

    # Specific selections and ranges
    check(_parse_numeric_selection("1, 3, 5", max_count) == [0, 2, 4], "Parse comma-separated '1, 3, 5'")
    check(_parse_numeric_selection("1 2", max_count) == [0, 1], "Parse space-separated '1 2'")
    check(_parse_numeric_selection("2-4", max_count) == [1, 2, 3], "Parse range '2-4'")
    check(_parse_numeric_selection("4-2", max_count) == [1, 2, 3], "Parse reverse range '4-2'")
    check(_parse_numeric_selection("1-2, 5", max_count) == [0, 1, 4], "Parse mixed '1-2, 5'")

    # Out of range / invalid input raises ValueError
    threw = False
    try:
        _parse_numeric_selection("99", max_count)
    except ValueError:
        threw = True
    check(threw, "Out of range number raises ValueError")

    threw_invalid = False
    try:
        _parse_numeric_selection("foo", max_count)
    except ValueError:
        threw_invalid = True
    check(threw_invalid, "Invalid token raises ValueError")


def test_select_depots():
    print("\n-- Depot Selection Flow --")
    null_console = Console(file=StringIO())

    # 1. Single depot auto-skip
    single_depot = {"12345": {"desc": "Solo Depot", "size": 1000}}
    check(select_depots("123", "Test Game", single_depot, console=null_console) == ["12345"], "Single depot auto-selects without prompt")

    # 2. Multi-depot with mocked selection
    multi_depots = {
        "101": {"desc": "Depot One", "oslist": "windows"},
        "102": {"desc": "Depot Two", "oslist": "windows"},
        "103": {"desc": "Depot Three", "oslist": "windows"},
    }

    # Simulate entering "1, 3"
    with patch("rich.prompt.Prompt.ask", return_value="1, 3"):
        result = select_depots("123", "Test Game", multi_depots, console=null_console)
        check(result == ["101", "103"], "Interactive select returned items 1 and 3")

    # Simulate entering "q" (cancel)
    with patch("rich.prompt.Prompt.ask", return_value="q"):
        result = select_depots("123", "Test Game", multi_depots, console=null_console)
        check(result == [], "Cancelling with 'q' returns empty list")


def test_select_dlcs():
    print("\n-- DLC Selection Flow --")
    null_console = Console(file=StringIO())

    # Empty DLCs
    check(select_dlcs({}, console=null_console) == [], "Empty DLCs returns empty list")

    dlcs = {"201": "Expansion Pass", "202": "Soundtrack", "203": "Costume Pack"}
    with patch("rich.prompt.Prompt.ask", return_value="1-2"):
        result = select_dlcs(dlcs, console=null_console)
        check(result == ["201", "202"], "DLC range '1-2' selects first two DLCs")


def test_select_steam_library():
    print("\n-- Steam Library Selection Flow --")
    null_console = Console(file=StringIO())

    # Single library auto-skip
    check(select_steam_library(["/home/user/.steam/steamapps"], console=null_console) == "/home/user/.steam/steamapps", "Single library auto-selects")

    # Multiple libraries
    libs = ["/mnt/games", "/mnt/fast_nvme"]
    with patch("rich.prompt.Prompt.ask", return_value="2"):
        result = select_steam_library(libs, console=null_console)
        check(result == "/mnt/fast_nvme", "Selected library 2 returns /mnt/fast_nvme")

    with patch("rich.prompt.Prompt.ask", return_value="q"):
        result = select_steam_library(libs, console=null_console)
        check(result is None, "Cancelling library selection returns None")


def test_select_destination_path():
    print("\n-- Destination Path Selection Flow --")
    null_console = Console(file=StringIO())

    # Default acceptance
    with patch("rich.prompt.Prompt.ask", return_value="/default/path"):
        check(select_destination_path("/default/path", console=null_console) == "/default/path", "Default path accepted")

    # Custom path
    with patch("rich.prompt.Prompt.ask", return_value="/custom/game/dir"):
        check(select_destination_path("/default/path", console=null_console) == "/custom/game/dir", "Custom path accepted")

    # Cancel
    with patch("rich.prompt.Prompt.ask", return_value="q"):
        check(select_destination_path("/default/path", console=null_console) is None, "Cancelled path returns None")


def test_select_branch():
    print("\n-- Branch Selection Flow --")
    null_console = Console(file=StringIO())

    # Single branch auto-selects
    check(select_branch("123", {"public": {}}, console=null_console) == "public", "Single branch auto-selects")

    branches = {
        "public": {"buildid": "1000", "description": "Release"},
        "beta": {"buildid": "2000", "description": "Preview"},
        "experimental": {"buildid": "3000", "description": "Testing"},
    }

    # Interactive select by number
    with patch("rich.prompt.Prompt.ask", return_value="2"):
        res = select_branch("123", branches, console=null_console)
        check(res == "beta", "Selected branch 2 returns 'beta'")

    # Interactive select by name
    with patch("rich.prompt.Prompt.ask", return_value="experimental"):
        res = select_branch("123", branches, console=null_console)
        check(res == "experimental", "Selected branch by name returns 'experimental'")

    # Cancel
    with patch("rich.prompt.Prompt.ask", return_value="q"):
        res = select_branch("123", branches, console=null_console)
        check(res is None, "Cancelling branch selection returns None")


def test_recommended_depots_and_smart_defaults():
    print("\n-- Smart Depot Recommendations & Defaults --")
    null_console = Console(file=StringIO())

    depots = {
        "101": {"desc": "Game Content Win", "oslist": "windows", "size": 1000},
        "102": {"desc": "Game Content Linux", "oslist": "linux", "size": 1000},
        "103": {"desc": "Bonus OST", "oslist": "all", "size": 500},
    }

    # Empty input with default_indices returns recommended indices
    parsed = _parse_numeric_selection("", 3, default_all=True, default_indices=[0, 1])
    check(parsed == [0, 1], "Empty string returns default_indices")

    # 'auto' or 'smart' returns default_indices
    parsed_auto = _parse_numeric_selection("smart", 3, default_all=True, default_indices=[1])
    check(parsed_auto == [1], "'smart' keyword returns default_indices")

    # Interactive select_depots with recommendations accepts recommended on empty Enter
    with patch("rich.prompt.Prompt.ask", return_value=""):
        res = select_depots("123", "Test Game", depots, console=null_console, recommended=["101"])
        check(res == ["101"], "Pressing Enter accepts smart-recommended depots")


def test_backward_compatibility_classes():
    print("\n-- Backward Compatibility Classes --")
    null_console = Console(file=StringIO())

    menu = DepotSelectionMenu("123", "Test Game", {"555": {"desc": "Solo"}})
    check(menu.run() == ["555"], "DepotSelectionMenu.run() works")

    branch_menu = BranchSelectionMenu("123", {"public": {}})
    check(branch_menu.run() == "public", "BranchSelectionMenu.run() works")

    lib_menu = SteamLibraryMenu(["/steam/lib"])
    check(lib_menu.run() == "/steam/lib", "SteamLibraryMenu.run() works")


def main():
    test_sorting_and_formatting()
    test_numeric_selection_parser()
    test_select_depots()
    test_select_dlcs()
    test_select_steam_library()
    test_select_destination_path()
    test_select_branch()
    test_recommended_depots_and_smart_defaults()
    test_backward_compatibility_classes()

    print(f"\nResult: {PASSED} checks passed, {len(_failures)} failures.")
    if _failures:
        print("Failures:")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
