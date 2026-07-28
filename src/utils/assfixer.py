#!/usr/bin/env python3
"""
SLSsteam config.yaml cleanup & update tool
==========================================
Fetches the latest default config template from the SLSsteam GitHub repo,
parases your existing config.yaml, and produces a perfectly-formatted output
that carries over ALL your personal values into the new template structure.

This tool is fully self-adapting: key types are inferred automatically from:
  1. Inline default values in the template (scalar detection)
  2. Commented-out examples in the template header (list vs map detection)
  3. The structure of your own config.yaml for any remaining unknowns

No manual updates are needed when the SLSsteam developer adds or removes keys.

Preserved exactly as-is (with normalized spacing):
  - AdditionalApps / AppIds / FakeOffline  list items + their inline comments
  - FakeAppIds / AppTokens mapping entries + their inline comments
  - GameTitles / SubscriptionTimestamps / DlcData / DenuvoGames entries
  - All scalar settings (DisableFamilyShareLock, LogLevel, FakeEmail, etc.)
  - IdleStatus sub-map

Cleaned automatically:
  - Duplicate AppToken / mapping keys → deduplicated (last value wins)
  - Extra spaces before inline comments → normalized to one space
  - Trailing whitespace on every line
  - All template comments are preserved verbatim

Usage:
    python3 assfixer.py [OPTIONS]

Options:
    --config PATH         Path to config.yaml (default: auto-detected)
    --output PATH         Where to write the result (default: overwrites --config after backup)
    --dry-run             Print result to stdout, don't write to disk
    --no-backup           Skip creating a .bak backup before writing
    --validate-only       Only check for errors in your current config, don't write
    --no-resolve-names    Skip outbound SteamCMD API calls for game name resolution
    --template-url URL    Override the GitHub raw URL for the default config template
    --manifests APPID     Query SteamCMD API for an AppID's depot manifests, and print them as a YAML snippet
    --version             Show version and exit
"""

import argparse
import json
import re
import shutil
import sys
import textwrap
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Set

# ──────────────────────────────────────────────────────────────
# Version
# ──────────────────────────────────────────────────────────────

VERSION = "3.2.0"

boot_status = None
boot_issues = []

# ──────────────────────────────────────────────────────────────
# Path / URL constants
# ──────────────────────────────────────────────────────────────

FLATPAK_CONFIG_PATH = (
    Path.home() / ".var" / "app" / "com.valvesoftware.Steam"
    / ".config" / "SLSsteam" / "config.yaml"
)
NATIVE_CONFIG_PATH  = Path.home() / ".config" / "SLSsteam" / "config.yaml"
DEFAULT_CONFIG_PATH = FLATPAK_CONFIG_PATH if FLATPAK_CONFIG_PATH.exists() else NATIVE_CONFIG_PATH

# C++ source that embeds the YAML default template as a raw string literal.
TEMPLATE_SOURCE_URL = (
    "https://raw.githubusercontent.com/AceSLS/SLSsteam/main/src/config_default.hpp"
)

TEMPLATE_TIMEOUT  = 15   # seconds – GitHub raw file download
STEAM_API_TIMEOUT =  5   # seconds – per-game name lookups (many in parallel)

# ──────────────────────────────────────────────────────────────
# Key type constants
# ──────────────────────────────────────────────────────────────

TYPE_SCALAR       = "scalar"        # single value (yes/no, number, string)
TYPE_LIST         = "list"          # sequence of `  - item` entries
TYPE_MAP          = "map"           # mapping of `  key: value` entries
TYPE_MAP_OF_LISTS = "map_of_lists"  # mapping whose values are sub-lists
TYPE_SUBMAP       = "submap"        # fixed-key sub-map (e.g. IdleStatus)
TYPE_UNKNOWN      = "unknown"       # auto-detected at parse time from user data

# These map keys expect numeric (integer) values — used for sanity-checking.
# This list rarely changes since it reflects Steam's numeric ID system.
NUMERIC_VALUE_MAP_KEYS = {
    "AppTokens", "FakeAppIds", "SubscriptionTimestamps",
    "ManifestIds", "DlcData",
}

IDLE_STATUS_KEY = "IdleStatus"

# ──────────────────────────────────────────────────────────────
# Colours / logging helpers
# ──────────────────────────────────────────────────────────────

RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def info(msg):  print(f"{CYAN}[INFO]{RESET}  {msg}")
def ok(msg):    print(f"{GREEN}[OK]{RESET}    {msg}")
def warn(msg):  print(f"{YELLOW}[WARN]{RESET}  {msg}")
def error(msg): print(f"{RED}[ERROR]{RESET} {msg}", file=sys.stderr)

# ──────────────────────────────────────────────────────────────
# Formatting / sanitization helpers
# ──────────────────────────────────────────────────────────────

_TITLE_RE = re.compile(r'[^\x20-\x7E]')

def sanitize_title(s: str) -> str:
    s = _TITLE_RE.sub("", s).strip().strip('"\'')
    if not s:
        return '""'
    needs_quotes = any(c in s for c in (':', '#', '"', "'", '[', ']', '{', '}'))
    if needs_quotes:
        s = s.replace('"', '\\"')
        return f'"{s}"'
    return s


# ──────────────────────────────────────────────────────────────
# Network helpers
# ──────────────────────────────────────────────────────────────

