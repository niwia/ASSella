"""AppTokens section.

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
from typing import Dict, Tuple

from utils.yaml.core import (
    _CONFIG_DISABLED,
    _atomic_write,
    _add_map_item_in_memory,
    _get_config_content_if_enabled,
    _get_section_bounds,
    _read_config_content,
    _remove_entry_from_section,
    batch_config_edit,
)
from utils.yaml import core as _core

logger = logging.getLogger(__name__)


def _get_app_tokens_section(content: str) -> str:
    """Extract the AppTokens section from YAML content."""
    bounds = _get_section_bounds(content, "AppTokens")
    if not bounds:
        return ""
    _, content_start, section_end = bounds
    return content[content_start:section_end]

def _fix_app_tokens_indentation(content: str) -> Tuple[str, bool]:
    """Fix indentation of AppTokens entries to have 2-space indentation."""
    bounds = _get_section_bounds(content, "AppTokens")
    if not bounds:
        return content, False

    _, content_start, section_end = bounds
    section_content = content[content_start:section_end]

    token_pattern = re.compile(r"^[ \t]*(\d+[ \t]*:[^\r\n]*)", re.MULTILINE)
    fixed_section = token_pattern.sub(r"  \1", section_content)

    if fixed_section != section_content:
        fixed_content = content[:content_start] + fixed_section + content[section_end:]
        logger.debug("Fixed indentation of AppTokens entries")
        return fixed_content, True

    return content, False

def add_app_token(config_path: Path, app_id: str, token: str) -> bool:
    """Add or update an AppToken in the AppTokens section in SLSsteam config.yaml."""
    try:
        if not config_path.exists():
            return False
        if not _core._config_management_enabled():
            return False

        app_id_str = str(app_id).strip()
        token_str = str(token).strip()
        if not app_id_str or not token_str:
            return False

        with batch_config_edit(config_path) as editor:
            content, fixed_indent = _fix_app_tokens_indentation(editor.content)
            bounds = _get_section_bounds(content, "AppTokens")
            modified = bool(fixed_indent)
            changed = False

            if bounds:
                _, content_start, section_end = bounds
                sec_content = content[content_start:section_end]

                dup_pattern = re.compile(
                    rf"^[ \t]*['\"]?{re.escape(app_id_str)}['\"]?[ \t]*:[ \t]*([^\r\n#]+)(?:#[^\r\n]*)?$",
                    re.MULTILINE,
                )
                matches = list(dup_pattern.finditer(sec_content))

                if len(matches) == 1:
                    existing_val = matches[0].group(1).strip().strip('"').strip("'")
                    if existing_val == token_str and not fixed_indent:
                        return False

                if matches:
                    # Keep the section single-valued: write the requested token
                    # and drop any duplicates rather than producing a duplicate key.
                    while True:
                        b = _get_section_bounds(content, "AppTokens")
                        if not b:
                            break
                        _, cs, se = b
                        sc = content[cs:se]
                        m = dup_pattern.search(sc)
                        if not m:
                            break
                        abs_m_start = cs + m.start()
                        l_start = content.rfind("\n", 0, abs_m_start)
                        l_start = 0 if l_start == -1 else l_start + 1
                        l_end = content.find("\n", abs_m_start)
                        l_end = len(content) if l_end == -1 else l_end + 1
                        content = content[:l_start] + content[l_end:]
                        modified = True

            new_content, changed = _add_map_item_in_memory(
                content, "AppTokens", app_id_str, token_str
            )
            if changed:
                content = new_content
                modified = True
            if modified:
                editor.content = content
                editor.has_changes = True

        if not editor.committed_successfully:
            return False
        if changed:
            logger.info(f"Updated AppToken for '{app_id_str}'")
        elif modified:
            logger.info(f"Cleaned up AppTokens for '{app_id_str}'")
        return modified

    except OSError as e:
        logger.error(f"Failed to add AppToken '{app_id}': {e}", exc_info=True)
        return False

def get_app_tokens(config_path: Path) -> Dict[str, str]:
    """Get all AppTokens from SLSsteam config.yaml."""
    tokens = {}
    try:
        if not config_path.exists():
            return tokens

        content = _read_config_content(config_path)
        if not content:
            return tokens

        bounds = _get_section_bounds(content, "AppTokens")
        if not bounds:
            return tokens

        _, content_start, section_end = bounds
        section_content = content[content_start:section_end]

        token_pattern = re.compile(
            r"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*([^\r\n#]+)",
            re.MULTILINE,
        )

        for token_match in token_pattern.finditer(section_content):
            app_id = token_match.group(1).strip()
            token = token_match.group(2).strip().strip('"').strip("'")
            tokens[app_id] = token

    except OSError as e:
        logger.error(f"Failed to read AppTokens from {config_path}: {e}", exc_info=True)

    return tokens

def remove_app_token(config_path: Path, app_id: str) -> bool:
    """Remove an AppID entry from the AppTokens section in SLSsteam config.yaml."""
    app_id_pattern = re.compile(
        rf"^[ \t]*['\"]?{re.escape(str(app_id))}['\"]?[ \t]*:[ \t]*[^\r\n#]+[ \t]*(?:#[^\r\n]*)?$",
        re.MULTILINE,
    )
    return _remove_entry_from_section(
        config_path,
        "AppTokens",
        app_id_pattern,
        f"Removed AppID '{app_id}' from AppTokens in {config_path}",
        f"Failed to remove AppToken for '{app_id}': {{e}}",
    )
