"""Custom animated switches, table builders, and formatting helpers for the Depots tab."""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

logger = logging.getLogger(__name__)


def _format_enriched_depot_desc(
    depot_id: str,
    fallback_desc: str,
    enrich_data: Optional[Dict[str, Any]] = None,
    has_os_icon: bool = False,
    base_appid: Optional[str] = None,
) -> str:
    """Format depot title with OS, architecture, language, DLC badges, and clean name."""
    from utils.depot_tag_helpers import format_depot_row_label

    return format_depot_row_label(
        depot_id=str(depot_id),
        depot_data=enrich_data or {},
        base_desc=fallback_desc,
        has_os_icon=has_os_icon,
        show_os_badge=(not has_os_icon),
        base_appid=base_appid,
    )


class MiniSwitchToggle(QWidget):
    """
    Minimal sideways switch toggle widget.
    Turns Green (#4CAF50) when checked (enabled) and neutral/greyscaled when off/locked.
    Knob slides smoothly sideways with cubic easing.
    In read-only mode (locked), user interaction is disabled.
    """

    toggled = pyqtSignal(bool)

    def __init__(
        self,
        checked: bool = False,
        read_only: bool = True,
        greyscaled: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._checked = checked
        self._read_only = read_only or greyscaled
        self._greyscaled = greyscaled
        self._hovered = False
        self.setFixedSize(36, 18)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if not self._read_only
            else Qt.CursorShape.ArrowCursor
        )

        # Knob slide range: unchecked = 2.0 (left), checked = 20.0 (right)
        self._circle_pos = 20.0 if checked else 2.0
        self._anim = QPropertyAnimation(self, b"circle_pos", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    @pyqtProperty(float)
    def circle_pos(self) -> float:
        return self._circle_pos

    @circle_pos.setter
    def circle_pos(self, pos: float) -> None:
        self._circle_pos = pos
        self.update()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, animate: bool = True) -> None:
        if self._checked != checked:
            self._checked = checked
            target = 20.0 if checked else 2.0
            if animate:
                self._anim.stop()
                self._anim.setStartValue(self._circle_pos)
                self._anim.setEndValue(target)
                self._anim.start()
            else:
                self._circle_pos = target
                self.update()
            self.toggled.emit(self._checked)

    def setReadOnly(self, read_only: bool) -> None:
        self._read_only = read_only or self._greyscaled
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if not self._read_only
            else Qt.CursorShape.ArrowCursor
        )
        self.update()

    def isReadOnly(self) -> bool:
        return self._read_only

    def setGreyscaled(self, greyscaled: bool) -> None:
        self._greyscaled = greyscaled
        if greyscaled:
            self._read_only = True
        self.setCursor(
            Qt.CursorShape.ArrowCursor
            if self._read_only
            else Qt.CursorShape.PointingHandCursor
        )
        self.update()

    def isGreyscaled(self) -> bool:
        return self._greyscaled

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._read_only and not self._greyscaled:
                self.setChecked(not self._checked)
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        try:
            from utils.settings import get_settings

            s = get_settings()
            is_christmas = s.value(
                "material_preset", "ocean", type=str
            ) == "christmas" or s.value("accent_color", "", type=str).lower() in (
                "#e53935",
                "#d32f2f",
                "#ff334b",
            )
        except Exception as e:
            logger.debug(f"[MiniSwitchToggle] Theme preset lookup failed: {e}")
            is_christmas = False

        if is_christmas and not self._greyscaled:
            r = 4.0
            track_rect = QRectF(1.0, 1.0, w - 2.0, h - 2.0)
            inner_rect = QRectF(3.0, 3.0, w - 6.0, h - 6.0)

            path = QPainterPath()
            path.addRoundedRect(track_rect, r, r)
            inner_path = QPainterPath()
            inner_path.addRoundedRect(inner_rect, r - 1.0, r - 1.0)
            border_path = path.subtracted(inner_path)

            p.save()
            p.setClipPath(border_path)
            for x in range(-h, w + h, 5):
                p.fillRect(x, 0, 2, h, QColor("#FF334B"))
                p.fillRect(x + 2, 0, 3, h, QColor("#FFFFFF"))
            p.restore()

            bg_col = QColor(140, 20, 20, 170) if self._checked else QColor("#0c1017")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(bg_col))
            p.drawRoundedRect(inner_rect, r - 1.0, r - 1.0)

            if self._checked:
                check_path = QPainterPath()
                check_path.moveTo(6.0, h / 2.0)
                check_path.lineTo(9.5, h / 2.0 + 3.0)
                check_path.lineTo(14.5, h / 2.0 - 3.5)
                p.strokePath(
                    check_path,
                    QPen(
                        QColor("#FFFFFF"),
                        1.8,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin,
                    ),
                )

            knob_d = 12.0
            knob_y = (h - knob_d) / 2.0
            knob_rect = QRectF(self._circle_pos, knob_y, knob_d, knob_d)
            p.setPen(QPen(QColor("#FF334B"), 1.0))
            p.setBrush(QBrush(QColor("#FFFFFF")))
            p.drawRoundedRect(knob_rect, 3.0, 3.0)
            p.end()
            return

        radius = h / 2.0

        if self._greyscaled:
            track_color = QColor(255, 255, 255, 30)
            knob_color = QColor(135, 135, 135)
        else:
            track_color = (
                QColor("#4CAF50") if self._checked else QColor(255, 255, 255, 70)
            )
            if self._checked:
                track_color.setAlpha(190 if self._read_only else 255)
            knob_color = QColor("#FFFFFF")

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(track_color))
        p.drawRoundedRect(0, 0, w, h, radius, radius)

        knob_d = 14.0
        knob_y = (h - knob_d) / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(knob_color))
        p.drawEllipse(int(self._circle_pos), int(knob_y), int(knob_d), int(knob_d))
        p.end()


