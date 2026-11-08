"""Colour & easing mathematics for Nalarka Studio."""
import math
from typing import Tuple, List


def hex_to_rgb(hex_str: str, default: Tuple[int, int, int] = (242, 234, 218)) -> Tuple[int, int, int]:
    if not isinstance(hex_str, str):
        return default
    h = hex_str.strip().lstrip("#")
    if len(h) == 3:
        h = "".join([c * 2 for c in h])
    if len(h) != 6:
        return default
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return default


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, int(rgb[0]))),
        max(0, min(255, int(rgb[1]))),
        max(0, min(255, int(rgb[2])))
    )


def clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(val)))


def ease_out_cubic(x: float) -> float:
    x = clamp(x)
    return 1.0 - pow(1.0 - x, 3)


def ease_in_out_cubic(x: float) -> float:
    x = clamp(x)
    return 4.0 * x * x * x if x < 0.5 else 1.0 - pow(-2.0 * x + 2.0, 3) / 2.0


def ease_out_back(x: float, overshoot: float = 1.65) -> float:
    x = clamp(x)
    c3 = overshoot + 1.0
    return 1.0 + c3 * pow(x - 1.0, 3) + overshoot * pow(x - 1.0, 2)


def ease_out_elastic(x: float) -> float:
    x = clamp(x)
    if x == 0.0 or x == 1.0:
        return x
    c4 = (2.0 * math.pi) / 3.0
    return pow(2.0, -10.0 * x) * math.sin((x * 10.0 - 0.75) * c4) + 1.0


def rotate_point(px: float, py: float, cx: float, cy: float, angle_deg: float) -> Tuple[int, int]:
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    nx = cos_a * (px - cx) - sin_a * (py - cy) + cx
    ny = sin_a * (px - cx) + cos_a * (py - cy) + cy
    return int(nx), int(ny)


def sample_bezier_curve(control_pts: List[Tuple[float, float]], steps: int = 28) -> List[Tuple[int, int]]:
    """Evaluates a De Casteljau Bezier curve of any degree (quadratic, cubic, or higher)."""
    if len(control_pts) < 2:
        return [(int(p[0]), int(p[1])) for p in control_pts]
    out = []
    n = len(control_pts) - 1
    for i in range(steps + 1):
        u = i / float(steps)
        pts = [(float(p[0]), float(p[1])) for p in control_pts]
        for r in range(1, n + 1):
            pts = [
                ((1.0 - u) * pts[k][0] + u * pts[k + 1][0], (1.0 - u) * pts[k][1] + u * pts[k + 1][1])
                for k in range(n - r + 1)
            ]
        out.append((int(pts[0][0]), int(pts[0][1])))
    return out
