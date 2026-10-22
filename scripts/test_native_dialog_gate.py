#!/usr/bin/env python3
"""Test the gate that fails on new native Qt dialogs.

The detector is the whole gate, so it is driven here against throwaway source
trees: a file that calls QMessageBox, one that imports it under an alias, one
that constructs a QFileDialog, one that already uses the themed dialog, one
that only touches an enum member (not a call), the vendored tree, and the
allowed replacement file. The baseline comparison and the CLI are driven the
same way, so a regression, an improvement and an unchanged tree are pinned.

    python3 scripts/test_native_dialog_gate.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "scripts" / "check_native_dialogs.py"

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


def load_gate():
    spec = importlib.util.spec_from_file_location("native_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["native_gate"] = module
    spec.loader.exec_module(module)
    return module


def make_tree(root: Path, files: dict) -> Path:
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        path = src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return src


MESSAGE_BOX = '''
from PyQt6.QtWidgets import QMessageBox


def warn(parent, text):
    QMessageBox.warning(parent, "Title", text)
    return QMessageBox.StandardButton.Yes
'''

ALIASED = '''
from PyQt6.QtWidgets import QMessageBox as MB


def warn(parent):
    MB.warning(parent, "T", "M")
'''

CONSTRUCTOR = '''
from PyQt6.QtWidgets import QFileDialog


def pick(parent):
    return QFileDialog(parent)
'''

THEMED = '''
from ui.dialogs.themed_message_box import ThemedMessageBox


def warn(parent):
    return ThemedMessageBox.warning(parent, "T", "M")
'''

ENUM_ONLY = '''
from PyQt6.QtWidgets import QMessageBox


FLAG = QMessageBox.StandardButton.Yes
'''


def run_gate(root: Path, baseline: Path, *extra: str):
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(root), "--baseline", str(baseline), *extra],
        capture_output=True,
        text=True,
    )


def main() -> int:
    gate = load_gate()

    print("\n-- what one site is --")
    with tempfile.TemporaryDirectory() as tmp:
        src = make_tree(Path(tmp), {
            "warn.py": MESSAGE_BOX,
            "aliased.py": ALIASED,
            "pick.py": CONSTRUCTOR,
            "themed.py": THEMED,
            "enum_only.py": ENUM_ONLY,
            "ui/dialogs/themed_message_box.py": MESSAGE_BOX,
            "deps/vendor.py": MESSAGE_BOX,
        })
        counts = gate.analyse(src)
        check(counts.get("warn.py") == {"message_box": 1},
              "a QMessageBox classmethod call is one message_box site")
        check(counts.get("aliased.py") == {"message_box": 1},
              "an aliased import is followed to the class it names")
        check(counts.get("pick.py") == {"file_dialog": 1},
              "a QFileDialog constructor is a file_dialog site")
        check("themed.py" not in counts, "a call on the themed dialog is not counted")
        check("enum_only.py" not in counts, "touching an enum member is not a dialog")
        check("ui/dialogs/themed_message_box.py" not in counts,
              "the replacement itself is allowed to name the native classes")
        check("deps/vendor.py" not in counts, "vendored code is skipped")
        check(gate.totals(counts) == {"message_box": 2, "file_dialog": 1}, "totals add up")

        check(gate.analyse(make_tree(Path(tmp) / "clean", {"calm.py": "x = 1\n"})) == {},
              "a tree with no native dialog counts nothing")

    print("\n-- the baseline comparison --")
    current = {"a.py": {"message_box": 1}, "b.py": {"file_dialog": 2}}
    regressions, improvements = gate.compare(current, current)
    check(regressions == [] and improvements == [], "an unchanged tree neither regresses nor improves")

    regressions, improvements = gate.compare(
        {"a.py": {"message_box": 2}, "b.py": {"file_dialog": 2}},
        {"a.py": {"message_box": 1}, "b.py": {"file_dialog": 2}},
    )
    check(len(regressions) == 1 and regressions[0][0] == "a.py" and regressions[0][3] == 1,
          "one more site in a known file is a regression")

    regressions, _ = gate.compare({"new.py": {"message_box": 1}}, {})
    check(len(regressions) == 1 and regressions[0][0] == "new.py",
          "a file that did not exist in the baseline is a regression")

    _, improvements = gate.compare({"a.py": {"message_box": 0}}, {"a.py": {"message_box": 1}})
    check(len(improvements) == 1 and improvements[0][3] == 1, "one fewer site is an improvement")

    _, improvements = gate.compare({}, {"gone.py": {"message_box": 1}})
    check(len(improvements) == 1 and improvements[0][0] == "gone.py",
          "a migrated file is an improvement")

    print("\n-- the gate from the command line --")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        src = make_tree(tmp_path, {"warn.py": MESSAGE_BOX})
        baseline = tmp_path / "baseline.json"

        missing = run_gate(src, baseline)
        check(missing.returncode == 1 and "is missing" in missing.stdout,
              "a missing baseline fails with a message that says what to do")

        written = run_gate(src, baseline, "--write")
        check(written.returncode == 0 and baseline.is_file(), "--write freezes the tree")
        check(json.loads(baseline.read_text()) == {"warn.py": {"message_box": 1}},
              "the baseline records the site by file and kind")

        passing = run_gate(src, baseline)
        check(passing.returncode == 0 and "No new native dialogs" in passing.stdout,
              "the gate passes when nothing moved")

        make_tree(tmp_path, {"another.py": MESSAGE_BOX})
        failing = run_gate(src, baseline)
        check(failing.returncode == 1 and "::error::New native dialogs:" in failing.stdout,
              "a new native dialog fails the gate")
        check("another.py" in failing.stdout, "the failing file is named")
        check("ThemedMessageBox" in failing.stdout, "the failure says what to use instead")

        (src / "another.py").unlink()
        check(run_gate(src, baseline).returncode == 0, "fixing it makes the gate pass again")

        report = run_gate(src, baseline, "--report")
        check("by kind:" in report.stdout and "warn.py" in report.stdout,
              "--report prints the ranked inventory")
        check(report.returncode == 0, "--report never fails")

        # An improvement is reported and does not fail the gate.
        (src / "warn.py").write_text(THEMED)
        tightened = run_gate(src, baseline)
        check(tightened.returncode == 0 and "improved: warn.py" in tightened.stdout,
              "a migration is reported as an improvement, not a failure")
        check("run --write to tighten" in tightened.stdout, "and the baseline is invited to tighten")

    print("\n-- the checked-in baseline --")
    real = REPO_ROOT / "scripts" / "native_dialogs_baseline.json"
    check(real.is_file(), "the baseline is checked in")
    stored = json.loads(real.read_text())
    live = gate.analyse(REPO_ROOT / "src")
    check(all(live.get(name, {}) == row for name, row in stored.items()),
          "the stored baseline matches the tree it was written from")
    check(gate.compare(live, stored)[0] == [],
          "the tree holds no native dialog the baseline does not know about")

    print()
    if _failures:
        print(f"FAILED {len(_failures)} of {PASSED + len(_failures)} checks:")
        for label in _failures:
            print(f"  - {label}")
        return 1
    print(f"All {PASSED} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
