import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs.themed_dialog import install_themed_frame
from utils.settings import get_settings

logger = logging.getLogger(__name__)


def _format_size(num_bytes: int) -> str:
    """Format byte size cleanly for executable rows."""
    if not num_bytes or num_bytes <= 0:
        return ""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def _normalize_summary(
    exe_count: int,
    processed_count: int,
    success: bool,
    summary: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Ensure a complete summary dictionary even for legacy positional calls."""
    if summary is not None:
        return dict(summary)

    scanned = max(0, int(exe_count))
    fixed = max(0, int(processed_count))
    if scanned == 0:
        overall = "no_executables"
        clean = 0
        failed = 0
    elif fixed > 0:
        overall = "fixed"
        clean = max(0, scanned - fixed)
        failed = 0
    else:
        overall = "clean" if success or scanned > 0 else "failed"
        clean = scanned if overall == "clean" else 0
        failed = 0 if overall == "clean" else scanned

    return {
        "game_directory": "",
        "target_exe": None,
        "engine": "",
        "scanned_count": scanned,
        "fixed_count": fixed,
        "clean_count": clean,
        "failed_count": failed,
        "overall_status": overall,
        "error": None,
        "executables": [],
    }


def _status_presentation(summary: Dict[str, Any]) -> Tuple[str, str, str]:
    """Return (headline, detail, hex_color) for the summary status banner."""
    overall = summary.get("overall_status", "clean")
    scanned = int(summary.get("scanned_count", 0))
    fixed = int(summary.get("fixed_count", 0))
    failed = int(summary.get("failed_count", 0))
    err = summary.get("error") or ""

    if overall == "fixed":
        return (
            "DRM Removed",
            f"Unpacked {fixed} of {scanned} scanned executable(s).",
            "#4ADE80",
        )
    if overall == "partial_failure":
        return (
            "Completed with Errors",
            f"{fixed} unpacked, {failed} failed out of {scanned} scanned executable(s).",
            "#FBBF24",
        )
    if overall == "failed":
        detail = err or f"Failed to unpack {failed} of {scanned} scanned executable(s)."
        return ("Steamless Failed", detail, "#F87171")
    if overall == "no_executables":
        detail = err or "No eligible Windows executables (>= 30 KB) were found."
        return ("No Suitable Executables Found", detail, "#FBBF24")
    return (
        "No SteamStub DRM Found",
        f"Evaluated {scanned} executable(s) — none required unpacking.",
        "#93C5FD",
    )


class SteamlessResumeDialog(QDialog):
    """Compact summary dialog for Steamless executable scanning and DRM removal."""

    def __init__(
        self,
        game_name: str,
        exe_count: int = 0,
        processed_count: int = 0,
        success: bool = False,
        parent: Optional[QWidget] = None,
        *,
        summary: Optional[Dict[str, Any]] = None,
        logs: Optional[List[str]] = None,
        appid: Optional[str] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Steamless Complete")
        self.setMinimumWidth(420)
        self.setWindowModality(
            Qt.WindowModality.WindowModal if parent is not None else Qt.WindowModality.ApplicationModal
        )

        settings = get_settings()
        self._accent = settings.value("accent_color", "#a1c9fd")
        self._summary = _normalize_summary(exe_count, processed_count, success, summary)
        self._logs = list(logs or [])
        self._appid = str(appid).strip() if appid else str(self._summary.get("appid") or "").strip()

        self.stat_values: Dict[str, QLabel] = {}
        self.game_name_label: Optional[QLabel] = None
        self.appid_label: Optional[QLabel] = None
        self.status_headline_label: Optional[QLabel] = None
        self.status_detail_label: Optional[QLabel] = None
        self.clean_toggle_btn: Optional[QPushButton] = None
        self.clean_container: Optional[QWidget] = None
        self.log_toggle_btn: Optional[QPushButton] = None
        self.log_view: Optional[QPlainTextEdit] = None

        self.layout = install_themed_frame(
            self,
            "Steamless Complete",
            margins=(16, 12, 16, 14),
            spacing=8,
        )

        self._setup_ui(game_name)
        self._shrink_to_fit()

        logger.debug(
            "SteamlessResumeDialog initialized: %s (appid=%s, scanned=%s, fixed=%s, clean=%s, failed=%s)",
            game_name,
            self._appid,
            self._summary.get("scanned_count", 0),
            self._summary.get("fixed_count", 0),
            self._summary.get("clean_count", 0),
            self._summary.get("failed_count", 0),
        )

    def _shrink_to_fit(self) -> None:
        """Compact window height cleanly around visible contents."""
        self.layout.activate()
        hint = self.sizeHint()
        target_w = max(430, min(hint.width(), 560))
        self.resize(target_w, hint.height())

    def _setup_ui(self, game_name: str) -> None:
        """Build the compact structured Steamless summary UI."""
        self._create_header(game_name)
        self._create_status_banner()
        self._create_stat_cards()
        self._create_executable_sections()
        self._create_footer()

    def _create_header(self, game_name: str) -> None:
        header_box = QVBoxLayout()
        header_box.setContentsMargins(0, 0, 0, 2)
        header_box.setSpacing(2)

        display_target = game_name or self._summary.get("game_directory") or "Unknown Target"
        self.game_name_label = QLabel(display_target)
        self.game_name_label.setWordWrap(True)
        self.game_name_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.game_name_label.setStyleSheet(
            f"font-size: 13pt; font-weight: 700; color: {self._accent}; background: transparent;"
        )
        header_box.addWidget(self.game_name_label)

        if self._appid:
            self.appid_label = QLabel(f"AppID: {self._appid}")
            self.appid_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.appid_label.setStyleSheet(
                "font-size: 8.5pt; color: rgba(255, 255, 255, 0.58); background: transparent;"
            )
            header_box.addWidget(self.appid_label)

        self.layout.addLayout(header_box)

    def _create_status_banner(self) -> None:
        headline, detail, color = _status_presentation(self._summary)

        banner = QFrame()
        banner.setStyleSheet(
            "QFrame {"
            "  background-color: rgba(255, 255, 255, 0.04);"
            "  border: 1px solid rgba(255, 255, 255, 0.10);"
            f" border-left: 3px solid {color};"
            "  border-radius: 7px;"
            "}"
        )
        banner_layout = QVBoxLayout(banner)
        banner_layout.setContentsMargins(10, 7, 12, 7)
        banner_layout.setSpacing(2)

        self.status_headline_label = QLabel(headline)
        self.status_headline_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.status_headline_label.setStyleSheet(
            f"font-size: 10.5pt; font-weight: 700; color: {color}; border: none; background: transparent;"
        )
        banner_layout.addWidget(self.status_headline_label)

        self.status_detail_label = QLabel(detail)
        self.status_detail_label.setWordWrap(True)
        self.status_detail_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.status_detail_label.setStyleSheet(
            "font-size: 9pt; color: rgba(255, 255, 255, 0.78); border: none; background: transparent;"
        )
        banner_layout.addWidget(self.status_detail_label)

        self.layout.addWidget(banner)

    def _create_stat_cards(self) -> None:
        cards_row = QHBoxLayout()
        cards_row.setContentsMargins(0, 0, 0, 0)
        cards_row.setSpacing(8)

        scanned = int(self._summary.get("scanned_count", 0))
        fixed = int(self._summary.get("fixed_count", 0))
        clean = int(self._summary.get("clean_count", 0))
        failed = int(self._summary.get("failed_count", 0))

        specs = [
            ("scanned", "Scanned", str(scanned), self._accent),
            (
                "fixed",
                "DRM Removed",
                str(fixed),
                "#4ADE80" if fixed > 0 else "rgba(255, 255, 255, 0.50)",
            ),
            (
                "clean",
                "Clean",
                str(clean),
                "#93C5FD" if clean > 0 else "rgba(255, 255, 255, 0.50)",
            ),
        ]
        if failed > 0:
            specs.append(("failed", "Failed", str(failed), "#F87171"))

        for key, title, value, color in specs:
            card = QFrame()
            card.setStyleSheet(
                "QFrame {"
                "  background-color: rgba(255, 255, 255, 0.04);"
                "  border: 1px solid rgba(255, 255, 255, 0.09);"
                "  border-radius: 7px;"
                "}"
            )
            col = QVBoxLayout(card)
            col.setContentsMargins(8, 6, 8, 6)
            col.setSpacing(1)

            val_lbl = QLabel(value)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val_lbl.setStyleSheet(
                f"font-size: 14pt; font-weight: 700; color: {color}; border: none; background: transparent;"
            )
            col.addWidget(val_lbl)

            cap_lbl = QLabel(title)
            cap_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap_lbl.setStyleSheet(
                "font-size: 8pt; color: rgba(255, 255, 255, 0.62); border: none; background: transparent;"
            )
            col.addWidget(cap_lbl)

            self.stat_values[key] = val_lbl
            cards_row.addWidget(card, 1)

        self.layout.addLayout(cards_row)

    def _create_executable_sections(self) -> None:
        executables = list(self._summary.get("executables") or [])
        actionable = [
            item for item in executables if item.get("status") in ("fixed", "failed")
        ]
        clean_items = [item for item in executables if item.get("status") == "clean"]

        if actionable:
            self.layout.addWidget(self._build_exe_list_widget(actionable, max_height=200))
        elif self._summary.get("overall_status") == "clean":
            empty_card = QFrame()
            empty_card.setStyleSheet(
                "QFrame {"
                "  background-color: rgba(255, 255, 255, 0.03);"
                "  border: 1px dashed rgba(255, 255, 255, 0.14);"
                "  border-radius: 7px;"
                "}"
            )
            empty_layout = QVBoxLayout(empty_card)
            empty_layout.setContentsMargins(10, 8, 10, 8)
            note = QLabel(
                "All evaluated executables are already free of SteamStub DRM. No files were modified."
            )
            note.setWordWrap(True)
            note.setStyleSheet(
                "font-size: 8.5pt; color: rgba(255, 255, 255, 0.72); border: none; background: transparent;"
            )
            empty_layout.addWidget(note)
            self.layout.addWidget(empty_card)

        if clean_items:
            count = len(clean_items)
            self.clean_toggle_btn = QPushButton(f"▸ Show clean executables ({count})")
            self.clean_toggle_btn.setCheckable(True)
            self.clean_toggle_btn.setChecked(False)
            self.clean_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.clean_toggle_btn.setStyleSheet(
                "QPushButton {"
                "  text-align: left;"
                "  color: rgba(255, 255, 255, 0.74);"
                "  background-color: transparent;"
                "  border: none;"
                "  padding: 2px 0px;"
                "  font-size: 8.5pt;"
                "}"
                "QPushButton:hover { color: #ffffff; }"
            )
            self.layout.addWidget(self.clean_toggle_btn)

            self.clean_container = self._build_exe_list_widget(clean_items, max_height=150)
            self.clean_container.setVisible(False)
            self.layout.addWidget(self.clean_container)

            self.clean_toggle_btn.toggled.connect(
                lambda checked, n=count: self._toggle_clean_list(checked, n)
            )

    def _toggle_clean_list(self, expanded: bool, count: int) -> None:
        if self.clean_container is not None:
            self.clean_container.setVisible(expanded)
        if self.clean_toggle_btn is not None:
            prefix = "▾ Hide" if expanded else "▸ Show"
            self.clean_toggle_btn.setText(f"{prefix} clean executables ({count})")
        self._shrink_to_fit()

    def _build_exe_list_widget(self, items: List[Dict[str, Any]], max_height: int) -> QWidget:
        """Build a tight executable list container, scrolling only when items > 3."""
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)

        for entry in items:
            col.addWidget(self._build_exe_row(entry))

        if len(items) <= 3:
            container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            return container

        col.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setWidget(container)
        scroll.setMaximumHeight(max_height)
        return scroll

    def _build_exe_row(self, entry: Dict[str, Any]) -> QFrame:
        status = entry.get("status", "clean")
        if status == "fixed":
            badge_text, badge_color, badge_bg = "FIXED", "#4ADE80", "rgba(74, 222, 128, 0.14)"
        elif status == "failed":
            badge_text, badge_color, badge_bg = "FAILED", "#F87171", "rgba(248, 113, 113, 0.15)"
        else:
            badge_text, badge_color, badge_bg = "CLEAN", "#93C5FD", "rgba(147, 197, 253, 0.12)"

        row = QFrame()
        row.setStyleSheet(
            "QFrame {"
            "  background-color: rgba(255, 255, 255, 0.035);"
            "  border: 1px solid rgba(255, 255, 255, 0.08);"
            "  border-radius: 7px;"
            "}"
        )
        vbox = QVBoxLayout(row)
        vbox.setContentsMargins(10, 7, 10, 7)
        vbox.setSpacing(3)

        # Single top line: [STATUS] + file path + size
        top_line = QHBoxLayout()
        top_line.setContentsMargins(0, 0, 0, 0)
        top_line.setSpacing(8)

        badge = QLabel(badge_text)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedWidth(52)
        badge.setFixedHeight(19)
        badge.setStyleSheet(
            f"color: {badge_color}; background-color: {badge_bg}; "
            f"border: 1px solid {badge_color}; border-radius: 4px; "
            "font-size: 7pt; font-weight: 700;"
        )
        top_line.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)

        rel_path = entry.get("rel_path") or entry.get("name") or "executable.exe"
        name_lbl = QLabel(rel_path)
        name_lbl.setToolTip(entry.get("path") or rel_path)
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        name_lbl.setStyleSheet(
            "font-size: 9.5pt; font-weight: 600; color: #f3f4f6; border: none; background: transparent;"
        )
        top_line.addWidget(name_lbl, 1)

        size_str = _format_size(int(entry.get("size") or 0))
        if size_str:
            size_lbl = QLabel(size_str)
            size_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            size_lbl.setStyleSheet(
                "font-size: 8.5pt; color: rgba(255, 255, 255, 0.55); border: none; background: transparent;"
            )
            top_line.addWidget(size_lbl, 0)

        vbox.addLayout(top_line)

        detail_text, detail_color = self._format_entry_detail(entry)
        if detail_text:
            detail_lbl = QLabel(detail_text)
            detail_lbl.setWordWrap(True)
            detail_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            detail_lbl.setStyleSheet(
                f"font-size: 8.5pt; font-style: italic; color: {detail_color}; "
                "border: none; background: transparent; padding-left: 60px;"
            )
            vbox.addWidget(detail_lbl)

        return row

    @staticmethod
    def _format_entry_detail(entry: Dict[str, Any]) -> Tuple[str, str]:
        status = entry.get("status", "clean")
        if status == "fixed":
            parts = [entry.get("variant") or "SteamStub DRM removed"]
            backup_path = entry.get("backup_path")
            if backup_path:
                parts.append(f"Backup: {os.path.basename(backup_path)}")
            return " • ".join(parts), "rgba(255, 255, 255, 0.66)"
        if status == "failed":
            err = entry.get("error") or "Unpacking failed"
            return err, "#FCA5A5"
        return "No SteamStub DRM detected (code 1)", "rgba(255, 255, 255, 0.55)"

    def _create_footer(self) -> None:
        if self._logs:
            self.log_view = QPlainTextEdit("\n".join(self._logs))
            self.log_view.setReadOnly(True)
            self.log_view.setVisible(False)
            self.log_view.setMaximumHeight(120)
            self.log_view.setStyleSheet(
                "QPlainTextEdit {"
                "  background-color: rgba(0, 0, 0, 0.35);"
                "  color: rgba(255, 255, 255, 0.78);"
                "  border: 1px solid rgba(255, 255, 255, 0.10);"
                "  border-radius: 6px;"
                "  font-family: monospace;"
                "  font-size: 8pt;"
                "  padding: 6px;"
                "}"
            )
            self.layout.addWidget(self.log_view)

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 2, 0, 0)
        footer_row.setSpacing(10)

        if self.log_view is not None:
            self.log_toggle_btn = QPushButton("▸ Show raw log")
            self.log_toggle_btn.setCheckable(True)
            self.log_toggle_btn.setChecked(False)
            self.log_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.log_toggle_btn.setStyleSheet(
                "QPushButton {"
                "  color: rgba(255, 255, 255, 0.60);"
                "  background: transparent;"
                "  border: none;"
                "  font-size: 8.5pt;"
                "  padding: 4px 2px;"
                "}"
                "QPushButton:hover { color: rgba(255, 255, 255, 0.90); }"
            )
            self.log_toggle_btn.toggled.connect(self._toggle_raw_log)
            footer_row.addWidget(self.log_toggle_btn)

        footer_row.addStretch(1)

        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.setMinimumWidth(88)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {self._accent};"
            "  color: #0b111b;"
            "  border: none;"
            "  border-radius: 6px;"
            "  padding: 6px 18px;"
            "  font-size: 9pt;"
            "  font-weight: 700;"
            "}"
            "QPushButton:hover {"
            "  background-color: #ffffff;"
            "}"
        )
        ok_btn.clicked.connect(self.accept)
        footer_row.addWidget(ok_btn)

        self.layout.addLayout(footer_row)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        from ui.dialogs.dialog_raiser import focus_dialog_on_show

        QTimer.singleShot(0, lambda: focus_dialog_on_show(self))

    def _toggle_raw_log(self, expanded: bool) -> None:
        if self.log_view is not None:
            self.log_view.setVisible(expanded)
        if self.log_toggle_btn is not None:
            self.log_toggle_btn.setText("▾ Hide raw log" if expanded else "▸ Show raw log")
        self._shrink_to_fit()