class _ToggleContainer(QWidget):
    """Container widget to center the switch toggle inside a table cell and forward clicks."""

    def __init__(self, toggle: MiniSwitchToggle, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.toggle = toggle
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(toggle)

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self.toggle.isReadOnly()
            and not self.toggle.isGreyscaled()
        ):
            self.toggle.setChecked(not self.toggle.isChecked())
            event.accept()
        else:
            super().mousePressEvent(event)


def _make_local_indicator(in_local: bool, is_missing: bool = False) -> QWidget:
    """Create a sleek, non-interactive indicator for Local DB status (never toggleable)."""
    container = QWidget()
    container.setStyleSheet("background: transparent;")
    h_lay = QHBoxLayout(container)
    h_lay.setContentsMargins(0, 0, 0, 0)
    h_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

    if in_local:
        dot = QFrame()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet("""
            QFrame {
                background-color: #4CAF50;
                border-radius: 5px;
            }
        """)
        dot.setToolTip("Decryption key present in local database")
        h_lay.addWidget(dot)
    elif is_missing:
        lbl = QLabel("—")
        lbl.setStyleSheet(
            "color: rgba(255, 255, 255, 0.25); font-weight: bold; font-size: 11px;"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setToolTip("Decryption key not available locally or on Hubcap")
        h_lay.addWidget(lbl)
    else:
        empty = QLabel("")
        h_lay.addWidget(empty)

    return container


def _make_table(columns: List[str], accent_color: str) -> QTableWidget:
    """Create a styled table widget matching ASSella design conventions."""
    tbl = QTableWidget()
    tbl.setIconSize(QSize(18, 18))
    tbl.setColumnCount(len(columns))
    for c_idx, col_name in enumerate(columns):
        item = QTableWidgetItem(col_name)
        if col_name in ("Config", "Local"):
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        tbl.setHorizontalHeaderItem(c_idx, item)

    tbl.verticalHeader().setVisible(False)
    tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    tbl.setShowGrid(False)
    tbl.setAlternatingRowColors(True)
    tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    tbl.setStyleSheet(f"""
        QTableWidget {{
            background-color: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 8px;
            gridline-color: transparent;
            outline: 0;
            color: #FFFFFF;
            font-size: 9pt;
            selection-background-color: transparent;
            selection-color: #FFFFFF;
        }}
        QTableWidget::item {{
            color: #FFFFFF;
            padding: 4px 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            border: none;
        }}
        QTableWidget::item:disabled {{
            color: rgba(255, 255, 255, 0.38);
            background-color: transparent;
        }}
        QTableWidget::item:selected {{
            background-color: rgba(255, 255, 255, 0.06);
            border: none;
            color: #FFFFFF;
        }}
        QToolTip {{
            background-color: #1e2029;
            color: #FFFFFF;
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 4px;
            padding: 5px 8px;
            font-size: 8.5pt;
        }}
        QHeaderView::section {{
            background-color: rgba(255, 255, 255, 0.04);
            color: rgba(255, 255, 255, 0.85);
            padding: 6px 10px;
            border: none;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            font-size: 8.5pt;
            font-weight: bold;
        }}
    """)
    return tbl


def _set_table_height(tbl: QTableWidget, row_count: int) -> None:
    """Set exact height to display all rows without internal scrollbar or clipping."""
    header_h = 32
    row_h = 32
    padding = 8
    total_h = header_h + (max(1, row_count) * row_h) + padding
    tbl.setFixedHeight(total_h)


def _make_bulk_toggle_bar(
    category_name: str, on_toggle_cb: Callable[[bool], None]
) -> Tuple[QFrame, MiniSwitchToggle]:
    """Create a sleek footer bar with a Select All / None switch for categories with >=5 items."""
    bar = QFrame()
    bar.setStyleSheet("background: transparent; border: none;")
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(4, 2, 4, 4)
    lay.setSpacing(8)

    lbl = QLabel(f"Select All / None ({category_name}):")
    lbl.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 8.5pt;")
    lay.addWidget(lbl)

    toggle = MiniSwitchToggle(checked=False, read_only=True)
    toggle.toggled.connect(on_toggle_cb)
    lay.addWidget(toggle)

    lay.addStretch()
    return bar, toggle