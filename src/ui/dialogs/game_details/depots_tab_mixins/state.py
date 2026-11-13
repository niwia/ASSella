"""State collection and depot/app resolution mixin for DepotsTabController."""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from PyQt6.QtGui import QColor

from ui.assets import DEPOT_BLACKLIST
from ui.dialogs.game_details.depots_tab_mixins.widgets import (
    _format_enriched_depot_desc,
)
from utils.helpers import get_base_path
from utils.lua_parsing import iter_live_matches
from utils.plugin_games import (
    SHARED_REDISTS,
    comment_has_owner_tag,
    load_plugin_library,
)
from utils.release_gate import (
    STATUS_UNRELEASED,
    UNRELEASED_TAG,
    ReleaseGate,
    unreleased_tooltip,
)
from utils.yaml_config_manager import (
    _get_section_bounds,
    get_additional_apps,
    get_additional_depots,
    get_decryption_keys,
    get_user_config_path,
)

logger = logging.getLogger(__name__)

# Unified set of shared redistributable depots across Steam
ALL_SHARED_REDISTS = SHARED_REDISTS | {str(d) for d in DEPOT_BLACKLIST}


class DepotsStateMixin:
    """Resolves game apps, depots, keys, ownership, and release states from local and live sources."""

    def _get_game_data(self) -> Dict[str, Any]:
        """Safely retrieve game_data from the weakly referenced dialog or startup snapshot."""
        dlg = self.dialog
        if dlg is not None and getattr(dlg, "game_data", None) is not None:
            return dlg.game_data or {}
        return getattr(self, "_fallback_game_data", None) or {}

    def _build_app_to_depots_map(
        self,
        state_apps: List[Dict[str, Any]],
        state_depots: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Set[str]]:
        """
        Dynamically map each AppID (base game and DLCs) to its associated depot IDs.
        Used for one-way dynamic cascade toggling across AdditionalApps,
        AdditionalDepots, and DecryptionKeys.
        """
        from utils.dlc_helpers import build_app_to_depots_map

        return build_app_to_depots_map(
            self.appid_str,
            game_data=self._get_game_data(),
            extra_appids=[item["id"] for item in state_apps],
            candidate_depots=[d["id"] for d in state_depots]
            if state_depots
            else None,
        )

    def _collect_state(self) -> Dict[str, Any]:
        """Collect and cross-reference data strictly for THIS game."""
        cfg_path = get_user_config_path()
        live_apps = (
            set(get_additional_apps(cfg_path)) if cfg_path.exists() else set()
        )
        live_depots = (
            set(get_additional_depots(cfg_path)) if cfg_path.exists() else set()
        )
        live_keys = get_decryption_keys(cfg_path) if cfg_path.exists() else {}

        gd = self._get_game_data()

        # 1. Plugin Library record for THIS game
        plugin_lib = load_plugin_library()
        game_record = plugin_lib.get(self.appid_str, {})
        game_name = (
            gd.get("game_name")
            or game_record.get("name")
            or f"App {self.appid_str}"
        )

        plugin_depots = {str(d) for d in game_record.get("depots", [])}
        plugin_keys = {
            str(d): str(k).lower() for d, k in game_record.get("keys", {}).items()
        }
        plugin_depot_names = {
            str(d): str(n) for d, n in game_record.get("depot_names", {}).items()
        }
        plugin_dlcs = {str(d) for d in game_record.get("dlc_appids", [])}

        # 2. Local SQLite depot keys for THIS appid
        from managers.depot_key_manager import DepotKeyManager

        dkm = DepotKeyManager.get_instance()
        local_db_keys = {
            str(d): str(k).lower()
            for d, k in (dkm.get_keys_for_app(self.appid_str) or {}).items()
        }

        # 3. Cached Lua parsing for THIS game
        cached_lua_path = (
            Path(get_base_path()) / "cached_luas" / f"{self.appid_str}.lua"
        )
        lua_keys: Dict[str, str] = {}
        lua_dlcs: Dict[str, str] = {}
        lua_depots: Dict[str, str] = {}
        blacklisted_depots: Set[str] = set()

        if cached_lua_path.exists():
            try:
                txt = cached_lua_path.read_text(encoding="utf-8", errors="ignore")
                for line in txt.splitlines():
                    if "blacklisted" in line.lower() and "depot" in line.lower():
                        for num in re.findall(r"\b(\d{4,9})\b", line):
                            blacklisted_depots.add(num)

                for m in iter_live_matches(
                    txt, r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)'
                ):
                    did, key = m.group(1), m.group(2)
                    lua_keys[str(did)] = key.lower()

                from core.tasks.process_zip_task import ProcessZipTask

                parsed: Dict[str, Any] = {}
                ProcessZipTask._parse_lua(txt, parsed)
                if parsed.get("app_key"):
                    lua_keys[self.appid_str] = parsed["app_key"].lower()
                if parsed.get("dlcs"):
                    for dlc_id, dlc_name in parsed["dlcs"].items():
                        lua_dlcs[str(dlc_id)] = str(dlc_name)
                if parsed.get("depots"):
                    for d, info in parsed["depots"].items():
                        d_str = str(d)
                        if isinstance(info, dict):
                            desc = info.get("desc") or info.get("name") or ""
                            lua_depots[d_str] = desc
                            if info.get("key"):
                                lua_keys[d_str] = str(info["key"]).lower()
            except Exception as e:
                logger.debug(f"[DepotsTab] Error parsing cached Lua: {e}")

        # 4. Merge game_data metadata (supports both dict and list/set formats)
        gd_dlcs_raw = gd.get("dlcs") or {}
        if isinstance(gd_dlcs_raw, dict):
            gd_dlcs = {str(k): str(v) for k, v in gd_dlcs_raw.items()}
        elif isinstance(gd_dlcs_raw, (list, set)):
            gd_dlcs = {str(d): f"DLC {d}" for d in gd_dlcs_raw}
        else:
            gd_dlcs = {}
        for dlc_id, dlc_desc in gd_dlcs.items():
            lua_dlcs.setdefault(dlc_id, dlc_desc)

        gd_depots_raw = gd.get("depots") or {}
        if isinstance(gd_depots_raw, dict):
            gd_depots = {
                str(k): (v if isinstance(v, dict) else {"desc": str(v)})
                for k, v in gd_depots_raw.items()
            }
        elif isinstance(gd_depots_raw, (list, set)):
            gd_depots = {str(d): {"desc": f"Depot {d}"} for d in gd_depots_raw}
        else:
            gd_depots = {}

        for d_str, dinfo in gd_depots.items():
            desc = dinfo.get("desc") or dinfo.get("name") or ""
            lua_depots.setdefault(d_str, desc)
            k = dinfo.get("key") or dinfo.get("decryption_key")
            if k and len(str(k)) == 64:
                lua_keys.setdefault(d_str, str(k).lower())

        gd_keys = gd.get("keys") or {}
        if isinstance(gd_keys, dict):
            for did, k in gd_keys.items():
                if k and len(str(k)) == 64:
                    lua_keys.setdefault(str(did), str(k).lower())

        # 5. Check config.yaml entries specifically tagged with this game's name or AppID
        tagged_apps_from_config: Dict[str, str] = {}
        tagged_depots_from_config: Set[str] = set()
        tagged_keys_from_config: Dict[str, str] = {}
        if cfg_path.exists():
            try:
                cfg_text = cfg_path.read_text(encoding="utf-8", errors="ignore")
                app_bounds = _get_section_bounds(cfg_text, "AdditionalApps")
                if app_bounds:
                    sec_app = cfg_text[app_bounds[1] : app_bounds[2]]
                    for line in sec_app.splitlines():
                        m = re.match(
                            r"^[ \t]*-[ \t]*(\d+)[ \t]*(?:#[ \t]*(.*))?$", line
                        )
                        if m:
                            aid, comment = m.group(1), (m.group(2) or "")
                            if (
                                game_name and game_name.lower() in comment.lower()
                            ) or comment_has_owner_tag(comment, self.appid_str):
                                if aid != self.appid_str:
                                    clean_c = re.sub(
                                        r"^\[DLC\]\s*",
                                        "",
                                        comment.strip(),
                                        flags=re.IGNORECASE,
                                    )
                                    tagged_apps_from_config[aid] = (
                                        clean_c or f"DLC {aid}"
                                    )

                dep_bounds = _get_section_bounds(cfg_text, "AdditionalDepots")
                if dep_bounds:
                    sec_dep = cfg_text[dep_bounds[1] : dep_bounds[2]]
                    for line in sec_dep.splitlines():
                        m = re.match(
                            r"^[ \t]*-[ \t]*(\d+)[ \t]*(?:#[ \t]*(.*))?$", line
                        )
                        if m:
                            did, comment = m.group(1), (m.group(2) or "")
                            if (
                                game_name and game_name.lower() in comment.lower()
                            ) or comment_has_owner_tag(comment, self.appid_str):
                                tagged_depots_from_config.add(did)

                key_bounds = _get_section_bounds(cfg_text, "DecryptionKeys")
                if key_bounds:
                    sec_key = cfg_text[key_bounds[1] : key_bounds[2]]
                    for line in sec_key.splitlines():
                        m = re.match(
                            r"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*['\"]?([a-fA-F0-9]{64})['\"]?[ \t]*(?:#[ \t]*(.*))?$",
                            line,
                        )
                        if m:
                            did, kval, comment = (
                                m.group(1),
                                m.group(2),
                                (m.group(3) or ""),
                            )
                            if (
                                game_name and game_name.lower() in comment.lower()
                            ) or comment_has_owner_tag(comment, self.appid_str):
                                tagged_keys_from_config[did] = kval.lower()
            except Exception as e:
                logger.debug(f"[DepotsTab] Error reading tagged config comments: {e}")

        # Compile game-specific keys map (strictly excluding common shared redists)
        game_keys: Dict[str, str] = {}
        game_keys.update(local_db_keys)
        game_keys.update(lua_keys)
        game_keys.update(plugin_keys)
        game_keys.update(tagged_keys_from_config)

        # Correlate live_keys from config that match this game's depots or AppID
        for d in set(gd_depots.keys()) | set(lua_depots.keys()) | {self.appid_str}:
            if d in live_keys and d not in ALL_SHARED_REDISTS:
                k = live_keys[d]
                if k and len(k) == 64:
                    game_keys.setdefault(d, k.lower())
                    if d not in local_db_keys:
                        try:
                            dkm.save_depot_keys(self.appid_str, {d: k.lower()})
                            local_db_keys[d] = k.lower()
                        except Exception as e:
                            logger.debug(f"[DepotsTab] Auto-seed local key failed: {e}")

        game_keys = {
            d: k for d, k in game_keys.items() if d not in ALL_SHARED_REDISTS
        }

        from core.steam_helpers import get_owned_steam_appids
        from managers.db_manager import DatabaseManager
        from utils.dlc_helpers import is_dlc_only_mode
        from utils.yaml_config_manager import is_depot_shared_with_other_games

        db_mgr = DatabaseManager()
        cached_enrichments = db_mgr.get_depot_enrichments(self.appid_str) or {}
        cached_app_info = db_mgr.get_app_info(self.appid_str) or {}
        cached_app_depots = (
            (cached_app_info.get("depots") or {})
            if isinstance(cached_app_info, dict)
            else {}
        )

        gate = ReleaseGate(
            self.appid_str,
            enrichments=cached_enrichments,
            app_depots=cached_app_depots,
            db=db_mgr,
        )

        is_steam_owned_base = self.appid_str in get_owned_steam_appids()
        try:
            from utils.dlc_helpers import get_owned_dlcs_from_steam

            steam_owned_dlcs = {
                str(x)
                for x in (get_owned_dlcs_from_steam(self.appid_str) or set())
            }
        except Exception as e:
            logger.debug(f"[DepotsTab] Could not read Steam DLC ownership: {e}")
            steam_owned_dlcs = set()
        is_dlc_only = bool(
            gd.get("is_dlc_only")
            or gd.get("_dlc_only")
            or is_dlc_only_mode(self.appid_str)
        )
        is_dlc_context = bool(self.is_dlc_setup or is_dlc_only)
        is_base_owned = bool(is_steam_owned_base or is_dlc_only)

        # DLC AppIDs set
        dlc_id_set = (
            set(lua_dlcs.keys())
            | plugin_dlcs
            | set(tagged_apps_from_config.keys())
        )
        dlc_id_set.discard(self.appid_str)

        # Build AppIDs list: Base AppID + DLC AppIDs
        base_desc = (
            f"{game_name} [Steam Owned]"
            if is_base_owned
            else f"{game_name} (Base)"
        )
        apps_list: List[Dict[str, Any]] = [
            {
                "id": self.appid_str,
                "desc": base_desc,
                "is_base": True,
                "is_owned_base": is_base_owned,
                "is_owned": is_steam_owned_base,
                "is_dlc_base_locked": is_dlc_context,
                "is_unreleased": False,
            }
        ]
        for dlc_id in sorted(
            dlc_id_set, key=lambda x: int(x) if x.isdigit() else 0
        ):
            desc = (
                lua_dlcs.get(dlc_id)
                or gd_dlcs.get(dlc_id)
                or tagged_apps_from_config.get(dlc_id)
                or f"DLC {dlc_id}"
            )
            is_unrel = gate.is_unreleased(dlc_id, is_dlc_app=True)
            prefix = f"{UNRELEASED_TAG} [DLC]" if is_unrel else "[DLC]"
            is_owned_dlc = dlc_id in steam_owned_dlcs
            if is_owned_dlc:
                prefix = "[Steam Owned] [DLC]"
            apps_list.append(
                {
                    "id": dlc_id,
                    "desc": f"{prefix} {desc}",
                    "is_base": False,
                    "is_owned_base": False,
                    "is_owned": is_owned_dlc,
                    "is_unreleased": is_unrel,
                }
            )

        content_dlcs = {
            d
            for d in dlc_id_set
            if (d in game_keys or d in lua_depots or d in gd_depots)
        }
        pure_license_dlcs = dlc_id_set - content_dlcs
        dlc_content_depots = set(dlc_id_set)
        for depot_id, depot_info in gd_depots.items():
            if (
                isinstance(depot_info, dict)
                and str(depot_info.get("dlcappid") or "").strip() in dlc_id_set
            ):
                dlc_content_depots.add(depot_id)

        all_referenced_depots = (
            set(lua_depots.keys())
            | plugin_depots
            | set(game_keys.keys())
            | tagged_depots_from_config
            | set(gd_depots.keys())
        )
        game_shared_depots = (
            all_referenced_depots & ALL_SHARED_REDISTS
        ) - {self.appid_str}

        game_depots_set = (
            all_referenced_depots
            - ALL_SHARED_REDISTS
            - {self.appid_str}
            - pure_license_dlcs
        )
        reported_missing = {
            str(x) for x in (gd.get("missing_depots_from_hubcap") or [])
        } - set(game_keys.keys())
        game_depots_set -= reported_missing

        # Gate 1 cascade: unreleased DLCs take mapped depots down with them
        unreleased_dlc_ids = {
            a["id"] for a in apps_list if a.get("is_unreleased")
        }
        unreleased_ids: Set[str] = set(unreleased_dlc_ids)
        if unreleased_dlc_ids:
            candidate_ids = sorted(
                game_depots_set | set(cached_app_depots.keys())
            )
            mapped = self._build_app_to_depots_map(
                apps_list, [{"id": str(c)} for c in candidate_ids]
            )
            for dlc_id in unreleased_dlc_ids:
                unreleased_ids |= {
                    str(x) for x in mapped.get(dlc_id, set())
                }

        # Steam-owned content mapping
        owned_dlc_ids = {a["id"] for a in apps_list if a.get("is_owned")}
        owned_ids: Set[str] = set(owned_dlc_ids)
        _candidates = sorted(game_depots_set | set(cached_app_depots.keys()))
        _owner_map = self._build_app_to_depots_map(
            apps_list, [{"id": str(c)} for c in _candidates]
        )
        _depot_owner: Dict[str, str] = {}
        for _aid, _dset in _owner_map.items():
            if _aid != self.appid_str:
                for _d in _dset:
                    _depot_owner.setdefault(str(_d), _aid)
        for _d, _info in gd_depots.items():
            _o = str((_info or {}).get("dlcappid") or "").strip()
            if _o and _o != self.appid_str:
                _depot_owner[_d] = _o
        for _d in _candidates:
            _o = gate.owner_dlc(_d)
            if _o:
                _depot_owner.setdefault(str(_d), _o)
        for _d, _o in _depot_owner.items():
            if _o in owned_dlc_ids:
                owned_ids.add(_d)
        if is_base_owned:
            for _d in game_depots_set:
                if _d not in _depot_owner and _d not in dlc_content_depots:
                    owned_ids.add(_d)

        def _is_owned(did: str) -> bool:
            return did in owned_ids

        def _is_unreleased(did: str) -> bool:
            if did in unreleased_ids:
                return True
            gd_owner = str(
                (gd_depots.get(did) or {}).get("dlcappid") or ""
            ).strip()
            if gd_owner and gd_owner in unreleased_dlc_ids:
                unreleased_ids.add(did)
                return True
            if gate.is_unreleased(did):
                unreleased_ids.add(did)
                return True
            return False

        from utils.depot_tag_helpers import get_os_icon

        dlg = self.dialog
        accent_val = (getattr(dlg, "accent_color", None) if dlg is not None else None) or self.ac
        active_tint = (
            QColor(accent_val)
            if accent_val and QColor(accent_val).isValid()
            else None
        )

        valid_key_depots = set(game_keys.keys())
        depots_list: List[Dict[str, Any]] = []
        for d in sorted(
            game_depots_set, key=lambda x: int(x) if x.isdigit() else 0
        ):
            if (
                d in blacklisted_depots
                and d not in live_depots
                and d not in valid_key_depots
            ):
                continue
            desc = (
                plugin_depot_names.get(d)
                or lua_depots.get(d)
                or (
                    gd_depots.get(d, {}).get("desc")
                    if isinstance(gd_depots.get(d), dict)
                    else ""
                )
                or (f"[DLC] {lua_dlcs.get(d)}" if d in lua_dlcs else None)
                or f"Depot {d}"
            )
            if d in dlc_content_depots and not desc.upper().startswith("[DLC]"):
                desc = f"[DLC] {desc}"
            enrich_info = (
                cached_enrichments.get(d) or cached_app_depots.get(d) or {}
            )

            os_icon = (
                get_os_icon(enrich_info.get("oslist"), tint_color=active_tint)
                if enrich_info
                else None
            )
            enriched_desc = _format_enriched_depot_desc(
                depot_id=d,
                fallback_desc=desc,
                enrich_data=enrich_info,
                has_os_icon=bool(os_icon),
                base_appid=self.appid_str,
            )

            size_str = self._extract_size_str(enrich_info)
            is_shared = is_depot_shared_with_other_games(
                d, excluding_appid=self.appid_str
            )
            depots_list.append(
                {
                    "id": d,
                    "desc": enriched_desc,
                    "raw_desc": desc,
                    "enrich_info": enrich_info,
                    "os_icon": os_icon,
                    "has_key": (d in valid_key_depots),
                    "is_shared": is_shared,
                    "size_str": size_str,
                    "is_missing": False,
                    "is_unreleased": _is_unreleased(d),
                    "is_owned": _is_owned(d),
                    "dlc_owner": _depot_owner.get(d, ""),
                }
            )

        # Collect missing / pending depots
        missing_depots_map: Dict[str, Dict[str, Any]] = {}

        at0m_stat = db_mgr.get_at0m_update_status(self.appid_str)
        if at0m_stat and isinstance(at0m_stat.get("new_depots"), list):
            for nd in at0m_stat["new_depots"]:
                if isinstance(nd, dict) and nd.get("id"):
                    nid = str(nd["id"])
                    if (
                        nid not in valid_key_depots
                        and nid != self.appid_str
                        and nid not in ALL_SHARED_REDISTS
                    ):
                        has_key = bool(nd.get("has_key"))
                        if _is_unreleased(nid):
                            missing_depots_map[nid] = {
                                "status": STATUS_UNRELEASED,
                                "tag": UNRELEASED_TAG,
                                "tooltip": unreleased_tooltip(nid),
                            }
                        elif has_key:
                            missing_depots_map[nid] = {
                                "status": "ready_on_hubcap",
                                "tag": "[Key Ready]",
                                "tooltip": (
                                    f"Depot {nid} has an AES decryption key available on Hubcap.\n"
                                    "Click 'Refetch keys' to download it."
                                ),
                            }
                        else:
                            missing_depots_map[nid] = {
                                "status": "key_pending",
                                "tag": "[Awaiting Key]",
                                "tooltip": (
                                    f"Depot {nid} is registered on Steam, but its AES decryption key is pending on Hubcap (cooldown active).\n"
                                    "It cannot be downloaded or injected into SLSsteam until the key is published."
                                ),
                            }

        for mid in [
            str(x) for x in (gd.get("missing_depots_from_hubcap") or [])
        ]:
            if (
                mid
                and mid not in valid_key_depots
                and mid not in missing_depots_map
                and mid != self.appid_str
                and mid not in ALL_SHARED_REDISTS
            ):
                if _is_unreleased(mid):
                    m_stat, m_tag, m_tip = (
                        STATUS_UNRELEASED,
                        UNRELEASED_TAG,
                        unreleased_tooltip(mid),
                    )
                else:
                    m_stat, m_tag, m_tip = gate.classify_missing(
                        mid, reported_missing=True
                    )
                missing_depots_map[mid] = {
                    "status": m_stat,
                    "tag": m_tag,
                    "tooltip": m_tip,
                }

        for row in db_mgr.get_missing_hubcap_depots(self.appid_str):
            mid = str(row.get("depot_id") or "")
            if (
                mid
                and mid not in valid_key_depots
                and mid not in missing_depots_map
                and mid != self.appid_str
                and mid not in ALL_SHARED_REDISTS
            ):
                if _is_unreleased(mid):
                    m_stat, m_tag, m_tip = (
                        STATUS_UNRELEASED,
                        UNRELEASED_TAG,
                        unreleased_tooltip(mid),
                    )
                else:
                    m_stat, m_tag, m_tip = gate.classify_missing(
                        mid, reported_missing=True
                    )
                missing_depots_map[mid] = {
                    "status": m_stat,
                    "tag": m_tag,
                    "tooltip": m_tip,
                }

        try:
            from managers.at0m_update_checker import At0mUpdateChecker

            install_platform = At0mUpdateChecker()._detect_install_platform(
                self.appid_str, cached_app_depots
            )
        except Exception as e:
            logger.debug(f"[DepotsTab] Install platform detection skipped: {e}")
            install_platform = None

        from ui.dialogs.depot_selection.rules import _depot_matches_platform

        for d, ddata in cached_app_depots.items():
            did_str = str(d)
            if (
                did_str not in valid_key_depots
                and did_str not in game_depots_set
                and did_str not in missing_depots_map
                and did_str != self.appid_str
                and did_str not in ALL_SHARED_REDISTS
                and isinstance(ddata, dict)
            ):
                if install_platform and not _depot_matches_platform(
                    ddata, install_platform
                ):
                    continue
                size = ddata.get("size")
                if size is not None and str(size).strip() == "0":
                    manifests = ddata.get("manifests") or {}
                    pub = (
                        manifests.get("public")
                        if isinstance(manifests, dict)
                        else {}
                    )
                    if not pub or (
                        isinstance(pub, dict)
                        and pub.get("download") in ("0", 0, None)
                    ):
                        continue
                if (
                    _is_unreleased(did_str)
                    or ddata.get("manifest_id")
                    or ddata.get("manifests")
                    or ddata.get("size")
                    or ddata.get("is_dlc")
                ):
                    if _is_unreleased(did_str):
                        m_stat, m_tag, m_tip = (
                            STATUS_UNRELEASED,
                            UNRELEASED_TAG,
                            unreleased_tooltip(did_str),
                        )
                    else:
                        m_stat, m_tag, m_tip = gate.classify_missing(did_str)
                    missing_depots_map[did_str] = {
                        "status": m_stat,
                        "tag": m_tag,
                        "tooltip": m_tip,
                    }

        existing_depot_ids = {d["id"] for d in depots_list}
        for d in sorted(
            missing_depots_map.keys() - existing_depot_ids,
            key=lambda x: int(x) if x.isdigit() else 0,
        ):
            m_info = missing_depots_map[d]
            desc = (
                plugin_depot_names.get(d)
                or lua_depots.get(d)
                or (
                    gd_depots.get(d, {}).get("desc")
                    if isinstance(gd_depots.get(d), dict)
                    else ""
                )
                or (f"[DLC] {lua_dlcs.get(d)}" if d in lua_dlcs else None)
                or f"Depot {d}"
            )
            if d in dlc_content_depots and not desc.upper().startswith("[DLC]"):
                desc = f"[DLC] {desc}"
            enrich_info = (
                cached_enrichments.get(d) or cached_app_depots.get(d) or {}
            )

            os_icon = (
                get_os_icon(enrich_info.get("oslist"), tint_color=active_tint)
                if enrich_info
                else None
            )
            enriched_desc = _format_enriched_depot_desc(
                depot_id=d,
                fallback_desc=desc,
                enrich_data=enrich_info,
                has_os_icon=bool(os_icon),
                base_appid=self.appid_str,
            )

            size_str = self._extract_size_str(enrich_info)
            depots_list.append(
                {
                    "id": d,
                    "desc": enriched_desc,
                    "raw_desc": desc,
                    "enrich_info": enrich_info,
                    "os_icon": os_icon,
                    "has_key": False,
                    "is_shared": False,
                    "size_str": size_str,
                    "is_missing": True,
                    "is_unreleased": m_info["status"] == STATUS_UNRELEASED,
                    "is_owned": _is_owned(d),
                    "dlc_owner": _depot_owner.get(d, ""),
                    "missing_status": m_info["status"],
                    "missing_tag": m_info["tag"],
                    "missing_tooltip": m_info["tooltip"],
                }
            )

        # Build Keys list for THIS GAME ONLY
        keys_list: List[Dict[str, Any]] = []
        if (
            self.appid_str in game_keys
            or self.appid_str in live_keys
            or is_base_owned
        ):
            root_k = (
                game_keys.get(self.appid_str)
                or live_keys.get(self.appid_str)
                or ""
            )
            keys_list.append(
                {
                    "id": self.appid_str,
                    "key": root_k,
                    "is_appkey": True,
                    "in_config": (self.appid_str in live_keys),
                    "in_local": bool(
                        self.appid_str in local_db_keys
                        or self.appid_str in lua_keys
                        or self.appid_str in plugin_keys
                    ),
                    "is_missing": False,
                    "is_base_owned": is_base_owned,
                    "is_dlc_base_locked": is_dlc_context,
                    "is_owned": is_steam_owned_base,
                }
            )

        all_target_key_ids = (
            (
                set(game_depots_set)
                | set(game_keys.keys())
                | set(tagged_keys_from_config.keys())
            )
            - {self.appid_str}
            - ALL_SHARED_REDISTS
        )
        for d in sorted(
            all_target_key_ids, key=lambda x: int(x) if x.isdigit() else 0
        ):
            key_val = game_keys.get(d) or (
                live_keys.get(d) if d in tagged_keys_from_config else ""
            )
            if not key_val and d not in game_keys and d not in live_keys:
                continue
            keys_list.append(
                {
                    "id": d,
                    "key": key_val or live_keys.get(d, ""),
                    "is_appkey": False,
                    "in_config": (d in live_keys),
                    "in_local": bool(
                        d in local_db_keys or d in lua_keys or d in plugin_keys
                    ),
                    "is_missing": False,
                    "is_base_owned": False,
                    "is_pure_license": (d in pure_license_dlcs),
                    "is_unreleased": _is_unreleased(d),
                    "is_owned": _is_owned(d),
                }
            )

        existing_key_ids = {k["id"] for k in keys_list}
        for d in sorted(
            missing_depots_map.keys() - existing_key_ids,
            key=lambda x: int(x) if x.isdigit() else 0,
        ):
            m_info = missing_depots_map[d]
            keys_list.append(
                {
                    "id": d,
                    "key": "",
                    "is_appkey": False,
                    "in_config": False,
                    "in_local": False,
                    "is_missing": True,
                    "missing_status": m_info["status"],
                    "missing_tag": m_info["tag"],
                    "missing_tooltip": m_info["tooltip"],
                    "is_base_owned": False,
                    "is_pure_license": (d in pure_license_dlcs),
                    "is_unreleased": m_info["status"] == STATUS_UNRELEASED,
                }
            )

        return {
            "game_name": game_name,
            "apps": apps_list,
            "depots": depots_list,
            "keys": keys_list,
            "live_apps": live_apps,
            "live_depots": live_depots,
            "live_keys": live_keys,
            "local_db_keys": local_db_keys,
            "lua_keys": lua_keys,
            "game_keys": game_keys,
            "game_shared_depots": game_shared_depots,
            "unreleased_ids": unreleased_ids,
            "owned_ids": owned_ids,
            "locked_ids": set(unreleased_ids) | set(owned_ids),
            "dlc_content_ids": set(dlc_content_depots)
            | set(_depot_owner.keys()),
        }

    @staticmethod
    def _extract_size_str(enrich_info: Dict[str, Any]) -> str:
        if not enrich_info:
            return ""
        size_str = enrich_info.get("size_str") or ""
        if not size_str and enrich_info.get("size_bytes"):
            try:
                from utils.helpers import format_bytes

                size_str = format_bytes(enrich_info["size_bytes"])
            except Exception as e:
                logger.debug(f"[DepotsTab] format_bytes fallback for size_bytes: {e}")
                size_str = str(enrich_info["size_bytes"])
        elif (
            not size_str
            and enrich_info.get("size")
            and str(enrich_info["size"]).isdigit()
        ):
            try:
                from utils.helpers import format_bytes

                size_str = format_bytes(int(enrich_info["size"]))
            except Exception as e:
                logger.debug(f"[DepotsTab] format_bytes fallback for size: {e}")
                size_str = str(enrich_info["size"])
        return size_str

    def _seed_dlc_setup_defaults(self) -> None:
        """DLC setup: preselect released, unowned DLCs plus their depots and keys.
        Suspected Steam-owned items start off unless already live in config."""
        state = self._collect_state()
        self.current_state = state
        dlc_content = state.get("dlc_content_ids", set())
        locked = state.get("locked_ids", set())
        for a in state["apps"]:
            if a.get("is_base") or a.get("is_unreleased"):
                continue
            if a.get("is_owned"):
                self.desired_apps[a["id"]] = a["id"] in state["live_apps"]
            else:
                self.desired_apps[a["id"]] = True
        for d in state["depots"]:
            if d.get("is_missing") or d.get("is_unreleased"):
                continue
            if d["id"] in locked:
                self.desired_depots[d["id"]] = d["id"] in state["live_depots"]
                continue
            self.desired_depots[d["id"]] = (
                d["id"] in dlc_content and bool(d.get("has_key"))
            ) or d["id"] in state["live_depots"]
        for k in state["keys"]:
            if (
                k.get("is_missing")
                or not k.get("key")
                or k.get("is_dlc_base_locked")
                or k.get("is_pure_license")
                or k.get("is_unreleased")
            ):
                continue
            if k["id"] in locked:
                self.desired_keys[k["id"]] = bool(k.get("in_config"))
                continue
            self.desired_keys[k["id"]] = k["id"] in dlc_content or bool(
                k.get("in_config")
            )