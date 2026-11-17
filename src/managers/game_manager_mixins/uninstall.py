"""Game uninstallation, DLC depot file removal, and platform cleanup mixin."""

import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from core.steam_helpers import find_steam_install, get_steam_libraries
from utils.helpers import get_base_path, get_dotnet_env, get_dotnet_path
from utils.lua_parsing import is_placeholder_key, iter_live_matches
from utils.paths import Paths
from utils.settings import get_settings
from utils.yaml_config_manager import (
    get_user_config_path,
    is_greenluma_wrapper_mode_enabled,
)

logger = logging.getLogger(__name__)


class GameManagerUninstallMixin:
    """Handles game uninstall confirmation messages, file removal, and platform cleanup."""

    def get_uninstall_confirmation_message(
        self, game_data, remove_compatdata=False, remove_saves=False
    ):
        """
        Build a confirmation message for uninstalling a game.
        Returns a string with the confirmation message.
        """
        game_name = game_data.get("game_name", "Unknown")
        install_path = game_data.get("install_path")
        library_path = game_data.get("library_path")
        appid = game_data.get("appid", "0")

        if not install_path or not os.path.exists(install_path):
            try:
                from utils.plugin_games import get_atom_game_install_info

                atom_info = get_atom_game_install_info(appid)
                if atom_info:
                    install_path = atom_info.get("common_path") or atom_info.get(
                        "install_path"
                    )
                    if not library_path:
                        library_path = atom_info.get("library_path")
            except Exception as e:
                logger.debug(f"  AT0-M install info unavailable for {appid}: {e}")

        is_accela_install = game_data.get("is_accela_install", False)

        is_dlc_only = False
        if appid and appid not in ("0", "N/A", "unknown"):
            from utils.dlc_helpers import is_dlc_only_mode

            is_dlc_only = (
                is_dlc_only_mode(str(appid))
                or bool(game_data.get("dlc_only"))
                or bool(game_data.get("is_dlc_only"))
            )
            if not is_dlc_only:
                try:
                    from utils.plugin_games import get_plugin_game

                    prec = get_plugin_game(str(appid), active_only=False)
                    if prec and prec.get("dlc_only"):
                        is_dlc_only = True
                except Exception as e:
                    logger.debug(
                        f"[GameManager] Could not check plugin game for DLC-only mode: {e}"
                    )

        if is_dlc_only:
            from utils.dlc_helpers import get_dlc_uninstall_message

            return get_dlc_uninstall_message(game_data)

        confirm_msg = f"Are you sure you want to uninstall '{game_name}'?\n\n"

        if not appid or appid in ("0", "N/A", "unknown"):
            confirm_msg += "WARNING: AppID is unknown for this game.\n"
            if platform.system() == "Linux":
                confirm_msg += "Compatdata and saves WILL NOT be removed.\n"
            elif platform.system() == "Windows" and is_accela_install:
                confirm_msg += "GreenLuma AppList files WILL NOT be removed.\n"
            confirm_msg += "\n"

        # Discover paths for transparency
        compatdata_path = None
        has_saves = False
        userdata_path = None
        if platform.system() == "Linux" and appid and appid not in ("0", "N/A", "unknown"):
            steam_libraries = get_steam_libraries() or []
            if library_path and library_path not in steam_libraries:
                steam_libraries.append(library_path)
            for lib in steam_libraries:
                cp = os.path.join(lib, "steamapps", "compatdata", appid)
                if os.path.exists(cp):
                    compatdata_path = cp
                    break
            for lib in steam_libraries:
                up = os.path.join(lib, "userdata")
                if os.path.exists(up):
                    userdata_path = up
                    try:
                        for user_dir in os.listdir(up):
                            user_path = os.path.join(up, user_dir)
                            if os.path.isdir(user_path):
                                sp = os.path.join(user_path, appid, "remote")
                                if os.path.exists(sp):
                                    has_saves = True
                                    break
                    except OSError as e:
                        logger.debug(f"Steam Cloud save scan incomplete: {e}")
                    if has_saves:
                        break

        confirm_msg += "[Will Be Removed]\n"
        confirm_msg += f"• Game directory: {install_path or 'Unknown'}\n"
        if appid and appid not in ("0", "N/A", "unknown"):
            confirm_msg += f"• Steam app manifest: appmanifest_{appid}.acf\n"
        confirm_msg += "• SLSsteam & AT0-M configuration entries\n"
        if platform.system() == "Linux":
            confirm_msg += "• Application shortcuts & icons\n"

        if remove_compatdata:
            if compatdata_path:
                confirm_msg += f"• Proton prefix (Windows saves): {compatdata_path}\n"
            else:
                confirm_msg += "• Proton prefix (none found on disk)\n"

        if remove_saves:
            if has_saves:
                confirm_msg += f"• Steam Cloud saves: {userdata_path}\n"
            else:
                confirm_msg += "• Steam Cloud saves (none found)\n"

        if (
            platform.system() == "Windows"
            and is_accela_install
            and appid
            and appid not in ("0", "N/A", "unknown")
        ):
            wrapper_mode_enabled = is_greenluma_wrapper_mode_enabled()
            if wrapper_mode_enabled:
                steam_path = find_steam_install()
                if steam_path:
                    app_list_dir = os.path.join(steam_path, "AppList")
                    if os.path.exists(app_list_dir):
                        try:
                            appid_str = str(appid)
                            dlc_ids = self._collect_known_dlc_ids(game_data)
                            if not dlc_ids:
                                dlc_ids = (
                                    self._infer_selected_dlcs_from_applist_and_manifest(
                                        appid_str
                                    )
                                )
                            app_ids_to_check = [appid_str, *dlc_ids]
                            files_by_id = self._find_applist_files_for_ids(
                                app_list_dir, app_ids_to_check
                            )
                            found_appid_files = files_by_id.get(appid_str, [])
                            found_dlc_files = []
                            for dlc_id in dlc_ids:
                                found_dlc_files.extend(files_by_id.get(str(dlc_id), []))
                            if found_appid_files or found_dlc_files:
                                all_gl = found_appid_files + found_dlc_files
                                confirm_msg += f"• GreenLuma AppList files: {', '.join(all_gl)}\n"
                        except OSError as e:
                            logger.debug(f"GreenLuma AppList scan incomplete: {e}")

        confirm_msg += "\n[Will Be Preserved]\n"
        if not remove_compatdata:
            if compatdata_path:
                confirm_msg += f"• Proton prefix (Windows saves): {compatdata_path}\n"
            else:
                confirm_msg += "• Proton prefix: Not found / not created\n"

        if not remove_saves:
            if has_saves:
                confirm_msg += f"• Steam Cloud saves: {userdata_path}\n"
            else:
                confirm_msg += "• Steam Cloud saves: None found\n"

        confirm_msg += "\nThis action cannot be undone!"
        return confirm_msg

    def _delete_single_dlc_depot(self, install_path, base_appid, dlc_appid, manifest_id):
        """Uses DepotDownloader to get files list for DLC depot and deletes only those files."""
        depotdownloader_path = Paths.depot_downloader_dll()
        if not depotdownloader_path.exists():
            logger.warning("DepotDownloader.dll not found, cannot delete DLC files")
            return

        dotnet = get_dotnet_path()
        if not dotnet:
            logger.warning(".NET runtime not found, cannot delete DLC files")
            return

        with tempfile.TemporaryDirectory() as temp_dir:
            cmd = [
                str(dotnet),
                str(depotdownloader_path),
                "-app",
                str(base_appid),
                "-depot",
                str(dlc_appid),
                "-manifest",
                str(manifest_id),
                "-manifest-only",
                "-dir",
                temp_dir,
            ]
            proxy_mgr = None
            try:
                from utils.kuroko import resolve_stealth_proxy_url
                proxy_url, proxy_mgr = resolve_stealth_proxy_url()
                if proxy_url:
                    cmd.extend(["-proxy", proxy_url])

                env = get_dotnet_env()
                subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)
                txt_path = Path(temp_dir) / f"manifest_{dlc_appid}_{manifest_id}.txt"
                if txt_path.exists():
                    start_parsing = False
                    files_deleted = 0
                    dirs_to_check = set()

                    with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line_stripped = line.strip()
                            if not line_stripped:
                                continue
                            if "Size Chunks File SHA" in line_stripped:
                                start_parsing = True
                                continue
                            if not start_parsing:
                                continue

                            parts = line_stripped.split(None, 4)
                            if len(parts) >= 5:
                                name = parts[4].strip()
                                file_path = Path(install_path) / name
                                if file_path.exists() and file_path.is_file():
                                    try:
                                        file_path.unlink()
                                        files_deleted += 1
                                        dirs_to_check.add(file_path.parent)
                                    except OSError as e:
                                        logger.warning(
                                            f"Failed to delete DLC file {file_path}: {e}"
                                        )

                    logger.info(
                        f"Deleted {files_deleted} DLC files for depot {dlc_appid}"
                    )

                    sorted_dirs = sorted(
                        list(dirs_to_check), key=lambda x: len(x.parts), reverse=True
                    )
                    for d in sorted_dirs:
                        if d.exists() and d.is_dir() and not os.listdir(d):
                            try:
                                d.rmdir()
                            except OSError as e:
                                logger.debug(f"  Left empty directory {d} behind: {e}")
            except Exception as e:
                logger.error(
                    f"Failed to fetch manifest and delete files for DLC {dlc_appid}: {e}"
                )
            finally:
                mgr, proxy_mgr = proxy_mgr, None
                if mgr is not None:
                    try:
                        mgr.stop()
                    except Exception as e:
                        logger.debug(f"[Uninstall] Error stopping stealth proxy manager: {e}")

    def _delete_dlc_depot_files(self, install_path, appid, target_dlc_ids=None):
        """Finds and deletes files for DLC depots listed in appid.depot or target_dlc_ids."""
        depot_file = Path(get_base_path()) / "depots" / f"{appid}.depot"
        if not depot_file.exists():
            return

        target_set = {str(d).strip() for d in target_dlc_ids} if target_dlc_ids else None
        remaining_lines = []
        try:
            lines = depot_file.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(":")
                if len(parts) >= 2:
                    dlc_appid = parts[0].strip()
                    manifest_id = parts[1].strip()
                    if target_set is not None and dlc_appid not in target_set:
                        remaining_lines.append(line)
                        continue
                    self._delete_single_dlc_depot(
                        install_path, appid, dlc_appid, manifest_id
                    )
                else:
                    remaining_lines.append(line)

            if target_set is not None:
                if remaining_lines:
                    depot_file.write_text("\n".join(remaining_lines) + "\n")
                else:
                    depot_file.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"Failed to read/delete files from depot config: {e}")

    def uninstall_game(
        self,
        game_data,
        remove_compatdata=False,
        remove_saves=False,
        remove_from_library=False,
        remove_shortcuts=True,
        remove_sls=False,
        wipe_sls_only=False,
        remove_game_files=True,
    ):
        """
        Uninstall a game by removing its folder (or DLC files if dlc_only), ACF file, and optionally compatdata/saves.
        If wipe_sls_only is True ('I bought the game'), delegates to _wipe_sls_only to clean integrations while
        preserving 100% of game files, prefix, and saves.
        Returns (success: bool, error_message: str)
        """
        if wipe_sls_only or game_data.get("wipe_sls_only"):
            from ui.dialogs.library.actions import LibraryActionsMixin
            return LibraryActionsMixin._wipe_sls_only(game_data)

        game_name = game_data.get("game_name", "Unknown")
        install_path = game_data.get("install_path")
        library_path = game_data.get("library_path")
        appid = game_data.get("appid", "0")

        try:
            if not install_path or not os.path.exists(install_path):
                try:
                    from utils.plugin_games import get_atom_game_install_info

                    atom_info = get_atom_game_install_info(appid)
                    if atom_info:
                        install_path = atom_info.get("common_path") or atom_info.get(
                            "install_path"
                        )
                        if not library_path:
                            library_path = atom_info.get("library_path")
                except Exception as e:
                    logger.debug(f"Could not resolve atom install info for {appid}: {e}")

            try:
                from utils.slssteam_integration import (
                    _retry_cancel_flags,
                    _retry_lock,
                )

                with _retry_lock:
                    cancel_event = _retry_cancel_flags.get(str(appid))
                if cancel_event:
                    cancel_event.set()
            except Exception as e:
                logger.debug(f"  Could not cancel an install retry for {appid}: {e}")

            if (
                remove_compatdata
                and platform.system() == "Linux"
                and appid
                and appid not in ("0", "N/A", "unknown")
            ):
                try:
                    from core.native_steam.native_steam_handoff import send_sls_api

                    send_sls_api(f"uninstall|{appid}")
                except Exception as e:
                    logger.debug(f"Error sending SLSsteam uninstall API trigger: {e}")

            is_dlc_only = False
            if appid and appid not in ("0", "N/A", "unknown"):
                from utils.dlc_helpers import is_dlc_only_mode

                is_dlc_only = (
                    is_dlc_only_mode(str(appid))
                    or bool(game_data.get("dlc_only"))
                    or bool(game_data.get("is_dlc_only"))
                )
                if not is_dlc_only:
                    try:
                        from utils.plugin_games import get_plugin_game

                        prec = get_plugin_game(str(appid), active_only=False)
                        if prec and prec.get("dlc_only"):
                            is_dlc_only = True
                    except Exception as e:
                        logger.debug(
                            f"[GameManager] Could not check plugin game for DLC-only mode: {e}"
                        )

            target_dlc_ids = game_data.get("_dlc_uninstall_ids")
            removed_items = []
            preserved_items = []

            if is_dlc_only:
                if install_path and os.path.exists(install_path):
                    self._delete_dlc_depot_files(install_path, appid, target_dlc_ids=target_dlc_ids)
                    removed_items.append("Selected DLC depot files")
                    preserved_items.append(f"Base game directory: {install_path}")
                    try:
                        if os.path.isdir(install_path) and not os.listdir(install_path):
                            shutil.rmtree(install_path, ignore_errors=True)
                            logger.info(
                                f"Removed empty game folder after DLC uninstall: {install_path}"
                            )
                    except Exception as e:
                        logger.warning(
                            f"Could not remove empty folder {install_path}: {e}"
                        )
            else:
                if remove_sls and install_path and os.path.exists(install_path):
                    for m_name in (
                        ".assella",
                        ".ASSELLA",
                        ".DepotDownloader",
                        ".accela",
                        ".ACCELA",
                    ):
                        m_path = os.path.join(install_path, m_name)
                        if os.path.exists(m_path):
                            try:
                                if os.path.isdir(m_path):
                                    shutil.rmtree(m_path)
                                else:
                                    os.remove(m_path)
                                logger.info(f"Removed marker {m_name}: {m_path}")
                            except Exception as e:
                                logger.warning(f"Could not remove marker {m_name}: {e}")

                if not (wipe_sls_only or game_data.get("wipe_sls_only")):
                    if remove_game_files and install_path and os.path.exists(install_path):
                        shutil.rmtree(install_path, ignore_errors=True)
                        if os.path.exists(install_path) and os.listdir(install_path):
                            logger.warning(f"Some files in game folder could not be deleted: {install_path}")
                            removed_items.append(f"Game folder: {install_path} (some files remaining)")
                        else:
                            logger.info(f"Removed game folder: {install_path}")
                            removed_items.append(f"Game folder: {install_path}")
                    elif not remove_game_files and install_path:
                        preserved_items.append(f"Game folder: {install_path} (preserved on disk)")

                if remove_game_files:
                    try:
                        libs = get_steam_libraries() or []
                        if library_path and library_path not in libs:
                            libs.append(library_path)
                        for lib in libs:
                            dl_dir = Path(lib) / "steamapps" / "downloading" / str(appid)
                            if dl_dir.exists():
                                shutil.rmtree(dl_dir, ignore_errors=True)
                                logger.info(f"Removed downloading folder: {dl_dir}")
                                removed_items.append("Temporary download caches")
                            temp_dir = Path(lib) / "steamapps" / "temp" / str(appid)
                            if temp_dir.exists():
                                shutil.rmtree(temp_dir, ignore_errors=True)
                                logger.info(f"Removed temp folder: {temp_dir}")
                    except Exception as e:
                        logger.debug(
                            f"Error cleaning downloading/temp folders for {appid}: {e}"
                        )

                    try:
                        libs = get_steam_libraries() or []
                        if library_path and library_path not in libs:
                            libs.append(library_path)
                        for lib in libs:
                            acf_file = Path(lib) / "steamapps" / f"appmanifest_{appid}.acf"
                            if acf_file.exists():
                                acf_file.unlink(missing_ok=True)
                                logger.info(f"Removed ACF file: {acf_file}")
                                removed_items.append(f"Steam app manifest: {acf_file.name}")
                    except Exception as e:
                        logger.debug(f"Error cleaning ACF file for {appid}: {e}")
                else:
                    manifest_name = f"appmanifest_{appid}.acf" if appid and appid != "0" else "appmanifest (.acf)"
                    preserved_items.append(f"Steam app manifest: {manifest_name} (preserved on disk)")

            depot_file = Path(get_base_path()) / "depots" / f"{appid}.depot"
            depot_has_remaining = (
                is_dlc_only
                and target_dlc_ids
                and depot_file.exists()
                and bool(depot_file.read_text(encoding="utf-8", errors="replace").strip())
            )

            if appid and appid not in ("0", "N/A", "unknown"):
                try:
                    if not depot_has_remaining and depot_file.exists():
                        depot_file.unlink()
                        logger.info(f"Removed depot file: {depot_file}")
                        removed_items.append(f"Depot tracking: {appid}.depot")
                    custom_depot = Path(get_base_path()) / "depots" / f"{appid}_custom.json"
                    if not depot_has_remaining and custom_depot.exists():
                        custom_depot.unlink()
                        logger.info(f"Removed custom depot file: {custom_depot}")
                except Exception as e:
                    logger.warning(
                        f"Failed to remove depot file for appid {appid}: {e}"
                    )

            if platform.system() == "Linux" and not is_dlc_only:
                resolved_compat = game_data.get("_resolved_compatdata")
                data_status = self._remove_linux_game_data(
                    appid, remove_compatdata, remove_saves, resolved_compatdata=resolved_compat
                )
                if remove_compatdata and data_status.get("compatdata_removed"):
                    removed_items.append(f"Proton prefix (Windows saves): {data_status['compatdata_path']}")
                elif data_status.get("compatdata_path"):
                    preserved_items.append(f"Proton prefix (Windows saves): {data_status['compatdata_path']}")
                else:
                    preserved_items.append("Proton prefix: None found / not created")

                if remove_saves and data_status.get("saves_removed"):
                    removed_items.append(f"Steam Cloud userdata saves: {data_status['saves_path']}")
                elif data_status.get("saves_path"):
                    preserved_items.append(f"Steam Cloud userdata saves: {data_status['saves_path']}")
                else:
                    preserved_items.append("Steam Cloud userdata saves: None found")

                if remove_shortcuts:
                    self._remove_linux_shortcuts_and_icons(appid)
                    removed_items.append("Desktop application shortcuts & icons")
                else:
                    preserved_items.append("Desktop application shortcuts & icons (preserved)")

            if appid and appid not in ("0", "N/A", "unknown"):
                # If partial DLC-only uninstall, only prune target_dlc_ids from config
                if is_dlc_only and target_dlc_ids and depot_has_remaining:
                    config_path = get_user_config_path()
                    if config_path.exists():
                        try:
                            from utils.yaml_config_manager import batch_config_edit

                            with batch_config_edit(config_path) as editor:
                                for did in target_dlc_ids:
                                    did_str = str(did).strip()
                                    editor.remove_app(did_str)
                                    editor.remove_fake_app_id(did_str)
                                    editor.remove_app_token(did_str)
                                    editor.remove_depot(did_str, check_shared=True, excluding_appid=str(appid))
                                    editor.remove_key(did_str, check_shared=True, excluding_appid=str(appid))
                            logger.info(f"Removed partial DLC IDs {target_dlc_ids} from SLS config")
                        except Exception as e:
                            logger.warning(f"Error purging partial DLC config entries: {e}")
                    logger.info(f"Partial DLC uninstall complete for {appid}; retaining base game in library.")
                    return True, None

                try:
                    from utils.plugin_games import unregister_plugin_game

                    unregister_plugin_game(str(appid))
                    logger.info(f"Unregistered plugin game {appid} during uninstall")
                except Exception as e:
                    logger.debug(f"Failed to unregister plugin game {appid}: {e}")

                config_path = get_user_config_path()
                if config_path.exists():
                    try:
                        from utils.yaml_config_manager import batch_config_edit

                        with batch_config_edit(config_path) as editor:
                            if is_dlc_only:
                                depot_file = (
                                    Path(get_base_path()) / "depots" / f"{appid}.depot"
                                )
                                if depot_file.exists():
                                    try:
                                        for line in depot_file.read_text(encoding="utf-8", errors="replace").splitlines():
                                            parts = line.split(":")
                                            if parts and parts[0].strip():
                                                did = str(parts[0].strip())
                                                editor.remove_app(did)
                                                editor.remove_fake_app_id(did)
                                                editor.remove_app_token(did)
                                    except Exception as e:
                                        logger.debug(
                                            f"  Could not clean {depot_file} from the SLSsteam config: {e}"
                                        )
                            else:
                                editor.remove_app(str(appid))

                            editor.remove_dlc(str(appid))
                            editor.remove_launch_option(str(appid))
                            editor.remove_fake_app_id(str(appid))
                            editor.remove_app_token(str(appid))
                            for sec in (
                                "GameTitles",
                                "ManifestIds",
                                "DepotBlacklist",
                                "CDKeys",
                                "FakeOffline",
                                "SubscriptionTimestamps",
                                "DenuvoGames",
                            ):
                                editor.remove_secondary_entry(sec, str(appid))
                        logger.info(
                            "Removed appid entries, DlcData, LaunchOptions, FakeAppIds, "
                            "AppTokens, and secondary sections from SLS config"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Error purging SLS config entries for {appid}: {e}"
                        )
            elif platform.system() == "Windows" and not is_dlc_only:
                self._remove_windows_game_data(appid, game_data)

            try:
                settings = get_settings()
                for key in (
                    f"manifest_is_fresh/{appid}",
                    f"fetched_manifest_id/{appid}",
                    f"latest_steam_manifest_id/{appid}",
                    f"installed_branch/{appid}",
                    f"selected_branch/{appid}",
                    f"installed_buildid/{appid}",
                    f"pin_build/{appid}",
                    f"exclude_from_update_all/{appid}",
                    f"auto_update_manifest/{appid}",
                    f"game_update_status/{appid}",
                    f"depot_selection/{appid}",
                    f"dlc_only_mode/{appid}",
                    f"at0m_no_depots/{appid}",
                    f"auto_update_enabled/{appid}",
                    f"last_checked_branch/{appid}",
                    f"last_checked_branch_buildid/{appid}",
                    f"last_checked_local_buildid/{appid}",
                    f"latest_steam_timeupdated/{appid}",
                    f"last_check_reason/{appid}",
                ):
                    settings.remove(key)
                for k in list(settings.allKeys()):
                    if k.startswith(f"installed_buildid/{appid}/") or k.startswith(f"depot_selection/{appid}/"):
                        settings.remove(k)
            except Exception as e:
                logger.debug(f"Failed to clear settings keys for appid {appid}: {e}")

            try:
                from utils.update_status_cache import get_update_status_cache
                usc = get_update_status_cache()
                usc.clear_status(str(appid))
                usc.save()
            except Exception as e:
                logger.debug(f"Failed to clear update status cache for appid {appid}: {e}")

            if appid and appid not in ("0", "N/A", "unknown"):
                removed_items.append("SLSsteam & AT0-M configuration entries")

            summary_lines = [
                f"Uninstalled '{game_name}'.",
                "",
                "[Removed]",
            ]
            summary_lines.extend(f"• {item}" for item in removed_items)
            if preserved_items:
                summary_lines.extend(["", "[Preserved]"])
                summary_lines.extend(f"• {item}" for item in preserved_items)

            self.last_uninstall_summary = "\n".join(summary_lines)

            self.remove_game(appid)
            return True, None

        except Exception as e:
            error_msg = f"Error uninstalling game {game_name}: {e}"
            logger.error(error_msg)
            return False, str(e)

    @staticmethod
    def _remove_linux_game_data(appid, remove_compatdata, remove_saves, resolved_compatdata=None):
        """Remove Linux-specific game data (compatdata and Steam Cloud saves). Returns dict of status."""
        status = {
            "compatdata_removed": False,
            "compatdata_path": None,
            "saves_removed": False,
            "saves_path": None,
        }
        if not appid or appid in ("0", "N/A", "unknown"):
            logger.warning(
                f"Skipping compatdata/saves removal for invalid appid: {appid}"
            )
            return status

        if not str(appid).isdigit():
            logger.error(
                f"Invalid appid format: {appid}. Must be numeric. Skipping compatdata/saves removal."
            )
            return status

        # 1. Proton prefix (compatdata)
        if resolved_compatdata and os.path.exists(resolved_compatdata):
            status["compatdata_path"] = resolved_compatdata
            if remove_compatdata:
                try:
                    shutil.rmtree(resolved_compatdata)
                    status["compatdata_removed"] = True
                    logger.info(f"Removed compatdata: {resolved_compatdata}")
                except Exception as e:
                    logger.warning(
                        f"Failed to remove compatdata {resolved_compatdata}: {e}"
                    )
        else:
            steam_libraries = get_steam_libraries() or []
            for lib in steam_libraries:
                compatdata_path = os.path.join(lib, "steamapps", "compatdata", str(appid))
                if os.path.exists(compatdata_path):
                    status["compatdata_path"] = compatdata_path
                    if remove_compatdata:
                        try:
                            shutil.rmtree(compatdata_path)
                            status["compatdata_removed"] = True
                            logger.info(f"Removed compatdata: {compatdata_path}")
                        except Exception as e:
                            logger.warning(
                                f"Failed to remove compatdata {compatdata_path}: {e}"
                            )
                    break

        # 2. Steam Cloud userdata saves (.../<appid>/remote)
        steam_libraries = get_steam_libraries() or []
        for lib in steam_libraries:
            userdata_path = os.path.join(lib, "userdata")
            if os.path.exists(userdata_path):
                try:
                    for user_dir in os.listdir(userdata_path):
                        user_path = os.path.join(userdata_path, user_dir)
                        if os.path.isdir(user_path):
                            saves_path = os.path.join(user_path, str(appid), "remote")
                            if os.path.exists(saves_path):
                                if not status["saves_path"]:
                                    status["saves_path"] = saves_path
                                if remove_saves:
                                    try:
                                        shutil.rmtree(saves_path)
                                        status["saves_removed"] = True
                                        logger.info(
                                            f"Removed saves for user {user_dir}: {saves_path}"
                                        )
                                    except Exception as e:
                                        logger.warning(f"Failed to remove saves: {e}")
                except Exception as e:
                    logger.debug(f"Error scanning userdata in {userdata_path}: {e}")

        return status

    @staticmethod
    def _remove_linux_shortcuts_and_icons(appid):
        """Remove Linux desktop shortcuts and icons created by ApplicationShortcutsTask."""
        if not appid or appid in ("0", "N/A", "unknown"):
            logger.warning(
                f"Skipping shortcuts/icons removal for invalid appid: {appid}"
            )
            return

        if not str(appid).isdigit():
            logger.error(
                f"Invalid appid format: {appid}. Must be numeric. Skipping shortcuts/icons removal."
            )
            return

        try:
            desktop_files_removed = 0
            desktop_dir = Path.home() / ".local" / "share" / "applications"
            if desktop_dir.exists():
                for desktop_file in desktop_dir.glob("*.desktop"):
                    try:
                        with open(desktop_file, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            if re.search(rf"steam://rungameid/{appid}(?!\d)", content):
                                os.remove(desktop_file)
                                logger.info(f"Removed desktop entry: {desktop_file}")
                                desktop_files_removed += 1
                    except (OSError, UnicodeDecodeError) as e:
                        logger.warning(
                            f"Error reading desktop file {desktop_file}: {e}"
                        )

                if desktop_files_removed == 0:
                    logger.info(f"No desktop entries found for AppID {appid}")

            icon_base = Path.home() / ".local" / "share" / "icons" / "hicolor"
            if icon_base.exists():
                icon_name = f"steam_icon_{appid}.png"
                icons_removed = 0

                for size_dir in icon_base.glob("*x*"):
                    if size_dir.is_dir():
                        apps_dir = size_dir / "apps"
                        if apps_dir.exists():
                            icon_path = apps_dir / icon_name
                            if icon_path.exists():
                                try:
                                    os.remove(icon_path)
                                    logger.info(f"Removed icon: {icon_path}")
                                    icons_removed += 1
                                except OSError as e:
                                    logger.warning(
                                        f"Failed to remove icon {icon_path}: {e}"
                                    )

                if icons_removed == 0:
                    logger.info(f"No icons found for AppID {appid}")

            if desktop_files_removed > 0:
                try:
                    from utils.desktop_shortcuts import _refresh_menus
                    _refresh_menus()
                except Exception as _menu_err:
                    logger.debug(f"[Shortcuts] Failed to refresh desktop menus: {_menu_err}")

        except OSError as e:
            logger.error(
                f"Failed to remove Linux shortcuts and icons for AppID {appid}: {e}"
            )

    def _remove_windows_game_data(self, appid, game_data):
        """Remove Windows-specific game data (GreenLuma AppList files)."""
        if not game_data.get("is_accela_install"):
            logger.debug("Skipping GreenLuma cleanup for non-ACCELA install")
            return

        if not is_greenluma_wrapper_mode_enabled():
            logger.debug(
                "GreenLuma wrapper mode is disabled, skipping AppList cleanup"
            )
            return

        if not appid or appid in ("0", "N/A", "unknown"):
            logger.warning(
                f"Skipping GreenLuma AppList cleanup for invalid appid: {appid}"
            )
            return

        if not str(appid).isdigit():
            logger.error(
                f"Invalid appid format: {appid}. Must be numeric. Skipping GreenLuma cleanup."
            )
            return

        steam_path = find_steam_install()
        if not steam_path:
            logger.warning(
                "Could not find Steam installation path. Skipping GreenLuma AppList cleanup."
            )
            return

        app_list_dir = os.path.join(steam_path, "AppList")
        if not os.path.exists(app_list_dir):
            logger.info(
                "AppList directory does not exist. No GreenLuma files to clean up."
            )
            return

        logger.info(f"Scanning GreenLuma AppList directory: {app_list_dir}")

        self._find_and_delete_greenluma_files(app_list_dir, appid)
        self._remove_dlc_files(app_list_dir, game_data, appid)

    @staticmethod
    def _find_and_delete_greenluma_files(app_list_dir, appid):
        """Find and delete GreenLuma files for the given AppID and renumber remaining files."""
        files_to_delete = []
        all_files_data = []

        try:
            for filename in os.listdir(app_list_dir):
                if filename.lower().endswith(".txt"):
                    filepath = os.path.join(app_list_dir, filename)
                    try:
                        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read().strip()
                            all_files_data.append((filename, filepath, content))

                            if content == str(appid):
                                files_to_delete.append((filename, filepath))
                                logger.info(
                                    f"Found GreenLuma file to delete: {filename} (contains AppID {appid})"
                                )
                    except (OSError, UnicodeDecodeError) as e:
                        logger.warning(f"Error reading AppList file {filepath}: {e}")
        except OSError as e:
            logger.error(f"Error scanning AppList directory {app_list_dir}: {e}")
            return

        for _filename_to_delete, filepath in files_to_delete:
            try:
                os.remove(filepath)
                logger.info(f"Deleted GreenLuma file: {filepath}")
            except OSError as e:
                logger.warning(f"Failed to delete GreenLuma file {filepath}: {e}")

        deleted_paths = {f[1] for f in files_to_delete}
        remaining_files = [
            (fname, fpath, fcontent)
            for fname, fpath, fcontent in all_files_data
            if fpath not in deleted_paths
        ]

        def extract_number(fname):
            match = re.match(r"^(\d+)\.txt$", fname)
            return int(match.group(1)) if match else 0

        remaining_files.sort(key=lambda x: extract_number(x[0]))

        for index, (old_filename, old_filepath, _content) in enumerate(remaining_files):
            new_filename = f"{index}.txt"
            new_filepath = os.path.join(app_list_dir, new_filename)

            if old_filename != new_filename:
                try:
                    os.rename(old_filepath, new_filepath)
                    logger.debug(
                        f"Renamed GreenLuma file: {old_filename} -> {new_filename}"
                    )
                except OSError as e:
                    logger.warning(
                        f"Failed to rename {old_filename} to {new_filename}: {e}"
                    )

        logger.info(
            f"GreenLuma AppList cleanup complete. Removed {len(files_to_delete)} file(s)."
        )

    @staticmethod
    def _collect_known_dlc_ids(game_data):
        """Collect DLC IDs from in-memory game data fields."""
        selected_dlcs = game_data.get("selected_dlcs") or []
        dlc_map = game_data.get("dlcs", {})
        if not isinstance(dlc_map, dict):
            dlc_map = {}

        dlc_ids = []
        seen = set()

        for dlc_id in selected_dlcs:
            dlc_id_str = str(dlc_id).strip()
            if not dlc_id_str or dlc_id_str in seen:
                continue
            seen.add(dlc_id_str)
            dlc_ids.append(dlc_id_str)

        for dlc_id in dlc_map:
            dlc_id_str = str(dlc_id).strip()
            if not dlc_id_str or dlc_id_str in seen:
                continue
            seen.add(dlc_id_str)
            dlc_ids.append(dlc_id_str)

        return dlc_ids

    @staticmethod
    def _find_applist_files_for_ids(app_list_dir, app_ids):
        """Return a mapping {appid: [filename, ...]} for matching AppList .txt files."""
        target_ids = {str(app_id).strip() for app_id in app_ids if str(app_id).strip()}
        files_by_id = {app_id: [] for app_id in target_ids}
        if not target_ids:
            return files_by_id

        for filename in os.listdir(app_list_dir):
            if not filename.lower().endswith(".txt"):
                continue

            filepath = os.path.join(app_list_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as file_handle:
                    content = file_handle.read().strip()
                if content in files_by_id:
                    files_by_id[content].append(filename)
            except (OSError, UnicodeDecodeError) as e:
                logger.debug(f"  Skipped unreadable AppList file {filepath}: {e}")
                continue

        for file_list in files_by_id.values():
            file_list.sort()

        return files_by_id

    def _read_manifest_dlc_ids(self, appid):
        """
        Best-effort read of DLC IDs from the local manifest ZIP for the given appid.
        Returns [] if no usable manifest is available.
        """
        appid_str = str(appid).strip()
        if not appid_str.isdigit():
            return []

        from core import morrenus_api

        manifest_zip = morrenus_api.get_manifest_zip_path(
            appid_str, morrenus_api.get_selected_branch(appid_str)
        )
        if not manifest_zip.exists():
            manifest_zip = morrenus_api.get_manifest_zip_path(appid_str, "public")
        if not manifest_zip.exists():
            return []

        try:
            with zipfile.ZipFile(manifest_zip, "r") as zip_ref:
                lua_files = [
                    name for name in zip_ref.namelist() if name.endswith(".lua")
                ]
                if not lua_files:
                    return []

                lua_content = zip_ref.read(lua_files[0]).decode(
                    "utf-8", errors="ignore"
                )
        except (OSError, zipfile.BadZipFile, RuntimeError) as e:
            logger.debug(f"Could not read manifest ZIP {manifest_zip}: {e}")
            return []

        app_matches = list(
            iter_live_matches(lua_content, r"addappid\((.*?)\)(.*)", re.IGNORECASE)
        )
        if len(app_matches) < 2:
            return []

        first_args = [arg.strip() for arg in app_matches[0].group(1).strip().split(",")]
        if not first_args:
            return []

        first_appid = first_args[0].strip('"')
        if first_appid != appid_str:
            return []

        dlc_ids = []
        seen = set()
        for match in app_matches[1:]:
            args = [arg.strip() for arg in match.group(1).strip().split(",")]
            if not args:
                continue

            candidate_id = args[0].strip('"')
            has_depot_key = (
                len(args) > 2
                and bool(args[2].strip('"'))
                and not is_placeholder_key(args[2])
            )
            if has_depot_key:
                continue
            if not candidate_id.isdigit():
                continue
            if candidate_id in seen:
                continue

            seen.add(candidate_id)
            dlc_ids.append(candidate_id)

        return dlc_ids

    def _infer_selected_dlcs_from_applist_and_manifest(self, appid):
        """
        Best-effort inference for old installs:
        intersect local manifest DLC IDs with IDs currently present in AppList.
        """
        appid_str = str(appid).strip()
        if not appid_str.isdigit():
            return []

        steam_path = find_steam_install()
        if not steam_path:
            return []

        app_list_dir = os.path.join(steam_path, "AppList")
        if not os.path.exists(app_list_dir):
            return []

        try:
            files_by_id = self._find_applist_files_for_ids(app_list_dir, [appid_str])
            if not files_by_id.get(appid_str):
                return []
        except OSError as e:
            logger.debug(f"Could not scan AppList for {appid_str}: {e}")
            return []

        manifest_dlc_ids = self._read_manifest_dlc_ids(appid_str)
        if not manifest_dlc_ids:
            return []

        try:
            files_by_id = self._find_applist_files_for_ids(
                app_list_dir, manifest_dlc_ids
            )
        except OSError as e:
            logger.debug(f"Could not scan AppList for DLC IDs of {appid_str}: {e}")
            return []

        return [dlc_id for dlc_id in manifest_dlc_ids if files_by_id.get(str(dlc_id))]

    def _remove_dlc_files(self, app_list_dir, game_data, appid):
        """Remove DLC files from the AppList directory."""
        dlc_ids = self._collect_known_dlc_ids(game_data)
        if not dlc_ids:
            dlc_ids = self._infer_selected_dlcs_from_applist_and_manifest(appid)

        if not dlc_ids:
            return

        logger.info(f"Removing {len(dlc_ids)} DLC files from AppList directory.")
        for dlc_id in dlc_ids:
            self._find_and_delete_greenluma_files(app_list_dir, dlc_id)