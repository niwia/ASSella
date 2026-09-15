import os
import sys
import json
import logging
import socket
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

from utils.paths import Paths
from managers.db_manager import DatabaseManager
from utils.settings import get_settings

logger = logging.getLogger(__name__)


def get_local_ip() -> str:
    """Robust way to get the local network IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't need to be reachable or send any packet
        s.connect(("10.254.254.254", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class WebRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Redirect standard http.server logs to python logging framework
        logger.debug("%s - - %s" % (self.address_string(), format % args))

    def _set_headers(self, content_type="application/json", status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(status=204)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        if path == "/":
            self._serve_web_ui()
        elif path == "/api/library":
            self._handle_get_library()
        elif path == "/api/status":
            self._handle_get_status()
        elif path == "/api/search":
            self._handle_get_search(query)
        elif path.startswith("/api/headers/"):
            self._handle_header_redirect(path)
        else:
            self.send_error(404, "File Not Found")

    def do_POST(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)

        if path == "/api/update":
            self._handle_post_update(query)
        elif path == "/api/prepare-download":
            self._handle_post_prepare_download()
        elif path == "/api/update-all":
            self._handle_post_update_all()
        elif path == "/api/check-updates":
            self._handle_post_check_updates()
        else:
            self.send_error(404, "Endpoint Not Found")

    def _serve_web_ui(self):
        try:
            ui_path = Paths.resource("web_ui.html")
            if ui_path.exists():
                with open(ui_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                self._set_headers("text/html", 200)
                self.wfile.write(html_content.encode("utf-8"))
            else:
                self.send_error(500, "Web UI Resource Missing")
        except Exception as e:
            logger.error(f"Web UI: failed to serve HTML: {e}")
            self.send_error(500, str(e))

    def _handle_get_library(self):
        main_win = self.server.main_window
        if not main_win or not main_win.game_manager:
            self._set_headers()
            self.wfile.write(json.dumps([]).encode("utf-8"))
            return

        try:
            db_mgr = DatabaseManager()
            games = main_win.game_manager.get_all_games()
            res = []
            for g in games:
                appid = str(g.get("appid", "0"))
                header_url = db_mgr.get_header_url(appid) or f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg"
                
                path = (
                    g.get("accela_marker_path")
                    or g.get("depot_downloader_path")
                    or g.get("appmanifest_path")
                    or g.get("install_path", "")
                )
                install_time = 0
                if path and os.path.exists(path):
                    try:
                        install_time = int(os.path.getmtime(path))
                    except Exception:
                        pass
                
                res.append({
                    "appid": appid,
                    "name": g.get("game_name", "Unknown"),
                    "update_status": g.get("update_status", "cannot_determine"),
                    "header_image_url": header_url,
                    "install_path": g.get("install_path", ""),
                    "size_bytes": g.get("size_on_disk", 0),
                    "install_time": install_time,
                })
            self._set_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
        except Exception as e:
            logger.error(f"Web UI: failed to retrieve library: {e}", exc_info=True)
            self.send_error(500, str(e))

    def _handle_get_status(self):
        main_win = self.server.main_window
        if not main_win or not main_win.task_manager:
            self._set_headers()
            self.wfile.write(json.dumps({"is_processing": False, "active_job": None, "queue": []}).encode("utf-8"))
            return

        try:
            task_manager = main_win.task_manager
            is_processing = task_manager.is_processing
            active_job = None

            if is_processing:
                metadata = task_manager.current_job_metadata or {}
                progress = 0
                speed = ""
                is_paused = bool(task_manager.is_download_paused)

                try:
                    if main_win.progress_bar and main_win.progress_bar.isVisible():
                        progress = main_win.progress_bar.value()
                except Exception:
                    pass

                try:
                    if main_win.speed_label and main_win.speed_label.isVisible():
                        speed = main_win.speed_label.text()
                except Exception:
                    pass

                # Determine active stage
                stage = "unknown"
                stage_label = "Processing..."
                if task_manager.is_cancelling:
                    stage = "cancelling"
                    stage_label = "Cancelling..."
                elif is_paused:
                    stage = "paused"
                    stage_label = "Paused"
                elif task_manager.zip_task_runner is not None:
                    stage = "processing_zip"
                    stage_label = "Processing manifest..."
                elif task_manager.download_runner is not None:
                    stage = "downloading"
                    stage_label = "Downloading..."
                elif task_manager.achievement_task_runner is not None:
                    stage = "achievements"
                    stage_label = "Generating Achievements..."
                elif task_manager.steamless_task is not None:
                    stage = "removing_drm"
                    stage_label = "Removing DRM..."

                active_job = {
                    "appid": metadata.get("appid"),
                    "name": metadata.get("game_name") or os.path.basename(task_manager.current_job or ""),
                    "progress": progress,
                    "speed": speed,
                    "is_paused": is_paused,
                    "stage": stage,
                    "stage_label": stage_label,
                }

            queue = []
            if main_win.job_queue:
                for job in main_win.job_queue.job_queue:
                    metadata = job.get("metadata", {})
                    name = metadata.get("game_name")
                    if not name:
                        name = os.path.basename(job["path"])
                    queue.append({
                        "appid": metadata.get("appid"),
                        "name": name,
                    })

            res = {
                "is_processing": is_processing,
                "active_job": active_job,
                "queue": queue,
            }
            self._set_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
        except Exception as e:
            logger.error(f"Web UI: failed to retrieve status: {e}", exc_info=True)
            self.send_error(500, str(e))

    def _handle_get_search(self, query):
        q_list = query.get("q")
        if not q_list or not q_list[0].strip():
            self._set_headers(status=400)
            self.wfile.write(json.dumps({"error": "Missing query parameter 'q'"}).encode("utf-8"))
            return

        q = q_list[0].strip()
        try:
            from core import morrenus_api
            results = morrenus_api.search_games(q)
            if isinstance(results, dict) and "error" in results:
                self._set_headers(status=500)
                self.wfile.write(json.dumps({"error": results["error"]}).encode("utf-8"))
                return

            game_results = results.get("results", []) if isinstance(results, dict) else []
            formatted_results = []
            for g in game_results:
                appid = str(g.get("game_id") or g.get("appid") or g.get("app_id") or g.get("id") or "0")
                name = g.get("game_name") or g.get("name") or g.get("title") or "Unknown"
                formatted_results.append({
                    "appid": appid,
                    "name": name,
                    "manifest_available": g.get("manifest_available", True),
                    "header_image_url": f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg",
                })
            self._set_headers()
            self.wfile.write(json.dumps(formatted_results).encode("utf-8"))
        except Exception as e:
            logger.error(f"Web UI: search error: {e}", exc_info=True)
            self.send_error(500, str(e))

    def _handle_header_redirect(self, path):
        try:
            # path format: /api/headers/<appid>.jpg
            parts = path.split("/")
            if len(parts) >= 4:
                filename = parts[3]
                appid = filename.split(".")[0]
                db_mgr = DatabaseManager()
                url = db_mgr.get_header_url(appid) or f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg"
                self.send_response(302)
                self.send_header("Location", url)
                self.end_headers()
                return
            self.send_error(404, "Invalid Header Path")
        except Exception as e:
            logger.error(f"Web UI: header redirect error: {e}")
            self.send_error(500, str(e))

    def _handle_post_update(self, query):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b""
        
        appid = None
        selected_depots = None
        local_path = None
        
        if post_data:
            try:
                data = json.loads(post_data.decode("utf-8"))
                appid = str(data.get("appid", ""))
                selected_depots = data.get("depots")
                local_path = data.get("local_path")
            except Exception:
                pass
                
        if not appid:
            appids = query.get("appid")
            if appids:
                appid = appids[0]
                
        if not appid or not appid.isdigit():
            self._set_headers(status=400)
            self.wfile.write(json.dumps({"error": "Missing or invalid appid parameter"}).encode("utf-8"))
            return

        # Start async manifest fetch + queueing in a separate thread so as not to block HTTP requests
        threading.Thread(
            target=self.server.download_and_queue_update_sync,
            args=(appid, selected_depots, local_path),
            daemon=True,
        ).start()

        self._set_headers()
        self.wfile.write(json.dumps({"status": "queued_update_job", "appid": appid}).encode("utf-8"))

    def _handle_post_prepare_download(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b""
        
        appid = None
        if post_data:
            try:
                data = json.loads(post_data.decode("utf-8"))
                appid = str(data.get("appid", ""))
            except Exception:
                pass
        
        if not appid:
            parsed_url = urlparse(self.path)
            query = parse_qs(parsed_url.query)
            appids = query.get("appid")
            if appids:
                appid = appids[0]

        if not appid or not appid.isdigit():
            self._set_headers(status=400)
            self.wfile.write(json.dumps({"error": "Missing or invalid appid parameter"}).encode("utf-8"))
            return

        try:
            from core import morrenus_api as _api
            from core.tasks.process_zip_task import ProcessZipTask

            # Use the bundle for the branch the user selected for this game
            branch = _api.get_selected_branch(appid)
            save_path = _api.get_manifest_zip_path(appid, branch)

            if save_path.exists():
                logger.info(f"Web UI: Using cached manifest ZIP for AppID {appid} at {save_path}")
                local_path = str(save_path)
            else:
                logger.info(f"Web UI: Preparing download (downloading manifest) for AppID {appid} (branch={branch})...")
                fpath, error = _api.download_manifest(appid, branch=branch)
                if error or not fpath:
                    self._set_headers(status=500)
                    self.wfile.write(json.dumps({"error": error or "Failed to download manifest"}).encode("utf-8"))
                    return
                local_path = str(fpath)

            zip_task = ProcessZipTask()
            parsed_data = zip_task.run(local_path)
            if not parsed_data:
                self._set_headers(status=500)
                self.wfile.write(json.dumps({"error": "Failed to parse manifest ZIP"}).encode("utf-8"))
                return

            depots_dict = parsed_data.get("depots") or {}
            depots_list = []
            for depot_id, info in depots_dict.items():
                depots_list.append({
                    "id": depot_id,
                    "desc": info.get("desc", f"Depot {depot_id}"),
                    "size": info.get("size", 0)
                })

            # Load smart selection & auto skip choices
            settings = get_settings()
            smart_active = settings.value("smart_depot_selection", False, type=bool)
            auto_skip = settings.value("auto_skip_single_choice", False, type=bool)
            
            cached_val = settings.value(f"depot_selection/{appid}", "", type=str)
            cached_selected = []
            cached_all = []
            if cached_val:
                try:
                    data = json.loads(cached_val)
                    cached_selected = data.get("selected", [])
                    cached_all = data.get("all_available", [])
                except Exception:
                    pass

            res = {
                "appid": appid,
                "game_name": parsed_data.get("game_name", f"App {appid}"),
                "local_path": local_path,
                "depots": depots_list,
                "smart_depot_selection": smart_active,
                "auto_skip_single_choice": auto_skip,
                "cached_selected": cached_selected,
                "cached_all": cached_all
            }
            self._set_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))

        except Exception as e:
            logger.error(f"Web UI: prepare-download error: {e}", exc_info=True)
            self.send_error(500, str(e))

    def _handle_post_update_all(self):
        threading.Thread(
            target=self.server.update_all_flow_sync,
            daemon=True,
        ).start()
        self._set_headers()
        self.wfile.write(json.dumps({"status": "queued_update_all_job"}).encode("utf-8"))

    def _handle_post_check_updates(self):
        self.server.web_command_queue.put({"type": "check_updates"})
        self._set_headers()
        self.wfile.write(json.dumps({"status": "queued_check_updates"}).encode("utf-8"))


class WebServer(ThreadingHTTPServer):
    def __init__(self, main_window, web_command_queue, host="0.0.0.0", port=8765):
        self.main_window = main_window
        self.web_command_queue = web_command_queue
        self.host = host
        self.port = port
        super().__init__((host, port), WebRequestHandler)


class WebServerManager:
    def __init__(self, main_window, web_command_queue):
        self.main_window = main_window
        self.web_command_queue = web_command_queue
        self.server = None
        self.server_thread = None

    def start(self, host="0.0.0.0", port=8765):
        if self.server:
            logger.warning("Web server is already running.")
            return False

        try:
            self.server = WebServer(self.main_window, self.web_command_queue, host, port)
            # Bind helpers for the request handlers to call
            self.server.download_and_queue_update_sync = self.download_and_queue_update_sync
            self.server.update_all_flow_sync = self.update_all_flow_sync
            
            self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.server_thread.start()
            logger.info(f"Web server started on http://{host}:{port} (local network: http://{get_local_ip()}:{port})")
            return True
        except OSError as e:
            import errno
            if e.errno == errno.EADDRINUSE:
                logger.info(f"Web server port {port} already in use (possibly another instance or background service is running).")
            else:
                logger.error(f"Failed to start web server: {e}", exc_info=True)
            self.server = None
            return False
        except Exception as e:
            logger.error(f"Failed to start web server: {e}", exc_info=True)
            self.server = None
            return False

    def stop(self):
        if not self.server:
            return False

        try:
            logger.info("Stopping web server...")
            self.server.shutdown()
            self.server.server_close()
            if self.server_thread:
                self.server_thread.join(timeout=2.0)
            logger.info("Web server stopped successfully.")
        except Exception as e:
            logger.error(f"Error stopping web server: {e}")
        finally:
            self.server = None
            self.server_thread = None
        return True

    def is_running(self):
        return self.server is not None

    def download_and_queue_update_sync(self, appid, selected_depots=None, local_path=None):
        try:
            from core import morrenus_api as _api
            from core.tasks.process_zip_task import ProcessZipTask

            # Use the bundle for the branch the user selected for this game
            branch = _api.get_selected_branch(appid)
            if not local_path:
                save_path = _api.get_manifest_zip_path(appid, branch)
                if save_path.exists():
                    logger.info(f"Web UI: Using cached manifest ZIP for AppID {appid} at {save_path}")
                    local_path = str(save_path)
                else:
                    logger.info(f"Web UI: Starting manifest download for AppID {appid} (branch={branch})...")
                    fpath, error = _api.download_manifest(appid, branch=branch)
                    if error or not fpath:
                        logger.warning(f"Web UI: manifest download failed for {appid}: {error}")
                        return
                    local_path = str(fpath)
            else:
                logger.info(f"Web UI: Using pre-downloaded manifest at {local_path} for AppID {appid}")

            # Parse for depots
            zip_task = ProcessZipTask()
            parsed_data = zip_task.run(local_path)
            if not parsed_data:
                logger.warning(f"Web UI: failed to parse manifest ZIP for {appid}")
                return

            depots = parsed_data.get("depots") or {}

            if not selected_depots:
                settings = get_settings()
                val = settings.value(f"depot_selection/{appid}", "", type=str)
                if val:
                    try:
                        data = json.loads(val)
                        selected_depots = data.get("selected", [])
                    except Exception:
                        pass

            if not selected_depots:
                # Fallback to all depots
                selected_depots = list(depots.keys())

            # Get game name and paths from current game manager
            game = None
            if self.main_window.game_manager:
                game = self.main_window.game_manager.get_game(appid)

            metadata = {
                "appid": appid,
                "library_path": game.get("library_path") if game else "",
                "install_path": game.get("install_path") if game else "",
                "game_name": game.get("game_name") if game else parsed_data.get("game_name", f"App {appid}"),
                "selected_depots_list": selected_depots,
                "branch": branch,
                "from_web_ui": True,
            }

            # Push to command queue
            self.web_command_queue.put({
                "type": "enqueue_job",
                "path": local_path,
                "metadata": metadata,
            })
            logger.info(f"Web UI: queued update for {metadata['game_name']} (appid={appid})")

        except Exception as e:
            logger.error(f"Web UI: failed to download/queue update for {appid}: {e}", exc_info=True)

    def update_all_flow_sync(self):
        try:
            if not self.main_window or not self.main_window.game_manager:
                return

            games = self.main_window.game_manager.get_all_games()
            settings = get_settings()
            updateable = []
            for g in games:
                if g.get("update_status") == "update_available":
                    appid = str(g.get("appid", ""))
                    if settings and settings.value(f"exclude_from_update_all/{appid}", False, type=bool):
                        continue
                    updateable.append(g)

            if not updateable:
                logger.info("Web UI: Update All triggered, but no games need updates.")
                return

            logger.info(f"Web UI: Starting sequential updates for {len(updateable)} games...")
            for g in updateable:
                appid = str(g.get("appid", "0"))
                if appid not in ("0", "N/A", "unknown"):
                    self.download_and_queue_update_sync(appid)
        except Exception as e:
            logger.error(f"Web UI: failed in update-all flow: {e}")
