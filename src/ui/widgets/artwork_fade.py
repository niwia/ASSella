"""Shared smoothstep artwork fading helpers for banner and thumbnail widgets."""

from PyQt6.QtCore import QRectF, Qt


def _smoothstep(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return u * u * (3.0 - 2.0 * u)


def build_faded_artwork(
    pixmap,
    target_size,
    *,
    direction: str = "fade_in_right",
    fade_start: float = 0.0,
    fade_end: float = 0.72,
    start_alpha: int = 0,
    peak_alpha: int = 225,
    end_alpha: int = 238,
    edge_alpha: int = 200,
    edge_span: float = 0.16,
    corner_radius: float = 0.0,
    round_corners: str = "none",
    steps: int = 10,
):
    """Scale pixmap to cover target_size, center-crop, and apply an S-curve alpha fade.

    Args:
        pixmap: Source QPixmap to scale and fade.
        target_size: Destination QSize.
        direction: ``"fade_in_right"`` (transparent on left -> visible on right)
            or ``"fade_out_right"`` (visible on left -> transparent on right).
        fade_start: Normalized horizontal position [0..1] where the S-curve begins.
        fade_end: Normalized horizontal position [0..1] where the S-curve finishes.
        start_alpha: Alpha at x=0 for ``fade_in_right``, or before ``fade_start`` for ``fade_out_right``.
        peak_alpha: Target alpha reached at ``fade_end`` for ``fade_in_right``.
        end_alpha: Alpha at x=1 for ``fade_in_right``, or from ``fade_end``..1.0 for ``fade_out_right``.
        edge_alpha: Top/bottom border alpha (< 255 softens top/bottom seams).
        edge_span: Normalized vertical distance [0..0.5) over which top/bottom edges soften.
        corner_radius: Optional rounded-corner radius in pixels.
        round_corners: ``"none"``, ``"left"`` (top-left + bottom-left), or ``"all"``.
        steps: Number of cubic smoothstep gradient stops across the fade span.
    """
    from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap

    if (
        pixmap is None
        or pixmap.isNull()
        or target_size is None
        or target_size.width() <= 0
        or target_size.height() <= 0
    ):
        return QPixmap()

    w = target_size.width()
    h = target_size.height()

    scaled = pixmap.scaled(
        target_size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    sx = max(0, (scaled.width() - w) // 2)
    sy = max(0, (scaled.height() - h) // 2)

    faded = QPixmap(target_size)
    faded.fill(Qt.GlobalColor.transparent)
    p = QPainter(faded)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    if corner_radius > 0.0 and round_corners in ("left", "all"):
        clip_path = QPainterPath()
        clip_w = float(w + corner_radius) if round_corners == "left" else float(w)
        clip_path.addRoundedRect(QRectF(0.0, 0.0, clip_w, float(h)), corner_radius, corner_radius)
        p.setClipPath(clip_path)

    p.drawPixmap(0, 0, scaled, sx, sy, w, h)
    p.setClipping(False)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)

    fade_start = max(0.0, min(0.99, float(fade_start)))
    fade_end = max(fade_start + 0.01, min(1.0, float(fade_end)))
    steps = max(2, int(steps))

    alpha_grad = QLinearGradient(0.0, 0.0, float(w), 0.0)
    if direction == "fade_out_right":
        if fade_start > 0.0:
            alpha_grad.setColorAt(0.0, QColor(0, 0, 0, start_alpha))
        span = fade_end - fade_start
        for i in range(steps + 1):
            u = i / steps
            s = _smoothstep(u)
            pos = fade_start + u * span
            a = int(round(start_alpha + (end_alpha - start_alpha) * s))
            alpha_grad.setColorAt(pos, QColor(0, 0, 0, max(0, min(255, a))))
        if fade_end < 1.0:
            alpha_grad.setColorAt(1.0, QColor(0, 0, 0, end_alpha))
    else:
        if fade_start > 0.0:
            alpha_grad.setColorAt(0.0, QColor(0, 0, 0, start_alpha))
        span = fade_end - fade_start
        for i in range(steps + 1):
            u = i / steps
            s = _smoothstep(u)
            pos = fade_start + u * span
            a = int(round(start_alpha + (peak_alpha - start_alpha) * s))
            alpha_grad.setColorAt(pos, QColor(0, 0, 0, max(0, min(255, a))))
        if fade_end < 1.0:
            alpha_grad.setColorAt(1.0, QColor(0, 0, 0, end_alpha))

    p.fillRect(0, 0, w, h, alpha_grad)

    if 0.0 < edge_span < 0.5 and 0 <= edge_alpha < 255:
        v_grad = QLinearGradient(0.0, 0.0, 0.0, float(h))
        v_grad.setColorAt(0.0, QColor(0, 0, 0, edge_alpha))
        v_grad.setColorAt(edge_span, QColor(0, 0, 0, 255))
        v_grad.setColorAt(1.0 - edge_span, QColor(0, 0, 0, 255))
        v_grad.setColorAt(1.0, QColor(0, 0, 0, edge_alpha))
        p.fillRect(0, 0, w, h, v_grad)

    p.end()
    return faded

