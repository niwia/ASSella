"""
Theme Manager.

Handles application theming, palette application, and font loading.
"""

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication

from utils.paths import Paths

logger = logging.getLogger(__name__)


def normal_palette_colors(
    background_color: QColor, accent_color: QColor
) -> Dict[QPalette.ColorRole, QColor]:
    """Define colors for the normal palette state."""
    return {
        QPalette.ColorRole.Window: background_color,
        QPalette.ColorRole.WindowText: accent_color,
        QPalette.ColorRole.Base: background_color.darker(120),
        QPalette.ColorRole.AlternateBase: background_color,
        QPalette.ColorRole.ToolTipBase: accent_color,
        QPalette.ColorRole.ToolTipText: background_color,
        QPalette.ColorRole.Text: accent_color,
        QPalette.ColorRole.Button: background_color,
        QPalette.ColorRole.ButtonText: accent_color,
        QPalette.ColorRole.BrightText: accent_color.lighter(120),
        QPalette.ColorRole.Link: accent_color.lighter(120),
        QPalette.ColorRole.Highlight: accent_color,
        QPalette.ColorRole.HighlightedText: background_color,
        QPalette.ColorRole.PlaceholderText: accent_color.darker(120),
    }


def disabled_palette_colors(
    disabled_bg: QColor, disabled_text: QColor, background_color: QColor
) -> Dict[QPalette.ColorRole, QColor]:
    """Define colors for the disabled palette state."""
    return {
        QPalette.ColorRole.Button: disabled_bg,
        QPalette.ColorRole.ButtonText: disabled_text,
        QPalette.ColorRole.Text: disabled_text,
        QPalette.ColorRole.WindowText: disabled_text,
        QPalette.ColorRole.Base: background_color.darker(140),
    }


def apply_palette(app: QApplication, accent: str, background: str, font_name: str = "") -> None:
    """Apply the Fusion style and custom color palette to the application."""
    app.setStyle("Fusion")
    dark_palette = QPalette()

    background_color = QColor(background)
    accent_color = QColor(accent)

    disabled_bg = background_color.darker(200)
    disabled_text = QColor(100, 100, 100)

    # Apply normal colors
    for role, color in normal_palette_colors(background_color, accent_color).items():
        dark_palette.setColor(role, color)

    # Apply disabled colors
    for role, color in disabled_palette_colors(
        disabled_bg, disabled_text, background_color
    ).items():
        dark_palette.setColor(QPalette.ColorGroup.Disabled, role, color)

    app.setPalette(dark_palette)
    _apply_stylesheet(app, background_color, accent_color, disabled_bg, disabled_text, font_name)


