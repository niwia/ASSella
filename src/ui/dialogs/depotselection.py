import logging
import re
import os
import tempfile
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QMessageBox,
    QProgressDialog
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


class DepotSelectionDialog(QDialog):
    def __init__(self, app_id, game_name, depots, header_url, parent=None, selected_depots=None):
        super().__init__(parent)
        self.setWindowTitle("Select Depots to Download")
        self.depots = depots
        self.app_id = app_id
        self.game_name = game_name
        self.header_url = header_url
        self.selected_depots = selected_depots
        self.selected_files = []
        self.resize(485, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(10)

        self.anchor_row = -1

        # Check if we should hide macOS depots
        try:
            from utils.settings import get_settings
            settings = get_settings()
            self._hide_macos = settings.value("hide_macos_depots", True, type=bool)
        except Exception:
            self._hide_macos = True

        self.header_label = QLabel("Loading header image...")
        self.header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_label.setFixedHeight(108)
        layout.addWidget(self.header_label)
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

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

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

        for depot_id, depot_data in sorted_depots:
            # Filter out macOS depots if setting is enabled
            if self._hide_macos and _depot_is_macos(depot_data):
                logger.debug(f"Hiding macOS depot {depot_id}")
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
                    final_desc = f"{tags} {self.game_name}".strip()
                else:
                    final_desc = original_desc

                is_first_depot = False
            else:
                if is_generic_fallback:
                    final_desc = tags
                else:
                    final_desc = original_desc

            if depot_data.get("size"):
                try:
                    size_gb = int(depot_data["size"]) / (1024**3)
                    final_desc += f" <{size_gb:.2f} GB>"
                except (ValueError, TypeError):
                    pass

            item_text = f"{depot_id} - {final_desc}"

            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, depot_id)
            # Store the depot_data reference for platform selection
            item.setData(Qt.ItemDataRole.UserRole + 1, depot_id)

            if self.selected_depots is not None:
                is_checked = depot_id in self.selected_depots
                item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
            else:
                item.setCheckState(Qt.CheckState.Unchecked)

            # Removes ItemIsUserCheckable flag to disable internal checkbox handling, handled manually in self.on_depot_item_clicked
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.list_widget.addItem(item)

        # Makes list widget update stylesheets for the items
        QApplication.processEvents()

        content_widget.addWidget(self.list_widget)

        self.list_widget.itemClicked.connect(self.on_depot_item_clicked)

        # Platform + Select/Deselect buttons in a single row
        button_layout = QHBoxLayout()
        button_layout.setSpacing(4)

        linux_button = QPushButton("Linux")
        linux_button.setToolTip("Select all Linux-compatible depots (including shared)")
        linux_button.clicked.connect(lambda: self._select_platform("linux"))
        
        # Disable the Linux button if there are no Linux-specific depots for this game
        has_linux = any(
            "linux" in (d.get("oslist") or "").lower() or "[linux]" in (d.get("desc") or "").lower()
            for d in self.depots.values()
            if isinstance(d, dict)
        )
        if not has_linux:
            linux_button.setDisabled(True)
            linux_button.setToolTip("No Linux-specific depots found for this game")

        button_layout.addWidget(linux_button)

        windows_button = QPushButton("Windows")
        windows_button.setToolTip("Select all Windows-compatible depots (including shared)")
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

        buttons = create_standard_buttons(self.accept, self.reject)
        content_widget.addWidget(buttons)

        layout.addLayout(content_widget)

    def on_depot_item_clicked(self, item):
        modifiers = QApplication.keyboardModifiers()
        current_row = self.list_widget.row(item)

        current_state = item.checkState()
        new_state = (
            Qt.CheckState.Unchecked
            if current_state == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

        if modifiers == Qt.KeyboardModifier.ShiftModifier:
            if self.anchor_row == -1:
                item.setCheckState(new_state)
                self.anchor_row = current_row
            else:
                try:
                    anchor_item = self.list_widget.item(self.anchor_row)
                    if anchor_item is None:
                        raise RuntimeError("Anchor item is None")
                    target_state = anchor_item.checkState()
                except Exception as e:
                    logger.warning(f"Could not find anchor item for shift-click: {e}")
                    target_state = new_state

                start_row = min(self.anchor_row, current_row)
                end_row = max(self.anchor_row, current_row)

                self.list_widget.blockSignals(True)
                for i in range(start_row, end_row + 1):
                    row_item = self.list_widget.item(i)
                    if row_item is not None:
                        row_item.setCheckState(target_state)
                self.list_widget.blockSignals(False)

        else:
            item.setCheckState(new_state)
            self.anchor_row = current_row

    def _toggle_all_checkboxes(self, check=True):
        state = Qt.CheckState.Checked if check else Qt.CheckState.Unchecked
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            row_item = self.list_widget.item(i)
            if row_item is not None:
                row_item.setCheckState(state)
        self.list_widget.blockSignals(False)

        self.anchor_row = -1

    def _select_platform(self, platform: str):
        """Select depots matching a platform (linux/windows), including shared depots."""
        # Check if there is any depot explicitly designated for this platform
        has_explicit_platform_depot = False
        for d_id, d_data in self.depots.items():
            oslist = (d_data.get("oslist") or "").lower()
            desc = (d_data.get("desc") or "").lower()
            if self._hide_macos and _depot_is_macos(d_data):
                continue
            if platform in oslist or f"[{platform}]" in desc:
                has_explicit_platform_depot = True
                break

        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            row_item = self.list_widget.item(i)
            if row_item is None:
                continue
            depot_id = row_item.data(Qt.ItemDataRole.UserRole)
            depot_data = self.depots.get(depot_id, {})
            if has_explicit_platform_depot and _depot_matches_platform(depot_data, platform):
                row_item.setCheckState(Qt.CheckState.Checked)
            else:
                row_item.setCheckState(Qt.CheckState.Unchecked)
        self.list_widget.blockSignals(False)
        self.anchor_row = -1

    def _fetch_header_image(self, app_id):
        self._current_app_id = app_id
        url = ImageFetcher.get_header_image_url(app_id)
        self.fetcher = ImageFetcher(url)
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
        # Scale to full dialog width (485px), height auto (Steam header is ~2.14:1 ratio = ~227px height)
        scaled = pixmap.scaledToWidth(
            self.width(), Qt.TransformationMode.SmoothTransformation
        )
        self.header_label.setPixmap(scaled)
        self.header_label.setFixedHeight(scaled.height())
        self.header_label.setStyleSheet("")

    def _show_no_image(self):
        """Show fallback text when image is not available."""
        self.header_label.setText("Header image not available.")
        self.header_label.setStyleSheet("")

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
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item is None:
                continue
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected

    def get_selected_files(self):
        """Returns the list of custom checked relative file paths."""
        return self.selected_files

    def _refresh_dlc_only_style(self) -> None:
        """Update the DLC Only button style to reflect its on/off state."""
        active = self._dlc_only_btn.isChecked()
        if active:
            # Inverted / lit-up: background = accent text colour, text = dark
            self._dlc_only_btn.setStyleSheet(
                "QPushButton {"
                "  background-color: #c8e6ff;"
                "  color: #0a1a2e;"
                "  border: 1px solid #4a90d9;"
                "  border-radius: 4px;"
                "  padding: 4px 10px;"
                "  font-weight: bold;"
                "}"
            )
        else:
            self._dlc_only_btn.setStyleSheet(
                "QPushButton {"
                "  background-color: transparent;"
                "  color: rgba(255,255,255,140);"
                "  border: 1px solid rgba(255,255,255,40);"
                "  border-radius: 4px;"
                "  padding: 4px 10px;"
                "}"
                "QPushButton:hover {"
                "  border-color: #4a90d9;"
                "  color: #4a90d9;"
                "}"
            )

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
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                depot_id = str(item.data(Qt.ItemDataRole.UserRole))
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
