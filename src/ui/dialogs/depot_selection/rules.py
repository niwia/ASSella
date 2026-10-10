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
    """Check if a depot is macOS-only."""
    oslist = (depot_data.get("oslist") or "").lower()
    desc = (depot_data.get("desc") or "").lower()

    # Check oslist
    if oslist in ("macosx", "macos"):
        return True

    # Check description tags
    if "[macos]" in desc or "[macosx]" in desc:
        return True

    return False
def _depot_is_android(depot_data: dict) -> bool:
    """Check if a depot is Android-only."""
    oslist = (depot_data.get("oslist") or "").lower()
    desc = (depot_data.get("desc") or "").lower()

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

    # Check if there are any native Linux depots
    has_linux = False
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict) or _depot_is_macos(d_data) or _depot_is_android(d_data):
            continue
        oslist = (d_data.get("oslist") or "").lower()
        desc = (d_data.get("desc") or "").lower()
        if "linux" in oslist or "[linux]" in desc:
            has_linux = True
            break

    if target_platform.lower() == "linux":
        active_platform = "linux" if has_linux else "windows"
    else:
        active_platform = "windows"

    # Check if 64-bit depots exist for active platform
    has_64 = False
    for d_id, d_data in depots.items():
        if not isinstance(d_data, dict):
            continue
        if not _depot_matches_platform(d_data, active_platform):
            continue
        osarch = str(d_data.get("osarch") or "").lower()
        desc = (d_data.get("desc") or "").lower()
        if osarch == "64" or "64-bit" in desc or "x64" in desc or "64 bit" in desc or "[64]" in desc:
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
            osarch = str(d_data.get("osarch") or "").lower()
            desc = (d_data.get("desc") or "").lower()
            is_32 = osarch == "32" or "32-bit" in desc or "x86" in desc or "32 bit" in desc or "[32]" in desc or "[x86]" in desc
            if is_32:
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
