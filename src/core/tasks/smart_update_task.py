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
from core import morrenus_api
from utils.helpers import get_base_path
from utils.settings import get_settings

try:
    from utils.isp_bypass import execute_hubcap_request
except ImportError:
    execute_hubcap_request = None

logger = logging.getLogger(__name__)


class SmartUpdateTask(QObject):
    """
    Assembles a complete game_data dict for an update/install using a smart tiered strategy:
      1. Single Manifest API (/generate/manifest) for single-depot games or delta updates (1,500/day pool)
      2. Bundle Manifest API (/generate/appmanifest) for multi-depot games (100/day pool)
      3. Multi-Single Fallback loop if bundle generation fails or is rate-limited
      4. Classic Full Zip Fallback (/manifest/{appid}) if cloud generation fails (55/day pool)

    Signals:
        progress(str)            — Human-readable step log (shown in main window pager)
        finished(dict)           — Final game_data dict, ready to pass to TaskManager
        needs_full_zip(str)      — Emitted when fallback to classic endpoint is required
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

    @staticmethod
    def _extract_manifest_gid(manifests_dict: Optional[dict], branch: str = "public") -> Optional[str]:
        """Extracts the manifest GID for a given branch from PICS manifests dict."""
        if not isinstance(manifests_dict, dict):
            return None
        entry = manifests_dict.get(branch)
        if entry is None and branch != "public":
            entry = manifests_dict.get("public")
        if isinstance(entry, dict):
            gid = entry.get("gid")
            return str(gid) if gid else None
        elif isinstance(entry, (str, int)):
            return str(entry)
        return None

    @staticmethod
    def _extract_manifest_mapping_from_zip(zip_bytes: io.BytesIO) -> dict:
        """Parses a zip and returns {depot_id: manifest_gid} mapping."""
        mapping = {}
        try:
            zip_bytes.seek(0)
            with zipfile.ZipFile(zip_bytes, "r") as zf:
                for filename in zf.namelist():
                    if not filename.endswith(".manifest"):
                        continue
                    stem = filename.replace(".manifest", "")
                    parts = stem.split("_")
                    if len(parts) == 2:
                        depot_id, manifest_gid = parts[0], parts[1]
                        mapping[depot_id] = manifest_gid
        except Exception as e:
            logger.error(f"Error parsing manifest zip: {e}")
        return mapping

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

        # Use branch-specific build ID from PICS branches dict when available.
        # pics_data["buildid"] is always the *public* branch build ID.
        # pics_data["branches"][self.branch]["buildid"] is the actual branch build.
        branch_bid = ""
        branches_map = pics_data.get("branches", {})
        if isinstance(branches_map, dict) and self.branch in branches_map:
            binfo = branches_map[self.branch]
            if isinstance(binfo, dict):
                branch_bid = str(binfo.get("buildid", ""))
        buildid = branch_bid or pics_data.get("buildid") or ""
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

        # ── STEP 3: Compare PICS depots vs cached keys & saved config ───────────
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

        # Read saved .depot tracking if exists
        saved_depot_ids = set()
        depot_file = get_base_path() / "depots" / f"{self.appid}.depot"
        if depot_file.exists():
            try:
                for line in depot_file.read_text().strip().splitlines():
                    parts = line.split(":", 1)
                    if parts and parts[0].strip():
                        saved_depot_ids.add(parts[0].strip())
            except Exception:
                pass

        # Identify which target depots require live manifests from PICS for this branch
        target_depots = {}  # {depot_id: gid}
        for depot_id in cached_keys.keys():
            depot_id_str = str(depot_id)
            if saved_depot_ids and depot_id_str not in saved_depot_ids:
                continue
            pics_info = pics_depots.get(depot_id_str, {})
            if isinstance(pics_info, dict):
                manifests_dict = pics_info.get("manifests", {})
                gid = self._extract_manifest_gid(manifests_dict, self.branch)
                if gid:
                    target_depots[depot_id_str] = gid

        needed_count = len(target_depots)
        logger.info(
            f"[SmartUpdate] Identified {needed_count} target depot(s) with live PICS GIDs for branch '{self.branch}': {target_depots}"
        )

        # ── STEP 4: Tiered Manifest Generation Strategy ────────────────────────
        t0 = time.time()
        manifest_mapping = {}
        zip_bytes = None

        # ── TIER 0: Local GID Cache Check (Instant 0s start with 0 API calls) ──
        # Check if an existing local zip already contains all needed target manifests with exact matching GIDs
        manifests_dir = get_base_path() / "hubcap_manifests"
        if self.branch and self.branch != "public":
            potential_zip = manifests_dir / f"accela_fetch_{self.appid}_branch_{self.branch}.zip"
        else:
            potential_zip = manifests_dir / f"accela_fetch_{self.appid}.zip"

        if potential_zip.exists() and needed_count > 0:
            try:
                with open(potential_zip, "rb") as pzf:
                    local_zip_bytes = io.BytesIO(pzf.read())
                local_mapping = self._extract_manifest_mapping_from_zip(local_zip_bytes)
                if all(local_mapping.get(d_id) == str(d_gid) for d_id, d_gid in target_depots.items()):
                    logger.info(
                        f"[SmartUpdate] Tier 0 SUCCESS: Exact manifest GID matches found in local cache {potential_zip.name}! "
                        f"Skipping network generation ({len(local_mapping)} manifest(s))."
                    )
                    self.progress.emit("[Smart Update] Local cached manifests are up to date! Skipping network fetch.")
                    zip_bytes = local_zip_bytes
                    manifest_mapping = local_mapping
            except Exception as e:
                logger.debug(f"[SmartUpdate] Tier 0 cache check error: {e}")

        # If Tier 0 did not satisfy all target depots, proceed with remote generation tiers
        if not manifest_mapping or not zip_bytes:
            if needed_count == 1:
                # ── PATH 1: Single Depot Target ──
                # Tier 1A: Try Single Manifest API (1,500/day pool)
                depot_id, gid = next(iter(target_depots.items()))
                self.progress.emit(
                    f"[Smart Update] Step 4/4: Single-depot target ({depot_id}) — fetching via /generate/manifest..."
                )
                logger.info(
                    f"[SmartUpdate] Tier 1A: Attempting single manifest generation for AppID {self.appid}, "
                    f"Depot {depot_id}, GID {gid} (1,500/day pool)..."
                )
                raw_bytes, s_err = morrenus_api.generate_single_manifest(depot_id, gid)
                if raw_bytes and not s_err:
                    logger.info(f"[SmartUpdate] Tier 1A SUCCESS: Single manifest generated for Depot {depot_id} ({len(raw_bytes)} bytes)")
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                        zf.writestr(f"{depot_id}_{gid}.manifest", raw_bytes)
                    zip_bytes = zip_buffer
                    manifest_mapping[depot_id] = gid
                else:
                    # Tier 1B: Fallback to Bundle Generation (100/day pool)
                    logger.warning(
                        f"[SmartUpdate] Tier 1A failed ({s_err}) — "
                        f"Tier 1B: Falling back to bundle generation for AppID {self.appid} (100/day pool)..."
                    )
                    self.progress.emit(f"[Smart Update] Single manifest failed ({s_err}) — trying bundle generation...")
                    bundle_bytes, b_err = morrenus_api.generate_bundle_manifest(self.appid, branch=self.branch)
                    if bundle_bytes and not b_err:
                        logger.info(f"[SmartUpdate] Tier 1B SUCCESS: Bundle manifest generated for AppID {self.appid}")
                        zip_bytes = io.BytesIO(bundle_bytes)
                        manifest_mapping = self._extract_manifest_mapping_from_zip(zip_bytes)
                    else:
                        # Tier 1C: Fallback to Classic Full Zip Download (55/day pool)
                        logger.warning(
                            f"[SmartUpdate] Tier 1B also failed ({b_err}) — "
                            "falling back to classic full zip download..."
                        )
                        reason = f"Generation endpoints failed for AppID {self.appid}: {b_err or s_err}"
                        self.needs_full_zip.emit(reason)
                        return

            elif needed_count >= 2:
                # ── PATH 2: Multi-Depot Target ──
                # Tier 2A: Try Bundle Generation (100/day pool, single roundtrip for all depots)
                self.progress.emit(
                    f"[Smart Update] Step 4/4: Multi-depot target ({needed_count} depots) — fetching via /generate/appmanifest..."
                )
                logger.info(
                    f"[SmartUpdate] Tier 2A: Attempting bundle generation for AppID {self.appid}, "
                    f"branch='{self.branch}' ({needed_count} depots)..."
                )
                bundle_bytes, b_err = morrenus_api.generate_bundle_manifest(self.appid, branch=self.branch)
                if bundle_bytes and not b_err:
                    logger.info(f"[SmartUpdate] Tier 2A SUCCESS: Bundle manifest generated for AppID {self.appid}")
                    zip_bytes = io.BytesIO(bundle_bytes)
                    manifest_mapping = self._extract_manifest_mapping_from_zip(zip_bytes)
                else:
                    # Tier 2B: Fallback to Multi-Single Generation Loop (1,500/day pool)
                    logger.warning(
                        f"[SmartUpdate] Tier 2A failed ({b_err}) — "
                        f"Tier 2B: Falling back to fetching all {needed_count} depots individually via /generate/manifest (1,500/day pool)..."
                    )
                    self.progress.emit(
                        f"[Smart Update] Bundle generation failed — attempting multi-single depot fetch ({needed_count} depots)..."
                    )
                    multi_zip_buffer = io.BytesIO()
                    multi_mapping = {}
                    all_singles_ok = True

                    with zipfile.ZipFile(multi_zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                        for d_id, d_gid in target_depots.items():
                            logger.info(f"[SmartUpdate] Multi-single: Fetching Depot {d_id} (GID {d_gid})...")
                            raw_bytes, s_err = morrenus_api.generate_single_manifest(d_id, d_gid)
                            if raw_bytes and not s_err:
                                zf.writestr(f"{d_id}_{d_gid}.manifest", raw_bytes)
                                multi_mapping[d_id] = d_gid
                            else:
                                logger.error(f"[SmartUpdate] Multi-single failed for Depot {d_id}: {s_err}")
                                all_singles_ok = False
                                break

                    if all_singles_ok and len(multi_mapping) == needed_count:
                        logger.info(f"[SmartUpdate] Tier 2B SUCCESS: Multi-single fetch completed for all {needed_count} depots!")
                        zip_bytes = multi_zip_buffer
                        manifest_mapping = multi_mapping
                    else:
                        # Tier 2C: Fallback to Classic Full Zip Download (55/day pool)
                        logger.warning(
                            f"[SmartUpdate] Tier 2B failed — "
                            "falling back to classic full zip download..."
                        )
                        reason = f"Bundle and multi-single generation failed for AppID {self.appid}: {b_err}"
                        self.needs_full_zip.emit(reason)
                        return

            else:
                # ── PATH 3: Unknown / No Target Depots in PICS -> Try Bundle Generation ──
                self.progress.emit(
                    f"[Smart Update] Step 4/4: Fetching live manifests from /generate/appmanifest/{self.appid}..."
                )
                bundle_bytes, b_err = morrenus_api.generate_bundle_manifest(self.appid, branch=self.branch)
                if bundle_bytes and not b_err:
                    zip_bytes = io.BytesIO(bundle_bytes)
                    manifest_mapping = self._extract_manifest_mapping_from_zip(zip_bytes)
                else:
                    reason = f"Generate bundle failed for AppID {self.appid}: {b_err}"
                    self.needs_full_zip.emit(reason)
                    return

        if not manifest_mapping or not zip_bytes:
            reason = f"Generate endpoint returned no manifest files for AppID {self.appid}"
            logger.error(f"[SmartUpdate] {reason}")
            self.progress.emit(f"[Smart Update] ERROR: {reason}")
            self.needs_full_zip.emit(reason)
            return

        # Save bundle zip to local cache
        self._save_generate_zip(zip_bytes, buildid)
        gen_elapsed = time.time() - t0

        logger.info(
            f"[SmartUpdate] Manifest fetch OK in {gen_elapsed:.2f}s — "
            f"{len(manifest_mapping)} manifest(s): {manifest_mapping}"
        )
        self.progress.emit(
            f"[Smart Update] Step 4 OK ({gen_elapsed:.2f}s): "
            f"{len(manifest_mapping)} manifest(s) fetched."
        )

        enriched_depots = {}
        for depot_id, key in cached_keys.items():
            # Skip depots not tracked in the saved depot config
            depot_id_str = str(depot_id)
            if saved_depot_ids and depot_id_str not in saved_depot_ids:
                logger.debug(f"[SmartUpdate] Skipping untracked depot {depot_id} for AppID {self.appid}")
                continue
            depot_info = {"key": key, "desc": f"Depot {depot_id}"}
            # Enrich with PICS size/oslist if available
            pics_depot = pics_depots.get(depot_id_str, {})
            if isinstance(pics_depot, dict):
                manifests_dict = pics_depot.get("manifests", {})
                branch_entry = manifests_dict.get(self.branch) if isinstance(manifests_dict, dict) else None
                if branch_entry is None and self.branch != "public" and isinstance(manifests_dict, dict):
                    branch_entry = manifests_dict.get("public")

                size_val = None
                if isinstance(branch_entry, dict):
                    size_val = branch_entry.get("size") or branch_entry.get("download")
                if not size_val:
                    size_val = pics_depot.get("maxsize") or pics_depot.get("size")
                if size_val:
                    depot_info["size"] = str(size_val)
                if pics_depot.get("oslist"):
                    depot_info["oslist"] = pics_depot["oslist"]
            enriched_depots[depot_id] = depot_info

        manifests_dir = get_base_path() / "hubcap_manifests"
        if self.branch and self.branch != "public":
            save_path = manifests_dir / f"accela_fetch_{self.appid}_branch_{self.branch}.zip"
        else:
            save_path = manifests_dir / f"accela_fetch_{self.appid}.zip"

        game_data = {
            "appid": self.appid,
            "game_name": game_name,
            "installdir": installdir,
            "buildid": buildid,
            "app_token": cached_token,
            "manifests": manifest_mapping,
            "depots": enriched_depots,
            "branch": self.branch,
            "zip_path": str(save_path),
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
        Saves the generate endpoint bundle as accela_fetch_{appid}.zip.
        Old manifest backup is currently disabled — only the latest is kept.
        """
        try:
            manifests_dir = get_base_path() / "hubcap_manifests"
            manifests_dir.mkdir(parents=True, exist_ok=True)
            if self.branch and self.branch != "public":
                save_path = manifests_dir / f"accela_fetch_{self.appid}_branch_{self.branch}.zip"
            else:
                save_path = manifests_dir / f"accela_fetch_{self.appid}.zip"

            # Old manifest backup disabled — just overwrite
            if save_path.exists():
                save_path.unlink()

            # Write new zip
            zip_bytes.seek(0)
            with open(save_path, "wb") as f:
                f.write(zip_bytes.read())
            logger.info(f"[SmartUpdate] Saved generate bundle to {save_path}")

        except Exception as e:
            logger.warning(f"[SmartUpdate] Failed to save generate zip: {e}")
