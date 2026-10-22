"""
Modular helper module for managing DLC-Only mode functionality in ASSella.
Provides centralized logic for checking DLC-only mode state, parsing installed DLCs,
syncing SLSsteam config, and formatting user-facing uninstall messages.
"""

import os
import re
import shutil
import logging
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple

from utils.settings import get_settings

logger = logging.getLogger("ACCELA.dlc_helpers")

_BASE_DEPOT_RE = re.compile(r"^(?:\[(?:WINDOWS|LINUX|MACOS|OSX|ALL)\]\s*)?Depot\s*\d+$", re.IGNORECASE)


def is_dlc_depot(
    depot_id: Any,
    desc: str = "",
    base_appid: Any = "",
    depot_meta: Optional[Dict[str, Any]] = None,
    dlc_appids: Optional[Any] = None,
) -> bool:
    """
    Returns True if depot_id belongs to a DLC rather than the base game.
    Checks:
    - Base AppID exclusion
    - Explicit membership in dlc_appids
    - 'dlcappid' or 'is_dlc' in depot_meta
    - Description indicators (e.g. '[DLC]', 'DLC 12345')
    """
    depot_id_str = str(depot_id).strip()
    base_appid_str = str(base_appid).strip() if base_appid else ""

    if not depot_id_str.isdigit():
        return False

    if base_appid_str and depot_id_str == base_appid_str:
        return False

    meta = depot_meta or {}
    meta_dlc = str(meta.get("dlcappid") or "").strip()
    if base_appid_str and meta_dlc == base_appid_str:
        return False

    dlc_set = {str(d).strip() for d in (dlc_appids or []) if str(d).strip().isdigit()}
    if depot_id_str in dlc_set:
        return True

    if meta_dlc and meta_dlc.isdigit():
        if not dlc_set or meta_dlc in dlc_set:
            return True

    if meta.get("is_dlc"):
        if not (base_appid_str and depot_id_str == base_appid_str) and not bool(_BASE_DEPOT_RE.match(desc.strip())):
            return True

    full_text = f"{desc} {meta.get('desc', '')} {meta.get('name', '')}".lower()
    if "[dlc" in full_text or bool(re.search(r"\bdlc\s+\d+", full_text)):
        if not bool(_BASE_DEPOT_RE.match(desc.strip())):
            return True

    return False