def _apply_stylesheet(
    app: QApplication,
    bg_color: QColor,
    accent_color: QColor,
    disabled_bg: QColor,
    disabled_text: QColor,
    font_name: str = "",
) -> None:
    """Generate and apply the CSS stylesheet."""
    def mix_colors(c1: QColor, c2: QColor, weight: float) -> QColor:
        r = min(255, max(0, int(c1.red() * (1 - weight) + c2.red() * weight)))
        g = min(255, max(0, int(c1.green() * (1 - weight) + c2.green() * weight)))
        b = min(255, max(0, int(c1.blue() * (1 - weight) + c2.blue() * weight)))
        return QColor(r, g, b)

    accent_light = accent_color.lighter(120).name()
    accent_r = accent_color.red()
    accent_g = accent_color.green()
    accent_b = accent_color.blue()

    is_dark = bg_color.lightness() < 128

    # Dynamic Surface (Base Background)
    surface = bg_color.name()

    # Dynamic Surface Variant (Elevated Cards/Panels) - Lighter in dark mode, darker in light mode
    bg_elevated = bg_color.lighter(112) if is_dark else bg_color.darker(108)
    surface_variant_color = mix_colors(bg_elevated, accent_color, 0.06)
    surface_variant = surface_variant_color.name()

    # Tonal Container color (For buttons, inputs background)
    container_bg_color = mix_colors(bg_color, accent_color, 0.08)
    container_bg = container_bg_color.name()

    # Hover surface variant tint (15% accent color blend)
    hover_bg_color = mix_colors(bg_elevated, accent_color, 0.14)
    hover_bg = hover_bg_color.name()

    # Active selection variant tint (25% accent color blend)
    selected_bg_color = mix_colors(bg_elevated, accent_color, 0.25)
    selected_bg = selected_bg_color.name()

    # Subtle outlines
    outline_color = mix_colors(bg_color.lighter(140) if is_dark else bg_color.darker(130), accent_color, 0.2)
    outline = outline_color.name()

    font_family_css = f"font-family: '{font_name}';" if font_name else ""

    style_sheet = f"""
        * {{
            {font_family_css}
        }}

        QLineEdit {{
            background-color: {container_bg};
            color: {accent_color.name()};
            border: 1.5px solid {outline};
            border-radius: 8px;
            padding: 5px 10px;
        }}

        QLineEdit:hover {{
            border: 1.5px solid rgba({accent_r}, {accent_g}, {accent_b}, 150);
        }}

        QLineEdit:focus {{
            border: 1.5px solid {accent_color.name()};
            background-color: {surface};
        }}

        QCheckBox {{
            background-color: transparent;
            color: {accent_color.name()};
            padding: 4px;
            spacing: 8px;
            font-weight: bold;
        }}

        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            background: transparent;
            border: 1.5px solid {outline};
            border-radius: 4px;
        }}

        QCheckBox::indicator:checked {{
            background: {accent_color.name()};
            border: 1.5px solid {accent_color.name()};
        }}

        QCheckBox::indicator:hover {{
            border: 1.5px solid {accent_light};
            background: rgba({accent_r}, {accent_g}, {accent_b}, 20);
        }}

        QDialog {{
            background-color: {surface};
            color: {accent_color.name()};
        }}

        QListWidget {{
            background-color: {surface_variant};
            color: {accent_color.name()};
            border-radius: 12px;
            outline: 0;
            border: none;
            padding: 4px;
        }}

        QListWidget::item {{
            background-color: transparent;
            color: {accent_color.name()};
            border-radius: 8px;
            padding: 8px 12px;
            margin: 2px 0px;
        }}

        QListWidget::item:hover {{
            background-color: {hover_bg};
            color: {accent_light};
        }}

        QListWidget::item:selected {{
            background-color: {selected_bg};
            color: {accent_light};
            font-weight: bold;
        }}

        QListWidget::item:checked {{
            background-color: {hover_bg};
            color: {accent_color.name()};
            font-weight: bold;
        }}

        QListWidget::indicator {{
            width: 14px;
            height: 14px;
            background: transparent;
            border: 1.5px solid {outline};
            border-radius: 4px;
        }}

        QListWidget::indicator:unchecked {{
            background-color: transparent;
        }}

        QListWidget::indicator:checked {{
            background-color: {accent_color.name()};
            border: 1.5px solid {accent_color.name()};
        }}

        QListWidget::indicator:hover {{
            border: 1.5px solid {accent_light};
            background-color: rgba({accent_r}, {accent_g}, {accent_b}, 20);
        }}

        QPushButton {{
            background-color: {container_bg};
            color: {accent_color.name()};
            padding: 4px 14px;
            border: 1.5px solid {outline};
            border-radius: 12px;
            font-weight: bold;
        }}

        QPushButton:hover {{
            background-color: {hover_bg};
            color: {accent_light};
            border: 1.5px solid {accent_color.name()};
        }}

        QPushButton:pressed {{
            background-color: {selected_bg};
        }}

        QPushButton:disabled {{
            background-color: {disabled_bg.name()};
            color: {disabled_text.name()};
            border: 1.5px solid {disabled_text.name()};
            border-radius: 12px;
            font-weight: normal;
        }}

        QPushButton:disabled:hover {{
            background-color: {disabled_bg.name()};
            color: {disabled_text.name()};
        }}

        QLabel {{
            color: {accent_color.name()};
        }}

        QGroupBox {{
            background-color: {surface_variant};
            border: 1.5px solid {outline};
            border-radius: 16px;
            margin-top: 16px;
            padding-top: 20px;
            padding-left: 12px;
            padding-right: 12px;
            padding-bottom: 12px;
            font-weight: bold;
            font-size: 10.5pt;
            color: {accent_color.name()};
        }}

        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 16px;
            padding: 0px 8px;
        }}

        QToolTip {{
            background-color: {surface_variant};
            color: {accent_color.name()};
            padding: 6px;
            border: 1px solid {outline};
            border-radius: 4px;
        }}

        QScrollBar:vertical {{
            border: none;
            background: transparent;
            width: 8px;
            margin: 0px;
        }}

        QScrollBar::handle:vertical {{
            background: rgba({accent_r}, {accent_g}, {accent_b}, 50);
            min-height: 20px;
            border-radius: 4px;
        }}

        QScrollBar::handle:vertical:hover {{
            background: rgba({accent_r}, {accent_g}, {accent_b}, 100);
        }}

        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
    """
    app.setStyleSheet(style_sheet)


