#!/usr/bin/env python3
"""
SLSsteam config.yaml cleanup & update tool
==========================================
Fetches the latest default config template from the SLSsteam GitHub repo,
parses your existing config.yaml, and produces a perfectly-formatted output
that carries over ALL your personal values into the new template structure.

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
    python3 slssteam_config.py [OPTIONS]

Options:
    --config PATH         Path to config.yaml (default: auto-detected)
    --output PATH         Where to write the result (default: overwrites --config after backup)
    --dry-run             Print result to stdout, don't write to disk
    --no-backup           Skip creating a .bck backup before writing
    --validate-only       Only check for errors in your current config, don't write
    --no-resolve-names    Skip outbound SteamCMD API calls for game name resolution
    --template-url URL    Override the GitHub raw URL for the default config template
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

VERSION = "2.0.0"

boot_status = None
boot_issues = []

def fetch_and_apply_rules() -> bool:
    """Fetches latest rules JSON from ASSfixer repo and updates validation keys."""
    global SCALAR_KEYS, BOOLEAN_KEYS, LIST_KEYS, MAP_KEYS, MAP_OF_LIST_KEYS, KNOWN_KEYS
    rules_url = "https://raw.githubusercontent.com/niwia/ASSfixer/main/asshead_rules.json"
    try:
        req = urllib.request.Request(rules_url, headers={"User-Agent": "ASSella-Fixer"})
        with urllib.request.urlopen(req, timeout=8) as response:
            rules_data = json.loads(response.read().decode("utf-8"))
            
            if "SCALAR_KEYS" in rules_data:
                SCALAR_KEYS.clear()
                SCALAR_KEYS.update(rules_data["SCALAR_KEYS"])
                
            if "BOOLEAN_KEYS" in rules_data:
                BOOLEAN_KEYS.clear()
                BOOLEAN_KEYS.update(rules_data["BOOLEAN_KEYS"])
                
            if "LIST_KEYS" in rules_data:
                LIST_KEYS.clear()
                LIST_KEYS.update(rules_data["LIST_KEYS"])
                
            if "MAP_KEYS" in rules_data:
                MAP_KEYS.clear()
                MAP_KEYS.update(rules_data["MAP_KEYS"])
                
            if "MAP_OF_LIST_KEYS" in rules_data:
                MAP_OF_LIST_KEYS.clear()
                MAP_OF_LIST_KEYS.update(rules_data["MAP_OF_LIST_KEYS"])
                
            KNOWN_KEYS = (
                SCALAR_KEYS | LIST_KEYS | MAP_KEYS | MAP_OF_LIST_KEYS
                | {IDLE_STATUS_KEY, "UnownedStatus"}
            )
            return True
    except Exception as e:
        # Fall back to local rules (hardcoded below)
        return False

def get_latest_backup_path(config_path: Path) -> Optional[Path]:
    """
    Returns the path to the most recent backup file (config.yaml.bak*)
    sorted by modification time (newest first).
    """
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
    """
    Restores the newest backup over the config.yaml file.
    Returns (success, message, restored_backup_path).
    """
    try:
        bak_path = get_latest_backup_path(config_path)
        if not bak_path:
            return False, "No backup file found to restore.", None
            
        shutil.copy2(bak_path, config_path)
        return True, f"Successfully restored config from backup:\n{bak_path.name}", bak_path
    except Exception as e:
        return False, f"Failed to restore backup: {e}", None

def run_boot_config_check() -> None:
    """
    Checks the SLSsteam config once in a background thread and updates global status.
    Uses DEFAULT_CONFIG_PATH.
    """
    global boot_status, boot_issues, KNOWN_KEYS, SCALAR_KEYS, LIST_KEYS, MAP_KEYS, BOOLEAN_KEYS
    if boot_status is not None and boot_status != "checking":
        return  # Already run or running

    boot_status = "checking"
    try:
        # Fetch dynamic rules first
        fetch_and_apply_rules()

        config_path = DEFAULT_CONFIG_PATH
        if not config_path.exists():
            boot_status = "no_config"
            return

        # Validate local config structure first
        issues = validate_config(config_path)

        # Try to download template to check for missing upstream keys.
        new_keys = set()
        try:
            template_yaml = fetch_template(TEMPLATE_SOURCE_URL)
            reader = SimpleYAMLReader()
            old_data = reader.parse(config_path.read_text(encoding="utf-8"))
            template_data = reader.parse(template_yaml)
            # Dynamically incorporate any new template keys into KNOWN_KEYS and SCALAR_KEYS
            for tk, tv in template_data.items():
                if tk not in KNOWN_KEYS:
                    if isinstance(tv, list):
                        LIST_KEYS.add(tk)
                    elif isinstance(tv, dict):
                        MAP_KEYS.add(tk)
                    else:
                        SCALAR_KEYS.add(tk)
                        BOOLEAN_KEYS.add(tk)
            KNOWN_KEYS = (
                SCALAR_KEYS | LIST_KEYS | MAP_KEYS | MAP_OF_LIST_KEYS
                | {IDLE_STATUS_KEY, "UnownedStatus"}
            )
            # Re-validate config with updated KNOWN_KEYS
            issues = validate_config(config_path)
            new_keys = set(template_data) - set(old_data)
        except Exception as net_err:
            # Network unavailable or GitHub unreachable — skip upstream key check
            boot_issues = [f"[Network] Could not fetch template: {net_err}"]
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
# Key-set constants  (defined here, before SimpleYAMLReader)
# ──────────────────────────────────────────────────────────────

SCALAR_KEYS = {
    "DisableFamilyShareLock", "UseWhitelist", "AutoFilterList",
    "PlayNotOwnedGames", "SafeMode", "Notifications", "WarnHashMissmatch",
    "NotifyInit", "API", "DisableCloud", "FakeEmail", "FakeWalletBalance",
    "LogLevel", "ExtendedLogging", "MaxSchemaTries", "DisableUpdates",
    "DumpClientInterfaces",
}
# All scalar keys that take yes/no values
BOOLEAN_KEYS = SCALAR_KEYS - {"FakeEmail", "FakeWalletBalance", "LogLevel"}

LIST_KEYS        = {"AppIds", "AdditionalApps", "FakeOffline", "DepotBlacklist"}
MAP_KEYS         = {"AppTokens", "FakeAppIds", "GameTitles", "SubscriptionTimestamps", "DlcData", "ManifestIds"}
MAP_OF_LIST_KEYS = {"DenuvoGames"}
IDLE_STATUS_KEY  = "IdleStatus"

KNOWN_KEYS = (
    SCALAR_KEYS | LIST_KEYS | MAP_KEYS | MAP_OF_LIST_KEYS
    | {IDLE_STATUS_KEY, "UnownedStatus"}
)

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

def normalize_comment_spacing(raw: str) -> str:
    """
    Normalize whitespace between a value and its trailing inline comment.

    '480   # Webfishing'  →  '480 # Webfishing'
    Quoted strings are left untouched.
    """
    s = raw.strip()
    if not s or s.startswith('"') or s.startswith("'"):
        return s
    m = re.match(r'^(\S+)\s+(#.*)$', s)
    return f"{m.group(1)} {m.group(2)}" if m else s


def bare_value(raw: str) -> str:
    """Return only the value part (no inline comment) for deduplication."""
    s = raw.strip()
    if s.startswith('"') or s.startswith("'"):
        return s
    m = re.match(r'^(\S+)\s*(?:#.*)?$', s)
    return m.group(1) if m else s


def sanitize_title(title: str) -> str:
    """Ensure a Title/String value is properly double-quoted."""
    title = title.strip()
    if not title:
        return '""'
    if title.startswith('"') and title.endswith('"') and len(title) >= 2:
        return title
    if title.startswith("'") and title.endswith("'") and len(title) >= 2:
        return title
    cleaned = title.strip('"').strip("'").strip()
    return '""' if cleaned in ("", ";") else f'"{cleaned}"'


def sanitize_boolean(val: str, default: str) -> str:
    """Normalize boolean strings to 'yes' or 'no'."""
    v = (val or "").strip().lower()
    if v in ("yes", "true", "1", "y", "on"):
        return "yes"
    if v in ("no", "false", "0", "n", "off"):
        return "no"
    return default


def sanitize_log_level(val: str, default: str) -> str:
    """LogLevel must be an integer 0–6."""
    v = (val or "").strip()
    return v if v.isdigit() and 0 <= int(v) <= 6 else default


def sanitize_wallet_balance(val: str, default: str) -> str:
    """FakeWalletBalance must be a non-negative integer."""
    v = (val or "").strip()
    return v if v.isdigit() else default


# ──────────────────────────────────────────────────────────────
# ConfigEntry  (dataclass replaces manual __slots__)
# ──────────────────────────────────────────────────────────────

@dataclass
class ConfigEntry:
    """
    One parsed entry from a raw list or mapping line.

    list item:  '  - 237990 # The Banner Saga'
      → key = None,     raw_value = '237990 # The Banner Saga'

    map item:   '  3146520: 480 # Webfishing'
      → key = '3146520', raw_value = '480 # Webfishing'
    """
    key:       str | None
    raw_value: str

    def __post_init__(self):
        self.raw_value = normalize_comment_spacing(self.raw_value)

    @property
    def dedup_key(self) -> str:
        """Value without inline comment — used for list deduplication."""
        return bare_value(self.raw_value)


# ──────────────────────────────────────────────────────────────
# Config parser  (zero external dependencies)
# ──────────────────────────────────────────────────────────────

class SimpleYAMLReader:
    """
    Parses the specific YAML structure that SLSsteam's config.yaml uses.

    Returns a dict mapping top-level key names to one of:
      None                       – empty key (no children)
      str                        – scalar
      list[ConfigEntry]          – list    (AppIds, AdditionalApps, FakeOffline)
      dict[str, ConfigEntry]     – map     (FakeAppIds, AppTokens, GameTitles, …)
      dict[str, list[ConfigEntry]] – map-of-lists  (DenuvoGames)
      dict[str, str]             – IdleStatus / UnownedStatus sub-map
    """

    # ── Top-level parse ──────────────────────────────────────────
    def parse(self, text: str) -> dict:
        lines = text.splitlines()
        data: dict = {}
        i = 0
        n = len(lines)

        while i < n:
            line     = lines[i]
            stripped = line.strip()

            if not stripped or stripped.startswith("#"):
                i += 1
                continue

            m = re.match(r'^([A-Za-z][A-Za-z0-9_]*):\s*(.*)', line)
            if not m:
                i += 1
                continue

            key  = m.group(1)
            rest = m.group(2).strip()

            rest_clean = self._strip_scalar_comment(rest)

            if rest_clean:
                # Value is on the same line as the key
                if key in LIST_KEYS:
                    data[key] = self._parse_scalar_as_list(rest)
                elif key in MAP_KEYS or key in MAP_OF_LIST_KEYS:
                    data[key] = self._parse_scalar_as_map(key, rest)
                elif key in (IDLE_STATUS_KEY, "UnownedStatus"):
                    data[key] = self._parse_scalar_as_submap(rest)
                else:
                    data[key] = rest_clean
                i += 1
            else:
                # Collect indented child lines
                i += 1
                children = []
                while i < n:
                    child = lines[i]
                    cs    = child.strip()
                    if not cs:
                        break
                    if cs.startswith("#"):
                        i += 1
                        continue
                    m_top = re.match(r'^([A-Za-z][A-Za-z0-9_]*):\s*(.*)', child)
                    if m_top and m_top.group(1) in KNOWN_KEYS:
                        break
                    children.append(child)
                    i += 1

                data[key] = self._parse_children(key, children) if children else None

        return data

    # ── Scalar comment stripper ──────────────────────────────────
    def _strip_scalar_comment(self, s: str) -> str:
        if not s or s.startswith('"') or s.startswith("'"):
            return s
        idx = s.find(" #")
        return s[:idx].strip() if idx >= 0 else s.strip()

    # ── Child block dispatcher ───────────────────────────────────
    def _parse_children(self, parent_key: str, lines: list) -> object:
        """Route child lines to the appropriate sub-parser."""
        stripped = [l.strip() for l in lines]
        if parent_key in LIST_KEYS:
            return self._parse_list_children(stripped)
        if parent_key in (IDLE_STATUS_KEY, "UnownedStatus"):
            return self._parse_submap_children(stripped)
        return self._parse_map_children(parent_key, lines, stripped)

    # ── List children ────────────────────────────────────────────
    def _parse_list_children(self, stripped: list[str]) -> list:
        """Parse '  - <appid> [# comment]' lines into ConfigEntry list."""
        result = []
        for s in stripped:
            if s.startswith("- "):
                val = s[2:].strip()
            elif s == "-":
                continue          # bare dash with no value → skip
            else:
                val = s.strip()   # non-dash line: try anyway

            if not val:
                continue
            m = re.match(r'^([0-9]+)\s*(#.*)?$', val)
            if m:
                num     = m.group(1)
                comment = m.group(2)
                raw_val = f"{num} {comment.strip()}" if comment else num
                result.append(ConfigEntry(None, raw_val))
        return result

    # ── IdleStatus / UnownedStatus children ──────────────────────
    def _parse_submap_children(self, stripped: list[str]) -> dict:
        """Parse the two-field AppId/Title sub-map."""
        result = {}
        for s in stripped:
            mm = re.match(r'^([A-Za-z][A-Za-z0-9_]*):\s*(.*)', s)
            if not mm:
                continue
            k   = mm.group(1)
            val = mm.group(2).strip()
            if k == "AppId":
                result[k] = val if val.isdigit() else "0"
            elif k == "Title":
                result[k] = sanitize_title(val)
        result.setdefault("AppId", "0")
        result.setdefault("Title", '""')
        return result

    # ── Mapping children (MAP_KEYS + MAP_OF_LIST_KEYS) ──────────
    def _parse_map_children(
        self, parent_key: str, lines: list[str], stripped: list[str]
    ) -> dict:
        """
        Parse '<appid>: <value>' lines, and optionally sub-lists under a bare key.
        Non-numeric keys are warned about and skipped.
        """
        result_map: dict = {}
        i = 0
        while i < len(lines):
            s  = stripped[i]
            mm = re.match(r'^([^:]+):\s*(.*)', s)
            if not mm:
                i += 1
                continue

            k   = mm.group(1).strip()
            val = mm.group(2).strip()

            if not k.isdigit():
                warn(
                    f"Skipping non-numeric map key '{k}' under '{parent_key}'"
                    f" — expected a Steam App ID; check your config for typos"
                )
                i += 1
                continue

            if val:
                cleaned = self._clean_map_value(parent_key, val)
                if cleaned:
                    result_map[k] = ConfigEntry(k, cleaned)
                i += 1
            else:
                # Bare key → look ahead for '  - <appid>' sub-list
                i += 1
                sub = []
                while i < len(lines):
                    ss = stripped[i]
                    if ss.startswith("- "):
                        sub_val = ss[2:].strip()
                        m_sub   = re.match(r'^([0-9]+)\s*(#.*)?$', sub_val)
                        if m_sub:
                            num     = m_sub.group(1)
                            comment = m_sub.group(2)
                            raw_sub = f"{num} {comment.strip()}" if comment else num
                            sub.append(ConfigEntry(None, raw_sub))
                        i += 1
                    else:
                        break
                result_map[k] = sub if sub else None

        return result_map

    def _clean_map_value(self, parent_key: str, val: str) -> str:
        """Sanitize a mapping entry's value string; return '' to discard."""
        m = re.match(r'^([^#\s]+)\s*(#.*)?$', val)
        if not m:
            return ""
        v_part  = m.group(1).strip()
        comment = m.group(2)
        if parent_key in ("AppTokens", "FakeAppIds", "SubscriptionTimestamps", "ManifestIds"):
            if not v_part.isdigit():
                return ""
            return f"{v_part} {comment.strip()}" if comment else v_part
        if parent_key == "GameTitles":
            v_quoted = sanitize_title(v_part)
            return f"{v_quoted} {comment.strip()}" if comment else v_quoted
        return val

    # ── Scalar-encoded structures ─────────────────────────────────
    def _parse_scalar_as_list(self, val: str) -> list:
        val = val.strip().strip("[]")
        result = []
        for part in val.split(","):
            part = part.strip()
            m    = re.match(r'^([0-9]+)\s*(#.*)?$', part)
            if m:
                num, comment = m.group(1), m.group(2)
                raw_val = f"{num} {comment.strip()}" if comment else num
                result.append(ConfigEntry(None, raw_val))
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
            if cleaned:
                result[k] = ConfigEntry(k, cleaned)
        return result

    def _parse_scalar_as_submap(self, val: str) -> dict:
        result  = {}
        m_appid = re.search(r'AppId:\s*([0-9]+)', val)
        m_title = re.search(r'Title:\s*(.*)',       val)
        result["AppId"]  = m_appid.group(1) if m_appid else "0"
        result["Title"] = sanitize_title(m_title.group(1)) if m_title else '""'
        return result


