"""
AppInfo.vdf Editor for Windows Steam

Provides functionality to read and modify the Steam appinfo.vdf binary file
to add/update PICS tokens for applications.

This module is Windows-only and used to add app tokens when running on Windows.
"""

import struct
import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAGIC_VERSION = 0x29


def get_appinfo_path() -> Optional[Path]:
    """
    Get the path to Steam's appinfo.vdf file on Windows.

    Returns:
        Path to appinfo.vdf or None if Steam is not installed
    """
    # Use the existing helper from steam_helpers.py
    try:
        from core.steam_helpers import find_steam_install

        steam_path = find_steam_install()
        if steam_path:
            appinfo_path = Path(steam_path) / "appcache" / "appinfo.vdf"
            if appinfo_path.exists():
                logger.info(f"Found appinfo.vdf at: {appinfo_path}")
                return appinfo_path
    except Exception as e:
        logger.warning(f"Failed to use find_steam_install: {e}")

    # Fallback to common installation paths
    steam_paths = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files"))
        / "Steam"
        / "appcache"
        / "appinfo.vdf",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
        / "Steam"
        / "appcache"
        / "appinfo.vdf",
        Path("C:/Program Files/Steam/appcache/appinfo.vdf"),
        Path("C:/Program Files (x86)/Steam/appcache/appinfo.vdf"),
    ]

    for path in steam_paths:
        if path.exists():
            logger.info(f"Found appinfo.vdf at: {path}")
            return path

    logger.warning("Could not find Steam appinfo.vdf")
    return None


class AppInfoEditor:
    """Editor for Steam appinfo.vdf binary file"""

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.magic: Optional[int] = None
        self.universe: Optional[int] = None
        self.string_table_offset: Optional[int] = None
        self.string_table_data: Optional[bytes] = None
        self.apps: list[dict] = []
        self.modified_apps: set[int] = set()

    def read_appinfo(self) -> bool:
        """Reads appinfo.vdf binary file"""
        try:
            with open(self.filepath, "rb") as f:
                # Read header
                self.magic = struct.unpack("<I", f.read(4))[0]
                self.universe = struct.unpack("<I", f.read(4))[0]

                if self.magic < MAGIC_VERSION:
                    logger.error(f"Unsupported appinfo version: 0x{self.magic:08X}")
                    return False

                self.string_table_offset = struct.unpack("<Q", f.read(8))[0]

                # Read app entries
                while True:
                    start_pos = f.tell()
                    app_id_bytes = f.read(4)

                    if len(app_id_bytes) < 4:
                        break

                    app_id = struct.unpack("<I", app_id_bytes)[0]

                    if app_id == 0:  # End of app list
                        break

                    app_data = {
                        "app_id": app_id,
                        "offset": start_pos,
                        "size": struct.unpack("<I", f.read(4))[0],
                        "info_state": struct.unpack("<I", f.read(4))[0],
                        "last_updated": struct.unpack("<I", f.read(4))[0],
                        "pics_token": struct.unpack("<Q", f.read(8))[0],
                        "sha1_hash": f.read(20),
                        "change_number": struct.unpack("<I", f.read(4))[0],
                        "binary_sha1": f.read(20),
                    }

                    # Read fields

                    # Calculate VDF data size
                    vdf_size = app_data["size"] - 60
                    if vdf_size > 0:
                        vdf_data = f.read(vdf_size)
                        if len(vdf_data) < vdf_size:
                            break
                        app_data["vdf_data"] = vdf_data
                    else:
                        app_data["vdf_data"] = b""

                    self.apps.append(app_data)

                # Read string table
                f.seek(self.string_table_offset)
                self.string_table_data = f.read()

            logger.info(f"Read {len(self.apps)} apps from appinfo.vdf")
            return True

        except Exception as e:
            logger.error(f"Failed to read appinfo.vdf: {e}", exc_info=True)
            return False

    def get_app(self, app_id: int) -> Optional[dict]:
        """Gets app data by ID"""
        for app in self.apps:
            if app["app_id"] == app_id:
                return app
        return None

    def set_token(self, app_id: int, token: str) -> bool:
        """
        Sets new PICS token for existing app or creates new entry.

        Args:
            app_id: The application ID
            token: The PICS token (can be string or int)

        Returns:
            True if token was set/created successfully
        """
        if not isinstance(app_id, int) or not (0 <= app_id <= 0xFFFFFFFF):
            try:
                app_id = int(app_id)
                if not (0 <= app_id <= 0xFFFFFFFF):
                    logger.error(f"Invalid app_id: {app_id} (out of uint32 range)")
                    return False
            except (ValueError, TypeError):
                logger.error(f"Invalid app_id: {app_id}")
                return False

        if isinstance(token, str):
            try:
                token = int(token, 0)
            except ValueError:
                logger.error(f"Cannot parse PICS token for App {app_id}: non-numeric token")
                return False

        if not isinstance(token, int) or not (0 <= token <= 0xFFFFFFFFFFFFFFFF):
            logger.error(f"Invalid PICS token for App {app_id}: must be unsigned 64-bit integer")
            return False

        app = self.get_app(app_id)

        if app:
            # Update existing app
            app["pics_token"] = token
            self.modified_apps.add(app_id)
            logger.info(f"Updated token for App {app_id} (token redacted)")
        else:
            # Create new app entry
            # SHA1 hash placeholder (20 bytes of zeros)
            # Steam will populate this when it updates the app info
            sha1_hash = b"\x00" * 20

            app_data = {
                "app_id": app_id,
                "offset": -1,
                "size": 60,
                "info_state": 0,
                "last_updated": 0,
                "pics_token": token,
                "sha1_hash": sha1_hash,
                "change_number": 0,
                "binary_sha1": b"\x00" * 20,
                "vdf_data": b"",
            }

            # Insert sorted by app_id
            insert_pos = 0
            for i, app in enumerate(self.apps):
                if app["app_id"] > app_id:
                    insert_pos = i
                    break
                insert_pos = i + 1

            self.apps.insert(insert_pos, app_data)
            self.modified_apps.add(app_id)
            logger.info(f"Created new entry for App {app_id} (token set)")

        return True

    def write_appinfo(self, output_path: Optional[Path] = None) -> bool:
        """
        Writes modified appinfo.vdf

        Args:
            output_path: Optional output path (defaults to original file)

        Returns:
            True if write was successful
        """
        if output_path is None:
            output_path = self.filepath

        try:
            # Calculate new string table offset
            new_offset = 16  # Header size

            # Add size of all app entries
            for app in self.apps:
                new_offset += 4 + 4 + 4 + 4 + 8 + 20 + 4 + 20 + len(app["vdf_data"])

            # Add footer (4 bytes for 0x00000000)
            new_offset += 4

            with open(output_path, "wb") as f:
                # Write header
                f.write(struct.pack("<I", self.magic))
                f.write(struct.pack("<I", self.universe))
                f.write(struct.pack("<Q", new_offset))

                # Write apps
                for app in self.apps:
                    f.write(struct.pack("<I", app["app_id"]))
                    f.write(struct.pack("<I", app["size"]))
                    f.write(struct.pack("<I", app["info_state"]))
                    f.write(struct.pack("<I", app["last_updated"]))
                    f.write(struct.pack("<Q", app["pics_token"]))
                    f.write(app["sha1_hash"])
                    f.write(struct.pack("<I", app["change_number"]))
                    f.write(app["binary_sha1"])
                    f.write(app["vdf_data"])

                # Write footer
                f.write(struct.pack("<I", 0))

                # Write string table
                if self.string_table_data:
                    f.write(self.string_table_data)

            logger.info(f"Wrote {len(self.apps)} apps to {output_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to write appinfo.vdf: {e}", exc_info=True)
            return False


