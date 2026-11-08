"""Tests for Job System, Dispatcher, Runner subprocess, and SSE streaming (Fase 4)."""
import os
import time
import pytest
import asyncio
from pathlib import Path

from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults
from nalarka.db.repos.jobs import create_job, get_job, get_job_events, update_job_status
from nalarka.db.repos.topics import create_topic
from nalarka.jobs.jobrunner import run_job
from nalarka.jobs.dispatcher import JobDispatcher
from nalarka.jobs.sse import format_sse_event, stream_job_events


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "test_jobs.db"
    conn = get_connection(p)
    run_migrations(conn)
    seed_defaults(conn)
    conn.close()
    return p


def test_jobrunner_scan_library(db_path, tmp_path):
    lib_dir = tmp_path / "test_lib"
    lib_dir.mkdir()
    (lib_dir / "sample_img.png").write_bytes(b"dummy")

    conn = get_connection(db_path)
    jid = "job-scan-1"
    create_job(conn, jid, kind="scan_library", weight="light", params={"folder": str(lib_dir)})
    conn.close()

    # Execute job
    code = run_job(jid, str(db_path))
    assert code == 0

    conn = get_connection(db_path)
    job = get_job(conn, jid)
    assert job["status"] == "succeeded"
    assert job["progress"] == 100
    assert job["result"]["scanned"] == 1

    events = get_job_events(conn, jid)
    assert len(events) >= 1
    conn.close()


def test_dispatcher_concurrency_rules(db_path):
    conn = get_connection(db_path)
    t1 = create_topic(conn, "Topic 1")
    t2 = create_topic(conn, "Topic 2")

    dispatcher = JobDispatcher(db_path=str(db_path), max_heavy=1, max_light=2)

    # Job A (heavy on Topic 1)
    j_a = {"kind": "full_render", "topic_id": t1, "status": "running"}
    # Job B (heavy on Topic 2)
    j_b = {"kind": "full_render", "topic_id": t2, "status": "queued"}
    # Job C (heavy on Topic 1)
    j_c = {"kind": "full_render", "topic_id": t1, "status": "queued"}
    # Job D (light on Topic 1)
    j_d = {"kind": "scan_library", "topic_id": t1, "status": "queued"}

    active = [j_a]
    # Cannot start heavy job B because heavy limit (1) is reached
    assert not dispatcher.can_start_job(j_b, active)

    # Cannot start heavy job C because heavy limit is reached AND topic 1 is locked
    assert not dispatcher.can_start_job(j_c, active)

    # Light job D can run because light slots are available
    assert dispatcher.can_start_job(j_d, active)

    conn.close()


def test_dispatcher_recovery_orphaned_jobs(db_path):
    conn = get_connection(db_path)
    jid = "orphaned-job-999"
    create_job(conn, jid, kind="scan_library", weight="light", params={})
    # Mark as running with nonexistent dead PID
    update_job_status(conn, jid, status="running", pid=9999999)
    conn.close()

    dispatcher = JobDispatcher(db_path=str(db_path))
    dispatcher.recover_orphaned_jobs()

    conn = get_connection(db_path)
    job = get_job(conn, jid)
    assert job["status"] == "failed"
    assert "interrupted or terminated" in job["error"]
    conn.close()


def test_dispatcher_cancellation(db_path, tmp_path):
    conn = get_connection(db_path)
    jid = "job-cancel-test"
    lib_dir = tmp_path / "lib_cancel"
    lib_dir.mkdir()
    create_job(conn, jid, kind="scan_library", weight="light", params={"folder": str(lib_dir)})
    conn.close()

    dispatcher = JobDispatcher(db_path=str(db_path))
    # Cancel while in queued state
    assert dispatcher.cancel_job(jid) is True

    conn = get_connection(db_path)
    job = get_job(conn, jid)
    assert job["status"] == "canceled"
    conn.close()


@pytest.mark.anyio
async def test_sse_event_streaming(db_path, tmp_path):
    conn = get_connection(db_path)
    jid = "job-sse-stream"
    lib_dir = tmp_path / "lib_sse"
    lib_dir.mkdir()
    (lib_dir / "test.png").write_bytes(b"dummy")
    create_job(conn, jid, kind="scan_library", weight="light", params={"folder": str(lib_dir)})
    conn.close()

    # Run job synchronously in another thread
    import threading
    t = threading.Thread(target=run_job, args=(jid, str(db_path)))
    t.start()

    received_lines = []
    async for chunk in stream_job_events(lambda: get_connection(db_path), jid, poll_interval=0.1):
        received_lines.append(chunk)

    t.join(timeout=5.0)

    full_stream = "".join(received_lines)
    assert "event: log" in full_stream or "event: result" in full_stream or "event: done" in full_stream
    assert "id: " in full_stream
    assert "data: " in full_stream
