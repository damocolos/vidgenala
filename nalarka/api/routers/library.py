"""Router for library items (references, BGM, uploaded audio)."""
import os
import uuid
import shutil
import sqlite3
from ...datadir import data_root
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, Query, status
from pydantic import BaseModel

from ...services.paths import safe_upload_name, save_upload
from ..deps import get_db_conn, get_dispatcher
from ..schemas import JobAcceptedResponse
from ..range_stream import range_stream_response
from ...db.repos.library import (
    upsert_library_item, get_library_item, list_library_items,
    delete_library_item, sync_library_folder, ALLOWED_EXTENSIONS,
)
from ...db.repos.settings import set_setting, get_setting
from ...db.repos.jobs import create_job
from ...core.reference import analyze_reference_video

router = APIRouter(tags=["Library"])


class SetFolderRequest(BaseModel):
    folder_path: str


@router.get("/library")
def get_library(
    kind: Optional[str] = Query(None),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    return list_library_items(conn, kind=kind)


@router.post("/library/upload", status_code=status.HTTP_201_CREATED)
def upload_library_file(
    kind: str = Query("reference", pattern=r"^(reference|bgm|voiceover_upload)$"),
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    suffix = Path(file.filename or "").suffix.lower()
    allowed = ALLOWED_EXTENSIONS.get(kind, set())
    if suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type for {kind}. Allowed: {', '.join(sorted(allowed))}",
        )

    dest_dir = data_root() / "library" / kind
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_upload_name(file.filename)
    dest_path = dest_dir / filename

    try:
        save_upload(file.file, dest_path)
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))

    media_type = "video" if suffix in (".mp4", ".mov", ".mkv", ".webm") else (
        "audio" if suffix in (".mp3", ".wav", ".m4a", ".ogg") else "image"
    )
    size = dest_path.stat().st_size
    w, h, profile = 1080, 1920, None

    if kind == "reference" and media_type == "video":
        try:
            profile = analyze_reference_video(str(dest_path), lambda m: None)
            w, h = profile.get("width", 1080), profile.get("height", 1920)
        except Exception:
            pass

    item_id = upsert_library_item(
        conn,
        kind=kind,
        filename=filename,
        file_path=str(dest_path),
        media_type=media_type,
        size_bytes=size,
        width=w,
        height=h,
        profile=profile,
        origin="upload",
    )

    return {"status": "ok", "item_id": item_id, "item": get_library_item(conn, item_id)}


@router.post("/library/scan", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
def scan_library_endpoint(
    conn: sqlite3.Connection = Depends(get_db_conn),
    dispatcher=Depends(get_dispatcher),
):
    folder_str = get_setting(conn, "library.dir", "references_library")
    job_id = f"scan-{uuid.uuid4().hex[:8]}"
    create_job(conn, job_id, kind="scan_library", weight="light", params={"folder": folder_str})
    dispatcher.dispatch_next()
    return JobAcceptedResponse(job_id=job_id)


@router.put("/library/folder")
def set_library_folder(
    req: SetFolderRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    p = Path(req.folder_path)
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail="Folder path does not exist or is not a directory.")
    set_setting(conn, "library.dir", str(p))
    return {"status": "ok", "library_dir": str(p)}


@router.delete("/library/{item_id}")
def delete_library_item_endpoint(
    item_id: int,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    item = get_library_item(conn, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Library item not found.")

    if item.get("origin") == "upload":
        p = Path(item["file_path"])
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    delete_library_item(conn, item_id)
    return {"status": "ok"}


@router.get("/library/{item_id}/file")
def stream_library_file(
    item_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    item = get_library_item(conn, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Library item not found.")
    p = Path(item["file_path"])
    media_type = "video/mp4" if p.suffix.lower() == ".mp4" else (
        "audio/mpeg" if p.suffix.lower() == ".mp3" else "application/octet-stream"
    )
    return range_stream_response(p, request, content_type=media_type)
