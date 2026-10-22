"""
qt_utils.py - Reusable PyQt6 UI and layout management utilities.
"""

from typing import Optional
from PyQt6.QtWidgets import QLayout


def clear_layout(layout: Optional[QLayout], keep_trailing_stretch: bool = False) -> None:
    """Remove and delete all widgets and sub-layouts from a QLayout.

    Recursively empties child layouts and schedules deletion on both widgets
    and nested layouts via deleteLater(). If keep_trailing_stretch is True,
    preserves the final stretch item in the layout.
    """
    if layout is None:
        return
    limit = 1 if keep_trailing_stretch else 0
    while layout.count() > limit:
        item = layout.takeAt(0)
        if item is None:
            continue
        w = item.widget()
        if w is not None:
            w.deleteLater()
        else:
            sub = item.layout()
            if sub is not None:
                clear_layout(sub)
                sub.deleteLater()
