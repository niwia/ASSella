"""Low-level config I/O and the transaction primitive.

Everything in this package ultimately reads through
``_read_config_content`` and commits through ``_atomic_write``. The
section-parsing helpers implement ASSella's hand-rolled YAML editing -
pyyaml is not used for writes because comments and ordering inside
config.yaml must survive a round trip.

Extracted from ``utils/yaml_config_manager.py``. That module is now a thin
facade that re-exports this package's public names, so every existing
``from utils.yaml_config_manager import ...`` keeps working unchanged.

Do not import across sibling modules in this package except from ``core`` -
dependencies must point in one direction (leaf -> core) to keep the package
import-cycle free.
"""

import logging

# --- imports ---------------------------------------------------------
import os, re, shutil
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from utils.yaml.constants import BACKUP_SUFFIX, SHARED_REDISTS, TOP_LEVEL_KEY_PATTERN
from utils.yaml.modes import is_slssteam_config_management_enabled, is_slssteam_mode_enabled
from utils.yaml.validation import _is_valid_hex64, _sanitize_comment, _sanitize_id, _validate_yaml_content

logger = logging.getLogger(__name__)


_CONFIG_DISABLED = object()

def _config_management_enabled() -> bool:
    return is_slssteam_mode_enabled() and is_slssteam_config_management_enabled()

def _read_config_content(config_path: Path, log_missing: bool = False) -> Optional[str]:
    if not config_path.exists():
        if log_missing:
            logger.warning(f"Config file not found at {config_path}")
        return None

    with open(config_path, "r", encoding="utf-8") as f:
        return f.read()

def _get_config_content_if_enabled(config_path: Path, log_missing: bool = False):
    if not _config_management_enabled():
        return _CONFIG_DISABLED
    return _read_config_content(config_path, log_missing=log_missing)

def _create_backup(config_path: Path) -> bool:
    """Create a backup of the config file.

    Creates config.yaml.bak with the current config content.
    Only creates backup if source file exists and is non-empty.
    """
    try:
        if not config_path.exists():
            return False

        new_size = config_path.stat().st_size
        if new_size == 0:
            logger.warning(f"Skipping backup: config file {config_path} is empty (0 bytes)")
            return False

        backup_path = config_path.with_name(config_path.name + BACKUP_SUFFIX)
        shutil.copy2(config_path, backup_path)
        logger.info(f"Created backup: {backup_path}")
        return True
    except OSError as e:
        logger.error(f"Failed to create backup for {config_path}: {e}", exc_info=True)
        return False

def _restrict_config_permissions(config_path: Path) -> bool:
    """Best-effort chmod 0600 on config.yaml (it contains decryption keys)."""
    try:
        mode = config_path.stat().st_mode
        if mode & 0o077:
            os.chmod(config_path, 0o600)
            logger.info(f"Restricted {config_path} permissions to 0600 (contains decryption keys)")
        return True
    except (OSError, AttributeError) as e:
        # Non-POSIX filesystem, or permission denied on a shared config.
        logger.debug(f"Could not restrict permissions on {config_path}: {e}")
        return False

def _atomic_write(config_path: Path, content: str) -> bool:
    """Write content to config file in-place to preserve inode and trigger inotify FileWatcher without empty-file window.

    config.yaml holds every depot decryption key the user owns, so it is forced
    to owner-only (0600) on every write. SLSsteam reads it as the same user, so
    this does not affect the running plugin. Best-effort: filesystems without
    POSIX modes are left alone rather than failing the write.
    """
    if not _validate_yaml_content(content):
        logger.error(f"Refusing to write invalid YAML content to {config_path}")
        return False

    try:
        if config_path.exists():
            with open(config_path, "r+", encoding="utf-8") as f:
                f.seek(0)
                f.write(content)
                f.truncate()
                f.flush()
                os.fsync(f.fileno())
        else:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        _restrict_config_permissions(config_path)
        return True
    except OSError as e:
        logger.error(f"Failed to write {config_path}: {e}", exc_info=True)
        return False

_write_in_place = _atomic_write

def calculate_file_sha256(file_path: Path) -> Optional[str]:
    """Calculate SHA256 checksum of a file."""
    if not file_path.is_file():
        return None
    try:
        import hashlib
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        logger.error(f"Failed to calculate SHA256 for {file_path}: {e}")
        return None

