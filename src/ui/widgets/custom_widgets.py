import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class Material3Switch(QWidget):
    """
    A 100% specification-compliant Material 3 Switch component.
    Features smooth thumb sliding animation, morphing thumb diameter, and checkmark icon.
    """

    toggled = pyqtSignal(bool)
    stateChanged = pyqtSignal(int)

    def __init__(
        self, checked: bool = False, accent_color: str = "#7ab3ff", parent=None
    ):
        super().__init__(parent)
        self.setFixedSize(46, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = checked
        self._locked = False
        self.accent_color = QColor(accent_color)
        self._thumb_pos = 1.0 if checked else 0.0

        self._anim = QPropertyAnimation(self, b"thumb_pos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_accent_color(self, hex_color: str):
        self.accent_color = QColor(hex_color)
        self.update()

    def setLocked(self, locked: bool):
        if self._locked != locked:
            self._locked = locked
            if locked:
                self.setCursor(Qt.CursorShape.ForbiddenCursor)
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.update()

    def isLocked(self) -> bool:
        return self._locked

    def get_thumb_pos(self) -> float:
        return self._thumb_pos

    def set_thumb_pos(self, pos: float):
        self._thumb_pos = pos
        self.update()

    thumb_pos = pyqtProperty(float, get_thumb_pos, set_thumb_pos)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        if self._checked != checked:
            self._checked = checked
            self._anim.stop()
            self._anim.setStartValue(self._thumb_pos)
            self._anim.setEndValue(1.0 if checked else 0.0)
            self._anim.start()
            self.toggled.emit(checked)
            self.stateChanged.emit(2 if checked else 0)

    def mousePressEvent(self, event):
        if self._locked:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        W = 44.0
        H = 24.0
        x_off = (self.width() - W) / 2.0
        y_off = (self.height() - H) / 2.0
        rect = QRectF(x_off, y_off, W, H)

        t = self._thumb_pos  # 0.0 (off) to 1.0 (on)

        # 1. Base Track & Thumb Colors
        if t > 0.01:
            track_bg = QColor(
                int(0x1E * (1 - t) + self.accent_color.red() * t),
                int(0x1E * (1 - t) + self.accent_color.green() * t),
                int(0x1E * (1 - t) + self.accent_color.blue() * t),
                int(100 * (1 - t) + 255 * t),
            )
        else:
            track_bg = QColor(0x1E, 0x1E, 0x22, 200)

        if t > 0.5:
            from utils.color_utils import get_best_foreground_color

            fg_hex = get_best_foreground_color(self.accent_color.name())
            thumb_color = QColor(fg_hex)
        else:
            thumb_color = QColor(180, 184, 196)

        check_icon_color = self.accent_color

        # If locked, run track, thumb, and checkmark colors through get_grayscale_color (saturation = 0, full opacity)
        if self._locked:
            from utils.color_utils import get_grayscale_color

            track_bg = QColor(get_grayscale_color(track_bg.name()))
            thumb_color = QColor(get_grayscale_color(thumb_color.name()))
            check_icon_color = QColor(
                get_grayscale_color(self.accent_color.name())
            )

        # 2. Paint Track
        painter.setBrush(QBrush(track_bg))
        if t > 0.01 and t < 0.99:
            border_col = QColor(142, 144, 153, int(255 * (1 - t)))
            painter.setPen(QPen(border_col, 2))
        elif t <= 0.01:
            painter.setPen(QPen(QColor(142, 144, 153), 2))
        else:
            painter.setPen(Qt.PenStyle.NoPen)

        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)
        painter.drawPath(path)

        # 3. Paint Thumb Circle (interpolates position & size)
        thumb_diameter = 12.0 + 6.0 * t
        thumb_x = (x_off + 6.0) + (20.0 - 6.0) * t
        thumb_y = y_off + (H - thumb_diameter) / 2.0

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(thumb_color))
        painter.drawEllipse(
            QRectF(thumb_x, thumb_y, thumb_diameter, thumb_diameter)
        )

        # 4. Checkmark Icon inside thumb when checked (t > 0.8)
        if t > 0.8:
            check_pen = QPen(check_icon_color, 2)
            check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(check_pen)

            tc_x = thumb_x + thumb_diameter / 2.0
            tc_y = thumb_y + thumb_diameter / 2.0
            check_path = QPainterPath()
            check_path.moveTo(tc_x - 3.5, tc_y)
            check_path.lineTo(tc_x - 1, tc_y + 2.5)
            check_path.lineTo(tc_x + 3.5, tc_y - 2.5)
            painter.drawPath(check_path)


