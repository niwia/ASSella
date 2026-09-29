import os
import re
import subprocess
import logging
from pathlib import Path
from typing import Optional, List

from PyQt6.QtCore import Qt, pyqtSignal, QThread, QUrl
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QIcon, QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTreeView,
    QPushButton,
    QLabel,
    QLineEdit,
    QHeaderView,
    QApplication,
    QMessageBox,
    QFileDialog,
    QProgressDialog,
)

logger = logging.getLogger(__name__)


class DirectDownloadThread(QThread):
    progress_signal = pyqtSignal(str, float)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, cmd, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.process = None
        self._is_cancelled = False

    def run(self):
        try:
            self.process = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in iter(self.process.stdout.readline, ""):
                if self._is_cancelled:
                    break
                line = line.strip()
                if not line:
                    continue
                pct_match = re.search(r"(\d+(?:\.\d+)?)%", line)
                pct = float(pct_match.group(1)) if pct_match else -1.0
                self.progress_signal.emit(line, pct)

            self.process.stdout.close()
            self.process.wait()

            if self._is_cancelled:
                self.finished_signal.emit(False, "Download cancelled by user.")
            elif self.process.returncode == 0:
                self.finished_signal.emit(True, "")
            else:
                self.finished_signal.emit(False, f"Download failed with exit code {self.process.returncode}")
        except Exception as ex:
            self.finished_signal.emit(False, str(ex))

    def cancel(self):
        self._is_cancelled = True
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass


