"""Dependencies for FastAPI routes."""
import sqlite3
from typing import Generator, Optional
from pathlib import Path
from ..db.connection import get_connection, run_migrations
from ..jobs.dispatcher import JobDispatcher

_DISPATCHER: Optional[JobDispatcher] = None


def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_dispatcher(db_path: Optional[str] = None) -> JobDispatcher:
    global _DISPATCHER
    if _DISPATCHER is None:
        _DISPATCHER = JobDispatcher(db_path=db_path)
    return _DISPATCHER


def set_dispatcher(dispatcher: JobDispatcher) -> None:
    global _DISPATCHER
    _DISPATCHER = dispatcher
