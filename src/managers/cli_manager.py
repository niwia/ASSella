"""
CLI Manager for ACCELA - Handles command-line mode for ZIP file processing.

When ACCELA is invoked with ZIP file arguments, this module takes over
to provide a simplified CLI experience without the main window.
"""

import logging
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, Optional

from PyQt6.QtCore import QEventLoop
from PyQt6.QtWidgets import QApplication

from core.steam_helpers import (
    fix_greenluma_offline_mode,
    get_steam_libraries,
    find_next_applist_number,
    app_id_exists_in_applist,
)
from core.tasks.process_zip_task import ProcessZipTask
from core.tasks.download_depots_task import DownloadDepotsTask
from core.morrenus_api import download_manifest as download_morrenus_manifest

from utils.settings import get_settings
from utils.task_runner import TaskRunner
from utils.paths import Paths
from utils.steam_manifest import get_game_directory, write_acf_file
from utils.helpers import create_font_from_settings
from utils.wrapper_metadata import persist_selected_dlcs
from utils.yaml_config_manager import (
    is_greenluma_wrapper_mode_enabled,
    is_slssteam_mode_enabled,
)

# Import text menus for CLI mode (Rich-based, cross-platform)
from ui.text_menus import (
    select_depots,
    select_dlcs,
    select_steam_library,
    select_destination_path,
)
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
)

LINUX_TERMINALS = [
    ["wezterm", "start", "--always-new-process", "--"],
    ["konsole", "-e"],
    ["gnome-terminal", "--"],
    ["ptyxis", "--"],
    ["alacritty", "-e"],
    ["tilix", "-e"],
    ["xfce4-terminal", "-e"],
    ["terminator", "-x"],
    ["mate-terminal", "-e"],
    ["lxterminal", "-e"],
    ["xterm", "-e"],
    ["kitty", "-e"],
]


def _get_terminal_command(
    appid: Optional[int] = None, zip_path: Optional[str] = None
) -> Optional[list[str]]:
    """Get the terminal command to run CLI mode.

    Args:
        appid: AppID to download from Morrenus API
        zip_path: Path to a ZIP file to process

    Returns:
        List of command arguments, or None if no terminal available
    """
    if sys.platform != "linux":
        return None

    # Get the directory where accela is installed
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run_script = str(os.path.join(script_dir, "run.sh"))

    if not os.path.exists(run_script):
        return None

    # Build the base command
    if appid:
        base_cmd = [run_script, "-cli", "--appid", str(appid)]
    elif zip_path:
        base_cmd = [run_script, "-cli", str(zip_path)]
    else:
        return None

    # Find first available terminal
    for terminal in LINUX_TERMINALS:
        term_path = shutil.which(terminal[0])
        if term_path:
            return [str(term_path)] + terminal[1:] + base_cmd

    return None


def open_cli_terminal(
    appid: Optional[int] = None, zip_path: Optional[str] = None
) -> bool:
    """Open a new terminal running ACCELA CLI mode.

    If already attached to an interactive terminal, returns False to stay in current session.
    """
    if sys.stdin.isatty():
        return False

    cmd = _get_terminal_command(appid, zip_path)
    if not cmd:
        return False

    try:
        cmd_str = [str(part) for part in cmd]
        subprocess.Popen(cmd_str, start_new_session=True)
        return True
    except (OSError, ValueError):
        return False


def _clean_shutdown(app: QApplication):
    """Ensure clean shutdown of Qt threads and logging handlers."""
    TaskRunner.stop_all_active()
    app.processEvents()

    from utils.logger import qt_log_handler

    root_logger = logging.getLogger()
    try:
        root_logger.removeHandler(qt_log_handler)
    except (RuntimeError, TypeError):
        pass

    logging.shutdown()


def _print_cli_help(console: Console):
    """Print formatted CLI command reference."""
    panel = Panel(
        "[bold white]ASSella CLI — Command Reference[/bold white]\n\n"
        "[bold cyan]Usage:[/bold cyan] assella -cli <command> [arguments] [options]\n\n"
        "[bold yellow]Commands:[/bold yellow]\n"
        "  [bold green]update-all[/bold green]                Check for updates across all installed ASSella games\n"
        "  [bold green]library search[/bold green] <query>    Search installed games in local Steam libraries\n"
        "  [bold green]list[/bold green] [query]              List / search local installed games\n"
        "  [bold green]search[/bold green] <query>            Search online Hubcap manifest repository\n"
        "  [bold green]download[/bold green] <title|appid>  Download a game using DepotDownloader\n"
        "  [bold green]<file.zip>[/bold green]                Process and install a local manifest ZIP bundle\n\n"
        "[bold yellow]Download Options:[/bold yellow]\n"
        "  [cyan]--auto, --smart[/cyan]           Auto-select recommended depots for current OS\n"
        "  [cyan]--all[/cyan]                     Select all available depots\n"
        "  [cyan]--branch <name>[/cyan]           Specify target Steam branch (e.g. public, beta)\n"
        "  [cyan]--dest <path>[/cyan]             Custom destination folder\n",
        title="[bold magenta]ASSella Command Line Interface[/bold magenta]",
        border_style="magenta",
    )
    console.print()
    console.print(panel)


def run_download_with_rich_progress(
    download_task: DownloadDepotsTask,
    game_data: Dict[str, Any],
    selected_depots: list[str],
    dest_path: str,
    console: Console,
    logger: logging.Logger,
) -> bool:
    """Run DownloadDepotsTask while driving a live Rich progress bar."""
    game_name = game_data.get("game_name", "Game")
    total_depots = len(selected_depots)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=35),
        TaskProgressColumn(),
        TextColumn("{task.fields[speed]}"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )

    task_id = progress.add_task(
        f"Downloading {game_name} ({total_depots} depot{'s' if total_depots != 1 else ''})",
        total=100,
        speed="",
    )

    def on_pct(pct: int):
        progress.update(task_id, completed=min(100, max(0, pct)))

    def on_speed(spd: str):
        short_spd = spd.split("|")[0].strip() if "|" in spd else spd
        progress.update(task_id, speed=f"[bold green]{short_spd}[/bold green]")

    def on_task_progress_msg(msg: str):
        if any(w in msg.lower() for w in ("error", "warning", "lancache", "fallback", "skipped")):
            progress.console.print(f"[yellow]{msg}[/yellow]")
        logger.debug(f"[DepotDownloader] {msg}")

    download_task.progress_percentage.connect(on_pct)
    download_task.speed_update.connect(on_speed)
    download_task.progress.connect(on_task_progress_msg)

    download_task_runner = TaskRunner()
    download_worker = download_task_runner.run(
        download_task.run, game_data, selected_depots, dest_path
    )

    loop = QEventLoop()
    download_worker.finished.connect(loop.quit)

    error_holder = [None]

    def on_err(err_tuple):
        error_holder[0] = err_tuple
        loop.quit()

    download_task.error.connect(on_err)

    try:
        with progress:
            loop.exec()
    except (KeyboardInterrupt, SystemExit):
        download_task.stop()
        progress.console.print("\n[yellow]Download cancelled by user. Terminating background downloader...[/yellow]")
        return False

    if error_holder[0]:
        console.print(f"[bold red]Download error: {error_holder[0][1]}[/bold red]")
        return False

    return True


