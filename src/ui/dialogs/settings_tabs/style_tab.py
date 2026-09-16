import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QMovie, QPainter
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QColorDialog,
    QMessageBox,
    QApplication,
)

from utils.helpers import create_checkbox_setting, create_font_setting, FontSelectionDialog


def create_style_tab(dialog) -> QWidget:
    """Create the Theme settings tab."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(24)

    desc_lbl = QLabel("Customize the visual appearance, accent colors, and font settings of ASSella.")
    desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; border: none; background: transparent;")
    layout.addWidget(desc_lbl)

    # Colors & Font Card
    theme_card, theme_card_lay = dialog._create_card_frame("Theme")
    theme_layout = QGridLayout()
    theme_layout.setContentsMargins(4, 4, 4, 4)
    theme_layout.setSpacing(10)

    # Accent color swatch row
    lbl_acc = QLabel("Accent Color:")
    lbl_acc.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    theme_layout.addWidget(lbl_acc, 0, 0)

    dialog.accent_color_button = QPushButton()
    dialog.accent_color_button.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.accent_color_button.setFixedSize(60, 24)
    dialog.accent_color_button.setStyleSheet(
        f"background-color: {dialog._user_accent_color}; border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 4px;"
    )
    dialog.accent_reset_button = QPushButton("Reset")
    dialog.accent_reset_button.setFixedWidth(70)
    dialog.accent_color_button.clicked.connect(lambda: choose_accent_color(dialog))
    dialog.accent_reset_button.clicked.connect(lambda: reset_accent_color(dialog))
    theme_layout.addWidget(dialog.accent_color_button, 0, 1)
    theme_layout.addWidget(dialog.accent_reset_button, 0, 2)

    # Background color swatch row
    lbl_bg = QLabel("Background Color:")
    lbl_bg.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    theme_layout.addWidget(lbl_bg, 1, 0)

    dialog.bg_color_button = QPushButton()
    dialog.bg_color_button.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.bg_color_button.setFixedSize(60, 24)
    dialog.bg_color_button.setStyleSheet(
        f"background-color: {dialog._user_background_color}; border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 4px;"
    )
    dialog.bg_reset_button = QPushButton("Reset")
    dialog.bg_reset_button.setFixedWidth(70)
    dialog.bg_color_button.clicked.connect(lambda: choose_bg_color(dialog))
    dialog.bg_reset_button.clicked.connect(lambda: reset_bg_color(dialog))
    theme_layout.addWidget(dialog.bg_color_button, 1, 1)
    theme_layout.addWidget(dialog.bg_reset_button, 1, 2)

    # Font row
    font_children, dialog.font_button, dialog.font_reset_button = create_font_setting(dialog)
    dialog.font_button.clicked.connect(lambda: choose_font(dialog))
    dialog.font_reset_button.clicked.connect(lambda: reset_font(dialog))

    dialog.font_button.setMinimumWidth(150)
    dialog.font_reset_button.setFixedWidth(70)

    lbl_font = QLabel("System Font:")
    lbl_font.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    theme_layout.addWidget(lbl_font, 2, 0)
    theme_layout.addWidget(dialog.font_button, 2, 1)
    theme_layout.addWidget(dialog.font_reset_button, 2, 2)

    # Material presets row
    lbl_preset = QLabel("Material Presets:")
    lbl_preset.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    theme_layout.addWidget(lbl_preset, 3, 0)

    dialog.preset_combo = QComboBox()
    dialog.preset_combo.addItem("Custom (Select Color)", "custom")
    dialog.preset_combo.addItem("Ocean Breeze (Monet Blue)", "ocean")
    dialog.preset_combo.addItem("Forest Sage (Mint Green)", "forest")
    dialog.preset_combo.addItem("Lavender Mist (Orchid Purple)", "lavender")
    dialog.preset_combo.setMinimumWidth(150)

    saved_preset = dialog.settings.value("material_preset", "ocean", type=str)
    idx = dialog.preset_combo.findData(saved_preset)
    if idx != -1:
        dialog.preset_combo.setCurrentIndex(idx)
    dialog.preset_combo.currentIndexChanged.connect(lambda idx: on_preset_changed(dialog, idx))

    theme_layout.addWidget(dialog.preset_combo, 3, 1)
    theme_card_lay.addLayout(theme_layout)
    layout.addWidget(theme_card)

    # Display / Behavior Card
    disp_card, disp_layout = dialog._create_card_frame("Display & Presentation")

    dialog.titlebar_position_checkbox = create_checkbox_setting(
        "Move Window Controls to Top",
        "titlebar_position",
        False,
        dialog,
        "Places the window close, minimize, and title buttons at the top instead of the bottom.",
        show_description=False,
    )
    is_top = dialog.settings.value("titlebar_position", "bottom", type=str) == "top"
    dialog.titlebar_position_checkbox.setChecked(is_top)
    dialog.titlebar_position_checkbox.stateChanged.connect(lambda s: on_titlebar_position_changed(dialog, s))
    disp_layout.addWidget(dialog.titlebar_position_checkbox)

    dialog.remember_origins_checkbox = create_checkbox_setting(
        "Remember Origins (Experimental)",
        "remember_origins",
        False,
        dialog,
        "Display atmospheric background animation.",
        show_description=False,
    )
    dialog.remember_origins_checkbox.stateChanged.connect(lambda s: on_origins_toggled(dialog, s))
    disp_layout.addWidget(dialog.remember_origins_checkbox)

    dialog.simplify_denuvo_status_checkbox = create_checkbox_setting(
        "Simplify Denuvo Status Badges",
        "simplify_denuvo_status",
        False,
        dialog,
        "Displays both Denuvo Hypervisor and Denuvo Uncracked games as simply Denuvo Uncracked.",
        show_description=False,
    )
    disp_layout.addWidget(dialog.simplify_denuvo_status_checkbox)

    layout.addWidget(disp_card)
    layout.addStretch(1)

    dialog.tab_widget.addTab(tab, "Theme")
    return tab


def choose_accent_color(dialog) -> None:
    color = QColorDialog.getColor()
    if not color.isValid():
        return
    if is_too_dark(color):
        show_color_warning()
        return
    hex_c = color.name()
    dialog.accent_color_button.setStyleSheet(f"background-color: {hex_c};")
    if hasattr(dialog, "preset_combo"):
        dialog.preset_combo.blockSignals(True)
        dialog.preset_combo.setCurrentIndex(0)
        dialog.preset_combo.blockSignals(False)


def reset_accent_color(dialog) -> None:
    default = "#C06C84"
    dialog.settings.setValue("accent_color", default)
    dialog.accent_color_button.setStyleSheet(f"background-color: {default};")
    if hasattr(dialog, "preset_combo"):
        dialog.preset_combo.blockSignals(True)
        dialog.preset_combo.setCurrentIndex(0)
        dialog.preset_combo.blockSignals(False)


def choose_bg_color(dialog) -> None:
    color = QColorDialog.getColor()
    if not color.isValid():
        return
    hex_c = color.name()
    dialog.bg_color_button.setStyleSheet(f"background-color: {hex_c};")
    if hasattr(dialog, "preset_combo"):
        dialog.preset_combo.blockSignals(True)
        dialog.preset_combo.setCurrentIndex(0)
        dialog.preset_combo.blockSignals(False)


def reset_bg_color(dialog) -> None:
    default = "#000000"
    dialog.settings.setValue("background_color", default)
    dialog.bg_color_button.setStyleSheet(f"background-color: {default};")
    if hasattr(dialog, "preset_combo"):
        dialog.preset_combo.blockSignals(True)
        dialog.preset_combo.setCurrentIndex(0)
        dialog.preset_combo.blockSignals(False)


def on_preset_changed(dialog, index: int) -> None:
    preset_type = dialog.preset_combo.itemData(index)
    if preset_type == "custom":
        return

    presets = {
        "ocean": ("#a1c9fd", "#111318"),
        "forest": ("#b1ecbe", "#0f1511"),
        "lavender": ("#e7bdfb", "#141217")
    }

    if preset_type in presets:
        accent_hex, bg_hex = presets[preset_type]
        dialog.settings.setValue("material_preset", preset_type)

        dialog.accent_color_button.setStyleSheet(f"background-color: {accent_hex};")
        dialog.bg_color_button.setStyleSheet(f"background-color: {bg_hex};")

        dialog.settings.setValue("user_accent_color", accent_hex)
        dialog.settings.setValue("user_background_color", bg_hex)
        dialog.settings.setValue("accent_color", accent_hex)
        dialog.settings.setValue("background_color", bg_hex)

        from ui.theme import update_appearance
        app = QApplication.instance()
        if app:
            update_appearance(app, accent_hex, bg_hex)


def is_too_dark(color: QColor) -> bool:
    brightness = color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114
    return brightness < 15


def is_too_close(accent: QColor, bg: QColor, threshold: int = 100) -> bool:
    r_diff = bg.red() - accent.red()
    g_diff = bg.green() - accent.green()
    b_diff = bg.blue() - accent.blue()
    return (r_diff**2 + g_diff**2 + b_diff**2) ** 0.5 < threshold


def show_color_warning() -> None:
    QMessageBox.warning(
        None,
        "Invalid Color",
        "This color is too dark and will make the interface unusable.",
    )


def choose_font(dialog) -> None:
    font, ok = FontSelectionDialog.get_font(dialog.current_font, dialog)
    if ok:
        dialog.current_font = font
        update_font_button_text(dialog)


def reset_font(dialog) -> None:
    default = QFont("TrixieCyrG-Plain", 10)
    default.setBold(False)
    default.setItalic(False)
    dialog.current_font = default
    update_font_button_text(dialog)


def update_font_button_text(dialog) -> None:
    if hasattr(dialog, "font_button") and hasattr(dialog, "current_font"):
        fam = dialog.current_font.family()
        size = dialog.current_font.pointSize()
        text = f"{fam} {size}pt"
        if dialog.current_font.bold():
            text += " Bold"
        if dialog.current_font.italic():
            text += " Italic"
        dialog.font_button.setText(text)
        dialog.font_button.setFont(dialog.current_font)


def on_titlebar_position_changed(dialog, state: int) -> None:
    pos = "top" if state == 2 else "bottom"
    dialog.settings.setValue("titlebar_position", pos)
    if dialog.main_window and hasattr(dialog.main_window, "reposition_titlebar"):
        dialog.main_window.reposition_titlebar(pos)


def save_style_settings(dialog) -> bool:
    acc_s = dialog.accent_color_button.styleSheet()
    bg_s = dialog.bg_color_button.styleSheet()
    u_accent = acc_s.split("background-color: ")[1].split(";")[0]
    u_bg = bg_s.split("background-color: ")[1].split(";")[0]

    dialog.settings.setValue("user_accent_color", u_accent)
    dialog.settings.setValue("user_background_color", u_bg)
    preset_type = "ocean"
    if hasattr(dialog, "preset_combo") and dialog.preset_combo is not None:
        preset_type = dialog.preset_combo.itemData(dialog.preset_combo.currentIndex())
        dialog.settings.setValue("material_preset", preset_type)

    applied_accent = u_accent
    applied_bg = u_bg
    dialog.settings.setValue("font-file", "")

    if is_too_close(QColor(u_accent), QColor(u_bg)):
        QMessageBox.warning(
            dialog,
            "Invalid Color",
            "Background too similar to accent color.",
        )
        return False

    dialog.settings.setValue("accent_color", applied_accent)
    dialog.settings.setValue("background_color", applied_bg)

    dialog.settings.setValue("font", dialog.current_font.family())
    dialog.settings.setValue("font-size", dialog.current_font.pointSize())

    style = "Normal"
    if dialog.current_font.bold():
        style = "Bold"
    if dialog.current_font.italic():
        style = "Italic"
    if dialog.current_font.bold() and dialog.current_font.italic():
        style = "Bold Italic"
    dialog.settings.setValue("font-style", style)

    origins = dialog.remember_origins_checkbox.isChecked()
    dialog.settings.setValue("remember_origins", origins)

    if hasattr(dialog, "simplify_denuvo_status_checkbox") and dialog.simplify_denuvo_status_checkbox is not None:
        simplify = dialog.simplify_denuvo_status_checkbox.isChecked()
        old_simplify = getattr(dialog, "_original_simplify_denuvo_status", None)
        dialog.settings.setValue("simplify_denuvo_status", simplify)

        if old_simplify is None or simplify != old_simplify:
            from ui.dialogs.gamelibrary import GameItemWidget
            from ui.dialogs.gamelibrary_v2 import GameDetailsDialogV2
            from ui.dialogs.fetchmanifest import SearchItemWidget
            for w in QApplication.instance().allWidgets():
                if isinstance(w, GameItemWidget):
                    w.update_denuvo_badge()
                    w.update_proton_badge()
                elif isinstance(w, GameDetailsDialogV2):
                    w.update_title()
                elif isinstance(w, SearchItemWidget):
                    w.update_ratings()

    orig_accent = getattr(dialog, "_original_accent_color", None)
    orig_bg = getattr(dialog, "_original_background_color", None)
    orig_font = getattr(dialog, "_original_font", None)
    orig_font_size = getattr(dialog, "_original_font_size", None)
    orig_font_style = getattr(dialog, "_original_font_style", None)
    orig_preset = getattr(dialog, "_original_material_preset", None)

    style_changed = (
        (orig_accent is not None and applied_accent.lower() != orig_accent.lower())
        or (orig_bg is not None and applied_bg.lower() != orig_bg.lower())
        or (orig_font is not None and dialog.current_font.family() != orig_font)
        or (orig_font_size is not None and dialog.current_font.pointSize() != orig_font_size)
        or (orig_font_style is not None and style != orig_font_style)
        or (orig_preset is not None and preset_type != orig_preset)
    )

    if style_changed and dialog.main_window and hasattr(dialog.main_window, "ui_state"):
        dialog.main_window.ui_state.apply_style_settings()

    return True


def on_origins_toggled(dialog, state: int) -> None:
    checked = bool(state)
    dialog.settings.setValue("remember_origins", checked)

    if dialog._origins_movie:
        dialog._origins_movie.stop()
        dialog._origins_movie = None

    if dialog._fade_timer:
        dialog._fade_timer.stop()
        dialog._fade_timer = None

    if checked:
        gif_path = os.path.expanduser("~/.local/share/ACCELA/jumpscare/lain.gif")
        if os.path.exists(gif_path):
            dialog._origins_movie = QMovie(gif_path)
            dialog._origins_movie.frameChanged.connect(dialog.update)
            dialog._origins_movie.start()

            dialog._flash_opacity = 0.85
            dialog._fade_timer = QTimer(dialog)
            dialog._fade_timer.timeout.connect(lambda: fade_origins_opacity(dialog))
            dialog._fade_timer.start(50)
        else:
            dialog._origins_movie = None
    else:
        dialog._origins_movie = None

    dialog.update()


def fade_origins_opacity(dialog) -> None:
    dialog._flash_opacity = max(0.18, dialog._flash_opacity - 0.04)
    dialog.update()
    if dialog._flash_opacity <= 0.18:
        if dialog._fade_timer:
            dialog._fade_timer.stop()
            dialog._fade_timer = None


def paint_origins_overlay(dialog, event) -> None:
    if hasattr(dialog, "_origins_movie") and dialog._origins_movie and dialog._origins_movie.state() == QMovie.MovieState.Running:
        painter = QPainter(dialog)
        current_pixmap = dialog._origins_movie.currentPixmap()
        if not current_pixmap.isNull():
            painter.setOpacity(dialog._flash_opacity)
            scaled_pixmap = current_pixmap.scaled(
                dialog.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            x = (dialog.width() - scaled_pixmap.width()) // 2
            y = (dialog.height() - scaled_pixmap.height()) // 2
            painter.drawPixmap(x, y, scaled_pixmap)
