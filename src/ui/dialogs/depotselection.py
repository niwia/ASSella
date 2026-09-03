import logging
import re
import os
import tempfile
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QMessageBox,
    QProgressDialog,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QSizePolicy,
)

from utils.image_fetcher import ImageFetcher
from utils.settings import get_settings
from ui.dialogs.dialog_helpers import create_standard_buttons

logger = logging.getLogger(__name__)


def _depot_matches_platform(depot_data: dict, platform: str) -> bool:
    """Check if a depot matches the given platform (linux/windows).

    A depot matches if:
    - Its oslist contains the platform name, OR
    - Its description tags contain [PLATFORM], OR
    - It has no oslist set (shared/common depot)
    """
    oslist = (depot_data.get("oslist") or "").lower()
    desc = (depot_data.get("desc") or "").lower()
    platform = platform.lower()

    # No oslist means it's a shared depot (common to all platforms)
    if not oslist:
        return True

    # Check oslist field (can be "windows", "linux", "windows,linux", etc.)
    if platform in oslist:
        return True

    # Check description tags like [LINUX], [WINDOWS]
    if f"[{platform}]" in desc:
        return True

    return False


def _depot_is_macos(depot_data: dict) -> bool:
    """Check if a depot is macOS-only."""
    oslist = (depot_data.get("oslist") or "").lower()
    desc = (depot_data.get("desc") or "").lower()

    # Check oslist
    if oslist in ("macosx", "macos"):
        return True

    # Check description tags
    if "[macos]" in desc or "[macosx]" in desc:
        return True

    return False


def _depot_is_android(depot_data: dict) -> bool:
    """Check if a depot is Android-only."""
    oslist = (depot_data.get("oslist") or "").lower()
    desc = (depot_data.get("desc") or "").lower()

    # Check oslist
    if oslist == "android":
        return True

    # Check description tags
    if "[android]" in desc:
        return True

    return False


def is_bonus_or_media_depot(depot_data: dict) -> bool:
    """Check if depot is soundtrack, wallpaper, artbook, manual, etc."""
    text = (
        (depot_data.get("desc") or "") + " " +
        (depot_data.get("name") or "")
    ).lower()
    
    bonus_keywords = [
        "soundtrack", " ost", "ost ", "(ost)", "[ost]", "original soundtrack", "bonus track",
        "wallpaper", "artbook", "art book", "manual", "guide", "strategy guide",
        "comic", "novel", "goodies", "avatar", "poster", "press kit", "bonus content",
        "extra content", "credits", "dedicated server", "server"
    ]
    for kw in bonus_keywords:
        if kw in text:
            return True
    return False


def get_smart_default_depots(depots: dict, target_platform: str = "linux", language: str = "english") -> list:
    """
    Intelligently pre-select depots for the user:
    1. If target_platform == "linux" and native Linux depots exist -> Target Linux + shared.
       If target_platform == "linux" and NO native Linux depots exist -> Target Windows + shared (for Proton).
       If target_platform == "windows" -> Target Windows + shared.
    2. Exclude macOS-only and Android-only depots.
    3. Exclude soundtracks, wallpapers, artbooks, manuals, bonus media by default.
    4. Exclude 32-bit depots if 64-bit depots exist for the target architecture.
    5. Prioritize selected language (English) if language-specific depots exist.
    6. Safety: fallback to non-macOS/non-Android depots if filtered list is empty.
    """
    if not depots:
        return []

    # Check if there are any native Linux depots
    has_linux = False
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict) or _depot_is_macos(d_data) or _depot_is_android(d_data):
            continue
        oslist = (d_data.get("oslist") or "").lower()
        desc = (d_data.get("desc") or "").lower()
        if "linux" in oslist or "[linux]" in desc:
            has_linux = True
            break

    if target_platform.lower() == "linux":
        active_platform = "linux" if has_linux else "windows"
    else:
        active_platform = "windows"

    # Check if 64-bit depots exist for active platform
    has_64 = False
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict):
            continue
        if not _depot_matches_platform(d_data, active_platform):
            continue
        osarch = str(d_data.get("osarch") or "").lower()
        desc = (d_data.get("desc") or "").lower()
        if osarch == "64" or "64-bit" in desc or "x64" in desc or "64 bit" in desc or "[64]" in desc:
            has_64 = True
            break

    # Check if language-specific depots exist
    has_lang_depots = False
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict):
            continue
        d_lang = (d_data.get("language") or "").lower()
        desc = (d_data.get("desc") or "").lower()
        if d_lang or any(f"[{l}]" in desc for l in ["english", "french", "german", "spanish", "italian", "japanese", "chinese", "russian", "korean"]):
            has_lang_depots = True
            break

    selected = []
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict):
            continue

        # 1. Skip macOS
        if _depot_is_macos(d_data):
            continue

        # 2. Skip bonus/media (soundtracks, wallpapers, artbooks)
        if is_bonus_or_media_depot(d_data):
            continue

        # 3. Match platform (Linux if native exists, else Windows + shared)
        if not _depot_matches_platform(d_data, active_platform):
            continue

        # 4. Filter 32-bit if 64-bit exists
        if has_64:
            osarch = str(d_data.get("osarch") or "").lower()
            desc = (d_data.get("desc") or "").lower()
            is_32 = osarch == "32" or "32-bit" in desc or "x86" in desc or "32 bit" in desc or "[32]" in desc or "[x86]" in desc
            if is_32:
                continue

        # 5. Language filtering if applicable
        if has_lang_depots:
            d_lang = (d_data.get("language") or "").lower()
            desc = (d_data.get("desc") or "").lower()
            if d_lang and d_lang != language.lower() and d_lang != "all":
                continue
            other_langs = ["french", "german", "spanish", "italian", "japanese", "chinese", "russian", "korean", "portuguese", "polish"]
            if language.lower() in other_langs:
                other_langs.remove(language.lower())
            if any(f"[{l}]" in desc for l in other_langs) and f"[{language.lower()}]" not in desc:
                continue

        selected.append(str(d_id))

    # Safety fallback: if everything got filtered out, fallback to basic non-macOS/non-Android matching
    if not selected:
        for d_id, d_data in depots.items():
            if isinstance(d_data, dict) and not _depot_is_macos(d_data) and not _depot_is_android(d_data):
                if _depot_matches_platform(d_data, active_platform):
                    selected.append(str(d_id))

    # If still empty, return all depot keys
    if not selected:
        selected = [str(k) for k in depots.keys()]

    return selected


