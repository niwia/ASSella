import logging
import requests
import json
import os
import tempfile
import re
import time
import threading
import queue
from typing import Optional, Dict, List, Tuple, Callable

from utils.image_fetcher import ImageFetcher
from managers.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

# Exponential backoff config for batched requests
BACKOFF_BASE = 1.0
BACKOFF_MAX = 30.0
BACKOFF_MULTIPLIER = 2.0
_batched_consecutive_failures = 0
_batched_failure_lock = threading.Lock()

try:
    from steam.client import SteamClient
    # Upstream steam-py bug workaround: Cryptodome raises ValueError on truncated/unpadded packets,
    # but cm.py _recv_messages only catches RuntimeError. Wrap ValueError into RuntimeError
    # so gevent greenlets disconnect cleanly on network drops without dumping raw tracebacks.
    try:
        import steam.core.crypto as steam_crypto
        _orig_sym_decrypt_hmac = steam_crypto.symmetric_decrypt_HMAC

        def _safe_symmetric_decrypt_HMAC(cyphertext, key, hmac_secret):
            try:
                return _orig_sym_decrypt_hmac(cyphertext, key, hmac_secret)
            except ValueError as val_err:
                raise RuntimeError(f"Unable to decrypt message (truncated block): {val_err}") from val_err

        steam_crypto.symmetric_decrypt_HMAC = _safe_symmetric_decrypt_HMAC
    except Exception:
        pass
except ImportError:
    SteamClient = None
    logger.warning(
        "`steam[client]` package not found. Skipping steam.client fetch method."
    )


class _SteamClientWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="SteamClientWorker")
        self.q = queue.Queue()
        self.client = None
        self._started_evt = threading.Event()

    def run(self):
        if not SteamClient:
            self._started_evt.set()
            return
        try:
            logger.debug("SteamClientWorker starting & initializing SteamClient...")
            self.client = SteamClient()
            self.client.connect(retry=5)  # try up to 5 CM servers before giving up
            self.client.anonymous_login()
            # Block until Valve CM sends the actual LogOnResponse (not just the call returning).
            # anonymous_login() is non-blocking — it dispatches and returns immediately.
            # Without this wait, _started_evt can fire before logged_on=True, causing
            # execute() to see a not-yet-logged-in client and raise ConnectionError.
            login_ok = self.client.wait_event("logged_on", timeout=15)
            if login_ok is None:
                logger.error("SteamClientWorker: timed out waiting for logged_on event")
            else:
                logger.debug("SteamClientWorker connected & logged on anonymously.")
        except Exception as e:
            logger.error(f"SteamClientWorker initial connect error: {e}")
        finally:
            # Signal readiness only AFTER login is confirmed (or failed)
            self._started_evt.set()

        while True:
            item = self.q.get()
            if item is None:
                break
            func_name, args, kwargs, reply_q = item
            try:
                if not self.client or not getattr(self.client, "connected", False) or not getattr(self.client, "logged_on", False):
                    logger.info("SteamClientWorker: connection lost or idle, reconnecting to Valve CM...")
                    if self.client is None:
                        self.client = SteamClient()
                    reconnected = False
                    try:
                        self.client.connect(retry=5)
                        self.client.anonymous_login()
                        # Wait for login before firing the queued query
                        login_ok = self.client.wait_event("logged_on", timeout=15)
                        if login_ok is not None:
                            reconnected = True
                            logger.info("SteamClientWorker reconnected & logged on successfully.")
                        else:
                            logger.error("SteamClientWorker: reconnect timed out waiting for logged_on")
                    except Exception as rec_err:
                        logger.error(f"SteamClientWorker reconnect error: {rec_err}")

                    if not reconnected:
                        # Don't fire the query on a still-dead client — signal failure immediately
                        reply_q.put((False, ConnectionError("SteamClient reconnect failed; skipping query")))
                        self.q.task_done()
                        continue

                # Run query with automatic single retry on mid-query disconnect
                try:
                    res = getattr(self.client, func_name)(*args, **kwargs)
                except Exception as call_err:
                    if not getattr(self.client, "connected", False) or not getattr(self.client, "logged_on", False):
                        logger.warning(f"SteamClientWorker disconnected during {func_name} ({call_err}), reconnecting to retry...")
                        try:
                            self.client.connect(retry=5)
                            self.client.anonymous_login()
                            if self.client.wait_event("logged_on", timeout=15) is not None:
                                logger.info(f"SteamClientWorker reconnected, retrying {func_name}...")
                                res = getattr(self.client, func_name)(*args, **kwargs)
                            else:
                                raise call_err
                        except Exception:
                            raise call_err
                    else:
                        raise call_err

                reply_q.put((True, res))
            except Exception as e:
                logger.error(f"SteamClientWorker task error ({func_name}): {e}")
                reply_q.put((False, e))
            finally:
                self.q.task_done()

    def execute(self, func_name, *args, timeout=30, **kwargs):
        # Wait up to 20s for initial worker thread readiness
        self._started_evt.wait(timeout=20)
        # We do not abort early on self.client.connected here; if the socket closed
        # during idle periods, the worker loop will seamlessly reconnect and execute the query!
        reply_q = queue.Queue()
        self.q.put((func_name, args, kwargs, reply_q))
        try:
            ok, res = reply_q.get(timeout=timeout)
            if not ok:
                raise res
            return res
        except queue.Empty:
            raise TimeoutError(f"SteamClientWorker query '{func_name}' timed out after {timeout}s")


