#!/usr/bin/env python3
"""Test depot-file reconciliation behind the update checker.

The checker compares the depot file ACCELA wrote against Steam. A depot the
current install was not downloaded with (an inherited Windows depot on a Linux
install) or a manifest id from before the last update stays in that file
forever, and then the game reports "update available" permanently even though
every selected depot matches.

`depot_reconcile` fixes that from two pieces of better evidence:
`.DepotDownloader/metadata.json` (which depots this install was made with) and
the manifests actually written into `.DepotDownloader`. These tests drive the
rules against a temp install dir - no Qt, no network, no user data.

    python3 scripts/test_depot_reconcile.py
"""
import json
import os
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


def make_install(root: Path, selected, manifests):
    """Build a fake install dir: .DepotDownloader/metadata.json + manifest files."""
    ddm = root / ".DepotDownloader"
    ddm.mkdir(parents=True, exist_ok=True)
    meta = {"buildid": "1234", "selected_depots_list": [str(d) for d in selected]}
    (ddm / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    for depot_id, manifest_id in manifests.items():
        f = ddm / f"{depot_id}_{manifest_id}.manifest"
        f.write_text("{}", encoding="utf-8")
    return root


def main() -> int:
    from core.tasks.depot_reconcile import (
        installed_manifest_id,
        reconcile_saved_depots,
        write_depot_file,
    )
    print("\n[1] the reported bug: an inherited depot in the depot file")
    tmp = Path(tempfile.mkdtemp(prefix="depotreconcile_"))
    install = make_install(
        tmp / "jackbox3",
        selected=["434171", "434173"],
        manifests={"434171": "7719", "434173": "6284"},
    )
    saved = {"434171": "7719", "434173": "6284", "2828508": "5145"}
    reconciled, changed = reconcile_saved_depots(saved, str(install))
    check(changed, "reconciliation reports a change")
    check("2828508" not in reconciled, "the unselected depot is dropped")
    check(reconciled == {"434171": "7719", "434173": "6284"}, "selected depots kept as-is")
    check("2828508" in saved, "the caller's dict is not mutated in place")

    print("\n[2] a pre-update manifest id is replaced by the written manifest")
    install2 = make_install(
        tmp / "bundle",
        selected=["100", "200"],
        manifests={"100": "new100", "200": "new200"},
    )
    saved2 = {"100": "old100", "200": "new200"}
    reconciled2, changed2 = reconcile_saved_depots(saved2, str(install2))
    check(changed2, "reports a change")
    check(reconciled2["100"] == "new100", "manifest id taken from disk")
    check(reconciled2["200"] == "new200", "matching depot untouched")
    check(installed_manifest_id(str(install2), "100") == "new100", "installed_manifest_id reads the file")
    check(installed_manifest_id(str(install2), "999") is None, "missing depot has no manifest")
    check(installed_manifest_id(None, "100") is None, "no install path is harmless")

    print("\n[3] newest manifest wins when several are on disk")
    install3 = make_install(tmp / "twice", selected=["300"], manifests={"300": "first300"})
    ddm3 = install3 / ".DepotDownloader"
    newer = ddm3 / "300_second300.manifest"
    newer.write_text("{}", encoding="utf-8")
    old = ddm3 / "300_first300.manifest"
    past = time.time() - 3600
    os.utime(old, (past, past))
    check(installed_manifest_id(str(install3), "300") == "second300", "latest by mtime")

    print("\n[4] evidence that would empty the file is ignored")
    install4 = make_install(tmp / "dlc", selected=["400"], manifests={"400": "m400"})
    saved4 = {"900": "m900"}  # a depot the metadata does not mention
    reconciled4, changed4 = reconcile_saved_depots(saved4, str(install4))
    check(reconciled4 == saved4, "a depot the selection does not cover is kept, not dropped")
    check(not changed4, "nothing to rewrite")

    print("\n[5] no metadata.json: manifest ids still reconcile")
    install5 = tmp / "nometa"
    ddm5 = install5 / ".DepotDownloader"
    ddm5.mkdir(parents=True)
    (ddm5 / "500_m500.manifest").write_text("{}", encoding="utf-8")
    reconciled5, changed5 = reconcile_saved_depots({"500": "stale"}, str(install5))
    check(reconciled5 == {"500": "m500"} and changed5, "stale id replaced without metadata")

    print("\n[6] missing install path leaves the caller alone")
    check(reconcile_saved_depots({"1": "a"}, None) == ({"1": "a"}, False), "no install path")
    check(
        reconcile_saved_depots({"1": "a"}, str(tmp / "does-not-exist")) == ({"1": "a"}, False),
        "nonexistent install path",
    )
    check(reconcile_saved_depots({}, str(install)) == ({}, False), "empty depot file")

    print("\n[7] rewritten depot file keeps the canonical format")
    depot_file = tmp / "depots" / "434170.depot"
    write_depot_file(depot_file, {"434173": "6284", "434171": "7719"})
    text = depot_file.read_text()
    check(text == "434171: 7719\n434173: 6284\n", f"numeric order, no extra keys: {text!r}")
    back = {}
    for line in text.splitlines():
        did, mid = line.split(":", 1)
        back[did.strip()] = mid.strip()
    check(back == {"434171": "7719", "434173": "6284"}, "round-trips through the depot-file reader")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
        return 1
    print(f"ALL DEPOT RECONCILE TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
