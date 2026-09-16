import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, pyqtSlot
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)

from ui.dialogs.library.widgets import GameItemWidget

try:
    from utils.helpers import get_base_path
except ImportError:
    def get_base_path():
        return Path(".")

logger = logging.getLogger(__name__)


class LibraryScannerMixin:
    """Mixin for library game scanning, update status changes, and unregistered LUA imports."""

    def _scan_for_games(self) -> None:
        if self._scanning or not self.game_manager:
            return

        self._scanning = True
        self.scan_button.setEnabled(False)
        self.scan_button.setText("Scanning...")
        self.info_label.setText("Scanning Steam libraries...")
        self._refreshing = True
        self.games_list.clear()

        self.game_manager.scan_steam_libraries_async()

    def _on_scan_complete(self, count: int) -> None:
        self.scan_button.setEnabled(True)
        self.scan_button.setText("Scan Libraries")

        if count > 0:
            # Update checks are triggered separately; wait for all_updates_checked signal
            self._checking_updates = True
            self.info_label.setText(
                f"Found {count} game(s) — checking for updates..."
            )
            return

        self.info_label.setText(f"Scan complete: Found {count} installed Steam game(s).")
        self._scanning = False
        self._refresh_game_list()

    def _on_all_updates_checked(self) -> None:
        """Called when the full batch update check finishes (replaces the 500ms polling loop)."""
        if not self._checking_updates:
            return
        self._checking_updates = False
        self._scanning = False
        self._refresh_game_list()

    def _on_game_update_status_changed(self, appid: str, update_status: str) -> None:
        if self._closing or not self.isVisible():
            return

        # Find matching item
        item = None
        for i in range(self.games_list.count()):
            it = self.games_list.item(i)
            game_data = it.data(Qt.ItemDataRole.UserRole)
            if game_data and game_data.get("appid") == appid:
                item = it
                break

        if not item:
            return

        self._update_item_status(item, appid, update_status)

    def _update_item_status(
        self, item: QListWidgetItem, appid: str, update_status: str
    ) -> None:
        """Update specific item status logic extracted to flatten logic."""
        game_data = item.data(Qt.ItemDataRole.UserRole)
        game_data["update_status"] = update_status
        item.setData(Qt.ItemDataRole.UserRole, game_data)

        # Update widget in-place directly
        widget = self.games_list.itemWidget(item)
        if isinstance(widget, GameItemWidget):
            widget.update_status(update_status)

    # --- Scan for LUA (Import Unregistered Games) ---

    def _on_scan_imports_clicked(self) -> None:
        """Handle the Scan Imports button click — scan cached_luas/ for unregistered lua files."""
        try:
            from managers.import_manager import ImportManager
        except ImportError:
            QMessageBox.critical(self, "Error", "Import manager module not found.")
            return

        self.scan_imports_button.setEnabled(False)
        self.scan_imports_button.setText("Scanning...")

        def _scan_in_background():
            try:
                mgr = ImportManager()
                results = mgr.scan_unregistered_luas()
                QMetaObject.invokeMethod(
                    self, "_on_scan_imports_complete",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(list, results),
                )
            except Exception as e:
                logger.error(f"[ScanImports] Scan failed: {e}", exc_info=True)
                QMetaObject.invokeMethod(
                    self, "_on_scan_imports_complete",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(list, []),
                )

        self.executor.submit(_scan_in_background)

    @pyqtSlot(list)
    def _on_scan_imports_complete(self, results: list) -> None:
        """Handle scan results on the main thread."""
        self.scan_imports_button.setEnabled(True)
        self.scan_imports_button.setText("Scan for LUA")

        if not results:
            QMessageBox.information(
                self, "Scan Complete",
                "No new .lua files found in the cached_luas folder.\n\n"
                "To import a game, place its .lua file in:\n"
                f"{get_base_path() / 'cached_luas'}"
            )
            return

        # Show results dialog
        self._show_import_results_dialog(results)

    def _show_import_results_dialog(self, results: list) -> None:
        """Show a dialog listing discovered unregistered luas with import options."""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Import Scanner — {len(results)} game(s) found")
        dialog.setMinimumWidth(550)
        dialog.setMinimumHeight(350)
        dialog.setStyleSheet(
            f"""
            QDialog {{
                background-color: {self.background_color};
                color: #FFFFFF;
            }}
            QLabel {{
                color: rgba(255, 255, 255, 0.85);
            }}
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.05);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
            }}
            QListWidget {{
                background-color: transparent;
                border: none;
            }}
            QListWidget::item {{
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid transparent;
                border-radius: 8px;
                margin: 2px 0px;
                padding: 10px;
                color: #FFFFFF;
            }}
            QListWidget::item:hover {{
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.14);
            }}
            QListWidget::item:selected {{
                background-color: rgba(255, 255, 255, 0.14);
                border: 1px solid {self.accent_color};
            }}
            """
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel(
            f"Found {len(results)} game(s) with .lua files not yet registered in your library.\n"
            "Select a game to import, or Import All."
        )
        header.setWordWrap(True)
        header.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.75);")
        layout.addWidget(header)

        result_list = QListWidget()
        result_list.setSpacing(2)
        for r in results:
            status_icon = "✅" if r["has_manifest_zip"] else "⚠️"
            status_text = "Ready (zip found)" if r["has_manifest_zip"] else "Needs API fetch"
            item_text = (
                f"{r['game_name']}  (AppID: {r['appid']})\n"
                f"  {status_icon} {status_text}  |  {r['depot_count']} depot key(s)"
            )
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, r)
            result_list.addItem(item)
        layout.addWidget(result_list)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        import_selected_btn = QPushButton("Import Selected")
        import_selected_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: #000000;
                border: none;
                border-radius: 8px;
                padding: 8px 20px;
                font-weight: bold;
            }}
            QPushButton:hover {{ opacity: 0.9; }}
            """
        )

        import_all_btn = QPushButton("Import All")
        close_btn = QPushButton("Close")

        btn_layout.addStretch()
        btn_layout.addWidget(import_selected_btn)
        btn_layout.addWidget(import_all_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        close_btn.clicked.connect(dialog.close)

        def _import_selected():
            current = result_list.currentItem()
            if not current:
                QMessageBox.warning(dialog, "No Selection", "Please select a game to import.")
                return
            data = current.data(Qt.ItemDataRole.UserRole)
            dialog.close()
            self._execute_import([data])

        def _import_all():
            all_items = []
            for i in range(result_list.count()):
                all_items.append(result_list.item(i).data(Qt.ItemDataRole.UserRole))
            dialog.close()
            self._execute_import(all_items)

        import_selected_btn.clicked.connect(_import_selected)
        import_all_btn.clicked.connect(_import_all)
        dialog.exec()

    def _execute_import(self, items: list) -> None:
        """Execute the import workflow for a list of scan results."""
        if not items:
            return

        progress = QProgressDialog(
            f"Importing {len(items)} game(s)...", "Cancel", 0, len(items), self
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        def _import_in_background():
            try:
                from managers.import_manager import ImportManager
                mgr = ImportManager()
                results = []

                for idx, item in enumerate(items):
                    if progress.wasCanceled():
                        break

                    appid = item["appid"]
                    lua_path = Path(item["lua_path"])
                    game_name = item.get("game_name", f"App {appid}")

                    logger.info(f"[ScanImports] Processing import for {game_name} ({appid})")

                    # Step 1: Import lua (parse, inject DB, check counterpart)
                    import_result = mgr.import_lua(lua_path)

                    if import_result.get("status") == "error":
                        results.append({
                            "appid": appid, "game_name": game_name,
                            "status": "error", "error": import_result.get("error", "Unknown error"),
                        })
                        continue

                    # Step 2: Resolve missing counterpart if needed
                    if import_result.get("needs_api"):
                        api_type = import_result["api_type"]
                        logger.info(
                            f"[ScanImports] Resolving counterpart for {appid} via {api_type} API"
                        )
                        sel_b = self.settings.value(f"selected_branch/{appid}", "public", type=str)
                        zip_path, error = mgr.resolve_missing_counterpart(appid, api_type, branch=sel_b)
                        if error:
                            results.append({
                                "appid": appid, "game_name": game_name,
                                "status": "api_error", "error": error,
                                "api_type": api_type,
                            })
                            continue
                        import_result["zip_path"] = zip_path
                        import_result["status"] = "ready"

                    results.append({
                        "appid": appid,
                        "game_name": game_name,
                        "status": import_result["status"],
                        "zip_path": import_result.get("zip_path"),
                    })

                QMetaObject.invokeMethod(
                    self, "_on_import_complete",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(list, results),
                )
            except Exception as e:
                logger.error(f"[ScanImports] Import failed: {e}", exc_info=True)
                QMetaObject.invokeMethod(
                    self, "_on_import_complete",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(list, [{"status": "error", "error": str(e)}]),
                )

        self.executor.submit(_import_in_background)

    @pyqtSlot(list)
    def _on_import_complete(self, results: list) -> None:
        """Handle import results on the main thread."""
        if not results:
            return

        ready_count = sum(1 for r in results if r.get("status") == "ready")
        error_count = sum(1 for r in results if r.get("status") in ("error", "api_error"))

        summary_parts = []
        for r in results:
            appid = r.get("appid", "?")
            name = r.get("game_name", f"App {appid}")
            if r["status"] == "ready":
                summary_parts.append(f"✅ {name} (AppID {appid}) — Ready")
            elif r["status"] == "error":
                summary_parts.append(f"❌ {name} (AppID {appid}) — {r.get('error', 'Failed')}")
            elif r["status"] == "api_error":
                summary_parts.append(
                    f"⚠️ {name} (AppID {appid}) — API fetch failed: {r.get('error', 'Unknown')}"
                )

        summary_text = "\n".join(summary_parts)
        msg_title = "Import Complete"
        if error_count > 0 and ready_count == 0:
            msg_title = "Import Failed"

        QMessageBox.information(
            self, msg_title,
            f"Import results ({ready_count} ready, {error_count} failed):\n\n{summary_text}\n\n"
            "Games marked as 'Ready' are now registered and can be downloaded\n"
            "from the main AppID entry or will appear in update checks."
        )

        # Refresh the game list to show any newly available games
        self._refresh_game_list()
