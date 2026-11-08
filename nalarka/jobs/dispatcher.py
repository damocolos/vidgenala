"""Job Dispatcher managing concurrency, worker processes, and cancellation."""
import os
import sys
import time
import logging
import subprocess
import threading
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Set

from ..db.connection import get_connection, run_migrations
from ..db.repos.jobs import (
    get_job, update_job_status, list_active_jobs, list_jobs,
)
from ..db.repos.settings import get_setting

def _popen_group_kwargs() -> dict:
    """Start workers in their own process group so cancel can kill ffmpeg/render children too."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def kill_process_tree(proc, grace: float = 2.0) -> None:
    """Terminate ``proc`` and all its descendants (best effort, never raises)."""
    try:
        if proc.poll() is not None and os.name == "nt":
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10)
            return
        import signal
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.time() + grace
        while time.time() < deadline:
            proc.poll()  # reap the leader so the group can disappear
            try:
                os.killpg(proc.pid, 0)  # group still has members?
            except ProcessLookupError:
                return
            time.sleep(0.05)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=1.0)  # reap
        except Exception:
            pass
    except Exception:
        pass


HEAVY_KINDS = {"full_render", "reroll_beat", "preview_beat", "verify_mp4", "generate_tts"}
TOPIC_LOCKED_KINDS = {"full_render", "reroll_beat"}


class JobDispatcher:
    def __init__(
        self,
        db_path: Optional[str] = None,
        max_heavy: int = 1,
        max_light: int = 4,
        poll_interval: float = 0.5,
    ):
        self.db_path = db_path
        self.max_heavy = max_heavy
        self.max_light = max_light
        self.poll_interval = poll_interval

        self._running_procs: Dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
        # Serialises dispatch_next(): called from the poll thread AND from API endpoints.
        self._dispatch_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _get_db(self) -> sqlite3.Connection:
        conn = get_connection(self.db_path)
        run_migrations(conn)
        return conn

    def is_pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            if os.name == "nt":
                # Windows check via OpenProcess
                import ctypes
                kernel32 = ctypes.windll.kernel32
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if h_proc:
                    kernel32.CloseHandle(h_proc)
                    return True
                return False
            else:
                os.kill(pid, 0)
                return True
        except (OSError, ProcessLookupError, PermissionError):
            return False

    def recover_orphaned_jobs(self) -> None:
        """Finds any jobs left in 'running' state without a live process and marks them failed."""
        conn = self._get_db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, pid, kind FROM jobs WHERE status = 'running'")
            for row in cur.fetchall():
                jid, pid = row["id"], row["pid"]
                if not pid or not self.is_pid_alive(pid):
                    print(f"[DISPATCHER RECOVERY] Job {jid} (pid {pid}) is dead. Marking failed.", flush=True)
                    update_job_status(
                        conn, jid, status="failed",
                        error="Job process was interrupted or terminated unexpectedly.",
                        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                    )
        finally:
            conn.close()

    def can_start_job(self, job: dict, active_jobs: List[dict]) -> bool:
        jid = job.get("id")
        if jid:
            with self._lock:
                if jid in self._running_procs:
                    return False

        is_heavy = job["kind"] in HEAVY_KINDS
        running_jobs = [j for j in active_jobs if j["status"] == "running"]
        running_heavy = sum(1 for j in running_jobs if j["kind"] in HEAVY_KINDS)
        running_light = sum(1 for j in running_jobs if j["kind"] not in HEAVY_KINDS)

        # Check heavy/light limit
        if is_heavy and running_heavy >= self.max_heavy:
            return False
        if not is_heavy and running_light >= self.max_light:
            return False

        # Check per-topic lock
        topic_id = job.get("topic_id")
        if topic_id and job["kind"] in TOPIC_LOCKED_KINDS:
            for rj in running_jobs:
                if rj.get("topic_id") == topic_id and rj["kind"] in TOPIC_LOCKED_KINDS:
                    return False

        return True

    def dispatch_next(self) -> None:
        """Examines queued jobs and spawns workers within concurrency boundaries."""
        with self._dispatch_lock:
            self._dispatch_next_locked()

    def _dispatch_next_locked(self) -> None:
        conn = self._get_db()
        try:
            # Poll status of running processes
            with self._lock:
                finished_jids = []
                for jid, proc in list(self._running_procs.items()):
                    ret = proc.poll()
                    if ret is not None:
                        finished_jids.append((jid, ret))
                for jid, ret in finished_jids:
                    self._running_procs.pop(jid, None)
                    if ret != 0:
                        try:
                            j = get_job(conn, jid)
                            if j and j["status"] in ("queued", "running"):
                                update_job_status(
                                    conn, jid, status="failed",
                                    error=j.get("error") or f"Worker process crashed (exit code {ret})",
                                    finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                )
                        except Exception:
                            pass

            active = list_active_jobs(conn)
            queued = [j for j in active if j["status"] == "queued"]

            for q_job in queued:
                if self.can_start_job(q_job, active):
                    self._spawn_job_process(q_job)
                    # refresh active list for next iterations in the same tick
                    active = list_active_jobs(conn)
        finally:
            conn.close()

    def _spawn_job_process(self, job: dict) -> None:
        jid = job["id"]
        with self._lock:
            if jid in self._running_procs:
                return

        cmd = [sys.executable, "-m", "nalarka.jobs.jobrunner", jid]
        if self.db_path:
            cmd.append(str(self.db_path))

        msg = f"[DISPATCHER] Spawning worker subprocess for job {jid} ({job['kind']})..."
        print(msg, flush=True)
        try:
            logging.info(msg)
        except Exception:
            pass

        # Avoid shell=True to avoid subshell escaping issues
        proc = subprocess.Popen(cmd, **_popen_group_kwargs())
        with self._lock:
            self._running_procs[jid] = proc

        # Immediately transition job to 'running' in DB so subsequent ticks don't re-queue it
        try:
            conn = self._get_db()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            update_job_status(conn, jid, status="running", pid=proc.pid, started_at=now_str)
            conn.close()
        except Exception as e:
            try:
                logging.error(f"[DISPATCHER] Failed updating job status to running for {jid}: {e}")
            except Exception:
                pass

    def cancel_job(self, job_id: str) -> bool:
        """Cancels a job and terminates its subprocess if currently running."""
        conn = self._get_db()
        try:
            job = get_job(conn, job_id)
            if not job:
                return False

            with self._lock:
                proc = self._running_procs.pop(job_id, None)
                if proc:
                    kill_process_tree(proc)

            update_job_status(
                conn, job_id, status="canceled",
                error="Cancelled by user.",
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            return True
        finally:
            conn.close()

    def run_loop(self) -> None:
        self.recover_orphaned_jobs()
        while not self._stop_event.is_set():
            try:
                self.dispatch_next()
            except Exception as exc:
                print(f"[DISPATCHER ERROR] {exc}", flush=True)
            self._stop_event.wait(self.poll_interval)

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self.run_loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        with self._lock:
            for jid, proc in list(self._running_procs.items()):
                kill_process_tree(proc)
            self._running_procs.clear()
