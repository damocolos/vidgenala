"""Tests for script generation job execution, language argument parity, and file logging."""
import os
import json
from pathlib import Path
from unittest.mock import patch
import pytest

from nalarka.core.script import generate_script
from nalarka.services.pipeline import execute_generate_script
from nalarka.services.context import RunContext
from nalarka.jobs.jobrunner import run_job
from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults
from nalarka.db.repos.topics import create_topic
from nalarka.db.repos.jobs import create_job, get_job
from nalarka.core.constants import LOG_FILE


MOCK_LLM_RESPONSE = {
    "hook": "Dengar rekaman suara sendiri rasanya geli dan malu.",
    "inti_1": "Saat bicara suara lewat udara dan getaran tulang tengkorak.",
    "inti_2": "Tulang tengkorak memperkuat frekuensi rendah dan bass.",
    "inti_3": "Mikrofon rekaman hanya menangkap suara yang lewat udara saja.",
    "penutup": "Jadi tenang, suaramu tidak aneh, hanya beda jalur dengar saja.",
}


def test_generate_script_accepts_language_keyword_arg():
    # Test that calling generate_script with language=... does NOT raise TypeError
    with patch("nalarka.core.script.call_ai_json", return_value=MOCK_LLM_RESPONSE):
        res1 = generate_script(
            base_url="https://mock.api/v1",
            api_key="mock-key",
            model="mock-model",
            topic="Test Topic",
            language="Indonesian",
            log_fn=lambda m: None,
            max_duration=60,
        )
        assert "tagged_text" in res1
        assert "[Hook]" in res1["tagged_text"]

        res2 = generate_script(
            base_url="https://mock.api/v1",
            api_key="mock-key",
            model="mock-model",
            topic="Test Topic",
            script_lang="English",
            log_fn=lambda m: None,
            max_duration=60,
        )
        assert "tagged_text" in res2


def test_execute_generate_script_pipeline():
    ctx = RunContext(log_fn=lambda m: None)
    with patch("nalarka.core.script.call_ai_json", return_value=MOCK_LLM_RESPONSE):
        data = execute_generate_script(
            ctx=ctx,
            base_url="https://mock.api/v1",
            api_key="mock-key",
            model="mock-model",
            topic="Test Topic",
            language="Indonesian",
            max_duration=60,
        )
        assert "tagged_text" in data
        assert "clean_tts_text" in data


def test_jobrunner_generate_script_and_log_file(tmp_path, monkeypatch):
    test_db = tmp_path / "test_script_job.db"
    test_log = tmp_path / "test_debug.log"

    monkeypatch.setattr("nalarka.core.logging_setup.LOG_FILE", str(test_log))
    monkeypatch.setattr("nalarka.core.constants.LOG_FILE", str(test_log))

    conn = get_connection(str(test_db))
    run_migrations(conn)
    seed_defaults(conn)

    topic_id = create_topic(conn, title="Uji Coba Script", language="Indonesian", max_duration=60)
    job_id = "test-script-001"
    create_job(conn, job_id, kind="generate_script", weight="light", params={}, topic_id=topic_id)
    conn.close()

    with patch("nalarka.core.script.call_ai_json", return_value=MOCK_LLM_RESPONSE):
        exit_code = run_job(job_id, str(test_db))
        assert exit_code == 0

    conn = get_connection(str(test_db))
    job = get_job(conn, job_id)
    conn.close()

    assert job["status"] == "succeeded"
    assert job["error"] is None

    # Verify that the log file was written and contains job logs
    assert test_log.exists()
    log_content = test_log.read_text(encoding="utf-8")
    assert f"[{job_id}]" in log_content
    assert "generate_script" in log_content

