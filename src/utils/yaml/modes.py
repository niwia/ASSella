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
    settings = get_settings()
    return settings.value("sls_config_management", True, type=bool)

def get_fake_appid_for_online() -> str:
    """Get the FakeAppId to use for playing games online.

    Returns:
        The appid from settings, or "480" (Spacewar) if not set.
    """
    settings = get_settings()
    fake_appid = settings.value("fake_appid_for_online", "", type=str).strip()
    return fake_appid if fake_appid else "480"
