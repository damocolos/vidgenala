"""7-Point Automated Verification Suite (ffprobe/ffmpeg)."""
import os
import re
import math
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Union
import numpy as np
from PIL import Image

from .constants import FPS, DEFAULT_MAX_DURATION

def run_verification_suite(
    output_path: str,
    expected_dur: Optional[int],
    expected_w: int,
    expected_h: int,
    log_fn,
    max_duration: int = DEFAULT_MAX_DURATION,
    check_dir: Optional[Union[str, Path]] = None,
) -> dict:
    if not os.path.exists(output_path):
        raise FileNotFoundError(f"Output MP4 file not found: {output_path}")

    log_fn("\n==========================================")
    log_fn("RUNNING 7-POINT AUTOMATED VERIFICATION SUITE")
    log_fn("==========================================")

    probe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,pix_fmt",
        "-of", "json", output_path
    ]
    probe_data = json.loads(subprocess.check_output(probe_cmd, text=True))
    fmt_dur = float(probe_data.get("format", {}).get("duration", 0.0))
    streams = probe_data.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    target_dur = expected_dur if expected_dur is not None else int(round(fmt_dur))
    expected_frames = target_dur * FPS

    frame_cmd = [
        "ffprobe", "-v", "error", "-count_frames",
        "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
        "-of", "csv=p=0", output_path
    ]
    actual_frames = int(subprocess.check_output(frame_cmd, text=True).strip())

    black_cmd = ["ffmpeg", "-i", output_path, "-vf", "blackdetect=d=0.1:pix_th=0.05", "-an", "-f", "null", "-"]
    black_proc = subprocess.run(black_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    black_hits = [ln for ln in black_proc.stderr.splitlines() if "black_start" in ln]

    vol_cmd = ["ffmpeg", "-i", output_path, "-af", "volumedetect", "-vn", "-f", "null", "-"]
    vol_proc = subprocess.run(vol_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    mean_vol, max_vol = -20.0, -1.0
    for ln in vol_proc.stderr.splitlines():
        if "mean_volume:" in ln:
            m = re.search(r"mean_volume:\s*([-\d.]+)", ln)
            if m:
                mean_vol = float(m.group(1))
        if "max_volume:" in ln:
            m = re.search(r"max_volume:\s*([-\d.]+)", ln)
            if m:
                max_vol = float(m.group(1))

    resolved_check_dir = Path(check_dir) if check_dir is not None else Path("check")
    if resolved_check_dir.exists():
        shutil.rmtree(resolved_check_dir)
    resolved_check_dir.mkdir(parents=True, exist_ok=True)

    last_ts = max(0.0, round(target_dur - 0.1, 2))
    subprocess.run(
        ["ffmpeg", "-y", "-i", output_path, "-vf", f"fps=1/{max(2.0, target_dur / 15.0):.3f},scale=360:-1", str(resolved_check_dir / "check_%02d.png")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    last_frame_path = str(resolved_check_dir / "last_frame.png")
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(last_ts), "-i", output_path, "-frames:v", "1", last_frame_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    last_frame_valid = False
    if os.path.exists(last_frame_path):
        lf_arr = np.asarray(Image.open(last_frame_path).convert("RGB"), dtype=np.float32)
        last_frame_valid = float(np.std(lf_arr)) > 5.0

    sheet_path = None
    chk_files = sorted(resolved_check_dir.glob("check_*.png"))
    if chk_files:
        thumbs = [Image.open(p).convert("RGB") for p in chk_files[:15]]
        cols = min(5, len(thumbs))
        rows = math.ceil(len(thumbs) / cols)
        tw, th = thumbs[0].size
        sheet = Image.new("RGB", (cols * tw, rows * th), (20, 20, 20))
        for i, im_t in enumerate(thumbs):
            r, c = divmod(i, cols)
            sheet.paste(im_t, (c * tw, r * th))
        sheet_path = resolved_check_dir / "verification_contact_sheet.jpg"
        sheet.save(str(sheet_path), quality=90)

    tests = [
        (
            "1. Codec & Pixel Format (H.264 + AAC, yuv420p)",
            v_stream.get("codec_name") == "h264" and a_stream.get("codec_name") == "aac" and v_stream.get("pix_fmt") == "yuv420p",
            f"v={v_stream.get('codec_name')}/{v_stream.get('pix_fmt')}, a={a_stream.get('codec_name')}"
        ),
        (
            "2. Resolution & Aspect Ratio Match",
            int(v_stream.get("width", 0)) == expected_w and int(v_stream.get("height", 0)) == expected_h,
            f"expected={expected_w}x{expected_h}, actual={v_stream.get('width')}x{v_stream.get('height')}"
        ),
        (
            "3. Constant Frame Rate (30/1 CFR)",
            v_stream.get("r_frame_rate") == "30/1" and v_stream.get("avg_frame_rate") == "30/1",
            f"r_frame_rate={v_stream.get('r_frame_rate')}, avg_frame_rate={v_stream.get('avg_frame_rate')}"
        ),
        (
            f"4. Exact Container Duration (<= {max_duration}s Cap)",
            abs(fmt_dur - target_dur) <= 0.12 and fmt_dur <= (max_duration + 0.12),
            f"target={target_dur}.000s, actual={fmt_dur:.3f}s"
        ),
        (
            "5. Exact Frame Count (DUR * 30)",
            actual_frames == expected_frames,
            f"expected={expected_frames} frames, actual={actual_frames} frames"
        ),
        (
            "6. Zero Black Frames & Active Visual Ending",
            len(black_hits) == 0 and last_frame_valid,
            f"black_segments={len(black_hits)}, last_frame_active={last_frame_valid}"
        ),
        (
            "7. Audio Presence & Anti-Clipping Protection",
            mean_vol > -45.0 and max_vol <= 0.0,
            f"mean_volume={mean_vol:.1f} dB, max_volume={max_vol:.1f} dB"
        ),
    ]

    passed_count = sum(1 for _, passed, _ in tests if passed)
    for name, passed, detail in tests:
        log_fn(f"  {'[PASS]' if passed else '[FAIL]'} {name} -> ({detail})")

    # Informational (not part of the 7 pass/fail checks): long frozen stretches usually mean a beat's animation stalled.
    try:
        frz = subprocess.run(
            ["ffmpeg", "-i", output_path, "-vf", "freezedetect=n=-60dB:d=2.5", "-an", "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        freezes = [ln.strip() for ln in frz.stderr.splitlines() if "freeze_start" in ln]
        if freezes:
            log_fn(f"  [NOTE] {len(freezes)} frozen stretch(es) >= 2.5s detected (check the contact sheet if unintended).")
    except Exception:
        pass

    sheet_str = str(sheet_path) if sheet_path else f"{resolved_check_dir}/verification_contact_sheet.jpg"
    log_fn(f"[VERIFICATION SUMMARY] Passed {passed_count}/{len(tests)} checks. Sheet: {sheet_str}")
    if passed_count < len(tests):
        raise AssertionError(f"Verification Suite failed {len(tests) - passed_count} check(s).")

    return {
        "passed": passed_count,
        "total": len(tests),
        "duration": fmt_dur,
        "frames": actual_frames,
        "check_dir": str(resolved_check_dir),
        "contact_sheet": sheet_str if sheet_path and sheet_path.exists() else None,
    }


