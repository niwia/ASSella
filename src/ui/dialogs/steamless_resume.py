import logging
from typing import Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from components.custom_widgets import ScaledFontLabel, ScaledLabel
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class SteamlessResumeDialog(QDialog):
    """Dialog showing a brief summary of Steamless processing results."""

    def __init__(
        self,
        game_name: str,
        exe_count: int,
        processed_count: int,
        success: bool,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Steamless Complete")
        self.setMinimumWidth(400)
        self.setMinimumHeight(300)
        self.setModal(True)

        # Main layout container
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(15)

        self._setup_ui(game_name, exe_count, processed_count, success)

        logger.debug(
            f"SteamlessResumeDialog initialized: {game_name}, "
            f"{exe_count} found, {processed_count} processed"
        )

    def _setup_ui(
        self,
        game_name: str,
        exe_count: int,
        processed_count: int,
        success: bool,
    ) -> None:
        """Orchestrate the creation of UI components."""
        self._create_header(game_name)
        self._create_separator()
        self._create_stats_section(exe_count, processed_count)
        self._create_status_message(exe_count, processed_count, success)
        self.layout.addSpacing(10)
        self._create_buttons()

    def _create_header(self, game_name: str) -> None:
        """Create the title and game name labels."""
        settings = get_settings()
        accent_color = settings.value("accent_color", "#a1c9fd")

        title = ScaledFontLabel("Steamless Processing Complete")
        title.setStyleSheet(f"font-size: 16pt; color: {accent_color};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(title)

        game_label = ScaledLabel(f"Game: {game_name}")
        game_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(game_label)

    def _create_separator(self) -> None:
        """Create a visual separator line."""
        settings = get_settings()
        accent_color = settings.value("accent_color", "#a1c9fd")

        separator = QLabel()
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background-color: {accent_color};")
        self.layout.addWidget(separator)

    def _create_stats_section(self, exe_count: int, processed_count: int) -> None:
        """Create the statistics display area."""
        stats_layout = QVBoxLayout()
        stats_layout.setSpacing(10)

        found_label = ScaledLabel(f"Found {exe_count} executable(s)")
        found_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stats_layout.addWidget(found_label)

        processed_label = ScaledLabel(f"Processed: {processed_count} executable(s)")
        processed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stats_layout.addWidget(processed_label)

        self.layout.addLayout(stats_layout)

    @staticmethod
    def _get_status_style(
        exe_count: int, processed_count: int, success: bool
    ) -> Tuple[str, str]:
        """Determine status text and color based on results."""
        if success and processed_count > 0:
            return "Completed Successfully", "#00FF00"

        if processed_count > 0:
            return "All DRM Removed", "#00FF00"

        if exe_count > 0 and processed_count == 0:
            return "No DRM Found", "#888888"

        return "No Executables Processed", "#FF6B6B"

    def _create_status_message(
        self, exe_count: int, processed_count: int, success: bool
    ) -> None:
        """Create the final status label."""
        text, color = self._get_status_style(exe_count, processed_count, success)

        status_label = ScaledFontLabel(text)
        status_label.setStyleSheet(f"color: {color}; font-size: 12pt;")
        status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(status_label)

    def _create_buttons(self) -> None:
        """Create the dialog buttons."""
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        button_box.accepted.connect(self.accept)
        button_box.setCenterButtons(True)
        self.layout.addWidget(button_box)
