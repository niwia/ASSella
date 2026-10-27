"""
Text-based menu system for CLI mode using Rich.

Provides beautiful, accessible terminal menus for selecting depots,
DLCs, Steam libraries, and destination paths.
"""

import logging
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Sorting and Formatting Helpers
# -----------------------------------------------------------------------------

def _calculate_os_priority(os_val: Optional[str]) -> int:
    """Calculate sort priority based on OS string (Windows first, then Linux)."""
    if os_val is None:
        return 4
    os_str = str(os_val).lower()
    if os_str == "windows":
        return 1
    if os_str == "linux":
        return 2
    if "all" in os_str:
        return 3
    if os_str in ("macosx", "macos"):
        return 5
    return 4


def _calculate_lang_priority(lang_val: Optional[str], desc_str: str) -> Tuple[int, str]:
    """Calculate language priority and sort key."""
    is_no_language = (
        lang_val is None
        and "english" not in desc_str
        and "japanese" not in desc_str
    )

    if "english" in desc_str:
        return 1, lang_val.lower() if lang_val else "english"
    if is_no_language:
        return 1, "english"
    if "japanese" in desc_str:
        return 2, "japanese"

    return 3, lang_val.lower() if lang_val else "zzzz"


def _sort_depots(depots_map: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """Sort depots using OS, language, and ID priorities."""
    def get_sort_key(depot_item: Tuple[str, Dict[str, Any]]) -> Tuple:
        depot_id, depot_data = depot_item
        os_priority = _calculate_os_priority(depot_data.get("oslist"))
        desc_str = str(depot_data.get("desc", "")).lower()
        lang_priority, lang_key = _calculate_lang_priority(
            depot_data.get("language"), desc_str
        )
        return os_priority, lang_priority, lang_key, depot_id

    return sorted(depots_map.items(), key=get_sort_key)


def _format_size(size: Union[str, int, None]) -> str:
    """Format size in bytes to human-readable GB string."""
    if not size:
        return "-"
    try:
        size_bytes = int(size)
        if size_bytes > 0:
            size_gb = size_bytes / (1024**3)
            return f"{size_gb:.2f} GB"
    except (ValueError, TypeError):
        pass
    return "-"


def _format_os(os_val: Optional[str]) -> str:
    """Return styled OS label."""
    if not os_val:
        return "[dim]All[/dim]"
    os_lower = str(os_val).lower()
    if os_lower == "windows":
        return "[cyan]Windows[/cyan]"
    elif os_lower == "linux":
        return "[green]Linux[/green]"
    elif "all" in os_lower:
        return "[yellow]All[/yellow]"
    elif "mac" in os_lower:
        return "[magenta]macOS[/magenta]"
    return f"[dim]{os_val}[/dim]"


def _parse_numeric_selection(
    user_input: str,
    max_count: int,
    default_all: bool = True,
    default_indices: Optional[List[int]] = None,
) -> Optional[List[int]]:
    """Parse comma/space separated numbers and ranges (1-indexed).

    Returns:
        List of 0-based integer indices, or None if cancelled ('q').
    Raises:
        ValueError: If input format or range is invalid.
    """
    text = user_input.strip().lower()
    if text in ("q", "quit", "exit", "cancel"):
        return None
    if not text:
        if default_indices is not None:
            return list(default_indices)
        return list(range(max_count)) if default_all else []
    if text in ("all", "a", "*"):
        return list(range(max_count))
    if text in ("none", "n", "d", "0"):
        return []
    if text in ("smart", "auto", "rec", "recommended") and default_indices is not None:
        return list(default_indices)

    selected: Set[int] = set()
    tokens = [t.strip() for t in text.replace(",", " ").split() if t.strip()]
    for token in tokens:
        if "-" in token:
            parts = token.split("-", 1)
            if parts[0].isdigit() and parts[1].isdigit():
                start, end = int(parts[0]), int(parts[1])
                if start > end:
                    start, end = end, start
                for idx in range(start, end + 1):
                    if 1 <= idx <= max_count:
                        selected.add(idx - 1)
                    else:
                        raise ValueError(f"Number {idx} is out of range (1-{max_count})")
            else:
                raise ValueError(f"Invalid range '{token}'")
        elif token.isdigit():
            idx = int(token)
            if 1 <= idx <= max_count:
                selected.add(idx - 1)
            else:
                raise ValueError(f"Number {idx} is out of range (1-{max_count})")
        else:
            raise ValueError(f"Unrecognized option '{token}'")

    return sorted(list(selected))


# -----------------------------------------------------------------------------
# Core Selection Functions
# -----------------------------------------------------------------------------

def select_depots(
    app_id: str,
    game_name: str,
    depots: Dict[str, Any],
    header_url: Optional[str] = None,
    console: Optional[Console] = None,
    recommended: Optional[Iterable[str]] = None,
) -> List[str]:
    """Show depot selection menu using Rich table and numbered prompt.

    Args:
        app_id: Steam AppID
        game_name: Name of the game
        depots: Dictionary of depot data keyed by depot ID
        header_url: Optional banner URL (unused in terminal)
        console: Optional Rich Console instance
        recommended: Optional list of recommended depot IDs to highlight/preselect

    Returns:
        List of selected depot IDs
    """
    if not depots:
        return []

    if console is None:
        console = Console()

    # Auto-skip when only one depot is available
    if len(depots) == 1:
        depot_id = list(depots.keys())[0]
        depot_desc = depots[depot_id].get("desc", "Unknown")
        logger.info(f"Auto-selected single depot: {depot_id} - {depot_desc}")
        return [str(depot_id)]

    sorted_depots = _sort_depots(depots)

    if recommended is None:
        try:
            from ui.dialogs.depot_selection.selection_policy import recommended_depots
            plat = "linux" if sys.platform == "linux" else "windows"
            recommended = recommended_depots(str(app_id), depots, platform=plat)
        except Exception as e:
            logger.debug(f"Could not compute recommended depots: {e}")
            recommended = None

    rec_set = {str(d) for d in recommended} if recommended else set()
    rec_indices: List[int] = [
        idx - 1 for idx, (did, _) in enumerate(sorted_depots, start=1)
        if str(did) in rec_set
    ]

    default_prompt = "all"
    if rec_indices:
        if len(rec_indices) == len(sorted_depots):
            default_prompt = "all"
        else:
            default_prompt = ",".join(str(i + 1) for i in rec_indices)

    table = Table(
        title=f"[bold white]{game_name}[/bold white] [dim](AppID: {app_id})[/dim] — Select Depots to Download",
        header_style="bold magenta",
        border_style="bright_black",
        show_header=True,
    )
    table.add_column("#", style="bold cyan", justify="right", width=4)
    table.add_column("Depot ID", style="green", width=12)
    table.add_column("Description", style="white", min_width=25)
    table.add_column("System", justify="center", width=10)
    table.add_column("Language", style="dim", justify="center", width=12)
    table.add_column("Size", style="bold yellow", justify="right", width=12)
    table.add_column("Auto", justify="center", width=6)

    for idx, (depot_id, depot_data) in enumerate(sorted_depots, start=1):
        desc = str(depot_data.get("desc") or f"Depot {depot_id}")
        os_str = _format_os(depot_data.get("oslist"))
        lang_str = str(depot_data.get("language") or "-")
        size_str = _format_size(depot_data.get("size"))
        is_rec = str(depot_id) in rec_set
        auto_badge = "[bold green]✓[/bold green]" if is_rec else "[dim]-[/dim]"
        table.add_row(str(idx), str(depot_id), desc, os_str, lang_str, size_str, auto_badge)

    console.print()
    console.print(table)
    if rec_indices and default_prompt != "all":
        console.print(
            f"[dim]Auto-selected recommendation: [bold green]{default_prompt}[/bold green] (Press Enter to accept)[/dim]"
        )
    console.print(
        "[dim]Enter depot numbers (e.g. [bold cyan]1,2,4[/bold cyan] or [bold cyan]1-3[/bold cyan]), "
        "[bold cyan]all[/bold cyan] for all, [bold cyan]none[/bold cyan] for none, or [bold red]q[/bold red] to cancel.[/dim]"
    )

    if not sys.stdin.isatty():
        logger.info("Non-interactive terminal detected: auto-selecting recommended depots")
        if rec_indices:
            return [str(sorted_depots[i][0]) for i in rec_indices]
        return [str(depot_id) for depot_id, _ in sorted_depots]

    while True:
        try:
            user_input = Prompt.ask(
                "[bold magenta]Select depots[/bold magenta]",
                default=default_prompt,
                console=console,
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Depot selection cancelled.[/yellow]")
            return []

        try:
            indices = _parse_numeric_selection(
                user_input, len(sorted_depots), default_all=True, default_indices=rec_indices or None
            )
            if indices is None:
                console.print("[yellow]Depot selection cancelled.[/yellow]")
                return []
            selected = [str(sorted_depots[i][0]) for i in indices]
            if not selected:
                console.print("[yellow]No depots selected.[/yellow]")
            else:
                console.print(f"[green]Selected {len(selected)} depot(s).[/green]")
            return selected
        except ValueError as err:
            console.print(f"[bold red]Invalid input:[/bold red] {err}. Please try again.")


def select_dlcs(
    dlcs: Dict[str, str],
    console: Optional[Console] = None,
) -> List[str]:
    """Show DLC selection menu using Rich table and numbered prompt.

    Args:
        dlcs: Dictionary of DLC AppID -> DLC name
        console: Optional Rich Console instance

    Returns:
        List of selected DLC AppIDs
    """
    if not dlcs:
        return []

    if console is None:
        console = Console()

    title = (
        "Select DLC for GreenLuma Wrapper"
        if sys.platform == "win32"
        else "Select DLC for SLSsteam Wrapper"
    )

    table = Table(
        title=f"[bold white]{title}[/bold white]",
        header_style="bold magenta",
        border_style="bright_black",
        show_header=True,
    )
    table.add_column("#", style="bold cyan", justify="right", width=4)
    table.add_column("AppID", style="green", width=12)
    table.add_column("DLC Name", style="white", min_width=30)

    dlc_list = list(dlcs.items())
    for idx, (appid, name) in enumerate(dlc_list, start=1):
        table.add_row(str(idx), str(appid), str(name))

    console.print()
    console.print(table)
    console.print(
        "[dim]Enter DLC numbers (e.g. [bold cyan]1,2[/bold cyan] or [bold cyan]1-3[/bold cyan]), "
        "[bold cyan]all[/bold cyan] for all, [bold cyan]none[/bold cyan] for none, or [bold red]q[/bold red] to cancel.[/dim]"
    )

    if not sys.stdin.isatty():
        logger.info("Non-interactive terminal detected: auto-selecting all DLCs")
        return [appid for appid, _ in dlc_list]

    while True:
        try:
            user_input = Prompt.ask(
                "[bold magenta]Select DLCs[/bold magenta]",
                default="all",
                console=console,
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]DLC selection cancelled.[/yellow]")
            return []

        try:
            indices = _parse_numeric_selection(
                user_input, len(dlc_list), default_all=True
            )
            if indices is None:
                console.print("[yellow]DLC selection cancelled.[/yellow]")
                return []
            selected = [dlc_list[i][0] for i in indices]
            return selected
        except ValueError as err:
            console.print(f"[bold red]Invalid input:[/bold red] {err}. Please try again.")


def select_branch(
    app_id: str,
    branches: Dict[str, Any],
    console: Optional[Console] = None,
) -> Optional[str]:
    """Show branch selection menu using Rich table and numbered prompt.

    Args:
        app_id: Steam AppID
        branches: Dictionary of branch data keyed by branch name (from Steam PICS)
        console: Optional Rich Console instance

    Returns:
        Selected branch name string, or None if cancelled
    """
    if not branches:
        return "public"

    if len(branches) == 1:
        return list(branches.keys())[0]

    if console is None:
        console = Console()

    # Sort: public first, then alphabetical
    sorted_branches = sorted(
        branches.keys(),
        key=lambda k: (0 if k.lower() == "public" else 1, k.lower()),
    )

    from utils.settings import get_settings
    settings = get_settings()
    saved_branch = (settings.value(f"selected_branch/{app_id}", "public", type=str) or "public").strip()
    if saved_branch not in sorted_branches:
        saved_branch = sorted_branches[0]

    default_idx = sorted_branches.index(saved_branch) + 1

    table = Table(
        title=f"[bold white]Available Branches for AppID {app_id}[/bold white]",
        header_style="bold magenta",
        border_style="bright_black",
        show_header=True,
    )
    table.add_column("#", style="bold cyan", justify="right", width=4)
    table.add_column("Branch Name", style="white", min_width=20)
    table.add_column("Build ID", style="yellow", justify="center", width=16)
    table.add_column("Description", style="dim", min_width=25)
    table.add_column("Default", justify="center", width=12)

    for idx, bname in enumerate(sorted_branches, start=1):
        bdata = branches.get(bname, {})
        bid = str(bdata.get("buildid", "-")) if isinstance(bdata, dict) else "-"
        desc = str(bdata.get("description", "")) if isinstance(bdata, dict) else ""
        is_default = "[bold green]★ Current[/bold green]" if bname == saved_branch else ""
        table.add_row(str(idx), bname, bid, desc, is_default)

    console.print()
    console.print(table)
    console.print(
        f"[dim]Select branch [1-{len(sorted_branches)}], press Enter for current ([bold cyan]{saved_branch}[/bold cyan]), "
        f"or [bold red]q[/bold red] to cancel.[/dim]"
    )

    if not sys.stdin.isatty():
        logger.info(f"Non-interactive terminal: auto-selecting branch '{saved_branch}'")
        return saved_branch

    while True:
        try:
            choice = Prompt.ask(
                "[bold magenta]Select branch[/bold magenta]",
                default=str(default_idx),
                console=console,
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Branch selection cancelled.[/yellow]")
            return None

        val = choice.strip()
        if val.lower() in ("q", "quit", "cancel"):
            return None
        if val.isdigit() and 1 <= int(val) <= len(sorted_branches):
            chosen = sorted_branches[int(val) - 1]
            try:
                settings.setValue(f"selected_branch/{app_id}", chosen)
            except Exception as e:
                logger.debug(f"Could not save selected branch {chosen} for AppID {app_id}: {e}")
            console.print(f"[green]Selected branch:[/] [bold white]{chosen}[/bold white]")
            return chosen

        for b in sorted_branches:
            if b.lower() == val.lower():
                try:
                    settings.setValue(f"selected_branch/{app_id}", b)
                except Exception as e:
                    logger.debug(f"Could not save selected branch {b} for AppID {app_id}: {e}")
                console.print(f"[green]Selected branch:[/] [bold white]{b}[/bold white]")
                return b

        console.print(f"[bold red]Invalid selection '{val}'.[/bold red] Please enter 1-{len(sorted_branches)} or branch name.")


def select_steam_library(
    library_paths: List[str],
    console: Optional[Console] = None,
) -> Optional[str]:
    """Show Steam library selection menu using Rich table and prompt.

    Args:
        library_paths: List of Steam library folder paths
        console: Optional Rich Console instance

    Returns:
        Selected library path, or None if cancelled
    """
    if not library_paths:
        return None

    if len(library_paths) == 1:
        logger.info(f"Auto-selected single Steam library: {library_paths[0]}")
        return library_paths[0]

    if console is None:
        console = Console()

    table = Table(
        title="[bold white]Select Steam Library[/bold white]",
        header_style="bold magenta",
        border_style="bright_black",
        show_header=True,
    )
    table.add_column("#", style="bold cyan", justify="right", width=4)
    table.add_column("Library Path", style="green", min_width=35)

    for idx, path in enumerate(library_paths, start=1):
        table.add_row(str(idx), path)

    console.print()
    console.print(table)
    console.print("[dim]Press Enter to accept default [1], or 'q' to cancel.[/dim]")

    if not sys.stdin.isatty():
        logger.info(f"Non-interactive terminal detected: auto-selecting first library: {library_paths[0]}")
        return library_paths[0]

    while True:
        try:
            choice = Prompt.ask(
                f"[bold magenta]Select library [1-{len(library_paths)}][/bold magenta]",
                default="1",
                console=console,
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Library selection cancelled.[/yellow]")
            return None

        val = choice.strip().lower()
        if val in ("q", "quit", "exit", "cancel"):
            console.print("[yellow]Library selection cancelled.[/yellow]")
            return None
        if val.isdigit():
            idx = int(val)
            if 1 <= idx <= len(library_paths):
                return library_paths[idx - 1]
        console.print(
            f"[bold red]Please enter a valid number between 1 and {len(library_paths)} (or 'q').[/bold red]"
        )


def select_destination_path(
    default_path: Optional[str] = None,
    console: Optional[Console] = None,
) -> Optional[str]:
    """Show destination path selection prompt using Rich.

    Args:
        default_path: Default directory path (defaults to user home)
        console: Optional Rich Console instance

    Returns:
        Selected destination path, or None if cancelled
    """
    resolved_default = default_path or os.path.expanduser("~")

    if not sys.stdin.isatty():
        return resolved_default

    if console is None:
        console = Console()

    panel = Panel(
        f"[bold white]Current / Default Path:[/bold white] [cyan]{resolved_default}[/cyan]\n"
        "[dim]Press Enter to accept default, enter a new path ('~' for home), or 'q' to cancel.[/dim]",
        title="[bold magenta]Select Destination Folder[/bold magenta]",
        border_style="magenta",
    )
    console.print()
    console.print(panel)

    try:
        user_input = Prompt.ask(
            "[bold magenta]Destination path[/bold magenta]",
            default=resolved_default,
            console=console,
        )
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Destination selection cancelled.[/yellow]")
        return None

    val = user_input.strip()
    if val.lower() in ("q", "quit", "exit", "cancel"):
        console.print("[yellow]Destination selection cancelled.[/yellow]")
        return None
    if not val:
        return resolved_default

    return os.path.abspath(os.path.expanduser(os.path.expandvars(val)))


# -----------------------------------------------------------------------------
# Backward Compatibility Menu Classes
# -----------------------------------------------------------------------------

class BaseTextMenu:
    """Base class preserved for compatibility with older callers."""

    def __init__(self, title: str, subtitle: str = ""):
        self.title = title
        self.subtitle = subtitle
        self.result: Any = None

    def run(self) -> Any:
        return self.result


class CheckboxSelectionMenu(BaseTextMenu):
    """Base checkbox menu class preserved for compatibility."""

    def __init__(self, title: str, subtitle: str = ""):
        super().__init__(title, subtitle)
        self.items_map: Dict[str, Any] = {}
        self.selected_items: Set[str] = set()


class DepotSelectionMenu(CheckboxSelectionMenu):
    """Depot selection menu class wrapper."""

    def __init__(
        self,
        app_id: str,
        game_name: str,
        depots: Dict[str, Any],
        header_url: Optional[str] = None,
    ):
        super().__init__(f"Select Depots to Download - {game_name}", f"AppID: {app_id}")
        self.app_id = app_id
        self.game_name = game_name
        self.depots = depots
        self.header_url = header_url

    def run(self) -> List[str]:
        return select_depots(self.app_id, self.game_name, self.depots, self.header_url)


class DlcSelectionMenu(CheckboxSelectionMenu):
    """DLC selection menu class wrapper."""

    def __init__(self, dlcs: Dict[str, str]):
        super().__init__("Select DLC")
        self.dlcs = dlcs

    def run(self) -> List[str]:
        return select_dlcs(self.dlcs)


class BranchSelectionMenu(BaseTextMenu):
    """Branch selection menu class wrapper."""

    def __init__(self, app_id: str, branches: Dict[str, Any]):
        super().__init__(f"Select Branch - AppID {app_id}")
        self.app_id = app_id
        self.branches = branches

    def run(self) -> Optional[str]:
        return select_branch(self.app_id, self.branches)


class SteamLibraryMenu(BaseTextMenu):
    """Steam library selection menu class wrapper."""

    def __init__(self, library_paths: List[str]):
        super().__init__("Select Steam Library")
        self.library_paths = library_paths

    def run(self) -> Optional[str]:
        return select_steam_library(self.library_paths)


class DestinationPathMenu(BaseTextMenu):
    """Destination path selection menu class wrapper."""

    def __init__(self, default_path: Optional[str] = None):
        super().__init__("Select Destination Folder")
        self.default_path = default_path

    def run(self) -> Optional[str]:
        return select_destination_path(self.default_path)


def _create_and_run_menu(menu_class, *args, **kwargs) -> Any:
    """Helper to create and run a menu."""
    menu = menu_class(*args, **kwargs)
    return menu.run()
