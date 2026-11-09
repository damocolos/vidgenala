"""Post-render media helpers: poster frame + checksum (best effort, never fails a render)."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def sha256_file(path: Path, chunk: int = 4 * 1024 * 1024) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                buf = f.read(chunk)
                if not buf:
                    break
                h.update(buf)
        return h.hexdigest()
    except OSError:
        return None


def make_poster(video: Path, out_jpg: Path, at_sec: float = 1.5, width: int = 540) -> Optional[Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not Path(video).exists():
        return None
    try:
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-ss", f"{max(0.0, at_sec):.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2", "-q:v", "4", str(out_jpg)],
            capture_output=True, timeout=60, check=True,
        )
        return out_jpg if out_jpg.exists() else None
    except Exception:
        return None


def probe_video(video: Path) -> dict:
    """width/height/duration/frames via ffprobe (empty dict when unavailable)."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not Path(video).exists():
        return {}
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,nb_frames,duration:format=duration", "-of", "json", str(video)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        d = json.loads(out)
        st = (d.get("streams") or [{}])[0]
        dur = float(st.get("duration") or (d.get("format") or {}).get("duration") or 0) or None
        frames = int(st["nb_frames"]) if str(st.get("nb_frames", "")).isdigit() else None
        return {"width": st.get("width"), "height": st.get("height"), "duration": dur, "frames": frames}
    except Exception:
        return {}


def finalize_render_media(video: Path, workdir: Path, total_dur: Optional[float] = None):
    """Returns (poster_path or None, sha256 or None)."""
    dur = total_dur or probe_video(video).get("duration") or 4
    out = Path(workdir) / "poster.jpg"
    poster = make_poster(video, out, at_sec=min(2.0, dur / 2)) or make_poster(video, out, at_sec=0.0)
    return (str(poster) if poster else None), sha256_file(Path(video))
