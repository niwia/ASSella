"""AdditionalApps and AdditionalDepots sections.

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
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from utils.yaml.constants import SHARED_REDISTS
from utils.yaml.core import _atomic_write, _get_section_bounds, _read_config_content, batch_config_edit, get_user_config_path
from utils.yaml.dlc import get_dlc_data
from utils.yaml.keys import get_decryption_keys
from utils.yaml.plugins import ensure_plugins_enabled
from utils.yaml.validation import _sanitize_comment, _sanitize_id

logger = logging.getLogger(__name__)


def _init_config_with_app(config_path: Path, app_id: str, comment: str) -> bool:
    """Create new config file with Plugins: yes and a single AdditionalApps entry."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if comment:
        new_entry = f"Plugins: yes\nAdditionalApps:\n  - {app_id} # {comment}\n"
    else:
        new_entry = f"Plugins: yes\nAdditionalApps:\n  - {app_id}\n"

    if _atomic_write(config_path, new_entry):
        logger.info(f"Created config file with Plugins: yes and AppID '{app_id}' in {config_path}")
        return True
    return False

def _append_to_additional_apps(
    content: str, app_id: str, comment: str, bounds: Tuple[int, int, int]
) -> str:
    """Append AppID to existing AdditionalApps section directly after the last list item."""
    _, content_start, section_end = bounds
    sec_content = content[content_start:section_end]
    lines = sec_content.splitlines(keepends=True)

    last_item_end_offset = 0
    curr_offset = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("-"):
            last_item_end_offset = curr_offset + len(line)
        elif stripped and not stripped.startswith("#") and not line.startswith(" ") and not line.startswith("\t"):
            break
        curr_offset += len(line)

    entry_line = f"  - {app_id} # {comment}\n" if comment else f"  - {app_id}\n"
    if last_item_end_offset > 0:
        insert_pos = content_start + last_item_end_offset
    else:
        insert_pos = content_start

    return content[:insert_pos] + entry_line + content[insert_pos:]

def add_additional_apps_batch(
    config_path: Path, apps: List[Tuple[Union[str, int], str]]
) -> bool:
    """Add multiple AppIDs to AdditionalApps in SLSsteam config.yaml in a single atomic write."""
    if not apps:
        return True
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        any_added = False
        for app_id, comment in apps:
            if editor.add_app(app_id, comment):
                any_added = True
    return any_added

def add_additional_depots_batch(
    config_path: Path, depots: List[Tuple[Union[str, int], str]]
) -> bool:
    """Add multiple DepotIDs to AdditionalDepots in SLSsteam config.yaml in a single atomic write."""
    if not depots:
        return True
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        any_added = False
        for depot_id, comment in depots:
            if editor.add_depot(depot_id, comment):
                any_added = True
    return any_added

def add_additional_app(config_path: Path, app_id: Union[str, int], comment: str = "") -> bool:
    """Add an AppID to the AdditionalApps list in SLSsteam config.yaml."""
    ensure_plugins_enabled(config_path)
    if not config_path.exists():
        app_id_clean = _sanitize_id(app_id)
        if not app_id_clean:
            return False
        return _init_config_with_app(config_path, app_id_clean, _sanitize_comment(comment))
    with batch_config_edit(config_path) as editor:
        res = editor.add_app(app_id, comment)
    return res and editor.committed_successfully

def remove_additional_app(config_path: Path, app_id: Union[str, int]) -> bool:
    """Remove an AppID from the AdditionalApps list."""
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        res = editor.remove_app(app_id)
    return res and editor.committed_successfully

