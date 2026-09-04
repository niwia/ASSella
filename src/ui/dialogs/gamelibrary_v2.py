import os
import platform
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any

from PyQt6.QtCore import Qt, QSize, QPropertyAnimation, pyqtProperty, pyqtSignal, QUrl, pyqtSlot, QEvent, QTimer
from PyQt6.QtGui import QColor, QPixmap, QPainter, QIntValidator, QPalette, QDesktopServices, QLinearGradient
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QCheckBox,
    QLineEdit, QComboBox, QMessageBox, QWidget, QFrame, QStackedWidget,
    QStylePainter, QStyleOptionComboBox, QStyle, QScrollArea, QApplication,
    QGridLayout, QListView, QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QInputDialog,
    QSizePolicy,
)

from utils.helpers import get_base_path
from utils.settings import get_settings
from utils.update_status_cache import get_update_cache
from utils.yaml_config_manager import (
    get_user_config_path, add_fake_app_id, remove_fake_app_id,
    get_fake_appid, is_slssteam_config_management_enabled,
)
from utils.image_fetcher import ImageFetcher
from ui.progress_button import ProgressButton
from ui.material_progress import MaterialSpinner
from core.steamdb_scraper import SteamDBBuildsCache, SteamDBScraper

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
#  Reusable widgets
# ──────────────────────────────────────────────────────────

class SwitchToggle(QWidget):
    stateChanged = pyqtSignal(bool)

    def __init__(self, parent=None, active_color="#4CAF50", bg_color="#333340", circle_color="#FFFFFF"):
        super().__init__(parent)
        self.setFixedSize(36, 16)
        self._checked = False
        self._active_color = QColor(active_color)
        self._bg_color = QColor(bg_color)
        self._circle_color = QColor(circle_color)
        self._circle_pos = 2
        self._animation = QPropertyAnimation(self, b"circle_pos", self)
        self._animation.setDuration(110)

    @pyqtProperty(int)
    def circle_pos(self):
        return self._circle_pos

    @circle_pos.setter
    def circle_pos(self, pos):
        self._circle_pos = pos
        self.update()

    def isChecked(self):
        return self._checked

    def setChecked(self, checked):
        if self._checked != checked:
            self._checked = checked
            self._animation.setEndValue(20 if checked else 2)
            self._animation.start()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.setChecked(not self._checked)
            self.stateChanged.emit(self._checked)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if not self.isEnabled():
            p.setBrush(QColor("#222226"))
        elif self._checked:
            p.setBrush(self._active_color)
        else:
            p.setBrush(self._bg_color)
        p.drawRoundedRect(0, 0, self.width(), self.height(), 8, 8)
        p.setBrush(QColor("#444448") if not self.isEnabled() else self._circle_color)
        p.drawEllipse(self._circle_pos, 2, 12, 12)


class CenteredComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QStyledItemDelegate
        
        class CenterDelegate(QStyledItemDelegate):
            def initStyleOption(self, option, index):
                super().initStyleOption(option, index)
                option.displayAlignment = Qt.AlignmentFlag.AlignCenter

        self.setItemDelegate(CenterDelegate(self))
        
        ac = getattr(parent, "accent_color", "#a1c9fd") if parent else "#a1c9fd"
        from utils.color_utils import get_dark_container_color
        sel_bg_hex = get_dark_container_color(ac)

        self.setStyleSheet(f"""
            QComboBox {{
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                color: #FFFFFF;
                padding: 3px 24px 3px 10px;
                font-size: 9.5pt;
            }}
            QComboBox:hover {{
                background-color: rgba(255, 255, 255, 0.08);
                border-color: rgba(255, 255, 255, 0.15);
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                border-left: none;
            }}
            QComboBox QAbstractItemView {{
                background-color: #1b1b1f;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                selection-background-color: {sel_bg_hex};
                selection-color: #FFFFFF;
                outline: 0px;
                padding: 4px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 28px;
                padding: 4px 12px;
                color: #E0E0E0;
            }}
            QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{
                background-color: {sel_bg_hex} !important;
                color: #FFFFFF !important;
            }}
        """)

    def paintEvent(self, event):
        p = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        p.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, opt,
            QStyle.SubControl.SC_ComboBoxEditField, self)
        pal = self.palette()
        color = self.itemData(self.currentIndex(), Qt.ItemDataRole.ForegroundRole)
        if isinstance(color, QColor):
            pal.setColor(QPalette.ColorRole.Text, color)
        
        p.drawItemText(rect, Qt.AlignmentFlag.AlignCenter, pal,
                       self.isEnabled(), self.currentText(), QPalette.ColorRole.Text)


class HeroBanner(QWidget):
    """Compact header art: fades from solid bg on left → art visible on right."""
    def __init__(self, bg_hex="#1a1a1e", parent=None):
        super().__init__(parent)
        self._px = None
        c = QColor(bg_hex) if bg_hex.startswith("#") else QColor("#1a1a1e")
        self._r, self._g, self._b = c.red(), c.green(), c.blue()

    def set_pixmap(self, px):
        self._px = px
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(self._r, self._g, self._b))
        if self._px and not self._px.isNull():
            sc = self._px.scaled(QSize(w, h),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            sx = max(0, (sc.width() - w) // 2)
            sy = max(0, (sc.height() - h) // 2)
            p.drawPixmap(0, 0, sc, sx, sy, w, h)
        grad = QLinearGradient(0, 0, w, 0)
        r, g, b = self._r, self._g, self._b
        grad.setColorAt(0.0,  QColor(r, g, b, 255))
        grad.setColorAt(0.45, QColor(r, g, b, 220))
        grad.setColorAt(0.75, QColor(r, g, b, 100))
        grad.setColorAt(1.0,  QColor(r, g, b, 20))
        p.fillRect(0, 0, w, h, grad)


# ──────────────────────────────────────────────────────────
#  Main dialog
# ──────────────────────────────────────────────────────────

class MaterialTile(QPushButton):
    """A premium Material You quick-settings-like tile button."""
    def __init__(self, title, subtext, parent=None, is_toggle=True, icon_char=None):
        super().__init__(parent)
        self.setCheckable(is_toggle)
        self.setFixedHeight(50)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title = title
        self.subtext = subtext
        self.icon_char = icon_char
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)
        
        # Optional leading icon
        if icon_char:
            self.icon_lbl = QLabel(icon_char)
            self.icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.icon_lbl.setStyleSheet("font-size: 12pt; font-weight: bold; background: transparent;")
            layout.addWidget(self.icon_lbl)
            
        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.title_lbl = QLabel(title)
        self.title_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl.setStyleSheet("font-weight: bold; font-size: 8.5pt; color: #FFFFFF; background: transparent;")
        
        self.sub_lbl = QLabel(subtext)
        self.sub_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_lbl.setStyleSheet("font-size: 7.5pt; font-style: italic; color: rgba(255, 255, 255, 0.6); background: transparent;")
        
        text_layout.addWidget(self.title_lbl)
        text_layout.addWidget(self.sub_lbl)
        layout.addLayout(text_layout, 1)
        
    def update_state(self, checked, accent_color, active_sub="Active", inactive_sub="Inactive", custom_color=None):
        from utils.color_utils import get_best_foreground_color
        bg_color = custom_color if custom_color else accent_color
        text_color = get_best_foreground_color(bg_color, dark_color="#121214", light_color="#FFFFFF")
        
        if checked:
            self.setChecked(True)
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg_color};
                    border: none;
                    border-radius: 8px;
                }}
                QPushButton:disabled {{
                    background-color: rgba(255, 255, 255, 0.02);
                    border: 1px solid rgba(255, 255, 255, 0.04);
                }}
            """)
            self.title_lbl.setStyleSheet(f"font-weight: bold; font-size: 8.5pt; color: {text_color}; background: transparent;")
            self.sub_lbl.setStyleSheet(f"font-size: 7.5pt; font-style: italic; color: {text_color}; opacity: 0.85; background: transparent;")
            self.sub_lbl.setText(active_sub)
            if hasattr(self, "icon_lbl"):
                self.icon_lbl.setStyleSheet(f"font-size: 12pt; font-weight: bold; color: {text_color}; background: transparent;")
        else:
            self.setChecked(False)
            self.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.05);
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 8px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 0.10);
                    border-color: rgba(255, 255, 255, 0.15);
                }
                QPushButton:disabled {
                    background-color: rgba(255, 255, 255, 0.02);
                    border: 1px solid rgba(255, 255, 255, 0.04);
                }
            """)
            self.title_lbl.setStyleSheet("font-weight: bold; font-size: 8.5pt; color: #FFFFFF; background: transparent;")
            self.sub_lbl.setStyleSheet("font-size: 7.5pt; font-style: italic; color: rgba(255, 255, 255, 0.6); background: transparent;")
            self.sub_lbl.setText(inactive_sub)
            if hasattr(self, "icon_lbl"):
                self.icon_lbl.setStyleSheet("font-size: 12pt; font-weight: bold; color: rgba(255, 255, 255, 0.7); background: transparent;")

        if not self.isEnabled():
            self.title_lbl.setStyleSheet("font-weight: bold; font-size: 8.5pt; color: rgba(255, 255, 255, 0.3); background: transparent;")
            self.sub_lbl.setStyleSheet("font-size: 7.5pt; font-style: italic; color: rgba(255, 255, 255, 0.2); background: transparent;")
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            if not self.isEnabled():
                self.title_lbl.setStyleSheet("font-weight: bold; font-size: 8.5pt; color: rgba(255, 255, 255, 0.3); background: transparent;")
                self.sub_lbl.setStyleSheet("font-size: 7.5pt; font-style: italic; color: rgba(255, 255, 255, 0.2); background: transparent;")
                self.setCursor(Qt.CursorShape.ArrowCursor)
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.title_lbl.setStyleSheet("font-weight: bold; font-size: 8.5pt; color: #FFFFFF; background: transparent;")
                self.sub_lbl.setStyleSheet("font-size: 7.5pt; font-style: italic; color: rgba(255, 255, 255, 0.6); background: transparent;")


