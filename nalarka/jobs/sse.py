"""Server-Sent Events (SSE) stream formatting and generators."""
import json
import time
import anyio
import anyio.to_thread
import sqlite3
from typing import AsyncGenerator, Optional, Dict, Any
from ..db.repos.jobs import get_job, get_job_events


def format_sse_event(event_id: int, event_kind: str, data: dict) -> str:
    """Formats payload into standard SSE wire format."""
    payload_str = json.dumps(data, ensure_ascii=False)
    return f"id: {event_id}\nevent: {event_kind}\ndata: {payload_str}\n\n"


def _poll_db(db_factory, job_id: str, since_id: int):
    """Blocking SQLite read; runs in a worker thread so the event loop stays responsive."""
    conn = db_factory()
    try:
        job = get_job(conn, job_id)
        events = get_job_events(conn, job_id, since_id=since_id, limit=200) if job else []
        return job, events
    finally:
        try:
            conn.close()
        except Exception:
            pass


async def stream_job_events(
    db_factory,
    job_id: str,
    last_event_id: int = 0,
    poll_interval: float = 0.25,
    max_wait_seconds: float = 7200.0,
) -> AsyncGenerator[str, None]:
    """
    Streams SSE events for a job until the job reaches a terminal status
    ('succeeded', 'failed', 'canceled') and all events have been flushed.
    """
    current_id = last_event_id
    start_time = time.time()

    while True:
        job, events = await anyio.to_thread.run_sync(_poll_db, db_factory, job_id, current_id)
        if not job:
            yield format_sse_event(current_id + 1, "error", {"message": f"Job {job_id} not found."})
            break

        for ev in events:
            current_id = ev["id"]
            ev_kind = ev["kind"]
            if ev_kind == "log":
                data = {"ts": ev["ts"], "message": ev["message"]}
            elif ev_kind == "progress":
                data = {"pct": ev["pct"], "status": ev["message"]}
            elif ev_kind == "result":
                data = json.loads(ev["message"]) if ev["message"] else {}
            else:
                data = {"message": ev["message"]}

            yield format_sse_event(current_id, ev_kind, data)

        is_terminal = job["status"] in ("succeeded", "failed", "canceled")
        if is_terminal and not events:
            final_data = {
                "status": job["status"],
                "result": job.get("result"),
                "error": job.get("error"),
            }
            yield format_sse_event(current_id + 1, "done", final_data)
            break

        if time.time() - start_time > max_wait_seconds:
            yield format_sse_event(current_id + 1, "error", {"message": "Stream timeout reached."})
            break

        await anyio.sleep(poll_interval)
