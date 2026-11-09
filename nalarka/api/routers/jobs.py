"""Router for jobs and Server-Sent Events (SSE)."""
import sqlite3
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request, status
from fastapi.responses import StreamingResponse

from ..deps import get_db_conn, get_dispatcher
from ..schemas import JobResponse
from ...db.repos.jobs import (
    get_job, list_jobs, list_active_jobs,
)
from ...jobs.sse import stream_job_events
from ...db.connection import get_connection

router = APIRouter(tags=["Jobs & SSE"])


@router.get("/jobs", response_model=List[JobResponse])
def get_jobs_list(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    if status == "active":
        return list_active_jobs(conn)
    return list_jobs(conn, status=status, limit=limit)


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job_by_id(job_id: str, conn: sqlite3.Connection = Depends(get_db_conn)):
    j = get_job(conn, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Job not found.")
    return j


@router.get("/jobs/{job_id}/events")
async def get_job_event_stream(
    job_id: str,
    request: Request,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    j = get_job(conn, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Job not found.")

    since_id = 0
    if last_event_id and last_event_id.isdigit():
        since_id = int(last_event_id)

    db_path = conn.execute("PRAGMA database_list;").fetchone()[2]

    generator = stream_job_events(
        db_factory=lambda: get_connection(db_path),
        job_id=job_id,
        last_event_id=since_id,
        poll_interval=0.25,
    )

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(generator, media_type="text/event-stream", headers=headers)


@router.post("/jobs/{job_id}/cancel")
def cancel_job_endpoint(
    job_id: str,
    conn: sqlite3.Connection = Depends(get_db_conn),
    dispatcher=Depends(get_dispatcher),
):
    j = get_job(conn, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Job not found.")
    ok = dispatcher.cancel_job(job_id)
    return {"status": "ok", "canceled": ok, "job_status": (get_job(conn, job_id) or {}).get("status")}