def _fetch_url(url: str, timeout: int) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": f"ASSfixer/{VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def fetch_template(url: str = TEMPLATE_SOURCE_URL) -> str:
    """Download config_default.hpp and extract the embedded YAML template."""
    try:
        raw = _fetch_url(url, TEMPLATE_TIMEOUT)
    except Exception as exc:
        raise RuntimeError(f"Failed to download template: {exc}")
    m = re.search(r'static const char\* defaultConfig = R"\((.+?)\)";', raw, re.DOTALL)
    if not m:
        raise RuntimeError("Could not find YAML template in the downloaded C++ file.")
    return m.group(1)


# ──────────────────────────────────────────────────────────────
# Dynamic key-type inference
# ──────────────────────────────────────────────────────────────

def _parse_commented_examples(raw_hpp: str) -> dict:
    """
    Parse commented-out YAML examples from the template header to infer
    the type of keys that would otherwise be ambiguous (empty defaults).
    """
    inferred: dict[str, str] = {}
    lines = raw_hpp.splitlines()

    i = 0
    while i < len(lines):
        m = re.match(r'^#([A-Za-z][A-Za-z0-9_]*)\s*:\s*$', lines[i].strip())
        if not m:
            i += 1
            continue

        key = m.group(1)
        j = i + 1
        while j < len(lines) and (not lines[j].strip() or lines[j].strip() == "#"):
            j += 1

        if j < len(lines):
            child = lines[j].strip().lstrip("#").strip()
            if child.startswith("- ") or child == "-":
                inferred[key] = TYPE_LIST
            elif ":" in child:
                k = j + 1
                while k < len(lines) and (not lines[k].strip() or lines[k].strip() == "#"):
                    k += 1
                if k < len(lines):
                    grandchild = lines[k].strip().lstrip("#").strip()
                    if grandchild.startswith("- ") or grandchild == "-":
                        inferred[key] = TYPE_MAP_OF_LISTS
                    else:
                        inferred[key] = TYPE_MAP
                else:
                    inferred[key] = TYPE_MAP
            else:
                inferred[key] = TYPE_SUBMAP
        i += 1

    return inferred


def infer_key_types(raw_hpp: str) -> dict:
    """
    Auto-discover the type of every top-level config key without any external
    hints file. Uses two passes:
    Pass 1 — Template body: keys with non-empty defaults are detected as scalars;
              keys with indented children are detected as list/map/submap.
    Pass 2 — Template header comments: looks for commented-out example blocks.
    """
    comment_hints = _parse_commented_examples(raw_hpp)

    m = re.search(r'static const char\* defaultConfig = R"\((.+?)\)";', raw_hpp, re.DOTALL)
    template_yaml = m.group(1) if m else ""

    key_types: dict[str, str] = {}
    lines = template_yaml.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        m2 = re.match(r'^([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*)', line)
        if not m2:
            i += 1
            continue

        key   = m2.group(1)
        value = m2.group(2).strip()

        if value and not value.startswith("#"):
            key_types[key] = TYPE_SCALAR
            i += 1
            continue

        j = i + 1
        while j < n and (not lines[j].strip() or lines[j].strip().startswith("#")):
            j += 1

        if j < n and lines[j].startswith("  ") and not lines[j].strip().startswith("#"):
            child_line = lines[j].strip()
            if child_line.startswith("- ") or child_line == "-":
                key_types[key] = TYPE_LIST
            else:
                child_m = re.match(r'^([^:]+):\s*(.*)', child_line)
                if child_m:
                    child_val = child_m.group(2).strip()
                    k = j + 1
                    while k < n and (not lines[k].strip() or lines[k].strip().startswith("#")):
                        k += 1
                    if k < n and lines[k].startswith("    ") and lines[k].strip().startswith("- "):
                        key_types[key] = TYPE_MAP_OF_LISTS
                    elif not child_val or child_val.startswith("#"):
                        key_types[key] = TYPE_SUBMAP
                    else:
                        key_types[key] = TYPE_MAP
                else:
                    key_types[key] = TYPE_UNKNOWN
        else:
            key_types[key] = comment_hints.get(key, TYPE_UNKNOWN)

        i += 1

    return key_types


# ──────────────────────────────────────────────────────────────
# ConfigEntry
# ──────────────────────────────────────────────────────────────

@dataclass
class ConfigEntry:
    key: Optional[str]   # None for list items
    val: str             # raw value string (may include inline comment)

    def __hash__(self):
        return hash((self.key, self.val.split("#")[0].strip()))

    def __eq__(self, other):
        if not isinstance(other, ConfigEntry):
            return NotImplemented
        return (self.key == other.key and
                self.val.split("#")[0].strip() == other.val.split("#")[0].strip())


# ──────────────────────────────────────────────────────────────
# SimpleYAMLReader
# ──────────────────────────────────────────────────────────────

class SimpleYAMLReader:
    def __init__(self, key_types: Optional[dict] = None, lenient: bool = True):
        self._key_types = key_types or {}
        self._lenient   = lenient

    def _ktype(self, key: str) -> str:
        return self._key_types.get(key, TYPE_UNKNOWN)

    def parse(self, text: str) -> dict:
        lines = text.splitlines()
        result: dict = {}
        i = 0
        n = len(lines)

        while i < n:
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue

            m = re.match(r'^([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*)', line)
            if not m:
                i += 1
                continue

            key   = m.group(1)
            value = m.group(2).strip()
            ktype = self._ktype(key)

            if value and not value.startswith("#"):
                if ktype == TYPE_LIST:
                    result[key] = self._parse_scalar_as_list(value)
                elif ktype == TYPE_MAP:
                    result[key] = self._parse_scalar_as_map(key, value)
                else:
                    result[key] = self._clean_scalar(value)
                i += 1
                continue

            children_start = i + 1

            if ktype == TYPE_LIST:
                items, i = self._read_list(lines, children_start, n, key)
                if not items and self._lenient:
                    items, i = self._salvage_block(lines, children_start, n, key, expect_list=True)
                result[key] = items

            elif ktype in (TYPE_MAP, TYPE_MAP_OF_LISTS):
                mapping, i = self._read_map(lines, children_start, n, key, ktype)
                if not mapping and self._lenient:
                    mapping, i = self._salvage_block(lines, children_start, n, key, expect_list=False)
                result[key] = mapping

            elif key == IDLE_STATUS_KEY or ktype == TYPE_SUBMAP:
                submap, i = self._read_submap(lines, children_start, n)
                result[key] = submap

            elif ktype == TYPE_SCALAR:
                result[key] = None
                i += 1

            else:
                j = children_start
                while j < n and (not lines[j].strip() or lines[j].strip().startswith("#")):
                    j += 1

                if j < n and lines[j].startswith("  ") and not lines[j].strip().startswith("#"):
                    child = lines[j].strip()
                    if child.startswith("- ") or child == "-":
                        items, i = self._read_list(lines, children_start, n, key)
                        result[key] = items
                    else:
                        mapping, i = self._read_map(lines, children_start, n, key, TYPE_MAP)
                        result[key] = mapping
                elif self._lenient:
                    block, i = self._salvage_block(lines, children_start, n, key)
                    result[key] = block
                else:
                    result[key] = None
                    i += 1

        return result

    def _read_list(self, lines, start, n, parent_key) -> tuple:
        items = []
        i = start
        while i < n:
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue
            if not line.startswith(" "):
                break
            m = re.match(r'^\s+-\s+(.*)', line)
            if not m:
                break
            raw = m.group(1).strip()
            m2 = re.match(r'^([0-9]+)\s*(#.*)?$', raw)
            if m2:
                num, comment = m2.group(1), m2.group(2)
                entry_val = f"{num} {comment.strip()}" if comment else num
            else:
                entry_val = raw
            items.append(ConfigEntry(None, entry_val))
            i += 1
        return items, i

    def _salvage_block(
        self, lines, start: int, n: int, parent_key: str,
        expect_list: Optional[bool] = None
    ) -> tuple:
        map_entries:  dict = {}
        list_entries: list = []
        discarded:    int  = 0
        i = start

        while i < n:
            line     = lines[i]
            stripped = line.strip()

            if not stripped:
                i += 1
                continue

            if (not line.startswith(" ")
                    and re.match(r'^[A-Za-z][A-Za-z0-9_]*\s*:', line)):
                break

            if stripped.startswith("#"):
                i += 1
                continue

            clean_m = re.match(r'^[^A-Za-z0-9]*(\d.*|[A-Za-z].*)', stripped)
            clean = clean_m.group(1).strip() if clean_m else ""
            if not clean:
                i += 1
                continue

            m_map_num = re.match(r'^(\d+)\s*:\s*(\d+)\s*(#.*)?$', clean)
            if m_map_num:
                k, v, cmt = m_map_num.group(1), m_map_num.group(2), m_map_num.group(3)
                entry_val = f"{v} {cmt.strip()}" if cmt else v
                map_entries[k] = ConfigEntry(k, entry_val)
                i += 1
                continue

            m_map_str = re.match(r'^([\w]+)\s*:\s*(.+)$', clean)
            if m_map_str:
                k, v = m_map_str.group(1), m_map_str.group(2).strip()
                if expect_list is not True:
                    cleaned_v = self._clean_map_value(parent_key, v)
                    if cleaned_v is not None:
                        map_entries[k] = ConfigEntry(k, cleaned_v)
                        i += 1
                        continue

            m_list = re.match(r'^(?:-\s+)?(\d+)\s*(#.*)?$', clean)
            if m_list:
                num, cmt = m_list.group(1), m_list.group(2)
                entry_val = f"{num} {cmt.strip()}" if cmt else num
                if ":" not in clean:
                    if expect_list is not False and not map_entries:
                        list_entries.append(ConfigEntry(None, entry_val))
                    else:
                        warn(f"  {parent_key}: discarding orphaned value '{num}' (no key:value pair)")
                        discarded += 1
                i += 1
                continue

            warn(f"  {parent_key}: discarding unrecognized entry: '{stripped[:60]}'")
            discarded += 1
            i += 1

        total_salvaged = len(map_entries) + len(list_entries)
        if total_salvaged or discarded:
            if total_salvaged:
                info(f"  {parent_key}: salvaged {total_salvaged} entr{'y' if total_salvaged == 1 else 'ies'}"
                     f" from malformed block{f', discarded {discarded} invalid line(s)' if discarded else ''}")
            else:
                warn(f"  {parent_key}: could not salvage any valid entries ({discarded} invalid line(s) discarded)")

        if map_entries:
            return map_entries, i
        if list_entries:
            return list_entries, i
        return None, i

    def _read_map(self, lines, start, n, parent_key, ktype) -> tuple:
        result_map: dict = {}
        i = start
        while i < n:
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue
            if not line.startswith(" "):
                break
            mm = re.match(r'^\s+([^:]+):\s*(.*)', line)
            if not mm:
                i += 1
                continue
            k   = mm.group(1).strip()
            val = mm.group(2).strip()
            if not k:
                i += 1
                continue

            if ktype == TYPE_MAP_OF_LISTS:
                sub: list = []
                i += 1
                while i < n:
                    sub_line = lines[i]
                    if not sub_line.strip() or sub_line.strip().startswith("#"):
                        i += 1
                        continue
                    if not sub_line.startswith("    "):
                        break
                    m_sub = re.match(r'^\s+-\s+([0-9]+)\s*(#.*)?$', sub_line)
                    if m_sub:
                        num     = m_sub.group(1)
                        comment = m_sub.group(2)
                        raw_sub = f"{num} {comment.strip()}" if comment else num
                        sub.append(ConfigEntry(None, raw_sub))
                        i += 1
                    else:
                        break
                result_map[k] = sub if sub else None
            else:
                cleaned = self._clean_map_value(parent_key, val)
                if cleaned is not None:
                    result_map[k] = ConfigEntry(k, cleaned)
                i += 1

        return result_map, i

    def _read_submap(self, lines, start, n) -> tuple:
        sub: dict = {}
        i = start
        while i < n:
            line = lines[i]
            if not line.strip() or line.strip().startswith("#"):
                i += 1
                continue
            if not line.startswith(" "):
                break
            mm = re.match(r'^\s+([^:]+):\s*(.*)', line)
            if not mm:
                i += 1
                continue
            sub[mm.group(1).strip()] = mm.group(2).strip()
            i += 1
        return sub, i

    def _clean_scalar(self, val: str) -> str:
        val = val.strip()
        m = re.match(r'^([^#]+?)\s*(#.*)?$', val)
        if not m:
            return val
        v   = m.group(1).strip()
        cmt = m.group(2)
        return f"{v} {cmt.strip()}" if cmt else v

    def _clean_map_value(self, parent_key: str, val: str) -> Optional[str]:
        if not val or val.startswith("#"):
            return None
        m = re.match(r'^([^#\s]+)\s*(#.*)?$', val)
        if not m:
            return None
        v_part  = m.group(1).strip()
        comment = m.group(2)

        if parent_key in NUMERIC_VALUE_MAP_KEYS and not v_part.isdigit():
            warn(f"  {parent_key}: unexpected non-numeric value '{v_part}' — keeping as-is")

        if parent_key == "GameTitles":
            v_part = sanitize_title(v_part)

        return f"{v_part} {comment.strip()}" if comment else v_part

    def _parse_scalar_as_list(self, val: str) -> list:
        val = val.strip().strip("[]")
        result = []
        for part in val.split(","):
            part = part.strip()
            m    = re.match(r'^([0-9]+)\s*(#.*)?$', part)
            if m:
                num, comment = m.group(1), m.group(2)
                result.append(ConfigEntry(None, f"{num} {comment.strip()}" if comment else num))
        return result

    def _parse_scalar_as_map(self, parent_key: str, val: str) -> dict:
        val    = val.strip().strip("{}")
        result = {}
        for part in val.split(","):
            part = part.strip()
            mm   = re.match(r'^([^:]+):\s*(.*)', part)
            if not mm:
                continue
            k = mm.group(1).strip()
            v = mm.group(2).strip()
            if not k.isdigit():
                continue
            cleaned = self._clean_map_value(parent_key, v)
            if cleaned is not None:
                result[k] = ConfigEntry(k, cleaned)
        return result


# ──────────────────────────────────────────────────────────────
# Deduplication helpers
# ──────────────────────────────────────────────────────────────

def dedup_list(items: list) -> list:
    seen = {}
    for entry in items:
        num = entry.val.split()[0]
        seen[num] = entry
    return list(seen.values())


def dedup_map(mapping: dict) -> dict:
    return dict(mapping)


# ──────────────────────────────────────────────────────────────
# Steam name resolver (SteamCMD API with DLC Recognition)
# ──────────────────────────────────────────────────────────────

_app_details_cache: dict[str, tuple[str, Optional[str], Optional[list[str]]]] = {
    "480": ("Spacewar", None, [])
}

def get_app_details(appid: str) -> tuple[str, Optional[str], Optional[list[str]]]:
    """
    Query the SteamCMD API for an app's display name, parent app ID (if DLC), and any list of DLCs.
    Returns (name, parent_appid, list_of_dlcs).
    """
    if not appid:
        return "", None, None
    if appid in _app_details_cache:
        return _app_details_cache[appid]

    url = f"https://api.steamcmd.net/v1/info/{appid}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=STEAM_API_TIMEOUT) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("status") == "success":
                app_info = res.get("data", {}).get(appid, {})
                name = app_info.get("common", {}).get("name") or app_info.get("name")

                # Check if it's a DLC and get parent
                app_type = app_info.get("common", {}).get("type") or app_info.get("extended", {}).get("type")
                parent = app_info.get("common", {}).get("parent") or app_info.get("extended", {}).get("dlcforappid")

                is_dlc = (app_type and str(app_type).upper() == "DLC") or parent
                parent_id = str(parent) if parent else None

                dlc_list = None
                dlc_raw = app_info.get("extended", {}).get("listofdlc")
                if dlc_raw:
                    dlc_list = [d.strip() for d in str(dlc_raw).split(",") if d.strip()]

                if name:
                    _app_details_cache[appid] = (name, parent_id if is_dlc else None, dlc_list)
                    return _app_details_cache[appid]
    except Exception:
        pass

    _app_details_cache[appid] = ("", None, None)
    return "", None, None


def get_formatted_name(appid: str) -> str:
    """
    Get Fully formatted game name (including [DLC] tag and parent game name if applicable).
    """
    name, parent_id, _ = get_app_details(appid)
    if not name:
        return ""

    if parent_id:
        parent_name, _, _ = get_app_details(parent_id)
        if parent_name:
            return f"[DLC] {name} / {parent_name}"
        else:
            return f"[DLC] {name}"

    return name


def is_generic_or_missing_comment(raw_value: str) -> bool:
    if "#" not in raw_value:
        return True
    comment = raw_value.split("#", 1)[1].strip()
    if not comment:
        return True
    if re.match(r'^(App\s*\d+|unknown|untitled|placeholder)$', comment, re.IGNORECASE):
        return True
    return False


def resolve_missing_names(config_data: dict) -> None:
    tasks = []

    def collect(entries):
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, ConfigEntry) and is_generic_or_missing_comment(e.val):
                    tasks.append(e)
        elif isinstance(entries, dict):
            for v in entries.values():
                if isinstance(v, ConfigEntry) and is_generic_or_missing_comment(v.val):
                    tasks.append(v)

    for val in config_data.values():
        collect(val)

    if not tasks:
        return

    info(f"Resolving {len(tasks)} game name(s) in parallel via SteamCMD...")
    
    # Extract numeric AppIDs for lookups
    all_ids = set()
    for entry in tasks:
        raw_val = entry.val.split("#")[0].strip()
        if raw_val.isdigit():
            all_ids.add(raw_val)
        if entry.key and entry.key.isdigit():
            all_ids.add(entry.key)

    uncached = {aid for aid in all_ids if aid not in _app_details_cache}

    if uncached:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(get_app_details, aid): aid for aid in uncached}
            for fut in as_completed(futures):
                pass

    for entry in tasks:
        # Check if list entry or map entry
        raw_val = entry.val.split("#")[0].strip()
        if entry.key and entry.key.isdigit() and raw_val.isdigit():
            # Map entry (FakeAppIds AppId: FakeAppId)
            src_name = get_formatted_name(entry.key)
            tgt_name = get_formatted_name(raw_val)
            if src_name:
                comment = f"{src_name} → {tgt_name}" if tgt_name else src_name
                entry.val = f"{raw_val} # {comment}"
        elif raw_val.isdigit():
            # List entry (AdditionalApps etc.)
            name = get_formatted_name(raw_val)
            if name:
                entry.val = f"{raw_val} # {name}"


