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
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPixmap, QColor, QPainter
from utils.paths import Paths
from utils.dlc_helpers import is_base_game_main_depot

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


def get_os_icon(oslist: Optional[str], tint_color: Optional[QColor] = None) -> Optional[QIcon]:
    """Returns QIcon for platform logo(s), composite if multi-OS, optionally tinted to match theme."""
    if not oslist:
        return None
    os_str = str(oslist).strip().lower()
    cache_key = f"{os_str}_{tint_color.name() if tint_color and tint_color.isValid() else 'none'}"
    if cache_key in _OS_ICON_CACHE:
        return _OS_ICON_CACHE[cache_key]

    platforms = []
    if "windows" in os_str or "win" in os_str:
        platforms.append(("windows", Paths.resource("logo/os/windows-8.png")))
    if "linux" in os_str:
        platforms.append(("linux", Paths.resource("logo/os/linux-platform.png")))
    if "macos" in os_str or "macosx" in os_str or "apple" in os_str:
        platforms.append(("macos", Paths.resource("logo/os/apple.png")))
    if "android" in os_str:
        platforms.append(("android", Paths.resource("logo/os/android.png")))

    if not platforms and "all" in os_str:
        platforms = [
            ("windows", Paths.resource("logo/os/windows-8.png")),
            ("linux", Paths.resource("logo/os/linux-platform.png")),
        ]

    valid = [(name, p) for name, p in platforms if p and p.exists()]
    if not valid:
        _OS_ICON_CACHE[cache_key] = None
        return None

    if len(valid) == 1:
        p = valid[0][1]
        raw = QPixmap(str(p))
        if raw.isNull():
            _OS_ICON_CACHE[cache_key] = None
            return None
        if tint_color and tint_color.isValid():
            tinted = QPixmap(raw.size())
            tinted.fill(Qt.GlobalColor.transparent)
            painter = QPainter(tinted)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.drawPixmap(0, 0, raw)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(tinted.rect(), tint_color)
            painter.end()
            icon = QIcon(tinted)
        else:
            icon = QIcon(raw)
    else:
        # Composite side-by-side icons (up to 4, plus '+' if more)
        icon_sz = 14
        gap = 2
        max_displayed = min(len(valid), 4)
        has_more = len(valid) > 4

        plus_width = 8 if has_more else 0
        total_w = max_displayed * icon_sz + (max_displayed - 1) * gap + plus_width
        total_h = 16

        composite = QPixmap(total_w, total_h)
        composite.fill(Qt.GlobalColor.transparent)

        painter = QPainter(composite)
        for idx in range(max_displayed):
            name, path = valid[idx]
            x = idx * (icon_sz + gap)
            y = (total_h - icon_sz) // 2
            raw = QPixmap(str(path))
            if not raw.isNull():
                scaled = raw.scaled(
                    icon_sz,
                    icon_sz,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                if tint_color and tint_color.isValid():
                    tinted = QPixmap(scaled.size())
                    tinted.fill(Qt.GlobalColor.transparent)
                    tp = QPainter(tinted)
                    tp.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                    tp.drawPixmap(0, 0, scaled)
                    tp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                    tp.fillRect(tinted.rect(), tint_color)
                    tp.end()
                    painter.drawPixmap(x, y, tinted)
                else:
                    painter.drawPixmap(x, y, scaled)

        if has_more:
            plus_x = max_displayed * (icon_sz + gap)
            painter.setPen(tint_color if tint_color and tint_color.isValid() else QColor("#ffffff"))
            font = painter.font()
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(plus_x, 0, plus_width, total_h, Qt.AlignmentFlag.AlignCenter, "+")

        painter.end()
        icon = QIcon(composite)

    _OS_ICON_CACHE[cache_key] = icon
    return icon


def extract_arch_code(depot_data: dict, desc: str = "") -> Optional[str]:
    """Detects architecture tag ('x64' or 'x86') from metadata or description."""
    if not isinstance(depot_data, dict):
        depot_data = {}

    # 1. Direct metadata field (from PICS config.osarch or direct osarch)
    arch = str(
        depot_data.get("osarch")
        or (depot_data.get("config", {}).get("osarch") if isinstance(depot_data.get("config"), dict) else None)
        or ""
    ).strip().lower()

    if arch in ("64", "x64", "amd64", "x86_64", "win64", "linux64"):
        return "x64"
    if arch in ("32", "86", "x86", "win32", "i386", "i686"):
        return "x86"

    # 2. Check description and name
    text = f"{desc} {depot_data.get('desc', '')} {depot_data.get('name', '')}".lower()
    if any(k in text for k in ("64-bit", "64 bit", "x64", "win64", "[64]", "[x64]")):
        return "x64"
    if any(k in text for k in ("32-bit", "32 bit", "x86", "win32", "[32]", "[x86]")):
        return "x86"

    return None


def get_os_tag(oslist: Optional[str]) -> str:
    """Returns standardized [WINDOWS], [LINUX], [MACOS] or empty string for multi-OS."""
    if not oslist:
        return ""
    os_str = str(oslist).strip().lower()
    platforms = []
    if "windows" in os_str or "win" in os_str:
        platforms.append("WINDOWS")
    if "linux" in os_str:
        platforms.append("LINUX")
    if "macos" in os_str or "macosx" in os_str or "apple" in os_str:
        platforms.append("MACOS")
    if "android" in os_str:
        platforms.append("ANDROID")

    if len(platforms) > 1 or "all" in os_str or "shared" in os_str:
        return ""
    if len(platforms) == 1:
        return f"[{platforms[0]}]"
    return f"[{oslist.upper()}]"


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
    has_os_icon: bool = False,
    show_os_badge: bool = True,
    base_appid: Optional[str] = None,
) -> str:
    """
    Builds clean, structured depot description string with tags:
    Tag order: [OS] [ARCH] [DLC] [LANG]  Depot Name
    e.g. [WINDOWS] [x64] [DLC] [EN]  Metro 2033 Redux Content
    """
    if not isinstance(depot_data, dict):
        depot_data = {}

    depot_id_str = str(depot_id).strip()
    base_appid_str = str(base_appid).strip() if base_appid else ""

    # Check base game exclusion: base game main depots must never be marked as DLC
    is_base = False
    if base_appid_str:
        if depot_id_str == base_appid_str:
            is_base = True
        elif str(depot_data.get("dlcappid") or "").strip() == base_appid_str:
            is_base = True
        elif is_base_game_main_depot(
            depot_id_str, base_desc, base_appid_str, depot_data
        ):
            is_base = True

    dlc_appid = depot_data.get("dlcappid")
    if is_base:
        is_dlc = False
    else:
        is_dlc = (
            depot_data.get("is_dlc", False)
            or (bool(dlc_appid) and str(dlc_appid).strip() != base_appid_str)
            or "[dlc" in base_desc.lower()
            or bool(re.search(r"\bDLC\s+\d+", base_desc, re.IGNORECASE))
        )

    raw_name = str(depot_data.get("name") or "").strip()
    if (not base_desc or re.fullmatch(r"Depot \d+", base_desc.strip(), re.IGNORECASE)) and raw_name and not re.fullmatch(r"Depot \d+", raw_name, re.IGNORECASE):
        base_desc = raw_name

    clean_desc = base_desc
    # Strip existing platform, arch, dlc, and language tags from clean_desc
    clean_desc = re.sub(
        r"\[(?:Windows|Linux|macOS|Android|iOS|SteamDeck|Deck|All)(?:,\s*(?:Windows|Linux|macOS|Android|iOS|SteamDeck|Deck|All))*\]\s*",
        "",
        clean_desc,
        flags=re.IGNORECASE,
    )
    clean_desc = re.sub(r"\[(?:x64|x86|64-bit|32-bit|64|32)\]\s*", "", clean_desc, flags=re.IGNORECASE)
    clean_desc = re.sub(r"\[DLC(?:\s+\d+)?\]\s*", "", clean_desc, flags=re.IGNORECASE)
    clean_desc = re.sub(r"^DLC\s+\d+\s*[-–:]?\s*", "", clean_desc, flags=re.IGNORECASE)

    tags = []

    # 1. [OS] text badge (only if no icon is shown)
    if not has_os_icon:
        if show_os_badge:
            os_tag = get_os_tag(depot_data.get("oslist"))
            if os_tag:
                tags.append(os_tag)
        else:
            os_name = get_os_display_name(depot_data.get("oslist"))
            if os_name:
                tags.append(os_name)

    # 2. [ARCH] badge ([x64] / [x86])
    arch_code = extract_arch_code(depot_data, base_desc)
    if arch_code:
        tags.append(f"[{arch_code}]")

    # 3. [DLC] badge
    if is_dlc:
        tags.append("[DLC]")

    # 4. [LANG] badge ([FR], [EN], etc.)
    lang_code = extract_language_code(depot_data, clean_desc)
    if lang_code:
        clean_desc = re.sub(
            r"\[" + re.escape(lang_code) + r"\]\s*", "", clean_desc, flags=re.IGNORECASE
        )
        tags.append(f"[{lang_code}]")

    # 5. Media tags
    desc_lower = clean_desc.lower()
    if "soundtrack" in desc_lower or " ost" in desc_lower or "[ost]" in desc_lower:
        tags.append("[SOUNDTRACK]")
    elif "artbook" in desc_lower or "art book" in desc_lower:
        tags.append("[ARTBOOK]")
    elif "wallpaper" in desc_lower:
        tags.append("[WALLPAPER]")

    clean_desc = re.sub(r"\s*-\s*Depot\s*" + re.escape(depot_id_str), "", clean_desc, flags=re.IGNORECASE).strip()

    tag_str = " ".join(tags)
    clean_desc = clean_desc.strip()
    if not clean_desc or re.fullmatch(r"Depot \d+", clean_desc, re.IGNORECASE):
        fallback_name = depot_data.get("name") or clean_desc or f"Depot {depot_id}"
        clean_desc = re.sub(
            r"\[(?:Windows|Linux|macOS|Android|iOS|SteamDeck|Deck|All)(?:,\s*(?:Windows|Linux|macOS|Android|iOS|SteamDeck|Deck|All))*\]\s*",
            "",
            fallback_name,
            flags=re.IGNORECASE,
        ).strip() or fallback_name

    if tag_str:
        return f"{tag_str}  {clean_desc}".strip()
    return clean_desc.strip()
