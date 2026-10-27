"""Depot classification rules: which depot goes in which bucket.

Pure functions over a depot dict - no Qt, no dialog, no I/O. These decide
"is this a macOS depot?", "is this a soundtrack?", "which depots should be
pre-ticked?", and they are now unit-testable without constructing a widget.

Moved verbatim from ``depotselection``; behaviour is unchanged. Every name is
re-exported from ``ui.dialogs.depotselection``.
"""

def _depot_matches_platform(depot_data: dict, platform: str) -> bool:
    """Check if a depot matches the given platform (linux/windows).

    A depot matches if:
    - Its oslist contains the platform name, OR
    - Its description tags contain [PLATFORM], OR
    - It has no oslist set (shared/common depot)
    """
    oslist = (depot_data.get("oslist") or "").lower()
    desc = (depot_data.get("desc") or "").lower()
    platform = platform.lower()

    # No oslist means it's a shared depot (common to all platforms)
    if not oslist:
        return True

    # Check oslist field (can be "windows", "linux", "windows,linux", etc.)
    if platform in oslist:
        return True

    # Check description tags like [LINUX], [WINDOWS]
    if f"[{platform}]" in desc:
        return True

    return False
def _depot_is_macos(depot_data: dict) -> bool:
    """Check if a depot is macOS-only (does not support Windows or Linux)."""
    oslist = (depot_data.get("oslist") or "").lower()
    desc = (depot_data.get("desc") or "").lower()

    # If it also supports Windows or Linux, it is a shared depot, NOT macOS-only
    if "windows" in oslist or "win" in oslist or "linux" in oslist or "[windows]" in desc or "[linux]" in desc:
        return False

    # Check oslist
    if oslist in ("macosx", "macos"):
        return True

    # Check description tags
    if "[macos]" in desc or "[macosx]" in desc:
        return True

    return False
def _depot_is_android(depot_data: dict) -> bool:
    """Check if a depot is Android-only (does not support Windows or Linux)."""
    oslist = (depot_data.get("oslist") or "").lower()
    desc = (depot_data.get("desc") or "").lower()

    # If it also supports Windows or Linux, it is a shared depot, NOT Android-only
    if "windows" in oslist or "win" in oslist or "linux" in oslist or "[windows]" in desc or "[linux]" in desc:
        return False

    # Check oslist
    if oslist == "android":
        return True

    # Check description tags
    if "[android]" in desc:
        return True

    return False
def is_bonus_or_media_depot(depot_data: dict) -> bool:
    """Check if depot is soundtrack, wallpaper, artbook, manual, etc."""
    text = (
        (depot_data.get("desc") or "") + " " +
        (depot_data.get("name") or "")
    ).lower()
    
    bonus_keywords = [
        "soundtrack", " ost", "ost ", "(ost)", "[ost]", "original soundtrack", "bonus track",
        "wallpaper", "artbook", "art book", "manual", "guide", "strategy guide",
        "comic", "novel", "goodies", "avatar", "poster", "press kit", "bonus content",
        "extra content", "credits", "dedicated server", "server"
    ]
    for kw in bonus_keywords:
        if kw in text:
            return True
    return False
