"""
Build Selection Dialog
======================
Modal dialog allowing users to browse SteamDB build history for a game,
view release dates and patch titles, and select an older build to install
and pin before downloading.
"""

import logging
import threading
from typing import Dict, List, Optional, Tuple, Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QFrame,
    QStackedWidget,
    QSizePolicy,
)

from ui.material_progress import MaterialSpinner
from utils.color_utils import get_dark_container_color, get_best_foreground_color
from utils.settings import get_settings

logger = logging.getLogger("ACCELA.build_selection")


class BuildSelectionDialog(QDialog):
    """Dialog displaying SteamDB build history with selection support."""

    builds_loaded = pyqtSignal(list)
    builds_error = pyqtSignal(str)
    depots_resolved = pyqtSignal(str, dict)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        app_id: str = "",
        game_name: str = "",
        current_build_id: str = "",
        accent_color: str = "#4c8df5",
        depots_dict: Optional[Dict[str, Any]] = None,
        default_depot_id: str = "",
    ):
        super().__init__(parent)
        self.app_id = str(app_id).strip()
        self.game_name = game_name or f"AppID {self.app_id}"
        self.current_build_id = str(current_build_id or "").strip()
        self.accent_color = accent_color
        self.depots_dict = dict(depots_dict or {})
        self.default_depot_id = str(default_depot_id or "").strip()

        self._selected_build_idx = -1
        self._selected_build_id = ""
        self._selected_patch_depots: Dict[str, Any] = {}
        self._build_cards: List[Tuple[QFrame, dict]] = []
        self._builds_data: List[dict] = []

        try:
            from managers.voices_manager import VoicesManager
            self.recommended_build_id = VoicesManager.get_instance().get_recommended_build_id(self.app_id, self.game_name) or ""
        except Exception:
            self.recommended_build_id = ""

        self.setWindowTitle(f"Build History — {self.game_name}")
        self.setMinimumSize(460, 380)
        self.resize(480, 400)
        self.setStyleSheet("""
            QDialog {
                background-color: #12131a;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }
        """)

        self.builds_loaded.connect(self._on_builds_loaded)
        self.builds_error.connect(self._on_builds_error)
        self.depots_resolved.connect(self._on_depots_resolved)

        self._build_ui()
        self._load_builds()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # --- Header ---
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)

        title_lbl = QLabel("Select Game Build / Version")
        title_lbl.setStyleSheet("font-size: 13pt; font-weight: bold; color: #FFFFFF;")
        header_layout.addWidget(title_lbl)

        sub_text = f"{self.game_name} ({self.app_id})"
        if self.current_build_id:
            sub_text += f"  •  Current Build: {self.current_build_id}"
        subtitle_lbl = QLabel(sub_text)
        subtitle_lbl.setStyleSheet("font-size: 9pt; color: rgba(255, 255, 255, 0.6);")
        header_layout.addWidget(subtitle_lbl)

        main_layout.addLayout(header_layout)

        # --- Stack: Spinner vs Scroll Area vs Error ---
        self.stack = QStackedWidget(self)
        self.stack.setStyleSheet("background: transparent;")

        # Page 0: Loading Spinner
        loading_page = QWidget()
        loading_layout = QVBoxLayout(loading_page)
        loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spinner = MaterialSpinner(loading_page, size=36, color=self.accent_color, thickness=3)
        loading_layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignCenter)
        loading_lbl = QLabel("Fetching SteamDB build history...")
        loading_lbl.setStyleSheet("font-size: 9.5pt; color: rgba(255, 255, 255, 0.6); margin-top: 10px;")
        loading_layout.addWidget(loading_lbl, 0, Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(loading_page)

        # Page 1: Scroll Area with Build Cards
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("""
            QScrollArea {
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                background-color: rgba(255, 255, 255, 0.02);
            }
            QScrollBar:vertical {
                border: none;
                background: rgba(255, 255, 255, 0.03);
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.20);
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.35);
            }
        """)

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet("background: transparent;")
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(8, 8, 8, 8)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()
        self.scroll.setWidget(self.cards_container)
        self.stack.addWidget(self.scroll)

        # Page 2: Empty / Fallback Page
        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl = QLabel("No build history found for this title.")
        self.empty_lbl.setStyleSheet("font-size: 10pt; color: rgba(255, 255, 255, 0.6);")
        empty_layout.addWidget(self.empty_lbl, 0, Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.empty_page)

        main_layout.addWidget(self.stack, 1)

        # --- Bottom Button Row (3 full-width buttons) ---
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedHeight(34)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #FFFFFF;
                font-weight: 600;
                font-size: 8.5pt;
                padding: 0 10px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn, 1)

        self.manual_btn = QPushButton("Manual")
        self.manual_btn.setFixedHeight(34)
        self.manual_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.manual_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #FFFFFF;
                font-weight: 600;
                font-size: 8.5pt;
                padding: 0 10px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                border-color: rgba(255, 255, 255, 0.25);
            }
        """)
        self.manual_btn.clicked.connect(self._on_manual_clicked)
        btn_row.addWidget(self.manual_btn, 1)

        self.select_btn = QPushButton("Use Build")
        self.select_btn.setFixedHeight(34)
        self.select_btn.setEnabled(False)
        self.select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                border: none;
                border-radius: 6px;
                color: {get_best_foreground_color(self.accent_color)};
                font-weight: bold;
                font-size: 8.5pt;
                padding: 0 10px;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.08);
                color: rgba(255, 255, 255, 0.3);
            }}
        """)
        self.select_btn.clicked.connect(self._on_select_clicked)
        btn_row.addWidget(self.select_btn, 1)

        main_layout.addLayout(btn_row)

    def _load_builds(self):
        """Load cached builds immediately, then query SteamDB if needed."""
        try:
            from core.steamdb_scraper import SteamDBBuildsCache
            cache = SteamDBBuildsCache()
            aid_int = int(self.app_id) if self.app_id.isdigit() else 0
            cached_builds, _ = cache.get_builds_with_age(aid_int)
            if cached_builds:
                self._populate_cards(cached_builds)
                self.stack.setCurrentIndex(1)
                return
        except Exception as e:
            logger.debug(f"Cache check error: {e}")

        # Fetch in background
        self.stack.setCurrentIndex(0)
        self._fetch_async()

    def _fetch_async(self):
        def _worker():
            try:
                from core.steamdb_scraper import SteamDBScraper, SteamDBBuildsCache
                aid_int = int(self.app_id) if self.app_id.isdigit() else 0
                scraper = SteamDBScraper()
                data = scraper.get_patchnotes(aid_int, limit=25)
                if data:
                    SteamDBBuildsCache().save_builds(aid_int, data)
                self.builds_loaded.emit(data or [])
            except Exception as e:
                logger.error(f"Failed to fetch SteamDB builds for {self.app_id}: {e}")
                self.builds_error.emit(str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_builds_loaded(self, builds: list):
        if builds:
            self._populate_cards(builds)
            self.stack.setCurrentIndex(1)
        elif self.cards_layout.count() > 1:
            self.stack.setCurrentIndex(1)
        else:
            self.empty_lbl.setText("No builds found on SteamDB.")
            self.stack.setCurrentIndex(2)

    def _on_builds_error(self, err_msg: str):
        if self.cards_layout.count() > 1:
            self.stack.setCurrentIndex(1)
        else:
            self.empty_lbl.setText(f"Unable to load build history: {err_msg}")
            self.stack.setCurrentIndex(2)

    def _populate_cards(self, data: list):
        # Clear existing cards except the trailing stretch
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            if item.widget():
                w = item.widget()
                w.setParent(None)
                w.deleteLater()

        self._build_cards = []
        self._builds_data = data
        self._selected_build_idx = -1
        self.select_btn.setEnabled(False)
        self.select_btn.setText("Use Build")

        container_bg = get_dark_container_color(self.accent_color)

        for idx, item in enumerate(data):
            build_id = str(item.get("buildid", ""))
            title = str(item.get("title", "Update")).strip()
            date_str = str(item.get("date", "")).strip()
            time_str = str(item.get("time", "")).strip()
            is_current = bool(build_id and self.current_build_id and build_id == self.current_build_id)

            card = QFrame()
            card.setObjectName("build_card")
            card.setCursor(Qt.CursorShape.PointingHandCursor)

            normal_style = (
                f"QFrame#build_card {{"
                f"  background-color: rgba(255, 255, 255, 0.035);"
                f"  border: 1px solid {'rgba(255, 255, 255, 0.22)' if is_current else 'rgba(255, 255, 255, 0.08)'};"
                f"  border-radius: 6px;"
                f"}}"
                f"QFrame#build_card:hover {{"
                f"  background-color: rgba(255, 255, 255, 0.065);"
                f"  border-color: rgba(255, 255, 255, 0.20);"
                f"}}"
            )
            selected_style = (
                f"QFrame#build_card {{"
                f"  background-color: {container_bg};"
                f"  border: 1px solid {self.accent_color};"
                f"  border-radius: 6px;"
                f"}}"
            )

            card._normal_style = normal_style
            card._selected_style = selected_style
            card.setStyleSheet(normal_style)

            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(4)

            # Top row: Build ID + Current Badge
            top_row = QHBoxLayout()
            top_row.setSpacing(8)

            bid_lbl = QLabel(f"Build {build_id}")
            bid_lbl.setStyleSheet("font-size: 10.5pt; font-weight: bold; color: #FFFFFF;")
            top_row.addWidget(bid_lbl)

            if is_current:
                curr_badge = QLabel("CURRENT")
                curr_badge.setStyleSheet(
                    "color: #81C784; background-color: rgba(76, 175, 80, 0.15); "
                    "border: 1px solid rgba(129, 199, 132, 0.35); border-radius: 4px; "
                    "padding: 1px 6px; font-size: 8.5pt; font-weight: bold;"
                )
                top_row.addWidget(curr_badge)

            is_rec = bool(self.recommended_build_id and str(build_id) == str(self.recommended_build_id))
            if is_rec:
                rec_badge = QLabel("RECOMMENDED")
                rec_badge.setStyleSheet(
                    f"color: {self.accent_color}; background-color: rgba(76, 141, 245, 0.2); "
                    f"border: 1px solid {self.accent_color}; border-radius: 4px; "
                    "padding: 1px 6px; font-size: 8.5pt; font-weight: bold;"
                )
                top_row.addWidget(rec_badge)

            top_row.addStretch()

            if date_str:
                date_lbl = QLabel(f"{date_str} {time_str}".strip())
                date_lbl.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.5);")
                top_row.addWidget(date_lbl)

            card_layout.addLayout(top_row)

            # Bottom row: Patch Title
            if title:
                title_lbl = QLabel(title)
                title_lbl.setStyleSheet("font-size: 9pt; color: rgba(255, 255, 255, 0.75);")
                title_lbl.setWordWrap(True)
                card_layout.addWidget(title_lbl)

            card.mousePressEvent = lambda _e, i=idx: self._on_card_clicked(i)

            self.cards_layout.insertWidget(idx, card)
            self._build_cards.append((card, item))

            # Auto-select the recommended build if present, else current build
            if is_rec and self._selected_build_idx == -1:
                self._on_card_clicked(idx)
            elif is_current and self._selected_build_idx == -1:
                self._on_card_clicked(idx)

    def _on_card_clicked(self, idx: int):
        if 0 <= self._selected_build_idx < len(self._build_cards):
            prev_card, _ = self._build_cards[self._selected_build_idx]
            prev_card.setStyleSheet(prev_card._normal_style)

        self._selected_build_idx = idx
        card, item = self._build_cards[idx]
        card.setStyleSheet(card._selected_style)

        build_id = str(item.get("buildid", ""))
        self._selected_build_id = build_id

        if build_id:
            self.select_btn.setEnabled(True)
            if self.current_build_id and build_id == self.current_build_id:
                self.select_btn.setText("Use Current Build")
            else:
                self.select_btn.setText(f"Use Build {build_id}")

    def _on_select_clicked(self):
        if not (0 <= self._selected_build_idx < len(self._build_cards)):
            return

        _, item = self._build_cards[self._selected_build_idx]
        build_id = str(item.get("buildid", ""))
        self._selected_build_id = build_id

        # Check if depots are already in item or cache
        try:
            from core.steamdb_scraper import SteamDBBuildsCache
            cache = SteamDBBuildsCache()
            c_depots = cache.get_build_depots(build_id)
            if c_depots:
                self._selected_patch_depots = c_depots
                self.accept()
                return
        except Exception:
            pass

        if item.get("depots"):
            self._selected_patch_depots = item["depots"]
            self.accept()
            return

        # Fetch depots for this patch asynchronously before closing
        self.select_btn.setEnabled(False)
        self.select_btn.setText("Resolving Manifests...")

        def _depot_worker():
            try:
                from core.steamdb_scraper import SteamDBScraper
                depots = SteamDBScraper().get_patch_depots(build_id)
                self.depots_resolved.emit(build_id, depots or {})
            except Exception as e:
                logger.error(f"Error fetching depots for build {build_id}: {e}")
                self.depots_resolved.emit(build_id, {})

        threading.Thread(target=_depot_worker, daemon=True).start()

    def _on_depots_resolved(self, build_id: str, depots: dict):
        self._selected_patch_depots = depots or {}
        try:
            from core.steamdb_scraper import SteamDBBuildsCache
            aid_int = int(self.app_id) if self.app_id.isdigit() else 0
            SteamDBBuildsCache().update_build_depots(aid_int, build_id, depots)
        except Exception:
            pass
        self.accept()

    def _on_manual_clicked(self):
        from ui.dialogs.manual_manifest_dialog import ManualManifestDialog
        dlg = ManualManifestDialog(
            parent=self,
            app_id=self.app_id,
            game_name=self.game_name,
            depots_dict=self.depots_dict,
            default_depot_id=self.default_depot_id,
            current_build_id=self.current_build_id,
            accent_color=self.accent_color,
        )
        if dlg.exec():
            self._selected_build_id, self._selected_patch_depots = dlg.get_selected_build()
            self.accept()

    def get_selected_build(self) -> Tuple[str, Dict[str, Any]]:
        """Return (selected_build_id, patch_depots_dict)."""
        return self._selected_build_id, self._selected_patch_depots