_steam_worker_instance = None
_steam_worker_lock = threading.Lock()


def get_steam_worker() -> _SteamClientWorker:
    global _steam_worker_instance
    with _steam_worker_lock:
        if _steam_worker_instance is None or not _steam_worker_instance.is_alive():
            _steam_worker_instance = _SteamClientWorker()
            _steam_worker_instance.start()
        return _steam_worker_instance


def get_shared_client():
    worker = get_steam_worker()
    return worker.client


def disconnect_shared_client():
    pass


CACHE_DIR = os.path.join(tempfile.gettempdir(), "mistwalker_api_cache")
CACHE_EXPIRATION_SECONDS = 86400


import concurrent.futures

def fetch_steamcmd_info(app_id: str) -> dict:
    """
    Fetch app metadata directly from SteamCMD REST API (https://api.steamcmd.net/v1/info/:id).
    Fast, stateless HTTP request with no socket or gevent overhead.
    Returns standard app info dict or {} on failure.
    """
    appid_str = str(app_id)
    url = f"https://api.steamcmd.net/v1/info/{appid_str}"
    max_retries = 2
    for attempt in range(max_retries):
        try:
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                payload = res.json()
                if payload.get("status") == "success":
                    app_data = payload.get("data", {}).get(appid_str, {})
                    if app_data and isinstance(app_data, dict):
                        depots_raw = app_data.get("depots", {}) if isinstance(app_data.get("depots"), dict) else {}
                        branches_raw = depots_raw.get("branches", {}) if isinstance(depots_raw.get("branches"), dict) else {}

                        depot_info = {}
                        for depot_id, depot_data in depots_raw.items():
                            if not isinstance(depot_data, dict):
                                continue
                            config = depot_data.get("config", {})
                            manifests = depot_data.get("manifests", {})
                            manifest_public = manifests.get("public", {})
                            manifest_id = (
                                manifest_public.get("gid")
                                if isinstance(manifest_public, dict)
                                else manifest_public
                            )
                            depot_info[depot_id] = {
                                "name": depot_data.get("name"),
                                "oslist": config.get("oslist"),
                                "language": config.get("language"),
                                "steamdeck": config.get("steamdeck") == "1",
                                "size": None,
                                "manifest_id": manifest_id,
                                "manifests": depot_data.get("manifests"),
                            }

                        public_branch = branches_raw.get("public", {})
                        build_id = public_branch.get("buildid") if isinstance(public_branch, dict) else None
                        time_updated = public_branch.get("timeupdated") if isinstance(public_branch, dict) else None
                        app_name = app_data.get("common", {}).get("name")
                        installdir = app_data.get("config", {}).get("installdir")
                        header_url = ImageFetcher.get_header_image_url(int(appid_str)) if appid_str.isdigit() else None

                        return {
                            "depots": depot_info,
                            "branches": branches_raw,
                            "installdir": installdir,
                            "header_url": header_url,
                            "buildid": build_id,
                            "timeupdated": time_updated,
                            "name": app_name,
                        }
            else:
                logger.debug(f"SteamCMD API returned HTTP {res.status_code} for AppID {appid_str} (attempt {attempt+1}/{max_retries})")
        except Exception as e:
            logger.debug(f"SteamCMD API request attempt {attempt+1}/{max_retries} failed for AppID {appid_str}: {e}")

        if attempt < max_retries - 1:
            time.sleep(0.3)

    return {}


