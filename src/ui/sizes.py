"""Shared control sizes so buttons and dropdowns line up across ASSella.

Use ``size_control(widget, "md")`` instead of ad-hoc setFixedWidth/Height:

    CONTROL_HEIGHT  one height for every button and dropdown in a form row
    WIDTHS          sm (short values, e.g. 8 / On), md (labels like "Ask Every Time"),
                    lg (paths, provider names)
"""

from __future__ import annotations

CONTROL_HEIGHT = 30
WIDTHS = {"sm": 120, "md": 180, "lg": 240}


def size_control(widget, width: str | int | None = "md", height: int = CONTROL_HEIGHT):
    """Give a button / combo box the standard height and, optionally, a standard width."""
    if width is not None:
        widget.setFixedWidth(WIDTHS.get(width, width) if isinstance(width, str) else int(width))
    widget.setFixedHeight(height)
    return widget