# ──────────────────────────────────────────────────────────────
# Depot Manifest Resolution
# ──────────────────────────────────────────────────────────────

def fetch_raw_app_info(appid: str) -> Optional[dict]:
    url = f"https://api.steamcmd.net/v1/info/{appid}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=STEAM_API_TIMEOUT) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("status") == "success":
                return res.get("data", {}).get(appid, {})
    except Exception:
        pass
    return None


def resolve_and_print_manifests(appid: str) -> None:
    if not appid or not appid.isdigit():
        error("Invalid AppID.")
        return

    info(f"Retrieving app info for {appid}...")
    app_info = fetch_raw_app_info(appid)
    if not app_info:
        error(f"Failed to retrieve data for AppID {appid}.")
        return

    game_name = app_info.get("common", {}).get("name") or app_info.get("name") or f"App {appid}"
    get_app_details(appid)  # Cache details

    depots = app_info.get("depots", {})
    manifest_lines = []

    def process_depots_dict(depots_dict: dict, label: str):
        for dep_id, dep_data in depots_dict.items():
            if not dep_id.isdigit() or not isinstance(dep_data, dict):
                continue
            manifests = dep_data.get("manifests", {})
            public_manifest = manifests.get("public", {})
            if isinstance(public_manifest, dict) and "gid" in public_manifest:
                gid = public_manifest["gid"]
                dep_name = dep_data.get("name")
                oslist = dep_data.get("config", {}).get("oslist", "")

                dlc_appid = dep_data.get("dlcappid")
                dep_label = label
                if dlc_appid:
                    dlc_name = get_formatted_name(str(dlc_appid))
                    if dlc_name:
                        dep_label = dlc_name
                    else:
                        dep_label = f"[DLC] App {dlc_appid} / {label}"

                comment = f"{dep_label}"
                if dep_name:
                    comment += f" - {dep_name}"
                else:
                    comment += f" - Depot {dep_id}"
                if oslist:
                    comment += f" ({oslist})"
                manifest_lines.append(f"  {dep_id}: {gid} # {comment}")

    process_depots_dict(depots, game_name)

    dlc_raw = app_info.get("extended", {}).get("listofdlc")
    if dlc_raw:
        dlc_ids = [d.strip() for d in str(dlc_raw).split(",") if d.strip()]
        if dlc_ids:
            info(f"Found {len(dlc_ids)} DLC(s) for this app. Fetching DLC depots...")
            with ThreadPoolExecutor(max_workers=8) as ex:
                futures = {ex.submit(get_app_details, aid): aid for aid in dlc_ids}
                for f in as_completed(futures):
                    pass

            for dlc_id in sorted(dlc_ids, key=int):
                dlc_name, _, _ = get_app_details(dlc_id)
                if not dlc_name:
                    continue
                dlc_app_info = fetch_raw_app_info(dlc_id)
                if dlc_app_info:
                    dlc_depots = dlc_app_info.get("depots", {})
                    process_depots_dict(dlc_depots, get_formatted_name(dlc_id) or f"[DLC] {dlc_name} / {game_name}")

    print()
    print(f"{BOLD}{'─'*60}{RESET}")
    print(f"{BOLD}ManifestIds mapping for {game_name} (AppID {appid}):{RESET}")
    print(f"{BOLD}{'─'*60}{RESET}")
    print("ManifestIds:")
    if manifest_lines:
        for line in manifest_lines:
            print(line)
    else:
        print("  # No public manifests or depots found.")
    print(f"{BOLD}{'─'*60}{RESET}")
    print()