class CheckboxSetting(QWidget):
    """A Material 3 Switch setting row with label + description on the left and switch control on the far right."""

    stateChanged = pyqtSignal(int)
    toggled = pyqtSignal(bool)

    def __init__(
        self,
        text: str,
        setting_key: str,
        default_value: bool,
        parent_widget: Optional[QWidget] = None,
        tooltip: Optional[str] = None,
        show_description: bool = True,
    ):
        super().__init__(
            None
        )  # Pass None to super() so Qt doesn't position at top-left (0,0) before layout placement!
        self.setting_key = setting_key
        self.default_value = default_value

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(12)

        # Left Text Block (Label stacked above Description)
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)

        self.label = QLabel(text)
        self.label.setStyleSheet(
            "color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;"
        )
        self.label.setWordWrap(True)
        text_box.addWidget(self.label)

        ac = "#7ab3ff"
        if parent_widget and hasattr(parent_widget, "accent_color"):
            ac = parent_widget.accent_color

        self.checkbox = Material3Switch(checked=False, accent_color=ac)

        # Initialize checked state from settings when parent_widget provided
        if parent_widget and hasattr(parent_widget, "settings"):
            current_value = parent_widget.settings.value(
                setting_key, default_value, type=bool
            )
            self.checkbox.setChecked(current_value)

        self.checkbox.stateChanged.connect(self.stateChanged.emit)
        self.checkbox.toggled.connect(self.toggled.emit)

        self._original_tooltip = tooltip
        self.desc_label = None
        if tooltip:
            if show_description:
                self.desc_label = QLabel(tooltip)
                self.desc_label.setStyleSheet(
                    "color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;"
                )
                self.desc_label.setWordWrap(True)
                text_box.addWidget(self.desc_label)
            self.setToolTip(tooltip)

        layout.addLayout(text_box, 1)
        layout.addWidget(self.checkbox)

    def isChecked(self) -> bool:
        return self.checkbox.isChecked()

    def setChecked(self, value: bool) -> None:
        self.checkbox.setChecked(value)

    def setLocked(self, locked: bool, lock_reason: Optional[str] = None):
        self._locked = locked
        self.checkbox.setLocked(locked)
        if locked:
            self.label.setStyleSheet(
                "color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;"
            )
            if lock_reason:
                self.setToolTip(lock_reason)
                if self.desc_label:
                    self.desc_label.setText(lock_reason)
                    self.desc_label.setStyleSheet(
                        "color: rgba(255, 255, 255, 0.7); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;"
                    )
        else:
            self.label.setStyleSheet(
                "color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;"
            )
            if hasattr(self, "_original_tooltip") and self._original_tooltip:
                self.setToolTip(self._original_tooltip)
                if self.desc_label:
                    self.desc_label.setText(self._original_tooltip)
                    self.desc_label.setStyleSheet(
                        "color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;"
                    )

    def isLocked(self) -> bool:
        return getattr(self, "_locked", False)

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self.checkbox.setEnabled(enabled)
        if enabled:
            self.label.setStyleSheet(
                "color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;"
            )
            if self.desc_label:
                self.desc_label.setStyleSheet(
                    "color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;"
                )
            self.checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.label.setStyleSheet(
                "color: rgba(255, 255, 255, 0.38); font-size: 9.5pt; font-weight: 500; border: none; background: transparent;"
            )
            if self.desc_label:
                self.desc_label.setStyleSheet(
                    "color: rgba(255, 255, 255, 0.25); font-size: 8.5pt; font-weight: 400; border: none; background: transparent;"
                )
            self.checkbox.setCursor(Qt.CursorShape.ForbiddenCursor)

    def setToolTip(self, text: Optional[str]):
        super().setToolTip(text or "")
        self.label.setToolTip(text or "")
        self.checkbox.setToolTip(text or "")
        if self.desc_label and text:
            self.desc_label.setText(text)


