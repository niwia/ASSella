import logging
import threading

from PyQt6.QtWidgets import (
    QDialog,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QWidget,
    QFrame,
    QScrollArea,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, pyqtSlot
from PyQt6.QtGui import QFont

from utils.settings import get_settings
from utils.version import (
    app_version,
    get_branch_from_version,
    get_build_date_display,
    check_for_updates,
)
from ui.dialogs.themed_dialog import install_themed_frame

logger = logging.getLogger(__name__)


class CreditsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Credits & Updates")
        self.setMinimumWidth(480)
        self.setMinimumHeight(420)
        self.resize(480, 460)
        self.setSizeGripEnabled(True)

        self.settings = get_settings()
        self.main_window = parent
        self.accent_color = self.settings.value("accent_color", "#a1c9fd")

        logger.debug("Opening CreditsDialog.")

        self.setStyleSheet(
            f"""
            QLabel {{
                color: #e0e0e0;
                background: transparent;
            }}
            QPushButton {{
                background-color: #242424;
                border: 1px solid #3a3a3a;
                border-radius: 5px;
                color: #ffffff;
                padding: 6px 16px;
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: #2e2e2e;
                border-color: {self.accent_color};
            }}
            QPushButton:disabled {{
                background-color: #1b1b1b;
                color: #555555;
                border-color: #222222;
            }}
            QScrollArea {{
                border: none;
                background: transparent;
            }}
            QScrollBar:vertical {{
                background: #1e1e1e;
                width: 6px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: #444444;
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            """
        )

        main_layout = install_themed_frame(self, margins=(20, 20, 20, 16), spacing=0)

        # ── Header ──────────────────────────────────────────────────────────
        main_layout.addWidget(self._build_header())
        main_layout.addSpacing(14)

        # Thin divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("background: #2a2a2a; max-height: 1px; border: none;")
        main_layout.addWidget(div)
        main_layout.addSpacing(14)

        # ── Scrollable body ──────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 6, 0)
        body_layout.setSpacing(16)

        body_layout.addWidget(self._build_developer_section())
        body_layout.addWidget(self._build_contributors_section())
        body_layout.addWidget(self._build_tools_section())
        body_layout.addStretch()

        scroll.setWidget(body)
        main_layout.addWidget(scroll, 1)
        main_layout.addSpacing(14)

        # ── Update row ───────────────────────────────────────────────────────
        main_layout.addWidget(self._build_update_row())
        main_layout.addSpacing(10)

        # ── Close button ─────────────────────────────────────────────────────
        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(34)
        close_btn.clicked.connect(self.reject)
        main_layout.addWidget(close_btn)

    # ─────────────────────────────────────────────────────────────────────────
    # Builder helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        left = QVBoxLayout()
        left.setSpacing(3)

        name = QLabel("ASSELA")
        name.setStyleSheet(
            f"font-size: 22px; font-weight: 900; color: {self.accent_color}; letter-spacing: 3px;"
        )

        # Build version + branch pill + date on the same row
        ver_row = QHBoxLayout()
        ver_row.setContentsMargins(0, 0, 0, 0)
        ver_row.setSpacing(8)

        build_date = get_build_date_display()
        ver_lbl = QLabel(f"v{app_version}  ·  {build_date}")
        ver_lbl.setStyleSheet("font-size: 11px; color: #666666;")

        branch = get_branch_from_version(app_version)
        branch_colors = {
            "beta":   ("#7B3F00", "#E07B00"),
            "canary": ("#4A2300", "#FF8C00"),
            "test":   ("#4A2300", "#FF8C00"),
            "main":   ("#1a2a3a", "#4A90D9"),
            "stable": ("#1a2a3a", "#4A90D9"),
        }
        bg, fg = branch_colors.get(branch, ("#2a2a2a", "#888888"))

        branch_pill = QLabel(branch.upper())
        branch_pill.setStyleSheet(
            f"font-size: 9px; font-weight: bold; color: {fg};"
            f"background: {bg}; border-radius: 3px; padding: 1px 6px;"
        )
        branch_pill.setFixedHeight(16)

        ver_row.addWidget(ver_lbl)
        ver_row.addWidget(branch_pill)
        ver_row.addStretch()

        left.addWidget(name)
        left.addLayout(ver_row)

        layout.addLayout(left)
        layout.addStretch()
        return w

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(
            f"font-size: 9px; font-weight: bold; color: {self.accent_color}; letter-spacing: 1px;"
        )
        return lbl

    def _build_developer_section(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        layout.addWidget(self._section_label("Developer"))

        name_lbl = QLabel("bakabakabaka")
        name_lbl.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #f0f0f0;"
        )
        layout.addWidget(name_lbl)

        orig_lbl = QLabel("Original code from ACCELA dev")
        orig_lbl.setStyleSheet(
            "font-size: 11px; color: #777777; font-style: italic;"
        )
        layout.addWidget(orig_lbl)
        return w

    def _build_contributors_section(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(self._section_label("Contributors"))

        contributors = [
            ("Morrenus",  "Steamless AIO / API", None, None),
            ("gogovang",  "Workshop / General", None, None),
            ("AceSLS",    "SLSsteam", "https://github.com/AceSLS/SLSsteam", "https://github.com/AceSLS/SLSsteam"),
            ("YesYes",    "EOS Proxy", "https://github.com/yesyes0649/eos-proxy", "https://github.com/yesyes0649/eos-proxy"),
        ]

        for cname, crole, name_url, role_url in contributors:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)

            name_lbl = QLabel()
            if name_url:
                name_lbl.setText(f'<a href="{name_url}" style="color: #cccccc; text-decoration: none;">{cname}</a>')
                name_lbl.setOpenExternalLinks(True)
                name_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                name_lbl.setText(cname)
            name_lbl.setStyleSheet("font-size: 13px; color: #cccccc; font-weight: bold;")
            name_lbl.setFixedWidth(120)

            role_lbl = QLabel()
            if role_url:
                role_lbl.setText(f'<a href="{role_url}" style="color: #666666; text-decoration: underline;">{crole}</a>')
                role_lbl.setOpenExternalLinks(True)
                role_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                role_lbl.setText(crole)
            role_lbl.setStyleSheet("font-size: 11px; color: #666666;")

            row.addWidget(name_lbl)
            row.addWidget(role_lbl)
            row.addStretch()
            layout.addLayout(row)

        return w

    def _build_tools_section(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(self._section_label("Third-Party Tools"))

        tools = [
            (
                "h3adcrab",
                "drazy",
                "https://github.com/Deadboy666/h3adcr-b",
                '<a href="https://github.com/Deadboy666/h3adcr-b" style="color: #555555; text-decoration: underline;">drazy</a>',
            ),
            (
                "Goldberg",
                "07/26 - Detanup01",
                "https://github.com/Detanup01/gbe_fork",
                '<a href="https://github.com/Detanup01/gbe_fork" style="color: #555555; text-decoration: underline;">07/26 - Detanup01</a>',
            ),
            (
                "Steamless",
                "morrenus / atom0s",
                None,
                'morrenus / <a href="https://github.com/atom0s/Steamless" style="color: #555555; text-decoration: underline;">atom0s</a>',
            ),
        ]

        for tname, trole_text, name_url, role_html in tools:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)

            name_lbl = QLabel()
            if name_url:
                name_lbl.setText(f'<a href="{name_url}" style="color: #999999; text-decoration: none; font-weight: bold;">{tname}</a>')
                name_lbl.setOpenExternalLinks(True)
                name_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                name_lbl.setText(tname)
            name_lbl.setStyleSheet("font-size: 12px; color: #999999;")
            name_lbl.setFixedWidth(140)

            role_lbl = QLabel(role_html)
            role_lbl.setOpenExternalLinks(True)
            role_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            role_lbl.setStyleSheet("font-size: 11px; color: #555555;")

            row.addWidget(name_lbl)
            row.addWidget(role_lbl)
            row.addStretch()
            layout.addLayout(row)

        return w

    def _build_update_row(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(
            "background: #1e1e1e; border-radius: 6px; border: 1px solid #2a2a2a;"
        )
        layout = QHBoxLayout(w)
        layout.setContentsMargins(12, 8, 10, 8)
        layout.setSpacing(10)

        self.status_label = QLabel("Check for a newer version.")
        self.status_label.setStyleSheet("font-size: 12px; color: #888888; background: transparent;")
        layout.addWidget(self.status_label, 1)

        self.check_btn = QPushButton("Check for Updates")
        self.check_btn.setFixedHeight(30)
        self.check_btn.clicked.connect(self.check_updates)
        layout.addWidget(self.check_btn)
        return w

    # ─────────────────────────────────────────────────────────────────────────
    # Update check logic (unchanged)
    # ─────────────────────────────────────────────────────────────────────────

    def check_updates(self):
        self.check_btn.setEnabled(False)
        self.status_label.setText("Checking for updates...")
        self.status_label.setStyleSheet("color: #aaaaaa; font-size: 12px; background: transparent;")

        def _check_sync():
            try:
                configured_channel = self.settings.value("update_channel", "").strip().lower()
                res = check_for_updates(configured_channel=configured_channel, current_version=app_version)
                if res["status"] == "update_available":
                    QMetaObject.invokeMethod(
                        self, "_on_check_available",
                        Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, res["remote_version"] or ""),
                    )
                elif res["status"] == "up_to_date":
                    QMetaObject.invokeMethod(
                        self, "_on_check_up_to_date",
                        Qt.ConnectionType.QueuedConnection,
                    )
                else:
                    QMetaObject.invokeMethod(
                        self, "_on_check_failed",
                        Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, res.get("error") or "Could not determine latest version."),
                    )
            except Exception as e:
                logger.warning(f"Credits check updates failed: {e}")
                QMetaObject.invokeMethod(
                    self, "_on_check_failed",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, str(e)),
                )

        threading.Thread(target=_check_sync, daemon=True).start()

    @pyqtSlot(str)
    def _on_check_available(self, remote_version: str) -> None:
        self.status_label.setText(f"Update available: v{remote_version}")
        self.status_label.setStyleSheet(
            "color: #E07B00; font-weight: bold; font-size: 12px; background: transparent;"
        )
        self.check_btn.setText("Install Update")
        self.check_btn.setEnabled(True)
        try:
            self.check_btn.clicked.disconnect()
        except TypeError:
            pass
        self.check_btn.clicked.connect(self.trigger_self_update)

    @pyqtSlot()
    def _on_check_up_to_date(self) -> None:
        self.status_label.setText("ASSella is up to date.")
        self.status_label.setStyleSheet(
            "color: #2ECC71; font-weight: bold; font-size: 12px; background: transparent;"
        )
        self.check_btn.setText("Check for Updates")
        self.check_btn.setEnabled(True)

    @pyqtSlot(str)
    def _on_check_failed(self, error: str) -> None:
        self.status_label.setText("Could not reach update server.")
        self.status_label.setStyleSheet(
            "color: #E74C3C; font-size: 12px; background: transparent;"
        )
        self.check_btn.setText("Retry")
        self.check_btn.setEnabled(True)

    def trigger_self_update(self) -> None:
        self.reject()
        if self.main_window and hasattr(self.main_window, "run_self_update"):
            self.main_window.run_self_update()
