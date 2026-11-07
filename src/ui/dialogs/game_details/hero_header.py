"""
Hero banner, title, badges, and layout helpers for GameDetailsDialogV2.
"""

import logging
import time
from PyQt6.QtCore import Qt, QSize, QUrl
from PyQt6.QtGui import QPixmap, QDesktopServices, QIcon
from PyQt6.sip import delete

logger = logging.getLogger(__name__)
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
)

from ui.dialogs.game_details.widgets import HeroBanner
from utils.image_fetcher import ImageFetcher
from utils.paths import Paths
from utils.update_status_cache import get_update_cache


def load_hero_image(dialog) -> None:
    """Asynchronously fetch or load cached hero background image."""
    if not hasattr(dialog.parent_window, "_image_cache"):
        return
    cached = dialog.parent_window._image_cache.get(dialog.appid)
    if cached:
        px = QPixmap()
        px.loadFromData(cached)
        if not px.isNull() and hasattr(dialog, "hero") and dialog.hero:
            dialog.hero.set_pixmap(px)
            from utils.color_utils import extract_dominant_color
            dom_col = extract_dominant_color(px, app_id=str(dialog.appid))
            if dom_col and dom_col.isValid() and hasattr(dialog, "_apply_dynamic_accent"):
                dialog._apply_dynamic_accent(dom_col)
        return
    if dialog.appid not in ("0", "N/A", "unknown") and ImageFetcher:
        url = ImageFetcher.get_header_image_url(dialog.appid)
        fetcher = ImageFetcher(url)

        def _done(data):
            if data:
                px = QPixmap()
                px.loadFromData(data)
                if not px.isNull() and hasattr(dialog, "hero") and dialog.hero:
                    dialog.hero.set_pixmap(px)
                    from utils.color_utils import extract_dominant_color
                    dom_col = extract_dominant_color(px, app_id=str(dialog.appid))
                    if dom_col and dom_col.isValid() and hasattr(dialog, "_apply_dynamic_accent"):
                        dialog._apply_dynamic_accent(dom_col)

        fetcher.finished.connect(_done)
        fetcher.finished.connect(
            lambda _, k=f"hero_{dialog.appid}": dialog._active_fetchers.pop(k, None)
        )
        fetcher.start()
        dialog._active_fetchers[f"hero_{dialog.appid}"] = fetcher


def _svg_icon(path: str, size: QSize, widget=None) -> QIcon:
    """An SVG rendered at the device pixel ratio (QIcon(svg) can come out soft)."""
    try:
        from PyQt6.QtGui import QPainter
        from PyQt6.QtSvg import QSvgRenderer

        dpr = widget.devicePixelRatioF() if widget is not None else 1.0
        dpr = max(1.0, dpr)
        px = QPixmap(round(size.width() * dpr), round(size.height() * dpr))
        px.fill(Qt.GlobalColor.transparent)
        r = QSvgRenderer(path)
        if not r.isValid():
            return QIcon(path)
        p = QPainter(px)
        r.render(p)
        p.end()
        px.setDevicePixelRatio(dpr)
        return QIcon(px)
    except Exception:
        return QIcon(path)


_PROTONDB_BADGE_QSS = (
    "QPushButton { border: 1px solid #ECEFF1; border-radius: 4px; padding: 2px 8px;"
    " font-size: 8.5pt; font-weight: bold; letter-spacing: 0.5px; }"
)
_badge_size_cache = None


def _protondb_badge_size() -> QSize:
    """Size of the ProtonDB badge showing PLATINUM (its widest common tier)."""
    global _badge_size_cache
    if _badge_size_cache is None:
        probe = QPushButton("PLATINUM")
        probe.setStyleSheet(_PROTONDB_BADGE_QSS)
        probe.ensurePolished()
        _badge_size_cache = probe.sizeHint()
        probe.deleteLater()
    return QSize(_badge_size_cache)


def _is_atom_game(dialog) -> bool:
    gd = dialog.game_data or {}
    return bool(
        gd.get("is_atom") or gd.get("is_vapor") or gd.get("is_plugin_game")
        or gd.get("update_status") in ("vapor", "at0m", "at0-m")
        or gd.get("source") in ("Vapor", "at0-m", "AT0-M")
    )


