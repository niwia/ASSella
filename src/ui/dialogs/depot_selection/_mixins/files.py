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
            if role in ("missing", "expander"):
                continue
            return str(id_item.data(Qt.ItemDataRole.UserRole))
        return None

    def _on_select_files_clicked(self):
        target_depot = self._first_checked_depot_id()
        if not target_depot:
            ThemedMessageBox.warning(self, "Warning", "Please select at least one depot first.")
            return

        try:
            dump = prepare_manifest_dump(self.app_id, target_depot)
        except ManifestDumpError as e:
            ThemedMessageBox.critical(self, "Error", str(e))
            return

        runner = ManifestDumpRunner(self, dump)
        runner.finished.connect(lambda ok, message: self._on_dump_finished(dump, ok, message))
        runner.start()

    def _on_dump_finished(self, dump, ok, message):
        """Open the file-selection tree over a listing that was written."""
        if not ok:
            ThemedMessageBox.critical(self, "Error", message)
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
