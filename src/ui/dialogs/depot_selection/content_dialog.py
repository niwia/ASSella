"""
Content-grouped depot selector (preview, opt-in for normal installs).

    ┌ thumb  Game name (appid)          [Edition ▾] [branch ▾] [Build ▾] ┐
    │ Content            │ Base game                    3 of 5 depots    │
    │ ☑ Base game  48 GB │ ☑ 1942281  [win] Content            46.1 GB  │
    │ ☑ Expansion  ready │ ☐ 1942284  [lin] Linux bin  other OS  0.1 GB │
    │ ☐ Season 2  unrel. │ ...                                          │
    └ [Drive ▾] [Browse files]          2 groups · 6 depots · 51 GB [Download]

All behaviour stays in ``DepotSelectionDialog``. Its depot table is kept
(hidden) as the single source of truth: ticks here go through the table's own
click handler, and every change the existing logic makes (edition picks,
smart select, branch / build changes, enrichment refreshes, missing /
unreleased rows) flows back through a debounced refresh. The existing header
controls (thumbnail, app id, edition, branch, build, browse files, download)
are re-parented into this layout, so they keep working exactly as before.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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
R_OWNED = Qt.ItemDataRole.UserRole + 25
R_HEADER = Qt.ItemDataRole.UserRole + 300

CAT_GAME, CAT_DLC, CAT_MUSIC, CAT_EXTRAS = "game", "dlc", "music", "extras"
CAT_ORDER = (CAT_GAME, CAT_DLC, CAT_MUSIC, CAT_EXTRAS)
CAT_HEADERS = {CAT_GAME: "Content", CAT_DLC: "DLC", CAT_MUSIC: "Soundtracks", CAT_EXTRAS: "Extras"}
_MUSIC_RE = re.compile(r"\b(?:soundtrack|o\.?s\.?t\.?|music|score)\b", re.I)
_EXTRAS_RE = re.compile(r"\b(?:art\s*-?\s*book|artbook|digital\s+(?:art|book)|wallpapers?|comic|guide|manual|making\s+of)\b", re.I)
_TAG_PREFIX_RE = re.compile(r"^(?:\s*\[[^\]]*\]\s*)+")
_SIZE_RE = re.compile(r"([\d.,]+)\s*([KMGT]?i?B)", re.I)
_UNIT = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4}
_SHARED_REDIST_IDS = {"228980", "228981", "228982", "228983", "228984", "228985", "228986", "228987", "228988", "228989", "228990", "229000", "229001", "229002", "229003", "229004", "229005", "229006", "229007", "229010", "229011", "229012", "229020", "229030", "229031", "229032", "229033"}

TEXT = "#E8E8EE"
MUTED = "rgba(255,255,255,0.50)"
DIM = "rgba(255,255,255,0.38)"
TAG_COLORS = {
    "ready": ("rgba(76,175,80,0.16)", "#81C784"),
    "owned": ("rgba(255,255,255,0.08)", "rgba(255,255,255,0.62)"),
    "unreleased": ("rgba(255,183,77,0.16)", "#FFB74D"),
    "no key": ("rgba(229,115,115,0.16)", "#E57373"),
    "other OS": ("rgba(255,255,255,0.06)", "rgba(255,255,255,0.55)"),
    "shared": ("rgba(255,255,255,0.06)", "rgba(255,255,255,0.55)"),
    "license": ("rgba(255,255,255,0.06)", "rgba(255,255,255,0.55)"),
}


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
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1000 or unit == "TB":
            return f"{v:.0f} {unit}" if unit in ("B", "KB") else f"{v:.1f} {unit}"
        v /= 1000
    return f"{n} B"


class _ElideLabel(QLabel):
    """Label that never clips: elides (middle or right) to its width, full text in the tooltip."""

    def __init__(self, text: str = "", mode=Qt.TextElideMode.ElideRight, parent=None):
        super().__init__(parent)
        self._full = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(24)
        self.setToolTip(text)

    def setFullText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self.setToolTip(text)
        self._apply()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def _apply(self):
        self.setText(self.fontMetrics().elidedText(self._full, self._mode, max(10, self.width() - 2)))


def _tag(text: str) -> QLabel:
    bg, fg = TAG_COLORS.get(text, TAG_COLORS["owned"])
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: 4px; padding: 1px 7px; font-size: 8pt;")
    lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return lbl


@dataclass
class _Depot:
    row: int
    depot_id: str
    name: str
    icon: QIcon
    size_text: str
    size: int
    checked: bool
    selectable: bool
    tag: str = ""
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
        return "owned" if not self.depots else "no key"

    @property
    def picked_size(self) -> int:
        return sum(d.size for d in self.depots if d.selectable and d.checked)

    @property
    def total_size(self) -> int:
        return sum(d.size for d in self.depots if d.selectable)


class ContentDepotSelectionDialog(DepotSelectionDialog):
    """DepotSelectionDialog presented as content groups + depots."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._groups: Dict[str, _Group] = {}
        self._current_group: Optional[str] = None
        self._group_filter = ""
        self._refreshing = False

        self._cs_build_shell()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(30)
        self._refresh_timer.timeout.connect(self._refresh_view)
        self.table_widget.itemChanged.connect(lambda *_: self._schedule_refresh())
        model = self.table_widget.model()
        model.modelReset.connect(self._schedule_refresh)
        model.rowsInserted.connect(self._schedule_refresh)
        model.rowsRemoved.connect(self._schedule_refresh)
        self._refresh_view()

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
    def _pill_style(self) -> str:
        ac = getattr(self, "accent_color", "#a1c9fd")
        return (
            "QPushButton { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.16);"
            f" border-radius: 6px; color: {TEXT}; font-size: 8.5pt; font-weight: 500; padding: 3px 10px; }}"
            f"QPushButton:hover {{ border-color: {ac}; background: rgba(255,255,255,0.10); }}"
        )

    def _cs_build_shell(self) -> None:
        root = self.layout()
        old_container = root.itemAt(root.count() - 1).widget() if root.count() > 1 else None

        shell = QWidget()
        lay = QVBoxLayout(shell)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(8)

        lay.addLayout(self._cs_build_top_bar())
        lay.addWidget(self._cs_build_body(), 1)
        lay.addLayout(self._cs_build_bottom_bar())

        if old_container is not None:
            old_container.setVisible(False)  # its controls now live in the shell
        root.addWidget(shell, 1)

    def _cs_build_top_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(10)

        thumb = getattr(self, "header_label", None)
        if thumb is not None:
            pm = thumb.pixmap()
            thumb.setFixedSize(86, 40)
            thumb.setScaledContents(True)
            if pm is not None and not pm.isNull():
                thumb.setPixmap(pm)
            bar.addWidget(thumb, 0, Qt.AlignmentFlag.AlignVCenter)

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        name = _ElideLabel(str(self.game_name or "Game"))
        name.setStyleSheet(f"color: {TEXT}; font-size: 10.5pt; font-weight: 600;")
        title_col.addWidget(name)
        sub = QHBoxLayout()
        sub.setSpacing(4)
        appid_btn = getattr(self, "appid_btn", None)
        if appid_btn is not None:
            appid_btn.setText(f"App {self.app_id}")
            appid_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; color: {MUTED}; font-size: 8pt; padding: 0; text-align: left; }}"
                f"QPushButton:hover {{ color: {getattr(self, 'accent_color', '#a1c9fd')}; text-decoration: underline; }}"
            )
            appid_btn.setToolTip("Copy AppID")
            sub.addWidget(appid_btn)
        sub.addStretch(1)
        title_col.addLayout(sub)
        bar.addLayout(title_col, 1)

        pill = self._pill_style()
        for attr in ("edition_button", "branch_combo", "branch_btn", "builds_btn"):
            w = getattr(self, attr, None)
            if w is None:
                continue
            if isinstance(w, QPushButton):
                w.setStyleSheet(pill)
            w.setFixedHeight(28)
            if attr == "edition_button":
                w.setMaximumWidth(170)
                w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            bar.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        return bar

    def _cs_build_body(self) -> QFrame:
        ac = getattr(self, "accent_color", "#a1c9fd")
        body = QFrame()
        body.setObjectName("csBody")
        body.setStyleSheet(f"""
            QFrame#csBody {{ background: rgba(255,255,255,0.025); border: 1px solid rgba(255,255,255,0.09); border-radius: 8px; }}
            QListWidget, QTableWidget {{ background: transparent; border: none; outline: none; color: {TEXT}; }}
            QListWidget::item {{ border-radius: 6px; margin: 0 4px; }}
            QListWidget::item:selected {{ background: rgba(255,255,255,0.07); border: 1px solid {ac}; }}
            QListWidget::item:hover:!selected {{ background: rgba(255,255,255,0.04); }}
            QTableWidget::item {{ border-bottom: 1px solid rgba(255,255,255,0.05); padding: 0 4px; color: {TEXT}; }}
            QTableWidget::item:selected {{ background: transparent; color: {TEXT}; }}
            QLineEdit#csFilter {{ background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.10); border-radius: 5px; padding: 2px 8px; color: #fff; font-size: 8.5pt; }}
            QLineEdit#csFilter:focus {{ border-color: {ac}; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; }}
            QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.18); border-radius: 3px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        h = QHBoxLayout(body)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        left = QWidget()
        left.setFixedWidth(250)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 6, 4, 6)
        ll.setSpacing(4)
        self._filter_edit = QLineEdit()
        self._filter_edit.setObjectName("csFilter")
        self._filter_edit.setPlaceholderText("Filter content...")
        self._filter_edit.setFixedHeight(26)
        self._filter_edit.textChanged.connect(self._on_filter)
        self._filter_edit.setVisible(False)
        ll.addWidget(self._filter_edit)
        self._group_list = QListWidget()
        self._group_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._group_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._group_list.currentItemChanged.connect(self._on_group_selected)
        ll.addWidget(self._group_list, 1)
        h.addWidget(left)

        div = QFrame()
        div.setFixedWidth(1)
        div.setStyleSheet("background: rgba(255,255,255,0.09);")
        h.addWidget(div)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 8, 6, 6)
        rl.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self._detail_title = _ElideLabel("")
        self._detail_title.setStyleSheet(f"color: {TEXT}; font-size: 9.5pt; font-weight: 600;")
        self._detail_sub = QLabel("")
        self._detail_sub.setStyleSheet(f"color: {MUTED}; font-size: 8pt;")
        head.addWidget(self._detail_title, 1)
        head.addWidget(self._detail_sub, 0)
        rl.addLayout(head)

        t = QTableWidget(0, 3)
        t.horizontalHeader().setVisible(False)
        t.verticalHeader().setVisible(False)
        t.setShowGrid(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        t.setIconSize(QSize(46, 14))
        t.setWordWrap(False)
        t.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(0, 104)
        t.setColumnWidth(2, 86)
        t.verticalHeader().setDefaultSectionSize(30)
        t.cellClicked.connect(self._on_depot_clicked)
        self._depot_table = t
        rl.addWidget(t, 1)
        h.addWidget(right, 1)
        return body

    def _cs_build_bottom_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)

        from ui.widgets.modern_dropdown import ModernComboBox
        self._drive_combo = ModernComboBox()
        self._drive_combo.setFixedHeight(30)
        self._drive_combo.setMinimumWidth(150)
        self._drive_combo.setMaximumWidth(230)
        self._fill_drive_combo()
        self._drive_combo.currentIndexChanged.connect(self._on_drive_changed)
        bar.addWidget(self._drive_combo)

        browse = next((b for b in self.findChildren(QPushButton) if b.text().startswith("Browse Files")), None)
        if browse is not None:
            browse.setText("Browse files")
            browse.setStyleSheet(self._pill_style())
            browse.setFixedHeight(30)
            browse.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            browse.setMinimumWidth(96)
            bar.addWidget(browse)

        bar.addStretch(1)
        self._summary = QLabel("")
        self._summary.setStyleSheet(f"color: {MUTED}; font-size: 8.5pt;")
        bar.addWidget(self._summary)

        dl = getattr(self, "download_btn", None)
        if dl is not None:
            dl.setFixedHeight(32)
            dl.setMinimumWidth(110)
            bar.addWidget(dl)
        return bar

    # --------------------------------------------------------------- drives
    def _fill_drive_combo(self) -> None:
        from ui.dialogs.depot_selection import storage_targets
        combo = self._drive_combo
        combo.blockSignals(True)
        combo.clear()
        paths = list(getattr(self, "_storage_paths", None) or [])
        current = getattr(self, "selected_storage_path", None)
        locked = bool(getattr(self, "lock_storage", False)) or getattr(self, "_locked_storage_btn", None) is not None
        if current and current not in paths:
            paths.insert(0, current)
        for p in paths:
            try:
                t = storage_targets.describe(p)
                label = f"{t.label} · {t.free} free" if getattr(t, "free", "") else t.label
            except Exception:
                label = str(p)
            combo.add_item(label, p, subtitle=str(p))
        if not paths:
            combo.add_item("Default location", None)
        idx = combo.findData(current) if current else 0
        combo.setCurrentIndex(max(idx, 0))
        combo.setEnabled(not locked and len(paths) > 1)
        if locked:
            combo.setToolTip("Installed games keep their current location.")
        combo.blockSignals(False)

    def _on_drive_changed(self, idx: int) -> None:
        path = self._drive_combo.itemData(idx)
        if not path:
            return
        self.selected_storage_path = path
        grp = getattr(self, "_storage_btn_group", None)
        if grp is not None:
            for b in grp.buttons():
                if b.property("storage_path") == path or b.toolTip() == path:
                    b.setChecked(True)

    # ---------------------------------------------------------------- model
    @staticmethod
    def _category_for(name: str, is_base: bool) -> str:
        if is_base:
            return CAT_GAME
        if _MUSIC_RE.search(name):
            return CAT_MUSIC
        if _EXTRAS_RE.search(name):
            return CAT_EXTRAS
        return CAT_DLC

    def _short_name(self, name: str) -> str:
        """Drop the game's own title from DLC names ("Call of Duty Black Ops 6 - X" -> "Black Ops 6 - X")."""
        title = re.sub(r"[\u00ae\u2122\u00a9]", "", str(self.game_name or "")).strip()
        clean = re.sub(r"[\u00ae\u2122\u00a9]", "", name).strip()
        if title and clean.lower().startswith(title.lower()):
            rest = clean[len(title):].lstrip(" :-\u2013\u2014").strip()
            if len(rest) >= 3:
                return rest
        return clean

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
            if kind == "missing" and (not owner or owner == did):
                # Missing rows carry their DLC in missing_depots_info, not the table.
                minfo = (getattr(self, "missing_depots_info", None) or {}).get(did) or {}
                owner = str(minfo.get("dlcappid") or owner or "").strip()
            owned = bool(it.data(R_OWNED))
            flags = it.flags()
            enabled = flags != Qt.ItemFlag.NoItemFlags and bool(flags & Qt.ItemFlag.ItemIsEnabled)

            is_dlc = (
                (owner and owner != app_id and owner != did)
                or did in catalog
                or "[DLC" in cfg[:48].upper()
            )
            clean = self._short_name(_TAG_PREFIX_RE.sub("", cfg).strip()) or f"Depot {did}"
            if not is_dlc:
                key = app_id
            elif owner and owner not in (app_id, did):
                key = owner                      # depot mapped to its DLC app
            elif did in catalog:
                key = did                        # the DLC app itself (license / DlcData row)
            else:
                # Unmapped DLC content split across several depots (per language /
                # OS) under one name: one group, not N identical rows.
                key = "name:" + clean.lower()

            tag = ""
            if owned:
                tag = "owned"
            elif kind == "unreleased" or "[Unreleased]" in cfg:
                tag = "unreleased"
            elif kind == "missing":
                tag = "no key"
            elif kind == "hidden_depot":
                tag = "other OS"
            elif did in _SHARED_REDIST_IDS:
                tag = "shared"
            elif size_text.strip().lower() == "dlcdata":
                tag = "license"
            selectable = kind in ("normal", "hidden_depot") and not owned and enabled

            g = groups.get(key)
            if g is None:
                if key == app_id:
                    gname = "Base game"
                else:
                    gname = self._short_name(_TAG_PREFIX_RE.sub("", catalog.get(key) or clean).strip())
                    if key.startswith("name:"):
                        gname = clean
                g = groups[key] = _Group(key, gname, self._category_for(gname, key == app_id))
            g.depots.append(_Depot(
                row=row, depot_id=did, name=clean, icon=cfg_item.icon() if cfg_item else QIcon(),
                size_text="" if tag == "license" else size_text, size=_parse_size(size_text),
                checked=it.checkState() == Qt.CheckState.Checked, selectable=selectable, tag=tag,
                tooltip=(cfg_item.toolTip() if cfg_item else "") or it.toolTip(),
            ))
        if app_id not in groups:
            groups[app_id] = _Group(app_id, "Base game", CAT_GAME)
        return groups

    # -------------------------------------------------------------- refresh
    def _refresh_view(self) -> None:
        if self._refreshing or not hasattr(self, "_group_list"):
            return
        self._refreshing = True
        try:
            self._groups = self._collect_groups()
            self._filter_edit.setVisible(len(self._groups) > 12)
            self._fill_group_list()
            self._fill_depot_table()
            self._update_summary()
        finally:
            self._refreshing = False

    def _ordered_groups(self) -> List[_Group]:
        flt = self._group_filter.lower()
        order = {"ready": 0, "owned": 1, "no key": 2, "unreleased": 3}
        out = [g for g in self._groups.values() if not flt or flt in g.name.lower() or flt in g.key]
        out.sort(key=lambda g: (CAT_ORDER.index(g.category), g.key != str(self.app_id), order.get(g.status, 9), g.name.lower()))
        return out

    def _fill_group_list(self) -> None:
        lst = self._group_list
        scroll = lst.verticalScrollBar().value()
        lst.blockSignals(True)
        lst.clear()
        groups = self._ordered_groups()
        keys = [g.key for g in groups]
        if self._current_group not in keys:
            self._current_group = keys[0] if keys else None
        last_cat = None
        for g in groups:
            # Section headers: "Content" over base game + DLC, then Soundtracks / Extras.
            section = CAT_GAME if g.category in (CAT_GAME, CAT_DLC) else g.category
            if section != last_cat:
                hdr = QListWidgetItem(CAT_HEADERS[section])
                hdr.setFlags(Qt.ItemFlag.NoItemFlags)
                hdr.setData(R_HEADER, True)
                hdr.setForeground(QColor(255, 255, 255, 110))
                f = hdr.font()
                f.setPointSizeF(8)
                hdr.setFont(f)
                hdr.setSizeHint(QSize(0, 22))
                lst.addItem(hdr)
                last_cat = section
            item = QListWidgetItem()
            item.setData(ROLE, g.key)
            item.setSizeHint(QSize(0, 32))
            lst.addItem(item)
            lst.setItemWidget(item, self._make_group_row(g))
            if g.key == self._current_group:
                lst.setCurrentItem(item)
        lst.blockSignals(False)
        lst.verticalScrollBar().setValue(scroll)

    def _make_group_row(self, g: _Group) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(8)
        cb = QCheckBox()
        cb.setFixedSize(18, 18)
        sel = g.selectable
        if sel:
            n = g.checked_count
            cb.setTristate(True)
            cb.setCheckState(
                Qt.CheckState.Checked if n == len(sel) else (Qt.CheckState.PartiallyChecked if n else Qt.CheckState.Unchecked)
            )
            cb.clicked.connect(lambda _c, key=g.key: self._toggle_group(key))
        else:
            cb.setChecked(g.status == "owned")
            cb.setEnabled(False)
        lay.addWidget(cb, 0, Qt.AlignmentFlag.AlignVCenter)
        name = _ElideLabel(g.name, Qt.TextElideMode.ElideMiddle)
        name.setToolTip(g.name if g.key.startswith("name:") else f"{g.name}\nApp {g.key}")
        name.setStyleSheet(f"color: {TEXT if sel else DIM}; font-size: 9pt; background: transparent;")
        name.setFixedHeight(20)
        lay.addWidget(name, 1, Qt.AlignmentFlag.AlignVCenter)
        if sel:
            total = g.picked_size or g.total_size
            if total >= 1_000_000:  # sub-MB entries are license stubs, not downloads
                sz = QLabel(_fmt_size(total))
                sz.setStyleSheet(f"color: {MUTED}; font-size: 8pt; background: transparent;")
                lay.addWidget(sz, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            lay.addWidget(_tag(g.status), 0, Qt.AlignmentFlag.AlignVCenter)
        return w

    def _fill_depot_table(self) -> None:
        tbl = self._depot_table
        g = self._groups.get(self._current_group or "")
        tbl.setRowCount(0)
        if g is None:
            self._detail_title.setFullText("")
            self._detail_sub.setText("")
            return
        self._detail_title.setFullText(g.name)
        sel = g.selectable
        if sel:
            n = g.checked_count
            size = _fmt_size(g.picked_size)
            self._detail_sub.setText(f"{n} of {len(sel)} depot{'s' if len(sel) != 1 else ''} · {size}")
        else:
            self._detail_sub.setText({
                "owned": "Owned on Steam", "unreleased": "Not released yet", "no key": "No key on Hubcap",
            }.get(g.status, ""))

        tbl.setRowCount(len(g.depots))
        for r, d in enumerate(g.depots):
            id_item = QTableWidgetItem(d.depot_id)
            id_item.setFlags(Qt.ItemFlag.ItemIsEnabled | (Qt.ItemFlag.ItemIsUserCheckable if d.selectable else Qt.ItemFlag.NoItemFlags))
            id_item.setCheckState(Qt.CheckState.Checked if d.checked else Qt.CheckState.Unchecked)
            id_item.setData(ROLE, d.row)
            id_item.setForeground(QColor(255, 255, 255, 120))
            name_item = QTableWidgetItem(d.name)
            name_item.setForeground(QColor(TEXT))
            if not d.icon.isNull():
                name_item.setIcon(d.icon)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            size_item = QTableWidgetItem(d.size_text)
            size_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            size_item.setForeground(QColor(255, 255, 255, 140))
            tip = d.tooltip or d.name
            for itm in (id_item, name_item, size_item):
                itm.setToolTip(tip)
            tbl.setItem(r, 0, id_item)
            tbl.setItem(r, 1, name_item)
            tbl.setItem(r, 2, size_item)
            if d.tag or not d.selectable:
                # Name + tag pill in one cell (and greyed text for locked rows; the
                # app stylesheet colours table items, so this uses labels).
                holder = QWidget()
                holder.setStyleSheet("background: transparent;")
                hl = QHBoxLayout(holder)
                hl.setContentsMargins(4, 0, 6, 0)
                hl.setSpacing(6)
                if not d.icon.isNull():
                    ic = QLabel()
                    ic.setPixmap(d.icon.pixmap(QSize(46, 14)))
                    ic.setFixedWidth(ic.pixmap().width() // max(1, int(ic.devicePixelRatioF())) + 2)
                    hl.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
                lbl = _ElideLabel(d.name, Qt.TextElideMode.ElideMiddle)
                lbl.setToolTip(tip)
                lbl.setStyleSheet(f"color: {TEXT if d.selectable else DIM}; background: transparent;")
                hl.addWidget(lbl, 1, Qt.AlignmentFlag.AlignVCenter)
                if d.tag:
                    hl.addWidget(_tag(d.tag), 0, Qt.AlignmentFlag.AlignVCenter)
                name_item.setText("")
                name_item.setIcon(QIcon())
                tbl.setCellWidget(r, 1, holder)

    def _update_summary(self) -> None:
        picked_groups = [g for g in self._groups.values() if g.checked_count]
        picked = [d for g in picked_groups for d in g.depots if d.checked and d.selectable and d.tag != "license"]
        total = sum(d.size for d in picked)
        self._summary.setText(
            f"{len(picked_groups)} group{'s' if len(picked_groups) != 1 else ''} · "
            f"{len(picked)} depot{'s' if len(picked) != 1 else ''} · {_fmt_size(total)}"
        )

    # -------------------------------------------------------- interactions
    def _on_filter(self, text: str) -> None:
        self._group_filter = text.strip()
        self._fill_group_list()
        self._fill_depot_table()

    def _on_group_selected(self, current: Optional[QListWidgetItem], _prev=None) -> None:
        if current is None or current.data(R_HEADER):
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

    def _toggle_group(self, key: str) -> None:
        g = self._groups.get(key)
        if g is None:
            return
        sel = g.selectable
        target = g.checked_count < len(sel)
        for d in sel:
            if d.checked != target:
                self._click_source_row(d.row)
        self._current_group = key
        self._schedule_refresh()


__all__ = ["ContentDepotSelectionDialog"]
