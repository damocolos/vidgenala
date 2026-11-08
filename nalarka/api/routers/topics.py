"""Router for topics and scripts."""
import uuid
import sqlite3
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..deps import get_db_conn, get_dispatcher
from ..schemas import (
    TopicResponse, CreateTopicRequest, UpdateTopicRequest,
    ScriptResponse, SaveManualScriptRequest, EstimateScriptRequest,
    JobAcceptedResponse,
)
from ...db.repos.topics import (
    create_topic, get_topic, update_topic, archive_topic, delete_topic, list_topics,
)
from ...db.repos.scripts import (
    create_script, get_script, get_current_script, list_scripts_for_topic,
)
from ...db.repos.jobs import create_job
from ...core.script import estimate_video_seconds, script_word_budget
from ...core.scripttext import parse_tagged_script

router = APIRouter(tags=["Topics & Scripts"])


@router.get("/topics", response_model=List[TopicResponse])
def get_all_topics(
    include_archived: bool = Query(False),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    return list_topics(conn, include_archived=include_archived)


@router.post("/topics", response_model=TopicResponse, status_code=status.HTTP_201_CREATED)
def create_new_topic(
    req: CreateTopicRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    tid = create_topic(conn, title=req.title, language=req.language, max_duration=req.max_duration, notes=req.notes)
    return get_topic(conn, tid)


@router.get("/topics/{topic_id}", response_model=TopicResponse)
def get_topic_by_id(topic_id: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    topic = get_topic(conn, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found.")
    return topic


@router.patch("/topics/{topic_id}", response_model=TopicResponse)
def update_topic_by_id(
    topic_id: int,
    req: UpdateTopicRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    res = update_topic(
        conn, topic_id, title=req.title, language=req.language,
        max_duration=req.max_duration, notes=req.notes,
    )
    if not res:
        raise HTTPException(status_code=404, detail="Topic not found.")
    return res


@router.delete("/topics/{topic_id}")
def delete_topic_by_id(
    topic_id: int,
    hard: bool = Query(False),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    if hard:
        ok = delete_topic(conn, topic_id)
    else:
        ok = archive_topic(conn, topic_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Topic not found or already archived.")
    return {"status": "ok"}


@router.post("/topics/{topic_id}/scripts/generate", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
def generate_script_endpoint(
    topic_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
    dispatcher=Depends(get_dispatcher),
):
    topic = get_topic(conn, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found.")
    job_id = f"gen-script-{uuid.uuid4().hex[:8]}"
    create_job(conn, job_id, kind="generate_script", weight="light", params={}, topic_id=topic_id)
    dispatcher.dispatch_next()
    return JobAcceptedResponse(job_id=job_id)


@router.post("/topics/{topic_id}/scripts", response_model=ScriptResponse, status_code=status.HTTP_201_CREATED)
def save_manual_script(
    topic_id: int,
    req: SaveManualScriptRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    topic = get_topic(conn, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found.")
    max_dur = req.max_duration or topic["max_duration"]
    s_row = create_script(conn, topic_id, req.raw_text, source="manual", max_duration=max_dur, is_current=True)
    return s_row


@router.get("/topics/{topic_id}/scripts", response_model=List[ScriptResponse])
def get_topic_scripts(topic_id: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    return list_scripts_for_topic(conn, topic_id)


@router.get("/scripts/{script_id}", response_model=ScriptResponse)
def get_script_by_id(script_id: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    s = get_script(conn, script_id)
    if not s:
        raise HTTPException(status_code=404, detail="Script not found.")
    return s


@router.post("/scripts/estimate")
def estimate_script(req: EstimateScriptRequest) -> Dict[str, Any]:
    clean_text, sections = parse_tagged_script(req.raw_text)
    words = len(clean_text.split())
    est_vo, est_vid = estimate_video_seconds(words)
    b_lo, b_hi = script_word_budget(req.max_duration)
    return {
        "word_count": words,
        "est_voice_seconds": est_vo,
        "est_video_seconds": est_vid,
        "word_budget_lo": b_lo,
        "word_budget_hi": b_hi,
        "over_cap": est_vid > req.max_duration,
        "sections_count": len(sections),
    }


@router.get("/scripts/{script_id}/clean-text")
def get_script_clean_text(script_id: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    s = get_script(conn, script_id)
    if not s:
        raise HTTPException(status_code=404, detail="Script not found.")
    return {"clean_text": s["clean_text"], "raw_text": s["raw_text"]}
