"""Repository for the AI request ledger (cost visibility + resume/replay)."""
import json
import sqlite3
from typing import Any, Dict, Iterable, List, Optional


def record_call(
    conn: sqlite3.Connection,
    *,
    job_id: Optional[str],
    topic_id: Optional[int],
    tag: str,
    model: str,
    base_url: str,
    request_hash: str,
    system_prompt: str,
    user_prompt: str,
    temperature: Optional[float],
    max_tokens: Optional[int],
    status: str,
    response_text: Optional[str] = None,
    finish_reason: Optional[str] = None,
    error: Optional[str] = None,
    duration_sec: Optional[float] = None,
    replay_of: Optional[int] = None,
    accepted: bool = False,
) -> int:
    with conn:
        cur = conn.execute(
            """
            INSERT INTO llm_calls (
              job_id, topic_id, tag, model, base_url, request_hash, system_prompt, user_prompt,
              temperature, max_tokens, response_text, finish_reason, status, accepted, error,
              req_chars, resp_chars, duration_sec, replay_of
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id, topic_id, tag, model, base_url, request_hash, system_prompt, user_prompt,
                temperature, max_tokens, response_text, finish_reason, status, int(accepted), error,
                len(system_prompt or "") + len(user_prompt or ""), len(response_text or ""),
                duration_sec, replay_of,
            ),
        )
        return cur.lastrowid


def mark_accepted(conn: sqlite3.Connection, call_id: int) -> None:
    with conn:
        conn.execute("UPDATE llm_calls SET accepted = 1 WHERE id = ?", (call_id,))


def find_replay(conn: sqlite3.Connection, request_hash: str, job_ids: Iterable[str]) -> Optional[dict]:
    ids = [j for j in job_ids if j]
    if not ids:
        return None
    marks = ",".join("?" for _ in ids)
    row = conn.execute(
        f"""
        SELECT id, response_text, finish_reason FROM llm_calls
        WHERE request_hash = ? AND accepted = 1 AND status IN ('ok','replayed')
          AND job_id IN ({marks})
        ORDER BY id DESC LIMIT 1
        """,
        (request_hash, *ids),
    ).fetchone()
    return dict(row) if row else None


def job_chain(conn: sqlite3.Connection, job_id: Optional[str], max_depth: int = 20) -> List[str]:
    """[job_id, the job it resumed, the one before that, ...]"""
    chain: List[str] = []
    cur_id = job_id
    while cur_id and cur_id not in chain and len(chain) < max_depth:
        chain.append(cur_id)
        row = conn.execute("SELECT params_json FROM jobs WHERE id = ?", (cur_id,)).fetchone()
        if not row or not row["params_json"]:
            break
        try:
            cur_id = (json.loads(row["params_json"]) or {}).get("resume_of")
        except ValueError:
            break
    return chain


_LIST_COLS = (
    "id, job_id, topic_id, tag, model, status, accepted, finish_reason, error, "
    "req_chars, resp_chars, duration_sec, replay_of, created_at"
)


def list_calls(
    conn: sqlite3.Connection,
    *,
    job_id: Optional[str] = None,
    topic_id: Optional[int] = None,
    limit: int = 200,
    with_text: bool = False,
) -> List[Dict[str, Any]]:
    cols = _LIST_COLS + (", system_prompt, user_prompt, response_text" if with_text else "")
    where, args = [], []
    if job_id:
        where.append("job_id = ?"); args.append(job_id)
    if topic_id is not None:
        where.append("topic_id = ?"); args.append(topic_id)
    sql = f"SELECT {cols} FROM llm_calls" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY id DESC LIMIT ?"
    return [dict(r) for r in conn.execute(sql, (*args, limit)).fetchall()]


def get_call(conn: sqlite3.Connection, call_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
    return dict(row) if row else None


def summary(conn: sqlite3.Connection, *, job_id: Optional[str] = None, topic_id: Optional[int] = None) -> Dict[str, Any]:
    where, args = [], []
    if job_id:
        where.append("job_id = ?"); args.append(job_id)
    if topic_id is not None:
        where.append("topic_id = ?"); args.append(topic_id)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    r = conn.execute(
        f"""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(status='ok'),0)       AS paid_calls,
               COALESCE(SUM(status='replayed'),0) AS replayed_calls,
               COALESCE(SUM(status='error'),0)    AS error_calls,
               COALESCE(SUM(CASE WHEN status='ok' THEN req_chars END),0)      AS paid_req_chars,
               COALESCE(SUM(CASE WHEN status='ok' THEN resp_chars END),0)     AS paid_resp_chars,
               COALESCE(SUM(CASE WHEN status='replayed' THEN req_chars END),0)  AS saved_req_chars,
               COALESCE(SUM(CASE WHEN status='replayed' THEN resp_chars END),0) AS saved_resp_chars
        FROM llm_calls{w}
        """,
        args,
    ).fetchone()
    return dict(r)
