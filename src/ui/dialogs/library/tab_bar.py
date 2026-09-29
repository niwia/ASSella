import logging
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QWidget,
)

logger = logging.getLogger(__name__)

TAB_ACCELA = "accela"
TAB_ATOM = "at0m"
TAB_STEAM = "steam"


class LibraryTabBar(QWidget):
    """
    Modern pill-style tab navigation bar for the Game Library dialog.
    Supports ACCELA, AT0-M, and Steam tabs with optional item count badges.
    """

    current_tab_changed = pyqtSignal(str)

    def __init__(
        self,
        accent_color: str = "#a1c9fd",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.accent_color = accent_color
        self._current_tab = TAB_ACCELA

        self._counts = {
            TAB_ACCELA: 0,
            TAB_ATOM: 0,
            TAB_STEAM: 0,
        }
        self._is_scanning = {
            TAB_STEAM: False,
        }

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(8)

        self.btn_accela = QPushButton("ACCELA")
        self.btn_accela.setCheckable(True)
        self.btn_accela.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_accela.clicked.connect(lambda: self.set_current_tab(TAB_ACCELA))

        self.btn_atom = QPushButton("AT0-M")
        self.btn_atom.setCheckable(True)
        self.btn_atom.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_atom.clicked.connect(lambda: self.set_current_tab(TAB_ATOM))

        self.btn_steam = QPushButton("Steam (Beta)")
        self.btn_steam.setCheckable(True)
        self.btn_steam.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_steam.clicked.connect(lambda: self.set_current_tab(TAB_STEAM))

        self._buttons = {
            TAB_ACCELA: self.btn_accela,
            TAB_ATOM: self.btn_atom,
            TAB_STEAM: self.btn_steam,
        }

        layout.addWidget(self.btn_accela)
        layout.addWidget(self.btn_atom)
        layout.addWidget(self.btn_steam)
        layout.addStretch()

        self._apply_styles()
        self._update_tab_state()

    def _apply_styles(self) -> None:
        style = f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.05);
                color: rgba(255, 255, 255, 0.7);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 15px;
                padding: 5px 16px;
                font-size: 11px;
                font-weight: 600;
                min-height: 20px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.1);
                color: #FFFFFF;
                border-color: rgba(255, 255, 255, 0.2);
            }}
            QPushButton:checked {{
                background-color: {self.accent_color};
                color: #000000;
                border: 1px solid {self.accent_color};
                font-weight: bold;
            }}
        """
        for btn in self._buttons.values():
            btn.setStyleSheet(style)

    def current_tab(self) -> str:
        return self._current_tab

    def set_current_tab(self, tab: str) -> None:
        if tab not in self._buttons:
            tab = TAB_ACCELA
        if self._current_tab == tab and self._buttons[tab].isChecked():
            return
        self._current_tab = tab
        self._update_tab_state()
        self.current_tab_changed.emit(self._current_tab)

    def update_counts(
        self,
        accela: Optional[int] = None,
        atom: Optional[int] = None,
        steam: Optional[int] = None,
    ) -> None:
        """Update count labels on the tab buttons."""
        if accela is not None:
            self._counts[TAB_ACCELA] = accela
        if atom is not None:
            self._counts[TAB_ATOM] = atom
        if steam is not None:
            self._counts[TAB_STEAM] = steam

        self._refresh_button_text()

    def set_scanning(self, tab: str, scanning: bool) -> None:
        """Mark a tab as currently scanning in background."""
        self._is_scanning[tab] = scanning
        self._refresh_button_text()

    def _refresh_button_text(self) -> None:
        c_accela = self._counts[TAB_ACCELA]
        c_atom = self._counts[TAB_ATOM]
        c_steam = self._counts[TAB_STEAM]

        self.btn_accela.setText(f"ACCELA ({c_accela})" if c_accela > 0 else "ACCELA")
        self.btn_atom.setText(f"AT0-M ({c_atom})" if c_atom > 0 else "AT0-M")
        if self._is_scanning.get(TAB_STEAM):
            self.btn_steam.setText("Steam (Beta) (Scanning...)")
        elif c_steam > 0:
            self.btn_steam.setText(f"Steam (Beta) ({c_steam})")
        else:
            self.btn_steam.setText("Steam (Beta)")

    def _update_tab_state(self) -> None:
        for tab_key, btn in self._buttons.items():
            btn.blockSignals(True)
            btn.setChecked(tab_key == self._current_tab)
            btn.blockSignals(False)
