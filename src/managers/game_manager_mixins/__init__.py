"""Domain-specific mixins for GameManager."""

from managers.game_manager_mixins.config_sync import GameManagerConfigSyncMixin
from managers.game_manager_mixins.scanning import GameManagerScanningMixin
from managers.game_manager_mixins.uninstall import GameManagerUninstallMixin
from managers.game_manager_mixins.updates import GameManagerUpdatesMixin, UPDATE_STATUS

__all__ = [
    "GameManagerConfigSyncMixin",
    "GameManagerScanningMixin",
    "GameManagerUninstallMixin",
    "GameManagerUpdatesMixin",
    "UPDATE_STATUS",
]