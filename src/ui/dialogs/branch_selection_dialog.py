"""
BranchSelectionDialog — A clean, comfortably sized dialog for selecting game branches.
Maintains the lightweight feel of QInputDialog while providing comfortable dimensions
and readable text so branch names and build IDs are not cramped.
"""

from typing import Dict, Any, Optional, Tuple
from PyQt6.QtWidgets import QInputDialog, QDialog
from utils.settings import get_settings
from utils.color_utils import get_best_foreground_color


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

        settings = get_settings()
        accent = settings.value("accent_color", "#C06C84", type=str) or "#C06C84"
        fg_color = get_best_foreground_color(accent, dark_color="#121214", light_color="#FFFFFF")

        dialog = QInputDialog(parent)
        dialog.setWindowTitle("Select Branch")
        dialog.setLabelText(f"Multiple branches found for AppID {app_id}.\nSelect which branch manifest to fetch:")
        dialog.setComboBoxItems(display_items)
        dialog.setComboBoxEditable(False)
        dialog.resize(420, 140)
        dialog.setStyleSheet(f"""
            QInputDialog {{
                background-color: #1e1e22;
                color: #FFFFFF;
                min-width: 420px;
                min-height: 135px;
            }}
            QLabel {{
                color: #E0E0E0;
                font-size: 10pt;
                padding-bottom: 4px;
            }}
            QComboBox {{
                background-color: #2b2b30;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 5px;
                padding: 4px 10px;
                font-size: 10pt;
                min-height: 28px;
            }}
            QComboBox:hover {{
                border-color: {accent};
            }}
            QComboBox QAbstractItemView {{
                background-color: #2b2b30;
                color: #FFFFFF;
                selection-background-color: {accent};
                selection-color: {fg_color};
                padding: 4px;
                font-size: 9.5pt;
            }}
            QPushButton {{
                background-color: #2d2d33;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                padding: 5px 16px;
                font-size: 9.5pt;
                min-width: 75px;
                min-height: 24px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.15);
            }}
            QPushButton:default {{
                background-color: {accent};
                color: {fg_color};
                border: none;
                font-weight: bold;
            }}
            QPushButton:default:hover {{
                background-color: #FFFFFF;
                color: #000000;
            }}
        """)

        # Pre-select saved branch if exists
        saved_branch = settings.value(f"selected_branch/{app_id}", "public", type=str)
        if saved_branch in items:
            idx = items.index(saved_branch)
            dialog.setTextValue(display_items[idx])

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
