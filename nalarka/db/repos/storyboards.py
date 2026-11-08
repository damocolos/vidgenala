"""Repository for storyboards, beats, helpers, and beat revisions."""
import json
import sqlite3
from typing import List, Optional, Dict, Any


def create_storyboard(
    conn: sqlite3.Connection,
    topic_id: int,
    coder_model: str,
    total_dur: int,
    palette: dict,
    script_id: Optional[int] = None,
    voiceover_id: Optional[int] = None,
    transcript_id: Optional[int] = None,
    ref_item_id: Optional[int] = None,
    is_current: bool = True,
) -> int:
    with conn:
        if is_current:
            conn.execute("UPDATE storyboards SET is_current = 0 WHERE topic_id = ? AND is_current = 1", (topic_id,))

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO storyboards (
                topic_id, script_id, voiceover_id, transcript_id, reference_item_id,
                coder_model, total_dur, palette_json, is_current, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                topic_id, script_id, voiceover_id, transcript_id, ref_item_id,
                coder_model, total_dur, json.dumps(palette, ensure_ascii=False),
                int(is_current),
            ),
        )
        return cur.lastrowid


def get_storyboard(conn: sqlite3.Connection, storyboard_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM storyboards WHERE id = ?", (storyboard_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["palette"] = json.loads(d["palette_json"])
    return d


def get_current_storyboard(conn: sqlite3.Connection, topic_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM storyboards WHERE topic_id = ? AND is_current = 1", (topic_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["palette"] = json.loads(d["palette_json"])
    return d


def list_storyboards_for_topic(conn: sqlite3.Connection, topic_id: int) -> List[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM storyboards WHERE topic_id = ? ORDER BY id DESC", (topic_id,))
    items = []
    for r in cur.fetchall():
        d = dict(r)
        d["palette"] = json.loads(d["palette_json"])
        items.append(d)
    return items


def insert_beat(
    conn: sqlite3.Connection,
    storyboard_id: int,
    beat_no: int,
    start_sec: float,
    end_sec: float,
    layout: Optional[str] = None,
    bg_color: Optional[str] = None,
    transition: Optional[str] = None,
    summary: Optional[str] = None,
    visual_blueprint: Optional[str] = None,
    dot_index: Optional[int] = None,
    lines: Optional[list] = None,
    extra: Optional[dict] = None,
    source_code: Optional[str] = None,
    code_status: str = "ok",
    audit: Optional[dict] = None,
) -> int:
    with conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO beats (
                storyboard_id, beat_no, start_sec, end_sec, layout, bg_color,
                transition, summary, visual_blueprint, dot_index, lines_json,
                extra_json, source_code, code_status, audit_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(storyboard_id, beat_no) DO UPDATE SET
                start_sec = excluded.start_sec,
                end_sec = excluded.end_sec,
                layout = excluded.layout,
                bg_color = excluded.bg_color,
                transition = excluded.transition,
                summary = excluded.summary,
                visual_blueprint = excluded.visual_blueprint,
                dot_index = excluded.dot_index,
                lines_json = excluded.lines_json,
                extra_json = excluded.extra_json,
                source_code = excluded.source_code,
                code_status = excluded.code_status,
                audit_json = excluded.audit_json
            """,
            (
                storyboard_id, beat_no, start_sec, end_sec, layout, bg_color,
                transition, summary, visual_blueprint, dot_index,
                json.dumps(lines, ensure_ascii=False) if lines is not None else None,
                json.dumps(extra, ensure_ascii=False) if extra is not None else None,
                source_code, code_status,
                json.dumps(audit, ensure_ascii=False) if audit is not None else None,
            ),
        )
        return cur.lastrowid


def get_beat(conn: sqlite3.Connection, beat_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM beats WHERE id = ?", (beat_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["lines"] = json.loads(d["lines_json"]) if d["lines_json"] else None
    d["extra"] = json.loads(d["extra_json"]) if d["extra_json"] else {}
    d["audit"] = json.loads(d["audit_json"]) if d["audit_json"] else None
    return d


def get_beat_by_number(conn: sqlite3.Connection, storyboard_id: int, beat_no: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM beats WHERE storyboard_id = ? AND beat_no = ?", (storyboard_id, beat_no))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["lines"] = json.loads(d["lines_json"]) if d["lines_json"] else None
    d["extra"] = json.loads(d["extra_json"]) if d["extra_json"] else {}
    d["audit"] = json.loads(d["audit_json"]) if d["audit_json"] else None
    return d


def list_beats_for_storyboard(conn: sqlite3.Connection, storyboard_id: int) -> List[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM beats WHERE storyboard_id = ? ORDER BY beat_no ASC", (storyboard_id,))
    items = []
    for r in cur.fetchall():
        d = dict(r)
        d["lines"] = json.loads(d["lines_json"]) if d["lines_json"] else None
        d["extra"] = json.loads(d["extra_json"]) if d["extra_json"] else {}
        d["audit"] = json.loads(d["audit_json"]) if d["audit_json"] else None
        items.append(d)
    return items


def update_beat_code(
    conn: sqlite3.Connection,
    beat_id: int,
    source_code: str,
    code_status: str = "ok",
    audit: Optional[dict] = None,
    note: Optional[str] = None,
    reason: str = "reroll",
) -> None:
    current = get_beat(conn, beat_id)
    if not current:
        raise ValueError(f"Beat id {beat_id} not found.")

    with conn:
        conn.execute(
            """
            UPDATE beats
            SET source_code = ?, code_status = ?, audit_json = ?
            WHERE id = ?
            """,
            (source_code, code_status, json.dumps(audit, ensure_ascii=False) if audit else None, beat_id),
        )

        # Record in beat_revisions
        create_beat_revision(
            conn,
            beat_id=beat_id,
            reason=reason,
            note=note,
            blueprint=current.get("visual_blueprint"),
            source_code=source_code,
        )


def insert_helper(conn: sqlite3.Connection, storyboard_id: int, name: str, seq: int, source: str) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO storyboard_helpers (storyboard_id, name, seq, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(storyboard_id, name) DO UPDATE SET
                seq = excluded.seq,
                source = excluded.source
            """,
            (storyboard_id, name, seq, source),
        )


def list_helpers_for_storyboard(conn: sqlite3.Connection, storyboard_id: int) -> List[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM storyboard_helpers WHERE storyboard_id = ? ORDER BY seq ASC", (storyboard_id,))
    return [dict(r) for r in cur.fetchall()]


def create_beat_revision(
    conn: sqlite3.Connection,
    beat_id: int,
    reason: str,
    note: Optional[str] = None,
    blueprint: Optional[str] = None,
    source_code: Optional[str] = None,
) -> int:
    with conn:
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(revision), 0) + 1 FROM beat_revisions WHERE beat_id = ?", (beat_id,))
        next_rev = cur.fetchone()[0]

        cur.execute(
            """
            INSERT INTO beat_revisions (beat_id, revision, reason, note, blueprint, source_code, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (beat_id, next_rev, reason, note, blueprint, source_code),
        )
        return cur.lastrowid


def list_revisions_for_beat(conn: sqlite3.Connection, beat_id: int) -> List[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM beat_revisions WHERE beat_id = ? ORDER BY revision ASC", (beat_id,))
    return [dict(r) for r in cur.fetchall()]
