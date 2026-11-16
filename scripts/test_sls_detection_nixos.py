import os
import sys
import tempfile
import errno
import time
from pathlib import Path

src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

import utils.slssteam_integration as sls
from core.steam_helpers import is_steam_running, STEAM_PROCESS_NAMES
from core.native_steam.native_steam_handoff import check_slssteam_active


def test_pipe_listening_nonexistent():
    # SLSSTEAM_API_PIPE when missing returns False
    old_pipe = sls.SLSSTEAM_API_PIPE
    try:
        sls.SLSSTEAM_API_PIPE = "/tmp/nonexistent_test_pipe.API"
        assert not sls._is_slssteam_pipe_listening()
        print("  [ok] non-existent pipe returns False")
    finally:
        sls.SLSSTEAM_API_PIPE = old_pipe


def test_pipe_listening_regular_file():
    # If the pipe is a regular file (not a FIFO), returns False
    with tempfile.NamedTemporaryFile() as tmp:
        old_pipe = sls.SLSSTEAM_API_PIPE
        try:
            sls.SLSSTEAM_API_PIPE = tmp.name
            assert not sls._is_slssteam_pipe_listening()
            print("  [ok] regular file (non-FIFO) pipe returns False")
        finally:
            sls.SLSSTEAM_API_PIPE = old_pipe


def test_pipe_listening_fifo_no_reader():
    # If the pipe is a FIFO but no reader is listening, open with O_NONBLOCK raises ENXIO and returns False
    fifo_path = tempfile.mktemp(suffix=".API")
    os.mkfifo(fifo_path)
    old_pipe = sls.SLSSTEAM_API_PIPE
    try:
        sls.SLSSTEAM_API_PIPE = fifo_path
        assert not sls._is_slssteam_pipe_listening()
        print("  [ok] FIFO without active reader returns False (ENXIO)")
    finally:
        sls.SLSSTEAM_API_PIPE = old_pipe
        if os.path.exists(fifo_path):
            os.unlink(fifo_path)


def test_pipe_listening_fifo_with_reader():
    # If a reader is open on the FIFO, _is_slssteam_pipe_listening returns True
    fifo_path = tempfile.mktemp(suffix=".API")
    os.mkfifo(fifo_path)
    old_pipe = sls.SLSSTEAM_API_PIPE
    try:
        sls.SLSSTEAM_API_PIPE = fifo_path
        # Open reader non-blocking
        r_fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            assert sls._is_slssteam_pipe_listening()
            print("  [ok] FIFO with active reader returns True")
        finally:
            os.close(r_fd)
    finally:
        sls.SLSSTEAM_API_PIPE = old_pipe
        if os.path.exists(fifo_path):
            os.unlink(fifo_path)


def test_nixos_steam_process_names():
    assert ".steam-wrapped" in sls.STEAM_PROCESS_NAMES
    assert "steam-wrapped" in sls.STEAM_PROCESS_NAMES
    assert "steam-original" in sls.STEAM_PROCESS_NAMES
    assert ".steam-wrapped" in STEAM_PROCESS_NAMES
    print("  [ok] NixOS wrapped binary names present in STEAM_PROCESS_NAMES")


def test_proc_cache_with_active_pipe():
    fifo_path = tempfile.mktemp(suffix=".API")
    os.mkfifo(fifo_path)
    old_pipe = sls.SLSSTEAM_API_PIPE
    try:
        sls.SLSSTEAM_API_PIPE = fifo_path
        r_fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            sls._proc_cache["last_check"] = 0.0
            sls._update_proc_cache_if_needed()
            assert sls._proc_cache["slssteam_active"]
            assert sls._proc_cache["steam_running"]
            assert sls.is_slssteam_process_active()
            assert sls.is_steam_process_running()
            assert is_steam_running()
            assert check_slssteam_active()
            print("  [ok] active pipe listener sets slssteam_active and steam_running across all modules")
        finally:
            os.close(r_fd)
    finally:
        sls.SLSSTEAM_API_PIPE = old_pipe
        if os.path.exists(fifo_path):
            os.unlink(fifo_path)
        sls._proc_cache["last_check"] = 0.0
        sls._update_proc_cache_if_needed()


