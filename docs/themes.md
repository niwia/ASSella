# Custom Themes Guide for ASSella

ASSella 3.0 supports custom, user-defined themes loaded directly from your personal configuration directory. You can customize the accent color, background color, background wallpaper, and themed checkbox/table indicator icons.

---

## 1. Where to Place Your Theme Files

All custom themes and associated assets live in:

```text
~/.local/share/ACCELA/themes/
```

> **Note:** If this directory does not exist yet, ASSella will automatically generate it on first launch, complete with an `example_theme.json.template` file. You can also create it manually with:
> ```bash
> mkdir -p ~/.local/share/ACCELA/themes
> ```

---

## 2. Theme JSON Structure

Each theme is a separate `.json` file placed in the `themes/` folder (e.g. `~/.local/share/ACCELA/themes/cyberpunk.json`).

### Key Properties

| Property | Type | Required? | Description |
| :--- | :--- | :--- | :--- |
| `id` | `string` | Recommended | Unique internal identifier (lowercase alphanumeric, e.g. `"cyberpunk"`). |
| `name` | `string` | **Yes** | Display name shown in ASSella's Theme dropdown in Settings. |
| `accent_color` | `string` | **Yes** | Hex color (`#RRGGBB`) used for buttons, active tabs, highlights, and borders. |
| `background_color`| `string` | **Yes** | Hex color (`#RRGGBB`) used as base window and panel background. |
| `background_image`| `string` | *Optional* | Image filename (PNG/JPG) relative to `themes/` or an absolute path. |
| `checkbox_unlit` | `string` | *Optional* | Icon (PNG/SVG) displayed for **unchecked** checkboxes and table items. |
| `checkbox_lit` | `string` | *Optional* | Icon (PNG/SVG) displayed for **checked** checkboxes and table items. |

---

## 3. Examples

### A. Minimal Theme (`minimal_purple.json`)

```json
{
    "id": "minimal_purple",
    "name": "Minimal Lavender",
    "accent_color": "#b39ddb",
    "background_color": "#121016"
}
```

### B. Complete Theme with Wallpaper & Custom Indicators (`halloween_custom.json`)

```json
{
    "id": "halloween_custom",
    "name": "Spooky Hollow (Custom)",
    "accent_color": "#ff7518",
    "background_color": "#141211",
    "background_image": "hollow_bg.jpg",
    "checkbox_unlit": "pumpkin_dark.png",
    "checkbox_lit": "pumpkin_lit.png"
}
```

Directory structure for this theme:
```text
~/.local/share/ACCELA/themes/
├── halloween_custom.json
├── hollow_bg.jpg
├── pumpkin_dark.png
└── pumpkin_lit.png
```

---

## 4. How Material You & Pastel Color Utilities Work

You don't need to specify every shade of every UI element. ASSella's built-in **Material You color engine** automatically calculates:
- **Surface & Container Tones:** Generates softened surface tints derived from your `accent_color` and `background_color`.
- **Selected Row Backgrounds:** Deep muted container shades so text remains crisp and readable.
- **Hover & Focus States:** Dynamic translucency values based on RGB decomposition.
- **Semantic Badges:** Harmonized pastel indicators for status messages (e.g. pastel sage green for `Verify Files` / `Validated`, warm amber for `Update Available`).

---

## 5. Themed Checkbox & Indicator Guidelines

When providing `checkbox_unlit` and `checkbox_lit`:
- **Recommended Size:** 16×16 to 32×32 pixels (PNG with alpha channel transparency or SVG).
- **Design Tip:** High-contrast silhouettes work best (e.g. an outline or silhouette for `unlit`, and a bright, illuminated graphic for `lit`).
- If omitted, ASSella will automatically use sleek rounded geometric indicators styled with your theme's `accent_color`.

---

## 6. How to Apply Your Theme in ASSella

1. Drop your `.json` file and any accompanying image files into `~/.local/share/ACCELA/themes/`.
2. Open **ASSella**.
3. Go to **Settings** (`Ctrl+S` or Gear icon) → **Style** tab.
4. In the **Theme Preset** dropdown, your new theme will appear with its custom name.
5. Select it — your color palette, wallpaper, and indicators will apply immediately!
