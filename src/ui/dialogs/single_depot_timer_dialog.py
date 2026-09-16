from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
)

from utils.settings import get_settings
from utils.color_utils import get_best_foreground_color


ACTION_NO = 0
ACTION_YES = 1
ACTION_MANUAL = 2


class SingleDepotTimerDialog(QDialog):
    """A Material 3 styled confirmation dialog with a 3-second auto-proceed countdown timer and manual options."""
    ACTION_NO = ACTION_NO
    ACTION_YES = ACTION_YES
    ACTION_MANUAL = ACTION_MANUAL

    def __init__(self, parent=None, title="Single Depot Option", message="Game has only one depot.\n\nProceed to download and add it to queue?", seconds=3):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedSize(410, 165)
        self.seconds = seconds
        
        settings = get_settings()
        ac = settings.value("accent_color", "#7ab3ff", type=str)
        bg = settings.value("background_color", "#141416", type=str)
        text_color = get_best_foreground_color(ac)

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
            }}
            QLabel {{ color: #FFFFFF; font-size: 9.5pt; }}
        """)
        
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(14)
        
        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        lay.addWidget(msg_lbl)
        
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        btn_row.addStretch()
        
        self.yes_btn = QPushButton(f"✓ Yes ({self.seconds})")
        self.yes_btn.setFixedHeight(32)
        self.yes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.yes_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ac};
                color: {text_color};
                border: none;
                border-radius: 8px;
                font-weight: bold;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
        """)
        self.yes_btn.clicked.connect(self._on_yes_clicked)
        
        self.manual_btn = QPushButton("⚙ Manual")
        self.manual_btn.setFixedHeight(32)
        self.manual_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.manual_btn.setToolTip("Open minimal depot selection to choose download drive or custom files")
        self.manual_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 8px;
                font-weight: bold;
                padding: 0 14px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.16);
            }
        """)
        self.manual_btn.clicked.connect(self._on_manual_clicked)

        self.no_btn = QPushButton("✕ No")
        self.no_btn.setFixedHeight(32)
        self.no_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.no_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.05);
                color: rgba(255, 255, 255, 0.75);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                font-weight: bold;
                padding: 0 14px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.12);
                color: #FFFFFF;
            }
        """)
        self.no_btn.clicked.connect(self._on_no_clicked)
        
        btn_row.addWidget(self.yes_btn)
        btn_row.addWidget(self.manual_btn)
        btn_row.addWidget(self.no_btn)
        lay.addLayout(btn_row)
        
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start()

    def _on_yes_clicked(self):
        self.timer.stop()
        self.done(self.ACTION_YES)

    def _on_manual_clicked(self):
        self.timer.stop()
        self.done(self.ACTION_MANUAL)

    def _on_no_clicked(self):
        self.timer.stop()
        self.done(self.ACTION_NO)

    def _on_tick(self):
        self.seconds -= 1
        if self.seconds <= 0:
            self.timer.stop()
            self.done(self.ACTION_YES)
        else:
            self.yes_btn.setText(f"✓ Yes ({self.seconds})")
