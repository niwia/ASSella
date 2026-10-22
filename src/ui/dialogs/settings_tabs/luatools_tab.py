"""
luatools_tab.py - Settings tab for lua.tools authentication and account integration.
Supports 1-click automatic PKCE browser login and 6-character code fallback.
"""

import logging
import webbrowser
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QFrame,
)
from ui.dialogs.themed_message_box import ThemedMessageBox

from core.lua_tools_api import LuaToolsAPI
from utils.task_runner import TaskRunner

logger = logging.getLogger(__name__)

NORMAL_BTN_STYLE = """
    QPushButton {
        background-color: rgba(255, 255, 255, 0.08);
        color: #FFFFFF;
        border: 1px solid rgba(255, 255, 255, 0.18);
        border-radius: 6px;
        padding: 8px 16px;
        font-size: 9pt;
        font-weight: 500;
    }
    QPushButton:hover {
        background-color: rgba(255, 255, 255, 0.16);
        border: 1px solid rgba(255, 255, 255, 0.35);
    }
    QPushButton:disabled {
        background-color: rgba(255, 255, 255, 0.03);
        color: rgba(255, 255, 255, 0.25);
        border-color: rgba(255, 255, 255, 0.06);
    }
"""

PRIMARY_BTN_STYLE = """
    QPushButton {
        background-color: #3b82f6;
        color: #FFFFFF;
        border: 1px solid #2563eb;
        border-radius: 6px;
        padding: 8px 16px;
        font-size: 9pt;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: #2563eb;
    }
    QPushButton:disabled {
        background-color: rgba(59, 130, 246, 0.3);
        color: rgba(255, 255, 255, 0.4);
    }
"""

DANGER_BTN_STYLE = """
    QPushButton {
        background-color: rgba(239, 68, 68, 0.15);
        color: #f87171;
        border: 1px solid rgba(239, 68, 68, 0.3);
        border-radius: 6px;
        padding: 6px 14px;
        font-size: 8.5pt;
        font-weight: 500;
    }
    QPushButton:hover {
        background-color: rgba(239, 68, 68, 0.25);
        border-color: rgba(239, 68, 68, 0.5);
    }
"""


