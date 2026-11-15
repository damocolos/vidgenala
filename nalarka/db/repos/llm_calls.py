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
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
) -> int:
    with conn:
        cur = conn.execute(
            """
            INSERT INTO llm_calls (
              job_id, topic_id, tag, model, base_url, request_hash, system_prompt, user_prompt,
              temperature, max_tokens, response_text, finish_reason, status, accepted, error,
              req_chars, resp_chars, duration_sec, replay_of, prompt_tokens, completion_tokens
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id, topic_id, tag, model, base_url, request_hash, system_prompt, user_prompt,
                temperature, max_tokens, response_text, finish_reason, status, int(accepted), error,
                len(system_prompt or "") + len(user_prompt or ""), len(response_text or ""),
                duration_sec, replay_of, prompt_tokens, completion_tokens,
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
    "req_chars, resp_chars, duration_sec, replay_of, prompt_tokens, completion_tokens, created_at"
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


def _where(job_id, topic_id):
    where, args = [], []
    if job_id:
        where.append("job_id = ?"); args.append(job_id)
    if topic_id is not None:
        where.append("topic_id = ?"); args.append(topic_id)
    return ((" WHERE " + " AND ".join(where)) if where else ""), args


def summary(conn: sqlite3.Connection, *, job_id: Optional[str] = None, topic_id: Optional[int] = None) -> Dict[str, Any]:
    w, args = _where(job_id, topic_id)
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


def usage_by_model(conn: sqlite3.Connection, *, job_id: Optional[str] = None, topic_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Per model: tokens paid for and tokens saved by replays. Reported tokens when the provider sent them, else chars/4."""
    w, args = _where(job_id, topic_id)
    rows = conn.execute(
        f"""
        SELECT model, status,
               COUNT(*) AS calls,
               COALESCE(SUM(COALESCE(prompt_tokens, req_chars / 4)), 0)     AS prompt_tokens,
               COALESCE(SUM(COALESCE(completion_tokens, resp_chars / 4)), 0) AS completion_tokens,
               COALESCE(SUM(prompt_tokens IS NULL), 0)                       AS estimated_calls
        FROM llm_calls{w}{' AND' if w else ' WHERE'} status IN ('ok','replayed')
        GROUP BY model, status
        """,
        args,
    ).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        m = out.setdefault(r["model"] or "", {"model": r["model"] or "", "paid_calls": 0, "paid_prompt": 0, "paid_completion": 0,
                                              "saved_calls": 0, "saved_prompt": 0, "saved_completion": 0, "estimated_calls": 0})
        pre = "paid" if r["status"] == "ok" else "saved"
        m[f"{pre}_calls"] += r["calls"]
        m[f"{pre}_prompt"] += r["prompt_tokens"]
        m[f"{pre}_completion"] += r["completion_tokens"]
        if r["status"] == "ok":
            m["estimated_calls"] += r["estimated_calls"]
    return sorted(out.values(), key=lambda x: x["model"])


def topic_job_ids(conn: sqlite3.Connection, topic_id: Optional[int], exclude: Optional[str] = None, limit: int = 20) -> List[str]:
    """Newest-first jobs of a topic that stored accepted answers. Answers are keyed by content hash, so
    replaying them in a later job of the same topic is always safe."""
    if topic_id is None:
        return []
    rows = conn.execute(
        "SELECT job_id, MAX(id) AS last FROM llm_calls WHERE topic_id = ? AND accepted = 1 AND job_id IS NOT NULL "
        "GROUP BY job_id ORDER BY last DESC LIMIT ?", (topic_id, limit + 1)).fetchall()
    return [r["job_id"] for r in rows if r["job_id"] != exclude][:limit]
