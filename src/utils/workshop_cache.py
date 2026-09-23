"""
workshop_cache.py
──────────────────
Persistent SQLite cache for Steam Workshop metadata (titles, update timestamps, file sizes).
Stores items in db/workshop_cache.db.
"""

import sqlite3
import threading
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional
from utils.helpers import get_data_file_path

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# Cache TTL for Workshop metadata: 24 hours
WORKSHOP_CACHE_TTL = 24 * 60 * 60

class WorkshopCacheManager:
    def __init__(self):
        self.db_path = get_data_file_path("workshop_cache.db")
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self.db_path), timeout=10.0)

    def _init_db(self):
        with _lock:
            try:
                with self._get_connection() as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS workshop_items (
                            wid TEXT PRIMARY KEY,
                            title TEXT NOT NULL,
                            time_updated INTEGER NOT NULL DEFAULT 0,
                            file_size INTEGER NOT NULL DEFAULT 0,
                            last_fetched INTEGER NOT NULL DEFAULT 0,
                            manifest TEXT DEFAULT '',
                            consumer_app_id TEXT DEFAULT ''
                        );
                    """)
                    try:
                        conn.execute("ALTER TABLE workshop_items ADD COLUMN manifest TEXT DEFAULT ''")
                    except Exception:
                        pass
                    try:
                        conn.execute("ALTER TABLE workshop_items ADD COLUMN consumer_app_id TEXT DEFAULT ''")
                    except Exception:
                        pass
                    conn.commit()
            except Exception as e:
                logger.error(f"Failed to initialize workshop_cache.db: {e}")

    def get_cached_details(self, wids: List[str]) -> Dict[str, dict]:
        if not wids:
            return {}
        result = {}
        now = int(time.time())
        with _lock:
            try:
                with self._get_connection() as conn:
                    placeholders = ",".join(["?"] * len(wids))
                    cursor = conn.execute(
                        f"SELECT wid, title, time_updated, file_size, last_fetched, manifest, consumer_app_id FROM workshop_items WHERE wid IN ({placeholders})",
                        [str(w) for w in wids],
                    )
                    for row in cursor.fetchall():
                        wid, title, time_updated, file_size, last_fetched, manifest, consumer_app_id = row
                        if now - last_fetched < WORKSHOP_CACHE_TTL and title not in ('.', "'", '"', "") and consumer_app_id:
                            result[str(wid)] = {
                                "title": title,
                                "time_updated": int(time_updated),
                                "file_size": int(file_size),
                                "manifest": str(manifest or ""),
                                "consumer_app_id": str(consumer_app_id or ""),
                            }
            except Exception as e:
                logger.debug(f"Failed to read from workshop_cache.db: {e}")
        return result

    def upsert_details(self, details: Dict[str, dict]):
        if not details:
            return
        now = int(time.time())
        with _lock:
            try:
                with self._get_connection() as conn:
                    for wid, info in details.items():
                        conn.execute(
                            """
                            INSERT INTO workshop_items (wid, title, time_updated, file_size, last_fetched, manifest, consumer_app_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(wid) DO UPDATE SET
                                title=excluded.title,
                                time_updated=excluded.time_updated,
                                file_size=excluded.file_size,
                                last_fetched=excluded.last_fetched,
                                manifest=excluded.manifest,
                                consumer_app_id=excluded.consumer_app_id;
                            """,
                            (
                                str(wid),
                                info.get("title", ""),
                                int(info.get("time_updated", 0)),
                                int(info.get("file_size", 0)),
                                now,
                                str(info.get("manifest", "") or info.get("hcontent_file", "") or ""),
                                str(info.get("consumer_app_id", "") or ""),
                            ),
                        )
                    conn.commit()
            except Exception as e:
                logger.error(f"Failed to write to workshop_cache.db: {e}")
