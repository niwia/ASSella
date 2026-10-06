import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QMovie, QPainter
from PyQt6.QtWidgets import (
    QWidget,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QColorDialog,
    QMessageBox,
    QApplication,
    QSlider,
)

from utils.helpers import create_checkbox_setting, create_font_setting, FontSelectionDialog


def _apply_circle_style(button: QPushButton, color_hex: str) -> None:
    """Helper to apply a circular color swatch style."""
    button.setStyleSheet(f"""
        QPushButton {{
            background-color: {color_hex};
            border: 2px solid rgba(255, 255, 255, 0.40);
            border-radius: 13px;
        }}
        QPushButton:hover {{
            border: 2px solid #FFFFFF;
        }}
    """)


def create_style_tab(dialog) -> QWidget:
    """Create the Theme settings tab."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)

    desc_lbl = QLabel("Customize the visual appearance, accent colors, and font settings of ASSella.")
    desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 8.5pt; border: none; background: transparent;")
    layout.addWidget(desc_lbl)

    # Colors & Font Card
    theme_card, theme_card_lay = dialog._create_card_frame("Theme")
    theme_vbox = QVBoxLayout()
    theme_vbox.setContentsMargins(4, 4, 4, 4)
    theme_vbox.setSpacing(12)

    # 1. Colour Row (Single line, two circular swatches side-by-side)
    colour_row = QHBoxLayout()
    colour_row.setContentsMargins(0, 0, 0, 0)
    lbl_colour = QLabel("Colour:")
    lbl_colour.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    lbl_colour.setFixedWidth(120)
    colour_row.addWidget(lbl_colour)

    dialog.accent_color_button = QPushButton()
    dialog.accent_color_button.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.accent_color_button.setFixedSize(26, 26)
    dialog.accent_color_button.setToolTip("Accent Colour (click to choose)")

    dialog.bg_color_button = QPushButton()
    dialog.bg_color_button.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.bg_color_button.setFixedSize(26, 26)
    dialog.bg_color_button.setToolTip("Background Colour (click to choose)")

    _apply_circle_style(dialog.accent_color_button, dialog._user_accent_color)
    _apply_circle_style(dialog.bg_color_button, dialog._user_background_color)

    dialog.accent_color_button.clicked.connect(lambda: choose_accent_color(dialog))
    dialog.bg_color_button.clicked.connect(lambda: choose_bg_color(dialog))

    colour_row.addWidget(dialog.accent_color_button)
    colour_row.addSpacing(10)
    colour_row.addWidget(dialog.bg_color_button)
    colour_row.addStretch()
    theme_vbox.addLayout(colour_row)

    # 2. Font Row (Font selection + expandable Size button)
    font_row = QHBoxLayout()
    font_row.setContentsMargins(0, 0, 0, 0)
    lbl_font = QLabel("Font:")
    lbl_font.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    lbl_font.setFixedWidth(120)
    font_row.addWidget(lbl_font)

    _, dialog.font_button, _ = create_font_setting(dialog)
    dialog.font_button.clicked.connect(lambda: choose_font(dialog))
    dialog.font_button.setMinimumWidth(160)
    font_row.addWidget(dialog.font_button)
    font_row.addSpacing(8)

    dialog.size_btn = QPushButton("Size ▾")
    dialog.size_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.size_btn.setFixedWidth(70)
    dialog.size_btn.setStyleSheet("""
        QPushButton {
            background-color: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 6px;
            color: #FFFFFF;
            font-size: 9pt;
            font-weight: 500;
            padding: 4px 8px;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.16);
            border-color: rgba(255, 255, 255, 0.35);
        }
    """)
    font_row.addWidget(dialog.size_btn)
    font_row.addStretch()
    theme_vbox.addLayout(font_row)

    # 3. Collapsible Typography & Sizing Controls (expands inline under Font)
    dialog.sizing_container = QFrame()
    dialog.sizing_container.setObjectName("sizing_container")
    dialog.sizing_container.setStyleSheet("""
        QFrame#sizing_container {
            background-color: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 6px;
        }
    """)
    dialog.sizing_container.setVisible(False)

    font_grid = QGridLayout(dialog.sizing_container)
    font_grid.setContentsMargins(12, 10, 12, 10)
    font_grid.setSpacing(10)

    # 1. Base UI Font Size Slider
    lbl_base = QLabel("Base UI Size:")
    lbl_base.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500; border: none; background: transparent;")
    font_grid.addWidget(lbl_base, 0, 0)

    saved_base_sz = dialog.settings.value("font-size", 10, type=int)
    dialog.base_font_slider = QSlider(Qt.Orientation.Horizontal)
    dialog.base_font_slider.setRange(8, 16)
    dialog.base_font_slider.setValue(saved_base_sz)

    lbl_base_val = QLabel(f"{saved_base_sz} pt")
    lbl_base_val.setFixedWidth(50)
    lbl_base_val.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9pt; border: none; background: transparent;")

    def _on_base_slider_changed(val: int):
        lbl_base_val.setText(f"{val} pt")
        if hasattr(dialog, "current_font"):
            dialog.current_font.setPointSize(val)
            update_font_button_text(dialog)

    dialog.base_font_slider.valueChanged.connect(_on_base_slider_changed)
    font_grid.addWidget(dialog.base_font_slider, 0, 1)
    font_grid.addWidget(lbl_base_val, 0, 2)

    # 2. Queue & Lists Font Size Slider
    lbl_queue = QLabel("Queue & Lists Size:")
    lbl_queue.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500; border: none; background: transparent;")
    font_grid.addWidget(lbl_queue, 1, 0)

    saved_queue_sz = dialog.settings.value("font-size-queue", 10, type=int)
    dialog.queue_font_slider = QSlider(Qt.Orientation.Horizontal)
    dialog.queue_font_slider.setRange(8, 16)
    dialog.queue_font_slider.setValue(saved_queue_sz)

    lbl_queue_val = QLabel(f"{saved_queue_sz} pt")
    lbl_queue_val.setFixedWidth(50)
    lbl_queue_val.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9pt; border: none; background: transparent;")

    dialog.queue_font_slider.valueChanged.connect(lambda val: lbl_queue_val.setText(f"{val} pt"))
    font_grid.addWidget(dialog.queue_font_slider, 1, 1)
    font_grid.addWidget(lbl_queue_val, 1, 2)

    # 3. Headers & Titles Font Size Slider
    lbl_hdr = QLabel("Headers & Titles Size:")
    lbl_hdr.setStyleSheet("color: #FFFFFF; font-size: 9pt; font-weight: 500; border: none; background: transparent;")
    font_grid.addWidget(lbl_hdr, 2, 0)

    saved_hdr_sz = dialog.settings.value("font-size-headers", 11, type=int)
    dialog.headers_font_slider = QSlider(Qt.Orientation.Horizontal)
    dialog.headers_font_slider.setRange(9, 20)
    dialog.headers_font_slider.setValue(saved_hdr_sz)

    lbl_hdr_val = QLabel(f"{saved_hdr_sz} pt")
    lbl_hdr_val.setFixedWidth(50)
    lbl_hdr_val.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-size: 9pt; border: none; background: transparent;")

    dialog.headers_font_slider.valueChanged.connect(lambda val: lbl_hdr_val.setText(f"{val} pt"))
    font_grid.addWidget(dialog.headers_font_slider, 2, 1)
    font_grid.addWidget(lbl_hdr_val, 2, 2)

    theme_vbox.addWidget(dialog.sizing_container)

    def _toggle_size_panel():
        is_vis = not dialog.sizing_container.isVisible()
        dialog.sizing_container.setVisible(is_vis)
        dialog.size_btn.setText("Size ▴" if is_vis else "Size ▾")

    dialog.size_btn.clicked.connect(_toggle_size_panel)

    # 4. Material Presets Row
    preset_row = QHBoxLayout()
    preset_row.setContentsMargins(0, 0, 0, 0)
    lbl_preset = QLabel("Material Presets:")
    lbl_preset.setStyleSheet("color: #FFFFFF; font-size: 9.5pt; font-weight: 500; border: none; background: transparent;")
    lbl_preset.setFixedWidth(120)
    preset_row.addWidget(lbl_preset)

    dialog.preset_combo = QComboBox()
    dialog.preset_combo.addItem("Custom (Select Color)", "custom")

    from utils.theme_manager import load_all_themes
    all_themes = load_all_themes()
    for tid, tinfo in all_themes.items():
        dialog.preset_combo.addItem(tinfo["name"], tid)
    dialog.preset_combo.setMinimumWidth(180)

    saved_preset = dialog.settings.value("material_preset", "ocean", type=str)
    idx = dialog.preset_combo.findData(saved_preset)
    if idx != -1:
        dialog.preset_combo.setCurrentIndex(idx)
    dialog.preset_combo.currentIndexChanged.connect(lambda idx: on_preset_changed(dialog, idx))

    preset_row.addWidget(dialog.preset_combo)
    preset_row.addStretch()
    theme_vbox.addLayout(preset_row)

    theme_card_lay.addLayout(theme_vbox)
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
    _apply_circle_style(dialog.accent_color_button, hex_c)
    if hasattr(dialog, "preset_combo"):
        dialog.preset_combo.blockSignals(True)
        dialog.preset_combo.setCurrentIndex(0)
        dialog.preset_combo.blockSignals(False)


def reset_accent_color(dialog) -> None:
    default = "#C06C84"
    dialog.settings.setValue("accent_color", default)
    _apply_circle_style(dialog.accent_color_button, default)
    if hasattr(dialog, "preset_combo"):
        dialog.preset_combo.blockSignals(True)
        dialog.preset_combo.setCurrentIndex(0)
        dialog.preset_combo.blockSignals(False)


def choose_bg_color(dialog) -> None:
    color = QColorDialog.getColor()
    if not color.isValid():
        return
    hex_c = color.name()
    _apply_circle_style(dialog.bg_color_button, hex_c)
    if hasattr(dialog, "preset_combo"):
        dialog.preset_combo.blockSignals(True)
        dialog.preset_combo.setCurrentIndex(0)
        dialog.preset_combo.blockSignals(False)


def reset_bg_color(dialog) -> None:
    default = "#000000"
    dialog.settings.setValue("background_color", default)
    _apply_circle_style(dialog.bg_color_button, default)
    if hasattr(dialog, "preset_combo"):
        dialog.preset_combo.blockSignals(True)
        dialog.preset_combo.setCurrentIndex(0)
        dialog.preset_combo.blockSignals(False)


def on_preset_changed(dialog, index: int) -> None:
    preset_type = dialog.preset_combo.itemData(index)
    if preset_type == "custom":
        return

    from utils.theme_manager import load_all_themes
    all_themes = load_all_themes()

    if preset_type in all_themes:
        tinfo = all_themes[preset_type]
        accent_hex = tinfo["accent_color"]
        bg_hex = tinfo["background_color"]
        bg_img = tinfo.get("background_image")

        dialog.settings.setValue("material_preset", preset_type)
        if bg_img:
            dialog.settings.setValue("theme_background_image", str(bg_img))
        else:
            dialog.settings.remove("theme_background_image")

        chk_unlit = tinfo.get("checkbox_unlit")
        chk_lit = tinfo.get("checkbox_lit")
        if chk_unlit and chk_lit:
            dialog.settings.setValue("theme_checkbox_unlit", str(chk_unlit))
            dialog.settings.setValue("theme_checkbox_lit", str(chk_lit))
        else:
            dialog.settings.remove("theme_checkbox_unlit")
            dialog.settings.remove("theme_checkbox_lit")

        _apply_circle_style(dialog.accent_color_button, accent_hex)
        _apply_circle_style(dialog.bg_color_button, bg_hex)

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
        if hasattr(dialog, "base_font_slider"):
            dialog.base_font_slider.blockSignals(True)
            dialog.base_font_slider.setValue(font.pointSize())
            dialog.base_font_slider.blockSignals(False)


def reset_font(dialog) -> None:
    default = QFont("Open Sans", 10)
    default.setBold(False)
    default.setItalic(False)
    dialog.current_font = default
    update_font_button_text(dialog)
    if hasattr(dialog, "base_font_slider"):
        dialog.base_font_slider.setValue(10)
    if hasattr(dialog, "queue_font_slider"):
        dialog.queue_font_slider.setValue(10)
    if hasattr(dialog, "headers_font_slider"):
        dialog.headers_font_slider.setValue(11)


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
    if hasattr(dialog, "queue_font_slider"):
        dialog.settings.setValue("font-size-queue", dialog.queue_font_slider.value())
    if hasattr(dialog, "headers_font_slider"):
        dialog.settings.setValue("font-size-headers", dialog.headers_font_slider.value())

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
    orig_font_size_queue = getattr(dialog, "_original_font_size_queue", None)
    orig_font_size_headers = getattr(dialog, "_original_font_size_headers", None)
    orig_font_style = getattr(dialog, "_original_font_style", None)
    orig_preset = getattr(dialog, "_original_material_preset", None)

    q_sz = dialog.queue_font_slider.value() if hasattr(dialog, "queue_font_slider") else None
    h_sz = dialog.headers_font_slider.value() if hasattr(dialog, "headers_font_slider") else None

    style_changed = (
        (orig_accent is not None and applied_accent.lower() != orig_accent.lower())
        or (orig_bg is not None and applied_bg.lower() != orig_bg.lower())
        or (orig_font is not None and dialog.current_font.family() != orig_font)
        or (orig_font_size is not None and dialog.current_font.pointSize() != orig_font_size)
        or (orig_font_size_queue is not None and q_sz != orig_font_size_queue)
        or (orig_font_size_headers is not None and h_sz != orig_font_size_headers)
        or (orig_font_style is not None and style != orig_font_style)
        or (orig_preset is not None and preset_type != orig_preset)
    )

    if style_changed:
        if dialog.main_window and hasattr(dialog.main_window, "ui_state"):
            dialog.main_window.ui_state.apply_style_settings()
        if hasattr(dialog, "current_font"):
            dialog.setFont(dialog.current_font)
        dialog.update()

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
