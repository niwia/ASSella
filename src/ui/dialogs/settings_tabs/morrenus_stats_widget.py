import logging
from datetime import datetime, timezone
from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
)

from core import morrenus_api
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class MorrenusStatsWidget(QWidget):
    """Widget displaying Hubcap API user statistics and cloud generation quotas."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.settings = get_settings()
        self.username_label = None
        self.expiration_label = None
        self.total_calls_label = None
        self.account_status_lbl = None
        self.steam_service_lbl = None
        self.refresh_button = None
        self._setup_ui()

    def _create_stat_bar(self, title: str, is_muted: bool = False):
        """Helper to create a titled progress bar row with value label."""
        container = QVBoxLayout()
        container.setSpacing(3)
        container.setContentsMargins(0, 2, 0, 2)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        title_lbl = QLabel(title)
        if is_muted:
            title_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 8.5pt;")
        else:
            title_lbl.setStyleSheet("color: #FFFFFF; font-size: 8.5pt; font-weight: 500;")

        val_lbl = QLabel("--")
        if is_muted:
            val_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.35); font-size: 8.5pt;")
        else:
            val_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;")

        header_layout.addWidget(title_lbl)
        header_layout.addStretch()
        header_layout.addWidget(val_lbl)
        container.addLayout(header_layout)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)

        accent_color = self.settings.value("accent_color", "#C06C84")
        from utils.color_utils import get_dark_container_color
        track_bg = get_dark_container_color(accent_color)

        if is_muted:
            bar.setStyleSheet("""
                QProgressBar {
                    background-color: rgba(255, 255, 255, 0.05);
                    border: none;
                    border-radius: 4px;
                }
                QProgressBar::chunk {
                    background-color: rgba(255, 255, 255, 0.2);
                    border-radius: 4px;
                    margin: 0px;
                }
            """)
        else:
            bar.setStyleSheet(
                f"""
                QProgressBar {{
                    background-color: {track_bg};
                    border: none;
                    border-radius: 4px;
                }}
                QProgressBar::chunk {{
                    background-color: {accent_color};
                    border-radius: 4px;
                    margin: 0px;
                }}
                """
            )

        container.addWidget(bar)
        return container, title_lbl, val_lbl, bar

    def _setup_ui(self) -> None:
        """Initialize the UI components."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 5, 0, 5)
        main_layout.setSpacing(8)

        # Row 1: User info row (Username, Expires, Total API Calls)
        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 2)
        info_row.setSpacing(12)

        self.username_label = QLabel("User: --")
        self.username_label.setStyleSheet("font-weight: bold; color: #FFFFFF; font-size: 9pt;")
        info_row.addWidget(self.username_label)
        info_row.addStretch()

        self.expiration_label = QLabel("Expires: --")
        self.expiration_label.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;")
        info_row.addWidget(self.expiration_label)

        self.total_calls_label = QLabel("Total: --")
        self.total_calls_label.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;")
        info_row.addWidget(self.total_calls_label)

        main_layout.addLayout(info_row)

        # Progress Bars Section
        # 1. Daily API Usage (Manifests)
        c1, self.daily_title_lbl, self.daily_val_lbl, self.daily_usage_bar = self._create_stat_bar("Daily API Usage (Manifests):")
        main_layout.addLayout(c1)

        # 2. Single Generation Quota
        c2, self.single_title_lbl, self.single_val_lbl, self.single_bar = self._create_stat_bar("Single Generation Quota:")
        main_layout.addLayout(c2)

        # 3. Workshop Generation Quota
        c3, self.workshop_title_lbl, self.workshop_val_lbl, self.workshop_bar = self._create_stat_bar("Workshop Generation Quota:")
        main_layout.addLayout(c3)

        # Bottom status row (Account Status on left, Steam Gen Service on right)
        status_bottom_row = QHBoxLayout()
        status_bottom_row.setContentsMargins(0, 4, 0, 0)

        self.account_status_lbl = QLabel("● Account: --")
        self.account_status_lbl.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.6);")
        status_bottom_row.addWidget(self.account_status_lbl)

        status_bottom_row.addStretch()

        self.steam_service_lbl = QLabel("● Steam Gen Service: --")
        self.steam_service_lbl.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.6);")
        status_bottom_row.addWidget(self.steam_service_lbl)

        main_layout.addLayout(status_bottom_row)

        # Refresh button
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.refresh_button.clicked.connect(self.refresh_stats)
        main_layout.addWidget(self.refresh_button)

    def refresh_stats(self) -> None:
        """Fetch and display latest stats from both user/stats and generate/usage."""
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Loading...")

        from utils.task_runner import TaskRunner
        self._stats_runner = TaskRunner(self)
        worker = self._stats_runner.run(morrenus_api.get_all_hubcap_stats)

        def on_stats_finished(result):
            self.refresh_button.setEnabled(True)
            self.refresh_button.setText("Refresh")
            if not result:
                self._display_error_state()
            else:
                self._display_all_stats(result.get("user_stats", {}), result.get("gen_usage", {}))

        def on_stats_error(err_tuple):
            self.refresh_button.setEnabled(True)
            self.refresh_button.setText("Refresh")
            self._display_error_state()

        worker.finished.connect(on_stats_finished)
        worker.error.connect(on_stats_error)

    def _display_error_state(self) -> None:
        """Update UI to show error state."""
        self.username_label.setText("User: Error")
        self.total_calls_label.setText("Total: --")
        self.expiration_label.setText("Expires: --")
        self.expiration_label.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;")
        self.account_status_lbl.setText("● Account: Error")
        self.account_status_lbl.setStyleSheet("font-size: 8pt; color: #e57373;")
        self.daily_val_lbl.setText("Error")
        self.daily_usage_bar.setValue(0)
        self.single_val_lbl.setText("Error")
        self.single_bar.setValue(0)
        self.workshop_val_lbl.setText("Error")
        self.workshop_bar.setValue(0)
        self.steam_service_lbl.setText("● Steam Gen Service: --")
        self.steam_service_lbl.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.5);")

    def _display_all_stats(self, user_stats: dict, gen_usage: dict) -> None:
        """Update UI with fetched statistics and quotas."""
        accent_color = self.settings.value("accent_color", "#C06C84")
        from utils.color_utils import get_semantic_colors
        semantic = get_semantic_colors(accent_color)

        # 1. User stats
        if user_stats and not user_stats.get("error"):
            self.username_label.setText(f"User: {user_stats.get('username', 'Unknown')}")
            self.total_calls_label.setText(f"Total: {user_stats.get('api_key_usage_count', 0)}")

            daily_usage = MorrenusStatsWidget._parse_int(user_stats.get("daily_usage", 0))
            daily_limit = MorrenusStatsWidget._parse_int(user_stats.get("daily_limit", 55))
            if daily_limit <= 0:
                daily_limit = 55

            self.daily_usage_bar.setRange(0, daily_limit)
            self.daily_usage_bar.setValue(daily_usage)
            self.daily_val_lbl.setText(f"{daily_usage} / {daily_limit}")

            self._update_expiration_label(user_stats.get("api_key_expires_at", ""))

            can_req = user_stats.get("can_make_requests", False)
            if can_req:
                self.account_status_lbl.setText("● Account: Active")
                self.account_status_lbl.setStyleSheet(f"font-size: 8pt; color: {semantic.get('success', '#81c784')}; font-weight: bold;")
            else:
                self.account_status_lbl.setText("● Account: Inactive")
                self.account_status_lbl.setStyleSheet(f"font-size: 8pt; color: {semantic.get('error', '#e57373')}; font-weight: bold;")
        else:
            self.username_label.setText("User: Error")
            self.daily_val_lbl.setText("Error")
            self.account_status_lbl.setText("● Account: Error")
            self.account_status_lbl.setStyleSheet(f"font-size: 8pt; color: {semantic.get('error', '#e57373')}; font-weight: bold;")

        # 2. Generation usage limits
        if gen_usage and not gen_usage.get("error"):
            single = gen_usage.get("single", {})
            s_usage = MorrenusStatsWidget._parse_int(single.get("usage", 0))
            s_limit = MorrenusStatsWidget._parse_int(single.get("limit", 1500))
            if s_limit <= 0:
                s_limit = 1500
            self.single_bar.setRange(0, s_limit)
            self.single_bar.setValue(s_usage)
            self.single_val_lbl.setText(f"{s_usage} / {s_limit}")

            workshop = gen_usage.get("workshop", {})
            w_usage = MorrenusStatsWidget._parse_int(workshop.get("usage", 0))
            w_limit = MorrenusStatsWidget._parse_int(workshop.get("limit", 500))
            if w_limit <= 0:
                w_limit = 500
            self.workshop_bar.setRange(0, w_limit)
            self.workshop_bar.setValue(w_usage)
            self.workshop_val_lbl.setText(f"{w_usage} / {w_limit}")

            ready = gen_usage.get("steam_service_ready", True)
            if ready:
                self.steam_service_lbl.setText("● Steam Gen Service: Ready")
                self.steam_service_lbl.setStyleSheet(f"font-size: 8pt; color: {semantic.get('success', '#81c784')};")
            else:
                self.steam_service_lbl.setText("● Steam Gen Service: Offline")
                self.steam_service_lbl.setStyleSheet(f"font-size: 8pt; color: {semantic.get('error', '#e57373')};")
        else:
            self.single_val_lbl.setText("--")
            self.workshop_val_lbl.setText("--")
            self.steam_service_lbl.setText("● Steam Gen Service: --")
            self.steam_service_lbl.setStyleSheet("font-size: 8pt; color: rgba(255, 255, 255, 0.5);")

    @staticmethod
    def _parse_int(value: Any, default: int = 0) -> int:
        """Safely parse an integer value."""
        try:
            return int(value or default)
        except (TypeError, ValueError):
            return default

    def _update_expiration_label(self, expires_at: str) -> None:
        """Format and update the expiration label with theme-adaptive warning colors."""
        if not expires_at:
            self.expiration_label.setText("Expires: Never")
            self.expiration_label.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 8.5pt;")
            return

        formatted_date = expires_at[:10]
        days_left = None
        try:
            exp_clean = expires_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(exp_clean)
            formatted_date = dt.strftime("%d/%m/%Y")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days_left = (dt - now).days
        except Exception as e:
            logger.debug(f"Failed to parse expiry date: {e}")

        accent_color = self.settings.value("accent_color", "#C06C84")
        from utils.color_utils import get_semantic_colors
        semantic = get_semantic_colors(accent_color)

        if days_left is not None:
            if days_left < 0:
                color = semantic.get("error", "#e57373")
                status_text = f"Expires: {formatted_date} (Expired)"
            elif days_left <= 7:
                # Urgent warning
                color = semantic.get("error", "#e57373")
                status_text = f"Expires: {formatted_date} ({days_left}d left)"
            elif days_left <= 30:
                # Moderate warning (~70% through validity or <30 days remaining)
                color = semantic.get("warning", "#ffd54f")
                status_text = f"Expires: {formatted_date} ({days_left}d left)"
            elif days_left <= 60:
                # Light advisory warning
                color = semantic.get("warning", "#ffd54f")
                status_text = f"Expires: {formatted_date}"
            else:
                # Plenty of time left -> standard theme-harmonized muted text
                color = "rgba(255, 255, 255, 0.75)"
                status_text = f"Expires: {formatted_date}"
        else:
            color = "rgba(255, 255, 255, 0.75)"
            status_text = f"Expires: {formatted_date}"

        self.expiration_label.setText(status_text)
        self.expiration_label.setStyleSheet(f"color: {color}; font-size: 8.5pt; font-weight: 500;")
