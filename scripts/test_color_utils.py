#!/usr/bin/env python3
"""Test the surface/ink colour rules the themed widgets share.

Raised elements (tiles, chips, cards) used to hardcode white ink and white
overlay tints, which vanish the moment a theme picks a light background.
``color_utils`` now derives those colours from the surface an element sits on:
the ink is chosen by WCAG contrast and the overlay tint follows the surface's
polarity. This pins the rules, and pins the guarantee that the built-in dark
themes render byte-for-byte as the literals they replaced.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_color_utils.py
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_failures = []
PASSED = 0

# The backgrounds the built-in themes ship with, plus the default.
DARK_SURFACES = ["#111318", "#0f1511", "#141217", "#141211", "#0d0d15", "#000000"]

# What the old hand-written literals were, for the default dark background.
ORIGINAL_DARK_TOKENS = {
    "ink": "#FFFFFF",
    "ink_muted": "rgba(255, 255, 255, 0.6)",
    "ink_faint": "rgba(255, 255, 255, 0.7)",
    "ink_disabled": "rgba(255, 255, 255, 0.3)",
    "ink_subtle": "rgba(255, 255, 255, 0.2)",
    "tint": "rgba(255, 255, 255, 0.05)",
    "tint_hover": "rgba(255, 255, 255, 0.10)",
    "tint_disabled": "rgba(255, 255, 255, 0.02)",
    "border": "rgba(255, 255, 255, 0.08)",
    "border_hover": "rgba(255, 255, 255, 0.15)",
    "border_disabled": "rgba(255, 255, 255, 0.04)",
}


def check(cond, label):
    global PASSED
    if cond:
        PASSED += 1
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        _failures.append(label)
    return cond


def load_module(name, path):
    """Load a module by path, so importing it cannot run a package __init__."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    cu = load_module("assella_color_utils", SRC_DIR / "utils" / "color_utils.py")

    # ------------------------------------------------------ the WCAG primitives
    print("=== the WCAG primitives ===")
    check(abs(cu.get_relative_luminance("#FFFFFF") - 1.0) < 1e-6,
          "white is fully light")
    check(abs(cu.get_relative_luminance("#000000") - 0.0) < 1e-6,
          "black is not light at all")
    check(abs(cu.get_contrast_ratio("#FFFFFF", "#000000") - 21.0) < 1e-3,
          "white on black is the maximum 21:1 contrast")
    check(abs(cu.get_contrast_ratio("#123456", "#123456") - 1.0) < 1e-6,
          "a colour against itself is 1:1")
    check(cu.get_best_foreground_color("#111318") == "#FFFFFF",
          "a dark surface wants white ink")
    check(cu.get_best_foreground_color("#f4f4f4") == "#121214",
          "a light surface wants dark ink")

    # --------------------------------------------- classifying a surface's side
    print("=== classifying a surface as light or dark ===")
    for surface in DARK_SURFACES:
        check(cu.is_light_color(surface) is False, f"{surface} reads as dark")
    for surface in ["#FFFFFF", "#f4f4f4", "#e0e0e0", "#fff"]:
        check(cu.is_light_color(surface) is True, f"{surface} reads as light")
    check(cu.is_light_color("#808080") is False,
          "mid grey (luminance 0.22) is treated as dark, not light")
    check(cu.is_light_color("not-a-color") is False,
          "an unparseable surface falls back to dark")
    check(cu.is_light_color("") is False, "an empty surface falls back to dark")
    check(cu.is_light_color(None) is False, "a missing surface falls back to dark")

    # ------------------------------------------- the overlay flips with the side
    print("=== the overlay colour flips with the surface ===")
    check(cu.surface_overlay_rgb("#111318") == "255, 255, 255",
          "a dark surface overlays in white")
    check(cu.surface_overlay_rgb("#f4f4f4") == "0, 0, 0",
          "a light surface overlays in black")
    check(cu.surface_overlay_rgb("nonsense") == "255, 255, 255",
          "an unparseable surface overlays the way the default dark one does")

    # ---------------------------------- the dark themes do not move at all
    print("=== the built-in dark themes render exactly as before ===")
    for surface in DARK_SURFACES:
        check(cu.get_raised_surface_tokens(surface) == ORIGINAL_DARK_TOKENS,
              f"{surface} keeps every literal the tiles used before")

    # ------------------------------------------- a light surface turns readable
    print("=== a light surface gets dark ink and dark tints ===")
    light = cu.get_raised_surface_tokens("#f4f4f4")
    check(light["ink"] == "#121214", "the ink on a light surface is dark")
    check(light["ink_muted"] == "rgba(0, 0, 0, 0.6)", "the muted ink is black at 0.6")
    check(light["ink_faint"] == "rgba(0, 0, 0, 0.7)", "the faint ink is black at 0.7")
    check(light["ink_disabled"] == "rgba(0, 0, 0, 0.3)", "the disabled ink is black at 0.3")
    check(light["ink_subtle"] == "rgba(0, 0, 0, 0.2)", "the subtle ink is black at 0.2")
    check(light["tint"] == "rgba(0, 0, 0, 0.05)", "the tile tint is black at 0.05")
    check(light["tint_hover"] == "rgba(0, 0, 0, 0.10)", "the hover tint is black at 0.10")
    check(light["border"] == "rgba(0, 0, 0, 0.08)", "the outline is black at 0.08")
    check(light["border_hover"] == "rgba(0, 0, 0, 0.15)", "the hover outline is black at 0.15")
    check("255, 255, 255" not in " ".join(light.values()),
          "nothing in a light surface's tokens is still white")

    # the keyboard/skeleton contract: exactly the keys callers rely on
    check(set(cu.get_raised_surface_tokens("#111318")) == set(ORIGINAL_DARK_TOKENS),
          "the token set is exactly the one the widgets read")

    # every surface, light or dark, gets ink that clears WCAG AA for body text
    print("=== ink stays readable on every theme background ===")
    for surface in DARK_SURFACES + ["#f4f4f4", "#e0e0e0", "#808080", "#3a3a3a"]:
        ink = cu.get_raised_surface_tokens(surface)["ink"]
        ratio = cu.get_contrast_ratio(surface, ink)
        check(ratio >= 4.5, f"{surface}: ink {ink} clears AA ({ratio:.1f}:1)")

    # ------------------------------- odd input normalises instead of inverting
    print("=== odd surfaces normalise to the default dark look ===")
    check(cu.get_raised_surface_tokens("nope") == ORIGINAL_DARK_TOKENS,
          "a junk surface gets the default dark tokens")
    check(cu.get_raised_surface_tokens("#111") == cu.get_raised_surface_tokens("#111111"),
          "a three-digit hex is expanded before use")

    # ------------------------------------------- the tile follows its dialog
    print("=== MaterialTile follows the surface it is drawn on ===")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.gettempdir())
    from PyQt6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])
    widgets = load_module(
        "assella_game_details_widgets",
        SRC_DIR / "ui" / "dialogs" / "game_details" / "widgets.py",
    )

    class FakeDialog(QWidget):
        # the real dialogs carry the chosen background under this name
        background_color = "#111318"

    dialog = FakeDialog()
    tile = widgets.MaterialTile("STATUS UNKNOWN", "Click to check", dialog, is_toggle=False)

    tile.update_state(False, "#a1c9fd")
    check("rgba(255, 255, 255, 0.05)" in tile.styleSheet(),
          "on a dark dialog the idle tile keeps its white tint")
    check("#FFFFFF" in tile.title_lbl.styleSheet(),
          "on a dark dialog the idle tile keeps white text")

    dialog.background_color = "#f4f4f4"
    tile.update_state(False, "#a1c9fd")
    check("rgba(0, 0, 0, 0.05)" in tile.styleSheet(),
          "on a light dialog the idle tile switches to a black tint")
    check("255, 255, 255" not in tile.styleSheet(),
          "on a light dialog no white overlay is left in the tile")
    check("#121214" in tile.title_lbl.styleSheet(),
          "on a light dialog the tile's title turns dark")
    check("rgba(0, 0, 0, 0.6)" in tile.sub_lbl.styleSheet(),
          "on a light dialog the tile's subtext turns dark and muted")

    # the active tile keeps its accent fill, whichever surface it is on
    dialog.background_color = "#111318"
    tile.update_state(True, "#a1c9fd")
    check("background-color: #a1c9fd" in tile.styleSheet(),
          "a checked tile is still filled with the accent")
    check(cu.get_best_foreground_color("#a1c9fd") in tile.title_lbl.styleSheet(),
          "a checked tile's text is the accent's readable ink")

    # disabling on a light surface must not fall back to white either
    dialog.background_color = "#f4f4f4"
    tile.update_state(False, "#a1c9fd")
    tile.setEnabled(False)
    check("rgba(0, 0, 0, 0.3)" in tile.title_lbl.styleSheet(),
          "a disabled tile on a light dialog dims to black, not white")
    tile.setEnabled(True)
    check("#121214" in tile.title_lbl.styleSheet(),
          "re-enabling the tile restores the light surface's ink")

    # a tile whose parent carries no background falls back to the theme setting
    plain = widgets.MaterialTile("Plain", "No dialog", None, is_toggle=False)
    tokens = plain._surface_tokens()
    check(set(tokens) == set(ORIGINAL_DARK_TOKENS),
          "a tile without a surface source still gets a full token set")

    # ------------------------------------------------ thumbnail dominant color & disk cache
    print("\n=== thumbnail dominant color & disk cache ===")
    from PyQt6.QtGui import QPixmap, QColor, QPainter

    # Create synthetic test pixmap with a bright crimson patch
    test_px = QPixmap(100, 100)
    test_px.fill(QColor(10, 10, 10))
    p = QPainter(test_px)
    p.fillRect(20, 20, 60, 60, QColor(220, 40, 60))
    p.end()

    # Test extraction
    extracted = cu.extract_dominant_color(test_px, app_id="9999991")
    check(extracted is not None and extracted.isValid(), "extracted color is valid")
    check(extracted.red() > extracted.blue() and extracted.red() > extracted.green(),
          "dominant red hue detected accurately")

    # Test memory cache
    cached_mem = cu.get_cached_thumbnail_color("9999991")
    check(cached_mem is not None and cached_mem == extracted,
          "cached color retrieved from memory cache")

    # Test disk cache round-trip
    cu._THUMBNAIL_COLOR_CACHE.clear()
    cu._DISK_CACHE_LOADED = False
    cached_disk = cu.get_cached_thumbnail_color("9999991")
    check(cached_disk is not None and cached_disk.name() == extracted.name(),
          "cached color successfully persisted and reloaded from disk cache")

    # Test un-cached appid
    check(cu.get_cached_thumbnail_color("999999999") is None,
          "unknown appid returns None from cache")

    del app
    print(f"\n{'=' * 60}")
    if _failures:
        print(f"FAILED: {len(_failures)} check(s)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"All {PASSED} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
