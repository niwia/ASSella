#!/usr/bin/env python3
"""Test the gate that fails on dialog windows without ASSella's frame.

The detector is the whole gate, so it is driven here against throwaway source
trees: a QDialog that never mentions the frame, one that sets
``FramelessWindowHint`` itself, one that calls ``install_themed_frame``, one
that extends ``ThemedDialog``, a QWidget (not a dialog), the exempt themed
dialog module, and the vendored tree. The baseline comparison and the CLI are
driven the same way, so a regression, an improvement and an unchanged tree are
pinned.

    python3 scripts/test_themed_dialog_gate.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "scripts" / "check_themed_dialogs.py"

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
    spec = importlib.util.spec_from_file_location("themed_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["themed_gate"] = module
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


BARE = '''
from PyQt6.QtWidgets import QDialog


class BareDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bare")
'''

SELF_FRAMED = '''
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog


class SelfFramedDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
'''

INSTALLED = '''
from PyQt6.QtWidgets import QDialog
from ui.dialogs.themed_dialog import install_themed_frame


class InstalledDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = install_themed_frame(self, "Installed")
'''

BASE_AND_CHILD = '''
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog


class ThemedDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)


class ChildDialog(ThemedDialog):
    pass
'''

NOT_A_DIALOG = '''
from PyQt6.QtWidgets import QWidget


class Panel(QWidget):
    pass
'''


def run_gate(root: Path, baseline: Path, *extra: str):
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(root), "--baseline", str(baseline), *extra],
        capture_output=True,
        text=True,
    )


def main() -> int:
    gate = load_gate()

    print("\n-- what one unframed dialog is --")
    with tempfile.TemporaryDirectory() as tmp:
        src = make_tree(Path(tmp), {
            "bare.py": BARE,
            "self_framed.py": SELF_FRAMED,
            "installed.py": INSTALLED,
            "child.py": BASE_AND_CHILD,
            "panel.py": NOT_A_DIALOG,
            "ui/dialogs/themed_dialog.py": BARE,
            "deps/vendor.py": BARE,
        })
        counts = gate.analyse(src)
        check(counts.get("bare.py") == {"unframed": 1},
              "a QDialog that never mentions the frame is one unframed class")
        check("self_framed.py" not in counts, "setting FramelessWindowHint itself is enough")
        check("installed.py" not in counts, "calling install_themed_frame is enough")
        check("child.py" not in counts, "extending ThemedDialog is enough")
        check("panel.py" not in counts, "a QWidget is not a dialog window")
        check("ui/dialogs/themed_dialog.py" not in counts,
              "the module that builds the frame is exempt")
        check("deps/vendor.py" not in counts, "vendored code is skipped")

        check(gate.analyse(make_tree(Path(tmp) / "clean", {"calm.py": "x = 1\n"})) == {},
              "a tree with no dialog counts nothing")
        both = make_tree(Path(tmp) / "both", {"two.py": BARE + BARE.replace("BareDialog", "OtherDialog")})
        check(gate.analyse(both).get("two.py") == {"unframed": 2},
              "two unframed classes in one file count as two")

    print("\n-- the baseline comparison --")
    current = {"a.py": {"unframed": 1}, "b.py": {"unframed": 2}}
    regressions, improvements = gate.compare(current, current)
    check(regressions == [] and improvements == [], "an unchanged tree neither regresses nor improves")

    regressions, improvements = gate.compare(
        {"a.py": {"unframed": 2}, "b.py": {"unframed": 2}},
        {"a.py": {"unframed": 1}, "b.py": {"unframed": 2}},
    )
    check(len(regressions) == 1 and regressions[0][0] == "a.py" and regressions[0][3] == 1,
          "one more unframed dialog in a known file is a regression")

    regressions, _ = gate.compare({"new.py": {"unframed": 1}}, {})
    check(len(regressions) == 1 and regressions[0][0] == "new.py",
          "a file that did not exist in the baseline is a regression")

    _, improvements = gate.compare({}, {"gone.py": {"unframed": 1}})
    check(len(improvements) == 1 and improvements[0][0] == "gone.py",
          "a converted file is an improvement")

    print("\n-- the gate from the command line --")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        src = make_tree(tmp_path, {"bare.py": BARE})
        baseline = tmp_path / "baseline.json"

        missing = run_gate(src, baseline)
        check(missing.returncode == 1 and "is missing" in missing.stdout,
              "a missing baseline fails with a message that says what to do")

        written = run_gate(src, baseline, "--write")
        check(written.returncode == 0 and baseline.is_file(), "--write freezes the tree")
        check(json.loads(baseline.read_text()) == {"bare.py": {"unframed": 1}},
              "the baseline records the class by file")

        passing = run_gate(src, baseline)
        check(passing.returncode == 0 and "No new unframed dialogs" in passing.stdout,
              "the gate passes when nothing moved")

        make_tree(tmp_path, {"another.py": BARE})
        failing = run_gate(src, baseline)
        check(failing.returncode == 1 and "::error::New dialogs without the themed frame:" in failing.stdout,
              "a new unframed dialog fails the gate")
        check("another.py" in failing.stdout, "the failing file is named")
        check("install_themed_frame" in failing.stdout, "the failure says what to use instead")

        (src / "another.py").unlink()
        check(run_gate(src, baseline).returncode == 0, "fixing it makes the gate pass again")

        report = run_gate(src, baseline, "--report")
        check("bare.py" in report.stdout, "--report prints the ranked inventory")
        check("1 files" in report.stdout and "1 classes" in report.stdout, "--report counts files and classes")
        check(report.returncode == 0, "--report never fails")

        (src / "bare.py").write_text(INSTALLED)
        tightened = run_gate(src, baseline)
        check(tightened.returncode == 0 and "improved: bare.py" in tightened.stdout,
              "a conversion is reported as an improvement, not a failure")
        check("run --write to tighten" in tightened.stdout, "and the baseline is invited to tighten")

    print("\n-- the checked-in baseline --")
    real = REPO_ROOT / "scripts" / "themed_dialogs_baseline.json"
    check(real.is_file(), "the baseline is checked in")
    stored = json.loads(real.read_text())
    live = gate.analyse(REPO_ROOT / "src")
    check(all(live.get(name, {}) == row for name, row in stored.items()),
          "the stored baseline matches the tree it was written from")
    check(gate.compare(live, stored)[0] == [],
          "the tree holds no unframed dialog the baseline does not know about")
    check(stored == {}, "every dialog in the tree already wears the themed frame")

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
