"""
depot_tag_helpers.py
====================
Helper functions for compact depot row presentation:
- Platform OS icons (Windows, Linux, Apple/macOS, Android)
- Compact [DLC] tag
- Language badges ([AR], [BR], [EN], etc.)
"""
import re
from typing import Optional, Dict
from PyQt6.QtGui import QIcon
from utils.paths import Paths

# Map known language names to standard short tags
STEAM_LANG_TO_CODE: Dict[str, str] = {
    "arabic": "AR",
    "brazilian": "BR",
    "portuguese-brazil": "BR",
    "bulgarian": "BG",
    "czech": "CS",
    "danish": "DA",
    "dutch": "NL",
    "english": "EN",
    "finnish": "FI",
    "french": "FR",
    "german": "DE",
    "greek": "EL",
    "hungarian": "HU",
    "indonesian": "ID",
    "italian": "IT",
    "japanese": "JA",
    "koreana": "KO",
    "korean": "KO",
    "latam": "ES-419",
    "norwegian": "NO",
    "polish": "PL",
    "portuguese": "PT",
    "romanian": "RO",
    "russian": "RU",
    "schinese": "ZH-CN",
    "simplified chinese": "ZH-CN",
    "spanish": "ES",
    "swedish": "SV",
    "tchinese": "ZH-TW",
    "traditional chinese": "ZH-TW",
    "thai": "TH",
    "turkish": "TR",
    "ukrainian": "UK",
    "vietnamese": "VI",
}

_LANG_REGEX_PATTERNS = [
    # Explicit [en], [br], [ar] in description
    (re.compile(r"\[(ar|bg|cs|da|de|el|en|es|fi|fr|hu|id|it|ja|ko|nl|no|pl|pt|pt-br|br|ro|ru|sv|th|tr|uk|vi|zh|zh-cn|zh-tw)\]", re.IGNORECASE), 1),
    # Patterns like "Brazilian Audio", "French Voice Pack", "Japanese Language Pack"
    (re.compile(r"\b(arabic|brazilian|czech|danish|dutch|english|finnish|french|german|greek|hungarian|indonesian|italian|japanese|korean|norwegian|polish|portuguese|romanian|russian|spanish|swedish|thai|turkish|ukrainian|vietnamese)\s+(?:voice|audio|language|subs|subtitles)\b", re.IGNORECASE), 1),
    # "Voice Pack - German", "Language Pack - French"
    (re.compile(r"(?:voice|audio|language)\s+pack\s*[-–:]\s*(arabic|brazilian|czech|danish|dutch|english|finnish|french|german|greek|hungarian|indonesian|italian|japanese|korean|norwegian|polish|portuguese|romanian|russian|spanish|swedish|thai|turkish|ukrainian|vietnamese)", re.IGNORECASE), 1),
]

_OS_ICON_CACHE: Dict[str, Optional[QIcon]] = {}


def extract_language_code(depot_data: dict, desc: str = "") -> Optional[str]:
    """Detects language tag (e.g. 'AR', 'BR', 'EN', 'JA') from metadata or description."""
    if not isinstance(depot_data, dict):
        depot_data = {}

    # 1. Direct language metadata field
    lang_field = str(depot_data.get("language") or "").strip().lower()
    if lang_field and lang_field not in ("all", "none", "shared"):
        if lang_field in STEAM_LANG_TO_CODE:
            return STEAM_LANG_TO_CODE[lang_field]
        if len(lang_field) in (2, 5):
            return lang_field.upper()

    # 2. Check description / name
    text_to_check = f"{desc} {depot_data.get('desc', '')} {depot_data.get('name', '')}"
    for pat, group_idx in _LANG_REGEX_PATTERNS:
        m = pat.search(text_to_check)
        if m:
            val = m.group(group_idx).lower()
            if val in STEAM_LANG_TO_CODE:
                return STEAM_LANG_TO_CODE[val]
            return val.upper()

    return None


def get_os_icon(oslist: Optional[str]) -> Optional[QIcon]:
    """Returns QIcon for platform logo if available."""
    if not oslist:
        return None
    os_str = str(oslist).strip().lower()
    if os_str in _OS_ICON_CACHE:
        return _OS_ICON_CACHE[os_str]

    icon: Optional[QIcon] = None
    if "windows" in os_str or "win" in os_str:
        p = Paths.resource("logo/os/windows-8.png")
        if p.exists():
            icon = QIcon(str(p))
    elif "linux" in os_str:
        p = Paths.resource("logo/os/linux-platform.png")
        if p.exists():
            icon = QIcon(str(p))
    elif "macos" in os_str or "macosx" in os_str or "apple" in os_str:
        p = Paths.resource("logo/os/apple.png")
        if p.exists():
            icon = QIcon(str(p))
    elif "android" in os_str:
        p = Paths.resource("logo/os/android.png")
        if p.exists():
            icon = QIcon(str(p))

    _OS_ICON_CACHE[os_str] = icon
    return icon


def get_os_display_name(oslist: Optional[str]) -> str:
    """Returns platform name if no logo is available."""
    if not oslist:
        return ""
    os_str = str(oslist).strip().lower()
    if any(k in os_str for k in ("windows", "win", "linux", "macos", "macosx", "apple", "android", "all")):
        return ""
    return f"[{oslist.capitalize()}]"


def format_depot_row_label(
    depot_id: str,
    depot_data: dict,
    base_desc: str,
    has_os_icon: bool = True,
) -> str:
    """
    Builds clean, compact depot description string:
    - Replaces '[DLC <id>]' with '[DLC]'
    - Inserts language badge '[AR]', '[BR]', etc. right upfront next to [DLC]
    - Omits OS tag if has_os_icon is True (unless OS has no icon)
    """
    if not isinstance(depot_data, dict):
        depot_data = {}

    is_dlc = (
        depot_data.get("is_dlc", False)
        or bool(depot_data.get("dlcappid"))
        or "[dlc" in base_desc.lower()
        or bool(re.search(r"\bDLC\s+\d+", base_desc, re.IGNORECASE))
    )

    clean_desc = base_desc
    # Strip existing [Windows], [Linux], [DLC ...], [lang] tags from base_desc
    clean_desc = re.sub(
        r"\[(?:Windows|Linux|macOS|All)(?:,\s*(?:Windows|Linux|macOS))*\]\s*",
        "",
        clean_desc,
        flags=re.IGNORECASE,
    )
    clean_desc = re.sub(r"\[DLC(?:\s+\d+)?\]\s*", "", clean_desc, flags=re.IGNORECASE)
    clean_desc = re.sub(r"^DLC\s+\d+\s*[-–:]?\s*", "", clean_desc, flags=re.IGNORECASE)

    tags = []
    if not has_os_icon:
        os_name = get_os_display_name(depot_data.get("oslist"))
        if os_name:
            tags.append(os_name)

    if is_dlc:
        tags.append("[DLC]")

    lang_code = extract_language_code(depot_data, clean_desc)
    if lang_code:
        # Strip redundant [lang] tag from clean_desc if already extracted
        clean_desc = re.sub(
            r"\[" + re.escape(lang_code) + r"\]\s*", "", clean_desc, flags=re.IGNORECASE
        )
        tags.append(f"[{lang_code}]")

    tag_str = " ".join(tags)
    clean_desc = clean_desc.strip()
    if not clean_desc or re.fullmatch(r"Depot \d+", clean_desc, re.IGNORECASE):
        clean_desc = depot_data.get("name") or clean_desc or f"Depot {depot_id}"

    if tag_str:
        return f"{tag_str}  {clean_desc}".strip()
    return clean_desc.strip()
