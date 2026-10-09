#!/usr/bin/env python3
"""
scripts/test_atom_config_sync.py

Comprehensive verification test suite for AT0-M mode configuration injection,
Lua parsing, DecryptionKeys root AppKey allowance, AdditionalDepots protection,
and Depots Tab unlocked toggle behavior.

This test suite can be run at any time (especially after builds) to verify that:
  1. Base AppID is added to AdditionalApps.
  2. Base AppID is NEVER added to AdditionalDepots.
  3. Any valid DecryptionKeys (including root AppKey and depot keys) are added 100% of the time.
  4. Users have full freedom to toggle keys in the Depots tab without self-sanitation resetting them.
  5. All changes made during the test are 100% reverted at the end.

Usage:
  python3 scripts/test_atom_config_sync.py
"""

import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Ensure src/ is on PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from utils.helpers import get_base_path
from utils.yaml_config_manager import (
    get_user_config_path,
    batch_config_edit,
    get_additional_apps,
    get_additional_depots,
    get_decryption_keys,
    is_depot_shared_with_other_games,
    _get_section_bounds,
    _validate_yaml_content,
    _atomic_write,
)
from utils.plugin_games import (
    register_plugin_game,
    unregister_plugin_game,
    load_plugin_library,
    save_plugin_library,
)


class Color:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def log_step(msg: str):
    print(f"\n{Color.CYAN}{Color.BOLD}>>> {msg}{Color.RESET}")


def log_pass(msg: str):
    print(f"  {Color.GREEN}[PASS]{Color.RESET} {msg}")


def log_fail(msg: str):
    print(f"  {Color.RED}[FAIL]{Color.RESET} {msg}")


def log_info(msg: str):
    print(f"  {Color.YELLOW}[INFO]{Color.RESET} {msg}")


def parse_lua_file(lua_text: str) -> Tuple[str, Dict[str, str], List[str]]:
    """Parse real Lua text to extract real AppID, depot keys, and depot list."""
    real_appid = ""
    # Find base appid: addappid(<appid>) without extra params or addappid(<appid>, 1, ...)
    app_re = re.compile(r"addappid\((\d+)\s*(?:--.*)?\)")
    m_app = app_re.search(lua_text)
    if m_app:
        real_appid = m_app.group(1)

    # Depot keys: addappid(<depot_id>, 1, "<key>")
    depot_key_re = re.compile(r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)')
    depot_keys = {}
    depot_ids = []
    for match in depot_key_re.finditer(lua_text):
        did = match.group(1)
        k = match.group(2)
        depot_keys[did] = k
        if did != real_appid:
            depot_ids.append(did)

    if not real_appid and depot_keys:
        real_appid = list(depot_keys.keys())[0]

    return real_appid, depot_keys, depot_ids


def obtain_4959210_lua() -> Tuple[str, str]:
    """Find or download Lua for test game 4959210 (Cubing Forge)."""
    accela_home = get_base_path()
    cached_lua = accela_home / "cached_luas" / "4959210.lua"
    if cached_lua.exists():
        return "Cubing Forge", cached_lua.read_text(encoding="utf-8")

    zip_fetch = accela_home / "hubcap_manifests" / "accela_fetch_4959210.zip"
    if zip_fetch.exists():
        with zipfile.ZipFile(zip_fetch, "r") as zf:
            for name in zf.namelist():
                if name.endswith("4959210.lua") or name.endswith(".lua"):
                    return "Cubing Forge", zf.read(name).decode("utf-8", errors="ignore")

    # Download from API if not locally present
    try:
        from core import morrenus_api
        zpath, err = morrenus_api.download_manifest("4959210", force_update=True)
        if zpath and os.path.exists(zpath):
            with zipfile.ZipFile(zpath, "r") as zf:
                for name in zf.namelist():
                    if name.endswith(".lua"):
                        return "Cubing Forge", zf.read(name).decode("utf-8", errors="ignore")
    except Exception as e:
        log_info(f"Could not download manifest from API: {e}")

    # Fallback minimal real Lua representation for 4959210
    fallback_lua = (
        "-- 4959210's Lua and Manifest Created by Hubcap Manifest\n"
        "-- Cubing Forge\n"
        "addappid(4959210) -- Cubing Forge\n"
        'addappid(4959211, 1, "6c24912e27c2bd8635557167782ecd55aea9b0134bbb8a63b6c3de4ab6dc0729") -- Depot 4959211\n'
        'addappid(4959212, 1, "82b31866e40ffa2571b6dab2a28a96ed9e8c2c717104b6116b22c44ffccbce78") -- Depot 4959212\n'
    )
    return "Cubing Forge", fallback_lua


