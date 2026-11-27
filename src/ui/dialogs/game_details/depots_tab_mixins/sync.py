"""SLSsteam config.yaml atomic persistence, unlock workflow, and DLC setup registration mixin."""

import logging
from pathlib import Path
import re
from typing import Any, Dict

from ui.dialogs.themed_message_box import ThemedMessageBox
from utils.plugin_games import (
    build_depot_comment,
    build_key_comment,
    load_plugin_library,
    save_plugin_library,
)
from utils.sls_bridge import SLSBridge
from utils.yaml_config_manager import (
    batch_config_edit,
    ensure_plugins_enabled,
    get_additional_depots,
    get_decryption_keys,
    get_user_config_path,
)

logger = logging.getLogger(__name__)


class DepotsSyncMixin:
    """Handles unlock confirmation, atomic YAML config synchronization, and DLC setup registration."""

    def _on_unlock(self) -> None:
        """Unlock custom depot and configuration selection after confirmation dialog."""
        if not self.is_unlocked:
            reply = ThemedMessageBox.warning(
                self.dialog,
                "Advanced Configuration",
                "Modifying depot and configuration entries is intended for advanced users only.\n\n"
                "Excluding required depots or keys may prevent Steam from downloading or launching the game properly.\n\n"
                "Do you want to unlock custom configuration editing?",
                ThemedMessageBox.StandardButton.Yes
                | ThemedMessageBox.StandardButton.No,
                ThemedMessageBox.StandardButton.No,
            )
            if reply == ThemedMessageBox.StandardButton.Yes:
                self.is_unlocked = True
                self._sync_mode_seg()
                self.status_lbl.setText(
                    "Editing on. Change the switches, then Save."
                )
                self._refresh_ui()
            else:
                self._sync_mode_seg()
        else:
            self.is_unlocked = False
            self._sync_mode_seg()
            self.status_lbl.setText("")
            self._refresh_ui()

    def _on_sync(self) -> None:
        """Sync desired additions and removals specifically for this game into config.yaml."""
        state = self._collect_state()
        self.current_state = state
        cfg_path = get_user_config_path()
        if not cfg_path.exists():
            self.status_lbl.setText("Error: config.yaml not found.")
            return

        ensure_plugins_enabled(cfg_path)
        gd = self._get_game_data()
        game_name = gd.get("game_name") or f"App {self.appid_str}"

        added_count = 0
        removed_count = 0

        selected_dlc_apps = {
            a["id"]: a["desc"]
            for a in state["apps"]
            if self.is_dlc_setup
            and not a.get("is_base")
            and not a.get("is_unreleased")
            and self.desired_apps.get(a["id"], False)
        }
        use_dlc_data = self.is_dlc_setup and len(selected_dlc_apps) >= 64
        dlc_data_pending: Dict[str, str] = {}

        with batch_config_edit(cfg_path) as editor:
            if self.is_unlocked:
                # 1. Apps
                for item in state["apps"]:
                    aid = item["id"]
                    if item.get("is_dlc_base_locked") or item.get(
                        "is_unreleased"
                    ):
                        desired = False
                    else:
                        desired = self.desired_apps.get(
                            aid, aid in state["live_apps"]
                        )
                    if desired and aid not in state["live_apps"]:
                        if use_dlc_data and not item.get("is_base"):
                            dlc_data_pending[aid] = re.sub(
                                r"^(?:\[[^\]]*\]\s*)+", "", item["desc"]
                            ).strip()
                            continue
                        editor.add_app(aid, comment=item["desc"])
                        added_count += 1
                    elif not desired and aid in state["live_apps"]:
                        editor.remove_app(aid)
                        removed_count += 1

                if self.appid_str in state["live_depots"]:
                    logger.warning(
                        f"[DepotsTab] Pruned base AppID '{self.appid_str}' from AdditionalDepots for {game_name}"
                    )
                    editor.remove_depot(self.appid_str, check_shared=False)
                    removed_count += 1

                # 2. Depots
                for item in state["depots"]:
                    did = item["id"]
                    if item.get("is_unreleased"):
                        if (
                            did in state["live_depots"]
                            and did != self.appid_str
                        ):
                            editor.remove_depot(
                                did,
                                check_shared=True,
                                excluding_appid=self.appid_str,
                            )
                            removed_count += 1
                        continue
                    if did == self.appid_str or item.get("is_missing"):
                        continue
                    desired = self.desired_depots.get(
                        did, did in state["live_depots"]
                    )
                    if desired and did not in state["live_depots"]:
                        comment = build_depot_comment(
                            game_name, did, "", self.appid_str
                        )
                        editor.add_depot(
                            did, comment=comment, app_id=self.appid_str
                        )
                        added_count += 1
                    elif not desired and did in state["live_depots"]:
                        editor.remove_depot(
                            did,
                            check_shared=True,
                            excluding_appid=self.appid_str,
                        )
                        removed_count += 1

                # 3. Keys
                for item in state["keys"]:
                    kid = item["id"]
                    if (
                        item.get("is_missing")
                        or not item.get("key")
                        or item.get("is_dlc_base_locked")
                        or item.get("is_pure_license")
                        or item.get("is_unreleased")
                    ):
                        continue
                    desired = self.desired_keys.get(kid, item["in_config"])
                    kval = item["key"]
                    if desired and not item["in_config"]:
                        if kval:
                            comment = build_key_comment(
                                game_name, kid, "", self.appid_str
                            )
                            editor.add_key(kid, kval, comment=comment)
                            added_count += 1
                    elif not desired and item["in_config"]:
                        editor.remove_key(
                            kid,
                            check_shared=True,
                            excluding_appid=self.appid_str,
                        )
                        removed_count += 1
            else:
                if self.appid_str in state["live_depots"]:
                    logger.warning(
                        f"[DepotsTab] Pruned base AppID '{self.appid_str}' from AdditionalDepots for {game_name}"
                    )
                    editor.remove_depot(self.appid_str, check_shared=False)
                    removed_count += 1

                # 1. Base AppID ONLY
                from utils.dlc_helpers import is_dlc_only_mode

                base_app_meta = next(
                    (a for a in state["apps"] if a["id"] == self.appid_str), {}
                )
                is_base_owned_locked = base_app_meta.get(
                    "is_owned_base", False
                )
                if (
                    not is_dlc_only_mode(self.appid_str)
                    and not is_base_owned_locked
                    and self.appid_str not in state["live_apps"]
                ):
                    editor.add_app(
                        self.appid_str, comment=f"{game_name} (Base)"
                    )
                    added_count += 1

                # 2. Depots
                valid_keys = set(
                    item["id"]
                    for item in state["keys"]
                    if item["key"]
                    and not item.get("is_base_owned")
                    and not item.get("is_pure_license")
                    and not item.get("is_unreleased")
                    and not item.get("is_owned")
                )
                for item in state["depots"]:
                    did = item["id"]
                    if item.get("is_unreleased"):
                        if (
                            did in state["live_depots"]
                            and did != self.appid_str
                        ):
                            editor.remove_depot(
                                did,
                                check_shared=True,
                                excluding_appid=self.appid_str,
                            )
                            removed_count += 1
                        continue
                    if did == self.appid_str or item.get("is_owned"):
                        continue
                    if did not in state["live_depots"] and did in valid_keys:
                        comment = build_depot_comment(
                            game_name, did, "", self.appid_str
                        )
                        editor.add_depot(
                            did, comment=comment, app_id=self.appid_str
                        )
                        added_count += 1

                # 3. Keys
                for item in state["keys"]:
                    kid = item["id"]
                    if (
                        item.get("is_base_owned")
                        or item.get("is_pure_license")
                        or item.get("is_unreleased")
                        or item.get("is_owned")
                    ):
                        continue
                    kval = item["key"]
                    if kval and not item["in_config"]:
                        comment = build_key_comment(
                            game_name, kid, "", self.appid_str
                        )
                        editor.add_key(kid, kval, comment=comment)
                        added_count += 1

            # Always ensure any shared redistributables required by this game are enabled
            for s_did in sorted(state.get("game_shared_depots", set())):
                if s_did not in state["live_depots"]:
                    editor.add_depot(s_did, comment="Steamworks Shared")
                    added_count += 1
                s_key = (
                    state.get("lua_keys", {}).get(s_did)
                    or state.get("game_keys", {}).get(s_did)
                    or state.get("local_db_keys", {}).get(s_did)
                )
                if s_key and s_did not in state["live_keys"]:
                    editor.add_key(s_did, s_key, comment="Steamworks Shared")
                    added_count += 1

        if dlc_data_pending:
            try:
                from utils.yaml_config_manager import add_dlc_data_batch

                add_dlc_data_batch(cfg_path, self.appid_str, dlc_data_pending)
                added_count += len(dlc_data_pending)
            except Exception as e:
                logger.error(
                    f"[DepotsTab] DlcData registration failed: {e}",
                    exc_info=True,
                )

        try:
            from managers.depot_key_manager import DepotKeyManager

            keys_to_persist = {}
            for item in state.get("keys", []):
                kid = item["id"]
                kval = item.get("key") or state.get("live_keys", {}).get(kid)
                if kval and len(str(kval)) == 64:
                    keys_to_persist[kid] = str(kval).lower()
            if keys_to_persist:
                DepotKeyManager.get_instance().save_depot_keys(
                    self.appid_str, keys_to_persist
                )
        except Exception as e:
            logger.debug(
                f"[DepotsTab] Error persisting keys to local DB on sync: {e}"
            )

        if editor.has_changes:
            try:
                plib = load_plugin_library()
                if self.appid_str in plib:
                    live_cfg_depots = set(get_additional_depots(cfg_path))
                    live_cfg_keys = get_decryption_keys(cfg_path)
                    existing_active = [
                        d
                        for d in plib[self.appid_str].get("depots", [])
                        if d in live_cfg_depots
                    ]
                    toggled_active = [
                        d["id"]
                        for d in state.get("depots", [])
                        if d["id"] in live_cfg_depots
                    ]
                    updated_rec_depots = sorted(
                        list(set(existing_active) | set(toggled_active))
                    )

                    updated_rec_keys = dict(
                        plib[self.appid_str].get("keys", {})
                    )
                    for k in state.get("keys", []):
                        kid = k["id"]
                        if kid in live_cfg_keys:
                            kval = k.get("key") or live_cfg_keys.get(kid)
                            if kval:
                                updated_rec_keys[kid] = str(kval).lower()
                        else:
                            updated_rec_keys.pop(kid, None)

                    plib[self.appid_str]["depots"] = updated_rec_depots
                    plib[self.appid_str]["keys"] = updated_rec_keys
                    save_plugin_library(plib)
            except Exception as e:
                logger.debug(
                    f"[DepotsTab] Error updating plugin library on sync: {e}"
                )

            SLSBridge.notify_reload()
            parts = []
            if added_count:
                parts.append(f"{added_count} added")
            if removed_count:
                parts.append(f"{removed_count} removed")
            self.status_lbl.setText(f"Saved: {', '.join(parts)}.")
        else:
            self.status_lbl.setText("Nothing to save.")

        if self.is_dlc_setup:
            self._register_dlc_setup(
                state,
                cfg_path,
                selected_dlc_apps,
                added_count,
                removed_count,
                bool(dlc_data_pending),
            )
            return

        self._refresh_ui()

    def _register_dlc_setup(
        self,
        state: Dict[str, Any],
        cfg_path: Path,
        selected_dlc_apps: Dict[str, str],
        added_count: int,
        removed_count: int,
        used_dlc_data: bool,
    ) -> None:
        """Record an AT0-M DLC-only setup (plugin library + DLC-only flag) and hand back."""
        gd = self._get_game_data()
        game_name = gd.get("game_name") or f"App {self.appid_str}"
        live_depots_now = set(get_additional_depots(cfg_path))
        live_keys_now = get_decryption_keys(cfg_path)
        reg_depots = {
            d["id"]: (d.get("raw_desc") or d["desc"])
            for d in state["depots"]
            if d["id"] in live_depots_now
        }
        reg_keys = {
            k["id"]: str(
                live_keys_now.get(k["id"]) or k.get("key") or ""
            ).lower()
            for k in state["keys"]
            if k["id"] in live_keys_now and not k.get("is_dlc_base_locked")
        }
        try:
            plib = load_plugin_library()
            rec = plib.get(self.appid_str, {})
            dlcs = set(rec.get("dlc_appids", [])) | set(
                selected_dlc_apps.keys()
            )
            names = dict(rec.get("depot_names", {}))
            names.update(reg_depots)
            keys = dict(rec.get("keys", {}))
            keys.update({k: v for k, v in reg_keys.items() if v})
            plib[self.appid_str] = {
                **rec,
                "name": game_name,
                "appid": self.appid_str,
                "is_atom": True,
                "is_dlc_only": True,
                "dlc_appids": sorted(
                    dlcs, key=lambda x: int(x) if x.isdigit() else 0
                ),
                "depots": sorted(
                    set(rec.get("depots", [])) | set(reg_depots),
                    key=lambda x: int(x) if x.isdigit() else 0,
                ),
                "keys": keys,
                "depot_names": names,
            }
            save_plugin_library(plib)
            from utils.settings import get_settings

            get_settings().setValue(f"dlc_only_mode/{self.appid_str}", True)
            get_settings().setValue(
                f"dlc_mode_source/{self.appid_str}", "native"
            )
        except Exception as e:
            logger.error(
                f"[DepotsTab] Could not record AT0-M DLC setup for {self.appid_str}: {e}",
                exc_info=True,
            )

        SLSBridge.notify_reload()
        summary = {
            "dlcs": len(selected_dlc_apps),
            "depots": len(reg_depots),
            "keys": len(reg_keys),
            "added": added_count,
            "removed": removed_count,
            "dlc_data": used_dlc_data,
        }
        if callable(self.on_registered):
            self.on_registered(summary)
        else:
            self.status_lbl.setText(
                f"Registered {summary['dlcs']} DLC(s), {summary['depots']} depot(s)."
            )
            self._refresh_ui()