"""The depot selection dialog.

Thin composer. Every behaviour lives in a mixin under ``_mixins`` - one module
per capability - and the mixins share instance state by design, because Qt
binds the signals, the slots and the event overrides to this one class.
``_mixins/__init__.py`` carries the same map.

What stays here is the public surface: the sibling-dialog routing in
``__new__``, construction and instance state, the result accessors, ``accept``
and the Qt event overrides.

The earlier split moved the classification rules, the table items and the
table views into their own modules; this one moves the behaviour into
``_mixins``. ``ui.dialogs.depotselection`` re-exports everything, so the
importers are unchanged either way.

Row order is fixed before the table is filled rather than by clicking column
headers. The header still carries the select-all checkbox - its only
interactive part.
"""

import logging
import tempfile

from pathlib import Path
from typing import Dict, Tuple

from PyQt6.QtCore import Qt, QTimer, QEvent, pyqtSignal

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QWidget,
)
from ui.dialogs.themed_message_box import ThemedMessageBox

from utils.image_fetcher import ImageFetcher
from utils.settings import get_settings

from ui.dialogs.depot_selection._mixins import (
    BuildUIMixin,
    DepotTableMixin,
    StorageMixin,
    DepotMetadataMixin,
    SelectionMixin,
    ExecutableVerifyMixin,
    HeaderImageMixin,
    FileCustomisationMixin,
    BranchBuildMixin,
    MigrationMixin,
)

logger = logging.getLogger(__name__)


