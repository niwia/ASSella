"""
SHSAH Reborn - Achievements Tab for GameDetailsDialogV2.
Provides achievement viewing, toggleable unlocking/locking, and binary saving.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QPixmap, QIcon, QPainter, QColor
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QScrollArea,
    QStackedWidget,
    QMessageBox,
    QSizePolicy,
    QDialog,
)

from ui.material_progress import MaterialSpinner
from ui.dialogs.game_details.widgets import SwitchToggle
from managers.shsah_reborn import SHSAHRebornManager, AchievementItem
from utils.settings import get_settings
from utils.color_utils import get_best_foreground_color

logger = logging.getLogger(__name__)


def show_experimental_warning(parent, accent_color: str = "#a1c9fd") -> None:
    """Show one-time experimental warning dialog for achievement management."""
    settings = get_settings()
    if settings.value("slsah_experimental_warning_shown", False, type=bool):
        return

    import os
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        settings.setValue("slsah_experimental_warning_shown", True)
        settings.sync()
        return

    dlg = QDialog(parent)
    dlg.setWindowTitle("SHSAH Reborn — Experimental Feature")
    dlg.setModal(True)
    dlg.setFixedWidth(440)
    dlg.setStyleSheet(f"""
        QDialog {{
            background-color: #17181c;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
        }}
        QLabel {{
            color: #FFFFFF;
            background: transparent;
        }}
        QPushButton {{
            background-color: {accent_color};
            color: {get_best_foreground_color(accent_color)};
            border: none;
            border-radius: 5px;
            padding: 8px 18px;
            font-size: 9.5pt;
            font-weight: 600;
        }}
        QPushButton:hover {{
            opacity: 0.9;
        }}
    """)

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(22, 22, 22, 20)
    layout.setSpacing(14)

    title = QLabel("⚠️ Achievement Unlocking is Experimental")
    title.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {accent_color};")
    layout.addWidget(title)

    desc = QLabel(
        "Achievement editing modifies your local Steam stats cache (UserGameStats.bin).\n\n"
        "• Unlocked achievements apply immediately to your local client and SLS/emulators.\n"
        "• Modifying stats for VAC-secured or competitive online titles is not recommended.\n\n"
        "This warning will only be shown once."
    )
    desc.setWordWrap(True)
    desc.setStyleSheet("color: rgba(255, 255, 255, 0.78); font-size: 9pt; line-height: 1.4;")
    layout.addWidget(desc)

    btn_row = QHBoxLayout()
    btn_row.addStretch()
    ok_btn = QPushButton("I Understand")
    ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    ok_btn.clicked.connect(dlg.accept)
    btn_row.addWidget(ok_btn)

    layout.addLayout(btn_row)
    dlg.exec()

    settings.setValue("slsah_experimental_warning_shown", True)
    settings.sync()


class AchievementCard(QFrame):
    """Achievement row card displaying icon, title, description, and toggle switch."""

    state_toggled = pyqtSignal(str, bool)  # (api_name, is_unlocked)
    icon_loaded = pyqtSignal(str)          # (file_path)

    def __init__(
        self,
        item: AchievementItem,
        appid: str,
        manager: SHSAHRebornManager,
        accent_color: str = "#a1c9fd",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.item = item
        self.appid = appid
        self.manager = manager
        self.accent_color = accent_color

        self.setStyleSheet("""
            AchievementCard {
                background-color: rgba(255, 255, 255, 0.035);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 6px;
            }
            AchievementCard:hover {
                background-color: rgba(255, 255, 255, 0.055);
                border-color: rgba(255, 255, 255, 0.12);
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 14, 10)
        layout.setSpacing(12)

        # ── Icon ──
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(48, 48)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 0.25);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.icon_label)

        # ── Text info ──
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)

        self.title_label = QLabel(item.display_name)
        self.title_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #FFFFFF;")
        self.title_label.setWordWrap(True)
        text_layout.addWidget(self.title_label)

        desc_text = item.description if item.description else ("Hidden achievement" if item.hidden else "")
        self.desc_label = QLabel(desc_text)
        self.desc_label.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.6);")
        self.desc_label.setWordWrap(True)
        text_layout.addWidget(self.desc_label)

        layout.addLayout(text_layout, 1)

        # ── Switch toggle ──
        self.toggle = SwitchToggle(self, active_color=accent_color)
        self.toggle.setChecked(item.unlocked)
        self.toggle.stateChanged.connect(self._on_toggled)
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignVCenter)

        # Connect icon signal
        self.icon_loaded.connect(self._set_icon_pixmap)
        self._load_icon()

    def _load_icon(self) -> None:
        """Load icon from local cache or request background download."""
        icon_hash = self.item.icon if self.item.unlocked else (self.item.icon_gray or self.item.icon)
        cached = self.manager.get_cached_icon_path(self.appid, icon_hash)
        if cached:
            self._set_icon_pixmap(str(cached))
        elif icon_hash:
            self._set_placeholder_icon()
            self.manager.download_icon_async(
                self.appid,
                icon_hash,
                on_complete=lambda path: self.icon_loaded.emit(str(path)) if path else None,
            )
        else:
            self._set_placeholder_icon()

    def _set_placeholder_icon(self) -> None:
        """Render a neat trophy placeholder."""
        self.icon_label.setText("🏆" if self.item.unlocked else "🔒")
        self.icon_label.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 0.25);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 4px;
                font-size: 18pt;
            }
        """)

    def _set_icon_pixmap(self, file_path: str) -> None:
        try:
            pix = QPixmap(file_path)
            if not pix.isNull():
                scaled = pix.scaled(
                    48, 48,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.icon_label.setText("")
                self.icon_label.setPixmap(scaled)
        except Exception:
            self._set_placeholder_icon()

    def _on_toggled(self, checked: bool) -> None:
        self.item.unlocked = checked
        self.state_toggled.emit(self.item.api_name, checked)
        self._load_icon()

    def set_state(self, checked: bool) -> None:
        """Programmatically update toggle without re-emitting duplicate user signals."""
        self.toggle.setChecked(checked)
        self.item.unlocked = checked
        self._load_icon()


class AchievementsLoaderThread(QThread):
    """Background loader thread to read achievements schema and user stats."""

    loaded = pyqtSignal(list, dict, int)  # (achievements, user_stats, account_id)
    error = pyqtSignal(str)

    def __init__(self, manager: SHSAHRebornManager, appid: str):
        super().__init__()
        self.manager = manager
        self.appid = appid

    def run(self):
        try:
            acc_id = self.manager.get_account_id(self.appid)
            if not acc_id:
                acc_id = 0

            achievements, user_stats = self.manager.get_achievements_with_status(
                account_id=acc_id,
                appid=self.appid,
            )
            self.loaded.emit(achievements, user_stats, acc_id)
        except Exception as e:
            logger.exception(f"Error loading achievements for appid {self.appid}: {e}")
            self.error.emit(str(e))


def init_achievements_tab(dialog) -> None:
    """Initialize the Achievements tab for GameDetailsDialogV2."""
    dialog.achievements_manager = SHSAHRebornManager()
    dialog.achievements_list: List[AchievementItem] = []
    dialog.achievements_user_stats: Dict = {}
    dialog.achievements_account_id: int = 0
    dialog.achievement_cards: Dict[str, AchievementCard] = {}
    dialog._achievements_fetch_requested = False

    achievements_page = QWidget()
    achievements_page.setStyleSheet("background: transparent;")
    page_layout = QVBoxLayout(achievements_page)
    page_layout.setContentsMargins(14, 10, 14, 10)
    page_layout.setSpacing(8)

    # ── Center Stack: Page 0 = Spinner, Page 1 = List, Page 2 = No Schema Found ──
    dialog.achievements_center_stack = QStackedWidget()

    # Page 0: Loading spinner
    loading_container = QWidget()
    loading_layout = QVBoxLayout(loading_container)
    loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    loading_layout.setSpacing(10)
    dialog.achievements_spinner = MaterialSpinner(loading_container, size=32, color=dialog.accent_color, thickness=3)
    loading_lbl = QLabel("Reading Steam achievement cache...")
    loading_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 8.5pt;")
    loading_layout.addWidget(dialog.achievements_spinner, 0, Qt.AlignmentFlag.AlignCenter)
    loading_layout.addWidget(loading_lbl, 0, Qt.AlignmentFlag.AlignCenter)
    dialog.achievements_center_stack.addWidget(loading_container)

    # Page 1: Scrollable achievements cards
    dialog.achievements_scroll = QScrollArea()
    dialog.achievements_scroll.setWidgetResizable(True)
    dialog.achievements_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    dialog.achievements_scroll.setStyleSheet("""
        QScrollArea { background: transparent; border: none; }
        QScrollBar:vertical { background: transparent; width: 6px; margin: 0px; }
        QScrollBar::handle:vertical { background: rgba(255, 255, 255, 0.14); border-radius: 3px; min-height: 24px; }
        QScrollBar::handle:vertical:hover { background: rgba(255, 255, 255, 0.25); }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
    """)

    dialog.achievements_scroll_inner = QWidget()
    dialog.achievements_scroll_inner.setStyleSheet("background: transparent;")
    dialog.achievements_cards_layout = QVBoxLayout(dialog.achievements_scroll_inner)
    dialog.achievements_cards_layout.setContentsMargins(0, 4, 4, 4)
    dialog.achievements_cards_layout.setSpacing(6)
    dialog.achievements_scroll.setWidget(dialog.achievements_scroll_inner)
    dialog.achievements_center_stack.addWidget(dialog.achievements_scroll)

    # Page 2: Empty / No Schema Found
    dialog.achievements_empty_container = QWidget()
    empty_layout = QVBoxLayout(dialog.achievements_empty_container)
    empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    empty_layout.setSpacing(10)

    empty_title = QLabel("No Achievement Schema Found")
    empty_title.setStyleSheet("font-size: 10.5pt; font-weight: bold; color: rgba(255, 255, 255, 0.85);")
    empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    empty_layout.addWidget(empty_title)

    empty_sub = QLabel(
        f"Steam has not cached an achievement schema for this game (AppID {dialog.appid}).\n"
        "Launch the game once with Steam/SLS or generate the schema via Settings -> Tools."
    )
    empty_sub.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.5);")
    empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
    empty_layout.addWidget(empty_sub)
    dialog.achievements_center_stack.addWidget(dialog.achievements_empty_container)

    page_layout.addWidget(dialog.achievements_center_stack, 1)

    # ── Status header line ──
    dialog.achievements_status_lbl = QLabel("")
    dialog.achievements_status_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; padding-left: 2px;")
    page_layout.addWidget(dialog.achievements_status_lbl)

    # ── Full-width bottom bar with 3 buttons ──
    dialog.achievements_bottom_bar = QWidget()
    bar_layout = QHBoxLayout(dialog.achievements_bottom_bar)
    bar_layout.setContentsMargins(0, 4, 0, 2)
    bar_layout.setSpacing(8)

    btn_unlock_all = QPushButton("Unlock All")
    btn_unlock_all.setFixedHeight(34)
    btn_unlock_all.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_unlock_all.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    btn_unlock_all.setStyleSheet("""
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            color: #FFFFFF;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 6px;
            font-weight: 600;
            font-size: 9pt;
        }
        QPushButton:hover {
            background-color: rgba(76, 175, 80, 0.25);
            border-color: rgba(76, 175, 80, 0.4);
            color: #81C784;
        }
    """)
    btn_unlock_all.clicked.connect(lambda: _set_all_achievements(dialog, True))
    bar_layout.addWidget(btn_unlock_all)

    btn_unlock_none = QPushButton("Unlock None")
    btn_unlock_none.setFixedHeight(34)
    btn_unlock_none.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_unlock_none.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    btn_unlock_none.setStyleSheet("""
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            color: #FFFFFF;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 6px;
            font-weight: 600;
            font-size: 9pt;
        }
        QPushButton:hover {
            background-color: rgba(244, 67, 54, 0.2);
            border-color: rgba(244, 67, 54, 0.4);
            color: #E57373;
        }
    """)
    btn_unlock_none.clicked.connect(lambda: _set_all_achievements(dialog, False))
    bar_layout.addWidget(btn_unlock_none)

    save_fg = get_best_foreground_color(dialog.accent_color)
    dialog.achievements_save_btn = QPushButton("Save")
    dialog.achievements_save_btn.setFixedHeight(34)
    dialog.achievements_save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.achievements_save_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    dialog.achievements_save_btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {dialog.accent_color};
            color: {save_fg};
            border: none;
            border-radius: 6px;
            font-weight: 700;
            font-size: 9pt;
        }}
        QPushButton:hover {{
            opacity: 0.92;
        }}
        QPushButton:disabled {{
            background-color: rgba(255, 255, 255, 0.05);
            color: rgba(255, 255, 255, 0.2);
        }}
    """)
    dialog.achievements_save_btn.clicked.connect(lambda: _save_achievements(dialog))
    bar_layout.addWidget(dialog.achievements_save_btn)

    page_layout.addWidget(dialog.achievements_bottom_bar)

    dialog.stacked.addWidget(achievements_page)


