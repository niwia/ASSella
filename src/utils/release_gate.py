"""
Two-gate availability classification for depots and DLC apps.

Gate 1 - Steam release state
    A depot or DLC whose Steam ``releasestate`` is ``prerelease`` is
    *unreleased*. It is still listed so the user can see it, but it is
    disabled, and so is everything mapped to it: a DLC's content depots
    and their decryption keys inherit the DLC's state.

Gate 2 - Hubcap availability (released content only)
    For released content that has no local key yet, the local Hubcap key
    index decides what we show: key ready on Hubcap, key pending, or not on
    Hubcap at all.

Callers pass in whatever metadata they already loaded (depot enrichments and
the base app's cached depots) so the common path costs no extra DB reads; a
DLC's own app record is only consulted as a last resort, and memoised.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

PRERELEASE = "prerelease"
UNPUBLISHED = "unpublished"  # listed by the game (listofdlc) but Steam does not publish the app
UNRELEASED_STATES = frozenset({PRERELEASE, UNPUBLISHED})

STATUS_UNRELEASED = "unreleased"
STATUS_READY = "ready_on_hubcap"
STATUS_PENDING = "key_pending"
STATUS_MISSING = "missing_hubcap"

UNRELEASED_TAG = "[Unreleased]"


def unreleased_tooltip(item_id: str, is_dlc_app: bool = False) -> str:
    what = "DLC" if is_dlc_app else "Depot"
    return (
        f"{what} {item_id} is not released on Steam (upcoming or unpublished).\n"
        "Valve has not published its files or decryption keys, so it cannot be enabled yet."
    )


class ReleaseGate:
    """Answers "is this unreleased?" and "does Hubcap have it?" for one base game."""

    def __init__(
        self,
        base_appid: Any,
        *,
        enrichments: Optional[Dict[str, dict]] = None,
        app_depots: Optional[Dict[str, Any]] = None,
        db: Any = None,
        key_index: Any = None,
    ):
        self.base_appid = str(base_appid)
        self.enrichments = enrichments or {}
        self.app_depots = app_depots or {}
        self._db = db
        self._key_index = key_index
        self._dlc_state: Dict[str, Optional[str]] = {}

    # ------------------------------------------------------------ metadata
    def _meta(self, item_id: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        dep = self.app_depots.get(item_id)
        if isinstance(dep, dict):
            out.update(dep)
        enr = self.enrichments.get(item_id)
        if isinstance(enr, dict):
            # Enrichments are refreshed more often than the PICS depot blob.
            out.update({k: v for k, v in enr.items() if v not in (None, "")})
        return out

    def owner_dlc(self, depot_id: Any) -> Optional[str]:
        """The DLC app a depot belongs to, if any (never the base game)."""
        owner = str(self._meta(str(depot_id)).get("dlcappid") or "").strip()
        if owner and owner != self.base_appid:
            return owner
        return None

    def _own_state(self, item_id: str) -> Optional[str]:
        state = self._meta(item_id).get("releasestate")
        return str(state).strip().lower() if state else None

    def dlc_release_state(self, dlc_id: Any) -> Optional[str]:
        """Release state of a DLC *app* (memoised)."""
        dlc_id = str(dlc_id)
        if dlc_id in self._dlc_state:
            return self._dlc_state[dlc_id]
        state = self._own_state(dlc_id)
        if not state and self._db is not None:
            try:
                info = self._db.get_app_info(dlc_id, bypass_expiration=True) or {}
                raw = info.get("releasestate")
                state = str(raw).strip().lower() if raw else None
            except Exception as e:
                logger.debug(f"[ReleaseGate] App lookup failed for {dlc_id}: {e}")
        self._dlc_state[dlc_id] = state
        return state

    # ------------------------------------------------------------- gate 1
    def is_unreleased(self, item_id: Any, *, is_dlc_app: bool = False) -> bool:
        """True when the item itself, or the DLC it is mapped to, is prerelease."""
        item_id = str(item_id)
        if item_id == self.base_appid:
            return False
        if is_dlc_app:
            return self.dlc_release_state(item_id) in UNRELEASED_STATES
        if self._own_state(item_id) in UNRELEASED_STATES:
            return True
        owner = self.owner_dlc(item_id)
        if owner:
            return self.dlc_release_state(owner) in UNRELEASED_STATES
        return False

    def needs_verification(self, dlc_id: Any) -> bool:
        """True when a DLC's release state was never verified against Steam.

        Legacy rows defaulted to "released" without asking PICS, which is how
        unpublished DLC slipped through; those (and DLCs with no row at all)
        are re-checked in the background via core.steam_api.resolve_dlc_release_states.
        """
        enr = self.enrichments.get(str(dlc_id))
        return not (isinstance(enr, dict) and enr.get("release_src"))

    def apply_verified(self, states: Dict[str, Tuple[str, str]]) -> None:
        """Fold freshly verified ``{id: (state, source)}`` into this gate."""
        for did, (state, src) in (states or {}).items():
            row = dict(self.enrichments.get(str(did)) or {})
            row["releasestate"], row["release_src"] = state, src
            self.enrichments[str(did)] = row
            self._dlc_state[str(did)] = state

    # ------------------------------------------------------------- gate 2
    def _index(self):
        if self._key_index is None:
            try:
                from core.hubcap_key_index import get_index

                self._key_index = get_index(allow_network=False)
            except Exception as e:
                logger.debug(f"[ReleaseGate] Hubcap key index unavailable: {e}")
                self._key_index = False
        return self._key_index or None

    def hubcap_state(self, depot_id: Any) -> Optional[str]:
        """Hubcap index state for a depot, or None when the index is not loaded.

        available -> key published; pending -> known but key not published yet;
        unknown -> index loaded and Hubcap simply does not have this depot.
        """
        index = self._index()
        if index is None or not getattr(index, "loaded", False):
            return None
        try:
            return index.state(str(depot_id))
        except Exception:
            return None

    def classify_missing(self, depot_id: Any, *, reported_missing: bool = False) -> Tuple[str, str, str]:
        """(status, tag, tooltip) for a depot that has no local key.

        Gate 1 runs first: unreleased content is never reported as awaiting a key.
        ``reported_missing`` marks depots Hubcap itself told us it does not have.
        """
        from core.hubcap_key_index import STATE_AVAILABLE, STATE_PENDING, STATE_UNKNOWN

        did = str(depot_id)
        if self.is_unreleased(did):
            return STATUS_UNRELEASED, UNRELEASED_TAG, unreleased_tooltip(did)

        state = self.hubcap_state(did)
        if state == STATE_AVAILABLE:
            return (
                STATUS_READY,
                "[Key Ready]",
                f"Depot {did} has an AES decryption key available on Hubcap.\n"
                "Click 'Refetch keys' to download it.",
            )
        if state == STATE_PENDING:
            return (
                STATUS_PENDING,
                "[Awaiting Key]",
                f"Depot {did} is released on Steam, but its AES decryption key is pending on Hubcap.\n"
                "It cannot be enabled in SLSsteam until the key is published.",
            )
        if state == STATE_UNKNOWN or reported_missing:
            return (
                STATUS_MISSING,
                "[No Key]",
                f"Depot {did} is not on Hubcap.\nNo AES decryption key or manifest is available.",
            )
        # Index not loaded yet: we cannot tell, so do not claim it is missing.
        return (
            STATUS_PENDING,
            "[Awaiting Key]",
            f"Depot {did} has no local key. Hubcap availability could not be checked right now.",
        )

__all__ = [
    "PRERELEASE",
    "ReleaseGate",
    "UNPUBLISHED",
    "UNRELEASED_STATES",
    "STATUS_MISSING",
    "STATUS_PENDING",
    "STATUS_READY",
    "STATUS_UNRELEASED",
    "UNRELEASED_TAG",
    "unreleased_tooltip",
]
