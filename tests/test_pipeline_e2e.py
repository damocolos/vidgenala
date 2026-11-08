import json
import os
from pathlib import Path
import pytest
import nalarka.core as core


def test_full_render_pipeline_and_verification(tmp_path):
    with open("tests/fixtures/sample_plan.json", encoding="utf-8") as f:
        plan = json.load(f)

    with open("tests/fixtures/sample_words.json", encoding="utf-8") as f:
        words = json.load(f)

    with open("tests/fixtures/sample_scene_code.py", encoding="utf-8") as f:
        code = f.read()

    helpers, beats = core.split_scene_code(code, lambda m: None)
    plan["scene_sources"] = {"helpers": helpers, "beats": beats}
    ns = core.build_namespace(plan["scene_sources"], lambda m: None)

    vo_path = "tests/fixtures/sample_vo.wav"
    out_mp4 = str(tmp_path / "test_output.mp4")
    total_dur = 15
    w, h = 540, 960

    core.render_video_pipeline(
        plan, ns, words, vo_path, None, out_mp4,
        total_dur, w, h,
        log_fn=lambda m: None,
        progress_fn=lambda p, s: None,
        workers=2
    )

    assert os.path.exists(out_mp4)
    res = core.run_verification_suite(out_mp4, total_dur, w, h, lambda m: None, max_duration=15)
    assert res["passed"] == 7
    assert res["total"] == 7
    assert res["frames"] == 450
