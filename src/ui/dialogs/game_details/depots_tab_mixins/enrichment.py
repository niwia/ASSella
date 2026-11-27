"""Background Hubcap quota checks, release verification, SteamDB enrichment, and refetch workers."""

import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Dict, List, Optional
import zipfile

from PyQt6.QtCore import QObject, pyqtSignal

from ui.dialogs.themed_message_box import ThemedMessageBox
from utils.helpers import get_base_path
from utils.lua_parsing import iter_live_matches
from utils.plugin_games import (
    SHARED_REDISTS,
    build_depot_comment,
    build_key_comment,
    load_plugin_library,
    save_plugin_library,
)
from utils.sls_bridge import SLSBridge
from utils.yaml_config_manager import batch_config_edit, get_user_config_path

logger = logging.getLogger(__name__)


class _WorkerBridge(QObject):
    finished = pyqtSignal(bool, str)


class _EnrichmentBridge(QObject):
    enriched = pyqtSignal()


class _QuotaBridge(QObject):
    quota_exhausted = pyqtSignal()


class DepotsEnrichmentMixin:
    """Manages background worker threads and Qt signal bridges for quota, metadata, and refetching."""

    def _emit_bridge_signal(
        self, bridge: Optional[QObject], signal_name: str, *args
    ) -> None:
        """Safely emit a signal on a child QObject bridge from a worker thread,
        shielding against TOCTOU widget deletion races."""
        if bridge is None or self._is_destroyed():
            return
        try:
            sig = getattr(bridge, signal_name, None)
            if sig is not None:
                sig.emit(*args)
        except RuntimeError as e:
            logger.debug(
                f"[DepotsTab] Bridge {signal_name} emission skipped after widget deletion: {e}"
            )

    def _init_quota_checks(self) -> None:
        """Non-blocking check of Hubcap token quota (instant cached read + async background refresh)."""
        try:
            from utils.settings import get_settings

            cached_stats = get_settings().value(
                "last_cached_user_stats", None
            )
            if isinstance(cached_stats, dict) and cached_stats:
                init_rem = max(
                    0,
                    (cached_stats.get("daily_limit") or 135)
                    - cached_stats.get("daily_usage", 0),
                )
                if init_rem <= 0 or not cached_stats.get(
                    "can_make_requests", True
                ):
                    self.refetch_btn.setEnabled(False)
                    self.refetch_btn.setToolTip(
                        "Daily Hubcap API token limit reached."
                    )
        except Exception as e:
            logger.debug(f"[DepotsTab] Cached Hubcap quota read failed: {e}")

        threading.Thread(
            target=self._check_hubcap_quota_async,
            daemon=True,
            name=f"DepotsQuotaCheck-{self.appid_str}",
        ).start()

    def _check_hubcap_quota_async(self) -> None:
        try:
            from core import morrenus_api

            st = morrenus_api.get_user_stats()
            if isinstance(st, dict) and st:
                rem = max(
                    0,
                    (st.get("daily_limit") or 135)
                    - st.get("daily_usage", 0),
                )
                if rem <= 0 or not st.get("can_make_requests", True):
                    self._emit_bridge_signal(
                        getattr(self, "_quota_bridge", None), "quota_exhausted"
                    )
        except Exception as e:
            logger.debug(f"[DepotsTab] Async Hubcap quota check failed: {e}")

    def _disable_refetch_due_to_quota(self) -> None:
        if self._is_destroyed():
            return
        self.refetch_btn.setEnabled(False)
        self.refetch_btn.setToolTip("Daily Hubcap API token limit reached.")

    def _schedule_background_tasks(self) -> None:
        """Schedule background Steam release state verification and depot enrichment."""
        try:
            from managers.db_manager import DatabaseManager

            _enr = (
                DatabaseManager().get_depot_enrichments(self.appid_str) or {}
            )
            _state0 = self.current_state or self._collect_state()
            _dlc_names = {
                a["id"]: a["desc"]
                for a in _state0["apps"]
                if not a.get("is_base")
            }
            _unverified = [
                d
                for d in _dlc_names
                if not (_enr.get(d) or {}).get("release_src")
            ]
            if _unverified:
                if self.is_dlc_setup:
                    self.status_lbl.setText(
                        f"Checking Steam release state for {len(_unverified)} DLC(s)..."
                    )
                threading.Thread(
                    target=self._verify_release_states,
                    args=(_unverified, _dlc_names),
                    daemon=True,
                    name=f"DepotsReleaseVerify-{self.appid_str}",
                ).start()
        except Exception as e:
            logger.debug(
                f"[DepotsTab] Could not schedule release state verification: {e}"
            )

        initial_state = self.current_state or self._collect_state()
        needs_enrichment = any(
            bool(
                re.match(
                    r"^(?:\[.*?\]\s*)?Depot\s+\d+$",
                    item.get("desc", ""),
                    re.IGNORECASE,
                )
            )
            for item in initial_state.get("depots", [])
        )
        if needs_enrichment:
            threading.Thread(
                target=self._run_background_enrichment,
                daemon=True,
                name=f"DepotsEnrich-{self.appid_str}",
            ).start()

    def _on_enrichment_ready(self) -> None:
        if self._is_destroyed():
            return
        self._refresh_ui()
        if self.is_dlc_setup:
            self.status_lbl.setText(
                "Pick the DLCs to add. Steam-owned items require confirmation; unreleased items are greyed out and left untouched."
            )

    def _verify_release_states(
        self, dlc_ids: List[str], names: Dict[str, str]
    ) -> None:
        try:
            from core.steam_api import resolve_dlc_release_states
            from managers.db_manager import DatabaseManager

            states = resolve_dlc_release_states(dlc_ids)
            if states:
                DatabaseManager().update_release_states(
                    self.appid_str, states, names
                )
                self._emit_bridge_signal(
                    getattr(self, "_enrichment_bridge", None), "enriched"
                )
        except Exception as e:
            logger.debug(
                f"[DepotsTab] Release state verification failed: {e}"
            )

    def _run_background_enrichment(self) -> None:
        try:
            from core.steam_api import get_depot_info_from_api
            from core.steamdb_scraper import SteamDBScraper
            from managers.db_manager import DatabaseManager

            db = DatabaseManager()
            new_enrichments: Dict[str, dict] = {}

            # 1. Query SteamCMD / Steam PICS
            api_info = get_depot_info_from_api(
                int(self.appid_str)
                if self.appid_str.isdigit()
                else self.appid_str
            )
            if (
                api_info
                and isinstance(api_info, dict)
                and api_info.get("depots")
            ):
                for did, ddata in api_info["depots"].items():
                    if isinstance(ddata, dict):
                        new_enrichments[str(did)] = {
                            "depot_id": str(did),
                            "name": ddata.get("name") or "",
                            "oslist": ddata.get("oslist") or "",
                            "language": ddata.get("language") or "",
                            "is_dlc": bool(ddata.get("is_dlc")),
                            "dlcappid": ddata.get("dlcappid") or "",
                            "size_str": "",
                            "size_bytes": ddata.get("size") or 0,
                            "dl_str": "",
                        }

            # 2. Query SteamDB (via Fast-Path race or Byparr)
            try:
                scraper = SteamDBScraper()
                sdb_depots = scraper.get_app_depots(self.appid_str)
                if sdb_depots:
                    for did, sdata in sdb_depots.items():
                        if did in new_enrichments:
                            if sdata.get("name"):
                                new_enrichments[did]["name"] = sdata["name"]
                            if sdata.get("oslist"):
                                new_enrichments[did]["oslist"] = sdata[
                                    "oslist"
                                ]
                            if sdata.get("size_str"):
                                new_enrichments[did]["size_str"] = sdata[
                                    "size_str"
                                ]
                        else:
                            new_enrichments[did] = sdata
            except Exception as e:
                logger.debug(f"[DepotsTab] SteamDB enrichment failed: {e}")

            if new_enrichments:
                db.save_depot_enrichments(self.appid_str, new_enrichments)
                self._emit_bridge_signal(
                    getattr(self, "_enrichment_bridge", None), "enriched"
                )
        except Exception as e:
            logger.debug(
                f"[DepotsTab] Background depot enrichment error: {e}"
            )

    def _on_refetch(self) -> None:
        """Refetch fresh Lua metadata and keys from Hubcap API."""
        from core import morrenus_api

        stats = morrenus_api.get_user_stats()
        daily_limit = (
            stats.get("daily_limit") or stats.get("role_daily_limit") or 135
        )
        daily_usage = stats.get("daily_usage", 0)
        can_make_requests = stats.get("can_make_requests", True)
        remaining = max(0, daily_limit - daily_usage)

        if not can_make_requests or remaining <= 0:
            ThemedMessageBox.warning(
                self.dialog,
                "API Token Limit Reached",
                f"Daily Hubcap API token limit reached (0/{daily_limit} tokens remaining).\n\n"
                "Please wait for your daily quota to reset, or change your ISP bypass settings in Settings.",
            )
            self.refetch_btn.setEnabled(False)
            self.status_lbl.setText(
                f"API token limit reached (0/{daily_limit}). Please wait for daily reset."
            )
            return

        reply = ThemedMessageBox.question(
            self.dialog,
            "Refetch Keys",
            f"This action will consume your daily Hubcap API tokens.\n\n"
            f"You have {remaining}/{daily_limit} tokens remaining.\n\n"
            "Do you want to continue?",
            ThemedMessageBox.StandardButton.Yes
            | ThemedMessageBox.StandardButton.Cancel,
            ThemedMessageBox.StandardButton.Cancel,
        )
        if reply != ThemedMessageBox.StandardButton.Yes:
            return

        self.refetch_btn.setEnabled(False)
        self.mode_seg.setEnabled(False)
        self.sync_btn.setEnabled(False)
        self.status_lbl.setText("Refetching metadata from Hubcap...")

        self._worker_bridge = _WorkerBridge(self)
        self._worker_bridge.finished.connect(self._on_refetch_done)
        threading.Thread(
            target=self._refetch_worker,
            args=(self._worker_bridge,),
            daemon=True,
            name=f"DepotsRefetch-{self.appid_str}",
        ).start()

    def _refetch_worker(self, bridge: _WorkerBridge) -> None:
        from core import morrenus_api

        try:
            zip_path, err = morrenus_api.download_manifest(
                self.appid_str, force_update=True
            )
            if zip_path and os.path.exists(zip_path):
                with zipfile.ZipFile(zip_path, "r") as zf:
                    all_discovered_depots = set()
                    for fname in zf.namelist():
                        if fname.endswith(".manifest"):
                            bname = os.path.basename(fname)
                            parts = bname.split("_")
                            if parts and parts[0].isdigit():
                                all_discovered_depots.add(parts[0])

                    lua_files = [
                        f for f in zf.namelist() if f.endswith(".lua")
                    ]
                    if lua_files:
                        dest_lua = (
                            Path(get_base_path())
                            / "cached_luas"
                            / f"{self.appid_str}.lua"
                        )
                        dest_lua.parent.mkdir(parents=True, exist_ok=True)
                        lua_data = zf.read(lua_files[0]).decode(
                            "utf-8", errors="ignore"
                        )
                        dest_lua.write_text(lua_data, encoding="utf-8")

                        for m in iter_live_matches(
                            lua_data, r"addappid\((\d+)"
                        ):
                            all_discovered_depots.add(m.group(1))

                        fresh_keys = {}
                        for m in iter_live_matches(
                            lua_data,
                            r'addappid\((\d+),\s*\d+,\s*["\']([a-fA-F0-9]{64})["\']\)',
                        ):
                            fresh_keys[m.group(1)] = m.group(2).lower()
                        if fresh_keys:
                            from managers.depot_key_manager import (
                                DepotKeyManager,
                            )

                            DepotKeyManager.get_instance().save_depot_keys(
                                self.appid_str, fresh_keys
                            )

                        try:
                            cfg_path = get_user_config_path()
                            lib = load_plugin_library()
                            rec = lib.get(self.appid_str)
                            is_dlc_only = (
                                rec.get("dlc_only", False) if rec else False
                            )
                            gd = self._get_game_data()
                            game_name = (
                                gd.get("game_name")
                                or f"App {self.appid_str}"
                            )
                            registered_depots = {
                                str(d)
                                for d in (rec.get("depots", []) if rec else [])
                            }
                            registered_dlc_apps = {
                                str(d)
                                for d in (
                                    rec.get("dlc_appids", []) if rec else []
                                )
                            }
                            discovered_dlc_apps = {
                                str(d)
                                for d in (gd.get("dlcs") or {})
                            } | registered_dlc_apps

                            keyed_depots = {
                                d
                                for d in all_discovered_depots
                                if d != self.appid_str
                                and d not in discovered_dlc_apps
                                and d in fresh_keys
                                and d not in SHARED_REDISTS
                                and (
                                    not is_dlc_only or d in registered_depots
                                )
                            }
                            allowed_key_ids = (
                                registered_depots
                                | registered_dlc_apps
                                | {self.appid_str}
                                | SHARED_REDISTS
                                if is_dlc_only
                                else keyed_depots
                                | {self.appid_str}
                                | SHARED_REDISTS
                            )
                            publish_keys = {
                                did: key
                                for did, key in fresh_keys.items()
                                if did in allowed_key_ids
                            }

                            if cfg_path and cfg_path.exists():
                                with batch_config_edit(cfg_path) as editor:
                                    for did, k in publish_keys.items():
                                        editor.add_key(
                                            did,
                                            k,
                                            comment=build_key_comment(
                                                game_name,
                                                did,
                                                "",
                                                self.appid_str,
                                            ),
                                        )
                                    for did in keyed_depots:
                                        if not is_dlc_only:
                                            editor.add_depot(
                                                did,
                                                comment=build_depot_comment(
                                                    game_name,
                                                    did,
                                                    "",
                                                    self.appid_str,
                                                ),
                                                app_id=self.appid_str,
                                            )
                                if editor.has_changes:
                                    SLSBridge.notify_reload()

                            if rec:
                                rec.setdefault("keys", {}).update(publish_keys)
                                if not is_dlc_only:
                                    existing_deps = set(
                                        str(d) for d in rec.get("depots", [])
                                    )
                                    existing_deps.update(keyed_depots)
                                    rec["depots"] = sorted(
                                        list(existing_deps),
                                        key=lambda x: int(x)
                                        if x.isdigit()
                                        else 0,
                                    )
                                rec["updated_at"] = int(time.time())
                                save_plugin_library(lib)

                            dlg = self.dialog
                            target_gd = (
                                dlg.game_data
                                if dlg is not None
                                and isinstance(
                                    getattr(dlg, "game_data", None), dict
                                )
                                else self._fallback_game_data
                            )
                            if isinstance(target_gd, dict):
                                g_deps = set(
                                    str(d)
                                    for d in target_gd.get("depots", [])
                                )
                                g_deps.update(keyed_depots)
                                target_gd["depots"] = sorted(
                                    list(g_deps),
                                    key=lambda x: int(x) if x.isdigit() else 0,
                                )
                                target_gd.setdefault("keys", {}).update(
                                    publish_keys
                                )
                        except Exception as sync_err:
                            logger.debug(
                                f"[DepotsTab] Error syncing refetched keys to config: {sync_err}"
                            )

                try:
                    from managers.db_manager import DatabaseManager

                    DatabaseManager().save_at0m_update_status(
                        appid=self.appid_str,
                        status="up_to_date",
                        new_depots=[],
                    )
                except Exception as e:
                    logger.debug(
                        f"[DepotsTab] Could not reset AT0-M update status after refetch: {e}"
                    )

                self._emit_bridge_signal(
                    bridge,
                    "finished",
                    True,
                    "Refetch successful. Local cache and configuration updated.",
                )
            else:
                err_str = str(err or "")
                if "404" in err_str or "not found" in err_str.lower():
                    self._emit_bridge_signal(
                        bridge,
                        "finished",
                        False,
                        "Hubcap returned 404 (Manifest not found on server).",
                    )
                elif "429" in err_str or "limit" in err_str.lower():
                    self._emit_bridge_signal(
                        bridge,
                        "finished",
                        False,
                        f"Daily API token limit exceeded ({err}).",
                    )
                else:
                    self._emit_bridge_signal(
                        bridge,
                        "finished",
                        False,
                        f"Refetch failed: {err_str or 'Unknown error'}",
                    )
        except Exception as e:
            err_str = str(e)
            if "404" in err_str or "not found" in err_str.lower():
                self._emit_bridge_signal(
                    bridge,
                    "finished",
                    False,
                    "Hubcap returned 404 (Manifest not found).",
                )
            else:
                self._emit_bridge_signal(
                    bridge, "finished", False, f"Refetch error: {e}"
                )

    def _on_refetch_done(self, success: bool, msg: str) -> None:
        if self._is_destroyed():
            return
        from core import morrenus_api

        self.refetch_btn.setEnabled(True)
        self.mode_seg.setEnabled(True)
        self.status_lbl.setText(msg)
        try:
            st = morrenus_api.get_user_stats(force=True)
            rem = max(
                0, (st.get("daily_limit") or 135) - st.get("daily_usage", 0)
            )
            if rem <= 0 or not st.get("can_make_requests", True):
                self.refetch_btn.setEnabled(False)
                self.refetch_btn.setToolTip("API token limit reached.")
        except Exception as e:
            logger.debug(f"[DepotsTab] Post-refetch quota refresh failed: {e}")
        self._refresh_ui()