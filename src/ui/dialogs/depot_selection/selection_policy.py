"""Which rows a preset should tick, and how those presets read.

Smart Select, the platform presets, the edition menu and DLC-only mode all mean
the same thing: replace the current ticks with one set of depot ids. Deciding
that set is policy, not widgets - the depots Steam would install, the depots a
platform preset wants, the depots the DLC rules allow - and the rules are worth
pinning down: a base-game depot is never DLC, DLC for both Windows and Linux
means no automatic choice at all, and one-KB stubs are skipped unless there are
so many DLCs that they must all be real. The select-all header click asks the
same kind of question - which rows may carry a tick - so its rule lives here too.

The mixin keeps the table walking, the checkboxes, the menu widget and the
warning boxes; everything decided here is Qt-free and tested against plain
depot dictionaries.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# DLC depots at or below this many bytes are stubs that carry no content.
STUB_SIZE_LIMIT = 1024

# Past this many DLC depots the stub filter is dropped: a game with this many
# DLCs packs real content into small depots, so nothing is filtered out.
BYPASS_SIZE_LIMIT_AT = 64

# Row roles that can never carry a tick: the hidden-depot section expander, and
# Hubcap-only depots that are not part of the package at all.
NON_SELECTABLE_ROLES = ("expander", "missing")


def counts_toward_select_all(role, hidden: bool) -> bool:
    """Whether the select-all header click may tick this row.

    The header click asks "is every row already chosen?" and then ticks or
    unticks the rest. Only rows that can actually carry a tick count towards
    that: a Hubcap-only depot is not selectable and a hidden row is not on
    screen. Counting either made "everything is chosen" unreachable, so the
    click re-ticked forever instead of clearing. Both the counting and the
    ticking go through this one rule so they cannot drift apart again.
    """
    return role not in NON_SELECTABLE_ROLES and not hidden


def depot_data(depots: Dict[Any, Any], depot_id) -> dict:
    """The record for a depot id, whichever way the dict is keyed.

    Depots arrive keyed by string here and by int from the database, so both
    are tried before giving up with an empty record.
    """
    depot_id = str(depot_id)
    found = depots.get(depot_id)
    if not found and depot_id.isdigit():
        found = depots.get(int(depot_id))
    return found or {}


@dataclass(frozen=True)
class DepotRowFacts:
    """What the DLC rules need to know about one selectable row."""

    depot_id: str
    desc: str
    config_text: str
    size: int
    data: Dict[str, Any]


@dataclass(frozen=True)
class DlcAutoSelection:
    """The depots DLC-only mode ticks, and why it ticked them."""

    ids: frozenset
    dlc_count: int
    os_conflict: bool
    bypass_size_limit: bool


def is_dlc_row(depot_id, desc, config_text, data, app_id) -> bool:
    """Whether a row is DLC rather than base-game or redistributable content.

    The depot record and the rendered cell are both consulted, because
    enrichment fills them at different times, and a depot that the helpers call
    the base game's main depot is never DLC no matter what its text says.
    """
    is_dlc = (
        data.get("is_dlc", False)
        or "[dlc]" in desc.lower()
        or "[dlc]" in config_text.lower()
        or bool(re.search(r"\bDLC\s+\d+", desc, re.IGNORECASE))
        or bool(re.search(r"\bDLC\s+\d+", config_text, re.IGNORECASE))
        or bool(data.get("dlcappid"))
    )
    if not is_dlc:
        return False

    from utils.dlc_helpers import is_base_game_main_depot

    return not is_base_game_main_depot(depot_id, desc, str(app_id))


def dlc_auto_selection(rows: Iterable[DepotRowFacts], app_id) -> DlcAutoSelection:
    """The depots DLC-only mode selects, following the four documented rules.

    1. Base-game and redistributable depots are never selected.
    2. DLC for both Windows and Linux means no automatic choice at all - the
       user has to say which platform they want.
    3. Sixty-four or more DLCs selects every DLC depot, stub sizes included.
    4. Fewer than that selects the DLC depots that are not one-KB stubs.
    """
    rows = list(rows)
    dlc_ids = set()
    has_win_dlc = False
    has_linux_dlc = False

    for row in rows:
        if not is_dlc_row(row.depot_id, row.desc, row.config_text, row.data, app_id):
            continue
        dlc_ids.add(row.depot_id)

        oslist = (row.data.get("oslist") or "").lower()
        if not oslist and "[windows" in row.config_text.lower():
            oslist = "windows"
        elif not oslist and "[linux" in row.config_text.lower():
            oslist = "linux"

        if "windows" in oslist:
            has_win_dlc = True
        if "linux" in oslist:
            has_linux_dlc = True

    os_conflict = has_win_dlc and has_linux_dlc
    if os_conflict:
        logger.info(
            f"[DepotSelection] App {app_id}: DLC depots detected for both Windows and Linux — "
            "bypassing auto-selection so user can pick target platform manually."
        )

    bypass_size_limit = len(dlc_ids) >= BYPASS_SIZE_LIMIT_AT

    if os_conflict:
        selected = frozenset()
    elif bypass_size_limit:
        selected = frozenset(dlc_ids)
    else:
        selected = frozenset(
            row.depot_id for row in rows if row.depot_id in dlc_ids and not 0 < row.size <= STUB_SIZE_LIMIT
        )

    return DlcAutoSelection(
        ids=selected,
        dlc_count=len(dlc_ids),
        os_conflict=os_conflict,
        bypass_size_limit=bypass_size_limit,
    )


def recommended_depots(
    app_id, depots: Dict[Any, Any], *, platform: str = "linux", use_steam: bool = True, steam_lookup=None
) -> List[str]:
    """The depots Steam would install, or the rules-based picker's answer.

    The Steam package lookup reads local caches and may raise or name nothing;
    either way the platform rules answer instead. Platform presets ask the
    rules directly and pass ``use_steam=False``.
    """
    if use_steam:
        if steam_lookup is None:
            try:
                from core.steam_package_info import get_steam_recommended_depots

                steam_lookup = get_steam_recommended_depots
            except Exception as err:
                logger.warning(f"[DepotSelection] Smart Select error: {err}")
                steam_lookup = None

        if steam_lookup is not None:
            try:
                rec_depots = steam_lookup(app_id, depots, target_platform=platform)
            except Exception as err:
                logger.warning(f"[DepotSelection] Smart Select error: {err}")
                rec_depots = []
            if rec_depots:
                return list(rec_depots)

    from ui.dialogs.depot_selection.rules import get_smart_default_depots

    return list(get_smart_default_depots(depots, target_platform=platform))


def platform_label(platform: str) -> str:
    """What the edition menu calls a platform."""
    return "Linux (Native)" if platform == "linux" else "Windows (Proton)"


def edition_clean_name(edition: dict) -> str:
    """An edition's name without the parenthesised notes the button does not need."""
    full_name = edition.get("name") or edition.get("short_name", "Edition")
    clean_name = re.sub(r"\s*\(All DLCs\)", "", full_name, flags=re.IGNORECASE).strip()
    return re.sub(r"\s*\(Base Game Only\)", "", clean_name, flags=re.IGNORECASE).strip()


