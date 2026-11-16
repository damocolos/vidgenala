"""Scene 3D branch of the full render: AI-written JavaScript beats, checked in a sandbox, drawn with three.js.

Called from execute_full_render once the voiceover, script and word timings are ready. The flat video type never
reaches this module."""
import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .pipeline import resolve_bgm
from ..core.constants import FPS
from ..core.scene3d.generate import generate_scene3d_batches, plan_storyboard_3d
from ..core.scene3d.render import Scene3DRenderer, encode_with_audio, ensure_three
from ..core.scene3d.runtime import Scene3DRuntime
from ..core.verify import run_verification_suite
from ..datadir import data_root
from .context import RunContext
from .plan_assembler import missing_beat_indices_3d


def three_cache_dir() -> Path:
    return data_root() / "cache" / "three"


def _beat_name(sc: dict, i: int) -> str:
    return f"beat_{int(sc.get('beat_id', i + 1)):02d}"


def _caption_frames(frames, cues: List[dict], fps: int):
    """Draws the phrase captions on JPEG frames (same look as the flat type)."""
    import io
    from PIL import Image
    from ..core.captions import apply_caption
    for i, jpg in enumerate(frames):
        t = i / float(fps)
        img = Image.open(io.BytesIO(jpg)).convert("RGB")
        out = apply_caption(img, t, cues)
        if out is img:
            yield jpg
            continue
        buf = io.BytesIO()
        out.convert("RGB").save(buf, "JPEG", quality=92)
        yield buf.getvalue()