def get_user_config_path() -> Path:
    """Get the path to the user's SLSsteam config.yaml file.

    Delegates to SteamEnv for Flatpak-aware path resolution:
      - Flatpak Steam: ~/.var/app/com.valvesoftware.Steam/.config/SLSsteam/config.yaml
      - Native Steam:  $XDG_CONFIG_HOME/SLSsteam/config.yaml  (or ~/.config/SLSsteam/config.yaml)

    Emits a warning if the resolved config does not exist yet.
    """
    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        config_path = env.sls_config_path
        if not config_path.exists():
            logger.warning(
                f"SLSsteam config.yaml not found at expected location: {config_path}. "
                f"(Steam type: {'Flatpak' if env.is_flatpak else 'Native'}) "
                "SLSsteam may not be installed or configured yet."
            )
        return config_path
    except Exception as e:
        # Graceful fallback: if SteamEnv fails for any reason, use the original native path
        logger.warning(f"get_user_config_path: SteamEnv unavailable ({e}), falling back to native path")
        xdg_config_home_str = os.environ.get("XDG_CONFIG_HOME", "")
        xdg_config_home = (
            Path(xdg_config_home_str).expanduser() if xdg_config_home_str else Path()
        )
        if xdg_config_home_str and Path(xdg_config_home_str).is_absolute():
            config_dir = xdg_config_home / "SLSsteam"
        else:
            config_dir = Path.home() / ".config" / "SLSsteam"
        return config_dir / "config.yaml"

def _get_section_bounds(content: str, section_name: str) -> Optional[Tuple[int, int, int]]:
    """Return (header_start, content_start, section_end) for a top-level YAML section.

    header_start: index of the first character of the section header line.
    content_start: index of the first character after the newline of the header.
    section_end: index of the start of the next top-level key line or EOF.
    """
    header_pattern = re.compile(
        r"^[ \t]*['\"]?" + re.escape(section_name) + r"['\"]?[ \t]*:[ \t]*(?:\[[ \t]*\]|\{[ \t]*\})?[ \t]*(?:#[^\r\n]*)?$",
        re.MULTILINE,
    )
    match = header_pattern.search(content)
    if not match:
        return None

    header_start = match.start()
    content_start = match.end()
    if content_start < len(content) and content[content_start] == "\r":
        content_start += 1
    if content_start < len(content) and content[content_start] == "\n":
        content_start += 1

    after_section = content[content_start:]
    next_match = TOP_LEVEL_KEY_PATTERN.search(after_section)
    section_end = (content_start + next_match.start()) if next_match else len(content)

    return header_start, content_start, section_end

def _expand_flow_section_if_needed(
    content: str, section_name: str, bounds: Tuple[int, int, int]
) -> Tuple[str, Tuple[int, int, int]]:
    """If a top-level section is written in flow style like 'Section: []', expand to block header 'Section:'."""
    h_start, c_start, s_end = bounds
    header_line = content[h_start:c_start]
    flow_pattern = re.compile(
        r"^([ \t]*" + re.escape(section_name) + r"[ \t]*:)[ \t]*(?:\[[ \t]*\]|\{[ \t]*\})([ \t]*(?:#[^\r\n]*)?[\r\n]*)$"
    )
    m = flow_pattern.match(header_line)
    if m:
        new_header = f"{m.group(1)}{m.group(2)}"
        content = content[:h_start] + new_header + content[c_start:]
        new_bounds = _get_section_bounds(content, section_name)
        if new_bounds:
            return content, new_bounds
    return content, bounds

def _find_section_insert_pos(content: str, bounds: Tuple[int, int, int]) -> int:
    """Find the best position to insert a new entry into a section.
    Inserts right after the last indented list or map item, avoiding placing new entries
    below leading comments or whitespace of the subsequent section.
    """
    _, content_start, section_end = bounds
    section_text = content[content_start:section_end]

    lines = section_text.splitlines(keepends=True)
    last_item_end = 0
    curr_offset = 0

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and (line.startswith(" ") or line.startswith("\t")):
            last_item_end = curr_offset + len(line)
        elif stripped and not stripped.startswith("#") and not line.startswith(" ") and not line.startswith("\t"):
            break
        curr_offset += len(line)

    if last_item_end > 0:
        return content_start + last_item_end

    return content_start

