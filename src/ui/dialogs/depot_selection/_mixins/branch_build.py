"""Branch and build selection from the header, plus the build pin and
manifest-override accessors."""

import logging
import re

from pathlib import Path
from typing import Optional, Dict

from PyQt6.QtWidgets import QPushButton

from utils.settings import get_settings

logger = logging.getLogger(__name__)


class BranchBuildMixin:
    """Branch and build selection, plus the build pin and manifest overrides."""

    def get_selected_branch(self) -> str:
        if hasattr(self, "branch_combo") and self.branch_combo is not None:
            return self.branch_combo.currentText().strip()
        return getattr(self, "branch", "public") or "public"

    def _resolve_local_buildid(self) -> str:
        """Attempt to resolve installed build ID from local appmanifest or QSettings."""
        aid = str(getattr(self, "app_id", "") or "").strip()
        if not aid or aid in ("0", "N/A", "unknown"):
            return ""
        try:
            from core.steam_helpers import get_steam_libraries
            from pathlib import Path
            import re
            for lib in get_steam_libraries():
                acf_path = Path(lib) / "steamapps" / f"appmanifest_{aid}.acf"
                if acf_path.is_file():
                    with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    m = re.search(r'"buildid"\s+"([^"]+)"', content)
                    if m and m.group(1).strip() and m.group(1).strip() != "0":
                        return m.group(1).strip()
        except Exception:
            pass

        try:
            from utils.settings import get_settings
            s = get_settings()
            stored = str(s.value(f"installed_buildid/{aid}", "")).strip()
            if stored and stored.isdigit() and stored != "0":
                return stored
        except Exception:
            pass
        return ""

    def _try_resolve_historical_buildid_async(self):
        """If bundle is stale and build ID is unknown, check SteamDB cache in background."""
        aid_str = str(getattr(self, "app_id", "") or "").strip()
        if not aid_str.isdigit() or aid_str in ("0", "N/A", "unknown"):
            return
        aid_int = int(aid_str)

        def _worker():
            try:
                from core.steamdb_scraper import SteamDBBuildsCache
                cached_builds = SteamDBBuildsCache().get_builds(aid_int) or []
                if not cached_builds:
                    return

                zip_manifests = {}
                depots_dict = getattr(self, "depots", {}) or {}
                from utils.depot_utils import iter_depot_manifest_gids
                sel_branch = getattr(self, "branch", "public")
                for did, ddata in depots_dict.items():
                    gids = iter_depot_manifest_gids(ddata, sel_branch)
                    if gids:
                        zip_manifests[str(did)] = gids

                if not zip_manifests:
                    return

                for cb in cached_builds:
                    c_depots = cb.get("depots", {})
                    if any(
                        str(d_id) in c_depots and str(c_depots[str(d_id)].get("manifest_id")) in m_ids
                        for d_id, m_ids in zip_manifests.items()
                    ):
                        matched_bid = str(cb.get("buildid", "")).strip()
                        if matched_bid:
                            def _update_ui(bid=matched_bid):
                                if not getattr(self, "_selected_build_id", None) or self._selected_build_id == getattr(self, "current_build_id", ""):
                                    self.current_build_id = bid
                                    self._selected_build_id = bid
                                    if hasattr(self, "builds_btn") and self.builds_btn is not None:
                                        self.builds_btn.setText(f"Build: {bid} (Older)")
                            from utils.main_thread import post_to_main_thread
                            post_to_main_thread(_update_ui)
                            break
            except Exception as e:
                logger.debug(f"[DepotSelection] Background build ID match failed: {e}")

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    def get_selected_build(self) -> Optional[str]:
        return getattr(self, "_selected_build_id", None) or self.current_build_id

    def is_build_pinned(self) -> bool:
        return getattr(self, "_is_build_pinned", False)

    def get_manifest_overrides(self) -> Dict[str, str]:
        return getattr(self, "_manifest_overrides", {})

    def _on_branch_changed(self, new_branch: str):
        if not new_branch:
            return
        self.branch = new_branch
        b_info = self.branches.get(new_branch)
        new_bid = ""
        if isinstance(b_info, dict) and b_info.get("buildid"):
            new_bid = str(b_info["buildid"]).strip()
            if new_bid:
                self.current_build_id = new_bid
                self._selected_build_id = new_bid
                if hasattr(self, "builds_btn") and self.builds_btn is not None:
                    self.builds_btn.setText(f"Build: {new_bid}")

        self._is_build_pinned = (new_branch != "public")
        if hasattr(self, "builds_btn") and self.builds_btn is not None:
            self._update_build_btn_style()

        # Update manifest overrides for the selected branch
        from utils.branch_helpers import resolve_branch_manifest_gid
        self._manifest_overrides.clear()

        depots_source = self.depots
        has_branch_manifests = any(
            isinstance(d, dict) and "manifests" in d for d in self.depots.values()
        )
        if not has_branch_manifests and self.app_id:
            try:
                from core.steam_api import get_depot_info_from_api
                pics_info = get_depot_info_from_api(self.app_id)
                if pics_info and pics_info.get("depots"):
                    depots_source = pics_info["depots"]
                    for did, d_data in depots_source.items():
                        if str(did) in self.depots and isinstance(self.depots[str(did)], dict):
                            if "manifests" in d_data:
                                self.depots[str(did)]["manifests"] = d_data["manifests"]
            except Exception as e:
                logger.debug(f"[DepotSelection] Could not fetch PICS depot info for branch manifests: {e}")

        if new_branch != "public":
            for did, d_info in depots_source.items():
                if str(did) == str(self.app_id):
                    continue
                gid = resolve_branch_manifest_gid(d_info, new_branch)
                if gid:
                    self._manifest_overrides[str(did)] = str(gid)
                    if str(did) in self.depots and isinstance(self.depots[str(did)], dict):
                        self.depots[str(did)]["manifest_id"] = str(gid)
            logger.info(f"[DepotSelection] Branch '{new_branch}' (Build {new_bid}) manifest overrides: {self._manifest_overrides}")
        else:
            for did, d_info in depots_source.items():
                if str(did) == str(self.app_id):
                    continue
                gid = resolve_branch_manifest_gid(d_info, "public")
                if gid:
                    if str(did) in self.depots and isinstance(self.depots[str(did)], dict):
                        self.depots[str(did)]["manifest_id"] = str(gid)
            logger.info(f"[DepotSelection] Switched to public branch (Build {new_bid})")

    def _update_build_btn_style(self):
        if getattr(self, "_is_build_pinned", False):
            self.builds_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.08);
                    border: 1px solid {self.accent_color};
                    border-radius: 6px;
                    color: {self.accent_color};
                    font-size: 8.5pt;
                    font-weight: 600;
                    padding: 2px 12px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.14);
                    border: 1px solid {self.accent_color};
                }}
            """)
        elif getattr(self, "is_bundle_stale", False):
            self.builds_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(230, 126, 34, 0.12);
                    border: 1px solid rgba(230, 126, 34, 0.6);
                    border-radius: 6px;
                    color: #f39c12;
                    font-size: 8.5pt;
                    font-weight: 600;
                    padding: 2px 12px;
                }
                QPushButton:hover {
                    background-color: rgba(230, 126, 34, 0.22);
                    border-color: #f39c12;
                    color: #f39c12;
                }
            """)
        else:
            accent_r, accent_g, accent_b = self._resolve_accent_colors() if hasattr(self, "_resolve_accent_colors") else (192, 108, 132)
            self.builds_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.06);
                    border: 1px solid rgba({accent_r}, {accent_g}, {accent_b}, 0.35);
                    border-radius: 6px;
                    color: #FFFFFF;
                    font-size: 8.5pt;
                    font-weight: 600;
                    padding: 2px 12px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.12);
                    border-color: {self.accent_color};
                    color: {self.accent_color};
                }}
            """)
        if hasattr(self, "builds_btn") and self.builds_btn is not None:
            self.builds_btn.adjustSize()

    def _apply_build_selection(self, selected_bid: str, patch_depots: dict):
        if not selected_bid:
            return
        self._selected_build_id = selected_bid
        self.builds_btn.setText(f"Build: {selected_bid}")
        if self.current_build_id and selected_bid == self.current_build_id:
            self._is_build_pinned = False
        else:
            self._is_build_pinned = True
        self._update_build_btn_style()

        if patch_depots:
            for did, info in patch_depots.items():
                mid = info.get("manifest_id") if isinstance(info, dict) else str(info)
                if mid:
                    self._manifest_overrides[str(did)] = str(mid)
                    if str(did) in self.depots and isinstance(self.depots[str(did)], dict):
                        self.depots[str(did)]["manifest_id"] = str(mid)
                    logger.info(f"[DepotSelection] Overrode depot {did} manifest to {mid} for build {selected_bid}")

    def _on_builds_clicked(self):
        from core.steamdb_scraper import ByparrManager
        has_byparr = ByparrManager.find_byparr_dir() is not None

        first_depot = next(iter(self.depots.keys())) if self.depots else str(self.app_id)

        active_bid = getattr(self, "_selected_build_id", None) or self.current_build_id

        if not has_byparr:
            from ui.dialogs.manual_manifest_dialog import ManualManifestDialog
            dlg = ManualManifestDialog(
                parent=self,
                app_id=self.app_id,
                game_name=self.game_name,
                depots_dict=self.depots,
                default_depot_id=first_depot,
                current_build_id=active_bid,
                accent_color=self.accent_color,
            )
            if dlg.exec():
                selected_bid, patch_depots = dlg.get_selected_build()
                self._apply_build_selection(selected_bid, patch_depots)
        else:
            from ui.dialogs.build_selection_dialog import BuildSelectionDialog
            dlg = BuildSelectionDialog(
                parent=self,
                app_id=self.app_id,
                game_name=self.game_name,
                current_build_id=active_bid,
                accent_color=self.accent_color,
                depots_dict=self.depots,
                default_depot_id=first_depot,
            )
            if dlg.exec():
                selected_bid, patch_depots = dlg.get_selected_build()
                self._apply_build_selection(selected_bid, patch_depots)
