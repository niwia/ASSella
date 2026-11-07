#!/usr/bin/env python3
"""Desktop shortcuts (utils/desktop_shortcuts.py): create, find, remove, exact AppID match.

Hermetic: HOME is a temp dir and the SteamGridDB lookup is stubbed.

    QT_QPA_PLATFORM=offscreen python3 scripts/test_desktop_shortcuts.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="assella-shortcuts-")
os.environ["HOME"] = _TMP
os.environ["XDG_CONFIG_HOME"] = str(Path(_TMP) / ".config")
os.environ["XDG_DATA_HOME"] = str(Path(_TMP) / ".local" / "share")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

FAILS = []


def check(cond, label):
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILS.append(label)


def main() -> int:
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])

    img = QImage(64, 64, QImage.Format.Format_ARGB32)
    img.fill(QColor("#a1c9fd"))
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    png = bytes(buf.data())

    from utils import desktop_shortcuts as ds

    if not ds.supported():
        print("not Linux: skipped")
        return 0

    ds._icon_bytes = lambda appid: png if appid == "123" else None
    ds._refresh_menus = lambda: None

    apps = Path(_TMP) / ".local/share/applications"
    icons = Path(_TMP) / ".local/share/icons/hicolor"

    print("=== create ===")
    ok, msg = ds.create_shortcut("123", "Some Game: Deluxe/Edition")
    check(ok, f"created ({msg})")
    f = ds.find_shortcut("123")
    check(f is not None and f.parent == apps, "found in ~/.local/share/applications")
    text = f.read_text() if f else ""
    check("Exec=steam steam://rungameid/123\n" in text, "starts through Steam, like ACCELA")
    check("Icon=steam_icon_123\n" in text, "uses the SteamGridDB icon name ACCELA used")
    check("/" not in f.name, "slash removed from the file name")
    check(all((icons / f"{s}x{s}/apps/steam_icon_123.png").is_file() for s in ds.ICON_SIZES),
          "icon written in every hicolor size")

    print("=== no icon falls back to the Steam icon ===")
    ok, _ = ds.create_shortcut("12", "Other")
    check(ok and "Icon=steam\n" in ds.find_shortcut("12").read_text(), "Icon=steam without SteamGridDB art")

    print("=== exact AppID match ===")
    check(ds.find_shortcut("1") is None, "AppID 1 does not match 12 or 123")
    ok, _ = ds.remove_shortcut("12")
    check(ok and not ds.has_shortcut("12"), "12 removed")
    check(ds.has_shortcut("123"), "123 kept when 12 is removed")

    print("=== refresh keeps one file ===")
    ds.create_shortcut("123", "Renamed")
    check(len(list(apps.glob("*.desktop"))) == 1, "re-creating rewrites the same file")

    print("=== remove ===")
    ok, _ = ds.remove_shortcut("123")
    check(ok and not ds.has_shortcut("123"), "shortcut removed")
    check(not any(icons.rglob("steam_icon_123.png")), "icons removed")

    print("=== bad input ===")
    check(not ds.create_shortcut("abc", "x")[0], "non-numeric AppID refused")

    if FAILS:
        print(f"FAILED ({len(FAILS)}): {FAILS}")
        return 1
    print("ALL DESKTOP SHORTCUT TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
