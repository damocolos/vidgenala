"""Tests for SQLite database, migrations, repositories, and secrets (Fase 3)."""
import os
import sqlite3
import pytest
from pathlib import Path

from nalarka.db.connection import get_connection, run_migrations, get_user_version
from nalarka.db.seed import seed_defaults
from nalarka.db.repos.settings import (
    get_setting, set_setting, get_all_settings, get_provider, set_provider,
    list_providers, get_models_catalog, list_tts_voices,
)
from nalarka.db.repos.topics import (
    create_topic, get_topic, update_topic, archive_topic, delete_topic, list_topics,
)
from nalarka.db.repos.scripts import (
    create_script, get_script, get_current_script, list_scripts_for_topic,
)
from nalarka.db.repos.voiceovers import (
    create_voiceover, get_voiceover, create_transcript, get_transcript,
)
from nalarka.db.repos.library import (
    upsert_library_item, get_library_item, list_library_items, sync_library_folder,
)
from nalarka.db.repos.renders import (
    create_render, update_render_status, get_render, create_verification,
    get_verification_for_render,
)
from nalarka.db.repos.jobs import (
    create_job, get_job, update_job_status, append_job_event,
    batch_append_job_events, get_job_events,
)
from nalarka.services.secrets import (
    encrypt_secret, decrypt_secret, mask_secret, redact_sensitive_string,
)


@pytest.fixture
def db(tmp_path):
    db_file = tmp_path / "test_nalarka.db"
    conn = get_connection(db_file)
    run_migrations(conn)
    seed_defaults(conn)
    yield conn
    conn.close()


def test_connection_pragmas(db):
    cur = db.cursor()
    cur.execute("PRAGMA foreign_keys;")
    assert cur.fetchone()[0] == 1
    cur.execute("PRAGMA busy_timeout;")
    assert cur.fetchone()[0] == 5000
    assert get_user_version(db) == 2


def test_secrets_encryption_and_masking():
    secret = "sk-ant-api03-abcdef1234567890abcdef"
    enc = encrypt_secret(secret)
    assert enc is not None
    assert enc != secret.encode("utf-8")
    assert secret not in enc.decode("latin1", errors="ignore")

    dec = decrypt_secret(enc)
    assert dec == secret

    masked = mask_secret(secret)
    assert masked == "sk-...cdef"
    assert secret not in masked

    # Log redaction
    log_msg = f"Calling AI with key {secret} and Bearer token1234567890abcdef12345"
    redacted = redact_sensitive_string(log_msg)
    assert secret not in redacted
    assert "[REDACTED_API_KEY]" in redacted


def test_settings_and_providers_repository(db):
    # Setting get/set with type conversions
    set_setting(db, "test.int", 42)
    assert get_setting(db, "test.int") == 42

    set_setting(db, "test.bool", True)
    assert get_setting(db, "test.bool") is True

    set_setting(db, "test.json", {"a": 1, "b": [2, 3]})
    assert get_setting(db, "test.json") == {"a": 1, "b": [2, 3]}

    # Provider with encrypted key
    set_provider(
        db, "openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-or-v1-secret1234567890abcdef",
        coder_model="anthropic/claude-3.7-sonnet",
        script_model="anthropic/claude-3.5-haiku",
        is_active=True,
    )
    p = get_provider(db, "openrouter")
    assert p["is_active"] == 1
    assert p["api_key"] == "sk-or-v1-secret1234567890abcdef"
    assert p["has_key"] is True
    assert p["masked_key"] == "sk-...cdef"

    # Only one provider can be active
    p_custom = get_provider(db, "custom")
    assert p_custom["is_active"] == 0

    # Model catalog & voices
    models = get_models_catalog(db, provider_key="openrouter")
    assert len(models) >= 2

    voices = list_tts_voices(db)
    assert len(voices) >= 1


def test_topics_repository(db):
    tid = create_topic(db, "Kenapa Langit Biru?", language="Indonesian", max_duration=60)
    topic = get_topic(db, tid)
    assert topic["title"] == "Kenapa Langit Biru?"
    assert topic["max_duration"] == 60
    assert topic["language"] == "Indonesian"

    # Update
    updated = update_topic(db, tid, title="Kenapa Langit Biru? (Revisi)", max_duration=90)
    assert updated["title"] == "Kenapa Langit Biru? (Revisi)"
    assert updated["max_duration"] == 90

    # Archive
    assert archive_topic(db, tid) is True
    active_topics = list_topics(db, include_archived=False)
    assert tid not in [t["id"] for t in active_topics]
    all_topics = list_topics(db, include_archived=True)
    assert tid in [t["id"] for t in all_topics]


def test_scripts_versioning_and_current_exclusivity(db):
    tid = create_topic(db, "Script Topic", max_duration=60)

    s1 = create_script(db, tid, "[Hook]\nIni hook pertama.\n[Penutup]\nSelesai.", max_duration=60)
    assert s1["version"] == 1
    assert s1["is_current"] == 1

    s2 = create_script(db, tid, "[Hook]\nIni hook kedua yang lebih bagus.\n[Penutup]\nSelesai.", max_duration=60)
    assert s2["version"] == 2
    assert s2["is_current"] == 1

    # Verify s1 is no longer current
    s1_fresh = get_script(db, s1["id"])
    assert s1_fresh["is_current"] == 0

    cur = get_current_script(db, tid)
    assert cur["id"] == s2["id"]


def test_voiceovers_and_transcripts(db):
    tid = create_topic(db, "VO Topic")
    vid = create_voiceover(
        db, topic_id=tid, file_path="audio/vo_01.mp3",
        provider="ElevenLabs", voice="Bagas", duration_sec=12.5, total_dur_locked=15,
    )
    vo = get_voiceover(db, vid)
    assert vo["file_path"] == "audio/vo_01.mp3"
    assert vo["duration_sec"] == 12.5

    tr_id = create_transcript(
        db, voiceover_id=vid, whisper_size="base", language="id",
        words=[{"word": "halo", "start": 1.0, "end": 1.5}],
    )
    tr = get_transcript(db, tr_id)
    assert len(tr["words"]) == 1
    assert tr["words"][0]["word"] == "halo"


def test_jobs_and_event_batching(db):
    jid = "job-uuid-1234"
    create_job(db, jid, kind="full_render", weight="heavy", params={"duration": 15})

    job = get_job(db, jid)
    assert job["status"] == "queued"

    update_job_status(db, jid, status="running", progress=50, status_text="Rendering...")
    job = get_job(db, jid)
    assert job["status"] == "running"
    assert job["progress"] == 50

    # Batch events
    events = [
        (jid, "log", f"Event message {i}", None)
        for i in range(10)
    ]
    batch_append_job_events(db, events)

    retrieved = get_job_events(db, jid, since_id=0)
    assert len(retrieved) == 10
    assert retrieved[0]["message"] == "Event message 0"
    assert retrieved[9]["message"] == "Event message 9"

    # Incremental query using since_id (SSE Last-Event-ID)
    since_5 = get_job_events(db, jid, since_id=retrieved[4]["id"])
    assert len(since_5) == 5
    assert since_5[0]["message"] == "Event message 5"