def batched_fetch_steamcmd_info(
    app_ids: List[str],
    max_workers: int = 50,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, dict]:
    """
    Fetch app info for multiple AppIDs concurrently using SteamCMD REST API.
    Calls on_progress(completed, total) as each request finishes for real-time UI counters.
    Returns Dict[appid_str, app_info_dict].
    Benchmarked optimal: 50 workers / 3s timeout → ~30 apps/s, max latency ~1.5s.
    """
    if not app_ids:
        return {}

    results = {}
    total = len(app_ids)
    completed = 0
    lock = threading.Lock()

    def _worker(appid_str):
        return appid_str, fetch_steamcmd_info(appid_str)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, total)) as executor:
        futures = [executor.submit(_worker, str(aid)) for aid in app_ids]
        for future in concurrent.futures.as_completed(futures):
            try:
                aid, data = future.result()
                with lock:
                    completed += 1
                    if data and data.get("depots"):
                        results[aid] = data
                    if on_progress:
                        try:
                            on_progress(completed, total)
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"Error in batched SteamCMD worker: {e}")

    logger.info(f"SteamCMD REST API fetched info for {len(results)}/{total} AppIDs")
    return results


def get_depot_info_from_api(app_id, access_token=None):
    # 1. Try to get complete info from DB first
    db = DatabaseManager()
    db_data = db.get_app_info(app_id)

    has_valid_name = False
    if db_data and db_data.get("name"):
        current_name = db_data["name"]
        is_generic = re.match(
            r"^App[ _]?" + str(app_id) + r"$", current_name, re.IGNORECASE
        )
        if not is_generic:
            has_valid_name = True

    if db_data and db_data.get("depots") and has_valid_name:
        logger.info(f"Loaded AppID {app_id} from database.")
        return db_data

    if db_data and not has_valid_name:
        logger.info(
            f"Cached data for AppID {app_id} has generic/missing name. Forcing API refresh."
        )

    # 2. Try live Steam PICS first if available (direct from Valve, up-to-date)
    final_data = None
    if SteamClient:
        logger.info(f"Attempting to fetch app info for AppID {app_id} using live Steam PICS...")
        steam_client_data = _fetch_with_steam_client(app_id, access_token)
        if steam_client_data and steam_client_data.get("depots"):
            logger.info(f"Successfully fetched AppID {app_id} via live Steam PICS.")
            final_data = steam_client_data

    # 3. Fallback to SteamCMD REST API (caching mirror)
    if not final_data:
        logger.info(f"Attempting to fetch app info for AppID {app_id} using SteamCMD REST API...")
        steamcmd_data = fetch_steamcmd_info(app_id)
        if steamcmd_data and steamcmd_data.get("depots"):
            logger.info(f"Successfully fetched AppID {app_id} via SteamCMD REST API fallback.")
            final_data = steamcmd_data

    web_api_data = _fetch_with_web_api(app_id)
    if not final_data:
        logger.warning(
            f"Steam PICS and SteamCMD failed for AppID {app_id}. Falling back to public Web API."
        )
        final_data = web_api_data

    if web_api_data.get("header_url"):
        if final_data.get("header_url") != web_api_data.get("header_url"):
            final_data["header_url"] = web_api_data["header_url"]
    elif not final_data.get("header_url"):
        logger.warning("Header URL not found in Web API or steam.client.")

    if not final_data.get("name") and web_api_data.get("name"):
        logger.info("Using Web API fallback for game name.")
        final_data["name"] = web_api_data["name"]

    if final_data:
        db.upsert_app_info(app_id, final_data)

    return final_data


