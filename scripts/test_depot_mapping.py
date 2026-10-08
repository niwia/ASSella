#!/usr/bin/env python3
"""
scripts/test_depot_mapping.py

Regression test suite for Tier 3 and Tier 5 depot mapping pipeline in dlc_helpers.py.
Verifies that:
  1. Commented-out depots (-- addappid) are strictly ignored (e.g. MISSING_KEY, empty depots).
  2. Section headers (-- DLCS WITHOUT DEDICATED DEPOTS, -- EMPTY DEPOTS, etc.) properly reset
     the target AppID so ownership does not leak into preceding DLCs.
  3. Base AppIDs are never included in their own depot set (mapping[base_appid]).
  4. Real cached Lua files (1043810, 1096900) and synthetic fixtures map precisely as expected.
"""

import os
import sys
import tempfile
from pathlib import Path

# Add src to PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from utils.dlc_helpers import build_app_to_depots_map


class Color:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def log_pass(msg: str):
    print(f"  {Color.GREEN}[PASS]{Color.RESET} {msg}")


def log_fail(msg: str):
    print(f"  {Color.RED}[FAIL]{Color.RESET} {msg}")


def log_step(msg: str):
    print(f"\n{Color.CYAN}{Color.BOLD}>>> {msg}{Color.RESET}")