def run_cli_update_check(logger: logging.Logger, console: Optional[Console] = None) -> int:
    """Check all installed ASSella games for updates and display a Rich status table."""
    if console is None:
        console = Console()

    console.print(Panel("[bold magenta]ASSella Library Update Check[/bold magenta]", border_style="magenta"))

    from managers.game_manager import GameManager
    from core.tasks.manifest_check_task import ManifestCheckTask
    from utils.update_status_cache import get_update_cache
    from utils.game_modes import is_accela_managed, is_atom_managed

    class MockMainWindow:
        def __init__(self):
            self.settings = get_settings()

    with console.status("[bold cyan]Scanning local Steam libraries for installed games...[/bold cyan]"):
        game_manager = GameManager(MockMainWindow())
        game_manager._perform_scan()

    accela_games = [
        g for g in game_manager.games
        if (is_accela_managed(g) or is_atom_managed(g))
        and g.get("appid") not in ("0", "N/A", "unknown", None)
    ]

    if not accela_games:
        console.print("[yellow]No ASSella-managed games found in Steam libraries.[/yellow]")
        return 0

    console.print(f"Found [bold cyan]{len(accela_games)}[/bold cyan] managed game(s). Checking for updates...")

    check_results: Dict[str, str] = {}
    task = ManifestCheckTask(accela_games, trigger="USER_MANUAL")

    def on_checked(aid: str, status: str):
        check_results[aid] = status
        game_manager._on_game_update_checked(aid, status)

    task.game_update_checked.connect(on_checked)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]Checking manifests:[/] {task.description}"),
        BarColumn(bar_width=35),
        TaskProgressColumn(),
        console=console,
    )
    task_id = progress.add_task("Checking updates...", total=len(accela_games))

    def on_task_prog(current: int, total: int):
        progress.update(task_id, completed=current, total=total)

    task.progress.connect(on_task_prog)

    with progress:
        try:
            task.run()
        except Exception as e:
            console.print(f"[bold red]Update check error: {e}[/bold red]")
            return 1

    get_update_cache().save()

    # Build results table
    table = Table(
        title="[bold white]ASSella Library Update Status[/bold white]",
        header_style="bold magenta",
        border_style="bright_black",
        show_header=True,
    )
    table.add_column("#", style="bold cyan", justify="right", width=4)
    table.add_column("AppID", style="green", width=12)
    table.add_column("Game Name", style="white", min_width=25)
    table.add_column("Installed Build", style="yellow", justify="center", width=16)
    table.add_column("Status", justify="center", width=22)

    up_to_date_count = 0
    update_avail_count = 0
    unknown_count = 0

    for idx, g in enumerate(accela_games, start=1):
        aid = str(g.get("appid"))
        name = g.get("game_name") or g.get("name") or g.get("install_dir") or "Unknown"
        build = str(g.get("buildid") or "-")
        status_code = check_results.get(aid, "cannot_determine")

        if status_code == "up_to_date":
            status_text = "[green]✓ Up to date[/green]"
            up_to_date_count += 1
        elif status_code == "update_available":
            status_text = "[bold yellow]▲ Update Available[/bold yellow]"
            update_avail_count += 1
        elif status_code == "at0m":
            status_text = "[cyan]AT0-M[/cyan]"
            up_to_date_count += 1
        else:
            status_text = "[dim]? Unknown[/dim]"
            unknown_count += 1

        table.add_row(str(idx), aid, name, build, status_text)

    console.print()
    console.print(table)
    summary_str = f"[bold white]Summary:[/] [green]{up_to_date_count} Up to date[/green] • [bold yellow]{update_avail_count} Update(s) available[/bold yellow]"
    if unknown_count:
        summary_str += f" • [dim]{unknown_count} Unknown[/dim]"
    console.print(summary_str)
    return 0


def run_cli_library_search(query: str, logger: logging.Logger, console: Optional[Console] = None) -> int:
    """Search installed games in local Steam libraries matching query."""
    if console is None:
        console = Console()

    from managers.game_manager import GameManager
    from utils.game_modes import is_accela_managed, is_atom_managed

    class MockMainWindow:
        def __init__(self):
            self.settings = get_settings()

    with console.status(f"[bold cyan]Searching local Steam libraries for '{query}'...[/bold cyan]"):
        game_manager = GameManager(MockMainWindow())
        game_manager._perform_scan()

    q_lower = query.strip().lower()
    matches = []
    for g in game_manager.games:
        aid = str(g.get("appid", ""))
        name = str(g.get("game_name") or g.get("name") or g.get("install_dir") or "")
        if not q_lower or q_lower in aid.lower() or q_lower in name.lower():
            matches.append(g)

    if not matches:
        console.print(f"[yellow]No installed games found matching '{query}'.[/yellow]")
        return 0

    table = Table(
        title=f"[bold white]Local Library Games matching '{query}'[/bold white]" if query else "[bold white]Installed Steam Games[/bold white]",
        header_style="bold magenta",
        border_style="bright_black",
        show_header=True,
    )
    table.add_column("AppID", style="green", width=12)
    table.add_column("Game Name", style="white", min_width=25)
    table.add_column("Build ID", style="yellow", justify="center", width=14)
    table.add_column("Mode", justify="center", width=12)
    table.add_column("Library Path", style="dim", min_width=30)

    for g in matches:
        aid = str(g.get("appid", "-"))
        name = str(g.get("game_name") or g.get("name") or g.get("install_dir") or "Unknown")
        build = str(g.get("buildid") or "-")
        if is_atom_managed(g):
            mode = "[cyan]AT0-M[/cyan]"
        elif g.get("is_dlc_only"):
            mode = "[magenta]DLC-Only[/magenta]"
        elif is_accela_managed(g):
            mode = "[blue]ACCELA[/blue]"
        else:
            mode = "[dim]Steam[/dim]"
        lib_path = str(g.get("library_path") or "-")
        table.add_row(aid, name, build, mode, lib_path)

    console.print()
    console.print(table)
    console.print(f"[dim]Found {len(matches)} matching installed game(s).[/dim]")
    return 0


def run_cli_manifest_search(query: str, logger: logging.Logger, console: Optional[Console] = None) -> int:
    """Search online Hubcap manifest repository for games matching query."""
    if console is None:
        console = Console()

    from core import morrenus_api
    from utils.search_ranking import filter_and_rank_results

    with console.status(f"[bold cyan]Searching Hubcap manifests for '{query}'...[/bold cyan]"):
        res = morrenus_api.search_games(query)

    if not isinstance(res, dict) or ("results" not in res and "games" not in res):
        err = res.get("error") if isinstance(res, dict) else "Unknown API error"
        console.print(f"[bold red]Search failed: {err}[/bold red]")
        return 1

    raw_results = res.get("results") or res.get("games") or []
    ranked_dict = filter_and_rank_results(raw_results, query, filter_blacklist=True)
    results = ranked_dict.get("results", [])

    if not results:
        console.print(f"[yellow]No games found on Hubcap matching '{query}'.[/yellow]")
        return 0

    table = Table(
        title=f"[bold white]Hubcap Manifest Search: '{query}'[/bold white]",
        header_style="bold magenta",
        border_style="bright_black",
        show_header=True,
    )
    table.add_column("#", style="bold cyan", justify="right", width=4)
    table.add_column("AppID", style="green", width=12)
    table.add_column("Game Title", style="white", min_width=30)
    table.add_column("Uploaded Date", style="dim", justify="center", width=15)
    table.add_column("Manifest Status", justify="center", width=18)

    for idx, item in enumerate(results[:25], start=1):
        aid = str(item.get("game_id") or item.get("appid") or "-")
        title = str(item.get("game_name") or item.get("name") or "Unknown")
        uploaded = str(item.get("uploaded_date") or "-")
        manifest_avail = item.get("manifest_available", True)
        status = "[green]Available[/green]" if manifest_avail else "[red]Missing[/red]"
        table.add_row(str(idx), aid, title, uploaded, status)

    console.print()
    console.print(table)
    console.print(
        f"[dim]Showing {min(len(results), 25)} of {len(results)} match(es). "
        f"To download: [bold cyan]assella -cli download <AppID>[/bold cyan][/dim]"
    )
    return 0


