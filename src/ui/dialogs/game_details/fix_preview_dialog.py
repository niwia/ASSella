"""
fix_preview_dialog.py - Dialog for previewing and selecting fix files before application or restoration.
In apply mode: cleanly lists all files to be installed with checkboxes, replace vs new indicators, and filters.
In restore mode: shows which files will be reverted from backup vs deleted, with conflict detection highlighted before modifying disk.
"""

import logging
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QScrollArea,
    QWidget,
    QCheckBox,
)
from ui.dialogs.themed_message_box import ThemedMessageBox
from ui.dialogs.themed_dialog import install_themed_frame

logger = logging.getLogger(__name__)


def _format_size(size_bytes: int) -> str:
    """Format byte size to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def show_fix_preview_dialog(
    parent,
    zip_path: str | Path,
    fix_title: str,
    game_name: str,
    game_path: Optional[str | Path] = None,
) -> Optional[Set[str]]:
    """
    Show a dialog previewing all files in a fix archive.
    User can select which files to apply.
    """
    dialog = FixPreviewDialog(
        parent=parent,
        zip_path=zip_path,
        fix_title=fix_title,
        game_name=game_name,
        game_path=game_path,
        mode="apply",
    )
    result = dialog.exec()
    if result == QDialog.DialogCode.Accepted:
        return dialog.selected_files
    return None


def show_restore_preview_dialog(
    parent,
    game_path: str | Path,
    fix_id: str,
    fix_title: str,
    game_name: str,
    preview_data: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Show a dialog previewing the actions that will be performed during fix restoration.
    Displays backup reversions, file removals, conflict warnings, and escape hatches.
    Returns:
      - 'restore': Normal restore confirmed
      - 'force': Force restore confirmed (overwrites conflicts)
      - 'discard': Discard fix record without touching game files
      - None: User cancelled
    """
    dialog = FixPreviewDialog(
        parent=parent,
        zip_path=None,
        fix_title=fix_title,
        game_name=game_name,
        game_path=game_path,
        mode="restore",
        preview_data=preview_data,
    )
    result = dialog.exec()
    if result == QDialog.DialogCode.Accepted:
        return dialog.action_result or "restore"
    return None


class _FileRowFrame(QFrame):
    """Interactive row frame that toggles checkbox on row click (apply mode)."""

    def mousePressEvent(self, event):
        if hasattr(self, "_cb") and self._cb and event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            child = self.childAt(pos)
            if child != self._cb:
                self._cb.setChecked(not self._cb.isChecked())
        super().mousePressEvent(event)


