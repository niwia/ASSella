import os
import re
import shutil
import logging
import threading
import requests
from typing import List, Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

def strip_emojis(text: str) -> str:
    """Remove emoji and non-standard symbol characters from strings while preserving brackets and ASCII symbols."""
    if not text:
        return ""
    # Correct unicode emoji regex pattern
    emoji_pattern = re.compile(
        "["
        "\U00010000-\U0010FFFF"
        "\u2600-\u26FF"
        "\u2700-\u27BF"
        "]+",
        flags=re.UNICODE,
    )
    clean = emoji_pattern.sub("", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean or clean in (".", "'", '"', "-"):
        return text.strip()
    return clean


def fetch_workshop_details(publishedfileids: List[str]) -> Dict[str, dict]:
    """Fetch workshop item titles, updated timestamps, and file sizes.
    Uses WorkshopCacheManager (SQLite) first for instant cached results,
    and queries Steam Web API only for missing/uncached items.
    """
    if not publishedfileids:
        return {}

    str_wids = [str(w) for w in publishedfileids]
    
    # 1. Read from SQLite Workshop Cache
    try:
        from utils.workshop_cache import WorkshopCacheManager
        cache_mgr = WorkshopCacheManager()
        cached_results = cache_mgr.get_cached_details(str_wids)
    except Exception as e:
        logger.debug(f"Workshop cache lookup error: {e}")
        cache_mgr = None
        cached_results = {}

    missing_wids = [w for w in str_wids if w not in cached_results]
    if not missing_wids:
        return cached_results

    # 2. Query Steam Web API for missing item IDs
    url = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
    data = {"itemcount": len(missing_wids)}
    for i, wid in enumerate(missing_wids):
        data[f"publishedfileids[{i}]"] = wid

    fresh_results = {}
    try:
        res = requests.post(url, data=data, timeout=10)
        if res.status_code == 200:
            payload = res.json()
            items = payload.get("response", {}).get("publishedfiledetails", [])
            for item in items:
                wid_str = str(item.get("publishedfileid", ""))
                if wid_str:
                    raw_title = item.get("title", f"Workshop Item #{wid_str}")
                    fresh_results[wid_str] = {
                        "title": raw_title or f"Workshop Item #{wid_str}",
                        "time_updated": int(item.get("time_updated", 0)),
                        "file_size": int(item.get("file_size", 0)),
                        "manifest": str(item.get("hcontent_file") or ""),
                        "consumer_app_id": str(item.get("consumer_app_id") or ""),
                    }
            if cache_mgr and fresh_results:
                cache_mgr.upsert_details(fresh_results)
    except Exception as e:
        logger.debug(f"Failed to fetch workshop item details from Steam API: {e}")

    # Combine cached + fresh results
    return {**cached_results, **fresh_results}


def delete_workshop_item(appid: str, wid: str, mod_path: str) -> bool:
    """Delete a local workshop item directory and clean up manifest entries."""
    success = False
    try:
        # 1. Delete content directory
        if mod_path and os.path.exists(mod_path):
            shutil.rmtree(mod_path, ignore_errors=True)
            logger.info(f"Deleted workshop item directory for WID {wid} at {mod_path}")
            success = True

        # 2. Check and clean up appworkshop_<appid>.acf if present
        from core.steam_helpers import get_steam_libraries
        for lib in get_steam_libraries():
            acf_path = os.path.join(lib, "steamapps", "workshop", f"appworkshop_{appid}.acf")
            if os.path.exists(acf_path):
                try:
                    with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    # Remove block for wid
                    pattern = re.compile(rf'"{wid}"\s*\{{[^}}]*\}}', re.DOTALL)
                    new_content = pattern.sub("", content)
                    if new_content != content:
                        with open(acf_path, "w", encoding="utf-8") as f:
                            f.write(new_content)
                        logger.info(f"Cleaned up WID {wid} from {acf_path}")
                except Exception as acf_err:
                    logger.debug(f"Failed to clean up workshop acf {acf_path}: {acf_err}")

            # 3. Clean up downloads folder if present
            dl_dir = os.path.join(lib, "steamapps", "workshop", "downloads", str(appid), str(wid))
            if os.path.exists(dl_dir):
                shutil.rmtree(dl_dir, ignore_errors=True)

    except Exception as e:
        logger.error(f"Failed to delete workshop item {wid}: {e}")

    return success


def check_game_has_workshop(appid: str, game_data: Optional[dict] = None, allow_network: bool = True) -> bool:
    """Check if a game has Steam Workshop support.
    Checks:
    0. DLC-only mode: games in DLC-only mode never show workshop
    1. Local filesystem for workshop content or appworkshop manifest
    2. Cached result from QSettings
    3. game_data depots metadata for 'workshopdepots'
    4. Steam store API categories (category 30 = Workshop) [only if allow_network=True]
    """
    if not appid or str(appid) in ("0", "N/A", "unknown"):
        return False

    appid_str = str(appid).strip()

    # 0. DLC-only mode guard
    try:
        from utils.dlc_helpers import is_dlc_only_mode
        if is_dlc_only_mode(appid_str):
            return False
    except Exception:
        pass

    # 1. Quick local check: do installed workshop files or directory exist?
    try:
        from core.steam_helpers import get_steam_libraries
        for lib in get_steam_libraries():
            ws_dir = os.path.join(lib, "steamapps", "workshop", "content", appid_str)
            if os.path.isdir(ws_dir) and os.listdir(ws_dir):
                return True
            acf_file = os.path.join(lib, "steamapps", "workshop", f"appworkshop_{appid_str}.acf")
            if os.path.exists(acf_file):
                return True
    except Exception:
        pass

    # 2. Check QSettings cache
    settings = None
    try:
        from utils.settings import get_settings
        settings = get_settings()
        cached = settings.value(f"has_workshop/{appid_str}", None)
        if cached is not None:
            # Handle boolean or string representation
            if isinstance(cached, bool):
                return cached
            return str(cached).lower() in ("true", "1", "yes")
    except Exception:
        settings = None

    # 3. Check game_data / depots metadata
    if game_data and isinstance(game_data, dict):
        depots = game_data.get("depots", {})
        if isinstance(depots, dict) and "workshopdepots" in depots:
            if settings:
                settings.setValue(f"has_workshop/{appid_str}", True)
            return True

    if not allow_network:
        return False

    # 4. Check Steam Store categories (Category 30 = Steam Workshop)
    try:
        url = f"https://store.steampowered.com/api/appdetails?appids={appid_str}&filters=categories"
        res = requests.get(url, timeout=3)
        if res.status_code == 200:
            data = res.json().get(appid_str, {})
            if data.get("success"):
                categories = data.get("data", {}).get("categories", [])
                has_ws = any(cat.get("id") == 30 for cat in categories)
                if settings:
                    settings.setValue(f"has_workshop/{appid_str}", has_ws)
                return has_ws
    except Exception as e:
        logger.debug(f"Could not check workshop support for appid {appid_str}: {e}")

    return False


def has_downloaded_workshop_content(appid: str) -> bool:
    """Check if the local workshop folder has any downloaded content/mods for this appid."""
    if not appid or str(appid) in ("0", "N/A", "unknown"):
        return False
    appid_str = str(appid).strip()
    try:
        from core.steam_helpers import get_steam_libraries
        for lib in get_steam_libraries():
            ws_dir = os.path.join(lib, "steamapps", "workshop", "content", appid_str)
            if os.path.isdir(ws_dir):
                try:
                    entries = [e for e in os.listdir(ws_dir) if not e.startswith(".")]
                    if entries:
                        return True
                except Exception:
                    pass
            acf_path = os.path.join(lib, "steamapps", "workshop", f"appworkshop_{appid_str}.acf")
            if os.path.isfile(acf_path) and os.path.getsize(acf_path) > 0:
                try:
                    with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if '"WorkshopItemDetails"' in content and re.search(r'"\d+"\s*\{', content):
                        return True
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"Error checking local workshop content for {appid_str}: {e}")
    return False


