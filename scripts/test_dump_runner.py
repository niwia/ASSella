#!/usr/bin/env python3
"""Test the Qt half of Browse Files: running a prepared dump.

``test_manifest_dump`` covers everything up to the command line. Something then
has to run DepotDownloader without freezing the dialog, notice when it exits
without writing the listing, and clean the depot key up on every failure. That
is ``ManifestDumpRunner``, and it is driven here with a fake DepotDownloader:
no dotnet, no network, no display.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_dump_runner.py
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

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


def make_dump(tmp, write_listing):
    """A dump as ``prepare_manifest_dump`` would hand it over."""
    from ui.dialogs.depot_selection.manifest_dump import ManifestDump

    temp_dir = str(Path(tmp) / "selective_manifests_1234")
    os.makedirs(temp_dir, exist_ok=True)
    keys_path = os.path.join(temp_dir, "depot.keys")
    Path(keys_path).write_text("1234;aabbccdd\n")
    txt_path = os.path.join(temp_dir, "manifest_1234_7.txt")
    if write_listing:
        Path(txt_path).write_text("file listing\n")
    return ManifestDump(
        app_id=1234,
        depot_id="1234",
        manifest_id="7",
        manifest_file=os.path.join(temp_dir, "1234_7.manifest"),
        keys_path=keys_path,
        temp_dir=temp_dir,
        txt_path=txt_path,
    )


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.gettempdir())

    from PyQt6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])

    import utils.helpers as helpers
    from ui.dialogs.depot_selection.dump_runner import ManifestDumpRunner

    # The runner is not supposed to need a real dotnet or a real resource tree.
    helpers.get_dotnet_path = lambda: "/nonexistent/dotnet"
    helpers.resource_path = lambda rel: rel

    calls = []
    failures = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if failures:
            raise RuntimeError(failures.pop(0))
        return subprocess.CompletedProcess(cmd, 0)

    subprocess.run = fake_run

    def drive(dump, timeout=10.0):
        parent = QWidget()
        outcome = []
        runner = ManifestDumpRunner(parent, dump)
        runner.finished.connect(lambda ok, message: outcome.append((ok, message)))
        runner.start()
        deadline = time.time() + timeout
        while not outcome and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        check(bool(outcome), "the runner reported back")
        return (outcome[0] if outcome else None), runner

    print("\n=== a written listing comes back as success ===")
    with tempfile.TemporaryDirectory() as tmp:
        dump = make_dump(tmp, write_listing=True)
        result, _ = drive(dump)
        check(result == (True, ""), f"success is reported as (True, '') ({result!r})")
        check(os.path.exists(dump.keys_path),
              "the key survives a success - the file tree still needs it")
        check(calls and calls[-1] == dump.command("/nonexistent/dotnet", "deps/DepotDownloader.dll"),
              "DepotDownloader is started with the prepared command line")

    print("\n=== a clean exit without a listing is still a failure ===")
    with tempfile.TemporaryDirectory() as tmp:
        dump = make_dump(tmp, write_listing=False)
        result, _ = drive(dump)
        check(result == (False, "Failed to locate generated file list text file."),
              f"a missing listing gets its own sentence ({result!r})")
        check(not os.path.exists(dump.keys_path),
              "the key is cleaned up when the listing never appeared")

    print("\n=== a failing DepotDownloader reports its error ===")
    with tempfile.TemporaryDirectory() as tmp:
        dump = make_dump(tmp, write_listing=False)
        failures.append("boom")
        result, _ = drive(dump)
        check(result is not None and result[0] is False
              and result[1] == "Failed to load file list: boom",
              f"the exception text is passed on ({result!r})")
        check(not os.path.exists(dump.keys_path),
              "the key is cleaned up after a failure too")

    print("\n=== the runner belongs to the dialog that made it ===")
    with tempfile.TemporaryDirectory() as tmp:
        dump = make_dump(tmp, write_listing=True)
        parent = QWidget()
        runner = ManifestDumpRunner(parent, dump)
        check(runner.parent() is parent, "the runner is parented to the dialog")
        check(runner.dump is dump, "the runner keeps the dump it was given")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)} of {PASSED + len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"ALL DUMP RUNNER TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