def test_slssteam_api_send_nonblocking():
    from core.steam_helpers import slssteam_api_send

    # 1. Non-existent pipe returns False immediately
    old_pipe = sls.SLSSTEAM_API_PIPE
    try:
        sls.SLSSTEAM_API_PIPE = "/tmp/nonexistent_test_pipe.API"
        assert not sls._slssteam_api_send("test|command")
        assert not slssteam_api_send("test|command")
        print("  [ok] api_send on non-existent pipe returns False immediately")
    finally:
        sls.SLSSTEAM_API_PIPE = old_pipe

    # 2. FIFO with NO reader returns False immediately (no deadlock/hang)
    fifo_path = tempfile.mktemp(suffix=".API")
    os.mkfifo(fifo_path)
    try:
        sls.SLSSTEAM_API_PIPE = fifo_path
        assert not sls._slssteam_api_send("test|command")
        assert not slssteam_api_send("test|command")
        print("  [ok] api_send on FIFO without reader returns False immediately (no deadlock)")
    finally:
        sls.SLSSTEAM_API_PIPE = old_pipe
        if os.path.exists(fifo_path):
            os.unlink(fifo_path)

    # 3. FIFO with reader receives command
    fifo_path = tempfile.mktemp(suffix=".API")
    os.mkfifo(fifo_path)
    try:
        sls.SLSSTEAM_API_PIPE = fifo_path
        r_fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            assert sls._slssteam_api_send("install|12345|0")
            data = os.read(r_fd, 1024).decode("utf-8")
            assert data == "install|12345|0\n"

            assert slssteam_api_send("install|67890|1")
            data2 = os.read(r_fd, 1024).decode("utf-8")
            assert data2 == "install|67890|1\n"
            print("  [ok] api_send on FIFO with reader sends command successfully and closes fd")
        finally:
            os.close(r_fd)
    finally:
        sls.SLSSTEAM_API_PIPE = old_pipe
        if os.path.exists(fifo_path):
            os.unlink(fifo_path)


def test_steam_restart_removed_and_compat_tool():
    from core.steam_helpers import kill_steam_process, start_steam, start_steam_with_slssteam, run_dll_injector
    import core.native_steam.compat_tool as ct

    # 1. Deprecated Steam lifecycle stubs return safe values without exceptions or subprocesses
    assert kill_steam_process() is False
    assert start_steam() == "FAILED"
    assert start_steam_with_slssteam() == "FAILED"
    assert run_dll_injector("/nonexistent") is False
    print("  [ok] steam lifecycle helpers are safely deprecated and no-op")

    # 2. force_proton_for_app rejects modification while Steam is running
    vdf_content = '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n\t\t\t{\n\t\t\t}\n\t\t}\n\t}\n}\n'
    with tempfile.NamedTemporaryFile("w+", suffix=".vdf") as tmp_vdf:
        tmp_vdf.write(vdf_content)
        tmp_vdf.flush()

        old_get_vdf = ct.get_steam_config_vdf_path
        old_is_steam_running = sls.is_steam_process_running
        try:
            ct.get_steam_config_vdf_path = lambda: Path(tmp_vdf.name)

            # Case A: Steam is running -> Must return False and not modify VDF
            sls._proc_cache["last_check"] = time.time() + 1000
            sls._proc_cache["steam_running"] = True

            assert not ct.force_proton_for_app(12345, "proton_experimental")
            assert not ct.set_compat_tool_for_app(12345, "proton_experimental")
            print("  [ok] force_proton_for_app guards against running Steam (prevents VDF overwrite)")

            # Case B: Steam is NOT running -> Successfully writes CompatToolMapping
            sls._proc_cache["steam_running"] = False
            sls._proc_cache["slssteam_active"] = False

            # Ensure steam comm check also returns False in this test
            saved_pipe = sls.SLSSTEAM_API_PIPE
            sls.SLSSTEAM_API_PIPE = "/tmp/nonexistent_test_pipe.API"
            try:
                assert ct.force_proton_for_app(12345, "proton_experimental")
                content = Path(tmp_vdf.name).read_text(encoding="utf-8")
                assert "12345" in content
                assert "proton_experimental" in content
                print("  [ok] force_proton_for_app safely writes CompatToolMapping when Steam is closed")
            finally:
                sls.SLSSTEAM_API_PIPE = saved_pipe

        finally:
            ct.get_steam_config_vdf_path = old_get_vdf
            sls._proc_cache["last_check"] = 0.0
            sls._update_proc_cache_if_needed()


if __name__ == "__main__":
    print("=== Testing NixOS SLSsteam & Steam Detection ===")
    test_pipe_listening_nonexistent()
    test_pipe_listening_regular_file()
    test_pipe_listening_fifo_no_reader()
    test_pipe_listening_fifo_with_reader()
    test_nixos_steam_process_names()
    test_proc_cache_with_active_pipe()
    test_slssteam_api_send_nonblocking()
    test_steam_restart_removed_and_compat_tool()
    print("ALL NIXOS DETECTION & LIFECYCLE TESTS PASSED")
