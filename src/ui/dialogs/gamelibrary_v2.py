import os
import platform
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QSize, QPropertyAnimation, pyqtProperty, pyqtSignal, QUrl
from PyQt6.QtGui import QColor, QPixmap, QPainter, QIntValidator, QPalette, QDesktopServices, QLinearGradient
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QCheckBox,
    QLineEdit, QComboBox, QMessageBox, QWidget, QFrame, QStackedWidget,
    QStylePainter, QStyleOptionComboBox, QStyle, QScrollArea, QApplication,
    QGridLayout, QListView, QStyledItemDelegate,
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
        view = QListView(self)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setItemDelegate(QStyledItemDelegate(view))
        view.setStyleSheet("""
            QListView {
                background-color: #16161a;
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 4px;
                padding: 6px;
                outline: 0px;
            }
            QListView::item {
                min-height: 28px;
                padding: 6px 12px;
                color: #E0E0E0;
                border-radius: 3px;
            }
            QListView::item:hover {
                background-color: rgba(192, 108, 132, 0.6);
                color: #FFFFFF;
            }
            QListView::item:selected {
                background-color: rgba(255, 255, 255, 0.15);
                color: #FFFFFF;
                font-weight: bold;
            }
            QScrollBar:vertical {
                border: none;
                background: rgba(0, 0, 0, 40);
                width: 8px;
                margin: 2px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.3);
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.5);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        self.setView(view)

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

class GameDetailsDialogV2(QDialog):
    branches_loaded = pyqtSignal(dict)

    # ── Rollback toggle: set False to use the old 65px hero layout ──
    USE_V2_HERO = True

    def __init__(self, parent, game_data):
        super().__init__(parent)
        self.parent_window = parent
        self.game_data = game_data
        self.appid = str(game_data.get("appid", "0"))
        self.settings = get_settings()
        self._active_fetchers = {}
        self.branches_loaded.connect(self._on_branches_loaded)

        self.accent_color  = getattr(parent, "accent_color",  "#a1c9fd")
        self.background_color = getattr(parent, "background_color", "#111318")

        self.setWindowTitle(f"{game_data.get('game_name', 'Game')} — Details")
        self.setMinimumSize(540, 420)
        self.resize(580, 480)
        self.setModal(True)

        self._apply_stylesheet()
        self._setup_ui()

        if self.parent():
            from ui.dialogs.dialog_raiser import DialogRaiser
            DialogRaiser(self.parent(), self)

        # Hook up main progress bar
        main_win = parent.main_window if hasattr(parent, "main_window") else None
        if main_win and hasattr(main_win, "progress_bar"):
            main_win.progress_bar.valueChanged.connect(self._on_main_progress_changed)

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
                color: rgba(255, 255, 255, 25);
            }}
            QLineEdit {{
                background-color: rgba(0, 0, 0, 50);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 15);
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 9.5pt;
            }}
            QLineEdit:focus {{ border-color: {ac}; }}
            QLineEdit:disabled {{
                color: rgba(255, 255, 255, 30);
                border-color: rgba(255, 255, 255, 8);
            }}
            QComboBox {{
                background-color: rgba(0, 0, 0, 50);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 15);
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 9.5pt;
            }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox QAbstractItemView {{
                background-color: #1a1a20;
                border: 1px solid rgba(255, 255, 255, 25);
                selection-background-color: {ac};
                selection-color: #FFFFFF;
                font-size: 9.5pt;
                outline: 0px;
                padding: 2px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 24px;
                padding: 4px 8px;
                color: #FFFFFF;
            }}
            QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{
                background-color: {ac};
                color: #FFFFFF;
            }}
            QCheckBox {{
                color: #FFFFFF;
                font-size: 9.5pt;
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 14px; height: 14px;
                border: 1px solid rgba(255, 255, 255, 20);
                border-radius: 3px;
                background: rgba(0, 0, 0, 40);
            }}
            QCheckBox::indicator:checked {{
                background-color: {ac};
                border-color: {ac};
            }}
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                border: none;
                background: rgba(0, 0, 0, 15);
                width: 4px;
                margin: 0px;
                border-radius: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 35);
                min-height: 20px;
                border-radius: 2px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(255, 255, 255, 65);
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
                background-color: rgba(0, 0, 0, 20);
                border-bottom: 1px solid rgba(255, 255, 255, 10);
            }}
        """)
        tab_bar_layout = QHBoxLayout(tab_bar_frame)
        tab_bar_layout.setContentsMargins(10, 0, 10, 0)
        tab_bar_layout.setSpacing(0)

        self._tab_buttons = []
        self._pages_info = [("Info", 0), ("Tools", 1)]
        for label, idx in self._pages_info:
            btn = QPushButton(label)
            btn.setFlat(True)
            btn.setCheckable(True)
            btn.setFixedHeight(30)
            btn.setStyleSheet("border: none; border-radius: 0px; padding: 0px 16px; font-size: 9.5pt;")
            btn.clicked.connect(lambda _c, i=idx: self._switch_tab(i))
            tab_bar_layout.addWidget(btn)
            self._tab_buttons.append(btn)

        tab_bar_layout.addStretch()

        close_btn = QPushButton("✕ Close")
        close_btn.setFlat(True)
        close_btn.setFixedHeight(30)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                border: none; border-radius: 0;
                padding: 0 10px; font-size: 9.5pt;
                color: rgba(255, 255, 255, 60);
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
        self._init_tools_tab()
        root.addWidget(self.stacked, 1)

        self._switch_tab(0)

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
                        color: rgba(255, 255, 255, 50);
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
        self.hero.setFixedHeight(100)
        banner_layout = QVBoxLayout(self.hero)
        banner_layout.setContentsMargins(14, 8, 120, 8)
        banner_layout.setSpacing(4)

        self.name_lbl = QLabel()
        self.name_lbl.setStyleSheet(
            "font-size: 14pt; font-weight: bold; color: #FFFFFF; background: transparent;")
        self.name_lbl.setWordWrap(True)
        banner_layout.addWidget(self.name_lbl)

        # Ratings badges row (Denuvo + ProtonDB pills, populated by update_title)
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
        banner_layout.addLayout(self._ratings_row)

        self.appid_lbl = QLabel(f"App ID: {self.appid}")
        self.appid_lbl.setStyleSheet(
            "font-size: 8pt; color: rgba(255, 255, 255, 55); background: transparent;")
        banner_layout.addWidget(self.appid_lbl)

        self.update_title()


        # Stats row — horizontal labels under name
        stats_row = QHBoxLayout()
        stats_row.setSpacing(16)
        def _stat_item(label_text, value_text, value_color=None):
            item_widget = QVBoxLayout()
            item_widget.setSpacing(1)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color: {self.accent_color}; font-size: 7pt; background: transparent; font-weight: bold;")
            val = QLabel(value_text)
            val.setStyleSheet(
                f"color: {value_color or self.accent_color}; font-size: 7.5pt; font-weight: bold; background: transparent;")
            item_widget.addWidget(lbl)
            item_widget.addWidget(val)
            return item_widget, val

        size_str = self.parent_window._format_size(self.game_data.get("size_on_disk", 0))
        ri, self.size_val_lbl = _stat_item("SIZE", size_str)
        stats_row.addLayout(ri)

        ri, self.cached_val_lbl = _stat_item("MANIFEST", self._get_manifest_age())
        stats_row.addLayout(ri)

        bid_str = str(self.game_data.get("buildid") or "Unknown")
        ri, self.build_val_lbl = _stat_item("BUILD", bid_str)
        self._hero_build_val_lbl = self.build_val_lbl
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
        self.hero.setFixedHeight(65)
        banner_layout = QHBoxLayout(self.hero)
        banner_layout.setContentsMargins(14, 6, 180, 6)
        banner_layout.setSpacing(0)

        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        self.name_lbl = QLabel()
        self.name_lbl.setStyleSheet(
            "font-size: 12.5pt; font-weight: bold; color: #FFFFFF; background: transparent;")
        self.name_lbl.setWordWrap(True)
        name_col.addWidget(self.name_lbl)

        # Ratings badges row (Denuvo + ProtonDB pills, populated by update_title)
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

        self.appid_lbl = QLabel(f"App ID: {self.appid}")
        self.appid_lbl.setStyleSheet(
            "font-size: 8pt; color: rgba(255, 255, 255, 60); background: transparent;")
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
        f.setStyleSheet("background: rgba(255, 255, 255, 8); border: none; max-height: 1px;")
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

    # ──────────────────────────────────────────
    #  TAB 1 — Info
    # ──────────────────────────────────────────
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

        # ── Status Pill / Banner ─────────────────────────────────
        self.status_btn = QPushButton()
        self.status_btn.setFixedHeight(26)
        self.status_btn.clicked.connect(self._on_status_btn_clicked)
        lay.addWidget(self.status_btn)
        lay.addSpacing(12)

        # ── Stats Grid (hidden in v2 hero — stats are inline) ───
        if not self.USE_V2_HERO:
            stats_widget = QWidget()
            stats_grid = QGridLayout(stats_widget)
            stats_grid.setContentsMargins(0, 0, 0, 0)
            stats_grid.setSpacing(10)

            size_lbl = QLabel("Install size:")
            size_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9.5pt;")
            size_val = self.parent_window._format_size(self.game_data.get("size_on_disk", 0))
            self.size_val_lbl = QLabel(size_val)
            self.size_val_lbl.setStyleSheet(f"color: {self.accent_color}; font-size: 9.5pt; font-weight: bold;")

            cached_lbl = QLabel("Manifest cached:")
            cached_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9.5pt;")
            self.cached_val_lbl = QLabel(self._get_manifest_age())
            self.cached_val_lbl.setStyleSheet(f"color: {self.accent_color}; font-size: 9.5pt; font-weight: bold;")

            build_id_str = str(self.game_data.get("buildid") or "Unknown")
            build_lbl = QLabel("Build ID:")
            build_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9.5pt;")
            self.build_val_lbl = QLabel(build_id_str)
            self.build_val_lbl.setStyleSheet(f"color: {self.accent_color}; font-size: 9.5pt; font-weight: bold;")

            lua_lbl = QLabel("LUA cached:")
            lua_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9.5pt;")
            self.lua_val_lbl = QLabel(self._get_lua_age())
            self.lua_val_lbl.setStyleSheet(f"color: {self.accent_color}; font-size: 9.5pt; font-weight: bold;")

            stats_grid.addWidget(size_lbl, 0, 0)
            stats_grid.addWidget(self.size_val_lbl, 0, 1)
            stats_grid.addWidget(cached_lbl, 0, 2)
            stats_grid.addWidget(self.cached_val_lbl, 0, 3)

            stats_grid.addWidget(build_lbl, 1, 0)
            stats_grid.addWidget(self.build_val_lbl, 1, 1)
            stats_grid.addWidget(lua_lbl, 1, 2)
            stats_grid.addWidget(self.lua_val_lbl, 1, 3)

            open_folder_btn = QPushButton("Open Install Folder")
            open_folder_btn.setFixedHeight(24)
            open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            open_folder_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255, 255, 255, 0.10);
                    border: 1px solid rgba(255, 255, 255, 0.22);
                    border-radius: 4px;
                    color: #FFFFFF;
                    font-weight: bold;
                    font-size: 8.5pt;
                    padding: 2px 8px;
                }}
                QPushButton:hover {{
                    background: rgba(255, 255, 255, 0.22);
                    border-color: {self.accent_color};
                    color: {self.accent_color};
                }}
            """)
            open_folder_btn.clicked.connect(
                lambda: self.parent_window._open_folder(self.game_data.get("install_path")))
            stats_grid.addWidget(open_folder_btn, 2, 0, 1, 4)

            from utils.dlc_helpers import is_dlc_only_mode, get_dlc_only_info
            self._is_dlc = is_dlc_only_mode(self.appid)

            lay.addWidget(stats_widget)
        else:
            # v2 hero: stats moved into hero, just the open folder button
            open_folder_btn = QPushButton("Open Install Folder")
            open_folder_btn.setFixedHeight(24)
            open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            open_folder_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255, 255, 255, 0.10);
                    border: 1px solid rgba(255, 255, 255, 0.22);
                    border-radius: 4px;
                    color: #FFFFFF;
                    font-weight: bold;
                    font-size: 8.5pt;
                    padding: 2px 12px;
                }}
                QPushButton:hover {{
                    background: rgba(255, 255, 255, 0.22);
                    border-color: {self.accent_color};
                    color: {self.accent_color};
                }}
            """)
            open_folder_btn.clicked.connect(
                lambda: self.parent_window._open_folder(self.game_data.get("install_path")))
            lay.addWidget(open_folder_btn)

            from utils.dlc_helpers import is_dlc_only_mode, get_dlc_only_info
            self._is_dlc = is_dlc_only_mode(self.appid)
        lay.addSpacing(12)
        lay.addWidget(self._thin_line())
        lay.addSpacing(10)

        # ── Actions (Select Branch, Build & Validate) ────────────
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        installed_branch = self.settings.value(f"installed_branch/{self.appid}", "public", type=str)
        installed_bid = self.settings.value(
            f"installed_buildid/{self.appid}/{installed_branch}",
            self.settings.value(f"installed_buildid/{self.appid}", str(self.game_data.get("buildid") or ""), type=str),
            type=str)

        # Default the selected branch to whatever the user has installed
        saved_b = installed_branch or self.settings.value(f"selected_branch/{self.appid}", "public", type=str)
        self.settings.setValue(f"selected_branch/{self.appid}", saved_b)

        self.branch_combo = CenteredComboBox()
        self.branch_combo.addItem(f"public ({installed_bid})" if installed_bid else "public", "public")
        self.branch_combo.setFixedHeight(26)
        self.branch_combo.setFixedWidth(180)
        self.branch_combo.setMaxVisibleItems(5)
        actions_row.addWidget(self.branch_combo, 0)

        self.validate_btn = ProgressButton("Verify Files", self)
        self.validate_btn.setFixedHeight(26)
        self.validate_btn.setEnabled(True)
        self.validate_btn.setStyleSheet("font-weight: bold; background: rgba(255, 255, 255, 12); color: rgba(255, 255, 255, 75); border: none;")
        actions_row.addWidget(self.validate_btn, 1)

        lay.addLayout(actions_row)
        lay.addSpacing(6)

        # ── Manual Build Download ─────────────────────────────────
        lay.addWidget(self._thin_line())
        lay.addSpacing(6)

        # Section Header / Toggle Button
        self.manual_expanded = False
        self.manual_expand_btn = QPushButton("▶  Manual Build Download (Advanced)")
        self.manual_expand_btn.setObjectName("manual_expand_btn")
        self.manual_expand_btn.setFlat(True)
        self.manual_expand_btn.setFixedHeight(24)
        self.manual_expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.manual_expand_btn.setStyleSheet(f"""
            QPushButton#manual_expand_btn {{
                border: none;
                border-radius: 0px;
                color: rgba(255, 255, 255, 0.4);
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 0.5px;
                background: transparent;
                background-color: transparent;
                text-align: left;
                padding: 0px;
            }}
            QPushButton#manual_expand_btn:hover {{
                color: {self.accent_color};
                background: transparent;
                background-color: transparent;
                border: none;
            }}
        """)
        self.manual_expand_btn.clicked.connect(self._toggle_manual_section)
        lay.addWidget(self.manual_expand_btn)

        # Container widget for the inputs
        self.manual_container = QWidget()
        self.manual_container.setVisible(False)
        manual_layout = QVBoxLayout(self.manual_container)
        manual_layout.setContentsMargins(0, 4, 0, 4)
        manual_layout.setSpacing(6)

        manual_row = QHBoxLayout()
        manual_row.setSpacing(6)

        self.manual_depot_combo = CenteredComboBox()
        self.manual_depot_combo.setFixedHeight(26)
        self.manual_depot_combo.setFixedWidth(100)
        self.manual_depot_combo.setMaxVisibleItems(5)
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
            logger.info(f"[DEBUG_DEV] Loaded {len(depots_dict)} depots from DB for app {self.appid}")
        except Exception as e:
            logger.error(f"[DEBUG_DEV] Failed to load depots from DB directly: {e}", exc_info=True)

        if not depots_dict:
            depots_dict = self.game_data.get("depots", {})

        if depots_dict:
            for d_id, d_info in depots_dict.items():
                self.manual_depot_combo.addItem(str(d_id), d_id)
        else:
            self.manual_depot_combo.addItem("No Depots", "")
        manual_row.addWidget(self.manual_depot_combo)

        self.manual_build_input = QLineEdit()
        self.manual_build_input.setPlaceholderText("Build ID")
        self.manual_build_input.setFixedHeight(26)
        self.manual_build_input.setFixedWidth(90)
        manual_row.addWidget(self.manual_build_input)

        self.manual_manifest_input = QLineEdit()
        self.manual_manifest_input.setPlaceholderText("Manifest ID")
        self.manual_manifest_input.setFixedHeight(26)
        self.manual_manifest_input.setFixedWidth(160)
        manual_row.addWidget(self.manual_manifest_input)

        self.manual_download_btn = QPushButton("Download")
        self.manual_download_btn.setFixedHeight(26)
        self.manual_download_btn.clicked.connect(self._on_manual_download_clicked)
        manual_row.addWidget(self.manual_download_btn)

        manual_layout.addLayout(manual_row)
        lay.addWidget(self.manual_container)
        lay.addSpacing(6)

        # Hook up manual download button state validation
        self.manual_depot_combo.currentIndexChanged.connect(self._update_manual_download_btn_state)
        self.manual_build_input.textChanged.connect(self._update_manual_download_btn_state)
        self.manual_manifest_input.textChanged.connect(self._update_manual_download_btn_state)
        self._update_manual_download_btn_state()

        self.validate_btn.clicked.connect(self._on_validate_btn_clicked)
        self.branch_combo.currentIndexChanged.connect(self._on_branch_combo_changed)

        # Fast-path: load branches synchronously from DB cache so the combo and
        # validate button render correctly the moment the dialog opens.
        # A silent background refresh is fired afterwards to keep the DB warm.
        self._load_branches_immediate()

        self._update_status_ui(self.game_data.get("update_status"))

        if self.parent_window.game_manager:
            self.parent_window.game_manager.game_update_status_changed.connect(self._on_status_changed)
            self.parent_window.game_manager.game_hubcap_status_checked.connect(self._on_hubcap_status_changed)
            
            def _cleanup_signals():
                if self.parent_window.game_manager:
                    try:
                        self.parent_window.game_manager.game_update_status_changed.disconnect(self._on_status_changed)
                    except Exception:
                        pass
                    try:
                        self.parent_window.game_manager.game_hubcap_status_checked.disconnect(self._on_hubcap_status_changed)
                    except Exception:
                        pass
            self.finished.connect(_cleanup_signals)

        lay.addSpacing(12)
        lay.addWidget(self._thin_line())
        lay.addSpacing(10)

        # ── Preferences ──────────────────────────────────────────
        def _pref_row(label, toggle, tooltip=None):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt;")
            if tooltip:
                lbl.setToolTip(tooltip)
            row.addWidget(lbl, 1)
            row.addWidget(toggle)
            return row

        self.pref1_toggle = SwitchToggle(active_color=self.accent_color)
        self.pref1_toggle.setChecked(
            self.settings.value(f"auto_update_manifest/{self.appid}", True, type=bool) if self.settings else True)
        self.pref1_toggle.stateChanged.connect(
            lambda s: self.settings.setValue(f"auto_update_manifest/{self.appid}", s) if self.settings else None)
        lay.addLayout(_pref_row("Auto-update manifest", self.pref1_toggle))
        lay.addSpacing(6)

        self.pref2_toggle = SwitchToggle(active_color="#e05a47")
        self.pref2_toggle.setChecked(
            self.settings.value(f"exclude_from_update_all/{self.appid}", False, type=bool) if self.settings else False)
        self.pref2_toggle.stateChanged.connect(
            lambda s: self.settings.setValue(f"exclude_from_update_all/{self.appid}", s) if self.settings else None)
        lay.addLayout(_pref_row("Exclude from update-all", self.pref2_toggle))
        lay.addSpacing(6)

        self.pref3_toggle = SwitchToggle(active_color="#4a90d9")
        self.pref3_toggle.setChecked(
            self.settings.value(f"dlc_only_mode/{self.appid}", False, type=bool) if self.settings else False)
        self.pref3_toggle.stateChanged.connect(self._on_dlc_only_toggled)
        lay.addLayout(_pref_row("DLC Only Mode", self.pref3_toggle,
            "Enable if you own the base game separately.\n"
            "Update checks compare only your selected DLC depots."))
        lay.addSpacing(6)

        self.pref4_toggle = SwitchToggle(active_color=self.accent_color)
        is_pinned = self.settings.value(f"pin_build/{self.appid}", False, type=bool) if self.settings else False
        self.pref4_toggle.setChecked(is_pinned)
        self.pref4_toggle.stateChanged.connect(self._on_pin_build_toggled)
        lay.addLayout(_pref_row("Pin Build", self.pref4_toggle,
            "Lock the installed version in place to disable update prompts\n"
            "and allow verification of this specific build version."))

        # Initialize Exclude from Update-All state if pinned
        if is_pinned:
            self.pref2_toggle.setChecked(False)
            self.pref2_toggle.setEnabled(False)

        lay.addSpacing(12)
        lay.addWidget(self._thin_line())
        lay.addSpacing(10)

        # ── SLSonline ────────────────────────────────────────────
        sls_row = QHBoxLayout()
        sls_row.setContentsMargins(0, 0, 0, 0)
        sls_row.setSpacing(10)
        el = QLabel("Enable SLSonline")
        el.setStyleSheet("color: #FFFFFF; font-size: 9.5pt;")
        self.sls_toggle = SwitchToggle(active_color=self.accent_color)
        sls_row.addWidget(el)
        sls_row.addWidget(self.sls_toggle)
        fl = QLabel("Fake App ID:")
        fl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9.5pt;")
        self.sls_input = QLineEdit()
        self.sls_input.setPlaceholderText("480")
        self.sls_input.setValidator(QIntValidator())
        self.sls_input.setFixedHeight(22)
        self.sls_input.setFixedWidth(80)
        sls_row.addWidget(fl)
        sls_row.addWidget(self.sls_input)
        sls_row.addStretch()
        lay.addLayout(sls_row)
        self._init_slsonline_logic()

        lay.addSpacing(12)
        lay.addWidget(self._thin_line())
        lay.addSpacing(10)

        # ── Uninstall expandable panel at bottom footer ──────────
        self._uninstall_expanded = False
        self._uninstall_panel = QFrame()
        self._uninstall_panel.setStyleSheet(f"""
            QFrame {{
                background-color: rgba(180,30,20,12);
                border: 1px solid rgba(180,60,50,30);
                border-radius: 4px;
            }}
        """)
        self._uninstall_panel.setVisible(False)
        self._uninstall_inner = QVBoxLayout(self._uninstall_panel)
        self._uninstall_inner.setContentsMargins(10, 8, 10, 8)
        self._uninstall_inner.setSpacing(6)
        self._uninstall_content = QVBoxLayout()
        self._uninstall_inner.addLayout(self._uninstall_content)
        self._build_uninstall_panel()

        self._uninstall_pill = QPushButton("⚠  Uninstall Game / Content")
        self._uninstall_pill.setFixedHeight(28)
        self._uninstall_pill.setStyleSheet(f"""
            QPushButton {{
                background: rgba(160,30,20,25);
                color: #ff8a7a;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                font-size: 9.5pt;
            }}
            QPushButton:hover {{
                background: rgba(160,30,20,45);
            }}
        """)
        self._uninstall_pill.clicked.connect(self._toggle_uninstall_panel)
        lay.addWidget(self._uninstall_pill)
        lay.addSpacing(4)
        lay.addWidget(self._uninstall_panel)
        lay.addStretch()

        self.stacked.addWidget(scroll)

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
                loaded_from_cache = True
            elif self.game_data.get("buildid"):
                fallback = {"public": {"buildid": str(self.game_data.get("buildid"))}}
                self._on_branches_loaded(fallback)
                loaded_from_cache = True
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
            except Exception as e:
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
        except Exception:
            pass

    def _on_branches_loaded(self, branches_dict: dict):
        if not branches_dict:
            return
        self._branches_dict = branches_dict
        self.branch_combo.blockSignals(True)
        self.branch_combo.clear()

        # Sort: 'public' branch first, then alphabetical. Default to installed branch.
        sorted_keys = sorted(branches_dict.keys(), key=lambda k: (0 if k == "public" else 1, k))
        installed_branch = self.settings.value(f"installed_branch/{self.appid}", "public", type=str)
        saved_branch = installed_branch or self.settings.value(f"selected_branch/{self.appid}", "public", type=str)
        select_idx = 0

        for idx, b_name in enumerate(sorted_keys):
            b_info = branches_dict[b_name]
            bid = str(b_info.get("buildid", "")) if isinstance(b_info, dict) else ""
            label = f"{b_name} ({bid})" if bid else b_name
            self.branch_combo.addItem(label, b_name)
            if b_name == saved_branch:
                select_idx = idx

        self.branch_combo.setCurrentIndex(select_idx)
        self.branch_combo.blockSignals(False)
        self._on_branch_combo_changed()

    def _on_branch_combo_changed(self):
        sel_branch = self.branch_combo.currentData() or "public"
        self.settings.setValue(f"selected_branch/{self.appid}", sel_branch)

        b_dict = getattr(self, "_branches_dict", {})
        b_info = b_dict.get(sel_branch, {}) if isinstance(b_dict, dict) else {}
        branch_bid = str(b_info.get("buildid", "")) if isinstance(b_info, dict) else ""

        installed_branch = self.settings.value(f"installed_branch/{self.appid}", "public", type=str)
        installed_bid = self.settings.value(f"installed_buildid/{self.appid}/{sel_branch}", str(self.game_data.get("buildid") or ""), type=str)

        # Update Build ID display with colour coding:
        #   green  = installed/current (local zip matches)
        #   blue   = available on Steam but not cached locally
        if hasattr(self, "build_val_lbl"):
            manifests_dir = get_base_path() / "hubcap_manifests"
            if sel_branch != "public":
                local_zip = manifests_dir / f"accela_fetch_{self.appid}_branch_{sel_branch}.zip"
            else:
                local_zip = manifests_dir / f"accela_fetch_{self.appid}.zip"
            is_cached = local_zip.exists()

            if branch_bid:
                if installed_branch == sel_branch and installed_bid == branch_bid:
                    self.build_val_lbl.setText(f"Build {branch_bid} (installed)")
                    self.build_val_lbl.setStyleSheet("color: #46b464; font-size: 9.5pt; font-weight: bold;")
                elif is_cached:
                    self.build_val_lbl.setText(f"Build {branch_bid} (cached)")
                    self.build_val_lbl.setStyleSheet("color: #46b464; font-size: 9.5pt; font-weight: bold;")
                else:
                    self.build_val_lbl.setText(f"Build {branch_bid} (steam)")
                    self.build_val_lbl.setStyleSheet("color: #7ab3ff; font-size: 9.5pt; font-weight: bold;")
            else:
                bid_text = installed_bid or "Unknown"
                self.build_val_lbl.setText(f"Build {bid_text}" if bid_text.isdigit() else bid_text)
                self.build_val_lbl.setStyleSheet(f"color: {self.accent_color}; font-size: 9.5pt; font-weight: bold;")

        # Dynamically refresh rollback combo for the selected branch
        if hasattr(self, "build_val_lbl"):
            manifests_dir = get_base_path() / "hubcap_manifests"
            if sel_branch != "public":
                local_zip = manifests_dir / f"accela_fetch_{self.appid}_branch_{sel_branch}.zip"
            else:
                local_zip = manifests_dir / f"accela_fetch_{self.appid}.zip"
            is_cached = local_zip.exists()

            if branch_bid:
                if installed_branch == sel_branch and installed_bid == branch_bid:
                    self.build_val_lbl.setText(f"Build {branch_bid} (installed)")
                    self.build_val_lbl.setStyleSheet("color: #46b464; font-size: 9.5pt; font-weight: bold;")
                elif is_cached:
                    self.build_val_lbl.setText(f"Build {branch_bid} (cached)")
                    self.build_val_lbl.setStyleSheet("color: #46b464; font-size: 9.5pt; font-weight: bold;")
                else:
                    self.build_val_lbl.setText(f"Build {branch_bid} (steam)")
                    self.build_val_lbl.setStyleSheet("color: #7ab3ff; font-size: 9.5pt; font-weight: bold;")
            else:
                bid_text = installed_bid or "Unknown"
                self.build_val_lbl.setText(f"Build {bid_text}" if bid_text.isdigit() else bid_text)
                self.build_val_lbl.setStyleSheet(f"color: {self.accent_color}; font-size: 9.5pt; font-weight: bold;")

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

        if pinned and has_cache and not is_missing_manifest_or_lua:
            self.validate_btn.setText("Verify Pinned Build")
            self.validate_btn.setStyleSheet(f"background: {success_hex}; color: #000000; font-weight: bold; border: none;")
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
            self.validate_btn.setStyleSheet(f"background: {accent_hex}; color: #000000; font-weight: bold; border: none;")
        elif is_missing_manifest_or_lua:
            self.validate_btn.setText("Refetch")
            self.validate_btn.setStyleSheet(f"background: {accent_hex}; color: #000000; font-weight: bold; border: none;")
        elif self.game_data.get("update_status") == "update_available":
            self.validate_btn.setText("Download Update")
            self.validate_btn.setStyleSheet(f"background: {accent_hex}; color: #000000; font-weight: bold; border: none;")
        else:
            self.validate_btn.setText("Verify Files")
            self.validate_btn.setStyleSheet(f"background: {success_hex}; color: #000000; font-weight: bold; border: none;")

    def _on_validate_btn_clicked(self):
        sel_branch = self.branch_combo.currentData() or "public" if hasattr(self, "branch_combo") else "public"
        btn_text = self.validate_btn.text()

        if btn_text == "Refetch":
            self.validate_btn.set_loading(True)
            self.validate_btn.setEnabled(False)
            self.validate_btn.setToolTip("Refetching manifest zip in progress...")
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

    def _toggle_manual_section(self):
        self.manual_expanded = not self.manual_expanded
        self.manual_container.setVisible(self.manual_expanded)
        if self.manual_expanded:
            self.manual_expand_btn.setText("▼  Manual Build Download (Advanced)")
        else:
            self.manual_expand_btn.setText("▶  Manual Build Download (Advanced)")

    def _update_manual_download_btn_state(self):
        depot = self.manual_depot_combo.currentData()
        build = self.manual_build_input.text().strip()
        manifest = self.manual_manifest_input.text().strip()
        
        is_valid = bool(depot) and bool(build) and bool(manifest) and build.isdigit() and manifest.isdigit()
        self.manual_download_btn.setEnabled(is_valid)
        if is_valid:
            self.manual_download_btn.setStyleSheet(f"background: {self.accent_color}; color: #FFFFFF; font-weight: bold; border: none; padding: 2px 10px;")
        else:
            self.manual_download_btn.setStyleSheet("background: rgba(255, 255, 255, 0.05); color: rgba(255, 255, 255, 0.2); font-weight: bold; border: none; padding: 2px 10px;")

    def _on_manual_download_clicked(self):
        depot_id = self.manual_depot_combo.currentData()
        if not depot_id:
            QMessageBox.warning(self, "No Depot Selected", "Please select a valid depot.")
            return

        build_id = self.manual_build_input.text().strip()
        manifest_id = self.manual_manifest_input.text().strip()

        logger.info(f"[DEBUG_DEV] Manual download clicked. AppID: {self.appid}, Depot: {depot_id}, Build: {build_id}, Manifest: {manifest_id}")

        if not build_id or not manifest_id:
            QMessageBox.warning(self, "Missing Fields", "Please specify both Build ID and Manifest ID.")
            return

        if not build_id.isdigit() or not manifest_id.isdigit():
            QMessageBox.warning(self, "Invalid Inputs", "Build ID and Manifest ID must be numeric digits only.")
            return

        # Disable button to prevent double-clicking/multiple submission events
        self.manual_download_btn.setEnabled(False)
        self.manual_download_btn.setStyleSheet(
            "background: rgba(255, 255, 255, 0.05); color: rgba(255, 255, 255, 0.2); "
            "font-weight: bold; border: none; padding: 2px 10px;"
        )

        from utils.helpers import get_base_path
        manifest_filename = f"{depot_id}_{manifest_id}.manifest"
        global_manifests_dir = get_base_path() / "manifests"
        src_manifest_path = global_manifests_dir / manifest_filename

        if src_manifest_path.exists():
            logger.info(f"[DEBUG_DEV] Manifest already exists locally at {src_manifest_path}. Proceeding directly.")
            self._do_package_and_submit_manual_job(src_manifest_path, manifest_filename, depot_id, build_id, manifest_id)
            return


        # Manifest does not exist locally. Try to fetch from Hubcap /generate/manifest endpoint.
        logger.info(f"[DEBUG_DEV] Manifest missing locally. Attempting to download from Hubcap...")
        
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
                Q_ARG(str, depot_id),
                Q_ARG(str, build_id),
                Q_ARG(str, manifest_id),
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
            # Re-enable the download button so the user can correct inputs and try again
            self.manual_download_btn.setEnabled(True)
            self._update_manual_download_btn_state()

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
        self._do_package_and_submit_manual_job(src_manifest_path, manifest_filename, depot_id, build_id, manifest_id)

    def _do_package_and_submit_manual_job(self, src_manifest_path, manifest_filename, depot_id, build_id, manifest_id):
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
            
            # Enable Pin Build by default for manual download
            if self.settings:
                self.settings.setValue(f"pin_build/{self.appid}", True)
                self.settings.setValue(f"exclude_from_update_all/{self.appid}", False)
                self.settings.setValue(f"installed_buildid/{self.appid}", build_id)
            if hasattr(self, "pref4_toggle") and self.pref4_toggle:
                self.pref4_toggle.setChecked(True)
            if hasattr(self, "pref2_toggle") and self.pref2_toggle:
                self.pref2_toggle.setChecked(False)
                self.pref2_toggle.setEnabled(False)
        except Exception as e:
            logger.error(f"[DEBUG_DEV] Failed to create temporary manifest zip: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to package manifest file: {e}")
            self.manual_download_btn.setEnabled(True)
            self._update_manual_download_btn_state()
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

        # Re-enable button after successful packaging and submission
        self.manual_download_btn.setEnabled(True)
        self._update_manual_download_btn_state()




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
                QPushButton { background: rgba(160,40,30,60); color: #ff8a7a;
                    border: none; font-size: 9.5pt; font-weight: bold; }
                QPushButton:hover { background: rgba(180,50,35,80); }
            """)
            confirm.clicked.connect(self._do_dlc_uninstall)
            self._uninstall_content.addWidget(confirm)
        else:
            warn = QLabel(
                f"Permanently removes all files for '{self.game_data.get('game_name','this game')}'.")
            warn.setStyleSheet("color: #ff8a7a; font-size: 9.5pt; background: transparent;")
            warn.setWordWrap(True)
            self._uninstall_content.addWidget(warn)
            self._uninstall_opts = {}
            if platform.system() == "Linux":
                for key, text in [("compat", "Remove Proton/Wine prefix"),
                                   ("saves", "Remove local cloud saves"),
                                   ("wipe_sls", "Wipe SLS (you own the game) — removes from config + .DepotDownloader")]:
                    cb = QCheckBox(text)
                    cb.setStyleSheet("color: #ffd0c8; font-size: 9.5pt; background: transparent;")
                    self._uninstall_opts[key] = cb
                    self._uninstall_content.addWidget(cb)
            confirm = QPushButton("Confirm Uninstall")
            confirm.setFixedHeight(25)
            confirm.setStyleSheet("""
                QPushButton { background: rgba(160,40,30,60); color: #ff8a7a;
                    border: none; font-size: 9.5pt; font-weight: bold; }
                QPushButton:hover { background: rgba(180,50,35,80); }
            """)
            confirm.clicked.connect(
                lambda: self.parent_window._uninstall_game(
                    self.game_data, self, {
                        key: cb.isChecked() for key, cb in getattr(self, "_uninstall_opts", {}).items()
                    }))
            self._uninstall_content.addWidget(confirm)

    def _toggle_uninstall_panel(self):
        self._uninstall_expanded = not self._uninstall_expanded
        self._uninstall_panel.setVisible(self._uninstall_expanded)
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
        if self.settings:
            self.settings.setValue(f"dlc_only_mode/{self.appid}", state)
            try:
                from utils.paths import get_user_config_path
                from utils.dlc_helpers import sync_dlc_only_sls_config
                cp = get_user_config_path()
                if cp.exists():
                    sync_dlc_only_sls_config(cp, self.appid, self.game_data.get("game_name", ""))
            except Exception as e:
                logger.debug(f"DLC sync error: {e}")
        self._build_uninstall_panel()

    def _init_slsonline_logic(self):
        if is_slssteam_config_management_enabled() and self.appid not in ("0", "N/A", "unknown", "480"):
            config = get_user_config_path()
            if config.exists():
                existing = get_fake_appid(config, self.appid)
                if existing:
                    self.sls_toggle.setChecked(True)
                    self.sls_input.setText(existing)
                    self.sls_input.setEnabled(True)
                else:
                    self.sls_toggle.setChecked(False)
                    self.sls_input.setText("480")
                    self.sls_input.setEnabled(False)

                def _tog(checked):
                    self.sls_input.setEnabled(checked)
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
                    if self.sls_toggle.isChecked():
                        fid = self.sls_input.text().strip() or "480"
                        name = self.game_data.get("game_name", "Unknown")
                        cur = get_fake_appid(config, self.appid)
                        if cur != fid:
                            if cur:
                                remove_fake_app_id(config, self.appid, cur)
                            add_fake_app_id(config, self.appid, name, fid)

                self.sls_toggle.stateChanged.connect(_tog)
                self.sls_input.editingFinished.connect(_fin)
        else:
            self.sls_toggle.setEnabled(False)
            self.sls_input.setEnabled(False)

    # ──────────────────────────────────────────
    def _on_status_btn_clicked(self):
        if self.parent_window.game_manager:
            self.status_btn.setEnabled(False)
            self._update_status_ui("checking")
            self._load_branches_async(force_refresh=True)
            self.parent_window.game_manager.check_single_game_update(self.appid)

    def _update_status_ui(self, status):
        ac = self.accent_color
        last_chk = self._get_last_checked()
        time_suffix = f" ({last_chk})" if last_chk != "Never" else ""

        if status == "update_available":
            hubcap_needs_update = self.game_data.get("hubcap_needs_update", False)
            hubcap_update_in_progress = self.game_data.get("hubcap_update_in_progress", False)
            
            if hubcap_needs_update or hubcap_update_in_progress:
                reason = "HUBCAP UPDATING" if hubcap_update_in_progress else "HUBCAP NOT READY"
                self.status_btn.setText(f"⚠  UPDATE AVAILABLE ({reason}){time_suffix}  —  click to check")
                self.status_btn.setStyleSheet("""
                    QPushButton { background: rgba(180, 110, 30, 110); color: #ffe699;
                        border: none; border-radius: 4px;
                        font-weight: bold; font-size: 8.5pt; }
                    QPushButton:hover { background: rgba(180, 110, 30, 150); }
                """)
            else:
                self.status_btn.setText(f"★  UPDATE AVAILABLE{time_suffix}  —  click to check")
                self.status_btn.setStyleSheet("""
                    QPushButton { background: rgba(180, 110, 30, 110); color: #ffe699;
                        border: none; border-radius: 4px;
                        font-weight: bold; font-size: 8.5pt; }
                    QPushButton:hover { background: rgba(180, 110, 30, 150); }
                """)
            self.status_btn.setEnabled(True)
        elif status == "up_to_date":
            self.status_btn.setText(f"✓  UP TO DATE{time_suffix}  —  click to check")
            self.status_btn.setStyleSheet("""
                QPushButton { background: rgba(36, 140, 70, 210); color: #ffffff;
                    border: 1px solid rgba(46, 180, 90, 0.9); border-radius: 4px;
                    font-weight: bold; font-size: 8.5pt; }
                QPushButton:hover { background: rgba(46, 180, 90, 240); color: #ffffff; }
            """)
            self.status_btn.setEnabled(True)
        elif status == "checking":
            self.status_btn.setText("⟳  CHECKING FOR UPDATES...")
            self.status_btn.setStyleSheet("""
                QPushButton { background: rgba(20,40,80,100); color: #7ab3ff;
                    border: none; border-radius: 4px;
                    font-weight: bold; font-size: 8.5pt; }
            """)
            self.status_btn.setEnabled(False)
        else:
            self.status_btn.setText("?  STATUS UNKNOWN  —  click to check")
            self.status_btn.setStyleSheet("""
                QPushButton { background: rgba(255,255,255,12); color: rgba(255,255,255,75);
                    border: none; border-radius: 4px;
                    font-weight: bold; font-size: 8.5pt; }
                QPushButton:hover { background: rgba(255,255,255,20); }
            """)
            self.status_btn.setEnabled(True)

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
    #  TAB 2 — Tools (Clean Two-Column Grid Setup)
    # ──────────────────────────────────────────
    def _init_tools_tab(self):
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        path = self.game_data.get("install_path")
        name = self.game_data.get("game_name")
        ac = self.accent_color

        def _btn(text, tooltip=None, width=246):
            btn = self._card_btn(text, tooltip)
            btn.setFixedWidth(width)
            return btn

        def _row_label(text):
            lbl = QLabel(text)
            lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9.5pt;")
            return lbl

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 0)

        row_idx = 0

        # Section 1: DRM Removal
        grid.addWidget(self._section_title("DRM & Emulation"), row_idx, 0, 1, 2)
        row_idx += 1

        b_steamless = _btn("Run Steamless")
        b_steamless.clicked.connect(
            lambda: self.parent_window.main_window.task_manager.run_steamless_for_game(path, name))
        grid.addWidget(_row_label("Steamless DRM Remover"), row_idx, 0)
        grid.addWidget(b_steamless, row_idx, 1, Qt.AlignmentFlag.AlignRight)
        row_idx += 1

        b_aio = _btn("Run Steamless-AIO")
        b_aio.clicked.connect(
            lambda: self.parent_window.main_window.task_manager.run_steamless_aio_for_game(path, name))
        grid.addWidget(_row_label("Steamless All-In-One"), row_idx, 0)
        grid.addWidget(b_aio, row_idx, 1, Qt.AlignmentFlag.AlignRight)
        row_idx += 1

        # Goldberg buttons side by side in the second column
        self.gb_apply_btn = _btn("Apply Goldberg", width=120)
        self.gb_remove_btn = _btn("Remove Goldberg", width=120)
        self.parent_window.goldberg_check_complete.connect(self._on_goldberg_check_complete)
        self.finished.connect(
            lambda: self.parent_window.goldberg_check_complete.disconnect(
                self._on_goldberg_check_complete)
            if hasattr(self.parent_window, "goldberg_check_complete") else None)
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

        gb_container = QWidget()
        gb_container.setFixedWidth(246)
        gb_container.setStyleSheet("background: transparent;")
        gb_row = QHBoxLayout(gb_container)
        gb_row.setContentsMargins(0, 0, 0, 0)
        gb_row.setSpacing(6)
        gb_row.addWidget(self.gb_apply_btn)
        gb_row.addWidget(self.gb_remove_btn)

        grid.addWidget(_row_label("Goldberg Steam Emulator"), row_idx, 0)
        grid.addWidget(gb_container, row_idx, 1, Qt.AlignmentFlag.AlignRight)
        row_idx += 1

        # Divider
        grid.addWidget(self._thin_line(), row_idx, 0, 1, 2)
        row_idx += 1

        # Section 2: Depot selection & ACF fixing
        grid.addWidget(self._section_title("Depots & Installation"), row_idx, 0, 1, 2)
        row_idx += 1

        self.depot_status_lbl = QLabel()
        self.depot_status_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.75); font-size: 9.5pt;")
        self._update_depot_label()

        choose_btn = _btn("Choose...", width=120)
        choose_btn.clicked.connect(self._configure_depots_wrapper)
        reset_btn = _btn("Reset", width=120)
        reset_btn.clicked.connect(self._reset_depots_wrapper)

        depot_container = QWidget()
        depot_container.setFixedWidth(246)
        depot_container.setStyleSheet("background: transparent;")
        depot_row = QHBoxLayout(depot_container)
        depot_row.setContentsMargins(0, 0, 0, 0)
        depot_row.setSpacing(6)
        depot_row.addWidget(choose_btn)
        depot_row.addWidget(reset_btn)

        grid.addWidget(self.depot_status_lbl, row_idx, 0)
        grid.addWidget(depot_container, row_idx, 1, Qt.AlignmentFlag.AlignRight)
        row_idx += 1

        fix_btn = _btn("Fix Installation")
        fix_btn.setToolTip("Removes local manifest (.acf) to force Steam verification.")
        fix_btn.clicked.connect(lambda: self.parent_window._fix_game_install(self.game_data))
        grid.addWidget(_row_label("Fix Installation State"), row_idx, 0)
        grid.addWidget(fix_btn, row_idx, 1, Qt.AlignmentFlag.AlignRight)
        row_idx += 1

        # Divider
        grid.addWidget(self._thin_line(), row_idx, 0, 1, 2)
        row_idx += 1

        # Section 3: Clipboard & Web Links
        grid.addWidget(self._section_title("Utility & Store Links"), row_idx, 0, 1, 2)
        row_idx += 1

        if self.appid not in ("0", "N/A", "unknown"):
            steam_btn = _btn("Open Store")
            steam_btn.clicked.connect(lambda: QDesktopServices.openUrl(
                QUrl(f"https://store.steampowered.com/app/{self.appid}/")))
            grid.addWidget(_row_label("Steam Store Page"), row_idx, 0)
            grid.addWidget(steam_btn, row_idx, 1, Qt.AlignmentFlag.AlignRight)
            row_idx += 1

            steamdb_btn = _btn("Open SteamDB")
            steamdb_btn.clicked.connect(lambda: QDesktopServices.openUrl(
                QUrl(f"https://www.steamdb.info/app/{self.appid}/")))
            grid.addWidget(_row_label("Steam Database"), row_idx, 0)
            grid.addWidget(steamdb_btn, row_idx, 1, Qt.AlignmentFlag.AlignRight)
            row_idx += 1

        copy_appid = _btn("Copy ID")
        copy_appid.clicked.connect(lambda: QApplication.clipboard().setText(self.appid))
        grid.addWidget(_row_label("Game Application ID"), row_idx, 0)
        grid.addWidget(copy_appid, row_idx, 1, Qt.AlignmentFlag.AlignRight)
        row_idx += 1

        copy_path = _btn("Copy Path")
        copy_path.clicked.connect(lambda: QApplication.clipboard().setText(
            str(self.game_data.get("install_path", ""))))
        grid.addWidget(_row_label("Install Folder Location"), row_idx, 0)
        grid.addWidget(copy_path, row_idx, 1, Qt.AlignmentFlag.AlignRight)
        row_idx += 1

        lay.addWidget(grid_widget)
        lay.addStretch()

        self.stacked.addWidget(scroll)

    # ──────────────────────────────────────────
    def _on_goldberg_check_complete(self, is_applied):
        if hasattr(self, "gb_apply_btn") and hasattr(self, "gb_remove_btn"):
            if is_applied:
                self.gb_apply_btn.setEnabled(False)
                self.gb_remove_btn.setEnabled(True)
                self.gb_remove_btn.setStyleSheet(f"background: rgba(160,30,20,30); color: #ff8a7a;")
                self.gb_apply_btn.setStyleSheet("")
            else:
                self.gb_apply_btn.setEnabled(True)
                self.gb_remove_btn.setEnabled(False)
                self.gb_apply_btn.setStyleSheet(f"background: rgba(255, 255, 255, 0.12); color: {self.accent_color};")
                self.gb_remove_btn.setStyleSheet("")

    def _update_depot_label(self):
        if self.settings:
            val = self.settings.value(f"depot_selection/{self.appid}", "", type=str)
            if val:
                try:
                    import json
                    data = json.loads(val)
                    sel = data.get("selected", [])
                    tot = len(data.get("all_available", []))
                    self.depot_status_lbl.setText(f"{len(sel)} of {tot} depots selected")
                    return
                except Exception:
                    pass
        self.depot_status_lbl.setText("All depots selected (default)")

    def _configure_depots_wrapper(self):
        self.parent_window._configure_depots(self.game_data)
        self._update_depot_label()

    def _reset_depots_wrapper(self):
        self.parent_window._reset_depot_selection(self.game_data)
        self._update_depot_label()

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
        if self.settings:
            self.settings.setValue(f"pin_build/{self.appid}", pinned)
        
        if pinned:
            # Force exclude_from_update_all to False and grey it out
            self.pref2_toggle.setChecked(False)
            self.pref2_toggle.setEnabled(False)
            if self.settings:
                self.settings.setValue(f"exclude_from_update_all/{self.appid}", False)
            
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
            self.pref2_toggle.setEnabled(True)

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
                "platinum": ("PLATINUM", "#0d47a1", "#b3e5fc"),
                "gold":     ("GOLD",     "#5d4037", "#ffd54f"),
                "silver":   ("SILVER",   "#263238", "#cfd8dc"),
                "bronze":   ("BRONZE",   "#4e342e", "#ffab91"),
                "borked":   ("BORKED",   "#ffffff", "#ef5350"),
                "native":   ("NATIVE",   "#1b5e20", "#a5d6a7"),
            }
            if tier and tier in _tier_map:
                p_text, p_color, p_bg = _tier_map[tier]
                self._proton_badge_lbl.setText(p_text)
                self._proton_badge_lbl.setStyleSheet(
                    f"color: {p_color}; background-color: {p_bg}; "
                    f"border-radius: 3px; padding: 2px 8px; "
                    f"font-size: 8.5pt; font-weight: bold; border: none;"
                )
                self._proton_badge_lbl.show()
            else:
                self._proton_badge_lbl.hide()



