from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout

if TYPE_CHECKING:
    from ui.widgets.simplified_terminal import SimplifiedTerminalWidget


class RecentActivityItemWidget(QFrame):
    """Interactive card for a recent activity entry. Clickable for games, static for workshop."""
    def __init__(self, entry: dict, parent_terminal):
        super().__init__()
        self.entry = entry
        self.parent_terminal = parent_terminal
        appid_val = str(entry.get("appid", "")).strip()
        self.appid = appid_val
        self.is_game = bool(appid_val and appid_val.isdigit() and appid_val.lower() != "workshop")

        self.setFrameShape(QFrame.Shape.NoFrame)
        self._init_ui()

    def _init_ui(self):
        t = self.entry.get("timestamp", 0)
        entry_dt = datetime.fromtimestamp(t)
        now_dt = datetime.now()
        if entry_dt.date() == now_dt.date():
            time_str = entry_dt.strftime('%H:%M')
        else:
            time_str = entry_dt.strftime('%b %d, %H:%M')

        from ui.dialogs.gamelibrary import format_game_display_name
        game_name = self.entry.get('game_name', 'Unknown')
        game_data = {"game_name": game_name, "appid": self.appid}
        display_name = format_game_display_name(game_data)

        success = self.entry.get("success", True)
        if not success:
            stat_text = "<span style='color: #E74C3C;'>Installation Failed</span>"
        else:
            dl_size = self.entry.get("download_size", 0)
            if dl_size > 0:
                # Imported here rather than at module scope: simplified_terminal
                # imports this module, so a top-level import would be circular.
                # The formatters are static, so this is safe at call time.
                from ui.widgets.simplified_terminal import SimplifiedTerminalWidget
                size_str = SimplifiedTerminalWidget._format_size(dl_size)
                dur_str = SimplifiedTerminalWidget._format_duration(self.entry.get("download_duration", 0))
                speed_str = SimplifiedTerminalWidget._format_speed(self.entry.get("avg_speed", 0))
                stat_text = f"<span style='color: #2ECC71;'>Success</span> • {size_str} in {dur_str} ({speed_str})"
            elif self.entry.get("handed_off"):
                stat_text = "<span style='color: #2ECC71;'>Handed off to Steam</span>"
            else:
                stat_text = "<span style='color: #2ECC71;'>Success</span> • Zip file"

        ach_status = self.entry.get("ach_status", "Skipped")
        steamless_status = self.entry.get("steamless_status", "Skipped")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        lbl = QLabel()
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setText(f"""
        <div style="margin-bottom: 2px;">
            <span style="color: #FFFFFF; font-weight: bold; font-size: 9pt;">{display_name}</span>
            <span style="color: #888888; font-size: 8pt; float: right;">[{time_str}]</span>
            <br/>
            <span style="color: #DDDDDD; font-size: 8pt;">{stat_text}</span>
            <br/>
            <span style="color: #AAAAAA; font-size: 8pt;">Ach: {ach_status} • DRM: {steamless_status}</span>
        </div>
        """)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("border: none; background: transparent;")
        lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(lbl)

        if self.is_game:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(f"Click to open Game Details for {game_name}")
            self.setStyleSheet("""
                RecentActivityItemWidget {
                    background-color: transparent;
                    border: 1px solid transparent;
                    border-radius: 6px;
                }
                RecentActivityItemWidget:hover {
                    background-color: rgba(255, 255, 255, 0.07);
                    border: 1px solid rgba(255, 255, 255, 0.15);
                }
            """)
        else:
            self.setStyleSheet("RecentActivityItemWidget { background-color: transparent; border: none; }")

    def mousePressEvent(self, event):
        if self.is_game and event.button() == Qt.MouseButton.LeftButton:
            if self.parent_terminal and hasattr(self.parent_terminal, "_open_game_details"):
                self.parent_terminal._open_game_details(self.appid)
        super().mousePressEvent(event)
