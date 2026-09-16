import logging
import os
import socket
import subprocess
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QPushButton,
    QMessageBox,
)

from utils.helpers import create_checkbox_setting

logger = logging.getLogger(__name__)


def create_webui_tab(dialog) -> QWidget:
    """Create the WebUI settings tab."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(15, 15, 15, 15)

    # Web Server Group
    server_group = QGroupBox("Web Server Configuration")
    server_layout = QVBoxLayout()

    dialog.remote_web_ui_checkbox = create_checkbox_setting(
        "Enable Remote Web UI",
        "enable_remote_web_ui",
        False,
        dialog,
        "Access and queue updates for your game library from a mobile browser on your local network.",
    )
    server_layout.addWidget(dialog.remote_web_ui_checkbox)

    # Port Layout
    port_layout = QHBoxLayout()
    port_label = QLabel("Web UI Port:")
    dialog.web_ui_port_spinbox = QSpinBox()
    dialog.web_ui_port_spinbox.setRange(1024, 65535)
    dialog.web_ui_port_spinbox.setValue(dialog.settings.value("web_ui_port", 8765, type=int))
    dialog.web_ui_port_spinbox.setFixedWidth(100)

    check_port_btn = QPushButton("Check Availability")
    check_port_btn.clicked.connect(lambda: check_port_availability(dialog))

    port_layout.addWidget(port_label)
    port_layout.addWidget(dialog.web_ui_port_spinbox)
    port_layout.addWidget(check_port_btn)
    port_layout.addStretch()
    server_layout.addLayout(port_layout)

    server_group.setLayout(server_layout)
    layout.addWidget(server_group)

    # Background Service Group (Linux only)
    if sys.platform != "win32":
        service_group = QGroupBox("Background Service (systemd)")
        service_layout = QVBoxLayout()

        dialog.service_status_label = QLabel("Background Service: Checking...")
        dialog.service_boot_label = QLabel("Start on Boot: Checking...")
        service_layout.addWidget(dialog.service_status_label)
        service_layout.addWidget(dialog.service_boot_label)

        # Control buttons
        control_layout = QHBoxLayout()
        dialog.start_service_btn = QPushButton("Start Service")
        dialog.start_service_btn.clicked.connect(lambda: start_service(dialog))
        dialog.stop_service_btn = QPushButton("Stop Service")
        dialog.stop_service_btn.clicked.connect(lambda: stop_service(dialog))
        control_layout.addWidget(dialog.start_service_btn)
        control_layout.addWidget(dialog.stop_service_btn)
        service_layout.addLayout(control_layout)

        # Boot buttons
        boot_layout = QHBoxLayout()
        dialog.enable_boot_btn = QPushButton("Enable on Boot")
        dialog.enable_boot_btn.clicked.connect(lambda: enable_boot(dialog))
        dialog.disable_boot_btn = QPushButton("Disable on Boot")
        dialog.disable_boot_btn.clicked.connect(lambda: disable_boot(dialog))
        boot_layout.addWidget(dialog.enable_boot_btn)
        boot_layout.addWidget(dialog.disable_boot_btn)
        service_layout.addLayout(boot_layout)

        service_group.setLayout(service_layout)
        layout.addWidget(service_group)

        dialog.service_poll_timer = QTimer(dialog)
        dialog.service_poll_timer.timeout.connect(lambda: update_service_status(dialog))
        dialog.service_poll_timer.start(2000)
        update_service_status(dialog)

    layout.addStretch()
    dialog.tab_widget.addTab(tab, "WebUI")
    return tab


def check_port_availability(dialog) -> None:
    port = dialog.web_ui_port_spinbox.value()

    is_our_running_port = False
    if dialog.main_window and hasattr(dialog.main_window, "web_server_manager"):
        if dialog.main_window.web_server_manager.is_running():
            if dialog.main_window.web_server_manager.server.port == port:
                is_our_running_port = True

    if is_our_running_port:
        QMessageBox.information(
            dialog,
            "Port Check",
            f"Port {port} is currently in use by this instance of ASSella (Active)."
        )
        return

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.close()
        QMessageBox.information(
            dialog,
            "Port Check",
            f"Port {port} is free and available!"
        )
    except OSError:
        QMessageBox.warning(
            dialog,
            "Port Check",
            f"Port {port} is already in use by another application or the background service."
        )


def get_service_status() -> str:
    try:
        service_path = os.path.expanduser("~/.config/systemd/user/assella-testing.service")
        if not os.path.exists(service_path):
            return "not_installed"

        res = subprocess.run(
            ["systemctl", "--user", "is-active", "assella-testing.service"],
            capture_output=True,
            text=True,
        )
        status = res.stdout.strip()
        if status == "active":
            return "running"
        else:
            return "stopped"
    except Exception as e:
        logger.error(f"Error checking service status: {e}")
        return "unknown"


def is_service_enabled() -> bool:
    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-enabled", "assella-testing.service"],
            capture_output=True,
            text=True,
        )
        return res.stdout.strip() == "enabled"
    except Exception:
        return False


def update_service_status(dialog) -> None:
    if sys.platform == "win32":
        return

    status = get_service_status()
    enabled = is_service_enabled()

    if status == "running":
        dialog.service_status_label.setText("Background Service: <font color='#44cc44'>Active (Running)</font>")
        dialog.start_service_btn.setEnabled(False)
        dialog.stop_service_btn.setEnabled(True)
    elif status == "stopped":
        dialog.service_status_label.setText("Background Service: <font color='#cc4444'>Inactive (Stopped)</font>")
        dialog.start_service_btn.setEnabled(True)
        dialog.stop_service_btn.setEnabled(False)
    elif status == "not_installed":
        dialog.service_status_label.setText("Background Service: <font color='#888888'>Not Configured / Installed</font>")
        dialog.start_service_btn.setEnabled(False)
        dialog.stop_service_btn.setEnabled(False)
    else:
        dialog.service_status_label.setText("Background Service: Unknown Status")
        dialog.start_service_btn.setEnabled(False)
        dialog.stop_service_btn.setEnabled(False)

    if status != "not_installed":
        dialog.enable_boot_btn.setEnabled(not enabled)
        dialog.disable_boot_btn.setEnabled(enabled)
        boot_text = "Enabled" if enabled else "Disabled"
        dialog.service_boot_label.setText(f"Start on Boot: <b>{boot_text}</b>")
    else:
        dialog.enable_boot_btn.setEnabled(False)
        dialog.disable_boot_btn.setEnabled(False)
        dialog.service_boot_label.setText("Start on Boot: N/A")

    if dialog.main_window and hasattr(dialog.main_window, "_update_web_ui_status_label"):
        dialog.main_window._update_web_ui_status_label()


def start_service(dialog) -> None:
    try:
        subprocess.run(["systemctl", "--user", "start", "assella-testing.service"])
        update_service_status(dialog)
    except Exception as e:
        QMessageBox.critical(dialog, "Service Error", f"Failed to start service: {e}")


def stop_service(dialog) -> None:
    try:
        subprocess.run(["systemctl", "--user", "stop", "assella-testing.service"])
        update_service_status(dialog)
    except Exception as e:
        QMessageBox.critical(dialog, "Service Error", f"Failed to stop service: {e}")


def enable_boot(dialog) -> None:
    try:
        subprocess.run(["systemctl", "--user", "enable", "assella-testing.service"])
        update_service_status(dialog)
    except Exception as e:
        QMessageBox.critical(dialog, "Service Error", f"Failed to enable service on boot: {e}")


def disable_boot(dialog) -> None:
    try:
        subprocess.run(["systemctl", "--user", "disable", "assella-testing.service"])
        update_service_status(dialog)
    except Exception as e:
        QMessageBox.critical(dialog, "Service Error", f"Failed to disable service on boot: {e}")
