"""Pipeline orchestration services decoupled from Tkinter.

Accepts RunContext for logging, progress reporting, path resolution, and cancellation.
"""
import os
import re
import json
import math
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union, Callable

from ..core.constants import (
    FPS, INTRO_OUTRO_SECONDS, DEFAULT_MAX_DURATION,
    ELEVENLABS_VOICE_PRESETS,
)
from ..core.scripttext import (
    parse_tagged_script, script_fingerprint,
)
from ..core.script import (
    generate_script, estimate_video_seconds, check_script_fits,
)
from ..core.tts import (
    generate_elevenlabs_tts, generate_edge_tts_fallback, fetch_elevenlabs_voices,
)
from ..core.audio import (
    get_audio_duration, extract_word_timestamps,
)
from ..core.reference import (
    analyze_reference_video,
)
from ..core.bgm import (
    generate_synthetic_ambient_bgm,
)
from ..core.storyboard import (
    generate_two_pass_storyboard_and_code,
)
from ..core.scene_code import (
    build_namespace, register_beat_source, write_scene_module_file,
)
from ..core.audit import (
    audit_and_fix_beats, rewrite_single_beat_function_with_ai,
)
from ..core.frames import (
    render_beat_preview_sheet,
)
from ..core.render import (
    default_render_workers, render_video_pipeline,
)
from ..core.verify import (
    run_verification_suite,
)
from .context import RunContext


def resolve_dimensions(orientation: str, ref_profile: Optional[dict] = None) -> Tuple[int, int]:
    """Resolves output canvas dimensions matching legacy logic."""
    ref = ref_profile or {}
    if "1080x1920" in orientation or orientation.lower() in ("portrait", "vertical"):
        return 1080, 1920
    elif "1920x1080" in orientation or orientation.lower() in ("landscape", "horizontal"):
        return 1920, 1080
    else:
        return int(ref.get("width", 1080)), int(ref.get("height", 1920))


def resolve_bgm(
    mode: str,
    user_path: Optional[str],
    synth_enabled: bool,
    total_dur: int,
    recommended_bpm: int,
    workdir: Path,
    log_fn: Callable[[str], None],
) -> Optional[str]:
    """
    Replicates the exact two BGM semantics:
    - 'full_render': user file if exists; else synthesized if synth_enabled; else None.
    - 'reroll': user file if exists; else reuse existing synthetic BGM if file exists; else None.
    """
    cleaned_user = str(user_path).strip() if user_path else ""
    if cleaned_user and os.path.exists(cleaned_user):
        return cleaned_user

    synth_file = workdir / "synthetic_ambient_bgm.wav"

    if mode == "full_render":
        if synth_enabled:
            return generate_synthetic_ambient_bgm(
                str(synth_file),
                total_dur,
                recommended_bpm,
                log_fn,
            )
        return None
    elif mode == "reroll":
        if synth_file.exists():
            return str(synth_file)
        return None
    else:
        raise ValueError(f"Unknown BGM resolution mode: {mode}")


def execute_generate_script(
    ctx: RunContext,
    base_url: str,
    api_key: str,
    model: str,
    topic: str,
    language: str,
    max_duration: int = DEFAULT_MAX_DURATION,
) -> dict:
    """Generates structured script with estimates and tags."""
    ctx.check_cancelled()
    ctx.set_progress(15, f"Generating structured script via '{model}' (cap {max_duration}s)...")
    data = generate_script(
        base_url=base_url,
        api_key=api_key,
        model=model,
        topic=topic,
        script_lang=language,
        log_fn=ctx.log,
        max_duration=max_duration,
    )
    clean_text = data["clean_tts_text"]
    word_count = len(clean_text.split())
    est_vo, est_vid = estimate_video_seconds(word_count)
    ctx.log(f"[SCRIPT] Estimate: ~{word_count} spoken words ≈ {est_vo}s voiceover ≈ {est_vid}s total video (cap {max_duration}s)")
    ctx.set_progress(100, "Structured script ready!")
    return data


