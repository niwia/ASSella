"""
Shared dropdown widgets.

``ModernPopup`` is the frameless, scrollable list popup that started life as the
edition selector in DepotSelectionDialog. ``ModernComboBox`` is a drop-in
``QComboBox`` that paints itself like the depot dialog's pill buttons and opens
a ``ModernPopup`` instead of Qt's native list, so every dropdown in the app looks
and behaves the same way.

``ModernComboBox`` keeps the full ``QComboBox`` API (``addItem``, ``itemData``,
``currentIndexChanged``, ``activated`` ...), so converting a call site only means
changing the constructor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence, Union

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontInfo, QFontMetrics, QPainter, QPainterPath, QPalette, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
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

ROLE_PAYLOAD = Qt.ItemDataRole.UserRole + 101
ROLE_IS_HEADER = Qt.ItemDataRole.UserRole + 102
ROLE_IS_CHECKED = Qt.ItemDataRole.UserRole + 103
ROLE_SUBTITLE = Qt.ItemDataRole.UserRole + 104

POPUP_BG = "#1e1e24"
HEADER_HEIGHT = 24
ROW_HEIGHT = 32
ROW_HEIGHT_SUBTITLE = 44
DEFAULT_MAX_HEIGHT = 350
DEFAULT_FILTER_THRESHOLD = 7

ColorLike = Union[str, QColor]


def resolve_accent(widget: Optional[QWidget], override: Optional[ColorLike] = None) -> QColor:
    """Find the accent colour for ``widget``.

    Dialogs in ASSella keep their (possibly game-adaptive) accent on an
    ``accent_color`` attribute, so walk up the parent chain looking for one and
    fall back to the application palette's highlight, which theme.apply_palette
    sets to the user's accent.
    """
    if override:
        c = QColor(override)
        if c.isValid():
            return c
    w = widget
    while w is not None:
        val = getattr(w, "accent_color", None)
        if isinstance(val, (str, QColor)) and val:
            c = QColor(val)
            if c.isValid():
                return c
        w = w.parentWidget()
    return QApplication.palette().color(QPalette.ColorRole.Highlight)


@dataclass
class PopupEntry:
    """One row in a ModernPopup."""

    text: str
    subtitle: str = ""
    checked: bool = False
    header: bool = False
    enabled: bool = True
    payload: Any = None


def _font(base: QFont, point_size: float, weight: QFont.Weight) -> QFont:
    f = QFont(base)
    f.setPointSizeF(point_size)
    f.setWeight(weight)
    return f


class DropdownItemDelegate(QStyledItemDelegate):
    """Paints popup rows: optional subtitle, section headers and an accent checkmark."""

    def __init__(self, parent: Optional[QWidget] = None, accent_color: ColorLike = "#6c5ce7", centered: bool = False):
        super().__init__(parent)
        self.accent_color = QColor(accent_color)
        self.centered = centered

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        if index.data(ROLE_IS_HEADER):
            return QSize(option.rect.width(), HEADER_HEIGHT)
        if index.data(ROLE_SUBTITLE):
            return QSize(option.rect.width(), ROW_HEIGHT_SUBTITLE)
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        base_font = option.font
        is_header = bool(index.data(ROLE_IS_HEADER))
        is_checked = bool(index.data(ROLE_IS_CHECKED))
        is_enabled = bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)

        if is_header:
            painter.setFont(_font(base_font, 8, QFont.Weight.Bold))
            painter.setPen(QColor(160, 160, 170))
            header_text = str(index.data(Qt.ItemDataRole.DisplayRole) or "").upper()
            painter.drawText(
                rect.adjusted(12, 4, -12, -2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                header_text,
            )
            painter.setPen(QColor(255, 255, 255, 25))
            mid_y = rect.center().y()
            text_w = painter.fontMetrics().horizontalAdvance(header_text)
            painter.drawLine(rect.left() + 20 + text_w, mid_y, rect.right() - 12, mid_y)
            painter.restore()
            return

        if is_enabled and (is_hovered or is_selected):
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect.adjusted(4, 2, -4, -2)), 4, 4)
            painter.fillPath(path, QColor(255, 255, 255, 25))

        name = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        subtitle = str(index.data(ROLE_SUBTITLE) or "")

        if not is_enabled:
            title_color = QColor(255, 255, 255, 90)
        elif is_checked:
            title_color = self.accent_color
        else:
            title_color = QColor("#FFFFFF")

        # Space for the checkmark is reserved on both sides when centred so the
        # text stays visually centred whether or not the row is checked.
        right_margin = 28 if (is_checked or self.centered) else 10
        left_margin = 28 if self.centered else 12
        h_align = Qt.AlignmentFlag.AlignHCenter if self.centered else Qt.AlignmentFlag.AlignLeft

        title_font = _font(base_font, 9, QFont.Weight.Bold if is_checked else QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(title_color)
        if subtitle:
            title_rect = rect.adjusted(left_margin, 4, -right_margin, -20)
        else:
            title_rect = rect.adjusted(left_margin, 0, -right_margin, 0)
        elided = painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, title_rect.width())
        painter.drawText(title_rect, h_align | Qt.AlignmentFlag.AlignVCenter, elided)

        if subtitle:
            painter.setFont(_font(base_font, 8, QFont.Weight.Normal))
            painter.setPen(QColor(170, 170, 180) if is_enabled else QColor(170, 170, 180, 90))
            sub_rect = rect.adjusted(left_margin, 22, -right_margin, -4)
            sub_elided = painter.fontMetrics().elidedText(subtitle, Qt.TextElideMode.ElideRight, sub_rect.width())
            painter.drawText(sub_rect, h_align | Qt.AlignmentFlag.AlignVCenter, sub_elided)

        if is_checked:
            painter.setFont(_font(base_font, 11, QFont.Weight.Bold))
            painter.setPen(self.accent_color)
            check_rect = rect.adjusted(rect.width() - 26, 0, -8, 0)
            painter.drawText(check_rect, Qt.AlignmentFlag.AlignCenter, "✓")

        painter.restore()


class ModernPopup(QFrame):
    """Frameless, scrollable selection popup.

    Features screen clamping, kinetic touch scrolling for the Steam Deck, an
    optional quick filter for long lists, keyboard navigation and an accent
    checkmark on the current choice. ``on_selected`` receives the chosen
    entry's ``payload``.
    """

    closed = pyqtSignal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        entries: Optional[Sequence[PopupEntry]] = None,
        accent_color: Optional[ColorLike] = None,
        on_selected: Optional[Callable[[Any], None]] = None,
        *,
        centered: bool = False,
        filter_threshold: int = DEFAULT_FILTER_THRESHOLD,
        filter_placeholder: str = "Filter...",
        max_visible_items: int = 0,
    ):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("modern_popup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.accent_color = resolve_accent(parent, accent_color).name()
        self.on_selected = on_selected
        self._entries = list(entries or ())
        self._centered = centered
        self._filter_threshold = filter_threshold
        self._filter_placeholder = filter_placeholder
        self._max_visible_items = max_visible_items
        self._anchor: Optional[QWidget] = None

        self._build_ui()

    # ------------------------------------------------------------------ build
    def _build_ui(self) -> None:
        ac = self.accent_color
        # Object-name selectors keep parent dialog / app stylesheets from leaking in.
        self.setStyleSheet(f"""
            QFrame#modern_popup {{
                background-color: {POPUP_BG};
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 6px;
            }}
            QLineEdit#modern_popup_filter {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                padding: 4px 8px;
                color: #FFFFFF;
                font-size: 8.5pt;
                margin: 4px 4px 2px 4px;
            }}
            QLineEdit#modern_popup_filter:focus {{
                border-color: {ac};
            }}
            QListWidget#modern_popup_list {{
                background: transparent;
                border: none;
                outline: none;
                padding: 2px 0;
            }}
            QListWidget#modern_popup_list QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 2px 1px 2px 0;
            }}
            QListWidget#modern_popup_list QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.22);
                min-height: 20px;
                border-radius: 3px;
            }}
            QListWidget#modern_popup_list QScrollBar::handle:vertical:hover {{
                background: {ac};
            }}
            QListWidget#modern_popup_list QScrollBar::add-line:vertical,
            QListWidget#modern_popup_list QScrollBar::sub-line:vertical {{
                height: 0px;
                border: none;
                background: none;
            }}
            QListWidget#modern_popup_list QScrollBar::add-page:vertical,
            QListWidget#modern_popup_list QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        selectable = sum(1 for e in self._entries if not e.header)

        self.filter_input: Optional[QLineEdit] = None
        if self._filter_threshold and selectable > self._filter_threshold:
            self.filter_input = QLineEdit()
            self.filter_input.setObjectName("modern_popup_filter")
            self.filter_input.setPlaceholderText(self._filter_placeholder)
            self.filter_input.textChanged.connect(self._on_filter_changed)
            self.filter_input.installEventFilter(self)
            layout.addWidget(self.filter_input)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("modern_popup_list")
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_widget.setMouseTracking(True)
        self.list_widget.installEventFilter(self)

        # Steam Deck kinetic touch scrolling
        QScroller.grabGesture(self.list_widget.viewport(), QScroller.ScrollerGestureType.TouchGesture)

        self.delegate = DropdownItemDelegate(self.list_widget, accent_color=self.accent_color, centered=self._centered)
        self.list_widget.setItemDelegate(self.delegate)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget)

        self._populate_items()

    def _populate_items(self) -> None:
        self.list_widget.clear()
        for entry in self._entries:
            item = QListWidgetItem(entry.text)
            item.setData(ROLE_IS_HEADER, entry.header)
            if entry.header:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            else:
                item.setData(ROLE_PAYLOAD, entry.payload)
                item.setData(ROLE_IS_CHECKED, bool(entry.checked))
                item.setData(ROLE_SUBTITLE, entry.subtitle or "")
                if not entry.enabled:
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.addItem(item)

    # ----------------------------------------------------------- behaviour
    @staticmethod
    def _is_selectable(item: Optional[QListWidgetItem]) -> bool:
        return bool(
            item is not None
            and not item.isHidden()
            and not item.data(ROLE_IS_HEADER)
            and (item.flags() & Qt.ItemFlag.ItemIsEnabled)
        )

    def _on_filter_changed(self, text: str) -> None:
        query = text.strip().lower()
        header: Optional[QListWidgetItem] = None
        header_has_visible = False
        first_visible: Optional[QListWidgetItem] = None

        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item is None:
                continue
            if item.data(ROLE_IS_HEADER):
                if header is not None:
                    header.setHidden(not header_has_visible)
                header, header_has_visible = item, False
                continue
            haystack = f"{item.text()} {item.data(ROLE_SUBTITLE) or ''}".lower()
            visible = (not query) or (query in haystack)
            item.setHidden(not visible)
            if visible:
                header_has_visible = True
                if first_visible is None and self._is_selectable(item):
                    first_visible = item
        if header is not None:
            header.setHidden(not header_has_visible)

        if query and first_visible is not None:
            self.list_widget.setCurrentItem(first_visible)

    def _move_current(self, step: int) -> None:
        count = self.list_widget.count()
        if count == 0:
            return
        row = self.list_widget.currentRow()
        if row < 0:
            row = -1 if step > 0 else count
        r = row + step
        while 0 <= r < count:
            item = self.list_widget.item(r)
            if self._is_selectable(item):
                self.list_widget.setCurrentItem(item)
                self.list_widget.scrollToItem(item)
                return
            r += step

    def _activate_current(self) -> None:
        curr = self.list_widget.currentItem()
        if self._is_selectable(curr):
            self._on_item_clicked(curr)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        if not self._is_selectable(item):
            return
        payload = item.data(ROLE_PAYLOAD)
        self.close()
        if callable(self.on_selected):
            try:
                self.on_selected(payload)
            except Exception:
                logger.exception("Dropdown selection handler failed")

    def eventFilter(self, obj, event) -> bool:
        if event.type() == event.Type.KeyPress and obj in (self.filter_input, self.list_widget):
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._move_current(1)
                return True
            if key == Qt.Key.Key_Up:
                self._move_current(-1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._activate_current()
                return True
            if key == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        # Clicking the anchor (combo / button) while open should just close the
        # popup, not have Qt replay the click and immediately reopen it.
        if not self.rect().contains(event.position().toPoint()) and self._anchor is not None:
            try:
                local = self._anchor.mapFromGlobal(event.globalPosition().toPoint())
                if self._anchor.rect().contains(local):
                    self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay, True)
            except RuntimeError as _silent_err:
                logger.debug("[modern_dropdown] mousePressEvent: ignored %s", _silent_err, exc_info=False)
        super().mousePressEvent(event)

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)

    # ------------------------------------------------------------ geometry
    def _natural_width(self) -> int:
        base = self.list_widget.font()
        title_fm = QFontMetrics(_font(base, 9, QFont.Weight.Bold))
        sub_fm = QFontMetrics(_font(base, 8, QFont.Weight.Normal))
        widest = 0
        for e in self._entries:
            if e.header:
                continue
            widest = max(widest, title_fm.horizontalAdvance(e.text), sub_fm.horizontalAdvance(e.subtitle or ""))
        # row paddings (12 + 28) + list/frame margins + scrollbar gutter
        return widest + 40 + 8 + 10

    def show_at(self, target_widget: QWidget, min_width: int = 0) -> None:
        """Show the popup below ``target_widget`` (or above it if there is no room), clamped to the screen."""
        self._anchor = target_widget
        opt = QStyleOptionViewItem()
        model = self.list_widget.model()

        heights = [
            self.delegate.sizeHint(opt, model.index(i, 0)).height()
            for i in range(self.list_widget.count())
            if not self.list_widget.item(i).isHidden()
        ]
        if self._max_visible_items > 0:
            heights = heights[: self._max_visible_items]
        # Only show the scrollbar when the list really overflows.
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        filter_h = 30 if self.filter_input else 0

        screen = target_widget.screen() or QApplication.primaryScreen()
        screen_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        # 8 layout margins + 4 list padding + 2 frame border + 2 slack
        total_h = min(sum(heights) + filter_h + 16, DEFAULT_MAX_HEIGHT, screen_geo.height() - 8)
        total_w = max(target_widget.width(), min_width, self._natural_width())
        total_w = min(total_w, screen_geo.width() - 8)
        self.resize(total_w, total_h)

        btn_pos = target_widget.mapToGlobal(QPoint(0, 0))
        x = btn_pos.x()
        y = btn_pos.y() + target_widget.height() + 2

        if x + total_w > screen_geo.right():
            x = screen_geo.right() - total_w - 4
        if x < screen_geo.left():
            x = screen_geo.left() + 4
        if y + total_h > screen_geo.bottom():
            y = btn_pos.y() - total_h - 2
        if y < screen_geo.top():
            y = screen_geo.top() + 4

        self.move(int(x), int(y))

        # Start on the checked row so long lists open where the user left off.
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(ROLE_IS_CHECKED) and self._is_selectable(item):
                self.list_widget.setCurrentItem(item)
                self.list_widget.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)
                break

        self.show()
        self.raise_()
        self.activateWindow()
        if self.filter_input:
            self.filter_input.setFocus()
        else:
            self.list_widget.setFocus()