# ──────────────────────────────────────────────────────────────
# Config validator
# ──────────────────────────────────────────────────────────────

def validate_config(config_path: Path, key_types: dict) -> list:
    issues = []
    text   = config_path.read_text(encoding="utf-8")
    lines  = text.splitlines()

    for lineno, line in enumerate(lines, 1):
        if line != line.rstrip():
            issues.append(f"Line {lineno}: trailing whitespace")
        if "  #" in line:
            parts = line.split("  #", 1)
            if parts[0].rstrip() != parts[0]:
                issues.append(f"Line {lineno}: multiple spaces before inline comment")

    reader = SimpleYAMLReader(key_types, lenient=True)
    try:
        data = reader.parse(text)
    except Exception as exc:
        issues.append(f"Parse error: {exc}")
        return issues

    for key in data:
        if key_types and key not in key_types and key != IDLE_STATUS_KEY:
            issues.append(f"Key '{key}' not found in current upstream template (may have been removed)")

    return issues


# ──────────────────────────────────────────────────────────────
# Config merger
# ──────────────────────────────────────────────────────────────

def _render_list(items: list, indent: str = "  ") -> str:
    return "\n".join(f"{indent}- {e.val}" for e in dedup_list(items))


def _render_map(mapping: dict, parent_key: str, indent: str = "  ") -> str:
    out = []
    for k, v in dedup_map(mapping).items():
        if isinstance(v, list):
            out.append(f"{indent}{k}:")
            for sub in v:
                out.append(f"{indent}  - {sub.val}")
        elif isinstance(v, ConfigEntry):
            out.append(f"{indent}{k}: {v.val}")
    return "\n".join(out)