# ──────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────

def validate_config(config_path: Path) -> list[str]:
    """Scan the raw config for common formatting issues. Returns warning strings."""
    try:
        text = config_path.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"Cannot read file: {exc}"]

    issues:    list[str] = []
    seen_keys: dict      = {}

    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if "\t" in raw:
            issues.append(f"Line {lineno}: TAB character (YAML requires spaces)")
        if raw.endswith("\r"):
            issues.append(f"Line {lineno}: Windows-style CRLF line ending")
        if re.match(r'^[A-Za-z][A-Za-z0-9_]*:[^ \n]', raw):
            issues.append(f"Line {lineno}: Missing space after colon  →  '{raw.rstrip()}'")
        if raw != raw.rstrip():
            issues.append(f"Line {lineno}: Trailing whitespace")
        if re.match(r'^\s+-[^ ]', raw) and not re.match(r'^\s+- ', raw):
            issues.append(f"Line {lineno}: List item missing space after dash  →  '{raw.rstrip()}'")

        m = re.match(r'^([A-Za-z][A-Za-z0-9_]*):', raw)
        if m:
            seen_keys.clear()
            if m.group(1) not in KNOWN_KEYS:
                issues.append(
                    f"Line {lineno}: Unknown top-level key '{m.group(1)}'"
                    f" — may be a typo or misplaced line (will be dropped)"
                )

        # Duplicate numeric mapping keys
        dm = re.match(r'^(\s*)([0-9]+):\s+', raw)
        if dm:
            entry = (len(dm.group(1)), dm.group(2))
            if entry in seen_keys:
                issues.append(
                    f"Line {lineno}: Duplicate mapping key '{dm.group(2)}'"
                    f" (first at line {seen_keys[entry]})"
                )
            else:
                seen_keys[entry] = lineno

    return issues


