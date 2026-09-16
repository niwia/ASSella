"""
Modular game details package for GameDetailsDialogV2.
"""

from ui.dialogs.game_details.widgets import (
    SwitchToggle,
    CenteredComboBox,
    HeroBanner,
    MaterialTile,
)

from ui.dialogs.game_details.hero_header import (
    load_hero_image,
    init_hero_v2,
    init_hero_legacy,
    update_title,
    thin_line,
    section_title,
    format_time_diff,
    get_manifest_age,
    get_lua_age,
    get_last_checked,
)

from ui.dialogs.game_details.info_tab import init_info_tab

from ui.dialogs.game_details.builds_tab import (
    init_builds_tab,
    fetch_steamdb_builds_async,
    on_builds_loaded,
    on_builds_error,
    populate_builds_cards,
    on_build_card_clicked,
    on_build_depots_loaded,
    on_build_depots_error,
    on_builds_download_clicked,
)

from ui.dialogs.game_details.tools_tab import (
    init_tools_tab,
    refresh_drm_emulation_state,
    update_depot_label,
    refresh_dlcdata_btn_text,
    handle_move_dlc_to_dlcdata,
)

from ui.dialogs.game_details.workshop_tab import (
    init_workshop_tab,
    scan_workshop_mods_async,
    on_workshop_mods_scanned,
    delete_workshop_item_dialog,
    update_workshop_items,
)

from ui.dialogs.game_details.shsah_reborn import (
    init_achievements_tab,
    ensure_achievements_loaded,
)

from ui.dialogs.game_details.tickets_tab import (
    init_tickets_tab,
    handle_ticket_file_import,
    handle_ticket_text_import,
    verify_ticket_status_dialog,
    paste_and_import_ticket,
    export_installed_ticket,
    delete_installed_ticket,
)

__all__ = [
    "init_achievements_tab",
    "ensure_achievements_loaded",
    "SwitchToggle",
    "CenteredComboBox",
    "HeroBanner",
    "MaterialTile",
    "load_hero_image",
    "init_hero_v2",
    "init_hero_legacy",
    "update_title",
    "thin_line",
    "section_title",
    "format_time_diff",
    "get_manifest_age",
    "get_lua_age",
    "get_last_checked",
    "init_info_tab",
    "init_builds_tab",
    "fetch_steamdb_builds_async",
    "on_builds_loaded",
    "on_builds_error",
    "populate_builds_cards",
    "on_build_card_clicked",
    "on_build_depots_loaded",
    "on_build_depots_error",
    "on_builds_download_clicked",
    "init_tools_tab",
    "refresh_drm_emulation_state",
    "update_depot_label",
    "refresh_dlcdata_btn_text",
    "handle_move_dlc_to_dlcdata",
    "init_workshop_tab",
    "scan_workshop_mods_async",
    "on_workshop_mods_scanned",
    "delete_workshop_item_dialog",
    "update_workshop_items",
    "init_tickets_tab",
    "handle_ticket_file_import",
    "handle_ticket_text_import",
    "verify_ticket_status_dialog",
    "paste_and_import_ticket",
    "export_installed_ticket",
    "delete_installed_ticket",
]
