#!/usr/bin/env python3
"""Behavioural checks for the lua.tools unlock gate and its cached/validated auth checks.

Run with:  QT_QPA_PLATFORM=offscreen XDG_RUNTIME_DIR=/tmp python3 scripts/test_luatools_gate.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.gettempdir())

_failures = 0


def check(condition: bool, label: str) -> None:
    global _failures
    print(("  PASS  " if condition else "  FAIL  ") + label)
    if not condition:
        _failures += 1


class FakeSettings:
    def __init__(self, unlocked: bool) -> None:
        self.unlocked = unlocked

    def value(self, key, default=None, type=None):  # noqa: A002 - matches QSettings
        if key == "luatools_unlocked":
            return self.unlocked
        return default


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def main() -> int:
    import core.lua_tools_api as api
    import utils.settings as settings_module

    tmp_dir = Path(tempfile.mkdtemp(prefix="assella_luatools_gate_"))
    session_file = tmp_dir / "session.json"
    read_paths: list[Path] = []
    network_calls: list[str] = []

    real_get_session_file = api._get_session_file
    real_urlopen = api.urllib.request.urlopen
    real_get_settings = settings_module.get_settings

    def fake_get_session_file():
        read_paths.append(session_file)
        return session_file

    def fake_urlopen(request, timeout=None):
        network_calls.append(getattr(request, "full_url", str(request)))
        return FakeResponse({"access_token": "fresh-token", "expires_in": 3600})

    api._get_session_file = fake_get_session_file
    api.urllib.request.urlopen = fake_urlopen

    def use_settings(unlocked: bool) -> None:
        settings_module.get_settings = lambda: FakeSettings(unlocked)

    print("=== locked install never touches the backend ===")
    use_settings(False)
    check(api.LuaToolsAPI.is_unlocked() is False, "is_unlocked is False by default")
    check(api.LuaToolsAPI.is_authenticated() is False, "is_authenticated refuses while locked")
    check(api.LuaToolsAPI.is_authenticated(allow_cached=False) is False, "validating auth refuses while locked")
    check(api.LuaToolsAPI.get_auth_status() == {"authenticated": False}, "get_auth_status refuses while locked")
    check(api.LuaToolsAPI.get_valid_access_token() is None, "get_valid_access_token refuses while locked")
    check(network_calls == [], "no network call was made while locked")
    check(read_paths == [], "the session file was never read while locked")

    print("=== unlocked but no stored session ===")
    use_settings(True)
    check(api.LuaToolsAPI.is_authenticated() is False, "no session means not authenticated")
    check(api.LuaToolsAPI.get_valid_access_token() is None, "nothing to refresh without a session")
    check(network_calls == [], "no network call without a session")

    print("=== cached session answers without the network ===")
    session = {
        "access_token": "",
        "refresh_token": "stored-refresh",
        "expires_at": int(time.time()) - 10,
        "user": {"user_metadata": {"custom_claims": {"global_name": "Niwia"}, "avatar_url": "a.png"}},
    }
    session_file.write_text(json.dumps(session), encoding="utf-8")
    check(api.LuaToolsAPI.is_authenticated() is True, "cached refresh token counts as authenticated")
    check(api.LuaToolsAPI.is_authenticated(allow_cached=False) is True, "validation succeeds through the refresh")
    check(len(network_calls) == 1, "validation spent exactly one refresh request")
    check(api.LuaToolsAPI.get_auth_status().get("display_name") == "Niwia", "cached status carries the display name")
    check(api.LuaToolsAPI.get_auth_status().get("authenticated") is True, "cached status reports authenticated")

    print("=== a still-valid access token is reused ===")
    session_file.write_text(
        json.dumps({
            "access_token": "still-good",
            "refresh_token": "stored-refresh",
            "expires_at": int(time.time()) + 3600,
        }),
        encoding="utf-8",
    )
    calls_before = len(network_calls)
    check(api.LuaToolsAPI.get_valid_access_token() == "still-good", "valid token returned as is")
    check(len(network_calls) == calls_before, "valid token costs no network call")

    print("=== an expired token is refreshed and stored ===")
    session_file.write_text(
        json.dumps({
            "access_token": "expired",
            "refresh_token": "stored-refresh",
            "expires_at": int(time.time()) - 10,
        }),
        encoding="utf-8",
    )
    check(api.LuaToolsAPI.get_valid_access_token() == "fresh-token", "expired token is refreshed")
    stored = json.loads(session_file.read_text(encoding="utf-8"))
    check(stored.get("access_token") == "fresh-token", "the refreshed token is persisted")
    check(stored.get("expires_at", 0) > time.time(), "the refreshed token gets an expiry")

    api._get_session_file = real_get_session_file
    api.urllib.request.urlopen = real_urlopen
    settings_module.get_settings = real_get_settings

    print()
    if _failures:
        print(f"FAILED: {_failures} check(s)")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