def edition_button_text(edition: dict, *, multi_platform: bool = False) -> str:
    """The Edition button's label for a chosen edition, truncated to fit."""
    clean_name = edition_clean_name(edition)
    plat = edition.get("platform", "")
    if multi_platform:
        prefix = "Linux: " if plat == "linux" else "Win: "
        btn_text = f"{prefix}{clean_name} ▾"
    else:
        btn_text = f"{clean_name} ▾"

    if len(btn_text) > 42:
        return f"{btn_text[:38].rstrip()}... ▾"
    return btn_text


def edition_button_tooltip(edition: dict) -> str:
    """The Edition button's tooltip: the platform, then the edition's clean name."""
    clean_name = edition_clean_name(edition)
    plat = edition.get("platform", "")
    return f"{plat.capitalize() + ': ' if plat else ''}{clean_name}"


@dataclass(frozen=True)
class EditionChoice:
    """One entry of the edition menu."""

    name: str
    edition: Dict[str, Any]
    checked: bool


@dataclass(frozen=True)
class EditionMenuGroup:
    """One platform's worth of the edition menu, or all of it when there is one."""

    label: Optional[str]
    choices: Tuple[EditionChoice, ...]


def edition_menu(
    platforms: Sequence[str],
    editions_by_platform: Dict[str, List[dict]],
    current_selection: Iterable[str],
) -> Tuple[EditionMenuGroup, ...]:
    """The shape of the edition menu: a group per platform, and what is ticked.

    More than one platform gives each its own submenu; a single platform leaves
    the entries at the top level, with no label to explain what is obvious.
    """
    current = {str(d) for d in current_selection}

    def choices_for(platform: str) -> Tuple[EditionChoice, ...]:
        return tuple(
            EditionChoice(
                name=ed["name"],
                edition=ed,
                checked={str(d) for d in ed.get("depot_ids", set())} == current,
            )
            for ed in editions_by_platform.get(platform, [])
        )

    if len(platforms) > 1:
        return tuple(EditionMenuGroup(platform_label(plat), choices_for(plat)) for plat in platforms)

    plat = platforms[0] if platforms else "windows"
    return (EditionMenuGroup(None, choices_for(plat)),)
