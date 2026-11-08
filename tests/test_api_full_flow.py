"""End-to-end API integration flow testing Topic creation, Voiceover upload, and Storyboard inspection."""
import json
import sqlite3
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from nalarka.api.main import create_app
from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults
from nalarka.services.plan_assembler import save_plan
from nalarka.db.repos.renders import create_render, create_verification, update_render_status


@pytest.fixture
def client_and_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_flow.db"
    conn = get_connection(str(test_db))
    run_migrations(conn)
    seed_defaults(conn)
    conn.close()

    monkeypatch.setenv("NALARKA_DB_PATH", str(test_db))
    app = create_app()
    with TestClient(app) as tc:
        yield tc, str(test_db)


def test_complete_web_api_flow(client_and_db, tmp_path):
    client, db_path = client_and_db

    # 1. Create a new topic
    t_res = client.post(
        "/api/topics",
        json={
            "title": "Misteri Dasar Samudera Mariana",
            "language": "Indonesian",
            "max_duration": 60,
            "notes": "E2E testing topic",
        },
    )
    assert t_res.status_code == 201
    topic = t_res.json()
    topic_id = topic["id"]
    assert topic["title"] == "Misteri Dasar Samudera Mariana"

    # 2. Save a structured script
    script_raw = (
        "[Hook] Palung Mariana adalah titik terdalam di muka bumi. Apa yang ada di bawah sana?\n\n"
        "[Inti 1] Di kedalaman sebelas kilometer, tekanannya mencapai ribuan kali lipat atmosfer.\n\n"
        "[Penutup] Namun kehidupan aneh tetap mampu bertahan di kegelapan abadi."
    )
    s_res = client.post(
        f"/api/topics/{topic_id}/scripts",
        json={"raw_text": script_raw, "max_duration": 60},
    )
    assert s_res.status_code == 201
    script = s_res.json()
    assert script["word_count"] > 10

    # 3. Test script estimate endpoint
    est_res = client.post(
        "/api/scripts/estimate",
        json={"raw_text": script_raw, "max_duration": 60},
    )
    assert est_res.status_code == 200
    est = est_res.json()
    assert est["word_count"] == script["word_count"]
    assert est["over_cap"] is False

    # 4. Upload Voiceover
    vo_fixture = Path("tests/fixtures/sample_vo.wav")
    assert vo_fixture.exists()
    with open(vo_fixture, "rb") as f:
        vo_res = client.post(
            f"/api/topics/{topic_id}/voiceovers/upload",
            files={"file": ("sample_vo.wav", f, "audio/wav")},
        )
    assert vo_res.status_code == 201
    vo_data = vo_res.json()
    assert vo_data["voiceover_id"] > 0
    assert vo_data["duration"] > 0

    # 5. Populate a test storyboard using plan_assembler and test beats inspection
    with open("tests/fixtures/sample_plan.json", encoding="utf-8") as f:
        plan = json.load(f)
    with open("tests/fixtures/sample_scene_code.py", encoding="utf-8") as f:
        code = f.read()
    with open("tests/fixtures/sample_words.json", encoding="utf-8") as f:
        words = json.load(f)

    from nalarka.core.scene_code import split_scene_code
    helpers, beats_code = split_scene_code(code, lambda m: None)
    plan["scene_sources"] = {"helpers": helpers, "beats": beats_code}

    conn = get_connection(db_path)
    sb_id = save_plan(
        conn=conn,
        topic_id=topic_id,
        plan=plan,
        script_id=script["id"],
        voiceover_id=vo_data["voiceover_id"],
    )

    # Record a completed render and verification in DB
    dummy_mp4 = tmp_path / "dummy_out.mp4"
    dummy_mp4.write_bytes(b"dummy mp4 video bytes")
    render_id = create_render(
        conn=conn,
        topic_id=topic_id,
        storyboard_id=sb_id,
        voiceover_id=vo_data["voiceover_id"],
        output_path=str(dummy_mp4),
        total_dur=15,
        width=1080,
        height=1920,
    )
    update_render_status(conn, render_id, "succeeded")
    create_verification(
        conn=conn,
        render_id=render_id,
        passed=7,
        total=7,
        duration_sec=15.0,
        frames=450,
        results=[{"name": f"check_{i}", "passed": True} for i in range(1, 8)],
    )
    conn.close()

    # 6. Verify Storyboard API
    sb_res = client.get(f"/api/topics/{topic_id}/storyboard/current")
    assert sb_res.status_code == 200
    assert sb_res.json()["id"] == sb_id

    # 7. Verify Beats list API
    beats_res = client.get(f"/api/storyboards/{sb_id}/beats")
    assert beats_res.status_code == 200
    beats = beats_res.json()
    assert len(beats) > 0

    # 8. Verify Verification API
    v_res = client.get(f"/api/renders/{render_id}/verification")
    assert v_res.status_code == 200
    assert v_res.json()["passed"] == 7

    # 9. Verify Web UI studio view renders with populated beats and renders
    studio_res = client.get(f"/topics/{topic_id}")
    assert studio_res.status_code == 200
    assert "Misteri Dasar Samudera Mariana" in studio_res.text
    assert f"Beat #01" in studio_res.text
