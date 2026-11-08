"""Universal Flat-Vector Engine (VectorKit / v) on Speckled Paper Canvas."""
import os
import math
from functools import lru_cache
from typing import Dict, Tuple, List, Optional, Any
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

from .mathutil import (
    hex_to_rgb, clamp, ease_out_cubic, ease_in_out_cubic,
    ease_out_back, ease_out_elastic, rotate_point, sample_bezier_curve
)

_PAPER_CACHE: Dict[Tuple[int, int, str], Image.Image] = {}


def get_speckled_paper_canvas(width: int, height: int, bg_hex: str) -> Image.Image:
    key = (width, height, bg_hex)
    if key in _PAPER_CACHE:
        return _PAPER_CACHE[key].copy()

    bg_rgb = hex_to_rgb(bg_hex, (242, 234, 218))
    base = Image.new("RGB", (width, height), bg_rgb)
    draw = ImageDraw.Draw(base)

    rng = np.random.default_rng(seed=77)
    num_specks = int((width * height) / 9500)
    xs = rng.integers(0, width, size=num_specks)
    ys = rng.integers(0, height, size=num_specks)
    radii = rng.choice([2, 2, 3, 3, 4], size=num_specks)

    speck_colors = [
        (215, 130, 75),
        (95, 145, 110),
        (165, 155, 140),
        (190, 95, 80),
    ]
    for i, (x, y, r) in enumerate(zip(xs, ys, radii)):
        sc = speck_colors[i % len(speck_colors)]
        blended = (
            int(bg_rgb[0] * 0.45 + sc[0] * 0.55),
            int(bg_rgb[1] * 0.45 + sc[1] * 0.55),
            int(bg_rgb[2] * 0.45 + sc[2] * 0.55),
        )
        draw.ellipse([int(x - r), int(y - r), int(x + r), int(y + r)], fill=blended)

    _PAPER_CACHE[key] = base
    return base.copy()


@lru_cache(maxsize=256)
def load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """Cached: `_fit_font` probes several sizes per text line per frame, and every probe used to re-read
    the TTF from disk (thousands of file loads on a long render)."""
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Black.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/Library/Fonts/Arial Black.ttf",
        "C:/Windows/Fonts/ariblk.ttf" if bold else "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/impact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, max(10, int(size)))
            except Exception:
                continue
    return ImageFont.load_default()


