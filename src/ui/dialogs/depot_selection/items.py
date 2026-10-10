"""Table cell items for the depot table.

The table is no longer click-sortable - row order is decided once, by
``stable_depot_order`` in :mod:`rules`, so the header checkbox cannot
reorder rows under the user's cursor. That removed the need for the ``__lt__``
overrides these classes used to define.

The ``sort_value`` attribute keeps its name but is no longer a sort key: it
holds the raw byte count, and the table reads it back when recomputing the
selected and downloaded totals. Renaming it is a mechanical follow-up.

``tier`` is accepted for call-site compatibility and is now unused. It only
ever existed to order rows by tier during click-sorting.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableWidgetItem


class NumericTableWidgetItem(QTableWidgetItem):
    """A size cell that keeps the raw byte count alongside the display text."""

    def __init__(self, text, sort_value, tier=0):
        super().__init__(text)
        self.sort_value = sort_value
        self.tier = tier
        self.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


class ConfigTableWidgetItem(QTableWidgetItem):
    """A plain text cell for the depot id and config columns."""

    def __init__(self, text, tier=0):
        super().__init__(text)
        self.tier = tier
        self.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)