import pytest
import legacy.nalarka_local as leg


def test_storyboard_windows():
    # <= 120s is a single window
    assert leg.storyboard_windows(60) == [(0.0, 60.0)]
    assert leg.storyboard_windows(120) == [(0.0, 120.0)]

    # > 120s splits per PASS1_WINDOW_SECONDS (90s)
    w_180 = leg.storyboard_windows(180)
    assert len(w_180) == 2
    assert w_180[0] == (0.0, 90.0)
    assert w_180[1] == (90.0, 180.0)


def test_sanitize_scenes_reordering_and_defaults():
    raw_scenes = [
        {"start": 5.0, "summary": "Beat 2", "layout": "top_text_bottom_art"},
        {"start": 1.0, "summary": "Beat 1", "layout": "invalid_layout", "bg_color": "not_a_color"},
    ]
    sanitized = leg.sanitize_scenes(raw_scenes, 15.0, lambda msg: None)
    assert len(sanitized) == 2
    # Beat 1 must be adjusted to start at 0.0
    assert sanitized[0]["start"] == 0.0
    assert sanitized[0]["beat_id"] == 1
    assert sanitized[0]["layout"] == "top_text_bottom_art"  # fallback from invalid
    assert sanitized[0]["bg_color"] in leg.VALID_BG_COLORS

    assert sanitized[1]["beat_id"] == 2
    assert sanitized[1]["end"] == 15.0


def test_sanitize_scenes_enforces_min_beat_seconds():
    # Two scenes starting with only 0.2s difference -> must be spaced by MIN_BEAT_SECONDS (1.2s)
    raw_scenes = [
        {"start": 0.0, "summary": "Beat 1"},
        {"start": 0.2, "summary": "Beat 2"},
    ]
    sanitized = leg.sanitize_scenes(raw_scenes, 10.0, lambda msg: None)
    assert sanitized[0]["start"] == 0.0
    assert sanitized[1]["start"] >= leg.MIN_BEAT_SECONDS


def test_validate_scene_source():
    # Valid code
    valid_code = "def beat_01(v):\n    v.rect(0, 0, 50, 50, color='cream')\n"
    assert leg.validate_scene_source(valid_code) is None

    # Disallowed import
    import_code = "import os\ndef beat_01(v):\n    pass\n"
    err = leg.validate_scene_source(import_code)
    assert err is not None
    assert "import statements are not allowed" in err

    # Disallowed while loop
    while_code = "def beat_01(v):\n    while True:\n        break\n"
    err = leg.validate_scene_source(while_code)
    assert err is not None
    assert "'while' loops are not allowed" in err

    # Disallowed dangerous builtins
    eval_code = "def beat_01(v):\n    eval('2+2')\n"
    err = leg.validate_scene_source(eval_code)
    assert err is not None


def test_split_scene_code():
    code = """
MY_CONST = 100

def my_helper(v, x):
    v.circle(x, 0, 20)

def beat_01(v):
    my_helper(v, MY_CONST)

def beat_02(v):
    v.rect(0, 0, 30, 30)
"""
    helpers, beats = leg.split_scene_code(code, lambda msg: None)
    assert "beat_01" in beats
    assert "beat_02" in beats
    assert "my_helper" in helpers
    assert any("MY_CONST" in h_src for h_src in helpers.values())
