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
from pathlib import Path
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
    QComboBox,
    QRadioButton,
    QButtonGroup,
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
        self.setFixedSize(520, 520)
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

        # ── Build Selection Frame (Offers choice between Manifest Build vs Latest Live Build) ──
        self.build_selection_frame = QFrame()
        self.build_selection_frame.setObjectName("build_selection_frame")
        self.build_selection_frame.setStyleSheet("""
            QFrame#build_selection_frame {
                background-color: rgba(255, 255, 255, 0.025);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
        """)
        bs_layout = QVBoxLayout(self.build_selection_frame)
        bs_layout.setContentsMargins(12, 8, 12, 8)
        bs_layout.setSpacing(4)

        self.bs_title = QLabel("Choose Version to Download:")
        self.bs_title.setStyleSheet("color: #FFFFFF; font-size: 8.8pt; font-weight: bold; border: none; background: transparent;")
        bs_layout.addWidget(self.bs_title)

        self.build_group = QButtonGroup(self)

        # Radio 1: Manifest Build
        self.radio_manifest_build = QRadioButton("Use Imported Manifest Build")
        self.radio_manifest_build.setChecked(True)
        self.radio_manifest_build.setCursor(Qt.CursorShape.PointingHandCursor)
        self.radio_manifest_build.setStyleSheet(f"""
            QRadioButton {{
                color: #FFFFFF;
                font-size: 8.8pt;
                font-weight: 600;
                spacing: 8px;
                background: transparent;
                border: none;
            }}
            QRadioButton::indicator {{
                width: 14px;
                height: 14px;
                border-radius: 7px;
                border: 1px solid rgba(255, 255, 255, 0.3);
                background: rgba(255, 255, 255, 0.05);
            }}
            QRadioButton::indicator:checked {{
                background: {self.accent_color};
                border: 1px solid {self.accent_color};
            }}
        """)
        self.build_group.addButton(self.radio_manifest_build)
        bs_layout.addWidget(self.radio_manifest_build)

        self.manifest_build_sub = QLabel("Historical version from dropped manifest")
        self.manifest_build_sub.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 7.8pt; border: none; background: transparent; margin-left: 23px;")
        bs_layout.addWidget(self.manifest_build_sub)

        # Radio 2: Latest Live Build
        self.radio_latest_build = QRadioButton("Use Latest Live Build")
        self.radio_latest_build.setCursor(Qt.CursorShape.PointingHandCursor)
        self.radio_latest_build.setStyleSheet(f"""
            QRadioButton {{
                color: #FFFFFF;
                font-size: 8.8pt;
                font-weight: 600;
                spacing: 8px;
                background: transparent;
                border: none;
            }}
            QRadioButton::indicator {{
                width: 14px;
                height: 14px;
                border-radius: 7px;
                border: 1px solid rgba(255, 255, 255, 0.3);
                background: rgba(255, 255, 255, 0.05);
            }}
            QRadioButton::indicator:checked {{
                background: {self.accent_color};
                border: 1px solid {self.accent_color};
            }}
        """)
        self.build_group.addButton(self.radio_latest_build)
        bs_layout.addWidget(self.radio_latest_build)

        self.latest_build_sub = QLabel("Current release on Steam")
        self.latest_build_sub.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 7.8pt; border: none; background: transparent; margin-left: 23px;")
        bs_layout.addWidget(self.latest_build_sub)

        self.radio_manifest_build.toggled.connect(self._on_build_selection_changed)

        self.confirm_layout.addWidget(self.build_selection_frame)

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

        # Destination Library Frame
        self.dest_frame = QFrame()
        self.dest_frame.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
        """)
        dest_layout = QHBoxLayout(self.dest_frame)
        dest_layout.setContentsMargins(10, 6, 10, 6)
        dest_layout.setSpacing(10)

        dest_lbl = QLabel("Install Location:")
        dest_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-size: 8.8pt; font-weight: 600; border: none; background: transparent;")
        dest_layout.addWidget(dest_lbl)

        self.dest_combo = QComboBox()
        self.dest_combo.setFixedHeight(28)
        self.dest_combo.setStyleSheet("""
            QComboBox {
                background-color: rgba(255, 255, 255, 0.07);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 2px 10px;
                font-size: 8.5pt;
                font-weight: 500;
            }
            QComboBox:hover {
                border-color: rgba(255, 255, 255, 0.3);
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a24;
                color: #FFFFFF;
                selection-background-color: #4C8DF5;
                border: 1px solid rgba(255, 255, 255, 0.15);
            }
        """)
        dest_layout.addWidget(self.dest_combo, 1)
        self.confirm_layout.addWidget(self.dest_frame)

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

        # Resolve parent AppID:
        # 1. From extracted manifest depot ID (ground truth)
        if (not appid or appid in ("0", "unknown")) and extracted_manifests:
            try:
                from utils.manifest_resolver import resolve_appid_from_depot
                first_depot = next(iter(extracted_manifests.keys()))
                resolved_aid, resolved_name = resolve_appid_from_depot(first_depot)
                if resolved_aid:
                    appid = resolved_aid
                    if resolved_name and not game_name:
                        game_name = resolved_name
            except Exception as _res_err:
                logger.debug(f"[ZipConfirmDialog] Depot-to-AppID resolution error: {_res_err}")

        # 2. Fallback AppID from zip filename (only for accela_fetch_<id> or pure digits <id>.zip)
        if not appid:
            fn = os.path.basename(zip_path)
            m_fn = re.search(r"accela_fetch_(\d+)", fn) or re.match(r"^(\d{4,9})\.zip$", fn)
            if m_fn:
                appid = m_fn.group(1)

        # Ensure depot keys & acquire latest live manifests from Hubcap if needed
        latest_bundle_manifests = {}
        if appid and appid not in ("0", "unknown"):
            try:
                from utils.manifest_resolver import ensure_depot_keys_for_app
                first_depot = next(iter(extracted_manifests.keys())) if extracted_manifests else None
                keys, token, latest_bundle_manifests = ensure_depot_keys_for_app(appid, first_depot)
                info["has_depot_keys"] = bool(keys)
                info["latest_bundle_manifests"] = latest_bundle_manifests
            except Exception as _k_err:
                logger.debug(f"[ZipConfirmDialog] Depot keys check error: {_k_err}")

        info["appid"] = appid or "0"
        info["manifest_count"] = len(extracted_manifests)

        # Check local installation state
        settings = get_settings()
        installed_bid = str(settings.value(f"installed_buildid/{info['appid']}", "", type=str)).strip()
        installed_branch = str(settings.value(f"installed_branch/{info['appid']}", "public", type=str)).strip()
        installed_lib_path = ""

        # Check if game is detected in Steam library by GameManager
        if hasattr(self, "parent") and self.parent():
            gm = getattr(self.parent(), "game_manager", None)
            if gm and hasattr(gm, "get_game"):
                installed_game = gm.get_game(info["appid"])
                if installed_game:
                    if not installed_bid:
                        installed_bid = str(installed_game.get("buildid", "")).strip()
                    if not game_name and installed_game.get("game_name"):
                        game_name = installed_game["game_name"]
                    if installed_game.get("library_path"):
                        installed_lib_path = installed_game["library_path"]

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
        info["library_path"] = installed_lib_path

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

        # Configure Build Selection Frame
        imported_bid = data.get("imported_buildid")
        live_bid = data.get("live_buildid")
        versions_behind = data.get("versions_behind", 0)
        has_different_builds = (imported_bid and live_bid and imported_bid != live_bid) or (versions_behind > 0)

        if has_different_builds:
            self.build_selection_frame.setVisible(True)
            # Manifest option
            m_text = f"Use Imported Manifest Build ({imported_bid or 'Historical'})"
            self.radio_manifest_build.setText(m_text)
            sub_text = f"Released: {data.get('patch_date', 'Earlier Release')}"
            if versions_behind > 0:
                sub_text += f" • {versions_behind} patch(es) behind current release"
            self.manifest_build_sub.setText(sub_text)

            # Latest live option
            l_text = f"Use Latest Live Build ({live_bid or 'Current'})"
            self.radio_latest_build.setText(l_text)
            self.latest_build_sub.setText(f"Current live release on Steam • {data.get('live_date', 'Latest')}")

            # Pre-select based on intent
            if versions_behind > 0 or intent == "Rollback":
                self.radio_manifest_build.setChecked(True)
                self.pin_checkbox.setChecked(True)
            else:
                self.radio_latest_build.setChecked(True)
        else:
            self.build_selection_frame.setVisible(False)

        # Populate Destination Libraries
        from core.steam_helpers import get_steam_libraries, find_steam_install
        from utils.paths import is_valid_download_directory
        import shutil

        self.dest_combo.clear()
        detected_libs = []
        try:
            raw_libs = get_steam_libraries() or []
            for p in raw_libs:
                if p and is_valid_download_directory(p):
                    real_p = os.path.realpath(p)
                    if real_p not in detected_libs:
                        detected_libs.append(real_p)
        except Exception:
            pass

        settings = get_settings()
        def_dir = settings.value("default_download_directory", "", type=str)
        if def_dir and is_valid_download_directory(def_dir):
            real_def = os.path.realpath(def_dir)
            if real_def not in detected_libs:
                detected_libs.append(real_def)

        steam_root = find_steam_install()
        preselect_idx = 0
        installed_lib = data.get("library_path")
        real_installed = os.path.realpath(installed_lib) if (installed_lib and is_valid_download_directory(installed_lib)) else None

        for idx, lib_path in enumerate(detected_libs):
            p_obj = Path(lib_path)
            try:
                free_b = shutil.disk_usage(lib_path).free
                if free_b >= 1024**4:
                    free_str = f"{free_b / (1024**4):.1f} TB free"
                elif free_b >= 1024**3:
                    free_str = f"{free_b / (1024**3):.1f} GB free"
                else:
                    free_str = f"{free_b / (1024**2):.0f} MB free"
            except Exception:
                free_str = ""

            p_lower = lib_path.lower()
            if steam_root and os.path.realpath(lib_path) == os.path.realpath(steam_root):
                drive_name = "Primary Drive"
            elif "/.local/share/steam" in p_lower or "/.steam/steam" in p_lower:
                drive_name = "Primary Drive"
            elif "sdcard" in p_lower or "sd_card" in p_lower or "mmcblk" in p_lower or "/sd" in p_lower:
                drive_name = "SD Card"
            else:
                drive_name = p_obj.name
                if drive_name.lower() in ("steamlibrary", "steamapps", "common") and len(p_obj.parts) > 1:
                    drive_name = p_obj.parts[-2]

            display_txt = f"{drive_name} ({lib_path})"
            if free_str:
                display_txt += f" — {free_str}"

            self.dest_combo.addItem(display_txt, lib_path)

            if real_installed and os.path.realpath(lib_path) == real_installed:
                preselect_idx = idx
            elif not real_installed and def_dir and os.path.realpath(lib_path) == os.path.realpath(def_dir):
                preselect_idx = idx

        if self.dest_combo.count() > 0:
            self.dest_combo.setCurrentIndex(preselect_idx)
            self.dest_frame.setVisible(True)
        else:
            self.dest_frame.setVisible(False)

        # Switch to confirmation page
        self.stack.setCurrentIndex(1)

    def _on_build_selection_changed(self, manifest_checked: Optional[bool] = None):
        if manifest_checked is None:
            manifest_checked = self.radio_manifest_build.isChecked()
        if manifest_checked:
            intent = self.result_data.get("intent", "Rollback")
            if intent == "Rollback":
                self.pin_checkbox.setChecked(True)
            self.proceed_btn.setText("Proceed with Manifest Build")
        else:
            self.pin_checkbox.setChecked(False)
            self.proceed_btn.setText("Proceed with Latest Build")

    def _on_proceed_clicked(self):
        """
        When user clicks proceed: show a loading state within this dialog while
        ProcessZipTask pre-processes the package. This prevents the main window from
        prematurely flashing the download progress screen before DepotSelectionDialog opens!
        """
        if self.dest_combo.count() > 0 and self.dest_combo.currentData():
            chosen_lib = self.dest_combo.currentData()
            self.result_data["library_path"] = chosen_lib
            logger.info(f"[ZipConfirmDialog] User selected destination library: {chosen_lib}")

        self.loading_title.setText("Preparing Package...")
        self.loading_sub.setText("Resolving depots and branches for installation")
        self.loading_cancel_btn.setVisible(False)
        self.stack.setCurrentIndex(0)

        def _prepare_worker():
            try:
                from core.tasks.process_zip_task import ProcessZipTask
                task = ProcessZipTask()
                task_meta = self.get_metadata()
                game_data = task.run(self.zip_path, metadata=task_meta)
                if self.result_data.get("library_path"):
                    game_data["library_path"] = self.result_data["library_path"]
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
        use_latest = False
        if (
            hasattr(self, "radio_latest_build")
            and self.radio_latest_build.isChecked()
            and hasattr(self, "build_selection_frame")
            and not self.build_selection_frame.isHidden()
        ):
            use_latest = True

        chosen_bid = self.result_data.get("live_buildid", "") if use_latest else self.result_data.get("imported_buildid", "")
        if not chosen_bid:
            chosen_bid = self.result_data.get("imported_buildid", "") or self.result_data.get("live_buildid", "")

        meta = {
            "pin_build": self.pin_checkbox.isChecked(),
            "use_latest_build": use_latest,
            "buildid": chosen_bid,
            "branch": self.result_data.get("branch", "public"),
            "is_rollback": (not use_latest) and (self.result_data.get("intent") == "Rollback"),
            "patch_title": self.result_data.get("patch_title", ""),
            "game_name": self.result_data.get("game_name", ""),
            "appid": self.result_data.get("appid", "0"),
        }
        if self.result_data.get("library_path"):
            meta["library_path"] = self.result_data["library_path"]
        if self.processed_game_data:
            meta["preprocessed_game_data"] = self.processed_game_data
        return meta