def _render_submap(submap: dict, indent: str = "  ") -> str:
    out = []
    for k, v in submap.items():
        val_str = v.val if isinstance(v, ConfigEntry) else str(v)
        out.append(f"{indent}{k}: {val_str}")
    return "\n".join(out)


def merge_config(template_yaml: str, user_data: dict, key_types: dict) -> str:
    out_lines = []
    lines = template_yaml.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            out_lines.append(line.rstrip())
            i += 1
            continue

        m = re.match(r'^([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*)', line)
        if not m:
            out_lines.append(line.rstrip())
            i += 1
            continue

        key   = m.group(1)
        value = m.group(2).strip()
        ktype = key_types.get(key, TYPE_UNKNOWN)

        if value and not value.startswith("#"):
            user_val = user_data.get(key)
            if isinstance(user_val, str) and user_val:
                uv = user_val.strip()
                mv = re.match(r'^([^#]+?)\s*(#.*)?$', uv)
                if mv:
                    clean_val = mv.group(1).strip()
                    cmt       = mv.group(2)
                    uv = f"{clean_val} {cmt.strip()}" if cmt else clean_val
                out_lines.append(f"{key}: {uv}")
            else:
                out_lines.append(f"{key}: {value}")
            i += 1
            continue

        out_lines.append(f"{key}:")

        i += 1
        while i < n and lines[i].startswith("  "):
            i += 1

        user_val = user_data.get(key)

        if key == IDLE_STATUS_KEY or ktype == TYPE_SUBMAP:
            if isinstance(user_val, dict) and user_val:
                out_lines.append(_render_submap(user_val))

        elif ktype == TYPE_LIST or (ktype == TYPE_UNKNOWN and isinstance(user_val, list)):
            if isinstance(user_val, list) and user_val:
                out_lines.append(_render_list(user_val))

        elif ktype in (TYPE_MAP, TYPE_MAP_OF_LISTS) or (ktype == TYPE_UNKNOWN and isinstance(user_val, dict)):
            if isinstance(user_val, dict) and user_val:
                out_lines.append(_render_map(user_val, key))

    return "\n".join(out_lines) + "\n"


