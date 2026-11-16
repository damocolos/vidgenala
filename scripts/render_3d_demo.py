"""Renders the built-in example shots with the REAL three.js on this computer and writes a short MP4.

Use it to see what Scene 3D looks like here and how long frames take, before any AI is involved.

    python scripts/render_3d_demo.py --out demo3d            (1080x1920, 3 shots of 2 s, 30 fps)
    python scripts/render_3d_demo.py --out demo3d --width 540 --height 960 --fps 15     (quick look)
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nalarka.core.scene3d.prompts import EXAMPLE_BEAT                       # noqa: E402
from nalarka.core.scene3d.render import Scene3DRenderer, encode_silent_video, ensure_three   # noqa: E402
from nalarka.core.scene3d.runtime import Scene3DUnavailable                 # noqa: E402


def demo_beats(shot_s: float = 2.0):
    orbit = EXAMPLE_BEAT.replace("beat_01", "beat_02").replace(
        "v.cam.dolly({ from: [-28, 7, 42], to: [-10, 9, 30], look: [0, 9, 0], fov: 50 });",
        "v.cam.orbit({ center: [0, 8, 0], radius: 38, height: 8, from: -50, to: 40, fov: 50 });")
    low = EXAMPLE_BEAT.replace("beat_01", "beat_03").replace(
        "v.cam.dolly({ from: [-28, 7, 42], to: [-10, 9, 30], look: [0, 9, 0], fov: 50 });",
        "v.cam.dolly({ from: [4, 1.6, 34], to: [2, 3.2, 22], look: [0, 6, 0], fov: 60 });")
    return [("beat_01", EXAMPLE_BEAT, 0.0, shot_s), ("beat_02", orbit, shot_s, 2 * shot_s), ("beat_03", low, 2 * shot_s, 3 * shot_s)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="demo3d")
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--three-file", help="use this three.js file instead of downloading r149")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        three = Path(args.three_file).read_text(encoding="utf-8") if args.three_file else ensure_three(out, log=print)
        beats = demo_beats()
        total = int(round(beats[-1][3] * args.fps))
        t0 = time.perf_counter()
        with Scene3DRenderer(three, size=(args.width, args.height)) as rd:
            def frames():
                for i, jpg in enumerate(rd.iter_frames(beats, args.fps, total)):
                    if i % (2 * args.fps) == 0:
                        (out / f"frame_{i:04d}.jpg").write_bytes(jpg)
                    yield jpg
            n = encode_silent_video(frames(), out / "demo.mp4", args.fps, (args.width, args.height))
            info = rd.info()
    except Scene3DUnavailable as err:
        print(f"Cannot run: {err}")
        return 2
    dt = time.perf_counter() - t0
    print(f"{n} frames in {dt:.1f} s = {dt / n:.3f} s/frame. Last frame: {info}. Video: {out / 'demo.mp4'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
