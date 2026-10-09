"""
ZIP Import SteamDB Inspection & Confirmation Dialog
====================================================
Replaces the basic "Pin Build Option" prompt when users drag-and-drop
a ZIP, LUA, or manifest bundle into ASSella.

Features:
- Content-First Identification: Extracts AppIDs, depot IDs, manifest GIDs, and build metadata
  directly from manifest binary protobufs and Lua tokens/comments (archive/file names are last resort).
- Version Comparison: Clearly contrasts Currently Installed Build vs Imported Package Build vs Steam Live Release.
- Smart Rollback & Pinned Build Awareness: Detects downgrades and historical builds, explaining the intent
  and automatically recommending/enforcing "Pin this build".
- Sub-second Fast Inspection: Uses local DB, Steam library ACFs, Hubcap zero-quota endpoints, and Steam PICS;
  SteamDB scraping is non-blocking with a strict timeout so the UI never freezes for 25s.
- Automatic Recovery for Incomplete Drops: When only LUA is provided, auto-fetches missing manifests via
  Wudrm / ManifestDeX MRC race directly from Steam CDN.
- Clean Modern UI: Snug auto-sizing without empty voids, game capsule banner, and clear action notices.
"""

import os
import re
import zipfile
import logging
import threading
import datetime
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QMetaObject
from PyQt6.QtGui import QPixmap
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
from utils.lua_parsing import iter_live_matches, is_placeholder_key

logger = logging.getLogger("ACCELA.zip_confirm")


