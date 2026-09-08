"""
ZIP Import SteamDB Inspection & Confirmation Dialog
====================================================
Replaces the basic "Pin Build Option" prompt when users drag-and-drop
a ZIP or manifest bundle into ASSella.

Features:
- Material You loading screen with Cancel button positioned cleanly at the bottom.
- Compact visual confirmation card without empty dead space.
- Graceful fallbacks if SteamDB is blocked, offline, or times out (never crashes).
- Smooth preparation on 'Proceed': runs ProcessZipTask inside the dialog before closing
  so the main window never flashes an intermediate download screen.
"""

import os
import re
import zipfile
import logging
import threading
from typing import Optional, Dict, Any

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QMetaObject
from PyQt6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QFrame,
    QCheckBox,
)

from ui.material_progress import MaterialSpinner
from utils.color_utils import get_best_foreground_color
from utils.settings import get_settings

logger = logging.getLogger("ACCELA.zip_confirm")


class ZipImportConfirmationDialog(QDialog):
    """
    Dialog providing SteamDB inspection and visual confirmation before queueing an imported ZIP.
    """

    inspection_completed = pyqtSignal(dict)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        zip_path: str = "",
        accent_color: str = "#4c8df5",
        bg_color: str = "#111318",
    ):
        super().__init__(parent)
        self.zip_path = zip_path
        self.accent_color = accent_color
        self.bg_color = bg_color

        self.result_data: Dict[str, Any] = {}
        self.processed_game_data: Optional[Dict[str, Any]] = None

        self.setWindowTitle("Import Package Inspection")
        self.setFixedSize(510, 380)
        self.setSizeGripEnabled(False)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {self.bg_color};
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }}
        """)

        self.inspection_completed.connect(self._on_inspection_completed)

        self._build_ui()
        self._start_inspection()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(0)

        self.stack = QStackedWidget(self)
        self.stack.setStyleSheet("background: transparent;")
        main_layout.addWidget(self.stack, 1)

        # ── Page 0: Loading Spinner (Material You Centered + Bottom Bar) ──
        self.page_loading = QWidget()
        loading_layout = QVBoxLayout(self.page_loading)
        loading_layout.setContentsMargins(0, 0, 0, 0)
        loading_layout.setSpacing(0)

        center_container = QWidget()
        center_box = QVBoxLayout(center_container)
        center_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_box.setSpacing(12)

        self.spinner = MaterialSpinner(center_container, size=38, color=self.accent_color, thickness=3)
        center_box.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignCenter)

        self.loading_title = QLabel("Inspecting Package...")
        self.loading_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_title.setStyleSheet("color: #FFFFFF; font-size: 11pt; font-weight: bold; border: none; background: transparent;")
        center_box.addWidget(self.loading_title)

        self.loading_sub = QLabel("Querying SteamDB for version history & build metadata")
        self.loading_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_sub.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; border: none; background: transparent;")
        center_box.addWidget(self.loading_sub)

        loading_layout.addStretch(1)
        loading_layout.addWidget(center_container, 0, Qt.AlignmentFlag.AlignCenter)
        loading_layout.addStretch(1)

        # Bottom row for Material You Cancel button pinned to bottom right
        bottom_loading_row = QHBoxLayout()
        bottom_loading_row.setContentsMargins(0, 0, 0, 4)
        bottom_loading_row.addStretch(1)

        self.loading_cancel_btn = QPushButton("Cancel")
        self.loading_cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 16px;
                color: #FFFFFF;
                font-size: 9pt;
                font-weight: 600;
                padding: 6px 20px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                border: 1px solid rgba(255, 255, 255, 0.22);
            }
        """)
        self.loading_cancel_btn.clicked.connect(self.reject)
        bottom_loading_row.addWidget(self.loading_cancel_btn)
        loading_layout.addLayout(bottom_loading_row)

        self.stack.addWidget(self.page_loading)

        # ── Page 1: Confirmation Details (Compact & Snug) ──
        self.page_confirm = QWidget()
        self.confirm_layout = QVBoxLayout(self.page_confirm)
        self.confirm_layout.setContentsMargins(0, 0, 0, 0)
        self.confirm_layout.setSpacing(10)

        # Header: Game Name & Badges
        header_widget = QWidget()
        header_widget.setStyleSheet("background: transparent;")
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        self.game_title_lbl = QLabel("Game Title")
        self.game_title_lbl.setStyleSheet("color: #FFFFFF; font-size: 12.5pt; font-weight: bold; border: none; background: transparent;")
        header_layout.addWidget(self.game_title_lbl)

        badges_row = QHBoxLayout()
        badges_row.setSpacing(6)

        self.appid_badge = QLabel("AppID: 0")
        self.appid_badge.setStyleSheet("""
            color: rgba(255, 255, 255, 0.8);
            background-color: rgba(255, 255, 255, 0.08);
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 8.5pt;
            font-weight: 600;
            border: none;
        """)
        badges_row.addWidget(self.appid_badge)

        self.intent_badge = QLabel("New Installation")
        self.intent_badge.setStyleSheet("""
            color: #FFFFFF;
            background-color: rgba(76, 141, 245, 0.25);
            border: 1px solid rgba(76, 141, 245, 0.6);
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 8.5pt;
            font-weight: 600;
        """)
        badges_row.addWidget(self.intent_badge)
        badges_row.addStretch(1)

        header_layout.addLayout(badges_row)
        self.confirm_layout.addWidget(header_widget)

        # Details Container Frame
        self.details_card = QFrame()
        self.details_card.setObjectName("details_card")
        self.details_card.setStyleSheet("""
            QFrame#details_card {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
            QFrame#details_card QLabel {
                border: none;
                background: transparent;
            }
        """)
        card_layout = QVBoxLayout(self.details_card)
        card_layout.setContentsMargins(12, 9, 12, 9)
        card_layout.setSpacing(5)

        # Patch title
        self.patch_title_lbl = QLabel("Patch Version")
        self.patch_title_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: bold;")
        self.patch_title_lbl.setWordWrap(True)
        card_layout.addWidget(self.patch_title_lbl)

        # Metadata rows
        self.build_info_lbl = QLabel("Package Build ID: -")
        self.build_info_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-size: 8.5pt;")
        card_layout.addWidget(self.build_info_lbl)

        self.live_steam_lbl = QLabel("Current Live on Steam: -")
        self.live_steam_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8pt;")
        card_layout.addWidget(self.live_steam_lbl)

        self.installed_status_lbl = QLabel("Installed Status: Not Installed")
        self.installed_status_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8pt;")
        card_layout.addWidget(self.installed_status_lbl)

        self.confirm_layout.addWidget(self.details_card)

        # Pin Build Tile
        self.pin_frame = QFrame()
        self.pin_frame.setObjectName("pin_frame")
        self.pin_frame.setStyleSheet("""
            QFrame#pin_frame {
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 8px;
            }
        """)
        pin_layout = QVBoxLayout(self.pin_frame)
        pin_layout.setContentsMargins(12, 8, 12, 8)
        pin_layout.setSpacing(3)

        self.pin_checkbox = QCheckBox("Pin this build")
        self.pin_checkbox.setStyleSheet("""
            QCheckBox {
                color: #FFFFFF;
                font-size: 9pt;
                font-weight: 600;
                spacing: 8px;
                background: transparent;
                border: none;
            }
            QCheckBox::indicator {
                width: 17px;
                height: 17px;
                border-radius: 4px;
                border: 1px solid rgba(255, 255, 255, 0.3);
                background: rgba(255, 255, 255, 0.05);
            }
            QCheckBox::indicator:checked {
                background: #4c8df5;
                border: 1px solid #4c8df5;
            }
        """)
        pin_layout.addWidget(self.pin_checkbox)

        self.pin_desc_lbl = QLabel("Locks the installed version and disables automatic updates for this game.")
        self.pin_desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 7.8pt; border: none; background: transparent; margin-left: 25px;")
        self.pin_desc_lbl.setWordWrap(True)
        pin_layout.addWidget(self.pin_desc_lbl)

        self.confirm_layout.addWidget(self.pin_frame)
        self.confirm_layout.addStretch(1)

        # Bottom Button Row
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)
        bottom_row.addStretch(1)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedWidth(95)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 6px;
                color: #FFFFFF;
                font-size: 9pt;
                font-weight: 600;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.1);
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        bottom_row.addWidget(self.cancel_btn)

        self.proceed_btn = QPushButton("Proceed")
        self.proceed_btn.setMinimumWidth(135)
        dl_fg = get_best_foreground_color(self.accent_color)
        self.proceed_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                border: none;
                border-radius: 6px;
                color: {dl_fg};
                font-size: 9pt;
                font-weight: bold;
                padding: 6px 16px;
            }}
            QPushButton:hover {{
                background-color: {self.accent_color}DD;
            }}
        """)
        self.proceed_btn.clicked.connect(self._on_proceed_clicked)
        bottom_row.addWidget(self.proceed_btn)

        self.confirm_layout.addLayout(bottom_row)
        self.stack.addWidget(self.page_confirm)

    def _start_inspection(self):
        def _worker():
            data = self._run_inspection_sync(self.zip_path)
            self.inspection_completed.emit(data)

        threading.Thread(target=_worker, daemon=True).start()

    def _run_inspection_sync(self, zip_path: str) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "appid": "0",
            "game_name": "Unknown Game",
            "imported_buildid": "",
            "patch_title": "Standard Release",
            "patch_date": "",
            "live_buildid": "",
            "live_date": "",
            "installed_buildid": "",
            "is_installed": False,
            "intent": "New Installation",
            "versions_behind": 0,
            "manifest_count": 0,
            "branch": "public",
            "steamdb_available": True,
        }

        if not os.path.exists(zip_path):
            return info

        extracted_manifests = {}
        appid = None
        game_name = None

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    if name.endswith(".manifest"):
                        base = os.path.basename(name).replace(".manifest", "")
                        parts = base.split("_")
                        if len(parts) == 2:
                            extracted_manifests[parts[0]] = parts[1]
                    elif name.endswith(".lua"):
                        try:
                            content = zf.read(name).decode("utf-8", errors="ignore")
                            m_app = re.search(r"addappid\((\d+),\s*1", content)
                            if m_app:
                                appid = m_app.group(1)
                            lines = [line.strip() for line in content.splitlines() if line.strip()]
                            if len(lines) > 1 and lines[1].startswith("--"):
                                game_name = lines[1].lstrip("-").strip()
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"Error reading zip for inspection: {e}")

        # Fallback AppID from zip filename
        if not appid:
            fn = os.path.basename(zip_path)
            m_fn = re.search(r"(\d{4,9})", fn)
            if m_fn:
                appid = m_fn.group(1)

        info["appid"] = appid or "0"
        info["manifest_count"] = len(extracted_manifests)

        # Check local installation state
        settings = get_settings()
        installed_bid = str(settings.value(f"installed_buildid/{info['appid']}", "", type=str)).strip()
        installed_branch = str(settings.value(f"installed_branch/{info['appid']}", "public", type=str)).strip()

        # If not in settings, check if game is detected in Steam library by GameManager
        if not installed_bid and hasattr(self, "parent") and self.parent():
            gm = getattr(self.parent(), "game_manager", None)
            if gm:
                installed_game = gm.get_game(info["appid"])
                if installed_game:
                    installed_bid = str(installed_game.get("buildid", "")).strip()
                    if not game_name and installed_game.get("game_name"):
                        game_name = installed_game["game_name"]

        # Look up in steam_headers.db ONLY for the game name (NEVER for installed status!)
        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            app_meta = db.get_app_info(info["appid"])
            if app_meta:
                if not game_name and app_meta.get("name"):
                    game_name = app_meta.get("name")
        except Exception as e:
            logger.debug(f"Local DB query error in zip inspection: {e}")

        info["game_name"] = game_name or f"App {info['appid']}"
        info["installed_buildid"] = installed_bid
        info["is_installed"] = bool(installed_bid)

        # SteamDB Scraping & Correlating (with robust timeout & fallback)
        try:
            from core.steamdb_scraper import SteamDBScraper, SteamDBBuildsCache
            scraper = SteamDBScraper()
            cache = SteamDBBuildsCache()
            aid_int = int(info["appid"]) if str(info["appid"]).isdigit() else 0

            if aid_int > 0:
                # 1. Check local builds cache first (instant 0.002s check)
                cached_builds = cache.get_builds(aid_int) or []
                for p in cached_builds:
                    depots = p.get("depots", {})
                    if any(d_id in depots and depots[d_id].get("manifest_id") == m_id
                           for d_id, m_id in extracted_manifests.items()):
                        info["imported_buildid"] = str(p.get("buildid", ""))
                        info["patch_title"] = p.get("title") or "Standard Release"
                        info["patch_date"] = p.get("date") or ""
                        if cached_builds:
                            info["live_buildid"] = str(cached_builds[0].get("buildid", ""))
                            info["live_date"] = str(cached_builds[0].get("date", ""))
                            info["versions_behind"] = cached_builds.index(p)
                        break

                # 2. If not matched in cache, fetch fresh patchnotes from SteamDB
                if not info["imported_buildid"]:
                    patches = scraper.get_patchnotes(aid_int, limit=15)
                    if patches:
                        cache.save_builds(aid_int, patches)
                        latest = patches[0]
                        info["live_buildid"] = str(latest.get("buildid", ""))
                        info["live_date"] = str(latest.get("date", ""))

                        # Match manifest GIDs to historical builds (check up to 10 most recent)
                        for p in patches[:10]:
                            bid = p.get("buildid")
                            depots = p.get("depots") or scraper.get_patch_depots(bid)
                            if depots:
                                cache.update_build_depots(aid_int, str(bid), depots)
                            if any(d_id in depots and depots[d_id].get("manifest_id") == m_id
                                   for d_id, m_id in extracted_manifests.items()):
                                info["imported_buildid"] = str(bid)
                                info["patch_title"] = p.get("title") or "Standard Release"
                                info["patch_date"] = p.get("date") or ""
                                info["versions_behind"] = patches.index(p)
                                break
                    else:
                        info["steamdb_available"] = False
        except Exception as e:
            logger.warning(f"SteamDB lookup failed (offline, blocked, or unavailable): {e}")
            info["steamdb_available"] = False

        # Fallback to local Steam PICS if live build is still empty
        if not info["live_buildid"] and info["appid"] != "0":
            try:
                from core.steam_api import get_depot_info_from_api
                pics_data = get_depot_info_from_api(info["appid"])
                if pics_data:
                    if not game_name and pics_data.get("name"):
                        info["game_name"] = pics_data["name"]
                    if pics_data.get("buildid"):
                        info["live_buildid"] = str(pics_data["buildid"])
            except Exception as _p_err:
                logger.debug(f"PICS fallback query failed: {_p_err}")

        # Intent comparison (clean text labels without emojis/icons)
        if not info["is_installed"]:
            info["intent"] = "New Installation"
        else:
            c_bid = info["installed_buildid"]
            i_bid = info["imported_buildid"]
            if c_bid.isdigit() and i_bid.isdigit():
                c_int = int(c_bid)
                i_int = int(i_bid)
                if i_int < c_int:
                    info["intent"] = "Rollback"
                elif i_int == c_int:
                    info["intent"] = "Reinstall"
                else:
                    info["intent"] = "Upgrade"
            else:
                info["intent"] = "Update"

        return info

    @pyqtSlot(dict)
    def _on_inspection_completed(self, data: Dict[str, Any]):
        self.result_data = data

        self.game_title_lbl.setText(data.get("game_name", "Unknown Game"))
        self.appid_badge.setText(f"AppID: {data.get('appid', '0')}")

        intent = data.get("intent", "New Installation")
        self.intent_badge.setText(intent)

        # Style intent badge based on action
        if intent == "Rollback":
            badge_bg = "rgba(255, 167, 38, 0.20)"
            badge_border = "rgba(255, 167, 38, 0.60)"
            badge_fg = "#FFA726"
            proceed_text = "Proceed with Rollback"
            self.pin_checkbox.setChecked(True)
        elif intent == "Upgrade":
            badge_bg = "rgba(102, 187, 106, 0.20)"
            badge_border = "rgba(102, 187, 106, 0.60)"
            badge_fg = "#81C784"
            proceed_text = "Proceed with Upgrade"
            self.pin_checkbox.setChecked(False)
        elif intent == "Reinstall":
            badge_bg = "rgba(255, 255, 255, 0.10)"
            badge_border = "rgba(255, 255, 255, 0.25)"
            badge_fg = "#FFFFFF"
            proceed_text = "Proceed with Reinstall"
            settings = get_settings()
            prev_pin = settings.value(f"pin_build/{data.get('appid')}", False, type=bool)
            self.pin_checkbox.setChecked(prev_pin)
        else:  # New Installation
            badge_bg = "rgba(76, 141, 245, 0.20)"
            badge_border = "rgba(76, 141, 245, 0.60)"
            badge_fg = "#4C8DF5"
            proceed_text = "Proceed with Install"
            self.pin_checkbox.setChecked(False)

        self.intent_badge.setStyleSheet(f"""
            color: {badge_fg};
            background-color: {badge_bg};
            border: 1px solid {badge_border};
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 8.5pt;
            font-weight: 600;
        """)
        self.proceed_btn.setText(proceed_text)

        # Patch title & Build metadata
        patch_title = data.get("patch_title") or "Standard Release"
        self.patch_title_lbl.setText(patch_title)

        imported_bid = data.get("imported_buildid")
        imported_date = data.get("patch_date")
        if imported_bid:
            build_str = f"Package Build ID: <b>{imported_bid}</b>"
            if imported_date:
                build_str += f" &nbsp;•&nbsp; Released: {imported_date}"
        else:
            build_str = f"Package Manifests: <b>{data.get('manifest_count', 0)} files</b> (Build ID not disclosed)"
        self.build_info_lbl.setText(build_str)

        # Live Steam build / fallback notice
        live_bid = data.get("live_buildid")
        live_date = data.get("live_date")
        versions_behind = data.get("versions_behind", 0)
        steamdb_ok = data.get("steamdb_available", True)

        if live_bid:
            live_str = f"Current Live on Steam: Build {live_bid}"
            if live_date:
                live_str += f" ({live_date})"
            if versions_behind > 0:
                live_str += f" &nbsp;—&nbsp; <span style='color: #FFA726;'>({versions_behind} patches behind)</span>"
            if not steamdb_ok:
                live_str += " &nbsp;•&nbsp; <span style='color: #FFA726;'>(SteamDB unavailable)</span>"
            self.live_steam_lbl.setText(live_str)
        elif not steamdb_ok:
            self.live_steam_lbl.setText("SteamDB metadata currently unavailable • Showing local package details")
            self.live_steam_lbl.setStyleSheet("color: rgba(255, 167, 38, 0.85); font-size: 8pt;")
        else:
            self.live_steam_lbl.setText("Current Live on Steam: Up to Date / Not Disclosed")

        # Installed status
        if data.get("is_installed"):
            inst_bid = data.get("installed_buildid")
            self.installed_status_lbl.setText(f"Currently Installed: Build <b>{inst_bid}</b>")
        else:
            self.installed_status_lbl.setText("Currently Installed: <i>Not Installed</i>")

        # Switch to confirmation page
        self.stack.setCurrentIndex(1)

    def _on_proceed_clicked(self):
        """
        When user clicks proceed: show a loading state within this dialog while
        ProcessZipTask pre-processes the package. This prevents the main window from
        prematurely flashing the download progress screen before DepotSelectionDialog opens!
        """
        self.loading_title.setText("Preparing Package...")
        self.loading_sub.setText("Resolving depots and branches for installation")
        self.loading_cancel_btn.setVisible(False)
        self.stack.setCurrentIndex(0)

        def _prepare_worker():
            try:
                from core.tasks.process_zip_task import ProcessZipTask
                task = ProcessZipTask()
                game_data = task.run(self.zip_path)
                self.processed_game_data = game_data
            except Exception as e:
                logger.error(f"Error preparing package in confirmation dialog: {e}")
                self.processed_game_data = {}

            QMetaObject.invokeMethod(self, "accept", Qt.ConnectionType.QueuedConnection)

        threading.Thread(target=_prepare_worker, daemon=True).start()

    def get_metadata(self) -> Dict[str, Any]:
        """
        Returns metadata to attach to the queued job.
        """
        meta = {
            "pin_build": self.pin_checkbox.isChecked(),
            "buildid": self.result_data.get("imported_buildid", ""),
            "branch": self.result_data.get("branch", "public"),
            "is_rollback": self.result_data.get("intent") == "Rollback",
            "patch_title": self.result_data.get("patch_title", ""),
            "game_name": self.result_data.get("game_name", ""),
        }
        if self.processed_game_data:
            meta["preprocessed_game_data"] = self.processed_game_data
        return meta
