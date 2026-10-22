#!/usr/bin/env python3
"""Test the manifest-dump helper that Browse Files is built on.

Preparing a dump is a chain of lookups: the newest Hubcap bundle, the depot key
from the bundle's LUA file or the local key database, the ``.manifest`` inside
the bundle, and the DepotDownloader command line built from them. Every failure
has to arrive as one message the dialog can show verbatim, so each stage is
pinned here.

The module is Qt-free and takes its directories as arguments, so the whole
chain can be driven against a scratch bundle - no Steam install, no network, no
display, no DepotDownloader.

    python3 scripts/test_manifest_dump.py
"""
import os
import subprocess
import sys
import tempfile
import zipfile
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


LUA = 'addappid(1234, 1, "aabbccdd11223344556677889900aabb")\n'
KEY = "aabbccdd11223344556677889900aabb"


def make_bundle(path, lua=None, members=None):
    """Write a Hubcap-shaped zip: an optional LUA file plus named members."""
    with zipfile.ZipFile(path, "w") as z:
        if lua is not None:
            z.writestr("app.lua", lua)
        for name, data in (members or {}).items():
            z.writestr(name, data)


def wrote_keys(temp_root, depot_id="1234"):
    return Path(temp_root) / f"selective_manifests_{depot_id}" / "depot.keys"


