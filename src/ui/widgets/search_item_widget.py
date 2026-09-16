from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QSizePolicy,
)


class SearchItemWidget(QWidget):
    """Custom widget for displaying polished search results — styled like the game library cards."""
    def __init__(self, name: str, app_id: str, in_library: bool, is_cached: bool = False, parent=None):
        super().__init__(parent)
        self.name = name
        self.app_id = app_id
        self.in_library = in_library
        self.is_cached = is_cached

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 16, 1)
        layout.setSpacing(16)

        # --- Image (same proportions as library cards) ---
        self.img_lbl = QLabel()
        self.img_lbl.setFixedSize(200, 94)
        self.img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_lbl.setText(name[:2].upper())
        self.img_lbl.setStyleSheet(
            "border-top-left-radius: 10px;"
            "border-bottom-left-radius: 10px;"
            "border-top-right-radius: 0px;"
            "border-bottom-right-radius: 0px;"
            "background-color: rgba(255,255,255,0.04);"
            "color: rgba(255,255,255,0.5);"
            "font-size: 20px; font-weight: bold;"
        )
        self.img_lbl.setScaledContents(True)
        layout.addWidget(self.img_lbl)

        # --- Info column ---
        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 10, 0, 10)
        info_col.setSpacing(5)

        # Top row: game name + minimal ProtonDB badge
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.setContentsMargins(0, 0, 0, 0)

        self.name_lbl = QLabel(name)
        name_color = "#77DD77" if in_library else "#FFFFFF"
        self.name_lbl.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {name_color};")
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
        self.meta_row.setSpacing(6)
        self.meta_row.setContentsMargins(0, 0, 0, 0)

        self.appid_lbl = QLabel(f"App ID: {app_id}")
        self.appid_lbl.setStyleSheet("font-size: 11px; color: rgba(255,255,255,0.50);")
        self.meta_row.addWidget(self.appid_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.denuvo_lbl = QLabel()
        self.denuvo_lbl.hide()
        self.denuvo_lbl.setStyleSheet("font-size: 11px; font-weight: bold;")
        self.meta_row.addWidget(self.denuvo_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        if in_library:
            self.in_lib_lbl = QLabel("•  In Library")
            self.in_lib_lbl.setStyleSheet("font-size: 11px; color: #81C784; font-weight: bold;")
            self.meta_row.addWidget(self.in_lib_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        if is_cached:
            self.cached_lbl = QLabel("•  Cached")
            self.cached_lbl.setStyleSheet("font-size: 11px; color: #4FC3F7; font-weight: bold;")
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
        try:
            from PyQt6 import sip
            if sip.isdeleted(self):
                return
        except Exception:
            pass

        try:
            from core.ratings import get_denuvo_status, get_protondb_tier

            # Denuvo status as colored text next to App ID
            denuvo_status = get_denuvo_status(self.app_id)
            _denuvo_text_map = {
                "cracked":    ("•  Denuvo Cracked",    "#81C784"),
                "hypervisor": ("•  Denuvo Hypervisor", "#FFA726"),
                "uncracked":  ("•  Denuvo Uncracked",  "#E57373"),
            }
            if denuvo_status and denuvo_status in _denuvo_text_map:
                text, color = _denuvo_text_map[denuvo_status]
                self.denuvo_lbl.setText(text)
                self.denuvo_lbl.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {color};")
                self.denuvo_lbl.show()
            else:
                self.denuvo_lbl.hide()

            # Minimal ProtonDB badge (read cached value without queuing network fetch)
            from core.ratings import _load_protondb_cache, is_protondb_fetching
            proton_cache = _load_protondb_cache()
            c_entry = proton_cache.get(str(self.app_id))
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
                self._is_fetching_proton = False
                text, color, bg, border = _tier_map[proton_tier]
                self.proton_badge.setText(text)
                self.proton_badge.setStyleSheet(
                    f"color: {color}; background-color: {bg}; border: 1px solid {border}; "
                    f"border-radius: 4px; padding: 1px 6px; font-size: 9px; font-weight: bold; letter-spacing: 0.5px;"
                )
                self.proton_badge.show()
            elif getattr(self, "_is_fetching_proton", False) or is_protondb_fetching(str(self.app_id)):
                self.set_proton_fetching()
            else:
                self._is_fetching_proton = False
                self.proton_badge.hide()
        except Exception:
            pass

    def set_proton_fetching(self) -> None:
        """Display 'FETCHING...' badge while resolving rating in parallel."""
        try:
            from PyQt6 import sip
            if sip.isdeleted(self):
                return
        except Exception:
            pass
        self._is_fetching_proton = True
        self.proton_badge.setText("FETCHING...")
        self.proton_badge.setStyleSheet(
            "color: #B0BEC5; background-color: rgba(255, 255, 255, 0.08); "
            "border: 1px solid rgba(255, 255, 255, 0.20); "
            "border-radius: 4px; padding: 1px 6px; font-size: 9px; font-weight: bold; letter-spacing: 0.5px;"
        )
        self.proton_badge.show()

    def set_image(self, pixmap: QPixmap) -> None:
        if pixmap and not pixmap.isNull():
            self.img_lbl.setPixmap(pixmap)
