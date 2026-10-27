import logging
import math
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPixmap, QPainter, QBrush, QLinearGradient, QColor, QFontMetrics, QImage
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


def format_game_display_name(game_data: dict) -> str:
    """Return the display name for a game, with branch suffix for non-public branches."""
    name = game_data.get("game_name", "Unknown")
    appid = str(game_data.get("appid", ""))
    parts = [name]
    if appid and appid not in ("0", "N/A", "unknown"):
        from utils.dlc_helpers import is_dlc_only_mode
        if is_dlc_only_mode(appid):
            parts.append("[DLC MODE]")
        is_vm = bool(
            game_data.get("is_atom")
            or game_data.get("is_vapor")
            or game_data.get("is_plugin_game")
            or game_data.get("update_status") in ("vapor", "at0m", "at0-m")
            or game_data.get("source") in ("Vapor", "at0-m", "AT0-M")
        )
        if not is_vm:
            from utils.settings import get_settings
            branch = get_settings().value(f"installed_branch/{appid}", "public", type=str)
            if branch and branch != "public":
                parts.append(f"({branch})")
    return " ".join(parts)


DLC_MODE_SUFFIX = " [DLC MODE]"


def split_dlc_mode(display_name: str) -> tuple:
    """Split the DLC-mode marker off a display name.

    The library shows the marker as its own accented chip, so it must not be
    part of the name the eliding label measures and truncates.
    """
    if display_name.endswith(DLC_MODE_SUFFIX):
        return display_name[: -len(DLC_MODE_SUFFIX)], True
    return display_name, False


def format_size(size_bytes: int) -> str:
    """Format byte size into human readable string."""
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_names[i]}"


class ElidedLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._full_text = text
        self.setWordWrap(True)
        self.setToolTip(text)
        
    def setText(self, text):
        self._full_text = text
        self.setToolTip(text)
        self.update_elision()
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_elision()
        
    def sizeHint(self) -> QSize:
        sh = super().sizeHint()
        fm = QFontMetrics(self.font())
        line_height = fm.lineSpacing()
        # Report height of exactly 2 lines (or 1 if short)
        w = self.width() or 400
        if fm.horizontalAdvance(self._full_text) <= w:
            sh.setHeight(line_height)
        else:
            sh.setHeight(line_height * 2)
        return sh

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def update_elision(self):
        text = self._full_text
        fm = QFontMetrics(self.font())
        if not text:
            super().setText("")
            self.setFixedHeight(fm.lineSpacing())
            return
            
        width = self.width()
        if width <= 10:
            super().setText(text)
            self.setFixedHeight(fm.lineSpacing())
            return
            
        if fm.horizontalAdvance(text) <= width:
            super().setText(text)
            self.setFixedHeight(fm.lineSpacing())
            return
            
        # Simple line-breaking for up to 2 lines
        words = text.split(" ")
        lines = []
        current_line = []
        for word in words:
            test_line = " ".join(current_line + [word]) if current_line else word
            if fm.horizontalAdvance(test_line) <= width:
                current_line.append(word)
            else:
                if len(lines) == 0:
                    lines.append(" ".join(current_line))
                    current_line = [word]
                else:
                    # Second line, we need to elide the rest
                    remaining = " ".join(current_line + [word] + words[words.index(word)+1:])
                    elided = fm.elidedText(remaining, Qt.TextElideMode.ElideRight, width)
                    lines.append(elided)
                    current_line = []
                    break
        if current_line:
            if len(lines) < 2:
                lines.append(" ".join(current_line))
        elided_text = "\n".join(lines[:2])
        super().setText(elided_text)
        self.setFixedHeight(fm.lineSpacing() * len(lines[:2]))