def ensure_achievements_loaded(dialog) -> None:
    """Lazy load achievements on first tab visit."""
    if getattr(dialog, "_achievements_fetch_requested", False):
        return
    dialog._achievements_fetch_requested = True

    # Show one-time experimental warning dialog
    show_experimental_warning(dialog, dialog.accent_color)

    # Start background loader
    dialog.achievements_center_stack.setCurrentIndex(0)
    dialog.achievements_bottom_bar.setEnabled(False)

    dialog._achievements_worker = AchievementsLoaderThread(dialog.achievements_manager, dialog.appid)
    dialog._achievements_worker.loaded.connect(lambda achs, stats, acc_id: _on_achievements_loaded(dialog, achs, stats, acc_id))
    dialog._achievements_worker.error.connect(lambda err: _on_achievements_error(dialog, err))
    dialog._achievements_worker.start()


def _on_achievements_loaded(dialog, achievements: List[AchievementItem], user_stats: Dict, account_id: int) -> None:
    dialog.achievements_list = achievements
    dialog.achievements_user_stats = user_stats
    dialog.achievements_account_id = account_id
    dialog.achievements_bottom_bar.setEnabled(True)

    if not achievements:
        dialog.achievements_center_stack.setCurrentIndex(2)
        dialog.achievements_status_lbl.setText("")
        return

    # Clear any previous cards
    while dialog.achievements_cards_layout.count() > 0:
        item = dialog.achievements_cards_layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.deleteLater()

    dialog.achievement_cards.clear()

    for ach in achievements:
        card = AchievementCard(
            item=ach,
            appid=dialog.appid,
            manager=dialog.achievements_manager,
            accent_color=dialog.accent_color,
            parent=dialog.achievements_scroll_inner,
        )
        card.state_toggled.connect(lambda _name, _st: _update_status_label(dialog))
        dialog.achievements_cards_layout.addWidget(card)
        dialog.achievement_cards[ach.api_name] = card

    dialog.achievements_cards_layout.addStretch()
    dialog.achievements_center_stack.setCurrentIndex(1)
    _update_status_label(dialog)


