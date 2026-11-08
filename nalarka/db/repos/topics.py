"""Repository for topics."""
import sqlite3
from typing import List, Optional, Dict, Any


def create_topic(
    conn: sqlite3.Connection,
    title: str,
    language: str = "Indonesian",
    max_duration: int = 60,
    notes: Optional[str] = None,
) -> int:
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Topic title cannot be empty.")
    if not (15 <= max_duration <= 900):
        raise ValueError("max_duration must be between 15 and 900 seconds.")
    if language not in ("Indonesian", "English"):
        raise ValueError("language must be 'Indonesian' or 'English'.")

    with conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO topics (title, language, max_duration, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (clean_title, language, max_duration, notes),
        )
        return cur.lastrowid


def get_topic(conn: sqlite3.Connection, topic_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, title, language, max_duration, notes, created_at, updated_at, archived_at
        FROM topics WHERE id = ?
        """,
        (topic_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def update_topic(
    conn: sqlite3.Connection,
    topic_id: int,
    *,
    title: Optional[str] = None,
    language: Optional[str] = None,
    max_duration: Optional[int] = None,
    notes: Optional[str] = None,
) -> Optional[dict]:
    current = get_topic(conn, topic_id)
    if not current:
        return None

    new_title = title.strip() if title is not None else current["title"]
    if not new_title:
        raise ValueError("Topic title cannot be empty.")

    new_lang = language if language is not None else current["language"]
    if new_lang not in ("Indonesian", "English"):
        raise ValueError("language must be 'Indonesian' or 'English'.")

    new_max = max_duration if max_duration is not None else current["max_duration"]
    if not (15 <= new_max <= 900):
        raise ValueError("max_duration must be between 15 and 900 seconds.")

    new_notes = notes if notes is not None else current["notes"]

    with conn:
        conn.execute(
            """
            UPDATE topics
            SET title = ?, language = ?, max_duration = ?, notes = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (new_title, new_lang, new_max, new_notes, topic_id),
        )
    return get_topic(conn, topic_id)


def archive_topic(conn: sqlite3.Connection, topic_id: int) -> bool:
    with conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE topics
            SET archived_at = datetime('now'), updated_at = datetime('now')
            WHERE id = ? AND archived_at IS NULL
            """,
            (topic_id,),
        )
        return cur.rowcount > 0


def delete_topic(conn: sqlite3.Connection, topic_id: int) -> bool:
    with conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
        return cur.rowcount > 0


def list_topics(conn: sqlite3.Connection, include_archived: bool = False) -> List[dict]:
    cur = conn.cursor()
    if include_archived:
        cur.execute("SELECT * FROM topics ORDER BY updated_at DESC")
    else:
        cur.execute("SELECT * FROM topics WHERE archived_at IS NULL ORDER BY updated_at DESC")
    return [dict(r) for r in cur.fetchall()]
