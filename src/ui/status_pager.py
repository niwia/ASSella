import os
import time
import json
import logging
import threading
import urllib.request
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt, pyqtSlot, pyqtSignal, QTimer
from utils.logger import qt_log_handler
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class StatusPagerWidget(QFrame):
    """A full-width, persistent pager-style status display with a retro LCD/calculator aesthetic.

    Displays smart, human-readable status updates by filtering the live log stream.
    Also supports high-priority remote broadcast notices from the developer.
    """

    broadcast_signal = pyqtSignal(str, int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self.last_msg = "SYSTEM READY · DRAG AND DROP ZIP TO INSTALL"
        self._broadcast_active = False
        self._critical_warning_active = False

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(15, 0, 15, 0)
        self.layout.setSpacing(0)

        self.label = QLabel(self.last_msg)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(self.label)

        self.update_style()

        # Connect to the live log stream handler
        qt_log_handler.new_record.connect(self.on_new_log)

        # Connect broadcast signal and trigger background fetch
        self.broadcast_signal.connect(self._handle_broadcast)
        self._fetch_remote_broadcast()

    def _fetch_remote_broadcast(self) -> None:
        """Asynchronously check for active developer broadcast announcements."""
        def _worker():
            url = os.environ.get(
                "ASSELLA_BROADCAST_URL",
                f"https://raw.githubusercontent.com/niwia/ASSella/beta/broadcast.json?t={int(time.time())}",
            )
            try:
                # Support both local file paths (for testing) and HTTP(S) URLs
                if url.startswith("file://") or url.startswith("/"):
                    local_path = url.replace("file://", "")
                    with open(local_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                else:
                    req = urllib.request.Request(
                        url,
                        headers={"User-Agent": "ASSella-Client", "Cache-Control": "no-cache"},
                    )
                    with urllib.request.urlopen(req, timeout=2.5) as resp:
                        if resp.status == 200:
                            data = json.loads(resp.read().decode("utf-8"))
                        else:
                            return

                if data.get("active", False):
                    msg = str(data.get("message", "")).strip()
                    if msg:
                        dur = int(data.get("duration", 15))
                        lvl = str(data.get("level", "warning")).lower()
                        self.broadcast_signal.emit(msg, dur, lvl)
            except Exception as e:
                logger.debug(f"Remote broadcast check skipped/failed: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(str, int, str)
    def _handle_broadcast(self, message: str, duration: int, level: str) -> None:
        """Display high-priority broadcast announcement and lock status bar for specified duration."""
        if self._critical_warning_active and level not in ("critical", "emergency"):
            return
        self._broadcast_active = True
        formatted = message.upper()
        self.last_msg = formatted
        self.label.setText(formatted)

        if level in ("warn", "warning", "error", "critical"):
            self.label.setStyleSheet("color: #FFB84D; font-size: 12px; font-weight: bold; border: none; background: transparent;")

        # Automatically expire after duration (defaults to 15s)
        QTimer.singleShot(max(1, duration) * 1000, self._on_broadcast_expired)

    def _on_broadcast_expired(self) -> None:
        """Revert broadcast back to regular log stream and default styling."""
        self._critical_warning_active = False
        self._broadcast_active = False
        self.label.setStyleSheet("")
        self.update_style()
        self.set_status("SYSTEM READY · DRAG AND DROP ZIP TO INSTALL", force=True)

    def set_status(self, message: str, force: bool = False, is_warning: bool = False) -> None:
        """Programmatically set the status message on the pager."""
        if self._broadcast_active and not force:
            return
        self.last_msg = message.upper()
        self.label.setText(self.last_msg)
        if is_warning:
            self.label.setStyleSheet("color: #FFB84D; font-size: 12px; font-weight: bold; border: none; background: transparent;")
        elif not self._broadcast_active:
            self.label.setStyleSheet("")
            self.update_style()

    @pyqtSlot(str)
    def show_warning_str(self, message: str) -> None:
        """Display a prominent warning status locked for 30 seconds."""
        self.show_warning(message, 30)

    @pyqtSlot(str, int)
    def show_warning(self, message: str, duration: int = 30) -> None:
        """Display a prominent warning status locked for duration seconds."""
        self._critical_warning_active = True
        self._handle_broadcast(message, duration, "critical")

    @pyqtSlot(object)
    def on_new_log(self, payload) -> None:
        """Show user-facing log events on the single-line status strip.

        Selection is by intent, not by wording. The handler marks each record
        with ``user_visible``; this previously had to substring-match the
        message against a list of keywords, which dropped real events when the
        wording drifted and flickered noise whenever a line happened to contain
        a word like "run" or "start".

        Accepts a plain string too so a caller pushing text directly still works.
        """
        if self._broadcast_active:
            return

        if isinstance(payload, dict):
            if not payload.get("user_visible"):
                return
            msg = str(payload.get("text", "")).strip()
            is_warn_log = int(payload.get("level", logging.INFO)) >= logging.WARNING
        else:
            # Back-compat path: no metadata, so be conservative and only surface
            # genuine warnings rather than showing every line that reaches us.
            msg = str(payload or "").strip()
            is_warn_log = "[WARNING]" in msg.upper() or "[ERROR]" in msg.upper()
            if not is_warn_log:
                return

        if not msg:
            return

        # The Qt formatter already prefixes warnings; strip it so the label is
        # just the sentence.
        if msg.startswith("[") and "]" in msg:
            idx = msg.find("]")
            if msg[1:idx].upper() in ("INFO", "WARNING", "ERROR", "CRITICAL", "DEBUG"):
                is_warn_log = is_warn_log or msg[1:idx].upper() in (
                    "WARNING",
                    "ERROR",
                    "CRITICAL",
                )
                msg = msg[idx + 1 :].strip()

        if "crashed" in msg.lower() or "filewatcher" in msg.lower():
            is_warn_log = True

        # Keep it single-line.
        if len(msg) > 90:
            msg = msg[:87] + "..."

        self.set_status(msg, is_warning=is_warn_log)

    def update_style(self) -> None:
        """Apply theme color choices to the LCD container and text."""
        settings = get_settings()
        accent = settings.value("accent_color", "#C06C84")
        bg_color = settings.value("background_color", "#000000")
        ui_mode = settings.value("ui_mode", "default")

        from PyQt6.QtGui import QFontDatabase
        from utils.helpers import get_base_path

        if ui_mode == "sonic":
            sonic_path = get_base_path() / "src" / "res" / "sonic" / "sonic-1-hud-font.otf"
            if sonic_path.exists():
                QFontDatabase.addApplicationFont(str(sonic_path))
            font_family = "'Sonic 1 HUD Font', 'Courier New', monospace"
        else:
            current_font = settings.value("font", "Open Sans")
            if current_font.lower() in ("trixiecyrg-plain", "trixiecyrg-plain regular", "trixie"):
                current_font = "Open Sans"
            font_family = f"'{current_font}', 'Open Sans', 'Google Sans', sans-serif"

        user_font_size = settings.value("font-size", 10, type=int)
        pager_size = max(10, min(14, int(user_font_size * 1.1)))

        # Retro LCD styling: dark recessed container, crisp centered status text
        self.setStyleSheet(
            f"""
            StatusPagerWidget {{
                background-color: rgba(18, 18, 22, 230);
                border: 1px solid rgba(255, 255, 255, 14);
                border-radius: 6px;
                margin: 4px 15px;
            }}
            QLabel {{
                color: {accent};
                font-family: {font_family};
                font-size: {pager_size}px;
                font-weight: bold;
                border: none;
                background: transparent;
            }}
        """
        )