def _fetch_with_steam_client(app_id, access_token=None):
    if not SteamClient:
        return {}
    try:
        try:
            int_app_id = int(app_id)
        except (ValueError, TypeError):
            logger.error(
                f"Invalid AppID format: '{app_id}'. Cannot convert to integer."
            )
            return {}

        # Build request list with token if provided (similar to mani.py)
        if access_token:
            # Convert token to int if it's a numeric string
            try:
                token_int = int(access_token)
                request_list = [{"appid": int_app_id, "access_token": token_int}]
                logger.debug(f"Using access token for AppID {app_id}")
            except (ValueError, TypeError):
                # If token is not numeric, use as string
                request_list = [{"appid": int_app_id, "access_token": access_token}]
                logger.debug(f"Using non-numeric access token for AppID {app_id}")
        else:
            request_list = [int_app_id]

        worker = get_steam_worker()
        result = worker.execute("get_product_info", apps=request_list, timeout=25)
        # Only write the debug dump when DEBUG logging is explicitly enabled
        if logger.isEnabledFor(logging.DEBUG):
            debug_dump_path = os.path.join(
                tempfile.gettempdir(), f"mistwalker_steamclient_response_{int_app_id}.json"
            )
            try:
                with open(debug_dump_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=4, default=str)
                logger.debug(
                    f"DEBUG: Raw steam.client response dumped to {debug_dump_path}"
                )
            except Exception as e:
                logger.error(f"DEBUG: Failed to dump raw response: {e}", exc_info=True)
        try:
            cleaned_result = json.loads(json.dumps(result, default=str))
        except Exception as e:
            logger.error(f"Failed to 'clean' the raw steam.client response: {e}")
            cleaned_result = {}
        app_data = cleaned_result.get("apps", {}).get(str(int_app_id), {})
        depot_info = {}
        installdir = None
        header_url = None
        build_id = None
        app_name = None

        if app_data:
            common_data = app_data.get("common", {})
            app_name = common_data.get("name")

            installdir = app_data.get("config", {}).get("installdir")
            header_path_fragment = common_data.get("header_image", {}).get("english")
            if header_path_fragment:
                header_url = ImageFetcher.get_header_image_url(int_app_id)
                logger.debug(f"Found header image URL: {header_url}")

            open_branches = {}
            try:
                all_branches = app_data.get("depots", {}).get("branches", {})
                if isinstance(all_branches, dict):
                    for b_name, b_info in all_branches.items():
                        if isinstance(b_info, dict):
                            if b_info.get("pwdrequired") != "1":
                                open_branches[b_name] = {
                                    "buildid": str(b_info.get("buildid", "")),
                                    "timeupdated": str(b_info.get("timeupdated", ""))
                                }
                build_id = (
                    app_data.get("depots", {})
                    .get("branches", {})
                    .get("public", {})
                    .get("buildid")
                )
                if build_id:
                    logger.info(f"Found public buildid: {build_id}")
                else:
                    logger.warning(
                        "Could not find public buildid in steam.client response."
                    )
            except Exception as e:
                logger.error(f"Error parsing buildid: {e}")

            depots = app_data.get("depots", {})
            for depot_id, depot_data in depots.items():
                if depot_id in ("branches", "workshopdepots", "branches_public") or not isinstance(depot_data, dict):
                    continue
                config = depot_data.get("config", {})
                manifests = depot_data.get("manifests", {})
                manifest_public = manifests.get("public", {})

                # Handle both dict and simple formats for manifest data
                if isinstance(manifest_public, dict):
                    manifest_id = manifest_public.get("gid")
                    size_str = manifest_public.get("size")
                else:
                    # Simple format where the value IS the manifest ID
                    manifest_id = manifest_public
                    size_str = None

                logger.debug(
                    f"Depot {depot_id}: Found raw size from API: {size_str} (Type: {type(size_str)})"
                )
                logger.debug(f"Depot {depot_id}: Found manifest_id: {manifest_id}")
                depot_info[depot_id] = {
                    "name": depot_data.get("name"),
                    "oslist": config.get("oslist"),
                    "language": config.get("language"),
                    "steamdeck": config.get("steamdeck") == "1",
                    "size": size_str,
                    "manifest_id": manifest_id,
                    "manifests": manifests,
                }
        api_data = {
            "depots": depot_info,
            "installdir": installdir,
            "header_url": header_url,
            "buildid": build_id,
            "name": app_name,
            "branches": open_branches,
        }
        if api_data and (
            api_data.get("depots") or api_data.get("buildid") or api_data.get("name")
        ):
            logger.info("steam.client fetch successful.")
            return api_data
        else:
            logger.warning("steam.client fetch returned no meaningful data.")
    except BaseException as e:
        logger.error(
            f"An unexpected error occurred in _fetch_with_steam_client: {e}",
            exc_info=True,
        )
        # Do not discard the shared client on query errors to prevent login-spamming
        pass
    logger.error("steam.client fetch failed.")
    return {}