def _on_achievements_error(dialog, error_msg: str) -> None:
    dialog.achievements_center_stack.setCurrentIndex(2)
    dialog.achievements_status_lbl.setText(f"Failed to load: {error_msg}")


def _update_status_label(dialog) -> None:
    unlocked_count = sum(1 for a in dialog.achievements_list if a.unlocked)
    total_count = len(dialog.achievements_list)
    dialog.achievements_status_lbl.setText(
        f"Achievements: {unlocked_count} of {total_count} unlocked (Account ID: {dialog.achievements_account_id})"
    )


def _set_all_achievements(dialog, unlock: bool) -> None:
    for card in dialog.achievement_cards.values():
        card.set_state(unlock)
    _update_status_label(dialog)


def _save_achievements(dialog) -> None:
    """Commit toggled states and write binary VDF file."""
    if not dialog.achievements_list or not dialog.achievements_account_id:
        QMessageBox.warning(dialog, "Save Failed", "No account ID or achievements found.")
        return

    try:
        states = {card.item.api_name: card.item.unlocked for card in dialog.achievement_cards.values()}
        dialog.achievements_manager.apply_batch_states(
            stats_data=dialog.achievements_user_stats,
            achievements=dialog.achievements_list,
            states=states,
        )

        saved_path = dialog.achievements_manager.save_user_stats(
            account_id=dialog.achievements_account_id,
            appid=dialog.appid,
            stats_data=dialog.achievements_user_stats,
        )

        unlocked_count = sum(1 for a in dialog.achievements_list if a.unlocked)
        total_count = len(dialog.achievements_list)

        dialog.achievements_status_lbl.setText(
            f"✓ Saved ({unlocked_count}/{total_count} unlocked) to {saved_path.name}! Restart game/Steam to apply."
        )

        QMessageBox.information(
            dialog,
            "Achievements Saved",
            f"Successfully updated achievements!\n\n"
            f"• Unlocked: {unlocked_count} / {total_count}\n"
            f"• File: {saved_path.name}\n\n"
            f"Restart Steam or launch the game to reflect new achievement statuses.",
        )
    except Exception as e:
        logger.exception(f"Error saving user stats: {e}")
        QMessageBox.critical(dialog, "Save Error", f"Failed to save achievements:\n{e}")
