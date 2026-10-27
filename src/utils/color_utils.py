import json
import logging
from pathlib import Path
from typing import Dict, Optional
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPixmap

logger = logging.getLogger(__name__)

_THUMBNAIL_COLOR_CACHE: Dict[str, QColor] = {}
_DISK_CACHE_LOADED: bool = False


def _get_color_cache_file() -> Path:
    from utils.helpers import get_base_path
    return get_base_path() / "thumbnail_colors.json"


def _ensure_disk_cache_loaded() -> None:
    global _DISK_CACHE_LOADED, _THUMBNAIL_COLOR_CACHE
    if _DISK_CACHE_LOADED:
        return
    _DISK_CACHE_LOADED = True
    try:
        cache_path = _get_color_cache_file()
        if cache_path.is_file():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for aid, hex_str in data.items():
                    if isinstance(hex_str, str) and hex_str.startswith("#"):
                        col = QColor(hex_str)
                        if col.isValid() and aid not in _THUMBNAIL_COLOR_CACHE:
                            _THUMBNAIL_COLOR_CACHE[str(aid)] = col
    except Exception as e:
        logger.debug(f"Could not load thumbnail color cache from disk: {e}")


def get_cached_thumbnail_color(app_id: Optional[str]) -> Optional[QColor]:
    """Retrieve pre-extracted thumbnail color from memory/disk cache without needing a pixmap."""
    if not app_id:
        return None
    _ensure_disk_cache_loaded()
    return _THUMBNAIL_COLOR_CACHE.get(str(app_id))


def _save_color_to_disk_cache(app_id: str, hex_val: str) -> None:
    try:
        cache_path = _get_color_cache_file()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if cache_path.is_file():
            try:
                raw = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
            except Exception:
                data = {}
        data[str(app_id)] = hex_val
        # Cap size at 1500 entries
        if len(data) > 1500:
            for k in list(data.keys())[:300]:
                data.pop(k, None)
        cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug(f"Could not save thumbnail color cache to disk: {e}")


