"""
EOS Detector Module

Detects whether a Steam game uses Epic Online Services (EOS) by checking if
EOSSDK-Win64-Shipping.dll (or the renamed EOSSDK-Win64-Shipping.yes) is present.
Also handles hash-based detection and applying/removing the EOS proxy.
"""

import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class EOSDetector:
    """Class to detect and manage Epic Online Services (EOS) proxy usage for a Steam game."""

    _bundled_proxy_hash_cache: Optional[str] = None

    @staticmethod
    def get_file_sha256(file_path: Union[str, Path]) -> Optional[str]:
        """Calculate SHA-256 hash of a file."""
        try:
            p = Path(file_path)
            if not p.is_file():
                return None
            h = hashlib.sha256()
            with open(p, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest()
        except Exception as e:
            logger.debug(f"Failed to calculate SHA-256 for {file_path}: {e}")
            return None

    @classmethod
    def get_proxy_dll_hash(cls, proxy_src_path: Optional[Union[str, Path]] = None) -> Optional[str]:
        """Get the SHA-256 hash of the bundled proxy DLL."""
        if proxy_src_path is None:
            if cls._bundled_proxy_hash_cache is not None:
                return cls._bundled_proxy_hash_cache
            try:
                from utils.paths import Paths
                proxy_src_path = Paths.deps("EOSSDK-Win64-Shipping.dll")
            except Exception:
                return None

        src_path = Path(proxy_src_path)
        if not src_path.exists():
            return None

        h = cls.get_file_sha256(src_path)
        if proxy_src_path is None or str(proxy_src_path).endswith("deps/EOSSDK-Win64-Shipping.dll"):
            cls._bundled_proxy_hash_cache = h
        return h

    @staticmethod
    def get_eos_dll_paths(game_directory: Union[str, Path], max_depth: int = 4) -> List[Path]:
        """Find all instances of EOSSDK-Win64-Shipping.dll or EOSSDK-Win64-Shipping.yes in the game folder."""
        dir_path = Path(game_directory)
        found_paths = []
        if not dir_path.exists() or not dir_path.is_dir():
            return found_paths

        PRUNE_DIRS = {
            ".depotdownloader", "__pycache__", "content", "paks", "pak", "assets",
            "sound", "sounds", "audio", "music", "media", "video", "videos", "movies",
            "textures", "cache", "saves", "screenshots", "shadercache", "streamingassets",
            "node_modules", ".git"
        }

        base_depth = len(dir_path.parts)
        try:
            for root, dirs, files in os.walk(dir_path):
                rel_depth = len(Path(root).parts) - base_depth
                if rel_depth >= max_depth:
                    dirs[:] = []
                    continue

                # Prune non-binary asset and cache directories in-place
                dirs[:] = [d for d in dirs if d.lower() not in PRUNE_DIRS and not d.startswith(".")]

                for file in files:
                    if file.lower() in ("eossdk-win64-shipping.dll", "eossdk-win64-shipping.yes"):
                        found_paths.append(Path(root) / file)
        except Exception:
            pass

        return found_paths

    @classmethod
    def get_proxy_status(cls, game_directory: Union[str, Path], proxy_src_path: Optional[Union[str, Path]] = None) -> str:
        """
        Determine the EOS proxy state for a given game directory.

        Returns:
            "active"   - Proxy is applied (.yes exists and in-game .dll matches proxy hash)
            "stale"    - Proxy was previously applied (.yes exists, but .dll does NOT match proxy hash e.g. after game update)
            "inactive" - Original EOS DLL is present (.yes does not exist)
            "none"     - No EOS DLLs found in the game directory
        """
        dll_paths = cls.get_eos_dll_paths(game_directory)
        if not dll_paths:
            return "none"

        proxy_hash = cls.get_proxy_dll_hash(proxy_src_path)

        # Group detected files by their parent directory
        dirs_with_eos: Dict[Path, Dict[str, Path]] = {}
        for p in dll_paths:
            parent = p.parent
            if parent not in dirs_with_eos:
                dirs_with_eos[parent] = {}
            if p.name.lower() == "eossdk-win64-shipping.yes" or p.suffix.lower() == ".yes":
                dirs_with_eos[parent]["yes"] = p
            elif p.name.lower() == "eossdk-win64-shipping.dll" or p.suffix.lower() == ".dll":
                dirs_with_eos[parent]["dll"] = p

        has_any_yes = any("yes" in files for files in dirs_with_eos.values())
        if not has_any_yes:
            has_any_dll = any("dll" in files for files in dirs_with_eos.values())
            return "inactive" if has_any_dll else "none"

        # If .yes exists in any directory, check if .dll matches the proxy hash
        for parent, files in dirs_with_eos.items():
            if "yes" in files:
                dll_file = files.get("dll")
                if not dll_file or not dll_file.exists():
                    return "stale"
                if proxy_hash:
                    dll_hash = cls.get_file_sha256(dll_file)
                    if dll_hash != proxy_hash:
                        return "stale"

        return "active"

    @classmethod
    def apply_proxy(cls, game_directory: Union[str, Path], proxy_src_path: Optional[Union[str, Path]] = None) -> bool:
        """
        Apply or re-apply the EOS proxy DLL.
        Renames original/updated EOSSDK-Win64-Shipping.dll to EOSSDK-Win64-Shipping.yes
        and copies the bundled proxy in place.
        """
        if proxy_src_path is None:
            try:
                from utils.paths import Paths
                proxy_src_path = Paths.deps("EOSSDK-Win64-Shipping.dll")
            except Exception:
                return False

        proxy_src = Path(proxy_src_path)
        if not proxy_src.exists():
            logger.error(f"Bundled proxy DLL not found at: {proxy_src}")
            return False

        dir_path = Path(game_directory)
        if not dir_path.exists() or not dir_path.is_dir():
            return False

        applied = False
        try:
            for root, _, files in os.walk(dir_path):
                if ".DepotDownloader" in Path(root).parts:
                    continue

                dll_target = Path(root) / "EOSSDK-Win64-Shipping.dll"
                yes_target = Path(root) / "EOSSDK-Win64-Shipping.yes"

                found_dll = None
                found_yes = None
                for file in files:
                    if file.lower() == "eossdk-win64-shipping.dll":
                        found_dll = Path(root) / file
                    elif file.lower() == "eossdk-win64-shipping.yes":
                        found_yes = Path(root) / file

                if found_dll:
                    if found_yes and found_yes.exists():
                        try:
                            found_yes.unlink()
                        except Exception as e:
                            logger.warning(f"Could not remove stale {found_yes}: {e}")
                    found_dll.rename(yes_target)
                    shutil.copy2(proxy_src, dll_target)
                    applied = True
                elif found_yes and not found_dll:
                    # Edge case: only .yes exists in folder
                    shutil.copy2(proxy_src, dll_target)
                    applied = True
        except Exception as e:
            logger.error(f"Error applying EOS proxy in {game_directory}: {e}", exc_info=True)
            return False

        return applied

    @classmethod
    def remove_proxy(cls, game_directory: Union[str, Path]) -> bool:
        """
        Remove the EOS proxy and restore the original DLL from .yes backup.
        """
        dir_path = Path(game_directory)
        if not dir_path.exists() or not dir_path.is_dir():
            return False

        removed = False
        try:
            for root, _, files in os.walk(dir_path):
                if ".DepotDownloader" in Path(root).parts:
                    continue

                dll_target = Path(root) / "EOSSDK-Win64-Shipping.dll"
                found_dll = None
                found_yes = None
                for file in files:
                    if file.lower() == "eossdk-win64-shipping.dll":
                        found_dll = Path(root) / file
                    elif file.lower() == "eossdk-win64-shipping.yes":
                        found_yes = Path(root) / file

                if found_yes:
                    if found_dll and found_dll.exists():
                        try:
                            found_dll.unlink()
                        except Exception as e:
                            logger.warning(f"Could not remove proxy DLL {found_dll}: {e}")
                    found_yes.rename(dll_target)
                    removed = True
        except Exception as e:
            logger.error(f"Error removing EOS proxy in {game_directory}: {e}", exc_info=True)
            return False

        return removed

    @classmethod
    def detect_eos(cls, appid: Union[int, str], game_directory: Optional[Union[str, Path]] = None) -> Dict[str, Union[bool, str, List[str]]]:
        """Check if EOS is used by scanning for EOSSDK-Win64-Shipping.dll/yes files."""
        if not game_directory:
            return {
                "uses_eos": False,
                "method": "none",
                "detected_files": [],
                "details": "No game directory provided for local file scan."
            }

        dll_paths = cls.get_eos_dll_paths(game_directory)
        if dll_paths:
            rel_paths = []
            dir_path = Path(game_directory)
            for p in dll_paths:
                try:
                    rel_paths.append(str(p.relative_to(dir_path)))
                except ValueError:
                    rel_paths.append(str(p))
            
            return {
                "uses_eos": True,
                "method": "files",
                "detected_files": rel_paths,
                "details": f"Detected EOS binaries: {', '.join(rel_paths)}"
            }

        return {
            "uses_eos": False,
            "method": "none",
            "detected_files": [],
            "details": "EOSSDK-Win64-Shipping.dll/yes not found in game directory."
        }