def format_size(size_bytes):
    if not size_bytes:
        return "0.00 B"
    try:
        bytes_val = int(size_bytes)
        if bytes_val <= 0:
            return "0.00 B"
        for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
            if bytes_val < 1024.0:
                return f"{bytes_val:.2f} {unit}"
            bytes_val /= 1024.0
        return f"{bytes_val:.2f} PiB"
    except Exception:
        return "Unknown"


class NumericTableWidgetItem(QTableWidgetItem):
    def __init__(self, text, sort_value):
        super().__init__(text)
        self.sort_value = sort_value

    def __lt__(self, other):
        if isinstance(other, NumericTableWidgetItem):
            return self.sort_value < other.sort_value
        return super().__lt__(other)


class DepotSelectionDialog(QDialog):
    def __init__(
        self,
        app_id,
        game_name,
        depots,
        header_url,
        parent=None,
        selected_depots=None,
        show_storage=True,
    ):
        super().__init__(parent)
        self.setWindowTitle("Select Depots to Download")
        self.depots = depots
        self.app_id = app_id
        self.game_name = game_name
        self.header_url = header_url
        self.selected_depots = selected_depots
        self.selected_files = []
        self.show_storage = show_storage
        self.selected_storage_path = None
        self.resize(650, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(10)

        self.anchor_row = -1

        # Check if we should hide macOS & Android depots
        try:
            from utils.settings import get_settings
            settings = get_settings()
            self._hide_macos = settings.value("hide_macos_depots", True, type=bool)
            self._hide_android = settings.value("hide_android_depots", True, type=bool)
            self.accent_color = settings.value("accent_color", "#C06C84", type=str)
        except Exception:
            self._hide_macos = True
            self._hide_android = True
            self.accent_color = "#C06C84"

        # Dynamically generate solid dark container color for selection background
        from utils.color_utils import get_dark_container_color
        sel_bg_hex = get_dark_container_color(self.accent_color)

        # Parse hex to RGB
        hex_c = self.accent_color.lstrip('#')
        try:
            accent_r = int(hex_c[0:2], 16)
            accent_g = int(hex_c[2:4], 16)
            accent_b = int(hex_c[4:6], 16)
        except Exception:
            accent_r, accent_g, accent_b = 192, 108, 132

        # Header Layout
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(15, 10, 15, 10)
        header_layout.setSpacing(15)

        # Thumbnail Label
        self.header_label = QLabel()
        self.header_label.setFixedSize(120, 56)
        self.header_label.setScaledContents(True)
        self.header_label.setStyleSheet("background-color: rgba(255, 255, 255, 0.04); border-radius: 6px;")
        header_layout.addWidget(self.header_label)

        # Title / Info Layout
        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)

        self.title_label = QLabel(self.game_name)
        self.title_label.setStyleSheet("font-size: 13pt; font-weight: bold; color: #FFFFFF;")

        self.subtitle_label = QLabel("Select depots and configurations to download")
        self.subtitle_label.setStyleSheet("font-size: 9pt; color: rgba(255, 255, 255, 0.588);")

        title_layout.addWidget(self.title_label)
        title_layout.addWidget(self.subtitle_label)

        header_layout.addLayout(title_layout)
        header_layout.addStretch()

        layout.addLayout(header_layout)
        layout.addSpacing(5)

        self._fetch_header_image(app_id)

        # Load DLC-only mode state
        try:
            self._settings = settings if settings is not None else get_settings()
        except Exception:
            self._settings = None
        self._dlc_only_mode = (
            self._settings.value(f"dlc_only_mode/{self.app_id}", False, type=bool)
            if self._settings else False
        )

        content_widget = QVBoxLayout()
        content_widget.setContentsMargins(10, 0, 10, 0)

        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(3)
        self.table_widget.setHorizontalHeaderLabels(["ID", "Configuration", "Size"])
        self.table_widget.verticalHeader().setVisible(False)
        self.table_widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table_widget.setShowGrid(False)
        self.table_widget.setAlternatingRowColors(True)

        self.table_widget.setStyleSheet(f"""
            QTableWidget {{
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.047);
                border-radius: 8px;
                gridline-color: transparent;
                outline: 0;
                color: #FFFFFF;
            }}
            QTableWidget::item {{
                padding: 6px 10px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.02);
            }}
            QTableWidget::item:hover {{
                background-color: rgba({accent_r}, {accent_g}, {accent_b}, 0.047);
            }}
            QTableWidget::item:selected {{
                background-color: {sel_bg_hex} !important;
                color: #FFFFFF !important;
            }}
            QHeaderView::section {{
                background-color: rgba(255, 255, 255, 0.031);
                color: rgba(255, 255, 255, 0.235);
                padding: 6px 10px;
                border: none;
                font-size: 8.5pt;
                font-weight: bold;
                text-transform: uppercase;
            }}
            QTableWidget::indicator, QTableView::indicator {{
                width: 14px;
                height: 14px;
                background: transparent;
                border: 1.5px solid rgba({accent_r}, {accent_g}, {accent_b}, 0.47);
                border-radius: 4px;
            }}
            QTableWidget::indicator:unchecked, QTableView::indicator:unchecked {{
                background-color: transparent;
            }}
            QTableWidget::indicator:checked, QTableView::indicator:checked {{
                background-color: {self.accent_color};
                border: 1.5px solid {self.accent_color};
            }}
            QTableWidget::indicator:hover, QTableView::indicator:hover {{
                border: 1.5px solid rgba({accent_r}, {accent_g}, {accent_b}, 1.0);
                background-color: rgba({accent_r}, {accent_g}, {accent_b}, 0.078);
            }}
        """)

        header = self.table_widget.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table_widget.setColumnWidth(0, 110)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        def get_sort_key(depot_item):
            _depot_id, data = depot_item

            os_val = data.get("oslist")
            if os_val is None:
                os_str = "zzzz"
            else:
                os_str = os_val.lower()

            os_priority = 4

            if os_str == "windows":
                os_priority = 1
            elif os_str == "linux":
                os_priority = 2
            elif "all" in os_str:
                os_priority = 3
            elif os_str == "macosx" or os_str == "macos":
                os_priority = 5

            desc_str = data.get("desc", "").lower()
            lang_val = data.get("language")

            lang_priority = 3
            lang_sort_key = lang_val.lower() if lang_val else "zzzz"

            is_no_language = (
                lang_val is None
                and "english" not in desc_str
                and "japanese" not in desc_str
            )

            if "english" in desc_str:
                lang_priority = 1
                lang_sort_key = lang_val.lower() if lang_val else "english"
            elif is_no_language:
                lang_priority = 1
                lang_sort_key = "english"
            elif "japanese" in desc_str:
                lang_priority = 2
                lang_sort_key = "japanese"

            final_key = (os_priority, lang_priority, lang_sort_key)
            logger.debug(
                f"Depot {_depot_id}: OS='{os_val}', Lang='{lang_val}', Desc='{data.get('desc', '')}'"
            )
            logger.debug(
                f"    -> Key: {final_key} (OS_Prio: {os_priority}, Lang_Prio: {lang_priority}, Lang_Key: '{lang_sort_key}')"
            )

            return final_key

        logger.debug("--- Starting Depot Sort ---")
        sorted_depots = sorted(self.depots.items(), key=get_sort_key)
        logger.debug("--- Depot Sort Finished ---")

        is_first_depot = True
        row_idx = 0

        # Set maximum possible row count, we will resize it dynamically
        self.table_widget.setRowCount(len(sorted_depots))

        # Determine initial selection: if selected_depots is provided, use it; otherwise compute smart defaults
        if self.selected_depots is not None:
            pre_selected_set = set(str(d) for d in self.selected_depots)
        else:
            pre_selected_set = set(get_smart_default_depots(self.depots, target_platform="linux"))

        for depot_id, depot_data in sorted_depots:
            # Filter out macOS depots if setting is enabled
            if self._hide_macos and _depot_is_macos(depot_data):
                logger.debug(f"Hiding macOS depot {depot_id}")
                continue

            # Filter out Android depots if setting is enabled
            if self._hide_android and _depot_is_android(depot_data):
                logger.debug(f"Hiding Android depot {depot_id}")
                continue

            original_desc = depot_data["desc"]

            original_desc = re.sub(
                r"\s*-\s*Depot\s*" + re.escape(depot_id),
                "",
                original_desc,
                flags=re.IGNORECASE,
            )

            tags = ""
            base_desc = original_desc.strip()
            tags_match = re.match(r"^((?:\[.*?]\s*)*)(.*)", original_desc)
            if tags_match:
                tags = tags_match.group(1).strip()
                base_desc = tags_match.group(2).strip()

            is_generic_fallback = bool(
                re.fullmatch(r"Depot \d+", base_desc, re.IGNORECASE)
            )

            if is_first_depot:
                if is_generic_fallback:
                    final_desc = f"{self.game_name}".strip()
                else:
                    final_desc = base_desc

                is_first_depot = False
            else:
                if is_generic_fallback:
                    final_desc = ""
                else:
                    final_desc = base_desc

            # Clean DLC tags from final_desc
            final_desc = re.sub(r"^DLC\s+\d+\s*-?\s*", "", final_desc, flags=re.IGNORECASE).strip()

            # Generate dynamic OS tags
            oslist = (depot_data.get("oslist") or "").lower()
            os_tag = ""
            if oslist == "windows":
                os_tag = "[Windows]"
            elif oslist == "linux":
                os_tag = "[Linux]"
            elif oslist in ("macos", "macosx"):
                os_tag = "[macOS]"
            elif "windows" in oslist and "linux" in oslist:
                os_tag = "[Windows, Linux]"
            elif "all" in oslist:
                os_tag = "[All]"

            display_tags = tags if tags else os_tag
            if display_tags:
                config_text = f"{display_tags}  {final_desc}".strip()
            else:
                config_text = final_desc.strip()

            if not config_text or config_text == display_tags.strip():
                config_text = f"{display_tags}  Depot {depot_id}".strip() if display_tags else f"Depot {depot_id}"

            size_str = ""
            if depot_data.get("size"):
                try:
                    size_bytes = int(depot_data["size"])
                    size_str = format_size(size_bytes)
                except (ValueError, TypeError):
                    size_str = "0.00 B"

            # Use NumericTableWidgetItem for proper numeric sorting
            id_val = int(depot_id) if depot_id.isdigit() else 0
            id_item = NumericTableWidgetItem(depot_id, id_val)
            id_item.setData(Qt.ItemDataRole.UserRole, depot_id)
            id_item.setData(Qt.ItemDataRole.UserRole + 1, depot_id)
 
            is_checked = str(depot_id) in pre_selected_set
            id_item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
 
            # Make item non-editable and disable internal checkbox handling (handled manually on cell click)
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsUserCheckable)
             
            config_item = QTableWidgetItem(config_text)
            config_item.setFlags(config_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
             
            raw_size = int(depot_data.get("size") or 0)
            size_item = NumericTableWidgetItem(size_str, raw_size)
            size_item.setFlags(size_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
 
            self.table_widget.setItem(row_idx, 0, id_item)
            self.table_widget.setItem(row_idx, 1, config_item)
            self.table_widget.setItem(row_idx, 2, size_item)
            row_idx += 1
 
        self.table_widget.setRowCount(row_idx)
        self.table_widget.setSortingEnabled(True)
 
        # Makes list widget update stylesheets for the items
        QApplication.processEvents()
 
        content_widget.addWidget(self.table_widget)
 
        self.table_widget.cellClicked.connect(self.on_depot_cell_clicked)

        # Platform + Select/Deselect buttons in a single row
        button_layout = QHBoxLayout()
        button_layout.setSpacing(4)

        linux_button = QPushButton("Linux")
        linux_button.setToolTip("Smart select Linux installation (Native Linux if available, or Windows + shared for Proton; excludes media/32-bit)")
        linux_button.clicked.connect(lambda: self._select_platform("linux"))
        button_layout.addWidget(linux_button)

        windows_button = QPushButton("Windows")
        windows_button.setToolTip("Smart select Windows installation (Windows + shared; excludes media/32-bit)")
        windows_button.clicked.connect(lambda: self._select_platform("windows"))
        button_layout.addWidget(windows_button)

        select_all_button = QPushButton("All")
        select_all_button.clicked.connect(
            lambda: self._toggle_all_checkboxes(check=True)
        )
        button_layout.addWidget(select_all_button)

        deselect_all_button = QPushButton("None")
        deselect_all_button.clicked.connect(
            lambda: self._toggle_all_checkboxes(check=False)
        )
        button_layout.addWidget(deselect_all_button)
        content_widget.addLayout(button_layout)

        # Custom File Selection + DLC Only button row
        file_sel_layout = QHBoxLayout()
        file_sel_layout.setSpacing(6)

        select_files_button = QPushButton("Select Files...")
        select_files_button.setToolTip("Customize downloaded files within the selected depots")
        select_files_button.clicked.connect(self._on_select_files_clicked)
        select_files_button.setStyleSheet("font-weight: bold; padding: 4px;")
        file_sel_layout.addWidget(select_files_button)

        self._dlc_only_btn = QPushButton("DLC Only")
        self._dlc_only_btn.setToolTip(
            "Only select this if you own the base game separately.\n"
            "Update checks will only compare the depots you select here."
        )
        self._dlc_only_btn.setCheckable(True)
        self._dlc_only_btn.setChecked(self._dlc_only_mode)
        self._dlc_only_btn.clicked.connect(self._on_dlc_only_toggled)
        self._refresh_dlc_only_style()
        file_sel_layout.addWidget(self._dlc_only_btn)

        content_widget.addLayout(file_sel_layout)

        # Bottom row: Storage Selection (Left, Expanding) + OK / Cancel (Right)
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(0, 4, 0, 0)
        bottom_bar.setSpacing(6)

        if self.show_storage:
            self._setup_storage_buttons(bottom_bar)
        else:
            bottom_bar.addStretch(1)

        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("ok_button")
        ok_btn.setFixedHeight(28)
        ok_btn.setMinimumWidth(80)
        ok_btn.clicked.connect(self.accept)
        ok_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: #111318;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                padding: 4px 14px;
            }}
            QPushButton:hover {{
                background-color: #FFFFFF;
            }}
        """)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancel_button")
        cancel_btn.setFixedHeight(28)
        cancel_btn.setMinimumWidth(80)
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

        content_widget.addLayout(bottom_bar)

        layout.addLayout(content_widget)

    def _setup_storage_buttons(self, layout: QHBoxLayout) -> None:
        import shutil
        from core.steam_helpers import get_steam_libraries, find_steam_install

        storage_paths = []
        def_dir = self._settings.value("default_download_directory", "", type=str) if self._settings else ""
        if def_dir and os.path.isdir(def_dir):
            storage_paths.append(def_dir)

        try:
            raw_libs = get_steam_libraries() or []
            for p in raw_libs:
                if p and os.path.isdir(p) and p not in storage_paths:
                    storage_paths.append(p)
        except Exception as e:
            logger.warning(f"Error discovering Steam storage libraries: {e}")

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

        def _get_storage_btn_style():
            from utils.color_utils import get_best_foreground_color
            text_hex = get_best_foreground_color(self.accent_color, dark_color="#111318", light_color="#FFFFFF")
            return f"""
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

        self._storage_buttons = {}
        self._more_storage_combo = None

        max_direct_buttons = 3 if len(storage_paths) <= 3 else 2

        for i in range(min(len(storage_paths), max_direct_buttons)):
            spath = storage_paths[i]
            lbl_text, tip_text = _format_storage_info(spath)
            btn = QPushButton(lbl_text)
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setToolTip(tip_text)
            btn.setStyleSheet(_get_storage_btn_style())

            def _make_handler(target_path=spath, target_btn=btn):
                def _on_clicked():
                    if target_btn.isChecked():
                        self.selected_storage_path = target_path
                        if self._more_storage_combo:
                            self._more_storage_combo.setCurrentIndex(0)
                return _on_clicked

            btn.clicked.connect(_make_handler())
            self._storage_btn_group.addButton(btn, i)
            self._storage_buttons[spath] = btn
            layout.addWidget(btn, 1)

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
                lbl_text, tip_text = _format_storage_info(spath)
                self._more_storage_combo.addItem(lbl_text, spath)

            def _on_combo_changed(index):
                if index > 0:
                    chosen_path = self._more_storage_combo.itemData(index)
                    if chosen_path:
                        self.selected_storage_path = chosen_path
                        checked_btn = self._storage_btn_group.checkedButton()
                        if checked_btn:
                            self._storage_btn_group.setExclusive(False)
                            checked_btn.setChecked(False)
                            self._storage_btn_group.setExclusive(True)

            self._more_storage_combo.currentIndexChanged.connect(_on_combo_changed)
            layout.addWidget(self._more_storage_combo, 1)

        # Pre-select default download directory or first available library
        default_target = def_dir if (def_dir and def_dir in storage_paths) else storage_paths[0]
        self.selected_storage_path = default_target
        if default_target in self._storage_buttons:
            self._storage_buttons[default_target].setChecked(True)
        elif self._more_storage_combo:
            for idx in range(1, self._more_storage_combo.count()):
                if self._more_storage_combo.itemData(idx) == default_target:
                    self._more_storage_combo.setCurrentIndex(idx)
                    break

    def on_depot_cell_clicked(self, row, col):
        id_item = self.table_widget.item(row, 0)
        if id_item is None:
            return

        modifiers = QApplication.keyboardModifiers()
        current_state = id_item.checkState()
        new_state = (
            Qt.CheckState.Unchecked
            if current_state == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

        # Toggle the checkbox in the first column
        id_item.setCheckState(new_state)

        if modifiers == Qt.KeyboardModifier.ShiftModifier:
            if self.anchor_row == -1:
                self.anchor_row = row
            else:
                anchor_item = self.table_widget.item(self.anchor_row, 0)
                target_state = anchor_item.checkState() if anchor_item else new_state

                start_row = min(self.anchor_row, row)
                end_row = max(self.anchor_row, row)

                self.table_widget.blockSignals(True)
                for r in range(start_row, end_row + 1):
                    r_item = self.table_widget.item(r, 0)
                    if r_item is not None:
                        r_item.setCheckState(target_state)
                self.table_widget.blockSignals(False)
        else:
            self.anchor_row = row

    def _toggle_all_checkboxes(self, check=True):
        state = Qt.CheckState.Checked if check else Qt.CheckState.Unchecked
        self.table_widget.blockSignals(True)
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is not None:
                id_item.setCheckState(state)
        self.table_widget.blockSignals(False)

        self.anchor_row = -1

    def _select_platform(self, platform: str):
        """Smart select depots matching a platform (linux/windows), including shared depots and filtering out bonus media/32-bit."""
        smart_depots = set(get_smart_default_depots(self.depots, target_platform=platform))

        self.table_widget.blockSignals(True)
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None:
                continue
            depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
            if depot_id in smart_depots:
                id_item.setCheckState(Qt.CheckState.Checked)
            else:
                id_item.setCheckState(Qt.CheckState.Unchecked)
        self.table_widget.blockSignals(False)
        self.anchor_row = -1

    def _fetch_header_image(self, app_id):
        self._current_app_id = app_id
        url = ImageFetcher.get_header_image_url(app_id)
        self.fetcher = ImageFetcher(url, ephemeral=True)
        self.fetcher.finished.connect(self.on_image_fetched)
        self.fetcher.finished.connect(self._cleanup_fetcher)
        self.fetcher.start()

    def on_image_fetched(self, image_data):
        if image_data:
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            self._apply_header_pixmap(pixmap)
        else:
            # Image fetch failed (404), try to get the correct URL from Steam API
            logger.debug("Image fetch failed, attempting to refresh from API")
            self._trigger_header_refresh()

    def _trigger_header_refresh(self):
        """
        Fetch the correct header URL from Steam API when generic URL fails.
        """
        app_id = getattr(self, "_current_app_id", None)
        if not app_id:
            self._show_no_image()
            return

        logger.debug(f"Fetching header URL from Steam API for appid {app_id}")

        try:
            # Fetch the correct URL from Steam API (synchronous but fast)
            api_url = ImageFetcher.fetch_header_from_web_api(app_id)

            if api_url:
                logger.info(f"Got header URL from API for appid {app_id}: {api_url}")

                # Update database with fresh URL
                try:
                    from managers.db_manager import DatabaseManager

                    db = DatabaseManager()
                    db.upsert_app_info(app_id, {"header_url": api_url})
                except Exception as e:
                    logger.debug(f"Could not update DB: {e}")

                # Re-fetch the image with the correct URL
                self.retry_fetcher = ImageFetcher(api_url)
                self.retry_fetcher.finished.connect(self._on_retry_image_fetched)
                self.retry_fetcher.finished.connect(self._cleanup_retry_fetcher)
                self.retry_fetcher.start()
            else:
                logger.debug(f"No header URL found in API for appid {app_id}")
                self._show_no_image()
        except Exception as e:
            logger.warning(f"Failed to refresh header for appid {app_id}: {e}")
            self._show_no_image()

    def _on_retry_image_fetched(self, image_data):
        """Handle the retry image fetch result."""
        if image_data:
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            self._apply_header_pixmap(pixmap)
            logger.info("Successfully loaded header image after refresh")
        else:
            self._show_no_image()

    def _apply_header_pixmap(self, pixmap: QPixmap) -> None:
        scaled = pixmap.scaled(
            120, 56, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
        )
        self.header_label.setPixmap(scaled)
        self.header_label.setStyleSheet("border-radius: 6px;")

    def _show_no_image(self):
        """Show fallback text when image is not available."""
        self.header_label.setText("No Image")
        self.header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_label.setStyleSheet(
            "background-color: rgba(255, 255, 255, 0.039); "
            "color: rgba(255, 255, 255, 0.314); "
            "font-size: 8pt; "
            "border-radius: 6px;"
        )

    def _cleanup_fetcher(self, _data: bytes) -> None:
        if hasattr(self, "fetcher") and self.fetcher is not None:
            self.fetcher.deleteLater()
            self.fetcher = None

    def _cleanup_retry_fetcher(self, _data: bytes) -> None:
        if hasattr(self, "retry_fetcher") and self.retry_fetcher is not None:
            self.retry_fetcher.deleteLater()
            self.retry_fetcher = None

    def get_selected_depots(self):
        selected = []
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is None:
                continue
            if id_item.checkState() == Qt.CheckState.Checked:
                selected.append(id_item.data(Qt.ItemDataRole.UserRole))
        return selected

    def get_selected_files(self):
        """Returns the list of custom checked relative file paths."""
        return self.selected_files

    def _refresh_dlc_only_style(self) -> None:
        """Update the DLC Only button style to reflect its on/off state."""
        active = self._dlc_only_btn.isChecked()
        from utils.color_utils import get_best_foreground_color
        if active:
            # Active state: Solid accent background with high-contrast text color
            text_hex = get_best_foreground_color(self.accent_color, dark_color="#121214", light_color="#FFFFFF")
            self._dlc_only_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.accent_color} !important;
                    color: {text_hex} !important;
                    border: 1px solid {self.accent_color} !important;
                    border-radius: 4px;
                    padding: 4px 10px;
                    font-weight: bold;
                }}
            """)
        else:
            # Inverted / outline style: Transparent background, accent hover
            self._dlc_only_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: rgba(255, 255, 255, 0.6);
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 4px;
                    padding: 4px 10px;
                }}
                QPushButton:hover {{
                    border-color: {self.accent_color};
                    color: {self.accent_color};
                }}
            """)

    def _on_dlc_only_toggled(self) -> None:
        """Toggle DLC Only mode and persist the setting."""
        self._dlc_only_mode = self._dlc_only_btn.isChecked()
        self._refresh_dlc_only_style()
        if self._settings:
            self._settings.setValue(f"dlc_only_mode/{self.app_id}", self._dlc_only_mode)

    def get_dlc_only_mode(self) -> bool:
        """Returns whether DLC Only mode is enabled for this dialog."""
        return self._dlc_only_mode

    def _on_select_files_clicked(self):
        # 1. Get chosen depots
        chosen_depots = []
        for i in range(self.table_widget.rowCount()):
            id_item = self.table_widget.item(i, 0)
            if id_item is not None and id_item.checkState() == Qt.CheckState.Checked:
                depot_id = str(id_item.data(Qt.ItemDataRole.UserRole))
                chosen_depots.append(depot_id)

        if not chosen_depots:
            QMessageBox.warning(self, "Warning", "Please select at least one depot first.")
            return

        # Use the first checked depot for file list customization
        target_depot = chosen_depots[0]

        # 2. Locate the manifest zip for this app
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

        # 3. Extract target manifests
        import zipfile
        temp_dir = os.path.join(tempfile.gettempdir(), f"selective_manifests_{app_id}")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to extract manifest zip: {e}")
            return

        # Extract key from LUA config or fallback to depot_keys.db
        depot_key = None
        lua_files = list(Path(temp_dir).glob("*.lua"))
        if lua_files:
            try:
                with open(str(lua_files[0]), "r", encoding="utf-8") as lf:
                    lua_content = lf.read()
                    match = re.search(r"addappid\(\s*" + re.escape(target_depot) + r"\s*,\s*\d+\s*,\s*\"([a-fA-F0-9]+)\"\)", lua_content)
                    if match:
                        depot_key = match.group(1)
            except Exception as e:
                logger.warning(f"Failed to parse LUA for depot keys: {e}")

        # Fallback to depot_keys.db if key was not in LUA file (e.g. smart generate bundle)
        if not depot_key:
            try:
                from managers.depot_key_manager import DepotKeyManager
                dkm = DepotKeyManager()
                cached = dkm.get_depot_keys(app_id)
                if target_depot in cached:
                    depot_key = cached[target_depot]
            except Exception as dkm_e:
                logger.warning(f"Failed to load key from depot_keys.db for depot {target_depot}: {dkm_e}")

        if not depot_key:
            QMessageBox.critical(self, "Error", f"Could not find depot key for depot {target_depot} in LUA config or local key database.")
            return

        # Create depot keys file
        keys_path = os.path.join(temp_dir, "depot.keys")
        try:
            with open(keys_path, "w") as kf:
                kf.write(f"{target_depot};{depot_key}\n")
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Failed to write keys file: {e}")
            return

        # Locate the manifest file and manifest ID
        manifest_files = list(Path(temp_dir).glob(f"{target_depot}_*.manifest"))
        if not manifest_files:
            # Fallback check if it was zipped without depot ID prefix
            manifest_files = list(Path(temp_dir).glob("*.manifest"))

        if not manifest_files:
            QMessageBox.critical(self, "Error", f"No manifest file (*.manifest) found for depot {target_depot} in the extracted manifest bundle.")
            return

        manifest_path = manifest_files[0]
        manifest_file = str(manifest_path)

        filename = manifest_path.name
        stem = filename.replace(".manifest", "")
        if "_" in stem:
            manifest_id = stem.split("_", 1)[1]
        else:
            manifest_id = stem

        # Dump manifest files using DDM in background progress
        progress_dialog = QProgressDialog("Loading file list from manifest...", "Cancel", 0, 0, self)
        progress_dialog.setWindowTitle("Loading Manifest")
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.show()

        # Define command args
        from utils.helpers import get_dotnet_path, resource_path
        dotnet_path = get_dotnet_path()
        dll_path = resource_path(os.path.join("deps", "DepotDownloader.dll"))

        cmd = [
            dotnet_path,
            dll_path,
            "-app", str(app_id),
            "-depot", str(target_depot),
            "-manifest", str(manifest_id),
            "-manifestfile", manifest_file,
            "-depotkeys", keys_path,
            "-manifest-only",
            "-dir", temp_dir
        ]

        class DumpThread(QThread):
            finished_signal = pyqtSignal(bool, str)
            def run(self):
                try:
                    subprocess.run(cmd, capture_output=True, text=True, check=True)
                    self.finished_signal.emit(True, "")
                except Exception as ex:
                    self.finished_signal.emit(False, str(ex))

        self.dump_thread = DumpThread()

        def on_dump_finished(success, err):
            progress_dialog.close()
            # Clean up temp keys file
            if os.path.exists(keys_path):
                try:
                    os.remove(keys_path)
                except OSError:
                    pass

            if not success:
                QMessageBox.critical(self, "Error", f"Failed to load file list: {err}")
                return

            txt_path = os.path.join(temp_dir, f"manifest_{target_depot}_{manifest_id}.txt")
            if not os.path.exists(txt_path):
                QMessageBox.critical(self, "Error", "Failed to locate generated file list text file.")
                return

            # Open File Selection Tree Dialog
            from ui.dialogs.fileselection import FileSelectionDialog
            sel_dialog = FileSelectionDialog(app_id, target_depot, txt_path, self)
            if sel_dialog.exec():
                self.selected_files = sel_dialog.selected_files
                QMessageBox.information(
                    self,
                    "Selection Confirmed",
                    f"Selected {len(self.selected_files)} file(s) for custom download.\nPress OK at the bottom to start installing."
                )

        self.dump_thread.finished_signal.connect(on_dump_finished)
        self.dump_thread.start()

    def accept(self):
        # 1. Validate depot selection
        selected_depots = self.get_selected_depots()
        if not selected_depots:
            QMessageBox.warning(self, "No Depots Selected", "Please select at least one depot to proceed.")
            return

        # 2. Validate storage selection if enabled
        if self.show_storage and hasattr(self, "_storage_paths") and self._storage_paths:
            if not self.selected_storage_path:
                QMessageBox.warning(self, "No Storage Selected", "Please select a storage location before proceeding.")
                return

        # 3. Promote header image to permanent cache since user is downloading
        if hasattr(self, "app_id") and self.app_id:
            ImageFetcher.promote_to_permanent_cache(self.app_id)

        super().accept()

    def get_selected_storage(self) -> str:
        """Returns the selected storage destination path, or None."""
        return self.selected_storage_path

    def closeEvent(self, a0):
        """Ensure image fetch is cleaned up when dialog closes."""
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