def find_branch_for_buildid(appid: str, buildid: str) -> Optional[str]:
    """
    Looks up live Steam PICS branch metadata for an appid and identifies which branch
    corresponds to the given buildid.
    Returns branch name (e.g. 'testingbranch', 'public') or None if not found/matched.
    """
    if not appid or not buildid:
        return None
    try:
        branches = get_app_branches(str(appid))
        if isinstance(branches, dict):
            for b_name, b_info in branches.items():
                if isinstance(b_info, dict) and str(b_info.get("buildid")) == str(buildid):
                    logger.info(f"[SteamAPI] Steam PICS matched buildid {buildid} -> branch '{b_name}' for AppID {appid}")
                    return b_name
    except BaseException as e:
        logger.debug(f"[SteamAPI] Failed to lookup branch for buildid {buildid}: {e}")
    return None


def _fetch_with_web_api(app_id):
    url = "https://store.steampowered.com/api/appdetails"
    params = {"appids": app_id}
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        return _parse_web_api_response(app_id, data)
    except requests.exceptions.RequestException as e:
        logger.error(f"Web API request failed for AppID {app_id}: {e}")
    return {}


def _parse_web_api_response(app_id, data):
    depot_info = {}
    installdir = None
    header_url = None
    app_name = None
    app_data_wrapper = data.get(str(app_id))
    if app_data_wrapper and app_data_wrapper.get("success"):
        app_data = app_data_wrapper.get("data", {})
        installdir = app_data.get("install_dir")
        header_url = app_data.get("header_image")
        app_name = app_data.get("name")
        depots = app_data.get("depots", {})
        for depot_id, depot_data in depots.items():
            if not isinstance(depot_data, dict):
                continue
            size_str = depot_data.get("max_size")
            logger.debug(f"Depot {depot_id} (Web API): Found raw size: {size_str}")
            depot_info[depot_id] = {
                "name": depot_data.get("name"),
                "oslist": None,
                "language": None,
                "steamdeck": False,
                "size": size_str,
            }
    return {
        "depots": depot_info,
        "installdir": installdir,
        "header_url": header_url,
        "name": app_name,
    }


