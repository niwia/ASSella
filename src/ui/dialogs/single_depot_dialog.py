import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QIcon
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QComboBox,
    QFrame,
    QButtonGroup,
    QMessageBox,
    QApplication,
)

from ui.dialogs.depotselection import format_size
from utils.image_fetcher import ImageFetcher
from utils.logger import logger


class SingleDepotSelectionDialog(QDialog):
    """A sleek, compact download configuration dialog for games with only a single depot.
    
    Eliminates the large multi-depot table and dead space, presenting a tight card layout
    with depot details, selective file downloads, and storage library selection.
    """

    _depots_enriched_signal = pyqtSignal(dict)

    def __init__(
        self,
        app_id,
        game_name,
        depots,
        header_url=None,
        parent=None,
        selected_depots=None,
        show_storage=True,
        is_single_depot=True,
        missing_hubcap_depots=None,
        missing_depots_info=None,
        library_path=None,
        refetched_depots=None,
        branch="public",
        branches=None,
        current_build_id="",
        **kwargs,
    ):
        super().__init__(parent)
        self._depots_enriched_signal.connect(self._on_depots_enriched)
        self.setWindowTitle("Select Depots to Download")

        self.app_id = str(app_id)
        self.game_name = game_name or "Unknown Game"
        self.depots = dict(depots or {})
        self.header_url = header_url
        self.selected_files: List[str] = []
        self.show_storage = show_storage
        self.is_single_depot = True
        self.preferred_library_path = library_path
        self.refetched_depots = [str(d) for d in (refetched_depots or []) if str(d).strip()]
        self.missing_depots_nudge_frame = None
        self.missing_depots_text_lbl = None
        self.selected_storage_path: Optional[str] = None

        if isinstance(missing_hubcap_depots, dict):
            if not missing_depots_info:
                missing_depots_info = missing_hubcap_depots
            self.missing_hubcap_depots = [str(d) for d in missing_hubcap_depots.keys() if str(d).strip()]
        else:
            self.missing_hubcap_depots = [str(d) for d in (missing_hubcap_depots or []) if str(d).strip()]

        self.missing_depots_info = dict(missing_depots_info or {})

        # Load accent color & settings
        try:
            from utils.settings import get_settings
            self._settings = get_settings()
            self.accent_color = self._settings.value("accent_color", "#C06C84", type=str)
        except Exception:
            self._settings = None
            self.accent_color = "#C06C84"

        # Resolve the single depot ID and metadata
        if self.depots:
            self.single_depot_id = str(next(iter(self.depots.keys())))
            self.single_depot_data = self.depots.get(self.single_depot_id, {})
            if not isinstance(self.single_depot_data, dict):
                self.single_depot_data = {}
        else:
            self.single_depot_id = self.app_id
            self.single_depot_data = {}

        self.branch = str(branch or "public")
        self.branches = dict(branches or {"public": {}})
        self.current_build_id = str(current_build_id or self.single_depot_data.get("buildid") or "").strip()
        if not self.current_build_id:
            self.current_build_id = self._resolve_local_buildid()
        self._selected_build_id = self.current_build_id
        self._is_build_pinned = False
        self._manifest_overrides: Dict[str, str] = {}

        # Determine pre-checked state
        if selected_depots is not None:
            pre_selected_set = {str(d) for d in selected_depots}
            self._is_pre_checked = str(self.single_depot_id) in pre_selected_set
        else:
            self._is_pre_checked = True

        # Pre-apply any cached enrichments from database
        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            cached_enrichments = db.get_depot_enrichments(str(self.app_id))
            if cached_enrichments:
                self._apply_depot_enrichments(cached_enrichments)
        except Exception as e:
            logger.debug(f"[SingleDepot] Could not load cached enrichments: {e}")

        self._build_ui()

        # Start header image fetch
        self._fetch_header_image(self.app_id)

        # Async enrichment if generic or missing size
        needs_enrichment = (
            (not self.single_depot_data.get("size") and not self.single_depot_data.get("size_str"))
            or re.match(r"^(?:\[.*?\]\s*)?(?:Depot|DLC)\s+\d+$", str(self.single_depot_data.get("desc") or "").strip(), re.IGNORECASE)
            or not str(self.single_depot_data.get("desc") or "").strip()
        )
        if needs_enrichment and self.app_id not in ("0", "N/A", "unknown"):
            self._start_enrichment_async()

    def _build_ui(self):
        self.setMinimumWidth(520)
        self.setMaximumWidth(580)
        self.setStyleSheet("""
            QDialog {
                background-color: #12131a;
                color: #FFFFFF;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 12, 14, 14)
        main_layout.setSpacing(10)

        # --- 1. Header Row ---
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 2)
        header_layout.setSpacing(12)

        # Banner Thumbnail
        self.header_label = QLabel()
        self.header_label.setFixedSize(110, 52)
        self.header_label.setScaledContents(True)
        self.header_label.setStyleSheet("background-color: rgba(255, 255, 255, 0.04); border-radius: 6px;")
        header_layout.addWidget(self.header_label)

        # Title & Subtitle
        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)

        display_title = (
            f"{self.game_name} ({self.app_id})"
            if self.app_id and self.app_id not in ("0", "unknown", "None", "") and f"({self.app_id})" not in str(self.game_name)
            else self.game_name
        )
        self.title_label = QLabel(display_title)
        self.title_label.setStyleSheet("font-size: 12pt; font-weight: bold; color: #FFFFFF;")
        title_layout.addWidget(self.title_label)

        # --- Branch & Builds Row ---
        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)
        controls_row.setContentsMargins(0, 4, 0, 0)

        # Check saved branch if not explicitly given
        if not self.branch or self.branch == "public":
            try:
                from utils.settings import get_settings
                saved_b = get_settings().value(f"selected_branch/{self.app_id}", "", type=str)
                if saved_b:
                    self.branch = saved_b
            except Exception:
                pass

        branch_list = sorted(self.branches.keys(), key=lambda k: (0 if k == "public" else 1, k)) if self.branches else ["public"]
        if "public" not in branch_list:
            branch_list.insert(0, "public")
        if self.branch and self.branch not in branch_list:
            branch_list.append(self.branch)

        has_other_branches = any(b.lower() != "public" for b in branch_list)

        if has_other_branches:
            branch_lbl = QLabel("Branch:")
            branch_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.7);")
            controls_row.addWidget(branch_lbl)

            self.branch_combo = QComboBox()
            self.branch_combo.setFixedHeight(28)
            self.branch_combo.setMinimumWidth(110)
            self.branch_combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: rgba(255, 255, 255, 0.06);
                    border: 1px solid rgba(255, 255, 255, 0.16);
                    border-radius: 6px;
                    color: #FFFFFF;
                    padding: 2px 10px;
                    font-size: 8.5pt;
                    font-weight: 600;
                }}
                QComboBox:hover {{
                    border-color: rgba(255, 255, 255, 0.3);
                    background-color: rgba(255, 255, 255, 0.10);
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 18px;
                }}
                QComboBox QAbstractItemView {{
                    background-color: #1a1c23;
                    color: #FFFFFF;
                    selection-background-color: {self.accent_color};
                    border: 1px solid rgba(255, 255, 255, 0.15);
                }}
            """)
            self.branch_combo.addItems(branch_list)
            self.branch_combo.currentTextChanged.connect(self._on_branch_changed)
            if self.branch in branch_list:
                self.branch_combo.setCurrentText(self.branch)
            controls_row.addWidget(self.branch_combo)
        else:
            self.branch_combo = None
            branch_lbl = QLabel(f"Branch: <b style='color: #FFFFFF;'>{self.branch or 'public'}</b>")
            branch_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.7);")
            controls_row.addWidget(branch_lbl)

        controls_row.addSpacing(4)

        display_bid = self.current_build_id or "Latest"
        self.builds_btn = QPushButton(f"Build: {display_bid}")
        self.builds_btn.setFixedHeight(28)
        self.builds_btn.setMinimumWidth(110)
        self.builds_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_build_btn_style()
        self.builds_btn.clicked.connect(self._on_builds_clicked)
        controls_row.addWidget(self.builds_btn)

        if self.branch and self.branch != "public":
            self._on_branch_changed(self.branch)

        controls_row.addStretch()
        title_layout.addLayout(controls_row)

        header_layout.addLayout(title_layout, 1)

        main_layout.addLayout(header_layout)

        # --- 2. Compact Single Depot Card ---
        card_frame = QFrame()
        card_frame.setObjectName("single_depot_card")
        card_frame.setCursor(Qt.CursorShape.PointingHandCursor)
        card_frame.setStyleSheet("""
            QFrame#single_depot_card {
                background-color: rgba(255, 255, 255, 0.035);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 6px;
            }
            QFrame#single_depot_card:hover {
                background-color: rgba(255, 255, 255, 0.055);
                border-color: rgba(255, 255, 255, 0.20);
            }
        """)

        card_layout = QHBoxLayout(card_frame)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(10)

        # Checkbox with custom SVG checkmark
        from utils.color_utils import get_best_foreground_color
        tick_color = "black" if get_best_foreground_color(self.accent_color, "#000000", "#FFFFFF") == "#000000" else "white"
        tick_svg_path = os.path.join(tempfile.gettempdir(), f"assella_tick_{tick_color}.svg")
        if not os.path.exists(tick_svg_path):
            try:
                with open(tick_svg_path, "w", encoding="utf-8") as f:
                    f.write(
                        f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" '
                        f'fill="none" stroke="{tick_color}" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">'
                        f'<polyline points="20 6 9 17 4 12"></polyline></svg>'
                    )
            except Exception:
                pass

        self.depot_checkbox = QCheckBox()
        self.depot_checkbox.setChecked(self._is_pre_checked)
        self.depot_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.depot_checkbox.setStyleSheet(f"""
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid rgba(255, 255, 255, 0.3);
                background-color: rgba(255, 255, 255, 0.05);
            }}
            QCheckBox::indicator:hover {{
                border-color: {self.accent_color};
            }}
            QCheckBox::indicator:checked {{
                background-color: {self.accent_color};
                border-color: {self.accent_color};
                image: url("{tick_svg_path}");
            }}
        """)
        card_layout.addWidget(self.depot_checkbox)

        # Depot ID
        self.depot_id_lbl = QLabel(str(self.single_depot_id))
        self.depot_id_lbl.setStyleSheet("font-weight: bold; font-size: 9pt; color: #FFFFFF;")
        card_layout.addWidget(self.depot_id_lbl)

        # Depot Description / Name
        desc_text = self._get_depot_display_desc()
        self.depot_name_lbl = QLabel(desc_text)
        self.depot_name_lbl.setStyleSheet("font-size: 9pt; color: rgba(255, 255, 255, 0.85);")
        card_layout.addWidget(self.depot_name_lbl, 1)

        # Depot Size
        size_text = self._get_depot_display_size()
        self.depot_size_lbl = QLabel(size_text)
        self.depot_size_lbl.setStyleSheet("font-size: 9pt; color: rgba(255, 255, 255, 0.6); font-weight: 500;")
        card_layout.addWidget(self.depot_size_lbl)

        # Click on card toggles checkbox
        card_frame.mousePressEvent = lambda event: self.depot_checkbox.toggle()

        main_layout.addWidget(card_frame)

        # --- 4. Action Row 1: Select Files (Full Width) ---
        file_sel_layout = QHBoxLayout()
        file_sel_layout.setContentsMargins(0, 0, 0, 0)

        self.select_files_button = QPushButton("Select Files...")
        self.select_files_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_files_button.setFixedHeight(30)
        self.select_files_button.clicked.connect(self._on_select_files_clicked)
        self.select_files_button.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: rgba(255, 255, 255, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 8.5pt;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
                border-color: {self.accent_color};
                color: #FFFFFF;
            }}
        """)
        file_sel_layout.addWidget(self.select_files_button, 1)

        main_layout.addLayout(file_sel_layout)

        # --- 5. Action Row 2: Storage Selector + OK / Cancel ---
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(0, 2, 0, 0)
        bottom_bar.setSpacing(6)

        if self.show_storage:
            self._setup_storage_buttons(bottom_bar)
        else:
            bottom_bar.addStretch(1)

        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("ok_button")
        ok_btn.setFixedHeight(28)
        ok_btn.setMinimumWidth(80)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.clicked.connect(self._on_ok_clicked)
        from utils.color_utils import get_best_foreground_color
        btn_text_color = get_best_foreground_color(self.accent_color, dark_color="#111318", light_color="#FFFFFF")
        ok_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: {btn_text_color};
                border: none;
                border-radius: 4px;
                font-weight: bold;
                padding: 4px 14px;
            }}
            QPushButton:hover {{
                background-color: #FFFFFF;
                color: #111318;
            }}
        """)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancel_button")
        cancel_btn.setFixedHeight(28)
        cancel_btn.setMinimumWidth(80)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setStyleSheet("""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.05);
                color: rgba(255, 255, 255, 0.8);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                padding: 4px 14px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.1);
                color: #FFFFFF;
            }}
        """)

        bottom_bar.addWidget(ok_btn)
        bottom_bar.addWidget(cancel_btn)

        main_layout.addLayout(bottom_bar)

        # Shrink dialog snugly to fit content
        self.adjustSize()

    def _get_depot_display_desc(self) -> str:
        d_data = self.single_depot_data
        desc = str(d_data.get("desc") or d_data.get("name") or "").strip()
        if not desc or re.match(r"^(?:\[.*?\]\s*)?(?:Depot|DLC)\s+\d+$", desc, re.IGNORECASE):
            desc = f"{self.game_name} Content"
        return desc

    def _get_depot_display_size(self) -> str:
        d_data = self.single_depot_data
        if d_data.get("size"):
            try:
                return format_size(int(d_data["size"]))
            except (ValueError, TypeError):
                pass
        return str(d_data.get("size_str") or "0.00 B")

    def _setup_storage_buttons(self, layout: QHBoxLayout) -> None:
        import shutil
        from core.steam_helpers import get_steam_libraries, find_steam_install
        from utils.paths import is_valid_download_directory

        storage_paths = []
        def_dir = self._settings.value("default_download_directory", "", type=str) if self._settings else ""
        if def_dir and is_valid_download_directory(def_dir):
            storage_paths.append(os.path.realpath(def_dir))

        try:
            raw_libs = get_steam_libraries() or []
            for p in raw_libs:
                if p and is_valid_download_directory(p):
                    real_p = os.path.realpath(p)
                    if real_p not in storage_paths:
                        storage_paths.append(real_p)
        except Exception as e:
            logger.warning(f"[SingleDepot] Error discovering Steam storage libraries: {e}")

        self._storage_paths = storage_paths
        self._storage_btn_group = QButtonGroup(self)
        self._storage_btn_group.setExclusive(True)

        if not storage_paths:
            layout.addStretch(1)
            return

        def _format_storage_info(path_str: str):
            p = Path(path_str)
            try:
                free_bytes = shutil.disk_usage(path_str).free
                if free_bytes >= 1024**4:
                    free_str = f"{free_bytes / (1024**4):.1f} TB free"
                elif free_bytes >= 1024**3:
                    free_str = f"{free_bytes / (1024**3):.1f} GB free"
                elif free_bytes >= 1024**2:
                    free_str = f"{free_bytes / (1024**2):.0f} MB free"
                else:
                    free_str = f"{free_bytes} B free"
            except Exception:
                free_str = ""

            steam_root = find_steam_install()
            p_str_lower = path_str.lower()

            if steam_root and os.path.realpath(path_str) == os.path.realpath(steam_root):
                label = "Primary"
            elif "/.local/share/steam" in p_str_lower or "/.steam/steam" in p_str_lower:
                label = "Primary"
            elif "sdcard" in p_str_lower or "sd_card" in p_str_lower or "mmcblk" in p_str_lower or "/sd" in p_str_lower:
                label = "SD Card"
            else:
                label = p.name
                if label.lower() in ("steamlibrary", "steamapps", "common") and len(p.parts) > 1:
                    label = p.parts[-2]
                if len(label) > 14:
                    label = label[:12] + "…"

            tooltip = f"Storage: {path_str}" + (f"\nAvailable: {free_str}" if free_str else "")
            return label, tooltip

        from utils.color_utils import get_best_foreground_color
        text_hex = get_best_foreground_color(self.accent_color, dark_color="#111318", light_color="#FFFFFF")
        btn_style = f"""
            QPushButton {{
                background-color: transparent;
                color: rgba(255, 255, 255, 0.7);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                padding: 4px 10px;
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

        max_direct_buttons = 2
        direct_paths = storage_paths[:max_direct_buttons]

        for i, spath in enumerate(direct_paths):
            lbl_text, tip_text = _format_storage_info(spath)
            btn = QPushButton(lbl_text)
            btn.setCheckable(True)
            btn.setToolTip(tip_text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(btn_style)

            def _make_handler(target_path):
                def _handler():
                    self.selected_storage_path = target_path
                    logger.info(f"[SingleDepot] Storage library selected: {target_path}")
                return _handler

            btn.clicked.connect(_make_handler(spath))
            self._storage_btn_group.addButton(btn, i)
            layout.addWidget(btn)

        # Pre-select priority: preferred -> default -> first
        real_pref = os.path.realpath(self.preferred_library_path) if self.preferred_library_path else ""
        real_def = os.path.realpath(def_dir) if def_dir else ""
        if real_pref and real_pref in storage_paths:
            default_target = real_pref
        elif real_def and real_def in storage_paths:
            default_target = real_def
        else:
            default_target = storage_paths[0]

        self.selected_storage_path = default_target
        for i, spath in enumerate(direct_paths):
            if spath == default_target:
                btn = self._storage_btn_group.button(i)
                if btn:
                    btn.setChecked(True)
                break

        layout.addStretch(1)

    def _fetch_header_image(self, app_id):
        self._current_app_id = app_id
        url = ImageFetcher.get_header_image_url(app_id)
        self.fetcher = ImageFetcher(url, ephemeral=True)
        self.fetcher.finished.connect(self._on_image_fetched)
        self.fetcher.start()

    def _on_image_fetched(self, image_data):
        if image_data:
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            self.header_label.setPixmap(pixmap)
        else:
            self._show_no_image()

    def _show_no_image(self):
        from PyQt6.QtGui import QPainter, QColor, QFont
        pixmap = QPixmap(110, 52)
        pixmap.fill(QColor(25, 25, 35))
        painter = QPainter(pixmap)
        painter.setPen(QColor(255, 255, 255, 100))
        painter.setFont(QFont("Arial", 8))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "No Image")
        painter.end()
        self.header_label.setPixmap(pixmap)

    def _on_select_files_clicked(self):
        if not self.depot_checkbox.isChecked():
            QMessageBox.warning(self, "Warning", "Please check the depot first before selecting files.")
            return

        from utils.helpers import get_base_path
        app_id = self.app_id

        manifests_dir = get_base_path() / "hubcap_manifests"
        zips = list(manifests_dir.glob(f"accela_fetch_{app_id}.zip")) + \
               list(manifests_dir.glob(f"accela_fetch_{app_id}_*.zip"))
        if not zips:
            QMessageBox.critical(self, "Error", f"No manifest zip file found for AppID {app_id} in {manifests_dir}.")
            return
        zips.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        zip_path = str(zips[0])

        temp_dir = os.path.join(tempfile.gettempdir(), f"selective_manifests_{app_id}")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(temp_dir)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to extract manifest zip: {e}")
            return

        # Fallback depot key lookup
        depot_key = None
        lua_files = list(Path(temp_dir).glob("*.lua"))
        if lua_files:
            try:
                content = lua_files[0].read_text(encoding="utf-8", errors="ignore")
                m = re.search(rf'\["?{self.single_depot_id}"?\]\s*=\s*"(.*?)"', content)
                if m:
                    depot_key = m.group(1).strip()
            except Exception:
                pass

        if not depot_key:
            try:
                from managers.depot_key_manager import DepotKeyManager
                depot_key = DepotKeyManager().get_key(self.single_depot_id)
            except Exception:
                pass

        manifest_candidates = list(Path(temp_dir).glob(f"{self.single_depot_id}_*.manifest"))
        if not manifest_candidates:
            QMessageBox.critical(self, "Error", f"No manifest file found for depot {self.single_depot_id}.")
            return

        manifest_path = str(manifest_candidates[0])
        from ui.dialogs.selective_downloader import SelectiveFileDownloaderDialog
        dlg = SelectiveFileDownloaderDialog(
            manifest_path=manifest_path,
            depot_key=depot_key,
            depot_id=self.single_depot_id,
            parent=self
        )
        if dlg.exec():
            self.selected_files = dlg.get_selected_files()
            if self.selected_files:
                self.select_files_button.setText(f"Select Files... ({len(self.selected_files)} files selected)")
            else:
                self.select_files_button.setText("Select Files...")

    def _start_enrichment_async(self):
        from utils.task_runner import TaskRunner
        from core.steam_api import get_depot_info_from_api

        try:
            app_id_int = int(self.app_id)
        except (ValueError, TypeError):
            return

        def _fetch():
            # 1. Try local database cache first
            try:
                from managers.db_manager import DatabaseManager
                db = DatabaseManager()
                enrichments = db.get_depot_enrichments(str(self.app_id)) or {}
                if self.single_depot_id in enrichments:
                    return enrichments
                app_info = db.get_app_info(str(self.app_id))
                if app_info and app_info.get("depots"):
                    return app_info["depots"]
            except Exception:
                pass

            # 2. Query via steam_api (PICS / SteamCMD)
            try:
                info = get_depot_info_from_api(app_id_int)
                if info and isinstance(info, dict) and info.get("depots"):
                    return info["depots"]
            except Exception as e:
                logger.debug(f"[SingleDepot] API enrichment error: {e}")

            # 3. Fallback to SteamDB scraper if byparr is running
            try:
                from core.steamdb_scraper import ByparrManager, SteamDBScraper
                if ByparrManager.is_running():
                    scraper = SteamDBScraper()
                    depots_info = scraper.get_app_depots(str(self.app_id))
                    if depots_info:
                        return depots_info
            except Exception:
                pass
            return {}

        def _on_done(enriched):
            if enriched and isinstance(enriched, dict):
                self._depots_enriched_signal.emit(enriched)

        self._enrich_runner = TaskRunner(self)
        worker = self._enrich_runner.run(_fetch)
        worker.finished.connect(_on_done)

    def _on_depots_enriched(self, enriched_depots: dict):
        if not enriched_depots:
            return
        self._apply_depot_enrichments(enriched_depots)

    def _apply_depot_enrichments(self, enrichments: dict):
        d_info = enrichments.get(self.single_depot_id) or enrichments.get(int(self.single_depot_id))
        if d_info and isinstance(d_info, dict):
            self.single_depot_data.update(d_info)
            self.depots[self.single_depot_id] = self.single_depot_data
            if hasattr(self, "depot_name_lbl"):
                self.depot_name_lbl.setText(self._get_depot_display_desc())
            if hasattr(self, "depot_size_lbl"):
                self.depot_size_lbl.setText(self._get_depot_display_size())

    def _on_ok_clicked(self):
        if not self.depot_checkbox.isChecked():
            QMessageBox.warning(self, "No Depots Selected", "Please select the depot to proceed with download.")
            return
        self.accept()

    # Public API conforming with DepotSelectionDialog
    def get_selected_depots(self) -> List[str]:
        if self.depot_checkbox.isChecked():
            return [str(self.single_depot_id)]
        return []

    def get_selected_files(self) -> List[str]:
        return self.selected_files

    def get_selected_storage(self) -> Optional[str]:
        return self.selected_storage_path

    def get_selected_branch(self) -> str:
        if getattr(self, "branch_combo", None) is not None:
            return self.branch_combo.currentText().strip()
        return getattr(self, "branch", "public") or "public"

    def _resolve_local_buildid(self) -> str:
        """Attempt to resolve installed build ID from local appmanifest or QSettings."""
        aid = str(getattr(self, "app_id", "") or "").strip()
        if not aid or aid in ("0", "N/A", "unknown"):
            return ""
        try:
            from core.steam_helpers import get_steam_libraries
            from pathlib import Path
            import re
            for lib in get_steam_libraries():
                acf_path = Path(lib) / "steamapps" / f"appmanifest_{aid}.acf"
                if acf_path.is_file():
                    with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    m = re.search(r'"buildid"\s+"([^"]+)"', content)
                    if m and m.group(1).strip() and m.group(1).strip() != "0":
                        return m.group(1).strip()
        except Exception:
            pass

        try:
            from utils.settings import get_settings
            s = get_settings()
            stored = str(s.value(f"installed_buildid/{aid}", "")).strip()
            if stored and stored.isdigit() and stored != "0":
                return stored
        except Exception:
            pass
        return ""

    def get_selected_build(self) -> Optional[str]:
        return getattr(self, "_selected_build_id", None) or self.current_build_id

    def is_build_pinned(self) -> bool:
        return getattr(self, "_is_build_pinned", False)

    def get_manifest_overrides(self) -> Dict[str, str]:
        return getattr(self, "_manifest_overrides", {})

    def _on_branch_changed(self, new_branch: str):
        if not new_branch:
            return
        self.branch = new_branch
        b_info = self.branches.get(new_branch)
        new_bid = ""
        if isinstance(b_info, dict) and b_info.get("buildid"):
            new_bid = str(b_info["buildid"]).strip()
            if new_bid:
                self.current_build_id = new_bid
                self._selected_build_id = new_bid
                if hasattr(self, "builds_btn") and self.builds_btn is not None:
                    self.builds_btn.setText(f"Build: {new_bid}")

        self._is_build_pinned = (new_branch != "public")
        if hasattr(self, "builds_btn") and self.builds_btn is not None:
            self._update_build_btn_style()

        # Update manifest overrides for the selected branch
        from utils.branch_helpers import resolve_branch_manifest_gid
        self._manifest_overrides.clear()

        depots_source = self.depots if self.depots else {str(self.single_depot_id): self.single_depot_data}
        has_branch_manifests = any(
            isinstance(d, dict) and "manifests" in d for d in depots_source.values()
        )
        if not has_branch_manifests and self.app_id:
            try:
                from core.steam_api import get_depot_info_from_api
                pics_info = get_depot_info_from_api(self.app_id)
                if pics_info and pics_info.get("depots"):
                    depots_source = pics_info["depots"]
                    if str(self.single_depot_id) in depots_source and "manifests" in depots_source[str(self.single_depot_id)]:
                        self.single_depot_data["manifests"] = depots_source[str(self.single_depot_id)]["manifests"]
            except Exception as e:
                logger.debug(f"[SingleDepot] Could not fetch PICS depot info for branch manifests: {e}")

        if new_branch != "public":
            for did, d_info in depots_source.items():
                if str(did) == str(self.app_id) and len(depots_source) > 1:
                    continue
                gid = resolve_branch_manifest_gid(d_info, new_branch)
                if gid:
                    self._manifest_overrides[str(did)] = str(gid)
                    if str(did) == str(self.single_depot_id):
                        self.single_depot_data["manifest_id"] = str(gid)
            logger.info(f"[SingleDepot] Branch '{new_branch}' (Build {new_bid}) manifest overrides: {self._manifest_overrides}")
        else:
            for did, d_info in depots_source.items():
                if str(did) == str(self.app_id) and len(depots_source) > 1:
                    continue
                gid = resolve_branch_manifest_gid(d_info, "public")
                if gid:
                    if str(did) == str(self.single_depot_id):
                        self.single_depot_data["manifest_id"] = str(gid)
            logger.info(f"[SingleDepot] Switched to public branch (Build {new_bid})")

    def _update_build_btn_style(self):
        if getattr(self, "_is_build_pinned", False):
            self.builds_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.08);
                    border: 1px solid {self.accent_color};
                    border-radius: 6px;
                    color: {self.accent_color};
                    font-size: 8.5pt;
                    font-weight: bold;
                    padding: 2px 10px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.14);
                }}
            """)
        else:
            self.builds_btn.setStyleSheet("""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.06);
                    border: 1px solid rgba(255, 255, 255, 0.16);
                    border-radius: 6px;
                    color: #FFFFFF;
                    font-size: 8.5pt;
                    font-weight: 600;
                    padding: 2px 10px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.12);
                    border-color: rgba(255, 255, 255, 0.3);
                }}
            """)

    def _apply_build_selection(self, selected_bid: str, patch_depots: dict):
        if not selected_bid:
            return
        self._selected_build_id = selected_bid
        self.builds_btn.setText(f"Build: {selected_bid}")
        if self.current_build_id and selected_bid == self.current_build_id:
            self._is_build_pinned = False
        else:
            self._is_build_pinned = True
        self._update_build_btn_style()

        if patch_depots:
            for did, info in patch_depots.items():
                mid = info.get("manifest_id") if isinstance(info, dict) else str(info)
                if mid:
                    self._manifest_overrides[str(did)] = str(mid)
                    if str(did) == str(self.single_depot_id):
                        self.single_depot_data["manifest_id"] = str(mid)
                        logger.info(f"[SingleDepot] Overrode depot {did} manifest to {mid} for build {selected_bid}")

    def _on_builds_clicked(self):
        from core.steamdb_scraper import ByparrManager
        has_byparr = ByparrManager.find_byparr_dir() is not None

        if not has_byparr:
            from ui.dialogs.manual_manifest_dialog import ManualManifestDialog
            dlg = ManualManifestDialog(
                parent=self,
                app_id=self.app_id,
                game_name=self.game_name,
                depots_dict=self.depots,
                default_depot_id=self.single_depot_id,
                current_build_id=self.current_build_id,
                accent_color=self.accent_color,
            )
            if dlg.exec():
                selected_bid, patch_depots = dlg.get_selected_build()
                self._apply_build_selection(selected_bid, patch_depots)
        else:
            from ui.dialogs.build_selection_dialog import BuildSelectionDialog
            dlg = BuildSelectionDialog(
                parent=self,
                app_id=self.app_id,
                game_name=self.game_name,
                current_build_id=self.current_build_id,
                accent_color=self.accent_color,
                depots_dict=self.depots,
                default_depot_id=self.single_depot_id,
            )
            if dlg.exec():
                selected_bid, patch_depots = dlg.get_selected_build()
                self._apply_build_selection(selected_bid, patch_depots)