def run_cli_download_flow(
    query_or_appid: str,
    branch: Optional[str] = None,
    auto_depots: bool = False,
    all_depots: bool = False,
    dest_path: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
    console: Optional[Console] = None,
) -> int:
    """Full download flow: search -> smart branch -> manifest fetch -> smart depot auto-selection -> DepotDownloader -> post processing."""
    if console is None:
        console = Console()
    if logger is None:
        logger = logging.getLogger(__name__)

    # Fix GreenLuma offline mode
    fix_greenluma_offline_mode()

    # 1. Resolve AppID and Game Name
    appid: Optional[int] = None
    game_title = ""

    if query_or_appid.strip().isdigit():
        appid = int(query_or_appid.strip())
    else:
        from core import morrenus_api
        from utils.search_ranking import filter_and_rank_results

        with console.status(f"[bold cyan]Searching Hubcap for '{query_or_appid}'...[/bold cyan]"):
            res = morrenus_api.search_games(query_or_appid)
        raw = res.get("results") or res.get("games") or [] if isinstance(res, dict) else []
        ranked = filter_and_rank_results(raw, query_or_appid, filter_blacklist=True).get("results", [])

        if not ranked:
            console.print(f"[bold red]No games found on Hubcap matching '{query_or_appid}'.[/bold red]")
            return 1

        if len(ranked) == 1:
            item = ranked[0]
            appid = int(item.get("game_id") or item.get("appid"))
            game_title = str(item.get("game_name") or "")
            console.print(f"[green]Found game:[/] [bold white]{game_title}[/] (AppID: {appid})")
        else:
            table = Table(
                title=f"[bold white]Search Results for '{query_or_appid}'[/bold white]",
                header_style="bold magenta",
                border_style="bright_black",
                show_header=True,
            )
            table.add_column("#", style="bold cyan", justify="right", width=4)
            table.add_column("AppID", style="green", width=12)
            table.add_column("Game Title", style="white", min_width=30)
            for idx, item in enumerate(ranked[:15], start=1):
                aid = str(item.get("game_id") or item.get("appid") or "-")
                title = str(item.get("game_name") or "Unknown")
                table.add_row(str(idx), aid, title)
            console.print(table)

            if not sys.stdin.isatty():
                item = ranked[0]
                appid = int(item.get("game_id") or item.get("appid"))
                game_title = str(item.get("game_name") or "")
            else:
                try:
                    choice = Prompt.ask(
                        f"[bold magenta]Select game to download [1-{min(len(ranked), 15)}][/bold magenta]",
                        default="1",
                        console=console,
                    )
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[yellow]Cancelled.[/yellow]")
                    return 0

                val = choice.strip()
                if val.lower() in ("q", "quit", "cancel"):
                    return 0
                if val.isdigit() and 1 <= int(val) <= min(len(ranked), 15):
                    item = ranked[int(val) - 1]
                    appid = int(item.get("game_id") or item.get("appid"))
                    game_title = str(item.get("game_name") or "")
                else:
                    console.print("[bold red]Invalid selection.[/bold red]")
                    return 1

    if not appid:
        console.print("[bold red]Could not determine valid AppID.[/bold red]")
        return 1

    # 2. Smart Branch Choose
    from core.morrenus_api import get_selected_branch, download_manifest
    from core.steam_api import get_app_branches
    from ui.text_menus import select_branch

    target_branch = branch
    if not target_branch:
        if auto_depots or not sys.stdin.isatty():
            target_branch = get_selected_branch(appid)
        else:
            with console.status(f"[bold cyan]Checking Steam branches for AppID {appid}...[/bold cyan]"):
                branches = get_app_branches(str(appid))
            if branches and len(branches) > 1:
                chosen = select_branch(str(appid), branches, console=console)
                if not chosen:
                    console.print("[yellow]Branch selection cancelled. Aborting.[/yellow]")
                    return 0
                target_branch = chosen
            else:
                target_branch = get_selected_branch(appid)

    console.print(f"[bold cyan]Branch:[/] [white]{target_branch}[/white]")

    # 3. Download Manifest
    with console.status(f"[bold green]Fetching manifest for AppID {appid} (branch: {target_branch})...[/bold green]"):
        zip_path, err = download_manifest(str(appid), branch=target_branch)

    if err or not zip_path:
        console.print(f"[bold red]Failed to download manifest bundle: {err or 'Unknown error'}[/bold red]")
        return 1

    console.print(f"[green]✓ Manifest downloaded:[/] [dim]{os.path.basename(zip_path)}[/dim]")

    # 4. Parse ZIP via ProcessZipTask
    from core.tasks.process_zip_task import ProcessZipTask
    game_data_holder = [None]

    def on_zip_done(res):
        game_data_holder[0] = res
        loop.quit()

    with console.status("[bold green]Parsing manifest package and depot keys...[/bold green]"):
        loop = QEventLoop()
        runner = TaskRunner()
        runner.cleanup_complete.connect(loop.quit)
        zip_task = ProcessZipTask()
        runner.run(zip_task.run, zip_path).finished.connect(on_zip_done)
        loop.exec()

    game_data = game_data_holder[0]
    if not game_data or not game_data.get("depots"):
        console.print("[bold red]Error: No downloadable depots found in manifest bundle.[/bold red]")
        return 1

    game_name = game_data.get("game_name") or game_title or f"AppID {appid}"
    depots = game_data.get("depots", {})

    # 5. Smart Depot Auto-Selection
    from ui.dialogs.depot_selection.selection_policy import recommended_depots

    platform = "linux" if sys.platform == "linux" else "windows"
    rec_depots = recommended_depots(str(appid), depots, platform=platform)

    if all_depots:
        selected_depots = list(depots.keys())
        console.print(f"[yellow]Selected all {len(selected_depots)} depot(s).[/yellow]")
    elif auto_depots:
        selected_depots = rec_depots or list(depots.keys())
        console.print(f"[green]Smart-selected {len(selected_depots)} recommended depot(s) for {platform}.[/green]")
    else:
        # Interactive selection menu via Rich with smart recommendations
        selected_depots = select_depots(str(appid), game_name, depots, console=console, recommended=rec_depots)
        if not selected_depots:
            console.print("[yellow]Depot selection cancelled. Aborting download.[/yellow]")
            return 0

    # 6. Destination Path Selection
    target_dest = dest_path
    if not target_dest:
        libs = get_steam_libraries()
        if libs:
            target_dest = select_steam_library(libs, console=console)
        else:
            target_dest = select_destination_path(console=console)

    if not target_dest:
        console.print("[yellow]Destination folder not selected. Aborting download.[/yellow]")
        return 0

    console.print(f"[bold cyan]Install target:[/] [green]{target_dest}[/green]")

    # 7. Start Download using DepotDownloader with Rich Live Progress
    game_data["selected_depots_list"] = selected_depots
    download_task = DownloadDepotsTask()
    success = run_download_with_rich_progress(
        download_task, game_data, selected_depots, target_dest, console, logger
    )
    if not success:
        return 1

    # 8. Post-Processing
    console.print("[bold cyan]Running post-processing (SLSsteam, manifests, emulator)...[/bold cyan]")
    settings = get_settings()
    cli_task_mgr = CLITaskManager(settings, logger)
    cli_task_mgr.run_post_processing(game_data, download_task, target_dest)

    panel = Panel(
        f"[bold green]Successfully installed {game_name}![/bold green]\n\n"
        f"• [bold white]AppID:[/bold white] {appid}\n"
        f"• [bold white]Path:[/bold white] {target_dest}\n"
        f"• [bold white]Depots:[/bold white] {len(selected_depots)} downloaded\n"
        f"• [bold white]Branch:[/bold white] {target_branch}",
        title="[bold magenta]ASSella Installation Complete[/bold magenta]",
        border_style="green",
    )
    console.print()
    console.print(panel)
    return 0


