#!/usr/bin/env python3
"""Test finding the game's launch executable and the depot that carries it.

The depot dialog refuses to accept a selection that leaves out the depot
holding the file Steam launches. Three things have to line up for that: the
executable's name (from the game record or from Steam's own appinfo cache), the
depot named by the manifest whose bytes mention that file, and the search order
that prefers the freshly fetched bundle over loose manifests. None of that is a
widget, so all of it is driven here against a synthetic appinfo cache and
synthetic manifest bundles - no Steam install, no display, no network.

    python3 scripts/test_executable_probe.py
"""
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ui.dialogs.depot_selection import executable_probe as ep

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


def appinfo_entry(appid, payload):
    """One appinfo.vdf record: id, total size, 48 bytes of fields, then the blob."""
    return struct.pack("<I", appid) + struct.pack("<I", 60 + len(payload)) + b"\x00" * 48 + payload


def write_appinfo(path, entries):
    """A cache with a 16-byte header, the given records, and the zero terminator."""
    path.write_bytes(b"\x00" * 16 + b"".join(entries) + struct.pack("<I", 0) + b"\x00" * 12)


def make_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def main():
    print("=== the launch executable's name ===")
    check(
        ep.launch_executable("1234", {"executable": "bin/game.exe"}) == "bin/game.exe",
        "the game record's 'executable' is the name",
    )
    check(
        ep.launch_executable("1234", {"launch_executable": "bin/other.exe"}) == "bin/other.exe",
        "the game record's 'launch_executable' is read too",
    )
    check(
        ep.launch_executable("1234", {"executable": "a.exe", "launch_executable": "b.exe"}) == "a.exe",
        "'executable' wins when the record has both",
    )
    check(ep.launch_executable("1234", None, paths=[]) is None, "no record and no cache means no name")
    check(ep.launch_executable("1234", "not a mapping", paths=[]) is None, "a record that is not a mapping is ignored")
    check(ep.launch_executable("1234", {"executable": ""}, paths=[]) is None, "an empty name is not a name")

    print("=== the bare file name ===")
    check(ep.executable_filename("bin/win/Game.EXE") == "game.exe", "directory and case are dropped")
    check(ep.executable_filename("game.exe") == "game.exe", "a bare name is kept")
    check(ep.executable_filename("") == "", "empty stays empty")
    check(ep.executable_filename(None) == "", "nothing is not a name")
    check(
        ep.executable_filename("bin\\win\\Game.EXE") == "bin\\win\\game.exe",
        "a backslash is not a separator here - the appinfo reader normalises first",
    )

    print("=== Steam's appinfo cache ===")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        cache = tmp / "appinfo.vdf"
        wanted = appinfo_entry(1111, b"executable\x00bin\\win\\Game.EXE\x00")
        other = appinfo_entry(2222, b"name\x00Some Game\x00")
        write_appinfo(cache, [other, wanted])

        check(
            ep.find_executable_in_appinfo_vdf("1111", [cache]) == "bin/win/Game.EXE",
            "the app's executable is read, with backslashes normalised",
        )
        check(
            ep.find_executable_in_appinfo_vdf(2222, [cache]) is None,
            "an app with no executable key yields nothing",
        )
        check(
            ep.find_executable_in_appinfo_vdf("3333", [cache]) is None,
            "an app that is not in the cache yields nothing",
        )
        check(
            ep.find_executable_in_appinfo_vdf("not-a-number", [cache]) is None,
            "an app id that is not a number yields nothing",
        )
        check(
            ep.find_executable_in_appinfo_vdf("1111", [tmp / "missing.vdf"]) is None,
            "a cache that is not there is skipped",
        )
        check(ep.find_executable_in_appinfo_vdf("1111", [tmp]) is None, "a directory is not a cache")

        empty = tmp / "empty.vdf"
        empty.write_bytes(b"")
        check(ep.find_executable_in_appinfo_vdf("1111", [empty]) is None, "an empty cache yields nothing")

        truncated = tmp / "truncated.vdf"
        truncated.write_bytes(b"\x00" * 20)
        check(ep.find_executable_in_appinfo_vdf("1111", [truncated]) is None, "a truncated cache yields nothing")

        junk = tmp / "junk.vdf"
        junk.write_bytes(b"not a vdf at all, really")
        check(ep.find_executable_in_appinfo_vdf("1111", [junk]) is None, "junk yields nothing, not a crash")

        after_end = tmp / "after_end.vdf"
        write_appinfo(after_end, [struct.pack("<I", 0) + b"\x00" * 20, wanted])
        check(
            ep.find_executable_in_appinfo_vdf("1111", [after_end]) is None,
            "records after the zero terminator are not read",
        )

        earlier = tmp / "earlier.vdf"
        write_appinfo(earlier, [wanted, other])
        check(
            ep.find_executable_in_appinfo_vdf("2222", [earlier]) is None,
            "an app entry without the key does not stop the walk",
        )
        check(
            ep.launch_executable("1111", None, paths=[cache]) == "bin/win/Game.EXE",
            "with no game record the cache supplies the name",
        )
        check(
            ep.launch_executable("1111", {"executable": "bin/from-record.exe"}, paths=[cache])
            == "bin/from-record.exe",
            "the game record wins over the cache",
        )

    print("=== the depot that carries the file ===")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bundles = tmp / "hubcap_manifests"
        bundles.mkdir()
        loose = tmp / "depotcache"
        loose.mkdir()

        bundle = bundles / "accela_fetch_1234.zip"
        make_zip(
            bundle,
            {
                "400_500.manifest": b"\x00\x01 GAME.EXE \x00",
                "401_501.manifest": b"\x00\x01 other.dll \x00",
                "readme.txt": b"GAME.EXE",
            },
        )
        check(
            ep.depot_with_executable("1234", "game.exe", zips=[bundle], dirs=[loose]) == "400",
            "the manifest whose bytes mention the file gives up its depot id",
        )
        check(
            ep.depot_with_executable("1234", "game.zip", zips=[bundle], dirs=[loose]) is None,
            "a file no manifest mentions gives nothing",
        )
        check(ep.depot_with_executable("1234", "", zips=[bundle], dirs=[loose]) is None, "no name means no search")

        only_readme = bundles / "accela_fetch_7777.zip"
        make_zip(
            only_readme,
            {"400_500.manifest": b"\x00\x01 other.dll \x00", "readme.txt": b"game.exe"},
        )
        check(
            ep.depot_with_executable("7777", "game.exe", zips=[only_readme], dirs=[]) is None,
            "only manifest members are searched, not every file in the bundle",
        )

        (loose / "600_700.manifest").write_bytes(b"\x00 game.exe \x00")
        check(
            ep.depot_with_executable("1234", "game.exe", zips=[], dirs=[loose]) == "600",
            "a loose manifest gives up its depot id",
        )
        check(
            ep.depot_with_executable("1234", "game.exe", zips=[bundle], dirs=[loose]) == "400",
            "the fetched bundle is searched before the loose manifests",
        )

        only_multi = tmp / "only_multi"
        only_multi.mkdir()
        (only_multi / "810_900_extra.manifest").write_bytes(b"\x00 game.exe \x00")
        check(
            ep.depot_with_executable("1234", "game.exe", zips=[], dirs=[only_multi]) == "810",
            "the depot id is the part before the first underscore",
        )

        broken = bundles / "accela_fetch_9999.zip"
        broken.write_bytes(b"this is not a zip")
        check(
            ep.depot_with_executable("9999", "game.exe", zips=[broken, bundle], dirs=[]) == "400",
            "an unreadable bundle is skipped, not fatal",
        )
        check(
            ep.depot_with_executable("1234", "game.exe", zips=[tmp / "nope.zip"], dirs=[]) is None,
            "a bundle that is not there is skipped",
        )
        check(
            ep.depot_with_executable("1234", "game.exe", zips=[], dirs=[tmp / "nope"]) is None,
            "a directory that is not there is skipped",
        )

        print("=== the real search locations ===")
        saved_zip, saved_dirs = ep.hubcap_zip_candidates, ep.manifest_search_dirs
        ep.hubcap_zip_candidates = lambda app_id: [bundle]
        ep.manifest_search_dirs = lambda: [loose]
        try:
            check(
                ep.depot_with_executable("1234", "game.exe") == "400",
                "with nothing injected the fetched bundle is where the search starts",
            )
            (loose / "620_720.manifest").write_bytes(b"\x00 special.dll \x00")
            check(
                ep.depot_with_executable("1234", "special.dll") == "620",
                "with nothing injected the loose manifests are the fallback",
            )
        finally:
            ep.hubcap_zip_candidates, ep.manifest_search_dirs = saved_zip, saved_dirs

        candidates = ep.hubcap_zip_candidates("1234")
        check(
            len(candidates) == 2 and candidates[0].name == "accela_fetch_1234.zip",
            "the bundle candidates are named for the app",
        )
        check(
            [p.name for p in ep.manifest_search_dirs()] == ["manifests", "depotcache"],
            "the loose manifests are searched in ours and Steam's cache",
        )

    print("=== the module stays Qt-free and importable on its own ===")
    # Loaded by path on purpose: importing it through the package would drag in
    # the dialog's Qt imports through __init__.py and prove nothing.
    module_path = SRC_DIR / "ui" / "dialogs" / "depot_selection" / "executable_probe.py"
    probe = (
        "import importlib.util, sys; "
        f"sys.path.insert(0, r'{SRC_DIR}'); "
        f"spec = importlib.util.spec_from_file_location('executable_probe_probe', r'{module_path}'); "
        "m = importlib.util.module_from_spec(spec); "
        "sys.modules['executable_probe_probe'] = m; spec.loader.exec_module(m); "
        "assert 'PyQt6' not in sys.modules, sorted(k for k in sys.modules if 'PyQt' in k); "
        "print(m.executable_filename('a/B.EXE'))"
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
    print(f"ALL EXECUTABLE PROBE TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
