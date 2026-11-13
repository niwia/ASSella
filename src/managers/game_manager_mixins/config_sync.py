"""SLSsteam config synchronization, AppToken extraction, and owned Steam lookup mixin."""

import logging
import re
import zipfile
from pathlib import Path

from utils.helpers import get_base_path
from utils.yaml_config_manager import (
    add_app_token,
    fix_slssteam_config_indentation,
    get_additional_apps,
    get_app_tokens,
    get_user_config_path,
    is_slssteam_mode_enabled,
    remove_additional_app,
)

logger = logging.getLogger(__name__)


class GameManagerConfigSyncMixin:
    """Handles SLSsteam YAML synchronization, AppToken sync, and owned AppID queries."""

    @staticmethod
    def _get_sls_additional_apps() -> set:
        """Return set of AppIDs registered in SLSsteam's AdditionalApps."""
        try:
            cfg_path = get_user_config_path()
            if not cfg_path.exists():
                return set()
            with open(cfg_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            m = re.search(r"AdditionalApps:\s*\n((?:\s*-\s*\d+[^\n]*\n*)+)", content)
            if not m:
                return set()
            return set(re.findall(r"^[ \t]*-[ \t]*(\d+)", m.group(1), re.MULTILINE))
        except Exception as e:
            logger.debug(f"Could not read SLSsteam AdditionalApps: {e}")
            return set()

    @staticmethod
    def _get_owned_steam_appids() -> set:
        """Return set of AppIDs that the user legitimately owns on Steam (via localconfig.vdf apptickets)."""
        try:
            from core.steam_helpers import get_owned_steam_appids

            return get_owned_steam_appids()
        except Exception as e:
            logger.debug(f"Could not read owned Steam AppIDs: {e}")
            return set()

    @staticmethod
    def _fix_slssteam_config():
        """
        Fix indentation of AdditionalApps entries in SLSsteam config.yaml.
        Runs automatically after a scan completes to fix misformatted entries.
        """
        config_path = get_user_config_path()
        if config_path.exists():
            fix_slssteam_config_indentation(config_path)

    def _sync_games_to_slssteam_config(self):
        """
        Sync found games to SLSsteam AdditionalApps if integration is enabled.
        Runs automatically after a scan completes.
        """
        if not is_slssteam_mode_enabled():
            return

        config_path = get_user_config_path()
        if not config_path.exists():
            logger.debug("SLSsteam config.yaml not found, skipping sync")
            return

        from utils.dlc_helpers import sync_dlc_only_sls_config

        added_count = 0
        for game in self.games:
            if not game.get("is_accela_install"):
                continue
            if game.get("is_atom") or game.get("is_vapor"):
                continue
            appid = game.get("appid")
            game_name = game.get("game_name", "")
            if appid and appid not in ("0", "N/A", "unknown"):
                if sync_dlc_only_sls_config(
                    config_path, str(appid), game_name, game_data=game
                ):
                    added_count += 1

        if added_count > 0:
            logger.info(f"Synced {added_count} game(s) to SLSsteam AdditionalApps")

    def _sync_app_tokens_from_manifests(self):
        """
        Check all ZIPs in hubcap_manifests for apptokens
        and add any missing tokens to config.yaml.
        Called after game library scan completes.
        """
        if not is_slssteam_mode_enabled():
            return

        config_path = get_user_config_path()
        if not config_path.exists():
            logger.debug("SLSsteam config.yaml not found, skipping token sync")
            return

        manifests_dir = Path(get_base_path()) / "hubcap_manifests"
        if not manifests_dir.exists():
            logger.debug("hubcap_manifests directory not found")
            return

        existing_tokens = get_app_tokens(config_path)
        logger.debug(f"Found {len(existing_tokens)} existing AppTokens in config")

        zip_pattern = re.compile(r"^accela_fetch_(\d+)\.zip$")
        tokens_added = 0
        tokens_skipped = 0

        try:
            for zip_file in manifests_dir.glob("accela_fetch_*.zip"):
                match = zip_pattern.match(zip_file.name)
                if not match:
                    continue

                app_id = match.group(1)

                if app_id in existing_tokens:
                    tokens_skipped += 1
                    continue

                try:
                    with zipfile.ZipFile(zip_file, "r") as zip_ref:
                        lua_files = [
                            f for f in zip_ref.namelist() if f.endswith(".lua")
                        ]
                        if not lua_files:
                            continue

                        lua_content = zip_ref.read(lua_files[0]).decode("utf-8")

                        token_pattern = r'addtoken\s*\(\s*\d+\s*,\s*"([^"]+)"\s*\)'
                        token_match = re.search(
                            token_pattern, lua_content, re.IGNORECASE
                        )

                        if not token_match:
                            continue

                        app_token = token_match.group(1)

                        if add_app_token(config_path, app_id, app_token):
                            tokens_added += 1
                            logger.info(
                                f"Added missing AppToken for AppID {app_id} from {zip_file.name}"
                            )
                        else:
                            tokens_skipped += 1

                except Exception as e:
                    logger.warning(f"Failed to extract token from {zip_file.name}: {e}")
                    continue

        except Exception as e:
            logger.error(
                f"Error scanning hubcap_manifests for tokens: {e}", exc_info=True
            )
            return

        if tokens_added > 0:
            logger.info(
                f"Synced {tokens_added} missing AppToken(s) from hubcap_manifests"
            )
        if tokens_skipped > 0:
            logger.debug(f"Skipped {tokens_skipped} AppToken(s) that already exist")

    def _scrub_dlc_only_base_appid(self, appid) -> None:
        """
        Remove a DLC-only base AppID from SLSsteam ``AdditionalApps`` if present.
        Self-heals configs where older builds persisted ``dlc_only_mode`` too late.
        """
        appid_str = str(appid or "").strip()
        if not appid_str.isdigit() or appid_str in ("0",):
            return
        try:
            from utils.yaml_config_manager import (
                get_user_config_path,
                get_additional_apps,
                remove_additional_app,
            )
            config_path = get_user_config_path()
            if not config_path or not config_path.exists():
                return
            if appid_str in get_additional_apps(config_path):
                if remove_additional_app(config_path, appid_str):
                    logger.info(
                        f"Removed DLC-only base AppID {appid_str} from AdditionalApps"
                    )
        except Exception as e:
            logger.debug(f"DLC-only config scrub failed for {appid_str}: {e}")