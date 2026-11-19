"""The storage-library picker in the bottom bar and the selected-storage
accessor."""

import logging
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QButtonGroup,
    QSizePolicy,
    QCheckBox,
)
from ui.widgets.modern_dropdown import ModernComboBox

from ui.dialogs.depot_selection import storage_targets

logger = logging.getLogger(__name__)


class StorageMixin:
    """The storage-library picker and the selected-storage accessor."""

    def _setup_storage_buttons(self, layout: QHBoxLayout) -> None:
        """Build the bottom-bar drive picker: buttons, then overflow, then lock.

        Which drives are worth offering, how each is labelled, and which one
        starts selected are decided by :mod:`storage_targets`; this only
        assembles the widgets around those answers.
        """
        def_dir = (
            self._settings.value("default_download_directory", "", type=str)
            if self._settings
            else ""
        )

        storage_paths = storage_targets.candidate_drives(def_dir, self.preferred_library_path) or []
        self._storage_paths = storage_paths
        self._storage_btn_group = QButtonGroup(self)
        self._storage_btn_group.setExclusive(True)

        # Locked storage display (verify / update / DLC flows over installed
        # games): greyed-out label showing the install location, no choice.
        # Fresh installs (lock_storage unset) always get the selectable UI.
        from ui.dialogs.storage_lock import (
            create_locked_storage_button,
            resolve_locked_target,
        )
        locked_target = resolve_locked_target(
            self.parent(),
            getattr(self, "app_id", ""),
            self.preferred_library_path,
            storage_paths,
            bool(getattr(self, "lock_storage", False)),
        )
        if locked_target:
            target = storage_targets.describe(locked_target)
            btn = create_locked_storage_button(
                locked_target, target.label, target.free, height=28, radius=4, accent_color=self.accent_color
            )
            self._locked_storage_btn = btn
            layout.addWidget(btn, 1)
            self.selected_storage_path = locked_target
            return

        def _get_storage_btn_style():
            from utils.color_utils import get_best_foreground_color
            text_hex = get_best_foreground_color(self.accent_color, dark_color="#111318", light_color="#FFFFFF")
            return f"""
                QPushButton {{
                    background-color: transparent;
                    color: rgba(255, 255, 255, 0.7);
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-size: 8.5pt;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    border-color: {self.accent_color};
                    color: {self.accent_color};
                }}
                QPushButton:checked {{
                    background-color: {self.accent_color} !important;
                    color: {text_hex} !important;
                    border: 1px solid {self.accent_color} !important;
                    font-weight: bold;
                }}
            """

        self._storage_buttons = {}
        self._more_storage_combo = None
        self._custom_storage_path = None

        total_storages = len(storage_paths)
        max_direct_buttons = 3

        for i in range(min(total_storages, max_direct_buttons)):
            spath = storage_paths[i]
            target = storage_targets.describe(spath)
            btn_text = (
                f"{target.label} ({target.free})"
                if (total_storages <= 2 and target.free)
                else target.label
            )
            btn = QPushButton(btn_text)
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setToolTip(target.tooltip)
            btn.setStyleSheet(_get_storage_btn_style())
            self._storage_btn_group.addButton(btn, i)
            self._storage_buttons[spath] = btn
            layout.addWidget(btn, 1)

        def _on_storage_btn_clicked(btn_id: int):
            if 0 <= btn_id < len(storage_paths):
                target_path = storage_paths[btn_id]
                self.selected_storage_path = target_path
                logger.info(f"Storage library selected via button [{btn_id}]: {target_path}")
                if self._custom_storage_btn:
                    self._custom_storage_btn.setChecked(False)
                if self._more_storage_combo:
                    self._more_storage_combo.blockSignals(True)
                    self._more_storage_combo.setCurrentIndex(-1)
                    self._more_storage_combo.blockSignals(False)

        self._storage_btn_group.idClicked.connect(_on_storage_btn_clicked)

        if len(storage_paths) > max_direct_buttons:
            self._more_storage_combo = ModernComboBox()
            self._more_storage_combo.setFixedHeight(28)
            self._more_storage_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._more_storage_combo.setPlaceholderText("More Drives")
            for i in range(max_direct_buttons, len(storage_paths)):
                spath = storage_paths[i]
                target = storage_targets.describe(spath)
                item_text = f"{target.label} ({target.free})" if target.free else target.label
                self._more_storage_combo.addItem(item_text, spath)
            self._more_storage_combo.setCurrentIndex(-1)

            def _on_combo_changed(index):
                if index >= 0:
                    chosen_path = self._more_storage_combo.itemData(index)
                    if chosen_path:
                        self.selected_storage_path = chosen_path
                        logger.info(f"Storage library selected via dropdown: {chosen_path}")
                        if self._custom_storage_btn:
                            self._custom_storage_btn.setChecked(False)
                        checked_btn = self._storage_btn_group.checkedButton()
                        if checked_btn:
                            self._storage_btn_group.setExclusive(False)
                            checked_btn.setChecked(False)
                            self._storage_btn_group.setExclusive(True)

            self._more_storage_combo.currentIndexChanged.connect(_on_combo_changed)
            layout.addWidget(self._more_storage_combo, 1)

        # Custom directory selection button
        self._custom_storage_btn = QPushButton("Custom...")
        self._custom_storage_btn.setCheckable(True)
        self._custom_storage_btn.setFixedHeight(28)
        self._custom_storage_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._custom_storage_btn.setToolTip("Select a custom directory or external drive")
        self._custom_storage_btn.setStyleSheet(_get_storage_btn_style())

        def _on_custom_btn_clicked():
            from ui.dialogs.themed_file_dialog import ThemedFileDialog
            from ui.dialogs.themed_message_box import ThemedMessageBox
            from utils.paths import is_valid_download_directory, normalize_to_library_root

            chosen_dir = ThemedFileDialog.getExistingDirectory(self, "Select Installation / Steam Library Location")
            if not chosen_dir:
                if self._custom_storage_path and self.selected_storage_path == self._custom_storage_path:
                    self._custom_storage_btn.setChecked(True)
                else:
                    self._custom_storage_btn.setChecked(False)
                    if self.selected_storage_path in self._storage_buttons:
                        self._storage_buttons[self.selected_storage_path].setChecked(True)
                return

            norm = normalize_to_library_root(chosen_dir)
            if not is_valid_download_directory(norm):
                ThemedMessageBox.warning(
                    self,
                    "Invalid Location",
                    "System temporary directories (/tmp, AppImage mounts, etc.) cannot be used as a download location.",
                )
                self._custom_storage_btn.setChecked(bool(self._custom_storage_path and self.selected_storage_path == self._custom_storage_path))
                return

            real_p = os.path.realpath(norm)
            self._custom_storage_path = real_p
            self.selected_storage_path = real_p

            target = storage_targets.describe(real_p)
            btn_text = f"{target.label} ({target.free})" if target.free else target.label
            self._custom_storage_btn.setText(btn_text)
            self._custom_storage_btn.setToolTip(target.tooltip)
            self._custom_storage_btn.setChecked(True)

            self._storage_btn_group.setExclusive(False)
            for b in self._storage_btn_group.buttons():
                b.setChecked(False)
            self._storage_btn_group.setExclusive(True)

            if self._more_storage_combo:
                self._more_storage_combo.blockSignals(True)
                self._more_storage_combo.setCurrentIndex(-1)
                self._more_storage_combo.blockSignals(False)

            logger.info(f"Custom storage library selected: {real_p}")

        self._custom_storage_btn.clicked.connect(_on_custom_btn_clicked)
        layout.addWidget(self._custom_storage_btn, 1)

        self.remember_storage_chk = QCheckBox("Remember choice")
        self.remember_storage_chk.setChecked(False)
        self.remember_storage_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remember_storage_chk.setToolTip("Use this library from now on without asking.\nChange it in Settings -> Downloads.")
        self.remember_storage_chk.setStyleSheet("""
            QCheckBox {
                font-size: 8pt;
                color: rgba(255, 255, 255, 0.7);
                spacing: 5px;
                border: none;
                background: transparent;
            }
            QCheckBox:focus {
                border: none;
                outline: none;
            }
        """)
        layout.addWidget(self.remember_storage_chk)

        # Pre-select priority: preferred library path (zip confirmation or job
        # metadata), then the library the game is already installed in, then the
        # configured default, then the first drive.
        parent = self.parent()
        game_manager = getattr(parent, "game_manager", None) if parent else None
        installed_target = storage_targets.installed_library(
            self.app_id, game_manager, storage_paths
        )
        if installed_target:
            logger.info(
                f"[DepotSelection] Pre-selecting existing install library for "
                f"AppID {self.app_id}: {installed_target}"
            )

        default_target = storage_targets.preselect(
            storage_paths, self.preferred_library_path, installed_target, def_dir
        )
        if (
            self.preferred_library_path
            and default_target
            and os.path.realpath(self.preferred_library_path) == default_target
        ):
            logger.info(f"[DepotSelection] Pre-selecting preferred library path: {default_target}")
        self.selected_storage_path = default_target
        if default_target in self._storage_buttons:
            self._storage_buttons[default_target].setChecked(True)
        elif self._more_storage_combo and any(self._more_storage_combo.itemData(i) == default_target for i in range(self._more_storage_combo.count())):
            for idx in range(self._more_storage_combo.count()):
                if self._more_storage_combo.itemData(idx) == default_target:
                    self._more_storage_combo.setCurrentIndex(idx)
                    break
        elif default_target and getattr(self, "_custom_storage_btn", None):
            self._custom_storage_path = default_target
            target = storage_targets.describe(default_target)
            btn_text = f"{target.label} ({target.free})" if target.free else target.label
            self._custom_storage_btn.setText(btn_text)
            self._custom_storage_btn.setToolTip(target.tooltip)
            self._custom_storage_btn.setChecked(True)

    def get_selected_storage(self) -> str:
        """Returns the selected storage destination path, or None."""
        return self.selected_storage_path

    def should_remember_storage(self) -> bool:
        """Whether the user checked the 'Remember choice' toggle for storage."""
        return bool(getattr(self, "remember_storage_chk", None) and self.remember_storage_chk.isChecked())
