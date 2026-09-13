import json
import logging
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any, Union, List

# Handle optional compression dependency
try:
    import zstandard as zstd
except ImportError:
    zstd = None

from utils.helpers import get_base_path
from utils.paths import Paths

logger = logging.getLogger(__name__)

# 14 Days in seconds
EXPIRATION_SECONDS = 1_209_600


def _create_empty_db(path: Path) -> None:
    try:
        conn = sqlite3.connect(str(path))
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS apps (
                appid INTEGER PRIMARY KEY,
                name TEXT,
                header_path TEXT,
                installdir TEXT,
                depots_json BLOB,
                last_updated INTEGER
            )
        """
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Schema creation failed: {e}")


def _setup_database_path() -> Path:
    """Ensures the database exists in a writable user location in db/."""
    from utils.helpers import get_data_file_path

    # Migrate legacy root file if present and target in db/ doesn't exist
    legacy_path = get_base_path() / "steam_headers.db"
    db_target = get_base_path() / "db" / "steam_headers.db"
    if legacy_path.exists() and not db_target.exists():
        try:
            db_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy_path), str(db_target))
            logger.info(f"Moved legacy steam_headers.db to {db_target}")
            return db_target
        except Exception as e:
            logger.warning(f"Failed to migrate legacy steam_headers.db: {e}")

    writable_path = get_data_file_path("steam_headers.db")
    if writable_path.exists():
        return writable_path

    writable_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path = Paths.base("data/steam_headers.db")

    if not seed_path.exists():
        logger.warning(f"Seed database not found at {seed_path}. Creating empty DB.")
        _create_empty_db(writable_path)
        return writable_path

    logger.info(f"Seeding database from {seed_path}")
    try:
        shutil.copy2(seed_path, writable_path)
    except Exception as e:
        logger.error(f"Failed to copy seed database: {e}")
        _create_empty_db(writable_path)

    return writable_path


def _normalize_header_path(appid: str, url: str) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None
    url = url.split("?", 1)[0]
    if "/apps/" in url:
        return url.split("/apps/", 1)[1]
    return f"{appid}/header.jpg"


def _construct_full_url(header_path: str) -> Optional[str]:
    if not header_path:
        return None
    if header_path.startswith("http"):
        return header_path
    return f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{header_path}"


class DatabaseManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DatabaseManager, cls).__new__(cls)
                cls._instance.is_initialized = False
        return cls._instance

    def __init__(self):
        if self.is_initialized:
            return

        if zstd is None:
            logger.critical(
                "Module 'zstandard' is missing! Database functionality will be limited. "
                "Please 'pip install zstandard'"
            )

        self.db_path = _setup_database_path()
        self._conn_lock = threading.RLock()
        self.conn = self._connect_db()
        self.cctx = zstd.ZstdCompressor(level=3) if zstd else None
        self.dctx = zstd.ZstdDecompressor() if zstd else None

        self.is_initialized = True
        logger.info(f"DatabaseManager initialized at: {self.db_path}")

    def _connect_db(self) -> Optional[sqlite3.Connection]:
        try:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as e:
            logger.error(f"DB Connection failed: {e}")
            return None

    def get_header_url(self, appid: Union[str, int]) -> Optional[str]:
        """
        Get header URL with expiration check.
        Returns None if missing or stale (>14 days).
        """
        if not self.conn:
            return None

        try:
            with self._conn_lock:
                # Ensure safe type for DB query
                safe_appid = str(appid).strip()

                cur = self.conn.cursor()
                cur.execute(
                    "SELECT header_path, last_updated FROM apps WHERE appid = ?",
                    (safe_appid,),
                )
                row = cur.fetchone()

                if not row or not row["header_path"]:
                    return None

                last_updated = row["last_updated"] or 0
                age = int(time.time()) - last_updated

                if age > EXPIRATION_SECONDS:
                    logger.debug(
                        f"Header URL for AppID {safe_appid} is stale. Refreshing."
                    )
                    return None

                return _construct_full_url(row["header_path"])

        except Exception as e:
            logger.error(f"DB Read Error for header_url {appid}: {e}")
            return None

    def get_app_info(self, appid: str, bypass_expiration: bool = False) -> Optional[Dict[str, Any]]:
        if not self.conn or not self.dctx:
            return None

        try:
            with self._conn_lock:
                cur = self.conn.cursor()
                cur.execute(
                    "SELECT name, header_path, installdir, depots_json, last_updated "
                    "FROM apps WHERE appid = ?",
                    (appid,),
                )
                row = cur.fetchone()

                if not row:
                    return None

                last_updated = row["last_updated"] or 0
                if not bypass_expiration and (int(time.time()) - last_updated) > EXPIRATION_SECONDS:
                    return None

                depots_data = self._decompress_depots(row["depots_json"], appid)
                buildid = None
                branches = None
                if "branches" in depots_data:
                    branches = depots_data.get("branches")
                    buildid = (
                        branches.get("public", {}).get("buildid") if isinstance(branches, dict) else None
                    )
                    del depots_data["branches"]

                hasdepotsindlc = depots_data.pop("hasdepotsindlc", None)
                listofdlc = depots_data.pop("listofdlc", None)
                dlcs_expanded = depots_data.pop("dlcs_expanded", None)

                full_header_url = _construct_full_url(row["header_path"])

                return {
                    "appid": appid,
                    "name": row["name"],
                    "installdir": row["installdir"],
                    "header_url": full_header_url,
                    "depots": depots_data,
                    "buildid": buildid,
                    "branches": branches,
                    "hasdepotsindlc": hasdepotsindlc,
                    "listofdlc": listofdlc,
                    "dlcs_expanded": dlcs_expanded,
                    "source": "database",
                }

        except Exception as e:
            logger.error(f"DB Read Error {appid}: {e}")
            return None

    def _decompress_depots(self, blob: bytes, appid: str) -> dict:
        if not blob:
            return {}
        try:
            # Guard with lock; zstd contexts are not thread-safe.
            with self._conn_lock:
                decompressed = self.dctx.decompress(blob)
                return json.loads(decompressed)
        except Exception as e:
            logger.error(f"Decompression error for {appid}: {e}")
            return {}

    def upsert_app_info(self, appid: str, data: Dict[str, Any]) -> None:
        if not self.conn or not self.cctx:
            return

        try:
            with self._conn_lock:
                header_raw = data.get("header_url")
                header_path = (
                    _normalize_header_path(appid, header_raw) if header_raw else None
                )
                now = int(time.time())
                cur = self.conn.cursor()

                if header_path and len(data) == 1 and "header_url" in data:
                    if self._try_partial_update(cur, appid, header_path, now):
                        return

                self._perform_full_upsert(cur, appid, data, header_path, now)

        except Exception as e:
            logger.error(f"DB Write Error {appid}: {e}")

    def _try_partial_update(self, cur, appid, header_path, now) -> bool:
        cur.execute("SELECT appid FROM apps WHERE appid = ?", (appid,))
        if not cur.fetchone():
            return False

        cur.execute(
            """
            UPDATE apps SET header_path = ?, last_updated = ? WHERE appid = ?
        """,
            (header_path, now, appid),
        )
        self.conn.commit()
        return True

    def _perform_full_upsert(self, cur, appid, data, header_path, now):
        cur.execute(
            "SELECT name, header_path, installdir, depots_json FROM apps WHERE appid = ?",
            (appid,),
        )
        row = cur.fetchone()

        existing_depots = {}
        existing_branches = None
        existing_name = None
        existing_header = None
        existing_installdir = None
        existing_hasdepotsindlc = None
        existing_listofdlc = None
        existing_dlcs_expanded = None

        if row:
            existing_name = row["name"]
            existing_header = row["header_path"]
            existing_installdir = row["installdir"]
            if row["depots_json"]:
                decompressed = self._decompress_depots(row["depots_json"], appid)
                if isinstance(decompressed, dict):
                    existing_branches = decompressed.pop("branches", None)
                    existing_hasdepotsindlc = decompressed.pop("hasdepotsindlc", None)
                    existing_listofdlc = decompressed.pop("listofdlc", None)
                    existing_dlcs_expanded = decompressed.pop("dlcs_expanded", None)
                    existing_depots = decompressed

        # Preserve existing metadata if incoming data doesn't provide it
        name = data.get("name") or existing_name or f"App {appid}"
        installdir = data.get("installdir") or existing_installdir
        final_header = header_path or existing_header

        # Merge depots (only include digit keys in depots)
        incoming_depots = data.get("depots")
        if incoming_depots and isinstance(incoming_depots, dict):
            for d_id, d_val in incoming_depots.items():
                if str(d_id).isdigit():
                    existing_depots[str(d_id)] = d_val
        depots_to_save = existing_depots

        # Preserve / merge DLC expansion flags
        if "hasdepotsindlc" in data:
            depots_to_save["hasdepotsindlc"] = data["hasdepotsindlc"]
        elif existing_hasdepotsindlc is not None:
            depots_to_save["hasdepotsindlc"] = existing_hasdepotsindlc

        if "listofdlc" in data:
            depots_to_save["listofdlc"] = data["listofdlc"]
        elif existing_listofdlc is not None:
            depots_to_save["listofdlc"] = existing_listofdlc

        if "dlcs_expanded" in data:
            depots_to_save["dlcs_expanded"] = data["dlcs_expanded"]
        elif existing_dlcs_expanded is not None:
            depots_to_save["dlcs_expanded"] = existing_dlcs_expanded

        # Check if Steam reports a new buildid for public branch
        incoming_buildid = None
        if data.get("buildid"):
            incoming_buildid = str(data["buildid"]).strip()
        elif isinstance(data.get("branches"), dict):
            incoming_buildid = str(data["branches"].get("public", {}).get("buildid") or "").strip()

        old_buildid = None
        if isinstance(existing_branches, dict):
            old_buildid = str(existing_branches.get("public", {}).get("buildid") or "").strip()

        if old_buildid and incoming_buildid and old_buildid != incoming_buildid:
            logger.info(
                f"[db_manager] BuildID changed for AppID {appid}: {old_buildid} -> {incoming_buildid}. "
                f"Clearing dlcs_expanded to trigger fresh DLC expansion."
            )
            if "dlcs_expanded" not in data:
                depots_to_save["dlcs_expanded"] = False

        # Merge branches
        if data.get("branches"):
            merged_branches = existing_branches if isinstance(existing_branches, dict) else {}
            if isinstance(data["branches"], dict):
                merged_branches.update(data["branches"])
                depots_to_save["branches"] = merged_branches
            else:
                depots_to_save["branches"] = data["branches"]
        elif data.get("buildid"):
            merged_branches = existing_branches if isinstance(existing_branches, dict) else {}
            pub = merged_branches.get("public", {}) if isinstance(merged_branches.get("public"), dict) else {}
            pub["buildid"] = data["buildid"]
            merged_branches["public"] = pub
            depots_to_save["branches"] = merged_branches
        elif existing_branches:
            depots_to_save["branches"] = existing_branches

        depots_json_str = json.dumps(depots_to_save)
        depots_compressed = self.cctx.compress(depots_json_str.encode("utf-8"))

        cur.execute(
            """
            INSERT OR REPLACE INTO apps
            (appid, name, header_path, installdir, depots_json, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (appid, name, final_header, installdir, depots_compressed, now),
        )
        self.conn.commit()

    def close(self):
        if self.conn:
            with self._conn_lock:
                self.conn.close()

    def clear_app_info(self, appid):
        if not self.conn:
            return
        try:
            with self._conn_lock:
                cur = self.conn.cursor()
                cur.execute("DELETE FROM apps WHERE appid = ?", (str(appid),))
                self.conn.commit()
        except Exception as e:
            logger.error(f"DB Delete Error {appid}: {e}")

    def clear_all_branches(self):
        """Clear cached branch and depot info across all apps."""
        if not self.conn:
            return
        try:
            with self._conn_lock:
                cur = self.conn.cursor()
                cur.execute("UPDATE apps SET depots_json = NULL")
                self.conn.commit()
        except Exception as e:
            logger.error(f"DB Clear All Branches Error: {e}")

    def get_cache_time(self, appid: str) -> Optional[int]:
        """Get the last_updated timestamp for a given appid (ignoring expiration)."""
        if not self.conn:
            return None
        try:
            with self._conn_lock:
                cur = self.conn.cursor()
                cur.execute("SELECT last_updated FROM apps WHERE appid = ?", (str(appid),))
                row = cur.fetchone()
                if row:
                    return row[0]
        except Exception as e:
            logger.error(f"DB Read Error for cache time {appid}: {e}")
        return None

    def save_depot_enrichments(self, appid: str, enrichments: Dict[str, dict]):
        """Persists enriched depot names and sizes for an appid into steam_headers.db."""
        if not enrichments or not self.conn:
            return
        try:
            with self._conn_lock:
                cur = self.conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS depot_enrichments (
                        depot_id TEXT PRIMARY KEY,
                        appid TEXT,
                        name TEXT,
                        size_str TEXT,
                        size_bytes INTEGER,
                        dl_str TEXT,
                        is_dlc INTEGER,
                        oslist TEXT,
                        updated_at INTEGER
                    )
                """)
                now = int(time.time())
                for dep_id, info in enrichments.items():
                    cur.execute("""
                        INSERT INTO depot_enrichments (depot_id, appid, name, size_str, size_bytes, dl_str, is_dlc, oslist, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(depot_id) DO UPDATE SET
                            appid = excluded.appid,
                            name = excluded.name,
                            size_str = excluded.size_str,
                            size_bytes = excluded.size_bytes,
                            dl_str = excluded.dl_str,
                            is_dlc = excluded.is_dlc,
                            oslist = excluded.oslist,
                            updated_at = excluded.updated_at
                    """, (
                        str(dep_id),
                        str(appid),
                        info.get("name", ""),
                        info.get("size_str", ""),
                        info.get("size_bytes", 0),
                        info.get("dl_str", ""),
                        1 if info.get("is_dlc") else 0,
                        info.get("oslist"),
                        now
                    ))
                self.conn.commit()
                logger.info(f"[DBManager] Saved {len(enrichments)} depot enrichments for App {appid}")
        except Exception as e:
            logger.error(f"[DBManager] Failed to save depot enrichments for {appid}: {e}")

    def get_depot_enrichments(self, appid: str) -> Dict[str, dict]:
        """Returns cached depot enrichments for an appid, or empty dict if none."""
        if not self.conn:
            return {}
        try:
            with self._conn_lock:
                cur = self.conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS depot_enrichments (
                        depot_id TEXT PRIMARY KEY,
                        appid TEXT,
                        name TEXT,
                        size_str TEXT,
                        size_bytes INTEGER,
                        dl_str TEXT,
                        is_dlc INTEGER,
                        oslist TEXT,
                        updated_at INTEGER
                    )
                """)
                cur.execute("""
                    SELECT depot_id, name, size_str, size_bytes, dl_str, is_dlc, oslist
                    FROM depot_enrichments
                    WHERE appid = ?
                """, (str(appid),))
                rows = cur.fetchall()
                results = {}
                for r in rows:
                    results[str(r[0])] = {
                        "depot_id": str(r[0]),
                        "name": r[1],
                        "size_str": r[2],
                        "size_bytes": r[3] or 0,
                        "dl_str": r[4],
                        "is_dlc": bool(r[5]),
                        "oslist": r[6]
                    }
                return results
        except Exception as e:
            logger.debug(f"[DBManager] Failed to read depot enrichments for {appid}: {e}")
            return {}

    # ── Missing Hubcap Depot Tracking ────────────────────────────────────────

    def _ensure_missing_depots_table(self, cur):
        """Creates the missing_hubcap_depots table if it doesn't exist yet."""
        cur.execute("""
            CREATE TABLE IF NOT EXISTS missing_hubcap_depots (
                appid TEXT NOT NULL,
                depot_id TEXT NOT NULL,
                manifest_id TEXT NOT NULL,
                depot_name TEXT,
                first_seen INTEGER,
                PRIMARY KEY (appid, depot_id)
            )
        """)

    def upsert_missing_hubcap_depots(self, appid: str, depots: List[Dict[str, str]]) -> None:
        """
        Persists a list of depots that Hubcap could not provide for a given appid.

        Each dict in `depots` should have:
            - depot_id (str, required)
            - manifest_id (str, required)
            - depot_name (str, optional)

        Uses INSERT OR IGNORE so that first_seen is preserved for records that
        already exist (i.e. we don't clobber the timestamp on repeated failures).
        """
        if not depots or not self.conn:
            return
        try:
            with self._conn_lock:
                cur = self.conn.cursor()
                self._ensure_missing_depots_table(cur)
                now = int(time.time())
                for entry in depots:
                    depot_id = str(entry.get("depot_id", "")).strip()
                    manifest_id = str(entry.get("manifest_id", "")).strip()
                    if not depot_id or not manifest_id:
                        continue
                    cur.execute("""
                        INSERT OR IGNORE INTO missing_hubcap_depots
                            (appid, depot_id, manifest_id, depot_name, first_seen)
                        VALUES (?, ?, ?, ?, ?)
                    """, (str(appid), depot_id, manifest_id, entry.get("depot_name"), now))
                self.conn.commit()
                logger.info(f"[DBManager] Tracked {len(depots)} missing Hubcap depot(s) for App {appid}")
        except Exception as e:
            logger.error(f"[DBManager] Failed to persist missing depots for {appid}: {e}")

    def get_missing_hubcap_depots(self, appid: str) -> List[Dict[str, str]]:
        """
        Returns the list of depots that were previously found missing from Hubcap
        for the given appid.

        Each returned dict has:
            - depot_id (str)
            - manifest_id (str)
            - depot_name (str or None)
            - first_seen (int)
        """
        if not self.conn:
            return []
        try:
            with self._conn_lock:
                cur = self.conn.cursor()
                self._ensure_missing_depots_table(cur)
                cur.execute("""
                    SELECT depot_id, manifest_id, depot_name, first_seen
                    FROM missing_hubcap_depots
                    WHERE appid = ?
                    ORDER BY first_seen ASC
                """, (str(appid),))
                return [
                    {
                        "depot_id": str(r[0]),
                        "manifest_id": str(r[1]),
                        "depot_name": r[2],
                        "first_seen": r[3],
                    }
                    for r in cur.fetchall()
                ]
        except Exception as e:
            logger.debug(f"[DBManager] Failed to read missing depots for {appid}: {e}")
            return []

    def clear_missing_hubcap_depot(self, appid: str, depot_id: str) -> None:
        """Removes a single depot from the missing-depot tracking table (i.e. it's been recovered)."""
        if not self.conn:
            return
        try:
            with self._conn_lock:
                cur = self.conn.cursor()
                self._ensure_missing_depots_table(cur)
                cur.execute(
                    "DELETE FROM missing_hubcap_depots WHERE appid = ? AND depot_id = ?",
                    (str(appid), str(depot_id)),
                )
                self.conn.commit()
                logger.info(f"[DBManager] Cleared recovered depot {depot_id} from missing-depot list for App {appid}")
        except Exception as e:
            logger.error(f"[DBManager] Failed to clear missing depot {depot_id} for {appid}: {e}")

    def clear_all_missing_hubcap_depots(self, appid: str) -> None:
        """Removes all missing-depot records for an appid (e.g. after a full re-download succeeds)."""
        if not self.conn:
            return
        try:
            with self._conn_lock:
                cur = self.conn.cursor()
                self._ensure_missing_depots_table(cur)
                cur.execute("DELETE FROM missing_hubcap_depots WHERE appid = ?", (str(appid),))
                self.conn.commit()
                logger.info(f"[DBManager] Cleared all missing-depot records for App {appid}")
        except Exception as e:
            logger.error(f"[DBManager] Failed to clear all missing depots for {appid}: {e}")
