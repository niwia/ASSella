"""Feature-flag queries: which operating modes are active.

Extracted from ``utils/yaml_config_manager.py``. That module is now a thin
facade that re-exports this package's public names, so every existing
``from utils.yaml_config_manager import ...`` keeps working unchanged.

Do not import across sibling modules in this package except from ``core`` -
dependencies must point in one direction (leaf -> core) to keep the package
import-cycle free.
"""

import logging

# --- imports ---------------------------------------------------------
import sys

from utils.settings import get_settings


logger = logging.getLogger(__name__)


def is_slssteam_mode_enabled() -> bool:
    """Check if Steam integration is enabled for the current platform."""
    from utils.helpers import is_arm64
    if is_arm64():
        # On ARM64, allow if native SLSsteam.so is installed in ~/.local/share/SLSsteam/
        from core.steam_helpers import get_steam_env
        try:
            if not get_steam_env().sls_installed():
                return False
        except Exception as e:
            logger.debug(f"Failed to check SLS installation on ARM64: {e}")
            return False
    settings = get_settings()
    if sys.platform == "linux":
        return settings.value("library_mode", False, type=bool)
    return settings.value("slssteam_mode", False, type=bool)

def is_greenluma_wrapper_mode_enabled() -> bool:
    """Check if GreenLuma wrapper mode is enabled on Windows."""
    if sys.platform != "win32":
        return False

    settings = get_settings()
    return settings.value("slssteam_mode", False, type=bool)

def is_slssteam_config_management_enabled() -> bool:
    """Check if SLSsteam config management is enabled in settings."""
    from utils.helpers import is_arm64
    if is_arm64():
        from core.steam_helpers import get_steam_env
        try:
            if not get_steam_env().sls_installed():
                return False
        except Exception as e:
            logger.debug(f"Failed to check SLS installation on ARM64: {e}")
            return False
    settings = get_settings()
    return settings.value("sls_config_management", True, type=bool)

def is_at0m_enabled() -> bool:
    """Check if AT0-M / Vapor mode is enabled via SLSsteam plugins."""
    from utils.helpers import is_arm64
    if is_arm64():
        return False
    from utils.yaml.plugins import is_slssteam_plugins_enabled
    return is_slssteam_plugins_enabled()


def get_fake_appid_for_online() -> str:
    """Get the FakeAppId to use for playing games online.

    Returns:
        The appid from settings, or "480" (Spacewar) if not set.
    """
    settings = get_settings()
    fake_appid = settings.value("fake_appid_for_online", "", type=str).strip()
    return fake_appid if fake_appid else "480"
