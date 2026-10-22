#!/usr/bin/env python3
"""Test the Game Details window's surface-derived stylesheet.

The window used to hardcode white ink and white overlay tints, which vanish on a
light background. ``window_style`` derives every one of them from the surface,
so this pins three things: the built-in dark themes still emit the colours they
always did, a light background gets dark ink and dark tints, and the one place
white is still right (text on the dark selection container) is the only place it
can appear.

    python3 scripts/test_window_style.py
"""
import importlib.util
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_failures = []
PASSED = 0

DARK_BACKGROUNDS = ["#111318", "#0f1511", "#141217", "#141211", "#0d0d15", "#000000"]
LIGHT_BACKGROUNDS = ["#f4f4f4", "#e0e0e0", "#ffffff"]
ACCENT = "#a1c9fd"


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
    app = QApplication.instance() or QApplication([])
    style = load_module(
        "assella_window_style",
        SRC_DIR / "ui" / "dialogs" / "game_details" / "window_style.py",
    )

    # ------------------------------------ local tab styles adapt as one sweep
    print("=== existing child styles adapt to light surfaces ===")
    sample = "QLabel { color: #FFFFFF; background: rgba(255, 255, 255, 0.08); }"
    dark_css = style.adapt_stylesheet_for_surface(sample, "#111318")
    light_css = style.adapt_stylesheet_for_surface(sample, "#f4f4f4")
    check(dark_css == sample, "dark-surface child CSS remains byte-identical")
    check("color: #121214" in light_css, "light-surface white text becomes dark ink")
    check("rgba(0, 0, 0, 0.08)" in light_css, "light-surface white tint flips and keeps alpha")
    check(
        style.adapt_stylesheet_for_surface("color: #E0E0E0;", "#ffffff") == "color: #121214;",
        "light-surface legacy gray text becomes readable ink",
    )

    root = QWidget()
    root.background_color = "#f4f4f4"
    child = QLabel(root)
    child.setStyleSheet(sample)
    style.adapt_child_stylesheets(root)
    check("color: #121214" in child.styleSheet(), "child-widget sweep applies adaptive CSS")
    check("rgba(0, 0, 0, 0.08)" in child.styleSheet(), "child-widget sweep adapts overlays too")
    root.close()
    dialog_source = (SRC_DIR / "ui" / "dialogs" / "gamelibrary_v2.py").read_text()
    check(
        "adapt_child_stylesheets(self)" in dialog_source,
        "the Game Details window runs the adaptive sweep after building its tabs",
    )

    # ------------------------------------------------- the polarity helper
    print("=== ink_on follows the surface ===")
    check(style.ink_on("#111318", 0.6) == "rgba(255, 255, 255, 0.6)",
          "dark ink at 0.6 is white")
    check(style.ink_on("#f4f4f4", 0.6) == "rgba(0, 0, 0, 0.6)",
          "light ink at 0.6 is black")
    check(style.ink_on("nonsense", 0.5) == "rgba(255, 255, 255, 0.5)",
          "an unparseable surface inks the way the default dark one does")

    # --------------------------------------------------- the generated sheet
    print("=== the stylesheet is well formed on every background ===")
    for background in DARK_BACKGROUNDS + LIGHT_BACKGROUNDS:
        css = style.build_stylesheet(background, ACCENT)
        check(css.count("{") == css.count("}"),
              f"{background}: the block braces balance")
        check("{{" not in css and "}}" not in css,
              f"{background}: no f-string escape is left behind")
        check("\n        QDialog {" in css,
              f"{background}: the window rule survived")
        check(ACCENT in css, f"{background}: the accent still reaches the sheet")

    # ------------------------------------- the dark themes do not move
    print("=== the built-in dark themes render as they always did ===")
    for background in DARK_BACKGROUNDS:
        css = style.build_stylesheet(background, ACCENT)
        check("color: #FFFFFF" in css, f"{background}: the window ink is still white")
        check("border: 1px solid rgba(255, 255, 255, 0.15)" in css,
              f"{background}: the window outline is still white at 0.15")
        check("background-color: rgba(255, 255, 255, 0.08)" in css,
              f"{background}: the button fill is still white at 0.08")
        check("color: rgba(255, 255, 255, 0.098)" in css,
              f"{background}: the disabled button ink is still white at 0.098")

    # ------------------------------------------- a light background adapts
    print("=== a light background gets dark ink and dark tints ===")
    for background in LIGHT_BACKGROUNDS:
        css = style.build_stylesheet(background, ACCENT)
        check("color: #121214" in css, f"{background}: the window ink turns dark")
        check("border: 1px solid rgba(0, 0, 0, 0.15)" in css,
              f"{background}: the window outline turns black at 0.15")
        check("background-color: rgba(0, 0, 0, 0.08)" in css,
              f"{background}: the button fill turns black at 0.08")
        check("color: rgba(0, 0, 0, 0.098)" in css,
              f"{background}: the disabled button ink turns black at 0.098")
        # the ONLY two places white is still correct: text on the selection
        # container (always a dark colour, whatever the accent), plus one
        # pre-existing malformed declaration kept verbatim.
        check(css.count("rgba(255, 255, 255, 8)") == 1,
              f"{background}: the one malformed legacy declaration is untouched")
        whites = [line.strip() for line in css.splitlines() if "255, 255, 255" in line]
        check(whites == ["border-color: rgba(255, 255, 255, 8);"],
              f"{background}: no white overlay is left anywhere else")
        hex_whites = [line.strip() for line in css.splitlines()
                      if "#FFFFFF" in line]
        check(all("selection" in line or line.startswith("color: #FFFFFF !important")
                  for line in hex_whites),
              f"{background}: white text is only used on the selection container")

    # --------------------------------- the selection keeps its own readable ink
    print("=== the selection container keeps readable ink ===")
    for accent in ["#a1c9fd", "#C06C84", "#000000", "#FFFFFF"]:
        css = style.build_stylesheet("#f4f4f4", accent)
        container = style.get_dark_container_color(accent)
        want = style.get_best_foreground_color(container)
        check(f"selection-background-color: {container}" in css,
              f"{accent}: the selection fill is the accent's dark container")
        check(f"selection-color: {want}" in css,
              f"{accent}: the selection text uses {want}")

    # ---------------------------------------- the popup follows the window
    print("=== the dropdown popup follows the window, not a fixed dark ===")
    for background in ["#111318", "#f4f4f4"]:
        css = style.build_stylesheet(background, ACCENT)
        check(f"background-color: {background}" in css,
              f"{background}: the popup is painted with the window's own background")
        check("#1b1b1f" not in css, f"{background}: the old hardcoded popup colour is gone")
        check("#E0E0E0" not in css, f"{background}: the old hardcoded item ink is gone")

    print(f"\n{'=' * 60}")
    del app
    if _failures:
        print(f"FAILED: {len(_failures)} check(s)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"All {PASSED} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