class BlurredHeaderWidget(QWidget):
    """Custom widget containing a blurred background image and overlay."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bg_label = QLabel(self)
        self.bg_label.setScaledContents(True)
        self.overlay = QWidget(self)
        self.overlay.setStyleSheet("background-color: rgba(0, 0, 0, 165);")
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.bg_label.setGeometry(0, 0, self.width(), self.height())
        self.overlay.setGeometry(0, 0, self.width(), self.height())


class GameItemWidget(QWidget):
    """
    Custom widget for displaying a game item in the library list.
    Layout: [ Checkbox (select mode) ] [ Image ] [ Name/Size/Status ]
    """

    def __init__(
        self,
        game_data: dict,
        size_str: str,
        accent_color: str,
        background_color: str,
        select_mode: bool = False,
        is_selected: bool = False,
        applist_2_0_enabled: bool = True,
        parent_dialog = None,
    ):
        super().__init__()
        self.game_data = game_data
        self.accent_color = accent_color
        self.background_color = background_color
        self._select_mode = select_mode
        self._is_selected = is_selected
        self.checkbox = None
        self.applist_2_0_enabled = applist_2_0_enabled
        self.parent_dialog = parent_dialog
        self.appid = str(game_data.get("appid", "") or "")
        self._init_ui(size_str)

    def _init_ui(self, size_str: str) -> None:
        """Initialize the UI components."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 1, 16, 1)
        layout.setSpacing(16)

        # Check setting
        applist_2_0_enabled = self.applist_2_0_enabled

        # --- Checkbox (select mode only, old style) ---
        if not applist_2_0_enabled and self._select_mode:
            self.checkbox = QCheckBox()
            self.checkbox.setChecked(self._is_selected)
            self.checkbox.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.checkbox.setStyleSheet("QCheckBox::indicator { width: 18px; height: 18px; }")
            layout.addWidget(self.checkbox)

        # --- Image Section ---
        self.image_label = QLabel()
        self.image_label.setFixedSize(220, 128)  # Fits perfectly in 130px card height minus borders
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        name = self.game_data.get("game_name", "Unknown")
        display_name = format_game_display_name(self.game_data)
        self.image_label.setText(name[:2].upper())

        self.image_label.setStyleSheet(
            f"border-top-left-radius: 11px; "
            f"border-bottom-left-radius: 11px; "
            f"border-top-right-radius: 0px; "
            f"border-bottom-right-radius: 0px; "
            f"background-color: rgba(255, 255, 255, 0.02); "
            f"color: {self.accent_color}; "
        )
        layout.addWidget(self.image_label)

        # --- Checkbox Overlay (new style) ---
        if applist_2_0_enabled:
            self.checkbox = QCheckBox(self.image_label)
            self.checkbox.setChecked(self._is_selected)
            self.checkbox.move(10, 10)
            self.checkbox.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.checkbox.setStyleSheet(
                f"""
                QCheckBox::indicator {{
                    width: 20px;
                    height: 20px;
                    border: 2px solid rgba(255, 255, 255, 180);
                    border-radius: 10px;
                    background-color: rgba(0, 0, 0, 150);
                }}
                QCheckBox::indicator:checked {{
                    background-color: {self.accent_color};
                    border-color: {self.accent_color};
                }}
                """
            )
            self.checkbox.setVisible(self._select_mode)

        # --- Info Section (Vertical) ---
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 8, 0, 8)
        info_layout.setSpacing(6)

        name_only, dlc_mode = split_dlc_mode(display_name)
        self.name_label = ElidedLabel(name_only)
        font = self.name_label.font()
        font.setPointSize(12)
        font.setBold(True)
        self.name_label.setFont(font)
        self.name_label.setStyleSheet("color: #FFFFFF; font-size: 12pt; font-weight: bold;")

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(6)
        name_row.addWidget(self.name_label, 1)
        self.dlc_mode_badge = None
        info_layout.addLayout(name_row)

        # Size
        size_label = QLabel(f"Size: {size_str}")
        size_label.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 12px;")
        info_layout.addWidget(size_label)

        # Check if this item belongs to the Steam (Beta) tab
        self._is_steam_tab = (
            (self.parent_dialog and getattr(self.parent_dialog, "_active_tab", "") == "steam")
            or self.game_data.get("is_steam_tab", False)
        )
        is_steam_tab = self._is_steam_tab

        # Manifest cache status (skip entirely for Steam tab)
        self.manifest_label = QLabel()
        if not is_steam_tab:
            info_layout.addWidget(self.manifest_label)
            self.update_manifest_label()
        else:
            self.manifest_label.setVisible(False)

        info_layout.addStretch()
        layout.addLayout(info_layout, 1)

        # Right column for Badges (status, denuvo, proton)
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 8, 0, 8)
        right_col.setSpacing(4)

        self.status_label = QLabel()
        self.status_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        self.pinned_build_label = QLabel()
        self.pinned_build_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.pinned_build_label.setVisible(False)
        self.pinned_label = self.pinned_build_label

        self.denuvo_badge = QLabel()
        self.denuvo_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.denuvo_badge.hide()

        self.proton_badge = QLabel()
        self.proton_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.proton_badge.hide()

        if not is_steam_tab:
            # Update status badge
            update_status = self.game_data.get("update_status", "cannot_determine")
            is_accela = bool(self.game_data.get("accela_marker_path") or self.game_data.get("source") == "ACCELA")
            is_at0m_game = not is_accela and bool(
                self.game_data.get("is_atom")
                or self.game_data.get("is_vapor")
                or self.game_data.get("is_plugin_game")
                or self.game_data.get("source") == "at0-m"
            )
            if is_at0m_game and update_status not in ("update_available", "key_pending"):
                update_status = "at0m"

            is_at0m = update_status in ("vapor", "at0m", "at0-m")
            if self._dlc_mode_applied() and update_status not in ("update_available", "key_pending"):
                self.status_label.setText("[DLC Only]")
                self.status_label.setStyleSheet(
                    f"color: {self.accent_color}; "
                    f"background-color: rgba(161, 201, 253, 0.15); "
                    f"border-radius: 10px; "
                    f"padding: 3px 10px; "
                    f"font-size: 11px; "
                    f"font-weight: bold;"
                )
            elif is_at0m:
                self.status_label.setText("[AT0-M]")
                self.status_label.setStyleSheet(
                    "color: #CE93D8; "
                    "background: transparent; "
                    "font-size: 11px; "
                    "font-weight: bold; "
                    "padding: 2px 2px;"
                )
            else:
                status_map = {
                    "update_available": ("New version available", "#FF8A80", "rgba(229, 115, 115, 0.15)"),
                    "key_pending": ("Keys Pending", "#FFB74D", "rgba(255, 183, 77, 0.15)"),
                    "up_to_date": ("Up to date", "#81C784", "rgba(129, 199, 132, 0.15)"),
                    "checking": ("Checking for updates...", "#FFA726", "rgba(255, 167, 38, 0.12)"),
                }
                text, color, bg_color = status_map.get(
                    update_status, ("Unable to check updates", "#B0BEC5", "rgba(176, 190, 197, 0.12)")
                )
                if is_at0m_game and update_status == "update_available":
                    text = "AT0-M: Keys Ready"
                elif is_at0m_game and update_status == "key_pending":
                    text = "AT0-M: Awaiting Hubcap Key"
                self.status_label.setText(text)
                self.status_label.setStyleSheet(
                    f"color: {color}; "
                    f"background-color: {bg_color}; "
                    f"border-radius: 10px; "
                    f"padding: 3px 10px; "
                    f"font-size: 11px; "
                    f"font-weight: bold;"
                )
            right_col.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignRight)

            # Pinned build label directly below the update status badge
            right_col.addWidget(self.pinned_build_label, 0, Qt.AlignmentFlag.AlignRight)
            self.update_pinned_label()

            right_col.addStretch(1)

            # Ratings row: Denuvo badge on LEFT, ProtonDB badge on RIGHT
            ratings_row = QHBoxLayout()
            ratings_row.setSpacing(6)
            ratings_row.setContentsMargins(0, 0, 0, 0)
            ratings_row.addStretch(1)
            ratings_row.addWidget(self.denuvo_badge, 0, Qt.AlignmentFlag.AlignVCenter)
            ratings_row.addWidget(self.proton_badge, 0, Qt.AlignmentFlag.AlignVCenter)
            right_col.addLayout(ratings_row)
        else:
            if self._dlc_mode_applied():
                self.status_label.setText("[DLC Only]")
                self.status_label.setStyleSheet(
                    f"color: {self.accent_color}; "
                    f"background-color: rgba(161, 201, 253, 0.15); "
                    f"border-radius: 10px; "
                    f"padding: 3px 10px; "
                    f"font-size: 11px; "
                    f"font-weight: bold;"
                )
                self.status_label.setVisible(True)
                right_col.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignRight)
            else:
                self.status_label.setVisible(False)
            right_col.addStretch(1)

        layout.addLayout(right_col)

    def update_manifest_label(self) -> None:
        """Update the manifest status label in-place."""
        if not hasattr(self, "manifest_label") or not self.manifest_label:
            return

        appid = self.game_data.get("appid", "0")

        if not appid or appid in ("0", "N/A", "unknown"):
            self.manifest_label.setText("Manifest: N/A")
            self.manifest_label.setStyleSheet("color: rgba(255, 255, 255, 0.45); font-size: 12px; font-style: italic;")
            return

        last_updated = None
        if self.parent_dialog and hasattr(self.parent_dialog, "_manifest_mtimes"):
            last_updated = self.parent_dialog._manifest_mtimes.get(appid)

        if last_updated is None:
            try:
                from core import morrenus_api
                if morrenus_api:
                    fpath = morrenus_api.get_manifest_zip_path(appid, morrenus_api.get_selected_branch(appid))
                    if fpath.exists():
                        try:
                            last_updated = fpath.stat().st_mtime
                        except Exception:
                            pass
            except ImportError:
                pass

        if not last_updated:
            self.manifest_label.setText("Manifest: Not Found")
            self.manifest_label.setStyleSheet("color: rgba(255, 255, 255, 0.45); font-size: 12px; font-style: italic;")
        else:
            import time
            age_seconds = time.time() - last_updated
            if age_seconds < 0:
                age_seconds = 0

            if age_seconds < 60:
                age_str = f"{int(age_seconds)}s"
            elif age_seconds < 3600:
                age_str = f"{int(age_seconds // 60)}m"
            elif age_seconds < 86400:
                age_str = f"{int(age_seconds // 3600)}h"
            else:
                days = int(age_seconds // 86400)
                if days < 30:
                    age_str = f"{days}d"
                elif days < 365:
                    age_str = f"{days // 30}mo"
                else:
                    age_str = f"{days // 365}y"

            self.manifest_label.setText(f"Manifest: Cached ({age_str} ago)")
            self.manifest_label.setStyleSheet("color: rgba(255, 255, 255, 0.65); font-size: 12px; font-style: italic;")

        self.update_pinned_label()

    def update_pinned_label(self) -> None:
        """Update pinned build label under the update status badge (cached lookup)."""
        lbl = getattr(self, "pinned_build_label", None) or getattr(self, "pinned_label", None)
        if not lbl:
            return

        appid = str(self.game_data.get("appid", "0"))
        pinned_cache = getattr(self.parent_dialog, "_pinned_cache", None) if self.parent_dialog else None
        
        if pinned_cache is not None:
            is_pinned = appid in pinned_cache
            bid = pinned_cache.get(appid, "")
        else:
            from utils.settings import get_settings
            s = get_settings()
            is_pinned = s.value(f"pin_build/{appid}", False, type=bool)
            bid = s.value(f"installed_buildid/{appid}", "", type=str)

        if is_pinned:
            if not bid:
                bid = str(self.game_data.get("buildid") or "")
            bid_str = f"Build: {bid}" if bid else "Pinned"
            lbl.setText(bid_str)
            lbl.setStyleSheet(
                "color: #FFB84D; "
                "font-size: 11px; "
                "font-weight: bold; "
                "background: transparent; "
                "padding-right: 4px;"
            )
            lbl.setVisible(True)
        else:
            lbl.setVisible(False)

    def update_status(self, update_status: str) -> None:
        """Update update and manifest status labels in-place."""
        is_accela = bool(self.game_data.get("accela_marker_path") or self.game_data.get("source") == "ACCELA")
        is_at0m_game = not is_accela and bool(
            self.game_data.get("is_atom")
            or self.game_data.get("is_vapor")
            or self.game_data.get("is_plugin_game")
            or self.game_data.get("source") == "at0-m"
        )
        if is_at0m_game and update_status not in ("update_available", "key_pending"):
            update_status = "at0m"

        self.game_data["update_status"] = update_status
        is_at0m = update_status in ("vapor", "at0m", "at0-m")
        if is_at0m:
            if hasattr(self, "status_label") and self.status_label:
                self.status_label.setText("[AT0-M]")
                self.status_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
                self.status_label.setStyleSheet(
                    "color: #CE93D8; "
                    "background: transparent; "
                    "font-size: 11px; "
                    "font-weight: bold; "
                    "padding: 2px 2px;"
                )
        else:
            status_map = {
                "update_available": ("New version available", "#FF8A80", "rgba(229, 115, 115, 0.15)"),
                "key_pending": ("Keys Pending", "#FFB74D", "rgba(255, 183, 77, 0.15)"),
                "up_to_date": ("Up to date", "#81C784", "rgba(129, 199, 132, 0.15)"),
                "checking": ("Checking for updates...", "#FFA726", "rgba(255, 167, 38, 0.12)"),
            }

            text, color, bg_color = status_map.get(
                update_status, ("Unable to check updates", "#B0BEC5", "rgba(176, 190, 197, 0.12)")
            )
            if is_at0m_game and update_status == "update_available":
                text = "AT0-M: Keys Ready"
            elif is_at0m_game and update_status == "key_pending":
                text = "AT0-M: Awaiting Hubcap Key"

            if hasattr(self, "status_label") and self.status_label:
                self.status_label.setText(text)
                self.status_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
                self.status_label.setStyleSheet(
                    f"color: {color}; "
                    f"background-color: {bg_color}; "
                    f"border-radius: 10px; "
                    f"padding: 3px 10px; "
                    f"font-size: 11px; "
                    f"font-weight: bold;"
                )

        self.update_manifest_label()
        self.update_denuvo_badge()
        self.update_proton_badge()

    def update_denuvo_badge(self) -> None:
        """Update the Denuvo status badge in-place."""
        if not hasattr(self, "denuvo_badge") or not self.denuvo_badge:
            return

        appid = self.game_data.get("appid", "0")
        if not appid or appid in ("0", "N/A", "unknown"):
            self.denuvo_badge.hide()
            return

        try:
            from core.ratings import get_denuvo_status
            status = get_denuvo_status(appid)
        except ImportError:
            status = None
 
        if not status:
            self.denuvo_badge.hide()
            return
 
        if status == "cracked":
            text = "Denuvo Cracked"
            color = "#81C784"
            bg_color = "rgba(129, 199, 132, 0.12)"
            border_color = "rgba(129, 199, 132, 0.25)"
        elif status == "hypervisor":
            text = "Denuvo Hypervisor"
            color = "#FFA726"
            bg_color = "rgba(255, 167, 38, 0.12)"
            border_color = "rgba(255, 167, 38, 0.25)"
        else:  # uncracked
            text = "Denuvo Uncracked"
            color = "#E57373"
            bg_color = "rgba(229, 115, 115, 0.12)"
            border_color = "rgba(229, 115, 115, 0.25)"
 
        self.denuvo_badge.setText(text)
        self.denuvo_badge.setStyleSheet(
            f"color: {color}; "
            f"background-color: {bg_color}; "
            f"border: 1px solid {border_color}; "
            f"border-radius: 4px; "
            f"padding: 1px 6px; "
            f"font-size: 9px; "
            f"font-weight: bold;"
        )
        self.denuvo_badge.show()

    def update_proton_badge(self) -> None:
        """Update the ProtonDB rating badge in-place."""
        if not hasattr(self, "proton_badge") or not self.proton_badge:
            return

        appid = self.game_data.get("appid", "0")
        if not appid or appid in ("0", "N/A", "unknown"):
            self.proton_badge.hide()
            return

        try:
            from core.ratings import get_protondb_tier
            tier = get_protondb_tier(appid)
        except ImportError:
            tier = None

        if not tier:
            # Currently loading asynchronously on the backend
            self.proton_badge.setText("FETCHING...")
            self.proton_badge.setStyleSheet(
                "color: #B0BEC5; "
                "background-color: rgba(255, 255, 255, 0.08); "
                "border: 1px solid rgba(255, 255, 255, 0.20); "
                "border-radius: 4px; "
                "padding: 1px 6px; "
                "font-size: 9px; "
                "font-weight: bold; "
                "letter-spacing: 0.5px;"
            )
            self.proton_badge.show()
            return

        if tier == "unknown":
            self.proton_badge.hide()
            return

        _tier_map = {
            "platinum": ("PLATINUM", "#90CAF9", "rgba(33, 150, 243, 0.15)", "rgba(144, 202, 249, 0.30)"),
            "gold":     ("GOLD",     "#FFE082", "rgba(255, 193, 7, 0.15)",   "rgba(255, 224, 130, 0.30)"),
            "silver":   ("SILVER",   "#CFD8DC", "rgba(144, 164, 174, 0.15)", "rgba(207, 216, 220, 0.30)"),
            "bronze":   ("BRONZE",   "#FFAB91", "rgba(255, 112, 67, 0.15)",  "rgba(255, 171, 145, 0.30)"),
            "borked":   ("BORKED",   "#EF9A9A", "rgba(239, 83, 80, 0.18)",   "rgba(239, 154, 154, 0.35)"),
            "native":   ("NATIVE",   "#A5D6A7", "rgba(76, 175, 80, 0.15)",   "rgba(165, 214, 167, 0.30)"),
        }

        if tier in _tier_map:
            text, color, bg_color, border_color = _tier_map[tier]
            self.proton_badge.setText(text)
            self.proton_badge.setStyleSheet(
                f"color: {color}; "
                f"background-color: {bg_color}; "
                f"border: 1px solid {border_color}; "
                f"border-radius: 4px; "
                f"padding: 1px 6px; "
                f"font-size: 9px; "
                f"font-weight: bold; "
                f"letter-spacing: 0.5px;"
            )
            self.proton_badge.show()
        else:
            self.proton_badge.hide()

    def _dlc_mode_applied(self) -> bool:
        """True when this game is tracked through DLC-only mode."""
        if self.game_data.get("is_dlc_only"):
            return True
        appid = str(self.game_data.get("appid", "") or "")
        if not appid:
            return False
        try:
            from utils.dlc_helpers import is_dlc_only_mode
            return bool(is_dlc_only_mode(appid))
        except Exception as exc:
            logger.debug("DLC mode lookup failed for %s: %s", appid, exc)
            return False

    def set_image(self, pixmap: QPixmap) -> None:
        """Sets the image on the label, scaling it nicely with right-fade blend."""
        if not pixmap or pixmap.isNull():
            return

        target_size = self.image_label.size()
        scaled = pixmap.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )

        if self._is_steam_tab and not self._dlc_mode_applied():
            # A Steam game that is not tracked in DLC mode is a catalogue
            # entry, not managed content: keep its artwork muted.
            scaled = QPixmap.fromImage(
                scaled.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
            )

        cropped_faded = QPixmap(target_size)
        cropped_faded.fill(Qt.GlobalColor.transparent)

        painter = QPainter(cropped_faded)
        dx = (target_size.width() - scaled.width()) // 2
        dy = (target_size.height() - scaled.height()) // 2
        painter.drawPixmap(dx, dy, scaled)

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        gradient = QLinearGradient(0, 0, target_size.width(), 0)
        gradient.setColorAt(0.0, QColor(0, 0, 0, 255))
        gradient.setColorAt(0.5, QColor(0, 0, 0, 255))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))

        painter.fillRect(cropped_faded.rect(), QBrush(gradient))
        painter.end()

        self.image_label.setPixmap(cropped_faded)

    def set_selected(self, selected: bool) -> None:
        """Update the checkbox checked state visually."""
        self._is_selected = selected
        if self.checkbox is not None:
            self.checkbox.setChecked(selected)

    def sizeHint(self) -> QSize:
        """Return size hint that matches the desired row height."""
        return QSize(400, 130)


