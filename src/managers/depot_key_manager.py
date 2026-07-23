"""
DepotKeyManager — Persistent SQLite store for AES depot decryption keys and AppTokens.

This database is the authoritative local source for the credentials needed to use
the /generate/appmanifest endpoint without re-fetching the full zip.

Schema:
    depot_keys(appid TEXT, depot_id TEXT, aes_key TEXT, updated_at INTEGER)
    app_tokens(appid TEXT, token TEXT, updated_at INTEGER)
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Set

from utils.helpers import get_base_path

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _get_db_path() -> Path:
    path = get_base_path() / "depot_keys.db"
    return path


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS depot_keys (
            appid    TEXT NOT NULL,
            depot_id TEXT NOT NULL,
            aes_key  TEXT NOT NULL,
            updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (appid, depot_id)
        );
        CREATE TABLE IF NOT EXISTS app_tokens (
            appid      TEXT PRIMARY KEY,
            token      TEXT NOT NULL,
            updated_at INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.commit()


class DepotKeyManager:
    """Thread-safe manager for depot AES keys and AppTokens."""

    def __init__(self):
        self._db_path = _get_db_path()
        self._ensure_db()

    def _ensure_db(self) -> None:
        try:
            with _lock:
                conn = sqlite3.connect(str(self._db_path))
                _init_db(conn)
                # Purge any invalid entries where depot_id == appid
                conn.execute("DELETE FROM depot_keys WHERE appid = depot_id")
                conn.commit()
                conn.close()
        except Exception as e:
            logger.error(f"[DepotKeyManager] Failed to init DB at {self._db_path}: {e}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ─────────────────────────────────────────────────────────────
    # Depot Keys
    # ─────────────────────────────────────────────────────────────

    def save_depot_keys(self, appid: str, depot_keys: Dict[str, str], timestamp: Optional[int] = None) -> bool:
        """
        Persist AES keys for all depots of an app.
        depot_keys: {depot_id: aes_key_hex_string}
        """
        if not depot_keys:
            logger.debug(f"[DepotKeyManager] No depot keys to save for AppID {appid}")
            return False
        try:
            now = timestamp if timestamp is not None else int(time.time())
            saved_count = 0
            with _lock:
                conn = self._connect()
                for did, key in depot_keys.items():
                    if not key or str(did) == str(appid):
                        continue
                    row = conn.execute("SELECT updated_at FROM depot_keys WHERE appid=? AND depot_id=?", (str(appid), str(did))).fetchone()
                    if row and row["updated_at"] >= now:
                        continue
                    
                    conn.execute("INSERT OR REPLACE INTO depot_keys (appid, depot_id, aes_key, updated_at) VALUES (?,?,?,?)", (str(appid), str(did), str(key), now))
                    saved_count += 1
                conn.commit()
                conn.close()
            logger.info(f"[DepotKeyManager] Saved {saved_count}/{len(depot_keys)} depot keys for AppID {appid}")
            return True
        except Exception as e:
            logger.error(f"[DepotKeyManager] Failed to save depot keys for AppID {appid}: {e}")
            return False

    def get_depot_keys(self, appid: str) -> Dict[str, str]:
        """Returns {depot_id: aes_key} for the given appid, or empty dict."""
        try:
            with _lock:
                conn = self._connect()
                rows = conn.execute(
                    "SELECT depot_id, aes_key FROM depot_keys WHERE appid=?", (str(appid),)
                ).fetchall()
                conn.close()
            result = {row["depot_id"]: row["aes_key"] for row in rows if str(row["depot_id"]) != str(appid)}
            logger.debug(f"[DepotKeyManager] Loaded {len(result)} depot keys for AppID {appid}")
            return result
        except Exception as e:
            logger.error(f"[DepotKeyManager] Failed to load depot keys for AppID {appid}: {e}")
            return {}

    def get_key_updated_at(self, appid: str) -> Optional[int]:
        """Returns maximum updated_at timestamp (epoch seconds) for keys of appid, or None."""
        try:
            with _lock:
                conn = self._connect()
                row = conn.execute(
                    "SELECT MAX(updated_at) AS max_ts FROM depot_keys WHERE appid=?", (str(appid),)
                ).fetchone()
                conn.close()
            if row and row["max_ts"]:
                return int(row["max_ts"])
            return None
        except Exception as e:
            logger.error(f"[DepotKeyManager] get_key_updated_at failed for AppID {appid}: {e}")
            return None

    def has_depot_keys(self, appid: str) -> bool:
        """Quick check — returns True if any depot keys are cached for this appid."""
        try:
            with _lock:
                conn = self._connect()
                count = conn.execute(
                    "SELECT COUNT(*) FROM depot_keys WHERE appid=?", (str(appid),)
                ).fetchone()[0]
                conn.close()
            return count > 0
        except Exception as e:
            logger.error(f"[DepotKeyManager] has_depot_keys check failed for AppID {appid}: {e}")
            return False

    def has_new_depots(self, appid: str, pics_depot_ids: Set[str]) -> bool:
        """
        Returns True if Steam PICS reports depot IDs we don't have keys for.
        Used to detect new DLCs / depots that require a fresh full zip fetch.
        """
        cached_ids = set(self.get_depot_keys(appid).keys())
        new_depots = pics_depot_ids - cached_ids
        if new_depots:
            logger.warning(
                f"[DepotKeyManager] AppID {appid} has {len(new_depots)} new depot(s) "
                f"not in cache: {new_depots} — full zip fetch required"
            )
        return bool(new_depots)

    def get_all_cached_appids(self) -> list:
        """Returns list of all appids that have at least one depot key cached."""
        try:
            with _lock:
                conn = self._connect()
                rows = conn.execute("SELECT DISTINCT appid FROM depot_keys").fetchall()
                conn.close()
            return [row["appid"] for row in rows]
        except Exception as e:
            logger.error(f"[DepotKeyManager] Failed to list cached appids: {e}")
            return []

    # ─────────────────────────────────────────────────────────────
    # App Tokens
    # ─────────────────────────────────────────────────────────────

    def save_app_token(self, appid: str, token: str, timestamp: Optional[int] = None) -> bool:
        """Persist an AppToken for the given appid."""
        if not token:
            return False
        try:
            now = timestamp if timestamp is not None else int(time.time())
            with _lock:
                conn = self._connect()
                row = conn.execute("SELECT updated_at FROM app_tokens WHERE appid=?", (str(appid),)).fetchone()
                if row and row["updated_at"] >= now:
                    conn.close()
                    return False
                
                conn.execute(
                    "INSERT OR REPLACE INTO app_tokens (appid, token, updated_at) VALUES (?,?,?)",
                    (str(appid), str(token), now)
                )
                conn.commit()
                conn.close()
            logger.info(f"[DepotKeyManager] Saved AppToken for AppID {appid}")
            return True
        except Exception as e:
            logger.error(f"[DepotKeyManager] Failed to save AppToken for AppID {appid}: {e}")
            return False

    def get_app_token(self, appid: str) -> Optional[str]:
        """Returns the cached AppToken for the given appid, or None."""
        try:
            with _lock:
                conn = self._connect()
                row = conn.execute(
                    "SELECT token FROM app_tokens WHERE appid=?", (str(appid),)
                ).fetchone()
                conn.close()
            if row:
                logger.debug(f"[DepotKeyManager] Loaded AppToken for AppID {appid}")
                return row["token"]
            return None
        except Exception as e:
            logger.error(f"[DepotKeyManager] Failed to load AppToken for AppID {appid}: {e}")
            return None

    # ─────────────────────────────────────────────────────────────
    # Migration helper
    # ─────────────────────────────────────────────────────────────

    def migration_status(self) -> Dict[str, int]:
        """Returns counts of cached appids and depot keys for display purposes."""
        try:
            with _lock:
                conn = self._connect()
                n_apps = conn.execute("SELECT COUNT(DISTINCT appid) FROM depot_keys").fetchone()[0]
                n_keys = conn.execute("SELECT COUNT(*) FROM depot_keys").fetchone()[0]
                n_tokens = conn.execute("SELECT COUNT(*) FROM app_tokens").fetchone()[0]
                conn.close()
            return {"apps": n_apps, "keys": n_keys, "tokens": n_tokens}
        except Exception as e:
            logger.error(f"[DepotKeyManager] migration_status failed: {e}")
            return {"apps": 0, "keys": 0, "tokens": 0}
