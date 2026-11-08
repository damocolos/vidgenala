"""Tests for config importer and exporter (Fase 3)."""
import json
import pytest
from pathlib import Path

from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults
from nalarka.services.config_importer import (
    import_legacy_config_dict, export_legacy_config,
)
from nalarka.db.repos.settings import get_provider, get_setting


@pytest.fixture
def db(tmp_path):
    conn = get_connection(tmp_path / "config_test.db")
    run_migrations(conn)
    seed_defaults(conn)
    yield conn
    conn.close()


def test_import_legacy_config_with_providers(db):
    sample = {
        "ai_provider": "openrouter",
        "providers": {
            "custom": {
                "base_url": "https://api.openai-sub.com/v1",
                "api_key": "sk-custom-12345",
                "coder_model": "claude-opus-5.5",
                "script_model": "claude-fable-5"
            },
            "openrouter": {
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "sk-or-67890",
                "coder_model": "anthropic/claude-3.7-sonnet",
                "script_model": "anthropic/claude-3.5-haiku"
            }
        },
        "tts_provider": "ElevenLabs API v1",
        "elevenlabs_api_key": "xi-secret-key-999",
        "elevenlabs_voice_id": "Bagas [generated] (ID123)",
        "elevenlabs_stability": "0.65",
        "elevenlabs_similarity": "0.85",
        "max_duration": 120,
        "render_workers": 4
    }

    res = import_legacy_config_dict(db, sample)
    assert res["imported"] >= 8

    # Verify provider settings
    p_or = get_provider(db, "openrouter")
    assert p_or["is_active"] == 1
    assert p_or["api_key"] == "sk-or-67890"

    p_cust = get_provider(db, "custom")
    assert p_cust["is_active"] == 0
    assert p_cust["api_key"] == "sk-custom-12345"

    # Verify TTS
    assert get_setting(db, "tts.stability") == 0.65
    assert get_setting(db, "tts.voice") == "Bagas [generated] (ID123)"

    # Verify render settings
    assert get_setting(db, "render.max_duration") == 120
    assert get_setting(db, "render.workers") == 4

    # Verify export
    exported = export_legacy_config(db, include_secrets=True)
    assert exported["ai_provider"] == "openrouter"
    assert exported["providers"]["openrouter"]["api_key"] == "sk-or-67890"
    assert exported["elevenlabs_api_key"] == "xi-secret-key-999"


def test_import_legacy_config_clamping(db):
    sample = {
        "max_duration": 99999,  # over 900
        "render_workers": 999,  # over 16
        "elevenlabs_stability": 2.5,  # over 1.0
    }
    import_legacy_config_dict(db, sample)
    assert get_setting(db, "render.max_duration") == 900
    assert get_setting(db, "render.workers") == 16
    assert get_setting(db, "tts.stability") == 1.0
