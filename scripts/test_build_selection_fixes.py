import sys
from pathlib import Path

# Add src to PYTHONPATH
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from ui.dialogs.depot_selection._mixins.branch_build import BranchBuildMixin
from ui.dialogs.build_selection_dialog import _has_usable_manifests


class DummyHost(BranchBuildMixin):
    def __init__(self):
        self.depots = {
            "33231": {"manifest_id": "1111111111111111111"},
            "33362": {"manifest_id": "4758459184864595027"},
        }
        self.app_id = "33230"
        self.game_name = "Assassin's Creed II"
        self.current_build_id = "2451034"
        self._selected_build_id = self.current_build_id
        self._manifest_overrides = {}
        self._is_build_pinned = False
        self.accent_color = "#C06C84"

        # Mock builds_btn
        class MockBtn:
            def setText(self, txt):
                self.text = txt
            def setStyleSheet(self, css):
                self.css = css
            def adjustSize(self):
                pass
        self.builds_btn = MockBtn()


def test_build_pinning_with_manifest_override():
    host = DummyHost()
    assert not host.is_build_pinned()

    # User manually overrides depot 33362 with manifest 5654928643791563232, keeping build_id as 2451034
    host._apply_build_selection("2451034", {"33362": {"manifest_id": "5654928643791563232"}})

    # Should be pinned because manifest overrides exist
    assert host.is_build_pinned(), "Build should be pinned when manifest overrides exist"
    assert host.get_manifest_overrides() == {"33362": "5654928643791563232"}
    assert host.depots["33362"]["manifest_id"] == "5654928643791563232"
    print("  [ok] build is pinned when manifest overrides are present even if build_id matches current")


def test_build_pinning_different_build_id():
    host = DummyHost()
    host._apply_build_selection("1843459", {"33231": {"manifest_id": "6394869848543559007"}})
    assert host.is_build_pinned()
    assert host.get_selected_build() == "1843459"
    assert host.get_manifest_overrides()["33231"] == "6394869848543559007"
    print("  [ok] build is pinned with different build_id and manifest overrides")


def test_build_unpinned_when_current_and_no_overrides():
    host = DummyHost()
    host._apply_build_selection("2451034", {})
    assert not host.is_build_pinned()
    print("  [ok] build is unpinned when build_id matches current and no overrides exist")


def test_usable_manifests_guard():
    # Empty or None
    assert not _has_usable_manifests(None)
    assert not _has_usable_manifests({})

    # Non-empty mapping with blank / zero manifest IDs
    assert not _has_usable_manifests({"33231": {"manifest_id": ""}})
    assert not _has_usable_manifests({"33231": ""})
    assert not _has_usable_manifests({"33231": {"manifest_id": "0"}})
    assert not _has_usable_manifests({"33231": "0"})
    assert not _has_usable_manifests({"33231": {"depot_id": "33231"}})

    # Valid numeric manifest IDs
    assert _has_usable_manifests({"33231": {"manifest_id": "5654928643791563232"}})
    assert _has_usable_manifests({"33231": "5654928643791563232"})
    assert _has_usable_manifests({"33231": {"manifest_id": ""}, "33362": {"manifest_id": "5654928643791563232"}})
    print("  [ok] _has_usable_manifests correctly rejects empty/blank manifest mappings and accepts valid IDs")


if __name__ == "__main__":
    print("=== Testing Build Selection & Pinning Invariants ===")
    test_build_pinning_with_manifest_override()
    test_build_pinning_different_build_id()
    test_build_unpinned_when_current_and_no_overrides()
    test_usable_manifests_guard()
    print("ALL BUILD SELECTION TESTS PASSED")
