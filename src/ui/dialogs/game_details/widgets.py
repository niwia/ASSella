"""
Custom UI widgets for GameDetailsDialogV2.
"""

from PyQt6.QtCore import Qt, QSize, QPropertyAnimation, pyqtProperty, pyqtSignal, QEvent
from PyQt6.QtGui import QColor, QPainter, QLinearGradient, QPalette
from PyQt6.QtWidgets import (
    QWidget,
    QPushButton,
    QComboBox,
    QLabel,
    QHBoxLayout,
    QVBoxLayout,
    QStylePainter,
    QStyleOptionComboBox,
    QStyle,
    QStyledItemDelegate,
)


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
        self._is_active = checked
        self._current_text_color = text_color

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
                if getattr(self, "_is_active", False) and hasattr(self, "_current_text_color"):
                    tc = self._current_text_color
                    self.title_lbl.setStyleSheet(f"font-weight: bold; font-size: 8.5pt; color: {tc}; background: transparent;")
                    self.sub_lbl.setStyleSheet(f"font-size: 7.5pt; font-style: italic; color: {tc}; opacity: 0.85; background: transparent;")
                else:
                    self.title_lbl.setStyleSheet("font-weight: bold; font-size: 8.5pt; color: #FFFFFF; background: transparent;")
                    self.sub_lbl.setStyleSheet("font-size: 7.5pt; font-style: italic; color: rgba(255, 255, 255, 0.6); background: transparent;")