def get_smart_default_depots(depots: dict, target_platform: str = "linux", language: str = "english") -> list:
    """
    Intelligently pre-select depots for the user:
    1. If target_platform == "linux" and native Linux depots exist -> Target Linux + shared.
       If target_platform == "linux" and NO native Linux depots exist -> Target Windows + shared (for Proton).
       If target_platform == "windows" -> Target Windows + shared.
    2. Exclude macOS-only and Android-only depots.
    3. Exclude soundtracks, wallpapers, artbooks, manuals, bonus media by default.
    4. Exclude 32-bit depots if 64-bit depots exist for the target architecture.
    5. Prioritize selected language (English) if language-specific depots exist.
    6. Safety: fallback to non-macOS/non-Android depots if filtered list is empty.
    """
    if not depots:
        return []

    # Check if there are any dedicated native Linux depots (not shared with Windows)
    has_linux = False
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict) or _depot_is_macos(d_data) or _depot_is_android(d_data):
            continue
        oslist = (d_data.get("oslist") or "").lower()
        desc = (d_data.get("desc") or "").lower()
        is_lin = "linux" in oslist or "[linux]" in desc
        is_win = "windows" in oslist or "win" in oslist or "[windows]" in desc or ".exe" in desc
        if is_lin and not is_win:
            has_linux = True
            break

    if target_platform.lower() == "linux":
        active_platform = "linux" if has_linux else "windows"
    else:
        active_platform = "windows"

    from utils.depot_tag_helpers import extract_arch_code

    # Check if 64-bit depots exist for active platform
    has_64 = False
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict):
            continue
        if not _depot_matches_platform(d_data, active_platform):
            continue
        desc = (d_data.get("desc") or "")
        if extract_arch_code(d_data, desc) == "x64":
            has_64 = True
            break

    # Check if language-specific depots exist
    has_lang_depots = False
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict):
            continue
        d_lang = (d_data.get("language") or "").lower()
        desc = (d_data.get("desc") or "").lower()
        if d_lang or any(f"[{l}]" in desc for l in ["english", "french", "german", "spanish", "italian", "japanese", "chinese", "russian", "korean"]):
            has_lang_depots = True
            break

    selected = []
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict):
            continue

        # 1. Skip macOS
        if _depot_is_macos(d_data):
            continue

        # 2. Skip bonus/media (soundtracks, wallpapers, artbooks)
        if is_bonus_or_media_depot(d_data):
            continue

        # 3. Match platform (Linux if native exists, else Windows + shared)
        if not _depot_matches_platform(d_data, active_platform):
            continue

        # 4. Filter 32-bit if 64-bit exists
        if has_64:
            desc = (d_data.get("desc") or "")
            if extract_arch_code(d_data, desc) == "x86":
                continue

        # 5. Language filtering if applicable
        if has_lang_depots:
            d_lang = (d_data.get("language") or "").lower()
            desc = (d_data.get("desc") or "").lower()
            if d_lang and d_lang != language.lower() and d_lang != "all":
                continue
            other_langs = ["french", "german", "spanish", "italian", "japanese", "chinese", "russian", "korean", "portuguese", "polish"]
            if language.lower() in other_langs:
                other_langs.remove(language.lower())
            if any(f"[{l}]" in desc for l in other_langs) and f"[{language.lower()}]" not in desc:
                continue

        selected.append(str(d_id))

    # Safety fallback: if everything got filtered out, fallback to basic non-macOS/non-Android matching
    if not selected:
        for d_id, d_data in depots.items():
            if isinstance(d_data, dict) and not _depot_is_macos(d_data) and not _depot_is_android(d_data):
                if _depot_matches_platform(d_data, active_platform):
                    selected.append(str(d_id))

    # If still empty, return all depot keys
    if not selected:
        selected = [str(k) for k in depots.keys()]

    return selected
def format_size(size_bytes):
    if not size_bytes:
        return "0.00 B"
    try:
        bytes_val = int(size_bytes)
        if bytes_val <= 0:
            return "0.00 B"
        for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
            if bytes_val < 1024.0:
                return f"{bytes_val:.2f} {unit}"
            bytes_val /= 1024.0
        return f"{bytes_val:.2f} PiB"
    except Exception:
        return "Unknown"


def is_effectively_single_depot(depots: dict, missing_depots=None) -> bool:
    """Return True if depots contain only one active content depot and no missing depots,
    taking into account the 'show_hidden_depots_in_selector' setting.
    """
    if not isinstance(depots, dict) or not depots:
        return False
    if missing_depots:
        return False

    # If user enabled "show hidden depots in selector" in settings,
    # then hidden depots are visible in the main table and count as full options.
    try:
        from utils.settings import get_settings
        settings = get_settings()
        show_hidden = settings.value("show_hidden_depots_in_selector", False, type=bool)
        if show_hidden:
            return len(depots) == 1
    except Exception:
        pass

    # Filter out hidden depots (macOS, Android, bonus/media/soundtrack/etc.)
    active_depots = []
    for did, d_data in depots.items():
        if not isinstance(d_data, dict):
            active_depots.append(str(did))
            continue
        if _depot_is_macos(d_data) or _depot_is_android(d_data) or is_bonus_or_media_depot(d_data):
            continue
        active_depots.append(str(did))

    # If all depots were filtered out as hidden, fallback to total depot count
    if not active_depots and depots:
        return len(depots) == 1

    return len(active_depots) == 1


def get_effective_single_depot(depots: dict, app_id: str = "") -> tuple:
    """Return (depot_id, depot_data) for the single active depot from a depots dict."""
    if not isinstance(depots, dict) or not depots:
        return str(app_id), {}

    active = {}
    for did, d_data in depots.items():
        if str(did) == str(app_id):
            continue
        if isinstance(d_data, dict) and (_depot_is_macos(d_data) or _depot_is_android(d_data) or is_bonus_or_media_depot(d_data)):
            continue
        active[str(did)] = d_data

    target_dict = active if active else {str(k): v for k, v in depots.items() if str(k) != str(app_id)}
    if not target_dict:
        return str(app_id), {}
    first_id = next(iter(target_dict.keys()))
    return first_id, target_dict.get(first_id, {})
