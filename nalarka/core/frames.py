"""Frame rendering, transitions, and beat preview contact sheets."""
import math
from typing import Dict, Any, List, Optional
from PIL import Image, ImageDraw

from .mathutil import ease_in_out_cubic
from .audit import render_single_beat_canvas

def composite_transition(
    img_prev: Image.Image,
    img_next: Image.Image,
    progress: float,
    trans_type: str
) -> Image.Image:
    if trans_type == "cut":
        return img_next
    p = ease_in_out_cubic(progress)
    w, h = img_next.size

    if trans_type == "directional_wipe":
        split_x = int(w * p)
        out = img_prev.copy()
        if split_x > 0:
            out.paste(img_next.crop((0, 0, split_x, h)), (0, 0))
        return out

    elif trans_type == "whip_pan":
        offset = int(w * p)
        out = Image.new("RGB", (w, h))
        out.paste(img_prev, (-offset, 0))
        out.paste(img_next, (w - offset, 0))
        return out

    elif trans_type == "circle_iris":
        mask = Image.new("L", (w, h), 0)
        mdraw = ImageDraw.Draw(mask)
        max_rad = int(math.hypot(w, h) * 0.65 * p)
        cx, cy = w // 2, h // 2
        mdraw.ellipse([cx - max_rad, cy - max_rad, cx + max_rad, cy + max_rad], fill=255)
        return Image.composite(img_next, img_prev, mask)

    return Image.blend(img_prev, img_next, p)


def render_frame(
    t: float,
    width: int,
    height: int,
    plan: dict,
    words: List[dict],
    namespace: Dict[str, Any]
) -> Image.Image:
    palette = plan.get("palette", {})
    scenes = plan["scenes"]
    active_idx = len(scenes) - 1
    for i, sc in enumerate(scenes):
        # half-open interval: a frame exactly on a boundary belongs to the NEW beat
        if float(sc["start"]) <= t < float(sc["end"]):
            active_idx = i
            break

    active_beat = scenes[active_idx]
    img, _, _ = render_single_beat_canvas(t, active_beat, active_idx, width, height, palette, words, namespace)

    trans_type = str(active_beat.get("transition", "cut")).lower()
    trans_dur = 0.22
    local_t = t - float(active_beat["start"])
    if active_idx > 0 and trans_type != "cut" and 0.0 <= local_t < trans_dur:
        prev_beat = scenes[active_idx - 1]
        img_prev, _, _ = render_single_beat_canvas(t, prev_beat, active_idx - 1, width, height, palette, words, namespace)
        img = composite_transition(img_prev, img, local_t / trans_dur, trans_type)

    return img


def render_beat_preview_sheet(
    plan: dict,
    namespace: Dict[str, Any],
    words: List[dict],
    beat_idx: int,
    width: int,
    height: int,
    out_path: str,
    frames: int = 6,
) -> str:
    """Contact sheet of ONE beat (no ffmpeg, no full render). On a 3-minute video, judging a single beat
    used to cost a full re-render; this takes a couple of seconds."""
    scenes = plan["scenes"]
    if not 0 <= beat_idx < len(scenes):
        raise IndexError(f"Beat index {beat_idx + 1} is out of range (1..{len(scenes)}).")
    beat = scenes[beat_idx]
    st, en = float(beat["start"]), float(beat["end"])
    frames = max(2, int(frames))
    tw = 360 if height >= width else 480
    thumbs: List[Image.Image] = []
    for k in range(frames):
        t = st + (en - st) * (k + 0.5) / frames
        img = render_frame(t, width, height, plan, words, namespace)
        thumbs.append(img.resize((tw, max(1, int(img.height * tw / img.width))), Image.Resampling.LANCZOS))
    cols = min(3, frames)
    rows = int(math.ceil(frames / cols))
    th = thumbs[0].height
    sheet = Image.new("RGB", (cols * tw, rows * th), (20, 20, 20))
    for i, im_t in enumerate(thumbs):
        r, c = divmod(i, cols)
        sheet.paste(im_t, (c * tw, r * th))
    sheet.save(out_path)
    return out_path