# ──────────────────────────────────────────────────────────────
# Backup helpers
# ──────────────────────────────────────────────────────────────

def make_backup_with_rotation(config_path: Path) -> Path:
    bak_base = config_path.with_name(config_path.name + ".bak")
    if not bak_base.exists():
        shutil.copy2(config_path, bak_base)
        return bak_base
    i = 2
    while True:
        bak_rot = config_path.with_name(config_path.name + f".bak{i}")
        if not bak_rot.exists():
            shutil.copy2(config_path, bak_rot)
            return bak_rot
        i += 1


def get_latest_backup_path(config_path: Path) -> Optional[Path]:
    parent = config_path.parent
    name = config_path.name
    backups = []
    
    bak_base = parent / (name + ".bak")
    if bak_base.exists():
        backups.append(bak_base)
        
    for p in parent.glob(name + ".bak*"):
        if p.exists() and p != bak_base:
            backups.append(p)
            
    if not backups:
        return None
        
    backups.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return backups[0]


def restore_latest_backup(config_path: Path) -> tuple[bool, str, Optional[Path]]:
    try:
        bak_path = get_latest_backup_path(config_path)
        if not bak_path:
            return False, "No backup file found to restore.", None
            
        shutil.copy2(bak_path, config_path)
        return True, f"Successfully restored config from backup:\n{bak_path.name}", bak_path
    except Exception as e:
        return False, f"Failed to restore backup: {e}", None


# ──────────────────────────────────────────────────────────────
# GUI integration API
# ──────────────────────────────────────────────────────────────

def run_boot_config_check() -> None:
    global boot_status, boot_issues
    if boot_status is not None and boot_status != "checking":
        return

    boot_status = "checking"
    try:
        config_path = DEFAULT_CONFIG_PATH
        if not config_path.exists():
            boot_status = "no_config"
            return

        new_keys = set()
        issues = []
        try:
            raw_hpp = _fetch_url(TEMPLATE_SOURCE_URL, TEMPLATE_TIMEOUT)
            key_types = infer_key_types(raw_hpp)
            m_tmpl = re.search(r'static const char\* defaultConfig = R"\((.+?)\)";', raw_hpp, re.DOTALL)
            if not m_tmpl:
                raise ValueError("Template syntax changed.")
            template_yaml = m_tmpl.group(1)

            config_text = config_path.read_text(encoding="utf-8")
            reader = SimpleYAMLReader(key_types, lenient=True)
            old_data = reader.parse(config_text)
            
            tmpl_reader = SimpleYAMLReader(key_types, lenient=False)
            template_data = tmpl_reader.parse(template_yaml)

            issues = validate_config(config_path, key_types)
            new_keys = set(template_data) - set(old_data)
        except Exception as net_err:
            boot_issues = [f"[Network] Could not fetch template: {net_err}"]
            issues = validate_config(config_path, {})
            if issues:
                boot_status = "needs_fix"
                boot_issues = issues[:]
            else:
                boot_status = "optimal"
            return

        if issues or new_keys:
            boot_status = "needs_fix"
            boot_issues = issues[:]
            if new_keys:
                boot_issues.append(f"Missing {len(new_keys)} upstream default key(s).")
        else:
            boot_status = "optimal"
    except Exception as e:
        boot_status = "failed"
        boot_issues = [str(e)]


