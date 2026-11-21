#!/usr/bin/env python3
"""Regression tests for the SLSsteam config.yaml write path.

Covers the shapes that used to corrupt a value or lose a write:

* an inline comment with no space before '#' (`Plugins: yes#tight`) and an
  empty value followed by a comment (`Plugins:  # note`) - rebuilding the line
  as `f"{key}: {value}{tail}"` produced `yes# note`, which YAML reads as the
  literal string, so `Plugins` was never actually enabled;
* a key that only exists nested under another mapping - the unanchored
  replacement pattern matched it and appended a second top-level key;
* a nested duplicate key, which `yaml.safe_load` silently resolves and the
  column-0 scan never saw;
* several batch editors running at once: the unlocked read-modify-write let
  the later writer commit stale content over the earlier one and silently
  drop its entries.
"""

import logging
import shutil
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

logging.disable(logging.CRITICAL)

import yaml

from utils.yaml.core import batch_config_edit
from utils.yaml.sls_settings import ADVANCED_SETTINGS, read_sls_settings, write_sls_setting
from utils.yaml.validation import _validate_yaml_content
from utils.yaml.yaml_values import get_yaml_boolean_value, update_yaml_boolean_value

SETTINGS = {s.key: s for s in ADVANCED_SETTINGS}

passed = 0
failed = []


def check(cond, label):
    global passed
    if cond:
        passed += 1
        print(f"  [ok] {label}")
    else:
        failed.append(label)
        print(f"  [FAIL] {label}")


def with_file(content):
    td = Path(tempfile.mkdtemp())
    path = td / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return td, path


def test_boolean_comment_separator():
    print("=== boolean writer keeps the comment separator ===")
    cases = [
        ("Plugins:  # enable plugins\n", True),
        ("Plugins:\t# enable plugins\n", True),
        ('"Plugins":  # note\n', True),
        ("Plugins: yes\t# tab before comment\n", False),
        ("Plugins: yes \t# space then tab before comment\n", False),
        ("Plugins: yes # note\twith tab inside comment\n", False),
        ("Plugins: # note\n", True),
    ]
    for content, value in cases:
        td, path = with_file(content)
        try:
            update_yaml_boolean_value(path, "Plugins", value)
            text = path.read_text(encoding="utf-8")
            check(get_yaml_boolean_value(path, "Plugins") is value, f"reads back {value} for {content.strip()!r}")
            check(" #" in text or "\t#" in text, f"comment still separated by whitespace: {text.strip()!r}")
        finally:
            shutil.rmtree(td)

    # 'yes#tight' is one YAML scalar, not a boolean plus a comment. There is no
    # safe in-place fix, so the value must not be silently mangled further: the
    # writer leaves a line it cannot classify completely alone.
    td, path = with_file("Plugins: no#tight\n")
    try:
        update_yaml_boolean_value(path, "Plugins", True)
        check(
            path.read_text(encoding="utf-8") == "Plugins: no#tight\n",
            "a value YAML reads as one scalar is not rewritten into a different scalar",
        )
    finally:
        shutil.rmtree(td)


def test_boolean_never_touches_nested_key():
    print("=== boolean writer only matches a column-0 key ===")
    td, path = with_file("Parent:\n  Plugins: no\n")
    try:
        update_yaml_boolean_value(path, "Plugins", True)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        check(data.get("Plugins") is True, "top-level Plugins added")
        check(data["Parent"]["Plugins"] is False, "nested Plugins untouched")
    finally:
        shutil.rmtree(td)


def test_sls_setting_comment_separator():
    print("=== SLS setting writer keeps the comment separator ===")
    td, path = with_file("DisableUpdates:  # pause updates\n")
    try:
        ok, msg = write_sls_setting(path, SETTINGS["DisableUpdates"], True)
        text = path.read_text(encoding="utf-8")
        check(ok, f"write succeeded ({msg})")
        check(read_sls_settings(path, ["DisableUpdates"])["DisableUpdates"] is True, "reads back True")
        check("# pause updates" in text and "yes #" in text, f"separator preserved: {text.strip()!r}")
    finally:
        shutil.rmtree(td)