def main() -> int:
    from ui.dialogs.depot_selection import manifest_dump as md

    # ------------------------------------------------------ bundle lookup
    print("=== the newest Accela bundle wins ===")
    with tempfile.TemporaryDirectory() as tmp:
        hubcap = Path(tmp)
        plain = hubcap / "accela_fetch_1234.zip"
        variant = hubcap / "accela_fetch_1234_smart.zip"
        make_bundle(plain)
        make_bundle(variant)
        make_bundle(hubcap / "accela_fetch_9999.zip")

        os.utime(plain, (1000, 1000))
        os.utime(variant, (2000, 2000))
        check(md.find_manifest_bundle(1234, hubcap).name == "accela_fetch_1234_smart.zip",
              "the _smart variant is used when it is newer")
        os.utime(variant, (500, 500))
        check(md.find_manifest_bundle(1234, hubcap).name == "accela_fetch_1234.zip",
              "the plain bundle is used once it is newer")
        check(md.find_manifest_bundle("1234", hubcap).name == "accela_fetch_1234.zip",
              "an app id given as a string finds the same bundle")

        try:
            md.find_manifest_bundle(4321, hubcap)
            message = None
        except md.ManifestDumpError as e:
            message = str(e)
        check(message == f"No manifest zip file found for AppID 4321 in {hubcap}.",
              "a missing bundle raises the message the dialog shows")

    # -------------------------------------------------------- manifest id
    print("\n=== manifest id comes out of the bundled filename ===")
    check(md.manifest_id_from_name("1234_555000111.manifest") == "555000111",
          "'<depot>_<manifest>.manifest' -> the manifest id")
    check(md.manifest_id_from_name("555000111.manifest") == "555000111",
          "a bare '<manifest>.manifest' is its own id")
    check(md.manifest_id_from_name("1234_55_66.manifest") == "55_66",
          "only the first underscore separates depot from manifest")

    # ---------------------------------------------------------- happy path
    print("\n=== a bundle with a LUA key prepares a full dump ===")
    with tempfile.TemporaryDirectory() as tmp:
        hubcap = Path(tmp) / "hubcap_manifests"
        hubcap.mkdir()
        work = Path(tmp) / "work"
        make_bundle(hubcap / "accela_fetch_1234.zip", lua=LUA,
                    members={"1234_555000111.manifest": b"manifest-bytes"})

        dump = md.prepare_manifest_dump(1234, "1234", manifests_dir=hubcap, temp_root=str(work))

        check(dump.app_id == 1234 and dump.depot_id == "1234",
              "the dump carries the app and depot it was built for")
        check(dump.manifest_id == "555000111", "the manifest id names the listing")
        check(dump.manifest_file.endswith("1234_555000111.manifest"),
              "the manifest path points at the extracted .manifest")
        check(Path(dump.temp_dir) == work / "selective_manifests_1234",
              "scratch lives in one directory per app")
        check(Path(dump.keys_path).read_text() == f"1234;{KEY}\n",
              "the key is written in DepotDownloader's one-line format")
        check(dump.txt_path == os.path.join(dump.temp_dir, "manifest_1234_555000111.txt"),
              "the listing path is derived from depot and manifest id")
        check(dump.command("/usr/bin/dotnet", "/opt/dd/DepotDownloader.dll") == [
            "/usr/bin/dotnet", "/opt/dd/DepotDownloader.dll",
            "-app", "1234", "-depot", "1234", "-manifest", "555000111",
            "-manifestfile", dump.manifest_file, "-depotkeys", dump.keys_path,
            "-manifest-only", "-dir", dump.temp_dir,
        ], "the command line is DepotDownloader's manifest-only invocation")

    print("\n=== a bundle without the depot prefix still yields a manifest ===")
    with tempfile.TemporaryDirectory() as tmp:
        hubcap = Path(tmp) / "hubcap_manifests"
        hubcap.mkdir()
        make_bundle(hubcap / "accela_fetch_1234.zip", lua=LUA,
                    members={"555000111.manifest": b"x"})
        dump = md.prepare_manifest_dump(1234, "1234", manifests_dir=hubcap,
                                        temp_root=str(Path(tmp) / "work"))
        check(dump.manifest_id == "555000111",
              "an unprefixed .manifest is accepted when it is the only one")

    # -------------------------------------------------------- key fallback
    print("\n=== the depot key falls back to the local key database ===")
    with tempfile.TemporaryDirectory() as tmp:
        hubcap = Path(tmp) / "hubcap_manifests"
        hubcap.mkdir()
        work = str(Path(tmp) / "work")
        make_bundle(hubcap / "accela_fetch_1234.zip", members={"1234_7.manifest": b"x"})

        asked = []

        def key_lookup(depot_id):
            asked.append(depot_id)
            return "deadbeef" if depot_id == "1234" else None

        dump = md.prepare_manifest_dump(1234, "1234", manifests_dir=hubcap,
                                        temp_root=work, key_lookup=key_lookup)
        check(Path(dump.keys_path).read_text() == "1234;deadbeef\n",
              "a smart-generated bundle uses the cached key")
        check(asked == ["1234"], "the lookup is asked about the depot")

        try:
            md.prepare_manifest_dump(1234, "1234", manifests_dir=hubcap,
                                     temp_root=work, key_lookup=lambda depot: None)
            message = None
        except md.ManifestDumpError as e:
            message = str(e)
        check(message == "Could not find depot key for depot 1234 in LUA config or local key database.",
              "no key anywhere raises the message the dialog shows")

    print("\n=== the key can also arrive as a [depot] entry in the LUA file ===")
    with tempfile.TemporaryDirectory() as tmp:
        hubcap = Path(tmp) / "hubcap_manifests"
        hubcap.mkdir()
        make_bundle(hubcap / "accela_fetch_1234.zip", lua='["1234"] = "cafebabe"\n',
                    members={"1234_9.manifest": b"x"})
        dump = md.prepare_manifest_dump(1234, "1234", manifests_dir=hubcap,
                                        temp_root=str(Path(tmp) / "work"))
        check(Path(dump.keys_path).read_text() == "1234;cafebabe\n",
              "the [\"depot\"] = \"key\" form is understood too")

    # ------------------------------------------------------------ failures
    print("\n=== every other failure is one message, with no key left behind ===")
    with tempfile.TemporaryDirectory() as tmp:
        hubcap = Path(tmp) / "hubcap_manifests"
        hubcap.mkdir()
        # One scratch root per case, so a leftover extraction cannot make the
        # next case look like a success.
        work_a = str(Path(tmp) / "work_a")

        (hubcap / "accela_fetch_1234.zip").write_text("not a zip at all")
        try:
            md.prepare_manifest_dump(1234, "1234", manifests_dir=hubcap, temp_root=work_a)
            message = None
        except md.ManifestDumpError as e:
            message = str(e)
        check((message or "").startswith("Failed to extract manifest zip:"),
              "an unreadable bundle is reported as an extraction failure")
        check(not wrote_keys(work_a).exists(), "no key is written for an unreadable bundle")

        work_b = str(Path(tmp) / "work_b")
        make_bundle(hubcap / "accela_fetch_1234.zip", lua=LUA, members={"1234_7.manifest": b"x"})
        with zipfile.ZipFile(hubcap / "accela_fetch_1234.zip", "a") as z:
            z.writestr("../escaped.manifest", b"x")
        try:
            md.prepare_manifest_dump(1234, "1234", manifests_dir=hubcap, temp_root=work_b)
            message = None
        except md.ManifestDumpError as e:
            message = str(e)
        check((message or "").startswith("Failed to extract manifest zip:"),
              "a traversal entry in the bundle is refused")
        check(not (Path(tmp) / "escaped.manifest").exists(),
              "nothing is written outside the scratch directory")

        work_c = str(Path(tmp) / "work_c")
        make_bundle(hubcap / "accela_fetch_1234.zip", lua=LUA, members={"readme.txt": b"x"})
        try:
            md.prepare_manifest_dump(1234, "1234", manifests_dir=hubcap, temp_root=work_c)
            message = None
        except md.ManifestDumpError as e:
            message = str(e)
        check(message == "No manifest file (*.manifest) found for depot 1234 in the extracted manifest bundle.",
              "a bundle without a manifest raises the message the dialog shows")
        check(not wrote_keys(work_c).exists(),
              "the key written for that dump is cleaned up again")

        work_d = str(Path(tmp) / "work_d")
        wrote_keys(work_d).mkdir(parents=True)
        make_bundle(hubcap / "accela_fetch_1234.zip", lua=LUA, members={"1234_7.manifest": b"x"})
        try:
            md.prepare_manifest_dump(1234, "1234", manifests_dir=hubcap, temp_root=work_d)
            message = None
        except md.ManifestDumpError as e:
            message = str(e)
        check((message or "").startswith("Failed to write keys file:"),
              "an unwritable keys path is reported as such")

    print("\n=== discarding a keys file is safe to call twice ===")
    with tempfile.TemporaryDirectory() as tmp:
        keys = Path(tmp) / "depot.keys"
        keys.write_text("x")
        md.discard_keys_file(str(keys))
        md.discard_keys_file(str(keys))
        check(not keys.exists(), "the key file is gone and a second call is harmless")

    # ------------------------------------------------------------- purity
    print("\n=== manifest_dump stays Qt-free and importable on its own ===")
    # Loaded by path on purpose: importing it through the package would drag in
    # the dialog's Qt imports through __init__.py and prove nothing.
    module_path = SRC_DIR / "ui" / "dialogs" / "depot_selection" / "manifest_dump.py"
    probe = (
        "import importlib.util, sys; "
        f"sys.path.insert(0, r'{SRC_DIR}'); "
        f"spec = importlib.util.spec_from_file_location('manifest_dump_probe', r'{module_path}'); "
        "m = importlib.util.module_from_spec(spec); "
        "sys.modules['manifest_dump_probe'] = m; spec.loader.exec_module(m); "
        "assert 'PyQt6' not in sys.modules, sorted(k for k in sys.modules if 'PyQt' in k); "
        "print(m.ManifestDump.__name__)"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    check(proc.returncode == 0,
          "importing the module pulls in no Qt")
    if proc.returncode != 0:
        print(proc.stderr.strip())

    print()
    if _failures:
        print(f"FAILED ({len(_failures)} of {PASSED + len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"ALL MANIFEST DUMP TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
