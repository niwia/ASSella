import os
import re
import subprocess
import logging
from pathlib import Path
from typing import Optional, List

from PyQt6.QtCore import QMimeDatabase, QSize, QSortFilterProxyModel, Qt, pyqtSignal, QThread, QUrl
from PyQt6.QtGui import QColor, QStandardItemModel, QStandardItem, QIcon, QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QFileIconProvider,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QTreeView,
    QPushButton,
    QLabel,
    QLineEdit,
    QHeaderView,
    QApplication,
)
from ui.dialogs.themed_file_dialog import ThemedFileDialog
from ui.dialogs.themed_message_box import ThemedMessageBox
from ui.dialogs.themed_progress_dialog import ThemedProgressDialog
from ui.dialogs.themed_dialog import install_themed_frame

logger = logging.getLogger(__name__)


class DirectDownloadThread(QThread):
    progress_signal = pyqtSignal(str, float)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, cmd, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.process = None
        self._is_cancelled = False
        self._proxy_mgr = None

    def run(self):
        try:
            cmd = list(self.cmd)
            from utils.kuroko import resolve_stealth_proxy_url
            proxy_url, self._proxy_mgr = resolve_stealth_proxy_url()
            if proxy_url:
                cmd.extend(["-proxy", proxy_url])

            from utils.helpers import get_dotnet_env
            env = get_dotnet_env()
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
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
        finally:
            mgr, self._proxy_mgr = self._proxy_mgr, None
            if mgr is not None:
                try:
                    mgr.stop()
                except Exception as e:
                    logger.debug(f"[FileSelection] Error stopping stealth proxy manager: {e}")

    def cancel(self):
        self._is_cancelled = True
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass
        mgr, self._proxy_mgr = self._proxy_mgr, None
        if mgr is not None:
            try:
                mgr.stop()
            except Exception as e:
                logger.debug(f"[FileSelection] Error stopping stealth proxy manager on cancel: {e}")


_DIR_ROLE = Qt.ItemDataRole.UserRole + 2
_SIZE_ROLE = Qt.ItemDataRole.UserRole + 1