def is_base_game_main_depot(
    depot_id: str,
    desc: str,
    base_appid: str,
    depot_meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Returns True if depot_id represents a main base game executable/content/OS depot,
    rather than a DLC depot.
    """
    depot_id_str = str(depot_id).strip()
    base_appid_str = str(base_appid).strip() if base_appid else ""

    if base_appid_str and depot_id_str == base_appid_str:
        return True

    meta = depot_meta or {}
    meta_dlc = str(meta.get("dlcappid") or "").strip()
    if base_appid_str and meta_dlc == base_appid_str:
        return True

    # If it is clearly identified as a DLC depot, it cannot be a base game main depot
    if is_dlc_depot(depot_id_str, desc=desc, base_appid=base_appid_str, depot_meta=depot_meta):
        return False

    # If depot_meta is available and lacks dlcappid / is_dlc, it belongs to the base game
    if bool(depot_meta) and isinstance(depot_meta, dict):
        dlc_val = str(depot_meta.get("dlcappid") or "").strip()
        if not dlc_val or dlc_val == base_appid_str:
            if not depot_meta.get("is_dlc"):
                return True

    if desc and bool(_BASE_DEPOT_RE.match(desc.strip())):
        return True

    return False


def filter_dlc_depots_only(
    depot_ids: Any,
    base_appid: Any,
    depots_meta: Optional[Dict[str, Any]] = None,
    dlc_appids: Optional[Any] = None,
) -> List[str]:
    """
    Filter a collection of depot IDs so that ONLY DLC depots are kept.
    Base game content/executable depots and the root AppID are filtered out.
    """
    appid_str = str(base_appid).strip()
    dlc_set = {str(d).strip() for d in (dlc_appids or []) if str(d).strip().isdigit()}
    meta_dict = depots_meta or {}

    if not meta_dict and appid_str:
        try:
            from managers.db_manager import DatabaseManager
            app_info = DatabaseManager().get_app_info(appid_str, bypass_expiration=True)
            if app_info and app_info.get("depots"):
                meta_dict = app_info["depots"]
        except Exception as e:
            logger.debug(f"[DLCHelpers] depot metadata lookup failed for {appid_str}: {e}")

    dlc_mapped_depots: Set[str] = set()
    try:
        app_to_depots = build_app_to_depots_map(appid_str, extra_appids=dlc_set, candidate_depots=depot_ids)
        for aid, dids in app_to_depots.items():
            if aid != appid_str and (not dlc_set or aid in dlc_set):
                dlc_mapped_depots.update(str(d).strip() for d in dids)
    except Exception as e:
        logger.debug(f"[DLCHelpers] build_app_to_depots_map in filter_dlc_depots_only failed: {e}")

    filtered = []
    for d in depot_ids:
        d_str = str(d).strip()
        if not d_str.isdigit() or d_str == appid_str or d_str in dlc_set:
            continue
        if d_str in dlc_mapped_depots:
            filtered.append(d_str)
            continue
        d_meta = meta_dict.get(d_str) or meta_dict.get(int(d_str) if d_str.isdigit() else 0) or {}
        desc = d_meta.get("desc", "") if isinstance(d_meta, dict) else ""
        if is_dlc_depot(d_str, desc=desc, base_appid=appid_str, depot_meta=d_meta if isinstance(d_meta, dict) else None, dlc_appids=dlc_set):
            filtered.append(d_str)
        elif not is_base_game_main_depot(d_str, desc=desc, base_appid=appid_str, depot_meta=d_meta if isinstance(d_meta, dict) else None):
            if not meta_dict:
                filtered.append(d_str)

    return filtered


def build_app_to_depots_map(
    appid: Any,
    game_data: Optional[Dict[str, Any]] = None,
    extra_appids: Optional[Any] = None,
    candidate_depots: Optional[Any] = None,
) -> Dict[str, Set[str]]:
    """
    Dynamically map each AppID (base game and DLCs) to its associated depot IDs
    using the 7-tier smart mapping pipeline.
    Returns: { "appid_str": {base_depot_ids...}, "dlc_id_1": {dlc_depot_ids...}, ... }
    """
    appid_str = str(appid).strip()
    mapping: Dict[str, Set[str]] = {}

    target_apps: Set[str] = {appid_str}
    if extra_appids:
        for ea in extra_appids:
            s_ea = str(ea).strip()
            if s_ea and s_ea.isdigit():
                target_apps.add(s_ea)

    gd = game_data or {}
    gd_dlcs = gd.get("dlcs") or {}
    for dlc_id in (gd_dlcs.keys() if isinstance(gd_dlcs, dict) else gd_dlcs):
        s_dlc = str(dlc_id).strip()
        if s_dlc and s_dlc.isdigit():
            target_apps.add(s_dlc)

    # 1. Check DatabaseManager SQLite records
    try:
        from managers.db_manager import DatabaseManager
        db = DatabaseManager()
        db_info = db.get_app_info(appid_str, bypass_expiration=True)
        if db_info and db_info.get("depots"):
            for did, d_meta in db_info["depots"].items():
                did_str = str(did).strip()
                dlc_id = str(d_meta.get("dlcappid") or "").strip()
                if dlc_id and dlc_id.isdigit() and dlc_id != appid_str:
                    mapping.setdefault(dlc_id, set()).add(did_str)
                    target_apps.add(dlc_id)
                elif d_meta.get("is_dlc"):
                    desc = d_meta.get("name") or d_meta.get("desc") or ""
                    m = re.search(r"\[DLC(?:\s*(\d+))?\]", desc)
                    if m and m.group(1) and m.group(1) != appid_str:
                        mapping.setdefault(m.group(1), set()).add(did_str)
                        target_apps.add(m.group(1))
                    else:
                        mapping.setdefault(appid_str, set()).add(did_str)
                else:
                    mapping.setdefault(appid_str, set()).add(did_str)

        for aid in list(target_apps):
            if aid != appid_str:
                a_info = db.get_app_info(aid, bypass_expiration=True)
                if a_info and a_info.get("depots"):
                    for did in a_info["depots"].keys():
                        mapping.setdefault(aid, set()).add(str(did).strip())
    except Exception as e:
        logger.debug(f"[DLCHelpers] DB map error for {appid_str}: {e}")

    # 2. Check game_data / installed_depots from ACF & game_data depots
    inst_depots = gd.get("installed_depots") or {}
    for did, dinfo in inst_depots.items():
        did_str = str(did).strip()
        if isinstance(dinfo, dict):
            dlc_id = str(dinfo.get("dlcappid") or "").strip()
            if dlc_id and dlc_id.isdigit() and dlc_id != appid_str:
                mapping.setdefault(dlc_id, set()).add(did_str)
                target_apps.add(dlc_id)
            else:
                mapping.setdefault(appid_str, set()).add(did_str)
        else:
            mapping.setdefault(appid_str, set()).add(did_str)

    gd_depots = gd.get("depots") or {}
    if isinstance(gd_depots, dict):
        for did, dinfo in gd_depots.items():
            did_str = str(did).strip()
            if isinstance(dinfo, dict):
                dlc_id = str(dinfo.get("dlcappid") or "").strip()
                if dlc_id and dlc_id.isdigit() and dlc_id != appid_str:
                    mapping.setdefault(dlc_id, set()).add(did_str)
                    target_apps.add(dlc_id)
                else:
                    mapping.setdefault(appid_str, set()).add(did_str)
            else:
                mapping.setdefault(appid_str, set()).add(did_str)
    elif isinstance(gd_depots, (list, set)):
        for did in gd_depots:
            did_str = str(did).strip()
            mapping.setdefault(appid_str, set()).add(did_str)

    # 3. Check cached Lua DLC sections and MAIN GAME DEPOTS
    try:
        from utils.helpers import get_base_path
        lua_path = Path(get_base_path()) / "cached_luas" / f"{appid_str}.lua"
        if lua_path.exists():
            txt = lua_path.read_text(encoding="utf-8", errors="ignore")
            current_target_appid = None
            for line in txt.splitlines():
                sline = line.strip()
                m_dlc = re.search(r"\(AppID:\s*(\d+)\)", sline, re.IGNORECASE)
                if m_dlc:
                    current_target_appid = m_dlc.group(1)
                    if current_target_appid != appid_str:
                        target_apps.add(current_target_appid)
                elif sline.startswith(("-- MAIN APP", "-- MAIN APPLICATION", "-- MAIN GAME")):
                    current_target_appid = appid_str
                elif (
                    sline.startswith("-- DLCS WITH")
                    or sline.startswith("-- DLCS WITHOUT")
                    or sline.startswith("-- DLCS EXCLUDED")
                    or sline.startswith("-- EMPTY DEPOTS")
                    or sline.startswith("-- SHARED")
                    or "SHARED REDIST" in sline.upper()
                ):
                    current_target_appid = None
                elif current_target_appid:
                    # Anchor strictly to uncommented addappid statements
                    m_app = re.match(r"^\s*addappid\((\d+)", sline)
                    if m_app:
                        did = m_app.group(1)
                        if did != appid_str:
                            mapping.setdefault(current_target_appid, set()).add(did)
    except Exception as e:
        logger.debug(f"[DLCHelpers] Lua parse error for {appid_str}: {e}")

    # 4. Check Plugin Library for all target apps
    try:
        from utils.plugin_games import load_plugin_library
        plugin_lib = load_plugin_library()
        for aid in list(target_apps):
            rec = plugin_lib.get(aid, {})
            is_dlc_game = rec.get("dlc_only")
            dlc_ids = [str(x).strip() for x in rec.get("dlc_appids", []) if str(x).strip() and str(x).strip() != appid_str]
            for did in rec.get("depots", []):
                did_s = str(did).strip()
                if did_s != appid_str:
                    if is_dlc_game and dlc_ids:
                        # A DLC-only record stores the depots of *all* its
                        # mapped DLCs in one flat list. Without per-depot
                        # attribution (Steps 2/3) inventing one is arbitrary:
                        # the map is set-ordered, so the same depot resolves to
                        # a different DLC every run and the wrong entitlement
                        # is registered. Only a record with a single mapped DLC
                        # can claim every depot; anything else stays unclaimed
                        # and falls through to the base game (Step 6).
                        if len(dlc_ids) == 1:
                            mapping.setdefault(dlc_ids[0], set()).add(did_s)
                    else:
                        mapping.setdefault(aid, set()).add(did_s)
            for dlc_id in dlc_ids:
                target_apps.add(dlc_id)
    except Exception as e:
        logger.warning(f"[DLCHelpers] plugin library unreadable, plugin DLCs are not mapped: {e}")

    # 5. Direct match: where DLC AppID == DepotID (strictly for DLCs, NEVER base game)
    for aid in target_apps:
        if aid != appid_str:
            mapping.setdefault(aid, set()).add(aid)

    # 6. Fallback: Any remaining candidate depots not claimed by DLCs belong to base game
    all_known_candidates: Set[str] = set()
    if candidate_depots:
        all_known_candidates.update(str(d).strip() for d in candidate_depots if str(d).strip().isdigit())
    if gd_depots:
        all_known_candidates.update(str(d).strip() for d in (gd_depots.keys() if isinstance(gd_depots, dict) else gd_depots) if str(d).strip().isdigit())
    if inst_depots:
        all_known_candidates.update(str(d).strip() for d in (inst_depots.keys() if isinstance(inst_depots, dict) else inst_depots) if str(d).strip().isdigit())

    claimed_by_dlcs = set()
    for aid, dids in mapping.items():
        if aid != appid_str:
            claimed_by_dlcs.update(dids)

    for did in all_known_candidates:
        if did and did != appid_str and did not in claimed_by_dlcs:
            mapping.setdefault(appid_str, set()).add(did)

    return mapping


def resolve_dlc_mapping_for_selection(
    appid: Any,
    selected_items: Optional[Any] = None,
    game_data: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, str], Set[str]]:
    """
    Given a base game and user selection (which may contain DLC AppIDs and/or Depot IDs),
    returns:
      (selected_dlc_apps, selected_dlc_depots)
      - selected_dlc_apps: { dlc_appid: label_for_comment } (NEVER base appid, NEVER raw depot IDs)
      - selected_dlc_depots: { dlc_depot_id, ... }           (ONLY depots belonging to selected DLCs)
    """
    appid_str = str(appid).strip()
    gd = game_data or {}
    gd_dlcs = gd.get("dlcs") or {}

    app_to_depots = build_app_to_depots_map(
        appid_str, game_data=gd, candidate_depots=selected_items
    )

    # Build reverse depot -> DLC AppID mapping (excluding base game)
    depot_to_dlc: Dict[str, str] = {}
    dlc_apps_in_map: Set[str] = set()
    for aid, dids in app_to_depots.items():
        if aid != appid_str:
            dlc_apps_in_map.add(aid)
            for did in dids:
                depot_to_dlc[did] = aid

    base_depots = app_to_depots.get(appid_str, set())

    resolved_dlc_appids: Set[str] = set()
    include_parent: bool = False

    if selected_items:
        sel_list = [str(x).strip() for x in selected_items if str(x).strip().isdigit()]
        for x in sel_list:
            if x == appid_str:
                include_parent = True
                continue
            if x in base_depots:
                continue  # Base game depots are strictly ignored in DLC-only mode

            if x in dlc_apps_in_map or x in gd_dlcs:
                resolved_dlc_appids.add(x)
            elif x in depot_to_dlc:
                resolved_dlc_appids.add(depot_to_dlc[x])
    else:
        # If no explicit selection provided, default to all known DLCs for this game
        resolved_dlc_appids.update(dlc_apps_in_map)
        resolved_dlc_appids.update(str(d).strip() for d in (gd_dlcs.keys() if isinstance(gd_dlcs, dict) else gd_dlcs) if str(d).strip().isdigit())
        resolved_dlc_appids.discard(appid_str)

    # Build selected DLC depots (ONLY depots belonging to resolved DLCs, NEVER parent AppID)
    resolved_dlc_depots: Set[str] = set()
    for dlc_id in resolved_dlc_appids:
        associated = app_to_depots.get(dlc_id, set())
        for did in associated:
            if did != appid_str and did not in base_depots:
                resolved_dlc_depots.add(did)

    # Strict safeguard: parent AppID is NEVER in AdditionalDepots
    resolved_dlc_depots.discard(appid_str)

    # If user selected specific depots, and some were DLC depots, retain those specifically
    if selected_items:
        sel_set = {str(x).strip() for x in selected_items if str(x).strip().isdigit()}
        user_dlc_depots = {d for d in sel_set if d in resolved_dlc_depots}
        if user_dlc_depots:
            resolved_dlc_depots = user_dlc_depots

    # Resolve readable names/descriptions for the DLC AppIDs
    selected_dlc_apps: Dict[str, str] = {}
    db = None
    try:
        from managers.db_manager import DatabaseManager
        db = DatabaseManager()
    except Exception as e:
        logger.debug(f"[DLCHelpers] DLC names cannot be read from the DB: {e}")

    # If user explicitly included/selected the parent AppID, allow it in AdditionalApps
    if include_parent:
        parent_name = gd.get("game_name") or gd.get("name") or (db.get_app_info(appid_str, bypass_expiration=True).get("name") if db else "") or f"App {appid_str}"
        selected_dlc_apps[appid_str] = str(parent_name).strip()

    for dlc_id in sorted(resolved_dlc_appids, key=lambda x: int(x) if x.isdigit() else 0):
        name = gd_dlcs.get(dlc_id) or gd_dlcs.get(int(dlc_id) if dlc_id.isdigit() else dlc_id)
        if not name and db:
            d_info = db.get_app_info(dlc_id, bypass_expiration=True)
            if d_info:
                name = d_info.get("name")
        selected_dlc_apps[dlc_id] = str(name or f"DLC {dlc_id}").strip()

    return selected_dlc_apps, resolved_dlc_depots


def is_dlc_only_mode(appid: str) -> bool:
    """
    Check if dlc_only_mode is explicitly enabled in settings for a given base game AppID.
    """
    if not appid or appid in ("0", "N/A", "unknown"):
        return False
    try:
        settings = get_settings()
        return settings.value(f"dlc_only_mode/{appid}", False, type=bool)
    except Exception as e:
        logger.error(f"Error checking dlc_only_mode setting for {appid}: {e}")
        return False


# Mirrors the limits used for the depot selector's smart presets
# (src/ui/dialogs/depot_selection/selection_policy.py). They are repeated here
# so this Qt-free module never imports the dialog package.
STUB_SIZE_LIMIT = 1024
STUB_BYPASS_AT = 64


@dataclass(frozen=True)
class DlcCandidate:
    """One DLC depot discovered for a base game."""

    depot_id: str
    dlc_appid: str
    name: str
    size: int
    oslist: Tuple[str, ...]

    @property
    def is_stub(self) -> bool:
        return 0 < self.size <= STUB_SIZE_LIMIT


@dataclass(frozen=True)
class DlcDiscovery:
    """What is known about a base game's DLCs before the user picks anything."""

    appid: str
    catalog: Dict[str, str]
    depots: Tuple[DlcCandidate, ...]
    sources: Tuple[str, ...]

    @property
    def dlc_appids(self) -> List[str]:
        return list(self.catalog)

    @property
    def has_sized_depots(self) -> bool:
        """True when at least one DLC depot can be downloaded for real."""
        if not self.depots:
            return False
        # Games that ship dozens of DLCs also ship dozens of tiny stubs; the
        # size rule is meaningless there, so any DLC depot counts.
        if len(self.depots) >= STUB_BYPASS_AT:
            return True
        if all(candidate.size <= 0 for candidate in self.depots):
            # PICS reports no size for these depots at all, so presence is the
            # only fact available; calling them depotless would refuse real
            # content and offer DLC mode for a game that can download fine.
            return True
        return any(not candidate.is_stub for candidate in self.depots)

    @property
    def is_config_only(self) -> bool:
        """DLCs are known, but none of them has a downloadable depot."""
        return bool(self.catalog) and not self.has_sized_depots


_DLC_NAME_PREFIX_RE = re.compile(r"^\[DLC(?:\s*\d+)?\]\s*", re.IGNORECASE)


def _clean_dlc_name(value: Any) -> str:
    """Drop a ``[DLC]`` or ``[DLC 12345]`` prefix so the name is fit to show."""
    return _DLC_NAME_PREFIX_RE.sub("", str(value or "")).strip()


def _collect_dlc_depots(
    appid: str,
    depots: Optional[Dict[str, Any]],
    catalog: Dict[str, str],
    found: Dict[str, DlcCandidate],
) -> bool:
    """Merge one depots block into ``found``; True when anything was added."""
    if not isinstance(depots, dict):
        return False
    added = False
    for depot_id, meta in depots.items():
        depot_id = str(depot_id).strip()
        if not depot_id.isdigit():
            continue
        meta = meta if isinstance(meta, dict) else {}
        desc = str(meta.get("desc") or meta.get("name") or "")
        if not is_dlc_depot(depot_id, desc=desc, base_appid=appid, depot_meta=meta):
            continue
        if is_base_game_main_depot(depot_id, desc, appid, meta):
            continue
        dlc_appid = str(meta.get("dlcappid") or "").strip()
        try:
            size = int(meta.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        oslist = meta.get("oslist") or ()
        if isinstance(oslist, str):
            oslist = tuple(part for part in oslist.replace(",", " ").split() if part)
        else:
            oslist = tuple(str(part) for part in oslist)
        name = _clean_dlc_name(meta.get("name") or meta.get("desc") or "")
        found[depot_id] = DlcCandidate(
            depot_id=depot_id,
            dlc_appid=dlc_appid,
            name=name,
            size=size,
            oslist=oslist,
        )
        if dlc_appid.isdigit() and dlc_appid != appid:
            catalog.setdefault(dlc_appid, name)
        added = True
    return added


def _absorb_app_info(
    appid: str,
    info: Any,
    catalog: Dict[str, str],
    found: Dict[str, DlcCandidate],
) -> bool:
    """Read depots and the DLC id list out of one app-info payload."""
    if not isinstance(info, dict):
        return False
    added = _collect_dlc_depots(appid, info.get("depots"), catalog, found)
    ids = info.get("listofdlc")
    if isinstance(ids, str):
        parts = ids.replace(",", " ").split()
    elif isinstance(ids, (list, tuple, set)):
        parts = [str(part) for part in ids]
    else:
        parts = []
    for part in parts:
        part = str(part).strip()
        if part.isdigit() and part != appid:
            catalog.setdefault(part, "")
    return added


def discover_dlc_depots(
    appid: Any,
    depots: Optional[Dict[str, Any]] = None,
    *,
    app_info: Optional[Dict[str, Any]] = None,
    allow_store: bool = False,
    depot_info_fetcher: Optional[Any] = None,
    store_lookup: Optional[Any] = None,
) -> DlcDiscovery:
    """Work out which DLCs a base game has and which of them can be downloaded.

    The cheap sources are consulted first (depots already in memory, then the
    cached app info); the network is only touched when those turn up nothing.
    The Steam Store is a last resort and only when ``allow_store`` is set,
    because it knows more DLCs than PICS does but its list can include
    soundtracks and other entries that carry no depot at all.
    """
    appid = str(appid or "").strip()
    if not appid or appid in ("0", "N/A", "unknown"):
        return DlcDiscovery(appid, {}, (), ())

    catalog: Dict[str, str] = {}
    found: Dict[str, DlcCandidate] = {}
    sources: List[str] = []

    if depots:
        if _collect_dlc_depots(appid, depots, catalog, found):
            sources.append("memory")
    if app_info and _absorb_app_info(appid, app_info, catalog, found):
        sources.append("cache")

    if not found:
        fetcher = depot_info_fetcher
        if fetcher is None:
            try:
                from core.steam_api import get_depot_info_from_api

                fetcher = get_depot_info_from_api
            except Exception:
                logger.debug("DLC discovery: no depot fetcher available", exc_info=True)
                fetcher = None
        if fetcher is not None:
            try:
                info = fetcher(appid)
            except Exception:
                logger.debug("DLC discovery: depot lookup failed for %s", appid, exc_info=True)
                info = None
            if _absorb_app_info(appid, info, catalog, found):
                sources.append("pics")

    if allow_store and not found:
        lookup = store_lookup
        if lookup is None:
            lookup = lambda base_appid: get_all_dlcs_for_app(base_appid, {}, allow_network=True)
        try:
            names = lookup(appid)
        except Exception:
            logger.debug("DLC discovery: store lookup failed for %s", appid, exc_info=True)
            names = None
        if isinstance(names, dict):
            pairs = list(names.items())
        elif isinstance(names, list):
            pairs = [
                (item.get("dlc_appid"), item.get("dlc_name"))
                for item in names
                if isinstance(item, dict)
            ]
        else:
            pairs = []
        if pairs:
            sources.append("store")
            for dlc_appid, name in pairs:
                dlc_appid = str(dlc_appid or "").strip()
                if dlc_appid.isdigit() and dlc_appid != appid:
                    catalog.setdefault(dlc_appid, str(name or ""))

    ordered = tuple(found[key] for key in sorted(found, key=lambda item: (found[item].name.lower(), item)))
    return DlcDiscovery(appid, catalog, ordered, tuple(sources))


def get_dlc_only_info(base_appid: str) -> List[Dict[str, str]]:
    """
    If dlc_only_mode is enabled for base_appid, parses all installed depots
    from '{base_appid}.depot' and returns a list of dictionaries:
    [{'dlc_appid': str, 'dlc_name': str, 'base_game_name': str}]
    for each depot that is not a base game OS depot.
    """
    if not is_dlc_only_mode(base_appid):
        return []

    from utils.helpers import get_base_path
    depot_file = get_base_path() / "depots" / f"{base_appid}.depot"
    if not depot_file.exists():
        return []

    results = []
    try:
        from managers.db_manager import DatabaseManager
        db = DatabaseManager()
        app_info = db.get_app_info(base_appid)
        base_game_name = app_info.get("name", "") if app_info else ""
        base_depots_data = app_info.get("depots", {}) if app_info else {}

        for line in depot_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            if parts and parts[0].strip():
                did = parts[0].strip()

                # Skip main OS depots of the base game
                depot_meta = base_depots_data.get(did, {})
                desc = depot_meta.get("desc") or depot_meta.get("name") or ""
                if is_base_game_main_depot(did, desc, base_appid, depot_meta=depot_meta):
                    continue

                # Resolve actual DLC AppID if this depot belongs to a DLC
                raw_dlc_id = str(depot_meta.get("dlcappid") or "").strip()
                dlc_appid = raw_dlc_id if (raw_dlc_id and raw_dlc_id != base_appid) else did

                dlc_name = ""
                dlc_info = db.get_app_info(dlc_appid)
                if dlc_info:
                    dlc_name = dlc_info.get("name", "")

                if not dlc_name:
                    # Fallback to direct sqlite query to bypass cache expiration checks
                    try:
                        conn = sqlite3.connect(str(db.db_path))
                        try:
                            cur = conn.cursor()
                            cur.execute("SELECT name FROM apps WHERE appid = ?", (dlc_appid,))
                            row = cur.fetchone()
                            if row and row[0]:
                                dlc_name = row[0]
                        finally:
                            conn.close()
                    except sqlite3.Error as e:
                        logger.debug(f"[DLCMode] app name lookup for {dlc_appid} failed: {e}")

                if not dlc_name and desc:
                    dlc_name = desc.replace(" - Depot " + did, "").strip()
                    dlc_name = re.sub(r"^\[(?:WINDOWS|LINUX|MACOS|OSX|ALL)\]\s*", "", dlc_name, flags=re.IGNORECASE).strip()

                results.append({
                    "dlc_appid": dlc_appid,
                    "dlc_name": dlc_name,
                    "base_game_name": base_game_name
                })
    except Exception as e:
        logger.error(f"Error checking dlc_only_mode for {base_appid}: {e}")

    return results


def get_all_dlcs_for_app(appid: str, game_data: Optional[dict] = None, allow_network: bool = True) -> list:
    """
    Returns a unified list of DLC dicts for an app:
      [{"dlc_appid": str, "dlc_name": str, "base_game_name": str}, ...]

    Order of resolution:
    1. Saved DLC-only info in QSettings (from a previous DLC toggle or import)
    2. game_data["dlcs"] (from local manifest / SLS config)
    3. Steam Store API appdetails (if allow_network=True)
    """
    appid_str = str(appid).strip()
    game_name = (game_data.get("game_name") if game_data else "") or ""
    results_by_id = {
        str(item.get("dlc_appid")): item
        for item in get_dlc_only_info(appid_str)
        if str(item.get("dlc_appid", "")).isdigit()
        and str(item.get("dlc_appid")) != appid_str
    }

    # Check game_data["dlcs"]
    if game_data and game_data.get("dlcs"):
        dlc_map = game_data["dlcs"]
        if isinstance(dlc_map, dict):
            for d_id, d_name in dlc_map.items():
                results_by_id.setdefault(str(d_id), {
                    "dlc_appid": str(d_id),
                    "dlc_name": str(d_name or f"DLC {d_id}"),
                    "base_game_name": game_name,
                })
        elif isinstance(dlc_map, list):
            for d_id in dlc_map:
                results_by_id.setdefault(str(d_id), {
                    "dlc_appid": str(d_id),
                    "dlc_name": f"DLC {d_id}",
                    "base_game_name": game_name,
                })

    # Some metadata describes the DLC on its content-depot record rather than
    # in the app's explicit DLC array. Preserve that entitlement relationship.
    if game_data and isinstance(game_data.get("depots"), dict):
        for depot_info in game_data["depots"].values():
            if not isinstance(depot_info, dict):
                continue
            dlc_id = str(depot_info.get("dlcappid") or "").strip()
            if dlc_id.isdigit() and dlc_id != appid_str:
                results_by_id.setdefault(dlc_id, {
                    "dlc_appid": dlc_id,
                    "dlc_name": str(depot_info.get("desc") or f"DLC {dlc_id}"),
                    "base_game_name": game_name,
                })
    if not allow_network:
        return list(results_by_id.values())

    # Fetch from Steam Store API (handles uninstalled/owned games with 64+ DLCs like 4678800)
    try:
        import urllib.request
        import json

        url = f"https://store.steampowered.com/api/appdetails?appids={appid_str}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            app_info = data.get(appid_str, {}).get("data", {})
            if not game_name:
                game_name = app_info.get("name", "")
            dlc_ids = app_info.get("dlc", [])
            for d_id in dlc_ids:
                results_by_id.setdefault(str(d_id), {
                    "dlc_appid": str(d_id),
                    "dlc_name": (
                        f"{game_name} - DLC {d_id}" if game_name else f"DLC {d_id}"
                    ),
                    "base_game_name": game_name,
                })
    except Exception as e:
        logger.debug(f"Could not fetch DLC list for {appid_str} from Steam Store API: {e}")

    return list(results_by_id.values())


def is_goldberg_applied(game_dir: str) -> bool:
    """Check for Goldberg backup files (.valve) in game directory."""
    if not game_dir or not os.path.exists(game_dir):
        return False
    for root, _, files in os.walk(game_dir):
        for fname in files:
            if fname.lower() in (
                "steam_api.dll.valve",
                "steam_api64.dll.valve",
                "libsteam_api.so.valve",
                "libsteam_api64.so.valve",
            ):
                return True
    return False


def restore_goldberg_backups(game_dir: str) -> List[str]:
    """
    Restore original Steam DLLs from .valve backups and remove steam_settings & steam_appid.txt.
    Returns list of restored file names.
    """
    restored: List[str] = []
    if not game_dir or not os.path.isdir(game_dir):
        return restored

    for root, _, files in os.walk(game_dir):
        if any(f.lower().endswith((".dll.valve", ".so.valve")) for f in files):
            st_dir = os.path.join(root, "steam_settings")
            if os.path.isdir(st_dir):
                shutil.rmtree(st_dir, ignore_errors=True)
            aid_txt = os.path.join(root, "steam_appid.txt")
            if os.path.exists(aid_txt):
                try:
                    os.remove(aid_txt)
                except OSError as e:
                    logger.debug(f"[DLCMode] could not remove {aid_txt}: {e}")
            for fname in files:
                if fname.lower().endswith((".dll.valve", ".so.valve")):
                    orig_name = fname[:-6]
                    bak_path = os.path.join(root, fname)
                    orig_path = os.path.join(root, orig_name)
                    if os.path.exists(orig_path):
                        try:
                            os.remove(orig_path)
                        except OSError as e:
                            logger.debug(f"[DLCMode] could not remove {orig_path} before restoring {orig_name}: {e}")
                    try:
                        os.rename(bak_path, orig_path)
                        restored.append(orig_name)
                        logger.info(f"[DLCMode] Restored Goldberg backup {orig_name} in {root}")
                    except Exception as r_err:
                        logger.warning(f"Failed to restore {bak_path}: {r_err}")
    return restored


def get_base_depot_ids_for_app(appid: str, game_data: Optional[dict] = None) -> List[str]:
    """
    Identify all depot IDs that belong to the base game (not a DLC) for appid.
    """
    appid_str = str(appid).strip()
    base_depots = {appid_str}

    # 1. From game_data
    if game_data and game_data.get("depots"):
        for did, dinfo in game_data["depots"].items():
            did_str = str(did)
            if isinstance(dinfo, dict):
                dlcappid = dinfo.get("dlcappid")
                desc = dinfo.get("desc", "")
                if not dlcappid or is_base_game_main_depot(did_str, desc, appid_str):
                    base_depots.add(did_str)
            else:
                base_depots.add(did_str)

    # 2. From DatabaseManager
    try:
        from managers.db_manager import DatabaseManager
        db = DatabaseManager()
        app_info = db.get_app_info(appid_str, bypass_expiration=True)
        if app_info and app_info.get("depots"):
            for did, dinfo in app_info["depots"].items():
                did_str = str(did)
                if isinstance(dinfo, dict):
                    dlcappid = dinfo.get("dlcappid")
                    desc = dinfo.get("desc", "")
                    if not dlcappid or is_base_game_main_depot(did_str, desc, appid_str):
                        base_depots.add(did_str)
                else:
                    base_depots.add(did_str)
    except Exception as e:
        logger.debug(f"Could not load depots from DB for {appid_str}: {e}")

    # 3. Exclude any known DLC AppIDs
    try:
        dlc_list = get_all_dlcs_for_app(appid_str, game_data, allow_network=False)
        dlc_appids = {str(d["dlc_appid"]) for d in dlc_list}
        base_depots = {d for d in base_depots if d not in dlc_appids}
    except Exception as e:
        logger.debug(f"[DLCHelpers] DLC AppIDs unavailable for {appid_str}, DLC depots stay in the base list: {e}")

    return sorted(list(base_depots))


def get_installed_depot_ids(appid: str) -> List[str]:
    """Return the depot ids that were actually installed for a game.

    The ``{base_path}/depots/{appid}.depot`` file is written when a game is
    installed and lists the depots that were fetched. It is the only local
    record of what is on disk.

    This matters because ``depot_selection/{appid}`` in QSettings is a
    *pre-selection for the next download* (the update flow and the install
    flow both rewrite it), not a record of what is installed. Trusting it to
    narrow the mounted set can drop depots that are on disk.
    """
    appid_str = str(appid).strip()
    if not appid_str:
        return []
    try:
        from utils.helpers import get_base_path
        depot_file = get_base_path() / "depots" / f"{appid_str}.depot"
        if not depot_file.exists():
            return []
        depots = []
        for line in depot_file.read_text(errors="ignore").splitlines():
            line = line.strip()
            if line and ":" in line:
                did = line.split(":", 1)[0].strip()
                if did.isdigit() and did not in depots:
                    depots.append(did)
        return depots
    except Exception as e:
        logger.debug(f"[DLCHelpers] installed depot list for {appid_str} is unreadable: {e}")
        return []


def resolve_depot_scope(
    appid: str,
    candidate_depots: List[str],
    extra_scope: Optional[List[str]] = None,
) -> Tuple[List[str], List[str]]:
    """Narrow a depot list to the user's chosen scope.

    ``depot_selection/{appid}`` in QSettings is the set the user confirmed for
    this game, but it is also rewritten by the update flow as a *pre-selection
    for the next download*, so it can be narrower than what is on disk. It may
    therefore never drop a depot that the install actually fetched: doing so
    would mount a game without content that is already on the machine.

    Returns ``(kept, dropped)`` so the caller can tell the user which depots
    were left out instead of silently mounting a partial game.
    """
    appid_str = str(appid).strip()
    candidates = [str(d) for d in candidate_depots if str(d).strip()]

    scope: Optional[Set[str]] = None
    try:
        saved = get_saved_depot_selection(appid_str)
        if saved:
            scope = {str(d) for d in saved}
    except Exception as e:
        logger.debug(f"[DLCHelpers] saved scope for {appid_str} is unreadable: {e}")

    if extra_scope:
        scope = (scope or set()) | {str(d) for d in extra_scope}

    installed = set(get_installed_depot_ids(appid_str))
    if scope and installed and not installed.issubset(scope):
        # The saved scope does not cover what is installed: it is stale or
        # partial. Keep the installed depots.
        scope = scope | installed

    if not scope:
        return candidates, []

    kept = [d for d in candidates if d in scope]
    if not kept:
        # The scope has nothing in common with the candidates: it belongs to a
        # different game state. Narrowing to it would mount nothing.
        return candidates, []

    dropped = [d for d in candidates if d not in scope]
    return kept, dropped


def get_saved_depot_selection(appid: str) -> List[str]:
    """Retrieve user's selected depots from QSettings or .depot file."""
    appid_str = str(appid).strip()
    try:
        settings = get_settings()
        saved = settings.value(f"depot_selection/{appid_str}", "", type=str)
        if saved:
            import json
            data = json.loads(saved)
            if isinstance(data, dict) and "selected" in data:
                return [str(d) for d in data["selected"]]
            elif isinstance(data, list):
                return [str(d) for d in data]
    except Exception as e:
        logger.debug(f"[DLCHelpers] saved selection for {appid_str} is unreadable: {e}")

    try:
        from utils.helpers import get_base_path
        depot_file = get_base_path() / "depots" / f"{appid_str}.depot"
        if depot_file.exists():
            depots = []
            for line in depot_file.read_text().splitlines():
                line = line.strip()
                if line and ":" in line:
                    did = line.split(":", 1)[0].strip()
                    if did.isdigit():
                        depots.append(did)
            if depots:
                return depots
    except Exception as e:
        logger.debug(f"[DLCHelpers] depot file fallback for {appid_str} failed: {e}")

    return []


def purge_and_sanitize_for_dlc_only(
    appid: str,
    game_name: str = "",
    install_path: Optional[str] = None,
    game_data: Optional[dict] = None,
    selected_depots: Optional[List[str]] = None,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Sanity check and purge applied settings when DLC-only mode is active,
    retaining ONLY the depots the user explicitly selected.
    Works for BOTH ASSella mode and Native Steam (at0m) mode:
      1. SLSsteam config.yaml:
         - Removes base game AppID from AdditionalApps (adds DLC AppIDs or DlcData).
         - Configures AdditionalDepots to contain ONLY the depots the user selected.
         - Cleans DecryptionKeys for unselected depots and base game AppID.
         - Removes AppID from FakeAppIds (disables SLSonline bypass).
         - Removes Steam launch option overrides (disables Netsock LD_AUDIT).
         - Cleans any pinned ManifestIds for unselected depots of this game.
      2. Local Game Directory:
         - Detects and reverts Goldberg Steam emulator (.valve backups restored, steam_settings & steam_appid.txt removed).
         - Detects and removes EOS proxy (restoring original EOS DLL).
    """
    summary: Dict[str, Any] = {
        "base_app_removed": False,
        "dlcs_added": [],
        "base_depots_removed": [],
        "base_keys_removed": [],
        "fake_app_removed": False,
        "launch_options_removed": False,
        "manifest_ids_removed": [],
        "goldberg_restored": [],
        "eos_proxy_removed": False,
    }
    appid_str = str(appid).strip()
    if not appid_str or appid_str in ("0", "N/A", "unknown"):
        return summary

    from utils.yaml_config_manager import (
        get_user_config_path,
        remove_additional_app,
        add_additional_app,
        remove_additional_depot,
        add_additional_depot,
        get_additional_depots,
        add_decryption_key,
        remove_decryption_key,
        get_decryption_keys,
        remove_fake_app_id,
        remove_launch_option,
        add_dlc_data_batch,
        remove_dlc_data,
    )
    try:
        from core.native_steam.steam_manifest_pinning import remove_manifest_ids, get_manifest_ids
    except ImportError:
        remove_manifest_ids, get_manifest_ids = None, None

    if config_path is None:
        try:
            config_path = get_user_config_path()
        except Exception:
            config_path = None

    # Resolve user's selected depots
    user_sel = selected_depots
    if not user_sel and game_data:
        user_sel = game_data.get("selected_depots") or (
            game_data.get("metadata", {}).get("selected_depots_list")
        )
    if not user_sel:
        saved = get_saved_depot_selection(appid_str)
        if saved:
            user_sel = saved

    user_sel_set = {str(d) for d in user_sel} if user_sel else None

    # Known depots for this game
    all_game_depots = set()
    if game_data and game_data.get("depots"):
        d_raw = game_data["depots"]
        all_game_depots.update(str(d) for d in (d_raw.keys() if isinstance(d_raw, dict) else d_raw))
    try:
        from managers.db_manager import DatabaseManager
        db = DatabaseManager()
        app_info = db.get_app_info(appid_str, bypass_expiration=True)
        if app_info and app_info.get("depots"):
            all_game_depots.update(str(d) for d in app_info["depots"].keys())
    except Exception as e:
        logger.debug(f"[DLCMode] known depot list limited to the in-memory data for {appid_str}: {e}")

    if config_path and config_path.exists():
        # 1. Resolve smart DLC & depot mapping
        sel_dlcappid_set, sel_dlc_depots = resolve_dlc_mapping_for_selection(
            appid_str, selected_items=user_sel_set, game_data=game_data
        )

        # Base AppID handling in AdditionalApps:
        # If user explicitly selected/included the parent AppID, keep/add it in AdditionalApps.
        # Otherwise, remove the base AppID from AdditionalApps.
        if appid_str in sel_dlcappid_set:
            parent_comment = game_name or f"App {appid_str}"
            add_additional_app(config_path, appid_str, parent_comment)
        else:
            if remove_additional_app(config_path, appid_str):
                summary["base_app_removed"] = True

        pure_dlcs = {aid: dname for aid, dname in sel_dlcappid_set.items() if aid != appid_str}
        if len(pure_dlcs) >= 64:
            add_dlc_data_batch(config_path, appid_str, pure_dlcs)
            summary["dlcs_added"].extend(list(pure_dlcs.keys()))
        else:
            remove_dlc_data(config_path, appid_str)
            for did, dname in pure_dlcs.items():
                comment = f"[DLC] {dname} / {game_name}" if game_name else f"[DLC] {dname}"
                if add_additional_app(config_path, did, comment):
                    summary["dlcs_added"].append(did)

        # 2. AdditionalDepots: ONLY selected DLC depots, NEVER parent AppID and NEVER base game main depots!
        sel_dlc_depots.discard(appid_str)
        existing_depots = get_additional_depots(config_path)
        for d in all_game_depots:
            if d in existing_depots and d not in sel_dlc_depots:
                if remove_additional_depot(config_path, d, excluding_appid=appid_str):
                    summary["base_depots_removed"].append(d)

        depots_meta = (game_data.get("depots") or {}) if game_data else {}
        for d in sorted(sel_dlc_depots, key=lambda x: int(x) if x.isdigit() else 0):
            if d == appid_str:
                continue  # Safeguard: parent AppID is NEVER in AdditionalDepots
            meta = depots_meta.get(d) or depots_meta.get(int(d) if d.isdigit() else d) or {}
            desc = meta.get("desc", "") if isinstance(meta, dict) else ""
            comment = f"{game_name} [{desc}] ({appid_str})" if desc else f"{game_name} ({appid_str})"
            add_additional_depot(config_path, d, comment=comment)

        # 3. DecryptionKeys: Remove unselected depot keys, then add keys for selected DLC depots
        existing_keys = get_decryption_keys(config_path)
        if appid_str not in sel_dlcappid_set and appid_str in existing_keys:
            if remove_decryption_key(config_path, appid_str, excluding_appid=appid_str):
                summary["base_keys_removed"].append(appid_str)

        for d in all_game_depots:
            if d in existing_keys and d not in sel_dlc_depots and d not in sel_dlcappid_set:
                if remove_decryption_key(config_path, d, excluding_appid=appid_str):
                    summary["base_keys_removed"].append(d)

        # Ensure keys for selected DLC depots and DLC AppIDs are added so Steam can decrypt DLC content
        depot_keys = {}
        if game_data and game_data.get("depot_keys"):
            depot_keys.update({str(k): v for k, v in game_data["depot_keys"].items()})
        if game_data and game_data.get("depots"):
            for did, dinfo in game_data["depots"].items():
                if isinstance(dinfo, dict) and dinfo.get("key"):
                    depot_keys[str(did)] = dinfo["key"]
        try:
            from managers.depot_key_manager import DepotKeyManager
            dkm = DepotKeyManager.get_instance()
            cached_dkm = dkm.get_depot_keys(appid_str)
            if cached_dkm:
                for k, v in cached_dkm.items():
                    depot_keys.setdefault(str(k), v)
        except Exception as e:
            logger.debug(f"[DLCMode] Error querying DepotKeyManager for {appid_str}: {e}")

        # Add decryption keys for selected DLC depots (and selected DLC appids / parent if keyed)
        for d in (sel_dlc_depots | set(sel_dlcappid_set.keys())):
            if str(d) in depot_keys:
                k = depot_keys[str(d)]
                if k:
                    meta = depots_meta.get(d) or depots_meta.get(int(d) if str(d).isdigit() else d) or {}
                    desc = meta.get("desc", "") if isinstance(meta, dict) else ""
                    if str(d) == appid_str:
                        comment = f"{game_name} [AppKey] ({appid_str})" if game_name else f"AppKey ({appid_str})"
                    else:
                        comment = f"{game_name} [{desc}] ({appid_str})" if desc else f"{game_name} ({appid_str})"
                    if add_decryption_key(config_path, str(d), k, comment=comment):
                        summary.setdefault("keys_added", []).append(str(d))

        # 4b. Sync DLC manifests to Steam depotcache so Steam does not fail on MRC
        try:
            from core.native_steam.native_steam_handoff import sync_manifests_to_depotcache
            sync_manifests_to_depotcache(appid_str)
        except Exception as e:
            logger.debug(f"[DLCMode] Error syncing manifests to depotcache: {e}")

        # 5. Remove FakeAppIds for this app (disable SLSonline)
        if remove_fake_app_id(config_path, appid_str):
            summary["fake_app_removed"] = True

        # 6. Remove launch options (disable Netsock LD_AUDIT / custom wrappers)
        if remove_launch_option(config_path, appid_str):
            summary["launch_options_removed"] = True

        # 7. Remove any pinned ManifestIds for unselected depots of this game
        if get_manifest_ids and remove_manifest_ids and user_sel_set is not None:
            current_pins = get_manifest_ids(config_path)
            pins_to_remove = [d for d in all_game_depots if d in current_pins and d not in user_sel_set]
            if pins_to_remove:
                if remove_manifest_ids(config_path, pins_to_remove):
                    summary["manifest_ids_removed"].extend(pins_to_remove)

    # 8. Revert emulators / proxies in game install path
    target_install = install_path
    if not target_install and game_data:
        target_install = game_data.get("install_path") or game_data.get("dest_path")
    if not target_install:
        try:
            settings = get_settings()
            target_install = settings.value(f"game_dir/{appid_str}", "")
        except Exception as e:
            logger.debug(f"[DLCMode] no install path recorded for {appid_str}: {e}")

    if target_install and os.path.isdir(target_install):
        # A. Goldberg restore
        try:
            restored = restore_goldberg_backups(target_install)
            if restored:
                summary["goldberg_restored"].extend(restored)
        except Exception as ge:
            logger.warning(f"Error restoring Goldberg in {target_install}: {ge}")

        # B. EOS proxy remove
        try:
            from utils.eos_detector import EOSDetector
            status = EOSDetector.get_proxy_status(target_install)
            if status in ("active", "stale"):
                if EOSDetector.remove_proxy(target_install):
                    summary["eos_proxy_removed"] = True
        except Exception as ee:
            logger.warning(f"Error removing EOS proxy in {target_install}: {ee}")

    return summary


def sync_dlc_only_sls_config(
    config_path: Path, appid: str, game_name: str, game_data: Optional[dict] = None
) -> bool:
    """
    Syncs a game to SLSsteam config.yaml based on DLC-only mode status.
    If DLC-only mode is active:
      - Runs full purge_and_sanitize_for_dlc_only to ensure only DLC items exist.
    Else:
      - Adds the base game AppID to AdditionalApps.
      - Removes DLC AppIDs from AdditionalApps.
      - If the game has 64 or more DLCs, adds them under DlcData to bypass Steam's 64 DLC limit.
    """
    from utils.yaml_config_manager import (
        add_additional_app,
        remove_additional_app,
        add_dlc_data_batch,
        remove_dlc_data,
    )

    appid_str = str(appid).strip()
    dlc_mode = is_dlc_only_mode(appid_str)
    dlc_list = get_all_dlcs_for_app(appid_str, game_data, allow_network=dlc_mode)

    if dlc_mode:
        install_path = (game_data.get("install_path") or game_data.get("dest_path")) if game_data else None
        res = purge_and_sanitize_for_dlc_only(
            appid_str, game_name, install_path=install_path, game_data=game_data, config_path=config_path
        )
        return bool(res.get("dlcs_added") or res.get("base_app_removed"))
    else:
        # Regular game mode - ensure base game in AdditionalApps
        added = add_additional_app(config_path, appid_str, game_name)
        # Remove individual DLCs from AdditionalApps if they were present
        if dlc_list:
            for dlc_entry in dlc_list:
                remove_additional_app(config_path, str(dlc_entry["dlc_appid"]))

        # If the game has 64 or more DLCs, ensure DlcData is populated
        if dlc_list and len(dlc_list) >= 64:
            dlc_dict = {str(d["dlc_appid"]): d["dlc_name"] for d in dlc_list}
            add_dlc_data_batch(config_path, appid_str, dlc_dict)
        elif not dlc_list or len(dlc_list) < 64:
            remove_dlc_data(config_path, appid_str)
        return added


def get_dlc_uninstall_message(game_data: dict) -> str:
    """
    Build a polished, user-friendly confirmation message for DLC-Only uninstall,
    including a bulleted list of target DLC names and AppIDs.
    """
    game_name = game_data.get("game_name", "Unknown")
    appid = str(game_data.get("appid", "0"))
    dlc_list = get_dlc_only_info(appid)

    confirm_msg = f"Are you sure you want to uninstall DLC(s) for '{game_name}'?\n\n"
    confirm_msg += "Since this is a DLC Only installation, the base game files will NOT be deleted.\n"
    confirm_msg += "Only downloaded DLC depot files and SLS configuration entries will be removed.\n\n"

    if dlc_list:
        confirm_msg += "Target DLC(s) to remove:\n"
        for dlc in dlc_list:
            d_name = dlc.get("dlc_name") or dlc.get("dlc_appid")
            confirm_msg += f"  • {d_name} (AppID: {dlc.get('dlc_appid')})\n"
        confirm_msg += "\n"
    else:
        confirm_msg += "Target: Installed DLC depot files\n\n"

    confirm_msg += "This action cannot be undone!"
    return confirm_msg


def has_game_dlcs(appid: str, depots: Optional[Dict[str, Any]] = None) -> bool:
    """
    Reliably checks whether the game has DLCs using multiple sources:
    1. In-memory depots dictionary (if provided).
    2. Local SQLite database cache (hasdepotsindlc, listofdlc, depots).
    3. Steam PICS / SteamCMD depot info from API.
    4. Saved depots on disk ({appid}.depot).
    """
    appid_str = str(appid) if appid else ""
    if not appid_str or appid_str in ("0", "N/A", "unknown"):
        return False

    # 1. In-memory depots
    if depots and isinstance(depots, dict):
        for did, d_data in depots.items():
            if not isinstance(d_data, dict):
                continue
            desc = str(d_data.get("desc") or d_data.get("name") or "")
            if is_base_game_main_depot(str(did), desc, appid_str):
                continue
            if (
                d_data.get("is_dlc") is True
                or bool(d_data.get("dlcappid"))
                or "[dlc]" in desc.lower()
                or bool(re.search(r"\bDLC\s+\d+", desc, re.IGNORECASE))
            ):
                return True

    # 2. DatabaseManager cache
    try:
        from managers.db_manager import DatabaseManager
        db = DatabaseManager()
        app_info = db.get_app_info(appid_str, bypass_expiration=True)
        if app_info:
            if app_info.get("hasdepotsindlc") in (1, "1", True):
                return True
            listofdlc = app_info.get("listofdlc")
            if listofdlc:
                if isinstance(listofdlc, (list, tuple)) and len(listofdlc) > 0:
                    return True
                if isinstance(listofdlc, str) and any(c.isdigit() for c in listofdlc):
                    return True
                if isinstance(listofdlc, int) and listofdlc > 0:
                    return True
            db_depots = app_info.get("depots") or {}
            for did, d_data in db_depots.items():
                if isinstance(d_data, dict):
                    desc = str(d_data.get("desc") or d_data.get("name") or "")
                    if is_base_game_main_depot(str(did), desc, appid_str):
                        continue
                    if d_data.get("is_dlc") or d_data.get("dlcappid"):
                        return True
    except Exception as e:
        logger.debug(f"[DLCMode] DB DLC check error for {appid_str}: {e}")

    # 3. Live Steam PICS / SteamCMD app/depot info
    try:
        from core.steam_api import get_depot_info_from_api
        api_info = get_depot_info_from_api(appid_str)
        if api_info:
            if api_info.get("hasdepotsindlc") in (1, "1", True):
                return True
            listofdlc = api_info.get("listofdlc")
            if listofdlc:
                if isinstance(listofdlc, (list, tuple)) and len(listofdlc) > 0:
                    return True
                if isinstance(listofdlc, str) and any(c.isdigit() for c in listofdlc):
                    return True
                if isinstance(listofdlc, int) and listofdlc > 0:
                    return True
    except Exception as e:
        logger.debug(f"[DLCMode] API DLC check error for {appid_str}: {e}")

    # 4. Check saved .depot file for multi-depot non-base entries
    try:
        from utils.helpers import get_base_path
        depot_file = get_base_path() / "depots" / f"{appid_str}.depot"
        if depot_file.exists():
            lines = [l.strip() for l in depot_file.read_text().splitlines() if l.strip()]
            if len(lines) > 1:
                for line in lines:
                    did = line.split(":", 1)[0].strip()
                    if did and did != appid_str:
                        return True
    except Exception as e:
        logger.debug(f"[DLCMode] saved depot listing for {appid_str} could not be read: {e}")

    return False


def get_injected_dlcs_from_config(base_appid: str, config_path: Optional[Path] = None) -> Set[str]:
    """Returns set of DLC AppIDs already present under DlcData -> base_appid in SLSsteam config.yaml."""
    base_appid_str = str(base_appid).strip()
    if not base_appid_str:
        return set()
    try:
        from utils.yaml_config_manager import get_user_config_path, get_dlc_data
        cp = config_path or get_user_config_path()
        if cp and Path(cp).exists():
            data = get_dlc_data(Path(cp), base_appid_str)
            if isinstance(data, dict):
                return {
                    str(k).strip()
                    for k in data.keys()
                    if str(k).strip().isdigit() and str(k).strip() != base_appid_str
                }
    except Exception as e:
        logger.debug(f"[DLCMode] Error reading injected DLCs for {base_appid_str}: {e}")
    return set()


def get_owned_dlcs_from_steam(base_appid: str, steam_userdata_path: Optional[str] = None) -> Set[str]:
    """
    Parses Steam's local AppOwnershipTicket binary blobs stored in localconfig.vdf
    under UserLocalConfigStore -> apptickets to discover legitimately owned DLCs offline.
    """
    base_appid_str = str(base_appid).strip()
    if not base_appid_str or not base_appid_str.isdigit():
        return set()
    base_appid_int = int(base_appid_str)
    owned_dlcs: Set[str] = set()

    steam_roots = []
    if steam_userdata_path:
        steam_roots.append(Path(steam_userdata_path))
    steam_roots.extend([
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
        Path.home() / ".steam/root",
        Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ])

    checked_dirs: Set[Path] = set()
    for root in steam_roots:
        userdata = root / "userdata" if (root / "userdata").is_dir() else (root if root.name == "userdata" else None)
        if not userdata or not userdata.is_dir() or userdata in checked_dirs:
            continue
        checked_dirs.add(userdata)

        for user_dir in userdata.iterdir():
            if not user_dir.is_dir() or not user_dir.name.isdigit():
                continue
            cfg_file = user_dir / "config" / "localconfig.vdf"
            if not cfg_file.is_file():
                continue
            try:
                content = cfg_file.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                logger.debug("[DLC] Could not read %s: %s", cfg_file, exc)
                continue

            match = re.search(rf"\"{base_appid_str}\"\s+\"([0-9a-fA-F]+)\"", content)
            if not match:
                continue
            hex_str = match.group(1)
            try:
                raw = bytes.fromhex(hex_str)
                if len(raw) < 48:
                    continue
                ticket_appid = struct.unpack_from("<I", raw, 16)[0]
                if ticket_appid != base_appid_int:
                    continue
                dlc_count = struct.unpack_from("<H", raw, 46)[0]
                offset = 48
                for _ in range(dlc_count):
                    if offset + 6 > len(raw):
                        break
                    dlc_id, _dlc_flags = struct.unpack_from("<IH", raw, offset)
                    if dlc_id > 0:
                        owned_dlcs.add(str(dlc_id))
                    offset += 6
            except Exception as e:
                logger.debug(f"[DLCMode] Failed parsing ticket for {base_appid_str} in {cfg_file}: {e}")

    return owned_dlcs


def enrich_dlc_names_with_byparr_or_store(
    base_appid: str,
    catalog: Dict[str, str],
) -> Dict[str, str]:
    """
    Enriches missing DLC names in catalog using Byparr (SteamDB /app/<appid>/dlc/)
    or Steam Store API fallbacks.
    """
    base_appid_str = str(base_appid).strip()
    if not base_appid_str or not catalog:
        return catalog

    enriched = dict(catalog)
    needs_lookup = [
        dlc_id
        for dlc_id, name in enriched.items()
        if not name or name.strip().lower() in (f"dlc {dlc_id}", f"app {dlc_id}", "")
    ]
    # Every name is known: do not open a browser or a socket for nothing.
    if not needs_lookup:
        return enriched

    # 1. Try Byparr SteamDB Scraper if Byparr is available
    try:
        from core.steamdb_scraper import ByparrManager, SteamDBScraper
        if ByparrManager.find_byparr_dir():
            scraper = SteamDBScraper()
            html = scraper.fetch_rendered_html(f"https://steamdb.info/app/{base_appid_str}/dlc/")
            if html:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")
                for tr in soup.find_all("tr"):
                    tds = tr.find_all(["td", "th"])
                    if len(tds) >= 2:
                        d_id = tds[0].get_text(strip=True)
                        d_name = tds[1].get_text(strip=True)
                        if d_id.isdigit() and d_id != base_appid_str and d_name:
                            # Name the DLCs the caller already knows about; never
                            # add an id. The catalogue is what discovery found, so
                            # an id that appears here alone would be listed as a
                            # depotless DLC the user never asked for.
                            if d_id in enriched and (
                                not enriched[d_id]
                                or enriched[d_id].strip().lower()
                                in (f"dlc {d_id}", f"app {d_id}", "")
                            ):
                                enriched[d_id] = d_name
                            if d_id in needs_lookup:
                                needs_lookup.remove(d_id)
    except Exception as e:
        logger.debug(f"[DLCMode] Byparr DLC name lookup failed for {base_appid_str}: {e}")

    # 2. Local database app_info lookup
    if needs_lookup:
        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            for dlc_id in list(needs_lookup):
                app_info = db.get_app_info(str(dlc_id))
                if app_info and app_info.get("name"):
                    clean_n = _clean_dlc_name(app_info["name"])
                    enriched[dlc_id] = clean_n or app_info["name"]
                    needs_lookup.remove(dlc_id)
        except Exception as e_db:
            logger.debug(f"[DLCMode] DB app_info lookup failed: {e_db}")

    # 3. Store API batch fallback for any remaining needs_lookup
    if needs_lookup:
        import requests
        chunk_size = 25
        for i in range(0, min(len(needs_lookup), 50), chunk_size):
            chunk = needs_lookup[i:i + chunk_size]
            try:
                ids_str = ",".join(chunk)
                r = requests.get(
                    f"https://store.steampowered.com/api/appdetails?appids={ids_str}",
                    timeout=4,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                )
                if r.status_code == 200:
                    d_data = r.json()
                    for dlc_id in chunk:
                        item_data = d_data.get(str(dlc_id), {})
                        if item_data.get("success"):
                            name = item_data.get("data", {}).get("name")
                            if name:
                                clean_n = _clean_dlc_name(name)
                                enriched[dlc_id] = clean_n or name
            except Exception as exc:
                logger.debug(f"[DLC] Store batch lookup failed for {chunk}: {exc}")

    return enriched
