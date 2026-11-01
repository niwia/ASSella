"""
Content-grouped depot selector (ASSella mode, normal installs).

Layout: content groups on the left (base game, each DLC, soundtracks,
extras), the selected group's depots on the right, edition / Browse Files
above, storage and Download below.

All behaviour stays in ``DepotSelectionDialog``: its depot table is kept
(hidden) as the single source of truth. Ticks in this view go through the
table's own click handler, and every change the existing logic makes
(edition picks, smart select, branch / build changes, enrichment refreshes,
missing / unreleased rows) flows back here through a debounced refresh. So
editions override the current picks, manual changes afterwards are kept, and
the next edition overrides again, exactly like the classic list.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.depot_selection.dialog import DepotSelectionDialog

logger = logging.getLogger(__name__)

ROLE = Qt.ItemDataRole.UserRole
R_OWNER = Qt.ItemDataRole.UserRole + 1
R_KIND = Qt.ItemDataRole.UserRole + 2
R_DEPOTLESS = Qt.ItemDataRole.UserRole + 21
R_OWNED = Qt.ItemDataRole.UserRole + 25

CAT_GAME, CAT_DLC, CAT_MUSIC, CAT_EXTRAS = "game", "dlc", "music", "extras"
CAT_LABELS = {CAT_GAME: "Game", CAT_DLC: "DLC", CAT_MUSIC: "Soundtracks", CAT_EXTRAS: "Extras"}
_MUSIC_RE = re.compile(r"\b(?:soundtrack|o\.?s\.?t\.?|music|score|songs?)\b", re.I)
_EXTRAS_RE = re.compile(r"\b(?:art\s*-?\s*book|artbook|digital\s+(?:art|book)|wallpapers?|comic|guide|manual|lore\s*book|making\s+of)\b", re.I)
_TAG_PREFIX_RE = re.compile(r"^(?:\s*\[[^\]]*\]\s*)+")
_SIZE_RE = re.compile(r"([\d.,]+)\s*([KMGT]?i?B)", re.I)
_UNIT = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4}


def _parse_size(text: str) -> int:
    m = _SIZE_RE.search(text or "")
    if not m:
        return 0
    try:
        return int(float(m.group(1).replace(",", "")) * _UNIT.get(m.group(2).lower(), 1))
    except ValueError:
        return 0


def _fmt_size(n: int) -> str:
    v = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if v < 1024 or unit == "TiB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{n} B"


class _MiddleElideLabel(QLabel):
    """Keeps both ends of long names visible ("Call of Duty… - Perseverance Pack")."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._full = text
        self.setToolTip(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        fm = self.fontMetrics()
        self.setText(fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(10, self.width() - 2)))


@dataclass
class _Depot:
    row: int
    depot_id: str
    name: str
    size_text: str
    size: int
    checked: bool
    selectable: bool
    tag: str = ""          # owned / unreleased / no key / hidden
    tooltip: str = ""


@dataclass
class _Group:
    key: str
    name: str
    category: str
    depots: List[_Depot] = field(default_factory=list)

    @property
    def selectable(self) -> List[_Depot]:
        return [d for d in self.depots if d.selectable]

    @property
    def checked_count(self) -> int:
        return sum(1 for d in self.depots if d.checked and d.selectable)

    @property
    def status(self) -> str:
        if self.selectable:
            return "ready"
        tags = {d.tag for d in self.depots}
        for t in ("owned", "unreleased", "no key"):
            if t in tags:
                return t
        return "locked"

    @property
    def total_size(self) -> int:
        return sum(d.size for d in self.depots if d.selectable)


