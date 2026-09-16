from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
)


class WindowsDepotWarningDialog(QDialog):
    """Warning dialog with a 10-second countdown timer before confirming disabling Windows depots."""
    def __init__(self, parent=None, accent_color="#C06C84"):
        super().__init__(parent)
        self.setWindowTitle("Warning: Disabling Windows Depots")
        self.setModal(True)
        self.setFixedWidth(440)
        self.accent_color = accent_color
        self.seconds_left = 10

        self.setStyleSheet("""
            QDialog {
                background-color: #1a1a24;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 10px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Title
        title_lbl = QLabel("⚠️ Disable Windows Depots?")
        title_lbl.setStyleSheet("font-size: 12pt; font-weight: bold; color: #FFB84D; background: transparent;")
        layout.addWidget(title_lbl)

        # Warning body
        msg_lbl = QLabel(
            "Windows depots are required by the vast majority of games on Steam "
            "(including games running via Proton on Linux).\n\n"
            "Disabling Windows depots may cause games to fail to download or show no available files.\n\n"
            "Are you sure you want to disable Windows depots?"
        )
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet("font-size: 9.5pt; color: rgba(255, 255, 255, 0.85); line-height: 1.4; background: transparent;")
        layout.addWidget(msg_lbl)

        layout.addSpacing(6)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedHeight(34)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.10);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.20);
                border-radius: 6px;
                font-weight: 500;
                padding: 0 16px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.16);
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        btn_layout.addStretch()

        self.confirm_btn = QPushButton(f"Confirm ({self.seconds_left}s)")
        self.confirm_btn.setFixedHeight(34)
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_btn.setStyleSheet("""
            QPushButton {
                background: #d32f2f;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                padding: 0 18px;
            }
            QPushButton:hover {
                background: #f44336;
            }
            QPushButton:disabled {
                background: rgba(211, 47, 47, 0.35);
                color: rgba(255, 255, 255, 0.45);
            }
        """)
        self.confirm_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.confirm_btn)

        layout.addLayout(btn_layout)

        # 10s Timer
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start()

    def _on_tick(self):
        self.seconds_left -= 1
        if self.seconds_left > 0:
            self.confirm_btn.setText(f"Confirm ({self.seconds_left}s)")
        else:
            self.timer.stop()
            self.confirm_btn.setEnabled(True)
            self.confirm_btn.setText("Confirm")

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)