class GameDetailsDialogV2(QDialog):
    branches_loaded = pyqtSignal(dict)
    builds_loaded = pyqtSignal(list)
    builds_error = pyqtSignal(str)
    build_depots_loaded = pyqtSignal(str, dict)
    build_depots_error = pyqtSignal(str)

    # ── Rollback toggle: set False to use the old 65px hero layout ──
    USE_V2_HERO = True

    def __init__(self, parent, game_data):
        super().__init__(parent)
        self.parent_window = parent
        self.game_data = game_data
        self.appid = str(game_data.get("appid") or game_data.get("app_id") or "0")
        self.settings = get_settings()
        self._active_fetchers = {}
        self.branches_loaded.connect(self._on_branches_loaded)

        # SteamDB Builds Cache and Scraper
        self.builds_cache = SteamDBBuildsCache()
        self.steamdb_scraper = SteamDBScraper()
        self._cached_build_depots = {}
        self.builds_loaded.connect(self._on_builds_loaded)
        self.builds_error.connect(self._on_builds_error)
        self.build_depots_loaded.connect(self._on_build_depots_loaded)
        self.build_depots_error.connect(self._on_build_depots_error)

        self.accent_color  = getattr(parent, "accent_color",  "#a1c9fd")
        self.background_color = getattr(parent, "background_color", "#111318")

        self.setWindowTitle(f"{game_data.get('game_name', 'Game')} — Details")
        self.setMinimumSize(540, 420)
        self.resize(580, 480)
        self.setModal(True)

        # Check if game supports Steam Workshop in a resource-friendly way
        from utils.workshop_helpers import check_game_has_workshop
        self._has_workshop = check_game_has_workshop(self.appid, self.game_data)

        self._apply_stylesheet()
        self._setup_ui()

        # Load initial cached builds and trigger background SteamDB check
        aid = int(self.appid) if self.appid.isdigit() else 0
        cached_builds, cache_age = self.builds_cache.get_builds_with_age(aid)
        CACHE_FRESH_SECONDS = 3600  # Don't re-scrape if under 1 hour old

        if cached_builds:
            self._populate_builds_cards(cached_builds)
            self.builds_center_stack.setCurrentIndex(1)
            if cache_age < 0 or cache_age >= CACHE_FRESH_SECONDS:
                # Stale (or unknown age) — trigger quiet background refresh
                QTimer.singleShot(250, self._fetch_steamdb_builds_async)
            # Fresh: nothing to do, show as-is
        else:
            self.builds_center_stack.setCurrentIndex(0)
            QTimer.singleShot(250, self._fetch_steamdb_builds_async)


        if self.parent():
            from ui.dialogs.dialog_raiser import DialogRaiser
            DialogRaiser(self.parent(), self)

        # Hook up main progress bar
        main_win = parent.main_window if hasattr(parent, "main_window") else None
        if main_win and hasattr(main_win, "progress_bar"):
            main_win.progress_bar.valueChanged.connect(self._on_main_progress_changed)

        if self._has_workshop:
            self._scan_workshop_mods_async()

    def _on_main_progress_changed(self, value):
        try:
            main_win = self.parent_window.main_window if hasattr(self.parent_window, "main_window") else None
            if main_win and hasattr(main_win, "task_manager") and main_win.task_manager:
                active_job = main_win.task_manager.game_data
                if active_job and str(active_job.get("appid")) == str(self.appid):
                    self.validate_btn.set_progress(value / 100.0)
        except Exception:
            pass

    # ──────────────────────────────────────────
    def _apply_stylesheet(self):
        ac = self.accent_color
        bg = self.background_color
        from utils.color_utils import get_dark_container_color
        sel_bg_hex = get_dark_container_color(ac)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: #FFFFFF;
            }}
            QFrame {{
                border: none;
                background: transparent;
            }}
            QLabel {{
                color: #FFFFFF;
                border: none;
                background: transparent;
                font-size: 9.5pt;
            }}
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 5px 14px;
                font-size: 9.5pt;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.16);
                color: {ac};
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.02);
                color: rgba(255, 255, 255, 0.098);
            }}
            QLineEdit {{
                background-color: rgba(0, 0, 0, 0.196);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.059);
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 9.5pt;
            }}
            QLineEdit:focus {{ border-color: {ac}; }}
            QLineEdit:disabled {{
                color: rgba(255, 255, 255, 0.118);
                border-color: rgba(255, 255, 255, 8);
            }}
            QComboBox {{
                background-color: rgba(0, 0, 0, 0.196);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.059);
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 9.5pt;
            }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox QAbstractItemView {{
                background-color: #1b1b1f;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                selection-background-color: {sel_bg_hex};
                selection-color: #FFFFFF;
                font-size: 9.5pt;
                outline: 0px;
                padding: 4px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 28px;
                padding: 4px 12px;
                color: #E0E0E0;
            }}
            QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{
                background-color: {sel_bg_hex} !important;
                color: #FFFFFF !important;
            }}
            QCheckBox {{
                color: #FFFFFF;
                font-size: 9.5pt;
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 14px; height: 14px;
                border: 1px solid rgba(255, 255, 255, 0.078);
                border-radius: 3px;
                background: rgba(0, 0, 0, 0.157);
            }}
            QCheckBox::indicator:checked {{
                background-color: {ac};
                border-color: {ac};
            }}
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                border: none;
                background: rgba(0, 0, 0, 0.059);
                width: 4px;
                margin: 0px;
                border-radius: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.137);
                min-height: 20px;
                border-radius: 2px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(255, 255, 255, 0.255);
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

    # ──────────────────────────────────────────
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Hero Banner — v2 or legacy
        if self.USE_V2_HERO:
            self._init_hero_v2(root)
        else:
            self._init_hero_legacy(root)

        # Tab Bar
        tab_bar_frame = QFrame()
        tab_bar_frame.setStyleSheet(f"""
            QFrame {{
                background-color: rgba(0, 0, 0, 0.078);
                border-bottom: 1px solid rgba(255, 255, 255, 0.039);
            }}
        """)
        tab_bar_layout = QHBoxLayout(tab_bar_frame)
        tab_bar_layout.setContentsMargins(10, 0, 10, 0)
        tab_bar_layout.setSpacing(0)

        self._tab_buttons = []
        self._pages_info = [("Info", 0), ("Builds", 1), ("Tools", 2)]
        p_idx = 3
        if self._has_workshop:
            self._pages_info.append(("Workshop", p_idx))
            p_idx += 1
        self._tickets_tab_index = p_idx
        self._pages_info.append(("Tickets", p_idx))

        for label, idx in self._pages_info:
            btn = QPushButton(label)
            btn.setFlat(True)
            btn.setCheckable(True)
            btn.setFixedHeight(30)
            btn.setStyleSheet("border: none; border-radius: 0px; padding: 0px 16px; font-size: 9.5pt;")
            btn.clicked.connect(lambda _c, i=idx: self._switch_tab(i))
            tab_bar_layout.addWidget(btn)
            self._tab_buttons.append(btn)
            if label == "Workshop":
                self.ws_tab_btn = btn
                self.ws_page_index = idx
                from utils.dlc_helpers import is_dlc_only_mode
                if is_dlc_only_mode(self.appid):
                    btn.setVisible(False)

        tab_bar_layout.addStretch()

        close_btn = QPushButton("✕ Close")
        close_btn.setFlat(True)
        close_btn.setFixedHeight(30)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                border: none; border-radius: 0;
                padding: 0 10px; font-size: 9.5pt;
                color: rgba(255, 255, 255, 0.6);
            }}
            QPushButton:hover {{ color: {self.accent_color}; }}
        """)
        close_btn.clicked.connect(self.accept)
        tab_bar_layout.addWidget(close_btn)

        root.addWidget(tab_bar_frame)

        # Stacked content
        self.stacked = QStackedWidget()
        self.stacked.setStyleSheet("background: transparent;")
        self._init_info_tab()
        self._init_builds_tab()
        self._init_tools_tab()
        if self._has_workshop:
            self._init_workshop_tab()
        self._init_tickets_tab()
        root.addWidget(self.stacked, 1)

        self._switch_tab(0)
        self._init_slsonline_logic()

    def _switch_tab(self, index):
        self.stacked.setCurrentIndex(index)
        for i, btn in enumerate(self._tab_buttons):
            active = (i == index)
            if active:
                btn.setChecked(True)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        border: none; border-radius: 0;
                        border-bottom: 2px solid {self.accent_color};
                        padding: 0px 16px; font-size: 9.5pt;
                        color: {self.accent_color};
                        font-weight: bold;
                    }}
                """)
            else:
                btn.setChecked(False)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        border: none; border-radius: 0;
                        padding: 0px 16px; font-size: 9.5pt;
                        color: rgba(255, 255, 255, 0.6);
                    }}
                    QPushButton:hover {{ color: {self.accent_color}; }}
                """)

    # ──────────────────────────────────────────
    def _load_hero_image(self):
        if not hasattr(self.parent_window, "_image_cache"):
            return
        cached = self.parent_window._image_cache.get(self.appid)
        if cached:
            px = QPixmap()
            px.loadFromData(cached)
            if not px.isNull():
                self.hero.set_pixmap(px)
            return
        if self.appid not in ("0", "N/A", "unknown") and ImageFetcher:
            url = ImageFetcher.get_header_image_url(self.appid)
            fetcher = ImageFetcher(url)
            def _done(data):
                if data:
                    px = QPixmap()
                    px.loadFromData(data)
                    if not px.isNull():
                        self.hero.set_pixmap(px)
            fetcher.finished.connect(_done)
            fetcher.finished.connect(
                lambda _, k=f"hero_{self.appid}": self._active_fetchers.pop(k, None))
            fetcher.start()
            self._active_fetchers[f"hero_{self.appid}"] = fetcher

    # ──────────────────────────────────────────
    #  Hero v2 — taller card with stats inline
    # ──────────────────────────────────────────
    def _init_hero_v2(self, root):
        self.hero = HeroBanner(bg_hex=self.background_color)
        self.hero.setFixedHeight(125)
        banner_layout = QVBoxLayout(self.hero)
        banner_layout.setContentsMargins(14, 8, 120, 8)
        banner_layout.setSpacing(4)

        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(2)

        # Top-left Ratings badges row (Denuvo + ProtonDB pills)
        self._ratings_row = QHBoxLayout()
        self._ratings_row.setSpacing(6)
        self._ratings_row.setContentsMargins(0, 0, 0, 0)
        self._denuvo_badge_lbl = QLabel()
        self._denuvo_badge_lbl.hide()
        self._proton_badge_lbl = QLabel()
        self._proton_badge_lbl.hide()
        self._ratings_row.addWidget(self._denuvo_badge_lbl)
        self._ratings_row.addWidget(self._proton_badge_lbl)
        self._ratings_row.addStretch()
        left_col.addLayout(self._ratings_row)

        self.name_lbl = QLabel()
        self.name_lbl.setStyleSheet(
            "font-size: 12.5pt; font-weight: bold; color: #FFFFFF; background: transparent;")
        self.name_lbl.setWordWrap(True)
        self.name_lbl.setMaximumHeight(38)
        left_col.addWidget(self.name_lbl)

        self.appid_lbl = QLabel(f"App ID: {self.appid}")
        self.appid_lbl.setStyleSheet(
            "font-size: 8.5pt; color: rgba(255, 255, 255, 0.706); background: transparent; font-weight: bold;")
        left_col.addWidget(self.appid_lbl)

        banner_layout.addLayout(left_col)

        self.update_title()

        # Stats row — horizontal labels under name
        stats_row = QHBoxLayout()
        stats_row.setSpacing(24)
        def _stat_item(label_text, value_text, value_color=None):
            item_widget = QVBoxLayout()
            item_widget.setSpacing(2)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color: {self.accent_color}; font-size: 8.5pt; background: transparent; font-weight: bold;")
            val = QLabel(value_text)
            val.setStyleSheet(
                f"color: {value_color or self.accent_color}; font-size: 9.5pt; font-weight: bold; background: transparent;")
            item_widget.addWidget(lbl)
            item_widget.addWidget(val)
            return item_widget, val

        if self.parent_window and hasattr(self.parent_window, "_format_size"):
            size_str = self.parent_window._format_size(self.game_data.get("size_on_disk", 0))
        else:
            sb = self.game_data.get("size_on_disk", 0) or 0
            if sb < 1024:
                size_str = f"{sb} B"
            elif sb < 1024 * 1024:
                size_str = f"{sb / 1024:.1f} KB"
            elif sb < 1024 * 1024 * 1024:
                size_str = f"{sb / (1024 * 1024):.1f} MB"
            else:
                size_str = f"{sb / (1024 * 1024 * 1024):.2f} GB"
        ri, self.size_val_lbl = _stat_item("SIZE", size_str)
        stats_row.addLayout(ri)

        ri, self.cached_val_lbl = _stat_item("MANIFEST", self._get_manifest_age())
        stats_row.addLayout(ri)

        installed_bid = self._get_installed_buildid()
        bid_str = installed_bid if installed_bid else "Unknown"
        initial_build_color = None
        if installed_bid:
            cached_bid = str(self.game_data.get("buildid", "")) if hasattr(self, "game_data") and isinstance(self.game_data, dict) else ""
            if cached_bid:
                is_old = False
                try:
                    is_old = int(installed_bid) < int(cached_bid)
                except (ValueError, TypeError):
                    is_old = (cached_bid != installed_bid)
                initial_build_color = "#FFB84D" if is_old else "#46b464"

        ri, self.build_val_lbl = _stat_item("BUILD", bid_str, value_color=initial_build_color)
        self._hero_build_val_lbl = self.build_val_lbl
        if installed_bid:
            tip = f"Installed Build: {installed_bid}"
            if initial_build_color == "#FFB84D":
                tip += " (Update available)"
            elif initial_build_color == "#46b464":
                tip += " (Up to date)"
            self.build_val_lbl.setToolTip(tip)
        stats_row.addLayout(ri)

        ri, self.lua_val_lbl = _stat_item("LUA", self._get_lua_age())
        stats_row.addLayout(ri)

        stats_row.addStretch()
        banner_layout.addLayout(stats_row)
        banner_layout.addStretch()

        self._load_hero_image()
        root.addWidget(self.hero)

    # ──────────────────────────────────────────
    #  Hero legacy — original compact 65px banner
    # ──────────────────────────────────────────
    def _init_hero_legacy(self, root):
        self.hero = HeroBanner(bg_hex=self.background_color)
        self.hero.setMinimumHeight(70)
        banner_layout = QHBoxLayout(self.hero)
        banner_layout.setContentsMargins(14, 6, 180, 6)
        banner_layout.setSpacing(0)

        name_col = QVBoxLayout()
        name_col.setSpacing(2)

        # Top-left Ratings badges row (Denuvo + ProtonDB pills)
        self._ratings_row = QHBoxLayout()
        self._ratings_row.setSpacing(6)
        self._ratings_row.setContentsMargins(0, 0, 0, 0)
        self._denuvo_badge_lbl = QLabel()
        self._denuvo_badge_lbl.hide()
        self._proton_badge_lbl = QLabel()
        self._proton_badge_lbl.hide()
        self._ratings_row.addWidget(self._denuvo_badge_lbl)
        self._ratings_row.addWidget(self._proton_badge_lbl)
        self._ratings_row.addStretch()
        name_col.addLayout(self._ratings_row)

        self.name_lbl = QLabel()
        self.name_lbl.setStyleSheet(
            "font-size: 12.5pt; font-weight: bold; color: #FFFFFF; background: transparent;")
        self.name_lbl.setWordWrap(True)
        name_col.addWidget(self.name_lbl)

        self.appid_lbl = QLabel(f"App ID: {self.appid}")
        self.appid_lbl.setStyleSheet(
            "font-size: 8pt; color: rgba(255, 255, 255, 0.4); background: transparent;")
        name_col.addWidget(self.appid_lbl)
        name_col.addStretch()
        banner_layout.addLayout(name_col)
        banner_layout.addStretch()

        self.update_title()

        self._load_hero_image()
        root.addWidget(self.hero)

    def _thin_line(self):
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet("background: rgba(255, 255, 255, 0.031); border: none; max-height: 1px;")
        return f

    def _section_title(self, text):
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(
            f"color: {self.accent_color}; font-size: 8px; font-weight: bold;"
            "letter-spacing: 1px; border: none; background: transparent;")
        return lbl

    def _card_btn(self, text, tooltip=None):
        b = QPushButton(text)
        b.setFixedHeight(25)
        if tooltip:
            b.setToolTip(tooltip)
        return b

    def _init_info_tab(self):
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.uninstall_scroll = scroll  # for auto-scroll when uninstall panel opens

        # ── Actions (Select Branch, Build & Validate) ────────────
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        installed_branch = self.settings.value(f"installed_branch/{self.appid}", "", type=str)
        if not installed_branch:
            installed_branch = self.game_data.get("installed_branch", "public")
        acf_bid = str(self.game_data.get("buildid") or "").strip()
        installed_bid = self.settings.value(
            f"installed_buildid/{self.appid}/{installed_branch}",
            self.settings.value(f"installed_buildid/{self.appid}", acf_bid, type=str),
            type=str)
        if acf_bid and acf_bid != "Unknown" and acf_bid != installed_bid:
            installed_bid = acf_bid
            self.settings.setValue(f"installed_buildid/{self.appid}", acf_bid)
            if installed_branch:
                self.settings.setValue(f"installed_buildid/{self.appid}/{installed_branch}", acf_bid)

        # Default the selected branch to whatever the user has chosen or installed (prioritize non-public branch)
        saved_b = self.settings.value(f"selected_branch/{self.appid}", "", type=str)
        if not saved_b or (installed_branch and installed_branch != "public" and saved_b == "public"):
            saved_b = installed_branch or "public"
        self.settings.setValue(f"selected_branch/{self.appid}", saved_b)

        self.branch_combo = CenteredComboBox()
        self.branch_combo.addItem(f"{saved_b} ({installed_bid})" if installed_bid else saved_b, saved_b)
        self.branch_combo.setFixedHeight(26)
        self.branch_combo.setFixedWidth(200) # Comfortable dropdown width
        self.branch_combo.setMaxVisibleItems(5)
        actions_row.addWidget(self.branch_combo, 0)

        self.validate_btn = ProgressButton("Verify Files", self)
        self.validate_btn.setFixedHeight(26)
        self.validate_btn.setEnabled(True)
        self.validate_btn.setStyleSheet("font-weight: bold; background: rgba(255, 255, 255, 0.047); color: rgba(255, 255, 255, 0.294); border: none;")
        actions_row.addWidget(self.validate_btn, 1)

        lay.addLayout(actions_row)
        lay.addSpacing(10)
        lay.addWidget(self._thin_line())
        lay.addSpacing(10)

        # ── Material You Quick Settings (Top Section Single Row) ───
        top_tiles_widget = QWidget()
        top_tiles_layout = QHBoxLayout(top_tiles_widget)
        top_tiles_layout.setContentsMargins(0, 0, 0, 0)
        top_tiles_layout.setSpacing(6)

        # 1. Status Check Tile (Up to Date / Update Available / Status Unknown)
        self.status_tile = MaterialTile("STATUS UNKNOWN", "Click to check", self, is_toggle=False)
        self.status_tile.clicked.connect(self._on_status_btn_clicked)
        top_tiles_layout.addWidget(self.status_tile, 1)

        # 2. Open Folder Tile (Renamed from "Open Install Folder")
        self.folder_tile = MaterialTile("Open Folder", "Open directory", self, is_toggle=False)
        self.folder_tile.update_state(False, self.accent_color, inactive_sub="Open directory")
        self.folder_tile.clicked.connect(lambda: self.parent_window._open_folder(self.game_data.get("install_path")))
        top_tiles_layout.addWidget(self.folder_tile, 1)

        # 3. DLC Mode Tile
        self.dlc_tile = MaterialTile("DLC Mode", "Inactive", self, is_toggle=True)
        is_dlc = self.settings.value(f"dlc_only_mode/{self.appid}", False, type=bool) if self.settings else False
        self.dlc_tile.update_state(is_dlc, self.accent_color)
        self.dlc_tile.clicked.connect(lambda: self._on_dlc_only_toggled(self.dlc_tile.isChecked()))
        top_tiles_layout.addWidget(self.dlc_tile, 1)

        # 4. Pin Build Tile
        self.pin_tile = MaterialTile("Pin Build", "Inactive", self, is_toggle=True)
        is_pinned = self.settings.value(f"pin_build/{self.appid}", False, type=bool) if self.settings else False
        self.pin_tile.update_state(is_pinned, self.accent_color)
        self.pin_tile.clicked.connect(lambda: self._on_pin_build_toggled(self.pin_tile.isChecked()))
        top_tiles_layout.addWidget(self.pin_tile, 1)

        # 5. Update-All Tile (Renamed from "Exclude Update-All", subtext "Include"/"Exclude")
        is_exclude = self.settings.value(f"exclude_from_update_all/{self.appid}", False, type=bool) if self.settings else False
        self.update_all_tile = MaterialTile("Update-All", "Include" if not is_exclude else "Exclude", self, is_toggle=True)
        self.exclude_tile = self.update_all_tile
        self.update_all_tile.setChecked(not is_exclude)
        self.update_all_tile.update_state(not is_exclude, self.accent_color if not is_exclude else "#e05a47", active_sub="Include", inactive_sub="Exclude")

        if is_pinned:
            self.update_all_tile.setChecked(False)
            self.update_all_tile.update_state(False, "#e05a47", active_sub="Include", inactive_sub="Exclude")
            self.update_all_tile.setEnabled(False)
            if self.settings:
                self.settings.setValue(f"exclude_from_update_all/{self.appid}", True)

        def _update_all_toggled(checked):
            is_exc = not checked
            self.update_all_tile.update_state(checked, self.accent_color if checked else "#e05a47", active_sub="Include", inactive_sub="Exclude")
            if self.settings:
                self.settings.setValue(f"exclude_from_update_all/{self.appid}", is_exc)
        self.update_all_tile.clicked.connect(lambda: _update_all_toggled(self.update_all_tile.isChecked()))
        top_tiles_layout.addWidget(self.update_all_tile, 1)

        lay.addWidget(top_tiles_widget)
        lay.addSpacing(8)

        # ── Bottom Row Section (SLSonline & EOS Proxy) ───────────
        bottom_tiles_widget = QWidget()
        bottom_tiles_layout = QHBoxLayout(bottom_tiles_widget)
        bottom_tiles_layout.setContentsMargins(0, 0, 0, 0)
        bottom_tiles_layout.setSpacing(10)

        # SLSonline Split Button Container (Material 3 Specs)
        sls_container = QFrame()
        sls_container.setFixedHeight(44)
        sls_container.setStyleSheet("""
            QFrame {
                background: transparent;
                border: none;
            }
        """)
        sls_container_layout = QHBoxLayout(sls_container)
        sls_container_layout.setContentsMargins(0, 0, 0, 0)
        sls_container_layout.setSpacing(0)

        self.sls_tile = MaterialTile("SLSonline", "Inactive", self, is_toggle=True)
        sls_container_layout.addWidget(self.sls_tile, 1)

        self.sls_input_container = QFrame()
        self.sls_input_container.setFixedWidth(95)
        self.sls_input_container.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-left: none;
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
            }
        """)
        sls_input_lay = QHBoxLayout(self.sls_input_container)
        sls_input_lay.setContentsMargins(6, 2, 6, 2)
        sls_input_lay.setSpacing(4)

        fl = QLabel("FID:")
        fl.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-size: 8pt; font-weight: bold;")
        self.sls_input = QLineEdit()
        self.sls_input.setPlaceholderText("480")
        self.sls_input.setValidator(QIntValidator())
        self.sls_input.setFixedHeight(22)
        self.sls_input.setFixedWidth(52)
        self.sls_input.setStyleSheet("""
            QLineEdit {
                background: rgba(0, 0, 0, 0.25);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                color: #FFFFFF;
                font-size: 8.5pt;
                font-weight: bold;
                padding: 0 4px;
            }
            QLineEdit:focus {
                border-color: #FFFFFF;
            }
        """)
        sls_input_lay.addWidget(fl)
        sls_input_lay.addWidget(self.sls_input)
        self.sls_input_container.setVisible(False)
        sls_container_layout.addWidget(self.sls_input_container)

        bottom_tiles_layout.addWidget(sls_container, 1)

        # EOS Proxy Tile
        self.eos_tile = MaterialTile("EOS Proxy", "Inactive", self, is_toggle=True)
        self.eos_tile.setVisible(False)
        bottom_tiles_layout.addWidget(self.eos_tile, 1)

        lay.addWidget(bottom_tiles_widget)
        lay.addSpacing(12)
        lay.addWidget(self._thin_line())
        lay.addSpacing(10)



        self.validate_btn.clicked.connect(self._on_validate_btn_clicked)
        self.branch_combo.currentIndexChanged.connect(self._on_branch_combo_changed)

        self._load_branches_immediate()
        self._update_status_ui(self.game_data.get("update_status"))

        if self.parent_window and hasattr(self.parent_window, "game_manager") and self.parent_window.game_manager:
            self.parent_window.game_manager.game_update_status_changed.connect(self._on_status_changed)
            self.parent_window.game_manager.game_hubcap_status_checked.connect(self._on_hubcap_status_changed)
            
            def _cleanup_signals():
                if self.parent_window and hasattr(self.parent_window, "game_manager") and self.parent_window.game_manager:
                    try:
                        self.parent_window.game_manager.game_update_status_changed.disconnect(self._on_status_changed)
                    except Exception:
                        pass
                    try:
                        self.parent_window.game_manager.game_hubcap_status_checked.disconnect(self._on_hubcap_status_changed)
                    except Exception:
                        pass
            self.finished.connect(_cleanup_signals)

        # Create container for Info tab to support floating footer
        info_tab_container = QWidget()
        info_tab_container.setStyleSheet("background: transparent;")
        info_tab_layout = QVBoxLayout(info_tab_container)
        info_tab_layout.setContentsMargins(0, 0, 0, 0)
        info_tab_layout.setSpacing(0)

        info_tab_layout.addWidget(scroll, 1)

        # ── Floating Footer at the bottom ───────────────────────
        self.footer_widget = QWidget()
        self.footer_widget.setObjectName("floatingFooter")
        self.footer_widget.setStyleSheet(f"""
            QWidget#floatingFooter {{
                background-color: {self.background_color};
                border-top: 1px solid rgba(255, 255, 255, 0.08);
            }}
        """)
        footer_layout = QVBoxLayout(self.footer_widget)
        footer_layout.setContentsMargins(14, 10, 14, 10)
        footer_layout.setSpacing(6)

        # Row of buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(10)

        # Define warning/error red colors
        from utils.color_utils import get_semantic_colors
        from PyQt6.QtGui import QColor
        sem_colors = get_semantic_colors(self.accent_color)
        err_color = sem_colors["error"]
        ec = QColor(err_color)

        # Standard Uninstall (Red pastel theme)
        err_bg = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.12)"
        err_border = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.22)"
        err_hover = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.20)"

        # Advanced Uninstall (Subtle dark red theme)
        adv_bg = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.05)"
        adv_border = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.12)"
        adv_hover = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.08)"
        adv_checked_bg = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.18)"
        adv_checked_border = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.35)"

        panel_bg = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.04)"
        panel_border = f"rgba({ec.red()}, {ec.green()}, {ec.blue()}, 0.15)"

        # Left: Standard Uninstall Button
        self._uninstall_pill = QPushButton("Uninstall")
        self._uninstall_pill.setFixedHeight(32)
        self._uninstall_pill.setStyleSheet(f"""
            QPushButton {{
                background: {err_bg};
                color: {err_color};
                border: 1px solid {err_border};
                border-radius: 6px;
                font-weight: bold;
                font-size: 9.5pt;
                padding: 0 16px;
            }}
            QPushButton:hover {{
                background: {err_hover};
            }}
        """)
        self._uninstall_pill.clicked.connect(self._do_standard_uninstall)
        btn_row.addWidget(self._uninstall_pill, 1)

        # Right: Advanced Uninstall Button
        self._adv_uninstall_btn = QPushButton("Advanced Uninstall")
        self._adv_uninstall_btn.setFixedHeight(32)
        self._adv_uninstall_btn.setCheckable(True)
        self._adv_uninstall_btn.setStyleSheet(f"""
            QPushButton {{
                background: {adv_bg};
                color: {err_color};
                border: 1px solid {adv_border};
                border-radius: 6px;
                font-weight: bold;
                font-size: 9.5pt;
                padding: 0 16px;
            }}
            QPushButton:hover {{
                background: {adv_hover};
            }}
            QPushButton:checked {{
                background: {adv_checked_bg};
                color: #FFFFFF;
                border-color: {adv_checked_border};
            }}
        """)
        self._adv_uninstall_btn.clicked.connect(self._toggle_uninstall_panel)
        btn_row.addWidget(self._adv_uninstall_btn, 1)

        footer_layout.addLayout(btn_row)

        # Expandable Advanced Panel
        self._uninstall_expanded = False
        self._uninstall_panel = QFrame()
        self._uninstall_panel.setObjectName("uninstallPanel")
        self._uninstall_panel.setStyleSheet(f"""
            QFrame#uninstallPanel {{
                background-color: {panel_bg};
                border: 1px solid {panel_border};
                border-radius: 4px;
            }}
            QFrame#uninstallPanel QLabel {{
                border: none;
                background: transparent;
            }}
        """)
        self._uninstall_panel.setVisible(False)
        self._uninstall_inner = QVBoxLayout(self._uninstall_panel)
        self._uninstall_inner.setContentsMargins(10, 8, 10, 8)
        self._uninstall_inner.setSpacing(6)
        self._uninstall_content = QVBoxLayout()
        self._uninstall_inner.addLayout(self._uninstall_content)
        self._build_uninstall_panel()

        footer_layout.addWidget(self._uninstall_panel)
        info_tab_layout.addWidget(self.footer_widget)

        self.stacked.addWidget(info_tab_container)

    def _load_branches_immediate(self):
        """
        Load branches synchronously from the DB cache so the combo and
        validate button render correctly the moment the dialog opens.
        If DB has data, call _on_branches_loaded directly (main thread — no signal delay).
        Regardless, fire a background refresh to keep the DB warm.
        """
        import threading
        appid = self.appid
        loaded_from_cache = False

        try:
            from managers.db_manager import DatabaseManager
            db = DatabaseManager()
            app_info = db.get_app_info(appid, bypass_expiration=True)
            cached_branches = app_info.get("branches") if app_info else None
            if cached_branches and isinstance(cached_branches, dict) and len(cached_branches) > 0:
                # Synchronous call — runs on the main thread immediately
                self._on_branches_loaded(cached_branches)
                loaded_from_cache = True
            elif app_info and app_info.get("buildid"):
                fallback = {"public": {"buildid": str(app_info.get("buildid"))}}
                self._on_branches_loaded(fallback)
            elif self.game_data.get("buildid"):
                fallback = {"public": {"buildid": str(self.game_data.get("buildid"))}}
                self._on_branches_loaded(fallback)
        except Exception:
            pass

        # Always fire a background refresh (silent — won't stutter UI since combo is already populated)
        threading.Thread(target=self._silent_refresh_branches, daemon=True).start()

        if not loaded_from_cache:
            # No DB data at all — kick off a full live fetch via the normal async path
            self._load_branches_async(force_refresh=True)

    def _load_branches_async(self, force_refresh: bool = False):
        import threading
        appid = self.appid

        # Fast path: use cached data from DatabaseManager without a Steam connection
        if not force_refresh:
            try:
                from managers.db_manager import DatabaseManager
                db = DatabaseManager()
                app_info = db.get_app_info(appid, bypass_expiration=True)
                cached_branches = app_info.get("branches") if app_info else None
                if cached_branches and isinstance(cached_branches, dict) and len(cached_branches) > 0:
                    self.branches_loaded.emit(cached_branches)
                    # Fire a background refresh to keep cache warm silently
                    threading.Thread(target=lambda: self._silent_refresh_branches(), daemon=True).start()
                    return
                elif app_info and app_info.get("buildid"):
                    fallback = {"public": {"buildid": str(app_info.get("buildid"))}}
                    self.branches_loaded.emit(fallback)
                    threading.Thread(target=lambda: self._silent_refresh_branches(), daemon=True).start()
                    return
            except Exception:
                pass

        # Slow path: live fetch
        def _fetch():
            try:
                from core.steam_api import get_app_branches
                return get_app_branches(appid, force_refresh=True)
            except BaseException as e:
                logger.error(f"Error fetching branches for {appid}: {e}")
                return {"public": {"buildid": ""}}

        def run_thread():
            b_data = _fetch()
            self.branches_loaded.emit(b_data)

        threading.Thread(target=run_thread, daemon=True).start()

    def _silent_refresh_branches(self):
        try:
            from core.steam_api import get_app_branches
            fresh = get_app_branches(self.appid, force_refresh=True)
            if fresh:
                self.branches_loaded.emit(fresh)
        except BaseException:
            pass

    def _on_branches_loaded(self, branches_dict: dict):
        try:
            if not branches_dict or not isinstance(branches_dict, dict):
                branches_dict = {"public": {"buildid": str(self.game_data.get("buildid") or "")}}
            self._branches_dict = branches_dict
            self.branch_combo.blockSignals(True)
            self.branch_combo.clear()

            sorted_keys = sorted(branches_dict.keys(), key=lambda k: (0 if k == "public" else 1, k))
            installed_branch = self.settings.value(f"installed_branch/{self.appid}", "", type=str)
            if not installed_branch:
                installed_branch = self.game_data.get("installed_branch", "public")
            saved_branch = self.settings.value(f"selected_branch/{self.appid}", "", type=str)
            if not saved_branch or (installed_branch and installed_branch != "public" and saved_branch == "public"):
                saved_branch = installed_branch or "public"
            select_idx = 0

            for idx, b_name in enumerate(sorted_keys):
                b_info = branches_dict[b_name]
                bid = str(b_info.get("buildid", "")) if isinstance(b_info, dict) else ""
                label = f"{b_name} ({bid})" if bid else b_name
                self.branch_combo.addItem(label, b_name)
                if b_name == saved_branch:
                    select_idx = idx

            self.branch_combo.setCurrentIndex(select_idx)
        except Exception as e:
            logger.error(f"Error in _on_branches_loaded: {e}", exc_info=True)
            self.branch_combo.clear()
            installed_bid = self.settings.value(f"installed_buildid/{self.appid}", str(self.game_data.get("buildid") or ""))
            self.branch_combo.addItem(f"public ({installed_bid})" if installed_bid else "public", "public")
        finally:
            self.branch_combo.blockSignals(False)
            try:
                self._on_branch_combo_changed()
            except Exception as e:
                logger.error(f"Error in _on_branch_combo_changed: {e}", exc_info=True)

    def _on_branch_combo_changed(self):
        sel_branch = self.branch_combo.currentData() or "public"
        self.settings.setValue(f"selected_branch/{self.appid}", sel_branch)

        b_dict = getattr(self, "_branches_dict", {})
        b_info = b_dict.get(sel_branch, {}) if isinstance(b_dict, dict) else {}
        branch_bid = str(b_info.get("buildid", "")) if isinstance(b_info, dict) else ""

        installed_bid = self._get_installed_buildid()
        installed_branch = self.settings.value(f"installed_branch/{self.appid}", "public", type=str)
        if installed_bid and installed_branch == sel_branch:
            self.settings.setValue(f"installed_buildid/{self.appid}/{sel_branch}", installed_bid)
            self.settings.setValue(f"installed_buildid/{self.appid}", installed_bid)

        # Update Build ID display in hero banner:
        # Show strictly the INSTALLED build ID.
        # If older than latest on the selected branch -> Orange (#FFB84D).
        # If up to date with latest -> Green (#46b464).
        if hasattr(self, "build_val_lbl"):
            if installed_bid:
                is_older = False
                if branch_bid and sel_branch == installed_branch:
                    try:
                        is_older = int(installed_bid) < int(branch_bid)
                    except (ValueError, TypeError):
                        is_older = (branch_bid != installed_bid)

                self.build_val_lbl.setText(installed_bid)
                if is_older:
                    self.build_val_lbl.setStyleSheet("color: #FFB84D; font-size: 9.5pt; font-weight: bold; background: transparent;")
                    self.build_val_lbl.setToolTip(f"Installed Build: {installed_bid}\nLatest on Steam ({sel_branch}): Build {branch_bid} (Update available)")
                else:
                    self.build_val_lbl.setStyleSheet("color: #46b464; font-size: 9.5pt; font-weight: bold; background: transparent;")
                    self.build_val_lbl.setToolTip(f"Installed Build: {installed_bid} (Up to date)")
            else:
                if branch_bid:
                    self.build_val_lbl.setText(branch_bid)
                    self.build_val_lbl.setStyleSheet("color: #7ab3ff; font-size: 9.5pt; font-weight: bold; background: transparent;")
                    self.build_val_lbl.setToolTip(f"Latest on Steam ({sel_branch}): Build {branch_bid}\n(Game not installed or build ID unknown)")
                else:
                    self.build_val_lbl.setText("Unknown")
                    self.build_val_lbl.setStyleSheet(f"color: {self.accent_color}; font-size: 9.5pt; font-weight: bold; background: transparent;")

        self._update_validate_button()

    def _update_validate_button(self):
        sel_branch = self.branch_combo.currentData() or "public" if hasattr(self, "branch_combo") else "public"
        installed_branch = self.settings.value(f"installed_branch/{self.appid}", "public", type=str)
        pinned = self.settings.value(f"pin_build/{self.appid}", False, type=bool) if self.settings else False
        if pinned:
            self._reconstruct_manifests_from_depotcache()
        installed_bid = self.settings.value(f"installed_buildid/{self.appid}", "") if self.settings else ""

        # Check if a cached manifest zip exists for this branch
        manifests_dir = get_base_path() / "hubcap_manifests"
        specific_zip = manifests_dir / f"accela_fetch_{self.appid}_build_{installed_bid}.zip" if installed_bid else None

        if pinned and specific_zip and specific_zip.exists():
            local_zip = specific_zip
            has_cache = True
        elif sel_branch != "public":
            local_zip = manifests_dir / f"accela_fetch_{self.appid}_branch_{sel_branch}.zip"
            has_cache = local_zip.exists()
        else:
            local_zip = manifests_dir / f"accela_fetch_{self.appid}.zip"
            has_cache = local_zip.exists()

        same_branch = (installed_branch == sel_branch)

        from managers.depot_key_manager import DepotKeyManager
        dkm = DepotKeyManager()
        has_keys = dkm.has_depot_keys(self.appid)
        is_missing_manifest_or_lua = (not has_cache) or (not has_keys)

        self.validate_btn.setEnabled(True)

        # Derive auto colors based on the theme's accent color
        accent_hex = self.accent_color
        try:
            accent_qcolor = QColor(accent_hex)
            h, s, v, a = accent_qcolor.getHsv()
            # Derive theme-harmonized success (green) color: hue 120
            s_s = max(s, 100)
            v_s = max(v, 120)
            success_qcolor = QColor.fromHsv(120, s_s, v_s, a)
            success_hex = success_qcolor.name()
        except Exception:
            success_hex = "#46b464"  # Fallback green

        # Reset button progress/loading if no job is active for this game
        main_win = self.parent_window.main_window if hasattr(self.parent_window, "main_window") else None
        active_job = main_win.task_manager.game_data if (main_win and hasattr(main_win, "task_manager") and main_win.task_manager) else None
        if not active_job or str(active_job.get("appid")) != str(self.appid):
            self.validate_btn.set_progress(0.0)

        from PyQt6.QtGui import QPalette
        from utils.color_utils import get_best_foreground_color

        def set_btn_style(base_hex):
            # Programmatically compute the highest-contrast foreground color to satisfy WCAG AA/AAA.
            text_hex = get_best_foreground_color(base_hex, dark_color="#121214", light_color="#FFFFFF")
            
            base_qcolor = QColor(base_hex)
            h, s, v, a = base_qcolor.getHsv()
            val_hover = min(255, int(v * 1.15)) if v > 0 else 30
            val_pressed = int(v * 0.85)
            hover_qcolor = QColor.fromHsv(h, s, val_hover, a)
            pressed_qcolor = QColor.fromHsv(h, s, val_pressed, a)
            
            from utils.color_utils import get_dark_container_color
            disabled_bg = get_dark_container_color(base_hex)
            
            self.validate_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {base_hex};
                    color: {text_hex};
                    font-weight: bold;
                    border: none;
                    border-radius: 6px;
                }}
                QPushButton:hover {{
                    background-color: {hover_qcolor.name()};
                }}
                QPushButton:pressed {{
                    background-color: {pressed_qcolor.name()};
                }}
                QPushButton:disabled {{
                    background-color: {disabled_bg};
                    color: rgba(255, 255, 255, 0.4);
                }}
            """)
            palette = self.validate_btn.palette()
            palette.setColor(QPalette.ColorRole.Highlight, base_qcolor)
            self.validate_btn.setPalette(palette)

        if pinned and has_cache and not is_missing_manifest_or_lua:
            self.validate_btn.setText("Verify Pinned Build")
            set_btn_style(success_hex)
        elif not same_branch:
            b_dict = getattr(self, "_branches_dict", {})
            branch_bid = ""
            if isinstance(b_dict, dict):
                b_info = b_dict.get(sel_branch, {})
                if isinstance(b_info, dict):
                    branch_bid = str(b_info.get("buildid", ""))
            label = f"Install {sel_branch}"
            if branch_bid:
                label += f" ({branch_bid})"
            self.validate_btn.setText(label)
            set_btn_style(accent_hex)
        elif is_missing_manifest_or_lua:
            self.validate_btn.setText("Refetch")
            set_btn_style(accent_hex)
        elif self.game_data.get("update_status") == "update_available":
            self.validate_btn.setText("Download Update")
            set_btn_style(accent_hex)
        else:
            self.validate_btn.setText("Verify Files")
            set_btn_style(success_hex)

    def _on_validate_btn_clicked(self):
        sel_branch = self.branch_combo.currentData() or "public" if hasattr(self, "branch_combo") else "public"
        btn_text = self.validate_btn.text()

        self.validate_btn.set_loading(True)
        self.validate_btn.setEnabled(False)
        self.validate_btn.setToolTip("Task in progress...")

        if btn_text == "Refetch":
            self.parent_window._fetch_game_manifest(
                self.game_data, self, branch=sel_branch, download_only=True
            )
        else:
            # Check if pinned build is active and we want to use the specific backup manifest
            pinned = self.settings.value(f"pin_build/{self.appid}", False, type=bool) if self.settings else False
            installed_bid = self.settings.value(f"installed_buildid/{self.appid}", "") if self.settings else ""
            manifests_dir = get_base_path() / "hubcap_manifests"
            specific_zip = manifests_dir / f"accela_fetch_{self.appid}_build_{installed_bid}.zip" if installed_bid else None
            
            local_path_override = None
            if pinned and specific_zip and specific_zip.exists():
                local_path_override = str(specific_zip)
                
            self.parent_window._fetch_game_manifest(
                self.game_data, self, branch=sel_branch, download_only=False, local_path_override=local_path_override
            )



    def _get_available_depots(self) -> dict:
        """Resolves all valid depots for this game with multi-tier fast local fallbacks (non-blocking)."""
        depots_dict = {}

        # Tier 1: In-memory game_data
        if isinstance(self.game_data.get("installed_depots"), dict) and self.game_data["installed_depots"]:
            depots_dict = dict(self.game_data["installed_depots"])
        elif isinstance(self.game_data.get("depots"), dict) and self.game_data["depots"]:
            depots_dict = dict(self.game_data["depots"])

        # Tier 2: In-memory cached build depots from current session
        if not depots_dict and hasattr(self, "_cached_build_depots") and self._cached_build_depots:
            for bd in self._cached_build_depots.values():
                if isinstance(bd, dict):
                    for d_id, d_info in bd.items():
                        depots_dict[str(d_id)] = d_info

        # Tier 3: SteamDB cached builds in SQLite
        if not depots_dict and hasattr(self, "builds_cache"):
            try:
                aid = int(self.appid) if self.appid.isdigit() else 0
                c_builds = self.builds_cache.get_builds(aid)
                for b in c_builds:
                    if b.get("depots") and isinstance(b["depots"], dict):
                        for d_id, d_info in b["depots"].items():
                            depots_dict[str(d_id)] = d_info
            except Exception:
                pass

        # Tier 4: DatabaseManager apps table
        if not depots_dict:
            try:
                from managers.db_manager import DatabaseManager
                from ui.assets import DEPOT_BLACKLIST
                string_blacklist = {str(item) for item in DEPOT_BLACKLIST}
                db = DatabaseManager()
                with db._conn_lock:
                    cur = db.conn.cursor()
                    cur.execute("SELECT depots_json FROM apps WHERE appid = ?", (self.appid,))
                    row = cur.fetchone()
                    if row and row["depots_json"]:
                        depots_data = db._decompress_depots(row["depots_json"], self.appid)
                        if depots_data:
                            depots_dict = {
                                k: v for k, v in depots_data.items()
                                if k not in ("branches", "workshopdepots", "branches_public")
                                and k not in string_blacklist
                                and isinstance(v, dict)
                            }
            except Exception as e:
                logger.debug(f"Depot resolution from DB failed: {e}")

        # Tier 5: Parse local ACF file directly if present
        if not depots_dict:
            acf_path = self.game_data.get("appmanifest_path")
            if acf_path and os.path.exists(acf_path):
                try:
                    import vdf
                    with open(acf_path, "r", encoding="utf-8") as f:
                        vd = vdf.loads(f.read())
                    ins_depots = vd.get("AppState", {}).get("InstalledDepots")
                    if isinstance(ins_depots, dict) and ins_depots:
                        depots_dict = ins_depots
                except Exception:
                    pass

        return depots_dict

    def _on_manual_rollback_clicked(self):
        from ui.dialogs.rollback_dialogs import ManualRollbackDialog
        depots = self._get_available_depots()
        dialog = ManualRollbackDialog(
            parent=self,
            appid=self.appid,
            game_name=self.game_data.get("game_name", ""),
            depots_dict=depots,
            accent_color=self.accent_color,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._trigger_rollback_job(
                dialog.selected_depot_id,
                dialog.selected_build_id,
                dialog.selected_manifest_id,
                pin_build=dialog.should_pin_build
            )

    def _on_steamdb_history_clicked(self):
        from ui.dialogs.rollback_dialogs import SteamDBHistoryDialog
        dialog = SteamDBHistoryDialog(
            parent=self,
            appid=self.appid,
            game_name=self.game_data.get("game_name", ""),
            accent_color=self.accent_color,
        )
        dialog.rollback_requested.connect(
            lambda depot_id, build_id, manifest_id: self._trigger_rollback_job(
                depot_id, build_id, manifest_id, pin_build=True
            )
        )
        dialog.exec()

    def _trigger_rollback_job(self, depot_id: str, build_id: str, manifest_id: str, pin_build: bool = True):
        logger.info(f"[DEBUG_DEV] Triggering rollback job. AppID: {self.appid}, Depot: {depot_id}, Build: {build_id}, Manifest: {manifest_id}, Pin: {pin_build}")

        if not depot_id or not build_id or not manifest_id:
            QMessageBox.warning(self, "Missing Fields", "Please specify Depot ID, Build ID, and Manifest ID.")
            return

        self._last_rollback_pin_build = pin_build

        from utils.helpers import get_base_path
        manifest_filename = f"{depot_id}_{manifest_id}.manifest"
        global_manifests_dir = get_base_path() / "manifests"
        src_manifest_path = global_manifests_dir / manifest_filename

        if src_manifest_path.exists():
            logger.info(f"[DEBUG_DEV] Manifest already exists locally at {src_manifest_path}. Proceeding directly.")
            self._do_package_and_submit_manual_job(src_manifest_path, manifest_filename, depot_id, build_id, manifest_id, pin_build=pin_build)
            return

        # Manifest does not exist locally. Try to fetch from Hubcap /generate/manifest endpoint.
        logger.info("[DEBUG_DEV] Manifest missing locally. Attempting to download from Hubcap...")
        
        # Show a progress dialog
        from PyQt6.QtWidgets import QProgressDialog
        progress = QProgressDialog("Generating manifest from Hubcap...", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.show()

        import threading
        
        def _fetch_thread():
            error_msg = None
            try:
                from core.morrenus_api import get_session, _get_headers
                headers = _get_headers()
                if not headers:
                    error_msg = "API key not configured in settings."
                else:
                    url = f"https://hubcapmanifest.com/api/v1/generate/manifest?depot_id={depot_id}&manifest_id={manifest_id}"
                    from utils.isp_bypass import execute_hubcap_request
                    r = execute_hubcap_request(get_session(), "GET", url, headers=headers, timeout=30)
                    if r.status_code == 200:
                        global_manifests_dir.mkdir(parents=True, exist_ok=True)
                        with open(src_manifest_path, "wb") as f:
                            f.write(r.content)
                        logger.info(f"[DEBUG_DEV] Successfully generated, downloaded, and saved manifest: {src_manifest_path}")
                    else:
                        try:
                            detail = r.json().get("detail", r.text)
                        except Exception:
                            detail = r.text
                        error_msg = f"Hubcap returned status code {r.status_code}: {detail}"
            except Exception as e:
                logger.error(f"[DEBUG_DEV] Error generating manifest from Hubcap: {e}", exc_info=True)
                error_msg = str(e)

            # Signal completion back to the main thread
            from PyQt6.QtCore import QMetaObject, Q_ARG
            QMetaObject.invokeMethod(
                self,
                "_on_manifest_fetch_completed",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, error_msg or ""),
                Q_ARG(str, str(src_manifest_path)),
                Q_ARG(str, manifest_filename),
                Q_ARG(str, str(depot_id)),
                Q_ARG(str, str(build_id)),
                Q_ARG(str, str(manifest_id)),
                Q_ARG(object, progress)
            )

        threading.Thread(target=_fetch_thread, daemon=True).start()

    from PyQt6.QtCore import pyqtSlot
    @pyqtSlot(str, str, str, str, str, str, object)
    def _on_manifest_fetch_completed(self, error_msg, src_manifest_path_str, manifest_filename, depot_id, build_id, manifest_id, progress_dialog):
        if progress_dialog:
            try:
                progress_dialog.close()
            except Exception:
                pass

        if error_msg:
            from utils.helpers import get_base_path
            global_manifests_dir = get_base_path() / "manifests"
            QMessageBox.critical(
                self,
                "Manifest Retrieval Failed",
                f"Failed to automatically download manifest from Hubcap.\n\n"
                f"Error: {error_msg}\n\n"
                f"Please manually place your manifest file '{manifest_filename}' into:\n"
                f"{global_manifests_dir}/\n\n"
                "Then try again."
            )
            return

        from pathlib import Path
        src_manifest_path = Path(src_manifest_path_str)
        pin_build = getattr(self, "_last_rollback_pin_build", True)
        self._do_package_and_submit_manual_job(src_manifest_path, manifest_filename, depot_id, build_id, manifest_id, pin_build=pin_build)

    def _do_package_and_submit_manual_job(self, src_manifest_path, manifest_filename, depot_id, build_id, manifest_id, pin_build: bool = True):
        import zipfile
        from utils.helpers import get_base_path

        manifests_dir = get_base_path() / "hubcap_manifests"
        manifests_dir.mkdir(parents=True, exist_ok=True)
        local_zip_path = manifests_dir / f"accela_fetch_{self.appid}_branch_manual.zip"

        try:
            with zipfile.ZipFile(local_zip_path, "w", zipfile.ZIP_DEFLATED) as zip_ref:
                zip_ref.write(src_manifest_path, manifest_filename)
            logger.info("[DEBUG_DEV] Successfully packaged manifest file into zip.")
            
            # Copy to specific build zip for future verification
            import shutil
            specific_zip_path = manifests_dir / f"accela_fetch_{self.appid}_build_{build_id}.zip"
            shutil.copy(local_zip_path, specific_zip_path)
            logger.info(f"Cached manual manifest zip to {specific_zip_path}")
            
            # Update Pin Build setting based on pin_build argument
            if self.settings:
                self.settings.setValue(f"pin_build/{self.appid}", pin_build)
                self.settings.setValue(f"exclude_from_update_all/{self.appid}", False)
                self.settings.setValue(f"installed_buildid/{self.appid}", build_id)
            if hasattr(self, "pin_tile") and self.pin_tile:
                self.pin_tile.setChecked(True)
                self.pin_tile.update_state(True, self.accent_color)
            if hasattr(self, "exclude_tile") and self.exclude_tile:
                self.exclude_tile.setChecked(False)
                self.exclude_tile.update_state(False, "#e05a47")
                self.exclude_tile.setEnabled(False)
        except Exception as e:
            logger.error(f"[DEBUG_DEV] Failed to create temporary manifest zip: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to package manifest file: {e}")
            return


        # Prepare game data override
        game_data = self.game_data.copy()
        game_data["buildid"] = build_id
        game_data["branch"] = "public"  # Avoid passing non-existent branch to DepotDownloader
        game_data["_is_rollback"] = True # Prevent marking game as up-to-date and skipping stage2 warnings
        game_data.setdefault("manifests", {})[depot_id] = manifest_id

        # Load and verify depots from DB to ensure decryption keys are present
        depots_dict = {}
        try:
            from managers.db_manager import DatabaseManager
            from ui.assets import DEPOT_BLACKLIST
            string_blacklist = {str(item) for item in DEPOT_BLACKLIST}
            db = DatabaseManager()
            with db._conn_lock:
                cur = db.conn.cursor()
                cur.execute("SELECT depots_json FROM apps WHERE appid = ?", (self.appid,))
                row = cur.fetchone()
                if row and row["depots_json"]:
                    depots_data = db._decompress_depots(row["depots_json"], self.appid)
                    if depots_data:
                        depots_dict = {
                            k: v for k, v in depots_data.items()
                            if k not in ("branches", "workshopdepots", "branches_public")
                            and k not in string_blacklist
                            and isinstance(v, dict)
                        }
            logger.info(f"[DEBUG_DEV] Loaded {len(depots_dict)} depots from DB for job app {self.appid}")
        except Exception as e:
            logger.error(f"[DEBUG_DEV] Failed to load depots from DB directly for job: {e}", exc_info=True)

        if depots_dict:
            game_data["depots"] = depots_dict
        else:
            game_data.setdefault("depots", {})

        # Submit the job
        logger.info(f"[DEBUG_DEV] Submitting job with zip: {local_zip_path} and game_data: {game_data}")
        self.parent_window._submit_job(str(local_zip_path), game_data, self)




    # ──────────────────────────────────────────
    def _build_uninstall_panel(self):
        while self._uninstall_content.count():
            item = self._uninstall_content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        from utils.dlc_helpers import is_dlc_only_mode, get_dlc_only_info
        is_dlc = is_dlc_only_mode(self.appid)

        if is_dlc:
            dlc_list = get_dlc_only_info(self.appid)
            info = QLabel("Choose DLC depot files to remove:")
            info.setStyleSheet("color: #ff8a7a; font-size: 9.5pt; background: transparent;")
            self._uninstall_content.addWidget(info)
            self._dlc_checkboxes = {}
            for dlc in (dlc_list or []):
                did = dlc.get("dlc_appid", "")
                dname = dlc.get("dlc_name") or did
                cb = QCheckBox(f"{dname}  ({did})")
                cb.setChecked(True)
                cb.setStyleSheet("color: #ffd0c8; font-size: 9.5pt; background: transparent;")
                self._dlc_checkboxes[did] = cb
                self._uninstall_content.addWidget(cb)
            if not dlc_list:
                self._uninstall_content.addWidget(QLabel("No DLC depot info found."))
            confirm = QPushButton("Remove Selected DLC Files")
            confirm.setFixedHeight(25)
            confirm.setStyleSheet("""
                QPushButton { background: rgba(160, 40, 30, 0.235); color: #ff8a7a;
                    border: none; font-size: 9.5pt; font-weight: bold; }
                QPushButton:hover { background: rgba(180, 50, 35, 0.314); }
            """)
            confirm.clicked.connect(self._do_dlc_uninstall)
            self._uninstall_content.addWidget(confirm)
        else:
            warn = QLabel(
                f"Permanently removes files for '{self.game_data.get('game_name','this game')}'.")
            warn.setStyleSheet("color: #ff8a7a; font-size: 9.5pt; background: transparent; border: none;")
            warn.setWordWrap(True)
            self._uninstall_content.addWidget(warn)

            self._uninstall_opts = {}
            options = [("wipe_sls_only", "I bought the game")]
            if platform.system() == "Linux":
                options.extend([
                    ("compat", "Remove Proton/Wine prefix"),
                    ("saves", "Remove local cloud saves"),
                ])

            for key, text in options:
                cb = QCheckBox(text)
                cb.setStyleSheet("color: #ffd0c8; font-size: 9.5pt; background: transparent;")
                self._uninstall_opts[key] = cb
                self._uninstall_content.addWidget(cb)

            confirm = QPushButton("Confirm Uninstall")
            confirm.setFixedHeight(28)
            confirm.setCursor(Qt.CursorShape.PointingHandCursor)
            self._uninstall_content.addWidget(confirm)

            def _update_uninstall_ui():
                is_bought_mode = bool(self._uninstall_opts.get("wipe_sls_only") and self._uninstall_opts["wipe_sls_only"].isChecked())

                # Disable and uncheck prefix / save deletion if "I bought the game" is checked
                for k in ("compat", "saves"):
                    if k in self._uninstall_opts:
                        self._uninstall_opts[k].setEnabled(not is_bought_mode)
                        if is_bought_mode:
                            self._uninstall_opts[k].setChecked(False)

                if is_bought_mode:
                    confirm.setText("Take away my sins")
                    confirm.setStyleSheet("""
                        QPushButton {
                            background: rgba(34, 197, 94, 0.25);
                            color: #4ADE80;
                            border: 1px solid rgba(34, 197, 94, 0.4);
                            border-radius: 4px;
                            font-size: 9.5pt;
                            font-weight: bold;
                        }
                        QPushButton:hover {
                            background: rgba(34, 197, 94, 0.35);
                        }
                    """)
                else:
                    confirm.setText("Confirm Uninstall")
                    confirm.setStyleSheet("""
                        QPushButton {
                            background: rgba(160, 40, 30, 0.235);
                            color: #ff8a7a;
                            border: none;
                            border-radius: 4px;
                            font-size: 9.5pt;
                            font-weight: bold;
                        }
                        QPushButton:hover {
                            background: rgba(180, 50, 35, 0.314);
                        }
                    """)

            # Connect all checkboxes to update the dynamic UI
            for cb in self._uninstall_opts.values():
                cb.toggled.connect(lambda _: _update_uninstall_ui())

            _update_uninstall_ui()

            confirm.clicked.connect(
                lambda: self.parent_window._uninstall_game(
                    self.game_data, self, {
                        key: cb.isChecked() for key, cb in getattr(self, "_uninstall_opts", {}).items()
                    }))

    def _do_standard_uninstall(self):
        from utils.dlc_helpers import is_dlc_only_mode, get_dlc_only_info
        if is_dlc_only_mode(self.appid):
            dlc_list = get_dlc_only_info(self.appid)
            all_dlc_ids = [dlc.get("dlc_appid") for dlc in (dlc_list or []) if dlc.get("dlc_appid")]
            gd = dict(self.game_data)
            gd["_dlc_uninstall_ids"] = all_dlc_ids
            self.parent_window._uninstall_game(gd, self, {})
        else:
            self.parent_window._uninstall_game(
                self.game_data, self, {"compat": False, "saves": False, "wipe_sls": True, "wipe_sls_only": False}
            )

    def _toggle_uninstall_panel(self):
        self._uninstall_expanded = not self._uninstall_expanded
        self._uninstall_panel.setVisible(self._uninstall_expanded)
        if hasattr(self, "_adv_uninstall_btn") and self._adv_uninstall_btn:
            self._adv_uninstall_btn.setChecked(self._uninstall_expanded)
        if self._uninstall_expanded and hasattr(self, "uninstall_scroll"):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(100, lambda: self._uninstall_scroll_to_bottom())

    def _uninstall_scroll_to_bottom(self):
        sb = self.uninstall_scroll.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _do_dlc_uninstall(self):
        checked = [did for did, cb in getattr(self, "_dlc_checkboxes", {}).items() if cb.isChecked()]
        if not checked:
            QMessageBox.information(self, "Nothing selected", "Select at least one DLC to remove.")
            return
        gd = dict(self.game_data)
        gd["_dlc_uninstall_ids"] = checked
        self.parent_window._uninstall_game(gd, self, {})

    def _on_dlc_only_toggled(self, state):
        if hasattr(self, "dlc_tile") and self.dlc_tile:
            self.dlc_tile.update_state(state, self.accent_color)
        if self.settings:
            self.settings.setValue(f"dlc_only_mode/{self.appid}", state)
            try:
                from utils.yaml_config_manager import get_user_config_path
                from utils.dlc_helpers import sync_dlc_only_sls_config
                cp = get_user_config_path()
                if cp.exists():
                    sync_dlc_only_sls_config(
                        cp, self.appid, self.game_data.get("game_name", ""), self.game_data
                    )
            except Exception as e:
                logger.debug(f"DLC sync error: {e}")
        self._build_uninstall_panel()
        self._refresh_drm_emulation_state()
        self.update_title()

        # Update SLSonline & EOS Proxy interactivity
        if state:
            if hasattr(self, "sls_tile") and self.sls_tile:
                self.sls_tile.setEnabled(False)
                self.sls_tile.setToolTip("Not available in DLC-Only mode")
            if hasattr(self, "sls_input") and self.sls_input:
                self.sls_input.setEnabled(False)
            if hasattr(self, "eos_tile") and self.eos_tile:
                self.eos_tile.setEnabled(False)
                self.eos_tile.setToolTip("Not available in DLC-Only mode")
            # Workshop tab: hide in real-time
            if hasattr(self, "ws_tab_btn") and self.ws_tab_btn:
                self.ws_tab_btn.setVisible(False)
                if hasattr(self, "ws_page_index") and self.stacked.currentIndex() == self.ws_page_index:
                    self._switch_tab(0)
        else:
            if hasattr(self, "sls_tile") and self.sls_tile:
                self.sls_tile.setEnabled(True)
                self.sls_tile.setToolTip("")
            if hasattr(self, "sls_input") and self.sls_input:
                self.sls_input.setEnabled(True)
            self._update_eos_btn_state()
            # Workshop tab: show in real-time if game has workshop
            if hasattr(self, "ws_tab_btn") and self.ws_tab_btn:
                from utils.workshop_helpers import check_game_has_workshop
                if check_game_has_workshop(self.appid, self.game_data):
                    self.ws_tab_btn.setVisible(True)

    def _update_eos_btn_state(self):
        from utils.dlc_helpers import is_dlc_only_mode
        if is_dlc_only_mode(self.appid):
            self.eos_tile.setEnabled(False)
            self.eos_tile.setToolTip("Not available in DLC-Only mode")
            return

        # Phase 1: File Detection & Hash-based State Resolution
        install_path = self.game_data.get("install_path")
        if not install_path or not os.path.exists(install_path):
            self.eos_tile.setVisible(False)
            return

        from utils.eos_detector import EOSDetector
        status = EOSDetector.get_proxy_status(install_path)

        if status == "none":
            self.eos_tile.setVisible(False)
            return

        # Phase 2: SLSonline Dependency Check & Status Handling
        self.eos_tile.setVisible(True)
        is_sls_active = self.sls_tile.isChecked() if hasattr(self, "sls_tile") else False

        if status == "active":
            # Proxy is currently applied and hash matches -> Active state
            self.eos_tile.setEnabled(True)  # Allow removing proxy regardless of SLS state
            self.eos_tile.update_state(True, self.accent_color, active_sub="Remove Proxy", inactive_sub="Enable Proxy")
            self.eos_tile.setToolTip("Epic Online Services proxy is active. Click to remove proxy and restore original DLL.")
        elif status == "stale":
            # Game was updated: .yes exists, but .dll was replaced with unpatched version -> Warning state
            self.eos_tile.setEnabled(True)
            self.eos_tile.update_state(True, self.accent_color, active_sub="Reapply Proxy", inactive_sub="Reapply Proxy", custom_color="#F59E0B")
            self.eos_tile.setToolTip("Game was updated with unpatched EOS binaries. Click to reapply Epic Online Services proxy.")
        else:
            # Proxy is not applied (original DLL present)
            if not is_sls_active:
                self.eos_tile.setEnabled(False)
                self.eos_tile.update_state(False, self.accent_color, active_sub="Remove Proxy", inactive_sub="Enable SLSonline")
                self.eos_tile.setToolTip("Activate SLSonline first to enable EOS Proxy.")
            else:
                self.eos_tile.setEnabled(True)
                self.eos_tile.update_state(False, self.accent_color, active_sub="Remove Proxy", inactive_sub="Enable Proxy")
                self.eos_tile.setToolTip("Apply Epic Online Services proxy DLL.")

    def _on_eos_btn_clicked(self):
        install_path = self.game_data.get("install_path")
        if not install_path or not os.path.exists(install_path):
            return

        from utils.eos_detector import EOSDetector
        status = EOSDetector.get_proxy_status(install_path)
        if status == "none":
            return

        try:
            if status == "active":
                # Disable/Remove EOS Proxy
                success = EOSDetector.remove_proxy(install_path)
                if success:
                    QMessageBox.information(self, "EOS Proxy", "Epic Online Services proxy removed successfully.")
                else:
                    QMessageBox.warning(self, "EOS Proxy", "Failed to remove Epic Online Services proxy.")
            elif status == "stale":
                # Reapply EOS Proxy after game update
                success = EOSDetector.apply_proxy(install_path)
                if success:
                    QMessageBox.information(self, "EOS Proxy", "Epic Online Services proxy reapplied successfully.")
                else:
                    QMessageBox.warning(self, "EOS Proxy", "Failed to reapply Epic Online Services proxy.")
            else:
                # Enable/Apply EOS Proxy
                success = EOSDetector.apply_proxy(install_path)
                if success:
                    QMessageBox.information(self, "EOS Proxy", "Epic Online Services proxy enabled successfully.")
                else:
                    from utils.paths import Paths
                    proxy_src = Paths.deps("EOSSDK-Win64-Shipping.dll")
                    if not proxy_src.exists():
                        QMessageBox.critical(self, "Error", "Bundled proxy file EOSSDK-Win64-Shipping.dll not found in deps folder.")
                    else:
                        QMessageBox.warning(self, "EOS Proxy", "No target EOSSDK-Win64-Shipping.dll found to replace.")
        except Exception as e:
            logger.error(f"Failed to toggle EOS Proxy: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to toggle EOS Proxy:\n{e}")

        self._update_eos_btn_state()

    def _init_slsonline_logic(self):
        # Connect action
        self.eos_tile.clicked.connect(self._on_eos_btn_clicked)

        if is_slssteam_config_management_enabled() and self.appid not in ("0", "N/A", "unknown", "480"):
            config = get_user_config_path()
            if config.exists():
                existing = get_fake_appid(config, self.appid)
                
                self.sls_tile.blockSignals(True)
                def _apply_split_style(checked):
                    self.sls_tile.update_state(checked, self.accent_color)
                    if checked:
                        from utils.color_utils import get_best_foreground_color
                        text_color = get_best_foreground_color(self.accent_color, dark_color="#121214", light_color="#FFFFFF")
                        self.sls_tile.setStyleSheet(f"""
                            QPushButton {{
                                background-color: {self.accent_color};
                                border: none;
                                border-top-left-radius: 8px;
                                border-bottom-left-radius: 8px;
                                border-top-right-radius: 0px;
                                border-bottom-right-radius: 0px;
                            }}
                        """)
                        self.sls_tile.title_lbl.setStyleSheet(f"font-weight: bold; font-size: 8.5pt; color: {text_color}; background: transparent;")
                        self.sls_tile.sub_lbl.setStyleSheet(f"font-size: 7.5pt; font-style: italic; color: {text_color}; opacity: 0.85; background: transparent;")

                if existing:
                    self.sls_tile.setChecked(True)
                    _apply_split_style(True)
                    self.sls_input.setText(existing)
                    self.sls_input_container.setVisible(True)
                else:
                    self.sls_tile.setChecked(False)
                    _apply_split_style(False)
                    self.sls_input.setText("480")
                    self.sls_input_container.setVisible(False)
                self.sls_tile.blockSignals(False)

                def _tog(checked):
                    _apply_split_style(checked)
                    self.sls_input_container.setVisible(checked)
                    self._update_eos_btn_state()
                    fid = self.sls_input.text().strip() or "480"
                    name = self.game_data.get("game_name", "Unknown")
                    if checked:
                        cur = get_fake_appid(config, self.appid)
                        if cur:
                            remove_fake_app_id(config, self.appid, cur)
                        add_fake_app_id(config, self.appid, name, fid)
                    else:
                        cur = get_fake_appid(config, self.appid)
                        if cur:
                            remove_fake_app_id(config, self.appid, cur)

                def _fin():
                    if self.sls_tile.isChecked():
                        fid = self.sls_input.text().strip() or "480"
                        name = self.game_data.get("game_name", "Unknown")
                        cur = get_fake_appid(config, self.appid)
                        if cur != fid:
                            if cur:
                                remove_fake_app_id(config, self.appid, cur)
                            add_fake_app_id(config, self.appid, name, fid)

                self.sls_tile.clicked.connect(_tog)
                self.sls_input.editingFinished.connect(_fin)
        else:
            self.sls_tile.setEnabled(False)
            self.sls_tile.update_state(False, self.accent_color)
            self.sls_input.setEnabled(False)

        # Update EOS Proxy tile state AFTER sls_tile state has been restored
        self._update_eos_btn_state()

        # Check DLC-only mode for SLS and EOS Proxy
        from utils.dlc_helpers import is_dlc_only_mode
        if is_dlc_only_mode(self.appid):
            self.sls_tile.setEnabled(False)
            self.sls_tile.setToolTip("Not available in DLC-Only mode")
            self.sls_input.setEnabled(False)
            self.eos_tile.setEnabled(False)
            self.eos_tile.setToolTip("Not available in DLC-Only mode")

    # ──────────────────────────────────────────
    def _on_status_btn_clicked(self):
        if self.parent_window and hasattr(self.parent_window, "game_manager") and self.parent_window.game_manager:
            self.status_tile.setEnabled(False)
            self._update_status_ui("checking")
            self._load_branches_async(force_refresh=True)
            self.parent_window.game_manager.check_single_game_update(self.appid)

    def _update_status_ui(self, status):
        ac = self.accent_color
        last_chk = self._get_last_checked()
        sub = last_chk if last_chk != "Never" else "Click to check"

        from utils.color_utils import get_semantic_colors
        sem_colors = get_semantic_colors(ac)

        if status == "update_available":
            hubcap_needs_update = self.game_data.get("hubcap_needs_update", False)
            hubcap_update_in_progress = self.game_data.get("hubcap_update_in_progress", False)
            
            if hubcap_needs_update or hubcap_update_in_progress:
                reason = "Hubcap updating" if hubcap_update_in_progress else "Hubcap not ready"
                title = f"UPDATE ({reason})"
            else:
                title = "UPDATE"
            
            self.status_tile.title_lbl.setText(title)
            self.status_tile.sub_lbl.setText(sub)
            self.status_tile.update_state(True, sem_colors["warning"], active_sub=sub)
            self.status_tile.setEnabled(True)
            
        elif status == "up_to_date":
            title = "UP TO DATE"
            self.status_tile.title_lbl.setText(title)
            self.status_tile.sub_lbl.setText(sub)
            self.status_tile.update_state(True, sem_colors["success"], active_sub=sub)
            self.status_tile.setEnabled(True)
            
        elif status == "checking":
            title = "CHECKING..."
            sub = "Checking Steam API..."
            self.status_tile.title_lbl.setText(title)
            self.status_tile.sub_lbl.setText(sub)
            self.status_tile.update_state(True, sem_colors["info"], active_sub=sub)
            self.status_tile.setEnabled(False)
            
        else:
            title = "STATUS UNKNOWN"
            sub = "Click to check"
            self.status_tile.title_lbl.setText(title)
            self.status_tile.sub_lbl.setText(sub)
            self.status_tile.update_state(False, ac, inactive_sub=sub)
            self.status_tile.setEnabled(True)

        self._update_validate_button()

    def _on_status_changed(self, changed_appid, new_status):
        if changed_appid != self.appid:
            return
        self.game_data["update_status"] = new_status
        self._update_status_ui(new_status)

    def _on_hubcap_status_changed(self, changed_appid, needs_update, update_in_progress):
        if changed_appid != self.appid:
            return
        self.game_data["hubcap_needs_update"] = needs_update
        self.game_data["hubcap_update_in_progress"] = update_in_progress
        self._update_status_ui(self.game_data.get("update_status"))

    # ──────────────────────────────────────────
    #  TAB 1 — Builds (SteamDB Build History & Rollback)
    # ──────────────────────────────────────────
    def _init_builds_tab(self):
        builds_page = QWidget()
        builds_page.setStyleSheet("background: transparent;")
        page_layout = QVBoxLayout(builds_page)
        page_layout.setContentsMargins(14, 10, 14, 10)
        page_layout.setSpacing(8)

        # ── Central Stack: Page 0 = Initial Spinner, Page 1 = Card list ──
        self.builds_center_stack = QStackedWidget()

        # Page 0: First-time loading spinner (shown before any cache exists)
        loading_container = QWidget()
        loading_layout = QVBoxLayout(loading_container)
        loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.setSpacing(10)
        self.builds_main_spinner = MaterialSpinner(loading_container, size=32, color=self.accent_color, thickness=3)
        loading_lbl = QLabel("Fetching version history from SteamDB...")
        loading_lbl.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 8.5pt;")
        loading_layout.addWidget(self.builds_main_spinner, 0, Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(loading_lbl, 0, Qt.AlignmentFlag.AlignCenter)
        self.builds_center_stack.addWidget(loading_container)

        # Page 1: Scrollable card list
        self.builds_scroll = QScrollArea()
        self.builds_scroll.setWidgetResizable(True)
        self.builds_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.builds_scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: transparent; width: 6px; margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.14); border-radius: 3px; min-height: 24px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.25);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)
        self.builds_scroll_inner = QWidget()
        self.builds_scroll_inner.setStyleSheet("background: transparent;")
        self.builds_cards_layout = QVBoxLayout(self.builds_scroll_inner)
        self.builds_cards_layout.setContentsMargins(0, 4, 4, 4)
        self.builds_cards_layout.setSpacing(8)
        self.builds_cards_layout.addStretch()
        self.builds_scroll.setWidget(self.builds_scroll_inner)
        self.builds_center_stack.addWidget(self.builds_scroll)

        # Page 2: SteamDB Unavailable error state
        self.builds_error_container = QWidget()
        err_layout = QVBoxLayout(self.builds_error_container)
        err_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        err_layout.setSpacing(10)

        err_title = QLabel("SteamDB Solver Required")
        err_title.setStyleSheet("color: #FFFFFF; font-size: 11pt; font-weight: bold; border: none; background: transparent;")
        err_desc = QLabel(
            "SteamDB is protected by Cloudflare and requires the local Byparr solver.\n"
            "Once setup, ACCELA will automatically start and stop Byparr as needed."
        )
        err_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        err_desc.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 8.5pt; border: none; background: transparent;")

        btns_row = QHBoxLayout()
        btns_row.setSpacing(10)
        btns_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        copy_btn = QPushButton("Copy Setup Command")
        copy_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                color: #FFFFFF;
                font-size: 8.5pt;
                font-weight: 600;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.14);
            }
        """)
        def _on_copy_setup_cmd():
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtCore import QTimer
            cb = QApplication.clipboard()
            if cb:
                cb.setText("bash <(curl -sSL https://raw.githubusercontent.com/niwia/ASSella/beta/scripts/setup_byparr.sh)")
            copy_btn.setText("✓ Copied!")
            QTimer.singleShot(2000, lambda: copy_btn.setText("Copy Setup Command"))
        copy_btn.clicked.connect(_on_copy_setup_cmd)

        retry_btn = QPushButton("Retry")
        retry_btn.setFixedWidth(90)
        retry_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                color: #FFFFFF;
                font-size: 8.5pt;
                font-weight: 600;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.14);
            }
        """)
        retry_btn.clicked.connect(self._fetch_steamdb_builds_async)

        btns_row.addWidget(copy_btn)
        btns_row.addWidget(retry_btn)

        err_layout.addWidget(err_title, 0, Qt.AlignmentFlag.AlignCenter)
        err_layout.addWidget(err_desc, 0, Qt.AlignmentFlag.AlignCenter)
        err_layout.addLayout(btns_row)
        self.builds_center_stack.addWidget(self.builds_error_container)

        page_layout.addWidget(self.builds_center_stack, 1)

        # ── Bottom 3-button bar (equal width) ──
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 4, 0, 0)
        bottom_row.setSpacing(8)

        self.builds_refresh_btn = QPushButton("⟳  Refresh")
        self.builds_manual_btn = QPushButton("Manual")
        self.builds_download_btn = QPushButton("Download Manifest")
        self.builds_download_btn.setEnabled(False)

        ghost_style = """
            QPushButton {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
                color: #FFFFFF;
                font-weight: 600;
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: #FFFFFF;
            }
            QPushButton:disabled {
                background-color: rgba(255, 255, 255, 0.02);
                color: rgba(255, 255, 255, 0.2);
                border: 1px solid rgba(255, 255, 255, 0.05);
            }
        """
        from utils.color_utils import get_best_foreground_color
        dl_fg = get_best_foreground_color(self.accent_color)
        download_style = f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: {dl_fg};
                border: none;
                border-radius: 6px;
                font-weight: 600;
                font-size: 9pt;
            }}
            QPushButton:hover:!disabled {{
                background-color: #FFFFFF;
                color: #000000;
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.05);
                color: rgba(255, 255, 255, 0.25);
                border: 1px solid rgba(255, 255, 255, 0.08);
            }}
        """
        for btn in (self.builds_refresh_btn, self.builds_manual_btn):
            btn.setFixedHeight(36)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(ghost_style)

        self.builds_download_btn.setFixedHeight(36)
        self.builds_download_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.builds_download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.builds_download_btn.setStyleSheet(download_style)

        self.builds_refresh_btn.clicked.connect(self._fetch_steamdb_builds_async)
        self.builds_manual_btn.clicked.connect(self._on_manual_rollback_clicked)
        self.builds_download_btn.clicked.connect(self._on_builds_download_clicked)

        bottom_row.addWidget(self.builds_refresh_btn)
        bottom_row.addWidget(self.builds_manual_btn)
        bottom_row.addWidget(self.builds_download_btn)
        page_layout.addLayout(bottom_row)

        self.stacked.addWidget(builds_page)

        # Runtime state
        self._selected_build_idx = -1
        self._build_cards = []   # [(QFrame, build_data_dict), ...]
        self._cached_build_depots = {}

    # ── Card builder ───────────────────────────────────────────────────────
    def _strip_build_title(self, title: str, game_name: str) -> str:
        """Strip the game name prefix from a SteamDB patch title."""
        if not game_name or not title:
            return title
        for sep in (" - ", ": ", " – ", " — ", " / "):
            if title.lower().startswith(game_name.lower() + sep):
                return title[len(game_name) + len(sep):]
        return title

    def _make_build_card(self, idx: int, item: dict, current_bid: str, game_name: str) -> QFrame:
        build_id = str(item.get("buildid", ""))
        title = item.get("title", "Update")
        date_str = item.get("date", "")
        time_str = item.get("time", "")
        is_current = bool(build_id) and build_id == current_bid

        short_title = self._strip_build_title(title, game_name)

        card = QFrame()
        card.setObjectName("build_card")
        card.setCursor(Qt.CursorShape.PointingHandCursor)

        from utils.color_utils import get_dark_container_color
        tinted_bg = get_dark_container_color(self.accent_color)

        def _normal_style():
            if is_current:
                return f"""
                    QFrame#build_card {{
                        background-color: rgba(255, 255, 255, 0.035);
                        border: 1px solid rgba(255, 255, 255, 0.12);
                        border-radius: 8px;
                    }}
                    QFrame#build_card:hover {{
                        background-color: rgba(255, 255, 255, 0.065);
                        border: 1px solid rgba(255, 255, 255, 0.22);
                    }}
                    QFrame#build_card QLabel {{
                        border: none;
                        background: transparent;
                    }}
                """
            return f"""
                QFrame#build_card {{
                    background-color: rgba(255, 255, 255, 0.025);
                    border: 1px solid rgba(255, 255, 255, 0.07);
                    border-radius: 8px;
                }}
                QFrame#build_card:hover {{
                    background-color: rgba(255, 255, 255, 0.055);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                }}
                QFrame#build_card QLabel {{
                    border: none;
                    background: transparent;
                }}
            """

        def _selected_style():
            return f"""
                QFrame#build_card {{
                    background-color: rgba(255, 255, 255, 0.06);
                    border: 1.5px solid {self.accent_color};
                    border-radius: 8px;
                }}
                QFrame#build_card QLabel {{
                    border: none;
                    background: transparent;
                }}
            """

        card._normal_style = _normal_style
        card._selected_style = _selected_style
        card.setStyleSheet(_normal_style())

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 11, 16, 11)
        layout.setSpacing(6)

        # ─ Row 1: Short title (left) + Installed Badge (right) ─
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(8)

        title_lbl = QLabel(short_title)
        title_lbl.setStyleSheet("color: #FFFFFF; font-size: 10pt; font-weight: bold; border: none; background: transparent;")
        title_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        row1.addWidget(title_lbl, 1)

        if is_current:
            badge = QLabel("Installed")
            badge.setStyleSheet(f"""
                color: #FFFFFF;
                background-color: {tinted_bg};
                border: 1px solid {self.accent_color};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 8pt;
                font-weight: 600;
            """)
            badge.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            row1.addWidget(badge, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout.addLayout(row1)

        # ─ Row 2: Build ID <id>    <date>    <time> ─
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(16)
        row2.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        bid_html = (
            f'<span style="color: rgba(255, 255, 255, 0.5); font-size: 9pt;">Build ID</span>'
            f'&nbsp;&nbsp;'
            f'<span style="color: {self.accent_color}; font-size: 9pt; font-weight: bold;">{build_id}</span>'
        )
        bid_lbl = QLabel(bid_html)
        bid_lbl.setStyleSheet("border: none; background: transparent;")
        bid_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        row2.addWidget(bid_lbl)

        if date_str:
            date_lbl = QLabel(date_str)
            date_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 8.5pt; border: none; background: transparent;")
            date_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            row2.addWidget(date_lbl)

        if time_str:
            time_lbl = QLabel(time_str)
            time_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 8.5pt; border: none; background: transparent;")
            time_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            row2.addWidget(time_lbl)

        row2.addStretch(1)
        layout.addLayout(row2)

        # Click handler
        card.mousePressEvent = lambda _e, i=idx: self._on_build_card_clicked(i)

        return card

    # ── Async fetch ────────────────────────────────────────────────────────
    def _fetch_steamdb_builds_async(self):
        self.builds_refresh_btn.setText("⟳  Checking...")
        self.builds_refresh_btn.setEnabled(False)

        # If no cached cards exist yet, show spinner page while fetching
        if self.builds_cards_layout.count() <= 1:
            self.builds_center_stack.setCurrentIndex(0)

        def _worker():
            try:
                aid = int(self.appid) if self.appid.isdigit() else 0
                data = self.steamdb_scraper.get_patchnotes(aid, limit=20)
                self.builds_loaded.emit(data)
            except Exception as e:
                logger.error(f"Failed to fetch SteamDB builds for {self.appid}: {e}")
                self.builds_error.emit(str(e))

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    def _on_builds_loaded(self, builds: list):
        self.builds_refresh_btn.setText("⟳  Refresh")
        self.builds_refresh_btn.setEnabled(True)
        self.builds_refresh_btn.setToolTip("")
        if builds:
            aid = int(self.appid) if self.appid.isdigit() else 0
            self.builds_cache.save_builds(aid, builds)
            self._populate_builds_cards(builds)
            self.builds_center_stack.setCurrentIndex(1)
        elif self.builds_cards_layout.count() > 1:
            self.builds_center_stack.setCurrentIndex(1)
        else:
            self.builds_center_stack.setCurrentIndex(2)

    def _on_builds_error(self, err_msg: str):
        self.builds_refresh_btn.setText("⟳  Refresh")
        self.builds_refresh_btn.setEnabled(True)
        # If we have cached build cards already displayed, keep showing them!
        if self.builds_cards_layout.count() > 1:
            self.builds_center_stack.setCurrentIndex(1)
            self.builds_refresh_btn.setToolTip(f"SteamDB is currently unavailable. Showing cached builds.\n(Error: {err_msg})")
        else:
            # No cache exists - show clean unavailable page (Page 2)
            self.builds_center_stack.setCurrentIndex(2)
            self.builds_refresh_btn.setToolTip(f"SteamDB unavailable: {err_msg}")

    def _get_build_action_label(self, build_id: str) -> str:
        current_bid = self._get_installed_buildid()
        if current_bid.isdigit() and str(build_id).isdigit():
            c_int = int(current_bid)
            s_int = int(build_id)
            if s_int < c_int:
                return "Downgrade"
            elif s_int == c_int:
                return "Verify"
            else:
                return "Update"
        return "Download Manifest"

    # ── Card list population ───────────────────────────────────────────────
    def _populate_builds_cards(self, data: list):
        # Clear existing cards (keep trailing stretch at index 0 = last item)
        while self.builds_cards_layout.count() > 1:
            item = self.builds_cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._build_cards = []
        self._selected_build_idx = -1
        self.builds_download_btn.setEnabled(False)
        self.builds_download_btn.setText("Download Manifest")

        current_bid = self._get_installed_buildid()
        game_name = self.game_data.get("game_name", "")

        for idx, item in enumerate(data):
            card = self._make_build_card(idx, item, current_bid, game_name)
            self.builds_cards_layout.insertWidget(idx, card)
            self._build_cards.append((card, item))

    # ── Card selection ─────────────────────────────────────────────────────
    def _on_build_card_clicked(self, idx: int):
        current_bid = self._get_installed_buildid()

        # Restore previous card to normal style
        if 0 <= self._selected_build_idx < len(self._build_cards):
            prev_card, _ = self._build_cards[self._selected_build_idx]
            prev_card.setStyleSheet(prev_card._normal_style())

        self._selected_build_idx = idx
        card, item = self._build_cards[idx]
        card.setStyleSheet(card._selected_style())

        build_id = str(item.get("buildid", ""))
        self.builds_download_btn.setEnabled(False)
        action = self._get_build_action_label(build_id)
        self.builds_download_btn.setText(f"{action}...")

        if not build_id:
            return

        # Use in-memory depot cache first
        if build_id in self._cached_build_depots:
            self._apply_depot_to_download_btn(build_id, self._cached_build_depots[build_id])
            return
        if item.get("depots"):
            self._apply_depot_to_download_btn(build_id, item["depots"])
            return

        # Async depot resolve
        self.builds_download_btn.setText("Fetching Manifest...")

        def _depot_worker():
            try:
                depots = self.steamdb_scraper.get_patch_depots(build_id)
                self.build_depots_loaded.emit(build_id, depots)
            except Exception as e:
                logger.error(f"Failed to fetch depots for build {build_id}: {e}")
                self.build_depots_error.emit(f"Failed to resolve manifests for Build {build_id}.")

        import threading
        threading.Thread(target=_depot_worker, daemon=True).start()

    def _on_build_depots_loaded(self, build_id: str, depots: dict):
        self._cached_build_depots[build_id] = depots
        aid = int(self.appid) if self.appid.isdigit() else 0
        self.builds_cache.update_build_depots(aid, build_id, depots)

        # Only update button if this build is still selected
        if 0 <= self._selected_build_idx < len(self._build_cards):
            _, item = self._build_cards[self._selected_build_idx]
            if str(item.get("buildid")) == str(build_id):
                self._apply_depot_to_download_btn(build_id, depots)

    def _on_build_depots_error(self, err_msg: str):
        if 0 <= self._selected_build_idx < len(self._build_cards):
            self.builds_download_btn.setText("Manifest Error")
            self.builds_download_btn.setEnabled(False)
            self.builds_download_btn.setToolTip(err_msg)

    def _apply_depot_to_download_btn(self, build_id: str, depots: dict):
        has_manifest = any(info.get("manifest_id") for info in depots.values()) if depots else False
        action = self._get_build_action_label(build_id)
        if has_manifest:
            self.builds_download_btn.setText(f"{action} (Build {build_id})")
            self.builds_download_btn.setEnabled(True)
        else:
            self.builds_download_btn.setText("No Manifests Found")
            self.builds_download_btn.setEnabled(False)

    # ── Download action ────────────────────────────────────────────────────
    def _on_builds_download_clicked(self):
        if not (0 <= self._selected_build_idx < len(self._build_cards)):
            return
        _, item = self._build_cards[self._selected_build_idx]
        build_id = str(item.get("buildid", ""))

        depots = self._cached_build_depots.get(build_id) or item.get("depots", {})
        if not depots:
            QMessageBox.warning(self, "No Manifest", "No depot manifests found for this build.")
            return

        installed_depots = self.game_data.get("installed_depots", {})
        selected_depot_id = None

        for d_id in depots.keys():
            if d_id in installed_depots:
                selected_depot_id = d_id
                break

        if not selected_depot_id and len(depots) == 1:
            selected_depot_id = list(depots.keys())[0]

        if not selected_depot_id and len(depots) > 1:
            items = [f"Depot {d_id}  (Manifest: {info.get('manifest_id')})"
                     for d_id, info in depots.items() if info.get("manifest_id")]
            if items:
                chosen, ok = QInputDialog.getItem(
                    self, "Select Depot",
                    "Multiple depots found. Select depot to download:", items, 0, False)
                if not ok or not chosen:
                    return
                selected_depot_id = chosen.split(" ")[1]

        if not selected_depot_id:
            selected_depot_id = list(depots.keys())[0]

        manifest_id = depots[selected_depot_id].get("manifest_id")
        if not manifest_id:
            QMessageBox.warning(self, "No Manifest ID",
                                f"Could not find manifest ID for depot {selected_depot_id}.")
            return

        action = self._get_build_action_label(build_id)
        if action == "Downgrade":
            should_pin = True
        elif action == "Verify":
            should_pin = self.settings.value(f"pin_build/{self.appid}", False, type=bool) if self.settings else False
        else:
            should_pin = False

        self._trigger_rollback_job(str(selected_depot_id), str(build_id), str(manifest_id), pin_build=should_pin)

    # ──────────────────────────────────────────
    #  TAB 2 — Tools (Clean Two-Column Grid Setup)
    # ──────────────────────────────────────────
    def _init_tools_tab(self):
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        path = self.game_data.get("install_path")
        name = self.game_data.get("game_name")
        ac = self.accent_color

        from utils.color_utils import get_best_foreground_color

        grid_widget = QWidget()
        grid = QVBoxLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)

        # Section 1: DRM & Emulation
        grid.addWidget(self._section_title("DRM & Emulation"))

        # Row 1: Steamless (Python) | Steamless (.NET CLI)
        self.b_steamless_aio = QPushButton("Steamless (Python)")
        self.b_steamless_aio.setToolTip("Remove Steam DRM using Python Steamless (AIO)")
        self.b_steamless_aio.setFixedHeight(36)
        self.b_steamless_aio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_steamless_aio.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                color: #FFFFFF;
                font-size: 9pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.12);
                border-color: {ac};
                color: {ac};
            }}
            QPushButton:pressed {{ background: rgba(255,255,255,0.18); }}
            QPushButton:disabled {{
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.25);
            }}
        """)
        self.b_steamless_aio.clicked.connect(
            lambda: self.parent_window.main_window.task_manager.run_steamless_aio_for_game(path, name))

        self.b_steamless_cli = QPushButton("Steamless (.NET CLI)")
        self.b_steamless_cli.setToolTip("Remove Steam DRM using .NET 9 Steamless CLI")
        self.b_steamless_cli.setFixedHeight(36)
        self.b_steamless_cli.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_steamless_cli.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                color: #FFFFFF;
                font-size: 9pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.12);
                border-color: {ac};
                color: {ac};
            }}
            QPushButton:pressed {{ background: rgba(255,255,255,0.18); }}
            QPushButton:disabled {{
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.25);
            }}
        """)
        self.b_steamless_cli.clicked.connect(
            lambda: self.parent_window.main_window.task_manager.run_steamless_for_game(path, name))
        self.b_steamless = self.b_steamless_aio

        self.sl_row_widget = QWidget()
        sl_row = QHBoxLayout(self.sl_row_widget)
        sl_row.setContentsMargins(0, 0, 0, 0)
        sl_row.setSpacing(8)
        sl_row.addWidget(self.b_steamless_aio, 1)
        sl_row.addWidget(self.b_steamless_cli, 1)
        grid.addWidget(self.sl_row_widget)

        # Row 2: Apply Goldberg | Remove Goldberg
        self.gb_apply_btn = QPushButton("Apply Goldberg")
        self.gb_apply_btn.setToolTip("Apply Goldberg Steam emulator to this game")
        self.gb_apply_btn.setFixedHeight(36)
        self.gb_apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gb_apply_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                color: #FFFFFF;
                font-size: 9pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.12);
                border-color: {ac};
                color: {ac};
            }}
            QPushButton:pressed {{ background: rgba(255,255,255,0.18); }}
            QPushButton:disabled {{
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.25);
            }}
        """)

        self.gb_remove_btn = QPushButton("Remove Goldberg")
        self.gb_remove_btn.setToolTip("Remove Goldberg Steam emulator from this game")
        self.gb_remove_btn.setFixedHeight(36)
        self.gb_remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gb_remove_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.07);
                border-radius: 8px;
                color: rgba(255,255,255,0.3);
                font-size: 9pt;
                font-weight: 600;
            }
            QPushButton:enabled {
                background: rgba(160,30,20,0.15);
                border-color: rgba(255,100,80,0.4);
                color: #ff8a7a;
            }
            QPushButton:enabled:hover {
                background: rgba(160,30,20,0.25);
                border-color: #ff8a7a;
            }
            QPushButton:disabled {
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.06);
                color: rgba(255,255,255,0.25);
            }
        """)
        self.gb_remove_btn.setEnabled(False)

        if self.parent_window and hasattr(self.parent_window, "goldberg_check_complete") and self.parent_window.goldberg_check_complete:
            self.parent_window.goldberg_check_complete.connect(self._on_goldberg_check_complete)
            self.finished.connect(
                lambda: self.parent_window.goldberg_check_complete.disconnect(
                    self._on_goldberg_check_complete)
                if hasattr(self.parent_window, "goldberg_check_complete") and self.parent_window.goldberg_check_complete else None)
        if self.parent_window and hasattr(self.parent_window, "executor") and self.parent_window.executor:
            self.parent_window.executor.submit(self.parent_window._check_goldberg_async, path)

        def _apply_gb():
            if self.parent_window.main_window and self.parent_window.main_window.task_manager:
                self.parent_window.main_window.task_manager.apply_goldberg_to_game(
                    path, self.appid, name, show_dialog=True)
                self.parent_window.executor.submit(self.parent_window._check_goldberg_async, path)

        def _remove_gb():
            if self.parent_window.main_window and self.parent_window.main_window.task_manager:
                self.parent_window.main_window.task_manager.remove_goldberg_from_game(
                    path, self.appid, name, show_dialog=True)
                self.parent_window.executor.submit(self.parent_window._check_goldberg_async, path)

        self.gb_apply_btn.clicked.connect(_apply_gb)
        self.gb_remove_btn.clicked.connect(_remove_gb)

        gb_row_widget = QWidget()
        gb_row = QHBoxLayout(gb_row_widget)
        gb_row.setContentsMargins(0, 0, 0, 0)
        gb_row.setSpacing(8)
        gb_row.addWidget(self.gb_apply_btn, 1)
        gb_row.addWidget(self.gb_remove_btn, 1)
        grid.addWidget(gb_row_widget)

        self._refresh_drm_emulation_state()

        grid.addWidget(self._thin_line())

        # Section 2: Depots & Installation (Single Row Layout)
        grid.addWidget(self._section_title("Depots & Installation"))

        depots_row_widget = QWidget()
        depots_row = QHBoxLayout(depots_row_widget)
        depots_row.setContentsMargins(0, 0, 0, 0)
        depots_row.setSpacing(8)

        # Grouped Choose + Reset (Material 3 Split Pill)
        choose_reset_group = QFrame()
        choose_reset_group.setFixedHeight(36)
        choose_reset_group.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
            }
        """)
        cr_layout = QHBoxLayout(choose_reset_group)
        cr_layout.setContentsMargins(0, 0, 0, 0)
        cr_layout.setSpacing(0)

        self.choose_depots_btn = QPushButton("Choose...")
        self.choose_depots_btn.setFixedHeight(34)
        self.choose_depots_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.choose_depots_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {ac};
                font-weight: bold;
                font-size: 8.5pt;
                padding: 0 10px;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.08);
            }}
        """)
        self.choose_depots_btn.clicked.connect(self._configure_depots_wrapper)

        cr_divider = QFrame()
        cr_divider.setFixedWidth(1)
        cr_divider.setStyleSheet("background: rgba(255, 255, 255, 0.12);")

        reset_btn = QPushButton("Reset")
        reset_btn.setFixedHeight(34)
        reset_btn.setFixedWidth(60)
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #e05a47;
                font-weight: bold;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background: rgba(224, 90, 71, 0.12);
            }
        """)
        reset_btn.clicked.connect(self._reset_depots_wrapper)

        cr_layout.addWidget(self.choose_depots_btn, 1)
        cr_layout.addWidget(cr_divider)
        cr_layout.addWidget(reset_btn)

        depots_row.addWidget(choose_reset_group, 1)

        # Fix Installation button adjacent on the same row
        self.fix_btn = QPushButton("Fix Installation")
        self.fix_btn.setFixedHeight(36)
        self.fix_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fix_btn.setToolTip("Removes local manifest (.acf) to force Steam verification.")
        self.fix_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                color: #FFFFFF;
                font-weight: bold;
                font-size: 8.5pt;
                padding: 0 12px;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.10);
                border-color: {ac};
                color: {ac};
            }}
            QPushButton:disabled {{
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.05);
                color: rgba(255, 255, 255, 0.25);
            }}
        """)
        self.fix_btn.clicked.connect(lambda: self.parent_window._fix_game_install(self.game_data))
        depots_row.addWidget(self.fix_btn, 1)

        grid.addWidget(depots_row_widget)

        self._update_depot_label()

        grid.addWidget(self._thin_line())

        # Section 3: Utility & Store Links (All 4 in a Single Horizontal Row)
        grid.addWidget(self._section_title("Utility & Store Links"))

        links_row_widget = QWidget()
        links_row = QHBoxLayout(links_row_widget)
        links_row.setContentsMargins(0, 0, 0, 0)
        links_row.setSpacing(6)

        is_real_app = self.appid not in ("0", "N/A", "unknown")

        steam_btn = QPushButton("Open Store")
        steam_btn.setFixedHeight(34)
        steam_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        steam_btn.setEnabled(is_real_app)
        steam_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 8px;
                color: #FFFFFF;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.10);
                border-color: {ac};
                color: {ac};
            }}
            QPushButton:disabled {{
                color: rgba(255,255,255,0.3);
                border-color: rgba(255,255,255,0.05);
            }}
        """)
        steam_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(f"https://store.steampowered.com/app/{self.appid}/")))
        links_row.addWidget(steam_btn, 1)

        steamdb_btn = QPushButton("Open SteamDB")
        steamdb_btn.setFixedHeight(34)
        steamdb_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        steamdb_btn.setEnabled(is_real_app)
        steamdb_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 8px;
                color: #FFFFFF;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.10);
                border-color: {ac};
                color: {ac};
            }}
            QPushButton:disabled {{
                color: rgba(255,255,255,0.3);
                border-color: rgba(255,255,255,0.05);
            }}
        """)
        steamdb_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(f"https://www.steamdb.info/app/{self.appid}/")))
        links_row.addWidget(steamdb_btn, 1)

        copy_appid = QPushButton("Copy App ID")
        copy_appid.setFixedHeight(34)
        copy_appid.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_appid.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 8px;
                color: #FFFFFF;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.10);
                border-color: {ac};
                color: {ac};
            }}
        """)
        copy_appid.clicked.connect(lambda: QApplication.clipboard().setText(self.appid))
        links_row.addWidget(copy_appid, 1)

        copy_path = QPushButton("Copy Path")
        copy_path.setFixedHeight(34)
        copy_path.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_path.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 8px;
                color: #FFFFFF;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.10);
                border-color: {ac};
                color: {ac};
            }}
        """)
        copy_path.clicked.connect(lambda: QApplication.clipboard().setText(
            str(self.game_data.get("install_path", ""))))
        links_row.addWidget(copy_path, 1)

        grid.addWidget(links_row_widget)

        grid.addWidget(self._thin_line())

        # Section 4: Experimental / Advanced DLC Tools
        grid.addWidget(self._section_title("Experimental DLC Tools"))

        self.dlcdata_exp_btn = QPushButton("Move DLC to DlcData (Advanced)")
        self.dlcdata_exp_btn.setFixedHeight(34)
        self.dlcdata_exp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_dlcdata_btn_text()
        self.dlcdata_exp_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 140, 0, 0.08);
                border: 1px solid rgba(255, 140, 0, 0.25);
                border-radius: 8px;
                color: #FFA726;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(255, 140, 0, 0.16);
                border-color: #FFA726;
                color: #FFB74D;
            }}
            QPushButton:disabled {{
                color: rgba(255, 255, 255, 0.25);
                border-color: rgba(255, 255, 255, 0.05);
                background: rgba(255, 255, 255, 0.02);
            }}
        """)
        self.dlcdata_exp_btn.clicked.connect(self._handle_move_dlc_to_dlcdata)
        grid.addWidget(self.dlcdata_exp_btn)

        lay.addWidget(grid_widget)
        lay.addStretch()

        self.stacked.addWidget(scroll)

    def _init_workshop_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        ws_widget = QWidget()
        self.ws_layout = QVBoxLayout(ws_widget)
        self.ws_layout.setContentsMargins(16, 14, 16, 14)
        self.ws_layout.setSpacing(10)

        # Header Title
        title_lbl = QLabel(f"Installed Workshop Mods ({self.game_data.get('game_name', 'Game')})")
        title_lbl.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {self.accent_color};")
        self.ws_layout.addWidget(title_lbl)

        # Loading placeholder
        self.ws_loading_lbl = QLabel("Scanning local workshop directories...")
        self.ws_loading_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 9.5pt;")
        self.ws_layout.addWidget(self.ws_loading_lbl)

        ws_widget.setLayout(self.ws_layout)
        scroll.setWidget(ws_widget)
        self.stacked.addWidget(scroll)

    def _scan_workshop_mods_async(self):
        def _thread_scan():
            ws_mods = []
            if self.appid and self.appid not in ("0", "N/A", "unknown"):
                try:
                    from core.steam_helpers import get_steam_libraries
                    from utils.workshop_helpers import fetch_workshop_details, strip_emojis
                    from pathlib import Path
                    from datetime import datetime

                    wids = []
                    local_mod_data = []

                    for lib in get_steam_libraries():
                        ws_dir = Path(lib) / "steamapps" / "workshop" / "content" / str(self.appid)
                        if ws_dir.exists():
                            for item_dir in ws_dir.iterdir():
                                try:
                                    if item_dir.is_dir() and item_dir.name.isdigit():
                                        wid = item_dir.name
                                        wids.append(wid)
                                        size = 0
                                        mtimes = []
                                        try:
                                            for f in item_dir.rglob('*'):
                                                try:
                                                    if f.is_file():
                                                        st = f.stat()
                                                        size += st.st_size
                                                        mtimes.append(st.st_mtime)
                                                except OSError:
                                                    pass
                                        except OSError:
                                            pass
                                        folder_mtime = item_dir.stat().st_mtime if item_dir.exists() else 0
                                        mtime = max(mtimes, default=folder_mtime)
                                        local_mod_data.append({
                                            "wid": wid,
                                            "path": str(item_dir),
                                            "size": size,
                                            "mtime": mtime,
                                        })
                                except OSError:
                                    pass

                    # Batch fetch real titles & updated timestamps from Steam API
                    api_details = fetch_workshop_details(wids) if wids else {}

                    for mod in local_mod_data:
                        wid = mod["wid"]
                        details = api_details.get(wid, {})
                        raw_title = details.get("title") or f"Workshop Item #{wid}"
                        time_updated = details.get("time_updated", 0)

                        # Update available if Steam updated time is newer than local folder mtime
                        update_available = (time_updated > 0 and time_updated > mod["mtime"] + 60)

                        dt = datetime.fromtimestamp(mod["mtime"]) if mod["mtime"] > 0 else datetime.now()
                        date_str = dt.strftime("%m/%d/%Y")

                        ws_mods.append({
                            "wid": wid,
                            "title": raw_title,
                            "path": mod["path"],
                            "size": mod["size"],
                            "mtime": mod["mtime"],
                            "date_str": date_str,
                            "time_updated": time_updated,
                            "update_available": update_available,
                        })

                except Exception as e:
                    logger.error(f"Error scanning workshop mods: {e}")

            from PyQt6.QtCore import QMetaObject, Q_ARG, Qt
            QMetaObject.invokeMethod(self, "_on_workshop_mods_scanned", Qt.ConnectionType.QueuedConnection, Q_ARG(list, ws_mods))

        import threading
        threading.Thread(target=_thread_scan, daemon=True).start()

    @pyqtSlot(list)
    def _on_workshop_mods_scanned(self, ws_mods):
        if not hasattr(self, "ws_layout") or self.ws_layout is None:
            return

        # Clear existing layout items safely
        while self.ws_layout.count() > 0:
            item = self.ws_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        from utils.workshop_helpers import strip_emojis
        from utils.color_utils import get_best_foreground_color
        btn_fg = get_best_foreground_color(self.accent_color)

        # Header Row
        header_widget = QWidget()
        header_lay = QHBoxLayout(header_widget)
        header_lay.setContentsMargins(0, 0, 0, 6)

        game_name = strip_emojis(self.game_data.get('game_name', 'Game'))
        title_lbl = QLabel(f"Installed Workshop Mods ({game_name})")
        title_lbl.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {self.accent_color};")
        header_lay.addWidget(title_lbl, 1)

        outdated_wids = [m["wid"] for m in ws_mods if m.get("update_available")]
        if outdated_wids:
            btn_update_all = QPushButton(f"Update All ({len(outdated_wids)})")
            btn_update_all.setFixedHeight(28)
            btn_update_all.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_update_all.setStyleSheet("""
                QPushButton {
                    background: #E5A93C;
                    border: none;
                    border-radius: 6px;
                    color: #000000;
                    font-size: 8.5pt;
                    font-weight: bold;
                    padding: 0 12px;
                }
                QPushButton:hover {
                    background: #F0B84D;
                }
            """)
            btn_update_all.clicked.connect(lambda: self._update_workshop_items(outdated_wids))
            header_lay.addWidget(btn_update_all)

        btn_rescan = QPushButton("Refresh")
        btn_rescan.setFixedHeight(28)
        btn_rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_rescan.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #FFFFFF;
                font-size: 8.5pt;
                font-weight: bold;
                padding: 0 12px;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.15);
            }}
        """)
        btn_rescan.clicked.connect(self._scan_workshop_mods_async)
        header_lay.addWidget(btn_rescan)

        self.ws_layout.addWidget(header_widget)

        if not ws_mods:
            empty_box = QFrame()
            empty_box.setStyleSheet("background: rgba(255, 255, 255, 0.03); border-radius: 8px; padding: 20px;")
            empty_lay = QVBoxLayout(empty_box)
            empty_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl = QLabel("No Workshop mods installed for this game yet.\nUse 'Fetch Manifest' -> 'Workshop Downloader' to install mods.")
            empty_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 9.5pt; line-height: 1.4;")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lay.addWidget(empty_lbl)
            self.ws_layout.addWidget(empty_box)
        else:
            for mod in ws_mods:
                mod_card = QFrame()
                mod_card.setObjectName("modCard")
                mod_card.setStyleSheet("""
                    QFrame#modCard {
                        background: rgba(255, 255, 255, 0.04);
                        border: 1px solid rgba(255, 255, 255, 0.08);
                        border-radius: 10px;
                    }
                    QFrame#modCard:hover {
                        background: rgba(255, 255, 255, 0.07);
                        border-color: rgba(255, 255, 255, 0.15);
                    }
                """)
                card_lay = QHBoxLayout(mod_card)
                card_lay.setContentsMargins(14, 10, 14, 10)
                card_lay.setSpacing(10)

                size_bytes = mod['size']
                if size_bytes >= 1024 * 1024 * 1024:
                    formatted_size = f"{size_bytes / (1024**3):.2f} GB"
                elif size_bytes >= 1024 * 1024:
                    formatted_size = f"{size_bytes / (1024**2):.2f} MB"
                else:
                    formatted_size = f"{size_bytes / 1024:.2f} KB"

                date_str = mod.get('date_str', '')
                details_line = f"{formatted_size} • {date_str}" if date_str else formatted_size
                import html
                clean_title = str(mod.get('title', ''))      # raw title for dialogs/delete
                mod_title = html.escape(clean_title)          # HTML-escaped for QLabel rich text

                update_badge = ""
                if mod.get("update_available"):
                    update_badge = "<span style='background: #E5A93C; color: #000000; font-size: 7.5pt; font-weight: bold; padding: 2px 6px; border-radius: 4px; margin-left: 8px;'>UPDATE AVAILABLE</span>"

                info_text = (
                    f"<b style='color: #FFFFFF; font-size: 9.5pt;'>{mod_title}</b>{update_badge}<br>"
                    f"<span style='color: rgba(255,255,255,0.6); font-size: 8pt;'>{details_line}</span>"
                )
                mod_info = QLabel(info_text)
                mod_info.setStyleSheet("background: transparent; border: none;")
                card_lay.addWidget(mod_info, 1)

                wid = mod['wid']
                mod_path = mod['path']

                # ── Action buttons (SVG icons, theme-coloured) ──────────────────
                from utils.color_utils import make_svg_icon
                from utils.paths import Paths
                from PyQt6.QtCore import QSize as _QSize

                _UPD_COLOR  = "#E5A93C"           # amber — always warm update colour
                _MUTED      = "rgba(255,255,255,0.70)"  # icon colour at rest
                _DEL_COLOR  = "#EF4444"           # red for delete

                def _mk_btn(tooltip, icon_path, icon_color, bg_normal, bg_hover,
                            border_normal, border_hover, fg_hover, fixed_size=34):
                    _b = QPushButton()
                    _b.setFixedSize(fixed_size, fixed_size)
                    _b.setToolTip(tooltip)
                    _b.setCursor(Qt.CursorShape.PointingHandCursor)
                    _b.setText("")
                    _ic = make_svg_icon(icon_path, icon_color, size=18)
                    _b.setIcon(_ic)
                    _b.setIconSize(_QSize(18, 18))
                    _b.setStyleSheet(f"""
                        QPushButton {{
                            background: {bg_normal};
                            border: 1px solid {border_normal};
                            border-radius: 7px;
                            padding: 0px;
                        }}
                        QPushButton:hover {{
                            background: {bg_hover};
                            border-color: {border_hover};
                        }}
                        QPushButton:pressed {{
                            background: {border_hover};
                            border-color: {border_hover};
                        }}
                    """)
                    return _b

                actions_layout = QHBoxLayout()
                actions_layout.setSpacing(5)
                actions_layout.setContentsMargins(0, 0, 0, 0)

                # 1. Update button — ALWAYS reserve space, hide/show based on status
                btn_upd = _mk_btn(
                    "Update mod to latest version",
                    Paths.icon("up1.svg"),
                    _UPD_COLOR,
                    "rgba(229,169,60,0.15)",
                    _UPD_COLOR,
                    "rgba(229,169,60,0.35)",
                    _UPD_COLOR,
                    "#000000",
                )
                # Tint the icon black on hover via a re-render trick: we use stylesheet opacity trick
                btn_upd.setVisible(bool(mod.get("update_available")))
                btn_upd.clicked.connect(lambda _c, w=wid: self._update_workshop_items([w]))
                actions_layout.addWidget(btn_upd)

                # 2. View on Steam Workshop
                btn_view = _mk_btn(
                    "View on Steam Workshop",
                    Paths.icon("link.svg"),
                    "rgba(255,255,255,0.70)",
                    "rgba(255,255,255,0.07)",
                    self.accent_color,
                    "rgba(255,255,255,0.12)",
                    self.accent_color,
                    btn_fg,
                )
                btn_view.clicked.connect(lambda _c, w=wid: QDesktopServices.openUrl(
                    QUrl(f"https://steamcommunity.com/sharedfiles/filedetails/?id={w}")))
                actions_layout.addWidget(btn_view)

                # 3. Open local folder
                btn_open = _mk_btn(
                    "Open local mod folder",
                    Paths.icon("folder.svg"),
                    "rgba(255,255,255,0.70)",
                    "rgba(255,255,255,0.07)",
                    "rgba(255,255,255,0.18)",
                    "rgba(255,255,255,0.12)",
                    "rgba(255,255,255,0.30)",
                    "#FFFFFF",
                )
                btn_open.clicked.connect(lambda _c, p=mod_path: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(p)))
                actions_layout.addWidget(btn_open)

                # 4. Delete / uninstall
                btn_del = _mk_btn(
                    "Uninstall / delete mod",
                    Paths.icon("bin.svg"),
                    _DEL_COLOR,
                    "rgba(239,68,68,0.12)",
                    "#EF4444",
                    "rgba(239,68,68,0.28)",
                    "#EF4444",
                    "#FFFFFF",
                )
                btn_del.clicked.connect(lambda _c, w=wid, p=mod_path, t=clean_title:
                    self._delete_workshop_item_dialog(w, p, t))
                actions_layout.addWidget(btn_del)

                card_lay.addLayout(actions_layout)

                self.ws_layout.addWidget(mod_card)

        self.ws_layout.addStretch()

    def _delete_workshop_item_dialog(self, wid: str, mod_path: str, title: str):
        from PyQt6.QtWidgets import QMessageBox
        from utils.workshop_helpers import delete_workshop_item
        reply = QMessageBox.question(
            self,
            "Delete Workshop Mod",
            f"Are you sure you want to delete '{title}' (WID: {wid})?\nThis will permanently remove the item files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            delete_workshop_item(self.appid, wid, mod_path)
            self._scan_workshop_mods_async()

    def _update_workshop_items(self, wids: List[str]):
        if not wids:
            return
        try:
            from utils.settings import get_settings
            from PyQt6.QtWidgets import QMessageBox, QApplication
            settings = get_settings()
            api_key = settings.value("morrenus_api_key", "", type=str).strip()
            max_downloads = settings.value("workshop_max_downloads", 8, type=int)
            cellid = settings.value("workshop_cell_id", "", type=str)
            steam_integration = settings.value("workshop_steam_enabled", True, type=bool)

            from core.steam_helpers import find_steam_install
            dest_path = find_steam_install()

            from ui.main_window import MainWindow
            mw = None
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, MainWindow):
                    mw = widget
                    break

            if mw and hasattr(mw, "job_queue"):
                mw.job_queue.add_workshop_job(wids, api_key, max_downloads, cellid, steam_integration, dest_path)
                QMessageBox.information(
                    self,
                    "Workshop Update",
                    f"Queued update for {len(wids)} workshop mod(s)!\nCheck Job Manager for download progress.",
                )
            else:
                QMessageBox.warning(self, "Error", "Job queue manager not available.")
        except Exception as e:
            logger.error(f"Failed to queue workshop update: {e}")

    def _init_tickets_tab(self):
        """Initialize the Tickets Management tab with drag & drop import, export, and status."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        tick_widget = QWidget()
        tick_widget.setAcceptDrops(True)

        def _drag_enter(event):
            if event.mimeData().hasUrls() or event.mimeData().hasText():
                event.acceptProposedAction()

        def _drop_event(event):
            urls = event.mimeData().urls()
            if urls:
                file_path = urls[0].toLocalFile()
                self._handle_ticket_file_import(file_path)
            elif event.mimeData().hasText():
                text = event.mimeData().text()
                self._handle_ticket_text_import(text)

        tick_widget.dragEnterEvent = _drag_enter
        tick_widget.dropEvent = _drop_event

        layout = QVBoxLayout(tick_widget)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        # Header Title
        title_lbl = QLabel(f"SLSsteam Ownership Tickets ({self.appid})")
        title_lbl.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {self.accent_color};")
        layout.addWidget(title_lbl)

        # Drag & Drop Zone Frame
        drop_zone = QFrame()
        drop_zone.setObjectName("dropZone")
        drop_zone.setStyleSheet(f"""
            QFrame#dropZone {{
                background: rgba(255, 255, 255, 0.03);
                border: 2px dashed rgba(255, 255, 255, 0.15);
                border-radius: 10px;
                padding: 16px;
            }}
            QFrame#dropZone:hover {{
                border-color: {self.accent_color};
                background: rgba(255, 255, 255, 0.05);
            }}
        """)
        drop_lay = QVBoxLayout(drop_zone)
        drop_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_lay.setSpacing(6)

        drop_icon = QLabel("Drag & Drop Ticket File (.yaml) Here")
        drop_icon.setStyleSheet(f"color: {self.accent_color}; font-size: 10.5pt; font-weight: bold;")
        drop_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_lay.addWidget(drop_icon)

        drop_sub = QLabel("Or click Browse File / Paste raw base64 payload below")
        drop_sub.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8.5pt;")
        drop_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_lay.addWidget(drop_sub)

        layout.addWidget(drop_zone)

        # Status & Inspection Box
        from utils.ticket_manager import get_ticket_status, verify_ticket_activation
        t_status = get_ticket_status(self.appid)
        v_status = verify_ticket_activation(self.appid)

        status_box = QFrame()
        status_box.setStyleSheet("background: rgba(255, 255, 255, 0.04); border-radius: 8px; padding: 12px;")
        status_lay = QVBoxLayout(status_box)
        status_lay.setSpacing(6)

        if t_status["exists"]:
            if v_status.get("sls_active"):
                st_color = "#4CAF50"
                st_badge = "Active (Verified in SLSsteam)"
            elif v_status.get("base64_valid"):
                st_color = "#FFC107"
                st_badge = "Installed (Ready for SLSsteam)"
            else:
                st_color = "#FF9800"
                st_badge = "Installed (Invalid Format)"

            st_text = f"<b>Status:</b> <span style='color: {st_color}; font-weight: bold;'>{st_badge}</span>"
            if t_status.get("steam_id"):
                raw_sid = str(t_status["steam_id"]).strip()
                if len(raw_sid) > 6:
                    blurred_sid = raw_sid[:4] + "••••••••" + raw_sid[-2:]
                else:
                    blurred_sid = "••••••••••••"
                st_text += f"<br><b>Holder SteamID:</b> <span style='font-family: monospace; color: rgba(255, 255, 255, 0.7);'>{blurred_sid}</span>"
            if t_status.get("updated_at"):
                st_text += f"<br><b>Last Updated:</b> <span style='color: rgba(255, 255, 255, 0.7);'>{t_status['updated_at']}</span>"
        else:
            st_text = f"<b>Status:</b> <span style='color: #ff8a7a;'>No Ticket file installed</span>"

        status_lbl = QLabel(st_text)
        status_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; line-height: 1.4;")
        status_lay.addWidget(status_lbl)

        layout.addWidget(status_box)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_verify = QPushButton("Verify Ticket Status")
        btn_verify.setFixedHeight(30)
        btn_verify.setStyleSheet("font-weight: bold; background: rgba(255, 255, 255, 0.1); color: #FFFFFF; border-radius: 5px;")
        btn_verify.clicked.connect(self._verify_ticket_status_dialog)
        btn_row.addWidget(btn_verify)

        if t_status["exists"]:
            btn_delete = QPushButton("Delete Ticket")
            btn_delete.setFixedHeight(30)
            btn_delete.setStyleSheet("background: rgba(220, 50, 40, 0.2); color: #ff8a7a; border: 1px solid rgba(220, 50, 40, 0.4); border-radius: 5px;")
            btn_delete.clicked.connect(self._delete_installed_ticket)
            btn_row.addWidget(btn_delete)

        layout.addLayout(btn_row)
        layout.addStretch()

        scroll.setWidget(tick_widget)
        self.stacked.addWidget(scroll)

    def _handle_ticket_file_import(self, file_path: str):
        from utils.ticket_manager import import_ticket, validate_ticket_file
        val = validate_ticket_file(file_path, self.appid)
        if not val.get("valid"):
            QMessageBox.warning(self, "Invalid Ticket File", f"Sanitation check failed:\n{val.get('error')}")
            return

        ok, msg = import_ticket(file_path, self.appid)
        if ok:
            QMessageBox.information(self, "Ticket Imported", f"✓ {msg}")
            self._switch_tab(getattr(self, "_tickets_tab_index", 3))
        else:
            QMessageBox.critical(self, "Import Failed", msg)

    def _handle_ticket_text_import(self, raw_text: str):
        from utils.ticket_manager import import_ticket, validate_ticket_content
        val = validate_ticket_content(raw_text, self.appid)
        if not val.get("valid"):
            QMessageBox.warning(self, "Invalid Ticket Payload", f"Sanitation check failed:\n{val.get('error')}")
            return

        ok, msg = import_ticket(raw_text, self.appid)
        if ok:
            QMessageBox.information(self, "Ticket Imported", f"✓ {msg}")
            self._switch_tab(getattr(self, "_tickets_tab_index", 3))
        else:
            QMessageBox.critical(self, "Import Failed", msg)

    def _verify_ticket_status_dialog(self):
        from utils.ticket_manager import verify_ticket_activation
        res = verify_ticket_activation(self.appid)
        msg = f"Ticket Verification for AppID {self.appid}:\n\n"
        msg += f"• File Installed: {'Yes' if res['installed'] else 'No'}\n"
        msg += f"• Payload Valid: {'Yes' if res['base64_valid'] else 'No'}\n"
        msg += f"• Active in SLSsteam: {'Yes' if res['sls_active'] else 'No'}\n\n"
        msg += f"Status: {res['message']}"

        if res["working"]:
            QMessageBox.information(self, "Ticket Verified Working", msg)
        elif res["installed"] and res["base64_valid"]:
            QMessageBox.warning(self, "Ticket Installed (Pending Launch)", msg)
        else:
            QMessageBox.critical(self, "Ticket Issue Detected", msg)

    def _paste_and_import_ticket(self):
        clipboard_text = QApplication.clipboard().text()
        if not clipboard_text:
            QMessageBox.warning(self, "Clipboard Empty", "No text found on clipboard to import.")
            return
        self._handle_ticket_text_import(clipboard_text)

    def _export_installed_ticket(self):
        from utils.ticket_manager import export_ticket
        save_path, _ = QFileDialog.getSaveFileName(self, "Export Ticket File", f"ticket_{self.appid}.yaml", "YAML Files (*.yaml)")
        if save_path:
            ok, msg = export_ticket(self.appid, save_path)
            if ok:
                QMessageBox.information(self, "Export Success", f"✓ {msg}")
            else:
                QMessageBox.critical(self, "Export Failed", msg)

    def _delete_installed_ticket(self):
        from utils.ticket_manager import remove_ticket
        ans = QMessageBox.question(self, "Delete Ticket", f"Are you sure you want to remove ticket files for AppID {self.appid}?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            ok, msg = remove_ticket(self.appid)
            if ok:
                QMessageBox.information(self, "Ticket Removed", f"✓ {msg}")
                self._switch_tab(getattr(self, "_tickets_tab_index", 3))
            else:
                QMessageBox.critical(self, "Removal Failed", msg)

    # ──────────────────────────────────────────
    def _on_goldberg_check_complete(self, is_applied):
        self._refresh_drm_emulation_state(is_applied)

    def _refresh_drm_emulation_state(self, is_applied=None):
        from utils.dlc_helpers import is_dlc_only_mode
        is_dlc = is_dlc_only_mode(self.appid)

        if is_applied is not None:
            self._last_goldberg_applied = is_applied
        applied = getattr(self, "_last_goldberg_applied", False)

        if is_dlc:
            if hasattr(self, "sl_row_widget") and self.sl_row_widget:
                self.sl_row_widget.setVisible(False)
            if hasattr(self, "b_steamless_aio") and self.b_steamless_aio:
                self.b_steamless_aio.setEnabled(False)
                self.b_steamless_aio.setToolTip("Not available in DLC-Only mode")
            if hasattr(self, "b_steamless_cli") and self.b_steamless_cli:
                self.b_steamless_cli.setEnabled(False)
                self.b_steamless_cli.setToolTip("Not available in DLC-Only mode")
            if hasattr(self, "b_steamless") and self.b_steamless:
                self.b_steamless.setEnabled(False)
                self.b_steamless.setToolTip("Not available in DLC-Only mode")
            if hasattr(self, "gb_apply_btn") and self.gb_apply_btn:
                self.gb_apply_btn.setEnabled(False)
                self.gb_apply_btn.setToolTip("Not available in DLC-Only mode")
            if hasattr(self, "gb_remove_btn") and self.gb_remove_btn:
                self.gb_remove_btn.setEnabled(False)
                self.gb_remove_btn.setToolTip("Not available in DLC-Only mode")
            if hasattr(self, "fix_btn") and self.fix_btn:
                self.fix_btn.setEnabled(False)
                self.fix_btn.setToolTip("Not needed for DLC-only games")
            return

        # Regular game mode - enable / configure buttons
        if hasattr(self, "sl_row_widget") and self.sl_row_widget:
            self.sl_row_widget.setVisible(True)
        if hasattr(self, "b_steamless_aio") and self.b_steamless_aio:
            self.b_steamless_aio.setEnabled(True)
            self.b_steamless_aio.setToolTip("Remove Steam DRM using Python Steamless (AIO)")
        if hasattr(self, "b_steamless_cli") and self.b_steamless_cli:
            self.b_steamless_cli.setEnabled(True)
            self.b_steamless_cli.setToolTip("Remove Steam DRM using .NET 9 Steamless CLI")
        if hasattr(self, "b_steamless") and self.b_steamless:
            self.b_steamless.setEnabled(True)
            self.b_steamless.setToolTip("Remove Steam DRM using Python Steamless (AIO)")
        if hasattr(self, "fix_btn") and self.fix_btn:
            self.fix_btn.setEnabled(True)
            self.fix_btn.setToolTip("Removes local manifest (.acf) to force Steam verification.")

        if hasattr(self, "gb_apply_btn") and hasattr(self, "gb_remove_btn"):
            if applied:
                self.gb_apply_btn.setEnabled(False)
                self.gb_apply_btn.setToolTip("Goldberg is currently applied")
                self.gb_remove_btn.setEnabled(True)
                self.gb_remove_btn.setToolTip("Remove Goldberg Steam emulator from this game")
            else:
                self.gb_apply_btn.setEnabled(True)
                self.gb_apply_btn.setToolTip("Apply Goldberg Steam emulator to this game")
                self.gb_remove_btn.setEnabled(False)
                self.gb_remove_btn.setToolTip("Goldberg is not applied")

    def _update_depot_label(self):
        btn_text = "Depots: Select"
        if self.settings:
            val = self.settings.value(f"depot_selection/{self.appid}", "", type=str)
            if val:
                try:
                    import json
                    data = json.loads(val)
                    sel = data.get("selected", [])
                    tot = len(data.get("all_available", []))
                    if sel and tot and len(sel) < tot:
                        btn_text = f"Depots: {len(sel)} of {tot}"
                    elif sel and tot and len(sel) == tot:
                        btn_text = "Depots: All"
                    elif sel:
                        btn_text = f"Depots: {len(sel)} Selected"
                    else:
                        btn_text = "Depots: Select"
                except Exception:
                    pass
        if hasattr(self, "choose_depots_btn") and self.choose_depots_btn:
            self.choose_depots_btn.setText(btn_text)

    def _configure_depots_wrapper(self):
        self.parent_window._configure_depots(self.game_data)
        self._update_depot_label()

    def _reset_depots_wrapper(self):
        self.parent_window._reset_depot_selection(self.game_data)
        self._update_depot_label()

    def _get_installed_buildid(self) -> str:
        """Resolves the real installed build ID on disk, exhausting all local sources."""
        # 1. Check game_data directly if valid numeric
        bid = str(self.game_data.get("buildid") or "").strip()
        if bid and bid.isdigit() and bid != "0":
            return bid

        # 2. Check appmanifest file directly
        acf_path = self.game_data.get("appmanifest_path")
        if not acf_path and self.appid and self.appid not in ("0", "N/A", "unknown"):
            from core.steam_helpers import get_steam_libraries
            try:
                for lib in get_steam_libraries():
                    p = Path(lib) / "steamapps" / f"appmanifest_{self.appid}.acf"
                    if p.exists():
                        acf_path = str(p)
                        self.game_data["appmanifest_path"] = acf_path
                        break
            except Exception:
                pass

        if acf_path and os.path.exists(acf_path):
            try:
                with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                m = re.search(r'"buildid"\s+"([^"]+)"', content)
                if m and m.group(1).strip() and m.group(1).strip() != "0":
                    found_bid = m.group(1).strip()
                    self.game_data["buildid"] = found_bid
                    return found_bid
            except Exception:
                pass

        # 3. Check QSettings installed_buildid
        if self.settings and self.appid:
            installed_branch = self.settings.value(f"installed_branch/{self.appid}", "", type=str)
            if installed_branch:
                saved = self.settings.value(f"installed_buildid/{self.appid}/{installed_branch}", "", type=str)
                if saved and str(saved).isdigit() and str(saved) != "0":
                    self.game_data["buildid"] = str(saved)
                    return str(saved)
            saved = self.settings.value(f"installed_buildid/{self.appid}", "", type=str)
            if saved and str(saved).isdigit() and str(saved) != "0":
                self.game_data["buildid"] = str(saved)
                return str(saved)

        # 4. Check ACCELA metadata JSON
        if self.appid and self.appid not in ("0", "N/A", "unknown"):
            try:
                meta_path = get_base_path() / "metadata" / f"{self.appid}.json"
                if meta_path.exists():
                    import json
                    with open(meta_path, "r", encoding="utf-8") as f:
                        m_data = json.load(f)
                        mbid = str(m_data.get("buildid", "")).strip()
                        if mbid and mbid.isdigit() and mbid != "0":
                            self.game_data["buildid"] = mbid
                            return mbid
            except Exception:
                pass

        # 5. Non-digit fallback from game_data if non-empty
        if bid and bid.lower() not in ("unknown", "none", "0", ""):
            return bid

        return ""

    def _get_manifest_age(self):
        if self.appid in ("0", "N/A", "unknown"):
            return "N/A"
        fpath = get_base_path() / "hubcap_manifests" / f"accela_fetch_{self.appid}.zip"
        if fpath.exists():
            try:
                return self._format_time_diff(fpath.stat().st_mtime)
            except Exception:
                pass
        return "Not cached"

    def _get_lua_age(self):
        if self.appid in ("0", "N/A", "unknown"):
            return "N/A"
        try:
            from managers.depot_key_manager import DepotKeyManager
            dkm = DepotKeyManager()
            ts = dkm.get_key_updated_at(self.appid)
            if ts:
                return self._format_time_diff(ts)
        except Exception:
            pass
        return "Not cached"

    def _get_last_checked(self):
        if self.appid in ("0", "N/A", "unknown"):
            return "Never"
        cache = get_update_cache()
        if cache:
            entry = cache._cache.get(str(self.appid))
            if entry and entry.get("updated_at"):
                try:
                    return self._format_time_diff(entry.get("updated_at"))
                except Exception:
                    pass
        return "Never"

    def _format_time_diff(self, ts):
        import time
        diff = int(time.time() - ts)
        if diff < 0:
            diff = 0
        if diff < 60:   return "just now"
        elif diff < 3600:    return f"{diff // 60}min ago"
        elif diff < 86400:   return f"{diff // 3600}hr ago"
        elif diff < 2592000: return f"{diff // 86400}d ago"
        elif diff < 31536000:return f"{diff // 2592000}mo ago"
        else:                return f"{diff // 31536000}yr ago"

    def _cleanup_fetcher(self, key):
        self._active_fetchers.pop(key, None)

    def _on_pin_build_toggled(self, pinned: bool):
        from utils.dlc_helpers import is_dlc_only_mode
        if pinned and is_dlc_only_mode(self.appid):
            res = QMessageBox.warning(
                self,
                "Pin Build Warning",
                f"'{self.game_data.get('game_name', 'This game')}' is currently in DLC-Only mode.\n\n"
                "Pinning a build for a DLC-only game is typically not required and may freeze manifest tracking.\n\n"
                "Are you sure you want to enable Pin Build?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if res != QMessageBox.StandardButton.Yes:
                if hasattr(self, "pin_tile") and self.pin_tile:
                    self.pin_tile.blockSignals(True)
                    self.pin_tile.setChecked(False)
                    self.pin_tile.update_state(False, self.accent_color)
                    self.pin_tile.blockSignals(False)
                return

        if hasattr(self, "pin_tile") and self.pin_tile:
            self.pin_tile.update_state(pinned, self.accent_color)
        if self.settings:
            self.settings.setValue(f"pin_build/{self.appid}", pinned)
        
        if pinned:
            # Mark update status as up_to_date and persist to cache immediately
            self.game_data["update_status"] = "up_to_date"
            try:
                from utils.update_status_cache import get_update_cache
                get_update_cache().set_status(self.appid, "up_to_date")
                get_update_cache().save_async()
                if self.parent_window and hasattr(self.parent_window, "game_manager") and self.parent_window.game_manager:
                    self.parent_window.game_manager.game_update_status_changed.emit(self.appid, "up_to_date")
            except Exception:
                pass

            # Force exclude_from_update_all to True and grey it out
            if hasattr(self, "update_all_tile") and self.update_all_tile:
                self.update_all_tile.setChecked(False)
                self.update_all_tile.update_state(False, "#e05a47", active_sub="Include", inactive_sub="Exclude")
                self.update_all_tile.setEnabled(False)
            if self.settings:
                self.settings.setValue(f"exclude_from_update_all/{self.appid}", True)
            
            # Smart copy/duplicate default manifest zip to specific zip
            try:
                from utils.helpers import get_base_path
                manifests_dir = get_base_path() / "hubcap_manifests"
                installed_bid = self.settings.value(f"installed_buildid/{self.appid}", "") if self.settings else ""
                if installed_bid:
                    specific_zip = manifests_dir / f"accela_fetch_{self.appid}_build_{installed_bid}.zip"
                    if not specific_zip.exists():
                        default_zip = manifests_dir / f"accela_fetch_{self.appid}.zip"
                        if default_zip.exists():
                            import shutil
                            shutil.copy(default_zip, specific_zip)
                            logger.info(f"Duplicated general manifest zip {default_zip.name} to {specific_zip.name} on pin build activation.")
            except Exception as e:
                logger.warning(f"Failed to duplicate manifest zip on pin build activation: {e}")
        else:
            if hasattr(self, "update_all_tile") and self.update_all_tile:
                self.update_all_tile.setEnabled(True)
                self.update_all_tile.setChecked(True)
                self.update_all_tile.update_state(True, self.accent_color, active_sub="Include", inactive_sub="Exclude")
            if self.settings:
                self.settings.setValue(f"exclude_from_update_all/{self.appid}", False)

            if self.parent_window and hasattr(self.parent_window, "_update_pending_updates_ui"):
                self.parent_window._update_pending_updates_ui()

        self._update_validate_button()

    def _reconstruct_manifests_from_depotcache(self):
        install_path = self.game_data.get("install_path")
        if not install_path:
            return
        try:
            from pathlib import Path
            path = Path(install_path).resolve()
            depotcache_dir = path.parents[1] / "depotcache"
            if not (depotcache_dir.exists() and depotcache_dir.is_dir()):
                local_depotcache = path / "depotcache"
                if local_depotcache.exists() and local_depotcache.is_dir():
                    depotcache_dir = local_depotcache
                else:
                    return
            
            # Scan files matching *.manifest
            manifests_map = {}
            for f in depotcache_dir.glob("*.manifest"):
                parts = f.name.replace(".manifest", "").split("_")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    manifests_map[parts[0]] = parts[1]
            
            if manifests_map:
                self.game_data.setdefault("manifests", {}).update(manifests_map)
                logger.info(f"Reconstructed {len(manifests_map)} manifests from depotcache: {manifests_map}")
        except Exception as e:
            logger.warning(f"Failed to reconstruct manifests from depotcache: {e}")

    def update_title(self) -> None:
        """Update the hero banner title; Denuvo + ProtonDB shown as separate pill badges."""
        from utils.dlc_helpers import is_dlc_only_mode

        installed_branch = self.settings.value(f"installed_branch/{self.appid}", "public", type=str)
        display_parts = [self.game_data.get("game_name", "Unknown")]
        if installed_branch and installed_branch != "public":
            display_parts.append(f"({installed_branch})")
        if is_dlc_only_mode(self.appid):
            display_parts.append("[DLC ONLY]")

        if hasattr(self, "name_lbl") and self.name_lbl:
            self.name_lbl.setText(" ".join(display_parts))

        # --- Denuvo badge ---
        if hasattr(self, "_denuvo_badge_lbl") and self._denuvo_badge_lbl:
            from core.ratings import get_denuvo_status
            denuvo = get_denuvo_status(self.appid)
            if denuvo == "cracked":
                d_text, d_color, d_bg = "Denuvo Cracked",    "#81C784", "rgba(129,199,132,0.20)"
            elif denuvo == "hypervisor":
                d_text, d_color, d_bg = "Denuvo Hypervisor", "#FFA726", "rgba(255,167,38,0.18)"
            elif denuvo == "uncracked":
                d_text, d_color, d_bg = "Denuvo Uncracked",  "#E57373", "rgba(229,115,115,0.20)"
            else:
                d_text = None

            if d_text:
                self._denuvo_badge_lbl.setText(d_text)
                self._denuvo_badge_lbl.setStyleSheet(
                    f"color: {d_color}; background-color: {d_bg}; "
                    f"border-radius: 4px; padding: 2px 8px; "
                    f"font-size: 9pt; font-weight: bold; border: none;"
                )
                self._denuvo_badge_lbl.show()
            else:
                self._denuvo_badge_lbl.hide()

        # --- ProtonDB badge ---
        if hasattr(self, "_proton_badge_lbl") and self._proton_badge_lbl:
            from core.ratings import get_protondb_tier
            tier = get_protondb_tier(self.appid)
            _tier_map = {
                "platinum": ("PLATINUM", "#90CAF9", "rgba(33, 150, 243, 0.15)", "rgba(144, 202, 249, 0.30)"),
                "gold":     ("GOLD",     "#FFE082", "rgba(255, 193, 7, 0.15)",   "rgba(255, 224, 130, 0.30)"),
                "silver":   ("SILVER",   "#CFD8DC", "rgba(144, 164, 174, 0.15)", "rgba(207, 216, 220, 0.30)"),
                "bronze":   ("BRONZE",   "#FFAB91", "rgba(255, 112, 67, 0.15)",  "rgba(255, 171, 145, 0.30)"),
                "borked":   ("BORKED",   "#EF9A9A", "rgba(239, 83, 80, 0.18)",   "rgba(239, 154, 154, 0.35)"),
                "native":   ("NATIVE",   "#A5D6A7", "rgba(76, 175, 80, 0.15)",   "rgba(165, 214, 167, 0.30)"),
            }
            if tier and tier in _tier_map:
                p_text, p_color, p_bg, p_border = _tier_map[tier]
                self._proton_badge_lbl.setText(p_text)
                self._proton_badge_lbl.setStyleSheet(
                    f"color: {p_color}; background-color: {p_bg}; border: 1px solid {p_border}; "
                    f"border-radius: 4px; padding: 1px 6px; "
                    f"font-size: 8pt; font-weight: bold; letter-spacing: 0.5px;"
                )
                self._proton_badge_lbl.show()
            else:
                self._proton_badge_lbl.hide()

    def _refresh_dlcdata_btn_text(self):
        if not hasattr(self, "dlcdata_exp_btn") or not self.dlcdata_exp_btn:
            return
        try:
            from utils.yaml_config_manager import get_user_config_path, get_dlc_data
            cp = get_user_config_path()
            if cp.exists() and bool(get_dlc_data(cp, self.appid)):
                self.dlcdata_exp_btn.setText("Revert DLC from DlcData (Advanced)")
            else:
                self.dlcdata_exp_btn.setText("Move DLC to DlcData (Advanced)")
        except Exception:
            self.dlcdata_exp_btn.setText("Move DLC to DlcData (Advanced)")

    def _handle_move_dlc_to_dlcdata(self):
        from utils.yaml_config_manager import (
            get_user_config_path, get_dlc_data, add_dlc_data_batch, remove_dlc_data
        )
        from utils.dlc_helpers import get_all_dlcs_for_app
        from PyQt6.QtWidgets import QMessageBox

        cp = get_user_config_path()
        if not cp.exists():
            QMessageBox.warning(self, "Config Not Found", "SLSsteam config.yaml could not be found.")
            return

        current_dlcs = get_dlc_data(cp, self.appid)
        game_name = self.game_data.get("game_name", f"AppID {self.appid}")

        if current_dlcs:
            # Revert Option
            ans = QMessageBox.question(
                self,
                "Revert DLC from DlcData",
                f"This game currently has {len(current_dlcs)} DLC(s) configured under DlcData in SLSsteam config.yaml.\n\n"
                f"Are you sure you want to revert and remove these DLC entries from DlcData for '{game_name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans == QMessageBox.StandardButton.Yes:
                if remove_dlc_data(cp, self.appid):
                    QMessageBox.information(
                        self,
                        "DlcData Reverted",
                        f"✓ Successfully removed DlcData entries for '{game_name}'.",
                    )
                else:
                    QMessageBox.warning(self, "Action Failed", "Could not remove entries from DlcData.")
                self._refresh_dlcdata_btn_text()
            return

        # Move to DlcData Option
        warn_msg = (
            f"⚠️ EXPERIMENTAL / ADVANCED OPTION\n\n"
            f"This will scan all DLCs for '{game_name}' (AppID {self.appid}) "
            f"and write them to the DlcData section in SLSsteam config.yaml.\n\n"
            f"Notice: This is NOT needed in normal cases! It is only required for rare games "
            f"or games hitting Steam's 64 DLC limit where in-game DLCs do not appear unlocked.\n\n"
            f"You can revert this action at any time using this same button.\n\n"
            f"Do you want to proceed?"
        )
        ans = QMessageBox.question(
            self,
            "Move DLC to DlcData (Advanced)",
            warn_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        # Fetch DLCs
        self.dlcdata_exp_btn.setEnabled(False)
        self.dlcdata_exp_btn.setText("Fetching DLCs...")
        QApplication.processEvents()

        try:
            dlc_list = get_all_dlcs_for_app(self.appid, self.game_data)
            if not dlc_list:
                QMessageBox.information(
                    self,
                    "No DLCs Found",
                    f"No downloadable or store DLCs could be found for '{game_name}'.",
                )
                return

            dlc_dict = {str(d["dlc_appid"]): d["dlc_name"] for d in dlc_list}
            ok = add_dlc_data_batch(cp, self.appid, dlc_dict)
            if ok:
                QMessageBox.information(
                    self,
                    "DLCs Moved to DlcData",
                    f"✓ Successfully wrote {len(dlc_dict)} DLC(s) for '{game_name}' to DlcData in config.yaml.\n\n"
                    f"SLSsteam will now explicitly report all these DLCs to the game.",
                )
            else:
                QMessageBox.critical(
                    self,
                    "Write Failed",
                    "Failed to write entries to DlcData in config.yaml.",
                )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to move DLCs: {e}")
        finally:
            self.dlcdata_exp_btn.setEnabled(True)
            self._refresh_dlcdata_btn_text()



