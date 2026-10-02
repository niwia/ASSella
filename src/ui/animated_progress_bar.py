import math
import os
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter,
    QPainterPath,
    QColor,
    QLinearGradient,
    QBrush,
    QPen,
    QPolygonF,
    QPixmap,
)
from PyQt6.QtWidgets import QProgressBar
from utils.paths import Paths


class AnimatedProgressBar(QProgressBar):
    """A modular download progress bar supporting custom animations:
    - Standard: Sleek theme accent fill
    - Retro Ghost: Retro floating white ghost leaving a misty fog trail (no capsule)
    - Pac-Man: Classic chomping Pac-Man eating dots at gentle speed, with pitch-black eaten path
    - Flying Witch: Scarlet-robed witch on broomstick with shimmering stardust trail
    - Goku (Kamehameha): Pixel-art Goku at the start unleashing a surging Kamehameha beam (no capsule)
    - Sonic: Blue blur spin-dashing with authentic sprites along Green Hill Zone collecting floating rings
    - Super Mario: Authentic 8-bit Mario leaping past rotating golden coins from NES
    - Shinobi Duel: Ninja rushing forward and clashing swords with defending Samurai at progress tip
    - Nyan Cat: Flying Pop-Tart cat leaving an undulating 6-color rainbow in space (no capsule)
    - Mega Man: Charging and firing a charged Mega Buster plasma beam (no capsule)
    - Pokémon: Spinning Pokéball tracking forward past patches of tall grass
    - City Drive: Retro sports car driving forward while skyline buildings scroll in reverse
    - Jack-o'-Lantern: Carved glowing mini pumpkin runner
    """

    def __init__(self, main_window=None, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.setFixedHeight(16)
        self.setTextVisible(False)
        self._phase = 0.0

        # Sprite caches
        self._goku_fire_pm = None
        self._goku_charge_pm = None
        self._sonic_stand_pm = None
        self._sonic_spin_pms = []
        self._mario_jump_pm = None
        self._mario_coin_pm = None
        self._shinobi_run_pms = []
        self._shinobi_atk_pms = []
        self._samurai_shield_pms = []

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(40)  # ~25 FPS
        self._anim_timer.timeout.connect(self._on_tick)

    def _get_anim_mode(self) -> str:
        try:
            if self.main_window and hasattr(self.main_window, "settings") and self.main_window.settings:
                return self.main_window.settings.value("download_animation", "standard", type=str)
            from utils.settings import get_settings
            return get_settings().value("download_animation", "standard", type=str)
        except Exception:
            return "standard"

    def _get_accent(self) -> str:
        try:
            if self.main_window and hasattr(self.main_window, "accent_color") and self.main_window.accent_color:
                return self.main_window.accent_color
            from utils.settings import get_settings
            return get_settings().value("accent_color", "#C06C84")
        except Exception:
            return "#C06C84"

    def setVisible(self, visible: bool) -> None:
        super().setVisible(visible)
        if visible:
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        else:
            if self._anim_timer.isActive():
                self._anim_timer.stop()

    def _on_tick(self) -> None:
        if not self.isVisible():
            self._anim_timer.stop()
            return
        self._phase += 1.0
        if self._phase > 10000:
            self._phase = 0.0
        self.update()

    # -------------------------------------------------------------
    # Lazy Sprite Loaders
    # -------------------------------------------------------------
    def _load_goku_sprites(self) -> None:
        if self._goku_fire_pm is None:
            fire_path = str(Paths.resource("goku/goku_fire.png"))
            if os.path.exists(fire_path):
                self._goku_fire_pm = QPixmap(fire_path)
        if self._goku_charge_pm is None:
            charge_path = str(Paths.resource("goku/goku_charge.png"))
            if os.path.exists(charge_path):
                self._goku_charge_pm = QPixmap(charge_path)

    def _load_sonic_sprites(self) -> None:
        if self._sonic_stand_pm is None:
            p = str(Paths.resource("sonic/sonic_stand.png"))
            if os.path.exists(p):
                self._sonic_stand_pm = QPixmap(p)
        if not self._sonic_spin_pms:
            for i in range(1, 5):
                p = str(Paths.resource(f"sonic/sonic_spin_{i}.png"))
                if os.path.exists(p):
                    self._sonic_spin_pms.append(QPixmap(p))

    def _load_mario_sprites(self) -> None:
        if self._mario_jump_pm is None:
            p = str(Paths.resource("mario/mario_jump.png"))
            if os.path.exists(p):
                self._mario_jump_pm = QPixmap(p)
        if self._mario_coin_pm is None:
            p = str(Paths.resource("mario/mario_coin.png"))
            if os.path.exists(p):
                self._mario_coin_pm = QPixmap(p)

    def _load_shinobi_sprites(self) -> None:
        if not self._shinobi_run_pms:
            for i in range(8):
                p = str(Paths.resource(f"shinobi/shinobi_run_{i}.png"))
                if os.path.exists(p):
                    self._shinobi_run_pms.append(QPixmap(p))
        if not self._shinobi_atk_pms:
            for i in range(5):
                p = str(Paths.resource(f"shinobi/shinobi_attack_{i}.png"))
                if os.path.exists(p):
                    self._shinobi_atk_pms.append(QPixmap(p))
        if not self._samurai_shield_pms:
            for i in range(4):
                p = str(Paths.resource(f"shinobi/samurai_shield_{i}.png"))
                if os.path.exists(p):
                    self._samurai_shield_pms.append(QPixmap(p))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        w = float(rect.width())
        h = float(rect.height())
        r = h / 2.0

        accent_hex = self._get_accent()
        accent_color = QColor(accent_hex)
        anim_mode = self._get_anim_mode()

        # Modes that have NO background capsule/cylinder (pure animation on transparent canvas)
        no_capsule_modes = ("goku", "ghost", "nyancat", "megaman")

        # 1. Background Track (if applicable)
        if anim_mode not in no_capsule_modes:
            track_path = QPainterPath()
            track_path.addRoundedRect(QRectF(0, 0, w, h), r, r)

            if anim_mode == "pacman":
                painter.fillPath(track_path, QColor(0, 0, 0, 255))
                painter.setPen(QPen(QColor(33, 33, 222, 180), 1))
                painter.drawPath(track_path)
            elif anim_mode == "witch":
                painter.fillPath(track_path, QColor(5, 5, 8, 255))
                painter.setPen(QPen(QColor(140, 80, 220, 70), 1))
                painter.drawPath(track_path)
            elif anim_mode == "sonic":
                painter.fillPath(track_path, QColor(8, 15, 28, 240))
                painter.setPen(QPen(QColor(46, 204, 113, 80), 1))
                painter.drawPath(track_path)
            elif anim_mode == "mario":
                painter.fillPath(track_path, QColor(92, 148, 252, 40))
                painter.setPen(QPen(QColor(92, 148, 252, 120), 1))
                painter.drawPath(track_path)
            elif anim_mode == "shinobi":
                painter.fillPath(track_path, QColor(10, 18, 14, 250))
                painter.setPen(QPen(QColor(46, 213, 115, 80), 1))
                painter.drawPath(track_path)
            elif anim_mode == "pokemon":
                painter.fillPath(track_path, QColor(16, 24, 20, 240))
                painter.setPen(QPen(QColor(46, 213, 115, 75), 1))
                painter.drawPath(track_path)
            elif anim_mode == "car":
                painter.fillPath(track_path, QColor(10, 14, 23, 255))
                painter.setPen(QPen(QColor(50, 65, 90, 120), 1))
                painter.drawPath(track_path)
                self._draw_city_buildings(painter, w, h)
            elif anim_mode == "lantern":
                painter.fillPath(track_path, QColor(21, 13, 5, 230))
                painter.setPen(QPen(QColor(255, 117, 24, 70), 1))
                painter.drawPath(track_path)
            else:
                painter.fillPath(track_path, QColor(255, 255, 255, 20))
                painter.setPen(QPen(QColor(255, 255, 255, 35), 1))
                painter.drawPath(track_path)

        # 2. Progress Calculation
        min_v = self.minimum()
        max_v = self.maximum()
        val = self.value()

        if min_v == max_v:
            # Indeterminate wave
            wave_w = max(40.0, w * 0.25)
            cycle = (self._phase * 3.0) % (w + wave_w * 2)
            prog_x = cycle - wave_w
            fill_rect = QRectF(max(0.0, prog_x), 0, min(w, wave_w), h)
            fill_path = QPainterPath()
            fill_path.addRoundedRect(fill_rect, r, r)
            painter.fillPath(fill_path, accent_color)
            return

        if max_v <= min_v or val <= min_v:
            if anim_mode == "pacman":
                self._draw_pacman_dots(painter, 12.0, w - 6.0, h)
            elif anim_mode == "goku":
                self._draw_goku_character(painter, 0.0, h, charging=True)
            elif anim_mode == "sonic":
                self._draw_sonic_rings(painter, 14.0, w - 6.0, h)
                self._draw_sonic_runner(painter, 12.0, h, spinning=False)
            elif anim_mode == "mario":
                self._draw_mario_coins_ahead(painter, 14.0, w - 6.0, h)
                self._draw_mario_runner(painter, 10.0, h)
            elif anim_mode == "shinobi":
                self._draw_shinobi_duel(painter, 12.0, h, clashing=False)
            return

        progress_fraction = max(0.0, min(1.0, (val - min_v) / float(max_v - min_v)))
        prog_w = max(h, w * progress_fraction)

        # 3. Ahead Items in Unfinished Track
        if anim_mode == "pacman" and prog_w < w - 6:
            self._draw_pacman_dots(painter, prog_w + 6, w - 6, h)
        elif anim_mode == "sonic" and prog_w < w - 6:
            self._draw_sonic_rings(painter, prog_w + 10, w - 6, h)
        elif anim_mode == "mario" and prog_w < w - 6:
            self._draw_mario_coins_ahead(painter, prog_w + 10, w - 6, h)
        elif anim_mode == "pokemon" and prog_w < w - 6:
            self._draw_pokemon_tall_grass(painter, prog_w + 6, w - 4, h)

        # 4. Fill Progress Path
        clip_path = QPainterPath()
        if anim_mode in no_capsule_modes:
            clip_path.addRect(QRectF(0, 0, prog_w, h))
        else:
            clip_path.addRoundedRect(QRectF(0, 0, prog_w, h), r, r)

        painter.save()
        painter.setClipPath(clip_path)

        if anim_mode == "ghost":
            fog_grad = QLinearGradient(0, 0, prog_w, 0)
            fog_grad.setColorAt(0.0, QColor(255, 255, 255, 40))
            fog_grad.setColorAt(0.4, QColor(220, 240, 255, 140))
            fog_grad.setColorAt(0.85, QColor(255, 255, 255, 220))
            fog_grad.setColorAt(1.0, QColor(255, 255, 255, 250))
            painter.fillPath(clip_path, fog_grad)
        elif anim_mode == "pacman":
            arcade_grad = QLinearGradient(0, 0, prog_w, 0)
            arcade_grad.setColorAt(0.0, QColor(0, 0, 0, 255))
            arcade_grad.setColorAt(0.85, QColor(0, 0, 0, 255))
            arcade_grad.setColorAt(1.0, QColor(14, 20, 36, 255))
            painter.fillPath(clip_path, arcade_grad)
        elif anim_mode == "witch":
            witch_grad = QLinearGradient(0, 0, prog_w, 0)
            witch_grad.setColorAt(0.0, QColor(16, 7, 34, 230))
            witch_grad.setColorAt(0.6, QColor(42, 16, 78, 240))
            witch_grad.setColorAt(1.0, QColor(72, 28, 128, 250))
            painter.fillPath(clip_path, witch_grad)
            self._draw_stardust(painter, prog_w, h)
        elif anim_mode == "goku":
            self._draw_kamehameha_beam(painter, prog_w, h)
        elif anim_mode == "sonic":
            self._draw_sonic_green_hill(painter, prog_w, h)
        elif anim_mode == "nyancat":
            self._draw_nyan_rainbow(painter, prog_w, h)
        elif anim_mode == "mario":
            self._draw_mario_trail(painter, prog_w, h)
        elif anim_mode == "shinobi":
            self._draw_shinobi_trail(painter, prog_w, h)
        elif anim_mode == "megaman":
            self._draw_megaman_buster_beam(painter, prog_w, h)
        elif anim_mode == "pokemon":
            self._draw_pokemon_capture_stream(painter, prog_w, h)
        elif anim_mode == "car":
            self._draw_car_highway(painter, prog_w, h)
        elif anim_mode == "lantern":
            pumpkin_grad = QLinearGradient(0, 0, prog_w, 0)
            pumpkin_grad.setColorAt(0.0, QColor(180, 70, 0, 160))
            pumpkin_grad.setColorAt(0.7, QColor(230, 105, 15, 220))
            pumpkin_grad.setColorAt(1.0, QColor(255, 140, 20, 255))
            painter.fillPath(clip_path, pumpkin_grad)
        else:
            grad = QLinearGradient(0, 0, prog_w, 0)
            grad.setColorAt(0.0, accent_color.darker(115))
            grad.setColorAt(1.0, accent_color)
            painter.fillPath(clip_path, grad)

        painter.restore()

        # 5. Characters & Runners
        edge_x = min(w - 7.0, prog_w)

        if anim_mode == "ghost":
            self._draw_ghost(painter, edge_x, h)
        elif anim_mode == "pacman":
            self._draw_pacman(painter, edge_x, h)
        elif anim_mode == "witch":
            self._draw_witch(painter, edge_x, h)
        elif anim_mode == "goku":
            self._draw_goku_character(painter, prog_w, h, charging=False)
        elif anim_mode == "sonic":
            self._draw_sonic_runner(painter, edge_x, h, spinning=True)
        elif anim_mode == "nyancat":
            self._draw_nyan_cat_runner(painter, edge_x, h)
        elif anim_mode == "mario":
            self._draw_mario_runner(painter, edge_x, h)
        elif anim_mode == "shinobi":
            self._draw_shinobi_duel(painter, edge_x, h, clashing=True)
        elif anim_mode == "megaman":
            self._draw_megaman_character(painter, prog_w, h)
        elif anim_mode == "pokemon":
            self._draw_pokemon_ball_runner(painter, edge_x, h)
        elif anim_mode == "car":
            self._draw_car_runner(painter, min(w - 11.0, prog_w), h)
        elif anim_mode == "lantern":
            self._draw_lantern(painter, edge_x, h)

    # -------------------------------------------------------------
    # Pac-Man Implementation
    # -------------------------------------------------------------
    def _draw_pacman_dots(self, painter: QPainter, start_x: float, end_x: float, h: float) -> None:
        spacing = 14.0
        shift = (self._phase * 0.4) % spacing
        cy = h / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffeaa7"))

        cur_x = end_x - shift
        while cur_x >= start_x:
            painter.drawEllipse(QPointF(cur_x, cy), 1.8, 1.8)
            cur_x -= spacing

    def _draw_pacman(self, painter: QPainter, edge_x: float, h: float) -> None:
        pr = 6.0
        cx = max(pr, edge_x - pr + 2.0)
        cy = h / 2.0

        mouth_angle = int(abs(math.sin(self._phase * 0.35)) * 40.0) + 2
        start_angle = mouth_angle * 16
        span_angle = (360 - mouth_angle * 2) * 16

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffe600"))
        painter.drawPie(QRectF(cx - pr, cy - pr, pr * 2.0, pr * 2.0), start_angle, span_angle)

    # -------------------------------------------------------------
    # Retro Ghost Implementation (Pure floating ghost with white fog)
    # -------------------------------------------------------------
    def _draw_ghost(self, painter: QPainter, edge_x: float, h: float) -> None:
        gw = 16.0
        gh = 13.0
        bob = math.sin(self._phase * 0.18) * 1.5
        cx = max(gw / 2.0, edge_x - gw / 2.0 + 1.0)
        cy = h / 2.0 + bob

        ghost_path = QPainterPath()
        ghost_path.moveTo(cx - 5.5, cy + 5.0)
        ghost_path.lineTo(cx - 5.5, cy - 1.0)
        ghost_path.cubicTo(cx - 5.5, cy - 6.5, cx + 5.5, cy - 6.5, cx + 5.5, cy - 1.0)
        ghost_path.lineTo(cx + 5.5, cy + 5.0)
        wave = math.sin(self._phase * 0.3) * 1.0
        ghost_path.quadTo(cx + 3.0, cy + 3.5 + wave, cx + 1.0, cy + 5.0)
        ghost_path.quadTo(cx - 1.5, cy + 3.5 - wave, cx - 3.5, cy + 5.0)
        ghost_path.lineTo(cx - 5.5, cy + 5.0)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawPath(ghost_path)

        painter.setBrush(QColor("#111116"))
        painter.drawEllipse(QRectF(cx - 3.8, cy - 3.0, 2.0, 2.8))
        painter.drawEllipse(QRectF(cx + 1.0, cy - 3.0, 2.0, 2.8))
        painter.drawEllipse(QRectF(cx - 1.2, cy + 1.0, 2.2, 2.5))

    # -------------------------------------------------------------
    # Flying Witch & Stardust Implementation
    # -------------------------------------------------------------
    def _draw_stardust(self, painter: QPainter, prog_w: float, h: float) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        limit = max(0, int(prog_w) - 6)
        for star_x in range(6, limit, 10):
            twinkle = abs(math.sin(self._phase * 0.18 + star_x * 0.45))
            star_y = (math.sin(star_x * 1.3) * 0.32 + 0.5) * (h - 4) + 2
            star_r = 1.0 + twinkle * 0.9

            if (star_x // 10) % 3 == 0:
                col = QColor(255, 238, 140, int(160 + twinkle * 95))
            elif (star_x // 10) % 3 == 1:
                col = QColor(220, 160, 255, int(150 + twinkle * 105))
            else:
                col = QColor(255, 255, 255, int(170 + twinkle * 85))

            painter.setBrush(col)
            painter.drawEllipse(QPointF(star_x, star_y), star_r, star_r)

    def _draw_witch(self, painter: QPainter, edge_x: float, h: float) -> None:
        bob = math.sin(self._phase * 0.22) * 1.6
        cx = max(10.0, edge_x - 6.0)
        cy = h / 2.0 + bob

        # Golden Straw Broomstick
        painter.setPen(QPen(QColor("#d35400"), 1.8))
        painter.drawLine(QPointF(cx - 8.0, cy + 3.0), QPointF(cx + 6.0, cy - 2.0))
        painter.setPen(Qt.PenStyle.NoPen)

        straw_poly = QPolygonF([
            QPointF(cx - 7.5, cy + 2.8),
            QPointF(cx - 12.0, cy + 1.2),
            QPointF(cx - 12.5, cy + 4.8),
        ])
        painter.setBrush(QColor("#f1c40f"))
        painter.drawPolygon(straw_poly)

        # Scarlet/Ruby Cloak
        flutter = math.sin(self._phase * 0.32) * 1.0
        cloak_poly = QPolygonF([
            QPointF(cx - 1.0, cy - 1.5),
            QPointF(cx + 2.5, cy + 0.5),
            QPointF(cx - 4.5 + flutter, cy + 4.5),
            QPointF(cx - 6.0, cy + 2.0),
        ])
        painter.setBrush(QColor("#ff3838"))
        painter.drawPolygon(cloak_poly)

        painter.setPen(QPen(QColor("#ffa502"), 0.8))
        painter.drawLine(QPointF(cx - 1.0, cy - 1.5), QPointF(cx + 2.5, cy + 0.5))
        painter.setPen(Qt.PenStyle.NoPen)

        # Face
        painter.setBrush(QColor("#f5cd79"))
        painter.drawEllipse(QPointF(cx + 1.0, cy - 2.0), 1.6, 1.6)

        # Charcoal Hat with Golden Buckle
        painter.setBrush(QColor("#1e272e"))
        painter.drawEllipse(QRectF(cx - 3.5, cy - 4.5, 7.0, 2.4))
        hat_cone = QPolygonF([
            QPointF(cx - 2.2, cy - 3.5),
            QPointF(cx + 2.2, cy - 3.5),
            QPointF(cx - 1.2, cy - 8.5),
        ])
        painter.drawPolygon(hat_cone)
        painter.setBrush(QColor("#f9ca24"))
        painter.drawRect(QRectF(cx - 1.0, cy - 4.5, 2.0, 1.2))

    # -------------------------------------------------------------
    # Goku & Kamehameha (No cylinder background, pure blast)
    # -------------------------------------------------------------
    def _draw_kamehameha_beam(self, painter: QPainter, prog_w: float, h: float) -> None:
        start_x = 18.0
        cy = h / 2.0

        if prog_w <= start_x:
            pulse = abs(math.sin(self._phase * 0.35)) * 2.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#00e5ff"))
            painter.drawEllipse(QPointF(start_x, cy), 3.5 + pulse, 3.5 + pulse)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(QPointF(start_x, cy), 1.8 + pulse * 0.4, 1.8 + pulse * 0.4)
            return

        beam_path = QPainterPath()
        beam_path.moveTo(start_x, cy - 4.0)

        x = start_x
        while x < prog_w - 4.0:
            step = min(6.0, prog_w - 4.0 - x)
            spike = 1.8 if int(x + self._phase * 1.5) % 10 < 5 else 0.0
            beam_path.lineTo(x + step * 0.5, cy - 4.2 - spike)
            beam_path.lineTo(x + step, cy - 4.0)
            x += step

        beam_path.lineTo(prog_w - 2.0, cy - 4.0)
        beam_path.lineTo(prog_w - 2.0, cy + 4.0)

        x = prog_w - 2.0
        while x > start_x:
            step = min(6.0, x - start_x)
            spike = 1.8 if int(x - self._phase * 1.5) % 10 < 5 else 0.0
            beam_path.lineTo(x - step * 0.5, cy + 4.2 + spike)
            beam_path.lineTo(x - step, cy + 4.0)
            x -= step

        beam_path.closeSubpath()

        plasma_grad = QLinearGradient(start_x, 0, prog_w, 0)
        plasma_grad.setColorAt(0.0, QColor("#00d2d3"))
        plasma_grad.setColorAt(0.7, QColor("#00e5ff"))
        plasma_grad.setColorAt(1.0, QColor("#54a0ff"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(beam_path, plasma_grad)

        core_h = 3.2
        core_rect = QRectF(start_x, cy - core_h / 2.0, prog_w - start_x, core_h)
        painter.fillRect(core_rect, QColor("#FFFFFF"))

        if prog_w > start_x + 4.0:
            bx = prog_w
            head_r = 6.2

            teeth_path = QPainterPath()
            teeth_path.addEllipse(QPointF(bx, cy), head_r, head_r)
            for angle_deg in [135, 160, 180, 200, 225]:
                rad = math.radians(angle_deg)
                flare_r = head_r + 2.5 + math.sin(self._phase * 0.4 + angle_deg) * 1.2
                px = bx + math.cos(rad) * flare_r
                py = cy + math.sin(rad) * flare_r
                teeth_path.lineTo(px, py)

            painter.setBrush(QColor("#00e5ff"))
            painter.drawPath(teeth_path)

            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(QPointF(bx, cy), 3.4, 3.4)

    def _draw_goku_character(self, painter: QPainter, prog_w: float, h: float, charging: bool = False) -> None:
        self._load_goku_sprites()
        sprite = self._goku_charge_pm if charging or prog_w <= 16.0 else self._goku_fire_pm

        if sprite and not sprite.isNull():
            target_h = int(h) + 2
            target_w = int(sprite.width() * (target_h / float(sprite.height())))
            scaled = sprite.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            y = (int(h) - scaled.height()) // 2
            painter.drawPixmap(0, y, scaled)

    # -------------------------------------------------------------
    # Sonic the Hedgehog (Authentic Sprites & Green Hill)
    # -------------------------------------------------------------
    def _draw_sonic_rings(self, painter: QPainter, start_x: float, end_x: float, h: float) -> None:
        spacing = 18.0
        cy = h / 2.0
        cur_x = end_x
        painter.setPen(QPen(QColor("#b8860b"), 1.0))
        painter.setBrush(QColor("#f1c40f"))

        while cur_x >= start_x:
            spin = abs(math.cos(self._phase * 0.25 + cur_x * 0.1))
            rw = max(1.2, 3.2 * spin)
            painter.drawEllipse(QPointF(cur_x, cy), rw, 3.2)
            cur_x -= spacing

    def _draw_sonic_green_hill(self, painter: QPainter, prog_w: float, h: float) -> None:
        # Green grass top layer
        painter.fillRect(QRectF(0, 0, prog_w, 4.0), QColor("#2ecc71"))
        # Checkered dirt
        dirt_rect = QRectF(0, 4.0, prog_w, h - 4.0)
        painter.fillRect(dirt_rect, QColor("#8a5a2e"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#b07d4f"))
        for cx in range(0, int(prog_w), 8):
            painter.drawRect(QRectF(cx, 4.0, 4.0, 6.0))
            painter.drawRect(QRectF(cx + 4.0, 10.0, 4.0, 6.0))

    def _draw_sonic_runner(self, painter: QPainter, edge_x: float, h: float, spinning: bool = True) -> None:
        self._load_sonic_sprites()
        if spinning and self._sonic_spin_pms:
            frame_idx = int(self._phase * 0.4) % len(self._sonic_spin_pms)
            sprite = self._sonic_spin_pms[frame_idx]
        else:
            sprite = self._sonic_stand_pm

        if sprite and not sprite.isNull():
            target_h = int(h)
            target_w = int(sprite.width() * (target_h / float(sprite.height())))
            scaled = sprite.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            cx = max(0, int(edge_x - target_w + 1))
            cy = (int(h) - scaled.height()) // 2
            painter.drawPixmap(cx, cy, scaled)

    # -------------------------------------------------------------
    # Super Mario (NES Sprites & Coins)
    # -------------------------------------------------------------
    def _draw_mario_coins_ahead(self, painter: QPainter, start_x: float, end_x: float, h: float) -> None:
        self._load_mario_sprites()
        spacing = 22.0
        cur_x = end_x
        cy = h / 2.0

        while cur_x >= start_x:
            spin = abs(math.cos(self._phase * 0.2 + cur_x * 0.1))
            if self._mario_coin_pm and not self._mario_coin_pm.isNull():
                coin_h = 10
                coin_w = max(2, int(coin_h * (self._mario_coin_pm.width() / float(self._mario_coin_pm.height())) * spin))
                scaled = self._mario_coin_pm.scaled(
                    coin_w, coin_h,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                painter.drawPixmap(int(cur_x - coin_w / 2), int(cy - coin_h / 2), scaled)
            else:
                painter.setPen(QPen(QColor("#b8860b"), 0.8))
                painter.setBrush(QColor("#f1c40f"))
                painter.drawEllipse(QPointF(cur_x, cy), max(1.2, 3.0 * spin), 4.5)
            cur_x -= spacing

    def _draw_mario_trail(self, painter: QPainter, prog_w: float, h: float) -> None:
        painter.fillRect(QRectF(0, 0, prog_w, h), QColor("#5c94fc"))
        painter.fillRect(QRectF(0, h - 3.5, prog_w, 3.5), QColor("#c0392b"))
        painter.setPen(QPen(QColor("#000000"), 0.6))
        for bx in range(0, int(prog_w), 6):
            painter.drawLine(QPointF(bx, h - 3.5), QPointF(bx, h))

    def _draw_mario_runner(self, painter: QPainter, edge_x: float, h: float) -> None:
        self._load_mario_sprites()
        sprite = self._mario_jump_pm
        bob = math.sin(self._phase * 0.25) * 1.5

        if sprite and not sprite.isNull():
            target_h = int(h) + 1
            target_w = int(sprite.width() * (target_h / float(sprite.height())))
            scaled = sprite.scaled(
                target_w, target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            cx = max(0, int(edge_x - target_w + 1))
            cy = int((h - scaled.height()) / 2 + bob)
            painter.drawPixmap(cx, cy, scaled)

    # -------------------------------------------------------------
    # Shinobi Duel (Ninja Clash)
    # -------------------------------------------------------------
    def _draw_shinobi_trail(self, painter: QPainter, prog_w: float, h: float) -> None:
        # Midnight bamboo forest gradient
        grad = QLinearGradient(0, 0, prog_w, 0)
        grad.setColorAt(0.0, QColor("#0d2117"))
        grad.setColorAt(0.5, QColor("#143525"))
        grad.setColorAt(1.0, QColor("#1e4f38"))
        painter.fillRect(QRectF(0, 0, prog_w, h), grad)

        # Bamboo vertical stalks
        painter.setPen(QPen(QColor("#2ed573"), 1.0))
        for bx in range(8, int(prog_w), 16):
            painter.drawLine(QPointF(bx, 0), QPointF(bx, h))
            painter.drawLine(QPointF(bx - 1.5, h * 0.4), QPointF(bx + 1.5, h * 0.4))

    def _draw_shinobi_duel(self, painter: QPainter, edge_x: float, h: float, clashing: bool = True) -> None:
        self._load_shinobi_sprites()

        # Shinobi runner (Attacking or running)
        if clashing and self._shinobi_atk_pms:
            frame_idx = int(self._phase * 0.35) % len(self._shinobi_atk_pms)
            shin_pm = self._shinobi_atk_pms[frame_idx]
        elif self._shinobi_run_pms:
            frame_idx = int(self._phase * 0.3) % len(self._shinobi_run_pms)
            shin_pm = self._shinobi_run_pms[frame_idx]
        else:
            shin_pm = None

        if shin_pm and not shin_pm.isNull():
            target_h = int(h) + 1
            target_w = int(shin_pm.width() * (target_h / float(shin_pm.height())))
            scaled = shin_pm.scaled(target_w, target_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            sx = max(0, int(edge_x - target_w + 1))
            sy = (int(h) - scaled.height()) // 2
            painter.drawPixmap(sx, sy, scaled)

        # Samurai Defender at the clash point
        if clashing and self._samurai_shield_pms:
            sam_idx = int(self._phase * 0.25) % len(self._samurai_shield_pms)
            sam_pm = self._samurai_shield_pms[sam_idx]
            if sam_pm and not sam_pm.isNull():
                target_h = int(h) + 1
                target_w = int(sam_pm.width() * (target_h / float(sam_pm.height())))
                scaled_sam = sam_pm.scaled(target_w, target_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                sam_x = int(edge_x + 2)
                sam_y = (int(h) - scaled_sam.height()) // 2
                painter.drawPixmap(sam_x, sam_y, scaled_sam)

                # Clashing sword sparks
                spark_x = edge_x + 1.0
                spark_y = h / 2.0
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#fff200"))
                painter.drawEllipse(QPointF(spark_x, spark_y), 2.2, 2.2)
                painter.setBrush(QColor("#FFFFFF"))
                painter.drawEllipse(QPointF(spark_x, spark_y), 1.0, 1.0)

    # -------------------------------------------------------------
    # Nyan Cat & Rainbow Stream (No capsule)
    # -------------------------------------------------------------
    def _draw_nyan_rainbow(self, painter: QPainter, prog_w: float, h: float) -> None:
        rainbow_colors = [
            QColor("#ff0000"),
            QColor("#ff9900"),
            QColor("#ffff00"),
            QColor("#33ff00"),
            QColor("#0099ff"),
            QColor("#6633ff"),
        ]
        stripe_h = h / len(rainbow_colors)

        for idx, col in enumerate(rainbow_colors):
            base_y = idx * stripe_h
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(col)

            path = QPainterPath()
            path.moveTo(0, base_y)

            x = 0.0
            while x < prog_w:
                wave = math.sin(self._phase * 0.35 + x * 0.12) * 1.4
                path.lineTo(x, base_y + wave)
                x += 4.0

            path.lineTo(prog_w, base_y + math.sin(self._phase * 0.35 + prog_w * 0.12) * 1.4 + stripe_h)

            while x > 0:
                wave = math.sin(self._phase * 0.35 + x * 0.12) * 1.4
                path.lineTo(x, base_y + wave + stripe_h)
                x -= 4.0

            path.closeSubpath()
            painter.drawPath(path)

    def _draw_nyan_cat_runner(self, painter: QPainter, edge_x: float, h: float) -> None:
        cx = max(10.0, edge_x - 8.0)
        bob = math.sin(self._phase * 0.35 + edge_x * 0.12) * 1.4
        cy = h / 2.0 + bob

        painter.setPen(Qt.PenStyle.NoPen)
        # Pop-Tart Toaster Pastry
        painter.setBrush(QColor("#f8c291"))
        painter.drawRoundedRect(QRectF(cx - 7.0, cy - 4.5, 11.0, 9.0), 1.5, 1.5)
        painter.setBrush(QColor("#ff9ff3"))
        painter.drawRoundedRect(QRectF(cx - 6.0, cy - 3.5, 9.0, 7.0), 1.0, 1.0)

        # Sprinkles
        painter.setBrush(QColor("#e84118"))
        painter.drawRect(QRectF(cx - 4.0, cy - 2.0, 1.0, 1.0))
        painter.drawRect(QRectF(cx + 0.5, cy + 1.0, 1.0, 1.0))
        painter.setBrush(QColor("#00d2d3"))
        painter.drawRect(QRectF(cx - 2.0, cy + 1.0, 1.0, 1.0))

        # Gray Cat Head
        painter.setBrush(QColor("#95afc0"))
        painter.drawRect(QRectF(cx + 2.5, cy - 3.5, 5.5, 5.5))
        ears = QPolygonF([
            QPointF(cx + 2.5, cy - 3.5),
            QPointF(cx + 4.0, cy - 6.0),
            QPointF(cx + 5.5, cy - 3.5),
        ])
        painter.drawPolygon(ears)
        painter.setBrush(QColor("#111116"))
        painter.drawRect(QRectF(cx + 4.0, cy - 2.0, 1.2, 1.2))
        painter.drawRect(QRectF(cx + 6.5, cy - 2.0, 1.2, 1.2))
        painter.setBrush(QColor("#ff4757"))
        painter.drawRect(QRectF(cx + 3.0, cy - 0.5, 1.2, 1.2))

    # -------------------------------------------------------------
    # Mega Man & Mega Buster Plasma Beam (No capsule)
    # -------------------------------------------------------------
    def _draw_megaman_buster_beam(self, painter: QPainter, prog_w: float, h: float) -> None:
        start_x = 16.0
        cy = h / 2.0

        if prog_w <= start_x:
            pulse = abs(math.sin(self._phase * 0.4)) * 2.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#00d2d3"))
            painter.drawEllipse(QPointF(start_x, cy), 3.0 + pulse, 3.0 + pulse)
            return

        plasma_grad = QLinearGradient(start_x, 0, prog_w, 0)
        plasma_grad.setColorAt(0.0, QColor("#0984e3"))
        plasma_grad.setColorAt(0.5, QColor("#00cec9"))
        plasma_grad.setColorAt(1.0, QColor("#ffeaa7"))
        painter.fillRect(QRectF(start_x, cy - 3.5, prog_w - start_x, 7.0), plasma_grad)

        painter.fillRect(QRectF(start_x, cy - 1.5, prog_w - start_x, 3.0), QColor("#FFFFFF"))

        painter.setPen(QPen(QColor("#fff200"), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for rx in range(int(start_x) + 8, int(prog_w), 16):
            painter.drawEllipse(QPointF(rx, cy), 2.5, 4.5)

    def _draw_megaman_character(self, painter: QPainter, prog_w: float, h: float) -> None:
        gx = 6.0
        cy = h / 2.0

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0984e3"))
        painter.drawRect(QRectF(gx - 4.0, cy - 6.0, 7.0, 5.0))
        painter.setBrush(QColor("#f5cd79"))
        painter.drawRect(QRectF(gx - 1.5, cy - 3.5, 4.0, 2.8))
        painter.setBrush(QColor("#00cec9"))
        painter.drawRect(QRectF(gx + 2.0, cy - 1.5, 6.0, 3.0))
        painter.setBrush(QColor("#0984e3"))
        painter.drawRect(QRectF(gx - 3.0, cy - 1.0, 5.0, 5.0))

    # -------------------------------------------------------------
    # Pokémon Pokéball Catch
    # -------------------------------------------------------------
    def _draw_pokemon_tall_grass(self, painter: QPainter, start_x: float, end_x: float, h: float) -> None:
        spacing = 16.0
        cur_x = end_x
        painter.setPen(QPen(QColor("#2ecc71"), 1.2))

        while cur_x >= start_x:
            painter.drawLine(QPointF(cur_x, h), QPointF(cur_x - 1.5, h - 5.5))
            painter.drawLine(QPointF(cur_x, h), QPointF(cur_x + 1.5, h - 6.5))
            cur_x -= spacing

    def _draw_pokemon_capture_stream(self, painter: QPainter, prog_w: float, h: float) -> None:
        grad = QLinearGradient(0, 0, prog_w, 0)
        grad.setColorAt(0.0, QColor("#e74c3c"))
        grad.setColorAt(0.5, QColor("#ecf0f1"))
        grad.setColorAt(1.0, QColor("#3498db"))
        painter.fillRect(QRectF(0, h / 2.0 - 1.5, prog_w, 3.0), grad)

    def _draw_pokemon_ball_runner(self, painter: QPainter, edge_x: float, h: float) -> None:
        br = 5.5
        cx = max(br, edge_x - br + 1.0)
        cy = h / 2.0

        painter.save()
        painter.translate(cx, cy)
        angle = (self._phase * 15.0) % 360.0
        painter.rotate(angle)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e74c3c"))
        painter.drawPie(QRectF(-br, -br, br * 2.0, br * 2.0), 0, 180 * 16)
        painter.setBrush(QColor("#ffffff"))
        painter.drawPie(QRectF(-br, -br, br * 2.0, br * 2.0), 180 * 16, 180 * 16)

        painter.setBrush(QColor("#2c3e50"))
        painter.drawRect(QRectF(-br, -0.8, br * 2.0, 1.6))
        painter.drawEllipse(QPointF(0, 0), 1.8, 1.8)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QPointF(0, 0), 0.9, 0.9)

        painter.restore()

    # -------------------------------------------------------------
    # City Drive (Retro Car)
    # -------------------------------------------------------------
    def _draw_city_buildings(self, painter: QPainter, w: float, h: float) -> None:
        building_widths = [12.0, 8.0, 14.0, 10.0, 16.0, 9.0, 13.0]
        building_heights = [7.0, 9.5, 6.0, 10.5, 8.0, 6.5, 10.0]
        total_cycle = sum(building_widths) + len(building_widths) * 2.0

        scroll = (self._phase * 0.6) % total_cycle

        cur_x = -scroll
        idx = 0
        painter.setPen(Qt.PenStyle.NoPen)

        while cur_x < w:
            bw = building_widths[idx % len(building_widths)]
            bh = building_heights[idx % len(building_heights)]

            if cur_x + bw > 0:
                b_rect = QRectF(cur_x, h - bh, bw, bh)
                painter.setBrush(QColor(22, 30, 46, 220))
                painter.drawRect(b_rect)

                win_y = h - bh + 2.0
                while win_y < h - 2.0:
                    win_x = cur_x + 2.5
                    while win_x < cur_x + bw - 2.0:
                        is_lit = (int(win_x * 7 + win_y * 13) % 5 == 0)
                        if is_lit:
                            painter.setBrush(QColor("#fed330"))
                            painter.drawRect(QRectF(win_x, win_y, 1.2, 1.2))
                        win_x += 3.0
                    win_y += 2.5

            cur_x += bw + 2.0
            idx += 1

    def _draw_car_highway(self, painter: QPainter, prog_w: float, h: float) -> None:
        painter.fillRect(QRectF(0, 0, prog_w, h), QColor("#222f3e"))
        dash_len = 6.0
        dash_gap = 4.0
        dash_stride = dash_len + dash_gap
        offset = (self._phase * 0.7) % dash_stride

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#feca57"))
        x = -offset
        while x < prog_w:
            if x + dash_len > 0:
                d_start = max(0.0, x)
                d_w = min(prog_w - d_start, (x + dash_len) - d_start)
                if d_w > 0:
                    painter.drawRect(QRectF(d_start, h / 2.0 - 0.5, d_w, 1.0))
            x += dash_stride

    def _draw_car_runner(self, painter: QPainter, edge_x: float, h: float) -> None:
        """Sleek sports car driving forward to the right with glowing forward headlights."""
        cw = 15.0
        cx = max(cw, edge_x)
        cy = h / 2.0 + 0.5

        painter.setPen(Qt.PenStyle.NoPen)
        # Headlight beam projecting to the right (+x forward)
        beam_poly = QPolygonF([
            QPointF(cx + 0.5, cy - 1.0),
            QPointF(cx + 14.0, cy - 3.5),
            QPointF(cx + 14.0, cy + 3.0),
            QPointF(cx + 0.5, cy + 1.0),
        ])
        beam_grad = QLinearGradient(cx, 0, cx + 14.0, 0)
        beam_grad.setColorAt(0.0, QColor(255, 234, 167, 180))
        beam_grad.setColorAt(1.0, QColor(255, 234, 167, 0))
        painter.setBrush(beam_grad)
        painter.drawPolygon(beam_poly)

        # Aerodynamic Car Body facing right (+x forward)
        car_body = QPolygonF([
            QPointF(cx - cw, cy + 1.0),       # Rear bottom bumper
            QPointF(cx - cw + 2.0, cy - 1.5), # Rear trunk/spoiler
            QPointF(cx - 9.0, cy - 1.5),      # Rear roofline
            QPointF(cx - 7.0, cy - 3.5),      # Cabin rear
            QPointF(cx - 2.5, cy - 3.5),      # Cabin roof
            QPointF(cx - 0.5, cy - 1.0),      # Sloped front hood
            QPointF(cx, cy + 1.0),            # Front bumper (+x)
        ])
        painter.setBrush(QColor("#ee5253"))
        painter.drawPolygon(car_body)

        # Windshield
        windshield = QPolygonF([
            QPointF(cx - 6.8, cy - 3.0),
            QPointF(cx - 3.0, cy - 3.0),
            QPointF(cx - 1.5, cy - 1.2),
            QPointF(cx - 6.0, cy - 1.2),
        ])
        painter.setBrush(QColor("#48dbfb"))
        painter.drawPolygon(windshield)

        # Spinning Wheels
        for wx in [cx - 11.5, cx - 3.0]:
            painter.setBrush(QColor("#1e272e"))
            painter.drawEllipse(QPointF(wx, cy + 1.8), 2.0, 2.0)
            painter.setBrush(QColor("#c8d6e5"))
            painter.drawEllipse(QPointF(wx, cy + 1.8), 0.7, 0.7)

    # -------------------------------------------------------------
    # Jack-o'-Lantern Implementation
    # -------------------------------------------------------------
    def _draw_lantern(self, painter: QPainter, edge_x: float, h: float) -> None:
        lantern_w = 16.0
        lantern_h = 13.0
        cx = max(lantern_w / 2.0, edge_x - lantern_w / 2.0)
        cy = h / 2.0

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ff7518"))
        painter.drawEllipse(QRectF(cx - lantern_w / 2.0, cy - lantern_h / 2.0, lantern_w, lantern_h))

        painter.setBrush(QColor("#4cd137"))
        painter.drawRect(QRectF(cx - 1.0, cy - lantern_h / 2.0 - 2.5, 2.0, 3.0))

        painter.setBrush(QColor("#fff200"))
        painter.drawPolygon(QPolygonF([
            QPointF(cx - 4.0, cy - 1.0),
            QPointF(cx - 2.0, cy - 1.0),
            QPointF(cx - 3.0, cy - 3.0),
        ]))
        painter.drawPolygon(QPolygonF([
            QPointF(cx + 2.0, cy - 1.0),
            QPointF(cx + 4.0, cy - 1.0),
            QPointF(cx + 3.0, cy - 3.0),
        ]))
        painter.drawPolygon(QPolygonF([
            QPointF(cx - 4.0, cy + 2.0),
            QPointF(cx - 2.0, cy + 4.0),
            QPointF(cx, cy + 2.5),
            QPointF(cx + 2.0, cy + 4.0),
            QPointF(cx + 4.0, cy + 2.0),
        ]))
