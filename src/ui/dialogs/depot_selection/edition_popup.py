"""
Modern, scrollable popup for selecting game editions and packages in DepotSelectionDialog.

Replaces standard QMenu with a sleek, kinetic-scrollable popup featuring custom delegates,
accent-themed scrollbars, screen clamping for Steam Deck / low-res displays, and optional
quick-filtering.
"""

from typing import Callable, Optional, Sequence
import logging

from PyQt6.QtCore import Qt, QPoint, QSize, QRectF
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QScroller,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Roles for list items
ROLE_EDITION_DATA = Qt.ItemDataRole.UserRole + 1
ROLE_IS_HEADER = Qt.ItemDataRole.UserRole + 2
ROLE_IS_CHECKED = Qt.ItemDataRole.UserRole + 3
ROLE_PLATFORM = Qt.ItemDataRole.UserRole + 4


class EditionItemDelegate(QStyledItemDelegate):
    """Paints sleek edition rows with real package names, subtitles, and selection checkmarks."""

    def __init__(self, parent: Optional[QWidget] = None, accent_color: str = "#6c5ce7"):
        super().__init__(parent)
        self.accent_color = QColor(accent_color)

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        is_header = index.data(ROLE_IS_HEADER)
        if is_header:
            return QSize(option.rect.width(), 24)
        return QSize(option.rect.width(), 44)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        is_header = bool(index.data(ROLE_IS_HEADER))
        is_checked = bool(index.data(ROLE_IS_CHECKED))
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)

        if is_header:
            # Platform section header
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            painter.setPen(QColor(160, 160, 170))
            header_text = str(index.data(Qt.ItemDataRole.DisplayRole) or "").upper()
            painter.drawText(
                rect.adjusted(12, 4, -12, -2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                header_text,
            )
            # Thin separator line
            painter.setPen(QColor(255, 255, 255, 25))
            mid_y = rect.center().y()
            text_w = painter.fontMetrics().horizontalAdvance(header_text)
            painter.drawLine(rect.left() + 20 + text_w, mid_y, rect.right() - 12, mid_y)
            painter.restore()
            return

        # Row background on hover/selection
        if is_hovered or is_selected:
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect.adjusted(4, 2, -4, -2)), 4, 4)
            painter.fillPath(path, QColor(255, 255, 255, 25))

        ed_data = index.data(ROLE_EDITION_DATA) or {}
        name = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        platform = str(index.data(ROLE_PLATFORM) or "").capitalize()
        depot_ids = ed_data.get("depot_ids") or set()
        depot_count = len(depot_ids)

        # Primary Title
        title_font = QFont("Segoe UI", 9, QFont.Weight.Bold if is_checked else QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor("#FFFFFF") if not is_checked else self.accent_color)

        # Checkmark width reservation
        right_margin = 28 if is_checked else 10
        title_rect = rect.adjusted(12, 4, -right_margin, -20)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        # Subtitle: Platform / Depot count
        sub_font = QFont("Segoe UI", 8, QFont.Weight.Normal)
        painter.setFont(sub_font)
        painter.setPen(QColor(170, 170, 180))

        sub_parts = []
        if platform:
            sub_parts.append(platform)
        if depot_count > 0:
            sub_parts.append(f"{depot_count} depot{'s' if depot_count != 1 else ''}")
        source = ed_data.get("source") or ""
        if source:
            sub_parts.append(source.capitalize())

        sub_text = " • ".join(sub_parts)
        sub_rect = rect.adjusted(12, 22, -right_margin, -4)
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sub_text)

        # Active Checkmark
        if is_checked:
            painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            painter.setPen(self.accent_color)
            check_rect = rect.adjusted(rect.width() - 26, 0, -8, 0)
            painter.drawText(check_rect, Qt.AlignmentFlag.AlignCenter, "✓")

        painter.restore()


