import os
import re
import tempfile
import zipfile
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

logger = logging.getLogger(__name__)


def resolve_appid_from_depot(depot_id: str | int) -> Tuple[Optional[str], Optional[str]]:
    """Resolves the parent game AppID and game title from a Steam Depot ID.
    
    Checks in order:
    1. Local database (apps and steam_headers).
    2. DepotKeyManager (if cached).
    3. Steam Store API heuristics (e.g. depot_id // 10 * 10, depot_id - 1, etc.).
    4. Steam PICS fallback.
    """
    if not depot_id:
        return None, None

    depot_str = str(depot_id).strip()
    if not depot_str.isdigit():
        return None, None

    depot_int = int(depot_str)

    # 1. Local Database Query
    try:
        from managers.db_manager import DatabaseManager
        db = DatabaseManager()
        with db._conn_lock:
            cur = db.conn.cursor()
            # Direct match
            cur.execute("SELECT appid, name FROM apps WHERE appid = ?", (depot_str,))
            row = cur.fetchone()
            if row and row["appid"]:
                return str(row["appid"]), str(row["name"] or f"App {row['appid']}")

            # Match inside depots_json
            cur.execute("SELECT appid, name, depots_json FROM apps WHERE depots_json LIKE ?", (f'%"{depot_str}"%',))
            row = cur.fetchone()
            if row and row["appid"]:
                return str(row["appid"]), str(row["name"] or f"App {row['appid']}")
    except Exception as e:
        logger.debug(f"[ManifestResolver] DB query for depot {depot_str} failed: {e}")

    # 2. Local DepotKeyManager Query
    try:
        from managers.depot_key_manager import DepotKeyManager
        dkm = DepotKeyManager()
        with dkm._lock:
            cur = dkm.conn.cursor()
            cur.execute("SELECT appid FROM depot_keys WHERE depot_id = ?", (depot_str,))
            row = cur.fetchone()
            if row and row[0]:
                aid = str(row[0])
                # Resolve title from local DB
                try:
                    from managers.db_manager import DatabaseManager
                    meta = DatabaseManager().get_app_info(aid)
                    if meta and meta.get("name"):
                        return aid, meta["name"]
                except Exception:
                    pass
                return aid, f"App {aid}"
    except Exception as e:
        logger.debug(f"[ManifestResolver] DepotKeyManager query for depot {depot_str} failed: {e}")

    # 3. Steam Store API Heuristic
    # Most Steam games allocate main depots as <appid> + 1, <appid> + 2, etc.
    # e.g. AppID 3159330 -> Depot 3159331; AppID 227900 -> Depot 227901.
    candidates: List[int] = []
    base_10 = (depot_int // 10) * 10
    if base_10 > 0:
        candidates.append(base_10)

    for offset in [0, -1, -2, -3, -4, -5, -10, 1, 2]:
        cand = depot_int + offset
        if cand > 0 and cand not in candidates:
            candidates.append(cand)

    import requests
    for cand in candidates:
        try:
            url = f"https://store.steampowered.com/api/appdetails?appids={cand}"
            resp = requests.get(url, timeout=3.5).json()
            cand_str = str(cand)
            if resp.get(cand_str, {}).get("success"):
                data = resp[cand_str].get("data", {})
                app_type = data.get("type", "")
                name = data.get("name")
                if app_type in ("game", "dlc", "application", "demo") and name:
                    logger.info(f"[ManifestResolver] Resolved Depot {depot_str} -> AppID {cand} ({name}) via Store API")
                    return cand_str, name
        except Exception:
            continue

    # 4. Steam PICS Query
    try:
        from core.steam_api import get_depot_info_from_api
        for cand in candidates[:3]:
            info = get_depot_info_from_api(str(cand))
            if info and info.get("name"):
                depots = info.get("depots") or {}
                if depot_str in depots or cand == depot_int:
                    logger.info(f"[ManifestResolver] Resolved Depot {depot_str} -> AppID {cand} ({info['name']}) via PICS")
                    return str(cand), info["name"]
    except Exception as e:
        logger.debug(f"[ManifestResolver] PICS query for depot {depot_str} failed: {e}")

    return None, None


def inspect_manifest_file(manifest_path: str | Path) -> Dict[str, Any]:
    """Inspects a Steam .manifest file directly without network requests.
    
    Returns depot_id, manifest_id (gid), total_size, file_count, executables, and creation_time.
    """
    path = Path(manifest_path)
    result: Dict[str, Any] = {
        "depot_id": "",
        "manifest_id": "",
        "total_size": 0,
        "file_count": 0,
        "executables": [],
        "creation_time": 0,
    }

    if not path.exists():
        return result

    # Extract depot_id and manifest_id from filename pattern: <depot_id>_<manifest_id>.manifest
    stem = path.stem
    parts = stem.split("_")
    if len(parts) >= 2 and parts[0].isdigit():
        result["depot_id"] = parts[0]
        result["manifest_id"] = parts[1]

    try:
        from steam.core.manifest import DepotManifest
        data = path.read_bytes()
        m = DepotManifest(data)
        if m.depot_id:
            result["depot_id"] = str(m.depot_id)
        if m.gid:
            result["manifest_id"] = str(m.gid)
        result["creation_time"] = int(m.creation_time or 0)

        total_bytes = 0
        executables = []
        file_count = 0

        if hasattr(m, "payload") and hasattr(m.payload, "mappings"):
            for mapping in m.payload.mappings:
                file_count += 1
                size = getattr(mapping, "size", 0)
                total_bytes += size
                fn = getattr(mapping, "filename", "")
                if fn.lower().endswith(".exe") and not fn.lower().endswith("crashreport.exe"):
                    executables.append(fn)

        result["total_size"] = total_bytes
        result["file_count"] = file_count
        result["executables"] = executables[:5]
    except Exception as e:
        logger.debug(f"[ManifestResolver] Failed to parse manifest binary for {path.name}: {e}")

    return result


def ensure_depot_keys_for_app(appid: str, depot_id: Optional[str] = None) -> Tuple[Dict[str, str], Optional[str], Dict[str, str]]:
    """Ensures depot keys and app token are stored locally for the given AppID.
    
    If missing, downloads the current bundle from Hubcap to acquire the depot keys,
    persists them into depot_keys.db, and returns (keys_dict, app_token, latest_manifests_dict).
    """
    keys: Dict[str, str] = {}
    token: Optional[str] = None
    latest_manifests: Dict[str, str] = {}

    if not appid or appid in ("0", "unknown"):
        return keys, token, latest_manifests

    # 1. Check local DepotKeyManager first for keys & token
    try:
        from managers.depot_key_manager import DepotKeyManager
        dkm = DepotKeyManager()
        cached_keys = dkm.get_depot_keys(appid) or {}
        cached_token = dkm.get_app_token(appid)
        if cached_keys and (depot_id is None or str(depot_id) in cached_keys):
            logger.info(f"[ManifestResolver] Found cached depot keys for AppID {appid} in depot_keys.db")
            keys = dict(cached_keys)
            token = cached_token
    except Exception as e:
        logger.debug(f"[ManifestResolver] Cache query failed: {e}")

    # 2. Check cached_luas directory for latest manifests (and keys/token if needed)
    try:
        from utils.helpers import get_base_path
        from core.tasks.process_zip_task import ProcessZipTask
        cached_lua_path = Path(get_base_path()) / "cached_luas" / f"{appid}.lua"
        if cached_lua_path.exists():
            content = cached_lua_path.read_text(encoding="utf-8", errors="ignore")
            tmp_game_data: Dict[str, Any] = {}
            ProcessZipTask._parse_lua(content, tmp_game_data)
            depots_map = tmp_game_data.get("depots", {})
            for d_id, d_info in depots_map.items():
                if isinstance(d_info, dict) and d_info.get("key"):
                    keys.setdefault(str(d_id), d_info["key"])
            if not token:
                token = ProcessZipTask._extract_app_token(content, appid)
            for d_id, m_id in tmp_game_data.get("manifests", {}).items():
                latest_manifests[str(d_id)] = str(m_id)
            if keys and (depot_id is None or str(depot_id) in keys) and latest_manifests:
                logger.info(f"[ManifestResolver] Recovered depot keys and latest manifests for AppID {appid} from cached_luas")
                return keys, token, latest_manifests
    except Exception as e:
        logger.debug(f"[ManifestResolver] Cached LUA parse failed: {e}")

    # 3. If keys or latest_manifests still missing, fetch current bundle from Hubcap via morrenus_api
    if not keys or (depot_id and str(depot_id) not in keys) or not latest_manifests:
        try:
            from core import morrenus_api
            from core.tasks.process_zip_task import ProcessZipTask
            logger.info(f"[ManifestResolver] Requesting manifest bundle from Hubcap for AppID {appid} to acquire depot keys & live manifests...")
            zip_path, err = morrenus_api.download_manifest(appid)
            if zip_path and os.path.exists(zip_path):
                with zipfile.ZipFile(zip_path, "r") as zf:
                    lua_files = [f for f in zf.namelist() if f.endswith(".lua")]
                    if lua_files:
                        lua_content = zf.read(lua_files[0]).decode("utf-8", errors="ignore")
                        tmp_game_data = {}
                        ProcessZipTask._parse_lua(lua_content, tmp_game_data)
                        if not token:
                            token = ProcessZipTask._extract_app_token(lua_content, appid)
                        depots_map = tmp_game_data.get("depots", {})
                        for d_id, d_info in depots_map.items():
                            if isinstance(d_info, dict) and d_info.get("key"):
                                keys[str(d_id)] = d_info["key"]
                        for d_id, m_id in tmp_game_data.get("manifests", {}).items():
                            latest_manifests[str(d_id)] = str(m_id)

                        # Persist keys to depot_keys.db
                        if keys:
                            try:
                                from managers.depot_key_manager import DepotKeyManager
                                dkm = DepotKeyManager()
                                dkm.save_depot_keys(appid, keys)
                                if token:
                                    dkm.save_app_token(appid, token)
                                logger.info(f"[ManifestResolver] Saved {len(keys)} depot key(s) to depot_keys.db for AppID {appid}")
                            except Exception as _save_err:
                                logger.warning(f"[ManifestResolver] Failed to save keys to db: {_save_err}")

                        # Archive LUA
                        try:
                            from utils.helpers import get_base_path
                            lua_dir = Path(get_base_path()) / "cached_luas"
                            lua_dir.mkdir(parents=True, exist_ok=True)
                            (lua_dir / f"{appid}.lua").write_text(lua_content, encoding="utf-8")
                        except Exception:
                            pass

                    # Also grab all latest manifest IDs inside the bundle
                    for name in zf.namelist():
                        if name.endswith(".manifest"):
                            base = os.path.basename(name).replace(".manifest", "")
                            parts = base.split("_")
                            if len(parts) == 2:
                                latest_manifests[parts[0]] = parts[1]

                logger.info(f"[ManifestResolver] Successfully acquired {len(keys)} depot keys and {len(latest_manifests)} latest manifests for AppID {appid}")
            else:
                logger.warning(f"[ManifestResolver] Hubcap download_manifest failed for AppID {appid}: {err}")
        except Exception as e:
            logger.error(f"[ManifestResolver] Failed to fetch companion bundle from Hubcap: {e}")

    return keys, token, latest_manifests
