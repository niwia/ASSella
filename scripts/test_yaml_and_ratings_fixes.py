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


def test_yaml_manager_regressions():
    """Empty flow sections, quoted IDs, unquoted DlcData, IdleStatus and one-write adds."""
    print("=== yaml manager regressions ===")
    from utils.yaml.apps import (
        add_additional_app,
        get_additional_apps,
        remove_additional_app,
    )
    from utils.yaml.core import _normalize_flow_sections
    from utils.yaml.dlc import add_dlc_data_batch, get_dlc_data
    from utils.yaml.fakeappid import add_fake_app_id
    from utils.yaml.maintenance import add_launch_option, get_launch_option
    from utils.yaml.sls_settings import ADVANCED_SETTINGS, read_sls_settings, write_sls_setting
    from utils.yaml.tokens import add_app_token

    base = "Plugins: yes\nFakeAppIds: {}\nLaunchOptions: {}\nAppTokens: {}\nDlcData: {}\n"
    for fn, args, label in (
        (add_fake_app_id, ("2689120", "Gladio Mori", "480"), "FakeAppIds"),
        (add_launch_option, ("2689120", "env LD_AUDIT=/tmp/x.so %command%"), "LaunchOptions"),
        (add_app_token, ("2689120", "abc123"), "AppTokens"),
    ):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.yaml"
            p.write_text(base, encoding="utf-8")
            assert fn(p, *args) is True, f"{label} write with an empty flow section failed"
            text = p.read_text(encoding="utf-8")
            assert _validate_yaml_content(text), f"{label} produced invalid YAML: {text!r}"
            assert text.count(f"{label}:") == 1, f"{label} duplicated: {text!r}"

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "config.yaml"
        p.write_text(base, encoding="utf-8")
        assert add_dlc_data_batch(p, "620", {"621": "Portal"}) is True
        assert get_dlc_data(p, "620") == {"621": "Portal"}
        assert _validate_yaml_content(p.read_text(encoding="utf-8"))

    # Quoted list IDs are recognised instead of being appended/ignored.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "config.yaml"
        p.write_text('Plugins: yes\nAdditionalApps:\n  - "123" # quoted\n', encoding="utf-8")
        assert add_additional_app(p, 123) is False
        assert get_additional_apps(p) == ["123"]
        assert remove_additional_app(p, 123) is True
        assert get_additional_apps(p) == []

    # Unquoted DlcData values are readable and not re-added as duplicates.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "config.yaml"
        p.write_text("Plugins: yes\nDlcData:\n  620:\n    621: Portal\n", encoding="utf-8")
        assert get_dlc_data(p, "620") == {"621": "Portal"}
        assert add_dlc_data_batch(p, "620", {"621": "Portal", "622": "Other"}) is True
        assert get_dlc_data(p, "620") == {"621": "Portal", "622": "Other"}

    # A flow-style IdleStatus must keep its sibling Title.
    idle = next(s for s in ADVANCED_SETTINGS if s.key == "IdleStatus.AppId")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "config.yaml"
        p.write_text('IdleStatus: {AppId: 0, Title: "Playing"}\n', encoding="utf-8")
        ok, msg = write_sls_setting(p, idle, 480)
        assert ok, msg
        vals = read_sls_settings(p, ["IdleStatus.AppId", "IdleStatus.Title"])
        assert vals["IdleStatus.AppId"] == 480
        assert vals["IdleStatus.Title"] == "Playing"

    # Long flow-style DLC names are not folded into an unreadable continuation.
    long_name = "A very long DLC name that goes on and on and on and should not be folded across lines by the YAML dumper"
    normalized = _normalize_flow_sections(f'DlcData: {{620: {{621: "{long_name}"}}}}\n')
    assert long_name in normalized, f"long DLC name was folded: {normalized!r}"
    assert "\\\n" not in normalized

    # Launch option reads the command, not its YAML quoting/comment.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "config.yaml"
        p.write_text('LaunchOptions:\n  1: "env X=1 %command%" # note\n', encoding="utf-8")
        assert get_launch_option(p, "1") == "env X=1 %command%"

    # Plugins: yes is folded into the same write as the app.
    import utils.yaml.core as yaml_core
    import utils.yaml.yaml_values as yaml_values

    writes = []
    real_write = yaml_core._atomic_write

    def counting_write(path, content):
        writes.append(path)
        return real_write(path, content)

    yaml_core._atomic_write = counting_write
    yaml_values._atomic_write = counting_write
    try:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.yaml"
            p.write_text("Plugins: no\n", encoding="utf-8")
            assert add_additional_app(p, 123) is True
            assert len(writes) == 1, f"add caused {len(writes)} writes, expected 1"
    finally:
        yaml_core._atomic_write = real_write
        yaml_values._atomic_write = real_write

    print("✅ test_yaml_manager_regressions passed")


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
        test_yaml_manager_regressions()
    print("\n🎉 ALL TESTS PASSED!")


if __name__ == "__main__":
    main()
