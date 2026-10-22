"""Which drive a download lands on, and how each drive is described.

The storage picker at the bottom of the depot dialogs needs four things that
have nothing to do with widgets: the drives worth offering, a short label and a
free-space line for each, the rule that decides which one starts selected, and
whether the game already sits on one of them.

The multi-depot and the single-depot dialog both ask for those, so they are
answered here once. Building the buttons, the overflow dropdown and the locked
greyed-out display stays with each dialog, and with ``storage_lock``.
"""

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: App ids that mean "not a game we know"; never looked up in the library.
_NON_APP_IDS = ("0", "N/A", "unknown")

#: Path fragments that make a drive read as a memory card rather than a disk.
_SD_MARKERS = ("sdcard", "sd_card", "mmcblk", "/sd")

#: Final path segments that say nothing, so the segment above them is used.
_ANONYMOUS_SEGMENTS = ("steamlibrary", "steamapps", "common")

_LABEL_MAX = 16


@dataclass(frozen=True)
class StorageTarget:
    """One drive, as the picker shows and describes it."""

    path: str
    label: str
    free: str
    tooltip: str


def is_usable(path: str) -> bool:
    """Whether a directory may be offered as a download target."""
    from utils.paths import is_valid_download_directory

    return bool(path) and is_valid_download_directory(path)


def steam_libraries() -> list[str]:
    """Realpaths of the Steam libraries, in Steam's own order, deduplicated."""
    from core.steam_helpers import get_steam_libraries

    drives: list[str] = []
    try:
        for path in get_steam_libraries() or []:
            if path and is_usable(path):
                real = os.path.realpath(path)
                if real not in drives:
                    drives.append(real)
    except Exception as e:
        logger.warning(f"Error discovering Steam storage libraries: {e}")
    return drives


def free_space_text(path: str) -> str:
    """Free space as the picker shows it, or "" when the drive cannot be read."""
    try:
        free_bytes = shutil.disk_usage(path).free
    except Exception:
        return ""
    if free_bytes >= 1024**4:
        return f"{free_bytes / (1024**4):.1f} TB free"
    if free_bytes >= 1024**3:
        return f"{free_bytes / (1024**3):.1f} GB free"
    if free_bytes >= 1024**2:
        return f"{free_bytes / (1024**2):.0f} MB free"
    return f"{free_bytes} B free"


def drive_label(path: str, *, steam_root: Optional[str] = None) -> str:
    """"Primary", "SD Card", or the directory's own name when it says nothing.

    ``steam_root`` is looked up when not given; passing it keeps this testable
    away from a real Steam install.
    """
    if steam_root is None:
        from core.steam_helpers import find_steam_install

        try:
            steam_root = find_steam_install()
        except Exception:
            steam_root = None

    folded = path.lower()
    if steam_root and os.path.realpath(path) == os.path.realpath(steam_root):
        return "Primary"
    if "/.local/share/steam" in folded or "/.steam/steam" in folded:
        return "Primary"
    if any(marker in folded for marker in _SD_MARKERS):
        return "SD Card"

    parts = Path(path).parts
    label = Path(path).name
    if label.lower() in _ANONYMOUS_SEGMENTS and len(parts) > 1:
        label = parts[-2]
    if len(label) > _LABEL_MAX:
        label = label[: _LABEL_MAX - 2] + "…"
    return label


def describe(path: str, *, steam_root: Optional[str] = None) -> StorageTarget:
    """Everything the picker shows for one drive."""
    free = free_space_text(path)
    tooltip = f"Storage: {path}" + (f"\nAvailable: {free}" if free else "")
    return StorageTarget(
        path=path, label=drive_label(path, steam_root=steam_root), free=free, tooltip=tooltip
    )


def candidate_drives(
    default_dir: str = "",
    preferred: str = "",
    *,
    only_default_when_configured: bool = False,
    preferred_first: bool = False,
) -> list[str]:
    """The drives worth offering, in display order.

    ``default_dir`` is the directory configured in Settings, ``preferred`` a
    path a caller asked for (a zip-confirmation choice or job metadata).

    The two flags keep the current behaviour of the two dialogs, which differs:
    the single-depot dialog narrows to the configured default whenever there is
    one, and shows a preferred path first; the multi-depot dialog offers every
    drive and appends both extras.
    """
    drives: list[str] = []

    if only_default_when_configured and is_usable(default_dir):
        return [os.path.realpath(default_dir)]

    for library in steam_libraries():
        if library not in drives:
            drives.append(library)

    def add(path):
        if path and is_usable(path):
            real = os.path.realpath(path)
            if real not in drives:
                drives.append(real)

    add(default_dir)
    if preferred_first:
        if preferred and is_usable(preferred):
            real = os.path.realpath(preferred)
            if real not in drives:
                drives.insert(0, real)
    else:
        add(preferred)

    return drives


def preselect(drives: list[str], *hints: str) -> str:
    """The drive that starts selected: the first hint that is on offer.

    Hints are paths the caller knows about, most meaningful first - a preferred
    path, the library the game is already installed in, the configured default.
    With no hint on offer the first drive wins.
    """
    for hint in hints:
        if not hint:
            continue
        real = os.path.realpath(hint)
        if real in drives:
            return real
    return drives[0] if drives else ""


def installed_library(app_id, game_manager, drives: list[str]) -> Optional[str]:
    """The drive the game is already installed in, when it is one on offer."""
    if not app_id or str(app_id) in _NON_APP_IDS or game_manager is None:
        return None

    get_game = getattr(game_manager, "get_game", None)
    if not callable(get_game):
        return None
    try:
        game = get_game(str(app_id))
    except Exception as e:
        logger.warning(f"Could not look up the installed library for {app_id}: {e}")
        return None
    if not isinstance(game, dict) or not game.get("library_path"):
        return None

    real = os.path.realpath(game["library_path"])
    return real if real in drives else None
