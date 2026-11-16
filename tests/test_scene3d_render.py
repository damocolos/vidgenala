"""Scene 3D phase 3. The page runs against a stand-in for three.js (tests/fixtures/fake_three.js), so these tests
check OUR code (tree -> objects, camera, per-frame animation, beat switching, JPEG pipe, ffmpeg), not three.js itself."""
import io
import shutil
import subprocess
from pathlib import Path

import pytest

from nalarka.core.scene3d.prompts import EXAMPLE_BEAT
from nalarka.core.scene3d import render as r3

FAKE = (Path(__file__).parent / "fixtures" / "fake_three.js").read_text(encoding="utf-8")
SIZE = (216, 384)


def test_frame_plan_switches_beats_and_holds_last():
    beats = [("beat_01", "x", 0.0, 1.0), ("beat_02", "x", 1.0, 2.0)]
    plan = r3.frame_plan(beats, 10, 25)
    assert [b for b, _ in plan[:10]] == [0] * 10 and [b for b, _ in plan[10:]] == [1] * 15
    assert plan[10][1] == 0.0 and plan[0][1] == 0.0
    assert plan[-1][1] <= 0.9 + 1e-9                       # never beyond the beat's own length


def test_ensure_three_uses_cache(tmp_path):
    (tmp_path / f"three-{r3.THREE_VERSION}.min.js").write_text("REVISION='x'", encoding="utf-8")
    assert r3.ensure_three(tmp_path) == "REVISION='x'"


def test_ensure_three_reports_download_failure(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("blocked")
    monkeypatch.setattr(r3.urllib.request, "urlopen", boom)
    with pytest.raises(r3.Scene3DUnavailable) as e:
        r3.ensure_three(tmp_path / "c")
    assert "three-" in str(e.value)


@pytest.fixture(scope="module")
def renderer():
    pytest.importorskip("playwright")
    try:
        rd = r3.Scene3DRenderer(FAKE, size=SIZE, quality=0.8).__enter__()
    except r3.Scene3DUnavailable as err:
        pytest.skip(str(err))
    yield rd
    rd.__exit__(None, None, None)


def _jpeg_ok(b):
    return b[:2] == b"\xff\xd8"


def test_loads_example_and_frames_change_with_camera(renderer):
    info = renderer.load_beat("beat_01", EXAMPLE_BEAT, 2.0)
    assert info["nodes"] > 20
    a, b = renderer.frame_jpeg(0.0), renderer.frame_jpeg(1.8)
    assert _jpeg_ok(a) and _jpeg_ok(b) and a != b            # dolly + animations change the picture
    assert renderer.frame_jpeg(0.7) == renderer.frame_jpeg(0.7)   # deterministic


def test_invalid_beat_is_refused(renderer):
    with pytest.raises(ValueError, match="failed the check"):
        renderer.load_beat("beat_01", "function beat_01(v){ v.box(1,1,1); }", 2.0)


def test_switching_beats_disposes_old_geometry(renderer):
    renderer._page.evaluate("() => { window.__disposed = 0; }")
    renderer.load_beat("beat_01", EXAMPLE_BEAT, 2.0)
    renderer.load_beat("beat_01", EXAMPLE_BEAT, 2.0)
    assert renderer._page.evaluate("() => window.__disposed") > 20


def test_missing_beat_code_is_reported(renderer):
    with pytest.raises(ValueError, match="beat_02"):
        list(renderer.iter_frames([("beat_01", EXAMPLE_BEAT, 0, 1), ("beat_02", None, 1, 2)], 10, 20))


def test_cancel_stops_rendering(renderer):
    n = {"c": 0}
    def stop():
        n["c"] += 1
        return n["c"] > 3
    with pytest.raises(InterruptedError):
        list(renderer.iter_frames([("beat_01", EXAMPLE_BEAT, 0, 2)], 10, 20, should_stop=stop))


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg not installed")
def test_end_to_end_video_has_exact_frames(renderer, tmp_path):
    second = EXAMPLE_BEAT.replace("beat_01", "beat_02").replace("[-28, 7, 42]", "[30, 9, 40]")
    beats = [("beat_01", EXAMPLE_BEAT, 0.0, 1.0), ("beat_02", second, 1.0, 2.0)]
    out = tmp_path / "v.mp4"
    n = r3.encode_silent_video(renderer.iter_frames(beats, 15, 30), out, 15, SIZE)
    assert n == 30
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                            "stream=nb_read_frames,width,height,r_frame_rate,codec_name,pix_fmt", "-of", "default=nw=1", str(out)],
                           capture_output=True, text=True).stdout
    assert "nb_read_frames=30" in probe and "width=216" in probe and "height=384" in probe
    assert "codec_name=h264" in probe and "pix_fmt=yuv420p" in probe and "r_frame_rate=15/1" in probe


def test_encoder_cleans_up_when_frames_fail(tmp_path):
    def bad():
        yield b"\xff\xd8"
        raise RuntimeError("frame failed")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    with pytest.raises(RuntimeError, match="frame failed"):
        r3.encode_silent_video(bad(), tmp_path / "x.mp4", 15, SIZE)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_demo_script_runs_with_stand_in(tmp_path):
    pytest.importorskip("playwright")
    fake = tmp_path / "fake.js"
    fake.write_text(FAKE, encoding="utf-8")
    script = Path(__file__).parents[1] / "scripts" / "render_3d_demo.py"
    # own process: the module-scoped renderer fixture already holds a Playwright session in this one
    boot = ("import sys, runpy\n"
            "try:\n    import stubplug\nexcept ImportError:\n    pass\n"       # only the offline test sandbox has it
            "sys.argv = sys.argv[1:]\nrunpy.run_path(sys.argv[0], run_name='__main__')")
    res = subprocess.run([__import__("sys").executable, "-c", boot, str(script), "--out", str(tmp_path / "o"), "--width", "216", "--height", "384",
                          "--fps", "10", "--three-file", str(fake)], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (tmp_path / "o" / "demo.mp4").stat().st_size > 1000 and list((tmp_path / "o").glob("frame_*.jpg"))