def run_asshead_migration(config_path: Path, template_url: str = TEMPLATE_SOURCE_URL) -> tuple[bool, str, Optional[Path]]:
    try:
        if not config_path.exists():
            return False, f"Config file not found at {config_path}", None

        raw_hpp = _fetch_url(template_url, TEMPLATE_TIMEOUT)
        key_types = infer_key_types(raw_hpp)

        m = re.search(r'static const char\* defaultConfig = R"\((.+?)\)";', raw_hpp, re.DOTALL)
        template_yaml = m.group(1) if m else ""

        config_text = config_path.read_text(encoding="utf-8")
        reader = SimpleYAMLReader(key_types, lenient=True)
        old_data = reader.parse(config_text)
        
        tmpl_reader = SimpleYAMLReader(key_types, lenient=False)
        template_data = tmpl_reader.parse(template_yaml)

        issues = validate_config(config_path, key_types)
        new_keys = set(template_data) - set(old_data)

        if not issues and not new_keys:
            return True, "No changes needed. SLSsteam config is already optimal!", None

        resolve_missing_names(old_data)

        merged = merge_config(template_yaml, old_data, key_types)
        bak_path = make_backup_with_rotation(config_path)
        config_path.write_text(merged, encoding="utf-8")

        msg = "Successfully updated config.yaml to the latest template."
        if new_keys:
            msg += f" Added {len(new_keys)} new key(s)."
        if issues:
            msg += f" Fixed {len(issues)} formatting issue(s)."

        return True, msg, bak_path

    except Exception as e:
        return False, f"Error: {e}", None


