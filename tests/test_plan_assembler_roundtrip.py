"""Round-trip tests for plan assembler (Fase 3 Exit Gate)."""
import json
import pytest
from pathlib import Path

from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults
from nalarka.db.repos.topics import create_topic
from nalarka.services.plan_assembler import save_plan, load_plan
from nalarka.core.scene_code import add_generated_code, new_scene_namespace
from nalarka.core.frames import render_frame


@pytest.fixture
def db(tmp_path):
    conn = get_connection(tmp_path / "plan_test.db")
    run_migrations(conn)
    seed_defaults(conn)
    yield conn
    conn.close()


def test_plan_assembler_roundtrip(db):
    fixtures_dir = Path(__file__).parent / "fixtures"
    orig_plan = json.loads((fixtures_dir / "sample_plan.json").read_text(encoding="utf-8"))
    orig_words = json.loads((fixtures_dir / "sample_words.json").read_text(encoding="utf-8"))
    code_text = (fixtures_dir / "sample_scene_code.py").read_text(encoding="utf-8")

    # Add extra arbitrary fields to beat 1 to test extra_json preservation
    orig_plan["scenes"][0]["custom_ai_metric"] = 0.985
    orig_plan["scenes"][0]["special_tags"] = ["vivid", "punchy"]

    # Build original namespace
    ns_orig = new_scene_namespace()
    add_generated_code(orig_plan, ns_orig, code_text, lambda m: None)

    # Save to SQLite
    topic_id = create_topic(db, "Plan Roundtrip Topic", max_duration=15)
    sb_id = save_plan(db, topic_id, orig_plan, coder_model="claude-opus-5.5")

    # Load from SQLite
    loaded_plan, loaded_ns = load_plan(db, sb_id)

    # 1. Structural parity
    assert loaded_plan["total_dur"] == orig_plan["total_dur"]
    assert loaded_plan["palette"] == orig_plan["palette"]
    assert len(loaded_plan["scenes"]) == len(orig_plan["scenes"])

    for orig_sc, load_sc in zip(orig_plan["scenes"], loaded_plan["scenes"]):
        assert load_sc["beat_id"] == orig_sc["beat_id"]
        assert load_sc["start"] == orig_sc["start"]
        assert load_sc["end"] == orig_sc["end"]
        assert load_sc["bg_color"] == orig_sc["bg_color"]
        assert load_sc["layout"] == orig_sc["layout"]
        assert load_sc["transition"] == orig_sc["transition"]
        assert load_sc["summary"] == orig_sc["summary"]
        assert load_sc["visual_blueprint"] == orig_sc["visual_blueprint"]
        assert load_sc["lines"] == orig_sc["lines"]
        # Extra fields preserved!
        if "custom_ai_metric" in orig_sc:
            assert load_sc["custom_ai_metric"] == orig_sc["custom_ai_metric"]
            assert load_sc["special_tags"] == orig_sc["special_tags"]

    # 2. Helper and beat sources parity
    orig_store = orig_plan["scene_sources"]
    load_store = loaded_plan["scene_sources"]
    assert load_store["helpers"] == orig_store["helpers"]
    assert load_store["beats"] == orig_store["beats"]

    # 3. Execution parity: frames rendered from loaded namespace must match original namespace pixel-for-pixel!
    for t in (0.5, 2.5, 7.5, 12.0):
        frame_orig = render_frame(t, 1080, 1920, orig_plan, orig_words, ns_orig)
        frame_loaded = render_frame(t, 1080, 1920, loaded_plan, orig_words, loaded_ns)
        assert frame_orig.tobytes() == frame_loaded.tobytes()
