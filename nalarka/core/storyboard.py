"""Storyboard planning (Pass 1) and batched vector code generation (Pass 2)."""
import math
import json
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Tuple, Any, Optional, Union

from .constants import (
    MIN_BEAT_SECONDS, PASS1_WINDOW_SECONDS, PASS2_BATCH_BEATS,
    PASS2_PARALLEL_CALLS, VALID_LAYOUTS, VALID_BG_COLORS, VALID_TRANSITIONS
)
from .prompts import PASS1_STORYBOARD_PROMPT, PASS2_PYTHON_VECTOR_ARTIST_PROMPT
from .llm import call_ai_json, call_ai_python_code
from .scene_code import (
    new_scene_sources, new_scene_namespace,
    add_generated_code, write_scene_module_file
)

def sanitize_scenes(scenes: List[Any], total_dur: float, log_fn) -> List[dict]:
    """
    Repairs the model's storyboard so rendering can trust it: sorted, strictly increasing starts,
    >= MIN_BEAT_SECONDS per beat, valid layout / colour / transition, beat 1 at 0.0 and last beat to total_dur.
    (The old code trusted the raw model output; unsorted or duplicate starts gave zero/negative-length beats.)
    """
    items: List[dict] = []
    for s in scenes or []:
        if not isinstance(s, dict):
            continue
        try:
            start = float(s.get("start", 0.0))
        except (TypeError, ValueError):
            continue
        d = dict(s)
        d["start"] = start
        items.append(d)
    if not items:
        raise ValueError("Storyboard contains no usable scenes.")

    items.sort(key=lambda s: s["start"])
    kept: List[dict] = []
    for s in items:
        st = max(0.0, min(s["start"], float(total_dur)))
        if not kept:
            st = 0.0
        elif st < kept[-1]["start"] + MIN_BEAT_SECONDS:
            st = kept[-1]["start"] + MIN_BEAT_SECONDS
        if kept and st > total_dur - MIN_BEAT_SECONDS:
            log_fn(f"[STORYBOARD] Dropped a beat that no longer fits before {total_dur}s.")
            continue
        s["start"] = round(st, 3)
        kept.append(s)

    bg_cycle = ["cream", "orange_bg", "green_bg"]
    for i, s in enumerate(kept):
        s["beat_id"] = i + 1
        s["end"] = round(kept[i + 1]["start"], 3) if i + 1 < len(kept) else float(total_dur)

        layout = str(s.get("layout", "")).lower().strip()
        s["layout"] = layout if layout in VALID_LAYOUTS else "top_text_bottom_art"

        bg = str(s.get("bg_color", "")).lower().strip()
        s["bg_color"] = bg if bg in VALID_BG_COLORS else bg_cycle[i % len(bg_cycle)]

        tr = str(s.get("transition", "cut")).lower().strip()
        s["transition"] = tr if tr in VALID_TRANSITIONS and i > 0 else "cut"

        try:
            dot = int(s.get("dot_index", 0) or 0)
        except (TypeError, ValueError):
            dot = 0
        s["dot_index"] = dot if dot in (0, 1, 2, 3) else 0

        lines: List[dict] = []
        raw_lines = s.get("lines")
        for ln in raw_lines if isinstance(raw_lines, list) else []:
            if isinstance(ln, str):
                ln = {"text": ln}
            if isinstance(ln, dict) and str(ln.get("text", "")).strip():
                lines.append(ln)
        s["lines"] = lines
        if s["layout"] == "statement_pill" and len(lines) < 3:
            s["layout"] = "top_text_bottom_art"

        s["summary"] = str(s.get("summary", "") or f"Beat {i + 1}")
        s["visual_blueprint"] = str(s.get("visual_blueprint", "") or s["summary"])
    return kept


def compress_words(words: List[dict], group: int = 4) -> List[dict]:
    """Phrase-level timestamps (keeps long-video prompts small)."""
    out = []
    for i in range(0, len(words), group):
        chunk = words[i:i + group]
        out.append({
            "start": chunk[0]["start"],
            "end": chunk[-1]["end"],
            "text": " ".join(w["word"] for w in chunk),
        })
    return out


