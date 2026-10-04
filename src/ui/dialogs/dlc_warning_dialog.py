"""
Modal warning dialog displayed when the user activates DLC-only mode.
Enforces a 3-second lockout countdown before enabling the 'Proceed' button to ensure
the user acknowledges the ownership and installation path instructions, alongside a 'Cancel' button.
"""

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QApplication,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from utils.settings import get_settings


class DlcModeWarningDialog(QDialog):
    """
    Dialog informing the user about DLC-only mode requirements with Proceed and Cancel buttons.
    The Proceed button is locked for 3 seconds before becoming clickable.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DLC Mode")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setMaximumWidth(520)

        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.CustomizeWindowHint
        )

        self._countdown = 3
        settings = get_settings()
        self.accent_color = getattr(parent, "accent_color", None) or (settings.value("accent_color", "#a1c9fd") if settings else "#a1c9fd")

        self._init_ui()
        self._start_timer()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(20)

        # Content message
        msg_lbl = QLabel(
            "DLC mode allows you to download and install dlc files for the games <b><u>YOU OWN</u></b>.<br><br>"
            "Make sure that you select all the dlc depots you want without the base game files "
            "and download it to the folder you have installed the base game!"
        )
        msg_lbl.setWordWrap(True)
        msg_lbl.setTextFormat(Qt.TextFormat.RichText)
        msg_lbl.setStyleSheet("""
            QLabel {
                font-size: 10pt;
                line-height: 1.5;
                color: #E8E8EC;
                background: transparent;
            }
        """)
        layout.addWidget(msg_lbl)

        # Bottom action bar with Cancel and Proceed buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedHeight(34)
        self.cancel_btn.setMinimumWidth(100)
        self.cancel_btn.clicked.connect(self.reject)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                font-weight: 600;
                font-size: 9.5pt;
                padding: 4px 16px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                border-color: rgba(255, 255, 255, 0.25);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.08);
            }
        """)
        btn_layout.addWidget(self.cancel_btn)

        self.proceed_btn = QPushButton("Proceed (3s)")
        self.proceed_btn.setFixedHeight(34)
        self.proceed_btn.setMinimumWidth(130)
        self.proceed_btn.setEnabled(False)
        self.proceed_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.proceed_btn)

        # Initial disabled styling for Proceed button
        self._update_button_style()

        layout.addLayout(btn_layout)

        # Dialog styling
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1b20;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
        """)

    def _update_button_style(self):
        if self._countdown > 0:
            self.proceed_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.06);
                    color: rgba(255, 255, 255, 0.35);
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 6px;
                    font-weight: bold;
                    font-size: 9.5pt;
                    padding: 4px 16px;
                }
            """)
        else:
            self.proceed_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.accent_color};
                    color: #FFFFFF;
                    border: none;
                    border-radius: 6px;
                    font-weight: bold;
                    font-size: 9.5pt;
                    padding: 4px 16px;
                }}
                QPushButton:hover {{
                    background-color: {self._get_hover_color()};
                }}
                QPushButton:pressed {{
                    background-color: {self.accent_color};
                }}
            """)

    def _get_hover_color(self) -> str:
        try:
            qc = QColor(self.accent_color)
            h, s, v, a = qc.getHsv()
            val = min(255, int(v * 1.15)) if v > 0 else 30
            return QColor.fromHsv(h, s, val, a).name()
        except Exception:
            return self.accent_color

    def _start_timer(self):
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    def _on_tick(self):
        self._countdown -= 1
        if self._countdown > 0:
            self.proceed_btn.setText(f"Proceed ({self._countdown}s)")
        else:
            self._timer.stop()
            self.proceed_btn.setText("Proceed")
            self.proceed_btn.setEnabled(True)
            self._update_button_style()

    def closeEvent(self, event):
        # Allow closing via 'X' button to cancel anytime
        self.reject()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._countdown > 0:
                event.ignore()
                return
            self.accept()
            return
        super().keyPressEvent(event)


def show_dlc_mode_warning(parent=None) -> bool:
    """Convenience helper to display the DLC mode warning modal with Proceed and Cancel buttons.
    Returns True if user clicked Proceed, False if cancelled.
    """
    dlg = DlcModeWarningDialog(parent)
    return dlg.exec() == QDialog.DialogCode.Accepted
