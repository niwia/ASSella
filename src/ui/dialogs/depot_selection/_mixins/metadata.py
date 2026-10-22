"""Depot metadata: cached enrichment from the local database, background
SteamDB / Store enrichment, and recovery of depots missing from the local
bundle. Owns the worker threads that feed the table."""

import logging
import re

from pathlib import Path
from typing import Dict

from PyQt6.QtCore import Qt

from PyQt6.QtGui import QColor, QPalette

from ui.dialogs.depot_selection import depot_metadata
from ui.dialogs.depot_selection.rules import format_size

logger = logging.getLogger(__name__)


class DepotMetadataMixin:
    """Cached and background depot metadata, plus recovery of depots missing from the bundle."""

    def _preload_cached_enrichments(self) -> None:
        """Apply depot descriptions and sizes cached in the database.

        Doing this before the table is built means rows render with their
        enriched text on the first paint instead of popping in afterwards.
        """
        # Pre-apply any cached enrichments from database
        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            cached_enrichments = db.get_depot_enrichments(str(self.app_id))
            if cached_enrichments:
                self._apply_depot_enrichments(cached_enrichments)
        except Exception as e:
            logger.debug(f"[DepotSelection] Could not load cached enrichments: {e}")

    def _kick_off_enrichment_check(self) -> None:
        """Start background description/size enrichment for depots that need it."""
        # Check if any depot needs enrichment (generic description or missing size)
        needs_enrichment = any(
            depot_metadata.needs_size(d_data) or depot_metadata.needs_name(d_data)
            for d_data in self.depots.values()
            if isinstance(d_data, dict)
        )
        if (needs_enrichment or self._dlc_only_mode) and self.app_id and str(self.app_id) not in ("0", "N/A", "unknown"):
            self._start_enrichment_async()
            if self._dlc_only_mode and not self._has_saved_selection and not self._user_interacted:
                self._apply_dlc_auto_selection()

    def _resolve_missing_depots_info(self):
        """Resolves metadata (name, size, oslist) for missing_hubcap_depots (fast local DB first, async network)."""
        if not self.missing_hubcap_depots:
            return

        if not hasattr(self, "missing_depots_info") or self.missing_depots_info is None:
            self.missing_depots_info = {}

        db_depots = {}
        cached_enrichments = {}
        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            if self.app_id:
                app_info = db.get_app_info(str(self.app_id))
                if app_info and isinstance(app_info, dict):
                    db_depots = app_info.get("depots") or {}
                cached_enrichments = db.get_depot_enrichments(str(self.app_id)) or {}
        except Exception as e:
            logger.debug(f"[DepotSelection] Error loading DB info for missing depots: {e}")

        resolved, missing_to_fetch = depot_metadata.resolve_missing_metadata(
            self.missing_hubcap_depots,
            app_depots=db_depots,
            cached_enrichments=cached_enrichments,
            branch=self.branch,
        )
        for did_str, info in resolved.items():
            current = self.missing_depots_info.setdefault(did_str, {})
            for field, value in info.items():
                if not current.get(field) and value:
                    current[field] = value
            if not current.get("name") or not current.get("size"):
                missing_to_fetch.append(did_str)

        if missing_to_fetch:
            self._start_missing_depots_fetch_async(missing_to_fetch)

    def _start_missing_depots_fetch_async(self, dids: list):
        """Asynchronously queries steamcmd for any missing depots that lacked local metadata."""
        import threading
        def _worker():
            try:
                from core.steam_api import fetch_steamcmd_info
                updated = False
                for did_str in dids:
                    scmd = fetch_steamcmd_info(str(did_str))
                    if scmd and isinstance(scmd, dict):
                        info = self.missing_depots_info.setdefault(str(did_str), {})
                        if not info.get("name") and scmd.get("name"):
                            info["name"] = scmd["name"]
                            updated = True
                        if not info.get("size") and scmd.get("size"):
                            info["size"] = scmd["size"]
                            updated = True
                if updated:
                    self._missing_depots_updated_signal.emit()
            except Exception as e:
                logger.debug(f"[DepotSelection] Async missing depots fetch error: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_missing_depots_updated(self):
        """Called on the main thread when async steamcmd metadata resolution finishes."""
        darker_grey = QColor(135, 135, 135)
        if hasattr(self, "table_widget") and self.table_widget:
            for row in range(self.table_widget.rowCount()):
                id_item = self.table_widget.item(row, 0)
                if id_item and id_item.data(Qt.ItemDataRole.UserRole + 2) == "missing":
                    did = str(id_item.data(Qt.ItemDataRole.UserRole))
                    minfo = self.missing_depots_info.get(did, {})
                    mname = minfo.get("name")
                    mraw_size = depot_metadata.manifest_size(minfo, self.branch)
                    msize_str = format_size(mraw_size) if mraw_size > 0 else "0 B"
                    tag = "[Checking]" if getattr(self, "_is_checking_missing_contents", False) else "[Missing]"
                    base_cfg = self._build_config_text(did, minfo)
                    cfg_text = f"{tag}  {base_cfg}"
                    cfg_item = self.table_widget.item(row, 1)
                    if cfg_item:
                        cfg_item.setText(cfg_text)
                        cfg_item.setForeground(darker_grey)
                        f = cfg_item.font()
                        f.setItalic(True)
                        cfg_item.setFont(f)
                    sz_col = self.table_widget.columnCount() - 1
                    sz_item = self.table_widget.item(row, sz_col)
                    if sz_item:
                        sz_item.setText(msize_str)
                        sz_item.setForeground(darker_grey)
                        sf = sz_item.font()
                        sf.setItalic(True)
                        sz_item.setFont(sf)
                        if hasattr(sz_item, "sort_value"):
                            sz_item.sort_value = mraw_size

            if hasattr(self, "linux_button") and self.linux_button:
                has_linux = self._has_native_linux_depots()
                self.linux_button.setEnabled(has_linux)
                if has_linux:
                    self.linux_button.setToolTip("Smart select Linux installation (Native Linux if available; excludes media/32-bit)")
                else:
                    self.linux_button.setToolTip("No native Linux depots available for this game")

    def _on_spinner_tick(self):
        """Advances the circular spinner animation angle and requests a redraw."""
        self._spinner_angle = (self._spinner_angle + 6) % 360
        if hasattr(self, "table_widget") and self.table_widget:
            self.table_widget.viewport().update()

    def _check_missing_contents_async(self):
        """Asynchronously checks Hubcap /contents (0-quota) in the background to see if missing depots are now available."""
        if not self.missing_hubcap_depots:
            return

        self._is_checking_missing_contents = True
        if hasattr(self, "_spinner_timer") and not self._spinner_timer.isActive():
            self._spinner_timer.start(16)

        import threading
        target_dids = list(self.missing_hubcap_depots)
        app_id_str = str(self.app_id)
        branch_str = str(self.branch or "public")

        def _worker():
            recovered = {}
            key_states: Dict[str, str] = {}
            try:
                from core import morrenus_api
                logger.debug(f"[DepotSelection] Checking /contents for App {app_id_str} in background...")
                contents_data = morrenus_api.get_manifest_contents(app_id_str, branch=branch_str)
                if isinstance(contents_data, dict) and "error" not in contents_data:
                    hubcap_depot_ids = contents_data.get("depot_ids", set())
                    manifest_map = contents_data.get("manifest_map") or {}
                    if not manifest_map and isinstance(contents_data.get("manifests"), list):
                        manifest_map = {
                            str(m["depot_id"]): str(m.get("manifest_id", ""))
                            for m in contents_data["manifests"]
                            if isinstance(m, dict) and m.get("depot_id")
                        }

                    from managers.depot_key_manager import DepotKeyManager
                    cached_keys = DepotKeyManager().get_depot_keys(app_id_str) or {}
                    hubcap_key_index = morrenus_api.get_hubcap_depot_keys(allow_network=True)
                    existing_key_dids = hubcap_key_index.get("existing_depot_ids", set()) if isinstance(hubcap_key_index, dict) else set()
                    pending_key_dids = hubcap_key_index.get("pending_depot_ids", set()) if isinstance(hubcap_key_index, dict) else set()

                    # What does Hubcap know about each missing depot?
                    #   available - Hubcap holds a decryption key for it
                    #   pending   - Hubcap knows the depot, the key is not out yet
                    #   unknown   - Hubcap has not indexed the depot at all
                    # The three must not be collapsed: "no key yet" and
                    # "Hubcap does not know it" lead to different actions.
                    key_states: Dict[str, str] = {}
                    for did in target_dids:
                        did_str = str(did)
                        if did_str in existing_key_dids:
                            key_states[did_str] = "available"
                        elif did_str in pending_key_dids:
                            key_states[did_str] = "pending"
                        else:
                            key_states[did_str] = "unknown"

                    # 1. Check if any missing depots lacking keys now have keys in Hubcap
                    needs_bundle_refetch = False
                    for did in target_dids:
                        did_str = str(did)
                        if did_str in existing_key_dids and not cached_keys.get(did_str):
                            logger.info(f"[DepotSelection] Missing depot {did_str} now has an AES decryption key on Hubcap!")
                            needs_bundle_refetch = True
                            break

                    # If Hubcap has added keys, refetch the fresh manifest zip to extract Lua keys and manifests
                    if needs_bundle_refetch:
                        logger.info(f"[DepotSelection] Refetching fresh manifest bundle for App {app_id_str} to extract new keys...")
                        fresh_zip, _ = morrenus_api.download_manifest(app_id_str, branch=branch_str, force_update=True)
                        if fresh_zip and Path(fresh_zip).exists():
                            try:
                                from core.tasks.process_zip_task import ProcessZipTask
                                parsed_fresh = ProcessZipTask().run(str(fresh_zip))
                                if parsed_fresh and parsed_fresh.get("depots"):
                                    cached_keys = DepotKeyManager().get_depot_keys(app_id_str) or {}
                                    for f_did, f_ddata in parsed_fresh["depots"].items():
                                        f_did_str = str(f_did)
                                        if f_did_str in target_dids:
                                            self.depots[f_did_str] = f_ddata
                                            f_mid = f_ddata.get("manifest_id")
                                            if not f_mid and isinstance(f_ddata.get("manifests"), dict):
                                                f_mid = (f_ddata["manifests"].get(branch_str) or f_ddata["manifests"].get("public") or {}).get("gid")
                                            if f_mid:
                                                recovered[f_did_str] = str(f_mid)
                            except Exception as _pe:
                                logger.warning(f"[DepotSelection] Error parsing freshly fetched zip: {_pe}")

                    # 2. Check remaining missing depots
                    for did in target_dids:
                        did_str = str(did)
                        if did_str in recovered:
                            continue
                        info = self.missing_depots_info.get(did_str, {})
                        # A depot cannot be recovered or downloaded if it lacks an AES decryption key
                        has_key = bool(
                            cached_keys.get(did_str)
                            or info.get("key")
                            or (self.depots.get(did_str) or {}).get("key")
                        )
                        if info.get("hubcap_status") == "missing_key" and not has_key:
                            continue

                        if did_str in hubcap_depot_ids:
                            mid = manifest_map.get(did_str)
                            if not mid:
                                mid = info.get("manifest_id") or info.get("gid")
                            if mid:
                                recovered[did_str] = str(mid)
            except Exception as e:
                logger.debug(f"[DepotSelection] Background /contents check error: {e}")
            finally:
                self._missing_contents_checked_signal.emit(
                    {"recovered": recovered, "key_states": key_states}
                )

        threading.Thread(target=_worker, daemon=True).start()

    def _on_missing_contents_check_finished(self, payload: Dict):
        """Called when background /contents check finishes. Settles spinners into final disabled state."""
        self._is_checking_missing_contents = False
        if hasattr(self, "_spinner_timer") and self._spinner_timer.isActive():
            self._spinner_timer.stop()

        # The payload carries the depots that came back and what Hubcap knows
        # about the ones that did not.
        if isinstance(payload, dict) and ("recovered" in payload or "key_states" in payload):
            recovered = payload.get("recovered") or {}
            key_states = payload.get("key_states") or {}
        else:
            recovered = payload or {}
            key_states = {}

        if recovered:
            self._on_missing_depots_recovered(recovered)

        if hasattr(self, "table_widget") and self.table_widget:
            darker_grey = QColor(135, 135, 135)
            for row in range(self.table_widget.rowCount()):
                id_item = self.table_widget.item(row, 0)
                if id_item and id_item.data(Qt.ItemDataRole.UserRole + 2) == "missing":
                    did_str = str(id_item.data(Qt.ItemDataRole.UserRole))
                    minfo = self.missing_depots_info.get(did_str, {})
                    hubcap_status = minfo.get("hubcap_status")
                    state = key_states.get(did_str)

                    if state == "available":
                        # Hubcap holds the key, but the manifest bundle in use
                        # is older than the key. Refetching the bundle picks it
                        # up - that is what the recovery above does.
                        tag = "[Key Ready]"
                        tip = (
                            f"Depot {did_str} has a decryption key on Hubcap. "
                            f"Refresh the manifest bundle to pick it up."
                        )
                    elif state == "pending":
                        tag = "[No Key]"
                        tip = (
                            f"Hubcap knows depot {did_str} but has not published "
                            f"a decryption key yet."
                        )
                    elif hubcap_status == "missing_key":
                        tag = "[No Key]"
                        tip = f"Depot {did_str} has a manifest on Hubcap but lacks an AES decryption key. It cannot be downloaded."
                    else:
                        tag = "[Missing]"
                        tip = "Checked: Hubcap does not know this depot yet"

                    base_cfg = self._build_config_text(did_str, minfo)
                    cfg_text = f"{tag}  {base_cfg}"

                    config_item = self.table_widget.item(row, 1)
                    if config_item:
                        config_item.setText(cfg_text)
                        config_item.setForeground(darker_grey)
                        f = config_item.font()
                        f.setItalic(True)
                        config_item.setFont(f)
                        config_item.setToolTip(tip)
                    id_item.setToolTip(tip)

            self.table_widget.viewport().update()

    def _on_missing_depots_recovered(self, recovered_dict: Dict[str, str]):
        """Upgrades recovered depots from greyed-out missing to active selectable depots in the UI."""
        if not recovered_dict or not hasattr(self, "table_widget") or not self.table_widget:
            return

        logger.info(f"[DepotSelection] Upgrading {len(recovered_dict)} recovered depot(s) in UI: {list(recovered_dict.keys())}")
        for did, mid in recovered_dict.items():
            did_str = str(did)
            self._recovered_depots_map[did_str] = str(mid)
            if did_str in self.missing_hubcap_depots:
                self.missing_hubcap_depots.remove(did_str)

            # Clear from DB tracking so [Recovered] shows only once during this session
            try:
                from managers.db_manager import DatabaseManager
                DatabaseManager().clear_missing_hubcap_depot(str(self.app_id), did_str)
            except Exception:
                pass

            # Resolve depot metadata for the recovered depot
            d_data = dict(self.depots.get(did_str) or self.depots.get(int(did_str) if did_str.isdigit() else None) or {})
            if not d_data:
                d_data = dict(self.missing_depots_info.get(did_str) or {})

            try:
                from managers.db_manager import DatabaseManager
                app_info = DatabaseManager().get_app_info(str(self.app_id))
                if app_info and isinstance(app_info.get("depots"), dict):
                    db_depot = app_info["depots"].get(did_str) or app_info["depots"].get(int(did_str) if did_str.isdigit() else None)
                    if db_depot and isinstance(db_depot, dict):
                        for k, v in db_depot.items():
                            if not d_data.get(k) and v:
                                d_data[k] = v
            except Exception as e:
                logger.debug(f"[DepotSelection] DB lookup for recovered depot {did_str} failed: {e}")

            self.depots[did_str] = d_data

            for row in range(self.table_widget.rowCount()):
                id_item = self.table_widget.item(row, 0)
                if id_item and str(id_item.data(Qt.ItemDataRole.UserRole)) == did_str:
                    config_item = self.table_widget.item(row, 1)
                    size_item = self.table_widget.item(row, 3)

                    id_item.setData(Qt.ItemDataRole.UserRole + 2, "normal")
                    id_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                    id_item.setForeground(QColor(255, 255, 255))

                    # Restore check state if it was previously selected or in target edition
                    should_check = False
                    if self.selected_depots is not None:
                        should_check = did_str in set(str(d) for d in self.selected_depots)
                    elif hasattr(self, "_multi_platform_editions"):
                        default_eds = getattr(self, "_multi_platform_editions", {}).get(getattr(self, "_default_platform", "linux"), [])
                        highest_ed = default_eds[0] if default_eds else None
                        if highest_ed and highest_ed.get("depot_ids"):
                            should_check = did_str in set(str(d) for d in highest_ed["depot_ids"])

                    id_item.setCheckState(Qt.CheckState.Checked if should_check else Qt.CheckState.Unchecked)

                    if config_item:
                        base_txt = self._build_config_text(did_str, d_data)
                        config_item.setText(f"[Recovered]  {base_txt}")
                        config_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                        config_item.setForeground(QColor(255, 255, 255))
                        f = config_item.font()
                        f.setItalic(False)
                        config_item.setFont(f)
                        config_item.setToolTip("")

                    id_item.setToolTip("")

                    if size_item:
                        size_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                        size_item.setForeground(QColor(255, 255, 255))
                        sf = size_item.font()
                        sf.setItalic(False)
                        size_item.setFont(sf)
                        mraw_size = depot_metadata.manifest_size(d_data, self.branch)
                        if mraw_size > 0:
                            size_item.setText(format_size(mraw_size))
                            if hasattr(size_item, "sort_value"):
                                size_item.sort_value = mraw_size
                    break

        if hasattr(self, "linux_button") and self.linux_button:
            self.linux_button.setEnabled(self._has_native_linux_depots())
        self._update_table_headers()
        self.table_widget.viewport().update()

    def _is_manifest_on_disk(self, did: str, mid: str) -> bool:
        """Checks if the .manifest file is already saved in persistent or temporary manifests directory."""
        return depot_metadata.manifest_on_disk(did, mid)

    def _apply_depot_enrichments(self, enrichments: dict):
        """Merges enriched metadata into self.depots dictionary."""
        try:
            depot_metadata.merge_enrichments(self.depots, enrichments, base_appid=str(self.app_id))
        finally:
            if hasattr(self, "linux_button") and self.linux_button:
                has_linux = self._has_native_linux_depots()
                self.linux_button.setEnabled(has_linux)
                if has_linux:
                    self.linux_button.setToolTip("Smart select Linux installation (Native Linux if available; excludes media/32-bit)")
                else:
                    self.linux_button.setToolTip("No native Linux depots available for this game")

    def _start_enrichment_async(self, force: bool = False):
        """Asynchronously queries DB / SteamDB / Store API to enrich any generic or sizeless depots."""
        if not self.app_id or str(self.app_id) in ("0", "N/A", "unknown"):
            return

        # 1. Fast local SQLite cache check (0ms)
        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            cached_enrichments = db.get_depot_enrichments(str(self.app_id))
            if cached_enrichments:
                self._depots_enriched_signal.emit(cached_enrichments)
                # Only return early if no active depots still require generic resolution
                still_unresolved = any(
                    isinstance(d, dict) and depot_metadata.needs_name(d)
                    for d in self.depots.values()
                )
                if not still_unresolved and not force:
                    return
        except Exception as e:
            logger.debug(f"[DepotSelection] DB cache enrichment lookup error: {e}")

        import threading

        def _worker():
            try:
                from core.steamdb_scraper import ByparrManager, SteamDBScraper
                from managers.db_manager import DatabaseManager
                scraper = SteamDBScraper()
                depots_info = {}

                # Try SteamDB via Byparr if running
                if ByparrManager.is_running():
                    depots_info = scraper.get_app_depots(str(self.app_id))

                # Fast fallback: if SteamDB returned empty or Byparr is not running, query Steam Store API
                if not depots_info:
                    depots_info = scraper._fetch_depots_fallback_steam_store(str(self.app_id))

                if depots_info:
                    db = DatabaseManager()
                    db.save_depot_enrichments(str(self.app_id), depots_info)
                    self._depots_enriched_signal.emit(depots_info)
            except Exception as e:
                logger.debug(f"[DepotSelection] Background depot enrichment failed: {e}")

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _on_depots_enriched(self, depots_info: dict):
        """Live-updates the table widget with enriched names, DLC tags, and sizes."""
        if not depots_info or not hasattr(self, "table_widget"):
            return

        self._apply_depot_enrichments(depots_info)

        for row in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(row, 0)
            if not id_item:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role == "expander":
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            info = depots_info.get(depot_id)
            if not info:
                # Also check matching by dlcappid
                d_data = self.depots.get(depot_id, {})
                d_dlc = str(d_data.get("dlcappid") or "")
                if d_dlc and d_dlc in depots_info:
                    info = depots_info[d_dlc]

            if info:
                config_item = self.table_widget.item(row, 1)
                size_item = self.table_widget.item(row, 3)

                if role == "missing":
                    curr_config = config_item.text().strip() if config_item else ""
                    if info.get("name") and "Depot " in curr_config:
                        tag = "[Checking]" if getattr(self, "_is_checking_missing_contents", False) else "[Missing]"
                        config_item.setText(f"{tag}  {info['name']}")
                        f = config_item.font()
                        f.setItalic(True)
                        config_item.setFont(f)
                else:
                    curr_config = config_item.text().strip() if config_item else ""
                    clean_curr = re.sub(r"^\[.*?\]\s*", "", curr_config).strip()
                    if not clean_curr or depot_metadata.is_placeholder_desc(clean_curr):
                        name = info.get("name", "")
                        if name:
                            d_data = dict(self.depots.get(depot_id, {}))
                            d_data.update(info)
                            from utils.depot_tag_helpers import format_depot_row_label, get_os_icon
                            new_text = format_depot_row_label(
                                str(depot_id),
                                d_data,
                                name,
                                has_os_icon=True,
                                show_os_badge=False,
                                base_appid=str(self.app_id),
                            )
                            if config_item:
                                config_item.setText(new_text)
                                active_tint = QColor(self.accent_color) if hasattr(self, "accent_color") and self.accent_color else self.palette().color(QPalette.ColorRole.Text)
                                os_ic = get_os_icon(d_data.get("oslist"), tint_color=active_tint)
                                if os_ic:
                                    config_item.setIcon(os_ic)

                # Update size if missing
                curr_size = size_item.text().strip() if size_item else ""
                if not curr_size or curr_size in ("0.00 B", "No size", ""):
                    if info.get("size_str") and info.get("size_str") != "No size":
                        if size_item:
                            size_item.setText(info["size_str"])
                            if hasattr(size_item, "sort_value"):
                                size_item.sort_value = int(info.get("size_bytes") or 0)

        self._update_table_headers()
        if getattr(self, "_dlc_only_mode", False) and not getattr(self, "_has_saved_selection", False) and not getattr(self, "_user_interacted", False):
            self._apply_dlc_auto_selection()