def execute_generate_voiceover(
    ctx: RunContext,
    raw_script_text: str,
    out_vo_path: Union[str, Path],
    provider: str,
    language: str,
    max_duration: int = DEFAULT_MAX_DURATION,
    el_api_key: str = "",
    el_voice: str = "",
    el_model: str = "eleven_multilingual_v2",
    el_stability: float = 0.5,
    el_similarity: float = 0.75,
    el_style: float = 0.0,
    el_speaker_boost: bool = True,
    el_ssml_breaks: bool = True,
) -> dict:
    """Synthesizes voiceover, checks script fit, writes fingerprint sidecar, and returns metadata."""
    ctx.check_cancelled()
    clean_raw = raw_script_text.strip()
    if not clean_raw:
        raise ValueError("The script text is empty.")

    check_script_fits(clean_raw, max_duration, ctx.log)

    out_vo = Path(out_vo_path)
    out_vo.parent.mkdir(parents=True, exist_ok=True)
    out_vo_str = str(out_vo)

    ctx.set_progress(25, f"Synthesizing voiceover via {provider}...")
    if "ElevenLabs" in provider:
        generate_elevenlabs_tts(
            raw_editor_text=clean_raw,
            output_path=out_vo_str,
            xi_api_key=el_api_key,
            voice_id=el_voice,
            model_id=el_model,
            stability=el_stability,
            similarity_boost=el_similarity,
            style=el_style,
            use_speaker_boost=el_speaker_boost,
            add_ssml_breaks=el_ssml_breaks,
            log_fn=ctx.log,
            language_code="en" if "English" in language else "id",
        )
    else:
        edge_voice = "en-US-ChristopherNeural" if "English" in language else "id-ID-ArdiNeural"
        generate_edge_tts_fallback(clean_raw, out_vo_str, edge_voice, ctx.log)

    fingerprint = script_fingerprint(clean_raw)
    try:
        sidecar_path = Path(f"{out_vo_str}.script.sha1")
        with open(sidecar_path, "w", encoding="utf-8") as f:
            f.write(fingerprint)
    except OSError as err:
        ctx.log(f"[TTS WARN] Could not write script fingerprint: {err}")

    dur = get_audio_duration(out_vo_str, ctx.log)
    total_dur = math.ceil(dur + INTRO_OUTRO_SECONDS)
    ctx.log(f"[TTS READY] Voiceover duration: {dur:.2f}s | Target video duration: {total_dur}s (cap {max_duration}s)")
    if total_dur > max_duration:
        raise ValueError(
            f"Voiceover duration ({dur:.1f}s + {INTRO_OUTRO_SECONDS}s = {total_dur}s) "
            f"exceeds max duration cap of {max_duration}s."
        )

    ctx.set_progress(100, f"Voiceover generated successfully ({dur:.2f}s)")
    return {
        "audio_path": out_vo_str,
        "duration": dur,
        "total_video_duration": total_dur,
        "fingerprint": fingerprint,
    }


