#!/usr/bin/env python3
"""Test the rule that decides which window a library action's dialog sits on.

Game-library actions raise dialogs - the depot selector, the recommended-build
prompt, the build list - and when the game-details window is open the action was
started from it. A dialog centred on the library window *behind* the details
window can end up out of sight, so every one of them asks the same helper which
window to sit on.

The details window is a preference, never a requirement: the rule has to fall
back to the library window so a direct action from the Steam Library tab still
works (the "Library Action Dialog Independence" invariant in AGENTS.md). That
fallback is what keeps this fix from turning details-dialog users into a
privileged path.

    python3 scripts/test_library_actions.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.gettempdir())

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
    from PyQt6.QtWidgets import QApplication

    from ui.dialogs.library.actions import LibraryActionsMixin

    QApplication.instance() or QApplication([])

    source = (SRC_DIR / "ui" / "dialogs" / "library" / "actions.py").read_text()

    # --------------------------------------------------------------------- rule
    print("=== which window a library dialog sits on ===")

    class Library:
        _details_dialog = None

    library = Library()
    check(LibraryActionsMixin._dialog_parent(library) is library,
          "with no details window open the dialog sits on the library window")

    details = object()
    library._details_dialog = details
    check(LibraryActionsMixin._dialog_parent(library) is details,
          "with the details window open the dialog sits on it instead")

    library._details_dialog = None
    check(LibraryActionsMixin._dialog_parent(library) is library,
          "a closed details window falls back to the library window")

    # ----------------------------------------------------- one rule, all sites
    print("=== every dialog in the mixin uses that one rule ===")
    check("def _dialog_parent" in source, "the mixin defines the rule once")
    check("self._details_dialog or self" in source,
          "the details window stays a preference, not a requirement")
    check("parent=self," not in source,
          "no dialog is parented straight to the library window any more")
    sites = source.count("self._dialog_parent()")
    check(sites >= 4, f"all {sites} dialog-raising sites ask the rule")

    # The depot selector is passed its parent positionally, so make sure that
    # call was not missed by only ever looking for the keyword form.
    check("self._dialog_parent()," in source,
          "the depot selection dialog's positional parent uses the rule too")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)} of {PASSED + len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"ALL LIBRARY ACTION TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
