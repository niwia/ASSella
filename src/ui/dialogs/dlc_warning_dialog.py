"""
Modal warning dialog displayed when the user activates DLC-only mode.
Enforces a 3-second lockout countdown before dismissal to ensure the user acknowledges the ownership and installation path instructions.
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
    Dialog informing the user about DLC-only mode requirements with a 3-second countdown timer.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DLC Mode")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setMaximumWidth(520)

        # Disable dialog close 'X' until countdown completes
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
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

        # Bottom action bar
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.ok_btn = QPushButton("Understood (3s)")
        self.ok_btn.setFixedHeight(34)
        self.ok_btn.setMinimumWidth(150)
        self.ok_btn.setEnabled(False)
        self.ok_btn.clicked.connect(self.accept)

        # Initial disabled styling
        self._update_button_style()

        btn_layout.addWidget(self.ok_btn)
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
            self.ok_btn.setStyleSheet("""
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
            self.ok_btn.setStyleSheet(f"""
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
            self.ok_btn.setText(f"Understood ({self._countdown}s)")
        else:
            self._timer.stop()
            self.ok_btn.setText("Understood")
            self.ok_btn.setEnabled(True)
            self._update_button_style()
            # Enable default close behavior
            self.setWindowFlags(
                Qt.WindowType.Dialog
                | Qt.WindowType.WindowTitleHint
                | Qt.WindowType.WindowCloseButtonHint
                | Qt.WindowType.CustomizeWindowHint
            )
            self.show()

    def closeEvent(self, event):
        if self._countdown > 0:
            event.ignore()
        else:
            event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._countdown > 0:
                event.ignore()
                return
        super().keyPressEvent(event)


def show_dlc_mode_warning(parent=None):
    """Convenience helper to display the 3-second lockout DLC mode warning modal."""
    dlg = DlcModeWarningDialog(parent)
    dlg.exec()
