"""
compat_tool.py
==============
Utilities to manage Steam client compatibility tools (Proton) and detect
platform mismatches between Steam on Linux and Windows-only depots in packages.

Prevents 0-byte ghost downloads by:
  1. Detecting when Steam on Linux defaults to native Linux depots while Hubcap
     only provides Windows depots/keys.
  2. Forcing Proton for the specified AppID in Steam's config.vdf.
"""

import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)


def get_steam_config_vdf_path() -> Optional[Path]:
    """Find the canonical Steam config.vdf on Linux / SteamOS."""
    candidates = [
        Path.home() / ".local/share/Steam/config/config.vdf",
        Path.home() / ".steam/steam/config/config.vdf",
        Path.home() / ".steam/root/config/config.vdf",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam/config/config.vdf",
        Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam/config/config.vdf",
    ]
    seen = set()
    for c in candidates:
        if c.is_file():
            real = os.path.realpath(str(c))
            if real not in seen and os.path.isfile(real):
                seen.add(real)
                return Path(real)
    return None


def is_proton_forced(appid: Union[int, str]) -> bool:
    """Check if a Proton compatibility tool is already forced for this AppID in config.vdf."""
    aid_str = str(appid).strip()
    if not aid_str or aid_str in ("0", "N/A", "unknown"):
        return False

    config_path = get_steam_config_vdf_path()
    if not config_path or not config_path.is_file():
        return False

    try:
        import vdf
        with open(config_path, "r", encoding="utf-8", errors="ignore") as f:
            data = vdf.load(f)
        compat = (
            data.get("InstallConfigStore", {})
            .get("Software", {})
            .get("Valve", {})
            .get("Steam", {})
            .get("CompatToolMapping", {})
        )
        entry = compat.get(aid_str)
        if entry and isinstance(entry, dict):
            name = entry.get("name", "")
            if name and name != "Steam-Play-None":
                return True
    except Exception as e:
        logger.debug(f"[CompatTool] Error checking is_proton_forced for {aid_str}: {e}")

    return False


def get_preferred_compat_tool() -> str:
    """
    Detect the preferred compatibility tool:
    1. Scan existing entries in CompatToolMapping for the most frequently used Proton runner.
    2. Fallback to scanning installed compatibility tools in Steam directories.
    3. Default fallback to 'proton_experimental'.
    """
    config_path = get_steam_config_vdf_path()
    if config_path and config_path.is_file():
        try:
            import vdf
            from collections import Counter
            with open(config_path, "r", encoding="utf-8", errors="ignore") as f:
                data = vdf.load(f)
            compat = (
                data.get("InstallConfigStore", {})
                .get("Software", {})
                .get("Valve", {})
                .get("Steam", {})
                .get("CompatToolMapping", {})
            )
            tools = [
                v.get("name")
                for v in compat.values()
                if isinstance(v, dict) and v.get("name") and v.get("name") != "Steam-Play-None"
            ]
            if tools:
                counts = Counter(tools)
                preferred = counts.most_common(1)[0][0]
                if preferred:
                    return str(preferred)
        except Exception as e:
            logger.debug(f"[CompatTool] Error scanning config.vdf for preferred tool: {e}")

    # Fallback to local Steam compatibilitytools.d directories
    tool_dirs = [
        Path.home() / ".local/share/Steam/compatibilitytools.d",
        Path.home() / ".steam/root/compatibilitytools.d",
    ]
    for td in tool_dirs:
        if td.is_dir():
            try:
                tools = [d.name for d in td.iterdir() if d.is_dir()]
                if tools:
                    return tools[-1]
            except Exception:
                pass

    return "proton_experimental"