def batched_get_product_info(
    appid_list,
    access_tokens=None,
    batch_size=20,
    rate_limit_delay=0.3,
    is_cancelled=None,
    request_timeout=25,
    on_progress=None,
):
    if access_tokens is None:
        access_tokens = {}
    if not SteamClient:
        logger.warning("SteamClient not available, cannot perform batched fetch")
        return {}

    if not appid_list:
        logger.warning("Empty appid_list provided to batched_get_product_info")
        return {}

    logger.info(
        f"Starting batched fetch for {len(appid_list)} appids (batch_size={batch_size})"
    )

    # Split appids into batches
    batches = []
    for i in range(0, len(appid_list), batch_size):
        batch = appid_list[i : i + batch_size]
        batches.append(batch)

    logger.info(f"Split into {len(batches)} batches")

    all_results = {}
    failed_appids = []
    processed_count = 0

    global _batched_consecutive_failures

    shared_client = None
    try:
        shared_client = get_shared_client()
    except Exception as e:
        logger.error(f"Batched fetch: Error obtaining shared SteamClient: {e}")
        shared_client = None

    # Process each batch
    for batch_idx, batch_appids in enumerate(batches):
        if is_cancelled and is_cancelled():
            logger.info("Batched fetch cancelled before batch execution")
            break

        # Convert appids to integers
        int_appids = []
        for appid in batch_appids:
            try:
                int_appids.append(int(appid))
            except (ValueError, TypeError):
                logger.error(f"Invalid AppID: '{appid}'")
                failed_appids.append(appid)

        if not int_appids:
            continue

        # Build request list with access tokens if available
        request_list = []
        for appid in int_appids:
            appid_str = str(appid)
            token = access_tokens.get(appid_str)
            if token:
                try:
                    request_list.append({"appid": appid, "access_token": int(token)})
                except (ValueError, TypeError):
                    request_list.append(appid)
            else:
                request_list.append(appid)

        # Retry loop for fallback mechanism
        batch_success = False
        max_batch_retries = 2
        for attempt in range(max_batch_retries):
            try:
                worker = get_steam_worker()
                result = worker.execute("get_product_info", apps=request_list, timeout=request_timeout)

                # Process results
                if result and isinstance(result, dict):
                    cleaned_result = json.loads(json.dumps(result, default=str))
                    apps_data = cleaned_result.get("apps", {})

                    for int_appid in int_appids:
                        appid_str = str(int_appid)
                        app_data = apps_data.get(appid_str, {})
                        build_id = None
                        app_name = None
                        time_updated = None

                        # Parse the app data
                        depot_info = {}
                        depots = {}
                        branches = {}
                        if app_data:
                            app_data.get("config", {}).get("installdir")
                            ImageFetcher.get_header_image_url(int_appid)
                            app_name = app_data.get("common", {}).get("name")
                            try:
                                public_branch = (
                                    app_data.get("depots", {})
                                    .get("branches", {})
                                    .get("public", {})
                                )
                                if isinstance(public_branch, dict):
                                    build_id = public_branch.get("buildid")
                                    time_updated = public_branch.get("timeupdated")
                            except AttributeError:
                                pass

                            depots = app_data.get("depots", {}) if isinstance(app_data.get("depots"), dict) else {}
                            branches = depots.get("branches", {}) if isinstance(depots.get("branches"), dict) else {}

                            for depot_id, depot_data in depots.items():
                                if not isinstance(depot_data, dict):
                                    continue
                                config = depot_data.get("config", {})
                                manifests = depot_data.get("manifests", {})
                                manifest_public = manifests.get("public", {})

                                manifest_id = (
                                    manifest_public.get("gid")
                                    if isinstance(manifest_public, dict)
                                    else manifest_public
                                )

                                depot_info[depot_id] = {
                                    "name": depot_data.get("name"),
                                    "oslist": config.get("oslist"),
                                    "language": config.get("language"),
                                    "steamdeck": config.get("steamdeck") == "1",
                                    "size": None,
                                    "manifest_id": manifest_id,
                                    "manifests": depot_data.get("manifests"),
                                }

                        all_results[appid_str] = {
                            "depots": depot_info,
                            "branches": branches,
                            "installdir": app_data.get("config", {}).get("installdir") if app_data else None,
                            "header_url": (
                                ImageFetcher.get_header_image_url(int_appid)
                                if app_data
                                else None
                            ),
                            "buildid": build_id,
                            "timeupdated": time_updated,
                            "name": app_name,
                            "listofdlc": app_data.get("extended", {}).get("listofdlc", "") if app_data else "",
                        }
                batch_success = True
                break

            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                logger.error(f"Batch {batch_idx + 1} (attempt {attempt + 1}/{max_batch_retries}): Error during fetch: {e}")
                if attempt + 1 < max_batch_retries and isinstance(e, (ConnectionError, TimeoutError)):
                    logger.info("Retrying batch after brief connection recovery pause...")
                    time.sleep(1.0)
                    continue
                if isinstance(e, (ConnectionError, TimeoutError)):
                    logger.warning("Steam PICS connection lost and reconnect failed. Aborting remaining batches.")
                    for rem_idx in range(batch_idx, len(batches)):
                        failed_appids.extend(batches[rem_idx])
                    break


        if not batch_success:
            failed_appids.extend(batch_appids)
            # Track backpressure
            with _batched_failure_lock:
                _batched_consecutive_failures += 1

            # Delay before next batch, with backoff on consecutive failures
            if is_cancelled and is_cancelled():
                logger.info("Batched fetch cancelled after batch execution")
                break

            if batch_idx < len(batches) - 1:
                delay = BACKOFF_BASE
                with _batched_failure_lock:
                    delay = min(BACKOFF_BASE * (BACKOFF_MULTIPLIER ** _batched_consecutive_failures), BACKOFF_MAX)
                logger.info(f"Backoff: waiting {delay:.1f}s before next batch (failures={_batched_consecutive_failures})")
                time.sleep(delay)
        else:
            # Success — decay backpressure
            with _batched_failure_lock:
                if _batched_consecutive_failures > 0:
                    _batched_consecutive_failures -= 1
            # Normal rate limiting between successful batches
            if batch_idx < len(batches) - 1 and rate_limit_delay > 0:
                time.sleep(rate_limit_delay)

        processed_count += len(batch_appids)
        if on_progress:
            try:
                on_progress(processed_count, len(appid_list))
            except Exception:
                pass

    success_count = len(all_results)
    failure_count = len(failed_appids)

    logger.info(f"Batched fetch: {success_count} succeeded, {failure_count} failed")

    if failure_count > 0:
        logger.debug(f"Failed appids: {failed_appids}")
    return all_results


