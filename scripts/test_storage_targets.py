#!/usr/bin/env python3
"""Test the storage-target rules both depot dialogs pick drives with.

The picker at the bottom of the depot dialogs shows buttons and a dropdown, but
nothing it decides is about widgets: which drives are worth offering, how each
one reads, which starts selected, and whether the game already sits on one of
them. Those answers live in ``storage_targets`` and are pinned here, including
the places where the single-depot and multi-depot dialogs differ on purpose.

The module is Qt-free and takes its directories as arguments, so every rule can
be driven against fake drives - no Steam install, no display, no real disks.

    python3 scripts/test_storage_targets.py
"""
import subprocess
import sys
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


def with_layout(libraries=(), usable_prefix="/valid"):
    """Point the module at fake drives without touching the machine."""
    from ui.dialogs.depot_selection import storage_targets as st

    st.steam_libraries = lambda: list(libraries)
    st.is_usable = lambda p: bool(p) and str(p).startswith(usable_prefix)
    return st


def main() -> int:
    from ui.dialogs.depot_selection import storage_targets as st

    # ------------------------------------------------- what counts as usable
    print("=== what counts as a download target ===")
    check(st.is_usable("") is False, "an empty path is never offered")
    check(st.is_usable("/tmp/not-a-library") is False,
          "transient directories such as /tmp are refused")
    check(st.is_usable(str(REPO_ROOT)) is True, "a real directory is offered")

    # ------------------------------------------------------------ the drives
    print("=== which drives each dialog offers ===")
    st = with_layout(["/valid/steamlib", "/valid/sd"])
    check(st.candidate_drives("/valid/custom", "/valid/pref") ==
          ["/valid/steamlib", "/valid/sd", "/valid/custom", "/valid/pref"],
          "the multi-depot dialog: libraries, then the default, then the preferred path")
    check(st.candidate_drives("", "") == ["/valid/steamlib", "/valid/sd"],
          "with neither a default nor a preferred path: just the libraries")

    check(st.candidate_drives("/valid/custom", "/valid/pref",
                              only_default_when_configured=True) == ["/valid/custom"],
          "the single-depot dialog narrows to the configured default alone")
    check(st.candidate_drives("", "/valid/pref", preferred_first=True) ==
          ["/valid/pref", "/valid/steamlib", "/valid/sd"],
          "with no default configured, the preferred path leads the single-depot list")

    check(st.candidate_drives("/valid/sd", "") == ["/valid/steamlib", "/valid/sd"],
          "a default that is already a library is not offered twice")
    check(st.candidate_drives("/nope/missing", "", only_default_when_configured=True) ==
          ["/valid/steamlib", "/valid/sd"],
          "an unusable default leaves the single-depot dialog with the libraries")
    check(st.candidate_drives("/valid/custom", "not-usable") ==
          ["/valid/steamlib", "/valid/sd", "/valid/custom"],
          "an unusable preferred path is dropped")

    # ------------------------------------------------------------- the label
    print("=== how a drive reads in the picker ===")
    check(st.drive_label("/valid/steam", steam_root="/valid/steam") == "Primary",
          "the Steam install itself is Primary")
    check(st.drive_label("/home/x/.local/share/steam/steamapps") == "Primary",
          "a path inside the default Steam location is Primary")
    check(st.drive_label("/run/media/x/1234-ABCD") == "1234-ABCD",
          "a mounted card keeps its own name")
    check(st.drive_label("/valid/sdcard") == "SD Card", "sdcard paths read as SD Card")
    check(st.drive_label("/valid/sd") == "SD Card", "a bare /sd path reads as SD Card")
    check(st.drive_label("/valid/SteamLibrary/SteamApps") == "SteamLibrary",
          "a segment that says nothing falls back to the one above it")
    check(len(st.drive_label("/valid/" + "x" * 40)) <= st._LABEL_MAX,
          "a long name is shortened")

    # -------------------------------------------------------- free space text
    print("=== the free-space line ===")
    check(st.free_space_text(str(REPO_ROOT)).endswith("free"),
          "a readable drive reports its free space")
    check(st.free_space_text("/nope/missing") == "",
          "an unreadable drive reports nothing instead of raising")

    target = st.describe("/valid/Data", steam_root="/valid/Data")
    check(target.path == "/valid/Data" and target.label == "Primary",
          "describe() answers a whole row in one call")
    check(target.tooltip.startswith(f"Storage: /valid/Data"),
          "the tooltip names the path")

    # ------------------------------------------------------------ preselection
    print("=== which drive starts selected ===")
    drives = ["/valid/a", "/valid/b", "/valid/c"]
    check(st.preselect(drives, "/valid/b", "/valid/a") == "/valid/b",
          "the first hint on offer wins")
    check(st.preselect(drives, "/nope", "", "/valid/c") == "/valid/c",
          "hints not on offer are passed over")
    check(st.preselect(drives) == "/valid/a", "with no hint the first drive wins")
    check(st.preselect([]) == "", "no drives means nothing is selected")

    # -------------------------------------------------- already-installed game
    print("=== the drive a game is already installed in ===")

    class FakeGameManager:
        def __init__(self, game):
            self.game = game

        def get_game(self, app_id):
            if isinstance(self.game, Exception):
                raise self.game
            return self.game

    check(st.installed_library("1234", FakeGameManager({"library_path": "/valid/b"}), drives)
          == "/valid/b", "the install library is found when it is on offer")
    check(st.installed_library("1234", FakeGameManager({"library_path": "/nope"}), drives) is None,
          "an install library not on offer is ignored")
    check(st.installed_library("1234", FakeGameManager({"library_path": "/valid/b"}),
                               ["/valid/a", "/valid/c"]) is None,
          "an install library outside the offered drives is ignored")
    check(st.installed_library("1234", FakeGameManager(None), drives) is None,
          "no installed game means no hint")
    check(st.installed_library("0", FakeGameManager({"library_path": "/valid/b"}), drives) is None,
          "a placeholder app id is never looked up")
    check(st.installed_library("1234", None, drives) is None,
          "no game manager means no hint")
    check(st.installed_library("1234", FakeGameManager(RuntimeError("db gone")), drives) is None,
          "a failing lookup is swallowed into no hint")

    # ------------------------------------------------------------- Qt-free
    print("=== the module stays Qt-free and importable on its own ===")
    # Loaded by path on purpose: importing it through the package would drag in
    # the dialog's Qt imports through __init__.py and prove nothing.
    module_path = SRC_DIR / "ui" / "dialogs" / "depot_selection" / "storage_targets.py"
    probe = (
        "import importlib.util, sys; "
        f"sys.path.insert(0, r'{SRC_DIR}'); "
        f"spec = importlib.util.spec_from_file_location('storage_targets_probe', r'{module_path}'); "
        "m = importlib.util.module_from_spec(spec); "
        "sys.modules['storage_targets_probe'] = m; spec.loader.exec_module(m); "
        "assert 'PyQt6' not in sys.modules, sorted(k for k in sys.modules if 'PyQt' in k); "
        "print(m.StorageTarget.__name__)"
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
    print(f"ALL STORAGE TARGET TESTS PASSED ({PASSED} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
