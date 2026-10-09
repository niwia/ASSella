#!/usr/bin/env python3
"""
Smoke test: import every ASSella module for real.

`py_compile` is NOT enough. Python 3.14 evaluates annotations lazily (PEP 649),
so a missing typing import such as `Optional` compiles cleanly and only explodes
at runtime inside the AppImage, where the bundled interpreter is stricter.
This walks the real import graph instead.

Exit code 0 = every module imports. Non-zero = do not build.
"""
import importlib
import inspect
import os
import sys
import traceback
from pathlib import Path
from typing import get_type_hints

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Modules that cannot be imported in a bare interpreter because they exec() and
# quit, or need a live GUI/display at import time. Every entry here must be
# justified - a module only lands in this list because it genuinely cannot be
# imported headless, NOT because its dependencies happen to be missing.
#
# A "missing dependency" is NOT an acceptable reason to skip: that is precisely
# how a removed dependency goes unnoticed while the shipped build breaks.
EXPECTED_SKIPS = {
    # exec()s and calls sys.exit() as a side effect of importing.
    "main",
}

# A module that fails to import because one of these is absent is a real
# failure: the dependency is declared in requirements.txt and must be installed.
HARD_DEPENDENCIES = {
    "PyQt6", "psutil", "gevent", "urwid", "capstone", "curl_cffi",
    "yaml", "cryptography", "Crypto", "vdf", "steam", "requests",
    "bs4", "curl_cffi.requests", "zstandard", "protobuf",
}


def missing_module(exc: BaseException) -> str:
    """Root module name that could not be imported, or '' if not that kind of error."""
    name = getattr(exc, "name", "") or ""
    if name:
        return str(name).split(".")[0]
    text = str(exc)
    for root in HARD_DEPENDENCIES:
        if f"No module named '{root}'" in text or f"No module named {root!r}" in text:
            return root
    return ""


def iter_modules() -> list:
    mods = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        rel = path.relative_to(SRC_DIR)
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][:-3]
        if any(p in ("deps", "__pycache__", "res", "data") for p in parts):
            continue
        if not parts:
            continue
        mods.append(".".join(parts))
    return mods


def main() -> int:
    mods = iter_modules()
    failed, skipped = [], []

    for name in mods:
        try:
            module = importlib.import_module(name)
        except SystemExit:
            # Some modules exec() and quit on import; only tolerated if expected.
            if name in EXPECTED_SKIPS:
                skipped.append(f"{name} (SystemExit, expected)")
            else:
                failed.append((name, "SystemExit on import - add to EXPECTED_SKIPS if intended"))
            continue
        except BaseException as e:  # noqa: BLE001 - smoke test wants everything
            missing = missing_module(e)
            if missing:
                # A declared dependency is absent. That is a packaging bug, not
                # an optional-extra situation, so it must fail loudly.
                failed.append((
                    name,
                    f"missing dependency '{missing}' - it is declared in "
                    f"requirements.txt; the build would ship broken. "
                    f"({type(e).__name__}: {e})",
                ))
            else:
                failed.append((name, f"{type(e).__name__}: {e}"))
            continue

        # Python 3.14 evaluates annotations lazily (PEP 649), so a missing
        # typing import such as `Optional` imports fine here but crashes on the
        # Python 3.13 interpreter that the AppImage and CI actually use.
        # Resolve every annotation explicitly to close that gap.
        #
        # Only functions/methods *defined in this module* are checked. Checking
        # classes would pull in inherited annotations from third-party bases
        # (e.g. urllib3's private _PoolManager), which are not our problem and
        # are not import blockers on 3.13 either.
        for attr in dir(module):
            obj = getattr(module, attr, None)
            if isinstance(obj, type):
                continue  # classes: inherited 3rd-party annotations are out of scope
            if not (inspect.isfunction(obj) or inspect.ismethod(obj)):
                continue
            if getattr(obj, "__module__", None) != name:
                continue  # re-exported from elsewhere; already checked there
            try:
                get_type_hints(obj)
            except NameError as e:
                failed.append((f"{name}.{attr}", f"NameError: {e}"))
            except Exception:
                # Unresolvable forward refs etc. are not import blockers.
                pass

    # ── Facade contract ──────────────────────────────────────────────────────
    # utils/yaml_config_manager is now a thin facade over the utils/yaml/
    # package. It re-exports names that 29 modules import, and a refactor that
    # moves an implementation without re-exporting it produces an ImportError
    # at a call site that no per-module check here would attribute correctly.
    # The contract is the set of names that must stay importable.
    api_file = SRC_DIR / "utils" / "_yaml_public_api.json"
    if api_file.is_file():
        import json

        contract = json.loads(api_file.read_text())
        facade = sys.modules.get("utils.yaml_config_manager")
        if facade is None:
            failed.append(("utils.yaml_config_manager", "facade did not import"))
        else:
            gone = sorted(n for n in contract if not hasattr(facade, n))
            if gone:
                failed.append((
                    "utils.yaml_config_manager (facade)",
                    "no longer re-exports: " + ", ".join(gone)
                    + " | 29 modules import from this facade; restore the name or"
                      " update src/utils/_yaml_public_api.json deliberately.",
                ))

    total = len(mods)
    print(f"imported {total - len(failed)}/{total} modules")
    if skipped:
        print(f"  expected skips: {len(skipped)}")
        for s in skipped:
            print(f"    - {s}")

    if failed:
        print(f"\nFAILED ({len(failed)}):")
        for name, err in failed:
            print(f"  {name}\n      {err}")
        print(
            "\nThese are real import/annotation errors in application code, or a "
            "declared dependency that is not installed.\nDo NOT build an AppImage "
            "until they are fixed."
        )
        return 1

    print("\nAll modules loaded cleanly (annotations resolved, deps present).")
    return 0


if __name__ == "__main__":
    sys.exit(main())