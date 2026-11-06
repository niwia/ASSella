"""
executable_scanner.py - Scans game directories for launch executables.

Provides categorized executable scanning (Root vs Subdirectory) with filtering
for redistributables, uninstallers, and crash reporting tools.
"""

from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

IGNORED_DIR_NAMES = {
    "_commonredist",
    "directx",
    "vcredist",
    "dotnet",
    "crashreportclient",
    "easyanticheat",
    "battleye",
    "installer",
    "installers",
    "redist",
    "support",
    "__pycache__",
    ".git",
    ".svn",
}

IGNORED_EXE_PREFIXES = (
    "unitycrashhandler",
    "crashreportclient",
    "dxsetup",
    "vcredist",
    "dotnet",
    "easyanticheat",
    "eac_",
    "unins",
    "uninstall",
    "cleanup",
    "vc_redist",
    "dotnetfx",
    "physx",
    "setup_",
)


def format_size(size_bytes: int) -> str:
    """Format file size in bytes to a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def is_ignored_exe(filename: str) -> bool:
    """Return True if an executable file is a known installer or crash tool."""
    lower = filename.lower()
    if not lower.endswith(".exe"):
        return True
    return any(lower.startswith(p) for p in IGNORED_EXE_PREFIXES)


def scan_game_executables(
    install_path: Union[str, Path],
    max_depth: int = 3,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Scan a game directory for candidate executables.

    Returns:
        (root_executables, subdir_executables) where each item is a dict with:
        name, relative_path, path, size_bytes, size_str, is_root
    """
    if not install_path:
        return [], []

    p = Path(install_path).resolve()
    if not p.is_dir():
        return [], []

    root_exes: List[Dict[str, Any]] = []
    subdir_exes: List[Dict[str, Any]] = []

    # 1. Root executables
    try:
        for entry in p.iterdir():
            if entry.is_file() and not is_ignored_exe(entry.name):
                try:
                    sz = entry.stat().st_size
                except OSError:
                    sz = 0
                root_exes.append({
                    "name": entry.name,
                    "relative_path": entry.name,
                    "path": str(entry),
                    "size_bytes": sz,
                    "size_str": format_size(sz),
                    "is_root": True,
                })
    except (PermissionError, OSError):
        pass

    # 2. Subdirectory executables
    def _walk_subdirs(current_dir: Path, current_depth: int) -> None:
        if current_depth > max_depth:
            return
        try:
            for item in current_dir.iterdir():
                if item.name.startswith("."):
                    continue
                if item.is_dir():
                    if item.name.lower() in IGNORED_DIR_NAMES:
                        continue
                    _walk_subdirs(item, current_depth + 1)
                elif item.is_file() and not is_ignored_exe(item.name):
                    try:
                        sz = item.stat().st_size
                    except OSError:
                        sz = 0
                    rel = item.relative_to(p).as_posix()
                    subdir_exes.append({
                        "name": item.name,
                        "relative_path": rel,
                        "path": str(item),
                        "size_bytes": sz,
                        "size_str": format_size(sz),
                        "is_root": False,
                    })
        except (PermissionError, OSError):
            pass

    try:
        for entry in p.iterdir():
            if entry.is_dir() and not entry.name.startswith("."):
                if entry.name.lower() not in IGNORED_DIR_NAMES:
                    _walk_subdirs(entry, 1)
    except (PermissionError, OSError):
        pass

    root_exes.sort(key=lambda x: x["name"].lower())
    subdir_exes.sort(key=lambda x: x["relative_path"].lower())
    return root_exes, subdir_exes