def _get_section_start(content: str, pattern: re.Pattern) -> Optional[int]:
    match = pattern.search(content)
    if not match:
        return None
    section_start = match.end()
    if section_start < len(content) and content[section_start] == "\r":
        section_start += 1
    if section_start < len(content) and content[section_start] == "\n":
        section_start += 1
    return section_start

def _get_section_end(
    content: str, section_start: int, next_key_pattern: re.Pattern
) -> int:
    after_section = content[section_start:]
    next_match = next_key_pattern.search(after_section)
    if next_match:
        return section_start + next_match.start()
    return len(content)

def _remove_entry_in_memory(
    content: str,
    section_name: str,
    pattern: re.Pattern,
) -> Tuple[str, bool]:
    """Remove matching lines only within a specific top-level YAML section in memory."""
    removed = False
    while True:
        bounds = _get_section_bounds(content, section_name)
        if not bounds:
            break
        _, content_start, section_end = bounds
        section_content = content[content_start:section_end]
        match = pattern.search(section_content)
        if not match:
            break

        abs_match_start = content_start + match.start()
        line_start = content.rfind("\n", 0, abs_match_start)
        line_start = 0 if line_start == -1 else line_start + 1

        line_end = content.find("\n", abs_match_start)
        if line_end == -1:
            line_end = len(content)
        else:
            line_end += 1

        content = content[:line_start] + content[line_end:]
        removed = True

    return content, removed

def _remove_entry_from_section(
    config_path: Path,
    section_name: str,
    pattern: re.Pattern,
    success_message: str,
    error_message: str,
) -> bool:
    """Remove matching lines only within a specific top-level YAML section."""
    try:
        content = _read_config_content(config_path)
        if content is None:
            return False

        new_content, removed = _remove_entry_in_memory(content, section_name, pattern)
        if not removed:
            return False

        if not _atomic_write(config_path, new_content):
            return False

        logger.info(success_message)
        return True
    except OSError as e:
        logger.error(error_message.format(e=e), exc_info=True)
        return False

