#!/usr/bin/env python3
"""Fail when a dialog window asks the desktop to draw its decorations.

Every window in ASSella wears the tool's own frame: ``install_themed_frame``
(``ui.dialogs.themed_dialog``) puts the rounded container and the custom
``DialogTitleBar`` on a dialog, and a dialog that already builds its own frame
sets ``Qt.WindowType.FramelessWindowHint`` itself. A dialog that does neither
gets the GTK/KDE title bar and, in a dark-theme session, looks like another
application's window sitting on top of this one.

The classes already in the tree are frozen in
``scripts/themed_dialogs_baseline.json``. This gate fails when a file gains
one, and notes when the baseline can be tightened, so the conversion can land
one dialog per commit without the rest of the tree blocking it.

    python3 scripts/check_themed_dialogs.py            # the CI gate
    python3 scripts/check_themed_dialogs.py --report   # ranked inventory
    python3 scripts/check_themed_dialogs.py --write    # refresh baseline

A class counts as framed when its body names ``FramelessWindowHint`` or
``install_themed_frame``, or when it extends ``ThemedDialog``. Only classes
that derive from ``QDialog`` (or from a themed dialog base) are looked at, and
the themed dialog modules themselves are exempt.
"""
import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO_ROOT / "src"
DEFAULT_BASELINE = REPO_ROOT / "scripts" / "themed_dialogs_baseline.json"

# Markers that mean "this dialog draws ASSella's own frame".
FRAME_MARKERS = ("FramelessWindowHint", "install_themed_frame")
THEMED_BASES = ("ThemedDialog",)

# The themed dialogs are the frame, not the thing that needs one.
ALLOWED_FILES = {
    "ui/dialogs/themed_dialog.py",
}

# Vendored code is not ours to rewrite.
SKIP_DIRS = {"deps", "__pycache__"}


def _is_dialog(bases: list) -> bool:
    """Whether a class derives from QDialog or one of the themed dialog bases."""
    for base in bases:
        name = ast.unparse(base).split(".")[-1]
        if name == "QDialog" or name in THEMED_BASES:
            return True
    return False


def _is_framed(node: ast.ClassDef) -> bool:
    """Whether the class inherits the frame or builds one itself."""
    if any(ast.unparse(base).split(".")[-1] in THEMED_BASES for base in node.bases):
        return True
    body = ast.unparse(node)
    return any(marker in body for marker in FRAME_MARKERS)


def analyse(root: Path) -> dict:
    """Count unframed dialog classes per file."""
    root = Path(root)
    counts = {}
    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(root))
        if set(path.parts) & SKIP_DIRS or rel in ALLOWED_FILES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        bare = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if _is_dialog(node.bases) and not _is_framed(node):
                bare.append(node.name)
        if bare:
            counts[rel] = {"unframed": len(bare)}
    return counts


def _flat(row: dict) -> int:
    return sum(row.values())


def compare(current: dict, baseline: dict):
    """(regressions, improvements) between two analyse() results."""
    regressions, improvements = [], []
    for name, now in current.items():
        was = baseline.get(name, {})
        gained = _flat(now) - _flat(was)
        if gained > 0:
            regressions.append((name, was, now, gained))
    for name, was in baseline.items():
        now = current.get(name, {})
        lost = _flat(was) - _flat(now)
        if lost > 0:
            improvements.append((name, was, now, lost))
    regressions.sort(key=lambda row: -row[3])
    improvements.sort(key=lambda row: -row[3])
    return regressions, improvements


def totals(counts: dict) -> dict:
    grand = {}
    for row in counts.values():
        for kind, number in row.items():
            grand[kind] = grand.get(kind, 0) + number
    return grand


def print_report(counts: dict) -> None:
    ranked = sorted(counts.items(), key=lambda kv: (-_flat(kv[1]), kv[0]))
    grand = totals(counts)
    print(f"{len(ranked)} files define a dialog without the themed frame "
          f"({sum(grand.values())} classes)\n")
    for name, row in ranked:
        print(f"{_flat(row):>6}  {name}")


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
        print("::error::New dialogs without the themed frame:")
        for name, was, now, gained in regressions:
            was_detail = ", ".join(f"{k} x{v}" for k, v in sorted(was.items())) or "none"
            now_detail = ", ".join(f"{k} x{v}" for k, v in sorted(now.items()))
            print(f"  {name}: {was_detail} -> {now_detail} (+{gained})")
        print("\nBuild the dialog with install_themed_frame from ui.dialogs.themed_dialog "
              "(or set Qt.WindowType.FramelessWindowHint yourself) so the window belongs to ASSella.")
        return 1

    now_totals, was_totals = totals(current), totals(baseline)
    print(f"No new unframed dialogs ({sum(now_totals.values())} classes left).")
    if improvements:
        for name, was, now, lost in improvements:
            print(f"  improved: {name} {_flat(was)} -> {_flat(now)} (-{lost})")
        print("  run --write to tighten the baseline.")
    if now_totals != was_totals and not improvements:
        print("  (counts moved without a clean per-file improvement; check for renames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
