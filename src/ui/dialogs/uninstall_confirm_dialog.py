"""
uninstall_confirm_dialog.py - Dedicated confirmation modal for game uninstallation.

Features:
- Unified confirmation modal combining standard and advanced uninstall options.
- Per-card interactive layout: each toggle owns its own 'WILL REMOVE' / 'WILL KEEP' / 'ALWAYS' mapping.
- Live disk item rows with glyphs (✕ / ✓), middle-elided paths with tooltips, and calculated sizes.
- Dedicated 'I bought the game' preset mode: transforms into 'Cleanse my sins', visually unchecks
  all other cards, forces them to 'WILL KEEP' (green), and switches the button to 'Cleanse'.
- Non-blocking, cancelable background size calculation thread that updates 'Uninstall · frees X GB'.
- Respects DLC-only mode (locks base game files & prefix as kept; lists individual DLC depot cards).
- Custom themed frame (install_themed_frame) with power-close button and user accent color styling.
"""

import logging
import os
import platform
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.library.widgets import format_size
from ui.dialogs.themed_dialog import install_themed_frame
from utils.paths import Paths
from utils.settings import get_settings

logger = logging.getLogger(__name__)

RED = "#f87171"
GREEN = "#4ade80"
AMBER = "#fbbf24"

check_icon_path = str(Paths.icon("check_white.svg")).replace("\\", "/")

UNINSTALL_QSS = f"""
QLabel#UninstallHeader {{
    color: #FFFFFF;
    font-size: 10pt;
    font-weight: bold;
    background: transparent;
}}

QFrame#BoughtBox {{
    background: #16221b;
    border: 1px solid #25402f;
    border-radius: 8px;
}}
QFrame#BoughtBox[on="true"] {{
    background: #1a2f22;
    border: 1px solid {GREEN};
}}
QFrame#BoughtBox QCheckBox {{
    color: {GREEN};
    font-size: 9.5pt;
    font-weight: bold;
    spacing: 10px;
    background: transparent;
}}

QFrame#Card {{
    background-color: #181c22;
    border: 1px solid #262b34;
    border-radius: 8px;
}}
QFrame#Card[state="remove"] {{
    border-color: #4a2a2e;
}}
QFrame#Card[state="keep"] {{
    border-color: #223a2b;
}}
QFrame#Card[state="locked"] {{
    border-color: #4a3d22;
}}

QFrame#Bar {{
    border-radius: 2px;
    background: #333333;
}}
QFrame#Card[state="remove"] QFrame#Bar {{
    background: {RED};
}}
QFrame#Card[state="keep"] QFrame#Bar {{
    background: {GREEN};
}}
QFrame#Card[state="locked"] QFrame#Bar {{
    background: {AMBER};
}}

QLabel#Pill {{
    border-radius: 9px;
    padding: 2px 9px;
    font-size: 8pt;
    font-weight: bold;
}}
QFrame#Card[state="remove"] QLabel#Pill {{
    color: {RED};
    background: #2a1a1d;
}}
QFrame#Card[state="keep"] QLabel#Pill {{
    color: {GREEN};
    background: #15261c;
}}
QFrame#Card[state="locked"] QLabel#Pill {{
    color: {AMBER};
    background: #2a2416;
}}

QLabel#Blurb {{
    color: #8a93a3;
    font-size: 8.5pt;
    background: transparent;
}}
QLabel#ItemGlyph {{
    font-weight: bold;
    font-size: 9pt;
    background: transparent;
}}
QFrame#Card[state="remove"] QLabel#ItemGlyph {{
    color: {RED};
}}
QFrame#Card[state="keep"] QLabel#ItemGlyph {{
    color: {GREEN};
}}
QFrame#Card[state="locked"] QLabel#ItemGlyph {{
    color: {AMBER};
}}

QLabel#ItemName {{
    color: #c4cad6;
    font-size: 9pt;
    background: transparent;
}}
QLabel#ItemPath {{
    color: #6f7888;
    font-size: 8pt;
    font-family: monospace;
    background: transparent;
}}
QLabel#ItemSize {{
    color: #aab2c0;
    font-size: 8.5pt;
    font-weight: bold;
    background: transparent;
}}
QLabel#ItemMissing {{
    color: #5b6372;
    font-size: 8.5pt;
    font-style: italic;
    background: transparent;
}}

QCheckBox {{
    color: #e8ecf2;
    font-weight: 600;
    font-size: 9.5pt;
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1.5px solid #4a5160;
    background: #1b1f26;
}}
QCheckBox::indicator:hover {{
    border: 1.5px solid #8fb4ff;
}}
QCheckBox::indicator:checked {{
    background: #3b82f6;
    border: 1.5px solid #3b82f6;
    image: url({check_icon_path});
}}
QCheckBox::indicator:disabled {{
    background: #20242c;
    border: 1.5px solid #2c313b;
}}
QCheckBox::indicator:checked:disabled {{
    background: #3a4560;
    border: 1.5px solid #3a4560;
    image: url({check_icon_path});
}}
"""


