"""Per-game cached info: list which games have some, and clear it everywhere.

Used by Settings -> Downloads -> Cached. Everything here reads or deletes
local files and databases only, and runs off the GUI thread.

What counts as a game's cache (all re-fetched the next time the game is opened):
  steam_headers.db   apps.depots_json (the name row stays: it is the app list),
                     depot_enrichments, missing_hubcap_depots, at0m_update_status
  steamdb_builds.db  builds
  workshop_cache.db  workshop_items for that game
  JSON caches        protondb_cache, update_status_cache, denuvo_cache (games),
                     editions catalog
  Files              image_cache/<appid>.jpg, image_cache/logos/<appid>.*,
                     sgdb_cache/{logos,heroes}/<appid>.*, metadata/<appid>.json

Download data (decryption keys and manifests: hubcap_manifests zips,
cached_luas/<appid>.lua, depots/<appid>.depot, depot_keys.db) is cleared too,
except for games that are installed or in the AT0-M library: they need it for
updates, and decryption keys are never thrown away for them.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_ID = re.compile(r"^\d{1,10}$")


def _base() -> Path:
    from utils.helpers import get_base_path

    return Path(get_base_path())


def _data(name: str) -> Path:
    from utils.helpers import get_data_file_path

    return Path(get_data_file_path(name))


def _connect(path: Path, write: bool = False) -> Optional[sqlite3.Connection]:
    if not path.is_file():
        return None
    try:
        if write:
            return sqlite3.connect(str(path), timeout=10.0)
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as e:
        logger.debug(f"[GameCache] cannot open {path}: {e}")
        return None


def _tables(conn: sqlite3.Connection) -> Set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# (database, table, appid column, extra WHERE) — rows deleted on clear
_INFO_ROWS = [
    ("steam_headers.db", "depot_enrichments", "appid", ""),
    ("steam_headers.db", "missing_hubcap_depots", "appid", ""),
    ("steam_headers.db", "at0m_update_status", "appid", ""),
    ("steamdb_builds.db", "builds", "appid", ""),
    ("workshop_cache.db", "workshop_items", "consumer_app_id", ""),
]
_KEY_ROWS = [
    ("depot_keys.db", "depot_keys", "appid", ""),
    ("depot_keys.db", "app_tokens", "appid", ""),
]
# Tables whose appids decide which games are listed (something the game page cached).
_LIST_ROWS = [
    ("steam_headers.db", "depot_enrichments", "appid"),
    ("steam_headers.db", "at0m_update_status", "appid"),
    ("steamdb_builds.db", "builds", "appid"),
]


def _file_ids(folder: Path, pattern: str, rx: str) -> Set[str]:
    out: Set[str] = set()
    if not folder.is_dir():
        return out
    r = re.compile(rx)
    try:
        for f in folder.glob(pattern):
            m = r.match(f.name)
            if m:
                out.add(m.group(1))
    except OSError as _silent_err:
        logger.debug("[game_cache] _file_ids: ignored %s", _silent_err, exc_info=False)
    return out


def _row_ids(specs) -> Set[str]:
    ids: Set[str] = set()
    for db, table, col in specs:
        conn = _connect(_data(db))
        if conn is None:
            continue
        try:
            if table in _tables(conn):
                ids.update(str(r[0]) for r in conn.execute(f'SELECT DISTINCT "{col}" FROM "{table}"'))
        except sqlite3.Error as e:
            logger.debug(f"[GameCache] {db}/{table}: {e}")
        finally:
            conn.close()
    return ids


def _cached_appids() -> Set[str]:
    """Games with info to clear. Download data alone does not list a game that keeps it anyway."""
    ids = _row_ids(_LIST_ROWS)
    conn = _connect(_data("steam_headers.db"))
    if conn is not None:
        try:
            ids.update(str(r[0]) for r in conn.execute(
                "SELECT appid FROM apps WHERE depots_json IS NOT NULL AND length(depots_json) > 0"))
        except sqlite3.Error as _silent_err:
            logger.debug("[game_cache] _cached_appids: ignored %s", _silent_err, exc_info=False)
        finally:
            conn.close()
    base = _base()
    ids |= _file_ids(base / "metadata", "*.json", r"^(\d+)\.json$")
    downloads = _row_ids([("depot_keys.db", "depot_keys", "appid")])
    downloads |= _file_ids(base / "hubcap_manifests", "accela_fetch_*.zip", r"^accela_fetch_(\d+)")
    downloads |= _file_ids(base / "cached_luas", "*.lua", r"^(?:accela_)?(\d+)\.lua$")
    downloads |= _file_ids(base / "depots", "*.depot", r"^(\d+)\.depot$")
    ids |= downloads - _protected_appids()
    return {i for i in ids if _ID.match(i) and i != "0"}


def _names(appids: Set[str]) -> Dict[str, str]:
    names: Dict[str, str] = {}
    conn = _connect(_data("steam_headers.db"))
    if conn is not None:
        try:
            ids = sorted(appids)
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = f"SELECT appid, name FROM apps WHERE appid IN ({','.join('?' * len(chunk))})"
                for appid, name in conn.execute(q, [int(x) for x in chunk]):
                    if name:
                        names[str(appid)] = str(name)
        except sqlite3.Error as e:
            logger.debug(f"[GameCache] names: {e}")
        finally:
            conn.close()
    missing = appids - names.keys()
    if missing:  # AT0-M library and the update cache carry titles too
        try:
            lib = json.loads(_data("plugin_library.json").read_text(encoding="utf-8"))
            for a in missing:
                n = (lib.get(a) or {}).get("name") or (lib.get(a) or {}).get("game_name")
                if n:
                    names[a] = str(n)
        except Exception as _silent_err:
            logger.debug("[game_cache] _names: ignored %s", _silent_err, exc_info=False)
    return names


def list_cached_games() -> List[Tuple[str, str]]:
    """[(appid, name)] of every game with cached info, sorted by name."""
    ids = _cached_appids()
    names = _names(ids)
    rows = [(a, names.get(a) or f"App {a}") for a in ids]
    rows.sort(key=lambda r: (r[1].casefold(), int(r[0])))
    return rows


def _protected_appids() -> Set[str]:
    """Games whose keys and manifests must stay: installed, or in the AT0-M library."""
    out: Set[str] = set()
    try:
        out.update(str(k) for k in json.loads(_data("plugin_library.json").read_text(encoding="utf-8")))
    except Exception as _silent_err:
        logger.debug("[game_cache] _protected_appids: ignored %s", _silent_err, exc_info=False)
    try:
        from core.steam_helpers import get_steam_libraries

        for lib in get_steam_libraries() or []:
            out |= _file_ids(Path(lib) / "steamapps", "appmanifest_*.acf", r"^appmanifest_(\d+)\.acf$")
    except Exception as e:
        logger.debug(f"[GameCache] steam libraries: {e}")
    return out


def _delete_rows(specs, appid: str, removed: List[str]) -> None:
    by_db: Dict[str, list] = {}
    for db, table, col, extra in specs:
        by_db.setdefault(db, []).append((table, col, extra))
    for db, items in by_db.items():
        conn = _connect(_data(db), write=True)
        if conn is None:
            continue
        try:
            have = _tables(conn)
            n = 0
            with conn:
                for table, col, extra in items:
                    if table in have:
                        n += conn.execute(f'DELETE FROM "{table}" WHERE CAST("{col}" AS TEXT) = ?{extra}',
                                          (appid,)).rowcount or 0
            if n:
                removed.append(f"{db} ({n})")
        except sqlite3.Error as e:
            logger.warning(f"[GameCache] {db}: {e}")
        finally:
            conn.close()


def _drop_json_key(path: Path, appid: str, nested: Optional[str] = None) -> bool:
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        holder = data.get(nested) if nested else data
        if not isinstance(holder, dict) or appid not in holder:
            return False
        del holder[appid]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.debug(f"[GameCache] {path.name}: {e}")
        return False


def _unlink(paths, removed: List[str], label: str) -> None:
    n = 0
    for p in paths:
        try:
            if p.is_file():
                p.unlink()
                n += 1
        except OSError as e:
            logger.debug(f"[GameCache] cannot delete {p}: {e}")
    if n:
        removed.append(f"{label} ({n})")


def clear_game_cache(appid: str) -> Dict[str, object]:
    """Clear one game's cached info everywhere. Returns {'removed': [...], 'kept_keys': bool}."""
    appid = str(appid).strip()
    if not _ID.match(appid):
        return {"removed": [], "kept_keys": False}
    removed: List[str] = []
    base = _base()

    _delete_rows(_INFO_ROWS, appid, removed)
    conn = _connect(_data("steam_headers.db"), write=True)
    if conn is not None:
        try:
            with conn:
                if conn.execute("UPDATE apps SET depots_json = NULL WHERE appid = ? AND depots_json IS NOT NULL",
                                (int(appid),)).rowcount:
                    removed.append("steam_headers.db (depots)")
        except sqlite3.Error as e:
            logger.debug(f"[GameCache] apps: {e}")
        finally:
            conn.close()

    for name, nested in (("protondb_cache.json", None), ("update_status_cache.json", None),
                         ("denuvo_cache.json", "games")):
        if _drop_json_key(_data(name), appid, nested):
            removed.append(name)
    try:
        from utils.helpers import get_cache_dir

        if _drop_json_key(Path(get_cache_dir("editions")) / "catalog.json", appid, "apps"):
            removed.append("editions catalog")
    except Exception as _silent_err:
        logger.debug("[game_cache] clear_game_cache: ignored %s", _silent_err, exc_info=False)

    files = [base / "image_cache" / f"{appid}.jpg", base / "metadata" / f"{appid}.json"]
    for d in (base / "image_cache" / "logos", base / "sgdb_cache" / "logos", base / "sgdb_cache" / "heroes"):
        if d.is_dir():
            files.extend(d.glob(f"{appid}.*"))
    _unlink(files, removed, "images and metadata")

    kept_keys = appid in _protected_appids()
    if not kept_keys:
        _delete_rows(_KEY_ROWS, appid, removed)
        dl = [base / "cached_luas" / f"{appid}.lua", base / "cached_luas" / f"accela_{appid}.lua",
              base / "depots" / f"{appid}.depot"]
        hz = base / "hubcap_manifests"
        if hz.is_dir():
            dl.extend(hz.glob(f"accela_fetch_{appid}.zip"))
            dl.extend(hz.glob(f"accela_fetch_{appid}_*.zip"))
        _unlink(dl, removed, "manifests and lua")

    logger.info(f"[GameCache] Cleared cache for {appid}: {', '.join(removed) or 'nothing'}"
                + (" (kept keys: installed or in the AT0-M library)" if kept_keys else ""))
    return {"removed": removed, "kept_keys": kept_keys}