def _add_list_item_in_memory(
    content: str, section_name: str, item_id: str, comment: str = ""
) -> Tuple[str, bool]:
    """Add an item to a YAML list section in memory."""
    bounds = _get_section_bounds(content, section_name)
    comment_suffix = f" # {comment}" if comment else ""
    entry_line = f"  - {item_id}{comment_suffix}\n"

    if bounds:
        content, bounds = _expand_flow_section_if_needed(content, section_name, bounds)
        _, content_start, section_end = bounds
        sec_content = content[content_start:section_end]
        item_pattern = re.compile(
            rf"^[ \t]*-[ \t]*{re.escape(item_id)}[ \t]*(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        m = item_pattern.search(sec_content)
        if m:
            if comment:
                abs_start = content_start + m.start()
                abs_end = content_start + m.end()
                new_line = f"  - {item_id}{comment_suffix}"
                if content[abs_start:abs_end] != new_line:
                    return content[:abs_start] + new_line + content[abs_end:], True
            return content, False

        insert_pos = _find_section_insert_pos(content, bounds)
        if insert_pos > 0 and content[insert_pos - 1] != "\n":
            entry_line = "\n" + entry_line
        return content[:insert_pos] + entry_line + content[insert_pos:], True
    else:
        new_content = content.rstrip() + f"\n\n{section_name}:\n{entry_line}"
        return new_content, True

def _add_map_item_in_memory(
    content: str, section_name: str, key: str, value: str, comment: str = ""
) -> Tuple[str, bool]:
    """Add or update a key-value pair in a YAML map section in memory."""
    bounds = _get_section_bounds(content, section_name)
    comment_suffix = f" # {comment}" if comment else ""
    entry_line = f"  {key}: {value}{comment_suffix}\n"

    if bounds:
        content, bounds = _expand_flow_section_if_needed(content, section_name, bounds)
        _, content_start, section_end = bounds
        sec_content = content[content_start:section_end]
        map_pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(key)}['\"]?[ \t]*:[ \t]*([^\r\n#]+)(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        m = map_pattern.search(sec_content)
        if m:
            existing_val = m.group(1).strip().strip('"').strip("'")
            existing_comment = ""
            m_comm = re.search(r"#[ \t]*(.*)$", m.group(0))
            if m_comm:
                existing_comment = m_comm.group(1).strip()

            final_comment = comment if comment else existing_comment
            final_suffix = f" # {final_comment}" if final_comment else ""
            new_line = f"  {key}: {value}{final_suffix}"

            abs_start = content_start + m.start()
            abs_end = content_start + m.end()
            if content[abs_start:abs_end] != new_line:
                return content[:abs_start] + new_line + content[abs_end:], True
            return content, False

        insert_pos = _find_section_insert_pos(content, bounds)
        if insert_pos > 0 and content[insert_pos - 1] != "\n":
            entry_line = "\n" + entry_line
        return content[:insert_pos] + entry_line + content[insert_pos:], True
    else:
        new_content = content.rstrip() + f"\n\n{section_name}:\n{entry_line}"
        return new_content, True

def _add_dlc_batch_in_memory(
    content: str, parent_app_id: str, dlc_dict: Dict[str, str]
) -> Tuple[str, bool]:
    """Add multiple DLC entries under parent_app_id in DlcData section in memory."""
    if not dlc_dict:
        return content, False

    parent_app_id = str(parent_app_id).strip()
    bounds = _get_section_bounds(content, "DlcData")

    if not bounds:
        lines = ["DlcData:", f"  {parent_app_id}:"]
        for did, dname in dlc_dict.items():
            did_str = _sanitize_id(did) or str(did).strip()
            cname = _sanitize_comment(dname or f"DLC {did_str}").replace('"', '\\"')
            lines.append(f'    {did_str}: "{cname}"')
        new_entry = "\n".join(lines) + "\n"
        new_content = content.rstrip() + "\n\n" + new_entry
        return new_content, True

    content, bounds = _expand_flow_section_if_needed(content, "DlcData", bounds)
    _, dlc_start, dlc_end = bounds
    dlc_section = content[dlc_start:dlc_end]

    parent_pattern = re.compile(rf"^[ \t]+{re.escape(parent_app_id)}:[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
    parent_match = parent_pattern.search(dlc_section)

    if not parent_match:
        lines = [f"  {parent_app_id}:"]
        for did, dname in dlc_dict.items():
            did_str = _sanitize_id(did) or str(did).strip()
            cname = _sanitize_comment(dname or f"DLC {did_str}").replace('"', '\\"')
            lines.append(f'    {did_str}: "{cname}"')
        insert_text = "\n".join(lines) + "\n"
        insert_pos = _find_section_insert_pos(content, bounds)
        new_content = content[:insert_pos].rstrip() + "\n" + insert_text + "\n" + content[insert_pos:].lstrip("\n")
        return new_content, True

    p_start = dlc_start + parent_match.end()
    if p_start < len(content) and content[p_start] == "\r":
        p_start += 1
    if p_start < len(content) and content[p_start] == "\n":
        p_start += 1

    p_after = content[p_start:dlc_end]
    next_parent = re.search(r"^[ \t]+[0-9A-Za-z_]+:[ \t]*(?:#[^\r\n]*)?$", p_after, re.MULTILINE)
    parent_end = (p_start + next_parent.start()) if next_parent else dlc_end

    parent_block = content[p_start:parent_end]
    new_dlc_lines = []
    for did, dname in dlc_dict.items():
        did_str = _sanitize_id(did) or str(did).strip()
        check_pat = re.compile(rf'^[ \t]*{re.escape(did_str)}[ \t]*:[ \t]*"', re.MULTILINE)
        if not check_pat.search(parent_block):
            cname = _sanitize_comment(dname or f"DLC {did_str}").replace('"', '\\"')
            new_dlc_lines.append(f'    {did_str}: "{cname}"')

    if not new_dlc_lines:
        return content, False

    insert_text = "\n".join(new_dlc_lines) + "\n"
    new_content = content[:parent_end].rstrip() + "\n" + insert_text + content[parent_end:]
    return new_content, True

def _fix_additional_apps_indentation(content: str) -> Tuple[str, bool]:
    """Fix indentation of AdditionalApps list items."""
    bounds = _get_section_bounds(content, "AdditionalApps")
    if not bounds:
        return content, False

    _, content_start, section_end = bounds
    section_content = content[content_start:section_end]

    misaligned_item_pattern = re.compile(
        r"^[ \t]*-[ \t]*([^\r\n#]+?)(?=[ \t]*(?:#|$))", re.MULTILINE
    )
    fixed_section = misaligned_item_pattern.sub(r"  - \1", section_content)

    if fixed_section != section_content:
        fixed_content = content[:content_start] + fixed_section + content[section_end:]
        logger.debug("Fixed indentation of AdditionalApps list items")
        return fixed_content, True

    return content, False

def is_depot_shared_with_other_games(
    depot_id: Union[str, int], excluding_appid: Optional[Union[str, int]] = None
) -> bool:
    """Check if a depot ID is shared with other installed/registered games or is a common redistributable.

    Returns True if:
      1. It is a known Steam common redistributable depot (Steamworks Shared, DirectX, VC++, etc.).
      2. It is referenced by any other registered game in plugin_library.json.
      3. It is listed under InstalledDepots in any other game's appmanifest_*.acf on disk.
      4. It is tagged for another AppID or marked as Steamworks Shared in SLSsteam config.yaml comments.
    """
    depot_id_str = str(depot_id).strip()
    if not depot_id_str:
        return False

    # 1. Known redistributables / shared runtime depots
    try:
        from ui.assets import DEPOT_BLACKLIST
        shared_known = {str(d) for d in DEPOT_BLACKLIST} | SHARED_REDISTS
    except Exception:
        shared_known = SHARED_REDISTS

    if depot_id_str in shared_known:
        logger.debug(f"[SharedDepotCheck] Depot {depot_id_str} is a known common redistributable.")
        return True

    ex_aid_str = str(excluding_appid).strip() if excluding_appid is not None else None

    # 2. Check plugin_library.json
    try:
        from utils.plugin_games import load_plugin_library
        lib = load_plugin_library()
        for aid, g in lib.items():
            if ex_aid_str and str(aid) == ex_aid_str:
                continue
            for did in g.get("depots", []):
                if str(did) == depot_id_str:
                    logger.debug(
                        f"[SharedDepotCheck] Depot {depot_id_str} is used by registered game "
                        f"'{g.get('name')}' ({aid})"
                    )
                    return True
            for kid in g.get("keys", {}).keys():
                if str(kid) == depot_id_str:
                    logger.debug(
                        f"[SharedDepotCheck] Depot {depot_id_str} key is used by registered game "
                        f"'{g.get('name')}' ({aid})"
                    )
                    return True
    except Exception as e:
        logger.debug(f"[SharedDepotCheck] Error checking plugin library: {e}")

    # 3. Check Steam library ACF manifests
    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        steamapps_dirs = getattr(env, "steamapps_paths", [])
        for sdir in steamapps_dirs:
            sdir_path = Path(sdir)
            if not sdir_path.is_dir():
                continue
            for acf in sdir_path.glob("appmanifest_*.acf"):
                if ex_aid_str and acf.name == f"appmanifest_{ex_aid_str}.acf":
                    continue
                try:
                    txt = acf.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r'"InstalledDepots"\s*\{([^}]*)\}', txt, re.DOTALL)
                    if m and f'"{depot_id_str}"' in m.group(1):
                        logger.debug(
                            f"[SharedDepotCheck] Depot {depot_id_str} found in InstalledDepots of {acf.name}"
                        )
                        return True
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"[SharedDepotCheck] Error checking Steam ACF manifests: {e}")

    # 4. Check SLSsteam config.yaml comments strictly for explicit Steamworks Shared marker
    try:
        cfg_path = get_user_config_path()
        if cfg_path and cfg_path.exists():
            cfg_text = cfg_path.read_text(encoding="utf-8", errors="ignore")
            # Check AdditionalDepots section
            depot_bounds = _get_section_bounds(cfg_text, "AdditionalDepots")
            if depot_bounds:
                sec_depots = cfg_text[depot_bounds[1]:depot_bounds[2]]
                m = re.search(
                    rf"^[ \t]*-[ \t]*['\"]?{re.escape(depot_id_str)}['\"]?[ \t]*(?:#[ \t]*(.*))?$",
                    sec_depots,
                    re.MULTILINE,
                )
                if m:
                    comment = (m.group(1) or "").strip()
                    if "steamworks shared" in comment.lower():
                        return True

            # Check DecryptionKeys section
            key_bounds = _get_section_bounds(cfg_text, "DecryptionKeys")
            if key_bounds:
                sec_keys = cfg_text[key_bounds[1]:key_bounds[2]]
                m = re.search(
                    rf"^[ \t]*['\"]?{re.escape(depot_id_str)}['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[ \t]*(.*))?$",
                    sec_keys,
                    re.MULTILINE,
                )
                if m:
                    comment = (m.group(1) or "").strip()
                    if "steamworks shared" in comment.lower():
                        return True
    except Exception as e:
        logger.debug(f"[SharedDepotCheck] Error checking config.yaml comments: {e}")

    return False

