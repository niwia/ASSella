"""Registration of the checked depots / DLC entitlements into AT0-M mode."""

import logging

logger = logging.getLogger(__name__)


class MigrationMixin:
    """Move the game to AT0-M (Steam Native) plugin mode."""

    def _handle_move_to_atom_clicked(self):
        """Registers checked depots / DLC entitlements into SLSsteam AT0-M plugin mode."""
        selected_depots = self.get_selected_depots()
        checked_dlcs = self.get_selected_dlc_data()

        if not selected_depots and not checked_dlcs:
            from ui.dialogs.themed_message_box import ThemedMessageBox

            ThemedMessageBox.warning(
                self,
                "Nothing Selected",
                "Please select at least one depot or DLC to register into AT0-M mode.",
            )
            return

        is_dlc_only = bool(getattr(self, "_dlc_only_mode", False))

        from ui.dialogs.themed_message_box import ThemedMessageBox
        from utils.plugin_games import can_use_atom_mode, migrate_game_to_atom

        can_atom, atom_err = can_use_atom_mode()
        if not can_atom:
            ThemedMessageBox.warning(
                self,
                "AT0-M Setup Incomplete",
                f"Cannot transition to AT0-M mode:\n\n{atom_err}\n\nPlease check your AT0-M and SLSsteam configuration in Settings.",
            )
            return

        reply = ThemedMessageBox.question(
            self,
            "Move to AT0-M (Steam Native)",
            f"Are you sure you want to register '{self.game_name}' to AT0-M mode?\n\n"
            f"• Mode: {'DLC-Only (Only DLC AppIDs injected)' if is_dlc_only else 'Full Game Install'}\n"
            f"• Depots / DLCs: {len(selected_depots)} depot(s), {len(checked_dlcs)} DLC entitlement(s)\n"
            "• SLSsteam config.yaml will be updated and reloaded.\n\n"
            "Proceed with transition?",
            ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
            ThemedMessageBox.StandardButton.Yes,
        )
        if reply != ThemedMessageBox.StandardButton.Yes:
            return

        try:
            from utils.yaml.dlc import add_dlc_data_batch
            from utils.yaml_config_manager import get_user_config_path

            config_path = get_user_config_path()

            game_data = {
                "appid": str(self.app_id),
                "game_name": self.game_name,
                "selected_depots": selected_depots,
                "depots": self.depots or {},
                "is_dlc_only": is_dlc_only,
            }

            res = migrate_game_to_atom(
                game_data,
                auto_revert_goldberg=True,
                auto_revert_eos=True,
            )

            if not res.get("success"):
                ThemedMessageBox.critical(
                    self,
                    "AT0-M Transition Failed",
                    f"Failed to write the AT0-M configuration for '{self.game_name}':\n\n"
                    f"{res.get('error', 'Unknown error')}\n\n"
                    "Nothing was changed. See the log for details.",
                )
                return

            # If DLC data was selected, ensure DlcData is also injected
            if checked_dlcs and config_path and config_path.exists():
                add_dlc_data_batch(config_path, self.app_id, checked_dlcs)

            if is_dlc_only:
                from utils.settings import get_settings

                get_settings().setValue(f"dlc_only_mode/{self.app_id}", True)

            self._atom_registered = True
            self._config_written = True

            ThemedMessageBox.information(
                self,
                "AT0-M Registered",
                f"✓ Successfully transitioned '{self.game_name}' to AT0-M mode!\n\n"
                "Please restart Steam now so Steam client can adopt the game files and update your library.\n\n"
                "After restarting, select your desired branch (Public/Beta/etc.) in Steam's library view.",
            )
            super().accept()
        except Exception as exc:
            logger.error(f"[DepotSelectionDialog] Could not move to AT0-M: {exc}", exc_info=True)
            ThemedMessageBox.critical(
                self,
                "AT0-M Transition Failed",
                f"Failed to register AT0-M configuration:\n\n{exc}",
            )
