import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from components.custom_widgets import ScaledFontLabel, ScaledLabel
from ui.dialogs.themed_dialog import install_themed_frame
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class ChmodResumeDialog(QDialog):
    """Dialog showing a brief summary of chmod operations."""

    def __init__(self, game_name, file_count, success, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Make Executable Complete")
        self.setMinimumWidth(400)
        self.setMinimumHeight(300)
        self.setModal(True)

        self._setup_ui(game_name, file_count, success)

        logger.debug(
            f"ChmodResumeDialog initialized: {game_name}, {file_count} files processed"
        )

    def _setup_ui(self, game_name, file_count, success):
        """Setup the dialog UI"""
        layout = install_themed_frame(self, "Make Executable Complete")
        layout.setSpacing(15)

        # Get colors from settings
        settings = get_settings()
        accent_color = settings.value("accent_color", "#C06C84")

        # Title
        title = ScaledFontLabel("Make Executable Complete")
        title.setStyleSheet(f"font-size: 16pt; color: {accent_color};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Game name
        game_label = ScaledLabel(f"Game: {game_name}")
        game_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(game_label)

        # Separator
        separator = QLabel()
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background-color: {accent_color};")
        layout.addWidget(separator)

        # Stats layout
        stats_layout = QVBoxLayout()
        stats_layout.setSpacing(10)

        # Files processed
        processed_label = ScaledLabel(f"Files processed: {file_count}")
        processed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stats_layout.addWidget(processed_label)

        layout.addLayout(stats_layout)

        # Status message
        if success and file_count > 0:
            status_text = "Completed Successfully"
            status_color = "#00FF00"
        elif file_count > 0:
            status_text = "All Permissions Set"
            status_color = "#00FF00"
        elif file_count == 0:
            status_text = "No Executable Files Found"
            status_color = "#888888"
        else:
            status_text = "Operation Failed"
            status_color = "#FF6B6B"

        status_label = ScaledFontLabel(status_text)
        status_label.setStyleSheet(f"color: {status_color}; font-size: 12pt;")
        status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(status_label)

        layout.addSpacing(10)

        # OK button
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        button_box.accepted.connect(self.accept)
        button_box.setCenterButtons(True)
        layout.addWidget(button_box)
