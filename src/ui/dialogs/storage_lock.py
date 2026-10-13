"""Shared helper for the locked-storage display in depot selection dialogs.

Locked means: a greyed-out, non-interactive label showing the game's existing
install location, offering no choice. It is shown only for verify / update /
DLC flows over already-installed games, where the caller passes
``lock_storage=True``. Fresh installs leave the flag unset and get the normal
selectable storage buttons.

Used by ``DepotSelectionDialog``, ``ModernDepotSelectionDialog`` and
``SingleDepotSelectionDialog`` so the resolve-and-display logic lives in one
place instead of being copy-pasted per dialog.
"""

import logging
import os

logger = logging.getLogger(__name__)

_NON_APP_IDS = ("0", "N/A", "unknown")


def _normalize_to_library_root(path_str: str) -> str:
    """If path_str points inside a steamapps folder (e.g. install_path), trim to library root."""
    if not path_str:
        return ""
    try:
        real = os.path.realpath(path_str)
        if "steamapps" in real:
            parts = real.split(os.sep)
            for i, part in enumerate(parts):
                if part.lower() == "steamapps":
                    root = os.sep.join(parts[:i])
                    if root and os.path.isdir(root):
                        return root
        return real
    except Exception:
        return str(path_str)


def resolve_locked_target(parent, app_id, preferred_library_path,
                           storage_paths, lock_storage):
    """Return the install dir to display locked, or None when not locked.

    Locking is purely flag-driven: with ``lock_storage`` falsy this always
    returns None (fresh-install flows stay unlocked even if the app happens
    to be installed already). With the flag set, the target resolves from
    the game manager's known install location first, then the preferred
    library path, then the first detected storage path.
    """
    if not lock_storage:
        return None

    app_id = str(app_id or "").strip()
    if app_id and app_id not in _NON_APP_IDS:
        try:
            gm = None
            curr = parent
            while curr is not None and gm is None:
                gm = getattr(curr, "game_manager", None)
                parent_func = getattr(curr, "parent", None)
                curr = parent_func() if callable(parent_func) else None

            get_game = getattr(gm, "get_game", None) if gm is not None else None
            if callable(get_game):
                igame = get_game(app_id)
                if isinstance(igame, dict) and igame.get("library_path"):
                    return _normalize_to_library_root(igame["library_path"])
        except Exception as e:
            logger.debug(f"[StorageLock] game_manager lookup failed: {e}")

    try:
        from utils.paths import is_valid_download_directory
        if preferred_library_path:
            norm_pref = _normalize_to_library_root(preferred_library_path)
            if norm_pref and is_valid_download_directory(norm_pref):
                return os.path.realpath(norm_pref)
    except Exception as e:
        logger.debug(f"[StorageLock] preferred path check failed: {e}")
        try:
            if preferred_library_path:
                norm_pref = _normalize_to_library_root(preferred_library_path)
                if norm_pref:
                    return os.path.realpath(norm_pref)
        except Exception:
            pass

    if storage_paths:
        try:
            return os.path.realpath(storage_paths[0])
        except Exception:
            return storage_paths[0]
    return None


def _shorten_path(path_str: str) -> str:
    """Shorten path for display, e.g. /home/aiwin/.local/share/Steam -> /home/aiwin/.."""
    if not path_str:
        return ""
    try:
        p = os.path.normpath(str(path_str))
        parts = [part for part in p.split(os.sep) if part]
        if p.startswith(os.sep):
            if len(parts) > 2:
                return f"{os.sep}{parts[0]}{os.sep}{parts[1]}{os.sep}.."
            return p
        else:
            if len(parts) > 2:
                return f"{parts[0]}{os.sep}{parts[1]}{os.sep}.."
            return p
    except Exception:
        return str(path_str)


def create_locked_storage_button(target_path, label_text, free_str=None,
                                 height=28, radius=4):
    """Build the greyed-out, non-interactive storage display button."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QPushButton, QSizePolicy

    short_path = _shorten_path(target_path)
    if short_path:
        btn_text = f"{label_text} ({short_path})"
    else:
        btn_text = f"{label_text}"

    btn = QPushButton(btn_text)
    btn.setFixedHeight(height)
    btn.setEnabled(False)
    btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    btn.setCursor(Qt.CursorShape.ForbiddenCursor)
    btn.setStyleSheet(f"""
        QPushButton {{
            background-color: rgba(255, 255, 255, 0.04);
            color: rgba(255, 255, 255, 0.45);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: {radius}px;
            padding: 4px 10px;
            font-size: 8.5pt;
            font-weight: 500;
        }}
    """)
    btn.setToolTip(f"Storage location: {target_path}\n(Locked to installation directory)")
    return btn

