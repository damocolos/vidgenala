"""Router for voiceover generation, upload, and audio streaming."""
import uuid
import math
import shutil
import sqlite3
from ...datadir import data_root
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, status

from ...services.paths import save_upload
from ..deps import get_db_conn, get_dispatcher
from ..schemas import GenerateTTSRequest, JobAcceptedResponse
from ..range_stream import range_stream_response
from ...db.repos.topics import get_topic
from ...db.repos.scripts import get_current_script
from ...db.repos.voiceovers import create_voiceover, get_voiceover
from ...db.repos.jobs import create_job
from ...core.audio import get_audio_duration
from ...core.constants import INTRO_OUTRO_SECONDS

router = APIRouter(tags=["Voiceovers"])


@router.post("/topics/{topic_id}/voiceovers/generate", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
def generate_voiceover_endpoint(
    topic_id: int,
    req: GenerateTTSRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
    dispatcher=Depends(get_dispatcher),
):
    topic = get_topic(conn, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found.")

    script_text = req.script_text
    if not script_text:
        cur_sc = get_current_script(conn, topic_id)
        if not cur_sc:
            raise HTTPException(status_code=400, detail="Topic has no script to generate voiceover from.")
        script_text = cur_sc["raw_text"]

    job_id = f"tts-{uuid.uuid4().hex[:8]}"
    create_job(
        conn, job_id, kind="generate_tts", weight="heavy",
        params={
            "script_text": script_text,
            "provider": req.provider,
            "voice": req.voice,
            "language": req.language or topic["language"],
            "max_duration": req.max_duration or topic["max_duration"],
        },
        topic_id=topic_id,
    )
    dispatcher.dispatch_next()
    return JobAcceptedResponse(job_id=job_id)


@router.post("/topics/{topic_id}/voiceovers/upload", status_code=status.HTTP_201_CREATED)
def upload_voiceover_endpoint(
    topic_id: int,
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    topic = get_topic(conn, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found.")

    suffix = Path(file.filename or "audio.mp3").suffix.lower()
    if suffix not in (".mp3", ".wav", ".m4a", ".ogg"):
        raise HTTPException(status_code=400, detail="Invalid audio format. Allowed: .mp3, .wav, .m4a, .ogg")

    dest_dir = data_root() / "audio"
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved_name = f"vo_{topic_id}_{uuid.uuid4().hex[:6]}{suffix}"
    dest_path = dest_dir / saved_name

    try:
        save_upload(file.file, dest_path)
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))

    dur = get_audio_duration(str(dest_path), lambda m: None)
    total_dur = math.ceil(dur + INTRO_OUTRO_SECONDS)

    vid = create_voiceover(
        conn,
        topic_id=topic_id,
        file_path=str(dest_path),
        origin="uploaded",
        duration_sec=dur,
        total_dur_locked=total_dur,
        script_fingerprint=None,  # uploaded audio skips fingerprint guard
    )

    return {
        "voiceover_id": vid,
        "file_path": str(dest_path),
        "duration": dur,
        "total_video_duration": total_dur,
    }


@router.get("/voiceovers/{voiceover_id}/audio")
def stream_voiceover_audio(
    voiceover_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    vo = get_voiceover(conn, voiceover_id)
    if not vo:
        raise HTTPException(status_code=404, detail="Voiceover not found.")

    p = Path(vo["file_path"])
    content_type = "audio/mpeg" if p.suffix.lower() == ".mp3" else "audio/wav"
    return range_stream_response(p, request, content_type=content_type)
