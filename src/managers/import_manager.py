"""
ImportManager — Handles scanning, validating, and importing user-provided lua/manifest files.

This module provides the core logic for "Import Mode", allowing users to bring
their own .lua and/or .manifest files for games not yet in their library.

Key operations:
  - scan_unregistered_luas(): Find lua files in cached_luas/ not in depot_keys.db
  - parse_lua_file(): Parse a .lua file and extract depot keys, manifest GIDs, app token
  - check_lua_staleness(): Compare lua manifest GIDs against live Steam PICS
  - import_lua_file(): Full import workflow (parse → inject DB → detect counterpart)
  - resolve_missing_counterpart(): Fetch manifests via generate or full manifest API
"""

import io
import logging
import re
import time
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.helpers import get_base_path
from utils.settings import get_settings

try:
    from managers.depot_key_manager import DepotKeyManager
except ImportError:
    DepotKeyManager = None

logger = logging.getLogger(__name__)


class ImportManager:
    """Handles scanning, validating, and importing user-provided lua/manifest files."""

    def __init__(self):
        self._lua_dir = get_base_path() / "cached_luas"
        self._manifests_dir = get_base_path() / "hubcap_manifests"
        self._dkm = DepotKeyManager() if DepotKeyManager else None

    # ─────────────────────────────────────────────────────────────
    # Scanning
    # ─────────────────────────────────────────────────────────────

    def scan_unregistered_luas(self) -> List[dict]:
        """
        Scan cached_luas/ for lua files whose appid is NOT already in depot_keys.db.

        Returns a list of dicts:
          {appid, lua_path, has_manifest_zip, game_name, depot_count}
        """
        results = []
        if not self._lua_dir.exists():
            logger.info("[ImportManager] cached_luas/ directory not found, nothing to scan")
            return results

        if not self._dkm:
            logger.error("[ImportManager] DepotKeyManager not available")
            return results

        cached_appids = set(self._dkm.get_all_cached_appids())
        logger.info(f"[ImportManager] {len(cached_appids)} appid(s) already in depot_keys.db")

        for lua_path in sorted(self._lua_dir.glob("*.lua")):
            try:
                parsed = self.parse_lua_file(lua_path)
                if not parsed or not parsed.get("appid"):
                    continue

                appid = parsed["appid"]

                # Skip luas that are just named accela_XXX (duplicates)
                if lua_path.stem.startswith("accela_"):
                    continue

                if appid in cached_appids:
                    continue

                # Check if manifest zip exists
                has_zip = self._find_manifest_zip(appid) is not None

                results.append({
                    "appid": appid,
                    "lua_path": str(lua_path),
                    "has_manifest_zip": has_zip,
                    "game_name": parsed.get("game_name", f"App {appid}"),
                    "depot_count": len(parsed.get("depot_keys", {})),
                })
                logger.info(
                    f"[ImportManager] Found unregistered lua: {lua_path.name} "
                    f"(AppID {appid}, {len(parsed.get('depot_keys', {}))} depot keys, "
                    f"zip={'YES' if has_zip else 'NO'})"
                )
            except Exception as e:
                logger.warning(f"[ImportManager] Failed to parse {lua_path.name}: {e}")

        logger.info(f"[ImportManager] Scan complete: {len(results)} unregistered lua(s) found")
        return results

    # ─────────────────────────────────────────────────────────────
    # Parsing
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def parse_lua_file(lua_path: Path) -> Optional[dict]:
        """
        Parse a .lua file and extract structured data.

        Returns:
          {appid, game_name, depot_keys: {depot_id: aes_key},
           app_token, manifest_gids: {depot_id: gid}}
        or None on failure.
        """
        try:
            text = Path(lua_path).read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.error(f"[ImportManager] Cannot read {lua_path}: {e}")
            return None

        result = {
            "appid": None,
            "game_name": None,
            "depot_keys": {},
            "app_token": None,
            "manifest_gids": {},
        }

        all_app_matches = list(re.finditer(r"addappid\((.*?)\)(.*)", text, re.IGNORECASE))
        if not all_app_matches:
            return None

        first_app_match = all_app_matches.pop(0)
        first_app_args = first_app_match.group(1).strip()
        args_list = [arg.strip().strip('"').strip("'") for arg in first_app_args.split(",")]
        if not args_list or not args_list[0]:
            return None
        result["appid"] = args_list[0]

        # Extract game name from first app comment
        comment_part = first_app_match.group(2)
        game_name_match = re.search(r"--\s*(.*)", comment_part)
        if game_name_match:
            result["game_name"] = game_name_match.group(1).strip()

        # Parse subsequent matches as depots
        for match in all_app_matches:
            args_str = match.group(1).strip()
            args = [arg.strip().strip('"').strip("'") for arg in args_str.split(",")]
            did = args[0]
            if len(args) > 2 and args[2]:
                result["depot_keys"][did] = args[2]

        # Extract app token
        tok_match = re.search(
            r'addtoken\(\s*\d+\s*,\s*["\']([^"\']+)["\']',
            text, re.IGNORECASE
        )
        if tok_match:
            result["app_token"] = tok_match.group(1)

        # Extract manifest GIDs
        for m in re.finditer(
            r'setManifestid\(\s*(\d+)\s*,\s*"([^"]+)"',
            text, re.IGNORECASE
        ):
            result["manifest_gids"][m.group(1)] = m.group(2)

        if not result.get("game_name"):
            # Try extracting from comment header
            header_match = re.search(r'^--\s*\d+.*?\n--\s*(.+)', text)
            if header_match:
                result["game_name"] = header_match.group(1).strip()

        return result

    # ─────────────────────────────────────────────────────────────
    # Staleness Check
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def check_lua_staleness(appid: str, lua_manifest_gids: Dict[str, str]) -> Tuple[bool, dict]:
        """
        Compare lua manifest GIDs against live Steam PICS data.

        Returns:
          (is_fresh: bool, pics_data: dict)

        is_fresh is True if ALL depot manifest GIDs in the lua match PICS.
        If PICS returns no data, we conservatively return is_fresh=False.
        """
        try:
            from core.steam_api import get_depot_info_from_api
            pics_data = get_depot_info_from_api(int(appid))
        except Exception as e:
            logger.error(f"[ImportManager] PICS fetch failed for AppID {appid}: {e}")
            return False, {}

        if not pics_data or not pics_data.get("depots"):
            logger.warning(f"[ImportManager] PICS returned no depot data for AppID {appid}")
            return False, pics_data or {}

        pics_depots = pics_data.get("depots", {})
        matched = 0
        checked = 0

        for depot_id, lua_gid in lua_manifest_gids.items():
            pics_depot = pics_depots.get(str(depot_id), {})
            if not isinstance(pics_depot, dict):
                continue
            pics_gid = pics_depot.get("manifest_id")
            if not pics_gid:
                # Depot not in PICS (might be filtered/shared depot) — skip
                continue
            checked += 1
            if str(lua_gid) == str(pics_gid):
                matched += 1
                logger.debug(f"[ImportManager] Depot {depot_id}: GID matches ({lua_gid})")
            else:
                logger.info(
                    f"[ImportManager] Depot {depot_id}: STALE "
                    f"(lua={lua_gid}, pics={pics_gid})"
                )
                return False, pics_data

        if checked == 0:
            # No depots could be compared — conservatively mark as stale
            logger.warning(
                f"[ImportManager] No comparable depots for AppID {appid} — treating as stale"
            )
            return False, pics_data

        logger.info(
            f"[ImportManager] AppID {appid}: All {matched}/{checked} depots match — FRESH"
        )
        return True, pics_data

    # ─────────────────────────────────────────────────────────────
    # Import Workflow
    # ─────────────────────────────────────────────────────────────

    def import_lua(self, lua_path: Path) -> dict:
        """
        Full import workflow for a single lua file:
          1. Parse lua
          2. Copy to cached_luas/ if not already there
          3. Inject keys + token into depot_keys.db
          4. Check for existing manifest zip
          5. Return status dict

        Returns:
          {appid, game_name, status, needs_api, api_type, error}
          - status: 'ready' | 'needs_generate' | 'needs_manifest' | 'error'
          - api_type: 'generate' | 'manifest' | None
        """
        parsed = self.parse_lua_file(lua_path)
        if not parsed or not parsed.get("appid"):
            return {"status": "error", "error": "Failed to parse lua file — no appid found"}

        appid = parsed["appid"]
        game_name = parsed.get("game_name", f"App {appid}")
        depot_keys = parsed.get("depot_keys", {})
        app_token = parsed.get("app_token")
        manifest_gids = parsed.get("manifest_gids", {})

        # Step 1: Copy lua to cached_luas/ if needed
        target_lua = self._lua_dir / f"{appid}.lua"
        if str(Path(lua_path).resolve()) != str(target_lua.resolve()):
            try:
                self._lua_dir.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy2(str(lua_path), str(target_lua))
                logger.info(f"[ImportManager] Copied lua to {target_lua}")
            except Exception as e:
                logger.warning(f"[ImportManager] Failed to copy lua: {e}")

        # Step 2: Inject depot keys + token into DB
        if self._dkm and depot_keys:
            ts = int(time.time())
            self._dkm.save_depot_keys(appid, depot_keys, timestamp=ts)
            logger.info(f"[ImportManager] Injected {len(depot_keys)} depot key(s) for AppID {appid}")
            if app_token:
                self._dkm.save_app_token(appid, app_token, timestamp=ts)
                logger.info(f"[ImportManager] Injected AppToken for AppID {appid}")

        # Step 3: Check for existing manifest zip
        zip_path = self._find_manifest_zip(appid)
        if zip_path:
            logger.info(f"[ImportManager] Found existing manifest zip: {zip_path}")
            return {
                "appid": appid,
                "game_name": game_name,
                "status": "ready",
                "zip_path": str(zip_path),
                "needs_api": False,
                "api_type": None,
            }

        # Step 4: No manifest zip — check staleness to decide API path
        if not manifest_gids:
            # No manifest GIDs in lua — can't verify staleness, need full fetch
            logger.info(f"[ImportManager] No manifest GIDs in lua for {appid} — need full manifest API")
            return {
                "appid": appid,
                "game_name": game_name,
                "status": "needs_manifest",
                "needs_api": True,
                "api_type": "manifest",
            }

        is_fresh, pics_data = self.check_lua_staleness(appid, manifest_gids)
        if is_fresh:
            # Lua is fresh — use generate API (manifest only, cheaper)
            logger.info(f"[ImportManager] Lua is fresh for {appid} — will use generate API")
            return {
                "appid": appid,
                "game_name": game_name,
                "status": "needs_generate",
                "needs_api": True,
                "api_type": "generate",
                "pics_data": pics_data,
            }
        else:
            # Lua is stale — need full manifest API (lua + manifests)
            logger.info(f"[ImportManager] Lua is stale for {appid} — will use manifest API")
            return {
                "appid": appid,
                "game_name": game_name,
                "status": "needs_manifest",
                "needs_api": True,
                "api_type": "manifest",
            }

    # ─────────────────────────────────────────────────────────────
    # Resolve Missing Counterpart
    # ─────────────────────────────────────────────────────────────

    def resolve_missing_counterpart(
        self, appid: str, api_type: str, branch: str = "public"
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Fetch the missing piece using the appropriate API endpoint.

        Args:
            appid: The Steam AppID
            api_type: 'generate' (manifest only) or 'manifest' (full zip with lua)
            branch: Branch name (default 'public')

        Returns:
            (zip_path, error_message) — zip_path is None on failure
        """
        if api_type == "generate":
            return self._fetch_via_generate(appid, branch)
        elif api_type == "manifest":
            return self._fetch_via_manifest(appid, branch)
        else:
            return None, f"Unknown api_type: {api_type}"

    def _fetch_via_generate(
        self, appid: str, branch: str = "public"
    ) -> Tuple[Optional[str], Optional[str]]:
        """Fetch manifest-only zip via /generate/appmanifest endpoint."""
        try:
            from core.morrenus_api import get_session, BASE_URL

            try:
                from utils.isp_bypass import execute_hubcap_request
            except ImportError:
                execute_hubcap_request = None

            settings = get_settings()
            api_key = settings.value("morrenus_api_key", "", type=str)
            if not api_key:
                return None, "Hubcap API key is not set. Please set it in Settings."

            headers = {"Authorization": f"Bearer {api_key}"}
            url = f"{BASE_URL}/generate/appmanifest/{appid}?branch={branch}"
            logger.info(f"[ImportManager] GET {url}")

            if execute_hubcap_request:
                resp = execute_hubcap_request(
                    get_session(), "GET", url, headers=headers, stream=True, timeout=60
                )
            else:
                import requests
                resp = requests.get(url, headers=headers, stream=True, timeout=60, verify=False)

            resp.raise_for_status()

            # Save the zip
            self._manifests_dir.mkdir(parents=True, exist_ok=True)
            if branch and branch != "public":
                save_path = self._manifests_dir / f"accela_fetch_{appid}_branch_{branch}.zip"
            else:
                save_path = self._manifests_dir / f"accela_fetch_{appid}.zip"

            save_path.write_bytes(resp.content)
            logger.info(f"[ImportManager] Saved generate zip to {save_path}")
            return str(save_path), None

        except Exception as e:
            error_msg = f"Generate API failed for AppID {appid}: {e}"
            logger.error(f"[ImportManager] {error_msg}")
            return None, error_msg

    def _fetch_via_manifest(
        self, appid: str, branch: str = "public"
    ) -> Tuple[Optional[str], Optional[str]]:
        """Fetch full zip (lua + manifests) via /manifest endpoint."""
        try:
            from core.morrenus_api import download_manifest
            return download_manifest(appid, branch=branch)
        except Exception as e:
            error_msg = f"Manifest API failed for AppID {appid}: {e}"
            logger.error(f"[ImportManager] {error_msg}")
            return None, error_msg

    # ─────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────

    def _find_manifest_zip(self, appid: str) -> Optional[Path]:
        """Find the primary manifest zip for an appid."""
        if not self._manifests_dir.exists():
            return None
        primary = self._manifests_dir / f"accela_fetch_{appid}.zip"
        if primary.exists():
            return primary
        return None
