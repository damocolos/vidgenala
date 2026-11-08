"""Repository for voiceovers and transcripts."""
import json
import sqlite3
from typing import List, Optional, Dict, Any


def create_voiceover(
    conn: sqlite3.Connection,
    topic_id: int,
    file_path: str,
    origin: str = "generated",
    script_id: Optional[int] = None,
    provider: Optional[str] = None,
    voice: Optional[str] = None,
    model_id: Optional[str] = None,
    params: Optional[dict] = None,
    duration_sec: Optional[float] = None,
    total_dur_locked: Optional[int] = None,
    script_fingerprint: Optional[str] = None,
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO voiceovers (
            topic_id, script_id, origin, provider, voice, model_id,
            params_json, file_path, duration_sec, total_dur_locked,
            script_fingerprint, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            topic_id, script_id, origin, provider, voice, model_id,
            json.dumps(params or {}, ensure_ascii=False),
            file_path, duration_sec, total_dur_locked, script_fingerprint,
        ),
    )
    return cur.lastrowid


def get_voiceover(conn: sqlite3.Connection, voiceover_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM voiceovers WHERE id = ?", (voiceover_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["params"] = json.loads(d["params_json"]) if d["params_json"] else {}
    return d


def list_voiceovers_for_topic(conn: sqlite3.Connection, topic_id: int) -> List[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM voiceovers WHERE topic_id = ? ORDER BY created_at DESC", (topic_id,))
    items = []
    for r in cur.fetchall():
        d = dict(r)
        d["params"] = json.loads(d["params_json"]) if d["params_json"] else {}
        items.append(d)
    return items


def create_transcript(
    conn: sqlite3.Connection,
    voiceover_id: int,
    whisper_size: str,
    language: Optional[str],
    words: List[dict],
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO transcripts (voiceover_id, whisper_size, language, words_json, created_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        """,
        (voiceover_id, whisper_size, language, json.dumps(words, ensure_ascii=False)),
    )
    return cur.lastrowid


def get_transcript(conn: sqlite3.Connection, transcript_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM transcripts WHERE id = ?", (transcript_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["words"] = json.loads(d["words_json"])
    return d


def get_transcript_for_voiceover(
    conn: sqlite3.Connection,
    voiceover_id: int,
    whisper_size: Optional[str] = None,
) -> Optional[dict]:
    cur = conn.cursor()
    if whisper_size:
        cur.execute(
            "SELECT * FROM transcripts WHERE voiceover_id = ? AND whisper_size = ? ORDER BY id DESC LIMIT 1",
            (voiceover_id, whisper_size),
        )
    else:
        cur.execute(
            "SELECT * FROM transcripts WHERE voiceover_id = ? ORDER BY id DESC LIMIT 1",
            (voiceover_id,),
        )
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["words"] = json.loads(d["words_json"])
    return d