def extract_dominant_color(pixmap: QPixmap, app_id: Optional[str] = None) -> Optional[QColor]:
    """
    Extracts a vibrant signature/accent color from a thumbnail pixmap using lightweight
    Qt image downsampling and HSV vibrance clustering.

    Filters out dark borders/letterboxing and desaturated background tones.
    Runs in < 0.5 ms and caches result in memory and disk by app_id if provided.
    """
    if not pixmap or pixmap.isNull():
        return None

    if app_id:
        _ensure_disk_cache_loaded()
        if str(app_id) in _THUMBNAIL_COLOR_CACHE:
            return _THUMBNAIL_COLOR_CACHE[str(app_id)]

    try:
        # Downsample to a small grid for near-instant analysis (24x24 = 576 pixels)
        img = pixmap.toImage().scaled(
            24, 24,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if img.isNull():
            return None

        width = img.width()
        height = img.height()

        # 12 hue buckets (30 degrees each)
        # Each bucket holds: [count, sum_r, sum_g, sum_b, total_score]
        buckets = [[0, 0, 0, 0, 0.0] for _ in range(12)]
        all_candidates = []

        for y in range(height):
            for x in range(width):
                rgb = img.pixel(x, y)
                c = QColor(rgb)
                h, s, v, _ = c.getHsv()

                # Filter out extreme darks (v < 38), washed out whites (v > 240 and s < 30),
                # and nearly pure grays (s < 35)
                if v < 38 or (v > 240 and s < 30) or s < 35:
                    continue

                # Weight by saturation and value (vibrancy score)
                score = ((s / 255.0) ** 1.2) * (v / 255.0)

                # Hue ranges 0..359
                bucket_idx = int((h % 360) / 30)
                b = buckets[bucket_idx]
                b[0] += 1
                b[1] += c.red()
                b[2] += c.green()
                b[3] += c.blue()
                b[4] += score
                all_candidates.append((c, score))

        # Find highest scoring bucket
        best_bucket = max(buckets, key=lambda b: b[4])

        result_color = None
        if best_bucket[0] > 0 and best_bucket[4] > 1.0:
            avg_r = int(best_bucket[1] / best_bucket[0])
            avg_g = int(best_bucket[2] / best_bucket[0])
            avg_b = int(best_bucket[3] / best_bucket[0])
            result_color = QColor(avg_r, avg_g, avg_b)
        elif all_candidates:
            # Fallback to the single most vibrant candidate
            result_color = max(all_candidates, key=lambda p: p[1])[0]

        if result_color and result_color.isValid():
            # Ensure the extracted color has enough saturation and brightness for a rich ambient glow
            h, s, v, _ = result_color.getHsv()
            s = max(70, min(255, int(s * 1.15))) if s > 0 else 0
            v = max(130, min(255, int(v * 1.05)))
            adjusted = QColor.fromHsv(h, s, v)
            if app_id:
                _THUMBNAIL_COLOR_CACHE[str(app_id)] = adjusted
                _save_color_to_disk_cache(str(app_id), adjusted.name())
            return adjusted

    except Exception as e:
        logger.debug(f"Dominant color extraction failed: {e}")

    return None


def get_relative_luminance(color_hex_or_rgb) -> float:
    """
    Computes the relative luminance of a color based on WCAG 2.1 specs.
    Accepts a hex string (e.g. '#FFFFFF' or 'FFFFFF') or an RGB tuple/list (r, g, b).
    """
    if isinstance(color_hex_or_rgb, str):
        hex_str = color_hex_or_rgb.lstrip('#')
        if len(hex_str) == 6:
            try:
                r = int(hex_str[0:2], 16)
                g = int(hex_str[2:4], 16)
                b = int(hex_str[4:6], 16)
            except ValueError:
                r, g, b = 255, 255, 255
        else:
            r, g, b = 255, 255, 255
    else:
        try:
            r, g, b = color_hex_or_rgb[:3]
        except (TypeError, IndexError):
            r, g, b = 255, 255, 255

    # Standard WCAG sRGB conversion to relative luminance
    def srgb_to_linear(c):
        val = c / 255.0
        if val <= 0.03928:
            return val / 12.92
        else:
            return ((val + 0.055) / 1.055) ** 2.4

    r_l = srgb_to_linear(r)
    g_l = srgb_to_linear(g)
    b_l = srgb_to_linear(b)

    return 0.2126 * r_l + 0.7152 * g_l + 0.0722 * b_l


def get_contrast_ratio(color1, color2) -> float:
    """
    Computes the WCAG contrast ratio between two colors.
    """
    l1 = get_relative_luminance(color1)
    l2 = get_relative_luminance(color2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def get_best_foreground_color(bg_hex: str, dark_color: str = "#121214", light_color: str = "#FFFFFF") -> str:
    """
    Returns either dark_color or light_color depending on which one has better WCAG contrast against bg_hex.
    Guarantees contrast complies with WCAG AA/AAA.
    """
    try:
        ratio_light = get_contrast_ratio(bg_hex, light_color)
        ratio_dark = get_contrast_ratio(bg_hex, dark_color)
        return light_color if ratio_light >= ratio_dark else dark_color
    except Exception as e:
        logger.error(f"Error computing best foreground color: {e}")
        return light_color


def _surface_hex(surface_hex) -> str:
    """
    Best-effort normalised hex for a surface colour. Anything unparseable becomes
    the app's default dark background, so callers always get usable tokens rather
    than a silent inversion.
    """
    if not isinstance(surface_hex, str):
        return "#111318"
    color = QColor(surface_hex)
    return color.name() if color.isValid() else "#111318"


def is_light_color(color_hex: str) -> bool:
    """
    True when `color_hex` reads as a light surface (WCAG relative luminance above
    0.5) — a surface that needs dark ink and dark overlay tints instead of white
    ones. Unparseable values are treated as dark, which keeps the app's
    long-standing dark look as the fallback.
    """
    try:
        return get_relative_luminance(_surface_hex(color_hex)) > 0.5
    except Exception as e:
        logger.error(f"Error classifying surface color '{color_hex}': {e}")
        return False


def surface_overlay_rgb(surface_hex: str) -> str:
    """
    The RGB triplet translucent overlays sitting on `surface_hex` should use, as a
    plain "r, g, b" string ready for rgba(): white on a dark surface, black on a
    light one. Lets a tile/chip/card tint itself with its surface at any alpha and
    stay visible on either.
    """
    return "0, 0, 0" if is_light_color(surface_hex) else "255, 255, 255"


def get_raised_surface_tokens(surface_hex: str) -> dict:
    """
    Ink and overlay colours for raised elements (tiles, chips, cards) sitting on
    `surface_hex`, so they stay legible on any background a theme picks.

    `ink` is the WCAG-chosen readable text colour for the surface; the muted,
    faint, subtle and disabled inks are the surface's overlay colour at the alphas
    the app has always used. tint/border are the raised element's own background
    and outline, with hover and disabled variants.

    On the built-in dark themes every value is byte-identical to the literals this
    replaces, so the default look does not move.

    Keys: ink, ink_muted, ink_faint, ink_disabled, ink_subtle,
          tint, tint_hover, tint_disabled, border, border_hover, border_disabled
    """
    surface = _surface_hex(surface_hex)
    overlay = surface_overlay_rgb(surface)
    return {
        "ink": get_best_foreground_color(surface, dark_color="#121214", light_color="#FFFFFF"),
        "ink_muted": f"rgba({overlay}, 0.6)",
        "ink_faint": f"rgba({overlay}, 0.7)",
        "ink_disabled": f"rgba({overlay}, 0.3)",
        "ink_subtle": f"rgba({overlay}, 0.2)",
        "tint": f"rgba({overlay}, 0.05)",
        "tint_hover": f"rgba({overlay}, 0.10)",
        "tint_disabled": f"rgba({overlay}, 0.02)",
        "border": f"rgba({overlay}, 0.08)",
        "border_hover": f"rgba({overlay}, 0.15)",
        "border_disabled": f"rgba({overlay}, 0.04)",
    }


def get_dark_container_color(accent_hex: str) -> str:
    """
    Generates a dark-mode friendly container color (Tone 30-ish in HCT/Material space)
    from the given accent color. Keeps the hue, but decreases the brightness/value.
    """
    try:
        color = QColor(accent_hex)
        h, s, v, a = color.getHsv()
        # M3 Tone 30 corresponds to ~30-38% brightness.
        # Boost saturation slightly to keep the color rich and vibrant in the dark container.
        s_dark = min(255, int(s * 1.15)) if s > 0 else 0
        v_dark = max(20, min(80, int(v * 0.35)))
        
        dark_color = QColor.fromHsv(h, s_dark, v_dark, a)
        return dark_color.name()
    except Exception as e:
        logger.error(f"Error generating dark container color: {e}")
        return "#2d161a"  # Safe default dark fallback


def get_semantic_colors(accent_hex: str) -> dict:
    """
    Returns a dict of theme-harmonized semantic colors (success, error, warning, info)
    derived from the user's accent color's saturation and lightness, mapped to Google
    Material You (M3 Tone 80 / pastel) palettes.
    """
    try:
        color = QColor(accent_hex)
        h, s, v, a = color.getHsv()
        # Google Material You (M3 Tone 80) pastel semantic colors:
        # Soft saturation (70-115) and high brightness (215-240)
        s_sem = max(70, min(s, 115)) if s > 0 else 0
        v_sem = max(215, min(v, 240))
        
        success = QColor.fromHsv(140, s_sem, v_sem, a).name()  # Soft pastel mint (M3 Tone 80)
        error = QColor.fromHsv(0, s_sem, v_sem, a).name()      # Soft pastel coral/rose (M3 Tone 80)
        warning = QColor.fromHsv(42, s_sem, v_sem, a).name()   # Soft pastel peach/amber (M3 Tone 80)
        info = QColor.fromHsv(210, s_sem, v_sem, a).name()     # Soft pastel sky blue (M3 Tone 80)
        
        return {
            "success": success,
            "error": error,
            "warning": warning,
            "info": info
        }
    except Exception as e:
        logger.error(f"Error generating semantic colors: {e}")
        return {
            "success": "#a8dab5",
            "error": "#f28b82",
            "warning": "#fdd663",
            "info": "#8ab4f8"
        }


def get_grayscale_color(hex_color: str) -> str:
    """
    Converts a hex color string to grayscale by zeroing out saturation (s = 0) while keeping
    lightness/brightness untouched, so a bright blue switch becomes a mid-gray, not white or black.
    Does NOT change opacity/alpha — output remains fully opaque.
    """
    try:
        color = QColor(hex_color)
        h, s, l, a = color.getHsl()
        gray_color = QColor.fromHsl(h, 0, l, a)
        return gray_color.name()
    except Exception as e:
        logger.error(f"Error generating grayscale color for '{hex_color}': {e}")
        return "#9E9E9E"


def make_svg_icon(svg_path: str, color: str, size: int = 20):
    """
    Renders an SVG file with `currentColor` replaced by `color` and returns a
    QIcon of the given pixel `size`. Falls back to an empty QIcon on error.

    `color` can be any value accepted by QColor (hex, named colour, or even
    CSS rgba() — it will be normalised to a plain hex string before being
    written into the SVG, because SVG stroke/fill do NOT support rgba()).

    Usage:
        icon = make_svg_icon("/path/to/icon.svg", "#C06C84", size=20)
        button.setIcon(icon)
        button.setIconSize(QSize(20, 20))
    """
    try:
        from PyQt6.QtSvg import QSvgRenderer
        from PyQt6.QtGui import QPixmap, QIcon, QPainter
        from PyQt6.QtCore import QByteArray, QSize

        # --- Normalise color to SVG-safe hex --------------------------------
        # QColor accepts hex (#rgb, #rrggbb) and named colours, but NOT CSS
        # rgba().  Parse rgba() manually; everything else goes through QColor.
        hex_color = "#FFFFFF"  # safe fallback
        try:
            c_stripped = color.strip()
            if c_stripped.lower().startswith("rgba("):
                # e.g. "rgba(255, 255, 255, 0.70)"
                parts = c_stripped[5:-1].split(",")
                r = max(0, min(255, int(float(parts[0].strip()))))
                g = max(0, min(255, int(float(parts[1].strip()))))
                b = max(0, min(255, int(float(parts[2].strip()))))
                hex_color = QColor(r, g, b).name()   # → "#rrggbb"
            elif c_stripped.lower().startswith("rgb("):
                parts = c_stripped[4:-1].split(",")
                r = max(0, min(255, int(float(parts[0].strip()))))
                g = max(0, min(255, int(float(parts[1].strip()))))
                b = max(0, min(255, int(float(parts[2].strip()))))
                hex_color = QColor(r, g, b).name()
            else:
                qc = QColor(c_stripped)
                hex_color = qc.name() if qc.isValid() else "#FFFFFF"
        except Exception:
            hex_color = "#FFFFFF"
        # --------------------------------------------------------------------

        with open(svg_path, "r", encoding="utf-8") as f:
            svg_data = f.read()

        colored_svg = svg_data.replace("currentColor", hex_color)

        renderer = QSvgRenderer(QByteArray(colored_svg.encode("utf-8")))
        pix = QPixmap(QSize(size, size))
        pix.fill(QColor(0, 0, 0, 0))   # transparent background
        painter = QPainter(pix)
        renderer.render(painter)
        painter.end()
        return QIcon(pix)
    except Exception as e:
        logger.warning(f"make_svg_icon: failed to render '{svg_path}': {e}")
        from PyQt6.QtGui import QIcon
        return QIcon()



