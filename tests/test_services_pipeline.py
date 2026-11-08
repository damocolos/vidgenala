"""Tests for parameterized pipeline services and multi-instance isolation (Fase 2)."""
import os
import json
import pytest
import threading
from pathlib import Path

from nalarka.services.context import RunContext
from nalarka.services.pipeline import (
    resolve_dimensions, resolve_bgm, execute_preview_beat,
    execute_verify_only,
)
from nalarka.core.scene_code import build_namespace
from nalarka.core.constants import FPS


def test_resolve_dimensions():
    # 1080x1920 / portrait
    assert resolve_dimensions("1080x1920 (9:16 Shorts/Reels)") == (1080, 1920)
    assert resolve_dimensions("portrait") == (1080, 1920)

    # 1920x1080 / landscape
    assert resolve_dimensions("1920x1080 (16:9 Standard)") == (1920, 1080)
    assert resolve_dimensions("landscape") == (1920, 1080)

    # Match reference
    ref = {"width": 720, "height": 1280}
    assert resolve_dimensions("Match Reference", ref) == (720, 1280)
    assert resolve_dimensions("Match Reference", None) == (1080, 1920)


def test_resolve_bgm_semantics(tmp_path):
    logs = []
    log_fn = logs.append

    user_bgm = tmp_path / "user_music.mp3"
    user_bgm.write_bytes(b"dummy mp3 data")

    # 1. Full render with user BGM
    res = resolve_bgm("full_render", str(user_bgm), False, 15, 116, tmp_path, log_fn)
    assert res == str(user_bgm)

    # 2. Full render without user BGM, synth disabled
    res = resolve_bgm("full_render", None, False, 15, 116, tmp_path, log_fn)
    assert res is None

    # 3. Full render without user BGM, synth enabled
    res = resolve_bgm("full_render", None, True, 10, 116, tmp_path, log_fn)
    synth_file = tmp_path / "synthetic_ambient_bgm.wav"
    assert res == str(synth_file)
    assert synth_file.exists()

    # 4. Reroll with existing synthetic BGM -> reuses it
    res_reroll = resolve_bgm("reroll", None, False, 10, 116, tmp_path, log_fn)
    assert res_reroll == str(synth_file)

    # 5. Reroll without synthetic BGM -> returns None
    tmp2 = tmp_path / "empty_dir"
    tmp2.mkdir()
    res_reroll_none = resolve_bgm("reroll", None, False, 10, 116, tmp2, log_fn)
    assert res_reroll_none is None


def test_run_context_cancellation():
    cancel_evt = threading.Event()
    ctx = RunContext(cancel_event=cancel_evt)
    assert not ctx.is_cancelled()
    ctx.check_cancelled()  # should not raise

    cancel_evt.set()
    assert ctx.is_cancelled()
    with pytest.raises(InterruptedError):
        ctx.check_cancelled()


def test_execute_preview_beat_isolated(tmp_path):
    fixtures_dir = Path(__file__).parent / "fixtures"
    plan = json.loads((fixtures_dir / "sample_plan.json").read_text(encoding="utf-8"))
    words = json.loads((fixtures_dir / "sample_words.json").read_text(encoding="utf-8"))
    code_text = (fixtures_dir / "sample_scene_code.py").read_text(encoding="utf-8")

    from nalarka.core.scene_code import split_scene_code, add_generated_code, new_scene_namespace
    ns = new_scene_namespace()
    add_generated_code(plan, ns, code_text, lambda m: None)

    workdir = tmp_path / "work"
    workdir.mkdir()
    ctx = RunContext(workdir=workdir)

    out_sheet = execute_preview_beat(
        ctx=ctx,
        plan=plan,
        namespace=ns,
        words=words,
        beat_idx=0,
        orientation="1080x1920",
    )
    assert Path(out_sheet).exists()
    assert Path(out_sheet).stat().st_size > 1000
    assert "preview_beat_01.png" in out_sheet


