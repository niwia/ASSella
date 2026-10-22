"""Behaviour mixins for
:class:`ui.dialogs.depot_selection.dialog.DepotSelectionDialog`.

The dialog used to be one 3.2k-line class. Each module here owns one
coherent capability so the dialog body stays navigable. These are mixins,
not collaborators: they share instance state by design, because Qt binds
the signals, the slots and the event overrides to the single composed
class.

* :class:`BuildUIMixin` - static widget construction for the header, table, action row and bottom bar.
* :class:`DepotTableMixin` - fills and re-labels the depot table, and owns the hidden-depot expander.
* :class:`StorageMixin` - the storage-library picker and the selected-storage accessor.
* :class:`DepotMetadataMixin` - cached and background depot metadata, plus recovery of depots missing from the bundle.
* :class:`SelectionMixin` - everything that ticks or unticks rows, including the presets and dlc-only mode.
* :class:`ExecutableVerifyMixin` - pre-flight check that the selection contains the game's launch executable.
* :class:`HeaderImageMixin` - thumbnail fetching for the header.
* :class:`FileCustomisationMixin` - browse files: manifest dump and the file-selection tree.
* :class:`BranchBuildMixin` - branch and build selection, plus the build pin and manifest overrides.
* :class:`MigrationMixin` - registration of the checked depots and DLC entitlements into AT0-M mode.
"""

from ui.dialogs.depot_selection._mixins.build_ui import BuildUIMixin
from ui.dialogs.depot_selection._mixins.table import DepotTableMixin
from ui.dialogs.depot_selection._mixins.storage import StorageMixin
from ui.dialogs.depot_selection._mixins.metadata import DepotMetadataMixin
from ui.dialogs.depot_selection._mixins.selection import SelectionMixin
from ui.dialogs.depot_selection._mixins.executable import ExecutableVerifyMixin
from ui.dialogs.depot_selection._mixins.header_image import HeaderImageMixin
from ui.dialogs.depot_selection._mixins.files import FileCustomisationMixin
from ui.dialogs.depot_selection._mixins.branch_build import BranchBuildMixin
from ui.dialogs.depot_selection._mixins.migration import MigrationMixin

__all__ = [
    "BuildUIMixin",
    "DepotTableMixin",
    "StorageMixin",
    "DepotMetadataMixin",
    "SelectionMixin",
    "ExecutableVerifyMixin",
    "HeaderImageMixin",
    "FileCustomisationMixin",
    "BranchBuildMixin",
    "MigrationMixin",
]