def _resolve_font_path(font_resource: Union[str, Path]) -> Path:
    """Resolve the provided font resource to a concrete Path object."""
    try:
        if isinstance(font_resource, str):
            candidate = Path(font_resource)
            if candidate.is_absolute() and candidate.exists():
                return candidate
            return Paths.resource(font_resource)

        if isinstance(font_resource, Path):
            return font_resource

        return Paths.resource(str(font_resource))
    except TypeError:
        # Fallback for unexpected types
        return Paths.resource(str(font_resource))


def _load_and_set_font(
    app: QApplication, font_path: Path, current_font: Optional[QFont]
) -> Tuple[bool, str]:
    """Load a font file from disk and set it to the application."""
    logger.debug(f"Attempting to load font from: {font_path}")

    if not font_path.exists():
        logger.warning(f"Font file not found at: {font_path}")
        return False, str(font_path)

    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id == -1:
        logger.warning(f"QFontDatabase failed to load font: {font_path}")
        return False, str(font_path)

    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        logger.warning(f"No font families returned for: {font_path}")
        return False, str(font_path)

    font_name = families[0]

    if current_font:
        # Update existing font object with new family
        current_font.setFamily(font_name)
        new_font = current_font
    else:
        # Create new default font
        new_font = QFont(font_name, 10)

    app.setFont(new_font)
    return True, font_name


def apply_font(
    app: QApplication,
    font: Optional[QFont],
    font_file: Optional[Union[str, Path]],
) -> Tuple[bool, Union[str, Path]]:
    """
    Applies the font to the application.

    If font_file is provided, loads that font file and applies it.
    If font is provided (with a family name), checks if it's a system font.
    Otherwise, falls back to the default TrixieCyrG font.
    """
    default_font_file = "TrixieCyrG-Plain Regular.otf"
    google_sans_path = Path("/home/deck/.local/share/ACCELA/fonts/Google_Sans/static/GoogleSans-Regular.ttf")
    if google_sans_path.exists():
        default_font_file = google_sans_path

    # Case 1: Specific font file provided
    if font_file:
        path = _resolve_font_path(font_file)
        return _load_and_set_font(app, path, font)

    # Case 2: System font provided
    if font and font.family():
        font_family = font.family()
        if font_family in QFontDatabase.families():
            logger.debug(f"Using system font: {font_family}")
            app.setFont(font)
            return True, font_family

        # System font not found, log and fall through to default
        logger.debug(f"Font family '{font_family}' not found in system, using default")

    # Case 3: Default - Check if Roboto is installed in the system
    families = QFontDatabase.families()
    if "Roboto" in families:
        logger.debug("Roboto font found in system database, using as default")
        if font:
            font.setFamily("Roboto")
            new_font = font
        else:
            new_font = QFont("Roboto", 10)
        app.setFont(new_font)
        return True, "Roboto"

    # Case 4: Fallback default file path if Roboto is not in the system
    default_font_file = "TrixieCyrG-Plain Regular.otf"
    google_sans_path = Path("/home/deck/.local/share/ACCELA/fonts/Google_Sans/static/GoogleSans-Regular.ttf")
    if google_sans_path.exists():
        default_font_file = google_sans_path

    path = _resolve_font_path(default_font_file)
    return _load_and_set_font(app, path, font)


def update_appearance(
    app: QApplication,
    accent: str = "#c36200",
    background: str = "#1f1f1f",
    font: Optional[QFont] = None,
    font_file: Optional[Union[str, Path]] = None,
) -> Tuple[bool, Union[str, Path]]:
    """
    Apply a dynamic palette and custom font to the application.

    Args:
        app: The QApplication instance.
        accent: Hex string for accent color.
        background: Hex string for background color.
        font: Optional QFont object for settings.
        font_file: Relative resource path to load custom font.
    """
    font_ok, font_info = apply_font(app, font, font_file)
    apply_palette(app, accent, background, str(font_info) if font_ok else "")
    return font_ok, font_info