def execute_full_render(
    ctx: RunContext,
    base_url: str,
    api_key: str,
    coder_model: str,
    topic: str,
    script_raw_text: str,
    vo_path: Union[str, Path],
    ref_path: Optional[Union[str, Path]] = None,
    orientation: str = "portrait",
    whisper_size: str = "base",
    language: str = "id",
    max_duration: int = DEFAULT_MAX_DURATION,
    bgm_path: Optional[Union[str, Path]] = None,
    synth_bgm: bool = True,
    workers: Optional[int] = None,
    output_mp4_path: Optional[Union[str, Path]] = None,
    check_script_fingerprint: bool = True,
    existing_plan: Optional[dict] = None,
    existing_namespace: Optional[Dict[str, Any]] = None,
    on_plan_ready: Optional[Callable[[dict, Dict[str, Any], List[dict]], None]] = None,
) -> dict:
    """Executes full video generation pipeline from voiceover and script to verified MP4."""
    ctx.check_cancelled()
    vo = Path(vo_path)
    if not vo.exists():
        raise FileNotFoundError(f"Voiceover audio file not found: {vo}")

    # Guard: fingerprint check
    if check_script_fingerprint and script_raw_text:
        sidecar = Path(f"{str(vo)}.script.sha1")
        if sidecar.exists():
            saved_fp = sidecar.read_text(encoding="utf-8").strip()
            current_fp = script_fingerprint(script_raw_text)
            if saved_fp != current_fp:
                raise ValueError(
                    f"Voiceover fingerprint mismatch! The audio was recorded from a different script.\n"
                    f"Re-generate voiceover before rendering."
                )

    dur = get_audio_duration(str(vo), ctx.log)
    total_dur = math.ceil(dur + INTRO_OUTRO_SECONDS)
    ctx.log(f"[PRE-CHECK] Voiceover duration: {dur:.2f}s -> Video total: {total_dur}s (cap {max_duration}s)")
    if total_dur > max_duration:
        raise ValueError(f"Total duration ({total_dur}s) exceeds max allowed duration cap ({max_duration}s)!")

    _, parsed_sections = parse_tagged_script(script_raw_text)
    if not parsed_sections:
        parsed_sections = [{"tag": "Body", "text": script_raw_text}]

    # Reference profile and canvas size
    ref_profile = analyze_reference_video(str(ref_path) if ref_path else None, ctx.log)
    width, height = resolve_dimensions(orientation, ref_profile)

    ctx.check_cancelled()
    # Speech alignment via Whisper
    ctx.set_progress(32, f"Extracting word timestamps (Faster-Whisper {whisper_size})...")
    whisper_lang = "en" if "English" in language else "id"
    words = extract_word_timestamps(str(vo), whisper_size, ctx.log, language=whisper_lang)

    ctx.check_cancelled()
    scene_mod_path = ctx.workdir / "generated_scenes.py"
    temp_scene_mod_path = ctx.workdir / ".generated_scenes.tmp"

    if existing_plan:
        ctx.log(f"[STORYBOARD] Reusing existing storyboard plan with {len(existing_plan.get('scenes', []))} beats (ZERO LLM tokens required).")
        plan = existing_plan
        namespace = existing_namespace if existing_namespace is not None else build_namespace(plan.get("scene_sources", {}), ctx.log)
    else:
        # Pass 1 & Pass 2
        ctx.set_progress(45, f"Running 2-Pass Storyboard & Vector Artist via '{coder_model}'...")
        plan, namespace = generate_two_pass_storyboard_and_code(
            base_url=base_url,
            api_key=api_key,
            model=coder_model,
            topic=topic,
            sections=parsed_sections,
            words=words,
            total_dur=total_dur,
            ref_profile=ref_profile,
            log_fn=ctx.log,
            cancel_event=ctx.cancel_event,
            scene_module_path=temp_scene_mod_path,
        )

    # Persist timeline plan JSON immediately BEFORE audit loop
    plan_path = ctx.workdir / "timeline_plan.json"
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)

    # Notify callback so plan and transcript are safely saved to SQLite immediately
    if on_plan_ready:
        try:
            on_plan_ready(plan, namespace, words)
        except Exception as e:
            ctx.log(f"[WARN] on_plan_ready callback error: {e}")

    ctx.check_cancelled()
    # Audit loop
    ctx.set_progress(62, "Auditing vector complexity, motion & visual uniqueness...")
    audit_and_fix_beats(
        plan=plan,
        namespace=namespace,
        words=words,
        width=width,
        height=height,
        base_url=base_url,
        api_key=api_key,
        model=coder_model,
        topic=topic,
        log_fn=ctx.log,
        max_retries=2,
        progress_fn=ctx.set_progress,
        cancel_event=ctx.cancel_event,
        scene_module_path=temp_scene_mod_path,
    )

    # Update persisted timeline plan JSON after audit loop
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)

    ctx.check_cancelled()
    # Resolve BGM
    final_bgm = resolve_bgm(
        mode="full_render",
        user_path=str(bgm_path) if bgm_path else None,
        synth_enabled=synth_bgm,
        total_dur=total_dur,
        recommended_bpm=ref_profile.get("recommended_bpm", 116),
        workdir=ctx.workdir,
        log_fn=ctx.log,
    )

    # Render MP4
    out_mp4 = Path(output_mp4_path) if output_mp4_path else ctx.workdir / "output_motion_graphic.mp4"
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_log = ctx.workdir / "ffmpeg_render.log"
    render_workers = workers if workers is not None else default_render_workers()

    ctx.set_progress(70, "Starting FFmpeg rawvideo render pipeline...")
    render_video_pipeline(
        plan=plan,
        namespace=namespace,
        words=words,
        vo_path=str(vo),
        bgm_path=final_bgm,
        output_path=str(out_mp4),
        total_dur=total_dur,
        width=width,
        height=height,
        log_fn=ctx.log,
        progress_fn=ctx.set_progress,
        workers=render_workers,
        ffmpeg_log_path=ffmpeg_log,
        cancel_event=ctx.cancel_event,
    )

    ctx.check_cancelled()
    # Verification
    ctx.set_progress(96, "Running 7-point automated verification...")
    check_dir = ctx.workdir / "check"
    verify_res = run_verification_suite(
        output_path=str(out_mp4),
        expected_dur=total_dur,
        expected_w=width,
        expected_h=height,
        log_fn=ctx.log,
        max_duration=max_duration,
        check_dir=check_dir,
    )

    # Write final generated_scenes.py now that video rendering and verification is complete
    write_scene_module_file(plan, path=scene_mod_path)
    if temp_scene_mod_path.exists():
        try:
            temp_scene_mod_path.unlink()
        except OSError:
            pass

    ctx.set_progress(100, f"Render Complete & Verified ({verify_res['passed']}/{verify_res['total']})")
    return {
        "output_mp4": str(out_mp4),
        "total_dur": total_dur,
        "width": width,
        "height": height,
        "frames": total_dur * FPS,
        "plan": plan,
        "namespace": namespace,
        "words": words,
        "verification": verify_res,
        "timeline_plan_path": str(plan_path),
        "generated_scenes_path": str(scene_mod_path),
    }