def force_proton_for_app(
    appid: Union[int, str],
    tool_name: Optional[str] = None,
    restart_steam_if_running: bool = True,
) -> bool:
    """
    Configure Proton for an AppID in Steam's config.vdf.
    If Steam is running, terminates it first so Steam does not overwrite config.vdf,
    writes the new CompatToolMapping entry atomically, and restarts Steam with SLSsteam.
    """
    aid_str = str(appid).strip()
    if not aid_str or aid_str in ("0", "N/A", "unknown"):
        return False

    config_path = get_steam_config_vdf_path()
    if not config_path or not config_path.is_file():
        logger.error("[CompatTool] Steam config.vdf not found; cannot force Proton.")
        return False

    if not tool_name:
        tool_name = get_preferred_compat_tool()

    from core.steam_helpers import is_steam_running, kill_steam_process, start_steam
    from core.native_steam.native_steam_handoff import check_slssteam_active

    was_running = is_steam_running()
    if was_running and restart_steam_if_running:
        logger.info(f"[CompatTool] Terminating Steam to modify {config_path}...")
        kill_steam_process()
        time.sleep(1.0)

    try:
        import vdf
        with open(config_path, "r", encoding="utf-8", errors="ignore") as f:
            data = vdf.load(f)

        compat = (
            data.setdefault("InstallConfigStore", {})
            .setdefault("Software", {})
            .setdefault("Valve", {})
            .setdefault("Steam", {})
            .setdefault("CompatToolMapping", {})
        )

        compat[aid_str] = {
            "name": tool_name,
            "config": "",
            "priority": "250",
        }

        # Safe backup if .bak does not exist
        bak_path = config_path.with_suffix(".vdf.bak")
        if not bak_path.exists():
            try:
                shutil.copy2(config_path, bak_path)
            except Exception as e:
                logger.debug(f"[CompatTool] Could not create backup of config.vdf: {e}")

        # Atomic write
        tmp_path = config_path.with_suffix(".vdf.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            vdf.dump(data, f, pretty=True)
        os.replace(tmp_path, config_path)
        logger.info(
            f"[CompatTool] Successfully set AppID {aid_str} to use '{tool_name}' in {config_path}"
        )

    except Exception as e:
        logger.error(f"[CompatTool] Failed to write CompatToolMapping in {config_path}: {e}")
        return False

    if was_running and restart_steam_if_running:
        logger.info("[CompatTool] Restarting Steam with SLSsteam...")
        start_steam()
        # Wait up to 15 seconds for SLSsteam to become active
        for _ in range(30):
            time.sleep(0.5)
            if check_slssteam_active():
                logger.info("[CompatTool] Steam and SLSsteam successfully active after restart.")
                break

    return True


def check_native_steam_platform_mismatch(
    appid: Union[int, str],
    available_depots: Optional[Iterable[Union[int, str]]] = None,
    game_name: str = "",
) -> Tuple[bool, Dict[str, Any]]:
    """
    Detect whether Steam on Linux will attempt to download native Linux depots
    when only Windows depots and keys are available in the package source.

    Returns:
        (is_mismatch, details_dict)
    """
    aid_str = str(appid).strip()
    if not aid_str or aid_str in ("0", "N/A", "unknown"):
        return False, {}

    # 1. Platform check: Only relevant when running Steam natively on Linux
    if not sys.platform.startswith("linux"):
        return False, {}

    # 2. If Proton is already forced for this app, Steam will download Windows depots -> No mismatch
    if is_proton_forced(aid_str):
        return False, {}

    # 3. Query Steam PICS to inspect official depots for this AppID
    try:
        from core.steam_api import batched_get_product_info
        pics_info = batched_get_product_info([int(aid_str)])
        app_info = pics_info.get(int(aid_str)) or pics_info.get(aid_str) or {}
    except Exception as e:
        logger.debug(f"[CompatTool] Error querying PICS for AppID {aid_str}: {e}")
        app_info = {}

    depots_dict = app_info.get("depots", {}) if isinstance(app_info, dict) else {}
    if not depots_dict:
        # Cannot determine depot breakdown from PICS -> Assume no mismatch
        return False, {}

    linux_depots: List[str] = []
    windows_depots: List[str] = []

    for d_id_raw, d_meta in depots_dict.items():
        d_id = str(d_id_raw)
        if not isinstance(d_meta, dict) or d_id == aid_str or d_id == "branches":
            continue
        oslist = str(d_meta.get("oslist") or d_meta.get("config", {}).get("oslist") or "").lower()
        if "linux" in oslist:
            linux_depots.append(d_id)
        elif "windows" in oslist or not oslist:
            windows_depots.append(d_id)

    # If the app has no native Linux content depots in Steam PICS, Steam automatically uses Proton/Windows -> No mismatch
    if not linux_depots:
        return False, {}

    # 4. Resolve available depots if not provided
    avail_set: Set[str] = set()
    if available_depots is not None:
        avail_set = {str(d) for d in available_depots if str(d) != aid_str}
    else:
        # Check cached Lua or fetch hubcap keys
        from utils.helpers import get_base_path
        cached_lua = Path(get_base_path()) / "cached_luas" / f"{aid_str}.lua"
        if cached_lua.is_file():
            try:
                from core.tasks.process_zip_task import ProcessZipTask
                txt = cached_lua.read_text(encoding="utf-8", errors="ignore")
                parsed: Dict[str, Any] = {}
                ProcessZipTask._parse_lua(txt, parsed)
                if parsed.get("depots"):
                    avail_set = {str(d) for d in parsed["depots"].keys() if str(d) != aid_str}
            except Exception as e:
                logger.debug(f"[CompatTool] Error reading cached Lua: {e}")

        if not avail_set:
            try:
                from core.tasks.native_steam_download_task import NativeSteamDownloadTask
                task = NativeSteamDownloadTask()
                keys, _ = task._fetch_hubcap_keys({}, aid_str)
                avail_set = {str(d) for d in keys.keys() if str(d) != aid_str}
            except Exception as e:
                logger.debug(f"[CompatTool] Error fetching Hubcap keys: {e}")

    if not avail_set:
        # No depot keys could be resolved -> Let downstream error handling manage this
        return False, {}

    # 5. Check if any native Linux depot is among the available depots
    has_available_linux = any(d in avail_set for d in linux_depots)
    has_available_windows = any(d in avail_set for d in windows_depots)

    # Mismatch occurs when Steam on Linux has native Linux depots, but the available package only has Windows depots
    if not has_available_linux and has_available_windows:
        preferred = get_preferred_compat_tool()
        resolved_name = (
            game_name
            or app_info.get("common", {}).get("name")
            or f"App {aid_str}"
        )
        details = {
            "appid": aid_str,
            "game_name": resolved_name,
            "linux_depots": sorted(linux_depots, key=lambda x: int(x) if x.isdigit() else 0),
            "windows_depots": sorted(
                [d for d in windows_depots if d in avail_set],
                key=lambda x: int(x) if x.isdigit() else 0,
            ),
            "available_depots": sorted(list(avail_set), key=lambda x: int(x) if x.isdigit() else 0),
            "preferred_tool": preferred,
        }
        logger.warning(
            f"[CompatTool] Platform mismatch detected for {resolved_name} ({aid_str}): "
            f"Steam PICS has Linux depots {linux_depots}, but package only has Windows depots {details['windows_depots']}. "
            f"Recommended Proton tool: {preferred}"
        )
        return True, details

    return False, {}
