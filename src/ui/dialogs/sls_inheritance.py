"""
SLS inheritance (testing)
orphan configs and external installtion manger (beta testing)
"""

import os
import re
import json
import shutil
import urllib.request
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QScrollArea, QWidget, QFrame, QCheckBox, QMessageBox, QFileDialog,
    QTabWidget, QTextEdit, QApplication
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIntValidator

from utils.paths import Paths
from utils.settings import get_settings
from utils.yaml_config_manager import (
    get_user_config_path, remove_additional_app, remove_dlc_data,
    replace_additional_app, _read_config_content
)
from utils.helpers import get_base_path

logger = logging.getLogger("ACCELA.sls_inheritance")


def get_all_steam_libraries() -> List[str]:
    """Find all configured Steam library paths on the system."""
    libraries = []
    home = Path.home()
    primary_candidates = [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".steam/root",
    ]
    main_steam = None
    for p in primary_candidates:
        if p.exists() and (p / "steamapps").exists():
            main_steam = p
            libraries.append(str(p))
            break

    if main_steam:
        vdf_path = main_steam / "steamapps/libraryfolders.vdf"
        if vdf_path.exists():
            try:
                content = vdf_path.read_text(encoding="utf-8", errors="ignore")
                matches = re.findall(r'"path"\s+"([^"]+)"', content)
                for m in matches:
                    norm = os.path.normpath(m)
                    if norm not in libraries and os.path.exists(norm):
                        libraries.append(norm)
            except Exception as e:
                logger.debug(f"Error parsing libraryfolders.vdf: {e}")

    settings = get_settings()
    custom_games_dir = settings.value("games_directory", "")
    if custom_games_dir and os.path.exists(custom_games_dir) and custom_games_dir not in libraries:
        libraries.append(str(custom_games_dir))

    return libraries


def scan_sls_orphans(game_manager=None, log_cb=None) -> List[Dict[str, Any]]:
    """
    Parse SLSsteam AdditionalApps and find all entries not managed by ASSella.
    Returns a list of dicts with detected status, name, and paths.
    """
    if log_cb:
        log_cb("Reading SLSsteam user config.yaml...")

    config_path = get_user_config_path()
    if not config_path.exists():
        if log_cb:
            log_cb(f"Config path does not exist: {config_path}")
        return []

    content = _read_config_content(config_path)
    if not content:
        if log_cb:
            log_cb("No content found in config.yaml")
        return []

    match = re.search(r"^AdditionalApps:[ \t]*$", content, re.MULTILINE)
    if not match:
        if log_cb:
            log_cb("No AdditionalApps section found in config.yaml")
        return []

    after = content[match.end():]
    next_top = re.search(r"^[A-Za-z0-9_]+:[ \t]*", after, re.MULTILINE)
    sec = after[:next_top.start()] if next_top else after

    entries_raw = []
    for line in sec.splitlines():
        line_clean = line.strip()
        m = re.match(r"^-[ \t]*([0-9]+)(?:[ \t]*#[ \t]*(.*))?$", line_clean)
        if m:
            appid = m.group(1)
            comment = m.group(2).strip() if m.group(2) else ""
            entries_raw.append({"appid": appid, "comment": comment})

    if log_cb:
        log_cb(f"Found {len(entries_raw)} total entry(ies) in AdditionalApps.")

    # Known installed games in ASSella
    known_assella_appids = set()
    if game_manager and hasattr(game_manager, "games"):
        for g in game_manager.games:
            if g.get("is_accela_install"):
                aid = str(g.get("appid", ""))
                if aid:
                    known_assella_appids.add(aid)

    # Also check depots/ directory
    depots_dir = Path(get_base_path()) / "depots"
    if depots_dir.exists():
        for f in depots_dir.glob("*.depot"):
            base_aid = f.stem
            known_assella_appids.add(base_aid)
            try:
                for dline in f.read_text().splitlines():
                    p = dline.strip().split(":")
                    if p and p[0].strip():
                        known_assella_appids.add(p[0].strip())
            except Exception:
                pass

    steam_libs = get_all_steam_libraries()
    if log_cb:
        log_cb(f"Checking across {len(steam_libs)} Steam library folder(s)...")

    orphans = []
    for item in entries_raw:
        appid = item["appid"]
        if appid in known_assella_appids:
            continue  # Already managed by ASSella

        comment = item["comment"]
        is_dlc = comment.startswith("[DLC]")
        clean_name = comment.replace("[DLC]", "").strip() if comment else ""
        if " / " in clean_name:
            clean_name = clean_name.split(" / ")[0].strip()

        detected_path = None
        detected_name = clean_name
        for lib in steam_libs:
            acf_path = Path(lib) / "steamapps" / f"appmanifest_{appid}.acf"
            if acf_path.exists():
                try:
                    acf_txt = acf_path.read_text(encoding="utf-8", errors="ignore")
                    m_name = re.search(r'"name"\s+"([^"]+)"', acf_txt)
                    m_dir = re.search(r'"installdir"\s+"([^"]+)"', acf_txt)
                    if m_name and not detected_name:
                        detected_name = m_name.group(1).strip()
                    if m_dir:
                        common_path = Path(lib) / "steamapps/common" / m_dir.group(1).strip()
                        if common_path.exists():
                            detected_path = str(common_path)
                            break
                except Exception:
                    pass

        if not detected_name:
            try:
                from managers.db_manager import DatabaseManager
                db = DatabaseManager()
                info = db.get_app_info(appid)
                if info and info.get("name"):
                    detected_name = info.get("name")
            except Exception:
                pass

        if not detected_name:
            detected_name = f"AppID {appid}"

        orphans.append({
            "appid": appid,
            "name": detected_name,
            "comment": comment,
            "is_dlc": is_dlc,
            "on_disk": bool(detected_path),
            "install_path": detected_path,
        })

    if log_cb:
        uninstalled = sum(1 for o in orphans if not o["on_disk"])
        on_disk = len(orphans) - uninstalled
        log_cb(f"Scan finished: {len(orphans)} unmanaged ({uninstalled} orphan configs, {on_disk} external installs).")

    return orphans


