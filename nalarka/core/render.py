"""Multi-process frame rendering and FFmpeg rawvideo pipe."""
import os
import json
import time
import threading
import subprocess
import multiprocessing
from pathlib import Path
from typing import Dict, Any, List, Optional, Union

from .constants import FPS, FFMPEG_LOG_FILE
from .scene_code import build_namespace, new_scene_sources
from .frames import render_frame

# --- multi-process frame rendering -------------------------------------------------
# Worker processes rebuild the scene functions from their SOURCE (functions exec'd from AI code
# cannot be pickled), then render frames independently. Output order is preserved by imap.
_MP_STATE: Dict[str, Any] = {}


def _mp_init(plan_json: str, words: List[dict], width: int, height: int):
    plan = json.loads(plan_json)
    _MP_STATE["plan"] = plan
    _MP_STATE["words"] = words
    _MP_STATE["w"] = width
    _MP_STATE["h"] = height
    _MP_STATE["ns"] = build_namespace(plan.get("scene_sources") or new_scene_sources(), lambda _m: None)


def _mp_ping() -> int:
    return 1


def _mp_render_frame(frame_idx: int) -> bytes:
    img = render_frame(
        frame_idx / float(FPS), _MP_STATE["w"], _MP_STATE["h"],
        _MP_STATE["plan"], _MP_STATE["words"], _MP_STATE["ns"],
    )
    return img.tobytes()


def default_render_workers() -> int:
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, NotImplementedError, OSError):
        cpus = os.cpu_count() or 2
    return max(1, min(8, cpus - 1))


def render_video_pipeline(
    plan: dict,
    namespace: Dict[str, Any],
    words: List[dict],
    vo_path: str,
    bgm_path: Optional[str],
    output_path: str,
    total_dur: int,
    width: int,
    height: int,
    log_fn,
    progress_fn,
    workers: int = 1,
    ffmpeg_log_path: Optional[Union[str, Path]] = None,
    cancel_event: Optional[threading.Event] = None,
):
    total_frames = total_dur * FPS
    fade_st = max(0.0, round(total_dur - 2.5, 2))
    fade_vo_st = max(0.0, round(total_dur - 0.5, 2))
    preset = "medium" if total_dur <= 90 else "veryfast"   # long videos: favour turnaround

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(FPS),
        "-i", "-",
        "-i", vo_path,
    ]

    if bgm_path and os.path.exists(bgm_path):
        cmd += ["-stream_loop", "-1", "-i", bgm_path]
        filter_complex = (
            f"[1:a]adelay=1000:all=1,apad=whole_dur={total_dur},atrim=0:{total_dur},asetpts=PTS-STARTPTS,asplit=2[vo1][vo2];"
            f"[2:a]atrim=0:{total_dur},asetpts=PTS-STARTPTS,volume=0.32,afade=t=out:st={fade_st}:d=2.5[bg];"
            f"[bg][vo1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[duck];"
            f"[duck][vo2]amix=inputs=2:duration=longest:dropout_transition=0,volume=2,alimiter=limit=0.95[aout]"
        )
    else:
        filter_complex = (
            f"[1:a]adelay=1000:all=1,apad=whole_dur={total_dur},atrim=0:{total_dur},"
            f"afade=t=out:st={fade_vo_st}:d=0.5[aout]"
        )

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "libx264", "-preset", preset, "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-frames:v", str(total_frames),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", str(total_dur),
        "-movflags", "+faststart", output_path
    ]

    # ---- frame source: worker pool (preferred) or in-process fallback ----
    pool = None
    workers = max(1, int(workers))
    if workers > 1 and total_frames > FPS * 3:
        try:
            ctx = multiprocessing.get_context("spawn")
            pool = ctx.Pool(
                processes=workers,
                initializer=_mp_init,
                initargs=(json.dumps(plan, ensure_ascii=False), words, width, height),
            )
            pool.apply_async(_mp_ping).get(timeout=90)   # fails fast if workers cannot start
        except Exception as err:
            log_fn(f"[RENDER WARN] Could not start {workers} render workers ({err}). Rendering in a single process instead.")
            if pool is not None:
                pool.terminate()
            pool = None

    stop = threading.Event()
    sem = threading.BoundedSemaphore(max(2, workers * 3)) if pool else None   # caps frames in flight (~6 MB each)

    def _task_ids():
        for i in range(total_frames):
            while not sem.acquire(timeout=0.5):
                if stop.is_set() or (cancel_event and cancel_event.is_set()):
                    return
            if stop.is_set() or (cancel_event and cancel_event.is_set()):
                return
            yield i

    if pool:
        frames_iter = pool.imap(_mp_render_frame, _task_ids(), chunksize=1)
    else:
        frames_iter = (render_frame(i / float(FPS), width, height, plan, words, namespace).tobytes() for i in range(total_frames))

    log_fn(
        f"[FFMPEG] Rendering {total_frames} frames ({total_dur}s @ {FPS}fps, {width}x{height}, "
        f"x264 {preset}, {'%d workers' % workers if pool else '1 process'})..."
    )
    t0 = time.time()
    written = 0
    pipe_broken = False
    actual_log = Path(ffmpeg_log_path) if ffmpeg_log_path else Path(FFMPEG_LOG_FILE)
    actual_log.parent.mkdir(parents=True, exist_ok=True)

    with open(actual_log, "w", encoding="utf-8") as ff_err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=ff_err)
        try:
            for frame_idx, data in enumerate(frames_iter):
                if cancel_event and cancel_event.is_set():
                    log_fn("[RENDER] Cancellation requested, stopping render...")
                    break
                if sem:
                    sem.release()
                try:
                    proc.stdin.write(data)
                except (BrokenPipeError, OSError):
                    pipe_broken = True   # ffmpeg died; its log (checked below) explains why
                    break
                written += 1

                if (frame_idx + 1) % 30 == 0 or frame_idx == total_frames - 1:
                    pct = int(((frame_idx + 1) / total_frames) * 100)
                    progress_fn(pct, f"Rendering frame {frame_idx + 1}/{total_frames} ({pct}%)")
                if (frame_idx + 1) % 150 == 0 or frame_idx == total_frames - 1:
                    elapsed = max(0.001, time.time() - t0)
                    rate = (frame_idx + 1) / elapsed
                    eta = (total_frames - frame_idx - 1) / rate
                    log_fn(f"[RENDER] {frame_idx + 1}/{total_frames} frames ({int((frame_idx+1)/total_frames*100)}%) | {rate:.1f} fps | ETA {eta:.0f}s")
        finally:
            stop.set()
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            if pool is not None:
                try:
                    pool.close()
                except Exception:
                    pass
                try:
                    pool.terminate()
                except Exception:
                    pass
            if cancel_event and cancel_event.is_set():
                try:
                    proc.terminate()
                except OSError:
                    pass
            ret = proc.wait()

    if cancel_event and cancel_event.is_set():
        log_fn("[RENDER] Render was cancelled.")
        raise InterruptedError("Render was cancelled.")

    if ret != 0 or pipe_broken or written != total_frames:
        err_tail = ""
        if actual_log.exists():
            with open(actual_log, "r", encoding="utf-8", errors="replace") as f:
                err_tail = f.read()[-1500:]
        log_fn(f"[FFMPEG ERROR] Exit code {ret}, frames written {written}/{total_frames}:\n{err_tail}")
        raise RuntimeError(f"FFmpeg failed (exit code {ret}, {written}/{total_frames} frames). Check {actual_log}.")

    log_fn(f"[RENDER] Completed in {round(time.time() - t0, 2)}s -> {output_path}")



