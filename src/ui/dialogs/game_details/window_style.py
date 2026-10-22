"""
The Game Details window's own stylesheet.

The window used to hardcode white ink and white overlay tints, which disappear
the moment a theme picks a light background. Everything here is derived from the
surface the window is painted on: `color_utils` picks the ink by WCAG contrast
and flips the overlay tints' polarity, so the window stays legible on any
background a built-in theme, a user theme or the custom colour picker hands it.

Two values deliberately follow the window rather than a fixed colour: the
combo box popup is painted with the window's background and its items with the
window's ink, so a dropdown can never be dark text on a dark popup.

One pre-existing declaration - `QLineEdit:disabled`'s
`border-color: rgba(255, 255, 255, 8)` - is malformed and is kept exactly as it
was, so nothing renders differently from before.
"""

from utils.color_utils import (
    get_best_foreground_color,
    get_dark_container_color,
    get_raised_surface_tokens,
    is_light_color,
    surface_overlay_rgb,
)
import re

from PyQt6.QtWidgets import QWidget


_WHITE_RGBA = re.compile(
    r"rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*([0-9]*\.?[0-9]+)\s*\)",
    re.IGNORECASE,
)
_WHITE_INK = re.compile(r"#(?:FFFFFF|E0E0E0|DDDDDD)\b", re.IGNORECASE)


def adapt_stylesheet_for_surface(stylesheet: str, background: str) -> str:
    """Flip hard-coded light ink and overlays when a child sits on a light surface.

    The Game Details tabs have accumulated local stylesheets over time.  Their
    dark-theme literals are intentional and remain byte-identical there; on a
    light/custom background this translates white text to readable dark ink and
    white translucent overlays to their black equivalents without changing alpha.
    """
    if not is_light_color(background) or not stylesheet:
        return stylesheet

    ink = get_best_foreground_color(background)
    adapted = _WHITE_RGBA.sub(lambda match: f"rgba(0, 0, 0, {match.group(1)})", stylesheet)
    return _WHITE_INK.sub(ink, adapted)


def adapt_child_stylesheets(root) -> None:
    """Adapt existing child-widget stylesheets to the Game Details surface."""
    background = getattr(root, "background_color", "#111318")
    for widget in root.findChildren(QWidget):
        stylesheet = widget.styleSheet()
        if stylesheet:
            adapted = adapt_stylesheet_for_surface(stylesheet, background)
            if adapted != stylesheet:
                widget.setStyleSheet(adapted)


def ink_on(background: str, alpha: float) -> str:
    """The window's own ink at `alpha`, following `background`'s polarity.

    White on the app's dark backgrounds, black on a light one, so an ad-hoc
    label or divider on a small widget still reads on either.
    """
    return f"rgba({surface_overlay_rgb(background)}, {alpha})"


def build_stylesheet(background: str, accent: str) -> str:
    """The stylesheet for a Game Details window painted on `background`."""
    tokens = get_raised_surface_tokens(background)
    ink = tokens["ink"]
    overlay = surface_overlay_rgb(background)
    selection = get_dark_container_color(accent)
    # The selection background is always a dark container, so its text is the
    # readable ink for *that* colour, not for the window behind it.
    selection_ink = get_best_foreground_color(selection)
    return f"""
        QDialog {{
            background-color: {background};
            color: {ink};
            border: 1px solid rgba({overlay}, 0.15);
        }}
        QFrame {{
            border: none;
            background: transparent;
        }}
        QLabel {{
            color: {ink};
            border: none;
            background: transparent;
            font-size: 9.5pt;
        }}
        QPushButton {{
            background-color: rgba({overlay}, 0.08);
            color: {ink};
            border: none;
            border-radius: 4px;
            padding: 5px 14px;
            font-size: 9.5pt;
        }}
        QPushButton:hover {{
            background-color: rgba({overlay}, 0.16);
            color: {accent};
        }}
        QPushButton:disabled {{
            background-color: rgba({overlay}, 0.02);
            color: rgba({overlay}, 0.098);
        }}
        QLineEdit {{
            background-color: rgba(0, 0, 0, 0.196);
            color: {ink};
            border: 1px solid rgba({overlay}, 0.059);
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 9.5pt;
        }}
        QLineEdit:focus {{ border-color: {accent}; }}
        QLineEdit:disabled {{
            color: rgba({overlay}, 0.118);
            border-color: rgba(255, 255, 255, 8);
        }}
        QComboBox {{
            background-color: rgba(0, 0, 0, 0.196);
            color: {ink};
            border: 1px solid rgba({overlay}, 0.059);
            border-radius: 6px;
            padding: 3px 8px;
            font-size: 9.5pt;
        }}
        QComboBox::drop-down {{ border: none; width: 18px; }}
        QComboBox QAbstractItemView {{
            background-color: {background};
            border: 1px solid rgba({overlay}, 0.08);
            border-radius: 8px;
            selection-background-color: {selection};
            selection-color: {selection_ink};
            font-size: 9.5pt;
            outline: 0px;
            padding: 4px;
        }}
        QComboBox QAbstractItemView::item {{
            min-height: 28px;
            padding: 4px 12px;
            color: {ink};
        }}
        QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{
            background-color: {selection} !important;
            color: {selection_ink} !important;
        }}
        QCheckBox {{
            color: {ink};
            font-size: 9.5pt;
            spacing: 6px;
        }}
        QCheckBox::indicator {{
            width: 14px; height: 14px;
            border: 1px solid rgba({overlay}, 0.078);
            border-radius: 3px;
            background: rgba(0, 0, 0, 0.157);
        }}
        QCheckBox::indicator:checked {{
            background-color: {accent};
            border-color: {accent};
        }}
        QScrollArea {{ border: none; background: transparent; }}
        QScrollBar:vertical {{
            border: none;
            background: rgba(0, 0, 0, 0.059);
            width: 4px;
            margin: 0px;
            border-radius: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: rgba({overlay}, 0.137);
            min-height: 20px;
            border-radius: 2px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: rgba({overlay}, 0.255);
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
    """
