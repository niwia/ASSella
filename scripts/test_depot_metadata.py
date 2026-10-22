#!/usr/bin/env python3
"""Test the depot-fact rules behind the depot dialog's metadata.

The metadata mixin both reads facts about depots and paints them into rows.
Everything on the reading side lives in ``depot_metadata``: the placeholder
descriptions that mean "not enriched yet", the byte size hidden in a branch's
manifest entry, merging enrichment records into depot dicts, resolving the
depots the local bundle is missing, and whether a manifest is already on disk.

The module is Qt-free and takes its data as arguments, so every rule is driven
against plain dicts - no database, no bundle, no display.

    python3 scripts/test_depot_metadata.py
"""
import subprocess
import sys
import tempfile
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


def main() -> int:
    from ui.dialogs.depot_selection import depot_metadata as dm

    # --------------------------------------------------- what still needs a name
    print("=== which descriptions count as 'no name yet' ===")
    check(dm.is_placeholder_desc("Depot 123") is True, "'Depot 123' is a placeholder")
    check(dm.is_placeholder_desc("DLC 45") is True, "'DLC 45' is a placeholder")
    check(dm.is_placeholder_desc("[LINUX] Depot 7") is True,
          "a placeholder keeps its platform tag")
    check(dm.is_placeholder_desc("  Depot 9  ") is True, "surrounding spaces do not hide it")
    check(dm.is_placeholder_desc("Half-Life 2") is False, "a real name is not a placeholder")
    check(dm.is_placeholder_desc("[DLC 5] Extra Mission") is False,
          "a tagged real name is not a placeholder")
    check(dm.is_placeholder_desc("") is False, "an empty description is not a placeholder")
    check(dm.is_placeholder_desc(None) is False, "None is not a placeholder")

    check(dm.needs_name({"desc": ""}) is True, "an empty description needs a name")
    check(dm.needs_name({}) is True, "a depot without a description needs a name")
    check(dm.needs_name({"desc": "Depot 12"}) is True, "a generic description needs a name")
    check(dm.needs_name({"desc": "Portal"}) is False, "a real description needs nothing")
    check(dm.needs_name("not a depot") is False, "a non-dict is never asked for a name")

    # --------------------------------------------------- what still needs a size
    print("=== which depots still lack a size ===")
    check(dm.needs_size({}) is True, "a depot without a size needs one")
    check(dm.needs_size({"size": 0}) is True, "a zero size does not count")
    check(dm.needs_size({"size": 1024}) is False, "a byte size counts")
    check(dm.needs_size({"size_str": "1.5 GB"}) is False, "a formatted size counts")

    # ------------------------------------------------------------- manifest size
    print("=== the byte size recorded for a depot ===")
    check(dm.manifest_size({"size": 500}) == 500, "the top-level size wins")
    check(dm.manifest_size({"size": "500"}) == 500, "a numeric string size is read")
    check(dm.manifest_size({"size": 0, "manifests": {"public": {"size": 900}}}, "public") == 900,
          "an empty top-level size falls through to the branch entry")
    check(dm.manifest_size({"manifests": {"beta": {"download": 700}, "public": {"size": 900}}}, "beta") == 700,
          "the branch asked for wins over public")
    check(dm.manifest_size({"manifests": {"public": {"size": 900}}}, "beta") == 900,
          "a missing branch falls back to public")
    check(dm.manifest_size({"manifests": {"alpha": {"size": 300}}}, "beta") == 300,
          "any branch that recorded a size beats none")
    check(dm.manifest_size({"manifests": {"alpha": {}}}, "beta") == 0,
          "an entry without a size answers zero")
    check(dm.manifest_size({"manifests": "nonsense"}) == 0, "a malformed manifests value answers zero")
    check(dm.manifest_size({}) == 0, "nothing recorded answers zero")
    check(dm.manifest_size(None) == 0, "a non-dict answers zero")

    # ----------------------------------------------------------- merge enrichment
    print("=== merging enrichment records into depot dicts ===")
    depots = {"123": {"desc": "Depot 123", "dlcappid": "777"}}
    dm.merge_enrichments(depots, {"777": {"name": "Extra Mission", "is_dlc": True, "size_bytes": 2048}})
    check(depots["123"]["desc"] == "[DLC] Extra Mission",
          "an enrichment keyed by the DLC app id reaches the depot that carries it")
    check(depots["123"]["name"] == "Extra Mission", "the plain name is kept alongside the tag")
    check(depots["123"]["size"] == 2048, "a missing size is filled in")
    check(depots["123"]["is_dlc"] is True, "a DLC depot is marked as one")

    depots = {"5": {"desc": "Soundtrack", "size": 4096}}
    dm.merge_enrichments(depots, {"5": {"name": "Soundtrack Deluxe", "size_bytes": 100}})
    check(depots["5"]["desc"] == "Soundtrack", "a description that already says something is kept")
    check(depots["5"]["size"] == 4096, "a size that is already known is kept")

    depots = {"9": {"desc": "Depot 9"}}
    dm.merge_enrichments(depots, {"9": {"name": "Redistributables", "oslist": "windows", "osarch": "64"}})
    check(depots["9"]["desc"] == "Redistributables",
          "a non-DLC depot takes the name without a DLC tag")
    check(depots["9"]["oslist"] == "windows" and depots["9"]["osarch"] == "64",
          "platform facts are filled in when missing")

    depots = {"9": {"desc": "Depot 9", "oslist": "linux"}}
    dm.merge_enrichments(depots, {"9": {"name": "Tool", "oslist": "windows"}})
    check(depots["9"]["oslist"] == "linux", "a known platform list is never overwritten")

    depots = {"1": {"desc": "Depot 1"}}
    dm.merge_enrichments(depots, {"1": "not a record", "2": {"name": "x"}})
    check(depots["1"]["desc"] == "Depot 1", "a malformed record changes nothing")
    check("2" not in depots, "a record for an unknown depot adds no depot")
    dm.merge_enrichments({}, {"1": {"name": "x"}})
    dm.merge_enrichments({"1": {}}, {})
    check(True, "empty inputs are harmless")

    # ------------------------------------------------- missing depot resolution
    print("=== describing the depots the bundle is missing ===")
    app_depots = {
        "111": {"size": 0, "manifests": {"public": {"size": 4096}}, "name": "Redist"},
        "222": {"desc": "Tools"},
    }
    cached = {"222": {"size_bytes": 512, "oslist": "windows"}}
    resolved, to_fetch = dm.resolve_missing_metadata(
        ["111", "222", "333", "444"],
        app_depots=app_depots,
        cached_enrichments=cached,
        ini_names={"333": "Soundtrack"},
        branch="public",
    )
    check(resolved["111"] == {"size": 4096, "name": "Redist"},
          "the database depot supplies both size and name")
    check(resolved["222"].get("desc") == "Tools" and resolved["222"].get("size") == 512,
          "the enrichment cache fills what the database left")
    check(resolved["333"] == {"name": "Soundtrack"},
          "the bundled ini supplies a name when nothing else does")
    check(resolved["444"] == {}, "a depot nothing knows stays empty")
    check(to_fetch == ["222", "333", "444"],
          "only depots still missing a name or a size need the network")

    resolved, to_fetch = dm.resolve_missing_metadata(
        ["111"], app_depots={}, cached_enrichments={}, ini_names={}, branch="public")
    check(to_fetch == ["111"], "a depot with nothing known needs the network")

    # ------------------------------------------------------ manifest on disk
    print("=== whether a manifest is already saved ===")
    with tempfile.TemporaryDirectory() as tmp:
        search = Path(tmp)
        check(dm.manifest_on_disk("1", "2", dirs=[search]) is False,
              "an unsaved manifest is not on disk")
        (search / "1_2.manifest").write_bytes(b"manifest")
        check(dm.manifest_on_disk("1", "2", dirs=[search]) is True,
              "a saved manifest is found")
        check(dm.manifest_on_disk("1", "3", dirs=[search]) is False,
              "a different manifest id is not the same file")

    # ------------------------------------------------------------- Qt-free
    print("=== the module stays Qt-free and importable on its own ===")
    # Loaded by path on purpose: importing it through the package would drag in
    # the dialog's Qt imports through __init__.py and prove nothing.
    module_path = SRC_DIR / "ui" / "dialogs" / "depot_selection" / "depot_metadata.py"
    probe = (
        "import importlib.util, sys; "
        f"sys.path.insert(0, r'{SRC_DIR}'); "
        f"spec = importlib.util.spec_from_file_location('depot_metadata_probe', r'{module_path}'); "
        "m = importlib.util.module_from_spec(spec); "
        "sys.modules['depot_metadata_probe'] = m; spec.loader.exec_module(m); "
        "assert 'PyQt6' not in sys.modules, sorted(k for k in sys.modules if 'PyQt' in k); "
        "print(m.manifest_size({'size': 1}))"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    check(proc.returncode == 0, "importing the module pulls in no Qt")
    if proc.returncode != 0:
        print(proc.stderr.strip())

    print()
    if _failures:
        print(f"FAILED ({len(_failures)} of {PASSED + len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"ALL DEPOT METADATA TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
