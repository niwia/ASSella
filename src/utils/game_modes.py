"""Single source of truth for ASSella-managed vs AT0-M game classification.

An ASSella-managed install is identified by a physical marker (resolved from
.DepotDownloader, with legacy .ACCELA/.assella still recognised on read) or
an explicit ``source == "ACCELA"``. Everything else with atom/vapor/plugin
signals is Steam-managed (AT0-M): ASSella must never check it for updates —
only At0mUpdateChecker watches those for newly added depots.

Keep this logic here, not copy-pasted per call site, so the guard
(manifest_check_task), the headless filter (helper_manager) and discovery
(at0m_update_checker) can never drift apart again.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def is_accela_managed(game: Any) -> bool:
    """True when the game dict describes an ASSella-managed install (including standalone custom folders)."""
    if not isinstance(game, dict):
        return False
    return bool(
        game.get("accela_marker_path")
        or game.get("source") in ("ACCELA", "Standalone")
        or game.get("is_accela_install")
        or game.get("is_standalone")
    )


def resolve_install_layout(
    dest_path: str, game_data: Optional[Dict[str, Any]] = None
) -> Tuple[bool, str]:
    """Resolve install layout returning (is_standalone, resolved_game_directory).

    Single source of truth for determining whether an install destination / game
    is standalone, and what its final directory on disk should be.
    """
    if not dest_path:
        return False, ""

    game = game_data if isinstance(game_data, dict) else {}

    # Anything Steam or a plugin owns is never standalone: AT0-M/Vapor/plugin
    # games (which include ACCELA-managed installs, stamped is_accela_install
    # while source stays "Steam") and any game with a real appmanifest.
    if _is_steam_owned_game(game):
        is_standalone = False
    elif game.get("is_standalone") or game.get("source") == "Standalone":
        is_standalone = True
    else:
        # Everything else is decided by the destination: a path inside a Steam
        # library uses the library layout, a path outside one is standalone.
        from core.steam_helpers import is_path_in_steam_library
        is_standalone = not is_path_in_steam_library(dest_path)

    if is_standalone:
        return True, dest_path

    # For Steam library layout, nest inside steamapps/common/<installdir>.
    # A destination already inside a steamapps/common tree is a game directory,
    # not a library root, so returning it untouched avoids .../common/X/steamapps/common/...
    from utils.steam_manifest import get_install_folder_name
    folder_name = get_install_folder_name(game)
    norm = os.path.normpath(dest_path)
    common_parts = ("steamapps", "common")
    parts = Path(norm).parts
    if any(parts[i: i + 2] == common_parts for i in range(len(parts) - 1)):
        return False, dest_path

    return False, os.path.join(dest_path, "steamapps", "common", folder_name)


# Sources that mean "Steam or a plugin owns this install", so ASSella must treat
# it as a normal Steam-library game rather than a standalone custom folder.
# "ACCELA" is ASSella's own managed install inside a Steam library.
_STEAM_OWNED_SOURCES = frozenset(
    ("at0-m", "AT0-M", "Steam", "Vapor", "plugin_native", "ACCELA")
)


def _is_steam_owned_game(game: Dict[str, Any]) -> bool:
    """True when the game dict carries Steam/AT0-M/plugin ownership signals.

    Deliberately does not look at ``is_accela_install``: standalone custom
    folders carry it too, and they are the case this predicate must not catch.
    """
    return bool(
        game.get("is_atom")
        or game.get("is_vapor")
        or game.get("is_plugin_game")
        or game.get("appmanifest_path")
        or game.get("source") in _STEAM_OWNED_SOURCES
    )


def is_standalone_game(game: Any) -> bool:
    """True when the game dict describes a Standalone Custom Folder install (decoupled from Steam/SLSsteam)."""
    if not isinstance(game, dict):
        return False
    if _is_steam_owned_game(game):
        return False
    if game.get("is_standalone") or game.get("source") == "Standalone":
        return True
    install_path = game.get("install_path") or game.get("library_path")
    if install_path:
        is_standalone, _ = resolve_install_layout(install_path, game)
        return is_standalone
    return False


def is_atom_managed(game: Any) -> bool:
    """True when Steam (AT0-M/Vapor/plugin mode) owns updates for this game.

    Physical flags only — deliberately not keyed on the cached
    ``update_status`` string, so a stale status can never flip classification.
    Callers that also want the status string as a hint (e.g. the update
    checker facing hand-built dicts) OR it in themselves.
    """
    if not isinstance(game, dict):
        return False
    if is_accela_managed(game):
        return False
    return bool(
        game.get("is_atom")
        or game.get("is_vapor")
        or game.get("is_plugin_game")
        or game.get("source") == "at0-m"
    )
