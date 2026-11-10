"""Self-correction loop (complexity, motion, and anti-repetition audit)."""
import re
import json
import threading
from pathlib import Path
from typing import Callable, Tuple, Optional, List, Dict, Any, Union
import numpy as np
from PIL import Image

from .vectorkit import VectorKit
from .prompts import PASS2_PYTHON_VECTOR_ARTIST_PROMPT
from .llm import call_ai_python_code
from .scene_code import split_scene_code, register_beat_source, write_scene_module_file

def render_single_beat_canvas(
    t: float,
    beat: dict,
    beat_idx: int,
    width: int,
    height: int,
    palette: dict,
    words: List[dict],
    namespace: Dict[str, Any]
) -> Tuple[Image.Image, VectorKit, Optional[str]]:
    v = VectorKit(width, height, t, beat, words, palette)
    v.render_beat_typography()

    fn_name = f"beat_{beat_idx + 1:02d}"
    fn = namespace.get(fn_name)
    err = None
    if callable(fn):
        try:
            fn(v)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    else:
        err = f"Function '{fn_name}(v)' is missing or was rejected by the code validator"

    # Procedural geometric fallback if AI function crashed after all retries (NO yawning face!)
    if err is not None or v.shape_count == 0:
        v.backdrop_circle(r=310, color="yellow_stage" if v.beat.get("bg_color") != "orange_bg" else "cream", delay=0.0)
        v.concentric_waves(0, 0, min_r=60, max_r=260, count=3, color="red_hot", width=10)
        v.waveform_bars(0, 0, w=340, h=120, count=11, color="dark", bg_color="white")

    return v.img, v, err


def rewrite_single_beat_function_with_ai(
    base_url: str,
    api_key: str,
    model: str,
    topic: str,
    beat_idx: int,
    beat: dict,
    reason: str,
    log_fn
) -> Optional[str]:
    """Returns validated-ready SOURCE for `beat_XX` (or None). Register it with register_beat_source()."""
    fn_name = f"beat_{beat_idx + 1:02d}"
    sys_prompt = (
        PASS2_PYTHON_VECTOR_ARTIST_PROMPT.format(first_fn=fn_name, last_fn=fn_name)
        + f"\nWrite ONLY the single Python function `def {fn_name}(v):` to fix the audit issue below.\n"
        "Use at least 8 distinct `v.*` vector shape calls to draw the exact object in `visual_blueprint`!"
    )
    user_prompt = json.dumps({
        "topic": topic,
        "function_name": fn_name,
        "bg_color": beat.get("bg_color", "cream"),
        "layout": beat.get("layout", "top_text_bottom_art"),
        "summary": beat.get("summary", ""),
        "visual_blueprint": beat.get("visual_blueprint", ""),
        "audit_issue_to_fix": reason
    }, ensure_ascii=False)

    code_str = call_ai_python_code(base_url, api_key, model, sys_prompt, user_prompt, log_fn, temperature=0.45)
    helpers, beats = split_scene_code(code_str, log_fn)
    src = beats.get(fn_name)
    if src is None and len(beats) == 1:   # model numbered it differently; rename to the expected name
        only_name, only_src = next(iter(beats.items()))
        src = re.sub(rf"^def\s+{re.escape(only_name)}\s*\(", f"def {fn_name}(", only_src, count=1)
    if src is None:
        return None
    if helpers:   # keep any helper next to the beat so it stays self-contained
        src = "\n\n".join(helpers.values()) + "\n\n" + src
    return src