def check_game_workshop_available_async(
    appid: str,
    game_data: Optional[dict] = None,
    callback: Optional[Callable[[bool], None]] = None,
) -> threading.Thread:
    """Check if game both has Workshop support AND has downloaded workshop content.
    Runs entirely in background daemon thread to avoid affecting dialog load time.
    """
    def _worker():
        try:
            # 1. Fast local check: does the game have downloaded workshop content?
            has_content = has_downloaded_workshop_content(appid)
            if not has_content:
                if callback:
                    callback(False)
                return

            # 2. Check if the game has workshop support
            has_ws = check_game_has_workshop(appid, game_data=game_data, allow_network=True)
            if callback:
                callback(bool(has_content and has_ws))
        except Exception as e:
            logger.debug(f"Error in check_game_workshop_available_async: {e}")
            if callback:
                callback(False)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread


def check_game_has_workshop_async(
    appid: str,
    game_data: Optional[dict] = None,
    callback: Optional[Callable[[bool], None]] = None,
) -> threading.Thread:
    """Perform workshop check asynchronously in a background daemon thread and invoke callback(bool)."""
    def _worker():
        try:
            res = bool(check_game_has_workshop(appid, game_data=game_data, allow_network=True))
        except Exception:
            res = False
        if callback:
            try:
                callback(res)
            except Exception:
                pass

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread


