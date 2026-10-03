import logging
from typing import Optional

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class ActiveGameCard(QFrame):
    """Hero game banner card displayed during active downloads with thumbnail background."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.appid = None
        self.game_name = "Installing Game..."
        self.pixmap = None
        self.accent_color = "#C06C84"

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(84)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(4)
        lay.addStretch()

        self.title_lbl = QLabel(self.game_name)
        self.title_lbl.setStyleSheet(
            "color: #FFFFFF; font-size: 11pt; font-weight: bold; background: transparent; border: none;"
        )
        self.title_lbl.setWordWrap(True)
        lay.addWidget(self.title_lbl)

        self.version_lbl = QLabel("")
        self.version_lbl.setStyleSheet(
            "font-size: 8.5pt; color: rgba(255, 255, 255, 0.85); background: transparent; border: none;"
        )
        self.version_lbl.setWordWrap(True)
        self.version_lbl.hide()
        lay.addWidget(self.version_lbl)

        self.sub_lbl = QLabel("Downloading game files...")
        self.sub_lbl.setStyleSheet(
            "color: rgba(255, 255, 255, 0.65); font-size: 8.5pt; font-weight: 500; background: transparent; border: none;"
        )
        lay.addWidget(self.sub_lbl)
        lay.addStretch()

    def set_game(self, appid: str, game_name: str, accent_color: Optional[str] = None):
        self.appid = str(appid) if appid else None
        self.game_name = game_name or "Installing Game..."
        if accent_color:
            self.accent_color = accent_color
        self.title_lbl.setText(self.game_name)
        self.version_lbl.setText("")
        self.version_lbl.hide()

        from utils.image_fetcher import ImageFetcher
        self.pixmap = None
        if self.appid and self.appid not in ("0", "unknown", "N/A"):
            cache_path = ImageFetcher.get_cache_path(self.appid)
            if cache_path.exists():
                self.pixmap = QPixmap(str(cache_path))
        self.update()

    def set_version_info(self, installed_build: str = "", target_build: str = "", appid: str = ""):
        """Displays version trajectory (installed -> target) with dates cleanly formatted under the build IDs."""
        aid = appid or self.appid
        installed_bid = str(installed_build or "").strip()
        target_bid = str(target_build or "").strip()

        if installed_bid.lower() in ("0", "none", "unknown", "n/a"):
            installed_bid = ""
        if target_bid.lower() in ("0", "none", "unknown", "n/a"):
            target_bid = ""

        if not installed_bid and not target_bid:
            self.version_lbl.setText("")
            self.version_lbl.hide()
            return

        build_lookup = {}
        if aid and str(aid).isdigit() and int(aid) > 0:
            try:
                from core.steamdb_scraper import SteamDBBuildsCache
                cache = SteamDBBuildsCache()
                builds = cache.get_builds(int(aid)) or []
                for b in builds:
                    bid_key = str(b.get("buildid", "")).strip()
                    if bid_key:
                        build_lookup[bid_key] = b
            except Exception as e:
                logger.debug(f"[ActiveGameCard] Could not load SteamDB cache for {aid}: {e}")

        def _get_build_date(bid: str) -> str:
            if not bid:
                return ""
            entry = build_lookup.get(bid)
            if entry and entry.get("date"):
                return str(entry["date"]).strip()
            # If not in SteamDB cache, try PICS branches timeupdated
            if aid and str(aid).isdigit() and int(aid) > 0:
                try:
                    from core.steam_api import get_depot_info_from_api
                    from datetime import datetime, timezone
                    p_info = get_depot_info_from_api(int(aid))
                    if p_info and "branches" in p_info:
                        for b_data in p_info["branches"].values():
                            if isinstance(b_data, dict) and str(b_data.get("buildid")) == str(bid):
                                tu = b_data.get("timeupdated")
                                if tu:
                                    return datetime.fromtimestamp(int(tu), tz=timezone.utc).strftime("%-d %B %Y")
                except Exception:
                    pass
            return ""

        from_date = _get_build_date(installed_bid)
        to_date = _get_build_date(target_bid)

        ac = self.accent_color or "#C06C84"
        arrow_html = f' <span style="color: {ac}; font-weight: bold; font-size: 9pt;">➔</span> '

        if installed_bid and target_bid and installed_bid != target_bid:
            build_line = (
                f'<span style="color: rgba(255, 255, 255, 0.85); font-weight: 700; font-size: 9.5pt;">{installed_bid}</span>'
                f'{arrow_html}'
                f'<span style="color: #FFFFFF; font-weight: 700; font-size: 9.5pt;">{target_bid}</span>'
            )
            if from_date and to_date:
                date_line = (
                    f'<span style="color: rgba(255, 255, 255, 0.55); font-size: 7.5pt;">{from_date}</span>'
                    f'<span style="color: rgba(255, 255, 255, 0.3); font-size: 7.5pt;"> ➔ </span>'
                    f'<span style="color: rgba(255, 255, 255, 0.75); font-size: 7.5pt;">{to_date}</span>'
                )
                html = f"{build_line}<br/>{date_line}"
                tip = f"Updating:\nFrom: Build {installed_bid} ({from_date})\nTo: Build {target_bid} ({to_date})"
            elif to_date:
                date_line = f'<span style="color: rgba(255, 255, 255, 0.65); font-size: 7.5pt;">Target released: {to_date}</span>'
                html = f"{build_line}<br/>{date_line}"
                tip = f"Updating: Build {installed_bid} ➔ Build {target_bid} ({to_date})"
            elif from_date:
                date_line = f'<span style="color: rgba(255, 255, 255, 0.65); font-size: 7.5pt;">Installed: {from_date}</span>'
                html = f"{build_line}<br/>{date_line}"
                tip = f"Updating: Build {installed_bid} ({from_date}) ➔ Build {target_bid}"
            else:
                html = build_line
                tip = f"Updating: Build {installed_bid} ➔ Build {target_bid}"

        elif target_bid:
            build_line = (
                f'<span style="color: rgba(255, 255, 255, 0.75); font-size: 8.5pt;">Target Build: </span>'
                f'<span style="color: #FFFFFF; font-weight: 700; font-size: 9.5pt;">{target_bid}</span>'
            )
            if to_date:
                date_line = f'<span style="color: rgba(255, 255, 255, 0.65); font-size: 7.5pt;">Released: {to_date}</span>'
                html = f"{build_line}<br/>{date_line}"
                tip = f"Target Build: {target_bid} ({to_date})"
            else:
                html = build_line
                tip = f"Target Build: {target_bid}"

        else:
            build_line = (
                f'<span style="color: rgba(255, 255, 255, 0.75); font-size: 8.5pt;">Installed Build: </span>'
                f'<span style="color: #FFFFFF; font-weight: 700; font-size: 9.5pt;">{installed_bid}</span>'
            )
            if from_date:
                date_line = f'<span style="color: rgba(255, 255, 255, 0.65); font-size: 7.5pt;">Installed: {from_date}</span>'
                html = f"{build_line}<br/>{date_line}"
                tip = f"Installed Build: {installed_bid} ({from_date})"
            else:
                html = build_line
                tip = f"Installed Build: {installed_bid}"

        self.version_lbl.setText(html)
        self.version_lbl.setToolTip(tip)
        self.version_lbl.show()

    def set_sub_status(self, text: str, is_warning: bool = False):
        self.sub_lbl.setText(text)
        if is_warning:
            self.sub_lbl.setStyleSheet(
                "color: #FFB84D; font-size: 8.5pt; font-weight: bold; background: transparent; border: none;"
            )
        else:
            self.sub_lbl.setStyleSheet(
                "color: rgba(255, 255, 255, 0.65); font-size: 8.5pt; font-weight: 500; background: transparent; border: none;"
            )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        bg_color = QColor(25, 25, 25)

        path = QPainterPath()
        path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()), 6.0, 6.0)
        painter.setClipPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg_color)
        painter.drawRect(rect)

        if self.pixmap and not self.pixmap.isNull():
            img_h = rect.height()
            img_w = int(img_h * (self.pixmap.width() / self.pixmap.height()))
            target_rect = QRect(rect.width() - img_w, 0, img_w, rect.height())
            painter.drawPixmap(target_rect, self.pixmap)

            # Gradient scrim overlay across the image
            gradient = QLinearGradient(rect.width() - img_w, 0, rect.width(), 0)
            gradient.setColorAt(0.0, bg_color)
            gradient.setColorAt(0.45, QColor(bg_color.red(), bg_color.green(), bg_color.blue(), 215))
            gradient.setColorAt(1.0, QColor(bg_color.red(), bg_color.green(), bg_color.blue(), 50))

            painter.setBrush(QBrush(gradient))
            painter.drawRect(rect)

        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 15), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 6, 6)
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 6, 6)
