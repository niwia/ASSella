import logging
import urllib.request
import threading
import re

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
from utils.version import app_version

logger = logging.getLogger(__name__)


def _get_branch_from_version(version: str) -> str:
    """Derive the branch label from the version string."""
    v = version.lower()
    if "+assela-" in v:
        tag = v.split("+assela-", 1)[1]
    else:
        tag = v
    if "rc" in tag or "beta" in tag:
        return "beta"
    if "alpha" in tag or "test" in tag or "dev" in tag:
        return "test"
    return "main"


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
            QDialog {{
                background-color: #151515;
            }}
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

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 16)
        main_layout.setSpacing(0)

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

        # Build version + branch pill on the same row
        ver_row = QHBoxLayout()
        ver_row.setContentsMargins(0, 0, 0, 0)
        ver_row.setSpacing(8)

        ver_lbl = QLabel(f"v{app_version}")
        ver_lbl.setStyleSheet("font-size: 11px; color: #666666;")

        branch = _get_branch_from_version(app_version)
        branch_colors = {
            "beta":  ("#7B3F00", "#E07B00"),
            "test":  ("#4A2300", "#FF8C00"),
            "main":  ("#1a2a3a", "#4A90D9"),
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
        layout.setSpacing(6)

        layout.addWidget(self._section_label("Developer"))

        name_lbl = QLabel("bakabakabaka")
        name_lbl.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #f0f0f0;"
        )
        layout.addWidget(name_lbl)
        return w

    def _build_contributors_section(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(self._section_label("Contributors"))

        contributors = ["drazy", "morrenus", "GogoVang"]

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)

        cols = 3
        for i, name in enumerate(contributors):
            lbl = QLabel(name)
            lbl.setStyleSheet("font-size: 13px; color: #cccccc; font-weight: bold;")
            lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            grid.addWidget(lbl, i // cols, i % cols)

        layout.addLayout(grid)
        return w

    def _build_tools_section(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(self._section_label("Third-Party Tools"))

        tools = [
            "GreenLuma",
            "SLSteam",
            "Steamless",
            "DepotDownloaderMod",
            "SLScheevo",
        ]

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)

        cols = 3
        for i, tool in enumerate(tools):
            lbl = QLabel(tool)
            lbl.setStyleSheet("font-size: 12px; color: #999999;")
            lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            grid.addWidget(lbl, i // cols, i % cols)

        layout.addLayout(grid)
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

        def _extract_semver(raw: str) -> str:
            if "+ASSella-" in raw:
                return raw.split("+ASSella-", 1)[1].strip()
            return raw.strip()

        def _parse_version(v_str: str) -> tuple:
            v_str = v_str.lstrip("v").strip()
            parts = v_str.split("-")
            main_part = parts[0]
            main_numbers = []
            for num in main_part.split("."):
                try:
                    main_numbers.append(int(num))
                except ValueError:
                    main_numbers.append(0)
            while len(main_numbers) < 3:
                main_numbers.append(0)
            pre_release_val = 0
            pre_release_num = 0
            if len(parts) > 1:
                pre_tag = parts[1].lower()
                pre_release_val = -1
                match = re.search(r"\d+$", pre_tag)
                if match:
                    try:
                        pre_release_num = int(match.group(0))
                    except ValueError:
                        pre_release_num = 0
            return tuple(main_numbers) + (pre_release_val, pre_release_num)

        def _check_sync():
            try:
                local_clean = _extract_semver(app_version)
                if "alpha" in local_clean.lower():
                    branch = "alpha"
                elif any(x in local_clean.lower() for x in ("beta", "rc")):
                    branch = "beta"
                else:
                    branch = "main"
                url = f"https://raw.githubusercontent.com/niwia/ASSella/{branch}/src/res/version"
                req = urllib.request.Request(url, headers={"User-Agent": "ASSella-Updater"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    remote_raw = response.read().decode("utf-8").strip()
                    remote_clean = _extract_semver(remote_raw)
                    if remote_clean:
                        if _parse_version(remote_clean) > _parse_version(local_clean):
                            QMetaObject.invokeMethod(
                                self, "_on_check_available",
                                Qt.ConnectionType.QueuedConnection,
                                Q_ARG(str, remote_clean),
                            )
                        else:
                            QMetaObject.invokeMethod(
                                self, "_on_check_up_to_date",
                                Qt.ConnectionType.QueuedConnection,
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
