"""FakeAppIds section and its merge against the on-disk database.

Extracted from ``utils/yaml_config_manager.py``. That module is now a thin
facade that re-exports this package's public names, so every existing
``from utils.yaml_config_manager import ...`` keeps working unchanged.

Do not import across sibling modules in this package except from ``core`` -
dependencies must point in one direction (leaf -> core) to keep the package
import-cycle free.
"""

import logging

# --- imports ---------------------------------------------------------
import re
from pathlib import Path
from typing import Optional, Set

from utils.yaml.core import _CONFIG_DISABLED, _atomic_write, _create_backup, _get_config_content_if_enabled, _get_section_bounds, _read_config_content, _remove_entry_from_section
from utils.yaml.modes import get_fake_appid_for_online, is_slssteam_config_management_enabled, is_slssteam_mode_enabled

from utils.settings import get_settings

logger = logging.getLogger(__name__)


def get_fake_app_ids(config_path: Path, fake_appid: str = "") -> Set[str]:
    """Get all FakeAppIds from SLSsteam config.yaml."""
    fake_app_ids = set()

    if not fake_appid:
        fake_appid = get_fake_appid_for_online()

    try:
        content = _read_config_content(config_path)
        if not content:
            return fake_app_ids

        bounds = _get_section_bounds(content, "FakeAppIds")
        if not bounds:
            return fake_app_ids

        _, content_start, section_end = bounds
        section_content = content[content_start:section_end]
        entry_pattern = re.compile(
            rf"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*{re.escape(fake_appid)}(?:[ \t]+#[^\r\n]*|[ \t]*)$",
            re.MULTILINE,
        )

        for entry_match in entry_pattern.finditer(section_content):
            app_id = entry_match.group(1).strip()
            fake_app_ids.add(app_id)

    except OSError as e:
        logger.error(
            f"Failed to read FakeAppIds from {config_path}: {e}",
            exc_info=True,
        )

    return fake_app_ids

def get_fake_appid(config_path: Path, app_id: str) -> Optional[str]:
    """Get the FakeAppId for a specific AppID from SLSsteam config.yaml."""
    try:
        content = _read_config_content(config_path)
        if not content:
            return None

        bounds = _get_section_bounds(content, "FakeAppIds")
        if not bounds:
            return None

        _, content_start, section_end = bounds
        section_content = content[content_start:section_end]
        entry_pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(str(app_id))}['\"]?[ \t]*:[ \t]*(\d+)",
            re.MULTILINE,
        )

        entry_match = entry_pattern.search(section_content)
        if entry_match:
            return entry_match.group(1).strip()

    except OSError as e:
        logger.error(
            f"Failed to read FakeAppId for '{app_id}' from {config_path}: {e}",
            exc_info=True,
        )

    return None

def add_fake_app_id(
    config_path: Path,
    app_id: str,
    game_name: str = "",
    fake_appid: str = "",
) -> bool:
    """Add an AppID to the FakeAppIds list in SLSsteam config.yaml."""
    if not fake_appid:
        fake_appid = get_fake_appid_for_online()

    suffix = "Spacewar" if fake_appid == "480" else "SLSonline"

    try:
        content = _get_config_content_if_enabled(config_path)
        if content is _CONFIG_DISABLED:
            return False
        if content is None:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            entry = f"FakeAppIds:\n  {app_id}: {fake_appid}"
            if game_name:
                entry += f"  # {game_name} -> {suffix}\n"
            else:
                entry += "\n"
            return _atomic_write(config_path, entry)

        bounds = _get_section_bounds(content, "FakeAppIds")

        entry_line = f"  {app_id}: {fake_appid}"
        if game_name:
            entry_line += f"  # {game_name} -> {suffix}\n"
        else:
            entry_line += "\n"

        if bounds:
            _, content_start, section_end = bounds
            section_content = content[content_start:section_end]
            existing_pattern = re.compile(
                rf"^[ \t]*['\"]?{re.escape(str(app_id))}['\"]?[ \t]*:[ \t]*{re.escape(str(fake_appid))}(?:[ \t]+#[^\r\n]*|[ \t]*)$",
                re.MULTILINE,
            )
            if existing_pattern.search(section_content):
                return False

            lines = section_content.splitlines(keepends=True)
            last_entry_end_offset = 0
            curr_offset = 0
            for line in lines:
                s = line.strip()
                if s and (s[0].isdigit() or s[0] in ('"', "'")):
                    last_entry_end_offset = curr_offset + len(line)
                elif not s or s.startswith("#"):
                    pass
                else:
                    break
                curr_offset += len(line)

            if last_entry_end_offset > 0:
                insert_pos = content_start + last_entry_end_offset
            else:
                insert_pos = content_start

            new_content = content[:insert_pos] + entry_line + content[insert_pos:]
        else:
            new_content = content.rstrip() + f"\n\nFakeAppIds:\n{entry_line}"

        if not _atomic_write(config_path, new_content):
            return False

        logger.info(f"Added AppID '{app_id}' to FakeAppIds in {config_path}")
        return True

    except OSError as e:
        logger.error(f"Failed to add FakeAppId '{app_id}': {e}", exc_info=True)
        return False

