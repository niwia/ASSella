"""Pre-flight check that the selected depots actually contain the game's
launch executable."""

from pathlib import Path

from PyQt6.QtCore import Qt

from ui.dialogs.themed_message_box import ThemedMessageBox

from ui.dialogs.depot_selection import executable_probe


class ExecutableVerifyMixin:
    """Pre-flight check that the selection contains the game's launch executable."""

    def _verify_executable_selection(self, selected_depots: list) -> bool:
        """
        Feature B Pre-flight guard:
        Verify that at least one selected depot contains the primary launch executable.
        If missing, prompts user with option to automatically select the depot and continue.
        """
        if not self.app_id or not selected_depots:
            return True

        appid_str = str(self.app_id).strip()

        # 1. Try resolving launch executable from game_data, then 2. from local Steam appinfo.vdf
        exe_name = executable_probe.launch_executable(appid_str, getattr(self, "game_data", None))
        if not exe_name:
            return True

        exe_filename = executable_probe.executable_filename(exe_name)
        if not exe_filename:
            return True

        # 3. Check which depot holds this executable: the fetched bundle, then loose manifests
        depot_with_exe = executable_probe.depot_with_executable(appid_str, exe_filename)

        # If a depot containing the executable is found, check if it's selected
        if depot_with_exe and str(depot_with_exe) not in [str(d) for d in selected_depots]:
            reply = ThemedMessageBox.warning(
                self,
                "Missing Game Executable",
                f"The launch executable '{Path(exe_name).name}' was detected in Depot {depot_with_exe}, "
                f"which is currently NOT selected.\n\n"
                f"Without this depot, Steam will fail to launch the game ('Missing Executable').\n\n"
                f"Would you like to select Depot {depot_with_exe} and proceed?",
                ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.Ignore | ThemedMessageBox.StandardButton.Cancel,
                ThemedMessageBox.StandardButton.Yes,
            )
            if reply == ThemedMessageBox.StandardButton.Yes:
                self.table_widget.blockSignals(True)
                for i in range(self.table_widget.rowCount()):
                    id_item = self.table_widget.item(i, 0)
                    if id_item and str(id_item.data(Qt.ItemDataRole.UserRole)) == str(depot_with_exe):
                        id_item.setCheckState(Qt.CheckState.Checked)
                        break
                self.table_widget.blockSignals(False)
                self._update_table_headers()
                return True
            elif reply == ThemedMessageBox.StandardButton.Ignore:
                return True
            else:
                return False

        return True