class _FileTreeProxy(QSortFilterProxyModel):
    """Folders first, names case-insensitive, sizes numeric; filtering keeps
    the parents of every match so results stay in their folders."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterKeyColumn(0)

    def lessThan(self, left, right):  # noqa: N802
        src = self.sourceModel()
        l0, r0 = left.siblingAtColumn(0), right.siblingAtColumn(0)
        l_dir, r_dir = bool(src.data(l0, _DIR_ROLE)), bool(src.data(r0, _DIR_ROLE))
        if l_dir != r_dir:
            # Folders above files whichever way the column is sorted.
            return l_dir if self.sortOrder() == Qt.SortOrder.AscendingOrder else r_dir
        if left.column() == 1:
            return (src.data(l0, _SIZE_ROLE) or 0) < (src.data(r0, _SIZE_ROLE) or 0)
        return str(src.data(l0) or "").lower() < str(src.data(r0) or "").lower()


class _FileIcons:
    """Theme icons by file type (falls back to Qt's generic file / folder icons)."""

    def __init__(self):
        self._db = QMimeDatabase()
        self._provider = QFileIconProvider()
        self._cache = {}
        self.folder = QIcon.fromTheme("folder", self._provider.icon(QFileIconProvider.IconType.Folder))
        self._file = self._provider.icon(QFileIconProvider.IconType.File)

    def for_file(self, name: str) -> QIcon:
        ext = os.path.splitext(name)[1].lower()
        icon = self._cache.get(ext)
        if icon is None:
            mime = self._db.mimeTypeForFile(name, QMimeDatabase.MatchMode.MatchExtension)
            icon = QIcon.fromTheme(mime.iconName())
            if icon.isNull():
                icon = QIcon.fromTheme(mime.genericIconName(), self._file)
            self._cache[ext] = icon
        return icon


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
        self.setWindowTitle(f"Browse Files - Depot {depot_id}")
        self.resize(760, 580)
        self.setMinimumSize(560, 420)
        self.selected_files = []
        self.all_files = []
        self._icons = _FileIcons()

        # Theme styling (inherit from parent)
        self.accent_color = "#a1c9fd"
        self.background_color = "#111318"
        if parent:
            if hasattr(parent, "accent_color"):
                self.accent_color = parent.accent_color
            if hasattr(parent, "background_color"):
                self.background_color = parent.background_color
        ac = self.accent_color

        self.setStyleSheet(f"""
            QLabel {{ color: #E8E8EE; font-size: 9pt; background: transparent; }}
            QLabel#fsTitle {{ font-size: 10.5pt; font-weight: 600; }}
            QLabel#fsMuted {{ color: rgba(255,255,255,0.5); font-size: 8.5pt; }}
            QLineEdit {{
                background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.12);
                border-radius: 6px; padding: 4px 10px; color: #FFFFFF; font-size: 9pt;
            }}
            QLineEdit:focus {{ border-color: {ac}; }}
            QFrame#fsPane {{
                background: rgba(255,255,255,0.025); border: 1px solid rgba(255,255,255,0.09); border-radius: 8px;
            }}
            QTreeView {{
                background: transparent; border: none; outline: none; color: #E8E8EE; font-size: 9pt;
                selection-background-color: transparent; selection-color: #FFFFFF;
            }}
            QTreeView::item {{ border: 1px solid transparent; border-radius: 5px; padding: 0 2px; }}
            QTreeView::item:hover {{ background: rgba(255,255,255,0.04); }}
            QTreeView::item:selected {{ background: rgba(255,255,255,0.07); border-color: {ac}; color: #FFFFFF; }}
            QHeaderView {{ background: transparent; }}
            QHeaderView::section {{
                background: transparent; color: rgba(255,255,255,0.45); border: none;
                border-bottom: 1px solid rgba(255,255,255,0.08); padding: 6px 8px; font-size: 8pt; font-weight: 600;
            }}
            QPushButton {{
                background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.16); border-radius: 6px;
                padding: 4px 12px; color: #E8E8EE; font-size: 8.5pt;
            }}
            QPushButton:hover {{ background: rgba(255,255,255,0.10); border-color: {ac}; }}
            QPushButton#fsChip {{ padding: 2px 10px; font-size: 8pt; }}
            QPushButton#fsPrimary {{ background: {ac}; color: #000000; font-weight: 600; border: none; }}
            QPushButton#fsPrimary:hover {{ background: {ac}; border: 1px solid #FFFFFF; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; }}
            QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.18); border-radius: 3px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        layout = install_themed_frame(self, margins=(14, 10, 14, 12), spacing=10)

        # ---- header: depot + totals, search on the right
        head = QHBoxLayout()
        head.setSpacing(10)
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title = QLabel(f"Depot {self.depot_id}")
        title.setObjectName("fsTitle")
        title_col.addWidget(title)
        self.summary_label = QLabel(f"App {self.app_id}")
        self.summary_label.setObjectName("fsMuted")
        title_col.addWidget(self.summary_label)
        head.addLayout(title_col, 1)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search files and folders")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedWidth(260)
        self.search_input.setFixedHeight(30)
        self.search_input.textChanged.connect(self._filter_tree)
        head.addWidget(self.search_input, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)

        # ---- tree pane with a small toolbar
        pane = QFrame()
        pane.setObjectName("fsPane")
        pl = QVBoxLayout(pane)
        pl.setContentsMargins(6, 6, 6, 6)
        pl.setSpacing(4)
        tools = QHBoxLayout()
        tools.setSpacing(6)
        for text, slot in (
            ("Select all", lambda: self._set_all_checkstate(Qt.CheckState.Checked)),
            ("Select none", lambda: self._set_all_checkstate(Qt.CheckState.Unchecked)),
            ("Expand all", lambda: self.tree_view.expandAll()),
            ("Collapse all", lambda: self.tree_view.collapseAll()),
        ):
            b = QPushButton(text)
            b.setObjectName("fsChip")
            b.setFixedHeight(24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            tools.addWidget(b)
        # Old attribute names, kept for anything that looks them up.
        self.select_all_btn, self.deselect_all_btn = tools.itemAt(0).widget(), tools.itemAt(1).widget()
        tools.addStretch(1)
        self.stats_label = QLabel("Loading file structure...")
        self.stats_label.setObjectName("fsMuted")
        tools.addWidget(self.stats_label)
        pl.addLayout(tools)

        self.tree_view = QTreeView()
        self.tree_view.setAnimated(True)
        self.tree_view.setUniformRowHeights(True)
        self.tree_view.setIndentation(18)
        self.tree_view.setIconSize(QSize(16, 16))
        self.tree_view.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self.tree_view.setVerticalScrollMode(QTreeView.ScrollMode.ScrollPerPixel)

        self.model = QStandardItemModel(0, 2, self.tree_view)
        self.model.setHeaderData(0, Qt.Orientation.Horizontal, "Name")
        self.model.setHeaderData(1, Qt.Orientation.Horizontal, "Size")
        self.proxy = _FileTreeProxy(self.tree_view)
        self.proxy.setSourceModel(self.model)
        self.tree_view.setModel(self.proxy)
        self.tree_view.setSortingEnabled(True)
        self.tree_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        header = self.tree_view.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(1, 96)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        pl.addWidget(self.tree_view, 1)
        layout.addWidget(pane, 1)

        # ---- destination + actions
        dest_layout = QHBoxLayout()
        dest_layout.setSpacing(8)
        dest_lbl = QLabel("Save to")
        dest_lbl.setObjectName("fsMuted")
        dest_layout.addWidget(dest_lbl)
        self.dest_edit = QLineEdit()
        self.dest_edit.setText(os.path.expanduser("~/Downloads"))
        self.dest_edit.setPlaceholderText("Folder for \"Download now\"")
        self.dest_edit.setFixedHeight(30)
        dest_layout.addWidget(self.dest_edit, 1)
        self.browse_btn = QPushButton("Change...")
        self.browse_btn.setFixedHeight(30)
        self.browse_btn.clicked.connect(self._browse_destination)
        dest_layout.addWidget(self.browse_btn)
        layout.addLayout(dest_layout)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)
        hint = QLabel("Unticked files and folders are skipped.")
        hint.setObjectName("fsMuted")
        actions_layout.addWidget(hint)
        actions_layout.addStretch(1)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self.direct_download_btn = QPushButton("Download now")
        self.direct_download_btn.setToolTip(
            "Download just the ticked files into the folder above, without installing the game."
        )
        self.direct_download_btn.clicked.connect(self._on_direct_download_clicked)
        self.ok_btn = QPushButton("Use selection")
        self.ok_btn.setObjectName("fsPrimary")
        self.ok_btn.setToolTip("Install only the ticked files when the game is downloaded.")
        self.ok_btn.clicked.connect(self._on_confirm)
        for b in (self.cancel_btn, self.direct_download_btn, self.ok_btn):
            b.setFixedHeight(32)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            actions_layout.addWidget(b)
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
                name_item.setIcon(self._icons.for_file(file_name))
                name_item.setToolTip(full_path)

                size_item = QStandardItem(self._format_size(size))
                size_item.setEditable(False)
                size_item.setData(size, _SIZE_ROLE)
                size_item.setForeground(QColor(255, 255, 255, 140))
                size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

                parent_item.appendRow([name_item, size_item])

        self._fill_folder_sizes(root)
        self.model.blockSignals(False)
        self.model.itemChanged.connect(self._on_item_changed)
        self.proxy.invalidate()
        # Open the top level so the first view is not a single collapsed folder.
        if self.proxy.rowCount() == 1:
            self.tree_view.expand(self.proxy.index(0, 0))
        files = [f for f in self.all_files if not f["is_dir"]]
        total = sum(f["size"] for f in files)
        self.summary_label.setText(f"App {self.app_id} · {len(files):,} files · {self._format_size(total)}")
        self._update_stats()

    def _fill_folder_sizes(self, item) -> int:
        """Folder rows show the total size of everything under them."""
        total = 0
        for row in range(item.rowCount()):
            child = item.child(row, 0)
            if child is None:
                continue
            if child.data(_DIR_ROLE):
                size = self._fill_folder_sizes(child)
                child.setData(size, _SIZE_ROLE)
                size_item = item.child(row, 1)
                if size_item is not None:
                    size_item.setText(self._format_size(size))
                    size_item.setData(size, _SIZE_ROLE)
                    size_item.setForeground(QColor(255, 255, 255, 110))
                    size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                total += size
            else:
                total += child.data(_SIZE_ROLE) or 0
        return total

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
        name_item.setIcon(self._icons.folder)

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

        self.stats_label.setText(
            f"{sel:,} of {tot:,} files · {self._format_size(sel_sz)} of {self._format_size(tot_sz)}"
        )
        if hasattr(self, "ok_btn"):
            self.ok_btn.setEnabled(sel > 0)
            self.direct_download_btn.setEnabled(sel > 0)

    def _filter_tree(self, text):
        self.proxy.setFilterFixedString(text.strip())
        if text.strip():
            self.tree_view.expandAll()  # show every match in place

    def _browse_destination(self):
        cur = self.dest_edit.text() or os.path.expanduser("~/Downloads")
        chosen = ThemedFileDialog.getExistingDirectory(self, "Select Download Folder", cur)
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
            ThemedMessageBox.warning(
                self, "Warning", "Please select at least one file to download."
            )
            return

        self.accept()

    def _on_direct_download_clicked(self):
        selected = self._collect_selected_files()
        if not selected:
            ThemedMessageBox.warning(self, "No Files Selected", "Please select at least one file to download.")
            return

        dest_folder = self.dest_edit.text().strip()
        if not dest_folder:
            ThemedMessageBox.warning(self, "No Destination", "Please choose a destination folder.")
            return

        try:
            os.makedirs(dest_folder, exist_ok=True)
        except OSError as e:
            ThemedMessageBox.critical(self, "Error", f"Failed to create destination folder:\n{e}")
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
            ThemedMessageBox.critical(self, "Error", f"Could not find depot manifest file in {temp_dir}.")
            return
        if not keys_path or not os.path.exists(keys_path):
            ThemedMessageBox.critical(self, "Error", f"Could not find depot decryption keys file in {temp_dir}.")
            return

        # Write selective filelist
        fl_path = os.path.join(temp_dir, f"direct_filelist_{self.depot_id}.txt")
        try:
            with open(fl_path, "w", encoding="utf-8") as fl:
                for fp in selected:
                    fl.write(fp.replace("\\", "/") + "\n")
        except OSError as e:
            ThemedMessageBox.critical(self, "Error", f"Failed to write filelist: {e}")
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

        progress_dialog = ThemedProgressDialog(f"Downloading {len(selected)} file(s)...", "Cancel", 0, 100, self)
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
                action = ThemedMessageBox.choices(
                    self,
                    "Download Complete",
                    f"Successfully downloaded {len(selected)} file(s) to:\n{dest_folder}",
                    [("Open Folder", "open"), ("Close", "close")],
                    default="open",
                )
                if action == "open":
                    QDesktopServices.openUrl(QUrl.fromLocalFile(dest_folder))
            else:
                if "cancelled" in err.lower():
                    ThemedMessageBox.information(self, "Cancelled", "Direct download was cancelled.")
                else:
                    ThemedMessageBox.critical(self, "Download Error", f"Download failed:\n{err}")

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
