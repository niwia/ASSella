#!/usr/bin/env python3
"""Fail when a new exception handler starts swallowing errors.

A handler that catches broadly and then does nothing - ``except Exception:
pass`` - is how the last three shipped bugs hid:

    add_job_safely was monkeypatched, the call failed, the surrounding
    ``except`` logged it and the job never queued;
    DepotKeyManager().get_key(...) did not exist, the AttributeError went
    into ``except Exception: pass`` and that key fallback never worked;
    four "Module X has no member Y" bugs reached a release for the same
    reason (see the ty gate comment in .github/workflows/ci.yml).

The sites already in the tree are frozen in
``scripts/silent_exceptions_baseline.json``. This gate fails when a file
gains one, and tells you when the baseline can be tightened. It never asks
you to fix all of them at once.

"Silent" means the handler reports nothing at all - a body of ``pass``, a bare
``return``, ``continue``/``break`` - so the error is lost with no trace. Any
log (``debug`` included), a fallback value and a re-raise all count as
reporting, which keeps the gate on the sites that disappear entirely rather
than on log-level taste.

    python3 scripts/check_silent_exceptions.py            # the CI gate
    python3 scripts/check_silent_exceptions.py --report   # ranked inventory
    python3 scripts/check_silent_exceptions.py --write    # refresh baseline
"""
import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO_ROOT / "src"
DEFAULT_BASELINE = REPO_ROOT / "scripts" / "silent_exceptions_baseline.json"

# Exception types this counts as "broad": a bare except, or a catch-all name.
BROAD_TYPES = {"Exception", "BaseException"}


def _is_silent_statement(node: ast.stmt) -> bool:
    """True when one statement hides the error rather than reporting it."""
    if isinstance(node, ast.Pass):
        return True
    if isinstance(node, ast.Continue) or isinstance(node, ast.Break):
        return True
    if isinstance(node, ast.Return):
        return node.value is None or (isinstance(node.value, ast.Constant) and node.value.value is None)
    return False


def _handler_is_silent(handler: ast.ExceptHandler) -> bool:
    return bool(handler.body) and all(_is_silent_statement(stmt) for stmt in handler.body)


def _caught_names(handler: ast.ExceptHandler):
    """The exception names a handler names, and whether it is bare."""
    if handler.type is None:
        return set(), True
    names = set()
    for node in ast.walk(handler.type):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names, False


def analyse(root: Path) -> dict:
    """Count silent handlers per file, split into broad and narrow catches."""
    counts = {}
    for path in sorted(Path(root).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        broad = narrow = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or not _handler_is_silent(node):
                continue
            names, is_bare = _caught_names(node)
            if is_bare or (names & BROAD_TYPES):
                broad += 1
            else:
                narrow += 1
        if broad or narrow:
            counts[str(path.relative_to(root))] = {"broad": broad, "narrow": narrow}
    return counts


def compare(current: dict, baseline: dict):
    """(regressions, improvements) between two analyse() results."""
    regressions, improvements = [], []
    for name, now in current.items():
        was = baseline.get(name, {"broad": 0, "narrow": 0})
        gained = now["broad"] - was.get("broad", 0) + now["narrow"] - was.get("narrow", 0)
        if gained > 0:
            regressions.append((name, was, now, gained))
    for name, was in baseline.items():
        now = current.get(name, {"broad": 0, "narrow": 0})
        lost = was.get("broad", 0) - now["broad"] + was.get("narrow", 0) - now["narrow"]
        if lost > 0:
            improvements.append((name, was, now, lost))
    regressions.sort(key=lambda row: -row[3])
    improvements.sort(key=lambda row: -row[3])
    return regressions, improvements


def totals(counts: dict) -> dict:
    return {
        "broad": sum(v["broad"] for v in counts.values()),
        "narrow": sum(v["narrow"] for v in counts.values()),
    }


def print_report(counts: dict) -> None:
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1]["broad"] - kv[1]["narrow"], kv[0]))
    grand = totals(counts)
    print(f"{len(ranked)} files hold {grand['broad'] + grand['narrow']} silent handlers "
          f"({grand['broad']} broad, {grand['narrow']} narrow)\n")
    print(f"{'broad':>6} {'narrow':>6}  file")
    for name, row in ranked:
        print(f"{row['broad']:>6} {row['narrow']:>6}  {name}")

    by_package = {}
    for name, row in counts.items():
        top = name.split("/")[0] if "/" in name else "(top level)"
        bucket = by_package.setdefault(top, {"broad": 0, "narrow": 0})
        bucket["broad"] += row["broad"]
        bucket["narrow"] += row["narrow"]
    print("\nby top-level package:")
    for name, row in sorted(by_package.items(), key=lambda kv: -(kv[1]["broad"] + kv[1]["narrow"])):
        print(f"{row['broad']:>6} {row['narrow']:>6}  {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="directory to walk")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE), help="baseline JSON")
    parser.add_argument("--report", action="store_true", help="print the ranked inventory")
    parser.add_argument("--write", action="store_true", help="rewrite the baseline from the tree")
    args = parser.parse_args()

    root = Path(args.root)
    baseline_path = Path(args.baseline)
    current = analyse(root)

    if args.report:
        print_report(current)
        return 0

    if args.write:
        baseline_path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        print(f"Wrote {baseline_path} ({len(current)} files).")
        return 0

    if not baseline_path.is_file():
        print(f"::error::{baseline_path} is missing; run with --write once to freeze the current tree.")
        return 1

    baseline = json.loads(baseline_path.read_text())
    regressions, improvements = compare(current, baseline)

    if regressions:
        print("::error::New silent exception handlers:")
        for name, was, now, gained in regressions:
            print(f"  {name}: {was['broad'] + was['narrow']} -> {now['broad'] + now['narrow']} "
                  f"(+{gained}; broad {was['broad']}->{now['broad']}, narrow {was['narrow']}->{now['narrow']})")
        print("\nReport the error (log with context, narrow the type, or re-raise) instead of adding a swallow.")
        return 1

    now_totals, was_totals = totals(current), totals(baseline)
    print(f"No new silent handlers ({now_totals['broad']} broad, {now_totals['narrow']} narrow).")
    if improvements:
        for name, was, now, lost in improvements:
            print(f"  improved: {name} {was['broad'] + was['narrow']} -> {now['broad'] + now['narrow']} (-{lost})")
        print("  run --write to tighten the baseline.")
    if now_totals != was_totals and not improvements:
        print("  (counts moved without a clean per-file improvement; check for renames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