def run_tests() -> bool:
    cfg_path = get_user_config_path()
    cfg_was_created = False
    if not cfg_path.exists():
        log_info(f"SLSsteam config not found at {cfg_path} - seeding hermetic minimal config for test")
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        minimal_config = (
            "DisableFamilyShareLock: yes\n"
            "UseWhitelist: no\n"
            "AppIds:\n"
            "AdditionalApps:\n"
            "AdditionalDepots:\n"
            "DecryptionKeys:\n"
        )
        cfg_path.write_text(minimal_config, encoding="utf-8")
        cfg_was_created = True

    orig_cfg_bytes = cfg_path.read_bytes()
    orig_inode = cfg_path.stat().st_ino
    orig_plugin_lib = load_plugin_library()

    test_passed = True
    added_test_appids = []

    try:
        print(f"{Color.BOLD}=== ASSella AT0-M & DecryptionKeys Invariants Test Suite ==={Color.RESET}")
        log_info(f"Config path: {cfg_path} (inode: {orig_inode})")

        # ---------------------------------------------------------------------
        # TEST 1: Normal Game Registration via Lua (Game 4959210 - Cubing Forge)
        # ---------------------------------------------------------------------
        log_step("TEST 1: Register game 4959210 (Cubing Forge) to AT0-M mode")
        game_name, lua_content = obtain_4959210_lua()
        appid_1, keys_1, depots_1 = parse_lua_file(lua_content)
        assert appid_1 == "4959210", f"Expected appid 4959210, got {appid_1}"

        log_info(f"Parsed from Lua: AppID={appid_1}, Depots={depots_1}, Keys={list(keys_1.keys())}")

        depot_names_1 = {d: f"Depot {d}" for d in depots_1}
        success = register_plugin_game(
            appid=appid_1,
            name=game_name,
            depot_ids=depots_1,
            decryption_keys=keys_1,
            installdir="Cubing Forge",
            depot_names=depot_names_1,
        )
        if not success:
            log_fail("register_plugin_game returned False for 4959210")
            test_passed = False
        else:
            added_test_appids.append(appid_1)
            log_pass("Game 4959210 registered successfully into plugin library")

        # Verify config.yaml contents
        live_apps = get_additional_apps(cfg_path)
        live_depots = get_additional_depots(cfg_path)
        live_keys = get_decryption_keys(cfg_path)

        if appid_1 in live_apps:
            log_pass(f"Base AppID '{appid_1}' is present in AdditionalApps")
        else:
            log_fail(f"Base AppID '{appid_1}' is MISSING from AdditionalApps")
            test_passed = False

        if appid_1 not in live_depots:
            log_pass(f"Base AppID '{appid_1}' is NOT in AdditionalDepots (Strict Guard verified)")
        else:
            log_fail(f"CRITICAL: Base AppID '{appid_1}' was incorrectly added to AdditionalDepots!")
            test_passed = False

        for d in depots_1:
            if d in live_depots:
                log_pass(f"Content Depot '{d}' is present in AdditionalDepots")
            else:
                log_fail(f"Content Depot '{d}' is MISSING from AdditionalDepots")
                test_passed = False

            if d in live_keys and live_keys[d].lower() == keys_1[d].lower():
                log_pass(f"Depot Key for '{d}' is present in DecryptionKeys ({live_keys[d][:16]}...)")
            else:
                log_fail(f"Depot Key for '{d}' is MISSING or incorrect in DecryptionKeys")
                test_passed = False

        # ---------------------------------------------------------------------
        # TEST 2: Root AppKey Registration (Sword With Sauce - 581630)
        # ---------------------------------------------------------------------
        log_step("TEST 2: Register game with root AppKey (581630 - Sword With Sauce)")
        appid_2 = "581630"
        name_2 = "Sword With Sauce"
        root_key_2 = "aa1aaefaa2b16e8e51535b804949dcab23f6f88f868b1a719367aa1fbe2ec6d6"
        depot_2 = "581631"
        key_2 = "1111111111111111111111111111111111111111111111111111111111111111"

        keys_2 = {
            appid_2: root_key_2,
            depot_2: key_2,
        }
        depots_2 = [depot_2]

        success_2 = register_plugin_game(
            appid=appid_2,
            name=name_2,
            depot_ids=depots_2,
            decryption_keys=keys_2,
            installdir=name_2,
            depot_names={depot_2: "Game Content"},
        )
        if not success_2:
            log_fail("register_plugin_game returned False for 581630")
            test_passed = False
        else:
            added_test_appids.append(appid_2)
            log_pass("Game 581630 registered successfully")

        live_apps = get_additional_apps(cfg_path)
        live_depots = get_additional_depots(cfg_path)
        live_keys = get_decryption_keys(cfg_path)

        if appid_2 in live_apps:
            log_pass(f"Base AppID '{appid_2}' is present in AdditionalApps")
        else:
            log_fail(f"Base AppID '{appid_2}' is MISSING from AdditionalApps")
            test_passed = False

        if appid_2 not in live_depots:
            log_pass(f"Base AppID '{appid_2}' is NOT in AdditionalDepots (Strict Guard verified)")
        else:
            log_fail(f"CRITICAL: Base AppID '{appid_2}' was incorrectly added to AdditionalDepots!")
            test_passed = False

        if appid_2 in live_keys and live_keys[appid_2].lower() == root_key_2.lower():
            log_pass(f"Root AppKey for '{appid_2}' is present in DecryptionKeys (Fix confirmed!)")
        else:
            log_fail(f"CRITICAL: Root AppKey for '{appid_2}' was REJECTED or MISSING from DecryptionKeys!")
            test_passed = False

        # ---------------------------------------------------------------------
        # TEST 3: Depots Tab Unlocked Toggles & Guard Behavior
        # ---------------------------------------------------------------------
        log_step("TEST 3: Verify Depots Tab Unlocked Toggle Freedom & Strict Guards")

        with batch_config_edit(cfg_path) as editor:
            # 3a. Disabling a key in DecryptionKeys (simulating user turning toggle OFF)
            log_info("Simulating user toggling root AppKey 581630 OFF in Depots tab...")
            res_rem = editor.remove_key(appid_2, check_shared=False)
            if not res_rem:
                log_fail(f"Failed to remove key '{appid_2}' from DecryptionKeys")
                test_passed = False
            else:
                log_pass(f"Successfully toggled OFF / removed key '{appid_2}'")

        live_keys = get_decryption_keys(cfg_path)
        if appid_2 not in live_keys:
            log_pass(f"Key '{appid_2}' confirmed removed from live DecryptionKeys")
        else:
            log_fail(f"Key '{appid_2}' still present in DecryptionKeys after toggle OFF")
            test_passed = False

        with batch_config_edit(cfg_path) as editor:
            # 3b. Re-enabling a key in DecryptionKeys (simulating user turning toggle ON)
            log_info("Simulating user toggling root AppKey 581630 ON in Depots tab...")
            res_add = editor.add_key(appid_2, root_key_2, comment=f"{name_2} [AppKey]")
            if not res_add:
                log_fail(f"CRITICAL: editor.add_key refused to re-add root AppKey '{appid_2}'!")
                test_passed = False
            else:
                log_pass(f"Successfully toggled ON / re-added root AppKey '{appid_2}' to DecryptionKeys")

        live_keys = get_decryption_keys(cfg_path)
        if appid_2 in live_keys and live_keys[appid_2].lower() == root_key_2.lower():
            log_pass(f"Root AppKey '{appid_2}' persisted in live DecryptionKeys without self-sanitation reversion")
        else:
            log_fail(f"Root AppKey '{appid_2}' failed to persist in live DecryptionKeys!")
            test_passed = False

        with batch_config_edit(cfg_path) as editor:
            # 3c. Attempting illegal action: adding base AppID to AdditionalDepots
            log_info("Verifying that adding base AppID to AdditionalDepots is strictly refused...")
            res_illegal = editor.add_depot(appid_2, comment="Illegal Base AppID", app_id=appid_2)
            if not res_illegal:
                log_pass("Strict guard rejected adding base AppID to AdditionalDepots as expected")
            else:
                log_fail("CRITICAL: Strict guard failed! Base AppID was allowed in AdditionalDepots!")
                test_passed = False

            # 3d. Verify sole depot with self-referencing comment is removable (not falsely marked shared)
            log_info(f"Verifying depot '{depot_2}' for game '{appid_2}' can be removed when sole-owned...")
            is_shared_check = is_depot_shared_with_other_games(depot_2, excluding_appid=appid_2)
            if not is_shared_check:
                log_pass(f"Depot '{depot_2}' correctly identified as NOT shared with other games")
            else:
                log_fail(f"CRITICAL: Depot '{depot_2}' was FALSELY identified as shared with other games!")
                test_passed = False

            res_remove_depot = editor.remove_depot(depot_2, check_shared=True, excluding_appid=appid_2)
            if res_remove_depot:
                log_pass(f"Depot '{depot_2}' successfully removed from AdditionalDepots (self-referencing comment bug fix confirmed)")
            else:
                log_fail(f"CRITICAL: Failed to remove depot '{depot_2}' from AdditionalDepots!")
                test_passed = False

            # 3e. Quoted section headers in YAML engine
            log_info("Verifying quoted section header parsing in YAML engine...")
            sample_quoted_yaml = "'AdditionalDepots':\n  - 1111\n'DecryptionKeys':\n  2222: 1234567890123456789012345678901234567890123456789012345678901234\n"
            b_depots = _get_section_bounds(sample_quoted_yaml, "AdditionalDepots")
            b_keys = _get_section_bounds(sample_quoted_yaml, "DecryptionKeys")
            if b_depots and b_keys:
                log_pass("Quoted section bounds successfully resolved for 'AdditionalDepots' and 'DecryptionKeys'")
            else:
                log_fail("CRITICAL: Failed to resolve bounds for single-quoted YAML sections!")
                test_passed = False

            sample_dupe_yaml = "'AdditionalDepots':\n  - 1111\nAdditionalDepots:\n  - 2222\n"
            dupe_valid = _validate_yaml_content(sample_dupe_yaml)
            if not dupe_valid:
                log_pass("YAML validator successfully detected duplicate section with mixed quoting")
            else:
                log_fail("CRITICAL: YAML validator allowed duplicate top-level keys with mixed quoting!")
                test_passed = False

            # 3f. DLC AppID in AdditionalDepots allowance (like Isaac 250900 DLC 401920)
            log_info("Verifying DLC AppID can be added to AdditionalDepots while base AppID is rejected...")
            editor.add_app("401920", comment="Isaac DLC (401920)")
            dlc_depot_ok = editor.add_depot("401920", comment="Isaac DLC Depot (401920)", app_id="250900")
            if dlc_depot_ok:
                log_pass("DLC AppID '401920' was successfully allowed in AdditionalDepots for game '250900'")
            else:
                log_fail("CRITICAL: DLC AppID was incorrectly blocked from AdditionalDepots!")
                test_passed = False

            base_depot_blocked = editor.add_depot("250900", comment="Isaac Base Depot", app_id="250900")
            if not base_depot_blocked:
                log_pass("Base AppID '250900' was strictly blocked from AdditionalDepots")
            else:
                log_fail("CRITICAL: Base AppID '250900' was allowed in AdditionalDepots!")
                test_passed = False

            # 3g. Verifying foreign base AppID in AdditionalApps is rejected even if app_id mismatch
            editor.add_app("1145360", comment="Hades")
            foreign_base_blocked = editor.add_depot("1145360", comment="Hades Depot", app_id="250900")
            if not foreign_base_blocked:
                log_pass("Foreign base AppID '1145360' was strictly blocked from AdditionalDepots despite app_id mismatch")
            else:
                log_fail("CRITICAL: Foreign base AppID '1145360' was permitted in AdditionalDepots!")
                test_passed = False
            # 3h. Verifying DLC-only transition to ACCELA mode retains base AppID in AdditionalApps
            test_dlc_game = "999001"
            test_dlc_child = "999002"
            register_plugin_game(
                appid=test_dlc_game,
                name="Test DLC Game",
                depot_ids=["999003"],
                decryption_keys={"999003": "a" * 64},
                dlc_appids=[test_dlc_child],
            )
            added_test_appids.append(test_dlc_game)
            unregister_plugin_game(test_dlc_game, keep_in_additional_apps=True)
            apps_after_transition = get_additional_apps(cfg_path)
            if test_dlc_game in apps_after_transition and test_dlc_child not in apps_after_transition:
                log_pass("DLC-only transition to ACCELA mode cleanly restored base AppID and pruned DLC AppID")
            else:
                log_fail(f"CRITICAL: Transition to ACCELA mode failed to retain base AppID! Apps: {apps_after_transition}")
                test_passed = False
    finally:
        # ---------------------------------------------------------------------
        # TEST 4: Cleanup & State Reversal
        # ---------------------------------------------------------------------
        log_step("TEST 4: Cleanup & Reverting All Changes")
        for aid in added_test_appids:
            log_info(f"Unregistering test game {aid}...")
            unregister_plugin_game(aid, keep_in_additional_apps=False)

        # Restore original config bytes in-place to preserve system inode exactly
        _atomic_write(cfg_path, orig_cfg_bytes.decode("utf-8"))
        save_plugin_library(orig_plugin_lib)

        final_inode = cfg_path.stat().st_ino
        if final_inode == orig_inode:
            log_pass(f"System inode preserved successfully ({orig_inode})")
        else:
            log_fail(f"Inode changed from {orig_inode} to {final_inode}")
            test_passed = False

        final_bytes = cfg_path.read_bytes()
        final_lib = load_plugin_library()

        if final_bytes == orig_cfg_bytes:
            log_pass("SLSsteam config.yaml 100% byte-for-byte restored to pre-test state")
        else:
            log_fail("SLSsteam config.yaml was not restored to exact initial state")
            test_passed = False

        if final_lib == orig_plugin_lib:
            log_pass("Plugin library 100% restored to pre-test state")
        else:
            log_fail("Plugin library was not restored to exact initial state")
            test_passed = False

        final_apps = get_additional_apps(cfg_path)
        final_depots = get_additional_depots(cfg_path)
        final_keys = get_decryption_keys(cfg_path)

        if "4959210" not in final_apps and "4959210" not in final_depots and "4959210" not in final_keys:
            log_pass("Temporary test game 4959210 cleanly removed from all config sections")
        else:
            log_fail("Test game 4959210 left residual entries in config!")
            test_passed = False

        if cfg_was_created:
            try:
                cfg_path.unlink(missing_ok=True)
                if cfg_path.parent.exists() and not any(cfg_path.parent.iterdir()):
                    cfg_path.parent.rmdir()
                log_pass("Temporary test config cleanly removed")
            except Exception:
                pass

    print("\n" + "=" * 60)
    if test_passed:
        print(f"{Color.GREEN}{Color.BOLD}ALL TESTS PASSED! DecryptionKeys & AdditionalDepots logic is 100% verified.{Color.RESET}")
    else:
        print(f"{Color.RED}{Color.BOLD}ONE OR MORE TESTS FAILED. Check log output above.{Color.RESET}")
    print("=" * 60 + "\n")

    return test_passed


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