def extract_workshop_id(raw: str) -> Optional[str]:
    """Extract Workshop PublishedFileId from a URL or raw ID string."""
    raw = (raw or "").strip()
    m = re.search(r"[?&]id=(\d+)", raw)
    if m:
        return m.group(1)
    if re.fullmatch(r"\d+", raw):
        return raw
    return None


def parse_workshop_ids(text: str) -> List[str]:
    """Extract a deduplicated list of valid Workshop IDs from a whitespace/comma-delimited text block."""
    tokens = re.split(r"[\s,]+", text or "")
    ids = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        wid = extract_workshop_id(t)
        if wid:
            ids.append(wid)
    return list(dict.fromkeys(ids))


def detect_game_for_workshop_items(wids: List[str]) -> Optional[dict]:
    """Given a list of Workshop IDs, identify target game AppID, game name,
    whether it is installed locally, and the installed library path.
    """
    if not wids:
        return None
    details = fetch_workshop_details([str(wids[0])])
    item_info = details.get(str(wids[0]), {})
    consumer_app_id = str(item_info.get("consumer_app_id") or "").strip()
    if not consumer_app_id or consumer_app_id == "0":
        return None

    from core.steam_helpers import get_steam_libraries
    installed = False
    library_path = None
    game_name = ""
    installdir = ""

    for lib in get_steam_libraries():
        manifest_p = os.path.join(lib, "steamapps", f"appmanifest_{consumer_app_id}.acf")
        if os.path.isfile(manifest_p):
            installed = True
            library_path = lib
            try:
                with open(manifest_p, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                m_name = re.search(r'"name"\s+"([^"]+)"', content)
                if m_name:
                    game_name = m_name.group(1).strip()
                m_dir = re.search(r'"installdir"\s+"([^"]+)"', content)
                if m_dir:
                    installdir = m_dir.group(1).strip()
            except Exception:
                pass
            break

    if not game_name:
        try:
            res = requests.get(f"https://store.steampowered.com/api/appdetails?appids={consumer_app_id}&filters=basic", timeout=3)
            if res.status_code == 200:
                game_name = res.json().get(consumer_app_id, {}).get("data", {}).get("name", "")
        except Exception:
            pass
    if not game_name:
        game_name = f"AppID {consumer_app_id}"

    return {
        "consumer_app_id": consumer_app_id,
        "game_name": game_name,
        "installed": installed,
        "library_path": library_path,
        "installdir": installdir,
    }


def resolve_workshop_items_batch(wids: List[str]) -> List[dict]:
    """Given a list of Workshop IDs, batch-resolve all items:
    Returns list of dicts with:
    {
        'wid': wid,
        'title': mod_title,
        'consumer_app_id': consumer_app_id,
        'game_name': game_name,
        'installed': bool,
        'library_path': str or None,
        'installdir': str,
        'file_size': int,
    }
    """
    if not wids:
        return []

    str_wids = list(dict.fromkeys(str(w).strip() for w in wids if str(w).strip()))
    details = fetch_workshop_details(str_wids)

    from core.steam_helpers import get_steam_libraries
    libraries = get_steam_libraries()

    # Cache game lookups by consumer_app_id
    game_cache = {}

    results = []
    for wid in str_wids:
        item = details.get(wid, {})
        raw_title = item.get("title") or f"Workshop Item #{wid}"
        consumer_app_id = str(item.get("consumer_app_id") or "").strip()

        if consumer_app_id and consumer_app_id != "0":
            if consumer_app_id not in game_cache:
                installed = False
                lib_path = None
                game_name = ""
                installdir = ""

                for lib in libraries:
                    manifest_p = os.path.join(lib, "steamapps", f"appmanifest_{consumer_app_id}.acf")
                    if os.path.isfile(manifest_p):
                        installed = True
                        lib_path = lib
                        try:
                            with open(manifest_p, "r", encoding="utf-8", errors="ignore") as f:
                                content = f.read()
                            m_name = re.search(r'"name"\s+"([^"]+)"', content)
                            if m_name:
                                game_name = m_name.group(1).strip()
                            m_dir = re.search(r'"installdir"\s+"([^"]+)"', content)
                            if m_dir:
                                installdir = m_dir.group(1).strip()
                        except Exception:
                            pass
                        break

                if not game_name:
                    try:
                        res = requests.get(f"https://store.steampowered.com/api/appdetails?appids={consumer_app_id}&filters=basic", timeout=3)
                        if res.status_code == 200:
                            game_name = res.json().get(consumer_app_id, {}).get("data", {}).get("name", "")
                    except Exception:
                        pass
                if not game_name:
                    game_name = f"AppID {consumer_app_id}"

                game_cache[consumer_app_id] = {
                    "installed": installed,
                    "library_path": lib_path,
                    "game_name": game_name,
                    "installdir": installdir,
                }

            ginfo = game_cache[consumer_app_id]
            results.append({
                "wid": wid,
                "title": raw_title,
                "consumer_app_id": consumer_app_id,
                "game_name": ginfo["game_name"],
                "installed": ginfo["installed"],
                "library_path": ginfo["library_path"],
                "installdir": ginfo["installdir"],
                "file_size": int(item.get("file_size", 0)),
            })
        else:
            results.append({
                "wid": wid,
                "title": raw_title,
                "consumer_app_id": "",
                "game_name": "Unknown Game",
                "installed": False,
                "library_path": None,
                "installdir": "",
                "file_size": int(item.get("file_size", 0)),
            })

    return results


def repair_workshop_for_game(appid: str, library_path: Optional[str] = None) -> dict:
    """Repair workshop manifest and base game appmanifest flags to fix 'Content Encrypted'
    and missing workshop item issues.

    1. Scans workshop/content/<appid>/ for all on-disk downloaded items.
    2. Determines sizes and manifest IDs.
    3. Reconstructs appworkshop_<appid>.acf: NeedsDownload=0, NeedsUpdate=0, correct SizeOnDisk,
       WorkshopItemsInstalled and WorkshopItemDetails populated, ghost items purged.
    4. Syncs userdata/<account_id>/ugc/<appid>_subscriptions.vdf.
    5. Checks and fixes base game appmanifest_<appid>.acf: populates InstalledDepots from
       .DepotDownloader/metadata.json, sets UpdateResult=0, ScheduledAutoUpdate=0.
    6. Ensures manifests exist in depotcache.
    """
    appid_str = str(appid).strip()
    if not appid_str or appid_str in ("0", "N/A", "unknown"):
        return {"success": False, "error": f"Invalid AppID: {appid}"}

    import time
    import json
    import vdf
    from core.steam_helpers import get_steam_libraries, find_steam_install

    target_lib = library_path
    if not target_lib or not os.path.exists(target_lib):
        # Locate which library has the game or workshop content
        for lib in get_steam_libraries():
            if os.path.isfile(os.path.join(lib, "steamapps", f"appmanifest_{appid_str}.acf")):
                target_lib = lib
                break
            if os.path.isdir(os.path.join(lib, "steamapps", "workshop", "content", appid_str)):
                target_lib = lib
                break
        if not target_lib:
            target_lib = find_steam_install()
        if not target_lib:
            return {"success": False, "error": "Could not determine Steam library path."}

    content_dir = os.path.join(target_lib, "steamapps", "workshop", "content", appid_str)
    ws_acf_path = os.path.join(target_lib, "steamapps", "workshop", f"appworkshop_{appid_str}.acf")
    appmanifest_path = os.path.join(target_lib, "steamapps", f"appmanifest_{appid_str}.acf")

    # 1. Discover all downloaded workshop item folders
    on_disk_items = {}
    if os.path.isdir(content_dir):
        for entry in os.listdir(content_dir):
            item_path = os.path.join(content_dir, entry)
            if os.path.isdir(item_path) and entry.isdigit():
                wid = entry
                item_size = 0
                manifest_id = ""
                mtimes = []
                try:
                    for root, dirs, files in os.walk(item_path):
                        for f in files:
                            fp = os.path.join(root, f)
                            try:
                                st = os.stat(fp)
                                item_size += st.st_size
                                mtimes.append(st.st_mtime)
                            except OSError:
                                pass
                except OSError:
                    pass

                # Check .DepotDownloader for manifest file
                dd_dir = os.path.join(item_path, ".DepotDownloader")
                if os.path.isdir(dd_dir):
                    try:
                        for f in os.listdir(dd_dir):
                            if f.endswith(".manifest") and not f.endswith(".manifest.sha"):
                                parts = f[:-9].split("_")
                                if len(parts) >= 2 and parts[-1].isdigit():
                                    manifest_id = parts[-1]
                                    break
                    except OSError:
                        pass

                folder_mtime = int(os.path.getmtime(item_path)) if os.path.exists(item_path) else int(time.time())
                best_mtime = int(max(mtimes, default=folder_mtime))

                on_disk_items[wid] = {
                    "size": item_size,
                    "manifest": manifest_id,
                    "timeupdated": best_mtime,
                    "item_path": item_path,
                }

    # 2. Read existing appworkshop acf to salvage manifests or metadata if missing
    last_buildid = ""
    time_last_app_ran = "0"
    account_id = "0"

    if os.path.isfile(ws_acf_path):
        try:
            with open(ws_acf_path, "r", encoding="utf-8", errors="ignore") as f:
                parsed = vdf.loads(f.read()).get("AppWorkshop", {})
                last_buildid = str(parsed.get("LastBuildID", ""))
                time_last_app_ran = str(parsed.get("TimeLastAppRan", "0"))
                det = parsed.get("WorkshopItemDetails", {})
                inst = parsed.get("WorkshopItemsInstalled", {})
                for wid in set(list(det.keys()) + list(inst.keys())):
                    m = str(det.get(wid, {}).get("manifest") or inst.get(wid, {}).get("manifest") or "")
                    sub_by = str(det.get(wid, {}).get("subscribedby") or "")
                    if sub_by and sub_by != "0":
                        account_id = sub_by
                    if wid in on_disk_items and not on_disk_items[wid]["manifest"] and m:
                        on_disk_items[wid]["manifest"] = m
        except Exception as e:
            logger.debug(f"Error reading existing workshop acf: {e}")

    # Fallback for account_id from Steam userdata
    if not account_id or account_id == "0":
        try:
            steam_root = find_steam_install()
            if steam_root:
                ud = os.path.join(steam_root, "userdata")
                if os.path.isdir(ud):
                    dirs = [d for d in os.listdir(ud) if d.isdigit() and d not in ("0", "anonymous")]
                    if dirs:
                        account_id = dirs[0]
        except Exception:
            pass
    if not account_id or account_id == "0":
        account_id = "1123573923"

    # Query Steam API for any missing manifests among on-disk items
    missing_manifest_wids = [wid for wid, d in on_disk_items.items() if not d["manifest"]]
    if missing_manifest_wids:
        try:
            api_info = fetch_workshop_details(missing_manifest_wids)
            for wid in missing_manifest_wids:
                m = api_info.get(wid, {}).get("manifest")
                if m:
                    on_disk_items[wid]["manifest"] = str(m)
                t = api_info.get(wid, {}).get("time_updated")
                if t and int(t) > 0:
                    on_disk_items[wid]["timeupdated"] = int(t)
        except Exception as e:
            logger.debug(f"Error fetching missing manifests from API: {e}")

    # Get LastBuildID from appmanifest if missing
    if not last_buildid and os.path.isfile(appmanifest_path):
        try:
            with open(appmanifest_path, "r", encoding="utf-8", errors="ignore") as mf:
                m_txt = mf.read()
            m_b = re.search(r'"buildid"\s+"([^"]+)"', m_txt)
            if m_b:
                last_buildid = m_b.group(1).strip()
        except Exception:
            pass

    # 3. Rebuild appworkshop_<appid>.acf
    now = int(time.time())
    total_size = sum(d["size"] for d in on_disk_items.values())

    ws_vdf_data = {
        "AppWorkshop": {
            "appid": appid_str,
            "SizeOnDisk": str(total_size),
            "NeedsUpdate": "0",
            "NeedsDownload": "0",
            "TimeLastUpdated": str(now),
            "TimeLastFullCheck": str(now),
            "TimeLastAppRan": time_last_app_ran,
            "LastBuildID": last_buildid or "0",
            "WorkshopItemsInstalled": {
                wid: {
                    "size": str(d["size"]),
                    "timeupdated": str(d["timeupdated"]),
                    "manifest": str(d["manifest"]),
                }
                for wid, d in on_disk_items.items()
            },
            "WorkshopItemDetails": {
                wid: {
                    "manifest": str(d["manifest"]),
                    "timeupdated": str(d["timeupdated"]),
                    "timetouched": str(now),
                    "subscribedby": account_id,
                    "latest_timeupdated": str(d["timeupdated"]),
                    "latest_manifest": str(d["manifest"]),
                }
                for wid, d in on_disk_items.items()
            }
        }
    }

    os.makedirs(os.path.dirname(ws_acf_path), exist_ok=True)
    with open(ws_acf_path, "w", encoding="utf-8") as f:
        vdf.dump(ws_vdf_data, f, pretty=True)
    logger.info(f"Successfully repaired {ws_acf_path} with {len(on_disk_items)} items.")

    # 4. Sync userdata subscriptions VDF
    try:
        steam_root = find_steam_install()
        if steam_root and account_id:
            ugc_dir = os.path.join(steam_root, "userdata", account_id, "ugc")
            os.makedirs(ugc_dir, exist_ok=True)
            vdf_sub_path = os.path.join(ugc_dir, f"{appid_str}_subscriptions.vdf")
            sub_dict = {
                "subscribedfiles": {
                    "appid": appid_str,
                    "time_last_updated": str(now),
                }
            }
            for idx, wid in enumerate(on_disk_items.keys()):
                sub_dict["subscribedfiles"][str(idx)] = {
                    "publishedfileid": str(wid),
                    "time_subscribed": str(now),
                    "disabled_locally": "0",
                }
            with open(vdf_sub_path, "w", encoding="utf-8") as f:
                vdf.dump(sub_dict, f, pretty=True)
            logger.info(f"Synchronized subscriptions to {vdf_sub_path}")
    except Exception as sub_err:
        logger.debug(f"Failed to sync subscriptions vdf: {sub_err}")

    # 5. Copy workshop manifests to depotcache
    depotcache_dir = os.path.join(target_lib, "depotcache")
    steam_root = find_steam_install()
    target_depotcaches = {depotcache_dir}
    if steam_root:
        target_depotcaches.add(os.path.join(steam_root, "depotcache"))

    for wid, d in on_disk_items.items():
        if d["manifest"]:
            m_filename = f"{appid_str}_{d['manifest']}.manifest"
            dd_m = os.path.join(d.get("item_path", ""), ".DepotDownloader", m_filename)
            if os.path.isfile(dd_m):
                for dc in target_depotcaches:
                    try:
                        os.makedirs(dc, exist_ok=True)
                        dest_m = os.path.join(dc, m_filename)
                        if not os.path.isfile(dest_m):
                            shutil.copy2(dd_m, dest_m)
                    except Exception:
                        pass

    # 6. Repair base game appmanifest if needed
    repaired_base_game = False
    if os.path.isfile(appmanifest_path):
        try:
            with open(appmanifest_path, "r", encoding="utf-8", errors="ignore") as mf:
                app_vdf = vdf.loads(mf.read())
            app_state = app_vdf.get("AppState", {})
            installed_depots = app_state.get("InstalledDepots", {})
            update_result = str(app_state.get("UpdateResult", "0"))
            size_on_disk = str(app_state.get("SizeOnDisk", "0"))
            installdir = str(app_state.get("installdir", ""))

            needs_base_fix = (
                not installed_depots
                or update_result != "0"
                or size_on_disk == "0"
                or str(app_state.get("ScheduledAutoUpdate", "0")) != "0"
            )

            if needs_base_fix:
                common_dir = os.path.join(target_lib, "steamapps", "common")
                possible_dd = []
                if installdir:
                    possible_dd.append(os.path.join(common_dir, installdir, ".DepotDownloader"))
                name = str(app_state.get("name", ""))
                if name:
                    possible_dd.append(os.path.join(common_dir, name, ".DepotDownloader"))

                found_metadata = None
                dd_found_dir = None
                for dd in possible_dd:
                    meta_p = os.path.join(dd, "metadata.json")
                    if os.path.isfile(meta_p):
                        try:
                            with open(meta_p, "r", encoding="utf-8") as f:
                                found_metadata = json.load(f)
                            dd_found_dir = dd
                            break
                        except Exception:
                            pass

                if found_metadata and dd_found_dir:
                    meta_size = str(found_metadata.get("size_on_disk", size_on_disk))
                    meta_buildid = str(found_metadata.get("buildid", app_state.get("buildid", "")))
                    depot_list = found_metadata.get("selected_depots_list", [])

                    new_depots = dict(installed_depots) if isinstance(installed_depots, dict) else {}
                    for dep in depot_list:
                        dep_str = str(dep)
                        dep_manifest = ""
                        for mf_name in os.listdir(dd_found_dir):
                            if mf_name.startswith(f"{dep_str}_") and mf_name.endswith(".manifest") and not mf_name.endswith(".sha"):
                                parts = mf_name[:-9].split("_")
                                if len(parts) >= 2:
                                    dep_manifest = parts[-1]
                                    break
                        if dep_manifest:
                            new_depots[dep_str] = {
                                "manifest": dep_manifest,
                                "size": meta_size,
                            }

                    if new_depots:
                        app_state["InstalledDepots"] = new_depots
                    if meta_size != "0":
                        app_state["SizeOnDisk"] = meta_size
                    if meta_buildid:
                        app_state["buildid"] = meta_buildid

                app_state["UpdateResult"] = "0"
                app_state["BytesToDownload"] = "0"
                app_state["BytesDownloaded"] = "0"
                app_state["BytesToStage"] = "0"
                app_state["BytesStaged"] = "0"
                app_state["ScheduledAutoUpdate"] = "0"
                app_state["FullValidateAfterNextUpdate"] = "0"
                app_state["DownloadType"] = "0"

                with open(appmanifest_path, "w", encoding="utf-8") as mf:
                    vdf.dump(app_vdf, mf, pretty=True)
                repaired_base_game = True
                logger.info(f"Repaired base game manifest {appmanifest_path}")

        except Exception as mf_err:
            logger.error(f"Error repairing appmanifest {appmanifest_path}: {mf_err}")

    return {
        "success": True,
        "items_count": len(on_disk_items),
        "total_size": total_size,
        "repaired_base_game": repaired_base_game,
        "library_path": target_lib,
    }

