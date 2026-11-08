"""Tests for FastAPI REST API endpoints (Fase 5)."""
import os
import pytest
from pathlib import Path
from httpx import AsyncClient, ASGITransport

from nalarka.api.main import create_app
from nalarka.api.deps import set_dispatcher
from nalarka.jobs.dispatcher import JobDispatcher
from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def app_with_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_api.db"
    monkeypatch.setenv("NALARKA_DB_PATH", str(test_db))

    conn = get_connection(test_db)
    run_migrations(conn)
    seed_defaults(conn)
    conn.close()

    dispatcher = JobDispatcher(db_path=str(test_db))
    set_dispatcher(dispatcher)

    app = create_app()
    yield app
    dispatcher.stop()


@pytest.mark.anyio
async def test_healthz(app_with_db):
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["database"] is True
        assert data["ffmpeg"] is True
        assert data["ffprobe"] is True
        assert data["status"] == "ok"


@pytest.mark.anyio
async def test_config_and_duration_hint(app_with_db):
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET config
        resp = await client.get("/api/config")
        assert resp.status_code == 200
        cfg = resp.json()
        assert "render.max_duration" in cfg

        # PUT config
        resp = await client.put("/api/config", json={"settings": {"render.max_duration": 45}})
        assert resp.status_code == 200

        # Verify updated
        resp = await client.get("/api/config")
        assert resp.json()["render.max_duration"] == 45

        # Duration hint
        resp = await client.get("/api/duration-hint?d=60")
        assert resp.status_code == 200
        hint = resp.json()
        assert hint["max_duration"] == 60
        assert hint["word_budget_lo"] > 0
        assert hint["recommended_sections"] >= 3


@pytest.mark.anyio
async def test_topics_crud(app_with_db):
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create
        create_resp = await client.post(
            "/api/topics",
            json={"title": "Misteri Laut Dalam", "language": "Indonesian", "max_duration": 60},
        )
        assert create_resp.status_code == 201
        topic = create_resp.json()
        tid = topic["id"]
        assert topic["title"] == "Misteri Laut Dalam"

        # Read
        get_resp = await client.get(f"/api/topics/{tid}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == tid

        # Patch
        patch_resp = await client.patch(
            f"/api/topics/{tid}",
            json={"title": "Misteri Laut Dalam (Update)", "max_duration": 90},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["title"] == "Misteri Laut Dalam (Update)"
        assert patch_resp.json()["max_duration"] == 90

        # Archive (Delete)
        del_resp = await client.delete(f"/api/topics/{tid}")
        assert del_resp.status_code == 200

        # List active topics does not include archived
        list_resp = await client.get("/api/topics")
        assert tid not in [t["id"] for t in list_resp.json()]


@pytest.mark.anyio
async def test_scripts_estimate_and_manual(app_with_db):
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Estimate
        raw = "[Hook]\nKenapa langit itu biru?\n[Inti 1]\nKarena hamburan cahaya matahari.\n[Penutup]\nItulah sains!"
        est_resp = await client.post("/api/scripts/estimate", json={"raw_text": raw, "max_duration": 60})
        assert est_resp.status_code == 200
        est = est_resp.json()
        assert est["word_count"] > 0
        assert est["over_cap"] is False
        assert est["sections_count"] == 3

        # Create topic & save manual script
        top_resp = await client.post("/api/topics", json={"title": "Langit Biru", "language": "Indonesian", "max_duration": 60})
        tid = top_resp.json()["id"]

        sc_resp = await client.post(f"/api/topics/{tid}/scripts", json={"raw_text": raw, "max_duration": 60})
        assert sc_resp.status_code == 201
        sc = sc_resp.json()
        assert sc["version"] == 1
        assert len(sc["sections"]) == 3

        # Clean text endpoint
        clean_resp = await client.get(f"/api/scripts/{sc['id']}/clean-text")
        assert clean_resp.status_code == 200
        assert "[Hook]" not in clean_resp.json()["clean_text"]
        assert "Kenapa langit itu biru?" in clean_resp.json()["clean_text"]


@pytest.mark.anyio
async def test_voiceover_upload_and_range_streaming(app_with_db):
    fixtures_dir = Path(__file__).parent / "fixtures"
    sample_vo = fixtures_dir / "sample_vo.wav"

    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create topic
        top_resp = await client.post("/api/topics", json={"title": "VO Test Topic", "language": "Indonesian", "max_duration": 60})
        tid = top_resp.json()["id"]

        # Upload voiceover
        files = {"file": ("test_vo.wav", sample_vo.read_bytes(), "audio/wav")}
        up_resp = await client.post(f"/api/topics/{tid}/voiceovers/upload", files=files)
        assert up_resp.status_code == 201
        vo_data = up_resp.json()
        vid = vo_data["voiceover_id"]
        assert vo_data["duration"] > 10.0

        # Full audio stream
        audio_full = await client.get(f"/api/voiceovers/{vid}/audio")
        assert audio_full.status_code == 200
        assert audio_full.headers.get("accept-ranges") == "bytes"
        assert len(audio_full.content) > 100000

        # Partial Range stream (HTTP 206)
        headers = {"Range": "bytes=0-99"}
        audio_part = await client.get(f"/api/voiceovers/{vid}/audio", headers=headers)
        assert audio_part.status_code == 206
        assert len(audio_part.content) == 100
        assert "bytes 0-99/" in audio_part.headers.get("content-range")
