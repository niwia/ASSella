"""Browse Files: dumping a depot's manifest and opening the file-selection
tree."""

from PyQt6.QtCore import Qt

from ui.dialogs.themed_message_box import ThemedMessageBox

from ui.dialogs.depot_selection.dump_runner import ManifestDumpRunner
from ui.dialogs.depot_selection.manifest_dump import (
    ManifestDumpError,
    discard_keys_file,
    prepare_manifest_dump,
)


class FileCustomisationMixin:
    """Browse Files: manifest dump and the file-selection tree."""

    def _first_checked_depot_id(self):
        """The first checked depot that can actually be downloaded."""
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None or id_item.checkState() != Qt.CheckState.Checked:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander", "unreleased"):
                continue
            return str(id_item.data(Qt.ItemDataRole.UserRole))
        return None

    def _checked_downloadable_depots(self):
        """All checked depots that can actually be downloaded: [(depot_id, name), ...]"""
        result = []
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None or id_item.checkState() != Qt.CheckState.Checked:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander", "unreleased"):
                continue
            did = str(id_item.data(Qt.ItemDataRole.UserRole) or id_item.text()).strip()
            name_item = self.table_widget.item(i, 1)
            name = name_item.text().strip() if name_item else f"Depot {did}"
            if did and did not in [d[0] for d in result]:
                result.append((did, name))
        return result

    def _on_select_files_clicked(self):
        checked = self._checked_downloadable_depots()
        if not checked:
            ThemedMessageBox.warning(self, "Warning", "Please select at least one depot first.")
            return

        overrides = getattr(self, "_manifest_overrides", {})

        if len(checked) == 1:
            target_depot = checked[0][0]
        else:
            # If multiple depots are checked, allow the user to select which depot to browse
            items = []
            default_idx = 0
            for idx, (did, dname) in enumerate(checked):
                mid = overrides.get(did)
                if not mid and hasattr(self, "depots") and did in self.depots:
                    d_info = self.depots[did]
                    if isinstance(d_info, dict):
                        mid = d_info.get("manifest_id")
                label = f"{did} - {dname}"
                if mid:
                    label += f" [Manifest: {mid}]"
                items.append(label)
                if did in overrides:
                    default_idx = idx

            from ui.dialogs.themed_input_dialog import ThemedInputDialog
            chosen_item, ok = ThemedInputDialog.getItem(
                self,
                title="Browse Files",
                label="Multiple depots are checked. Select which depot to browse:",
                items=items,
                current=default_idx,
            )
            if not ok or not chosen_item:
                return
            target_depot = chosen_item.split(" - ", 1)[0].strip()

        target_manifest_id = overrides.get(str(target_depot))
        if not target_manifest_id and hasattr(self, "depots") and str(target_depot) in self.depots:
            d_info = self.depots[str(target_depot)]
            if isinstance(d_info, dict):
                target_manifest_id = d_info.get("manifest_id")

        runner = ManifestDumpRunner(
            self,
            app_id=str(self.app_id),
            depot_id=str(target_depot),
            manifest_id=str(target_manifest_id) if target_manifest_id else None,
        )
        runner.finished.connect(lambda ok, message, r=runner: self._on_dump_finished(r.dump, ok, message))
        runner.start()

    def _on_dump_finished(self, dump, ok, message):
        """Open the file-selection tree over a listing that was written."""
        if not ok or not dump:
            ThemedMessageBox.critical(self, "Error", message or "Failed to prepare manifest.")
            return

        from ui.dialogs.fileselection import FileSelectionDialog
        sel_dialog = FileSelectionDialog(
            app_id=dump.app_id,
            depot_id=dump.depot_id,
            manifest_txt_path=dump.txt_path,
            parent=self,
            manifest_file=dump.manifest_file,
            keys_path=dump.keys_path,
            manifest_id=dump.manifest_id,
        )
        try:
            if sel_dialog.exec():
                self.selected_files = sel_dialog.selected_files
                ThemedMessageBox.information(
                    self,
                    "Selection Confirmed",
                    f"Selected {len(self.selected_files)} file(s) for custom download.\nPress OK at the bottom to start installing."
                )
        finally:
            discard_keys_file(dump.keys_path)
