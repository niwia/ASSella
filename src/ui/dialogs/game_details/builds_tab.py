"""
Builds tab implementation for GameDetailsDialogV2.
Handles SteamDB patch history, build cards, depot inspection, and version rollback.
"""

import threading
import logging
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QScrollArea,
    QStackedWidget,
    QInputDialog,
    QMessageBox,
    QSizePolicy,
)

from ui.material_progress import MaterialSpinner

logger = logging.getLogger(__name__)


def init_builds_tab(dialog) -> None:
    """Initialize the Builds tab with version history, cards list, and rollback buttons."""
    builds_page = QWidget()
    builds_page.setStyleSheet("background: transparent;")
    page_layout = QVBoxLayout(builds_page)
    page_layout.setContentsMargins(14, 10, 14, 10)
    page_layout.setSpacing(8)

    # ── Central Stack: Page 0 = Initial Spinner, Page 1 = Card list, Page 2 = Manual fallback ──
    dialog.builds_center_stack = QStackedWidget()

    # Page 0: First-time loading spinner
    loading_container = QWidget()
    loading_layout = QVBoxLayout(loading_container)
    loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    loading_layout.setSpacing(10)
    dialog.builds_main_spinner = MaterialSpinner(loading_container, size=32, color=dialog.accent_color, thickness=3)
    loading_lbl = QLabel("Fetching version history...")
    loading_lbl.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 8.5pt;")
    loading_layout.addWidget(dialog.builds_main_spinner, 0, Qt.AlignmentFlag.AlignCenter)
    loading_layout.addWidget(loading_lbl, 0, Qt.AlignmentFlag.AlignCenter)
    dialog.builds_center_stack.addWidget(loading_container)

    # Page 1: Scrollable card list
    dialog.builds_scroll = QScrollArea()
    dialog.builds_scroll.setWidgetResizable(True)
    dialog.builds_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    dialog.builds_scroll.setStyleSheet("""
        QScrollArea { background: transparent; border: none; }
        QScrollBar:vertical { background: transparent; width: 6px; margin: 0px; }
        QScrollBar::handle:vertical { background: rgba(255, 255, 255, 0.14); border-radius: 3px; min-height: 24px; }
        QScrollBar::handle:vertical:hover { background: rgba(255, 255, 255, 0.25); }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
    """)
    dialog.builds_scroll_inner = QWidget()
    dialog.builds_scroll_inner.setStyleSheet("background: transparent;")
    dialog.builds_cards_layout = QVBoxLayout(dialog.builds_scroll_inner)
    dialog.builds_cards_layout.setContentsMargins(0, 4, 4, 4)
    dialog.builds_cards_layout.setSpacing(8)
    dialog.builds_cards_layout.addStretch()
    dialog.builds_scroll.setWidget(dialog.builds_scroll_inner)
    dialog.builds_center_stack.addWidget(dialog.builds_scroll)

    # Page 2: Manual fallback when builds are unavailable
    dialog.builds_error_container = QWidget()
    manual_layout = QVBoxLayout(dialog.builds_error_container)
    manual_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    manual_layout.setSpacing(10)

    manual_title = QLabel("SteamDB Build History Unavailable")
    manual_title.setStyleSheet("font-size: 10.5pt; font-weight: bold; color: rgba(255, 255, 255, 0.85);")
    manual_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    manual_layout.addWidget(manual_title)

    manual_sub = QLabel("Automated patch history requires the Byparr solver.\nYou can still roll back to a specific version or download a manifest manually:")
    manual_sub.setStyleSheet("font-size: 8.5pt; color: rgba(255, 255, 255, 0.5);")
    manual_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
    manual_layout.addWidget(manual_sub)

    manual_center_btn = QPushButton("Manual Rollback / Manifest")
    manual_center_btn.setFixedSize(210, 36)
    manual_center_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    from utils.color_utils import get_best_foreground_color
    dl_fg = get_best_foreground_color(dialog.accent_color)
    download_style = f"""
        QPushButton {{
            background-color: {dialog.accent_color};
            color: {dl_fg};
            border: none;
            border-radius: 6px;
            font-weight: 600;
            font-size: 9pt;
        }}
        QPushButton:hover:!disabled {{
            background-color: #FFFFFF;
            color: #000000;
        }}
        QPushButton:disabled {{
            background-color: rgba(255, 255, 255, 0.05);
            color: rgba(255, 255, 255, 0.25);
            border: 1px solid rgba(255, 255, 255, 0.08);
        }}
    """
    manual_center_btn.setStyleSheet(download_style)
    manual_center_btn.clicked.connect(lambda: dialog._on_manual_rollback_clicked())
    manual_layout.addWidget(manual_center_btn, 0, Qt.AlignmentFlag.AlignCenter)
    dialog.builds_center_stack.addWidget(dialog.builds_error_container)

    page_layout.addWidget(dialog.builds_center_stack, 1)

    # ── Bottom 3-button bar (wrapped in builds_bottom_bar) ──
    dialog.builds_bottom_bar = QWidget()
    bottom_row = QHBoxLayout(dialog.builds_bottom_bar)
    bottom_row.setContentsMargins(0, 4, 0, 0)
    bottom_row.setSpacing(8)

    dialog.builds_refresh_btn = QPushButton("⟳  Refresh")
    dialog.builds_manual_btn = QPushButton("Manual")
    dialog.builds_download_btn = QPushButton("Download Manifest")
    dialog.builds_download_btn.setEnabled(False)

    ghost_style = """
        QPushButton {
            background-color: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            color: #FFFFFF;
            font-weight: 600;
            font-size: 9pt;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.1);
            color: #FFFFFF;
        }
        QPushButton:disabled {
            background-color: rgba(255, 255, 255, 0.02);
            color: rgba(255, 255, 255, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.05);
        }
    """

    for btn in (dialog.builds_refresh_btn, dialog.builds_manual_btn):
        btn.setFixedHeight(36)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(ghost_style)

    dialog.builds_download_btn.setFixedHeight(36)
    dialog.builds_download_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    dialog.builds_download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.builds_download_btn.setStyleSheet(download_style)

    dialog.builds_refresh_btn.clicked.connect(lambda: fetch_steamdb_builds_async(dialog))
    dialog.builds_manual_btn.clicked.connect(lambda: dialog._on_manual_rollback_clicked())
    dialog.builds_download_btn.clicked.connect(lambda: on_builds_download_clicked(dialog))

    bottom_row.addWidget(dialog.builds_refresh_btn)
    bottom_row.addWidget(dialog.builds_manual_btn)
    bottom_row.addWidget(dialog.builds_download_btn)
    page_layout.addWidget(dialog.builds_bottom_bar)

    try:
        from core.steamdb_scraper import ByparrManager
        has_byparr = ByparrManager.find_byparr_dir() is not None
    except Exception:
        has_byparr = False
    dialog.builds_bottom_bar.setVisible(has_byparr)

    dialog.stacked.addWidget(builds_page)

    dialog._selected_build_idx = -1
    dialog._build_cards = []
    dialog._cached_build_depots = {}


