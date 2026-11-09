"""Repository for background jobs and real-time SSE events."""
import json
import sqlite3
from typing import List, Optional, Dict, Any, Tuple


def create_job(
    conn: sqlite3.Connection,
    job_id: str,
    kind: str,
    weight: str,
    params: dict,
    topic_id: Optional[int] = None,
    render_id: Optional[int] = None,
) -> str:
    # Ensure params do NOT contain unencrypted API keys
    safe_params = dict(params)
    safe_params.pop("api_key", None)
    safe_params.pop("el_api_key", None)

    with conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, kind, weight, topic_id, render_id, status, progress,
                params_json, created_at
            ) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, datetime('now'))
            """,
            (job_id, kind, weight, topic_id, render_id, json.dumps(safe_params, ensure_ascii=False)),
        )
    return job_id


def get_job(conn: sqlite3.Connection, job_id: str) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["params"] = json.loads(d["params_json"]) if d["params_json"] else {}
    d["result"] = json.loads(d["result_json"]) if d["result_json"] else None
    return d


def update_job_status(
    conn: sqlite3.Connection,
    job_id: str,
    status: str,
    *,
    progress: Optional[int] = None,
    status_text: Optional[str] = None,
    result: Optional[dict] = None,
    error: Optional[str] = None,
    pid: Optional[int] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> None:
    current = get_job(conn, job_id)
    if not current:
        raise ValueError(f"Job {job_id} not found.")

    upd_prog = progress if progress is not None else current["progress"]
    upd_text = status_text if status_text is not None else current["status_text"]
    upd_err = error if error is not None else current["error"]
    upd_pid = pid if pid is not None else current["pid"]
    upd_started = started_at if started_at is not None else current["started_at"]
    upd_finished = finished_at if finished_at is not None else current["finished_at"]
    res_str = json.dumps(result, ensure_ascii=False) if result is not None else (
        json.dumps(current["result"], ensure_ascii=False) if current["result"] is not None else None
    )

    with conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, progress = ?, status_text = ?, result_json = ?,
                error = ?, pid = ?, started_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, upd_prog, upd_text, res_str, upd_err, upd_pid, upd_started, upd_finished, job_id),
        )


def list_active_jobs(conn: sqlite3.Connection) -> List[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM jobs
        WHERE status IN ('queued', 'running')
        ORDER BY created_at ASC
        """
    )
    items = []
    for r in cur.fetchall():
        d = dict(r)
        d["params"] = json.loads(d["params_json"]) if d["params_json"] else {}
        d["result"] = json.loads(d["result_json"]) if d["result_json"] else None
        items.append(d)
    return items


def list_jobs(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    limit: int = 50,
    topic_id: Optional[int] = None,
    kind: Optional[str] = None,
) -> List[dict]:
    where, args = [], []
    if status:
        where.append("status = ?"); args.append(status)
    if topic_id is not None:
        where.append("topic_id = ?"); args.append(topic_id)
    if kind:
        where.append("kind = ?"); args.append(kind)
    sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    items = []
    for r in conn.execute(sql, (*args, limit)).fetchall():
        d = dict(r)
        d["params"] = json.loads(d["params_json"]) if d["params_json"] else {}
        d["result"] = json.loads(d["result_json"]) if d["result_json"] else None
        items.append(d)
    return items


def has_active_resume(conn: sqlite3.Connection, job_id: str) -> bool:
    """True if a queued/running job already resumes `job_id` (prevents paying twice)."""
    for r in conn.execute("SELECT params_json FROM jobs WHERE status IN ('queued','running')").fetchall():
        try:
            if (json.loads(r["params_json"]) or {}).get("resume_of") == job_id:
                return True
        except ValueError:
            continue
    return False


def append_job_event(
    conn: sqlite3.Connection,
    job_id: str,
    kind: str,
    message: Optional[str] = None,
    pct: Optional[int] = None,
) -> int:
    with conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO job_events (job_id, kind, message, pct)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, kind, message, pct),
        )
        return cur.lastrowid


def batch_append_job_events(
    conn: sqlite3.Connection,
    events: List[Tuple[str, str, Optional[str], Optional[int]]],
) -> int:
    """Inserts multiple events in a single batch for high-throughput logging."""
    if not events:
        return 0
    with conn:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO job_events (job_id, kind, message, pct)
            VALUES (?, ?, ?, ?)
            """,
            events,
        )
        return cur.rowcount


def get_job_events(
    conn: sqlite3.Connection,
    job_id: str,
    since_id: int = 0,
    limit: int = 500,
) -> List[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, job_id, ts, kind, message, pct
        FROM job_events
        WHERE job_id = ? AND id > ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (job_id, since_id, limit),
    )
    return [dict(r) for r in cur.fetchall()]