def execute_reroll_beat(
    ctx: RunContext,
    plan: dict,
    namespace: Dict[str, Any],
    words: List[dict],
    beat_idx: int,
    custom_note: str,
    base_url: str,
    api_key: str,
    coder_model: str,
    topic: str,
    vo_path: Union[str, Path],
    ref_path: Optional[Union[str, Path]] = None,
    orientation: str = "portrait",
    bgm_path: Optional[Union[str, Path]] = None,
    workers: Optional[int] = None,
    output_mp4_path: Optional[Union[str, Path]] = None,
    max_duration: int = DEFAULT_MAX_DURATION,
) -> dict:
    """Regenerates a single beat function with AI note, modifies blueprint, and re-renders full MP4."""
    ctx.check_cancelled()
    scenes = plan.get("scenes", [])
    if not 0 <= beat_idx < len(scenes):
        raise IndexError(f"Beat index {beat_idx + 1} out of bounds (1..{len(scenes)})")

    beat = scenes[beat_idx]
    user_note = custom_note.strip() or "Draw a completely fresh, detailed multi-part flat-vector illustration for this beat."
    if custom_note.strip():
        beat["visual_blueprint"] = f"{beat.get('visual_blueprint', '')} ({user_note})"

    ctx.set_progress(25, f"Regenerating Beat #{beat_idx + 1:02d} via '{coder_model}'...")
    new_src = rewrite_single_beat_function_with_ai(
        base_url=base_url,
        api_key=api_key,
        model=coder_model,
        topic=topic,
        beat_idx=beat_idx,
        beat=beat,
        reason=user_note,
        log_fn=ctx.log,
    )
    scene_mod_path = ctx.workdir / "generated_scenes.py"
    if new_src and register_beat_source(plan, namespace, f"beat_{beat_idx + 1:02d}", new_src, ctx.log):
        write_scene_module_file(plan, path=scene_mod_path)
    else:
        ctx.log(f"[REROLL WARN] No valid replacement for Beat #{beat_idx + 1:02d}; re-rendering with the previous drawing.")

    ref_profile = analyze_reference_video(str(ref_path) if ref_path else None, ctx.log)
    width, height = resolve_dimensions(orientation, ref_profile)
    total_dur = int(round(float(scenes[-1]["end"])))

    # Resolve BGM with reroll semantics (reuses synthetic BGM if file exists)
    final_bgm = resolve_bgm(
        mode="reroll",
        user_path=str(bgm_path) if bgm_path else None,
        synth_enabled=False,
        total_dur=total_dur,
        recommended_bpm=ref_profile.get("recommended_bpm", 116),
        workdir=ctx.workdir,
        log_fn=ctx.log,
    )

    out_mp4 = Path(output_mp4_path) if output_mp4_path else ctx.workdir / "output_motion_graphic.mp4"
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_log = ctx.workdir / "ffmpeg_render.log"
    render_workers = workers if workers is not None else default_render_workers()

    ctx.set_progress(45, "Re-rendering MP4 with updated beat...")
    render_video_pipeline(
        plan=plan,
        namespace=namespace,
        words=words,
        vo_path=str(vo_path),
        bgm_path=final_bgm,
        output_path=str(out_mp4),
        total_dur=total_dur,
        width=width,
        height=height,
        log_fn=ctx.log,
        progress_fn=ctx.set_progress,
        workers=render_workers,
        ffmpeg_log_path=ffmpeg_log,
        cancel_event=ctx.cancel_event,
    )

    ctx.set_progress(95, "Running verification on re-rendered MP4...")
    check_dir = ctx.workdir / "check"
    verify_res = run_verification_suite(
        output_path=str(out_mp4),
        expected_dur=total_dur,
        expected_w=width,
        expected_h=height,
        log_fn=ctx.log,
        max_duration=max_duration,
        check_dir=check_dir,
    )

    ctx.set_progress(100, f"Beat #{beat_idx + 1:02d} Re-rolled & Verified!")
    return {
        "output_mp4": str(out_mp4),
        "beat_idx": beat_idx,
        "new_source": new_src,
        "verification": verify_res,
    }