def test_sls_setting_refuses_instead_of_corrupting():
    print("=== SLS setting writer refuses a shape it cannot rewrite safely ===")
    # 'yes#tight' is one YAML scalar, not a boolean plus a comment. The writer
    # cannot fix it in place, and must not leave a mangled line behind.
    for content, key, value in (
        ("DisableUpdates: yes#tight\n", "DisableUpdates", False),
        ("FakeName: Neo#tight\n", "FakeName", "Neo"),
    ):
        td, path = with_file(content)
        try:
            ok, _ = write_sls_setting(path, SETTINGS[key], value)
            check(ok is False, f"refused {content.strip()!r}")
            check(path.read_text(encoding="utf-8") == content, "file left byte-for-byte untouched")
        finally:
            shutil.rmtree(td)


def test_sls_setting_nested_append():
    print("=== SLS setting writer still appends a missing nested child ===")
    td, path = with_file("IdleStatus:\n  Title: Old\n")
    try:
        ok, _ = write_sls_setting(path, SETTINGS["IdleStatus.AppId"], 480)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        check(ok, "write succeeded")
        check(data["IdleStatus"]["AppId"] == 480, f"AppId appended (got {data['IdleStatus'].get('AppId')})")
        check(data["IdleStatus"]["Title"] == "Old", "existing child untouched")
    finally:
        shutil.rmtree(td)


def test_validator_rejects_nested_duplicates():
    print("=== validator rejects duplicate keys at any depth ===")
    check(not _validate_yaml_content('DlcData:\n  730:\n    731: "a"\n    731: "b"\n'), "nested duplicate rejected")
    check(not _validate_yaml_content("Plugins: yes\nPlugins: no\n"), "top-level duplicate rejected")
    check(_validate_yaml_content('DlcData:\n  730:\n    731: "a"\n    732: "b"\n'), "distinct keys accepted")
    check(_validate_yaml_content("AdditionalApps:\n  - 730\n  - 730\n"), "repeated list items are not duplicates")


def test_concurrent_batch_edits_keep_every_entry():
    print("=== concurrent batch edits keep every entry ===")
    td = Path(tempfile.mkdtemp())
    path = td / "config.yaml"
    path.write_text("Plugins: yes\n", encoding="utf-8")
    errors = []

    def worker(i):
        try:
            with batch_config_edit(path) as editor:
                editor.add_app(10000 + i, f"Game {i}")
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    try:
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        text = path.read_text(encoding="utf-8")
        missing = [i for i in range(20) if f"  - {10000 + i} # Game {i}" not in text]
        check(not errors, f"no thread raised ({errors[:1]})")
        check(not missing, f"all 20 entries present (missing {missing})")
        check(_validate_yaml_content(text), "result is valid YAML")
    finally:
        shutil.rmtree(td)


def test_batch_edit_preserves_inode_and_outside_edits():
    print("=== batch edit keeps the inode and outside edits ===")
    td, path = with_file("Plugins: yes\n")
    try:
        inode = path.stat().st_ino
        with open(path, "a", encoding="utf-8") as f:
            f.write("FakeName: External\n")
        with batch_config_edit(path) as editor:
            editor.add_app(400, "Portal")
        text = path.read_text(encoding="utf-8")
        check(path.stat().st_ino == inode, "inode preserved (SLSsteam watcher follows it)")
        check("FakeName: External" in text, "edit made outside the editor survived")
        check("- 400" in text, "batch entry written")
    finally:
        shutil.rmtree(td)