def remove_fake_app_id(config_path: Path, app_id: str, fake_appid: str = "") -> bool:
    """Remove an AppID from the FakeAppIds list in SLSsteam config.yaml."""
    if fake_appid:
        app_id_pattern = re.compile(
            rf"^[ \t]*['\"]?{re.escape(str(app_id))}['\"]?[ \t]*:[ \t]*{re.escape(str(fake_appid))}[ \t]*(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
    else:
        app_id_pattern = re.compile(
            rf"^[ \t]*(?:['\"]?{re.escape(str(app_id))}['\"]?[ \t]*:[ \t]*[^\r\n#]+|[^\r\n#:]+[ \t]*:[ \t]*['\"]?{re.escape(str(app_id))}['\"]?)[ \t]*(?:#[^\r\n]*)?$",
            re.MULTILINE,
        )
    return _remove_entry_from_section(
        config_path,
        "FakeAppIds",
        app_id_pattern,
        f"Removed AppID '{app_id}' from FakeAppIds in {config_path}",
        f"Failed to remove FakeAppId '{app_id}': {{e}}",
    )

def check_and_merge_fakeappid_db(config_path: Path) -> bool:
    """Check if fakeappid database integration is enabled and merge it if so.

    Returns:
        True if changes were written, False otherwise.
    """
    settings = get_settings()
    if not settings.value("fakeappid_db_integration", False, type=bool):
        return False

    if not is_slssteam_mode_enabled():
        return False

    if not is_slssteam_config_management_enabled():
        return False

    # Get database file path
    from utils.paths import Paths
    db_path = Paths.resource("fakeapps_accela.yaml")
    if not db_path.exists():
        logger.warning(f"Fake AppID database not found at {db_path}")
        return False

    # Parse database FakeAppIds
    db_fakeapps = {}
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line == "FakeAppIds:":
                    continue
                # Split at comment if any
                comment = ""
                if "#" in line:
                    line, comment = line.split("#", 1)
                    comment = comment.strip()
                if ":" in line:
                    k, v = line.split(":", 1)
                    k, v = k.strip(), v.strip()
                    if k.isdigit() and v.isdigit():
                        db_fakeapps[k] = (v, comment)
    except Exception as e:
        logger.error(f"Failed to parse Fake AppID database: {e}")
        return False

    if not db_fakeapps:
        return False

    # Load current content
    try:
        content = _read_config_content(config_path)
        if content is None:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            content = ""
    except OSError as e:
        logger.error(f"Failed to read config file {config_path}: {e}")
        return False
    bounds = _get_section_bounds(content, "FakeAppIds")

    # Let's collect existing FakeAppIds
    existing_fake_apps = {}
    if bounds:
        _, content_start, section_end = bounds
        section_content = content[content_start:section_end]
        entry_pattern = re.compile(r"^[ \t]*(\d+)[ \t]*:[ \t]*(\d+)", re.MULTILINE)
        for m in entry_pattern.finditer(section_content):
            existing_fake_apps[m.group(1).strip()] = m.group(2).strip()

    # Determine which entries are missing
    missing_entries = {}
    for appid, (fake_appid, comment) in db_fakeapps.items():
        if appid not in existing_fake_apps:
            missing_entries[appid] = (fake_appid, comment)

    if not missing_entries:
        logger.debug("No missing FakeAppIds to merge.")
        return False

    logger.info(f"Merging {len(missing_entries)} entries from database into SLSsteam FakeAppIds...")
    
    # Create the text block to insert
    insert_text = ""
    for appid, (fake_appid, comment) in missing_entries.items():
        comment_suffix = f" # {comment}" if comment else ""
        insert_text += f"  {appid}: {fake_appid}{comment_suffix}\n"

    if bounds:
        _, content_start, section_end = bounds
        new_content = content[:content_start] + insert_text + content[content_start:]
    else:
        new_content = content.rstrip() + "\n\nFakeAppIds:\n" + insert_text

    # Write atomically
    _create_backup(config_path)
    if _atomic_write(config_path, new_content):
        logger.info(f"Successfully merged Fake AppID database into {config_path}")
        return True
    return False

def clean_fakeappid_db(config_path: Path) -> bool:
    """Remove all FakeAppIds that belong to the database from SLSsteam config.yaml.

    Returns:
        True if changes were written, False otherwise.
    """
    if not config_path.exists():
        return False

    from utils.paths import Paths
    db_path = Paths.resource("fakeapps_accela.yaml")
    if not db_path.exists():
        return False

    # Parse database AppIDs
    db_appids = set()
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line == "FakeAppIds:":
                    continue
                if "#" in line:
                    line, _ = line.split("#", 1)
                if ":" in line:
                    k, _ = line.split(":", 1)
                    k = k.strip()
                    if k.isdigit():
                        db_appids.add(k)
    except Exception as e:
        logger.error(f"Failed to parse Fake AppID database: {e}")
        return False

    if not db_appids:
        return False

    try:
        content = _read_config_content(config_path)
        if not content:
            return False
    except OSError as e:
        logger.error(f"Failed to read config file {config_path}: {e}")
        return False

    bounds = _get_section_bounds(content, "FakeAppIds")
    if not bounds:
        return False

    _, content_start, section_end = bounds
    section_content = content[content_start:section_end]

    # Rebuild section content, omitting any lines that match db_appids
    new_section_lines = []
    removed_count = 0
    entry_pattern = re.compile(r"^[ \t]*(\d+)[ \t]*:")
    for line in section_content.splitlines():
        m = entry_pattern.match(line)
        if m:
            appid = m.group(1).strip()
            if appid in db_appids:
                removed_count += 1
                continue
        new_section_lines.append(line)

    if removed_count == 0:
        return False

    new_section_content = "\n".join(new_section_lines)
    if new_section_content and not new_section_content.endswith("\n"):
        new_section_content += "\n"
    new_content = content[:content_start] + new_section_content + content[section_end:]

    _create_backup(config_path)
    if _atomic_write(config_path, new_content):
        logger.info(f"Successfully cleaned {removed_count} database FakeAppIds from {config_path}")
        return True
    return False