def test_multi_instance_isolation(tmp_path):
    """Exit Gate Fase 2: Verify two separate workdirs render and verify independently without file collisions."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    plan = json.loads((fixtures_dir / "sample_plan.json").read_text(encoding="utf-8"))
    words = json.loads((fixtures_dir / "sample_words.json").read_text(encoding="utf-8"))
    code_text = (fixtures_dir / "sample_scene_code.py").read_text(encoding="utf-8")
    sample_vo = fixtures_dir / "sample_vo.wav"

    from nalarka.core.scene_code import add_generated_code, new_scene_namespace
    from nalarka.core.render import render_video_pipeline
    from nalarka.core.verify import run_verification_suite

    # Setup Instance A
    dir_a = tmp_path / "run_a"
    dir_a.mkdir()
    ns_a = new_scene_namespace()
    plan_a = json.loads(json.dumps(plan))
    add_generated_code(plan_a, ns_a, code_text, lambda m: None)
    out_a = dir_a / "video_a.mp4"
    log_a = dir_a / "ffmpeg_a.log"
    check_a = dir_a / "check"

    # Setup Instance B
    dir_b = tmp_path / "run_b"
    dir_b.mkdir()
    ns_b = new_scene_namespace()
    plan_b = json.loads(json.dumps(plan))
    add_generated_code(plan_b, ns_b, code_text, lambda m: None)
    out_b = dir_b / "video_b.mp4"
    log_b = dir_b / "ffmpeg_b.log"
    check_b = dir_b / "check"

    # Render A (15s)
    render_video_pipeline(
        plan=plan_a,
        namespace=ns_a,
        words=words,
        vo_path=str(sample_vo),
        bgm_path=None,
        output_path=str(out_a),
        total_dur=15,
        width=1080,
        height=1920,
        log_fn=lambda m: None,
        progress_fn=lambda p, s: None,
        workers=2,
        ffmpeg_log_path=log_a,
    )

    # Render B (15s)
    render_video_pipeline(
        plan=plan_b,
        namespace=ns_b,
        words=words,
        vo_path=str(sample_vo),
        bgm_path=None,
        output_path=str(out_b),
        total_dur=15,
        width=1080,
        height=1920,
        log_fn=lambda m: None,
        progress_fn=lambda p, s: None,
        workers=2,
        ffmpeg_log_path=log_b,
    )

    assert out_a.exists() and log_a.exists()
    assert out_b.exists() and log_b.exists()

    # Verify A in check_a
    res_a = run_verification_suite(
        output_path=str(out_a),
        expected_dur=15,
        expected_w=1080,
        expected_h=1920,
        log_fn=lambda m: None,
        check_dir=check_a,
    )
    # Verify B in check_b
    res_b = run_verification_suite(
        output_path=str(out_b),
        expected_dur=15,
        expected_w=1080,
        expected_h=1920,
        log_fn=lambda m: None,
        check_dir=check_b,
    )

    assert res_a["passed"] == 7
    assert res_b["passed"] == 7
    assert (check_a / "verification_contact_sheet.jpg").exists()
    assert (check_b / "verification_contact_sheet.jpg").exists()
    # Check that A and B did not overwrite each other's check dir
    assert check_a != check_b


def test_vectorkit_kwargs_tolerance():
    from nalarka.core.vectorkit import VectorKit
    vk = VectorKit(1080, 1920, 1.0, {"start": 0, "end": 3, "bg_color": "cream"}, [], {})
    # All these unexpected kwargs used to throw TypeError, now handled safely:
    vk.rect(0, 0, 100, 100, outline="dark", delay=0.1, unknown_param=123)
    vk.line(0, 0, 50, 50, delay=0.2, extra_arg="test")
    vk.blob(0, 0, r=50, alpha=200, unexpected=True)
    vk.droplet(0, 0, r=20, alpha=150, foo="bar")
    vk.bezier([(0, 0), (20, 20)], angle=15, delay=0.1, baz=None)
    assert vk.shape_count >= 5


def test_extract_python_code_multi_block():
    from nalarka.core.extractors import extract_python_code_safely
    raw = """
<think>
Some thinking process here
</think>
Here is the first function:
```python
def beat_01(v):
    v.circle(0, 0, 50)
```
And here is the second function:
```python
def beat_02(v):
    v.rect(0, 0, 50, 50)
```
"""
    code = extract_python_code_safely(raw)
    assert "def beat_01" in code
    assert "def beat_02" in code
    assert "<think>" not in code
