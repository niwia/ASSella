"""Steam self-update blocking (``steam.cfg``) - one owner for the file and its state.

Steam reads ``steam.cfg`` from its install directory once, when the client
starts. A Headcrab style installer writes it so Steam does not update the
client underneath a patched launcher; the Health tab toggle removes it again
so the user can let Steam update itself.

Both the toggle and the status visor ask this module, so the button in
Settings -> Health and the "Steam Updates" field in the status visor cannot
disagree about the state.

The paths are resolved per call, never at import time, so the module stays
hermetic: a test can point ``HOME`` at a temporary directory and the module
follows it.
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# The two keys Steam honours to stop the client updating itself.
INHIBIT_KEY = "BootStrapperInhibitAll"
FORCE_SELF_UPDATE_KEY = "BootStrapperForceSelfUpdate"

_BLOCKED_LINES = (
    f"{INHIBIT_KEY}=enable",
    f"{FORCE_SELF_UPDATE_KEY}=disable",
)

_OWN_KEYS = (INHIBIT_KEY.lower(), FORCE_SELF_UPDATE_KEY.lower())

_TRUE_VALUES = {"enable", "enabled", "true", "1"}
_FALSE_VALUES = {"disable", "disabled", "false", "0"}


def candidate_paths() -> List[Path]:
    """Every ``steam.cfg`` location Steam may read, most likely first."""
    home = Path.home()
    paths: List[Path] = []

    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        if env and env.steam_path:
            p = Path(env.steam_path) / "steam.cfg"
            paths.append(p)
    except Exception as e:
        logger.debug("Could not query SteamEnv in candidate_paths: %s", e)

    for p in [
        home / ".steam" / "steam" / "steam.cfg",
        home / ".steam" / "root" / "steam.cfg",
        home / ".local" / "share" / "Steam" / "steam.cfg",
        home / ".steam" / "steam.cfg",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".steam" / "steam" / "steam.cfg",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam" / "steam.cfg",
    ]:
        if p not in paths:
            paths.append(p)
    return paths


def find_steam_cfg() -> Optional[Path]:
    """The first existing ``steam.cfg``, or None when there is none."""
    for path in candidate_paths():
        try:
            if path.is_file():
                return path
        except OSError as e:
            logger.warning("Could not inspect %s: %s", path, e)
    return None


def target_steam_cfg() -> Path:
    """Where a new ``steam.cfg`` belongs: an existing one, else the Steam dir."""
    existing = find_steam_cfg()
    if existing is not None:
        return existing

    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        if env and env.steam_path:
            p = Path(env.steam_path) / "steam.cfg"
            if p.parent.is_dir():
                return p
    except Exception as e:
        logger.debug("Could not query SteamEnv in target_steam_cfg: %s", e)

    home = Path.home()
    flatpak_steam = home / ".var" / "app" / "com.valvesoftware.Steam" / ".steam" / "steam"
    try:
        if flatpak_steam.is_dir():
            return flatpak_steam / "steam.cfg"
    except OSError as e:
        logger.warning("Could not inspect %s: %s", flatpak_steam, e)
    return home / ".steam" / "steam" / "steam.cfg"


def _parse_settings(path: Path) -> dict:
    """The key/value pairs of a ``steam.cfg``; comments and blanks are ignored."""
    settings = {}
    try:
        text = path.read_text(errors="replace")
    except OSError as e:
        logger.warning("Could not read %s: %s", path, e)
        return settings

    for line in text.splitlines():
        # Strip comments the way Steam does: '#' and ';' start a comment.
        line = line.split("#")[0].split(";")[0].strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        settings[key.strip().lower()] = value.strip().strip('"')
    return settings


def is_blocking(path: Path) -> bool:
    """True when this ``steam.cfg`` stops Steam from updating itself."""
    settings = _parse_settings(path)
    if settings.get(INHIBIT_KEY.lower(), "").lower() in _TRUE_VALUES:
        return True
    return settings.get(FORCE_SELF_UPDATE_KEY.lower(), "").lower() in _FALSE_VALUES


def are_steam_updates_blocked() -> bool:
    """True when any known ``steam.cfg`` blocks Steam self-updates."""
    for path in candidate_paths():
        try:
            if path.is_file() and is_blocking(path):
                return True
        except OSError as e:
            logger.warning("Could not inspect %s: %s", path, e)
    return False


def _is_our_key(line: str) -> bool:
    """True for a line that sets one of the two update-block keys."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith(";"):
        return False
    key, sep, _ = stripped.partition("=")
    return bool(sep) and key.strip().lower() in _OWN_KEYS


def set_steam_updates_blocked(blocked: bool) -> Tuple[bool, str]:
    """Create or remove the update block. Returns ``(ok, message)``.

    Blocking writes the two keys into the ``steam.cfg`` Steam reads, keeping
    any other setting the user put there. Unblocking removes those keys again;
    the file is deleted only when nothing else is left in it.
    """
    if blocked:
        return _write_block()
    return _remove_block()


def _write_block() -> Tuple[bool, str]:
    path = target_steam_cfg()
    try:
        existing = path.read_text(errors="replace") if path.is_file() else ""
    except OSError as e:
        logger.warning("Could not read %s: %s", path, e)
        existing = ""

    kept = [line for line in existing.splitlines() if not _is_our_key(line)]
    content = "\n".join(kept + list(_BLOCKED_LINES)) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    except OSError as e:
        logger.error("Could not write %s: %s", path, e)
        return False, f"Could not write {path}: {e}"

    return True, f"Steam updates are blocked. {path} was written."


def _remove_block() -> Tuple[bool, str]:
    touched: List[str] = []
    for path in candidate_paths():
        try:
            if not path.is_file():
                continue
            text = path.read_text(errors="replace")
        except OSError as e:
            logger.warning("Could not read %s: %s", path, e)
            continue

        lines = text.splitlines()
        kept = [line for line in lines if not _is_our_key(line)]
        if len(kept) == len(lines):
            continue  # nothing of ours in this file

        meaningful = [
            line
            for line in kept
            if line.strip() and not line.strip().startswith(("#", ";"))
        ]
        try:
            if meaningful:
                path.write_text("\n".join(kept).rstrip() + "\n")
                touched.append(f"{path} (other settings kept)")
            else:
                path.unlink()
                touched.append(f"{path} (removed)")
        except OSError as e:
            logger.error("Could not update %s: %s", path, e)
            return False, f"Could not update {path}: {e}"

    if not touched:
        return True, "Steam updates are already enabled - no steam.cfg was found."
    return True, "Steam updates are enabled. Removed: " + ", ".join(touched)
