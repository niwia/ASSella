import json
import logging
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any, Union

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
    """Ensures the database exists in a writable user location."""
    writable_path = get_base_path() / "steam_headers.db"

    if writable_path.exists():
        return writable_path

    get_base_path().mkdir(parents=True, exist_ok=True)
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

                full_header_url = _construct_full_url(row["header_path"])

                return {
                    "appid": appid,
                    "name": row["name"],
                    "installdir": row["installdir"],
                    "header_url": full_header_url,
                    "depots": depots_data,
                    "buildid": buildid,
                    "branches": branches,
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
        name = data.get("name", f"App {appid}")
        installdir = data.get("installdir")
        depots_to_save = data.get("depots", {}).copy()

        if data.get("buildid"):
            depots_to_save["branches"] = {"public": {"buildid": data["buildid"]}}

        depots_json_str = json.dumps(depots_to_save)
        depots_compressed = self.cctx.compress(depots_json_str.encode("utf-8"))

        cur.execute(
            """
            INSERT OR REPLACE INTO apps
            (appid, name, header_path, installdir, depots_json, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (appid, name, header_path, installdir, depots_compressed, now),
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
