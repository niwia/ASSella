"""Table views: the checkbox delegate and the header with select-all."""

import os
from typing import TYPE_CHECKING, Optional, Tuple

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QHeaderView,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
)

from utils.settings import get_settings

if TYPE_CHECKING:
    # Type-only: dialog.py imports this module, so a runtime import here would
    # be a cycle.
    from ui.dialogs.depot_selection.dialog import DepotSelectionDialog


class DepotCheckboxDelegate(QStyledItemDelegate):
    def __init__(self, dialog: "DepotSelectionDialog"):
        super().__init__(dialog)
        self.dialog = dialog
        self._unlit_pm: Optional[QPixmap] = None
        self._lit_pm: Optional[QPixmap] = None
        self._loaded_theme: Optional[str] = None

    def _get_themed_icons(self) -> Tuple[Optional[QPixmap], Optional[QPixmap]]:
        settings = getattr(self.dialog, "settings", None) or get_settings()
        preset = settings.value("material_preset", "ocean", type=str)
        accent = getattr(self.dialog, "accent_color", "#C06C84")

        theme_key = f"{preset}_{accent}"
        if self._loaded_theme == theme_key and self._unlit_pm is not None:
            return self._unlit_pm, self._lit_pm

        self._loaded_theme = theme_key
        self._unlit_pm = None
        self._lit_pm = None

        from utils.paths import Paths
        unlit_svg = str(Paths.resource("halloween/pumpkin_unlit.svg"))
        lit_svg = str(Paths.resource("halloween/pumpkin_lit.svg"))

        custom_unlit = settings.value("theme_checkbox_unlit", "", type=str)
        custom_lit = settings.value("theme_checkbox_lit", "", type=str)

        path_unlit, path_lit = None, None
        if custom_unlit and custom_lit and os.path.exists(custom_unlit) and os.path.exists(custom_lit):
            path_unlit, path_lit = custom_unlit, custom_lit
        elif preset == "halloween" or str(accent).lower() in ("#ffb77d", "#ff7518"):
            if os.path.exists(unlit_svg) and os.path.exists(lit_svg):
                path_unlit, path_lit = unlit_svg, lit_svg

        if path_unlit and path_lit:
            try:
                from PyQt6.QtSvg import QSvgRenderer
                for p, is_lit in [(path_unlit, False), (path_lit, True)]:
                    if p.lower().endswith(".svg"):
                        rend = QSvgRenderer(p)
                        pm = QPixmap(36, 36)
                        pm.fill(Qt.GlobalColor.transparent)
                        p_painter = QPainter(pm)
                        rend.render(p_painter)
                        p_painter.end()
                    else:
                        pm = QPixmap(p)

                    if is_lit:
                        self._lit_pm = pm
                    else:
                        self._unlit_pm = pm
            except Exception:
                pass

        return self._unlit_pm, self._lit_pm

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = option.widget
        style = widget.style() if widget else QApplication.style()

        is_missing = (index.data(Qt.ItemDataRole.UserRole + 2) == "missing")
        is_checking = getattr(self.dialog, "_is_checking_missing_contents", False)

        if is_missing and is_checking:
            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, widget)
            check_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, widget)
            text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, widget)

            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            spinner_size = 14
            sx = check_rect.x() + (check_rect.width() - spinner_size) / 2.0
            sy = check_rect.y() + (check_rect.height() - spinner_size) / 2.0
            rect = QRectF(sx, sy, spinner_size, spinner_size)

            angle = getattr(self.dialog, "_spinner_angle", 0)
            cycle = (angle // 2) % 180
            span = 90 + abs(cycle - 90) * 2

            pen = QPen(QColor(135, 135, 135))
            pen.setWidth(2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, int(angle * 16), int(span * 16))
            painter.restore()

            painter.save()
            painter.setPen(QColor(135, 135, 135))
            painter.setFont(opt.font)
            style.drawItemText(painter, text_rect, opt.displayAlignment, opt.palette, True, opt.text)
            painter.restore()
        else:
            unlit_icon, lit_icon = self._get_themed_icons()
            if unlit_icon and lit_icon and (opt.features & QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator):
                style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, widget)
                check_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, widget)
                text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, widget)

                is_checked = (opt.checkState == Qt.CheckState.Checked)
                pm = lit_icon if is_checked else unlit_icon
                if pm and not pm.isNull():
                    icon_size = 18
                    ix = check_rect.x() + (check_rect.width() - icon_size) / 2.0
                    iy = check_rect.y() + (check_rect.height() - icon_size) / 2.0
                    painter.save()
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    painter.drawPixmap(QRectF(ix, iy, icon_size, icon_size).toRect(), pm)
                    painter.restore()

                painter.save()
                text_color = opt.palette.color(QPalette.ColorRole.Text) if not (opt.state & QStyle.StateFlag.State_Selected) else QColor("#FFFFFF")
                painter.setPen(text_color)
                painter.setFont(opt.font)
                style.drawItemText(painter, text_rect, opt.displayAlignment, opt.palette, True, opt.text)
                painter.restore()
            else:
                super().paint(painter, option, index)
class DepotHeaderView(QHeaderView):
    def __init__(self, parent: "DepotSelectionDialog"):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.dialog = parent
        self._check_state = Qt.CheckState.Unchecked
        self._count_str = ""
        self.setSectionsClickable(True)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def set_check_state_and_count(self, state: Qt.CheckState, count_str: str):
        if self._check_state != state or self._count_str != count_str:
            self._check_state = state
            self._count_str = count_str
            self.viewport().update()

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int):
        painter.save()
        super().paintSection(painter, rect, logical_index)
        painter.restore()

        if logical_index == 0:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            accent = getattr(self.dialog, "accent_color", "#C06C84")
            accent_color = QColor(accent)

            box_size = 15
            bx = rect.x() + 10
            by = rect.y() + (rect.height() - box_size) // 2
            box_rect = QRectF(bx, by, box_size, box_size)

            if self._check_state == Qt.CheckState.Checked:
                painter.setBrush(accent_color)
                painter.setPen(QPen(accent_color, 1.2))
                painter.drawRoundedRect(box_rect, 3.5, 3.5)

                p = QPainterPath()
                p.moveTo(bx + 3.2, by + 7.5)
                p.lineTo(bx + 6.2, by + 10.8)
                p.lineTo(bx + 11.8, by + 4.2)
                painter.strokePath(p, QPen(QColor("#FFFFFF"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            elif self._check_state == Qt.CheckState.PartiallyChecked:
                painter.setBrush(accent_color)
                painter.setPen(QPen(accent_color, 1.2))
                painter.drawRoundedRect(box_rect, 3.5, 3.5)

                painter.fillRect(QRectF(bx + 3.5, by + 6.5, 8.0, 2.0), QColor("#FFFFFF"))
            else:
                painter.setBrush(QColor(255, 255, 255, 12))
                painter.setPen(QPen(QColor(255, 255, 255, 75), 1.2))
                painter.drawRoundedRect(box_rect, 3.5, 3.5)

            if self._count_str:
                painter.setFont(self.font())
                painter.setPen(QColor("#A0AEC0"))
                text_rect = QRect(bx + box_size + 6, rect.y(), rect.width() - (box_size + 18), rect.height())
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._count_str)

            painter.restore()
