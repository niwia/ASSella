import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from components.custom_widgets import ScaledFontLabel, ScaledLabel
from utils.logger import open_log_directory
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class StatusDialog(QDialog):
    """Dialog showing the status of tools for the last installed game."""

    # Status colors
    STATUS_OK = "#00FF00"
    STATUS_IN_PROGRESS = "#FFA500"
    STATUS_ERROR = "#FF0000"

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.parent_window = parent
        self.setWindowTitle("Last Download Task Status")
        self.resize(450, 180)
        self.setMinimumSize(400, 150)

        # UI State placeholders
        self.ddm_status: str = ""
        self.ddm_status_text: str = ""
        self.slscheevo_status: str = ""
        self.slscheevo_status_text: str = ""
        self.steamless_status: str = ""
        self.steamless_status_text: str = ""
        self.last_game_name: str = ""

        # Main Layout
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(5)

        self._gather_status()
        self._setup_ui()

        logger.debug("StatusDialog initialized.")

    def _gather_status(self) -> None:
        """Gather status from task_manager."""
        settings = get_settings()
        accent_color = settings.value("accent_color", "#a1c9fd")

        # Set defaults first
        self.ddm_status = accent_color
        self.ddm_status_text = "Not run"
        self.slscheevo_status = accent_color
        self.slscheevo_status_text = "Not run"
        self.steamless_status = accent_color
        self.steamless_status_text = "Not run"
        self.last_game_name = "No game installed"

        if not self.parent_window or not hasattr(self.parent_window, "task_manager"):
            return

        task_manager = self.parent_window.task_manager
        status = task_manager.get_component_status()

        # Map status strings to colors
        status_map = {
            "ok": task_manager.STATUS_OK,
            "in_progress": task_manager.STATUS_IN_PROGRESS,
            "error": task_manager.STATUS_ERROR,
            "not_run": accent_color,
        }

        self.ddm_status = status_map.get(status["ddm_status"], task_manager.STATUS_OK)
        self.ddm_status_text = status["ddm_status_text"]

        self.slscheevo_status = status_map.get(
            status["slscheevo_status"], task_manager.STATUS_OK
        )
        self.slscheevo_status_text = status["slscheevo_status_text"]

        self.steamless_status = status_map.get(
            status["steamless_status"], task_manager.STATUS_OK
        )
        self.steamless_status_text = status["steamless_status_text"]

        self.last_game_name = task_manager.last_installed_game or "No game installed"

    def _setup_ui(self) -> None:
        """Orchestrate UI creation."""
        self._create_header()
        self.layout.addSpacing(5)
        self._create_status_group()
        self.layout.addStretch()
        self._create_footer_buttons()

    def _create_header(self) -> None:
        """Create the title and game name label."""
        title = ScaledFontLabel("Last Download Task Status")
        title.setStyleSheet("font-size: 14pt;")
        self.layout.addWidget(title)

        game_label = ScaledLabel(self.last_game_name)
        game_label.setStyleSheet("font-size: 10pt")
        self.layout.addWidget(game_label)

    def _create_status_group(self) -> None:
        """Create the group box containing status rows."""
        status_group = QGroupBox()
        status_group.setStyleSheet("QGroupBox { border: none; }")

        status_layout = QVBoxLayout()
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(3)

        # Rows
        status_layout.addLayout(
            self._create_status_row(
                " Download Manager", self.ddm_status, self.ddm_status_text
            )
        )
        status_layout.addLayout(
            self._create_status_row(
                " Achievements",
                self.slscheevo_status,
                self.slscheevo_status_text,
            )
        )
        status_layout.addLayout(
            self._create_status_row(
                " DRM Removal",
                self.steamless_status,
                self.steamless_status_text,
            )
        )

        status_group.setLayout(status_layout)
        self.layout.addWidget(status_group)

    def _create_footer_buttons(self) -> None:
        """Create the open logs button and OK button."""
        button_layout = QHBoxLayout()

        logs_button = QPushButton("Open Logs")
        logs_button.clicked.connect(open_log_directory)
        button_layout.addWidget(logs_button)

        button_layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        button_layout.addWidget(buttons)

        self.layout.addLayout(button_layout)

    @staticmethod
    def _create_status_row(name: str, color: str, status_text: str) -> QHBoxLayout:
        """Create a single status row layout."""
        row_layout = QHBoxLayout()

        indicator = QLabel()
        indicator.setFixedSize(12, 12)
        indicator.setStyleSheet(f"border-radius: 6px; background-color: {color};")

        name_label = ScaledLabel(name)
        name_label.setMinimumWidth(150)

        status_label = ScaledLabel(status_text)

        row_layout.addWidget(indicator)
        row_layout.addWidget(name_label)
        row_layout.addWidget(status_label, alignment=Qt.AlignmentFlag.AlignRight)

        return row_layout