def _mode_logo_pixmap(is_atom: bool, size: int = 18) -> QPixmap:
    """Steam logo for AT0-M games, ASSella logo for ASSella-managed ones."""
    from pathlib import Path
    from utils.helpers import get_base_path

    name = "steam.png" if is_atom else "accela.png"
    for c in (
        Path(__file__).resolve().parents[3] / "res" / "logo" / name,
        get_base_path() / "Logo" / name,
    ):
        if c.is_file():
            px = QPixmap(str(c))
            if not px.isNull():
                return px.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
    return QPixmap()


def get_installed_date(dialog) -> str:
    """When the installed build was installed.

    ASSella installs: the newest depot manifest DepotDownloader wrote into
    .DepotDownloader (the same files the update check reads). AT0-M: the
    Steam manifest's LastUpdated. Nothing found: empty (no guessing from
    folder dates, which change for unrelated reasons).
    """
    import os
    import re
    from datetime import datetime

    ts = 0
    path = dialog.game_data.get("install_path")
    if path and not _is_atom_game(dialog):
        ddm = os.path.join(path, ".DepotDownloader")
        try:
            for f in os.listdir(ddm):
                if f.endswith(".manifest"):
                    ts = max(ts, int(os.path.getmtime(os.path.join(ddm, f))))
        except OSError:
            pass
    if not ts:
        try:
            from utils.steam_acf import find_appmanifest_path

            acf = find_appmanifest_path(dialog.game_data, str(dialog.appid))
            if acf and os.path.isfile(acf):
                with open(acf, "r", encoding="utf-8", errors="ignore") as f:
                    m = re.search(r'"LastUpdated"\s+"(\d+)"', f.read())
                if m:
                    ts = int(m.group(1))
        except Exception:
            ts = 0
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).strftime("%d/%m/%y")


SNIPPET_BASE = (
    "QPushButton {{ background: rgba(0,0,0,0.35); border: 1px solid {border}; border-radius: 5px;"
    " color: {color}; font-size: 8.5pt; font-weight: 600; padding: 2px 8px; text-align: left; }}"
    # Information only: no hover change, so nothing looks clickable ({hover} unused).
)