def storyboard_windows(total_dur: int) -> List[Tuple[float, float]]:
    if total_dur <= 120:
        return [(0.0, float(total_dur))]
    n = int(math.ceil(total_dur / PASS1_WINDOW_SECONDS))
    edges = [round(total_dur * i / n, 1) for i in range(n + 1)]
    edges[-1] = float(total_dur)
    return [(edges[i], edges[i + 1]) for i in range(n)]


def plan_storyboard(
    base_url: str,
    api_key: str,
    model: str,
    topic: str,
    sections: List[Dict[str, str]],
    words: List[dict],
    total_dur: int,
    log_fn,
) -> List[dict]:
    """PASS 1. Short videos use one call; long ones are planned window by window so the model
    never has to emit (or keep coherent) a 60-beat JSON document in one go."""
    windows = storyboard_windows(total_dur)
    scenes: List[dict] = []
    for wi, (w_start, w_end) in enumerate(windows):
        span = w_end - w_start
        beats_lo = max(1, int(math.ceil(span / 7.0)))
        beats_hi = max(beats_lo, int(math.ceil(span / 4.5)))
        note = ""
        if len(windows) > 1:
            note = f"This is part {wi + 1} of {len(windows)} of the video."
            if wi > 0:
                note += " Continue the story after `previous_beats` and do NOT repeat their objects."
            if wi == len(windows) - 1:
                note += " The last beat must hold until the end with a memorable closing visual."

        in_win = [w for w in words if w_start - 0.5 <= w["start"] < w_end]
        stamps = compress_words(in_win) if len(in_win) > 160 else in_win
        payload: Dict[str, Any] = {
            "topic": topic,
            "structured_sections": sections,
            "word_timestamps": stamps,
            "total_duration": total_dur,
            "window": [w_start, w_end],
        }
        if scenes:
            payload["previous_beats"] = [
                {"beat_id": s.get("beat_id", i + 1), "summary": s.get("summary", ""), "visual": str(s.get("visual_blueprint", ""))[:160],
                 "bg_color": s.get("bg_color"), "layout": s.get("layout")}
                for i, s in enumerate(scenes[-8:])
            ]

        p1_sys = PASS1_STORYBOARD_PROMPT.format(
            total_dur=total_dur, win_start=w_start, win_end=w_end,
            beats_lo=beats_lo, beats_hi=beats_hi, window_note=note,
        )
        log_fn(f"[PASS 1/2: CREATIVE DIRECTOR] Window {wi + 1}/{len(windows)} ({w_start:.0f}s-{w_end:.0f}s): planning {beats_lo}-{beats_hi} beats...")
        data = call_ai_json(
            base_url, api_key, model, p1_sys, json.dumps(payload, ensure_ascii=False), log_fn,
            temperature=0.40, max_tokens=16000,
        )
        win_scenes = data.get("scenes", []) if isinstance(data, dict) else []
        if not win_scenes:
            raise ValueError(f"Pass 1 returned an empty 'scenes' list for window {wi + 1}.")
        for s in win_scenes:
            if isinstance(s, dict):
                s.setdefault("start", w_start)
        scenes.extend(win_scenes)
    return scenes


