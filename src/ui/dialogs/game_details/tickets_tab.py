"""
Tickets tab implementation for GameDetailsDialogV2.
Handles drag & drop import, manual paste, verification, export, and deletion of SLSsteam ownership tickets.
"""

import logging
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QScrollArea,
    QMessageBox,
    QApplication,
    QFileDialog,
)

from utils.ticket_manager import (
    get_ticket_status,
    verify_ticket_activation,
    import_ticket,
    validate_ticket_file,
    validate_ticket_content,
    export_ticket,
    remove_ticket,
)

logger = logging.getLogger(__name__)


def init_tickets_tab(dialog) -> None:
    """Initialize the Tickets Management tab with drag & drop import, export, and status."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

    tick_widget = QWidget()
    tick_widget.setAcceptDrops(True)

    def _drag_enter(event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def _drop_event(event):
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            handle_ticket_file_import(dialog, file_path)
        elif event.mimeData().hasText():
            text = event.mimeData().text()
            handle_ticket_text_import(dialog, text)

    tick_widget.dragEnterEvent = _drag_enter
    tick_widget.dropEvent = _drop_event

    layout = QVBoxLayout(tick_widget)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(12)

    # Header Title
    title_lbl = QLabel(f"SLSsteam Ownership Tickets ({dialog.appid})")
    title_lbl.setStyleSheet(f"font-size: 11pt; font-weight: bold; color: {dialog.accent_color};")
    layout.addWidget(title_lbl)

    # Drag & Drop Zone Frame
    drop_zone = QFrame()
    drop_zone.setObjectName("dropZone")
    drop_zone.setStyleSheet(f"""
        QFrame#dropZone {{
            background: rgba(255, 255, 255, 0.03);
            border: 2px dashed rgba(255, 255, 255, 0.15);
            border-radius: 10px;
            padding: 16px;
        }}
        QFrame#dropZone:hover {{
            border-color: {dialog.accent_color};
            background: rgba(255, 255, 255, 0.05);
        }}
    """)
    drop_lay = QVBoxLayout(drop_zone)
    drop_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
    drop_lay.setSpacing(6)

    drop_icon = QLabel("Drag & Drop Ticket File (.yaml) Here")
    drop_icon.setStyleSheet(f"color: {dialog.accent_color}; font-size: 10.5pt; font-weight: bold;")
    drop_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    drop_lay.addWidget(drop_icon)

    drop_sub = QLabel("Or click Browse File / Paste raw base64 payload below")
    drop_sub.setStyleSheet("color: rgba(255, 255, 255, 0.5); font-size: 8.5pt;")
    drop_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
    drop_lay.addWidget(drop_sub)

    layout.addWidget(drop_zone)

    # Status & Inspection Box
    t_status = get_ticket_status(dialog.appid)
    v_status = verify_ticket_activation(dialog.appid)

    status_box = QFrame()
    status_box.setStyleSheet("background: rgba(255, 255, 255, 0.04); border-radius: 8px; padding: 12px;")
    status_lay = QVBoxLayout(status_box)
    status_lay.setSpacing(6)

    if t_status["exists"]:
        if v_status.get("sls_active"):
            st_color = "#4CAF50"
            st_badge = "Active (Verified in SLSsteam)"
        elif v_status.get("base64_valid"):
            st_color = "#FFC107"
            st_badge = "Installed (Ready for SLSsteam)"
        else:
            st_color = "#FF9800"
            st_badge = "Installed (Invalid Format)"

        st_text = f"<b>Status:</b> <span style='color: {st_color}; font-weight: bold;'>{st_badge}</span>"
        if t_status.get("steam_id"):
            raw_sid = str(t_status["steam_id"]).strip()
            if len(raw_sid) > 6:
                blurred_sid = raw_sid[:4] + "••••••••" + raw_sid[-2:]
            else:
                blurred_sid = "••••••••••••"
            st_text += f"<br><b>Holder SteamID:</b> <span style='font-family: monospace; color: rgba(255, 255, 255, 0.7);'>{blurred_sid}</span>"
        if t_status.get("updated_at"):
            st_text += f"<br><b>Last Updated:</b> <span style='color: rgba(255, 255, 255, 0.7);'>{t_status['updated_at']}</span>"
    else:
        st_text = "<b>Status:</b> <span style='color: #ff8a7a;'>No Ticket file installed</span>"

    status_lbl = QLabel(st_text)
    status_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; line-height: 1.4;")
    status_lay.addWidget(status_lbl)

    layout.addWidget(status_box)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)

    btn_verify = QPushButton("Verify Ticket Status")
    btn_verify.setFixedHeight(30)
    btn_verify.setStyleSheet(
        "font-weight: bold; background: rgba(255, 255, 255, 0.1); color: #FFFFFF; border-radius: 5px;"
    )
    btn_verify.clicked.connect(lambda: verify_ticket_status_dialog(dialog))
    btn_row.addWidget(btn_verify)

    if t_status["exists"]:
        btn_delete = QPushButton("Delete Ticket")
        btn_delete.setFixedHeight(30)
        btn_delete.setStyleSheet(
            "background: rgba(220, 50, 40, 0.2); color: #ff8a7a; border: 1px solid rgba(220, 50, 40, 0.4); border-radius: 5px;"
        )
        btn_delete.clicked.connect(lambda: delete_installed_ticket(dialog))
        btn_row.addWidget(btn_delete)

    layout.addLayout(btn_row)
    layout.addStretch()

    scroll.setWidget(tick_widget)
    dialog.stacked.addWidget(scroll)


def handle_ticket_file_import(dialog, file_path: str) -> None:
    """Import and validate ticket YAML file."""
    val = validate_ticket_file(file_path, dialog.appid)
    if not val.get("valid"):
        QMessageBox.warning(dialog, "Invalid Ticket File", f"Sanitation check failed:\n{val.get('error')}")
        return

    ok, msg = import_ticket(file_path, dialog.appid)
    if ok:
        QMessageBox.information(dialog, "Ticket Imported", f"✓ {msg}")
        dialog._switch_tab(getattr(dialog, "_tickets_tab_index", 4))
    else:
        QMessageBox.critical(dialog, "Import Failed", msg)


def handle_ticket_text_import(dialog, raw_text: str) -> None:
    """Import and validate raw base64 or yaml text."""
    val = validate_ticket_content(raw_text, dialog.appid)
    if not val.get("valid"):
        QMessageBox.warning(dialog, "Invalid Ticket Payload", f"Sanitation check failed:\n{val.get('error')}")
        return

    ok, msg = import_ticket(raw_text, dialog.appid)
    if ok:
        QMessageBox.information(dialog, "Ticket Imported", f"✓ {msg}")
        dialog._switch_tab(getattr(dialog, "_tickets_tab_index", 4))
    else:
        QMessageBox.critical(dialog, "Import Failed", msg)


def verify_ticket_status_dialog(dialog) -> None:
    """Show detailed status message regarding ticket installation and SLSsteam status."""
    res = verify_ticket_activation(dialog.appid)
    msg = f"Ticket Verification for AppID {dialog.appid}:\n\n"
    msg += f"• File Installed: {'Yes' if res['installed'] else 'No'}\n"
    msg += f"• Payload Valid: {'Yes' if res['base64_valid'] else 'No'}\n"
    msg += f"• Active in SLSsteam: {'Yes' if res['sls_active'] else 'No'}\n\n"
    msg += f"Status: {res['message']}"

    if res["working"]:
        QMessageBox.information(dialog, "Ticket Verified Working", msg)
    elif res["installed"] and res["base64_valid"]:
        QMessageBox.warning(dialog, "Ticket Installed (Pending Launch)", msg)
    else:
        QMessageBox.critical(dialog, "Ticket Issue Detected", msg)


def paste_and_import_ticket(dialog) -> None:
    """Paste ticket payload from clipboard and import."""
    clipboard_text = QApplication.clipboard().text()
    if not clipboard_text:
        QMessageBox.warning(dialog, "Clipboard Empty", "No text found on clipboard to import.")
        return
    handle_ticket_text_import(dialog, clipboard_text)


def export_installed_ticket(dialog) -> None:
    """Export installed ticket YAML to disk."""
    save_path, _ = QFileDialog.getSaveFileName(
        dialog, "Export Ticket File", f"ticket_{dialog.appid}.yaml", "YAML Files (*.yaml)"
    )
    if save_path:
        ok, msg = export_ticket(dialog.appid, save_path)
        if ok:
            QMessageBox.information(dialog, "Export Success", f"✓ {msg}")
        else:
            QMessageBox.critical(dialog, "Export Failed", msg)


def delete_installed_ticket(dialog) -> None:
    """Delete ticket file for this app."""
    ans = QMessageBox.question(
        dialog,
        "Delete Ticket",
        f"Are you sure you want to remove ticket files for AppID {dialog.appid}?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if ans == QMessageBox.StandardButton.Yes:
        ok, msg = remove_ticket(dialog.appid)
        if ok:
            QMessageBox.information(dialog, "Ticket Removed", f"✓ {msg}")
            dialog._switch_tab(getattr(dialog, "_tickets_tab_index", 4))
        else:
            QMessageBox.critical(dialog, "Removal Failed", msg)
