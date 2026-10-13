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

from typing import Any, Dict


def is_accela_managed(game: Any) -> bool:
    """True when the game dict describes an ASSella-managed install."""
    if not isinstance(game, dict):
        return False
    return bool(game.get("accela_marker_path") or game.get("source") == "ACCELA")


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
