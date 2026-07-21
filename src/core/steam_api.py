import logging
import requests
import json
import os
import tempfile
import re
import time

from utils.image_fetcher import ImageFetcher
from managers.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

try:
    from steam.client import SteamClient
except ImportError:
    SteamClient = None
    logger.warning(
        "`steam[client]` package not found. Skipping steam.client fetch method."
    )

CACHE_DIR = os.path.join(tempfile.gettempdir(), "mistwalker_api_cache")
CACHE_EXPIRATION_SECONDS = 86400


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

    logger.info(
        f"Attempting to fetch app info for AppID {app_id} using steam.client..."
    )
    steam_client_data = _fetch_with_steam_client(app_id, access_token)
    logger.info(f"Fetching Web API data for AppID {app_id} for header image...")
    web_api_data = _fetch_with_web_api(app_id)
    if steam_client_data and steam_client_data.get("depots"):
        logger.debug("Using depot and installdir info from steam.client.")
        final_data = steam_client_data
    else:
        logger.warning(
            f"steam.client method failed for AppID {app_id}. Falling back to public Web API for all data."
        )
        final_data = web_api_data
    if web_api_data.get("header_url"):
        if final_data.get("header_url") != web_api_data.get("header_url"):
            logger.info(
                "Overwriting steam.client header URL with more reliable Web API version."
            )
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
    client = SteamClient()
    try:
        logger.debug("Attempting Anonymous login")
        client.anonymous_login()
        if not client.logged_on:
            logger.error("Failed to anonymously login to Steam.")
            return {}
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

        result = client.get_product_info(apps=request_list, timeout=30)
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

            try:
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
                if not isinstance(depot_data, dict):
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
                }
        api_data = {
            "depots": depot_info,
            "installdir": installdir,
            "header_url": header_url,
            "buildid": build_id,
            "name": app_name,
        }
        logger.debug("Data processed, logging out.")
        client.logout()
        if api_data and (
            api_data.get("depots") or api_data.get("buildid") or api_data.get("name")
        ):
            logger.info("steam.client fetch successful.")
            return api_data
        else:
            logger.warning("steam.client fetch returned no meaningful data.")
    except Exception as e:
        logger.error(
            f"An unexpected error occurred in _fetch_with_steam_client: {e}",
            exc_info=True,
        )
    finally:
        if client and client.logged_on:
            logger.debug("Ensure logout in finally block.")
            client.logout()
    logger.error("steam.client fetch failed.")
    return {}


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
    request_timeout=10,
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

    shared_client = None
    try:
        shared_client = SteamClient()
        shared_client.anonymous_login()
        if not shared_client.logged_on:
            logger.warning("Batched fetch: Failed to establish shared SteamClient. Will fallback to per-batch clients.")
            shared_client = None
    except Exception as e:
        logger.error(f"Batched fetch: Error establishing shared SteamClient: {e}. Will fallback.")
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
        client = None
        for attempt in range(2):
            used_shared_client = False
            try:
                if attempt == 0 and shared_client and shared_client.logged_on:
                    client = shared_client
                    used_shared_client = True
                else:
                    logger.debug(f"Batch {batch_idx + 1}: Using new fallback client for attempt {attempt + 1}")
                    client = SteamClient()
                    client.anonymous_login()

                if is_cancelled and is_cancelled():
                    logger.info("Batched fetch cancelled after login")
                    break

                if not client.logged_on:
                    logger.error(f"Batch {batch_idx + 1}: Failed to login to Steam")
                    continue

                # Single API call for all appids in this batch
                result = client.get_product_info(apps=request_list, timeout=request_timeout)

                # Process results
                if result and isinstance(result, dict):
                    cleaned_result = json.loads(json.dumps(result, default=str))
                    apps_data = cleaned_result.get("apps", {})

                    for int_appid in int_appids:
                        appid_str = str(int_appid)
                        app_data = apps_data.get(appid_str, {})
                        build_id = None
                        app_name = None

                        # Parse the app data
                        depot_info = {}
                        if app_data:
                            app_data.get("config", {}).get("installdir")
                            ImageFetcher.get_header_image_url(int_appid)
                            app_name = app_data.get("common", {}).get("name")
                            build_id = None
                            time_updated = None
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

                            depots = app_data.get("depots", {})
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
                                }

                        all_results[appid_str] = {
                            "depots": depot_info,
                            "installdir": app_data.get("config", {}).get("installdir"),
                            "header_url": (
                                ImageFetcher.get_header_image_url(int_appid)
                                if app_data
                                else None
                            ),
                            "buildid": build_id,
                            "timeupdated": time_updated,
                            "name": app_name,
                        }
                batch_success = True
                break  # Success! Exit retry loop

            except BaseException as e:
                # gevent.timeout.Timeout inherits from BaseException, not Exception,
                # so a plain `except Exception` misses it entirely — the timeout
                # propagates uncaught all the way up and crashes the update-check thread.
                # Re-raise only genuine process-level exits; treat everything else
                # (including gevent timeouts and network errors) as a retryable failure.
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                is_timeout = type(e).__name__ == "Timeout"  # gevent.timeout.Timeout
                if is_timeout:
                    logger.warning(
                        f"Batch {batch_idx + 1} (Attempt {attempt + 1}): "
                        f"Steam API timed out after {request_timeout}s — "
                        "will retry with a new client if possible."
                    )
                else:
                    logger.error(
                        f"Batch {batch_idx + 1} (Attempt {attempt + 1}): "
                        f"Error during fetch: {e}"
                    )
                if used_shared_client:
                    # Drop the broken shared client so the next iteration uses the fallback
                    logger.info(
                        f"Batch {batch_idx + 1}: Shared client failed, "
                        "falling back to new client..."
                    )
                    shared_client = None
                # If we're on the last attempt, it's a hard failure
                if attempt == 1 or not used_shared_client:
                    break
            finally:
                if client and not used_shared_client and client.logged_on:
                    try:
                        client.logout()
                    except BaseException as e:
                        if isinstance(e, (KeyboardInterrupt, SystemExit)):
                            raise
                        logger.error(f"Error during fallback client logout: {e}")

        if not batch_success:
            failed_appids.extend(batch_appids)

        # Rate limiting: delay before next batch
        if is_cancelled and is_cancelled():
            logger.info("Batched fetch cancelled after batch execution")
            break

        if batch_idx < len(batches) - 1 and rate_limit_delay > 0:
            time.sleep(rate_limit_delay)

    success_count = len(all_results)
    failure_count = len(failed_appids)

    logger.info(f"Batched fetch: {success_count} succeeded, {failure_count} failed")

    if failure_count > 0:
        logger.debug(f"Failed appids: {failed_appids}")

    # Ensure shared client is logged out at the very end
    if shared_client and shared_client.logged_on:
        try:
            shared_client.logout()
        except Exception as e:
            logger.error(f"Error logging out shared client: {e}")

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