def get_manifest_id(appid, depot_id=None, use_cache=True):
    try:
        if not use_cache:
            # Force a refresh by clearing any existing cache for this app
            db = DatabaseManager()
            db.clear_app_info(appid)

        app_data = get_depot_info_from_api(appid)
        if not app_data:
            return {
                "success": False,
                "manifest_id": None,
                "depot_id": depot_id,
                "error": "Failed to fetch app data",
            }

        depots = app_data.get("depots", {})
        if not depots:
            return {
                "success": False,
                "manifest_id": None,
                "depot_id": depot_id,
                "error": "No depots found for this app",
            }

        # Use specified depot or first depot
        if depot_id:
            if str(depot_id) not in depots:
                return {
                    "success": False,
                    "manifest_id": None,
                    "depot_id": depot_id,
                    "error": f"Depot {depot_id} not found",
                }
            target_depot_id = str(depot_id)
        else:
            target_depot_id = list(depots.keys())[0]

        depot_info = depots.get(target_depot_id, {})
        manifest_id = depot_info.get("manifest_id")

        if not manifest_id:
            # If manifest_id is missing from cached data, try force refresh
            if use_cache:
                logger.debug(
                    f"Manifest ID not found in cached data for {appid}, trying force refresh"
                )
                return get_manifest_id(appid, depot_id, use_cache=False)

            return {
                "success": False,
                "manifest_id": None,
                "depot_id": target_depot_id,
                "error": "No manifest ID found",
            }

        return {
            "success": True,
            "manifest_id": manifest_id,
            "depot_id": target_depot_id,
            "error": None,
        }

    except Exception as e:
        logger.error(f"Error fetching manifest for {appid}: {e}")
        return {
            "success": False,
            "manifest_id": None,
            "depot_id": depot_id,
            "error": f"Unexpected error: {str(e)}",
        }


