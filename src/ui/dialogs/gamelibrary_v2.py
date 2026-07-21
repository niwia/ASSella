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
    QGridLayout,
)

from utils.helpers import get_base_path
from utils.settings import get_settings
from utils.update_status_cache import get_update_cache
from utils.yaml_config_manager import (
    get_user_config_path, add_fake_app_id, remove_fake_app_id,
    get_fake_appid, is_slssteam_config_management_enabled,
)
from utils.image_fetcher import ImageFetcher

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
    def paintEvent(self, event):
        p = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        p.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, opt,
            QStyle.SubControl.SC_ComboBoxEditField, self)
        p.drawItemText(rect, Qt.AlignmentFlag.AlignCenter, self.palette(),
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
    def __init__(self, parent, game_data):
        super().__init__(parent)
        self.parent_window = parent
        self.game_data = game_data
        self.appid = str(game_data.get("appid", "0"))
        self.settings = get_settings()
        self._active_fetchers = {}

        self.accent_color  = getattr(parent, "accent_color",  "#C06C84")
        self.background_color = getattr(parent, "background_color", "#1a1a1e")

        self.setWindowTitle(f"{game_data.get('game_name', 'Game')} — Details")
        self.setMinimumSize(540, 420)
        self.resize(580, 480)
        self.setModal(True)

        self._apply_stylesheet()
        self._setup_ui()

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
                background-color: {bg};
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 15);
                selection-background-color: {ac};
                selection-color: #000000;
                font-size: 9.5pt;
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

        # Hero Banner
        self.hero = HeroBanner(bg_hex=self.background_color)
        self.hero.setFixedHeight(65)
        banner_layout = QHBoxLayout(self.hero)
        banner_layout.setContentsMargins(14, 6, 180, 6)
        banner_layout.setSpacing(0)

        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        self.name_lbl = QLabel(self.game_data.get("game_name", "Unknown"))
        self.name_lbl.setStyleSheet(
            "font-size: 12.5pt; font-weight: bold; color: #FFFFFF; background: transparent;")
        self.name_lbl.setWordWrap(True)
        self.appid_lbl = QLabel(f"App ID: {self.appid}")
        self.appid_lbl.setStyleSheet(
            "font-size: 8pt; color: rgba(255, 255, 255, 60); background: transparent;")
        name_col.addWidget(self.name_lbl)
        name_col.addWidget(self.appid_lbl)
        name_col.addStretch()
        banner_layout.addLayout(name_col)
        banner_layout.addStretch()

        self._load_hero_image()
        root.addWidget(self.hero)

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
    def _thin_line(self):
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet("background: rgba(255, 255, 255, 8); border: none; max-height: 1px;")
        return f

    def _section_title(self, text):
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(
            "color: rgba(255, 255, 255, 45); font-size: 8px; font-weight: bold;"
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

        # ── Status Pill / Banner ─────────────────────────────────
        self.status_btn = QPushButton()
        self.status_btn.setFixedHeight(26)
        self.status_btn.clicked.connect(self._on_status_btn_clicked)
        lay.addWidget(self.status_btn)
        lay.addSpacing(12)

        # ── Stats Grid (Bright text labels) ──────────────────────
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

        stats_grid.addWidget(size_lbl, 0, 0)
        stats_grid.addWidget(self.size_val_lbl, 0, 1)
        stats_grid.addWidget(cached_lbl, 0, 2)
        stats_grid.addWidget(self.cached_val_lbl, 0, 3)

        from utils.dlc_helpers import is_dlc_only_mode, get_dlc_only_info
        self._is_dlc = is_dlc_only_mode(self.appid)
        if self._is_dlc:
            dlc_list = get_dlc_only_info(self.appid)
            cnt = len(dlc_list)
            mode_lbl = QLabel("Mode:")
            mode_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9.5pt;")
            mode_val = QLabel(f"DLC Only ({cnt} DLC{'s' if cnt != 1 else ''})")
            mode_val.setStyleSheet("color: #7ab3ff; font-size: 9.5pt; font-weight: bold;")
            stats_grid.addWidget(mode_lbl, 1, 0)
            stats_grid.addWidget(mode_val, 1, 1)

        lay.addWidget(stats_widget)
        lay.addSpacing(10)

        # ── Open Install Folder button ───────────────────────────
        open_folder_btn = QPushButton("Open Install Folder")
        open_folder_btn.setFixedHeight(28)
        open_folder_btn.clicked.connect(
            lambda: self.parent_window._open_folder(self.game_data.get("install_path")))
        lay.addWidget(open_folder_btn)

        lay.addSpacing(12)
        lay.addWidget(self._thin_line())
        lay.addSpacing(10)

        # ── Actions (Select Build & Validate) ────────────────────
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        self.rollback_combo = CenteredComboBox()
        self.rollback_combo.addItem("Latest Build", None)
        manifests_dir = get_base_path() / "hubcap_manifests"
        self._backups = sorted(manifests_dir.glob(f"accela_fetch_{self.appid}_*.zip"), reverse=True)
        for b in self._backups:
            try:
                parts = b.stem.split("_")
                ts1, ts2 = parts[-2], parts[-1]
                if len(ts1) == 8 and len(ts2) == 6:
                    date_str = f"{ts1[:4]}-{ts1[4:6]}-{ts1[6:]}"
                    self.rollback_combo.addItem(f"Backup: {date_str}", str(b))
                else:
                    self.rollback_combo.addItem(f"Backup: {b.name}", str(b))
            except Exception:
                self.rollback_combo.addItem(f"Backup: {b.name}", str(b))
        self.rollback_combo.setFixedHeight(26)
        actions_row.addWidget(self.rollback_combo, 1)

        self.validate_btn = QPushButton()
        self.validate_btn.setFixedHeight(26)
        self.validate_btn.setStyleSheet("font-weight: bold;")
        actions_row.addWidget(self.validate_btn, 1)

        lay.addLayout(actions_row)
        lay.addSpacing(3)

        self.validate_btn.clicked.connect(
            lambda: self.parent_window._fetch_game_manifest(
                self.game_data, self,
                local_path_override=self.rollback_combo.currentData() if self._backups else None))
        if self._backups:
            self.rollback_combo.currentIndexChanged.connect(self._on_combo_changed)

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
                                   ("saves", "Remove local cloud saves")]:
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
                    self.game_data, self, getattr(self, "_uninstall_opts", {})))
            self._uninstall_content.addWidget(confirm)

    def _toggle_uninstall_panel(self):
        self._uninstall_expanded = not self._uninstall_expanded
        self._uninstall_panel.setVisible(self._uninstall_expanded)

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
                    QPushButton { background: rgba(30,80,35,110); color: #7be09d;
                        border: none; border-radius: 4px;
                        font-weight: bold; font-size: 8.5pt; }
                    QPushButton:hover { background: rgba(30,80,35,150); }
                """)
            self.status_btn.setEnabled(True)
            self.validate_btn.setText("Download Update")
            self.validate_btn.setStyleSheet(f"""
                QPushButton {{ background: {ac}; color: #000000; border: none; font-weight: bold; }}
                QPushButton:hover {{ background: #FFFFFF; color: #000000; }}
            """)
        elif status == "up_to_date":
            self.status_btn.setText(f"✓  UP TO DATE{time_suffix}  —  click to check")
            self.status_btn.setStyleSheet("""
                QPushButton { background: rgba(255,255,255,12); color: #FFFFFF;
                    border: none; border-radius: 4px;
                    font-weight: bold; font-size: 8.5pt; }
                QPushButton:hover { background: rgba(255,255,255,20); color: #FFFFFF; }
            """)
            self.status_btn.setEnabled(True)
            self.validate_btn.setText("Validate Files")
            self.validate_btn.setStyleSheet(f"""
                QPushButton {{ background: rgba(255,255,255,12); color: #FFFFFF; border: none; font-weight: bold; }}
                QPushButton:hover {{ background: rgba(255,255,255,20); color: {ac}; }}
            """)
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
            self.validate_btn.setText("Validate Files")
            self.validate_btn.setStyleSheet(f"""
                QPushButton {{ background: rgba(255,255,255,12); color: #FFFFFF; border: none; font-weight: bold; }}
                QPushButton:hover {{ background: rgba(255,255,255,20); }}
            """)

    def _on_status_changed(self, changed_appid, new_status):
        if changed_appid != self.appid:
            return
        self.game_data["update_status"] = new_status
        self._update_status_ui(new_status)
        if self.pref1_toggle.isChecked() and new_status == "update_available":
            self.parent_window._fetch_game_manifest(self.game_data, self, download_only=True)

    def _on_hubcap_status_changed(self, changed_appid, needs_update, update_in_progress):
        if changed_appid != self.appid:
            return
        self.game_data["hubcap_needs_update"] = needs_update
        self.game_data["hubcap_update_in_progress"] = update_in_progress
        self._update_status_ui(self.game_data.get("update_status"))

    def _on_combo_changed(self):
        if self.rollback_combo.currentData() is not None:
            self.validate_btn.setText("Install Selected Build")
        else:
            is_upd = self.game_data.get("update_status") == "update_available"
            self.validate_btn.setText("Download Update" if is_upd else "Validate Files")

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

        def _row_label(text):
            lbl = QLabel(text)
            lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9.5pt;")
            return lbl

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        row_idx = 0

        # Section 1: DRM Removal
        grid.addWidget(self._section_title("DRM & Emulation"), row_idx, 0, 1, 2)
        row_idx += 1

        b_steamless = self._card_btn("Run Steamless")
        b_steamless.clicked.connect(
            lambda: self.parent_window.main_window.task_manager.run_steamless_for_game(path, name))
        grid.addWidget(_row_label("Steamless DRM Remover"), row_idx, 0)
        grid.addWidget(b_steamless, row_idx, 1)
        row_idx += 1

        b_aio = self._card_btn("Run Steamless-AIO")
        b_aio.clicked.connect(
            lambda: self.parent_window.main_window.task_manager.run_steamless_aio_for_game(path, name))
        grid.addWidget(_row_label("Steamless All-In-One"), row_idx, 0)
        grid.addWidget(b_aio, row_idx, 1)
        row_idx += 1

        # Goldberg buttons side by side in the second column
        self.gb_apply_btn = self._card_btn("Apply Goldberg")
        self.gb_remove_btn = self._card_btn("Remove Goldberg")
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

        gb_row = QHBoxLayout()
        gb_row.setContentsMargins(0, 0, 0, 0)
        gb_row.setSpacing(6)
        gb_row.addWidget(self.gb_apply_btn, 1)
        gb_row.addWidget(self.gb_remove_btn, 1)

        grid.addWidget(_row_label("Goldberg Steam Emulator"), row_idx, 0)
        grid.addLayout(gb_row, row_idx, 1)
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

        choose_btn = self._card_btn("Choose...")
        choose_btn.clicked.connect(self._configure_depots_wrapper)
        reset_btn = self._card_btn("Reset")
        reset_btn.clicked.connect(self._reset_depots_wrapper)

        depot_row = QHBoxLayout()
        depot_row.setContentsMargins(0, 0, 0, 0)
        depot_row.setSpacing(6)
        depot_row.addWidget(choose_btn, 1)
        depot_row.addWidget(reset_btn)

        grid.addWidget(self.depot_status_lbl, row_idx, 0)
        grid.addLayout(depot_row, row_idx, 1)
        row_idx += 1

        fix_btn = self._card_btn("Fix Installation")
        fix_btn.setToolTip("Removes local manifest (.acf) to force Steam verification.")
        fix_btn.clicked.connect(lambda: self.parent_window._fix_game_install(self.game_data))
        grid.addWidget(_row_label("Fix Installation State"), row_idx, 0)
        grid.addWidget(fix_btn, row_idx, 1)
        row_idx += 1

        # Divider
        grid.addWidget(self._thin_line(), row_idx, 0, 1, 2)
        row_idx += 1

        # Section 3: Clipboard & Web Links
        grid.addWidget(self._section_title("Utility & Store Links"), row_idx, 0, 1, 2)
        row_idx += 1

        if self.appid not in ("0", "N/A", "unknown"):
            steam_btn = self._card_btn("Open Store")
            steam_btn.clicked.connect(lambda: QDesktopServices.openUrl(
                QUrl(f"https://store.steampowered.com/app/{self.appid}/")))
            grid.addWidget(_row_label("Steam Store Page"), row_idx, 0)
            grid.addWidget(steam_btn, row_idx, 1)
            row_idx += 1

            steamdb_btn = self._card_btn("Open SteamDB")
            steamdb_btn.clicked.connect(lambda: QDesktopServices.openUrl(
                QUrl(f"https://www.steamdb.info/app/{self.appid}/")))
            grid.addWidget(_row_label("Steam Database"), row_idx, 0)
            grid.addWidget(steamdb_btn, row_idx, 1)
            row_idx += 1

        copy_appid = self._card_btn("Copy ID")
        copy_appid.clicked.connect(lambda: QApplication.clipboard().setText(self.appid))
        grid.addWidget(_row_label("Game Application ID"), row_idx, 0)
        grid.addWidget(copy_appid, row_idx, 1)
        row_idx += 1

        copy_path = self._card_btn("Copy Path")
        copy_path.clicked.connect(lambda: QApplication.clipboard().setText(
            str(self.game_data.get("install_path", ""))))
        grid.addWidget(_row_label("Install Folder Location"), row_idx, 0)
        grid.addWidget(copy_path, row_idx, 1)
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
