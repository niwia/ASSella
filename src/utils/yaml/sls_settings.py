"""SLSsteam's own settings in config.yaml (the scalar ones ASSella does not manage).

config.yaml is the source of truth: values are read from the file every time,
nothing is mirrored in ASSella's settings.

Writing follows the rest of the YAML package:

* Only the value span of the one line is replaced. Comments (above the key and
  inline after it), ordering and the rest of the file stay byte-for-byte.
* Keys are matched at column 0 only, so an identically named nested key is
  never touched. Nested values (IdleStatus.AppId/Title) are matched inside
  their parent block only.
* The new content must parse, must have no duplicate top-level keys, and must
  read back as exactly the value that was asked for, or nothing is written.
* The write goes through ``_atomic_write``: in place (same inode, so SLSsteam's
  file watcher sees one change and never an empty file), fsync'd, 0600.
* A missing key is appended at the end, after a blank line, never duplicated.
* The session backup (config.yaml.bak) is taken before the first change.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from pathlib import Path

from utils.yaml.core import _atomic_write, _create_backup

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlsSetting:
    key: str                 # top-level key, or "Parent.Child" for one level of nesting
    label: str
    kind: str                # "bool" | "int" | "hex" | "str"
    group: str
    hint: str = ""           # fallback one-liner when the file has no comment
    minimum: int = 0
    maximum: int = 2_147_483_647
    flags: Tuple[Tuple[int, str], ...] = field(default_factory=tuple)


# What the Advanced tab shows. Keys ASSella manages elsewhere are left out on
# purpose: AppIds, AdditionalApps, AdditionalDepots, DecryptionKeys, DlcData,
# FakeAppIds, ManifestIds, LaunchOptions, Plugins, DenuvoGames, API, and the
# logging keys (Tools -> Logging).
ADVANCED_SETTINGS: List[SlsSetting] = [
    SlsSetting("DisableFamilyShareLock", "Disable Family Share lock", "bool", "Library & licences",
               "Stops Family Share from locking your library while someone else plays."),
    SlsSetting("DisableUpdates", "Disable updates for added apps", "bool", "Library & licences",
               "Stops Steam updating games added through AdditionalApps."),
    SlsSetting("DisableCloud", "Disable cloud saves", "bool", "Library & licences",
               "Turns off Steam Cloud for unlocked games."),
    SlsSetting("SafeMode", "Safe mode", "bool", "Safety",
               "Disables SLSsteam when steamclient.so is not a known-good version."),
    SlsSetting("WarnHashMissmatch", "Warn on Steam client change", "bool", "Safety",
               "Notifies you when steamclient.so differs from the known-safe one."),
    SlsSetting("NotifyInit", "Notify when loaded", "bool", "Safety",
               "Shows a notification once SLSsteam has started."),
    SlsSetting("SmartTickets", "Smart tickets", "hex", "Games & DRM",
               "Smarter SteamID spoofing by inspecting the game at launch.",
               maximum=0xFF, flags=((0x1, "SteamDRM"), (0x2, "Denuvo"))),
    SlsSetting("MaxSchemaTries", "Achievement schema tries", "int", "Games & DRM",
               "How often to fetch achievement schemas from Steam's CDN (0 = off).", maximum=1000),
    SlsSetting("IdleStatus.AppId", "Idle status AppID", "int", "Profile",
               "Custom in-game status. 0 turns it off."),
    SlsSetting("IdleStatus.Title", "Idle status title", "str", "Profile",
               "Text shown with the custom status."),
    SlsSetting("FakeName", "Display name", "str", "Profile",
               "Changes your persona name on this PC only."),
    SlsSetting("FakeEmail", "E-mail", "str", "Profile",
               "Changes your account e-mail on this PC only. Blank = off."),
    SlsSetting("FakeWalletBalance", "Wallet balance", "int", "Profile",
               "Changes the wallet balance shown on this PC only. 0 = off."),
]

LOGGING_SETTINGS: Dict[str, SlsSetting] = {
    "LogLevels": SlsSetting("LogLevels", "Log level", "hex", "Logging", maximum=0xFF,
                            flags=((0x1, "Trace"), (0x2, "Once"), (0x4, "Debug"), (0x8, "Warn"),
                                   (0x10, "Error"), (0x20, "Info"), (0x40, "Short notifications"),
                                   (0x80, "Long notifications"))),
    "ExtendedLogging": SlsSetting("ExtendedLogging", "Extended logging", "bool", "Logging"),
}

# Log level presets for the Tools -> Logging dropdown.
LOG_LEVEL_PRESETS: List[Tuple[str, int]] = [
    ("Everything + notifications", 0xFF),
    ("Everything (default)", 0x3F),
    ("Info, warnings, errors", 0x38),
    ("Warnings and errors", 0x18),
    ("Errors only", 0x10),
    ("Off", 0x0),
]

_TRUE = ("yes", "true", "on", "enabled", "1")
_FALSE = ("no", "false", "off", "disabled", "0")


# --------------------------------------------------------------------- reading
def _read(path: Path) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _load(content: str) -> Dict[str, Any]:
    import yaml

    data = yaml.safe_load(content)
    return data if isinstance(data, dict) else {}


def _get(data: Dict[str, Any], key: str) -> Any:
    if "." in key:
        parent, child = key.split(".", 1)
        block = data.get(parent)
        return block.get(child) if isinstance(block, dict) else None
    return data.get(key)


def read_sls_settings(path: Path, keys: List[str]) -> Dict[str, Any]:
    """Current values straight from config.yaml (None = key not present)."""
    content = _read(path)
    if content is None:
        return {}
    try:
        data = _load(content)
    except Exception as e:
        logger.warning(f"[SlsSettings] {path} does not parse: {e}")
        return {}
    return {k: _get(data, k) for k in keys}


def read_descriptions(path: Path) -> Dict[str, str]:
    """The comment block right above each top-level key (SLSsteam's own docs).

    Shown as the tooltip, so the full explanation stays whatever the
    installed SLSsteam version says.
    """
    content = _read(path) or ""
    out: Dict[str, str] = {}
    comment: List[str] = []
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("#"):
            comment.append(s.lstrip("#").strip())
            continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9_]*)[ \t]*:", line)
        if m and comment:
            out.setdefault(m.group(1), "\n".join(c for c in comment if c))
        comment = []
    return out


# --------------------------------------------------------------------- writing
def format_value(setting: SlsSetting, value: Any) -> str:
    """The YAML text for a value, in the style SLSsteam's template uses."""
    if setting.kind == "bool":
        return "yes" if bool(value) else "no"
    if setting.kind == "int":
        return str(int(value))
    if setting.kind == "hex":
        return f"0x{int(value):x}"
    text = "" if value is None else str(value)
    if "\n" in text or "\r" in text:
        raise ValueError("Line breaks are not allowed here.")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _coerce(setting: SlsSetting, value: Any) -> Any:
    """What PyYAML should read back for ``value`` (to verify the write)."""
    if setting.kind == "bool":
        return bool(value)
    if setting.kind in ("int", "hex"):
        v = int(value)
        if not setting.minimum <= v <= setting.maximum:
            raise ValueError(f"{setting.label} must be between {setting.minimum} and {setting.maximum}.")
        return v
    return "" if value is None else str(value)


def _value_regex(key: str, indent: str = "") -> re.Pattern:
    # The value is everything up to an inline comment (" #") or end of line;
    # the separator before the comment is kept, so "yes # note" stays valid.
    return re.compile(
        r"^(?P<lead>" + re.escape(indent) + re.escape(key) + r"[ \t]*:)(?P<sp>[ \t]*)"
        r"(?P<val>(?:\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\n]|'')*'|[^#\n]*?))"
        r"(?P<tail>[ \t]+#.*)?$",
        re.MULTILINE,
    )


def _block_bounds(content: str, parent: str) -> Optional[Tuple[int, int]]:
    m = re.search(r"^" + re.escape(parent) + r"[ \t]*:[ \t]*(?:#.*)?$", content, re.MULTILINE)
    if not m:
        return None
    start = m.end() + 1
    nxt = re.compile(r"^(?=\S)", re.MULTILINE).search(content, start)
    end = nxt.start() if nxt else len(content)
    return start, end


def _apply(content: str, setting: SlsSetting, text: str) -> str:
    if "." not in setting.key:
        rx = _value_regex(setting.key)
        m = rx.search(content)
        if m is None:
            return content.rstrip("\n") + f"\n\n{setting.key}: {text}\n"
        return content[: m.start("sp")] + " " + text + content[m.end("val"):]

    parent, child = setting.key.split(".", 1)
    bounds = _block_bounds(content, parent)
    if bounds is None:
        return content.rstrip("\n") + f"\n\n{parent}:\n  {child}: {text}\n"
    start, end = bounds
    block = content[start:end]
    indent_m = re.search(r"^([ \t]+)\S", block, re.MULTILINE)
    indent = indent_m.group(1) if indent_m else "  "
    m = _value_regex(child, indent).search(block)
    if m is None:
        insert = f"{indent}{child}: {text}\n"
        # after the last indented line of the block
        last = 0
        for lm in re.finditer(r"^[ \t]+\S.*$\n?", block, re.MULTILINE):
            last = lm.end()
        block = block[:last] + ("" if block[:last].endswith("\n") or not last else "\n") + insert + block[last:]
    else:
        block = block[: m.start("sp")] + " " + text + block[m.end("val"):]
    return content[:start] + block + content[end:]


def write_sls_setting(path: Path, setting: SlsSetting, value: Any) -> Tuple[bool, str]:
    """Set one SLSsteam setting in config.yaml. Returns (changed_ok, message).

    Verifies the result reads back as the requested value before writing; on
    any doubt the file is left untouched.
    """
    try:
        wanted = _coerce(setting, value)
        text = format_value(setting, value)
    except (TypeError, ValueError) as e:
        return False, str(e)

    content = _read(path)
    if content is None:
        return False, f"{path} not found."
    try:
        before = _load(content)
    except Exception as e:
        return False, f"config.yaml does not parse, not touching it: {e}"

    current = _get(before, setting.key)
    if current is not None and _same(setting, current, wanted):
        return True, "unchanged"

    new_content = _apply(content, setting, text)
    try:
        after = _load(new_content)
    except Exception as e:
        logger.error(f"[SlsSettings] Edit of {setting.key} would break the YAML: {e}")
        return False, "That change would break config.yaml; nothing was written."
    if not _same(setting, _get(after, setting.key), wanted):
        logger.error(f"[SlsSettings] {setting.key} would read back as {_get(after, setting.key)!r}, not {wanted!r}")
        return False, "Could not write that value safely; nothing was written."
    # Nothing else may change meaning.
    for k in set(before) | set(after):
        top = setting.key.split(".", 1)[0]
        if k != top and before.get(k) != after.get(k):
            logger.error(f"[SlsSettings] Edit of {setting.key} would also change {k}; refusing")
            return False, "Could not write that value safely; nothing was written."

    _create_backup(path)
    if not _atomic_write(path, new_content):
        return False, "Writing config.yaml failed (see log)."
    logger.info(f"[SlsSettings] {setting.key} = {text}")
    return True, "saved"


def _same(setting: SlsSetting, current: Any, wanted: Any) -> bool:
    if setting.kind == "bool":
        if isinstance(current, str):
            low = current.strip().lower()
            current = True if low in _TRUE else False if low in _FALSE else current
        return current is wanted or current == wanted
    if setting.kind in ("int", "hex"):
        try:
            return int(current) == wanted
        except (TypeError, ValueError):
            return False
    return ("" if current is None else str(current)) == wanted


__all__ = [
    "ADVANCED_SETTINGS",
    "LOGGING_SETTINGS",
    "LOG_LEVEL_PRESETS",
    "SlsSetting",
    "format_value",
    "read_descriptions",
    "read_sls_settings",
    "write_sls_setting",
]
