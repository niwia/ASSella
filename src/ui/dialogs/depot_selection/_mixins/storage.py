"""The storage-library picker in the bottom bar and the selected-storage
accessor."""

import logging
import os

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QButtonGroup,
    QComboBox,
    QSizePolicy,
)

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

        storage_paths = storage_targets.candidate_drives(def_dir, self.preferred_library_path)
        self._storage_paths = storage_paths
        self._storage_btn_group = QButtonGroup(self)
        self._storage_btn_group.setExclusive(True)

        if not storage_paths:
            layout.addStretch(1)
            return

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
                locked_target, target.label, target.free, height=28, radius=4
            )
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
                if self._more_storage_combo:
                    self._more_storage_combo.blockSignals(True)
                    self._more_storage_combo.setCurrentIndex(0)
                    self._more_storage_combo.blockSignals(False)

        self._storage_btn_group.idClicked.connect(_on_storage_btn_clicked)

        if len(storage_paths) > max_direct_buttons:
            self._more_storage_combo = QComboBox()
            self._more_storage_combo.setFixedHeight(28)
            self._more_storage_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._more_storage_combo.addItem("More Drives ▾", None)
            self._more_storage_combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: transparent;
                    color: rgba(255, 255, 255, 0.7);
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 4px;
                    padding: 2px 8px;
                    font-size: 8.5pt;
                }}
                QComboBox:hover {{
                    border-color: {self.accent_color};
                    color: {self.accent_color};
                }}
                QComboBox QAbstractItemView {{
                    background-color: #1a1a24;
                    color: #FFFFFF;
                    selection-background-color: {self.accent_color};
                    border: 1px solid rgba(255, 255, 255, 0.15);
                }}
            """)
            for i in range(max_direct_buttons, len(storage_paths)):
                spath = storage_paths[i]
                target = storage_targets.describe(spath)
                item_text = f"{target.label} ({target.free})" if target.free else target.label
                self._more_storage_combo.addItem(item_text, spath)

            def _on_combo_changed(index):
                if index > 0:
                    chosen_path = self._more_storage_combo.itemData(index)
                    if chosen_path:
                        self.selected_storage_path = chosen_path
                        logger.info(f"Storage library selected via dropdown: {chosen_path}")
                        checked_btn = self._storage_btn_group.checkedButton()
                        if checked_btn:
                            self._storage_btn_group.setExclusive(False)
                            checked_btn.setChecked(False)
                            self._storage_btn_group.setExclusive(True)

            self._more_storage_combo.currentIndexChanged.connect(_on_combo_changed)
            layout.addWidget(self._more_storage_combo, 1)

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
        elif self._more_storage_combo:
            for idx in range(1, self._more_storage_combo.count()):
                if self._more_storage_combo.itemData(idx) == default_target:
                    self._more_storage_combo.setCurrentIndex(idx)
                    break

    def get_selected_storage(self) -> str:
        """Returns the selected storage destination path, or None."""
        return self.selected_storage_path