def test_prerequisites_is_one_write():
    print("=== SLSsteam prerequisites apply in one write ===")
    from utils.yaml import slssteam as slssteam_module

    td, path = with_file("API: no\nLogLevels: 0x4\nPlugins: no\n")
    from utils.yaml import core as yaml_core

    real_write = yaml_core._atomic_write
    real_backup = yaml_core._create_backup
    real_mode = slssteam_module.is_slssteam_mode_enabled
    real_manage = slssteam_module.is_slssteam_config_management_enabled
    calls = {"write": 0, "backup": 0}

    def counting_write(p, content):
        calls["write"] += 1
        return real_write(p, content)

    def counting_backup(p):
        calls["backup"] += 1
        return real_backup(p)

    try:
        yaml_core._atomic_write = counting_write
        yaml_core._create_backup = counting_backup
        slssteam_module.is_slssteam_mode_enabled = lambda: True
        slssteam_module.is_slssteam_config_management_enabled = lambda: True

        inode = path.stat().st_ino
        changed = slssteam_module.ensure_slssteam_prerequisites(path)
        text = path.read_text(encoding="utf-8")
        check(changed is True, "prerequisites reported a change")
        check(calls["write"] == 1, f"three prerequisites cost one write (got {calls['write']})")
        # The backup is once per session per file, so assert at most one.
        check(calls["backup"] <= 1, f"at most one backup taken (got {calls['backup']})")
        check(path.stat().st_ino == inode, "inode preserved")
        check(get_yaml_boolean_value(path, "API") is True, "API: yes written")
        check(get_yaml_boolean_value(path, "Plugins") is True, "Plugins: yes written")
        check("LogLevels: 0x6" in text, f"Once flag OR-ed into LogLevels ({text.strip()!r})")

        calls.update(write=0, backup=0)
        check(slssteam_module.ensure_slssteam_prerequisites(path) is False, "already-correct config reports no change")
        check(calls["write"] == 0, f"already-correct config is not rewritten (got {calls['write']} writes)")
        check(calls["backup"] == 0, "no backup for a no-op")
    finally:
        yaml_core._atomic_write = real_write
        yaml_core._create_backup = real_backup
        slssteam_module.is_slssteam_mode_enabled = real_mode
        slssteam_module.is_slssteam_config_management_enabled = real_manage
        shutil.rmtree(td)


def test_backup_rotation_is_capped():
    print("=== repair backups stop at three ===")
    from utils import assfixer

    td, path = with_file("API: yes\n")
    try:
        for i in range(5):
            path.write_text(f"API: yes\n# {i}\n", encoding="utf-8")
            assfixer.make_backup_with_rotation(path)
        names = sorted(p.name for p in td.iterdir() if p.name != "config.yaml")
        check(names == ["config.yaml.bak", "config.yaml.bak2", "config.yaml.bak3"], f"only three backups kept ({names})")
        current = path.read_text(encoding="utf-8")
        snapshots = [td / n for n in names]
        check(
            any(s.read_text(encoding="utf-8") == current for s in snapshots),
            "the current config is one of the three snapshots",
        )
        check(
            all(s.read_text(encoding="utf-8").strip() for s in snapshots),
            "all snapshots are readable",
        )
    finally:
        shutil.rmtree(td)


def test_boot_template_is_reused():
    print("=== boot check reuses a recent template ===")
    import os
    from utils import assfixer

    td = Path(tempfile.mkdtemp())
    cache = td / "sls_config_template.yaml"
    real_path = assfixer._template_cache_path
    real_fetch = assfixer.fetch_template
    real_bundled = assfixer.get_bundled_or_cached_template
    try:
        assfixer._template_cache_path = lambda: cache

        def refuse(*_a, **_k):
            raise AssertionError("network used although a fresh cache exists")

        cache.write_text("API: yes\n" + "# pad\n" * 40, encoding="utf-8")
        assfixer.fetch_template = refuse
        check(len(assfixer.get_boot_template()) > 200, "fresh cache is used without fetching")

        os.utime(cache, (0, 0))
        stale_fetches = []

        def failing_fetch(*_a, **_k):
            stale_fetches.append(1)
            raise RuntimeError("offline")

        assfixer.fetch_template = failing_fetch
        assfixer.get_bundled_or_cached_template = lambda: "API: yes\n" + "# bundled\n" * 40
        template = assfixer.get_boot_template()
        check(len(stale_fetches) == 1, "a stale cache triggers one fetch attempt")
        check("bundled" in template, "offline falls back to the bundled template")
    finally:
        assfixer._template_cache_path = real_path
        assfixer.fetch_template = real_fetch
        assfixer.get_bundled_or_cached_template = real_bundled
        shutil.rmtree(td)


def main() -> int:
    test_boolean_comment_separator()
    test_boolean_never_touches_nested_key()
    test_sls_setting_comment_separator()
    test_sls_setting_refuses_instead_of_corrupting()
    test_sls_setting_nested_append()
    test_validator_rejects_nested_duplicates()
    test_concurrent_batch_edits_keep_every_entry()
    test_batch_edit_preserves_inode_and_outside_edits()
    test_prerequisites_is_one_write()
    test_backup_rotation_is_capped()
    test_boot_template_is_reused()

    print("\n--------------------------------------------------")
    print(f"Results: {passed} passed, {len(failed)} failed")
    if failed:
        print("Failures:")
        for f in failed:
            print(f"  - {f}")
        return 1
    print("ALL CONFIG WRITE TESTS PASSED!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
