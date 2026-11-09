"""Default database seed data matching Nalarka desktop defaults."""
import re
import json
import sqlite3
from typing import Optional

from ..core.constants import (
    DEFAULT_MAX_DURATION, ELEVENLABS_VOICE_PRESETS,
    PROVIDER_DEFAULT_BASE_URL, OPENROUTER_VISUAL_CODER_MODELS,
    OPENROUTER_SCRIPT_MODELS, OAO_VISUAL_CODER_MODELS,
    OAO_SCRIPT_MODELS,
)
from ..core.render import default_render_workers

DEFAULT_SETTINGS = [
    ("render.max_duration", str(DEFAULT_MAX_DURATION), "int"),
    ("render.workers", str(default_render_workers()), "int"),
    ("render.orientation", "Match Reference", "string"),
    ("render.whisper_size", "base", "string"),
    ("render.synth_bgm", "true", "bool"),
    ("render.output_name", "output_motion_graphic.mp4", "string"),
    ("render.reuse_transcript_cache", "false", "bool"),
    ("tts.provider", "ElevenLabs API v1", "string"),
    ("tts.voice", ELEVENLABS_VOICE_PRESETS[0] if ELEVENLABS_VOICE_PRESETS else "Bagas (Indonesia)", "string"),
    ("tts.model_id", "eleven_multilingual_v2", "string"),
    ("tts.stability", "0.50", "float"),
    ("tts.similarity", "0.75", "float"),
    ("tts.style", "0.15", "float"),
    ("tts.speaker_boost", "true", "bool"),
    ("tts.ssml_breaks", "false", "bool"),
    ("library.dir", "references_library", "string"),
    ("library.selected_reference_id", "", "string"),
    ("ai.batch_beats", "6", "int"),
    ("ai.parallel_calls", "3", "int"),
    ("ai.max_consecutive_failures", "3", "int"),
    ("ai.max_total_failures", "6", "int"),
    ("ai.max_minutes_per_run", "45", "int"),
    ("ui.active_provider", "custom", "string"),
]


def seed_defaults(conn: sqlite3.Connection) -> None:
    """Idempotently seeds settings, providers, model presets, and TTS voices."""
    with conn:
        # 1. App settings
        for key, val, vtype in DEFAULT_SETTINGS:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, value_type)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (key, val, vtype),
            )

        # 2. AI providers
        custom_url = PROVIDER_DEFAULT_BASE_URL.get("custom", "https://oao.ai.id/v1")
        or_url = PROVIDER_DEFAULT_BASE_URL.get("openrouter", "https://openrouter.ai/api/v1")
        conn.execute(
            """
            INSERT INTO ai_providers (key, label, base_url, coder_model, script_model, is_active)
            VALUES ('custom', 'Custom / LM Studio / Local / OAO', ?, ?, ?, 1)
            ON CONFLICT(key) DO NOTHING
            """,
            (custom_url, OAO_VISUAL_CODER_MODELS[0], OAO_SCRIPT_MODELS[0]),
        )
        conn.execute(
            """
            INSERT INTO ai_providers (key, label, base_url, coder_model, script_model, is_active)
            VALUES ('openrouter', 'OpenRouter (openrouter.ai)', ?, ?, ?, 0)
            ON CONFLICT(key) DO NOTHING
            """,
            (or_url, OPENROUTER_VISUAL_CODER_MODELS[0], OPENROUTER_SCRIPT_MODELS[0]),
        )

        # 3. Model catalog (presets)
        for model in OAO_VISUAL_CODER_MODELS:
            conn.execute(
                """
                INSERT INTO model_catalog (provider_key, model_id, role, is_free, source)
                VALUES ('custom', ?, 'coder', 0, 'preset')
                ON CONFLICT(provider_key, model_id, role) DO NOTHING
                """,
                (model,),
            )
        for model in OAO_SCRIPT_MODELS:
            conn.execute(
                """
                INSERT INTO model_catalog (provider_key, model_id, role, is_free, source)
                VALUES ('custom', ?, 'script', 0, 'preset')
                ON CONFLICT(provider_key, model_id, role) DO NOTHING
                """,
                (model,),
            )
        for model in OPENROUTER_VISUAL_CODER_MODELS:
            conn.execute(
                """
                INSERT INTO model_catalog (provider_key, model_id, role, is_free, source)
                VALUES ('openrouter', ?, 'coder', ?, 'preset')
                ON CONFLICT(provider_key, model_id, role) DO NOTHING
                """,
                (model, 1 if ":free" in model else 0),
            )
        for model in OPENROUTER_SCRIPT_MODELS:
            conn.execute(
                """
                INSERT INTO model_catalog (provider_key, model_id, role, is_free, source)
                VALUES ('openrouter', ?, 'script', ?, 'preset')
                ON CONFLICT(provider_key, model_id, role) DO NOTHING
                """,
                (model, 1 if ":free" in model else 0),
            )

        # 4. Preset TTS voices
        for voice_str in ELEVENLABS_VOICE_PRESETS:
            # e.g. "Bagas (Indonesia) [generated] (ID_HERE)" or simple "Name (id)"
            m = re.match(r"^(.*?)(?:\s*\[(.*?)\])?\s*\((.*?)\)$", voice_str)
            if m:
                v_name, v_cat, v_id = m.group(1).strip(), m.group(2) or "preset", m.group(3).strip()
            else:
                v_name, v_cat, v_id = voice_str, "preset", voice_str
            conn.execute(
                """
                INSERT INTO tts_voices (voice_id, name, category, label, source)
                VALUES (?, ?, ?, ?, 'preset')
                ON CONFLICT(voice_id) DO NOTHING
                """,
                (v_id, v_name, v_cat, voice_str),
            )
