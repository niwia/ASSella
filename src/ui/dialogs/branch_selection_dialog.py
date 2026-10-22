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
        branches: Optional[Dict[str, Any]] = None
    ) -> Tuple[Optional[str], bool]:
        branches = branches or {}
        items = sorted(branches.keys(), key=lambda k: (0 if k == "public" else 1, k.lower()))
        if not items:
            items = ["public"]

        display_items = []
        for b in items:
            b_info = branches.get(b, {})
            bid = b_info.get("buildid", "") if isinstance(b_info, dict) else ""
            display_items.append(f"{b} (Build: {bid})" if bid else b)

        dialog = ThemedInputDialog(
            parent,
            title="Select Branch",
            label=f"Multiple branches found for AppID {app_id}.\nSelect which branch manifest to fetch:",
            items=display_items,
        )

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