class FixPreviewDialog(QDialog):
    """Sleek dialog for previewing and selecting files to install or restore."""

    def __init__(
        self,
        parent,
        zip_path: Optional[str | Path] = None,
        fix_title: str = "Fix",
        game_name: str = "Game",
        game_path: Optional[str | Path] = None,
        mode: str = "apply",
        preview_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(parent)
        self.zip_path = Path(zip_path) if zip_path else None
        self.fix_title = fix_title
        self.game_name = game_name
        self.game_path = Path(game_path) if game_path else None
        self.mode = mode
        self.preview_data = preview_data or {}
        self.selected_files: Set[str] = set()
        self._all_files: List[Dict[str, Any]] = []
        self._list_widgets: List[QFrame] = []
        self._filter_buttons: Dict[Optional[str], QPushButton] = {}
        self._active_filter: Optional[str] = None
        self.action_result: Optional[str] = None

        action_title = "Apply Fix" if self.mode == "apply" else "Restore Fix"
        self.setWindowTitle(f"{action_title}: {fix_title}")
        self.resize(700, 530)
        self.setMinimumSize(560, 430)

        self._setup_ui()
        self._load_files()

    def _setup_ui(self):
        """Set up the dialog UI."""
        action_title = "Apply Fix" if self.mode == "apply" else "Restore Fix"
        layout = install_themed_frame(
            self,
            title=f"{action_title}: {self.fix_title}",
            margins=(20, 16, 20, 18),
            spacing=14,
        )

        # Header section
        header_vbox = QVBoxLayout()
        header_vbox.setSpacing(6)

        header_top = QHBoxLayout()
        header_top.setSpacing(8)

        icon_str = "🔧" if self.mode == "apply" else "↩"
        title_lbl = QLabel(f"{icon_str} {self.fix_title}")
        title_lbl.setStyleSheet("color: #FFFFFF; font-size: 13pt; font-weight: bold;")
        header_top.addWidget(title_lbl)

        game_lbl = QLabel(f"for {self.game_name}")
        game_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 9.5pt;")
        header_top.addWidget(game_lbl)
        header_top.addStretch()

        header_vbox.addLayout(header_top)

        # Legend explanation
        if self.mode == "apply":
            legend_lbl = QLabel(
                '<span style="color: #fb923c; font-weight: 600;">●</span> '
                '<span style="color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;">Orange files replace existing game files</span>'
                '&nbsp;&nbsp;&nbsp;&nbsp;'
                '<span style="color: #e2e8f0; font-weight: 600;">●</span> '
                '<span style="color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;">White files are newly added</span>'
            )
        else:
            legend_lbl = QLabel(
                '<span style="color: #60a5fa; font-weight: 600;">●</span> '
                '<span style="color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;">Blue: restore</span>'
                '&nbsp;&nbsp;&nbsp;&nbsp;'
                '<span style="color: #f87171; font-weight: 600;">●</span> '
                '<span style="color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;">Red: delete</span>'
                '&nbsp;&nbsp;&nbsp;&nbsp;'
                '<span style="color: #a78bfa; font-weight: 600;">●</span> '
                '<span style="color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;">Purple: other fix</span>'
                '&nbsp;&nbsp;&nbsp;&nbsp;'
                '<span style="color: #fbbf24; font-weight: 600;">●</span> '
                '<span style="color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;">Amber: conflict</span>'
            )
        legend_lbl.setStyleSheet("margin-top: 2px;")
        header_vbox.addWidget(legend_lbl)

        # Alerts / Warnings for Restore Mode
        if self.mode == "restore":
            conflicts = self.preview_data.get("conflicts", [])
            if conflicts:
                conflict_banner = QLabel(
                    f"⚠️ Conflict detected on {len(conflicts)} file(s). "
                    "Game files were modified externally after applying this fix. "
                    "Use 'Restore Anyway' to force overwrite from backups, or 'Discard Record' to safely unregister."
                )
                conflict_banner.setWordWrap(True)
                conflict_banner.setStyleSheet("""
                    background: rgba(239, 68, 68, 0.15);
                    border: 1px solid rgba(239, 68, 68, 0.35);
                    border-radius: 6px;
                    color: #fca5a5;
                    font-size: 8.5pt;
                    padding: 8px 12px;
                """)
                header_vbox.addWidget(conflict_banner)

            if self.preview_data.get("has_legacy_files"):
                legacy_banner = QLabel(
                    "ℹ️ Legacy fix receipt: post-install hashes were not recorded. "
                    "Files will be restored directly from original backups."
                )
                legacy_banner.setWordWrap(True)
                legacy_banner.setStyleSheet("""
                    background: rgba(59, 130, 246, 0.12);
                    border: 1px solid rgba(59, 130, 246, 0.3);
                    border-radius: 6px;
                    color: #93c5fd;
                    font-size: 8pt;
                    padding: 6px 12px;
                """)
                header_vbox.addWidget(legacy_banner)

        layout.addLayout(header_vbox)

        # Controls bar (Selection buttons on left, Filter pills on right)
        controls = QHBoxLayout()
        controls.setSpacing(8)

        if self.mode == "apply":
            self.select_all_btn = QPushButton("Select All")
            self.select_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.select_all_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255, 255, 255, 0.08);
                    border: 1px solid rgba(255, 255, 255, 0.14);
                    border-radius: 5px;
                    color: #e5e7eb;
                    font-size: 8.5pt;
                    font-weight: 500;
                    padding: 4px 12px;
                }
                QPushButton:hover {
                    background: rgba(255, 255, 255, 0.14);
                    border-color: rgba(255, 255, 255, 0.25);
                }
            """)
            self.select_all_btn.clicked.connect(self._on_select_all)
            controls.addWidget(self.select_all_btn)

            self.deselect_all_btn = QPushButton("Deselect All")
            self.deselect_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.deselect_all_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255, 255, 255, 0.08);
                    border: 1px solid rgba(255, 255, 255, 0.14);
                    border-radius: 5px;
                    color: #e5e7eb;
                    font-size: 8.5pt;
                    font-weight: 500;
                    padding: 4px 12px;
                }
                QPushButton:hover {
                    background: rgba(255, 255, 255, 0.14);
                    border-color: rgba(255, 255, 255, 0.25);
                }
            """)
            self.deselect_all_btn.clicked.connect(self._on_deselect_all)
            controls.addWidget(self.deselect_all_btn)

        controls.addStretch()

        # Filter toggle pills
        filter_lbl = QLabel("Filter:")
        filter_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.45); font-size: 8pt;")
        controls.addWidget(filter_lbl)

        filter_options = [("All", None)]
        if self.mode == "apply":
            filter_options.extend([("Modified", "modified"), ("New", "added")])
        else:
            filter_options.extend([("Restore", "revert"), ("Delete", "delete")])
            if any(f.get("status") == "managed_by_other_fix" for f in self.preview_data.get("files", [])):
                filter_options.append(("Other Fix", "managed_by_other_fix"))
            if self.preview_data.get("conflicts"):
                filter_options.append(("Conflicts", "conflict"))

        for label, val in filter_options:
            btn = self._create_filter_pill(label, val)
            self._filter_buttons[val] = btn
            controls.addWidget(btn)

        self._update_filter_button_styles()
        layout.addLayout(controls)

        # File list container frame
        list_container = QFrame()
        list_container.setStyleSheet("""
            QFrame {
                background: rgba(0, 0, 0, 0.25);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
        """)
        list_container_lay = QVBoxLayout(list_container)
        list_container_lay.setContentsMargins(6, 6, 6, 6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        scroll_widget = QWidget()
        scroll_widget.setStyleSheet("background: transparent;")
        self.file_list_layout = QVBoxLayout(scroll_widget)
        self.file_list_layout.setContentsMargins(2, 2, 2, 2)
        self.file_list_layout.setSpacing(3)
        self.file_list_layout.addStretch()

        scroll.setWidget(scroll_widget)
        list_container_lay.addWidget(scroll)
        layout.addWidget(list_container, 1)

        # Bottom section: status on left, discard / cancel / action button on right
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt;")
        bottom_row.addWidget(self.status_label, 1)

        if self.mode == "restore":
            self.discard_btn = QPushButton("Discard Record")
            self.discard_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.discard_btn.setToolTip("Unregister this fix without modifying any files on disk")
            self.discard_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(239, 68, 68, 0.12);
                    border: 1px solid rgba(239, 68, 68, 0.35);
                    border-radius: 6px;
                    color: #fca5a5;
                    font-size: 8.5pt;
                    font-weight: 500;
                    padding: 8px 14px;
                }
                QPushButton:hover {
                    background: rgba(239, 68, 68, 0.22);
                    border-color: rgba(239, 68, 68, 0.5);
                }
            """)
            self.discard_btn.clicked.connect(self._on_discard_record)
            bottom_row.addWidget(self.discard_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 6px;
                color: #e5e7eb;
                font-size: 9pt;
                font-weight: 500;
                padding: 8px 18px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.15);
                border-color: rgba(255, 255, 255, 0.28);
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        bottom_row.addWidget(self.cancel_btn)

        if self.mode == "apply":
            self.action_btn = QPushButton("Apply Selected")
            self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_btn.setStyleSheet("""
                QPushButton {
                    background: #3b82f6;
                    border: 1px solid #2563eb;
                    border-radius: 6px;
                    color: #FFFFFF;
                    font-size: 9pt;
                    font-weight: 600;
                    padding: 8px 20px;
                }
                QPushButton:hover {
                    background: #2563eb;
                    border-color: #1d4ed8;
                }
                QPushButton:disabled {
                    background: rgba(255, 255, 255, 0.08);
                    border-color: rgba(255, 255, 255, 0.12);
                    color: rgba(255, 255, 255, 0.35);
                }
            """)
            self.action_btn.clicked.connect(self._on_apply)
        else:
            self.action_btn = QPushButton("Restore Fix")
            self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_btn.setStyleSheet("""
                QPushButton {
                    background: #d97706;
                    border: 1px solid #b45309;
                    border-radius: 6px;
                    color: #FFFFFF;
                    font-size: 9pt;
                    font-weight: 600;
                    padding: 8px 20px;
                }
                QPushButton:hover {
                    background: #b45309;
                    border-color: #92400e;
                }
                QPushButton:disabled {
                    background: rgba(255, 255, 255, 0.08);
                    border-color: rgba(255, 255, 255, 0.12);
                    color: rgba(255, 255, 255, 0.35);
                }
            """)
            self.action_btn.clicked.connect(self._on_restore_confirm)

        bottom_row.addWidget(self.action_btn)
        layout.addLayout(bottom_row)

    def _create_filter_pill(self, label: str, filter_val: Optional[str]) -> QPushButton:
        """Create a compact rounded filter toggle pill."""
        btn = QPushButton(label)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _checked, v=filter_val: self._on_filter_pill_clicked(v))
        return btn

    def _on_filter_pill_clicked(self, filter_val: Optional[str]):
        """Handle clicking a filter toggle pill."""
        self._active_filter = filter_val
        self._update_filter_button_styles()
        self._filter_files(filter_val)

    def _update_filter_button_styles(self):
        """Update active vs inactive appearance of filter pills."""
        for val, btn in self._filter_buttons.items():
            if val == self._active_filter:
                btn.setStyleSheet("""
                    QPushButton {
                        background: rgba(255, 255, 255, 0.22);
                        border: 1px solid rgba(255, 255, 255, 0.40);
                        border-radius: 4px;
                        color: #FFFFFF;
                        font-size: 8pt;
                        font-weight: 600;
                        padding: 3px 9px;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background: rgba(255, 255, 255, 0.05);
                        border: 1px solid rgba(255, 255, 255, 0.12);
                        border-radius: 4px;
                        color: #9ca3af;
                        font-size: 8pt;
                        font-weight: 500;
                        padding: 3px 9px;
                    }
                    QPushButton:hover {
                        background: rgba(255, 255, 255, 0.12);
                        color: #e5e7eb;
                    }
                """)

    def _load_files(self):
        """Load and display files according to mode."""
        if self.mode == "restore":
            self._all_files = list(self.preview_data.get("files", []))
            self._render_file_list()
            self._update_status()
            return

        if not self.zip_path or not self.zip_path.is_file():
            ThemedMessageBox.critical(self, "Error", f"Archive not found: {self.zip_path}")
            return

        try:
            with zipfile.ZipFile(self.zip_path, "r") as zf:
                entries = zf.infolist()

            self._all_files = []
            for member in entries:
                if member.is_dir():
                    continue

                size = member.file_size
                action = "added"
                if self.game_path and (self.game_path / member.filename).exists():
                    action = "modified"

                self._all_files.append({
                    "relative_path": member.filename,
                    "size": size,
                    "action": action,
                    "status": "ok",
                })

            self._render_file_list()
            self._update_status()

        except Exception as e:
            logger.error(f"[FixPreviewDialog] Failed to load ZIP: {e}")
            ThemedMessageBox.critical(self, "Error", f"Failed to read fix archive:\n{e}")

    def _render_file_list(self):
        """Render the file list in the dialog."""
        while self.file_list_layout.count() > 1:
            item = self.file_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._list_widgets = []

        for file_info in self._all_files:
            widget = self._create_file_widget(file_info)
            self.file_list_layout.insertWidget(len(self._list_widgets), widget)
            self._list_widgets.append(widget)

    def _create_file_widget(self, file_info: Dict[str, Any]) -> QWidget:
        """Create a clean row for a single file entry."""
        container = _FileRowFrame()
        container._file_info = file_info
        container.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid transparent;
                border-radius: 6px;
            }
            QFrame:hover {
                background: rgba(255, 255, 255, 0.06);
                border-color: rgba(255, 255, 255, 0.1);
            }
        """)

        lay = QHBoxLayout(container)
        lay.setContentsMargins(10, 8, 12, 8)
        lay.setSpacing(12)

        action = file_info.get("action", "added")
        status = file_info.get("status", "ok")
        rel_path = file_info["relative_path"]

        if self.mode == "apply":
            container.setCursor(Qt.CursorShape.PointingHandCursor)
            cb = QCheckBox()
            cb.setChecked(True)
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            cb.stateChanged.connect(self._update_status)
            container._cb = cb
            lay.addWidget(cb)
        else:
            container._cb = None
            badge_lbl = QLabel()
            if status == "conflict":
                badge_lbl.setText("⚠️")
                badge_lbl.setToolTip(file_info.get("conflict_reason") or "File conflict")
            elif status == "managed_by_other_fix":
                badge_lbl.setText("🔗")
                badge_lbl.setStyleSheet("color: #a78bfa; font-weight: bold; font-size: 10pt;")
                badge_lbl.setToolTip(file_info.get("conflict_reason") or "Managed by another active fix")
            elif action == "revert":
                badge_lbl.setText("↩")
                badge_lbl.setStyleSheet("color: #60a5fa; font-weight: bold; font-size: 10pt;")
                badge_lbl.setToolTip("Will be restored from backup")
            elif action == "delete":
                badge_lbl.setText("✕")
                badge_lbl.setStyleSheet("color: #f87171; font-weight: bold; font-size: 10pt;")
                badge_lbl.setToolTip("Will be permanently deleted")
            else:
                badge_lbl.setText("✓")
                badge_lbl.setStyleSheet("color: #9ca3af; font-size: 9pt;")
            lay.addWidget(badge_lbl)

        # File path label
        path_lbl = QLabel(rel_path)
        path_lbl.setWordWrap(True)

        if self.mode == "apply":
            if action == "modified":
                path_lbl.setStyleSheet("color: #fb923c; font-size: 9pt; font-weight: 500; background: transparent; border: none;")
                path_lbl.setToolTip(f"Replaces existing file: {rel_path}")
            else:
                path_lbl.setStyleSheet("color: #f3f4f6; font-size: 9pt; font-weight: 400; background: transparent; border: none;")
                path_lbl.setToolTip(f"New file: {rel_path}")
        else:
            if status == "conflict":
                path_lbl.setStyleSheet("color: #fbbf24; font-size: 9pt; font-weight: bold; background: transparent; border: none;")
                path_lbl.setToolTip(file_info.get("conflict_reason") or "Conflict detected")
            elif status == "managed_by_other_fix":
                path_lbl.setStyleSheet("color: #c4b5fd; font-size: 9pt; font-weight: 500; background: transparent; border: none;")
                managing_title = file_info.get("managing_fix_title") or file_info.get("managing_fix_id") or "another fix"
                path_lbl.setToolTip(f"Active fix '{managing_title}' currently controls this file; it will be kept intact.")
            elif action == "revert":
                path_lbl.setStyleSheet("color: #93c5fd; font-size: 9pt; font-weight: 500; background: transparent; border: none;")
                path_lbl.setToolTip(f"Restored from backup: {rel_path}")
            elif action == "delete":
                path_lbl.setStyleSheet("color: #fca5a5; font-size: 9pt; font-weight: 400; background: transparent; border: none;")
                path_lbl.setToolTip(f"Deleted (added by fix): {rel_path}")
            else:
                path_lbl.setStyleSheet("color: #6b7280; font-size: 9pt; font-style: italic; background: transparent; border: none;")
                path_lbl.setToolTip(f"Already removed: {rel_path}")

        lay.addWidget(path_lbl, 1)

        # Extra badge for conflicts or managed files in restore mode
        if self.mode == "restore":
            if status == "conflict":
                c_tag = QLabel("CONFLICT")
                c_tag.setStyleSheet("""
                    background: rgba(239, 68, 68, 0.2);
                    border: 1px solid rgba(239, 68, 68, 0.4);
                    border-radius: 3px;
                    color: #fca5a5;
                    font-size: 7pt;
                    font-weight: bold;
                    padding: 1px 4px;
                """)
                lay.addWidget(c_tag)
            elif status == "managed_by_other_fix":
                m_tag = QLabel("OTHER FIX")
                m_tag.setStyleSheet("""
                    background: rgba(139, 92, 246, 0.2);
                    border: 1px solid rgba(139, 92, 246, 0.4);
                    border-radius: 3px;
                    color: #c4b5fd;
                    font-size: 7pt;
                    font-weight: bold;
                    padding: 1px 4px;
                """)
                m_tag.setToolTip("Controlled by another active fix")
                lay.addWidget(m_tag)

        # File size label
        size_lbl = QLabel(_format_size(file_info.get("size", 0)))
        size_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.45); font-size: 8.5pt; background: transparent; border: none;")
        size_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(size_lbl)

        return container

    def _update_status(self):
        """Update the status label and action button state."""
        total = len(self._list_widgets)

        if self.mode == "apply":
            checked = sum(1 for w in self._list_widgets if getattr(w, "_cb", None) and w._cb.isChecked())
            mod_selected = sum(
                1 for w in self._list_widgets
                if getattr(w, "_cb", None) and w._cb.isChecked() and getattr(w, "_file_info", {}).get("action") == "modified"
            )
            add_selected = checked - mod_selected

            if total == 0:
                self.status_label.setText("No files in archive")
                self.action_btn.setEnabled(False)
                return

            detail_parts = []
            if mod_selected > 0:
                detail_parts.append(f"{mod_selected} to replace")
            if add_selected > 0:
                detail_parts.append(f"{add_selected} new")

            detail_str = f" ({', '.join(detail_parts)})" if detail_parts else ""
            self.status_label.setText(f"{checked} of {total} files selected{detail_str}")
            self.action_btn.setEnabled(checked > 0)

        else:
            conflicts = self.preview_data.get("conflicts", [])
            revert_count = self.preview_data.get("revert_count", 0)
            delete_count = self.preview_data.get("delete_count", 0)
            managed_count = sum(
                1 for f in self._all_files if f.get("status") == "managed_by_other_fix"
            )

            if conflicts:
                self.status_label.setText(f"⚠️ {len(conflicts)} conflict(s) detected. Choose an action:")
                self.status_label.setStyleSheet("color: #fbbf24; font-weight: bold; font-size: 8.5pt;")
                self.action_btn.setText("Restore Anyway")
                self.action_btn.setStyleSheet("""
                    QPushButton {
                        background: #b45309;
                        border: 1px solid #d97706;
                        border-radius: 6px;
                        color: #FFFFFF;
                        font-size: 9pt;
                        font-weight: 600;
                        padding: 8px 16px;
                    }
                    QPushButton:hover {
                        background: #92400e;
                    }
                """)
                self.action_btn.setEnabled(True)
            else:
                extra = f" ({managed_count} preserved for another fix)" if managed_count else ""
                self.status_label.setText(f"Ready: {revert_count} restored, {delete_count} removed{extra}")
                self.status_label.setStyleSheet("color: #4ade80; font-size: 8.5pt;")
                self.action_btn.setText("Restore Fix")
                self.action_btn.setStyleSheet("""
                    QPushButton {
                        background: #d97706;
                        border: 1px solid #b45309;
                        border-radius: 6px;
                        color: #FFFFFF;
                        font-size: 9pt;
                        font-weight: 600;
                        padding: 8px 20px;
                    }
                    QPushButton:hover {
                        background: #b45309;
                        border-color: #92400e;
                    }
                """)
                self.action_btn.setEnabled(True)

    def _on_select_all(self):
        """Select all visible files."""
        for w in self._list_widgets:
            if not w.isHidden() and getattr(w, "_cb", None):
                w._cb.setChecked(True)
        self._update_status()

    def _on_deselect_all(self):
        """Deselect all visible files."""
        for w in self._list_widgets:
            if not w.isHidden() and getattr(w, "_cb", None):
                w._cb.setChecked(False)
        self._update_status()

    def _filter_files(self, action_filter: Optional[str]):
        """Show only files matching the filter, or all if None."""
        for w in self._list_widgets:
            file_info = getattr(w, "_file_info", None)
            if not file_info:
                continue

            if action_filter is None:
                w.setVisible(True)
            elif action_filter == "conflict":
                w.setVisible(file_info.get("status") == "conflict")
            elif action_filter == "managed_by_other_fix":
                w.setVisible(file_info.get("status") == "managed_by_other_fix")
            elif file_info.get("action") == action_filter:
                w.setVisible(True)
            else:
                w.setVisible(False)

        self._update_status()

    def _on_apply(self):
        """Collect selected files and accept dialog in apply mode."""
        self.selected_files = set()
        for w in self._list_widgets:
            if getattr(w, "_cb", None) and w._cb.isChecked() and hasattr(w, "_file_info"):
                self.selected_files.add(w._file_info["relative_path"])
        self.action_result = "apply"
        self.accept()

    def _on_restore_confirm(self):
        """Confirm and accept dialog in restore mode."""
        conflicts = self.preview_data.get("conflicts", [])
        if conflicts:
            managed = [
                f for f in self.preview_data.get("files", [])
                if f.get("status") == "managed_by_other_fix"
            ]
            managed_note = ""
            if managed:
                managed_note = (
                    f"\n\n{len(managed)} file(s) are currently owned by another applied fix "
                    "and will be overwritten as well."
                )
            reply = ThemedMessageBox.question(
                self,
                "Force Restore Despite Conflicts?",
                f"Conflict detected on {len(conflicts)} file(s).\n\n"
                "Restoring anyway will overwrite existing files with backups and remove added files."
                f"{managed_note}\n\n"
                "Are you sure you want to proceed with Force Restore?",
                ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
                ThemedMessageBox.StandardButton.No,
            )
            if reply != ThemedMessageBox.StandardButton.Yes:
                return
            self.action_result = "force"
        else:
            self.action_result = "restore"
        self.accept()

    def _on_discard_record(self):
        """Prompt to discard fix record without touching files."""
        reply = ThemedMessageBox.question(
            self,
            "Discard Fix Record?",
            f"Are you sure you want to discard the record and backups for '{self.fix_title}'?\n\n"
            "This will remove the fix from ASSella's installed fixes list WITHOUT modifying any game files on disk.",
            ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
            ThemedMessageBox.StandardButton.No,
        )
        if reply == ThemedMessageBox.StandardButton.Yes:
            self.action_result = "discard"
            self.accept()