def execute_preview_beat(
    ctx: RunContext,
    plan: dict,
    namespace: Dict[str, Any],
    words: List[dict],
    beat_idx: int,
    orientation: str = "portrait",
    ref_path: Optional[Union[str, Path]] = None,
    out_png_path: Optional[Union[str, Path]] = None,
) -> str:
    """Renders a 6-frame contact sheet for a single beat without ffmpeg."""
    ctx.check_cancelled()
    ref_profile = analyze_reference_video(str(ref_path) if ref_path else None, ctx.log)
    width, height = resolve_dimensions(orientation, ref_profile)

    ctx.set_progress(40, f"Rendering preview of Beat #{beat_idx + 1:02d}...")
    out_png = Path(out_png_path) if out_png_path else ctx.workdir / f"preview_beat_{beat_idx + 1:02d}.png"
    out_png.parent.mkdir(parents=True, exist_ok=True)

    render_beat_preview_sheet(
        plan=plan,
        namespace=namespace,
        words=words,
        beat_idx=beat_idx,
        width=width,
        height=height,
        out_path=str(out_png),
    )
    ctx.log(f"[PREVIEW] Beat #{beat_idx + 1:02d} contact sheet saved: {out_png}")
    ctx.set_progress(100, f"Preview ready (Beat #{beat_idx + 1:02d})")
    return str(out_png)


def execute_verify_only(
    ctx: RunContext,
    mp4_path: Union[str, Path],
    expected_w: int = 1080,
    expected_h: int = 1920,
    expected_dur: Optional[int] = None,
    max_duration: int = DEFAULT_MAX_DURATION,
) -> dict:
    """Runs 7-point verification suite on an existing MP4 file."""
    ctx.check_cancelled()
    mp4 = Path(mp4_path)
    if not mp4.exists():
        raise FileNotFoundError(f"Output MP4 file not found: {mp4}")

    ctx.set_progress(50, "Running 7-point automated verification...")
    check_dir = ctx.workdir / "check"
    res = run_verification_suite(
        output_path=str(mp4),
        expected_dur=expected_dur,
        expected_w=expected_w,
        expected_h=expected_h,
        log_fn=ctx.log,
        max_duration=max_duration,
        check_dir=check_dir,
    )
    ctx.set_progress(100, f"Verified: {res['passed']}/{res['total']} Passed!")
    return res
