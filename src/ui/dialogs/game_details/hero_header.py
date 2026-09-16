"""
Hero banner, title, badges, and layout helpers for GameDetailsDialogV2.
"""

import time
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap
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

        fetcher.finished.connect(_done)
        fetcher.finished.connect(
            lambda _, k=f"hero_{dialog.appid}": dialog._active_fetchers.pop(k, None)
        )
        fetcher.start()
        dialog._active_fetchers[f"hero_{dialog.appid}"] = fetcher


def init_hero_v2(dialog, root) -> None:
    """Initialize modern 120px tall hero header with inline game stats."""
    dialog.hero = HeroBanner(bg_hex=dialog.background_color)
    dialog.hero.setFixedHeight(125)
    banner_layout = QVBoxLayout(dialog.hero)
    banner_layout.setContentsMargins(14, 8, 120, 8)
    banner_layout.setSpacing(4)

    left_col = QVBoxLayout()
    left_col.setContentsMargins(0, 0, 0, 0)
    left_col.setSpacing(2)

    # Top-left Ratings badges row (Denuvo + ProtonDB pills)
    dialog._ratings_row = QHBoxLayout()
    dialog._ratings_row.setSpacing(6)
    dialog._ratings_row.setContentsMargins(0, 0, 0, 0)
    dialog._denuvo_badge_lbl = QLabel()
    dialog._denuvo_badge_lbl.hide()
    dialog._proton_badge_lbl = QLabel()
    dialog._proton_badge_lbl.hide()
    dialog._ratings_row.addWidget(dialog._denuvo_badge_lbl)
    dialog._ratings_row.addWidget(dialog._proton_badge_lbl)
    dialog._ratings_row.addStretch()
    left_col.addLayout(dialog._ratings_row)

    dialog.name_lbl = QLabel()
    dialog.name_lbl.setStyleSheet(
        "font-size: 12.5pt; font-weight: bold; color: #FFFFFF; background: transparent;"
    )
    dialog.name_lbl.setWordWrap(True)
    dialog.name_lbl.setMaximumHeight(38)
    left_col.addWidget(dialog.name_lbl)

    dialog.appid_lbl = QLabel(f"App ID: {dialog.appid}")
    dialog.appid_lbl.setStyleSheet(
        "font-size: 8.5pt; color: rgba(255, 255, 255, 0.706); background: transparent; font-weight: bold;"
    )
    left_col.addWidget(dialog.appid_lbl)

    banner_layout.addLayout(left_col)

    update_title(dialog)

    # Stats row — horizontal labels under name
    stats_row = QHBoxLayout()
    stats_row.setSpacing(24)

    def _stat_item(label_text, value_text, value_color=None):
        item_widget = QVBoxLayout()
        item_widget.setSpacing(2)
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color: {dialog.accent_color}; font-size: 8.5pt; background: transparent; font-weight: bold;")
        val = QLabel(value_text)
        val.setStyleSheet(
            f"color: {value_color or dialog.accent_color}; font-size: 9.5pt; font-weight: bold; background: transparent;"
        )
        item_widget.addWidget(lbl)
        item_widget.addWidget(val)
        return item_widget, val

    if dialog.parent_window and hasattr(dialog.parent_window, "_format_size"):
        size_str = dialog.parent_window._format_size(dialog.game_data.get("size_on_disk", 0))
    else:
        sb = dialog.game_data.get("size_on_disk", 0) or 0
        if sb < 1024:
            size_str = f"{sb} B"
        elif sb < 1024 * 1024:
            size_str = f"{sb / 1024:.1f} KB"
        elif sb < 1024 * 1024 * 1024:
            size_str = f"{sb / (1024 * 1024):.1f} MB"
        else:
            size_str = f"{sb / (1024 * 1024 * 1024):.2f} GB"

    ri, dialog.size_val_lbl = _stat_item("SIZE", size_str)
    stats_row.addLayout(ri)

    ri, dialog.cached_val_lbl = _stat_item("MANIFEST", get_manifest_age(dialog))
    stats_row.addLayout(ri)

    installed_bid = dialog._get_installed_buildid()
    bid_str = installed_bid if installed_bid else "Unknown"
    initial_build_color = None
    if installed_bid:
        cached_bid = str(dialog.game_data.get("buildid", "")) if hasattr(dialog, "game_data") and isinstance(dialog.game_data, dict) else ""
        if cached_bid:
            is_old = False
            try:
                is_old = int(installed_bid) < int(cached_bid)
            except (ValueError, TypeError):
                is_old = (cached_bid != installed_bid)
            initial_build_color = "#FFB84D" if is_old else "#46b464"

    ri, dialog.build_val_lbl = _stat_item("BUILD", bid_str, value_color=initial_build_color)
    dialog._hero_build_val_lbl = dialog.build_val_lbl
    if installed_bid:
        tip = f"Installed Build: {installed_bid}"
        if initial_build_color == "#FFB84D":
            tip += " (Update available)"
        elif initial_build_color == "#46b464":
            tip += " (Up to date)"
        dialog.build_val_lbl.setToolTip(tip)
    stats_row.addLayout(ri)

    ri, dialog.lua_val_lbl = _stat_item("LUA", get_lua_age(dialog))
    stats_row.addLayout(ri)

    stats_row.addStretch()
    banner_layout.addLayout(stats_row)
    banner_layout.addStretch()

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

    # Top-left Ratings badges row (Denuvo + ProtonDB pills)
    dialog._ratings_row = QHBoxLayout()
    dialog._ratings_row.setSpacing(6)
    dialog._ratings_row.setContentsMargins(0, 0, 0, 0)
    dialog._denuvo_badge_lbl = QLabel()
    dialog._denuvo_badge_lbl.hide()
    dialog._proton_badge_lbl = QLabel()
    dialog._proton_badge_lbl.hide()
    dialog._ratings_row.addWidget(dialog._denuvo_badge_lbl)
    dialog._ratings_row.addWidget(dialog._proton_badge_lbl)
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
    banner_layout.addLayout(name_col)
    banner_layout.addStretch()

    update_title(dialog)

    load_hero_image(dialog)
    root.addWidget(dialog.hero)


