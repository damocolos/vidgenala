"""Repository for versioned scripts."""
import json
import sqlite3
from typing import List, Optional, Dict, Any

from ...core.scripttext import parse_tagged_script, script_fingerprint
from ...core.script import estimate_video_seconds, script_word_budget


def create_script(
    conn: sqlite3.Connection,
    topic_id: int,
    raw_text: str,
    source: str = "ai",
    model: Optional[str] = None,
    max_duration: int = 60,
    is_current: bool = True,
) -> dict:
    clean_raw = raw_text.strip()
    if not clean_raw:
        raise ValueError("Script text cannot be empty.")

    clean_text, sections = parse_tagged_script(clean_raw)
    fingerprint = script_fingerprint(clean_raw)
    word_count = len(clean_text.split())
    est_vo, est_vid = estimate_video_seconds(word_count)
    budget_lo, budget_hi = script_word_budget(max_duration)

    with conn:
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM scripts WHERE topic_id = ?", (topic_id,))
        next_ver = cur.fetchone()[0]

        if is_current:
            conn.execute("UPDATE scripts SET is_current = 0 WHERE topic_id = ? AND is_current = 1", (topic_id,))

        cur.execute(
            """
            INSERT INTO scripts (
                topic_id, version, source, model, raw_text, clean_text,
                sections_json, fingerprint, word_count, est_voice_sec,
                est_video_sec, word_budget_lo, word_budget_hi, max_duration_at,
                is_current, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                topic_id, next_ver, source, model, clean_raw, clean_text,
                json.dumps(sections, ensure_ascii=False), fingerprint, word_count,
                est_vo, est_vid, budget_lo, budget_hi, max_duration, int(is_current),
            ),
        )
        script_id = cur.lastrowid
    return get_script(conn, script_id)


def get_script(conn: sqlite3.Connection, script_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM scripts WHERE id = ?", (script_id,))
    row = cur.fetchone()
    if not row:
        return None
    data = dict(row)
    data["sections"] = json.loads(data["sections_json"])
    return data


def get_current_script(conn: sqlite3.Connection, topic_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM scripts WHERE topic_id = ? AND is_current = 1", (topic_id,))
    row = cur.fetchone()
    if not row:
        return None
    data = dict(row)
    data["sections"] = json.loads(data["sections_json"])
    return data


def list_scripts_for_topic(conn: sqlite3.Connection, topic_id: int) -> List[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM scripts WHERE topic_id = ? ORDER BY version DESC", (topic_id,))
    items = []
    for r in cur.fetchall():
        d = dict(r)
        d["sections"] = json.loads(d["sections_json"])
        items.append(d)
    return items


def set_current_script(conn: sqlite3.Connection, topic_id: int, script_id: int) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT id FROM scripts WHERE id = ? AND topic_id = ?", (script_id, topic_id))
    if not cur.fetchone():
        return False
    with conn:
        conn.execute("UPDATE scripts SET is_current = 0 WHERE topic_id = ? AND is_current = 1", (topic_id,))
        conn.execute("UPDATE scripts SET is_current = 1 WHERE id = ?", (script_id,))
    return True