class FileSelectionDialog(QDialog):
    def __init__(
        self,
        app_id,
        depot_id,
        manifest_txt_path,
        parent=None,
        manifest_file=None,
        keys_path=None,
        manifest_id=None,
    ):
        super().__init__(parent)
        self.app_id = str(app_id)
        self.depot_id = str(depot_id)
        self.manifest_txt_path = manifest_txt_path
        self.manifest_file = manifest_file
        self.keys_path = keys_path
        self.manifest_id = manifest_id
        self.setWindowTitle(f"Select Files to Download - Depot {depot_id}")
        self.resize(700, 560)
        self.selected_files = []
        self.all_files = []

        # Theme styling (inherit from parent)
        self.accent_color = "#a1c9fd"
        self.background_color = "#111318"
        if parent:
            if hasattr(parent, "accent_color"):
                self.accent_color = parent.accent_color
            if hasattr(parent, "background_color"):
                self.background_color = parent.background_color

        self.setStyleSheet(
            f"""
            QDialog {{
                background-color: {self.background_color};
                color: #FFFFFF;
            }}
            QLabel {{
                color: #FFFFFF;
                font-size: 9.5pt;
            }}
            QLineEdit {{
                background-color: rgba(255, 255, 255, 0.039);
                border: 1px solid rgba(255, 255, 255, 0.078);
                border-radius: 4px;
                padding: 4px 8px;
                color: #FFFFFF;
                font-size: 9pt;
            }}
            QLineEdit:focus {{
                border-color: {self.accent_color};
            }}
            QTreeView {{
                background-color: rgba(255, 255, 255, 5);
                border: 1px solid rgba(255, 255, 255, 0.059);
                border-radius: 6px;
                color: #FFFFFF;
            }}
            QTreeView::item:hover {{
                background-color: rgba(255, 255, 255, 0.039);
            }}
            QTreeView::item:selected {{
                background-color: rgba(255, 255, 255, 0.059);
                color: #FFFFFF;
            }}
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.039);
                border: 1px solid rgba(255, 255, 255, 0.078);
                border-radius: 4px;
                padding: 5px 12px;
                color: #FFFFFF;
                font-size: 9pt;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.059);
                border-color: {self.accent_color};
            }}
            QPushButton:pressed {{
                background-color: rgba(255, 255, 255, 5);
            }}
            QHeaderView::section {{
                background-color: #1a1a1a;
                color: #888888;
                padding: 4px;
                border: none;
                font-size: 8.5pt;
                font-weight: bold;
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Header info
        info_label = QLabel(
            "Expand folders to customize your download. Unchecked files and folders will be skipped."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Search / Filter Row
        search_layout = QHBoxLayout()
        search_layout.setSpacing(6)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter files/folders by name...")
        self.search_input.textChanged.connect(self._filter_tree)
        search_layout.addWidget(self.search_input)
        layout.addLayout(search_layout)

        # Tree View
        self.tree_view = QTreeView()
        self.tree_view.setHeaderHidden(False)
        self.tree_view.setAlternatingRowColors(True)
        self.tree_view.setAnimated(True)

        self.model = QStandardItemModel(0, 2, self.tree_view)
        self.model.setHeaderData(0, Qt.Orientation.Horizontal, "File/Folder Path")
        self.model.setHeaderData(1, Qt.Orientation.Horizontal, "Size")
        self.tree_view.setModel(self.model)

        self.tree_view.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree_view.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self.tree_view)

        # Bottom stats
        self.stats_label = QLabel("Loading file structure...")
        layout.addWidget(self.stats_label)

        # Destination Folder Row
        dest_layout = QHBoxLayout()
        dest_layout.setSpacing(6)
        dest_lbl = QLabel("Download To:")
        dest_lbl.setStyleSheet("font-weight: bold; color: rgba(255, 255, 255, 0.9);")
        dest_layout.addWidget(dest_lbl)

        self.dest_edit = QLineEdit()
        default_dir = os.path.expanduser("~/Downloads")
        self.dest_edit.setText(default_dir)
        self.dest_edit.setPlaceholderText("Select folder to download chosen file(s)...")
        dest_layout.addWidget(self.dest_edit, 1)

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._browse_destination)
        dest_layout.addWidget(self.browse_btn)

        layout.addLayout(dest_layout)

        # Actions Row
        actions_layout = QHBoxLayout()
        
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(lambda: self._set_all_checkstate(Qt.CheckState.Checked))
        actions_layout.addWidget(self.select_all_btn)

        self.deselect_all_btn = QPushButton("Deselect All")
        self.deselect_all_btn.clicked.connect(lambda: self._set_all_checkstate(Qt.CheckState.Unchecked))
        actions_layout.addWidget(self.deselect_all_btn)

        actions_layout.addStretch()

        self.direct_download_btn = QPushButton("Download Selected")
        self.direct_download_btn.setToolTip("Directly download the selected files into the chosen folder without queuing other depots.")
        self.direct_download_btn.setStyleSheet(f"background-color: {self.accent_color}; color: #000000; font-weight: bold; border: none; padding: 5px 14px;")
        self.direct_download_btn.clicked.connect(self._on_direct_download_clicked)
        actions_layout.addWidget(self.direct_download_btn)

        self.ok_btn = QPushButton("Confirm Selection")
        self.ok_btn.setToolTip("Save this selection to download during the regular game installation queue.")
        self.ok_btn.clicked.connect(self._on_confirm)
        actions_layout.addWidget(self.ok_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        actions_layout.addWidget(self.cancel_btn)

        layout.addLayout(actions_layout)

        # Load data
        self._parse_manifest_and_build_tree()

    def _parse_manifest_and_build_tree(self):
        if not os.path.exists(self.manifest_txt_path):
            self.stats_label.setText("Error: Manifest dump file not found.")
            return

        # Parse text manifest
        self.all_files = []
        start_parsing = False
        with open(self.manifest_txt_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_stripped = line.strip()
                if not line_stripped:
                    continue
                if "Size Chunks File SHA" in line_stripped:
                    start_parsing = True
                    continue
                if not start_parsing:
                    continue

                parts = line_stripped.split(None, 4)
                if len(parts) >= 5:
                    try:
                        size = int(parts[0])
                        chunks = int(parts[1])
                        flags = parts[3]
                        name = parts[4]
                        # Directory if flag is 40 or size/chunks are 0
                        is_dir = (flags == "40" or (size == 0 and chunks == 0))
                        self.all_files.append({
                            "size": size,
                            "name": name,
                            "is_dir": is_dir
                        })
                    except ValueError:
                        pass

        # Sort files to construct folders hierarchically
        self.all_files.sort(key=lambda x: x["name"])

        # Populate Standard Item Model
        self.model.blockSignals(True)
        root = self.model.invisibleRootItem()
        dirs_dict = {"": root}

        for file_entry in self.all_files:
            full_path = file_entry["name"]
            size = file_entry["size"]
            is_dir = file_entry["is_dir"]

            if is_dir:
                # Add folder
                self._get_or_create_dir_item(full_path, dirs_dict)
            else:
                # Add file
                dir_name, file_name = os.path.split(full_path)
                parent_item = self._get_or_create_dir_item(dir_name, dirs_dict)

                name_item = QStandardItem(file_name)
                name_item.setCheckable(True)
                name_item.setCheckState(Qt.CheckState.Checked)
                name_item.setData(full_path, Qt.ItemDataRole.UserRole)
                name_item.setData(size, Qt.ItemDataRole.UserRole + 1)
                name_item.setData(False, Qt.ItemDataRole.UserRole + 2) # is_dir = False

                size_item = QStandardItem(self._format_size(size))
                size_item.setEditable(False)

                parent_item.appendRow([name_item, size_item])

        self.model.blockSignals(False)
        self.model.itemChanged.connect(self._on_item_changed)
        self._update_stats()

    def _get_or_create_dir_item(self, path, dirs_dict):
        # Normalize slashes
        path = path.replace("\\", "/")
        if path in dirs_dict:
            return dirs_dict[path]

        # Get parent path
        parts = path.split("/")
        if len(parts) == 1:
            parent_path = ""
            dir_name = parts[0]
        else:
            parent_path = "/".join(parts[:-1])
            dir_name = parts[-1]

        parent_item = self._get_or_create_dir_item(parent_path, dirs_dict)

        # Create folder standard items
        name_item = QStandardItem(dir_name)
        name_item.setCheckable(True)
        name_item.setCheckState(Qt.CheckState.Checked)
        name_item.setData(path, Qt.ItemDataRole.UserRole)
        name_item.setData(0, Qt.ItemDataRole.UserRole + 1) # Size 0 for folders
        name_item.setData(True, Qt.ItemDataRole.UserRole + 2) # is_dir = True

        size_item = QStandardItem("")
        size_item.setEditable(False)

        parent_item.appendRow([name_item, size_item])
        dirs_dict[path] = name_item
        return name_item

    def _on_item_changed(self, item):
        self.model.blockSignals(True)
        state = item.checkState()
        is_dir = item.data(Qt.ItemDataRole.UserRole + 2)

        # If user checked/unchecked a folder, recursively apply checking to children
        if is_dir:
            self._set_children_checkstate(item, state)

        # Update parent states upward
        self._update_parent_checkstate(item.parent())

        self.model.blockSignals(False)
        self._update_stats()

    def _set_children_checkstate(self, parent_item, state):
        for row in range(parent_item.rowCount()):
            child = parent_item.child(row, 0)
            if child:
                child.setCheckState(state)
                if child.data(Qt.ItemDataRole.UserRole + 2): # is_dir
                    self._set_children_checkstate(child, state)

    def _update_parent_checkstate(self, parent_item):
        if not parent_item:
            return

        checked_count = 0
        partially_checked = False
        child_count = parent_item.rowCount()

        for row in range(child_count):
            child = parent_item.child(row, 0)
            if child:
                c_state = child.checkState()
                if c_state == Qt.CheckState.Checked:
                    checked_count += 1
                elif c_state == Qt.CheckState.PartiallyChecked:
                    partially_checked = True

        if checked_count == child_count:
            parent_item.setCheckState(Qt.CheckState.Checked)
        elif checked_count > 0 or partially_checked:
            parent_item.setCheckState(Qt.CheckState.PartiallyChecked)
        else:
            parent_item.setCheckState(Qt.CheckState.Unchecked)

        # Traverse upwards
        self._update_parent_checkstate(parent_item.parent())

    def _set_all_checkstate(self, state):
        self.model.blockSignals(True)
        root = self.model.invisibleRootItem()
        for row in range(root.rowCount()):
            item = root.child(row, 0)
            if item:
                item.setCheckState(state)
                self._set_children_checkstate(item, state)
        self.model.blockSignals(False)
        self._update_stats()

    def _update_stats(self):
        tot_sz = 0
        tot = 0
        sel = 0
        sel_sz = 0

        # Traverse model to collect checks
        root = self.model.invisibleRootItem()

        def _traverse(item):
            nonlocal tot, sel, tot_sz, sel_sz
            for row in range(item.rowCount()):
                child = item.child(row, 0)
                if child:
                    is_dir = child.data(Qt.ItemDataRole.UserRole + 2)
                    if not is_dir:
                        size = child.data(Qt.ItemDataRole.UserRole + 1) or 0
                        tot += 1
                        tot_sz += size
                        if child.checkState() == Qt.CheckState.Checked:
                            sel += 1
                            sel_sz += size
                    else:
                        _traverse(child)

        _traverse(root)

        tot_gb = tot_sz / (1024**3)
        sel_gb = sel_sz / (1024**3)
        self.stats_label.setText(
            f"Selected: <b>{sel}</b> of <b>{tot}</b> files "
            f"({sel_gb:.2f} GB chosen / {tot_gb:.2f} GB total)"
        )

    def _filter_tree(self, text):
        # Very basic filtering
        self.tree_view.keyboardSearch(text)

    def _browse_destination(self):
        cur = self.dest_edit.text() or os.path.expanduser("~/Downloads")
        chosen = QFileDialog.getExistingDirectory(self, "Select Download Folder", cur)
        if chosen:
            self.dest_edit.setText(chosen)

    def _collect_selected_files(self):
        self.selected_files = []
        root = self.model.invisibleRootItem()

        def _collect(item):
            for row in range(item.rowCount()):
                child = item.child(row, 0)
                if child:
                    is_dir = child.data(Qt.ItemDataRole.UserRole + 2)
                    if not is_dir:
                        if child.checkState() == Qt.CheckState.Checked:
                            self.selected_files.append(child.data(Qt.ItemDataRole.UserRole))
                    else:
                        _collect(child)

        _collect(root)
        return self.selected_files

    def _on_confirm(self):
        selected = self._collect_selected_files()
        if not selected:
            QMessageBox.warning(
                self, "Warning", "Please select at least one file to download."
            )
            return

        self.accept()

    def _on_direct_download_clicked(self):
        selected = self._collect_selected_files()
        if not selected:
            QMessageBox.warning(self, "No Files Selected", "Please select at least one file to download.")
            return

        dest_folder = self.dest_edit.text().strip()
        if not dest_folder:
            QMessageBox.warning(self, "No Destination", "Please choose a destination folder.")
            return

        try:
            os.makedirs(dest_folder, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Failed to create destination folder:\n{e}")
            return

        # Resolve manifest and keys if not passed explicitly
        temp_dir = os.path.dirname(self.manifest_txt_path)
        manifest_file = self.manifest_file
        keys_path = self.keys_path
        manifest_id = self.manifest_id

        if not manifest_id:
            stem = os.path.basename(self.manifest_txt_path).replace(".txt", "")
            manifest_id = stem.split("_")[-1]

        if not manifest_file:
            cand = list(Path(temp_dir).glob(f"{self.depot_id}_*.manifest")) or list(Path(temp_dir).glob("*.manifest"))
            if cand:
                manifest_file = str(cand[0])

        if not keys_path:
            cand_keys = os.path.join(temp_dir, "depot.keys")
            if os.path.exists(cand_keys):
                keys_path = cand_keys

        if not manifest_file or not os.path.exists(manifest_file):
            QMessageBox.critical(self, "Error", f"Could not find depot manifest file in {temp_dir}.")
            return
        if not keys_path or not os.path.exists(keys_path):
            QMessageBox.critical(self, "Error", f"Could not find depot decryption keys file in {temp_dir}.")
            return

        # Write selective filelist
        fl_path = os.path.join(temp_dir, f"direct_filelist_{self.depot_id}.txt")
        try:
            with open(fl_path, "w", encoding="utf-8") as fl:
                for fp in selected:
                    fl.write(fp.replace("\\", "/") + "\n")
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Failed to write filelist: {e}")
            return

        from utils.helpers import get_dotnet_path, resource_path
        dotnet_path = get_dotnet_path()
        dll_path = resource_path(os.path.join("deps", "DepotDownloader.dll"))

        cmd = [
            dotnet_path,
            dll_path,
            "-app", str(self.app_id),
            "-depot", str(self.depot_id),
            "-manifest", str(manifest_id),
            "-manifestfile", str(manifest_file),
            "-depotkeys", str(keys_path),
            "-filelist", fl_path,
            "-dir", dest_folder,
            "-validate",
            "-max-downloads", "8"
        ]

        progress_dialog = QProgressDialog(f"Downloading {len(selected)} file(s)...", "Cancel", 0, 100, self)
        progress_dialog.setWindowTitle("Downloading Selected Files")
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setValue(0)
        progress_dialog.show()

        thread = DirectDownloadThread(cmd, self)

        def on_progress(msg, pct):
            if pct >= 0:
                progress_dialog.setValue(int(pct))
            progress_dialog.setLabelText(msg[:80] + "..." if len(msg) > 80 else msg)

        def on_finished(success, err):
            progress_dialog.close()
            if os.path.exists(fl_path):
                try:
                    os.remove(fl_path)
                except OSError:
                    pass

            if success:
                box = QMessageBox(self)
                box.setWindowTitle("Download Complete")
                box.setText(f"Successfully downloaded {len(selected)} file(s) to:\n{dest_folder}")
                open_btn = box.addButton("Open Folder", QMessageBox.ButtonRole.ActionRole)
                box.addButton(QMessageBox.StandardButton.Ok)
                box.exec()
                if box.clickedButton() == open_btn:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(dest_folder))
            else:
                if "cancelled" in err.lower():
                    QMessageBox.information(self, "Cancelled", "Direct download was cancelled.")
                else:
                    QMessageBox.critical(self, "Download Error", f"Download failed:\n{err}")

        thread.progress_signal.connect(on_progress)
        thread.finished_signal.connect(on_finished)
        progress_dialog.canceled.connect(thread.cancel)
        thread.start()

    @staticmethod
    def _format_size(size_bytes):
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024**2:
            return f"{(size_bytes / 1024):.1f} KB"
        if size_bytes < 1024**3:
            return f"{(size_bytes / 1024**2):.1f} MB"
        return f"{(size_bytes / 1024**3):.2f} GB"
