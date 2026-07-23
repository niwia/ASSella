"""
DepotKeyMigrationTask — One-time background migration to populate depot_keys.db
from all existing cached hubcap_manifests/*.zip files that contain a .lua file.

This runs once on first boot after Smart Update Mode is introduced.
Progress is reported via the progress signal so it shows in the main window log.
"""

import logging
import re
import zipfile
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from managers.depot_key_manager import DepotKeyManager
from utils.helpers import get_base_path
from utils.settings import get_settings

logger = logging.getLogger(__name__)


class DepotKeyMigrationTask(QObject):
    """
    Scans all accela_fetch_*.zip files in hubcap_manifests/, extracts depot
    AES keys and AppTokens from any embedded .lua files, and persists them
    to depot_keys.db via DepotKeyManager.

    Signals:
        progress(str)          — Step-by-step log shown in the main window pager
        finished(int, int)     — (apps_migrated, apps_skipped) when done
    """

    progress = pyqtSignal(str)
    finished = pyqtSignal(int, int)

    _LUA_APPID_RE = re.compile(r"addappid\s*\(\s*(\d+)", re.IGNORECASE)
    _LUA_DEPOT_RE = re.compile(
        r'addappid\s*\(\s*(\d+)\s*,[^,]+,\s*"([a-fA-F0-9]+)"', re.IGNORECASE
    )
    _LUA_TOKEN_RE = re.compile(
        r'addtoken\s*\(\s*\d+\s*,\s*"([^"]+)"', re.IGNORECASE
    )

    def run(self) -> None:
        dkm = DepotKeyManager()
        settings = get_settings()

        # Check if already migrated
        status = dkm.migration_status()
        if status["apps"] > 0:
            logger.info(
                f"[Migration] depot_keys.db already populated "
                f"({status['apps']} apps, {status['keys']} keys) — skipping migration."
            )
            self.progress.emit(
                f"[Depot Key Cache] Already populated: "
                f"{status['apps']} game(s), {status['keys']} key(s) — no migration needed."
            )
            self.finished.emit(0, status["apps"])
            return

        manifests_dir = get_base_path() / "hubcap_manifests"
        if not manifests_dir.exists():
            self.progress.emit("[Depot Key Cache] No hubcap_manifests directory found — nothing to migrate.")
            self.finished.emit(0, 0)
            return

        # Only look at primary zips (no _build_ backup zips)
        all_zips = sorted(manifests_dir.glob("accela_fetch_*.zip"))
        primary_zips = [z for z in all_zips if "_build_" not in z.name]

        if not primary_zips:
            self.progress.emit("[Depot Key Cache] No cached manifest zips found.")
            self.finished.emit(0, 0)
            return

        self.progress.emit(
            f"[Depot Key Cache] Starting migration: scanning {len(primary_zips)} cached zip(s)..."
        )
        logger.info(f"[Migration] Starting — {len(primary_zips)} primary zips to scan")

        migrated = 0
        skipped = 0

        for zip_path in primary_zips:
            appid = zip_path.stem.replace("accela_fetch_", "")
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    lua_files = [f for f in zf.namelist() if f.endswith(".lua")]
                    if not lua_files:
                        logger.debug(f"[Migration] {zip_path.name}: no .lua file — skipping")
                        skipped += 1
                        continue

                    lua_content = zf.read(lua_files[0]).decode("utf-8", errors="replace")

                # Extract appid from LUA
                appid_match = self._LUA_APPID_RE.search(lua_content)
                real_appid = appid_match.group(1) if appid_match else appid

                # Extract all depot keys: addappid(<depot_id>, ..., "<key>")
                depot_keys = {}
                for match in self._LUA_DEPOT_RE.finditer(lua_content):
                    depot_id = match.group(1)
                    key = match.group(2)
                    if key and str(depot_id) != str(real_appid):
                        depot_keys[depot_id] = key

                # Extract AppToken: addtoken(<appid>, "<token>")
                token_match = self._LUA_TOKEN_RE.search(lua_content)
                app_token = token_match.group(1) if token_match else None

                if not depot_keys:
                    logger.debug(f"[Migration] {zip_path.name}: no depot keys found in LUA — skipping")
                    skipped += 1
                    continue

                # Persist to depot_keys.db
                dkm.save_depot_keys(real_appid, depot_keys)
                if app_token:
                    dkm.save_app_token(real_appid, app_token)

                migrated += 1
                token_str = "with token" if app_token else "no token"
                logger.info(
                    f"[Migration] {zip_path.name}: migrated AppID {real_appid} — "
                    f"{len(depot_keys)} depot key(s), {token_str}"
                )
                self.progress.emit(
                    f"[Depot Key Cache] Migrated: AppID {real_appid} — "
                    f"{len(depot_keys)} key(s) {token_str}"
                )

            except zipfile.BadZipFile:
                logger.warning(f"[Migration] {zip_path.name}: corrupt zip — skipping")
                skipped += 1
            except Exception as e:
                logger.error(f"[Migration] {zip_path.name}: failed — {e}", exc_info=True)
                skipped += 1

        # Mark migration as done
        settings.setValue("depot_key_migration_done", True)

        final_status = dkm.migration_status()
        summary = (
            f"[Depot Key Cache] Migration complete: "
            f"{migrated} game(s) migrated, {skipped} skipped. "
            f"DB now has {final_status['apps']} app(s), "
            f"{final_status['keys']} key(s), {final_status['tokens']} token(s)."
        )
        logger.info(f"[Migration] {summary}")
        self.progress.emit(summary)
        self.finished.emit(migrated, skipped)
