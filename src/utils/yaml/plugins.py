"""Lua plugin deployment into the SLSsteam plugin directories.

Extracted from ``utils/yaml_config_manager.py``. That module is now a thin
facade that re-exports this package's public names, so every existing
``from utils.yaml_config_manager import ...`` keeps working unchanged.

Do not import across sibling modules in this package except from ``core`` -
dependencies must point in one direction (leaf -> core) to keep the package
import-cycle free.
"""

import logging

# --- imports ---------------------------------------------------------
from pathlib import Path
from typing import List, Optional, Tuple

from utils.yaml.core import get_user_config_path
from utils.yaml.modes import is_slssteam_config_management_enabled
from utils.yaml.yaml_values import get_yaml_boolean_value, update_yaml_boolean_value

from utils.settings import get_settings

logger = logging.getLogger(__name__)


def ensure_plugins_enabled(config_path: Optional[Path] = None) -> bool:
    """Ensure 'Plugins: yes' is present and enabled in SLSsteam config.yaml.

    If missing or disabled ('no', 'false'), imposes 'Plugins: yes'.
    Creates a backup if modification is needed and writes atomically.
    """
    if config_path is None:
        config_path = get_user_config_path()

    if not config_path.exists():
        logger.debug(f"ensure_plugins_enabled: Config not found at {config_path}")
        return False

    if not is_slssteam_config_management_enabled():
        logger.debug("ensure_plugins_enabled: SLS config management disabled in settings")
        return False

    return update_yaml_boolean_value(config_path, "Plugins", True)

def get_sls_plugins_dirs() -> List[Path]:
    """Resolve target plugin directories based on detected Native/Flatpak Steam environments."""
    dirs: List[Path] = []
    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        primary_dir = env.sls_config_dir / "plugins"
        dirs.append(primary_dir)

        # Check if alternate environment exists on disk (Flatpak vs Native)
        alt_base = (
            Path.home() / ".config" / "SLSsteam"
            if env.is_flatpak
            else Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / ".config" / "SLSsteam"
        )
        if alt_base.exists():
            alt_dir = alt_base / "plugins"
            if alt_dir not in dirs:
                dirs.append(alt_dir)
    except Exception as e:
        logger.warning(f"Error resolving SteamEnv for plugins: {e}")
        dirs.append(Path.home() / ".config" / "SLSsteam" / "plugins")

    return dirs

def deploy_sls_plugin(plugin_filename: str) -> Tuple[bool, bool, str]:
    """Deploy a specific SLSsteam plugin on demand from Cloud / local cache.

    Returns:
        (success: bool, skipped: bool, message: str)
        - skipped=True if all target locations already have the matching SHA-256.
    """
    from utils.plugin_manager import deploy_plugin
    return deploy_plugin(plugin_filename)

def deploy_all_sls_plugins() -> Tuple[bool, List[str]]:
    """Deploy required plugins (download.lua, spliced-tickets.lua) from Cloud on demand."""
    from utils.plugin_manager import deploy_all_plugins
    return deploy_all_plugins()

def are_sls_plugins_deployed() -> bool:
    """Check if the required plugins exist in at least the primary SLSsteam plugins directory."""
    from utils.plugin_manager import are_all_plugins_installed
    return are_all_plugins_installed()

def is_slssteam_plugins_enabled() -> bool:
    """Check if the user has enabled SLSsteam plugins in ASSella or in config.yaml.

    Returns:
        bool: True if plugins are enabled in settings or config.yaml.
    """
    settings = get_settings()

    # 1. Check ASSella explicit user settings (enable_vapor / enable_at0m)
    vapor_val = settings.value("enable_vapor", None)
    if vapor_val is not None:
        return settings.value("enable_vapor", type=bool)

    at0m_val = settings.value("enable_at0m", None)
    if at0m_val is not None:
        return settings.value("enable_at0m", type=bool)

    # 2. Check config.yaml Plugins key
    cfg_path = get_user_config_path()
    if cfg_path.exists():
        return get_yaml_boolean_value(cfg_path, "Plugins", default=False)

    return False


def sync_plugins_on_startup() -> bool:
    """Startup auto-sync disabled to prevent destructive overwriting of custom plugins."""
    return True
