"""
Edition / package picker popup for DepotSelectionDialog.

Thin wrapper over the shared ``ui.widgets.modern_dropdown.ModernPopup`` (which
every dropdown in the app now uses): it turns edition groups into popup rows
with a "Platform • N depots • Source" subtitle and per-platform headers.
"""

from typing import Callable, Optional, Sequence

from PyQt6.QtWidgets import QWidget

from ui.widgets.modern_dropdown import ModernPopup, PopupEntry


def _edition_subtitle(edition: dict) -> str:
    parts = []
    platform = str(edition.get("platform") or "").capitalize()
    if platform:
        parts.append(platform)
    depot_count = len(edition.get("depot_ids") or ())
    if depot_count > 0:
        parts.append(f"{depot_count} depot{'s' if depot_count != 1 else ''}")
    source = str(edition.get("source") or "")
    if source:
        parts.append(source.capitalize())
    return " • ".join(parts)


class EditionPopup(ModernPopup):
    """Scrollable popup listing available game editions, grouped by platform."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        groups: Optional[Sequence] = None,
        accent_color: str = "#6c5ce7",
        on_selected: Optional[Callable[[dict], None]] = None,
    ):
        groups = groups or ()
        multi_group = len(groups) > 1
        entries = []
        for group in groups:
            label = getattr(group, "label", None)
            choices = getattr(group, "choices", [])
            if multi_group and label and choices:
                entries.append(PopupEntry(text=label, header=True))
            for choice in choices:
                edition = choice.edition or {}
                entries.append(
                    PopupEntry(
                        text=choice.name,
                        subtitle=_edition_subtitle(edition),
                        checked=bool(choice.checked),
                        payload=edition,
                    )
                )

        def _selected(edition):
            if edition and callable(on_selected):
                on_selected(edition)

        super().__init__(
            parent,
            entries,
            accent_color=accent_color,
            on_selected=_selected,
            filter_placeholder="Filter editions...",
        )

    def show_at(self, target_widget: QWidget, min_width: int = 0) -> None:
        super().show_at(target_widget, min_width=max(min_width, target_widget.width() + 40, 290))
