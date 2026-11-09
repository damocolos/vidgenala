"""Environment-independent render checks (the golden-hash tests need reference fonts)."""
import hashlib
import json

import nalarka.core as core


def _setup():
    with open("tests/fixtures/sample_plan.json", encoding="utf-8") as f:
        plan = json.load(f)
    with open("tests/fixtures/sample_words.json", encoding="utf-8") as f:
        words = json.load(f)
    with open("tests/fixtures/sample_scene_code.py", encoding="utf-8") as f:
        code = f.read()
    helpers, beats = core.split_scene_code(code, lambda m: None)
    plan["scene_sources"] = {"helpers": helpers, "beats": beats}
    return plan, words, core.build_namespace(plan["scene_sources"], lambda m: None)


def test_render_frame_is_deterministic_and_animates():
    plan, words, ns = _setup()
    w, h = 270, 480
    times = [0.5, 5.5, 8.0, 11.0, 14.0]
    a = [hashlib.sha256(core.render_frame(t, w, h, plan, words, ns).tobytes()).hexdigest() for t in times]
    b = [hashlib.sha256(core.render_frame(t, w, h, plan, words, ns).tobytes()).hexdigest() for t in times]
    assert a == b, "same input must give identical pixels"
    assert len(set(a)) >= 3, "frames at different times should differ"
