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
        self._broadcast_active = True
        formatted = message.upper()
        self.last_msg = formatted
        self.label.setText(formatted)

        if level in ("warn", "warning", "error"):
            self.label.setStyleSheet("color: #FFB84D; font-size: 12px; font-weight: bold; border: none; background: transparent;")

        # Automatically expire after duration (defaults to 15s)
        QTimer.singleShot(max(1, duration) * 1000, self._on_broadcast_expired)

    def _on_broadcast_expired(self) -> None:
        """Revert broadcast back to regular log stream and default styling."""
        self._broadcast_active = False
        self.update_style()
        self.set_status("SYSTEM READY · DRAG AND DROP ZIP TO INSTALL", force=True)

    def set_status(self, message: str, force: bool = False) -> None:
        """Programmatically set the status message on the pager."""
        if self._broadcast_active and not force:
            return
        self.last_msg = message.upper()
        self.label.setText(self.last_msg)

    @pyqtSlot(str)
    def on_new_log(self, raw_msg: str) -> None:
        """Filter log stream and show human-readable status changes."""
        if self._broadcast_active:
            return
        msg = raw_msg.strip()
        if not msg:
            return

        msg_lower = msg.lower()

        # Clean/exclude traces and verbose library logs
        exclusions = [
            "debug",
            "traceback",
            "file \"",
            "line ",
            "connection pool",
            "urllib3",
            "http/1.1",
            "get_user_stats",
            "heartbeat",
        ]
        if any(exc in msg_lower for exc in exclusions):
            return

        # Check for interesting user-facing keywords
        interesting_keywords = [
            "download",
            "depot",
            "manifest",
            "fetch",
            "drm",
            "steamless",
            "achievement",
            "scheevo",
            "zip",
            "extract",
            "install",
            "finaliz",
            "success",
            "fail",
            "error",
            "warn",
            "start",
            "complet",
            "run",
            "pause",
            "resum",
            "stop",
            "block",
            "optimal",
        ]

        if any(kw in msg_lower for kw in interesting_keywords):
            # Clean up prefix like "[INFO] " or "[WARNING] "
            cleaned = msg
            if cleaned.startswith("[") and "]" in cleaned:
                idx = cleaned.find("]")
                cleaned = cleaned[idx + 1 :].strip()

            # Limit length to keep it single-line
            if len(cleaned) > 90:
                cleaned = cleaned[:87] + "..."

            self.set_status(cleaned)

    def update_style(self) -> None:
        """Apply theme color choices to the LCD container and text."""
        settings = get_settings()
        accent = settings.value("accent_color", "#C06C84")
        bg_color = settings.value("background_color", "#000000")

        # Register bundled typewriter/calculator fonts if not already registered
        from PyQt6.QtGui import QFontDatabase
        from utils.helpers import get_base_path
        
        trixie_path = get_base_path() / "src" / "res" / "TrixieCyrG-Plain Regular.otf"
        if trixie_path.exists():
            QFontDatabase.addApplicationFont(str(trixie_path))
            
        sonic_path = get_base_path() / "src" / "res" / "sonic" / "sonic-1-hud-font.otf"
        if sonic_path.exists():
            QFontDatabase.addApplicationFont(str(sonic_path))

        # Prioritize typewriter (TrixieCyrG-Plain) and calculator (Sonic 1 HUD Font)
        font_family = "TrixieCyrG-Plain, Sonic 1 HUD Font, Courier New, Consolas, monospace"

        # Retro LCD styling: dark recessed container, monospace text
        self.setStyleSheet(
            f"""
            StatusPagerWidget {{
                background-color: rgba(10, 10, 10, 220);
                border: 1px solid rgba(255, 255, 255, 12);
                border-radius: 6px;
                margin: 4px 15px;
            }}
            QLabel {{
                color: {accent};
                font-family: {font_family};
                font-size: 12px;
                font-weight: bold;
                border: none;
                background: transparent;
            }}
        """
        )
