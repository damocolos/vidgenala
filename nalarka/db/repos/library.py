"""Repository for reference library, background music, and audio assets."""
import os
import json
import sqlite3
from pathlib import Path
from typing import List, Optional, Dict, Any

from ...core.reference import analyze_reference_video

ALLOWED_EXTENSIONS = {
    "reference": {".mp4", ".mov", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".webp"},
    "bgm": {".mp3", ".wav", ".m4a", ".ogg"},
    "voiceover_upload": {".mp3", ".wav", ".m4a", ".ogg"},
}


def upsert_library_item(
    conn: sqlite3.Connection,
    kind: str,
    filename: str,
    file_path: str,
    media_type: str,
    size_bytes: Optional[int] = None,
    sha256: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    profile: Optional[dict] = None,
    origin: str = "upload",
) -> int:
    profile_str = json.dumps(profile, ensure_ascii=False) if profile else None
    with conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO library_items (
                kind, filename, file_path, media_type, size_bytes, sha256,
                width, height, profile_json, origin, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(kind, filename) DO UPDATE SET
                file_path = excluded.file_path,
                media_type = excluded.media_type,
                size_bytes = excluded.size_bytes,
                sha256 = excluded.sha256,
                width = excluded.width,
                height = excluded.height,
                profile_json = excluded.profile_json,
                origin = excluded.origin
            """,
            (
                kind, filename, file_path, media_type, size_bytes, sha256,
                width, height, profile_str, origin,
            ),
        )
        if cur.lastrowid:
            return cur.lastrowid
        cur.execute("SELECT id FROM library_items WHERE kind = ? AND filename = ?", (kind, filename))
        return cur.fetchone()[0]


def get_library_item(conn: sqlite3.Connection, item_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM library_items WHERE id = ?", (item_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["profile"] = json.loads(d["profile_json"]) if d["profile_json"] else None
    return d


def get_library_item_by_kind_and_filename(conn: sqlite3.Connection, kind: str, filename: str) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM library_items WHERE kind = ? AND filename = ?", (kind, filename))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["profile"] = json.loads(d["profile_json"]) if d["profile_json"] else None
    return d


def list_library_items(conn: sqlite3.Connection, kind: Optional[str] = None) -> List[dict]:
    cur = conn.cursor()
    if kind:
        cur.execute("SELECT * FROM library_items WHERE kind = ? ORDER BY filename ASC", (kind,))
    else:
        cur.execute("SELECT * FROM library_items ORDER BY kind, filename ASC")
    items = []
    for r in cur.fetchall():
        d = dict(r)
        d["profile"] = json.loads(d["profile_json"]) if d["profile_json"] else None
        items.append(d)
    return items


def delete_library_item(conn: sqlite3.Connection, item_id: int) -> bool:
    with conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM library_items WHERE id = ?", (item_id,))
        return cur.rowcount > 0


def sync_library_folder(conn: sqlite3.Connection, folder_path: Path, log_fn=print) -> dict:
    """Scans reference directory on disk and syncs with library_items (kind='reference')."""
    if not folder_path.exists() or not folder_path.is_dir():
        return {"scanned": 0, "added": 0}

    ref_exts = ALLOWED_EXTENSIONS["reference"]
    disk_files = {p.name: p for p in folder_path.iterdir() if p.suffix.lower() in ref_exts and p.is_file()}

    added = 0
    for name, path in disk_files.items():
        media_type = "video" if path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm") else "image"
        size = path.stat().st_size
        profile = None
        w, h = 1080, 1920
        if media_type == "video":
            try:
                profile = analyze_reference_video(str(path), log_fn)
                w, h = profile.get("width", 1080), profile.get("height", 1920)
            except Exception:
                pass

        upsert_library_item(
            conn,
            kind="reference",
            filename=name,
            file_path=str(path),
            media_type=media_type,
            size_bytes=size,
            width=w,
            height=h,
            profile=profile,
            origin="scan",
        )
        added += 1

    return {"scanned": len(disk_files), "added": added}
