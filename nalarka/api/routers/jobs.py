"""Router for jobs and Server-Sent Events (SSE)."""
import sqlite3
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request, status
from fastapi.responses import StreamingResponse

from ..deps import get_db_conn, get_dispatcher
from ..schemas import JobResponse, ResumeRequest
import uuid
from ...db.repos.jobs import (
    get_job, list_jobs, list_active_jobs, get_job_events, create_job, has_active_resume,
)
from ...db.repos import llm_calls as llm_repo
from ...jobs.sse import stream_job_events
from ...db.connection import get_connection

router = APIRouter(tags=["Jobs & SSE"])


@router.get("/jobs", response_model=List[JobResponse])
def get_jobs_list(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    topic_id: Optional[int] = Query(None),
    kind: Optional[str] = Query(None),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    if status == "active":
        return list_active_jobs(conn)
    return list_jobs(conn, status=status, limit=limit, topic_id=topic_id, kind=kind)


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


@router.get("/jobs/{job_id}/log")
def get_job_log(
    job_id: str,
    after_id: int = Query(0, ge=0),
    limit: int = Query(2000, ge=1, le=5000),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    """Persisted log of a job (same lines the backend prints), so the UI can show it after a reload."""
    j = get_job(conn, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Job not found.")
    events = get_job_events(conn, job_id, since_id=after_id, limit=limit)
    return {
        "job_id": job_id, "kind": j["kind"], "status": j["status"], "progress": j["progress"],
        "status_text": j.get("status_text"), "error": j.get("error"),
        "last_id": events[-1]["id"] if events else after_id,
        "events": [e for e in events if e["kind"] in ("log", "progress")],
    }


@router.post("/jobs/{job_id}/resume", status_code=status.HTTP_202_ACCEPTED)
def resume_job(
    job_id: str,
    req: Optional[ResumeRequest] = None,
    conn: sqlite3.Connection = Depends(get_db_conn),
    dispatcher=Depends(get_dispatcher),
):
    """Re-run a failed/canceled job. AI responses it already received are replayed from the
    ledger (no second charge); only the missing/failed calls are sent to the provider."""
    j = get_job(conn, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Job not found.")
    if j["status"] not in ("failed", "canceled"):
        raise HTTPException(status_code=409, detail=f"Only failed or canceled jobs can be resumed (status: {j['status']}).")
    if has_active_resume(conn, job_id):
        raise HTTPException(status_code=409, detail="This job is already being resumed.")
    params = dict(j["params"])
    params["resume_of"] = job_id
    if req is not None:
        # Smaller code batches: the storyboard plan still replays; code requests are asked again, shorter.
        if req.batch_beats:
            params["batch_beats"] = req.batch_beats
        if req.parallel_calls:
            params["parallel_calls"] = req.parallel_calls
    new_id = f"resume-{uuid.uuid4().hex[:8]}"
    create_job(conn, new_id, kind=j["kind"], weight=j["weight"], params=params, topic_id=j.get("topic_id"))
    dispatcher.dispatch_next()
    return {"job_id": new_id, "resume_of": job_id}


@router.get("/jobs/{job_id}/llm-calls")
def get_job_llm_calls(job_id: str, conn: sqlite3.Connection = Depends(get_db_conn)):
    if not get_job(conn, job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"summary": llm_repo.summary(conn, job_id=job_id),
            "calls": list(reversed(llm_repo.list_calls(conn, job_id=job_id, limit=500)))}


@router.get("/llm-calls")
def get_llm_calls(
    topic_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    return {"summary": llm_repo.summary(conn, topic_id=topic_id),
            "calls": llm_repo.list_calls(conn, topic_id=topic_id, limit=limit)}


@router.get("/llm-calls/{call_id}")
def get_llm_call_detail(call_id: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    c = llm_repo.get_call(conn, call_id)
    if not c:
        raise HTTPException(status_code=404, detail="AI call not found.")
    return c
