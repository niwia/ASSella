"""Table population, row toggle callbacks, and bulk toggle handlers for DepotsTabController."""

from functools import partial
import logging
from typing import Any, Dict

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QTableWidgetItem

from ui.dialogs.game_details.depots_tab_mixins.widgets import (
    MiniSwitchToggle,
    _ToggleContainer,
    _make_local_indicator,
    _set_table_height,
)
from ui.dialogs.themed_message_box import confirm_steam_owned_override
from utils.release_gate import STATUS_UNRELEASED, UNRELEASED_TAG, unreleased_tooltip

logger = logging.getLogger(__name__)


class DepotsTablesMixin:
    """Manages rendering of Apps, Depots, and Keys tables along with cascading toggles."""

    def _refresh_ui(self) -> None:
        """Populate table widgets with gathered data."""
        if self._is_destroyed():
            return
        state = self._collect_state()
        self.current_state = state
        self.app_to_depots = self._build_app_to_depots_map(
            state["apps"], state.get("depots")
        )

        live_apps = state["live_apps"]
        live_depots = state["live_depots"]

        self.app_toggles.clear()
        self.depot_toggles.clear()
        self.key_toggles.clear()

        if not self.is_unlocked:
            self.desired_apps.clear()
            for item in state["apps"]:
                self.desired_apps[item["id"]] = item["id"] in live_apps
            self.desired_depots.clear()
            for item in state["depots"]:
                self.desired_depots[item["id"]] = item["id"] in live_depots
            self.desired_keys.clear()
            for item in state["keys"]:
                self.desired_keys[item["id"]] = item["in_config"]

        self._populate_apps_table(state)
        self._populate_depots_table(state)
        self._populate_keys_table(state)

        self._update_sync_button_label(state)
        self._update_bulk_states(state)

    def _populate_apps_table(self, state: Dict[str, Any]) -> None:
        apps = state["apps"]
        live_apps = state["live_apps"]
        game_keys = state["game_keys"]

        self.apps_table.setRowCount(len(apps))
        for row, item in enumerate(apps):
            aid = item["id"]
            is_base = item.get("is_base", False)
            is_live = aid in live_apps
            has_matching_key = (not is_base) and (aid in game_keys)

            id_item = QTableWidgetItem(aid)
            id_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            desc_item = QTableWidgetItem(item["desc"])
            desc_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )

            if has_matching_key:
                id_item.setForeground(QColor("#777777"))
                desc_item.setForeground(QColor("#777777"))
                desc_item.setToolTip(
                    "DLC has matching depot key. Decryption is handled via DecryptionKeys."
                )

            if item.get("is_dlc_base_locked") and is_base:
                id_item.setForeground(QColor("#777777"))
                desc_item.setForeground(QColor("#777777"))
                desc_item.setToolTip(
                    "DLC-Only Mode is active. Base game AppID cannot be added to AdditionalApps."
                )
                self.apps_table.setItem(row, 0, id_item)
                self.apps_table.setItem(row, 1, desc_item)
                toggle = MiniSwitchToggle(
                    checked=False, read_only=True, greyscaled=True
                )
                toggle.setEnabled(False)
                toggle.setToolTip(
                    "DLC-Only Mode is active. Base game AppID cannot be added to AdditionalApps."
                )
                self.app_toggles[aid] = toggle
                self.apps_table.setCellWidget(row, 2, _ToggleContainer(toggle))
                self.desired_apps[aid] = False
                continue

            is_owned_app = bool(
                item.get("is_owned") or item.get("is_owned_base")
            )
            if is_owned_app and not item.get("is_unreleased"):
                id_item.setForeground(QColor("#777777"))
                desc_item.setForeground(QColor("#777777"))
                desc_item.setToolTip(
                    "ASSella suspects this item is owned on Steam. Toggle only if you do not own it on Steam."
                )

            if item.get("is_unreleased"):
                id_item.setForeground(QColor("#777777"))
                desc_item.setForeground(QColor("#777777"))
                f_desc = desc_item.font()
                f_desc.setItalic(True)
                desc_item.setFont(f_desc)
                desc_item.setToolTip(
                    "DLC is unreleased on Steam (prerelease). Cannot be enabled."
                )
                self.apps_table.setItem(row, 0, id_item)
                self.apps_table.setItem(row, 1, desc_item)
                toggle = MiniSwitchToggle(
                    checked=False, read_only=True, greyscaled=True
                )
                toggle.setEnabled(False)
                toggle.setToolTip(
                    "DLC is unreleased on Steam (prerelease). Cannot be enabled."
                )
                self.app_toggles[aid] = toggle
                self.apps_table.setCellWidget(row, 2, _ToggleContainer(toggle))
                self.desired_apps[aid] = False
                continue

            self.apps_table.setItem(row, 0, id_item)
            self.apps_table.setItem(row, 1, desc_item)

            current_checked = self.desired_apps.get(aid, is_live)
            toggle = MiniSwitchToggle(
                checked=current_checked, read_only=(not self.is_unlocked)
            )
            if is_owned_app:
                toggle.setToolTip(
                    "ASSella suspects this item is owned on Steam. Enabling requires confirmation."
                )
            self.app_toggles[aid] = toggle

            toggle.toggled.connect(
                partial(self._on_app_row_toggled, aid, is_owned_app)
            )
            self.apps_table.setCellWidget(row, 2, _ToggleContainer(toggle))

        _set_table_height(self.apps_table, len(apps))
        self.apps_bulk_bar.setVisible(len(apps) >= 5)
        self.apps_bulk_toggle.setReadOnly(not self.is_unlocked)

    def _populate_depots_table(self, state: Dict[str, Any]) -> None:
        depots = state["depots"]
        live_depots = state["live_depots"]

        self.depots_table.setRowCount(len(depots))
        for row, item in enumerate(depots):
            did = item["id"]
            is_live = did in live_depots
            is_unrel = bool(item.get("is_unreleased"))
            is_owned_row = bool(item.get("is_owned")) and not is_unrel
            is_missing_key = bool(item.get("is_missing", False))
            is_locked_row = is_missing_key or is_unrel

            if is_unrel and not item.get("missing_tag"):
                item = dict(
                    item,
                    missing_status=STATUS_UNRELEASED,
                    missing_tag=UNRELEASED_TAG,
                    missing_tooltip=unreleased_tooltip(did),
                )
            elif is_owned_row and not is_missing_key:
                item = dict(
                    item,
                    missing_status="owned",
                    missing_tag="[Steam Owned]",
                    missing_tooltip=(
                        f"ASSella suspects depot {did} belongs to content you own on Steam. "
                        "Toggle only if you do not own it on Steam."
                    ),
                )

            is_muted = is_locked_row or is_owned_row
            id_item = QTableWidgetItem(did)
            id_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            if is_muted:
                id_item.setForeground(QColor(130, 130, 130))
                font_id = id_item.font()
                font_id.setItalic(True)
                id_item.setFont(font_id)
                id_item.setToolTip(
                    item.get("missing_tooltip")
                    or f"Depot {did} key is missing / awaiting Hubcap."
                )

            desc_text = item["desc"]
            if item.get("is_shared"):
                desc_text = f"{desc_text} [Shared]"
            if is_muted and item.get("missing_tag"):
                desc_text = f"{item['missing_tag']}  {desc_text}"

            desc_item = QTableWidgetItem(desc_text)
            desc_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            if item.get("os_icon"):
                desc_item.setIcon(item["os_icon"])
            if is_muted:
                m_stat = item.get("missing_status")
                if m_stat == "ready_on_hubcap":
                    desc_item.setForeground(QColor("#4DD0E1"))
                elif m_stat == "key_pending":
                    desc_item.setForeground(QColor("#FFB74D"))
                else:
                    desc_item.setForeground(QColor(130, 130, 130))
                font_desc = desc_item.font()
                font_desc.setItalic(True)
                desc_item.setFont(font_desc)
                desc_item.setToolTip(item.get("missing_tooltip") or desc_text)
            else:
                tooltip_parts = [desc_text]
                if item.get("size_str"):
                    tooltip_parts.append(f"Size: {item['size_str']}")
                if item.get("is_shared"):
                    tooltip_parts.append(
                        "Shared with another game / protected from removal"
                    )
                desc_item.setToolTip("\n".join(tooltip_parts))

            self.depots_table.setItem(row, 0, id_item)
            self.depots_table.setItem(row, 1, desc_item)

            if is_locked_row:
                toggle = MiniSwitchToggle(
                    checked=False, read_only=True, greyscaled=True
                )
                toggle.setEnabled(False)
                toggle.setToolTip(
                    item.get("missing_tooltip")
                    or "Depot key is not available — cannot enable in AdditionalDepots."
                )
                self.depot_toggles[did] = toggle
                self.depots_table.setCellWidget(row, 2, _ToggleContainer(toggle))
            else:
                current_checked = self.desired_depots.get(did, is_live)
                toggle = MiniSwitchToggle(
                    checked=current_checked, read_only=(not self.is_unlocked)
                )
                if is_owned_row:
                    toggle.setToolTip(
                        item.get("missing_tooltip")
                        or "ASSella suspects this depot is owned on Steam."
                    )
                self.depot_toggles[did] = toggle

                toggle.toggled.connect(
                    partial(
                        self._on_depot_row_toggled,
                        did,
                        bool(item.get("is_shared")),
                        is_owned_row,
                    )
                )
                self.depots_table.setCellWidget(row, 2, _ToggleContainer(toggle))

        _set_table_height(self.depots_table, len(depots))
        self.depots_bulk_bar.setVisible(len(depots) >= 5)
        self.depots_bulk_toggle.setReadOnly(not self.is_unlocked)

    def _populate_keys_table(self, state: Dict[str, Any]) -> None:
        keys = state["keys"]
        gd = self._get_game_data()
        game_name = (
            state.get("game_name")
            or gd.get("game_name")
            or f"App {self.appid_str}"
        )

        self.keys_table.setRowCount(len(keys))
        for row, item in enumerate(keys):
            kid = item["id"]
            is_missing = item.get("is_missing", False)

            id_item = QTableWidgetItem(kid)
            id_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            if is_missing:
                id_item.setForeground(QColor(130, 130, 130))
                font_id = id_item.font()
                font_id.setItalic(True)
                id_item.setFont(font_id)

            matching_desc = next(
                (d["desc"] for d in state["depots"] if d["id"] == kid), None
            )
            if not matching_desc and (
                kid == self.appid_str or item.get("is_appkey")
            ):
                matching_desc = f"{game_name} (AppKey)"
            elif not matching_desc:
                matching_app = next(
                    (
                        a["desc"]
                        for a in state.get("apps", [])
                        if a["id"] == kid
                    ),
                    None,
                )
                if matching_app:
                    matching_desc = f"{matching_app} (Key)"
            if matching_desc:
                id_item.setToolTip(matching_desc)
            self.keys_table.setItem(row, 0, id_item)

            if is_missing:
                key_text = item.get("missing_tag", "[No Key]")
                key_item = QTableWidgetItem(key_text)
                key_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                m_stat = item.get("missing_status")
                if m_stat == "ready_on_hubcap":
                    key_item.setForeground(QColor("#4DD0E1"))
                elif m_stat == "key_pending":
                    key_item.setForeground(QColor("#FFB74D"))
                else:
                    key_item.setForeground(QColor(130, 130, 130))
                font_k = key_item.font()
                font_k.setItalic(True)
                key_item.setFont(font_k)
                key_item.setToolTip(
                    item.get("missing_tooltip")
                    or "No AES decryption key available."
                )
                self.keys_table.setItem(row, 1, key_item)

                toggle = MiniSwitchToggle(
                    checked=False, read_only=True, greyscaled=True
                )
                toggle.setEnabled(False)
                toggle.setToolTip(
                    item.get("missing_tooltip")
                    or "Decryption key not available — cannot enable."
                )
                self.key_toggles[kid] = toggle
                self.keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                loc_ind = _make_local_indicator(in_local=False, is_missing=True)
                self.keys_table.setCellWidget(row, 3, loc_ind)
            elif item.get("is_unreleased"):
                raw_key = item.get("key") or ""
                short_key = (
                    f"{raw_key[:15]}..." if len(raw_key) > 15 else raw_key
                )
                key_item = QTableWidgetItem(
                    f"{UNRELEASED_TAG}  {short_key}".strip()
                )
                key_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                key_item.setForeground(QColor(130, 130, 130))
                f_u = key_item.font()
                f_u.setItalic(True)
                key_item.setFont(f_u)
                lock_tip = unreleased_tooltip(kid)
                key_item.setToolTip(lock_tip)
                self.keys_table.setItem(row, 1, key_item)

                toggle = MiniSwitchToggle(
                    checked=bool(item.get("in_config")),
                    read_only=True,
                    greyscaled=True,
                )
                toggle.setEnabled(False)
                toggle.setToolTip(lock_tip)
                self.key_toggles[kid] = toggle
                self.keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                loc_ind = _make_local_indicator(
                    in_local=bool(item.get("in_local")), is_missing=False
                )
                self.keys_table.setCellWidget(row, 3, loc_ind)
            elif item.get("is_dlc_base_locked") or (
                item.get("is_base_owned") and not item.get("key")
            ):
                locked_tip = (
                    "DLC-Only Mode is active. Base AppKey is not injected."
                    if item.get("is_dlc_base_locked")
                    else "Base game is owned legitimately on Steam and has no AppKey available."
                )
                key_item = QTableWidgetItem(
                    "[Steam Owned]"
                    if item.get("is_base_owned")
                    else "[No Key]"
                )
                key_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                key_item.setForeground(QColor("#777777"))
                key_item.setToolTip(locked_tip)
                self.keys_table.setItem(row, 1, key_item)

                toggle = MiniSwitchToggle(
                    checked=False, read_only=True, greyscaled=True
                )
                toggle.setEnabled(False)
                toggle.setToolTip(locked_tip)
                self.key_toggles[kid] = toggle
                self.keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                loc_ind = _make_local_indicator(
                    in_local=bool(item.get("in_local")), is_missing=False
                )
                self.keys_table.setCellWidget(row, 3, loc_ind)
            elif item.get("is_pure_license"):
                key_item = QTableWidgetItem("[License Only]")
                key_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                key_item.setForeground(QColor(234, 179, 8))
                f = key_item.font()
                f.setItalic(True)
                key_item.setFont(f)
                key_item.setToolTip(
                    "Entitlement is granted via AdditionalApps / DlcData. No depot files or AES key required."
                )
                self.keys_table.setItem(row, 1, key_item)

                toggle = MiniSwitchToggle(
                    checked=False, read_only=True, greyscaled=True
                )
                toggle.setEnabled(False)
                toggle.setToolTip(
                    "Entitlement is granted via AdditionalApps / DlcData. No depot files or AES key required."
                )
                self.key_toggles[kid] = toggle
                self.keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                loc_ind = _make_local_indicator(
                    in_local=False, is_missing=False
                )
                self.keys_table.setCellWidget(row, 3, loc_ind)
            else:
                raw_key = item["key"]
                is_owned_key = bool(
                    item.get("is_owned") or item.get("is_base_owned")
                )
                short_key = (
                    f"{raw_key[:15]}..."
                    if len(raw_key) > 15
                    else (raw_key or "N/A")
                )
                display_key = (
                    f"[Steam Owned]  {short_key}".strip()
                    if is_owned_key
                    else short_key
                )
                key_item = QTableWidgetItem(display_key)
                key_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                if is_owned_key:
                    key_item.setForeground(QColor(130, 130, 130))
                    f_o = key_item.font()
                    f_o.setItalic(True)
                    key_item.setFont(f_o)
                    owned_tip = (
                        f"ASSella suspects {kid} belongs to content you own on Steam. "
                        "Toggle only if you do not own it on Steam."
                    )
                    key_item.setToolTip(
                        f"{owned_tip}\n{raw_key}" if raw_key else owned_tip
                    )
                elif raw_key:
                    key_item.setToolTip(raw_key)
                self.keys_table.setItem(row, 1, key_item)

                is_cfg = item["in_config"]
                current_checked = self.desired_keys.get(kid, is_cfg)
                toggle = MiniSwitchToggle(
                    checked=current_checked, read_only=(not self.is_unlocked)
                )
                if is_owned_key:
                    toggle.setToolTip(
                        "ASSella suspects this key belongs to content you own on Steam."
                    )
                self.key_toggles[kid] = toggle

                toggle.toggled.connect(
                    partial(self._on_key_row_toggled, kid, is_owned_key)
                )
                self.keys_table.setCellWidget(row, 2, _ToggleContainer(toggle))

                is_loc = item["in_local"]
                loc_ind = _make_local_indicator(
                    in_local=is_loc, is_missing=False
                )
                self.keys_table.setCellWidget(row, 3, loc_ind)

        _set_table_height(self.keys_table, len(keys))
        self.keys_bulk_bar.setVisible(len(keys) >= 5)
        self.keys_bulk_toggle.setReadOnly(not self.is_unlocked)

    def _on_app_row_toggled(
        self, app_id: str, owned_item: bool, checked: bool
    ) -> None:
        state = self.current_state
        assoc_depots = self.app_to_depots.get(app_id, set())
        unreleased = state.get("unreleased_ids", set())
        if checked:
            has_owned_cascade = any(
                did not in unreleased
                and did in state.get("owned_ids", set())
                and (
                    (
                        did in self.depot_toggles
                        and not self.desired_depots.get(did, False)
                    )
                    or (
                        did in self.key_toggles
                        and not self.desired_keys.get(did, False)
                    )
                )
                for did in assoc_depots
            )
            if not self._confirm_owned_action(
                self.app_toggles.get(app_id), owned_item or has_owned_cascade
            ):
                self.desired_apps[app_id] = False
                self._update_sync_button_label(state)
                self._update_bulk_states(state)
                return

        self.desired_apps[app_id] = checked

        # ONE-WAY DYNAMIC CASCADE:
        # Enabling/disabling an app/DLC automatically cascades to its matching key & depots
        self._suppress_owned_confirm = True
        try:
            if checked:
                if app_id in self.key_toggles and not self.desired_keys.get(
                    app_id, False
                ):
                    key_meta = next(
                        (k for k in state["keys"] if k["id"] == app_id), None
                    )
                    if (
                        key_meta
                        and key_meta.get("key")
                        and not key_meta.get("is_dlc_base_locked")
                    ):
                        self.desired_keys[app_id] = True
                        self.key_toggles[app_id].setChecked(True, animate=True)

                for did in assoc_depots:
                    if did in unreleased:
                        continue
                    if (
                        did in self.depot_toggles
                        and not self.desired_depots.get(did, False)
                    ):
                        self.desired_depots[did] = True
                        self.depot_toggles[did].setChecked(True, animate=True)
                    if did in self.key_toggles and not self.desired_keys.get(
                        did, False
                    ):
                        d_key_meta = next(
                            (k for k in state["keys"] if k["id"] == did), None
                        )
                        if (
                            d_key_meta
                            and d_key_meta.get("key")
                            and not d_key_meta.get("is_dlc_base_locked")
                        ):
                            self.desired_keys[did] = True
                            self.key_toggles[did].setChecked(
                                True, animate=True
                            )
            else:
                if app_id in self.key_toggles and self.desired_keys.get(
                    app_id
                ):
                    self.desired_keys[app_id] = False
                    self.key_toggles[app_id].setChecked(False, animate=True)

                for did in assoc_depots:
                    if did in self.depot_toggles and self.desired_depots.get(
                        did
                    ):
                        self.desired_depots[did] = False
                        self.depot_toggles[did].setChecked(
                            False, animate=True
                        )
                    if did in self.key_toggles and self.desired_keys.get(did):
                        self.desired_keys[did] = False
                        self.key_toggles[did].setChecked(False, animate=True)
        finally:
            self._suppress_owned_confirm = False

        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        self.status_lbl.setText(
            f"App {app_id} {'enabled (cascaded to depots/keys)' if checked else 'disabled (cascaded to depots/keys)'}. Click 'Sync Changes' to apply."
        )

    def _on_depot_row_toggled(
        self, depot_id: str, is_shared: bool, owned_item: bool, checked: bool
    ) -> None:
        state = self.current_state
        if checked and not self._confirm_owned_action(
            self.depot_toggles.get(depot_id), owned_item
        ):
            self.desired_depots[depot_id] = False
            self._update_sync_button_label(state)
            self._update_bulk_states(state)
            return
        self.desired_depots[depot_id] = checked
        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        if not checked and is_shared:
            self.status_lbl.setText(
                f"Depot {depot_id} is shared with other games. It will remain protected in config."
            )
        else:
            self.status_lbl.setText(
                f"Depot {depot_id} {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply."
            )

    def _on_key_row_toggled(
        self, key_id: str, owned_item: bool, checked: bool
    ) -> None:
        state = self.current_state
        if checked and not self._confirm_owned_action(
            self.key_toggles.get(key_id), owned_item
        ):
            self.desired_keys[key_id] = False
            self._update_sync_button_label(state)
            self._update_bulk_states(state)
            return
        self.desired_keys[key_id] = checked
        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        self.status_lbl.setText(
            f"Key {key_id} {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply."
        )

    def _update_sync_button_label(self, state: Dict[str, Any]) -> None:
        """Light up Save only when something differs from the live config, and
        flag Refetch when Hubcap has keys this game is missing."""
        n_changes = sum(
            1
            for item in state["apps"]
            if item["id"] in self.desired_apps
            and self.desired_apps[item["id"]]
            != (item["id"] in state["live_apps"])
        )
        n_changes += sum(
            1
            for item in state["depots"]
            if item["id"] in self.desired_depots
            and self.desired_depots[item["id"]]
            != (item["id"] in state["live_depots"])
        )
        n_changes += sum(
            1
            for item in state["keys"]
            if item["id"] in self.desired_keys
            and self.desired_keys[item["id"]] != item["in_config"]
        )
        is_dirty = n_changes > 0

        ready = {
            str(item["id"])
            for item in (state["depots"] + state["keys"])
            if str(item.get("missing_tag") or "") == "[Key Ready]"
        }
        if self.refetch_btn.isEnabled() or not self.refetch_btn.toolTip().startswith(
            ("Daily", "API token")
        ):
            if ready:
                self.refetch_btn.setText(
                    f"Refetch keys \u00b7 {len(ready)} ready"
                )
                self.refetch_btn.setStyleSheet(self._BTN_ATTENTION_STYLE)
            else:
                self.refetch_btn.setText("Refetch keys")
                self.refetch_btn.setStyleSheet(self._BTN_BASE_STYLE)

        if self.is_dlc_setup:
            n_sel = sum(
                1
                for a in state["apps"]
                if not a.get("is_base")
                and not a.get("is_unreleased")
                and self.desired_apps.get(a["id"], False)
            )
            self.sync_btn.setText(
                f"Register {n_sel} DLC{'s' if n_sel != 1 else ''}"
            )
            self.sync_btn.setEnabled(n_sel > 0 or is_dirty)
        else:
            self.sync_btn.setText(
                f"Save {n_changes} change{'s' if n_changes != 1 else ''}"
                if is_dirty
                else "Save"
            )
            self.sync_btn.setEnabled(is_dirty)

    def _update_bulk_states(self, state: Dict[str, Any]) -> None:
        """Synchronize the checked state of the bulk Select All / None toggles."""
        self._internal_bulk_update = True
        try:
            apps = state["apps"]
            valid_apps = [
                a
                for a in apps
                if not a.get("is_unreleased")
                and not a.get("is_dlc_base_locked")
            ]
            if len(apps) >= 5:
                all_apps_on = bool(valid_apps) and all(
                    self.desired_apps.get(item["id"], False)
                    for item in valid_apps
                )
                self.apps_bulk_toggle.setChecked(all_apps_on, animate=False)

            depots = state["depots"]
            valid_depots = [
                d
                for d in depots
                if not d.get("is_missing") and not d.get("is_unreleased")
            ]
            if len(depots) >= 5:
                all_depots_on = bool(valid_depots) and all(
                    self.desired_depots.get(item["id"], False)
                    for item in valid_depots
                )
                self.depots_bulk_toggle.setChecked(
                    all_depots_on, animate=False
                )

            keys = state["keys"]
            valid_keys = [
                k
                for k in keys
                if k.get("key")
                and not k.get("is_missing")
                and not k.get("is_dlc_base_locked")
                and not k.get("is_pure_license")
                and not k.get("is_unreleased")
            ]
            if len(keys) >= 5:
                all_keys_on = bool(valid_keys) and all(
                    self.desired_keys.get(item["id"], False)
                    for item in valid_keys
                )
                self.keys_bulk_toggle.setChecked(all_keys_on, animate=False)
        finally:
            self._internal_bulk_update = False

    def _on_bulk_toggle_apps(self, checked: bool) -> None:
        if self._internal_bulk_update or not self.is_unlocked:
            return
        state = self.current_state or self._collect_state()
        app_to_depots = self.app_to_depots or self._build_app_to_depots_map(
            state["apps"], state.get("depots")
        )
        unreleased = state.get("unreleased_ids", set())
        if checked:
            needs_confirm = False
            for item in state["apps"]:
                aid = item["id"]
                if item.get("is_unreleased") or item.get("is_dlc_base_locked"):
                    continue
                if (
                    item.get("is_owned") or item.get("is_owned_base")
                ) and not self.desired_apps.get(aid, False):
                    needs_confirm = True
                    break
                for did in app_to_depots.get(aid, set()):
                    if did in unreleased or did not in state.get(
                        "owned_ids", set()
                    ):
                        continue
                    d_meta = next(
                        (d for d in state["depots"] if d["id"] == did), None
                    )
                    k_meta = next(
                        (k for k in state["keys"] if k["id"] == did), None
                    )
                    will_dep = (
                        did in self.depot_toggles
                        and not self.desired_depots.get(did, False)
                        and (not d_meta or not d_meta.get("is_missing"))
                    )
                    will_key = (
                        did in self.key_toggles
                        and not self.desired_keys.get(did, False)
                        and k_meta
                        and k_meta.get("key")
                        and not k_meta.get("is_missing")
                    )
                    if will_dep or will_key:
                        needs_confirm = True
                        break
                if needs_confirm:
                    break
            if needs_confirm and not confirm_steam_owned_override(self.dialog):
                self._set_switch_silently(self.apps_bulk_toggle, False)
                return

        self._suppress_owned_confirm = True
        try:
            for item in state["apps"]:
                aid = item["id"]
                if item.get("is_unreleased") or item.get("is_dlc_base_locked"):
                    continue
                self.desired_apps[aid] = checked
                if aid in self.app_toggles:
                    self.app_toggles[aid].setChecked(checked, animate=True)

                assoc_depots = app_to_depots.get(aid, set())
                if checked:
                    if aid in self.key_toggles and not self.desired_keys.get(
                        aid, False
                    ):
                        key_meta = next(
                            (k for k in state["keys"] if k["id"] == aid), None
                        )
                        if (
                            key_meta
                            and key_meta.get("key")
                            and not key_meta.get("is_missing")
                            and not key_meta.get("is_dlc_base_locked")
                        ):
                            self.desired_keys[aid] = True
                            self.key_toggles[aid].setChecked(
                                True, animate=True
                            )

                    for did in assoc_depots:
                        if did in unreleased:
                            continue
                        d_meta = next(
                            (d for d in state["depots"] if d["id"] == did),
                            None,
                        )
                        if (
                            did in self.depot_toggles
                            and not self.desired_depots.get(did, False)
                        ):
                            if not d_meta or not d_meta.get("is_missing"):
                                self.desired_depots[did] = True
                                self.depot_toggles[did].setChecked(
                                    True, animate=True
                                )
                        if (
                            did in self.key_toggles
                            and not self.desired_keys.get(did, False)
                        ):
                            d_key_meta = next(
                                (k for k in state["keys"] if k["id"] == did),
                                None,
                            )
                            if (
                                d_key_meta
                                and d_key_meta.get("key")
                                and not d_key_meta.get("is_missing")
                            ):
                                self.desired_keys[did] = True
                                self.key_toggles[did].setChecked(
                                    True, animate=True
                                )
                else:
                    if aid in self.key_toggles and self.desired_keys.get(aid):
                        self.desired_keys[aid] = False
                        self.key_toggles[aid].setChecked(False, animate=True)
                    for did in assoc_depots:
                        if (
                            did in self.depot_toggles
                            and self.desired_depots.get(did)
                        ):
                            self.desired_depots[did] = False
                            self.depot_toggles[did].setChecked(
                                False, animate=True
                            )
                        if did in self.key_toggles and self.desired_keys.get(
                            did
                        ):
                            self.desired_keys[did] = False
                            self.key_toggles[did].setChecked(
                                False, animate=True
                            )
        finally:
            self._suppress_owned_confirm = False

        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        self.status_lbl.setText(
            f"All AppIDs {'enabled (cascaded)' if checked else 'disabled (cascaded)'}. Click 'Sync Changes' to apply."
        )

    def _on_bulk_toggle_depots(self, checked: bool) -> None:
        if self._internal_bulk_update or not self.is_unlocked:
            return
        state = self.current_state or self._collect_state()
        if checked:
            needs_confirm = any(
                item.get("is_owned")
                and not item.get("is_unreleased")
                and not item.get("is_missing")
                and not self.desired_depots.get(item["id"], False)
                for item in state["depots"]
            )
            if needs_confirm and not confirm_steam_owned_override(self.dialog):
                self._set_switch_silently(self.depots_bulk_toggle, False)
                return

        self._suppress_owned_confirm = True
        try:
            for item in state["depots"]:
                if item.get("is_unreleased") or item.get("is_missing"):
                    continue
                did = item["id"]
                self.desired_depots[did] = checked
                if did in self.depot_toggles:
                    self.depot_toggles[did].setChecked(checked, animate=True)
        finally:
            self._suppress_owned_confirm = False

        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        self.status_lbl.setText(
            f"All Depots {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply."
        )

    def _on_bulk_toggle_keys(self, checked: bool) -> None:
        if self._internal_bulk_update or not self.is_unlocked:
            return
        state = self.current_state or self._collect_state()
        if checked:
            needs_confirm = any(
                (item.get("is_owned") or item.get("is_base_owned"))
                and item.get("key")
                and not item.get("is_missing")
                and not item.get("is_unreleased")
                and not item.get("is_dlc_base_locked")
                and not item.get("is_pure_license")
                and not self.desired_keys.get(item["id"], False)
                for item in state["keys"]
            )
            if needs_confirm and not confirm_steam_owned_override(self.dialog):
                self._set_switch_silently(self.keys_bulk_toggle, False)
                return

        self._suppress_owned_confirm = True
        try:
            for item in state["keys"]:
                kid = item["id"]
                if (
                    item.get("is_unreleased")
                    or item.get("is_dlc_base_locked")
                    or item.get("is_pure_license")
                    or not item.get("key")
                    or item.get("is_missing")
                ):
                    continue
                self.desired_keys[kid] = checked
                if kid in self.key_toggles:
                    self.key_toggles[kid].setChecked(checked, animate=True)
        finally:
            self._suppress_owned_confirm = False

        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        self.status_lbl.setText(
            f"All Keys {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply."
        )

    def _on_bulk_toggle_all(self, checked: bool) -> None:
        if self._internal_bulk_update or not self.is_unlocked:
            return
        state = self.current_state or self._collect_state()
        if checked:
            needs_confirm = (
                any(
                    (item.get("is_owned") or item.get("is_owned_base"))
                    and not item.get("is_unreleased")
                    and not item.get("is_dlc_base_locked")
                    and not self.desired_apps.get(item["id"], False)
                    for item in state["apps"]
                )
                or any(
                    item.get("is_owned")
                    and not item.get("is_unreleased")
                    and not item.get("is_missing")
                    and not self.desired_depots.get(item["id"], False)
                    for item in state["depots"]
                )
                or any(
                    (item.get("is_owned") or item.get("is_base_owned"))
                    and item.get("key")
                    and not item.get("is_missing")
                    and not item.get("is_unreleased")
                    and not item.get("is_dlc_base_locked")
                    and not item.get("is_pure_license")
                    and not self.desired_keys.get(item["id"], False)
                    for item in state["keys"]
                )
            )
            if needs_confirm and not confirm_steam_owned_override(self.dialog):
                return

        self._suppress_owned_confirm = True
        try:
            for item in state["apps"]:
                aid = item["id"]
                if item.get("is_unreleased") or item.get("is_dlc_base_locked"):
                    continue
                self.desired_apps[aid] = checked
                if aid in self.app_toggles:
                    self.app_toggles[aid].setChecked(checked, animate=True)

            for item in state["depots"]:
                if item.get("is_unreleased") or item.get("is_missing"):
                    continue
                did = item["id"]
                self.desired_depots[did] = checked
                if did in self.depot_toggles:
                    self.depot_toggles[did].setChecked(checked, animate=True)

            for item in state["keys"]:
                kid = item["id"]
                if (
                    item.get("is_unreleased")
                    or item.get("is_dlc_base_locked")
                    or item.get("is_pure_license")
                    or not item.get("key")
                    or item.get("is_missing")
                ):
                    continue
                self.desired_keys[kid] = checked
                if kid in self.key_toggles:
                    self.key_toggles[kid].setChecked(checked, animate=True)
        finally:
            self._suppress_owned_confirm = False

        self._update_sync_button_label(state)
        self._update_bulk_states(state)
        self.status_lbl.setText(
            f"All sections {'enabled' if checked else 'disabled'}. Click 'Sync Changes' to apply."
        )