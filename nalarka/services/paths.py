"""Path safety helpers: keep client-supplied paths inside known media roots."""
import os
import re
from pathlib import Path
from typing import Iterable, List, Optional, Union

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")


def data_root() -> Path:
    """Resolved application data directory (``data/`` unless NALARKA_DATA_DIR is set)."""
    return Path(os.environ.get("NALARKA_DATA_DIR", "data")).resolve()


def media_roots(extra: Iterable[Optional[Union[str, Path]]] = ()) -> List[Path]:
    """Directories user-supplied media may live in: data root, extras (e.g. the
    configured library folder) and NALARKA_MEDIA_DIRS (os.pathsep separated)."""
    roots = [data_root()]
    for e in extra:
        if e:
            roots.append(Path(e).resolve())
    for e in os.environ.get("NALARKA_MEDIA_DIRS", "").split(os.pathsep):
        if e.strip():
            roots.append(Path(e.strip()).resolve())
    return roots


def is_within(path: Union[str, Path], roots: Iterable[Path]) -> bool:
    try:
        p = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    for r in roots:
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def resolve_media_path(raw: Union[str, Path], roots: Iterable[Path]) -> Path:
    """Resolve ``raw`` and require it to sit under one of ``roots``. Raises ValueError."""
    if raw is None or str(raw).strip() == "" or "\x00" in str(raw):
        raise ValueError("Path is empty or invalid.")
    p = Path(raw).resolve()
    if not is_within(p, roots):
        raise ValueError("Path is outside the allowed media directories.")
    return p


def safe_output_name(name: Optional[str], default: str = "output_motion_graphic.mp4") -> str:
    """Reduce a client-supplied output name to a plain ``*.mp4`` file name (no directories)."""
    base = (name or "").replace("\\", "/").split("/")[-1].strip().strip(".")
    base = _SAFE_NAME_RE.sub("_", base).strip(" ._")
    if not base:
        return default
    if not base.lower().endswith(".mp4"):
        base += ".mp4"
    return base[:120]


def max_upload_bytes() -> int:
    try:
        return int(float(os.environ.get("NALARKA_MAX_UPLOAD_MB", "500")) * 1024 * 1024)
    except ValueError:
        return 500 * 1024 * 1024


def safe_upload_name(name: Optional[str], default: str = "upload") -> str:
    """Plain file name (no directories, no odd characters); keeps the extension."""
    base = (name or "").replace("\\", "/").split("/")[-1].strip().strip(".")
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = _SAFE_NAME_RE.sub("_", stem).strip(" ._") or default
    ext = _SAFE_NAME_RE.sub("", ext)[:10]
    return f"{stem[:100]}.{ext}" if ext else stem[:100]


def save_upload(src, dest: Union[str, Path], max_bytes: Optional[int] = None, chunk: int = 1024 * 1024) -> int:
    """Copy file-like ``src`` to ``dest`` atomically (temp file + replace) with a size cap.
    Raises ValueError when the cap is exceeded; leaves no partial file behind."""
    limit = max_upload_bytes() if max_bytes is None else max_bytes
    dest = Path(dest)
    tmp = dest.with_name(dest.name + ".part")
    total = 0
    try:
        with open(tmp, "wb") as f:
            while True:
                buf = src.read(chunk)
                if not buf:
                    break
                total += len(buf)
                if total > limit:
                    raise ValueError(f"File too large (limit {limit // (1024 * 1024)} MB).")
                f.write(buf)
        os.replace(tmp, dest)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return total
