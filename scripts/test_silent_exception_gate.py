#!/usr/bin/env python3
"""Test the gate that fails on new silent exception handlers.

The detector is the whole gate, so it is driven here against throwaway source
trees: a file that swallows, a file that reports, a file that only logs at
debug, a file that re-raises, and the shapes in between. The baseline
comparison is driven the same way, so a regression, an improvement and an
unchanged tree are all pinned.

    python3 scripts/test_silent_exception_gate.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "scripts" / "check_silent_exceptions.py"

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
    spec = importlib.util.spec_from_file_location("silent_gate", GATE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["silent_gate"] = module
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


SWALLOWING = """
def load(path):
    try:
        return open(path).read()
    except Exception:
        pass
"""

BARE = """
def load(path):
    try:
        return open(path).read()
    except:
        pass
"""

NARROW = """
def load(path):
    try:
        return open(path).read()
    except OSError:
        pass
"""

REPORTING = """
import logging
logger = logging.getLogger(__name__)

def load(path):
    try:
        return open(path).read()
    except Exception as e:
        logger.warning(f"could not read {path}: {e}")
    return ""
"""

RERAISING = """
def load(path):
    try:
        return open(path).read()
    except Exception:
        raise
"""

FALLBACK_VALUE = """
def load(path):
    try:
        return open(path).read()
    except Exception:
        return {}
"""

DEBUG_ONLY = """
import logging
logger = logging.getLogger(__name__)

def load(path):
    try:
        return open(path).read()
    except Exception as e:
        logger.debug(f"ignored: {e}")
"""

CONDITIONAL = """
def load(path, strict):
    try:
        return open(path).read()
    except Exception:
        if strict:
            pass
        else:
            raise
"""


def main() -> int:
    gate = load_gate()

    # ---------------------------------------------------- what gets counted
    print("=== what counts as a swallow ===")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = make_tree(root, {
            "swallow.py": SWALLOWING,
            "bare.py": BARE,
            "narrow.py": NARROW,
            "reporting.py": REPORTING,
            "reraise.py": RERAISING,
            "fallback.py": FALLBACK_VALUE,
            "debug.py": DEBUG_ONLY,
            "conditional.py": CONDITIONAL,
        })
        counts = gate.analyse(src)

        check(counts.get("swallow.py") == {"broad": 1, "narrow": 0},
              "except Exception: pass is a broad swallow")
        check(counts.get("bare.py") == {"broad": 1, "narrow": 0},
              "a bare except: pass counts too")
        check(counts.get("narrow.py") == {"broad": 0, "narrow": 1},
              "a typed catch that swallows counts as narrow")
        check("reporting.py" not in counts,
              "logging a warning is not a swallow")
        check("reraise.py" not in counts,
              "re-raising is not a swallow")
        check("fallback.py" not in counts,
              "returning a real fallback value is not a swallow")
        check(counts.get("debug.py") is None,
              "a debug log reports the error, so it is not a swallow")
        check("conditional.py" not in counts,
              "a handler that does anything else is not counted (lower bound)")

        check(gate.totals(counts) == {"broad": 2, "narrow": 1},
              "the totals add up")

    # ------------------------------------------------- the baseline compare
    print("=== comparing against the baseline ===")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base_counts = gate.analyse(make_tree(root / "base", {"a.py": SWALLOWING}))

        # Each scenario gets its own tree so one cannot leak into the next.
        regressions, improvements = gate.compare(
            gate.analyse(make_tree(root / "more", {"a.py": SWALLOWING + "\n" + SWALLOWING, "b.py": SWALLOWING})),
            base_counts,
        )
        check([r[0] for r in regressions] == ["a.py", "b.py"],
              "a file that gains one, and a new file, are both regressions")
        check(improvements == [], "and nothing is reported as improved")

        regressions, improvements = gate.compare(
            gate.analyse(make_tree(root / "same", {"a.py": SWALLOWING})), base_counts
        )
        check(regressions == [] and improvements == [],
              "an unchanged tree is neither a regression nor an improvement")

        regressions, improvements = gate.compare(
            gate.analyse(make_tree(root / "less", {"a.py": REPORTING})), base_counts
        )
        check(regressions == [] and [i[0] for i in improvements] == ["a.py"],
              "fixing a swallow is reported as an improvement")

        regressions, improvements = gate.compare(
            gate.analyse(make_tree(root / "mixed", {"a.py": REPORTING, "b.py": SWALLOWING})), base_counts
        )
        check([r[0] for r in regressions] == ["b.py"] and [i[0] for i in improvements] == ["a.py"],
              "a fix in one file and a new swallow in another are both reported")

    # --------------------------------------------------------- the CLI gate
    print("=== the gate as CI runs it ===")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = make_tree(root, {"a.py": SWALLOWING, "b.py": REPORTING})
        baseline = root / "baseline.json"
        baseline.write_text(json.dumps(gate.analyse(src)))

        def run(*extra):
            return subprocess.run(
                [sys.executable, str(GATE), "--root", str(src), "--baseline", str(baseline), *extra],
                capture_output=True, text=True, cwd=str(REPO_ROOT),
            )

        clean = run()
        check(clean.returncode == 0 and "No new silent handlers" in clean.stdout,
              "a tree matching its baseline passes")

        make_tree(root, {"a.py": SWALLOWING + "\n" + SWALLOWING})
        dirty = run()
        check(dirty.returncode == 1 and "New silent exception handlers" in dirty.stdout,
              "a tree that gained one fails")

        make_tree(root, {"a.py": REPORTING})
        improved = run()
        check(improved.returncode == 0 and "improved: a.py" in improved.stdout,
              "a tree that improved passes and suggests tightening")

        rewrite = run("--write")
        check(rewrite.returncode == 0 and json.loads(baseline.read_text()) == gate.analyse(src),
              "--write freezes the tree as it is now")

        missing = subprocess.run(
            [sys.executable, str(GATE), "--root", str(src), "--baseline", str(root / "nope.json")],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        check(missing.returncode == 1 and "is missing" in missing.stdout,
              "a missing baseline is a loud failure, not a silent pass")

        report = run("--report")
        check(report.returncode == 0 and "silent handlers" in report.stdout,
              "--report prints the inventory")

    # ------------------------------------------------- the checked-in baseline
    print("=== the repository's own baseline ===")
    real = REPO_ROOT / "scripts" / "silent_exceptions_baseline.json"
    check(real.is_file(), "the baseline is checked in")
    if real.is_file():
        stored = json.loads(real.read_text())
        current = gate.analyse(REPO_ROOT / "src")
        regressions, _ = gate.compare(current, stored)
        check(regressions == [], "the tree holds no swallow the baseline does not know about")
        check(stored and all(set(row) == {"broad", "narrow"} for row in stored.values()),
              "the baseline lists a broad/narrow count per file")

    print(f"\n{'=' * 60}")
    if _failures:
        print(f"FAILED: {len(_failures)} check(s)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"All {PASSED} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
