"""
SmartUpdateTask — Assembles game_data for a manifest update using:
  1. Cached depot AES keys + AppToken from DepotKeyManager (SQLite)
  2. Live Steam PICS data (game name, installdir, build ID, depot list)
  3. Hubcap /generate/appmanifest/{appid} (live manifest GIDs, no LUA)

This task is only used when "Smart Update Mode" is enabled in Settings.
It saves one full Hubcap API call per update (no full zip re-download)
while always fetching the latest Steam manifest files on demand.

If cached keys are missing or PICS reveals new depots, this task signals
needs_full_zip=True so the caller falls back to the old endpoint.
"""

import io
import logging
import time
import zipfile
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from managers.depot_key_manager import DepotKeyManager
from core.steam_api import get_depot_info_from_api
from core.morrenus_api import get_session, BASE_URL, download_manifest
from utils.helpers import get_base_path
from utils.settings import get_settings

try:
    from utils.isp_bypass import execute_hubcap_request
except ImportError:
    execute_hubcap_request = None

logger = logging.getLogger(__name__)


class SmartUpdateTask(QObject):
    """
    Assembles a complete game_data dict for an update/install using the
    /generate/appmanifest endpoint + cached depot keys, instead of a full zip download.

    Signals:
        progress(str)            — Human-readable step log (shown in main window pager)
        finished(dict)           — Final game_data dict, ready to pass to TaskManager
        needs_full_zip(str)      — Emitted when fallback to old endpoint is required
                                   (arg is the reason why)
        error(str)               — Emitted on unexpected errors
    """

    progress = pyqtSignal(str)
    finished = pyqtSignal(dict)
    needs_full_zip = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, appid: str, game_name: str = "", branch: str = "public"):
        super().__init__()
        self.appid = str(appid)
        self.game_name = game_name or f"App {appid}"
        self.branch = branch or "public"

    def run(self) -> None:
        logger.info(f"[SmartUpdate] Starting smart update for AppID {self.appid} ({self.game_name})")
        self.progress.emit(f"[Smart Update] Starting for {self.game_name} ({self.appid})...")

        try:
            self._execute()
        except Exception as e:
            logger.error(f"[SmartUpdate] Unexpected error for AppID {self.appid}: {e}", exc_info=True)
            self.error.emit(f"Smart update failed: {e}")

    def _execute(self) -> None:
        dkm = DepotKeyManager()

        # ── STEP 1: Check cached depot keys ────────────────────────────────────
        self.progress.emit(f"[Smart Update] Step 1/4: Checking depot key cache for AppID {self.appid}...")
        cached_keys = dkm.get_depot_keys(self.appid)

        if not cached_keys:
            reason = (
                f"AppID {self.appid} has no cached depot keys. "
                "A full manifest fetch is needed at least once to extract the LUA keys."
            )
            logger.warning(f"[SmartUpdate] {reason}")
            self.progress.emit(f"[Smart Update] WARNING: {reason}")
            self.needs_full_zip.emit(reason)
            return

        logger.info(f"[SmartUpdate] Found {len(cached_keys)} cached depot key(s) for AppID {self.appid}")
        self.progress.emit(
            f"[Smart Update] Step 1 OK: {len(cached_keys)} depot key(s) cached "
            f"({', '.join(cached_keys.keys())})"
        )

        cached_token = dkm.get_app_token(self.appid)
        if cached_token:
            logger.info(f"[SmartUpdate] AppToken loaded from depot_keys.db for AppID {self.appid}")
            self.progress.emit(f"[Smart Update] AppToken present in cache.")
        else:
            logger.debug(f"[SmartUpdate] No AppToken cached for AppID {self.appid} (may not be required)")
            self.progress.emit(f"[Smart Update] No AppToken cached (may be OK for public games).")

        # ── STEP 2: Fetch live Steam PICS data ─────────────────────────────────
        self.progress.emit(f"[Smart Update] Step 2/4: Fetching live PICS data from Steam...")
        t0 = time.time()
        try:
            pics_data = get_depot_info_from_api(int(self.appid))
        except Exception as e:
            logger.error(f"[SmartUpdate] PICS fetch failed for AppID {self.appid}: {e}")
            self.progress.emit(f"[Smart Update] ERROR: PICS fetch failed — {e}")
            self.error.emit(f"PICS fetch failed: {e}")
            return

        if not pics_data:
            reason = f"Steam PICS returned no data for AppID {self.appid}"
            logger.warning(f"[SmartUpdate] {reason}")
            self.progress.emit(f"[Smart Update] WARNING: {reason} — falling back to full zip")
            self.needs_full_zip.emit(reason)
            return

        pics_elapsed = time.time() - t0
        game_name = pics_data.get("name") or self.game_name
        installdir = pics_data.get("installdir") or f"App_{self.appid}"
        buildid = pics_data.get("buildid") or ""
        pics_depots = pics_data.get("depots", {})
        pics_depot_ids = {str(k) for k in pics_depots.keys()}

        logger.info(
            f"[SmartUpdate] PICS OK in {pics_elapsed:.2f}s — "
            f"Game: '{game_name}', Build: {buildid}, Depots: {pics_depot_ids}"
        )
        self.progress.emit(
            f"[Smart Update] Step 2 OK ({pics_elapsed:.2f}s): "
            f"'{game_name}' | Build ID: {buildid} | Depots: {sorted(pics_depot_ids)}"
        )

        # ── STEP 3: Check for new depots not in our key cache ──────────────────
        self.progress.emit(f"[Smart Update] Step 3/4: Comparing PICS depots vs cached keys...")
        if dkm.has_new_depots(self.appid, pics_depot_ids):
            logger.info(
                f"[SmartUpdate] Steam PICS reports new depot(s) for AppID {self.appid}. "
                "Attempting 0-token manifest generation with existing LUA first."
            )
            self.progress.emit(
                f"[Smart Update] Step 3: New depot(s) detected — attempting smart update with existing LUA."
            )
        else:
            logger.info(f"[SmartUpdate] No new depots detected — safe to use smart update path")
            self.progress.emit(f"[Smart Update] Step 3 OK: Depot set unchanged — smart path is safe.")

        # ── STEP 4: Fetch live manifests from /generate/appmanifest ───────────
        self.progress.emit(
            f"[Smart Update] Step 4/4: Fetching live manifests from "
            f"/generate/appmanifest/{self.appid}..."
        )
        t0 = time.time()
        manifest_mapping = {}
        try:
            settings = get_settings()
            api_key = settings.value("morrenus_api_key", "", type=str)
            if not api_key:
                raise ValueError("Hubcap API key is not set in Settings")

            headers = {"Authorization": f"Bearer {api_key}"}
            url = f"{BASE_URL}/generate/appmanifest/{self.appid}?branch={self.branch}"
            logger.info(f"[SmartUpdate] GET {url}")

            if execute_hubcap_request:
                resp = execute_hubcap_request(
                    get_session(), "GET", url, headers=headers, stream=True, timeout=60
                )
            else:
                import requests
                resp = requests.get(url, headers=headers, stream=True, timeout=60, verify=False)

            resp.raise_for_status()
            zip_bytes = io.BytesIO(resp.content)
            gen_elapsed = time.time() - t0

            with zipfile.ZipFile(zip_bytes, "r") as zf:
                files_in_zip = zf.namelist()
                logger.info(f"[SmartUpdate] Generate zip contains: {files_in_zip}")
                for filename in files_in_zip:
                    if not filename.endswith(".manifest"):
                        continue
                    stem = filename.replace(".manifest", "")
                    parts = stem.split("_")
                    if len(parts) == 2:
                        depot_id, manifest_gid = parts[0], parts[1]
                        manifest_mapping[depot_id] = manifest_gid
                        logger.info(
                            f"[SmartUpdate] Manifest: Depot {depot_id} -> GID {manifest_gid}"
                        )

                # Save the bundle zip as the primary cached zip for this appid
                self._save_generate_zip(zip_bytes, buildid)

        except Exception as e:
            logger.error(f"[SmartUpdate] Generate endpoint failed for AppID {self.appid}: {e}", exc_info=True)
            self.progress.emit(f"[Smart Update] ERROR: Failed to fetch generate endpoint — {e}")
            self.error.emit(f"Generate endpoint failed: {e}")
            return

        if not manifest_mapping:
            reason = f"Generate endpoint returned no manifest files for AppID {self.appid}"
            logger.error(f"[SmartUpdate] {reason}")
            self.progress.emit(f"[Smart Update] ERROR: {reason}")
            self.error.emit(reason)
            return

        # Verify generated manifest GIDs against expected Steam PICS manifest ID if known
        expected_steam_gid = settings.value(f"latest_steam_manifest_id/{self.appid}", "", type=str)
        if expected_steam_gid and expected_steam_gid not in manifest_mapping.values():
            reason = (
                f"Generate endpoint manifest GIDs {list(manifest_mapping.values())} "
                f"do not match latest Steam PICS manifest GID {expected_steam_gid} for branch '{self.branch}'"
            )
            logger.warning(f"[SmartUpdate] {reason} — falling back to full zip download.")
            self.progress.emit(f"[Smart Update] WARNING: Stale generate bundle — {reason}")
            self.needs_full_zip.emit(reason)
            return

        logger.info(
            f"[SmartUpdate] Generate OK in {gen_elapsed:.2f}s — "
            f"{len(manifest_mapping)} manifest(s): {manifest_mapping}"
        )
        self.progress.emit(
            f"[Smart Update] Step 4 OK ({gen_elapsed:.2f}s): "
            f"{len(manifest_mapping)} manifest(s) fetched."
        )

        # ── Assemble final game_data ───────────────────────────────────────────
        # Build depots dict with cached keys + PICS size metadata
        enriched_depots = {}
        for depot_id, key in cached_keys.items():
            depot_info = {"key": key, "desc": f"Depot {depot_id}"}
            # Enrich with PICS size/oslist if available
            pics_depot = pics_depots.get(str(depot_id), {})
            if isinstance(pics_depot, dict):
                if pics_depot.get("size"):
                    depot_info["size"] = pics_depot["size"]
                if pics_depot.get("oslist"):
                    depot_info["oslist"] = pics_depot["oslist"]
            enriched_depots[depot_id] = depot_info

        game_data = {
            "appid": self.appid,
            "game_name": game_name,
            "installdir": installdir,
            "buildid": buildid,
            "app_token": cached_token,
            "manifests": manifest_mapping,
            "depots": enriched_depots,
            "_smart_update": True,  # Flag so downstream tasks know this came from smart path
        }

        # Persist buildid for UI display
        if buildid:
            get_settings().setValue(f"fetched_buildid/{self.appid}", buildid)

        logger.info(
            f"[SmartUpdate] SUCCESS for AppID {self.appid} — "
            f"game_data assembled with {len(enriched_depots)} depot(s), buildid={buildid}"
        )
        self.progress.emit(
            f"[Smart Update] COMPLETE for '{game_name}' — "
            f"Build {buildid} | {len(enriched_depots)} depot(s) ready."
        )
        self.finished.emit(game_data)

    def _save_generate_zip(self, zip_bytes: io.BytesIO, buildid: str) -> None:
        """
        Saves the generate endpoint bundle as accela_fetch_{appid}.zip (primary)
        and backs up the old one to accela_fetch_{appid}_build_{old_buildid}.zip.
        """
        try:
            manifests_dir = get_base_path() / "hubcap_manifests"
            manifests_dir.mkdir(parents=True, exist_ok=True)
            if self.branch and self.branch != "public":
                save_path = manifests_dir / f"accela_fetch_{self.appid}_branch_{self.branch}.zip"
            else:
                save_path = manifests_dir / f"accela_fetch_{self.appid}.zip"

            settings = get_settings()

            # Backup old zip using old buildid if different from new buildid
            if save_path.exists() and settings.value("save_old_manifests", True, type=bool):
                import os
                old_buildid = settings.value(f"fetched_buildid/{self.appid}", "", type=str)
                if old_buildid and str(old_buildid) != str(buildid):
                    backup_path = manifests_dir / f"accela_fetch_{self.appid}_build_{old_buildid}.zip"
                    if backup_path.exists():
                        backup_path.unlink()
                    try:
                        os.rename(save_path, backup_path)
                        logger.info(f"[SmartUpdate] Backed up old zip (build {old_buildid}) to {backup_path.name}")

                        # Enforce max_old_manifests limit
                        limit = settings.value("max_old_manifests", 3, type=int)
                        backups = sorted(
                            manifests_dir.glob(f"accela_fetch_{self.appid}_*.zip"),
                            key=lambda p: p.stat().st_mtime
                        )
                        for old in backups[:max(0, len(backups) - limit)]:
                            try:
                                os.remove(old)
                                logger.info(f"[SmartUpdate] Deleted old backup: {old.name}")
                            except OSError:
                                pass
                    except OSError as e:
                        logger.warning(f"[SmartUpdate] Could not backup old zip: {e}")

            # Write new zip
            zip_bytes.seek(0)
            with open(save_path, "wb") as f:
                f.write(zip_bytes.read())
            logger.info(f"[SmartUpdate] Saved generate bundle to {save_path}")

        except Exception as e:
            logger.warning(f"[SmartUpdate] Failed to save generate zip: {e}")