class FontSelectionDialog(QDialog):
    def __init__(self, initial_font: QFont, parent=None):
        super().__init__(parent)
        try:
            from ui.theme import register_application_fonts

            register_application_fonts()
        except Exception as e:
            logger.debug("[FontSelectionDialog] Could not register application fonts: %s", e)

        from ui.dialogs.themed_dialog import install_themed_frame

        self.setWindowTitle("Select Font")
        self.setMinimumWidth(400)
        self.resize(400, 290)

        self.setStyleSheet("""
            QLabel[form_label="true"] {
                color: #FFFFFF;
                font-size: 9.5pt;
            }
            QSpinBox {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 6px;
                color: #FFFFFF;
                padding: 5px 8px;
                font-size: 9.5pt;
            }
        """)

        layout = install_themed_frame(
            self, margins=(16, 16, 16, 16), spacing=14
        )

        form_layout = QFormLayout()
        form_layout.setSpacing(10)

        lbl_f = QLabel("Font:", self)
        lbl_f.setProperty("form_label", True)
        from ui.widgets.modern_dropdown import (
            ModernComboBox,
            ModernFontComboBox,
        )

        self.font_combo = ModernFontComboBox(self)
        if initial_font and initial_font.family():
            if initial_font.family().lower() in (
                "trixiecyrg-plain",
                "trixiecyrg-plain regular",
                "trixie",
            ):
                initial_font = QFont("Open Sans", initial_font.pointSize())
            self.font_combo.setCurrentFont(initial_font)
        form_layout.addRow(lbl_f, self.font_combo)

        lbl_s = QLabel("Style:", self)
        lbl_s.setProperty("form_label", True)
        self.style_combo = ModernComboBox(self)
        self.style_combo.addItems(["Normal", "Bold", "Italic", "Bold Italic"])

        if initial_font.bold() and initial_font.italic():
            self.style_combo.setCurrentIndex(3)
        elif initial_font.bold():
            self.style_combo.setCurrentIndex(1)
        elif initial_font.italic():
            self.style_combo.setCurrentIndex(2)
        else:
            self.style_combo.setCurrentIndex(0)

        form_layout.addRow(lbl_s, self.style_combo)

        lbl_sz = QLabel("Size (pt):", self)
        lbl_sz.setProperty("form_label", True)
        self.size_spin = QSpinBox(self)
        self.size_spin.setRange(6, 72)
        self.size_spin.setValue(
            initial_font.pointSize() if initial_font.pointSize() > 0 else 10
        )
        form_layout.addRow(lbl_sz, self.size_spin)

        layout.addLayout(form_layout)

        preview_group = QVBoxLayout()
        preview_title = QLabel("Preview:", self)
        preview_title.setStyleSheet(
            "color: rgba(255, 255, 255, 0.5); font-size: 8.5pt;"
        )
        self.preview_label = QLabel(
            "Pack my box with five dozen liquor jugs. 1234567890", self
        )
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setWordWrap(True)

        preview_group.addWidget(preview_title)
        preview_group.addWidget(self.preview_label)
        layout.addLayout(preview_group)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.button_box.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.09);
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 6px;
                color: #FFFFFF;
                padding: 6px 16px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.18);
            }
        """)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self.font_combo.currentFontChanged.connect(self.update_preview)
        self.style_combo.currentIndexChanged.connect(self.update_preview)
        self.size_spin.valueChanged.connect(self.update_preview)

        self.update_preview()

    def update_preview(self):
        font = self.get_selected_font()
        self.preview_label.setFont(font)
        weight = "bold" if font.bold() else "normal"
        style = "italic" if font.italic() else "normal"
        self.preview_label.setStyleSheet(
            f"background-color: rgba(255, 255, 255, 0.04); "
            f"border: 1px dashed rgba(255, 255, 255, 0.15); "
            f"border-radius: 6px; padding: 12px; min-height: 48px; color: #FFFFFF; "
            f"font-family: '{font.family()}'; font-size: {font.pointSize()}pt; "
            f"font-weight: {weight}; font-style: {style};"
        )

    def get_selected_font(self) -> QFont:
        font = self.font_combo.currentFont()
        font.setPointSize(self.size_spin.value())
        style = self.style_combo.currentText()
        if style == "Bold":
            font.setBold(True)
            font.setItalic(False)
        elif style == "Italic":
            font.setBold(False)
            font.setItalic(True)
        elif style == "Bold Italic":
            font.setBold(True)
            font.setItalic(True)
        else:
            font.setBold(False)
            font.setItalic(False)
        return font

    @staticmethod
    def get_font(initial_font: QFont, parent=None) -> Tuple[QFont, bool]:
        dialog = FontSelectionDialog(initial_font, parent)
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            return dialog.get_selected_font(), True
        return initial_font, False