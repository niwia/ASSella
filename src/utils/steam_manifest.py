import os
import re
import sys
from typing import Any, Dict, Iterable, Optional


_EMPTY_PLATFORM_CONFIG = '\t"UserConfig"\n\t{\n\t}\n\t"MountedConfig"\n\t{\n\t}'


def sanitize_game_name(game_name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", game_name or "").strip()


def get_install_folder_name(game_data: Dict[str, Any]) -> str:
    safe_game_name = sanitize_game_name(game_data.get("game_name", ""))
    install_folder_name = game_data.get("installdir") or safe_game_name
    if not install_folder_name:
        install_folder_name = f"App_{game_data.get('appid')}"
    return install_folder_name


def get_game_directory(dest_path: str, game_data: Dict[str, Any]) -> str:
    return os.path.join(
        dest_path, "steamapps", "common", get_install_folder_name(game_data)
    )


def _get_depot_platform(depot_info: Dict[str, Any]) -> str:
    try:
        platform = (depot_info.get("oslist") or "").lower()
    except AttributeError:
        return "unknown"
    return platform or "unknown"


def _build_platform_config(
    selected_depots: Iterable[Any],
    all_depots: Dict[str, Any],
    log_proton: bool,
    logger,
    branch: Optional[str] = None,
) -> str:
    downloading_windows_depots = False
    downloading_linux_depots = False

    for depot_id in selected_depots:
        depot_id_str = str(depot_id)
        depot_info = all_depots.get(depot_id_str, {})
        platform = _get_depot_platform(depot_info)

        if platform == "windows":
            downloading_windows_depots = True
        elif platform == "linux":
            downloading_linux_depots = True

    user_config_lines = []
    if sys.platform == "linux" and downloading_windows_depots:
        if log_proton and logger:
            logger.info("Windows depots on Linux - adding Proton configuration")
        user_config_lines.append('\t\t"platform_override_dest"\t\t"linux"')
        user_config_lines.append('\t\t"platform_override_source"\t\t"windows"')

    if branch and branch != "public":
        user_config_lines.append(f'\t\t"betakey"\t\t"{branch}"')

    sections = []
    if user_config_lines:
        sections.append(
            '\t"UserConfig"\n\t{\n' + "\n".join(user_config_lines) + "\n\t}"
        )

    if sys.platform == "linux" and downloading_windows_depots:
        sections.append(
            '\t"MountedConfig"\n'
            "\t{\n"
            '\t\t"platform_override_dest"\t\t"linux"\n'
            '\t\t"platform_override_source"\t\t"windows"\n'
            "\t}"
        )

    return "\n".join(sections) if sections else _EMPTY_PLATFORM_CONFIG


def _build_depots_content(
    selected_depots: Iterable[Any],
    all_manifests: Dict[str, Any],
    all_depots: Dict[str, Any],
) -> str:
    depots_content = ""
    for depot_id in selected_depots:
        depot_id_str = str(depot_id)
        manifest_gid = all_manifests.get(depot_id_str)
        depot_info = all_depots.get(depot_id_str, {})
        depot_size = depot_info.get("size", "0")

        if manifest_gid:
            depots_content += (
                f'\t\t"{depot_id_str}"\n'
                f"\t\t{{\n"
                f'\t\t\t"manifest"\t\t"{manifest_gid}"\n'
                f'\t\t\t"size"\t\t"{depot_size}"\n'
                f"\t\t}}\n"
            )
    return depots_content


def _get_active_steam_id() -> str:
    """Retrieve active SteamID64 from Steam's loginusers.vdf if available."""
    try:
        from core.steam_helpers import find_steam_install
        steam_path = find_steam_install()
        if steam_path:
            loginusers_path = os.path.join(steam_path, "config", "loginusers.vdf")
            if os.path.exists(loginusers_path):
                with open(loginusers_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    # Find MostRecent user block: "7656119xxxxxxxx" { ... "MostRecent" "1" ... }
                    users = re.findall(r'"(\d{17})"\s*\{([^}]+)\}', content, re.DOTALL)
                    for steamid, block in users:
                        if '"MostRecent"\t\t"1"' in block or '"mostrecent"\t\t"1"' in block:
                            return steamid
                    # Fallback to first 17-digit SteamID found
                    m = re.search(r'"(\d{17})"', content)
                    if m:
                        return m.group(1)
    except Exception:
        pass
    return "76561199083839651"


def build_acf_content(
    game_data: Dict[str, Any],
    size_on_disk: int,
    install_folder_name: str,
    include_depots: bool,
    log_proton: bool = False,
    logger=None,
    existing_fields: Optional[Dict[str, str]] = None,
) -> str:
    buildid = game_data.get("buildid", "0")
    selected_depots = game_data.get("selected_depots_list", [])
    all_manifests = game_data.get("manifests", {})
    all_depots = game_data.get("depots", {})
    steam_id = _get_active_steam_id()
    branch = game_data.get("branch")

    platform_config = _build_platform_config(
        selected_depots, all_depots, log_proton, logger, branch=branch
    )
    depots_content = _build_depots_content(selected_depots, all_manifests, all_depots)

    installed_depots_str = (
        f'\t"InstalledDepots"\n\t{{\n{depots_content}\t}}'
        if include_depots and depots_content
        else '\t"InstalledDepots"\n\t{\n\t}'
    )

    preserve_content = ""
    if existing_fields:
        ignored_keys = {
            "appid", "Universe", "name", "StateFlags", "installdir",
            "SizeOnDisk", "buildid", "InstalledDepots", "UserConfig",
            "MountedConfig", "LastOwner", "TargetBuildID", "DownloadType",
            "UpdateResult", "AutoUpdateBehavior"
        }
        for k, v in existing_fields.items():
            if k not in ignored_keys:
                preserve_content += f'\t"{k}"\t\t"{v}"\n'

    acf_content = (
        f'"AppState"\n'
        f"{{\n"
        f'\t"appid"\t\t"{game_data.get("appid", "")}"\n'
        f'\t"Universe"\t\t"1"\n'
        f'\t"name"\t\t"{game_data.get("name") or game_data.get("game_name", "")}"\n'
        f'\t"StateFlags"\t\t"4"\n'
        f'\t"installdir"\t\t"{install_folder_name}"\n'
        f'\t"LastUpdated"\t\t"0"\n'
        f'\t"LastPlayed"\t\t"0"\n'
        f'\t"SizeOnDisk"\t\t"{size_on_disk}"\n'
        f'\t"StagingSize"\t\t"0"\n'
        f'\t"buildid"\t\t"{buildid}"\n'
        f'\t"LastOwner"\t\t"{steam_id}"\n'
        f'\t"DownloadType"\t\t"1"\n'
        f'\t"UpdateResult"\t\t"0"\n'
        f'\t"BytesToDownload"\t\t"0"\n'
        f'\t"BytesDownloaded"\t\t"0"\n'
        f'\t"BytesToStage"\t\t"0"\n'
        f'\t"BytesStaged"\t\t"0"\n'
        f'\t"TargetBuildID"\t\t"{buildid}"\n'
        f'\t"AutoUpdateBehavior"\t\t"0"\n'
        f'\t"AllowOtherDownloadsWhileRunning"\t\t"0"\n'
        f'\t"ScheduledAutoUpdate"\t\t"0"\n'
    )
    if preserve_content:
        acf_content += preserve_content

    acf_content += f"{installed_depots_str}"

    if platform_config:
        acf_content += f"\n{platform_config}"

    acf_content += "\n}"
    return acf_content


def write_acf_file(
    dest_path: str,
    game_data: Dict[str, Any],
    size_on_disk: int,
    include_depots: bool,
    log_proton: bool = False,
    logger=None,
) -> Optional[str]:
    if not dest_path or not game_data:
        return None

    install_folder_name = get_install_folder_name(game_data)
    acf_path = os.path.join(
        dest_path, "steamapps", f"appmanifest_{game_data['appid']}.acf"
    )
    os.makedirs(os.path.dirname(acf_path), exist_ok=True)

    existing_fields = {}
    if os.path.exists(acf_path):
        try:
            with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            for line in content.splitlines():
                match = re.match(r'^\s*"([^"]+)"\s*"([^"]*)"\s*$', line)
                if match:
                    k, v = match.groups()
                    existing_fields[k] = v
        except Exception as e:
            if logger:
                logger.error(f"Failed to read existing ACF file: {e}")

    acf_content = build_acf_content(
        game_data,
        size_on_disk,
        install_folder_name,
        include_depots=include_depots,
        log_proton=log_proton,
        logger=logger,
        existing_fields=existing_fields,
    )

    with open(acf_path, "w", encoding="utf-8") as f:
        f.write(acf_content)

    return acf_path
