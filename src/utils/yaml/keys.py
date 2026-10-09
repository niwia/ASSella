"""DecryptionKeys section.

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
from utils.yaml.core import _get_section_bounds, _read_config_content, batch_config_edit, get_user_config_path
from utils.yaml.plugins import ensure_plugins_enabled

logger = logging.getLogger(__name__)


def add_decryption_keys_batch(
    config_path: Path,
    keys: Union[Dict[Union[str, int], Union[Tuple[str, str], str]], List[Union[Tuple[Union[str, int], str, str], Tuple[Union[str, int], str]]]],
) -> bool:
    """Add or update multiple depot AES decryption keys in DecryptionKeys in a single atomic write."""
    if not keys:
        return True
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        any_added = False
        if isinstance(keys, dict):
            for depot_id, val in keys.items():
                if isinstance(val, (tuple, list)) and len(val) >= 2:
                    k, comm = val[0], val[1]
                else:
                    k, comm = str(val), ""
                if editor.add_key(depot_id, k, comment=comm):
                    any_added = True
        elif isinstance(keys, (list, tuple)):
            for item in keys:
                if len(item) == 3:
                    did, k, comm = item
                elif len(item) == 2:
                    did, k = item
                    comm = ""
                else:
                    continue
                if editor.add_key(did, k, comment=comm):
                    any_added = True
    return any_added

def get_decryption_keys(config_path: Path) -> Dict[str, str]:
    """Get mapping of {depot_id: key} currently in DecryptionKeys section."""
    content = _read_config_content(config_path)
    if not content:
        return {}
    bounds = _get_section_bounds(content, "DecryptionKeys")
    if not bounds:
        return {}
    _, content_start, section_end = bounds
    sec = content[content_start:section_end]
    results = {}
    key_pattern = re.compile(
        r"^[ \t]*['\"]?([0-9]+)['\"]?[ \t]*:[ \t]*['\"]?([a-fA-F0-9]{64})['\"]?",
        re.MULTILINE,
    )
    for m in key_pattern.finditer(sec):
        results[m.group(1)] = m.group(2)
    return results

def add_decryption_key(config_path: Path, depot_id: Union[str, int], key: str, comment: str = "") -> bool:
    """Add or update a depot AES decryption key in DecryptionKeys section in SLSsteam config.yaml."""
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        res = editor.add_key(depot_id, key, comment)
    return res and editor.committed_successfully

def remove_decryption_key(
    config_path: Path,
    depot_id: Union[str, int],
    check_shared: bool = True,
    excluding_appid: Optional[Union[str, int]] = None,
) -> bool:
    """Remove a depot key from DecryptionKeys section in SLSsteam config.yaml.
    If check_shared is True, skips removal if the depot is shared with another game or is a common redistributable.
    """
    ensure_plugins_enabled(config_path)
    with batch_config_edit(config_path) as editor:
        res = editor.remove_key(depot_id, check_shared=check_shared, excluding_appid=excluding_appid)
    return res and editor.committed_successfully

def has_game_decryption_keys(
    appid: Union[str, int], game_data: Optional[Dict[str, Any]] = None
) -> bool:
    """Check if an ASSella-mode game has an active, non-shared DecryptionKey in SLSsteam config.yaml.
    Strictly ignores universal shared redistributables.

    Returns True if:
      - The root AppKey (appid) is present in DecryptionKeys (non-shared)
      - Any known non-shared depot belonging to this game has a key in DecryptionKeys
      - Any non-shared key entry in DecryptionKeys has a comment explicitly referencing
        the AppID or game name
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

    live_keys = get_decryption_keys(cfg_path)
    if not live_keys:
        return False

    # 1. Root AppKey in DecryptionKeys (strictly non-shared)
    if appid_str in live_keys and appid_str not in all_shared:
        return True

    # 2. Collect known depots for this game
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

    try:
        from utils.plugin_games import load_plugin_library
        lib = load_plugin_library()
        if appid_str in lib:
            game_depots.update(str(d).strip() for d in lib[appid_str].get("depots", []))
            game_depots.update(str(d).strip() for d in lib[appid_str].get("keys", {}).keys())
    except Exception:
        pass

    non_shared_game_depots = (game_depots - all_shared) - {appid_str}
    if any(d in live_keys for d in non_shared_game_depots):
        return True

    # 3. Check inline comments in DecryptionKeys specifically referencing appid_str or game_name
    try:
        txt = cfg_path.read_text(encoding="utf-8", errors="ignore")
        bounds = _get_section_bounds(txt, "DecryptionKeys")
        if bounds:
            sec_text = txt[bounds[1]:bounds[2]]
            game_name = (
                (gd.get("game_name") or gd.get("name") or "").strip().lower()
                if gd else ""
            )
            for line in sec_text.splitlines():
                m = re.match(r"^[ \t]*['\"]?(\d+)['\"]?[ \t]*:[ \t]*[^\r\n#]+(?:#[ \t]*(.*))?$", line)
                if m:
                    item_id, comment = m.group(1), (m.group(2) or "").lower()
                    if item_id in all_shared:
                        continue
                    if appid_str in comment or (game_name and len(game_name) > 3 and game_name in comment):
                        return True
    except Exception:
        pass

    return False
