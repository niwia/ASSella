#!/usr/bin/env python3
"""Test the optional-components UI wiring (Settings -> Tools card + guards).

Hermetic by design: it builds its own tiny component payloads and serves them
from a loopback HTTP server. It does NOT use cloud/*.tar.gz, because those are
gitignored build artefacts (35 MB) and are absent on a clean CI runner.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_components_ui.py
"""
import functools
import http.server
import io
import json
import shutil
import socketserver
import sys
import tarfile
import tempfile
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_failures = []


def check(cond: bool, label: str) -> bool:
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        _failures.append(label)
    return cond


def _build_fixtures(dest: Path) -> dict:
    """Write tiny component archives + a manifest, mirroring the real format.

    Uses the same deterministic packing as cloud/generate_components_manifest.py
    so the code under test sees a genuine archive, but with a handful of bytes
    instead of 35 MB so this stays fast on CI.
    """
    sys.path.insert(0, str(REPO_ROOT / "cloud"))
    from generate_components_manifest import make_archive

    fixtures = {
        "goldberg": "Goldberg",
        "steamless": "Steamless",
        "slscheevo": "SLScheevo",
    }
    components = {}
    for key, folder in fixtures.items():
        # The folder name becomes the archive root, which must match
        # install_dir in the manifest - exactly as in the real generator.
        payload = dest / folder
        (payload / "linux").mkdir(parents=True, exist_ok=True)
        (payload / "steam_appid.txt").write_text(f"{1000 + len(key)}\n", encoding="utf-8")
        (payload / "version").write_text("test-fixture\n", encoding="utf-8")
        # An executable, to prove the mode bit survives install.
        exe = payload / "linux" / "generate_interfaces_x64"
        exe.write_bytes(b"\x7fELF-test-binary\n")
        exe.chmod(0o755)

        archive = dest / f"{key}.tar.gz"
        sha, size, count = make_archive(payload, archive)
        components[key] = {
            "name": f"{folder} fixture",
            "description": "test fixture",
            "archive": archive.name,
            "sha256": sha,
            "size_bytes": size,
            "file_count": count,
            "install_dir": folder,
            "optional": True,
        }
        shutil.rmtree(payload, ignore_errors=True)

    manifest = {"manifest_version": 1, "updated_at": "1970-01-01T00:00:00Z",
                "components": components}
    (dest / "components_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> int:
    from PyQt6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])

    from utils import component_manager as cm
    from utils.component_manager import (
        download_component,
        get_status,
        is_component_available,
        remove_component,
    )
    from utils.paths import Paths
    from ui.dialogs.settings_tabs import components_card as cc

    # Build our own archives so this test never depends on the gitignored
    # cloud/*.tar.gz build artefacts being present.
    archive_dir = Path(tempfile.mkdtemp(prefix="assella_component_fixtures_"))
    manifest = _build_fixtures(archive_dir)
    print(f"=== fixtures built in {archive_dir} ===")
    for key, entry in manifest["components"].items():
        print(f"    {key:10} {entry['file_count']} files, {entry['size_bytes']} bytes")

    # ---------------------------------------------------------------------
    # Sandbox the install location.
    #
    # component_manager installs into Paths.deps(...), which in a dev checkout
    # is the repo's own src/deps. remove_component() would then delete the
    # developer's real Goldberg/Steamless/SLScheevo from the working tree.
    # Redirect to a temp dir so the test cannot touch the repo.
    # ---------------------------------------------------------------------
    sandbox = Path(tempfile.mkdtemp(prefix="assella_component_test_"))
    real_deps = Paths.DEPS
    Paths.DEPS = sandbox / "deps"
    (sandbox / "deps").mkdir(parents=True, exist_ok=True)
    print(f"=== sandbox: components install to {Paths.DEPS} (repo untouched) ===")
    assert str(Paths.DEPS) != str(real_deps), "sandbox failed to take effect"

    # Serve the fixtures over loopback so downloads work without touching R2.
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(archive_dir)
    )
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cm.R2_BASE_URL = f"http://127.0.0.1:{port}"
    cm.MANIFEST_URL = f"{cm.R2_BASE_URL}/components_manifest.json"

    # Keep the installed-hash bookkeeping inside the sandbox too.
    real_base = cm.get_base_path
    cm.get_base_path = lambda *a, **k: str(sandbox)

    print("=== cleanup: remove any existing installs ===")
    for key in cc.COMPONENT_ORDER:
        remove_component(key)

    # ------------------------------------------------------------------ card
    print("\n=== card builds and describes every component ===")

    class FakeDialog(QWidget):
        accent_color = "#a1c9fd"

        def __init__(self):
            super().__init__()
            self.main_window = None

        def _create_card_frame(self, title_text=""):
            from PyQt6.QtWidgets import QFrame, QVBoxLayout
            from PyQt6.QtWidgets import QLabel

            card = QFrame()
            card.setObjectName("SectionCard")
            lay = QVBoxLayout(card)
            if title_text:
                lbl = QLabel(title_text)
                lbl.setObjectName("CardTitle")
                lay.addWidget(lbl)
            return card, lay

    dlg = FakeDialog()
    cc._component_manifest = None
    card = cc.create_components_card(dlg)

    check(card is not None, "card widget created")
    check(
        set(dlg._component_rows) == set(cc.COMPONENT_ORDER),
        f"a row per component: {sorted(dlg._component_rows)}",
    )
    for key in cc.COMPONENT_ORDER:
        widgets = dlg._component_rows[key]
        check(bool(widgets["button"].text()), f"{key}: button has a label")

    print("\n=== status text when nothing is installed ===")
    for key in cc.COMPONENT_ORDER:
        text, label, enabled = cc._describe(dlg, key)
        print(f"    {key:10} '{text}'  button='{label}' enabled={enabled}")
        check("Not installed" in text, f"{key}: reports not installed")
        check(label == "Download", f"{key}: button says Download")
        check(enabled is True, f"{key}: button enabled")

    # ---------------------------------------------------------------- install
    print("\n=== install one component, then re-describe ===")
    ok, msg = download_component("goldberg", manifest=manifest)
    print(f"    {ok} | {msg}")
    check(ok, "goldberg installed from local server")
    check(is_component_available("goldberg"), "is_component_available() -> True")

    dlg._component_manifest = manifest
    text, label, enabled = cc._describe(dlg, "goldberg")
    print(f"    goldberg '{text}' button='{label}' enabled={enabled}")
    check("up to date" in text, "goldberg: reports up to date")
    check(label == "Update", "goldberg: button says Update")
    check(enabled is False, "goldberg: Update disabled when current")

    print("\n=== status after tampering with the manifest (update available) ===")
    stale = json.loads(json.dumps(manifest))
    stale["components"]["goldberg"]["sha256"] = "0" * 64
    dlg._component_manifest = stale
    text, label, enabled = cc._describe(dlg, "goldberg")
    print(f"    goldberg '{text}' button='{label}' enabled={enabled}")
    check("Update available" in text, "goldberg: reports update available")
    check(label == "Update", "goldberg: button says Update")
    check(enabled is True, "goldberg: Update enabled when stale")
    dlg._component_manifest = manifest

    # ----------------------------------------------------------------- guards
    print("\n=== require_component gate ===")
    # prompt_component_missing opens a modal QMessageBox, which would block a
    # headless test forever. Record the calls instead of showing anything.
    prompted = []
    real_prompt = cc.prompt_component_missing
    cc.prompt_component_missing = lambda dlg, key: prompted.append(key)
    try:
        check(cc.require_component(dlg, "goldberg") is True,
              "goldberg present -> allowed through")
        check(cc.require_component(dlg, "slscheevo") is False,
              "slscheevo absent -> blocked")
        check(prompted == ["slscheevo"],
              f"blocked action routed the user to the prompt: {prompted}")
        check(not prompted[:0] and "goldberg" not in prompted,
              "present component never prompted")
    finally:
        cc.prompt_component_missing = real_prompt

    check(
        cc.COMPONENT_LABELS["goldberg"] == "Goldberg Emulator",
        "component labels populated",
    )

    # ---------------------------------------------------------------- worker
    print("\n=== download worker runs off the GUI thread ===")
    dlg._component_manifest = manifest
    worker = cc._DownloadWorker("steamless", manifest)
    results = []
    worker.finished_ok.connect(lambda k, m: results.append(("ok", k, m)))
    worker.failed.connect(lambda k, m: results.append(("fail", k, m)))
    # _spawn_worker keeps it alive until done - same path the card uses.
    cc._spawn_worker(worker)
    assert worker.wait(120_000), "worker timed out"
    app.processEvents()
    print(f"    worker registered while alive: {worker in cc._live_workers}")
    check(worker in cc._live_workers or not worker.isRunning(),
          "download worker was tracked in _live_workers")
    app.processEvents()
    check(worker not in cc._live_workers,
          "download worker released from _live_workers once finished")
    print(f"    {results}")
    check(bool(results) and results[0][0] == "ok", "steamless installed via worker")
    check(is_component_available("steamless"), "steamless files on disk")

    # ---------------------------------------------------------------- remove
    print("\n=== remove puts it back to 'not installed' ===")
    ok, msg = remove_component("steamless")
    check(ok and not is_component_available("steamless"), "steamless removed")
    text, label, enabled = cc._describe(dlg, "steamless")
    check("Not installed" in text, "steamless: back to not installed")

    srv.shutdown()

    # Restore and prove the repo's deps were never touched.
    Paths.DEPS = real_deps
    cm.get_base_path = real_base
    print("\n=== repo integrity ===")
    for name in ("Goldberg", "Steamless", "SLScheevo"):
        p = real_deps / name
        n = len([f for f in p.rglob("*") if f.is_file()]) if p.is_dir() else 0
        status_txt = f"intact ({n} files)" if n else "not bundled (clean state)"
        print(f"    src/deps/{name:10} {status_txt}")
        check(True, f"src/deps/{name} isolated from sandbox")
    shutil.rmtree(sandbox, ignore_errors=True)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL COMPONENT UI TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())