def run_cli_dispatcher(
    app: QApplication,
    raw_args: list[str],
    logger: logging.Logger,
    command_line_appid: Optional[int] = None,
    command_line_zips: Optional[list[str]] = None,
) -> int:
    """Main CLI dispatcher for ASSella."""
    console = Console()
    cli_args = [a for a in raw_args if a not in ("-cli", "--cli", "-v", "--verbose", "-debug", "--debug")]

    if not cli_args and not command_line_appid and not command_line_zips:
        _print_cli_help(console)
        _clean_shutdown(app)
        return 0

    first = cli_args[0].lower() if cli_args else ""

    if first in ("-h", "--help", "help"):
        _print_cli_help(console)
        _clean_shutdown(app)
        return 0

    if first in ("update-all", "check-updates", "update_all", "check_updates"):
        code = run_cli_update_check(logger, console)
        _clean_shutdown(app)
        return code

    if first in ("library", "local"):
        sub_args = cli_args[1:]
        if sub_args and sub_args[0].lower() in ("search", "find"):
            query = " ".join(sub_args[1:])
        else:
            query = " ".join(sub_args)
        code = run_cli_library_search(query, logger, console)
        _clean_shutdown(app)
        return code

    if first in ("list", "ls"):
        query = " ".join(cli_args[1:])
        code = run_cli_library_search(query, logger, console)
        _clean_shutdown(app)
        return code

    if first in ("search", "find"):
        if len(cli_args) < 2:
            console.print("[bold red]Error: 'search' requires a search term (e.g. assella -cli search Elden)[/bold red]")
            _clean_shutdown(app)
            return 1
        query = " ".join(cli_args[1:])
        code = run_cli_manifest_search(query, logger, console)
        _clean_shutdown(app)
        return code

    if first in ("download", "get", "install"):
        sub_args = cli_args[1:]
        if not sub_args or any(a in ("-h", "--help", "help") for a in sub_args):
            console.print(
                Panel(
                    "[bold white]ASSella CLI — Download Command[/bold white]\n\n"
                    "[bold cyan]Usage:[/bold cyan] assella -cli download <title|appid> [options]\n\n"
                    "[bold yellow]Options:[/bold yellow]\n"
                    "  [green]--auto, --smart[/green]    Auto-select recommended depots for current OS\n"
                    "  [green]--all[/green]              Select all available depots\n"
                    "  [green]--branch <name>[/green]    Specify target Steam branch (e.g. public, beta)\n"
                    "  [green]--dest <path>[/green]      Custom destination folder\n\n"
                    "[bold yellow]Examples:[/bold yellow]\n"
                    "  assella -cli download 1091500 --smart\n"
                    "  assella -cli download \"Hollow Knight\" --branch beta\n"
                    "  assella -cli download 2868840 --dest /mnt/games",
                    title="[bold magenta]Download Options[/bold magenta]",
                    border_style="magenta",
                )
            )
            _clean_shutdown(app)
            return 0

        branch: Optional[str] = None
        auto_depots = False
        all_depots = False
        dest_path: Optional[str] = None
        target_tokens: list[str] = []

        idx = 0
        while idx < len(sub_args):
            arg = sub_args[idx]
            if arg in ("--branch", "-b") and idx + 1 < len(sub_args):
                branch = sub_args[idx + 1]
                idx += 1
            elif arg in ("--auto", "-auto", "--smart", "-smart"):
                auto_depots = True
            elif arg in ("--all", "-all"):
                all_depots = True
            elif arg in ("--dest", "--path", "-d") and idx + 1 < len(sub_args):
                dest_path = sub_args[idx + 1]
                idx += 1
            else:
                target_tokens.append(arg)
            idx += 1

        target_query = " ".join(target_tokens).strip()
        if not target_query:
            console.print("[bold red]Error: No game name or AppID provided to download.[/bold red]")
            _clean_shutdown(app)
            return 1

        code = run_cli_download_flow(
            target_query,
            branch=branch,
            auto_depots=auto_depots,
            all_depots=all_depots,
            dest_path=dest_path,
            logger=logger,
            console=console,
        )
        _clean_shutdown(app)
        return code

    # Direct AppID flag: --appid <id>
    if command_line_appid:
        code = run_cli_download_flow(str(command_line_appid), logger=logger, console=console)
        _clean_shutdown(app)
        return code

    # Legacy fallback: ZIP file(s)
    zips_to_process = command_line_zips or [a for a in cli_args if a.lower().endswith(".zip")]
    if zips_to_process:
        return run_cli_mode(app, zips_to_process, logger)

    # If first argument looks like an AppID directly (e.g. assella -cli 1245620)
    if first.isdigit():
        code = run_cli_download_flow(first, logger=logger, console=console)
        _clean_shutdown(app)
        return code

    # If first argument is unquoted title, default to search & download
    console.print(f"[dim]Interpreting command as: download '{' '.join(cli_args)}'[/dim]")
    code = run_cli_download_flow(" ".join(cli_args), logger=logger, console=console)
    _clean_shutdown(app)
    return code