class DepotSelectionDialog(
    BuildUIMixin,
    DepotTableMixin,
    StorageMixin,
    DepotMetadataMixin,
    SelectionMixin,
    ExecutableVerifyMixin,
    HeaderImageMixin,
    FileCustomisationMixin,
    BranchBuildMixin,
    MigrationMixin,
    QDialog,
):
    """Thin composer; behaviours live in the ``_mixins`` package."""

    _depots_enriched_signal = pyqtSignal(dict)
    _missing_contents_checked_signal = pyqtSignal(dict)
    _missing_depots_updated_signal = pyqtSignal()

    def __new__(cls, *args, **kwargs):
        if cls is not DepotSelectionDialog:
            return super().__new__(cls)

        is_single = kwargs.get("is_single_depot", False)
        depots = kwargs.get("depots")
        if depots is None and len(args) >= 3:
            depots = args[2]

        missing = kwargs.get("missing_hubcap_depots")
        if missing is None and len(args) >= 9:
            missing = args[8]

        is_dlc = kwargs.get("dlc_only_mode", False) or kwargs.get("lock_dlc_only", False)
        if not is_dlc and len(args) > 16:
            is_dlc = bool(args[16])
        if not is_dlc and len(args) > 17:
            is_dlc = bool(args[17])

        # Only route to SingleDepotSelectionDialog if not DLC-only and there are NO missing depots from Hubcap.
        # If DLCs/depots are missing, full DepotSelectionDialog MUST be shown so the user sees
        # the greyed out missing depots and their statuses.
        from ui.dialogs.depot_selection.rules import is_effectively_single_depot
        effective_single = is_single or (isinstance(depots, dict) and is_effectively_single_depot(depots, missing))
        if not is_dlc and not missing and effective_single:
            from ui.dialogs.single_depot_dialog import SingleDepotSelectionDialog
            return SingleDepotSelectionDialog(*args, **kwargs)

        return super().__new__(cls)

    def __init__(
        self,
        app_id,
        game_name,
        depots,
        header_url=None,
        parent=None,
        selected_depots=None,
        show_storage=True,
        is_single_depot=False,
        missing_hubcap_depots=None,
        missing_depots_info=None,
        library_path=None,
        refetched_depots=None,
        branch="public",
        branches=None,
        current_build_id="",
        lock_storage=False,
        dlc_only_mode=False,
        lock_dlc_only=False,
        is_atom_mode=False,
        **kwargs,
    ):
        super().__init__(parent)
        self._init_state(
            app_id, depots, game_name, header_url, selected_depots,
            show_storage, is_single_depot, library_path, refetched_depots,
            branch, branches, current_build_id, missing_hubcap_depots,
            missing_depots_info,
            lock_storage=lock_storage or kwargs.get("lock_storage", False),
            dlc_only_mode=dlc_only_mode or kwargs.get("dlc_only_mode", False),
            lock_dlc_only=lock_dlc_only or kwargs.get("lock_dlc_only", False),
            dlc_catalog=kwargs.get("dlc_catalog") or {},
            editions=kwargs.get("editions"),
            is_atom_mode=is_atom_mode or kwargs.get("is_atom_mode", False),
        )
        self.resize(700, 540)
        self.setMinimumSize(620, 480)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Dialog)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setStyleSheet("""
            QDialog {
                background-color: #121318;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.15);
            }
        """)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        from ui.dialog_titlebar import DialogTitleBar
        bar_title = "Select DLC Depots (AT0-M)" if (self._is_atom_mode and self._dlc_only_mode) else ("Configure AT0-M Depots" if self._is_atom_mode else "Select Depots to Download")
        self.title_bar = DialogTitleBar(
            self,
            title=bar_title,
            can_minimize=True,
            can_maximize=True,
            use_power_close=True,
        )
        root_layout.addWidget(self.title_bar)

        content_container = QWidget()
        layout = QVBoxLayout(content_container)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(10)
        root_layout.addWidget(content_container, 1)

        self.anchor_row = -1

        self._preload_cached_enrichments()
        self._load_settings()
        accent_r, accent_g, accent_b = self._resolve_accent_colors()
        self._build_header_section(layout)
        self._load_selection_state()

        content_widget = QVBoxLayout()
        content_widget.setContentsMargins(10, 0, 10, 0)

        self._build_depot_table(content_widget, accent_r, accent_g, accent_b)
        self._add_depot_rows(content_widget)
        self._build_bottom_bar(content_widget)
        layout.addLayout(content_widget)

        self._kick_off_enrichment_check()
        self._start_edition_prefetch()

    def _init_state(
        self, app_id, depots, game_name, header_url, selected_depots,
        show_storage, is_single_depot, library_path, refetched_depots,
        branch, branches, current_build_id, missing_hubcap_depots, missing_depots_info,
        lock_storage: bool = False,
        dlc_only_mode: bool = False,
        lock_dlc_only: bool = False,
        dlc_catalog: dict = None,
        editions: dict = None,
        **kwargs,
    ) -> None:
        """Store every constructor argument and wire up signals and timers.

        Split out of __init__ so the constructor reads as a layout spine. All
        attributes the rest of the class relies on are established here.
        """
        self._depots_enriched_signal.connect(self._on_depots_enriched)
        self._missing_contents_checked_signal.connect(self._on_missing_contents_check_finished)
        self._missing_depots_updated_signal.connect(self._on_missing_depots_updated)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setWindowTitle("Select Depots to Download")
        self.app_id = app_id
        self.depots = {k: v for k, v in (depots or {}).items() if str(k) != str(app_id)}
        self.game_name = game_name
        self.header_url = header_url
        self.selected_depots = selected_depots
        self.selected_files = []
        self.show_storage = show_storage
        self.is_single_depot = is_single_depot
        self.preferred_library_path = library_path
        self.lock_storage = lock_storage
        self.lock_dlc_only = lock_dlc_only
        self._is_atom_mode = bool(kwargs.get("is_atom_mode", False))
        self._settings = get_settings()
        self._dlc_only_mode = bool(
            dlc_only_mode
            or (self._settings.value(f"dlc_only_mode/{self.app_id}", False, type=bool) if self._settings else False)
        )
        self.refetched_depots = [str(d) for d in (refetched_depots or []) if str(d).strip()]
        self.branch = str(branch or "public")
        self.branches = dict(branches or {"public": {}})
        self.current_build_id = str(current_build_id or "").strip()
        if not self.current_build_id:
            self.current_build_id = self._resolve_local_buildid()
        if not self.current_build_id and self.depots:
            for d_data in self.depots.values():
                if isinstance(d_data, dict) and d_data.get("buildid"):
                    self.current_build_id = str(d_data["buildid"]).strip()
                    break
        self._selected_build_id = self.current_build_id
        self._is_build_pinned = False
        self._manifest_overrides: Dict[str, str] = {}
        self._recovered_depots_map: Dict[str, str] = {}
        self._is_checking_missing_contents = False
        self._spinner_angle = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.timeout.connect(self._on_spinner_tick)

        if isinstance(missing_hubcap_depots, dict):
            if not missing_depots_info:
                # The keys are still worth keeping: we know which depots are
                # missing even before any metadata arrives. The values are
                # reason strings, not per-depot metadata, so seed empty dicts
                # rather than aliasing the whole mapping.
                missing_depots_info = {str(d): {} for d in missing_hubcap_depots}
            self.missing_hubcap_depots = [str(d) for d in missing_hubcap_depots.keys() if str(d).strip()]
        else:
            self.missing_hubcap_depots = [str(d) for d in (missing_hubcap_depots or []) if str(d).strip()]

        if self.missing_hubcap_depots:
            self._is_checking_missing_contents = True
            self._spinner_timer.start(16)

        # Every consumer of this mapping treats the values as per-depot
        # metadata dicts (minfo.get("name"), minfo.get("size"), and the
        # isinstance(d_data, dict) platform checks). Keep that invariant even if
        # a caller hands us something else.
        self.missing_depots_info = {
            str(k): (dict(v) if isinstance(v, dict) else {})
            for k, v in (missing_depots_info or {}).items()
        }
        self.selected_storage_path = None

        try:
            from utils.dlc_helpers import get_owned_dlcs_from_steam, get_injected_dlcs_from_config
            self._steam_owned_dlcs = get_owned_dlcs_from_steam(self.app_id)
            self._injected_dlcs = get_injected_dlcs_from_config(self.app_id)
        except Exception as _dlc_init_err:
            logger.debug(f"[DepotSelectionDialog] Could not load owned/injected DLCs: {_dlc_init_err}")
            self._steam_owned_dlcs = set()
            self._injected_dlcs = set()

        self._injection_only = False
        self._config_written = False
        self.dlc_catalog = dlc_catalog or kwargs.get("dlc_catalog") or {}
        if editions or kwargs.get("editions"):
            self._multi_platform_editions = editions or kwargs.get("editions")

    def _load_selection_state(self) -> None:
        """Load DLC-only mode and whether a previous depot selection exists.

        Must run before the table is built: _populate_table reads these to
        decide what to preselect.
        """
        # Load DLC-only mode state
        self._dlc_only_mode = bool(
            self._dlc_only_mode
            or (self._settings.value(f"dlc_only_mode/{self.app_id}", False, type=bool) if self._settings else False)
        )

        # Check if saved depot selections exist from previous sessions
        if self.selected_depots is None and self._settings:
            saved_val = self._settings.value(f"depot_selection/{self.app_id}", "", type=str)
            if saved_val:
                try:
                    import json
                    saved_data = json.loads(saved_val)
                    if saved_data.get("selected"):
                        self.selected_depots = saved_data["selected"]
                except Exception:
                    pass

        self._has_saved_selection = bool(self.selected_depots is not None and len(self.selected_depots) > 0)
        self._user_interacted = False

    def get_selected_depots(self):
        if getattr(self, "_injection_only", False):
            return []
        selected = []
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None:
                continue
            role = id_item.data(Qt.ItemDataRole.UserRole + 2)
            if role in ("missing", "expander"):
                continue
            # Strictly exclude depotless DlcData items and Steam-owned depots from download queue
            is_depotless = bool(id_item.data(Qt.ItemDataRole.UserRole + 21))
            is_owned = bool(id_item.data(Qt.ItemDataRole.UserRole + 25))
            if is_depotless or is_owned:
                continue
            if id_item.checkState() == Qt.CheckState.Checked:
                depot_id = str(id_item.data(Qt.ItemDataRole.UserRole) or "").strip()
                if depot_id and depot_id not in selected:
                    selected.append(depot_id)
        return selected

    def get_selected_dlc_data(self) -> Dict[str, str]:
        """Returns {dlc_app_id: dlc_name} for checked DLC rows in Col 0 that are not owned legitimately."""
        checked = {}
        import re
        for r in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(r, 0)
            if not id_item:
                continue
            if id_item.checkState() != Qt.CheckState.Checked:
                continue
            if id_item.data(Qt.ItemDataRole.UserRole + 25):
                # Owned legitimately on Steam
                continue

            is_depotless = bool(id_item.data(Qt.ItemDataRole.UserRole + 21))
            dlc_id = str(id_item.data(Qt.ItemDataRole.UserRole + 1) or "").strip()
            raw_id = str(id_item.data(Qt.ItemDataRole.UserRole) or "").strip()

            target_dlc = ""
            if is_depotless:
                target_dlc = raw_id
            elif dlc_id and dlc_id in self.dlc_catalog:
                target_dlc = dlc_id
            elif raw_id and raw_id in self.dlc_catalog:
                target_dlc = raw_id

            if target_dlc and target_dlc != str(self.app_id):
                config_item = self.table_widget.item(r, 1)
                raw_name = self.dlc_catalog.get(target_dlc) or (config_item.text() if config_item else "") or f"DLC {target_dlc}"
                clean_name = re.sub(r"^\[(?:DLC|SOUNDTRACK|ARTBOOK|WALLPAPER|EN|BR|AR|DE|FR|JA|KO|RU|ES|ZH-CN|ZH-TW)\]\s*", "", raw_name).strip()
                checked[target_dlc] = clean_name or f"DLC {target_dlc}"
        return checked

    def get_config_written(self) -> bool:
        """Returns whether DLC configuration was written to SLSsteam config."""
        return getattr(self, "_config_written", False)


    def get_selected_files(self):
        """Returns the list of custom checked relative file paths."""
        return self.selected_files

    def get_dlc_only_mode(self) -> bool:
        """Returns whether DLC Only mode is enabled for this dialog."""
        return self._dlc_only_mode

    def accept(self):
        if getattr(self, "_is_atom_mode", False):
            if hasattr(self, "_handle_move_to_atom_clicked"):
                self._handle_move_to_atom_clicked()
                return

        selected_depots = self.get_selected_depots()
        checked_dlcs = self.get_selected_dlc_data()

        if not selected_depots and not checked_dlcs:
            ThemedMessageBox.warning(self, "No Depots Selected", "Please select at least one depot or DLC to proceed.")
            return

        # Auto-inject any checked DLC entitlements into SLSsteam config
        if checked_dlcs:
            try:
                from utils.yaml.dlc import add_dlc_data_batch
                from utils.yaml_config_manager import get_user_config_path, remove_additional_app

                config_path = get_user_config_path()
                if config_path and config_path.exists():
                    add_dlc_data_batch(config_path, self.app_id, checked_dlcs)
                    if getattr(self, "_dlc_only_mode", False) and not selected_depots:
                        remove_additional_app(config_path, self.app_id)
                        get_settings().setValue(f"dlc_only_mode/{self.app_id}", True)
                    self._config_written = True
            except Exception as e:
                logger.warning(f"[DepotSelectionDialog] Auto-injecting checked DLC data failed: {e}")

        # If only depotless DLCs were selected (no files to download)
        if not selected_depots:
            ThemedMessageBox.information(
                self,
                "DLC Mode Saved",
                f"DLC mode is enabled for '{self.game_name}'.\n\n"
                f"✓ Injected {len(checked_dlcs)} DLC(s) into SLSsteam config.\n"
                f"No downloads were queued because no downloadable depot files were selected.",
            )
            super().accept()
            return

        # 0. On-demand manifest generation for any selected recovered depots that lack files on disk
        if hasattr(self, "_recovered_depots_map") and self._recovered_depots_map:
            needs_gen = [
                did for did in selected_depots
                if did in self._recovered_depots_map and not self._is_manifest_on_disk(did, self._recovered_depots_map[did])
            ]
            if needs_gen:
                from ui.dialogs.themed_progress_dialog import ThemedProgressDialog
                from pathlib import Path
                import tempfile
                from utils.helpers import get_base_path
                from core import morrenus_api
                progress = ThemedProgressDialog(f"Generating manifest for {len(needs_gen)} recovered depot(s)...", None, 0, len(needs_gen), self)
                progress.setWindowModality(Qt.WindowModality.WindowModal)
                progress.show()
                for idx, r_did in enumerate(needs_gen):
                    r_mid = self._recovered_depots_map[r_did]
                    progress.setValue(idx)
                    progress.setLabelText(f"Generating manifest for depot {r_did}...")
                    QApplication.processEvents()
                    raw_bytes, g_err = morrenus_api.generate_single_manifest(r_did, r_mid)
                    if raw_bytes:
                        try:
                            from utils.manifest_resolver import sanitize_manifest_bytes
                            raw_bytes = sanitize_manifest_bytes(raw_bytes)
                        except Exception:
                            pass
                        for s_dir in [Path(tempfile.gettempdir()) / "mistwalker_manifests", Path(get_base_path()) / "manifests"]:
                            try:
                                s_dir.mkdir(parents=True, exist_ok=True)
                                (s_dir / f"{r_did}_{r_mid}.manifest").write_bytes(raw_bytes)
                            except Exception:
                                pass
                        self._manifest_overrides[str(r_did)] = str(r_mid)
                        logger.info(f"[DepotSelection] Generated manifest for recovered depot {r_did}_{r_mid}")
                    else:
                        logger.warning(f"[DepotSelection] Failed to generate manifest for recovered depot {r_did}: {g_err}")
                progress.close()

        # 1b. Missing executable pre-flight guard
        if not self._verify_executable_selection(selected_depots):
            return

        # Re-fetch selected depots in case user chose to add the executable depot
        selected_depots = self.get_selected_depots()

        # 2. Validate storage selection if enabled
        if self.show_storage and hasattr(self, "_storage_paths") and self._storage_paths:
            if not self.selected_storage_path:
                ThemedMessageBox.warning(self, "No Storage Selected", "Please select a storage location before proceeding.")
                return

        # 3. Promote header image to permanent cache since user is downloading
        if hasattr(self, "app_id") and self.app_id:
            ImageFetcher.promote_to_permanent_cache(self.app_id)

        super().accept()

    def closeEvent(self, a0):
        """Ensure image fetch and animation timer are cleaned up when dialog closes."""
        if hasattr(self, "_spinner_timer") and self._spinner_timer is not None:
            try:
                self._spinner_timer.stop()
            except Exception:
                pass
        if hasattr(self, "fetcher") and self.fetcher is not None:
            try:
                self.fetcher.stop()
            except RuntimeError:
                pass
        if hasattr(self, "retry_fetcher") and self.retry_fetcher is not None:
            try:
                self.retry_fetcher.stop()
            except RuntimeError:
                pass
        super().closeEvent(a0)

    def changeEvent(self, a0):
        super().changeEvent(a0)
        if a0 and a0.type() == QEvent.Type.WindowStateChange:
            if not self.isMinimized():
                self.raise_()
                self.activateWindow()

    def paintEvent(self, event):
        super().paintEvent(event)
        try:
            from PyQt6.QtGui import QPainter, QColor, QRadialGradient
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            w = self.width()
            center_x = w * 0.5
            center_y = 65.0
            radius = max(w * 0.65, 320.0)

            grad = QRadialGradient(center_x, center_y, radius)

            # Prioritize extracted thumbnail color for ambient background, falling back to accent_color
            base_col = getattr(self, "_ambient_color", None)
            if not base_col or not isinstance(base_col, QColor) or not base_col.isValid():
                accent_hex = getattr(self, "accent_color", "#C06C84") or "#C06C84"
                base_col = QColor(accent_hex)
                if not base_col.isValid():
                    base_col = QColor(192, 108, 132)

            # Subtle atmospheric glow (~10% center, ~4% mid, 0% edge)
            center_col = QColor(base_col.red(), base_col.green(), base_col.blue(), 26)
            mid_col = QColor(base_col.red(), base_col.green(), base_col.blue(), 10)
            outer_col = QColor(base_col.red(), base_col.green(), base_col.blue(), 0)

            grad.setColorAt(0.0, center_col)
            grad.setColorAt(0.45, mid_col)
            grad.setColorAt(1.0, outer_col)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(grad)
            painter.drawRect(self.rect())
            painter.end()
        except Exception:
            pass

    def showEvent(self, a0):
        super().showEvent(a0)
        self.raise_()
        self.activateWindow()
