"""Router for renders, verifications, storyboards, and beats."""
import uuid
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Query, status
from fastapi.responses import FileResponse

from ..deps import get_db_conn, get_dispatcher
from ..schemas import (
    CreateRenderRequest, RerollBeatRequest, RenderResponse, JobAcceptedResponse,
)
from ..range_stream import range_stream_response
from ...db.repos.topics import get_topic
from ...db.repos.renders import (
    verification_summaries,
    create_render, get_render, list_renders_for_topic, list_all_renders,
    get_verification_for_render,
)
from ...db.repos.storyboards import (
    get_storyboard, get_current_storyboard, list_beats_for_storyboard,
    get_beat, list_revisions_for_beat,
)
from ...db.repos.jobs import create_job
from ...db.repos.settings import get_setting
from ...services.paths import media_roots, resolve_media_path, safe_output_name, is_within


def _checked_media(conn, raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return str(resolve_media_path(raw, media_roots([get_setting(conn, "library.dir", None)])))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid media path: {e}")

router = APIRouter(tags=["Renders & Storyboards"])


@router.post("/topics/{topic_id}/renders", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
def trigger_full_render(
    topic_id: int,
    req: CreateRenderRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
    dispatcher=Depends(get_dispatcher),
):
    topic = get_topic(conn, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found.")

    job_id = f"render-{uuid.uuid4().hex[:8]}"
    create_job(
        conn, job_id, kind="full_render", weight="heavy",
        params={
            "voiceover_id": req.voiceover_id,
            "vo_path": _checked_media(conn, req.vo_path),
            "ref_path": _checked_media(conn, req.ref_path),
            "orientation": req.orientation,
            "whisper_size": req.whisper_size,
            "synth_bgm": req.synth_bgm,
            "bgm_path": _checked_media(conn, req.bgm_path),
            "workers": req.workers,
            "output_name": safe_output_name(req.output_name),
            "storyboard_id": req.storyboard_id,
            "reuse_storyboard": req.reuse_storyboard,
        },
        topic_id=topic_id,
    )
    dispatcher.dispatch_next()
    return JobAcceptedResponse(job_id=job_id)


def enrich_renders(conn: sqlite3.Connection, rows: List[dict]) -> List[dict]:
    """Add what the UI needs to show a video card: is the file still there, did it pass verification."""
    summ = verification_summaries(conn, [r["id"] for r in rows])
    titles = {}
    for r in rows:
        tid = r.get("topic_id")
        if tid not in titles:
            t = get_topic(conn, tid) if tid else None
            titles[tid] = t["title"] if t else None
    out = []
    for r in rows:
        d = dict(r)
        p = d.get("output_path")
        d["video_available"] = bool(p) and Path(p).is_file()
        v = summ.get(d["id"])
        d["verification_passed"] = v["passed"] if v else None
        d["verification_total"] = v["total"] if v else None
        d["topic_title"] = titles.get(d.get("topic_id"))
        out.append(d)
    return out


@router.get("/renders", response_model=List[RenderResponse])
def get_renders_list(
    topic_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    rows = list_renders_for_topic(conn, topic_id) if topic_id else list_all_renders(conn, limit=limit)
    return enrich_renders(conn, rows)


@router.get("/renders/preview-image")
def get_preview_image(path: str = Query(...), conn: sqlite3.Connection = Depends(get_db_conn)):
    try:
        p = Path(path).resolve()
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="Preview image not found.")
    # Must be an existing png/jpg/jpeg/webp *inside* the data dir / library dir
    roots = media_roots([get_setting(conn, "library.dir", None)])
    if (not is_within(p, roots) or not p.is_file()
            or p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp")):
        raise HTTPException(status_code=404, detail="Preview image not found.")
    media_type = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(p, media_type=media_type)


@router.get("/renders/{render_id}", response_model=RenderResponse)
def get_render_by_id(render_id: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    r = get_render(conn, render_id)
    if not r:
        raise HTTPException(status_code=404, detail="Render not found.")
    return enrich_renders(conn, [r])[0]


@router.get("/renders/{render_id}/poster")
def get_render_poster(render_id: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    r = get_render(conn, render_id)
    p = Path(r["poster_path"]) if r and r.get("poster_path") else None
    if not p or not p.is_file() or not is_within(p, media_roots()):
        raise HTTPException(status_code=404, detail="Poster not available.")
    return FileResponse(p, media_type="image/jpeg")


@router.get("/renders/{render_id}/video")
def stream_rendered_video(
    render_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    r = get_render(conn, render_id)
    if not r or not r.get("output_path"):
        raise HTTPException(status_code=404, detail="Render not found or output path is missing.")
    p = Path(r["output_path"])
    return range_stream_response(p, request, content_type="video/mp4")


@router.get("/renders/{render_id}/download")
def download_rendered_video(
    render_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    r = get_render(conn, render_id)
    if not r or not r.get("output_path"):
        raise HTTPException(status_code=404, detail="Render not found.")
    p = Path(r["output_path"])
    if not p.exists():
        raise HTTPException(status_code=404, detail="Video file does not exist on disk.")
    return FileResponse(p, filename=p.name, media_type="video/mp4")


@router.post("/renders/{render_id}/verify", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
def verify_render_endpoint(
    render_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
    dispatcher=Depends(get_dispatcher),
):
    r = get_render(conn, render_id)
    if not r or not r.get("output_path"):
        raise HTTPException(status_code=404, detail="Render not found or output path missing.")
    job_id = f"verify-{uuid.uuid4().hex[:8]}"
    create_job(
        conn, job_id, kind="verify_mp4", weight="heavy",
        params={
            "mp4_path": r["output_path"],
            "expected_w": r.get("width", 1080),
            "expected_h": r.get("height", 1920),
            "expected_dur": r.get("total_dur"),
        },
        render_id=render_id,
        topic_id=r.get("topic_id"),
    )
    dispatcher.dispatch_next()
    return JobAcceptedResponse(job_id=job_id)


@router.get("/renders/{render_id}/verification")
def get_render_verification(
    render_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    v = get_verification_for_render(conn, render_id)
    if not v:
        raise HTTPException(status_code=404, detail="Verification results not found for this render.")
    return v


@router.get("/storyboards/{storyboard_id}")
def get_storyboard_by_id(storyboard_id: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    sb = get_storyboard(conn, storyboard_id)
    if not sb:
        raise HTTPException(status_code=404, detail="Storyboard not found.")
    return sb


@router.get("/topics/{topic_id}/storyboard/current")
def get_current_topic_storyboard(topic_id: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    sb = get_current_storyboard(conn, topic_id)
    if not sb:
        raise HTTPException(status_code=404, detail="No current storyboard found for this topic.")
    return sb


@router.get("/storyboards/{storyboard_id}/beats")
def get_storyboard_beats(storyboard_id: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    return list_beats_for_storyboard(conn, storyboard_id)


@router.post("/beats/{beat_id}/reroll", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
def reroll_beat_endpoint(
    beat_id: int,
    req: RerollBeatRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
    dispatcher=Depends(get_dispatcher),
):
    b = get_beat(conn, beat_id)
    if not b:
        raise HTTPException(status_code=404, detail="Beat not found.")
    job_id = f"reroll-{uuid.uuid4().hex[:8]}"
    create_job(
        conn, job_id, kind="reroll_beat", weight="heavy",
        params={
            "beat_id": beat_id,
            "custom_note": req.custom_note,
        },
    )
    dispatcher.dispatch_next()
    return JobAcceptedResponse(job_id=job_id)


@router.post("/beats/{beat_id}/preview", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
def preview_beat_endpoint(
    beat_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
    dispatcher=Depends(get_dispatcher),
):
    b = get_beat(conn, beat_id)
    if not b:
        raise HTTPException(status_code=404, detail="Beat not found.")
    job_id = f"preview-{uuid.uuid4().hex[:8]}"
    create_job(
        conn, job_id, kind="preview_beat", weight="heavy",
        params={"beat_id": beat_id},
    )
    dispatcher.dispatch_next()
    return JobAcceptedResponse(job_id=job_id)


@router.get("/beats/{beat_id}/revisions")
def get_beat_revisions_list(beat_id: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    return list_revisions_for_beat(conn, beat_id)