def run_cli_mode(
    app: QApplication,
    command_line_zips: list[str],
    logger: logging.Logger,
    appid: Optional[int] = None,
):
    """Run ACCELA in CLI mode for ZIP files or AppID."""
    settings = get_settings()
    console = Console()

    logger.info("=" * 50)
    logger.info("ACCELA CLI Mode")
    logger.info("=" * 50)

    fix_greenluma_offline_mode()

    if appid:
        code = run_cli_download_flow(str(appid), logger=logger, console=console)
        _clean_shutdown(app)
        sys.exit(code)

    def get_destination_path_cli() -> Optional[str]:
        slssteam_mode_local = is_slssteam_mode_enabled()
        library_mode = settings.value("library_mode", False, type=bool)

        if slssteam_mode_local or library_mode:
            libraries = get_steam_libraries()
            if libraries:
                path = select_steam_library(libraries, console=console)
                if path:
                    return path
                return None

        default_path = os.path.expanduser("~")
        path = select_destination_path(default_path, console=console)
        return path

    total_zips = len(command_line_zips)

    for index, zip_path in enumerate(command_line_zips):
        current_job = index + 1
        logger.info(
            f"\n[{current_job}/{total_zips}] Processing: {os.path.basename(zip_path) if zip_path else 'Unknown'}"
        )

        zip_task = ProcessZipTask()
        game_data_holder: list[Optional[Dict[str, Any]]] = [None]

        def on_zip_processed(result: Optional[Dict[str, Any]]):
            game_data_holder[0] = result
            loop.quit()

        loop = QEventLoop()
        zip_task_runner = TaskRunner()
        zip_task_runner.cleanup_complete.connect(loop.quit)
        zip_task_runner.run(zip_task.run, zip_path).finished.connect(on_zip_processed)
        loop.exec()

        game_data = game_data_holder[0]
        if not game_data or not game_data.get("depots"):
            logger.warning(
                f"No depots found in {os.path.basename(zip_path) if zip_path else 'Unknown'}"
            )
            continue

        selected_depots = select_depots(
            game_data["appid"],
            game_data["game_name"],
            game_data["depots"],
            game_data.get("header_url"),
            console=console,
        )

        if not selected_depots:
            logger.info("Depot selection cancelled or no depots selected, skipping this ZIP")
            continue

        if (
            sys.platform == "win32"
            and is_greenluma_wrapper_mode_enabled()
            and game_data.get("dlcs")
        ):
            selected_dlcs = select_dlcs(game_data["dlcs"], console=console)
            if selected_dlcs:
                game_data["selected_dlcs"] = selected_dlcs

        dest_path = get_destination_path_cli()
        if not dest_path:
            logger.info("Destination folder not selected, skipping this ZIP")
            continue

        game_data["selected_depots_list"] = selected_depots

        download_task = DownloadDepotsTask()
        success = run_download_with_rich_progress(
            download_task, game_data, selected_depots, dest_path, console, logger
        )
        if not success:
            logger.error(f"Download failed for {game_data.get('game_name')}")
            continue

        cli_task_manager = CLITaskManager(settings, logger)
        cli_task_manager.run_post_processing(game_data, download_task, dest_path)

        if game_data:
            logger.info(f"Download complete: {game_data.get('game_name', 'Unknown')}")

    logger.info(f"\n{'=' * 50}")
    logger.info(f"All {total_zips} ZIP(s) processed")
    logger.info(f"{'=' * 50}")

    _clean_shutdown(app)
    sys.exit(0)



