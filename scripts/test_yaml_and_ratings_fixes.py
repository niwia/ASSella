#!/usr/bin/env python3
"""
Test suite verifying:
1. Concurrent thread safety of ProtonDB and Denuvo cache flushes in core.ratings
2. Robustness of YAML boolean and SLS settings editing against CRLF, quotes, empty keys, and duplicate prevention
3. Flow-style YAML sections and CRLF round-trips

Hermetic: both cache paths are redirected to a temporary directory, so the
suite never reads or overwrites the real user cache.
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import core.ratings as ratings

from core.ratings import (
    PROTONDB_CACHE_PATH,
    DENUVO_CACHE_PATH,
    _save_protondb_entry,
    flush_protondb_cache,
    _save_denuvo_cache_data,
)
from utils.yaml.apps import (
    add_additional_app,
    get_additional_apps,
    remove_additional_app,
)
from utils.yaml.core import _get_section_bounds
from utils.yaml.dlc import get_dlc_data
from utils.yaml.yaml_values import update_yaml_boolean_value, get_yaml_boolean_value
from utils.yaml.sls_settings import ADVANCED_SETTINGS, write_sls_setting, read_sls_settings
from utils.yaml.validation import _validate_yaml_content


def test_concurrent_protondb_flush():
    """Verify concurrent ProtonDB cache updates and flushes do not race or fail."""
    errors = []

    def worker(worker_id: int):
        try:
            for i in range(50):
                appid = str(100000 + (worker_id * 100) + i)
                tier = "platinum" if i % 2 == 0 else "gold"
                _save_protondb_entry(appid, tier, success=True)
                if i % 10 == 0:
                    flush_protondb_cache()
        except Exception as e:
            errors.append(f"Worker {worker_id} error: {e}")

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    flush_protondb_cache()
    assert not errors, f"ProtonDB concurrency errors encountered: {errors}"
    assert PROTONDB_CACHE_PATH.exists(), "ProtonDB cache file does not exist"
    print("✅ test_concurrent_protondb_flush passed")


def test_concurrent_denuvo_flush():
    """Verify concurrent Denuvo cache writes do not collide or fail."""
    errors = []

    def worker(worker_id: int):
        try:
            for i in range(20):
                data = {str(200000 + i): f"denuvo_v{worker_id}"}
                _save_denuvo_cache_data(data)
        except Exception as e:
            errors.append(f"Denuvo Worker {worker_id} error: {e}")

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Denuvo concurrency errors encountered: {errors}"
    assert DENUVO_CACHE_PATH.exists(), "Denuvo cache file does not exist"
    print("✅ test_concurrent_denuvo_flush passed")


def test_yaml_boolean_edge_cases():
    """Verify update_yaml_boolean_value handles CRLF, quotes, and empty keys without duplicate key errors."""
    cases = [
        # (initial_content, key, new_val, expected_new_val)
        ("Plugins: yes\n", "Plugins", False, False),
        ("Plugins: yes\r\n", "Plugins", False, False),
        ('"Plugins": yes\n', "Plugins", False, False),
        ("'Plugins': yes\r\n", "Plugins", False, False),
        ("Plugins:\n", "Plugins", True, True),
        ("Plugins:   # inline comment\r\n", "Plugins", True, True),
        ("DisableUpdates: yes\r\n", "DisableUpdates", False, False),
        ('"DisableUpdates": yes\n', "DisableUpdates", False, False),
        ("DisableUpdates:\n", "DisableUpdates", True, True),
    ]

    for content, key, new_val, expected in cases:
        with tempfile.NamedTemporaryFile("w", delete=False, newline="") as f:
            f.write(content)
            f.flush()
            p = Path(f.name)
        try:
            ok = update_yaml_boolean_value(p, key, new_val)
            assert ok, f"update_yaml_boolean_value failed for {key} with content {repr(content)}"
            res = p.read_text(encoding="utf-8")
            assert _validate_yaml_content(res), f"Resulting YAML is invalid: {repr(res)}"
            read_back = get_yaml_boolean_value(p, key)
            assert read_back == expected, f"Expected {expected}, got {read_back} for content {repr(content)}"
        finally:
            if p.exists():
                p.unlink()

    print("✅ test_yaml_boolean_edge_cases passed")


def test_sls_settings_edge_cases():
    """Verify write_sls_setting handles quoted keys, CRLF, and empty keys without duplicate key errors."""
    du_setting = next(s for s in ADVANCED_SETTINGS if s.key == "DisableUpdates")
    fn_setting = next(s for s in ADVANCED_SETTINGS if s.key == "FakeName")
    idle_app_setting = next(s for s in ADVANCED_SETTINGS if s.key == "IdleStatus.AppId")

    cases = [
        ('"DisableUpdates": yes\n', du_setting, False),
        ('DisableUpdates: yes\r\n', du_setting, False),
        ('DisableUpdates:\n', du_setting, False),
        ('"FakeName": ""\n', fn_setting, "TestPlayer"),
        ('FakeName:\r\n', fn_setting, "TestPlayer"),
        ('"IdleStatus":\r\n  "AppId": 0\r\n  "Title": ""\r\n', idle_app_setting, 480),
        ('IdleStatus:\r\n  AppId:\r\n', idle_app_setting, 480),
    ]

    for content, setting, new_val in cases:
        with tempfile.NamedTemporaryFile("w", delete=False, newline="") as f:
            f.write(content)
            f.flush()
            p = Path(f.name)
        try:
            ok, msg = write_sls_setting(p, setting, new_val)
            assert ok, f"write_sls_setting failed for {setting.key} with content {repr(content)}: {msg}"
            res = p.read_text(encoding="utf-8")
            assert _validate_yaml_content(res), f"Resulting YAML is invalid: {repr(res)}"
            vals = read_sls_settings(p, [setting.key])
            assert vals.get(setting.key) == new_val, f"Expected {new_val}, got {vals.get(setting.key)}"
        finally:
            if p.exists():
                p.unlink()

    print("✅ test_sls_settings_edge_cases passed")


def test_flow_style_sections():
    """Flow-style sections are expanded to block style, not duplicated."""
    print("=== flow-style sections round-trip ===")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "config.yaml"
        p.write_text(
            'AdditionalApps: [111, 222] # existing\n'
            'Plugins: yes\n'
            'DlcData: {620: {621: "Portal"}}\n',
            encoding="utf-8",
        )

        assert add_additional_app(p, 333, "New App") is True, "add to a flow list failed"
        text = p.read_text(encoding="utf-8")
        assert text.count("AdditionalApps:") == 1, f"duplicate section header: {text!r}"
        assert "# existing" in text, f"header comment lost: {text!r}"
        assert _validate_yaml_content(text), f"result is not valid YAML: {text!r}"
        assert {"111", "222", "333"} <= {str(a) for a in get_additional_apps(p)}

        # A nested mapping that happens to be called AdditionalApps must not be
        # mistaken for the top-level section.
        nested = "Parent:\n  AdditionalApps:\n    - 1\n"
        assert _get_section_bounds(nested, "AdditionalApps") is None

        dlc = get_dlc_data(p, "620")
        assert dlc.get("621") == "Portal", f"flow DlcData lost: {dlc!r}"

        assert remove_additional_app(p, 111) is True, "remove from flow list failed"
        remaining = {str(a) for a in get_additional_apps(p)}
        assert "111" not in remaining and {"222", "333"} <= remaining

    # Direct in-memory edits (used by the AT0-M writer) keep CRLF input intact.
    from utils.yaml.core import _add_list_item_in_memory

    out, changed = _add_list_item_in_memory(
        "AdditionalApps:\r\n  - 111\r\n", "AdditionalApps", "222"
    )
    assert changed and "\n" not in out.replace("\r\n", ""), f"mixed line endings: {out!r}"
    assert out.count("AdditionalApps:") == 1

    print("✅ test_flow_style_sections passed")


def main():
    global PROTONDB_CACHE_PATH, DENUVO_CACHE_PATH
    with tempfile.TemporaryDirectory() as td:
        PROTONDB_CACHE_PATH = Path(td) / "protondb_cache.json"
        DENUVO_CACHE_PATH = Path(td) / "denuvo_cache.json"
        ratings.PROTONDB_CACHE_PATH = PROTONDB_CACHE_PATH
        ratings.DENUVO_CACHE_PATH = DENUVO_CACHE_PATH
        ratings._invalidate_protondb_cache()
        ratings._invalidate_denuvo_cache()

        test_concurrent_protondb_flush()
        test_concurrent_denuvo_flush()
        test_yaml_boolean_edge_cases()
        test_sls_settings_edge_cases()
        test_flow_style_sections()
    print("\n🎉 ALL TESTS PASSED!")


if __name__ == "__main__":
    main()
