"""Buffered event emitter for background jobs."""
import time
import queue
import threading
import sqlite3
from datetime import datetime
from typing import Optional, List, Tuple

from ..db.repos.jobs import batch_append_job_events, update_job_status


class JobEventEmitter:
    """Thread-safe event collector that flushes events in batches to SQLite."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        job_id: str,
        batch_size: int = 50,
        flush_interval: float = 0.2,
    ):
        self.conn = conn
        self.job_id = job_id
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self._lock = threading.Lock()
        self._buffer: List[Tuple[str, str, Optional[str], Optional[int]]] = []
        self._last_flush = time.time()
        self._closed = False

    def _format_log_message(self, message: str) -> str:
        # Replicate desktop console timestamp format [HH:MM:SS] if not already present
        if message and not (message.startswith("[") and len(message) >= 10 and message[3] == ":" and message[6] == ":"):
            ts_str = datetime.now().strftime("%H:%M:%S")
            return f"[{ts_str}] {message}"
        return message

    def log(self, message: str) -> None:
        formatted = self._format_log_message(message)
        print(formatted, flush=True)
        with self._lock:
            self._buffer.append((self.job_id, "log", formatted, None))
            if len(self._buffer) >= self.batch_size or (time.time() - self._last_flush) >= self.flush_interval:
                self._flush_locked()

    def set_progress(self, pct: int, status: str = "") -> None:
        pct = max(0, min(100, int(pct)))
        with self._lock:
            self._buffer.append((self.job_id, "progress", status, pct))
            # Also keep jobs table progress column in sync
            try:
                update_job_status(self.conn, self.job_id, status="running", progress=pct, status_text=status)
            except Exception:
                pass
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffer or self._closed:
            return
        to_write = list(self._buffer)
        self._buffer.clear()
        self._last_flush = time.time()
        try:
            batch_append_job_events(self.conn, to_write)
        except Exception as exc:
            print(f"[EVENT FLUSH ERROR] {exc}", flush=True)

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def close(self) -> None:
        with self._lock:
            self._flush_locked()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
