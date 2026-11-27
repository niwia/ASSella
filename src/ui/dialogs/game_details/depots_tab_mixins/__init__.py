"""Domain-specific mixins and UI primitives for the Game Details Depots tab."""

from ui.dialogs.game_details.depots_tab_mixins.enrichment import (
    DepotsEnrichmentMixin,
    _EnrichmentBridge,
    _QuotaBridge,
    _WorkerBridge,
)
from ui.dialogs.game_details.depots_tab_mixins.state import (
    ALL_SHARED_REDISTS,
    DepotsStateMixin,
)
from ui.dialogs.game_details.depots_tab_mixins.sync import DepotsSyncMixin
from ui.dialogs.game_details.depots_tab_mixins.tables import DepotsTablesMixin
from ui.dialogs.game_details.depots_tab_mixins.widgets import (
    MiniSwitchToggle,
    _ToggleContainer,
    _format_enriched_depot_desc,
    _make_bulk_toggle_bar,
    _make_local_indicator,
    _make_table,
    _set_table_height,
)

__all__ = [
    "ALL_SHARED_REDISTS",
    "DepotsEnrichmentMixin",
    "DepotsStateMixin",
    "DepotsSyncMixin",
    "DepotsTablesMixin",
    "MiniSwitchToggle",
    "_EnrichmentBridge",
    "_QuotaBridge",
    "_ToggleContainer",
    "_WorkerBridge",
    "_format_enriched_depot_desc",
    "_make_bulk_toggle_bar",
    "_make_local_indicator",
    "_make_table",
    "_set_table_height",
]