class EditAppIdDialog(QDialog):
    """Dialog allowing users to replace a dummy/test AppID with a real game AppID in SLSsteam config."""

    def __init__(self, current_appid: str, current_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sanitize / Edit AppID in SLS Config")
        self.resize(460, 240)
        self.current_appid = current_appid
        self.new_appid = current_appid
        self.new_name = current_name

        self.setStyleSheet("""
            QDialog {
                background-color: #1a1a24;
                color: #FFFFFF;
            }
            QLabel {
                color: rgba(255, 255, 255, 0.85);
                font-size: 8.5pt;
            }
            QLineEdit {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 6px;
                padding: 6px 10px;
                color: #FFFFFF;
                font-size: 9pt;
            }
            QLineEdit:focus {
                border-color: #82aaff;
            }
        """)

        self._init_ui()

    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        header = QLabel(f"Sanitize Entry: {self.current_name} (AppID {self.current_appid})")
        header.setStyleSheet("font-size: 10pt; font-weight: bold; color: #FFFFFF;")
        lay.addWidget(header)

        desc = QLabel(
            "Enter the real Steam AppID for this game. This will replace the entry in SLSsteam config.yaml "
            "and update any associated DlcData entries."
        )
        desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8pt;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        # AppID Input Row
        aid_row = QHBoxLayout()
        aid_row.setSpacing(8)
        aid_lbl = QLabel("New AppID:")
        aid_lbl.setFixedWidth(80)
        self.aid_input = QLineEdit()
        self.aid_input.setValidator(QIntValidator())
        self.aid_input.setPlaceholderText("e.g. 244210")
        self.aid_input.setText(self.current_appid)
        self.aid_input.textChanged.connect(self._on_appid_changed)
        aid_row.addWidget(aid_lbl)
        aid_row.addWidget(self.aid_input, 1)

        resolve_btn = QPushButton("Lookup Name")
        resolve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        resolve_btn.setStyleSheet("""
            QPushButton {
                background: rgba(33, 150, 243, 0.2);
                border: 1px solid rgba(33, 150, 243, 0.4);
                border-radius: 6px;
                color: #90CAF9;
                font-size: 8pt;
                font-weight: 600;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: rgba(33, 150, 243, 0.3);
            }
        """)
        resolve_btn.clicked.connect(self._fetch_name_online)
        aid_row.addWidget(resolve_btn)
        lay.addLayout(aid_row)

        # Game Name Input Row
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_lbl = QLabel("Game Name:")
        name_lbl.setFixedWidth(80)
        self.name_input = QLineEdit()
        self.name_input.setText(self.current_name)
        name_row.addWidget(name_lbl)
        name_row.addWidget(self.name_input, 1)
        lay.addLayout(name_row)

        lay.addStretch()

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.15); border-radius: 6px; padding: 6px 14px; color: #FFFFFF;")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save & Replace")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet("""
            QPushButton {
                background: rgba(76, 175, 80, 0.2);
                border: 1px solid rgba(76, 175, 80, 0.4);
                border-radius: 6px;
                padding: 6px 16px;
                color: #A5D6A7;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(76, 175, 80, 0.3);
            }
        """)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        lay.addLayout(btn_row)

    def _on_appid_changed(self, text: str):
        aid = text.strip()
        if aid and aid != self.current_appid:
            # Check local DB quickly
            try:
                from managers.db_manager import DatabaseManager
                info = DatabaseManager().get_app_info(aid)
                if info and info.get("name"):
                    self.name_input.setText(info["name"])
            except Exception:
                pass

    def _fetch_name_online(self):
        aid = self.aid_input.text().strip()
        if not aid:
            return
        self.name_input.setPlaceholderText("Looking up Steam store...")
        try:
            url = f"https://store.steampowered.com/api/appdetails?appids={aid}&filters=basic"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if str(aid) in data and data[str(aid)].get("success"):
                    name = data[str(aid)]["data"].get("name", "")
                    if name:
                        self.name_input.setText(name)
                        return
        except Exception as e:
            logger.debug(f"Could not resolve name online for {aid}: {e}")
        QMessageBox.information(self, "Lookup Result", f"Could not automatically resolve title for AppID {aid}. You can type it manually.")

    def _on_save(self):
        new_aid = self.aid_input.text().strip()
        if not new_aid:
            QMessageBox.warning(self, "Missing AppID", "Please enter a valid numeric AppID.")
            return

        self.new_appid = new_aid
        self.new_name = self.name_input.text().strip() or f"AppID {new_aid}"

        cp = get_user_config_path()
        ok = replace_additional_app(cp, self.current_appid, self.new_appid, self.new_name)
        if ok:
            self.accept()
        else:
            QMessageBox.critical(self, "Replace Failed", f"Could not replace AppID {self.current_appid} in SLS config.yaml.")


class SlsInheritanceDialog(QDialog):
    """
    SLS inheritance (testing)
    orphan configs and external installtion manger (beta testing)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setWindowTitle("SLS inheritance (testing)")
        self.resize(840, 620)
        self.setMinimumSize(740, 500)

        self.settings = get_settings()
        self.accent_color = self.settings.value("accent_color", "#a1c9fd")
        self.background_color = "#111318"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {self.background_color};
                color: #FFFFFF;
            }}
            QLabel {{
                color: rgba(255, 255, 255, 0.85);
            }}
            QTabWidget::pane {{
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                background: rgba(0, 0, 0, 0.2);
            }}
            QTabBar::tab {{
                background: rgba(255, 255, 255, 0.04);
                color: rgba(255, 255, 255, 0.65);
                padding: 8px 16px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
                font-size: 8.5pt;
                margin-right: 4px;
            }}
            QTabBar::tab:selected {{
                background: rgba(255, 255, 255, 0.12);
                color: #FFFFFF;
            }}
        """)

        self.orphan_items: List[Dict[str, Any]] = []
        self.checkboxes: Dict[str, QCheckBox] = {}

        self._init_ui()
        QTimer.singleShot(100, self._load_orphans)

    def _log(self, text: str):
        now = datetime.now().strftime("%H:%M:%S")
        self.console_box.append(f"[{now}] {text}")
        QApplication.processEvents()

    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        # Header
        top_row = QHBoxLayout()
        header_col = QVBoxLayout()
        header_col.setSpacing(2)

        title_lbl = QLabel("SLS inheritance (testing)")
        title_lbl.setStyleSheet("font-size: 13pt; font-weight: bold; color: #FFFFFF;")
        header_col.addWidget(title_lbl)

        desc_lbl = QLabel("orphan configs and external installtion manger (beta testing)")
        desc_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.55);")
        header_col.addWidget(desc_lbl)
        top_row.addLayout(header_col, 1)

        # Stats badges
        self.badge_orphans = QLabel("0 Orphan Configs")
        self.badge_orphans.setStyleSheet("background: rgba(239, 83, 80, 0.15); color: #EF5350; border: 1px solid rgba(239, 83, 80, 0.3); border-radius: 6px; padding: 4px 10px; font-size: 8pt; font-weight: bold;")
        top_row.addWidget(self.badge_orphans)

        self.badge_external = QLabel("0 External Installs")
        self.badge_external.setStyleSheet("background: rgba(76, 175, 80, 0.15); color: #81C784; border: 1px solid rgba(76, 175, 80, 0.3); border-radius: 6px; padding: 4px 10px; font-size: 8pt; font-weight: bold;")
        top_row.addWidget(self.badge_external)

        lay.addLayout(top_row)

        # Filter Bar
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter entries by AppID or game name...")
        self.search_box.setFixedHeight(32)
        self.search_box.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 0 10px;
                color: #FFFFFF;
                font-size: 8.5pt;
            }}
            QLineEdit:focus {{
                border-color: {self.accent_color};
            }}
        """)
        self.search_box.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search_box, 1)

        self.select_all_cb = QCheckBox("Select All")
        self.select_all_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_all_cb.setStyleSheet("color: rgba(255, 255, 255, 0.75); font-size: 8.5pt;")
        self.select_all_cb.stateChanged.connect(self._toggle_select_all)
        filter_row.addWidget(self.select_all_cb)

        lay.addLayout(filter_row)

        # Tabs for Orphan Configs vs External Installs
        self.tabs = QTabWidget()

        self.orphans_scroll, self.orphans_layout, self.orphans_container = self._create_scroll_section()
        self.tabs.addTab(self.orphans_scroll, "Orphan Configs")

        self.externals_scroll, self.externals_layout, self.externals_container = self._create_scroll_section()
        self.tabs.addTab(self.externals_scroll, "External Installations")

        lay.addWidget(self.tabs, 1)

        # Live Activity Console
        console_header = QLabel("Live Activity & Audit Log:")
        console_header.setStyleSheet("font-size: 8pt; font-weight: bold; color: rgba(255, 255, 255, 0.6); margin-top: 2px;")
        lay.addWidget(console_header)

        self.console_box = QTextEdit()
        self.console_box.setReadOnly(True)
        self.console_box.setFixedHeight(85)
        self.console_box.setStyleSheet("""
            QTextEdit {
                background: rgba(0, 0, 0, 0.35);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
                color: #A9B1D6;
                font-family: monospace;
                font-size: 7.5pt;
                padding: 4px;
            }
        """)
        lay.addWidget(self.console_box)

        # Bottom Action Buttons
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        self.wipe_sel_btn = QPushButton("Wipe Selected (Config Only)")
        self.wipe_sel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wipe_sel_btn.setFixedHeight(30)
        self.wipe_sel_btn.setStyleSheet("""
            QPushButton {
                background: rgba(239, 83, 80, 0.12);
                border: 1px solid rgba(239, 83, 80, 0.35);
                border-radius: 6px;
                color: #EF5350;
                font-weight: 600;
                padding: 0 12px;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background: rgba(239, 83, 80, 0.22);
            }
        """)
        self.wipe_sel_btn.clicked.connect(self._wipe_selected)
        bottom_bar.addWidget(self.wipe_sel_btn)

        self.wipe_uninstalled_btn = QPushButton("Wipe All Orphan Configs")
        self.wipe_uninstalled_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wipe_uninstalled_btn.setFixedHeight(30)
        self.wipe_uninstalled_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                color: rgba(255, 255, 255, 0.85);
                padding: 0 12px;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.12);
            }
        """)
        self.wipe_uninstalled_btn.clicked.connect(self._wipe_all_uninstalled)
        bottom_bar.addWidget(self.wipe_uninstalled_btn)

        bottom_bar.addStretch()

        refresh_btn = QPushButton("Rescan")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setFixedHeight(30)
        refresh_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #FFFFFF;
                padding: 0 12px;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.1);
            }
        """)
        refresh_btn.clicked.connect(self._load_orphans)
        bottom_bar.addWidget(refresh_btn)

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedHeight(30)
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 6px;
                color: #FFFFFF;
                padding: 0 16px;
                font-weight: bold;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.16);
            }
        """)
        close_btn.clicked.connect(self.accept)
        bottom_bar.addWidget(close_btn)

        lay.addLayout(bottom_bar)

    def _create_scroll_section(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addStretch()

        scroll.setWidget(container)
        return scroll, layout, container

    def _clear_layout(self, layout):
        while layout.count() > 1:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

    def _load_orphans(self):
        self._log("Initiating SLSsteam inheritance scan...")
        self._clear_layout(self.orphans_layout)
        self._clear_layout(self.externals_layout)
        self.checkboxes.clear()

        gm = getattr(self.parent_window, "game_manager", None)
        self.orphan_items = scan_sls_orphans(gm, log_cb=self._log)

        orphans_list = [it for it in self.orphan_items if not it["on_disk"]]
        externals_list = [it for it in self.orphan_items if it["on_disk"]]

        self.badge_orphans.setText(f"{len(orphans_list)} Orphan Configs")
        self.badge_external.setText(f"{len(externals_list)} External Installs")
        self.tabs.setTabText(0, f"Orphan Configs ({len(orphans_list)})")
        self.tabs.setTabText(1, f"External Installs ({len(externals_list)})")

        if not orphans_list:
            empty = QLabel("✓ No uninstalled orphan entries in config.yaml.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 9pt; padding: 30px;")
            self.orphans_layout.insertWidget(0, empty)
        else:
            for idx, it in enumerate(orphans_list):
                card = self._build_item_card(it, is_orphan=True)
                self.orphans_layout.insertWidget(idx, card)

        if not externals_list:
            empty = QLabel("✓ No external installations awaiting adoption.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 9pt; padding: 30px;")
            self.externals_layout.insertWidget(0, empty)
        else:
            for idx, it in enumerate(externals_list):
                card = self._build_item_card(it, is_orphan=False)
                self.externals_layout.insertWidget(idx, card)

    def _build_item_card(self, item: Dict[str, Any], is_orphan: bool) -> QWidget:
        appid = item["appid"]
        name = item["name"]
        path = item["install_path"]
        is_dlc = item["is_dlc"]

        card = QFrame()
        card.setObjectName(f"card_{appid}")
        card.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
            }
            QFrame:hover {
                background: rgba(255, 255, 255, 0.07);
                border-color: rgba(255, 255, 255, 0.16);
            }
        """)
        clay = QHBoxLayout(card)
        clay.setContentsMargins(10, 6, 10, 6)
        clay.setSpacing(10)

        cb = QCheckBox()
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.checkboxes[appid] = cb
        clay.addWidget(cb)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-size: 9pt; font-weight: bold; color: #FFFFFF; border: none; background: transparent;")
        title_row.addWidget(name_lbl)

        aid_lbl = QLabel(f"AppID: {appid}")
        aid_lbl.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.45); border: none; background: transparent;")
        title_row.addWidget(aid_lbl)

        if is_orphan:
            badge = QLabel("Orphan Config")
            badge.setStyleSheet("background: rgba(239, 83, 80, 0.15); color: #EF5350; border: 1px solid rgba(239, 83, 80, 0.3); border-radius: 4px; padding: 1px 6px; font-size: 7.5pt;")
        elif is_dlc:
            badge = QLabel("DLC Entry")
            badge.setStyleSheet("background: rgba(255, 152, 0, 0.15); color: #FFB74D; border: 1px solid rgba(255, 152, 0, 0.3); border-radius: 4px; padding: 1px 6px; font-size: 7.5pt;")
        else:
            badge = QLabel("Installed on Disk")
            badge.setStyleSheet("background: rgba(76, 175, 80, 0.15); color: #81C784; border: 1px solid rgba(76, 175, 80, 0.3); border-radius: 4px; padding: 1px 6px; font-size: 7.5pt;")

        title_row.addWidget(badge)
        title_row.addStretch()
        info_col.addLayout(title_row)

        sub_text = path if path else ("Not found on disk" if not is_dlc else f"Metadata: {item.get('comment', '')}")
        sub_lbl = QLabel(sub_text)
        sub_lbl.setStyleSheet("font-size: 7.5pt; color: rgba(255, 255, 255, 0.4); border: none; background: transparent;")
        info_col.addWidget(sub_lbl)

        clay.addLayout(info_col, 1)

        # Action Buttons Layout
        actions_lay = QHBoxLayout()
        actions_lay.setSpacing(6)

        # 1. Edit AppID (Sanitize) Button
        edit_btn = QPushButton("Edit AppID")
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_btn.setFixedHeight(24)
        edit_btn.setStyleSheet("""
            QPushButton {
                background: rgba(147, 112, 219, 0.15);
                border: 1px solid rgba(147, 112, 219, 0.35);
                border-radius: 4px;
                color: #D1C4E9;
                font-size: 8pt;
                font-weight: 600;
                padding: 0 8px;
            }
            QPushButton:hover {
                background: rgba(147, 112, 219, 0.28);
            }
        """)
        edit_btn.setToolTip("Sanitize or replace this AppID in SLS config.yaml.")
        edit_btn.clicked.connect(lambda _, a=appid, n=name: self._edit_appid(a, n))
        actions_lay.addWidget(edit_btn)

        if is_orphan:
            # Download Button
            dl_btn = QPushButton("Download")
            dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            dl_btn.setFixedHeight(24)
            dl_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(33, 150, 243, 0.15);
                    border: 1px solid rgba(33, 150, 243, 0.35);
                    border-radius: 4px;
                    color: #90CAF9;
                    font-size: 8pt;
                    font-weight: 600;
                    padding: 0 8px;
                }
                QPushButton:hover {
                    background: rgba(33, 150, 243, 0.25);
                }
            """)
            dl_btn.clicked.connect(lambda _, a=appid: self._download_appid(a))
            actions_lay.addWidget(dl_btn)
        else:
            # Adopt Button
            adopt_btn = QPushButton("Adopt")
            adopt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            adopt_btn.setFixedHeight(24)
            adopt_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(76, 175, 80, 0.15);
                    border: 1px solid rgba(76, 175, 80, 0.35);
                    border-radius: 4px;
                    color: #A5D6A7;
                    font-size: 8pt;
                    font-weight: 600;
                    padding: 0 8px;
                }
                QPushButton:hover {
                    background: rgba(76, 175, 80, 0.25);
                }
            """)
            adopt_btn.clicked.connect(lambda _, it=item: self._adopt_item(it))
            actions_lay.addWidget(adopt_btn)

            # Option B: Delete Files & Wipe Config Button (Separate)
            del_files_btn = QPushButton("Delete Files")
            del_files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_files_btn.setFixedHeight(24)
            del_files_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(211, 47, 47, 0.18);
                    border: 1px solid rgba(211, 47, 47, 0.45);
                    border-radius: 4px;
                    color: #FF8A80;
                    font-size: 8pt;
                    font-weight: 600;
                    padding: 0 8px;
                }
                QPushButton:hover {
                    background: rgba(211, 47, 47, 0.32);
                }
            """)
            del_files_btn.setToolTip("Permanently delete game directory and wipe from SLS config.")
            del_files_btn.clicked.connect(lambda _, it=item: self._delete_files_and_wipe(it))
            actions_lay.addWidget(del_files_btn)

        # Option A: Wipe from Config Only Button (Separate)
        wipe_btn = QPushButton("Wipe Config")
        wipe_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        wipe_btn.setFixedHeight(24)
        wipe_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 152, 0, 0.12);
                border: 1px solid rgba(255, 152, 0, 0.35);
                border-radius: 4px;
                color: #FFB74D;
                font-size: 8pt;
                font-weight: 600;
                padding: 0 8px;
            }
            QPushButton:hover {
                background: rgba(255, 152, 0, 0.22);
            }
        """)
        wipe_btn.setToolTip("Remove only from config.yaml. Leaves all disk files untouched.")
        wipe_btn.clicked.connect(lambda _, a=appid, n=name: self._wipe_single(a, n))
        actions_lay.addWidget(wipe_btn)

        clay.addLayout(actions_lay)
        return card

    def _apply_filter(self, text: str):
        query = text.strip().lower()
        for item in self.orphan_items:
            aid = item["appid"]
            for container in (self.orphans_container, self.externals_container):
                card = container.findChild(QFrame, f"card_{aid}")
                if card:
                    match = (query in aid.lower()) or (query in item["name"].lower())
                    card.setVisible(match)

    def _toggle_select_all(self, state):
        checked = (state == Qt.CheckState.Checked.value or state == 2 or bool(state))
        for cb in self.checkboxes.values():
            cb.setChecked(checked)

    def _edit_appid(self, appid: str, name: str):
        dlg = EditAppIdDialog(appid, name, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._log(f"✓ Sanitized AppID: {appid} -> {dlg.new_appid} ({dlg.new_name}) in config.yaml.")
            self._load_orphans()

    def _wipe_single(self, appid: str, name: str):
        ans = QMessageBox.question(
            self,
            "Wipe SLSsteam Entry (Config Only)",
            f"Are you sure you want to remove '{name}' (AppID {appid}) from SLSsteam config.yaml?\n\n"
            f"ℹ️ Notice: This ONLY removes the entry from SLSsteam config.yaml.\n"
            f"Your game files on disk will NEVER be touched or deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._log(f"Wiping AppID {appid} ({name}) from SLS config (files untouched)...")
            cp = get_user_config_path()
            remove_additional_app(cp, appid)
            remove_dlc_data(cp, appid)
            self._log(f"✓ Removed AppID {appid} from config (files untouched).")
            self._load_orphans()

    def _delete_files_and_wipe(self, item: Dict[str, Any]):
        appid = item["appid"]
        name = item["name"]
        install_path = item.get("install_path")

        if not install_path or not os.path.exists(install_path):
            QMessageBox.warning(self, "Directory Not Found", f"No local folder found for {name}.")
            return

        warn_text = (
            f"⚠️ PERMANENT FILE DELETION WARNING\n\n"
            f"You are about to permanently delete the folder:\n"
            f"{install_path}\n\n"
            f"Game: {name} (AppID {appid})\n\n"
            f"This will:\n"
            f"1. Delete all game files and folders in this directory.\n"
            f"2. Remove any associated Steam appmanifest on disk.\n"
            f"3. Remove '{name}' from SLSsteam config.yaml.\n\n"
            f"Are you completely sure? THIS CANNOT BE UNDONE!"
        )
        ans = QMessageBox.warning(
            self,
            "Delete Game Files & Wipe Config",
            warn_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self._log(f"Deleting files and wiping config for {name} ({appid})...")
        try:
            if os.path.exists(install_path):
                shutil.rmtree(install_path)
                self._log(f"✓ Deleted directory: {install_path}")
        except Exception as e:
            self._log(f"❌ Error deleting directory: {e}")
            QMessageBox.critical(self, "Deletion Error", f"Could not delete directory: {e}")
            return

        for lib in get_all_steam_libraries():
            acf = Path(lib) / "steamapps" / f"appmanifest_{appid}.acf"
            if acf.exists():
                try:
                    acf.unlink()
                    self._log(f"✓ Removed Steam manifest: {acf.name}")
                except Exception:
                    pass

        cp = get_user_config_path()
        remove_additional_app(cp, appid)
        remove_dlc_data(cp, appid)
        self._log(f"✓ Wiped AppID {appid} from SLSsteam config.")
        QMessageBox.information(
            self,
            "Files & Config Deleted",
            f"✓ Successfully deleted game files and removed '{name}' from SLS config.",
        )
        self._load_orphans()

    def _wipe_selected(self):
        selected_aids = [aid for aid, cb in self.checkboxes.items() if cb.isChecked()]
        if not selected_aids:
            QMessageBox.information(self, "Nothing Selected", "Please select at least one entry to wipe.")
            return

        ans = QMessageBox.question(
            self,
            "Wipe Selected Entries (Config Only)",
            f"Are you sure you want to remove {len(selected_aids)} selected entry(ies) from SLSsteam config.yaml?\n\n"
            f"ℹ️ Notice: This ONLY removes entries from SLSsteam config.yaml. Your game files on disk will NEVER be touched or deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._log(f"Wiping {len(selected_aids)} selected entry(ies) from config (files untouched)...")
            cp = get_user_config_path()
            for aid in selected_aids:
                remove_additional_app(cp, aid)
                remove_dlc_data(cp, aid)
            self._log(f"✓ Removed {len(selected_aids)} entry(ies) from config (files untouched).")
            self._load_orphans()

    def _wipe_all_uninstalled(self):
        uninstalled_aids = [it["appid"] for it in self.orphan_items if not it["on_disk"]]
        if not uninstalled_aids:
            QMessageBox.information(self, "No Orphan Configs", "No uninstalled orphan config entries were found.")
            return

        ans = QMessageBox.question(
            self,
            "Wipe Orphan Configs",
            f"Are you sure you want to remove all {len(uninstalled_aids)} orphan config entry(ies) from SLSsteam config.yaml?\n\n"
            f"ℹ️ Notice: This ONLY removes entries from SLSsteam config.yaml.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._log(f"Wiping all {len(uninstalled_aids)} orphan configs...")
            cp = get_user_config_path()
            for aid in uninstalled_aids:
                remove_additional_app(cp, aid)
                remove_dlc_data(cp, aid)
            self._log(f"✓ Successfully wiped {len(uninstalled_aids)} orphan config entries.")
            self._load_orphans()

    def _download_appid(self, appid: str):
        self._log(f"Opening manifest download workflow for AppID {appid}...")
        self.accept()
        try:
            from ui.dialogs.fetchmanifest import FetchManifestDialog
            dlg = FetchManifestDialog(self.parent_window, initial_query=appid)
            dlg.exec()
        except Exception as e:
            logger.error(f"Failed to open FetchManifestDialog for AppID {appid}: {e}")

    def _adopt_item(self, item: Dict[str, Any]):
        appid = item["appid"]
        name = item["name"]
        install_path = item.get("install_path")

        if not install_path or not os.path.exists(install_path):
            folder = QFileDialog.getExistingDirectory(
                self, f"Select Install Folder for '{name}' (AppID {appid})"
            )
            if not folder:
                return
            install_path = folder

        self._log(f"Adopting '{name}' (AppID {appid}) at {install_path}...")
        depots_dir = Path(get_base_path()) / "depots"
        depots_dir.mkdir(parents=True, exist_ok=True)
        depot_file = depots_dir / f"{appid}.depot"

        try:
            if not depot_file.exists():
                depot_file.write_text(f"{appid}: Main Depot\n", encoding="utf-8")

            dot_dd = Path(install_path) / ".depotdownloader"
            dot_dd.mkdir(parents=True, exist_ok=True)

            self._log(f"✓ '{name}' adopted into ASSella successfully.")
            QMessageBox.information(
                self,
                "Game Adopted",
                f"✓ '{name}' (AppID {appid}) has been adopted into ASSella!\n\n"
                f"Location: {install_path}\n"
                f"Run a library refresh in ASSella to manage this game.",
            )
            self._load_orphans()
        except Exception as e:
            self._log(f"❌ Adoption failed: {e}")
            QMessageBox.critical(self, "Adoption Failed", f"Failed to adopt game: {e}")
