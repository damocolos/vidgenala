"""Repository for render history and automated verifications."""
import json
import sqlite3
from typing import List, Optional, Dict, Any


def create_render(
    conn: sqlite3.Connection,
    topic_id: int,
    trigger: str = "full",
    storyboard_id: Optional[int] = None,
    voiceover_id: Optional[int] = None,
    bgm_item_id: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    total_dur: Optional[int] = None,
    workers: Optional[int] = None,
    whisper_size: Optional[str] = None,
    synth_bgm: Optional[bool] = None,
    orientation: Optional[str] = None,
    output_path: Optional[str] = None,
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO renders (
            topic_id, storyboard_id, voiceover_id, bgm_item_id, trigger,
            status, width, height, total_dur, workers, whisper_size,
            synth_bgm, orientation, output_path, created_at
        ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            topic_id, storyboard_id, voiceover_id, bgm_item_id, trigger,
            width, height, total_dur, workers, whisper_size,
            int(synth_bgm) if synth_bgm is not None else None,
            orientation, output_path,
        ),
    )
    return cur.lastrowid


def update_render_status(
    conn: sqlite3.Connection,
    render_id: int,
    status: str,
    error: Optional[str] = None,
    output_path: Optional[str] = None,
    file_size: Optional[int] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> None:
    current = get_render(conn, render_id)
    if not current:
        raise ValueError(f"Render {render_id} not found.")

    upd_path = output_path if output_path is not None else current["output_path"]
    upd_size = file_size if file_size is not None else current["file_size"]
    upd_started = started_at if started_at is not None else current["started_at"]
    upd_finished = finished_at if finished_at is not None else current["finished_at"]

    conn.execute(
        """
        UPDATE renders
        SET status = ?, error = ?, output_path = ?, file_size = ?,
            started_at = ?, finished_at = ?
        WHERE id = ?
        """,
        (status, error, upd_path, upd_size, upd_started, upd_finished, render_id),
    )


def get_render(conn: sqlite3.Connection, render_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM renders WHERE id = ?", (render_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def list_renders_for_topic(conn: sqlite3.Connection, topic_id: int) -> List[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM renders WHERE topic_id = ? ORDER BY id DESC", (topic_id,))
    return [dict(r) for r in cur.fetchall()]


def list_all_renders(conn: sqlite3.Connection, limit: int = 50) -> List[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM renders ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in cur.fetchall()]


def create_verification(
    conn: sqlite3.Connection,
    render_id: int,
    passed: int,
    total: int,
    duration_sec: Optional[float] = None,
    frames: Optional[int] = None,
    results: Optional[List[dict]] = None,
    contact_sheet_path: Optional[str] = None,
    max_duration_used: Optional[int] = None,
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO verifications (
            render_id, passed, total, duration_sec, frames,
            results_json, contact_sheet_path, max_duration_used, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            render_id, passed, total, duration_sec, frames,
            json.dumps(results or [], ensure_ascii=False),
            contact_sheet_path, max_duration_used,
        ),
    )
    return cur.lastrowid


def get_verification_for_render(conn: sqlite3.Connection, render_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM verifications WHERE render_id = ? ORDER BY id DESC LIMIT 1", (render_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["results"] = json.loads(d["results_json"]) if d["results_json"] else []
    return d
