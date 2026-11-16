"""App-menu shortcuts for games, the way ACCELA made them.

A shortcut is a ``.desktop`` entry in ``~/.local/share/applications`` that
starts the game through Steam (``steam steam://rungameid/<appid>``), with the
game's icon from SteamGridDB installed into the hicolor icon theme as
``steam_icon_<appid>.png``. Same file names and Exec line as ACCELA, so
ACCELA-made shortcuts are recognised and removed too
(GameManager._remove_linux_shortcuts_and_icons).

Linux only. Network and disk work: call off the GUI thread.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

SETTING_AFTER_INSTALL = "create_desktop_shortcut_after_install"
ICON_SIZES = (16, 24, 32, 48, 64, 96, 128, 256)


def supported() -> bool:
    return sys.platform.startswith("linux")


def _applications_dir() -> Path:
    return Path.home() / ".local" / "share" / "applications"


def _icon_base() -> Path:
    return Path.home() / ".local" / "share" / "icons" / "hicolor"


def _icon_name(appid: str) -> str:
    return f"steam_icon_{appid}"


def _exec_marker(appid: str) -> str:
    return f"steam://rungameid/{appid}"


def find_shortcut(appid: str) -> Optional[Path]:
    """The .desktop file that starts this AppID, if any."""
    d = _applications_dir()
    if not d.is_dir() or not str(appid).isdigit():
        return None
    marker = _exec_marker(str(appid))
    for f in d.glob("*.desktop"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            logger.debug(f"[Shortcuts] cannot read {f}: {e}")
            continue
        # Exact match: rungameid/12 must not match rungameid/123.
        if re.search(re.escape(marker) + r"(?!\d)", text):
            return f
    return None


def has_shortcut(appid: str) -> bool:
    return find_shortcut(appid) is not None


def _download(url: str) -> Optional[bytes]:
    try:
        import requests

        r = requests.get(url, timeout=15)
        if r.status_code == 200 and r.content:
            return r.content
        logger.debug(f"[Shortcuts] icon download HTTP {r.status_code}: {url}")
    except Exception as e:
        logger.debug(f"[Shortcuts] icon download failed: {e}")
    return None


def _icon_bytes(appid: str) -> Optional[bytes]:
    """Best static icon from SteamGridDB, if a key is set."""
    from utils import steamgriddb

    for asset in steamgriddb.get_assets("icons", appid):
        url = asset.get("url") or ""
        if url.lower().endswith((".png", ".jpg", ".jpeg")):  # skip .ico / animated
            data = _download(url)
            if data:
                return data
    return None


def _install_icons(appid: str, data: bytes) -> bool:
    """Write the icon in every hicolor size (Qt does the resizing; no Pillow needed)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage

    img = QImage.fromData(data)
    if img.isNull():
        return False
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    ok = False
    for size in ICON_SIZES:
        target = _icon_base() / f"{size}x{size}" / "apps"
        try:
            target.mkdir(parents=True, exist_ok=True)
            scaled = img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            ok = scaled.save(str(target / f"{_icon_name(appid)}.png"), "PNG") or ok
        except OSError as e:
            logger.debug(f"[Shortcuts] could not write {size}px icon: {e}")
    return ok


def _safe_file_name(name: str, appid: str) -> str:
    clean = re.sub(r"[\\/\0\n\r\t]", "", name).strip().strip(".") or f"Steam App {appid}"
    return clean[:120] + ".desktop"


def _desktop_value(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", " ").replace("\r", " ")


def create_shortcut(appid: str, game_name: str) -> Tuple[bool, str]:
    """Create (or refresh) the app-menu shortcut. Returns (ok, message)."""
    appid = str(appid).strip()
    if not supported():
        return False, "Desktop shortcuts are only available on Linux."
    if not appid.isdigit() or appid == "0":
        return False, "This game has no valid AppID."
    name = (game_name or "").strip() or f"Steam App {appid}"

    icon_note = ""
    data = _icon_bytes(appid)
    if data and _install_icons(appid, data):
        icon = _icon_name(appid)
    else:
        icon = "steam"  # the Steam client's icon
        from utils import steamgriddb

        icon_note = (" No SteamGridDB icon was found, so it uses the Steam icon."
                     if steamgriddb.get_api_key() else
                     " Add a SteamGridDB key in Settings -> Integrations for the game's own icon.")

    existing = find_shortcut(appid)
    path = existing or (_applications_dir() / _safe_file_name(name, appid))
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={_desktop_value(name)}\n"
        f"Comment=Play {_desktop_value(name)} on Steam\n"
        f"Exec=steam {_exec_marker(appid)}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Game;\n"
        "X-ASSella-AppID=" + appid + "\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
    except OSError as e:
        return False, f"Could not write the shortcut: {e}"
    _refresh_menus()
    logger.info(f"[Shortcuts] {'Updated' if existing else 'Created'} {path} for {appid}")
    return True, f"Added {name} to your app menu.{icon_note}"


def remove_shortcut(appid: str) -> Tuple[bool, str]:
    """Remove the shortcut and its icons (same clean-up as uninstall)."""
    appid = str(appid).strip()
    if not appid.isdigit():
        return False, "This game has no valid AppID."
    from managers.game_manager import GameManager

    GameManager._remove_linux_shortcuts_and_icons(appid)
    _refresh_menus()
    return (not has_shortcut(appid)), "Shortcut removed."


def _refresh_menus() -> None:
    """Ask the desktop to re-read the app menu (best effort, never blocks long)."""
    import shutil
    import subprocess

    tool = shutil.which("update-desktop-database")
    if not tool:
        return
    try:
        subprocess.run([tool, str(_applications_dir())], timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.debug(f"[Shortcuts] update-desktop-database failed: {e}")