def run_scene3d(
    ctx: RunContext, *, base_url: str, api_key: str, coder_model: str, storyboard_model: Optional[str], topic: str,
    sections: List[dict], words: List[dict], total_dur: int, width: int, height: int, vo: Path,
    bgm_path: Optional[str], synth_bgm: bool, ref_profile: dict, existing_plan: Optional[dict], max_duration: int,
    output_mp4: Path, script_raw_text: str, captions: bool, batch_beats: Optional[int], parallel_calls: Optional[int],
    on_plan_ready: Optional[Callable[[dict, Dict[str, Any], List[dict]], None]],
    on_code_update: Optional[Callable[[dict, str], None]],
) -> dict:
    # --- everything this type needs from the computer is checked BEFORE any tokens are spent ---
    ctx.set_progress(40, "Checking Scene 3D requirements (Chromium, three.js, WebGL)...")
    three = ensure_three(three_cache_dir(), log=ctx.log)
    with Scene3DRenderer(three, size=(min(width, 320), min(height, 568))):
        pass
    ctx.log("[3D] Chromium and WebGL are working.")

    ctx.check_cancelled()
    if existing_plan:
        plan = existing_plan
        ctx.log(f"[STORYBOARD] Reusing the saved 3D storyboard with {len(plan.get('scenes', []))} shots (no tokens needed for planning).")
    else:
        ctx.set_progress(45, f"Planning the 3D shots via '{storyboard_model or coder_model}'...")
        scenes = plan_storyboard_3d(base_url, api_key, storyboard_model or coder_model, topic, sections, words, total_dur, ctx.log)
        plan = {"total_dur": total_dur, "total_duration": total_dur, "palette": {}, "scenes": scenes,
                "scene_sources": {"helpers": {}, "beats": {}}, "video_type": "3d"}
        if on_plan_ready:
            try:
                on_plan_ready(plan, {}, words)
            except Exception as err:
                ctx.log(f"[WARN] on_plan_ready callback error: {err}")

    scenes_all = plan["scenes"]
    missing = missing_beat_indices_3d(plan)
    if missing:
        ctx.log(f"[BEATS] Reusing {len(scenes_all) - len(missing)}/{len(scenes_all)} saved shots; generating {len(missing)}: "
                + ", ".join(_beat_name(scenes_all[i], i) for i in missing))
        ctx.set_progress(50, f"Writing {len(missing)} 3D scene(s) via '{coder_model}'...")
        with Scene3DRuntime() as rt:
            generate_scene3d_batches(
                base_url, api_key, coder_model, topic, scenes_all, plan,
                lambda name, code, dur: rt.validate(name, code, dur, aspect=width / float(height)),
                ctx.log, cancel_event=ctx.cancel_event, batch_beats=batch_beats, parallel_calls=parallel_calls,
                only_indices=missing, on_code_update=on_code_update,
            )
        still = missing_beat_indices_3d(plan)
        if still:
            raise RuntimeError(
                f"{len(still)} of {len(scenes_all)} shots could not be turned into valid 3D code: "
                + ", ".join(_beat_name(scenes_all[i], i) for i in still)
                + ". The other shots are saved. Run the render again to retry only these (or try a stronger coder model)."
            )
    else:
        ctx.log(f"[BEATS] All {len(scenes_all)} shots already saved - no AI scene writing needed.")

    plan_path = ctx.workdir / "timeline_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    ctx.log("[3D] The audit loop of the flat type is not used here: every shot was already checked in the sandbox.")

    ctx.check_cancelled()
    final_bgm = resolve_bgm(mode="full_render", user_path=str(bgm_path) if bgm_path else None, synth_enabled=synth_bgm,
                            total_dur=total_dur, recommended_bpm=ref_profile.get("recommended_bpm", 116),
                            workdir=ctx.workdir, log_fn=ctx.log)

    cues = None
    if captions:
        from ..core.captions import build_cues
        from ..core.scripttext import parse_tagged_script
        clean = parse_tagged_script(script_raw_text)[0] if script_raw_text else ""
        cues, rep = build_cues(clean, words)
        ctx.log(f"[CAPTIONS] {rep['cues']} phrases from the {rep['source']} text")

    beats = [(_beat_name(sc, i), plan["scene_sources"]["beats"][_beat_name(sc, i)], float(sc["start"]), float(sc["end"]))
             for i, sc in enumerate(scenes_all)]
    total_frames = total_dur * FPS
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    ctx.set_progress(70, f"Rendering {total_frames} frames with three.js...")
    ctx.log(f"[3D RENDER] {total_frames} frames, {len(beats)} shots, {width}x{height}, {FPS} fps.")
    step = max(1, total_frames // 100)

    def _progress(n: int) -> None:
        if n % step == 0 or n == total_frames:
            ctx.set_progress(70 + int(25 * n / total_frames), f"Rendering frame {n}/{total_frames}")

    with Scene3DRenderer(three, size=(width, height)) as rd:
        frames = rd.iter_frames(beats, FPS, total_frames, should_stop=ctx.cancel_event.is_set if ctx.cancel_event else None)
        if cues:
            frames = _caption_frames(frames, cues, FPS)
        written = encode_with_audio(frames, output_mp4, FPS, (width, height), total_dur, str(vo), final_bgm,
                                    log_path=ctx.workdir / "ffmpeg_render.log", on_frame=_progress)
    if written != total_frames:
        raise RuntimeError(f"3D render wrote {written} frames, expected {total_frames}.")

    ctx.check_cancelled()
    ctx.set_progress(96, "Running 7-point automated verification...")
    verify_res = run_verification_suite(output_path=str(output_mp4), expected_dur=total_dur, expected_w=width, expected_h=height,
                                        log_fn=ctx.log, max_duration=max_duration, check_dir=ctx.workdir / "check")

    scenes_js = ctx.workdir / "generated_scenes.js"
    scenes_js.write_text("\n\n".join(plan["scene_sources"]["beats"][_beat_name(sc, i)] for i, sc in enumerate(scenes_all)) + "\n", encoding="utf-8")
    ctx.set_progress(100, f"Render Complete & Verified ({verify_res['passed']}/{verify_res['total']})")
    return {
        "output_mp4": str(output_mp4), "total_dur": total_dur, "width": width, "height": height, "frames": total_frames,
        "plan": plan, "namespace": {}, "words": words, "verification": verify_res,
        "timeline_plan_path": str(plan_path), "generated_scenes_path": str(scenes_js),
    }