class ContentDepotSelectionDialog(DepotSelectionDialog):
    """DepotSelectionDialog with the content-grouped layout."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._groups: Dict[str, _Group] = {}
        self._current_group: Optional[str] = None
        self._current_cat = CAT_GAME
        self._group_filter = ""
        self._refreshing = False

        self.resize(max(self.width(), 900), max(self.height(), 620))
        self._build_content_view()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(30)
        self._refresh_timer.timeout.connect(self._refresh_content_view)
        self.table_widget.itemChanged.connect(lambda *_: self._schedule_refresh())
        model = self.table_widget.model()
        model.modelReset.connect(self._schedule_refresh)
        model.rowsInserted.connect(self._schedule_refresh)
        model.rowsRemoved.connect(self._schedule_refresh)
        self._refresh_content_view()

    # ------------------------------------------------- hooks into the logic
    # Several existing paths change checks with table signals blocked, so the
    # methods they always end with also schedule a refresh.
    def _schedule_refresh(self) -> None:
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.start()

    def _update_table_headers(self):
        super()._update_table_headers()
        self._schedule_refresh()

    def _populate_table(self):
        super()._populate_table()
        self._schedule_refresh()

    def _apply_depot_checks(self, wanted) -> None:
        super()._apply_depot_checks(wanted)
        self._schedule_refresh()

    def _toggle_all_checkboxes(self, check=True):
        super()._toggle_all_checkboxes(check)
        self._schedule_refresh()

    # ------------------------------------------------------------ building
    @staticmethod
    def _find_layout_with(root: QLayout, widget: QWidget):
        for i in range(root.count()):
            it = root.itemAt(i)
            w = it.widget()
            if w is widget:
                return root, i
            child = it.layout() or (w.layout() if w is not None else None)
            if child is not None:
                found = ContentDepotSelectionDialog._find_layout_with(child, widget)
                if found:
                    return found
        return None

    def _build_content_view(self) -> None:
        ac = getattr(self, "accent_color", "#a1c9fd")
        self.table_widget.setVisible(False)
        chips = getattr(self, "_filter_chips_widget", None)
        if chips is not None:
            chips.setVisible(False)

        found = self._find_layout_with(self.layout(), self.table_widget)
        host_layout, index = found if found else (self.layout(), self.layout().count())

        view = QFrame()
        view.setObjectName("contentView")
        view.setStyleSheet(f"""
            QFrame#contentView {{ background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; }}
            QListWidget, QTableWidget {{ background: transparent; border: none; outline: none; color: #E8E8EE; }}
            QListWidget::item {{ border-radius: 6px; margin: 1px 4px; }}
            QListWidget::item:selected {{ background: rgba(255,255,255,0.08); }}
            QListWidget::item:hover {{ background: rgba(255,255,255,0.05); }}
            QTableWidget::item {{ padding: 2px 4px; border-bottom: 1px solid rgba(255,255,255,0.05); }}
            QTableWidget::item:selected {{ background: transparent; color: #E8E8EE; }}
            QHeaderView::section {{ background: transparent; color: rgba(255,255,255,0.45); border: none; padding: 4px; font-size: 8pt; }}
            QLineEdit#groupFilter {{ background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.10); border-radius: 5px; padding: 3px 8px; color: #fff; font-size: 8.5pt; }}
            QLineEdit#groupFilter:focus {{ border-color: {ac}; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; }}
            QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.18); border-radius: 3px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        vlay = QHBoxLayout(view)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(0)

        # Left: category tabs, filter, groups
        left = QWidget()
        left.setFixedWidth(290)
        llay = QVBoxLayout(left)
        llay.setContentsMargins(8, 8, 6, 8)
        llay.setSpacing(6)

        self._tab_row = QHBoxLayout()
        self._tab_row.setSpacing(4)
        self._tab_buttons: Dict[str, QPushButton] = {}
        for cat in (CAT_GAME, CAT_DLC, CAT_MUSIC, CAT_EXTRAS):
            b = QPushButton(CAT_LABELS[cat])
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c, c=cat: self._select_category(c))
            self._tab_buttons[cat] = b
            self._tab_row.addWidget(b)
        self._tab_row.addStretch(1)
        llay.addLayout(self._tab_row)

        self._group_filter_edit = QLineEdit()
        self._group_filter_edit.setObjectName("groupFilter")
        self._group_filter_edit.setPlaceholderText("Filter content...")
        self._group_filter_edit.textChanged.connect(self._on_group_filter)
        llay.addWidget(self._group_filter_edit)

        sel_row = QHBoxLayout()
        sel_row.setSpacing(6)
        self._cat_summary = QLabel("")
        self._cat_summary.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 8pt;")
        sel_row.addWidget(self._cat_summary, 1)
        for text, handler in (("All", lambda: self._set_category_checked(True)),
                              ("None", lambda: self._set_category_checked(False))):
            lb = QPushButton(text)
            lb.setCursor(Qt.CursorShape.PointingHandCursor)
            lb.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {ac}; font-size: 8pt; padding: 0 2px; }} QPushButton:hover {{ text-decoration: underline; }}")
            lb.clicked.connect(handler)
            sel_row.addWidget(lb)
        llay.addLayout(sel_row)

        self._group_list = QListWidget()
        self._group_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._group_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._group_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._group_list.setUniformItemSizes(True)
        self._group_list.currentItemChanged.connect(self._on_group_selected)
        llay.addWidget(self._group_list, 1)
        vlay.addWidget(left)

        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setStyleSheet("background: rgba(255,255,255,0.08);")
        vlay.addWidget(divider)

        # Right: selected group's depots
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(10, 8, 8, 8)
        rlay.setSpacing(4)
        head = QHBoxLayout()
        self._detail_title = QLabel("")
        self._detail_title.setStyleSheet("color: #FFFFFF; font-size: 10pt; font-weight: 600;")
        self._detail_sub = QLabel("")
        self._detail_sub.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 8pt;")
        head.addWidget(self._detail_title, 1)
        head.addWidget(self._detail_sub)
        rlay.addLayout(head)

        self._depot_table = QTableWidget(0, 3)
        self._depot_table.setHorizontalHeaderLabels(["Depot", "Name", "Size"])
        self._depot_table.verticalHeader().setVisible(False)
        self._depot_table.setShowGrid(False)
        self._depot_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._depot_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._depot_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hh = self._depot_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._depot_table.setColumnWidth(0, 112)
        self._depot_table.verticalHeader().setDefaultSectionSize(28)
        self._depot_table.cellClicked.connect(self._on_depot_clicked)
        rlay.addWidget(self._depot_table, 1)
        vlay.addWidget(right, 1)

        host_layout.insertWidget(index, view, 1)
        self._content_view = view

        # Edition + Browse Files move above the content, next to the header.
        self._move_edition_row_above(host_layout, index)
        self._style_tabs()

    def _move_edition_row_above(self, host_layout: QLayout, index: int) -> None:
        widgets = []
        if getattr(self, "edition_button", None) is not None:
            widgets.append(self.edition_button)
        browse = next((b for b in self.findChildren(QPushButton) if b.text().startswith("Browse Files")), None)
        if browse is not None:
            widgets.append(browse)
        if not widgets:
            return
        row = QHBoxLayout()
        row.setSpacing(10)
        for w in widgets:
            w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row.addWidget(w, 1)  # re-parents out of the old row below the table
        host_layout.insertLayout(index, row)

    def _style_tabs(self) -> None:
        ac = getattr(self, "accent_color", "#a1c9fd")
        for cat, b in self._tab_buttons.items():
            on = cat == self._current_cat
            b.setChecked(on)
            b.setStyleSheet(
                f"QPushButton {{ background: {'rgba(255,255,255,0.10)' if on else 'transparent'};"
                f" border: 1px solid {ac if on else 'rgba(255,255,255,0.10)'}; border-radius: 5px;"
                f" color: {'#FFFFFF' if on else 'rgba(255,255,255,0.65)'}; font-size: 8pt; padding: 2px 8px; }}"
                "QPushButton:hover { color: #FFFFFF; }"
            )

    # ---------------------------------------------------------------- model
    def _category_for(self, name: str, is_base: bool) -> str:
        if is_base:
            return CAT_GAME
        if _MUSIC_RE.search(name):
            return CAT_MUSIC
        if _EXTRAS_RE.search(name):
            return CAT_EXTRAS
        return CAT_DLC

    def _collect_groups(self) -> Dict[str, _Group]:
        t = self.table_widget
        app_id = str(self.app_id)
        catalog = {str(k): str(v) for k, v in (getattr(self, "dlc_catalog", None) or {}).items()}
        size_col = t.columnCount() - 1
        groups: Dict[str, _Group] = {}

        for row in range(t.rowCount()):
            it = t.item(row, 0)
            if it is None:
                continue
            kind = str(it.data(R_KIND) or "normal")
            if kind == "expander":
                continue
            did = str(it.data(ROLE) or it.text() or "").strip()
            owner = str(it.data(R_OWNER) or "").strip()
            cfg_item = t.item(row, 1)
            cfg = cfg_item.text() if cfg_item else ""
            size_item = t.item(row, size_col)
            size_text = size_item.text() if size_item else ""
            owned = bool(it.data(R_OWNED))
            flags = it.flags()
            enabled = flags != Qt.ItemFlag.NoItemFlags and bool(flags & Qt.ItemFlag.ItemIsEnabled)

            is_dlc = (
                (owner and owner != app_id and owner != did)
                or did in catalog
                or cfg.lstrip().upper().startswith("[DLC")
                or "[DLC]" in cfg[:40]
            )
            group_key = (owner if owner and owner != app_id else did) if is_dlc else app_id

            tag = ""
            if owned:
                tag = "owned"
            elif kind == "unreleased" or "[Unreleased]" in cfg:
                tag = "unreleased"
            elif kind == "missing":
                tag = "no key"
            elif kind == "hidden_depot":
                tag = "hidden"
            # Same rule as the classic list's click handler: real depots that
            # are enabled and not Steam-owned. Hidden-by-default depots (other
            # OS / languages) stay pickable here, they are just tagged.
            selectable = kind in ("normal", "hidden_depot") and not owned and enabled

            clean = _TAG_PREFIX_RE.sub("", cfg).strip() or f"Depot {did}"
            g = groups.get(group_key)
            if g is None:
                if group_key == app_id:
                    gname = "Base game"
                else:
                    gname = catalog.get(group_key) or clean
                    gname = _TAG_PREFIX_RE.sub("", gname).strip()
                g = groups[group_key] = _Group(group_key, gname, self._category_for(gname, group_key == app_id))
            g.depots.append(_Depot(
                row=row, depot_id=did, name=clean, size_text=size_text, size=_parse_size(size_text),
                checked=it.checkState() == Qt.CheckState.Checked, selectable=selectable, tag=tag,
                tooltip=it.toolTip() or (cfg_item.toolTip() if cfg_item else ""),
            ))

        if app_id not in groups:
            groups[app_id] = _Group(app_id, "Base game", CAT_GAME)
        return groups

    # -------------------------------------------------------------- refresh
    def _refresh_content_view(self) -> None:
        if self._refreshing or not hasattr(self, "_group_list"):
            return
        self._refreshing = True
        try:
            self._groups = self._collect_groups()
            counts = {c: 0 for c in CAT_LABELS}
            for g in self._groups.values():
                counts[g.category] += 1
            for cat, b in self._tab_buttons.items():
                b.setText(CAT_LABELS[cat] if cat == CAT_GAME else f"{CAT_LABELS[cat]} {counts[cat]}")
                b.setVisible(counts[cat] > 0)
            if counts.get(self._current_cat, 0) == 0:
                self._current_cat = CAT_GAME
            self._style_tabs()
            self._fill_group_list()
            self._fill_depot_table()
            self._update_summary()
        finally:
            self._refreshing = False

    def _visible_groups(self) -> List[_Group]:
        flt = self._group_filter.lower()
        out = [g for g in self._groups.values() if g.category == self._current_cat]
        if flt:
            out = [g for g in out if flt in g.name.lower() or flt in g.key]
        order = {"ready": 0, "owned": 1, "no key": 2, "unreleased": 3, "locked": 4}
        out.sort(key=lambda g: (g.key != str(self.app_id), order.get(g.status, 9), g.name.lower()))
        return out

    def _fill_group_list(self) -> None:
        lst = self._group_list
        lst.blockSignals(True)
        lst.clear()
        groups = self._visible_groups()
        keys = [g.key for g in groups]
        if self._current_group not in keys:
            self._current_group = keys[0] if keys else None
        for g in groups:
            item = QListWidgetItem()
            item.setData(ROLE, g.key)
            w = self._make_group_row(g)
            item.setSizeHint(QSize(0, 34))
            lst.addItem(item)
            lst.setItemWidget(item, w)
            if g.key == self._current_group:
                lst.setCurrentItem(item)
        lst.blockSignals(False)
        total = len(groups)
        picked = sum(1 for g in groups if g.checked_count)
        self._cat_summary.setText(f"{picked} of {total} selected")

    def _make_group_row(self, g: _Group) -> QWidget:
        w = QWidget()
        w.setFixedHeight(32)
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(8)
        cb = QCheckBox()
        cb.setFixedSize(20, 20)
        sel = g.selectable
        if sel:
            n = g.checked_count
            cb.setTristate(True)
            cb.setCheckState(
                Qt.CheckState.Checked if n == len(sel) else (Qt.CheckState.PartiallyChecked if n else Qt.CheckState.Unchecked)
            )
            cb.clicked.connect(lambda _c, key=g.key: self._toggle_group(key))
        else:
            cb.setEnabled(False)
        lay.addWidget(cb)
        name = _MiddleElideLabel(g.name)
        name.setStyleSheet(f"color: {'#E8E8EE' if sel else 'rgba(255,255,255,0.45)'}; font-size: 9pt; background: transparent; padding: 0; margin: 0;")
        name.setFixedHeight(22)
        name.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        name.setToolTip(f"{g.name}\nApp {g.key}")
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setMinimumWidth(40)
        lay.addWidget(name, 1)
        status = g.status
        if status != "ready":
            lay.addWidget(self._tag_label(status))
        elif g.total_size:
            sz = QLabel(_fmt_size(g.total_size))
            sz.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 8pt; background: transparent;")
            lay.addWidget(sz)
        return w

    @staticmethod
    def _tag_label(tag: str) -> QLabel:
        colors = {
            "owned": ("rgba(255,255,255,0.08)", "rgba(255,255,255,0.6)"),
            "unreleased": ("rgba(255,183,77,0.16)", "#FFB74D"),
            "no key": ("rgba(229,115,115,0.16)", "#E57373"),
            "hidden": ("rgba(255,255,255,0.06)", "rgba(255,255,255,0.5)"),
            "locked": ("rgba(255,255,255,0.06)", "rgba(255,255,255,0.5)"),
        }
        bg, fg = colors.get(tag, colors["locked"])
        lbl = QLabel(tag)
        lbl.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: 4px; padding: 1px 6px; font-size: 7.5pt;")
        return lbl

    def _fill_depot_table(self) -> None:
        tbl = self._depot_table
        g = self._groups.get(self._current_group or "")
        tbl.setRowCount(0)
        if g is None:
            self._detail_title.setText("")
            self._detail_sub.setText("")
            return
        self._detail_title.setText(g.name)
        sel = g.selectable
        if sel:
            self._detail_sub.setText(f"{g.checked_count} of {len(sel)} depots · {_fmt_size(sum(d.size for d in sel if d.checked))}")
        else:
            reason = {"owned": "Owned on Steam · Steam provides it", "unreleased": "Not released on Steam yet",
                      "no key": "No key on Hubcap"}.get(g.status, "Nothing to download")
            self._detail_sub.setText(reason)

        grey = QColor(255, 255, 255, 110)
        tbl.setRowCount(len(g.depots))
        for r, d in enumerate(g.depots):
            id_item = QTableWidgetItem(d.depot_id)
            id_item.setFlags(Qt.ItemFlag.ItemIsEnabled | (Qt.ItemFlag.ItemIsUserCheckable if d.selectable else Qt.ItemFlag.NoItemFlags))
            id_item.setCheckState(Qt.CheckState.Checked if d.checked else Qt.CheckState.Unchecked)
            id_item.setData(ROLE, d.row)
            name_txt = d.name if not d.tag or d.tag == "hidden" else f"{d.name}"
            name_item = QTableWidgetItem(name_txt)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            size_item = QTableWidgetItem(d.size_text if d.selectable or d.tag == "hidden" else (d.tag or d.size_text))
            size_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            for itm in (id_item, name_item, size_item):
                if d.tooltip:
                    itm.setToolTip(d.tooltip)
                if not d.selectable:
                    itm.setForeground(grey)
            if d.tag == "hidden":
                name_item.setText(f"{d.name}  · hidden")
            tbl.setItem(r, 0, id_item)
            tbl.setItem(r, 1, name_item)
            tbl.setItem(r, 2, size_item)

    def _update_summary(self) -> None:
        picked_groups = sum(1 for g in self._groups.values() if g.checked_count)
        picked = [d for g in self._groups.values() for d in g.depots if d.checked and d.selectable]
        total = sum(d.size for d in picked)
        tip = f"{picked_groups} content group(s) · {len(picked)} depot(s) · {_fmt_size(total)}"
        if getattr(self, "download_btn", None) is not None:
            self.download_btn.setToolTip(tip)

    # -------------------------------------------------------- interactions
    def _select_category(self, cat: str) -> None:
        self._current_cat = cat
        self._current_group = None
        self._style_tabs()
        self._fill_group_list()
        self._fill_depot_table()

    def _on_group_filter(self, text: str) -> None:
        self._group_filter = text.strip()
        self._fill_group_list()
        self._fill_depot_table()

    def _on_group_selected(self, current: Optional[QListWidgetItem], _prev=None) -> None:
        if current is None:
            return
        self._current_group = str(current.data(ROLE))
        self._fill_depot_table()

    def _click_source_row(self, row: int) -> None:
        # The table's own handler: owned / missing / unreleased guards, edition
        # button turning "Custom", header totals, saved-selection flags.
        self.on_depot_cell_clicked(row, 0)

    def _on_depot_clicked(self, r: int, _c: int) -> None:
        item = self._depot_table.item(r, 0)
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return
        self._click_source_row(int(item.data(ROLE)))
        self._schedule_refresh()

    def _set_rows(self, depots: List[_Depot], check: bool) -> None:
        for d in depots:
            if d.selectable and d.checked != check:
                self._click_source_row(d.row)
        self._schedule_refresh()

    def _toggle_group(self, key: str) -> None:
        g = self._groups.get(key)
        if g is None:
            return
        sel = g.selectable
        self._set_rows(sel, check=g.checked_count < len(sel))
        self._current_group = key

    def _set_category_checked(self, check: bool) -> None:
        for g in self._visible_groups():
            self._set_rows(g.selectable, check)


__all__ = ["ContentDepotSelectionDialog"]
