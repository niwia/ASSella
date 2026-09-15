import multiprocessing
import os
import sys
import threading
from urllib.parse import unquote
import ctypes
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import QApplication
from utils.paths import Paths
from PyQt6.QtCore import QTimer, QMetaObject, Qt, Q_ARG
from ui.main_window import MainWindow
from ui.theme import update_appearance
from managers.cli_manager import run_cli_mode, open_cli_terminal
from utils.logger import setup_logging
from utils.settings import get_settings
from utils.yaml_config_manager import (
    backup_config_on_startup,
    ensure_slssteam_prerequisites,
    get_user_config_path,
)
from utils.version import app_version
from core.steam_helpers import fix_greenluma_offline_mode
from core.morrenus_api import download_manifest, get_selected_branch
from utils.helpers import create_font_from_settings


# -----------------------------------------------------------------------------
# Platform-specific adjustments
# -----------------------------------------------------------------------------
def set_app_user_model_id():
    """Set the AppUserModelID for Windows to ensure correct taskbar grouping."""
    if sys.platform == "win32":
        try:
            my_app_id = "god.is.in.the.wired.accela"
            # noinspection PyUnresolvedReferences
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(my_app_id)
        except (ImportError, AttributeError):
            pass  # Fails gracefully on non-Windows platforms or if ctypes is missing


set_app_user_model_id()

# Ensure project root is in path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def migrate_manifests(logger):
    """Migrate all cached manifest ZIP files from morrenus_manifests to hubcap_manifests."""
    try:
        from utils.helpers import get_base_path
        import shutil
        base = get_base_path()
        morrenus_dir = base / "morrenus_manifests"
        hubcap_dir = base / "hubcap_manifests"
        if morrenus_dir.exists():
            hubcap_dir.mkdir(parents=True, exist_ok=True)
            copied = 0
            for f in morrenus_dir.glob("accela_fetch_*.zip"):
                dest = hubcap_dir / f.name
                if not dest.exists():
                    try:
                        shutil.copy2(f, dest)
                        copied += 1
                    except Exception as e:
                        logger.warning(f"Failed to copy manifest {f.name}: {e}")
            if copied > 0:
                logger.info(f"Migrated {copied} manifest(s) from morrenus_manifests to hubcap_manifests")
    except Exception as e:
        logger.warning(f"Error during manifest migration: {e}")