# ──────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent("""\
            SLSsteam config.yaml cleanup & update tool
            ==========================================
            Fetches the latest upstream template and carries over all your
            personal values into the new structure, fixing formatting issues
            and adding any new keys with their default values.

            Key types are inferred automatically from the template — no manual
            updates needed when the SLSsteam developer adds or removes keys.
        """),
        epilog=textwrap.dedent("""\
            Examples:
              Preview the result without writing:
                python3 assfixer.py --dry-run

              Update in place (auto-backup created):
                python3 assfixer.py

              Write to a different output file:
                python3 assfixer.py --output /tmp/config_new.yaml

              Skip outbound name lookups:
                python3 assfixer.py --no-resolve-names

              Query manifest/depot mapping for AppID 480:
                python3 assfixer.py --manifests 480
        """),
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH,
        help=f"Path to config.yaml (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output path (default: overwrites --config after backup)",
    )
    parser.add_argument("--dry-run",           action="store_true",
                        help="Print merged config to stdout, don't write to disk")
    parser.add_argument("--no-backup",         action="store_true",
                        help="Skip creating a .bak backup before writing")
    parser.add_argument("--validate-only",     action="store_true",
                        help="Only check for errors, don't merge or write")
    parser.add_argument("--no-resolve-names",  action="store_true",
                        help="Skip outbound SteamCMD API calls for game name resolution")
    parser.add_argument(
        "--template-url", default=TEMPLATE_SOURCE_URL,
        help="Override the GitHub URL for the default config template",
    )
    parser.add_argument(
        "--manifests", "-m", type=str, default=None,
        help="Query SteamCMD API for an AppID's depot manifests, and print them as a YAML snippet"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    args = parser.parse_args()

    if args.manifests:
        resolve_and_print_manifests(args.manifests)
        return

    interactive = (
        args.config        == DEFAULT_CONFIG_PATH
        and args.output    is None
        and not args.dry_run
        and not args.no_backup
        and not args.validate_only
        and not args.no_resolve_names
        and args.template_url == TEMPLATE_SOURCE_URL
        and args.manifests    is None
    )

    print()
    print(f"{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}   SLSsteam Config Cleanup & Update Tool  v{VERSION}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    print()

    config_path: Path = args.config
    choice            = 1

    if interactive:
        print("Please select an option:")
        print("  1) Clean & Update Config")
        print("  2) Restore Backup  (from config.bak)")
        print("  3) Apply Steam Deck Recommended Settings")
        print("  4) Resolve Manifests/Depots for a Game")
        while True:
            try:
                user_choice = input("Enter choice (1-4, default: 1): ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled.")
                sys.exit(0)
            if not user_choice:
                choice = 1
                break
            if user_choice in ("1", "2", "3", "4"):
                choice = int(user_choice)
                break
            print("Invalid choice. Please enter 1, 2, 3, or 4.")
        print()

    # ── Option 2: Restore backup ─────────────────────────────────
    if interactive and choice == 2:
        print(f"Default config location: {DEFAULT_CONFIG_PATH}")
        while True:
            try:
                user_input = input("Enter path to config.yaml (press Enter for default): ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled.")
                sys.exit(0)
            config_path = (
                Path(user_input).expanduser().resolve() if user_input else DEFAULT_CONFIG_PATH
            )
            bak_path = config_path.with_name(config_path.name + ".bak")
            if bak_path.exists():
                break
            error(f"Backup file not found: {bak_path}. Please try again.")
            print()

        info(f"Restoring {bak_path} → {config_path} …")
        try:
            shutil.copy2(bak_path, config_path)
            ok("Backup successfully restored.")
        except Exception as exc:
            error(f"Failed to restore backup: {exc}")
            sys.exit(1)
        print()
        return

    # ── Option 4: Resolve Manifests/Depots for a Game ────────────
    if interactive and choice == 4:
        try:
            appid_input = input("Enter Steam AppID: ").strip()
            if appid_input:
                resolve_and_print_manifests(appid_input)
            else:
                error("No AppID provided.")
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
        print()
        return

    # ── Resolve config path ───────────────────────────────────────
    if interactive:
        print(f"Default config location: {DEFAULT_CONFIG_PATH}")
        while True:
            try:
                user_input = input("Enter path to config.yaml (press Enter for default): ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled.")
                sys.exit(0)
            config_path = (
                Path(user_input).expanduser().resolve() if user_input else DEFAULT_CONFIG_PATH
            )
            if config_path.exists():
                break
            error(f"Config file not found: {config_path}. Please try again.")
            print()
    else:
        if not config_path.exists():
            error(f"Config file not found: {config_path}")
            error("Make sure SLSsteam has been run at least once.")
            sys.exit(1)

    info(f"Config file: {config_path}")
    print()

    # ── Fetch template and infer key types ───────────────────────
    print(f"{BOLD}[0/4] Fetching and analysing upstream template…{RESET}")
    try:
        raw_hpp = _fetch_url(args.template_url, TEMPLATE_TIMEOUT)
    except Exception as exc:
        error(f"Failed to fetch template: {exc}")
        sys.exit(1)

    key_types = infer_key_types(raw_hpp)

    m_tmpl = re.search(r'static const char\* defaultConfig = R"\((.+?)\)";', raw_hpp, re.DOTALL)
    if not m_tmpl:
        error("Could not find YAML template in the downloaded C++ file.")
        sys.exit(1)
    template_yaml = m_tmpl.group(1)

    ok(f"  Discovered {len(key_types)} top-level key(s) in upstream template.")
    unknown = [k for k, t in key_types.items() if t == TYPE_UNKNOWN]
    if unknown:
        info(f"  Keys with auto-detected type (from your config): {', '.join(unknown)}")
    print()

    # ── 1. Validate ──────────────────────────────────────────────
    print(f"{BOLD}[1/4] Validating existing config…{RESET}")
    issues = validate_config(config_path, key_types)
    if issues:
        print(f"  Found {len(issues)} issue(s):")
        for issue in issues:
            warn(f"  {issue}")
    else:
        ok("  No formatting issues detected.")

    if args.validate_only:
        print()
        if issues:
            print(f"{YELLOW}Validation complete — {len(issues)} issue(s) found.{RESET}")
            print("Run without --validate-only to auto-fix and update.")
        else:
            print(f"{GREEN}Validation complete — config looks good!{RESET}")
        return
    print()

    # ── 2. Parse old config ──────────────────────────────────────
    print(f"{BOLD}[2/4] Parsing existing config…{RESET}")
    config_text = config_path.read_text(encoding="utf-8")
    reader      = SimpleYAMLReader(key_types)
    old_data    = reader.parse(config_text)

    for section, val in old_data.items():
        if val is None:
            ok(f"  {section}: (empty)")
        elif isinstance(val, str):
            ok(f"  {section}: {val}")
        elif isinstance(val, (list, dict)):
            ok(f"  {section}: {len(val)} entries")

    if not args.no_resolve_names:
        resolve_missing_names(old_data)
    print()

    # ── 2.5 Steam Deck preset ────────────────────────────────────
    if interactive and choice == 3:
        info("Applying Steam Deck recommended overrides:")
        overrides = {
            "SafeMode":        "yes",
            "Notifications":   "yes",
            "LogLevel":        "2",
            "ExtendedLogging": "no",
        }
        for k, v in overrides.items():
            info(f"  {k}: {v}")
            old_data[k] = v
        print()

    # ── 3. Compare ───────────────────────────────────────────────
    print(f"{BOLD}[3/4] Comparing with upstream template…{RESET}")
    template_data = reader.parse(template_yaml)
    new_keys      = set(template_data) - set(old_data)
    removed_keys  = set(old_data)      - set(template_data)
    if new_keys:
        info(f"  New upstream key(s) — using defaults: {', '.join(sorted(new_keys))}")
    if removed_keys:
        warn(f"  Key(s) not in template (dropped): {', '.join(sorted(removed_keys))}")
    if not new_keys and not removed_keys:
        ok("  Your config is in sync with the upstream template.")
    print()

    # ── 4. Merge ─────────────────────────────────────────────────
    print(f"{BOLD}[4/4] Merging your values into new template…{RESET}")
    merged = merge_config(template_yaml, old_data, key_types)

    for key in key_types:
        old_val = old_data.get(key)
        if isinstance(old_val, dict):
            before, after = len(old_val), len(dedup_map(old_val))
        elif isinstance(old_val, list):
            before, after = len(old_val), len(dedup_list(old_val))
        else:
            continue
        if before != after:
            info(f"  {key}: deduplicated {before} → {after} entries")

    ok("  Merge complete.")
    print()

    # ── Output ───────────────────────────────────────────────────
    if args.dry_run:
        print(f"{BOLD}{'─'*60}{RESET}")
        print(f"{BOLD}DRY RUN — merged config (not written to disk):{RESET}")
        print(f"{BOLD}{'─'*60}{RESET}")
        print(merged)
        return

    out_path: Path = args.output or config_path

    if out_path == config_path and merged == config_text:
        ok("No changes detected. Config is already optimal!")
        print()
        return

    if not args.no_backup:
        bak_path = make_backup_with_rotation(config_path)
        ok(f"Backup created: {bak_path}")

    out_path.write_text(merged, encoding="utf-8")
    ok(f"Config written:  {out_path}")
    print()

    if issues:
        print(f"{YELLOW}Fixed {len(issues)} formatting issue(s) from your old config.{RESET}")
    if new_keys:
        print(f"{CYAN}Added {len(new_keys)} new upstream key(s) with defaults.{RESET}")
    if removed_keys:
        print(f"{YELLOW}Dropped {len(removed_keys)} key(s) no longer in template.{RESET}")

    print()
    print(f"{GREEN}{BOLD}Done! Your config.yaml has been cleaned up and updated.{RESET}")
    print()


if __name__ == "__main__":
    main()