class ZipImportConfirmationDialog(QDialog):
    """
    Dialog providing content-first inspection, version comparison, and visual confirmation
    before queueing an imported ZIP or loose LUA/manifest bundle.
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
        self.img_fetcher = None

        self.setWindowTitle("Import Package Inspection")
        self.setMinimumWidth(540)
        self.resize(540, 500)
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

        # ── Page 0: Loading Spinner ──
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

        self.loading_title = QLabel("Inspecting Package Contents...")
        self.loading_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_title.setStyleSheet("color: #FFFFFF; font-size: 11pt; font-weight: bold; border: none; background: transparent;")
        center_box.addWidget(self.loading_title)

        self.loading_sub = QLabel("Reading manifest payloads and verifying version history")
        self.loading_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_sub.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; border: none; background: transparent;")
        center_box.addWidget(self.loading_sub)

        loading_layout.addStretch(1)
        loading_layout.addWidget(center_container, 0, Qt.AlignmentFlag.AlignCenter)
        loading_layout.addStretch(1)

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

        # ── Page 1: Confirmation Details (Snug & Information-Rich) ──
        self.page_confirm = QWidget()
        self.confirm_layout = QVBoxLayout(self.page_confirm)
        self.confirm_layout.setContentsMargins(0, 0, 0, 0)
        self.confirm_layout.setSpacing(9)

        # 1. Header Row (Capsule Banner + Game Name & Badges)
        header_widget = QWidget()
        header_widget.setStyleSheet("background: transparent;")
        header_h_layout = QHBoxLayout(header_widget)
        header_h_layout.setContentsMargins(0, 0, 0, 0)
        header_h_layout.setSpacing(12)

        # Game capsule banner image
        self.capsule_img_lbl = QLabel()
        self.capsule_img_lbl.setFixedSize(124, 58)
        self.capsule_img_lbl.setStyleSheet("""
            QLabel {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
            }
        """)
        self.capsule_img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.capsule_img_lbl.setScaledContents(True)
        header_h_layout.addWidget(self.capsule_img_lbl)

        # Title & Badges
        header_text_box = QVBoxLayout()
        header_text_box.setContentsMargins(0, 0, 0, 0)
        header_text_box.setSpacing(4)

        self.game_title_lbl = QLabel("Game Title")
        self.game_title_lbl.setStyleSheet("color: #FFFFFF; font-size: 11.5pt; font-weight: bold; border: none; background: transparent;")
        self.game_title_lbl.setWordWrap(True)
        header_text_box.addWidget(self.game_title_lbl)

        badges_row = QHBoxLayout()
        badges_row.setSpacing(5)

        self.appid_badge = QLabel("AppID: 0")
        self.appid_badge.setStyleSheet("""
            color: rgba(255, 255, 255, 0.85);
            background-color: rgba(255, 255, 255, 0.08);
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 8pt;
            font-weight: 600;
            border: none;
        """)
        badges_row.addWidget(self.appid_badge)

        self.branch_badge = QLabel("Branch: public")
        self.branch_badge.setStyleSheet("""
            color: rgba(255, 255, 255, 0.85);
            background-color: rgba(255, 255, 255, 0.08);
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 8pt;
            font-weight: 600;
            border: none;
        """)
        badges_row.addWidget(self.branch_badge)

        self.manifests_badge = QLabel("0 Depots")
        self.manifests_badge.setStyleSheet("""
            color: rgba(255, 255, 255, 0.85);
            background-color: rgba(255, 255, 255, 0.08);
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 8pt;
            font-weight: 600;
            border: none;
        """)
        badges_row.addWidget(self.manifests_badge)

        self.intent_badge = QLabel("New Installation")
        self.intent_badge.setStyleSheet("""
            color: #FFFFFF;
            background-color: rgba(76, 141, 245, 0.25);
            border: 1px solid rgba(76, 141, 245, 0.6);
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 8pt;
            font-weight: 600;
        """)
        badges_row.addWidget(self.intent_badge)
        badges_row.addStretch(1)

        header_text_box.addLayout(badges_row)
        header_h_layout.addLayout(header_text_box, 1)
        self.confirm_layout.addWidget(header_widget)

        # 2. Version Comparison Card
        self.version_card = QFrame()
        self.version_card.setObjectName("version_card")
        self.version_card.setStyleSheet("""
            QFrame#version_card {
                background-color: rgba(255, 255, 255, 0.035);
                border: 1px solid rgba(255, 255, 255, 0.09);
                border-radius: 8px;
            }
            QFrame#version_card QLabel {
                border: none;
                background: transparent;
            }
        """)
        vcard_layout = QVBoxLayout(self.version_card)
        vcard_layout.setContentsMargins(12, 8, 12, 8)
        vcard_layout.setSpacing(5)

        self.patch_title_lbl = QLabel("Patch Version")
        self.patch_title_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.2pt; font-weight: bold;")
        self.patch_title_lbl.setWordWrap(True)
        vcard_layout.addWidget(self.patch_title_lbl)

        # 3 Comparison Rows
        self.row_imported_lbl = QLabel("📦 Package Build: -")
        self.row_imported_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.9); font-size: 8.5pt;")
        vcard_layout.addWidget(self.row_imported_lbl)

        self.row_installed_lbl = QLabel("💻 Currently Installed: Not Installed")
        self.row_installed_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.2pt;")
        vcard_layout.addWidget(self.row_installed_lbl)

        self.row_live_lbl = QLabel("☁️ Steam Live Release: -")
        self.row_live_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.2pt;")
        vcard_layout.addWidget(self.row_live_lbl)

        self.confirm_layout.addWidget(self.version_card)

        # 3. Action Notice Banner (Explains what is happening in plain English)
        self.action_notice_banner = QFrame()
        self.action_notice_banner.setObjectName("action_notice_banner")
        self.action_notice_banner.setStyleSheet("""
            QFrame#action_notice_banner {
                background-color: rgba(76, 141, 245, 0.10);
                border: 1px solid rgba(76, 141, 245, 0.35);
                border-radius: 6px;
            }
        """)
        banner_layout = QHBoxLayout(self.action_notice_banner)
        banner_layout.setContentsMargins(10, 6, 10, 6)
        banner_layout.setSpacing(8)

        self.notice_icon_lbl = QLabel("ℹ️")
        self.notice_icon_lbl.setStyleSheet("font-size: 11pt; border: none; background: transparent;")
        banner_layout.addWidget(self.notice_icon_lbl)

        self.notice_text_lbl = QLabel("Ready to install package.")
        self.notice_text_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.4pt; border: none; background: transparent;")
        self.notice_text_lbl.setWordWrap(True)
        banner_layout.addWidget(self.notice_text_lbl, 1)

        self.confirm_layout.addWidget(self.action_notice_banner)

        # 4. Build Selection Frame (Offered when imported build differs from live build)
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
        bs_layout.setContentsMargins(12, 7, 12, 7)
        bs_layout.setSpacing(3)

        self.bs_title = QLabel("Choose Build to Install:")
        self.bs_title.setStyleSheet("color: #FFFFFF; font-size: 8.6pt; font-weight: bold; border: none; background: transparent;")
        bs_layout.addWidget(self.bs_title)

        self.build_group = QButtonGroup(self)

        # Radio 1: Imported Build
        self.radio_manifest_build = QRadioButton("Use Imported Build")
        self.radio_manifest_build.setChecked(True)
        self.radio_manifest_build.setCursor(Qt.CursorShape.PointingHandCursor)
        self.radio_manifest_build.setStyleSheet(f"""
            QRadioButton {{
                color: #FFFFFF;
                font-size: 8.6pt;
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

        self.manifest_build_sub = QLabel("Specific pinned build from imported files (recommended for mods/rollbacks)")
        self.manifest_build_sub.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 7.8pt; border: none; background: transparent; margin-left: 23px;")
        bs_layout.addWidget(self.manifest_build_sub)

        # Radio 2: Latest Live Build
        self.radio_latest_build = QRadioButton("Switch to Latest Live Build")
        self.radio_latest_build.setCursor(Qt.CursorShape.PointingHandCursor)
        self.radio_latest_build.setStyleSheet(f"""
            QRadioButton {{
                color: #FFFFFF;
                font-size: 8.6pt;
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

        self.latest_build_sub = QLabel("Fetch and install current public release on Steam")
        self.latest_build_sub.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 7.8pt; border: none; background: transparent; margin-left: 23px;")
        bs_layout.addWidget(self.latest_build_sub)

        self.radio_manifest_build.toggled.connect(self._on_build_selection_changed)
        self.confirm_layout.addWidget(self.build_selection_frame)

        # 5. Pin Build Tile
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
        pin_layout.setContentsMargins(12, 7, 12, 7)
        pin_layout.setSpacing(2)

        self.pin_checkbox = QCheckBox("Pin this build in Steam")
        self.pin_checkbox.setStyleSheet(f"""
            QCheckBox {{
                color: #FFFFFF;
                font-size: 8.8pt;
                font-weight: 600;
                spacing: 8px;
                background: transparent;
                border: none;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid rgba(255, 255, 255, 0.3);
                background: rgba(255, 255, 255, 0.05);
            }}
            QCheckBox::indicator:checked {{
                background: {self.accent_color};
                border: 1px solid {self.accent_color};
            }}
        """)
        pin_layout.addWidget(self.pin_checkbox)

        self.pin_desc_lbl = QLabel("Locks this installed version in Steam to prevent automatic updates from overwriting it.")
        self.pin_desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 7.8pt; border: none; background: transparent; margin-left: 25px;")
        self.pin_desc_lbl.setWordWrap(True)
        pin_layout.addWidget(self.pin_desc_lbl)

        self.confirm_layout.addWidget(self.pin_frame)

        # 6. Destination Library Frame
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
        dest_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-size: 8.6pt; font-weight: 600; border: none; background: transparent;")
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
                font-size: 8.4pt;
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
                font-size: 8.8pt;
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
        self.proceed_btn.setMinimumWidth(150)
        dl_fg = get_best_foreground_color(self.accent_color)
        self.proceed_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                border: none;
                border-radius: 6px;
                color: {dl_fg};
                font-size: 8.8pt;
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
            "has_depot_keys": False,
            "missing_manifests": {},
            "depot_keys": {},
            "app_token": None,
            "latest_bundle_manifests": {},
            "creation_dates": [],
        }

        if not os.path.exists(zip_path):
            return info

        extracted_manifests = {}
        lua_manifests = {}
        depot_keys = {}
        appid = None
        game_name = None
        app_token = None
        lua_buildid = None
        lua_branch = "public"
        lua_build_date = None
        manifest_creation_times = []

        # ── Step 1: Content-First Parsing (Protobuf & LUA payload vs Filenames) ──
        try:
            from steam.core.manifest import DepotManifest
        except Exception:
            DepotManifest = None

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    if name.endswith(".manifest"):
                        try:
                            mf_bytes = zf.read(name)
                            if DepotManifest:
                                try:
                                    dm = DepotManifest(mf_bytes)
                                    if dm.depot_id and dm.gid:
                                        extracted_manifests[str(dm.depot_id)] = str(dm.gid)
                                        if dm.creation_time:
                                            manifest_creation_times.append(dm.creation_time)
                                        continue
                                except Exception:
                                    pass
                            # Fallback if protobuf parse failed
                            base = os.path.basename(name).replace(".manifest", "")
                            parts = base.split("_")
                            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                                extracted_manifests[parts[0]] = parts[1]
                        except Exception as _mf_err:
                            logger.debug(f"[ZipConfirmDialog] Error inspecting manifest {name}: {_mf_err}")

                    elif name.endswith(".lua"):
                        try:
                            content = zf.read(name).decode("utf-8", errors="ignore")
                            # Extract header comments: Title, Build ID, Branch, Build Date
                            for line in content.splitlines():
                                s = line.strip()
                                if not s.startswith("--"):
                                    continue
                                if "Build:" in s:
                                    p = s.split("Build:", 1)[1].strip()
                                    if "(" in p and ")" in p:
                                        lua_buildid = p.split("(", 1)[0].strip()
                                        b_match = p.split("(", 1)[1].split(")", 1)[0].replace("branch", "").strip()
                                        if b_match:
                                            lua_branch = b_match
                                    else:
                                        lua_buildid = p
                                elif "Build Date:" in s:
                                    lua_build_date = s.split("Build Date:", 1)[1].strip()
                                elif not game_name and "Lua and Manifest" not in s and "Website:" not in s and "Total" not in s and "MAIN" not in s and "DLCS" not in s:
                                    cleaned = s.lstrip("-").strip()
                                    if cleaned and not cleaned.isdigit():
                                        game_name = cleaned

                            # Live addappid matches
                            for match in iter_live_matches(content, r"addappid\((.*?)\)(.*)", 0):
                                args = [a.strip().strip('"').strip("'") for a in match.group(1).split(",")]
                                if not args or not args[0]:
                                    continue
                                cur_id = args[0]
                                if not appid:
                                    appid = cur_id
                                    comm = match.group(2).strip()
                                    if not game_name and comm.startswith("--"):
                                        game_name = comm.lstrip("-").strip()
                                    if len(args) > 2 and args[2] and not is_placeholder_key(args[2]):
                                        depot_keys[cur_id] = args[2]
                                else:
                                    if len(args) > 2 and args[2] and not is_placeholder_key(args[2]):
                                        depot_keys[cur_id] = args[2]

                            # Live setManifestid matches
                            for match in iter_live_matches(content, r'setManifestid\(\s*(\d+)\s*,\s*"([^"]+)"', 0):
                                lua_manifests[match.group(1)] = match.group(2)

                            # Live addtoken matches
                            m_tok = re.search(r'addtoken\(\s*\d+\s*,\s*"([^"]+)"\s*\)', content)
                            if m_tok:
                                app_token = m_tok.group(1)

                        except Exception as _lua_err:
                            logger.debug(f"[ZipConfirmDialog] Error inspecting lua {name}: {_lua_err}")

        except Exception as e:
            logger.error(f"[ZipConfirmDialog] Error opening zip: {e}")

        # ── Step 2: Parent AppID Resolution (Ground Truth) ──
        if not appid or appid in ("0", "unknown"):
            first_did = None
            if extracted_manifests:
                first_did = next(iter(extracted_manifests.keys()))
            elif lua_manifests:
                first_did = next(iter(lua_manifests.keys()))

            if first_did:
                try:
                    from utils.manifest_resolver import resolve_appid_from_depot
                    resolved_aid, resolved_name = resolve_appid_from_depot(first_did)
                    if resolved_aid:
                        appid = resolved_aid
                        if resolved_name and not game_name:
                            game_name = resolved_name
                except Exception as _res_err:
                    logger.debug(f"[ZipConfirmDialog] Depot-to-AppID resolution failed: {_res_err}")

        # Fallback to zip filename only as absolute last resort
        if not appid:
            fn = os.path.basename(zip_path)
            m_fn = re.search(r"accela_fetch_(\d+)", fn) or re.match(r"^(\d{4,9})\.zip$", fn)
            if m_fn:
                appid = m_fn.group(1)

        info["appid"] = appid or "0"
        info["branch"] = lua_branch
        info["app_token"] = app_token
        info["depot_keys"] = depot_keys

        # Determine target manifest set and missing manifests (for auto-fetching LUA-only drops)
        all_target_manifests = dict(extracted_manifests)
        for did, mid in lua_manifests.items():
            if did not in all_target_manifests:
                info["missing_manifests"][did] = mid
                all_target_manifests[did] = mid

        info["manifest_count"] = len(all_target_manifests)

        # ── Step 3: Local Installation State Detection (Ground Truth ACF Scanner) ──
        installed_bid = ""
        installed_lib_path = ""

        # A. Check GameManager if active
        if hasattr(self, "parent") and self.parent():
            gm = getattr(self.parent(), "game_manager", None)
            if gm and hasattr(gm, "get_game"):
                inst_g = gm.get_game(info["appid"])
                if inst_g:
                    installed_bid = str(inst_g.get("buildid", "")).strip()
                    if not game_name and inst_g.get("game_name"):
                        game_name = inst_g["game_name"]
                    if inst_g.get("library_path"):
                        installed_lib_path = inst_g["library_path"]

        # B. Check ACF manifests directly across all Steam libraries on disk
        if not installed_bid and info["appid"] != "0":
            try:
                from core.steam_helpers import get_steam_libraries
                for lib in get_steam_libraries() or []:
                    acf_p = Path(lib) / "steamapps" / f"appmanifest_{info['appid']}.acf"
                    if acf_p.is_file():
                        try:
                            content = acf_p.read_text(encoding="utf-8", errors="replace")
                            m_b = re.search(r'"buildid"\s+"(\d+)"', content, re.IGNORECASE)
                            if m_b:
                                installed_bid = m_b.group(1).strip()
                                installed_lib_path = str(lib)
                            m_n = re.search(r'"name"\s+"([^"]+)"', content, re.IGNORECASE)
                            if m_n and not game_name:
                                game_name = m_n.group(1).strip()
                            if installed_bid:
                                break
                        except Exception:
                            pass
            except Exception as _acf_err:
                logger.debug(f"[ZipConfirmDialog] ACF scan error: {_acf_err}")

        # C. Check settings fallback
        settings = get_settings()
        if not installed_bid:
            installed_bid = str(settings.value(f"installed_buildid/{info['appid']}", "", type=str)).strip()

        # Check local DB for game name & metadata
        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            app_meta = db.get_app_info(info["appid"])
            if app_meta and not game_name and app_meta.get("name"):
                game_name = app_meta.get("name")
        except Exception:
            pass

        info["game_name"] = game_name or f"App {info['appid']}"
        info["installed_buildid"] = installed_bid
        info["is_installed"] = bool(installed_bid)
        info["library_path"] = installed_lib_path

        # ── Step 4: Fast Live Build & Depot Keys Discovery (<1.0s) ──
        # Check cached keys & tokens without spending API tokens
        if info["appid"] != "0":
            try:
                from utils.manifest_resolver import ensure_depot_keys_for_app
                first_did = next(iter(all_target_manifests.keys())) if all_target_manifests else None
                keys, token, latest_mfs = ensure_depot_keys_for_app(info["appid"], first_did)
                if keys:
                    info["depot_keys"].update(keys)
                    info["has_depot_keys"] = True
                if latest_mfs:
                    info["latest_bundle_manifests"] = latest_mfs
            except Exception as _k_err:
                logger.debug(f"[ZipConfirmDialog] Keys resolution error: {_k_err}")

        # Check Hubcap /contents for live release manifest map (fast 0.4s zero-quota check)
        hubcap_live_map = {}
        if info["appid"] != "0":
            try:
                from core import morrenus_api
                h_contents = morrenus_api.get_manifest_contents(info["appid"])
                if isinstance(h_contents, dict):
                    hubcap_live_map = h_contents.get("manifest_map", {})
                    if hubcap_live_map and not info["latest_bundle_manifests"]:
                        info["latest_bundle_manifests"] = hubcap_live_map
            except Exception as _h_err:
                logger.debug(f"[ZipConfirmDialog] Hubcap contents error: {_h_err}")

        # Steam PICS query for official name & live buildid (fast cached or Steam direct)
        if info["appid"] != "0":
            try:
                from core.steam_api import get_depot_info_from_api
                pics = get_depot_info_from_api(info["appid"], access_token=info.get("app_token"))
                if pics:
                    if pics.get("buildid"):
                        info["live_buildid"] = str(pics["buildid"]).strip()
                    if pics.get("name") and (not game_name or game_name.startswith("App ")):
                        info["game_name"] = pics["name"]
            except Exception as _p_err:
                logger.debug(f"[ZipConfirmDialog] PICS query error: {_p_err}")

        # Determine if package matches current Live Steam Release:
        is_live_package = False
        if all_target_manifests and hubcap_live_map:
            # Check if all depots in package match Hubcap's live manifest GIDs
            matches = [
                str(all_target_manifests[did]) == str(hubcap_live_map[did])
                for did in all_target_manifests
                if did in hubcap_live_map
            ]
            if matches and all(matches):
                is_live_package = True

        # ── Step 5: Historical Build Matching & Patch Information ──
        matched_hist_bid = None
        patch_title = "Standard Release"
        patch_date = lua_build_date or ""

        if is_live_package:
            info["imported_buildid"] = info["live_buildid"] or lua_buildid or ""
            info["patch_title"] = "Current Live Release"
            info["patch_date"] = lua_build_date or ""
            info["versions_behind"] = 0
        else:
            # Package is a historical / pinned build or rollback
            if lua_buildid:
                matched_hist_bid = lua_buildid
                patch_title = f"Build {lua_buildid}"

            # Check SteamDB Builds Cache (instant SQLite 0.002s query)
            try:
                from core.steamdb_scraper import SteamDBBuildsCache, SteamDBScraper
                cache = SteamDBBuildsCache()
                aid_int = int(info["appid"]) if str(info["appid"]).isdigit() else 0
                if aid_int > 0:
                    cached_builds = cache.get_builds(aid_int) or []
                    for cb in cached_builds:
                        cb_depots = cb.get("depots", {})
                        if any(str(d_id) in cb_depots and cb_depots[str(d_id)].get("manifest_id") == str(m_id)
                               for d_id, m_id in all_target_manifests.items()):
                            matched_hist_bid = str(cb.get("buildid", ""))
                            patch_title = cb.get("title") or patch_title
                            patch_date = cb.get("date") or patch_date
                            if not info["live_buildid"] and cached_builds:
                                info["live_buildid"] = str(cached_builds[0].get("buildid", ""))
                                info["live_date"] = str(cached_builds[0].get("date", ""))
                            info["versions_behind"] = cached_builds.index(cb)
                            break

                    # If not matched in cache and not already discovered from LUA, do a brief non-blocking query
                    if not matched_hist_bid:
                        scraper = SteamDBScraper()
                        patches = scraper.get_patchnotes(aid_int, limit=5)
                        if patches:
                            cache.save_builds(aid_int, patches)
                            if not info["live_buildid"]:
                                info["live_buildid"] = str(patches[0].get("buildid", ""))
                                info["live_date"] = str(patches[0].get("date", ""))
                            for p in patches[:5]:
                                bid = p.get("buildid")
                                depots = p.get("depots") or {}
                                if any(str(d_id) in depots and depots[str(d_id)].get("manifest_id") == str(m_id)
                                       for d_id, m_id in all_target_manifests.items()):
                                    matched_hist_bid = str(bid)
                                    patch_title = p.get("title") or patch_title
                                    patch_date = p.get("date") or patch_date
                                    info["versions_behind"] = patches.index(p)
                                    break
            except Exception as _sdb_err:
                logger.debug(f"[ZipConfirmDialog] SteamDB cache check error: {_sdb_err}")
                info["steamdb_available"] = False

            if not patch_date and manifest_creation_times:
                try:
                    earliest_ts = min(manifest_creation_times)
                    dt = datetime.datetime.fromtimestamp(earliest_ts)
                    patch_date = dt.strftime("%B %d, %Y")
                except Exception:
                    pass

            info["imported_buildid"] = matched_hist_bid or lua_buildid or ""
            info["patch_title"] = patch_title
            info["patch_date"] = patch_date

        # If live build is still empty, fallback to imported if live package
        if not info["live_buildid"] and is_live_package:
            info["live_buildid"] = info["imported_buildid"]

        # ── Step 6: Intent Classification & Pinning Decision ──
        # Understanding whether this is a Rollback, Reinstall, Upgrade, or Historical Install
        imp_bid = info["imported_buildid"]
        inst_bid = info["installed_buildid"]
        live_bid = info["live_buildid"]

        if info["is_installed"]:
            if imp_bid.isdigit() and inst_bid.isdigit():
                if int(imp_bid) < int(inst_bid):
                    info["intent"] = "Rollback"
                    info["recommend_pin"] = True
                elif int(imp_bid) == int(inst_bid):
                    info["intent"] = "Reinstall"
                    info["recommend_pin"] = settings.value(f"pin_build/{info['appid']}", False, type=bool)
                else:
                    info["intent"] = "Upgrade"
                    info["recommend_pin"] = False
            else:
                if not is_live_package:
                    info["intent"] = "Rollback"
                    info["recommend_pin"] = True
                else:
                    info["intent"] = "Reinstall"
                    info["recommend_pin"] = False
        else:
            if not is_live_package or (imp_bid and live_bid and imp_bid != live_bid):
                info["intent"] = "Historical Build"
                info["recommend_pin"] = True
            else:
                info["intent"] = "New Installation"
                info["recommend_pin"] = False

        return info

    @pyqtSlot(dict)
    def _on_inspection_completed(self, data: Dict[str, Any]):
        self.result_data = data
        appid = data.get("appid", "0")

        # 1. Header Information & Capsule Image
        self.game_title_lbl.setText(data.get("game_name", "Unknown Game"))
        self.appid_badge.setText(f"AppID: {appid}")
        branch = data.get("branch", "public")
        self.branch_badge.setText(f"Branch: {branch}")
        m_count = data.get("manifest_count", 0)
        self.manifests_badge.setText(f"{m_count} Depots")

        # Load game capsule banner asynchronously
        self._load_capsule_image(appid)

        # 2. Intent Badge & Pin Recommendations
        intent = data.get("intent", "New Installation")
        self.intent_badge.setText(intent)

        if intent == "Rollback":
            badge_bg = "rgba(255, 167, 38, 0.20)"
            badge_border = "rgba(255, 167, 38, 0.60)"
            badge_fg = "#FFA726"
            proceed_text = "Proceed with Rollback"
            self.pin_checkbox.setChecked(True)
            self.notice_icon_lbl.setText("⚠️")
            inst_b = data.get("installed_buildid", "current")
            imp_b = data.get("imported_buildid", "historical")
            self.notice_text_lbl.setText(
                f"<b>Rollback Detected:</b> You are downgrading from installed Build <b>{inst_b}</b> to Build <b>{imp_b}</b>. "
                "Pinning this build is strongly recommended to prevent Steam from overwriting it."
            )
            self.action_notice_banner.setStyleSheet("""
                QFrame#action_notice_banner {
                    background-color: rgba(255, 167, 38, 0.12);
                    border: 1px solid rgba(255, 167, 38, 0.45);
                    border-radius: 6px;
                }
            """)

        elif intent == "Historical Build":
            badge_bg = "rgba(171, 71, 188, 0.22)"
            badge_border = "rgba(171, 71, 188, 0.60)"
            badge_fg = "#CE93D8"
            proceed_text = "Proceed with Pinned Build"
            self.pin_checkbox.setChecked(True)
            self.notice_icon_lbl.setText("📌")
            imp_b = data.get("imported_buildid", "historical")
            live_b = data.get("live_buildid", "latest")
            self.notice_text_lbl.setText(
                f"<b>Historical Build:</b> Package targets Build <b>{imp_b}</b> (Steam current live is Build <b>{live_b}</b>). "
                "Pinning is recommended so Steam will not auto-update your installed files upon launch."
            )
            self.action_notice_banner.setStyleSheet("""
                QFrame#action_notice_banner {
                    background-color: rgba(171, 71, 188, 0.12);
                    border: 1px solid rgba(171, 71, 188, 0.45);
                    border-radius: 6px;
                }
            """)

        elif intent == "Upgrade":
            badge_bg = "rgba(102, 187, 106, 0.20)"
            badge_border = "rgba(102, 187, 106, 0.60)"
            badge_fg = "#81C784"
            proceed_text = "Proceed with Upgrade"
            self.pin_checkbox.setChecked(False)
            self.notice_icon_lbl.setText("⬆️")
            inst_b = data.get("installed_buildid", "old")
            imp_b = data.get("imported_buildid", "new")
            self.notice_text_lbl.setText(
                f"<b>Upgrade Available:</b> Upgrading installed game from Build <b>{inst_b}</b> to Build <b>{imp_b}</b>."
            )
            self.action_notice_banner.setStyleSheet("""
                QFrame#action_notice_banner {
                    background-color: rgba(102, 187, 106, 0.12);
                    border: 1px solid rgba(102, 187, 106, 0.45);
                    border-radius: 6px;
                }
            """)

        elif intent == "Reinstall":
            badge_bg = "rgba(255, 255, 255, 0.10)"
            badge_border = "rgba(255, 255, 255, 0.25)"
            badge_fg = "#FFFFFF"
            proceed_text = "Proceed with Reinstall"
            self.pin_checkbox.setChecked(bool(data.get("recommend_pin", False)))
            self.notice_icon_lbl.setText("ℹ️")
            inst_b = data.get("installed_buildid", "")
            self.notice_text_lbl.setText(
                f"<b>Reinstall / Verify:</b> Package matches your currently installed version (Build <b>{inst_b}</b>)."
            )
            self.action_notice_banner.setStyleSheet("""
                QFrame#action_notice_banner {
                    background-color: rgba(255, 255, 255, 0.05);
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 6px;
                }
            """)

        else:  # New Installation
            badge_bg = "rgba(76, 141, 245, 0.20)"
            badge_border = "rgba(76, 141, 245, 0.60)"
            badge_fg = "#4C8DF5"
            proceed_text = "Proceed with Install"
            self.pin_checkbox.setChecked(False)
            self.notice_icon_lbl.setText("✅")
            live_b = data.get("live_buildid", "")
            b_str = f" (Build <b>{live_b}</b>)" if live_b else ""
            self.notice_text_lbl.setText(
                f"<b>Ready to Install:</b> Package matches the current live Steam release{b_str}."
            )
            self.action_notice_banner.setStyleSheet("""
                QFrame#action_notice_banner {
                    background-color: rgba(76, 141, 245, 0.12);
                    border: 1px solid rgba(76, 141, 245, 0.45);
                    border-radius: 6px;
                }
            """)

        self.intent_badge.setStyleSheet(f"""
            color: {badge_fg};
            background-color: {badge_bg};
            border: 1px solid {badge_border};
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 8pt;
            font-weight: 600;
        """)
        self.proceed_btn.setText(proceed_text)

        # 3. Version Comparison Card Text
        patch_title = data.get("patch_title") or "Standard Release"
        self.patch_title_lbl.setText(f"Release: {patch_title}")

        imported_bid = data.get("imported_buildid")
        imported_date = data.get("patch_date")
        if imported_bid:
            pkg_str = f"📦 Package Build: <b>Build {imported_bid}</b>"
            if imported_date:
                pkg_str += f" &nbsp;•&nbsp; Released: {imported_date}"
        else:
            pkg_str = f"📦 Package Manifests: <b>{data.get('manifest_count', 0)} files</b>"
        self.row_imported_lbl.setText(pkg_str)

        # Currently installed
        if data.get("is_installed"):
            inst_bid = data.get("installed_buildid")
            self.row_installed_lbl.setText(f"💻 Currently Installed: <b>Build {inst_bid}</b>")
        else:
            self.row_installed_lbl.setText("💻 Currently Installed: <i>Not Installed</i>")

        # Live Steam release
        live_bid = data.get("live_buildid")
        live_date = data.get("live_date")
        versions_behind = data.get("versions_behind", 0)

        if live_bid:
            live_str = f"☁️ Steam Live Release: <b>Build {live_bid}</b>"
            if live_date:
                live_str += f" ({live_date})"
            if versions_behind > 0:
                live_str += f" &nbsp;—&nbsp; <span style='color: #FFA726;'>({versions_behind} patch{'es' if versions_behind > 1 else ''} behind)</span>"
            self.row_live_lbl.setText(live_str)
        else:
            self.row_live_lbl.setText("☁️ Steam Live Release: Up to Date")

        # 4. Build Selection Frame Configuration
        has_different_builds = bool(imported_bid and live_bid and imported_bid != live_bid)

        if has_different_builds:
            self.build_selection_frame.setVisible(True)
            self.radio_manifest_build.setText(f"Use Imported Build (Build {imported_bid})")
            m_sub = "Historical version from package"
            if imported_date:
                m_sub += f" • Released: {imported_date}"
            if versions_behind > 0:
                m_sub += f" • {versions_behind} patch(es) behind current release"
            self.manifest_build_sub.setText(m_sub)

            self.radio_latest_build.setText(f"Switch to Latest Live Build (Build {live_bid})")
            self.latest_build_sub.setText(f"Current live release on Steam • {live_date or 'Latest'}")

            # Pre-select based on intent: for rollbacks and historical packages, default to the imported build!
            if intent in ("Rollback", "Historical Build") or versions_behind > 0:
                self.radio_manifest_build.setChecked(True)
                self.pin_checkbox.setChecked(True)
            else:
                self.radio_latest_build.setChecked(True)
        else:
            self.build_selection_frame.setVisible(False)

        # 5. Populate Destination Libraries
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

        # Switch to confirmation page and snug auto-fit
        self.stack.setCurrentIndex(1)
        self.adjustSize()

    def _load_capsule_image(self, appid: str):
        if not appid or appid in ("0", "unknown"):
            return

        from utils.image_fetcher import ImageFetcher
        from managers.db_manager import DatabaseManager
        db_url = DatabaseManager().get_header_url(str(appid))
        target_url = db_url or f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg"

        def _on_fetched(img_bytes):
            if img_bytes:
                pm = QPixmap()
                pm.loadFromData(img_bytes)
                if not pm.isNull():
                    scaled = pm.scaled(
                        124, 58,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.capsule_img_lbl.setPixmap(scaled)
                    return
            # Fallback text
            self.capsule_img_lbl.setText(f"App {appid}")

        self.img_fetcher = ImageFetcher(target_url)
        self.img_fetcher.finished.connect(_on_fetched)
        self.img_fetcher.start()

    def _on_build_selection_changed(self, manifest_checked: Optional[bool] = None):
        if manifest_checked is None:
            manifest_checked = self.radio_manifest_build.isChecked()
        intent = self.result_data.get("intent", "Rollback")

        if manifest_checked:
            if intent in ("Rollback", "Historical Build"):
                self.pin_checkbox.setChecked(True)
            self.proceed_btn.setText(
                "Proceed with Rollback" if intent == "Rollback" else "Proceed with Pinned Build"
            )
        else:
            self.pin_checkbox.setChecked(False)
            self.proceed_btn.setText("Proceed with Latest Build")

    def _on_proceed_clicked(self):
        """
        When user clicks proceed: show preparing state within this dialog while
        auto-fetching missing manifests (if LUA-only was dropped) and running ProcessZipTask.
        """
        if self.dest_combo.count() > 0 and self.dest_combo.currentData():
            chosen_lib = self.dest_combo.currentData()
            self.result_data["library_path"] = chosen_lib
            logger.info(f"[ZipConfirmDialog] User selected destination library: {chosen_lib}")

        missing_mfs = self.result_data.get("missing_manifests", {})
        if missing_mfs:
            self.loading_title.setText("Downloading Missing Manifests...")
            self.loading_sub.setText(f"Fetching {len(missing_mfs)} manifest(s) directly from Steam CDN via MRC")
        else:
            self.loading_title.setText("Preparing Package...")
            self.loading_sub.setText("Resolving depots and branches for installation")

        self.loading_cancel_btn.setVisible(False)
        self.stack.setCurrentIndex(0)

        def _prepare_worker():
            try:
                # Auto-fetch any missing manifests for incomplete drops (e.g. LUA-only drop)
                if missing_mfs:
                    from core import morrenus_api
                    depot_keys = self.result_data.get("depot_keys", {})
                    fetched_manifests = {}
                    for d_id, m_id in missing_mfs.items():
                        d_key = depot_keys.get(str(d_id))
                        logger.info(f"[ZipConfirmDialog] Auto-fetching missing manifest {d_id}_{m_id} via MRC/Steam CDN...")
                        raw_bytes, g_err = morrenus_api.generate_single_manifest(d_id, m_id, depot_key=d_key)
                        if raw_bytes and not g_err:
                            fetched_manifests[f"{d_id}_{m_id}.manifest"] = raw_bytes
                            # Also cache on disk for downstream tasks
                            from utils.helpers import get_base_path
                            for s_dir in [
                                Path(tempfile.gettempdir()) / "mistwalker_manifests",
                                Path(get_base_path()) / "manifests",
                            ]:
                                try:
                                    s_dir.mkdir(parents=True, exist_ok=True)
                                    (s_dir / f"{d_id}_{m_id}.manifest").write_bytes(raw_bytes)
                                except Exception:
                                    pass

                    # Inject fetched manifests into zip file so ProcessZipTask reads them directly
                    if fetched_manifests and os.path.exists(self.zip_path):
                        try:
                            with zipfile.ZipFile(self.zip_path, "a") as zf_append:
                                for mf_name, mf_data in fetched_manifests.items():
                                    if mf_name not in zf_append.namelist():
                                        zf_append.writestr(mf_name, mf_data)
                            logger.info(f"[ZipConfirmDialog] Appended {len(fetched_manifests)} missing manifest(s) into {self.zip_path}")
                        except Exception as _app_err:
                            logger.warning(f"[ZipConfirmDialog] Could not append manifests to zip: {_app_err}")

                from core.tasks.process_zip_task import ProcessZipTask
                task = ProcessZipTask()
                task_meta = self.get_metadata()
                game_data = task.run(self.zip_path, metadata=task_meta)
                if self.result_data.get("library_path"):
                    game_data["library_path"] = self.result_data["library_path"]
                self.processed_game_data = game_data

            except Exception as e:
                logger.error(f"[ZipConfirmDialog] Error preparing package in confirmation dialog: {e}", exc_info=True)
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
            "latest_bundle_manifests": self.result_data.get("latest_bundle_manifests", {}),
        }
        if self.result_data.get("library_path"):
            meta["library_path"] = self.result_data["library_path"]
        if self.processed_game_data:
            meta["preprocessed_game_data"] = self.processed_game_data
        return meta