class GameTileWidget(GameItemWidget):
    """
    Vertical card widget for displaying a game item in a 3x3 grid (Tiles mode).
    Layout:
    [ Header image ~214x100 ]
    [ Game Name (bold, white) ]
    [ Subtitle / Publisher or Size ]
    [ App ID: {appid} ]
    [ Badges (DLC / Status / Proton / Denuvo) ]
    """

    def __init__(
        self,
        game_data: dict,
        size_str: str,
        accent_color: str,
        background_color: str,
        select_mode: bool = False,
        is_selected: bool = False,
        applist_2_0_enabled: bool = True,
        parent_dialog = None,
    ):
        super().__init__(
            game_data,
            size_str,
            accent_color,
            background_color,
            select_mode=select_mode,
            is_selected=is_selected,
            applist_2_0_enabled=applist_2_0_enabled,
            parent_dialog=parent_dialog,
        )

    def _init_ui(self, size_str: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 8)
        layout.setSpacing(4)

        # 1. Image Container (460x215 ratio scaled to ~214x100)
        self.image_label = QLabel()
        self.image_label.setFixedSize(214, 100)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        name = self.game_data.get("game_name", "Unknown")
        display_name = format_game_display_name(self.game_data)
        self.image_label.setText(name[:2].upper())

        self.image_label.setStyleSheet(
            f"""
            QLabel {{
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                border-bottom-left-radius: 4px;
                border-bottom-right-radius: 4px;
                background-color: rgba(255, 255, 255, 0.03);
                color: {self.accent_color};
                font-size: 16px;
                font-weight: bold;
            }}
            """
        )
        layout.addWidget(self.image_label, 0, Qt.AlignmentFlag.AlignCenter)

        # Multi-select Checkbox overlay
        self.checkbox = QCheckBox(self.image_label)
        self.checkbox.setChecked(self._is_selected)
        self.checkbox.move(8, 8)
        self.checkbox.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.checkbox.setStyleSheet(
            f"""
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 2px solid rgba(255, 255, 255, 180);
                border-radius: 9px;
                background-color: rgba(0, 0, 0, 160);
            }}
            QCheckBox::indicator:checked {{
                background-color: {self.accent_color};
                border: 2px solid #000000;
            }}
            """
        )
        self.checkbox.setVisible(self._select_mode)

        # 2. Text info container
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(4, 2, 4, 0)
        info_layout.setSpacing(2)

        # Title
        name_only, dlc_mode = split_dlc_mode(display_name)
        self.name_label = QLabel(name_only)
        self.name_label.setWordWrap(False)
        fm = QFontMetrics(self.name_label.font())
        elided = fm.elidedText(name_only, Qt.TextElideMode.ElideRight, 206)
        self.name_label.setText(elided)
        self.name_label.setToolTip(name_only)
        self.name_label.setStyleSheet("color: #FFFFFF; font-size: 10pt; font-weight: bold;")
        info_layout.addWidget(self.name_label)

        # Subtitle (Publisher / Developer if present, or size)
        pub = self.game_data.get("publisher") or self.game_data.get("developer")
        if not pub:
            pub = f"Size: {size_str}" if size_str and size_str != "0 B" else (f"Build: {self.game_data.get('buildid')}" if self.game_data.get('buildid') else "Steam App")
        self.sub_label = QLabel(str(pub))
        self.sub_label.setToolTip(str(pub))
        fm_sub = QFontMetrics(self.sub_label.font())
        self.sub_label.setText(fm_sub.elidedText(str(pub), Qt.TextElideMode.ElideRight, 206))
        self.sub_label.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 8.5pt;")
        info_layout.addWidget(self.sub_label)

        # App ID
        appid = str(self.game_data.get("appid", ""))
        self.appid_label = QLabel(f"App ID: {appid}" if appid and appid not in ("0", "N/A", "unknown") else "")
        self.appid_label.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 8.5pt; font-family: monospace;")
        info_layout.addWidget(self.appid_label)

        # Check if Steam tab
        self._is_steam_tab = (
            (self.parent_dialog and getattr(self.parent_dialog, "_active_tab", "") == "steam")
            or self.game_data.get("is_steam_tab", False)
        )

        # Badges row
        badges_row = QHBoxLayout()
        badges_row.setContentsMargins(0, 2, 0, 0)
        badges_row.setSpacing(4)

        self.dlc_mode_badge = None
        if dlc_mode:
            self.dlc_mode_badge = QLabel("DLC")
            self.dlc_mode_badge.setToolTip("DLC-only mode")
            self.dlc_mode_badge.setStyleSheet(
                f"color: {self.accent_color}; border: 1px solid {self.accent_color}; "
                "border-radius: 3px; padding: 0px 4px; font-size: 8pt; font-weight: bold;"
            )
            badges_row.addWidget(self.dlc_mode_badge)

        self.status_label = QLabel()
        self.status_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        badges_row.addWidget(self.status_label)

        self.pinned_build_label = QLabel()
        self.pinned_build_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.pinned_build_label.setVisible(False)
        self.pinned_label = self.pinned_build_label
        badges_row.addWidget(self.pinned_build_label)

        self.denuvo_badge = QLabel()
        self.denuvo_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.denuvo_badge.hide()
        badges_row.addWidget(self.denuvo_badge)

        self.proton_badge = QLabel()
        self.proton_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.proton_badge.hide()
        badges_row.addWidget(self.proton_badge)

        self.manifest_label = QLabel()
        if not self._is_steam_tab:
            badges_row.addWidget(self.manifest_label)
            self.update_manifest_label()
        else:
            self.manifest_label.setVisible(False)

        badges_row.addStretch()
        info_layout.addLayout(badges_row)

        layout.addLayout(info_layout)
        self.update_pinned_label()

    def set_image(self, pixmap: QPixmap) -> None:
        """Sets the image on the label with rounded corners."""
        if not pixmap or pixmap.isNull():
            return
        target_size = self.image_label.size()
        scaled = pixmap.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if self._is_steam_tab and not self._dlc_mode_applied():
            scaled = QPixmap.fromImage(
                scaled.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
            )

        rounded = QPixmap(target_size)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        from PyQt6.QtGui import QPainterPath
        path = QPainterPath()
        path.addRoundedRect(0, 0, target_size.width(), target_size.height(), 6, 6)
        painter.setClipPath(path)

        dx = (target_size.width() - scaled.width()) // 2
        dy = (target_size.height() - scaled.height()) // 2
        painter.drawPixmap(dx, dy, scaled)
        painter.end()

        self.image_label.setPixmap(rounded)

    def sizeHint(self) -> QSize:
        return QSize(226, 195)


