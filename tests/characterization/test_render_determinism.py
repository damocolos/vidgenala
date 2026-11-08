import json
import hashlib
from pathlib import Path
import legacy.nalarka_local as leg


def test_golden_frame_determinism():
    golden_path = Path("tests/golden/frame_hashes.json")
    assert golden_path.exists(), "Golden hashes file missing"
    golden_data = json.loads(golden_path.read_text(encoding="utf-8"))
    w, h = golden_data["width"], golden_data["height"]
    golden_hashes = golden_data["hashes"]

    with open("tests/fixtures/sample_plan.json", encoding="utf-8") as f:
        plan = json.load(f)

    with open("tests/fixtures/sample_words.json", encoding="utf-8") as f:
        words = json.load(f)

    with open("tests/fixtures/sample_scene_code.py", encoding="utf-8") as f:
        code = f.read()

    helpers, beats = leg.split_scene_code(code, lambda m: None)
    plan["scene_sources"] = {"helpers": helpers, "beats": beats}
    ns = leg.build_namespace(plan["scene_sources"], lambda m: None)

    for t_str, expected_hash in golden_hashes.items():
        t = float(t_str)
        img = leg.render_frame(t, w, h, plan, words, ns)
        current_hash = hashlib.sha256(img.tobytes()).hexdigest()
        assert current_hash == expected_hash, f"Frame at t={t}s diverged from golden hash!"