# ──────────────────────────────────────────────────────────────
# Output formatters
# ──────────────────────────────────────────────────────────────

def fmt_list(entries: list, indent: int = 2) -> str:
    pad = " " * indent
    return "\n".join(f"{pad}- {e.raw_value}" for e in entries)


def fmt_map(entries: dict, indent: int = 2) -> str:
    pad   = " " * indent
    lines = []
    for k, v in entries.items():
        if v is None:
            lines.append(f"{pad}{k}:")
        elif isinstance(v, list):
            lines.append(f"{pad}{k}:")
            for item in v:
                lines.append(f"{pad}  - {item.raw_value}")
        else:
            lines.append(f"{pad}{k}: {v.raw_value}")
    return "\n".join(lines)


def fmt_map_of_lists(entries: dict, indent: int = 2) -> str:
    pad   = " " * indent
    lines = []
    for k, v in entries.items():
        lines.append(f"{pad}{k}:")
        if isinstance(v, list):
            for item in v:
                lines.append(f"{pad}  - {item.raw_value}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Deduplication
# ──────────────────────────────────────────────────────────────

def dedup_list(entries: list) -> list:
    """Remove duplicate list entries; preserve order, keep first occurrence."""
    seen, result = set(), []
    for entry in entries:
        key = entry.dedup_key
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return result


def dedup_map(entries: dict) -> dict:
    """
    Remove duplicate map keys. Last value wins; insertion order from first
    occurrence is preserved (Python 3.7+ dict semantics handle this natively).
    """
    ordered: dict = {}
    for k, v in entries.items():
        ordered[k] = v   # re-assigning an existing key updates value, not position
    return ordered


# ──────────────────────────────────────────────────────────────
# Steam name resolution  (module-level cache + parallel lookups)
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

                # Check for list of DLCs
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
    Get the fully formatted name (including [DLC] tag and parent game name if applicable).
    """
    name, parent_id, _ = get_app_details(appid)
    if not name:
        return ""

    if parent_id:
        # Resolve the parent name
        parent_name, _, _ = get_app_details(parent_id)
        if parent_name:
            return f"[DLC] {name} / {parent_name}"
        else:
            return f"[DLC] {name}"

    return name


def is_generic_or_missing_comment(raw_value: str) -> bool:
    """
    Check if a comment is missing or generic (e.g. '# App 12345' or '# unknown').
    """
    if "#" not in raw_value:
        return True
    comment = raw_value.split("#", 1)[1].strip()
    if not comment:
        return True

    # Matches placeholder comments like "App <digits>", "unknown app", "unknown", "untitled", "placeholder"
    if re.match(r'^(App\s*\d+|unknown|untitled|placeholder)$', comment, re.IGNORECASE):
        return True
    return False


def resolve_missing_names(old_data: dict) -> None:
    """
    Fill in or update generic inline comments (game names) for AdditionalApps and FakeAppIds.
    All API lookups are issued in parallel for speed.
    """
    additional_apps = old_data.get("AdditionalApps") or []
    fake_app_ids    = old_data.get("FakeAppIds")     or {}

    missing_additional = [
        e for e in additional_apps
        if isinstance(e, ConfigEntry) and is_generic_or_missing_comment(e.raw_value)
    ]
    missing_fake = [
        (k, e) for k, e in fake_app_ids.items()
        if isinstance(e, ConfigEntry) and is_generic_or_missing_comment(e.raw_value)
    ]

    if not missing_additional and not missing_fake:
        return

    # Collect every unique app ID that still needs a lookup
    all_ids: set[str] = set()
    for entry in missing_additional:
        all_ids.add(entry.dedup_key)
    for src_appid, entry in missing_fake:
        all_ids.add(src_appid)
        all_ids.add(entry.dedup_key)

    uncached = {aid for aid in all_ids if aid.isdigit() and aid not in _app_details_cache}

    if uncached:
        info(f"Resolving {len(uncached)} game name(s) in parallel...")
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(get_app_details, aid): aid for aid in uncached}
            for f in as_completed(futures):
                pass   # side-effect: results land in cache

    # Patch entries using the now-populated cache
    for entry in missing_additional:
        appid = entry.dedup_key
        name  = get_formatted_name(appid)
        if appid.isdigit() and name:
            entry.raw_value = f"{appid} # {name}"
            info(f"  AdditionalApps {appid} → {name}")

    for src_appid, entry in missing_fake:
        tgt_appid = entry.dedup_key
        if not (src_appid.isdigit() and tgt_appid.isdigit()):
            continue
        src_name = get_formatted_name(src_appid)
        tgt_name = get_formatted_name(tgt_appid)
        if src_name:
            comment         = f"{src_name} → {tgt_name}" if tgt_name else src_name
            entry.raw_value = f"{tgt_appid} # {comment}"
            info(f"  FakeAppIds {src_appid}:{tgt_appid} → {src_name}")


def fetch_raw_app_info(appid: str) -> Optional[dict]:
    """Fetch raw app info dict from SteamCMD API."""
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
    """
    Query the SteamCMD API for the given AppID, print depot manifest mapping,
    and do the same for any DLCs associated with the app.
    """
    if not appid or not appid.isdigit():
        error("Invalid AppID.")
        return

    info(f"Retrieving app info for {appid}...")
    app_info = fetch_raw_app_info(appid)
    if not app_info:
        error(f"Failed to retrieve data for AppID {appid}.")
        return

    game_name = app_info.get("common", {}).get("name") or app_info.get("name") or f"App {appid}"

    # Pre-warm cache for the main game details
    get_app_details(appid)

    # Process depots of this app
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

                # Check if this depot belongs to a DLC
                dlc_appid = dep_data.get("dlcappid")
                dep_label = label
                if dlc_appid:
                    # Resolve the DLC formatted name (e.g. "[DLC] DLC Name / Parent Name")
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

    # Check for list of DLCs
    dlc_raw = app_info.get("extended", {}).get("listofdlc")
    if dlc_raw:
        dlc_ids = [d.strip() for d in str(dlc_raw).split(",") if d.strip()]
        if dlc_ids:
            info(f"Found {len(dlc_ids)} DLC(s) for this app. Fetching DLC depots...")

            # Fetch DLC details in parallel to populate details cache
            with ThreadPoolExecutor(max_workers=8) as ex:
                futures = {ex.submit(get_app_details, aid): aid for aid in dlc_ids}
                for f in as_completed(futures):
                    pass

            for dlc_id in sorted(dlc_ids, key=int):
                # Retrieve from cache
                dlc_name, _, dlc_depots_raw = get_app_details(dlc_id)
                if not dlc_name:
                    continue
                # Also retrieve depots for the DLC specifically
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
# Fetch + extract template YAML from GitHub
# ──────────────────────────────────────────────────────────────

def fetch_template(url: str) -> str:
    """Download config_default.hpp and extract the YAML raw-string literal."""
    try:
        with urllib.request.urlopen(url, timeout=TEMPLATE_TIMEOUT) as resp:
            source = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError(f"Failed to download template: {exc}")

    match = re.search(r'= R"\((.+?)\)";', source, re.DOTALL)
    if not match:
        raise RuntimeError("Could not find the YAML template inside config_default.hpp!")

    return match.group(1)


# ──────────────────────────────────────────────────────────────
# Merge old config values into fresh template
# ──────────────────────────────────────────────────────────────

def _skip_template_children(template_lines: list, start: int) -> int:
    """Skip over indented child lines in the template (replaced by user data)."""
    i = start
    n = len(template_lines)
    while i < n:
        child = template_lines[i]
        cs    = child.strip()
        if not cs or cs.startswith("#"):
            break
        if child.startswith("  ") or child.startswith("\t"):
            i += 1
        else:
            break
    return i


def merge_config(template_yaml: str, old_data: dict) -> str:
    """
    Walk the template line-by-line, injecting the user's values wherever a
    known key appears. Template comments and overall structure are preserved.
    """
    out_lines      = []
    template_lines = template_yaml.splitlines()
    i = 0
    n = len(template_lines)

    while i < n:
        line     = template_lines[i]
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            out_lines.append(line)
            i += 1
            continue

        m = re.match(r'^([A-Za-z][A-Za-z0-9_]*):\s*(.*)', line)
        if not m:
            out_lines.append(line)
            i += 1
            continue

        key         = m.group(1)
        default_val = m.group(2).strip()

        # ── Scalar ───────────────────────────────────────────────
        if key in SCALAR_KEYS:
            user_val = old_data.get(key)
            val      = user_val if isinstance(user_val, str) and user_val else default_val
            if key in BOOLEAN_KEYS:
                val = sanitize_boolean(val, default_val)
            elif key == "LogLevel":
                val = sanitize_log_level(val, default_val)
            elif key == "FakeWalletBalance":
                val = sanitize_wallet_balance(val, default_val)
            elif key == "FakeEmail":
                if not val or val == '""':
                    val = '""'
                elif not (val.startswith('"') or val.startswith("'")):
                    val = f'"{val.strip()}"'
            out_lines.append(f"{key}: {val}")
            i += 1

        # ── List ─────────────────────────────────────────────────
        elif key in LIST_KEYS:
            out_lines.append(f"{key}:")
            user_val = old_data.get(key)
            if isinstance(user_val, list):
                clean = dedup_list(user_val)
                if clean:
                    out_lines.append(fmt_list(clean))
            i += 1
            i = _skip_template_children(template_lines, i)

        # ── Mapping ──────────────────────────────────────────────
        elif key in MAP_KEYS:
            out_lines.append(f"{key}:")
            user_val = old_data.get(key)
            if isinstance(user_val, dict):
                clean = dedup_map(user_val)
                if clean:
                    out_lines.append(fmt_map(clean))
            i += 1
            i = _skip_template_children(template_lines, i)

        # ── Mapping-of-lists ─────────────────────────────────────
        elif key in MAP_OF_LIST_KEYS:
            out_lines.append(f"{key}:")
            user_val = old_data.get(key)
            if isinstance(user_val, dict):
                out_lines.append(fmt_map_of_lists(user_val))
            i += 1
            i = _skip_template_children(template_lines, i)

        # ── IdleStatus / UnownedStatus ────────────────────────────
        elif key in (IDLE_STATUS_KEY, "UnownedStatus"):
            out_lines.append(f"{key}:")
            user_val = old_data.get(key)
            if isinstance(user_val, dict):
                app_id = user_val.get("AppId", "0")
                title  = sanitize_title(user_val.get("Title", '""'))
            else:
                app_id, title = "0", '""'
            out_lines.append(f"  AppId: {app_id}")
            out_lines.append(f"  Title: {title}")
            i += 1
            i = _skip_template_children(template_lines, i)

        # ── Unknown key → pass through unchanged ──────────────────
        else:
            out_lines.append(line)
            i += 1

    return "\n".join(out_lines).rstrip("\n") + "\n"


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SLSsteam config.yaml cleanup & update tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(f"""\
            Examples:
              Validate your current config for errors:
                python3 slssteam_config.py --validate-only

              Preview the cleaned & merged output (nothing is written):
                python3 slssteam_config.py --dry-run

              Update in place (auto-backup created):
                python3 slssteam_config.py

              Write to a different output file:
                python3 slssteam_config.py --output /tmp/config_new.yaml

              Skip outbound name lookups:
                python3 slssteam_config.py --no-resolve-names

              Query manifest/depot mapping for AppID 480:
                python3 slssteam_config.py --manifests 480
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
    parser.add_argument("--dry-run",          action="store_true",
                        help="Print merged config to stdout, don't write to disk")
    parser.add_argument("--no-backup",        action="store_true",
                        help="Skip creating a .bck backup before writing")
    parser.add_argument("--validate-only",    action="store_true",
                        help="Only check for errors, don't merge or write")
    parser.add_argument("--no-resolve-names", action="store_true",
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

    # If manifests query is requested, handle it immediately and exit
    if args.manifests:
        resolve_and_print_manifests(args.manifests)
        return

    # Interactive mode: no CLI flags were passed by the user
    interactive = (
        args.config          == DEFAULT_CONFIG_PATH
        and args.output      is None
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
        print("  2) Restore Backup  (from config.bck)")
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
            bak_path = config_path.with_suffix(".bck")
            if bak_path.exists():
                break
            error(f"Backup file not found: {bak_path}. Please try again.")
            print()

        info(f"Restoring {bak_path} → {config_path} ...")
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

    # ── Options 1/3 (interactive) or non-interactive CLI ─────────
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

    # ── 1. Validate ──────────────────────────────────────────────
    print(f"{BOLD}[1/4] Validating existing config...{RESET}")
    issues = validate_config(config_path)
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
    print(f"{BOLD}[2/4] Parsing existing config...{RESET}")
    config_text = config_path.read_text(encoding="utf-8")
    reader      = SimpleYAMLReader()
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

    # ── 2.5 Steam Deck preset (option 3) ─────────────────────────
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

    # ── 3. Fetch template ────────────────────────────────────────
    print(f"{BOLD}[3/4] Fetching latest template from GitHub...{RESET}")
    template_yaml = fetch_template(args.template_url)

    template_data = reader.parse(template_yaml)
    new_keys      = set(template_data) - set(old_data)
    removed_keys  = set(old_data)      - set(template_data)
    if new_keys:
        info(f"  New upstream key(s) — using defaults: {', '.join(sorted(new_keys))}")
    if removed_keys:
        warn(f"  Key(s) not in template (dropped): {', '.join(sorted(removed_keys))}")
    print()

    # ── 4. Merge ─────────────────────────────────────────────────
    print(f"{BOLD}[4/4] Merging your values into new template...{RESET}")
    merged = merge_config(template_yaml, old_data)

    for key in list(MAP_KEYS) + list(LIST_KEYS):
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

    if not args.no_backup:
        bak_path = config_path.with_suffix(".bck")
        shutil.copy2(config_path, bak_path)
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


def make_backup_with_rotation(config_path: Path) -> Path:
    """
    Creates a backup of config.yaml.
    If config.yaml.bak exists, tries config.yaml.bak2, config.yaml.bak3, etc.
    Returns the backup path.
    """
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


def run_asshead_migration(config_path: Path, template_url: str = TEMPLATE_SOURCE_URL) -> tuple[bool, str, Optional[Path]]:
    """
    Runs validation and merge of config.yaml.
    If changes/fixes are needed, creates a rotating backup and writes the merged config.
    Returns (success, message, backup_path).
    """
    try:
        if not config_path.exists():
            return False, f"Config file not found at {config_path}", None

        # 1. Parse old config
        config_text = config_path.read_text(encoding="utf-8")
        reader = SimpleYAMLReader()
        old_data = reader.parse(config_text)

        # 2. Fetch template
        template_yaml = fetch_template(template_url)
        template_data = reader.parse(template_yaml)

        # 3. Check if changes are needed
        issues = validate_config(config_path)
        new_keys = set(template_data) - set(old_data)

        # If no issues and no new keys, then it's already optimal!
        if not issues and not new_keys:
            return True, "No changes needed. SLSsteam config is already optimal!", None

        # 4. Merge values into new template
        merged = merge_config(template_yaml, old_data)

        # 5. Create backup and write
        bak_path = make_backup_with_rotation(config_path)
        config_path.write_text(merged, encoding="utf-8")

        msg = "Successfully updated config.yaml to the latest template and cleaned formatting."
        if new_keys:
            msg += f" Added {len(new_keys)} new upstream default key(s)."
        if issues:
            msg += f" Fixed {len(issues)} formatting issue(s)."

        return True, msg, bak_path

    except Exception as e:
        return False, f"Error: {e}", None


if __name__ == "__main__":
    main()
