"""Which Steam library a download goes to, and whether to ask.

Two settings work together:

* ``default_download_directory`` - the preferred library. Every picker
  (Steam library dialog, depot dialog drive row) starts on it.
* ``ask_library_each_time`` - whether the library picker is shown at all.
  Off: downloads go straight to the preferred library.

Settings -> Downloads edits both; "Remember choice" in a picker sets the
preferred library and turns asking off. Before this key existed, a non-empty
``default_download_directory`` already meant "don't ask" (its empty value was
labelled "Ask Every Time"), so a missing key is read that way.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

KEY_DEFAULT = "default_download_directory"
KEY_ASK = "ask_library_each_time"


def _settings(settings=None):
    if settings is not None:
        return settings
    from utils.settings import get_settings

    return get_settings()


def preferred_library(settings=None) -> str:
    s = _settings(settings)
    return (s.value(KEY_DEFAULT, "", type=str) or "").strip() if s else ""


def ask_each_time(settings=None) -> bool:
    s = _settings(settings)
    if not s:
        return True
    if s.contains(KEY_ASK):
        return s.value(KEY_ASK, True, type=bool)
    return not preferred_library(s)


def _match(path: str, libraries: Iterable[str]) -> Optional[str]:
    if not path:
        return None
    norm = os.path.normpath(path)
    for lib in libraries:
        if os.path.normpath(str(lib)) == norm:
            return str(lib)
    return None


def remembered_library(libraries: Iterable[str], settings=None) -> Optional[str]:
    """The library to use without asking, or None when the picker should show."""
    libs = [str(x) for x in libraries]
    if ask_each_time(settings):
        return None
    pref = preferred_library(settings)
    matched = _match(pref, libs)
    if matched:
        return matched
    if pref:
        from utils.paths import is_valid_download_directory
        if is_valid_download_directory(pref):
            return pref
    return None


def preselect_library(libraries: Iterable[str], settings=None) -> Optional[str]:
    """The library a picker should start on (the preferred one, when it exists)."""
    pref = preferred_library(settings)
    matched = _match(pref, [str(x) for x in libraries])
    if matched:
        return matched
    if pref:
        from utils.paths import is_valid_download_directory
        if is_valid_download_directory(pref):
            return pref
    return None


def remember_library(path: str, settings=None) -> None:
    """"Remember choice": make it the preferred library and stop asking."""
    s = _settings(settings)
    if not s or not path:
        return
    from utils.paths import normalize_to_library_root
    norm_path = normalize_to_library_root(path)
    s.setValue(KEY_DEFAULT, norm_path)
    s.setValue(KEY_ASK, False)


def set_preferences(path: str, ask: bool, settings=None) -> None:
    s = _settings(settings)
    if not s:
        return
    from utils.paths import normalize_to_library_root
    norm_path = normalize_to_library_root(path) if path else ""
    s.setValue(KEY_DEFAULT, norm_path)
    s.setValue(KEY_ASK, bool(ask) or not norm_path)
