import logging

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QSizePolicy,
)

logger = logging.getLogger(__name__)


class SearchItemWidget(QWidget):
    """Custom widget for displaying polished search results — styled like the game library cards."""
    def __init__(self, name: str, app_id: str, in_library: bool, is_cached: bool = False, parent=None, compact: bool = False):
        super().__init__(parent)
        self.name = name
        self.app_id = app_id
        self.in_library = in_library
        self.is_cached = is_cached
        self.compact = compact

        layout = QHBoxLayout(self)
        if compact:
            layout.setContentsMargins(0, 1, 10, 1)
            layout.setSpacing(10)
        else:
            layout.setContentsMargins(0, 1, 16, 1)
            layout.setSpacing(16)

        # --- Image (same proportions as library cards) ---
        self.img_lbl = QLabel()
        if compact:
            self.img_lbl.setFixedSize(92, 44)
            self.img_lbl.setStyleSheet(
                "border-top-left-radius: 6px;"
                "border-bottom-left-radius: 6px;"
                "border-top-right-radius: 0px;"
                "border-bottom-right-radius: 0px;"
                "background-color: rgba(255,255,255,0.04);"
                "color: rgba(255,255,255,0.5);"
                "font-size: 13px; font-weight: bold;"
            )
        else:
            self.img_lbl.setFixedSize(200, 94)
            self.img_lbl.setStyleSheet(
                "border-top-left-radius: 10px;"
                "border-bottom-left-radius: 10px;"
                "border-top-right-radius: 0px;"
                "border-bottom-right-radius: 0px;"
                "background-color: rgba(255,255,255,0.04);"
                "color: rgba(255,255,255,0.5);"
                "font-size: 20px; font-weight: bold;"
            )
        self.img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_lbl.setText(name[:2].upper())
        self.img_lbl.setScaledContents(True)
        layout.addWidget(self.img_lbl)

        # --- Info column ---
        info_col = QVBoxLayout()
        if compact:
            info_col.setContentsMargins(0, 2, 0, 2)
            info_col.setSpacing(2)
        else:
            info_col.setContentsMargins(0, 10, 0, 10)
            info_col.setSpacing(5)

        # Top row: game name + minimal ProtonDB badge
        name_row = QHBoxLayout()
        name_row.setSpacing(compact and 4 or 8)
        name_row.setContentsMargins(0, 0, 0, 0)

        self.name_lbl = QLabel(name)
        name_color = "#77DD77" if in_library else "#FFFFFF"
        font_size = "12px" if compact else "14px"
        self.name_lbl.setStyleSheet(f"font-size: {font_size}; font-weight: bold; color: {name_color};")
        self.name_lbl.setWordWrap(False)
        name_row.addWidget(self.name_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.proton_badge = QLabel()
        self.proton_badge.hide()
        self.proton_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        name_row.addWidget(self.proton_badge, 0, Qt.AlignmentFlag.AlignVCenter)

        name_row.addStretch()
        info_col.addLayout(name_row)

        info_col.addStretch(1)

        # Bottom: AppID + Denuvo status row
        self.meta_row = QHBoxLayout()
        self.meta_row.setSpacing(compact and 4 or 6)
        self.meta_row.setContentsMargins(0, 0, 0, 0)

        meta_font_size = "10px" if compact else "11px"
        self.appid_lbl = QLabel(f"App ID: {app_id}")
        self.appid_lbl.setStyleSheet(f"font-size: {meta_font_size}; color: rgba(255,255,255,0.50);")
        self.meta_row.addWidget(self.appid_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.denuvo_lbl = QLabel()
        self.denuvo_lbl.hide()
        self.denuvo_lbl.setStyleSheet(f"font-size: {meta_font_size}; font-weight: bold;")
        self.meta_row.addWidget(self.denuvo_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        if in_library:
            self.in_lib_lbl = QLabel("•  In Library")
            self.in_lib_lbl.setStyleSheet(f"font-size: {meta_font_size}; color: #81C784; font-weight: bold;")
            self.meta_row.addWidget(self.in_lib_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        if is_cached:
            self.cached_lbl = QLabel("•  Cached")
            self.cached_lbl.setStyleSheet(f"font-size: {meta_font_size}; color: #4FC3F7; font-weight: bold;")
            self.meta_row.addWidget(self.cached_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.meta_row.addStretch()
        info_col.addLayout(self.meta_row)

        layout.addLayout(info_col, 1)

        # Populate ratings/badges immediately (in-memory, instant for cached games)
        self.update_ratings()

    def set_cached(self, cached: bool = True):
        self.is_cached = cached
        if not hasattr(self, "cached_lbl"):
            self.cached_lbl = QLabel("•  Cached")
            self.cached_lbl.setStyleSheet("font-size: 11px; color: #4FC3F7; font-weight: bold;")
            self.meta_row.insertWidget(self.meta_row.count() - 1, self.cached_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        self.cached_lbl.setVisible(cached)

    def update_ratings(self) -> None:
        """Update Denuvo and ProtonDB badges dynamically."""
        # The list layout uses slightly larger badges than the tile/compact
        # layouts, so the sizes are passed in rather than hard-coded here.
        _apply_ratings_to_widget(self, denuvo_font_size="11px", badge_padding="1px 6px")

    def set_proton_fetching(self) -> None:
        """Display 'FETCHING...' badge while resolving rating in parallel."""
        _set_proton_fetching_on_widget(self, badge_padding="1px 6px")

    def set_image(self, pixmap: QPixmap) -> None:
        _set_image_on_widget(self, pixmap)


def _set_image_on_widget(widget: QWidget, pixmap: QPixmap) -> None:
    """Attach a cover pixmap to a widget's image label, if it still exists."""
    try:
        from PyQt6 import sip
        if sip.isdeleted(widget):
            return
        if pixmap and not pixmap.isNull():
            widget.img_lbl.setPixmap(pixmap)
    except Exception as e:
        logger.debug(f"[SearchWidgets] Could not apply image to {getattr(widget, 'app_id', '?')}: {e}")


def _apply_ratings_to_widget(
    widget: QWidget,
    denuvo_font_size: str = "10px",
    badge_padding: str = "1px 5px",
) -> None:
    """Helper to dynamically populate Denuvo and ProtonDB badges across search widget styles."""
    try:
        from PyQt6 import sip
        if sip.isdeleted(widget):
            return
    except Exception as e:
        logger.debug(f"[SearchWidgets] Could not probe widget liveness: {e}")

    try:
        from core.ratings import get_denuvo_status, get_protondb_tier

        # Denuvo status
        denuvo_status = get_denuvo_status(widget.app_id)
        _denuvo_text_map = {
            "cracked":    ("•  Denuvo Cracked",    "#81C784"),
            "hypervisor": ("•  Denuvo Hypervisor", "#FFA726"),
            "uncracked":  ("•  Denuvo Uncracked",  "#E57373"),
        }
        if denuvo_status and denuvo_status in _denuvo_text_map:
            text, color = _denuvo_text_map[denuvo_status]
            widget.denuvo_lbl.setText(text)
            widget.denuvo_lbl.setStyleSheet(f"font-size: {denuvo_font_size}; font-weight: bold; color: {color};")
            widget.denuvo_lbl.show()
        else:
            widget.denuvo_lbl.hide()

        # ProtonDB badge
        from core.ratings import _load_protondb_cache, is_protondb_fetching
        proton_cache = _load_protondb_cache()
        c_entry = proton_cache.get(str(widget.app_id))
        proton_tier = c_entry.get("tier") if isinstance(c_entry, dict) else None
        _tier_map = {
            "platinum": ("PLATINUM", "#90CAF9", "rgba(33, 150, 243, 0.15)", "rgba(144, 202, 249, 0.30)"),
            "gold":     ("GOLD",     "#FFE082", "rgba(255, 193, 7, 0.15)",   "rgba(255, 224, 130, 0.30)"),
            "silver":   ("SILVER",   "#CFD8DC", "rgba(144, 164, 174, 0.15)", "rgba(207, 216, 220, 0.30)"),
            "bronze":   ("BRONZE",   "#FFAB91", "rgba(255, 112, 67, 0.15)",  "rgba(255, 171, 145, 0.30)"),
            "borked":   ("BORKED",   "#EF9A9A", "rgba(239, 83, 80, 0.18)",   "rgba(239, 154, 154, 0.35)"),
            "native":   ("NATIVE",   "#A5D6A7", "rgba(76, 175, 80, 0.15)",   "rgba(165, 214, 167, 0.30)"),
        }
        if proton_tier and proton_tier in _tier_map:
            widget._is_fetching_proton = False
            text, color, bg, border = _tier_map[proton_tier]
            widget.proton_badge.setText(text)
            widget.proton_badge.setStyleSheet(
                f"color: {color}; background-color: {bg}; border: 1px solid {border}; "
                f"border-radius: 4px; padding: {badge_padding}; font-size: 9px; font-weight: bold; letter-spacing: 0.5px;"
            )
            widget.proton_badge.show()
        elif getattr(widget, "_is_fetching_proton", False) or is_protondb_fetching(str(widget.app_id)):
            _set_proton_fetching_on_widget(widget, badge_padding=badge_padding)
        else:
            widget._is_fetching_proton = False
            widget.proton_badge.hide()
    except Exception as e:
        logger.debug(f"[SearchWidgets] Rating lookup failed for {getattr(widget, 'app_id', '?')}: {e}")


def _set_proton_fetching_on_widget(widget: QWidget, badge_padding: str = "1px 5px") -> None:
    try:
        from PyQt6 import sip
        if sip.isdeleted(widget):
            return
    except Exception as e:
        logger.debug(f"[SearchWidgets] Could not probe widget liveness: {e}")
    widget._is_fetching_proton = True
    widget.proton_badge.setText("FETCHING...")
    widget.proton_badge.setStyleSheet(
        "color: #B0BEC5; background-color: rgba(255, 255, 255, 0.08); "
        "border: 1px solid rgba(255, 255, 255, 0.20); "
        f"border-radius: 4px; padding: {badge_padding}; font-size: 9px; font-weight: bold; letter-spacing: 0.5px;"
    )
    widget.proton_badge.show()


class SearchTileWidget(QWidget):
    """Tile widget for 3x3 grid view of search results, dynamically expanding with window size."""

    def __init__(self, name: str, app_id: str, in_library: bool, is_cached: bool = False, parent=None):
        super().__init__(parent)
        self.name = name
        self.app_id = app_id
        self.in_library = in_library
        self.is_cached = is_cached

        self.setStyleSheet(
            """
            SearchTileWidget {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
            SearchTileWidget:hover {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.18);
            }
            """
        )

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(0, 0, 0, 8)
        card_layout.setSpacing(6)

        # 1. Header Image on Top
        self.img_lbl = QLabel()
        self.img_lbl.setFixedHeight(110)
        self.img_lbl.setStyleSheet(
            "border-top-left-radius: 8px;"
            "border-top-right-radius: 8px;"
            "border-bottom-left-radius: 0px;"
            "border-bottom-right-radius: 0px;"
            "background-color: rgba(255, 255, 255, 0.06);"
            "color: rgba(255, 255, 255, 0.5);"
            "font-size: 20px; font-weight: bold;"
        )
        self.img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_lbl.setText(name[:2].upper())
        self.img_lbl.setScaledContents(True)
        card_layout.addWidget(self.img_lbl)

        # 2. Text Content Container
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(10, 0, 10, 0)
        content_layout.setSpacing(4)

        # Title
        self.name_lbl = QLabel(name)
        name_color = "#77DD77" if in_library else "#FFFFFF"
        self.name_lbl.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {name_color};")
        self.name_lbl.setWordWrap(False)
        content_layout.addWidget(self.name_lbl)

        # App ID & Status Badges
        self.meta_row = QHBoxLayout()
        self.meta_row.setContentsMargins(0, 0, 0, 0)
        self.meta_row.setSpacing(6)

        self.appid_lbl = QLabel(f"App ID: {app_id}")
        self.appid_lbl.setStyleSheet("font-size: 10px; color: rgba(255, 255, 255, 0.50);")
        self.meta_row.addWidget(self.appid_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.proton_badge = QLabel()
        self.proton_badge.hide()
        self.proton_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.meta_row.addWidget(self.proton_badge, 0, Qt.AlignmentFlag.AlignVCenter)

        self.denuvo_lbl = QLabel()
        self.denuvo_lbl.hide()
        self.denuvo_lbl.setStyleSheet("font-size: 10px; font-weight: bold;")
        self.meta_row.addWidget(self.denuvo_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        if in_library:
            self.in_lib_lbl = QLabel("• In Lib")
            self.in_lib_lbl.setStyleSheet("font-size: 10px; color: #81C784; font-weight: bold;")
            self.meta_row.addWidget(self.in_lib_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        if is_cached:
            self.cached_lbl = QLabel("• Cached")
            self.cached_lbl.setStyleSheet("font-size: 10px; color: #4FC3F7; font-weight: bold;")
            self.meta_row.addWidget(self.cached_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.meta_row.addStretch()
        content_layout.addLayout(self.meta_row)

        card_layout.addLayout(content_layout)
        self.update_ratings()

    def set_image(self, pixmap: QPixmap) -> None:
        _set_image_on_widget(self, pixmap)

    def set_cached(self, cached: bool = True):
        self.is_cached = cached
        if not hasattr(self, "cached_lbl"):
            self.cached_lbl = QLabel("• Cached")
            self.cached_lbl.setStyleSheet("font-size: 10px; color: #4FC3F7; font-weight: bold;")
            self.meta_row.insertWidget(self.meta_row.count() - 1, self.cached_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        self.cached_lbl.setVisible(cached)

    def update_ratings(self) -> None:
        _apply_ratings_to_widget(self)

    def set_proton_fetching(self) -> None:
        _set_proton_fetching_on_widget(self)

    def sizeHint(self) -> QSize:
        return QSize(236, 175)


class SearchCompactWidget(QWidget):
    """Compact text-only row for listing games without images."""

    def __init__(self, name: str, app_id: str, in_library: bool, is_cached: bool = False, parent=None):
        super().__init__(parent)
        self.name = name
        self.app_id = app_id
        self.in_library = in_library
        self.is_cached = is_cached

        self.setStyleSheet(
            """
            SearchCompactWidget {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 6px;
            }
            SearchCompactWidget:hover {
                background-color: rgba(255, 255, 255, 0.07);
                border: 1px solid rgba(255, 255, 255, 0.14);
            }
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(10)

        # Game title
        self.name_lbl = QLabel(name)
        name_color = "#77DD77" if in_library else "#FFFFFF"
        self.name_lbl.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {name_color};")
        self.name_lbl.setWordWrap(False)
        layout.addWidget(self.name_lbl, 1, Qt.AlignmentFlag.AlignVCenter)

        # App ID
        self.appid_lbl = QLabel(f"App ID: {app_id}")
        self.appid_lbl.setStyleSheet("font-size: 10px; color: rgba(255, 255, 255, 0.50); font-family: monospace;")
        layout.addWidget(self.appid_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        # ProtonDB badge
        self.proton_badge = QLabel()
        self.proton_badge.hide()
        self.proton_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.proton_badge, 0, Qt.AlignmentFlag.AlignVCenter)

        # Denuvo label
        self.denuvo_lbl = QLabel()
        self.denuvo_lbl.hide()
        self.denuvo_lbl.setStyleSheet("font-size: 10px; font-weight: bold;")
        layout.addWidget(self.denuvo_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        if in_library:
            self.in_lib_lbl = QLabel("• In Library")
            self.in_lib_lbl.setStyleSheet("font-size: 10px; color: #81C784; font-weight: bold;")
            layout.addWidget(self.in_lib_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        if is_cached:
            self.cached_lbl = QLabel("• Cached")
            self.cached_lbl.setStyleSheet("font-size: 10px; color: #4FC3F7; font-weight: bold;")
            layout.addWidget(self.cached_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.update_ratings()

    def set_image(self, pixmap: QPixmap) -> None:
        """Text mode does not display images."""
        pass

    def set_cached(self, cached: bool = True):
        self.is_cached = cached
        if not hasattr(self, "cached_lbl"):
            self.cached_lbl = QLabel("• Cached")
            self.cached_lbl.setStyleSheet("font-size: 10px; color: #4FC3F7; font-weight: bold;")
            self.layout().addWidget(self.cached_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        self.cached_lbl.setVisible(cached)

    def update_ratings(self) -> None:
        _apply_ratings_to_widget(self)

    def set_proton_fetching(self) -> None:
        _set_proton_fetching_on_widget(self)

    def sizeHint(self) -> QSize:
        return QSize(0, 36)
