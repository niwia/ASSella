import multiprocessing
import os
import sys
import threading
import ctypes
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import QApplication
from utils.paths import Paths
from PyQt6.QtCore import QTimer, Qt
from ui.main_window import MainWindow
from ui.theme import update_appearance
from managers.cli_manager import run_cli_mode, open_cli_terminal
from utils.logger import setup_logging
from utils.main_thread import post_to_main_thread
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

    try:
        from managers.voices_manager import VoicesManager
        VoicesManager.get_instance()
    except Exception as e:
        logger.warning(f"Error initializing VoicesManager: {e}")

    # Pre-scan for headless/helper mode to set environment variable before QApplication is created
    headless_mode = "--headless" in sys.argv or "--helper" in sys.argv or "-helper" in sys.argv or "--check-updates" in sys.argv or "-cli" in sys.argv or "--cli" in sys.argv

    if headless_mode:
        logger.info("Headless/Helper mode requested. Setting QT_QPA_PLATFORM=offscreen")
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    # People only have substance within the memories of other people.

    # Configure fractional high-DPI scaling policy before QApplication is constructed.
    # On Wayland (KDE Plasma 6 / SteamOS 3.5+ Desktop Mode), PassThrough prevents
    # font blur and window geometry distortion at 125%/150% fractional display scaling.
    if hasattr(QApplication, "setHighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

    app = QApplication(sys.argv)
    app.setApplicationName("ASSella")
    app.setDesktopFileName("assella")

    # Eagerly initialize the main-thread dispatch bridge so background tasks
    # can safely post to the GUI event loop.
    import utils.main_thread  # noqa: F401

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
    if cli_mode:
        from managers.cli_manager import run_cli_dispatcher
        code = run_cli_dispatcher(
            app=app,
            raw_args=args,
            logger=logger,
            command_line_appid=command_line_appid,
            command_line_zips=command_line_zips,
        )
        sys.exit(code if isinstance(code, int) else 0)

    # -------------------------------------------------------------------------
    # GUI Mode Initialization
    # -------------------------------------------------------------------------

    if not headless_mode:
        from utils.single_instance import SingleInstanceIPC

        launch_payload = {
            "appid": command_line_appid,
            "zips": command_line_zips,
        }
        if SingleInstanceIPC.send_and_exit_if_running(launch_payload):
            logger.info("Forwarded request to running ASSella instance. Exiting.")
            sys.exit(0)

    config_path = get_user_config_path()
    try:
        backup_created = backup_config_on_startup(config_path)
        if backup_created:
            logger.info("SLSsteam config backup created at startup")

        if config_path.exists():
            ensure_slssteam_prerequisites(config_path)

            # config.yaml holds every depot decryption key the user owns.
            # Older versions left it world-readable; tighten existing installs.
            try:
                from utils.yaml_config_manager import harden_existing_config_permissions
                harden_existing_config_permissions(config_path)
            except Exception as ex:
                logger.debug(f"Could not harden config permissions: {ex}")
            
            try:
                from utils.yaml_config_manager import check_and_merge_fakeappid_db
                check_and_merge_fakeappid_db(config_path)
            except Exception as ex:
                logger.error(f"Failed to check and merge Fake AppID database: {ex}")

            try:
                from utils.yaml_config_manager import get_fake_appid, add_fake_app_id, remove_fake_app_id
                all_on = settings.value("all_games_online", None)
                if all_on is None:
                    all_on = settings.value("all_games_online_beta", None)

                # Normalize boolean
                if isinstance(all_on, str):
                    all_on_bool = all_on.strip().lower() in ("true", "1", "yes")
                elif all_on is not None:
                    all_on_bool = bool(all_on)
                else:
                    all_on_bool = True  # Default on if never set

                if all_on_bool:
                    if get_fake_appid(config_path, "0") is None:
                        add_fake_app_id(config_path, "0", game_name="All Unowned Apps", fake_appid="480")
                else:
                    if get_fake_appid(config_path, "0") is not None:
                        remove_fake_app_id(config_path, "0")
            except Exception as ex:
                logger.debug(f"Failed to sync all_games_online FakeAppId: {ex}")

            # A Steam update would replace the files SLSsteam patches, so the
            # config keeps DisableUpdates set. The writer memoises "already
            # correct", so this costs nothing once the file agrees.
            try:
                from utils.yaml_config_manager import update_yaml_boolean_value
                update_yaml_boolean_value(config_path, "DisableUpdates", True)
            except Exception as ex:
                logger.debug(f"Failed to ensure DisableUpdates in the SLSsteam config: {ex}")
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
            logger.info(f"ASSella running headless on http://127.0.0.1:{server_port} (token required for API access)")
            logger.info("========================================")
        else:
            main_win.show()
            logger.info("Main window displayed successfully.")

        # ---------------------------------------------------------------------
        # Post-Launch Processing & Single-Instance IPC Bridge
        # ---------------------------------------------------------------------
        def add_manifest_to_queue(path):
            """Run on the Qt main thread; queues a downloaded manifest ZIP."""
            logger.info(f"Adding to queue: {os.path.basename(path)}")
            main_win.job_queue.add_job(path)

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

                # Hand the ZIP to the Qt main thread through the app's bridge.
                post_to_main_thread(add_manifest_to_queue, zip_file)
            except Exception as ex:
                logger.error(f"Threaded download failed: {ex}")

        if not headless_mode:
            from utils.single_instance import SingleInstanceIPC

            def _handle_ipc_command(payload: dict):
                p_appid = payload.get("appid")
                p_zips = payload.get("zips", [])
                if p_appid:
                    logger.info(f"[IPC] Received AppID {p_appid} from secondary launch; queueing download...")
                    t = threading.Thread(
                        target=threaded_manifest_download,
                        args=(p_appid,),
                        daemon=True,
                    )
                    t.start()
                if p_zips:
                    logger.info(f"[IPC] Received {len(p_zips)} ZIP file(s) from secondary launch; adding to queue...")
                    for zf in p_zips:
                        post_to_main_thread(add_manifest_to_queue, zf)

                # Bring window to front
                main_win.setWindowState(
                    main_win.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive
                )
                main_win.show()
                main_win.raise_()
                main_win.activateWindow()

            ipc_server = SingleInstanceIPC(_handle_ipc_command, parent=app)
            ipc_server.start_server()
            app.aboutToQuit.connect(ipc_server.close)

        if command_line_zips or command_line_appid:
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