def _repolish(w: QWidget) -> None:
    style = w.style()
    if style:
        style.unpolish(w)
        style.polish(w)


class ElidedLabel(QLabel):
    """Single-line label that middle-elides long file paths and sets tooltip to full text."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip(text)
        self._refresh()

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = text
        self.setToolTip(text)
        self._refresh()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        if not self._full_text:
            super().setText("")
            return
        fm = QFontMetrics(self.font())
        elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, max(self.width(), 40))
        super().setText(elided)


class ItemRow(QWidget):
    """A row inside an OptionCard representing a specific disk path or configuration entry."""

    def __init__(
        self,
        label: str,
        path: Optional[str] = None,
        exists: bool = True,
        size_bytes: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.label_text = label
        self.path = path
        self.exists = exists
        self.size_bytes = size_bytes

        lay = QHBoxLayout(self)
        lay.setContentsMargins(28, 1, 6, 1)
        lay.setSpacing(8)

        self.glyph_lbl = QLabel("✕")
        self.glyph_lbl.setObjectName("ItemGlyph")
        self.glyph_lbl.setFixedWidth(14)
        lay.addWidget(self.glyph_lbl)

        self.name_lbl = QLabel(label)
        self.name_lbl.setObjectName("ItemName")
        lay.addWidget(self.name_lbl)

        if path and exists:
            self.path_lbl = ElidedLabel(path)
            self.path_lbl.setObjectName("ItemPath")
            lay.addWidget(self.path_lbl, 1)
        elif not exists:
            self.miss_lbl = QLabel("nothing found")
            self.miss_lbl.setObjectName("ItemMissing")
            lay.addWidget(self.miss_lbl)
            lay.addStretch(1)
        else:
            lay.addStretch(1)

        self.size_lbl = QLabel(format_size(size_bytes) if size_bytes else "")
        self.size_lbl.setObjectName("ItemSize")
        self.size_lbl.setMinimumWidth(64)
        self.size_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.size_lbl)

    def set_removing(self, removing: bool, is_locked: bool = False) -> None:
        if is_locked:
            self.glyph_lbl.setText("•")
        elif removing:
            self.glyph_lbl.setText("✕")
        else:
            self.glyph_lbl.setText("✓")

    def set_size(self, n: int) -> None:
        self.size_bytes = n
        if n > 0:
            self.exists = True
        self.size_lbl.setText(format_size(n))


class OptionCard(QFrame):
    """A card containing a toggle checkbox, left colored bar, pill, and individual item rows."""

    toggled = pyqtSignal()

    def __init__(
        self,
        key: str,
        title: str,
        blurb: str,
        items: List[dict],
        checked: bool = True,
        locked: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.key = key
        self.locked = locked
        self._forced_keep = False
        self._saved_checked = checked
        self.setObjectName("Card")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.bar = QFrame()
        self.bar.setObjectName("Bar")
        self.bar.setFixedWidth(4)
        outer.addWidget(self.bar)

        body = QVBoxLayout()
        body.setContentsMargins(12, 10, 14, 10)
        body.setSpacing(4)
        outer.addLayout(body, 1)

        head = QHBoxLayout()
        # Escape single ampersands as && so Qt does not treat & as a keyboard accelerator mnemonic
        safe_title = title.replace("&", "&&") if "&&" not in title else title
        self.check = QCheckBox(safe_title)
        self.check.setChecked(checked)
        self.check.setEnabled(not locked)
        self.check.toggled.connect(self._on_check_toggled)
        head.addWidget(self.check, 1)

        self.pill = QLabel()
        self.pill.setObjectName("Pill")
        head.addWidget(self.pill)
        body.addLayout(head)

        self.blurb_lbl = QLabel(blurb)
        self.blurb_lbl.setObjectName("Blurb")
        self.blurb_lbl.setContentsMargins(28, 0, 0, 4)
        self.blurb_lbl.setWordWrap(True)
        body.addWidget(self.blurb_lbl)

        self.rows: List[ItemRow] = []
        for it in items:
            row = ItemRow(
                label=it.get("label", ""),
                path=it.get("path"),
                exists=it.get("exists", True),
                size_bytes=it.get("size_bytes"),
            )
            self.rows.append(row)
            body.addWidget(row)

        self._sync()

    def _on_check_toggled(self) -> None:
        self._sync()
        self.toggled.emit()

    def removing(self) -> bool:
        if self.locked:
            return True
        return self.check.isChecked() and not self._forced_keep

    def force_keep(self, on: bool) -> None:
        """When 'Cleanse my sins' is enabled, force card to visually appear kept."""
        if self.locked:
            return
        self._forced_keep = on
        if on:
            self._saved_checked = self.check.isChecked()
            self.check.blockSignals(True)
            self.check.setChecked(False)  # visually unticked while overridden
            self.check.blockSignals(False)
            self.check.setEnabled(False)
        else:
            self.check.blockSignals(True)
            self.check.setChecked(getattr(self, "_saved_checked", self.check.isChecked()))
            self.check.blockSignals(False)
            self.check.setEnabled(True)
        self._sync()

    def _sync(self) -> None:
        removing = self.removing()
        if self.locked:
            state, text = "locked", "ALWAYS"
        else:
            state, text = ("remove", "WILL REMOVE") if removing else ("keep", "WILL KEEP")
        self.setProperty("state", state)
        self.pill.setText(text)
        for r in self.rows:
            r.set_removing(removing, is_locked=self.locked)
        _repolish(self)
        _repolish(self.pill)
        _repolish(self.bar)
        for r in self.rows:
            _repolish(r.glyph_lbl)

    def reclaimable_bytes(self) -> int:
        if not self.removing() or self.locked:
            return 0
        return sum(r.size_bytes or 0 for r in self.rows if (r.exists or (r.size_bytes and r.size_bytes > 0)))


class SizeScannerThread(QThread):
    """Background worker to lazily calculate directory sizes without freezing the GUI."""

    size_found = pyqtSignal(str, int, int)  # card_key, row_idx, size_bytes
    finished = pyqtSignal()

    def __init__(self, targets: List[tuple]):
        """targets: list of (card_key, row_idx, path)"""
        super().__init__()
        self.targets = targets
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    def run(self) -> None:
        for card_key, row_idx, path in self.targets:
            if self._stopped:
                return
            if not path or not os.path.exists(path):
                continue
            total = 0
            try:
                if os.path.isfile(path):
                    total = os.path.getsize(path)
                else:
                    for root, _dirs, files in os.walk(path):
                        if self._stopped:
                            return
                        for f in files:
                            if self._stopped:
                                return
                            fp = os.path.join(root, f)
                            try:
                                total += os.path.getsize(fp)
                            except OSError:
                                pass
                if not self._stopped:
                    self.size_found.emit(card_key, row_idx, total)
            except Exception as e:
                logger.debug(f"[SizeScanner] Error scanning {path}: {e}")
        if not self._stopped:
            self.finished.emit()


# Alias for backward compatibility with existing tests
PrefixScannerThread = SizeScannerThread


def find_compatdata_path(
    appid: str,
    install_path: Optional[str] = None,
    library_path: Optional[str] = None,
) -> Optional[str]:
    """Discover the Proton prefix (compatdata) path on disk for a given AppID."""
    if not appid or appid in ("0", "N/A", "unknown"):
        return None

    candidates = []
    if library_path:
        candidates.append(os.path.join(library_path, "steamapps", "compatdata", str(appid)))

    if install_path:
        try:
            p = Path(install_path).resolve()
            for parent in p.parents:
                if parent.name.lower() == "steamapps":
                    candidates.append(str(parent / "compatdata" / str(appid)))
                    break
        except Exception as e:
            logger.debug(f"[UninstallConfirm] Path traversal error checking compatdata: {e}")

    try:
        from core.steam_helpers import get_steam_libraries

        for lib in get_steam_libraries() or []:
            candidates.append(os.path.join(lib, "steamapps", "compatdata", str(appid)))
    except Exception as e:
        logger.debug(f"[UninstallConfirm] Error reading steam libraries for compatdata: {e}")

    for cp in candidates:
        if os.path.exists(cp):
            return cp
    return None


def find_acf_path(appid: str, library_path: Optional[str] = None) -> Optional[str]:
    """Locate the steamapps/appmanifest_{appid}.acf file if it exists."""
    if not appid or appid in ("0", "N/A", "unknown"):
        return None
    try:
        from core.steam_helpers import get_steam_libraries

        libs = list(get_steam_libraries() or [])
        if library_path and library_path not in libs:
            libs.append(library_path)
        for lib in libs:
            p = Path(lib) / "steamapps" / f"appmanifest_{appid}.acf"
            if p.exists():
                return str(p)
    except Exception as e:
        logger.debug(f"[UninstallConfirm] Error finding ACF file: {e}")
    return None


def find_userdata_saves_path(appid: str, library_path: Optional[str] = None) -> Optional[str]:
    """Locate local Steam Cloud userdata saves folder for this game."""
    if not appid or appid in ("0", "N/A", "unknown"):
        return None
    try:
        from core.steam_helpers import get_steam_libraries

        libs = list(get_steam_libraries() or [])
        if library_path and library_path not in libs:
            libs.append(library_path)
        for lib in libs:
            userdata_dir = os.path.join(lib, "userdata")
            if os.path.isdir(userdata_dir):
                for uid in os.listdir(userdata_dir):
                    p = os.path.join(userdata_dir, uid, str(appid), "remote")
                    if os.path.exists(p):
                        return p
                    p_root = os.path.join(userdata_dir, uid, str(appid))
                    if os.path.exists(p_root):
                        return p_root
    except Exception as e:
        logger.debug(f"[UninstallConfirm] Error finding userdata saves: {e}")
    return None


class UninstallConfirmDialog(QDialog):
    """
    Unified confirmation modal for uninstallation with per-card inline mapping.
    Displays customizable removal options with colored bars, status pills, live glyphs,
    elided paths, calculated sizes, and a dedicated 'I bought the game' preset.
    """

    def __init__(self, parent: Optional[QWidget] = None, game_data: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.game_data = game_data or {}
        self.game_name = self.game_data.get("game_name", "this game")
        self.appid = str(self.game_data.get("appid", "0"))
        self.install_path = self.game_data.get("install_path") or ""
        self.library_path = self.game_data.get("library_path") or ""

        # Fallback path resolution for AT0-M titles
        if not self.install_path or not os.path.exists(self.install_path):
            try:
                from utils.plugin_games import get_atom_game_install_info

                atom_info = get_atom_game_install_info(self.appid)
                if atom_info:
                    self.install_path = atom_info.get("common_path") or atom_info.get("install_path") or ""
                    if not self.library_path:
                        self.library_path = atom_info.get("library_path") or ""
            except Exception as e:
                logger.debug(f"[UninstallConfirm] Could not resolve AT0-M install info: {e}")

        # DLC-Only detection
        self.is_dlc_only = False
        if self.appid and self.appid not in ("0", "N/A", "unknown"):
            from utils.dlc_helpers import is_dlc_only_mode

            self.is_dlc_only = (
                is_dlc_only_mode(self.appid)
                or bool(self.game_data.get("dlc_only"))
                or bool(self.game_data.get("is_dlc_only"))
            )
            if not self.is_dlc_only:
                try:
                    from utils.plugin_games import get_plugin_game

                    prec = get_plugin_game(self.appid, active_only=False)
                    if prec and prec.get("dlc_only"):
                        self.is_dlc_only = True
                except Exception as e:
                    logger.debug(f"[UninstallConfirm] Could not check DLC-only status: {e}")

        self.acf_path = find_acf_path(self.appid, self.library_path)
        self.compatdata_path = None
        self.saves_path = None
        if platform.system() == "Linux" and not self.is_dlc_only:
            self.compatdata_path = find_compatdata_path(self.appid, self.install_path, self.library_path)
            self.saves_path = find_userdata_saves_path(self.appid, self.library_path)

        self._scanner: Optional[SizeScannerThread] = None
        self._scan_started = False
        self.cards: Dict[str, OptionCard] = {}
        self._dlc_cards: Dict[str, OptionCard] = {}

        # Internal summary label kept for backward compatibility with tests/logs
        self.summary_lbl = QLabel()
        self.summary_lbl.hide()

        settings = get_settings()
        self.accent_color = (
            getattr(parent, "accent_color", None)
            or (settings.value("accent_color", "#a1c9fd") if settings else "#a1c9fd")
        )

        self.setMinimumWidth(540)
        self.setMaximumWidth(620)
        self.setStyleSheet(UNINSTALL_QSS)
        self._init_ui()

    def _init_ui(self) -> None:
        title = f"Uninstall - {self.game_name}"
        layout = install_themed_frame(self, title, use_power_close=True)
        layout.setSpacing(10)

        # Header question
        header_text = (
            f"Uninstall options for DLCs of <b>{self.game_name}</b>:"
            if self.is_dlc_only
            else f"Uninstall options for <b>{self.game_name}</b>:"
        )
        self.header_lbl = QLabel(header_text)
        self.header_lbl.setObjectName("UninstallHeader")
        self.header_lbl.setWordWrap(True)
        layout.addWidget(self.header_lbl)

        # Scrollable container for cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")

        scroll_widget = QWidget()
        scroll_widget.setStyleSheet("background: transparent;")
        cards_layout = QVBoxLayout(scroll_widget)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(8)

        if not self.is_dlc_only:
            # 1. Preset Box: 'I bought the game' -> 'Cleanse my sins'
            self.bought_box = QFrame()
            self.bought_box.setObjectName("BoughtBox")
            b_layout = QHBoxLayout(self.bought_box)
            b_layout.setContentsMargins(12, 10, 12, 10)

            self.bought_cb = QCheckBox("I bought the game")
            self.bought_cb.setToolTip(
                "Cleans all SLSsteam integrations while leaving your game folder, saves, achievements, and prefix untouched."
            )
            self.bought_cb.toggled.connect(self._on_bought_toggled)
            b_layout.addWidget(self.bought_cb)
            cards_layout.addWidget(self.bought_box)

            # 2. Card: Game Files
            files_items = [
                {
                    "label": "Game folder",
                    "path": self.install_path,
                    "exists": bool(self.install_path and os.path.exists(self.install_path)),
                }
            ]
            if self.appid and self.appid not in ("0", "N/A", "unknown"):
                manifest_name = Path(self.acf_path).name if self.acf_path else f"appmanifest_{self.appid}.acf"
                files_items.append({
                    "label": "Steam manifest",
                    "path": self.acf_path or manifest_name,
                    "exists": bool(self.acf_path),
                })
            self.files_card = OptionCard(
                key="game_files",
                title="Delete game files",
                blurb="The installed game and Steam's record of it.",
                items=files_items,
                checked=True,
            )
            self.files_card.toggled.connect(self._on_card_toggled)
            self.cards["game_files"] = self.files_card
            cards_layout.addWidget(self.files_card)

            # 3. Card: SLSsteam config (Locked, ALWAYS)
            sls_items = [
                {
                    "label": "SLSsteam && AT0-M configuration entries",
                    "path": None,
                    "exists": True,
                }
            ]
            self.sls_card = OptionCard(
                key="sls_config",
                title="Clean SLSsteam && AT0-M entries",
                blurb="Always happens. Removes this game from your SLS config.",
                items=sls_items,
                checked=True,
                locked=True,
            )
            self.cards["sls_config"] = self.sls_card
            cards_layout.addWidget(self.sls_card)

            # 4. Card: Desktop shortcuts (Linux only)
            if platform.system() == "Linux":
                shortcuts_items = [
                    {
                        "label": "Application shortcuts && icons",
                        "path": None,
                        "exists": True,
                    }
                ]
                self.shortcuts_card = OptionCard(
                    key="shortcuts",
                    title="Remove desktop shortcuts && icons",
                    blurb="Menu entries created for this game.",
                    items=shortcuts_items,
                    checked=True,
                )
                self.shortcuts_card.toggled.connect(self._on_card_toggled)
                self.cards["shortcuts"] = self.shortcuts_card
                cards_layout.addWidget(self.shortcuts_card)
            else:
                self.shortcuts_card = None

            # 5. Card: Proton prefix (Linux only)
            if platform.system() == "Linux":
                compat_items = [
                    {
                        "label": "Proton prefix",
                        "path": self.compatdata_path,
                        "exists": bool(self.compatdata_path),
                    }
                ]
                self.compat_card = OptionCard(
                    key="compatdata",
                    title="Wipe Proton prefix (with saves)",
                    blurb="Windows-side saves and settings live here.",
                    items=compat_items,
                    checked=False,
                )
                if not self.compatdata_path:
                    self.compat_card.check.setText("Wipe Proton prefix (none found)")
                    self.compat_card.check.setEnabled(False)
                self.compat_card.toggled.connect(self._on_card_toggled)
                self.cards["compatdata"] = self.compat_card
                cards_layout.addWidget(self.compat_card)
            else:
                self.compat_card = None

            # 6. Card: Steam Cloud userdata saves (Linux only)
            if platform.system() == "Linux":
                saves_items = [
                    {
                        "label": "Cloud userdata",
                        "path": self.saves_path,
                        "exists": bool(self.saves_path),
                    }
                ]
                self.saves_card = OptionCard(
                    key="saves",
                    title="Remove local Steam Cloud saves",
                    blurb="Local copies in userdata. Cloud copies are untouched.",
                    items=saves_items,
                    checked=False,
                )
                self.saves_card.toggled.connect(self._on_card_toggled)
                self.cards["saves"] = self.saves_card
                cards_layout.addWidget(self.saves_card)
            else:
                self.saves_card = None

            # Backward-compatibility attribute mappings for tests
            self.bought_preset_cb = self.bought_cb
            self.delete_files_cb = self.files_card.check
            self.remove_sls_cb = self.sls_card.check
            self.remove_shortcuts_cb = self.shortcuts_card.check if self.shortcuts_card else None
            self.wipe_prefix_cb = self.compat_card.check if self.compat_card else None
            self.remove_saves_cb = self.saves_card.check if self.saves_card else None

        else:
            # DLC-Only mode: Base game & prefix are permanently locked / kept
            self.bought_box = None
            self.bought_cb = QCheckBox("I bought the game")
            self.bought_cb.hide()
            self.bought_preset_cb = self.bought_cb

            base_items = [
                {
                    "label": "Base game folder",
                    "path": self.install_path or "Base game directory",
                    "exists": bool(self.install_path and os.path.exists(self.install_path)),
                }
            ]
            self.base_card = OptionCard(
                key="base_game",
                title="Base game files",
                blurb="Preserved 100% intact. Only DLC files are removed.",
                items=base_items,
                checked=False,
                locked=False,
            )
            self.base_card.check.setEnabled(False)
            cards_layout.addWidget(self.base_card)

            from utils.dlc_helpers import get_dlc_only_info

            dlc_list = get_dlc_only_info(self.appid) or []
            for dlc in dlc_list:
                did = str(dlc.get("dlc_appid", "")).strip()
                if not did:
                    continue
                dname = dlc.get("dlc_name") or f"DLC {did}"
                dlc_card = OptionCard(
                    key=f"dlc_{did}",
                    title=f"{dname} ({did})",
                    blurb="DLC depot files and SLS configuration entries.",
                    items=[{"label": f"Depot {did}", "path": None, "exists": True}],
                    checked=True,
                )
                dlc_card.toggled.connect(self._on_card_toggled)
                self._dlc_cards[did] = dlc_card
                cards_layout.addWidget(dlc_card)

            if not dlc_list:
                cards_layout.addWidget(QLabel("No DLC depot records found."))

            # Dummy hidden widgets for tests in DLC mode
            self.delete_files_cb = QCheckBox("Delete game files")
            self.delete_files_cb.hide()
            self.remove_shortcuts_cb = QCheckBox("Remove desktop shortcuts")
            self.remove_shortcuts_cb.hide()
            self.wipe_prefix_cb = QCheckBox("Wipe Proton prefix (with saves)")
            self.wipe_prefix_cb.hide()
            self.remove_saves_cb = QCheckBox("Remove local Steam Cloud saves")
            self.remove_saves_cb.hide()
            self.remove_sls_cb = QCheckBox("Remove SLS")
            self.remove_sls_cb.hide()

        cards_layout.addStretch(1)
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll, 1)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setFixedSize(100, 28)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                color: #e5e7eb;
                font-size: 8.5pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                color: #ffffff;
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.confirm_btn = QPushButton("Uninstall")
        self.confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_btn.setFixedHeight(28)
        self.confirm_btn.setMinimumWidth(140)
        self.confirm_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.confirm_btn)

        layout.addLayout(btn_layout)
        self._refresh_confirm_button()
        self._update_summary()

    def _on_bought_toggled(self, checked: bool) -> None:
        """When 'I bought the game' is checked, transform into 'Cleanse my sins' and force keep on other cards."""
        self.bought_cb.setText("Cleanse my sins" if checked else "I bought the game")
        if self.bought_box:
            self.bought_box.setProperty("on", checked)
            _repolish(self.bought_box)
        for card in self.cards.values():
            card.force_keep(checked)
        self._refresh_confirm_button()
        self._update_summary()

    def _on_card_toggled(self) -> None:
        self._refresh_confirm_button()
        self._update_summary()

    def _refresh_confirm_button(self) -> None:
        bought = self.bought_cb.isChecked()
        if bought:
            self.confirm_btn.setText("Cleanse")
            self.confirm_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(34, 197, 94, 0.22);
                    color: #4ade80;
                    border: 1px solid rgba(34, 197, 94, 0.45);
                    border-radius: 5px;
                    font-size: 8.5pt;
                    font-weight: bold;
                    padding: 0 16px;
                }
                QPushButton:hover {
                    background-color: rgba(34, 197, 94, 0.35);
                    color: #ffffff;
                }
            """)
            self.confirm_btn.setEnabled(True)
            return

        if self.is_dlc_only:
            checked_count = sum(1 for c in self._dlc_cards.values() if c.removing())
            self.confirm_btn.setText(f"Uninstall Selected DLC ({checked_count})")
            self.confirm_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(229, 115, 115, 0.22);
                    color: #ff8a7a;
                    border: 1px solid rgba(229, 115, 115, 0.4);
                    border-radius: 5px;
                    font-size: 8.5pt;
                    font-weight: bold;
                    padding: 0 16px;
                }
                QPushButton:hover {
                    background-color: rgba(229, 115, 115, 0.35);
                    color: #ffffff;
                }
            """)
            self.confirm_btn.setEnabled(checked_count > 0)
            return

        reclaimable = sum(c.reclaimable_bytes() for c in self.cards.values())
        if reclaimable > 0:
            self.confirm_btn.setText(f"Uninstall · frees {format_size(reclaimable)}")
        else:
            self.confirm_btn.setText("Uninstall")

        self.confirm_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(229, 115, 115, 0.22);
                color: #ff8a7a;
                border: 1px solid rgba(229, 115, 115, 0.4);
                border-radius: 5px;
                font-size: 8.5pt;
                font-weight: bold;
                padding: 0 16px;
            }
            QPushButton:hover {
                background-color: rgba(229, 115, 115, 0.35);
                color: #ffffff;
            }
        """)
        self.confirm_btn.setEnabled(True)

    def _update_summary(self) -> None:
        """Maintains backward-compatible summary HTML text for tests and logging."""
        if self.is_dlc_only:
            self.summary_lbl.setText("Base game directory (100% intact)\nProton prefix & Windows saves (100% intact)")
            return

        if self.should_wipe_sls_only():
            self.summary_lbl.setText("Clean integrations mode\nBase game directory (100% intact)")
            return

        items = []
        if self.should_delete_game_files():
            items.append("Game folder")
        if self.should_wipe_prefix():
            items.append(f"Proton prefix (with saves): {self.compatdata_path or ''}")
        else:
            items.append(f"Proton prefix (Windows saves): {self.compatdata_path or ''}")
        self.summary_lbl.setText("\n".join(items))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._scan_started:
            self._scan_started = True
            targets = []
            if not self.is_dlc_only:
                if self.install_path and os.path.exists(self.install_path):
                    targets.append(("game_files", 0, self.install_path))
                if self.compatdata_path and os.path.exists(self.compatdata_path):
                    targets.append(("compatdata", 0, self.compatdata_path))
                if self.saves_path and os.path.exists(self.saves_path):
                    targets.append(("saves", 0, self.saves_path))

            if targets:
                self._scanner = SizeScannerThread(targets)
                self._scanner.size_found.connect(self._on_size_found)
                self._scanner.finished.connect(self._on_scan_finished)
                self._scanner.start()

    def _on_size_found(self, card_key: str, row_idx: int, size_bytes: int) -> None:
        card = self.cards.get(card_key)
        if card and row_idx < len(card.rows):
            card.rows[row_idx].set_size(size_bytes)
        self._refresh_confirm_button()

    def _on_scan_finished(self) -> None:
        self._refresh_confirm_button()

    def _cleanup_scanner(self) -> None:
        if self._scanner:
            try:
                self._scanner.stop()
                self._scanner.wait(150)
            except Exception as e:
                logger.debug(f"[UninstallConfirm] Error stopping size scanner: {e}")
            self._scanner = None

    def closeEvent(self, event) -> None:  # noqa: N802
        self._cleanup_scanner()
        super().closeEvent(event)

    def reject(self) -> None:
        self._cleanup_scanner()
        super().reject()

    def accept(self) -> None:
        self._cleanup_scanner()
        super().accept()

    def should_wipe_sls_only(self) -> bool:
        """Returns True if the user selected 'I bought the game' ('Cleanse my sins') preset."""
        return bool(self.bought_cb and self.bought_cb.isChecked())

    def should_delete_game_files(self) -> bool:
        """Returns True if game files should be deleted from disk."""
        if self.should_wipe_sls_only() or self.is_dlc_only:
            return False
        if hasattr(self, "files_card"):
            return self.files_card.removing()
        return True

    def should_remove_sls(self) -> bool:
        """Always removes SLSsteam entries on uninstall."""
        return True

    def should_wipe_prefix(self) -> bool:
        """Returns True if user explicitly opted to wipe the Proton prefix."""
        if self.should_wipe_sls_only() or self.is_dlc_only:
            return False
        if hasattr(self, "compat_card") and self.compat_card:
            return self.compat_card.removing()
        return False

    def should_remove_saves(self) -> bool:
        """Returns True if user opted to delete local Steam Cloud userdata saves."""
        if self.should_wipe_sls_only() or self.is_dlc_only:
            return False
        if hasattr(self, "saves_card") and self.saves_card:
            return self.saves_card.removing()
        return False

    def should_remove_shortcuts(self) -> bool:
        """Returns True if user opted to remove desktop shortcuts."""
        if self.should_wipe_sls_only() or self.is_dlc_only:
            return False
        if hasattr(self, "shortcuts_card") and self.shortcuts_card:
            return self.shortcuts_card.removing()
        return True

    def get_target_dlc_ids(self) -> Optional[List[str]]:
        """Returns list of selected DLC IDs to uninstall for DLC-only games."""
        if not self.is_dlc_only:
            return None
        return [did for did, card in self._dlc_cards.items() if card.removing()]