def _snippet(text: str, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setFlat(True)
    b.setToolTip(tip)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setStyleSheet(SNIPPET_BASE.format(border="rgba(255,255,255,0.14)", color="#E8E8EE", hover="rgba(255,255,255,0.3)"))
    return b


def set_build_snippet(dialog, build: str, state: str, tooltip: str = "") -> None:
    """state: 'ok' | 'update' | 'latest' (not installed) | 'unknown'.

    An update is shown as an orange snippet with "!" after the build ID; the
    Update button below the header does the rest.
    """
    lbl = getattr(dialog, "build_val_lbl", None)
    if lbl is None:
        return
    if state == "update" and _is_atom_game(dialog):
        state = "ok"  # Steam updates AT0-M games; nothing to flag here
    if state == "update":
        lbl.setText(f"{build}  !")
        lbl.setStyleSheet(SNIPPET_BASE.format(border="rgba(255,183,77,0.6)", color="#FFB74D", hover="#FFB74D"))
    else:
        lbl.setText(build or "—")
        lbl.setStyleSheet(SNIPPET_BASE.format(border="rgba(255,255,255,0.14)", color="#E8E8EE", hover="rgba(255,255,255,0.3)"))
    lbl.setToolTip(tooltip or "Installed build")


def _clear_hero_layout(dialog):
    if hasattr(dialog, "hero") and dialog.hero and dialog.hero.layout() is not None:
        l = dialog.hero.layout()
        def _drain(lay):
            while lay.count():
                item = lay.takeAt(0)
                sub = item.layout()
                if sub:
                    _drain(sub)
                    delete(sub)
        _drain(l)
        delete(l)


def apply_classic_header(dialog) -> None:
    """Classic header layout used when SteamGridDB logos are disabled or unavailable."""
    _clear_hero_layout(dialog)
    dialog.hero.setFixedHeight(100)

    banner_layout = QVBoxLayout(dialog.hero)
    banner_layout.setContentsMargins(14, 8, 14, 10)
    banner_layout.setSpacing(3)

    top_row = QHBoxLayout()
    top_row.setContentsMargins(0, 0, 0, 0)
    top_row.setSpacing(10)

    left_col = QVBoxLayout()
    left_col.setContentsMargins(0, 0, 0, 0)
    left_col.setSpacing(0)

    name_row = QHBoxLayout()
    name_row.setSpacing(8)
    dialog.name_lbl.show()
    name_row.addWidget(dialog.name_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
    dialog.mode_logo_lbl.show()
    name_row.addWidget(dialog.mode_logo_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
    name_row.addStretch(1)
    dialog._ratings_row = name_row
    left_col.addLayout(name_row)

    dialog.appid_lbl.show()
    left_col.addWidget(dialog.appid_lbl, 0, Qt.AlignmentFlag.AlignLeft)
    top_row.addLayout(left_col, 1)

    top_row.addWidget(dialog._proton_badge_lbl, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
    banner_layout.addLayout(top_row)

    # Snippets row: size · build · installed date · denuvo · steamdb
    snip_row = QHBoxLayout()
    snip_row.setSpacing(6)
    snip_row.addWidget(dialog.size_val_lbl)
    snip_row.addWidget(dialog.build_val_lbl)
    snip_row.addWidget(dialog.date_val_lbl)
    snip_row.addWidget(dialog._denuvo_badge_lbl)
    snip_row.addStretch(1)
    snip_row.addWidget(dialog._steamdb_btn, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

    banner_layout.addSpacing(6)
    banner_layout.addLayout(snip_row)
    dialog.logo_lbl.hide()


# ── SteamGridDB title logo ───────────────────────────────────────────────
# Logos are cached as <base>/image_cache/logos/<appid>.png. A game SteamGridDB
# has no logo for gets a <appid>.none marker so it is not asked again for a week.
LOGO_BOX_W = 230          # widest a logo may be
LOGO_BOX_H = 84           # tallest a logo may be
LOGO_TARGET_AREA = 230 * 62  # wide and square logos end up with similar visual weight
_LOGO_MISS_TTL = 7 * 86400


def _logo_cache_dir():
    from utils.helpers import get_base_path

    d = get_base_path() / "image_cache" / "logos"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def _logo_miss_recent(appid: str) -> bool:
    marker = _logo_cache_dir() / f"{appid}.none"
    try:
        return marker.is_file() and (time.time() - marker.stat().st_mtime) < _LOGO_MISS_TTL
    except OSError:
        return False


def cached_logo(appid: str) -> QPixmap:
    f = _logo_cache_dir() / f"{appid}.png"
    if f.is_file():
        px = QPixmap(str(f))
        if not px.isNull():
            return px
    return QPixmap()


def _trim_transparent(px: QPixmap) -> QPixmap:
    """Crop fully transparent margins, so padding in the source does not shrink the logo."""
    img = px.toImage()
    if not img.hasAlphaChannel():
        return px
    w, h = img.width(), img.height()
    step = max(1, max(w, h) // 200)  # ~200 samples across: fast enough for the GUI thread
    left, top, right, bottom = w, h, -1, -1
    for y in range(0, h, step):
        for x in range(0, w, step):
            if (img.pixel(x, y) >> 24) & 0xFF > 12:
                if x < left: left = x
                if x > right: right = x
                if y < top: top = y
                if y > bottom: bottom = y
    if right < 0:
        return px
    left, top = max(0, left - step), max(0, top - step)
    right, bottom = min(w - 1, right + step), min(h - 1, bottom + step)
    if (right - left + 1) >= w - 2 and (bottom - top + 1) >= h - 2:
        return px
    return px.copy(left, top, right - left + 1, bottom - top + 1)


def fit_logo(px: QPixmap, dpr: float = 1.0) -> QPixmap:
    """Trim, then scale so every logo reads at a similar size.

    Fitting into a box alone makes square or tall logos tiny and lets wide ones
    fill everything; scaling to a target area, capped by the box, evens that out.
    Small sources are scaled up as well as down.
    """
    px = _trim_transparent(px)
    w, h = max(1, px.width()), max(1, px.height())
    scale = min(LOGO_BOX_W / w, LOGO_BOX_H / h, (LOGO_TARGET_AREA / (w * h)) ** 0.5)
    tw, th = max(1, round(w * scale)), max(1, round(h * scale))
    out = px.scaled(round(tw * dpr), round(th * dpr), Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
    out.setDevicePixelRatio(dpr)
    return out


def _pick_logo_asset(assets: list):
    """Highest-scored PNG that is not tiny; else the best of what there is."""
    pngs = [a for a in assets if str(a.get("mime", "")).endswith("png") or str(a.get("url", "")).endswith(".png")]
    pool = pngs or assets
    big = [a for a in pool if int(a.get("width") or 0) >= 300]
    return (big or pool or [None])[0]


def apply_logo_header(dialog, pixmap: QPixmap) -> None:
    """Refined logo header layout: bounded logo on left, 5-line metadata stack, ProtonDB top-right, SteamDB bottom-right."""
    _clear_hero_layout(dialog)
    dialog.hero.setFixedHeight(116)

    banner_layout = QHBoxLayout(dialog.hero)
    banner_layout.setContentsMargins(16, 8, 16, 8)
    banner_layout.setSpacing(14)

    # 1. Logo on the left, trimmed and scaled to a similar visual size (fit_logo)
    dialog.name_lbl.hide()
    dpr = dialog.devicePixelRatioF() if hasattr(dialog, "devicePixelRatioF") else 1.0
    dialog.logo_lbl.setPixmap(fit_logo(pixmap, dpr))
    # Fixed slot so the metadata column does not jump between games.
    dialog.logo_lbl.setFixedSize(LOGO_BOX_W, LOGO_BOX_H + 8)
    dialog.logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dialog.logo_lbl.setToolTip(dialog.game_data.get("game_name", ""))
    dialog.logo_lbl.show()
    banner_layout.addWidget(dialog.logo_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

    # 2. Metadata column stacked directly to the right:
    # Line 1: assella/steam logo
    # Line 2: appid
    # Line 3: size
    # Line 4: buildid  date
    # Line 5: denuvo (if it has)
    meta_col = QVBoxLayout()
    meta_col.setContentsMargins(0, 0, 0, 0)
    meta_col.setSpacing(2)
    meta_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)

    dialog.mode_logo_lbl.show()
    meta_col.addWidget(dialog.mode_logo_lbl, 0, Qt.AlignmentFlag.AlignLeft)

    dialog.appid_lbl.show()
    meta_col.addWidget(dialog.appid_lbl, 0, Qt.AlignmentFlag.AlignLeft)

    dialog.size_val_lbl.show()
    meta_col.addWidget(dialog.size_val_lbl, 0, Qt.AlignmentFlag.AlignLeft)

    build_date_row = QHBoxLayout()
    build_date_row.setContentsMargins(0, 0, 0, 0)
    build_date_row.setSpacing(6)
    build_date_row.addWidget(dialog.build_val_lbl)
    build_date_row.addWidget(dialog.date_val_lbl)
    build_date_row.addStretch(1)
    meta_col.addLayout(build_date_row)

    meta_col.addWidget(dialog._denuvo_badge_lbl, 0, Qt.AlignmentFlag.AlignLeft)

    banner_layout.addLayout(meta_col, 0)
    banner_layout.addStretch(1)

    # 3. Right column: ProtonDB on top right, SteamDB on bottom right
    right_col = QVBoxLayout()
    right_col.setContentsMargins(0, 0, 0, 0)
    right_col.setSpacing(0)
    right_col.addWidget(dialog._proton_badge_lbl, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
    right_col.addStretch(1)
    right_col.addWidget(dialog._steamdb_btn, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

    banner_layout.addLayout(right_col, 0)


def init_hero_v2(dialog, root) -> None:
    """Header: name with the mode logo, italic clickable AppID, and snippets
    for size, installed build (orange "!" when an update exists) and date."""
    from PyQt6.QtWidgets import QApplication

    is_atom = _is_atom_game(dialog)
    dialog.hero = HeroBanner(bg_hex=dialog.background_color)

    dialog.logo_lbl = QLabel()
    dialog.logo_lbl.setStyleSheet("background: transparent;")
    dialog.logo_lbl.hide()

    dialog.name_lbl = QLabel()
    dialog.name_lbl.setStyleSheet("font-size: 13pt; font-weight: bold; color: #FFFFFF; background: transparent;")
    dialog.name_lbl.setMaximumHeight(28)

    dialog.mode_logo_lbl = QLabel()
    dialog.mode_logo_lbl.setStyleSheet("background: transparent;")
    px = _mode_logo_pixmap(is_atom)
    if not px.isNull():
        dialog.mode_logo_lbl.setPixmap(px)
    dialog.mode_logo_lbl.setToolTip(
        "AT0-M: installed and updated by the Steam client" if is_atom else "Managed by ASSella"
    )

    dialog.appid_lbl = QPushButton(str(dialog.appid))
    dialog.appid_lbl.setFlat(True)
    dialog.appid_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.appid_lbl.setToolTip("Copy AppID")
    dialog.appid_lbl.setStyleSheet(
        "QPushButton { background: transparent; border: none; padding: 0; text-align: left;"
        " color: rgba(255,255,255,0.55); font-size: 11pt; font-style: italic; }"
        f"QPushButton:hover {{ color: {dialog.accent_color}; text-decoration: underline; }}"
    )

    def _copy_appid():
        QApplication.clipboard().setText(str(dialog.appid))
        dialog.appid_lbl.setText(f"{dialog.appid}  copied")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1200, lambda: dialog.appid_lbl.setText(str(dialog.appid)))

    dialog.appid_lbl.clicked.connect(_copy_appid)

    # Snippets: size · build · installed date
    if dialog.parent_window and hasattr(dialog.parent_window, "_format_size"):
        size_str = dialog.parent_window._format_size(dialog.game_data.get("size_on_disk", 0))
    else:
        sb = float(dialog.game_data.get("size_on_disk", 0) or 0)
        size_str = next(
            (f"{sb / f:.1f} {u}" for f, u in ((1024 ** 3, "GB"), (1024 ** 2, "MB"), (1024, "KB")) if sb >= f),
            f"{int(sb)} B",
        )
    dialog.size_val_lbl = _snippet(size_str, "Size on disk")
    dialog.size_val_lbl.setEnabled(False)

    dialog.build_val_lbl = _snippet("")
    dialog._hero_build_val_lbl = dialog.build_val_lbl
    installed_bid = dialog._get_installed_buildid()
    state = "unknown"
    if installed_bid:
        state = "ok"
        latest = str(dialog.game_data.get("buildid", "") or "")
        try:
            if latest and int(installed_bid) < int(latest):
                state = "update"
        except (ValueError, TypeError):
            pass
    set_build_snippet(dialog, installed_bid, state, f"Installed build {installed_bid}" if installed_bid else "")
    dialog.build_val_lbl.setCursor(Qt.CursorShape.ArrowCursor)
    dialog.build_val_lbl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    dialog.build_val_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

    date_str = get_installed_date(dialog)
    dialog.date_val_lbl = _snippet(date_str or "--/--/--",
                                   f"Installed build installed on {date_str}" if date_str else "Install date unknown")
    dialog.date_val_lbl.setEnabled(False)
    dialog.date_val_lbl.setVisible(bool(date_str))

    for w in (dialog.size_val_lbl, dialog.date_val_lbl):
        w.setStyleSheet(w.styleSheet() + " QPushButton:disabled { color: #E8E8EE; }")

    dialog._denuvo_badge_lbl = QLabel()
    dialog._denuvo_badge_lbl.hide()

    dialog._proton_badge_lbl = QPushButton()
    dialog._proton_badge_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog._proton_badge_lbl.hide()
    dialog._proton_badge_lbl.clicked.connect(
        lambda: QDesktopServices.openUrl(QUrl(f"https://www.protondb.com/app/{dialog.appid}"))
    )
    dialog._proton_badge_btn = dialog._proton_badge_lbl

    dialog._steamdb_btn = QPushButton()
    dialog._steamdb_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog._steamdb_btn.setToolTip(f"Open {dialog.appid} on SteamDB (https://steamdb.info/app/{dialog.appid}/)")
    # Same size as the ProtonDB badge (its "PLATINUM" size), with the white
    # lockup on a solid plate so it stays legible over any hero art.
    dialog._steamdb_btn.setFixedSize(_protondb_badge_size())
    icon_path = Paths.icon("steamdb-lockup.svg")
    if not icon_path.exists():
        icon_path = Paths.resource("steamdb-lockup.svg")
    if icon_path.exists():
        icon_size = QSize(60, 12)
        dialog._steamdb_btn.setIcon(_svg_icon(str(icon_path), icon_size, dialog))
        dialog._steamdb_btn.setIconSize(icon_size)
    else:
        dialog._steamdb_btn.setText("SteamDB")

    dialog._steamdb_btn.setStyleSheet("""
        QPushButton {
            background-color: #16191f;
            border: 1px solid rgba(255, 255, 255, 0.22);
            border-radius: 4px;
            padding: 2px 8px;
            color: #FFFFFF;
            font-size: 8.5pt;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: #232833;
            border-color: rgba(255, 255, 255, 0.45);
        }
        QPushButton:pressed {
            background-color: #0f1115;
        }
    """)
    dialog._steamdb_btn.clicked.connect(
        lambda: QDesktopServices.openUrl(QUrl(f"https://steamdb.info/app/{dialog.appid}/"))
    )

    # A cached logo is a local file read, so it is shown straight away; a
    # download only starts after the page is up (load_title_logo).
    from utils.settings import get_settings
    px_cached = QPixmap()
    if get_settings().value("steamgriddb_fetch_logos", False, type=bool):
        px_cached = cached_logo(str(dialog.appid))
    if not px_cached.isNull():
        apply_logo_header(dialog, px_cached)
    else:
        apply_classic_header(dialog)

    update_title(dialog)

    # Kept for older callers; no longer shown.
    dialog.cached_val_lbl = QLabel(get_manifest_age(dialog))
    dialog.lua_val_lbl = QLabel(get_lua_age(dialog))

    load_hero_image(dialog)
    root.addWidget(dialog.hero)


def init_hero_legacy(dialog, root) -> None:
    """Legacy compact 65px header."""
    dialog.hero = HeroBanner(bg_hex=dialog.background_color)
    dialog.hero.setMinimumHeight(70)
    banner_layout = QHBoxLayout(dialog.hero)
    banner_layout.setContentsMargins(14, 6, 180, 6)
    banner_layout.setSpacing(0)

    name_col = QVBoxLayout()
    name_col.setSpacing(2)

    dialog._ratings_row = QHBoxLayout()
    dialog._ratings_row.setSpacing(6)
    dialog._ratings_row.setContentsMargins(0, 0, 0, 0)
    dialog._denuvo_badge_lbl = QLabel()
    dialog._denuvo_badge_lbl.hide()
    dialog._ratings_row.addWidget(dialog._denuvo_badge_lbl)
    dialog._ratings_row.addStretch()
    name_col.addLayout(dialog._ratings_row)

    dialog.name_lbl = QLabel()
    dialog.name_lbl.setStyleSheet(
        "font-size: 12.5pt; font-weight: bold; color: #FFFFFF; background: transparent;"
    )
    dialog.name_lbl.setWordWrap(True)
    name_col.addWidget(dialog.name_lbl)

    dialog.appid_lbl = QLabel(f"App ID: {dialog.appid}")
    dialog.appid_lbl.setStyleSheet(
        "font-size: 8pt; color: rgba(255, 255, 255, 0.4); background: transparent;"
    )
    name_col.addWidget(dialog.appid_lbl)
    name_col.addStretch()
    banner_layout.addLayout(name_col, 1)

    dialog._proton_badge_lbl = QPushButton()
    dialog._proton_badge_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog._proton_badge_lbl.hide()
    dialog._proton_badge_lbl.clicked.connect(
        lambda: QDesktopServices.openUrl(QUrl(f"https://www.protondb.com/app/{dialog.appid}"))
    )
    dialog._proton_badge_btn = dialog._proton_badge_lbl
    banner_layout.addWidget(dialog._proton_badge_lbl, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

    update_title(dialog)

    load_hero_image(dialog)
    root.addWidget(dialog.hero)


def update_title(dialog) -> None:
    """Update the hero banner title; Denuvo + ProtonDB shown as separate pill badges."""
    from utils.dlc_helpers import is_dlc_only_mode

    is_vm = bool(
        dialog.game_data.get("is_atom")
        or dialog.game_data.get("is_vapor")
        or dialog.game_data.get("is_plugin_game")
        or dialog.game_data.get("update_status") in ("vapor", "at0m", "at0-m")
        or dialog.game_data.get("source") in ("Vapor", "at0-m", "AT0-M")
    )
    installed_branch = "public" if is_vm else (
        dialog.settings.value(f"installed_branch/{dialog.appid}", "public", type=str) if dialog.settings else "public"
    )
    display_parts = [dialog.game_data.get("game_name", "Unknown")]
    if installed_branch and installed_branch != "public":
        display_parts.append(f"({installed_branch})")
    if is_dlc_only_mode(dialog.appid):
        display_parts.append("[DLC ONLY]")

    if hasattr(dialog, "name_lbl") and dialog.name_lbl:
        dialog.name_lbl.setText(" ".join(display_parts))

    # --- Denuvo badge ---
    if hasattr(dialog, "_denuvo_badge_lbl") and dialog._denuvo_badge_lbl:
        from core.ratings import get_denuvo_status
        denuvo = get_denuvo_status(dialog.appid)
        if denuvo == "cracked":
            d_color, d_tip = "#81C784", "Denuvo: cracked"
        elif denuvo == "uncracked":
            d_color, d_tip = "#E57373", "Denuvo: not cracked"
        elif denuvo:
            d_color, d_tip = "#FFB74D", f"Denuvo: {denuvo}"
        else:
            d_color = None

        if d_color:
            dialog._denuvo_badge_lbl.setText("Denuvo")
            dialog._denuvo_badge_lbl.setToolTip(d_tip)
            dialog._denuvo_badge_lbl.setStyleSheet(
                f"color: {d_color}; background: rgba(0,0,0,0.35); border: 1px solid {d_color};"
                " border-radius: 5px; padding: 2px 8px; font-size: 8.5pt; font-weight: 600;"
            )
            dialog._denuvo_badge_lbl.show()
        else:
            dialog._denuvo_badge_lbl.hide()

    # --- ProtonDB badge ---
    if hasattr(dialog, "_proton_badge_lbl") and dialog._proton_badge_lbl:
        from core.ratings import get_protondb_tier
        tier = get_protondb_tier(dialog.appid)
        _tier_map = {
            "platinum": ("PLATINUM", "#263238", "#CFD8DC", "#ECEFF1", "#B0BEC5"),
            "gold":     ("GOLD",     "#111111", "#F59E0B", "#FBBF24", "#D97706"),
            "silver":   ("SILVER",   "#FFFFFF", "#546E7A", "#78909C", "#607D8B"),
            "bronze":   ("BRONZE",   "#FFFFFF", "#C2410C", "#FB923C", "#EA580C"),
            "borked":   ("BORKED",   "#FFFFFF", "#DC2626", "#F87171", "#EF4444"),
            "native":   ("NATIVE",   "#FFFFFF", "#16A34A", "#4ADE80", "#22C55E"),
        }
        if tier and tier in _tier_map:
            p_text, p_color, p_bg, p_border, p_hover = _tier_map[tier]
            dialog._proton_badge_lbl.setText(p_text)
            dialog._proton_badge_lbl.setStyleSheet(f"""
                QPushButton {{
                    color: {p_color};
                    background-color: {p_bg};
                    border: 1px solid {p_border};
                    border-radius: 4px;
                    padding: 2px 8px;
                    font-size: 8.5pt;
                    font-weight: bold;
                    letter-spacing: 0.5px;
                    outline: none;
                }}
                QPushButton:hover {{
                    background-color: {p_hover};
                }}
                QPushButton:pressed {{
                    background-color: {p_bg};
                }}
            """)
            dialog._proton_badge_lbl.setToolTip(f"Open {p_text} rating on ProtonDB (https://www.protondb.com/app/{dialog.appid})")
            dialog._proton_badge_lbl.show()
        elif tier is None:
            dialog._proton_badge_lbl.setText("FETCHING...")
            dialog._proton_badge_lbl.setStyleSheet("""
                QPushButton {
                    color: #D1D5DB;
                    background-color: #374151;
                    border: 1px solid #4B5563;
                    border-radius: 4px;
                    padding: 2px 8px;
                    font-size: 8.5pt;
                    font-weight: bold;
                    letter-spacing: 0.5px;
                    outline: none;
                }
                QPushButton:hover {
                    background-color: #4B5563;
                }
            """)
            dialog._proton_badge_lbl.setToolTip(f"Checking ProtonDB rating for AppID {dialog.appid}...")
            dialog._proton_badge_lbl.show()
        else:
            dialog._proton_badge_lbl.hide()


def thin_line() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("background: rgba(255, 255, 255, 0.031); border: none; max-height: 1px;")
    return f


def section_title(dialog_or_text, text_or_color=None) -> QLabel:
    if isinstance(dialog_or_text, str):
        text = dialog_or_text
        accent_color = text_or_color or "#a1c9fd"
    else:
        text = text_or_color or ""
        accent_color = getattr(dialog_or_text, "accent_color", "#a1c9fd")
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        f"color: {accent_color}; font-size: 8px; font-weight: bold;"
        "letter-spacing: 1px; border: none; background: transparent;"
    )
    return lbl


def card_btn(text: str, tooltip: str = None) -> QPushButton:
    b = QPushButton(text)
    b.setFixedHeight(25)
    if tooltip:
        b.setToolTip(tooltip)
    return b


def get_manifest_age(dialog) -> str:
    if dialog.appid in ("0", "N/A", "unknown"):
        return "N/A"
    from core.morrenus_api import get_manifest_zip_path, get_selected_branch
    fpath = get_manifest_zip_path(dialog.appid, get_selected_branch(dialog.appid))
    if fpath.exists():
        try:
            return format_time_diff(fpath.stat().st_mtime)
        except Exception:
            pass
    return "Not cached"


def get_lua_age(dialog) -> str:
    if dialog.appid in ("0", "N/A", "unknown"):
        return "N/A"
    try:
        from managers.depot_key_manager import DepotKeyManager
        dkm = DepotKeyManager()
        ts = dkm.get_key_updated_at(dialog.appid)
        if ts:
            return format_time_diff(ts)
    except Exception:
        pass
    return "Not cached"


def get_last_checked(dialog) -> str:
    if dialog.appid in ("0", "N/A", "unknown"):
        return "Never"
    cache = get_update_cache()
    if cache:
        entry = cache._cache.get(str(dialog.appid))
        if entry and entry.get("updated_at"):
            try:
                return format_time_diff(entry.get("updated_at"))
            except Exception:
                pass
    return "Never"


def format_time_diff(ts) -> str:
    diff = int(time.time() - ts)
    if diff < 0:
        diff = 0
    if diff < 60:
        return "just now"
    elif diff < 3600:
        return f"{diff // 60}min ago"
    elif diff < 86400:
        return f"{diff // 3600}hr ago"
    elif diff < 2592000:
        return f"{diff // 86400}d ago"
    elif diff < 31536000:
        return f"{diff // 2592000}mo ago"
    else:
        return f"{diff // 31536000}yr ago"


def load_title_logo(dialog) -> None:
    """Fetch the SteamGridDB title logo once the page is up.

    Called (once) after the details dialog has shown. Does nothing when the
    logo is already on screen from the cache, when the feature is off, or when
    SteamGridDB had no logo recently. The download runs on a worker thread and
    the result is handed to the GUI thread with post_to_main_thread (a QTimer
    started from a plain Python thread never fires).
    """
    from utils.settings import get_settings

    settings = get_settings()
    if not settings.value("steamgriddb_fetch_logos", False, type=bool):
        return
    appid = str(getattr(dialog, "appid", "") or "")
    if not appid or appid in ("0", "N/A", "unknown"):
        return
    from PyQt6 import sip

    if sip.isdeleted(dialog) or getattr(dialog, "_logo_fetch_started", False):
        return
    dialog._logo_fetch_started = True
    if not cached_logo(appid).isNull() or _logo_miss_recent(appid):
        return

    import threading

    from utils.main_thread import post_to_main_thread

    cache_dir = _logo_cache_dir()

    def _apply(data: bytes) -> None:
        if sip.isdeleted(dialog) or not dialog.isVisible() or sip.isdeleted(dialog.hero):
            return
        px = QPixmap()
        if px.loadFromData(data) and not px.isNull():
            apply_logo_header(dialog, px)
            if hasattr(dialog, "_fit_height_for_tab"):
                dialog._fit_height_for_tab(0)

    def _worker() -> None:
        try:
            import requests

            from utils import steamgriddb as sgdb

            asset = _pick_logo_asset(sgdb.get_assets("logos", appid, settings=settings))
            url = asset.get("url") if asset else None
            if not url:
                (cache_dir / f"{appid}.none").touch()
                return
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200 or not resp.content:
                return  # transient; try again next time
            tmp = cache_dir / f"{appid}.png.tmp"
            tmp.write_bytes(resp.content)
            tmp.replace(cache_dir / f"{appid}.png")
            post_to_main_thread(_apply, resp.content)
        except Exception as e:
            logger.debug(f"[HeroHeader] Title logo for {appid} failed: {e}")

    threading.Thread(target=_worker, name=f"sgdb-logo-{appid}", daemon=True).start()