_branch_cache = {}

def get_app_branches(appid: str, access_token: str = None, force_refresh: bool = False) -> dict:
    """
    Query Steam PICS for available open branches for an AppID.
    If force_refresh=False, returns cached branch info from DB or memory instantly.
    """
    try:
        if not force_refresh:
            if appid in _branch_cache:
                return _branch_cache[appid]

            info = get_depot_info_from_api(appid, access_token)
            cached_branches = info.get("branches") if info else None
            if cached_branches and isinstance(cached_branches, dict) and len(cached_branches) > 0:
                _branch_cache[appid] = cached_branches
                return cached_branches

            if info and info.get("buildid"):
                fallback_b = {"public": {"buildid": str(info.get("buildid"))}}
                _branch_cache[appid] = fallback_b
                return fallback_b

        from utils.settings import get_settings
        settings = get_settings()
        update_provider = settings.value("update_check_api_provider", "steampics", type=str)
        branches = {}

        if update_provider == "steampics":
            # 1. Primary: live steam.client PICS (Valve Direct, authoritative)
            logger.info(f"Fetching branches for AppID {appid} via live Steam PICS...")
            data = _fetch_with_steam_client(appid, access_token)
            branches = data.get("branches", {}) if data else {}
            if not branches and data:
                bid = data.get("buildid", "")
                if bid:
                    branches = {"public": {"buildid": str(bid)}}

            # Fallback to SteamCMD if PICS returned nothing
            if not branches:
                logger.info(f"Steam PICS returned no branches for AppID {appid}. Falling back to SteamCMD REST API...")
                cmd_info = fetch_steamcmd_info(appid)
                branches = cmd_info.get("branches", {}) if cmd_info else {}
        else:
            # 1. Primary: SteamCMD REST API (fast 0.2s HTTP call)
            cmd_info = fetch_steamcmd_info(appid)
            branches = cmd_info.get("branches", {}) if cmd_info else {}

            if not branches:
                # 2. Fallback to steam.client PICS
                logger.info(f"SteamCMD API returned no branches for AppID {appid}. Falling back to steam.client PICS...")
                data = _fetch_with_steam_client(appid, access_token)
                branches = data.get("branches", {}) if data else {}
                if not branches and data:
                    bid = data.get("buildid", "")
                    if bid:
                        branches = {"public": {"buildid": str(bid)}}

        if branches:
            db = DatabaseManager()
            db.upsert_app_info(appid, {"branches": branches})

        _branch_cache[appid] = branches
        return branches
    except BaseException as e:
        logger.error(f"Failed to fetch branches for AppID {appid}: {e}")
        # Try DB fallback if live query fails
        try:
            db = DatabaseManager()
            info = db.get_app_info(appid, bypass_expiration=True)
            cached_branches = info.get("branches") if info else None
            if cached_branches and isinstance(cached_branches, dict) and len(cached_branches) > 0:
                logger.info(f"Fell back to cached DB branches for AppID {appid} after live fetch failure")
                _branch_cache[appid] = cached_branches
                return cached_branches
        except Exception as db_err:
            logger.debug(f"DB fallback failed for AppID {appid}: {db_err}")
        return {"public": {"buildid": ""}}


def clear_branch_cache(appid: str = None) -> None:
    """Clear in-memory and database branch cache for a specific app or all apps."""
    global _branch_cache
    if appid:
        _branch_cache.pop(str(appid), None)
        try:
            db = DatabaseManager()
            db.clear_app_info(str(appid))
        except Exception:
            pass
    else:
        _branch_cache.clear()
        try:
            db = DatabaseManager()
            db.clear_all_branches()
        except Exception:
            pass