def strip_build_title(title: str, game_name: str) -> str:
    """Strip the game name prefix from a SteamDB patch title."""
    if not game_name or not title:
        return title
    for sep in (" - ", ": ", " – ", " — ", " / "):
        if title.lower().startswith(game_name.lower() + sep):
            return title[len(game_name) + len(sep):]
    return title


def make_build_card(dialog, idx: int, item: dict, current_bid: str, game_name: str) -> QFrame:
    build_id = str(item.get("buildid", ""))
    title = item.get("title", "Update")
    date_str = item.get("date", "")
    time_str = item.get("time", "")
    is_current = bool(build_id) and build_id == current_bid

    short_title = strip_build_title(title, game_name)

    card = QFrame()
    card.setObjectName("build_card")
    card.setCursor(Qt.CursorShape.PointingHandCursor)

    from utils.color_utils import get_dark_container_color
    tinted_bg = get_dark_container_color(dialog.accent_color)

    def _normal_style():
        if is_current:
            return f"""
                QFrame#build_card {{
                    background-color: rgba(255, 255, 255, 0.035);
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 8px;
                }}
                QFrame#build_card:hover {{
                    background-color: rgba(255, 255, 255, 0.065);
                    border: 1px solid rgba(255, 255, 255, 0.22);
                }}
                QFrame#build_card QLabel {{
                    border: none;
                    background: transparent;
                }}
            """
        return f"""
            QFrame#build_card {{
                background-color: rgba(255, 255, 255, 0.025);
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 8px;
            }}
            QFrame#build_card:hover {{
                background-color: rgba(255, 255, 255, 0.055);
                border: 1px solid rgba(255, 255, 255, 0.18);
            }}
            QFrame#build_card QLabel {{
                border: none;
                background: transparent;
            }}
        """

    def _selected_style():
        return f"""
            QFrame#build_card {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1.5px solid {dialog.accent_color};
                border-radius: 8px;
            }}
            QFrame#build_card QLabel {{
                border: none;
                background: transparent;
            }}
        """

    card._normal_style = _normal_style
    card._selected_style = _selected_style
    card.setStyleSheet(_normal_style())

    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 11, 16, 11)
    layout.setSpacing(6)

    # ─ Row 1: Short title + Installed Badge ─
    row1 = QHBoxLayout()
    row1.setContentsMargins(0, 0, 0, 0)
    row1.setSpacing(8)

    title_lbl = QLabel(short_title)
    title_lbl.setStyleSheet("color: #FFFFFF; font-size: 10pt; font-weight: bold; border: none; background: transparent;")
    title_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    row1.addWidget(title_lbl, 1)

    if is_current:
        badge = QLabel("Installed")
        badge.setStyleSheet(f"""
            color: #FFFFFF;
            background-color: {tinted_bg};
            border: 1px solid {dialog.accent_color};
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 8pt;
            font-weight: 600;
        """)
        badge.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        row1.addWidget(badge, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    layout.addLayout(row1)

    # ─ Row 2: Build ID, date, time ─
    row2 = QHBoxLayout()
    row2.setContentsMargins(0, 0, 0, 0)
    row2.setSpacing(16)
    row2.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    bid_html = (
        f'<span style="color: rgba(255, 255, 255, 0.5); font-size: 9pt;">Build ID</span>'
        f'&nbsp;&nbsp;'
        f'<span style="color: {dialog.accent_color}; font-size: 9pt; font-weight: bold;">{build_id}</span>'
    )
    bid_lbl = QLabel(bid_html)
    bid_lbl.setStyleSheet("border: none; background: transparent;")
    bid_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    row2.addWidget(bid_lbl)

    if date_str:
        date_lbl = QLabel(date_str)
        date_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 8.5pt; border: none; background: transparent;")
        date_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        row2.addWidget(date_lbl)

    if time_str:
        time_lbl = QLabel(time_str)
        time_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 8.5pt; border: none; background: transparent;")
        time_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        row2.addWidget(time_lbl)

    row2.addStretch(1)
    layout.addLayout(row2)

    card.mousePressEvent = lambda _e, i=idx: on_build_card_clicked(dialog, i)
    return card


def fetch_steamdb_builds_async(dialog) -> None:
    try:
        from core.steamdb_scraper import ByparrManager
        if not ByparrManager.find_byparr_dir():
            on_builds_error(dialog, "Solver not installed")
            return
    except Exception:
        on_builds_error(dialog, "Solver unavailable")
        return

    dialog.builds_refresh_btn.setText("⟳  Checking...")
    dialog.builds_refresh_btn.setEnabled(False)

    if dialog.builds_cards_layout.count() <= 1:
        dialog.builds_center_stack.setCurrentIndex(0)

    def _worker():
        try:
            aid = int(dialog.appid) if dialog.appid.isdigit() else 0
            data = dialog.steamdb_scraper.get_patchnotes(aid, limit=20)
            dialog.builds_loaded.emit(data)
        except Exception as e:
            logger.error(f"Failed to fetch builds for {dialog.appid}: {e}")
            dialog.builds_error.emit(str(e))

    threading.Thread(target=_worker, daemon=True).start()


def on_builds_loaded(dialog, builds: list) -> None:
    from core.steamdb_scraper import ByparrManager
    has_byparr = ByparrManager.find_byparr_dir() is not None
    dialog.builds_refresh_btn.setText("⟳  Refresh")
    dialog.builds_refresh_btn.setEnabled(True)
    dialog.builds_refresh_btn.setToolTip("")
    if builds:
        aid = int(dialog.appid) if dialog.appid.isdigit() else 0
        dialog.builds_cache.save_builds(aid, builds)
        populate_builds_cards(dialog, builds)
        dialog.builds_center_stack.setCurrentIndex(1)
        if hasattr(dialog, "builds_bottom_bar") and dialog.builds_bottom_bar:
            dialog.builds_bottom_bar.setVisible(True)
    elif dialog.builds_cards_layout.count() > 1:
        dialog.builds_center_stack.setCurrentIndex(1)
        if hasattr(dialog, "builds_bottom_bar") and dialog.builds_bottom_bar:
            dialog.builds_bottom_bar.setVisible(True)
    else:
        dialog.builds_center_stack.setCurrentIndex(2)
        if hasattr(dialog, "builds_bottom_bar") and dialog.builds_bottom_bar:
            dialog.builds_bottom_bar.setVisible(has_byparr)


def on_builds_error(dialog, err_msg: str) -> None:
    from core.steamdb_scraper import ByparrManager
    has_byparr = ByparrManager.find_byparr_dir() is not None
    dialog.builds_refresh_btn.setText("⟳  Refresh")
    dialog.builds_refresh_btn.setEnabled(True)
    if dialog.builds_cards_layout.count() > 1:
        dialog.builds_center_stack.setCurrentIndex(1)
        dialog.builds_refresh_btn.setToolTip(f"Build history currently unavailable. Showing cached builds.\n(Error: {err_msg})")
        if hasattr(dialog, "builds_bottom_bar") and dialog.builds_bottom_bar:
            dialog.builds_bottom_bar.setVisible(True)
    else:
        dialog.builds_center_stack.setCurrentIndex(2)
        dialog.builds_refresh_btn.setToolTip(f"Build history unavailable: {err_msg}")
        if hasattr(dialog, "builds_bottom_bar") and dialog.builds_bottom_bar:
            dialog.builds_bottom_bar.setVisible(has_byparr)


def get_build_action_label(dialog, build_id: str) -> str:
    current_bid = dialog._get_installed_buildid()
    if current_bid.isdigit() and str(build_id).isdigit():
        c_int = int(current_bid)
        s_int = int(build_id)
        if s_int < c_int:
            return "Downgrade"
        elif s_int == c_int:
            return "Verify"
        else:
            return "Update"
    return "Download Manifest"


def populate_builds_cards(dialog, data: list) -> None:
    while dialog.builds_cards_layout.count() > 1:
        item = dialog.builds_cards_layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

    dialog._build_cards = []
    dialog._selected_build_idx = -1
    dialog.builds_download_btn.setEnabled(False)
    dialog.builds_download_btn.setText("Download Manifest")

    current_bid = dialog._get_installed_buildid()
    game_name = dialog.game_data.get("game_name", "")

    for idx, item in enumerate(data):
        card = make_build_card(dialog, idx, item, current_bid, game_name)
        dialog.builds_cards_layout.insertWidget(idx, card)
        dialog._build_cards.append((card, item))


def on_build_card_clicked(dialog, idx: int) -> None:
    if 0 <= dialog._selected_build_idx < len(dialog._build_cards):
        prev_card, _ = dialog._build_cards[dialog._selected_build_idx]
        prev_card.setStyleSheet(prev_card._normal_style())

    dialog._selected_build_idx = idx
    card, item = dialog._build_cards[idx]
    card.setStyleSheet(card._selected_style())

    build_id = str(item.get("buildid", ""))
    dialog.builds_download_btn.setEnabled(False)
    action = get_build_action_label(dialog, build_id)
    dialog.builds_download_btn.setText(f"{action}...")

    if not build_id:
        return

    if build_id in dialog._cached_build_depots:
        apply_depot_to_download_btn(dialog, build_id, dialog._cached_build_depots[build_id])
        return
    if item.get("depots"):
        apply_depot_to_download_btn(dialog, build_id, item["depots"])
        return

    dialog.builds_download_btn.setText("Fetching Manifest...")

    def _depot_worker():
        try:
            depots = dialog.steamdb_scraper.get_patch_depots(build_id)
            dialog.build_depots_loaded.emit(build_id, depots)
        except Exception as e:
            logger.error(f"Failed to fetch depots for build {build_id}: {e}")
            dialog.build_depots_error.emit(f"Failed to resolve manifests for Build {build_id}.")

    threading.Thread(target=_depot_worker, daemon=True).start()


def on_build_depots_loaded(dialog, build_id: str, depots: dict) -> None:
    dialog._cached_build_depots[build_id] = depots
    aid = int(dialog.appid) if dialog.appid.isdigit() else 0
    dialog.builds_cache.update_build_depots(aid, build_id, depots)

    if 0 <= dialog._selected_build_idx < len(dialog._build_cards):
        _, item = dialog._build_cards[dialog._selected_build_idx]
        if str(item.get("buildid")) == str(build_id):
            apply_depot_to_download_btn(dialog, build_id, depots)


def on_build_depots_error(dialog, err_msg: str) -> None:
    if 0 <= dialog._selected_build_idx < len(dialog._build_cards):
        dialog.builds_download_btn.setText("Manifest Error")
        dialog.builds_download_btn.setEnabled(False)
        dialog.builds_download_btn.setToolTip(err_msg)


def apply_depot_to_download_btn(dialog, build_id: str, depots: dict) -> None:
    has_manifest = any(info.get("manifest_id") for info in depots.values()) if depots else False
    action = get_build_action_label(dialog, build_id)
    if has_manifest:
        dialog.builds_download_btn.setText(f"{action} (Build {build_id})")
        dialog.builds_download_btn.setEnabled(True)
    else:
        dialog.builds_download_btn.setText("No Manifests Found")
        dialog.builds_download_btn.setEnabled(False)


def on_builds_download_clicked(dialog) -> None:
    if not (0 <= dialog._selected_build_idx < len(dialog._build_cards)):
        return
    _, item = dialog._build_cards[dialog._selected_build_idx]
    build_id = str(item.get("buildid", ""))

    depots = dialog._cached_build_depots.get(build_id) or item.get("depots", {})
    if not depots:
        QMessageBox.warning(dialog, "No Manifest", "No depot manifests found for this build.")
        return

    installed_depots = dialog.game_data.get("installed_depots", {})
    selected_depot_id = None

    for d_id in depots.keys():
        if d_id in installed_depots:
            selected_depot_id = d_id
            break

    if not selected_depot_id and len(depots) == 1:
        selected_depot_id = list(depots.keys())[0]

    if not selected_depot_id and len(depots) > 1:
        items = [f"Depot {d_id}  (Manifest: {info.get('manifest_id')})"
                 for d_id, info in depots.items() if info.get("manifest_id")]
        if items:
            chosen, ok = QInputDialog.getItem(
                dialog, "Select Depot",
                "Multiple depots found. Select depot to download:", items, 0, False)
            if not ok or not chosen:
                return
            selected_depot_id = chosen.split(" ")[1]

    if not selected_depot_id:
        selected_depot_id = list(depots.keys())[0]

    manifest_id = depots[selected_depot_id].get("manifest_id")
    if not manifest_id:
        QMessageBox.warning(dialog, "No Manifest ID", f"Could not find manifest ID for depot {selected_depot_id}.")
        return

    action = get_build_action_label(dialog, build_id)
    if action == "Downgrade":
        should_pin = True
    elif action == "Verify":
        should_pin = dialog.settings.value(f"pin_build/{dialog.appid}", False, type=bool) if dialog.settings else False
    else:
        should_pin = False

    dialog._trigger_rollback_job(str(selected_depot_id), str(build_id), str(manifest_id), pin_build=should_pin)