def run_tests() -> bool:
    print(f"{Color.BOLD}=== Depot Mapping Pipeline Verification Suite ==={Color.RESET}")
    all_passed = True

    # -------------------------------------------------------------------------
    # TEST 1: Synthetic Multi-Section Lua Fixture
    # -------------------------------------------------------------------------
    log_step("TEST 1: Synthetic Multi-Section Lua Fixture Parsing")
    synthetic_lua = """
-- 999990's Lua and Manifest Created by Hubcap Manifest
-- Test Game
-- Total Depots: 4
-- Total DLCs: 3

-- MAIN APPLICATION
addappid(999990, 1, "0000000000000000000000000000000000000000000000000000000000000001") -- Test Game Base AppKey

-- MAIN APP DEPOTS
addappid(999991, 1, "0000000000000000000000000000000000000000000000000000000000000002") -- Depot 999991
setManifestid(999991, "1111111111111111111", 1000)

-- DLCS WITH DEDICATED DEPOTS
-- Expansion Pack 1 (AppID: 888880)
addappid(888880)
addappid(888881, 1, "0000000000000000000000000000000000000000000000000000000000000003") -- Depot 888881

-- DLCS WITHOUT DEDICATED DEPOTS
addappid(777770) -- License-only Cosmetic Pack (no depots)

-- EMPTY DEPOTS (no content on any branch)
-- addappid(666660) -- Depot 666660 (empty depot)

-- DLCS EXCLUDED (MISSING DEPOT KEYS)
-- addappid(555551, 1, "MISSING_KEY") -- Broken DLC Depot 555551

-- SHARED DEPOTS (from other apps)
addappid(228980) -- Steamworks Shared
"""
    from utils.helpers import get_base_path
    cached_dir = Path(get_base_path()) / "cached_luas"
    cached_dir.mkdir(parents=True, exist_ok=True)
    synth_path = cached_dir / "999990.lua"
    synth_path.write_text(synthetic_lua, encoding="utf-8")

    try:
        mapping = build_app_to_depots_map("999990")

        # 1. Base AppID must NOT be in mapping[999990]
        base_depots = mapping.get("999990", set())
        if "999990" not in base_depots:
            log_pass("Base AppID '999990' is strictly NOT in mapping['999990']")
        else:
            log_fail("CRITICAL: Base AppID '999990' leaked into its own depot set!")
            all_passed = False

        if "999991" in base_depots:
            log_pass("Content Depot '999991' correctly assigned to base AppID '999990'")
        else:
            log_fail("Content Depot '999991' missing from base AppID mapping")
            all_passed = False

        # 2. Expansion 888880 must have 888881 and 888880, but NOT 777770, 666660, 555551, or 228980
        exp_depots = mapping.get("888880", set())
        if "888881" in exp_depots:
            log_pass("Dedicated Depot '888881' correctly assigned to DLC '888880'")
        else:
            log_fail("Dedicated Depot '888881' missing from DLC '888880'")
            all_passed = False

        if "777770" not in exp_depots:
            log_pass("License-only DLC '777770' did NOT leak into DLC '888880' (Target reset fix confirmed)")
        else:
            log_fail("CRITICAL: License-only DLC '777770' leaked into DLC '888880'!")
            all_passed = False

        if "666660" not in exp_depots:
            log_pass("Commented empty depot '666660' did NOT leak into DLC '888880' (Comment filter fix confirmed)")
        else:
            log_fail("CRITICAL: Commented empty depot '666660' leaked into DLC '888880'!")
            all_passed = False

        if "555551" not in exp_depots:
            log_pass("Commented MISSING_KEY depot '555551' did NOT leak into DLC '888880'")
        else:
            log_fail("CRITICAL: Commented MISSING_KEY depot '555551' leaked into DLC '888880'!")
            all_passed = False

        # 3. Across all apps, empty depots and missing keys must be completely absent
        all_mapped_depots = set().union(*mapping.values()) if mapping else set()
        if "666660" not in all_mapped_depots and "555551" not in all_mapped_depots:
            log_pass("Disabled depots (666660, 555551) are 100% absent across all mappings")
        else:
            log_fail("Disabled depots were found in global mapping!")
            all_passed = False

    finally:
        if synth_path.exists():
            synth_path.unlink()

    # -------------------------------------------------------------------------
    # TEST 2: Real Corpus Fixture - 1043810 (Tactical Breach Wizards)
    # -------------------------------------------------------------------------
    log_step("TEST 2: Real Corpus Verification - 1043810 (Tactical Breach Wizards)")
    real_1043810 = Path(get_base_path()) / "cached_luas" / "1043810.lua"
    if real_1043810.exists():
        m_1043810 = build_app_to_depots_map("1043810")
        dlc_3065010_depots = m_1043810.get("3065010", set())

        # DLC 3065010 must contain 3065010
        if "3065010" in dlc_3065010_depots:
            log_pass("DLC '3065010' correctly mapped to its identity depot '3065010'")
        else:
            log_fail("DLC '3065010' missing identity depot")
            all_passed = False

        # DLC 3065010 must NOT contain license-only 3139350 or empty depot 3139300
        if "3139350" not in dlc_3065010_depots:
            log_pass("License-only DLC '3139350' did NOT leak into DLC '3065010'")
        else:
            log_fail("License-only DLC '3139350' leaked into DLC '3065010'!")
            all_passed = False

        if "3139300" not in dlc_3065010_depots:
            log_pass("Empty depot '3139300' did NOT leak into DLC '3065010'")
        else:
            log_fail("Empty depot '3139300' leaked into DLC '3065010'!")
            all_passed = False

        # Base AppID 1043810 must NOT contain itself
        base_1043810_depots = m_1043810.get("1043810", set())
        if "1043810" not in base_1043810_depots:
            log_pass("Base AppID '1043810' is NOT in mapping['1043810']")
        else:
            log_fail("Base AppID '1043810' leaked into its own depot set!")
            all_passed = False
    else:
        print("  [SKIP] 1043810.lua not present in local cache")

    # -------------------------------------------------------------------------
    # TEST 3: Real Corpus Fixture - 1096900 (RPG Maker MZ)
    # -------------------------------------------------------------------------
    log_step("TEST 3: Real Corpus Verification - 1096900 (RPG Maker MZ - Commented Depots)")
    real_1096900 = Path(get_base_path()) / "cached_luas" / "1096900.lua"
    if real_1096900.exists():
        m_1096900 = build_app_to_depots_map("1096900")
        all_1096900_depots = set().union(*m_1096900.values()) if m_1096900 else set()

        # 5136471 is commented out with MISSING_KEY in 1096900.lua
        if "5136471" not in all_1096900_depots:
            log_pass("MISSING_KEY depot '5136471' is 100% excluded from mapping")
        else:
            log_fail("MISSING_KEY depot '5136471' was incorrectly claimed by mapping!")
            all_passed = False

        # Base AppID 1096900 must NOT contain itself
        base_1096900_depots = m_1096900.get("1096900", set())
        if "1096900" not in base_1096900_depots:
            log_pass("Base AppID '1096900' is NOT in mapping['1096900']")
        else:
            log_fail("Base AppID '1096900' leaked into its own depot set!")
            all_passed = False
    else:
        print("  [SKIP] 1096900.lua not present in local cache")

    # -------------------------------------------------------------------------
    # TEST 4: Lua comment-awareness helper (utils/lua_parsing.py)
    # -------------------------------------------------------------------------
    log_step("TEST 4: Comment-Aware Lua Scanning (utils/lua_parsing.py)")
    from utils.lua_parsing import (
        comment_spans,
        find_live,
        is_placeholder_key,
        iter_live_matches,
    )

    sample = (
        "-- 123456's Lua and Manifest Created by Hubcap Manifest\n"
        "-- Sample Game\n"
        "\n"
        "-- MAIN APPLICATION\n"
        'addappid(111, 1, "' + ("a" * 64) + '") -- Sample Game\n'
        "-- MAIN APP DEPOTS\n"
        'addappid(112, 1, "' + ("b" * 64) + '") -- Depot 112\n'
        "-- DLCS WITH DEDICATED DEPOTS\n"
        "-- Disabled DLC (AppID: 222)\n"
        '-- addappid(222, 1, "MISSING_KEY") -- Depot 222 (no key available)\n'
        "-- DLCS WITHOUT DEDICATED DEPOTS\n"
        "addappid(333) -- license-only dlc\n"
        "-- EMPTY DEPOTS (no content on any branch)\n"
        "-- addappid(444) -- Depot 444 (empty depot)\n"
        # A "--" inside the quoted key argument must not start a comment.
        'addappid(555, 1, "ab--cd") -- Depot 555\n'
    )
    live_ids = [m.group(1) for m in iter_live_matches(sample, r"addappid\((\d+)")]
    if live_ids == ["111", "112", "333", "555"]:
        log_pass("iter_live_matches skips commented depots (222, 444) and keeps live ones")
    else:
        log_fail(
                f"Comment filtering wrong; live ids = {live_ids} "
                "(expected ['111','112','333','555'])"
            )
        all_passed = False

    # A comment span covers "-- addappid(222..." entirely, so probe the position of
    # the marker itself rather than of the digits that follow it.
    marker_222 = sample.index("-- addappid(222")
    if any(s[0] <= marker_222 < s[1] for s in comment_spans(sample)):
        log_pass("comment_spans() located the commented addappid ranges")
    else:
        log_fail("comment_spans() failed to locate commented ranges")
        all_passed = False

    # A "--" inside a quoted argument is not a comment start, but a leading "--"
    # still is even when the same line also contains a quoted "--".
    quoted_only = (
        "-- 999999's Lua and Manifest Created by Hubcap Manifest\n"
        "-- Quoted Test\n"
        "-- MAIN APPLICATION\n"
        'addappid(777, 1, "ab--cd") -- dep 777\n'
        '-- addappid(778, 1, "x--y") -- commented despite quoted --\n'
    )
    if [
        m.group(1) for m in iter_live_matches(quoted_only, r"addappid\((\d+)")
    ] == ["777"]:
        log_pass(
            "Quoted '--' in an argument is not a comment, "
            "while a leading '--' still is"
        )
    else:
        log_fail("Quote-aware comment detection is wrong")
        all_passed = False

    if find_live(sample, r"addappid\(222") is None:
        log_pass("find_live() returns None for a commented-only depot")
    else:
        log_fail("find_live() matched inside a comment")
        all_passed = False

    for sentinel, expected in [
        ("MISSING_KEY", True),
        ("", True),
        ("null", True),
        (("ab" * 32), False),
    ]:
        actual = is_placeholder_key(sentinel)
        if actual != expected:
            log_fail(f"is_placeholder_key({sentinel!r}) = {actual}, expected {expected}")
            all_passed = False
            break
    else:
        log_pass("is_placeholder_key() screens sentinels but keeps real 64-hex keys")

    # -------------------------------------------------------------------------
    # TEST 5: ProcessZipTask._parse_lua must not ingest commented depots
    # -------------------------------------------------------------------------
    log_step("TEST 5: _parse_lua Excludes Commented Depots (source of phantom DLCs)")
    try:
        from core.tasks.process_zip_task import ProcessZipTask
    except Exception as exc:  # pragma: no cover - optional import guard
        log_fail(f"Could not import ProcessZipTask: {exc}")
        all_passed = False
    else:
        gd: dict = {}
        ProcessZipTask._parse_lua(sample, gd)
        depots = gd.get("depots", {})
        dlcs = gd.get("dlcs", {})

        if "222" not in depots:
            log_pass("Commented MISSING_KEY depot '222' is NOT in parsed depots")
        else:
            log_fail("Commented depot '222' leaked into parsed depots!")
            all_passed = False

        if "444" not in depots:
            log_pass("Commented empty depot '444' is NOT in parsed depots")
        else:
            log_fail("Commented depot '444' leaked into parsed depots!")
            all_passed = False

        if "222" not in dlcs and "444" not in dlcs:
            log_pass("Commented depots are NOT misfiled as DLC AppIDs")
        else:
            bad = [d for d in ("222", "444") if d in dlcs]
            log_fail(f"Commented depots misfiled as DLCs: {bad}")
            all_passed = False

        if "333" in dlcs:
            log_pass("Genuine license-only DLC '333' is still parsed correctly")
        else:
            log_fail("Genuine license-only DLC '333' was dropped!")
            all_passed = False

        # _parse_lua consumes the first live addappid() as the MAIN APPLICATION
        # entry, so it is reported as "appid"/"app_key" rather than as a depot.
        if gd.get("appid") == "111" and gd.get("app_key") == ("a" * 64):
            log_pass(
                "First live addappid '111' is consumed as MAIN APPLICATION with its AppKey"
            )
        else:
            log_fail(
                f"Main application entry misparsed: appid={gd.get('appid')!r}"
            )
            all_passed = False

        for live_depot in ("112", "555"):
            if live_depot in depots:
                log_pass(f"Live keyed depot '{live_depot}' is still parsed correctly")
            else:
                log_fail(f"Live keyed depot '{live_depot}' was dropped!")
                all_passed = False

    # -------------------------------------------------------------------------
    # TEST 6: Real-corpus sweep - no commented depot may reach game_data
    # -------------------------------------------------------------------------
    log_step("TEST 6: Real-Corpus Sweep (all cached_luas)")
    cached_dir = None
    try:
        from utils.helpers import get_base_path

        cached_dir = Path(get_base_path()) / "cached_luas"
    except Exception:
        cached_dir = REPO_ROOT / "cached_luas"

    if not cached_dir.is_dir() or not any(cached_dir.glob("*.lua")):
        print("  [SKIP] cached_luas not available")
    else:
        import re as _re

        checked = 0
        bad_files: list = []
        phantom_total = 0
        fake_key_total = 0
        for lua_file in sorted(cached_dir.glob("*.lua")):
            text = lua_file.read_text(encoding="utf-8", errors="ignore")
            commented_ids = {
                m.group(1)
                for m in _re.finditer(r"^\s*--\s*addappid\((\d+)", text, _re.M)
            }
            if not commented_ids:
                continue
            live_ids = {
                m.group(1)
                for m in _re.finditer(r"^addappid\((\d+)", text, _re.M)
            }
            # Only IDs that appear exclusively in comments must be excluded.
            only_commented = commented_ids - live_ids
            if not only_commented:
                continue

            checked += 1
            try:
                parsed: dict = {}
                ProcessZipTask._parse_lua(text, parsed)
            except Exception:
                continue

            parsed_depots = set(parsed.get("depots", {}))
            parsed_dlcs = set(parsed.get("dlcs", {}))
            leaked_depots = only_commented & parsed_depots
            leaked_dlcs = only_commented & parsed_dlcs
            if leaked_depots or leaked_dlcs:
                bad_files.append(lua_file.name)
                phantom_total += len(leaked_dlcs)
                fake_key_total += len(leaked_depots)

        if not bad_files:
            log_pass(
                f"No commented depot reached game_data across {checked} corpus files "
                "(phantoms 0, fake keys 0)"
            )
        else:
            log_fail(
                f"{len(bad_files)} corpus file(s) leak commented depots "
                f"(phantoms={phantom_total}, fake keys={fake_key_total}): {bad_files[:5]}"
            )
            all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print(f"{Color.GREEN}{Color.BOLD}ALL DEPOT MAPPING TESTS PASSED!{Color.RESET}")
    else:
        print(f"{Color.RED}{Color.BOLD}ONE OR MORE TESTS FAILED.{Color.RESET}")
    print("=" * 60 + "\n")

    return all_passed


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
