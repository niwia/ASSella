import logging
import time
import re
import os
from collections import OrderedDict
from pathlib import Path
from functools import wraps
from typing import List, Optional, Union, Set

import requests
from PyQt6.QtCore import QObject, QUrl, pyqtSignal, Qt, QTimer
from PyQt6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

# Attempt to import DatabaseManager, handling potential path variations
try:
    from managers.db_manager import DatabaseManager
except ImportError:
    try:
        from db_manager import DatabaseManager
    except ImportError:
        DatabaseManager = None
        logging.getLogger(__name__).warning(
            "Could not import DatabaseManager. DB cache will be disabled."
        )

logger = logging.getLogger(__name__)

# Global network manager - shared across all fetchers, lives on main thread
_network_manager: Optional[QNetworkAccessManager] = None


def get_network_manager() -> QNetworkAccessManager:
    """Get or create global QNetworkAccessManager (must run on main thread)."""
    global _network_manager
    if _network_manager is None:
        _network_manager = QNetworkAccessManager()
    return _network_manager


def time_function(func):
    """Decorator to time function execution."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        execution_time = (end_time - start_time) * 1000
        # Only log if it takes a noticeable amount of time (>10ms) to reduce spam
        if execution_time > 10:
            logger.debug(f"{func.__name__} executed in {execution_time:.2f}ms")
        return result

    return wrapper


def send_request(url: str) -> bool:
    """Fast URL validation using HEAD requests."""
    try:
        response = requests.head(
            url,
            timeout=1.5,
            headers={"User-Agent": "Mozilla/5.0"},
            allow_redirects=True,
        )
        return response.status_code == 200
    except Exception as e:
        logger.debug(f"URL check failed for {url}: {e}")
        return False


# In-memory LRU session cache for transient search results and fast RAM hits
_session_memory_cache: OrderedDict[str, bytes] = OrderedDict()
MAX_SESSION_CACHE_SIZE = 50


class ImageFetcher(QObject):
    """Async image fetcher using Qt's QNetworkAccessManager with 2-tier RAM/Disk caching."""

    finished = pyqtSignal(bytes)
    MAX_SESSION_CACHE_SIZE = 50

    def __init__(self, url: str, ephemeral: bool = False, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.url = url
        self.ephemeral = ephemeral
        self._stopped = False
        self._reply: Optional[QNetworkReply] = None
        self._start_time: Optional[float] = None

        # Parse AppID from URL
        self.app_id = None
        match = re.search(r'/apps/(\d+)/', url)
        if match:
            self.app_id = match.group(1)

        # Build fallback list of URLs to try in sequence
        self.urls_to_try = [url]
        if self.app_id:
            if self.ephemeral:
                fallbacks = [
                    f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{self.app_id}/header.jpg",
                    f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{self.app_id}/library_capsule.jpg",
                ]
            else:
                fallbacks = [
                    f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{self.app_id}/header.jpg",
                    f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{self.app_id}/library_capsule.jpg",
                    f"https://cdn.akamai.steamstatic.com/steam/apps/{self.app_id}/header.jpg",
                    f"https://cdn.akamai.steamstatic.com/steam/apps/{self.app_id}/capsule_231x87.jpg",
                    f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{self.app_id}/library_hero.jpg"
                ]
            for fb in fallbacks:
                if fb not in self.urls_to_try:
                    self.urls_to_try.append(fb)

    def stop(self) -> None:
        """Abort the request and prevent signal emission."""
        self._stopped = True
        if self._reply is not None:
            self._reply.abort()

    def start(self) -> None:
        """Start the async fetch using RAM cache, disk cache, or QNetworkAccessManager."""
        if self._stopped:
            return

        if self.app_id:
            # 1. Tier 1: Check in-memory RAM cache first (instant 0ms, 0 disk I/O)
            mem_data = ImageFetcher.get_from_session_cache(self.app_id)
            if mem_data and not self._stopped:
                QTimer.singleShot(0, lambda: self.finished.emit(mem_data))
                return

            # 2. Tier 2: Check permanent disk cache
            cached_path = ImageFetcher.get_cache_path(self.app_id)
            if cached_path.exists():
                try:
                    data = cached_path.read_bytes()
                    if data and not self._stopped:
                        ImageFetcher.put_to_session_cache(self.app_id, data)
                        QTimer.singleShot(0, lambda: self.finished.emit(data))
                        return
                except Exception as e:
                    logger.debug(f"Failed to read cached image for AppID {self.app_id}: {e}")

        # 3. Tier 3: Fetch over network
        self._start_time = time.time()
        self._fetch_next_url()

    def _fetch_next_url(self) -> None:
        if self._stopped or not self.urls_to_try:
            if not self._stopped:
                self.finished.emit(b"")
            return

        current_url = self.urls_to_try.pop(0)
        manager = get_network_manager()

        request = QNetworkRequest(QUrl(current_url))
        request.setRawHeader(b"User-Agent", b"Mozilla/5.0")

        self._reply = manager.get(request)
        # ignore type error for connect
        self._reply.finished.connect(self._on_finished)  # type: ignore

    def _on_finished(self) -> None:
        """Handle the network reply."""
        # Guard Clause: Invalid state
        if self._stopped or self._reply is None:
            if self._reply:
                self._reply.deleteLater()
                self._reply = None
            return

        reply = self._reply
        self._reply = None

        try:
            # Guard Clause: Network Error -> Try fallback next
            if reply.error() != QNetworkReply.NetworkError.NoError:
                logger.debug(
                    f"Failed to fetch image from {reply.url().toString()}: {reply.errorString()}"
                )
                self._fetch_next_url()
                return

            # Success Path
            data = reply.readAll().data()  # .data() returns Python bytes

            if self.app_id and data:
                # Always store in the fast 50-item LRU RAM session cache
                ImageFetcher.put_to_session_cache(self.app_id, data)

                # Only persist to permanent disk cache if not ephemeral (e.g. library / installed games)
                if not self.ephemeral:
                    ImageFetcher.save_to_cache(self.app_id, data)

            if self._start_time:
                download_time = (time.time() - self._start_time) * 1000
                if download_time > 100:
                    logger.debug(
                        f"Downloaded {len(data)} bytes from {reply.url().toString()} "
                        f"in {download_time:.2f}ms"
                    )

            if not self._stopped:
                self.finished.emit(data)

        except Exception as e:
            logger.error(f"Error handling image reply: {e}")
            self._fetch_next_url()
        finally:
            reply.deleteLater()

    # --------------------------
    # In-Memory Session Cache (Max 50 items LRU)
    # --------------------------

    @staticmethod
    def get_from_session_cache(app_id: str) -> Optional[bytes]:
        """Get image data from 50-item LRU session RAM cache."""
        global _session_memory_cache
        app_id_str = str(app_id)
        if app_id_str in _session_memory_cache:
            _session_memory_cache.move_to_end(app_id_str)
            return _session_memory_cache[app_id_str]
        return None

    @staticmethod
    def put_to_session_cache(app_id: str, data: bytes) -> None:
        """Put image data into 50-item LRU session RAM cache."""
        global _session_memory_cache
        if not app_id or not data:
            return
        app_id_str = str(app_id)
        _session_memory_cache[app_id_str] = data
        _session_memory_cache.move_to_end(app_id_str)
        while len(_session_memory_cache) > MAX_SESSION_CACHE_SIZE:
            _session_memory_cache.popitem(last=False)

    @staticmethod
    def promote_to_permanent_cache(app_id: Union[str, int]) -> bool:
        """Promote an in-memory session image to permanent disk cache when installed or downloaded."""
        app_id_str = str(app_id)
        data = ImageFetcher.get_from_session_cache(app_id_str)
        if data:
            ImageFetcher.save_to_cache(app_id_str, data)
            return True
        return False

    # --------------------------
    # Permanent Disk Cache
    # --------------------------

    @staticmethod
    def get_cache_dir() -> Path:
        return Path.home() / ".local" / "share" / "ACCELA" / "image_cache"

    @staticmethod
    def get_cache_path(app_id: str) -> Path:
        return ImageFetcher.get_cache_dir() / f"{app_id}.jpg"

    @staticmethod
    def save_to_cache(app_id: str, data: bytes) -> None:
        try:
            # Write exact original bytes directly (no compression, no resize)
            cache_dir = ImageFetcher.get_cache_dir()
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / f"{app_id}.jpg"
            cache_path.write_bytes(data)
        except Exception as e:
            logger.warning(f"Failed to cache image for AppID {app_id}: {e}")

    # --------------------------
    # Uninstalled Cache Scrubber
    # --------------------------

    @staticmethod
    def get_installed_appids() -> Set[str]:
        """Collects all installed AppIDs across Steam libraries, depots, and cached LUAs."""
        installed: Set[str] = set()
        try:
            from core.steam_helpers import get_steam_libraries
            libs = get_steam_libraries()
            for lib in libs:
                steamapps = Path(lib) / "steamapps"
                if steamapps.exists():
                    for acf in steamapps.glob("appmanifest_*.acf"):
                        m = re.search(r"appmanifest_(\d+)\.acf", acf.name)
                        if m:
                            installed.add(m.group(1))
        except Exception:
            pass

        try:
            from utils.helpers import get_base_path
            base = get_base_path()
            depots_dir = base / "depots"
            if depots_dir.exists():
                for d in depots_dir.glob("*.depot"):
                    installed.add(d.stem)
            cached_luas = base / "cached_luas"
            if cached_luas.exists():
                for lua in cached_luas.glob("*.lua"):
                    stem = lua.stem.replace("accela_", "")
                    installed.add(stem)
        except Exception:
            pass

        return installed

    @staticmethod
    def cleanup_uninstalled_cache() -> int:
        """Scans image_cache/ and removes thumbnails of games that are not installed."""
        try:
            cache_dir = ImageFetcher.get_cache_dir()
            if not cache_dir.exists():
                return 0

            installed_appids = ImageFetcher.get_installed_appids()
            deleted_count = 0

            for f in cache_dir.glob("*.jpg"):
                if f.stem not in installed_appids:
                    try:
                        f.unlink()
                        deleted_count += 1
                    except Exception:
                        pass

            if deleted_count > 0:
                logger.info(
                    f"Cleaned up {deleted_count} non-installed cached search images from disk."
                )
            return deleted_count
        except Exception as e:
            logger.warning(f"Error enforcing image cache limit: {e}")
            return 0

    # Legacy method for compatibility
    def run(self) -> None:
        self.start()

    @staticmethod
    @time_function
    def _get_best_image_url(url_list: List[str]) -> str:
        """URL checking with HEAD requests to find a working image URL."""
        if len(url_list) == 1:
            return url_list[0]

        for url in url_list:
            if send_request(url):
                return url

        # Fallback
        return url_list[0]

    @staticmethod
    @time_function
    def get_header_image_url(app_id: Union[int, str]) -> str:
        """
        Get the best header image URL for a given app ID.
        Prioritizes Local DB
        """
        # Try DB for the specific hash URL
        if DatabaseManager:
            try:
                # Force string for DB lookup consistency
                db_url = DatabaseManager().get_header_url(str(app_id))
                if db_url:
                    # logger.debug(f"DB Cache HIT for AppID {app_id}")
                    return db_url
                # else:
                #     logger.debug(f"DB Cache MISS for AppID {app_id}")
            except Exception as e:
                # LOG the error instead of silently passing
                logger.warning(f"DB Cache lookup failed for AppID {app_id}: {e}")

        # 2. Fallback to generic URL construction
        base_urls = [
            f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/header.jpg",
            f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg",
            f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/library_header.jpg",
            f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/library_hero.jpg",
        ]

        # Specific override for known issues
        if str(app_id) == "3949040":
            return (
                "https://cdn.akamai.steamstatic.com/steam/apps/3949040/library_hero.jpg"
            )

        # Return first URL immediately - validation is too slow for UI lists
        return base_urls[0]

    @staticmethod
    @time_function
    def fetch_header_from_web_api(app_id: Union[int, str]) -> Optional[str]:
        url = "https://store.steampowered.com/api/appdetails"
        params = {"appids": app_id}
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            logger.warning(f"Header API request failed for AppID {app_id}: {e}")
            return None

        app_data = data.get(str(app_id))
        if not app_data or not app_data.get("success"):
            return None

        return app_data.get("data", {}).get("header_image")

    @staticmethod
    def get_capsule_image_url(app_id: Union[int, str]) -> str:
        """Get the best capsule image URL for a given app ID."""
        urls = [
            f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/capsule_184x69.jpg",
            f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/library_capsule.jpg",
        ]

        if str(app_id) == "3949040":
            return "https://cdn.akamai.steamstatic.com/steam/apps/3949040/library_capsule.jpg"

        return ImageFetcher._get_best_image_url(urls)