def add_token_to_appinfo(appinfo_path: Path, app_id: str, token: str) -> bool:
    """
    Add or update an app token in Steam's appinfo.vdf.

    Args:
        appinfo_path: Path to the appinfo.vdf file
        app_id: The Application ID
        token: The PICS token

    Returns:
        True if token was added/updated successfully
    """
    backup_path = appinfo_path.with_suffix(".vdf.bak")
    backup_created = False
    try:
        if not appinfo_path.exists():
            logger.warning(f"appinfo.vdf not found at {appinfo_path}")
            return False

        # Pre-validate inputs before touching anything
        try:
            app_id_int = int(app_id)
            if not (0 <= app_id_int <= 0xFFFFFFFF):
                logger.error(f"Invalid app_id {app_id}: must be unsigned 32-bit integer")
                return False
            token_int = int(token, 0) if isinstance(token, str) else int(token)
            if not (0 <= token_int <= 0xFFFFFFFFFFFFFFFF):
                logger.error(f"Invalid PICS token for App {app_id}: must be unsigned 64-bit integer")
                return False
        except (ValueError, TypeError) as val_err:
            logger.error(f"Failed to validate app_id or token: {val_err}")
            return False

        # Create backup
        try:
            import shutil

            shutil.copy2(appinfo_path, backup_path)
            backup_created = True
            logger.info(f"Created backup: {backup_path}")
        except Exception as e:
            logger.warning(f"Failed to create backup: {e}")

        # Read, modify, write
        editor = AppInfoEditor(appinfo_path)
        if not editor.read_appinfo():
            return False

        if not editor.set_token(app_id_int, token_int):
            return False

        if editor.modified_apps:
            success = editor.write_appinfo()
            if not success:
                if backup_created and backup_path.exists():
                    try:
                        import shutil
                        shutil.copy2(backup_path, appinfo_path)
                        logger.warning(f"Restored {appinfo_path} from {backup_path} after failed write")
                    except Exception as rb_err:
                        logger.error(f"Failed to restore backup after write failure: {rb_err}")
                return False
            return True

        logger.debug(f"No changes needed for App {app_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to add token to appinfo.vdf: {e}", exc_info=True)
        if backup_created and backup_path.exists():
            try:
                import shutil
                shutil.copy2(backup_path, appinfo_path)
                logger.warning(f"Restored {appinfo_path} from {backup_path} after unexpected error")
            except Exception:
                pass
        return False
