import json
import hashlib
from pathlib import Path
import pytest
from PIL import Image

import legacy.nalarka_local as leg
import nalarka.core as core
from tests._golden import skip_unless_reference_fonts


def test_core_math_parity():
    for hex_val in ["#F2EADA", "#EE8631", "#38844C", "#000000", "#FFFFFF", "123"]:
        assert leg.hex_to_rgb(hex_val) == core.hex_to_rgb(hex_val)
    for rgb in [(242, 234, 218), (0, 0, 0), (255, 255, 255), (100, 200, 50)]:
        assert leg.rgb_to_hex(rgb) == core.rgb_to_hex(rgb)
    for v in [-10, 0, 50, 100, 150]:
        assert leg.clamp(v, 0, 100) == core.clamp(v, 0, 100)
    for x in [0.0, 0.25, 0.5, 0.75, 1.0]:
        assert leg.ease_out_cubic(x) == core.ease_out_cubic(x)
        assert leg.ease_in_out_cubic(x) == core.ease_in_out_cubic(x)
        assert leg.ease_out_back(x) == core.ease_out_back(x)
        assert leg.ease_out_elastic(x) == core.ease_out_elastic(x)


def test_core_scripttext_parity():
    script_data = {
        "hook": "Kenapa suara sendiri bikin cringe?",
        "inti_1": "Suara merambat lewat udara dan tulang.",
        "inti_2": "Tulang menambah getaran rendah.",
        "penutup": "Jadi suara rekaman adalah suaramu yang asli."
    }
    assert leg.format_sections_to_tagged_text(script_data) == core.format_sections_to_tagged_text(script_data)

    raw_text = """[Hook] Kenapa suara rekaman aneh?
    
[Inti 1] Ada dua jalan perambatan suara.

[Penutup] Terimalah suaramu apa adanya."""
    assert leg.parse_tagged_script(raw_text) == core.parse_tagged_script(raw_text)
    assert leg.prepare_text_for_elevenlabs(raw_text, True) == core.prepare_text_for_elevenlabs(raw_text, True)
    assert leg.prepare_text_for_elevenlabs(raw_text, False) == core.prepare_text_for_elevenlabs(raw_text, False)
    assert leg.script_fingerprint(raw_text) == core.script_fingerprint(raw_text)


def test_core_budget_parity():
    for d in [30, 60, 90, 120, 180, 300, 600]:
        assert leg.script_word_budget(d) == core.script_word_budget(d)
        assert leg.script_section_count(d) == core.script_section_count(d)
    for words in [10, 50, 100, 200, 350]:
        assert leg.estimate_video_seconds(words) == core.estimate_video_seconds(words)


def test_core_extractors_parity():
    test_json = '<think>reasoning</think>\n```json\n{"status": "ok", "count": 12}\n```'
    assert leg.extract_json_safely(test_json, lambda m: None) == core.extract_json_safely(test_json, lambda m: None)

    test_code = '<think>reasoning</think>\n```python\ndef beat_01(v):\n    v.rect(0, 0, 10, 10)\n```'
    assert leg.extract_python_code_safely(test_code) == core.extract_python_code_safely(test_code)


def test_core_storyboard_scenes_parity():
    raw_scenes = [
        {"start": 5.0, "summary": "Beat 2", "layout": "top_text_bottom_art"},
        {"start": 1.0, "summary": "Beat 1", "layout": "invalid_layout", "bg_color": "not_a_color"},
        {"start": 1.2, "summary": "Beat 1b"},
    ]
    assert leg.sanitize_scenes(raw_scenes, 15.0, lambda m: None) == core.sanitize_scenes(raw_scenes, 15.0, lambda m: None)
    assert leg.storyboard_windows(60) == core.storyboard_windows(60)
    assert leg.storyboard_windows(180) == core.storyboard_windows(180)


def test_core_render_frame_golden_parity():
    skip_unless_reference_fonts()
    golden_path = Path("tests/golden/frame_hashes.json")
    golden_data = json.loads(golden_path.read_text(encoding="utf-8"))
    w, h = golden_data["width"], golden_data["height"]
    golden_hashes = golden_data["hashes"]

    with open("tests/fixtures/sample_plan.json", encoding="utf-8") as f:
        plan = json.load(f)

    with open("tests/fixtures/sample_words.json", encoding="utf-8") as f:
        words = json.load(f)

    with open("tests/fixtures/sample_scene_code.py", encoding="utf-8") as f:
        code = f.read()

    helpers, beats = core.split_scene_code(code, lambda m: None)
    plan["scene_sources"] = {"helpers": helpers, "beats": beats}
    ns = core.build_namespace(plan["scene_sources"], lambda m: None)

    for t_str, expected_hash in golden_hashes.items():
        t = float(t_str)
        img = core.render_frame(t, w, h, plan, words, ns)
        current_hash = hashlib.sha256(img.tobytes()).hexdigest()
        assert current_hash == expected_hash, f"core.render_frame at t={t}s diverged from golden hash!"