class VectorKit:
    """
    Universal Flat-Vector Drawing Engine (`v`) for ANY topic.
    - ZERO hardcoded topic presets (no yawning face or chili fallback!).
    - Uses a super intuitive Local Stage Coordinate System where `(0, 0)` is the center of the
      illustration stage (`self.stage_cx, self.stage_cy`), and coordinates are in virtual pixels
      (`-450 .. +450`), automatically scaled by `self.S`.
    - Typography & Layout headers (`three_dots`, `poster_lines`, `statement_pill_poster`) are
      rendered automatically from the Beat Storyboard JSON so the Python code focuses 100% on
      building rich custom vector art!
    """
    def __init__(self, width: int, height: int, t: float, beat: dict, words: List[dict], palette: dict):
        self.W = width
        self.H = height
        self.S = min(width, height) / 1080.0
        self.t = float(t)
        self.start = float(beat.get("start", 0.0))
        self.end = float(beat.get("end", 2.5))
        self.dur = max(0.25, self.end - self.start)
        self.lt = max(0.0, self.t - self.start)
        self.p = clamp(self.lt / self.dur, 0.0, 1.0)
        self.beat = beat
        self.palette = palette
        self.shape_count = 0

        bg_key = str(beat.get("bg_color", "cream")).lower().strip()
        bg_hex = self._resolve_hex(bg_key)
        self.img = get_speckled_paper_canvas(width, height, bg_hex)
        self.draw = ImageDraw.Draw(self.img, "RGBA")

        # Determine Stage Center `(stage_cx, stage_cy)` based on beat layout
        layout = str(beat.get("layout", "top_text_bottom_art")).lower()
        self.stage_cx = width // 2
        if layout in ("triptych_dots", "top_art_bottom_text"):
            self.stage_cy = int(height * 0.43)
        elif layout == "statement_pill":
            self.stage_cy = int(height * 0.77)
        else:
            # Default: Top title + center/lower illustration stage
            self.stage_cy = int(height * 0.60)

    def _resolve_hex(self, key_or_hex: str) -> str:
        mapping = {
            "cream": self.palette.get("cream", "#F2EADA"),
            "bg": self.palette.get("cream", "#F2EADA"),
            "orange_bg": self.palette.get("orange_bg", "#EE8631"),
            "orange": self.palette.get("orange_bg", "#EE8631"),
            "green_bg": self.palette.get("green_bg", "#38844C"),
            "green": self.palette.get("green_bg", "#38844C"),
            "blue_bg": self.palette.get("blue_bg", "#2680C2"),
            "dark": self.palette.get("dark", "#181818"),
            "black": "#181818",
            "red": self.palette.get("red_hot", "#EC4C28"),
            "red_hot": self.palette.get("red_hot", "#EC4C28"),
            "yellow": self.palette.get("yellow_stage", "#F6C23E"),
            "yellow_stage": self.palette.get("yellow_stage", "#F6C23E"),
            "blue_soft": self.palette.get("blue_soft", "#B3D9EE"),
            "blue": self.palette.get("blue_tear", "#2680C2"),
            "blue_tear": self.palette.get("blue_tear", "#2680C2"),
            "mint": self.palette.get("mint_soft", "#B5DEC8"),
            "mint_soft": self.palette.get("mint_soft", "#B5DEC8"),
            "pink": self.palette.get("pink_organ", "#F38375"),
            "pink_organ": self.palette.get("pink_organ", "#F38375"),
            "pink_soft": self.palette.get("pink_soft", "#F5A3A2"),
            "purple": self.palette.get("purple_soft", "#C8B6E2"),
            "skin": self.palette.get("skin", "#F7C6A3"),
            "skin_dark": self.palette.get("skin_dark", "#E0A37B"),
            "white": "#FFFFFF",
            "grey": "#7F7B74",
            "stone": "#6E6A63",
            "stone_dark": "#4A4742",
            "brown": "#6E3B1E",
        }
        return mapping.get(str(key_or_hex).lower().strip(), str(key_or_hex))

    def col(self, key_or_hex: str, alpha: int = 255) -> Tuple[int, int, int, int]:
        rgb = hex_to_rgb(self._resolve_hex(key_or_hex), (24, 24, 24))
        return (rgb[0], rgb[1], rgb[2], int(clamp(alpha, 0, 255)))

    # --- Stage Coordinate Converters (`(0,0)` = Stage Center, units in virtual 1080p pixels) ---
    def sx(self, x: float) -> int:
        return int(self.stage_cx + x * self.S)

    def sy(self, y: float) -> int:
        return int(self.stage_cy + y * self.S)

    def ss(self, s: float) -> int:
        return max(1, int(s * self.S))

    # --- Physics & Animation Timing Helpers ---
    def ease(self, delay: float = 0.0, dur: float = 0.45) -> float:
        return ease_out_cubic((self.lt - delay) / max(0.05, dur))

    def spring(self, delay: float = 0.0, dur: float = 0.48, overshoot: float = 1.65) -> float:
        return ease_out_back((self.lt - delay) / max(0.05, dur), overshoot=overshoot)

    def elastic(self, delay: float = 0.0, dur: float = 0.60) -> float:
        return ease_out_elastic((self.lt - delay) / max(0.05, dur))

    def wave(self, freq: float = 2.0, amp: float = 15.0, phase: float = 0.0) -> float:
        return math.sin(self.lt * freq * 2.0 * math.pi + phase) * amp

    def pulse(self, freq: float = 2.5, lo: float = 0.94, hi: float = 1.06, phase: float = 0.0) -> float:
        mid = (lo + hi) * 0.5
        amp = (hi - lo) * 0.5
        return mid + math.sin(self.lt * freq * 2.0 * math.pi + phase) * amp

    def _fit_font(self, text: str, target_px: int, max_w_ratio: float = 0.88) -> ImageFont.FreeTypeFont:
        sz = int(target_px * self.S)
        max_w = int(self.W * max_w_ratio)
        while sz > 18:
            f = load_font(sz, bold=True)
            bb = self.draw.textbbox((0, 0), text, font=f)
            if (bb[2] - bb[0]) <= max_w:
                return f
            sz -= 5
        return load_font(18, bold=True)

    # ==========================================
    # 1. UNIVERSAL FLAT-VECTOR SHAPE PRIMITIVES
    # ==========================================
    def backdrop_circle(self, r: float = 340, color: str = "yellow_stage", x: float = 0, y: float = 0, delay: float = 0.0, **kwargs):
        """Draws a soft circular stage backdrop centered at `(x, y)` relative to stage center."""
        p = self.spring(delay, 0.48, overshoot=1.3)
        if p <= 0.02:
            return
        self.shape_count += 1
        cx, cy, pr = self.sx(x), self.sy(y), int(self.ss(r) * p)
        self.draw.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=self.col(color))

    def circle(
        self,
        x: float,
        y: float,
        r: float,
        color: str = "red_hot",
        alpha: int = 255,
        outline: Optional[str] = None,
        width: float = 0,
        delay: float = 0.0,
        **kwargs
    ):
        p = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p <= 0.02:
            return
        self.shape_count += 1
        cx, cy = self.sx(x), self.sy(y)
        pr = int(self.ss(r) * p)
        out_c = self.col(outline, alpha) if outline else None
        self.draw.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=self.col(color, alpha) if color else None, outline=out_c, width=self.ss(width) if width > 0 else 0)

    def ellipse(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        color: str = "pink_organ",
        angle: float = 0.0,
        alpha: int = 255,
        delay: float = 0.0,
        **kwargs
    ):
        p = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p <= 0.02:
            return
        self.shape_count += 1
        cx, cy = self.sx(x), self.sy(y)
        pw, ph = max(2, int(self.ss(w) * p)), max(2, int(self.ss(h) * p))
        if abs(angle) < 0.5:
            self.draw.ellipse([cx - pw // 2, cy - ph // 2, cx + pw // 2, cy + ph // 2], fill=self.col(color, alpha))
        else:
            pad = 20
            layer = Image.new("RGBA", (pw + pad * 2, ph + pad * 2), (0, 0, 0, 0))
            ldraw = ImageDraw.Draw(layer)
            ldraw.ellipse([pad, pad, pad + pw, pad + ph], fill=self.col(color, alpha))
            rot = layer.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)
            self.img.paste(rot, (cx - rot.width // 2, cy - rot.height // 2), rot)
            self.draw = ImageDraw.Draw(self.img, "RGBA")

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        radius: float = 20,
        color: str = "white",
        angle: float = 0.0,
        alpha: int = 255,
        shadow: bool = False,
        delay: float = 0.0,
        outline: Optional[str] = None,
        width: float = 0,
        **kwargs
    ):
        """Draws a flat-vector rounded rectangle centered at `(x, y)` with optional rotation and soft shadow."""
        p = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p <= 0.02:
            return
        self.shape_count += 1
        cx, cy = self.sx(x), self.sy(y)
        pw, ph = max(2, int(self.ss(w) * p)), max(2, int(self.ss(h) * p))
        rad = min(pw // 2, ph // 2, self.ss(radius))

        out_c = self.col(outline, alpha) if outline else None
        w_out = self.ss(width) if width > 0 else (max(1, self.ss(2)) if outline else 0)

        if abs(angle) < 0.5:
            x1, y1, x2, y2 = cx - pw // 2, cy - ph // 2, cx + pw // 2, cy + ph // 2
            if shadow:
                so = self.ss(10)
                self.draw.rounded_rectangle([x1 + so, y1 + so, x2 + so, y2 + so], radius=rad, fill=(20, 20, 20, 45))
            self.draw.rounded_rectangle([x1, y1, x2, y2], radius=rad, fill=self.col(color, alpha) if color else None, outline=out_c, width=w_out)
        else:
            pad = 24
            layer = Image.new("RGBA", (pw + pad * 2, ph + pad * 2), (0, 0, 0, 0))
            ldraw = ImageDraw.Draw(layer)
            ldraw.rounded_rectangle([pad, pad, pad + pw, pad + ph], radius=rad, fill=self.col(color, alpha) if color else None, outline=out_c, width=w_out)
            rot = layer.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)
            self.img.paste(rot, (cx - rot.width // 2, cy - rot.height // 2), rot)
            self.draw = ImageDraw.Draw(self.img, "RGBA")

    def poly(
        self,
        points: List[Tuple[float, float]],
        color: str = "red_hot",
        alpha: int = 255,
        dx: float = 0.0,
        dy: float = 0.0,
        scale: float = 1.0,
        angle: float = 0.0,
        delay: float = 0.0,
        **kwargs
    ):
        """Draws a custom flat-vector polygon with local translation, scale, and rotation."""
        p = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p <= 0.02 or len(points) < 3:
            return
        self.shape_count += 1
        sc = scale * p
        transformed = []
        for px, py in points:
            tx, ty = px * sc, py * sc
            if abs(angle) > 0.1:
                rad = math.radians(angle)
                rx = tx * math.cos(rad) - ty * math.sin(rad)
                ry = tx * math.sin(rad) + ty * math.cos(rad)
                tx, ty = rx, ry
            transformed.append((self.sx(tx + dx), self.sy(ty + dy)))
        self.draw.polygon(transformed, fill=self.col(color, alpha))

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        width: float = 12,
        color: str = "dark",
        alpha: int = 255,
        progress: float = 1.0,
        dashed: bool = False,
        dash_speed: float = 140.0,
        delay: float = 0.0,
        **kwargs
    ):
        p = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p <= 0.02:
            return
        pr = clamp(progress * p, 0.0, 1.0)
        if pr <= 0.01:
            return
        self.shape_count += 1
        sx1, sy1 = self.sx(x1), self.sy(y1)
        sx2, sy2 = self.sx(x2), self.sy(y2)
        ex = int(sx1 + (sx2 - sx1) * pr)
        ey = int(sy1 + (sy2 - sy1) * pr)
        w_px = self.ss(width)
        c = self.col(color, alpha)

        if not dashed:
            self.draw.line([(sx1, sy1), (ex, ey)], fill=c, width=w_px, joint="curve")
            r_cap = w_px // 2
            self.draw.ellipse([sx1 - r_cap, sy1 - r_cap, sx1 + r_cap, sy1 + r_cap], fill=c)
            self.draw.ellipse([ex - r_cap, ey - r_cap, ex + r_cap, ey + r_cap], fill=c)
        else:
            dist = max(1.0, math.hypot(ex - sx1, ey - sy1))
            ux, uy = (ex - sx1) / dist, (ey - sy1) / dist
            dash_len = self.ss(28)
            gap_len = self.ss(22)
            step = dash_len + gap_len
            offset = (self.lt * dash_speed * self.S) % step
            cur = -offset
            while cur < dist:
                d_start = max(0.0, cur)
                d_end = min(dist, cur + dash_len)
                if d_end > d_start + 2:
                    p1 = (int(sx1 + ux * d_start), int(sy1 + uy * d_start))
                    p2 = (int(sx1 + ux * d_end), int(sy1 + uy * d_end))
                    self.draw.line([p1, p2], fill=c, width=w_px)
                cur += step

    def arc(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        start: float = 0,
        end: float = 180,
        width: float = 14,
        color: str = "dark",
        alpha: int = 255,
        delay: float = 0.0,
        **kwargs
    ):
        p = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p <= 0.02:
            return
        self.shape_count += 1
        cx, cy = self.sx(x), self.sy(y)
        pw, ph = max(2, int(self.ss(w) * p)), max(2, int(self.ss(h) * p))
        self.draw.arc([cx - pw // 2, cy - ph // 2, cx + pw // 2, cy + ph // 2], start=start, end=end, fill=self.col(color, alpha), width=self.ss(width))

    def bezier(
        self,
        points: List[Tuple[float, float]],
        width: float = 14,
        color: str = "red_hot",
        fill: Optional[str] = None,
        alpha: int = 255,
        progress: float = 1.0,
        angle: float = 0.0,
        delay: float = 0.0,
        **kwargs
    ):
        """Draws a smooth curved Bezier path or filled organic shape from control points."""
        if len(points) < 2:
            return
        p = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p <= 0.02:
            return
        self.shape_count += 1
        if abs(angle) > 0.1:
            rad = math.radians(angle)
            cos_a, sin_a = math.cos(rad), math.sin(rad)
            pts = [(px * cos_a - py * sin_a, px * sin_a + py * cos_a) for px, py in points]
        else:
            pts = points
        screen_pts = [(self.sx(px * p), self.sy(py * p)) for px, py in pts]
        curve = sample_bezier_curve(screen_pts, steps=32)
        n_keep = max(2, int(len(curve) * clamp(progress, 0.0, 1.0)))
        sliced = curve[:n_keep]
        if fill and len(sliced) >= 3:
            self.draw.polygon(sliced, fill=self.col(fill, alpha))
        if width > 0:
            self.draw.line(sliced, fill=self.col(color, alpha), width=self.ss(width), joint="curve")

    def wave_line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        amp: float = 22.0,
        freq: float = 3.5,
        speed: float = 8.0,
        width: float = 10.0,
        color: str = "blue_tear",
        alpha: int = 255,
        progress: float = 1.0,
        delay: float = 0.0,
        **kwargs
    ):
        """Draws an animated sine wave traveling between `(x1, y1)` and `(x2, y2)` (ideal for soundwaves, bone vibration, heat, signals)."""
        p_spring = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p_spring <= 0.02:
            return
        pr = clamp(progress * p_spring, 0.0, 1.0)
        if pr <= 0.02:
            return
        self.shape_count += 1
        sx1, sy1 = self.sx(x1), self.sy(y1)
        sx2, sy2 = self.sx(x2), self.sy(y2)
        dx, dy = sx2 - sx1, sy2 - sy1
        dist = max(1.0, math.hypot(dx, dy))
        ux, uy = dx / dist, dy / dist
        nx, ny = -uy, ux  # perpendicular normal vector

        steps = 40
        max_step = max(2, int(steps * pr))
        amp_px = amp * self.S
        pts = []
        for i in range(max_step + 1):
            u = i / float(steps)
            env = math.sin(u * math.pi)  # smooth taper at endpoints
            w_off = math.sin(u * freq * 2.0 * math.pi - self.lt * speed) * amp_px * env
            px = int(sx1 + dx * u + nx * w_off)
            py = int(sy1 + dy * u + ny * w_off)
            pts.append((px, py))
        if len(pts) >= 2:
            self.draw.line(pts, fill=self.col(color, alpha), width=self.ss(width), joint="curve")

    def concentric_waves(
        self,
        x: float,
        y: float,
        min_r: float = 45,
        max_r: float = 240,
        count: int = 3,
        color: str = "red_hot",
        width: float = 10,
        start_angle: float = 0,
        end_angle: float = 360,
        delay: float = 0.0,
        **kwargs
    ):
        """Draws expanding sonar / sound / acoustic / shockwave rings or directional arcs."""
        p = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p <= 0.02:
            return
        self.shape_count += 1
        cx, cy = self.sx(x), self.sy(y)
        r0, r1 = self.ss(min_r) * p, self.ss(max_r) * p
        for i in range(max(1, int(count))):
            rp = (self.lt * 0.75 + i / float(max(1, count))) % 1.0
            rr = int(r0 + (r1 - r0) * rp)
            alpha = int(235 * (1.0 - rp))
            if abs(end_angle - start_angle) >= 359:
                self.draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=self.col(color, alpha), width=self.ss(width))
            else:
                self.draw.arc([cx - rr, cy - rr, cx + rr, cy + rr], start=start_angle, end=end_angle, fill=self.col(color, alpha), width=self.ss(width))

    def waveform_bars(
        self,
        x: float,
        y: float,
        w: float = 380,
        h: float = 130,
        count: int = 13,
        color: str = "red_hot",
        bg_color: Optional[str] = None,
        speed: float = 9.0,
        delay: float = 0.0,
        **kwargs
    ):
        """Draws animated audio / voice-note equalizer bars."""
        p = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p <= 0.02:
            return
        self.shape_count += 2
        if bg_color:
            self.rect(x, y, w + 60, h + 50, radius=28, color=bg_color, shadow=True)
        cx, cy = self.sx(x), self.sy(y)
        pw, ph = int(self.ss(w) * p), int(self.ss(h) * p)
        n = max(3, int(count))
        gap = self.ss(10)
        bw = max(6, (pw - gap * (n - 1)) // n)
        start_x = cx - pw // 2

        for i in range(n):
            wave_val = 0.25 + 0.75 * abs(math.sin(self.lt * speed + i * 0.65))
            bh = max(bw, int(ph * wave_val * self.ease(i * 0.02, 0.35)))
            bx = start_x + i * (bw + gap)
            self.draw.rounded_rectangle([bx, cy - bh // 2, bx + bw, cy + bh // 2], radius=bw // 2, fill=self.col(color))

    def blob(
        self,
        x: float,
        y: float,
        r: float = 135,
        lobes: int = 7,
        color: str = "pink_soft",
        delay: float = 0.0,
        alpha: int = 255,
        **kwargs
    ):
        """Draws an organic multi-lobed cloud / brain / cell / organ silhouette."""
        p = self.spring(delay, 0.48)
        if p <= 0.03:
            return
        self.shape_count += 2
        cx, cy = self.sx(x), self.sy(y)
        base_r = int(self.ss(r) * p)
        c = self.col(color, alpha)
        self.draw.ellipse([cx - base_r, cy - int(base_r * 0.85), cx + base_r, cy + int(base_r * 0.85)], fill=c)
        for i in range(max(4, int(lobes))):
            ang = (2.0 * math.pi * i) / lobes + math.sin(self.lt * 2.0 + i) * 0.05
            lx = cx + int(math.cos(ang) * base_r * 0.62)
            ly = cy + int(math.sin(ang) * base_r * 0.52)
            lr = int(base_r * 0.58)
            self.draw.ellipse([lx - lr, ly - lr, lx + lr, ly + lr], fill=c)

    def arrow(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        width: float = 14,
        color: str = "red_hot",
        delay: float = 0.1,
        dur: float = 0.45,
        alpha: int = 255,
        **kwargs
    ):
        p = self.ease(delay, dur)
        if p <= 0.04:
            return
        self.shape_count += 1
        sx1, sy1 = self.sx(x1), self.sy(y1)
        sx2, sy2 = self.sx(x2), self.sy(y2)
        cx = int(sx1 + (sx2 - sx1) * p)
        cy = int(sy1 + (sy2 - sy1) * p)
        c = self.col(color, alpha)
        self.draw.line([(sx1, sy1), (cx, cy)], fill=c, width=self.ss(width))
        if p > 0.2:
            ang = math.atan2(sy2 - sy1, sx2 - sx1)
            hlen = self.ss(width * 2.3)
            p1 = (int(cx - hlen * math.cos(ang - 0.55)), int(cy - hlen * math.sin(ang - 0.55)))
            p2 = (int(cx - hlen * math.cos(ang + 0.55)), int(cy - hlen * math.sin(ang + 0.55)))
            self.draw.polygon([(cx, cy), p1, p2], fill=c)

    def starburst(
        self,
        x: float,
        y: float,
        r_out: float = 110,
        r_in: float = 55,
        points: int = 10,
        color: str = "yellow_stage",
        speed: float = 0.6,
        delay: float = 0.0,
        alpha: int = 255,
        **kwargs
    ):
        p = self.elastic(delay, 0.5) if delay > 0 else 1.0
        if p <= 0.03:
            return
        self.shape_count += 1
        cx, cy = self.sx(x), self.sy(y)
        ro, ri = self.ss(r_out) * p, self.ss(r_in) * p
        pts = []
        step = math.pi / max(3, int(points))
        for i in range(int(points) * 2):
            r = ro if i % 2 == 0 else ri
            ang = i * step + self.lt * speed
            pts.append((int(cx + math.cos(ang) * r), int(cy + math.sin(ang) * r)))
        self.draw.polygon(pts, fill=self.col(color, alpha))

    def droplet(
        self,
        x: float,
        y: float,
        r: float = 32,
        color: str = "blue_tear",
        angle: float = 0.0,
        delay: float = 0.0,
        alpha: int = 255,
        **kwargs
    ):
        """Draws a teardrop / sweat drop / flame petal centered at `(x, y)`."""
        p = self.spring(delay, 0.42) if delay > 0 else 1.0
        if p <= 0.03:
            return
        self.shape_count += 1
        pts = [(-r, 0), (0, -r * 2.1), (r, 0)]
        self.circle(x, y, r * p, color=color, alpha=alpha)
        self.poly(pts, color=color, alpha=alpha, dx=x, dy=y, scale=p, angle=angle)

    def ring_gauge(
        self,
        x: float,
        y: float,
        r: float = 150,
        pct: float = 0.78,
        color: str = "red_hot",
        bg_color: str = "white",
        label: str = "78%",
        delay: float = 0.05,
        alpha: int = 255,
        **kwargs
    ):
        p = self.spring(delay, 0.5)
        if p <= 0.04:
            return
        self.shape_count += 2
        cx, cy = self.sx(x), self.sy(y)
        pr = int(self.ss(r) * p)
        bbox = [cx - pr, cy - pr, cx + pr, cy + pr]
        self.draw.ellipse(bbox, fill=self.col(bg_color, alpha))
        sweep = int(360.0 * clamp(pct, 0.0, 1.0) * self.ease(delay + 0.05, 0.65))
        self.draw.pieslice(bbox, start=-90, end=-90 + sweep, fill=self.col(color, alpha))
        ir = int(pr * 0.62)
        self.draw.ellipse([cx - ir, cy - ir, cx + ir, cy + ir], fill=self.col(self.beat.get("bg_color", "cream")))
        if label and p > 0.45:
            f = self._fit_font(str(label), int(r * 0.45), max_w_ratio=0.35)
            self.draw.text((cx, cy), str(label), font=f, fill=self.col("dark", alpha), anchor="mm")

    def label_pill(
        self,
        x: float,
        y: float,
        text: str = "LABEL",
        bg: str = "white",
        fg: str = "red_hot",
        size: int = 38,
        pointer: bool = False,
        delay: float = 0.15,
        alpha: int = 255,
        **kwargs
    ):
        """Draws a floating callout pill or speech bubble (`pointer=True`) at `(x, y)` relative to stage center."""
        p = self.spring(delay, 0.42, overshoot=1.7)
        if p <= 0.05 or not text:
            return
        self.shape_count += 1
        cx = self.sx(x)
        cy = self.sy(y) + int((1.0 - p) * 30 * self.S)
        txt = str(text).upper().strip()
        f = self._fit_font(txt, size, max_w_ratio=0.55)
        bb = self.draw.textbbox((0, 0), txt, font=f)
        bw = int(((bb[2] - bb[0]) + 52 * self.S) * min(1.05, p))
        bh = int(((bb[3] - bb[1]) + 32 * self.S) * min(1.05, p))

        x1, y1, x2, y2 = cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2
        self.draw.rounded_rectangle([x1, y1, x2, y2], radius=bh // 2 if not pointer else int(22 * self.S), fill=self.col(bg))
        if pointer:
            tw, th = self.ss(16), self.ss(24)
            self.draw.polygon([(cx - tw, y2 - 2), (cx + tw, y2 - 2), (cx, y2 + th)], fill=self.col(bg))
        if p > 0.4:
            self.draw.text((cx, cy), txt, font=f, fill=self.col(fg), anchor="mm")

    # ==========================================
    # 2. AUTOMATIC STORYBOARD LAYOUT & TYPOGRAPHY RENDERER
    # ==========================================
    def render_beat_typography(self):
        """Renders the beat's main poster typography & sequence indicators automatically from the Storyboard JSON."""
        layout = str(self.beat.get("layout", "top_text_bottom_art")).lower()
        dots_idx = int(self.beat.get("dot_index", 0) or 0)
        lines = self.beat.get("lines", [])

        if dots_idx in (1, 2, 3):
            cy = int(self.H * 0.075)
            cx = self.W // 2
            spacing = int(140 * self.S)
            for i in range(1, 4):
                dx = cx + (i - 2) * spacing
                is_act = (i == dots_idx)
                r = int((26 if is_act else 22) * self.S * (self.spring(0.0, 0.35) if is_act else 1.0))
                c = self.col("red_hot") if is_act else self.col("grey", 200)
                self.draw.ellipse([dx - r, cy - r, dx + r, cy + r], fill=c)

        if layout == "statement_pill" and len(lines) >= 3:
            w1 = str(lines[0].get("text", "")).upper()
            w2 = str(lines[1].get("text", "")).upper()
            w3 = str(lines[2].get("text", "")).upper()
            cx, cy = self.W // 2, int(self.H * 0.38)
            p1 = self.spring(0.0, 0.42)
            if p1 > 0.05:
                f1 = self._fit_font(w1, 175)
                self.draw.text((cx, cy - int(205 * self.S) + int((1.0 - p1) * 45 * self.S)), w1, font=f1, fill=self.col("cream"), anchor="mm")
            p2 = self.spring(0.16, 0.45)
            if p2 > 0.05:
                pw, ph = int(760 * self.S * p2), int(145 * self.S * p2)
                layer = Image.new("RGBA", (pw + 60, ph + 60), (0, 0, 0, 0))
                ld = ImageDraw.Draw(layer)
                ld.rounded_rectangle([30, 30, 30 + pw, 30 + ph], radius=int(24 * self.S), fill=self.col("dark"))
                f2 = self._fit_font(w2, 105, max_w_ratio=0.65)
                ld.text((30 + pw // 2, 30 + ph // 2), w2, font=f2, fill=self.col("cream"), anchor="mm")
                rot = layer.rotate(4.0, resample=Image.Resampling.BICUBIC, expand=True)
                self.img.paste(rot, (cx - rot.width // 2, cy - rot.height // 2), rot)
                self.draw = ImageDraw.Draw(self.img, "RGBA")
            p3 = self.spring(0.32, 0.45)
            if p3 > 0.05:
                f3 = self._fit_font(w3, 185)
                self.draw.text((cx, cy + int(215 * self.S) + int((1.0 - p3) * 50 * self.S)), w3, font=f3, fill=self.col("cream"), anchor="mm")
            return

        # Standard Top or Bottom Staggered Poster Lines
        y_start_frac = 0.73 if layout in ("triptych_dots", "top_art_bottom_text") else 0.14
        cur_y = int(self.H * y_start_frac)
        cx = self.W // 2

        for idx, item in enumerate(lines):
            txt = str(item.get("text", "")).upper().strip()
            if not txt:
                continue
            delay = float(item.get("delay", idx * 0.16))
            col_key = str(item.get("color", "dark"))
            base_sz = int(item.get("size", 108))

            p = self.spring(delay, 0.42, overshoot=1.55)
            if p <= 0.05:
                continue
            f = self._fit_font(txt, base_sz, max_w_ratio=0.88)
            y_off = int((1.0 - p) * 45 * self.S)
            f_px = int(getattr(f, "size", base_sz * self.S))   # bitmap fallback font has no .size
            ty = cur_y + int(f_px * 0.5) + y_off
            self.draw.text((cx, ty), txt, font=f, fill=self.col(col_key), anchor="mm")
            cur_y += int(f_px * 1.16)