def generate_scene_code_batches(
    base_url: str,
    api_key: str,
    model: str,
    topic: str,
    scenes: List[dict],
    plan: dict,
    namespace: Dict[str, Any],
    log_fn,
    cancel_event: Optional[threading.Event] = None,
    batch_beats: Optional[int] = None,
    parallel_calls: Optional[int] = None,
):
    """PASS 2. Beats are coded in small batches (several calls in parallel) so output limits are never hit.

    ``batch_beats`` / ``parallel_calls`` override the defaults. Smaller batches mean shorter answers,
    which helps providers or proxies that cut long responses (e.g. a 120 s gateway timeout)."""
    size = max(1, int(batch_beats or PASS2_BATCH_BEATS))
    workers = max(1, int(parallel_calls or PASS2_PARALLEL_CALLS))
    batches = [(i, min(i + size, len(scenes))) for i in range(0, len(scenes), size)]
    log_fn(f"[PASS 2/2: VECTOR ARTIST] Coding {len(scenes)} beats in {len(batches)} batch(es) of up to {size} beat(s), {workers} call(s) in parallel...")

    def _run(span: Tuple[int, int]) -> Tuple[Tuple[int, int], Optional[str]]:
        if cancel_event is not None and cancel_event.is_set():
            return span, None
        lo, hi = span
        first_fn, last_fn = f"beat_{lo + 1:02d}", f"beat_{hi:02d}"
        p2_sys = PASS2_PYTHON_VECTOR_ARTIST_PROMPT.format(first_fn=first_fn, last_fn=last_fn)
        blueprint_list = []
        for s in scenes[lo:hi]:
            blueprint_list.append({
                "function_name": f"beat_{s['beat_id']:02d}",
                "duration": round(float(s["end"]) - float(s["start"]), 2),
                "bg_color": s.get("bg_color", "cream"),
                "layout": s.get("layout", "top_text_bottom_art"),
                "summary": s.get("summary", ""),
                "visual_blueprint": s.get("visual_blueprint", ""),
            })
        prev = ""
        if lo > 0:
            prev = f"For variety, the beat just before this batch showed: {scenes[lo - 1].get('visual_blueprint', '')[:200]}\n"
        p2_user = (
            f"Topic: {topic}\n{prev}"
            f"Write Python functions `{first_fn}(v)` through `{last_fn}(v)` implementing each `visual_blueprint` below:\n"
            + json.dumps(blueprint_list, indent=2, ensure_ascii=False)
        )
        try:
            return span, call_ai_python_code(base_url, api_key, model, p2_sys, p2_user, log_fn, temperature=0.35)
        except Exception as err:
            log_fn(f"[PASS 2 WARN] Batch {first_fn}-{last_fn} failed ({err}). The audit loop will rewrite these beats one by one.")
            return span, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_run, batches))

    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Batch code generation cancelled.")

    for (lo, hi), code in results:   # registered in order, on this thread (no races on `plan`)
        if code:
            loaded = add_generated_code(plan, namespace, code, log_fn)
            missing = [f"beat_{i + 1:02d}" for i in range(lo, hi) if f"beat_{i + 1:02d}" not in loaded]
            if missing:
                log_fn(f"[PASS 2 WARN] Missing/invalid after batch: {', '.join(missing)} (will be rewritten by the audit).")


def generate_two_pass_storyboard_and_code(
    base_url: str,
    api_key: str,
    model: str,
    topic: str,
    sections: List[Dict[str, str]],
    words: List[dict],
    total_dur: int,
    ref_profile: dict,
    log_fn,
    cancel_event: Optional[threading.Event] = None,
    scene_module_path: Optional[Union[str, Path]] = None,
    batch_beats: Optional[int] = None,
    parallel_calls: Optional[int] = None,
) -> Tuple[dict, Dict[str, Any]]:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Operation was cancelled by user.")
    log_fn(f"[PASS 1/2: CREATIVE DIRECTOR] Planning topic-specific visual blueprints for '{topic}' ({total_dur}s)...")
    raw_scenes = plan_storyboard(base_url, api_key, model, topic, sections, words, total_dur, log_fn)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Operation was cancelled by user.")
    scenes = sanitize_scenes(raw_scenes, total_dur, log_fn)

    plan: Dict[str, Any] = {"scenes": scenes, "palette": ref_profile["palette"], "total_duration": total_dur}
    plan["scene_sources"] = new_scene_sources()
    for s in scenes:
        log_fn(f"  Beat #{s['beat_id']:02d} [{s['start']:05.1f}s-{s['end']:05.1f}s] ({s.get('layout')}, {s.get('bg_color')}, {s.get('transition')}): {s.get('visual_blueprint')}")

    namespace = new_scene_namespace()
    generate_scene_code_batches(
        base_url, api_key, model, topic, scenes, plan, namespace, log_fn,
        cancel_event=cancel_event, batch_beats=batch_beats, parallel_calls=parallel_calls,
    )
    target_path = scene_module_path if scene_module_path else "generated_scenes.py"
    write_scene_module_file(plan, path=target_path)
    return plan, namespace