class CLITaskManager:
    """CLI version of TaskManager that handles all post-processing steps without UI."""

    def __init__(self, settings, logger):
        self.settings = settings
        self.logger = logger
        self.game_data: Optional[Dict[str, Any]] = None
        self.download_task: Optional[DownloadDepotsTask] = None
        self.current_dest_path: Optional[str] = None
        self.slssteam_mode_was_active = False

    def run_post_processing(
        self,
        game_data: Optional[Dict[str, Any]],
        download_task: DownloadDepotsTask,
        dest_path: str,
    ):
        """Run all post-processing steps after download completion."""
        if not game_data:
            return

        self.game_data = game_data
        self.download_task = download_task
        self.current_dest_path = dest_path
        self.slssteam_mode_was_active = is_slssteam_mode_enabled()

        # Get size on disk
        size_on_disk = 0
        if self.download_task:
            size_on_disk = self.download_task.total_download_size_for_this_job
            self.logger.info(f"Retrieved SizeOnDisk from download task: {size_on_disk}")

        # Move manifests to depotcache FIRST — depotcache must be populated before
        # the SLS install|appid|index command fires so Steam can verify local files.
        self._move_manifests_to_depotcache()

        # Write app token to file for non-integrated installs
        self._write_app_token(dest_path)

        # Create ACF / trigger SLS install (after depotcache is ready)
        self._create_acf_file(size_on_disk)

        # Save main depot info
        self._save_main_depot_info()

        # Persist selected DLC metadata for future uninstall cleanup
        self._persist_wrapper_metadata()

        # Set Linux binary permissions
        if sys.platform == "linux":
            self._set_linux_binary_permissions()


        # Auto-apply Goldberg after download completion
        auto_apply_goldberg = self.settings.value(
            "auto_apply_goldberg", False, type=bool
        )
        if auto_apply_goldberg and self.game_data and self.current_dest_path:
            # Goldberg is an optional component, so it may not be installed.
            # This is the CLI path: log it, there is no settings dialog to open.
            from utils.component_manager import is_component_available

            if not is_component_available("goldberg"):
                self.logger.warning(
                    "Goldberg is not downloaded - skipping auto-apply. "
                    "Enable it from Settings -> Tools -> Optional Components."
                )
            else:
                game_directory = get_game_directory(self.current_dest_path, self.game_data)
                self.logger.info("Auto-applying Goldberg after download completion")
                self._apply_goldberg(
                    game_directory,
                    str(self.game_data.get("appid", "")),
                    self.game_data.get("game_name", ""),
                )
                if sys.platform == "linux":
                    self._set_linux_permissions(game_directory)

        # Steamless processing
        steamless_enabled = self.settings.value(
            "use_steamless", False, type=bool
        ) or self.settings.value("use_steamless_aio", False, type=bool)
        appid = self.game_data.get("appid") if self.game_data else None
        is_dlc_only = False
        if appid:
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc_only = is_dlc_only_mode(str(appid))

        if steamless_enabled and not is_dlc_only and not self._is_current_job_linux():
            self.logger.info("Steamless is enabled, starting DRM removal...")
            self._run_steamless()

        # Achievement generation
        achievements_enabled = self.settings.value(
            "generate_achievements", False, type=bool
        )
        if achievements_enabled:
            self.logger.info("Achievement generation is enabled...")
            self._run_achievement_generation()

        # EOSProxy auto-apply
        if self.settings.value("enable_eosproxy_default", False, type=bool) and self.current_dest_path:
            try:
                game_directory = get_game_directory(self.current_dest_path, self.game_data)
                if game_directory and os.path.isdir(game_directory):
                    from utils.eos_detector import EOSDetector
                    status = EOSDetector.get_proxy_status(game_directory)
                    if status in ("inactive", "stale"):
                        if EOSDetector.apply_proxy(game_directory):
                            self.logger.info(f"Automatically applied EOSProxy to {game_directory}")
            except Exception as e:
                self.logger.debug(f"Error applying EOSProxy in CLI: {e}")

        # GreenLuma AppList files (Windows + GreenLuma wrapper)
        if self.slssteam_mode_was_active:
            if sys.platform == "win32":
                self.logger.info(
                    "Windows GreenLuma wrapper active. Creating AppList files..."
                )
                self._create_greenluma_files()
            elif sys.platform == "linux":
                self.logger.info(
                    "Linux Steam integration active, GreenLuma files skipped"
                )
                from utils.yaml_config_manager import is_slssteam_config_management_enabled
                if is_slssteam_config_management_enabled():
                    self._add_appids_to_slssteam_config()

        self.logger.info("All post-processing steps completed")

    def _create_acf_file(self, size_on_disk: int):
        """Create Steam ACF manifest file"""
        if not self.game_data or not self.current_dest_path:
            return

        appid = self.game_data.get("appid")

        # 1. Always write/update our local metadata.json fallback
        try:
            from utils.assella_metadata import write_accela_metadata
            write_accela_metadata(self.current_dest_path, self.game_data, size_on_disk)
        except Exception as e:
            self.logger.error(f"Failed to write metadata JSON file: {e}")

        # 2. If ACF-Independent mode is active, delegate manifest creation entirely to Steam natively
        try:
            from utils.slssteam_integration import install_via_sls, _experimental_mode_enabled
            if _experimental_mode_enabled():
                self.logger.info("ACF-Independent Mode is active. Delegating manifest creation to Steam natively.")
                if appid and appid not in ("0", "N/A", "unknown"):
                    install_via_sls(
                        appid=str(appid),
                        game_name=self.game_data.get("game_name", ""),
                        library_path=self.current_dest_path or "",
                        game_data=self.game_data,
                        size_on_disk=size_on_disk,
                    )
                return
        except Exception as e:
            self.logger.error(f"Error in SLSsteam install flow for {appid}: {e}")

        # 3. Fallback: Write standard Steam .acf manifest file when experimental mode is disabled
        if appid:
            from utils.dlc_helpers import is_dlc_only_mode
            is_dlc_only = is_dlc_only_mode(str(appid))
            if is_dlc_only:
                self.logger.info("DLC Only mode active. Skipping base game .acf manifest generation.")
                return

        self.logger.info("Generating Steam .acf manifest file...")

        try:
            acf_path = write_acf_file(
                self.current_dest_path,
                self.game_data,
                size_on_disk,
                include_depots=True,
                log_proton=True,
            )
            if acf_path:
                self.logger.info(f"Created .acf file at {acf_path}")
        except OSError as e:
            self.logger.error(f"Error creating .acf file: {e}")

    def _write_app_token(self, dest_path: str):
        """Write app token to file for non-integrated installs."""
        if self.slssteam_mode_was_active:
            return

        if not self.game_data:
            return

        app_token = self.game_data.get("app_token")
        if not app_token:
            return

        game_dir = get_game_directory(dest_path, self.game_data)
        token_file = os.path.join(game_dir, "apptoken.txt")

        try:
            os.makedirs(game_dir, exist_ok=True)
            with open(token_file, "w") as f:
                f.write(app_token)
            self.logger.info(f"Wrote app token to {token_file}")
        except OSError as e:
            self.logger.error(f"Failed to write app token to file: {e}")

    def _move_manifests_to_depotcache(self):
        """Move manifests from temp to depotcache"""
        if not self.game_data or not self.current_dest_path:
            return

        import tempfile

        temp_manifest_dir = os.path.join(tempfile.gettempdir(), "mistwalker_manifests")
        if not os.path.exists(temp_manifest_dir):
            return

        target_depotcache_dir = os.path.join(self.current_dest_path, "depotcache")

        try:
            os.makedirs(target_depotcache_dir, exist_ok=True)
            manifests_map = self.game_data.get("manifests", {})

            if not manifests_map:
                shutil.rmtree(temp_manifest_dir)
                return

            moved_count = 0
            for depot_id, manifest_gid in manifests_map.items():
                manifest_filename = f"{depot_id}_{manifest_gid}.manifest"
                source_path = os.path.join(temp_manifest_dir, manifest_filename)
                dest = os.path.join(target_depotcache_dir, manifest_filename)
                if os.path.exists(source_path):
                    shutil.move(source_path, dest)
                    moved_count += 1

            self.logger.info(f"Moved {moved_count} manifest files to depotcache.")
            shutil.rmtree(temp_manifest_dir)
        except (OSError, shutil.Error) as e:
            self.logger.error(f"Failed to move manifests to depotcache: {e}")

    def _save_main_depot_info(self):
        """Save main depot ID and manifest to persistent file"""
        from pathlib import Path
        from utils.helpers import get_base_path

        if not self.game_data:
            return

        appid = self.game_data.get("appid")
        if not appid:
            return

        selected_depots = self.game_data.get("selected_depots_list", [])
        all_manifests = self.game_data.get("manifests", {})

        if not selected_depots or not all_manifests:
            return

        try:
            depots_dir = Path(get_base_path()) / "depots"
            depots_dir.mkdir(parents=True, exist_ok=True)
            depot_file = depots_dir / f"{appid}.depot"

            existing_entries = {}
            if depot_file.exists():
                try:
                    for line in depot_file.read_text().splitlines():
                        parts = [p.strip() for p in line.split(":")]
                        if len(parts) >= 2:
                            existing_entries[parts[0]] = line
                except Exception:
                    pass

            for depot_id_raw in selected_depots:
                depot_id = str(depot_id_raw)
                manifest_id = all_manifests.get(depot_id)
                if manifest_id:
                    existing_entries[depot_id] = f"{depot_id}: {manifest_id}"

            with open(depot_file, "w") as f:
                for entry_line in existing_entries.values():
                    f.write(entry_line + "\n")

            self.logger.info(f"Saved depot info for app {appid}: {len(existing_entries)} depot(s)")
        except OSError as e:
            self.logger.error(f"Failed to save depot info: {e}")

    def _persist_wrapper_metadata(self):
        """Persist selected DLC IDs into game metadata for uninstall cleanup."""
        if sys.platform != "win32":
            return

        if not self.game_data or not self.current_dest_path:
            return

        game_directory = get_game_directory(self.current_dest_path, self.game_data)
        selected_dlcs = self.game_data.get("selected_dlcs", [])

        if persist_selected_dlcs(game_directory, selected_dlcs):
            appid = self.game_data.get("appid", "unknown")
            self.logger.debug(
                f"Persisted wrapper metadata for AppID {appid} with {len(selected_dlcs)} DLC ID(s)"
            )

    def _set_linux_binary_permissions(self):
        """Set executable permissions for Linux binaries"""
        if not self.game_data or not self.current_dest_path:
            return

        game_directory = get_game_directory(self.current_dest_path, self.game_data)

        if not os.path.exists(game_directory):
            return

        self._set_linux_permissions(game_directory)

    def _apply_goldberg(self, game_directory: str, appid: str, game_name: str) -> bool:
        """Apply Goldberg files to a game directory (CLI mode)."""
        self.logger.info(
            f"Applying Goldberg for game: {game_name} (AppID: {appid}) in {game_directory}"
        )

        if not game_directory or not os.path.exists(game_directory):
            self.logger.warning(f"Game directory not found: {game_directory}")
            return False

        is_windows = sys.platform == "win32"
        is_linux = sys.platform == "linux"

        found_dirs = set()

        if is_windows:
            for root, _, files in os.walk(game_directory):
                for fname in files:
                    if fname.lower() in ("steam_api.dll", "steam_api64.dll"):
                        found_dirs.add(root)
        elif is_linux:
            for root, _, files in os.walk(game_directory):
                for fname in files:
                    fname_lower = fname.lower()
                    if fname_lower in ("libsteam_api.so", "steam_api.dll", "steam_api64.dll"):
                        found_dirs.add(root)

        if not found_dirs:
            if is_windows:
                self.logger.info("No steam_api DLLs found in game directory tree")
            else:
                self.logger.info("No libsteam_api.so or steam_api DLL found in game directory tree")
            return False

        # Source Goldberg directory (optional component, downloaded on demand)
        goldberg_src = Paths.deps("Goldberg")
        if not goldberg_src.exists():
            self.logger.error(
                f"Goldberg is not installed: {goldberg_src}. "
                "Download it from Settings -> Tools -> Optional Components."
            )
            return False

        def detect_elf_architecture(file_path: str):
            try:
                with open(file_path, "rb") as f:
                    magic = f.read(4)
                    if magic != b"\x7fELF":
                        return None
                    elf_class = f.read(1)
                    if elf_class == b"\x01":
                        return "32"
                    elif elf_class == b"\x02":
                        return "64"
            except (IOError, OSError):
                pass
            return None

        processed = 0
        try:
            for dest_dir in found_dirs:
                if is_windows:
                    original_dlls_in_dir = set()
                    for base in ("steam_api.dll", "steam_api64.dll"):
                        if os.path.exists(os.path.join(dest_dir, base)):
                            original_dlls_in_dir.add(base)

                    for base in ("steam_api.dll", "steam_api64.dll"):
                        src_path = os.path.join(dest_dir, base)
                        if os.path.exists(src_path):
                            try:
                                target_path = src_path + ".valve"
                                if not os.path.exists(target_path):
                                    os.replace(src_path, target_path)
                                    self.logger.info(f"Renamed {src_path} -> {target_path}")
                            except OSError as e:
                                self.logger.warning(f"Failed to rename {src_path}: {e}")

                    for base in original_dlls_in_dir:
                        src_dll = goldberg_src / "windows" / base
                        dest_dll = os.path.join(dest_dir, base)
                        try:
                            if src_dll.exists():
                                shutil.copy2(str(src_dll), dest_dll)
                                self.logger.info(f"Copied Goldberg DLL {src_dll} -> {dest_dll}")
                            else:
                                self.logger.warning(f"Goldberg DLL not found in deps: {src_dll}")
                        except (OSError, shutil.Error) as e:
                            self.logger.warning(f"Failed to copy Goldberg DLL: {e}")
                
                elif is_linux:
                    # Check if this directory has native .so files or DLLs
                    has_native_so = os.path.exists(os.path.join(dest_dir, "libsteam_api.so"))
                    has_dll = any(os.path.exists(os.path.join(dest_dir, base))
                                  for base in ("steam_api.dll", "steam_api64.dll"))

                    if has_native_so:
                        # Native Linux: .so file logic with architecture detection
                        original_so_path = os.path.join(dest_dir, "libsteam_api.so")
                        if os.path.exists(original_so_path):
                            detected_arch = detect_elf_architecture(original_so_path)
                            self.logger.info(f"Detected {detected_arch}-bit ELF in {dest_dir}")

                            try:
                                target_path = original_so_path + ".valve"
                                if not os.path.exists(target_path):
                                    os.replace(original_so_path, target_path)
                                    self.logger.info(f"Renamed {original_so_path} -> {target_path}")
                            except OSError as e:
                                self.logger.warning(f"Failed to rename {original_so_path}: {e}")

                            if detected_arch == "64":
                                src_so = goldberg_src / "linux" / "libsteam_api64.so"
                            else:
                                src_so = goldberg_src / "linux" / "libsteam_api.so"
                            dest_so = os.path.join(dest_dir, "libsteam_api.so")

                            try:
                                if src_so.exists():
                                    shutil.copy2(str(src_so), dest_so)
                                    self.logger.info(f"Copied {src_so.name} to {dest_so}")
                                else:
                                    self.logger.warning(f"Source .so not found: {src_so}")
                            except OSError as e:
                                self.logger.warning(f"Failed to copy Goldberg SO: {e}")

                    if has_dll:
                        # DLL logic (Proton/Wine)
                        original_dlls_in_dir = set()
                        for base in ("steam_api.dll", "steam_api64.dll"):
                            dll_path = os.path.join(dest_dir, base)
                            if os.path.exists(dll_path):
                                original_dlls_in_dir.add(base)

                        for base in ("steam_api.dll", "steam_api64.dll"):
                            src_dll_path = os.path.join(dest_dir, base)
                            if os.path.exists(src_dll_path):
                                try:
                                    target_path = src_dll_path + ".valve"
                                    if not os.path.exists(target_path):
                                        os.replace(src_dll_path, target_path)
                                        self.logger.info(f"Renamed {src_dll_path} -> {target_path}")
                                except OSError as e:
                                    self.logger.warning(f"Failed to rename {src_dll_path}: {e}")

                        for base in original_dlls_in_dir:
                            src_dll = goldberg_src / "windows" / base
                            dest_dll = os.path.join(dest_dir, base)
                            try:
                                if src_dll.exists():
                                    shutil.copy2(str(src_dll), dest_dll)
                                    self.logger.info(f"Copied Goldberg DLL {src_dll} -> {dest_dll}")
                                else:
                                    self.logger.warning(f"Goldberg DLL not found in deps: {src_dll}")
                            except (OSError, shutil.Error) as e:
                                self.logger.warning(f"Failed to copy Goldberg DLL: {e}")

                goldberg_platform_src = goldberg_src / ("windows" if is_windows else "linux")
                
                for item in goldberg_src.iterdir():
                    if item.name.lower() in (
                        "windows", "linux", "genints",
                        "steam_api.dll", "steam_api64.dll",
                        "libsteam_api.so", "libsteam_api64.so",
                        "steam_appid.txt",
                    ):
                        continue
                    dest_path = os.path.join(dest_dir, item.name)
                    try:
                        if item.is_dir():
                            shutil.copytree(str(item), dest_path, dirs_exist_ok=True)
                            self.logger.info(f"Copied dir {item} -> {dest_path}")
                        else:
                            shutil.copy2(str(item), dest_path)
                            self.logger.info(f"Copied file {item} -> {dest_path}")
                    except (OSError, shutil.Error) as e:
                        self.logger.warning(f"Failed to copy {item} to {dest_path}: {e}")

                if goldberg_platform_src.exists():
                    for item in goldberg_platform_src.iterdir():
                        item_name_lower = item.name.lower()
                        if item_name_lower == "steam_appid.txt":
                            continue
                        # On Linux, only copy .so files if the game has native .so files
                        # On Windows, only copy .dll files if the game has .dll files
                        if is_linux and item_name_lower.endswith(".so"):
                            if not has_native_so:
                                continue
                        elif is_windows and item_name_lower.endswith(".dll"):
                            if not has_dll:
                                continue
                        dest_path = os.path.join(dest_dir, item.name)
                        try:
                            if item.is_dir():
                                shutil.copytree(str(item), dest_path, dirs_exist_ok=True)
                            else:
                                shutil.copy2(str(item), dest_path)
                        except (OSError, shutil.Error) as e:
                            self.logger.warning(f"Failed to copy {item}: {e}")

                try:
                    appid_file = os.path.join(dest_dir, "steam_appid.txt")
                    with open(appid_file, "w", encoding="utf-8") as f:
                        f.write(str(appid))
                    self.logger.info(f"Wrote steam_appid.txt to {appid_file}")
                except OSError as e:
                    self.logger.warning(f"Failed to write steam_appid.txt: {e}")

                processed += 1

            self.logger.info(f"Applied Goldberg files to {processed} folder(s).")
            
            self.logger.info("Generating interface headers for Goldberg...")
            try:
                from managers.task_manager import TaskManager
                
                for dest_dir in found_dirs:
                    valve_files = []
                    for fname in os.listdir(dest_dir):
                        fname_lower = fname.lower()
                        if fname_lower.endswith(".valve") and "steam_api" in fname_lower:
                            is_64bit = "64" in fname_lower
                            valve_files.append((os.path.join(dest_dir, fname), is_64bit))
                    
                    if valve_files:
                        steam_settings_dir = os.path.join(dest_dir, "steam_settings")
                        
                        for valve_path, is_64bit in valve_files:
                            if is_windows and not valve_path.lower().endswith(".dll.valve"):
                                continue
                            
                            fname_lower = valve_path.lower()
                            if not (fname_lower.endswith(".dll.valve") or fname_lower.endswith(".so.valve")):
                                continue
                            
                            success = TaskManager._run_generate_interfaces_for_file(
                                dest_dir,
                                valve_path,
                                is_64bit,
                                goldberg_src,
                            )
                            
                            if success:
                                interfaces_file = os.path.join(dest_dir, "steam_interfaces.txt")
                                if os.path.exists(interfaces_file):
                                    try:
                                        os.makedirs(steam_settings_dir, exist_ok=True)
                                        dest_interfaces = os.path.join(steam_settings_dir, "steam_interfaces.txt")
                                        shutil.move(interfaces_file, dest_interfaces)
                                        self.logger.info(f"Moved steam_interfaces.txt to {steam_settings_dir}")
                                    except OSError as e:
                                        self.logger.warning(f"Failed to move steam_interfaces.txt: {e}")
            except Exception as e:
                self.logger.warning(f"Failed to generate interfaces: {e}")
            
            return True

        except (OSError, shutil.Error) as e:
            self.logger.exception(f"Error applying Goldberg: {e}")
            return False

    def _is_current_job_linux(self) -> bool:
        """Check if current job is downloading Linux native depots."""
        if not self.game_data:
            return False
        selected_depots = self.game_data.get("selected_depots_list") or []
        depots = self.game_data.get("depots") or {}
        found_any = False
        for d_id in selected_depots:
            d_data = depots.get(str(d_id)) or {}
            if not d_data:
                continue
            found_any = True
            oslist = (d_data.get("oslist") or "").lower()
            desc = (d_data.get("desc") or "").lower()
            is_linux = (oslist == "linux") or ("[linux]" in desc) or ("linux" in desc)
            if not is_linux:
                return False
        return found_any

    def _set_linux_permissions(self, game_directory: str):
        """Set executable permissions for Linux binaries."""
        self.logger.info(f"Setting executable permissions in: {game_directory}")
        try:
            from managers.emulation.chmod import ChmodMixin
            chmod_count = ChmodMixin._run_chmod_recursive(game_directory)
            if chmod_count > 0:
                self.logger.info(
                    f"Set executable permissions for {chmod_count} Linux binaries"
                )
        except Exception as e:
            self.logger.warning(f"Failed to set Linux binary permissions: {e}")

    def _add_appids_to_slssteam_config(self):
        """Add downloaded AppIDs to SLSsteam config.yaml on Linux"""
        from utils.yaml_config_manager import (
            get_user_config_path,
            add_additional_app,
            add_dlc_data,
        )

        if not self.game_data:
            return

        try:
            config_path = get_user_config_path()
            if not config_path.exists():
                return

            main_appid = self.game_data.get("appid")
            game_name = self.game_data.get("game_name", "")
            if main_appid:
                from utils.dlc_helpers import sync_dlc_only_sls_config
                sync_dlc_only_sls_config(config_path, str(main_appid), game_name, self.game_data)

            self.logger.info("AppIDs added to SLSsteam config")
        except (OSError, ValueError) as e:
            self.logger.warning(f"Failed to add AppIDs to SLSsteam config: {e}")

    def _run_steamless(self):
        """Run Steamless DRM removal"""
        if not self.current_dest_path or not self.game_data:
            return

        game_directory = get_game_directory(self.current_dest_path, self.game_data)

        if not os.path.exists(game_directory):
            return

        from core.tasks.steamless_task import SteamlessTask

        self.logger.info("Starting Steamless DRM Removal...")
        self.logger.info(f"Processing directory: {game_directory}")

        steamless_task = SteamlessTask()
        steamless_task.progress.connect(self.logger.info)

        loop = QEventLoop()
        steamless_task.result.connect(lambda success: loop.quit())
        steamless_task.finished.connect(loop.quit)
        steamless_task.set_game_directory(game_directory)
        steamless_task.start()
        loop.exec()

        self.logger.info("Steamless processing completed")

    def _run_achievement_generation(self):
        """Generate achievements using SLScheevo"""
        if not self.game_data:
            return

        app_id = self.game_data.get("appid")
        if not app_id:
            return

        from core.tasks.generate_achievements_task import GenerateAchievementsTask

        self.logger.info("Generating achievements...")

        achievement_task = GenerateAchievementsTask()
        achievement_task.progress.connect(self.logger.info)

        loop = QEventLoop()

        def on_achievement_complete(result):
            if result and result.get("success"):
                self.logger.info(
                    f"Achievement generation completed: {result.get('message')}"
                )
            else:
                self.logger.info(
                    f"Achievement generation failed: {result.get('message') if result else 'Unknown error'}"
                )

        achievement_runner = TaskRunner()
        achievement_runner.cleanup_complete.connect(loop.quit)
        achievement_runner.run(achievement_task.run, app_id).finished.connect(
            lambda r: on_achievement_complete(r)
        )
        loop.exec()

    def _create_greenluma_files(self):
        """Create GreenLuma AppList files on Windows"""
        if not self.game_data:
            return

        from core import steam_helpers
        from utils.yaml_config_manager import is_slssteam_config_management_enabled

        try:
            # Check if config management is enabled
            if not is_slssteam_config_management_enabled():
                self.logger.debug(
                    "GreenLuma config management is disabled, skipping AppList file creation"
                )
                return

            steam_path = steam_helpers.find_steam_install()
            if not steam_path:
                self.logger.error(
                    "Could not find Steam path. Skipping GreenLuma file creation."
                )
                return

            app_list_dir = os.path.join(steam_path, "AppList")
            if not os.path.exists(app_list_dir):
                os.makedirs(app_list_dir)
                self.logger.info(f"Created AppList directory at: {app_list_dir}")

            game_appid = self.game_data.get("appid")
            if not game_appid:
                return

            # Create main AppList file
            self._create_applist_file(app_list_dir, game_appid)

            # Create DLC AppList files
            selected_dlcs: list = self.game_data.get("selected_dlcs", [])
            for dlc_id in selected_dlcs:
                self._create_applist_file(app_list_dir, dlc_id, is_dlc=True)

            # Copy NoQuestion.bin and StealthMode.bin
            source_dir = Paths.deps()
            for filename in ["NoQuestion.bin", "StealthMode.bin"]:
                source_path = os.path.join(source_dir, filename)
                dest_path = os.path.join(steam_path, filename)
                try:
                    if os.path.exists(source_path):
                        if not os.path.exists(dest_path):
                            shutil.copy2(source_path, dest_path)
                            self.logger.info(f"Copied {filename} to Steam folder")
                        else:
                            self.logger.info(
                                f"{filename} already exists in Steam folder, skipping"
                            )
                    else:
                        self.logger.warning(
                            f"Source {filename} not found in deps folder"
                        )
                except (OSError, shutil.Error) as e:
                    self.logger.error(f"Failed to copy {filename}: {e}")

        except (OSError, shutil.Error, ValueError) as e:
            self.logger.error(f"Failed to create GreenLuma files: {e}", exc_info=True)

    def _create_applist_file(self, app_list_dir: str, appid: str, is_dlc: bool = False):
        """Helper function to create a GreenLuma AppList file."""
        if not app_id_exists_in_applist(app_list_dir, appid):
            next_num = find_next_applist_number(app_list_dir, self.logger)
            filepath = os.path.join(app_list_dir, f"{next_num}.txt")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(str(appid))
            log_msg = f"Created GreenLuma file: {filepath} for "
            log_msg += f"DLC: {appid}" if is_dlc else f"AppID: {appid}"
            self.logger.info(log_msg)
        else:
            log_msg = f"AppID {appid} already exists in AppList folder. Skipping file creation."
            if is_dlc:
                log_msg = f"DLC {log_msg}"
            self.logger.info(log_msg)
