"""
BranchSelectionDialog — branch picker for a single AppID.
A themed one-question dialog (ThemedInputDialog) sized so branch names and build
IDs are not cramped.
"""

from typing import Dict, Any, Optional, Tuple
from PyQt6.QtWidgets import QDialog
from utils.settings import get_settings
from ui.dialogs.themed_input_dialog import ThemedInputDialog


class BranchSelectionDialog:
    """Compact modal dialog allowing users to pick a branch for an AppID."""

    @staticmethod
    def select_branch(
        parent=None,
        app_id: str = "",
        branches: Optional[Dict[str, Any]] = None,
        game_name: str = "",
    ) -> Tuple[Optional[str], bool]:
        branches = branches or {}
        items = sorted(branches.keys(), key=lambda k: (0 if k == "public" else 1, k.lower()))
        if not items:
            items = ["public"]

        display_items = []
        for b in items:
            b_info = branches.get(b, {})
            bid = b_info.get("buildid", "") if isinstance(b_info, dict) else ""
            display_items.append(f"{b} ({bid})" if bid else b)

        if not game_name:
            try:
                from utils.edition_catalog import get_app_name

                game_name = get_app_name(str(app_id), allow_network=False)
            except Exception:
                game_name = ""
        dialog = ThemedInputDialog(
            parent,
            title="Select Branch",
            label=f"Select branch for: <b>{game_name or f'App {app_id}'}</b>",
            items=display_items,
        )
        # Branch names and build IDs are short: no need for a full-width field.
        dialog.setMinimumWidth(340)
        dialog.field.setFixedWidth(230)
        dialog.adjustSize()

        # Pre-select saved branch if exists
        settings = get_settings()
        saved_branch = settings.value(f"selected_branch/{app_id}", "public", type=str)
        if saved_branch in items:
            dialog.setTextValue(display_items[items.index(saved_branch)])

        ok = (dialog.exec() == QDialog.DialogCode.Accepted)
        if ok:
            selected_text = dialog.textValue()
            if selected_text in display_items:
                idx = display_items.index(selected_text)
                chosen_branch = items[idx]
                settings.setValue(f"selected_branch/{app_id}", chosen_branch)
                return chosen_branch, True
            return "public", True

        return None, False
