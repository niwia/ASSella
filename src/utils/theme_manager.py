import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

BUILTIN_THEMES: Dict[str, Dict[str, Any]] = {
    "ocean": {
        "id": "ocean",
        "name": "Ocean Breeze (Monet Blue)",
        "accent_color": "#a1c9fd",
        "background_color": "#111318",
        "background_image": None,
    },
    "forest": {
        "id": "forest",
        "name": "Forest Sage (Mint Green)",
        "accent_color": "#b1ecbe",
        "background_color": "#0f1511",
        "background_image": None,
    },
    "lavender": {
        "id": "lavender",
        "name": "Lavender Mist (Orchid Purple)",
        "accent_color": "#e7bdfb",
        "background_color": "#141217",
        "background_image": None,
    },
    "halloween": {
        "id": "halloween",
        "name": "Halloween Night (Pumpkin & Black)",
        "accent_color": "#ffb77d",
        "background_color": "#141211",
        "background_image": "src/res/halloween_bg.jpg",
    },
}


def get_user_themes_dir() -> Path:
    """Returns ~/.local/share/ACCELA/themes/ ensuring it exists."""
    themes_dir = Path(os.path.expanduser("~/.local/share/ACCELA/themes"))
    try:
        themes_dir.mkdir(parents=True, exist_ok=True)
        # Ensure a friendly template exists
        template_file = themes_dir / "example_theme.json.template"
        if not template_file.exists():
            example_data = {
                "id": "cyberpunk_example",
                "name": "Cyberpunk Neon (Example)",
                "accent_color": "#00f0ff",
                "background_color": "#0d0d15",
                "background_image": "optional_wallpaper.jpg"
            }
            with open(template_file, "w", encoding="utf-8") as f:
                json.dump(example_data, f, indent=4)
    except Exception as e:
        logger.debug(f"[ThemeManager] Error creating themes directory: {e}")
    return themes_dir


def load_all_themes() -> Dict[str, Dict[str, Any]]:
    """Loads all builtin themes + custom JSON themes from ~/.local/share/ACCELA/themes/."""
    themes = dict(BUILTIN_THEMES)
    themes_dir = get_user_themes_dir()

    if themes_dir.exists():
        for file in sorted(themes_dir.glob("*.json")):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                theme_id = str(data.get("id") or file.stem).lower().strip()
                name = str(data.get("name") or theme_id.capitalize())
                accent = str(data.get("accent_color") or "#C06C84")
                bg = str(data.get("background_color") or "#101014")
                bg_img = data.get("background_image")

                # Resolve relative background image path against themes directory
                if bg_img:
                    bg_img_path = Path(bg_img)
                    if not bg_img_path.is_absolute():
                        bg_img_path = themes_dir / bg_img
                    if bg_img_path.exists():
                        bg_img = str(bg_img_path)
                    else:
                        bg_img = None

                # Resolve custom checkbox indicators
                chk_unlit = data.get("checkbox_unlit")
                if chk_unlit:
                    chk_p = Path(chk_unlit)
                    if not chk_p.is_absolute():
                        chk_p = themes_dir / chk_unlit
                    chk_unlit = str(chk_p) if chk_p.exists() else None

                chk_lit = data.get("checkbox_lit")
                if chk_lit:
                    chk_p = Path(chk_lit)
                    if not chk_p.is_absolute():
                        chk_p = themes_dir / chk_lit
                    chk_lit = str(chk_p) if chk_p.exists() else None

                themes[theme_id] = {
                    "id": theme_id,
                    "name": name,
                    "accent_color": accent,
                    "background_color": bg,
                    "background_image": bg_img,
                    "checkbox_unlit": chk_unlit,
                    "checkbox_lit": chk_lit,
                    "custom": True,
                }
            except Exception as e:
                logger.warning(f"[ThemeManager] Failed to load custom theme from {file.name}: {e}")

    return themes


def get_theme(theme_id: str) -> Optional[Dict[str, Any]]:
    """Get theme dictionary by id."""
    themes = load_all_themes()
    return themes.get(theme_id)