class ModernComboBox(QComboBox):
    """Drop-in QComboBox styled like the depot dialog's pill buttons.

    Opens a ``ModernPopup`` instead of the native list. Item subtitles can be
    set with ``add_item(..., subtitle=...)`` or ``setItemSubtitle``. Mouse-wheel
    scrolling is passed to the parent so scrolling a settings page never
    changes a value by accident.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        accent_color: Optional[ColorLike] = None,
        centered: bool = False,
        filter_threshold: int = DEFAULT_FILTER_THRESHOLD,
        filter_placeholder: str = "Filter...",
    ):
        super().__init__(parent)
        self._accent_override = accent_color
        self._centered = centered
        self._filter_threshold = filter_threshold
        self._filter_placeholder = filter_placeholder
        self._popup: Optional[ModernPopup] = None

        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        m = self.model()
        m.rowsInserted.connect(self.updateGeometry)
        m.rowsRemoved.connect(self.updateGeometry)
        m.dataChanged.connect(self.updateGeometry)
        m.modelReset.connect(self.updateGeometry)

    # ------------------------------------------------------------- helpers
    def set_accent_color(self, color: Optional[ColorLike]) -> None:
        self._accent_override = color
        self.update()

    def accent(self) -> QColor:
        return resolve_accent(self, self._accent_override)

    def add_item(self, text: str, data: Any = None, subtitle: str = "") -> None:
        self.addItem(text, data)
        if subtitle:
            self.setItemSubtitle(self.count() - 1, subtitle)

    def setItemSubtitle(self, index: int, subtitle: str) -> None:  # noqa: N802 (Qt naming)
        self.setItemData(index, subtitle, ROLE_SUBTITLE)

    def is_popup_open(self) -> bool:
        return self._popup is not None

    # --------------------------------------------------------------- popup
    def showPopup(self) -> None:  # noqa: N802
        if self._popup is not None:
            self._popup.close()
            return
        if self.count() == 0 or not self.isEnabled():
            return

        model = self.model()
        col = self.modelColumn()
        current = self.currentIndex()
        entries = []
        for i in range(self.count()):
            flags = model.flags(model.index(i, col, self.rootModelIndex()))
            entries.append(
                PopupEntry(
                    text=self.itemText(i),
                    subtitle=str(self.itemData(i, ROLE_SUBTITLE) or ""),
                    checked=(i == current),
                    enabled=bool(flags & Qt.ItemFlag.ItemIsEnabled),
                    payload=i,
                )
            )

        popup = ModernPopup(
            self,
            entries,
            accent_color=self.accent(),
            on_selected=self._on_popup_selected,
            centered=self._centered,
            filter_threshold=self._filter_threshold,
            filter_placeholder=self._filter_placeholder,
            max_visible_items=self.maxVisibleItems() if self.maxVisibleItems() != 10 else 0,
        )
        popup.closed.connect(self._on_popup_closed)
        self._popup = popup
        popup.show_at(self)
        self.update()

    def hidePopup(self) -> None:  # noqa: N802
        if self._popup is not None:
            self._popup.close()

    def _on_popup_selected(self, index: Any) -> None:
        if not isinstance(index, int) or not (0 <= index < self.count()):
            return
        if index != self.currentIndex():
            self.setCurrentIndex(index)
        self.activated.emit(index)
        self.textActivated.emit(self.itemText(index))

    def _on_popup_closed(self) -> None:
        self._popup = None
        self.update()

    # ---------------------------------------------------------------- input
    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)

    # ---------------------------------------------------------------- sizing
    def _text_font(self) -> QFont:
        f = QFont(self.font())
        f.setWeight(QFont.Weight.DemiBold)
        return f

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self._text_font())
        widest = max((fm.horizontalAdvance(self.itemText(i)) for i in range(self.count())), default=0)
        widest = max(widest, fm.horizontalAdvance("x" * max(self.minimumContentsLength(), 4)))
        pad = 12 + 26 + (14 if self._centered else 0)
        return QSize(min(widest + pad, 420), max(28, fm.height() + 12))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self._text_font())
        w = min(self.sizeHint().width(), fm.horizontalAdvance("x" * 8) + 38)
        return QSize(w, max(28, fm.height() + 12))

    # -------------------------------------------------------------- painting
    def paintEvent(self, event) -> None:  # noqa: N802
        if self.isEditable():
            # Editable combos host a QLineEdit; keep Qt's own frame around it.
            super().paintEvent(event)
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        enabled = self.isEnabled()
        if not enabled:
            p.setOpacity(0.45)

        hovered = enabled and self.underMouse()
        is_open = self._popup is not None
        accent = self.accent()

        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)

        if is_open:
            bg = QColor(255, 255, 255, 51)  # 0.20, matches :pressed
        elif hovered:
            bg = QColor(255, 255, 255, 36)  # 0.14
        else:
            bg = QColor(255, 255, 255, 18)  # 0.07
        p.fillPath(path, bg)

        if is_open or hovered:
            border = QColor(accent)
        elif self.hasFocus():
            border = QColor(accent)
            border.setAlpha(150)
        else:
            border = QColor(255, 255, 255, 46)  # 0.18
        p.setPen(QPen(border, 1))
        p.drawPath(path)

        # Text
        arrow_w = 26
        if self._centered:
            text_rect = self.rect().adjusted(arrow_w, 0, -arrow_w, 0)
            align = Qt.AlignmentFlag.AlignCenter
        else:
            text_rect = self.rect().adjusted(12, 0, -arrow_w, 0)
            align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        p.setFont(self._text_font())
        text = self.currentText()
        item_color = self.itemData(self.currentIndex(), Qt.ItemDataRole.ForegroundRole)
        if not text and self.placeholderText():
            p.setPen(QColor(255, 255, 255, 120))
            text = self.placeholderText()
        elif isinstance(item_color, QColor):
            p.setPen(item_color)
        else:
            p.setPen(QColor("#FFFFFF"))
        p.drawText(text_rect, align, p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width()))

        # Small filled arrow (▾), flipped while open
        cx = self.width() - 15
        cy = self.height() / 2
        if is_open:
            tri = QPolygonF([QPointF(cx - 4, cy + 2), QPointF(cx + 4, cy + 2), QPointF(cx, cy - 2.5)])
        else:
            tri = QPolygonF([QPointF(cx - 4, cy - 2), QPointF(cx + 4, cy - 2), QPointF(cx, cy + 2.5)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent if (is_open or hovered) else QColor(255, 255, 255, 215))
        p.drawPolygon(tri)
        p.end()


class ModernFontComboBox(ModernComboBox):
    """ModernComboBox listing installed font families (stand-in for QFontComboBox)."""

    currentFontChanged = pyqtSignal(QFont)

    def __init__(self, parent: Optional[QWidget] = None, **kwargs):
        kwargs.setdefault("filter_placeholder", "Filter fonts...")
        super().__init__(parent, **kwargs)
        from PyQt6.QtGui import QFontDatabase

        self.addItems(sorted(set(QFontDatabase.families()), key=str.lower))
        self.currentIndexChanged.connect(lambda _i: self.currentFontChanged.emit(self.currentFont()))

    def currentFont(self) -> QFont:  # noqa: N802
        return QFont(self.currentText())

    def setCurrentFont(self, font: QFont) -> None:  # noqa: N802
        family = font.family()
        idx = self.findText(family, Qt.MatchFlag.MatchFixedString)
        if idx < 0:
            idx = self.findText(QFontInfo(font).family(), Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self.setCurrentIndex(idx)


__all__ = [
    "DropdownItemDelegate",
    "ModernFontComboBox",
    "ModernComboBox",
    "ModernPopup",
    "PopupEntry",
    "ROLE_SUBTITLE",
    "resolve_accent",
]