class EditionPopup(QFrame):
    """
    Scrollable popup window displaying available game editions.
    Features screen clamping, touch gestures for Steam Deck, and real package mapping.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        groups: Optional[Sequence] = None,
        accent_color: str = "#6c5ce7",
        on_selected: Optional[Callable[[dict], None]] = None,
    ):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("edition_popup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.accent_color = accent_color
        self.on_selected = on_selected
        self._groups = groups or ()

        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(f"""
            QFrame#edition_popup {{
                background-color: #1e1e24;
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 6px;
            }}
            QLineEdit#filter_input {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                padding: 4px 8px;
                color: #FFFFFF;
                font-size: 8.5pt;
                margin: 4px 4px 2px 4px;
            }}
            QLineEdit#filter_input:focus {{
                border-color: {self.accent_color};
            }}
            QListWidget {{
                background: transparent;
                border: none;
                outline: none;
                padding: 2px 0;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 2px 1px 2px 0;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.22);
                min-height: 20px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {self.accent_color};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                border: none;
                background: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Count total choices
        total_choices = sum(len(getattr(g, "choices", [])) for g in self._groups)

        # Filter bar if more than 7 choices
        self.filter_input: Optional[QLineEdit] = None
        if total_choices > 7:
            self.filter_input = QLineEdit()
            self.filter_input.setObjectName("filter_input")
            self.filter_input.setPlaceholderText("Filter editions...")
            self.filter_input.textChanged.connect(self._on_filter_changed)
            layout.addWidget(self.filter_input)

        self.list_widget = QListWidget()
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)

        # Enable Steam Deck kinetic touch scrolling
        QScroller.grabGesture(self.list_widget.viewport(), QScroller.ScrollerGestureType.TouchGesture)

        self.delegate = EditionItemDelegate(self.list_widget, accent_color=self.accent_color)
        self.list_widget.setItemDelegate(self.delegate)

        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget)

        self._populate_items()

    def _populate_items(self) -> None:
        self.list_widget.clear()
        multi_group = len(self._groups) > 1

        for group in self._groups:
            label = getattr(group, "label", None)
            choices = getattr(group, "choices", [])

            if multi_group and label and choices:
                header_item = QListWidgetItem(label)
                header_item.setData(ROLE_IS_HEADER, True)
                header_item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list_widget.addItem(header_item)

            for choice in choices:
                item = QListWidgetItem(choice.name)
                item.setData(ROLE_EDITION_DATA, choice.edition)
                item.setData(ROLE_IS_HEADER, False)
                item.setData(ROLE_IS_CHECKED, bool(choice.checked))
                item.setData(ROLE_PLATFORM, choice.edition.get("platform", ""))
                self.list_widget.addItem(item)

    def _on_filter_changed(self, text: str) -> None:
        query = text.strip().lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item:
                continue
            if item.data(ROLE_IS_HEADER):
                # Hide header if all following siblings are hidden
                continue
            name = (item.text() or "").lower()
            visible = (not query) or (query in name)
            item.setHidden(not visible)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        if not item or item.data(ROLE_IS_HEADER):
            return
        ed_data = item.data(ROLE_EDITION_DATA)
        if ed_data and callable(self.on_selected):
            self.on_selected(ed_data)
        self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            curr = self.list_widget.currentItem()
            if curr and not curr.data(ROLE_IS_HEADER):
                self._on_item_clicked(curr)
                return
        elif event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def show_at(self, target_widget: QWidget) -> None:
        """Position the popup cleanly below or above target_widget, clamped to screen geometry."""
        # Calculate dynamic size
        item_count = self.list_widget.count()
        header_height = 30 if self.filter_input else 0
        list_h = sum(self.delegate.sizeHint(QStyleOptionViewItem(), self.list_widget.model().index(i, 0)).height() for i in range(item_count))
        total_h = min(list_h + header_height + 12, 350)
        total_w = max(target_widget.width() + 40, 290)

        self.resize(total_w, total_h)

        # Screen clamping
        btn_pos = target_widget.mapToGlobal(QPoint(0, 0))
        screen = target_widget.screen() or QApplication.primaryScreen()
        screen_geo = screen.availableGeometry() if screen else QRectF(0, 0, 1920, 1080).toRect()

        x = btn_pos.x()
        y = btn_pos.y() + target_widget.height() + 2

        # Check horizontal overflow
        if x + total_w > screen_geo.right():
            x = screen_geo.right() - total_w - 4
        if x < screen_geo.left():
            x = screen_geo.left() + 4

        # Check vertical overflow: if below screen, place above button
        if y + total_h > screen_geo.bottom():
            y = btn_pos.y() - total_h - 2
        if y < screen_geo.top():
            y = screen_geo.top() + 4

        self.move(int(x), int(y))
        self.show()
        self.raise_()
        self.activateWindow()
        if self.filter_input:
            self.filter_input.setFocus()
        else:
            self.list_widget.setFocus()
