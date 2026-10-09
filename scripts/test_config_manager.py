#!/usr/bin/env python3
"""Functional test for the config.yaml manager, after the utils/yaml split.

Covers every section the module owns end to end, and asserts the facade still
behaves as a single import surface. Runs against a temporary config file, so it
never touches the user's real SLSsteam config.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_config_manager.py
"""
import importlib
import shutil
import sys
import tempfile
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
    tmp = Path(tempfile.mkdtemp(prefix="cfgmgr_test_"))
    cfg = tmp / "config.yaml"
    cfg.write_text("API: yes\nPlugins: yes\n", encoding="utf-8")

    import utils.yaml_config_manager as m

    # ---------------------------------------------------------------- facade
    print("=== facade is a working single import surface ===")
    api = importlib.import_module("utils.yaml_config_manager")
    names = ["get_user_config_path", "batch_config_edit", "BatchConfigEditor",
             "add_additional_app", "add_decryption_key", "add_dlc_data",
             "add_app_token", "get_additional_apps", "get_dlc_data",
             "SHARED_REDISTS", "_atomic_write", "_get_section_bounds",
             "fix_slssteam_config_indentation", "is_slssteam_mode_enabled"]
    missing = [n for n in names if not hasattr(api, n)]
    check(not missing, f"facade exposes {len(names)} probed names (missing: {missing})")
    check(len(api.SHARED_REDISTS) == 27,
          f"SHARED_REDISTS intact: {len(api.SHARED_REDISTS)} entries")

    print("\n=== every split module imports standalone ===")
    import ast
    pkg = SRC_DIR / "utils" / "yaml"
    mods = sorted(p.stem for p in pkg.glob("*.py") if p.name != "__init__.py")
    for mod in mods:
        try:
            importlib.import_module(f"utils.yaml.{mod}")
            ok, err = True, ""
        except Exception as e:
            ok, err = False, f"{type(e).__name__}: {e}"
        check(ok, f"utils.yaml.{mod}" + (f"  <- {err}" if err else ""))

    print("\n=== no definition is duplicated across the package ===")
    import collections
    seen = collections.Counter()
    for p in pkg.glob("*.py"):
        if p.name == "__init__.py":
            continue
        for n in ast.parse(p.read_text()).body:
            if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                seen[n.name] += 1
    dupes = [k for k, v in seen.items() if v > 1]
    check(not dupes, f"no duplicate definitions (found: {dupes})")

    # ------------------------------------------------------------ atomic write
    print("\n=== writes are atomic and validated ===")
    check(m._atomic_write(cfg, "API: yes\nPlugins: yes\nExtra: 1\n"), "valid YAML writes")
    check("Extra: 1" in cfg.read_text(), "content landed")
    before = cfg.stat().st_ino
    check(not m._atomic_write(cfg, "key: [unclosed\n"), "invalid YAML is refused")
    check("Extra: 1" in cfg.read_text(), "refused write left the file intact")
    check(cfg.stat().st_ino == before, "inode preserved (inotify watcher still fires)")
    check(oct(cfg.stat().st_mode)[-3:] == "600",
          f"config is 0600 (got {oct(cfg.stat().st_mode)[-3:]})")

    # ------------------------------------------------------------- AdditionalApps
    print("\n=== AdditionalApps / AdditionalDepots ===")
    check(m.add_additional_app(cfg, "123456", "Test Game"), "add_additional_app")
    apps = m.get_additional_apps(cfg)
    check(any("123456" in str(k) for k in (apps or [])),
          f"game appears in get_additional_apps: {apps}")
    check(m.add_additional_depot(cfg, "123457", "Test Depot"), "add_additional_depot")
    # These two resolve the user's config path internally (appid-first
    # signature), so point the resolver at our temp file for the test.
    # Patch inside the owning module: patching the facade re-binds the facade's
    # own name, while apps.py/keys.py call the one they imported from core.
    import utils.yaml.apps as _apps
    import utils.yaml.keys as _keys
    _apps.get_user_config_path = lambda: cfg
    _keys.get_user_config_path = lambda: cfg
    check(m.has_game_config_entries("123456"), "has_game_config_entries")
    check(m.remove_additional_app(cfg, "123456"), "remove_additional_app")
    check(not m.has_game_config_entries("123456"), "entry gone after removal")

    # ---------------------------------------------------------------- keys
    print("\n=== DecryptionKeys ===")
    key64 = "a" * 64
    check(m.add_decryption_key(cfg, "123458", key64), "add_decryption_key")
    keys = m.get_decryption_keys(cfg)
    check(any("123458" in str(k) for k in (keys or {})), f"key readable: {list((keys or {}).keys())[:4]}")
    check(m.has_game_decryption_keys("123458"), "has_game_decryption_keys")
    check(not m.add_decryption_key(cfg, "123459", "tooshort"),
          "invalid (non-hex64) key rejected")
    check(m.remove_decryption_key(cfg, "123458"), "remove_decryption_key")

    # ---------------------------------------------------------------- DLC
    # DlcData and AppTokens writers go through _get_config_content_if_enabled,
    # which returns a sentinel and writes nothing when SLSsteam config
    # management is off. On a clean runner there is no real config, so it is off
    # by default - force it on to exercise the write path.
    import utils.yaml.core as _core
    _core._config_management_enabled = lambda: True

    print("\n=== DlcData ===")
    check(m.add_dlc_data(cfg, "123460", "999", "DLC One"), "add_dlc_data")
    dl = m.get_dlc_data(cfg, "123460")
    check(dl and str(dl).find("999") >= 0, f"get_dlc_data returns it: {dl}")
    check(m.remove_dlc_data(cfg, "123460", "999"), "remove_dlc_data")

    # ------------------------------------------------------------ AppTokens
    print("\n=== AppTokens ===")
    check(m.add_app_token(cfg, "123461", "deadbeef"), "add_app_token")
    check(m.get_app_tokens(cfg) is not None, "get_app_tokens returns a mapping")
    check(m.remove_app_token(cfg, "123461"), "remove_app_token")

    # ------------------------------------------------------- scalar + repair
    print("\n=== scalar values and structural repair ===")
    check(m.get_yaml_boolean_value(cfg, "API", default=False) is True,
          "get_yaml_boolean_value reads API")
    check(m.update_yaml_boolean_value(cfg, "Logging", True), "update_yaml_boolean_value")
    check(m.get_yaml_boolean_value(cfg, "Logging", default=False) is True,
          "new key round-trips")
    cfg.write_text("API: yes\nAdditionalApps:\n      - 999 # bad indent\n", encoding="utf-8")
    m.fix_slssteam_config_indentation(cfg)
    check("999" in cfg.read_text(), "indentation repair kept the entry")

    # -------------------------------------------------------------- batching
    print("\n=== BatchConfigEditor commits once ===")
    cfg.write_text("API: yes\n", encoding="utf-8")
    with m.batch_config_edit(cfg) as ed:
        check(ed is not None, "batch_config_edit yields an editor")
    check(cfg.read_text().startswith("API"), "empty batch leaves config valid")

    # ------------------------------------------------------------ back / plan
    print("\n=== backup / maintenance ===")
    m.backup_config_on_startup(cfg)
    check((tmp / "config.yaml.bak").exists(), "backup created alongside the config")

    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)} of {PASSED + len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"ALL CONFIG MANAGER TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())