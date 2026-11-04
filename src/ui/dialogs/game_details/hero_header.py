"""
Hero banner, title, badges, and layout helpers for GameDetailsDialogV2.
"""

import time
from PyQt6.QtCore import Qt, QSize, QUrl
from PyQt6.QtGui import QPixmap, QDesktopServices
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
    "QPushButton:hover {{ border-color: {hover}; }}"
)


def _snippet(text: str, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setFlat(True)
    b.setToolTip(tip)
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
        lbl.setText(f"Build {build}  !")
        lbl.setStyleSheet(SNIPPET_BASE.format(border="rgba(255,183,77,0.6)", color="#FFB74D", hover="#FFB74D"))
    else:
        lbl.setText(f"Build {build}" if build else "Build unknown")
        lbl.setStyleSheet(SNIPPET_BASE.format(border="rgba(255,255,255,0.14)", color="#E8E8EE", hover="rgba(255,255,255,0.3)"))
    lbl.setToolTip((tooltip + "\n" if tooltip else "") + "Click to check for updates")


def init_hero_v2(dialog, root) -> None:
    """Header: name with the mode logo, italic clickable AppID, and snippets
    for size, installed build (orange "!" when an update exists) and date."""
    from PyQt6.QtWidgets import QApplication

    is_atom = _is_atom_game(dialog)
    dialog.hero = HeroBanner(bg_hex=dialog.background_color)
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
    dialog.name_lbl = QLabel()
    dialog.name_lbl.setStyleSheet("font-size: 13pt; font-weight: bold; color: #FFFFFF; background: transparent;")
    dialog.name_lbl.setMaximumHeight(28)
    name_row.addWidget(dialog.name_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
    dialog.mode_logo_lbl = QLabel()
    dialog.mode_logo_lbl.setStyleSheet("background: transparent;")
    px = _mode_logo_pixmap(is_atom)
    if not px.isNull():
        dialog.mode_logo_lbl.setPixmap(px)
    dialog.mode_logo_lbl.setToolTip(
        "AT0-M: installed and updated by the Steam client" if is_atom else "Managed by ASSella"
    )
    name_row.addWidget(dialog.mode_logo_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
    dialog._denuvo_badge_lbl = QLabel()
    dialog._denuvo_badge_lbl.hide()  # placed in the snippet row below
    name_row.addStretch(1)
    dialog._ratings_row = name_row
    left_col.addLayout(name_row)

    dialog.appid_lbl = QPushButton(str(dialog.appid))
    dialog.appid_lbl.setFlat(True)
    dialog.appid_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.appid_lbl.setToolTip("Copy AppID")
    dialog.appid_lbl.setStyleSheet(
        "QPushButton { background: transparent; border: none; padding: 0; text-align: left;"
        " color: rgba(255,255,255,0.55); font-size: 11.5pt; font-style: italic; }"
        f"QPushButton:hover {{ color: {dialog.accent_color}; text-decoration: underline; }}"
    )

    def _copy_appid():
        QApplication.clipboard().setText(str(dialog.appid))
        dialog.appid_lbl.setText(f"{dialog.appid}  copied")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1200, lambda: dialog.appid_lbl.setText(str(dialog.appid)))

    dialog.appid_lbl.clicked.connect(_copy_appid)
    left_col.addWidget(dialog.appid_lbl, 0, Qt.AlignmentFlag.AlignLeft)
    top_row.addLayout(left_col, 1)

    right_badge_col = QVBoxLayout()
    right_badge_col.setContentsMargins(0, 2, 0, 0)
    dialog._proton_badge_lbl = QPushButton()
    dialog._proton_badge_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog._proton_badge_lbl.hide()
    dialog._proton_badge_lbl.clicked.connect(
        lambda: QDesktopServices.openUrl(QUrl(f"https://www.protondb.com/app/{dialog.appid}"))
    )
    dialog._proton_badge_btn = dialog._proton_badge_lbl
    top_row.addLayout(right_badge_col, 0)
    banner_layout.addLayout(top_row)

    update_title(dialog)

    # Snippets: size · build · installed date
    snip_row = QHBoxLayout()
    snip_row.setSpacing(6)
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
    snip_row.addWidget(dialog.size_val_lbl)

    dialog.build_val_lbl = _snippet("")
    dialog._hero_build_val_lbl = dialog.build_val_lbl
    dialog.build_val_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
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
    if not is_atom:
        dialog.build_val_lbl.clicked.connect(
            lambda: dialog._on_status_btn_clicked() if hasattr(dialog, "_on_status_btn_clicked") else None
        )
    else:
        dialog.build_val_lbl.setToolTip("Steam handles updates for this game.")
    snip_row.addWidget(dialog.build_val_lbl)

    date_str = get_installed_date(dialog)
    dialog.date_val_lbl = _snippet(date_str or "--/--/--",
                                   f"Installed build installed on {date_str}" if date_str else "Install date unknown")
    dialog.date_val_lbl.setEnabled(False)
    dialog.date_val_lbl.setVisible(bool(date_str))
    snip_row.addWidget(dialog.date_val_lbl)
    snip_row.addWidget(dialog._denuvo_badge_lbl)
    snip_row.addWidget(dialog._proton_badge_lbl)  # ProtonDB tier, as a snippet
    snip_row.addStretch(1)
    for w in (dialog.size_val_lbl, dialog.date_val_lbl):
        w.setStyleSheet(w.styleSheet() + " QPushButton:disabled { color: #E8E8EE; }")
    banner_layout.addSpacing(6)
    banner_layout.addLayout(snip_row)

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
            "platinum": ("PLATINUM", "#FFFFFF", "#1976D2", "#42A5F5", "#1E88E5"),
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
