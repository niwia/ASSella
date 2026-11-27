import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout

logger = logging.getLogger(__name__)


class ActiveGameCard(QFrame):
    """Hero game banner card displayed during active downloads with thumbnail background."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.appid = None
        self.game_name = "Installing Game..."
        self.pixmap = None
        self._scaled_pixmap = None
        self.accent_color = "#C06C84"

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(84)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 20, 12)
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scaled_pixmap = None

    def set_game(self, appid: str, game_name: str, accent_color: str = None):
        self.appid = str(appid) if appid else None
        self.game_name = game_name or "Installing Game..."
        if accent_color:
            self.accent_color = accent_color
        self.title_lbl.setText(self.game_name)
        self.version_lbl.setText("")
        self.version_lbl.hide()

        from utils.image_fetcher import ImageFetcher
        from PyQt6.QtGui import QPixmap
        self.pixmap = None
        self._scaled_pixmap = None
        self._cached_fade_mode = None
        self._img_fetcher = None
        if self.appid and self.appid not in ("0", "unknown", "N/A"):
            cache_path = ImageFetcher.get_cache_path(self.appid)
            if cache_path.exists():
                self.pixmap = QPixmap(str(cache_path))
            else:
                expected_appid = self.appid

                def _on_img_fetched(data, aid=expected_appid):
                    from PyQt6 import sip
                    if sip.isdeleted(self) or self.appid != aid:
                        return
                    if data:
                        pm = QPixmap()
                        if pm.loadFromData(data) and not pm.isNull():
                            self.pixmap = pm
                            self._scaled_pixmap = None
                            self.update()

                url = ImageFetcher.get_header_image_url(self.appid)
                self._img_fetcher = ImageFetcher(url, parent=self)
                self._img_fetcher.finished.connect(_on_img_fetched)
                self._img_fetcher.start()
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
            # If not in SteamDB cache, try PICS branches timeupdated from local DatabaseManager cache
            if aid and str(aid).isdigit() and int(aid) > 0:
                try:
                    from managers.db_manager import DatabaseManager
                    from datetime import datetime, timezone
                    p_info = DatabaseManager().get_app_info(str(aid), bypass_expiration=True)
                    if p_info and "branches" in p_info and isinstance(p_info["branches"], dict):
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
        if getattr(self, "_current_warning_state", None) != is_warning:
            self._current_warning_state = is_warning
            if is_warning:
                self.sub_lbl.setStyleSheet(
                    "color: #FFB84D; font-size: 8.5pt; font-weight: bold; background: transparent; border: none;"
                )
            else:
                self.sub_lbl.setStyleSheet(
                    "color: rgba(255, 255, 255, 0.65); font-size: 8.5pt; font-weight: 500; background: transparent; border: none;"
                )

    def _artwork_layout_params(self, rect):
        """Return mode-specific artwork geometry & gradient measurements.

        Single-game downloads span the full bottom section (~700-850px), while
        queued downloads sit beside the queue list (~350-420px) and need more
        fade runway + stronger scrim so wrapped text remains easy to read.
        """
        is_compact = rect.width() < 520
        natural_w = int(rect.height() * (self.pixmap.width() / max(self.pixmap.height(), 1)))
        if is_compact:
            target_w = min(rect.width(), max(natural_w, int(rect.width() * 0.68)))
            return (
                "compact",
                target_w,
                {
                    "fade_start": 0.05,
                    "fade_end": 0.80,
                    "peak_alpha": 220,
                    "end_alpha": 238,
                    "scrim_mid": 45,
                    "scrim_end": 95,
                },
            )
        target_w = min(rect.width(), max(natural_w, int(rect.width() * 0.58)))
        return (
            "wide",
            target_w,
            {
                "fade_start": 0.0,
                "fade_end": 0.68,
                "peak_alpha": 232,
                "end_alpha": 245,
                "scrim_mid": 25,
                "scrim_end": 85,
            },
        )

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QLinearGradient, QPainterPath, QPen
        from PyQt6.QtCore import QRect
        from ui.widgets.artwork_fade import build_faded_artwork

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = self.rect()
        pal = self.palette()
        win_col = pal.color(pal.ColorRole.Window)
        if win_col.isValid():
            bg_color = QColor(win_col.red(), win_col.green(), win_col.blue(), 230)
        else:
            bg_color = QColor(18, 18, 22, 230)

        path = QPainterPath()
        path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()), 6.0, 6.0)
        painter.setClipPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg_color)
        painter.drawRect(rect)

        if self.pixmap and not self.pixmap.isNull():
            mode, target_w, params = self._artwork_layout_params(rect)
            target_rect = QRect(rect.width() - target_w, 0, target_w, rect.height())

            if (
                self._scaled_pixmap is None
                or self._scaled_pixmap.size() != target_rect.size()
                or getattr(self, "_cached_fade_mode", None) != mode
            ):
                self._scaled_pixmap = build_faded_artwork(
                    self.pixmap,
                    target_rect.size(),
                    direction="fade_in_right",
                    fade_start=params["fade_start"],
                    fade_end=params["fade_end"],
                    start_alpha=0,
                    peak_alpha=params["peak_alpha"],
                    end_alpha=params["end_alpha"],
                    edge_alpha=205,
                    edge_span=0.14,
                )
                self._cached_fade_mode = mode

            if not self._scaled_pixmap.isNull():
                painter.drawPixmap(target_rect, self._scaled_pixmap)

            # Soft readability scrim over the artwork zone without creating a hard band
            scrim = QLinearGradient(float(target_rect.left()), 0.0, float(target_rect.right()), 0.0)
            scrim.setColorAt(0.0, QColor(0, 0, 0, 0))
            scrim.setColorAt(0.45, QColor(0, 0, 0, params["scrim_mid"]))
            scrim.setColorAt(1.0, QColor(0, 0, 0, params["scrim_end"]))
            painter.fillRect(target_rect, scrim)

        painter.setClipping(False)
        border_col = QColor(255, 255, 255, 18) if bg_color.lightness() < 128 else QColor(0, 0, 0, 22)
        painter.setPen(QPen(border_col, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 6, 6)