def create_luatools_tab(dialog) -> QWidget:
    """Create the LuaTools Settings Tab."""
    tab = QWidget()
    main_lay = QVBoxLayout(tab)
    main_lay.setContentsMargins(15, 15, 15, 15)
    main_lay.setSpacing(12)

    card, card_lay = dialog._create_card_frame("lua.tools Integration")
    card.setStyleSheet("""
        QFrame#SectionCard {
            background-color: rgba(255, 255, 255, 0.03);
            border: none;
            border-radius: 10px;
        }
        QFrame#SectionCard > QLabel {
            border: none !important;
            background: transparent !important;
            padding: 0px !important;
        }
    """)

    intro_lbl = QLabel(
        "Connect your lua.tools account to download community fixes, online multiplayer bypasses, "
        "and patches directly from the LuaFix game details tab."
    )
    intro_lbl.setWordWrap(True)
    intro_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9pt; line-height: 1.4; border: none; background: transparent;")
    card_lay.addWidget(intro_lbl)

    card_lay.addSpacing(6)

    # Status box (borderless)
    status_frame = QFrame()
    status_frame.setStyleSheet(
        "background: rgba(255, 255, 255, 0.04); border: none; border-radius: 8px;"
    )
    status_lay = QHBoxLayout(status_frame)
    status_lay.setContentsMargins(14, 12, 14, 12)

    dialog.luatools_status_dot = QLabel("●")
    dialog.luatools_status_dot.setStyleSheet("color: #ef4444; font-size: 14pt; border: none; background: transparent;")
    status_lay.addWidget(dialog.luatools_status_dot)

    dialog.luatools_status_text = QLabel("Checking status...")
    dialog.luatools_status_text.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    status_lay.addWidget(dialog.luatools_status_text)
    status_lay.addStretch()

    dialog.luatools_disconnect_btn = QPushButton("Disconnect")
    dialog.luatools_disconnect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.luatools_disconnect_btn.setStyleSheet(DANGER_BTN_STYLE)
    dialog.luatools_disconnect_btn.setVisible(False)
    status_lay.addWidget(dialog.luatools_disconnect_btn)

    card_lay.addWidget(status_frame)

    # Option: Ignore Online Fixes
    card_lay.addSpacing(4)
    from utils.helpers import create_checkbox_setting
    dialog.ignore_online_fixes_checkbox = create_checkbox_setting(
        "Ignore Online Fixes",
        "luatools_ignore_online_fixes",
        False,
        dialog,
        "Hide fixes that only contain online multiplayer patches. If a game has only online fixes, the LuaFix tab will remain hidden.",
    )
    card_lay.addWidget(dialog.ignore_online_fixes_checkbox)

    # Login methods container
    dialog.luatools_login_container = QWidget()
    dialog.luatools_login_container.setStyleSheet("background: transparent; border: none;")
    login_lay = QVBoxLayout(dialog.luatools_login_container)
    login_lay.setContentsMargins(0, 8, 0, 0)
    login_lay.setSpacing(10)


    # 1. 1-Click Browser Login
    browser_title = QLabel("1-Click Browser Login")
    browser_title.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 600;")
    login_lay.addWidget(browser_title)

    browser_desc = QLabel(
        "Opens your default browser to authorize via Discord. ASSella captures the login callback automatically."
    )
    browser_desc.setWordWrap(True)
    browser_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt;")
    login_lay.addWidget(browser_desc)

    browser_btn_row = QHBoxLayout()
    dialog.luatools_browser_btn = QPushButton("Log In with Browser")
    dialog.luatools_browser_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.luatools_browser_btn.setStyleSheet(PRIMARY_BTN_STYLE)
    browser_btn_row.addWidget(dialog.luatools_browser_btn)

    dialog.luatools_cancel_pkce_btn = QPushButton("Cancel")
    dialog.luatools_cancel_pkce_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.luatools_cancel_pkce_btn.setStyleSheet(NORMAL_BTN_STYLE)
    dialog.luatools_cancel_pkce_btn.setVisible(False)
    browser_btn_row.addWidget(dialog.luatools_cancel_pkce_btn)

    browser_btn_row.addStretch()
    login_lay.addLayout(browser_btn_row)

    dialog.luatools_pkce_status = QLabel("")
    dialog.luatools_pkce_status.setStyleSheet("color: #3b82f6; font-size: 8.5pt;")
    dialog.luatools_pkce_status.setVisible(False)
    login_lay.addWidget(dialog.luatools_pkce_status)

    login_lay.addSpacing(6)

    # Divider
    divider = QFrame()
    divider.setFrameShape(QFrame.Shape.HLine)
    divider.setStyleSheet("background: rgba(255, 255, 255, 0.08); max-height: 1px;")
    login_lay.addWidget(divider)

    login_lay.addSpacing(6)

    # 2. 6-Character Redeem Code
    code_title = QLabel("Or Enter 6-Character Login Code")
    code_title.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 600;")
    login_lay.addWidget(code_title)

    code_desc = QLabel(
        "If you are already logged in to lua.tools on another device or browser, enter your 6-character connect code:"
    )
    code_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt;")
    login_lay.addWidget(code_desc)

    code_row = QHBoxLayout()
    dialog.luatools_code_input = QLineEdit()
    dialog.luatools_code_input.setPlaceholderText("e.g. A7X9Q2")
    dialog.luatools_code_input.setMaxLength(6)
    dialog.luatools_code_input.setFixedWidth(140)
    dialog.luatools_code_input.setStyleSheet("""
        QLineEdit {
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            color: #FFFFFF;
            font-size: 11pt;
            font-weight: bold;
            letter-spacing: 2px;
            padding: 6px 10px;
        }
    """)
    code_row.addWidget(dialog.luatools_code_input)

    dialog.luatools_redeem_btn = QPushButton("Connect Code")
    dialog.luatools_redeem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.luatools_redeem_btn.setStyleSheet(NORMAL_BTN_STYLE)
    code_row.addWidget(dialog.luatools_redeem_btn)
    code_row.addStretch()
    login_lay.addLayout(code_row)

    card_lay.addWidget(dialog.luatools_login_container)

    main_lay.addWidget(card)
    main_lay.addStretch()

    # PKCE Polling Timer
    dialog.luatools_poll_timer = QTimer(dialog)
    dialog.luatools_poll_timer.setInterval(800)

    # Helper function to refresh UI (uses local cache first to avoid blocking GUI thread)
    def _paint_status(status):
        if status.get("authenticated"):
            name = status.get("display_name", "Connected")
            dialog.luatools_status_dot.setStyleSheet("color: #22c55e; font-size: 14pt;")
            dialog.luatools_status_text.setText(f"Connected: {name}")
            dialog.luatools_disconnect_btn.setVisible(True)
            dialog.luatools_login_container.setVisible(False)
            stop_pkce_polling()
        else:
            dialog.luatools_status_dot.setStyleSheet("color: #ef4444; font-size: 14pt;")
            dialog.luatools_status_text.setText("Not connected to lua.tools")
            dialog.luatools_disconnect_btn.setVisible(False)
            dialog.luatools_login_container.setVisible(True)

    # Bumped on every refresh so a slow validation cannot overwrite a newer state.
    status_generation = {"value": 0}

    def update_ui_status(validate: bool = False):
        """Paint the cached account state, optionally validating it in the background.

        `validate` is passed only when the tab is opened, so opening Settings
        costs no network call and the one refresh happens when the account
        status is actually on screen. Both paths return early while LuaTools is
        locked, so a locked install never touches the lua.tools backend.
        """
        if not LuaToolsAPI.is_unlocked():
            return
        status_generation["value"] += 1
        generation = status_generation["value"]
        _paint_status(LuaToolsAPI.get_auth_status(allow_cached=True))
        if not validate:
            return

        dialog.luatools_status_text.setText("Checking account…")
        runner = getattr(dialog, "luatools_status_runner", None)
        if runner is None:
            runner = TaskRunner(dialog)
            dialog.luatools_status_runner = runner

        def _validated(status):
            if generation != status_generation["value"]:
                return
            _paint_status(status)

        def _validation_failed(error):
            if generation != status_generation["value"]:
                return
            logger.warning(f"[LuaTools] Could not validate the session: {error}")
            _paint_status(LuaToolsAPI.get_auth_status(allow_cached=True))

        runner.run(
            LuaToolsAPI.get_auth_status,
            False,
            on_finished=_validated,
            on_error=_validation_failed,
        )

    def start_browser_login():
        ok, auth_url = LuaToolsAPI.start_pkce_login()
        if not ok:
            ThemedMessageBox.critical(dialog, "Login Error", auth_url)
            return

        dialog.luatools_browser_btn.setEnabled(False)
        dialog.luatools_cancel_pkce_btn.setVisible(True)
        dialog.luatools_pkce_status.setText("Opening browser... Waiting for authorization...")
        dialog.luatools_pkce_status.setVisible(True)

        # Open browser
        try:
            QDesktopServices.openUrl(QUrl(auth_url))
        except Exception:
            webbrowser.open(auth_url)

        dialog.luatools_poll_timer.start()

    def poll_pkce():
        res = LuaToolsAPI.poll_pkce_login()
        status = res.get("status")
        if status == "success":
            stop_pkce_polling()
            update_ui_status()
            ThemedMessageBox.information(dialog, "Success", "Successfully connected to lua.tools!")
        elif status == "error":
            stop_pkce_polling()
            ThemedMessageBox.warning(dialog, "Login Failed", res.get("error", "Unknown error during authorization."))
        elif status == "timeout":
            stop_pkce_polling()
            ThemedMessageBox.warning(dialog, "Login Timeout", "Authorization timed out. Please try again.")

    def stop_pkce_polling():
        dialog.luatools_poll_timer.stop()
        LuaToolsAPI.cancel_pkce()
        dialog.luatools_browser_btn.setEnabled(True)
        dialog.luatools_cancel_pkce_btn.setVisible(False)
        dialog.luatools_pkce_status.setVisible(False)

    def redeem_code():
        code = dialog.luatools_code_input.text().strip().upper()
        if len(code) != 6:
            ThemedMessageBox.warning(dialog, "Code Required", "Please enter a valid 6-character login code.")
            return

        dialog.luatools_redeem_btn.setEnabled(False)
        dialog.luatools_redeem_btn.setText("Connecting...")
        try:
            ok, msg = LuaToolsAPI.sign_in_with_code(code)
            if ok:
                dialog.luatools_code_input.clear()
                update_ui_status()
                ThemedMessageBox.information(dialog, "Connected", msg)
            else:
                ThemedMessageBox.warning(dialog, "Connection Failed", msg)
        finally:
            dialog.luatools_redeem_btn.setEnabled(True)
            dialog.luatools_redeem_btn.setText("Connect Code")

    def disconnect_account():
        reply = ThemedMessageBox.question(
            dialog,
            "Disconnect",
            "Are you sure you want to log out from lua.tools?",
            ThemedMessageBox.StandardButton.Yes | ThemedMessageBox.StandardButton.No,
            ThemedMessageBox.StandardButton.No,
        )
        if reply == ThemedMessageBox.StandardButton.Yes:
            LuaToolsAPI.clear_session()
            update_ui_status()

    # Wire signals
    dialog.luatools_browser_btn.clicked.connect(start_browser_login)
    dialog.luatools_cancel_pkce_btn.clicked.connect(stop_pkce_polling)
    dialog.luatools_poll_timer.timeout.connect(poll_pkce)
    dialog.luatools_redeem_btn.clicked.connect(redeem_code)
    dialog.luatools_disconnect_btn.clicked.connect(disconnect_account)

    # Attach handler for lazy invocation when the LuaTools tab is accessed
    dialog.update_luatools_ui_status = update_ui_status

    return tab
