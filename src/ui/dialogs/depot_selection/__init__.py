"""Depot selection dialog, split into testable pieces.

    rules.py     pure classification rules (platform, media, defaults) - no Qt
    items.py     table cell items that carry byte counts
    views.py     the checkbox delegate and the header with the select-all box
    dialog.py    DepotSelectionDialog: routing, state, accessors, Qt events
    _mixins/     the dialog's behaviour, one module per capability

Browse Files is not a mixin-shaped capability: ``manifest_dump.py`` prepares a
dump (Qt-free) and ``dump_runner.py`` runs it, and both the multi-depot and the
single-depot dialog use them.

Choosing a drive is shared the same way: ``storage_targets.py`` answers which
drives are worth offering, how each reads, and which starts selected, while the
widgets stay with each dialog and with ``storage_lock``.

The facts about a depot are shared in the same spirit: ``depot_metadata.py``
answers what still needs enriching, what size a branch records, how enrichment
records merge into depot dicts, and what is known about depots the bundle is
missing - leaving the metadata mixin the threads, the signals and the cells.

The pre-flight check for the launch executable is shared too:
``executable_probe.py`` finds the executable's name in the game record or
Steam's appinfo cache, and the depot whose manifest mentions it, while the
mixin keeps the warning box and the row it ticks.

Which depots a preset ticks is shared in the same way:
``selection_policy.py`` answers what Steam would install, what a platform
preset wants, what the edition menu reads, and which DLCs DLC-only mode may
select, while the mixin keeps the table walking, the checkboxes and the menu.

Both ``ui.dialogs.depot_selection`` and the legacy facade
``ui.dialogs.depotselection`` export the full public API.
"""

from ui.dialogs.depot_selection.dialog import DepotSelectionDialog
from ui.dialogs.depot_selection.items import (
    ConfigTableWidgetItem,
    NumericTableWidgetItem,
)
from ui.dialogs.depot_selection.rules import (
    _depot_is_android,
    _depot_is_macos,
    _depot_matches_platform,
    format_size,
    get_smart_default_depots,
    is_bonus_or_media_depot,
)
from ui.dialogs.depot_selection.views import DepotCheckboxDelegate, DepotHeaderView

__all__ = [
    "ConfigTableWidgetItem",
    "DepotCheckboxDelegate",
    "DepotHeaderView",
    "DepotSelectionDialog",
    "NumericTableWidgetItem",
    "_depot_is_android",
    "_depot_is_macos",
    "_depot_matches_platform",
    "format_size",
    "get_smart_default_depots",
    "is_bonus_or_media_depot",
]
