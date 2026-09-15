"""
Helpers for Steam branch (beta) handling shared by the update checker and the
smart update path.

Steam appinfo lists, per depot, one manifest per branch that includes that depot:

    depots/<depot_id>/manifests/<branch> -> {"gid": ..., "size": ...}   (PICS)
    depots/<depot_id>/manifests/<branch> -> "<gid>"                    (SteamCMD REST)

The convenience key ``manifest_id`` that ASSella attaches to each depot entry is
ALWAYS the *public* GID. A depot with no entry for a branch is not part of that
branch's build, so falling back to the public GID for a beta install produces a
false "update available" that then installs the public build on top of the beta.
"""

import logging
from typing import Optional

logger = logging.getLogger("ACCELA.branch_helpers")

_EMPTY_GIDS = (None, "", 0, "0")


def resolve_branch_manifest_gid(
    depot_info, branch: str, allow_public_fallback: bool = False
) -> Optional[str]:
    """
    Resolve the current manifest GID of a depot on ``branch``.

    Args:
        depot_info: PICS-style depot entry ({"manifest_id": ..., "manifests": {...}}).
        branch: Branch name selected by the user ("public" when unset).
        allow_public_fallback: Permit using the public GID when the branch has no
            entry. Only meant for depots that live in a *different* app (DLC apps
            with ``hasdepotsindlc``), which normally carry no beta branches of their
            own. Never enable it for the base app's own depots.

    Returns:
        The GID as a string, or None when the depot has no usable manifest on the
        branch (and the public fallback was not allowed / not available).
    """
    if not isinstance(depot_info, dict):
        return None
    branch = branch or "public"

    manifests = depot_info.get("manifests")
    if isinstance(manifests, dict) and branch in manifests:
        entry = manifests[branch]
        gid = entry.get("gid") if isinstance(entry, dict) else entry
        if gid not in _EMPTY_GIDS:
            return str(gid)

    if branch == "public" or allow_public_fallback:
        public_gid = depot_info.get("manifest_id")
        if public_gid in _EMPTY_GIDS and isinstance(manifests, dict):
            entry = manifests.get("public")
            public_gid = entry.get("gid") if isinstance(entry, dict) else entry
        if public_gid not in _EMPTY_GIDS:
            return str(public_gid)
    return None
