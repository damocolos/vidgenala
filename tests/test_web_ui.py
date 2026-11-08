"""Tests for the Nalarka Web UI routes and Jinja2 templates."""
import pytest
from fastapi.testclient import TestClient

from nalarka.api.main import create_app
from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults
from nalarka.db.repos.topics import create_topic


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_db = tmp_path / "test_web.db"
    conn = get_connection(str(test_db))
    run_migrations(conn)
    seed_defaults(conn)
    conn.close()

    monkeypatch.setenv("NALARKA_DB_PATH", str(test_db))
    app = create_app()
    with TestClient(app) as tc:
        yield tc


def test_static_files_served(client):
    res_css = client.get("/static/css/style.css")
    assert res_css.status_code == 200
    assert "Nalarka Motion Graphics Studio" in res_css.text

    res_htmx = client.get("/static/js/htmx.min.js")
    assert res_htmx.status_code == 200
    assert len(res_htmx.text) > 1000

    res_app = client.get("/static/js/app.js")
    assert res_app.status_code == 200
    assert "connectJobEvents" in res_app.text


def test_index_redirect(client):
    res = client.get("/", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"] == "/topics"


def test_topics_dashboard_view(client):
    res = client.get("/topics")
    assert res.status_code == 200
    assert "Studio Dashboard" in res.text
    assert "Create New Topic" in res.text
    assert "Total Topics" in res.text


def test_topic_studio_view_parity(client):
    # Create a test topic first
    create_res = client.post(
        "/api/topics",
        json={"title": "Misteri Laut Dalam", "language": "Indonesian", "max_duration": 60},
    )
    assert create_res.status_code == 201
    topic_id = create_res.json()["id"]

    res = client.get(f"/topics/{topic_id}")
    assert res.status_code == 200
    html = res.text

    # Verify F-01 to F-08 (Provider section)
    assert "AI Engine & Models" in html
    assert "provider-select" in html
    assert "provider-base-url" in html
    assert "provider-api-key" in html
    assert "provider-coder-model" in html
    assert "provider-script-model" in html
    assert "Save Config" in html
    assert "Test Vector Model" in html
    assert "Fetch Models" in html

    # Verify F-10 to F-18 (Topic & Script section)
    assert "Misteri Laut Dalam" in html
    assert "topic-title-input" in html
    assert "topic-lang-select" in html
    assert "max-duration-input" in html
    assert "Generate Script (AI)" in html
    assert "script-editor" in html
    assert "Copy Clean Text" in html
    assert "script-word-count" in html
    assert "OVER CAP" in html

    # Verify F-20 to F-41 (Voiceover & Render params)
    assert "Voiceover & Audio Configuration" in html
    assert "tts-provider-select" in html
    assert "ElevenLabs API v1" in html
    assert "Edge-TTS" in html
    assert "tts-voice-select" in html
    assert "tts-stability" in html
    assert "tts-similarity" in html
    assert "tts-style" in html
    assert "Speaker Boost" in html
    assert 'Add &lt;break time="0.4s"/&gt;' in html
    assert "Fetch My Voices" in html
    assert "Generate Audio Now" in html
    assert "reference-select" in html
    assert "orientation-select" in html
    assert "whisper-size-select" in html
    assert "synth-bgm-check" in html
    assert "render-workers" in html
    assert "output-name" in html

    # Verify F-42 to F-46 (Render & Beat Inspector)
    assert "Render Engine & Beat Inspector" in html
    assert "Generate & Render MP4" in html
    assert "Verify MP4" in html
    assert "beat-select" in html
    assert "Preview Selected Beat" in html
    assert "Regenerate Selected Beat" in html
    assert "beat-reroll-note" in html

    # Verify F-50 to F-54 (Console & Progress)
    assert "Engine Progress" in html
    assert "job-progress" in html
    assert "live-console" in html


def test_library_view(client):
    res = client.get("/library")
    assert res.status_code == 200
    html = res.text
    assert "Reference & Asset Library" in html
    assert "library-folder-input" in html
    assert "Scan Folder Now" in html
    assert "Upload Asset" in html
    assert "Reference Videos & Visual Assets" in html
    assert "Background Music Assets" in html


def test_history_view(client):
    res = client.get("/history")
    assert res.status_code == 200
    html = res.text
    assert "Render History & Verification" in html
    assert "All Video Renders" in html


def test_settings_view(client):
    res = client.get("/settings")
    assert res.status_code == 200
    html = res.text
    assert "Studio Settings & Configuration" in html
    assert "Text-to-Speech Defaults" in html
    assert "Config Import & Export" in html
    assert "Export config_nalarka.json" in html
    assert "import-config-file" in html


def test_topics_and_history_view_with_null_renders(client, monkeypatch):
    import os
    from nalarka.db.connection import get_connection
    from nalarka.db.repos.renders import create_render

    # Create topic
    t_res = client.post(
        "/api/topics",
        json={"title": "Topic Null Test", "language": "Indonesian", "max_duration": 60},
    )
    topic_id = t_res.json()["id"]

    # Insert a render record with None total_dur and None output_path
    db_path = os.environ.get("NALARKA_DB_PATH")
    conn = get_connection(db_path)
    create_render(
        conn=conn,
        topic_id=topic_id,
        trigger="full",
        output_path=None,
        total_dur=None,
        width=None,
        height=None,
    )
    conn.close()

    # Access /topics dashboard: should render without 500 error
    res_topics = client.get("/topics")
    assert res_topics.status_code == 200
    assert "Topic Null Test" in res_topics.text

    # Access /history view: should render without 500 error
    res_history = client.get("/history")
    assert res_history.status_code == 200
    assert f"Topic #{topic_id}" in res_history.text
