import threading
from typing import Any
from PyQt6.QtCore import QSettings

APP_NAME = "ACCELA"
ORG_NAME = "Tachibana Labs"

_settings_local = threading.local()


class RobustQSettings(QSettings):
    """Subclass of QSettings that robustly handles boolean values stored as string literals on Linux."""

    def value(self, key: str, defaultValue: Any = None, type: Any = None) -> Any:
        if type is bool or (type is None and isinstance(defaultValue, bool)):
            val = super().value(key, defaultValue)
            if val is None:
                return defaultValue
            if isinstance(val, str):
                return val.lower() in ("true", "1", "yes")
            return bool(val)

        if type is not None:
            return super().value(key, defaultValue, type)
        return super().value(key, defaultValue)


def get_settings() -> QSettings:
    """Get the application settings object.

    Returns a thread-local cached instance so that background threads each get
    their own QSettings object (QSettings is not safe to share across threads).
    This avoids creating a new object on every call while still being thread-safe.
    """
    if not hasattr(_settings_local, "instance"):
        _settings_local.instance = RobustQSettings(ORG_NAME, APP_NAME)
        # First run preset overrides for ASSella
        if _settings_local.instance.value("assela_initialized") is None:
            _settings_local.instance.setValue("assela_initialized", True)
            _settings_local.instance.setValue("accent_color", "#a1c9fd")
            _settings_local.instance.setValue("background_color", "#111318")
            _settings_local.instance.setValue("material_preset", "ocean")
            _settings_local.instance.setValue("user_accent_color", "#a1c9fd")
            _settings_local.instance.setValue("user_background_color", "#111318")
            _settings_local.instance.setValue("workshop_steam_enabled", True)
            _settings_local.instance.setValue("workshop_max_downloads", 4)
            _settings_local.instance.setValue("use_lancache", True)
            _settings_local.instance.setValue("prompt_steam_restart", False)
            _settings_local.instance.setValue("sls_config_management", True)
            _settings_local.instance.setValue("font", "Open Sans")
            _settings_local.instance.setValue("font-size", 10)
            _settings_local.instance.setValue("font-style", "Normal")
            _settings_local.instance.sync()

        # Sanitize legacy font settings (e.g. old Accela Trixie Cyrillic typewriter font)
        try:
            saved_font = _settings_local.instance.value("font", "", type=str)
            if saved_font.lower() in ("trixiecyrg-plain", "trixiecyrg-plain regular", "trixie"):
                _settings_local.instance.setValue("font", "Open Sans")
                _settings_local.instance.sync()
            saved_font_file = _settings_local.instance.value("font-file", "", type=str)
            if "trixie" in saved_font_file.lower():
                _settings_local.instance.setValue("font-file", "")
                _settings_local.instance.sync()
        except Exception:
            pass

        # Sanitize default_download_directory (e.g. if transient /tmp was left behind)
        try:
            from utils.paths import is_valid_download_directory
            cur_dl = _settings_local.instance.value("default_download_directory", "", type=str)
            if cur_dl and not is_valid_download_directory(cur_dl):
                _settings_local.instance.setValue("default_download_directory", "")
                _settings_local.instance.sync()
        except Exception:
            pass

        # Halloween theme seasonal auto-activation (Oct 26 - Nov 5, one-time only)
        try:
            check_and_apply_halloween_theme(_settings_local.instance)
        except Exception:
            pass

    return _settings_local.instance


def check_and_apply_halloween_theme(settings: QSettings) -> bool:
    """Auto-activates Halloween theme once if launched between Oct 26 and Nov 5.
    
    The switch 'halloween_auto_applied' is persistently flipped to True so it never activates
    automatically again, even if the user changes or keeps the theme.
    """
    try:
        from datetime import date
        today = date.today()
        # Active window: October 26 through November 5 (inclusive)
        in_halloween_window = (today.month == 10 and today.day >= 26) or (today.month == 11 and today.day <= 5)
        if in_halloween_window:
            already_applied = settings.value("halloween_auto_applied", False, type=bool)
            if not already_applied:
                settings.setValue("halloween_auto_applied", True)
                settings.setValue("material_preset", "halloween")
                settings.setValue("accent_color", "#ffb77d")
                settings.setValue("background_color", "#141211")
                settings.setValue("user_accent_color", "#ffb77d")
                settings.setValue("user_background_color", "#141211")
                settings.sync()
                return True
    except Exception:
        pass
    return False


def is_twp_needed() -> bool:
    """True if the Training Wheels Protocol should be shown (first ASSella launch / transition from ACCELA)."""
    s = get_settings()
    return not s.value("assella_twp_seen", False, type=bool)


def is_canary_welcome_needed() -> bool:
    """True if the Canary Welcome Slideshow should be shown (first Canary launch / onboarding)."""
    s = get_settings()
    return not s.value("canary_welcome_seen", False, type=bool)

