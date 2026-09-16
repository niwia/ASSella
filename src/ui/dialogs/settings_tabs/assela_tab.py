import logging
import os
import shutil
import subprocess
import sys
import threading

from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QSlider,
    QMessageBox,
)

from utils.helpers import create_checkbox_setting

logger = logging.getLogger(__name__)


def create_assela_tab(dialog) -> QWidget:
    """Create the ASSella settings tab."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(24)

    # Section 1 Card: ASSella Settings
    assella_card, assella_lay = dialog._create_card_frame("ASSella Settings")

    # 1. Smart Selection
    dialog.smart_depot_selection_checkbox = create_checkbox_setting(
        "Smart Selection",
        "smart_depot_selection",
        False,
        dialog,
        "Automatically reuse previously chosen depots on update, unless a brand new depot is added.",
    )
    assella_lay.addWidget(dialog.smart_depot_selection_checkbox)

    # 2. Check Updates on Boot
    dialog.check_updates_on_boot_checkbox = create_checkbox_setting(
        "Check Updates on Boot",
        "check_updates_on_boot",
        True,
        dialog,
        "Automatically check for game updates in the background on startup.",
    )
    assella_lay.addWidget(dialog.check_updates_on_boot_checkbox)

    # 3. ISP Bypass & Hubcap Gateway Selector
    isp_group = QVBoxLayout()
    isp_group.setSpacing(8)
    isp_group.setContentsMargins(0, 2, 0, 2)

    isp_row = QHBoxLayout()
    isp_row.setContentsMargins(0, 0, 0, 0)
    isp_lbl = QLabel("Hubcap Gateway:")
    isp_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    isp_lbl.setToolTip("Select how ASSella routes requests to Hubcap API (Auto Smart Fallback, Direct, DoH, Tor, or Wire).")
    isp_row.addWidget(isp_lbl)
    isp_row.addStretch()

    dialog.isp_gateway_combo = QComboBox()
    dialog.isp_gateway_combo.addItem("Auto", "auto")
    dialog.isp_gateway_combo.addItem("Direct", "direct")
    dialog.isp_gateway_combo.addItem("DoH", "doh")
    dialog.isp_gateway_combo.addItem("Tor", "tor")
    dialog.isp_gateway_combo.addItem("Wire", "wirecutter")
    dialog.isp_gateway_combo.setFixedWidth(115)

    # Load saved mode
    saved_mode = dialog.settings.value("isp_bypass_mode", "auto", type=str)
    if not saved_mode:
        saved_mode = "auto"

    idx = dialog.isp_gateway_combo.findData(saved_mode)
    if idx >= 0:
        dialog.isp_gateway_combo.setCurrentIndex(idx)
    else:
        dialog.isp_gateway_combo.setCurrentIndex(0)

    dialog.isp_gateway_combo.currentIndexChanged.connect(
        lambda idx: on_isp_gateway_changed(dialog, idx)
    )
    isp_row.addWidget(dialog.isp_gateway_combo)
    isp_group.addLayout(isp_row)

    # 4-Button Gateway Health Tester Row
    test_bar_box = QVBoxLayout()
    test_bar_box.setSpacing(4)
    test_bar_box.setContentsMargins(0, 2, 0, 0)

    test_bar_lbl = QLabel("Hubcap Gateway Health Check:")
    test_bar_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; border: none; background: transparent;")
    test_bar_box.addWidget(test_bar_lbl)

    dialog.gateway_btn_row = QHBoxLayout()
    dialog.gateway_btn_row.setSpacing(8)

    base_btn_css = """
        QPushButton {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 6px;
            padding: 5px 8px;
            color: #e0e0e0;
            font-size: 8.5pt;
            font-weight: 500;
            min-height: 26px;
        }
        QPushButton:hover {
            background: rgba(255, 255, 255, 0.15);
            border: 1px solid rgba(255, 255, 255, 0.25);
        }
        QPushButton:disabled {
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.05);
            color: rgba(255, 255, 255, 0.25);
        }
    """

    dialog.test_direct_btn = QPushButton("Direct")
    dialog.test_direct_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.test_direct_btn.setStyleSheet(base_btn_css)
    dialog.test_direct_btn.clicked.connect(lambda: test_single_gateway(dialog, "direct", dialog.test_direct_btn, "Direct"))
    dialog.gateway_btn_row.addWidget(dialog.test_direct_btn)

    dialog.test_doh_btn = QPushButton("DoH")
    dialog.test_doh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.test_doh_btn.setStyleSheet(base_btn_css)
    dialog.test_doh_btn.clicked.connect(lambda: test_single_gateway(dialog, "doh", dialog.test_doh_btn, "DoH"))
    dialog.gateway_btn_row.addWidget(dialog.test_doh_btn)

    dialog.test_tor_btn = QPushButton("Tor")
    dialog.test_tor_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.test_tor_btn.setStyleSheet(base_btn_css)
    dialog.test_tor_btn.clicked.connect(lambda: test_single_gateway(dialog, "tor", dialog.test_tor_btn, "Tor"))
    dialog.gateway_btn_row.addWidget(dialog.test_tor_btn)

    dialog.test_wirecutter_btn = QPushButton("Wirecutter")
    dialog.test_wirecutter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.test_wirecutter_btn.setStyleSheet(base_btn_css)
    dialog.test_wirecutter_btn.clicked.connect(lambda: test_single_gateway(dialog, "wirecutter", dialog.test_wirecutter_btn, "Wirecutter"))
    dialog.gateway_btn_row.addWidget(dialog.test_wirecutter_btn)

    test_bar_box.addLayout(dialog.gateway_btn_row)
    isp_group.addLayout(test_bar_box)
    assella_lay.addLayout(isp_group)

    # Initial button state check
    if saved_mode == "disabled":
        dialog.test_direct_btn.setEnabled(False)
        dialog.test_doh_btn.setEnabled(False)
        dialog.test_tor_btn.setEnabled(False)
        dialog.test_wirecutter_btn.setEnabled(False)

    # 4. Update Check Interval Slider
    slider_layout = QHBoxLayout()
    slider_layout.setContentsMargins(2, 2, 2, 2)
    slider_label = QLabel("Update Check Interval:")
    slider_label.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    slider_label.setToolTip("Set how often to check for game updates. Move to the leftmost position (0) to disable.")

    dialog.update_interval_slider = QSlider(Qt.Orientation.Horizontal)
    dialog.update_interval_slider.setRange(0, 20)
    dialog.update_interval_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
    dialog.update_interval_slider.setTickInterval(1)

    dialog.update_interval_slider.setStyleSheet("""
        QSlider::groove:horizontal {
            border: none;
            height: 6px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 3px;
        }
        QSlider::sub-page:horizontal {
            background: %s;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: %s;
            border: none;
            width: 16px;
            height: 16px;
            margin: -5px 0;
            border-radius: 8px;
        }
        QSlider::handle:horizontal:hover {
            background: white;
        }
    """ % (dialog.accent_color, dialog.accent_color))

    dialog.update_interval_value_label = QLabel("Disabled")
    dialog.update_interval_value_label.setStyleSheet("color: rgba(255, 255, 255, 0.8); font-size: 9pt; font-weight: bold; border: none; background: transparent;")
    dialog.update_interval_value_label.setFixedWidth(75)

    current_minutes = dialog.settings.value("update_check_interval_minutes", 0, type=int)
    slider_val = min(20, max(0, current_minutes // 5))
    dialog.update_interval_slider.setValue(slider_val)

    def update_slider_label(val):
        if val == 0:
            dialog.update_interval_value_label.setText("Disabled")
        else:
            dialog.update_interval_value_label.setText(f"{val * 5} mins")

    update_slider_label(slider_val)
    dialog.update_interval_slider.valueChanged.connect(update_slider_label)

    slider_layout.addWidget(slider_label)
    slider_layout.addWidget(dialog.update_interval_slider, 1)
    slider_layout.addWidget(dialog.update_interval_value_label)
    assella_lay.addLayout(slider_layout)

    # SteamAPI provider selector
    provider_layout = QHBoxLayout()
    provider_layout.setSpacing(12)
    provider_label = QLabel("SteamAPI Provider:")
    provider_label.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500; border: none; background: transparent;")
    provider_label.setToolTip(
        "Select which API service is prioritized for library game update checks.\n\n"
        "• SteamPICS: Queries Valve servers directly. Authoritative, live data with zero stale caching.\n"
        "• SteamcmdAPI: Fast parallel scan via CDN mirror."
    )

    dialog.update_provider_combo = QComboBox()
    dialog.update_provider_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    dialog.update_provider_combo.setStyleSheet("""
        QComboBox {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 6px;
            padding: 4px 12px;
            color: #FFFFFF;
            font-size: 9pt;
            min-width: 130px;
        }
        QComboBox:hover {
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(255, 255, 255, 0.25);
        }
        QComboBox QAbstractItemView {
            background: #1e1e24;
            border: 1px solid rgba(255, 255, 255, 0.15);
            color: #FFFFFF;
            selection-background-color: %s;
        }
    """ % dialog.accent_color)
    dialog.update_provider_combo.addItem("Auto (Hybrid)", "auto")
    dialog.update_provider_combo.addItem("SteamPICS", "steampics")
    dialog.update_provider_combo.addItem("SteamcmdAPI", "steamcmd")
    dialog.update_provider_combo.setToolTip(
        "Auto (Hybrid): Queries SteamCMD and live Steam PICS in tandem with highest-build resolution for maximum speed and zero stale data.\n"
        "SteamPICS: Live connection directly to Valve CM servers.\n"
        "SteamcmdAPI: Stateless HTTP requests via CDN."
    )

    current_provider = dialog.settings.value("update_check_api_provider", "auto", type=str)
    p_idx = dialog.update_provider_combo.findData(current_provider)
    if p_idx >= 0:
        dialog.update_provider_combo.setCurrentIndex(p_idx)

    provider_layout.addWidget(provider_label)
    provider_layout.addStretch(1)
    provider_layout.addWidget(dialog.update_provider_combo)
    assella_lay.addLayout(provider_layout)

    layout.addWidget(assella_card)
    layout.addStretch()

    # ── Uninstall (Linux only) ────────────────────────────────────────
    if sys.platform != "win32":
        uninstall_btn = QPushButton("Uninstall ASSella")
        uninstall_btn.setToolTip("Remove ASSella and optionally restore the original ACCELA.")

        accent_color = dialog.settings.value("accent_color", "#C06C84")
        from utils.color_utils import get_semantic_colors
        sem_colors = get_semantic_colors(accent_color)
        err_color = sem_colors["error"]

        uninstall_btn.setStyleSheet(f"""
            QPushButton {{
                color: {err_color} !important;
                background: transparent;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: rgba(235, 87, 87, 0.12);
                border-color: {err_color};
            }}
        """)
        uninstall_btn.clicked.connect(lambda: uninstall_assela(dialog))
        layout.addWidget(uninstall_btn)

    dialog.tab_widget.addTab(tab, "ASSella")
    return tab


def on_isp_gateway_changed(dialog, index: int) -> None:
    """Saves selected mode immediately and triggers Tor warm boot if needed."""
    if not hasattr(dialog, "isp_gateway_combo") or not dialog.isp_gateway_combo:
        return
    mode = dialog.isp_gateway_combo.currentData() or "auto"
    dialog.settings.setValue("isp_bypass_mode", mode)
    dialog.settings.setValue("isp_bypass_hubcap", True)

    if mode == "tor":
        from utils.isp_bypass import TorManager
        threading.Thread(target=TorManager.start_tor_if_needed, daemon=True).start()


def test_single_gateway(dialog, gateway_key: str, btn: QPushButton, label: str) -> None:
    """Runs an individual gateway health check in a background thread and updates button."""
    btn.setEnabled(False)
    btn.setText("Testing...")
    btn.setStyleSheet("""
        QPushButton {
            background: #3b4261;
            border: 1px solid #e0af68;
            border-radius: 6px;
            padding: 5px 8px;
            color: #e0af68;
            font-size: 8.5pt;
            font-weight: bold;
            min-height: 26px;
        }
    """)

    def _target():
        import utils.isp_bypass as isp
        if gateway_key == "direct":
            ok, status, lat = isp.test_gateway_direct()
        elif gateway_key == "doh":
            ok, status, lat = isp.test_gateway_doh()
        elif gateway_key == "tor":
            ok, status, lat = isp.test_gateway_tor()
        elif gateway_key == "wirecutter":
            ok, status, lat = isp.test_gateway_wirecutter()
        else:
            ok, status, lat = False, "Unknown", 0

        QMetaObject.invokeMethod(
            dialog,
            "_handle_gateway_test_done",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(object, (btn, label, ok, status, lat)),
        )

    threading.Thread(target=_target, daemon=True).start()


def handle_gateway_test_done(dialog, data) -> None:
    btn, label, ok, status, lat = data
    btn.setEnabled(True)
    if ok:
        btn.setText(f"{label}: OK")
        btn.setToolTip(f"{label} Gateway: {status}")
        btn.setStyleSheet("""
            QPushButton {
                background: #1b5e20;
                border: 1px solid #4caf50;
                border-radius: 6px;
                padding: 5px 8px;
                color: #a5d6a7;
                font-size: 8.5pt;
                font-weight: bold;
                min-height: 26px;
            }
            QPushButton:hover {
                background: #2e7d32;
            }
        """)
    else:
        btn.setText(f"{label}: {status}")
        btn.setToolTip(f"{label} Gateway Failed: {status}")
        btn.setStyleSheet("""
            QPushButton {
                background: #b71c1c;
                border: 1px solid #ef5350;
                border-radius: 6px;
                padding: 5px 8px;
                color: #ffcdd2;
                font-size: 8.5pt;
                font-weight: bold;
                min-height: 26px;
            }
            QPushButton:hover {
                background: #c62828;
            }
        """)


def uninstall_assela(dialog) -> None:
    """Remove ASSella and optionally restore the original ACCELA backup."""
    install_dir = os.path.expanduser("~/.local/share/ACCELA")
    assela_path = os.path.join(install_dir, "ASSella.AppImage")
    symlink_path = os.path.join(install_dir, "ACCELA.AppImage")
    backup_path = os.path.join(install_dir, "ACCELA.AppImage.bak")
    desktop_entry = os.path.expanduser("~/.local/share/applications/accela.desktop")
    has_backup = os.path.isfile(backup_path)

    reply = QMessageBox.question(
        dialog,
        "Uninstall ASSella",
        "This will remove ASSella and revert the desktop shortcut to ACCELA.\n\nAre you sure?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return

    restore = False
    if has_backup:
        restore_reply = QMessageBox.question(
            dialog,
            "Restore Original ACCELA?",
            "A backup of the original ACCELA (ACCELA.AppImage.bak) was found.\n\n"
            "Would you like to restore it after uninstalling ASSella?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        restore = restore_reply == QMessageBox.StandardButton.Yes

    errors = []

    if os.path.islink(symlink_path):
        try:
            os.remove(symlink_path)
        except OSError as e:
            errors.append(f"Could not remove symlink: {e}")

    if os.path.isfile(assela_path):
        try:
            os.remove(assela_path)
        except OSError as e:
            errors.append(f"Could not remove ASSella.AppImage: {e}")

    cache_dir = os.path.join(install_dir, "image_cache")
    if os.path.exists(cache_dir):
        try:
            shutil.rmtree(cache_dir)
        except OSError as e:
            errors.append(f"Could not remove image cache directory: {e}")

    if restore:
        try:
            shutil.copy2(backup_path, symlink_path)
            os.chmod(symlink_path, 0o755)
        except OSError as e:
            errors.append(f"Could not restore ACCELA backup: {e}")

    if os.path.isfile(desktop_entry):
        try:
            with open(desktop_entry, "r") as f:
                content = f.read()
            content = content.replace("Name=ASSella", "Name=ACCELA")
            with open(desktop_entry, "w") as f:
                f.write(content)
            try:
                subprocess.run(
                    ["update-desktop-database", os.path.dirname(desktop_entry)],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass
        except OSError as e:
            errors.append(f"Could not update desktop entry: {e}")

    if errors:
        QMessageBox.warning(dialog, "Uninstall — Partial", "\n".join(errors))
    else:
        msg = "ASSella has been uninstalled."
        if restore:
            msg += "\nOriginal ACCELA has been restored."
        QMessageBox.information(dialog, "Done", msg)
