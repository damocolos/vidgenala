"""Scene 3D generation: Pass 1 (shots) and Pass 2 (one JS function per beat, checked and repaired)."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple

from ..constants import PASS2_BATCH_BEATS, PASS2_PARALLEL_CALLS
from ..llm import _chat_text, accept_last_call, call_ai_json
from ..storyboard import beat_spans, compress_words, sanitize_scenes, storyboard_windows
from .extract import extract_beat_functions
from .prompts import PASS1_SCENE3D_PROMPT, pass2_prompt, repair_prompt

import math

SHOT_MIN_S, SHOT_MAX_S = 3.5, 6.5          # shots are long enough for the animation to tell the story


def snap_to_words(scenes: List[dict], words: List[dict], tol: float = 0.6, min_len: float = 2.5) -> List[dict]:
    """Moves each cut to the nearest start of a spoken word (within tol seconds) so the picture changes with the speech."""
    starts = sorted(float(w["start"]) for w in words if "start" in w)
    if not starts:
        return scenes
    for i in range(1, len(scenes)):
        cur = float(scenes[i]["start"])
        best = min(starts, key=lambda x: abs(x - cur))
        lo, hi = float(scenes[i - 1]["start"]) + min_len, float(scenes[i]["end"]) - min_len
        if abs(best - cur) <= tol and lo <= best <= hi:
            scenes[i]["start"] = round(best, 3)
            scenes[i - 1]["end"] = round(best, 3)
    return scenes


def attach_narration(scenes: List[dict], words: List[dict], world: str = "") -> List[dict]:
    """Stores what is SAID during each shot (and the shared world) on the shot, so the code writer sees the real context."""
    for sc in scenes:
        a, b = float(sc["start"]) - 0.05, float(sc["end"]) - 0.05
        sc["narration"] = " ".join(str(w.get("word", "")).strip() for w in words if a <= float(w.get("start", -1)) < b).strip()
        if world:
            sc["world"] = world
    return scenes


Validator = Callable[[str, str, float], dict]      # (name, code, dur) -> {ok, errors, warnings, stats}


def call_ai_js_code(base_url, api_key, model, system_prompt, user_prompt, log_fn, temperature=0.35, max_tokens=16000) -> Dict[str, str]:
    """One model call; returns {function_name: source} for the complete beat functions in the answer."""
    raw, finish = _chat_text(base_url, api_key, model, system_prompt, user_prompt, log_fn, "AI SCENE3D",
                             temperature=temperature, timeout=420.0, max_tokens=max_tokens, min_chars=50)
    if finish == "length":
        log_fn(f"[AI SCENE3D WARN] '{model}' hit its output limit. Complete functions are kept; the rest is regenerated later.")
    fns = extract_beat_functions(raw)
    accept_last_call()
    return fns


def plan_storyboard_3d(base_url, api_key, model, topic, sections, words, total_dur, log_fn) -> List[dict]:
    """PASS 1 for Scene 3D: same windows as the flat type, shots of about 2 s."""
    windows = storyboard_windows(total_dur)
    scenes: List[dict] = []
    world = ""
    for wi, (w0, w1) in enumerate(windows):
        span = w1 - w0
        lo, hi = max(1, int(math.ceil(span / SHOT_MAX_S))), max(1, int(math.floor(span / SHOT_MIN_S)))
        hi = max(lo, hi)
        note = ""
        if len(windows) > 1:
            note = f"This is part {wi + 1} of {len(windows)}."
            if wi > 0:
                note += " Continue after `previous_beats`; do not repeat their scenes."
            if wi == len(windows) - 1:
                note += " The last beat holds until the end with a memorable closing image."
        in_win = [w for w in words if w0 - 0.5 <= w["start"] < w1]
        payload = {"topic": topic, "structured_sections": sections,
                   "word_timestamps": compress_words(in_win) if len(in_win) > 160 else in_win,
                   "total_duration": total_dur, "window": [w0, w1]}
        if scenes:
            payload["previous_beats"] = [{"summary": s.get("summary", ""), "visual": str(s.get("visual_blueprint", ""))[:160]} for s in scenes[-8:]]
        world_note = f"Keep this world: {world}" if world else "Invent it in this answer."
        system = PASS1_SCENE3D_PROMPT.format(total_dur=total_dur, win_start=w0, win_end=w1, beats_lo=lo, beats_hi=hi,
                                             window_note=note, world_note=world_note)
        log_fn(f"[PASS 1/2: 3D DIRECTOR] Window {wi + 1}/{len(windows)} ({w0:.0f}s-{w1:.0f}s): planning {lo}-{hi} shots...")
        data = call_ai_json(base_url, api_key, model, system, json.dumps(payload, ensure_ascii=False), log_fn,
                            temperature=0.40, max_tokens=16000)
        got = data.get("scenes", []) if isinstance(data, dict) else []
        if isinstance(data, dict) and not world and isinstance(data.get("world"), str):
            world = data["world"].strip()[:800]
        if not got:
            raise ValueError(f"Pass 1 returned an empty 'scenes' list for window {wi + 1}.")
        for s in got:
            if isinstance(s, dict):
                s.setdefault("start", w0)
        scenes.extend(got)
    out = sanitize_scenes(scenes, total_dur, log_fn)
    return attach_narration(snap_to_words(out, words), words, world)


def _fmt_errors(res: dict) -> str:
    return "\n".join(f"- {e}" for e in (res.get("errors") or ["unknown problem"])[:8])


def check_and_repair(name: str, code: str, dur: float, validator: Validator, repair: Callable[[str, str, str], Optional[str]],
                     log_fn, max_repairs: int = 2) -> Optional[str]:
    """Returns validated code, or None when it could not be fixed. Repairs go back to the model with the exact errors."""
    for attempt in range(max_repairs + 1):
        res = validator(name, code, dur)
        if res.get("ok"):
            for w in res.get("warnings") or []:
                log_fn(f"[3D WARN] {name}: {w}")
            return code
        log_fn(f"[3D CHECK] {name} rejected (attempt {attempt + 1}): {(res.get('errors') or ['?'])[0]}")
        if attempt >= max_repairs:
            return None
        fixed = repair(name, code, _fmt_errors(res))
        if not fixed:
            return None
        code = fixed
    return None


def generate_scene3d_batches(base_url, api_key, model, topic, scenes: List[dict], plan: dict, validator: Validator, log_fn,
                             cancel_event: Optional[threading.Event] = None, batch_beats: Optional[int] = None,
                             parallel_calls: Optional[int] = None, only_indices: Optional[List[int]] = None,
                             on_code_update: Optional[Callable[[dict, str], None]] = None, max_repairs: int = 2) -> None:
    """PASS 2 for Scene 3D. Model calls run in parallel; checking and repairing run one at a time on this thread
    (the browser used for checking is not thread-safe). Only beats that pass the check are stored."""
    size = max(1, int(batch_beats or PASS2_BATCH_BEATS))
    workers = max(1, int(parallel_calls or PASS2_PARALLEL_CALLS))
    batches = beat_spans(len(scenes) if only_indices is None else only_indices, size)
    todo = len(scenes) if only_indices is None else len(only_indices)
    log_fn(f"[PASS 2/2: 3D PROGRAMMER] Coding {todo} beat(s) in {len(batches)} batch(es) of up to {size}, {workers} call(s) in parallel...")
    store = plan.setdefault("scene_sources", {"helpers": {}, "beats": {}}).setdefault("beats", {})

    def _blueprints(lo: int, hi: int) -> List[dict]:
        return [{"function_name": f"beat_{s['beat_id']:02d}", "duration": round(float(s["end"]) - float(s["start"]), 2),
                 "narration_in_this_beat": s.get("narration", ""), "summary": s.get("summary", ""),
                 "visual_blueprint": s.get("visual_blueprint", "")} for s in scenes[lo:hi]]

    def _run(span: Tuple[int, int]):
        if cancel_event is not None and cancel_event.is_set():
            return span, {}
        lo, hi = span
        first, last = f"beat_{lo + 1:02d}", f"beat_{hi:02d}"
        system = pass2_prompt(first, last)
        prev = f"For variety, the shot just before this batch was: {scenes[lo - 1].get('visual_blueprint', '')[:200]}\n" if lo > 0 else ""
        world = next((sc.get("world") for sc in scenes if sc.get("world")), "")
        world_txt = f"The video's world (every beat happens in it, keep the same look): {world}\n" if world else ""
        user = (f"Topic: {topic}\n{world_txt}{prev}Write `{first}(v)` through `{last}(v)` for these shots:\n"
                + json.dumps(_blueprints(lo, hi), indent=2, ensure_ascii=False))
        try:
            return span, call_ai_js_code(base_url, api_key, model, system, user, log_fn)
        except Exception as err:
            log_fn(f"[PASS 2 WARN] Batch {first}-{last} failed ({err}). Those beats stay missing and can be retried.")
            return span, {}

    def _repair(name: str, code: str, errors: str) -> Optional[str]:
        idx = int(name.split("_")[1]) - 1
        bp = scenes[idx] if 0 <= idx < len(scenes) else {}
        user = (f"Shot: {bp.get('visual_blueprint', '')}\n\nFunction `{name}`:\n{code}\n\nProblems found:\n{errors}")
        try:
            return call_ai_js_code(base_url, api_key, model, repair_prompt(name), user, log_fn, temperature=0.2, max_tokens=8000).get(name)
        except Exception as err:
            log_fn(f"[3D REPAIR WARN] {name}: {err}")
            return None

    def _register(span: Tuple[int, int], fns: Dict[str, str]) -> None:
        lo, hi = span
        ok_names = []
        for i in range(lo, hi):
            name = f"beat_{i + 1:02d}"
            if name not in fns:
                continue
            dur = float(scenes[i]["end"]) - float(scenes[i]["start"])
            good = check_and_repair(name, fns[name], dur, validator, _repair, log_fn, max_repairs)
            if good:
                store[name] = good
                ok_names.append(name)
        missing = [f"beat_{i + 1:02d}" for i in range(lo, hi) if f"beat_{i + 1:02d}" not in ok_names]
        if missing:
            log_fn(f"[PASS 2 WARN] Missing/invalid after batch: {', '.join(missing)} (kept as 'missing'; the audit or the next run fills them).")
        if ok_names and on_code_update is not None:
            try:
                on_code_update(plan, "generated")
            except Exception as err:
                log_fn(f"[DB WARN] Could not save batch beat_{lo + 1:02d}-beat_{hi:02d}: {err}")

    stop: Optional[BaseException] = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run, b) for b in batches]
        for fut in as_completed(futures):
            if fut.cancelled():
                continue
            try:
                span, fns = fut.result()
            except BaseException as exc:
                if stop is None:
                    stop = exc
                    for f in futures:
                        f.cancel()
                continue
            _register(span, fns)
    if stop is not None:
        raise stop
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("3D code generation cancelled.")