class BatchConfigEditor:
    """Context manager and in-memory batch editor for SLSsteam config.yaml.
    Performs all modifications in memory and writes once on context exit,
    preventing filewatcher reload storms and ensuring atomicity.
    """

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.content: Optional[str] = None
        self.has_changes = False
        self.committed_successfully = True

    def __enter__(self):
        self.content = _read_config_content(self.config_path)
        if self.content is None:
            self.content = ""
        self.has_changes = False
        self.committed_successfully = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.committed_successfully = False
            logger.warning(f"Batch config edit on {self.config_path} aborted due to: {exc_val}")
            return False

        if self.has_changes and self.content is not None:
            if not _atomic_write(self.config_path, self.content):
                logger.error(f"Failed to commit batch config changes to {self.config_path}")
                self.committed_successfully = False
                return False
            self.committed_successfully = True
            logger.info(f"Committed batch config changes to {self.config_path}")
        return True

    def add_app(self, app_id: Union[str, int], comment: str = "") -> bool:
        app_id_str = _sanitize_id(app_id)
        if not app_id_str:
            logger.warning(f"Invalid AppID provided: {app_id}")
            return False
        comment_clean = _sanitize_comment(comment)
        self.content, _ = _fix_additional_apps_indentation(self.content)
        new_content, changed = _add_list_item_in_memory(
            self.content, "AdditionalApps", app_id_str, comment_clean
        )
        if changed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_app(self, app_id: Union[str, int]) -> bool:
        app_id_str = _sanitize_id(app_id)
        if not app_id_str:
            return False
        pattern = re.compile(
            rf"^[ \t]*-[ \t]*{re.escape(app_id_str)}[ \t]*(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        new_content, removed = _remove_entry_in_memory(
            self.content, "AdditionalApps", pattern
        )
        if removed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def add_depot(
        self,
        depot_id: Union[str, int],
        comment: str = "",
        app_id: Optional[Union[str, int]] = None,
    ) -> bool:
        depot_id_str = _sanitize_id(depot_id)
        if not depot_id_str:
            logger.warning(f"Invalid DepotID provided: {depot_id}")
            return False

        # Guard: never add a base game AppID into AdditionalDepots
        if app_id and depot_id_str == str(app_id).strip():
            logger.warning(f"Refusing to add base AppID '{depot_id_str}' to AdditionalDepots")
            return False

        # Guard: never add any registered base game AppID from plugin_library
        try:
            from utils.plugin_games import load_plugin_library
            plib = load_plugin_library()
            if depot_id_str in plib and not plib[depot_id_str].get("dlc_only"):
                logger.warning(f"Refusing to add registered base AppID '{depot_id_str}' to AdditionalDepots")
                return False
        except Exception:
            pass

        # Guard: check AdditionalApps (reject base AppIDs, allow verified DLC AppIDs)
        bounds_apps = _get_section_bounds(self.content, "AdditionalApps")
        if bounds_apps:
            apps_text = self.content[bounds_apps[1] : bounds_apps[2]]
            m_app = re.search(
                rf"^[ \t]*-[ \t]*{re.escape(depot_id_str)}(?:[ \t]*#[ \t]*(.*))?$",
                apps_text,
                re.MULTILINE,
            )
            if m_app:
                is_known_dlc = False
                try:
                    from utils.plugin_games import build_dlc_reverse_map
                    if depot_id_str in build_dlc_reverse_map():
                        is_known_dlc = True
                except Exception:
                    pass

                if not is_known_dlc and app_id:
                    try:
                        from utils.dlc_helpers import is_dlc_depot
                        if is_dlc_depot(depot_id_str, base_appid=app_id):
                            is_known_dlc = True
                    except Exception:
                        pass

                if not is_known_dlc:
                    cm = (m_app.group(1) or "").lower()
                    if "dlc" in cm:
                        is_known_dlc = True

                if not is_known_dlc:
                    logger.warning(
                        f"Refusing to add base AppID '{depot_id_str}' from AdditionalApps to AdditionalDepots"
                    )
                    return False

        bounds_main = _get_section_bounds(self.content, "AppIds")
        if bounds_main:
            main_text = self.content[bounds_main[1] : bounds_main[2]]
            if re.search(
                rf"^[ \t]*-[ \t]*{re.escape(depot_id_str)}(?:[ \t]*#[ \t]*(.*))?$",
                main_text,
                re.MULTILINE,
            ):
                logger.warning(
                    f"Refusing to add AppID '{depot_id_str}' from AppIds to AdditionalDepots"
                )
                return False

        if depot_id_str in SHARED_REDISTS:
            comment_clean = "Steamworks Shared"
        else:
            comment_clean = _sanitize_comment(comment)

        new_content, changed = _add_list_item_in_memory(
            self.content, "AdditionalDepots", depot_id_str, comment_clean
        )
        if changed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_depot(
        self,
        depot_id: Union[str, int],
        check_shared: bool = True,
        excluding_appid: Optional[Union[str, int]] = None,
    ) -> bool:
        depot_id_str = _sanitize_id(depot_id)
        if not depot_id_str:
            return False
        if check_shared and is_depot_shared_with_other_games(
            depot_id_str, excluding_appid=excluding_appid
        ):
            logger.info(
                f"Preserving shared depot '{depot_id_str}' in AdditionalDepots (used by other games or redistributable)"
            )
            return False
        pattern = re.compile(
            rf"^[ \t]*-[ \t]*{re.escape(depot_id_str)}[ \t]*(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        new_content, removed = _remove_entry_in_memory(
            self.content, "AdditionalDepots", pattern
        )
        if removed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def add_key(
        self,
        depot_id: Union[str, int],
        key: str,
        comment: str = "",
        app_id: Optional[Union[str, int]] = None,
    ) -> bool:
        depot_id_str = _sanitize_id(depot_id)
        if not depot_id_str:
            logger.warning(f"Invalid DepotID for key: {depot_id}")
            return False
        key_str = str(key).strip().lower()
        if not _is_valid_hex64(key_str):
            logger.warning(f"Invalid AES decryption key (not 64 hex chars) for depot {depot_id_str}")
            return False

        if depot_id_str in SHARED_REDISTS:
            comment_clean = "Steamworks Shared"
        else:
            comment_clean = _sanitize_comment(comment)

        new_content, changed = _add_map_item_in_memory(
            self.content, "DecryptionKeys", depot_id_str, key_str, comment_clean
        )
        if changed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_key(
        self,
        depot_id: Union[str, int],
        check_shared: bool = True,
        excluding_appid: Optional[Union[str, int]] = None,
    ) -> bool:
        depot_id_str = _sanitize_id(depot_id)
        if not depot_id_str:
            return False
        if check_shared and is_depot_shared_with_other_games(
            depot_id_str, excluding_appid=excluding_appid
        ):
            logger.info(
                f"Preserving decryption key for shared depot '{depot_id_str}' (used by other games or redistributable)"
            )
            return False
        pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(depot_id_str)}['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        new_content, removed = _remove_entry_in_memory(
            self.content, "DecryptionKeys", pattern
        )
        if removed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def add_dlc(
        self,
        parent_app_id: Union[str, int],
        dlc_id: Union[str, int],
        dlc_name: str = "",
    ) -> bool:
        parent_str = _sanitize_id(parent_app_id)
        dlc_str = _sanitize_id(dlc_id)
        if not parent_str or not dlc_str:
            return False
        return self.add_dlc_batch(parent_str, {dlc_str: dlc_name})

    def add_dlc_batch(
        self,
        parent_app_id: Union[str, int],
        dlc_dict: Dict[str, str],
    ) -> bool:
        parent_str = _sanitize_id(parent_app_id)
        if not parent_str or not dlc_dict:
            return False
        new_content, changed = _add_dlc_batch_in_memory(
            self.content, parent_str, dlc_dict
        )
        if changed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_dlc(
        self, parent_app_id: Union[str, int], dlc_id: Optional[Union[str, int]] = None
    ) -> bool:
        parent_str = _sanitize_id(parent_app_id)
        if not parent_str:
            return False
        bounds = _get_section_bounds(self.content, "DlcData")
        if not bounds:
            return False
        _, dlc_start, dlc_end = bounds
        dlc_section = self.content[dlc_start:dlc_end]
        parent_pattern = re.compile(rf"^[ \t]+{re.escape(parent_str)}:[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
        parent_match = parent_pattern.search(dlc_section)
        if not parent_match:
            return False

        p_line_start = dlc_start + parent_match.start()
        p_start = dlc_start + parent_match.end()
        if p_start < len(self.content) and self.content[p_start] == "\r":
            p_start += 1
        if p_start < len(self.content) and self.content[p_start] == "\n":
            p_start += 1

        p_after = self.content[p_start:dlc_end]
        next_parent = re.search(r"^[ \t]+[0-9A-Za-z_]+:[ \t]*(?:#[^\r\n]*)?$", p_after, re.MULTILINE)
        p_block_end = (p_start + next_parent.start()) if next_parent else dlc_end

        if dlc_id is None:
            # Remove entire parent section
            self.content = self.content[:p_line_start] + self.content[p_block_end:]
            self.has_changes = True
            return True

        dlc_str = _sanitize_id(dlc_id)
        if not dlc_str:
            return False

        dlc_item_pattern = re.compile(
            rf'^[ \t]*{re.escape(dlc_str)}[ \t]*:[ \t]*"[^"\r\n]*"(?:#[^\r\n]*)?$',
            re.MULTILINE,
        )
        parent_block = self.content[p_start:p_block_end]
        match = dlc_item_pattern.search(parent_block)
        if not match:
            return False

        abs_match_start = p_start + match.start()
        line_start = self.content.rfind("\n", 0, abs_match_start)
        line_start = 0 if line_start == -1 else line_start + 1
        line_end = self.content.find("\n", abs_match_start)
        line_end = len(self.content) if line_end == -1 else line_end + 1

        self.content = self.content[:line_start] + self.content[line_end:]
        self.has_changes = True
        return True

    def add_fake_app_id(
        self,
        app_id: Union[str, int],
        target_id: Union[str, int],
        comment: str = "",
    ) -> bool:
        app_id_str = _sanitize_id(app_id)
        target_id_str = _sanitize_id(target_id)
        if not app_id_str or not target_id_str:
            return False
        comment_clean = _sanitize_comment(comment)
        new_content, changed = _add_map_item_in_memory(
            self.content, "FakeAppIds", app_id_str, target_id_str, comment_clean
        )
        if changed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_fake_app_id(self, app_id: Union[str, int]) -> bool:
        app_id_str = _sanitize_id(app_id)
        if not app_id_str:
            return False
        pattern = re.compile(
            rf"^[ \t]*(?:['\"]?{re.escape(app_id_str)}['\"]?[ \t]*:[ \t]*[^\r\n#]+|[^\r\n#:]+[ \t]*:[ \t]*['\"]?{re.escape(app_id_str)}['\"]?)(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        new_content, removed = _remove_entry_in_memory(
            self.content, "FakeAppIds", pattern
        )
        if removed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_app_token(self, app_id: Union[str, int]) -> bool:
        app_id_str = _sanitize_id(app_id)
        if not app_id_str:
            return False
        pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(app_id_str)}['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        new_content, removed = _remove_entry_in_memory(
            self.content, "AppTokens", pattern
        )
        if removed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_secondary_entry(self, section_name: str, item_id: Union[str, int]) -> bool:
        item_id_str = _sanitize_id(item_id)
        if not item_id_str:
            return False
        pattern = re.compile(
            rf"^[ \t]*(?:-[ \t]*)?['\"]?{re.escape(item_id_str)}['\"]?[ \t]*(?::[^\r\n#]*|$)(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        new_content, removed = _remove_entry_in_memory(
            self.content, section_name, pattern
        )
        if removed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def add_launch_option(self, app_id: Union[str, int], option: str) -> bool:
        app_id_str = _sanitize_id(app_id)
        if not app_id_str:
            return False
        option_clean = _sanitize_comment(option)
        new_content, changed = _add_map_item_in_memory(
            self.content, "LaunchOptions", app_id_str, option_clean
        )
        if changed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

    def remove_launch_option(self, app_id: Union[str, int]) -> bool:
        app_id_str = _sanitize_id(app_id)
        if not app_id_str:
            return False
        pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(app_id_str)}['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
        new_content, removed = _remove_entry_in_memory(
            self.content, "LaunchOptions", pattern
        )
        if removed:
            self.content = new_content
            self.has_changes = True
            return True
        return False

def batch_config_edit(config_path: Optional[Path] = None) -> BatchConfigEditor:
    """Return a BatchConfigEditor context manager to batch multiple additions, updates,
    and removals into a single atomic write."""
    if config_path is None:
        config_path = get_user_config_path()
    return BatchConfigEditor(config_path)