def replace_additional_app(config_path: Path, old_app_id: Union[str, int], new_app_id: Union[str, int], new_comment: str = "") -> bool:
    """Replace an existing AppID in AdditionalApps with a new AppID and optional comment.
    Also migrates any DlcData or FakeAppIds entries if present.
    """
    content = _read_config_content(config_path)
    if not content:
        return False

    old_aid_str = _sanitize_id(old_app_id)
    new_aid_str = _sanitize_id(new_app_id)
    if not old_aid_str or not new_aid_str:
        return False

    bounds = _get_section_bounds(content, "AdditionalApps")
    if not bounds:
        return False

    _, content_start, section_end = bounds
    sec_content = content[content_start:section_end]
    pattern = re.compile(rf"^[ \t]*-[ \t]*{re.escape(old_aid_str)}[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
    match = pattern.search(sec_content)
    if not match:
        logger.warning(f"AppID '{old_aid_str}' not found in AdditionalApps section of {config_path}")
        return False

    # If new_comment not explicitly provided, preserve existing comment if any
    if not new_comment:
        m_comm = re.search(rf"^[ \t]*-[ \t]*{re.escape(old_aid_str)}[ \t]*#[ \t]*(.+)$", sec_content, re.MULTILINE)
        if m_comm:
            new_comment = m_comm.group(1).strip()

    new_comment = _sanitize_comment(new_comment)
    replacement_line = f"  - {new_aid_str} # {new_comment}" if new_comment else f"  - {new_aid_str}"
    new_sec_content = pattern.sub(replacement_line, sec_content, count=1)
    updated_content = content[:content_start] + new_sec_content + content[section_end:]

    # Migrate DlcData section key if present
    dlc_bounds = _get_section_bounds(updated_content, "DlcData")
    if dlc_bounds:
        _, d_start, d_end = dlc_bounds
        d_sec = updated_content[d_start:d_end]
        d_pat = re.compile(rf"^[ \t]+{re.escape(old_aid_str)}:[ \t]*(?:#[^\r\n]*)?$", re.MULTILINE)
        if d_pat.search(d_sec):
            new_d_sec = d_pat.sub(f"  {new_aid_str}:", d_sec, count=1)
            updated_content = updated_content[:d_start] + new_d_sec + updated_content[d_end:]

    # Migrate FakeAppIds section key if present
    fake_bounds = _get_section_bounds(updated_content, "FakeAppIds")
    if fake_bounds:
        _, f_start, f_end = fake_bounds
        f_sec = updated_content[f_start:f_end]
        f_pat = re.compile(rf"^([ \t]*['\"]?){re.escape(old_aid_str)}(['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[^\r\n]*)?)$", re.MULTILINE)
        if f_pat.search(f_sec):
            new_f_sec = f_pat.sub(rf"\g<1>{new_aid_str}\g<2>", f_sec, count=1)
            updated_content = updated_content[:f_start] + new_f_sec + updated_content[f_end:]

    if _atomic_write(config_path, updated_content):
        logger.info(f"Successfully replaced AppID '{old_aid_str}' with '{new_aid_str}' in {config_path}")
        return True
    return False

def get_additional_apps(config_path: Path) -> List[str]:
    """Get list of AppIDs currently in AdditionalApps section."""
    content = _read_config_content(config_path)
    if not content:
        return []
    bounds = _get_section_bounds(content, "AdditionalApps")
    if not bounds:
        return []
    _, content_start, section_end = bounds
    sec = content[content_start:section_end]
    results = []
    for line in sec.splitlines():
        m = re.match(r"^[ \t]*-[ \t]*([0-9]+)", line)
        if m:
            results.append(m.group(1))
    return results

def get_additional_depots(config_path: Path) -> List[str]:
    """Get list of DepotIDs currently in AdditionalDepots section."""
    content = _read_config_content(config_path)
    if not content:
        return []
    bounds = _get_section_bounds(content, "AdditionalDepots")
    if not bounds:
        return []
    _, content_start, section_end = bounds
    sec = content[content_start:section_end]
    results = []
    for line in sec.splitlines():
        m = re.match(r"^[ \t]*-[ \t]*([0-9]+)", line)
        if m:
            results.append(m.group(1))
    return results

def has_game_config_entries(
    appid: Union[str, int], game_data: Optional[Dict[str, Any]] = None
) -> bool:
    """Check if an ASSella-mode game has active entries in SLSsteam config.yaml.
    Strictly ignores universal shared redistributables.

    Returns True if the game qualifies by having:
      - Its AppID in AdditionalApps
      - Any DLC AppID in AdditionalApps or dlc_data
      - Any non-shared depot in AdditionalDepots
      - Any non-shared decryption key in DecryptionKeys (including root AppKey)
      - Explicitly tagged comments referencing the AppID or game name in config.yaml
      - Or registration in plugin_library.json
    """
    appid_str = str(appid).strip()
    if not appid_str or not appid_str.isdigit():
        return False

    cfg_path = get_user_config_path()
    if not cfg_path or not cfg_path.exists():
        return False

    try:
        from ui.assets import DEPOT_BLACKLIST
        all_shared = {str(d) for d in DEPOT_BLACKLIST} | SHARED_REDISTS
    except Exception:
        all_shared = SHARED_REDISTS

    # 1. Plugin library registration
    try:
        from utils.plugin_games import load_plugin_library
        lib = load_plugin_library()
        if appid_str in lib:
            return True
    except Exception:
        pass

    # 2. Check dlc_data batch block in config.yaml
    try:
        dlc_data = get_dlc_data(cfg_path)
        if appid_str in dlc_data:
            return True
    except Exception:
        pass

    # 3. Check AdditionalApps
    try:
        live_apps = get_additional_apps(cfg_path)
        if appid_str in live_apps:
            return True
        gd = game_data or {}
        gd_dlcs = gd.get("dlcs") or {}
        for dlc_id in gd_dlcs.keys():
            if str(dlc_id).strip() in live_apps:
                return True
    except Exception:
        pass

    # Gather known depots for this game
    game_depots: Set[str] = set()
    gd = game_data or {}
    if gd.get("depots"):
        game_depots.update(str(d).strip() for d in gd["depots"].keys())

    try:
        from managers.depot_key_manager import DepotKeyManager
        dkm = DepotKeyManager.get_instance()
        local_keys = dkm.get_keys_for_app(appid_str)
        if local_keys:
            game_depots.update(str(d).strip() for d in local_keys.keys())
    except Exception:
        pass

    non_shared_game_depots = (game_depots - all_shared) - {appid_str}

    # 4. Check AdditionalDepots (strictly non-shared)
    try:
        live_depots = get_additional_depots(cfg_path)
        if any(d in live_depots for d in non_shared_game_depots):
            return True
    except Exception:
        pass

    # 5. Check DecryptionKeys (strictly non-shared)
    try:
        live_keys = get_decryption_keys(cfg_path)
        if appid_str in live_keys:
            return True
        if any(d in live_keys for d in non_shared_game_depots):
            return True
    except Exception:
        pass

    # 6. Check tagged comments specifically mentioning appid_str or game_name
    try:
        txt = cfg_path.read_text(encoding="utf-8", errors="ignore")
        game_name = (
            (game_data.get("game_name") or game_data.get("name") or "").strip().lower()
            if game_data
            else ""
        )

        for sec_name in ("AdditionalApps", "AdditionalDepots", "DecryptionKeys"):
            bounds = _get_section_bounds(txt, sec_name)
            if not bounds:
                continue
            sec_text = txt[bounds[1]:bounds[2]]
            for line in sec_text.splitlines():
                m = re.match(r"^[ \t]*-[ \t]*['\"]?(\d+)['\"]?[ \t]*(?:#[ \t]*(.*))?$", line)
                if not m:
                    m = re.match(r"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[ \t]*(.*))?$", line)
                if m:
                    item_id, comment = m.group(1), (m.group(2) or "").lower()
                    if sec_name in ("AdditionalDepots", "DecryptionKeys") and item_id in all_shared:
                        continue
                    if appid_str in comment or (game_name and len(game_name) > 3 and game_name in comment):
                        return True
    except Exception:
        pass

    return False

def add_additional_depot(
    config_path: Path,
    depot_id: Union[str, int],
    comment: str = "",
    app_id: Optional[Union[str, int]] = None,
) -> bool:
    """Add a DepotID to the AdditionalDepots list in SLSsteam config.yaml."""
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        res = editor.add_depot(depot_id, comment, app_id=app_id)
    return res and editor.committed_successfully

def remove_additional_depot(
    config_path: Path,
    depot_id: Union[str, int],
    check_shared: bool = True,
    excluding_appid: Optional[Union[str, int]] = None,
) -> bool:
    """Remove a DepotID from the AdditionalDepots list in SLSsteam config.yaml.
    If check_shared is True, skips removal if the depot is shared with another game or is a common redistributable.
    """
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        res = editor.remove_depot(depot_id, check_shared=check_shared, excluding_appid=excluding_appid)
    return res and editor.committed_successfully
