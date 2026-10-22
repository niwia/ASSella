#!/usr/bin/env python3
"""Fail when a window is built from a native Qt dialog instead of the themed one.

Every window in ASSella must be ASSella's own: the native dialogs below paint
themselves in the desktop's GTK/KDE theme, with the desktop's window
decorations, and in a dark-theme session they look like another application's
window sitting on top of this one.

    QMessageBox          -> ui.dialogs.themed_message_box.ThemedMessageBox
    QProgressDialog      -> ui.dialogs.themed_progress_dialog.ThemedProgressDialog
    QFileDialog          -> a themed file picker
    QInputDialog         -> a themed prompt
    QColorDialog         -> a themed colour picker
    QFontDialog          -> a themed font picker
    QErrorMessage        -> a themed error report

The sites already in the tree are frozen in
``scripts/native_dialogs_baseline.json``. This gate fails when a file gains
one, and notes when the baseline can be tightened, so the migration can land
one module per commit without the rest of the tree blocking it.

    python3 scripts/check_native_dialogs.py            # the CI gate
    python3 scripts/check_native_dialogs.py --report   # ranked inventory
    python3 scripts/check_native_dialogs.py --write    # refresh baseline

Only direct calls and constructors are seen (``QMessageBox.warning(...)``,
``QFileDialog(...)``), including through an import alias
(``from PyQt6.QtWidgets import QMessageBox as MB``). Enum members
(``QMessageBox.StandardButton.Yes``) are not calls and are ignored, and so are
the themed replacements themselves.
"""
import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO_ROOT / "src"
DEFAULT_BASELINE = REPO_ROOT / "scripts" / "native_dialogs_baseline.json"

# The native class -> the name of the kind it counts as.
NATIVE_CLASSES = {
    "QMessageBox": "message_box",
    "QProgressDialog": "progress_dialog",
    "QFileDialog": "file_dialog",
    "QInputDialog": "input_dialog",
    "QColorDialog": "color_dialog",
    "QFontDialog": "font_dialog",
    "QErrorMessage": "error_message",
}

# The replacements themselves are allowed to name the native classes.
ALLOWED_FILES = {
    # The themed wrappers themselves: they embed the native widget on purpose.
    "ui/dialogs/themed_message_box.py",
    "ui/dialogs/themed_progress_dialog.py",
    "ui/dialogs/themed_file_dialog.py",
    "ui/dialogs/themed_color_dialog.py",
}

# Vendored code is not ours to rewrite.
SKIP_DIRS = {"deps", "__pycache__"}


def _aliases(tree: ast.Module) -> dict:
    """Names that refer to a native class, following ``import ... as``."""
    aliases = dict(NATIVE_CLASSES)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "PyQt6.QtWidgets":
            continue
        for imported in node.names:
            kind = NATIVE_CLASSES.get(imported.name)
            if kind:
                aliases[imported.asname or imported.name] = kind
    return aliases


def _callee_kind(func: ast.expr, aliases: dict):
    """The native kind a call names, or None for everything else."""
    if isinstance(func, ast.Name):
        return aliases.get(func.id)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return aliases.get(func.value.id)
    return None


def analyse(root: Path) -> dict:
    """Count native dialog uses per file, by kind."""
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
        aliases = _aliases(tree)
        found = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kind = _callee_kind(node.func, aliases)
            if kind:
                found[kind] = found.get(kind, 0) + 1
        if found:
            counts[rel] = found
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
    print(f"{len(ranked)} files still build a native window ({sum(grand.values())} sites)\n")
    print("by kind:")
    for kind, number in sorted(grand.items(), key=lambda kv: -kv[1]):
        print(f"{number:>6}  {kind}")
    print(f"\n{'sites':>6}  file")
    for name, row in ranked:
        detail = ", ".join(f"{kind} x{number}" for kind, number in sorted(row.items()))
        print(f"{_flat(row):>6}  {name}  ({detail})")


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
        print("::error::New native dialogs:")
        for name, was, now, gained in regressions:
            was_detail = ", ".join(f"{k} x{v}" for k, v in sorted(was.items())) or "none"
            now_detail = ", ".join(f"{k} x{v}" for k, v in sorted(now.items()))
            print(f"  {name}: {was_detail} -> {now_detail} (+{gained})")
        print("\nUse the themed dialog instead (ThemedMessageBox, ThemedProgressDialog, ...) "
              "so the window belongs to ASSella.")
        return 1

    now_totals, was_totals = totals(current), totals(baseline)
    print(f"No new native dialogs ({sum(now_totals.values())} sites left: "
          + ", ".join(f"{kind} {number}" for kind, number in sorted(now_totals.items(), key=lambda kv: -kv[1]))
          + ").")
    if improvements:
        for name, was, now, lost in improvements:
            print(f"  improved: {name} {_flat(was)} -> {_flat(now)} (-{lost})")
        print("  run --write to tighten the baseline.")
    if now_totals != was_totals and not improvements:
        print("  (counts moved without a clean per-file improvement; check for renames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
