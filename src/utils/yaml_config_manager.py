"""Facade for the SLSsteam ``config.yaml`` manager.

The implementation lives in :mod:`utils.yaml`, split by responsibility:

===========================  ==================================================
:mod:`utils.yaml.constants`  Shared constant sets; no dependencies.
:mod:`utils.yaml.modes`      Feature-flag queries.
:mod:`utils.yaml.validation` Content validation and identifier sanitising.
:mod:`utils.yaml.core`       File I/O, the hand-rolled section parser, and
                             ``BatchConfigEditor`` - the transaction primitive
                             every section writer goes through.
:mod:`utils.yaml.yaml_values` Generic scalar get/set.
:mod:`utils.yaml.dlc` / ``keys`` / ``tokens`` / ``apps`` / ``fakeappid``
                             One module per config.yaml section.
:mod:`utils.yaml.plugins` / ``slssteam``
                             Plugin deployment and SLSsteam prerequisites.
:mod:`utils.yaml.repair`     Structural repair of hand-edited files.
:mod:`utils.yaml.maintenance`  Startup backup, permissions, Denuvo, Netsock,
                             LaunchOptions.
===========================  ==================================================

**This module stays the import target.** All 29 importing modules across
``utils``, ``core``, ``managers`` and ``ui`` are unchanged; the names are
re-exported here. Import from ``utils.yaml.*`` directly only for the leaves.

Re-exporting rather than repointing the importers keeps every consumer
byte-identical while the implementation moves underneath, which makes a revert a
one-file operation. The facade is a strict superset of the previous module:
every name that used to be importable still is.
"""

import logging

from utils.settings import get_settings  # noqa: F401

from utils.yaml.apps import (  # noqa: F401
    _append_to_additional_apps,
    _init_config_with_app,
    add_additional_app,
    add_additional_apps_batch,
    add_additional_depot,
    add_additional_depots_batch,
    get_additional_apps,
    get_additional_depots,
    has_game_config_entries,
    remove_additional_app,
    remove_additional_depot,
    replace_additional_app,
)

from utils.yaml.constants import (  # noqa: F401
    BACKUP_SUFFIX,
    HEX_64_PATTERN,
    NUMERIC_ID_PATTERN,
    SHARED_REDISTS,
    TOP_LEVEL_KEY_PATTERN,
)

from utils.yaml.core import (  # noqa: F401
    BatchConfigEditor,
    _CONFIG_DISABLED,
    _add_dlc_batch_in_memory,
    _add_list_item_in_memory,
    _add_map_item_in_memory,
    _atomic_write,
    _config_management_enabled,
    _create_backup,
    _expand_flow_section_if_needed,
    _find_section_insert_pos,
    _fix_additional_apps_indentation,
    _get_config_content_if_enabled,
    _get_section_bounds,
    _get_section_end,
    _get_section_start,
    _read_config_content,
    _remove_entry_from_section,
    _remove_entry_in_memory,
    _restrict_config_permissions,
    _write_in_place,
    batch_config_edit,
    calculate_file_sha256,
    get_user_config_path,
    is_depot_shared_with_other_games,
)

from utils.yaml.dlc import (  # noqa: F401
    add_dlc_data,
    add_dlc_data_batch,
    get_dlc_data,
    remove_dlc_data,
)

from utils.yaml.fakeappid import (  # noqa: F401
    add_fake_app_id,
    check_and_merge_fakeappid_db,
    clean_fakeappid_db,
    get_fake_app_ids,
    get_fake_appid,
    remove_fake_app_id,
)

from utils.yaml.keys import (  # noqa: F401
    add_decryption_key,
    add_decryption_keys_batch,
    get_decryption_keys,
    has_game_decryption_keys,
    remove_decryption_key,
)

from utils.yaml.maintenance import (  # noqa: F401
    add_launch_option,
    backup_config_on_startup,
    clean_denuvo_games_section,
    ensure_netsock_binary,
    find_existing_netsock_so,
    get_denuvo_games,
    get_launch_option,
    get_netsock_so_launch_path,
    get_netsock_tools_dir,
    harden_existing_config_permissions,
    remove_launch_option,
    save_denuvo_games,
)

from utils.yaml.modes import (  # noqa: F401
    get_fake_appid_for_online,
    is_greenluma_wrapper_mode_enabled,
    is_slssteam_config_management_enabled,
    is_slssteam_mode_enabled,
)

from utils.yaml.plugins import (  # noqa: F401
    are_sls_plugins_deployed,
    deploy_all_sls_plugins,
    deploy_sls_plugin,
    ensure_plugins_enabled,
    get_sls_plugins_dirs,
    is_slssteam_plugins_enabled,
    sync_plugins_on_startup,
)

from utils.yaml.repair import (  # noqa: F401
    fix_slssteam_config_indentation,
)

from utils.yaml.slssteam import (  # noqa: F401
    ensure_slssteam_api_enabled,
    ensure_slssteam_logging_enabled,
    ensure_slssteam_prerequisites,
    ensure_smart_tickets_enabled,
    is_smart_tickets_enabled,
)

from utils.yaml.tokens import (  # noqa: F401
    _fix_app_tokens_indentation,
    _get_app_tokens_section,
    add_app_token,
    get_app_tokens,
    remove_app_token,
)

from utils.yaml.validation import (  # noqa: F401
    _is_valid_hex64,
    _sanitize_comment,
    _sanitize_id,
    _validate_yaml_content,
)

from utils.yaml.yaml_values import (  # noqa: F401
    get_yaml_boolean_value,
    update_yaml_boolean_value,
)

logger = logging.getLogger(__name__)
