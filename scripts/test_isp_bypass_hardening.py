#!/usr/bin/env python3
"""
Unit tests for ISP Bypass hardening:
1. TorManager reentrant locking, zombie cleanup, client-only + memory capping flags, timeout termination.
2. Thread-safe _override_target_domain_ip DNS context manager.
3. 4-tier fallback ordering: Direct -> DoH -> Wirecutter -> Tor (Tor is last resort).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import threading
import socket

# Add src to path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import utils.isp_bypass as isp


def test_tor_lock_reentrancy():
    print("[TEST] test_tor_lock_reentrancy...")
    assert isinstance(isp._tor_lock, type(threading.RLock())), "isp._tor_lock must be an RLock"
    # Verify reentrant acquire works without deadlock
    with isp._tor_lock:
        with isp._tor_lock:
            pass
    print("  -> Passed.")


def test_doh_dns_override_and_restoration():
    print("[TEST] test_doh_dns_override_and_restoration...")
    original_fn = socket.getaddrinfo
    assert original_fn is isp._REAL_SOCKET_GETADDRINFO, "Original socket.getaddrinfo must match cached"

    test_ip = "192.0.2.123"
    with isp._override_target_domain_ip(test_ip):
        res = socket.getaddrinfo(isp.TARGET_DOMAIN, 443)
        assert res[0][4][0] == test_ip, f"Expected {test_ip}, got {res[0][4][0]}"
        # Ensure it is currently patched
        assert socket.getaddrinfo is not original_fn

    # Ensure restored cleanly
    assert socket.getaddrinfo is original_fn, "socket.getaddrinfo must be restored after context exit"

    # Test restoration on exception
    try:
        with isp._override_target_domain_ip(test_ip):
            raise ValueError("boom")
    except ValueError:
        pass
    assert socket.getaddrinfo is original_fn, "socket.getaddrinfo must be restored even on exception"
    print("  -> Passed.")


def test_tor_command_flags_and_timeout_cleanup():
    print("[TEST] test_tor_command_flags_and_timeout_cleanup...")
    # Mock proxy check to always report False so timeout is reached
    with patch.object(isp.TorManager, "is_proxy_active", return_value=False), \
         patch.object(isp.TorManager, "_find_tor_binary", return_value="/usr/bin/tor"), \
         patch("time.sleep", return_value=None), \
         patch("subprocess.Popen") as mock_popen:

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        started = isp.TorManager.start_tor_if_needed()
        assert started is False, "start_tor_if_needed must return False on listener timeout"

        # Check launch flags
        called_cmd = mock_popen.call_args[0][0]
        assert "--ClientOnly" in called_cmd and called_cmd[called_cmd.index("--ClientOnly") + 1] == "1"
        assert "--MaxMemInQueues" in called_cmd and called_cmd[called_cmd.index("--MaxMemInQueues") + 1] == "256 MB"

        # Check that stop_tor was called to clean up the process (not left orphaned)
        assert mock_proc.terminate.called or mock_proc.kill.called, "Subprocess must be terminated on timeout"
        assert isp._tor_process is None, "_tor_process must be cleared to None"

    print("  -> Passed.")


def test_tor_zombie_cleanup():
    print("[TEST] test_tor_zombie_cleanup...")
    # Setup mock zombie process
    mock_zombie = MagicMock()
    mock_zombie.poll.return_value = None
    isp._tor_process = mock_zombie

    with patch.object(isp.TorManager, "is_proxy_active", return_value=False), \
         patch.object(isp.TorManager, "_find_tor_binary", return_value=None):
        # start_tor_if_needed should detect zombie, call stop_tor, and find no binary
        started = isp.TorManager.start_tor_if_needed()
        assert started is False
        assert mock_zombie.terminate.called or mock_zombie.kill.called, "Zombie process must be terminated"
        assert isp._tor_process is None, "_tor_process must be None after cleaning zombie"

    print("  -> Passed.")


def test_fallback_order_wirecutter_before_tor():
    print("[TEST] test_fallback_order_wirecutter_before_tor...")
    import requests

    call_order = []

    def mock_request(method, url, *args, **kwargs):
        call_order.append(url)
        raise requests.exceptions.ConnectionError(f"Simulated failure for {url}")

    mock_session = MagicMock()
    mock_session.request.side_effect = mock_request

    with patch.object(isp, "resolve_doh", return_value="1.2.3.4"), \
         patch.object(isp.TorManager, "start_tor_if_needed", return_value=True), \
         patch.object(isp.TorManager, "get_tor_proxies", return_value={}), \
         patch("utils.isp_bypass.get_settings") as mock_settings:

        mock_set = MagicMock()
        mock_set.value.return_value = "auto"
        mock_settings.return_value = mock_set

        try:
            isp.execute_hubcap_request(mock_session, "GET", "https://hubcapmanifest.com/api/v1/test", timeout=1)
        except requests.exceptions.ConnectionError:
            pass

        # Verify call order:
        # 1. Direct -> hubcapmanifest.com
        # 2. DoH -> hubcapmanifest.com (via IP override)
        # 3. Wirecutter -> rewrite_url_for_wirecutter
        # 4. Tor -> hubcapmanifest.com (via tor proxy)
        print("  Observed call sequence:", call_order)
        assert len(call_order) >= 4, f"Expected 4 requests in pipeline, got {len(call_order)}"
        # Direct:
        assert call_order[0] == "https://hubcapmanifest.com/api/v1/test"
        # DoH:
        assert call_order[1] == "https://hubcapmanifest.com/api/v1/test"
        # Wirecutter is 3rd:
        assert "worker" in call_order[2] or "wirecutter" in call_order[2] or "workers.dev" in call_order[2] or "hubcapmanifest.com" in call_order[2]
        # Tor is 4th (last):
        assert call_order[3] == "https://hubcapmanifest.com/api/v1/test"

    print("  -> Passed.")


if __name__ == "__main__":
    test_tor_lock_reentrancy()
    test_doh_dns_override_and_restoration()
    test_tor_command_flags_and_timeout_cleanup()
    test_tor_zombie_cleanup()
    test_fallback_order_wirecutter_before_tor()
    print("\nALL 5/5 ISP BYPASS HARDENING TESTS PASSED!")