class GameCompactWidget(GameItemWidget):
    """
    Compact single-line text row widget (Text-only mode).
    [ Checkbox ] [ Name (bold) ] [ Badges ] [ App ID ] [ Size ]
    """

    def __init__(
        self,
        game_data: dict,
        size_str: str,
        accent_color: str,
        background_color: str,
        select_mode: bool = False,
        is_selected: bool = False,
        applist_2_0_enabled: bool = True,
        parent_dialog = None,
    ):
        super().__init__(
            game_data,
            size_str,
            accent_color,
            background_color,
            select_mode=select_mode,
            is_selected=is_selected,
            applist_2_0_enabled=applist_2_0_enabled,
            parent_dialog=parent_dialog,
        )

    def _init_ui(self, size_str: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 14, 4)
        layout.setSpacing(10)

        # Checkbox
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(self._is_selected)
        self.checkbox.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.checkbox.setStyleSheet(
            f"""
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border: 2px solid rgba(255, 255, 255, 180);
                border-radius: 8px;
                background-color: rgba(0, 0, 0, 160);
            }}
            QCheckBox::indicator:checked {{
                background-color: {self.accent_color};
                border: 2px solid #000000;
            }}
            """
        )
        self.checkbox.setVisible(self._select_mode)
        layout.addWidget(self.checkbox)

        # Title
        display_name = format_game_display_name(self.game_data)
        name_only, dlc_mode = split_dlc_mode(display_name)
        self.name_label = QLabel(name_only)
        self.name_label.setToolTip(name_only)
        self.name_label.setStyleSheet("color: #FFFFFF; font-size: 11px; font-weight: bold;")
        layout.addWidget(self.name_label, 1)

        # Check if Steam tab
        self._is_steam_tab = (
            (self.parent_dialog and getattr(self.parent_dialog, "_active_tab", "") == "steam")
            or self.game_data.get("is_steam_tab", False)
        )

        # DLC Badge
        self.dlc_mode_badge = None
        if dlc_mode:
            self.dlc_mode_badge = QLabel("DLC")
            self.dlc_mode_badge.setStyleSheet(
                f"color: {self.accent_color}; border: 1px solid {self.accent_color}; "
                "border-radius: 3px; padding: 0px 4px; font-size: 8pt; font-weight: bold;"
            )
            layout.addWidget(self.dlc_mode_badge)

        self.status_label = QLabel()
        self.status_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.status_label)

        self.pinned_build_label = QLabel()
        self.pinned_build_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.pinned_build_label.setVisible(False)
        self.pinned_label = self.pinned_build_label
        layout.addWidget(self.pinned_build_label)

        self.denuvo_badge = QLabel()
        self.denuvo_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.denuvo_badge.hide()
        layout.addWidget(self.denuvo_badge)

        self.proton_badge = QLabel()
        self.proton_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.proton_badge.hide()
        layout.addWidget(self.proton_badge)

        self.manifest_label = QLabel()
        if not self._is_steam_tab:
            layout.addWidget(self.manifest_label)
            self.update_manifest_label()
        else:
            self.manifest_label.setVisible(False)

        # App ID
        appid = str(self.game_data.get("appid", ""))
        self.appid_label = QLabel(f"AppID: {appid}" if appid and appid not in ("0", "N/A", "unknown") else "")
        self.appid_label.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 9px; font-family: monospace;")
        layout.addWidget(self.appid_label)

        # Size
        self.size_label = QLabel(size_str)
        self.size_label.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 10px;")
        layout.addWidget(self.size_label)

        self.update_pinned_label()

    def set_image(self, pixmap: QPixmap) -> None:
        """Compact text view does not display images."""
        pass

    def sizeHint(self) -> QSize:
        return QSize(400, 34)
