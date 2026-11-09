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
    with conn:
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

    with conn:
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
    with conn:
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


def bind_render_to_job(conn: sqlite3.Connection, render_id: int, job_id: str) -> None:
    """Links render <-> job both ways so cancel/crash/resume can find the render."""
    with conn:
        conn.execute("UPDATE renders SET job_id = ? WHERE id = ?", (job_id, render_id))
        conn.execute("UPDATE jobs SET render_id = ? WHERE id = ?", (render_id, job_id))


def update_render_media(conn: sqlite3.Connection, render_id: int, poster_path: Optional[str], sha256: Optional[str]) -> None:
    with conn:
        conn.execute("UPDATE renders SET poster_path = ?, sha256 = ? WHERE id = ?", (poster_path, sha256, render_id))


def end_unfinished_render_for_job(conn: sqlite3.Connection, job_id: str, status: str, error: Optional[str]) -> None:
    """Never leave a render stuck on queued/running after its job failed, was canceled or crashed."""
    with conn:
        conn.execute(
            """
            UPDATE renders SET status = ?, error = COALESCE(error, ?), finished_at = datetime('now')
            WHERE status IN ('queued','running')
              AND (job_id = ? OR id = (SELECT render_id FROM jobs WHERE id = ?))
            """,
            (status, error, job_id, job_id),
        )


def update_render_metrics(conn: sqlite3.Connection, render_id: int, width=None, height=None, total_dur=None) -> None:
    """Store what ffprobe measured, so history/UI can show resolution and length."""
    with conn:
        conn.execute(
            "UPDATE renders SET width = COALESCE(?, width), height = COALESCE(?, height), "
            "total_dur = COALESCE(?, total_dur) WHERE id = ?",
            (width, height, int(round(total_dur)) if total_dur else None, render_id),
        )


def verification_summaries(conn: sqlite3.Connection, render_ids: List[int]) -> Dict[int, dict]:
    """{render_id: {"passed": n, "total": n}} for the latest verification of each render."""
    if not render_ids:
        return {}
    marks = ",".join("?" for _ in render_ids)
    rows = conn.execute(
        f"""SELECT v.render_id, v.passed, v.total FROM verifications v
            JOIN (SELECT render_id, MAX(id) AS mid FROM verifications
                  WHERE render_id IN ({marks}) GROUP BY render_id) m ON v.id = m.mid""",
        render_ids,
    ).fetchall()
    return {r["render_id"]: {"passed": r["passed"], "total": r["total"]} for r in rows}