def update_title(dialog) -> None:
    """Update the hero banner title; Denuvo + ProtonDB shown as separate pill badges."""
    from utils.dlc_helpers import is_dlc_only_mode

    installed_branch = dialog.settings.value(f"installed_branch/{dialog.appid}", "public", type=str) if dialog.settings else "public"
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
            d_text, d_color, d_bg = "Denuvo Cracked",    "#81C784", "rgba(129,199,132,0.20)"
        elif denuvo == "hypervisor":
            d_text, d_color, d_bg = "Denuvo Hypervisor", "#FFA726", "rgba(255,167,38,0.18)"
        elif denuvo == "uncracked":
            d_text, d_color, d_bg = "Denuvo Uncracked",  "#E57373", "rgba(229,115,115,0.20)"
        else:
            d_text = None

        if d_text:
            dialog._denuvo_badge_lbl.setText(d_text)
            dialog._denuvo_badge_lbl.setStyleSheet(
                f"color: {d_color}; background-color: {d_bg}; "
                f"border-radius: 4px; padding: 2px 8px; "
                f"font-size: 9pt; font-weight: bold; border: none;"
            )
            dialog._denuvo_badge_lbl.show()
        else:
            dialog._denuvo_badge_lbl.hide()

    # --- ProtonDB badge ---
    if hasattr(dialog, "_proton_badge_lbl") and dialog._proton_badge_lbl:
        from core.ratings import get_protondb_tier
        tier = get_protondb_tier(dialog.appid)
        _tier_map = {
            "platinum": ("PLATINUM", "#90CAF9", "rgba(33, 150, 243, 0.15)", "rgba(144, 202, 249, 0.30)"),
            "gold":     ("GOLD",     "#FFE082", "rgba(255, 193, 7, 0.15)",   "rgba(255, 224, 130, 0.30)"),
            "silver":   ("SILVER",   "#CFD8DC", "rgba(144, 164, 174, 0.15)", "rgba(207, 216, 220, 0.30)"),
            "bronze":   ("BRONZE",   "#FFAB91", "rgba(255, 112, 67, 0.15)",  "rgba(255, 171, 145, 0.30)"),
            "borked":   ("BORKED",   "#EF9A9A", "rgba(239, 83, 80, 0.18)",   "rgba(239, 154, 154, 0.35)"),
            "native":   ("NATIVE",   "#A5D6A7", "rgba(76, 175, 80, 0.15)",   "rgba(165, 214, 167, 0.30)"),
        }
        if tier and tier in _tier_map:
            p_text, p_color, p_bg, p_border = _tier_map[tier]
            dialog._proton_badge_lbl.setText(p_text)
            dialog._proton_badge_lbl.setStyleSheet(
                f"color: {p_color}; background-color: {p_bg}; border: 1px solid {p_border}; "
                f"border-radius: 4px; padding: 1px 6px; "
                f"font-size: 8pt; font-weight: bold; letter-spacing: 0.5px;"
            )
            dialog._proton_badge_lbl.show()
        elif tier is None:
            dialog._proton_badge_lbl.setText("FETCHING...")
            dialog._proton_badge_lbl.setStyleSheet(
                "color: #B0BEC5; background-color: rgba(255, 255, 255, 0.08); "
                "border: 1px solid rgba(255, 255, 255, 0.20); "
                "border-radius: 4px; padding: 1px 6px; "
                "font-size: 8pt; font-weight: bold; letter-spacing: 0.5px;"
            )
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
