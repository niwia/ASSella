"""WireGuard configuration profile management and validation."""

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


def validate_wireguard_config(config_text_or_path: Union[str, Path]) -> bool:
    """Validate that a WireGuard configuration file or string contains required sections."""
    if not config_text_or_path:
        return False

    content = ""
    if isinstance(config_text_or_path, Path):
        if not config_text_or_path.is_file():
            return False
        try:
            content = config_text_or_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.debug(f"[Kuroko] Could not read WireGuard config '{config_text_or_path}': {e}")
            return False
    elif isinstance(config_text_or_path, str):
        if "\n" not in config_text_or_path and os.path.isfile(config_text_or_path):
            try:
                content = Path(config_text_or_path).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                logger.debug(f"[Kuroko] Could not read WireGuard config '{config_text_or_path}': {e}")
                return False
        else:
            content = config_text_or_path
    else:
        return False

    lower = content.lower()
    return "[interface]" in lower and "[peer]" in lower


class WireguardProfileManager:
    """Manages imported WireGuard .conf files stored in ~/.local/share/ACCELA/stealth/wireguard/."""

    @staticmethod
    def get_profiles_dir() -> Path:
        """Return ~/.local/share/ACCELA/stealth/wireguard/ with directory mode 0o700."""
        from utils.helpers import get_base_path

        p = Path(get_base_path()) / "stealth" / "wireguard"
        p.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(p, 0o700)
        except OSError as e:
            logger.debug(f"[Kuroko] Could not set 0700 on profiles dir: {e}")
        return p

    @staticmethod
    def validate_wireguard_config(config_text_or_path: Union[str, Path]) -> bool:
        """Validate that a WireGuard .conf path or string contains [Interface] and [Peer] sections."""
        return validate_wireguard_config(config_text_or_path)

    @classmethod
    def list_profiles(cls) -> list[dict]:
        """List all imported .conf profiles sorted alphabetically."""
        p_dir = cls.get_profiles_dir()
        profiles = []
        for f in sorted(p_dir.glob("*.conf")):
            if f.is_file():
                profiles.append({
                    "filename": f.name,
                    "name": f.stem,
                    "path": str(f.resolve()),
                    "size": f.stat().st_size,
                    "modified": f.stat().st_mtime,
                })
        return profiles

    @classmethod
    def import_profile(cls, src_path: Union[str, Path]) -> Optional[dict]:
        """Import a .conf file into storage with strict 0o600 file permissions."""
        src = Path(src_path)
        if not src.is_file():
            return None
        dest_dir = cls.get_profiles_dir()
        base_stem = re.sub(r"[^\w\-.]", "_", src.stem).strip("_") or "wg_profile"
        target_name = f"{base_stem}.conf"
        target_path = dest_dir / target_name
        counter = 1
        while target_path.exists():
            target_name = f"{base_stem}_{counter}.conf"
            target_path = dest_dir / target_name
            counter += 1

        shutil.copy2(src, target_path)
        try:
            os.chmod(target_path, 0o600)
        except OSError as e:
            logger.debug(f"[Kuroko] Could not set 0600 on {target_path}: {e}")

        return {
            "filename": target_name,
            "name": target_path.stem,
            "path": str(target_path.resolve()),
            "size": target_path.stat().st_size,
            "modified": target_path.stat().st_mtime,
        }

    @classmethod
    def import_folder(cls, folder_path: Union[str, Path]) -> list[dict]:
        """Import all .conf files from a directory into profile storage."""
        folder = Path(folder_path)
        if not folder.is_dir():
            return []
        imported: list[dict] = []
        for conf_file in sorted(folder.glob("*.conf")):
            if conf_file.is_file():
                prof = cls.import_profile(conf_file)
                if prof is not None:
                    imported.append(prof)
        return imported

    @classmethod
    def delete_profile(cls, filename: str) -> bool:
        """Permanently delete an imported profile from storage."""
        p_dir = cls.get_profiles_dir()
        clean_name = Path(filename).name
        target = p_dir / clean_name
        if target.is_file():
            try:
                target.unlink()
                logger.info(f"[Kuroko] Deleted WireGuard profile: {clean_name}")
                return True
            except OSError as e:
                logger.error(f"[Kuroko] Failed to delete profile {clean_name}: {e}")
                return False
        return False


__all__ = [
    "WireguardProfileManager",
    "validate_wireguard_config",
]