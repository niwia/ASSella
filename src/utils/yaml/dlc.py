"""DlcData section.

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
from typing import Dict, Optional

from utils.yaml.core import _CONFIG_DISABLED, _atomic_write, _get_config_content_if_enabled, _get_section_bounds

logger = logging.getLogger(__name__)


def add_dlc_data(
    config_path: Path, parent_app_id: str, dlc_id: str, dlc_name: str
) -> bool:
    """Add a DLC entry to DlcData section in SLSsteam config.yaml."""
    return add_dlc_data_batch(config_path, parent_app_id, {str(dlc_id): dlc_name})

def add_dlc_data_batch(
    config_path: Path, parent_app_id: str, dlc_dict: Dict[str, str]
) -> bool:
    """Add multiple DLC entries under parent_app_id in DlcData section in SLSsteam config.yaml."""
    if not dlc_dict:
        return True
    try:
        content = _get_config_content_if_enabled(config_path, log_missing=True)
        if content is _CONFIG_DISABLED or content is None:
            return False

        parent_app_id = str(parent_app_id).strip()
        bounds = _get_section_bounds(content, "DlcData")

        if not bounds:
            # Create new DlcData section
            lines = ["DlcData:", f"  {parent_app_id}:"]
            for did, dname in dlc_dict.items():
                cname = str(dname or f"DLC {did}").replace('"', '\\"')
                lines.append(f'    {did}: "{cname}"')
            new_entry = "\n".join(lines) + "\n"
            new_content = content.rstrip() + "\n\n" + new_entry
            return _atomic_write(config_path, new_content)

        _, dlc_start, dlc_end = bounds
        dlc_section = content[dlc_start:dlc_end]

        parent_pattern = re.compile(rf"^[ \t]+{re.escape(parent_app_id)}:[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
        parent_match = parent_pattern.search(dlc_section)

        if not parent_match:
            # Parent AppID does not exist under DlcData yet. Insert it.
            lines = [f"  {parent_app_id}:"]
            for did, dname in dlc_dict.items():
                cname = str(dname or f"DLC {did}").replace('"', '\\"')
                lines.append(f'    {did}: "{cname}"')
            insert_text = "\n".join(lines) + "\n"
            insert_pos = dlc_end
            new_content = content[:insert_pos].rstrip() + "\n" + insert_text + "\n" + content[insert_pos:].lstrip("\n")
            return _atomic_write(config_path, new_content)

        # Parent exists in DlcData section.
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
            check_pat = re.compile(rf'^[ \t]*{re.escape(str(did))}[ \t]*:[ \t]*"', re.MULTILINE)
            if not check_pat.search(parent_block):
                cname = str(dname or f"DLC {did}").replace('"', '\\"')
                new_dlc_lines.append(f'    {did}: "{cname}"')

        if not new_dlc_lines:
            return True  # All already exist

        insert_text = "\n".join(new_dlc_lines) + "\n"
        new_content = content[:parent_end].rstrip() + "\n" + insert_text + content[parent_end:]
        return _atomic_write(config_path, new_content)

    except Exception as e:
        logger.error(f"Failed to add DLC batch for '{parent_app_id}': {e}", exc_info=True)
        return False

def remove_dlc_data(
    config_path: Path, parent_app_id: str, dlc_id: Optional[str] = None
) -> bool:
    """Remove a DLC or entire parent_app_id from DlcData section in SLSsteam config.yaml."""
    try:
        content = _get_config_content_if_enabled(config_path, log_missing=True)
        if content is _CONFIG_DISABLED or content is None:
            return False

        parent_app_id = str(parent_app_id).strip()
        bounds = _get_section_bounds(content, "DlcData")
        if not bounds:
            return True

        _, dlc_start, dlc_end = bounds
        dlc_section = content[dlc_start:dlc_end]
        parent_pattern = re.compile(rf"^[ \t]+{re.escape(parent_app_id)}:[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
        parent_match = parent_pattern.search(dlc_section)
        if not parent_match:
            return True

        p_line_start = dlc_start + parent_match.start()
        p_start = dlc_start + parent_match.end()
        if p_start < len(content) and content[p_start] == "\r":
            p_start += 1
        if p_start < len(content) and content[p_start] == "\n":
            p_start += 1

        p_after = content[p_start:dlc_end]
        next_parent = re.search(r"^[ \t]+[0-9A-Za-z_]+:[ \t]*(?:#[^\r\n]*)?$", p_after, re.MULTILINE)
        p_block_end = (p_start + next_parent.start()) if next_parent else dlc_end

        if dlc_id is None:
            # Remove entire parent section
            new_content = content[:p_line_start] + content[p_block_end:]
            return _atomic_write(config_path, new_content)
        else:
            # Remove specific DLC line
            dlc_pattern = re.compile(
                rf"^[ \t]*{re.escape(str(dlc_id))}[ \t]*:[^\r\n]*\r?\n?", re.MULTILINE
            )
            target_block = content[p_start:p_block_end]
            new_block, count = dlc_pattern.subn("", target_block)
            if count > 0:
                new_content = content[:p_start] + new_block + content[p_block_end:]
                return _atomic_write(config_path, new_content)
            return True

    except Exception as e:
        logger.error(f"Failed to remove DLC data for '{parent_app_id}': {e}", exc_info=True)
        return False

def get_dlc_data(config_path: Path, parent_app_id: str) -> Dict[str, str]:
    """Retrieve all DLC entries for a given parent_app_id under DlcData in SLSsteam config.yaml."""
    try:
        content = _get_config_content_if_enabled(config_path)
        if content is _CONFIG_DISABLED or content is None:
            return {}

        parent_app_id = str(parent_app_id).strip()
        bounds = _get_section_bounds(content, "DlcData")
        if not bounds:
            return {}

        _, dlc_start, dlc_end = bounds
        dlc_section = content[dlc_start:dlc_end]
        parent_pattern = re.compile(rf"^[ \t]+{re.escape(parent_app_id)}:[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
        parent_match = parent_pattern.search(dlc_section)
        if not parent_match:
            return {}

        p_start = dlc_start + parent_match.end()
        if p_start < len(content) and content[p_start] == "\r":
            p_start += 1
        if p_start < len(content) and content[p_start] == "\n":
            p_start += 1

        p_after = content[p_start:dlc_end]
        next_parent = re.search(r"^[ \t]+[0-9A-Za-z_]+:[ \t]*(?:#[^\r\n]*)?$", p_after, re.MULTILINE)
        p_end = (p_start + next_parent.start()) if next_parent else dlc_end

        result = {}
        for line in content[p_start:p_end].splitlines():
            m = re.match(r'^[ \t]*([0-9]+)[ \t]*:[ \t]*"(.*)"[ \t]*(?:#[^\r\n]*)?$', line)
            if m:
                result[m.group(1)] = m.group(2)
        return result
    except Exception:
        return {}
