"""Core module of Nalarka Motion Graphics Studio (GUI-free engine)."""

from .constants import (
    CONFIG_FILE, LOG_FILE, FFMPEG_LOG_FILE, DEFAULT_REF_LIBRARY_DIR,
    FPS, MAX_DURATION, MIN_DURATION_CAP, HARD_MAX_DURATION, DEFAULT_MAX_DURATION,
    DURATION_PRESETS, SPEECH_WORDS_PER_SEC, INTRO_OUTRO_SECONDS, MIN_BEAT_SECONDS,
    PASS1_WINDOW_SECONDS, PASS2_BATCH_BEATS, PASS2_PARALLEL_CALLS, EL_CHUNK_CHARS,
    TTS_OVERSHOOT_TOLERANCE, PROVIDER_CUSTOM, PROVIDER_OPENROUTER, PROVIDER_DEEPSEEK, PROVIDER_LABELS,
    DEEPSEEK_VISUAL_CODER_MODELS, DEEPSEEK_SCRIPT_MODELS, PROVIDER_HINTS,
    PROVIDER_DEFAULT_BASE_URL, OPENROUTER_MODELS_URL, OPENROUTER_VISUAL_CODER_MODELS,
    OPENROUTER_SCRIPT_MODELS, OAO_VISUAL_CODER_MODELS, OAO_SCRIPT_MODELS,
    ELEVENLABS_VOICE_PRESETS, VALID_LAYOUTS, VALID_BG_COLORS, VALID_TRANSITIONS,
)
from .logging_setup import setup_logging
from .mathutil import (
    hex_to_rgb, rgb_to_hex, clamp, ease_out_cubic, ease_in_out_cubic,
    ease_out_back, ease_out_elastic, rotate_point, sample_bezier_curve,
)
from .scripttext import (
    format_sections_to_tagged_text, parse_tagged_script,
    prepare_text_for_elevenlabs, script_fingerprint,
)
from .extractors import extract_json_safely, extract_python_code_safely
from .llm import (
    is_openrouter, is_deepseek, make_client, fetch_openrouter_models, fetch_provider_models, friendly_api_error,
    _chat_text, call_ai_json, call_ai_python_code,
)
from .script import (
    script_word_budget, script_section_count, estimate_video_seconds,
    check_script_fits, generate_script,
)
from .tts import (
    fetch_elevenlabs_voices, split_text_for_tts, concat_audio_files,
    generate_elevenlabs_tts, generate_edge_tts_fallback,
)
from .bgm import BGM_LOOP_SECONDS, generate_synthetic_ambient_bgm
from .audio import get_audio_duration, extract_word_timestamps
from .reference import scan_reference_library, analyze_reference_video
from .vectorkit import get_speckled_paper_canvas, load_font, VectorKit
from .prompts import PASS1_STORYBOARD_PROMPT, PASS2_PYTHON_VECTOR_ARTIST_PROMPT
from .scene_code import (
    validate_scene_source, new_scene_namespace, split_scene_code,
    register_source, new_scene_sources, build_namespace,
    add_generated_code, register_beat_source, write_scene_module_file,
)
from .storyboard import (
    sanitize_scenes, compress_words, storyboard_windows,
    plan_storyboard, generate_scene_code_batches,
    generate_two_pass_storyboard_and_code,
)
from .audit import (
    render_single_beat_canvas, rewrite_single_beat_function_with_ai,
    audit_and_fix_beats,
)
from .frames import (
    composite_transition, render_frame, render_beat_preview_sheet,
)
from .render import (
    default_render_workers, render_video_pipeline,
)
from .verify import (
    run_verification_suite,
)

__all__ = [
    "CONFIG_FILE", "LOG_FILE", "FFMPEG_LOG_FILE", "DEFAULT_REF_LIBRARY_DIR",
    "FPS", "MAX_DURATION", "MIN_DURATION_CAP", "HARD_MAX_DURATION", "DEFAULT_MAX_DURATION",
    "DURATION_PRESETS", "SPEECH_WORDS_PER_SEC", "INTRO_OUTRO_SECONDS", "MIN_BEAT_SECONDS",
    "PASS1_WINDOW_SECONDS", "PASS2_BATCH_BEATS", "PASS2_PARALLEL_CALLS", "EL_CHUNK_CHARS",
    "TTS_OVERSHOOT_TOLERANCE", "PROVIDER_CUSTOM", "PROVIDER_OPENROUTER", "PROVIDER_DEEPSEEK", "PROVIDER_LABELS",
    "DEEPSEEK_VISUAL_CODER_MODELS", "DEEPSEEK_SCRIPT_MODELS", "PROVIDER_HINTS", "is_deepseek", "fetch_provider_models",
    "PROVIDER_DEFAULT_BASE_URL", "OPENROUTER_MODELS_URL", "OPENROUTER_VISUAL_CODER_MODELS",
    "OPENROUTER_SCRIPT_MODELS", "OAO_VISUAL_CODER_MODELS", "OAO_SCRIPT_MODELS",
    "ELEVENLABS_VOICE_PRESETS", "VALID_LAYOUTS", "VALID_BG_COLORS", "VALID_TRANSITIONS",
    "setup_logging", "hex_to_rgb", "rgb_to_hex", "clamp", "ease_out_cubic",
    "ease_in_out_cubic", "ease_out_back", "ease_out_elastic", "rotate_point",
    "sample_bezier_curve", "format_sections_to_tagged_text", "parse_tagged_script",
    "prepare_text_for_elevenlabs", "script_fingerprint", "extract_json_safely",
    "extract_python_code_safely", "is_openrouter", "make_client", "fetch_openrouter_models",
    "friendly_api_error", "_chat_text", "call_ai_json", "call_ai_python_code",
    "script_word_budget", "script_section_count", "estimate_video_seconds",
    "check_script_fits", "generate_script", "fetch_elevenlabs_voices",
    "split_text_for_tts", "concat_audio_files", "generate_elevenlabs_tts",
    "generate_edge_tts_fallback", "BGM_LOOP_SECONDS", "generate_synthetic_ambient_bgm",
    "get_audio_duration", "extract_word_timestamps", "scan_reference_library",
    "analyze_reference_video", "get_speckled_paper_canvas", "load_font",
    "VectorKit", "PASS1_STORYBOARD_PROMPT", "PASS2_PYTHON_VECTOR_ARTIST_PROMPT",
    "validate_scene_source", "new_scene_namespace", "split_scene_code",
    "register_source", "new_scene_sources", "build_namespace",
    "add_generated_code", "register_beat_source", "write_scene_module_file",
    "sanitize_scenes", "compress_words", "storyboard_windows",
    "plan_storyboard", "generate_scene_code_batches",
    "generate_two_pass_storyboard_and_code", "render_single_beat_canvas",
    "rewrite_single_beat_function_with_ai", "audit_and_fix_beats",
    "composite_transition", "render_frame", "render_beat_preview_sheet",
    "default_render_workers", "render_video_pipeline", "run_verification_suite",
]