def main():
    logger = setup_logging()
    migrate_manifests(logger)
    from utils.helpers import migrate_databases_to_db_folder
    migrate_databases_to_db_folder()

    logger.info("========================================")
    logger.info(f"ASSELA {app_version} starting...")
    logger.info("========================================")

    # Pre-scan for headless/helper mode to set environment variable before QApplication is created
    headless_mode = "--headless" in sys.argv or "--helper" in sys.argv or "-helper" in sys.argv or "--check-updates" in sys.argv
    if not headless_mode:
        for arg in sys.argv:
            if arg.startswith("accela://") and ("download/" in arg or "helper/" in arg):
                headless_mode = True
                break

    if headless_mode:
        logger.info("Headless/Helper mode requested. Setting QT_QPA_PLATFORM=offscreen")
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    # People only have substance within the memories of other people.

    app = QApplication(sys.argv)
    app.setApplicationName("ASSella")
    app.setDesktopFileName("assella.desktop")

    # Set default window icon for all dialogs and sub-windows
    icon_path = Paths.resource("logo/icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # -------------------------------------------------------------------------
    # Argument Parsing
    # -------------------------------------------------------------------------
    cli_mode = False
    helper_mode = False
    check_updates_mode = False
    command_line_zips = []
    command_line_appid = None

    def _add_zip_from_url(zip_url: str, found_suffix: str = "") -> bool:
        if not zip_url:
            return False
        if os.path.exists(zip_url):
            command_line_zips.append(zip_url)
            suffix = f" {found_suffix}" if found_suffix else ""
            logger.info(f"Found ZIP file from URL: {zip_url}{suffix}")
            return True
        logger.warning(f"ZIP file not found from URL: {zip_url}")
        return False

    def _parse_url_action(url: str):
        nonlocal cli_mode, command_line_appid, helper_mode
        try:
            url_content = url[9:]  # Remove 'accela://'
            if url_content.startswith("cli/"):
                cli_mode = True
                rest = url_content[4:]
            elif url_content.startswith("helper/"):
                helper_mode = True
                rest = url_content[7:]
            else:
                rest = url_content

            if "/" in rest:
                action, param_val = rest.split("/", 1)
                param_val = unquote(param_val)
            else:
                action = rest
                param_val = None

            if action == "download" and param_val and param_val.isdigit():
                command_line_appid = int(param_val)
                helper_mode = True
                logger.info(
                    f"Found accela://{action} URL for AppID: {param_val} (Helper mode)"
                )
            elif action == "zip" and param_val:
                _add_zip_from_url(param_val, "(Helper mode)" if helper_mode else "(GUI mode)")
            else:
                logger.warning(f"Invalid accela:// URL format: {url}")
        except ValueError as ve:
            logger.error(f"Failed to parse URL {url}: {ve}")

    settings = get_settings()
    server_port = int(settings.value("web_ui_port", 8765, type=int))
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-cli", "--cli"):
            cli_mode = True
        elif arg in ("-helper", "--helper"):
            helper_mode = True
        elif arg == "--check-updates":
            check_updates_mode = True
        elif arg == "--headless":
            # Already handled in pre-scan
            pass
        elif arg == "--port" and i + 1 < len(args):
            try:
                server_port = int(args[i + 1])
            except ValueError:
                logger.error(f"Invalid port: {args[i + 1]} (must be a number)")
            i += 1
        elif arg == "--appid" and i + 1 < len(args):
            appid_str = args[i + 1]
            if appid_str.isdigit():
                command_line_appid = int(appid_str)
            else:
                logger.error(f"Invalid AppID: {appid_str} (must be a number)")
            i += 1
        elif arg.startswith("accela://"):
            _parse_url_action(arg)
        elif arg.lower().endswith(".zip"):
            zip_file_path = os.path.abspath(arg)
            if os.path.exists(zip_file_path):
                command_line_zips.append(zip_file_path)
                logger.info(f"Found ZIP file from command line: {zip_file_path}")
            else:
                logger.warning(f"ZIP file not found: {arg}")
        i += 1

    if command_line_appid and command_line_zips:
        logger.error("Cannot use --appid and .zip files together. Choose one.")
        return None

    # -------------------------------------------------------------------------
    # Helper / Check Updates Mode Execution
    # -------------------------------------------------------------------------
    if check_updates_mode:
        logger.info("Will perform headless update check...")
        from managers.helper_manager import run_headless_update_check
        return run_headless_update_check(logger)

    if helper_mode and command_line_appid:
        logger.info(f"Will process AppID {command_line_appid} via Headless Helper")
        from managers.helper_manager import run_headless_helper
        return run_headless_helper(command_line_appid, logger)

    # -------------------------------------------------------------------------
    # CLI Mode Execution
    # -------------------------------------------------------------------------
    if cli_mode and (command_line_zips or command_line_appid):
        if cli_mode and sys.platform == "linux":
            if command_line_appid:
                logger.info(
                    f"Opening CLI mode in external terminal for AppID {command_line_appid}"
                )
                if open_cli_terminal(appid=command_line_appid):
                    return None
            elif command_line_zips:
                logger.info("Opening CLI mode in external terminal for ZIP(s)")
                if open_cli_terminal(zip_path=command_line_zips[0]):
                    return None

        if command_line_appid:
            logger.info(f"Will process AppID {command_line_appid} via CLI")
            return run_cli_mode(app, None, logger, appid=command_line_appid)
        else:
            logger.info(f"Processing {len(command_line_zips)} ZIPs via CLI")
            return run_cli_mode(app, command_line_zips, logger)

    # -------------------------------------------------------------------------
    # GUI Mode Initialization
    # -------------------------------------------------------------------------

    config_path = get_user_config_path()
    try:
        backup_created = backup_config_on_startup(config_path)
        if backup_created:
            logger.info("SLSsteam config backup created at startup")

        if config_path.exists():
            ensure_slssteam_prerequisites(config_path)
            
            try:
                from utils.yaml_config_manager import check_and_merge_fakeappid_db
                check_and_merge_fakeappid_db(config_path)
            except Exception as ex:
                logger.error(f"Failed to check and merge Fake AppID database: {ex}")
    except OSError as e:
        logger.error(f"Startup I/O error (Config/Backup): {e}")

    # 2. Settings & Theme
    settings = get_settings()
    accent_color = settings.value("accent_color", "#C06C84")
    bg_color = settings.value("background_color", "#000000")
    ui_mode = settings.value("ui_mode", "default")

    font_file = None
    font_to_use = QFont()

    if ui_mode == "sonic":
        accent_color = "#ffcc00"
        bg_color = "#002c83"
        font_file = settings.value("font-file", "sonic/sonic-1-hud-font.otf")
    else:
        font_to_use = create_font_from_settings(settings)

    # 3. GreenLuma Check
    try:
        fix_greenluma_offline_mode()
    except OSError as e:
        logger.warning(f"Failed to check GreenLuma status: {e}")

    # 4. Apply Appearance
    font_ok, font_info = update_appearance(
        app, accent_color, bg_color, font=font_to_use, font_file=font_file
    )
    if font_ok:
        logger.info(f"Loaded custom font: '{str(font_info)}'")
    else:
        logger.warning(f"Failed to load custom font: '{str(font_info)}'")

    # -------------------------------------------------------------------------
    # Main Window Launch
    # -------------------------------------------------------------------------
    try:
        main_win = MainWindow()
        if headless_mode:
            main_win.hide()
            main_win.toggle_web_server(True, port=server_port)
            logger.info("========================================")
            logger.info(f"ASSella running headless on http://0.0.0.0:{server_port}")
            logger.info("========================================")
        else:
            main_win.show()
            logger.info("Main window displayed successfully.")

        # ---------------------------------------------------------------------
        # Post-Launch Processing
        # ---------------------------------------------------------------------
        if command_line_zips or command_line_appid:

            def threaded_manifest_download(appid):
                """Background thread to handle network I/O."""
                try:
                    branch = get_selected_branch(appid)
                    logger.info(
                        f"Downloading manifest for AppID {appid} on branch '{branch}' (Background Thread)"
                    )
                    zip_file, error = download_manifest(appid, branch=branch)
                    if error:
                        logger.error(f"Failed to download manifest: {error}")
                        return

                    # Schedule the UI update on the main thread
                    # We use QMetaObject.invokeMethod to safely cross thread boundaries
                    QMetaObject.invokeMethod(
                        main_win,
                        "add_job_safely",
                        Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, zip_file),
                    )
                except Exception as ex:
                    logger.error(f"Threaded download failed: {ex}")

            def process_command_line_args():
                """Dispatcher for command line args."""
                if command_line_appid:
                    t = threading.Thread(
                        target=threaded_manifest_download,
                        args=(command_line_appid,),
                        daemon=True,
                    )
                    t.start()
                else:
                    logger.info(f"Adding {len(command_line_zips)} ZIP file(s) to queue")
                    for zip_file in command_line_zips:
                        main_win.job_queue.add_job(zip_file)

            def add_job_safely_patch(path):
                logger.info(f"Adding to queue: {os.path.basename(path)}")
                main_win.job_queue.add_job(path)

            # Monkey-patching the method onto the instance for safety
            main_win.add_job_safely = add_job_safely_patch

            QTimer.singleShot(0, process_command_line_args)

        sys.exit(app.exec())
    except Exception as e:
        logger.critical(
            f"A critical error occurred. Error: {e}",
            exc_info=True,
        )
        sys.exit(1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
