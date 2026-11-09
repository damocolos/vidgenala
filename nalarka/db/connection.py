"""SQLite Connection Factory and Migration Runner."""
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional, Union, Generator
from contextlib import contextmanager

DEFAULT_DB_PATH = Path("data") / "nalarka.db"
DEFAULT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_db_path() -> Path:
    env_path = os.environ.get("NALARKA_DB_PATH")
    if env_path:
        return Path(env_path)
    from ..datadir import data_root
    return data_root() / "nalarka.db"


def get_connection(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """Creates a configured SQLite connection with WAL, foreign keys, and Row factory."""
    target = db_path if db_path is not None else get_db_path()
    is_memory = str(target) == ":memory:" or "mode=memory" in str(target)

    if not is_memory:
        p = Path(target)
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), timeout=5.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode = WAL;")
    else:
        conn = sqlite3.connect(":memory:", timeout=5.0, check_same_thread=False)

    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn


def get_user_version(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute("PRAGMA user_version;")
    row = cur.fetchone()
    return int(row[0]) if row else 0


def set_user_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)};")


def run_migrations(
    conn: sqlite3.Connection,
    migrations_dir: Optional[Path] = None,
) -> int:
    """Applies pending SQL migrations and returns total applied count."""
    target_dir = migrations_dir or DEFAULT_MIGRATIONS_DIR
    if not target_dir.exists():
        return 0

    current_version = get_user_version(conn)
    sql_files = sorted(target_dir.glob("*.sql"))
    applied = 0

    for file_path in sql_files:
        match = re.match(r"^(\d+)_.*\.sql$", file_path.name)
        if not match:
            continue
        migration_version = int(match.group(1))
        if migration_version > current_version:
            sql_script = file_path.read_text(encoding="utf-8")
            # Run migration in a transaction
            with conn:
                conn.executescript(sql_script)
                set_user_version(conn, migration_version)
            current_version = migration_version
            applied += 1

    return applied


@contextmanager
def get_db_context(
    db_path: Optional[Union[str, Path]] = None,
    auto_migrate: bool = True,
) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for thread-safe SQLite connection lifecycle."""
    conn = get_connection(db_path)
    if auto_migrate:
        run_migrations(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