def audit_and_fix_beats(
    plan: dict,
    namespace: Dict[str, Any],
    words: List[dict],
    width: int,
    height: int,
    base_url: str,
    api_key: str,
    model: str,
    topic: str,
    log_fn,
    max_retries: int = 2,
    progress_fn=None,
    cancel_event: Optional[threading.Event] = None,
    scene_module_path: Optional[Union[str, Path]] = None,
    on_code_update: Optional[Callable[[dict, str], None]] = None,
):
    scenes = plan.get("scenes", [])
    log_fn(f"[SELF-CORRECTION LOOP v8.1] Auditing {len(scenes)} beats for complexity, motion & anti-repetition...")
    palette = plan.get("palette", {})
    prev_stage_crop: Optional[np.ndarray] = None

    for idx, beat in enumerate(scenes):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Audit loop cancelled.")
        if progress_fn:
            progress_fn(62 + int(8 * idx / max(1, len(scenes))), f"Auditing beat {idx + 1}/{len(scenes)}...")
        st, en = float(beat["start"]), float(beat["end"])
        dur = max(0.3, en - st)
        t_early = st + min(0.12, dur * 0.15)
        t_mid = st + dur * 0.55
        t_late = st + dur * 0.88

        for attempt in range(max_retries + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Audit loop cancelled.")
            img1, v1, err1 = render_single_beat_canvas(t_early, beat, idx, width, height, palette, words, namespace)
            img2, v2, err2 = render_single_beat_canvas(t_mid, beat, idx, width, height, palette, words, namespace)
            img3, v3, err3 = render_single_beat_canvas(t_late, beat, idx, width, height, palette, words, namespace)

            runtime_err = err1 or err2 or err3
            arr1 = np.asarray(img1, dtype=np.float32)
            arr2 = np.asarray(img2, dtype=np.float32)
            arr3 = np.asarray(img3, dtype=np.float32)
            motion_score = float(np.mean(np.abs(arr2 - arr1)) + np.mean(np.abs(arr3 - arr2)))

            # Crop illustration stage region to check visual uniqueness against previous beat
            cy = v2.stage_cy
            r_crop = int(min(width, height) * 0.28)
            y_lo, y_hi = max(0, cy - r_crop), min(height, cy + r_crop)
            x_lo, x_hi = max(0, width // 2 - r_crop), min(width, width // 2 + r_crop)
            cur_crop = np.asarray(img2.crop((x_lo, y_lo, x_hi, y_hi)).resize((64, 64)), dtype=np.float32)

            diff_from_prev = 99.0
            if prev_stage_crop is not None and prev_stage_crop.shape == cur_crop.shape:
                diff_from_prev = float(np.mean(np.abs(cur_crop - prev_stage_crop)))

            issues = []
            if runtime_err:
                issues.append(f"Runtime Error: {runtime_err}")
            if v2.shape_count < 5:
                issues.append(f"Illustration is too simple (only {v2.shape_count} shapes). Build a detailed multi-part object with >= 7 shapes.")
            if motion_score < 0.22:
                issues.append(f"Motion score too low ({motion_score:.2f}). Animate parts using `v.spring`, `v.wave`, `v.wave_line`, or `v.pulse`.")
            if diff_from_prev < 6.5:
                issues.append(f"Illustration looks too similar to Beat #{idx:02d} (diff={diff_from_prev:.1f}). Draw a completely different object matching `{beat.get('visual_blueprint')}`!")

            if not issues:
                prev_stage_crop = cur_crop
                log_fn(
                    f"  [BEAT #{idx+1:02d} PASS] shapes={v2.shape_count} | motion={motion_score:.2f} | "
                    f"unique_diff={diff_from_prev:.1f} | '{beat.get('summary')}'"
                )
                break

            reason_str = " | ".join(issues)
            if attempt < max_retries:
                log_fn(f"  [BEAT #{idx+1:02d} AUTO-FIX {attempt+1}/{max_retries}] {reason_str}")
                try:
                    new_src = rewrite_single_beat_function_with_ai(base_url, api_key, model, topic, idx, beat, reason_str, log_fn)
                except Exception as err:
                    log_fn(f"  [BEAT #{idx+1:02d} AUTO-FIX ERROR] {err}")
                    new_src = None
                if new_src and register_beat_source(plan, namespace, f"beat_{idx+1:02d}", new_src, log_fn) and on_code_update:
                    try:
                        on_code_update(plan, "audit")   # a paid-for fix is saved right away
                    except Exception as err:
                        log_fn(f"  [DB WARN] Could not save beat #{idx+1:02d}: {err}")
            else:
                prev_stage_crop = cur_crop
                log_fn(f"  [BEAT #{idx+1:02d} PROCEED] Max retries reached ({reason_str}).")

    target_path = scene_module_path if scene_module_path else "generated_scenes.py"
    write_scene_module_file(plan, path=target_path)   # generated_scenes.py now reflects every rewrite



